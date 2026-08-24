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
    # Digits belong in the class. Without them V.edge_body_5 is read as
    # the key edge_body_, which does not exist, and the test fails on its
    # own regex rather than on anything real.
    used = set(re.findall(r"V\.([a-z_0-9]+)", B.HTML))
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


# ------------------------------------------- the whole page is him

def test_nothing_on_the_page_is_written_by_a_machine():
    """
    The provenance line used to read "Built 8/21/2026. Board pulled...
    Odds API credits left 465", which is a machine describing itself in the
    middle of a page that is meant to be one man talking.
    """
    assert "Built ${new Date" not in B.HTML
    assert "provenance" in B.VOICE
    assert B.VOICE["provenance"].startswith("I ")


def test_the_tab_title_is_his_too():
    assert B.VOICE["page_title"].startswith("Steve")
    assert "__PAGE_TITLE__" in B.HTML
    assert "- the card</title>" not in B.HTML


@pytest.mark.parametrize("label", [
    "stat_units", "stat_record", "stat_winrate", "stat_roi",
    "stat_clv", "stat_close", "stat_pending",
])
def test_the_figures_are_labelled_in_his_words(label):
    """
    ROI, AVG CLV and PENDING are report headers. He says return, line value
    and still out there.
    """
    assert label in B.VOICE and B.VOICE[label]


def test_no_report_jargon_survives_in_the_labels():
    labels = " ".join(B.VOICE[k] for k in B.VOICE if k.startswith(("stat_", "col_")))
    for jargon in ("ROI", "CLV", "Pending", "Avg"):
        assert jargon not in labels, f"{jargon} is report language, not his"


def test_no_table_header_is_hard_coded():
    """A column header typed into the markup is a header he cannot change."""
    body = B.HTML.split("<script>", 1)[1]
    for hard in ('["Week",', '["Record",', '["Units",', '["Reading",', '["Reason",'):
        assert hard not in body, f"{hard} is hard coded instead of coming from VOICE"


# ------------------------------------------- empty sections disappear

def test_empty_sections_hide_rather_than_apologize():
    """
    Before the card publishes and anything grades, 7 sections rendered a
    heading over a line saying nothing was there yet. That reads as
    scaffolding. A section with no data now hides itself, heading and all,
    and only the card and the board keep their empty lines, because those
    say something a visitor needs: when the card lands, and that the board
    goes up Monday.
    """
    body = B.HTML.split("<script>", 1)[1]
    assert "function hide(id)" in body
    for section in ("book", "cal", "cum", "wk", "split", "fac", "les"):
        assert f'hide("{section}")' in body, f"{section} never hides"


def test_the_card_and_board_keep_their_empty_lines():
    body = B.HTML.split("<script>", 1)[1]
    assert "V.card_empty" in body
    assert "V.board_empty" in body
    assert 'hide("card")' not in body
    assert 'hide("board")' not in body


# ---------------------------------------------------------------------
# Defending a pick
#
# Every lean and every published pick carries the arithmetic that produced
# it. These guard the two ways that goes wrong: the numbers stop matching
# the claim, or the claim stops sounding like a person said it.
# ---------------------------------------------------------------------

MODEL = {
    "home_team": "Ohio State Buckeyes",
    "away_team": "Ball State Cardinals",
    "hfa_applied": 2.2,
    "inputs": {
        "home_rating": 31.26, "away_rating": -22.31,
        "home_points": 38.9, "away_points": 10.3,
        "home_components": {"sp": 31.26, "fpi": 30.0},
        "away_components": {"sp": -22.31, "fpi": -20.0},
    },
}


def test_a_spread_defense_states_the_number_rather_than_the_ingredients():
    d = B.defend(MODEL, "spread", "Ohio State Buckeyes", -50.5, 2.17, -56.4)
    assert "56.4" in d["text"]
    assert "Ohio State Buckeyes" in d["text"]
    assert "Book's got it at 50.5." in d["text"]


def test_a_total_defense_adds_the_two_sides_up():
    d = B.defend(MODEL, "total", "Under", 56.5, 1.94, 50.1)
    assert "50.1" in d["text"] and "38.9" in d["text"] and "10.3" in d["text"]
    assert "Book's asking 56.5." in d["text"]


