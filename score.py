"""Score each niche for community potential and app-gap signals from fetched comments.

Usage: python score.py [--videos videos.csv] [--min-comments 500]

Reads videos.csv + data/comments/<video_id>.csv. The creator's own comments are excluded.
Writes:
  scores.csv   - one row per niche, raw metrics + rank-based scores
  scores.md    - the same, as a readable ranked table
  evidence.md  - the most-liked matching comments per niche per signal, for manual reading

Scores are rank-based (0-1, 1 = best niche on that metric), averaged per group, so no
metric dominates because of its scale. They narrow the list; reading evidence.md decides.
"""
import argparse
import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

COMMENTS = Path("data/comments")

# Regexes are English-only and deliberately simple; evidence.md is the check on them.
SIGNALS = {
    # community
    "checkin": r"\b(day|week|month)\s*#?\d{1,3}\b|\bcheck(ing)?[- ]in\b|\bupdate\s*:",
    "peer": (r"\banyone else\b|\bwho else\b|\bjoin me\b|\bwho'?s with me\b|\baccountability\b"
             r"|\bsame here\b|\bme too\b|\bwe got this\b|\bi'?m not alone\b|\bso relatable\b"),
    "identity": (r"\bas an? (\w+ )?(mom|mum|dad|woman|man|runner|nurse|diabetic|beginner|"
                 r"type ?1|t1d|veteran|teacher|lifter|athlete|\d{2})\b|\bfellow\b"
                 r"|\bi'?m \d{2}\b|\bat \d{2}\b|\b\d{2} ?(yo|y/o|years old)\b"),
    # gap
    "app_ask": (r"\bis there an? app\b|\bany (good )?apps?\b|\bwish there (was|were)\b"
                r"|\bneed an app\b|\brecommend an app\b|\bapp (for|that) (this|tracks|would)\b"),
    "manual_tracking": r"\bspreadsheet\b|\bnotes app\b|\bi track\b|\btracking (it|this|my)\b|\bexcel\b|\bjournal(ing)?\b",
    "money": (r"\bi'?d pay\b|\bwould pay\b|\bworth (the|every) (money|penny|cent)\b|\bpaid (a|for|\$)"
              r"|\$\d+|\bsubscription\b|\bpersonal trainer\b|\bphysio(therapist)?\b|\bcoach\b"),
}
SIGNALS = {k: re.compile(v, re.I) for k, v in SIGNALS.items()}

COMMUNITY = ["viewer_reply_thread_share", "viewer_replies_per_thread", "checkin_per_1k",
             "peer_per_1k", "identity_per_1k", "repeat_author_share"]
GAP = ["app_ask_per_1k", "manual_tracking_per_1k", "money_per_1k"]


def load(videos_csv):
    videos = list(csv.DictReader(open(videos_csv, encoding="utf-8")))
    by_niche = defaultdict(list)  # niche -> list of (video, comments)
    for v in videos:
        path = COMMENTS / f"{v['video_id']}.csv"
        if not path.exists() or path.stat().st_size == 0:
            continue
        rows = [r for r in csv.DictReader(open(path, encoding="utf-8"))
                if r["author_channel_id"] != v["channel_id"]]  # drop the creator
        by_niche[v["niche"]].append((v, rows))
    return by_niche


def niche_metrics(items):
    comments = [c for _, rows in items for c in rows]
    n = len(comments)
    tops = [c for c in comments if c["is_reply"] == "False"]
    replies_by_parent = defaultdict(int)
    for c in comments:
        if c["is_reply"] == "True":
            replies_by_parent[c["parent_id"]] += 1  # creator already filtered out

    author_videos = defaultdict(set)
    for v, rows in items:
        for c in rows:
            if c["author_channel_id"]:
                author_videos[c["author_channel_id"]].add(v["video_id"])

    m = {
        "videos": len(items),
        "comments": n,
        "authors": len(author_videos),
        "median_views_per_day": statistics.median(float(v["views_per_day"]) for v, _ in items),
        "viewer_reply_thread_share": (sum(replies_by_parent[t["comment_id"]] >= 2 for t in tops)
                                      / max(len(tops), 1)),
        "viewer_replies_per_thread": (sum(replies_by_parent[t["comment_id"]] for t in tops)
                                      / max(len(tops), 1)),
        "repeat_author_share": (sum(len(vs) >= 2 for vs in author_videos.values())
                                / max(len(author_videos), 1)),
    }
    hits = {}
    for name, rx in SIGNALS.items():
        matched = [c for c in comments if rx.search(c["text"])]
        hits[name] = matched
        m[f"{name}_per_1k"] = 1000 * len(matched) / max(n, 1)
    return m, hits


