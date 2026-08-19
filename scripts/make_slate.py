#!/usr/bin/env python3
"""
handicapper's prep tool. Joins the FanDuel board to the rating model and
writes data/slate.json: every game with the model number, the market number,
and the raw disagreement between them.

This produces candidates, not picks. The agent still has to do the research.

    python3 scripts/make_slate.py --season 2026 --week 1
    python3 scripts/make_slate.py --min-edge 3.0
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.cfbd import CFBDClient, CFBDError, current_week  # noqa: E402
from lib.model import (  # noqa: E402
    DEFAULT_HFA,
    build_rating_book,
    edge_vs_market,
    project_game,
    suggested_confidence,
)
from lib.teams import canonical, suggest  # noqa: E402


def find(lines: list[dict], market: str, side: str) -> dict | None:
    for ln in lines:
        if ln.get("market") == market and ln.get("side") == side:
            return ln
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--min-edge", type=float, default=1.5,
                    help="points of disagreement needed to list a candidate")
    ap.add_argument("--hfa", type=float, default=DEFAULT_HFA)
    args = ap.parse_args()

    board = store.load_board()
    games = board.get("games", [])
    if not games:
        print("ERROR: data/board.json is empty. Run scripts/fetch_odds.py first.",
              file=sys.stderr)
        return 2

    age_note = ""
    if board.get("fetched_at"):
        try:
            fetched = datetime.fromisoformat(board["fetched_at"])
            hours = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
            if hours > 12:
                age_note = f"board is {hours:.0f} hours old, refresh before rating"
        except ValueError:
            pass

    cfbd = CFBDClient()
    try:
        cal = cfbd.calendar(args.season)
        week = args.week or current_week(cal) or 1
        sp = cfbd.sp_ratings(args.season)
        srs = cfbd.srs(args.season)
        elo = cfbd.elo(args.season, week=week)
        fpi = cfbd.fpi(args.season)
        sched = cfbd.games(args.season, week=week)
    except CFBDError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    book = build_rating_book(sp, fpi, srs, elo)
    known = set(book.keys())

    neutral_by_pair = {}
    for g in sched:
        h, a = g.get("homeTeam"), g.get("awayTeam")
        if h and a:
            neutral_by_pair[(h, a)] = bool(g.get("neutralSite"))

    rows = []
    unmapped: dict[str, list[str]] = {}
    skipped = []

    for g in games:
        home = canonical(g["home_team"], known)
        away = canonical(g["away_team"], known)

        # No guessing. A name we cannot place gets reported and the game is
        # left off the slate, because a wrong rating lookup produces a
        # confident number for the wrong team, which is worse than a gap.
        if home is None or away is None:
            for raw, mapped in ((g["home_team"], home), (g["away_team"], away)):
                if mapped is None and raw not in unmapped:
                    unmapped[raw] = suggest(raw, known)
            skipped.append(f"{g['away_team']} @ {g['home_team']}")
            continue

        neutral = neutral_by_pair.get((home, away), False)

        proj = project_game(home, away, book, neutral=neutral, hfa=args.hfa)
        lines = g.get("lines", [])

        mkt_spread = find(lines, "spreads", g["home_team"])
        mkt_total = find(lines, "totals", "Over")
        mkt_h1_spread = find(lines, "spreads_h1", g["home_team"])
        mkt_h1_total = find(lines, "totals_h1", "Over")

        candidates = []

        def add(market: str, period: str, mkt: dict | None,
                projected: float | None, side_over: str | None = None) -> None:
            if not mkt or projected is None or mkt.get("point") is None:
                return
            e = edge_vs_market(projected, float(mkt["point"]))
            if e is None or abs(e) < args.min_edge:
                return
            if market == "spread":
                # Model number below the market number means home is undervalued.
                side = home if e < 0 else away
                line = float(mkt["point"]) if side == home else -float(mkt["point"])
                price = mkt.get("price")
            else:
                side = "Over" if e > 0 else "Under"
                line = float(mkt["point"])
                other = find(lines, "totals_h1" if period == "h1" else "totals", side)
                price = (other or mkt).get("price")

            complete = (proj.confidence_inputs["ratings_present_home"] >= 2
                        and proj.confidence_inputs["ratings_present_away"] >= 2)
            candidates.append({
                "market": market,
                "period": period,
                "side": side,
                "market_line": float(mkt["point"]),
                "bet_line": line,
                "price": price,
                "model_number": projected,
                "edge_points": e,
                "floor_confidence": suggested_confidence(e, market, complete),
            })

        add("spread", "full", mkt_spread, proj.projected_spread)
        add("total", "full", mkt_total, proj.projected_total)
        add("spread", "h1", mkt_h1_spread, proj.projected_h1_spread)
        add("total", "h1", mkt_h1_total, proj.projected_h1_total)

        if candidates:
            rows.append({
                "event_id": g["event_id"],
                "kickoff": g["commence_time"],
                "matchup": f"{g['away_team']} @ {g['home_team']}",
                "home_team": g["home_team"],
                "away_team": g["away_team"],
                "neutral_site": neutral,
                "model": proj.to_dict(),
                "candidates": sorted(candidates,
                                     key=lambda c: abs(c["edge_points"]),
                                     reverse=True),
            })

    rows.sort(key=lambda r: max(abs(c["edge_points"]) for c in r["candidates"]),
              reverse=True)

    out = {
        "built_at": store.now_iso(),
        "season": args.season,
        "week": week,
        "hfa": args.hfa,
        "board_fetched_at": board.get("fetched_at"),
        "warning": age_note or None,
        "games_on_board": len(games),
        "games_with_candidates": len(rows),
        "games_skipped_unmapped": skipped,
        "unmapped_names": unmapped,
        "min_edge": args.min_edge,
        "slate": rows,
    }
    (store.DATA / "slate.json").write_text(json.dumps(out, indent=2))

    print(json.dumps({
        "week": week,
        "games_on_board": len(games),
        "games_with_candidates": len(rows),
        "games_skipped_unmapped": len(skipped),
        "top": [
            {"matchup": r["matchup"],
             "best": r["candidates"][0]["side"],
             "market": f"{r['candidates'][0]['market']}/{r['candidates'][0]['period']}",
             "edge": r["candidates"][0]["edge_points"],
             "floor_conf": r["candidates"][0]["floor_confidence"]}
            for r in rows[:15]
        ],
        "warning": age_note or None,
    }, indent=2))

    if unmapped:
        print("\nNAME MAPPING FAILED for these schools, so their games were "
              "left off the slate:", file=sys.stderr)
        for raw, hints in sorted(unmapped.items()):
            hint = f"  did you mean: {', '.join(hints)}" if hints else \
                   "  no close match in the CFBD team list"
            print(f"  {raw!r}\n{hint}", file=sys.stderr)
        print("\nFix by adding the spelling to VARIANTS in scripts/lib/teams.py, "
              "then rerun. Do not hand-edit the slate.", file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
