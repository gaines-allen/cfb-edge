#!/usr/bin/env python3
"""
Is the live site actually current, and did the last run finish.

On 26 August the 2 PM job failed a test gate, published nothing, and the
page sat 28 hours stale for 3 hours before anyone noticed. Nothing was
broken about the detection: the gate did its job and the page said it was
stale. The failure was that both facts sat there with nobody looking.

This looks. It is deliberately outcome first: the question it asks is
whether the page a reader loads right now is current, not whether some
workflow reported success. A run can go green and still publish nothing,
Pages can fail to deploy, a cron can silently stop firing, a secret can
expire. Every one of those ends the same way, with an old page, and one
check catches all of them including the ones nobody predicted.

The run conclusion is checked too, because it says the same thing hours
earlier and names which step broke.

Exit code 0 healthy, 1 unhealthy, so a workflow can gate on it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SITE = "https://gaines-allen.github.io/cfb-edge/"

# The same number the page uses to decide whether to show the board at
# all, imported rather than retyped.
#
# The first draft of this file set its own limit at 30 against the page's
# 26, which opens a 4 hour window where every reader is looking at a dead
# board and the watchdog reports healthy. A watchdog may be stricter than
# the thing it watches. It may never be looser. And two copies of one
# threshold drift the first time either is tuned, which is the same
# mistake that took the daily publish down twice on 26 August.
from build_site import MAX_BOARD_AGE_HOURS as MAX_PAGE_AGE_HOURS  # noqa: E402

# How old the page has to get before the watchdog stops waiting for the
# scheduler and pulls the data itself.
#
# GitHub cron is best effort. On this repo scheduled runs normally fire 14
# to 89 minutes late, and on 27 August the 2 PM run did not fire at all,
# which is documented behaviour under load rather than a fault. A site
# that promises a daily refresh cannot rest on that alone.
#
# Set below the staleness limit so recovery starts before readers lose the
# board, and high enough that a normal day never reaches it, so this costs
# an extra pull only on a day the scheduler already skipped.
RECOVER_AFTER_HOURS = MAX_PAGE_AGE_HOURS - 6


def board_fetched_at(html: str) -> str | None:
    """The timestamp the page itself carries for its lines."""
    m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return (data.get("board") or {}).get("fetched_at")


def age_hours(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() / 3600.0


def verdict(html: str | None, now: datetime,
            last_run: str | None = None,
            max_age: float = MAX_PAGE_AGE_HOURS) -> dict:
    """
    Healthy or not, and why, in words a person can act on.

    Unreachable counts as unhealthy. A page nobody can load is not a page
    that is merely untested, and treating a fetch failure as "no news" is
    how a check ends up reporting green through an outage.
    """
    problems: list[str] = []

    if html is None:
        problems.append("The site did not respond, so nobody can read it.")
        age = None
    else:
        stamp = board_fetched_at(html)
        age = age_hours(stamp, now)
        if stamp is None:
            problems.append(
                "The page loaded but carries no board timestamp, so its age "
                "cannot be established.")
        elif age is None:
            problems.append(f"The board timestamp {stamp} is unreadable.")
        elif age > max_age:
            problems.append(
                f"The lines on the live page are {age:.1f} hours old, past "
                f"the {max_age:.0f} hour limit. The board and the leans are "
                f"down for every reader until a pull lands.")

    if last_run and last_run.lower() not in ("success", "skipped", ""):
        problems.append(
            f"The last Daily update run ended in {last_run}, so nothing was "
            f"committed or deployed.")

    # Worth pulling the data rather than only saying so. Only when the
    # page is genuinely ageing: an unreachable site or a missing timestamp
    # is a different fault and firing a data pull at it would spend
    # credits on a problem it cannot fix.
    recover = age is not None and age > RECOVER_AFTER_HOURS

    return {
        "healthy": not problems,
        "checked_at": now.isoformat(timespec="seconds"),
        "page_age_hours": None if age is None else round(age, 1),
        "last_run": last_run,
        "problems": problems,
        "should_recover": recover,
        "recover_after_hours": RECOVER_AFTER_HOURS,
    }


def fetch(url: str, timeout: int = 20) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=SITE)
    ap.add_argument("--last-run", default=None,
                    help="conclusion of the most recent Daily update run")
    ap.add_argument("--max-age", type=float, default=MAX_PAGE_AGE_HOURS)
    ap.add_argument("--page", default=None,
                    help="read a local file instead of fetching, for tests")
    args = ap.parse_args()

    html = (open(args.page).read() if args.page
            else fetch(f"{args.url}?cb={int(datetime.now().timestamp())}"))
    out = verdict(html, datetime.now(timezone.utc), args.last_run,
                  args.max_age)
    print(json.dumps(out, indent=2))
    return 0 if out["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
