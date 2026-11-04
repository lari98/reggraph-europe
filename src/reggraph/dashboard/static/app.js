// RegGraph console. Plain JavaScript, no libraries, no inline script (strict CSP).
// Safety rules: every value is written with textContent / text nodes (never parsed as markup); the
// access token lives only in this closure (never in storage, cookies or the address bar - a
// #token=... fragment is read once and removed); no timers or polling, so an idle tab costs nothing
// (good for battery on a train or plane); every request goes to this same server only.
"use strict";

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const S = { token: "", ccy: "EUR", asof: "", cache: new Map(), ents: null, labels: {}, offline: false,
  dash: null, nav: 0, locale: navigator.language || "en", ccyOk: true };

// ---------------------------------------------------------------- element helpers
function h(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = String(v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
function s(tag, attrs, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
  for (const kid of kids.flat(Infinity)) if (kid) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return n;
}

// ---------------------------------------------------------------- formatting
function money(v, full) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  try {
    return new Intl.NumberFormat(S.locale, { style: "currency", currency: S.ccy, notation: full ? "standard" : "compact",
      maximumFractionDigits: full ? 2 : 1 }).format(n);
  } catch (e) { return String(v); }
}
const num = (v) => new Intl.NumberFormat(S.locale).format(Number(v) || 0);
const pct = (share) => (Number(share) * 100).toFixed(1) + "%";
const dateOf = (iso) => (iso ? String(iso).slice(0, 10) : "-");
const label = (key, fallback) => S.labels["dashboard." + key] || fallback;
const words = (t) => String(t).replaceAll("_", " ");
const enc = encodeURIComponent;
const typeClass = (t) => "nt-" + (["bank", "insurer", "reinsurer", "corporate", "holding"].includes(t) ? t : "other");
const typeIdx = { bank: 1, insurer: 2, reinsurer: 3, corporate: 4, holding: 5 };

// ---------------------------------------------------------------- api
async function api(path, opts) {
  const o = opts || {};
  const method = o.method || "GET";
  if (method === "GET" && S.cache.has(path)) return S.cache.get(path);
  const res = await fetch(path, { method, headers: { Authorization: "Bearer " + S.token, Accept: "application/json" },
    cache: "no-store", credentials: "omit" });
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (!res.ok) {
    const err = new Error((body && body.message) || "HTTP " + res.status);
    err.status = res.status;
    throw err;
  }
  if (method === "GET") S.cache.set(path, body);
  return body;
}
const qs = () => "currency=" + S.ccy + (S.asof ? "&on=" + S.asof : "");

async function entities() {
  if (S.ents) return S.ents;
  let all = [], after = "";
  for (let i = 0; i < 10; i += 1) {
    const page = await api("/v1/entities?limit=200" + (after ? "&after=" + after : ""));
    all = all.concat(page);
    if (page.length < 200) break;
    after = page[page.length - 1].lei;
  }
  S.ents = new Map(all.map((v) => [v.lei, v.entity]));
  return S.ents;
}
const nm = (lei) => (S.ents && S.ents.get(lei) ? S.ents.get(lei).legal_name : lei);
const typeOf = (lei) => (S.ents && S.ents.get(lei) ? S.ents.get(lei).entity_type : "other");
const entLink = (lei, text) => h("a", { href: "#/entity/" + enc(lei), text: text || nm(lei) });

// ---------------------------------------------------------------- small components
const chip = (text, tone) => h("span", { class: "chip chip-" + (tone || "neutral"), text });
const skel = () => h("div", null, h("div", { class: "skel" }), h("div", { class: "skel" }), h("div", { class: "skel" }));
function toast(text, bad) {
  const t = h("div", { class: "toast" + (bad ? " bad" : ""), text });
  $("toasts").append(t);
  setTimeout(() => t.remove(), 4500);
}
function errMsg(e) {
  if (e.status === 403) return h("p", { class: "lock", text: label("unavailable", "Not available for your role.") });
  if (e.status === 401) return h("p", { class: "err", text: "Your session expired. Sign in again." });
  return h("p", { class: "err", text: e.message || "Something went wrong." });
}
function lazy(title, loader, build, o) {
  const opt = o || {};
  const body = h("div", { class: "card-body" }, skel());
  const card = h("section", { class: "card " + (opt.class || "") }, h("div", { class: "card-h" }, h("h2", { text: title }), opt.actions || null), body);
  loader().then((d) => body.replaceChildren(build(d))).catch((e) => body.replaceChildren(errMsg(e)));
  return card;
}
const card = (title, content, cls, actions) => h("section", { class: "card " + (cls || "") },
  h("div", { class: "card-h" }, h("h2", { text: title }), actions || null), h("div", { class: "card-body" }, content));
function kpi(cls, value, title, sub, href) {
  const inner = [h("div", { class: "t", text: title }), h("div", { class: "v", text: value }), sub ? h("div", { class: "s", text: sub }) : null];
  return href ? h("a", { class: "kpi " + cls, href }, inner) : h("div", { class: "kpi " + cls }, inner);
}
function kv(pairs) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of pairs) dl.append(h("dt", { text: k }), h("dd", null, v === null || v === undefined || v === "" ? "-" : v));
  return dl;
}
function pageHead(title, crumbs, right) {
  return h("div", { class: "page-h" }, h("div", null, crumbs ? h("div", { class: "crumbs" }, crumbs) : null, h("h1", { text: title })), right || null);
}
function donut(pairs, centre, hrefOf) {
  const total = pairs.reduce((a, p) => a + p[1], 0) || 1;
  const R = 46, C = 2 * Math.PI * R;
  const g = s("svg", { viewBox: "0 0 120 120", class: "donut", role: "img", "aria-label": pairs.map((p) => p[0] + " " + p[1]).join(", ") });
  g.append(s("circle", { cx: 60, cy: 60, r: R, "stroke-width": 16, class: "track" }));
  let off = 0;
  pairs.forEach((p, i) => {
    const len = (p[1] / total) * C;
    g.append(s("circle", { cx: 60, cy: 60, r: R, "stroke-width": 16, class: "seg s" + ((i % 6) + 1),
      "stroke-dasharray": len.toFixed(2) + " " + (C - len).toFixed(2), "stroke-dashoffset": (-off).toFixed(2), transform: "rotate(-90 60 60)" }));
    off += len;
  });
  g.append(s("text", { x: 60, y: 66 }, String(centre)));
  const ul = h("ul", { class: "legend" });
  pairs.forEach((p, i) => {
    const inner = [h("span", { class: "sw s" + ((i % 6) + 1) }), h("span", { text: words(p[0]) }), h("span", { class: "n", text: num(p[1]) })];
    ul.append(h("li", null, hrefOf ? h("a", { href: hrefOf(p[0]) }, inner) : h("div", null, inner)));
  });
  return h("div", { class: "chart" }, g, ul);
}
function bars(rows) {
  const ul = h("ul", { class: "bars" });
  rows.forEach((r, i) => {
    const fill = h("div", { class: "fill s" + (((r.color === undefined ? i : r.color) % 6) + 1) });
    const inner = [h("div", { class: "row" }, h("span", { class: "lbl", text: r.name }), h("span", { class: "val", text: r.text })), h("div", { class: "track" }, fill)];
    ul.append(h("li", { class: "bar-i" }, r.href ? h("a", { href: r.href, title: r.title || r.name }, inner) : h("div", { title: r.title || r.name }, inner)));
    requestAnimationFrame(() => { fill.style.width = Math.max(2, Math.min(100, r.ratio * 100)).toFixed(1) + "%"; });
  });
  return ul;
}

