import { VIDEO_EXT, GIF_EXT, extOf, defaultProfile, defaultNaming, makeName, encodeArgs, repeatsNeeded, natCompare, fmtSize } from './core.js';
import { getEngine } from './engine.js';
import * as L from './loops.js';
import * as SR from './search.js';
import { makeZip } from './zip.js';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const engine = getEngine();
class Cancelled extends Error {}
class Ctl { constructor() { this.cancel = false; } check() { if (this.cancel) throw new Cancelled('cancelled'); } }

const TOOLS = {
  convert: { label: 'Convert', sub: 'Bulk convert mov, webm, mkv, gif … to VJ-friendly mp4, with bulk file naming. Everything runs in your browser.', go: 'Convert', gif: false },
  gif2mp4: { label: 'GIF → MP4', sub: 'Bulk convert GIF files to mp4 that VJ software can play smoothly.', go: 'Convert GIFs', gif: true },
  loop: { label: 'Make loops', sub: 'Turns clips into seamless loops: checks the loop point, then trims, plays forward + backward, or crossfades.', go: 'Make loops', gif: false },
  search: { label: 'Find loops', sub: 'Search free sources for loops, download them and convert them to VJ-friendly clips.', go: 'Download and convert selected', gif: false },
};
let tool = 'convert'; try { tool = localStorage.getItem('vj_tool') || 'convert'; } catch (e) { /* ignore */ }
if (TOOLS[location.hash.slice(1)]) tool = location.hash.slice(1);
if (!TOOLS[tool]) tool = 'convert';
const FILES = { convert: [], gif2mp4: [], loop: [] }, SEL = { convert: new Set(), gif2mp4: new Set(), loop: new Set() };
let RES = [], RSEL = new Set(), SRCSEL = new Set(), nextId = 1;
const JOBS = [];

const GROUPS = [
  { g: 'VJ-friendly output', open: true, f: [
    { k: 'size', t: 'select', o: ['1280x720', '1920x1080', '2560x1440', '3840x2160'], h: 'The size box. Portrait material is turned automatically. In the browser, 1920×1080 or smaller is wise.' },
    { k: 'fit', t: 'select', o: [['keep', 'Keep shape, only shrink'], ['fit', 'Letterbox to exactly the box'], ['fill', 'Crop to exactly the box']] },
    { k: 'upscale', t: 'bool', l: 'Enlarge small videos up to the box', h: 'Only with “Keep shape”. Good for tiny GIFs.' },
    { k: 'fps', t: 'select', num: 1, o: [['0', 'Keep (snapped to 24/25/30/50/60)'], ['24', '24'], ['25', '25'], ['30', '30'], ['50', '50'], ['60', '60']] },
    { k: 'crf', t: 'range', min: 12, max: 28, step: 1, l: 'Quality (lower = better, larger)' },
    { k: 'preset', t: 'select', o: ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium'], h: 'The browser is slower than the desktop version. veryfast is a good balance.' },
    { k: 'gop', t: 'range', min: 1, max: 120, step: 1, l: 'Keyframe every (frames)', h: 'Short = smooth scrubbing and looping in VJ software.' },
    { k: 'minSeconds', t: 'range', min: 0, max: 120, step: 1, l: 'Repeat until at least (s)', h: 'Whole loops are repeated. 0 = off. The Munken Loops mixer likes 16 s and longer.' },
    { k: 'bg', t: 'text', l: 'Background for transparent GIFs', h: 'black, white or #rrggbb' },
    { k: 'keepAudio', t: 'bool', l: 'Keep sound', h: 'Off by default: loops normally have no sound.' }] },
  { g: 'Loop', loop: true, open: true, f: [
    { k: 'mode', t: 'select', lp: 1, o: [['auto', 'Auto: keep good loops, otherwise fix them'], ['pingpong', 'Forward, then backward'], ['crossfade', 'Crossfade end into start'], ['trim', 'Trim to the best loop point'], ['none', 'Do nothing (just convert)']], h: 'Auto: a clip that already loops is left alone; if cutting a bit off the end gives a clean loop it does that; otherwise it uses the fallback.' },
    { k: 'fallback', t: 'select', lp: 1, o: [['pingpong', 'Forward, then backward'], ['crossfade', 'Crossfade']], l: 'Auto fallback' },
    { k: 'fade', t: 'range', lp: 1, min: 0.3, max: 8, step: 0.1, l: 'Crossfade length (s)' }] },
  { g: 'Naming', open: true, naming: true, f: [
    { k: 'template', t: 'text', n: 1, l: 'Name template', h: 'Tokens: {name} {n} {n2} {n3} {n4} {w} {h} {res} {fps} {dur} {date}' },
    { k: 'prefix', t: 'text', n: 1 }, { k: 'suffix', t: 'text', n: 1 },
    { k: 'start', t: 'number', n: 1, l: 'First number' },
    { k: 'case', t: 'select', n: 1, o: [['keep', 'Keep'], ['lower', 'lowercase'], ['upper', 'UPPERCASE']] },
    { k: 'spaces', t: 'select', n: 1, o: [['_', 'Underscore _'], ['-', 'Dash -'], ['keep', 'Keep spaces']] },
    { k: 'ascii', t: 'bool', n: 1, l: 'Plain letters only (æøå → ae o a)' }] },
  { g: 'Saving', open: true, f: [
    { k: 'saveDirect', t: 'bool', x: 1, l: 'Save each file straight into a folder I choose', h: 'Chrome and Edge only. Saves memory with many or big files. Otherwise results stay in the page until you download them.' },
    { k: 'loopify', t: 'select', x: 1, srch: 1, o: [['none', 'Just convert'], ['auto', 'Make them seamless loops (auto)'], ['pingpong', 'Forward, then backward'], ['crossfade', 'Crossfade']], l: 'Downloaded clips' }] },
];
const LABEL = k => k.replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase());
const HAS_DIR = typeof window.showDirectoryPicker === 'function';

