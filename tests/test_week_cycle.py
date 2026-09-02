"""
The shape of the week: the Monday board, the Lock, the Thursday early run,
Saturday tracking and the Sunday settle.

Most of these guard seams between generated data and the page, because
that is where this session's real bugs have lived: a market number sourced
from the wrong file, a breakeven compared at the wrong scale, a logo keyed
by the wrong form of a name.
"""

from __future__ import annotations

import json
import yaml
import sys
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import build_site as B  # noqa: E402
import track_scores  # noqa: E402

NOW = datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc)


# ------------------------------------------------------- the board

@pytest.fixture(scope="module")
def board():
    return B.board_rows()


def test_every_board_game_carries_both_market_numbers(board):
    """
    The first cut sourced market numbers from candidates, which only exist
    where the model found an edge, so every fairly priced game showed n/a.
    A board that goes blank exactly where the pricing is good is backwards.
    """
    rows = board["rows"]
    assert rows, "the board is empty"
    missing = [r["matchup"] for r in rows
               if r["market_spread"] is None or r["market_total"] is None]
    assert missing == [], missing


def test_every_board_game_carries_both_logos(board):
    missing = [r["matchup"] for r in board["rows"]
               if not (r["home_logo"] and r["away_logo"])]
    assert missing == [], missing


def test_logos_come_through_the_real_matcher():
    """
    Sam Houston State Bearkats has to find ESPN's Sam Houston. Only
    canonical() knows that, and a half reimplementation missed it.
    """
    src = open(B.__file__).read()
    assert "canonical(" in src.split("def board_rows")[1].split("def ")[1] \
        or "canonical(" in src.split("def board_rows")[1][:3000]


def test_the_board_leads_with_the_strongest_lean(board):
    """
    Sorted by kickoff, the best thing on the whole slate sat third behind
    whatever kicked earliest, so a reader had to scroll to find the point.
    Strongest first, weakest last, with kickoff still on every row.
    """
    def best(r):
        return max((c.get("floor_confidence") or 0)
                   for c in r["candidates"]) if r["candidates"] else -1

    confs = [best(r) for r in board["rows"]]
    assert confs == sorted(confs, reverse=True), confs[:8]


def test_a_game_leads_with_its_own_strongest_lean(board):
    """A game carrying 2 leans opens on the better one."""
    for r in board["rows"]:
        confs = [c.get("floor_confidence") or 0 for c in r["candidates"]]
        assert confs == sorted(confs, reverse=True), r["matchup"]


def test_games_with_no_lean_sink_to_the_bottom(board):
    """
    Nothing to say ranks below something to say, whatever time it kicks.
    """
    rows = board["rows"]
    first_empty = next((i for i, r in enumerate(rows) if not r["candidates"]),
                       len(rows))
    assert all(not r["candidates"] for r in rows[first_empty:])


def test_a_missing_logo_stays_missing():
    """No logo beats a wrong logo, same reason None beats a guessed match."""
    from lib.teams import canonical, load_logos
    logos = load_logos()
    assert canonical("Nowhere Tech Fighting Nobodies", set(logos)) is None


# ------------------------------------------------------- the lock

def pick(id, conf, edge=0.0, placed="2026-08-28T18:00:00+00:00", **over):
    base = {"id": id, "live": True, "season": 2026, "week": 1,
            "confidence": conf, "edge": edge, "placed_at": placed,
            "result": "pending"}
    base.update(over)
    return base


def test_the_lock_is_the_most_confident_pick():
    picks = [pick("a", 8.2), pick("b", 8.6), pick("c", 8.4)]
    assert B.lock_id(picks, 2026, 1) == "b"


def test_lock_ties_break_on_edge_then_first_published():
    tied = [pick("a", 8.4, edge=2.0, placed="2026-08-28T19:00:00+00:00"),
            pick("b", 8.4, edge=3.5, placed="2026-08-28T20:00:00+00:00")]
    assert B.lock_id(tied, 2026, 1) == "b"
    dead = [pick("a", 8.4, edge=2.0, placed="2026-08-27T12:00:00+00:00"),
            pick("b", 8.4, edge=2.0, placed="2026-08-28T12:00:00+00:00")]
    assert B.lock_id(dead, 2026, 1) == "a"


def test_a_thursday_pick_can_wear_the_crown():
    """Published early does not mean demoted."""
    picks = [pick("thu", 9.0, placed="2026-08-27T18:00:00+00:00"),
             pick("fri", 8.4, placed="2026-08-28T18:00:00+00:00")]
    assert B.lock_id(picks, 2026, 1) == "thu"


def test_shadow_picks_never_lock():
    picks = [pick("shadow", 9.9, live=False), pick("real", 8.1)]
    assert B.lock_id(picks, 2026, 1) == "real"