// ---------------------------------------------------------------- data table: sort, search, filter, pages, CSV
function csvCell(v) {
  let t = v === null || v === undefined ? "" : String(v);
  if (/^[=+@]/.test(t) || (/^-/.test(t) && Number.isNaN(Number(t)))) t = "'" + t;  // no spreadsheet formulas
  return '"' + t.replaceAll('"', '""') + '"';
}
function table(o) {
  const st = { sort: -1, dir: 1, page: 0, q: "", f: {} };
  const size = o.pageSize || 12;
  const cols = o.cols;
  const text = (c, r) => String(c.text ? c.text(r) : c.get(r) ?? "");
  const wrap = h("div", { class: "tbl" });
  const tools = h("div", { class: "tools" });
  const body = h("tbody");
  const pager = h("div", { class: "pager" });
  const head = h("tr");
  const tableEl = h("table", null, h("thead", null, head), body);
  const filterSel = [];
  if (o.search !== false) tools.append(h("input", { type: "search", placeholder: "Filter this table", "aria-label": "Filter this table",
    oninput: (e) => { st.q = e.target.value.toLowerCase(); st.page = 0; draw(); } }));
  for (const f of o.filters || []) {
    const opts = [...new Set(o.rows.map((r) => String(f.get(r))))].sort();
    st.f[f.key] = (o.init && o.init[f.key]) || "";
    const sel = h("select", { "aria-label": f.label, onchange: (e) => { st.f[f.key] = e.target.value; st.page = 0; draw(); } },
      h("option", { value: "", text: f.label + ": all" }), opts.map((v) => h("option", { value: v, text: words(v), selected: v === st.f[f.key] })));
    filterSel.push(sel);
    tools.append(sel);
  }
  if (o.csv) tools.append(h("button", { type: "button", class: "btn", text: "Export CSV", onclick: () => {
    const rows = view(true);
    const lines = [cols.map((c) => csvCell(c.title)).join(",")].concat(rows.map((r) => cols.map((c) => csvCell(text(c, r))).join(",")));
    const url = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8" }));
    const a = h("a", { href: url, download: o.csv + ".csv" });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } }));
  cols.forEach((c, i) => {
    const th = h("th", { class: c.num ? "num" : "", scope: "col", "aria-sort": "none" });
    th.append(h("button", { type: "button", onclick: () => { st.dir = st.sort === i ? -st.dir : 1; st.sort = i; draw(); } }, c.title));
    head.append(th);
  });
  function view(all) {
    let rows = o.rows.filter((r) => (!st.q || cols.some((c) => text(c, r).toLowerCase().includes(st.q)))
      && (o.filters || []).every((f) => !st.f[f.key] || String(f.get(r)) === st.f[f.key]));
    if (st.sort >= 0) {
      const c = cols[st.sort];
      const val = (r) => (c.sort ? c.sort(r) : c.get(r));
      rows = rows.slice().sort((a, b) => {
        const x = val(a), y = val(b);
        const d = c.num ? Number(x) - Number(y) : String(x ?? "").localeCompare(String(y ?? ""), S.locale);
        return d * st.dir;
      });
    }
    return all ? rows : rows;
  }
  function draw() {
    const rows = view(false);
    const pages = Math.max(1, Math.ceil(rows.length / size));
    st.page = Math.min(st.page, pages - 1);
    [...head.children].forEach((th, i) => th.setAttribute("aria-sort", st.sort === i ? (st.dir > 0 ? "ascending" : "descending") : "none"));
    body.replaceChildren();
    for (const r of rows.slice(st.page * size, st.page * size + size)) {
      const tr = h("tr");
      cols.forEach((c, i) => {
        const v = c.render ? c.render(r) : (c.get(r) ?? "-");
        tr.append(h("td", { class: c.num ? "num" : "" }, i === 0 && o.href ? h("a", { href: o.href(r) }, v) : v));
      });
      if (o.href) {
        tr.className = "link";
        tr.addEventListener("click", (e) => { if (!e.target.closest("a,button")) location.hash = o.href(r); });
      } else if (o.open) {
        tr.className = "link"; tr.tabIndex = 0;
        tr.addEventListener("click", (e) => { if (!e.target.closest("a,button")) o.open(r); });
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") o.open(r); });
      }
      body.append(tr);
    }
    if (!rows.length) body.append(h("tr", null, h("td", { colspan: cols.length, class: "empty", text: o.empty || "Nothing to show." })));
    pager.replaceChildren(h("span", { text: rows.length ? (st.page * size + 1) + "-" + Math.min(rows.length, (st.page + 1) * size) + " of " + rows.length : "0 rows" }),
      h("button", { type: "button", class: "btn", text: "Prev", disabled: st.page === 0, onclick: () => { st.page -= 1; draw(); } }),
      h("button", { type: "button", class: "btn", text: "Next", disabled: st.page >= pages - 1, onclick: () => { st.page += 1; draw(); } }));
  }
  wrap.append(tools, h("div", { class: "scroll" }, tableEl), pager);
  draw();
  return wrap;
}

// ---------------------------------------------------------------- drawer (detail panel, drill-down)
let lastFocus = null;
function closeDrawer() {
  $("drawer-root").replaceChildren();
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
}
function drawer(title, content) {
  lastFocus = document.activeElement;
  const close = h("button", { type: "button", class: "btn", text: "Close", onclick: closeDrawer });
  const bodyEl = h("div", { class: "drawer-b" }, skel());
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": title,
    onkeydown: (e) => {
      if (e.key === "Escape") { closeDrawer(); return; }
      if (e.key !== "Tab") return;
      const f = [...panel.querySelectorAll("a[href],button:not([disabled]),input,select")];
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    } }, h("div", { class: "drawer-h" }, h("h2", { text: title }), close), bodyEl);
  $("drawer-root").replaceChildren(h("div", { class: "scrim", onclick: closeDrawer }), panel);
  close.focus();
  Promise.resolve(content).then((n) => bodyEl.replaceChildren(n)).catch((e) => bodyEl.replaceChildren(errMsg(e)));
}

