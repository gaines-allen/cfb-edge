"""
The logger, exercised as a whole rather than by its pieces.

The card rules were wired into this script and the wiring named a variable
that did not exist. Every unit test still passed, because nothing ran the
script end to end. These do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ROOT

FRESH = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
STALE = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def board_event():
    board = json.loads((ROOT / "data" / "board.json").read_text())
    for g in board["games"]:
        spread = next((l for l in g["lines"]
                       if l["market"] == "spreads" and l["side"] == g["home_team"]),
                      None)
        if spread:
            return g, spread
    pytest.fail("the committed board carries no spreads")


def draft_row(event, spread, **over):
    row = {
        "event_id": event["event_id"],
        "matchup": f"{event['away_team']} @ {event['home_team']}",
        "kickoff": event["commence_time"],
        "market": "spread", "period": "full", "side": event["home_team"],
        "line": spread["point"], "price": spread["price"],
        "confidence": 8.4, "units": 1.0, "model_number": -21.5,
        "rationale": (
            "The market has not moved on the reported line change, which the "
            "ratings cannot see. What would make me wrong is a snap count "
            "that keeps him under 20 plays."),
        "factors": ["rating_edge", "injury_edge"],
        "sources": [{"url": "https://example.com/report",
                     "publisher": "Beat writer", "date": FRESH,
                     "claim": "starter cleared to play",
                     "quote": "The starting left tackle has been cleared to "
                              "play in the opener, the coach said Monday."}],
    }
    row.update(over)
    return row


def run_log(tmp_path, rows, *extra):
    import os
    draft = tmp_path / "picks_draft.json"
    draft.write_text(json.dumps(rows))
    env = dict(os.environ)
    env.pop("CFBD_API_KEY", None)
    env.pop("ODDS_API_KEY", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "log_picks.py"),
         "--file", str(draft), "--season", "2026", "--week", "1", *extra],
        capture_output=True, text=True, cwd=str(ROOT), env=env)


def test_a_clean_card_rehearses_without_touching_the_ledger(tmp_path, board_event):
    event, spread = board_event
    before = (ROOT / "data" / "picks.json").read_bytes()
    proc = run_log(tmp_path, [draft_row(event, spread)], "--dry-run")
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout)
    assert out["dry_run"] is True
    assert out["live_picks"] == 1
    assert out["units_at_risk"] == 1.0
    assert (ROOT / "data" / "picks.json").read_bytes() == before


def test_the_script_runs_at_all(tmp_path, board_event):
    """
    The regression for the bug above. A NameError raised at import or on the
    first card would have shipped, because every unit test passed.
    """
    event, spread = board_event
    proc = run_log(tmp_path, [draft_row(event, spread)], "--dry-run")
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    assert "NameError" not in proc.stderr


def test_the_card_rules_are_enforced_here_too(tmp_path, board_event):
    """
    A hand logged card must clear the same gate the weekly workflow clears,
    or the automated path is the strict one and the manual path is the way
    around it.
    """
    event, spread = board_event
    proc = run_log(tmp_path,
                   [draft_row(event, spread, factors=["rating_edge"], sources=[])],
                   "--dry-run")
    assert proc.returncode == 3
    assert "rating_edge alone" in proc.stderr


def test_stale_research_is_refused(tmp_path, board_event):
    event, spread = board_event
    proc = run_log(tmp_path, [draft_row(
        event, spread,
        sources=[{"url": "https://example.com/a", "date": STALE,
                  "quote": "The starting left tackle has been cleared to "
                           "play in the opener, the coach said Monday."}])],
        "--dry-run")
    assert proc.returncode == 3
    assert "last week's reasoning" in proc.stderr


def seven_games(rows_wanted=7):
    board = json.loads((ROOT / "data" / "board.json").read_text())
    rows = []
    for g in board["games"]:
        sp = next((l for l in g["lines"]
                   if l["market"] == "spreads" and l["side"] == g["home_team"]), None)
        if sp:
            rows.append((g, sp))
        if len(rows) == rows_wanted:
            break
    return rows


def test_the_card_is_the_six_highest_and_the_rest_are_shadow(tmp_path, board_event):
    """
    Seven rated games used to be refused as one over the ceiling. The card
    is the 6 best rated of whatever was researched, so the 7th is not an
    error, it is the first shadow pick.
    """
    games = seven_games(7)
    rows = [draft_row(g, sp, confidence=c)
            for (g, sp), c in zip(games, (8.4, 7.9, 7.6, 7.2, 6.8, 6.5, 6.1))]
    proc = run_log(tmp_path, rows, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["live_picks"] == 6
    assert out["shadow_picks"] == 1
    assert out["units_at_risk"] == 6.0


def test_the_lowest_rated_is_the_one_left_off(tmp_path, board_event):
    games = seven_games(7)
    confs = (8.4, 7.9, 7.6, 7.2, 6.8, 6.5, 6.1)
    rows = [draft_row(g, sp, confidence=c) for (g, sp), c in zip(games, confs)]
    proc = run_log(tmp_path, rows, "--dry-run")
    out = json.loads(proc.stdout)
    on_card = " ".join(out["card"])
    assert "(6.1," not in on_card
    assert "(6.5," in on_card


def test_two_markets_on_one_game_fill_one_slot(tmp_path, board_event):
    """
    One opinion per game. A spread and a total on the same matchup are one
    read priced two ways, and the second must not spend a card slot.
    """
    games = seven_games(6)
    rows = [draft_row(g, sp, confidence=c)
            for (g, sp), c in zip(games, (8.4, 7.9, 7.6, 7.2, 6.8, 6.5))]
    g0, sp0 = games[0]
    tot = next((l for l in g0["lines"] if l["market"] == "totals" and l["side"] == "Over"), None)
    if tot:
        rows.append(draft_row(g0, sp0, market="total", side="Over",
                              line=tot["point"], price=tot["price"], confidence=8.0))
    proc = run_log(tmp_path, rows, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["live_picks"] == 6
    assert sum(1 for c in out["card"] if g0["home_team"] in c) <= 1


def test_an_event_not_on_the_board_is_refused(tmp_path, board_event):
    event, spread = board_event
    proc = run_log(tmp_path,
                   [draft_row(event, spread, event_id="not-a-real-event")],
                   "--dry-run")
    assert proc.returncode == 3
    assert "not on the current board" in proc.stderr


def test_sources_survive_onto_the_pick_record(board_event):
    """The grader needs them to tell a sourced read from a hunch."""
    from lib import store
    row = store.new_pick(
        season=2026, week=1, event_id="e", matchup="A @ B", kickoff="",
        market="spread", period="full", side="B", line=-6.5, price=-110,
        confidence=8.4, units=1.0, rationale="r", factors={"injury_edge": True},
        sources=[{"url": "https://example.com/a", "date": FRESH}])
    assert row["sources"][0]["url"] == "https://example.com/a"


def test_a_pick_with_no_sources_still_records_an_empty_list():
    from lib import store
    row = store.new_pick(
        season=2026, week=1, event_id="e", matchup="A @ B", kickoff="",
        market="spread", period="full", side="B", line=-6.5, price=-110,
        confidence=7.2, units=1.0, rationale="r", factors={})
    assert row["sources"] == []
