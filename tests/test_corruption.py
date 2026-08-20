"""
A deliberately corrupted fixture must make a test fail rather than a pick
appear.

Each case takes a real payload, applies 1 named piece of damage of the kind
this system has actually shipped, and proves 2 things. The validator raises,
and the pipeline stops instead of producing a candidate with a confidence
attached to it.

The second half is the one that matters. A validator that raises in a unit
test but sits outside the path a script takes is decoration.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import DELETE, ROOT, corrupt, load_fixture, seed_cfbd_cache
from lib import schema
from lib.schema import ShapeError


# ------------------------------------------------- the validators bite

def first_market_path(board, key="spreads"):
    for i, ev in enumerate(board):
        for bi, bk in enumerate(ev.get("bookmakers", [])):
            for mi, mk in enumerate(bk["markets"]):
                if mk["key"] == key:
                    return [i, "bookmakers", bi, "markets", mi]
    pytest.fail(f"no {key} market on the captured board")


def test_missing_price_raises(odds_board):
    """
    The parser used to do int(oc.get("price", 0)), so a renamed price became
    a 0 that reached payout() looking like a real number.
    """
    path = first_market_path(odds_board) + ["outcomes", 0, "price"]
    bad = corrupt(odds_board, (path, DELETE))
    with pytest.raises(ShapeError, match="price"):
        schema.validate_odds_board(bad)


def test_renamed_price_raises(odds_board):
    path = first_market_path(odds_board) + ["outcomes", 0]
    bad = copy.deepcopy(odds_board)
    node = bad
    for step in path:
        node = node[step]
    node["odds"] = node.pop("price")
    with pytest.raises(ShapeError, match="price"):
        schema.validate_odds_board(bad)


def test_price_of_zero_raises(odds_board):
    """The exact value the old default produced."""
    path = first_market_path(odds_board) + ["outcomes", 0, "price"]
    bad = corrupt(odds_board, (path, 0))
    with pytest.raises(ShapeError, match="American odds"):
        schema.validate_odds_board(bad)


def test_missing_spread_point_raises(odds_board):
    path = first_market_path(odds_board) + ["outcomes", 0, "point"]
    bad = corrupt(odds_board, (path, DELETE))
    with pytest.raises(ShapeError, match="point"):
        schema.validate_odds_board(bad)


def test_string_where_a_spread_belongs_raises(odds_board):
    path = first_market_path(odds_board) + ["outcomes", 0, "point"]
    bad = corrupt(odds_board, (path, "-7.5"))
    with pytest.raises(ShapeError, match="point"):
        schema.validate_odds_board(bad)


def test_spread_sign_flip_raises(odds_board):
    """
    Both sides laying the same number means the sign convention moved, and
    every projection would be graded against the wrong side.
    """
    base = first_market_path(odds_board)
    bad = copy.deepcopy(odds_board)
    node = bad
    for step in base:
        node = node[step]
    node["outcomes"][1]["point"] = node["outcomes"][0]["point"]
    with pytest.raises(ShapeError, match="sum to 0"):
        schema.validate_odds_board(bad)


def test_sp_offense_as_a_differential_raises(cfbd_sp):
    """
    The bug that produced 83 point totals against a 48.5 market and made
    every candidate an Over. Shifting the column onto a differential scale
    is what a renamed or redefined field would actually look like.
    """
    bad = copy.deepcopy(cfbd_sp)
    for r in bad:
        if isinstance(r.get("offense"), dict) and r["offense"].get("rating") is not None:
            r["offense"]["rating"] = r["offense"]["rating"] - 28.0
    with pytest.raises(ShapeError, match="differential"):
        schema.validate_sp_ratings(bad)


def test_sp_offense_renamed_raises(cfbd_sp):
    bad = copy.deepcopy(cfbd_sp)
    for r in bad:
        if "offense" in r:
            r["off"] = r.pop("offense")
    with pytest.raises(ShapeError, match="offense"):
        schema.validate_sp_ratings(bad)


def test_sp_overall_as_points_raises(cfbd_sp):
    """The same swap in the other direction."""
    bad = copy.deepcopy(cfbd_sp)
    for r in bad:
        if r.get("rating") is not None:
            r["rating"] = r["rating"] + 28.0
    with pytest.raises(ShapeError, match="points scored"):
        schema.validate_sp_ratings(bad)


def test_cfbd_name_carrying_a_mascot_raises(cfbd_games):
    """
    CFBD publishes locations. If it ever started returning Alabama Crimson
    Tide, every rating lookup would miss and the whole slate would silently
    thin out.
    """
    bad = copy.deepcopy(cfbd_games)
    bad[0]["homeTeam"] = "Alabama Crimson Tide"
    with pytest.raises(ShapeError, match="mascot|shortens"):
        schema.validate_cfbd_games(bad)


def test_quarter_scores_as_a_string_raises(cfbd_games):
    """
    Quarter scores are the only reason a first half pick can settle, so a
    string here must not reach half_points and come back None.
    """
    bad = copy.deepcopy(cfbd_games)
    played = next(i for i, g in enumerate(bad) if g.get("homeLineScores"))
    bad[played]["homeLineScores"] = "7,14,6,7"
    with pytest.raises(ShapeError, match="list of quarter scores"):
        schema.validate_cfbd_games(bad)


def test_quarter_score_as_a_float_raises(cfbd_games):
    bad = copy.deepcopy(cfbd_games)
    played = next(i for i, g in enumerate(bad) if g.get("homeLineScores"))
    bad[played]["homeLineScores"] = [7.5, 14, 6, 7]
    with pytest.raises(ShapeError, match="integer"):
        schema.validate_cfbd_games(bad)


def test_missing_completed_flag_raises(cfbd_games):
    bad = corrupt(cfbd_games, ([0, "completed"], DELETE))
    with pytest.raises(ShapeError, match="completed"):
        schema.validate_cfbd_games(bad)


def test_calendar_without_a_window_raises(cfbd_calendar):
    """
    make_slate falls back to no week filter when this is missing, which
    turns off the guard against rating a November game off a preseason
    number. The board runs weeks ahead, so that guard matters.
    """
    bad = corrupt(cfbd_calendar, ([0, "firstGameStart"], DELETE))
    with pytest.raises(ShapeError, match="firstGameStart"):
        schema.validate_cfbd_calendar(bad)


def test_calendar_with_an_unparseable_date_raises(cfbd_calendar):
    bad = corrupt(cfbd_calendar, ([0, "lastGameStart"], "week one"))
    with pytest.raises(ShapeError, match="ISO 8601"):
        schema.validate_cfbd_calendar(bad)


def test_elo_from_the_wrong_week_raises(cfbd_elo):
    """
    Every input has to be as of the week being scored. Elo arriving from a
    different week is lookahead bias wearing the right field name.
    """
    bad = copy.deepcopy(cfbd_elo)
    for r in bad:
        r["week"] = 14
    with pytest.raises(ShapeError, match="week 5"):
        schema.validate_elo_ratings(bad, week=5)


def test_elo_on_the_wrong_scale_raises(cfbd_elo):
    bad = copy.deepcopy(cfbd_elo)
    for r in bad:
        r["elo"] = (r.get("elo") or 1500) / 100.0
    with pytest.raises(ShapeError, match="1000 and 2200"):
        schema.validate_elo_ratings(bad)


def test_espn_losing_the_location_split_raises(espn_page):
    """
    location and name published separately is the whole trick behind the
    mascot map. Without it the map cannot be rebuilt at all.
    """
    bad = copy.deepcopy(espn_page)
    for w in bad["sports"][0]["leagues"][0]["teams"]:
        w["team"].pop("location", None)
    with pytest.raises(ShapeError, match="location"):
        schema.validate_espn_teams(bad)


def test_espn_silent_truncation_raises(espn_page):
    """
    The endpoint caps at 500 and truncates alphabetically, and asking for
    900 silently returns the top 25 rather than erroring.
    """
    bad = copy.deepcopy(espn_page)
    bad["sports"][0]["leagues"][0]["teams"] = \
        bad["sports"][0]["leagues"][0]["teams"][:25]
    with pytest.raises(ShapeError, match="truncation"):
        schema.validate_espn_teams(bad, expected=100)


def test_lines_all_null_on_a_played_week_raises(cfbd_lines):
    bad = copy.deepcopy(cfbd_lines)
    for g in bad:
        for ln in g.get("lines") or []:
            ln["spread"] = ln["overUnder"] = None
            ln["spreadOpen"] = ln["overUnderOpen"] = None
    with pytest.raises(ShapeError, match="priced"):
        schema.validate_cfbd_lines(bad)


# -------------------------------------- and the pipeline stops dead

def run_slate(cache: Path, season=2026, week=1):
    import os
    env = dict(os.environ)
    env.pop("CFBD_API_KEY", None)
    env.pop("ODDS_API_KEY", None)
    env["CFBD_CACHE_DIR"] = str(cache)
    env["CFBD_CACHE_TTL"] = "-1"
    env["RUN_ID"] = "corrupt00001"
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_slate.py"),
         "--season", str(season), "--week", str(week), "--dry-run"],
        capture_output=True, text=True, env=env, cwd=str(ROOT))


CORRUPTIONS = {
    "sp_offense_shifted_to_a_differential": ("sp", lambda p: [
        {**r, "offense": ({**r["offense"], "rating": r["offense"]["rating"] - 28.0}
                          if isinstance(r.get("offense"), dict)
                          and r["offense"].get("rating") is not None
                          else r.get("offense"))} for r in p]),
    "sp_offense_renamed": ("sp", lambda p: [
        {k if k != "offense" else "off": v for k, v in r.items()} for r in p]),
    "cfbd_names_carry_mascots": ("games", lambda p: [
        {**r, "homeTeam": "Alabama Crimson Tide"} for r in p]),
    "calendar_lost_its_window": ("calendar", lambda p: [
        {k: v for k, v in w.items() if k != "firstGameStart"} for w in p]),
    "quarter_scores_became_a_string": ("games", lambda p: [
        {**r, "homeLineScores": "7,14,6,7"} for r in p]),
}


@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_corrupt_fixture_stops_the_slate(tmp_path, name):
    """
    The done condition. A corrupted payload has to stop the run, and no
    candidate may come out the other side carrying a confidence.
    """
    kind, damage = CORRUPTIONS[name]
    fixture_name = (f"{kind}_2026_w01" if kind in ("games", "lines", "elo")
                    else f"{kind}_2026")
    payload = load_fixture("cfbd", fixture_name)
    cache = seed_cfbd_cache(tmp_path / "cache", 2026, 1,
                            overrides={kind: damage(payload)})

    proc = run_slate(cache)
    assert proc.returncode != 0, (
        f"{name} did not stop the slate build. stdout was:\n{proc.stdout[:800]}"
    )
    assert "SHAPE ERROR" in proc.stderr, proc.stderr[-800:]
    # And nothing that looks like a candidate came out.
    assert "floor_conf" not in proc.stdout
    assert "top" not in proc.stdout


def test_the_clean_fixture_still_produces_a_slate(tmp_path):
    """
    The control. Without this the test above passes when everything is
    broken, which is the failure mode of every corruption suite.
    """
    cache = seed_cfbd_cache(tmp_path / "cache", 2026, 1)
    proc = run_slate(cache)
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout)
    assert out["games_with_candidates"] > 0
    assert out["unmapped_names"] == 0


def test_no_candidate_ever_reaches_the_publish_threshold(tmp_path):
    """
    The ratings model must never justify a published pick on its own, so
    every floor confidence on a real slate sits under 8.0.
    """
    cache = seed_cfbd_cache(tmp_path / "cache", 2026, 1)
    proc = run_slate(cache)
    out = json.loads(proc.stdout)
    for row in out["top"]:
        assert row["floor_conf"] < 8.0, row