// ---------------------------------------------------------------- network graph (SVG)
function network(data, o) {
  const opt = o || {};
  const W = 760, H = 520, cx = W / 2, cy = H / 2;
  const ids = Object.keys(data.nodes);
  const pos = new Map();
  if (opt.circle) {
    ids.sort((a, b) => nm(a).localeCompare(nm(b)));
    ids.forEach((id, i) => { const a = (i / ids.length) * 2 * Math.PI - Math.PI / 2; pos.set(id, [cx + 210 * Math.cos(a), cy + 200 * Math.sin(a)]); });
  } else {
    const rings = {};
    for (const id of ids) (rings[data.nodes[id]] = rings[data.nodes[id]] || []).push(id);
    for (const [d, list] of Object.entries(rings)) {
      list.sort((a, b) => nm(a).localeCompare(nm(b)));
      if (Number(d) === 0) { list.forEach((id) => pos.set(id, [cx, cy])); continue; }
      const rx = 130 + (Number(d) - 1) * 105, ry = 105 + (Number(d) - 1) * 85;
      list.forEach((id, i) => { const a = (i / list.length) * 2 * Math.PI - Math.PI / 2 + (Number(d) % 2) * 0.3; pos.set(id, [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]); });
    }
  }
  const maxAmt = Math.max(1, ...data.edges.map((e) => Number(e.amount || e.total || 0)));
  const svg = s("svg", { viewBox: "0 0 " + W + " " + H, class: "net", role: "group", "aria-label": "Relationship network. Each node is a link to that entity." });
  const defs = s("defs");
  svg.append(defs);
  data.edges.forEach((e) => {
    const a = pos.get(e.from_lei || e.holder_lei), b = pos.get(e.to_lei || e.counterparty_lei);
    if (!a || !b) return;
    const kind = e.kind || "exposure";
    const amt = Number(e.amount || e.total || 0);
    const w = kind === "ownership" ? 2 : 1 + 4 * Math.sqrt(amt / maxAmt);
    const mx = (a[0] + b[0]) / 2 + (b[1] - a[1]) * 0.12, my = (a[1] + b[1]) / 2 - (b[0] - a[0]) * 0.12;
    const p = s("path", { d: "M" + a[0].toFixed(1) + " " + a[1].toFixed(1) + " Q" + mx.toFixed(1) + " " + my.toFixed(1) + " " + b[0].toFixed(1) + " " + b[1].toFixed(1),
      class: "edge e-" + kind, "stroke-width": w.toFixed(1) });
    p.append(s("title", null, kind + ": " + nm(e.from_lei || e.holder_lei) + " -> " + nm(e.to_lei || e.counterparty_lei) + (amt ? " " + money(amt) : (e.percent ? " " + e.percent + "%" : ""))));
    svg.append(p);
  });
  ids.forEach((id) => {
    const [x, y] = pos.get(id);
    const d = data.nodes[id];
    const r = opt.circle ? 11 : d === 0 ? 22 : d === 1 ? 15 : 11;
    const name = nm(id).replace(" (demo)", "");
    svg.append(s("a", { href: "#/entity/" + enc(id) },
      s("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r, class: "node " + typeClass(typeOf(id)) }, s("title", null, nm(id) + " (" + words(typeOf(id)) + ")")),
      s("text", { x: x.toFixed(1), y: (y + r + 13).toFixed(1) }, name.length > 22 ? name.slice(0, 21) + "…" : name)));
  });
  const legend = h("div", { class: "net-legend" });
  for (const k of ["exposure", "ownership", "insurance"]) {
    if (!data.edges.some((e) => (e.kind || "exposure") === k)) continue;
    legend.append(h("label", null, h("input", { type: "checkbox", checked: true, onchange: (e) => svg.classList.toggle("hide-" + k, !e.target.checked) }), k));
  }
  for (const t of ["bank", "insurer", "reinsurer", "corporate", "holding"]) {
    if (ids.some((id) => typeOf(id) === t)) legend.append(h("span", null, h("span", { class: "sw s" + typeIdx[t] }), t));
  }
  return h("div", null, svg, legend);
}

// ---------------------------------------------------------------- shared drill-down panels
const moneyIn = (v, ccy) => {
  try { return new Intl.NumberFormat(S.locale, { style: "currency", currency: ccy, notation: "compact", maximumFractionDigits: 1 }).format(Number(v)); }
  catch (e) { return String(v) + " " + ccy; }
};
const short = (hash) => h("span", { class: "mono", title: hash, text: String(hash).slice(0, 12) });
const shockText = (sh) => sh.kind + ": " + Object.entries(sh).filter(([k]) => k !== "kind")
  .map(([k, v]) => k + "=" + (Array.isArray(v) ? v.map(nm).join(", ") : v)).join("; ");

async function contractPanel(ref) {
  await entities().catch(() => null);
  const [cur, hist] = await Promise.all([api("/v1/contracts/" + enc(ref)), api("/v1/contracts/" + enc(ref) + "/history")]);
  const c = cur.contract;
  return h("div", { class: "drawer-b-inner" },
    kv([["Reference", c.contract_ref], ["Line", chip(words(c.line), "info")], ["Insurer", entLink(c.insurer_lei)], ["Insured", entLink(c.insured_lei)],
      ["Sum insured", moneyIn(c.sum_insured, c.currency) + " (" + num(c.sum_insured) + " " + c.currency + ")"], ["Start", c.start_date], ["End", c.end_date || "open"],
      ["Reinsures", c.reinsures_ref ? h("a", { href: "#", text: c.reinsures_ref, onclick: (e) => { e.preventDefault(); drawer("Contract " + c.reinsures_ref, contractPanel(c.reinsures_ref)); } }) : "-"],
      ["Version", cur.version_no], ["Content hash", short(cur.content_hash)]]),
    h("h3", { text: "Version history" }),
    table({ cols: [{ title: "Version", num: true, get: (r) => r.version_no }, { title: "Changed", get: (r) => dateOf(r.changed_at) },
      { title: "By", get: (r) => r.changed_by }, { title: "Summary", get: (r) => r.change_summary }, { title: "Hash", get: (r) => r.content_hash, render: (r) => short(r.content_hash) }],
    rows: hist, search: false }));
}
async function rulePanel(key) {
  const hist = await api("/v1/rules/" + enc(key));
  const cur = hist.slice().sort((a, b) => b.version_no - a.version_no)[0];
  const r = cur.rule;
  const link = r.source_url && /^https:\/\//.test(r.source_url) ? h("a", { href: r.source_url, rel: "noopener noreferrer", target: "_blank", text: r.source_url }) : r.source_url;
  return h("div", null,
    kv([["Key", r.rule_key], ["Title", r.title], ["Status", chip(cur.status, cur.status === "approved" ? "ok" : cur.status === "rejected" ? "bad" : "warn")], ["Citation", r.citation], ["Source", link],
      ["Jurisdictions", (r.jurisdictions || []).join(", ")], ["Entity types", (r.entity_types || []).map(words).join(", ")], ["Consolidation", words(r.consolidation || "-")],
      ["Published", r.publication_date], ["Effective", r.effective_date], ["Applies from", r.application_date], ["Transition ends", r.transition_end || "-"],
      ["Control owner", r.control_owner], ["Version", cur.version_no], ["Content hash", short(cur.content_hash)]]),
    h("h3", { text: "Numeric requirements" }),
    (r.numeric_requirements || []).length ? table({ cols: [{ title: "Name", get: (x) => x.name }, { title: "Test", get: (x) => x.comparator },
      { title: "Value", num: true, get: (x) => x.value }, { title: "Unit", get: (x) => x.unit }], rows: r.numeric_requirements, search: false }) : h("p", { class: "empty", text: "None recorded." }),
    h("h3", { text: "Decision trail" }),
    table({ cols: [{ title: "Version", num: true, get: (x) => x.version_no }, { title: "Status", get: (x) => x.status }, { title: "Proposed by", get: (x) => x.proposed_by },
      { title: "Decided by", get: (x) => x.decided_by || "-" }, { title: "Note", get: (x) => x.decision_note || "-" }], rows: hist, search: false }));
}
const openContract = (ref) => drawer("Contract " + ref, contractPanel(ref));
const openRule = (key) => drawer("Rule " + key, rulePanel(key));
const hhiBand = (v) => { const n = Number(v); return n >= 2500 ? ["high", "bad"] : n >= 1500 ? ["moderate", "warn"] : ["low", "ok"]; };

// ---------------------------------------------------------------- pages
const PAGES = {};

PAGES.overview = async () => {
  const dash = await api("/v1/dashboard?currency=" + S.ccy + (S.asof ? "&as_of=" + S.asof : ""));
  S.dash = dash;
  await entities().catch(() => null);
  const sec = dash.sections, ok = (k) => sec[k] && !sec[k].error;
  const kp = h("div", { class: "kpis" });
  const breaches = ok("stress") ? (sec.stress.recent_runs || []).reduce((a, r) => a + (r.breaches || 0), 0) : 0;
  if (ok("registry")) kp.append(kpi("k1", num(sec.registry.entities), label("registry", "Registry"), sec.registry.active + " active, " + sec.registry.ownership_links + " ownership links", "#/entities"));
  if (ok("exposure")) kp.append(kpi("k2", money(sec.exposure.total), label("exposure", "Exposure"), sec.exposure.positions + " positions", "#/exposure"));
  if (ok("insurance")) kp.append(kpi("k3", num(sec.insurance.contracts), label("insurance", "Insurance"), "contracts", "#/insurance"));
  if (ok("stress")) kp.append(kpi("k4", num(sec.stress.runs), label("stress", "Stress"), breaches ? breaches + " capital breach(es) in recent runs" : "no breaches in recent runs", "#/stress"));
  if (ok("rules")) kp.append(kpi("k5", num((sec.rules.versions_by_status || {}).approved || 0), label("rules", "Rules"), "approved, " + (sec.rules.open_review_tasks || 0) + " awaiting review", "#/rules"));
  if (ok("connectors")) kp.append(kpi("k6", num(sec.connectors.open_alerts || 0), "Open alerts", "source changes to review", "#/data"));
  const g = h("div", { class: "grid" });
  if (ok("registry")) {
    g.append(card(label("registry", "Registry") + " by type", donut(Object.entries(sec.registry.by_type).sort((a, b) => b[1] - a[1]), sec.registry.entities, (t) => "#/entities?type=" + enc(t)), "span-4"));
    const j = Object.entries(sec.registry.by_jurisdiction).sort((a, b) => b[1] - a[1]);
    g.append(card("Jurisdictions", bars(j.slice(0, 7).map(([c, n], i) => ({ name: c, text: String(n), ratio: n / j[0][1], href: "#/entities?jur=" + enc(c), color: i }))), "span-4"));
  }
  if (ok("insurance")) g.append(card(label("insurance", "Insurance") + " by line", donut(Object.entries(sec.insurance.by_line).sort((a, b) => b[1] - a[1]), sec.insurance.contracts, (t) => "#/insurance?line=" + enc(t)), "span-4"));
  if (ok("exposure")) {
    const top = sec.exposure.top_counterparties || [];
    const m = Math.max(1, ...top.map((r) => Number(r.amount)));
    g.append(lazy("Exposure network", () => api("/v1/exposures/edges?" + qs()), (d) => network({ nodes: Object.fromEntries([...new Set(d.edges.flatMap((e) => [e.holder_lei, e.counterparty_lei]))].map((x) => [x, 1])), edges: d.edges }, { circle: true }), { class: "span-8" }));
    g.append(card("Top counterparties", bars(top.map((r, i) => ({ name: r.name || r.lei, text: money(r.amount) + " | " + r.share_percent + "%", ratio: Number(r.amount) / m, href: "#/entity/" + enc(r.lei) + "/exposure", color: i }))), "span-4"));
  }
  if (ok("stress")) g.append(card("Latest stress runs", table({ cols: [{ title: "Scenario", get: (r) => r.scenario_key }, { title: "Gross", num: true, get: (r) => r.gross_loss, render: (r) => money(r.gross_loss) },
    { title: "Net", num: true, get: (r) => r.net_loss, render: (r) => money(r.net_loss) }, { title: "Status", get: (r) => r.breaches, render: (r) => (r.breaches > 0 ? chip(r.breaches + " breach", "bad") : chip("OK", "ok")) }],
  rows: sec.stress.recent_runs, href: (r) => "#/stress/" + r.run_id, search: false, pageSize: 6 }), "span-6"));
  if (ok("rules")) {
    const by = sec.rules.versions_by_status || {};
    g.append(card("Rules and sources", h("div", null, h("div", { class: "chips" }, Object.entries(by).map(([k, n]) => chip(k + " " + n, k === "approved" ? "ok" : "info")),
      chip("review " + (sec.rules.open_review_tasks || 0), sec.rules.open_review_tasks ? "warn" : "ok"), ok("rates") ? chip(sec.rates.currencies + " FX rates, " + sec.rates.latest_date, "info") : null,
      ok("connectors") ? chip("alerts " + sec.connectors.open_alerts, sec.connectors.open_alerts ? "warn" : "ok") : null),
    h("p", { class: "muted" }, h("a", { href: "#/rules", text: "Open rule library" }), "  |  ", h("a", { href: "#/data", text: "Data & audit" }))), "span-6"));
  }
  for (const k of dash.omitted) g.append(card(label(k, k), h("p", { class: "lock", text: label("unavailable", "Not available for your role.") }), "span-4"));
  return h("div", { class: "grid" }, h("div", { class: "span-12" }, pageHead(label("title", "Overview"), null, h("div", { class: "chips" }, chip(dash.view, "info"), chip(dash.as_of, "neutral"), chip(dash.currency, "neutral")))), h("div", { class: "span-12" }, kp), g.children.length ? [...g.children] : null);
};

PAGES.entities = async (_a, q) => {
  const ents = await entities();
  const rows = [...ents.entries()].map(([lei, e]) => ({ lei, ...e }));
  return h("div", { class: "grid" }, h("div", { class: "span-12" }, pageHead(label("registry", "Entities"), null, chip(rows.length + " entities", "info"))),
    card("Registry", table({ cols: [{ title: "Name", get: (r) => r.legal_name }, { title: "LEI", get: (r) => r.lei, render: (r) => h("span", { class: "mono", text: r.lei }) },
      { title: "Type", get: (r) => r.entity_type, render: (r) => chip(words(r.entity_type), "info") }, { title: "Jur.", get: (r) => r.jurisdiction }, { title: "Currency", get: (r) => r.reporting_currency },
      { title: "Basis", get: (r) => r.reporting_basis, render: (r) => words(r.reporting_basis) }, { title: "Status", get: (r) => r.status, render: (r) => chip(r.status, r.status === "active" ? "ok" : "neutral") }],
    rows, href: (r) => "#/entity/" + enc(r.lei), csv: "entities", pageSize: 15,
    filters: [{ key: "type", label: "Type", get: (r) => r.entity_type }, { key: "jur", label: "Jurisdiction", get: (r) => r.jurisdiction }, { key: "status", label: "Status", get: (r) => r.status }],
    init: { type: q.get("type") || "", jur: q.get("jur") || "" } }), "span-12"));
};

const ENTITY_TABS = [["profile", "Profile"], ["group", "Group"], ["exposure", "Exposure"], ["insurance", "Insurance"], ["rules", "Rules"], ["network", "Network"], ["history", "History"]];
PAGES.entity = async (a) => {
  const lei = a[0], tab = a[1] || "profile";
  await entities().catch(() => null);
  const ent = await api("/v1/entities/" + enc(lei));
  const e = ent.entity, L = enc(lei);
  const head = h("div", { class: "span-12" }, pageHead(e.legal_name, [h("a", { href: "#/entities", text: "Entities" }), " / ", e.lei],
    h("div", { class: "chips" }, chip(words(e.entity_type), "info"), chip(e.jurisdiction, "neutral"), chip(e.status, e.status === "active" ? "ok" : "neutral"))),
  h("nav", { class: "tabs", "aria-label": "Entity sections" }, ENTITY_TABS.map(([k, t]) => h("a", { href: "#/entity/" + L + "/" + k, text: t, "aria-current": k === tab ? "page" : null }))));
  const body = h("div", { class: "grid" });
  const T = {
    profile: () => {
      body.append(card("Details", kv([["Legal name", e.legal_name], ["LEI", h("span", { class: "mono", text: e.lei })], ["Type", words(e.entity_type)], ["Jurisdiction", e.jurisdiction],
        ["Reporting currency", e.reporting_currency], ["Reporting basis", words(e.reporting_basis)], ["Registration no.", e.registration_number], ["Status", e.status], ["Version", ent.version_no],
        ["Last change", dateOf(ent.changed_at) + " by " + ent.changed_by], ["Content hash", short(ent.content_hash)]]), "span-6"));
      body.append(lazy("Exposure position", () => api("/v1/exposures/edges?" + qs()), (d) => {
        const out = d.edges.filter((x) => x.holder_lei === lei), inn = d.edges.filter((x) => x.counterparty_lei === lei);
        const sum = (l) => l.reduce((t, x) => t + Number(x.total), 0);
        return h("div", null, kv([["Lent / held (outgoing)", money(sum(out)) + " in " + out.length + " relationship(s)"], ["Owed to others (incoming)", money(sum(inn)) + " from " + inn.length + " holder(s)"]]),
          h("p", { class: "muted" }, h("a", { href: "#/entity/" + L + "/exposure", text: "Drill into concentration" })));
      }, { class: "span-6" }));
      body.append(card("Licences", (e.licences || []).length ? table({ cols: [{ title: "Licence", get: (x) => JSON.stringify(x) }], rows: e.licences, search: false }) : h("p", { class: "empty", text: "No licences recorded." }), "span-6"));
    },
    group: () => {
      body.append(lazy("Ownership", () => api("/v1/entities/" + L + "/ownership"), (d) => h("div", null, h("h3", { text: "Parents" }),
        d.parents.length ? table({ cols: [{ title: "Parent", get: (r) => nm(r.link.parent_lei), render: (r) => entLink(r.link.parent_lei) }, { title: "Share %", num: true, get: (r) => r.link.percent }, { title: "Kind", get: (r) => r.link.kind }, { title: "Since", get: (r) => r.link.effective_from }], rows: d.parents, search: false }) : h("p", { class: "empty", text: "No parent." }),
        h("h3", { text: "Children" }),
        d.children.length ? table({ cols: [{ title: "Subsidiary", get: (r) => nm(r.link.child_lei), render: (r) => entLink(r.link.child_lei) }, { title: "Share %", num: true, get: (r) => r.link.percent }, { title: "Kind", get: (r) => r.link.kind }, { title: "Since", get: (r) => r.link.effective_from }], rows: d.children, search: false }) : h("p", { class: "empty", text: "No subsidiaries." }))
      , { class: "span-6" }));
      body.append(lazy("Control perimeter", () => api("/v1/entities/" + L + "/group"), (d) => h("div", null, h("p", { class: "muted", text: d.note }),
        d.members.length ? table({ cols: [{ title: "Member", get: (r) => nm(r.lei), render: (r) => entLink(r.lei) }, { title: "Depth", num: true, get: (r) => r.depth }, { title: "Direct %", num: true, get: (r) => r.direct_percent }, { title: "Effective %", num: true, get: (r) => r.effective_percent }], rows: d.members, search: false }) : h("p", { class: "empty", text: "No controlled entities." }))
      , { class: "span-6" }));
      body.append(lazy("Ultimate parent", () => api("/v1/entities/" + L + "/ultimate-parent"), (d) => h("p", null, entLink(d.lei, d.entity.legal_name), d.lei === lei ? "  (this entity is the top of its group)" : ""), { class: "span-12" }));
    },
    exposure: () => {
      const holder = h("div");
      let byGroup = false;
      const draw = () => {
        holder.replaceChildren(skel());
        api("/v1/exposures/concentration/" + L + "?" + qs() + (byGroup ? "&by_group=true" : "")).then((d) => {
          const [band, tone] = hhiBand(d.hhi);
          const m = Math.max(1, ...d.shares.map((x) => Number(x.total)));
          holder.replaceChildren(h("div", { class: "kpis" }, kpi("k2", money(d.total), "Total exposure", d.shares.length + " counterparties"), kpi("k3", Number(d.hhi).toFixed(0), "HHI", band + " concentration (indicative bands)"),
            kpi("k4", pct(d.top1), "Largest counterparty"), kpi("k5", pct(d.top3), "Top three")),
          h("p", null, chip(band + " concentration", tone)), bars(d.shares.map((x, i) => ({ name: nm(x.key), text: money(x.total) + " | " + pct(x.share), ratio: Number(x.total) / m, href: "#/entity/" + enc(x.key), color: i }))),
          h("p", { class: "muted", text: "Data lineage: batches " + d.lineage.batch_ids.join(", ") + ", " + d.lineage.positions_used + " positions." }));
        }).catch((er) => holder.replaceChildren(errMsg(er)));
      };
      body.append(card("Concentration", holder, "span-12", h("label", { class: "muted" }, h("input", { type: "checkbox", onchange: (ev) => { byGroup = ev.target.checked; draw(); } }), " roll up to group")));
      draw();
      body.append(lazy("Relationships", () => api("/v1/exposures/edges?" + qs()), (d) => {
        const rel = d.edges.filter((x) => x.holder_lei === lei || x.counterparty_lei === lei).map((x) => ({ ...x, dir: x.holder_lei === lei ? "outgoing" : "incoming", other: x.holder_lei === lei ? x.counterparty_lei : x.holder_lei }));
        return table({ cols: [{ title: "Direction", get: (r) => r.dir, render: (r) => chip(r.dir, r.dir === "outgoing" ? "info" : "warn") }, { title: "Counterparty", get: (r) => nm(r.other), render: (r) => entLink(r.other) },
          { title: "Amount", num: true, get: (r) => r.total, render: (r) => money(r.total) }, { title: "Positions", num: true, get: (r) => r.count }], rows: rel, csv: "relationships-" + lei, search: false });
      }, { class: "span-12" }));
    },
    insurance: () => {
      body.append(lazy("Cover received", () => api("/v1/entities/" + L + "/insurance?" + qs()), (d) => {
        const m = Math.max(1, ...d.shares.map((x) => Number(x.total)));
        return h("div", null, h("div", { class: "kpis" }, kpi("k3", money(d.total_cover), "Total cover"), kpi("k4", pct(d.top1), "Largest insurer"), kpi("k5", Number(d.hhi).toFixed(0), "Insurer HHI")),
          bars(d.shares.map((x, i) => ({ name: nm(x.key), text: money(x.total) + " | " + pct(x.share), ratio: Number(x.total) / m, href: "#/entity/" + enc(x.key), color: i }))),
          d.contracts.length ? table({ cols: [{ title: "Contract", get: (r) => r.contract_ref }, { title: "Line", get: (r) => r.line, render: (r) => chip(words(r.line), "info") }, { title: "Insurer", get: (r) => nm(r.insurer_lei), render: (r) => entLink(r.insurer_lei) },
            { title: "Cover", num: true, get: (r) => r.sum_insured, render: (r) => money(r.sum_insured) }], rows: d.contracts, open: (r) => openContract(r.contract_ref), search: false }) : h("p", { class: "empty", text: "No cover recorded." }));
      }, { class: "span-12" }));
      body.append(lazy("Contracts written by this entity", () => api("/v1/contracts?limit=200&insurer=" + L), (d) => (d.length ? table({ cols: [{ title: "Contract", get: (r) => r.contract_ref }, { title: "Insured", get: (r) => nm(r.contract.insured_lei), render: (r) => entLink(r.contract.insured_lei) },
        { title: "Line", get: (r) => r.contract.line, render: (r) => chip(words(r.contract.line), "info") }, { title: "Sum insured", num: true, get: (r) => r.contract.sum_insured, render: (r) => moneyIn(r.contract.sum_insured, r.contract.currency) }],
      rows: d, open: (r) => openContract(r.contract_ref), search: false }) : h("p", { class: "empty", text: "None." })), { class: "span-12" }));
    },
    rules: () => body.append(lazy("Applicable rules", () => api("/v1/entities/" + L + "/rules"), (d) => (d.length ? table({ cols: [{ title: "Rule", get: (r) => r.version.rule_key }, { title: "Title", get: (r) => r.version.rule.title },
      { title: "Applies from", get: (r) => r.version.rule.application_date }, { title: "Status", get: (r) => (r.in_transition ? "transition" : "in force"), render: (r) => chip(r.in_transition ? "transition" : "in force", r.in_transition ? "warn" : "ok") }],
    rows: d, open: (r) => openRule(r.version.rule_key), csv: "rules-" + lei }) : h("p", { class: "empty", text: "No approved rule applies on this date." })), { class: "span-12" })),
    network: () => {
      const holder = h("div");
      let depth = 2;
      const draw = () => {
        holder.replaceChildren(skel());
        api("/v1/entities/" + L + "/ecosystem?" + qs() + "&depth=" + depth).then((d) => holder.replaceChildren(network(d), ...(d.truncated ? [h("p", { class: "muted", text: "The view was truncated for size." })] : []))).catch((er) => holder.replaceChildren(errMsg(er)));
      };
      body.append(card("Ecosystem", holder, "span-12", h("label", { class: "muted" }, "Depth ", h("select", { "aria-label": "Depth", onchange: (ev) => { depth = Number(ev.target.value); draw(); } },
        [1, 2, 3].map((n) => h("option", { value: n, text: String(n), selected: n === 2 }))))));
      draw();
    },
    history: () => body.append(lazy("Version history", () => api("/v1/entities/" + L + "/history"), (d) => table({ cols: [{ title: "Version", num: true, get: (r) => r.version_no }, { title: "Changed", get: (r) => dateOf(r.changed_at) },
      { title: "By", get: (r) => r.changed_by }, { title: "Summary", get: (r) => r.change_summary }, { title: "Hash", get: (r) => r.content_hash, render: (r) => short(r.content_hash) }], rows: d, search: false }), { class: "span-12" })),
  };
  (T[tab] || T.profile)();
  return h("div", { class: "grid" }, head, h("div", { class: "span-12" }, body));
};

PAGES.exposure = async () => {
  await entities().catch(() => null);
  const edges = await api("/v1/exposures/edges?" + qs());
  const total = edges.edges.reduce((a, x) => a + Number(x.total), 0) || 1;
  const holders = [...new Set(edges.edges.map((x) => x.holder_lei))];
  const cps = new Set(edges.edges.map((x) => x.counterparty_lei));
  const g = h("div", { class: "grid" });
  g.append(h("div", { class: "span-12" }, pageHead(label("exposure", "Exposure"), null, h("div", { class: "chips" }, chip(S.ccy, "neutral"), chip("as of " + (S.asof || "today"), "neutral")))));
  g.append(h("div", { class: "span-12 kpis" }, kpi("k2", money(total), "Total exposure"), kpi("k1", num(edges.edges.length), "Relationships"), kpi("k3", num(holders.length), "Holders"), kpi("k4", num(cps.size), "Counterparties")));
  g.append(card("Largest exposures", table({ cols: [{ title: "Holder", get: (r) => nm(r.holder_lei), render: (r) => entLink(r.holder_lei) }, { title: "Counterparty", get: (r) => nm(r.counterparty_lei), render: (r) => entLink(r.counterparty_lei) },
    { title: "Amount", num: true, get: (r) => r.total, render: (r) => money(r.total) }, { title: "Share", num: true, get: (r) => Number(r.total) / total, render: (r) => pct(Number(r.total) / total) }, { title: "Positions", num: true, get: (r) => r.count }],
  rows: edges.edges, csv: "exposures", pageSize: 10, open: (r) => { location.hash = "#/entity/" + enc(r.holder_lei) + "/exposure"; } }), "span-8"));
  g.append(lazy("Concentration by holder", () => Promise.all(holders.slice(0, 12).map((x) => api("/v1/exposures/concentration/" + enc(x) + "?" + qs()))), (list) => table({ cols: [
    { title: "Holder", get: (r) => nm(r.holder_lei), render: (r) => entLink(r.holder_lei) }, { title: "Total", num: true, get: (r) => r.total, render: (r) => money(r.total) },
    { title: "Top 1", num: true, get: (r) => r.top1, render: (r) => pct(r.top1) }, { title: "HHI", num: true, get: (r) => r.hhi, render: (r) => { const [b, t] = hhiBand(r.hhi); return h("span", { class: "hhi" }, Number(r.hhi).toFixed(0), chip(b, t)); } }],
  rows: list, search: false, pageSize: 8, open: (r) => { location.hash = "#/entity/" + enc(r.holder_lei) + "/exposure"; } }), { class: "span-4" }));
  g.append(lazy("Relationship network", () => Promise.resolve(edges), (d) => network({ nodes: Object.fromEntries([...new Set(d.edges.flatMap((e) => [e.holder_lei, e.counterparty_lei]))].map((x) => [x, 1])), edges: d.edges }, { circle: true }), { class: "span-8" }));
  g.append(lazy("Data batches (lineage)", () => api("/v1/exposures/batches?limit=200"), (d) => table({ cols: [{ title: "Batch", num: true, get: (r) => r.batch_id }, { title: "Source", get: (r) => r.source_system }, { title: "Positions", num: true, get: (r) => r.position_count },
    { title: "Loaded", get: (r) => dateOf(r.loaded_at) }, { title: "Hash", get: (r) => r.content_hash, render: (r) => short(r.content_hash) }], rows: d, search: false, pageSize: 6 }), { class: "span-4" }));
  return g;
};

PAGES.insurance = async (_a, q) => {
  await entities().catch(() => null);
  let rows = [], after = "";
  for (let i = 0; i < 10; i += 1) {
    const page = await api("/v1/contracts?limit=200" + (after ? "&after=" + enc(after) : ""));
    rows = rows.concat(page);
    if (page.length < 200) break;
    after = page[page.length - 1].contract_ref;
  }
  const flat = rows.map((r) => ({ ...r.contract, version_no: r.version_no }));
  const byLine = {}, byInsurer = {};
  for (const c of flat) { byLine[c.line] = (byLine[c.line] || 0) + 1; byInsurer[c.insurer_lei] = (byInsurer[c.insurer_lei] || 0) + 1; }
  const ins = Object.entries(byInsurer).sort((a, b) => b[1] - a[1]);
  return h("div", { class: "grid" }, h("div", { class: "span-12" }, pageHead(label("insurance", "Insurance"), null, chip(flat.length + " contracts", "info"))),
    card("Contracts by line", donut(Object.entries(byLine).sort((a, b) => b[1] - a[1]), flat.length, (t) => "#/insurance?line=" + enc(t)), "span-4"),
    card("Contracts per insurer", bars(ins.slice(0, 8).map(([lei, n], i) => ({ name: nm(lei), text: String(n), ratio: n / ins[0][1], href: "#/entity/" + enc(lei) + "/insurance", color: i }))), "span-4"),
    card("Amounts", h("p", { class: "muted", text: "Contract amounts are shown in each contract's own currency. Cross-currency totals are only produced in the exposure and stress views, using stored FX rates." }), "span-4"),
    card("Contracts", table({ cols: [{ title: "Contract", get: (r) => r.contract_ref }, { title: "Line", get: (r) => r.line, render: (r) => chip(words(r.line), "info") }, { title: "Insurer", get: (r) => nm(r.insurer_lei), render: (r) => entLink(r.insurer_lei) },
      { title: "Insured", get: (r) => nm(r.insured_lei), render: (r) => entLink(r.insured_lei) }, { title: "Sum insured", num: true, get: (r) => r.sum_insured, render: (r) => moneyIn(r.sum_insured, r.currency) },
      { title: "From", get: (r) => r.start_date }, { title: "Reinsures", get: (r) => r.reinsures_ref || "-" }], rows: flat, open: (r) => openContract(r.contract_ref), csv: "contracts", pageSize: 12,
    filters: [{ key: "line", label: "Line", get: (r) => r.line }], init: { line: q.get("line") || "" } }), "span-12"));
};

PAGES.stress = async (a) => {
  await entities().catch(() => null);
  if (a[0]) return stressRun(Number(a[0]));
  const [runs, scen] = await Promise.all([api("/v1/stress/runs?limit=50"), api("/v1/stress/scenarios")]);
  const details = await Promise.all(runs.slice(0, 20).map((r) => api("/v1/stress/runs/" + r.run_id).catch(() => null)));
  const rows = runs.map((r, i) => ({ ...r, tot: details[i] ? details[i].result.totals : null }));
  return h("div", { class: "grid" }, h("div", { class: "span-12" }, pageHead(label("stress", "Stress tests"), null, chip(runs.length + " runs", "info"))),
    card("Scenarios", table({ cols: [{ title: "Scenario", get: (r) => r.scenario_key }, { title: "Title", get: (r) => r.scenario.title }, { title: "Version", num: true, get: (r) => r.version_no },
      { title: "Shocks", get: (r) => r.scenario.shocks.map((x) => x.kind).join(", ") }, { title: "Insurance", get: (r) => (r.scenario.use_insurance ? "applied" : "ignored") }], rows: scen, search: false, pageSize: 6,
    open: (r) => drawer("Scenario " + r.scenario_key, Promise.resolve(h("div", null, kv([["Title", r.scenario.title], ["Description", r.scenario.description], ["Version", r.version_no], ["Insurance", r.scenario.use_insurance ? "recoveries applied" : "ignored"], ["Hash", short(r.content_hash)]]),
      h("h3", { text: "Shocks" }), h("ul", null, r.scenario.shocks.map((x) => h("li", { text: shockText(x) })))))) }), "span-12"),
    card("Runs (newest first)", table({ cols: [{ title: "Run", num: true, get: (r) => r.run_id }, { title: "Scenario", get: (r) => r.scenario_key }, { title: "As of", get: (r) => r.as_of },
      { title: "Gross loss", num: true, get: (r) => (r.tot ? r.tot.gross_loss : ""), render: (r) => (r.tot ? moneyIn(r.tot.gross_loss, r.currency) : "-") }, { title: "Net loss", num: true, get: (r) => (r.tot ? r.tot.net_loss : ""), render: (r) => (r.tot ? moneyIn(r.tot.net_loss, r.currency) : "-") },
      { title: "Breaches", get: (r) => (r.tot ? r.tot.breaches : ""), render: (r) => (r.tot ? (r.tot.breaches > 0 ? chip(r.tot.breaches + " breach", "bad") : chip("OK", "ok")) : "-") }, { title: "Created", get: (r) => dateOf(r.created_at) }],
    rows, href: (r) => "#/stress/" + r.run_id, csv: "stress-runs", pageSize: 12 }), "span-12"));
};
async function stressRun(id) {
  const [d, snap] = await Promise.all([api("/v1/stress/runs/" + id), api("/v1/stress/runs/" + id + "/snapshot").catch(() => null)]);
  const run = d.run, r = d.result, t = r.totals, ccy = r.currency;
  const scen = await api("/v1/stress/scenarios/" + enc(run.scenario_key) + "?version=" + run.scenario_version).catch(() => null);
  const mc = (v) => moneyIn(v, ccy);
  const verify = h("div");
  const worst = Math.max(1, ...r.holders.map((x) => Number(x.net_loss_ratio)));
  const g = h("div", { class: "grid" });
  g.append(h("div", { class: "span-12" }, pageHead("Run #" + id + " - " + run.scenario_key, [h("a", { href: "#/stress", text: "Stress tests" }), " / run ", String(id)], h("div", { class: "chips" }, chip(run.as_of, "neutral"), chip(ccy, "neutral"), chip(t.breaches > 0 ? t.breaches + " capital breach(es)" : "no breaches", t.breaches > 0 ? "bad" : "ok")))));
  g.append(h("div", { class: "span-12 kpis" }, kpi("k2", mc(t.exposure), "Exposure in scope"), kpi("k4", mc(t.gross_loss), "Gross loss"), kpi("k3", mc(t.insurance_recovery), "Insurance recovery"), kpi("k1", mc(t.net_loss), "Net loss"), kpi("k5", num(t.breaches), "Breaches")));
  g.append(card("Holders", table({ cols: [{ title: "Holder", get: (x) => nm(x.holder_lei), render: (x) => entLink(x.holder_lei) }, { title: "Exposure", num: true, get: (x) => x.exposure, render: (x) => mc(x.exposure) }, { title: "Gross", num: true, get: (x) => x.gross_loss, render: (x) => mc(x.gross_loss) },
    { title: "Recovery", num: true, get: (x) => x.insurance_recovery, render: (x) => mc(x.insurance_recovery) }, { title: "Net", num: true, get: (x) => x.net_loss, render: (x) => mc(x.net_loss) },
    { title: "Net / exposure", num: true, get: (x) => x.net_loss_ratio, render: (x) => pct(x.net_loss_ratio) }, { title: "Buffer", num: true, get: (x) => x.capital_buffer || 0, render: (x) => (x.capital_buffer ? mc(x.capital_buffer) : "-") },
    { title: "Status", get: (x) => (x.breach ? 1 : 0), render: (x) => (x.breach ? chip("BREACH", "bad") : x.capital_buffer ? chip("covered", "ok") : chip("no buffer set", "neutral")) }],
  rows: r.holders, csv: "stress-run-" + id, search: false, pageSize: 10 }), "span-8"));
  g.append(card("Biggest loss drivers", bars((r.top_contributors || []).map((x, i) => ({ name: nm(x.counterparty_lei), text: mc(x.gross_loss), ratio: Number(x.gross_loss) / Math.max(1, ...r.top_contributors.map((y) => Number(y.gross_loss))), href: "#/entity/" + enc(x.counterparty_lei), color: i }))), "span-4"));
  g.append(card("Scenario", scen ? h("div", null, kv([["Title", scen.scenario.title], ["Version", scen.version_no], ["Insurance", scen.scenario.use_insurance ? "recoveries applied" : "ignored"]]), h("ul", null, scen.scenario.shocks.map((x) => h("li", { text: shockText(x) })))) : h("p", { class: "empty", text: "Scenario text unavailable." }), "span-6"));
  g.append(card("Integrity", h("div", null, h("p", { class: "muted", text: "Recomputes the result from the frozen inputs and checks hashes and the signature." }),
    h("button", { type: "button", class: "btn btn-primary", text: "Verify this run", onclick: async (ev) => {
      ev.target.disabled = true;
      try {
        const v = await api("/v1/stress/runs/" + id + "/verify", { method: "POST" });
        verify.replaceChildren(h("div", { class: "chips" }, Object.entries(v).filter(([k]) => k !== "run_id").map(([k, ok]) => chip(words(k) + ": " + (ok ? "yes" : "NO"), ok ? "ok" : "bad"))));
      } catch (er) { verify.replaceChildren(errMsg(er)); }
      ev.target.disabled = false;
    } }), verify, h("p", { class: "muted" }, "Input hash ", short(run.input_hash), "  Result hash ", short(run.result_hash))), "span-6"));
  if (snap) g.append(card("Frozen inputs (lineage)", h("div", null, kv([["Batches", (snap.snapshot.lineage.batch_ids || []).join(", ")], ["As of", snap.snapshot.as_of], ["Positions", num((snap.snapshot.rows || []).length)], ["Contracts used", num((snap.snapshot.covers || []).length)]]),
    h("h3", { text: "FX rates used (per 1 unit, in " + ccy + ")" }), table({ cols: [{ title: "Currency", get: (x) => x.currency }, { title: "Rate", num: true, get: (x) => x.rate }, { title: "Date", get: (x) => x.rate_date }], rows: snap.snapshot.lineage.fx || [], search: false, pageSize: 8 })), "span-12"));
  return g;
}

PAGES.rules = async () => {
  const [rules, tasks] = await Promise.all([api("/v1/rules"), api("/v1/review-tasks").catch(() => [])]);
  const flat = rules.map((v) => ({ key: v.rule_key, status: v.status, ver: v.version_no, ...v.rule }));
  return h("div", { class: "grid" }, h("div", { class: "span-12" }, pageHead(label("rules", "Rules"), null, h("div", { class: "chips" }, chip(flat.length + " rules", "info"), chip(tasks.length + " awaiting review", tasks.length ? "warn" : "ok")))),
    card("Rule library", table({ cols: [{ title: "Key", get: (r) => r.key }, { title: "Title", get: (r) => r.title }, { title: "Status", get: (r) => r.status, render: (r) => chip(r.status, r.status === "approved" ? "ok" : r.status === "rejected" ? "bad" : "warn") },
      { title: "Jurisdictions", get: (r) => (r.jurisdictions || []).join(", ") }, { title: "Effective", get: (r) => r.effective_date }, { title: "Applies from", get: (r) => r.application_date }, { title: "Owner", get: (r) => r.control_owner }],
    rows: flat, open: (r) => openRule(r.key), csv: "rules", filters: [{ key: "status", label: "Status", get: (r) => r.status }] }), "span-12"),
    card("Open review tasks", tasks.length ? table({ cols: [{ title: "Task", num: true, get: (r) => r.task_id }, { title: "Rule", get: (r) => r.rule_key }, { title: "Version", num: true, get: (r) => r.version_no }, { title: "Owner", get: (r) => r.owner }, { title: "Opened", get: (r) => dateOf(r.created_at) }],
      rows: tasks, open: (r) => openRule(r.rule_key), search: false }) : h("p", { class: "empty", text: "Nothing waiting." }), "span-12"));
};

PAGES.data = async () => {
  const g = h("div", { class: "grid" });
  g.append(h("div", { class: "span-12" }, pageHead("Data & audit", null, S.offline ? chip("offline: connectors are off", "warn") : null)));
  g.append(lazy("Audit chain", () => api("/v1/audit/verify"), (v) => h("div", null, h("div", { class: "chips" }, chip(v.ok ? "chain intact" : "CHAIN BROKEN at #" + v.first_bad, v.ok ? "ok" : "bad"), chip(v.entries + " entries", "info")),
    h("p", { class: "muted" }, "Head ", short(v.head.hash || "-"), "  (every entry contains the hash of the one before it; any edit breaks the chain)")), { class: "span-6" }));
  g.append(lazy("Source connectors", () => api("/v1/connectors/runs?limit=50"), (runs) => {
    const run = (name) => h("button", { type: "button", class: "btn", text: "Run " + name, disabled: S.offline, title: S.offline ? "Switched off in offline mode" : "Fetch now",
      onclick: async (ev) => { ev.target.disabled = true; try { const r = await api("/v1/connectors/" + name + "/run", { method: "POST" }); S.cache.clear(); toast(name + ": " + (r.status || "done")); } catch (er) { toast(er.message, true); } ev.target.disabled = S.offline; } });
    return h("div", null, h("div", { class: "chips" }, run("ecb_rates"), run("eurlex_watch")), runs.length ? table({ cols: [{ title: "Run", num: true, get: (r) => r.run_id }, { title: "Connector", get: (r) => r.connector }, { title: "Status", get: (r) => r.status }, { title: "Started", get: (r) => dateOf(r.started_at || r.created_at) }], rows: runs, search: false, pageSize: 5 }) : h("p", { class: "empty", text: "No runs yet." }));
  }, { class: "span-6" }));
  g.append(lazy("Source alerts", () => api("/v1/source-alerts?limit=200"), (d) => table({ cols: [{ title: "Alert", num: true, get: (r) => r.alert_id }, { title: "Source", get: (r) => r.source }, { title: "Title", get: (r) => r.title, render: (r) => (r.url && /^https:\/\//.test(r.url) ? h("a", { href: r.url, rel: "noopener noreferrer", target: "_blank", text: r.title }) : r.title) },
    { title: "Status", get: (r) => r.status, render: (r) => chip(r.status, r.status === "open" ? "warn" : "ok") }, { title: "", get: () => "", render: (r) => (r.status === "open" ? h("button", { type: "button", class: "btn", text: "Acknowledge", onclick: async (ev) => {
      ev.target.disabled = true;
      try { await api("/v1/source-alerts/" + r.alert_id + "/acknowledge", { method: "POST" }); S.cache.clear(); toast("Alert " + r.alert_id + " acknowledged"); route(); } catch (er) { toast(er.message, true); ev.target.disabled = false; }
    } }) : "") }], rows: d, filters: [{ key: "status", label: "Status", get: (r) => r.status }], pageSize: 6 }), { class: "span-12" }));
  g.append(lazy("Audit trail", () => api("/v1/audit?limit=500"), (d) => table({ cols: [{ title: "#", num: true, get: (r) => r.n }, { title: "Time", get: (r) => r.ts.slice(0, 19).replace("T", " ") }, { title: "Actor", get: (r) => r.actor }, { title: "Action", get: (r) => r.action },
    { title: "Subject", get: (r) => r.subject }, { title: "Hash", get: (r) => r.entry_hash, render: (r) => short(r.entry_hash) }], rows: d.slice().reverse(), csv: "audit", pageSize: 12, filters: [{ key: "action", label: "Action", get: (r) => r.action }],
  open: (r) => drawer("Audit entry #" + r.n, Promise.resolve(h("div", null, kv([["Time", r.ts], ["Actor", r.actor], ["Action", r.action], ["Subject", r.subject], ["Entry hash", h("span", { class: "mono", text: r.entry_hash })], ["Previous hash", h("span", { class: "mono", text: r.prev_hash })]]),
    h("h3", { text: "Detail" }), kv(Object.entries(r.detail || {}).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)]))))) }), { class: "span-12" }));
  return g;
};

