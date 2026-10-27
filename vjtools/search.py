"""Search free video sources for loops, download them and keep track of who made them and under which licence.

Sources that work without any account: Internet Archive (open licences only), Wikimedia Commons.
Sources that need a free API key: Pexels, Pixabay (set PEXELS_API_KEY / PIXABAY_API_KEY, or save the keys in the web UI).
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .core import Ctl, Cancelled, VIDEO_EXT

UA = "vj-toolbox/0.1 (+https://github.com/munkenbissniss/vj-toolbox)"
CONFIG = Path(os.environ.get("VJTOOLS_CONFIG", Path.home() / ".config" / "vjtools" / "config.json"))


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1))
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass


def api_key(name):
    return os.environ.get(f"{name.upper()}_API_KEY") or load_config().get(f"{name}_key", "")


def http(url, headers=None, timeout=25, data=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})}, data=data)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{urllib.parse.urlparse(url).netloc}: HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"{urllib.parse.urlparse(url).netloc}: {e.reason}")


def get_json(url, headers=None):
    with http(url, headers) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


@dataclass
class Result:
    provider: str
    id: str
    title: str
    page: str = ""
    thumb: str = ""
    url: str = ""            # direct file (may be empty until resolved)
    width: int = 0
    height: int = 0
    duration: float = 0.0
    author: str = ""
    license: str = ""
    license_url: str = ""
    size: int = 0

    def key(self):
        return f"{self.provider}:{self.id}"


def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


class Pexels:
    name, label, needs_key = "pexels", "Pexels", True
    base = "https://api.pexels.com/videos/search"

    def search(self, q, n, page, o):
        key = api_key("pexels")
        if not key:
            raise RuntimeError("Pexels needs a free API key (https://www.pexels.com/api/)")
        params = {"query": q, "per_page": min(n, 80), "page": page}
        if o.get("orientation"):
            params["orientation"] = o["orientation"]
        j = get_json(f"{self.base}?{urllib.parse.urlencode(params)}", {"Authorization": key})
        out = []
        for v in j.get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
            if not files:
                continue
            cap = o.get("max_height") or 1080
            ok = [f for f in files if (f.get("height") or 0) <= cap] or files
            best = max(ok, key=lambda f: (f.get("height") or 0))
            out.append(Result("pexels", str(v["id"]), f"Pexels video {v['id']} " + (v.get("url", "").rstrip("/").split("/")[-1].rsplit("-", 1)[0].replace("-", " ")),
                              v.get("url", ""), v.get("image", ""), best["link"], best.get("width") or 0, best.get("height") or 0,
                              float(v.get("duration") or 0), (v.get("user") or {}).get("name", ""), "Pexels License",
                              "https://www.pexels.com/license/", best.get("size") or 0))
        return out


class Pixabay:
    name, label, needs_key = "pixabay", "Pixabay", True
    base = "https://pixabay.com/api/videos/"

    def search(self, q, n, page, o):
        key = api_key("pixabay")
        if not key:
            raise RuntimeError("Pixabay needs a free API key (https://pixabay.com/api/docs/)")
        params = {"key": key, "q": q, "per_page": max(3, min(n, 200)), "page": page, "safesearch": "true"}
        j = get_json(f"{self.base}?{urllib.parse.urlencode(params)}")
        out = []
        for h in j.get("hits", []):
            vs = h.get("videos", {})
            cap = o.get("max_height") or 1080
            cands = [vs[k] for k in ("large", "medium", "small", "tiny") if vs.get(k, {}).get("url")]
            ok = [c for c in cands if (c.get("height") or 0) <= cap] or cands
            if not ok:
                continue
            best = max(ok, key=lambda c: c.get("height") or 0)
            thumb = f"https://i.vimeocdn.com/video/{h['picture_id']}_640x360.jpg" if h.get("picture_id") else ""
            out.append(Result("pixabay", str(h["id"]), (h.get("tags") or "Pixabay video").replace(",", " ·"), h.get("pageURL", ""), thumb,
                              best["url"], best.get("width") or 0, best.get("height") or 0, float(h.get("duration") or 0),
                              h.get("user", ""), "Pixabay Content License", "https://pixabay.com/service/license-summary/",
                              best.get("size") or 0))
        return out


class Archive:
    name, label, needs_key = "archive", "Internet Archive (open licences)", False
    base = "https://archive.org"

    def search(self, q, n, page, o):
        lic = "(licenseurl:*creativecommons* OR licenseurl:*publicdomain*)"
        query = f"({q}) AND mediatype:movies AND {lic}"
        params = [("q", query), ("rows", min(n, 100)), ("page", page), ("output", "json"), ("sort[]", "downloads desc")]
        params += [("fl[]", f) for f in ("identifier", "title", "creator", "licenseurl", "item_size")]
        j = get_json(f"{self.base}/advancedsearch.php?{urllib.parse.urlencode(params)}")
        out = []
        for d in j.get("response", {}).get("docs", []):
            ident = d["identifier"]
            cr = d.get("creator", "")
            out.append(Result("archive", ident, _strip(d.get("title", ident)), f"{self.base}/details/{ident}",
                              f"{self.base}/services/img/{ident}", "", 0, 0, 0, ", ".join(cr) if isinstance(cr, list) else cr,
                              "Open licence (see page)", d.get("licenseurl", "") if isinstance(d.get("licenseurl"), str) else "",
                              int(d.get("item_size") or 0)))
        return out

    def resolve(self, r: Result):
        j = get_json(f"{self.base}/metadata/{r.id}")
        files = j.get("files", [])
        cands = [f for f in files if str(f.get("name", "")).lower().endswith((".mp4", ".webm", ".ogv", ".mov", ".m4v", ".mkv"))
                 and f.get("source") in ("original", "derivative")]
        if not cands:
            raise RuntimeError("No video file in this archive item")
        def score(f):
            n = f["name"].lower()
            pri = 0 if n.endswith(".mp4") else 1 if n.endswith(".webm") else 2
            return (pri, int(f.get("size") or 0) if int(f.get("size") or 0) > 0 else 1e18)
        best = sorted(cands, key=score)[0]
        r.url = f"{self.base}/download/{r.id}/{urllib.parse.quote(best['name'])}"
        r.size = int(best.get("size") or 0)
        if best.get("width") and best.get("height"):
            r.width, r.height = int(best["width"]), int(best["height"])
        lic = (j.get("metadata") or {}).get("licenseurl")
        if isinstance(lic, str):
            r.license_url = lic
            r.license = _license_name(lic)
        return r


def _license_name(u):
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/?([\d.]*)", u or "")
    if not m:
        return "Open licence"
    return ("CC " + m[2].upper() + (" " + m[3] if m[3] else "")) if m[1] == "licenses" else "Public domain (" + m[2].upper() + ")"


class Commons:
    name, label, needs_key = "commons", "Wikimedia Commons", False
    base = "https://commons.wikimedia.org/w/api.php"

    def search(self, q, n, page, o):
        params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                  "gsrsearch": f"filetype:video {q}", "gsrlimit": min(n, 50), "gsroffset": (page - 1) * min(n, 50),
                  "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata|mediatype", "iiurlwidth": 640}
        j = get_json(f"{self.base}?{urllib.parse.urlencode(params)}")
        pages = sorted((j.get("query", {}).get("pages", {})).values(), key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            ii = (p.get("imageinfo") or [{}])[0]
            if not ii.get("url"):
                continue
            md = ii.get("extmetadata", {})
            title = re.sub(r"^File:", "", p.get("title", "")).rsplit(".", 1)[0]
            dur = md.get("Duration", {}).get("value") if isinstance(md.get("Duration"), dict) else None
            out.append(Result("commons", str(p.get("pageid")), title, ii.get("descriptionurl", ""), ii.get("thumburl", ""),
                              ii["url"], ii.get("width") or 0, ii.get("height") or 0, float(ii.get("duration") or dur or 0),
                              _strip(md.get("Artist", {}).get("value", "")), md.get("LicenseShortName", {}).get("value", ""),
                              md.get("LicenseUrl", {}).get("value", ""), ii.get("size") or 0))
        return out


PROVIDERS = {c.name: c() for c in (Pexels, Pixabay, Archive, Commons)}


def provider_status():
    return [dict(name=p.name, label=p.label, needs_key=p.needs_key, ready=(not p.needs_key) or bool(api_key(p.name)))
            for p in PROVIDERS.values()]


def search(query, sources=None, per_source=12, page=1, loop_hint=True, min_height=0, min_seconds=0.0, max_seconds=0.0,
           orientation="", max_height=1080):
    """Returns (results, errors). A failing source never stops the others."""
    sources = sources or [n for n, p in PROVIDERS.items() if (not p.needs_key) or api_key(n)]
    q = query.strip()
    results, errors = [], {}
    for name in sources:
        p = PROVIDERS.get(name)
        if not p:
            errors[name] = "Unknown source"
            continue
        qq = q + (" loop" if loop_hint and name in ("pexels", "pixabay", "commons") and "loop" not in q.lower() else "")
        try:
            rs = p.search(qq, per_source, page, dict(orientation=orientation, max_height=max_height))
        except Exception as e:
            errors[name] = str(e)
            continue
        for r in rs:
            if min_height and r.height and r.height < min_height:
                continue
            if min_seconds and r.duration and r.duration < min_seconds:
                continue
            if max_seconds and r.duration and r.duration > max_seconds:
                continue
            results.append(r)
    return results, errors


# ---------------------------------------------------------------------------------------------
# downloading
# ---------------------------------------------------------------------------------------------
def safe_fname(s):
    return re.sub(r"[^\w.\-]+", "_", s)[:80].strip("_") or "video"


def download_file(url, dst, ctl: Ctl | None = None, cb=None, max_bytes=2_000_000_000):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    with http(url, timeout=40) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        if total and total > max_bytes:
            raise RuntimeError(f"File is too large ({total / 1e9:.1f} GB)")
        got = 0
        while True:
            if ctl and ctl.cancel:
                raise Cancelled()
            b = r.read(1 << 18)
            if not b:
                break
            f.write(b)
            got += len(b)
            if got > max_bytes:
                raise RuntimeError("File is too large")
            if cb and total:
                cb(got / total)
    os.replace(part, dst)
    return dst


def ext_from(url, ctype=""):
    e = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if e in VIDEO_EXT:
        return e
    return {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "image/gif": ".gif"}.get(ctype.split(";")[0], ".mp4")


def fetch_result(r: Result, rawdir, ctl=None, cb=None):
    """Download one search result into rawdir. Returns the file path."""
    p = PROVIDERS.get(r.provider)
    if not r.url and p and hasattr(p, "resolve"):
        p.resolve(r)
    if not r.url:
        raise RuntimeError("No download link")
    dst = Path(rawdir) / f"{r.provider}_{safe_fname(r.id)}{ext_from(r.url)}"
    return download_file(r.url, dst, ctl, cb)


def fetch_url(url, rawdir, ctl=None, cb=None):
    """A direct video link, or (if yt-dlp is installed) a page link. You are responsible for having the right to use it."""
    url = url.strip()
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if ext in VIDEO_EXT:
        return download_file(url, Path(rawdir) / ("url_" + safe_fname(Path(urllib.parse.urlparse(url).path).name)), ctl, cb)
    exe = shutil.which("yt-dlp")
    if not exe:
        raise RuntimeError("That is not a direct video file link. Install yt-dlp (pip install yt-dlp) to download from web pages.")
    out = str(Path(rawdir) / "ytdlp_%(id)s.%(ext)s")
    Path(rawdir).mkdir(parents=True, exist_ok=True)
    before = set(os.listdir(rawdir))
    subprocess.run([exe, "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--no-playlist", "-o", out, "--merge-output-format", "mp4", url],
                   check=True, capture_output=True)
    new = [f for f in os.listdir(rawdir) if f not in before and not f.endswith(".part")]
    if not new:
        raise RuntimeError("yt-dlp did not produce a file")
    return Path(rawdir) / new[0]


def write_credits(outdir, rows):
    """Append who made what and under which licence to credits.csv in the output folder."""
    path = Path(outdir) / "credits.csv"
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["file", "title", "author", "licence", "licence_url", "source_page", "downloaded"])
        for r in rows:
            w.writerow(r)
