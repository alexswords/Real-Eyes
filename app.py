"""Real Eyes — paste a URL, get every image/video/audio file on the page."""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import subprocess
import threading
import time
import uuid
import tempfile
import zipfile
from urllib.parse import urlparse

import requests
from flask import (Flask, Response, jsonify, render_template, request,
                   send_file, send_from_directory)

from scraper import (HEADERS, cdx_fetch_pages, crawl_pages, extract_media,
                     fetch_page, is_animated_gif, norm_url, page_snapshots,
                     parse_cdx_row, resolve_wayback, wayback_median_snapshot)

app = Flask(__name__)

VERSION = "dev"
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")) as _vf:
        VERSION = _vf.read().strip()
except OSError:
    pass


@app.get("/api/version")
def version():
    return VERSION


@app.post("/api/shutdown")
def shutdown():
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/scrape")
def scrape():
    """Streams NDJSON progress lines, ending with a {"done": ...} payload."""
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    source = data.get("source", "live")          # live | wayback | both
    scope = data.get("scope", "page")            # page | local | site
    tkind = data.get("time", "now")              # now | range | all
    tfrom = re.sub(r"\D", "", str(data.get("tfrom") or ""))[:8] or None
    tto = re.sub(r"\D", "", str(data.get("tto") or ""))[:8] or None
    deep = bool(data.get("deep"))
    if tkind == "now":
        # archive side of "now" = roughly the last year of captures
        cdx_from, cdx_to = time.strftime("%Y%m%d", time.localtime(time.time() - 365 * 86400)), None
    elif tkind == "range":
        cdx_from, cdx_to = tfrom, tto
    else:
        cdx_from = cdx_to = None

    def line(obj):
        return json.dumps(obj) + "\n"

    def resolve_snapshot(target):
        if tkind == "now":
            return resolve_wayback(target)               # newest capture
        if tkind == "range" and (tfrom or tto):
            try:
                snap = wayback_median_snapshot(target, tfrom, tto)
            except requests.RequestException:
                snap = None
            return snap or resolve_wayback(target, tto or tfrom)
        try:
            snap = wayback_median_snapshot(target)        # all time -> median
        except requests.RequestException:
            snap = None
        return snap or resolve_wayback(target)

    def generate():
        live_norms = set()
        try:
            if scope in ("site", "local"):
                cdx_scope = "path" if scope == "local" else "domain"
                label = "site folder" if scope == "local" else "entire site"
                count = 0
                if source in ("live", "both"):
                    yield line({"status": "Crawling the live site"})
                    pages, seen = 0, set()
                    for page_url, html in crawl_pages(url, scope=cdx_scope):
                        pages += 1
                        fresh = [it for it in extract_media(html, page_url)
                                 if it["url"] not in seen]
                        for it in fresh:
                            seen.add(it["url"])
                            live_norms.add(norm_url(it["url"]))
                            if source == "both":
                                it["origin"] = "live"
                        count += len(fresh)
                        if fresh:
                            yield line({"items": fresh})
                        yield line({"status": f"Crawling — {pages} pages, {count} files found"})
                    if source == "live":
                        yield line({"done": {"url": f"{url} ({label}, live crawl, {pages} pages)",
                                             "count": count}})
                        return
                yield line({"status": "Querying the Wayback Machine index"})
                batch, seen_cdx = [], set()
                for kind, payload in cdx_fetch_pages(url, scope=cdx_scope,
                                                     ts_from=cdx_from, ts_to=cdx_to):
                    if kind == "status":
                        yield line({"status": payload})
                        continue
                    for row in payload:
                        item = parse_cdx_row(row, seen_cdx)
                        if not item:
                            continue
                        if source == "both":
                            if norm_url(item["url"]) in live_norms:
                                continue          # still present on the live site
                            item["origin"] = "archive"
                        batch.append(item)
                        if len(batch) >= 250:
                            count += len(batch)
                            yield line({"items": batch, "progress": count})
                            batch = []
                if batch:
                    count += len(batch)
                    yield line({"items": batch, "progress": count})
                hist = ("archive " + (tfrom or "…") + "–" + (tto or "…") if tkind == "range"
                        else "recent archive captures" if tkind == "now"
                        else "all archived history")
                suffix = ("live + " + hist) if source == "both" else hist
                yield line({"done": {"url": f"{url} ({label}, {suffix})", "count": count}})
                return

            # ---- single page ----
            count = 0
            seen_norm = {}          # norm -> playback url first emitted
            appears = {}            # norm -> number of snapshots containing it
            if source in ("live", "both"):
                yield line({"status": "Fetching page"})
                html, final_url = fetch_page(url)
                fresh = []
                for it in extract_media(html, final_url):
                    n = norm_url(it["url"])
                    appears[n] = appears.get(n, 0) + 1
                    if n in seen_norm:
                        continue
                    seen_norm[n] = it["url"]
                    if source == "both":
                        it["origin"] = "live"
                    fresh.append(it)
                count += len(fresh)
                yield line({"items": fresh, "progress": count})
                if source == "live":
                    yield line({"done": {"url": final_url, "count": count}})
                    return

            # archive side: one snapshot for "now", a sampled walk otherwise
            if "web.archive.org" in url:
                targets = [url]
            elif tkind == "now":
                yield line({"status": "Resolving Wayback snapshot"})
                snap = resolve_wayback(url)
                targets = [snap] if snap else []
            else:
                yield line({"status": "Listing the page's snapshots"})
                try:
                    targets = page_snapshots(url, cdx_from, cdx_to, deep=deep)
                except requests.RequestException:
                    targets = []
                if not targets:
                    snap = resolve_snapshot(url)
                    targets = [snap] if snap else []
            if not targets:
                if source == "both":
                    yield line({"done": {"url": f"{url} (live; no archive snapshots found)",
                                         "count": count}})
                else:
                    yield line({"error": "No Wayback snapshot found for that URL"})
                return
            for i, snap in enumerate(targets, 1):
                ts = snap.split("/web/")[1][:8] if "/web/" in snap else ""
                nice = f" ({ts[:4]}-{ts[4:6]}-{ts[6:8]})" if len(ts) == 8 and ts.isdigit() else ""
                yield line({"status": f"Reading snapshot {i}/{len(targets)}{nice}"})
                try:
                    html, final_url = fetch_page(snap)
                except requests.RequestException:
                    continue
                fresh = []
                for it in extract_media(html, final_url):
                    n = norm_url(it["url"])
                    appears[n] = appears.get(n, 0) + 1
                    if n in seen_norm:
                        continue
                    seen_norm[n] = it["url"]
                    if source == "both":
                        it["origin"] = "archive"
                    fresh.append(it)
                if fresh:
                    count += len(fresh)
                    yield line({"items": fresh, "progress": count})
            swept = False
            if tkind != "now":
                # complementary sweep: EVERY file the archive ever stored in this
                # page's folder — closes the gaps between sampled snapshots
                yield line({"status": "Sweeping the page's folder in the archive index"})
                seen_cdx = set()
                try:
                    for kind, payload in cdx_fetch_pages(url, scope="path",
                                                         total_limit=100000,
                                                         ts_from=cdx_from, ts_to=cdx_to):
                        if kind == "status":
                            yield line({"status": payload})
                            continue
                        fresh = []
                        for row in payload:
                            item = parse_cdx_row(row, seen_cdx)
                            if not item:
                                continue
                            n = norm_url(item["url"])
                            if n in seen_norm:
                                continue
                            seen_norm[n] = item["url"]
                            if source == "both":
                                item["origin"] = "archive"
                            fresh.append(item)
                        if fresh:
                            count += len(fresh)
                            yield line({"items": fresh, "progress": count})
                    swept = True
                except requests.RequestException as e:
                    yield line({"status": f"Folder index sweep skipped ({e})"})
            if len(targets) > 1:
                snaps = {seen_norm[n]: c for n, c in appears.items() if n in seen_norm}
                yield line({"meta": {"snaps": snaps, "total": len(targets)}})
            extra = " + folder index" if swept else ""
            if source == "both":
                done_url = f"{url} (live + {len(targets)} archived snapshots{extra})"
            elif len(targets) > 1 or swept:
                done_url = f"{url} ({len(targets)} snapshots{extra} across time)"
            else:
                done_url = targets[0]
            yield line({"done": {"url": done_url, "count": count}})
        except requests.RequestException as e:
            yield line({"error": f"Request failed: {e}"})
        except Exception as e:  # keep the stream well-formed on any failure
            yield line({"error": str(e)})

    return Response(generate(), mimetype="application/x-ndjson")


