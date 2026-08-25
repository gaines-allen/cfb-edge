"""
The backtest, and specifically its arithmetic.

Everything on this site assumes disagreeing with the market predicts
something, and this is the only thing that checks. Which makes a sign
error here worse than no backtest at all: it would report a confident
edge that does not exist, and the whole card would be built on it.

So the grading is a pure function and it is tested against hand worked
games where the right answer is obvious.
"""

from __future__ import annotations

import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import backtest as BT  # noqa: E402


# ------------------------------------------------------------- spreads
# Convention: spread is negative when the home team lays. A model number
# below the close means the model wants more home than the book asks.

def test_home_lean_wins_when_home_covers():
    # Book has home -7. Model says home -10, so take home. Home wins 21-3,
    # a 18 point margin, which covers 7.
    assert BT.grade_against_close("spread", -10.0, -7.0, 21, 3) == "win"


def test_home_lean_loses_when_home_wins_but_does_not_cover():
    # Same lean. Home wins 21-17, a 4 point margin, short of 7.
    assert BT.grade_against_close("spread", -10.0, -7.0, 21, 17) == "loss"


def test_away_lean_wins_when_the_favourite_wins_small():
    # Book has home -7. Model says home -3, so take away +7. Home wins by
    # 4, so the away side covers.
    assert BT.grade_against_close("spread", -3.0, -7.0, 24, 20) == "win"


def test_away_lean_wins_when_the_underdog_wins_outright():
    assert BT.grade_against_close("spread", -3.0, -7.0, 17, 24) == "win"


def test_an_exact_landing_is_a_push():
    assert BT.grade_against_close("spread", -10.0, -7.0, 21, 14) == "push"


def test_agreeing_with_the_book_is_not_an_opinion():
    assert BT.grade_against_close("spread", -7.0, -7.0, 21, 3) is None


def test_the_home_dog_case_keeps_its_signs():
    # Book has home +3, so the away team is favoured. Model says home
    # +6, which is above the close, so the lean is away.
    assert BT.grade_against_close("spread", 6.0, 3.0, 20, 24) == "win"
    assert BT.grade_against_close("spread", 6.0, 3.0, 24, 20) == "loss"


@pytest.mark.parametrize("home,away", [(31, 10), (10, 31), (17, 17)])
def test_a_lean_and_its_mirror_never_both_win(home, away):
    # The same game graded from both sides has to disagree. If both win,
    # the sign handling is broken in a way that manufactures edge.
    take_home = BT.grade_against_close("spread", -10.0, -7.0, home, away)
    take_away = BT.grade_against_close("spread", -3.0, -7.0, home, away)
    assert not (take_home == "win" and take_away == "win")
    assert not (take_home == "loss" and take_away == "loss")


# -------------------------------------------------------------- totals

def test_over_lean_wins_when_the_game_goes_over():
    assert BT.grade_against_close("total", 58.0, 52.5, 35, 28) == "win"


def test_over_lean_loses_when_the_game_stays_under():
    assert BT.grade_against_close("total", 58.0, 52.5, 17, 10) == "loss"


def test_under_lean_wins_when_the_game_stays_under():
    assert BT.grade_against_close("total", 44.0, 52.5, 17, 10) == "win"


def test_a_total_landing_on_the_number_is_a_push():
    assert BT.grade_against_close("total", 58.0, 52.0, 35, 17) == "push"


# ------------------------------------------------------------- buckets

@pytest.mark.parametrize("edge,label", [
    (0.5, "0 to 1.5"), (-0.5, "0 to 1.5"), (2.0, "1.5 to 3"),
    (4.0, "3 to 4.5"), (-5.0, "4.5 to 6"), (7.0, "6 to 9"), (30.0, "9+"),
])
def test_edges_bucket_by_size_and_ignore_direction(edge, label):
    assert BT.bucket_for(edge) == label


# ------------------------------------------------------ picking a close

def test_the_preferred_book_wins_and_the_order_is_fixed():
    lines = [{"provider": "Bovada", "spread": -6.5},
             {"provider": "DraftKings", "spread": -7.0}]
    assert BT.pick_close(lines, "spread") == (-7.0, "DraftKings")
    # Same set, other order, same answer. Cherry picking the friendliest
    # close is how a backtest flatters itself.
    assert BT.pick_close(list(reversed(lines)), "spread") == (-7.0,
                                                              "DraftKings")


def test_an_unpriced_game_yields_nothing():
    assert BT.pick_close([], "spread") == (None, None)
    assert BT.pick_close([{"provider": "X", "spread": None}],
                         "spread") == (None, None)


# ------------------------------------------------------- the conclusion

def test_a_flat_result_reports_no_gradient():
    rows = ([{"season": 2023, "market": "spread", "edge": 0.5,
              "result": "win" if i % 2 else "loss"} for i in range(80)]
            + [{"season": 2023, "market": "spread", "edge": 8.0,
                "result": "win" if i % 2 else "loss"} for i in range(80)])
    out = BT.report(rows)
    assert out["overall"]["verdict"] in ("no_signal", "below_breakeven")
    assert out["gradient"]["spread_between_ends"] == 0.0


def test_a_real_gradient_is_visible():
    rows = ([{"season": 2023, "market": "spread", "edge": 0.5,
              "result": "win" if i < 40 else "loss"} for i in range(100)]
            + [{"season": 2023, "market": "spread", "edge": 8.0,
                "result": "win" if i < 65 else "loss"} for i in range(100)])
    out = BT.report(rows)
    assert out["gradient"]["narrowest"] == 40.0
    assert out["gradient"]["widest"] == 65.0
    assert out["gradient"]["spread_between_ends"] == 25.0


def test_a_thin_bucket_is_left_out_of_the_gradient():
    # 12 games cannot vote on whether the scale works.
    rows = ([{"season": 2023, "market": "spread", "edge": 0.5,
              "result": "win"} for _ in range(12)]
            + [{"season": 2023, "market": "spread", "edge": 8.0,
                "result": "win" if i < 60 else "loss"} for i in range(100)])
    out = BT.report(rows)
    assert out["gradient"]["buckets_with_enough_sample"] == 1
    assert out["gradient"]["spread_between_ends"] is None
