"""
The ratings model, and the 2 properties that must not move.

The first is that suggested_confidence never reaches 8.0. It caps at 7.5
deliberately, below the publish threshold, so the ratings model can never
justify a published pick on its own. Everything above 8 has to be earned
with research the model cannot do.

The second is that SP+ offense and defense are points scored and allowed,
not differentials. Treating them as differentials produced 83 point totals
against a 48.5 market and made every candidate an Over.
"""

from __future__ import annotations

import math

import pytest

from lib.model import (
    DEFAULT_HFA, NEUTRAL_HFA, RATING_WEIGHTS, blended_rating,
    build_rating_book, edge_vs_market, project_game, suggested_confidence,
    z_score,
)
from lib.store import LIVE_THRESHOLD

# Measured against the live board and recorded in data/model_calibration.json.
MEASURED = {"totals": {"bias": -0.95, "sigma": 3.25},
            "spreads": {"bias": 0.58, "sigma": 2.77}}


# ------------------------------------------------- the 7.5 cap

EDGES = [0.0, 0.1, 0.5, 1.0, 2.0, 3.0, 4.5, 6.0, 8.0, 10.0, 14.0, 21.0, 40.0]
SIGMAS = [0.1, 0.5, 1.0, 2.77, 3.25, 6.0]


@pytest.mark.parametrize("edge", EDGES + [-e for e in EDGES])
@pytest.mark.parametrize("market", ["spread", "total", "moneyline"])
@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("sigma", SIGMAS)
def test_confidence_never_reaches_the_publish_threshold(edge, market,
                                                        complete, sigma):
    """
    A sweep rather than a spot check, because the failure mode is a tiny
    sigma. z is edge over sigma, so a 0.1 sigma turns a 4 point gap into 40
    standard deviations, and only the clamp inside the function holds the
    number down.
    """
    cal = {"totals": {"bias": 0.0, "sigma": sigma},
           "spreads": {"bias": 0.0, "sigma": sigma}}
    got = suggested_confidence(edge, market, ratings_complete=complete,
                               calibration=cal)
    assert got < 8.0, f"edge {edge} on {market} at sigma {sigma} returned {got}"
    assert got <= 7.5


def test_confidence_caps_at_exactly_7_5():
    """The ceiling is 7.5, and a huge edge should land on it rather than under."""
    cal = {"spreads": {"bias": 0.0, "sigma": 0.5}}
    assert suggested_confidence(40.0, "spread", calibration=cal) == 7.5


def test_cap_sits_below_the_live_threshold():
    assert 7.5 < LIVE_THRESHOLD == 8.0


@pytest.mark.parametrize("edge", EDGES)
@pytest.mark.parametrize("market", ["spread", "total"])
def test_uncalibrated_confidence_caps_lower(edge, market):
    """
    With no calibration there is no way to know how unusual a gap is, so the
    fallback scale caps at 6.5 rather than 7.5.
    """
    got = suggested_confidence(edge, market, calibration={})
    assert got <= 6.5


def test_no_edge_is_zero_confidence():
    assert suggested_confidence(None, "spread", calibration=MEASURED) == 0.0


def test_incomplete_ratings_lower_confidence():
    a = suggested_confidence(6.0, "spread", ratings_complete=True,
                             calibration=MEASURED)
    b = suggested_confidence(6.0, "spread", ratings_complete=False,
                             calibration=MEASURED)
    assert b < a


def test_six_point_total_edge_is_under_two_sigma():
    """
    The number that reframes everything. A six point edge on a total sounds
    enormous and is 1.85 sigma against the measured dispersion.
    """
    z = z_score(6.0, "total", MEASURED)
    assert z == pytest.approx(1.85, abs=0.01)
    assert suggested_confidence(6.0, "total", calibration=MEASURED) < 7.0


# ------------------------------------------- SP+ is points, not a gap

def sp_row(team, off, deff, overall=0.0):
    return {"team": team, "rating": overall,
            "offense": {"rating": off}, "defense": {"rating": deff}}


