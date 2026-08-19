# Handoff

Written at the point the system first ran end to end on its own. Read this
before changing anything, particularly the section on what already broke.

## What exists and works

A public repo at github.com/gaines-allen/cfb-edge with a live dashboard at
https://gaines-allen.github.io/cfb-edge/.

Four agent specs in `.claude/agents/`, split so none of them marks its own
homework. `odds-scout` pulls FanDuel numbers and never picks. `handicapper`
works the slate and never grades. `grader` settles tickets and never picks.
`site-builder` renders the ledger and computes nothing.

A GitHub Action (`.github/workflows/daily.yml`) that runs the entire
deterministic half by itself: pull the board, settle finished games,
recalibrate against the market, build the candidate slate, run the selftest,
rebuild the page, commit, deploy. Verified green end to end on run #4.

Two recurring Cowork tasks, Wednesday 8:07am for the card and Sunday 10:01am
for the grading review. Neither can push to GitHub, so both deliver files and
notify. They are a safety net for when Claude Code is not open, and should be
retired once equivalent routines exist in Code.

## The data sources, and what each is actually for

The Odds API supplies FanDuel's posted numbers. Free tier is 500 credits a
month. A board pull is 3 credits and the schedule burns about 90 a month.
Never scrape fanduel.com; their terms prohibit it and the API carries FanDuel
as a named bookmaker anyway.

CollegeFootballData supplies three separate things: the SP+, FPI, SRS and Elo
ratings behind the projected number; final scores and quarter-by-quarter line
scores, which is the only reason first-half picks can be graded; and opening
and closing spreads as a second read on closing line value. Free tier is 1,000
calls a month against roughly 300 of usage. Tier 1 at one dollar a month
raises that to 5,000 and adds game weather and opponent-adjusted metrics.

ESPN's public team endpoint needs no key and supplies the mascot map. It is
undocumented and unsanctioned, so keep it behind `scripts/build_aliases.py`
and expect it to change shape without warning.

## Things that already broke, so you do not rediscover them

**Team names carry mascots.** The Odds API returns "Alabama Crimson Tide"
where CFBD returns "Alabama". Stripping trailing words does not work, because
mascots run one to three of them and "Arizona State Sun Devils" would become
"Arizona State Sun". The mascot map in `data/team_aliases.json` comes from
ESPN, which publishes location and mascot separately. Regenerate with
`python3 scripts/build_aliases.py`.

**The matcher must never guess.** An earlier version fell back to a six
character prefix match, which quietly maps Ohio onto Ohio State. `teams.py`
now returns None rather than a plausible wrong answer, and `MUST_STAY_DISTINCT`
holds 27 near-collision pairs the selftest checks on every run. Do not add
fuzzy matching. An unmatched pick never settles, sits pending forever, and
silently vanishes from the record, which is the most dangerous failure this
system has.

**Duplicate dict keys silently delete variants.** A second `VARIANTS` entry
for a school wiped the first one's spellings. Python collapses those at parse
time, so the selftest reads `teams.py` as text to catch it.

**Half markets are not on the bulk odds endpoint.** Asking for `spreads_h1`
returns 422 INVALID_MARKET. They exist only on the single-event endpoint at 2
credits per game. Pulling them board-wide would cost 120 credits a run. Use
`fetch_odds.py --halves` with explicit event ids. As of late August no book is
posting college half lines at all.

**SP+ offense and defense are points scored and allowed, not differentials.**
Treating them as differentials produced 83 point totals against a 48.5 market
and made every candidate an Over. Each side's expected points is the average
of its own offense and the opponent's defense.

**Do not conflate an unrated school with an unmapped one.** A team with no FBS
rating is an FCS opponent and dropping that game is correct, since a buy game
is not a bet. A name nothing recognizes is a matcher bug. On a live board that
is 47 of the former and zero of the latter.

**The board runs weeks ahead.** Filter to the target week or you will rate a
November game off a preseason number.

## The one number that matters most

`data/model_calibration.json`, measured by `scripts/calibrate_model.py`
against the live board. First measurement: totals sit 0.95 points under market
with 3.25 points of dispersion, spreads 0.58 over with 2.77.

The bias gets subtracted, because a model that is a point light on every total
does not have an edge on every total, it has an offset.

The dispersion is the important half. It converts a points gap into a z score,
and it reframes everything. The largest disagreement across a 110 game board
was 2.1 sigma. A "six point edge" on a total sounds enormous and is under two
standard deviations. `suggested_confidence` therefore scores in sigma and caps
at 7.5, deliberately below the 8.0 publish threshold, so the ratings model can
never justify a live pick on its own. Everything above 8 has to be earned with
research the model cannot do. Preserve that property.

