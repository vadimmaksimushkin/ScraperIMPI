'use strict';

const API = '/api/v1';
const POLL_MS = 3000;

const $ = (id) => document.getElementById(id);

function jsonBlock(label, httpStatus, data) {
  const block = document.createElement('div');
  block.className = 'json-block';

  const head = document.createElement('div');
  head.className = 'json-label';
  const ts = new Date().toLocaleTimeString();
  head.textContent = `${ts} — ${label}`;
  if (httpStatus != null) {
    const code = document.createElement('span');
    code.className = 'status-code';
    code.textContent = `  [${httpStatus}]`;
    head.appendChild(code);
  }

  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(data, null, 2);

  block.appendChild(head);
  block.appendChild(pre);
  return block;
}

function appendJson(label, httpStatus, data) {
  const out = $('json-output');
  out.appendChild(jsonBlock(label, httpStatus, data));
  out.scrollTop = out.scrollHeight;
}

function setStatus(text, kind) {
  const el = $('status');
  el.textContent = text;
  el.className = 'status ' + kind;
}

function renderDownloads(files) {
  const box = $('downloads');
  box.innerHTML = '';
  for (const f of files) {
    const a = document.createElement('a');
    a.className = 'download-btn';
    a.href = f.download_url;
    a.setAttribute('download', '');
    a.textContent =
        `⬇ Download ${f.type} — ${f.filename} (${fmtBytes(f.size_bytes)})`;
    box.appendChild(a);
  }
}

function fmtBytes(n) {
  if (n == null) return '?';
  const mb = n / (1024 * 1024);
  return mb >= 1 ? mb.toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB';
}

let pollTimer = null;

async function start() {
  clearInterval(pollTimer);
  $('downloads').innerHTML = '';
  $('json-output').innerHTML = '';
  $('start').disabled = true;
  setStatus('Starting…', 'running');

  const raw = $('type').value.trim();
  const types =
      raw ? raw.split(',').map((s) => s.trim()).filter(Boolean) : ['xlsx'];
  const qs = types.map((t) => `type=${encodeURIComponent(t)}`).join('&');
  const url = `${API}/home/today?${qs}`;

  let res, data;
  try {
    res = await fetch(url);
    data = await res.json();
  } catch (e) {
    setStatus('Request failed: ' + e.message, 'error');
    $('start').disabled = false;
    return;
  }
  appendJson(`GET /home/today?${qs}`, res.status, data);

  if (!res.ok || !data.status_url) {
    setStatus('Failed to start job', 'error');
    $('start').disabled = false;
    return;
  }

  setStatus('Downloading…', 'running');
  pollTimer = setInterval(() => pollOnce(data.status_url), POLL_MS);
}

async function pollOnce(statusUrl) {
  let res, data;
  try {
    res = await fetch(statusUrl);
    data = await res.json();
  } catch (e) {
    clearInterval(pollTimer);
    appendJson('poll error', null, {error: e.message});
    setStatus('Polling stopped — ' + e.message, 'error');
    $('start').disabled = false;
    return;
  }
  appendJson(`GET ${statusUrl}`, res.status, data);

  if (!res.ok) {
    clearInterval(pollTimer);
    setStatus(`Polling stopped — HTTP ${res.status}`, 'error');
    $('start').disabled = false;
    return;
  }

  if (data.status === 'done') {
    clearInterval(pollTimer);
    $('start').disabled = false;
    if (data.files?.length) {
      setStatus('Download is ready', 'ready');
      renderDownloads(data.files);
    } else {
      setStatus(data.message || 'Done — nothing to download', 'ready');
    }
  } else if (data.status === 'failed') {
    clearInterval(pollTimer);
    setStatus('Job failed — see log', 'error');
    $('start').disabled = false;
  }
  // status === "running" → keep polling
}

