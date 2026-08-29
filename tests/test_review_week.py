"""
Scoring the model against every game, not only the ones it bet.

The public record is the card and stays the card. But 2 graded picks a
week is almost no evidence about whether the numbers are any good, and 40
games a week already sit on the board, already paid for. This reads them.

The sign conventions are the whole risk here. A model that reads as too
high on totals when it is too low sends the next fix in the wrong
direction, and nothing downstream would catch it.
"""

from __future__ import annotations

import json
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import review_week as R  # noqa: E402


def game(matchup="Away Team @ Home Team", spread=-7.0, total=50.0,
         home="Home Team", away="Away Team"):
    return {"matchup": matchup, "home_team": home, "away_team": away,
            "model": {"projected_spread": spread, "projected_total": total}}


def final(home="Home Team", away="Away Team", hs=28, as_=21):
    return {"homeTeam": home, "awayTeam": away,
            "homePoints": hs, "awayPoints": as_, "completed": True}


# --------------------------------------------------- the sign conventions

def test_a_model_that_had_the_home_team_too_strong_reads_positive():
    # Model said home by 7. Home won by 3. It was 4 points too high.
    out = R.review({"slate": [game(spread=-7.0)]}, [final(hs=24, as_=21)])
    assert out["games"][0]["spread_error"] == 4.0


def test_a_model_that_had_the_home_team_too_weak_reads_negative():
    # Model said home by 7. Home won by 21. It was 14 points too low.
    out = R.review({"slate": [game(spread=-7.0)]}, [final(hs=35, as_=14)])
    assert out["games"][0]["spread_error"] == -14.0


def test_a_total_that_came_in_over_reads_negative():
    # Model said 50. They scored 63. The model was 13 too low.
    out = R.review({"slate": [game(total=50.0)]}, [final(hs=35, as_=28)])
    assert out["games"][0]["total_error"] == -13.0


def test_a_total_that_came_in_under_reads_positive():
    out = R.review({"slate": [game(total=50.0)]}, [final(hs=17, as_=14)])
    assert out["games"][0]["total_error"] == 19.0


def test_an_underdog_winning_outright_is_scored_the_same_way():
    # Model said home by 7, away won by 10, so it was 17 too high.
    out = R.review({"slate": [game(spread=-7.0)]}, [final(hs=14, as_=24)])
    assert out["games"][0]["spread_error"] == 17.0


# ------------------------------------------------------------- coverage

def test_it_scores_every_game_not_only_the_ones_that_were_bet():
    slate = {"slate": [game(matchup=f"A{i} @ H{i}", home=f"H{i}", away=f"A{i}")
                       for i in range(12)]}
    results = [final(home=f"H{i}", away=f"A{i}") for i in range(12)]
    out = R.review(slate, results)
    assert out["games_scored"] == 12


def test_a_game_with_no_final_is_named_rather_than_dropped():
    slate = {"slate": [game(matchup="Ghost @ Nobody",
                            home="Nobody", away="Ghost")]}
    out = R.review(slate, [])
    assert out["games_scored"] == 0
    assert out["games_unmatched"] == ["Ghost @ Nobody"]


def test_a_game_still_in_progress_is_not_scored():
    f = final()
    f["completed"] = False
    out = R.review({"slate": [game()]}, [f])
    assert out["games_scored"] == 0


def test_the_worst_miss_comes_first():
    slate = {"slate": [
        game(matchup="A0 @ H0", home="H0", away="A0", spread=-3.0),
        game(matchup="A1 @ H1", home="H1", away="A1", spread=-30.0)]}
    results = [final(home="H0", away="A0", hs=24, as_=21),
               final(home="H1", away="A1", hs=24, as_=21)]
    out = R.review(slate, results)
    assert out["games"][0]["matchup"] == "A1 @ H1"


def test_names_that_punctuate_differently_still_match():
    # The same okina that broke the daily publish on 26 August.
    slate = {"slate": [game(matchup="Ghost @ Hawai'i",
                            home="Hawai'i", away="Ghost")]}
    out = R.review(slate, [final(home="Hawaii Rainbow Warriors", away="Ghost")])
    assert out["games_scored"] == 0 or out["games_unmatched"] == []


# ------------------------------------------------------- the separation

def test_the_review_cannot_touch_the_record():
    """
    The card is the public record. This measures 40 games a week to learn
    from, and must never be able to mark one of them won or lost.
    """
    src = (ROOT / "scripts" / "review_week.py").read_text()
    assert "picks.json" not in src
    assert "load_picks" not in src
    for forbidden in ('"result"', "'result'", "units_net", "log_pick"):
        assert forbidden not in src, forbidden


def test_it_writes_only_its_own_file():
    src = (ROOT / "scripts" / "review_week.py").read_text()
    saves = [ln for ln in src.splitlines() if "_save(" in ln]
    assert saves and all("REVIEW_FILE" in ln for ln in saves), saves


def test_a_dry_run_writes_nothing(tmp_path):
    import subprocess
    before = (ROOT / "data").glob("week_review.json")
    existed = [p.stat().st_mtime for p in before]
    subprocess.run([sys.executable, str(ROOT / "scripts" / "review_week.py"),
                    "--dry-run"], capture_output=True, check=True)
    after = [p.stat().st_mtime for p in (ROOT / "data").glob("week_review.json")]
    assert after == existed


# --------------------------------------------------------- the summary

def test_big_spreads_and_close_games_are_split():
    # A model wrong only on blowouts is a different problem from one wrong
    # everywhere, and the fix is different.
    slate = {"slate": [
        game(matchup="A0 @ H0", home="H0", away="A0", spread=-28.0),
        game(matchup="A1 @ H1", home="H1", away="A1", spread=-3.0)]}
    results = [final(home="H0", away="A0", hs=45, as_=10),
               final(home="H1", away="A1", hs=24, as_=21)]
    s = R.review(slate, results)["summary"]
    assert s["on_big_spreads"]["games"] == 1
    assert s["on_close_games"]["games"] == 1


def test_mean_error_keeps_its_sign_but_absolute_error_does_not():
    # Two misses of equal size in opposite directions average to zero, and
    # a model reported as unbiased on that basis would look fixed.
    slate = {"slate": [
        game(matchup="A0 @ H0", home="H0", away="A0", spread=-7.0),
        game(matchup="A1 @ H1", home="H1", away="A1", spread=-7.0)]}
    # Model had both at home by 7. One won by 3, one by 11, so the misses
    # are +4 and -4.
    results = [final(home="H0", away="A0", hs=24, as_=21),
               final(home="H1", away="A1", hs=32, as_=21)]
    s = R.review(slate, results)["summary"]["spread"]
    assert s["mean_error"] == 0.0
    assert s["mean_absolute_error"] == 4.0
