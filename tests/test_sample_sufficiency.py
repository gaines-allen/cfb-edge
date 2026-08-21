"""
Whether a record says anything, or is noise wearing a percentage.

The card is capped at 6 a week and often produces 2, so a 14 week season
yields somewhere between 28 and 84 graded picks spread across 16 factor
tags. Most cells will hold 3 or 4 entries. The handicapper is required to
read that file before rating anything, so a bare record invites it to avoid
first half unders because they are 1 and 4, which is learning noise and
getting worse while appearing to improve.

A record is evidence only when its 95% interval sits entirely on one side of
breakeven. Everything else is labelled and must not be acted on.
"""

from __future__ import annotations

import pytest

from lib.scoring import (
    annotate, breakeven, calibration_table, sample_verdict, wilson_interval,
)

BREAKEVEN_110 = 100.0 * breakeven(-110)


def test_breakeven_at_the_standard_price():
    assert BREAKEVEN_110 == pytest.approx(52.38, abs=0.01)


# ------------------------------------------------------- the interval

def test_no_decided_picks_gives_no_interval():
    assert wilson_interval(0, 0) is None


def test_the_interval_stays_inside_zero_and_one_hundred():
    """
    The plain normal interval runs past 100% at 3 wins from 4, which is
    worse than saying nothing. Wilson is used precisely because it does not.
    """
    for wins, decided in [(4, 4), (0, 4), (3, 4), (1, 3)]:
        lo, hi = wilson_interval(wins, decided)
        assert 0.0 <= lo <= hi <= 100.0


def test_the_interval_narrows_as_the_sample_grows():
    widths = []
    for n in (10, 50, 200, 1000):
        lo, hi = wilson_interval(int(n * 0.6), n)
        widths.append(hi - lo)
    assert widths == sorted(widths, reverse=True)


# -------------------------------------------------------- the verdict

@pytest.mark.parametrize("wins,decided,expected", [
    (0, 0, "no_data"),
    (3, 1, "no_signal"),      # 75 percent from 4
    (1, 4, "no_signal"),      # the case that would scare a handicapper off
    (6, 4, "no_signal"),
    (30, 20, "no_signal"),
    (60, 40, "no_signal"),    # 60 percent over 100 and still not evidence
    (600, 400, "beats_breakeven"),
])
def test_the_verdict(wins, decided, expected):
    assert sample_verdict(wins, wins + decided)["verdict"] == expected


def test_a_strong_small_sample_is_still_no_signal():
    """
    The whole point. A 75 percent record from 4 picks reads as a hot hand
    and carries no information at all.
    """
    v = sample_verdict(3, 4)
    assert v["verdict"] == "no_signal"
    assert v["interval_95"][0] < BREAKEVEN_110 < v["interval_95"][1]
    assert "Do not act on it" in v["reading"]


def test_a_genuinely_losing_factor_is_eventually_called():
    v = sample_verdict(300, 1000)
    assert v["verdict"] == "below_breakeven"
    assert v["interval_95"][1] < BREAKEVEN_110


def test_the_reading_names_the_breakeven_it_compared_against():
    v = sample_verdict(60, 100)
    assert "52.4" in v["reading"]
    assert v["breakeven_pct"] == pytest.approx(52.4, abs=0.1)


def test_one_season_of_picks_cannot_reach_a_verdict():
    """
    84 graded picks is the ceiling for a full season at 6 a week. Even a 60
    percent season says nothing, which is why nobody should be retuning the
    model off 1 year of results.
    """
    v = sample_verdict(50, 84)
    assert v["verdict"] == "no_signal"


# ------------------------------------------------ attached to the rollups

def sample(result, conf, factor="rating_edge"):
    return {"result": result, "units": 1.0, "units_net": 0.0,
            "confidence": conf, "market": "spread", "live": True,
            "factors": {factor: True}}


def test_calibration_buckets_carry_a_verdict():
    rows = calibration_table([sample("win", 8.2), sample("loss", 8.3),
                              sample("win", 8.1)])
    assert rows
    for r in rows:
        assert r["verdict"] in ("no_data", "no_signal",
                                "beats_breakeven", "below_breakeven")
        assert "reading" in r


def test_a_thin_calibration_bucket_is_marked_no_signal():
    rows = calibration_table([sample("win", 8.2), sample("win", 8.3),
                              sample("loss", 8.1)])
    assert all(r["verdict"] == "no_signal" for r in rows)


def test_annotate_leaves_the_original_counts_alone():
    row = {"wins": 3, "losses": 1, "units": 1.5}
    out = annotate(dict(row))
    assert out["wins"] == 3 and out["losses"] == 1 and out["units"] == 1.5
    assert out["verdict"] == "no_signal"
