# yt-comment-miner

Pull every comment and reply from YouTube videos you choose, then analyse them for audience research.

Built for a simple job: find out what an audience actually says, in their own words, before deciding what to build or which ad angle to test. It has a small local web UI, because picking which videos represent a niche needs human judgement — an automated "top videos by comments" pipeline returns whatever went viral, not what's relevant.

No database, no accounts, no cloud. Python plus one HTML file, everything on your machine.

## What it does

- **Search** YouTube for your terms, with every search cached so repeats cost no quota.
- **Pick** videos by hand in the browser: thumbnail, title, views, comment count, comments per 1,000 views.
- **Export** all their comments **including full reply threads** to CSV.
- **Analyse** an export: structure, behavioural signals, and the most-liked comments per signal to read yourself.

## Requirements

- Python 3.9+
- A YouTube Data API v3 key (free, no billing account)

## Setup

```bash
git clone https://github.com/<you>/yt-comment-miner.git
cd yt-comment-miner
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Get a key: [Google Cloud console](https://console.cloud.google.com) → create a project → **APIs & Services → Library** → enable **YouTube Data API v3** → **Credentials → Create credentials → API key** → restrict it to that API.

Keep the key out of the code. On macOS:

```bash
security add-generic-password -a "$USER" -s youtube-api -w   # paste key when prompted
export YOUTUBE_API_KEY="$(security find-generic-password -a "$USER" -s youtube-api -w)"
```

Elsewhere, export `YOUTUBE_API_KEY` however your system handles secrets. The scripts only ever read it from the environment.

## Use

### The UI (recommended)

```bash
YOUTUBE_API_KEY=... .venv/bin/python app.py
```

Open http://127.0.0.1:8765. Type search terms one per line, search, tick the videos you want, export. The CSV lands in `exports/` and is offered as a download. The server binds to localhost only, and the key stays in the server process — the browser never receives it.

### One video from the command line

```bash
YOUTUBE_API_KEY=... .venv/bin/python fetch_comments.py VIDEO_ID out.csv
```

### Batch mode, by niche

`niches.json` holds niches, their search terms, title keywords a video must match, and phrases that exclude it.

```bash
.venv/bin/python discover.py --orders viewCount,relevance   # -> videos.csv, rejected.csv
.venv/bin/python fetch_niches.py                            # -> data/comments/<video_id>.csv
.venv/bin/python score.py                                   # -> scores.csv, scores.md, evidence.md
```

Use this when you already trust your keywords. Otherwise use the UI — batch discovery is only as good as the filters, and filters can remove bad candidates but never find good ones.

### Analyse an export

```bash
.venv/bin/python analyze.py exports/<file>.csv --samples 30 > analysis.txt
```

Prints structure (threads, replies, repeat commenters, languages), counts for each behavioural signal, then the most-liked comments per signal. The counts narrow it down; reading the comments decides.

Signals: questions · progress check-ins ("day 12") · peer-seeking ("anyone else") · identity statements · app requests · manual tracking · money · fear and pain · desire · gear · timestamp references.

## Quota, and why it shapes everything

Two separate daily pools:

| Pool | Limit | Covers |
|---|---|---|
| Search | **100 calls/day** | `search.list` |
| Everything else | **10,000 units/day** | video metadata, comments, replies |

Comments are cheap — 100 per call, 1 unit. Search is the bottleneck. Both reset at midnight Pacific.

The tools work with that:

- Search results are cached in `data/search/`, keyed by query, sort order, period and result count.
- Comments are cached per video in `data/comments/`, so re-exports cost nothing.
- Exports resume: if the quota runs out, finished videos stay and the next run continues.
- Threads per video are capped (default 2,000) so one viral video can't drain the day's units.

A real day: 85 searches, ~2,000 units, 55 videos, 34,680 comments.

## Notes worth knowing

- `commentThreads.list` embeds **at most 5 replies** per thread. This tool calls `comments.list` whenever a thread reports more, so reply threads come through complete — that's usually the part of a comment section that matters.
- Videos can have comments disabled; those are skipped and marked, not retried.
- `relatedToVideoId` no longer exists, so discovery goes through keywords and channels.
- Sort order changes what you get: `viewCount` returns what went viral, `relevance` what matches, `date` what's being made now.

## Storing what you pull

YouTube's [Developer Policies](https://developers.google.com/youtube/terms/developer-policies) allow this kind of public API data to be stored for **no more than 30 calendar days**, after which it must be deleted or refreshed, and stored data must stay consistent with what's live. `data/` and `exports/` are gitignored for that reason and because the text is other people's writing. Keep your analysis, not the corpus.

## License

MIT