// ---------------------------------------------------------------- router and shell
const NAV = [
  ["overview", "title", "Overview", "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"],
  ["entities", "registry", "Entities", "M4 21V5l8-2v18M12 9h8v12M7 9h2M7 13h2M7 17h2M15 13h2M15 17h2"],
  ["exposure", "exposure", "Exposure", "M4 20V10M10 20V4M16 20v-8M22 20H2"],
  ["insurance", "insurance", "Insurance", "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"],
  ["stress", "stress", "Stress tests", "M3 12h4l3-8 4 16 3-8h4"],
  ["rules", "rules", "Rules", "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7"],
  ["data", "connectors", "Data & audit", "M4 6c0-2 16-2 16 0v12c0 2-16 2-16 0zM4 12c0 2 16 2 16 0M4 6c0 2 16 2 16 0"],
];
function buildNav() {
  $("nav").replaceChildren(...NAV.map(([id, key, fallback, d]) => h("a", { href: "#/" + id, "data-page": id }, s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("path", { d })), h("span", { text: id === "overview" || id === "data" ? fallback : label(key, fallback) }))));
}
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  return { parts: path.split("/").filter(Boolean).map(decodeURIComponent), q: new URLSearchParams(query || "") };
}
function errorPanel(e) {
  if (e.status === 401) { showSignin("Your session expired. Sign in again."); return h("div"); }
  return h("div", { class: "card" }, h("div", { class: "card-body" }, errMsg(e), h("p", null, h("button", { type: "button", class: "btn", text: "Try again", onclick: () => { S.cache.clear(); route(); } }))));
}
async function route() {
  closeDrawer();
  const { parts, q } = parseHash();
  const name = PAGES[parts[0]] ? parts[0] : "overview";
  document.querySelectorAll("#nav a").forEach((a) => (a.dataset.page === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  const main = $("main");
  const my = ++S.nav;
  main.replaceChildren(skel());
  try {
    const node = await PAGES[name](parts.slice(1), q);
    if (my !== S.nav) return;
    main.replaceChildren(node);
  } catch (e) {
    if (my !== S.nav) return;
    main.replaceChildren(errorPanel(e));
  }
  document.title = (name === "overview" ? "Overview" : name[0].toUpperCase() + name.slice(1)) + " - RegGraph Europe";
  main.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  if (S.dash) $("status").textContent = [S.dash.view, S.dash.as_of, S.ccy].join(" | ");
}
function showSignin(msg) {
  S.token = ""; S.cache.clear(); S.ents = null;
  $("app").hidden = true; $("signin").hidden = false;
  $("signin-error").textContent = msg || "";
  $("token").value = "";
  $("token").focus();
}
async function start(token) {
  S.token = token;
  try {
    const d = await api("/v1/dashboard?currency=" + S.ccy);
    S.dash = d; S.labels = d.labels || {}; S.offline = !!d.offline;
  } catch (e) {
    showSignin(e.status === 401 ? "That token was not accepted." : e.message);
    return;
  }
  $("signin").hidden = true; $("app").hidden = false;
    $("offline").hidden = !S.offline;
  $("who").textContent = S.dash.view;
  $("notice").textContent = S.labels["notice.decision_support"] || "";
  buildNav();
  initHeader();
  if (!location.hash.startsWith("#/")) history.replaceState(null, "", location.pathname + location.search + "#/overview");
  route();
}

// ---------------------------------------------------------------- global search (combobox)
function setupSearch() {
  const input = $("q"), list = $("q-list");
  let items = [], idx = -1;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); idx = -1; };
  const go = (lei) => { close(); input.value = ""; location.hash = "#/entity/" + enc(lei); };
  const mark = () => [...list.children].forEach((li, i) => li.setAttribute("aria-selected", i === idx ? "true" : "false"));
  input.addEventListener("input", async () => {
    const t = input.value.trim().toLowerCase();
    if (!t) { close(); return; }
    const ents = await entities().catch(() => new Map());
    items = [...ents.entries()].filter(([lei, e]) => e.legal_name.toLowerCase().includes(t) || lei.toLowerCase().includes(t)).slice(0, 8);
    list.replaceChildren(...items.map(([lei, e], i) => h("li", { role: "option", id: "q-" + i, "aria-selected": "false", onmousedown: (ev) => { ev.preventDefault(); go(lei); } },
      h("span", { text: e.legal_name }), h("span", { class: "muted", text: e.jurisdiction + " " + words(e.entity_type) }))));
    if (!items.length) list.replaceChildren(h("li", { class: "muted", text: "No match" }));
    list.hidden = false; input.setAttribute("aria-expanded", "true"); idx = -1;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { close(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); idx = Math.min(items.length - 1, idx + 1); mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); idx = Math.max(0, idx - 1); mark(); }
    else if (e.key === "Enter" && items.length) { e.preventDefault(); go(items[Math.max(0, idx)][0]); }
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if ((e.key === "/" && !typing) || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k")) { e.preventDefault(); input.focus(); }
  });
}

