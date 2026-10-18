"use strict";
// Factory layout agent page: replays a recorded run on load, streams live runs from /api/design,
// and draws every proposed layout with the solver's verdict on top.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const TAG_COLORS = {
  flammable: "#F0B35A", hot: "#E0896A", dust: "#C8B07E", sparks: "#E6D36B",
  receive: "#8FBCE0", ship: "#8FBCE0",
};
const BASE_COLOR = "#70CD96";
const BAD = "#FF6B6B";
const ACCENT = "#BBD96D";

const S = {
  site: null, eq: {}, families: {}, presets: [],
  rounds: [], sel: -1, feed: [], cur: null, live: false, running: false, ctrl: null, done: null,
};

// ---------- analytics + embed sizing ----------

function track(action, label) {
  // Only fixed action names and preset ids leave the frame, never the visitor's text.
  try { window.parent.postMessage({ type: "chalk-demo-interaction", action, label }, "*"); } catch (_) { /* no parent */ }
}
function reportHeight() {
  try {
    window.parent.postMessage({ type: "chalk-demo-height", height: Math.ceil(document.body.getBoundingClientRect().height) + 4 }, "*");
  } catch (_) { /* no parent */ }
}
new ResizeObserver(reportHeight).observe(document.body);

// ---------- event handling (live and replay share this) ----------

function reset(live) {
  S.rounds = []; S.sel = -1; S.feed = []; S.cur = null; S.done = null; S.live = live;
  renderAll();
}

function onEvent(e) {
  switch (e.type) {
    case "start":
      $("model").textContent = (e.model || "").replace("anthropic/", "");
      break;
    case "thinking_delta":
    case "text_delta": {
      const kind = e.type === "thinking_delta" ? "think" : "txt";
      if (!S.cur || S.cur.kind !== kind) { S.cur = { kind, text: "" }; S.feed.push(S.cur); }
      S.cur.text += e.text;
      break;
    }
    case "turn_end":
      S.cur = null;
      break;
    case "tool":
      S.cur = null;
      S.feed.push({ kind: "tool", name: e.name, summary: e.summary });
      break;
    case "writing": {
      S.cur = null;
      const w = S.feed.find((f) => f.kind === "writing" && f.round === e.round);
      if (w) w.chars = e.chars; else S.feed.push({ kind: "writing", round: e.round, chars: e.chars });
      break;
    }
    case "verdict": {
      S.feed = S.feed.filter((f) => !(f.kind === "writing" && f.round === e.round));
      S.rounds.push(e);
      S.sel = S.rounds.length - 1;
      S.feed.push({ kind: "verdict", round: e.round, v: e });
      renderPlan(); renderVerdict(); renderTabs();
      break;
    }
    case "done":
      S.done = e;
      // The summary already streamed as text; show it once, in the final card.
      if (e.summary) S.feed = S.feed.filter((f) => !(f.kind === "txt" && e.summary.includes(f.text.trim())));
      S.cur = null;
      S.feed.push({ kind: "final", e });
      break;
    case "error":
      S.feed.push({ kind: "err", text: e.message });
      break;
  }
  scheduleFeed();
}

// ---------- live run ----------

async function runLive(prompt) {
  if (S.running) return;
  stopReplay();
  reset(true);
  setRunning(true);
  S.ctrl = new AbortController();
  try {
    const r = await fetch("api/design", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ prompt }), signal: S.ctrl.signal,
    });
    if (!r.ok) {
      let msg = `The demo returned ${r.status}.`;
      try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : "Please write between 3 and 300 characters."; } catch (_) { /* not JSON */ }
      onEvent({ type: "error", message: msg });
      return;
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") onEvent({ type: "error", message: "Lost the connection to the demo. Try again." });
    else onEvent({ type: "error", message: "Stopped." });
  } finally {
    setRunning(false);
  }
}

function setRunning(on) {
  S.running = on;
  $("go").disabled = on;
  $("go").textContent = on ? "Designing…" : "Design the line";
  $("stop").hidden = !on;
  renderMode();
}

// ---------- replay ----------

