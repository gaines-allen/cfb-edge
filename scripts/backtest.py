#!/usr/bin/env python3
"""
Does disagreeing with the market predict anything.

Everything else on this site assumes it does. Calibration centres the
model on the closing line, so "edge" means "disagrees with the book", and
nothing anywhere establishes that disagreement wins. Until this runs, the
card is a claim, and the accounting around it is a very tidy way of
counting the results of an untested claim.

What it measures: for every completed game it can price, how far the
model sat from the closing number, and whether the side the model
preferred actually covered. Bucketed by how large the disagreement was.
If a 6 point gap wins at the same rate as a 1 point gap, there is no edge
and the honest move is to stop staking and say so.

The lookahead problem, and what is done about it
------------------------------------------------
SP+ and FPI published at the end of a season are built from that season's
results. Using them to "predict" that season's games is not a backtest, it
is reading the answers. It would show a large, entirely fake edge.

So each season is projected using the previous season's ratings. That is
weaker than the live system, which uses current season ratings updated
weekly, so the number this produces is a floor rather than a replica.
A floor is the useful direction to be wrong in: if prior season ratings
already beat the close, the live model has something to work with. If they
do not, that is not proof the live model fails, and the report says so
rather than pretending otherwise.

Usage:
    python3 scripts/backtest.py --seasons 2021 2022 2023 2024
    python3 scripts/backtest.py --seasons 2023 --out data/backtest.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.cfbd import CFBDClient, CFBDError  # noqa: E402
from lib.model import build_rating_book, project_game  # noqa: E402
from lib.runlog import RunLog  # noqa: E402
from lib.scoring import wilson_interval  # noqa: E402
from lib.teams import canonical  # noqa: E402

# Edge buckets in points. The live board's median disagreement is near 1
# sigma, and the top tenth reach roughly 6 points on a spread, so the
# buckets have to separate small from large inside that range rather than
# lumping everything over 3 together.
BUCKETS = ((0.0, 1.5), (1.5, 3.0), (3.0, 4.5), (4.5, 6.0),
           (6.0, 9.0), (9.0, 999.0))

BREAKEVEN_PCT = 52.4
PROVIDER_ORDER = ("DraftKings", "Bovada", "ESPN Bet", "consensus")


def bucket_for(edge: float) -> str:
    e = abs(float(edge))
    for lo, hi in BUCKETS:
        if lo <= e < hi:
            return f"{lo:g} to {hi:g}" if hi < 999 else f"{lo:g}+"
    return "unclassified"


def grade_against_close(market: str, model_number: float, close_line: float,
                        home_score: int, away_score: int) -> str | None:
    """
    Which way the model leaned, and whether that side covered.

    Spreads are stored home negative, so a model number below the close
    means the model thinks the home team should be laying more than the
    book asks, and the play is home. Returns win, loss, push, or None when
    the model had no opinion.
    """
    margin = int(home_score) - int(away_score)
    if market == "spread":
        if model_number == close_line:
            return None
        take_home = model_number < close_line
        need = -float(close_line)
        if margin == need:
            return "push"
        covered = margin > need
        return "win" if covered == take_home else "loss"

    if market == "total":
        if model_number == close_line:
            return None
        total = int(home_score) + int(away_score)
        if total == close_line:
            return "push"
        return "win" if (total > close_line) == (model_number > close_line) \
            else "loss"

    return None


def pick_close(lines_for_game: list[dict], key: str):
    """
    The closing number, preferring the books this system actually uses.

    A game priced by 6 providers has 6 closes that disagree by up to a
    point, and picking the friendliest one is how a backtest flatters
    itself. The order is fixed in advance and the same for every game.
    """
    by_provider = {}
    for ln in lines_for_game or []:
        v = ln.get(key)
        if v is None:
            continue
        by_provider.setdefault(ln.get("provider"), float(v))
    for name in PROVIDER_ORDER:
        if name in by_provider:
            return by_provider[name], name
    if by_provider:
        name = sorted(by_provider)[0]
        return by_provider[name], name
    return None, None


def summarise(rows: list[dict]) -> dict:
    wins = sum(1 for r in rows if r["result"] == "win")
    losses = sum(1 for r in rows if r["result"] == "loss")
    pushes = sum(1 for r in rows if r["result"] == "push")
    decided = wins + losses
    interval = wilson_interval(wins, decided)
    win_pct = round(100.0 * wins / decided, 1) if decided else None
    verdict = "no_data"
    if interval:
        lo, hi = interval
        if lo > BREAKEVEN_PCT:
            verdict = "beats_breakeven"
        elif hi < BREAKEVEN_PCT:
            verdict = "below_breakeven"
        else:
            verdict = "no_signal"
    return {"games": len(rows), "wins": wins, "losses": losses,
            "pushes": pushes, "decided": decided, "win_pct": win_pct,
            "interval_95": interval, "verdict": verdict}


def season_rows(client: CFBDClient, year: int, log: RunLog) -> list[dict]:
    """Every gradeable game in one season, priced off the prior year."""
    book = build_rating_book(
        client.sp_ratings(year - 1), client.fpi(year - 1),
        client.srs(year - 1), [])
    if not book:
        log.event("backtest_no_ratings", year=year - 1)
        return []

    games = client.games(year)
    lines = client.lines(year)
    by_id = {}
    for ln in lines:
        by_id.setdefault(ln.get("id"), []).extend(ln.get("lines") or [])

    known = set(book)
    rows: list[dict] = []
    for g in games:
        hs, as_ = g.get("homePoints"), g.get("awayPoints")
        if hs is None or as_ is None:
            continue
        home = canonical(g.get("homeTeam") or "", known)
        away = canonical(g.get("awayTeam") or "", known)
        if not home or not away:
            continue
        proj = project_game(home, away, book,
                            neutral=bool(g.get("neutralSite")),
                            calibrate=False)
        priced = by_id.get(g.get("id")) or []
        for market, model_number, key in (
                ("spread", proj.projected_spread, "spread"),
                ("total", proj.projected_total, "overUnder")):
            if model_number is None:
                continue
            close, provider = pick_close(priced, key)
            if close is None:
                continue
            result = grade_against_close(market, model_number, close, hs, as_)
            if result is None:
                continue
            rows.append({
                "season": year, "week": g.get("week"),
                "matchup": f"{g.get('awayTeam')} @ {g.get('homeTeam')}",
                "market": market, "model_number": model_number,
                "close": close, "provider": provider,
                "edge": round(model_number - close, 2),
                "result": result,
            })
    return rows


def report(rows: list[dict]) -> dict:
    """The whole point: does a wider gap win more often than a narrow one."""
    out = {"overall": summarise(rows), "by_market": {}, "by_edge": {},
           "by_market_and_edge": {}, "by_season": {}}
    for market in sorted({r["market"] for r in rows}):
        out["by_market"][market] = summarise(
            [r for r in rows if r["market"] == market])
    for lo, hi in BUCKETS:
        label = f"{lo:g} to {hi:g}" if hi < 999 else f"{lo:g}+"
        out["by_edge"][label] = summarise(
            [r for r in rows if bucket_for(r["edge"]) == label])
        for market in out["by_market"]:
            out["by_market_and_edge"].setdefault(market, {})[label] = \
                summarise([r for r in rows if r["market"] == market
                           and bucket_for(r["edge"]) == label])
    for season in sorted({r["season"] for r in rows}):
        out["by_season"][season] = summarise(
            [r for r in rows if r["season"] == season])

    # The headline question, answered in one line. If the widest bucket
    # does not beat the narrowest, the size of a disagreement carries no
    # information and the confidence scale is decoration.
    ordered = [out["by_edge"][f"{lo:g} to {hi:g}" if hi < 999 else f"{lo:g}+"]
               for lo, hi in BUCKETS]
    rated = [b["win_pct"] for b in ordered
             if b["win_pct"] is not None and b["decided"] >= 30]
    out["gradient"] = {
        "buckets_with_enough_sample": len(rated),
        "narrowest": rated[0] if rated else None,
        "widest": rated[-1] if rated else None,
        "monotonic": rated == sorted(rated) if len(rated) > 1 else None,
        "spread_between_ends": (round(rated[-1] - rated[0], 1)
                                if len(rated) > 1 else None),
    }
    if rows:
        out["median_edge"] = round(
            statistics.median(abs(r["edge"]) for r in rows), 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, nargs="+", required=True)
    ap.add_argument("--out", default=str(store.DATA / "backtest.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and score but write nothing")
    args = ap.parse_args()

    log = RunLog("backtest", dry_run=args.dry_run)
    client = CFBDClient(log=log)

    rows: list[dict] = []
    for year in args.seasons:
        try:
            got = season_rows(client, year, log)
        except CFBDError as e:
            log.event("error", year=year, message=str(e))
            print(f"ERROR: {year}: {e}", file=sys.stderr)
            return 1
        log.event("backtest_season", year=year, rows=len(got))
        rows += got

    out = report(rows)
    out["seasons"] = args.seasons
    out["ratings_lag"] = ("each season priced off the prior season's "
                          "ratings, so this is a floor and not a replica "
                          "of the live model")
    out["generated_at"] = store.now_iso()

    print(json.dumps({k: v for k, v in out.items()
                      if k != "by_market_and_edge"}, indent=2))
    if not args.dry_run:
        Path(args.out).write_text(json.dumps(out, indent=2))
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
