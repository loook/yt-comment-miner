"""Profile an exported comments CSV: structure, signals, and samples to read by hand.

Usage: python analyze.py exports/<file>.csv [--samples 40] > analysis.txt

Prints counts first, then the most-liked comments per signal. The counts narrow things
down; the samples are what a human (or the model) actually reads before concluding.
"""
import argparse
import csv
import re
import sys
from collections import Counter, defaultdict

SIGNALS = {
    "question": r"\?",
    "checkin": r"\b(day|week|month|mile|km)\s*#?\d{1,4}\b|\bcheck(ing)?[- ]in\b|\bupdate\s*:",
    "peer": (r"\banyone else\b|\bwho else\b|\bjoin me\b|\bsame here\b|\bme too\b|\bwe got this\b"
             r"|\byou got this\b|\bi'?m not alone\b|\bcongrat"),
    "identity": (r"\bas an? (\w+ )?(mom|mum|dad|woman|man|runner|beginner|newbie|veteran|\d{2})\b"
                 r"|\bi'?m \d{2}\b|\b\d{2} ?(yo|y/o|years old)\b|\bfirst (ultra|marathon|50k|100)"),
    "app_ask": (r"\bis there an? app\b|\bany (good )?apps?\b|\bwish there (was|were)\b"
                r"|\bneed an app\b|\brecommend an app\b|\bwhat app\b|\bwhich app\b"),
    "tracking": r"\bspreadsheet\b|\bstrava\b|\bgarmin\b|\bcoros\b|\bsuunto\b|\btraining (log|plan|peaks)\b|\bjournal",
    "money": (r"\bi'?d pay\b|\bwould pay\b|\bworth (the|every) (money|penny|cent)\b|\$\d+"
              r"|\bsubscription\b|\bcoach\b|\bentry fee\b|\bexpensive\b"),
    "fear_pain": (r"\bafraid\b|\bscared\b|\bfear\b|\banxiet|\binjur|\bblister|\bcramp|\bdnf\b"
                  r"|\bquit\b|\bgave up\b|\bpain\b|\bhurt\b|\bstruggl"),
    "desire": (r"\bi want to\b|\bi hope to\b|\bgoal\b|\bdream\b|\bone day\b|\binspir|\bmotivat"
               r"|\bsigned up\b|\bnext year\b"),
    "gear": r"\bshoes?\b|\bvest\b|\bpoles?\b|\bwatch\b|\bgels?\b|\bhydration\b|\bpack\b|\bsocks?\b",
    "timestamp": r"\b\d{1,2}:\d{2}\b",
}
SIGNALS = {k: re.compile(v, re.I) for k, v in SIGNALS.items()}
LATIN = re.compile(r"[A-Za-z]")
NON_ENGLISH_HINT = re.compile(
    r"\b(que|pero|para|como|muy|gracias|merci|très|vraiga|juga|yang|dan|saya|tidak|banget|mantap"
    r"|sehr|und|ist|nicht|обо|это)\b", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csvfile")
    ap.add_argument("--samples", type=int, default=40)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csvfile, encoding="utf-8")))
    if not rows:
        sys.exit("empty file")
    tops = [r for r in rows if r["is_reply"] == "False"]
    replies = [r for r in rows if r["is_reply"] == "True"]
    likes = lambda r: int(r["like_count"] or 0)

    by_video = defaultdict(list)
    for r in rows:
        by_video[r["video_title"]].append(r)
    authors = Counter(r["author_channel_id"] for r in rows if r["author_channel_id"])
    author_videos = defaultdict(set)
    for r in rows:
        if r["author_channel_id"]:
            author_videos[r["author_channel_id"]].add(r["video_id"])
    replies_per_thread = Counter(r["parent_id"] for r in replies)

    print(f"FILE {args.csvfile}")
    print(f"comments {len(rows)} | top-level {len(tops)} | replies {len(replies)} "
          f"| videos {len(by_video)} | authors {len(authors)}")
    print(f"dates {min(r['published_at'] for r in rows)[:10]} .. "
          f"{max(r['published_at'] for r in rows)[:10]}")
    print(f"threads with >=2 replies: {sum(v >= 2 for v in replies_per_thread.values())} "
          f"({100*sum(v >= 2 for v in replies_per_thread.values())/max(len(tops),1):.1f}% of threads)")
    print(f"authors on >=2 videos: {sum(len(v) >= 2 for v in author_videos.values())} "
          f"({100*sum(len(v) >= 2 for v in author_videos.values())/max(len(author_videos),1):.1f}%)")
    print(f"authors with >=3 comments: {sum(v >= 3 for v in authors.values())}")
    non_latin = sum(1 for r in rows if not LATIN.search(r["text"]))
    foreign = sum(1 for r in rows if NON_ENGLISH_HINT.search(r["text"]))
    print(f"no Latin letters: {non_latin} | likely non-English: {foreign} "
          f"({100*(non_latin+foreign)/len(rows):.1f}% of all)")

    print("\nPER VIDEO (comments | replies% | median likes | title)")
    for title, rs in sorted(by_video.items(), key=lambda kv: -len(kv[1])):
        rp = sum(1 for r in rs if r["is_reply"] == "True")
        ls = sorted(likes(r) for r in rs)
        print(f"  {len(rs):>5} | {100*rp/len(rs):>4.0f}% | {ls[len(ls)//2]:>4} | {title[:78]}")

    print("\nSIGNAL COUNTS (share of all comments)")
    hits = {}
    for name, rx in SIGNALS.items():
        hits[name] = [r for r in rows if rx.search(r["text"])]
        print(f"  {name:<10} {len(hits[name]):>6}  {100*len(hits[name])/len(rows):>5.1f}%  "
              f"likes {sum(map(likes, hits[name])):>7}")

    print(f"\n=== MOST-LIKED COMMENTS OVERALL (top {args.samples}) ===")
    for r in sorted(rows, key=likes, reverse=True)[:args.samples]:
        print(f"[{r['like_count']}] {' '.join(r['text'].split())[:400]}")

    for name in SIGNALS:
        print(f"\n=== {name.upper()} (top {args.samples}) ===")
        seen = set()
        for r in sorted(hits[name], key=likes, reverse=True):
            t = " ".join(r["text"].split())[:400]
            if t.lower() in seen:
                continue
            seen.add(t.lower())
            print(f"[{r['like_count']}] {t}")
            if len(seen) >= args.samples:
                break


if __name__ == "__main__":
    main()
