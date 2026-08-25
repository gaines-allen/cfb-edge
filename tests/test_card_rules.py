"""
The rules that stand between an unattended handicapper and the ledger.

The ratings model caps at 7.5 so it can never publish on its own. These
rules enforce the other half, that whatever lifted a pick to 8.0 was
research, that the research was sourced, and that the week's exposure stays
inside the ceiling.

Every rule gets a case that passes and a case that fails. A rule with only a
passing case is a rule nobody has checked.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ROOT
from lib.card_rules import (
    MAX_LIVE_PICKS, MAX_SOURCE_AGE_DAYS, MAX_UNITS_PER_WEEK, check_card,
    check_pick, correlation_warnings, units_for,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
STALE = (NOW - timedelta(days=MAX_SOURCE_AGE_DAYS + 5)).strftime("%Y-%m-%d")


def pick(**over):
    base = {
        "event_id": "ev1", "matchup": "Away @ Home", "market": "spread",
        "period": "full", "side": "Home", "line": -6.5, "price": -110,
        "confidence": 8.2, "units": 1.0,
        "rationale": "A" * 120,
        "factors": ["rating_edge", "injury_edge"],
        "sources": [{"url": "https://example.com/a", "publisher": "Beat",
                     "date": FRESH, "claim": "starter is out",
                     "quote": "The starting left tackle has been cleared to "
                              "play in the opener, the coach said Monday."}],
    }
    base.update(over)
    return base


def errs(p):
    return check_pick(p, 0, now=NOW)


# ------------------------------------------------------- the core rule

def test_a_sourced_researched_pick_passes():
    assert errs(pick()) == []


def test_ratings_alone_cannot_publish():
    """
    The whole point. suggested_confidence caps at 7.5 so the model can never
    justify a live pick, and restating its number at 8.1 would walk around
    that cap.
    """
    bad = errs(pick(factors=["rating_edge"]))
    assert any("rating_edge alone" in e for e in bad)


def test_ratings_alone_is_fine_below_the_threshold():
    """Shadow picks exist to test the threshold, so they carry no such bar."""
    assert errs(pick(confidence=7.4, factors=["rating_edge"], sources=[])) == []


def test_a_live_pick_needs_sources():
    bad = errs(pick(sources=[]))
    assert any("no sources" in e for e in bad)


GOOD_QUOTE = ("The starting left tackle has been cleared to play in the "
              "opener, the coach said Monday.")


@pytest.mark.parametrize("source,fragment", [
    ({"url": "not-a-url", "date": FRESH, "quote": GOOD_QUOTE}, "no usable url"),
    ({"url": "https://x.com/a", "date": "the other day", "quote": GOOD_QUOTE},
     "no parseable date"),
    ({"url": "https://x.com/a", "date": STALE, "quote": GOOD_QUOTE}, "days old"),
    ({"url": "https://x.com/a", "quote": GOOD_QUOTE,
      "date": (NOW + timedelta(days=5)).strftime("%Y-%m-%d")}, "future"),
    ({"url": "https://x.com/a", "date": FRESH}, "no quote"),
    ({"url": "https://x.com/a", "date": FRESH, "quote": "he is out"},
     "under the 25"),
])
def test_bad_sources_are_rejected(source, fragment):
    assert any(fragment in e for e in errs(pick(sources=[source])))


def test_stale_research_is_reusing_last_weeks_reasoning():
    bad = errs(pick(sources=[{"url": "https://x.com/a", "date": STALE,
                              "quote": GOOD_QUOTE}]))
    assert any("last week's reasoning" in e for e in bad)


# ----------------------------------------------------------- injuries

def test_injury_claim_without_a_source_is_rejected():
    bad = errs(pick(factors=["rating_edge", "injury_edge"], sources=[]))
    assert any("injury_edge with no source" in e for e in bad)


def test_an_unconfirmed_injury_caps_the_confidence():
    """
    College football has no league wide injury report. A pick built on a
    rumoured quarterback injury that turns out wrong is bad process.
    """
    bad = errs(pick(confidence=8.8, units=1.5,
                    rationale="The starter is reportedly out. " + "A" * 100))
    assert any("not confirmed" in e for e in bad)


def test_an_unconfirmed_injury_is_allowed_lower_down():
    ok = errs(pick(confidence=8.2, units=1.0,
                   rationale="The starter is reportedly out. " + "A" * 100))
    assert ok == []


# ------------------------------------------------------- max stake bar

def test_two_units_needs_two_factors_and_two_sources():
    bad = errs(pick(confidence=9.2, units=2.0))
    assert any("Two or more" in e for e in bad)


def test_two_units_passes_with_two_of_each():
    ok = errs(pick(
        confidence=9.2, units=2.0,
        factors=["rating_edge", "injury_edge", "line_value"],
        sources=[{"url": "https://a.com/1", "date": FRESH, "quote": GOOD_QUOTE},
                 {"url": "https://b.com/2", "date": FRESH, "quote": GOOD_QUOTE}]))
    assert ok == []


# --------------------------------------------------------- the ladder

@pytest.mark.parametrize("conf,want", [
    (8.0, 1.0), (8.4, 1.0), (8.5, 1.5), (8.9, 1.5), (9.0, 2.0), (9.9, 2.0),
])
def test_the_staking_ladder(conf, want):
    assert units_for(conf) == want


def test_units_must_match_the_ladder():
    bad = errs(pick(confidence=8.2, units=2.0))
    assert any("stakes 1.0 units, not 2.0" in e for e in bad)


def test_nothing_stakes_more_than_two_units():
    bad = errs(pick(confidence=9.5, units=3.0,
                    factors=["rating_edge", "injury_edge", "line_value"],
                    sources=[{"url": "https://a.com/1", "date": FRESH},
                             {"url": "https://b.com/2", "date": FRESH}]))
    assert any("unit ceiling on a single pick" in e for e in bad)


# ------------------------------------------------------ card wide

def live_card(n, **over):
    return [pick(event_id=f"ev{i}", matchup=f"A{i} @ H{i}", **over)
            for i in range(n)]


def test_six_live_picks_is_allowed():
    assert check_card(live_card(MAX_LIVE_PICKS), now=NOW) == []


def test_seven_live_picks_is_rejected():
    bad = check_card(live_card(MAX_LIVE_PICKS + 1), now=NOW)
    assert any("live picks" in e and "ceiling" in e for e in bad)


def test_a_short_card_is_fine():
    """A short card is the correct outcome when the slate is priced well."""
    assert check_card(live_card(2), now=NOW) == []


def test_an_empty_card_is_fine():
    assert check_card([], now=NOW) == []


def test_the_weekly_unit_ceiling_holds():
    card = [pick(event_id=f"ev{i}", matchup=f"A{i} @ H{i}", confidence=9.1,
                 units=2.0, factors=["rating_edge", "injury_edge", "line_value"],
                 sources=[{"url": "https://a.com/1", "date": FRESH,
                           "quote": GOOD_QUOTE},
                          {"url": "https://b.com/2", "date": FRESH,
                           "quote": GOOD_QUOTE}])
            for i in range(7)]
    bad = check_card(card, now=NOW)
    assert any(f"weekly ceiling is {MAX_UNITS_PER_WEEK}" in e for e in bad)


def test_the_same_game_and_market_cannot_be_taken_twice():
    card = [pick(event_id="same"), pick(event_id="same")]
    bad = check_card(card, now=NOW)
    assert any("twice the size" in e for e in bad)


def test_the_same_game_on_a_different_market_is_one_opinion_not_two():
    """
    A spread and a total on one matchup read the same game twice. Six
    plays that are really four opinions is a smaller and more correlated
    book than the unit count says, and the staking math assumes six.
    """
    card = [pick(event_id="same", market="spread"),
            pick(event_id="same", market="total", side="Over", line=52.5)]
    errs = check_card(card, now=NOW)
    assert len(errs) == 1
    assert "one opinion at twice the size" in errs[0]


def test_two_different_games_are_two_opinions():
    card = [pick(event_id="a", market="spread"),
            pick(event_id="b", market="total", side="Over", line=52.5)]
    assert check_card(card, now=NOW) == []


# ------------------------------------------------------- correlation

def test_correlated_picks_are_flagged():
    """
    Week 1 of 2026 produced 3 Unders on heavy favourite non conference
    openers, all resting on the same mechanism. Three bets on one idea is
    one bet at three times the stake.
    """
    card = live_card(3, factors=["rating_edge", "garbage_time_fade"])
    warn = correlation_warnings(card)
    assert warn and "garbage_time_fade" in warn[0]


def test_correlation_is_advisory_not_blocking():
    card = live_card(3, factors=["rating_edge", "garbage_time_fade"])
    assert check_card(card, now=NOW) == []


def test_two_picks_on_one_factor_is_not_flagged():
    assert correlation_warnings(live_card(2)) == []


# ------------------------------------------------- the gate as a script

def today_card(n, **over):
    """
    The gate runs as a subprocess against the real clock, so its fixtures
    have to be dated against the real clock too rather than the frozen one
    the unit tests use.
    """
    real_fresh = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    over.setdefault("sources", [{"url": "https://example.com/a",
                                 "publisher": "Beat", "date": real_fresh,
                                 "claim": "starter is out",
                                 "quote": GOOD_QUOTE}])
    return [pick(event_id=f"ev{i}", matchup=f"A{i} @ H{i}", **over)
            for i in range(n)]


def run_gate(tmp_path, card):
    draft = tmp_path / "picks_draft.json"
    draft.write_text(json.dumps(card))
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_card.py"),
         "--file", str(draft), "--json"],
        capture_output=True, text=True, cwd=str(ROOT))


def test_the_gate_accepts_a_clean_card(tmp_path):
    proc = run_gate(tmp_path, today_card(3))
    assert proc.returncode == 0, proc.stderr[-500:]
    assert json.loads(proc.stdout)["passed"] is True


def test_the_gate_rejects_a_ratings_only_card(tmp_path):
    proc = run_gate(tmp_path, today_card(2, factors=["rating_edge"], sources=[]))
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["passed"] is False
    assert any("rating_edge alone" in e for e in out["errors"])


def test_the_gate_reports_units_at_risk(tmp_path):
    proc = run_gate(tmp_path, today_card(4))
    assert json.loads(proc.stdout)["units_at_risk"] == 4.0


def test_a_missing_draft_is_an_error_not_a_pass(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_card.py"),
         "--file", str(tmp_path / "nope.json")],
        capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 2



# ------------------------------------------------- the quote requirement

def test_a_source_without_a_quote_is_rejected():
    """
    A url and a date prove a page exists. The quote is what lets a machine
    check the claim against the page instead of taking the paraphrase on
    trust, which is the whole product.
    """
    bad = errs(pick(sources=[{"url": "https://x.com/a", "date": FRESH}]))
    assert any("no quote" in e for e in bad)


def test_a_trivial_quote_is_rejected():
    """"he is out" appears everywhere and proves nothing about this page."""
    bad = errs(pick(sources=[{"url": "https://x.com/a", "date": FRESH,
                              "quote": "he is out"}]))
    assert any("under the 25" in e for e in bad)


def test_a_shadow_pick_needs_no_quote():
    assert errs(pick(confidence=7.2, factors=["rating_edge"], sources=[])) == []
