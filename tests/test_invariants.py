"""
Things that must be true, as opposed to things that must be shaped right.

The suite that shipped the halving bug had 1,787 tests. Every one of them
checked plumbing: does this return a dict, does that raise, is the
workflow permission set correct. Not one asked whether the model agreed
with itself, and the spread said a team won by 56 while the total implied
28 through every green run for as long as the file existed.

These are the assertions that would have caught it, generalised. They ask
whether the numbers describe football, whether the parts of the system
agree, and whether the ordering the site sells actually holds.
"""

from __future__ import annotations

import json
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

from lib import model as M  # noqa: E402
from lib.model import suggested_confidence  # noqa: E402

SLATE = ROOT / "data" / "slate.json"


def slate_games() -> list[dict]:
    if not SLATE.exists():
        return []
    return json.loads(SLATE.read_text()).get("slate", [])


def projections():
    """Every game on the slate, reprojected with the current model."""
    for g in slate_games():
        m = g.get("model") or {}
        inp = m.get("inputs") or {}
        home, away = m.get("home_team"), m.get("away_team")
        if not home or not away:
            continue
        book = {home: inp.get("home_components") or {},
                away: inp.get("away_components") or {}}
        yield g, M.project_game(home, away, book,
                                neutral=bool(g.get("neutral_site")))


# --------------------------------------------------- does it describe football

def test_no_projected_score_is_negative():
    for g, p in projections():
        for side in ("home_points", "away_points"):
            v = p.inputs.get(side)
            if v is not None:
                assert v >= 0, f"{g['matchup']} {side} {v}"


def test_projected_totals_land_in_a_range_football_produces():
    # College games have gone under 10 and over 100, but a model that
    # routinely projects outside 20 to 90 is not modelling this sport.
    for g, p in projections():
        if p.projected_total is None:
            continue
        assert 20 <= p.projected_total <= 90, \
            f"{g['matchup']} total {p.projected_total}"


def test_no_projected_spread_exceeds_what_the_sport_does():
    for g, p in projections():
        if p.projected_spread is None:
            continue
        assert abs(p.projected_spread) <= 70, \
            f"{g['matchup']} spread {p.projected_spread}"


def test_the_points_margin_is_the_spread_margin():
    """
    The one invariant the halving broke, stated where it cannot be
    skipped. Two unequal teams, no clamping, no tolerance: the margin the
    points imply is the margin the spread states. Averaging offense and
    defense produces exactly half of this and fails here immediately.

    Equal teams cannot catch it. Halving zero is zero, so a symmetric
    fixture passes either way, which is why the sides are lopsided.
    """
    book = {"Home": {"sp": 14.0, "sp_off": 34.0, "sp_def": 20.0},
            "Away": {"sp": 6.0, "sp_off": 24.0, "sp_def": 18.0}}
    p = M.project_game("Home", "Away", book, neutral=True, calibrate=False)
    from_points = p.inputs["home_points"] - p.inputs["away_points"]
    assert from_points == pytest.approx(-p.projected_spread, abs=0.15), (
        f"points say {from_points}, spread says {-p.projected_spread}")


def test_the_gate_is_an_exception_and_not_the_rule():
    """
    A gate that holds most of the board is not catching bad games, it is
    reporting a bad model, and it hides that by looking like caution.

    This is the assertion that was missing. The first version of this
    file skipped every game over tolerance, so when the halving put the
    whole board over tolerance, it passed with nothing left to check.
    """
    gaps = [p.coherence_gap for _, p in projections()
            if p.coherence_gap is not None]
    if len(gaps) < 10:
        pytest.skip("no board to measure")
    bad = sum(1 for g in gaps if g > M.COHERENCE_TOLERANCE)
    assert bad / len(gaps) <= 0.2, (
        f"{bad} of {len(gaps)} games disagree with themselves. That is the "
        f"model, not the slate.")


def test_the_two_halves_agree_on_every_game_that_publishes():
    """
    Checks the published numbers against the gate, rounding the way the
    gate rounds.

    The gate rounds its gap to 2 decimals before comparing and this did
    not, so a game whose gap is exactly the tolerance cleared the gate and
    then failed here on a float a hair above 4.0. Hawai'i and UNLV landed
    on that line and took the daily publish down twice. Rounding the
    points at the source never touched it, because the game was not off by
    a rounding error, it was on the line exactly.

    Two values compared against one threshold, for the third time in two
    days. There is one rounding rule here now and it is the gate's.
    """
    for g, p in projections():
        if p.coherence_gap is None or p.coherence_gap > M.COHERENCE_TOLERANCE:
            continue
        hp, ap = p.inputs["home_points"], p.inputs["away_points"]
        assert hp is not None and ap is not None, g["matchup"]
        gap = round(abs((hp - ap) - (-p.projected_spread)), 2)
        assert gap <= M.COHERENCE_TOLERANCE, (
            f"{g['matchup']}: page shows a {gap} point disagreement but the "
            f"gate let it publish")


