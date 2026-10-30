"""Leakage-free pre-game features for each Titled Tuesday game.

Every feature for a game is computed only from information that existed before
that game started:

* Pre-game rating: the post-game rating of the player's most recent rated
  blitz game that *ended* at or before this game's start time. (The API's
  per-game rating is the post-game rating and leaks the result.)
* Recent form: rated blitz games that ended before the *tournament* started
  (last 30 days): number of games, rating trend, draw tendency.
* Tournament state: results of the same tournament's earlier rounds only
  (points so far and performance against rating expectation).

Nothing derived from the game itself (moves, opening, duration, result) or
from later games is used. tests/test_features.py checks this by perturbing
the present and the future and asserting that features do not move.

    python -m ttpredict.features
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import DATA_DIR, GAMES_CSV

FEATURES_CSV = DATA_DIR / "features.csv"
WINDOW_S = 30 * 24 * 3600  # recent-form window before the tournament start
DRAW_PRIOR_GAMES = 20  # pseudo-games used to shrink each player's draw rate

SCORE = {"white_win": 1.0, "draw": 0.5, "black_win": 0.0}
DRAW_CODES = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}

# Columns the models may use. Everything else in features.csv is an identifier,
# the target, or a diagnostic.
FEATURE_COLUMNS = [
    "rating_diff",
    "abs_rating_diff",
    "rating_mean",
    "round",
    "points_diff",
    "points_mean",
    "overperf_white",
    "overperf_black",
    "trend_white",
    "trend_black",
    "log_games_white",
    "log_games_black",
    "draw_rate_white",
    "draw_rate_black",
]


def expected_score(rating: np.ndarray | float, opp_rating: np.ndarray | float) -> np.ndarray | float:
    """Elo expected score of `rating` against `opp_rating`."""
    return 1.0 / (1.0 + 10.0 ** ((opp_rating - rating) / 400.0))


def player_games(history: pd.DataFrame) -> pd.DataFrame:
    """One row per (player, rated blitz game): end time, post-game rating, draw flag."""
    is_draw = history["white_result"].isin(DRAW_CODES) & history["black_result"].isin(DRAW_CODES)
    sides = []
    for side in ("white", "black"):
        sides.append(
            pd.DataFrame(
                {
                    "player": history[side],
                    "end_time": history["end_time"],
                    "rating_post": history[f"{side}_rating_post"],
                    "is_draw": is_draw.astype(float),
                }
            )
        )
    return pd.concat(sides, ignore_index=True).sort_values("end_time", kind="stable").reset_index(drop=True)


def appearances(games: pd.DataFrame) -> pd.DataFrame:
    """One row per (Titled Tuesday game, side)."""
    cols = ["game_id", "tournament", "t_start_time", "round", "start_time"]
    sides = []
    for side in ("white", "black"):
        a = games[cols + [side]].rename(columns={side: "player"})
        a["side"] = side
        sides.append(a)
    return pd.concat(sides, ignore_index=True)


def _last_rating_before(app: pd.DataFrame, pg: pd.DataFrame, time_col: str) -> pd.Series:
    """Post-game rating of each player's last rated blitz game ending <= app[time_col]."""
    left = app[["player", time_col]].reset_index().sort_values(time_col, kind="stable")
    right = pg[["player", "end_time", "rating_post"]]
    merged = pd.merge_asof(
        left, right, left_on=time_col, right_on="end_time", by="player", direction="backward", allow_exact_matches=True
    )
    return merged.set_index("index")["rating_post"].reindex(app.index)


