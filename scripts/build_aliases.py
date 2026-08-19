#!/usr/bin/env python3
"""
Regenerates data/team_aliases.json, the mascot map.

The Odds API returns "Alabama Crimson Tide" where CFBD returns "Alabama".
ESPN publishes location and mascot as separate fields, so it can tell us
which trailing words are the mascot. That is the whole trick.

Run this when a school rebrands, a team joins FBS, or a board name fails to
map. It costs nothing: ESPN's team endpoint needs no key.

    python3 scripts/build_aliases.py

Note the endpoint is hard capped at 500 results and truncates alphabetically,
so it has to be paged. Asking for limit=900 silently returns the top 25.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.teams import ALIAS_FILE, normalize  # noqa: E402

BASE = ("https://site.api.espn.com/apis/site/v2/sports/football/"
        "college-football/teams")


def fetch_teams() -> list[dict]:
    out: list[dict] = []
    for page in range(1, 15):
        url = f"{BASE}?limit=100&page={page}"
        with urllib.request.urlopen(url, timeout=30) as r:
            payload = json.load(r)
        teams = payload["sports"][0]["leagues"][0].get("teams", [])
        if not teams:
            break
        out.extend(t["team"] for t in teams)
    return out


def main() -> int:
    teams = fetch_teams()
    if not teams:
        print("ERROR: ESPN returned no teams", file=sys.stderr)
        return 2

    raw: dict[str, str] = {}
    for t in teams:
        loc = t.get("location")
        if not loc:
            continue
        mascot = t.get("name")
        short = t.get("shortDisplayName")
        for form in (t.get("displayName"),
                     f"{loc} {mascot}" if mascot else None,
                     f"{short} {mascot}" if short and mascot else None,
                     loc):
            if form:
                raw[form] = loc

    # Collapse onto normalized keys, and drop any key that two different
    # schools claim. An ambiguous key is worse than a missing one.
    buckets: dict[str, set[str]] = {}
    for form, loc in raw.items():
        buckets.setdefault(normalize(form), set()).add(loc)

    clean = {k: next(iter(v)) for k, v in buckets.items() if len(v) == 1}
    dropped = {k: sorted(v) for k, v in buckets.items() if len(v) > 1}

    ALIAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALIAS_FILE.write_text(json.dumps(clean, indent=0, sort_keys=True))

    print(json.dumps({
        "teams_seen": len(teams),
        "alias_keys": len(clean),
        "ambiguous_dropped": dropped,
        "wrote": str(ALIAS_FILE.relative_to(ALIAS_FILE.parents[1])),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
