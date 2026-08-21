#!/usr/bin/env python3
"""
Captures real API payloads into tests/fixtures/ and keeps them from rotting.

The fixtures are raw upstream responses, not parsed ones, because the parser
and the validators are the code under test and a parsed fixture would test
neither. Every capture runs the matching validator before it is written, so
a payload that already changed shape cannot be frozen into a fixture and
then trusted for 3 seasons.

These are also phase 2's input. The backtest replays them week by week, so
this captures more than the tests need: 3 seasons of games, lines and weekly
Elo rather than the 1 week the tests read.

    python3 scripts/refresh_fixtures.py --dry-run
    python3 scripts/refresh_fixtures.py --sources espn
    python3 scripts/refresh_fixtures.py --sources cfbd --seasons 2023,2024,2025
    python3 scripts/refresh_fixtures.py --sources odds --archive

Credit cost. CFBD charges 1 per uncached call against 1,000 a month. The
Odds API charges markets times regions, so a 3 market board pull is 3
against 500 a month. Dry run prints the exact call list and the total
before anything is spent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import schema  # noqa: E402
from lib.cfbd import CFBDClient, CFBDError  # noqa: E402
from lib.odds_api import SPORT, OddsClient, OddsAPIError  # noqa: E402
from lib.runlog import RunLog  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
MANIFEST = FIXTURES / "MANIFEST.json"

ESPN_BASE = ("https://site.api.espn.com/apis/site/v2/sports/football/"
             "college-football/teams")

# 2023 through 2025 are what phase 2 walks. 2026 is the live season, and it
# is captured so the tests run against the same shape the daily job sees.
BACKTEST_SEASONS = [2023, 2024, 2025]
LIVE_SEASON = 2026
DEFAULT_WEEKS = list(range(1, 16))


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


class Capture:
    """One planned call. Knows what it costs before it is made."""

    def __init__(self, source: str, name: str, endpoint: str, params: dict,
                 credits: int, validator=None, validator_kwargs: dict | None = None):
        self.source = source
        self.name = name
        self.endpoint = endpoint
        self.params = params
        self.credits = credits
        self.validator = validator
        self.validator_kwargs = validator_kwargs or {}

    @property
    def path(self) -> Path:
        return FIXTURES / self.source / f"{self.name}.json"


def plan_cfbd(seasons: list[int], weeks: list[int]) -> list[Capture]:
    out: list[Capture] = []
    for yr in seasons:
        out.append(Capture("cfbd", f"calendar_{yr}", "/calendar", {"year": yr}, 1,
                           schema.validate_cfbd_calendar))
        out.append(Capture("cfbd", f"sp_{yr}", "/ratings/sp", {"year": yr}, 1,
                           schema.validate_sp_ratings))
        out.append(Capture("cfbd", f"fpi_{yr}", "/ratings/fpi", {"year": yr}, 1,
                           schema.validate_fpi_ratings))
        out.append(Capture("cfbd", f"srs_{yr}", "/ratings/srs", {"year": yr}, 1,
                           schema.validate_srs_ratings))
        out.append(Capture("cfbd", f"teams_fbs_{yr}", "/teams/fbs", {"year": yr}, 1,
                           schema.validate_fbs_teams))
        # The live season has no results yet, so it only gets the first few
        # weeks. Those exist so a test can replay the live path offline.
        season_weeks = [w for w in weeks if w <= 3] if yr >= LIVE_SEASON else weeks
        for wk in season_weeks:
            out.append(Capture(
                "cfbd", f"games_{yr}_w{wk:02d}", "/games",
                {"year": yr, "week": wk, "seasonType": "regular",
                 "classification": "fbs"}, 1,
                schema.validate_cfbd_games, {"min_rows": 0}))
            out.append(Capture(
                "cfbd", f"lines_{yr}_w{wk:02d}", "/lines",
                {"year": yr, "week": wk, "seasonType": "regular"}, 1,
                schema.validate_cfbd_lines, {"min_rows": 0}))
            # Elo is the only rating here that genuinely varies by week, so
            # it is the one an honest as-of backtest can lean on.
            out.append(Capture(
                "cfbd", f"elo_{yr}_w{wk:02d}", "/ratings/elo",
                {"year": yr, "week": wk}, 1,
                schema.validate_elo_ratings, {"week": wk}))
    return out


def plan_odds(archive: bool) -> list[Capture]:
    board = Capture(
        "odds_api", f"board_{stamp()}" if archive else "board_current",
        f"/sports/{SPORT}/odds",
        {"regions": "us", "markets": "spreads,totals,h2h",
         "oddsFormat": "american", "dateFormat": "iso", "bookmakers": "fanduel"},
        3, schema.validate_odds_board, {"min_events": 0})
    scores = Capture(
        "odds_api", "scores_current", f"/sports/{SPORT}/scores",
        {"daysFrom": 3, "dateFormat": "iso"}, 2, schema.validate_odds_scores)
    return [board, scores]


def capture_espn(log: RunLog, dry_run: bool) -> list[dict]:
    """
    No key needed. Paged at 100 because the endpoint caps at 500 and
    truncates alphabetically, and asking for 900 silently returns 25.
    """
    written = []
    for page in range(1, 15):
        url = f"{ESPN_BASE}?limit=100&page={page}"
        name = f"teams_page{page:02d}"
        target = FIXTURES / "espn" / f"{name}.json"
        if dry_run:
            log.event("planned", source="espn", endpoint=url,
                      credit_cost=0, path=str(target))
            written.append({"name": name, "rows": None})
            if page >= 8:
                break
            continue
        with log.call(ESPN_BASE, {"limit": 100, "page": page}) as call:
            with urllib.request.urlopen(url, timeout=30) as r:
                payload = json.load(r)
            call.credit_cost = 0
            teams = schema.validate_espn_teams(payload, expected=100)
            call.rows = len(teams)
        if not teams:
            break
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, separators=(",", ":")))
        log.write(target, rows=len(teams),
                  size_bytes=target.stat().st_size)
        written.append({
            "name": name, "source": "espn", "endpoint": ESPN_BASE,
            "params": {"limit": 100, "page": page}, "rows": len(teams),
            "sha256": sha(payload), "credit_cost": 0,
            "bytes": target.stat().st_size, "captured_at": stamp(),
        })
        if len(teams) < 100:
            break
    return written


def run_capture(cap: Capture, cfbd: CFBDClient | None,
                odds: OddsClient | None, log: RunLog) -> dict | None:
    if cap.source == "cfbd":
        payload = cfbd.get(cap.endpoint, cap.params)
        cost = 1
    else:
        payload = odds.get_raw(cap.endpoint, cap.params)
        cost = odds.quota.last_cost if odds.quota.last_cost is not None else cap.credits

    if cap.validator is not None:
        # Validate before writing. A payload that already changed shape must
        # not be frozen into a fixture and then trusted for 3 seasons.
        cap.validator(payload, **cap.validator_kwargs)

    # An empty response gets captured too. SRS has nothing to say before a
    # season is played and preseason Elo is not published, so [] is the real
    # answer rather than a miss, and a replay needs it on disk or the client
    # reports nothing cached and asks for a key.
    rows = len(payload) if isinstance(payload, list) else 1
    if rows == 0:
        log.event("empty", endpoint=cap.endpoint, params=cap.params, rows=0)

    cap.path.parent.mkdir(parents=True, exist_ok=True)
    cap.path.write_text(json.dumps(payload, separators=(",", ":")))
    size = cap.path.stat().st_size
    log.write(cap.path, rows=rows, size_bytes=size)
    return {
        "name": cap.name, "source": cap.source, "endpoint": cap.endpoint,
        "params": cap.params, "rows": rows, "sha256": sha(payload),
        "credit_cost": cost, "bytes": size, "captured_at": stamp(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="espn,cfbd,odds",
                    help="comma separated: espn, cfbd, odds")
    ap.add_argument("--seasons", default=",".join(str(s) for s in
                                                  BACKTEST_SEASONS + [LIVE_SEASON]))
    ap.add_argument("--weeks", default="1-15",
                    help="a range like 1-15 or a list like 1,4,9")
    ap.add_argument("--archive", action="store_true",
                    help="timestamp the board fixture instead of overwriting, "
                         "so the multi week set builds up over the season")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the call list and the credit total, spend nothing")
    args = ap.parse_args()

    log = RunLog("refresh_fixtures", dry_run=args.dry_run)
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}
    seasons = [int(s) for s in args.seasons.split(",") if s.strip()]
    if "-" in args.weeks:
        lo, hi = args.weeks.split("-")
        weeks = list(range(int(lo), int(hi) + 1))
    else:
        weeks = [int(w) for w in args.weeks.split(",") if w.strip()]

    captures: list[Capture] = []
    if "cfbd" in sources:
        captures += plan_cfbd(seasons, weeks)
    if "odds" in sources:
        captures += plan_odds(args.archive)

    cfbd_credits = sum(c.credits for c in captures if c.source == "cfbd")
    odds_credits = sum(c.credits for c in captures if c.source == "odds_api")

    if args.dry_run:
        for c in captures:
            log.event("planned", source=c.source, endpoint=c.endpoint,
                      params=c.params, credit_cost=c.credits, path=str(c.path))
        if "espn" in sources:
            capture_espn(log, dry_run=True)
        print(json.dumps({
            "dry_run": True,
            "calls": len(captures),
            "cfbd_calls": cfbd_credits,
            "odds_credits": odds_credits,
            "note": ("CFBD free tier is 1,000 calls a month against about 300 "
                     "of normal usage. The Odds API free tier is 500 credits "
                     "a month and the daily schedule burns about 90."),
        }, indent=2))
        log.finish()
        return 0

    entries = []
    if "espn" in sources:
        entries += capture_espn(log, dry_run=False)

    cfbd = odds = None
    if any(c.source == "cfbd" for c in captures):
        cfbd = CFBDClient(log=log, cache_ttl=0)
    if any(c.source == "odds_api" for c in captures):
        try:
            odds = OddsClient(log=log)
        except OddsAPIError as e:
            log.error(str(e))
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    failures = []
    for cap in captures:
        try:
            entry = run_capture(cap, cfbd, odds, log)
            if entry:
                entries.append(entry)
        except (CFBDError, OddsAPIError, schema.ShapeError) as e:
            failures.append({"name": cap.name, "error": str(e)})
            log.error(str(e), endpoint=cap.endpoint, params=cap.params)

    existing = {}
    if MANIFEST.exists():
        try:
            existing = {e["name"]: e for e in
                        json.loads(MANIFEST.read_text()).get("fixtures", [])}
        except (json.JSONDecodeError, KeyError):
            existing = {}
    for e in entries:
        existing[e["name"]] = e

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "refreshed_at": stamp(),
        "note": ("Raw upstream payloads. Captured by "
                 "scripts/refresh_fixtures.py, validated before writing, and "
                 "replayed by the tests and by the phase 2 backtest. Rerun "
                 "the script rather than hand editing any file here."),
        "fixtures": sorted(existing.values(), key=lambda e: (e["source"], e["name"])),
    }, indent=2) + "\n")

    out = {
        "captured": len(entries),
        "failed": len(failures),
        "credits_spent": log.credits_spent,
        "manifest": str(MANIFEST.relative_to(ROOT)),
    }
    print(json.dumps(out, indent=2))
    for f in failures:
        print(f"FAILED {f['name']}: {f['error']}", file=sys.stderr)
    log.finish(status="ok" if not failures else "partial")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
