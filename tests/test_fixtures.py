"""
The fixtures themselves.

Phase 2 replays these to backtest 2023 through 2025, so a fixture that goes
missing or drifts from its manifest entry is a backtest that quietly covers
less than it claims.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from conftest import FIXTURES

BACKTEST_SEASONS = [2023, 2024, 2025]
WEEKS = list(range(1, 16))


@pytest.fixture(scope="module")
def entries(manifest):
    return {e["name"]: e for e in manifest["fixtures"]}


def test_manifest_matches_what_is_on_disk(entries):
    on_disk = {p.stem for p in FIXTURES.rglob("*.json") if p.name != "MANIFEST.json"}
    missing = sorted(set(entries) - on_disk)
    unlisted = sorted(on_disk - set(entries))
    assert missing == [], f"the manifest lists fixtures that are gone: {missing}"
    assert unlisted == [], f"fixtures on disk with no manifest entry: {unlisted}"


def test_every_fixture_still_hashes_to_its_manifest_entry(entries):
    """A hand edited fixture is a silently rewritten test."""
    bad = []
    for name, e in entries.items():
        p = FIXTURES / e["source"] / f"{name}.json"
        payload = json.loads(p.read_text())
        got = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        if got != e["sha256"]:
            bad.append(name)
    assert bad == [], (
        f"{len(bad)} fixtures no longer match the manifest. Rerun "
        f"scripts/refresh_fixtures.py rather than editing a fixture by hand."
    )


@pytest.mark.parametrize("season", BACKTEST_SEASONS)
@pytest.mark.parametrize("kind", ["games", "lines", "elo"])
def test_every_backtest_week_is_captured(season, kind):
    """
    Phase 2 walks 15 weeks of 3 seasons. A gap here is a week the backtest
    would skip without saying so.
    """
    missing = [w for w in WEEKS
               if not (FIXTURES / "cfbd" / f"{kind}_{season}_w{w:02d}.json").exists()]
    assert missing == [], f"{kind} {season} is missing weeks {missing}"


@pytest.mark.parametrize("season", BACKTEST_SEASONS)
def test_season_ratings_are_captured(season):
    for kind in ("calendar", "sp", "fpi", "srs", "teams_fbs"):
        assert (FIXTURES / "cfbd" / f"{kind}_{season}.json").exists(), \
            f"{kind}_{season} is missing"


def test_a_full_fanduel_board_is_captured():
    board = json.loads((FIXTURES / "odds_api" / "board_current.json").read_text())
    assert len(board) > 50


def test_the_espn_teams_page_is_captured():
    pages = sorted(FIXTURES / "espn" for _ in [0])
    files = sorted((FIXTURES / "espn").glob("teams_page*.json"))
    assert len(files) >= 5, "the mascot map needs the endpoint paged, not 1 page"


def test_games_fixtures_carry_quarter_scores():
    """
    Quarter scores are the only reason first half picks can be graded, and
    phase 4 cannot backtest first half markets without them.
    """
    played = json.loads(
        (FIXTURES / "cfbd" / "games_2024_w05.json").read_text())
    with_quarters = [g for g in played if g.get("homeLineScores")]
    assert len(with_quarters) > len(played) * 0.8


def test_lines_fixtures_carry_opening_and_closing_numbers():
    """The CLV backbone, and phase 2's market source for 2023 through 2025."""
    rows = json.loads((FIXTURES / "cfbd" / "lines_2024_w05.json").read_text())
    opens = closes = 0
    for g in rows:
        for ln in g.get("lines") or []:
            opens += ln.get("spreadOpen") is not None
            closes += ln.get("spread") is not None
    assert opens > 20 and closes > 20


def test_fixtures_carry_no_api_key(entries):
    """A captured payload must never have a key baked into it."""
    for name, e in entries.items():
        p = FIXTURES / e["source"] / f"{name}.json"
        text = p.read_text()
        assert "apiKey" not in text
        assert "Authorization" not in text
