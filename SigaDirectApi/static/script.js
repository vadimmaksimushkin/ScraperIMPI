'use strict';

const API = '/api/v1';
const POLL_MS = 3000;

// ── Virtual-scroll tunables (uniform card height) ────────────────────────────
// Only a window of cards is ever in the DOM, so the result set can be huge while
// the DOM stays tiny. Tune these freely:
//   CARD_HEIGHT — fixed px height of every result card (the scroll stride).
//   VIEWPORT_H  — px height of the scroll window (≈ VIEWPORT_H/CARD_HEIGHT shown).
//   OVERSCAN    — extra cards rendered above+below the viewport as a buffer, so
//                 scrolling reveals already-rendered rows. Rendered count ≈
//                 VIEWPORT_H/CARD_HEIGHT + 2*OVERSCAN.
const CARD_HEIGHT = 180;
const VIEWPORT_H = 720;
const OVERSCAN = 200;
// Browsers cap element/scroll height (Chrome/Safari ≈ 33.5M px = 2^25, Firefox
// ≈ 17.9M). Past that the scrollbar can't reach the lower rows, so we cap the
// sizer here and map scroll→row proportionally beyond it (scrolling gets coarser
// but every row stays reachable). Below this many px it's pixel-perfect.
// (33.5M/180 ≈ 186k rows exact; lower this on Firefox.)
const MAX_SCROLL_PX = 33000000;
// Max ids per bulk export. Conservative client guard — a fichas PDF is ~110 KB
// per ficha, so 1000 ≈ a ~125 MB file. The real upstream ceiling gets probed
// separately; raise/lower this once it's known.
const MAX_EXPORT = 1000;
// Copies are enormous (a single ejemplar zip is ~160 MB), so a much lower cap —
// 50 mirrors the SIGA website's per-page selection limit.
const MAX_EXPORT_COPIES = 50;
// Per-ficha images are fetched one ficha at a time (a whole-search sweep would be
// thousands of /images calls), kept in a small LRU so paging back doesn't refetch.
const IMAGE_CACHE_MAX = 200;
const imageCache = new Map();     // fichaId -> [base64,...]  (insertion order = LRU)
const imageInflight = new Map();  // fichaId -> Promise, dedups concurrent loads

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  // Guard the main thread: a 15k-record dump pretty-prints to tens of MB and
  // freezes the page. Truncate anything huge (the table is the real result view).
  const text = JSON.stringify(data, null, 2);
  const MAX = 200000;
  pre.textContent = text.length > MAX
      ? text.slice(0, MAX) +
          `\n… (truncated, ${text.length.toLocaleString()} chars total)`
      : text;

  block.appendChild(head);
  block.appendChild(pre);
  return block;
}

// Fetch + parse without ever throwing on a non-JSON body. An upstream timeout or
// crash can return an HTML/text error page; we surface that as {error, body}
// rather than letting `res.json()` throw a cryptic "Unexpected token" message.
async function fetchJson(url, opts) {
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    return {ok: false, status: 0, data: {error: e.message}};
  }
  const body = await res.text();
  let data;
  try {
    data = body ? JSON.parse(body) : null;
  } catch {
    data = {error: `HTTP ${res.status} ${res.statusText}`.trim(), body: body.slice(0, 300)};
  }
  return {ok: res.ok, status: res.status, data};
}

// Pull the server's filename out of a Content-Disposition header (RFC 5987
// filename*=... wins over a plain filename=).
function filenameFromDisposition(cd) {
  if (!cd) return null;
  const star = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(cd);
  if (star) { try { return decodeURIComponent(star[1].trim()); } catch { /* fall through */ } }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(cd);
  return plain ? plain[1].trim() : null;
}

// Save a Blob to disk via a transient object URL.
function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Export one or more fichas via POST /export/fichas, which streams the file back
// (no job/poll). Read it as a blob and save it. Used for both per-card (one id)
// and bulk (the selection) export. Throws with the server's detail on failure.
async function exportFichas(ids, format) {
  const res = await fetch(`${API}/export/fichas`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({format, id: ids}),
  });
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { detail = `HTTP ${res.status}`; }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const name = filenameFromDisposition(res.headers.get('Content-Disposition')) ||
      `fichas.${format}`;
  triggerDownload(blob, name);
}

// Copies export is a background job (the file can be GBs): POST kicks it off and
// returns a poll URL; we poll until ready, then surface download link(s). Unlike
// fichas, we don't auto-download — the user clicks (a 160 MB+ file each).
async function pollExportJob(statusUrl) {
  while (true) {
    const {ok, data} = await fetchJson(statusUrl);
    if (!ok) throw new Error(data?.detail || data?.error || 'poll failed');
    if (data.status === 'done') return data.files || [];
    if (data.status === 'failed') {
      throw new Error(data.errors?.[0]?.error || 'export job failed');
    }
    await sleep(POLL_MS);  // status === 'running'
  }
}