// ---------------------------------------------------------------- boot
// ---------------------------------------------------------------- header: popovers, currency, date, theme, account
const POPS = [];
function popover(btn, pop, onOpen) {
  const close = (focusBack) => { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); if (focusBack) btn.focus(); };
  const open = () => { POPS.forEach((p) => p.close(false)); pop.hidden = false; btn.setAttribute("aria-expanded", "true"); if (onOpen) onOpen(); };
  btn.addEventListener("click", () => (pop.hidden ? open() : close(true)));
  pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(true); } });
  POPS.push({ close, pop, btn });
  return { close, open };
}
document.addEventListener("click", (e) => POPS.forEach((p) => { if (!p.pop.hidden && !p.pop.contains(e.target) && !p.btn.contains(e.target)) p.close(false); }));

const COMMON = ["EUR", "CHF", "USD", "GBP", "SGD"];
let CURRENCIES = [];
function drawCurrencies(filter) {
  const t = (filter || "").toLowerCase();
  const list = $("ccy-list");
  const rows = CURRENCIES.filter((c) => !t || c.code.toLowerCase().includes(t) || c.name.toLowerCase().includes(t));
  list.replaceChildren(...rows.map((c) => h("li", { role: "presentation" }, h("button", { type: "button", class: "opt", role: "option", "aria-selected": c.code === S.ccy ? "true" : "false",
    onclick: () => { pickCurrency(c.code); } }, h("span", { class: "sym", text: c.symbol }), h("span", { class: "code", text: c.code }), h("span", { class: "nm", text: c.name }), c.code === S.ccy ? h("span", { class: "tick", text: "\u2713" }) : null))));
  if (!rows.length) list.replaceChildren(h("li", { class: "pop-note", text: "No currency matches." }));
}
function pickCurrency(code) {
  S.ccy = code; $("ccy-label").textContent = code; ccyPop.close(true);
  S.cache.clear(); route();
}
async function loadCurrencies() {
  try {
    const list = await api("/v1/currencies");
    CURRENCIES = list.slice().sort((a, b) => {
      const x = COMMON.indexOf(a.code), y = COMMON.indexOf(b.code);
      return (x < 0 ? 99 : x) - (y < 0 ? 99 : y) || a.code.localeCompare(b.code);
    });
  } catch (e) { CURRENCIES = [{ code: "EUR", name: "Euro", symbol: "\u20ac" }]; }
  drawCurrencies("");
}
const ccyPop = popover($("ccy-btn"), $("ccy-pop"), () => { $("ccy-q").value = ""; drawCurrencies(""); $("ccy-q").focus(); });
$("ccy-q").addEventListener("input", (e) => drawCurrencies(e.target.value));
$("ccy-q").addEventListener("keydown", (e) => {
  const opts = [...$("ccy-list").querySelectorAll("button")];
  if (e.key === "Enter" && opts.length) { e.preventDefault(); opts[0].click(); }
  else if (e.key === "ArrowDown" && opts.length) { e.preventDefault(); opts[0].focus(); }
});
$("ccy-list").addEventListener("keydown", (e) => {
  const opts = [...$("ccy-list").querySelectorAll("button")], i = opts.indexOf(document.activeElement);
  if (e.key === "ArrowDown") { e.preventDefault(); (opts[i + 1] || opts[0]).focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); (i <= 0 ? $("ccy-q") : opts[i - 1]).focus(); }
});

