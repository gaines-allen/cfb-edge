#!/usr/bin/env python3
"""
Score the model against every game of the week, not only the ones it bet.

The public record is the card and stays the card. 2 graded picks a week
tells you almost nothing about whether the numbers are any good, and it
takes a season to find out. Every game on the board is a free test of the
same model, already paid for, and there are 40 of them a week.

So this settles nothing and publishes nothing. It measures: how far the
model's spread and total landed from the result, on every game, and where
the misses cluster. That is what the grader reads before it writes a
lesson, and what makes a lesson about more than a 2 pick sample.

The separation matters and is enforced by tests. Nothing here can mark a
pick won or lost, and nothing here reaches the record.
"""

from __future__ import annotations

import argparse
import json
import functools
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.runlog import RunLog  # noqa: E402
from lib.teams import canonical, load_logos, normalize  # noqa: E402

REVIEW_FILE = store.DATA / "week_review.json"
# Every game the model has ever priced, kept because the slate does not.
ARCHIVE_FILE = store.DATA / "model_archive.json"


@functools.lru_cache(maxsize=1)
def _known() -> frozenset:
    """
    The school list canonical() resolves against, read once.

    Without it canonical returns None for most of the board, including
    "Colorado State Rams", and the fallback then keeps the mascot in the
    key while CFBD does not. Every consumer of canonical in this repo
    passes this set. Two that did not both failed silently.
    """
    return frozenset(load_logos())


def key_for(name) -> str:
    """
    One school, one key, across sources that spell it differently.

    canonical() is the mascot map and resolves the odds board's "Hawaii
    Rainbow Warriors" and CFBD's "Hawai'i" to the same school. It returns
    None for anything it cannot place, and a lot of the FCS opponents on
    an early season board are not in it, so those fall back to the folded
    text. Falling back keeps them scored instead of silently dropped, and
    it cannot create a false match: 2 schools it cannot place still have
    to spell the same.
    """
    return canonical(str(name), _known()) or normalize(str(name))


def archive(slate: dict, prior: dict | None = None) -> dict:
    """
    Remember what the model said, because the slate forgets.

    make_slate rebuilds daily from the games still to come, so a game
    drops off the moment it kicks. Reviewing the current slate against
    last weekend's finals compares next week's fixtures to last week's
    results and matches nothing, which is exactly what it did: 0 of 39,
    reported as a clean run.

    The first number stands. A game is written once, on the first day the
    model priced it, so this is what was actually believed going in
    rather than a number tuned as the week went on.
    """
    out = dict((prior or {}).get("games") or {})
    for g in slate.get("slate", []):
        ev = g.get("event_id")
        if not ev or ev in out:
            continue
        m = g.get("model") or {}
        if m.get("projected_spread") is None and m.get("projected_total") is None:
            continue
        out[ev] = {
            "season": slate.get("season"), "week": slate.get("week"),
            "matchup": g.get("matchup"),
            "home_team": g.get("home_team"), "away_team": g.get("away_team"),
            "kickoff": g.get("kickoff"),
            "first_seen": store.now_iso(),
            "model": {"projected_spread": m.get("projected_spread"),
                      "projected_total": m.get("projected_total")},
        }
    return {"updated_at": store.now_iso(), "games": out}


def index_results(games: list[dict]) -> dict:
    """
    Finals keyed by the canonical school, the way grading does it.

    Keyed on normalize() first, which folds punctuation but keeps the
    mascot, so CFBD's "Hawai'i" and the odds board's "Hawaii Rainbow
    Warriors" landed in different buckets and 39 of 39 games went
    unmatched. canonical() is the mascot map and resolves both to one
    school. A name it cannot place is left out rather than guessed at.
    """
    idx = {}
    for g in games:
        home = g.get("homeTeam") or g.get("home_team")
        away = g.get("awayTeam") or g.get("away_team")
        hp = g.get("homePoints", g.get("home_points"))
        ap = g.get("awayPoints", g.get("away_points"))
        if not home or not away or hp is None or ap is None:
            continue
        if not g.get("completed", True):
            continue
        idx[(key_for(home), key_for(away))] = {
            "home_score": float(hp), "away_score": float(ap)}
    return idx


