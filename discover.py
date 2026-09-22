"""Find candidate videos per niche via search.list, then rank them by comment volume.

Usage: python discover.py [--niches niches.json] [--months 24] [--per-query 25]
                          [--top 10] [--max-searches 95]

search.list has its own quota: 100 calls/day by default. Each query result is cached in
data/search/, so re-runs (e.g. the next day, after hitting the cap) only spend quota on
queries not yet fetched. videos.list (stats) costs 1 unit per 50 videos from the main quota.
Relevance guards (search sorted by views surfaces off-topic viral videos):
  - the title must contain one of the niche's must_match keywords (niches.json), matched
    at a word start, and none of its exclude phrases
  - --orders viewCount,relevance merges both search passes (each cached separately)
  - titles in non-Latin scripts are dropped (relevanceLanguage is only a hint)
  - a video matching several niches is kept only in the one whose queries found it most
Writes videos.csv (top N per niche by comment count) and rejected.csv (what was filtered
and why), so the filters can be audited.
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

from googleapiclient.errors import HttpError

from fetch_comments import client, error_reason, execute

CACHE = Path("data/search")
NON_LATIN = re.compile(r"[\u0400-\u04FF\u0590-\u06FF\u0900-\u0DFF\u0E00-\u0E7F"
                       r"\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")
FIELDS = ["niche", "video_id", "title", "channel_id", "channel_title", "published_at",
          "views", "likes", "comments", "comments_per_1k_views", "views_per_day", "queries"]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def search(yt, query, published_after, per_query, budget, order="viewCount", months=24):
    """Cached search. Returns (items, spent_quota).

    Cache key = query + order + months + per_query; defaults keep the original file names.
    """
    suffix = "" if order == "viewCount" else f"--{order}"
    if months != 24:
        suffix += f"--{months}m"
    if per_query != 25:
        suffix += f"--{per_query}n"
    path = CACHE / f"{slug(query)}{suffix}.json"
    if path.exists():
        return json.loads(path.read_text()), False
    if budget <= 0:
        return None, False
    resp = execute(yt.search().list(
        part="snippet", q=query, type="video", order=order,
        publishedAfter=published_after, relevanceLanguage="en",
        maxResults=per_query,
    ))
    items = [{"video_id": i["id"]["videoId"], "query": query} for i in resp.get("items", [])]
    path.write_text(json.dumps(items))
    return items, True


def video_stats(yt, ids):
    stats = {}
    for i in range(0, len(ids), 50):
        resp = execute(yt.videos().list(part="snippet,statistics", id=",".join(ids[i:i + 50])))
        for v in resp.get("items", []):
            s, sn = v.get("statistics", {}), v["snippet"]
            stats[v["id"]] = {
                "title": sn["title"], "channel_id": sn["channelId"],
                "channel_title": sn["channelTitle"], "published_at": sn["publishedAt"],
                "views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                # commentCount is absent when comments are disabled
                "comments": int(s.get("commentCount", 0)),
            }
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--niches", default="niches.json")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--per-query", type=int, default=25)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-comments", type=int, default=50)
    ap.add_argument("--max-searches", type=int, default=95)
    ap.add_argument("--orders", default="viewCount",
                    help="comma-separated search orders to merge, e.g. viewCount,relevance")
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    niches = json.loads(Path(args.niches).read_text())
    niches = {n: cfg if isinstance(cfg, dict) else {"queries": cfg}
              for n, cfg in niches.items()}
    orders = [o.strip() for o in args.orders.split(",") if o.strip()]
    now = dt.datetime.now(dt.timezone.utc)
    after = (now - dt.timedelta(days=30 * args.months)).strftime("%Y-%m-%dT%H:%M:%SZ")
    yt = client()

    budget, spent, missing = args.max_searches, 0, []
    found = {}  # niche -> {video_id: set(queries)}
    for niche, cfg in niches.items():
        found[niche] = {}
        for q, order in ((q, o) for o in orders for q in cfg["queries"]):
            try:
                items, used = search(yt, q, after, args.per_query, budget - spent, order, args.months)
            except HttpError as e:
                if error_reason(e) in ("quotaExceeded", "rateLimitExceeded"):
                    print(f"search quota exhausted at '{q}'; re-run tomorrow to resume",
                          file=sys.stderr)
                    budget = spent  # stop spending, keep using cache
                    items, used = None, False
                else:
                    raise
            spent += used
            if items is None:
                missing.append(f"{q} [{order}]")
                continue
            for it in items:
                found[niche].setdefault(it["video_id"], set()).add(q)

    all_ids = sorted({v for vids in found.values() for v in vids})
    stats = video_stats(yt, all_ids)

    # one niche per video: the niche whose queries returned it most often
    best = {}
    for niche, vids in found.items():
        for vid, qs in vids.items():
            if vid not in best or len(qs) > len(found[best[vid]][vid]):
                best[vid] = niche

    rows, rejected = [], []
    for niche, vids in found.items():
        keys = [re.compile(r"\b" + re.escape(k), re.I) for k in niches[niche].get("must_match", [])]
        excl = [x.lower() for x in niches[niche].get("exclude", [])]
        ranked = []
        for vid, qs in vids.items():
            s = stats.get(vid)
            reason = None
            if not s:
                reason = "no stats"
            elif keys and not any(k.search(s["title"]) for k in keys):
                reason = "title off-topic"
            elif any(x in s["title"].lower() for x in excl):
                reason = "title excluded"
            elif NON_LATIN.search(s["title"]):
                reason = "non-Latin title"
            elif best[vid] != niche:
                reason = f"assigned to {best[vid]}"
            elif s["comments"] < args.min_comments:
                reason = "too few comments"
            if reason:
                rejected.append({"niche": niche, "video_id": vid, "reason": reason,
                                 "title": s["title"] if s else ""})
                continue
            age_days = max((now - dt.datetime.fromisoformat(
                s["published_at"].replace("Z", "+00:00"))).days, 1)
            ranked.append({
                "niche": niche, "video_id": vid, **s,
                "comments_per_1k_views": round(1000 * s["comments"] / max(s["views"], 1), 2),
                "views_per_day": round(s["views"] / age_days, 1),
                "queries": " | ".join(sorted(qs)),
            })
        ranked.sort(key=lambda r: r["comments"], reverse=True)
        rows.extend(ranked[:args.top])
        print(f"{niche}: {len(vids)} found, {len(ranked)} passed filters, "
              f"kept {min(len(ranked), args.top)}", file=sys.stderr)

    with open("videos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    with open("rejected.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["niche", "video_id", "reason", "title"])
        w.writeheader()
        w.writerows(rejected)

    if spent:  # shared daily counter with app.py (Pacific-time day)
        from zoneinfo import ZoneInfo
        day = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        qp = Path("data/quota.json")
        try:
            q = json.loads(qp.read_text())
        except (FileNotFoundError, ValueError):
            q = {}
        prev = q.get("searches", 0) if q.get("day") == day else 0
        qp.write_text(json.dumps({"day": day, "searches": prev + spent}))

    print(f"\nSearches spent this run: {spent}. Videos kept: {len(rows)} -> videos.csv")
    if missing:
        print(f"Not yet searched ({len(missing)}): re-run after the quota resets "
              f"(midnight Pacific).", file=sys.stderr)


if __name__ == "__main__":
    main()
