#!/usr/bin/env python3
"""
The gate between the handicapper and the ledger.

Runs in the weekly workflow after the agent writes its draft and before
anything is logged. Exits non-zero when the card breaks a rule, which stops
the workflow and leaves data/picks.json untouched.

    python3 scripts/validate_card.py --file picks_draft.json
    python3 scripts/validate_card.py --file picks_draft.json --json

The rules live in scripts/lib/card_rules.py so this and log_picks.py check
the same things. Nothing here judges whether a pick is good. It judges
whether the process that produced it was followed, which is the only thing
a machine can check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.card_rules import (  # noqa: E402
    LIVE_THRESHOLD, check_card, correlation_warnings, select_card, units_for,
)
from lib.runlog import RunLog  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--json", action="store_true",
                    help="machine readable result for the workflow summary")
    ap.add_argument("--dry-run", action="store_true",
                    help="accepted for symmetry, this script never writes")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=None,
                    help="defaults to the slate's week, as log_picks does")
    args = ap.parse_args()

    log = RunLog("validate_card", dry_run=args.dry_run)

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found. The handicapper writes it.",
              file=sys.stderr)
        log.error("draft missing", path=str(path))
        return 2

    try:
        picks = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: {path} is not valid JSON: {e}", file=sys.stderr)
        log.error(f"draft unparseable: {e}")
        return 2

    if not isinstance(picks, list):
        print("ERROR: the draft must be a JSON list of picks", file=sys.stderr)
        return 2

    # The same selection log_picks will make, made here first, so the
    # rules are checked against the card that will actually be logged and
    # not against every rated row as if it were live.
    week = args.week
    if week is None:
        slate_path = store.DATA / "slate.json"
        if slate_path.exists():
            week = json.loads(slate_path.read_text()).get("week")
    this_week = [p for p in store.load_picks()
                 if p.get("season") == args.season and p.get("week") == week]
    select_card(picks, this_week)

    errors = check_card(picks)
    warnings = correlation_warnings(picks)

    live = [p for p in picks if p.get("live")]
    shadow = [p for p in picks if p not in live]
    staked = sum(units_for(p.get("confidence", 8.0)) for p in live)

    result = {
        "picks": len(picks),
        "live": len(live),
        "shadow": len(shadow),
        "units_at_risk": round(staked, 1),
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }

    log.rows("card", len(picks), live=len(live), units=staked,
             errors=len(errors), warnings=len(warnings))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{len(picks)} rated, {len(live)} live, "
              f"{round(staked, 1)} units at risk.")
        for w in warnings:
            print(f"WARNING: {w}")
        for e in errors:
            print(f"REJECTED: {e}", file=sys.stderr)
        if not errors:
            print("Card passes every rule.")

    log.finish(status="ok" if not errors else "rejected")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
