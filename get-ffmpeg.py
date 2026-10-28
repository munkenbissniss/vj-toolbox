#!/usr/bin/env python3
"""Downloads ffmpeg.wasm (the video engine of the browser version) into docs/tools/vendor/.
Run this once, then commit the vendor folder together with the rest of docs/ (about 31 MB).

    python3 get-ffmpeg.py

The files come from the official npm packages @ffmpeg/ffmpeg and @ffmpeg/core (single-thread build, so the page needs no
special server headers and works on GitHub Pages)."""
import argparse
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

FFMPEG_VERSION, CORE_VERSION = "0.12.10", "0.12.6"
MIRRORS = ["https://registry.npmjs.org", "https://registry.npmmirror.com"]
VENDOR = Path(__file__).resolve().parent / "docs" / "tools" / "vendor"


def fetch(registries, pkg, ver):
    name = pkg.split("/")[1]
    err = None
    for reg in registries:
        url = f"{reg}/{pkg}/-/{name}-{ver}.tgz"
        try:
            print(f"  {url}")
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "vj-toolbox"}), timeout=120) as r:
                return r.read()
        except Exception as e:
            err = e
            print(f"    failed: {e}")
    sys.exit(f"Could not download {pkg}@{ver}: {err}")


def extract(blob, subdir, dest, only=None):
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        for m in t.getmembers():
            if not m.isfile() or not m.name.startswith(subdir):
                continue
            base = m.name[len(subdir):]
            if "/" in base or base.endswith(".map") or base.endswith(".d.ts") or (only and base not in only):
                continue
            (dest / base).write_bytes(t.extractfile(m).read())
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", action="append", help="npm registry to use (default: npmjs.org, then a mirror)")
    a = ap.parse_args()
    regs = a.registry or MIRRORS
    print("Downloading ffmpeg.wasm …")
    lib = fetch(regs, "@ffmpeg/ffmpeg", FFMPEG_VERSION)
    core = fetch(regs, "@ffmpeg/core", CORE_VERSION)
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    n1 = extract(lib, "package/dist/esm/", VENDOR / "ffmpeg")
    n2 = extract(core, "package/dist/esm/", VENDOR / "core", only={"ffmpeg-core.js", "ffmpeg-core.wasm"})
    need = [VENDOR / "ffmpeg" / "index.js", VENDOR / "ffmpeg" / "worker.js", VENDOR / "core" / "ffmpeg-core.js", VENDOR / "core" / "ffmpeg-core.wasm"]
    missing = [str(p.relative_to(VENDOR)) for p in need if not p.exists()]
    if missing:
        sys.exit(f"Unexpected package layout, missing: {', '.join(missing)}")
    size = sum(p.stat().st_size for p in VENDOR.rglob("*") if p.is_file()) / 1e6
    print(f"Done: {n1 + n2} files, {size:.0f} MB in {VENDOR}")
    print("Now commit docs/ (including docs/tools/vendor) and push.")


if __name__ == "__main__":
    main()
