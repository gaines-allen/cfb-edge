"""
Grading cases the hand worked set did not reach, found by validating the
real payloads.

These pin current behaviour rather than asserting it is correct. Where the
behaviour is wrong the test says so, so phase 2 does not build a backtest on
top of it and report a number nobody can explain.
"""

from __future__ import annotations

import json

from conftest import FIXTURES
from lib.scoring import grade_pick, half_points


def find_shortened_game():
    """
    Kentucky 31 Southern Miss 0, 2024 week 1, called after 3 quarters. Real
    data, not a shape change, and the reason the quarter count check in
    schema.py has no minimum.
    """
    rows = json.loads((FIXTURES / "cfbd" / "games_2024_w01.json").read_text())
    for g in rows:
        hl = g.get("homeLineScores") or []
        if g.get("completed") and 0 < len(hl) < 4:
            return g
    return None


def test_a_weather_shortened_game_exists_in_the_fixtures():
    g = find_shortened_game()
    assert g is not None
    assert len(g["homeLineScores"]) == 3


def test_first_half_still_settles_on_a_shortened_game():
    """2 quarters were played, so a first half bet has everything it needs."""
    g = find_shortened_game()
    assert half_points(g["homeLineScores"], 1) is not None
    pick = {"market": "spread", "period": "h1",
            "side": g["homeTeam"], "line": -13.5}
    game = {
        "home_team": g["homeTeam"], "away_team": g["awayTeam"],
        "home_score": g["homePoints"], "away_score": g["awayPoints"],
        "home_line_scores": g["homeLineScores"],
        "away_line_scores": g["awayLineScores"],
        "completed": True,
    }
    assert grade_pick(pick, game) in ("win", "loss", "push")


def test_second_half_on_a_shortened_game_stays_pending_forever():
    """
    Known gap, pinned rather than endorsed. half_points requires 4 quarters
    for the second half, so a game called early returns None and grade_pick
    answers pending. A pending pick never settles, drops out of the record,
    and the handoff calls that the most dangerous failure this system has.

    It has not been changed here because the ported assertion set requires
    the current behaviour, and because a book would more likely void such a
    ticket than settle it. Phase 2 has to decide which, and until it does
    the backtest must exclude shortened games rather than score them.
    """
    g = find_shortened_game()
    assert half_points(g["homeLineScores"], 2) is None
    pick = {"market": "spread", "period": "h2",
            "side": g["homeTeam"], "line": -3.5}
    game = {
        "home_team": g["homeTeam"], "away_team": g["awayTeam"],
        "home_score": g["homePoints"], "away_score": g["awayPoints"],
        "home_line_scores": g["homeLineScores"],
        "away_line_scores": g["awayLineScores"],
        "completed": True,
    }
    assert grade_pick(pick, game) == "pending"


def test_a_full_game_pick_settles_on_a_shortened_game():
    """The full game number is final, so this one is unaffected."""
    g = find_shortened_game()
    game = {
        "home_team": g["homeTeam"], "away_team": g["awayTeam"],
        "home_score": g["homePoints"], "away_score": g["awayPoints"],
        "home_line_scores": g["homeLineScores"],
        "away_line_scores": g["awayLineScores"],
        "completed": True,
    }
    pick = {"market": "spread", "period": "full",
            "side": g["homeTeam"], "line": -24.5}
    assert grade_pick(pick, game) == "win"
