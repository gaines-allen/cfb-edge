"""
The thing that watches the thing.

On 26 August the 2 PM job failed a gate, published nothing, and the page
sat 28 hours stale for 3 hours. Nothing was broken about the detection.
The gate refused to publish and the page told readers its lines were 28
hours old. The failure was that both facts sat there with nobody looking.

A watchdog that reports healthy when it cannot tell is worse than none,
so most of what follows is about the ways this could lie.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import healthcheck as H  # noqa: E402

NOW = datetime(2026, 8, 26, 22, 0, tzinfo=timezone.utc)


def page(fetched_at: str | None) -> str:
    board = {"rows": [], "held": []}
    if fetched_at is not None:
        board["fetched_at"] = fetched_at
    return "<html>const DATA = " + json.dumps({"board": board}) + ";\n</html>"


def test_a_current_page_is_healthy():
    v = H.verdict(page("2026-08-26T18:00:00+00:00"), NOW, "success")
    assert v["healthy"]
    assert v["page_age_hours"] == 4.0


def test_a_page_past_the_limit_is_not():
    v = H.verdict(page("2026-08-25T14:00:00+00:00"), NOW, "success")
    assert not v["healthy"]
    assert "32.0 hours old" in v["problems"][0]


def test_the_exact_case_from_26_august():
    # Board last pulled 2026-08-25T18:23, checked at 22:22 the next day.
    when = datetime(2026, 8, 26, 22, 22, tzinfo=timezone.utc)
    v = H.verdict(page("2026-08-25T18:23:33+00:00"), when, "failure")
    assert not v["healthy"]
    assert len(v["problems"]) == 2, v["problems"]
    assert any("28.0 hours old" in p for p in v["problems"])
    assert any("failure" in p for p in v["problems"])


# ------------------------------------------------- the ways it could lie

def test_a_site_that_does_not_respond_is_unhealthy():
    # Silence is not good news. A check that treats a fetch failure as
    # "nothing to report" reports green straight through an outage.
    v = H.verdict(None, NOW, "success")
    assert not v["healthy"]
    assert "did not respond" in v["problems"][0]


def test_a_page_with_no_timestamp_is_unhealthy():
    v = H.verdict(page(None), NOW, "success")
    assert not v["healthy"]
    assert "no board timestamp" in v["problems"][0]


def test_an_unparseable_timestamp_is_unhealthy():
    v = H.verdict(page("last tuesday"), NOW, "success")
    assert not v["healthy"]


def test_garbage_instead_of_a_page_is_unhealthy():
    v = H.verdict("<html>404 not found</html>", NOW, "success")
    assert not v["healthy"]


def test_a_broken_payload_does_not_crash_the_check():
    v = H.verdict("const DATA = {not json};\n", NOW, "success")
    assert not v["healthy"]


def test_a_green_run_over_a_stale_page_still_fails():
    # A run can finish clean and publish nothing. The page is the fact.
    v = H.verdict(page("2026-08-24T18:00:00+00:00"), NOW, "success")
    assert not v["healthy"]


def test_a_failed_run_over_a_fresh_page_still_fails():
    # The page may be fresh from an earlier run while today's broke. That
    # is a warning, not a pass, because tomorrow's will break the same way.
    v = H.verdict(page("2026-08-26T18:00:00+00:00"), NOW, "failure")
    assert not v["healthy"]
    assert any("failure" in p for p in v["problems"])


@pytest.mark.parametrize("conclusion", ["success", "skipped", "", None])
def test_conclusions_that_are_not_failures_are_left_alone(conclusion):
    v = H.verdict(page("2026-08-26T20:00:00+00:00"), NOW, conclusion)
    assert v["healthy"], v["problems"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_every_other_conclusion_is_reported(conclusion):
    v = H.verdict(page("2026-08-26T20:00:00+00:00"), NOW, conclusion)
    assert not v["healthy"]


def test_the_watchdog_is_never_looser_than_the_page_it_watches():
    """
    The page takes the board down at its own limit. If this check tolerates
    more, there is a window where every reader sees a dead board and the
    watchdog reports healthy.

    It is imported rather than retyped, because two copies of one threshold
    drift the first time either is tuned. That exact mistake took the daily
    publish down twice on 26 August.
    """
    import build_site as B
    assert H.MAX_PAGE_AGE_HOURS <= B.MAX_BOARD_AGE_HOURS
    src = (ROOT / "scripts" / "healthcheck.py").read_text()
    assert "from build_site import MAX_BOARD_AGE_HOURS" in src


def test_the_report_names_the_age_so_it_can_be_acted_on():
    v = H.verdict(page("2026-08-25T00:00:00+00:00"), NOW, "success")
    assert v["page_age_hours"] == 46.0
    assert str(v["page_age_hours"]) in v["problems"][0]


# ------------------------------------------------------ pulling it back

def test_recovery_starts_before_readers_lose_the_board():
    """
    GitHub cron is best effort. On 27 August the 2 PM pull did not fire at
    all, which is documented behaviour under load. Waiting for a scheduler
    that already skipped is how a daily promise quietly becomes a
    sometimes promise.
    """
    assert H.RECOVER_AFTER_HOURS < H.MAX_PAGE_AGE_HOURS
    # And with real room, so recovery has time to run before the board
    # goes down rather than racing it.
    assert H.MAX_PAGE_AGE_HOURS - H.RECOVER_AFTER_HOURS >= 4


def test_a_normal_day_never_triggers_a_pull():
    # The daily job refreshes every 24 hours, so a healthy page is only
    # ever a few hours old when checked. If ordinary freshness tripped
    # recovery this would double every day's API spend.
    v = H.verdict(page("2026-08-26T18:00:00+00:00"), NOW, "success")
    assert not v["should_recover"]


def test_an_ageing_page_triggers_a_pull_while_still_healthy():
    when = NOW + timedelta(hours=17)
    v = H.verdict(page("2026-08-26T18:00:00+00:00"), when, "success")
    assert v["healthy"], "should still be serving readers"
    assert v["should_recover"], "and already pulling the next board"


def test_a_stale_page_triggers_a_pull_too():
    when = NOW + timedelta(hours=30)
    v = H.verdict(page("2026-08-26T18:00:00+00:00"), when, "failure")
    assert not v["healthy"]
    assert v["should_recover"]


@pytest.mark.parametrize("html", [None, "<html>404</html>",
                                  "const DATA = {bad};\n"])
def test_a_fault_a_pull_cannot_fix_does_not_trigger_one(html):
    # An unreachable site or an unparseable page is not a stale board.
    # Firing a data pull at it spends credits on the wrong problem and
    # tells nobody anything.
    v = H.verdict(html, NOW, "failure")
    assert not v["healthy"]
    assert not v["should_recover"]


# ------------------------------------------------------------- the card

def test_a_failed_card_is_reported_even_when_the_page_is_current():
    """
    The card is invisible to a page age check. A card that never opens
    leaves yesterday's board sitting there looking perfectly current, so
    on 27 August the card failed twice and the watchdog called it healthy
    both times.
    """
    v = H.verdict(page("2026-08-26T20:00:00+00:00"), NOW, "success",
                  last_card="failure")
    assert not v["healthy"]
    assert any("Weekly card" in p for p in v["problems"])


def test_the_card_warning_says_the_work_may_already_be_done():
    # It usually is. Everything upstream of the pull request passes and
    # the branch is pushed, so the failure mode is a card sitting on a
    # branch rather than research that has to be paid for again.
    v = H.verdict(page("2026-08-26T20:00:00+00:00"), NOW, "success",
                  last_card="failure")
    problem = [p for p in v["problems"] if "Weekly card" in p][0]
    assert "sitting on a branch" in problem
    assert "before starting another" in problem


@pytest.mark.parametrize("conclusion", ["success", "skipped", "", None])
def test_a_card_that_did_not_fail_is_left_alone(conclusion):
    v = H.verdict(page("2026-08-26T20:00:00+00:00"), NOW, "success",
                  last_card=conclusion)
    assert v["healthy"], v["problems"]


def test_a_failed_card_does_not_trigger_a_data_pull():
    # Wrong remedy. The board is current; it is the card that is stuck.
    v = H.verdict(page("2026-08-26T20:00:00+00:00"), NOW, "success",
                  last_card="failure")
    assert not v["should_recover"]
