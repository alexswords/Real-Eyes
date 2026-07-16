# Real Eyes 💿

A local media scraper with a Windows-7-glass, sage-green interface. Paste a URL and harvest every image, video, audio file, and Flash movie it references — from the live web or from the entire archived history of a site via the Wayback Machine.

## Features

- **Three scrape modes** — a single page (live or a Wayback snapshot, defaulting to the median capture date), a *local site* scoped to one folder on a big hosting domain, or an *entire site's* full archived history via the CDX index (up to 100,000 files, streamed with live progress and stoppable at any point keeping partial results).
- **Virtualized library** — endless-scroll grid or compact list view that stays smooth at 100k files, with a folder tree, per-extension toggle switches, filename search, archive-date range, minimum-size filter, and sorting by type/name/date/size.
- **Floating viewer** — draggable, resizable, minimizes to a tray pill; auto-sizes to each file; slideshow-free navigation (±1, ±10, first/last, arrow keys).
- **Plays dead formats** — Flash via the Ruffle emulator, and vintage video/audio (Sorenson QuickTime, RealMedia, WMV, FLV…) through an on-demand ffmpeg converter. Both runtimes download and cache themselves on first use; nothing is bundled.
- **Bulk export** — hand-pick files with checkboxes/shift-click, download selections or entire filtered sets as a zip (background job with progress and cancel), or export the URL list as text.
- The server shuts itself down a few minutes after the last tab closes.

## Run from source

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py    # then open http://127.0.0.1:5001
```

## Build the Mac app

```bash
bash build_app.sh          # produces "Real Eyes.app"
```

First launch: right-click → Open (unsigned app). The app creates its environment in `~/Library/Application Support/Real-Eyes` on first run.

## Notes

- Ruffle (Flash) and ffmpeg are fetched from their official releases at first use and cached locally; they are not part of this repository.
- Respect the terms of service and copyright of any site you scrape, and be gentle with the Internet Archive — it's a nonprofit.