function fld(f) {
  const id = 'o_' + f.k, l = f.l || LABEL(f.k); let c;
  if (f.k === 'saveDirect' && !HAS_DIR) return '';
  if (f.t === 'range') c = `<div class="ctl"><input id="${id}" type="range" min="${f.min}" max="${f.max}" step="${f.step}"><output></output></div>`;
  else if (f.t === 'select') c = `<select id="${id}">${f.o.map(o => Array.isArray(o) ? `<option value="${esc(o[0])}">${esc(o[1])}</option>` : `<option>${esc(o)}</option>`).join('')}</select>`;
  else if (f.t === 'bool') c = `<label class="chips"><input id="${id}" type="checkbox"> <span style="font-size:.9rem">${esc(l)}</span></label>`;
  else c = `<input id="${id}" type="${f.t === 'number' ? 'number' : 'text'}">`;
  return `<div class="f${f.srch ? ' srch' : ''}"><label for="${id}" ${f.t === 'bool' ? 'class="hide"' : ''}>${esc(l)}</label>${c}${f.h ? `<div class="h">${esc(f.h)}</div>` : ''}</div>`;
}
function buildSettings() {
  $('#groups').innerHTML = GROUPS.map(g => `<details data-g="${g.g}" ${g.open ? 'open' : ''} class="${g.loop ? 'loopg' : ''}"><summary>${g.g}</summary><div class="fields">${g.f.map(fld).join('')}${g.naming ? '<div class="f" style="grid-column:1/-1"><label>Preview</label><div class="prev" id="nprev">Add files to see the new names.</div></div>' : ''}</div></details>`).join('');
  const P = { ...defaultProfile(), preset: 'veryfast' }, N = defaultNaming(), X = { saveDirect: false, loopify: 'none', mode: 'auto', fallback: 'pingpong', fade: 1.5 };
  GROUPS.forEach(g => g.f.forEach(f => {
    const e = $('#o_' + f.k); if (!e) return;
    const v = f.n ? N[f.k] : (f.x || f.lp) ? X[f.k] : P[f.k];
    if (e.type === 'checkbox') e.checked = !!v; else e.value = String(v);
    if (e.type === 'range') { const o = e.nextElementSibling; o.textContent = e.value; e.oninput = () => o.textContent = e.value; }
  }));
  $('#groups').addEventListener('input', () => namePrev());
}
const val = f => { const e = $('#o_' + f.k); if (!e) return undefined; return e.type === 'checkbox' ? e.checked : (e.type === 'range' || e.type === 'number' || f.num) ? Number(e.value) : e.value; };
function collect() {
  const profile = {}, naming = {}, loop = {}, ex = {};
  GROUPS.forEach(g => g.f.forEach(f => { const v = val(f); if (v === undefined) return; if (f.n) naming[f.k] = v; else if (f.lp) loop[f.k] = v; else if (f.x) ex[f.k] = v; else profile[f.k] = v; }));
  profile.maxFps = 60;
  if (tool === 'search') loop.mode = ex.loopify;
  return { profile, naming, loop, ex };
}

