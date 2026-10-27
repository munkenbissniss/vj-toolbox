"""The batch tools: convert, gif2mp4, loops. Each one takes files + options and reports progress through a callback."""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .core import (Ctl, Cancelled, Profile, Naming, probe, encode, repeats_needed, collect, make_name, out_ext,
                   VIDEO_EXT, GIF_EXT, need)
from . import loops as L


def _prepare(files, outdir, nm: Naming, prof: Profile, overwrite):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    taken = set()
    if not overwrite:
        taken = {p.stem.lower() for p in outdir.iterdir() if p.is_file()}
    plan = []
    for i, f in enumerate(files):
        try:
            info = probe(f)
        except Exception as e:
            plan.append(dict(src=f, info=None, dst=None, error=str(e).splitlines()[0] if str(e) else "unreadable"))
            continue
        base = make_name(f, i, nm, info, taken)
        plan.append(dict(src=f, info=info, dst=outdir / (base + out_ext(prof)), error=None))
    return plan


def preview_names(files, outdir, nm: Naming, prof: Profile, probe_files=True):
    """Dry run of the naming: returns [(source name, new name)] without touching any file."""
    taken = set()
    od = Path(outdir) if outdir else None
    if od and od.is_dir():
        taken = {p.stem.lower() for p in od.iterdir() if p.is_file()}
    rows = []
    for i, f in enumerate(files):
        info = None
        if probe_files:
            try:
                info = probe(f)
            except Exception:
                info = None
        rows.append((Path(f).name, make_name(f, i, nm, info, taken) + out_ext(prof)))
    return rows


def run_batch(files, outdir, prof: Profile, nm: Naming, fn, ctl: Ctl | None = None, cb=None, jobs=2, overwrite=False):
    """fn(src, dst, info) -> note. Returns a list of result dicts."""
    need("ffmpeg"); need("ffprobe")
    ctl = ctl or Ctl()
    cb = cb or (lambda e: None)
    plan = _prepare(files, outdir, nm, prof, overwrite)
    n = len(plan)
    results = [None] * n
    done = [0]

    def one(i):
        p = plan[i]
        name = Path(p["src"]).name
        if ctl.cancel:
            results[i] = dict(src=str(p["src"]), status="cancelled")
            return
        if p["error"]:
            results[i] = dict(src=str(p["src"]), status="failed", note=f"Cannot read: {p['error']}")
            cb(dict(i=i, n=n, name=name, status="failed", note=results[i]["note"], done=done[0] + 1))
            done[0] += 1
            return
        cb(dict(i=i, n=n, name=name, status="running", done=done[0]))
        t0 = time.time()
        tmpdst = p["dst"].with_name("." + p["dst"].name)
        tmpdst = tmpdst.with_suffix(p["dst"].suffix)  # keep a real extension so ffmpeg picks the container
        try:
            note = fn(p["src"], tmpdst, p["info"], ctl)
            os.replace(tmpdst, p["dst"])
            results[i] = dict(src=str(p["src"]), dst=str(p["dst"]), status="done", note=note, seconds=round(time.time() - t0, 1),
                              size=p["dst"].stat().st_size)
        except Cancelled:
            results[i] = dict(src=str(p["src"]), status="cancelled")
        except Exception as e:
            results[i] = dict(src=str(p["src"]), status="failed", note=str(e)[-400:])
        finally:
            try:
                if tmpdst.exists():
                    tmpdst.unlink()
            except OSError:
                pass
        done[0] += 1
        r = results[i]
        cb(dict(i=i, n=n, name=name, status=r["status"], note=r.get("note", ""), dst=r.get("dst", ""), done=done[0]))

    with ThreadPoolExecutor(max(1, jobs)) as ex:
        list(ex.map(one, range(n)))
    return results


def convert(files, outdir, prof: Profile, nm: Naming, **kw):
    def fn(src, dst, info, ctl):
        encode(str(src), str(dst), prof, info, ctl, repeat=repeats_needed(info.duration, prof.min_seconds))
        return f"{info.width}x{info.height} {info.codec} -> {prof.codec}" + (" (HDR to SDR)" if info.hdr and prof.tonemap else "")
    return run_batch(files, outdir, prof, nm, fn, **kw)


def gif2mp4(files, outdir, prof: Profile, nm: Naming, **kw):
    files = [f for f in files if Path(f).suffix.lower() in GIF_EXT]
    return convert(files, outdir, prof, nm, **kw)


def make_loops(files, outdir, prof: Profile, nm: Naming, mode="auto", fade=1.5, fallback="pingpong", **kw):
    def fn(src, dst, info, ctl):
        note, _ = L.make_loop(str(src), str(dst), prof, mode, fade, fallback, ctl)
        return note
    return run_batch(files, outdir, prof, nm, fn, **kw)


def download_and_convert(items, outdir, prof: Profile, nm: Naming, loopify="none", fade=1.5, ctl: Ctl | None = None, cb=None,
                         jobs=2, overwrite=False):
    """items: list of search.Result objects or plain URLs (str). Downloads them, converts to the VJ profile,
    optionally makes them loop, and writes credits.csv. Returns result dicts."""
    import shutil
    import tempfile
    from . import search as S
    ctl = ctl or Ctl()
    cb = cb or (lambda e: None)
    raw = Path(tempfile.mkdtemp(prefix="vjdl_"))
    srcs, meta, results = [], {}, []
    n = len(items)
    try:
        for i, it in enumerate(items):
            ctl.check()
            label = it if isinstance(it, str) else it.title
            cb(dict(i=i, n=n, name=label[:60], status="running", note="downloading", done=i, phase="download"))
            try:
                p = S.fetch_url(it, raw, ctl) if isinstance(it, str) else S.fetch_result(it, raw, ctl)
                srcs.append(p)
                meta[str(p)] = it
            except Cancelled:
                raise
            except Exception as e:
                results.append(dict(src=label, status="failed", note=f"Download failed: {e}"))
                cb(dict(i=i, n=n, name=label[:60], status="failed", note=str(e)[-200:], done=i + 1, phase="download"))
        if srcs and not ctl.cancel:
            cb(dict(i=0, n=len(srcs), name="", status="running", note="converting", done=0, phase="convert"))
            if loopify and loopify != "none":
                res = make_loops(srcs, outdir, prof, nm, mode=loopify, fade=fade, ctl=ctl, cb=cb, jobs=jobs, overwrite=overwrite)
            else:
                res = convert(srcs, outdir, prof, nm, ctl=ctl, cb=cb, jobs=jobs, overwrite=overwrite)
            rows = []
            for r in res:
                it = meta.get(r["src"])
                if r.get("status") == "done" and it is not None and not isinstance(it, str):
                    rows.append([Path(r["dst"]).name, it.title, it.author, it.license, it.license_url, it.page,
                                 time.strftime("%Y-%m-%d")])
                elif r.get("status") == "done" and isinstance(it, str):
                    rows.append([Path(r["dst"]).name, "", "", "unknown - check the source", "", it, time.strftime("%Y-%m-%d")])
            if rows:
                S.write_credits(outdir, rows)
            results += res
    finally:
        shutil.rmtree(raw, ignore_errors=True)
    return results
