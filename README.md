# CFB Edge

Six college football plays a week, published only above 8.0 out of 10, with
every number, every reason, and every result tracked in public so the model
can find out whether it is any good.

The dashboard is at `site/index.html`, served from GitHub Pages.

## How it works

Four agents split the job so none of them can mark their own homework.

`odds-scout` pulls FanDuel's posted numbers through The Odds API and records
every move. `handicapper` reads the board and the grader's memory file, works
the slate, and writes the card. `grader` settles the tickets, computes
closing line value, and rebuilds the calibration table. `site-builder` renders
the ledger. Their instructions live in `.claude/agents/`.

The picks need an agent doing research, so those happen Wednesday from
Claude. Everything deterministic happens on its own: a GitHub Action pulls
the board daily, twice more on Saturday to capture closing lines, settles
finished games Sunday, rebuilds the page, and deploys.

## Markets

Full game spread and total, first half spread and total, second half spread
and total, moneyline. No parlays, no props, no live betting.

First half markets are on the card because they dodge the two things that
wreck full game bets, garbage time and late clock management.

## The confidence scale

Every candidate gets rated 1 to 10. Only above 8.0 is published. Everything
rated 6.0 and up is logged as a shadow pick so the threshold gets tested
rather than assumed, and the dashboard shows both.

Staking runs off confidence: 8.0 to 8.4 is 1 unit, 8.5 to 8.9 is 1.5, 9.0 and
above is 2. Twelve units is the ceiling for a week.

## Setup

Get an Odds API key at https://the-odds-api.com and a CollegeFootballData key
at https://collegefootballdata.com/key. Both have free tiers that cover this.

    pip install -r requirements.txt
    export ODDS_API_KEY=...
    export CFBD_API_KEY=...

Add both as repository secrets under Settings, Secrets and variables,
Actions, so the daily workflow can run. Then turn on Pages under Settings,
Pages, with the source set to GitHub Actions.

## Running it by hand

    python3 scripts/fetch_odds.py                      # pull the board
    python3 scripts/make_slate.py --season 2026 --week 1   # build candidates
    python3 scripts/log_picks.py --file picks_draft.json --dry-run
    python3 scripts/grade_results.py --season 2026      # settle
    python3 scripts/build_site.py                       # rebuild the page

## Credits

A board pull costs 3 Odds API credits: spreads, totals and moneyline across
one region. The scheduled runs come to about 90 a month against a free tier
of 500.

Half markets are not available on the bulk endpoint, which returns 422 if you
ask. They come from the single-event endpoint at 2 credits per game, so
`fetch_odds.py --halves` takes explicit event ids and is meant for the handful
of games actually under consideration. Books post college half lines close to
kickoff, so before game week the call usually comes back empty and free.

CFBD responses cache to disk for 12 hours, so repeated runs on the same day
cost nothing.

## What this is not

Opinions on football games. Not investment advice, not a guarantee, and not a
system that is known to work, because it has not run long enough for anyone
to know that. The record on the dashboard is the whole argument, including
the losing weeks.

If gambling stops being fun, call 1-800-GAMBLER.
