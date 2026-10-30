"""Download Titled Tuesday games and flatten them into data/games.csv.

One row per game. Besides the pre-game fields used for modelling, a few
post-game fields are kept (result, end_time, ply count, termination) because
they are needed for the target, for ordering, or for diagnostics. Which
columns may be used as features is decided in features.py, not here.

    python -m ttpredict.collect
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pandas as pd

from .api import get_json, tournament_url
from .config import GAMES_CSV, TOURNAMENTS

DRAW_CODES = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}

_COMMENT = re.compile(r"\{[^}]*\}")
_MOVE_NUMBER = re.compile(r"\d+\.(\.\.)?")
_RESULT_TOKEN = re.compile(r"(1-0|0-1|1/2-1/2|\*)\s*$")


def outcome(white_result: str, black_result: str) -> str:
    """Map Chess.com per-side result codes to White's outcome."""
    if white_result == "win":
        return "white_win"
    if black_result == "win":
        return "black_win"
    if white_result in DRAW_CODES and black_result in DRAW_CODES:
        return "draw"
    raise ValueError(f"unrecognised result pair: {white_result!r}, {black_result!r}")


def count_plies(pgn: str) -> int:
    """Number of half-moves in a PGN (diagnostic only — never a feature)."""
    movetext = pgn.split("\n\n", 1)[1] if "\n\n" in pgn else ""
    movetext = _RESULT_TOKEN.sub("", _MOVE_NUMBER.sub(" ", _COMMENT.sub(" ", movetext)))
    return len(movetext.split())


def pgn_tag(pgn: str, tag: str) -> str | None:
    m = re.search(rf'^\[{tag} "([^"]*)"\]', pgn, flags=re.MULTILINE)
    return m.group(1) if m else None


def start_time(pgn: str) -> int:
    """Game start as a Unix timestamp, from the PGN's UTCDate/UTCTime tags."""
    stamp = datetime.strptime(f"{pgn_tag(pgn, 'UTCDate')} {pgn_tag(pgn, 'UTCTime')}", "%Y.%m.%d %H:%M:%S")
    return int(stamp.replace(tzinfo=timezone.utc).timestamp())


def tournament_games(slug: str, t_index: int) -> list[dict]:
    meta = get_json(tournament_url(slug))
    rows = []
    for round_no, round_url in enumerate(meta["rounds"], start=1):
        assert round_url.endswith(f"/{round_no}"), round_url
        for group_url in get_json(round_url)["groups"]:
            for g in get_json(group_url)["games"]:
                w, b = g["white"], g["black"]
                pgn = g.get("pgn", "")
                rows.append(
                    {
                        "tournament": slug,
                        "t_index": t_index,
                        "t_start_time": meta["start_time"],
                        "round": round_no,
                        "n_rounds": meta["settings"]["total_rounds"],
                        "game_id": g["url"].rsplit("/", 1)[1],
                        "start_time": start_time(pgn),
                        "white": w["username"].lower(),
                        "black": b["username"].lower(),
                        "time_control": g["time_control"],
                        "rated": g["rated"],
                        # --- post-game columns: target / diagnostics only ---
                        # NB: the API's per-game "rating" is the rating *after*
                        # the game (verified empirically, see README), so it
                        # leaks the result and is never used as a feature.
                        "white_rating_post": w["rating"],
                        "black_rating_post": b["rating"],
                        "white_result": w["result"],
                        "black_result": b["result"],
                        "outcome": outcome(w["result"], b["result"]),
                        "end_time": g["end_time"],
                        "n_plies": count_plies(pgn),
                        "termination": pgn_tag(pgn, "Termination"),
                    }
                )
    return rows


def collect(slugs: list[str] = TOURNAMENTS) -> pd.DataFrame:
    rows = []
    for t_index, slug in enumerate(slugs):
        games = tournament_games(slug, t_index)
        print(f"{slug}: {len(games)} games")
        rows.extend(games)
    df = pd.DataFrame(rows)
    n_dupes = df.duplicated("game_id").sum()
    if n_dupes:
        print(f"dropping {n_dupes} duplicate game ids")
        df = df.drop_duplicates("game_id")
    return df.sort_values(["t_index", "round", "game_id"]).reset_index(drop=True)


def main() -> None:
    df = collect()
    GAMES_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(GAMES_CSV, index=False, lineterminator="\n")
    print(f"wrote {len(df)} games to {GAMES_CSV}")


if __name__ == "__main__":
    main()
