// Ask your data: page logic and charts (vanilla JS, inline SVG, no dependencies).
// Everything that comes from the model or the database is inserted with textContent.

const NS = "http://www.w3.org/2000/svg";
const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}
function sv(tag, attrs = {}, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

// ---------------------------------------------------------------- numbers
function columnKind(name, values) {
  const nums = values.filter((v) => typeof v === "number");
  if (!nums.length) return "text";
  const n = name.toLowerCase();
  if (/(^|_)(rate|pct|percent|percentage|share|ratio|attainment|growth|yoy|churn)(_|$)/.test(n)) {
    return nums.every((v) => Math.abs(v) <= 5) ? "pct" : "pct100";
  }
  if (/(revenue|sales|spend|cost|price|aov|value|discount|fee|cac|amount|gross|margin|refund|target|net)/.test(n)) return "money";
  if (nums.every(Number.isInteger)) return "int";
  return "num";
}
const usd = (v, d) => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
function fmtFull(v, kind) {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return String(v);
  switch (kind) {
    case "money": return usd(v, 2);
    case "pct": return (v * 100).toFixed(1) + "%";
    case "pct100": return v.toFixed(1) + "%";
    case "int": return v.toLocaleString("en-US");
    default: return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
}
const trim = (s) => (s.includes(".") ? s.replace(/\.?0+$/, "") : s);
function compact(v) {
  const a = Math.abs(v);
  if (a >= 1e9) return trim((v / 1e9).toFixed(a >= 1e10 ? 0 : 1)) + "B";
  if (a >= 1e6) return trim((v / 1e6).toFixed(a >= 1e7 ? 1 : 2)) + "M";
  if (a >= 1e4) return (v / 1e3).toFixed(0) + "K";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return Number.isInteger(v) ? String(v) : v.toFixed(a < 10 ? 2 : 0);
}
function fmtCompact(v, kind) {
  if (v === null || v === undefined) return "—";
  if (kind === "money") return (v < 0 ? "-$" : "$") + compact(Math.abs(v));
  if (kind === "pct") return (v * 100).toFixed(Math.abs(v) < 0.1 ? 1 : 0) + "%";
  if (kind === "pct100") return v.toFixed(0) + "%";
  return compact(v);
}
const humanize = (c) => c.replace(/_/g, " ").replace(/^\w/, (ch) => ch.toUpperCase());
const truncate = (s, n) => (s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s);

function niceTicks(lo, hi, count = 4) {
  if (lo === hi) { hi = lo + 1; }
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const start = Math.floor(lo / step) * step;
  const ticks = [];
  for (let t = start; t <= hi + step * 1e-9; t += step) ticks.push(+t.toFixed(10));
  if (ticks[ticks.length - 1] < hi) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
  return ticks;
}

// ---------------------------------------------------------------- tooltip
function tooltip(host) {
  const tip = el("div", { class: "tip", hidden: "" });
  const strong = el("strong");
  const span = el("span");
  tip.append(strong, span);
  host.append(tip);
  return {
    show(value, label, x, y) {
      strong.textContent = value;
      span.textContent = label;
      tip.hidden = false;
      const w = tip.offsetWidth, hw = host.clientWidth;
      tip.style.left = Math.max(0, Math.min(hw - w, x + 12)) + "px";
      tip.style.top = Math.max(0, y - tip.offsetHeight - 8) + "px";
    },
    hide() { tip.hidden = true; },
  };
}
function pointerIn(host, evt) {
  const r = host.getBoundingClientRect();
  return [evt.clientX - r.left, evt.clientY - r.top];
}

// ---------------------------------------------------------------- charts
function barPath(x0, x1, yt, h) {
  const r = Math.min(4, Math.abs(x1 - x0), h / 2);
  if (x1 >= x0) {
    return `M${x0},${yt}H${x1 - r}Q${x1},${yt} ${x1},${yt + r}V${yt + h - r}Q${x1},${yt + h} ${x1 - r},${yt + h}H${x0}Z`;
  }
  return `M${x0},${yt}H${x1 + r}Q${x1},${yt} ${x1},${yt + r}V${yt + h - r}Q${x1},${yt + h} ${x1 + r},${yt + h}H${x0}Z`;
}

function barChart(host, labels, values, kind, title) {
  const W = Math.max(300, host.clientWidth || 600);
  const rowH = 32, barH = 20, pad = 4;
  const H = labels.length * rowH + pad * 2;
  const charW = 7.1;
  const labelW = Math.min(W * 0.42, Math.max(...labels.map((l) => String(l).length)) * charW + 10);
  const valueRoom = 64;
  const x0 = labelW + 8;
  const plotW = W - x0 - valueRoom;
  const nums = values.filter((v) => v !== null);
  const lo = Math.min(0, ...nums), hi = Math.max(0, ...nums);
  const sx = (v) => x0 + ((v - lo) / (hi - lo || 1)) * plotW;
  const zx = sx(0);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Bar chart: ${title}` });
  const tip = tooltip(host);
  labels.forEach((lab, i) => {
    const mid = pad + i * rowH + rowH / 2;
    const name = String(lab);
    svg.append(sv("text", { x: x0 - 8, y: mid, "text-anchor": "end", "dominant-baseline": "middle",
      fill: "var(--text-2)", "font-size": 13 }, truncate(name, Math.floor((labelW - 10) / charW))));
    const v = values[i];
    if (v === null) return;
    const xe = sx(v);
    const bar = sv("path", { d: barPath(zx, xe, mid - barH / 2, barH), fill: "var(--series)" });
    svg.append(bar);
    svg.append(sv("text", { x: v >= 0 ? xe + 6 : xe - 6, y: mid, "text-anchor": v >= 0 ? "start" : "end",
      "dominant-baseline": "middle", fill: "var(--text-2)", "font-size": 12 }, fmtCompact(v, kind)));
    const hit = sv("rect", { x: 0, y: pad + i * rowH, width: W, height: rowH, fill: "transparent",
      tabindex: 0, "aria-label": `${name}: ${fmtFull(v, kind)}` });
    const on = (evt) => {
      bar.setAttribute("opacity", "0.78");
      const [x, y] = evt && evt.clientX !== undefined ? pointerIn(host, evt) : [xe * (host.clientWidth / W), mid];
      tip.show(fmtFull(v, kind), name, x, y);
    };
    const off = () => { bar.removeAttribute("opacity"); tip.hide(); };
    hit.addEventListener("pointermove", on);
    hit.addEventListener("pointerleave", off);
    hit.addEventListener("focus", () => on());
    hit.addEventListener("blur", off);
    svg.append(hit);
  });
  svg.append(sv("line", { x1: zx, x2: zx, y1: pad, y2: H - pad, stroke: "var(--axis)", "stroke-width": 1 }));
  host.append(svg);
}

function lineChart(host, labels, values, kind, title) {
  const W = Math.max(300, host.clientWidth || 600), H = 240;
  const m = { l: 52, r: 60, t: 12, b: 28 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const nums = values.filter((v) => v !== null);
  let lo = Math.min(...nums), hi = Math.max(...nums);
  if (lo >= 0) lo = 0;
  const ticks = niceTicks(lo, hi, 4);
  lo = ticks[0]; hi = ticks[ticks.length - 1];
  const n = labels.length;
  const sx = (i) => m.l + (n === 1 ? pw / 2 : (i * pw) / (n - 1));
  const sy = (v) => m.t + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Line chart: ${title}` });
  for (const t of ticks) {
    const y = sy(t);
    svg.append(sv("line", { x1: m.l, x2: m.l + pw, y1: y, y2: y, stroke: t === lo ? "var(--axis)" : "var(--grid)", "stroke-width": 1 }));
    svg.append(sv("text", { x: m.l - 8, y, "text-anchor": "end", "dominant-baseline": "middle", fill: "var(--muted)",
      "font-size": 12, style: "font-variant-numeric: tabular-nums" }, fmtCompact(t, kind)));
  }
  const maxLabels = Math.max(2, Math.floor(pw / 70));
  const every = Math.ceil(n / maxLabels);
  labels.forEach((lab, i) => {
    if (i % every !== 0 && i !== n - 1) return;
    if (i !== n - 1 && n - 1 - i < every) return; // keep the last label clear of its neighbour
    svg.append(sv("text", { x: sx(i), y: H - 8, "text-anchor": "middle", fill: "var(--muted)", "font-size": 12 }, String(lab)));
  });
  const pts = values.map((v, i) => (v === null ? null : [sx(i), sy(v)])).filter(Boolean);
  if (pts.length > 1) {
    const linePath = pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join("");
    svg.append(sv("path", { d: `${linePath}L${pts[pts.length - 1][0]},${sy(lo)}L${pts[0][0]},${sy(lo)}Z`, fill: "var(--series-wash)" }));
    svg.append(sv("path", { d: linePath, fill: "none", stroke: "var(--series)", "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  const last = pts[pts.length - 1];
  const lastVal = values.filter((v) => v !== null).slice(-1)[0];
  svg.append(sv("circle", { cx: last[0], cy: last[1], r: 4, fill: "var(--series)", stroke: "var(--surface)", "stroke-width": 2 }));
  svg.append(sv("text", { x: last[0] + 9, y: last[1], "dominant-baseline": "middle", fill: "var(--text-2)", "font-size": 12 },
    fmtCompact(lastVal, kind)));
  // Crosshair: snaps to the nearest data point.
  const cross = sv("line", { y1: m.t, y2: m.t + ph, stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
  const dot = sv("circle", { r: 4, fill: "var(--series)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
  svg.append(cross, dot);
  const tip = tooltip(host);
  const overlay = sv("rect", { x: m.l - 8, y: m.t, width: pw + 16, height: ph, fill: "transparent" });
  overlay.addEventListener("pointermove", (evt) => {
    const [px, py] = pointerIn(host, evt);
    const scale = W / host.clientWidth;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px * scale - m.l) / (pw || 1)) * (n - 1))));
    if (values[i] === null) return;
    cross.setAttribute("x1", sx(i)); cross.setAttribute("x2", sx(i));
    dot.setAttribute("cx", sx(i)); dot.setAttribute("cy", sy(values[i]));
    cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
    tip.show(fmtFull(values[i], kind), String(labels[i]), sx(i) / scale, py);
  });
  overlay.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hide();
  });
  svg.append(overlay);
  host.append(svg);
}

// ---------------------------------------------------------------- result card
const charts = new Set();

function renderViz(card, data) {
  const { columns, rows, chart } = data;
  if (!rows.length) return;
  const numericIdx = columns.map((c, i) => i).filter((i) =>
    rows.every((r) => r[i] === null || typeof r[i] === "number") && rows.some((r) => typeof r[i] === "number"));
  if (rows.length === 1 && numericIdx.length >= 1 && numericIdx.length <= 3) {
    const stats = el("div", { class: "stats" });
    const textIdx = columns.map((c, i) => i).filter((i) => !numericIdx.includes(i));
    const eyebrow = textIdx.map((i) => rows[0][i]).filter((v) => v !== null).join(" · ");
    for (const i of numericIdx) {
      const kind = columnKind(columns[i], [rows[0][i]]);
      const s = el("div", { class: "stat" });
      s.append(el("p", { class: "label" }, (eyebrow ? eyebrow + " · " : "") + humanize(columns[i])));
      s.append(el("p", { class: "value" }, kind === "money" && Math.abs(rows[0][i]) >= 1e5 ? fmtCompact(rows[0][i], kind) : fmtFull(rows[0][i], kind)));
      if (kind === "money" && Math.abs(rows[0][i]) >= 1e5) s.append(el("p", { class: "exact" }, fmtFull(rows[0][i], kind)));
      stats.append(s);
    }
    card.append(stats);
    return "stat";
  }
  if (!chart || rows.length < 2) return;
  const xi = columns.indexOf(chart.x), yi = columns.indexOf(chart.y);
  if (xi < 0 || yi < 0 || !numericIdx.includes(yi)) return;
  const limit = chart.type === "bar" ? 20 : 200;
  const sub = rows.slice(0, limit);
  const kind = columnKind(columns[yi], rows.map((r) => r[yi]));
  const viz = el("figure", { class: "viz" });
  viz.style.margin = "16px 0 0";
  if (chart.title) viz.append(el("figcaption", { class: "viz-title" }, chart.title));
  const host = el("div", { class: "viz" });
  host.style.margin = "0";
  viz.append(host);
  card.append(viz);
  const draw = () => {
    host.replaceChildren();
    const labels = sub.map((r) => r[xi]), values = sub.map((r) => r[yi]);
    (chart.type === "line" ? lineChart : barChart)(host, labels, values, kind, chart.title || humanize(columns[yi]));
  };
  charts.add({ host, draw, width: 0 });
  requestAnimationFrame(draw);
  if (rows.length > limit) card.append(el("p", { class: "note" }, `Chart shows the first ${limit} of ${rows.length} rows; the table has them all.`));
}

function renderTable(card, data) {
  const { columns, rows } = data;
  if (!rows.length) { card.append(el("p", { class: "note" }, "The query returned no rows.")); return; }
  const kinds = columns.map((c, i) => columnKind(c, rows.map((r) => r[i])));
  const wrap = el("div", { class: "table-wrap" });
  const table = el("table");
  const thead = el("thead"), tr = el("tr");
  columns.forEach((c, i) => tr.append(el("th", { class: kinds[i] === "text" ? "" : "num", scope: "col" }, c)));
  thead.append(tr);
  const tbody = el("tbody");
  for (const r of rows.slice(0, 50)) {
    const row = el("tr");
    r.forEach((v, i) => row.append(el("td", { class: kinds[i] === "text" ? "" : "num" }, fmtFull(v, kinds[i]))));
    tbody.append(row);
  }
  table.append(thead, tbody);
  wrap.append(table);
  card.append(wrap);
  const total = data.row_count + (data.truncated ? "+" : "");
  if (rows.length > 50 || data.truncated) card.append(el("p", { class: "note" }, `Showing 50 of ${total} rows.`));
}

function renderSteps(card, trace) {
  if (!trace || !trace.length) return;
  const d = el("details");
  d.append(el("summary", {}, `How it got there (${trace.length} step${trace.length === 1 ? "" : "s"})`));
  const ol = el("ol", { class: "steps" });
  for (const t of trace) {
    const li = el("li");
    if (t.tool === "search_glossary") {
      li.textContent = `Looked up the glossary: “${t.query}”`;
    } else if (t.tool === "run_sql") {
      if (t.error) {
        li.append(el("span", { class: "err" }, `Query failed: ${t.error}`));
      } else {
        li.textContent = `Ran ${t.id}: ${t.purpose || "query"} (${t.rows}${t.truncated ? "+" : ""} rows, ${t.ms} ms)`;
      }
      const sd = el("details");
      sd.append(el("summary", {}, "SQL"), el("pre", {}, t.sql));
      li.append(sd);
    } else {
      li.textContent = t.tool;
    }
    ol.append(li);
  }
  d.append(ol);
  card.append(d);
}

function renderMeta(card, data) {
  const parts = [];
  if (data.model_label) {
    parts.push(data.escalated ? `${data.model_label} (escalated from Claude Haiku 5.5: ${data.escalation_reason})` : data.model_label);
  }
  parts.push(`${data.api_calls} API call${data.api_calls === 1 ? "" : "s"}`);
  parts.push(`${data.latency_s.toFixed(1)} s`);
  parts.push(`$${data.cost_usd.toFixed(4)}`);
  if (data.api_calls) parts.push(`${Math.round(data.cache_hit_ratio * 100)}% of input read from cache`);
  card.append(el("p", { class: "meta" }, parts.join(" · ")));
}

function renderResult(card, question, data) {
  card.replaceChildren();
  card.className = "card";
  card.append(el("p", { class: "q" }, question));
  if (data.status === "answered") {
    card.append(el("p", { class: "answer" }, data.answer));
    if (data.assumptions && data.assumptions.length) {
      const ul = el("ul", { class: "assumptions", "aria-label": "Assumptions" });
      for (const a of data.assumptions) ul.append(el("li", {}, a));
      card.append(ul);
    }
    if (renderViz(card, data) !== "stat") renderTable(card, data);
    const d = el("details");
    d.append(el("summary", {}, "SQL behind this answer"));
    const pre = el("pre", {}, data.sql);
    const copy = el("button", { class: "copy", type: "button" }, "Copy SQL");
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(data.sql); copy.textContent = "Copied"; } catch { copy.textContent = "Select and copy"; }
    });
    d.append(pre, copy);
    card.append(d);
  } else if (data.status === "clarify") {
    card.append(el("p", { class: "answer" }, data.clarify_question));
    const opts = el("div", { class: "options" });
    for (const o of data.clarify_options || []) {
      const b = el("button", { class: "chip", type: "button" }, o);
      b.addEventListener("click", () => ask(`${question} (I mean: ${o})`));
      opts.append(b);
    }
    card.append(opts);
  } else {
    card.classList.add("failed");
    card.append(el("p", { class: "answer" }, `Couldn't answer: ${data.error || "unknown error"}`));
  }
  renderSteps(card, data.trace);
  renderMeta(card, data);
}