function renderCopyDownloads(files) {
  const box = $('c-downloads');
  if (!files.length) {
    const note = document.createElement('div');
    note.className = 'hint';
    note.textContent = 'export produced no file';
    box.appendChild(note);
    return;
  }
  for (const f of files) {
    const a = document.createElement('a');
    a.className = 'download-btn';
    a.href = f.download_url;
    a.setAttribute('download', '');
    a.textContent = `⬇ ${(f.type || '').toUpperCase()} — ${f.filename} (${fmtBytes(f.size_bytes)})`;
    box.appendChild(a);
  }
}

async function exportCopies(ids, format) {
  const {ok, status, data} = await fetchJson(`${API}/export/copies`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({format, id: ids}),
  });
  if (!ok || !data?.status_url) {
    throw new Error(data?.detail || `HTTP ${status}`);
  }
  const files = await pollExportJob(data.status_url);
  renderCopyDownloads(files);
  return files;
}

// SIGA returns raw base64 (usually GIF); sniff the magic bytes for the mime so a
// PNG/JPEG also renders, and pass through anything already a data: URI.
function imageDataUri(s) {
  if (typeof s !== 'string') return '';
  if (s.startsWith('data:')) return s;
  let mime = 'image/gif';
  if (s.startsWith('iVBOR')) mime = 'image/png';
  else if (s.startsWith('/9j/')) mime = 'image/jpeg';
  return `data:${mime};base64,${s}`;
}

function cachePutImages(id, imgs) {
  imageCache.delete(id);
  imageCache.set(id, imgs);
  while (imageCache.size > IMAGE_CACHE_MAX) {
    imageCache.delete(imageCache.keys().next().value);  // evict least-recent
  }
}

function touchImages(id) {
  const v = imageCache.get(id);
  imageCache.delete(id);
  imageCache.set(id, v);
  return v;
}

// One /images call per ficha, cached + in-flight-deduped, so a re-render or a
// scroll-back never refetches.
async function fetchImage(fichaId) {
  if (imageCache.has(fichaId)) return touchImages(fichaId);
  if (imageInflight.has(fichaId)) return imageInflight.get(fichaId);
  const p = (async () => {
    const {ok, data} = await fetchJson(`${API}/images/${fichaId}`);
    const imgs = ok && Array.isArray(data?.data?.imagenBase64) ? data.data.imagenBase64 : [];
    cachePutImages(fichaId, imgs);
    imageInflight.delete(fichaId);
    return imgs;
  })();
  imageInflight.set(fichaId, p);
  return p;
}

function showImages(box, imgs) {
  box.replaceChildren();
  box.classList.remove('loading');
  if (!imgs || !imgs.length) { box.classList.add('empty'); box.textContent = 'no image'; return; }
  box.classList.remove('empty');
  for (const b of imgs) {
    const img = document.createElement('img');
    img.className = 'rc-img-el';
    img.loading = 'lazy';
    img.src = imageDataUri(b);
    box.appendChild(img);
  }
}

// Decide a ficha image slot's contents: cached image → show it; auto-load + in
// the viewport → fetch now; otherwise a 🖼 button to load on demand. Only the
// truly-visible cards auto-fetch, so a scroll never fires the whole render window.
function renderImageArea(box, fichaId, inViewport, autoLoad) {
  if (imageCache.has(fichaId)) { showImages(box, touchImages(fichaId)); return; }
  const load = () => {
    box.replaceChildren();
    box.classList.add('loading');
    box.textContent = '…';
    fetchImage(fichaId).then((imgs) => { if (box.isConnected) showImages(box, imgs); });
  };
  if (autoLoad && inViewport) { load(); return; }
  box.replaceChildren();
  box.classList.remove('loading');
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn-secondary btn-mini rc-img-btn';
  btn.textContent = '🖼';
  btn.title = 'Load image';
  btn.addEventListener('click', load);
  box.appendChild(btn);
}

