# Titled Tuesday outcome prediction

Chess.com Engineering Internship – AI/ML take-home, **Option A**: predict the
result of a Titled Tuesday game (White win / draw / Black win) using only
information available **before the game starts**.

**→ The write-up (decisions, results, honest assessment, next steps) is in [REPORT.md](REPORT.md).**
Auto-generated numbers are in [reports/results.md](reports/results.md) and
[reports/metrics.json](reports/metrics.json).

## Quick start

Requires Python ≥ 3.11.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

`python run.py` trains and evaluates every model from the committed
`data/features.csv` and writes `reports/` (≈ 5–15 minutes on a laptop, almost
all of it hyper-parameter search and bootstrap). Run the tests with
`python -m pytest`.

To rebuild the dataset from the Chess.com API instead:

```bash
python run.py --refresh
```

This downloads the 13 tournaments (~300 requests) and ~4,300 player-month game
archives (~20–40 minutes; responses are cached in `data/raw/`, so an
interrupted run resumes where it stopped).

## Pipeline

| step | module | output |
|---|---|---|
| discover Titled Tuesday events (documents how the pinned list was found) | `ttpredict.discover` | stdout |
| download tournaments → one row per game | `ttpredict.collect` | `data/games.csv` |
| download players' monthly blitz archives | `ttpredict.archives` | `data/raw/blitz_history.csv` (not committed) |
| pre-game features + sanity checks | `ttpredict.features` | `data/features.csv`, `data/build_stats.json` |
| model selection, test evaluation, plots | `ttpredict.model` | `reports/` |

```
src/ttpredict/
  config.py     tournament list, train/test split, paths
  api.py        cached, retrying Chess.com API client
  discover.py   find tournament slugs via players' tournament histories
  collect.py    tournaments → games.csv
  archives.py   player game archives (for pre-game ratings and form)
  features.py   leakage-free features
  model.py      CV, training, evaluation, report generation
tests/          leakage tests (features must not change when the present/future changes)
```

## Data

* 13 weekly Titled Tuesday events, 6 Jan – 31 Mar 2026 (includes the two
  events named in the brief, 10 Feb and 10 Mar), 24,786 games.
* Train: the 9 events from 6 Jan – 3 Mar. Test: the 4 events from 10 – 31 Mar.
* 399,401 rated blitz games from the participants' archives, used only for
  pre-game ratings and pre-tournament form.
