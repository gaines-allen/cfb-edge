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

## Scope

The invocation may carry `scope thursday` or `scope full`.

`scope thursday` is the early run. Rate only games kicking before Friday
6 PM Eastern, and leave everything else alone for Friday's run. If nothing
in that window clears 8.0, write an empty list and say so. That is the
normal Thursday outcome: an early game only publishes early when the
research genuinely likes it, and a game you passed on still shows on the
site's board with the model's numbers, so anyone who wants it can bet it
themselves.

`scope full`, or no scope, is the coming weekend. Rate only games kicking
within 4 days of now, and skip any game already carrying a published pick
in `data/picks.json` from an earlier run this week.

The 4 day window matters because a CFBD week is not always a weekend.
Week 1 of 2026 runs 29 August to 8 September and holds two separate
weekends, 44 games. Taking the whole week on the Friday before the first
one would publish a card on 28 August whose plays mostly kick on 5
September, 8 days out, against lines that will move several times before
anyone can act on them. The card is for the weekend in front of it. The
rest of the week gets its own card the following Friday.

Every other week of the season fits inside the window anyway, so this
only changes week 1 and any other stretch the calendar folds together.

## The job

Read `data/memory.json` first, every time, before you look at a single game.
It holds the calibration table, the record by market and period, the factor
scorecard and the lessons the grader left. If it is empty because nothing has
graded yet, say so in your summary and weigh every factor on argument alone.

Every row in the calibration table, the market and period breakdowns, and
the factor scorecard now carries a verdict alongside its record. Read the
verdict, not the percentage.

A row marked `no_signal` is not weak evidence, it is no evidence. Its 95
percent interval still contains the 52.4 percent needed to break even, so
the record is consistent with having no edge in either direction. Do not
change a rating because of it, do not avoid a market because of it, and do
not cite it as a reason. A factor sitting at 1 and 4 reads like a warning
and is a coin landing tails 4 times.

Only `beats_breakeven` and `below_breakeven` are evidence, and both need
several hundred decided picks before they appear. Expect the whole file to
read `no_signal` for a long time. That is the file being honest rather than
the system failing.

`no_data` means nothing has graded in that cell yet.


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

Anything at 8.0 or above with no sources. Every source needs a url, a date,
and a quote, and a date older than 14 days counts as reusing last week's
reasoning.

The quote is the part that matters most. Copy the exact sentence from the
page that supports your claim, verbatim, at least 25 characters. After you
finish, scripts/verify_sources.py opens every url and checks that sentence
is really on that page. A paraphrase will not match. A remembered quote will
not match. A page you did not actually open will not match. Any live pick
whose sources cannot be confirmed stops the run.

So do not cite a page you have not read, and do not reconstruct a quote from
memory. If you cannot fetch a page, either drop the claim or keep the pick
below 8.0 and say in the rationale that the source could not be opened.

What the check cannot do is tell whether the page is accurate, whether the
quote is in context, or whether the outlet is any good. A person reads the
card for that, so make the claim easy to check rather than easy to believe.

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
           "claim": "what this source establishes",
           "quote": "the exact sentence from that page, copied verbatim"}
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
