"""
Opening the cited page and checking the claim is really on it.

Before this, a source was checked for a url starting with http and a date
inside 14 days, and nothing ever opened it. An agent could paraphrase a
coach, cite the wrong page, or invent a quote, and the card passed clean.
The product is the judgment, so a judgment resting on an unread source must
not look identical to one resting on a read source.

Every test here uses a fake fetcher. The suite stays offline, so a pull
request from a fork runs exactly what a branch runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from verify_sources import check_source, normalize, page_text, verify

FRESH = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
QUOTE = ("Layne has no timeline and we are planning for him to miss the "
         "opener, Eck said.")
PAGE = (f"<html><head><style>p{{color:red}}</style></head><body>"
        f"<h1>Camp report</h1><p>{QUOTE}</p>"
        f"<script>var x=1;</script></body></html>")


def fake(page: str | None, detail: str = "http 200"):
    return lambda url, timeout=20: (page, detail)


def source(**over):
    s = {"url": "https://example.com/report", "publisher": "Beat",
         "date": FRESH, "claim": "the quarterback misses the opener",
         "quote": QUOTE}
    s.update(over)
    return s


# ----------------------------------------------------------- the check

def test_a_quote_on_the_page_verifies():
    got = check_source(source(), fetcher=fake(PAGE))
    assert got["status"] == "verified"


def test_a_quote_not_on_the_page_is_caught():
    """The case that matters: a real page cited for something it never said."""
    got = check_source(source(), fetcher=fake("<p>Nothing of the sort.</p>"))
    assert got["status"] == "quote_not_found"
    assert "does not contain" in got["detail"]


def test_a_paraphrase_does_not_pass():
    """
    An agent recalling the gist rather than opening the page produces text
    close to the truth and not on it. That has to fail.
    """
    got = check_source(
        source(quote="Eck said Layne will probably not play in week one."),
        fetcher=fake(PAGE))
    assert got["status"] == "quote_not_found"


def test_an_unreachable_page_is_caught():
    got = check_source(source(), fetcher=fake(None, "http 404"))
    assert got["status"] == "unreachable"
    assert "404" in got["detail"]


def test_a_missing_quote_is_caught():
    got = check_source(source(quote=""), fetcher=fake(PAGE))
    assert got["status"] == "no_quote"


def test_a_trivial_quote_is_caught():
    got = check_source(source(quote="he is out"), fetcher=fake(PAGE))
    assert got["status"] == "quote_too_short"


def test_markup_and_scripts_do_not_hide_the_quote():
    assert QUOTE.lower() in normalize(page_text(PAGE))
    assert "var x=1" not in normalize(page_text(PAGE))
    assert "color:red" not in normalize(page_text(PAGE))


@pytest.mark.parametrize("rendered", [
    QUOTE.replace("'", "’"),
    QUOTE.replace(" ", "\n  "),
    QUOTE.replace("-", "—"),
    f"&nbsp;{QUOTE}&nbsp;",
])
def test_typography_differences_do_not_cause_a_false_alarm(rendered):
    """
    A site rendering curly quotes or wrapping lines is not the agent lying,
    and a check that cries wolf on formatting gets switched off.
    """
    got = check_source(source(), fetcher=fake(f"<p>{rendered}</p>"))
    assert got["status"] == "verified"


# -------------------------------------------------------- the whole card

def pick(conf, sources):
    return {"matchup": "A @ B", "side": "B", "confidence": conf,
            "sources": sources}


def test_a_live_pick_with_a_verified_source_passes():
    rep = verify([pick(8.4, [source()])], fetcher=fake(PAGE))
    assert rep["passed"] is True
    assert rep["unverified_on_live_picks"] == []


def test_a_live_pick_with_no_verifiable_source_fails():
    rep = verify([pick(8.4, [source()])], fetcher=fake(None, "http 404"))
    assert rep["passed"] is False
    assert any("not 1 of its" in p for p in rep["unverified_on_live_picks"])


def test_one_verified_source_carries_the_pick_but_the_rest_are_named():
    """
    A paywalled second source should not sink a pick whose main claim is
    confirmed, but it must be listed so a person knows to read it.
    """
    pages = {"https://good.example/a": PAGE, "https://paywall.example/b": None}

    def fetcher(url, timeout=20):
        body = pages.get(url)
        return (body, "http 200") if body else (None, "http 403")

    rep = verify([pick(8.4, [source(url="https://good.example/a"),
                             source(url="https://paywall.example/b")])],
                 fetcher=fetcher)
    assert rep["passed"] is True
    assert any("paywall.example" in p for p in rep["unverified_on_live_picks"])


def test_a_shadow_pick_is_reported_but_never_blocks():
    rep = verify([pick(6.2, [source()])], fetcher=fake(None, "http 404"))
    assert rep["passed"] is True
    assert rep["unverified_on_live_picks"] == []


def test_the_report_states_what_it_cannot_check():
    """
    A verified source means the sentence is on the page. It says nothing
    about whether the page is right, and the report must not imply it does.
    """
    rep = verify([pick(8.4, [source()])], fetcher=fake(PAGE))
    note = rep["note"].lower()
    assert "does not mean" in note
    assert "credible" in note or "accurate" in note


def test_counts_are_reported():
    rep = verify([pick(8.4, [source(), source()])], fetcher=fake(PAGE))
    assert rep["sources_checked"] == 2
    assert rep["sources_verified"] == 2
