"""Browser test of docs/tools. The video engine (ffmpeg.wasm) is replaced by the computer's own ffmpeg through a small
bridge, so this tests the whole app (UI, naming, loop logic, search, zip) against real ffmpeg.
Run:  python3 tests/web/ui_test.py      (needs: playwright for Python + Chromium, ffmpeg)"""
import base64, http.server, json, os, re, shutil, subprocess, sys, tempfile, threading, functools, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import mockserver
from playwright.sync_api import sync_playwright

T = Path(tempfile.mkdtemp(prefix="vjweb_"))
IN, FS = T / "in", T / "fs"
IN.mkdir(); FS.mkdir()

def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *a], check=True)

ff("-f", "lavfi", "-i", "testsrc2=size=200x120:rate=10:duration=2", "-vf", "format=pal8", str(IN / "Ærlig gif 1.gif"))
ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(IN / "Mitt Klipp.mov"))
ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=2", "-c:v", "libvpx-vp9", "-b:v", "500k", str(IN / "clip.webm"))
ff("-f", "lavfi", "-i", "gradients=s=320x180:d=4:r=25:speed=0.05:seed=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(IN / "nonloop.mp4"))
ff("-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=4,geq=r='128+100*sin(2*PI*(X/80+T/4))':g='128+100*sin(2*PI*(Y/60+T/4))':b='128+100*sin(2*PI*T/4)'", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(IN / "isloop.mp4"))

# ---- bridge: the "engine" is native ffmpeg working in a folder that plays the role of the engine's file system
def host(op, a=None, b=None):
    if op == "exec":
        args = json.loads(a)
        r = subprocess.run(["ffmpeg", "-nostdin", *args], cwd=FS, capture_output=True)
        log = [l for l in re.split(r"[\r\n]+", r.stderr.decode("utf-8", "replace")) if l]
        return json.dumps({"code": r.returncode, "log": log})
    if op == "write":
        p = FS / a; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(base64.b64decode(b)); return ""
    if op == "read":
        return base64.b64encode((FS / a).read_bytes()).decode()
    if op == "remove":
        (FS / a).unlink(missing_ok=True); return ""
    if op == "rmdir":
        shutil.rmtree(FS / a, ignore_errors=True); return ""
    if op == "ls":
        return json.dumps(sorted(os.listdir(FS)))
    raise ValueError(op)

ENGINE_JS = r"""
(() => {
  const b64 = u8 => { let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000)); return btoa(s); };
  const un64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  let n = 0; window.__execs = 0;
  window.__VJ_ENGINE__ = {
    async load() {}, 
    async exec(args) { window.__execs++; return JSON.parse(await window.host('exec', JSON.stringify(args))); },
    async write(p, u8) { await window.host('write', p, b64(u8)); },
    async read(p) { return un64(await window.host('read', p)); },
    async remove(p) { await window.host('remove', p); },
    async mount(file) { const d = 'm' + (++n); await window.host('write', d + '/' + file.name, b64(new Uint8Array(await file.arrayBuffer()))); return d + '/' + file.name; },
    async unmount(p) { await window.host('rmdir', p.split('/')[0]); },
    terminate() {}
  };
})();
"""

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT / "docs")))
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/tools/index.html"
mock, base = mockserver.start(IN / "clip.webm")

def probe(path, entries="stream=codec_name,width,height,r_frame_rate,pix_fmt:format=duration"):
    return subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)], capture_output=True, text=True).stdout
def frames(path):
    return int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip())

fails = []
def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def results(pg, idx=0):
    """save the result files of job idx to disk, return {name: path}"""
    d = T / f"res{idx}_{len(os.listdir(T))}"; d.mkdir()
    data = pg.evaluate("""async (i) => { const out = {}; for (const r of VJ.JOBS[i].results) if (r.blob) { const u8 = new Uint8Array(await r.blob.arrayBuffer()); let s=''; for (let k=0;k<u8.length;k+=0x8000) s+=String.fromCharCode.apply(null,u8.subarray(k,k+0x8000)); out[r.name]=btoa(s);} return out; }""", idx)
    paths = {}
    for n, b in data.items():
        (d / n).write_bytes(base64.b64decode(b)); paths[n] = d / n
    return paths