function setTool(t) {
  tool = t; history.replaceState(null, '', '#' + t); try { localStorage.setItem('vj_tool', t); } catch (e) { /* ignore */ }
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  document.querySelectorAll('[data-for]').forEach(e => e.classList.toggle('hide', !e.dataset.for.split(' ').includes(t)));
  $('#sub').textContent = TOOLS[t].sub; $('#go').textContent = TOOLS[t].go;
  document.querySelectorAll('.loopg').forEach(e => e.classList.toggle('hide', t !== 'loop'));
  document.querySelectorAll('.srch').forEach(e => e.classList.toggle('hide', t !== 'search'));
  $('#looptools').classList.toggle('hide', t !== 'loop');
  $('#droptxt').textContent = TOOLS[t].gif ? 'Drop GIF files here' : 'Drop videos here';
  renderFiles(); namePrev(); runInfo();
}
function runInfo() {
  $('#runinfo').textContent = tool === 'search' ? (RSEL.size ? `${RSEL.size} clip(s) selected` : 'Select search results above') : (SEL[tool].size ? `${SEL[tool].size} of ${FILES[tool].length} file(s) selected` : 'Select files above');
}

// ---------------------------------------------------------------------------------------------- files
const okExt = (n, t = tool) => (TOOLS[t].gif ? GIF_EXT : VIDEO_EXT).has(extOf(n));
function addFiles(list, t = tool) {
  const have = new Set(FILES[t].map(f => f.key)); let skipped = 0;
  for (const file of list) {
    const name = file.webkitRelativePath || file.name;
    if (!okExt(file.name, t)) { skipped++; continue; }
    const key = `${name}|${file.size}|${file.lastModified}`; if (have.has(key)) continue;
    const it = { id: nextId++, key, file, name: file.name, folder: (file.webkitRelativePath || '').split('/').slice(-2, -1)[0] || '', size: file.size, url: URL.createObjectURL(file), meta: null, tag: null };
    FILES[t].push(it); SEL[t].add(it.id);
  }
  FILES[t].sort((a, b) => natCompare(a.name, b.name));
  if (t === tool) { renderFiles(); namePrev(); runInfo(); }
  $('#msg').textContent = skipped ? `${skipped} file(s) skipped (${TOOLS[t].gif ? 'only GIF files here' : 'not a video type I know'}).` : '';
}
function renderFiles() {
  if (tool === 'search') return;
  const Ls = FILES[tool];
  $('#fcount').textContent = Ls.length ? `(${SEL[tool].size} selected of ${Ls.length})` : '';
  $('#files').innerHTML = Ls.map(f => `<div class="vt ${SEL[tool].has(f.id) ? 'sel' : ''}" data-id="${f.id}">${f.name.toLowerCase().endsWith('.gif') ? `<img alt="" src="${f.url}">` : `<video muted preload="metadata" playsinline src="${f.url}#t=0.3"></video>`}${f.tag ? `<span class="tag ${f.tag.cls}" title="${esc(f.tag.text)}">${esc(f.tag.short)}</span>` : ''}<button class="x" data-rm="${f.id}" title="Remove from list">✕</button><div class="cap"><span title="${esc(f.name)}">${esc(f.name)}</span><span>${fmtSize(f.size)}</span></div></div>`).join('') || '<span class="muted">No files yet.</span>';
  $('#files').querySelectorAll('video').forEach((v, i) => {
    const f = Ls[i]; v.onloadedmetadata = () => { f.meta = { width: v.videoWidth, height: v.videoHeight, duration: v.duration }; namePrev(); };
    v.onerror = () => { v.style.visibility = 'hidden'; };
  });
}
$('#files').onclick = e => {
  const rm = e.target.closest('[data-rm]');
  if (rm) { const id = +rm.dataset.rm; FILES[tool] = FILES[tool].filter(f => f.id !== id); SEL[tool].delete(id); renderFiles(); namePrev(); runInfo(); return; }
  const t = e.target.closest('.vt'); if (!t) return; const id = +t.dataset.id;
  SEL[tool].has(id) ? SEL[tool].delete(id) : SEL[tool].add(id); t.classList.toggle('sel');
  $('#fcount').textContent = `(${SEL[tool].size} selected of ${FILES[tool].length})`; namePrev(); runInfo();
};
$('#selall').onclick = () => { FILES[tool].forEach(f => SEL[tool].add(f.id)); renderFiles(); namePrev(); runInfo(); };
$('#selnone').onclick = () => { SEL[tool].clear(); renderFiles(); namePrev(); runInfo(); };
$('#clearf').onclick = () => { FILES[tool] = []; SEL[tool].clear(); renderFiles(); namePrev(); runInfo(); };
$('#pick').onclick = () => $('#picker').click();
$('#pickdir').onclick = () => $('#pickerdir').click();
$('#picker').onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };
$('#pickerdir').onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };
const drop = $('#drop');
['dragenter', 'dragover'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', async e => {
  const items = [...(e.dataTransfer.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (items.some(i => i.isDirectory)) { addFiles(await readEntries(items)); return; }
  addFiles([...e.dataTransfer.files]);
});
async function readEntries(entries) {
  const out = [];
  const walk = async en => {
    if (en.isFile) out.push(await new Promise(r => en.file(r)));
    else { const rd = en.createReader(); for (;;) { const b = await new Promise(r => rd.readEntries(r)); if (!b.length) break; for (const c of b) await walk(c); } }
  };
  for (const en of entries) await walk(en);
  return out;
}

// ---------------------------------------------------------------------------------------------- naming preview
let npT;
function namePrev() {
  clearTimeout(npT);
  npT = setTimeout(() => {
    const el = $('#nprev'); if (!el) return;
    if (tool === 'search') { el.textContent = 'Downloaded clips are named “provider_id” unless you change the template.'; return; }
    const sel = FILES[tool].filter(f => SEL[tool].has(f.id));
    if (!sel.length) { el.textContent = 'Add files to see the new names.'; return; }
    const { naming } = collect(), taken = new Set();
    const rows = sel.map((f, i) => `${f.name}  →  ${makeName(f.name, i, naming, f.meta, taken, f.folder)}.mp4`);
    el.textContent = rows.slice(0, 12).join('\n') + (rows.length > 12 ? `\n… and ${rows.length - 12} more` : '');
  }, 200);
}

// ---------------------------------------------------------------------------------------------- engine status
function engineStatus(t) { $('#engine').textContent = t || ''; }
async function ensureEngine() { await engine.load(engineStatus); engineStatus(''); }

// open one picked File in the engine (read straight from disk, no copy)
async function openInput(file) {
  const safe = new File([file], 'src' + (extOf(file.name) || '.mp4'), { type: file.type });
  const path = await engine.mount(safe);
  return path;
}

// ---------------------------------------------------------------------------------------------- loop check
$('#checkloops').onclick = async () => {
  const sel = FILES.loop.filter(f => SEL.loop.has(f.id)); let i = 0;
  try { await ensureEngine(); } catch (e) { $('#chkmsg').textContent = ' ' + e.message; return; }
  for (const f of sel) {
    $('#chkmsg').textContent = ` Checking ${++i}/${sel.length}…`;
    let path;
    try {
      path = await openInput(f.file);
      const info = await L.probe(engine, path, f.name), r = await L.analyze(engine, path, info);
      f.tag = { short: r.verdict === 'seamless' ? 'loops ✓' : r.verdict === 'almost' ? 'almost' : 'jump', cls: r.verdict === 'seamless' ? 'good' : 'bad', text: L.reportText(r) };
    } catch (e) { f.tag = { short: '?', cls: 'bad', text: e.message }; }
    finally { if (path) await engine.unmount(path); }
    renderFiles();
  }
  $('#chkmsg').textContent = ' Done. Hover a label for details.';
};

// ---------------------------------------------------------------------------------------------- search
function renderSrcs() {
  $('#srcs').innerHTML = SR.status().map(s => `<label class="srcchip ${SRCSEL.has(s.name) ? 'sel' : ''} ${s.ready ? '' : 'off'}" title="${s.ready ? '' : 'Needs a free API key – see below'}"><input type="checkbox" data-s="${s.name}" ${SRCSEL.has(s.name) ? 'checked' : ''} ${s.ready ? '' : 'disabled'}>${esc(s.label)}${s.ready ? '' : ' 🔑'}</label>`).join('');
}
$('#srcs').onchange = e => { const s = e.target.dataset.s; if (!s) return; e.target.checked ? SRCSEL.add(s) : SRCSEL.delete(s); renderSrcs(); };
$('#savekeys').onclick = () => { SR.setKey('pexels', $('#k_pexels').value); SR.setKey('pixabay', $('#k_pixabay').value); SR.status().filter(s => s.ready).forEach(s => SRCSEL.add(s.name)); renderSrcs(); $('#keymsg').textContent = 'Saved in this browser.'; };
let SQ = null;   // the search that "Load more" continues
async function doSearch(more = false) {
  const q = more && SQ ? SQ.q : $('#q').value.trim(); if (!q) { $('#smsg').textContent = 'Type what you are looking for.'; return; }
  const page = more && SQ ? SQ.page + 1 : 1;
  $('#smsg').textContent = 'Searching…'; $('#dosearch').disabled = true; $('#more').disabled = true;
  try {
    const o = { loopWord: $('#loopword').checked, minHeight: +$('#minh').value, minSeconds: +$('#mins').value || 0, maxSeconds: +$('#maxs').value || 0, orientation: $('#orient').value, pixabayType: $('#ptype').value, page };
    const r = await SR.search(q, [...SRCSEL], o);
    const have = new Set(RES.map(x => x.provider + ':' + x.id)), fresh = r.results.filter(x => !have.has(x.provider + ':' + x.id));
    if (more) RES = RES.concat(fresh); else { RES = fresh; RSEL = new Set(); }
    SQ = { q, page }; renderResults();
    const er = Object.entries(r.errors).map(([k, v]) => `${k}: ${v}`).join(' · ');
    $('#smsg').innerHTML = (RES.length ? `${RES.length} clips found${more && !fresh.length ? ' (nothing new on this page)' : ''}.` : 'Nothing found. Try other words.') + (er ? ` <span class="warn">${esc(er)}</span>` : '');
  } catch (e) { $('#smsg').textContent = e.message; }
  $('#dosearch').disabled = false; $('#more').disabled = false; runInfo();
}
function renderResults() {
  $('#rtools').classList.toggle('hide', !RES.length); $('#moretools').classList.toggle('hide', !RES.length); $('#rcount').textContent = RES.length ? `${RSEL.size} selected` : '';
  $('#results').innerHTML = RES.map((r, i) => `<div class="rc ${RSEL.has(i) ? 'sel' : ''}" data-i="${i}">${r.thumb ? `<img loading="lazy" referrerpolicy="no-referrer" alt="" src="${esc(r.thumb)}" onerror="this.style.visibility='hidden'">` : '<img alt="">'}<div class="b"><span class="t" title="${esc(r.title)}">${esc(r.title)}</span><span class="muted">${esc(r.provider)} · ${r.height ? r.width + '×' + r.height : 'size ?'} · ${r.duration ? Math.round(r.duration) + ' s' : '? s'}</span><span class="muted" title="${esc(r.author)}">${esc(r.license || 'licence ?')}${r.author ? ' · ' + esc(r.author) : ''}</span>${r.page ? `<a href="${esc(r.page)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="font-size:.78rem">Open page ↗</a>` : ''}</div></div>`).join('');
}
$('#results').onclick = e => { const c = e.target.closest('.rc'); if (!c) return; const i = +c.dataset.i; RSEL.has(i) ? RSEL.delete(i) : RSEL.add(i); c.classList.toggle('sel'); $('#rcount').textContent = `${RSEL.size} selected`; runInfo(); };
$('#rall').onclick = () => { RES.forEach((_, i) => RSEL.add(i)); renderResults(); runInfo(); };
$('#rnone').onclick = () => { RSEL.clear(); renderResults(); runInfo(); };
$('#dosearch').onclick = () => doSearch(false); $('#more').onclick = () => doSearch(true); $('#q').onkeydown = e => { if (e.key === 'Enter') doSearch(false); };

// ---------------------------------------------------------------------------------------------- jobs
const stems = async dir => { const s = new Set(); for await (const [n] of dir.entries()) s.add(n.replace(/\.[^.]+$/, '').toLowerCase()); return s; };

async function processOne(job, src, idx, srcName, folder, c) {
  // src: File. Returns { name, blob?, size, note }
  const ctl = job.ctl; let path;
  try {
    path = await openInput(src);
    const info = await L.probe(engine, path, srcName);
    if (!info.width) throw new Error('No picture found in this file');
    const base = makeName(srcName, idx, c.naming, { ...info }, job.taken, folder), out = `o_${job.id}_${idx}.mp4`;
    const prog = r => { job.progress = (job.finished + r) / job.total; job.dirty = true; };
    let note;
    if (job.tool === 'loop' || (job.tool !== 'convert' && job.tool !== 'gif2mp4' && c.loop.mode && c.loop.mode !== 'none')) {
      const r = await L.makeLoop(engine, path, out, c.profile, info, { mode: c.loop.mode, fade: c.loop.fade, fallback: c.loop.fallback || 'pingpong', ctl, onProgress: prog });
      note = r.note;
    } else {
      const r = await engine.exec(encodeArgs(path, out, c.profile, info, { repeat: repeatsNeeded(info.duration, c.profile.minSeconds) }), { onProgress: prog });
      if (r.code !== 0) throw new Error('Could not convert this file (unsupported format? ' + (r.log.filter(l => /error|invalid|not supported|unknown/i.test(l)).slice(-1)[0] || '') + ')');
      note = `${info.width}x${info.height} ${info.codec} -> h264`;
    }
    ctl.check();
    const data = await engine.read(out); await engine.remove(out);
    return { name: base + '.mp4', blob: new Blob([data], { type: 'video/mp4' }), size: data.length, note };
  } finally { if (path) await engine.unmount(path); }
}

async function runJob(job) {
  const c = job.c; job.status = 'running'; render();
  try {
    await ensureEngine();
    if (job.dir) job.taken = await stems(job.dir);
    const credits = [];
    for (let i = 0; i < job.items.length; i++) {
      job.ctl.check(); const it = job.items[i]; job.current = it.label; render();
      try {
        let file = it.file, srcName = it.name, folder = it.folder || '';
        if (it.result) { job.current = 'Downloading ' + it.label; render(); file = await SR.download(it.result, p => { job.progress = (job.finished + p * .5) / job.total; job.dirty = true; }, job.abort.signal); srcName = file.name; }
        const r = await processOne(job, file, i, srcName, folder, c);
        if (job.dir) { const fh = await job.dir.getFileHandle(r.name, { create: true }), w = await fh.createWritable(); await w.write(r.blob); await w.close(); r.saved = true; r.blob = null; }
        job.results.push(r);
        if (it.result) credits.push([r.name, it.result.title, it.result.author, it.result.license, it.result.licenseUrl, it.result.page, new Date().toISOString().slice(0, 10)]);
        job.log.push(`OK  ${it.label}  ${r.note}`);
      } catch (e) {
        if (e instanceof Cancelled || job.ctl.cancel) throw new Cancelled();
        job.failed++; job.log.push(`ERR ${it.label}  ${String(e.message).slice(0, 250)}`);
      }
      job.finished++; job.progress = job.finished / job.total; render();
    }
    if (credits.length) {
      const blob = new Blob([SR.creditsCsv(credits)], { type: 'text/csv' });
      if (job.dir) { const fh = await job.dir.getFileHandle('credits.csv', { create: true }), w = await fh.createWritable(); await w.write(blob); await w.close(); }
      else job.results.push({ name: 'credits.csv', blob, size: blob.size, note: 'who made the clips and under which licence' });
    }
    job.status = job.failed && job.failed === job.items.length ? 'failed' : 'done';
  } catch (e) {
    job.status = (e instanceof Cancelled || job.ctl.cancel) ? 'cancelled' : 'failed';
    if (job.status === 'failed') job.error = e.message;
  }
  job.progress = job.status === 'done' ? 1 : job.progress; job.current = ''; render();
  if (job.ctl.cancel) engine.terminate();
}

let running = false;
async function pump() {
  if (running) return; running = true;
  try { for (;;) { const j = JOBS.find(x => x.status === 'queued'); if (!j) break; await runJob(j); } } finally { running = false; }
}

async function start(kind, items) {
  $('#msg').textContent = '';
  if (!items.length) { $('#msg').textContent = kind === 'search' ? 'Select search results first.' : kind === 'fetch' ? 'Paste at least one link.' : 'Add and select some files first.'; return; }
  const c = collect(); let dir = null;
  if (c.ex.saveDirect && HAS_DIR) { try { dir = await window.showDirectoryPicker({ mode: 'readwrite' }); } catch (e) { return; } }
  const job = { id: nextId++, tool: kind, status: 'queued', created: Date.now(), items, total: items.length, finished: 0, failed: 0, progress: 0, c, dir, taken: new Set(), results: [], log: [], ctl: new Ctl(), abort: new AbortController(), current: '', open: false, urls: [] };
  JOBS.unshift(job); render(); pump();
}
$('#go').onclick = () => {
  if (tool === 'search') return start('search', [...RSEL].map(i => ({ result: { ...RES[i] }, label: RES[i].title, name: RES[i].id })));
  start(tool, FILES[tool].filter(f => SEL[tool].has(f.id)).map(f => ({ file: f.file, name: f.name, folder: f.folder, label: f.name })));
};
$('#fetchgo').onclick = () => {
  const urls = $('#urls').value.split(/\s+/).filter(Boolean);
  start('search', urls.map(u => ({ result: { provider: 'url', id: u.split('/').pop().split('?')[0] || 'clip', url: u, title: u, author: '', license: 'unknown - check the source', licenseUrl: '', page: u }, label: u, name: u })));
};

const NAMES = { convert: 'Convert', gif2mp4: 'GIF → MP4', loop: 'Make loops', search: 'Find loops' };
let rT;
function render() { clearTimeout(rT); rT = setTimeout(draw, 60); }
function draw() {
  if (!JOBS.length) { $('#jobs').innerHTML = '<p class="muted">No jobs yet.</p>'; return; }
  $('#jobs').innerHTML = JOBS.map(j => {
    const run = j.status === 'running' || j.status === 'queued', fl = j.results.filter(r => r.name !== 'credits.csv' || !j.dir);
    return `<div class="card job" data-id="${j.id}"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b>${NAMES[j.tool]}</b><span class="chip ${j.status}">${j.status}</span><span class="muted">${new Date(j.created).toLocaleTimeString()}</span><span class="muted">${j.finished}/${j.total}</span>${j.failed ? `<span class="warn">${j.failed} failed</span>` : ''}</div>
 ${run ? `<div class="bar"><i style="width:${Math.round(j.progress * 100)}%"></i></div><div class="cur">${esc(j.current)}${j.status === 'queued' ? 'Waiting in line…' : ''}</div>` : ''}
 ${j.error ? `<div class="warn">${esc(j.error)}</div>` : ''}
 ${j.dir ? `<div class="muted" style="font-size:.85rem">Saved into the folder you chose.</div>` : ''}
 <div class="res">${fl.slice(0, 8).map(r => r.blob ? `<a href="${blobUrl(r)}" download="${esc(r.name)}">${esc(r.name)} <small class="muted">${fmtSize(r.size)}</small></a>` : `<a style="pointer-events:none">${esc(r.name)} <small class="muted">${fmtSize(r.size)} · saved</small></a>`).join('')}${fl.length > 8 ? `<span class="muted">… and ${fl.length - 8} more</span>` : ''}</div>
 ${j.open && fl[0] && fl[0].blob ? `<video controls loop muted autoplay src="${blobUrl(fl[0])}"></video>` : ''}
 ${j.log.length ? `<details><summary>Log (${j.log.length})</summary><pre>${esc(j.log.slice(-60).join('\n'))}</pre></details>` : ''}
 <div class="acts">${run ? `<button class="sm" data-a="cancel">Stop</button>` : `${fl.some(r => r.blob) ? `<button class="sm" data-a="prev">${j.open ? 'Hide preview' : 'Preview first'}</button><button class="sm" data-a="zip">Download all (zip)</button>${HAS_DIR ? '<button class="sm" data-a="save">Save all to a folder</button>' : ''}` : ''}<button class="sm" data-a="del">Remove from list</button>`}</div></div>`;
  }).join('');
}
const urls = new WeakMap();
function blobUrl(r) { if (!urls.has(r)) urls.set(r, URL.createObjectURL(r.blob)); return urls.get(r); }
setInterval(() => { if (JOBS.some(j => j.dirty)) { JOBS.forEach(j => j.dirty = false); draw(); } }, 400);
$('#jobs').onclick = async e => {
  const b = e.target.closest('[data-a]'); if (!b) return; const j = JOBS.find(x => x.id === +b.closest('.job').dataset.id), a = b.dataset.a;
  if (a === 'cancel') { j.ctl.cancel = true; j.abort.abort(); if (j.status === 'queued') j.status = 'cancelled'; else { engineStatus('Stopping…'); engine.terminate(); engineStatus(''); } }
  if (a === 'del') { j.results.forEach(r => urls.has(r) && URL.revokeObjectURL(urls.get(r))); JOBS.splice(JOBS.indexOf(j), 1); }
  if (a === 'prev') j.open = !j.open;
  if (a === 'zip') { try { const z = await makeZip(j.results.filter(r => r.blob).map(r => ({ name: r.name, blob: r.blob }))), u = URL.createObjectURL(z), l = document.createElement('a'); l.href = u; l.download = `vjtools-${j.id}.zip`; l.click(); setTimeout(() => URL.revokeObjectURL(u), 60000); } catch (er) { $('#msg').textContent = er.message; } }
  if (a === 'save') { try { const d = await window.showDirectoryPicker({ mode: 'readwrite' }); for (const r of j.results.filter(x => x.blob)) { const fh = await d.getFileHandle(r.name, { create: true }), w = await fh.createWritable(); await w.write(r.blob); await w.close(); } $('#msg').textContent = 'Saved.'; } catch (er) { if (er.name !== 'AbortError') $('#msg').textContent = er.message; } }
  draw();
};

// ---------------------------------------------------------------------------------------------- start
function init() {
  $('#tabs').innerHTML = Object.entries(TOOLS).map(([k, v]) => `<button data-t="${k}">${v.label}</button>`).join('');
  $('#tabs').onclick = e => { const b = e.target.closest('[data-t]'); b && setTool(b.dataset.t); };
  SR.status().filter(s => s.ready).forEach(s => SRCSEL.add(s.name)); renderSrcs();
  buildSettings(); setTool(tool); draw();
  window.VJ = { JOBS, FILES, SEL, addFiles, start };   // handy for tests and for poking around in the console
}
init();
