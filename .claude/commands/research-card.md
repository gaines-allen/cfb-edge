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
do not commit, and do not push. Later steps validate your draft against
scripts/lib/card_rules.py, open every page you cited and confirm the quote
is really on it, and only then publish. Nobody reads it before it goes
live. That is exactly why you write one file and deterministic code
decides what reaches the ledger.

## Scope

The card runs once a week, on Wednesday, and covers everything from
Thursday night through the coming Sunday.

Rate only games kicking within 5 days of now, and skip any game already
carrying a published pick in `data/picks.json` from an earlier run this
week.

5 days is what a Wednesday needs to reach Sunday's late kickoffs. It used
to be 4, measured from a Friday, and moving the card 2 days earlier
without moving the window would have cut Sunday off the card entirely.

The window matters because a CFBD week is not always a weekend. Week 1 of
2026 runs 29 August to 8 September and holds two separate weekends, 44
games. Taking the whole week at once would publish plays that kick 8 days
out, against lines that will move several times before anyone can act on
them. The card is for the weekend in front of it. The rest of the week
gets its own card the following Wednesday.

Thursday games are the reason for the Wednesday slot. They used to be
rated the same afternoon they kicked, on an early run scoped to them
alone. Now they sit in the same card as the rest of the week with more
than a day of notice, and they are rated on the same bar as everything
else rather than a separate pass with its own rules.

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
the rating gap alone and caps at 7.5, below where a pick can stand, on
purpose. It is your anchor, not your pick.

Then do the work the model cannot. For each game worth chasing, find out why
the market disagrees with the ratings. Who is hurt, who is back, what the
coach said Monday, what happened last week, is there a look ahead spot, a
letdown spot, a travel problem, a weather problem, a quarterback change
nobody has priced. Follow `.claude/agents/handicapper.md` for how to weigh
the six inputs, and follow its hard rules exactly.

In September the ratings are mostly last season's carryover, so returning
production is the honest tiebreaker and it is not in the model. Check it.

## What to research on every team

Both teams, every game you rate, before you write a number. The point of
a 6 pick card is that all 6 got this, not that the top 2 did.

Against the spread. The record this season and last, and the split as a
favorite against as an underdog. A team that covers as a dog and folds as
a favorite is telling you something the ratings cannot.

Home and road. Points scored and allowed at home against on the road,
this season and last. Some teams are 10 points worse away from home and
the home field number in the model is a flat 2.2 for everyone.

The defense. Yards and points allowed per game, and the trend over the
last 4 games rather than the season average, because a defense that has
lost 2 starters is not the defense the season average describes.

The coaches. How each head coach has done in big games, as a favorite, as
an underdog, and against this opponent if there is history. Whether the
staff changed this year, and where the new coordinators came from.

Travel. Distance, time zones crossed, a short week, a body clock kickoff
for a west coast team playing at noon eastern, or the second road game in
a row. Altitude where it applies.

Injuries and depth. Who is out, who is questionable, who came back, and
at which positions. A backup quarterback is a different game. A backup
right guard usually is not.

Recent results. The last 3 games for each, with the score and the line,
so you can see whether a team is covering or just winning, and whether an
upset last week has a hangover in it.

Cite a source for the facts that carry the pick. The record against the
spread, the injury, the coaching history, whichever of these is the reason
you rated the game where you did. The validator opens every page and
checks the quote is there.


## What the validator will reject

Any pick on the card whose only factor is `rating_edge`. The ratings
model caps at 7.5 so it can never publish on its own, and restating its
number at 8.1 walks around that cap.

Any pick on the card with no sources. Every source needs a url, a date,
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
off the card and say in the rationale that the source could not be opened.

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

A JSON list of at least 6 games, each a different matchup, every one
researched to the standard above. The card is the 6 highest rated of
them, chosen by deterministic code after you finish, and all 6 publish.
So there is no such thing as a pick you can rate lightly because it
probably will not make it. Rate more than 6 if the slate has more than 6
worth the work, because a 7th fully researched game is what fills a slot
when 2 of your top 6 turn out to be the same matchup.

Include anything else you rated at 6.0 and up with whatever research it
got. Those are shadow picks, tracked but not staked, and they are how the
scale gets tested against results.

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

Units follow confidence: below 8.5 is 1 unit, 8.5 to 8.9 is 1.5, 9.0 and
above is 2. Never more than 2. A card of 6 at 1 unit each is 6 units, half
the weekly ceiling, so the ceiling only binds when several picks are
rated 8.5 and up.

Use only these factor tags: rating_edge, injury_edge, line_value, home_field,
situational, pace_mismatch, weather, momentum_mechanism, market_overreaction,
scheme_mismatch, coaching_change, travel, altitude, garbage_time_fade,
revenge_spot, lookahead_spot.

## When you are done

Print a short summary: how many you rated, which 6 you expect to make the
card, the total units, and one line on anything you decided against and
why. The card is always 6. What varies is how confident those 6 are, and
the number on each one is where that honesty lives, not in whether it was
published. A 6.4 on the card is a 6.4, staked at 1 unit, and the record
will say what a 6.4 was worth.

Prose in the rationale, no bullet lists, no em dashes, numbers over
adjectives.

$ARGUMENTS
