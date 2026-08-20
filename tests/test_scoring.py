"""
The grading math, against hand worked cases.

Every assertion here came out of scripts/selftest.py unchanged. A wrong
grader is worse than no grader, because it teaches the handicapper the
wrong lesson and the lesson persists in memory.json for the rest of the
season.
"""

from __future__ import annotations

import pytest

from lib.scoring import (
    breakeven, calibration_table, clv_cents, clv_points, grade_pick,
    grade_spread, grade_total, half_points, payout, summarize,
)

TOL = 1e-6


# ------------------------------------------------------------- spreads

@pytest.mark.parametrize("label,args,want", [
    ("fav covers", (31, 20, -7.5), "win"),
    ("fav fails", (24, 20, -7.5), "loss"),
    ("fav exact push", (27, 20, -7.0), "push"),
    ("dog covers on loss", (20, 24, 7.5), "win"),
    ("dog covers outright", (25, 24, 7.5), "win"),
    ("dog buried", (3, 45, 14.0), "loss"),
    ("pick em tie", (21, 21, 0.0), "push"),
])
def test_grade_spread(label, args, want):
    assert grade_spread(*args) == want, label


# -------------------------------------------------------------- totals

@pytest.mark.parametrize("label,args,want", [
    ("over hits", (31, 28, 52.5, "Over"), "win"),
    ("over misses", (10, 7, 52.5, "Over"), "loss"),
    ("under hits", (10, 7, 52.5, "Under"), "win"),
    ("total push", (28, 24, 52.0, "Under"), "push"),
    ("over push", (28, 24, 52.0, "over"), "push"),
])
def test_grade_total(label, args, want):
    assert grade_total(*args) == want, label


# -------------------------------------------------------------- halves

@pytest.mark.parametrize("label,args,want", [
    ("h1 from quarters", ([7, 10, 3, 14], 1), 17),
    ("h2 from quarters", ([7, 10, 3, 14], 2), 17),
    ("h2 includes OT", ([7, 10, 3, 14, 6], 2), 23),
    ("h1 ignores OT", ([7, 10, 3, 14, 6], 1), 17),
    ("h1 needs two quarters", ([7], 1), None),
    ("no line scores", (None, 1), None),
])
def test_half_points(label, args, want):
    assert half_points(*args) == want, label


# ----------------------------------------------------------- grade_pick

GAME = {
    "home_team": "Ohio State", "away_team": "Michigan",
    "home_score": 34, "away_score": 27,
    "home_line_scores": [7, 14, 6, 7],   # 1H 21, 2H 13
    "away_line_scores": [3, 7, 10, 7],   # 1H 10, 2H 17
    "completed": True,
}


@pytest.mark.parametrize("label,pick,game,want", [
    ("full spread home",
     {"market": "spread", "period": "full", "side": "Ohio State", "line": -6.5},
     GAME, "win"),
    ("full spread away",
     {"market": "spread", "period": "full", "side": "Michigan", "line": 6.5},
     GAME, "loss"),
    # 21-10 at the half, so a margin of 11
    ("h1 spread home lays 7",
     {"market": "spread", "period": "h1", "side": "Ohio State", "line": -7.0},
     GAME, "win"),
    ("h1 spread home lays 14",
     {"market": "spread", "period": "h1", "side": "Ohio State", "line": -14.0},
     GAME, "loss"),
    ("h1 spread exact push",
     {"market": "spread", "period": "h1", "side": "Ohio State", "line": -11.0},
     GAME, "push"),
    # 17-13 to the away side after the break
    ("h2 spread away",
     {"market": "spread", "period": "h2", "side": "Michigan", "line": 3.5},
     GAME, "win"),
    ("h1 total over",
     {"market": "total", "period": "h1", "side": "Over", "line": 28.5},
     GAME, "win"),
    ("h1 total under",
     {"market": "total", "period": "h1", "side": "Under", "line": 28.5},
     GAME, "loss"),
    ("full total under",
     {"market": "total", "period": "full", "side": "Under", "line": 62.5},
     GAME, "win"),
    ("moneyline home",
     {"market": "moneyline", "period": "full", "side": "Ohio State", "line": None},
     GAME, "win"),
    # A name that does not match the game voids rather than guessing.
    ("unknown team voids",
     {"market": "spread", "period": "full", "side": "Purdue", "line": -3.0},
     GAME, "void"),
    ("incomplete game pends",
     {"market": "spread", "period": "full", "side": "Ohio State", "line": -6.5},
     {**GAME, "completed": False}, "pending"),
    ("completed but no quarters pends",
     {"market": "spread", "period": "h1", "side": "Ohio State", "line": -7.0},
     {**GAME, "home_line_scores": None, "away_line_scores": None}, "pending"),
])
def test_grade_pick(label, pick, game, want):
    assert grade_pick(pick, game) == want, label


