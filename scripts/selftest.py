#!/usr/bin/env python3
"""
Checks the grading math against hand-worked cases. A wrong grader is worse
than no grader, because it teaches the handicapper the wrong lesson.

    python3 scripts/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.scoring import (  # noqa: E402
    breakeven, calibration_table, clv_cents, clv_points, grade_pick,
    grade_spread, grade_total, half_points, payout, summarize,
)

FAILS: list[str] = []


def eq(label: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{label}: got {got!r}, wanted {want!r}")


def close(label: str, got, want, tol=1e-6) -> None:
    if got is None or abs(got - want) > tol:
        FAILS.append(f"{label}: got {got!r}, wanted ~{want!r}")


# ---- spreads -------------------------------------------------------------
eq("fav covers", grade_spread(31, 20, -7.5), "win")
eq("fav fails", grade_spread(24, 20, -7.5), "loss")
eq("fav exact push", grade_spread(27, 20, -7.0), "push")
eq("dog covers on loss", grade_spread(20, 24, 7.5), "win")
eq("dog covers outright", grade_spread(25, 24, 7.5), "win")
eq("dog buried", grade_spread(3, 45, 14.0), "loss")
eq("pick em tie", grade_spread(21, 21, 0.0), "push")

# ---- totals --------------------------------------------------------------
eq("over hits", grade_total(31, 28, 52.5, "Over"), "win")
eq("over misses", grade_total(10, 7, 52.5, "Over"), "loss")
eq("under hits", grade_total(10, 7, 52.5, "Under"), "win")
eq("total push", grade_total(28, 24, 52.0, "Under"), "push")
eq("over push", grade_total(28, 24, 52.0, "over"), "push")

# ---- halves --------------------------------------------------------------
eq("h1 from quarters", half_points([7, 10, 3, 14], 1), 17)
eq("h2 from quarters", half_points([7, 10, 3, 14], 2), 17)
eq("h2 includes OT", half_points([7, 10, 3, 14, 6], 2), 23)
eq("h1 ignores OT", half_points([7, 10, 3, 14, 6], 1), 17)
eq("h1 needs two quarters", half_points([7], 1), None)
eq("no line scores", half_points(None, 1), None)

GAME = {
    "home_team": "Ohio State", "away_team": "Michigan",
    "home_score": 34, "away_score": 27,
    "home_line_scores": [7, 14, 6, 7],   # 1H 21, 2H 13
    "away_line_scores": [3, 7, 10, 7],   # 1H 10, 2H 17
    "completed": True,
}

eq("full spread home",
   grade_pick({"market": "spread", "period": "full", "side": "Ohio State",
               "line": -6.5}, GAME), "win")
eq("full spread away",
   grade_pick({"market": "spread", "period": "full", "side": "Michigan",
               "line": 6.5}, GAME), "loss")
eq("h1 spread home lays 7",
   grade_pick({"market": "spread", "period": "h1", "side": "Ohio State",
               "line": -7.0}, GAME), "win")   # 21-10, margin 11
eq("h1 spread home lays 14",
   grade_pick({"market": "spread", "period": "h1", "side": "Ohio State",
               "line": -14.0}, GAME), "loss")
eq("h1 spread exact push",
   grade_pick({"market": "spread", "period": "h1", "side": "Ohio State",
               "line": -11.0}, GAME), "push")
eq("h2 spread away",
   grade_pick({"market": "spread", "period": "h2", "side": "Michigan",
               "line": 3.5}, GAME), "win")    # 17-13 away
eq("h1 total over",
   grade_pick({"market": "total", "period": "h1", "side": "Over",
               "line": 28.5}, GAME), "win")   # 31 first half
eq("h1 total under",
   grade_pick({"market": "total", "period": "h1", "side": "Under",
               "line": 28.5}, GAME), "loss")
eq("full total under",
   grade_pick({"market": "total", "period": "full", "side": "Under",
               "line": 62.5}, GAME), "win")   # 61
eq("moneyline home", grade_pick({"market": "moneyline", "period": "full",
                                 "side": "Ohio State", "line": None}, GAME), "win")
eq("unknown team voids", grade_pick({"market": "spread", "period": "full",
                                     "side": "Purdue", "line": -3.0}, GAME), "void")
eq("incomplete game pends",
   grade_pick({"market": "spread", "period": "full", "side": "Ohio State",
               "line": -6.5}, {**GAME, "completed": False}), "pending")
eq("completed but no quarters pends",
   grade_pick({"market": "spread", "period": "h1", "side": "Ohio State", "line": -7.0},
              {**GAME, "home_line_scores": None, "away_line_scores": None}), "pending")

# ---- payouts -------------------------------------------------------------
close("win at -110", payout(1.0, -110, "win"), 100 / 110)
close("win at +150", payout(1.0, 150, "win"), 1.5)
close("2u win at -110", payout(2.0, -110, "win"), 200 / 110)
close("loss", payout(1.5, -110, "loss"), -1.5)
close("push", payout(1.5, -110, "push"), 0.0)
close("breakeven -110", breakeven(-110), 110 / 210)
close("breakeven +100", breakeven(100), 0.5)

# ---- CLV -----------------------------------------------------------------
close("spread CLV good", clv_points("spread", "Alabama", -7.5, -9.5), 2.0)
close("spread CLV bad", clv_points("spread", "Alabama", -7.5, -6.5), -1.0)
close("dog CLV good", clv_points("spread", "Vandy", 7.5, 6.5), 1.0)
close("over CLV good", clv_points("total", "Over", 52.5, 55.5), 3.0)
close("over CLV bad", clv_points("total", "Over", 52.5, 50.5), -2.0)
close("under CLV good", clv_points("total", "Under", 52.5, 49.5), 3.0)
eq("CLV needs both numbers", clv_points("spread", "Alabama", None, -9.5), None)
eq("price CLV", clv_cents(-105, -115), 10)

# ---- rollups -------------------------------------------------------------
SAMPLE = [
    {"result": "win", "units": 1.0, "units_net": 100 / 110, "confidence": 8.2,
     "clv_points": 1.0, "market": "spread"},
    {"result": "loss", "units": 2.0, "units_net": -2.0, "confidence": 9.1,
     "clv_points": -0.5, "market": "spread"},
    {"result": "push", "units": 1.0, "units_net": 0.0, "confidence": 8.6,
     "clv_points": 0.0, "market": "total"},
    {"result": "pending", "units": 1.0, "units_net": 0.0, "confidence": 8.0},
]
s = summarize(SAMPLE)
eq("summary counts pending out", s["picks"], 3)
eq("wins", s["wins"], 1)
eq("losses", s["losses"], 1)
eq("pushes", s["pushes"], 1)
eq("push excluded from risked", s["risked"], 3.0)
close("net units", s["units"], round(100 / 110 - 2.0, 2), 0.02)
eq("win pct off decided only", s["win_pct"], 50.0)
close("avg clv", s["avg_clv"], 0.17, 0.01)
eq("beat close share", s["beat_close_pct"], 33.3)

cal = calibration_table(SAMPLE)
eq("calibration buckets", sorted(r["bucket"] for r in cal),
   ["8.0-8.4", "8.5-8.9", "9.0-9.4"])

# ---- team name matching -------------------------------------------------
from lib.teams import (  # noqa: E402
    MUST_STAY_DISTINCT, VARIANTS, canonical, normalize, suggest,
)

# Names that must never collapse into each other. This is the check that
# stops Ohio from being graded as Ohio State.
for a, b in MUST_STAY_DISTINCT:
    if normalize(a) == normalize(b):
        FAILS.append(f"name collision: {a} and {b} both normalize to {normalize(a)}")

# Every declared variant resolves to its canonical spelling.
for canon_name, alts in VARIANTS.items():
    for alt in alts:
        eq(f"variant {alt}", canonical(alt, {canon_name}), canon_name)

CFBD_SAMPLE = {
    "Mississippi", "Mississippi State", "Ohio", "Ohio State", "Miami",
    "Miami (OH)", "Washington", "Washington State", "San José State",
    "San Diego State", "Hawai'i", "UT San Antonio", "Appalachian State",
    "NC State", "North Carolina", "Louisiana", "Louisiana Monroe",
    "Louisiana Tech", "LSU", "Texas A&M", "Sam Houston", "Connecticut",
    "Massachusetts", "UCF", "South Florida", "Pittsburgh", "Army",
    "Southern Mississippi", "Michigan", "Michigan State",
}

for src, want in [
    ("Ole Miss", "Mississippi"), ("Mississippi State", "Mississippi State"),
    ("Ohio", "Ohio"), ("Ohio State", "Ohio State"),
    ("Miami", "Miami"), ("Miami (OH)", "Miami (OH)"), ("Miami Ohio", "Miami (OH)"),
    ("San Jose State", "San José State"), ("Hawaii", "Hawai'i"),
    ("UTSA", "UT San Antonio"), ("App State", "Appalachian State"),
    ("North Carolina State", "NC State"), ("UL Monroe", "Louisiana Monroe"),
    ("Louisiana-Lafayette", "Louisiana"), ("Louisiana State", "LSU"),
    ("Southern Miss", "Southern Mississippi"), ("Central Florida", "UCF"),
    ("USF", "South Florida"), ("Pitt", "Pittsburgh"), ("UConn", "Connecticut"),
    ("UMass", "Massachusetts"), ("Sam Houston State", "Sam Houston"),
    ("Army West Point", "Army"), ("Washington", "Washington"),
    ("Washington State", "Washington State"), ("Michigan", "Michigan"),
    ("Texas A and M", "Texas A&M"),
]:
    eq(f"canonical({src})", canonical(src, CFBD_SAMPLE), want)

# A name we cannot place must come back None. Silence is the bug we are
# guarding against, so returning a plausible wrong answer is a failure.
for bad in ["Nowhere Tech", "Fake State", "Springfield A&M", ""]:
    eq(f"unknown {bad!r} refuses to match", canonical(bad, CFBD_SAMPLE), None)

eq("suggest returns candidates", bool(suggest("Mississippi Stat", CFBD_SAMPLE)), True)

if FAILS:
    print(f"FAILED {len(FAILS)} check(s):")
    for f in FAILS:
        print("  " + f)
    raise SystemExit(1)

print("All grading and name-matching checks passed.")
