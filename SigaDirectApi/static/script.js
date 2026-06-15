"use strict";

const API = "/api/v1";
const POLL_MS = 3000;

const $ = (id) => document.getElementById(id);

// --- response log -----------------------------------------------------------
function jsonBlock(label, httpStatus, data) {
  const block = document.createElement("div");
  block.className = "json-block";

  const head = document.createElement("div");
  head.className = "json-label";
  const ts = new Date().toLocaleTimeString();
  head.textContent = `${ts} — ${label}`;
  if (httpStatus != null) {
    const code = document.createElement("span");
    code.className = "status-code";
    code.textContent = `  [${httpStatus}]`;
    head.appendChild(code);
  }

  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(data, null, 2);

  block.appendChild(head);
  block.appendChild(pre);
  return block;
}

function appendJson(label, httpStatus, data) {
  const out = $("json-output");
  out.appendChild(jsonBlock(label, httpStatus, data));
  out.scrollTop = out.scrollHeight;
}

// --- status + downloads -----------------------------------------------------
function setStatus(text, kind) {
  const el = $("status");
  el.textContent = text;
  el.className = "status " + kind;
}

function renderDownloads(files) {
  const box = $("downloads");
  box.innerHTML = "";
  for (const f of files) {
    const a = document.createElement("a");
    a.className = "download-btn";
    a.href = f.download_url;
    a.setAttribute("download", "");
    a.textContent = `⬇ Download ${f.type} — ${f.filename} (${fmtBytes(f.size_bytes)})`;
    box.appendChild(a);
  }
}

function fmtBytes(n) {
  if (n == null) return "?";
  const mb = n / (1024 * 1024);
  return mb >= 1 ? mb.toFixed(1) + " MB" : (n / 1024).toFixed(0) + " KB";
}

// --- flow -------------------------------------------------------------------
let pollTimer = null;

async function start() {
  clearInterval(pollTimer);
  $("downloads").innerHTML = "";
  $("json-output").innerHTML = "";
  $("start").disabled = true;
  setStatus("Starting…", "running");

  const raw = $("type").value.trim();
  const types = raw ? raw.split(",").map((s) => s.trim()).filter(Boolean) : ["xlsx"];
  const qs = types.map((t) => `type=${encodeURIComponent(t)}`).join("&");
  const url = `${API}/home/today?${qs}`;

  let res, data;
  try {
    res = await fetch(url);
    data = await res.json();
  } catch (e) {
    setStatus("Request failed: " + e.message, "error");
    $("start").disabled = false;
    return;
  }
  appendJson(`GET /home/today?${qs}`, res.status, data);

  if (!res.ok || !data.status_url) {
    setStatus("Failed to start job", "error");
    $("start").disabled = false;
    return;
  }

  setStatus("Downloading…", "running");
  pollTimer = setInterval(() => pollOnce(data.status_url), POLL_MS);
}

async function pollOnce(statusUrl) {
  let res, data;
  try {
    res = await fetch(statusUrl);
    data = await res.json();
  } catch (e) {
    clearInterval(pollTimer);
    appendJson("poll error", null, { error: e.message });
    setStatus("Polling stopped — " + e.message, "error");
    $("start").disabled = false;
    return;
  }
  appendJson(`GET ${statusUrl}`, res.status, data);

  if (!res.ok) {
    clearInterval(pollTimer);
    setStatus(`Polling stopped — HTTP ${res.status}`, "error");
    $("start").disabled = false;
    return;
  }

  if (data.status === "done") {
    clearInterval(pollTimer);
    $("start").disabled = false;
    if (data.files && data.files.length) {
      setStatus("Download is ready", "ready");
      renderDownloads(data.files);
    } else {
      setStatus(data.message || "Done — nothing to download", "ready");
    }
  } else if (data.status === "failed") {
    clearInterval(pollTimer);
    setStatus("Job failed — see log", "error");
    $("start").disabled = false;
  }
  // status === "running" → keep polling
}

// --- copies search ----------------------------------------------------------
async function copiesSearch() {
  const area = $("c-area").value.trim();
  if (!area) {
    $("json-output").innerHTML = "";
    appendJson("copies/search — not sent", null, { error: "area is required" });
    return;
  }

  const params = new URLSearchParams({ area });
  const optional = {
    gaceta: $("c-gaceta").value.trim(),
    fecha_desde: $("c-desde").value.trim(),
    fecha_hasta: $("c-hasta").value.trim(),
    recaptcha: $("c-recaptcha").value.trim(),
  };
  for (const [k, v] of Object.entries(optional)) {
    if (v) params.set(k, v);
  }

  $("json-output").innerHTML = "";
  const btn = $("c-search");
  btn.disabled = true;
  let res, data;
  try {
    res = await fetch(`${API}/copies/search?${params.toString()}`);
    data = await res.json();
  } catch (e) {
    appendJson("GET /copies/search", null, { error: e.message });
    btn.disabled = false;
    return;
  }
  appendJson(`GET /copies/search?${params.toString()}`, res.status, data);
  btn.disabled = false;
}

