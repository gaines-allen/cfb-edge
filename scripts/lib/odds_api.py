"""
The Odds API v4 client, scoped to college football and FanDuel.

Why this instead of scraping FanDuel directly: FanDuel's terms prohibit
automated collection, and a scraper against their front end will break on
every layout change and can get the account flagged. The Odds API is a
sanctioned feed that carries FanDuel as a named bookmaker, so the numbers
are the ones actually on the board without the terms problem.

Quota math: cost = (# markets) x (# regions) per call.
Default market set below is 5 markets x 1 region = 5 credits per call.
One call per day is ~150 credits/month, inside the 500 free tier.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import requests

BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_ncaaf"
BOOK = "fanduel"

# The bulk /odds endpoint only serves featured markets. Asking it for
# spreads_h1 returns 422 INVALID_MARKET, confirmed against the live API.
# Half markets exist, but only on the single-event endpoint.
FEATURED_MARKETS = ["spreads", "totals", "h2h"]
PERIOD_MARKETS = ["spreads_h1", "totals_h1", "spreads_h2", "totals_h2"]

# Kept for callers that still import these names.
DEFAULT_MARKETS = FEATURED_MARKETS
ALL_MARKETS = FEATURED_MARKETS + PERIOD_MARKETS


class OddsAPIError(RuntimeError):
    pass


@dataclass
class Quota:
    remaining: int | None = None
    used: int | None = None
    last_cost: int | None = None

    @classmethod
    def from_headers(cls, h: Any) -> "Quota":
        def _i(k: str) -> int | None:
            v = h.get(k)
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        return cls(
            remaining=_i("x-requests-remaining"),
            used=_i("x-requests-used"),
            last_cost=_i("x-requests-last"),
        )


@dataclass
class Line:
    """One priced number on one side of one market."""
    market: str          # spreads | totals | spreads_h1 | totals_h1 | h2h ...
    side: str            # team name, or "Over" / "Under"
    point: float | None  # spread or total; None for moneyline
    price: int           # American odds
    book: str = BOOK


@dataclass
class Game:
    event_id: str
    commence_time: str   # ISO8601 UTC
    home_team: str
    away_team: str
    lines: list[Line] = field(default_factory=list)

    def get(self, market: str, side: str) -> Line | None:
        for ln in self.lines:
            if ln.market == market and ln.side == side:
                return ln
        return None

    def spread_for(self, team: str, market: str = "spreads") -> Line | None:
        return self.get(market, team)

    def total(self, direction: str, market: str = "totals") -> Line | None:
        return self.get(market, direction.title())

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class OddsClient:
    def __init__(self, api_key: str | None = None, book: str = BOOK,
                 timeout: int = 30, retries: int = 3):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")
        if not self.api_key:
            raise OddsAPIError(
                "No ODDS_API_KEY set. Get a free key at https://the-odds-api.com/ "
                "and export ODDS_API_KEY, or add it as a GitHub Actions secret."
            )
        self.book = book
        self.timeout = timeout
        self.retries = retries
        self.quota = Quota()

    # ---------- transport ----------

    def _get(self, path: str, params: dict) -> Any:
        params = {**params, "apiKey": self.api_key}
        url = f"{BASE}{path}"
        last_err: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = requests.get(url, params=params, timeout=self.timeout)
                if r.status_code == 401:
                    raise OddsAPIError("401 from The Odds API - the key is wrong or expired.")
                if r.status_code == 429:
                    raise OddsAPIError("429 - monthly credit quota exhausted.")
                if r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code} from upstream")
                r.raise_for_status()
                self.quota = Quota.from_headers(r.headers)
                return r.json()
            except OddsAPIError:
                raise
            except Exception as e:  # noqa: BLE001 - retry transport faults only
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise OddsAPIError(f"Odds API unreachable after {self.retries} tries: {last_err}")

    # ---------- public ----------

    def board(self, markets: list[str] | None = None,
              days_ahead: int = 10) -> list[Game]:
        """Current FanDuel board for upcoming NCAAF games."""
        markets = markets or DEFAULT_MARKETS
        raw = self._get(
            f"/sports/{SPORT}/odds",
            {
                "regions": "us",
                "markets": ",".join(markets),
                "oddsFormat": "american",
                "dateFormat": "iso",
                "bookmakers": self.book,
            },
        )
        return [self._parse_event(ev) for ev in raw if self._has_book(ev)]

    def event_odds(self, event_id: str,
                   markets: list[str] | None = None) -> Game | None:
        """
        Half and quarter markets for one game. These are not available on the
        bulk endpoint, so this is the only way to get them.

        Cost is markets times regions per event, and it is charged only when
        a bookmaker actually returns a price. Books post college half lines
        close to kickoff, so before game week this is usually free and
        usually empty. Never loop this over a full board: sixty games at two
        markets is 120 credits, most of a free month.
        """
        markets = markets or ["spreads_h1", "totals_h1"]
        try:
            raw = self._get(
                f"/sports/{SPORT}/events/{event_id}/odds",
                {
                    "regions": "us",
                    "markets": ",".join(markets),
                    "oddsFormat": "american",
                    "dateFormat": "iso",
                    "bookmakers": self.book,
                },
            )
        except OddsAPIError:
            return None
        if not raw or not raw.get("bookmakers"):
            return None
        return self._parse_event(raw)

    def scores(self, days_from: int = 3) -> list[dict]:
        """Recent and live scores. days_from is capped at 3 by the API."""
        return self._get(
            f"/sports/{SPORT}/scores",
            {"daysFrom": max(1, min(3, days_from)), "dateFormat": "iso"},
        )

    # ---------- parsing ----------

    def _has_book(self, ev: dict) -> bool:
        return any(b.get("key") == self.book for b in ev.get("bookmakers", []))

    def _parse_event(self, ev: dict) -> Game:
        g = Game(
            event_id=ev["id"],
            commence_time=ev["commence_time"],
            home_team=ev["home_team"],
            away_team=ev["away_team"],
        )
        for bk in ev.get("bookmakers", []):
            if bk.get("key") != self.book:
                continue
            for mk in bk.get("markets", []):
                mkey = mk.get("key")
                for oc in mk.get("outcomes", []):
                    g.lines.append(
                        Line(
                            market=mkey,
                            side=oc.get("name", ""),
                            point=oc.get("point"),
                            price=int(oc.get("price", 0)),
                            book=self.book,
                        )
                    )
        return g


def american_to_prob(price: int) -> float:
    """Implied probability including vig."""
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


def devig_pair(price_a: int, price_b: int) -> tuple[float, float]:
    """Two-way de-vig by proportional (multiplicative) normalization."""
    pa, pb = american_to_prob(price_a), american_to_prob(price_b)
    s = pa + pb
    if s <= 0:
        return 0.5, 0.5
    return pa / s, pb / s


def prob_to_american(p: float) -> int:
    """Fair American price for a probability."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    if p >= 0.5:
        return int(round(-100.0 * p / (1.0 - p)))
    return int(round(100.0 * (1.0 - p) / p))
