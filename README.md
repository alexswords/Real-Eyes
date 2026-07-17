# Real Eyes 💿

A local web app for pulling media out of web pages — live ones, or a site's entire archived history in the Wayback Machine. Built on Flask and BeautifulSoup, with the Wayback CDX API handling whole-site indexing.

## How it works

- **Page scrapes** are fetched with Requests and parsed with BeautifulSoup, which collects image, video, and audio references from the page's markup (including srcset variants, lazy-load attributes, media links, and social-card metadata).
- **Site scrapes** query the Wayback Machine's CDX index instead of crawling, returning every media file ever archived for a domain (or a single folder of one), up to 100,000 entries, streamed in as they're found.
- **Playback** happens in a floating viewer. Modern formats play natively; Flash runs in the Ruffle emulator, and legacy video/audio (old QuickTime, RealMedia, WMV, FLV…) is converted with ffmpeg. Both runtimes are downloaded on first use and cached — nothing is bundled.
- Results are shown in a virtualized grid or list that stays smooth at 100k files, with filtering by name, type, archive date, and size.

## Running it

From source:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py     # http://127.0.0.1:5001
```

Or build the Mac app:

```bash
bash build_app.sh           # emits "Real Eyes.app"; right-click → Open on first launch
```

The server shuts itself down a few minutes after the last tab closes.

## Tips

- **Source and scope are independent:** pick *Live web*, *Wayback archive*, or *Both* — which scrapes the live site and then adds everything the archive holds that the live site no longer serves, tagging those files “archived” — then choose how far to reach — one page, this URL’s folder, or the whole site. Live folder/site scrapes crawl up to ~120 pages of the site itself; Wayback folder/site scrapes read the archive’s index across all history.
- **Sites on big hosts:** use *Site folder* scope for anything living inside a larger domain (e.g. `bighost.com/~someone/site`) — *Entire site* would cover the whole hosting service.
- **File-type toggles start off** after a scrape — flip on what you want, or hit **All**. On big grabs, set **Min size** to 5–25 KB to hide icons, spacers, and tracking pixels in one move.
- **Stopping is safe:** *Stop Scraping* keeps everything gathered so far, fully browsable and downloadable.
- **Time:** *Now* means the newest state (freshest captures on the archive side); *All time* spans the archive’s full history — single pages resolve to their *median* capture, usually the site in its prime; *Custom* opens two calendars to bracket an era.
- **Selecting:** hover a card for its checkbox; shift-click selects ranges. The zip button downloads your selection if one exists, otherwise everything shown. Big zips run in the background — cancelling still delivers a partial archive.
- **First-time waits:** the first .swf triggers a one-time Ruffle download, and the first legacy video downloads ffmpeg then converts (a minute or so). Both are cached; repeats are instant.
- **Viewer:** drag it by its title bar, resize from the bottom-left grip, ⚙ for settings (auto-size, mute, loop, dark background — handy for transparent images). Arrow keys step through files; Esc minimizes to a tray pill.
- Be gentle with the Internet Archive — it's a nonprofit — and respect the terms and copyright of whatever you scrape.
