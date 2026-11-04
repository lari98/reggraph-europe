// RegGraph console. Plain JavaScript, no libraries, no inline script (strict CSP).
// Safety rules: every value is written with textContent / text nodes (never parsed as markup); the
// access token lives only in this closure (never in storage, cookies or the address bar - a
// #token=... fragment is read once and removed); every request goes to this same server only.
// Live updates: one small timer refreshes the data and checks for a new release, but only while the
// tab is visible and never faster than every few seconds (a hidden tab costs nothing).
"use strict";

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const S = { token: "", ccy: "EUR", asof: "", cache: new Map(), ents: null, labels: {}, offline: false,
  dash: null, nav: 0, lang: navigator.language || "en", locale: navigator.language || "en", ccyOk: true, build: "", version: "" };

// ---------------------------------------------------------------- texts (lang.js) and language
const TEXT = window.RG_I18N || { en: {} };
const baseOf = (tag) => String(tag || "en").split("-")[0].toLowerCase();
const own = (key) => (TEXT[baseOf(S.lang)] || {})[key];            // the chosen language only
function t(key, vars) {                                              // chosen language, else English
  let out = own(key);
  if (out === undefined) out = (TEXT.en || {})[key];
  if (out === undefined) return key;
  return vars ? out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : out;
}
function applyStrings() {
  document.documentElement.lang = S.lang;
  document.querySelectorAll("[data-i]").forEach((n) => { n.textContent = t(n.dataset.i); });
  document.querySelectorAll("[data-i-ph]").forEach((n) => n.setAttribute("placeholder", t(n.dataset.iPh)));
  document.querySelectorAll("[data-i-aria]").forEach((n) => n.setAttribute("aria-label", t(n.dataset.iAria)));
  document.querySelectorAll("[data-i-title]").forEach((n) => n.setAttribute("title", t(n.dataset.iTitle)));
}

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
const LABEL_KEY = { title: "nav.overview", registry: "nav.entities", exposure: "nav.exposure", insurance: "nav.insurance",
  stress: "nav.stress", rules: "nav.rules", connectors: "nav.data", unavailable: "unavail" };
const label = (key, fallback) => (LABEL_KEY[key] && own(LABEL_KEY[key])) || fallback;     // page's own English if untranslated
const word = (x) => own("w." + x) || cap(words(x));
const cap = (x) => { const w = String(x); return w ? w[0].toUpperCase() + w.slice(1) : w; };
const words = (t) => String(t).replaceAll("_", " ");
const enc = encodeURIComponent;
const typeClass = (t) => "nt-" + (["bank", "insurer", "reinsurer", "corporate", "holding"].includes(t) ? t : "other");
const typeIdx = { bank: 1, insurer: 2, reinsurer: 3, corporate: 4, holding: 5 };

