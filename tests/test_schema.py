"""
Shape validation against the real payloads.

Two halves. The first proves every validator accepts what the APIs actually
return today, so the suite is not green because the checks are vacuous. The
second lives in test_corruption.py and proves each check bites.
"""

from __future__ import annotations

import json

import pytest

from conftest import FIXTURES, load_fixture
from lib import schema
from lib.schema import ShapeError


# ------------------------------------------- the live shapes all pass

def test_odds_board_validates(odds_board):
    events = schema.validate_odds_board(odds_board)
    assert len(events) > 50, "a full FanDuel board should carry 50 games or more"


def test_odds_scores_validates(odds_scores):
    schema.validate_odds_scores(odds_scores)


def test_espn_teams_validates(espn_page):
    teams = schema.validate_espn_teams(espn_page, expected=100)
    assert len(teams) == 100


def test_cfbd_games_validates(cfbd_games):
    rows = schema.validate_cfbd_games(cfbd_games)
    assert any(g.get("homeLineScores") for g in rows), (
        "quarter scores are the only reason a first half pick can settle"
    )


def test_cfbd_lines_validates(cfbd_lines):
    schema.validate_cfbd_lines(cfbd_lines)


def test_cfbd_calendar_validates(cfbd_calendar):
    schema.validate_cfbd_calendar(cfbd_calendar)


def test_sp_validates(cfbd_sp):
    schema.validate_sp_ratings(cfbd_sp)


def test_fpi_validates(cfbd_fpi):
    schema.validate_fpi_ratings(cfbd_fpi)


def test_srs_validates(cfbd_srs):
    schema.validate_srs_ratings(cfbd_srs)


def test_elo_validates(cfbd_elo):
    schema.validate_elo_ratings(cfbd_elo, week=5)


# ------------------------------------ every captured week still passes

def _weekly(kind: str):
    out = []
    for p in sorted((FIXTURES / "cfbd").glob(f"{kind}_*_w*.json")):
        season, week = p.stem.split("_")[1], int(p.stem.split("_w")[1])
        out.append((p, int(season), week))
    return out


@pytest.mark.parametrize("path,season,week", _weekly("games"),
                         ids=lambda v: str(v) if not hasattr(v, "stem") else v.stem)
def test_every_captured_games_week_validates(path, season, week):
    """
    Phase 2 replays all 45 of these, so a week that fails validation now is
    a week that would quietly score garbage later.
    """
    schema.validate_cfbd_games(json.loads(path.read_text()), min_rows=0)


@pytest.mark.parametrize("path,season,week", _weekly("lines"),
                         ids=lambda v: str(v) if not hasattr(v, "stem") else v.stem)
def test_every_captured_lines_week_validates(path, season, week):
    schema.validate_cfbd_lines(json.loads(path.read_text()), min_rows=0)


@pytest.mark.parametrize("path,season,week", _weekly("elo"),
                         ids=lambda v: str(v) if not hasattr(v, "stem") else v.stem)
def test_every_captured_elo_week_validates(path, season, week):
    schema.validate_elo_ratings(json.loads(path.read_text()), week=week)


# --------------------------------------------------- semantic checks

def test_sp_offense_is_points_scored_not_a_differential(cfbd_sp):
    """
    The bug that produced 83 point totals. SP+ offense centres near 28
    because it is points scored. A differential centres on 0.
    """
    offense = [r["offense"]["rating"] for r in cfbd_sp
               if isinstance(r.get("offense"), dict)
               and r["offense"].get("rating") is not None]
    assert len(offense) > 50
    median = sorted(offense)[len(offense) // 2]
    assert median > 12.0, f"SP+ offense median is {median}, which is a differential"
    assert 15.0 < median < 50.0


def test_sp_overall_rating_is_a_differential(cfbd_sp):
    overall = [r["rating"] for r in cfbd_sp if r.get("rating") is not None]
    median = sorted(overall)[len(overall) // 2]
    assert abs(median) < 12.0, (
        f"SP+ overall median is {median}, which looks like points scored"
    )


def test_cfbd_publishes_bare_school_names(cfbd_games):
    """CFBD returns Alabama where the board returns Alabama Crimson Tide."""
    for g in cfbd_games[:40]:
        schema.assert_no_mascot(g["homeTeam"], "cfbd /games", "homeTeam")
        schema.assert_no_mascot(g["awayTeam"], "cfbd /games", "awayTeam")


def test_board_names_carry_mascots(odds_board):
    """
    The other half of the same fact. If the board ever stopped carrying
    mascots, the alias map would be doing nothing and nobody would notice.
    """
    from lib.teams import MASCOT_MAP, normalize
    carried = sum(1 for ev in odds_board
                  if normalize(MASCOT_MAP.get(normalize(ev["home_team"]), ""))
                  not in ("", normalize(ev["home_team"])))
    assert carried > len(odds_board) * 0.5, (
        "most board names should shorten under the mascot map"
    )


def test_every_board_price_is_real_american_odds(odds_board):
    """The parser used to default a missing price to 0."""
    seen = 0
    for ev in odds_board:
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                for oc in mk["outcomes"]:
                    assert abs(float(oc["price"])) >= 100
                    seen += 1
    assert seen > 100


def test_spreads_mirror_and_totals_match(odds_board):
    for ev in odds_board:
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                pts = [o.get("point") for o in mk["outcomes"]]
                if mk["key"] == "spreads" and len(pts) == 2:
                    assert abs(pts[0] + pts[1]) < 1e-6
                if mk["key"] == "totals" and len(pts) == 2:
                    assert abs(pts[0] - pts[1]) < 1e-6


def test_calendar_carries_the_week_window(cfbd_calendar):
    """
    make_slate falls back to no week filter when this is missing, which
    turns off the guard against rating a November game off a preseason
    number.
    """
    for w in cfbd_calendar:
        assert w.get("firstGameStart")
        assert w.get("lastGameStart")


def test_elo_is_time_varying_across_weeks():
    """
    Phase 2 leans on Elo precisely because it changes week to week. If it
    stopped, the backtest would be using an end of season number as an
    as-of number, which is lookahead bias.
    """
    w1 = {r["team"]: r["elo"] for r in load_fixture("cfbd", "elo_2024_w01")}
    w12 = {r["team"]: r["elo"] for r in load_fixture("cfbd", "elo_2024_w12")}
    shared = set(w1) & set(w12)
    assert len(shared) > 50
    moved = sum(1 for t in shared if w1[t] != w12[t])
    assert moved > len(shared) * 0.8, (
        "Elo barely moved between week 1 and week 12, so it is not the "
        "time varying input the backtest assumes it is"
    )