def rank_scores(table, cols):
    """Percentile rank per column across niches (1 = highest), then mean per niche."""
    k = len(table)
    for col in cols:
        values = sorted(r[col] for r in table)
        for r in table:
            # ties share their average position, so equal values get equal ranks
            lo = values.index(r[col])
            hi = len(values) - 1 - values[::-1].index(r[col])
            r[f"rank_{col}"] = (lo + hi) / 2 / (k - 1) if k > 1 else 1.0
    for r in table:
        yield r, statistics.mean(r[f"rank_{c}"] for c in cols)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", default="videos.csv")
    ap.add_argument("--min-comments", type=int, default=500,
                    help="niches below this are scored but flagged as thin samples")
    ap.add_argument("--evidence", type=int, default=5, help="quotes per signal per niche")
    args = ap.parse_args()

    by_niche = load(args.videos)
    if not by_niche:
        raise SystemExit("No comment files found; run discover.py and fetch_niches.py first.")

    table, evidence = [], {}
    for niche, items in by_niche.items():
        m, hits = niche_metrics(items)
        table.append({"niche": niche, **m})
        evidence[niche] = hits

    for r, s in rank_scores(table, COMMUNITY):
        r["community_score"] = s
    for r, s in rank_scores(table, GAP):
        r["gap_score"] = s
    for r in table:
        r["total_score"] = (r["community_score"] + r["gap_score"]) / 2
        r["thin_sample"] = r["comments"] < args.min_comments
    table.sort(key=lambda r: r["total_score"], reverse=True)

    cols = (["niche", "total_score", "community_score", "gap_score", "thin_sample",
             "videos", "comments", "authors", "median_views_per_day"]
            + COMMUNITY + GAP)
    with open("scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(table)

    fmt = lambda x: f"{x:.2f}" if isinstance(x, float) else str(x)
    short = ["niche", "total_score", "community_score", "gap_score", "comments",
             "viewer_reply_thread_share", "checkin_per_1k", "peer_per_1k", "identity_per_1k",
             "repeat_author_share", "app_ask_per_1k", "manual_tracking_per_1k", "money_per_1k",
             "median_views_per_day"]
    lines = ["# Niche scores", "",
             "Rank-based scores (0-1). `*` = thin sample (< "
             f"{args.min_comments} comments). Rates are per 1,000 viewer comments.", "",
             "| " + " | ".join(short) + " |", "|" + "---|" * len(short)]
    for r in table:
        cells = [fmt(r[c]) for c in short]
        if r["thin_sample"]:
            cells[0] += " *"
        lines.append("| " + " | ".join(cells) + " |")
    Path("scores.md").write_text("\n".join(lines) + "\n")

    out = ["# Evidence — most-liked matching comments", ""]
    for r in table:
        niche = r["niche"]
        out += [f"## {niche}", ""]
        for name, matched in evidence[niche].items():
            top = sorted(matched, key=lambda c: int(c["like_count"] or 0), reverse=True)
            out.append(f"### {name} ({len(matched)} matches)")
            seen = set()
            for c in top:
                text = " ".join(c["text"].split())[:300]
                if text.lower() in seen:  # skip copy-paste duplicates
                    continue
                seen.add(text.lower())
                out.append(f"- [{c['like_count']} likes] {text}")
                if len(seen) == args.evidence:
                    break
            out.append("")
    Path("evidence.md").write_text("\n".join(out))

    print(f"Scored {len(table)} niches -> scores.csv, scores.md, evidence.md")
    for r in table[:5]:
        print(f"  {r['total_score']:.2f}  {r['niche']}  (community {r['community_score']:.2f}, "
              f"gap {r['gap_score']:.2f}, {r['comments']} comments)")


if __name__ == "__main__":
    main()
