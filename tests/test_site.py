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
from lib import model  # noqa: E402


# Every __PLACEHOLDER__ the template contains, read off the template
# rather than listed by hand. A new one added to the markup and forgotten
# in main() would otherwise ship unreplaced with no test to catch it.
# [A-Z_]+ rather than [A-Z]+, which silently skipped every
# placeholder with an underscore in it, __PAGE_TITLE__ included.
PLACEHOLDERS = sorted(set(re.findall(r"__[A-Z_]+__", B.HTML)))


def render(payload: dict) -> str:
    html = B.HTML.replace("__DATA__", json.dumps(payload)) \
                 .replace("__VOICE__", json.dumps(B.VOICE))
    # Not every placeholder is copy. This one carries the staleness
    # threshold so the page and the builder cannot hold two different
    # numbers for it.
    html = html.replace("__MAX_BOARD_AGE__", str(B.MAX_BOARD_AGE_HOURS))
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

    The exceptions are the ones that are not copy: the payload, the voice
    table itself, and the staleness threshold, which is injected so the
    page and the builder cannot end up holding two different numbers for
    it. Each is filled explicitly in main().
    """
    NOT_COPY = ("__DATA__", "__VOICE__", "__MAX_BOARD_AGE__")
    missing = [t for t in PLACEHOLDERS
               if t not in NOT_COPY
               and t.strip("_").lower() not in B.VOICE]
    assert missing == [], f"no VOICE key for {missing}"
    # And every one of those is actually substituted somewhere.
    src = (ROOT / "scripts" / "build_site.py").read_text()
    for token in NOT_COPY:
        assert f'"{token}"' in src, f"{token} is never filled"


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
    joined = " ".join(B.VOICE.values()).lower()
    for british in ("apologise", "realise", "favourite", "colour"):
        assert british not in joined


def test_the_honest_lines_survived_the_rewrite():
    """
    The jokes are allowed to change. These are not, because they are the
    product rather than the packaging.
    """
    assert "52.5" in B.VOICE["no_signal_long"]
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
    """
    The board always says something a visitor needs, so it never hides.
    The card keeps its empty line too, but only inside the window it is
    promised in. Monday through Thursday an empty card is scaffolding.
    """
    body = B.HTML.split("<script>", 1)[1]
    assert "V.card_empty" in body
    assert "V.board_empty" in body
    assert 'hide("board")' not in body


def test_the_card_stays_down_until_the_day_it_is_promised():
    """
    The card publishes Wednesday afternoon and covers Thursday night
    through Sunday, so Monday and Tuesday are the only days an empty card
    section is scaffolding rather than news.
    """
    body = B.HTML.split("<script>", 1)[1]
    assert "function cardWindow()" in body
    assert '["Wed", "Thu", "Fri", "Sat", "Sun"]' in body
    # The window gates the empty state only. A Thursday game that earns
    # its place brings the card back on its own.
    card = body[body.index("if (!live.length) {"):]
    assert 'hide("card")' in card[:200]
    assert "cardWindow()" in card[:200]


def test_a_failed_timezone_lookup_shows_the_card_rather_than_hiding_it():
    body = B.HTML.split("<script>", 1)[1]
    window = body[body.index("function cardWindow()"):]
    window = window[:window.index("function hide(id)")]
    assert "catch" in window and "return true" in window


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
    d = B.defend(MODEL, "spread", "Ohio State Buckeyes", -50.5, 2.17,
                 -56.4, 5.9)
    assert "56.5" in d["text"]
    assert "Ohio State Buckeyes" in d["text"]
    assert "Book's got it at 50.5." in d["text"]


def test_a_total_defense_adds_the_two_sides_up():
    d = B.defend(MODEL, "total", "Under", 56.5, 1.94, 50.1, -6.4)
    assert "50.0" in d["text"] and "39.0" in d["text"] and "10.5" in d["text"]
    assert "Book's asking 56.5." in d["text"]


@pytest.mark.parametrize("market,number,shown", [
    ("spread", -56.4, "56.5"), ("total", 50.1, "50.0")])
def test_the_defense_quotes_the_number_printed_beside_it(market, number,
                                                         shown):
    # The page prints model_number in the row's metadata. Rebuilding the
    # number from the ratings instead lands 0.6 off, because the bias
    # correction sits between them, and a defense that disagrees with the
    # figure next to it is worse than no defense.
    d = B.defend(MODEL, market, "x", -50.5, 2.0, number, 5.9)
    assert shown in d["text"]


def test_the_bias_correction_is_named_when_it_moves_the_number():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.0, -56.4, 5.9)
    # Raw ratings give 55.77, published is 56.4. Both belong in the text.
    assert "56.0" in d["text"] and "56.5" in d["text"]
    assert "nudge it" in d["text"]


def test_no_bias_line_when_the_correction_did_nothing():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.0, -55.8, 5.9)
    assert "nudge it" not in d["text"]


def test_a_moneyline_does_not_compare_a_price_to_a_margin():
    d = B.defend(MODEL, "moneyline", "Ball State Cardinals", -145, 1.2,
                 -56.4, 5.9)
    assert "-145" not in d["text"]
    assert "just taking a side" in d["text"]


@pytest.mark.parametrize("scaled,phrase", [
    (0.80, "nothing special"),
    (1.50, "bigger gap than most"),
    (2.10, "top tenth"),
    (2.40, "as far apart as me and the book ever get"),
])
def test_the_gap_tiers_actually_separate(scaled, phrase):
    # The live board runs a median near 1.0 and a max near 2.4. A tier set
    # that fires "widest on the board" at 1.9 said it on six picks at once,
    # which is the same as saying nothing. The scaled figure picks the
    # tier; only the points gap is ever printed.
    assert phrase in B.defend(MODEL, "spread", "x", -50.5, scaled,
                              -56.4, 5.9)["text"]


def test_a_missing_rating_source_is_named_not_hidden():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.0, -56.4, 5.9)
    assert "SP+ and FPI" in d["built"]
    assert "SRS and Elo" in d["built"]
    assert "2 opinions instead of 4" in d["built"]


def test_a_defense_survives_a_model_with_nothing_in_it():
    d = B.defend({}, "spread", "x", -3.5, 1.0, None, 2.0)
    assert d["line"] is None
    assert isinstance(d["text"], str)


def test_the_same_matchup_keeps_the_same_phrasing_between_pulls():
    # The card is rebuilt every afternoon. A defense that rewords itself
    # while the pick underneath has not moved reads like new information.
    first = B.defend(MODEL, "total", "Under", 56.5, 1.94, 50.1, -6.4)["text"]
    second = B.defend(MODEL, "total", "Under", 56.5, 1.94, 50.1, -6.4)["text"]
    assert first == second


def test_a_shared_caveat_is_hoisted_off_the_rows():
    rows = [B.defend(MODEL, "spread", str(i), -50.5, 2.0, -56.4, 5.9)
            for i in range(3)]
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
    rows = [B.defend(MODEL, "spread", "a", -50.5, 2.0, -56.4, 5.9),
            B.defend(full, "spread", "b", -50.5, 2.0, -56.4, 5.9)]
    assert B.hoist_built(rows) is None
    assert all(r["built"] for r in rows)


def test_the_published_ticket_carries_the_model_case_too():
    assert "p.defense && p.defense.text" in B.HTML
    assert "V.pick_numbers" in B.HTML


def test_the_explainer_sits_below_the_record():
    order = re.findall(r'id="(\w+)-h"', B.HTML)
    assert order.index("edge") > order.index("les")
    assert order[-1] == "edge"


# ---------------------------------------------------------------------
# Plain numbers, plain words
#
# A book prices in halves and a bettor reads in halves. Precision the
# model cannot justify reads as authority it has not earned, and a Greek
# letter on a betting page sends a reader to a search engine.
# ---------------------------------------------------------------------

@pytest.mark.parametrize("raw,shown", [
    (7.13, "7.0"), (6.84, "7.0"), (6.6, "6.5"), (54.3, "54.5"),
    (28.1, "28.0"), (0, "0.0"), (-3.26, "-3.5"),
])
def test_every_printed_number_lands_on_a_half(raw, shown):
    assert B.half(raw) == shown


def test_half_leaves_nothing_alone():
    assert B.half(None) is None
    assert B.half(2.0, signed=True) == "+2.0"
    assert B.half(-2.0, signed=True) == "-2.0"


def test_the_javascript_rounds_the_same_way():
    # Two rounding rules, one in Python and one in the page, would show a
    # different number in the defense than in the row above it.
    assert "Math.round(Number(n) * 2) / 2" in B.HTML


def test_no_greek_reaches_the_reader():
    for key, text in B.VOICE.items():
        assert "sigma" not in text.lower(), key
        assert "σ" not in text, key
    markup = re.sub(r"\{[^{}]*\}", "", B.HTML)
    assert "&sigma;" not in markup
    assert "σ" not in markup


def test_the_defense_counts_in_points_not_multiples():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.17, -56.4, 5.9)
    assert "5.9" not in d["text"]      # the raw gap, rounded off
    assert "6.0 points between us" in d["text"]


def test_a_defense_without_a_gap_says_nothing_about_the_gap():
    d = B.defend(MODEL, "spread", "x", -50.5, 2.17, -56.4, None)
    assert "points between us" not in d["text"]


# ---------------------------------------------------------------------
# The board
#
# A spread is stored from the home team's side, so -50.5 alone names no
# favourite. On the board that is the one thing a reader is looking for.
# ---------------------------------------------------------------------

def test_the_board_names_who_is_favoured():
    assert "const fav = (g, sp)" in B.HTML
    assert "g.home_short || g.home_team" in B.HTML
    assert "pick 'em" in B.HTML


def test_the_board_carries_short_names_for_the_favourite_label():
    rows = B.board_rows()["rows"]
    if not rows:
        pytest.skip("no board built")
    assert any(r.get("home_short") for r in rows)
    # The short name comes off the model, never off a guess at the mascot.
    #
    # Compared on the normalized key rather than as a substring. The two
    # names come from different sources and punctuate differently: CFBD
    # writes Hawai'i with the okina, the odds board writes Hawaii Rainbow
    # Warriors, and a raw "in" check calls that a bug. It is the same
    # school, which is the entire reason normalize() exists.
    from lib.teams import normalize
    for r in rows:
        if r.get("home_short"):
            assert normalize(r["home_team"]).startswith(
                normalize(r["home_short"])), r["home_team"]


def test_each_board_team_keeps_its_logo_beside_its_name():
    # .gteams used to expand between the away name and the home logo,
    # parking that logo against the numbers instead of its own team.
    assert '<span class="gside">' in B.HTML
    assert ".gside { display: inline-flex" in B.HTML


# ---------------------------------------------------------------------
# No contradictions
#
# The spread came off a blended rating and the total came off SP+ offense
# and defense, and nothing made them agree. The page ran both: Ohio State
# by 56.5 on one row, and 39.0 to 10.5 on the next, which is by 28.5.
# Same game, same card, two different games.
# ---------------------------------------------------------------------

def implied_margin(defense: dict) -> float | None:
    """The margin a total's defense implies, read back out of its text."""
    nums = re.findall(r"(\d+\.\d)", (defense or {}).get("line") or "")
    return abs(float(nums[0]) - float(nums[1])) if len(nums) >= 2 else None


