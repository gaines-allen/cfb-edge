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
from datetime import datetime, timezone
from urllib.parse import urlparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.runlog import RunLog  # noqa: E402
from lib.cfbd import CFBDClient, CFBDError  # noqa: E402
from lib.schema import ShapeError  # noqa: E402
from lib.scoring import (  # noqa: E402
    annotate,
    breakdown,
    calibration_table,
    grade_pick,
    payout,
    summarize,
)
from lib.store import LIVE_THRESHOLD  # noqa: E402
from lib.teams import canonical, normalize, suggest  # noqa: E402


def build_result_index(cfbd_games: list[dict]) -> tuple[dict, set[str]]:
    """Key finals by a normalized (home, away) pair, plus the team universe."""
    idx: dict = {}
    known: set[str] = set()
    for g in cfbd_games:
        home = g.get("homeTeam") or g.get("home_team")
        away = g.get("awayTeam") or g.get("away_team")
        if not home or not away:
            continue
        known.update((home, away))
        idx[(normalize(home), normalize(away))] = {
            "home_team": home,
            "away_team": away,
            "home_score": g.get("homePoints", g.get("home_points")),
            "away_score": g.get("awayPoints", g.get("away_points")),
            "home_line_scores": g.get("homeLineScores", g.get("home_line_scores")),
            "away_line_scores": g.get("awayLineScores", g.get("away_line_scores")),
            "completed": bool(g.get("completed")),
            "neutral_site": bool(g.get("neutralSite", g.get("neutral_site"))),
        }
    return idx, known


def split_matchup(matchup: str) -> tuple[str, str] | None:
    if " @ " in matchup:
        away, home = matchup.split(" @ ", 1)
    elif " vs " in matchup:
        away, home = matchup.split(" vs ", 1)
    else:
        return None
    return away.strip(), home.strip()


def match_game(pick: dict, idx: dict, known: set[str]) -> dict | None:
    """
    Resolve a pick's matchup to a CFBD game. Tries both orientations, since
    a neutral site game can be listed either way by either source.
    """
    parts = split_matchup(pick.get("matchup", ""))
    if not parts:
        return None
    away_raw, home_raw = parts

    home = canonical(home_raw, known) or home_raw
    away = canonical(away_raw, known) or away_raw

    for key in ((normalize(home), normalize(away)),
                (normalize(away), normalize(home))):
        if key in idx:
            return idx[key]
    return None


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
        # A bare record invites the handicapper to act on 4 picks. The
        # verdict says whether the record can carry that weight at all.
        annotate(b)
    mem["factor_scorecard"] = dict(
        sorted(scorecard.items(), key=lambda kv: kv[1]["units"], reverse=True)
    )

    _research_scorecard(mem, live)

    seasons = sorted({p.get("season") for p in picks if p.get("season")})
    mem["seasons_tracked"] = seasons
    mem["overall"] = summarize(live)
    mem["overall_including_shadow"] = summarize(graded)
    store.save_memory(mem)
    return mem


def _research_scorecard(mem: dict, picks: list[dict]) -> None:
    """
    How the research layer has actually done.

    The ratings model caps at 7.5 and the publish line is 8.0, so research
    is the only thing that can put a play on the card, and it was the only
    layer with nothing grading it. Two questions: does a pick with
    research beat one without, and do the outlets being cited predict
    anything.

    verify_sources.py already proves a quote is on the page it claims. It
    cannot prove the quote supports the pick, that the outlet is any good,
    or that nobody went looking for whatever would clear the gate. Only
    the record answers that, so the record keeps score of it.
    """
    live = [p for p in picks
            if float(p.get("confidence", 0) or 0) >= LIVE_THRESHOLD
            and p.get("result") in ("win", "loss", "push")]
    card: dict[str, dict] = {}

    def bucket(where: str, p: dict) -> None:
        b = card.setdefault(where, {"picks": 0, "wins": 0, "losses": 0,
                                    "pushes": 0, "units": 0.0})
        b["picks"] += 1
        b[{"win": "wins", "loss": "losses"}.get(p["result"], "pushes")] += 1
        b["units"] = round(b["units"] + float(p.get("units_net", 0) or 0), 2)

    for p in live:
        srcs = p.get("sources") or []
        bucket("researched" if srcs else "model only", p)
        # A set, so citing one outlet 3 times in a pick is 1 pick for that
        # outlet rather than 3, which would let a favourite source inflate
        # its own sample.
        for host in sorted({_host(sp.get("url")) for sp in srcs} - {""}):
            bucket(f"source: {host}", p)

    for b in card.values():
        decided = b["wins"] + b["losses"]
        b["win_pct"] = round(100.0 * b["wins"] / decided, 1) if decided else 0.0
        annotate(b)
    mem["research_scorecard"] = dict(
        sorted(card.items(), key=lambda kv: kv[1]["units"], reverse=True))


