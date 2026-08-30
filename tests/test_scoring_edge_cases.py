"""
Grading cases the hand worked set did not reach, found by validating the
real payloads.

These pin current behaviour rather than asserting it is correct. Where the
behaviour is wrong the test says so, so phase 2 does not build a backtest on
top of it and report a number nobody can explain.
"""

from __future__ import annotations

import json

import pytest

from conftest import FIXTURES, ROOT
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


# ---------------------------------------------------------------------
# Names across sources
#
# 30 August: the ledger stored the odds board's "Hawaii Rainbow Warriors"
# and "NC State Wolfpack", CFBD stored "Hawai'i" and "NC State", and
# grade_pick compared them as strings. Both picks lost, both were voided,
# and a 0 and 2 week was recorded as no result at all.
#
# An error that flatters the record is the worst kind available here.
# ---------------------------------------------------------------------

def _game(home, away, hs, as_):
    return {"home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_, "completed": True}


def _spread(side, line=4.5):
    return {"market": "spread", "period": "full", "side": side, "line": line}


@pytest.mark.parametrize("side", [
    "Hawaii Rainbow Warriors", "Hawai'i", "Hawaii"])
def test_a_dog_that_lost_outright_is_a_loss_however_it_is_spelled(side):
    g = _game("Stanford", "Hawai'i", 37, 27)
    assert grade_pick(_spread(side), g) == "loss"


def test_the_mascot_name_from_the_odds_board_still_grades():
    g = _game("Virginia", "NC State", 34, 8)
    assert grade_pick(_spread("NC State Wolfpack"), g) == "loss"


def test_a_cover_is_still_a_win_across_spellings():
    # Lost by 3 while getting 4.5.
    g = _game("Stanford", "Hawai'i", 24, 21)
    assert grade_pick(_spread("Hawaii Rainbow Warriors"), g) == "win"


def test_a_name_that_cannot_be_placed_still_voids():
    # Voiding beats guessing which team was bet. Ohio and Ohio State are
    # different schools and a prefix rule would have called them one.
    g = _game("Ohio State", "Michigan", 30, 10)
    assert grade_pick(_spread("Ohio"), g) == "void"


def test_a_team_not_in_the_game_at_all_voids():
    g = _game("Virginia", "NC State", 34, 8)
    assert grade_pick(_spread("Alabama Crimson Tide"), g) == "void"


def test_a_void_is_not_a_settled_result_and_stays_gradeable():
    """
    The ledger freezes what a pick was and what it settled as. A void is
    not a settlement, it is a failure to reach one, so it has to stay
    eligible once the reason it failed is fixed. Otherwise 2 losses sit
    permanently outside the record because of a name spelling.
    """
    src = (ROOT / "scripts" / "grade_results.py").read_text()
    assert 'UNRESOLVED = ("pending", "void")' in src
    assert 'p.get("result") in UNRESOLVED' in src
