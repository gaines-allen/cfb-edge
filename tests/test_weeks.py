"""
Which week a run actually works.

The board runs weeks ahead, so filtering to the target week is what stops a
November game being rated off a preseason number. That makes the week
argument load bearing, and it used to be discarded silently when it was 0.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import ROOT, load_fixture, seed_cfbd_cache

sys.path.insert(0, str(ROOT / "scripts"))

from make_slate import resolve_week  # noqa: E402

SEASONS = [2023, 2024, 2025, 2026]


@pytest.mark.parametrize("season", SEASONS)
def test_cfbd_publishes_no_week_zero(season):
    """
    CFBD folds week 0 openers into week 1 for every season here, so a
    request for week 0 has no data behind it and must not be answered with
    a different week's slate.
    """
    cal = load_fixture("cfbd", f"calendar_{season}")
    weeks = {w["week"] for w in cal if w.get("seasonType") == "regular"}
    assert 0 not in weeks
    assert min(weeks) == 1


@pytest.mark.parametrize("season", SEASONS)
def test_week_zero_is_refused_rather_than_substituted(season):
    """
    0 is falsy, so "args.week or current_week(cal)" threw it away and
    returned whichever week the calendar thought was current. Asking for one
    week and silently getting another is the failure this repo exists to
    avoid.
    """
    cal = load_fixture("cfbd", f"calendar_{season}")
    with pytest.raises(ValueError, match="no week 0"):
        resolve_week(0, cal, season)


@pytest.mark.parametrize("bad", [0, -1, 99])
def test_a_week_outside_the_calendar_is_refused(bad):
    cal = load_fixture("cfbd", "calendar_2024")
    with pytest.raises(ValueError, match=f"no week {bad}"):
        resolve_week(bad, cal, 2024)


@pytest.mark.parametrize("week", [1, 5, 12, 15])
def test_a_real_week_passes_through_unchanged(week):
    cal = load_fixture("cfbd", "calendar_2024")
    assert resolve_week(week, cal, 2024) == week


def test_no_week_asked_for_falls_back_to_the_calendar():
    cal = load_fixture("cfbd", "calendar_2024")
    got = resolve_week(None, cal, 2024)
    assert isinstance(got, int) and got >= 1


def test_the_error_names_the_weeks_that_do_exist():
    """An error a human can act on without opening the code."""
    cal = load_fixture("cfbd", "calendar_2026")
    with pytest.raises(ValueError) as e:
        resolve_week(0, cal, 2026)
    assert "weeks 1 to 15" in str(e.value)
    assert "ask for week 1" in str(e.value)


def test_the_slate_refuses_week_zero_end_to_end(tmp_path):
    cache = seed_cfbd_cache(tmp_path / "cache", 2026, 1)
    import os
    env = dict(os.environ)
    env.pop("CFBD_API_KEY", None)
    env.pop("ODDS_API_KEY", None)
    env["CFBD_CACHE_DIR"] = str(cache)
    env["CFBD_CACHE_TTL"] = "-1"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_slate.py"),
         "--season", "2026", "--week", "0", "--dry-run"],
        capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert proc.returncode == 4, proc.stdout[:400]
    assert "no week 0" in proc.stderr
    assert "games_with_candidates" not in proc.stdout
