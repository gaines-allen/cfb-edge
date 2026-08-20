---
description: Research the slate and write the draft card. Writes no ledger and pushes nothing.
---

Build this week's draft card. You are running unattended in CI, so read this
whole file before you start and stop at the boundary it sets.

## What you may touch

Read `data/memory.json`, `data/slate.json`, `data/board.json` and
`data/model_calibration.json`. Search the web freely.

Write exactly one file, `picks_draft.json`, in the repository root. Write
nothing else. Do not run `log_picks.py`, do not edit anything under `data/`,
do not commit, and do not push. A later step in the workflow validates your
draft and opens a pull request. A person merges it. That separation is the
reason you are allowed to run on your own.

## The job

Read `data/memory.json` first, every time, before you look at a single game.
It holds the calibration table, the record by market and period, the factor
scorecard and the lessons the grader left. If it is empty because nothing has
graded yet, say so in your summary and weigh every factor on argument alone.

Read `data/slate.json`. It carries a blended SP+, FPI, SRS and Elo projection
for every game on the board, the FanDuel number beside it, the points of
disagreement, the z score, and a floor confidence. That floor is built from
the rating gap alone and caps at 7.5, below the 8.0 publish threshold, on
purpose. It is your anchor, not your pick.

Then do the work the model cannot. For each game worth chasing, find out why
the market disagrees with the ratings. Who is hurt, who is back, what the
coach said Monday, what happened last week, is there a look ahead spot, a
letdown spot, a travel problem, a weather problem, a quarterback change
nobody has priced. Follow `.claude/agents/handicapper.md` for how to weigh
the six inputs, and follow its hard rules exactly.

In September the ratings are mostly last season's carryover, so returning
production is the honest tiebreaker and it is not in the model. Check it.

## What the validator will reject

Anything at 8.0 or above whose only factor is `rating_edge`. The ratings
model caps at 7.5 so it can never publish on its own, and restating its
number at 8.1 walks around that cap.

Anything at 8.0 or above with no sources. Every source needs a url and a
date, and a date older than 14 days counts as reusing last week's reasoning.

Anything tagged `injury_edge` without a source. If you cannot confirm a
player's status, say so in the rationale and keep the confidence below 8.5
rather than assuming.

Anything at 9.0 or above with fewer than 2 researched factors or fewer than
2 sources, because 9.0 stakes the maximum 2 units.

More than 6 live picks, more than 12 units across the week, units that do
not match the staking ladder, or the same game and market taken twice.

If several of your picks rest on the same single reason, the validator will
say so. They are not independent bets and the staking math assumes they are.
Say in your summary whether you meant it.

## The draft format

A JSON list. Include everything you rated 6.0 and up, not only what cleared
8.0, because the shadow picks are how the threshold gets tested.

    [
      {
        "event_id": "matches an id in data/board.json",
        "matchup": "Away Team @ Home Team",
        "kickoff": "ISO 8601",
        "market": "spread | total | moneyline",
        "period": "full | h1 | h2",
        "side": "team name, or Over or Under",
        "line": -6.5,
        "price": -110,
        "confidence": 8.4,
        "units": 1.0,
        "model_number": -9.4,
        "rationale": "Three to five sentences of plain prose. Lead with the specific fact that makes the market wrong. Close with one sentence on what would make you wrong.",
        "factors": ["rating_edge", "injury_edge"],
        "sources": [
          {"url": "https://...", "publisher": "name", "date": "2026-09-02",
           "claim": "what this source establishes"}
        ]
      }
    ]

Units follow confidence: 8.0 to 8.4 is 1 unit, 8.5 to 8.9 is 1.5, 9.0 and
above is 2. Never more than 2.

Use only these factor tags: rating_edge, injury_edge, line_value, home_field,
situational, pace_mismatch, weather, momentum_mechanism, market_overreaction,
scheme_mismatch, coaching_change, travel, altitude, garbage_time_fade,
revenge_spot, lookahead_spot.

## When you are done

Print a short summary: how many you rated, how many cleared 8.0, the total
units, and one line on anything you decided against and why. If nothing
cleared 8.0, write an empty list and say that. A week with no card is a
normal outcome and padding it is the fastest way to turn a good model into a
losing one.

Prose in the rationale, no bullet lists, no em dashes, numbers over
adjectives.

$ARGUMENTS
