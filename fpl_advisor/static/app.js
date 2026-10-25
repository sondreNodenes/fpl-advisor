const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (x, d = 1) => Number(x).toFixed(d);
const signed = (x, d = 1) => (x > 0 ? "+" : "") + fmt(x, d);
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                set(k, v) { try { localStorage.setItem(k, v); } catch {} } };

const fdr = (f) => `<span class="fdr fdr-${f.difficulty ?? 3}" title="FPL difficulty ${f.difficulty ?? "?"}">${esc(f.opponent)} ${f.home ? "H" : "A"}</span>`;
const fixtures = (opps) => opps && opps.length ? opps.map(fdr).join(" ") : `<span class="muted">blank</span>`;
// The CSP forbids inline style attributes, so bar widths are set through the CSSOM.
const applyWidths = (root) => root.querySelectorAll("[data-w]").forEach((el) => { el.style.width = `${el.dataset.w}%`; });
const heatClass = (p) => "h" + Math.min(5, Math.max(0, Math.floor(p) - 1));  // <2, 2-3, ..., 6+
const flag = (p) => p.availability < 1 ? ` <span class="flag" title="${esc(p.news)}">${Math.round(p.availability * 100)}%</span>` : "";

function kpis(a, best, base) {
  const horizon = best.lineups.reduce((s, l) => s + l.points, 0) - best.hit_cost;
  const baseHorizon = base ? base.lineups.reduce((s, l) => s + l.points, 0) : horizon;
  const span = a.gameweeks.length > 1 ? `GW${a.gameweeks[0]}–${a.gameweeks.at(-1)}` : `GW${a.gameweek}`;
  const tile = (label, value, note = "") => `<div class="kpi"><span>${label}</span><strong>${value}</strong><em>${note}</em></div>`;
  return `<div class="kpis span-12">
    ${tile(esc(a.team_name), esc(a.manager), `squad after GW${a.picks_gameweek}`)}
    ${tile(`Expected pts GW${a.gameweek}`, fmt(best.lineups[0].points - best.hit_cost), base ? `<span class="delta">${signed(best.first_gain)}</span> vs no transfers` : "")}
    ${tile(`Expected pts ${span}`, fmt(horizon), base ? `<span class="delta">${signed(horizon - baseHorizon)}</span> vs no transfers` : "")}
    ${tile("Captain", esc(best.captain.name), `vice ${esc(best.vice_captain.name)}`)}
    ${tile("Bank", `£${fmt(a.bank)}m`, best.n_transfers ? `£${fmt(best.bank_after)}m after transfers` : "")}
    ${tile("Free transfers", a.free_transfers, "estimated from history")}
  </div>`;
}

function swapCard(t) {
  const max = Math.max(...t.in.horizon.map((h) => h.points), ...t.out.horizon.map((h) => h.points), 1);
  const rows = t.in.horizon.map((h, i) => {
    const o = t.out.horizon[i];
    return `<div class="gw">GW${h.gameweek}</div><div class="bars">
      <div class="barrow" title="${esc(t.in.name)}: ${fmt(h.points, 2)} pts"><i class="b-in" data-w="${h.points / max * 70}"></i><b>${fmt(h.points)}</b> ${fixtures(h.opponents)}</div>
      <div class="barrow" title="${esc(t.out.name)}: ${fmt(o.points, 2)} pts"><i class="b-out" data-w="${o.points / max * 70}"></i><b>${fmt(o.points)}</b> ${fixtures(o.opponents)}</div></div>`;
  }).join("");
  return `<div class="swap">
    <div class="swap-top">
      <span class="pill">${esc(t.out.name)}<small>${esc(t.out.team)} · £${fmt(t.out.price)}m</small></span>${flag(t.out)}
      <span class="arrow">→</span>
      <span class="pill">${esc(t.in.name)}<small>${esc(t.in.team)} · £${fmt(t.in.price)}m</small></span>
      <span class="gainbadge" title="Player-for-player difference over the horizon">${signed(t.points_gain)} pts</span>
    </div>
    <div class="legend"><span><i class="sw-in"></i>${esc(t.in.name)} (in)</span><span><i class="sw-out"></i>${esc(t.out.name)} (out)</span></div>
    <div class="compare">${rows}</div>
    <ul class="why">${t.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
  </div>`;
}

