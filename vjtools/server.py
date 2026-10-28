"""Local web interface for VJ Toolbox (Flask). Runs on your own computer; files stay on it."""
from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, fields
from pathlib import Path

try:
    from flask import Flask, request, jsonify, send_file, abort, Response
    from werkzeug.utils import secure_filename
except ImportError:
    sys.exit("Flask is missing. Install it with: pip install flask")

from . import __version__, tools, loops, search as S
from .core import (Profile, Naming, Ctl, VIDEO_EXT, GIF_EXT, collect, probe, has_encoder, has_filter, fmt_size)

HERE = Path(__file__).resolve().parent
PROFILE_KEYS = {f.name: f.type for f in fields(Profile)}
NAMING_KEYS = {f.name: f.type for f in fields(Naming)}


def cast(cls_keys, d, cls):
    kw = {}
    defaults = cls()
    for k, v in (d or {}).items():
        if k not in cls_keys or v is None or v == "":
            continue
        base = getattr(defaults, k)
        if isinstance(base, bool):
            kw[k] = bool(v)
        elif isinstance(base, int):
            kw[k] = int(float(v))
        elif isinstance(base, float):
            kw[k] = float(v)
        else:
            kw[k] = str(v)
    obj = cls(**kw)
    return obj


def make_app(data_dir):
    DATA = Path(data_dir)
    UP, OUT, TH = DATA / "uploads", DATA / "out", DATA / "thumbs"
    for d in (UP, OUT, TH):
        d.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3
    JOBS, CTLS, Q, LOCK = {}, {}, queue.Queue(), threading.Lock()

    def media_path(p, exts=VIDEO_EXT):
        f = Path(p).expanduser()
        if not f.is_file() or f.suffix.lower() not in exts:
            abort(404)
        return f

    # ------------------------------------------------------------------ info
    @app.get("/")
    def index():
        return send_file(HERE / "static" / "index.html")

    @app.get("/api/info")
    def info():
        return jsonify(version=__version__, ffmpeg=bool(shutil.which("ffmpeg")), hap=has_encoder("hap"),
                       hdr=has_filter("zscale") and has_filter("tonemap"), sources=S.provider_status(),
                       yt_dlp=bool(shutil.which("yt-dlp")), data=str(DATA), home=str(Path.home()),
                       profile=asdict(Profile()), naming=asdict(Naming()))

    # ------------------------------------------------------------------ files
    @app.post("/api/upload")
    def upload():
        out = []
        for f in request.files.getlist("file"):
            name = secure_filename(f.filename or "")
            if Path(name).suffix.lower() not in VIDEO_EXT:
                continue
            d = UP / uuid.uuid4().hex[:8]
            d.mkdir()
            # keep the original name (safe version) so {name} in the naming template still makes sense
            orig = Path(f.filename or name).name
            dst = d / (secure_filename(orig) or name)
            f.save(dst)
            out.append(dict(path=str(dst), name=orig, size=dst.stat().st_size))
        return jsonify(files=out)

    @app.get("/api/scan")
    def scan():
        p = Path(request.args.get("path", "")).expanduser()
        exts = GIF_EXT if request.args.get("gif") == "1" else VIDEO_EXT
        if not p.exists():
            return jsonify(error="That folder or file does not exist on this computer"), 400
        files = collect([p], exts, request.args.get("recursive", "1") == "1")
        return jsonify(files=[dict(path=str(f), name=f.name, size=f.stat().st_size) for f in files[:2000]], truncated=len(files) > 2000)

    @app.get("/api/thumb")
    def thumb():
        f = media_path(request.args.get("path", ""))
        key = hashlib.md5(f"{f}{f.stat().st_mtime}".encode()).hexdigest()
        t = TH / f"{key}.jpg"
        if not t.exists():
            try:
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-ss", "0.3", "-i", str(f), "-frames:v", "1",
                                "-vf", "scale=320:-2", "-q:v", "4", str(t)], check=True, capture_output=True, timeout=30)
                if not t.exists():
                    raise RuntimeError
            except Exception:
                try:
                    subprocess.run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(f), "-frames:v", "1", "-vf", "scale=320:-2",
                                    str(t)], check=True, capture_output=True, timeout=30)
                except Exception:
                    abort(404)
        return send_file(t, mimetype="image/jpeg", max_age=3600)

    @app.get("/api/file")
    def file():
        return send_file(media_path(request.args.get("path", "")), conditional=True)

    @app.post("/api/probe")
    def do_probe():
        b = request.get_json(force=True)
        f = media_path(b.get("path", ""))
        i = probe(str(f))
        out = dict(width=i.width, height=i.height, fps=round(i.fps, 2), duration=round(i.duration, 2), codec=i.codec, hdr=i.hdr,
                   alpha=i.alpha)
        if b.get("loop"):
            try:
                r = loops.analyze(str(f))
                out["loop"] = dict(verdict=r.verdict, text=r.text(), ratio=round(r.ratio, 2), is_loop=r.is_loop)
            except Exception as e:
                out["loop"] = dict(verdict="?", text=str(e)[:100])
        return jsonify(out)

    @app.post("/api/naming-preview")
    def naming_preview():
        b = request.get_json(force=True)
        files = [str(media_path(p, VIDEO_EXT)) for p in b.get("files", [])[:300]]
        prof, nm = cast(PROFILE_KEYS, b.get("profile"), Profile), cast(NAMING_KEYS, b.get("naming"), Naming)
        return jsonify(rows=tools.preview_names(files, b.get("outdir"), nm, prof))

    # ------------------------------------------------------------------ search
    @app.get("/api/search")
    def search():
        a = request.args
        sources = [s for s in a.get("sources", "").split(",") if s] or None
        res, errs = S.search(a.get("q", ""), sources, int(a.get("n", 12)), int(a.get("page", 1)), a.get("loop", "1") == "1",
                             int(a.get("min_height", 0) or 0), float(a.get("min_s", 0) or 0), float(a.get("max_s", 0) or 0),
                             a.get("orientation", ""), pixabay_type=a.get("ptype", ""))
        return jsonify(results=[asdict(r) for r in res], errors=errs)

    @app.post("/api/config")
    def config():
        b = request.get_json(force=True)
        cfg = S.load_config()
        for k in ("pexels", "pixabay"):
            if b.get(k) is not None:
                cfg[k + "_key"] = str(b[k]).strip()
        S.save_config(cfg)
        return jsonify(sources=S.provider_status())

    # ------------------------------------------------------------------ jobs
    def save(j):
        try:
            (OUT / j["id"] / "job.json").write_text(json.dumps({k: v for k, v in j.items() if k != "_n"}, default=str))
        except OSError:
            pass

    for f in sorted(OUT.glob("*/job.json")):
        try:
            j = json.loads(f.read_text())
            if j["status"] in ("queued", "running"):
                j["status"] = "failed"
                j["error"] = "Stopped by restart"
            JOBS[j["id"]] = j
        except Exception:
            pass

    def run_job(j):
        ctl = CTLS[j["id"]]
        b = j["_req"]
        prof, nm = cast(PROFILE_KEYS, b.get("profile"), Profile), cast(NAMING_KEYS, b.get("naming"), Naming)
        lp = b.get("loop") or {}
        outdir = j["outdir"]

        def cb(e):
            with LOCK:
                if e.get("phase") == "convert":
                    j["_conv"] = True
                off = j.get("_dl", 0) if (j.get("_conv") and not e.get("phase")) else 0
                j["done"] = e.get("done", 0) + off
                if e["status"] in ("done", "failed"):
                    j["log"].append(f"{'OK ' if e['status'] == 'done' else 'ERR'} {e['name']}  {e.get('note', '')}"[:300])
                if e["status"] == "running" and e.get("name"):
                    j["current"] = e["name"]
        kw = dict(ctl=ctl, cb=cb, jobs=int(b.get("jobs") or 2), overwrite=bool(b.get("overwrite")))
        tool = j["tool"]
        if tool in ("search", "fetch"):
            if tool == "search":
                its = []
                for d in b.get("items", []):
                    allowed = {k: v for k, v in d.items() if k in S.Result.__dataclass_fields__}
                    its.append(S.Result(**allowed))
            else:
                its = [u for u in b.get("urls", []) if str(u).strip()]
            j["total"] = len(its) * 2
            j["_dl"] = len(its)
            res = tools.download_and_convert(its, outdir, prof, nm, lp.get("mode", "none"), float(lp.get("fade", 1.5)), **kw)
        else:
            files = [str(media_path(p)) for p in b.get("files", [])]
            j["total"] = len(files)
            if tool == "gif2mp4":
                res = tools.gif2mp4(files, outdir, prof, nm, **kw)
            elif tool == "loop":
                res = tools.make_loops(files, outdir, prof, nm, mode=lp.get("mode", "auto"), fade=float(lp.get("fade", 1.5)),
                                       fallback=lp.get("fallback", "pingpong"), **kw)
            else:
                res = tools.convert(files, outdir, prof, nm, **kw)
        j["results"] = res
        return res

    def worker():
        while True:
            jid = Q.get()
            j = JOBS.get(jid)
            if not j or j["status"] != "queued":
                continue
            j["status"] = "running"
            j["started"] = time.time()
            try:
                run_job(j)
                if CTLS[jid].cancel:
                    j["status"] = "cancelled"
                else:
                    bad = sum(1 for r in j.get("results", []) if r and r.get("status") == "failed")
                    j["status"] = "failed" if bad and bad == len(j["results"]) else "done"
                    j["failed"] = bad
            except Exception as e:
                j["status"] = "cancelled" if CTLS[jid].cancel else "failed"
                j["error"] = str(e)[-500:]
            j["ended"] = time.time()
            save(j)
    threading.Thread(target=worker, daemon=True).start()

    @app.post("/api/jobs")
    def new_job():
        b = request.get_json(force=True)
        tool = b.get("tool")
        if tool not in ("convert", "gif2mp4", "loop", "search", "fetch"):
            return jsonify(error="Unknown tool"), 400
        if tool in ("convert", "gif2mp4", "loop") and not b.get("files"):
            return jsonify(error="Add some files first"), 400
        if tool == "search" and not b.get("items"):
            return jsonify(error="Select some search results first"), 400
        if tool == "fetch" and not [u for u in b.get("urls", []) if str(u).strip()]:
            return jsonify(error="Paste at least one link"), 400
        if not shutil.which("ffmpeg"):
            return jsonify(error="ffmpeg was not found on this computer (Ubuntu: sudo apt install ffmpeg)"), 400
        jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        (OUT / jid).mkdir(parents=True)
        od = (b.get("outdir") or "").strip()
        outdir = str(Path(od).expanduser()) if od else str(OUT / jid / "files")
        try:
            Path(outdir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return jsonify(error=f"Cannot use that output folder: {e}"), 400
        n = len(b.get("files") or b.get("items") or b.get("urls") or [])
        j = dict(id=jid, tool=tool, status="queued", created=time.time(), outdir=outdir, total=n, done=0, finished=0, log=[],
                 results=[], _req=b)
        JOBS[jid], CTLS[jid] = j, Ctl()
        Q.put(jid)
        return jsonify(id=jid)

    def view(j):
        out = {k: v for k, v in j.items() if k not in ("_req",)}
        out["log"] = j["log"][-40:]
        tot = max(1, j.get("total", 1))
        out["progress"] = 100 if j["status"] == "done" else round(100 * min(j.get("done", 0), tot) / tot)
        out["files"] = [dict(name=Path(r["dst"]).name, size=r.get("size", 0), note=r.get("note", "")) for r in j.get("results", [])
                        if r and r.get("status") == "done"]
        return out

    @app.get("/api/jobs")
    def jobs():
        return jsonify([view(j) for j in sorted(JOBS.values(), key=lambda j: -j["created"])])

    @app.post("/api/jobs/<jid>/cancel")
    def cancel(jid):
        j = JOBS.get(jid) or abort(404)
        if j["status"] in ("queued", "running"):
            CTLS[jid].stop()
            if j["status"] == "queued":
                j["status"] = "cancelled"
        return jsonify(ok=True)

    @app.delete("/api/jobs/<jid>")
    def del_job(jid):
        j = JOBS.get(jid) or abort(404)
        if j["status"] in ("queued", "running"):
            return jsonify(error="Stop the job first"), 400
        JOBS.pop(jid)
        shutil.rmtree(OUT / jid, ignore_errors=True)
        if request.args.get("files") == "1" and Path(j["outdir"]).is_dir() and str(OUT) in str(Path(j["outdir"]).resolve()):
            shutil.rmtree(j["outdir"], ignore_errors=True)
        return jsonify(ok=True)

    def out_file(jid, name):
        j = JOBS.get(jid) or abort(404)
        f = Path(j["outdir"]) / name
        if name != Path(name).name or not f.is_file():
            abort(404)
        return f

    @app.get("/api/out/<jid>/<name>")
    def out(jid, name):
        return send_file(out_file(jid, name), conditional=True, as_attachment=request.args.get("download") == "1")

    @app.get("/api/jobs/<jid>/zip")
    def zipit(jid):
        j = JOBS.get(jid) or abort(404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for r in j.get("results", []):
                if r and r.get("status") == "done" and Path(r["dst"]).is_file():
                    z.write(r["dst"], Path(r["dst"]).name)
            cr = Path(j["outdir"]) / "credits.csv"
            if cr.is_file():
                z.write(cr, "credits.csv")
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"vjtools-{jid}.zip")

    return app


def serve(host, port, data):
    app = make_app(data)
    print(f"VJ Toolbox: http://{host}:{port}   (data: {data})")
    if host not in ("127.0.0.1", "localhost"):
        print("Warning: there is no password. Only use this on a network you trust.")
    app.run(host=host, port=port, threaded=True)
    return 0