async function copiesSearch() {
  const area = singleVal('c-area', 'c-area-sel');
  if (!area) {
    $('json-output').innerHTML = '';
    appendJson('copies/search — not sent', null, {error: 'area is required'});
    return;
  }

  const params = new URLSearchParams({area});
  const optional = {
    gaceta: singleVal('c-gaceta', 'c-gaceta-sel'),
    fecha_desde: $('c-desde').value.trim(),
    fecha_hasta: $('c-hasta').value.trim(),
    recaptcha: $('c-recaptcha').value.trim(),
  };
  for (const [k, v] of Object.entries(optional)) {
    if (v) params.set(k, v);
  }

  $('json-output').innerHTML = '';
  const btn = $('c-search');
  btn.disabled = true;
  let res, data;
  try {
    res = await fetch(`${API}/copies/search?${params.toString()}`);
    data = await res.json();
  } catch (e) {
    appendJson('GET /copies/search', null, {error: e.message});
    btn.disabled = false;
    return;
  }
  appendJson(`GET /copies/search?${params.toString()}`, res.status, data);
  btn.disabled = false;
}

async function recordsSearch() {
  const busqueda = $('r-busqueda').value.trim();
  if (!busqueda) {
    $('json-output').innerHTML = '';
    appendJson(
        'records/search — not sent', null, {error: 'busqueda is required'});
    return;
  }

  const params = new URLSearchParams({busqueda});
  const area = singleVal('r-area', 'r-area-sel');
  if (area) params.set('area', area);

  for (const g of multiVals('r-gacetas', 'r-gacetas-sel')) {
    params.append('gacetas', g);
  }

  const desde = $('r-desde').value.trim();
  if (desde) params.set('fecha_desde', desde);
  const hasta = $('r-hasta').value.trim();
  if (hasta) params.set('fecha_hasta', hasta);
  const recaptcha = $('r-recaptcha').value.trim();
  if (recaptcha) params.set('recaptcha', recaptcha);

  $('json-output').innerHTML = '';
  const btn = $('r-search');
  btn.disabled = true;
  let res, data;
  try {
    res = await fetch(`${API}/records/search?${params.toString()}`);
    data = await res.json();
  } catch (e) {
    appendJson('GET /records/search', null, {error: e.message});
    btn.disabled = false;
    return;
  }
  appendJson(`GET /records/search?${params.toString()}`, res.status, data);
  btn.disabled = false;
}

function addDatoRow() {
  const row = document.createElement('div');
  row.className = 'dato-row';
  row.innerHTML =
      '<input class="d-columna raw-only" type="text" placeholder="columna (e.g. CLASE)" />' +
      '<select class="d-columna-sel human-only" hidden></select>' +
      '<input class="d-operador" type="text" placeholder="operador (blank, AND/OR/NOT)" />' +
      '<input class="d-valor" type="text" placeholder="valor" />' +
      '<input class="d-fecha" type="text" placeholder="fecha YYYY-MM-DD" />' +
      '<button class="d-remove" type="button" title="Remove Dato">×</button>';
  row.querySelector('.d-remove').addEventListener('click', () => row.remove());
  $('a-datos').appendChild(row);
  applyMode(isHuman());  // show the right columna control on this fresh row
  if (isHuman()) runCascade(refreshColumnaOptions);
}

function collectDatos() {
  const datos = [];
  for (const row of document.querySelectorAll('#a-datos .dato-row')) {
    const columna = isHuman() ? row.querySelector('.d-columna-sel').value :
                                row.querySelector('.d-columna').value.trim();
    const operador = row.querySelector('.d-operador').value.trim();
    const valor = row.querySelector('.d-valor').value.trim();
    const fecha = row.querySelector('.d-fecha').value.trim();
    if (!columna && !operador && !valor && !fecha) continue;  // skip blank rows
    datos.push({columna, operador, valor, fecha: fecha || null});
  }
  return datos;
}