// ---------------------------------------------------------------- api
async function api(path, opts) {
  const o = opts || {};
  const method = o.method || "GET";
  if (method === "GET" && S.cache.has(path)) return S.cache.get(path);
  const res = await fetch(path, { method, headers: { Authorization: "Bearer " + S.token, Accept: "application/json", "Accept-Language": S.lang },
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
  if (e.status === 401) return h("p", { class: "err", text: t("signin.expired") });
  return h("p", { class: "err", text: e.message || "Something went wrong." });
}
function cardHead(title, sub, actions) {
  return h("div", { class: "card-h" }, h("div", null, h("h2", { text: title }), sub ? h("p", { class: "card-sub", text: sub }) : null), actions || null);
}
function lazy(title, loader, build, o) {
  const opt = o || {};
  const body = h("div", { class: "card-body" }, skel());
  const card = h("section", { class: "card " + (opt.class || "") }, cardHead(title, opt.sub, opt.actions), body);
  loader().then((d) => body.replaceChildren(build(d))).catch((e) => body.replaceChildren(errMsg(e)));
  return card;
}
const card = (title, content, cls, actions, sub) => h("section", { class: "card " + (cls || "") },
  cardHead(title, sub, actions), h("div", { class: "card-body" }, content));
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
    const inner = [h("span", { class: "sw s" + ((i % 6) + 1) }), h("span", { class: "lg", text: word(p[0]) }), h("span", { class: "n", text: num(p[1]) })];
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
    pager.replaceChildren(h("span", { text: rows.length ? t("pg.of", { a: st.page * size + 1, b: Math.min(rows.length, (st.page + 1) * size), n: rows.length }) : t("pg.none") }),
      h("button", { type: "button", class: "btn", text: t("pg.prev"), disabled: st.page === 0, onclick: () => { st.page -= 1; draw(); } }),
      h("button", { type: "button", class: "btn", text: t("pg.next"), disabled: st.page >= pages - 1, onclick: () => { st.page += 1; draw(); } }));
    pager.hidden = rows.length <= size;                    // one page: no pager
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
    legend.append(h("label", null, h("input", { type: "checkbox", checked: true, onchange: (e) => svg.classList.toggle("hide-" + k, !e.target.checked) }), word(k)));
  }
  for (const t of ["bank", "insurer", "reinsurer", "corporate", "holding"]) {
    if (ids.some((id) => typeOf(id) === t)) legend.append(h("span", null, h("span", { class: "sw s" + typeIdx[t] }), word(t)));
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

const dashPath = () => "/v1/dashboard?currency=" + S.ccy + (S.asof ? "&as_of=" + S.asof : "");
function setDashMeta(d) {
  S.dash = d; S.labels = d.labels || {}; S.offline = !!d.offline; S.version = d.version || S.version;
  S.build = S.build || d.build || "";                       // the build this page was loaded with
  $("notice").textContent = S.labels["notice.decision_support"] || "";
  $("build").textContent = S.version ? "v" + S.version + " · " + (d.build || "").slice(0, 6) : "";
}
const ICON = {
  reg: "M4 21V5l8-2v18M12 9h8v12M7 9h2M7 13h2M7 17h2M15 13h2M15 17h2", exp: "M4 20V10M10 20V4M16 20v-8M22 20H2",
  ins: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z", str: "M3 12h4l3-8 4 16 3-8h4", rul: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7",
  alr: "M12 3a6 6 0 0 0-6 6v4l-2 3h16l-2-3V9a6 6 0 0 0-6-6zM10 19a2 2 0 0 0 4 0",
};
function kpiCard(cls, icon, title, value, sub, href, full) {
  return h("a", { class: "kpi kpi-ov " + cls, href },
    h("div", { class: "kpi-top" }, h("span", { class: "kpi-ic" }, s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("path", { d: icon }))), h("span", { class: "t", text: title })),
    h("div", { class: "v", text: value, title: full || value }), h("div", { class: "s", text: sub }));
}

// ---------------------------------------------------------------- Overview pictures (0.7.0)
// All drawn here as SVG: no map service, no tiles, no outside call, so they work offline and under the
// strict page policy. The Europe "map" is a tile grid: one square per country, roughly where it lies.
const GEO = { NO: [4, 0], SE: [5, 0], FI: [6, 0], EE: [7, 0], IS: [1, 1], DK: [4, 1], LV: [7, 1], IE: [1, 2], GB: [2, 2], NL: [3, 2], DE: [4, 2], PL: [5, 2], LT: [7, 2],
  FR: [2, 3], BE: [3, 3], LU: [4, 3], CZ: [5, 3], SK: [6, 3], PT: [0, 4], ES: [1, 4], CH: [3, 4], AT: [4, 4], SI: [5, 4], HU: [6, 4], RO: [7, 4],
  IT: [4, 5], HR: [5, 5], RS: [6, 5], BG: [7, 5], MT: [4, 6], GR: [7, 6], CY: [8, 6] };
const geoKey = (c) => (c === "UK" ? "GB" : c);
function geoMap(byJur) {
  const cnt = {}, raw = {};
  for (const [k, v] of Object.entries(byJur)) { cnt[geoKey(k)] = (cnt[geoKey(k)] || 0) + v; raw[geoKey(k)] = k; }
  const max = Math.max(1, ...Object.values(cnt));
  const extra = Object.keys(cnt).filter((k) => !GEO[k]).sort();
  const STEP = 50, W = 9 * STEP, H = (extra.length ? 8 : 7) * STEP;
  const svg = s("svg", { viewBox: "0 0 " + W + " " + H, class: "geomap", role: "group", "aria-label": t("c.map") });
  const place = (code, col, row) => {
    const n = cnt[code] || 0, r = n / max, x = col * STEP, y = row * STEP;
    const kids = [s("title", {}, code + ": " + t("map.tip", { n: num(n) })),
      s("rect", { x, y, width: 46, height: 46, rx: 8, class: "tile-r", "fill-opacity": n ? (0.2 + 0.8 * Math.pow(r, 0.6)).toFixed(2) : "0.08" }),
      s("text", { x: x + 23, y: y + 21, class: "tile-c" + (r > 0.55 ? " on" : "") }, code),
      s("text", { x: x + 23, y: y + 37, class: "tile-n" + (r > 0.55 ? " on" : "") }, n ? String(n) : "·")];
    svg.append(n ? s("a", { href: "#/entities?jur=" + enc(raw[code]), class: "tile" }, ...kids) : s("g", { class: "tile tile-0" }, ...kids));
  };
  for (const [code, [c, r]] of Object.entries(GEO)) place(code, c, r);
  extra.forEach((code, i) => place(code, i, 7));
  const scale = h("div", { class: "scale" }, h("span", { text: t("map.few") }), h("span", { class: "ramp" }), h("span", { text: t("map.many") }));
  return h("div", { class: "geo" }, svg, scale);
}

// Half-circle gauge: how concentrated is the exposure? Zones follow the usual HHI bands (1,500 / 2,500).
function gauge(hhi) {
  const MAXV = 5000, R = 78, cx = 100, cy = 98, v = Math.min(MAXV, hhi);
  const pt = (val, rad) => { const a = Math.PI * (1 - val / MAXV); return [cx + rad * Math.cos(a), cy - rad * Math.sin(a)]; };
  const arc = (a, b) => { const p = pt(a, R), q = pt(b, R); return "M" + p[0].toFixed(1) + " " + p[1].toFixed(1) + " A" + R + " " + R + " 0 0 1 " + q[0].toFixed(1) + " " + q[1].toFixed(1); };
  const level = hhi < 1500 ? "ok" : hhi < 2500 ? "warn" : "bad";
  const n = pt(v, R - 14);
  const svg = s("svg", { viewBox: "0 0 200 126", class: "gauge", role: "img", "aria-label": t("gauge.alt", { v: num(Math.round(hhi)), l: t("gauge." + level) }) },
    s("path", { d: arc(0, 1500), class: "gz gz-ok" }), s("path", { d: arc(1500, 2500), class: "gz gz-warn" }), s("path", { d: arc(2500, MAXV), class: "gz gz-bad" }),
    s("line", { x1: cx, y1: cy, x2: n[0].toFixed(1), y2: n[1].toFixed(1), class: "needle" }), s("circle", { cx, cy, r: 6, class: "hub" }),
    s("text", { x: cx, y: 120, class: "g-val" }, num(Math.round(hhi))));
  return h("div", { class: "gauge-wrap" }, svg,
    h("div", { class: "chips" }, chip(t("gauge." + level), level), h("span", { class: "muted", text: t("gauge.scale") })));
}

// Loss before and after insurance (or loss against capital): two thin bars per row, same scale.
function pairBars(rows, ka, kb, ca, cb) {
  const max = Math.max(1, ...rows.flatMap((r) => [r.a, r.b]));
  const key = h("div", { class: "pair-key" }, h("span", null, h("i", { class: "sw s" + ca }), ka), h("span", null, h("i", { class: "sw s" + cb }), kb));
  const ul = h("ul", { class: "pairs" });
  for (const r of rows) {
    const line = (label, val, text, c) => {
      const f = h("div", { class: "fill s" + c });
      requestAnimationFrame(() => { f.style.width = (val > 0 ? Math.max(2, (val / max) * 100) : 0).toFixed(1) + "%"; });
      return h("div", { class: "pr" }, h("span", { class: "pk", text: label }), h("div", { class: "track" }, f), h("span", { class: "pv", text }));
    };
    const inner = [h("div", { class: "row" }, h("span", { class: "lbl", text: r.name }), r.flag ? chip(r.flag, "bad") : null), line(ka, r.a, r.aText, ca), line(kb, r.b, r.bText, cb)];
    ul.append(h("li", { class: "pair-i" }, r.href ? h("a", { href: r.href, title: r.name }, inner) : h("div", { title: r.name }, inner)));
  }
  return h("div", null, key, ul);
}

// Country-to-country grid: row = country of the institution that is owed money, column = country of the
// one that owes it. Blue on the diagonal is money that stays at home, amber is money that crosses a border.
function crossGrid(edges) {
  const tot = {}, grid = {};
  for (const e of edges) {
    const a = (S.ents.get(e.holder_lei) || {}).jurisdiction, b = (S.ents.get(e.counterparty_lei) || {}).jurisdiction, v = Number(e.total) || 0;
    if (!a || !b) continue;
    grid[a + ">" + b] = (grid[a + ">" + b] || 0) + v; tot[a] = (tot[a] || 0) + v; tot[b] = (tot[b] || 0) + v;
  }
  const js = Object.keys(tot).sort((x, y) => tot[y] - tot[x]).slice(0, 6);
  if (!js.length) return h("p", { class: "empty", text: t("r.empty") });
  const L = 34, T = 22, CW = 60, CH = 30, max = Math.max(1, ...Object.values(grid));
  const svg = s("svg", { viewBox: "0 0 " + (L + js.length * CW) + " " + (T + js.length * CH), class: "xgrid", role: "img", "aria-label": t("c.mx") });
  js.forEach((c, i) => {
    svg.append(s("text", { x: L + i * CW + CW / 2, y: 15, class: "xg-h" }, c), s("text", { x: L - 6, y: T + i * CH + CH / 2 + 4, class: "xg-h xg-l" }, c));
  });
  js.forEach((a, r) => js.forEach((b, c) => {
    const v = grid[a + ">" + b] || 0;
    svg.append(s("rect", { x: L + c * CW + 1, y: T + r * CH + 1, width: CW - 2, height: CH - 2, rx: 5, class: a === b ? "xg-home" : "xg-cross", "fill-opacity": v ? (0.15 + 0.85 * Math.sqrt(v / max)).toFixed(2) : "0.06" }, s("title", {}, a + " → " + b + ": " + money(v))));
    if (v) svg.append(s("text", { x: L + c * CW + CW / 2, y: T + r * CH + CH / 2 + 4, class: "xg-v" + (v / max > 0.5 ? " on" : "") }, money(v)));
  }));
  return h("div", null, svg, h("div", { class: "pair-key" }, h("span", null, h("i", { class: "sw sw-home" }), t("mx.home")), h("span", null, h("i", { class: "sw sw-cross" }), t("mx.cross"))),
    h("p", { class: "muted small", text: t("mx.key") }));
}

// Overview: written for someone who has never seen the platform. Top to bottom: one sentence on how
// things stand, what needs attention, six headline numbers with a plain-language line each, a
// one-minute guide, then the pictures. Every box links to the detail behind it.
PAGES.overview = async () => {
  const dash = await api(dashPath());
  setDashMeta(dash);
  await entities().catch(() => null);
  const sec = dash.sections, ok = (k) => sec[k] && !sec[k].error;
  const breaches = ok("stress") ? (sec.stress.recent_runs || []).reduce((a, r) => a + (r.breaches || 0), 0) : 0;
  const alerts = ok("connectors") ? sec.connectors.open_alerts || 0 : 0;
  const review = ok("rules") ? sec.rules.open_review_tasks || 0 : 0;
  const top = ok("exposure") ? sec.exposure.top_counterparties || [] : [];
  const share = top.length ? Number(top[0].share_percent) : 0;

  const items = [];
  if (breaches) items.push({ tone: "bad", text: t("ov.a.breach", { n: breaches }), href: "#/stress" });
  if (alerts) items.push({ tone: "warn", text: t("ov.a.alerts", { n: alerts }), href: "#/data" });
  if (review) items.push({ tone: "warn", text: t("ov.a.review", { n: review }), href: "#/rules" });
  if (share >= 25) items.push({ tone: "warn", text: t("ov.a.conc", { name: top[0].name || top[0].lei, share: share.toFixed(1) }), href: "#/entity/" + enc(top[0].lei) + "/exposure" });
  const level = breaches ? "bad" : items.length ? "warn" : "ok";
  const edgesP = ok("exposure") ? api("/v1/exposures/edges?" + qs()) : Promise.resolve(null);
  const runs0 = ok("stress") ? sec.stress.recent_runs || [] : [];
  const runP = runs0.length ? api("/v1/stress/runs/" + runs0[0].run_id) : Promise.resolve(null);
  const hhi = top.reduce((a, r) => a + Number(r.share_percent) ** 2, 0);       // lower bound: top positions only
  const facts = ok("registry") && ok("exposure") && ok("insurance")
    ? t("ov.facts", { ents: num(sec.registry.entities), jur: Object.keys(sec.registry.by_jurisdiction).length, exp: money(sec.exposure.total), ins: num(sec.insurance.contracts) }) : "";

  const hero = h("section", { class: "hero hero-" + level, "aria-labelledby": "ov-h" },
    h("div", { class: "hero-main" },
      h("div", { class: "chips" }, h("span", { class: "status status-" + level }, h("span", { class: "dot" }), t("ov.h." + level)),
        chip(dash.view, "info"), chip(dash.as_of, "neutral"), chip(dash.currency, "neutral")),
      h("h1", { id: "ov-h", text: t("ov.title") }),
      h("p", { class: "hero-sentence", text: level === "ok" ? t("ov.s.ok") : t("ov.s." + level, { n: items.length }) }),
      facts ? h("p", { class: "muted hero-facts", text: facts }) : null,
      h("p", { class: "muted hero-sub", text: t("ov.sub") })),
    h("div", { class: "hero-attn" }, h("h2", { text: t("ov.attn") }),
      items.length ? h("ul", { class: "attn" }, items.map((i) => h("li", null, h("a", { class: "attn-i attn-" + i.tone, href: i.href },
        h("span", { class: "dot" }), h("span", { class: "attn-t", text: i.text }), h("span", { class: "go", "aria-hidden": "true", text: "→" })))))
        : h("p", { class: "attn-none" }, h("span", { class: "dot" }), t("ov.attn.none"))));

  const kp = h("div", { class: "kpis kpis-ov" });
  if (ok("registry")) kp.append(kpiCard("k1", ICON.reg, t("kpi.reg"), num(sec.registry.entities), t("kpi.reg.p", { a: sec.registry.active, l: sec.registry.ownership_links }), "#/entities"));
  if (ok("exposure")) kp.append(kpiCard("k2", ICON.exp, t("kpi.exp"), money(sec.exposure.total), t("kpi.exp.p", { p: sec.exposure.positions }), "#/exposure", money(sec.exposure.total, true)));
  if (ok("insurance")) kp.append(kpiCard("k3", ICON.ins, t("kpi.ins"), num(sec.insurance.contracts), t("kpi.ins.p"), "#/insurance"));
  if (ok("stress")) kp.append(kpiCard("k4", ICON.str, t("kpi.str"), num(sec.stress.runs), breaches ? t("kpi.str.bad", { n: breaches }) : t("kpi.str.ok"), "#/stress"));
  if (ok("rules")) kp.append(kpiCard("k5", ICON.rul, t("kpi.rul"), num((sec.rules.versions_by_status || {}).approved || 0), t("kpi.rul.p", { r: review }), "#/rules"));
  if (ok("connectors")) kp.append(kpiCard("k6", ICON.alr, t("kpi.alr"), num(alerts), t("kpi.alr.p"), "#/data"));
  kp.classList.add("n" + kp.children.length);

  // "What this means": a few sentences a non-specialist can read without any glossary.
  const analysis = card(t("c.an"), h("div", { class: "an-body" }), "an-card", null, t("c.an.s"));
  Promise.all([edgesP.catch(() => null), runP.catch(() => null)]).then(([ed, run]) => {
    const lines = [];
    if (top.length) lines.push({ tone: hhi >= 2500 ? "bad" : hhi >= 1500 ? "warn" : "ok", text: t("an.conc", { name: top[0].name || top[0].lei, share: share.toFixed(1), lvl: t("gauge." + (hhi < 1500 ? "ok" : hhi < 2500 ? "warn" : "bad")) }) });
    if (ed && ed.edges.length) {
      let all = 0, cross = 0;
      for (const e of ed.edges) {
        const a = (S.ents.get(e.holder_lei) || {}).jurisdiction, b = (S.ents.get(e.counterparty_lei) || {}).jurisdiction, v = Number(e.total) || 0;
        if (a && b) { all += v; if (a !== b) cross += v; }
      }
      if (all > 0) lines.push({ tone: cross / all > 0.5 ? "warn" : "ok", text: t("an.cross", { p: ((cross / all) * 100).toFixed(0) }) });
    }
    const tot = run && run.result ? run.result.totals : null;
    if (tot && Number(tot.gross_loss) > 0) lines.push({ tone: "ok", text: t("an.ins", { p: ((Number(tot.insurance_recovery) / Number(tot.gross_loss)) * 100).toFixed(0), scn: words(run.run.scenario_key) }) });
    if (tot) lines.push({ tone: tot.breaches ? "bad" : "ok", text: t(tot.breaches ? "an.buf.bad" : "an.buf.ok", { n: tot.breaches }) });
    if (!lines.length) lines.push({ tone: "ok", text: t("r.empty") });
    analysis.querySelector(".an-body").replaceChildren(h("ul", { class: "an" }, lines.map((l) => h("li", { class: "an-" + l.tone }, h("span", { class: "dot" }), h("span", { text: l.text })))));
  });

  const guide = h("details", { class: "guide card" }, h("summary", null, h("span", { class: "guide-ic", "aria-hidden": "true", text: "?" }), h("span", { text: t("guide.t") })),
    h("div", { class: "guide-grid" }, [1, 2, 3, 4, 5].map((i) => h("div", { class: "g" }, h("strong", { text: t("g." + i + "t") }), h("p", { text: t("g." + i + "d") })))),
    h("p", { class: "muted guide-note", text: t("guide.note") }));

  const g = h("div", { class: "grid" });
  if (ok("registry")) {
    g.append(card(t("c.types"), donut(Object.entries(sec.registry.by_type).sort((a, b) => b[1] - a[1]), sec.registry.entities, (x) => "#/entities?type=" + enc(x)), "span-4", null, t("c.types.s")));
    const j = Object.entries(sec.registry.by_jurisdiction).sort((a, b) => b[1] - a[1]);
    g.append(card(t("c.jur"), bars(j.slice(0, 6).map(([c, n], i) => ({ name: c, text: String(n), ratio: n / j[0][1], href: "#/entities?jur=" + enc(c), color: i }))), "span-4", null, t("c.jur.s")));
  }
  if (ok("insurance")) g.append(card(t("c.line"), donut(Object.entries(sec.insurance.by_line).sort((a, b) => b[1] - a[1]), sec.insurance.contracts, (x) => "#/insurance?line=" + enc(x)), "span-4", null, t("c.line.s")));
  if (ok("exposure")) {
    const m = Math.max(1, ...top.map((r) => Number(r.amount)));
    if (ok("registry")) g.append(card(t("c.map"), geoMap(sec.registry.by_jurisdiction), "span-7", null, t("c.map.s")));
    if (top.length) g.append(card(t("c.conc"), h("div", null, gauge(hhi), h("p", { class: "muted small", text: t("c.conc.d") })), "span-5", null, t("c.conc.s")));
    g.append(lazy(t("c.net"), () => edgesP, (d) => network({ nodes: Object.fromEntries([...new Set(d.edges.flatMap((e) => [e.holder_lei, e.counterparty_lei]))].map((x) => [x, 1])), edges: d.edges }, { circle: true }), { class: "span-8", sub: t("c.net.s") }));
    g.append(card(t("c.top"), bars(top.map((r, i) => ({ name: r.name || r.lei, text: money(r.amount) + " · " + Number(r.share_percent).toFixed(1) + "%", ratio: Number(r.amount) / m, href: "#/entity/" + enc(r.lei) + "/exposure", color: i }))), "span-4", null, t("c.top.s")));
  }
  if (ok("exposure")) g.append(lazy(t("c.mx"), () => edgesP, (d) => crossGrid(d.edges), { class: "span-7", sub: t("c.mx.s") }));
  if (ok("stress")) {
    const seen = new Set(), latest = [];
    for (const r of runs0) if (!seen.has(r.scenario_key)) { seen.add(r.scenario_key); latest.push(r); }
    g.append(card(t("c.gn"), pairBars(latest.slice(0, 5).map((r) => ({ name: words(r.scenario_key), a: Number(r.gross_loss), b: Number(r.net_loss), aText: money(r.gross_loss), bText: money(r.net_loss),
      flag: r.breaches > 0 ? t("st.breach", { n: r.breaches }) : "", href: "#/stress/" + r.run_id })), t("th.gross"), t("th.net"), 3, 2), "span-5", null, t("c.gn.s")));
  }
  if (ok("stress")) g.append(card(t("c.str"), table({ cols: [{ title: t("th.scn"), get: (r) => r.scenario_key, render: (r) => words(r.scenario_key) }, { title: t("th.gross"), num: true, get: (r) => r.gross_loss, render: (r) => money(r.gross_loss) },
    { title: t("th.net"), num: true, get: (r) => r.net_loss, render: (r) => money(r.net_loss) }, { title: t("th.status"), get: (r) => r.breaches, render: (r) => (r.breaches > 0 ? chip(t("st.breach", { n: r.breaches }), "bad") : chip(t("st.ok"), "ok")) }],
  rows: sec.stress.recent_runs, href: (r) => "#/stress/" + r.run_id, search: false, pageSize: 6 }), "span-7", null, t("c.str.s")));
  if (ok("rules")) {
    const by = sec.rules.versions_by_status || {};
    g.append(card(t("c.rul"), h("div", null, donut(Object.entries(by).sort((a, b) => b[1] - a[1]), Object.values(by).reduce((a, b) => a + b, 0)), h("div", { class: "chips" }, Object.entries(by).map(([k, n]) => chip(word(k) + " " + n, k === "approved" ? "ok" : "info")),
      chip(t("r.review", { n: review }), review ? "warn" : "ok"), ok("rates") ? chip(t("r.fx", { n: sec.rates.currencies, date: sec.rates.latest_date || "-" }), "info") : null,
      ok("connectors") ? chip(t("r.alerts", { n: alerts }), alerts ? "warn" : "ok") : null),
    h("p", { class: "muted links" }, h("a", { href: "#/rules", text: t("r.lib") }), "  |  ", h("a", { href: "#/data", text: t("r.data") }))), "span-5", null, t("c.rul.s")));
  }
  if (ok("stress") && runs0.length) g.append(lazy(t("c.buf"), () => runP, (d) => {
    const hs = d.result.holders.filter((x) => Number(x.capital_buffer) > 0).sort((a, b) => Number(b.net_loss_ratio) - Number(a.net_loss_ratio)).slice(0, 6);
    return hs.length ? pairBars(hs.map((x) => ({ name: nm(x.holder_lei), a: Number(x.net_loss), b: Number(x.capital_buffer), aText: moneyIn(x.net_loss, d.result.currency), bText: moneyIn(x.capital_buffer, d.result.currency),
      flag: x.breach ? t("st.breach", { n: 1 }) : "", href: "#/entity/" + enc(x.holder_lei) })), t("buf.loss"), t("buf.cap"), 4, 2) : h("p", { class: "empty", text: t("buf.none") });
  }, { class: "span-12", sub: t("c.buf.s") }));
  for (const k of dash.omitted) g.append(card(label(k, k), h("p", { class: "lock", text: t("unavail") }), "span-4"));
  return h("div", { class: "grid ov" }, h("div", { class: "span-12" }, hero), h("div", { class: "span-12" }, kp), h("div", { class: "span-12" }, analysis), h("div", { class: "span-12" }, guide), [...g.children]);
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
  $("nav").replaceChildren(...NAV.map(([id, key, fallback, d]) => h("a", { href: "#/" + id, "data-page": id }, s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("path", { d })), h("span", { text: t("nav." + id) }))));
}
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  return { parts: path.split("/").filter(Boolean).map(decodeURIComponent), q: new URLSearchParams(query || "") };
}
function errorPanel(e) {
  if (e.status === 401) { showSignin(t("signin.expired")); return h("div"); }
  return h("div", { class: "card" }, h("div", { class: "card-body" }, errMsg(e), h("p", null, h("button", { type: "button", class: "btn", text: "Try again", onclick: () => { S.cache.clear(); route(); } }))));
}
// soft = redraw in place after a live data change: no skeleton, no jump to the top, and the
// open/closed state of the guide is kept. A normal call (navigation) behaves as before.
async function route(soft) {
  if (!soft) closeDrawer();
  const { parts, q } = parseHash();
  const name = PAGES[parts[0]] ? parts[0] : "overview";
  document.querySelectorAll("#nav a").forEach((a) => (a.dataset.page === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  const main = $("main");
  const my = ++S.nav;
  const guideOpen = soft && main.querySelector(".guide") ? main.querySelector(".guide").open : false;
  const y = window.scrollY;
  if (!soft) main.replaceChildren(skel());
  try {
    const node = await PAGES[name](parts.slice(1), q);
    if (my !== S.nav) return;
    main.replaceChildren(node);
    const guide = main.querySelector(".guide");
    if (guide && guideOpen) guide.open = true;
  } catch (e) {
    if (my !== S.nav) return;
    if (soft) return;                                       // keep what is on screen; the next tick retries
    main.replaceChildren(errorPanel(e));
  }
  document.title = t("nav." + (name === "entity" ? "entities" : name === "overview" || NAV.some((n) => n[0] === name) ? name : "overview")) + " - RegGraph Europe";
  if (soft) window.scrollTo(0, y);
  else { main.focus({ preventScroll: true }); window.scrollTo(0, 0); }
  if (S.dash) $("status").textContent = [S.dash.view, S.dash.as_of, S.ccy].join(" | ");
}
function showSignin(msg) {
  S.token = ""; S.cache.clear(); S.ents = null; liveStop();
  $("app").hidden = true; $("signin").hidden = false;
  $("signin-error").textContent = msg || "";
  $("token").value = "";
  $("token").focus();
}
async function start(token) {
  S.token = token;
  try {
    const d = await api(dashPath());
    S.languages = d.languages || [];
    S.lang = matchLang(S.lang);                              // a tag the server supports (else English)
    setDashMeta(d);
  } catch (e) {
    showSignin(e.status === 401 ? t("signin.bad") : e.message);
    return;
  }
  $("signin").hidden = true; $("app").hidden = false;
  $("offline").hidden = !S.offline;
  $("who").textContent = S.dash.view;
  S.locale = S.lang;
  applyStrings();
  buildNav();
  initHeader();
  if (S.restore) { history.replaceState(null, "", location.pathname + location.search + "#" + S.restore); S.restore = ""; }
  if (!location.hash.startsWith("#/")) history.replaceState(null, "", location.pathname + location.search + "#/overview");
  route();
  liveStart();
}

// ---------------------------------------------------------------- global search (combobox)
function setupSearch() {
  const input = $("q"), list = $("q-list");
  let items = [], idx = -1;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); idx = -1; };
  const go = (lei) => { close(); input.value = ""; location.hash = "#/entity/" + enc(lei); };
  const mark = () => [...list.children].forEach((li, i) => li.setAttribute("aria-selected", i === idx ? "true" : "false"));
  input.addEventListener("input", async () => {
    const needle = input.value.trim().toLowerCase();
    if (!needle) { close(); return; }
    const ents = await entities().catch(() => new Map());
    items = [...ents.entries()].filter(([lei, e]) => e.legal_name.toLowerCase().includes(needle) || lei.toLowerCase().includes(needle)).slice(0, 8);
    list.replaceChildren(...items.map(([lei, e], i) => h("li", { role: "option", id: "q-" + i, "aria-selected": "false", onmousedown: (ev) => { ev.preventDefault(); go(lei); } },
      h("span", { text: e.legal_name }), h("span", { class: "muted", text: e.jurisdiction + " " + words(e.entity_type) }))));
    if (!items.length) list.replaceChildren(h("li", { class: "muted", text: t("search.none") }));
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
  const needle = (filter || "").toLowerCase();
  const list = $("ccy-list");
  const rows = CURRENCIES.filter((c) => !needle || c.code.toLowerCase().includes(needle) || c.name.toLowerCase().includes(needle));
  list.replaceChildren(...rows.map((c) => h("li", { role: "presentation" }, h("button", { type: "button", class: "opt", role: "option", "aria-selected": c.code === S.ccy ? "true" : "false",
    onclick: () => { pickCurrency(c.code); } }, h("span", { class: "sym", text: c.symbol }), h("span", { class: "code", text: c.code }), h("span", { class: "nm", text: c.name }), c.code === S.ccy ? h("span", { class: "tick", text: "\u2713" }) : null))));
  if (!rows.length) list.replaceChildren(h("li", { class: "pop-note", text: t("ccy.none") }));
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
  return [[t("date.today"), ""], [t("date.yday"), iso(day(1))], [t("date.lm"), iso(new Date(y, m, 0))], [t("date.lq"), iso(qEnd)], [t("date.ly"), iso(new Date(y - 1, 11, 31))]];
}
function dateLabel() {
  $("date-label").textContent = S.asof ? new Intl.DateTimeFormat(S.locale, { day: "numeric", month: "short", year: "numeric" }).format(new Date(S.asof + "T12:00:00")) : t("date.today");
}
function setAsOf(v) {
  S.asof = v; $("asof").value = v;
  dateLabel();
  datePop.close(true); S.cache.clear(); route();
}
const datePop = popover($("date-btn"), $("date-pop"), () => {
  $("date-presets").replaceChildren(...presets().map(([name, v]) => h("button", { type: "button", class: "opt", "aria-selected": v === S.asof ? "true" : "false", onclick: () => setAsOf(v) },
    h("span", { text: name }), h("span", { class: "nm", text: v || t("date.live") }), v === S.asof ? h("span", { class: "tick", text: "\u2713" }) : null)));
});
$("asof").max = iso(new Date());
$("asof").addEventListener("change", (e) => { if (e.target.value) setAsOf(e.target.value); });

function applyTheme(light) {
  document.documentElement.dataset.theme = light ? "light" : "dark";
  const sw = $("theme");
  sw.setAttribute("aria-checked", light ? "true" : "false");
  sw.setAttribute("aria-label", light ? t("theme.light") : t("theme.dark"));
}
$("theme").addEventListener("click", () => applyTheme($("theme").getAttribute("aria-checked") !== "true"));
applyTheme(!matchMedia("(prefers-color-scheme: dark)").matches);

popover($("user-btn"), $("user-pop"));
$("print").addEventListener("click", () => { POPS.forEach((p) => p.close(false)); window.print(); });
$("signout").addEventListener("click", () => { POPS.forEach((p) => p.close(false)); history.replaceState(null, "", location.pathname + location.search); showSignin(t("signin.out")); });
$("refresh").addEventListener("click", () => { S.cache.clear(); S.ents = null; route(); toast(t("reloaded")); });

// ---------------------------------------------------------------- language
// Picking a language changes text (where translated), number/date/currency formats and the legal
// notice (the server translates it from the Accept-Language header we send).
const matchLang = (tag) => {
  const list = S.languages || [];
  const hit = list.find((l) => l.tag.toLowerCase() === String(tag).toLowerCase()) || list.find((l) => baseOf(l.tag) === baseOf(tag) && !l.tag.includes("-"));
  return hit ? hit.tag : list.some((l) => l.tag === "en") ? "en" : S.lang;
};
const isFull = (tag) => Boolean(TEXT[baseOf(tag)]);
function drawLanguages(filter) {
  const q = (filter || "").toLowerCase();
  const rows = (S.languages || []).filter((l) => !q || l.name.toLowerCase().includes(q) || l.tag.toLowerCase().includes(q));
  $("lang-list").replaceChildren(...rows.map((l) => h("li", { role: "presentation" }, h("button", { type: "button", class: "opt", role: "option", lang: l.tag, "aria-selected": l.tag === S.lang ? "true" : "false",
    onclick: () => pickLang(l.tag) }, h("span", { class: "code", text: l.tag }), h("span", { class: "lname", text: l.name }),
  h("span", { class: "chip " + (isFull(l.tag) ? "chip-ok" : "chip-neutral"), text: t(isFull(l.tag) ? "lang.full" : "lang.part") }), l.tag === S.lang ? h("span", { class: "tick", text: "\u2713" }) : null))));
}
async function pickLang(tag) {
  S.lang = tag; S.locale = tag; langPop.close(true);
  $("lang-label").textContent = tag.toUpperCase();
  applyStrings(); buildNav(); dateLabel(); applyTheme($("theme").getAttribute("aria-checked") === "true"); userLine();
  S.cache.clear();
  route();
}
const langPop = popover($("lang-btn"), $("lang-pop"), () => { $("lang-q").value = ""; drawLanguages(""); $("lang-q").focus(); });
$("lang-q").addEventListener("input", (e) => drawLanguages(e.target.value));
$("lang-q").addEventListener("keydown", (e) => {
  const opts = [...$("lang-list").querySelectorAll("button")];
  if (e.key === "Enter" && opts.length) { e.preventDefault(); opts[0].click(); }
  else if (e.key === "ArrowDown" && opts.length) { e.preventDefault(); opts[0].focus(); }
});
$("lang-list").addEventListener("keydown", (e) => {
  const opts = [...$("lang-list").querySelectorAll("button")], i = opts.indexOf(document.activeElement);
  if (e.key === "ArrowDown") { e.preventDefault(); (opts[i + 1] || opts[0]).focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); (i <= 0 ? $("lang-q") : opts[i - 1]).focus(); }
});

function userLine() {
  const who = (S.dash && S.dash.view) || "user";
  $("user-name").textContent = t("user.in", { who }) + (S.offline ? t("user.off") : "");
}
function initHeader() {
  const who = (S.dash && S.dash.view) || "user";
  $("avatar-text").textContent = who.slice(0, 2).toUpperCase();
  $("lang-label").textContent = S.lang.toUpperCase();
  userLine(); dateLabel();
  loadCurrencies();
}

// ---------------------------------------------------------------- live updates
// One timer, three jobs: (1) ask /dashboard/version whether a new release is being served and, if so,
// reload the page (keeping token, route, language, currency, day and theme); (2) re-read the
// overview numbers and redraw the page in place when they changed; (3) show the state in the header.
// It pauses while the tab is hidden, backs off while the server is away and resumes by itself.
const LIVE = { on: true, timer: 0, fails: 0, sig: "", at: null, ms: Math.min(600000, Math.max(300, Number(window.__RG_LIVE_MS) || 30000)) };
function liveRender() {
  const b = $("live"), state = !LIVE.on ? "paused" : LIVE.fails ? "reconnect" : "on";
  b.dataset.state = state;
  b.setAttribute("aria-pressed", LIVE.on ? "true" : "false");
  $("live-label").textContent = state === "on" ? t("live.on") : state === "paused" ? t("live.paused") : t("live.reconnect");
  const when = LIVE.at ? LIVE.at.toLocaleTimeString(S.locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
  $("live-time").textContent = state === "on" && when ? t("live.upd", { time: when }) : "";
  b.title = state === "paused" ? t("live.tip.paused") : state === "reconnect" ? t("live.tip.reconnect") : t("live.tip", { s: Math.round(LIVE.ms / 1000) });
}
function liveStop() { clearTimeout(LIVE.timer); LIVE.timer = 0; }
function liveSchedule() {
  liveStop();
  if (!S.token) return;
  const wait = LIVE.fails ? Math.min(60000, 2000 * 2 ** Math.min(LIVE.fails, 5)) : LIVE.ms;
  LIVE.timer = setTimeout(liveTick, wait);
}
const busy = () => Boolean($("drawer-root").children.length) || /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName) && $("main").contains(document.activeElement);
function reloadApp() {
  const frag = new URLSearchParams({ token: S.token, r: location.hash.replace(/^#/, ""), l: S.lang, c: S.ccy, d: S.asof, th: $("theme").getAttribute("aria-checked") === "true" ? "light" : "dark" });
  history.replaceState(null, "", location.pathname + location.search + "#" + frag.toString());
  location.reload();
}
async function liveTick() {
  if (!S.token) return;
  if (!LIVE.on || document.hidden) { liveSchedule(); return; }
  try {
    const res = await fetch("/dashboard/version", { cache: "no-store", credentials: "omit" });
    if (!res.ok) throw new Error("version " + res.status);
    const v = await res.json();
    LIVE.fails = 0;
    if (S.build && v.build !== S.build) { toast(t("live.newver")); setTimeout(reloadApp, 1200); return; }
    S.cache.clear();
    const d = await api(dashPath());
    const sig = JSON.stringify([d.sections, d.omitted, d.as_of, d.currency]);
    if (LIVE.sig && sig !== LIVE.sig && !busy()) {
      setDashMeta(d); S.ents = null;
      await route(true);
      toast(t("live.changed"));
      LIVE.sig = sig;
    } else if (!LIVE.sig || sig === LIVE.sig) LIVE.sig = sig;   // changed but busy: keep the old one, retry next tick
    LIVE.at = new Date();
  } catch (e) {
    if (e.status === 401) { showSignin(t("signin.expired")); return; }
    LIVE.fails += 1;
  }
  liveRender();
  liveSchedule();
}
function liveStart() {
  LIVE.fails = 0; LIVE.sig = ""; LIVE.at = new Date();
  liveRender(); liveSchedule();
  api(dashPath()).then((d) => { LIVE.sig = JSON.stringify([d.sections, d.omitted, d.as_of, d.currency]); }).catch(() => null);
}
$("live").addEventListener("click", () => { LIVE.on = !LIVE.on; liveRender(); if (LIVE.on) { liveStop(); liveTick(); } });
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.token && LIVE.on) { liveStop(); liveTick(); } });
$("signin-form").addEventListener("submit", (e) => { e.preventDefault(); start($("token").value.trim()); });
window.addEventListener("hashchange", () => { if (S.token) route(); });
setupSearch();

// The fragment is "#token=T" (the printed link) or, after an automatic reload, "#token=T&r=route&l=lang&c=ccy&d=day&th=theme".
// Each part is checked against a strict pattern; anything else is ignored. It is removed from the address bar at once.
(function boot() {
  const raw = location.hash.replace(/^#/, "");
  const q = new URLSearchParams(raw);
  const token = q.get("token");
  applyStrings();
  if (!token || !/^[A-Za-z0-9._~-]{1,4096}$/.test(token) || !/^token=/.test(raw)) { showSignin(""); return; }
  const pick = (k, re) => { const v = q.get(k); return v && re.test(v) ? v : ""; };
  const lang = pick("l", /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$/), ccy = pick("c", /^[A-Z]{3}$/), day = pick("d", /^\d{4}-\d{2}-\d{2}$/);
  const route = pick("r", /^\/[A-Za-z0-9_\-./?=&%~]{0,300}$/), theme = pick("th", /^(light|dark)$/);
  if (lang) S.lang = lang;
  if (ccy) { S.ccy = ccy; $("ccy-label").textContent = ccy; }
  if (day && day <= iso(new Date())) { S.asof = day; $("asof").value = day; }
  if (theme) applyTheme(theme === "light");
  if (route) S.restore = route;
  history.replaceState(null, "", location.pathname + location.search);
  applyStrings();
  start(token);
})();