def score_game(game: dict, final: dict) -> dict | None:
    """
    One game, model against result.

    Spread error is signed the way the model signs it, negative when the
    home team is laying, so a positive error means the model had the home
    team too strong. Total error is positive when the model was too high.
    """
    model = game.get("model") or {}
    sp, tot = model.get("projected_spread"), model.get("projected_total")
    if sp is None and tot is None:
        return None
    hs, as_ = final["home_score"], final["away_score"]
    actual_margin = hs - as_
    actual_total = hs + as_
    out = {
        "matchup": game.get("matchup"),
        "home_score": hs, "away_score": as_,
        "actual_margin": actual_margin, "actual_total": actual_total,
    }
    if sp is not None:
        # projected_spread is negative when home is favored, so the margin
        # it predicts is its negation.
        out["model_margin"] = -sp
        out["spread_error"] = round(-sp - actual_margin, 1)
    if tot is not None:
        out["model_total"] = tot
        out["total_error"] = round(tot - actual_total, 1)
    return out


def summarize(rows: list[dict]) -> dict:
    """Where the misses cluster, in the only terms that suggest a fix."""
    def stats(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return None
        return {
            "games": len(vals),
            "mean_error": round(st.mean(vals), 2),
            "mean_absolute_error": round(st.mean([abs(v) for v in vals]), 2),
            "median_absolute_error": round(st.median([abs(v) for v in vals]), 2),
            "worst": round(max(vals, key=abs), 1),
        }

    # A model that is only wrong on blowouts is a different problem from
    # one that is wrong everywhere, and the fix is different too.
    big = [r for r in rows if abs(r.get("model_margin") or 0) >= 21]
    close = [r for r in rows if abs(r.get("model_margin") or 0) < 21]
    return {
        "spread": stats("spread_error"),
        "total": stats("total_error"),
        "on_big_spreads": {
            "games": len(big),
            "mean_absolute_error": (
                round(st.mean([abs(r["spread_error"]) for r in big]), 2)
                if big else None),
        },
        "on_close_games": {
            "games": len(close),
            "mean_absolute_error": (
                round(st.mean([abs(r["spread_error"]) for r in close]), 2)
                if close else None),
        },
    }


def review(slate: dict, results: list[dict], archived: dict | None = None,
           week=None) -> dict:
    """
    Score against everything the model has priced, not only what is still
    on the board. `slate` is accepted for the shape its games use; the
    archive is what makes a played game reviewable at all.
    """
    idx = index_results(results)
    games = list(slate.get("slate", []))
    seen = {g.get("event_id") for g in games}
    for ev, a in ((archived or {}).get("games") or {}).items():
        if ev in seen:
            continue
        if week is not None and a.get("week") != week:
            continue
        games.append(a)

    rows, unmatched = [], []
    for g in games:
        home, away = g.get("home_team"), g.get("away_team")
        final = idx.get((key_for(home or ""), key_for(away or "")))
        if not final:
            unmatched.append(g.get("matchup"))
            continue
        row = score_game(g, final)
        if row:
            rows.append(row)

    rows.sort(key=lambda r: abs(r.get("spread_error") or 0), reverse=True)
    return {
        "season": slate.get("season"),
        "week": slate.get("week"),
        "reviewed_at": store.now_iso(),
        "games_scored": len(rows),
        "games_unmatched": unmatched,
        "summary": summarize(rows),
        # Worst first, because that is where a lesson comes from.
        "games": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    log = RunLog("review_week", dry_run=args.dry_run)

    slate = store._load(store.DATA / "slate.json", {})
    results = store._load(store.DATA / "results.json", {})
    games = results.get("games", []) if isinstance(results, dict) else results

    prior = store._load(ARCHIVE_FILE, {})
    arch = archive(slate, prior)
    if not args.dry_run:
        store._save(ARCHIVE_FILE, arch)

    out = review(slate, games or [], arch, week=slate.get("week"))
    log.event("week_reviewed", season=out["season"], week=out["week"],
              scored=out["games_scored"], unmatched=len(out["games_unmatched"]))
    if args.dry_run:
        print(json.dumps(out["summary"], indent=2))
        return 0
    store._save(REVIEW_FILE, out)
    print(json.dumps(out["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
