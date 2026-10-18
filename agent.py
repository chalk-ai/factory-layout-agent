"""Claude designs a production line; CP-SAT decides whether it is valid.

The agent gets three tools: the site, the equipment catalog, and `check_layout`, which runs the
solver in `solver.py`. It must call `check_layout` until the layout passes; the loop refuses to
finish on an unverified layout.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

import factory
import solver

MAX_CHECKS = 8
MAX_TURNS = 16
MAX_SECONDS = 300.0
MAX_TOKENS = 32000

SYSTEM_PROMPT = """You are a manufacturing engineer. You lay out production lines inside one \
existing building, and an independent constraint solver checks every layout you propose.

How to work:
1. Call get_site and list_equipment once each.
2. Design the process as steps from a receiving step to a shipping step (steps list the steps \
they come after, so parallel sub-lines can merge). Use only catalog equipment. Each step needs \
enough machines that the sum of their uph meets the target rate. If the user gives no rate, use \
60 units per hour and say so. Never lower a rate the user asked for; if the building cannot fit \
it, say so instead of proposing a slower line.
3. Give every station coordinates and call check_layout with the complete layout. Before you \
place anything, plan zones on the floor so material flows from the receiving doors to the \
shipping doors.
4. check_layout runs a constraint solver and is the only judge of validity. Fix every \
violation it reports and call check_layout again with the complete corrected layout. When it \
suggests solver moves, you may adopt them. Never claim a layout is valid before check_layout \
says so.
5. When check_layout returns valid, reply with a plain-text summary of at most 90 words: the \
steps, machine counts, and the bottleneck step with its utilization (rate / capacity). No \
markdown: no headings, bold, bullets or tables.