# ------------------------------------------------------- do the parts agree

def test_home_advantage_moves_the_number_toward_the_home_team():
    book = {"H": {"sp": 10.0, "sp_off": 30.0, "sp_def": 20.0},
            "A": {"sp": 10.0, "sp_off": 30.0, "sp_def": 20.0}}
    home = M.project_game("H", "A", book, neutral=False, calibrate=False)
    neutral = M.project_game("H", "A", book, neutral=True, calibrate=False)
    # Negative means home lays, so home field must not raise the number.
    assert home.projected_spread < neutral.projected_spread


def test_two_equal_teams_on_a_neutral_field_are_a_pick_em():
    book = {"H": {"sp": 4.0, "sp_off": 28.0, "sp_def": 24.0},
            "A": {"sp": 4.0, "sp_off": 28.0, "sp_def": 24.0}}
    p = M.project_game("H", "A", book, neutral=True, calibrate=False)
    assert p.projected_spread == pytest.approx(0.0, abs=0.05)


def test_a_better_team_is_favoured_over_a_worse_one():
    book = {"Good": {"sp": 20.0, "sp_off": 38.0, "sp_def": 18.0},
            "Bad": {"sp": -20.0, "sp_off": 17.0, "sp_def": 37.0}}
    p = M.project_game("Bad", "Good", book, neutral=True, calibrate=False)
    # Home is the worse team here, so the spread must be positive.
    assert p.projected_spread > 0


def test_swapping_the_sides_mirrors_the_number():
    book = {"A": {"sp": 12.0, "sp_off": 33.0, "sp_def": 21.0},
            "B": {"sp": -4.0, "sp_off": 24.0, "sp_def": 28.0}}
    one = M.project_game("A", "B", book, neutral=True, calibrate=False)
    two = M.project_game("B", "A", book, neutral=True, calibrate=False)
    assert one.projected_spread == pytest.approx(-two.projected_spread,
                                                 abs=0.05)
    assert one.projected_total == pytest.approx(two.projected_total, abs=0.05)


# ------------------------------------------------------ does the scale hold

def test_a_wider_gap_never_scores_lower_than_a_narrower_one():
    """
    The site sells confidence as a ranking. If a 7 point disagreement can
    score below a 4 point one on the same market, the ranking is decor.
    """
    last = -1.0
    for pts in (1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 20.0):
        c = suggested_confidence(pts, "spread")
        assert c >= last, f"{pts} points scored {c}, below the previous"
        last = c


def test_confidence_never_reaches_the_publish_line_on_its_own():
    # The ratings model must never be able to publish a pick by itself.
    for pts in range(0, 60):
        for market in ("spread", "total"):
            c = suggested_confidence(float(pts), market)
            if c is not None:
                assert c <= 7.5, f"{pts} on {market} produced {c}"


def test_the_same_gap_scores_differently_on_a_total_than_a_spread():
    # Totals swing wider, so 6 points on a total must not be worth what 6
    # points on a spread is. If these ever match, the scaling is dead.
    spread = suggested_confidence(6.0, "spread")
    total = suggested_confidence(6.0, "total")
    if spread is not None and total is not None:
        assert spread != total


# ------------------------------------------------- does the board hold up

def test_no_game_reaches_the_board_arguing_with_itself():
    import build_site as B
    for r in B.board_rows()["rows"]:
        assert not r.get("incoherent"), r["matchup"]


def test_the_card_never_carries_one_game_twice():
    import build_site as B
    running = (B.build_payload().get("running") or {}).get("card") or []
    games = [(c.get("home_team"), c.get("away_team")) for c in running]
    assert len(games) == len(set(games)), games


def test_the_gate_measures_the_numbers_the_page_prints():
    """
    The gate decided on the unrounded projection while the page published
    the rounded one, so the two held different values for the same
    quantity and were compared against the same 4.0 threshold. A game
    within a tenth of the line passed the gate and then failed the check
    that its published halves agree, which took the daily publish down on
    26 August over Hawai'i and UNLV.

    Rounded once at the source now, so there is one number.
    """
    for g, p in projections():
        if p.coherence_gap is None:
            continue
        hp, ap = p.inputs["home_points"], p.inputs["away_points"]
        from_published = abs((hp - ap) - (-p.projected_spread))
        assert abs(from_published - p.coherence_gap) < 1e-9, g["matchup"]


def test_published_points_are_already_rounded():
    # If inputs carried more precision than the page shows, the gate and
    # the display would drift apart again the moment one of them rounded.
    for _, p in projections():
        for side in ("home_points", "away_points"):
            v = p.inputs.get(side)
            if v is not None:
                assert round(v, 1) == v, f"{side} is {v}"
