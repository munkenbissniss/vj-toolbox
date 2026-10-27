"""Shared helpers: ffmpeg/ffprobe, the VJ-friendly profile, file naming and file collection."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".wmv", ".flv", ".ogv", ".mts", ".m2ts",
             ".3gp", ".gif", ".apng", ".hevc", ".ts"}
GIF_EXT = {".gif", ".apng"}


class Cancelled(Exception):
    pass


class Ctl:
    """Cancel flag + the ffmpeg process that is currently running (so a job can be stopped at once)."""

    def __init__(self):
        self.cancel = False
        self.proc = None
        self._lock = threading.Lock()

    def stop(self):
        self.cancel = True
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()

    def check(self):
        if self.cancel:
            raise Cancelled()


def need(tool):
    if not shutil.which(tool):
        raise RuntimeError(f"{tool} was not found. Install ffmpeg first (Ubuntu: sudo apt install ffmpeg).")


def run(cmd, ctl: Ctl | None = None, timeout=None):
    """Run a command, return stdout. Raises RuntimeError with the tail of stderr on failure."""
    cmd = [str(c) for c in cmd]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    if ctl:
        with ctl._lock:
            ctl.proc = p
    try:
        out, err = p.communicate(timeout=timeout)
    finally:
        if ctl:
            with ctl._lock:
                ctl.proc = None
    if ctl and ctl.cancel:
        raise Cancelled()
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{err.decode('utf-8', 'replace')[-1500:]}")
    return out


def ffmpeg(args, ctl=None):
    return run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin", *args], ctl)


_enc_cache = {}


def has_encoder(name):
    if name not in _enc_cache:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
            _enc_cache[name] = bool(re.search(rf"\s{re.escape(name)}\s", out))
        except Exception:
            _enc_cache[name] = False
    return _enc_cache[name]


def has_filter(name):
    key = "f:" + name
    if key not in _enc_cache:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
            _enc_cache[key] = bool(re.search(rf"\s{re.escape(name)}\s", out))
        except Exception:
            _enc_cache[key] = False
    return _enc_cache[key]


# ---------------------------------------------------------------------------------------------
# probing
# ---------------------------------------------------------------------------------------------
@dataclass
class Info:
    path: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    frames: int = 0
    codec: str = ""
    pix_fmt: str = ""
    transfer: str = ""
    primaries: str = ""
    bitrate: float = 0.0
    has_audio: bool = False
    rotation: int = 0
    alpha: bool = False
    is_gif: bool = False

    @property
    def hdr(self):
        return self.transfer in ("smpte2084", "arib-std-b67") or self.primaries == "bt2020"


def _frac(s):
    try:
        a, b = str(s).split("/")
        return float(a) / float(b) if float(b) else 0.0
    except Exception:
        try:
            return float(s)
        except Exception:
            return 0.0


def probe(path) -> Info:
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    j = json.loads(out)
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError("No video stream found")
    info = Info(str(path))
    info.width, info.height = int(v.get("width", 0)), int(v.get("height", 0))
    info.codec, info.pix_fmt = v.get("codec_name", ""), v.get("pix_fmt", "")
    info.transfer, info.primaries = v.get("color_transfer", ""), v.get("color_primaries", "")
    info.fps = _frac(v.get("avg_frame_rate")) or _frac(v.get("r_frame_rate"))
    fmt = j.get("format", {})
    dur = v.get("duration") or fmt.get("duration")
    info.duration = float(dur) if dur not in (None, "N/A") else 0.0
    try:
        info.frames = int(v.get("nb_frames") or 0)
    except ValueError:
        info.frames = 0
    if not info.frames and info.duration and info.fps:
        info.frames = int(round(info.duration * info.fps))
    try:
        info.bitrate = float(v.get("bit_rate") or fmt.get("bit_rate") or 0)
    except ValueError:
        pass
    info.has_audio = any(s.get("codec_type") == "audio" for s in j.get("streams", []))
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            info.rotation = int(sd["rotation"]) % 360
    rot = (v.get("tags") or {}).get("rotate")
    if rot:
        info.rotation = int(rot) % 360
    if info.rotation in (90, 270):  # ffmpeg autorotates, so report the size people will see
        info.width, info.height = info.height, info.width
    info.is_gif = info.codec in ("gif", "apng") or Path(path).suffix.lower() in GIF_EXT
    info.alpha = info.is_gif or info.pix_fmt.startswith(("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "pal8", "ya8"))
    return info


def count_frames(path) -> int:
    out = run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
               "stream=nb_read_frames", "-of", "csv=p=0", str(path)])
    return int(out.decode().strip().split(",")[0])


# ---------------------------------------------------------------------------------------------
# VJ-friendly profile
# ---------------------------------------------------------------------------------------------
@dataclass
class Profile:
    codec: str = "h264"          # h264 | hap | hap_q | prores
    size: str = "1920x1080"      # box the video must fit in (or fill / pad to, see fit)
    fit: str = "keep"            # keep = keep aspect, only shrink | fit = letterbox to the box | fill = crop to the box
    upscale: bool = False        # allow 'keep' to enlarge small videos up to the box
    fps: float = 0.0             # 0 = keep the source rate (snapped to a standard rate, max 60)
    max_fps: float = 60.0
    crf: int = 17
    preset: str = "medium"
    gop: int = 30                # keyframe every N frames (short = smooth scrubbing / looping in VJ software)
    bg: str = "black"            # background for transparent sources
    min_seconds: float = 0.0     # repeat whole loops until the clip is at least this long
    tonemap: bool = True         # convert HDR (iPhone etc.) to normal SDR colours
    keep_audio: bool = False

    def box(self):
        w, h = (int(x) for x in self.size.lower().split("x"))
        return w, h


STD_FPS = (23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60)


_EXACT = {23.976: 24000 / 1001, 29.97: 30000 / 1001, 59.94: 60000 / 1001}


def choose_fps(info: Info, prof: Profile) -> float:
    if prof.fps > 0:
        return prof.fps
    f = info.fps or 30.0
    if info.is_gif:
        f = min(f, 30.0)  # GIF frame delays are rough; 30 fps is plenty
    f = min(f, prof.max_fps)
    near = min(STD_FPS, key=lambda s: abs(s - f))
    if abs(near - f) < 0.6:
        return _EXACT.get(near, float(near))
    return round(f, 3)


def target_size(info: Info, prof: Profile):
    bw, bh = prof.box()
    if info.width <= 0 or info.height <= 0:
        return bw, bh
    portrait = info.height > info.width
    if portrait and bw > bh:  # the box describes a landscape screen; for portrait material turn it
        bw, bh = bh, bw
    if prof.fit in ("fit", "fill"):
        w, h = bw, bh
    else:
        s = min(bw / info.width, bh / info.height)
        if s > 1 and not prof.upscale:
            s = 1.0
        w, h = info.width * s, info.height * s
    mult = 4 if prof.codec.startswith("hap") else 2
    w, h = int(round(w / mult)) * mult, int(round(h / mult)) * mult
    return max(mult, w), max(mult, h)


def vf_chain(info: Info, prof: Profile, fps: float, extra_pre=None):
    """Video filter chain that makes any source VJ friendly."""
    w, h = target_size(info, prof)
    f = []
    if extra_pre:
        f += extra_pre
    if info.alpha:
        # flatten transparency on a solid colour
        f.append(f"format=rgba,split[a][b];[a]drawbox=x=0:y=0:w=iw:h=ih:color={prof.bg}:t=fill[bg];[bg][b]overlay=format=auto")
    if info.hdr and prof.tonemap and has_filter("zscale") and has_filter("tonemap"):
        f.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                 "zscale=t=bt709:m=bt709:r=tv")
    if prof.fit == "fill":
        f.append(f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}")
    elif prof.fit == "fit":
        f.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                 f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={prof.bg}")
    else:
        f.append(f"scale={w}:{h}:flags=lanczos")
    f.append(f"fps={fps}")
    f.append("setsar=1")
    f.append("format=yuv420p" if not prof.codec.startswith("hap") else "format=rgba")
    return ",".join(f)


def codec_args(prof: Profile, fps: float):
    g = max(1, int(prof.gop))
    if prof.codec == "h264":
        return ["-c:v", "libx264", "-preset", prof.preset, "-crf", str(prof.crf), "-pix_fmt", "yuv420p",
                "-g", str(g), "-keyint_min", str(g), "-sc_threshold", "0", "-profile:v", "high",
                "-movflags", "+faststart", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if prof.codec in ("hap", "hap_q"):
        if not has_encoder("hap"):
            raise RuntimeError("This ffmpeg has no HAP encoder.")
        return ["-c:v", "hap", "-format", "hap_q" if prof.codec == "hap_q" else "hap"]
    if prof.codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    raise ValueError(f"Unknown codec {prof.codec}")


def out_ext(prof: Profile):
    return ".mp4" if prof.codec == "h264" else ".mov"


def repeats_needed(duration, min_seconds):
    if min_seconds <= 0 or duration <= 0 or duration >= min_seconds:
        return 1
    return int(math.ceil(min_seconds / duration - 1e-9))


def encode(src, dst, prof: Profile, info: Info | None = None, ctl=None, repeat=1, trim_frames=None):
    """Encode one file to the VJ-friendly profile. `repeat` plays the whole clip N times."""
    info = info or probe(src)
    fps = choose_fps(info, prof)
    args = []
    if repeat > 1:
        args += ["-stream_loop", str(repeat - 1)]
    args += ["-i", src]
    vf = vf_chain(info, prof, fps)
    if info.alpha:
        args += ["-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]"]
    else:
        args += ["-vf", vf, "-map", "0:v:0"]
    if prof.keep_audio and info.has_audio and prof.codec == "h264":
        args += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-an"]
    if trim_frames:
        args += ["-frames:v", str(int(trim_frames))]
    args += ["-fps_mode", "cfr", *codec_args(prof, fps), "-map_metadata", "-1", str(dst)]
    ffmpeg(args, ctl)


# ---------------------------------------------------------------------------------------------
# collecting files and naming
# ---------------------------------------------------------------------------------------------
def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def collect(paths, exts=None, recursive=True, order="name"):
    exts = exts or VIDEO_EXT
    out = []
    for p in paths:
        p = Path(p).expanduser()
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out += [f for f in it if f.is_file() and f.suffix.lower() in exts and not f.name.startswith(".")]
        elif p.is_file():
            out.append(p)
    seen, uniq = set(), []
    for f in out:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(f)
    if order == "date":
        uniq.sort(key=lambda f: (f.stat().st_mtime, natural_key(f.name)))
    elif order == "size":
        uniq.sort(key=lambda f: (f.stat().st_size, natural_key(f.name)))
    else:
        uniq.sort(key=lambda f: natural_key(str(f)))
    return uniq


_TRANSLIT = {"æ": "ae", "Æ": "Ae", "ø": "o", "Ø": "O", "å": "a", "Å": "A", "ß": "ss", "ð": "d", "þ": "th", "đ": "d",
             "ł": "l", "Ł": "L", "œ": "oe", "Œ": "Oe"}


@dataclass
class Naming:
    template: str = "{name}"       # tokens: {name} {n} {n2} {n3} {n4} {w} {h} {res} {fps} {dur} {folder} {date}
    prefix: str = ""
    suffix: str = ""
    start: int = 1
    ascii: bool = True             # æøå etc. -> ae o a, accents removed
    spaces: str = "_"              # "_" | "-" | "keep"
    case: str = "keep"             # keep | lower | upper
    keep_chars: str = "-_."        # other characters that survive (letters and digits always do)


def clean_name(s, nm: Naming):
    if nm.ascii:
        s = "".join(_TRANSLIT.get(c, c) for c in s)
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    if nm.spaces != "keep":
        s = re.sub(r"\s+", nm.spaces, s.strip())
    allowed = re.escape(nm.keep_chars) + (r"\s" if nm.spaces == "keep" else "")
    s = re.sub(rf"[^\w{allowed}]", "", s, flags=re.UNICODE if not nm.ascii else re.ASCII)
    s = re.sub(r"([_\-.])\1{1,}", r"\1", s).strip("_-. ")
    if nm.case == "lower":
        s = s.lower()
    elif nm.case == "upper":
        s = s.upper()
    return s or "clip"


def make_name(src, index, nm: Naming, info: Info | None = None, taken=None):
    """Build the output file name (without extension). `index` counts from 0."""
    src = Path(src)
    n = index + nm.start
    tok = {"name": src.stem, "n": str(n), "n2": f"{n:02d}", "n3": f"{n:03d}", "n4": f"{n:04d}",
           "folder": src.parent.name, "date": time.strftime("%Y%m%d"),
           "w": str(info.width) if info else "", "h": str(info.height) if info else "",
           "res": f"{info.width}x{info.height}" if info else "", "fps": f"{info.fps:.0f}" if info else "",
           "dur": f"{info.duration:.0f}s" if info else ""}
    t = nm.template or "{name}"
    t = re.sub(r"\{(\w+)(?::0?(\d+))?\}", lambda m: (f"{int(tok['n']):0{m[2]}d}" if m[1] == "n" and m[2] else tok.get(m[1], m[0])), t)
    base = clean_name(nm.prefix + t + nm.suffix, nm)
    if taken is not None:
        cand, k = base, 2
        while cand.lower() in taken:
            cand, k = f"{base}-{k}", k + 1
        taken.add(cand.lower())
        base = cand
    return base


def fmt_size(b):
    return f"{b / 1e6:.1f} MB" if b >= 1e6 else f"{b / 1e3:.0f} kB"
