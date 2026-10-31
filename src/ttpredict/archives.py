"""Fetch each player's monthly game archives (rated blitz games only).

Two things that the tournament endpoints cannot give us come from here:

* the *pre-game* rating — the API's per-game rating is the post-game rating,
  so a player's rating going into a game is the post-game rating of their
  previous rated blitz game;
* recent activity/form before the tournament (games played, rating trend,
  draw tendency).

For every player we fetch the month of each tournament they played and the
month 30 days before it. Archives are slimmed to a handful of fields before
being cached (no PGNs), and every past month is immutable, so the cache never
goes stale.

    python -m ttpredict.archives
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .api import fetch
from .config import API_BASE, GAMES_CSV, RAW_DIR

ARCHIVE_DIR = RAW_DIR / "archives"
HISTORY_CSV = RAW_DIR / "blitz_history.csv"
LOOKBACK_DAYS = 30
N_WORKERS = 4


def _path(user: str, year: int, month: int) -> Path:
    return ARCHIVE_DIR / user / f"{year:04d}-{month:02d}.json"


def _slim(game: dict) -> dict | None:
    if game.get("rules") != "chess" or game.get("time_class") != "blitz" or not game.get("rated"):
        return None
    w, b = game["white"], game["black"]
    return {
        "game_id": game["url"].rsplit("/", 1)[1],
        "end_time": game["end_time"],
        "white": w["username"].lower(),
        "black": b["username"].lower(),
        "white_rating_post": w["rating"],
        "black_rating_post": b["rating"],
        "white_result": w["result"],
        "black_result": b["result"],
    }


def fetch_month(user: str, year: int, month: int) -> list[dict]:
    path = _path(user, year, month)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["games"]
    data = fetch(f"{API_BASE}/player/{user}/games/{year:04d}/{month:02d}", missing_ok=True)
    games = [] if data is None else [s for g in data.get("games", []) if (s := _slim(g))]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"missing": data is None, "games": games}), encoding="utf-8", newline="\n")
    return games


def needed_months(games: pd.DataFrame) -> set[tuple[str, int, int]]:
    out = set()
    for side in ("white", "black"):
        for user, t0 in zip(games[side], games["t_start_time"]):
            start = datetime.fromtimestamp(t0, tz=timezone.utc)
            for d in (start, start - timedelta(days=LOOKBACK_DAYS)):
                out.add((user, d.year, d.month))
    return out


def build_history(games: pd.DataFrame) -> pd.DataFrame:
    """Download (or load) all needed archives; return one row per rated blitz game."""
    todo = sorted(needed_months(games))
    rows: dict[str, dict] = {}
    with ThreadPoolExecutor(N_WORKERS) as pool:
        futures = {pool.submit(fetch_month, *key): key for key in todo}
        for i, fut in enumerate(as_completed(futures), start=1):
            for g in fut.result():
                rows[g["game_id"]] = g  # the same game appears in both players' archives
            if i % 250 == 0 or i == len(todo):
                print(f"archives: {i}/{len(todo)}", flush=True)
    return pd.DataFrame(rows.values()).sort_values(["end_time", "game_id"]).reset_index(drop=True)


def main() -> None:
    games = pd.read_csv(GAMES_CSV)
    history = build_history(games)
    history.to_csv(HISTORY_CSV, index=False, lineterminator="\n")
    print(f"wrote {len(history)} rated blitz games to {HISTORY_CSV}")


if __name__ == "__main__":
    main()
