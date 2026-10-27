"""Loop analysis and loop making (play-forward-then-backward, crossfaded loop, or trimming to the best loop point)."""
from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .core import (Ctl, Info, Profile, probe, run, ffmpeg, encode, count_frames, choose_fps, repeats_needed, codec_args,
                   target_size, vf_chain)


@dataclass
class LoopReport:
    frames: int
    fps: float
    wrap: float          # difference between last and first picture (0..1)
    step_typ: float      # typical difference between neighbouring pictures
    ratio: float         # wrap / step_typ: about 1 or less = the loop point is not visible
    is_loop: bool
    verdict: str         # seamless | almost | jump
    best_cut: int        # last frame to keep for the best loop point (-1 if none found)
    best_ratio: float
    best_keeps: float    # share of the clip that is kept when trimming there

    def text(self):
        s = f"{self.verdict} (loop jump {self.ratio:.1f}x a normal frame step)"
        if not self.is_loop and self.best_cut >= 0 and self.best_ratio <= 1.25:
            s += f"; a better loop point exists after frame {self.best_cut} (keeps {self.best_keeps * 100:.0f} %)"
        return s


def gray_frames(path, fps, w=64, h=36, ctl=None, max_frames=20000):
    out = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-an",
               "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-frames:v", str(max_frames),
               "-f", "rawvideo", "-"], ctl)
    a = np.frombuffer(out, dtype=np.uint8)
    n = len(a) // (w * h)
    return a[: n * w * h].reshape(n, h, w).astype(np.float32) / 255.0


def analyze_frames(F, fps) -> LoopReport:
    n = len(F)
    if n < 4:
        return LoopReport(n, fps, 0, 0, 0, False, "jump", -1, 9e9, 0)
    steps = np.abs(F[1:] - F[:-1]).mean(axis=(1, 2))
    ref = max(float(np.percentile(steps, 90)), 0.004)
    wrap = float(np.abs(F[-1] - F[0]).mean())
    ratio = wrap / ref
    is_loop = ratio <= 1.25 or wrap < 0.01
    verdict = "seamless" if is_loop else "almost" if ratio <= 3 else "jump"
    # best place to cut: after frame k, the next frame k+1 should look like frame 0
    lo = n // 2
    cost = np.abs(F[lo + 1:] - F[0]).mean(axis=(1, 2))  # index j -> k = lo + j, compares frame k+1
    if len(cost):
        # avoid a "freeze": prefer matches that are really close but ignore ties by choosing the longest
        j = int(np.argmin(cost + 1e-9 * (len(cost) - np.arange(len(cost)))))
        k = lo + j
        return LoopReport(n, fps, wrap, ref, ratio, is_loop, verdict, k, float(cost[j]) / ref, (k + 1) / n)
    return LoopReport(n, fps, wrap, ref, ratio, is_loop, verdict, -1, 9e9, 0)


def analyze(path, fps=None, ctl=None) -> LoopReport:
    info = probe(path)
    fps = fps or choose_fps(info, Profile())
    return analyze_frames(gray_frames(path, fps, ctl=ctl), fps)


def _norm_profile(prof: Profile) -> Profile:
    return dataclasses.replace(prof, codec="h264", crf=10, preset="veryfast", gop=1, min_seconds=0.0, keep_audio=False)


def _enc_args(fps):
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "10", "-pix_fmt", "yuv420p", "-g", "1", "-r", str(fps),
            "-fps_mode", "cfr", "-an"]