function transfersCard(a) {
  const best = a.recommendations[0];
  const body = best.n_transfers ? best.transfers.map(swapCard).join("") :
    `<p class="sub">Your current squad is the best use of your transfers right now. ${esc(best.reason)}.</p>`;
  const alts = a.recommendations.slice(1).map((r) => `<div class="alt">
      <span>${r.n_transfers ? r.transfers.map((t) => `${esc(t.out.name)} → ${esc(t.in.name)}`).join(", ") : "No transfers"}</span>
      <span class="muted">${esc(r.reason)}</span></div>`).join("");
  const altDetails = a.recommendations.slice(1).filter((r) => r.n_transfers).map((r) =>
    `<p class="sub mt12">Option ${r.rank}: ${esc(r.reason)}</p>${r.transfers.map(swapCard).join("")}`).join("");
  return `<section class="card span-8">
    <h2>Transfer plan</h2><p class="sub">Best moves now, judged on GW${a.gameweeks[0]}–${a.gameweeks.at(-1)} with your free transfers and the −4 hit.</p>
    <p class="headline">${best.n_transfers ? best.transfers.map((t) => `${esc(t.out.name)} → ${esc(t.in.name)}`).join(", ") : "Hold"}: <span class="normal">${esc(best.reason)}</span></p>
    ${body}
    <details><summary>Other options</summary>${alts}${altDetails}</details>
  </section>`;
}

function captainCard(best, a) {
  const opts = [...best.starting].sort((x, y) => y.predicted_points - x.predicted_points).slice(0, 5);
  const max = opts[0]?.predicted_points || 1;
  const rows = opts.map((p) => `<div class="cap-row" title="${esc(p.name)}: ${fmt(p.predicted_points, 2)} expected points">
    <span class="name">${esc(p.name)}${p.id === best.captain.id ? '<span class="tag">C</span>' : p.id === best.vice_captain.id ? '<span class="tag">V</span>' : ""}</span>
    <span class="track"><i data-w="${p.predicted_points / max * 75}"></i><b>${fmt(p.predicted_points)}</b></span></div>`).join("");
  const weekly = best.lineups.map((l) => `GW${l.gameweek}: ${esc(l.captain)}`).join(" · ");
  return `<section class="card span-4"><h2>Captaincy GW${a.gameweek}</h2>
    <p class="sub">Expected points among your starters (a captain scores double).</p>${rows}
    <p class="sub after">Planned captains: ${weekly}</p></section>`;
}

function chipsCard(a) {
  if (!a.chips.length) return "";
  const rows = a.chips.map((c) => {
    const vals = c.values.length ? c.values.map((v) => `GW${v.gameweek} +${fmt(v.value)}`).join(" · ") + ` · play from +${fmt(c.threshold, 0)}` : "";
    const label = {play: "Play now", save: "Save", unavailable: "Used"}[c.verdict] || c.verdict;
    return `<div class="chip"><div class="chip-head">${esc(c.name)}<span class="verdict v-${esc(c.verdict)}">${label}</span></div>
      <p>${esc(c.detail)}</p>${vals ? `<div class="vals">${vals}</div>` : ""}</div>`;
  }).join("");
  return `<section class="card span-4"><h2>Chips</h2>
    <p class="sub">Extra expected points each chip would add, per gameweek.</p>${rows}</section>`;
}

function shirt(p, best, newIds) {
  const badge = p.id === best.captain.id ? "C" : p.id === best.vice_captain.id ? "V" : "";
  return `<div class="shirt${newIds.has(p.id) ? " new" : ""}" title="${esc(p.name)} · ${esc(p.team)} · £${fmt(p.price)}m${p.news ? " · " + esc(p.news) : ""}">
    ${badge ? `<span class="badge">${badge}</span>` : ""}
    <div class="n">${esc(p.name)}${flag(p)}</div><div class="p">${fmt(p.predicted_points)}</div>
    <div>${fixtures(p.opponents)}</div></div>`;
}

function pitchCard(a, best) {
  const newIds = new Set(best.transfers.map((t) => t.in.id));
  const lines = ["GK", "DEF", "MID", "FWD"].map((pos) =>
    `<div class="line">${best.starting.filter((p) => p.position === pos).map((p) => shirt(p, best, newIds)).join("")}</div>`).join("");
  return `<section class="card span-6"><h2>Best XI GW${a.gameweek}</h2>
    <p class="sub">After the recommended transfers; new signings outlined. Number = expected points.</p>
    <div class="pitch">${lines}</div>
    <div class="bench"><div class="sub label">Bench, in order</div>
      <div class="line">${best.bench.map((p) => shirt(p, best, newIds)).join("")}</div></div></section>`;
}