const iso = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
function presets() {
  const now = new Date(), y = now.getFullYear(), m = now.getMonth();
  const day = (n) => { const d = new Date(now); d.setDate(d.getDate() - n); return d; };
  const qEnd = new Date(y, Math.floor(m / 3) * 3, 0);
  return [["Today", ""], ["Yesterday", iso(day(1))], ["End of last month", iso(new Date(y, m, 0))], ["End of last quarter", iso(qEnd)], ["End of last year", iso(new Date(y - 1, 11, 31))]];
}
function setAsOf(v) {
  S.asof = v; $("asof").value = v;
  $("date-label").textContent = v ? new Intl.DateTimeFormat(S.locale, { day: "numeric", month: "short", year: "numeric" }).format(new Date(v + "T12:00:00")) : "Today";
  datePop.close(true); S.cache.clear(); route();
}
const datePop = popover($("date-btn"), $("date-pop"), () => {
  $("date-presets").replaceChildren(...presets().map(([t, v]) => h("button", { type: "button", class: "opt", "aria-selected": v === S.asof ? "true" : "false", onclick: () => setAsOf(v) },
    h("span", { text: t }), h("span", { class: "nm", text: v || "live data" }), v === S.asof ? h("span", { class: "tick", text: "\u2713" }) : null)));
});
$("asof").max = iso(new Date());
$("asof").addEventListener("change", (e) => { if (e.target.value) setAsOf(e.target.value); });