def make_pingpong(inter, out, fps, tmp, ctl=None, budget=700e6):
    """forward 0..N-1 then backward N-2..1, so the clip ends right before it starts again."""
    N = count_frames(inter)
    if N < 3:
        raise RuntimeError("Clip is too short to make a loop of")
    inf = probe(inter)
    per = max(1, inf.width * inf.height * 3 // 2)
    C = max(24, int(budget // per))
    parts = []
    # backward part: frames N-2 .. 1, reverse in chunks so memory stays small
    lo, hi = 1, N - 1
    chunks = [(a, min(a + C, hi)) for a in range(lo, hi, C)]
    for ci, (a, b) in reversed(list(enumerate(chunks))):
        p = os.path.join(tmp, f"rev_{ci:04d}.mp4")
        ss = [] if a == 0 else ["-ss", f"{(a - 0.5) / fps:.6f}"]
        ffmpeg([*ss, "-i", inter, "-vf", f"trim=end_frame={b - a},reverse,setpts=N/FRAME_RATE/TB", "-frames:v", str(b - a), *_enc_args(fps), p], ctl)
        parts.append(p)
    lst = os.path.join(tmp, "pp.txt")
    with open(lst, "w") as f:
        f.write(f"file '{inter}'\n")
        for p in parts:
            f.write(f"file '{p}'\n")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, *_enc_args(fps), out], ctl)
    return 2 * N - 2


def make_crossfade(inter, out, fps, fade_s, tmp, ctl=None):
    """Blend the end of the clip into its beginning. The result is shorter by the fade and loops without a jump."""
    N = count_frames(inter)
    xf = int(min(round(fade_s * fps), N // 3))
    if xf < 2:
        raise RuntimeError("Clip is too short for a crossfaded loop")
    w = f"st(0,(N+1)/{xf + 1});st(1,ld(0)*ld(0)*ld(0)*(ld(0)*(ld(0)*6-15)+10))"
    fg = (f"[0:v]split=2[h0][r0];[h0]trim=end_frame={xf},setpts=PTS-STARTPTS[head];"
          f"[r0]trim=start_frame={xf}:end_frame={N - xf},setpts=PTS-STARTPTS[rest];"
          f"[1:v]trim=end_frame={xf},setpts=PTS-STARTPTS[tail];"
          f"[tail][head]blend=all_expr='{w};A*(1-ld(1))+B*ld(1)'[bl];[bl][rest]concat=n=2:v=1:a=0[v]")
    ss = f"{max(0.0, (N - xf - 0.5) / fps):.6f}"
    ffmpeg(["-i", inter, "-ss", ss, "-i", inter, "-filter_complex", fg, "-map", "[v]", *_enc_args(fps), out], ctl)
    return N - xf


def make_loop(src, dst, prof: Profile, mode="auto", fade=1.5, fallback="pingpong", ctl: Ctl | None = None):
    """Make `dst` a VJ friendly clip that loops without a visible jump. Returns (note, method_used)."""
    info = probe(src)
    fps = choose_fps(info, prof)
    rep = analyze_frames(gray_frames(src, fps, ctl=ctl), fps)
    method = mode
    if mode == "auto":
        if rep.is_loop:
            method = "asis"
        elif rep.best_cut >= 0 and rep.best_ratio <= 1.25 and rep.best_keeps >= 0.6:
            method = "trim"
        else:
            method = fallback
    if method == "none":
        method = "asis"
    if method == "trim" and rep.best_cut < 0:
        method = fallback
    weak = method == "trim" and rep.best_ratio > 1.25

    if method == "asis":
        encode(src, dst, prof, info, ctl, repeat=repeats_needed(info.duration, prof.min_seconds))
        return f"already loops ({rep.text()})" if rep.is_loop else f"kept as is ({rep.text()})", "asis"

    tmp = tempfile.mkdtemp(prefix="vjloop_")
    try:
        inter = os.path.join(tmp, "norm.mp4")
        trim = rep.best_cut + 1 if method == "trim" else None
        encode(src, inter, _norm_profile(prof), info, ctl, trim_frames=trim)
        if method == "trim":
            frames, final_src = trim, inter
        elif method == "pingpong":
            fin = os.path.join(tmp, "loop.mp4")
            frames = make_pingpong(inter, fin, fps, tmp, ctl)
            final_src = fin
        elif method == "crossfade":
            fin = os.path.join(tmp, "loop.mp4")
            frames = make_crossfade(inter, fin, fps, fade, tmp, ctl)
            final_src = fin
        else:
            raise ValueError(f"Unknown loop mode {mode}")
        dur = frames / fps
        encode(final_src, dst, prof, probe(final_src), ctl, repeat=repeats_needed(dur, prof.min_seconds))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    what = {"trim": f"trimmed to the best loop point (frame {rep.best_cut})" + (" - no really good point exists, a small jump may remain" if weak else ""), "pingpong": "played forward then backward",
            "crossfade": f"end crossfaded into the start ({fade:g} s)"}[method]
    return f"{what}; before: {rep.text()}", method