def test_no_two_leans_on_one_game_describe_different_games():
    payload = B.build_payload()
    running = payload.get("running") or {}
    by_game = {}
    for c in running.get("card", []) + running.get("dropped", []):
        by_game.setdefault((c.get("home_team"), c.get("away_team")), []) \
            .append(c)
    for game, leans in by_game.items():
        margins = [m for m in (implied_margin(c.get("defense"))
                               for c in leans) if m is not None]
        if len(margins) < 2:
            continue
        assert max(margins) - min(margins) <= model.COHERENCE_TOLERANCE, game


def test_a_game_the_model_argues_with_itself_about_never_reaches_the_page():
    broken = {"model": {"projected_spread": -56.4,
                        "inputs": {"home_points": 38.9, "away_points": 10.3}}}
    assert B.incoherent(broken)
    fine = {"model": {"projected_spread": -20.2,
                      "inputs": {"home_points": 30.3, "away_points": 10.1}}}
    assert not B.incoherent(fine)


def test_the_gate_is_recomputed_rather_than_trusted():
    # A slate built before the flag existed carries no flag. Missing must
    # not read as fine.
    assert B.incoherent({"model": {"projected_spread": -56.4,
                                   "inputs": {"home_points": 38.9,
                                              "away_points": 10.3}}})
    # And an explicit flag is honoured even when the numbers look calm.
    assert B.incoherent({"incoherent": True, "model": {}})


