"""
The page, and the character speaking on it.

Phil is a persona wrapped around a ledger. That is fine right up until the
persona starts making claims the ledger does not support, so these tests
guard the seam: the voice stays in one place, the honest copy survives, and
the headline record can never be printed without the caveat that it means
nothing yet.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import build_site as B  # noqa: E402


def render(payload: dict) -> str:
    return (B.HTML
            .replace("__DATA__", json.dumps(payload))
            .replace("__VOICE__", json.dumps(B.VOICE))
            .replace("__NAME__", B.VOICE["name"])
            .replace("__TAGLINE__", B.VOICE["tagline"])
            .replace("__SUBHEAD__", B.VOICE["subhead"]))


# ------------------------------------------------------- the character

def test_the_voice_lives_in_one_place():
    """
    Swapping who speaks has to be a change to VOICE and nothing else, or
    the character calcifies into the markup and cannot be replaced.
    """
    body = B.HTML.split("<body>", 1)[1]
    for phrase in ("Your bookie never", "Nothing this week", "priced right"):
        assert phrase not in body, (
            f"{phrase!r} is hard coded into the markup instead of VOICE"
        )


@pytest.mark.parametrize("key", sorted(B.VOICE))
def test_no_em_dashes_anywhere_in_the_voice(key):
    """House style, and it applies to the character too."""
    assert "—" not in B.VOICE[key]
    assert "–" not in B.VOICE[key]


def test_the_page_says_phil_is_not_a_person():
    """
    A page whose whole argument is that it does not overclaim cannot open
    by implying a real handicapper is behind it.
    """
    about = B.VOICE["about"].lower()
    assert "not a person" in about


def test_the_helpline_is_not_in_character():
    """Nobody should have to parse a persona to find the helpline."""
    d = B.VOICE["disclaimer"]
    assert "1-800-GAMBLER" in d
    assert "I " not in d, "the disclaimer slipped into first person"


def test_every_voice_key_the_template_asks_for_exists():
    used = set(re.findall(r"V\.([a-z_]+)", B.HTML))
    missing = sorted(used - set(B.VOICE))
    assert missing == [], f"template reads VOICE keys that do not exist: {missing}"


def test_no_placeholder_survives_a_render():
    html = render({"picks": [], "overall": {}, "current": {}})
    for token in ("__DATA__", "__VOICE__", "__NAME__", "__TAGLINE__", "__SUBHEAD__"):
        assert token not in html


# ------------------------------------------- the record cannot overclaim

def test_the_headline_record_carries_a_verdict():
    """
    2 and 1 renders as a 66.7 percent win rate and a 27.3 percent return.
    Printing that without the caveat is the exact overclaiming this system
    is built against, and it shipped that way until this test existed.
    """
    payload = B.build_payload()
    assert "verdict" in payload["overall"]
    assert "verdict" in payload["overall_shadow"]


def test_a_thin_record_is_marked_no_signal():
    from lib.scoring import annotate, summarize
    thin = [{"result": "win", "units": 1.0, "units_net": 0.9, "confidence": 8.2},
            {"result": "win", "units": 1.0, "units_net": 0.9, "confidence": 8.4},
            {"result": "loss", "units": 1.0, "units_net": -1.0, "confidence": 8.1}]
    assert annotate(summarize(thin))["verdict"] == "no_signal"


def test_the_template_shows_the_caveat_when_there_is_no_signal():
    assert 'o.verdict === "no_signal"' in B.HTML
    assert "no_signal_long" in B.HTML


# ------------------------------------------------------- the ticket

def test_the_play_does_not_repeat_the_price():
    """The price has its own place on the ticket. It printed twice."""
    p = {"side": "Hawaii", "line": 5.5, "market": "spread",
         "period": "full", "price": -110}
    assert B.pick_title(p) == "Hawaii +5.5"
    assert "-110" not in B.pick_title(p)
    assert "-110" in B.pick_full(p)


def test_a_total_reads_without_a_sign():
    p = {"side": "Under", "line": 54.5, "market": "total",
         "period": "full", "price": -110}
    assert B.pick_title(p) == "Under 54.5"


def test_sources_reach_the_page():
    """Verified sources are the receipt, so they belong on the ticket."""
    payload = B.build_payload()
    assert all("sources" in p for p in payload["picks"])


# --------------------------------------------------------- the build

def test_the_page_builds_from_an_empty_ledger():
    """
    The most common state this page will ever be in. It has to render, and
    it has to say the short card is correct rather than looking broken.
    """
    payload = B.build_payload()
    html = render(payload)
    assert len(html) > 8000
    assert B.VOICE["card_empty"] in html


def test_building_the_site_never_touches_the_ledger():
    before = (ROOT / "data" / "picks.json").read_bytes()
    B.build_payload()
    assert (ROOT / "data" / "picks.json").read_bytes() == before
