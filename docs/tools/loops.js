// Loop analysis and loop making (port of vjtools/loops.py). Works through the engine interface.
import { chooseFps, encodeArgs, repeatsNeeded, parseProbe } from './core.js';

const W = 64, H = 36, S = W * H;

export async function probe(engine, path, name = '') {
  const { log } = await engine.exec(['-hide_banner', '-i', path]);
  return parseProbe(log.join('\n'), name || path);
}

// grey 64x36 pictures at `fps`, as one Uint8Array (n * S)
export async function grayFrames(engine, path, fps, maxFrames = 20000) {
  const out = `g_${Math.random().toString(36).slice(2)}.raw`;
  const r = await engine.exec(['-y', '-hide_banner', '-i', path, '-an', '-vf', `fps=${fps},scale=${W}:${H}:flags=area,format=gray`, '-frames:v', String(maxFrames), '-f', 'rawvideo', out]);
  if (r.code !== 0) throw new Error('Could not read this video');
  const data = await engine.read(out);
  await engine.remove(out);
  return { data, n: Math.floor(data.length / S) };
}

const meanDiff = (a, ia, b, ib) => { let s = 0; const oa = ia * S, ob = ib * S; for (let i = 0; i < S; i++) s += Math.abs(a[oa + i] - b[ob + i]); return s / S / 255; };
function percentile(sorted, q) { const p = (sorted.length - 1) * q / 100, lo = Math.floor(p), hi = Math.ceil(p); return sorted[lo] + (sorted[hi] - sorted[lo]) * (p - lo); }

export function analyzeFrames(data, n, fps) {
  if (n < 4) return { frames: n, fps, wrap: 0, stepTyp: 0, ratio: 0, isLoop: false, verdict: 'jump', bestCut: -1, bestRatio: 9e9, bestKeeps: 0 };
  const steps = new Float64Array(n - 1);
  for (let i = 0; i < n - 1; i++) steps[i] = meanDiff(data, i, data, i + 1);
  const ref = Math.max(percentile(Float64Array.from(steps).sort(), 90), 0.004);
  const wrap = meanDiff(data, n - 1, data, 0), ratio = wrap / ref;
  const isLoop = ratio <= 1.25 || wrap < 0.01, verdict = isLoop ? 'seamless' : ratio <= 3 ? 'almost' : 'jump';
  const lo = Math.floor(n / 2), len = n - 1 - lo;   // candidates k = lo .. n-2, compare frame k+1 with frame 0
  let best = -1, bestCost = Infinity;
  for (let j = 0; j < len; j++) { const c = meanDiff(data, lo + j + 1, data, 0) + 1e-9 * (len - j); if (c < bestCost) { bestCost = c; best = j; } }
  if (best < 0) return { frames: n, fps, wrap, stepTyp: ref, ratio, isLoop, verdict, bestCut: -1, bestRatio: 9e9, bestKeeps: 0 };
  const k = lo + best, raw = meanDiff(data, k + 1, data, 0);
  return { frames: n, fps, wrap, stepTyp: ref, ratio, isLoop, verdict, bestCut: k, bestRatio: raw / ref, bestKeeps: (k + 1) / n };
}

export function reportText(r) {
  let s = `${r.verdict} (loop jump ${r.ratio.toFixed(1)}x a normal frame step)`;
  if (!r.isLoop && r.bestCut >= 0 && r.bestRatio <= 1.25) s += `; a better loop point exists after frame ${r.bestCut} (keeps ${Math.round(r.bestKeeps * 100)} %)`;
  return s;
}

export async function analyze(engine, path, info) {
  const fps = chooseFps(info, { fps: 0, maxFps: 60 });
  const { data, n } = await grayFrames(engine, path, fps);
  return analyzeFrames(data, n, fps);
}

const normProfile = prof => ({ ...prof, crf: 12, preset: 'ultrafast', gop: 15, minSeconds: 0, keepAudio: false });
const encOut = (fps) => ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '12', '-pix_fmt', 'yuv420p', '-g', '15', '-r', String(fps), '-fps_mode', 'cfr', '-an'];
let uid = 0;
const tmpName = (tag, ext = 'mp4') => `t${Date.now().toString(36)}${++uid}_${tag}.${ext}`;

