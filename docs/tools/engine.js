// The video engine: ffmpeg compiled to WebAssembly, running in a worker inside the page.
// The app only talks to this small interface, so the engine can be swapped (the tests plug in a native ffmpeg).
//
//   engine.load(onStatus)              start the engine (downloads ffmpeg-core.wasm the first time)
//   engine.exec(args, {onProgress})    run ffmpeg -> { code, log }       (all paths are relative to the engine's own file system)
//   engine.write(path, uint8)          put a file in the engine
//   engine.read(path)                  get a file out -> Uint8Array
//   engine.remove(path)                delete a file
//   engine.mount(file) / unmount(p)    let ffmpeg read a File from disk without copying it into memory
//   engine.terminate()                 stop at once (used by "Stop"); the next call starts it again

const VENDOR = new URL('./vendor/', import.meta.url);

export class WasmEngine {
  constructor() { this.ff = null; this.cur = null; this.mounts = 0; this.loading = null; }

  async load(onStatus = () => {}) {
    if (this.ff) return;
    if (this.loading) return this.loading;
    this.loading = (async () => {
      onStatus('Loading the video engine (about 30 MB, only the first time)…');
      let mod;
      try { mod = await import(new URL('ffmpeg/index.js', VENDOR).href); }
      catch (e) {
        throw new Error('The video engine files are missing. Run  python3 get-ffmpeg.py  in the repository once (it downloads ffmpeg.wasm into docs/tools/vendor), then reload this page.');
      }
      const ff = new mod.FFmpeg();
      ff.on('log', ({ message }) => { if (this.cur) this.cur.log.push(message); });
      ff.on('progress', ({ progress }) => { if (this.cur && this.cur.onProgress && progress >= 0 && progress <= 1) this.cur.onProgress(progress); });
      await ff.load({ coreURL: new URL('core/ffmpeg-core.js', VENDOR).href, wasmURL: new URL('core/ffmpeg-core.wasm', VENDOR).href });
      this.ff = ff;
    })();
    try { await this.loading; } finally { this.loading = null; }
  }

  async exec(args, { onProgress } = {}) {
    await this.load();
    this.cur = { log: [], onProgress };
    let code;
    try { code = await this.ff.exec(args); }
    finally { var log = this.cur.log; this.cur = null; }
    return { code, log };
  }
  async write(path, data) { await this.load(); await this.ff.writeFile(path, data); }
  async read(path) { await this.load(); return await this.ff.readFile(path); }
  async remove(path) { try { await this.ff.deleteFile(path); } catch (e) { /* already gone */ } }

  async mount(file) {
    await this.load();
    const dir = `m${++this.mounts}`;
    await this.ff.createDir('/' + dir);
    await this.ff.mount('WORKERFS', { files: [file] }, '/' + dir);
    return `${dir}/${file.name}`;
  }
  async unmount(path) {
    const dir = path.split('/')[0];
    try { await this.ff.unmount('/' + dir); await this.ff.deleteDir('/' + dir); } catch (e) { /* ignore */ }
  }
  terminate() { try { this.ff && this.ff.terminate(); } catch (e) { /* ignore */ } this.ff = null; this.cur = null; }
}

export function getEngine() { return window.__VJ_ENGINE__ || new WasmEngine(); }
