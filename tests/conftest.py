"""
Shared plumbing for the suite.

Two jobs. It puts scripts/ on the path so the tests import the same modules
the scripts do, and it loads the captured fixtures so no test ever reaches
the network. A test that needs a key is a test that will not run on a pull
request from a fork.

It also guards the ledger. data/picks.json is append only and its rows
freeze at the timestamp they were written, so the suite hashes it at the
start of the session and fails at the end if it moved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPTS))


# ------------------------------------------------------------- fixtures

def fixture_path(source: str, name: str) -> Path:
    return FIXTURES / source / f"{name}.json"


def load_fixture(source: str, name: str):
    """
    A missing fixture fails rather than skips. A suite that skips its way to
    green is worse than a red one, because it reads as covered.
    """
    p = fixture_path(source, name)
    if not p.exists():
        pytest.fail(
            f"fixture {source}/{name}.json is missing. Capture it with "
            f"python3 scripts/refresh_fixtures.py --sources {source.split('_')[0]}"
        )
    return json.loads(p.read_text())


def have_fixture(source: str, name: str) -> bool:
    return fixture_path(source, name).exists()


@pytest.fixture(scope="session")
def manifest() -> dict:
    p = FIXTURES / "MANIFEST.json"
    if not p.exists():
        pytest.fail("tests/fixtures/MANIFEST.json is missing. Run "
                    "python3 scripts/refresh_fixtures.py")
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def odds_board() -> list:
    return load_fixture("odds_api", "board_current")


@pytest.fixture(scope="session")
def odds_scores() -> list:
    return load_fixture("odds_api", "scores_current")


@pytest.fixture(scope="session")
def espn_page() -> dict:
    return load_fixture("espn", "teams_page01")


@pytest.fixture(scope="session")
def cfbd_games() -> list:
    """A played week, so quarter scores are present and halves can settle."""
    return load_fixture("cfbd", "games_2024_w05")


@pytest.fixture(scope="session")
def cfbd_lines() -> list:
    return load_fixture("cfbd", "lines_2024_w05")


@pytest.fixture(scope="session")
def cfbd_sp() -> list:
    return load_fixture("cfbd", "sp_2024")


@pytest.fixture(scope="session")
def cfbd_fpi() -> list:
    return load_fixture("cfbd", "fpi_2024")


@pytest.fixture(scope="session")
def cfbd_srs() -> list:
    return load_fixture("cfbd", "srs_2024")


@pytest.fixture(scope="session")
def cfbd_elo() -> list:
    return load_fixture("cfbd", "elo_2024_w05")


@pytest.fixture(scope="session")
def cfbd_calendar() -> list:
    return load_fixture("cfbd", "calendar_2024")


# ---------------------------------------------------------- corruption

def corrupt(payload, *edits):
    """
    A deep copy with named damage applied, for the tests that prove a
    changed upstream field raises instead of producing a pick.

    Each edit is (path, value). A path is a list of keys and indexes, and a
    value of DELETE removes the key instead of setting it.

        corrupt(board, (["0", "bookmakers", 0, "markets", 0,
                         "outcomes", 0, "price"], DELETE))
    """
    out = copy.deepcopy(payload)
    for path, value in edits:
        node = out
        for step in path[:-1]:
            node = node[step]
        last = path[-1]
        if value is DELETE:
            if isinstance(node, dict):
                node.pop(last, None)
            else:
                del node[last]
        else:
            node[last] = value
    return out


class _Delete:
    def __repr__(self) -> str:
        return "DELETE"


DELETE = _Delete()


def rename(payload, path, old_key: str, new_key: str):
    """Move a key, which is what an upstream rename actually looks like."""
    out = copy.deepcopy(payload)
    node = out
    for step in path:
        node = node[step]
    node[new_key] = node.pop(old_key)
    return out


# --------------------------------------------------------- ledger guard

def _hash_file(p: Path) -> str | None:
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def picks_ledger_is_frozen():
    """
    data/picks.json is append only and nothing in this suite may touch it.
    Hashed before and after the whole session.
    """
    p = ROOT / "data" / "picks.json"
    before = _hash_file(p)
    yield
    after = _hash_file(p)
    assert after == before, (
        "data/picks.json changed during the test run. It is an append only "
        "ledger and its rows freeze at the timestamp they were written."
    )


@pytest.fixture
def data_dir() -> Path:
    return ROOT / "data"


@pytest.fixture
def site_dir() -> Path:
    return ROOT / "site"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


# ------------------------------------------------------- fixture replay

# The params each endpoint sends, so a fixture can be filed at the cache key
# the client will look for. Phase 2 replays through this same path.
CFBD_CACHE_PARAMS = {
    "games": lambda yr, wk: ("/games", {"year": yr, "week": wk,
                                        "seasonType": "regular",
                                        "classification": "fbs"}),
    "lines": lambda yr, wk: ("/lines", {"year": yr, "week": wk,
                                        "seasonType": "regular"}),
    "elo": lambda yr, wk: ("/ratings/elo", {"year": yr, "week": wk}),
    "calendar": lambda yr, wk: ("/calendar", {"year": yr}),
    "sp": lambda yr, wk: ("/ratings/sp", {"year": yr}),
    "fpi": lambda yr, wk: ("/ratings/fpi", {"year": yr}),
    "srs": lambda yr, wk: ("/ratings/srs", {"year": yr}),
    "teams_fbs": lambda yr, wk: ("/teams/fbs", {"year": yr}),
}


def seed_cfbd_cache(cache_dir: Path, season: int, week: int,
                    kinds=("calendar", "sp", "fpi", "srs", "elo", "games",
                           "lines", "teams_fbs"),
                    overrides: dict | None = None) -> Path:
    """
    File the captured fixtures where the CFBD client will find them, so a
    script runs end to end with no key and no network. overrides swaps in a
    corrupted payload for one kind, which is how the corruption tests prove
    a bad response stops the pipeline rather than producing a pick.
    """
    from lib.cfbd import CFBDClient

    cache_dir.mkdir(parents=True, exist_ok=True)
    client = CFBDClient(api_key="offline", cache_dir=cache_dir)
    overrides = overrides or {}
    for kind in kinds:
        name = (f"{kind}_{season}_w{week:02d}"
                if kind in ("games", "lines", "elo") else f"{kind}_{season}")
        if kind in overrides:
            payload = overrides[kind]
        elif have_fixture("cfbd", name):
            payload = json.loads(fixture_path("cfbd", name).read_text())
        else:
            continue
        endpoint, params = CFBD_CACHE_PARAMS[kind](season, week)
        params = {k: v for k, v in params.items() if v is not None}
        client._cache_path(endpoint, params).write_text(json.dumps(payload))
    return cache_dir


@pytest.fixture
def replay_env(tmp_path):
    """
    Environment for running a script offline against the fixtures. No API
    key, a never expiring cache, and a run id so the logs are traceable.
    """
    import os
    env = dict(os.environ)
    env.pop("CFBD_API_KEY", None)
    env.pop("ODDS_API_KEY", None)
    env["CFBD_CACHE_DIR"] = str(tmp_path / "cache")
    env["CFBD_CACHE_TTL"] = "-1"
    env["RUN_ID"] = "testrun00001"
    return env
