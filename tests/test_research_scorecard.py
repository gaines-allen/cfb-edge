"""
Keeping score of the layer that decides what publishes.

The ratings model caps at 7.5 and the card starts at 8.0, so research is
the only thing that can put a play up. That made it the most consequential
part of the system and the only one with nothing grading it.

verify_sources.py proves a quote sits on the page it claims. It cannot
prove the quote supports the pick, that the outlet is any good, or that
nobody went looking for whatever would clear the gate. Only the record
answers those, so the record keeps score.
"""

from __future__ import annotations

import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import grade_results as G  # noqa: E402


def pick(result, units_net, sources=None, conf=8.5):
    return {"confidence": conf, "result": result, "units_net": units_net,
            "units": 1.0, "factors": {}, "sources": sources or []}


def src(url):
    return {"url": url, "quote": "x" * 40, "date": "2026-08-20"}


def score(picks):
    """Run only the research half of the grader over a ledger."""
    mem = {"factor_scorecard": {}}
    G._research_scorecard(mem, picks)
    return mem["research_scorecard"]


def test_researched_and_model_only_are_scored_apart():
    card = score([
        pick("win", 0.91, [src("https://espn.com/a")]),
        pick("win", 0.91, [src("https://espn.com/b")]),
        pick("loss", -1.0),
    ])
    assert card["researched"]["picks"] == 2
    assert card["researched"]["wins"] == 2
    assert card["model only"]["picks"] == 1
    assert card["model only"]["losses"] == 1


def test_every_outlet_leaned_on_gets_its_own_row():
    card = score([
        pick("win", 0.91, [src("https://www.espn.com/a")]),
        pick("loss", -1.0, [src("https://theathletic.com/b")]),
    ])
    assert "source: espn.com" in card
    assert "source: theathletic.com" in card
    assert card["source: espn.com"]["wins"] == 1
    assert card["source: theathletic.com"]["losses"] == 1


def test_www_is_not_a_different_outlet():
    card = score([
        pick("win", 0.91, [src("https://www.espn.com/a")]),
        pick("win", 0.91, [src("https://espn.com/b")]),
    ])
    assert card["source: espn.com"]["picks"] == 2


def test_one_outlet_cited_twice_in_a_pick_counts_once():
    card = score([pick("win", 0.91, [src("https://espn.com/a"),
                                     src("https://espn.com/b")])])
    assert card["source: espn.com"]["picks"] == 1


def test_a_thin_row_carries_the_verdict_that_says_so():
    # 2 and 0 is not evidence, and a bare 100% invites acting on it.
    card = score([pick("win", 0.91, [src("https://espn.com/a")]),
                  pick("win", 0.91, [src("https://espn.com/b")])])
    assert card["researched"]["win_pct"] == 100.0
    assert card["researched"]["verdict"] == "no_signal"


def test_a_broken_url_does_not_become_an_outlet():
    card = score([pick("win", 0.91, [{"url": None, "quote": "x" * 40}])])
    assert not any(k.startswith("source: ") for k in card)
    assert card["researched"]["picks"] == 1


def test_shadow_picks_stay_out_of_it():
    # Below the publish line is not a published pick, and folding it in
    # would let unstaked plays flatter the record that gates staking.
    card = score([pick("win", 0.0, [src("https://espn.com/a")], conf=7.0),
                  pick("loss", -1.0, [src("https://espn.com/b")])])
    assert card["researched"]["picks"] == 1
    assert card["researched"]["losses"] == 1