# ------------------------------------------------------------- payouts

@pytest.mark.parametrize("label,args,want", [
    ("win at -110", (1.0, -110, "win"), 100 / 110),
    ("win at +150", (1.0, 150, "win"), 1.5),
    ("2u win at -110", (2.0, -110, "win"), 200 / 110),
    ("loss", (1.5, -110, "loss"), -1.5),
    ("push", (1.5, -110, "push"), 0.0),
])
def test_payout(label, args, want):
    assert payout(*args) == pytest.approx(want, abs=TOL), label


@pytest.mark.parametrize("label,price,want", [
    ("breakeven -110", -110, 110 / 210),
    ("breakeven +100", 100, 0.5),
])
def test_breakeven(label, price, want):
    assert breakeven(price) == pytest.approx(want, abs=TOL), label


# ----------------------------------------------------------------- CLV

@pytest.mark.parametrize("label,args,want", [
    ("spread CLV good", ("spread", "Alabama", -7.5, -9.5), 2.0),
    ("spread CLV bad", ("spread", "Alabama", -7.5, -6.5), -1.0),
    ("dog CLV good", ("spread", "Vandy", 7.5, 6.5), 1.0),
    ("over CLV good", ("total", "Over", 52.5, 55.5), 3.0),
    ("over CLV bad", ("total", "Over", 52.5, 50.5), -2.0),
    ("under CLV good", ("total", "Under", 52.5, 49.5), 3.0),
])
def test_clv_points(label, args, want):
    assert clv_points(*args) == pytest.approx(want, abs=TOL), label


def test_clv_needs_both_numbers():
    assert clv_points("spread", "Alabama", None, -9.5) is None


def test_clv_cents():
    assert clv_cents(-105, -115) == 10


# ------------------------------------------------------------- rollups

SAMPLE = [
    {"result": "win", "units": 1.0, "units_net": 100 / 110, "confidence": 8.2,
     "clv_points": 1.0, "market": "spread"},
    {"result": "loss", "units": 2.0, "units_net": -2.0, "confidence": 9.1,
     "clv_points": -0.5, "market": "spread"},
    {"result": "push", "units": 1.0, "units_net": 0.0, "confidence": 8.6,
     "clv_points": 0.0, "market": "total"},
    {"result": "pending", "units": 1.0, "units_net": 0.0, "confidence": 8.0},
]


@pytest.fixture(scope="module")
def rollup():
    return summarize(SAMPLE)


def test_summary_counts_pending_out(rollup):
    assert rollup["picks"] == 3


def test_summary_win_loss_push(rollup):
    assert (rollup["wins"], rollup["losses"], rollup["pushes"]) == (1, 1, 1)


def test_push_excluded_from_risked(rollup):
    assert rollup["risked"] == 3.0


def test_net_units(rollup):
    assert rollup["units"] == pytest.approx(round(100 / 110 - 2.0, 2), abs=0.02)


def test_win_pct_off_decided_only(rollup):
    assert rollup["win_pct"] == 50.0


def test_avg_clv(rollup):
    assert rollup["avg_clv"] == pytest.approx(0.17, abs=0.01)


def test_beat_close_share(rollup):
    assert rollup["beat_close_pct"] == 33.3


def test_calibration_buckets():
    cal = calibration_table(SAMPLE)
    assert sorted(r["bucket"] for r in cal) == ["8.0-8.4", "8.5-8.9", "9.0-9.4"]