// In human mode the table is the result surface, so the log only takes a compact
// line (the full payload is what froze the page). Raw mode logs the full body.
function logSearch(label, status, data, ok) {
  if (!isHuman()) {
    appendJson(label, status, data);
    return;
  }
  const summary = ok
      ? (Array.isArray(data?.data) ? {results: data.data.length, status} : {status})
      : {status, error: data?.error || data?.detail || `HTTP ${status}`, body: data?.body};
  appendJson(label, status, summary);
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

// ── Results view: a uniform-height virtual-scroll list ───────────────────────
// `mount` is an empty container; `renderCard(record, index, ctx)` returns the
// card element (ctx carries the per-row selection state). `getId(record)` yields
// the stable id used for selection/export. Only the rows near the viewport are
// in the DOM; a tall (capped) sizer fakes the full scrollbar. Selection lives in
// a Set of ids, decoupled from the DOM, so it survives recycling in both scroll
// directions and scales to selecting 100k+ rows.
function createResultsView(mount, renderCard, getId, opts = {}) {
  const {onExport, exportFormats = ['xlsx', 'pdf'], maxExport = MAX_EXPORT} = opts;
  mount.classList.add('results');
  mount.replaceChildren();

  const bar = document.createElement('div');
  bar.className = 'results-bar';
  const count = document.createElement('span');
  count.className = 'results-count';
  const actions = document.createElement('span');
  actions.className = 'results-actions';
  const mkBtn = (label) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-secondary btn-mini';
    b.textContent = label;
    return b;
  };
  const btnAll = mkBtn('Select all');
  const btnVisible = mkBtn('Select visible');
  const btnClear = mkBtn('Clear');
  actions.append(btnAll, btnVisible, btnClear);
  const selCount = document.createElement('span');
  selCount.className = 'results-selected';
  bar.append(count, actions, selCount);

  // Bulk export of the current selection (only if this view supports it).
  let exportStatus = null;
  if (onExport) {
    const exportWrap = document.createElement('span');
    exportWrap.className = 'results-export';
    const fmt = document.createElement('select');
    fmt.className = 'export-format';
    for (const f of exportFormats) {
      const o = document.createElement('option');
      o.value = f;
      o.textContent = f.toUpperCase();
      fmt.appendChild(o);
    }
    const btnExport = mkBtn('Export selected');
    exportStatus = document.createElement('span');
    exportStatus.className = 'export-status';
    exportWrap.append(fmt, btnExport, exportStatus);
    bar.append(exportWrap);

    btnExport.addEventListener('click', async () => {
      const ids = [...selected];
      if (!ids.length) { exportStatus.textContent = 'select rows first'; return; }
      if (ids.length > maxExport) {
        exportStatus.textContent =
            `max ${maxExport.toLocaleString()} per export (selected ${ids.length.toLocaleString()})`;
        return;
      }
      btnExport.disabled = true;
      exportStatus.textContent = `exporting ${ids.length.toLocaleString()}…`;
      try {
        await onExport(ids, fmt.value);
        exportStatus.textContent = `exported ${ids.length.toLocaleString()} (${fmt.value.toUpperCase()})`;
      } catch (e) {
        exportStatus.textContent = 'export failed: ' + e.message;
      } finally {
        btnExport.disabled = false;  // selection is kept, mirroring SIGA
      }
    });
  }

  const scroll = document.createElement('div');
  scroll.className = 'vscroll';
  scroll.style.height = VIEWPORT_H + 'px';
  const sizer = document.createElement('div');
  sizer.className = 'vsizer';
  const win = document.createElement('div');
  win.className = 'vwindow';
  sizer.appendChild(win);
  scroll.appendChild(sizer);
  mount.append(bar, scroll);

  let records = [];
  let rafPending = false;
  let autoLoad = false;               // this view's auto-load-images state
  let visible = {start: 0, end: 0};   // truly-visible viewport range, for "Select visible"
  const selected = new Set();

  function updateSelCount() {
    const n = selected.size;
    selCount.textContent = n ? `${n.toLocaleString()} selected` : '';
    btnClear.disabled = n === 0;
  }

  function paint() {
    rafPending = false;
    const total = records.length;
    const perView = Math.ceil(VIEWPORT_H / CARD_HEIGHT);
    const virtualH = total * CARD_HEIGHT;
    const scrollTop = scroll.scrollTop;

    let start, winTop, visStart, visEnd;
    if (virtualH <= MAX_SCROLL_PX) {
      // Exact: sizer is the real height, native scroll is pixel-perfect.
      visStart = Math.floor(scrollTop / CARD_HEIGHT);
      visEnd = Math.ceil((scrollTop + VIEWPORT_H) / CARD_HEIGHT);
      start = visStart - OVERSCAN;
      if (start < 0) start = 0;
      winTop = start * CARD_HEIGHT;
    } else {
      // Scaled: the sizer is capped, so map the scroll position to a row index
      // proportionally and anchor that row at the top of the viewport.
      const maxScroll = MAX_SCROLL_PX - VIEWPORT_H;
      const maxFocus = Math.max(0, total - perView);
      const ratio = maxScroll > 0 ? scrollTop / maxScroll : 0;
      visStart = Math.round(Math.min(1, ratio) * maxFocus);
      visEnd = visStart + perView;
      start = visStart - OVERSCAN;
      if (start < 0) start = 0;
      winTop = scrollTop - (visStart - start) * CARD_HEIGHT;
    }
    let end = start + perView + OVERSCAN * 2;
    if (end > total) end = total;
    // The truly-visible rows (viewport only), for "Select visible".
    visible = {start: visStart, end: Math.min(total, visEnd)};

    win.style.transform = `translateY(${winTop}px)`;
    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      const rec = records[i];
      const id = getId(rec);
      const slot = document.createElement('div');
      slot.className = 'rc-slot';
      slot.style.height = CARD_HEIGHT + 'px';
      if (selected.has(id)) slot.classList.add('selected');
      // Checkbox state is derived from the Set at render time, so a row scrolled
      // back into view always reflects the current selection.
      const ctx = {
        selected: selected.has(id),
        inViewport: i >= visible.start && i < visible.end,
        autoLoad,
        onToggle(on) {
          if (on) selected.add(id);
          else selected.delete(id);
          slot.classList.toggle('selected', on);
          updateSelCount();
        },
      };
      slot.appendChild(renderCard(rec, i, ctx));
      frag.appendChild(slot);
    }
    win.replaceChildren(frag);
  }

  scroll.addEventListener('scroll', () => {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(paint);
  }, {passive: true});

  function sync(resetScroll) {
    const n = records.length;
    sizer.style.height = Math.min(n * CARD_HEIGHT, MAX_SCROLL_PX) + 'px';
    count.textContent = `${n.toLocaleString()} result${n === 1 ? '' : 's'}`;
    mount.hidden = n === 0;
    if (resetScroll) scroll.scrollTop = 0;
    paint();
  }

  btnAll.addEventListener('click', () => {
    for (const r of records) selected.add(getId(r));
    updateSelCount();
    paint();
  });
  btnVisible.addEventListener('click', () => {
    for (let i = visible.start; i < visible.end; i++) selected.add(getId(records[i]));
    updateSelCount();
    paint();
  });
  btnClear.addEventListener('click', () => {
    selected.clear();
    updateSelCount();
    paint();
  });

  updateSelCount();

  return {
    // Copy on set so the view owns its array (append mutates it in place, which
    // keeps draining 200k+ rows from re-allocating the whole list each page).
    // A fresh result set drops the old selection (those ids are gone).
    setRecords(arr) { records = arr ? arr.slice() : []; selected.clear(); updateSelCount(); sync(true); },
    appendRecords(arr) {
      if (!arr || !arr.length) return;
      for (const r of arr) records.push(r);
      sync(false);
    },
    clear() { records = []; selected.clear(); updateSelCount(); sync(true); },
    refresh() { paint(); },   // re-render the window
    setAutoLoad(on) { autoLoad = on; paint(); },
    get mount() { return mount; },
    get length() { return records.length; },
    get selectedCount() { return selected.size; },
    selectedIds() { return [...selected]; },
  };
}