WB_RE = re.compile(r"(https?://web\.archive\.org/web/)(\d{4,14})([a-z]{2}_)?/(.*)", re.S)


def _wayback_variants(url):
    """The same capture under different playback modifiers, most-likely first."""
    m = WB_RE.match(url)
    if not m:
        return [url]
    base, ts, mod, rest = m.groups()
    out, seen = [], set()
    for mm in [mod or "", "id_", "im_", "oe_", ""]:
        u = f"{base}{ts}{mm}/{rest}"
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _fetch_upstream(url, timeout=30):
    """GET a file, retrying Wayback modifier variants until one returns 200."""
    last = None
    for u in _wayback_variants(url):
        try:
            r = requests.get(u, headers=HEADERS, timeout=timeout, stream=True)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            r.close()
        except requests.RequestException as e:
            last = str(e)
    raise requests.RequestException(last or "fetch failed")


@app.get("/api/download")
def download_one():
    """Stream one remote file to the browser as an attachment."""
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Bad URL"}), 400
    try:
        r = _fetch_upstream(url)
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502
    name = (urlparse(url).path.rsplit("/", 1)[-1] or "file").replace('"', "")
    return Response(
        r.iter_content(65536),
        mimetype=r.headers.get("Content-Type", "application/octet-stream"),
        headers={"Content-Disposition": 'attachment; filename="%s"' % name})


