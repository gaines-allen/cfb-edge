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
from lib.runlog import RunLog  # noqa: E402
from lib.cfbd import CFBDClient, CFBDError, current_week  # noqa: E402
from lib.schema import ShapeError  # noqa: E402
from lib.model import (  # noqa: E402
    DEFAULT_HFA,
    build_rating_book,
    edge_vs_market,
    load_calibration,
    project_game,
    suggested_confidence,
    z_score,
)
from lib.teams import MASCOT_MAP, canonical, suggest  # noqa: E402


def find(lines: list[dict], market: str, side: str) -> dict | None:
    for ln in lines:
        if ln.get("market") == market and ln.get("side") == side:
            return ln
    return None


def resolve_week(requested, cal, season):
    """
    Pick the week to work, and refuse rather than substitute.

    Week 0 is the trap this exists for. It is a real thing people ask for,
    and 0 is falsy, so "requested or current_week(cal)" threw it away and
    quietly returned a different week with no warning. CFBD also folds week
    0 games into week 1 for every season from 2023 to 2026, so a request
    for 0 has no data behind it at all.
    """
    weeks = sorted({int(w["week"]) for w in cal
                    if w.get("seasonType") == "regular"
                    and w.get("week") is not None})
    if requested is None:
        return current_week(cal) or (weeks[0] if weeks else 1)
    requested = int(requested)
    if weeks and requested not in weeks:
        raise ValueError(
            f"season {season} has no week {requested}. The calendar carries "
            f"weeks {weeks[0]} to {weeks[-1]}. CFBD folds week 0 openers into "
            f"week 1, so ask for week {weeks[0]}."
        )
    return requested

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--min-edge", type=float, default=1.5,
                    help="points of disagreement needed to list a candidate")
    ap.add_argument("--hfa", type=float, default=DEFAULT_HFA)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    log = RunLog("make_slate", dry_run=args.dry_run)
    store.set_dry_run(args.dry_run, log)

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

    cfbd = CFBDClient(log=log)
    try:
        cal = cfbd.calendar(args.season)
        week = resolve_week(args.week, cal, args.season)
        sp = cfbd.sp_ratings(args.season)
        srs = cfbd.srs(args.season)
        elo = cfbd.elo(args.season, week=week)
        fpi = cfbd.fpi(args.season)
        sched = cfbd.games(args.season, week=week)
    except ValueError as e:
        log.error(str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return 4
    except ShapeError as e:
        # An upstream field changed meaning. Stop rather than project a
        # confident number off a payload nobody has looked at.
        log.error(str(e))
        print(f"SHAPE ERROR: {e}", file=sys.stderr)
        return 5
    except CFBDError as e:
        log.error(str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    book = build_rating_book(sp, fpi, srs, elo)
    known = set(book.keys())
    calibration = load_calibration()

    neutral_by_pair = {}
    for g in sched:
        h, a = g.get("homeTeam"), g.get("awayTeam")
        if h and a:
            neutral_by_pair[(h, a)] = bool(g.get("neutralSite"))

    # The board runs weeks ahead, so filter to the target week's window
    # rather than rating a November game off a preseason number.
    window = next((w for w in cal if int(w.get("week", -1)) == int(week)
                   and w.get("seasonType") == "regular"), None)
    lo = hi = None
    if window:
        try:
            lo = datetime.fromisoformat(window["firstGameStart"].replace("Z", "+00:00"))
            hi = datetime.fromisoformat(window["lastGameStart"].replace("Z", "+00:00"))
            hi = hi.replace(hour=23, minute=59)
        except (KeyError, ValueError, AttributeError):
            lo = hi = None

    def in_week(iso_ts: str) -> bool:
        if lo is None or hi is None:
            return True
        try:
            k = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return True
        return lo <= k <= hi

    rows = []
    unmapped: dict[str, list[str]] = {}
    unrated: list[str] = []
    skipped, out_of_week = [], 0

    # Everything the alias map knows about, FBS or not. Used to tell a name
    # we cannot place apart from a school that simply has no FBS rating.
    all_schools = set(MASCOT_MAP.values()) | known

    for g in games:
        if not in_week(g.get("commence_time", "")):
            out_of_week += 1
            continue

        home = canonical(g["home_team"], known)
        away = canonical(g["away_team"], known)

        # Two different failures that must not be conflated. A school with
        # no FBS rating is an FCS opponent and dropping the game is correct,
        # since a buy game is not a bet. A name nothing recognizes is a bug
        # in the matcher and has to be loud.
        if home is None or away is None:
            for raw, mapped in ((g["home_team"], home), (g["away_team"], away)):
                if mapped is not None:
                    continue
                # Recognized by the alias map or the variant table counts as
                # a real school. UTRGV is in neither ESPN's list nor the FBS
                # ratings because the program only started in 2025, but the
                # variant table knows it, and that is enough to call it an
                # unrated opponent rather than a matcher bug.
                if canonical(raw, all_schools) or canonical(raw):
                    if raw not in unrated:
                        unrated.append(raw)
                elif raw not in unmapped:
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
                "edge_sigma": z_score(e, market, calibration),
                "floor_confidence": suggested_confidence(e, market, complete,
                                                         calibration),
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
        "games_dropped": skipped,
        "games_outside_week": out_of_week,
        "unrated_schools": sorted(unrated),
        "unmapped_names": unmapped,
        "min_edge": args.min_edge,
        "calibration": calibration,
        "slate": rows,
    }
    wrote = store.write_json(store.DATA / "slate.json", out)

    print(json.dumps({
        "week": week,
        "games_on_board": len(games),
        "games_with_candidates": len(rows),
        "games_dropped_no_rating": len(skipped),
        "games_outside_week": out_of_week,
        "unrated_schools": len(unrated),
        "unmapped_names": len(unmapped),
        "top": [
            {"matchup": r["matchup"],
             "best": r["candidates"][0]["side"],
             "market": f"{r['candidates'][0]['market']}/{r['candidates'][0]['period']}",
             "edge": r["candidates"][0]["edge_points"],
             "sigma": r["candidates"][0]["edge_sigma"],
             "floor_conf": r["candidates"][0]["floor_confidence"]}
            for r in rows[:15]
        ],
        "warning": age_note or None,
    }, indent=2))

    if unrated:
        print(f"\nDropped {len(skipped)} games. {len(unrated)} schools have no FBS "
              "rating, which is expected for FCS opponents and means those games "
              "are correctly off the card:", file=sys.stderr)
        print("  " + ", ".join(sorted(unrated)[:12])
              + (" ..." if len(unrated) > 12 else ""), file=sys.stderr)

    if unmapped:
        print("\nNAME MAPPING FAILED for these schools. This is a matcher bug, "
              "not an FCS game:", file=sys.stderr)
        for raw, hints in sorted(unmapped.items()):
            hint = f"  did you mean: {', '.join(hints)}" if hints else \
                   "  no close match in the CFBD team list"
            print(f"  {raw!r}\n{hint}", file=sys.stderr)
        print("\nFix by adding the spelling to VARIANTS in scripts/lib/teams.py, "
              "then rerun. Do not hand-edit the slate.", file=sys.stderr)
        return 4

    log.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
