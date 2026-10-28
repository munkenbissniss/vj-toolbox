# VJ Toolbox

Small, sharp tools for people who make VJ loops and digital visuals. Everything runs on your own computer with ffmpeg: no uploads, no accounts (except free API keys if you want Pexels/Pixabay search).

| Tool | What it does |
|---|---|
| **Convert** | Bulk convert mov, webm, mkv, avi, gif … to VJ-friendly mp4 (or HAP / ProRes) and rename them in bulk |
| **GIF → MP4** | Bulk convert GIFs (also transparent ones) to smooth mp4 |
| **Make loops** | Checks if a clip loops. If not: trims it to the best loop point, plays it forward + backward, or crossfades the end into the start |
| **Find loops** | Searches free sources (Pexels, Pixabay, Wikimedia Commons, NASA, Internet Archive, Prelinger Archives), downloads the clips you pick, converts them, and saves who made them and under which licence |

It has a **web interface** (same look as Munken Loops) and a **command line** (`vj`). The results are ordinary mp4 files that go straight into the [Munken Loops](https://github.com/munkenbissniss/munken-loops) mixer, Resolume, VDMX, TouchDesigner, OBS …

## Two versions

| | Desktop (this repo's `vj` command and `vj ui`) | Browser version (`docs/`) |
|---|---|---|
| Runs | on your computer with ffmpeg | in the browser (ffmpeg compiled to WebAssembly), nothing to install |
| Speed | fast | about 5-10 times slower |
| Formats | everything ffmpeg reads, HAP, ProRes, HDR (iPhone) to SDR, HEVC | H.264 mp4 output; no HAP/ProRes/HDR conversion; HEVC (iPhone) input may not open |
| Find loops | all four sources, downloads always work | search works; downloads only where the site allows web pages to fetch files |
| Best for | big batches, 4K, long clips | quick jobs anywhere, and a public website |

## Install the desktop version (Ubuntu)

```bash
sudo apt install ffmpeg python3-venv
git clone https://github.com/munkenbissniss/vj-toolbox.git && cd vj-toolbox
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

macOS: `brew install ffmpeg`. Windows: install ffmpeg and make sure `ffmpeg` and `ffprobe` are on the PATH. Optional: `pip install yt-dlp` to also download from web page links.

## Start the web interface

```bash
python3 vj ui            # then open http://127.0.0.1:8090
```

Add files by dragging them in, or type a **folder on your computer** (best for big files: nothing is copied). Pick the tool in the tabs, adjust the settings, press the button. Jobs run in a queue; you can preview the result, stop a job, or download everything as a zip. Use `--data` to choose where uploads and results are kept, and `--host 0.0.0.0` to reach it from another machine (there is no password, so only on a network you trust).

## The browser version (for a website)

`docs/` is a ready-made static site. `docs/index.html` is a small start page and `docs/tools/` is the app (same four tools, same look as the web interface above). It needs no server: put it on GitHub Pages.

1. Download the video engine once and commit it (it is about 31 MB):
   ```bash
   python3 get-ffmpeg.py
   git add -A && git commit -m "Add ffmpeg.wasm" && git push
   ```
2. On GitHub: **Settings → Pages → Deploy from a branch → main, folder `/docs`**. The tools are then at `https://munkenbissniss.github.io/vj-toolbox/tools/`.

To try it locally: `python3 -m http.server -d docs 8000`, then open <http://localhost:8000/tools/>.

Good to know:

- Files are read straight from disk and never uploaded. Results stay in the page until you download them (single files, a zip, or **Save all to a folder** in Chrome/Edge). With "Save each file straight into a folder" on, each result is written as soon as it is ready, which is best for big jobs.
- It runs one file at a time and is slower than the desktop version; 1920×1080 or smaller is the sweet spot. Stop it at any time.
- Pexels and Pixabay keys are stored in your browser only and sent only to those two sites. Some sources do not let a web page download their files; the app then tells you and you can use *Open page*, download the clip yourself and drop it into Convert.
- You can link straight to a tool: `…/tools/#loop`.

## What "VJ-friendly" means

The default profile (change anything you like):

- **H.264 mp4**, 8-bit `yuv420p`, plays everywhere (HAP and ProRes are available too; HAP files are large but cost the computer almost nothing to play).
- **Fits inside 1920×1080**, never enlarged (unless you say so), even width and height. Portrait clips stay portrait.
- **Constant frame rate**, snapped to 24/25/30/50/60 (max 60). GIFs are limited to 30.
- **Keyframe every 30 frames** (`--gop`), so scrubbing and looping are smooth in VJ software.
- **No sound** and no metadata (iPhone location etc. is removed).
- **HDR from iPhones is converted to normal colours** (no washed-out or grey look).
- **Transparent GIFs / webm** are placed on a solid background (`--bg`, black by default).
- Clips can be **repeated until a minimum length** (`--min-seconds`), for example 16 s for the Munken Loops mixer.

## Command line

```bash
python3 vj convert  ~/Downloads/clips  -o vj-out --name "{n3}_{name}" --case lower
python3 vj gif2mp4  ~/gifs -o vj-out --upscale --size 1920x1080 --min-seconds 16
python3 vj loop     ~/clips -o loops --check             # only report which clips already loop
python3 vj loop     ~/clips -o loops --min-seconds 20
python3 vj search   "neon tunnel" --limit 12             # list results
python3 vj search   "neon tunnel" --download 1,3,5-8 --loopify auto --name "neon_{n2}" -o stock
python3 vj fetch    https://example.com/clip.mp4 -o stock
python3 vj keys --pexels YOURKEY --pixabay YOURKEY
```

Inputs can be files and folders (sub folders are included; `--no-recursive` to skip). Existing files are never overwritten: a number is added (`clip-2.mp4`) unless you use `--overwrite`. Run any command with `--help` for everything.

### Output options (all tools)

| Option | Default | Meaning |
|---|---|---|
| `--codec` | `h264` | `h264`, `hap`, `hap_q`, `prores` (HAP/ProRes are saved as .mov) |
| `--size` | `1920x1080` | Size box. Portrait material turns the box automatically |
| `--fit` | `keep` | `keep` = keep shape, only shrink · `fit` = letterbox to exactly the box · `fill` = crop to exactly the box |
| `--upscale` | off | With `keep`: also enlarge small videos up to the box |
| `--fps` | `0` | `0` = keep source (snapped to a standard rate) |
| `--crf` | `17` | h264 quality, lower = better and larger (14-23 is sensible) |
| `--preset` | `medium` | x264 speed. Slower = smaller file |
| `--gop` | `30` | Keyframe interval in frames |
| `--bg` | `black` | Background for transparent sources (`white`, `#112233` …) |
| `--min-seconds` | `0` | Repeat whole loops until the clip is at least this long |
| `--no-tonemap` | off | Do not convert HDR to SDR |
| `--keep-audio` | off | Keep sound (h264 only) |
| `-o`, `--out` | `vj-out` | Output folder |
| `--jobs` | `2` | Files converted at the same time |
| `--overwrite` | off | Overwrite instead of adding `-2`, `-3` |

### Bulk naming

`--name` is a template. Tokens:

| Token | Gives |
|---|---|
| `{name}` | the original file name without extension |
| `{n}` `{n2}` `{n3}` `{n4}` | running number (no padding, 2, 3 or 4 digits); also `{n:03}` |
| `{w}` `{h}` `{res}` `{fps}` `{dur}` | width, height, `1920x1080`, frame rate, length (`12s`) |
| `{folder}` | name of the folder the file was in |
| `{date}` | today, `20261027` |

Plus `--prefix`, `--suffix`, `--start` (first number), `--order name|date|size` (numbering order, names sort naturally: `clip2` before `clip10`), `--case keep|lower|upper`, `--spaces _|-|keep`, and `--no-ascii`. By default names are made safe for any software: `Ærlig Åse (final).mov` becomes `Aerlig_Ase_final.mp4`. The web interface shows a live preview of the new names before you run anything.

## Make loops

```bash
python3 vj loop clips/ -o loops                    # auto
python3 vj loop clips/ -o loops --mode pingpong
python3 vj loop clips/ -o loops --mode crossfade --fade 2
```

| Mode | What happens | Best for |
|---|---|---|
| `auto` (default) | Clips that already loop are left alone. If cutting a little off the end gives a clean loop (keeping at least 60 % of the clip), it does that. Otherwise it uses `--fallback` (`pingpong`, or `crossfade`) | everything |
| `pingpong` | Plays forward, then backward (first and last picture are not repeated, so there is no stutter). The clip becomes twice as long | camera moves, waves, anything that looks fine in reverse |
| `crossfade` | The end is dissolved into the start with a smooth ease. The clip becomes `--fade` seconds shorter | clouds, smoke, abstract motion; not for things with clear direction |
| `trim` | Cuts at the best loop point only | clips that almost loop |
| `none` | Just converts | |

**How the loop check works.** The clip is shrunk to 64×36 grey pictures. The difference between the last and the first picture (the jump when it starts again) is compared with the difference between ordinary neighbouring pictures. If the jump is no bigger than a normal step (≤ 1.25×), the loop point is invisible. The same numbers are used to find the best place to cut: after frame *k*, the next frame *k+1* should look like frame 0. `--check` (and the "Check which clips already loop" button in the web interface) shows the verdict without converting: *seamless*, *almost* or *jump*.

Pingpong reverses in small chunks, so long and large clips do not eat all your memory.

## Find loops

Sources:

| Source | Needs | Notes |
|---|---|---|
| Internet Archive | nothing | Only items with a Creative Commons or public-domain licence |
| Wikimedia Commons | nothing | Free-licence videos; many need credit (CC BY, CC BY-SA) |
| NASA Image and Video Library | nothing | Space footage; generally public domain, but check the item for logos and people |
| Prelinger Archives | nothing | Old ephemeral films from the Internet Archive; public domain. Great for retro VJ material |

Pixabay can be limited to *animations only* or *films only*, and **Load more results** fetches the next page.

Sources without a public API (Mixkit, Videvo, Coverr, Mazwai and similar) can't be searched here, and Coverr's API terms forbid commercial use. Download from their sites yourself, then use **Convert**.
| Pexels | free API key | Free to use, no credit required (but appreciated) |
| Pixabay | free API key | Free to use under the Pixabay Content License |

Get a key in a minute at [pexels.com/api](https://www.pexels.com/api/) and [pixabay.com/api/docs](https://pixabay.com/api/docs/), then `python3 vj keys --pexels KEY --pixabay KEY` (or type them in the web interface; or set `PEXELS_API_KEY` and `PIXABAY_API_KEY`). Keys are stored in `~/.config/vjtools/config.json` on your computer only.

The search adds the word *loop* by default (switch off with `--no-loop-word`), and you can filter by size, length and orientation. Downloads are converted with the same profile and naming as everything else, and `--loopify auto` also turns them into seamless loops. **Every download is listed in `credits.csv`** (file, title, author, licence, link to the page, date), so you can give credit where the licence asks for it.

You are responsible for checking that you may use a clip the way you plan to. Only sources that publish their material for reuse are searched; the tool does not scrape YouTube or similar. `vj fetch` takes direct file links, and page links if `yt-dlp` is installed. Use it only for material you have the right to use.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

```bash
node --test tests/web/test_core.mjs      # browser version: naming, fps/size rules, probe parsing, zip, loop maths
python3 tests/web/ui_test.py             # browser version in a real browser (needs playwright for Python and Chromium)
```

The browser tests replace ffmpeg.wasm with the computer's own ffmpeg, so they check all of the app's logic against real ffmpeg; they do not test the download or speed of ffmpeg.wasm itself.

The Python tests make their own small videos with ffmpeg, check conversion, naming, loop detection and the pingpong/crossfade/trim results frame by frame, run the whole web API, and test all four search sources against a built-in fake server (so they need no internet or keys).

## Layout

```
get-ffmpeg.py         downloads ffmpeg.wasm for the browser version
docs/index.html       start page for GitHub Pages
docs/tools/           the browser app: app.js (UI), core.js (profile, naming), loops.js, search.js, zip.js, engine.js
vj                    command line launcher (python3 vj ...)
vjtools/core.py       ffmpeg helpers, the VJ-friendly profile, naming
vjtools/tools.py      batch convert / gif2mp4 / loops / download
vjtools/loops.py      loop analysis and loop making
vjtools/search.py     search sources, downloads, credits
vjtools/server.py     web interface (Flask) + static/index.html
tests/                Python tests, the fake search server, browser tests (tests/web)
```

## Roadmap

These tools are the first pieces of a bigger toolbox for VJs and digital artists: the Munken Loops mixer, more tools, a library of VJ loops, and info. Ideas are welcome.

## Licence

MIT, see `LICENSE`.
