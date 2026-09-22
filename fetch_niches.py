"""Fetch comments for every video in videos.csv, one cached CSV per video.

Usage: python fetch_niches.py [--max-threads 2000]

Per-video files go to data/comments/<video_id>.csv; videos already fetched are skipped,
so an interrupted or quota-capped run resumes where it stopped.
max-threads caps top-level threads per video (newest first) so one viral video can't eat
the daily 10,000-unit quota: 2,000 threads is ~20 calls plus reply fetches.
"""
import argparse
import csv
import sys
from pathlib import Path

from googleapiclient.errors import HttpError

from fetch_comments import client, error_reason, fetch_video_comments, write_csv

OUT = Path("data/comments")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", default="videos.csv")
    ap.add_argument("--max-threads", type=int, default=2000)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    videos = list(csv.DictReader(open(args.videos, encoding="utf-8")))
    yt = client()
    done = skipped = 0
    for i, v in enumerate(videos, 1):
        path = OUT / f"{v['video_id']}.csv"
        if path.exists():
            continue
        print(f"[{i}/{len(videos)}] {v['niche']}: {v['title'][:60]}", file=sys.stderr)
        try:
            rows = fetch_video_comments(yt, v["video_id"], args.max_threads, log=False)
        except HttpError as e:
            reason = error_reason(e)
            if reason == "quotaExceeded":
                sys.exit(f"Daily quota exhausted after {done} videos; re-run after "
                         f"midnight Pacific to resume.")
            print(f"  skipped: HTTP {e.resp.status} {reason}", file=sys.stderr)
            path.write_text("")  # mark as attempted (e.g. commentsDisabled)
            skipped += 1
            continue
        write_csv(path, rows)
        done += 1
    print(f"Fetched {done} videos, skipped {skipped}. Files in {OUT}/")


if __name__ == "__main__":
    main()
