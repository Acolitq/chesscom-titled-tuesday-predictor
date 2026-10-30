"""Project-wide configuration: which tournaments to use and how to split them."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
GAMES_CSV = DATA_DIR / "games.csv"
REPORTS_DIR = ROOT / "reports"

API_BASE = "https://api.chess.com/pub"
# Chess.com asks API users to send an identifying User-Agent.
USER_AGENT = "titled-tuesday-outcome-model/0.1 (chess.com internship take-home)"

# Every weekly Titled Tuesday (blitz, 3+0... in practice 5+0 in 2026) from
# January through March 2026, in chronological order. The list was discovered
# from players' /pub/player/{user}/tournaments endpoints (see
# `python -m ttpredict.discover`) and is pinned here so the dataset is fixed.
TOURNAMENTS = [
    "titled-tuesday-blitz-january-06-2026-6140433",
    "titled-tuesday-blitz-january-13-2026-6151065",
    "titled-tuesday-blitz-january-20-2026-6160609",
    "titled-tuesday-blitz-january-27-2026-6170439",
    "titled-tuesday-blitz-february-03-2026-6190821",
    "titled-tuesday-blitz-february-10-2026-6221327",
    "titled-tuesday-blitz-february-17-2026-6221393",
    "titled-tuesday-blitz-february-24-2026-6256793",
    "titled-tuesday-blitz-march-03-2026-6262447",
    "titled-tuesday-blitz-march-10-2026-6277141",
    "titled-tuesday-blitz-march-17-2026-6282783",
    "titled-tuesday-blitz-march-24-2026-6292855",
    "titled-tuesday-blitz-march-31-2026-6322539",
]

# Temporal split: whole tournaments, never shuffled. The model is trained on
# the earlier events and evaluated once on the last four (March 10–31).
N_TEST_TOURNAMENTS = 4
TRAIN_TOURNAMENTS = TOURNAMENTS[:-N_TEST_TOURNAMENTS]
TEST_TOURNAMENTS = TOURNAMENTS[-N_TEST_TOURNAMENTS:]

# Outcome classes, from White's point of view.
CLASSES = ["black_win", "draw", "white_win"]

RANDOM_STATE = 0