def test_the_lock_gets_a_crown_not_units():
    """The lock is presentation. Nothing in the staking ladder reads it."""
    import re
    from lib import card_rules
    src = open(card_rules.__file__).read()
    assert not re.search(r"\block\b", src, re.I), (
        "card_rules mentions the lock, so staking may be reading presentation"
    )


# ------------------------------------------------- saturday tracking

def test_no_pending_picks_means_no_network():
    assert track_scores.watchable([], NOW) == []
    settled = [pick("a", 8.4, result="win",
                    kickoff="2026-08-29T16:00:00Z")]
    assert track_scores.watchable(settled, NOW) == []


def test_only_games_near_kickoff_are_watched():
    inside = pick("in", 8.4, kickoff="2026-08-29T23:30:00Z")
    tomorrow = pick("out", 8.4, kickoff="2026-09-05T16:00:00Z")
    long_over = pick("old", 8.4, kickoff="2026-08-29T02:00:00Z")
    got = track_scores.watchable([inside, tomorrow, long_over], NOW)
    assert [p["id"] for p in got] == ["in"]


def test_the_tracker_never_imports_the_ledger_writer():
    """It reads picks and writes one scores file. Nothing else."""
    src = open(track_scores.__file__).read()
    assert "save_picks" not in src
    assert "log_picks" not in src


# ------------------------------------------------- the live strip

def strip_for(raw, picks):
    import build_site
    orig = build_site.store._load
    try:
        build_site.store._load = lambda path, default: (
            raw if path == build_site.LIVE_FILE else orig(path, default))
        return build_site.live_strip(picks)
    finally:
        build_site.store._load = orig


LIVE_PICK = {"id": "p1", "live": True, "result": "pending", "event_id": "e1",
             "matchup": "A @ B", "title": "B -6.5", "market": "spread",
             "period": "full", "side": "B", "line": -6.5}
GAME = {"event_id": "e1", "home_team": "B", "away_team": "A",
        "home_score": 21, "away_score": 10, "completed": False}


def fresh(mins_ago=5):
    at = datetime.now(timezone.utc) - timedelta(minutes=mins_ago)
    return {"fetched_at": at.isoformat(timespec="seconds"), "games": [GAME]}


def test_a_fresh_feed_renders_and_computes_covering():
    got = strip_for(fresh(), [LIVE_PICK])
    assert got and got["rows"][0]["covering"] == "covering"


def test_a_stale_feed_renders_nothing():
    """
    A Tuesday page must not show Saturday's scoreboard. 90 minutes is the
    line, since the poller runs every 30.
    """
    assert strip_for(fresh(mins_ago=180), [LIVE_PICK]) is None


def test_not_covering_and_tied_read_correctly():
    dog = dict(LIVE_PICK, side="A", line=6.5)   # A +6.5, down 11
    got = strip_for(fresh(), [dog])
    assert got["rows"][0]["covering"] == "not covering"
    push = dict(LIVE_PICK, line=-11.0)
    got = strip_for(fresh(), [push])
    assert got["rows"][0]["covering"] == "tied"


def test_half_bets_show_scores_but_never_a_covering_flag():
    """The feed has no quarter detail, so claiming coverage would be a guess."""
    h1 = dict(LIVE_PICK, period="h1")
    got = strip_for(fresh(), [h1])
    assert got["rows"][0]["covering"] is None


# --------------------------------------------------- the schedules

DAILY = (ROOT / ".github" / "workflows" / "daily.yml").read_text()
WEEKLY = (ROOT / ".github" / "workflows" / "weekly-card.yml").read_text()
TRACK = (ROOT / ".github" / "workflows" / "track-scores.yml").read_text()


def test_the_site_updates_daily_at_2pm_eastern():
    assert '"0 18 * * *"' in DAILY


def test_the_card_researches_on_wednesday():
    """
    Wednesday 14:00 Eastern. A Thursday night game used to be rated the
    same afternoon it kicked; now it goes up more than a day ahead.
    """
    doc = yaml.safe_load(WEEKLY)
    on = doc[True] if True in doc else doc["on"]
    crons = [c["cron"] for c in on["schedule"]]
    assert crons == ["0 18 * * 3"], crons



def test_the_card_runs_one_scope_for_the_whole_week():
    """
    It used to split: a Thursday pass scoped to games kicking that night,
    then a Friday pass for the rest. Moving the card to Wednesday covers
    both in one card, so there is no early and late scope to derive.
    """
    assert 'SCOPE="full"' in WEEKLY
    assert 'SCOPE="thursday"' not in WEEKLY
    assert "scope $SCOPE" in WEEKLY


def test_sunday_grades_in_the_morning():
    assert '"0 13 * * 0"' in DAILY


def test_saturday_tracking_covers_noon_to_midnight_eastern():
    assert '"*/30 16-23 * * 6"' in TRACK
    assert '"*/30 0-3 * * 0"' in TRACK