function plannerCard(a, best) {
  const squad = [...best.starting, ...best.bench];
  const order = {GK: 0, DEF: 1, MID: 2, FWD: 3};
  squad.sort((x, y) => order[x.position] - order[y.position] || y.horizon_points - x.horizon_points);
  const newIds = new Set(best.transfers.map((t) => t.in.id));
  const head = a.gameweeks.map((g) => `<th class="c">GW${g}</th>`).join("");
  const rows = squad.map((p) => `<tr><td>${esc(p.name)}${newIds.has(p.id) ? '<span class="tag">NEW</span>' : ""}${flag(p)}</td><td class="muted">${esc(p.position)}</td>
    ${p.horizon.map((h) => `<td class="heat ${heatClass(h.points)}" title="${esc(p.name)} GW${h.gameweek}: ${fmt(h.points, 2)} pts"><span class="p">${fmt(h.points)}</span><span class="o">${h.opponents.length ? h.opponents.map((o) => `${esc(o.opponent)} ${o.home ? "H" : "A"}`).join(", ") : "blank"}</span></td>`).join("")}
    <td class="num"><strong>${fmt(p.horizon_points)}</strong></td></tr>`).join("");
  const totals = best.lineups.map((l) => `<td class="c"><strong>${fmt(l.points)}</strong><br><span class="muted">bench ${fmt(l.bench_points)}</span></td>`).join("");
  return `<section class="card span-6"><h2>Fixture planner</h2>
    <p class="sub">Expected points per gameweek for the squad after transfers. Darker = more points.</p>
    <div class="scroll"><table><thead><tr><th>Player</th><th></th>${head}<th class="num">Total</th></tr></thead>
    <tbody>${rows}<tr><td colspan="2"><strong>XI + captain</strong></td>${totals}<td></td></tr></tbody></table></div></section>`;
}

function render(a) {
  const best = a.recommendations[0];
  const base = a.recommendations.find((r) => r.n_transfers === 0);
  $("#gwlabel").textContent = `GW${a.gameweek} · data through GW${a.data_through_gameweek}`;
  $("#out").innerHTML = `<div class="grid">
    ${kpis(a, best, base)}
    ${transfersCard(a)}
    <div class="span-4 side">${captainCard(best, a)}${chipsCard(a)}</div>
    ${pitchCard(a, best)}
    ${plannerCard(a, best)}
    <section class="card span-12"><h2>Notes</h2><ul class="notes">${a.notes.map((n) => `<li>${esc(n)}</li>`).join("")}
      <li>"Expected points" already include each player's chance of playing. FDR badges use FPL's difficulty (1 easy – 5 hard).</li></ul></section>
  </div>`;
  applyWidths($("#out"));
}

const topState = {sort: "next", pos: ""};
async function loadTop() {
  try {
    const q = new URLSearchParams({limit: 30, sort: topState.sort});
    if (topState.pos) q.set("position", topState.pos);
    const r = await fetch(`/predictions?${q}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail);
    $("#topsub").textContent = `GW${d.gameweeks[0]}–${d.gameweeks.at(-1)}. Expected points, already scaled by chance of playing.`;
    const head = d.gameweeks.map((g) => `<th class="c">GW${g}</th>`).join("");
    const rows = d.players.map((p, i) => `<tr><td class="muted">${i + 1}</td><td>${esc(p.name)}${flag(p)}</td><td>${esc(p.team)}</td><td>${esc(p.position)}</td>
      <td class="num">£${fmt(p.price)}m</td><td class="num">${fmt(p.form_points)}</td>
      ${p.horizon.map((h) => `<td class="heat ${heatClass(h.points)}" title="${esc(p.name)} GW${h.gameweek}: ${fmt(h.points, 2)}"><span class="p">${fmt(h.points)}</span><span class="o">${h.opponents.map((o) => `${esc(o.opponent)} ${o.home ? "H" : "A"}`).join(", ") || "blank"}</span></td>`).join("")}
      <td class="num"><strong>${fmt(p.horizon_points)}</strong></td><td class="num muted">${p.ep_next ?? ""}</td></tr>`).join("");
    $("#top").innerHTML = `<table><thead><tr><th>#</th><th>Player</th><th>Club</th><th>Pos</th><th class="num">Price</th>
      <th class="num" title="Average points over the last 3 gameweeks">Form</th>${head}<th class="num">Total</th><th class="num" title="FPL's own ep_next">FPL ep</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { $("#top").innerHTML = `<span class="error">Could not load predictions${e.message ? ": " + esc(e.message) : ""}</span>`; }
}
for (const [id, key] of [["#sorttabs", "sort"], ["#postabs", "pos"]]) {
  $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    topState[key] = b.dataset[key];
    $(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    loadTop();
  });
}

async function advise(team) {
  const btn = $("#go");
  btn.disabled = true; btn.textContent = "Working…";
  $("#out").innerHTML = `<div class="card empty">Fetching your squad and planning three gameweeks ahead…</div>`;
  try {
    const r = await fetch("/recommend", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({team})});
    const d = await r.json();
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Invalid request");
    store.set("team", team);
    history.replaceState(null, "", `?team=${encodeURIComponent(team)}`);
    render(d);
  } catch (e) {
    $("#out").innerHTML = `<div class="card error">${esc(e.message)}</div>`;
  } finally { btn.disabled = false; btn.textContent = "Get advice"; }
}
$("#form").addEventListener("submit", (ev) => { ev.preventDefault(); advise($("#team").value.trim()); });

const initial = new URLSearchParams(location.search).get("team") || store.get("team");
if (initial) { $("#team").value = initial; advise(initial); }
loadTop();
