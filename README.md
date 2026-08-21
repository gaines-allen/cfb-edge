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

Every script that writes to `data/` or `site/` takes `--dry-run`, which
reports what would change and writes nothing. Use it before any run you are
not certain about, and before anything that touches the ledger.

Each run writes one structured record per external call to stderr and to a
gitignored JSONL under `data/.cache/runs/`, carrying the run id, the
endpoint, the credit cost and the row count. stdout is unchanged, because
the workflow and the agents parse it. Set `RUN_ID` to join a local run to a
GitHub Actions run.

## Tests

    python3 scripts/selftest.py            # what the daily job runs
    python3 scripts/selftest.py -k teams -v

`selftest.py` is a wrapper around pytest so the workflow keeps calling one
command. The checks live in `tests/` and run offline against captured
payloads, so no key is needed and a pull request from a fork runs exactly
what a branch runs. `.github/workflows/tests.yml` runs them on every pull
request under Python 3.9 and 3.11.

Shape validation sits on every external response in `scripts/lib/schema.py`.
A missing or renamed field raises rather than resolving to None and flowing
onward as a confident number, which is how every bug this system has shipped
got out. Presence checks are the easy half. The half that matters is
semantic: a column of SP+ offense ratings whose median sits near 0 is a
differential whatever it is called, and a CFBD team name the ESPN map can
shorten is carrying a mascot whatever the docs say.

## The weekly card

The daily job does the deterministic half and stops, because making a pick
needs an agent that can read an injury report. `.github/workflows/weekly-card.yml`
runs that agent on Wednesday at 08:30 Chicago, and by hand from the Actions
tab.

The boundary is the point. The agent reads the slate, searches the web, and
writes one file, `picks_draft.json`, which is gitignored scratch. It cannot
run git, cannot run `log_picks.py`, cannot edit anything, and has `Write`
scoped to that single filename. Deterministic code then checks the card, and
only a card that passes reaches `data/picks.json`. The result lands in a pull
request, so a person reads the rationale and the sources and merges. Nothing
publishes itself.

`scripts/lib/card_rules.py` holds the rules, and `scripts/validate_card.py`
runs them as a gate. `log_picks.py` runs the same ones, so a hand logged card
cannot walk around a check the automated path has to clear. Anything at 8.0
or above needs a factor beyond `rating_edge` and at least one source with a
url and a date under 14 days old. An injury claim needs a source, and an
unconfirmed status caps the confidence below 8.5. Anything at 9.0 stakes the
maximum 2 units and needs 2 factors and 2 sources. Six live picks and 12
units are the weekly ceilings, and the same game and market cannot be taken
twice. Several picks resting on one reason get flagged as correlated, which
is a warning rather than a rejection.

Authentication is a subscription token, not a metered API key. Run
`claude setup-token` locally, which mints a 1 year OAuth token on a Pro,
Max, Team or Enterprise plan, and add the value as the repository secret
`CLAUDE_CODE_OAUTH_TOKEN`. The run then bills against the Claude plan
rather than API usage. The run reports its own cost estimate in the
workflow summary, and `--max-turns` caps how long the agent can loop.

The workflow does not pass `--bare` for 2 reasons. Bare mode skips
`CLAUDE.md` and the agent definitions in `.claude/`, which are the
instructions the handicapper needs, and bare mode does not read
`CLAUDE_CODE_OAUTH_TOKEN` at all.

Every cited source is opened and read before anything is logged.
`scripts/verify_sources.py` fetches each url and checks that the exact
sentence the pick relies on is really on that page. A url and a date only
prove a page exists, so without this a paraphrase, a misremembered quote, or
a page saying the opposite all pass identically to a source someone actually
read. A live pick needs at least 1 source confirmed this way. Anything that
could not be confirmed, including a paywall or a dead link, is named in the
pull request rather than passing quietly.

What that check cannot do is tell whether the page is accurate, whether the
quote sits in context, or whether the outlet is worth anything. It moves the
question from "did the agent read something" to "is what it read any good",
and the second one is still yours.

One honest limit. Phase 2 tests the ratings model and the staking math, and
it cannot test the research layer, which is where every pick above 8.0 comes
from. `factor_scorecard` in `data/memory.json` is what will eventually
measure it, and it is empty until picks grade. Until it fills, run this with
`research_only` set to true and read the drafts, or read every pull request
before merging it. The rules above check that the process was followed. They
cannot check that the reasoning was any good.

## Fixtures

`tests/fixtures/` holds raw upstream payloads, captured and refreshed by

    python3 scripts/refresh_fixtures.py --dry-run     # price the run first
    python3 scripts/refresh_fixtures.py --sources cfbd --seasons 2024

They are raw rather than parsed, because the parser and the validators are
the code under test. `MANIFEST.json` records the endpoint, the parameters,
the row count, the credit cost and a hash of every one, and a test fails if
a fixture stops matching its entry. Never edit a fixture by hand, rerun the
script.

The set covers 15 weeks each of 2023, 2024 and 2025, because phase 2 replays
them to backtest those seasons. Point `CFBD_CACHE_DIR` at a directory seeded
from the fixtures and any script runs offline with no key and no credits.

One limit worth knowing before phase 2. The Odds API serves current and
upcoming odds on the free tier, and its historical endpoint is a paid add
on, so FanDuel boards cannot be back captured for 2023 through 2025. The
market numbers for those seasons come from CFBD `/lines`, which carries
opening and closing spreads and totals by provider. The FanDuel board
fixtures cover the live path and the parser only. Run the capture with
`--archive` to file each board pull under its own timestamp so the multi
week set builds up over the season.

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

## Reading the record

Every row the grader writes carries a verdict beside its record, computed
from a Wilson 95 percent interval against the 52.4 percent breakeven at
-110. A row reading `no_signal` is not weak evidence, it is no evidence,
because the interval still contains breakeven.

The numbers are worth knowing before the first week grades. A 3 and 1 record
carries an interval from 30 to 95 percent. Sixty wins and 40 losses, a 60
percent record over 100 picks, still reads `no_signal` at 50.2 to 69.1.
Even 160 and 120 is not there. A verdict of `beats_breakeven` needs several
hundred decided picks.

The card is capped at 6 a week and often produces 2, so a 14 week season
yields between 28 and 84 graded picks across 16 factor tags. Most cells will
hold 3 or 4 entries for a long time. The point of the verdict is that the
handicapper cannot read "first half unders are 1 and 4" as a lesson and
start avoiding them, which is learning noise and getting worse while
appearing to improve.

Expect the file to read `no_signal` almost everywhere for a long time. That
is the file being honest.

## What this is not

Opinions on football games. Not investment advice, not a guarantee, and not a
system that is known to work, because it has not run long enough for anyone
to know that. The record on the dashboard is the whole argument, including
the losing weeks.

If gambling stops being fun, call 1-800-GAMBLER.