def test_a_held_game_says_which_one_and_why():
    assert "{games}" in B.VOICE["board_incoherent"]
    assert "{count}" in B.VOICE["board_incoherent"]
    assert "disagreed with each other" in B.VOICE["board_incoherent"]


def test_a_published_pick_on_a_broken_game_keeps_its_reason_but_not_my_number():
    broken = {"e1": {"model": {"projected_spread": -56.4,
                               "inputs": {"home_points": 38.9,
                                          "away_points": 10.3}},
                     "candidates": []}}
    pick = {"event_id": "e1", "market": "spread", "period": "full",
            "side": "Home", "model_number": -56.4}
    assert B.pick_defense(broken, pick) is None


def test_a_published_pick_on_a_broken_game_keeps_its_reason_but_not_my_number():
    broken = {"e1": {"model": {"projected_spread": -56.4,
                               "inputs": {"home_points": 38.9,
                                          "away_points": 10.3}},
                     "candidates": []}}
    pick = {"event_id": "e1", "market": "spread", "period": "full",
            "side": "Home", "model_number": -56.4}
    assert B.pick_defense(broken, pick) is None




# ---------------------------------------------------------------------
# A page that stops rebuilding
#
# The staleness gate was decided in Python when the page was built, and a
# page is only built when the job succeeds. So the one situation it exists
# for, the job silently stopping, was the one situation it could not see:
# the last good page stayed up reporting an age of 0 hours on lines that
# could be a week old.
# ---------------------------------------------------------------------

