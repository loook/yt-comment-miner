"""Fetch all top-level comments and full reply threads for a YouTube video into a CSV.

Usage: YOUTUBE_API_KEY=... python fetch_comments.py [VIDEO_ID] [OUT_CSV]
Also imported by fetch_niches.py (see fetch_video_comments).
"""
import csv
import os
import sys
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

FIELDS = [
    "comment_id", "parent_id", "is_reply", "author", "author_channel_id",
    "text", "like_count", "reply_count", "published_at", "updated_at",
]


def client():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit("YOUTUBE_API_KEY is not set")
    return build("youtube", "v3", developerKey=key, cache_discovery=False)


def execute(request, retries=5):
    """Run a request, retrying transient 5xx/429 errors with backoff."""
    for attempt in range(retries):
        try:
            return request.execute()
        except HttpError as e:
            status = e.resp.status
            if status in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def error_reason(e):
    """The API's machine-readable reason, e.g. commentsDisabled, quotaExceeded."""
    try:
        return e.error_details[0].get("reason", "")
    except (AttributeError, IndexError, TypeError):
        return ""


def row(snippet, comment_id, parent_id, reply_count=0):
    return {
        "comment_id": comment_id,
        "parent_id": parent_id or "",
        "is_reply": bool(parent_id),
        "author": snippet.get("authorDisplayName", ""),
        "author_channel_id": snippet.get("authorChannelId", {}).get("value", ""),
        "text": snippet.get("textOriginal", snippet.get("textDisplay", "")),
        "like_count": snippet.get("likeCount", 0),
        "reply_count": reply_count,
        "published_at": snippet.get("publishedAt", ""),
        "updated_at": snippet.get("updatedAt", ""),
    }


def fetch_replies(yt, parent_id):
    """commentThreads only embeds up to 5 replies; comments.list returns the full thread."""
    replies, token = [], None
    while True:
        resp = execute(yt.comments().list(
            part="snippet", parentId=parent_id, maxResults=100,
            textFormat="plainText", pageToken=token,
        ))
        for item in resp.get("items", []):
            replies.append(row(item["snippet"], item["id"], parent_id))
        token = resp.get("nextPageToken")
        if not token:
            return replies


def fetch_video_comments(yt, video_id, max_threads=None, log=True):
    """All top-level comments plus full reply threads for one video.

    max_threads caps top-level threads (newest first) to bound quota on huge videos.
    Raises HttpError; callers decide how to handle commentsDisabled / quotaExceeded.
    """
    rows, token, threads = [], None, 0
    while True:
        resp = execute(yt.commentThreads().list(
            part="snippet,replies", videoId=video_id, maxResults=100,
            order="time", textFormat="plainText", pageToken=token,
        ))
        for thread in resp.get("items", []):
            threads += 1
            top = thread["snippet"]["topLevelComment"]
            total_replies = thread["snippet"].get("totalReplyCount", 0)
            rows.append(row(top["snippet"], top["id"], None, total_replies))

            embedded = thread.get("replies", {}).get("comments", [])
            if total_replies > len(embedded):
                rows.extend(fetch_replies(yt, top["id"]))
            else:
                rows.extend(row(c["snippet"], c["id"], top["id"]) for c in embedded)

        token = resp.get("nextPageToken")
        if log:
            print(f"  {video_id}: threads {threads}, rows {len(rows)}", file=sys.stderr)
        if not token or (max_threads and threads >= max_threads):
            return rows


def write_csv(path, rows, fields=FIELDS):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    video_id = sys.argv[1] if len(sys.argv) > 1 else "hBNgCuBTyaU"
    out_csv = sys.argv[2] if len(sys.argv) > 2 else "comments.csv"
    try:
        rows = fetch_video_comments(client(), video_id)
    except HttpError as e:
        sys.exit(f"commentThreads.list failed: HTTP {e.resp.status} {error_reason(e)}: {e}")

    write_csv(out_csv, rows)
    top = [r for r in rows if not r["is_reply"]]
    n_replies = len(rows) - len(top)
    expected = sum(r["reply_count"] for r in top)
    print(f"Saved {len(rows)} rows ({len(top)} top-level, {n_replies} replies; "
          f"threads report {expected} replies) to {out_csv}.")


if __name__ == "__main__":
    main()
