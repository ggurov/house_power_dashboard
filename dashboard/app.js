/* House power dashboard: SSE live feed + history API, dependency-free canvas charts. */
"use strict";

const $ = (id) => document.getElementById(id);
const liveBuf = [];          // {t, l1, l2, tot} seconds, max 10 min
const LIVE_WINDOW = 600;

function fmt(n, d = 0) {
  return n == null || isNaN(n) ? "\u2014" : Number(n).toLocaleString(undefined,
    { maximumFractionDigits: d, minimumFractionDigits: d });
}

function collect(series, pick) {
  // Same as series.flatMap((s) => s.points.map(pick)) but ES6-only:
  // pre-2019 browsers (old wall tablets) have no Array.flatMap, which
  // silently killed every chart while fetches kept succeeding.
  const out = [];
  for (const s of series) for (const p of s.points) out.push(pick(p));
  return out;
}

function drawChart(canvas, series, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight || canvas.height;
  if (!w) return;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const all = collect(series, (p) => p.y);
  if (!all.length) {
    ctx.fillStyle = "#8b98a5"; ctx.font = "13px system-ui";
    ctx.fillText("No data yet \u2014 waiting for sampler\u2026", 12, h / 2);
    return;
  }
  let max = Math.max(...all, 1) * 1.1, min = 0;
  const t0 = Math.min(...collect(series, (p) => p.t));
  const t1 = Math.max(...collect(series, (p) => p.t), t0 + 1);
  const X = (t) => 8 + ((t - t0) / (t1 - t0)) * (w - 16);
  const Y = (v) => h - 22 - ((v - min) / (max - min)) * (h - 34);
  // gridlines + max label
  ctx.strokeStyle = "#26303b"; ctx.fillStyle = "#8b98a5"; ctx.font = "11px system-ui";
  for (let i = 0; i <= 3; i++) {
    const v = min + ((max - min) * i) / 3, y = Y(v);
    ctx.beginPath(); ctx.moveTo(8, y); ctx.lineTo(w - 8, y); ctx.stroke();
    ctx.fillText(fmt(v) + " W", 10, y - 3);
  }
  // time labels
  const f = (t) => new Date(t * 1000).toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  ctx.fillText(f(t0), 8, h - 6);
  const end = f(t1); ctx.fillText(end, w - 8 - ctx.measureText(end).width, h - 6);
  for (const s of series) {
    ctx.strokeStyle = s.color; ctx.lineWidth = 1.5; ctx.beginPath();
    s.points.forEach((p, i) => (i ? ctx.lineTo(X(p.t), Y(p.y)) : ctx.moveTo(X(p.t), Y(p.y))));
    ctx.stroke();
  }
  // legend
  let lx = w - 8;
  for (const s of [...series].reverse()) {
    const label = `${s.name} ${fmt(s.points.length ? s.points[s.points.length - 1].y : NaN)} W`;
    lx -= ctx.measureText(label).width + 18;
    ctx.fillStyle = s.color; ctx.fillRect(lx, 8, 10, 10);
    ctx.fillStyle = "#e8eef4"; ctx.fillText(label, lx + 14, 17);
  }
}

function renderLive() {
  drawChart($("liveChart"), [
    { name: "L1", color: "#ffb454", points: liveBuf.map((p) => ({ t: p.t, y: p.l1 })) },
    { name: "L2", color: "#5ac8fa", points: liveBuf.map((p) => ({ t: p.t, y: p.l2 })) },
    { name: "Total", color: "#7ee787", points: liveBuf.map((p) => ({ t: p.t, y: p.tot })) },
  ]);
}

