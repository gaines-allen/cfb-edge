#!/usr/bin/env python3
"""
The running card: the 6 leans the model likes best right now, tracked day
by day as the market moves.

A published pick freezes at the number it was taken at, and data/picks.json
is append only so those rows can never move. This is the other thing: a
live read on where the model still disagrees with the board today. What
looked good Monday often does not survive Thursday, because the market
moves toward the number and the gap closes. That closing is the story this
file tells.

Nothing here is a pick. Every entry sits under the 8.0 publish line by
construction, since the ratings model caps at 7.5 and cannot clear it
alone. These are leans, and the site labels them that way.

    python3 scripts/build_running_card.py
    python3 scripts/build_running_card.py --dry-run

Run it after make_slate, once per pull. It appends one observation per
lean per run and keeps the week's history, so the card can say a lean
entered Monday at 6.4 and sits at 5.1 today.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.runlog import RunLog  # noqa: E402

RUNNING_FILE = store.DATA / "running_card.json"
TOP_N = 6

# How far a lean can fall out of the top before it stops being shown at
# all. Keeping the near misses visible is what makes a drop legible rather
# than a disappearance.
SHOW_DROPPED = 4


def lean_key(event_id: str, c: dict) -> str:
    return f"{event_id}|{c['market']}|{c['period']}|{c['side']}"


def observe(slate: dict) -> dict[str, dict]:
    """Every candidate on today's slate, keyed, with today's numbers."""
    seen = {}
    for g in slate.get("slate", []):
        for c in g.get("candidates", []):
            if c.get("floor_confidence") is None:
                continue
            seen[lean_key(g["event_id"], c)] = {
                "event_id": g["event_id"],
                "matchup": g["matchup"],
                "kickoff": g.get("kickoff"),
                "market": c["market"],
                "period": c["period"],
                "side": c["side"],
                "line": c.get("bet_line"),
                "market_line": c.get("market_line"),
                "price": c.get("price"),
                "confidence": c["floor_confidence"],
                "sigma": c.get("edge_sigma"),
                "edge_points": c.get("edge_points"),
                "model_number": c.get("model_number"),
            }
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    log = RunLog("build_running_card", dry_run=args.dry_run)
    store.set_dry_run(args.dry_run, log)

    slate = store._load(store.DATA / "slate.json", {})
    if not slate.get("slate"):
        print(json.dumps({"leans": 0, "note": "no slate to read"}))
        log.finish(status="idle")
        return 0

    season, week = slate.get("season"), slate.get("week")
    at = slate.get("board_fetched_at") or store.now_iso()
    today = observe(slate)

    prior = store._load(RUNNING_FILE, {})
    # A new week starts a new card. Carrying last week's leans forward
    # would make a stale entry look like a lean that is holding.
    if prior.get("season") != season or prior.get("week") != week:
        prior = {"season": season, "week": week, "leans": {}}
    leans = prior.get("leans", {})

    for key, now in today.items():
        entry = leans.get(key)
        if entry is None:
            entry = {**now, "first_seen": at, "first_line": now["line"],
                     "first_confidence": now["confidence"], "history": []}
            leans[key] = entry
        # Refresh the live fields, keep the first ones untouched.
        for f in ("line", "market_line", "price", "confidence", "sigma",
                  "edge_points", "model_number", "matchup", "kickoff"):
            entry[f] = now[f]
        entry["last_seen"] = at
        if not entry["history"] or entry["history"][-1]["at"] != at:
            entry["history"].append({"at": at, "line": now["line"],
                                     "confidence": now["confidence"],
                                     "sigma": now["sigma"]})

    # A lean that has left the board entirely keeps its last reading and is
    # marked gone, because a lean that vanishes with no explanation is the
    # thing this file exists to prevent.
    for key, entry in leans.items():
        entry["on_board"] = key in today

    # One opinion per game. A spread lean and a total lean on the same
    # matchup are not two reads, they are one read sold twice, and six
    # plays that are really four opinions is a smaller and more
    # correlated book than the unit count claims. The weaker one stays
    # in the file, keeps its history and its rank, and is marked so the
    # page can say it was set aside rather than never seen.
    ranked = sorted(
        (e for e in leans.values() if e.get("on_board")),
        key=lambda e: -float(e.get("confidence") or 0))
    spoken_for = set()
    for e in ranked:
        ev = e.get("event_id")
        e["second_opinion"] = ev in spoken_for
        spoken_for.add(ev)

    for i, e in enumerate(
            [e for e in ranked if not e["second_opinion"]]
            + [e for e in ranked if e["second_opinion"]]):
        e["rank"] = i + 1
    for e in leans.values():
        if not e.get("on_board"):
            e["rank"] = None

    # Best rank a lean has ever held, which is what makes a fall legible.
    for e in leans.values():
        r = e.get("rank")
        best = e.get("peak_rank")
        if r is not None and (best is None or r < best):
            e["peak_rank"] = r

    def keys_in_rank_order(lo, hi):
        return [k for k, e in sorted(
            ((k, e) for k, e in leans.items()
             if e.get("rank") and lo <= e["rank"] <= hi),
            key=lambda kv: kv[1]["rank"])]

    payload = {
        "updated_at": store.now_iso(),
        "board_fetched_at": at,
        "season": season,
        "week": week,
        "top_n": TOP_N,
        "leans": leans,
        "card": keys_in_rank_order(1, TOP_N),
        "watch": keys_in_rank_order(TOP_N + 1, TOP_N + SHOW_DROPPED),
        # Held a top 6 spot on an earlier run and does not now. This is the
        # list that answers what stopped being a good bet since Monday.
        "dropped": [k for k, e in leans.items()
                    if e.get("peak_rank") and e["peak_rank"] <= TOP_N
                    and (e.get("rank") is None or e["rank"] > TOP_N)],
    }
    store.write_json(RUNNING_FILE, payload)

    out = {
        "season": season, "week": week,
        "leans_tracked": len(leans),
        "card": len(payload["card"]),
        "watch": len(payload["watch"]),
        "dropped": len(payload["dropped"]),
        "top_confidence": (leans[payload["card"][0]]["confidence"]
                           if payload["card"] else None),
    }
    print(json.dumps(out, indent=2))
    log.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