let replayTimer = null;
async function startReplay() {
  let events;
  try { events = await (await fetch("api/sample")).json(); } catch (_) { return; }
  if (S.running) return;
  reset(false);
  let i = 0;
  const step = () => {
    if (i >= events.length) { replayTimer = null; renderMode(); return; }
    const e = events[i++];
    onEvent(e);
    const next = events[i];
    // Recorded gaps are compressed: model think time becomes a short beat.
    const gap = next ? Math.min(Math.max((next.t || 0) - (e.t || 0), 0) * 1000, e.type === "verdict" ? 1400 : 120) : 0;
    replayTimer = setTimeout(step, gap);
  };
  step();
  renderMode();
}
function stopReplay() { if (replayTimer) { clearTimeout(replayTimer); replayTimer = null; } }

// ---------- rendering ----------

function renderMode() {
  const m = $("mode");
  if (S.live) m.innerHTML = S.running ? "<b>Live run</b> · designing" : "<b>Live run</b>";
  else m.innerHTML = replayTimer ? "Replaying a recorded run" : "Recorded run · press Design for a live one";
}

function renderAll() { renderTabs(); renderPlan(); renderVerdict(); scheduleFeed(); renderMode(); }

function renderTabs() {
  $("tabs").innerHTML = S.rounds.map((r, i) => {
    const mark = r.structure_errors && r.structure_errors.length ? `<span class="x">✗ shape</span>`
      : r.valid ? `<span class="ok">✓ valid</span>` : `<span class="x">✗ ${r.violations.length}</span>`;
    return `<button class="tab${i === S.sel ? " on" : ""}" data-i="${i}">Round ${r.round} ${mark}</button>`;
  }).join("");
}
$("tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest(".tab"); if (!b) return;
  S.sel = Number(b.dataset.i); renderTabs(); renderPlan(); renderVerdict();
  track("select_round", `round_${S.rounds[S.sel].round}`);
});

function dims(st) {
  const e = S.eq[st.type] || { w: 2, d: 2, access: 0 };
  return st.rotated ? { w: e.d, h: e.w, a: e.access } : { w: e.w, h: e.d, a: e.access };
}
function colorFor(type) {
  const e = S.eq[type]; if (!e) return BASE_COLOR;
  for (const t of ["flammable", "hot", "sparks", "dust", "receive", "ship"]) if (e.tags.includes(t)) return TAG_COLORS[t];
  return BASE_COLOR;
}

