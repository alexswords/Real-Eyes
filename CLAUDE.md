# Real Eyes — codebase map for Claude

Local Flask app: paste a URL, harvest every image/video/audio/Flash file from the
live web and/or the Wayback Machine. Run: `bash run.sh` (port 5001). Build the
macOS app: `bash build_app.sh`.

## Files

- `app.py` (~650 lines) — all Flask routes.
  - `/api/scrape` (~line 55): the big one. Streams NDJSON (`{status}`, `{items}`,
    `{meta}`, `{done}`/`{error}` lines). Two branches: site/local scope (live BFS
    crawl → CDX index sweep → deep archived-page reads) and single-page scope
    (live fetch → snapshot walk → folder-index sweep → rarity `meta.snaps`).
  - `_wayback_variants`/`_fetch_upstream` (~270): retry a file across Wayback
    playback modifiers (`id_`, `im_`, `oe_`).
  - `/api/download`, `/api/raw` (Flash passthrough), `/api/animated`,
    `/api/transcode` (ffmpeg, 3-pass fallback), `/ruffle/<f>` (Flash emulator).
    ffmpeg + Ruffle are downloaded & cached in `~/Library/Application
    Support/Real-Eyes/` on first use — nothing bundled.
  - Zip export: `_zip_worker` + `/api/zip_start|status|cancel|file` (~540+).
  - Watchdog: exits after 3 min without `/api/ping` (unless a zip job runs).
- `scraper.py` (~470 lines) — no Flask imports; pure scraping helpers.
  - `classify` (ext→type), `extract_media` (BeautifulSoup DOM walk: img/srcset,
    video/audio/source, a[href], og/twitter meta, inline-style url()).
  - `norm_url`: scheme/www/port-insensitive identity, strips Wayback wrapping —
    the key for live-vs-archive matching and all dedup.
  - CDX: `_cdx_target` (host or host+folder), `cdx_fetch_pages` (paged media
    index w/ resumeKey, 429/5xx backoff), `cdx_html_pages` (archived HTML pages,
    for deep site scans), `page_snapshots` (a page's capture history; deep=daily
    capped 500, else ~12 samples), `parse_cdx_row`.
  - `_gif_scan`/`is_animated_gif`: real GIF block-structure parser; tri-state
    True/False/None — None (undetermined) must never hide a file.
  - `crawl_pages`: BFS over live site, same-host, optional folder prefix.
- `templates/index.html` (~1400 lines) — entire UI, ONE `<script>` block.
  - ~340–460: markup (form, source/time/scope rows, chip calendars, toolbar,
    chips + folder tree + grid).
  - ~500–570: `updateFlow` progressive reveal; deep checkbox rules + `deeplbl`.
  - ~600–680: submit handler, NDJSON reader.
  - ~775–900: `visible()` filter chain, folder tree, extension chips,
    `probeGifs` (4 workers, tri-state `m.animated`).
  - Further down: virtualized grid/list (fine at 100k), selection/shift-click,
    floating viewer (Ruffle/transcode playback), exports, idle ping.
- `build_app.sh` — builds `Real Eyes.app`; launcher checks `/api/version`,
  replaces stale servers. `VERSION` is gitignored, generated at build.

## Conventions & gotchas

- Wayback playback URLs: `https://web.archive.org/web/<ts><mod>/<original>`;
  `im_` = image bytes, `id_` = untouched original. Media from CDX rows get these
  modifiers in `parse_cdx_row`.
- Dedup is by `norm_url`, never raw URL. In "both" mode, archive items matching
  a live norm are dropped; survivors get `origin: "archive"` (UI badge).
- The archive rate-limits (429) aggressively — every CDX caller must tolerate
  it, and any per-file probe failure must degrade to "unknown", not "negative".
- Deep semantics: page scope = read every capture; site/local = also read the
  site's archived HTML pages (catches media on other domains / CSS-only refs).
- `time` param: now / range (tfrom+tto, YYYYMMDD) / all; "now" archive side =
  last year of captures.
- After changing files: `python3 -m py_compile app.py scraper.py` and
  `node --check` on the extracted script block. Commit; user pushes and runs
  `build_app.sh` manually.
