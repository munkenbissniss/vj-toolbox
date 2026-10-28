// node --test tests/web      (no dependencies)
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as C from '../../docs/tools/core.js';
import { makeZip, crc32 } from '../../docs/tools/zip.js';
import { analyzeFrames } from '../../docs/tools/loops.js';

const nm = o => ({ ...C.defaultNaming(), ...o });

test('cleanName matches the Python version', () => {
  assert.equal(C.cleanName('Ærlig  Åse\'s (final) ØL.mov', nm({})), 'Aerlig_Ases_final_OL.mov');
  assert.equal(C.cleanName('a b', nm({ spaces: '-', case: 'upper' })), 'A-B');
  assert.equal(C.cleanName('Ærlig', nm({ ascii: false })), 'Ærlig');
  assert.equal(C.cleanName('???', nm({})), 'clip');
});

test('templates, numbering and collisions', () => {
  const n = nm({ template: 'loop_{n3}_{name}', prefix: 'vj_', case: 'lower' }), taken = new Set();
  assert.equal(C.makeName('/x/My Clip.mov', 0, n, null, taken), 'vj_loop_001_my_clip');
  assert.equal(C.makeName('/x/My Clip.mov', 0, n, null, taken), 'vj_loop_001_my_clip-2');
  assert.equal(C.makeName('a.mp4', 6, nm({ template: '{n:04}', start: 10 })), '0016');
  assert.equal(C.makeName('a.mp4', 0, nm({ template: '{res}_{fps}_{dur}' }), { width: 1920, height: 1080, fps: 29.97, duration: 12.4 }), '1920x1080_30_12s');
});

test('fps and size rules', () => {
  const P = C.defaultProfile();
  assert.deepEqual(C.targetSize({ width: 3840, height: 2160 }, P), [1920, 1080]);
  assert.deepEqual(C.targetSize({ width: 100, height: 100 }, P), [100, 100]);
  assert.deepEqual(C.targetSize({ width: 100, height: 100 }, { ...P, upscale: true }), [1080, 1080]);
  assert.deepEqual(C.targetSize({ width: 1080, height: 1920 }, { ...P, fit: 'fit' }), [1080, 1920]);
  assert.ok(Math.abs(C.chooseFps({ fps: 29.97 }, P) - 30000 / 1001) < 1e-6);
  assert.equal(C.chooseFps({ fps: 100, isGif: true }, P), 30);
  assert.equal(C.repeatsNeeded(1.9, 5), 3);
  assert.equal(C.repeatsNeeded(10, 5), 1);
});

test('probe parsing of real ffmpeg output', () => {
  const mov = `Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'in.mov':
  Duration: 00:00:03.00, start: 0.000000, bitrate: 9726 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1280x720 [SAR 1:1 DAR 16:9], 9720 kb/s, 30 fps, 30 tbr, 15360 tbn (default)`;
  const i = C.parseProbe(mov, 'in.mov');
  assert.deepEqual([i.width, i.height, i.fps, i.duration, i.codec, i.pix_fmt, i.alpha], [1280, 720, 30, 3, 'h264', 'yuv420p', false]);
  const gif = `  Duration: 00:00:01.90, start: 0.000000, bitrate: 363 kb/s
  Stream #0:0: Video: gif, bgra, 200x120, 10 fps, 10 tbr, 100 tbn`;
  const g = C.parseProbe(gif, 'a.gif');
  assert.equal(g.alpha, true); assert.equal(g.isGif, true); assert.equal(g.width, 200);
  const rot = `Duration: 00:00:05.00, start: 0.0
  Stream #0:0: Video: hevc (Main) (hvc1 / 0x31637668), yuv420p10le(tv, bt709), 1920x1080, 20000 kb/s, 29.97 fps, 29.97 tbr, 600 tbn
  Side data:
    displaymatrix: rotation of -90.00 degrees`;
  const r = C.parseProbe(rot, 'iphone.mov');
  assert.deepEqual([r.width, r.height, r.rotation, r.pix_fmt], [1080, 1920, 270, 'yuv420p10le']);
  assert.throws(() => C.parseProbe('nothing here'));
});

test('encode arguments', () => {
  const P = C.defaultProfile(), info = { width: 3840, height: 2160, fps: 30, alpha: false };
  const a = C.encodeArgs('in.mov', 'out.mp4', P, info, { repeat: 3 });
  assert.deepEqual(a.slice(0, 5), ['-y', '-stream_loop', '2', '-i', 'in.mov']);
  assert.ok(a.join(' ').includes('scale=1920:1080'));
  assert.ok(a.includes('-an') && a.includes('libx264') && a.at(-1) === 'out.mp4');
  const g = C.encodeArgs('a.gif', 'o.mp4', P, { width: 100, height: 100, fps: 12, alpha: true, isGif: true });
  assert.ok(g.includes('-filter_complex') && g.join(' ').includes('drawbox'));
});

test('crc32 and zip are valid', () => {
  assert.equal(crc32(new TextEncoder().encode('123456789')), 0xCBF43926);
});

test('zip opens in Python', async () => {
  const blob = await makeZip([{ name: 'a.txt', blob: new Blob(['hello']) }, { name: 'Ærlig.mp4', blob: new Blob([new Uint8Array(100000).fill(7)]) }]);
  const f = join(mkdtempSync(join(tmpdir(), 'zt')), 't.zip');
  writeFileSync(f, new Uint8Array(await blob.arrayBuffer()));
  const out = execFileSync('python3', ['-c', `import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);assert z.testzip() is None;print(sorted(z.namelist()),len(z.read('Ærlig.mp4')),z.read('a.txt'))`, f]).toString();
  assert.match(out, /a\.txt.*Ærlig\.mp4.*100000.*hello/s);
});

test('loop analysis on synthetic pictures', () => {
  const S = 64 * 36, n = 100;
  const mk = fn => { const d = new Uint8Array(n * S); for (let t = 0; t < n; t++) for (let i = 0; i < S; i++) d[t * S + i] = fn(t, i); return d; };
  const loop = mk((t, i) => 128 + 100 * Math.sin(2 * Math.PI * (i / 200 + t / n)));
  assert.equal(analyzeFrames(loop, n, 25).isLoop, true);
  const ramp = mk((t, i) => Math.min(255, t * 2 + (i % 5)));
  const r = analyzeFrames(ramp, n, 25);
  assert.equal(r.isLoop, false); assert.equal(r.verdict, 'jump');
  // a clip that repeats after 60 frames: best cut is found there
  const rep = mk((t, i) => 128 + 100 * Math.sin(2 * Math.PI * (i / 200 + (t % 60) / 60 * (1))));
  const b = analyzeFrames(rep, n, 25);
  assert.ok(b.bestRatio <= 1.25 && b.bestCut >= 50, JSON.stringify(b));
});
