"""
What a card has to satisfy before it can be published.

These rules exist because the handicapper is about to run without a person
watching it. The ratings model is capped at 7.5 so it can never justify a
published pick on its own, and that cap is enforced in model.py. This file
enforces the other half: that whatever lifted a pick from 7.5 to 8.0 is
research, that the research is sourced, and that the week's exposure stays
inside the ceiling.

One definition, two callers. log_picks.py checks these when a person logs a
card by hand, and validate_card.py checks them in the weekly workflow before
anything reaches the ledger. A rule that only ran in one of those places
would be a rule the other path could walk around.

Nothing here judges whether a pick is good. It judges whether the process
that produced it was followed.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

LIVE_THRESHOLD = 8.0
MAX_LIVE_PICKS = 6
MAX_UNITS_PER_PICK = 2.0
MAX_UNITS_PER_WEEK = 12.0

# The tag the ratings model supplies on its own. A pick whose only support is
# this one has not been researched, whatever its confidence says.
RATINGS_ONLY = "rating_edge"

# How stale a cited source may be. The handicapper is forbidden from reusing
# last week's reasoning, and a 3 week old report is last week's reasoning.
MAX_SOURCE_AGE_DAYS = 14

# A quote shorter than this matches a page by accident. "he is out" appears
# everywhere and proves nothing about the page being cited.
MIN_QUOTE_CHARS = 25

# Above this a pick stakes the maximum 2 units, so it carries a higher bar.
MAX_STAKE_THRESHOLD = 9.0

UNCERTAIN = re.compile(
    r"\b(unconfirmed|unverified|rumou?red|reportedly|unclear|expected to|"
    r"questionable|game.time decision)\b", re.I)


def units_for(confidence: float) -> float:
    """The staking ladder from the handicapper spec. 2 units is the ceiling."""
    c = float(confidence)
    if c >= 9.0:
        return 2.0
    if c >= 8.5:
        return 1.5
    return 1.0


def _sources(pick: dict) -> list[dict]:
    raw = pick.get("sources") or []
    return [s for s in raw if isinstance(s, dict)]


def _factors(pick: dict) -> set[str]:
    f = pick.get("factors")
    if isinstance(f, dict):
        return {k for k, v in f.items() if v}
    return set(f or [])


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat,
                  lambda s: datetime.strptime(s, "%Y-%m-%d")):
        try:
            d = parse(text)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


# ------------------------------------------------------------ per pick

def check_pick(pick: dict, i: int, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    errs: list[str] = []
    label = f"[{i}] {pick.get('matchup', '?')} {pick.get('side', '?')}"

    try:
        conf = float(pick.get("confidence"))
    except (TypeError, ValueError):
        return [f"{label}: confidence is not a number"]

    factors = _factors(pick)
    sources = _sources(pick)
    live = conf >= LIVE_THRESHOLD

    # The staking ladder is not a suggestion.
    want_units = units_for(conf)
    got_units = float(pick.get("units", want_units))
    if live and abs(got_units - want_units) > 1e-6:
        errs.append(f"{label}: confidence {conf} stakes {want_units} units, "
                    f"not {got_units}")
    if got_units > MAX_UNITS_PER_PICK:
        errs.append(f"{label}: {got_units} units is above the {MAX_UNITS_PER_PICK} "
                    f"unit ceiling on a single pick")

    if not live:
        return errs

    # The core rule. Everything above 8.0 has to be earned with research the
    # model cannot do, so a published pick supported only by the rating gap
    # is the exact thing the 7.5 cap exists to prevent.
    beyond_ratings = factors - {RATINGS_ONLY}
    if not beyond_ratings:
        errs.append(
            f"{label}: published at {conf} on {RATINGS_ONLY} alone. The "
            f"ratings model caps at 7.5 on purpose. Name the researched "
            f"reason the market is wrong or drop this below 8.0.")

    if not sources:
        errs.append(f"{label}: published at {conf} with no sources. Every "
                    f"researched claim needs a source and a date.")

    for j, s in enumerate(sources):
        if not str(s.get("url", "")).startswith("http"):
            errs.append(f"{label}: source {j} has no usable url")
        # The quote is the exact sentence from the page that supports the
        # claim, and scripts/verify_sources.py checks it is really there.
        # Without it nothing can tell a read source from a paraphrased one.
        quote = str(s.get("quote") or "").strip()
        if not quote:
            errs.append(
                f"{label}: source {j} has no quote. Give the exact sentence "
                f"from the page that supports the claim, so it can be checked "
                f"against the page rather than taken on trust.")
        elif len(quote) < MIN_QUOTE_CHARS:
            errs.append(
                f"{label}: source {j} quotes {len(quote)} characters, under "
                f"the {MIN_QUOTE_CHARS} needed to identify a sentence.")
        d = _parse_date(s.get("date"))
        if d is None:
            errs.append(f"{label}: source {j} has no parseable date")
        elif d > now + timedelta(days=1):
            errs.append(f"{label}: source {j} is dated in the future")
        elif now - d > timedelta(days=MAX_SOURCE_AGE_DAYS):
            errs.append(
                f"{label}: source {j} is {(now - d).days} days old. Reusing "
                f"research older than {MAX_SOURCE_AGE_DAYS} days is reusing "
                f"last week's reasoning.")

    # College football has no league wide injury report, so an injury claim
    # without a source is a rumour, and a pick built on a rumour that turns
    # out wrong is bad process rather than bad luck.
    if "injury_edge" in factors and not sources:
        errs.append(f"{label}: cites injury_edge with no source")

    rationale = str(pick.get("rationale", ""))
    if "injury_edge" in factors and UNCERTAIN.search(rationale) and conf >= 8.5:
        errs.append(
            f"{label}: the rationale says the status is not confirmed, so the "
            f"confidence has to come down below 8.5 rather than sit at {conf}")

    # 2 units is the largest bet this system makes, so it needs more than 1
    # reason and more than 1 source behind it.
    if conf >= MAX_STAKE_THRESHOLD:
        if len(beyond_ratings) < 2:
            errs.append(f"{label}: {conf} stakes the maximum 2 units on "
                        f"{len(beyond_ratings)} researched factor. Two or more.")
        if len(sources) < 2:
            errs.append(f"{label}: {conf} stakes the maximum 2 units on "
                        f"{len(sources)} source. Two or more.")

    return errs


# ------------------------------------------------------------ per card

def check_card(picks: list[dict], now: datetime | None = None) -> list[str]:
    errs: list[str] = []
    for i, p in enumerate(picks):
        errs += check_pick(p, i, now=now)

    live = [p for p in picks
            if float(p.get("confidence", 0) or 0) >= LIVE_THRESHOLD]

    if len(live) > MAX_LIVE_PICKS:
        errs.append(f"card carries {len(live)} live picks. The ceiling is "
                    f"{MAX_LIVE_PICKS}, and a short card beats a padded one.")

    staked = sum(float(p.get("units", units_for(p.get("confidence", 8.0))))
                 for p in live)
    if staked > MAX_UNITS_PER_WEEK:
        errs.append(f"card risks {staked} units. The weekly ceiling is "
                    f"{MAX_UNITS_PER_WEEK}.")

    # One opinion per game. Keying this on game and market let a spread
    # and a total on the same matchup both through, which is the same
    # read priced two ways: a card of 6 that is really 4 opinions, staked
    # as though it were 6 independent ones.
    seen: dict[str, int] = {}
    for i, p in enumerate(live):
        key = p.get("event_id")
        if key in seen:
            errs.append(f"[{i}] {p.get('matchup')} is already on the card at "
                        f"index {seen[key]}. Two markets on one game is one "
                        f"opinion at twice the size. Take the stronger.")
        seen[key] = i

    return errs


def correlation_warnings(picks: list[dict]) -> list[str]:
    """
    Advisory, not blocking. Several live picks resting on the same single
    reason are not several independent bets, and the staking math assumes
    they are. Week 1 of 2026 produced 3 Unders on heavy favourite non
    conference openers, all off the same mechanism.
    """
    live = [p for p in picks
            if float(p.get("confidence", 0) or 0) >= LIVE_THRESHOLD]
    counts: dict[str, list[str]] = {}
    for p in live:
        for f in _factors(p) - {RATINGS_ONLY}:
            counts.setdefault(f, []).append(p.get("matchup", "?"))

    out = []
    for factor, games in sorted(counts.items()):
        if len(games) >= 3:
            out.append(f"{len(games)} live picks rest on {factor}: "
                       f"{', '.join(games)}. Correlated exposure, so the "
                       f"effective stake is larger than the unit count says.")
    return out
