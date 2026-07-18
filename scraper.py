"""Extract all media URLs (images, video, audio) from a web page."""
from __future__ import annotations

import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif",
            ".tif", ".tiff", ".heic", ".heif", ".apng", ".jfif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".ogv", ".mkv", ".flv", ".wmv", ".avi",
              ".mpg", ".mpeg", ".3gp", ".m2v", ".mts"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".wma",
              ".aiff", ".aif", ".mid", ".midi", ".amr", ".oga"}
OTHER_EXTS = {".swf"}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Real-Eyes/1.0)"}


def classify(url: str) -> str | None:
    path = urlparse(url).path
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in IMG_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in OTHER_EXTS:
        return "other"
    return None


def is_wayback_noise(url: str) -> bool:
    """True for Wayback Machine's own toolbar/player assets, not archived content."""
    p = urlparse(url)
    return p.netloc.endswith("archive.org") and p.path.startswith(("/_static/", "/static/"))


def resolve_wayback(url: str, date: str | None = None) -> str | None:
    """Return the closest Wayback snapshot URL for `url`, or None if not archived.

    `date` may be YYYY-MM-DD or YYYYMMDD; omitted = newest snapshot.
    """
    params = {"url": url}
    if date:
        params["timestamp"] = date.replace("-", "")
    r = requests.get("https://archive.org/wayback/available", params=params,
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    snap = r.json().get("archived_snapshots", {}).get("closest")
    if snap and snap.get("available"):
        return snap["url"].replace("http://web.archive.org", "https://web.archive.org", 1)
    return None


def wayback_median_snapshot(url: str, ts_from: str | None = None,
                            ts_to: str | None = None) -> str | None:
    """Snapshot at the MEDIAN capture date for a page (optionally within a range)."""
    params = {"url": url, "output": "json", "fl": "timestamp",
              "filter": "statuscode:200", "limit": "5000"}
    if ts_from:
        params["from"] = ts_from
    if ts_to:
        params["to"] = ts_to
    r = requests.get("https://web.archive.org/cdx/search/cdx", params=params,
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = r.json() if r.text.strip() else []
    stamps = sorted(row[0] for row in rows[1:] if row)
    if not stamps:
        return None
    mid = stamps[len(stamps) // 2]
    return f"https://web.archive.org/web/{mid}/{url}"


def page_snapshots(url: str, ts_from: str | None = None, ts_to: str | None = None,
                   max_snaps: int = 12, deep: bool = False) -> list[str]:
    """Playback URLs across a page's capture history. Normally monthly-collapsed and
    thinned to max_snaps; deep=True keeps every daily capture (capped at 500)."""
    collapse = "timestamp:8" if deep else "timestamp:6"
    params = {"url": url, "output": "json", "fl": "timestamp",
              "filter": "statuscode:200", "collapse": collapse, "limit": "2000"}
    if ts_from:
        params["from"] = ts_from
    if ts_to:
        params["to"] = ts_to
    r = requests.get("https://web.archive.org/cdx/search/cdx", params=params,
                     headers=HEADERS, timeout=60)
    r.raise_for_status()
    rows = r.json() if r.text.strip() else []
    stamps = [row[0] for row in rows[1:] if row]
    if not stamps:
        return []
    cap = 500 if deep else max_snaps
    if len(stamps) > cap:
        step = (len(stamps) - 1) / (cap - 1)
        stamps = [stamps[round(i * step)] for i in range(cap)]
    return [f"https://web.archive.org/web/{ts}/{url}" for ts in stamps]


def _mime_to_type(mime: str) -> str | None:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime in ("application/x-shockwave-flash", "application/vnd.adobe.flash.movie"):
        return "other"
    return None


_WB_PLAYBACK = re.compile(r"https?://web\.archive\.org/web/\d{4,14}(?:[a-z]{2}_)?/(.*)", re.S)


def wayback_original(u: str) -> str:
    """Strip Wayback playback wrapping, returning the originally archived URL."""
    m = _WB_PLAYBACK.match(u)
    return m.group(1) if m else u


def norm_url(u: str) -> str:
    """Scheme/www/port-insensitive identity for matching live vs archived files."""
    u = wayback_original(u)
    p = urlparse(u if "://" in u else "http://" + u)
    host = p.netloc.rsplit("@", 1)[-1].replace(":80", "").replace(":443", "")
    if host.startswith("www."):
        host = host[4:]
    return host + p.path + (("?" + p.query) if p.query else "")


def folder_of(path: str) -> str:
    """Folder containing `path`: files map to their parent, folders to themselves."""
    if not path:
        return "/"
    if path.endswith("/"):
        return path
    last = path.rsplit("/", 1)[-1]
    if "." in last:                      # looks like a file
        return path.rsplit("/", 1)[0] + "/"
    return path + "/"


def crawl_pages(start_url: str, scope: str = "domain", max_pages: int = 120):
    """BFS over same-scope HTML pages of a LIVE site, yielding (url, html)."""
    start = start_url if "://" in start_url else "https://" + start_url
    parsed = urlparse(start)
    netloc = parsed.netloc
    prefix = folder_of(parsed.path) if scope == "path" else "/"
    seen, queue, pages = {start}, deque([start]), 0
    while queue and pages < max_pages:
        u = queue.popleft()
        try:
            r = requests.get(u, headers=HEADERS, timeout=15)
        except requests.RequestException:
            continue
        if "html" not in r.headers.get("Content-Type", ""):
            continue
        pages += 1
        yield r.url, r.text
        for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
            v = urljoin(r.url, a["href"].split("#")[0].strip())
            pv = urlparse(v)
            if pv.scheme not in ("http", "https") or pv.netloc != netloc:
                continue
            if not pv.path.startswith(prefix):
                continue
            if classify(v):              # media links are collected, not crawled
                continue
            if v not in seen and len(seen) < max_pages * 6:
                seen.add(v)
                queue.append(v)
        time.sleep(0.1)                  # politeness


def _cdx_target(url: str, scope: str) -> str:
    """CDX query target for a URL: its host, or host + folder for scope='path'
    (for sites living inside a big hosting domain)."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if scope == "path" and parsed.path.strip("/"):
        return (parsed.netloc + folder_of(parsed.path)).rstrip("/")
    return parsed.netloc or url


def cdx_fetch_rows(url: str, limit: int = 20000) -> list:
    """Raw CDX index rows for every archived media file on a domain."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    domain = parsed.netloc or url
    params = {
        "url": domain + "/*",
        "output": "json",
        "fl": "timestamp,original,mimetype",
        "filter": ["statuscode:200",
                   "mimetype:(image|video|audio)/.*|application/x-shockwave-flash"],
        "collapse": "urlkey",
        "limit": str(limit),
    }
    r = requests.get("https://web.archive.org/cdx/search/cdx", params=params,
                     headers=HEADERS, timeout=90)
    r.raise_for_status()
    return r.json() if r.text.strip() else []


def parse_cdx_row(row, seen: set) -> dict | None:
    """One CDX row -> media item (or None if not media / already seen)."""
    ts, original, mime = row[0], row[1], row[2]
    mtype = _mime_to_type(mime) or classify(original)
    if not mtype or original in seen:
        return None
    seen.add(original)
    size = 0
    if len(row) > 3:
        try:
            size = int(row[3])
        except (TypeError, ValueError):
            size = 0
    mod = "im_" if mtype == "image" else "id_"   # id_ = untouched original bytes
    return {
        "url": f"https://web.archive.org/web/{ts}{mod}/{original}",
        "type": mtype,
        "source_tag": mime or "cdx",
        "size": size,
    }


def cdx_fetch_pages(url: str, total_limit: int = 100000, page_size: int = 10000,
                    scope: str = "domain", ts_from: str | None = None,
                    ts_to: str | None = None):
    """Yield batches of CDX rows, paging with resumeKey up to total_limit.

    scope="domain": everything on the host. scope="path": only URLs under the
    given URL's folder — for sites living inside a big hosting domain.
    """
    domain = _cdx_target(url, scope)
    resume, fetched = None, 0
    while fetched < total_limit:
        params = {
            "url": domain + "/*",
            "output": "json",
            "fl": "timestamp,original,mimetype,length",
            "filter": ["statuscode:200",
                       "mimetype:(image|video|audio)/.*|application/x-shockwave-flash"],
            "collapse": "urlkey",
            "limit": str(min(page_size, total_limit - fetched)),
            "showResumeKey": "true",
        }
        if ts_from:
            params["from"] = ts_from
        if ts_to:
            params["to"] = ts_to
        if resume:
            params["resumeKey"] = resume
        r, last_err = None, None
        for attempt in range(5):
            try:
                r = requests.get("https://web.archive.org/cdx/search/cdx", params=params,
                                 headers=HEADERS, timeout=120)
                if r.status_code == 429:
                    wait = min(int(r.headers.get("Retry-After") or 0) or 15 * (attempt + 1), 90)
                    last_err = "HTTP 429"
                    r = None
                    yield ("status", f"Archive is rate-limiting — waiting {wait}s "
                                     f"(attempt {attempt + 1}/5)")
                    time.sleep(wait)
                    continue
                if r.status_code in (502, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                    r = None
                    # huge domains: server-side dedup is what times out — do it locally instead
                    if "collapse" in params:
                        params.pop("collapse")
                    yield ("status", f"Archive index strained ({last_err}) — retrying")
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            except requests.RequestException as e:
                last_err = str(e)
                r = None
                time.sleep(4)
        if r is None:
            if "429" in (last_err or ""):
                raise requests.RequestException(
                    "The archive is rate-limiting this connection (HTTP 429). It clears on its "
                    "own — wait a couple of minutes before scraping again. Repeated large scans "
                    "trip it fastest; prefer Site folder scope or a narrow time range.")
            raise requests.RequestException(
                f"The archive's index timed out for this domain ({last_err}). "
                "Domains this large may not be fully scannable — try Site folder scope "
                "or narrow the Time to a custom range.")
        rows = r.json() if r.text.strip() else []
        if rows and rows[0] and rows[0][0] == "timestamp":
            rows = rows[1:]
        # tail format with showResumeKey: ..., [], ["<key>"]
        resume = None
        while rows and rows[-1] == []:
            rows.pop()
        if rows and len(rows[-1]) == 1:
            resume = rows[-1][0]
            rows = rows[:-1]
            while rows and rows[-1] == []:
                rows.pop()
        if not rows:
            return
        fetched += len(rows)
        yield ("rows", rows)
        if not resume:
            return


def cdx_html_pages(url: str, scope: str = "domain", ts_from: str | None = None,
                   ts_to: str | None = None, limit: int = 500) -> list[str]:
    """Playback URLs for the archived HTML pages of a site/folder (one capture per
    page URL). Used by deep site scans to catch media the CDX media sweep misses —
    files hosted on other domains, or referenced from CSS/markup only."""
    domain = _cdx_target(url, scope)
    params = {
        "url": domain + "/*",
        "output": "json",
        "fl": "timestamp,original",
        "filter": ["statuscode:200", "mimetype:text/html"],
        "collapse": "urlkey",
        "limit": str(limit),
    }
    if ts_from:
        params["from"] = ts_from
    if ts_to:
        params["to"] = ts_to
    r = requests.get("https://web.archive.org/cdx/search/cdx", params=params,
                     headers=HEADERS, timeout=120)
    r.raise_for_status()
    rows = r.json() if r.text.strip() else []
    out, seen = [], set()
    for row in rows[1:]:                     # first row is the header
        if len(row) < 2 or row[1] in seen:
            continue
        seen.add(row[1])
        out.append(f"https://web.archive.org/web/{row[0]}/{row[1]}")
    return out


def cdx_site_media(url: str, limit: int = 20000) -> list[dict]:
    """Every media URL ever archived for a whole site, via the Wayback CDX API."""
    rows = cdx_fetch_rows(url, limit)
    out, seen = [], set()
    for row in rows[1:]:  # first row is the header
        item = parse_cdx_row(row, seen)
        if item:
            out.append(item)
    return out


def _srcset_urls(srcset: str):
    for part in srcset.split(","):
        candidate = part.strip().split(" ")[0].strip()
        if candidate:
            yield candidate


def extract_media(html: str, base_url: str) -> list[dict]:
    """Return a list of {url, type, source_tag} dicts, deduplicated, in page order."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}

    def add(raw: str, mtype: str | None, tag: str):
        if not raw or raw.startswith(("data:", "javascript:", "blob:")):
            return
        url = urljoin(base_url, raw.strip())
        if not url.startswith(("http://", "https://")):
            return
        if is_wayback_noise(url):
            return
        mtype = mtype or classify(url)
        if not mtype:
            return
        if url not in found:
            found[url] = {"url": url, "type": mtype, "source_tag": tag}

    for img in soup.find_all("img"):
        add(img.get("src") or img.get("data-src") or "", "image", "img")
        if img.get("srcset"):
            for u in _srcset_urls(img["srcset"]):
                add(u, "image", "img[srcset]")

    for tag_name, mtype in (("video", "video"), ("audio", "audio")):
        for el in soup.find_all(tag_name):
            add(el.get("src") or "", mtype, tag_name)
            for source in el.find_all("source"):
                add(source.get("src") or "", mtype, f"{tag_name}>source")

    for source in soup.find_all("source"):
        if source.get("srcset"):
            for u in _srcset_urls(source["srcset"]):
                add(u, None, "picture>source")

    # Links pointing directly at media files
    for a in soup.find_all("a", href=True):
        add(a["href"], None, "a[href]")

    # Open Graph / Twitter card media
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in ("og:image", "og:video", "og:audio", "twitter:image", "twitter:player:stream"):
            mtype = "image" if "image" in prop else ("audio" if "audio" in prop else "video")
            add(meta.get("content") or "", mtype, f"meta[{prop}]")

    # CSS background images in inline styles
    for el in soup.find_all(style=True):
        style = el["style"]
        idx = 0
        while (pos := style.find("url(", idx)) != -1:
            end = style.find(")", pos)
            if end == -1:
                break
            raw = style[pos + 4:end].strip("'\" ")
            add(raw, "image", "style")
            idx = end + 1

    return list(found.values())


def fetch_page(url: str) -> tuple[str, str]:
    """Fetch a page; returns (html, final_url after redirects)."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text, resp.url


def _gif_scan(buf: bytes, whole: bool) -> bool | None:
    """Walk a GIF's block structure counting image descriptors (frames).

    True = 2+ frames (animated), False = structure finished with <2 (static),
    None = need more data / not parseable. Counting real image descriptors —
    not raw 0x2C bytes, which also occur inside color tables and pixel data —
    is what makes the answer reliable."""
    if len(buf) < 13 or buf[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    packed = buf[10]
    pos = 13
    if packed & 0x80:                        # global color table
        pos += 3 * (2 << (packed & 0x07))
    frames = 0

    def skip_subblocks(p: int) -> int:       # -1 = truncated mid-chain
        while True:
            if p >= len(buf):
                return -1
            size = buf[p]
            p += 1
            if size == 0:
                return p
            p += size

    while pos < len(buf):
        marker = buf[pos]
        pos += 1
        if marker == 0x3B:                   # trailer — file complete
            return frames >= 2
        if marker == 0x00:                   # zero padding between blocks
            continue
        if marker == 0x21:                   # extension: label byte + sub-blocks
            pos = skip_subblocks(pos + 1)
        elif marker == 0x2C:                 # image descriptor — one frame
            frames += 1
            if frames >= 2:
                return True
            if pos + 9 > len(buf):
                return None
            ipacked = buf[pos + 8]
            pos += 9
            if ipacked & 0x80:               # local color table
                pos += 3 * (2 << (ipacked & 0x07))
            pos = skip_subblocks(pos + 1)    # LZW min-code byte, then data
        else:                                # corrupt / unknown block
            return None
        if pos == -1:
            return None
    # buffer exhausted without a trailer: clean end of a whole file -> static
    return False if whole else None


def is_animated_gif(url: str) -> bool | None:
    """True/False if `url` is an animated GIF; None if undetermined. Streams the
    file and parses its block structure, stopping at the second frame."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, stream=True)
        r.raise_for_status()
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) >= 6 and buf[:6] not in (b"GIF87a", b"GIF89a"):
                return None                  # not GIF bytes at all
            res = _gif_scan(buf, whole=False)
            if res is not None:
                return res
            if len(buf) > 8_000_000:
                return None                  # giving up mid-file -> undetermined
        return _gif_scan(buf, whole=True)
    except requests.RequestException:
        return None