function renderPlan() {
  if (!S.site) return;
  const W = S.site.building.width, H = S.site.building.depth;
  const Y = (y) => H - y;
  const r = S.rounds[S.sel];
  const parts = [];
  const P = 5;
  parts.push(`<svg viewBox="${-P} ${-P} ${W + 2 * P} ${H + 2 * P}" xmlns="http://www.w3.org/2000/svg" font-family="ui-monospace,Menlo,monospace" role="img" aria-label="Factory floor plan">`);
  parts.push(`<defs><pattern id="hatch" width="1.2" height="1.2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="1.2" stroke="#32645B" stroke-width=".45"/></pattern>
    <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="${ACCENT}"/></marker>
    <marker id="arrbad" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="${BAD}"/></marker></defs>`);
  parts.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="#183530" stroke="#B9CFC7" stroke-width=".35"/>`);
  for (let x = 6; x < W; x += 6) parts.push(`<line x1="${x}" y1="0" x2="${x}" y2="${H}" stroke="#32645B" stroke-width=".06"/>`);
  for (let y = 6; y < H; y += 6) parts.push(`<line x1="0" y1="${Y(y)}" x2="${W}" y2="${Y(y)}" stroke="#32645B" stroke-width=".06"/>`);
  const a = S.site.aisle;
  parts.push(`<rect x="${a.x0}" y="${Y(a.y1)}" width="${a.x1 - a.x0}" height="${a.y1 - a.y0}" fill="url(#hatch)"/>`);
  parts.push(`<text x="${W / 2}" y="${Y((a.y0 + a.y1) / 2) + 0.5}" font-size="1.4" fill="#86A79C" text-anchor="middle" letter-spacing=".3">MAIN AISLE · 4 M</text>`);
  // Roof exhaust runs along the north wall.
  parts.push(`<line x1="0" y1="-1.1" x2="${W}" y2="-1.1" stroke="#F0B35A" stroke-width=".3" stroke-dasharray="1 .8"/>`);
  parts.push(`<text x="${W}" y="-2.2" font-size="1.4" fill="#F0B35A" text-anchor="end">ROOF EXHAUST · NORTH WALL</text>`);
  const d = S.site.docks;
  parts.push(`<line x1="-.6" y1="${Y(d.receiving.y1)}" x2="-.6" y2="${Y(d.receiving.y0)}" stroke="${ACCENT}" stroke-width="1"/>`);
  parts.push(`<text transform="translate(-2.2 ${Y((d.receiving.y0 + d.receiving.y1) / 2)}) rotate(-90)" font-size="1.4" fill="${ACCENT}" text-anchor="middle">RECEIVING</text>`);
  parts.push(`<line x1="${W + 0.6}" y1="${Y(d.shipping.y1)}" x2="${W + 0.6}" y2="${Y(d.shipping.y0)}" stroke="${ACCENT}" stroke-width="1"/>`);
  parts.push(`<text transform="translate(${W + 2.2} ${Y((d.shipping.y0 + d.shipping.y1) / 2)}) rotate(90)" font-size="1.4" fill="${ACCENT}" text-anchor="middle">SHIPPING</text>`);
  for (const e of S.site.exits) {
    parts.push(`<rect x="${e.x0}" y="${Y(e.y1)}" width="${e.x1 - e.x0}" height="${e.y1 - e.y0}" fill="none" stroke="#70CD96" stroke-width=".2" stroke-dasharray=".5 .4"/>`);
    const ty = e.y0 === 0 ? H + 2.6 : -2.2;
    parts.push(`<text x="${(e.x0 + e.x1) / 2}" y="${ty}" font-size="1.3" fill="#70CD96" text-anchor="middle">EXIT</text>`);
  }
  const cs = S.site.columns.size;
  for (const c of S.site.columns.centers) parts.push(`<rect x="${c.x - cs / 2}" y="${Y(c.y) - cs / 2}" width="${cs}" height="${cs}" fill="#B9CFC7"/>`);

  if (r && r.layout && r.layout.stations) {
    const L = r.layout;
    const pos = {};
    for (const st of L.stations) { const k = dims(st); pos[st.id] = { ...k, x: st.x, y: st.y, cx: st.x + k.w / 2, cy: st.y + k.h / 2, st }; }
    const bad = new Set();
    for (const v of r.violations || []) {
      if (v.family === "flow" || v.family === "capacity") { if (v.family === "flow") bad.add(v.stations[0]); continue; }
      v.stations.forEach((s) => bad.add(s));
    }
    // Flow: each station to its nearest station in every following step.
    const byStep = {};
    for (const st of L.stations) (byStep[st.step] = byStep[st.step] || []).push(pos[st.id]);
    const flowBad = new Set((r.violations || []).filter((v) => v.family === "flow").map((v) => v.rule));
    for (const step of L.steps) {
      const downs = byStep[step.id] || [];
      for (const up of step.after) for (const u of byStep[up] || []) {
        let best = null, bd = 1e9;
        for (const v of downs) { const dd = Math.abs(u.cx - v.cx) + Math.abs(u.cy - v.cy); if (dd < bd) { bd = dd; best = v; } }
        if (!best) continue;
        const isBad = flowBad.has(`flow:${u.st.id}>${step.id}`);
        parts.push(`<line x1="${u.cx}" y1="${Y(u.cy)}" x2="${best.cx}" y2="${Y(best.cy)}" stroke="${isBad ? BAD : ACCENT}" stroke-width="${isBad ? 0.3 : 0.22}" stroke-opacity="${isBad ? 1 : 0.75}" ${isBad ? 'stroke-dasharray=".8 .5" marker-end="url(#arrbad)"' : 'marker-end="url(#arr)"'}/>`);
      }
    }
    for (const id in pos) {
      const p = pos[id];
      parts.push(`<rect x="${p.x - p.a}" y="${Y(p.y + p.h) - p.a}" width="${p.w + 2 * p.a}" height="${p.h + 2 * p.a}" fill="none" stroke="#B9CFC7" stroke-opacity=".35" stroke-width=".1" stroke-dasharray=".4 .3"/>`);
    }
    for (const id in pos) {
      const p = pos[id], e = S.eq[p.st.type] || { name: p.st.type, uph: "?", tags: [] };
      const isBad = bad.has(id);
      const fs = Math.min(1.3, p.w / Math.max(id.length * 0.62, 1), p.h * 0.55);
      parts.push(`<g><title>${esc(id)} · ${esc(e.name)} · step ${esc(p.st.step)} · ${e.uph}/h${e.tags.length ? " · " + esc(e.tags.join(", ")) : ""}</title>
        <rect x="${p.x}" y="${Y(p.y + p.h)}" width="${p.w}" height="${p.h}" rx=".25" fill="${colorFor(p.st.type)}" fill-opacity=".9" stroke="${isBad ? BAD : "#12302A"}" stroke-width="${isBad ? 0.45 : 0.12}"/>
        <text x="${p.cx}" y="${Y(p.cy) + fs * 0.35}" font-size="${fs.toFixed(2)}" fill="#12302A" text-anchor="middle" font-weight="600">${esc(id)}</text></g>`);
    }
    for (const v of r.violations || []) {
      if ((v.family === "clearance" || v.family === "hazard") && pos[v.stations[0]] && pos[v.stations[1]]) {
        const p = pos[v.stations[0]], q = pos[v.stations[1]];
        parts.push(`<line x1="${p.cx}" y1="${Y(p.cy)}" x2="${q.cx}" y2="${Y(q.cy)}" stroke="${BAD}" stroke-width=".25" stroke-dasharray=".6 .4"/>`);
      }
    }
    for (const m of (r.repair && r.repair.moves) || []) {
      const p = pos[m.id]; if (!p) continue;
      parts.push(`<rect x="${m.x}" y="${Y(m.y + p.h)}" width="${p.w}" height="${p.h}" rx=".25" fill="none" stroke="${ACCENT}" stroke-width=".25" stroke-dasharray=".7 .4"/>`);
      parts.push(`<line x1="${p.cx}" y1="${Y(p.cy)}" x2="${m.x + p.w / 2}" y2="${Y(m.y + p.h / 2)}" stroke="${ACCENT}" stroke-width=".15" stroke-dasharray=".3 .3"/>`);
    }
  } else {
    parts.push(`<text x="${W / 2}" y="${H / 2 - 4}" font-size="1.8" fill="#86A79C" text-anchor="middle">${S.running ? "Claude is designing the line…" : "No layout yet"}</text>`);
  }
  parts.push("</svg>");
  $("plan").innerHTML = parts.join("");
}

function renderLegend() {
  const items = [["Spray / coat", TAG_COLORS.flammable], ["Hot", TAG_COLORS.hot], ["Dust", TAG_COLORS.dust],
    ["Sparks", TAG_COLORS.sparks], ["Dock", TAG_COLORS.receive], ["Other", BASE_COLOR]];
  $("legend").innerHTML = items.map(([l, c]) => `<span><i style="background:${c}"></i>${l}</span>`).join("")
    + `<span><i style="border:1px dashed #B9CFC7"></i>Access zone</span><span><i style="background:${ACCENT};height:3px"></i>Flow</span>`
    + `<span><i style="border:2px solid ${BAD}"></i>Violation</span><span><i style="border:1px dashed ${ACCENT}"></i>Solver's fix</span>`;
}