## Roadmap

All four workstreams are in scope. Harden and backtest come first, and they
are more coupled than they look: the fixtures the test suite needs are the
same captured API payloads the backtest replays, and the schema validators
that stop a silent shape change are what stop a backtest from quietly
scoring garbage. Build them together and each is roughly half the work.

### Phase 1, harden

Replace the hand-rolled `selftest.py` with pytest, keeping a thin wrapper so
the GitHub Action keeps calling one command. Capture real API payloads as
fixtures under `tests/fixtures/`: a full FanDuel board, a CFBD games response
with quarter scores, SP+ and FPI rows, an ESPN teams page. Those fixtures are
phase 2's input, so capture more than the tests strictly need.

Add shape validation on every external response. Every bug this system has
shipped came from an upstream field meaning something other than what the
code assumed, so a missing or renamed field has to raise rather than resolve
to None and flow onward as a confident number.

Add a dry-run flag to every script that writes, and structured logging with
the run id, the endpoint, the credit cost and the row count.

Run pytest on pull requests, not just on the daily schedule.

Done when a deliberately corrupted fixture makes a test fail rather than a
pick appear, and when `pytest` and the PR check are both green.

### Phase 2, backtest

Walk 2023, 2024 and 2025 week by week. For each week build the slate, apply
the confidence scoring, and grade against what actually happened using the
existing `scoring.py`, which is already covered by hand-worked cases.

**The trap that would make this lie to you.** CFBD's `/ratings/sp` returns
end-of-season SP+. Using it to score a week 3 game means the model already
knows how the season turned out. That is lookahead bias, and it will make a
mediocre model look excellent, which is the worst possible outcome because
you will then bet it. Every input has to be as-of the week being scored.
Weekly Elo from `/ratings/elo?week=` is genuinely time-varying. SP+ is not,
so either restrict SP+ to preseason projections, or reconstruct week-by-week
ratings from game results, or drop SP+ from the backtest and accept that you
are testing a weaker model than the live one. Whichever you pick, write down
which and why, because a backtest whose time handling is undocumented is
worth nothing.

Split the data. Fit and tune on 2023 and 2024, hold 2025 back untouched as
the test set. Tuning on everything and reporting the fit is the other classic
way to fool yourself.

Report win rate by sigma bucket against the 52.4 percent breakeven, ROI on
units risked, the CLV distribution, and a calibration curve of predicted
against actual. That last one is the real deliverable.

Honest limit worth restating: this tests the ratings model and the staking
math. It cannot test the handicapper's research layer, which is where every
pick above 8.0 is supposed to come from. A clean backtest tells you the
baseline is sound, not that the system wins.

Heaviest CFBD usage of anything here. Budget for Tier 2 at five dollars.

Done when it reports calibration on held-out 2025 with the time handling
documented.

### Phase 3, deepen the inputs

Only after phase 2, because "better" needs a measurement. Add one input at a
time, re-run the backtest, and keep it only if it improves the held-out set.
Candidates in rough order of expected value: returning production, which
matters most in preseason when there are no results yet; pace, which drives
totals more than anything else on the list; PPA and success rate;
explosiveness splits; garbage-time-adjusted numbers; weather, which needs
CFBD Tier 1; and the SEC and Big Ten availability reports.

Expect several of these to fail to improve anything. Record the ones that
did not work and why, so nobody re-adds them in November.

### Phase 4, half markets

Poll the per-event endpoint as game week approaches and capture half lines
the moment books post them, at two credits per game against a 500 credit
month, so the poller needs a shortlist rather than the board.

Once phase 2 exists, first-half performance is backtestable, because CFBD
carries quarter scores. Do that before betting them.

Decide what happens when the lines never appear, which is the current state
across every book. The derived numbers in the model, 0.55 of the full spread
and 0.47 of the total, are a prior for comparison, not a price. Nobody can
bet a number that is not posted.

## Rules that do not bend

Never scrape fanduel.com.

Never hand-edit a result, confidence, line or price in `data/picks.json`.
Those freeze at the timestamp they were written and the whole point of the
ledger is that they cannot move.

Never pad the card to six. A short card is the correct outcome when the slate
is priced well.

Never publish a pick built on an unconfirmed injury without saying it is
unconfirmed and dropping the confidence.

Never let `suggested_confidence` reach 8.0 on ratings alone.

Prose in this repo follows the house rules: no bullet lists, no em dashes,
plain verbs, numbers instead of adjectives, no summary paragraph at the end.