let lastTs = 0;
let maxBufTs = 0;   // newest ts in liveBuf (drives the 10-min eviction window)
const bufTs = new Set();  // every ts in liveBuf: dedupes SSE stream vs preload
function pushLive(t, l1, l2, tot) {  // false = duplicate, ignored
  if (bufTs.has(t)) return false;
  bufTs.add(t);
  liveBuf.push({ t, l1, l2, tot });
  if (t > maxBufTs) maxBufTs = t;
  return true;
}
function evictLive() {
  const cutoff = maxBufTs - LIVE_WINDOW;
  while (liveBuf.length && liveBuf[0].t < cutoff) {
    bufTs.delete(liveBuf[0].t);
    liveBuf.shift();
  }
}
function onReading(m) {
  lastTs = m.ts;
  $("totalW").textContent = fmt(m.total_w);
  $("leg1W").textContent = fmt(m.leg1_w); $("leg1A").textContent = fmt(m.leg1_a, 2);
  $("leg2W").textContent = fmt(m.leg2_w); $("leg2A").textContent = fmt(m.leg2_a, 2);
  $("rangeSetting").textContent = m.range_setting;
  $("schemaV").textContent = m.v != null ? m.v : "1";
  if (pushLive(m.ts, m.leg1_w, m.leg2_w, m.total_w)) {
    evictLive();
    renderLive();
  }
}

// Fill the live chart from storage so a reload doesn't wipe it.
// Merge (don't replace): the SSE stream usually delivers the newest point
// BEFORE the preload query returns, so a naive "skip older than newest"
// check would discard the entire preload. A ts Set dedupes either order.
async function preloadLive() {
  const now = Date.now() / 1000;
  try {
    const r = await fetch(`/api/v1/history?start=${now - LIVE_WINDOW}&end=${now}`);
    if (!r.ok) return;
    const h = await r.json();
    let added = 0;
    for (const p of h.points) {
      if (pushLive(p.t, p.leg1_w, p.leg2_w, p.total_w)) added++;
    }
    if (added) {
      liveBuf.sort((a, b) => a.t - b.t);
      evictLive();
      renderLive();
    }
  } catch (_) { /* storage unavailable: SSE fills the chart from scratch */ }
}

function tickAge() {
  if (!lastTs) return;
  const age = Date.now() / 1000 - lastTs;
  $("age").textContent = age < 5 ? "live" : `${fmt(age)}s ago`;
}

function connectSSE() {
  const dot = $("connDot"), txt = $("connText");
  const es = new EventSource("/api/v1/stream");
  es.onopen = () => { dot.className = "conn live"; txt.textContent = "live"; };
  es.onerror = () => { dot.className = "conn dead"; txt.textContent = "reconnecting\u2026"; };
  es.onmessage = (e) => { try { onReading(JSON.parse(e.data)); } catch (_) { /* keep old */ } };
}

let currentRange = 86400;
async function loadHistory(seconds) {
  currentRange = seconds;
  const now = Date.now() / 1000;
  const start = seconds === "all" ? 0 : now - seconds;
  const meta = $("histMeta");
  meta.textContent = "loading\u2026";
  // One bucket per chart pixel: the server averages, we transfer ~1k
  // points instead of ~20k. Zero visual loss on a line chart.
  const px = Math.min(4000, Math.max(50,
    Math.round($("histChart").clientWidth) || 800));
  try {
    const r = await fetch(`/api/v1/history?start=${start}&end=${now}&pixels=${px}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const h = await r.json();
    drawChart($("histChart"), [
      { name: "L1", color: "#ffb454", points: h.points.map((p) => ({ t: p.t, y: p.leg1_w })) },
      { name: "L2", color: "#5ac8fa", points: h.points.map((p) => ({ t: p.t, y: p.leg2_w })) },
      { name: "Total", color: "#7ee787", points: h.points.map((p) => ({ t: p.t, y: p.total_w })) },
    ]);
    meta.textContent = `${h.points.length} points \u00b7 ${h.step_s}s buckets \u00b7 ${h.resolution} \u00b7 ${h.volts} V assumed/leg`;
  } catch (e) {
    meta.textContent = `history unavailable (${e.message})`;
  }
}

$("ranges").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  document.querySelectorAll("#ranges button").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  loadHistory(b.dataset.range === "all" ? "all" : Number(b.dataset.range));
});

let resizeTimer = null;
window.addEventListener("resize", () => {
  renderLive();
  // Chart width changed -> rebucket from the server (debounced).
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => loadHistory(currentRange), 400);
});
fetch("/api/v1/current").then((r) => r.json()).then(onReading).catch(() => {});
preloadLive();
connectSSE();
loadHistory(86400);
setInterval(tickAge, 1000);