async function advancedSearch() {
  const areaStr = singleVal('a-area', 'a-area-sel');
  if (!areaStr) {
    $('json-output').innerHTML = '';
    appendJson('advanced/search — not sent', null, {error: 'area is required'});
    return;
  }

  const fd = $('a-desde').value.trim();
  const fh = $('a-hasta').value.trim();
  const body = {
    area: Number(areaStr),
    datos: collectDatos(),
    gacetas: multiVals('a-gacetas', 'a-gacetas-sel').map(Number),
    secciones: multiVals('a-secciones', 'a-secciones-sel').map(Number),
    fecha_desde: fd || null,
    fecha_hasta: fh || null,
    recaptcha: $('a-recaptcha').value.trim(),
  };

  $('json-output').innerHTML = '';
  appendJson('POST /advanced/search — request body', null, body);

  const btn = $('a-search');
  btn.disabled = true;
  let res, data;
  try {
    res = await fetch(`${API}/advanced/search`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    data = await res.json();
  } catch (e) {
    appendJson('POST /advanced/search', null, {error: e.message});
    btn.disabled = false;
    return;
  }
  appendJson('POST /advanced/search', res.status, data);
  btn.disabled = false;
}

async function runHelper(url, outId) {
  const el = $(outId);
  el.innerHTML = '';
  let res, data;
  try {
    res = await fetch(url);
    data = await res.json();
  } catch (e) {
    el.appendChild(jsonBlock('GET ' + url, null, {error: e.message}));
    return;
  }
  el.appendChild(jsonBlock(`GET ${url}`, res.status, data));
}

function isHuman() {
  return $('mode-toggle').checked;
}

// Toggle raw ⇄ human visibility with the native `hidden` attribute — no CSS.
function applyMode(human) {
  document.querySelectorAll('.raw-only').forEach((el) => (el.hidden = human));
  document.querySelectorAll('.human-only')
      .forEach((el) => (el.hidden = !human));
}

function getSelected(sel) {
  return Array.from(sel.selectedOptions)
      .map((o) => o.value)
      .filter((v) => v !== '');
}

// Read a value as raw text or, in human mode, from the paired <select>.
function singleVal(rawId, selId) {
  return isHuman() ? getSelected($(selId))[0] || '' : $(rawId).value.trim();
}

function multiVals(rawId, selId) {
  return isHuman() ? getSelected($(selId)) : splitCsv($(rawId).value);
}

// Helper GETs are cached by URL — the same selection never refetches.
const helperCache = new Map();
async function getJSON(url) {
  if (helperCache.has(url)) return helperCache.get(url);
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  helperCache.set(url, data);
  return data;
}

function qs(pairs) {
  const parts = [];
  for (const [key, vals] of pairs) {
    for (const v of vals) parts.push(`${key}=${encodeURIComponent(v)}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

// Split a comma-separated input into a trimmed, blank-free list.
const splitCsv = (s) => s.split(',').map((v) => v.trim()).filter(Boolean);

const asOptions = (rows) => rows.map((r) => ({value: r.id, label: r.name}));

// Rebuild a <select>, re-selecting any prior choices whose value survived.
// That survival is the R3 prune: values no longer offered simply drop.
function fillSelect(sel, options, multi, placeholder) {
  const keep = new Set(getSelected(sel));
  sel.innerHTML = '';
  if (!multi) {
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = placeholder || '— any —';
    sel.appendChild(blank);
  }
  for (const o of options) {
    const opt = document.createElement('option');
    opt.value = String(o.value);
    opt.textContent = o.label;
    if (keep.has(opt.value)) opt.selected = true;
    sel.appendChild(opt);
  }
}

// Native <select multiple> needs ctrl-click and wipes the selection on a plain
// click. Make a plain click toggle the option instead, leaving the rest alone.
function enableClickToggle(sel) {
  sel.addEventListener('mousedown', (e) => {
    if (e.target.tagName !== 'OPTION') return;
    e.preventDefault();
    e.target.selected = !e.target.selected;
    sel.dispatchEvent(new Event('change', {bubbles: true}));
  });
}

async function copiesCascade() {
  const areas = getSelected($('c-area-sel'));
  const gacetas =
      await getJSON(`${API}/advanced/gacetas${qs([['area', areas]])}`);
  fillSelect($('c-gaceta-sel'), asOptions(gacetas), false);
}

async function recordsCascade() {
  const areas = getSelected($('r-area-sel'));
  const gacetas =
      await getJSON(`${API}/advanced/gacetas${qs([['area', areas]])}`);
  fillSelect($('r-gacetas-sel'), asOptions(gacetas), true);
}

async function advancedFromArea() {
  const areas = getSelected($('a-area-sel'));
  const gacetas =
      await getJSON(`${API}/advanced/gacetas${qs([['area', areas]])}`);
  fillSelect($('a-gacetas-sel'), asOptions(gacetas), true);
  await advancedFromGacetas();
}

async function advancedFromGacetas() {
  const gacetas = getSelected($('a-gacetas-sel'));
  const secciones =
      await getJSON(`${API}/advanced/secciones${qs([['gaceta', gacetas]])}`);
  fillSelect($('a-secciones-sel'), asOptions(secciones), true);
  await refreshColumnaOptions();
}

async function refreshColumnaOptions() {
  const gacetas = getSelected($('a-gacetas-sel'));
  const secciones = getSelected($('a-secciones-sel'));
  const url = `${API}/advanced/columnas${qs([
    ['gaceta', gacetas],
    ['seccion', secciones],
  ])}`;
  const cols = await getJSON(url);
  const opts = cols.map((c) => ({value: c.name, label: c.label}));
  for (const sel of document.querySelectorAll('#a-datos .d-columna-sel')) {
    fillSelect(sel, opts, false, '— columna —');
  }
}

async function runCascade(fn) {
  try {
    await fn();
  } catch (e) {
    appendJson('human-mode load failed', null, {error: e.message});
  }
}

let areasLoaded = false;
async function enterHumanMode() {
  if (!areasLoaded) {
    const opts = asOptions(await getJSON(`${API}/advanced/areas`));
    fillSelect($('c-area-sel'), opts, false);
    fillSelect($('r-area-sel'), opts, false);
    fillSelect($('a-area-sel'), opts, false);
    areasLoaded = true;
  }
  await Promise.all([copiesCascade(), recordsCascade(), advancedFromArea()]);
}

addDatoRow();  // start with one Dato row

$('start').addEventListener('click', start);
$('c-search').addEventListener('click', copiesSearch);
$('r-search').addEventListener('click', recordsSearch);
$('a-add').addEventListener('click', addDatoRow);
$('a-search').addEventListener('click', advancedSearch);
$('h-areas').addEventListener(
    'click', () => runHelper(`${API}/advanced/areas`, 'h-areas-out'));
$('h-gacetas').addEventListener('click', () => {
  const areas = splitCsv($('h-gacetas-area').value);
  runHelper(`${API}/advanced/gacetas${qs([['area', areas]])}`, 'h-gacetas-out');
});
$('h-secciones').addEventListener('click', () => {
  const gacetas = splitCsv($('h-secciones-gaceta').value);
  runHelper(
      `${API}/advanced/secciones${qs([['gaceta', gacetas]])}`,
      'h-secciones-out');
});

$('mode-toggle').addEventListener('change', (e) => {
  applyMode(e.target.checked);
  if (e.target.checked) runCascade(enterHumanMode);
});
$('c-area-sel').addEventListener('change', () => runCascade(copiesCascade));
$('r-area-sel').addEventListener('change', () => runCascade(recordsCascade));
$('a-area-sel').addEventListener('change', () => runCascade(advancedFromArea));
$('a-gacetas-sel')
    .addEventListener('change', () => runCascade(advancedFromGacetas));
$('a-secciones-sel')
    .addEventListener('change', () => runCascade(refreshColumnaOptions));
['r-gacetas-sel', 'a-gacetas-sel', 'a-secciones-sel'].forEach(
    (id) => enableClickToggle($(id)));

for (const btn of document.querySelectorAll('.section-toggle')) {
  btn.addEventListener('click', () => {
    const body = btn.closest('section').querySelector('.section-body');
    body.hidden = !body.hidden;
    btn.textContent = body.hidden ? 'Show' : 'Hide';
  });
}

// Human mode is the default — apply it and load its selects on first paint.
applyMode(isHuman());
if (isHuman()) runCascade(enterHumanMode);
