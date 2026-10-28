// Shared logic for the browser version: profile, probing (from ffmpeg's log), file naming, ffmpeg arguments.
// This is a port of vjtools/core.py, so both versions give the same results.

export const VIDEO_EXT = new Set(['.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi', '.mpg', '.mpeg', '.wmv', '.flv', '.ogv', '.mts', '.m2ts', '.3gp', '.gif', '.apng', '.hevc', '.ts']);
export const GIF_EXT = new Set(['.gif', '.apng']);
export const extOf = n => { const m = /\.[^.\/\\]+$/.exec(n || ''); return m ? m[0].toLowerCase() : ''; };
export const stemOf = n => String(n || '').replace(/^.*[\/\\]/, '').replace(/\.[^.]+$/, '');

export const defaultProfile = () => ({
  size: '1920x1080', fit: 'keep', upscale: false, fps: 0, maxFps: 60, crf: 17, preset: 'veryfast', gop: 30,
  bg: 'black', minSeconds: 0, keepAudio: false,
});
export const defaultNaming = () => ({ template: '{name}', prefix: '', suffix: '', start: 1, ascii: true, spaces: '_', case: 'keep' });

// ---------------------------------------------------------------------------------------------
// probing: ffmpeg has no ffprobe in the browser, so we read what `ffmpeg -i file` prints
// ---------------------------------------------------------------------------------------------
function splitTop(s) {
  const out = []; let depth = 0, cur = '';
  for (const c of s) {
    if (c === '(' || c === '[') depth++;
    if (c === ')' || c === ']') depth--;
    if (c === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += c;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

export function parseProbe(text, name = '') {
  const t = String(text);
  const line = t.split(/\r|\n/).find(l => /Stream #\d+:\d+.*Video:/.test(l));
  if (!line) throw new Error('No video stream found');
  const after = line.slice(line.indexOf('Video:') + 6).trim();
  const parts = splitTop(after);
  const codec = (parts[0] || '').split(/\s+/)[0];
  const pix = (parts[1] || '').replace(/\(.*$/, '').trim();
  const size = parts.map(p => /(\d{2,5})x(\d{2,5})/.exec(p)).find(Boolean);
  const info = { name, width: size ? +size[1] : 0, height: size ? +size[2] : 0, codec, pix_fmt: pix, fps: 0, duration: 0, hasAudio: /Stream #\d+:\d+.*Audio:/.test(t), rotation: 0 };
  const fp = /([\d.]+)\s*fps/.exec(after) || /([\d.]+)\s*tbr/.exec(after);
  info.fps = fp ? parseFloat(fp[1]) : 0;
  const d = /Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)/.exec(t);
  if (d) info.duration = (+d[1]) * 3600 + (+d[2]) * 60 + parseFloat(d[3]);
  const rot = /rotation of (-?[\d.]+) degrees/.exec(t);
  if (rot) info.rotation = ((Math.round(parseFloat(rot[1])) % 360) + 360) % 360;
  if (info.rotation === 90 || info.rotation === 270) [info.width, info.height] = [info.height, info.width];
  info.isGif = codec === 'gif' || codec === 'apng' || GIF_EXT.has(extOf(name));
  info.alpha = info.isGif || /^(yuva|rgba|bgra|argb|abgr|gbrap|pal8|ya8)/.test(pix);
  info.frames = Math.round(info.duration * info.fps);
  return info;
}

// ---------------------------------------------------------------------------------------------
// VJ-friendly profile
// ---------------------------------------------------------------------------------------------
const STD_FPS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60];
const EXACT = { 23.976: 24000 / 1001, 29.97: 30000 / 1001, 59.94: 60000 / 1001 };

export function chooseFps(info, prof) {
  if (prof.fps > 0) return prof.fps;
  let f = info.fps || 30;
  if (info.isGif) f = Math.min(f, 30);
  f = Math.min(f, prof.maxFps);
  const near = STD_FPS.reduce((a, b) => Math.abs(b - f) < Math.abs(a - f) ? b : a);
  if (Math.abs(near - f) < 0.6) return EXACT[near] ?? near;
  return Math.round(f * 1000) / 1000;
}

export function targetSize(info, prof) {
  let [bw, bh] = prof.size.toLowerCase().split('x').map(Number);
  if (!(info.width > 0 && info.height > 0)) return [bw, bh];
  if (info.height > info.width && bw > bh) [bw, bh] = [bh, bw];
  let w, h;
  if (prof.fit === 'fit' || prof.fit === 'fill') { w = bw; h = bh; }
  else {
    let s = Math.min(bw / info.width, bh / info.height);
    if (s > 1 && !prof.upscale) s = 1;
    w = info.width * s; h = info.height * s;
  }
  const even = v => Math.max(2, Math.round(v / 2) * 2);
  return [even(w), even(h)];
}

export function vfChain(info, prof, fps) {
  const [w, h] = targetSize(info, prof);
  const f = [];
  if (info.alpha) f.push(`format=rgba,split[a][b];[a]drawbox=x=0:y=0:w=iw:h=ih:color=${prof.bg}:t=fill[bg];[bg][b]overlay=format=auto`);
  if (prof.fit === 'fill') f.push(`scale=${w}:${h}:force_original_aspect_ratio=increase:flags=lanczos,crop=${w}:${h}`);
  else if (prof.fit === 'fit') f.push(`scale=${w}:${h}:force_original_aspect_ratio=decrease:flags=lanczos,pad=${w}:${h}:(ow-iw)/2:(oh-ih)/2:color=${prof.bg}`);
  else f.push(`scale=${w}:${h}:flags=lanczos`);
  f.push(`fps=${fps}`, 'setsar=1', 'format=yuv420p');
  return f.join(',');
}

export function repeatsNeeded(duration, minSeconds) {
  if (minSeconds <= 0 || duration <= 0 || duration >= minSeconds) return 1;
  return Math.ceil(minSeconds / duration - 1e-9);
}

export function codecArgs(prof) {
  const g = String(Math.max(1, Math.floor(prof.gop)));
  return ['-c:v', 'libx264', '-preset', prof.preset, '-crf', String(prof.crf), '-pix_fmt', 'yuv420p', '-g', g, '-keyint_min', g,
    '-sc_threshold', '0', '-profile:v', 'high', '-movflags', '+faststart', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709'];
}

// full ffmpeg argument list that makes `src` VJ friendly in `dst`
export function encodeArgs(src, dst, prof, info, { repeat = 1, trimFrames = 0 } = {}) {
  const fps = chooseFps(info, prof);
  const a = ['-y'];
  if (repeat > 1) a.push('-stream_loop', String(repeat - 1));
  a.push('-i', src);
  const vf = vfChain(info, prof, fps);
  if (info.alpha) a.push('-filter_complex', `[0:v]${vf}[v]`, '-map', '[v]');
  else a.push('-vf', vf, '-map', '0:v:0');
  if (prof.keepAudio && info.hasAudio) a.push('-map', '0:a:0?', '-c:a', 'aac', '-b:a', '192k');
  else a.push('-an');
  if (trimFrames) a.push('-frames:v', String(Math.floor(trimFrames)));
  a.push('-fps_mode', 'cfr', ...codecArgs(prof), '-map_metadata', '-1', dst);
  return a;
}

// ---------------------------------------------------------------------------------------------
// naming
// ---------------------------------------------------------------------------------------------
const TRANSLIT = { 'æ': 'ae', 'Æ': 'Ae', 'ø': 'o', 'Ø': 'O', 'å': 'a', 'Å': 'A', 'ß': 'ss', 'ð': 'd', 'þ': 'th', 'đ': 'd', 'ł': 'l', 'Ł': 'L', 'œ': 'oe', 'Œ': 'Oe' };

export function cleanName(s, nm) {
  s = String(s);
  if (nm.ascii) {
    s = [...s].map(c => TRANSLIT[c] ?? c).join('').normalize('NFKD').replace(/[^\x00-\x7F]/g, '');
  }
  if (nm.spaces !== 'keep') s = s.trim().replace(/\s+/g, nm.spaces);
  const sp = nm.spaces === 'keep' ? '\\s' : '';
  s = nm.ascii ? s.replace(new RegExp(`[^A-Za-z0-9_\\-.${sp}]`, 'g'), '') : s.replace(new RegExp(`[^\\p{L}\\p{N}_\\-.${sp}]`, 'gu'), '');
  s = s.replace(/([_\-.])\1+/g, '$1').replace(/^[_\-. ]+|[_\-. ]+$/g, '');
  if (nm.case === 'lower') s = s.toLowerCase(); else if (nm.case === 'upper') s = s.toUpperCase();
  return s || 'clip';
}

// index counts from 0. info is optional (fills {w} {h} {res} {fps} {dur}); taken is a Set of lower-case names already used
export function makeName(srcName, index, nm, info = null, taken = null, folder = '') {
  const n = index + Number(nm.start || 1);
  const tok = {
    name: stemOf(srcName), n: String(n), n2: String(n).padStart(2, '0'), n3: String(n).padStart(3, '0'), n4: String(n).padStart(4, '0'),
    folder, date: new Date().toISOString().slice(0, 10).replace(/-/g, ''),
    w: info?.width ? String(info.width) : '', h: info?.height ? String(info.height) : '',
    res: info?.width ? `${info.width}x${info.height}` : '', fps: info?.fps ? String(Math.round(info.fps)) : '',
    dur: info?.duration ? `${Math.round(info.duration)}s` : '',
  };
  const t = (nm.template || '{name}').replace(/\{(\w+)(?::0?(\d+))?\}/g, (m, k, pad) =>
    k === 'n' && pad ? String(n).padStart(+pad, '0') : (k in tok ? tok[k] : m));
  let base = cleanName((nm.prefix || '') + t + (nm.suffix || ''), nm);
  if (taken) {
    let cand = base, k = 2;
    while (taken.has(cand.toLowerCase())) cand = `${base}-${k++}`;
    taken.add(cand.toLowerCase());
    base = cand;
  }
  return base;
}

export const natCompare = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
export const fmtSize = b => b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.round(b / 1e3) + ' kB';
