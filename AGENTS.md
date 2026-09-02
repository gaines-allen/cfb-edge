# CFB Edge

Four agents, one ledger. Nobody grades their own work.

## The roster

odds-scout pulls the FanDuel board through The Odds API and tracks movement.
It never picks.

handicapper reads the board and the grader's memory file, works the slate,
and publishes only plays above 8.0 out of 10. It never grades.

grader settles every pick against the final score, computes record, ROI and
closing line value, rebuilds the calibration and factor scorecards, and
writes the lessons the handicapper has to read next week. It never picks.

site-builder renders the ledger to site/index.html. It never computes a
record of its own and never rewrites a rationale.

Agent definitions are in .Codex/agents/. Read the relevant one before doing
that agent's job.

## The week

Wednesday morning: odds-scout pulls the board, handicapper builds the slate
and writes the card. Six plays is the target, above 8.0 is the bar, and a
short card beats a padded one. The weekly-card workflow now runs that same
research unattended and opens a pull request with the result. It writes a
draft, deterministic code validates it against scripts/lib/card_rules.py,
and a person merges. Read the sources on every pick before you do.

Thursday through Saturday: odds-scout pulls daily. The Saturday morning pull
is the one that captures closing lines, so it is the one that cannot be
missed.

Sunday: grader settles, rebuilds memory, writes the week's report.

Tuesday: grader does the process review, separating picks that were wrong
from picks that were right and lost anyway.

Every day: the GitHub Action does the deterministic part on its own. Pull,
grade, rebuild, commit, deploy.

## Never

Never scrape fanduel.com. Their terms prohibit it, a front end scraper breaks
constantly, and it puts the account at risk. FanDuel's posted numbers come
through The Odds API, which carries them as a named bookmaker.

Never hand-edit data/picks.json to change a result, a confidence, or a line.
Those are frozen at the timestamp they were written and the whole point of
the ledger is that they cannot move.

Never pad the card to six.

Never publish a pick built on an unconfirmed injury without saying it is
unconfirmed and dropping the confidence.

## Files

    data/picks.json         every pick ever made, live and shadow
    data/board.json         the current FanDuel board
    data/line_history.json  append-only record of what the market did
    data/memory.json        what the grader learned, read before every card
    data/slate.json         this week's candidates, rebuilt by make_slate.py
    site/index.html         the dashboard, self-contained, no network needed

## Before you change anything

Run `python3 scripts/selftest.py`. It runs the pytest suite in `tests/`
offline against captured payloads and takes 4 seconds. Every script that
writes takes `--dry-run`, and using it first costs nothing.

Two properties are load bearing and have tests that fail if either moves.
`scripts/lib/teams.py` returns None rather than guessing, so do not add
fuzzy or prefix matching to it. `suggested_confidence` caps at 7.5, below
the 8.0 publish threshold, so the ratings model can never justify a
published pick on its own.

## Keys

ODDS_API_KEY from the-odds-api.com, free tier is 500 credits a month and the
default pull costs 5.

CFBD_API_KEY from collegefootballdata.com/key, free tier is 1,000 calls a
month, and every call is cached to disk for 12 hours so reruns cost nothing.

Both live as GitHub Actions secrets, and as environment variables locally.

## Writing

Everything written in this repo, including a pick rationale and a grader
report, follows the house rules: prose not bullet lists, no em dashes, plain
verbs, numbers instead of adjectives, no summary paragraph at the end.