function renderVerdict() {
  const r = S.rounds[S.sel];
  const el = $("verdict");
  if (!r) { el.innerHTML = `<span class="badge idle">${S.running ? "Waiting for the first layout" : "No layout yet"}</span>`; return; }
  if (r.structure_errors && r.structure_errors.length) {
    el.innerHTML = `<span class="badge bad">✗ Malformed layout</span><ul class="viol">${r.structure_errors.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`;
    return;
  }
  const n = r.violations.length;
  const head = r.valid
    ? `<span class="badge ok">✓ Valid · all ${r.constraints} rules hold</span>`
    : `<span class="badge bad">✗ ${n} violation${n === 1 ? "" : "s"} of ${r.constraints} rules</span>`;
  const rows = Object.values(r.families).filter((f) => f.checked > 0).map((f) =>
    `<tr><td>${esc(f.label)}</td><td class="n">${f.violated ? `<span class="x">✗ ${f.violated}</span> / ${f.checked}` : `<span class="ok">✓</span> ${f.checked}`}</td></tr>`).join("");
  const shown = r.violations.slice(0, 8);
  const list = n ? `<ul class="viol">${shown.map((v) => `<li>${esc(v.message)}</li>`).join("")}${n > shown.length ? `<li>…and ${n - shown.length} more</li>` : ""}</ul>` : "";
  let fix = "";
  if (r.repair && r.repair.moves && r.repair.moves.length) {
    fix = `<div class="fix">CP-SAT found a fix: move ${r.repair.moves.length} station${r.repair.moves.length === 1 ? "" : "s"}, ${r.repair.total_move_m} m in total (dashed outlines). It went back to Claude with the violations.</div>`;
  } else if (r.repair && r.repair.reason) {
    fix = `<div class="fix">No fix by moving stations: ${esc(r.repair.reason)}.</div>`;
  }
  const target = r.layout ? `<div class="foot">Target: ${esc(r.layout.rate_per_hour)}/h${r.layout.product ? " · " + esc(r.layout.product) : ""} · ${r.layout.stations.length} stations</div>` : "";
  const mdl = r.model ? ` · ${r.model.variables.toLocaleString()} vars, ${r.model.constraints.toLocaleString()} constraints` : "";
  el.innerHTML = `${head}${target}${list}${fix}<table>${rows}</table><div class="foot">${esc(r.solver || "")}${mdl} · ${r.solves} solve${r.solves === 1 ? "" : "s"} in ${r.solve_ms} ms</div>`;
}

