"""A tiny fake of Pexels / Pixabay / Internet Archive / Wikimedia Commons, for offline tests."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs


def start(video_path, port=0):
    video = Path(video_path).read_bytes()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype="application/json", code=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            if not self.path.startswith("/nocors"):
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            base = f"http://127.0.0.1:{self.server.server_port}"
            p = u.path
            if p == "/pexels":
                if self.headers.get("Authorization") != "testkey":
                    return self.send({"error": "bad key"}, code=401)
                return self.send({"videos": [{"id": 111, "url": "https://www.pexels.com/video/neon-tunnel-111/", "image": base + "/thumb.jpg",
                                              "duration": 12, "user": {"name": "Ann Artist"},
                                              "video_files": [{"file_type": "video/mp4", "link": base + "/files/p.mp4", "width": 1920, "height": 1080, "size": len(video)},
                                                              {"file_type": "video/mp4", "link": base + "/files/p.mp4", "width": 3840, "height": 2160}]}]})
            if p == "/pixabay":
                return self.send({"hits": [{"id": 222, "pageURL": "https://pixabay.com/videos/x-222/", "tags": "fire, flame", "duration": 9, "user": "bob",
                                            "picture_id": "999", "videos": {"large": {"url": base + "/files/x.mp4", "width": 1920, "height": 1080, "size": 1},
                                                                            "tiny": {"url": base + "/files/x.mp4", "width": 640, "height": 360}}}]})
            if p == "/advancedsearch.php":
                return self.send({"response": {"docs": [{"identifier": "vid_one", "title": "Old <b>film</b>", "creator": ["Carl"],
                                                         "licenseurl": "https://creativecommons.org/licenses/by/4.0/", "item_size": 5}]}})
            if p == "/metadata/vid_one":
                return self.send({"metadata": {"licenseurl": "https://creativecommons.org/licenses/by/4.0/"},
                                  "files": [{"name": "thumb.jpg", "source": "derivative"},
                                            {"name": "film big.mp4", "source": "derivative", "size": "999999", "width": "640", "height": "480"},
                                            {"name": "film.mp4", "source": "derivative", "size": "100", "width": "320", "height": "240"}]})
            if p.startswith("/download/vid_one/"):
                return self.send(video, "video/mp4")
            if p == "/commons":
                return self.send({"query": {"pages": {"1": {"pageid": 333, "title": "File:Waves loop.webm", "index": 1, "imageinfo": [{
                    "url": base + "/files/c.webm", "thumburl": base + "/thumb.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Waves_loop.webm",
                    "width": 1280, "height": 720, "duration": 7.5, "size": 10,
                    "extmetadata": {"Artist": {"value": "<a href='x'>Dora</a>"}, "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]}}}})
            if p.startswith("/files/") or p.startswith("/nocors/files/"):
                return self.send(video, "video/mp4")
            if p == "/thumb.jpg":
                return self.send(b"\xff\xd8\xff\xd9", "image/jpeg")
            return self.send({"error": "nope"}, code=404)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def patch(S, base):
    S.PROVIDERS["pexels"].base = base + "/pexels"
    S.PROVIDERS["pixabay"].base = base + "/pixabay"
    S.PROVIDERS["archive"].base = base
    S.PROVIDERS["commons"].base = base + "/commons"