Use short station ids such as saw1, booth2, oven1. If the request is not about setting up a \
manufacturing or assembly line, reply in one sentence that you only lay out production lines, \
and call no tools. If the product needs machines the catalog lacks, approximate with the \
closest ones and say so in the summary."""

LAYOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string"},
        "rate_per_hour": {"type": "integer", "description": "finished units per hour"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "after": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ids of the steps that feed this one",
                    },
                },
                "required": ["id", "name", "after"],
            },
        },
        "stations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "description": "equipment type from the catalog"},
                    "step": {"type": "string"},
                    "x": {"type": "number", "description": "south-west corner, meters east"},
                    "y": {"type": "number", "description": "south-west corner, meters north"},
                    "rotated": {"type": "boolean", "description": "swap width and depth"},
                },
                "required": ["id", "type", "step", "x", "y"],
            },
        },
    },
    "required": ["product", "rate_per_hour", "steps", "stations"],
}

TOOLS = [
    {
        "name": "get_site",
        "description": "The building: size, aisle, columns, exits, docks, exhaust wall, and the "
        + "rules every layout must satisfy.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_equipment",
        "description": "The equipment catalog: footprint (w x d meters), access zone, units per "
        + "hour (uph) and hazard tags for each machine type.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_layout",
        "description": "Verify a complete layout with a constraint solver. Returns valid, or the "
        + "violated rules and, when the stations can be fixed by moving them, the smallest set "
        + "of moves the solver found.",
        "input_schema": LAYOUT_SCHEMA,
        # Stream the (large) layout as it is written so the page can show progress.
        "eager_input_streaming": True,
    },
]


@dataclass
class RunConfig:
    client: anthropic.Anthropic
    model: str
    effort: str


@dataclass
class RunState:
    checks: int = 0
    last_valid: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    rounds: list[dict[str, Any]] = field(default_factory=list)


Emit = Callable[[dict[str, Any]], None]


def _verdict_for_model(v: dict[str, Any], rep: dict[str, Any] | None, checks_left: int) -> str:
    if v["structure_errors"]:
        return json.dumps(
            {"valid": False, "structure_errors": v["structure_errors"], "checks_left": checks_left}
        )
    out: dict[str, Any] = {
        "valid": v["valid"],
        "constraints_checked": v["constraints"],
        "violations": [x["message"] for x in v["violations"][:30]],
        "checks_left": checks_left,
    }
    if len(v["violations"]) > 30:
        out["more_violations"] = len(v["violations"]) - 30
    if rep and rep.get("moves"):
        out["solver_moves"] = [{"id": mv["id"], "x": mv["x"], "y": mv["y"]} for mv in rep["moves"]]
        out["solver_moves_note"] = (
            "Moving these stations to these x,y "
            + f"({rep['total_move_m']} m total) satisfies every "
            + "geometric rule."
        )
    elif rep and rep.get("reason"):
        out["solver_moves_note"] = f"No repair by moving stations: {rep['reason']}."
    return json.dumps(out)


def run(cfg: RunConfig, prompt: str, emit: Emit) -> RunState:
    st = RunState()
    t0 = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    final_text = ""

    for _turn in range(MAX_TURNS):
        if time.monotonic() - t0 > MAX_SECONDS:
            emit({"type": "error", "message": "The run hit its time limit."})
            break
        resp = _stream_turn(cfg, messages, emit, st)
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        texts = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
        for b in resp.content:
            if b.type != "tool_use":
                continue
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": _run_tool(b.name, b.input, st, emit),
                }
            )
        if results:
            messages.append({"role": "user", "content": results})
            if st.checks >= MAX_CHECKS and not st.last_valid:
                emit({"type": "error", "message": f"No valid layout after {MAX_CHECKS} checks."})
                break
            continue
        final_text = "\n".join(texts)
        if st.last_valid or st.checks == 0:
            # Either verified, or a refusal that never proposed a layout.
            break
        messages.append(
            {
                "role": "user",
                "content": "check_layout has not passed yet. Fix the "
                + "violations and call check_layout again.",
            }
        )

    emit(
        {
            "type": "done",
            "valid": st.last_valid,
            "summary": final_text if st.last_valid or st.checks == 0 else "",
            "checks": st.checks,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "tokens": {"input": st.input_tokens, "output": st.output_tokens},
            "model": cfg.model,
        }
    )
    return st


def _stream_turn(cfg: RunConfig, messages: list[dict[str, Any]], emit: Emit, st: RunState) -> Any:
    chars = 0
    last = 0.0
    in_layout = False
    with cfg.client.messages.stream(
        model=cfg.model,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
        max_tokens=MAX_TOKENS,
        # Summarized thinking streams a readable account of the reasoning to the page.
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": cfg.effort},
    ) as stream:
        for ev in stream:
            if ev.type == "thinking":
                emit({"type": "thinking_delta", "text": ev.thinking})
            elif ev.type == "content_block_start":
                in_layout = (
                    ev.content_block.type == "tool_use" and ev.content_block.name == "check_layout"
                )
                if in_layout:
                    emit({"type": "writing", "chars": 0, "round": st.checks + 1})
            elif ev.type == "input_json" and in_layout:
                chars += len(ev.partial_json)
                now = time.monotonic()
                if now - last > 0.3:
                    last = now
                    emit({"type": "writing", "chars": chars, "round": st.checks + 1})
            elif ev.type == "text":
                emit({"type": "text_delta", "text": ev.text})
        resp = stream.get_final_message()
    st.input_tokens += resp.usage.input_tokens
    st.output_tokens += resp.usage.output_tokens
    emit({"type": "turn_end"})
    return resp


def _run_tool(name: str, args: dict[str, Any], st: RunState, emit: Emit) -> str:
    if name == "get_site":
        emit(
            {
                "type": "tool",
                "name": name,
                "summary": "72 x 40 m building, 10 columns, " + "1 aisle, 3 exits, 2 docks",
            }
        )
        return json.dumps(factory.site_description())
    if name == "list_equipment":
        eq = factory.equipment_list()
        emit({"type": "tool", "name": name, "summary": f"{len(eq)} machine types"})
        return json.dumps(eq)
    if name != "check_layout":
        return json.dumps({"error": f"unknown tool {name}"})

    st.checks += 1
    v = solver.verify(args)
    rep = None
    if not v["valid"] and not v["structure_errors"]:
        rep = solver.repair(v["layout"], time_limit_s=3.0)
    st.last_valid = v["valid"]
    st.rounds.append({"layout": v["layout"], "valid": v["valid"]})
    emit({"type": "verdict", "round": st.checks, **_public_verdict(v), "repair": rep})
    return _verdict_for_model(v, rep, MAX_CHECKS - st.checks)


def _public_verdict(v: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v.get(k)
        for k in (
            "valid",
            "structure_errors",
            "violations",
            "families",
            "constraints",
            "model",
            "solves",
            "solve_ms",
            "solver",
            "layout",
        )
    }