@pytest.mark.parametrize("market,number", [
    ("spread", -56.4), ("total", 50.1)])
def test_the_defense_quotes_the_number_printed_beside_it(market, number):
    # The page prints model_number in the row's metadata. Rebuilding the
    # number from the ratings instead lands 0.6 off, because the bias
    # correction sits between them, and a defense that disagrees with the
    # figure next to it is worse than no defense.
    d = B.defend(MODEL, market, "x", -50.5, 2.0, number)
    assert str(abs(number)) in d["text"]


def test_the_bias_correction_is_named_when_it_moves_the_number():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.0, -56.4)
    # Raw ratings give 55.77, published is 56.4. Both belong in the text.
    assert "55.8" in d["text"] and "56.4" in d["text"]
    assert "bias check" in d["text"]


def test_no_bias_line_when_the_correction_did_nothing():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.0, -55.8)
    assert "bias check" not in d["text"]


def test_a_moneyline_does_not_compare_a_price_to_a_margin():
    d = B.defend(MODEL, "moneyline", "Ball State Cardinals", -145, 1.2)
    assert "-145" not in d["text"]
    assert "just taking a side" in d["text"]


@pytest.mark.parametrize("sigma,phrase", [
    (0.80, "nothing special"),
    (1.50, "wider gap than most"),
    (2.10, "top tenth"),
    (2.40, "as far apart as me and the book ever get"),
])
def test_the_sigma_tiers_actually_separate(sigma, phrase):
    # The live board runs a median near 1.0 and a max near 2.4. A tier set
    # that fires "widest on the board" at 1.9 said it on six picks at once,
    # which is the same as saying nothing.
    assert phrase in B.defend(MODEL, "spread", "x", -50.5, sigma,
                              -56.4)["text"]


def test_a_missing_rating_source_is_named_not_hidden():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.0)
    assert "SP+ and FPI" in d["built"]
    assert "SRS and Elo" in d["built"]
    assert "2 opinions instead of 4" in d["built"]


def test_a_defense_survives_a_model_with_nothing_in_it():
    d = B.defend({}, "spread", "x", -3.5, 1.0)
    assert d["line"] is None
    assert isinstance(d["text"], str)


def test_the_same_matchup_keeps_the_same_phrasing_between_pulls():
    # The card is rebuilt every afternoon. A defense that rewords itself
    # while the pick underneath has not moved reads like new information.
    first = B.defend(MODEL, "total", "Under", 56.5, 1.94)["text"]
    second = B.defend(MODEL, "total", "Under", 56.5, 1.94)["text"]
    assert first == second


def test_a_shared_caveat_is_hoisted_off_the_rows():
    rows = [B.defend(MODEL, "spread", str(i), -50.5, 2.0) for i in range(3)]
    shared = B.hoist_built(rows)
    assert shared and "SRS and Elo" in shared
    for r in rows:
        assert r["built"] == ""
        assert "SRS and Elo" not in r["text"]


def test_a_caveat_that_differs_by_row_stays_on_the_rows():
    full = {**MODEL, "inputs": {**MODEL["inputs"],
                                "home_components": {"sp": 1, "fpi": 1,
                                                    "srs": 1, "elo": 1},
                                "away_components": {"sp": 1, "fpi": 1,
                                                    "srs": 1, "elo": 1}}}
    rows = [B.defend(MODEL, "spread", "a", -50.5, 2.0),
            B.defend(full, "spread", "b", -50.5, 2.0)]
    assert B.hoist_built(rows) is None
    assert all(r["built"] for r in rows)


def test_the_running_card_row_shows_both_teams_and_their_logos():
    row = re.search(r"const row = \(c, gone\).*?\n    </div></div>`;",
                    B.HTML, re.S).group(0)
    for field in ("c.away_team", "c.home_team", "c.away_logo", "c.home_logo",
                  "c.defense"):
        assert field in row, field


def test_the_published_ticket_carries_the_model_case_too():
    assert "p.defense && p.defense.text" in B.HTML
    assert "V.pick_numbers" in B.HTML


def test_the_explainer_sits_below_the_record():
    order = re.findall(r'id="(\w+)-h"', B.HTML)
    assert order.index("edge") > order.index("les")
    assert order[-1] == "edge"
