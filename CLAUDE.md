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
  - `/api/download`, `/api/raw` (Flash passthrough),
    `/api/transcode` (ffmpeg, 3-pass fallback), `/ruffle/<f>` (Flash emulator).
    ffmpeg + Ruffle are downloaded & cached in `~/Library/Application
    Support/Real-Eyes/` on first use — nothing bundled.
  - Zip export: `_zip_worker` + `/api/zip_start|status|cancel|file` (~540+).
  - Watchdog: exits after 3 min without `/api/ping` — but never while a scrape
    (`ACTIVE_SCRAPES`) or zip job runs.
  - Progress is sacred: the frontend salvages streamed items on ANY error
    (partial results + error banner), the CDX sweep failing mid-run is
    non-fatal (deep pass still runs, done line notes the gap), and
    `beforeunload` warns while a scrape is active.
- `scraper.py` (~470 lines) — no Flask imports; pure scraping helpers.
  - `classify` (ext→type), `extract_media` (BeautifulSoup DOM walk: img/srcset,
    video/audio/source, a[href], og/twitter meta, inline-style url()).
  - `norm_url`: scheme/www/port-insensitive identity, strips Wayback wrapping —
    the key for live-vs-archive matching and all dedup.
  - CDX: `_cdx_target` (host or host+folder), `cdx_fetch_pages` (paged media
    index w/ resumeKey, 429/5xx backoff), `cdx_html_pages` (archived HTML pages
    for deep site scans — up to 4 captures spread per page, deterministic, capped
    500 reads), `page_snapshots` (a page's capture history; deep=daily capped
    500, else ~12 samples), `parse_cdx_row`.
  - `fetch_page_retry`: 429/5xx backoff — ALL archive page reads go through it
    (plain `fetch_page` is for live pages only). Skipping failures silently made
    deep runs nondeterministic; skips are now counted and reported in `done.url`.
  - `crawl_pages`: BFS over live site, same-host, optional folder prefix.
- `templates/index.html` (~1400 lines) — entire UI, ONE `<script>` block.
  - ~340–460: markup (form, source/time/scope rows, chip calendars, toolbar,
    chips + folder tree + grid).
  - ~500–570: `updateFlow` progressive reveal; deep checkbox rules + `deeplbl`.
  - ~600–680: submit handler, NDJSON reader.
  - ~775–870: `visible()` filter chain, folder tree, extension chips.
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
  it. Thumbnails retry twice with backoff (`__thumbRetry`) before falling back
  to a placeholder icon.
- ALL Python HTTP goes through `scraper.SESSION` (shared keep-alive pool) —
  per-request connections got the whole IP refused ([Errno 61]) during deep
  scans. Never call bare `requests.get`. ConnectionError = archive shedding
  load: back off tens of seconds, not single-digit.
- Deep-found items get `deep: true` from the backend and a red DEEP badge in
  grid/list/viewer (set only in the site/local deep page-read pass).
- Deep semantics: page scope = read every capture; site/local = also read the
  site's archived HTML pages (catches media on other domains / CSS-only refs).
- `time` param: now / range (tfrom+tto, YYYYMMDD) / all; "now" archive side =
  last year of captures.
- After changing files: `python3 -m py_compile app.py scraper.py` and
  `node --check` on the extracted script block. Commit; user pushes and runs
  `build_app.sh` manually.