function applyTheme(light) {
  document.documentElement.dataset.theme = light ? "light" : "dark";
  const sw = $("theme");
  sw.setAttribute("aria-checked", light ? "true" : "false");
  sw.setAttribute("aria-label", light ? "Light theme (switch to dark)" : "Dark theme (switch to light)");
}
$("theme").addEventListener("click", () => applyTheme($("theme").getAttribute("aria-checked") !== "true"));
applyTheme(!matchMedia("(prefers-color-scheme: dark)").matches);

popover($("user-btn"), $("user-pop"));
$("print").addEventListener("click", () => { POPS.forEach((p) => p.close(false)); window.print(); });
$("signout").addEventListener("click", () => { POPS.forEach((p) => p.close(false)); history.replaceState(null, "", location.pathname + location.search); showSignin("Signed out."); });
$("refresh").addEventListener("click", () => { S.cache.clear(); S.ents = null; route(); toast("Reloaded"); });
function initHeader() {
  const who = (S.dash && S.dash.view) || "user";
  $("avatar-text").textContent = who.slice(0, 2).toUpperCase();
  $("user-name").textContent = "Signed in as " + who + (S.offline ? " (offline mode)" : "");
  loadCurrencies();
}
$("signin-form").addEventListener("submit", (e) => { e.preventDefault(); start($("token").value.trim()); });
window.addEventListener("hashchange", () => { if (S.token) route(); });
setupSearch();

(function boot() {
  const m = /^#token=([A-Za-z0-9._~-]{1,4096})$/.exec(location.hash);
  if (m) { history.replaceState(null, "", location.pathname + location.search); start(m[1]); } else showSignin("");
})();
