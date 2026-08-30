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


def test_the_odds_board_name_matches_the_cfbd_name():
    """
    The slate carries the odds board's name and the finals carry CFBD's.
    Keyed on normalize() these landed in different buckets and 39 of 39
    games went unmatched while the run reported success.
    """
    slate = {"slate": [game(matchup="Stanford @ Hawaii Rainbow Warriors",
                            home="Hawaii Rainbow Warriors", away="Stanford")]}
    out = R.review(slate, [final(home="Hawai'i", away="Stanford",
                                 hs=27, as_=37)])
    assert out["games_scored"] == 1, out["games_unmatched"]


def test_two_schools_it_cannot_place_still_have_to_spell_the_same():
    # The fallback keeps early season FCS opponents scoreable. It must not
    # become a way for 2 different schools to match.
    slate = {"slate": [game(matchup="Ghost @ Nowhere U",
                            home="Nowhere U", away="Ghost")]}
    assert R.review(slate, [final(home="Ohio State", away="Ghost")])["games_scored"] == 0
    assert R.review(slate, [final(home="Nowhere U", away="Ghost")])["games_scored"] == 1


def test_the_mascot_map_still_wins_over_the_fallback():
    assert R.key_for("Ohio") != R.key_for("Ohio State")
    assert R.key_for("Hawai'i") == R.key_for("Hawaii Rainbow Warriors")


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


def test_it_writes_only_its_own_files():
    # Its review and its archive, and nothing else. The ledger in
    # particular must stay out of reach.
    src = (ROOT / "scripts" / "review_week.py").read_text()
    saves = [ln for ln in src.splitlines() if "_save(" in ln]
    assert saves
    for ln in saves:
        assert "REVIEW_FILE" in ln or "ARCHIVE_FILE" in ln, ln


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


# ---------------------------------------------------------------------
# The file has to actually be written
#
# grade_results fetched the finals, used them in memory and dropped them,
# so data/results.json sat at {} from the first day. Every consumer read
# an empty file and reported nothing wrong. This scored 0 of 39 games on
# 30 August for exactly that reason and looked like it had run fine.
# ---------------------------------------------------------------------

def test_grading_persists_what_it_fetched():
    src = (ROOT / "scripts" / "grade_results.py").read_text()
    assert 'results.json' in src, "the finals must be written down"
    assert "fetched.extend(games)" in src


def test_the_review_reads_what_grading_writes():
    grading = (ROOT / "scripts" / "grade_results.py").read_text()
    review = (ROOT / "scripts" / "review_week.py").read_text()
    assert 'results.json' in grading and 'results.json' in review
    # And on the same shape, or the reader silently sees nothing.
    assert '"games": fetched' in grading
    assert '.get("games", [])' in review


def test_a_finished_game_still_pending_is_annotated():
    src = (ROOT / "scripts" / "grade_results.py").read_text()
    assert "::warning::" in src
    assert "still pending well" in src
    # 6 hours, so a long game or a slow feed does not cry wolf.
    assert "hours > 6" in src


def test_an_empty_results_file_is_not_reported_as_a_clean_review():
    out = R.review({"slate": [game()]}, [])
    assert out["games_scored"] == 0
    assert out["games_unmatched"], "an unscored game must be named"


def test_canonical_is_given_the_school_list_it_needs():
    """
    canonical() without the known set returns None for most of the board,
    including "Colorado State Rams", and the fallback then keeps the
    mascot in the key while CFBD does not. Every other consumer in this
    repo passes it. The 2 that did not both failed silently.
    """
    assert R.key_for("Colorado State Rams") == R.key_for("Colorado State")
    assert R.key_for("Hawaii Rainbow Warriors") == R.key_for("Hawai'i")
    assert R.key_for("NC State Wolfpack") == R.key_for("NC State")
    assert R.key_for("Ohio") != R.key_for("Ohio State")


# ---------------------------------------------------------------------
# The slate forgets
#
# make_slate rebuilds daily from the games still to come, so a game drops
# off the moment it kicks. Reviewing the current slate against last
# weekend's finals compares next week's fixtures to last week's results
# and matches nothing. It scored 0 of 39 and reported a clean run.
# ---------------------------------------------------------------------

def test_a_game_is_archived_the_first_time_the_model_prices_it():
    slate = {"season": 2026, "week": 1, "slate": [
        {**game(), "event_id": "e1", "kickoff": "2026-08-29T19:45:00Z"}]}
    a = archive_of(slate)
    assert "e1" in a["games"]
    assert a["games"]["e1"]["model"]["projected_spread"] == -7.0


def test_the_first_number_stands():
    """
    What was believed going in, not a number tuned as the week went on.
    """
    slate = {"season": 2026, "week": 1, "slate": [
        {**game(spread=-7.0), "event_id": "e1"}]}
    first = archive_of(slate)
    later = {"season": 2026, "week": 1, "slate": [
        {**game(spread=-21.0), "event_id": "e1"}]}
    second = R.archive(later, first)
    assert second["games"]["e1"]["model"]["projected_spread"] == -7.0


def test_a_played_game_is_still_reviewable_after_it_leaves_the_slate():
    played = {"season": 2026, "week": 1, "slate": [
        {**game(matchup="Stanford @ Hawaii Rainbow Warriors",
                home="Hawaii Rainbow Warriors", away="Stanford", spread=-7.0),
         "event_id": "e1"}]}
    arch = archive_of(played)
    # Next build drops it and carries only an upcoming fixture.
    upcoming = {"season": 2026, "week": 1, "slate": [
        {**game(matchup="A @ B", home="B", away="A"), "event_id": "e2"}]}
    out = R.review(upcoming, [final(home="Hawai'i", away="Stanford",
                                    hs=27, as_=37)], arch, week=1)
    assert out["games_scored"] == 1, out["games_unmatched"]


def test_the_archive_does_not_drag_in_another_week():
    arch = {"games": {"old": {
        "season": 2026, "week": 1, "matchup": "X @ Y",
        "home_team": "Y", "away_team": "X",
        "model": {"projected_spread": -3.0, "projected_total": 50.0}}}}
    out = R.review({"slate": []}, [], arch, week=2)
    assert out["games_scored"] == 0
    assert out["games_unmatched"] == []


def archive_of(slate):
    return R.archive(slate, None)