let feedQueued = false;
function scheduleFeed() { if (!feedQueued) { feedQueued = true; requestAnimationFrame(renderFeed); } }
function renderFeed() {
  feedQueued = false;
  const el = $("feed");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  if (!S.feed.length) { el.innerHTML = `<div class="empty">${S.running ? "Starting…" : "Loading…"}</div>`; return; }
  const last = S.feed[S.feed.length - 1];
  el.innerHTML = S.feed.map((f) => {
    switch (f.kind) {
      case "think": return `<div class="it think${f === last && (S.running || replayTimer) ? " live" : ""}">${esc(f.text.trim())}</div>`;
      case "txt": return f.text.trim() ? `<div class="it txt">${esc(f.text.trim())}</div>` : "";
      case "tool": return `<div class="it tool">→ ${esc(f.name)} <span>${esc(f.summary)}</span></div>`;
      case "writing": return `<div class="it tool">→ check_layout <span>writing round ${f.round} · ${(f.chars / 1000).toFixed(1)}k chars</span></div>`;
      case "verdict": {
        const v = f.v;
        if (v.structure_errors && v.structure_errors.length) return `<div class="it v x">Round ${f.round}: ✗ malformed (${v.structure_errors.length} problems)</div>`;
        return v.valid
          ? `<div class="it v ok">Round ${f.round}: ✓ valid · ${v.constraints} rules · ${v.solve_ms} ms</div>`
          : `<div class="it v x">Round ${f.round}: ✗ ${v.violations.length} violations · ${v.constraints} rules · ${v.solve_ms} ms</div>`;
      }
      case "final": {
        const e = f.e;
        const stats = `${e.checks} solver check${e.checks === 1 ? "" : "s"} · ${e.elapsed_s} s · ${(e.tokens.input / 1000).toFixed(1)}k in / ${(e.tokens.output / 1000).toFixed(1)}k out tokens`;
        if (!e.summary) return e.checks ? `<div class="it stats">${stats}</div>` : "";
        return `<div class="it final">${esc(e.summary)}<div class="stats">${stats}</div></div>`;
      }
      case "err": return `<div class="it err">${esc(f.text)}</div>`;
    }
    return "";
  }).join("");
  if (atBottom || S.running || replayTimer) el.scrollTop = el.scrollHeight;
}

// ---------- boot ----------

$("ask").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const p = $("prompt").value.trim();
  if (p.length < 3) return;
  const preset = S.presets.find((x) => x.prompt === p);
  track("run_design", preset ? preset.id : "custom");
  runLive(p);
});
$("stop").addEventListener("click", () => { if (S.ctrl) S.ctrl.abort(); track("stop_design"); });

(async () => {
  const r = await fetch("api/site");
  const j = await r.json();
  S.site = j.site; S.families = j.families; S.presets = j.presets;
  for (const e of j.equipment) S.eq[e.type] = e;
  $("model").textContent = (j.model || "").replace("anthropic/", "");
  const row = $("presets");
  for (const p of S.presets) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = p.label;
    b.addEventListener("click", () => { $("prompt").value = p.prompt; $("prompt").focus(); track("select_preset", p.id); });
    row.insertBefore(b, $("mode"));
  }
  renderLegend();
  renderAll();
  startReplay();
})();
