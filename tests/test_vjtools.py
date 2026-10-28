"""Run:  python3 -m unittest discover -s tests -v      (needs ffmpeg; no internet: search sources are faked)"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["VJTOOLS_CONFIG"] = os.path.join(tempfile.mkdtemp(), "cfg.json")
os.environ["PEXELS_API_KEY"] = "testkey"
os.environ["PIXABAY_API_KEY"] = "testkey"

from vjtools import core, tools, loops, search as S
import mockserver


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args], check=True)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = Path(tempfile.mkdtemp(prefix="vjtest_"))
        i = cls.t / "in"
        i.mkdir()
        ff("-f", "lavfi", "-i", "testsrc2=size=200x120:rate=10:duration=2", "-vf", "format=pal8", str(i / "Ærlig gif 1.gif"))
        ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(i / "Mitt Klipp.mov"))
        ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=2", "-c:v", "libvpx-vp9", "-b:v", "500k", str(i / "clip.webm"))
        ff("-f", "lavfi", "-i", "gradients=s=320x180:d=4:r=25:speed=0.05:seed=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(i / "nonloop.mp4"))
        ff("-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=4,geq=r='128+100*sin(2*PI*(X/80+T/4))':g='128+100*sin(2*PI*(Y/60+T/4))':b='128+100*sin(2*PI*T/4)'",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(i / "isloop.mp4"))
        cls.i = i
        cls.fast = core.Profile(preset="ultrafast")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.t, ignore_errors=True)


class TestNaming(unittest.TestCase):
    def test_clean(self):
        nm = core.Naming()
        self.assertEqual(core.clean_name("Ærlig  Åse's (final) ØL.mov", nm), "Aerlig_Ases_final_OL.mov")
        self.assertEqual(core.clean_name("a b", core.Naming(spaces="-", case="upper")), "A-B")
        self.assertEqual(core.clean_name("Ærlig", core.Naming(ascii=False, case="keep")), "Ærlig")

    def test_template_and_collision(self):
        nm = core.Naming(template="loop_{n3}_{name}", prefix="vj_", case="lower")
        taken = set()
        self.assertEqual(core.make_name("/x/My Clip.mov", 0, nm, None, taken), "vj_loop_001_my_clip")
        self.assertEqual(core.make_name("/x/My Clip.mov", 0, nm, None, taken), "vj_loop_001_my_clip-2")
        self.assertEqual(core.make_name("/x/a.mp4", 6, core.Naming(template="{n:04}", start=10), None), "0016")

    def test_fps_and_size(self):
        info = core.Info("x", width=3840, height=2160, fps=29.97)
        self.assertEqual(core.target_size(info, core.Profile()), (1920, 1080))
        small = core.Info("x", width=100, height=100, fps=10)
        self.assertEqual(core.target_size(small, core.Profile()), (100, 100))
        self.assertEqual(core.target_size(small, core.Profile(upscale=True)), (1080, 1080))
        self.assertEqual(core.target_size(core.Info("x", width=1080, height=1920), core.Profile(fit="fit")), (1080, 1920))
        self.assertAlmostEqual(core.choose_fps(info, core.Profile()), 30000 / 1001, 3)
        self.assertEqual(core.choose_fps(core.Info("x", fps=100, codec="gif", is_gif=True), core.Profile()), 30)
        self.assertEqual(core.target_size(core.Info("x", width=401, height=203), core.Profile(codec="hap")) [0] % 4, 0)


class TestConvert(Base):
    def test_bulk_convert_and_naming(self):
        files = core.collect([self.i])
        out = self.t / "o_conv"
        res = tools.convert(files, out, self.fast, core.Naming(template="{n3}_{name}", case="lower"))
        self.assertTrue(all(r["status"] == "done" for r in res), res)
        names = sorted(p.name for p in out.iterdir())
        self.assertIn("005_aerlig_gif_1.mp4", names)
        self.assertEqual(len(names), 5)
        self.assertFalse(any(n.startswith(".") for n in names), "no temp files left")
        for p in out.iterdir():
            i = core.probe(p)
            self.assertEqual(i.codec, "h264")
            self.assertEqual(i.pix_fmt, "yuv420p")
            self.assertFalse(i.has_audio)
            self.assertEqual(i.width % 2, 0)

    def test_collision_adds_number(self):
        out = self.t / "o_col"
        tools.convert([self.i / "clip.webm"], out, self.fast, core.Naming())
        tools.convert([self.i / "clip.webm"], out, self.fast, core.Naming())
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["clip-2.mp4", "clip.mp4"])

    def test_downscale_and_min_seconds(self):
        out = self.t / "o_min"
        prof = core.Profile(preset="ultrafast", size="640x360", min_seconds=5)
        tools.convert([self.i / "Mitt Klipp.mov"], out, prof, core.Naming())
        i = core.probe(out / "Mitt_Klipp.mp4")
        self.assertEqual((i.width, i.height), (640, 360))
        self.assertGreaterEqual(i.duration, 5.0)

    def test_gif_only_and_bad_file(self):
        out = self.t / "o_gif"
        bad = self.t / "broken.mp4"
        bad.write_bytes(b"not a video")
        res = tools.gif2mp4([self.i / "Ærlig gif 1.gif", self.i / "clip.webm"], out, self.fast, core.Naming())
        self.assertEqual(len(res), 1)
        res = tools.convert([bad, self.i / "clip.webm"], self.t / "o_bad", self.fast, core.Naming())
        self.assertEqual(sorted(r["status"] for r in res), ["done", "failed"])

    def test_hap(self):
        if not core.has_encoder("hap"):
            self.skipTest("no hap encoder")
        out = self.t / "o_hap"
        res = tools.convert([self.i / "clip.webm"], out, core.Profile(codec="hap"), core.Naming())
        self.assertEqual(res[0]["status"], "done", res)
        self.assertEqual(core.probe(next(out.iterdir())).codec, "hap")

    def test_cancel(self):
        ctl = core.Ctl()
        ctl.stop()
        res = tools.convert(core.collect([self.i]), self.t / "o_cancel", self.fast, core.Naming(), ctl=ctl)
        self.assertTrue(all(r["status"] == "cancelled" for r in res))


class TestLoops(Base):
    def test_analysis(self):
        self.assertTrue(loops.analyze(str(self.i / "isloop.mp4")).is_loop)
        r = loops.analyze(str(self.i / "nonloop.mp4"))
        self.assertFalse(r.is_loop)
        self.assertGreater(r.ratio, 3)

    def test_modes_make_real_loops(self):
        src = str(self.i / "nonloop.mp4")
        N = core.probe(src).frames
        for mode, frames in (("pingpong", 2 * N - 2), ("crossfade", None), ("trim", None), ("auto", 2 * N - 2)):
            dst = str(self.t / f"lp_{mode}.mp4")
            note, used = loops.make_loop(src, dst, self.fast, mode, fade=1.0)
            n = core.count_frames(dst)
            if frames:
                self.assertEqual(n, frames, mode)
            if mode == "crossfade":
                self.assertEqual(n, N - 25)
                self.assertTrue(loops.analyze(dst).is_loop)
            if mode in ("pingpong", "auto"):
                self.assertTrue(loops.analyze(dst).ratio < 2.5, (mode, loops.analyze(dst).text()))
        self.assertEqual(used, "pingpong")

    def test_auto_leaves_good_loop(self):
        dst = str(self.t / "keep.mp4")
        note, used = loops.make_loop(str(self.i / "isloop.mp4"), dst, self.fast, "auto")
        self.assertEqual(used, "asis")
        self.assertEqual(core.count_frames(dst), core.probe(str(self.i / "isloop.mp4")).frames)

    def test_loop_min_seconds(self):
        dst = str(self.t / "long.mp4")
        loops.make_loop(str(self.i / "nonloop.mp4"), dst, core.Profile(preset="ultrafast", min_seconds=20), "pingpong")
        self.assertGreaterEqual(core.probe(dst).duration, 20)

    def test_chunked_reverse_matches(self):
        # force tiny chunks: result must still be exactly forward + backward
        src = str(self.i / "nonloop.mp4")
        inter = str(self.t / "inter.mp4")
        core.encode(src, inter, loops._norm_profile(self.fast))
        tmp = tempfile.mkdtemp()
        out = str(self.t / "pp_small.mp4")
        n = loops.make_pingpong(inter, out, 25, tmp, budget=320 * 180 * 1.5 * 30)  # ~30 frame chunks
        self.assertEqual(core.count_frames(out), n)
        F = loops.gray_frames(out, 25)
        N = len(F) // 2 + 1
        import numpy as np
        for k in (1, 5, 40):  # frame N-1+k must equal frame N-1-k
            self.assertLess(float(np.abs(F[N - 1 + k] - F[N - 1 - k]).mean()), 0.01)


class TestSearch(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.srv, cls.base = mockserver.start(cls.i / "clip.webm")
        mockserver.patch(S, cls.base)

    def test_all_sources(self):
        res, errs = S.search("tunnel", ["pexels", "pixabay", "archive", "commons", "nasa", "prelinger"], pixabay_type="animation")
        self.assertEqual(errs, {})
        by = {r.provider: r for r in res}
        self.assertEqual(set(by), {"pexels", "pixabay", "archive", "commons", "nasa", "prelinger"})
        self.assertEqual(by["nasa"].author, "NASA JPL")
        self.assertEqual(by["prelinger"].license, "Public domain (Prelinger Archives)")
        self.assertTrue(by["nasa"].thumb.startswith("https://"))
        self.assertEqual(by["pexels"].height, 1080)  # picks the best file within the height cap
        self.assertEqual(by["pixabay"].height, 1080)
        self.assertEqual(by["archive"].title, "Old film")
        self.assertEqual(by["commons"].author, "Dora")
        self.assertEqual(by["commons"].license, "CC BY-SA 4.0")

    def test_filters_and_failures(self):
        res, _ = S.search("x", ["pexels", "commons"], min_seconds=10)
        self.assertEqual([r.provider for r in res], ["pexels"])
        os.environ["PEXELS_API_KEY"] = "wrong"
        try:
            res, errs = S.search("x", ["pexels", "commons"])
        finally:
            os.environ["PEXELS_API_KEY"] = "testkey"
        self.assertIn("pexels", errs)
        self.assertEqual([r.provider for r in res], ["commons"])

    def test_archive_resolve_prefers_small_mp4(self):
        r = S.Archive.search(S.PROVIDERS["archive"], "x", 5, 1, {})[0]
        S.PROVIDERS["archive"].resolve(r)
        self.assertTrue(r.url.endswith("film.mp4") or "film%20big" in r.url)
        self.assertEqual(r.license, "CC BY 4.0")

    def test_nasa_and_prelinger_resolve(self):
        n = S.PROVIDERS["nasa"].search("x", 5, 1, {})[0]
        S.PROVIDERS["nasa"].resolve(n)
        self.assertTrue(n.url.endswith("~large.mp4"), n.url)      # large preferred over the huge original
        p = S.PROVIDERS["prelinger"].search("x", 5, 1, {})[0]
        S.PROVIDERS["prelinger"].resolve(p)
        self.assertTrue(p.url.endswith("duck.mp4"))
        self.assertEqual(p.license, "Public domain (Prelinger Archives)")

    def test_download_convert_credits(self):
        res, _ = S.search("x", ["pexels", "commons", "archive"])
        out = self.t / "o_dl"
        results = tools.download_and_convert(res, out, self.fast, core.Naming(template="stock_{n2}"), loopify="pingpong")
        self.assertEqual([r["status"] for r in results], ["done"] * 3, results)
        self.assertEqual(sorted(p.name for p in out.glob("*.mp4")), ["stock_01.mp4", "stock_02.mp4", "stock_03.mp4"])
        rows = list(csv.reader(open(out / "credits.csv", encoding="utf-8")))
        self.assertEqual(len(rows), 4)
        self.assertTrue(any("Ann Artist" in r for r in rows))
        self.assertTrue(any("CC BY-SA 4.0" in r for r in rows))

    def test_fetch_direct_url(self):
        out = self.t / "o_url"
        r = tools.download_and_convert([self.base + "/files/direct.mp4", self.base + "/nothing.mp4"], out, self.fast, core.Naming())
        self.assertEqual(sorted(x["status"] for x in r), ["done", "failed"])


class TestServer(Base):
    def test_api_flow(self):
        from vjtools.server import make_app
        app = make_app(self.t / "data")
        c = app.test_client()
        info = c.get("/api/info").json
        self.assertTrue(info["ffmpeg"])
        sc = c.get("/api/scan", query_string={"path": str(self.i), "gif": 1}).json
        self.assertEqual([f["name"] for f in sc["files"]], ["Ærlig gif 1.gif"])
        files = [f["path"] for f in c.get("/api/scan", query_string={"path": str(self.i)}).json["files"]]
        pv = c.post("/api/naming-preview", json={"files": files, "naming": {"template": "x_{n2}", "prefix": "a "}}).json["rows"]
        self.assertEqual(pv[0][1], "a_x_01.mp4")
        self.assertEqual(c.get("/api/thumb", query_string={"path": files[0]}).status_code, 200)
        jid = c.post("/api/jobs", json={"tool": "loop", "files": files[:2], "profile": {"preset": "ultrafast"},
                                        "naming": {"template": "L{n}"}, "loop": {"mode": "auto"}, "outdir": str(self.t / "srv_out")}).json["id"]
        for _ in range(120):
            j = [x for x in c.get("/api/jobs").json if x["id"] == jid][0]
            if j["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.5)
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(len(j["files"]), 2)
        self.assertEqual(c.get(f"/api/out/{jid}/{j['files'][0]['name']}").status_code, 200)
        self.assertEqual(c.get(f"/api/jobs/{jid}/zip").status_code, 200)
        self.assertEqual(c.post("/api/jobs", json={"tool": "convert", "files": []}).status_code, 400)
        self.assertEqual(c.delete(f"/api/jobs/{jid}").json["ok"], True)

    def test_path_safety(self):
        from vjtools.server import make_app
        c = make_app(self.t / "data2").test_client()
        self.assertEqual(c.get("/api/file", query_string={"path": "/etc/passwd"}).status_code, 404)
        self.assertEqual(c.get("/api/thumb", query_string={"path": "/etc/passwd"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