// ---------------------------------------------------------------- flow
const results = $("#results");
const form = $("#ask");
const input = $("#q");
const modelSel = $("#model");
const go = $("#go");

async function ask(question) {
  question = question.trim();
  if (!question) return;
  const card = el("article", { class: "card pending" });
  card.append(el("p", { class: "q" }, question));
  const status = el("p", { class: "answer" }, "Working on it…");
  card.append(status);
  results.prepend(card);
  const started = performance.now();
  const timer = setInterval(() => {
    status.textContent = `Working on it… ${Math.round((performance.now() - started) / 1000)} s`;
  }, 1000);
  go.disabled = true;
  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, model: modelSel.value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderResult(card, question, data);
  } catch (err) {
    card.className = "card failed";
    status.textContent = `Couldn't answer: ${err.message}`;
  } finally {
    clearInterval(timer);
    go.disabled = false;
  }
}

form.addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});

let resizeTimer;
new ResizeObserver(() => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    for (const c of charts) {
      if (!c.host.isConnected) { charts.delete(c); continue; }
      if (Math.abs(c.host.clientWidth - c.width) > 4) { c.width = c.host.clientWidth; c.draw(); }
    }
  }, 120);
}).observe(results);

(async function init() {
  const meta = await (await fetch("/api/meta")).json();
  for (const m of meta.models) modelSel.append(el("option", { value: m.key }, m.label));
  const ex = $("#examples");
  for (const q of meta.examples) {
    const b = el("button", { class: "chip", type: "button" }, q);
    b.addEventListener("click", () => { input.value = q; ask(q); });
    ex.append(b);
  }
  $("#dataset").textContent = `Data: orders from 2024-04-01 to ${meta.data_through}. "Today" is ${meta.today}. Fiscal years run April to March (FY2026 = Apr 2025 – Mar 2026).`;
  if (!meta.key_present) {
    const banner = $("#banner");
    banner.textContent = "No API key found. Add ANTHROPIC_API_KEY to the project's .env file (or set ASKDATA_ANTHROPIC_API_KEY) and restart the server.";
    banner.hidden = false;
  }
  window.__askdata = { renderResult, ask }; // used by the screenshot script
})();
