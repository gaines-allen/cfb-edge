#!/usr/bin/env python3
"""
Commits the handicapper's card to the ledger. Validates hard, because a
malformed pick is a pick that never grades.

    python3 scripts/log_picks.py --file picks_draft.json
    python3 scripts/log_picks.py --file picks_draft.json --dry-run

Draft format is a JSON list. Every entry needs:
  event_id, matchup, market, period, side, line, price, confidence, rationale
Optional: units (derived from confidence if absent), factors, model_number,
kickoff (looked up from the board if absent).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402

VALID_MARKETS = {"spread", "total", "moneyline"}
VALID_PERIODS = {"full", "h1", "h2"}
VALID_FACTORS = {
    "rating_edge", "injury_edge", "line_value", "home_field", "situational",
    "pace_mismatch", "weather", "momentum_mechanism", "market_overreaction",
    "scheme_mismatch", "coaching_change", "travel", "altitude",
    "garbage_time_fade", "revenge_spot", "lookahead_spot",
}


def units_for(conf: float) -> float:
    if conf >= 9.0:
        return 2.0
    if conf >= 8.5:
        return 1.5
    if conf >= store.LIVE_THRESHOLD:
        return 1.0
    # Shadow picks are never staked. They carry a notional 1 unit so the
    # calibration table can answer whether the 8.0 threshold is set in the
    # right place. Every headline figure filters on live, so notional units
    # never touch the real record.
    return 1.0


def validate(d: dict, board_index: dict, i: int) -> list[str]:
    errs = []
    for f in ("event_id", "matchup", "market", "period", "side",
              "price", "confidence", "rationale"):
        if d.get(f) in (None, ""):
            errs.append(f"[{i}] missing {f}")

    if d.get("market") not in VALID_MARKETS:
        errs.append(f"[{i}] market must be one of {sorted(VALID_MARKETS)}")
    if d.get("period") not in VALID_PERIODS:
        errs.append(f"[{i}] period must be one of {sorted(VALID_PERIODS)}")

    if d.get("market") in ("spread", "total") and d.get("line") is None:
        errs.append(f"[{i}] {d.get('market')} needs a line")

    if d.get("market") == "total" and str(d.get("side", "")).lower() not in (
            "over", "under"):
        errs.append(f"[{i}] total side must be Over or Under")

    try:
        c = float(d.get("confidence", -1))
        if not 1.0 <= c <= 10.0:
            errs.append(f"[{i}] confidence must be 1.0 to 10.0")
    except (TypeError, ValueError):
        errs.append(f"[{i}] confidence is not a number")

    if d.get("event_id") and d["event_id"] not in board_index:
        errs.append(f"[{i}] event_id {d['event_id']} is not on the current board")

    bad = set(d.get("factors") or []) - VALID_FACTORS
    if bad:
        errs.append(f"[{i}] unknown factor tags: {sorted(bad)}. "
                    f"Use the tags in the handicapper spec so the grader can track them.")

    r = str(d.get("rationale", ""))
    if len(r) < 80:
        errs.append(f"[{i}] rationale is too thin ({len(r)} chars). "
                    "Say why the market is wrong and what would make you wrong.")
    if "—" in r:
        errs.append(f"[{i}] rationale contains an em dash")

    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, required=False)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-overwrite", action="store_true")
    args = ap.parse_args()

    draft_path = Path(args.file)
    if not draft_path.exists():
        print(f"ERROR: {draft_path} not found", file=sys.stderr)
        return 2

    draft = json.loads(draft_path.read_text())
    if not isinstance(draft, list):
        print("ERROR: draft must be a JSON list", file=sys.stderr)
        return 2

    board = store.load_board()
    board_index = {g["event_id"]: g for g in board.get("games", [])}

    errors: list[str] = []
    for i, d in enumerate(draft):
        errors.extend(validate(d, board_index, i))
    if errors:
        print("Validation failed:\n" + "\n".join(errors), file=sys.stderr)
        return 3

    week = args.week
    if week is None:
        slate_path = store.DATA / "slate.json"
        if slate_path.exists():
            week = json.loads(slate_path.read_text()).get("week")
    if week is None:
        print("ERROR: pass --week, or build the slate first", file=sys.stderr)
        return 2

    existing = store.load_picks()
    already = {(p["event_id"], p["market"], p["period"], p["side"])
               for p in existing
               if p.get("season") == args.season and p.get("week") == week}

    new_rows, skipped = [], []
    for d in draft:
        key = (d["event_id"], d["market"], d["period"], d["side"])
        if key in already and not args.allow_overwrite:
            skipped.append(f"{d['matchup']} {d['side']} {d['market']}/{d['period']}")
            continue

        conf = float(d["confidence"])
        ev = board_index.get(d["event_id"], {})
        new_rows.append(store.new_pick(
            season=args.season,
            week=int(week),
            event_id=d["event_id"],
            matchup=d["matchup"],
            kickoff=d.get("kickoff") or ev.get("commence_time", ""),
            market=d["market"],
            period=d["period"],
            side=d["side"],
            line=(float(d["line"]) if d.get("line") is not None else None),
            price=int(d["price"]),
            confidence=conf,
            units=float(d.get("units", units_for(conf))),
            rationale=d["rationale"].strip(),
            factors={t: True for t in (d.get("factors") or [])},
            model_number=(float(d["model_number"])
                          if d.get("model_number") is not None else None),
        ))

    live = [p for p in new_rows if p["live"]]
    shadow = [p for p in new_rows if not p["live"]]

    summary = {
        "season": args.season,
        "week": week,
        "live_picks": len(live),
        "shadow_picks": len(shadow),
        "skipped_duplicates": skipped,
        "units_at_risk": round(sum(p["units"] for p in live), 2),
        "card": [
            f"{p['side']} {p['line'] if p['line'] is not None else ''} "
            f"{p['market']}/{p['period']} @ {p['price']} "
            f"({p['confidence']}, {p['units']}u)"
            for p in live
        ],
    }

    if len(live) > store.TARGET_PICKS:
        summary["note"] = (
            f"{len(live)} plays cleared {store.LIVE_THRESHOLD}, target is "
            f"{store.TARGET_PICKS}. Keeping all of them, but check whether the "
            "bar is being applied honestly."
        )
    elif len(live) < store.TARGET_PICKS:
        summary["note"] = (
            f"Only {len(live)} plays cleared {store.LIVE_THRESHOLD}. That is the "
            "correct outcome when the slate is priced well. Do not pad the card."
        )

    if args.dry_run:
        print(json.dumps({"dry_run": True, **summary}, indent=2))
        return 0

    store.save_picks(existing + new_rows)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
