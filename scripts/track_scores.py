#!/usr/bin/env python3
"""
Saturday's scoreboard. Polls the scores endpoint while card games run and
writes data/live_scores.json for the site to render.

Two rules keep this safe and cheap. It never touches the ledger: the file
it writes is informational, the covering flag on the site is where a bet
stands right now, and grading stays with the grader on Sunday, which has
the quarter detail this feed does not. And it spends nothing when there is
nothing to watch: if no live pick is pending with a kickoff inside the
window, it exits before a client is even constructed, so a bye week or an
unmerged card costs 0 credits.

    python3 scripts/track_scores.py
    python3 scripts/track_scores.py --dry-run

The scores call costs 2 credits. Every 30 minutes from noon to midnight
Eastern is 24 polls, about 50 credits a Saturday, and only on Saturdays
where a card is actually live.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.runlog import RunLog  # noqa: E402

LIVE_FILE = store.DATA / "live_scores.json"

# How far around now a kickoff has to sit for the game to be worth a poll.
# 6 hours back covers a game in its 4th quarter, 8 forward covers the
# evening slate from an afternoon poll.
LOOKBACK_HOURS = 6
LOOKAHEAD_HOURS = 8


def watchable(picks: list[dict], now: datetime) -> list[dict]:
    out = []
    for p in picks:
        if not p.get("live") or p.get("result") != "pending":
            continue
        try:
            kick = datetime.fromisoformat(
                str(p.get("kickoff", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - timedelta(hours=LOOKBACK_HOURS) <= kick \
                <= now + timedelta(hours=LOOKAHEAD_HOURS):
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    log = RunLog("track_scores", dry_run=args.dry_run)
    store.set_dry_run(args.dry_run, log)
    now = datetime.now(timezone.utc)

    watch = watchable(store.load_picks(), now)
    if not watch:
        # The cheap exit. No client, no call, no credits.
        print(json.dumps({"watching": 0, "note": "no live pick inside the "
                          "window, nothing pulled"}))
        log.finish(status="idle")
        return 0

    from lib.odds_api import OddsAPIError, OddsClient  # noqa: E402
    from lib.schema import ShapeError  # noqa: E402
    try:
        client = OddsClient(log=log)
        raw = client.scores(days_from=1)
    except (OddsAPIError, ShapeError) as e:
        log.error(str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    wanted = {p["event_id"] for p in watch}
    games = []
    for ev in raw:
        if ev.get("id") not in wanted:
            continue
        by_team = {s.get("name"): s.get("score")
                   for s in (ev.get("scores") or [])}
        hs, as_ = by_team.get(ev.get("home_team")), by_team.get(ev.get("away_team"))
        games.append({
            "event_id": ev.get("id"),
            "home_team": ev.get("home_team"),
            "away_team": ev.get("away_team"),
            "home_score": int(hs) if hs is not None else None,
            "away_score": int(as_) if as_ is not None else None,
            "completed": bool(ev.get("completed")),
            "last_update": ev.get("last_update"),
        })

    store.write_json(LIVE_FILE, {"fetched_at": store.now_iso(), "games": games})
    out = {"watching": len(watch), "scored": len(games),
           "credits_remaining": client.quota.remaining}
    print(json.dumps(out, indent=2))
    log.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