def test_the_tracker_workflow_cannot_reach_the_ledger():
    assert "log_picks" not in TRACK
    assert "git add data/live_scores.json site" in TRACK


def test_scope_is_documented_for_the_agent():
    doc = (ROOT / ".claude" / "commands" / "research-card.md").read_text()
    assert "within 5 days" in doc
    assert "Thursday night through the coming Sunday" in doc



def gate_with(board_file, slate_file):
    """Run board_rows against injected board and slate payloads."""
    orig_board = B.store.load_board
    orig_load = B.store._load
    slate_path = B.store.DATA / "slate.json"
    try:
        B.store.load_board = lambda: board_file
        B.store._load = lambda path, default: (
            slate_file if path == slate_path else orig_load(path, default))
        return B.board_rows()
    finally:
        B.store.load_board = orig_board
        B.store._load = orig_load


def board_payload(fetched_at, games=None):
    return {"fetched_at": fetched_at, "games": games or []}


def slate_payload(board_fetched_at, rows):
    return {"week": 1, "season": 2026, "built_at": board_fetched_at,
            "board_fetched_at": board_fetched_at, "slate": rows}


def game_row(event_id="e1", home="Home Team", away="Away Team"):
    return {"event_id": event_id, "kickoff": "2026-08-29T16:00:00Z",
            "matchup": f"{away} @ {home}", "home_team": home,
            "away_team": away, "neutral_site": False,
            "model": {"projected_spread": -3.5, "projected_total": 51.0},
            "candidates": []}


def lines_for(home, spread=-6.5, total=52.5):
    return [{"market": "spreads", "side": home, "point": spread, "price": -110},
            {"market": "totals", "side": "Over", "point": total, "price": -110}]


def iso_hours_ago(h):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=h)
            ).isoformat(timespec="seconds")


def test_a_fresh_matching_board_publishes():
    at = iso_hours_ago(1)
    board = board_payload(at, [{"event_id": "e1",
                                "home_team": "Home Team",
                                "lines": lines_for("Home Team")}])
    got = gate_with(board, slate_payload(at, [game_row()]))
    assert got["stale"] is False
    assert len(got["rows"]) == 1 and got["held"] == []


def test_an_aged_board_does_not_publish_numbers():
    """
    The site's promise is a line at most a day old. A 30 hour board still
    renders a page, but the page says the numbers are down, and no game
    row carries a number nobody should bet.
    """
    at = iso_hours_ago(30)
    board = board_payload(at, [{"event_id": "e1",
                                "home_team": "Home Team",
                                "lines": lines_for("Home Team")}])
    got = gate_with(board, slate_payload(at, [game_row()]))
    assert got["stale"] is True
    assert got["age_hours"] > 26


def test_a_half_updated_pipeline_reads_as_stale():
    """
    Slate built from one pull, board on disk from another. That mismatch
    is exactly the shape of the bug that froze the site for 2 days, so it
    is stale outright even when both halves are individually recent.
    """
    board = board_payload(iso_hours_ago(1), [{
        "event_id": "e1", "home_team": "Home Team",
        "lines": lines_for("Home Team")}])
    got = gate_with(board, slate_payload(iso_hours_ago(3), [game_row()]))
    assert got["mismatched"] is True
    assert got["stale"] is True


def test_a_game_off_the_book_is_held_and_named():
    """
    On the slate but absent from the current pull means the book took it
    down or the fetch failed. Either way the number cannot be verified, so
    the game is held back and called out rather than shown.
    """
    at = iso_hours_ago(1)
    board = board_payload(at, [{"event_id": "e1",
                                "home_team": "Home Team",
                                "lines": lines_for("Home Team")}])
    rows = [game_row(), game_row(event_id="gone", home="Gone Team",
                              away="Other Team")]
    got = gate_with(board, slate_payload(at, rows))
    assert len(got["rows"]) == 1
    assert got["held"] == [{"matchup": "Other Team @ Gone Team",
                            "kickoff": "2026-08-29T16:00:00Z",
                            "why": "no_line"}]


def test_a_missing_board_reads_as_stale():
    got = gate_with({"fetched_at": None, "games": []},
                    slate_payload(iso_hours_ago(1), [game_row()]))
    assert got["stale"] is True


def test_the_page_speaks_when_the_board_is_down():
    assert "board_stale" in B.VOICE and "{age}" in B.VOICE["board_stale"]
    assert "board_held" in B.VOICE and "{games}" in B.VOICE["board_held"]
    # The check reads the timestamp against the reader's clock rather
    # than the flag stamped in at build time, because a page only gets
    # built when the job succeeds and the flag cannot report a job that
    # stopped running.
    assert "function boardStale()" in B.HTML
    assert "fetched_at" in B.HTML


def test_the_build_output_names_what_was_held():
    """The workflow log is where the callout lands for the routines to read."""
    src = open(B.__file__).read()
    assert "games_held_back" in src
    assert "board_stale" in src
