"""Local UI: search YouTube, pick videos by hand, export their comments.

Run:  YOUTUBE_API_KEY=... .venv/bin/python app.py   ->  http://127.0.0.1:8765
Binds to localhost only. The API key stays in this process; the browser never sees it.

Quota: search.list has its own 100 calls/day bucket. Searches are cached in data/search/
(shared with discover.py), so repeating a query costs nothing. Fresh searches are counted
per Pacific-time day in data/quota.json (only searches made by this app or discover.py
after this file existed; the console's Quotas page is the source of truth).
"""
import csv
import datetime as dt
import json
import re
import threading
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from googleapiclient.errors import HttpError

from discover import search as cached_search
from fetch_comments import FIELDS, client, error_reason, execute, fetch_video_comments, write_csv

ROOT = Path(__file__).parent
COMMENTS = ROOT / "data/comments"
EXPORTS = ROOT / "exports"
QUOTA = ROOT / "data/quota.json"
SEARCH_DAILY_CAP = 100

app = Flask(__name__)
yt = client()
jobs, jobs_lock = {}, threading.Lock()


def pacific_day():
    return dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def searches_today():
    try:
        q = json.loads(QUOTA.read_text())
    except (FileNotFoundError, ValueError):
        return 0
    return q.get("searches", 0) if q.get("day") == pacific_day() else 0


def add_searches(n):
    if n:
        QUOTA.write_text(json.dumps({"day": pacific_day(), "searches": searches_today() + n}))


def video_details(ids):
    out = {}
    for i in range(0, len(ids), 50):
        resp = execute(yt.videos().list(part="snippet,statistics,contentDetails",
                                        id=",".join(ids[i:i + 50])))
        for v in resp.get("items", []):
            s, sn = v.get("statistics", {}), v["snippet"]
            thumbs = sn.get("thumbnails", {})
            out[v["id"]] = {
                "video_id": v["id"],
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "title": sn["title"],
                "channel": sn["channelTitle"],
                "channel_id": sn["channelId"],
                "published_at": sn["publishedAt"][:10],
                "duration": v.get("contentDetails", {}).get("duration", ""),
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                # absent when comments are disabled
                "comments": int(s["commentCount"]) if "commentCount" in s else None,
                "thumb": (thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
            }
    return out


@app.get("/")
def index():
    return send_from_directory(ROOT, "ui.html")


@app.get("/api/quota")
def quota():
    return jsonify(searches_today=searches_today(), cap=SEARCH_DAILY_CAP)


@app.post("/api/search")
def api_search():
    body = request.get_json(force=True)
    queries = [q.strip() for q in body.get("queries", []) if q.strip()][:20]
    if not queries:
        return jsonify(error="Enter at least one search term."), 400
    months = int(body.get("months", 24))
    order = body.get("order", "relevance")
    if order not in ("relevance", "viewCount", "date", "rating"):
        return jsonify(error=f"Unknown order: {order}"), 400
    per_query = max(1, min(int(body.get("per_query", 25)), 50))
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30 * months)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")

    found, spent, errors = {}, 0, []
    budget = SEARCH_DAILY_CAP - searches_today()
    for q in queries:
        try:
            items, used = cached_search(yt, q, after, per_query, budget - spent, order, months)
        except HttpError as e:
            errors.append(f"'{q}': {error_reason(e) or e.resp.status}")
            if error_reason(e) in ("quotaExceeded", "rateLimitExceeded"):
                break
            continue
        spent += used
        if items is None:
            errors.append(f"'{q}': daily search budget used up (resets midnight Pacific)")
            continue
        for it in items:
            found.setdefault(it["video_id"], []).append(q)
    add_searches(spent)

    details = video_details(list(found)) if found else {}
    videos = []
    for vid, qs in found.items():
        d = details.get(vid)
        if d:
            d["queries"] = qs
            d["exported"] = (COMMENTS / f"{vid}.csv").exists()
            videos.append(d)
    return jsonify(videos=videos, searches_spent=spent,
                   searches_today=searches_today(), cap=SEARCH_DAILY_CAP, errors=errors)


def run_export(job_id, videos, max_threads, label):
    job = jobs[job_id]
    COMMENTS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(exist_ok=True)
    merged = []
    for v in videos:
        with jobs_lock:
            job["current"] = v["title"]
        path = COMMENTS / f"{v['video_id']}.csv"
        try:
            if path.exists() and path.stat().st_size:
                rows = list(csv.DictReader(open(path, encoding="utf-8")))
                source = "cache"
            else:
                rows = fetch_video_comments(yt, v["video_id"], max_threads, log=False)
                write_csv(path, rows)
                source = "api"
        except HttpError as e:
            reason = error_reason(e) or str(e.resp.status)
            with jobs_lock:
                job["errors"].append(f"{v['title'][:60]}: {reason}")
                job["done"] += 1
            if reason == "quotaExceeded":
                with jobs_lock:
                    job["errors"].append("Daily quota exhausted; re-run after midnight Pacific "
                                         "(already-fetched videos are cached).")
                break
            continue
        for r in rows:
            merged.append({"video_id": v["video_id"], "video_title": v["title"],
                           "video_url": f"https://www.youtube.com/watch?v={v['video_id']}", **r})
        with jobs_lock:
            job["done"] += 1
            job["rows"] = len(merged)
            job["log"].append(f"{v['title'][:60]} — {len(rows)} comments ({source})")

    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or "export"
    name = f"{safe}_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    write_csv(EXPORTS / name, merged, ["video_id", "video_title", "video_url"] + FIELDS)
    with jobs_lock:
        job.update(status="finished", file=name, current="")


@app.post("/api/export")
def api_export():
    body = request.get_json(force=True)
    videos = body.get("videos", [])
    if not videos:
        return jsonify(error="No videos selected."), 400
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"status": "running", "total": len(videos), "done": 0, "rows": 0,
                    "current": "", "log": [], "errors": [], "file": None}
    threading.Thread(target=run_export, daemon=True,
                     args=(job_id, videos, int(body.get("max_threads", 2000)),
                           body.get("label", "export"))).start()
    return jsonify(job_id=job_id)


@app.get("/api/job/<job_id>")
def api_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        return jsonify(job) if job else (jsonify(error="unknown job"), 404)


@app.get("/api/download/<name>")
def download(name):
    path = (EXPORTS / name).resolve()
    if path.parent != EXPORTS.resolve() or not path.exists():
        abort(404)
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, debug=False)