def _host(url) -> str:
    """The domain a source came from, for keeping score by outlet."""
    try:
        return (urlparse(str(url)).hostname or "").lower().removeprefix("www.")
    except (ValueError, AttributeError):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    log = RunLog("grade_results", dry_run=args.dry_run)
    store.set_dry_run(args.dry_run, log)

    picks = store.load_picks()
    pending = [p for p in picks if p.get("result") == "pending"]

    if not pending:
        mem = rebuild_memory(picks, args.season)
        print(json.dumps({"graded": 0, "note": "nothing pending",
                          "record": mem.get("overall", {})}, indent=2))
        log.finish()
        return 0

    weeks = sorted({p.get("week") for p in pending if p.get("week")})
    if args.week:
        weeks = [args.week]

    cfbd = CFBDClient(log=log)
    idx: dict = {}
    known: set[str] = set()
    fetched: list[dict] = []
    for wk in weeks:
        try:
            games = cfbd.games(args.season, week=wk)
            fetched.extend(games)
            i, k = build_result_index(games)
            idx.update(i)
            known.update(k)
        except ShapeError as e:
            # Never downgrade this to a warning. Grading against a payload
            # whose fields changed meaning writes a wrong result into an
            # append only ledger, and those rows cannot be moved afterwards.
            log.error(str(e), week=wk)
            print(f"SHAPE ERROR on week {wk}: {e}", file=sys.stderr)
            return 5
        except CFBDError as e:
            log.error(str(e), week=wk)
            print(f"WARNING: could not load week {wk}: {e}", file=sys.stderr)

    # Keep what was fetched. The finals were being pulled, used in memory
    # and dropped, so data/results.json sat at {} while every consumer of
    # it read an empty file and reported nothing wrong. review_week.py
    # scored 0 of 39 games for exactly this reason.
    if fetched:
        store._save(store.DATA / "results.json",
                    {"season": args.season, "weeks": weeks,
                     "fetched_at": store.now_iso(), "games": fetched})

    graded_now, unmatched = 0, []
    for p in pending:
        game = match_game(p, idx, known)
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

    # A pick whose game has finished and is still pending is the same
    # failure as an unmatched one: it quietly leaves the record instead of
    # scoring against it. On 30 August both card picks sat pending after
    # their games were over, the step reported success, and nothing said a
    # word. Annotate it so the run list carries the reason.
    stale = []
    for pk in picks:
        if pk.get("result") != "pending" or not pk.get("live"):
            continue
        ko = pk.get("kickoff")
        if not ko:
            continue
        try:
            when = datetime.fromisoformat(str(ko).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
        if hours > 6:
            stale.append(f"{pk['matchup']} ({hours:.0f}h past kickoff)")
    if stale:
        print(f"::warning::{len(stale)} published pick(s) still pending well "
              f"after kickoff: " + "; ".join(stale), file=sys.stderr)
        for line in stale:
            print(f"STILL PENDING: {line}", file=sys.stderr)

    if unmatched:
        # An unmatched pick never settles. It sits pending forever, quietly
        # missing from the record, which is the most dangerous failure this
        # system has. Make it loud and make it non-zero.
        print(f"\nUNMATCHED: {len(set(unmatched))} matchups did not map to a "
              "CFBD game, so those picks are still pending:", file=sys.stderr)
        for mu in sorted(set(unmatched)):
            parts = split_matchup(mu)
            hints = ""
            if parts and known:
                bad = [t for t in parts if canonical(t, known) is None]
                if bad:
                    hints = "; ".join(
                        f"{t!r} -> try {', '.join(suggest(t, known)) or 'no close match'}"
                        for t in bad
                    )
            print(f"  {mu}{'  (' + hints + ')' if hints else ''}", file=sys.stderr)
        print("\nAdd the spelling to VARIANTS in scripts/lib/teams.py and rerun. "
              "Never hand-edit a result into picks.json.", file=sys.stderr)
        return 5

    log.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