// A label: value pair, built with textContent so record data can never inject
// markup.
function kv(label, value) {
  const wrap = document.createElement('div');
  wrap.className = 'kv';
  const k = document.createElement('span');
  k.className = 'k';
  k.textContent = (label == null ? '' : label) + ':';
  const v = document.createElement('span');
  v.className = 'v';
  v.textContent = value == null ? '' : String(value);
  wrap.append(k, document.createTextNode(' '), v);
  return wrap;
}

// A per-card download strip: one button per format, each calling exportFn(ids,
// fmt). Shows ✓/✗ feedback inline and re-enables itself.
function cardExportStrip(exportFn, ids, formats) {
  const strip = document.createElement('div');
  strip.className = 'rc-actions';
  for (const fmt of formats) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-secondary btn-mini';
    b.textContent = '⬇ ' + fmt.toUpperCase();
    b.addEventListener('click', async () => {
      const orig = b.textContent;
      b.disabled = true;
      b.textContent = '…';
      try {
        await exportFn(ids, fmt);
        b.textContent = orig;
      } catch (e) {
        b.textContent = '✗';
        b.title = e.message;
        setTimeout(() => { b.textContent = orig; b.title = ''; }, 2000);
      } finally {
        b.disabled = false;
      }
    });
    strip.appendChild(b);
  }
  return strip;
}

// A selection checkbox wired to the view's selection Set via ctx.onToggle.
function selectCheckbox(ctx) {
  const label = document.createElement('label');
  label.className = 'rc-check';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = ctx.selected;
  cb.addEventListener('change', () => ctx.onToggle(cb.checked));
  label.appendChild(cb);
  return label;
}

// One ficha (records/advanced) as a pre-expanded card: gray header band with
// the ejemplar/gaceta/sección/fecha block, then every `datos` entry below.
function fichaCard(rec, index, ctx) {
  const card = document.createElement('div');
  card.className = 'rc';

  const head = document.createElement('div');
  head.className = 'rc-head';
  const num = document.createElement('span');
  num.className = 'rc-num';
  num.textContent = '#' + (index + 1);
  const hgrid = document.createElement('div');
  hgrid.className = 'rc-grid';
  hgrid.append(
      kv('Ejemplar', rec.ejemplar),
      kv('Gaceta', rec.gaceta),
      kv('Sección', rec.seccion),
      kv('Fecha Puesta Circulación', rec.fechaPuestaCirculacion),
  );
  head.append(selectCheckbox(ctx), num, hgrid);

  const body = document.createElement('div');
  body.className = 'rc-body';
  const datos = document.createElement('div');
  datos.className = 'rc-grid';
  for (const d of rec.datos || []) datos.appendChild(kv(d.descripcion, d.datoTxt));
  body.appendChild(datos);

  // Image slot on the right (only for fichas that have one), like the SIGA card.
  if (rec.imagen) {
    const imgBox = document.createElement('div');
    imgBox.className = 'rc-img';
    renderImageArea(imgBox, rec.fichaId, !!ctx.inViewport, !!ctx.autoLoad);
    body.appendChild(imgBox);
  }

  card.append(head, body, cardExportStrip(exportFichas, [rec.fichaId], ['xlsx', 'pdf']));
  return card;
}

