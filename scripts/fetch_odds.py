#!/usr/bin/env python3
"""
odds-scout's tool. Pulls the FanDuel board, snapshots every number we care
about, and stamps closing lines onto any pick whose game has kicked off.

    python3 scripts/fetch_odds.py                 # full board, 3 credits
    python3 scripts/fetch_odds.py --close-only    # just stamp closers, free
    python3 scripts/fetch_odds.py --halves ID,ID  # add 1H for those games

The bulk endpoint only serves spreads, totals and moneyline. Half markets
come from the single-event endpoint at 2 credits a game, so they are opt in
per event rather than pulled across the board.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.odds_api import (  # noqa: E402
    FEATURED_MARKETS,
    PERIOD_MARKETS,
    Game,
    OddsAPIError,
    OddsClient,
)
from lib.scoring import clv_cents, clv_points  # noqa: E402

MARKET_TO_PERIOD = {
    "spreads": ("spread", "full"),
    "totals": ("total", "full"),
    "h2h": ("moneyline", "full"),
    "spreads_h1": ("spread", "h1"),
    "totals_h1": ("total", "h1"),
    "spreads_h2": ("spread", "h2"),
    "totals_h2": ("total", "h2"),
}
PERIOD_TO_MARKET = {v: k for k, v in MARKET_TO_PERIOD.items()}


def snapshot_of(game: Game) -> dict:
    """The compact per-event record we store in line_history."""
    snap: dict = {}
    for ln in game.lines:
        snap[f"{ln.market}|{ln.side}"] = {"point": ln.point, "price": ln.price}
    return snap


def kicked_off(iso_ts: str) -> bool:
    try:
        return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")) <= datetime.now(
            timezone.utc
        )
    except (ValueError, AttributeError):
        return False


def stamp_closing_lines(games_by_id: dict[str, dict]) -> int:
    """
    Freeze the last number seen before kickoff onto every pending pick.
    That frozen number is what CLV is measured against, and CLV is the only
    honest read on whether a pick was good independent of the result.
    """
    picks = store.load_picks()
    hist = store.load_line_history()
    changed = 0

    for p in picks:
        if p.get("close_line") is not None or p.get("result") != "pending":
            continue
        if not kicked_off(p.get("kickoff", "")):
            continue

        series = hist.get(p["event_id"]) or []
        if not series:
            continue

        mkey = PERIOD_TO_MARKET.get((p["market"], p["period"]))
        if not mkey:
            continue

        side = p["side"] if p["market"] != "total" else p["side"].title()
        want = f"{mkey}|{side}"

        # Walk backwards to the last snapshot taken before kickoff.
        last = None
        for entry in series:
            if entry.get("at", "") >= p["kickoff"]:
                break
            if want in entry:
                last = entry[want]
        if not last:
            continue

        p["close_line"] = last.get("point")
        p["close_price"] = last.get("price")
        p["clv_points"] = clv_points(p["market"], p["side"], p.get("line"),
                                     p["close_line"])
        if p.get("price") is not None and p["close_price"] is not None:
            p["clv_cents"] = clv_cents(int(p["price"]), int(p["close_price"]))
        changed += 1

    if changed:
        store.save_picks(picks)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="default", choices=["default", "all"])
    ap.add_argument("--close-only", action="store_true")
    ap.add_argument("--halves", default="",
                    help="comma separated event ids to also pull half markets "
                         "for. Costs 2 credits per event, so pass only the "
                         "games actually being considered, never the board.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.close_only:
        n = stamp_closing_lines({})
        print(json.dumps({"closing_lines_stamped": n}))
        return 0

    try:
        client = OddsClient()
    except OddsAPIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    markets = FEATURED_MARKETS

    try:
        games = client.board(markets=markets)
    except OddsAPIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    # Half markets, only for the events explicitly asked for. The bulk
    # endpoint rejects them, and pulling them board-wide would burn the
    # monthly credit allowance in a single run.
    want_halves = [e.strip() for e in args.halves.split(",") if e.strip()]
    period_markets = PERIOD_MARKETS if args.markets == "all" else [
        "spreads_h1", "totals_h1"]
    halves_found, halves_empty = 0, 0

    for g in games:
        if g.event_id in want_halves:
            extra = client.event_odds(g.event_id, markets=period_markets)
            if extra and extra.lines:
                g.lines.extend(extra.lines)
                halves_found += 1
            else:
                halves_empty += 1

    payload = []
    for g in games:
        store.append_line_snapshot(g.event_id, snapshot_of(g))
        payload.append(g.to_dict())

    quota = {
        "remaining": client.quota.remaining,
        "used": client.quota.used,
        "last_cost": client.quota.last_cost,
    }
    store.save_board(payload, quota)
    stamped = stamp_closing_lines({g.event_id: g.to_dict() for g in games})

    out = {
        "games": len(payload),
        "markets": markets,
        "half_markets_requested": len(want_halves),
        "half_markets_found": halves_found,
        "half_markets_not_posted": halves_empty,
        "closing_lines_stamped": stamped,
        "credits_remaining": quota["remaining"],
        "fetched_at": store.now_iso(),
    }
    if not args.quiet:
        print(json.dumps(out, indent=2))

    if quota["remaining"] is not None and quota["remaining"] < 40:
        print(f"WARNING: only {quota['remaining']} Odds API credits left this month.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
