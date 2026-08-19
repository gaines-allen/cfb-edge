#!/usr/bin/env python3
"""
grader's tool. Pulls finals, settles every pending pick including halves,
recomputes the record, and rewrites the calibration and factor scorecards
that the handicapper reads before the next slate.

    python3 scripts/grade_results.py
    python3 scripts/grade_results.py --season 2026 --week 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.cfbd import CFBDClient, CFBDError  # noqa: E402
from lib.scoring import (  # noqa: E402
    breakdown,
    calibration_table,
    grade_pick,
    payout,
    summarize,
)

# The Odds API and CFBD do not always spell a school the same way.
ALIASES = {
    "ole miss": "mississippi",
    "miami (fl)": "miami",
    "miami (oh)": "miami (oh)",
    "usc": "usc",
    "ul monroe": "louisiana monroe",
    "ul lafayette": "louisiana",
    "sam houston state": "sam houston",
    "app state": "appalachian state",
    "utsa": "ut san antonio",
    "hawai'i": "hawaii",
    "san jose state": "san josé state",
}


def norm(name: str) -> str:
    n = (name or "").strip().lower()
    n = n.replace("&", "and").replace(".", "").replace("'", "'")
    n = " ".join(n.split())
    return ALIASES.get(n, n)


def build_result_index(cfbd_games: list[dict]) -> dict:
    """Key finals by a normalized (home, away) pair."""
    idx = {}
    for g in cfbd_games:
        home = g.get("homeTeam") or g.get("home_team")
        away = g.get("awayTeam") or g.get("away_team")
        if not home or not away:
            continue
        idx[(norm(home), norm(away))] = {
            "home_team": home,
            "away_team": away,
            "home_score": g.get("homePoints", g.get("home_points")),
            "away_score": g.get("awayPoints", g.get("away_points")),
            "home_line_scores": g.get("homeLineScores", g.get("home_line_scores")),
            "away_line_scores": g.get("awayLineScores", g.get("away_line_scores")),
            "completed": bool(g.get("completed")),
            "neutral_site": bool(g.get("neutralSite", g.get("neutral_site"))),
        }
    return idx


def match_game(pick: dict, idx: dict) -> dict | None:
    """Match on the matchup string, both orientations."""
    mu = pick.get("matchup", "")
    if " @ " in mu:
        away, home = mu.split(" @ ", 1)
    elif " vs " in mu:
        away, home = mu.split(" vs ", 1)
    else:
        return None
    key = (norm(home), norm(away))
    if key in idx:
        return idx[key]
    flipped = (norm(away), norm(home))
    return idx.get(flipped)


def rebuild_memory(picks: list[dict], season: int) -> dict:
    """
    Recompute every learning surface from the full pick history. The grader
    agent then layers written lessons on top of these numbers.
    """
    mem = store.load_memory()
    graded = [p for p in picks if p.get("result") in ("win", "loss", "push")]
    live = [p for p in graded if p.get("live")]

    for p in graded:
        p.setdefault("side_role", "n/a")

    mem["calibration"] = calibration_table(graded)
    mem["by_market"] = breakdown(live, "market")
    mem["by_period"] = breakdown(live, "period")
    mem["by_role"] = breakdown(live, "side_role")

    # Factor scorecard: for every factor the handicapper cited, how have
    # picks citing it actually done? This is the feedback loop.
    scorecard: dict[str, dict] = {}
    for p in live:
        for factor in (p.get("factors") or {}):
            bucket = scorecard.setdefault(
                factor, {"picks": 0, "wins": 0, "losses": 0, "pushes": 0, "units": 0.0}
            )
            bucket["picks"] += 1
            if p["result"] == "win":
                bucket["wins"] += 1
            elif p["result"] == "loss":
                bucket["losses"] += 1
            else:
                bucket["pushes"] += 1
            bucket["units"] = round(bucket["units"] + float(p.get("units_net", 0)), 2)

    for name, b in scorecard.items():
        decided = b["wins"] + b["losses"]
        b["win_pct"] = round(100.0 * b["wins"] / decided, 1) if decided else 0.0
    mem["factor_scorecard"] = dict(
        sorted(scorecard.items(), key=lambda kv: kv[1]["units"], reverse=True)
    )

    seasons = sorted({p.get("season") for p in picks if p.get("season")})
    mem["seasons_tracked"] = seasons
    mem["overall"] = summarize(live)
    mem["overall_including_shadow"] = summarize(graded)
    store.save_memory(mem)
    return mem


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    picks = store.load_picks()
    pending = [p for p in picks if p.get("result") == "pending"]

    if not pending:
        mem = rebuild_memory(picks, args.season)
        print(json.dumps({"graded": 0, "note": "nothing pending",
                          "record": mem.get("overall", {})}, indent=2))
        return 0

    weeks = sorted({p.get("week") for p in pending if p.get("week")})
    if args.week:
        weeks = [args.week]

    cfbd = CFBDClient()
    idx: dict = {}
    for wk in weeks:
        try:
            idx.update(build_result_index(cfbd.games(args.season, week=wk)))
        except CFBDError as e:
            print(f"WARNING: could not load week {wk}: {e}", file=sys.stderr)

    graded_now, unmatched = 0, []
    for p in pending:
        game = match_game(p, idx)
        if not game:
            unmatched.append(p["matchup"])
            continue
        if not game.get("completed"):
            continue

        outcome = grade_pick(
            {"market": p["market"], "period": p["period"],
             "side": p["side"], "line": p.get("line")},
            game,
        )
        if outcome == "pending":
            continue

        p["result"] = outcome
        p["units_net"] = round(
            payout(float(p.get("units", 1.0)), int(p.get("price", -110)), outcome), 3
        )
        p["final_score"] = (
            f"{game['away_team']} {game['away_score']} - "
            f"{game['home_team']} {game['home_score']}"
        )
        p["graded_at"] = store.now_iso()
        graded_now += 1

    store.save_picks(picks)
    mem = rebuild_memory(picks, args.season)

    out = {
        "graded": graded_now,
        "still_pending": sum(1 for p in picks if p.get("result") == "pending"),
        "unmatched": sorted(set(unmatched)),
        "record_live": mem.get("overall", {}),
        "calibration": mem.get("calibration", []),
    }
    if not args.quiet:
        print(json.dumps(out, indent=2))
    if unmatched:
        print(f"WARNING: {len(set(unmatched))} matchups did not map to a CFBD game. "
              "Check ALIASES in grade_results.py.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
