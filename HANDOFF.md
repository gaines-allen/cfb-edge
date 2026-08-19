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

## Open workstreams

Descriptions only. Ordering is a separate decision.

**Backtest.** Run the pipeline against 2023 through 2025, which CFBD has in
full including quarter scores and opening and closing lines. For each week,
build the slate from ratings as they stood, apply the confidence scoring, and
grade against what happened. Answers whether a two sigma edge actually beats
52.4 percent, whether the confidence scale separates anything, and whether 8.0
is the right threshold. The honest limit: this backtests the ratings model and
the staking math, not the handicapper's research layer, which cannot be
simulated. Heaviest CFBD usage of anything here, likely Tier 2 territory.

**Deepen the inputs.** The projection currently uses SP+ overall and its
offense and defense split, FPI, SRS and Elo. Absent: returning production,
which matters most in preseason; PPA and success rate; explosiveness splits;
pace, which drives totals; garbage-time-adjusted numbers; weather; and the SEC
and Big Ten availability reports. Each addition needs to be shown to improve
the number, which means it needs the backtest to measure against.

**Half markets.** Build a poller that checks the per-event endpoint as game
week approaches and captures half lines the moment books post them, with the
credit accounting that implies. Decide what happens when they never appear.
The derived numbers in the model, 0.55 of the full spread and 0.47 of the
total, are a prior for comparison, not a price you can bet.

**Harden.** Replace the hand-rolled selftest with pytest, run it on pull
requests, validate the shape of every external response so a source change
fails loudly instead of producing quiet garbage, add a dry-run mode, add
structured logging. Two real bugs shipped in one build session and both were
caught by looking at live data rather than by any test.

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
