// RegGraph dashboard page. Plain JavaScript, no libraries, no inline script (strict CSP).
// Safety rules: every value is written with textContent (never parsed as markup); the access token
// lives only in this closure and in the password field - never in storage, cookies or the address
// (a #token=... fragment is read once, then removed from the address bar). No polling: the page
// makes a request only when the person presses Load (energy-friendly, quiet on a train or plane).
"use strict";

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
};
const svg = (tag, attrs) => {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
  return node;
};
let L = {};
let CUR = "EUR";
let LOCALE = navigator.language || "en";
const label = (key, fallback) => L["dashboard." + key] || fallback || key;

function money(text) {
  // Amounts arrive as decimal strings; they are formatted for display only, never calculated on.
  try {
    return new Intl.NumberFormat(LOCALE, { style: "currency", currency: CUR, notation: "compact",
      maximumFractionDigits: 1 }).format(text);
  } catch (e) { return String(text); }
}
const num = (v) => new Intl.NumberFormat(LOCALE).format(v);
const entries = (o) => Object.entries(o || {}).sort((a, b) => b[1] - a[1]);

function kpi(cls, value, title) {
  const box = el("div", null, "kpi " + cls);
  box.append(el("div", value, "v"), el("div", title, "t"));
  return box;
}

function donut(pairs, centre) {
  const wrap = el("div", null, "chart");
  const total = pairs.reduce((s, p) => s + p[1], 0) || 1;
  const g = svg("svg", { viewBox: "0 0 120 120", class: "donut", role: "img",
    "aria-label": pairs.map((p) => p[0] + " " + p[1]).join(", ") });
  const R = 46, C = 2 * Math.PI * R;
  g.append(svg("circle", { cx: 60, cy: 60, r: R, "stroke-width": 16, class: "track" }));
  let offset = 0;
  pairs.forEach((p, i) => {
    const len = (p[1] / total) * C;
    g.append(svg("circle", { cx: 60, cy: 60, r: R, "stroke-width": 16, class: "seg s" + ((i % 6) + 1),
      "stroke-dasharray": len.toFixed(2) + " " + (C - len).toFixed(2),
      "stroke-dashoffset": (-offset).toFixed(2), transform: "rotate(-90 60 60)" }));
    offset += len;
  });
  const big = svg("text", { x: 60, y: 62, "font-size": 20, "font-weight": 800 });
  big.textContent = String(centre);
  g.append(big);
  const list = el("ul", null, "legend");
  pairs.forEach((p, i) => {
    const li = el("li");
    li.append(el("span", null, "sw s" + ((i % 6) + 1)), el("span", p[0]), el("span", num(p[1]), "n"));
    list.append(li);
  });
  wrap.append(g, list);
  return wrap;
}

function bars(rows) {
  // rows: [{name, text, ratio 0..1}] -> horizontal bars, width set through the CSSOM (CSP-safe).
  const ul = el("ul", null, "bars");
  rows.forEach((r, i) => {
    const li = el("li", null, "bar");
    const row = el("div", null, "row");
    row.append(el("span", r.name, "lbl"), el("span", r.text, "val"));
    const track = el("div", null, "track");
    const fill = el("div", null, "fill s" + ((i % 6) + 1));
    track.append(fill);
    li.append(row, track);
    ul.append(li);
    requestAnimationFrame(() => { fill.style.width = Math.max(2, r.ratio * 100).toFixed(1) + "%"; });
  });
  return ul;
}

function table(cols, rows) {
  const wrap = el("div", null, "scroll");
  const t = el("table");
  const head = el("tr");
  cols.forEach((c) => head.append(el("th", c.title, c.num ? "n" : "")));
  t.append(head);
  rows.forEach((r) => {
    const tr = el("tr");
    cols.forEach((c) => {
      const td = el("td", null, c.num ? "n" : "");
      const v = c.cell(r);
      if (v instanceof Node) td.append(v); else td.textContent = String(v);
      tr.append(td);
    });
    t.append(tr);
  });
  wrap.append(t);
  return wrap;
}

const chip = (text, kind) => el("span", text, "chip chip-" + kind);
const section = (title) => { const s = el("section"); s.append(el("h2", title)); return s; };
const failed = (s, v) => { if (v && v.error) { s.append(el("p", v.error, "err")); return true; } return false; };

function registry(v) {
  const s = section(label("registry", "Registry"));
  if (failed(s, v)) return s;
  s.append(donut(entries(v.by_type), v.entities));
  s.append(el("h3", "Jurisdictions"));
  const j = entries(v.by_jurisdiction);
  const top = j.length ? j[0][1] : 1;
  s.append(bars(j.slice(0, 6).map((p) => ({ name: p[0], text: num(p[1]), ratio: p[1] / top }))));
  return s;
}

function exposure(v) {
  const s = section(label("exposure", "Exposure"));
  if (failed(s, v)) return s;
  const rows = v.top_counterparties || [];
  if (!rows.length) { s.append(el("p", "-", "empty")); return s; }
  const top = Math.max(...rows.map((r) => Number(r.amount))) || 1;
  s.append(bars(rows.map((r) => ({ name: r.name || r.lei, ratio: Number(r.amount) / top,
    text: money(r.amount) + " | " + r.share_percent + "%" }))));
  return s;
}

function insurance(v) {
  const s = section(label("insurance", "Insurance"));
  if (failed(s, v)) return s;
  s.append(donut(entries(v.by_line).map((p) => [p[0].replaceAll("_", " "), p[1]]), v.contracts));
  return s;
}

