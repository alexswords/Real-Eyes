# Real Eyes 💿

A local Flask application for exhaustive media extraction from live web pages and from the full archived history of sites in the Internet Archive's Wayback Machine. Single-page scrapes are parsed with **BeautifulSoup**; whole-site scrapes are driven by the Wayback CDX index API. Results render in a virtualized, filterable library with an in-browser viewer that plays defunct formats (Flash, Sorenson-era QuickTime, RealMedia) through lazily provisioned emulation and transcoding runtimes.

## Architecture

Three components, no external services:

- **`scraper.py`** — extraction logic. For page scrapes, BeautifulSoup (`html.parser`) walks the DOM and collects media references from `<img>` (`src`, lazy-load `data-src`, and full `srcset` candidate lists), `<video>`/`<audio>` elements and their `<source>` children, `<picture>` sources, direct `<a href>` links to media files, Open Graph / Twitter Card `<meta>` tags, and `url()` references inside inline `style` attributes. URLs are resolved against the document base with `urljoin`, deduplicated, classified by extension/MIME into image/video/audio/other, and filtered of Wayback toolbar noise. For site scrapes, the CDX API is paged via `resumeKey` (10k rows/request, 100k cap) with server-side MIME and status filters, `collapse=urlkey` deduplication, and byte sizes from the `length` field. Playback URLs are constructed with the correct Wayback modifier per type (`im_` for images, `id_` — verbatim original bytes — for everything else).
- **`app.py`** — Flask server (threaded). `/api/scrape` streams NDJSON: status lines, incremental 250-item batches, and progress counts, so the client renders results as they arrive and an aborted scrape keeps everything gathered. Fetch proxies (`/api/raw`, `/api/download`) retry Wayback modifier variants until one returns 200. `/api/transcode` shells out to a static ffmpeg build (`libx264`/`aac`, even-dimension scale filter for vintage frame sizes, error-tolerant retry pass, audio-only last resort) with SHA-1-keyed on-disk caching. Zip exports run as background jobs with progress polling, cancellation, and 429/503 backoff. A heartbeat watchdog exits the process after ~3 minutes without a client; version/shutdown endpoints let a newer build replace a running server.
- **`templates/index.html`** — self-contained frontend, no framework. The results grid is windowed/virtualized (only on-screen cards exist in the DOM), so 100k-item libraries scroll without jank in either grid or compact-list mode. Client-side filtering: substring search, archive-date range, minimum size, per-extension toggles, and a folder tree reconstructed from original URL paths. The floating viewer is draggable/resizable, auto-fits media dimensions, and detects silent video-decode failures (zero decoded frames with a live audio track) to reroute files through the transcoder automatically.

## Runtime provisioning

Nothing heavy ships in the repo or the app bundle. On first use the server downloads and caches into `~/Library/Application Support/Real-Eyes/`:

- **Ruffle** (Wasm Flash emulator, ~10 MB) — fetched from its GitHub releases when the first `.swf` is opened; served at `/ruffle/`.
- **ffmpeg** (static single binary for the host arch) — fetched when the first legacy video/audio file needs conversion; outputs are cached MP4s.
- A Python virtualenv with the three dependencies in `requirements.txt` (Flask, BeautifulSoup4, Requests), created by the launcher on first run.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py     # http://127.0.0.1:5001
```

## macOS app bundle

```bash
bash build_app.sh           # emits "Real Eyes.app"
```

The bundle is a launcher script around the same source: it provisions the venv, version-checks any running server (replacing stale ones), starts Flask headless, and opens the browser when the port answers. Unsigned — right-click → Open on first launch.

## Notes

- Ruffle and ffmpeg are downloaded from their official release channels at runtime and are not distributed with this repository.
- Respect the terms of service and copyright of scraped sites, and rate-limit your enthusiasm toward the Internet Archive — it's a nonprofit.
