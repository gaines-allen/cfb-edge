"""
CollegeFootballData client - the model inputs behind the picks.

Free tier is 1,000 calls/month with an emailed key from
https://collegefootballdata.com/key. Tier 1 ($1/mo) adds weather and
opponent-adjusted metrics; Tier 2 ($5/mo) raises the cap to 30k.

Every call here is cached to disk so a rerun on the same day costs zero
credits. The cache is what keeps a daily cron inside the free tier.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import schema
from .runlog import RunLog

BASE = "https://api.collegefootballdata.com"

# CFBD_CACHE_DIR points the cache somewhere else, which is how a test and
# the phase 2 backtest replay captured fixtures without a key and without
# spending a call. The default is the gitignored cache the daily job uses.
CACHE_DIR = Path(os.environ.get(
    "CFBD_CACHE_DIR",
    Path(__file__).resolve().parents[2] / "data" / ".cache"))
CACHE_TTL_SECONDS = int(os.environ.get("CFBD_CACHE_TTL", 12 * 3600))


class CFBDError(RuntimeError):
    pass


class CFBDClient:
    def __init__(self, api_key: str | None = None, timeout: int = 30,
                 cache_ttl: int = CACHE_TTL_SECONDS,
                 log: RunLog | None = None,
                 cache_dir: Path | None = None):
        self.api_key = api_key or os.environ.get("CFBD_API_KEY", "")
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.enabled = bool(self.api_key)
        self.log = log or RunLog("cfbd", to_stderr=False, to_file=False)
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------- cache ----------

    def _cache_path(self, path: str, params: dict) -> Path:
        key = hashlib.sha256(
            f"{path}|{json.dumps(params, sort_keys=True)}".encode()
        ).hexdigest()[:24]
        safe = path.strip("/").replace("/", "_")
        return self.cache_dir / f"{safe}.{key}.json"

    def _read_cache(self, p: Path) -> Any | None:
        if not p.exists():
            return None
        # A negative ttl never expires, which is what a fixture replay wants.
        # A 0 ttl always expires, which is what a fresh capture wants.
        if self.cache_ttl >= 0 and time.time() - p.stat().st_mtime > self.cache_ttl:
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None

    # ---------- transport ----------

    def get(self, path: str, params: dict | None = None,
            allow_stale: bool = True) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        cp = self._cache_path(path, params)

        # A cache hit costs 0 calls, a live call costs 1. Logging which is
        # the only way to see whether the 12 hour cache is doing its job
        # against a 1,000 call month.
        with self.log.call(path, params) as call:
            cached = self._read_cache(cp)
            if cached is not None:
                call.cached = True
                call.credit_cost = 0
                call.rows = len(cached) if isinstance(cached, list) else 1
                return cached

            if not self.enabled:
                raise CFBDError(
                    "No CFBD_API_KEY set and nothing cached. Free key: "
                    "https://collegefootballdata.com/key"
                )

            try:
                r = requests.get(
                    f"{BASE}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout,
                )
                if r.status_code == 401:
                    raise CFBDError("401 from CFBD - bad or missing bearer token.")
                r.raise_for_status()
                data = r.json()
                call.credit_cost = 1
            except CFBDError:
                raise
            except Exception as e:  # noqa: BLE001
                # A stale cache beats no data at all on a daily automated run.
                if allow_stale and cp.exists():
                    call.cached = True
                    call.status = "stale_cache"
                    call.credit_cost = 1
                    return json.loads(cp.read_text())
                raise CFBDError(f"CFBD request failed for {path}: {e}") from e

            call.rows = len(data) if isinstance(data, list) else 1
            cp.write_text(json.dumps(data))
            return data

    # ---------- endpoints ----------

    def calendar(self, year: int) -> list[dict]:
        return schema.validate_cfbd_calendar(self.get("/calendar", {"year": year}))

    def games(self, year: int, week: int | None = None,
              season_type: str = "regular",
              classification: str = "fbs") -> list[dict]:
        return schema.validate_cfbd_games(self.get("/games", {
            "year": year, "week": week,
            "seasonType": season_type, "classification": classification,
        }), min_rows=0)

    def lines(self, year: int, week: int | None = None,
              season_type: str = "regular") -> list[dict]:
        """Opening and closing spreads/totals by provider - the CLV backbone."""
        return schema.validate_cfbd_lines(self.get("/lines", {
            "year": year, "week": week, "seasonType": season_type,
        }), min_rows=0)

    def sp_ratings(self, year: int, team: str | None = None) -> list[dict]:
        rows = self.get("/ratings/sp", {"year": year, "team": team})
        return schema.validate_sp_ratings(rows)

    def srs(self, year: int) -> list[dict]:
        return schema.validate_srs_ratings(self.get("/ratings/srs", {"year": year}))

    def elo(self, year: int, week: int | None = None) -> list[dict]:
        rows = self.get("/ratings/elo", {"year": year, "week": week})
        return schema.validate_elo_ratings(rows, week=week)

    def fpi(self, year: int) -> list[dict]:
        return schema.validate_fpi_ratings(self.get("/ratings/fpi", {"year": year}))

    def talent(self, year: int) -> list[dict]:
        return self.get("/talent", {"year": year})

    def returning_production(self, year: int) -> list[dict]:
        return self.get("/player/returning", {"year": year})

    def advanced_season_stats(self, year: int, team: str | None = None,
                              exclude_garbage: bool = True,
                              start_week: int | None = None,
                              end_week: int | None = None) -> list[dict]:
        return self.get("/stats/season/advanced", {
            "year": year, "team": team,
            "excludeGarbageTime": str(exclude_garbage).lower(),
            "startWeek": start_week, "endWeek": end_week,
        })

    def ppa_teams(self, year: int, team: str | None = None,
                  exclude_garbage: bool = True) -> list[dict]:
        return self.get("/ppa/teams", {
            "year": year, "team": team,
            "excludeGarbageTime": str(exclude_garbage).lower(),
        })

    def ppa_games(self, year: int, week: int | None = None,
                  team: str | None = None) -> list[dict]:
        return self.get("/ppa/games", {"year": year, "week": week, "team": team})

    def ats_records(self, year: int, team: str | None = None) -> list[dict]:
        return self.get("/teams/ats", {"year": year, "team": team})

    def fbs_teams(self, year: int) -> list[dict]:
        return schema.validate_fbs_teams(self.get("/teams/fbs", {"year": year}))

    def venues(self) -> list[dict]:
        return self.get("/venues", {})

    def weather(self, year: int, week: int | None = None) -> list[dict]:
        """Tier 1+ only. Returns [] rather than raising on a free key."""
        try:
            return self.get("/games/weather", {"year": year, "week": week})
        except CFBDError:
            return []


def current_week(cal: list[dict], now: datetime | None = None) -> int | None:
    """Which week are we in right now, per the season calendar."""
    now = now or datetime.now(timezone.utc)
    for wk in cal:
        try:
            start = datetime.fromisoformat(wk["firstGameStart"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(wk["lastGameStart"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        if start <= now <= end:
            return int(wk["week"])
    # Between weeks: return the next one that has not started.
    upcoming = []
    for wk in cal:
        try:
            start = datetime.fromisoformat(wk["firstGameStart"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        if start > now:
            upcoming.append((start, int(wk["week"])))
    return min(upcoming)[1] if upcoming else None
