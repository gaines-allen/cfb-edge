"""
Every script that writes to data/ or site/ takes --dry-run, and a dry run
leaves both byte identical.

The test is a hash of every tracked file before and after, not an inspection
of what the script said it did. A script that prints "dry run" and writes
anyway would pass the second and fail the first.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT, seed_cfbd_cache

WRITERS = [
    "fetch_odds.py", "grade_results.py", "make_slate.py",
    "calibrate_model.py", "build_aliases.py", "build_site.py",
    "log_picks.py", "refresh_fixtures.py",
]


def fingerprint() -> str:
    """
    Every file under data/ and site/, content and all. The cache is skipped
    because it is gitignored scratch that a run is allowed to fill.
    """
    h = hashlib.sha256()
    for base in ("data", "site"):
        for p in sorted((ROOT / base).rglob("*")):
            if not p.is_file() or ".cache" in p.parts:
                continue
            h.update(str(p.relative_to(ROOT)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def offline_env(cache: Path) -> dict:
    import os
    env = dict(os.environ)
    env.pop("CFBD_API_KEY", None)
    env.pop("ODDS_API_KEY", None)
    env["CFBD_CACHE_DIR"] = str(cache)
    env["CFBD_CACHE_TTL"] = "-1"
    env["RUN_ID"] = "dryrun000001"
    return env


def run(script: str, *args, env=None):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        capture_output=True, text=True, cwd=str(ROOT),
        env=env if env is not None else offline_env(ROOT / "data" / ".cache"))


# ------------------------------------------------- the flag exists

@pytest.mark.parametrize("script", WRITERS)
def test_every_writer_exposes_dry_run(script):
    proc = run(script, "--help")
    assert proc.returncode == 0, proc.stderr[-400:]
    assert "--dry-run" in proc.stdout, (
        f"scripts/{script} writes to data/ or site/ and has no --dry-run"
    )


# --------------------------------------- a dry run touches nothing

@pytest.mark.parametrize("script,args", [
    ("build_site.py", []),
    ("make_slate.py", ["--season", "2026", "--week", "1"]),
    ("calibrate_model.py", ["--season", "2026", "--week", "1"]),
    ("grade_results.py", ["--season", "2026", "--week", "1"]),
])
def test_dry_run_leaves_disk_untouched(tmp_path, script, args):
    cache = seed_cfbd_cache(tmp_path / "cache", 2026, 1)
    before = fingerprint()
    proc = run(script, *args, "--dry-run", env=offline_env(cache))
    after = fingerprint()
    assert after == before, (
        f"scripts/{script} --dry-run modified data/ or site/. "
        f"stdout:\n{proc.stdout[:500]}"
    )


def test_fetch_odds_dry_run_writes_nothing(tmp_path, monkeypatch, odds_board):
    """
    Driven off the captured board rather than the network, so the write path
    runs for real and only the disk write is suppressed.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_odds
    from lib import store
    from lib.odds_api import OddsClient

    parser = OddsClient(api_key="offline")
    games = [parser._parse_event(ev) for ev in odds_board
             if any(b.get("key") == "fanduel"
                    for b in ev.get("bookmakers", []))]
    assert games, "the captured board carried no FanDuel prices"

    monkeypatch.setattr(OddsClient, "board", lambda self, **k: games)
    monkeypatch.setattr(sys, "argv", ["fetch_odds.py", "--dry-run", "--quiet"])
    monkeypatch.setenv("ODDS_API_KEY", "offline")

    before = fingerprint()
    try:
        fetch_odds.main()
    finally:
        store.set_dry_run(False)
    assert fingerprint() == before


def test_build_aliases_dry_run_writes_nothing(monkeypatch, espn_page):
    """Driven off the captured ESPN page rather than the live endpoint."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_aliases
    from lib import store

    teams = [w["team"] for w in espn_page["sports"][0]["leagues"][0]["teams"]]
    monkeypatch.setattr(build_aliases, "fetch_teams", lambda: teams)
    monkeypatch.setattr(sys, "argv", ["build_aliases.py", "--dry-run"])

    before = fingerprint()
    try:
        assert build_aliases.main() == 0
    finally:
        store.set_dry_run(False)
    assert fingerprint() == before


def test_log_picks_dry_run_writes_nothing(tmp_path):
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps([{
        "event_id": "abc123", "matchup": "Michigan @ Ohio State",
        "kickoff": "2026-09-05T16:00:00+00:00", "market": "spread",
        "period": "full", "side": "Ohio State", "line": -6.5, "price": -110,
        "confidence": 8.4, "units": 1.0,
        "rationale": "A test row that must never reach the ledger.",
        "factors": {},
    }]))
    before = fingerprint()
    proc = run("log_picks.py", "--file", str(draft), "--season", "2026",
               "--week", "1", "--dry-run")
    assert fingerprint() == before, proc.stdout[:400]


def test_refresh_fixtures_dry_run_spends_nothing(tmp_path):
    """
    The Odds API free tier is 500 credits a month, so the capture has to be
    able to price itself before it runs.
    """
    fixtures_before = sorted(
        p.name for p in (ROOT / "tests" / "fixtures").rglob("*.json"))
    proc = run("refresh_fixtures.py", "--dry-run")
    assert proc.returncode == 0, proc.stderr[-400:]
    out = json.loads(proc.stdout)
    assert out["dry_run"] is True
    assert out["calls"] > 0
    assert out["odds_credits"] > 0
    after = sorted(p.name for p in (ROOT / "tests" / "fixtures").rglob("*.json"))
    assert after == fixtures_before


# --------------------------------------------- the switch itself

def test_write_json_reports_that_it_skipped(tmp_path):
    from lib import store
    target = tmp_path / "out.json"
    store.set_dry_run(True)
    try:
        assert store.write_json(target, {"a": 1}) is False
        assert not target.exists()
    finally:
        store.set_dry_run(False)
    assert store.write_json(target, {"a": 1}) is True
    assert json.loads(target.read_text()) == {"a": 1}


def test_picks_ledger_is_never_written_by_a_dry_run(tmp_path):
    """
    data/picks.json is append only and its rows freeze at the timestamp they
    were written. Nothing in a dry run may go near it.
    """
    from lib import store
    picks = ROOT / "data" / "picks.json"
    before = picks.read_bytes() if picks.exists() else None
    store.set_dry_run(True)
    try:
        store.save_picks([{"id": "should-never-land"}])
    finally:
        store.set_dry_run(False)
    after = picks.read_bytes() if picks.exists() else None
    assert after == before