async function makePingpong(engine, inter, out, fps, ctl, budget = 200e6) {
  const { n: N } = await grayFrames(engine, inter, fps);
  if (N < 3) throw new Error('Clip is too short to make a loop of');
  const info = await probe(engine, inter);
  const per = Math.max(1, Math.floor(info.width * info.height * 3 / 2)), C = Math.max(24, Math.floor(budget / per));
  const parts = [], lo = 1, hi = N - 1, chunks = [];
  for (let a = lo; a < hi; a += C) chunks.push([a, Math.min(a + C, hi)]);
  for (let ci = chunks.length - 1; ci >= 0; ci--) {
    ctl.check();
    const [a, b] = chunks[ci], p = tmpName(`rev${ci}`);
    const ss = a === 0 ? [] : ['-ss', ((a - 0.5) / fps).toFixed(6)];
    const r = await engine.exec(['-y', ...ss, '-i', inter, '-vf', `trim=end_frame=${b - a},reverse,setpts=N/FRAME_RATE/TB`, '-frames:v', String(b - a), ...encOut(fps), p]);
    if (r.code !== 0) throw new Error('Reversing failed (not enough memory? try a smaller size)');
    parts.push(p);
  }
  const list = tmpName('list', 'txt');
  await engine.write(list, new TextEncoder().encode([inter, ...parts].map(p => `file '${p}'\n`).join('')));
  const r = await engine.exec(['-y', '-f', 'concat', '-safe', '0', '-i', list, ...encOut(fps), out]);
  await engine.remove(list); for (const p of parts) await engine.remove(p);
  if (r.code !== 0) throw new Error('Joining the loop failed');
  return 2 * N - 2;
}

async function makeCrossfade(engine, inter, out, fps, fadeS) {
  const { n: N } = await grayFrames(engine, inter, fps);
  const xf = Math.min(Math.round(fadeS * fps), Math.floor(N / 3));
  if (xf < 2) throw new Error('Clip is too short for a crossfaded loop');
  const w = `st(0,(N+1)/${xf + 1});st(1,ld(0)*ld(0)*ld(0)*(ld(0)*(ld(0)*6-15)+10))`;
  const fg = `[0:v]split=2[h0][r0];[h0]trim=end_frame=${xf},setpts=PTS-STARTPTS[head];` +
    `[r0]trim=start_frame=${xf}:end_frame=${N - xf},setpts=PTS-STARTPTS[rest];` +
    `[1:v]trim=end_frame=${xf},setpts=PTS-STARTPTS[tail];` +
    `[tail][head]blend=all_expr='${w};A*(1-ld(1))+B*ld(1)'[bl];[bl][rest]concat=n=2:v=1:a=0[v]`;
  const ss = Math.max(0, (N - xf - 0.5) / fps).toFixed(6);
  const r = await engine.exec(['-y', '-i', inter, '-ss', ss, '-i', inter, '-filter_complex', fg, '-map', '[v]', ...encOut(fps), out]);
  if (r.code !== 0) throw new Error('Crossfade failed');
  return N - xf;
}

// Make dstName a VJ-friendly clip that loops without a visible jump. Returns { note, method }.
export async function makeLoop(engine, src, dst, prof, info, { mode = 'auto', fade = 1.5, fallback = 'pingpong', ctl = { check() {} }, onProgress } = {}) {
  const fps = chooseFps(info, prof);
  const g = await grayFrames(engine, src, fps);
  const rep = analyzeFrames(g.data, g.n, fps);
  let method = mode;
  if (mode === 'auto') {
    if (rep.isLoop) method = 'asis';
    else if (rep.bestCut >= 0 && rep.bestRatio <= 1.25 && rep.bestKeeps >= 0.6) method = 'trim';
    else method = fallback;
  }
  if (method === 'none') method = 'asis';
  if (method === 'trim' && rep.bestCut < 0) method = fallback;
  const weak = method === 'trim' && rep.bestRatio > 1.25;
  const finish = async (from, finfo, frames) => {
    const dur = frames / fps;
    const r = await engine.exec(encodeArgs(from, dst, prof, finfo, { repeat: repeatsNeeded(dur, prof.minSeconds) }), { onProgress });
    if (r.code !== 0) throw new Error('Encoding failed');
  };
  if (method === 'asis') {
    const r = await engine.exec(encodeArgs(src, dst, prof, info, { repeat: repeatsNeeded(info.duration, prof.minSeconds) }), { onProgress });
    if (r.code !== 0) throw new Error('Encoding failed');
    return { note: rep.isLoop ? `already loops (${reportText(rep)})` : `kept as is (${reportText(rep)})`, method: 'asis' };
  }
  const tmps = [];
  try {
    ctl.check();
    const inter = tmpName('norm'); tmps.push(inter);
    const ir = await engine.exec(encodeArgs(src, inter, normProfile(prof), info, { trimFrames: method === 'trim' ? rep.bestCut + 1 : 0 }));
    if (ir.code !== 0) throw new Error('Encoding failed');
    let frames, from;
    if (method === 'trim') { frames = rep.bestCut + 1; from = inter; }
    else {
      from = tmpName('loop'); tmps.push(from);
      frames = method === 'pingpong' ? await makePingpong(engine, inter, from, fps, ctl) : method === 'crossfade' ? await makeCrossfade(engine, inter, from, fps, fade) : (() => { throw new Error('Unknown loop mode ' + mode); })();
    }
    ctl.check();
    await finish(from, await probe(engine, from), frames);
  } finally { for (const t of tmps) await engine.remove(t); }
  const what = { trim: `trimmed to the best loop point (frame ${rep.bestCut})` + (weak ? ' - no really good point exists, a small jump may remain' : ''), pingpong: 'played forward then backward', crossfade: `end crossfaded into the start (${fade} s)` }[method];
  return { note: `${what}; before: ${reportText(rep)}`, method };
}
