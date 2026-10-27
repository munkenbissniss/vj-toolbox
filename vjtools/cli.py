"""Command line interface: vj gif2mp4 | convert | loop | search | fetch | ui"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .core import Profile, Naming, collect, VIDEO_EXT, GIF_EXT, Ctl, need, has_encoder
from . import tools


def add_profile_args(ap, gif=False):
    g = ap.add_argument_group("VJ-friendly output")
    g.add_argument("--codec", choices=["h264", "hap", "hap_q", "prores"], default="h264",
                   help="h264 = small, plays everywhere (default). hap/hap_q = Resolume/VDMX/TouchDesigner friendly, huge files. prores = editing")
    g.add_argument("--size", default="1920x1080", help="size box, WIDTHxHEIGHT (portrait material is turned automatically)")
    g.add_argument("--fit", choices=["keep", "fit", "fill"], default="keep",
                   help="keep = keep shape, only shrink to the box | fit = letterbox to exactly the box | fill = crop to exactly the box")
    g.add_argument("--upscale", action="store_true", help="with --fit keep: also enlarge small videos up to the box")
    g.add_argument("--fps", type=float, default=0, help="output frame rate (0 = keep source, snapped to 24/25/30/50/60)")
    g.add_argument("--max-fps", type=float, default=60)
    g.add_argument("--crf", type=int, default=17, help="quality for h264, lower = better/larger (14-23)")
    g.add_argument("--preset", default="medium", help="x264 speed preset (ultrafast ... veryslow)")
    g.add_argument("--gop", type=int, default=30, help="keyframe interval in frames (short = smoother scrubbing in VJ software)")
    g.add_argument("--bg", default="black", help="background colour for transparent sources (black, white, #112233 ...)")
    g.add_argument("--min-seconds", type=float, default=0, help="repeat whole loops until the clip is at least this long (0 = off)")
    g.add_argument("--no-tonemap", action="store_true", help="do not convert HDR (iPhone) to SDR")
    g.add_argument("--keep-audio", action="store_true", help="keep sound (h264 only). Default: no sound")
    n = ap.add_argument_group("Naming")
    n.add_argument("--name", default="{name}", help="name template. Tokens: {name} {n} {n2} {n3} {n4} {w} {h} {res} {fps} {dur} {folder} {date}")
    n.add_argument("--prefix", default="")
    n.add_argument("--suffix", default="")
    n.add_argument("--start", type=int, default=1, help="first number for {n}")
    n.add_argument("--order", choices=["name", "date", "size"], default="name", help="numbering order")
    n.add_argument("--case", choices=["keep", "lower", "upper"], default="keep")
    n.add_argument("--spaces", choices=["_", "-", "keep"], default="_", help="what to put instead of spaces")
    n.add_argument("--no-ascii", action="store_true", help="keep letters like æøå instead of converting to ae o a")
    r = ap.add_argument_group("Run")
    r.add_argument("-o", "--out", default="vj-out", help="output folder")
    r.add_argument("--jobs", type=int, default=2, help="files converted at the same time")
    r.add_argument("--overwrite", action="store_true", help="overwrite files with the same name (default: add -2, -3 ...)")
    r.add_argument("--no-recursive", action="store_true", help="do not look in sub folders")


def profile_from(a):
    return Profile(codec=a.codec, size=a.size, fit=a.fit, upscale=a.upscale, fps=a.fps, max_fps=a.max_fps, crf=a.crf,
                   preset=a.preset, gop=a.gop, bg=a.bg, min_seconds=a.min_seconds, tonemap=not a.no_tonemap,
                   keep_audio=a.keep_audio)


def naming_from(a):
    return Naming(template=a.name, prefix=a.prefix, suffix=a.suffix, start=a.start, ascii=not a.no_ascii,
                  spaces=a.spaces, case=a.case)


def progress_cb():
    def cb(e):
        if e.get("status") in ("done", "failed"):
            mark = "ok " if e["status"] == "done" else "ERR"
            extra = f"  {e.get('note', '')}" if e.get("note") else ""
            print(f"[{e.get('done', 0)}/{e['n']}] {mark} {e['name']}{extra}", flush=True)
    return cb


def summary(results, out):
    ok = sum(1 for r in results if r and r["status"] == "done")
    bad = [r for r in results if r and r["status"] == "failed"]
    print(f"\nDone: {ok} file(s) in {out}" + (f", {len(bad)} failed" if bad else ""))
    for r in bad:
        print("  failed:", r["src"], "-", (r.get("note") or "").splitlines()[0] if r.get("note") else "")
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vj", description="VJ Toolbox - tools for VJ loops and digital visuals.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("convert", help="bulk convert any video (mov, webm, mkv, gif ...) to VJ-friendly mp4 with bulk naming")
    p.add_argument("inputs", nargs="+", help="files and/or folders")
    add_profile_args(p)

    p = sub.add_parser("gif2mp4", help="bulk convert GIF (and APNG) files to mp4")
    p.add_argument("inputs", nargs="+")
    add_profile_args(p)

    p = sub.add_parser("loop", help="make loops: detects if a clip loops, otherwise plays it forward+backward, crossfades or trims it")
    p.add_argument("inputs", nargs="+")
    add_profile_args(p)
    p.add_argument("--mode", choices=["auto", "pingpong", "crossfade", "trim", "none"], default="auto",
                   help="auto = leave good loops, trim to a good loop point if there is one, else --fallback. "
                        "pingpong = forward then backward. crossfade = blend end into start. trim = cut at the best loop point")
    p.add_argument("--fallback", choices=["pingpong", "crossfade"], default="pingpong", help="what auto does when the clip does not loop")
    p.add_argument("--fade", type=float, default=1.5, help="crossfade length in seconds")
    p.add_argument("--check", action="store_true", help="only report whether each clip loops, do not convert")

    p = sub.add_parser("search", help="search free sources for loops and download them as VJ-friendly clips")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--source", default="", help="comma separated: pexels,pixabay,archive,commons (default: every source that is ready)")
    p.add_argument("--limit", type=int, default=12, help="results per source")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--no-loop-word", action="store_true", help="do not add the word 'loop' to the search")
    p.add_argument("--min-height", type=int, default=0)
    p.add_argument("--result-min-seconds", type=float, default=0, dest="s_min", help="only results at least this long")
    p.add_argument("--result-max-seconds", type=float, default=0, dest="s_max", help="only results at most this long")
    p.add_argument("--orientation", choices=["", "landscape", "portrait", "square"], default="")
    p.add_argument("--download", default="", help="download these results: numbers like 1,3,5-8 or 'all'")
    p.add_argument("--loopify", choices=["none", "auto", "pingpong", "crossfade"], default="none",
                   help="also turn the downloaded clips into seamless loops")
    p.add_argument("--fade", type=float, default=1.5)
    add_profile_args(p)

    p = sub.add_parser("fetch", help="download direct video links (or page links if yt-dlp is installed) and convert them")
    p.add_argument("urls", nargs="+")
    p.add_argument("--loopify", choices=["none", "auto", "pingpong", "crossfade"], default="none")
    p.add_argument("--fade", type=float, default=1.5)
    add_profile_args(p)

    p = sub.add_parser("keys", help="save free API keys for Pexels / Pixabay")
    p.add_argument("--pexels")
    p.add_argument("--pixabay")

    p = sub.add_parser("ui", help="start the web interface on this computer")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--data", default=str(Path.home() / "vjtools-data"), help="where uploads and results are kept")

    a = ap.parse_args(argv)
    try:
        return dispatch(a)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except RuntimeError as e:
        print("Error:", e, file=sys.stderr)
        return 2


def parse_pick(s, n):
    if s.strip().lower() == "all":
        return list(range(n))
    out = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            x, y = part.split("-", 1)
            out += list(range(int(x) - 1, int(y)))
        elif part:
            out.append(int(part) - 1)
    return [i for i in out if 0 <= i < n]


def dispatch(a):
    if a.cmd == "ui":
        from .server import serve
        return serve(a.host, a.port, a.data)
    if a.cmd == "keys":
        from . import search as S
        cfg = S.load_config()
        if a.pexels:
            cfg["pexels_key"] = a.pexels
        if a.pixabay:
            cfg["pixabay_key"] = a.pixabay
        S.save_config(cfg)
        for s in S.provider_status():
            print(f"{s['label']:34} {'ready' if s['ready'] else 'needs a key'}")
        return 0
    need("ffmpeg")
    need("ffprobe")
    if a.cmd in ("convert", "gif2mp4", "loop"):
        prof, nm = profile_from(a), naming_from(a)
        exts = GIF_EXT if a.cmd == "gif2mp4" else VIDEO_EXT
        files = collect(a.inputs, exts, not a.no_recursive, a.order)
        if not files:
            print("No matching files found.")
            return 1
        print(f"{len(files)} file(s) -> {a.out}")
        if a.cmd == "loop" and a.check:
            from . import loops
            for f in files:
                try:
                    print(f"{f.name}: {loops.analyze(str(f)).text()}")
                except Exception as e:
                    print(f"{f.name}: cannot read ({str(e).splitlines()[0]})")
            return 0
        kw = dict(cb=progress_cb(), jobs=a.jobs, overwrite=a.overwrite)
        if a.cmd == "loop":
            res = tools.make_loops(files, a.out, prof, nm, mode=a.mode, fade=a.fade, fallback=a.fallback, **kw)
        else:
            res = tools.convert(files, a.out, prof, nm, **kw)
        return summary(res, a.out)
    if a.cmd == "search":
        from . import search as S
        sources = [s for s in a.source.split(",") if s] or None
        if not a.query:
            for s in S.provider_status():
                print(f"{s['name']:9} {s['label']:34} {'ready' if s['ready'] else 'needs a free API key (vj keys --' + s['name'] + ' KEY)'}")
            return 0
        res, errs = S.search(a.query, sources, a.limit, a.page, not a.no_loop_word, a.min_height, a.s_min, a.s_max, a.orientation)
        for i, r in enumerate(res, 1):
            dim = f"{r.width}x{r.height}" if r.height else "?"
            print(f"{i:3}. [{r.provider}] {r.title[:60]}  {dim}  {r.duration:.0f}s  {r.license}  by {r.author[:30]}\n      {r.page}")
        for k, v in errs.items():
            print(f"({k}: {v})")
        if not res:
            print("No results.")
        if a.download and res:
            pick = [res[i] for i in parse_pick(a.download, len(res))]
            out = tools.download_and_convert(pick, a.out, profile_from(a), naming_from(a), a.loopify, a.fade, cb=progress_cb(),
                                             jobs=a.jobs, overwrite=a.overwrite)
            print("Credits and licences are saved in", Path(a.out) / "credits.csv")
            return summary(out, a.out)
        elif res:
            print("\nDownload with e.g.:  --download 1,3,5-8   or   --download all")
        return 0
    if a.cmd == "fetch":
        out = tools.download_and_convert(a.urls, a.out, profile_from(a), naming_from(a), a.loopify, a.fade, cb=progress_cb(),
                                         jobs=a.jobs, overwrite=a.overwrite)
        return summary(out, a.out)
    return 1