function stress(v) {
  const s = section(label("stress", "Stress"));
  if (failed(s, v)) return s;
  const runs = v.recent_runs || [];
  if (!runs.length) { s.append(el("p", "-", "empty")); return s; }
  s.append(table([
    { title: "Scenario", cell: (r) => r.scenario_key },
    { title: "Gross", num: true, cell: (r) => money(r.gross_loss) },
    { title: "Net", num: true, cell: (r) => money(r.net_loss) },
    { title: "", cell: (r) => (r.breaches > 0 ? chip(r.breaches + " breach", "bad") : chip("OK", "ok")) },
  ], runs));
  return s;
}

function rules(v) {
  const s = section(label("rules", "Rules"));
  if (failed(s, v)) return s;
  const by = v.versions_by_status || {};
  const total = Object.values(by).reduce((a, b) => a + b, 0) || 1;
  const stack = el("div", null, "stack");
  Object.entries(by).forEach(([k, n], i) => {
    const part = el("span", null, "s" + ((i % 6) + 1));
    part.style.width = ((n / total) * 100).toFixed(1) + "%";
    part.title = k + ": " + n;
    stack.append(part);
  });
  s.append(stack);
  const chips = el("div", null, "chips");
  Object.entries(by).forEach(([k, n]) => chips.append(chip(k + " " + n, k === "approved" ? "ok" : "info")));
  chips.append(chip("review " + (v.open_review_tasks || 0), (v.open_review_tasks || 0) ? "warn" : "ok"));
  s.append(chips);
  return s;
}

function rates(v) {
  const s = section(label("rates", "Rates"));
  if (failed(s, v)) return s;
  const chips = el("div", null, "chips");
  chips.append(chip(num(v.currencies || 0) + " currencies", "info"), chip(v.latest_date || "-", "ok"));
  s.append(chips);
  return s;
}

function connectors(v) {
  const s = section(label("connectors", "Connectors"));
  if (failed(s, v)) return s;
  const chips = el("div", null, "chips");
  Object.entries(v.last_runs || {}).forEach(([name, run]) => {
    const status = run && typeof run === "object" ? (run.status || "run") : "never run";
    chips.append(chip(name + ": " + status, status === "ok" ? "ok" : status === "never run" ? "info" : "warn"));
  });
  chips.append(chip("alerts " + (v.open_alerts || 0), (v.open_alerts || 0) ? "warn" : "ok"));
  s.append(chips);
  return s;
}

const RENDER = { registry, exposure, insurance, stress, rules, rates, connectors };

function render(data) {
  L = data.labels || {};
  CUR = data.currency;
  $("title").textContent = label("title", "RegGraph Europe");
  $("l-token").textContent = label("token", "Access token");
  $("load").textContent = label("load", "Load");
  $("offline").hidden = !data.offline;
  $("notice").textContent = L["notice.decision_support"] || "";
  const board = $("board");
  board.replaceChildren();
  const sec = data.sections;
  const kp = el("div", null, "kpis");
  if (sec.registry && !sec.registry.error) kp.append(kpi("k1", num(sec.registry.entities), label("registry", "Registry")));
  if (sec.exposure && !sec.exposure.error) kp.append(kpi("k2", money(sec.exposure.total), label("exposure", "Exposure")));
  if (sec.insurance && !sec.insurance.error) kp.append(kpi("k3", num(sec.insurance.contracts), label("insurance", "Insurance")));
  if (sec.stress && !sec.stress.error) kp.append(kpi("k4", num(sec.stress.runs), label("stress", "Stress")));
  if (sec.rules && !sec.rules.error) kp.append(kpi("k5", num((sec.rules.versions_by_status || {}).approved || 0), label("rules", "Rules")));
  if (sec.rates && !sec.rates.error) kp.append(kpi("k6", num(sec.rates.currencies), label("rates", "Rates")));
  if (kp.children.length) board.append(kp);
  for (const [name, value] of Object.entries(sec)) {
    board.append((RENDER[name] || (() => section(name)))(value));
  }
  for (const name of data.omitted) {
    const card = section(label(name, name));
    card.className = "omitted";
    card.append(el("p", label("unavailable", "Not available for your role."), "empty"));
    board.append(card);
  }
  $("status").textContent = [data.view, data.as_of, data.currency].join(" | ");
}

async function load(event) {
  if (event) event.preventDefault();
  const params = new URLSearchParams();
  const currency = $("currency").value.trim().toUpperCase();
  if (currency) params.set("currency", currency);
  if ($("asof").value) params.set("as_of", $("asof").value);
  $("status").textContent = "...";
  try {
    const response = await fetch("/v1/dashboard?" + params.toString(), {
      headers: { Authorization: "Bearer " + $("token").value.trim() },
      cache: "no-store",
      credentials: "omit",
    });
    const body = await response.json();
    if (!response.ok) { $("status").textContent = body.message || String(response.status); return; }
    render(body);
  } catch (err) {
    $("status").textContent = "Network error";
  }
}

$("controls").addEventListener("submit", load);

// A link printed by `reggraph demo` carries the token after '#'. Browsers never send a fragment to
// the server; we read it once, put it in the (password) field and wipe it from the address bar.
(function takeFragmentToken() {
  const m = /^#token=([A-Za-z0-9._~-]{1,4096})$/.exec(location.hash);
  if (!m) return;
  $("token").value = m[1];
  history.replaceState(null, "", location.pathname + location.search);
  load(null);
})();