def test_the_age_is_measured_against_the_reader_clock():
    body = B.HTML.split("<script>", 1)[1]
    assert "function boardAgeHours()" in body
    assert "Date.now()" in body
    assert "fetched_at" in body


def test_the_gate_does_not_trust_the_flag_baked_at_build_time():
    body = B.HTML.split("<script>", 1)[1]
    stale = body[body.index("function boardStale()"):]
    stale = stale[:stale.index("\n}")]
    # The baked flag may only widen the result, never narrow it.
    assert "||" in stale
    assert "MAX_BOARD_AGE_HOURS" in stale


def test_the_threshold_reaches_the_page_rather_than_being_retyped():
    # Two copies of 26 drift apart the first time one is tuned.
    assert "__MAX_BOARD_AGE__" in B.HTML
    built = (ROOT / "site" / "index.html").read_text()
    assert f"MAX_BOARD_AGE_HOURS = {B.MAX_BOARD_AGE_HOURS}" in built
    assert "__MAX_BOARD_AGE__" not in built


def test_a_stale_warning_is_not_hidden_behind_a_toggle():
    # Nobody opens a drawer to discover it is empty.
    body = B.HTML.split("<script>", 1)[1]
    board = body[body.index("(function renderBoard()"):]
    assert 'el("board-alert")' in board[:900]
    assert 'hide("board-fold")' in board[:900]
    assert '<div id="board-alert"></div>' in B.HTML


def test_the_public_record_counts_the_card_and_nothing_else():
    """
    Every game of the week is scored by scripts/review_week.py so the
    model can be learned from on 40 games instead of 2. None of that is
    the record. What Steve is graded on publicly is the card: the live
    picks, the ones that were published before kickoff with units on them.
    """
    src = (ROOT / "scripts" / "build_site.py").read_text()
    line = next(ln for ln in src.splitlines() if "settled = [" in ln)
    assert "live" in line, line
    # And the review must not be able to reach the ledger at all.
    review = (ROOT / "scripts" / "review_week.py").read_text()
    assert "picks.json" not in review and "load_picks" not in review


# ---------------------------------------------------------------------
# Consistency
#
# 30 August: the card read "NC State +4.5" and best bets read "NC State
# 13.5" on the same page. Both numbers were true. The card froze at the
# price taken on the 28th, the board moved 9 points by the 29th, and
# nothing said which was which. Best bets was also still recommending
# that game 18 hours after kickoff, and Memphis at UNLV 11 hours after.
#
# Two true numbers with nothing distinguishing them is a contradiction to
# everyone except the person who wrote the code.
# ---------------------------------------------------------------------

def _running(entries, now_offset_hours=24):
    import datetime as dt
    when = (dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(hours=now_offset_hours)).isoformat()
    leans, keys = {}, []
    for i, e in enumerate(entries):
        k = f"k{i}"
        leans[k] = {"matchup": e.get("matchup", f"A{i} @ H{i}"),
                    "home_team": e.get("home", f"H{i}"),
                    "away_team": e.get("away", f"A{i}"),
                    "side": "Over", "market": "total", "period": "full",
                    "line": e.get("line", 50.0), "price": -110,
                    "confidence": e.get("conf", 6.0),
                    "kickoff": e.get("kickoff", when),
                    "history": [{}], "rank": i + 1, "on_board": True}
        keys.append(k)
    return {"card": keys, "leans": leans, "dropped": []}


def test_a_game_that_already_kicked_is_not_a_best_bet(tmp_path, monkeypatch):
    import datetime as dt
    past = (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=3)).isoformat()
    raw = _running([{"kickoff": past}, {}])
    monkeypatch.setattr(B.store, "_load",
                        lambda p, d=None: raw if "running" in str(p) else {})
    out = B.running_card()
    assert out is None or len(out["card"]) == 1