def form_features(app: pd.DataFrame, pg: pd.DataFrame, draw_prior: float) -> pd.DataFrame:
    """Recent form over the WINDOW_S seconds before each tournament started."""
    keys = app[["player", "t_start_time"]].drop_duplicates().reset_index(drop=True)
    windowed = keys.merge(pg, on="player", how="left")
    windowed = windowed[
        (windowed["end_time"] < windowed["t_start_time"]) & (windowed["end_time"] >= windowed["t_start_time"] - WINDOW_S)
    ].sort_values("end_time", kind="stable")
    grouped = windowed.groupby(["player", "t_start_time"])
    stats = pd.DataFrame(
        {
            "n_games": grouped.size(),
            "n_draws": grouped["is_draw"].sum(),
            # Change in rating across the window: last post-game rating minus
            # the rating after the first game in the window.
            "trend": grouped["rating_post"].last() - grouped["rating_post"].first(),
        }
    ).reset_index()
    out = keys.merge(stats, on=["player", "t_start_time"], how="left")
    out["n_games"] = out["n_games"].fillna(0)
    out["n_draws"] = out["n_draws"].fillna(0)
    out["trend"] = out["trend"].fillna(0.0)
    out["draw_rate"] = (out["n_draws"] + DRAW_PRIOR_GAMES * draw_prior) / (out["n_games"] + DRAW_PRIOR_GAMES)
    out["log_games"] = np.log1p(out["n_games"])
    return app[["player", "t_start_time"]].merge(out, on=["player", "t_start_time"], how="left").set_index(app.index)


def tournament_state(games: pd.DataFrame) -> pd.DataFrame:
    """Points and performance-vs-expectation from earlier rounds of the same event.

    Requires `white_pre` / `black_pre` columns (pre-game ratings). Points count
    every earlier game; over-performance (score minus Elo expectation) only
    counts earlier games where both pre-game ratings are known. Byes are not
    visible in the game list, so points from byes are not counted.
    """
    score_w = games["outcome"].map(SCORE)
    exp_w = expected_score(games["white_pre"], games["black_pre"])
    long = pd.concat(
        [
            pd.DataFrame(
                {"game_id": games["game_id"], "side": "white", "tournament": games["tournament"],
                 "round": games["round"], "player": games["white"], "score": score_w, "expected": exp_w}
            ),
            pd.DataFrame(
                {"game_id": games["game_id"], "side": "black", "tournament": games["tournament"],
                 "round": games["round"], "player": games["black"], "score": 1 - score_w, "expected": 1 - exp_w}
            ),
        ],
        ignore_index=True,
    ).sort_values(["tournament", "player", "round"], kind="stable")
    g = long.groupby(["tournament", "player"])
    # Cumulative sums shifted by one row: only strictly earlier rounds count.
    long["points_before"] = g["score"].cumsum() - long["score"]
    long["residual"] = (long["score"] - long["expected"]).fillna(0.0)
    long["overperf"] = long.groupby(["tournament", "player"])["residual"].cumsum() - long["residual"]
    wide = long.pivot(index="game_id", columns="side", values=["points_before", "overperf"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    return wide.reindex(games["game_id"]).reset_index(drop=True)


def build_features(games: pd.DataFrame, history: pd.DataFrame, draw_prior: float) -> pd.DataFrame:
    """Return `games` with pre-game feature columns added (same row order)."""
    games = games.reset_index(drop=True)
    pg = player_games(history)
    app = appearances(games)

    app["pre_rating"] = _last_rating_before(app, pg, "start_time")
    form = form_features(app, pg, draw_prior)
    for col in ("trend", "log_games", "draw_rate"):
        app[col] = form[col]

    wide = app.pivot(index="game_id", columns="side", values=["pre_rating", "trend", "log_games", "draw_rate"])
    wide = wide.reindex(games["game_id"])
    out = games.copy()
    out["white_pre"] = wide[("pre_rating", "white")].to_numpy()
    out["black_pre"] = wide[("pre_rating", "black")].to_numpy()
    for col in ("trend", "log_games", "draw_rate"):
        out[f"{col}_white"] = wide[(col, "white")].to_numpy()
        out[f"{col}_black"] = wide[(col, "black")].to_numpy()

    state = tournament_state(out)
    out["points_white"] = state["points_before_white"].to_numpy()
    out["points_black"] = state["points_before_black"].to_numpy()
    out["overperf_white"] = state["overperf_white"].to_numpy()
    out["overperf_black"] = state["overperf_black"].to_numpy()

    out["rating_diff"] = out["white_pre"] - out["black_pre"]
    out["abs_rating_diff"] = out["rating_diff"].abs()
    out["rating_mean"] = (out["white_pre"] + out["black_pre"]) / 2
    out["points_diff"] = out["points_white"] - out["points_black"]
    out["points_mean"] = (out["points_white"] + out["points_black"]) / 2
    return out
