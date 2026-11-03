// RegGraph dashboard page. Plain JavaScript, no libraries, no inline script (strict CSP).
// Safety rules: every value is written with textContent (never parsed as markup); the access token
// lives only in this closure and in the password field - never in storage, cookies or URLs.
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
};

function scalarRows(obj) {
  const dl = el("dl");
  for (const [key, value] of Object.entries(obj)) {
    if (value !== null && typeof value === "object") continue;
    dl.append(el("dt", key), el("dd", value === null ? "-" : value));
  }
  return dl;
}

function table(rows) {
  const t = el("table");
  if (!rows.length) return t;
  const cols = Object.keys(rows[0]);
  const head = el("tr");
  for (const c of cols) head.append(el("th", c));
  t.append(head);
  for (const row of rows) {
    const tr = el("tr");
    for (const c of cols) tr.append(el("td", row[c] === null || row[c] === undefined ? "-" : row[c]));
    t.append(tr);
  }
  return t;
}

function renderValue(parent, value) {
  if (value.error) { parent.append(el("p", value.error, "err")); return; }
  parent.append(scalarRows(value));
  for (const [key, inner] of Object.entries(value)) {
    if (inner === null || typeof inner !== "object") continue;
    parent.append(el("h3", key));
    if (Array.isArray(inner)) {
      parent.append(table(inner.filter((r) => r && typeof r === "object")));
    } else {
      parent.append(scalarRows(inner.constructor === Object ? flatten(inner) : {}));
    }
  }
}

function flatten(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== null && typeof v === "object") {
      for (const [k2, v2] of Object.entries(v)) out[k + "." + k2] = v2;
    } else out[k] = v;
  }
  return out;
}

function render(data) {
  const labels = data.labels || {};
  $("title").textContent = labels["dashboard.title"] || "RegGraph Europe";
  $("l-token").textContent = labels["dashboard.token"] || "Access token";
  $("load").textContent = labels["dashboard.load"] || "Load";
  const board = $("board");
  board.replaceChildren();
  for (const [name, value] of Object.entries(data.sections)) {
    const card = el("section");
    card.append(el("h2", labels["dashboard." + name] || name));
    renderValue(card, value);
    board.append(card);
  }
  for (const name of data.omitted) {
    const card = el("section", null, "omitted");
    card.append(el("h2", labels["dashboard." + name] || name));
    card.append(el("p", labels["dashboard.unavailable"] || "Not available for your role."));
    board.append(card);
  }
  $("status").textContent = data.view + " | " + data.as_of + " | " + data.currency;
}

async function load(event) {
  event.preventDefault();
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
