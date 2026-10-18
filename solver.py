"""Verify a factory layout with OR-Tools CP-SAT, and search for the nearest valid one.

`verify` fixes every station at the agent's coordinates and guards each rule with its own
assumption literal. While the model is infeasible, CP-SAT names a set of assumptions that cannot
all hold; those rules are violated, so they are dropped and the model is solved again. When it
becomes feasible, the remaining rules are proven to hold together, and the layout is valid only
if nothing was dropped.

`repair` frees the coordinates, makes every geometric rule hard and minimizes how far the stations
move, so a failing layout comes back with the smallest set of moves that would fix it (or a proof
that none exists nearby).

All geometry is integer decimeters inside the model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ortools.sat.python import cp_model

import factory

DM = 10  # model units per meter
FAMILIES = {
    "bounds": "Inside the building",
    "clearance": "Access clearances",
    "columns": "Clear of columns",
    "aisle_exits": "Aisle and exits clear",
    "hazard": "Hazard separation",
    "exhaust": "Exhaust on north wall",
    "docks": "Docks",
    "flow": "Material flow distance",
    "capacity": "Capacity vs. target rate",
}
MAX_STATIONS = 40


def to_dm(v: float) -> int:
    return int(round(float(v) * DM))


def m_(v: int | float) -> str:
    return f"{v / DM:.1f} m"


@dataclass(frozen=True)
class Rect:
    """A rectangle in decimeters; x/y may be CP-SAT variables."""

    x: Any
    y: Any
    w: int
    h: int


@dataclass
class Station:
    id: str
    type: str
    step: str
    x: int
    y: int
    w: int
    h: int
    access: int
    uph: int
    tags: list[str]


@dataclass
class Rule:
    id: str
    family: str
    stations: list[str]
    encode: Callable[[cp_model.CpModel, dict[str, Rect], Any], None]
    explain: Callable[[], str | None]


# ---------- input normalization ----------


def normalize(layout: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Check the shape of an agent-proposed layout. Returns (normalized, structure errors)."""
    errors: list[str] = []
    if not isinstance(layout, dict):
        return None, ["layout must be a JSON object"]
    try:
        rate = int(layout.get("rate_per_hour", 0))
    except (TypeError, ValueError):
        rate = 0
    if rate <= 0:
        errors.append("rate_per_hour must be a positive integer")

    steps_in = layout.get("steps") or []
    stations_in = layout.get("stations") or []
    if not isinstance(steps_in, list) or not steps_in:
        return None, errors + ["steps must be a non-empty list"]
    if not isinstance(stations_in, list) or not stations_in:
        return None, errors + ["stations must be a non-empty list"]
    if len(stations_in) > MAX_STATIONS:
        return None, errors + [f"at most {MAX_STATIONS} stations"]

    steps: list[dict[str, Any]] = []
    step_ids: set[str] = set()
    for s in steps_in:
        if not isinstance(s, dict) or not s.get("id"):
            errors.append("every step needs an id")
            continue
        sid = str(s["id"])
        if sid in step_ids:
            errors.append(f"duplicate step id {sid!r}")
        step_ids.add(sid)
        after = [str(a) for a in (s.get("after") or [])]
        steps.append({"id": sid, "name": str(s.get("name") or sid), "after": after})
    for s in steps:
        for a in s["after"]:
            if a not in step_ids:
                errors.append(f"step {s['id']!r} comes after unknown step {a!r}")

    stations: list[dict[str, Any]] = []
    ids: set[str] = set()
    for st in stations_in:
        if not isinstance(st, dict):
            errors.append("every station must be an object")
            continue
        sid = str(st.get("id") or "")
        typ = str(st.get("type") or "")
        step = str(st.get("step") or "")
        if not sid:
            errors.append("every station needs an id")
            continue
        if sid in ids:
            errors.append(f"duplicate station id {sid!r}")
        ids.add(sid)
        if typ not in factory.EQUIPMENT:
            errors.append(f"station {sid!r}: unknown equipment type {typ!r}")
        if step not in step_ids:
            errors.append(f"station {sid!r}: unknown step {step!r}")
        try:
            x, y = float(st["x"]), float(st["y"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"station {sid!r}: x and y must be numbers")
            continue
        stations.append(
            {
                "id": sid,
                "type": typ,
                "step": step,
                "x": round(x, 1),
                "y": round(y, 1),
                "rotated": bool(st.get("rotated", False)),
            }
        )

    used_steps = {s["step"] for s in stations}
    for s in steps:
        if s["id"] not in used_steps:
            errors.append(f"step {s['id']!r} has no stations")

    # The process must be acyclic, start at receiving and end at shipping.
    succ: dict[str, list[str]] = {s["id"]: [] for s in steps}
    for s in steps:
        for a in s["after"]:
            if a in succ:
                succ[a].append(s["id"])
    if not errors and _has_cycle(succ):
        errors.append("steps form a cycle")
    types_by_step: dict[str, set[str]] = {}
    for st in stations:
        types_by_step.setdefault(st["step"], set()).add(st["type"])
    for s in steps:
        types = types_by_step.get(s["id"], set())
        if "receiving_bay" in types and s["after"]:
            errors.append(f"receiving step {s['id']!r} cannot come after another step")
        if "shipping_bay" in types and succ.get(s["id"]):
            errors.append(f"shipping step {s['id']!r} cannot have following steps")
    if not any("receiving_bay" in t for t in types_by_step.values()):
        errors.append("the line needs a receiving_bay")
    if not any("shipping_bay" in t for t in types_by_step.values()):
        errors.append("the line needs a shipping_bay")

    out = {
        "product": str(layout.get("product") or "")[:80],
        "rate_per_hour": rate,
        "steps": steps,
        "stations": stations,
    }
    return out, errors


def _has_cycle(succ: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        state[n] = 1
        for m in succ.get(n, []):
            if state.get(m) == 1 or (state.get(m) is None and visit(m)):
                return True
        state[n] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in succ)


def _stations(layout: dict[str, Any]) -> list[Station]:
    out = []
    for st in layout["stations"]:
        eq = factory.EQUIPMENT[st["type"]]
        w, h = to_dm(eq["w"]), to_dm(eq["d"])
        if st["rotated"]:
            w, h = h, w
        out.append(
            Station(
                st["id"],
                st["type"],
                st["step"],
                to_dm(st["x"]),
                to_dm(st["y"]),
                w,
                h,
                to_dm(eq["access"]),
                int(eq["uph"]),
                list(eq["tags"]),
            )
        )
    return out


# ---------- encoding helpers ----------


def _enforce(ct: Any, lit: Any) -> None:
    if lit is not None:
        ct.OnlyEnforceIf(lit)


def _separated(m: cp_model.CpModel, a: Rect, b: Rect, gap: int, lit: Any) -> None:
    """a and b are at least `gap` apart along x or along y."""
    bs = [m.NewBoolVar("") for _ in range(4)]
    m.Add(a.x + a.w + gap <= b.x).OnlyEnforceIf(bs[0])
    m.Add(b.x + b.w + gap <= a.x).OnlyEnforceIf(bs[1])
    m.Add(a.y + a.h + gap <= b.y).OnlyEnforceIf(bs[2])
    m.Add(b.y + b.h + gap <= a.y).OnlyEnforceIf(bs[3])
    _enforce(m.AddBoolOr(bs), lit)


def _grow(r: Rect, by: int) -> Rect:
    return Rect(r.x - by, r.y - by, r.w + 2 * by, r.h + 2 * by)


def _gap(ax: int, ay: int, aw: int, ah: int, bx: int, by: int, bw: int, bh: int) -> int:
    """Largest axis separation between two fixed rectangles (negative when they overlap)."""
    gx = max(bx - (ax + aw), ax - (bx + bw))
    gy = max(by - (ay + ah), ay - (by + bh))
    return max(gx, gy)


def _fixed(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
    return to_dm(x0), to_dm(y0), to_dm(x1) - to_dm(x0), to_dm(y1) - to_dm(y0)


# ---------- rules ----------


def build_rules(layout: dict[str, Any]) -> tuple[list[Station], list[Rule]]:
    sts = _stations(layout)
    W, H = to_dm(factory.BUILDING["width"]), to_dm(factory.BUILDING["depth"])
    rules: list[Rule] = []

    for s in sts:

        def enc_bounds(m, V, lit, s=s):
            r = V[s.id]
            for ct in (
                m.Add(r.x >= 0),
                m.Add(r.y >= 0),
                m.Add(r.x + r.w <= W),
                m.Add(r.y + r.h <= H),
            ):
                _enforce(ct, lit)

        def exp_bounds(s=s):
            over = {"west": -s.x, "south": -s.y, "east": s.x + s.w - W, "north": s.y + s.h - H}
            wall, by = max(over.items(), key=lambda kv: kv[1])
            return f"{s.id} extends {m_(by)} past the {wall} wall." if by > 0 else None

        rules.append(Rule(f"bounds:{s.id}", "bounds", [s.id], enc_bounds, exp_bounds))

    for i, a in enumerate(sts):
        for b in sts[i + 1 :]:
            need = a.access + b.access

            def enc_clear(m, V, lit, a=a, b=b, need=need):
                _separated(m, V[a.id], V[b.id], need, lit)

            def exp_clear(a=a, b=b, need=need):
                g = _gap(a.x, a.y, a.w, a.h, b.x, b.y, b.w, b.h)
                if g >= need:
                    return None
                if g < 0:
                    return f"{a.id} and {b.id} overlap."
                return f"{a.id} and {b.id} are {m_(g)} apart; their access zones need {m_(need)}."

            rules.append(
                Rule(f"clearance:{a.id}|{b.id}", "clearance", [a.id, b.id], enc_clear, exp_clear)
            )

    col = to_dm(factory.COLUMN_SIZE)
    cols = [
        (c["name"], to_dm(c["x"]) - col // 2, to_dm(c["y"]) - col // 2) for c in factory.COLUMNS
    ]
    for s in sts:

        def enc_cols(m, V, lit, s=s):
            for _, cx, cy in cols:
                _separated(m, V[s.id], Rect(cx, cy, col, col), 0, lit)

        def exp_cols(s=s):
            hit = [n for n, cx, cy in cols if _gap(s.x, s.y, s.w, s.h, cx, cy, col, col) < 0]
            return f"{s.id} sits on column {', '.join(hit)}." if hit else None

        rules.append(Rule(f"columns:{s.id}", "columns", [s.id], enc_cols, exp_cols))

    zones = [("the main aisle", *_fixed(**{k: factory.AISLE[k] for k in ("x0", "y0", "x1", "y1")}))]
    zones += [
        (f"the {e['name']}", *_fixed(e["x0"], e["y0"], e["x1"], e["y1"])) for e in factory.EXITS
    ]
    for s in sts:

        def enc_zones(m, V, lit, s=s):
            env = _grow(V[s.id], s.access)
            for _, zx, zy, zw, zh in zones:
                _separated(m, env, Rect(zx, zy, zw, zh), 0, lit)

        def exp_zones(s=s):
            ex, ey, ew, eh = s.x - s.access, s.y - s.access, s.w + 2 * s.access, s.h + 2 * s.access
            hit = [n for n, zx, zy, zw, zh in zones if _gap(ex, ey, ew, eh, zx, zy, zw, zh) < 0]
            if not hit:
                return None
            return f"{s.id} (with its {m_(s.access)} access zone) blocks {' and '.join(hit)}."

        rules.append(Rule(f"aisle_exits:{s.id}", "aisle_exits", [s.id], enc_zones, exp_zones))

    for hz in factory.HAZARD_RULES:
        gap = to_dm(hz["min_gap"])
        for a in sts:
            if hz["a"] not in a.tags:
                continue
            for b in sts:
                if b.id == a.id or hz["b"] not in b.tags:
                    continue

                def enc_hz(m, V, lit, a=a, b=b, gap=gap):
                    _separated(m, V[a.id], V[b.id], gap, lit)

                def exp_hz(a=a, b=b, gap=gap, hz=hz):
                    g = _gap(a.x, a.y, a.w, a.h, b.x, b.y, b.w, b.h)
                    if g >= gap:
                        return None
                    return (
                        f"{a.id} ({hz['a']}) is {m_(max(g, 0))} from {b.id} ({hz['b']}); "
                        + f"{hz['why']} needs {m_(gap)}."
                    )

                rules.append(Rule(f"hazard:{a.id}|{b.id}", "hazard", [a.id, b.id], enc_hz, exp_hz))

    within = to_dm(factory.EXHAUST_WITHIN)
    for s in sts:
        if "exhaust" not in s.tags:
            continue

        def enc_ex(m, V, lit, s=s):
            _enforce(m.Add(V[s.id].y + V[s.id].h >= H - within), lit)

        def exp_ex(s=s):
            away = H - (s.y + s.h)
            return (
                (
                    f"{s.id} needs roof exhaust: put it against the north wall "
                    + f"(it is {m_(away)} away)."
                )
                if away > within
                else None
            )

        rules.append(Rule(f"exhaust:{s.id}", "exhaust", [s.id], enc_ex, exp_ex))

    for s in sts:
        kind = "receiving" if "receive" in s.tags else "shipping" if "ship" in s.tags else None
        if not kind:
            continue
        dock = factory.DOCKS[kind]
        y0, y1, near = to_dm(dock["y0"]), to_dm(dock["y1"]), to_dm(dock["within"])

        def enc_dock(m, V, lit, s=s, kind=kind, y0=y0, y1=y1, near=near):
            r = V[s.id]
            wall = m.Add(r.x <= near) if kind == "receiving" else m.Add(r.x + r.w >= W - near)
            for ct in (wall, m.Add(r.y <= y1 - 1), m.Add(r.y + r.h >= y0 + 1)):
                _enforce(ct, lit)

        def exp_dock(s=s, kind=kind, y0=y0, y1=y1, near=near):
            dist = s.x if kind == "receiving" else W - (s.x + s.w)
            faces = s.y <= y1 - 1 and s.y + s.h >= y0 + 1
            if dist <= near and faces:
                return None
            d = factory.DOCKS[kind]
            return (
                f"{s.id} must sit against the {d['wall']} wall facing the {kind} doors "
                + f"(y {d['y0']:g}-{d['y1']:g} m)."
            )

        rules.append(Rule(f"docks:{s.id}", "docks", [s.id], enc_dock, exp_dock))

    max_t = 2 * to_dm(factory.MAX_TRANSFER_M)  # centers are doubled to stay integral
    step_names = {s["id"]: s["name"] for s in layout["steps"]}
    members: dict[str, list[Station]] = {}
    for s in sts:
        members.setdefault(s.step, []).append(s)
    for step in layout["steps"]:
        downs = members.get(step["id"], [])
        for up_id in step["after"]:
            for u in members.get(up_id, []):

                def enc_flow(m, V, lit, u=u, downs=downs):
                    bs = []
                    ru = V[u.id]
                    for v in downs:
                        rv = V[v.id]
                        big = 4 * (W + H)
                        dx, dy = m.NewIntVar(-big, big, ""), m.NewIntVar(-big, big, "")
                        adx, ady = m.NewIntVar(0, big, ""), m.NewIntVar(0, big, "")
                        m.Add(dx == 2 * ru.x + ru.w - 2 * rv.x - rv.w)
                        m.Add(dy == 2 * ru.y + ru.h - 2 * rv.y - rv.h)
                        m.AddAbsEquality(adx, dx)
                        m.AddAbsEquality(ady, dy)
                        b = m.NewBoolVar("")
                        m.Add(adx + ady <= max_t).OnlyEnforceIf(b)
                        bs.append(b)
                    _enforce(m.AddBoolOr(bs), lit)

                def exp_flow(u=u, downs=downs, name=step_names[step["id"]]):
                    d = min(
                        abs(2 * u.x + u.w - 2 * v.x - v.w) + abs(2 * u.y + u.h - 2 * v.y - v.h)
                        for v in downs
                    )
                    if d <= max_t:
                        return None
                    return (
                        f"{u.id} -> {name}: the nearest station is {m_(d / 2)} away "
                        + f"(max {factory.MAX_TRANSFER_M:g} m)."
                    )

                rules.append(
                    Rule(
                        f"flow:{u.id}>{step['id']}",
                        "flow",
                        [u.id] + [v.id for v in downs],
                        enc_flow,
                        exp_flow,
                    )
                )

    rate = layout["rate_per_hour"]
    for step in layout["steps"]:
        mem = members.get(step["id"], [])
        total = sum(s.uph for s in mem)

        def enc_cap(m, V, lit, mem=mem):
            units = [m.NewConstant(s.uph) for s in mem]
            _enforce(m.Add(sum(units) >= rate), lit)

        def exp_cap(mem=mem, total=total, step=step):
            if total >= rate:
                return None
            per = {s.type: s.uph for s in mem}
            typ, uph = max(per.items(), key=lambda kv: kv[1])
            more = -(-(rate - total) // uph)
            return (
                f"Step '{step['name']}' makes {total}/h with {len(mem)} station(s); the line "
                + f"needs {rate}/h. Add {more} more {typ}."
            )

        rules.append(
            Rule(f"capacity:{step['id']}", "capacity", [s.id for s in mem], enc_cap, exp_cap)
        )

    return sts, rules


# ---------- verify ----------


def verify(layout_in: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    layout, errors = normalize(layout_in)
    if errors or layout is None:
        return {
            "valid": False,
            "structure_errors": errors,
            "violations": [],
            "families": {},
            "constraints": 0,
            "solve_ms": 0.0,
            "layout": layout,
        }

    sts, rules = build_rules(layout)
    m = cp_model.CpModel()
    V = {
        s.id: Rect(m.NewIntVar(s.x, s.x, f"x_{s.id}"), m.NewIntVar(s.y, s.y, f"y_{s.id}"), s.w, s.h)
        for s in sts
    }
    lits = []
    for r in rules:
        lit = m.NewBoolVar(r.id)
        r.encode(m, V, lit)
        lits.append(lit)
    index_to_rule = {lit.Index(): i for i, lit in enumerate(lits)}

    solver = cp_model.CpSolver()
    # Cores come from the single-worker search; presolve keeps assumption literals intact.
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = 10.0
    active = set(range(len(rules)))
    violated: list[int] = []
    solves = 0
    while True:
        m.ClearAssumptions()
        m.AddAssumptions([lits[i] for i in sorted(active)])
        status = solver.Solve(m)
        solves += 1
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        if status != cp_model.INFEASIBLE:
            raise RuntimeError(f"CP-SAT returned {solver.StatusName(status)}")
        core = [
            index_to_rule[v]
            for v in solver.SufficientAssumptionsForInfeasibility()
            if v in index_to_rule
        ]
        if not core:
            raise RuntimeError("infeasible with an empty core")
        for i in core:
            active.discard(i)
            violated.append(i)

    families = {f: {"label": label, "checked": 0, "violated": 0} for f, label in FAMILIES.items()}
    for r in rules:
        families[r.family]["checked"] += 1
    violations = []
    for i in sorted(violated, key=lambda i: list(FAMILIES).index(rules[i].family)):
        r = rules[i]
        families[r.family]["violated"] += 1
        violations.append(
            {
                "rule": r.id,
                "family": r.family,
                "stations": r.stations,
                "message": r.explain() or r.id,
            }
        )
    proto = m.Proto()
    return {
        "valid": not violated,
        "structure_errors": [],
        "violations": violations,
        "families": families,
        "constraints": len(rules),
        "model": {"variables": len(proto.variables), "constraints": len(proto.constraints)},
        "solves": solves,
        "solve_ms": round((time.perf_counter() - t0) * 1000, 1),
        "solver": f"OR-Tools CP-SAT {_ortools_version()}",
        "layout": layout,
    }


def _ortools_version() -> str:
    import ortools

    return ortools.__version__


# ---------- repair ----------


def repair(layout: dict[str, Any], time_limit_s: float = 4.0) -> dict[str, Any]:
    """Nearest valid placement of the same stations (geometry only), minimizing total movement."""
    t0 = time.perf_counter()
    sts, rules = build_rules(layout)
    short = [r for r in rules if r.family == "capacity" and r.explain()]
    if short:
        return {
            "status": "skipped",
            "reason": "capacity is short; add machines first",
            "moves": [],
            "solve_ms": 0.0,
        }
    W, H = to_dm(factory.BUILDING["width"]), to_dm(factory.BUILDING["depth"])
    m = cp_model.CpModel()
    V: dict[str, Rect] = {}
    moves = []
    for s in sts:
        x = m.NewIntVar(0, max(0, W - s.w), f"x_{s.id}")
        y = m.NewIntVar(0, max(0, H - s.h), f"y_{s.id}")
        m.AddHint(x, min(max(s.x, 0), max(0, W - s.w)))
        m.AddHint(y, min(max(s.y, 0), max(0, H - s.h)))
        V[s.id] = Rect(x, y, s.w, s.h)
        for var, orig in ((x, s.x), (y, s.y)):
            d = m.NewIntVar(-2 * (W + H), 2 * (W + H), "")
            ad = m.NewIntVar(0, 2 * (W + H), "")
            m.Add(d == var - orig)
            m.AddAbsEquality(ad, d)
            moves.append(ad)
    for r in rules:
        if r.family != "capacity":
            r.encode(m, V, None)
    # Redundant global constraint: lets CP-SAT propagate access zones as one packing problem.
    xs, ys = [], []
    for s in sts:
        r = _grow(V[s.id], s.access)
        xs.append(m.NewFixedSizeIntervalVar(r.x, r.w, ""))
        ys.append(m.NewFixedSizeIntervalVar(r.y, r.h, ""))
    m.AddNoOverlap2D(xs, ys)
    m.Minimize(sum(moves))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 8
    status = solver.Solve(m)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status": solver.StatusName(status).lower(),
            "moves": [],
            "solve_ms": ms,
            "reason": "no valid placement of these stations exists"
            if status == cp_model.INFEASIBLE
            else f"none found in {time_limit_s:g} s",
        }
    out = []
    for s in sts:
        nx, ny = solver.Value(V[s.id].x), solver.Value(V[s.id].y)
        if (nx, ny) != (s.x, s.y):
            out.append({"id": s.id, "x": nx / DM, "y": ny / DM, "from": [s.x / DM, s.y / DM]})
    return {
        "status": "optimal" if status == cp_model.OPTIMAL else "feasible",
        "moves": out,
        "total_move_m": round(solver.ObjectiveValue() / DM, 1),
        "solve_ms": ms,
    }
