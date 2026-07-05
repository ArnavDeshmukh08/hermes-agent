/* ═══════════════════════════════════════════════════════════════════
   JACK OS — dashboard client
   ═══════════════════════════════════════════════════════════════════ */
"use strict";

const $ = (id) => document.getElementById(id);
const REFRESH_MS = 20000;
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ── boot sequence ──────────────────────────────────────────────── */
const BOOT = [
  ["ok", "◇ kernel ......... online"],
  ["ok", "◇ agent mesh ..... 6 nodes"],
  ["dim", "◇ tailscale bridge ... linking"],
  ["ok", "◇ orsa crm ....... mounted"],
  ["ok", "◇ gmail · calendar ... authorized"],
  ["dim", "◇ core telemetry ... streaming"],
  ["ok", "▶ JACK OS READY"],
];
function boot() {
  const box = $("bootLines");
  if (reduced) { finishBoot(); return; }
  BOOT.forEach(([cls, txt], i) => {
    const d = document.createElement("div");
    d.className = cls; d.style.animationDelay = `${i * 0.28}s`;
    d.textContent = txt; box.appendChild(d);
  });
  setTimeout(finishBoot, BOOT.length * 280 + 650);
}
function finishBoot() { $("boot").classList.add("done"); }

/* ── starfield / constellation ──────────────────────────────────── */
function starfield() {
  if (reduced) return;
  const c = $("starfield"), ctx = c.getContext("2d");
  let w, h, pts;
  const resize = () => {
    w = c.width = innerWidth; h = c.height = innerHeight;
    const n = Math.min(90, Math.floor((w * h) / 22000));
    pts = Array.from({ length: n }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.22, vy: (Math.random() - 0.5) * 0.22,
      r: Math.random() * 1.6 + 0.4,
    }));
  };
  resize(); addEventListener("resize", resize);
  const draw = () => {
    ctx.clearRect(0, 0, w, h);
    for (const p of pts) {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7);
      ctx.fillStyle = "rgba(120,200,255,0.55)"; ctx.fill();
    }
    for (let i = 0; i < pts.length; i++)
      for (let j = i + 1; j < pts.length; j++) {
        const a = pts[i], b = pts[j], dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 15000) {
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
          ctx.strokeStyle = `rgba(0,229,255,${0.14 * (1 - d2 / 15000)})`;
          ctx.lineWidth = 0.5; ctx.stroke();
        }
      }
    requestAnimationFrame(draw);
  };
  draw();
}

/* ── clock (IST) ────────────────────────────────────────────────── */
function clock() {
  const t = new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false });
  $("clock").textContent = t + " IST";
}

