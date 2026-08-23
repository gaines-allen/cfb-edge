"""
The page, and the character speaking on it.

Steve is a persona wrapped around a ledger. That is fine right up until the
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


# Every __PLACEHOLDER__ the template contains, read off the template
# rather than listed by hand. A new one added to the markup and forgotten
# in main() would otherwise ship unreplaced with no test to catch it.
PLACEHOLDERS = sorted(set(re.findall(r"__[A-Z]+__", B.HTML)))


def render(payload: dict) -> str:
    html = B.HTML.replace("__DATA__", json.dumps(payload)) \
                 .replace("__VOICE__", json.dumps(B.VOICE))
    for token in PLACEHOLDERS:
        key = token.strip("_").lower()
        if key in B.VOICE:
            html = html.replace(token, B.VOICE[key])
    return html


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


def test_the_page_says_the_character_is_not_a_person():
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


def test_every_placeholder_has_something_to_fill_it():
    """
    __NAME__ and friends are filled from VOICE. A template placeholder with
    no matching key would render literally on the page.
    """
    missing = [t for t in PLACEHOLDERS
               if t not in ("__DATA__", "__VOICE__")
               and t.strip("_").lower() not in B.VOICE]
    assert missing == [], f"no VOICE key for {missing}"


def test_no_placeholder_survives_a_render():
    html = render({"picks": [], "overall": {}, "current": {}})
    for token in PLACEHOLDERS:
        assert token not in html, f"{token} was never replaced"


def test_the_real_build_replaces_every_placeholder():
    """render() above is the test's own copy. This checks main()'s."""
    import subprocess
    import sys as _sys
    proc = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "build_site.py"), "--dry-run"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-400:]
    built = (ROOT / "site" / "index.html").read_text()
    for token in PLACEHOLDERS:
        assert token not in built, f"{token} is unreplaced in the built page"


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


# ------------------------------------------------- units on the chart

def test_breakeven_is_a_percentage_not_a_fraction():
    """
    BREAKEVEN is 52.4, not 0.524. The calibration chart multiplied it by
    100 again, so every bar was compared against 5240 percent, drew as a
    loss, and the breakeven marker was rendered off the chart. Nothing
    caught it because nothing had graded, so the chart was always empty.
    """
    assert B.BREAKEVEN == pytest.approx(52.4, abs=0.1)
    assert "100 * (DATA.breakeven" not in B.HTML
    assert "DATA.breakeven || 52.4" in B.HTML


def test_a_winning_bucket_would_draw_green():
    """
    The comparison the bug broke. A bucket above breakeven has to be on the
    winning side of the line.
    """
    assert 66.7 >= B.BREAKEVEN


def test_the_chart_colours_come_from_the_brand():
    """Chart colours drifted from the palette when the page was rebranded."""
    for stale in ("#6fbf73", "#d2645c", "#e8b04b", "#9b968c", "#f1ece2"):
        assert stale not in B.HTML, f"{stale} is from the old palette"


# ------------------------------------------------ he has to sound like him

def test_he_uses_contractions():
    """
    Without them he reads like a form letter, and a wise guy who talks like
    a form letter is not a wise guy.
    """
    spoken = " ".join(v for k, v in B.VOICE.items()
                      if k not in ("disclaimer", "name", "full_name", "logo",
                                   "kicker", "sign_off"))
    assert spoken.count("'") >= 8, "he is not using contractions"


def test_the_disclaimer_stays_straight():
    """
    The helpline is not a bit. It keeps the formal register even when
    everything around it loosens up.
    """
    d = B.VOICE["disclaimer"]
    for contraction in ("n't", "I'm", "it's", "there's"):
        assert contraction not in d


def test_american_spelling():
    joined = " ".join(B.VOICE.values())
    for british in ("apologise", "realise", "favourite", "colour"):
        assert british not in joined


def test_the_honest_lines_survived_the_rewrite():
    """
    The jokes are allowed to change. These are not, because they are the
    product rather than the packaging.
    """
    assert "52.4" in B.VOICE["no_signal_long"]
    assert "lucky" in B.VOICE["no_signal_long"]
    assert "priced right" in B.VOICE["card_empty"]
    assert "not a person" in B.VOICE["about"]
    assert "1-800-GAMBLER" in B.VOICE["disclaimer"]