// --- records search ---------------------------------------------------------
async function recordsSearch() {
  const busqueda = $("r-busqueda").value.trim();
  if (!busqueda) {
    $("json-output").innerHTML = "";
    appendJson("records/search — not sent", null, { error: "busqueda is required" });
    return;
  }

  const params = new URLSearchParams({ busqueda });
  const area = $("r-area").value.trim();
  if (area) params.set("area", area);

  const gacetas = $("r-gacetas").value.trim();
  if (gacetas) {
    for (const g of gacetas.split(",").map((s) => s.trim()).filter(Boolean)) {
      params.append("gacetas", g);
    }
  }

  const desde = $("r-desde").value.trim();
  if (desde) params.set("fecha_desde", desde);
  const hasta = $("r-hasta").value.trim();
  if (hasta) params.set("fecha_hasta", hasta);
  const recaptcha = $("r-recaptcha").value.trim();
  if (recaptcha) params.set("recaptcha", recaptcha);

  $("json-output").innerHTML = "";
  const btn = $("r-search");
  btn.disabled = true;
  let res, data;
  try {
    res = await fetch(`${API}/records/search?${params.toString()}`);
    data = await res.json();
  } catch (e) {
    appendJson("GET /records/search", null, { error: e.message });
    btn.disabled = false;
    return;
  }
  appendJson(`GET /records/search?${params.toString()}`, res.status, data);
  btn.disabled = false;
}

// --- advanced search --------------------------------------------------------
function addDatoRow() {
  const row = document.createElement("div");
  row.className = "dato-row";
  row.innerHTML =
    '<input class="d-columna" type="text" placeholder="columna (e.g. CLASE)" />' +
    '<input class="d-operador" type="text" placeholder="operador (blank, AND/OR/NOT)" />' +
    '<input class="d-valor" type="text" placeholder="valor" />' +
    '<input class="d-fecha" type="text" placeholder="fecha YYYY-MM-DD" />' +
    '<button class="d-remove" type="button" title="Remove term">×</button>';
  row.querySelector(".d-remove").addEventListener("click", () => row.remove());
  $("a-datos").appendChild(row);
}

function collectDatos() {
  const datos = [];
  for (const row of document.querySelectorAll("#a-datos .dato-row")) {
    const columna = row.querySelector(".d-columna").value.trim();
    const operador = row.querySelector(".d-operador").value.trim();
    const valor = row.querySelector(".d-valor").value.trim();
    const fecha = row.querySelector(".d-fecha").value.trim();
    if (!columna && !operador && !valor && !fecha) continue; // skip blank rows
    datos.push({ columna, operador, valor, fecha: fecha || null });
  }
  return datos;
}

function splitInts(raw) {
  return raw.split(",").map((s) => s.trim()).filter(Boolean).map(Number);
}

async function advancedSearch() {
  const areaStr = $("a-area").value.trim();
  if (!areaStr) {
    $("json-output").innerHTML = "";
    appendJson("advanced/search — not sent", null, { error: "area is required" });
    return;
  }

  const fd = $("a-desde").value.trim();
  const fh = $("a-hasta").value.trim();
  const body = {
    area: Number(areaStr),
    datos: collectDatos(),
    gacetas: splitInts($("a-gacetas").value),
    secciones: splitInts($("a-secciones").value),
    fecha_desde: fd || null,
    fecha_hasta: fh || null,
    recaptcha: $("a-recaptcha").value.trim(),
  };

  $("json-output").innerHTML = "";
  appendJson("POST /advanced/search — request body", null, body);

  const btn = $("a-search");
  btn.disabled = true;
  let res, data;
  try {
    res = await fetch(`${API}/advanced/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    data = await res.json();
  } catch (e) {
    appendJson("POST /advanced/search", null, { error: e.message });
    btn.disabled = false;
    return;
  }
  appendJson("POST /advanced/search", res.status, data);
  btn.disabled = false;
}

// --- advanced helpers -------------------------------------------------------
async function runHelper(url, outId) {
  const el = $(outId);
  el.innerHTML = "";
  let res, data;
  try {
    res = await fetch(url);
    data = await res.json();
  } catch (e) {
    el.appendChild(jsonBlock("GET " + url, null, { error: e.message }));
    return;
  }
  el.appendChild(jsonBlock(`GET ${url}`, res.status, data));
}

// --- wiring -----------------------------------------------------------------
addDatoRow(); // start with one term row

$("start").addEventListener("click", start);
$("c-search").addEventListener("click", copiesSearch);
$("r-search").addEventListener("click", recordsSearch);
$("a-add").addEventListener("click", addDatoRow);
$("a-search").addEventListener("click", advancedSearch);
$("h-areas").addEventListener("click", () =>
  runHelper(`${API}/advanced/areas`, "h-areas-out")
);
$("h-gacetas").addEventListener("click", () => {
  const a = $("h-gacetas-area").value.trim();
  runHelper(`${API}/advanced/gacetas?area=${encodeURIComponent(a)}`, "h-gacetas-out");
});
$("h-secciones").addEventListener("click", () => {
  const g = $("h-secciones-gaceta").value.trim();
  runHelper(`${API}/advanced/secciones?gaceta=${encodeURIComponent(g)}`, "h-secciones-out");
});