def test_the_running_card_gives_a_second_read_no_rank():
    src = (ROOT / "scripts" / "build_running_card.py").read_text()
    block = src[src.index("Only a first opinion gets a rank"):]
    block = block[:block.index("# Best rank")] if "# Best rank" in block else block
    assert 'e["rank"] = None' in block
    assert "second_opinion" in block


def test_the_page_enforces_one_pick_per_game_itself():
    # The file is written by another script on another schedule.
    src = (ROOT / "scripts" / "build_site.py").read_text()
    assert "spoken_for" in src
    block = src[src.index("One pick per game, enforced again here"):]
    assert "continue" in block[:600]


def test_a_moved_line_is_labelled_rather_than_left_to_look_like_a_typo():
    assert "board_line_now" in B.HTML or "p.board_line" in B.HTML
    copy = B.VOICE["pick_line_moved"]
    assert "{taken}" in copy and "{now}" in copy
    assert "graded at the price it went up at" in copy


def test_the_board_line_is_only_reported_when_it_actually_moved():
    slate = {"e1": {"candidates": [
        {"market": "spread", "period": "full", "side": "NC State",
         "bet_line": 4.5}]}}
    same = {"event_id": "e1", "market": "spread", "period": "full",
            "side": "NC State Wolfpack", "line": 4.5}
    assert B.board_line_now(slate, same) is None
    moved = {**same, "line": 13.5}
    assert B.board_line_now(slate, moved) == 4.5


def test_a_full_name_and_a_short_name_are_the_same_side():
    # The ledger stores "NC State Wolfpack", a candidate stores "NC State".
    # An exact match found nothing, so the card showed no model number and
    # could not tell its line had moved 9 points.
    assert B.same_side("NC State", "NC State Wolfpack")
    assert B.same_side("Hawai'i", "Hawaii Rainbow Warriors")
    assert B.same_side("Ohio State", "Ohio State Buckeyes")
    assert B.same_side("Over", "Over")
    assert not B.same_side("Over", "Under")
    assert not B.same_side(None, "Over")
    # Resolved through the mascot map, never by prefix. A prefix rule
    # accepts NC State Wolfpack and also accepts Ohio for Ohio State,
    # which is the exact failure teams.py refuses to allow.
    assert not B.same_side("Ohio", "Ohio State")
    src = (ROOT / "scripts" / "build_site.py").read_text()
    block = src[src.index("def same_side("):src.index("def board_line_now(")]
    assert "canonical(" in block
    assert "startswith" not in block


def test_the_card_does_not_show_a_weekend_that_is_over():
    """
    CFBD week 1 runs 29 August to 8 September, so 2 separate weekends
    share a week number. Filtering the card on the week alone meant that
    on Wednesday it still presented the previous Saturday's settled
    losses as this week's plays, while best bets correctly looked ahead.
    """
    body = B.HTML.split("<script>", 1)[1]
    block = body[body.index("const CARD_TAIL_HOURS"):]
    assert "CARD_TAIL_HOURS = 48" in block[:200]
    assert "stillCurrent(p)" in block[:900]
    # A pick with no kickoff must still show. Hiding a real pick is worse
    # than showing a stale one.
    assert "if (!p.kickoff) return true;" in block
    assert "Number.isNaN(ko)) return true;" in block


def test_the_tail_keeps_saturday_up_through_sunday():
    # Long enough that a Saturday card survives Sunday morning grading,
    # short enough to be gone by Monday.
    body = B.HTML.split("<script>", 1)[1]
    tail = int(body.split("CARD_TAIL_HOURS = ")[1].split(";")[0])
    assert 36 <= tail <= 60


def test_the_record_is_not_filtered_by_recency():
    # The card section is a view. The record counts every settled pick
    # for the season and must never inherit this window.
    src = (ROOT / "scripts" / "build_site.py").read_text()
    line = next(ln for ln in src.splitlines() if "settled = [" in ln)
    assert "kickoff" not in line and "TAIL" not in line


def test_there_is_one_card_and_no_leans_section():
    """
    The page carried a second list, 6 daily leans that moved with the
    lines, above the card. One card on Wednesday, 6 fully researched
    picks, tracked all week. The leans section is gone and cannot come
    back by accident.
    """
    assert 'id="running"' not in B.HTML
    assert 'id="running-h"' not in B.HTML
    assert "renderRunning" not in B.HTML
    assert "Six arguments" not in B.HTML
    nav = B.HTML[B.HTML.index("<nav"):B.HTML.index("</nav>")] if "<nav" in B.HTML else ""
    assert "#running-h" not in nav