/* ── helpers ────────────────────────────────────────────────────── */
async function getJSON(path) {
  try { const r = await fetch(path, { cache: "no-store" }); return await r.json(); }
  catch { return { available: false }; }
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function countUp(el, to) {
  if (reduced) { el.textContent = to; return; }
  const from = 0, dur = 700, t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur);
    el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function ring(pct, label, val, color = "var(--cyan)") {
  const R = 32, C = 2 * Math.PI * R, off = C * (1 - Math.max(0, Math.min(1, pct)));
  return `<div class="ring"><svg width="78" height="78" viewBox="0 0 78 78">
    <circle class="track" cx="39" cy="39" r="${R}" fill="none" stroke-width="6"/>
    <circle class="fill" cx="39" cy="39" r="${R}" fill="none" stroke-width="6"
      stroke="${color}" stroke-dasharray="${C}" stroke-dashoffset="${off}"/></svg>
    <div class="center"><b>${val}</b><small>${label}</small></div></div>`;
}
const offline = (msg = "source offline") => `<div class="offline">⚠ ${msg}</div>`;

/* ── renderers ──────────────────────────────────────────────────── */
function renderCore(s) {
  const reactor = $("reactor"), label = $("coreLabel"), sub = $("coreSub");
  const gatewayUp = s.agents?.find((a) => a.id === "hermes-gateway")?.state === "active";
  reactor.classList.toggle("down", !gatewayUp);
  label.className = "label " + (gatewayUp ? "online" : "down");
  label.textContent = gatewayUp ? "ONLINE" : "DEGRADED";
  sub.textContent = s.vps_reachable ? `${s.online}/${s.total} systems nominal` : "core link lost";
  $("coreVitals").innerHTML =
    `<div><div class="n" id="cvOnline">0</div><div class="l">Online</div></div>
     <div><div class="n">${s.vps_reachable ? "◉" : "◌"}</div><div class="l">Core Link</div></div>
     <div><div class="n">6</div><div class="l">Nodes</div></div>`;
  countUp($("cvOnline"), s.online || 0);
  $("agentCount").textContent = `${s.online} / ${s.total}`;
  $("coreLink").textContent = s.vps_reachable ? "◉ linked" : "◌ lost";
}
function renderAgents(s) {
  if (!s.available) { $("agents").innerHTML = offline(); return; }
  $("agentsBadge").textContent = `${s.online}/${s.total} up`;
  $("agents").innerHTML = s.agents.map((a) => `
    <div class="agent">
      <span class="dot ${esc(a.state)}"></span>
      <div><div class="name">${esc(a.name)}</div><div class="role">${esc(a.role)}</div></div>
      <span class="host ${esc(a.host)}">${esc(a.host)}</span>
    </div>`).join("");
}
function renderLeads(d) {
  if (!d.available) { $("leads").innerHTML = offline("orsa unreachable"); return; }
  const b = d.buckets || {}, tot = (b.hot + b.warm + b.cold) || 1;
  $("leads").innerHTML = `
    <div class="metrics">
      <div class="metric"><div class="n" id="lTot">0</div><div class="l">Total</div></div>
      <div class="metric"><div class="n" id="lNew">0</div><div class="l">New · 7d</div></div>
      <div class="metric"><div class="n">${d.avg_score ?? "—"}</div><div class="l">Avg score</div></div>
    </div>
    <div class="bar">
      <span class="hot" style="width:${(b.hot / tot) * 100}%"></span>
      <span class="warm" style="width:${(b.warm / tot) * 100}%"></span>
      <span class="cold" style="width:${(b.cold / tot) * 100}%"></span>
    </div>
    <div class="legend">
      <span><i style="background:var(--magenta)"></i>Hot ${b.hot}</span>
      <span><i style="background:var(--amber)"></i>Warm ${b.warm}</span>
      <span><i style="background:var(--cyan)"></i>Cold ${b.cold}</span>
    </div>
    <div style="margin-top:12px">${(d.top || []).slice(0, 4).map((l) => `
      <div class="row"><span class="m">${esc(l.name)}</span>
      <span class="s"> · ${esc(l.city || "")} · score ${esc(l.score)}</span></div>`).join("")}</div>`;
  countUp($("lTot"), d.total || 0); countUp($("lNew"), d.new_7d || 0);
}
function renderCalendar(d) {
  if (!d.available) { $("calendar").innerHTML = offline("calendar not linked"); return; }
  if (!d.events.length) { $("calendar").innerHTML = `<div class="offline" style="color:var(--text-dim)">◇ nothing scheduled today</div>`; return; }
  $("calendar").innerHTML = d.events.map((e) => {
    const time = /T/.test(e.start) ? e.start.split("T")[1].slice(0, 5) : "all-day";
    return `<div class="row"><span class="t">${esc(time)}</span> &nbsp;<span class="m">${esc(e.summary)}</span></div>`;
  }).join("");
}
function renderEmail(d) {
  if (!d.available) { $("email").innerHTML = offline("gmail not linked"); return; }
  $("email").innerHTML = `
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px">
      <span class="big-num" id="mCount">0</span><span class="big-cap">unread</span></div>
    ${d.messages.slice(0, 5).map((m) => `<div class="row"><span class="t">${esc(m.from)}</span><br>
      <span class="m" style="font-size:12px">${esc(m.subject)}</span></div>`).join("")}`;
  countUp($("mCount"), d.count || 0);
}
function renderHealth(d) {
  if (!d.available) { $("health").innerHTML = offline("garmin relay down"); return; }
  const sleep = d.sleep || {}, stats = d.stats || {};
  const parts = [];
  if (sleep.score != null) parts.push(ring((sleep.score || 0) / 100, "sleep", sleep.score, "var(--violet)"));
  if (sleep.total_sleep_h != null) parts.push(ring(Math.min(1, (sleep.total_sleep_h || 0) / 8), "hours", sleep.total_sleep_h));
  if (stats.body_battery_high != null) parts.push(ring((stats.body_battery_high || 0) / 100, "battery", stats.body_battery_high, "var(--green)"));
  if (stats.steps != null) parts.push(ring(Math.min(1, (stats.steps || 0) / 10000), "steps", (stats.steps > 999 ? (stats.steps / 1000).toFixed(1) + "k" : stats.steps), "var(--cyan)"));
  $("health").innerHTML = parts.length ? `<div class="ring-wrap">${parts.join("")}</div>` : `<div class="offline" style="color:var(--text-dim)">◇ no data synced today</div>`;
}
function renderReminders(d) {
  $("remBadge").textContent = d.total_pending ? `${d.total_pending} pending` : "";
  if (!d.available) { $("reminders").innerHTML = offline("core link lost"); return; }
  if (!d.upcoming.length) { $("reminders").innerHTML = `<div class="offline" style="color:var(--text-dim)">◇ nothing queued</div>`; return; }
  $("reminders").innerHTML = d.upcoming.map((r) => `
    <div class="row"><span class="m">${esc(r.message)}</span> ${r.recurring ? '<span class="s">↻</span>' : ""}<br>
    <span class="t">${esc(r.fire_at_ist)}</span></div>`).join("");
}
function renderGoals(d) {
  if (!d.available) { $("goals").innerHTML = offline("core link lost"); return; }
  if (!d.goals.length) { $("goals").innerHTML = `<div class="offline" style="color:var(--text-dim)">◇ no active goals</div>`; return; }
  $("goals").innerHTML = d.goals.map((g) => `
    <div class="goal">
      <div><div class="title">${esc(g.title)}</div><div class="meta">${esc(g.type)} · ${esc(g.status)}${g.notes ? ` · ${g.notes} logs` : ""}</div></div>
      ${g.days_left != null ? `<div class="countdown"><b>${g.days_left}</b><small>days left</small></div>` : ""}
    </div>`).join("");
}
function renderNudges(d) {
  $("nudgeBadge").textContent = d.total ? `${d.total} sent` : "";
  if (!d.available) { $("nudges").innerHTML = offline("core link lost"); return; }
  if (!d.recent.length) { $("nudges").innerHTML = `<div class="offline" style="color:var(--text-dim)">◇ no nudges yet</div>`; return; }
  const tier = { P1: "var(--red)", P2: "var(--amber)", P3: "var(--cyan)" };
  $("nudges").innerHTML = d.recent.map((n) => `
    <div class="row"><span style="color:${tier[n.priority] || "var(--text-dim)"}">${esc(n.priority || "◇")}</span>
    <span class="m" style="font-size:12px"> ${esc(n.text).slice(0, 90)}</span></div>`).join("");
}

/* ── command router (preview only) ──────────────────────────────── */
let cmdTimer;
async function classify(text) {
  const chip = $("routeChip");
  if (!text.trim()) { chip.classList.remove("show"); return; }
  const r = await getJSON("/api/classify?q=" + encodeURIComponent(text));
  if (r.intent) {
    const act = r.params && r.params.action ? ` · ${r.params.action}` : "";
    chip.textContent = `→ ${r.intent}${act}`;
    chip.classList.add("show");
  } else { chip.classList.remove("show"); }
}

/* ── orchestration ──────────────────────────────────────────────── */
async function loadAll() {
  const dot = $("syncDot"); dot.classList.add("busy");
  // Render each panel independently the moment its data lands — a slow source
  // (the VPS SSH probe) must never hold back the fast local ones.
  const jobs = [
    getJSON("/api/services").then((d) => { renderCore(d); renderAgents(d); }),
    getJSON("/api/leads").then(renderLeads),
    getJSON("/api/calendar").then(renderCalendar),
    getJSON("/api/email").then(renderEmail),
    getJSON("/api/health").then(renderHealth),
    getJSON("/api/reminders").then(renderReminders),
    getJSON("/api/goals").then(renderGoals),
    getJSON("/api/nudges").then(renderNudges),
  ];
  await Promise.allSettled(jobs);
  $("lastSync").textContent = "synced " + new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false }) + " IST";
  setTimeout(() => dot.classList.remove("busy"), 500);
}

function init() {
  boot(); starfield(); clock(); setInterval(clock, 1000);
  loadAll(); setInterval(loadAll, REFRESH_MS);
  $("cmd").addEventListener("input", (e) => { clearTimeout(cmdTimer); cmdTimer = setTimeout(() => classify(e.target.value), 280); });
  $("cmdGo").addEventListener("click", () => classify($("cmd").value));
  $("cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") classify(e.target.value); });
}
document.addEventListener("DOMContentLoaded", init);