def test_expected_points_average_own_offense_and_opponent_defense():
    """
    Each side's expected points is the average of its own offense and the
    opponent's defense. The bug was treating both as differentials and
    adding them to a league average, which produced 83 point totals.
    """
    book = build_rating_book(
        [sp_row("Home", 34.0, 20.0), sp_row("Away", 24.0, 18.0)], [], [], [])
    proj = project_game("Home", "Away", book, calibrate=False)
    # Home scores (34 + 18) / 2 = 26. Away scores (24 + 20) / 2 = 22.
    assert proj.projected_total == pytest.approx(48.0, abs=0.05)
    assert proj.inputs["home_points"] == pytest.approx(26.0, abs=0.05)
    assert proj.inputs["away_points"] == pytest.approx(22.0, abs=0.05)


def test_projected_total_lands_in_a_believable_range():
    """
    A real FBS total sits between 30 and 80. The differential bug put it at
    83 against a 48.5 market, so this is the shape of that regression.
    """
    book = build_rating_book(
        [sp_row("Home", 31.0, 24.0), sp_row("Away", 28.0, 26.0)], [], [], [])
    proj = project_game("Home", "Away", book, calibrate=False)
    assert 30.0 <= proj.projected_total <= 80.0


def test_h1_total_is_47_percent_of_the_full_game():
    book = build_rating_book(
        [sp_row("Home", 30.0, 24.0), sp_row("Away", 28.0, 26.0)], [], [], [])
    proj = project_game("Home", "Away", book, calibrate=False)
    assert proj.projected_h1_total == pytest.approx(
        round(proj.projected_total * 0.47, 1), abs=0.05)


# ------------------------------------------------------------- spread

def test_home_field_advantage_applies_and_neutral_removes_it():
    book = build_rating_book([sp_row("Home", 30.0, 24.0, 10.0),
                              sp_row("Away", 30.0, 24.0, 10.0)], [], [], [])
    home_game = project_game("Home", "Away", book, calibrate=False)
    neutral = project_game("Home", "Away", book, neutral=True, calibrate=False)
    assert home_game.hfa_applied == DEFAULT_HFA
    assert neutral.hfa_applied == NEUTRAL_HFA
    # Equal teams, so the whole number is the home field.
    assert home_game.projected_spread == pytest.approx(-DEFAULT_HFA, abs=0.05)
    assert neutral.projected_spread == pytest.approx(0.0, abs=0.05)


def test_negative_spread_means_home_is_favoured():
    book = build_rating_book([sp_row("Home", 38.0, 15.0, 25.0),
                              sp_row("Away", 20.0, 30.0, -5.0)], [], [], [])
    proj = project_game("Home", "Away", book, calibrate=False)
    assert proj.projected_spread < 0


def test_missing_ratings_give_no_projection_rather_than_a_guess():
    book = build_rating_book([sp_row("Home", 30.0, 24.0, 8.0)], [], [], [])
    proj = project_game("Home", "Nobody", book, calibrate=False)
    assert proj.projected_spread is None
    assert proj.projected_total is None


def test_empty_rating_book_raises():
    """
    All 4 sources empty used to build an empty book, project every game as
    None, and drop the whole slate without saying why.
    """
    with pytest.raises(ValueError, match="rating book is empty"):
        build_rating_book([], [], [], [])


def test_elo_is_scaled_onto_points():
    """Elo is a rating, not points, so it has to be centred and divided."""
    book = build_rating_book([], [], [], [{"team": "T", "elo": 1500}])
    assert blended_rating(book["T"]) == pytest.approx(0.0, abs=1e-6)
    book2 = build_rating_book([], [], [], [{"team": "T", "elo": 1750}])
    assert blended_rating(book2["T"]) == pytest.approx(10.0, abs=1e-6)


def test_blended_rating_renormalizes_over_what_is_present():
    """
    SRS and FPI have nothing to say before a season is played, so the
    weights have to renormalize rather than treating a missing input as 0.
    """
    only_sp = blended_rating({"sp": 20.0})
    assert only_sp == pytest.approx(20.0, abs=1e-6)
    assert blended_rating({}) is None


def test_weights_sum_to_one():
    assert sum(RATING_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------------- edges

@pytest.mark.parametrize("proj,market,want", [
    (-9.4, -6.5, -2.9),
    (52.0, 48.5, 3.5),
    (None, -6.5, None),
    (-9.4, None, None),
])
def test_edge_vs_market(proj, market, want):
    got = edge_vs_market(proj, market)
    if want is None:
        assert got is None
    else:
        assert got == pytest.approx(want, abs=0.01)