def wait_job(pg, n_done, timeout=180000):
    pg.wait_for_function(f"VJ.JOBS.filter(j => ['done','failed','cancelled'].includes(j.status)).length >= {n_done}", timeout=timeout)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1180, "height": 900}, accept_downloads=True)
    ctx.add_init_script(ENGINE_JS)
    ctx.add_init_script(f"window.VJ_BASES = {json.dumps({'pexels': base + '/pexels', 'pixabay': base + '/pixabay', 'archive': base, 'commons': base + '/commons', 'nasa': base + '/nasa', 'prelinger': base})};"
                        "localStorage.setItem('vj_key_pexels','testkey');localStorage.setItem('vj_key_pixabay','testkey');")
    pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "ERR_" not in m.text and "Failed to load resource" not in m.text and "blocked by CORS" not in m.text else None)
    pg.expose_function("host", host)
    pg.goto(URL); pg.wait_for_selector("#groups details")

    # ---------- Convert
    pg.set_input_files("#picker", [str(f) for f in sorted(IN.iterdir())])
    pg.wait_for_selector("#files .vt")
    check(pg.locator("#files .vt").count() == 5, "5 files added")
    pg.fill("#o_template", "vj_{n3}_{name}"); pg.select_option("#o_case", "lower"); pg.select_option("#o_preset", "ultrafast")
    pg.wait_for_timeout(500)
    prev = pg.inner_text("#nprev")
    check("vj_001_aerlig_gif_1.mp4" in prev, "live naming preview: " + prev.splitlines()[-1])
    pg.click("#go"); wait_job(pg, 1)
    j = pg.evaluate("({s: VJ.JOBS[0].status, f: VJ.JOBS[0].failed, n: VJ.JOBS[0].results.length})")
    check(j == {"s": "done", "f": 0, "n": 5}, f"convert job done {j}")
    res = results(pg)
    check(sorted(res) == ["vj_001_aerlig_gif_1.mp4", "vj_002_clip.mp4", "vj_003_isloop.mp4", "vj_004_mitt_klipp.mp4", "vj_005_nonloop.mp4"], f"names {sorted(res)}")
    for n, path in res.items():
        i = json.loads(probe(path))["streams"][0]
        check(i["codec_name"] == "h264" and i["pix_fmt"] == "yuv420p" and i["width"] % 2 == 0, f"{n}: {i['codec_name']} {i['width']}x{i['height']}")
    check(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(res["vj_004_mitt_klipp.mp4"])], capture_output=True, text=True).stdout.strip() == "", "no sound in output")
    pg.screenshot(path=str(T / "web1.png"), full_page=True)

    # zip
    with pg.expect_download() as dl: pg.click("#jobs [data-a=zip]")
    zp = T / "dl.zip"; dl.value.save_as(zp)
    z = zipfile.ZipFile(zp); check(z.testzip() is None and len(z.namelist()) == 5, f"zip with {len(z.namelist())} files is valid")

    # ---------- GIF tab
    pg.click("#tabs [data-t=gif2mp4]")
    pg.set_input_files("#picker", [str(f) for f in sorted(IN.iterdir())])
    pg.wait_for_selector("#files .vt")
    check(pg.locator("#files .vt").count() == 1, "gif tab keeps only the gif")
    check("skipped" in pg.inner_text("#msg"), "tells that others were skipped: " + pg.inner_text("#msg"))
    pg.fill("#o_minSeconds", "5") if False else pg.evaluate("document.querySelector('#o_minSeconds').value=5;document.querySelector('#o_minSeconds').dispatchEvent(new Event('input',{bubbles:true}))")
    pg.fill("#o_template", "{name}"); pg.click("#go"); wait_job(pg, 2)
    res = results(pg, 0)
    dur = float(json.loads(probe(res["aerlig_gif_1.mp4"]))["format"]["duration"])
    check(dur >= 5, f"gif repeated to at least 5 s ({dur:.1f})")
    pg.evaluate("document.querySelector('#o_minSeconds').value=0;document.querySelector('#o_minSeconds').dispatchEvent(new Event('input',{bubbles:true}))")

    # ---------- Loop tab
    pg.click("#tabs [data-t=loop]")
    pg.set_input_files("#picker", [str(IN / "nonloop.mp4"), str(IN / "isloop.mp4")])
    pg.wait_for_selector("#files .vt")
    pg.click("#checkloops"); pg.wait_for_function("document.querySelector('#chkmsg').textContent.includes('Done')", timeout=60000)
    tags = pg.eval_on_selector_all("#files .tag", "e => e.map(x => x.textContent)")
    check(sorted(tags) == ["jump", "loops ✓"], f"loop check tags {tags}")
    pg.fill("#o_template", "{name}_loop")
    pg.click("#go"); wait_job(pg, 3)
    res = results(pg, 0)
    check(sorted(res) == ["isloop_loop.mp4", "nonloop_loop.mp4"], f"loop outputs {sorted(res)}")
    check(frames(res["nonloop_loop.mp4"]) == 198, f"pingpong frames {frames(res['nonloop_loop.mp4'])} (expected 198)")
    check(frames(res["isloop_loop.mp4"]) == 100, "good loop left alone (100 frames)")
    log = pg.evaluate("VJ.JOBS[0].log.join('\\n')")
    check("forward then backward" in log and "already loops" in log, "log explains what happened")
    # crossfade mode with explicit length
    pg.select_option("#o_mode", "crossfade"); pg.evaluate("const e=document.querySelector('#o_fade');e.value=1;e.dispatchEvent(new Event('input',{bubbles:true}))")
    pg.click("#go"); wait_job(pg, 4)
    res = results(pg, 0)
    check(frames(res["nonloop_loop.mp4"]) == 75, f"crossfade frames {frames(res['nonloop_loop.mp4'])} (expected 75)")
    pg.screenshot(path=str(T / "web2.png"), full_page=True)

    # ---------- Search
    pg.click("#tabs [data-t=search]")
    pg.fill("#q", "tunnel"); pg.click("#dosearch"); pg.wait_for_selector("#results .rc")
    check(pg.locator("#results .rc").count() == 6, "6 results from 6 sources: " + pg.inner_text("#smsg"))
    pg.click("#more"); pg.wait_for_function("!document.querySelector('#more').disabled", timeout=20000)
    check(pg.locator("#results .rc").count() >= 6, "load more keeps results: " + pg.inner_text("#smsg"))
    pg.click("#rall"); pg.select_option("#o_loopify", "pingpong"); pg.fill("#o_template", "stock_{n2}")
    pg.click("#go"); wait_job(pg, 5)
    res = results(pg, 0)
    check(sorted(res) == ["credits.csv"] + [f"stock_0{i}.mp4" for i in range(1, 7)], f"search outputs {sorted(res)}")
    csv = res["credits.csv"].read_text(encoding="utf-8")
    check("Ann Artist" in csv and "CC BY-SA 4.0" in csv, "credits.csv has author and licence")
    check(frames(res["stock_01.mp4"]) == 2 * 50 - 2, f"downloaded clip made into pingpong ({frames(res['stock_01.mp4'])} frames)")
    # a download the site does not allow from a web page (no CORS headers)
    pg.fill("#urls", f"{base}/nocors/files/x.mp4 {base}/files/ok.mp4"); pg.select_option("#o_loopify", "none")
    pg.click("#fetchgo"); wait_job(pg, 6)
    j = pg.evaluate("({s: VJ.JOBS[0].status, f: VJ.JOBS[0].failed, log: VJ.JOBS[0].log.join('\\n')})")
    check(j["f"] == 1 and "does not let a web page download" in j["log"], "blocked download gives a friendly message")
    check(j["s"] == "done", "other link still converted")
    pg.screenshot(path=str(T / "web3.png"), full_page=True)

    # ---------- Stop
    pg.click("#tabs [data-t=convert]")
    pg.evaluate("VJ.SEL.convert.size")  # keep selection
    pg.click("#go"); pg.wait_for_selector("#jobs [data-a=cancel]")
    pg.click("#jobs [data-a=cancel]"); wait_job(pg, 7)
    st = pg.evaluate("VJ.JOBS[0].status")
    check(st in ("cancelled", "done"), f"stop works ({st})")
    check(not errs, f"no page errors {errs}")
    # ---------- without the vendored engine files the user gets a clear instruction
    pg2 = b.new_context(accept_downloads=True).new_page()
    pg2.goto(URL); pg2.wait_for_selector("#groups details")
    pg2.set_input_files("#picker", [str(IN / "clip.webm")]); pg2.wait_for_selector("#files .vt"); pg2.click("#go")
    pg2.wait_for_function("VJ.JOBS[0].status === 'failed'", timeout=20000)
    msg = pg2.evaluate("VJ.JOBS[0].error")
    check("get-ffmpeg.py" in msg, "missing engine files -> clear instruction: " + msg[:70])
    b.close()
print("\nFAILED:" if fails else "\nALL OK", fails or "")
shutil.copy(T / "web1.png", "/tmp/claude-0/-home-claude/dd37ff87-9a4d-568f-acf3-3577471a1e1d/scratchpad/web1.png") if os.path.isdir("/tmp/claude-0") else None
shutil.copy(T / "web3.png", "/tmp/claude-0/-home-claude/dd37ff87-9a4d-568f-acf3-3577471a1e1d/scratchpad/web3.png") if os.path.isdir("/tmp/claude-0") else None
sys.exit(1 if fails else 0)