@app.get("/api/animated")
def animated():
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Bad URL"}), 400
    res = is_animated_gif(url)
    return jsonify({"animated": res})


@app.get("/api/raw")
def raw_file():
    """Same-origin passthrough (no attachment header) — used by the Flash player."""
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Bad URL"}), 400
    try:
        r = _fetch_upstream(url)
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502
    return Response(r.iter_content(65536),
                    mimetype=r.headers.get("Content-Type", "application/octet-stream"))


# ---- ffmpeg (legacy-format converter) — downloaded & cached on first use ----
APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library",
                           "Application Support", "Real-Eyes")
FFMPEG_BIN = os.path.join(APP_SUPPORT, "bin", "ffmpeg")
TRANSCODE_DIR = os.path.join(APP_SUPPORT, "transcode")
_ffmpeg_lock = threading.Lock()


def _ensure_ffmpeg():
    if os.path.exists(FFMPEG_BIN):
        return True
    with _ffmpeg_lock:
        if os.path.exists(FFMPEG_BIN):
            return True
        arch = "arm64" if platform.machine() == "arm64" else "x64"
        url = ("https://github.com/eugeneware/ffmpeg-static/releases/latest/"
               f"download/ffmpeg-darwin-{arch}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=600)
            r.raise_for_status()
            os.makedirs(os.path.dirname(FFMPEG_BIN), exist_ok=True)
            tmp = FFMPEG_BIN + ".part"
            with open(tmp, "wb") as f:
                f.write(r.content)
            os.chmod(tmp, 0o755)
            os.replace(tmp, FFMPEG_BIN)
            return True
        except (requests.RequestException, OSError):
            return False


@app.get("/api/log")
def tail_log():
    path = os.path.join(APP_SUPPORT, "server.log")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            return Response(f.read().decode(errors="replace"), mimetype="text/plain")
    except OSError as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/transcode")
def transcode():
    """Convert a legacy media file to MP4 (cached), then stream it like any video."""
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Bad URL"}), 400
    os.makedirs(TRANSCODE_DIR, exist_ok=True)
    out = os.path.join(TRANSCODE_DIR, hashlib.sha1(url.encode()).hexdigest() + "-v2.mp4")
    if not os.path.exists(out):
        if not _ensure_ffmpeg():
            return jsonify({"error": "Could not download the media converter"}), 502
        try:
            src_r = _fetch_upstream(url, timeout=60)
        except requests.RequestException as e:
            return jsonify({"error": str(e)}), 502
        src_path = out + "." + uuid.uuid4().hex + ".src"
        part = out + "." + uuid.uuid4().hex + ".part.mp4"
        try:
            with open(src_path, "wb") as f:
                for chunk in src_r.iter_content(65536):
                    f.write(chunk)
            proc = subprocess.run(
                [FFMPEG_BIN, "-y", "-i", src_path,
                 "-movflags", "+faststart",
                 "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                 "-pix_fmt", "yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "160k", part],
                capture_output=True, timeout=900)
            if proc.returncode != 0 or not os.path.exists(part):
                print("transcode video pass failed:",
                      proc.stderr.decode(errors="replace")[-400:], flush=True)
                proc = subprocess.run(   # tolerant retry for damaged files
                    [FFMPEG_BIN, "-y", "-err_detect", "ignore_err",
                     "-fflags", "+genpts", "-i", src_path,
                     "-movflags", "+faststart",
                     "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                     "-pix_fmt", "yuv420p",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                     "-c:a", "aac", "-b:a", "160k", part],
                    capture_output=True, timeout=900)
            if proc.returncode != 0 or not os.path.exists(part):
                print("transcode tolerant pass failed:",
                      proc.stderr.decode(errors="replace")[-400:], flush=True)
                proc = subprocess.run(   # audio-only fallback
                    [FFMPEG_BIN, "-y", "-i", src_path, "-vn",
                     "-c:a", "aac", "-b:a", "160k", part],
                    capture_output=True, timeout=900)
                if proc.returncode != 0 or not os.path.exists(part):
                    print("transcode audio pass failed:",
                          proc.stderr.decode(errors="replace")[-400:], flush=True)
                    return jsonify({"error": "Conversion failed"}), 415
            os.replace(part, out)
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Conversion timed out"}), 504
        finally:
            for pth in (src_path, part):
                try:
                    os.remove(pth)
                except OSError:
                    pass
    resp = send_file(out, mimetype="video/mp4", conditional=True)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- Ruffle (Flash emulator) — downloaded & cached on first use ----
RUFFLE_DIR = os.path.join(os.path.expanduser("~"), "Library",
                          "Application Support", "Real-Eyes", "ruffle")
_ruffle_lock = threading.Lock()


def _ensure_ruffle():
    if os.path.exists(os.path.join(RUFFLE_DIR, "ruffle.js")):
        return True
    with _ruffle_lock:
        if os.path.exists(os.path.join(RUFFLE_DIR, "ruffle.js")):
            return True
        try:
            rel = requests.get(
                "https://api.github.com/repos/ruffle-rs/ruffle/releases?per_page=5",
                headers=HEADERS, timeout=30)
            rel.raise_for_status()
            asset_url = None
            for release in rel.json():
                for asset in release.get("assets", []):
                    if asset.get("name", "").endswith("web-selfhosted.zip"):
                        asset_url = asset["browser_download_url"]
                        break
                if asset_url:
                    break
            if not asset_url:
                return False
            data = requests.get(asset_url, headers=HEADERS, timeout=180)
            data.raise_for_status()
            os.makedirs(RUFFLE_DIR, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(data.content)) as zf:
                zf.extractall(RUFFLE_DIR)
            return os.path.exists(os.path.join(RUFFLE_DIR, "ruffle.js"))
        except (requests.RequestException, zipfile.BadZipFile, OSError, ValueError):
            return False


@app.get("/ruffle/<path:fname>")
def ruffle_asset(fname):
    if not _ensure_ruffle():
        return jsonify({"error": "Could not download the Ruffle Flash player"}), 502
    return send_from_directory(RUFFLE_DIR, fname)


LAST_SEEN = {"t": time.time()}


@app.post("/api/ping")
def ping():
    LAST_SEEN["t"] = time.time()
    return jsonify({"ok": True})


def _watchdog():
    """Exit when no browser tab has pinged for 3 minutes (unless a zip is running)."""
    while True:
        time.sleep(15)
        if any(j.get("status") == "running" for j in ZIP_JOBS.values()):
            continue
        if time.time() - LAST_SEEN["t"] > 180:
            os._exit(0)


ZIP_JOBS = {}


def _zip_worker(job_id, urls):
    job = ZIP_JOBS[job_id]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    path = tmp.name
    tmp.close()
    used = set()
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for u in urls:
                if job["cancel"]:
                    break
                content = None
                for attempt in range(3):
                    try:
                        r = requests.get(u, headers=HEADERS, timeout=20)
                        if r.status_code in (429, 503):   # throttled — back off, retry
                            time.sleep(2 * (attempt + 1))
                            continue
                        r.raise_for_status()
                        content = r.content
                        break
                    except requests.RequestException:
                        break
                if content is None:
                    try:
                        rr = _fetch_upstream(u, timeout=20)
                        content = rr.content
                    except requests.RequestException:
                        content = None
                job["done"] += 1
                if content is None:
                    job["skipped"] += 1
                    continue
                name = urlparse(u).path.rsplit("/", 1)[-1] or "file"
                base, dot, ext = name.rpartition(".")
                n, cand = 1, name
                while cand in used:
                    cand = f"{base or name}_{n}{dot}{ext}" if dot else f"{name}_{n}"
                    n += 1
                used.add(cand)
                zf.writestr(cand, content)
        job["path"] = path
        job["status"] = "cancelled" if job["cancel"] else "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.post("/api/zip_start")
def zip_start():
    urls = (request.json or {}).get("urls", [])[:100000]
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400
    job_id = uuid.uuid4().hex
    ZIP_JOBS[job_id] = {"total": len(urls), "done": 0, "skipped": 0,
                        "status": "running", "cancel": False, "path": None}
    threading.Thread(target=_zip_worker, args=(job_id, urls), daemon=True).start()
    return jsonify({"id": job_id, "total": len(urls)})


@app.get("/api/zip_status")
def zip_status():
    job = ZIP_JOBS.get(request.args.get("id", ""))
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    return jsonify({k: job[k] for k in ("total", "done", "skipped", "status")} |
                   ({"error": job.get("error")} if job.get("error") else {}))


@app.post("/api/zip_cancel")
def zip_cancel():
    job = ZIP_JOBS.get((request.json or {}).get("id", ""))
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    job["cancel"] = True
    return jsonify({"ok": True})


@app.get("/api/zip_file")
def zip_file():
    job = ZIP_JOBS.get(request.args.get("id", ""))
    if not job or not job.get("path"):
        return jsonify({"error": "Not ready"}), 404
    return send_file(job["path"], mimetype="application/zip",
                     as_attachment=True, download_name="media.zip")


if __name__ == "__main__":
    threading.Thread(target=_watchdog, daemon=True).start()
    print("Real Eyes running → http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
