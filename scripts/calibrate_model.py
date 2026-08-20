#!/usr/bin/env python3
"""
Measures how far the model sits from the market, and writes the result to
data/model_calibration.json.

Two numbers per market. Bias is the average signed gap, and it gets
subtracted, because a model that is a point light on every total does not
have an edge on every total, it has an offset. Sigma is the spread of what
is left, and it is the number that matters: it converts a raw points
disagreement into how unusual that disagreement actually is.

Measured on the first live board: totals ran 0.95 points under market with
a 3.25 point sigma, spreads 0.58 over with 2.77. So a six point total gap,
which sounds enormous, is under two standard deviations.

    python3 scripts/calibrate_model.py
    python3 scripts/calibrate_model.py --season 2026
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.runlog import RunLog  # noqa: E402
from lib.cfbd import CFBDClient, CFBDError, current_week  # noqa: E402
from lib.schema import ShapeError  # noqa: E402
from lib.model import CALIBRATION_FILE, build_rating_book, project_game  # noqa: E402
from lib.teams import canonical  # noqa: E402

MIN_SAMPLE = 20


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    log = RunLog("calibrate_model", dry_run=args.dry_run)
    store.set_dry_run(args.dry_run, log)

    games = store.load_board().get("games", [])
    if not games:
        print("ERROR: data/board.json is empty. Run fetch_odds.py first.",
              file=sys.stderr)
        return 2

    cfbd = CFBDClient()
    try:
        week = args.week or current_week(cfbd.calendar(args.season)) or 1
        book = build_rating_book(
            cfbd.sp_ratings(args.season), cfbd.fpi(args.season),
            cfbd.srs(args.season), cfbd.elo(args.season, week=week),
        )
    except ShapeError as e:
        log.error(str(e))
        print(f"SHAPE ERROR: {e}", file=sys.stderr)
        return 5
    except CFBDError as e:
        log.error(str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    known = set(book)
    total_gaps: list[float] = []
    spread_gaps: list[float] = []

    for g in games:
        home = canonical(g["home_team"], known)
        away = canonical(g["away_team"], known)
        if not home or not away:
            continue
        proj = project_game(home, away, book, calibrate=False)
        lines = g.get("lines", [])

        mkt_total = next((ln["point"] for ln in lines
                          if ln["market"] == "totals" and ln["side"] == "Over"), None)
        mkt_spread = next((ln["point"] for ln in lines
                           if ln["market"] == "spreads"
                           and ln["side"] == g["home_team"]), None)

        if mkt_total is not None and proj.projected_total is not None:
            total_gaps.append(proj.projected_total - float(mkt_total))
        if mkt_spread is not None and proj.projected_spread is not None:
            spread_gaps.append(proj.projected_spread - float(mkt_spread))

    def summarize(gaps: list[float]) -> dict | None:
        if len(gaps) < MIN_SAMPLE:
            return None
        return {
            "n": len(gaps),
            "bias": round(statistics.mean(gaps), 3),
            "median": round(statistics.median(gaps), 3),
            "sigma": round(statistics.pstdev(gaps), 3),
        }

    totals = summarize(total_gaps)
    spreads = summarize(spread_gaps)

    if not totals and not spreads:
        print(f"ERROR: fewer than {MIN_SAMPLE} rated games on the board, "
              "not enough to calibrate against.", file=sys.stderr)
        return 4

    payload = {
        "measured_at": store.now_iso(),
        "season": args.season,
        "week": week,
        "totals": totals,
        "spreads": spreads,
        "note": ("Bias is subtracted from the model number. Sigma converts a "
                 "points gap into a z score. Re-measure when ratings update, "
                 "which in season means weekly."),
    }
    store.write_json(CALIBRATION_FILE, payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
