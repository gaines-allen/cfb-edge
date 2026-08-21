"""
The price on a candidate has to be the price of the side being bet.

Found by the first unattended handicapper run. make_slate mirrored the
point for an away side but kept the home side's price, so every away spread
candidate carried a number nobody could have bet. On the live week 1 board
Hawaii +5.5 was +100 while Stanford -5.5 was -122, and the slate reported
-122 for Hawaii.

It matters because price is not cosmetic. payout() turns it into units,
breakeven() turns it into the win rate a pick has to clear, and clv_cents()
scores it against the close. A 22 cent error moves all 3.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import ROOT, seed_cfbd_cache


@pytest.fixture(scope="module")
def slate(tmp_path_factory):
    import os
    cache = seed_cfbd_cache(tmp_path_factory.mktemp("cache"), 2026, 1)
    out = tmp_path_factory.mktemp("out")
    env = dict(os.environ)
    env.pop("CFBD_API_KEY", None)
    env.pop("ODDS_API_KEY", None)
    env["CFBD_CACHE_DIR"] = str(cache)
    env["CFBD_CACHE_TTL"] = "-1"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_slate.py"),
         "--season", "2026", "--week", "1"],
        capture_output=True, text=True, cwd=str(ROOT), env=env)
    assert proc.returncode == 0, proc.stderr[-600:]
    built = json.loads((ROOT / "data" / "slate.json").read_text())
    # Put the committed build back rather than leaving a rebuilt one behind.
    subprocess.run(["git", "checkout", "--", "data/slate.json"], cwd=str(ROOT))
    return built


@pytest.fixture(scope="module")
def board():
    return json.loads((ROOT / "data" / "board.json").read_text())["games"]


def board_price(board, event_id, market, side_name):
    game = next(g for g in board if g["event_id"] == event_id)
    for ln in game["lines"]:
        if ln["market"] == market and ln["side"] == side_name:
            return ln["price"], ln["point"]
    return None, None


def test_every_spread_candidate_quotes_its_own_side(slate, board):
    """
    The regression. Walks the whole slate rather than 1 game, because the
    bug was systematic on away sides and invisible on home sides.
    """
    wrong = []
    for row in slate["slate"]:
        game = next(g for g in board if g["event_id"] == row["event_id"])
        for c in row["candidates"]:
            if c["market"] != "spread" or c["period"] != "full":
                continue
            # The slate stores canonical names, the board stores its own.
            raw = (game["home_team"] if c["side"] == row["model"]["home_team"]
                   else game["away_team"])
            price, point = board_price(board, row["event_id"], "spreads", raw)
            if price is None:
                continue
            if c["price"] != price or abs(c["bet_line"] - point) > 1e-6:
                wrong.append(
                    f"{row['matchup']}: {c['side']} quoted {c['bet_line']} at "
                    f"{c['price']}, board has {point} at {price}")
    assert wrong == [], "\n".join(wrong)


def test_the_two_sides_of_a_spread_can_differ_in_price(board):
    """
    The premise. If both sides were always priced the same the bug would
    have been harmless, and this test would be pointless.
    """
    differing = 0
    for g in board:
        prices = [ln["price"] for ln in g["lines"] if ln["market"] == "spreads"]
        if len(prices) == 2 and prices[0] != prices[1]:
            differing += 1
    assert differing > 0, (
        "no game on the board prices its 2 spread sides differently, so this "
        "board cannot exercise the bug"
    )


def test_total_candidates_quote_their_own_side(slate, board):
    """The totals branch was already correct. This keeps it that way."""
    wrong = []
    for row in slate["slate"]:
        for c in row["candidates"]:
            if c["market"] != "total" or c["period"] != "full":
                continue
            price, point = board_price(board, row["event_id"], "totals", c["side"])
            if price is None:
                continue
            if c["price"] != price:
                wrong.append(f"{row['matchup']}: {c['side']} at {c['price']}, "
                             f"board has {price}")
    assert wrong == []


def test_a_wrong_price_changes_the_breakeven_it_implies():
    """
    Why 22 cents is not cosmetic. This is the actual pair off the week 1
    board, and the gap is 5 points of required win rate.
    """
    from lib.scoring import breakeven
    assert breakeven(100) == pytest.approx(0.500, abs=0.001)
    assert breakeven(-122) == pytest.approx(0.550, abs=0.001)
