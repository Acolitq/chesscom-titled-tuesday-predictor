"""Leakage tests for the feature pipeline.

The core property: a game's features must not change when we alter anything
that happens at or after its start — its own result and post-game ratings, or
any later game.
"""

import numpy as np
import pandas as pd
import pytest

from ttpredict.collect import count_plies, outcome, start_time
from ttpredict.features import FEATURE_COLUMNS, build_features, expected_score

HOUR = 3600
T0 = 1_770_000_000  # tournament start


def _history_game(gid, end, white, black, w_post, b_post, w_res, b_res):
    return {
        "game_id": gid, "end_time": end, "white": white, "black": black,
        "white_rating_post": w_post, "black_rating_post": b_post,
        "white_result": w_res, "black_result": b_res,
    }


def _toy():
    """Two players with some pre-tournament history and a 2-round event."""
    history = [
        # pre-tournament games (within the 30-day window)
        _history_game("h1", T0 - 5 * 24 * HOUR, "a", "x", 3000, 2900, "win", "resigned"),
        _history_game("h2", T0 - 2 * 24 * HOUR, "b", "y", 2800, 2810, "agreed", "agreed"),
        _history_game("h3", T0 - 1 * HOUR, "a", "y", 3008, 2802, "win", "timeout"),
        _history_game("h4", T0 - 3 * HOUR, "d", "z", 2610, 2500, "win", "resigned"),
        # tournament round 1 (a vs c, b vs d) and round 2 (a vs b)
        _history_game("r1a", T0 + 600, "a", "c", 3015, 2700, "win", "resigned"),
        _history_game("r1b", T0 + 650, "d", "b", 2600, 2808, "resigned", "win"),
        _history_game("r2", T0 + 1300, "a", "b", 3005, 2818, "resigned", "win"),
        # a game after the tournament
        _history_game("post", T0 + 5000, "a", "b", 2990, 2830, "resigned", "win"),
    ]
    games = pd.DataFrame(
        [
            {"tournament": "t", "t_index": 0, "t_start_time": T0, "round": 1, "game_id": "r1a", "start_time": T0,
             "white": "a", "black": "c", "outcome": "white_win", "white_rating_post": 3015, "black_rating_post": 2700},
            {"tournament": "t", "t_index": 0, "t_start_time": T0, "round": 1, "game_id": "r1b", "start_time": T0,
             "white": "d", "black": "b", "outcome": "black_win", "white_rating_post": 2600, "black_rating_post": 2808},
            {"tournament": "t", "t_index": 0, "t_start_time": T0, "round": 2, "game_id": "r2", "start_time": T0 + 700,
             "white": "a", "black": "b", "outcome": "black_win", "white_rating_post": 3005, "black_rating_post": 2818},
        ]
    )
    return games, pd.DataFrame(history)


def test_pre_rating_is_previous_game_post_rating():
    games, history = _toy()
    f = build_features(games, history, draw_prior=0.1).set_index("game_id")
    # Round 1: last game before start.
    assert f.loc["r1a", "white_pre"] == 3008
    assert f.loc["r1b", "black_pre"] == 2800
    # Round 2: the previous round's post-game rating, not this game's.
    assert f.loc["r2", "white_pre"] == 3015
    assert f.loc["r2", "black_pre"] == 2808
    # Players with no history at all stay missing (handled downstream).
    assert np.isnan(f.loc["r1a", "black_pre"])


def test_tournament_state_uses_only_earlier_rounds():
    games, history = _toy()
    f = build_features(games, history, draw_prior=0.1).set_index("game_id")
    assert f.loc["r1a", "points_white"] == 0 and f.loc["r1a", "overperf_white"] == 0
    assert f.loc["r2", "points_white"] == 1 and f.loc["r2", "points_black"] == 1
    assert f.loc["r2", "points_diff"] == 0
    # b (pre 2800) beat d (pre 2610), so b's over-performance after round 1
    # is 1 - E(2800 vs 2610).
    assert f.loc["r1b", "white_pre"] == 2610
    assert f.loc["r2", "overperf_black"] == pytest.approx(1 - expected_score(2800, 2610))


def test_form_features_only_use_games_before_tournament_start():
    games, history = _toy()
    f = build_features(games, history, draw_prior=0.1).set_index("game_id")
    # a played h1, h3 in the window (h3 ended one hour before the start).
    assert f.loc["r1a", "log_games_white"] == pytest.approx(np.log1p(2))
    assert f.loc["r1a", "trend_white"] == 8  # 3008 - 3000
    # In round 2, form is still frozen at the tournament start.
    assert f.loc["r2", "log_games_white"] == pytest.approx(np.log1p(2))
    # b drew 1 of 1 games: shrunk towards the prior.
    assert f.loc["r1b", "draw_rate_black"] == pytest.approx((1 + 20 * 0.1) / (1 + 20))


@pytest.mark.parametrize("target", ["r1a", "r2"])
def test_features_invariant_to_present_and_future(target):
    games, history = _toy()
    base = build_features(games, history, draw_prior=0.1).set_index("game_id")

    start = games.set_index("game_id").loc[target, "start_time"]
    later = history["end_time"] > start  # this game and everything after it
    rng = np.random.default_rng(0)
    h2 = history.copy()
    h2.loc[later, "white_rating_post"] = rng.integers(1000, 3500, later.sum())
    h2.loc[later, "black_rating_post"] = rng.integers(1000, 3500, later.sum())
    h2.loc[later, ["white_result", "black_result"]] = ["agreed", "agreed"]
    g2 = games.copy()
    g2.loc[g2["start_time"] >= start, "outcome"] = "draw"
    g2.loc[g2["start_time"] >= start, ["white_rating_post", "black_rating_post"]] = [1, 1]

    pert = build_features(g2, h2, draw_prior=0.1).set_index("game_id")
    pd.testing.assert_series_equal(base.loc[target, FEATURE_COLUMNS], pert.loc[target, FEATURE_COLUMNS])


def test_outcome_mapping():
    assert outcome("win", "resigned") == "white_win"
    assert outcome("checkmated", "win") == "black_win"
    assert outcome("repetition", "repetition") == "draw"
    assert outcome("timevsinsufficient", "timevsinsufficient") == "draw"
    with pytest.raises(ValueError):
        outcome("abandoned", "abandoned")


def test_pgn_helpers():
    pgn = (
        '[UTCDate "2026.02.10"]\n[UTCTime "16:00:00"]\n\n'
        "1. e4 {[%clk 0:04:59.3]} 1... e5 {[%clk 0:04:58]} 2. Nf3 {[%clk 0:04:57]} 1-0"
    )
    assert count_plies(pgn) == 3
    assert start_time(pgn) == 1770739200  # matches the API's tournament start_time
    assert count_plies('[Result "0-1"]\n\n0-1') == 0