// One ejemplar (copies) card: i_id + date in the header, every other scalar field
// below (the exact field set isn't pinned, so render generically). No image — an
// ejemplar is the whole issue, not a logo. Export is the job-based copies flow.
function ejemplarCard(rec, index, ctx) {
  const card = document.createElement('div');
  card.className = 'rc';

  const head = document.createElement('div');
  head.className = 'rc-head';
  const num = document.createElement('span');
  num.className = 'rc-num';
  num.textContent = '#' + (index + 1);
  const hgrid = document.createElement('div');
  hgrid.className = 'rc-grid';
  hgrid.append(kv('i_id', rec.i_id));
  if (rec.i_anio != null) hgrid.append(kv('Fecha', `${rec.i_dia}/${rec.i_mes}/${rec.i_anio}`));
  head.append(selectCheckbox(ctx), num, hgrid);

  const body = document.createElement('div');
  body.className = 'rc-body';
  const grid = document.createElement('div');
  grid.className = 'rc-grid';
  const skip = new Set(['i_id', 'i_anio', 'i_mes', 'i_dia']);
  for (const [k, v] of Object.entries(rec)) {
    if (skip.has(k) || v == null || typeof v === 'object') continue;
    grid.appendChild(kv(k, v));
  }
  body.appendChild(grid);

  card.append(head, body, cardExportStrip(exportCopies, [rec.i_id], ['pdf', 'xlsx']));
  return card;
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

// Copies never hit the 15000 cap, so there's no cursor / Next page / All — but the
// by-fecha shape is a dict-of-lists (grouped by gaceta). A plain search keeps the
// groups (rendered as a SIGA-style selector, one group's cards shown at a time).
let copiesLoading = false;

const setCopiesFetchStatus = (text) => { $('c-fetch-status').textContent = text || ''; };

function copiesBuildParams() {
  const area = singleVal('c-area', 'c-area-sel');
  if (!area) return null;
  const params = new URLSearchParams({area});
  const gaceta = singleVal('c-gaceta', 'c-gaceta-sel');
  if (gaceta) params.set('gaceta', gaceta);
  const desde = $('c-desde').value.trim();
  if (desde) params.set('fecha_desde', desde);
  const hasta = $('c-hasta').value.trim();
  if (hasta) params.set('fecha_hasta', hasta);
  const recaptcha = $('c-recaptcha').value.trim();
  if (recaptcha) params.set('recaptcha', recaptcha);
  return params;
}

function updateCopiesControls() {
  $('c-search').disabled = copiesLoading;
}

function updateCopiesVisibility() {
  $('c-results').hidden = !isHuman() || copiesView.length === 0;
  if (!isHuman()) $('c-groups').hidden = true;
}

// data is a flat list (by gaceta / all) or a dict-of-lists (by fecha). Lists go
// straight to the table; a dict gets a group selector that swaps the table.
function copiesRender(data) {
  const box = $('c-groups');
  if (Array.isArray(data)) {
    box.hidden = true;
    box.replaceChildren();
    copiesView.setRecords(data);
  } else if (data && typeof data === 'object') {
    renderCopyGroups(data);
  } else {
    box.hidden = true;
    box.replaceChildren();
    copiesView.clear();
  }
  updateCopiesVisibility();
}

function renderCopyGroups(groups) {
  const box = $('c-groups');
  box.replaceChildren();
  const keys = Object.keys(groups);
  if (!keys.length) { box.hidden = true; copiesView.clear(); return; }
  box.hidden = false;
  for (const key of keys) {
    const list = Array.isArray(groups[key]) ? groups[key] : [];
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-secondary btn-mini group-tab';
    btn.textContent = `${key} (${list.length})`;
    btn.addEventListener('click', () => {
      box.querySelectorAll('.group-tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      copiesView.setRecords(list);
      updateCopiesVisibility();
    });
    box.appendChild(btn);
  }
  box.querySelector('.group-tab').click();  // open the first group
}

async function copiesSearch() {
  $('json-output').innerHTML = '';
  $('c-downloads').replaceChildren();
  const params = copiesBuildParams();
  if (!params) {
    copiesView.clear();
    $('c-groups').hidden = true;
    $('c-groups').replaceChildren();
    setCopiesFetchStatus('');
    appendJson('copies/search — not sent', null, {error: 'area is required'});
    updateCopiesControls();
    updateCopiesVisibility();
    return;
  }
  const copiesQuery = params.toString();
  copiesLoading = true;
  updateCopiesControls();
  const url = `${API}/copies/search?${copiesQuery}`;
  const {ok, status, data} = await fetchJson(url);
  if (!isHuman()) appendJson(`GET ${url}`, status, data);
  else { logSearch(`GET ${url}`, status, data, ok); copiesRender(ok ? data?.data : null); }
  copiesLoading = false;
  updateCopiesControls();
}

// Records paging state. The cursor binds the search params, so the query that
// produced a page must be reused byte-for-byte to fetch the next one — we freeze
// it in `recordsQuery` at search time and reuse it for Next page / All (client).
let recordsQuery = null;     // frozen base query string (no all/cursor)
let recordsCursor = null;    // continuation token, or null when fully drained
let recordsLoading = false;  // a fetch is in flight
let recordsLoopActive = false;  // the All (client) loop is running (Stop shown)
let recordsStop = false;     // Stop requested mid-loop

function recordsBaseParams() {
  const busqueda = $('r-busqueda').value.trim();
  if (!busqueda) return null;
  const params = new URLSearchParams({busqueda});
  const area = singleVal('r-area', 'r-area-sel');
  if (area) params.set('area', area);
  for (const g of multiVals('r-gacetas', 'r-gacetas-sel')) params.append('gacetas', g);
  const desde = $('r-desde').value.trim();
  if (desde) params.set('fecha_desde', desde);
  const hasta = $('r-hasta').value.trim();
  if (hasta) params.set('fecha_hasta', hasta);
  const recaptcha = $('r-recaptcha').value.trim();
  if (recaptcha) params.set('recaptcha', recaptcha);
  return params;
}

const setRecordsFetchStatus = (text) => { $('r-fetch-status').textContent = text || ''; };

// Buttons stay put; they just disable while a fetch is in flight. Next page only
// shows when a cursor came back; Stop only during the All (client) loop. All of
// the paging controls are human-mode only (raw mode = single plain requests).
function updateRecordsControls() {
  const human = isHuman();
  for (const id of ['r-all-server', 'r-all-client']) {
    $(id).hidden = !human;
    $(id).disabled = recordsLoading;
  }
  $('r-next').hidden = !human || !recordsCursor;
  $('r-next').disabled = recordsLoading;
  $('r-stop').hidden = !recordsLoopActive;
  $('r-autoimg-wrap').hidden = !human;
  $('r-search').disabled = recordsLoading;
}

// The card table is the human-mode result surface; in raw mode the JSON log is.
function updateRecordsVisibility() {
  $('r-results').hidden = !isHuman() || recordsView.length === 0;
}

// Fetch one page (a window or the all-envelope) into the table, set/append rows,
// and capture the continuation cursor.
async function recordsLoadPage(url, append) {
  recordsLoading = true;
  updateRecordsControls();
  const {ok, status, data} = await fetchJson(url);
  logSearch(`GET ${url}`, status, data, ok);
  const rows = ok && Array.isArray(data?.data) ? data.data : [];
  if (append) recordsView.appendRecords(rows);
  else recordsView.setRecords(rows);
  recordsCursor = ok ? (data?.pagination?.cursor ?? null) : null;
  setRecordsFetchStatus(recordsView.length
      ? `loaded ${recordsView.length.toLocaleString()}${recordsCursor ? ' — more available' : ''}`
      : (ok ? 'no results' : 'request failed'));
  recordsLoading = false;
  updateRecordsControls();
  updateRecordsVisibility();
}

async function recordsSearch() {
  $('json-output').innerHTML = '';
  const base = recordsBaseParams();
  if (!base) {
    recordsQuery = recordsCursor = null;
    recordsView.clear();
    setRecordsFetchStatus('');
    appendJson('records/search — not sent', null, {error: 'busqueda is required'});
    updateRecordsControls();
    updateRecordsVisibility();
    return;
  }
  recordsQuery = base.toString();
  recordsCursor = null;
  // Raw mode: a single plain request, shown in full in the JSON log (no paging).
  if (!isHuman()) {
    recordsLoading = true;
    updateRecordsControls();
    const url = `${API}/records/search?${recordsQuery}`;
    const {status, data} = await fetchJson(url);
    appendJson(`GET ${url}`, status, data);
    recordsLoading = false;
    updateRecordsControls();
    return;
  }
  // Human mode: paged first page (cursor= empty) so a cursor can come back.
  recordsView.clear();
  await recordsLoadPage(`${API}/records/search?${recordsQuery}&cursor=`, false);
}

async function recordsNextPage() {
  if (recordsLoading || !recordsCursor || !recordsQuery) return;
  await recordsLoadPage(
      `${API}/records/search?${recordsQuery}&cursor=${encodeURIComponent(recordsCursor)}`,
      true);
}

async function recordsAllServer() {
  if (recordsLoading) return;
  $('json-output').innerHTML = '';
  const base = recordsBaseParams();
  if (!base) {
    appendJson('records/search — not sent', null, {error: 'busqueda is required'});
    return;
  }
  recordsQuery = base.toString();
  recordsCursor = null;
  recordsView.clear();
  setRecordsFetchStatus('draining all (server)… this can take a while');
  await recordsLoadPage(`${API}/records/search?${recordsQuery}&all=true`, false);
}

// Client-side drain: walk the cursor ourselves, appending each window. Peak
// memory is one window, not the whole 170 MB server response.
async function recordsAllClient() {
  if (recordsLoading) return;
  $('json-output').innerHTML = '';
  const base = recordsBaseParams();
  if (!base) {
    appendJson('records/search — not sent', null, {error: 'busqueda is required'});
    return;
  }
  recordsQuery = base.toString();
  recordsCursor = null;
  recordsView.clear();
  recordsLoading = recordsLoopActive = true;
  recordsStop = false;
  updateRecordsControls();

  let url = `${API}/records/search?${recordsQuery}&cursor=`;
  let first = true, pages = 0, ended = 'done';
  while (true) {
    const {ok, status, data} = await fetchJson(url);
    if (!ok) {
      logSearch(`GET ${url}`, status, data, ok);
      ended = 'error';
      break;
    }
    const rows = Array.isArray(data?.data) ? data.data : [];
    if (first) { recordsView.setRecords(rows); first = false; }
    else recordsView.appendRecords(rows);
    pages++;
    setRecordsFetchStatus(
        `fetched ${recordsView.length.toLocaleString()} in ${pages} page${pages === 1 ? '' : 's'}…`);
    updateRecordsVisibility();
    recordsCursor = data?.pagination?.cursor ?? null;
    if (!recordsCursor) { ended = 'done'; break; }
    if (recordsStop) { ended = 'stopped'; break; }
    url = `${API}/records/search?${recordsQuery}&cursor=${encodeURIComponent(recordsCursor)}`;
  }
  recordsLoading = recordsLoopActive = false;
  const total = recordsView.length;
  setRecordsFetchStatus(
      `${ended} — ${total.toLocaleString()} loaded in ${pages} page${pages === 1 ? '' : 's'}` +
      (recordsCursor ? ' (more available)' : ''));
  appendJson('records/search (all · client)', null,
      {pages, loaded: total, ended, more: recordsCursor != null});
  updateRecordsControls();
  updateRecordsVisibility();
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

// Advanced search mirrors records' paging, but it's a POST: the body carries the
// search and cursor/all ride as query params. The body is frozen at search time
// (the cursor binds it) and reused for Next page / All, same as records' query.
let advancedBody = null;     // frozen request body
let advancedCursor = null;
let advancedLoading = false;
let advancedLoopActive = false;
let advancedStop = false;

const setAdvancedFetchStatus = (text) => { $('a-fetch-status').textContent = text || ''; };

function advancedBuildBody() {
  const areaStr = singleVal('a-area', 'a-area-sel');
  if (!areaStr) return null;
  return {
    area: Number(areaStr),
    datos: collectDatos(),
    gacetas: multiVals('a-gacetas', 'a-gacetas-sel').map(Number),
    secciones: multiVals('a-secciones', 'a-secciones-sel').map(Number),
    fecha_desde: $('a-desde').value.trim() || null,
    fecha_hasta: $('a-hasta').value.trim() || null,
    recaptcha: $('a-recaptcha').value.trim(),
  };
}

function advancedPost(query) {
  return fetchJson(`${API}/advanced/search${query}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(advancedBody),
  });
}

function updateAdvancedControls() {
  const human = isHuman();
  for (const id of ['a-all-server', 'a-all-client']) {
    $(id).hidden = !human;
    $(id).disabled = advancedLoading;
  }
  $('a-next').hidden = !human || !advancedCursor;
  $('a-next').disabled = advancedLoading;
  $('a-stop').hidden = !advancedLoopActive;
  $('a-autoimg-wrap').hidden = !human;
  $('a-search').disabled = advancedLoading;
}

function updateAdvancedVisibility() {
  $('a-results').hidden = !isHuman() || advancedView.length === 0;
}

async function advancedLoadPage(query, append) {
  advancedLoading = true;
  updateAdvancedControls();
  const {ok, status, data} = await advancedPost(query);
  logSearch(`POST /advanced/search${query}`, status, data, ok);
  const rows = ok && Array.isArray(data?.data) ? data.data : [];
  if (append) advancedView.appendRecords(rows);
  else advancedView.setRecords(rows);
  advancedCursor = ok ? (data?.pagination?.cursor ?? null) : null;
  setAdvancedFetchStatus(advancedView.length
      ? `loaded ${advancedView.length.toLocaleString()}${advancedCursor ? ' — more available' : ''}`
      : (ok ? 'no results' : 'request failed'));
  advancedLoading = false;
  updateAdvancedControls();
  updateAdvancedVisibility();
}

async function advancedSearch() {
  $('json-output').innerHTML = '';
  const body = advancedBuildBody();
  if (!body) {
    advancedBody = advancedCursor = null;
    advancedView.clear();
    setAdvancedFetchStatus('');
    appendJson('advanced/search — not sent', null, {error: 'area is required'});
    updateAdvancedControls();
    updateAdvancedVisibility();
    return;
  }
  advancedBody = body;
  advancedCursor = null;
  if (!isHuman()) {
    advancedLoading = true;
    updateAdvancedControls();
    const {status, data} = await advancedPost('');
    appendJson('POST /advanced/search', status, data);
    advancedLoading = false;
    updateAdvancedControls();
    return;
  }
  advancedView.clear();
  await advancedLoadPage('?cursor=', false);
}

async function advancedNextPage() {
  if (advancedLoading || !advancedCursor || !advancedBody) return;
  await advancedLoadPage('?cursor=' + encodeURIComponent(advancedCursor), true);
}

async function advancedAllServer() {
  if (advancedLoading) return;
  $('json-output').innerHTML = '';
  const body = advancedBuildBody();
  if (!body) {
    appendJson('advanced/search — not sent', null, {error: 'area is required'});
    return;
  }
  advancedBody = body;
  advancedCursor = null;
  advancedView.clear();
  setAdvancedFetchStatus('draining all (server)… this can take a while');
  await advancedLoadPage('?all=true', false);
}

async function advancedAllClient() {
  if (advancedLoading) return;
  $('json-output').innerHTML = '';
  const body = advancedBuildBody();
  if (!body) {
    appendJson('advanced/search — not sent', null, {error: 'area is required'});
    return;
  }
  advancedBody = body;
  advancedCursor = null;
  advancedView.clear();
  advancedLoading = advancedLoopActive = true;
  advancedStop = false;
  updateAdvancedControls();

  let query = '?cursor=';
  let first = true, pages = 0, ended = 'done';
  while (true) {
    const {ok, status, data} = await advancedPost(query);
    if (!ok) {
      logSearch(`POST /advanced/search${query}`, status, data, ok);
      ended = 'error';
      break;
    }
    const rows = Array.isArray(data?.data) ? data.data : [];
    if (first) { advancedView.setRecords(rows); first = false; }
    else advancedView.appendRecords(rows);
    pages++;
    setAdvancedFetchStatus(
        `fetched ${advancedView.length.toLocaleString()} in ${pages} page${pages === 1 ? '' : 's'}…`);
    updateAdvancedVisibility();
    advancedCursor = data?.pagination?.cursor ?? null;
    if (!advancedCursor) { ended = 'done'; break; }
    if (advancedStop) { ended = 'stopped'; break; }
    query = '?cursor=' + encodeURIComponent(advancedCursor);
  }
  advancedLoading = advancedLoopActive = false;
  const total = advancedView.length;
  setAdvancedFetchStatus(
      `${ended} — ${total.toLocaleString()} loaded in ${pages} page${pages === 1 ? '' : 's'}` +
      (advancedCursor ? ' (more available)' : ''));
  appendJson('advanced/search (all · client)', null,
      {pages, loaded: total, ended, more: advancedCursor != null});
  updateAdvancedControls();
  updateAdvancedVisibility();
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

const recordsView = createResultsView(
    $('r-results'), fichaCard, (r) => r.fichaId, {onExport: exportFichas});
updateRecordsControls();

const advancedView = createResultsView(
    $('a-results'), fichaCard, (r) => r.fichaId, {onExport: exportFichas});
updateAdvancedControls();

const copiesView = createResultsView(
    $('c-results'), ejemplarCard, (r) => r.i_id,
    {onExport: exportCopies, exportFormats: ['pdf', 'xlsx'], maxExport: MAX_EXPORT_COPIES});
updateCopiesControls();

$('start').addEventListener('click', start);
$('c-search').addEventListener('click', copiesSearch);
$('r-search').addEventListener('click', recordsSearch);
$('r-all-server').addEventListener('click', recordsAllServer);
$('r-all-client').addEventListener('click', recordsAllClient);
$('r-next').addEventListener('click', recordsNextPage);
$('r-stop').addEventListener('click', () => { recordsStop = true; });
$('r-autoimg').addEventListener('change', (e) => {
  recordsView.setAutoLoad(e.target.checked);
});
$('a-add').addEventListener('click', addDatoRow);
$('a-search').addEventListener('click', advancedSearch);
$('a-all-server').addEventListener('click', advancedAllServer);
$('a-all-client').addEventListener('click', advancedAllClient);
$('a-next').addEventListener('click', advancedNextPage);
$('a-stop').addEventListener('click', () => { advancedStop = true; });
$('a-autoimg').addEventListener('change', (e) => {
  advancedView.setAutoLoad(e.target.checked);
});
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
  updateRecordsVisibility();
  updateRecordsControls();
  updateAdvancedVisibility();
  updateAdvancedControls();
  updateCopiesVisibility();
  updateCopiesControls();
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
