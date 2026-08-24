#!/usr/bin/env python3
"""
site-builder's tool. Derives every figure from the ledger in Python, then
writes one self-contained site/index.html with the data inlined. No fetch,
no CDN, no storage APIs, so it renders from GitHub Pages or from a file://
path with no network.

    python3 scripts/build_site.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import store  # noqa: E402
from lib.model import COHERENCE_TOLERANCE, load_calibration  # noqa: E402
from lib.teams import canonical, load_logos  # noqa: E402
from lib.runlog import RunLog  # noqa: E402
from lib.scoring import (  # noqa: E402
    annotate, breakdown, calibration_table, summarize,
)

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BREAKEVEN = 52.4  # win rate needed at -110

RESULT_LABEL = {"win": "WIN", "loss": "LOSS", "push": "PUSH",
                "pending": "PENDING", "void": "VOID"}
MARKET_LABEL = {"spread": "Spread", "total": "Total", "moneyline": "Moneyline"}
PERIOD_LABEL = {"full": "Full game", "h1": "1st half", "h2": "2nd half"}


def fmt_line(p: dict) -> str:
    if p.get("line") is None:
        return ""
    v = float(p["line"])
    if p["market"] == "total":
        return f"{v:g}"
    return f"{v:+g}"


def pick_title(p: dict) -> str:
    """
    The play as it would be written on a ticket, and nothing else. The
    period, the market and the price each have their own place on the card,
    so repeating them here printed the price twice.
    """
    line = fmt_line(p)
    return f"{p['side']} {line}".strip()


def pick_full(p: dict) -> str:
    """The long form, for anywhere the play has to stand on its own."""
    return (f"{pick_title(p)}, {PERIOD_LABEL[p['period']].lower()}, "
            f"{p['price']:+d}")


def cumulative_units(picks: list[dict]) -> list[dict]:
    settled = sorted(
        [p for p in picks if p.get("live") and p.get("result") in ("win", "loss", "push")],
        key=lambda p: (p.get("graded_at") or "", p.get("kickoff") or ""),
    )
    running, out = 0.0, []
    for i, p in enumerate(settled, 1):
        running += float(p.get("units_net", 0.0))
        out.append({
            "n": i,
            "units": round(running, 2),
            "week": p.get("week"),
            "label": f"W{p.get('week')} {p.get('side')}",
            "result": p.get("result"),
        })
    return out


def weekly_rows(picks: list[dict]) -> list[dict]:
    by_week: dict[tuple, list[dict]] = defaultdict(list)
    for p in picks:
        if p.get("live"):
            by_week[(p.get("season"), p.get("week"))].append(p)

    rows = []
    for (season, week), group in sorted(by_week.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
        s = summarize(group)
        pending = sum(1 for p in group if p.get("result") == "pending")
        rows.append({
            "season": season, "week": week,
            "picks": len(group), "pending": pending,
            **s,
        })
    return rows


# ---------------------------------------------------------------- voice

# The character. Every line the page says out loud lives here, so the
# persona can be rewritten without touching a single tag.
#
# Steve is a wise guy who books. He needles, he does not sell, and the
# needling only works because he is telling the truth. The joke and the
# honesty are the same move: a man willing to say the record makes him look
# like an idiot is a man you can believe about the record.
#
# So the empty states, the losing weeks and the "this means nothing yet"
# lines are the funniest thing he says, not exceptions carved out of him.
# A guy who only shows you the good stretch is selling something, and he
# says so on the page.
#
# He talks the way the rest of this repo writes. Prose, no lists, no em
# dashes, plain verbs, numbers instead of adjectives, and he never signs
# off with a summary. He is a persona and the page says so, because a page
# built on not overclaiming cannot open by implying a real handicapper.
VOICE = {
    "name": "STEVE",
    "full_name": "Steve",
    "kicker": "College football picks",
    "logo": "steve.png",
    "sign_off": "Steve",
    "tagline": "Six plays. Every number. Every result, including the ones "
               "that make me look like an idiot.",
    "subhead": "Nothing goes up under 8.0 out of 10, and the card is posted "
               "before kickoff, so I can't quietly improve it afterwards. "
               "Neither can you.",

    # Openly a persona. A page built on not overclaiming cannot open by
    # implying a real handicapper is behind it, and the joke lands better
    # when it is honest about itself.
    "about": "Steve is the voice of this system, not a person. Nobody's "
             "back there with a cigar. The numbers come from a ratings "
             "model, the reasons come from research that is checked "
             "against its sources before anything is posted, and every "
             "figure about the record is computed from the ledger rather "
             "than typed in by someone having a good week.",

    "page_title": "Steve. The card, and the record.",
    "running_heading": "Best bets",
    "running_note": "The {n} I like best right now, not picks. These "
                    "move. A number the market has caught up to stops "
                    "being worth anything, so what was good Monday is "
                    "often gone by Thursday, and I'd rather show you that "
                    "than quietly swap it out. One thing before you "
                    "scroll: a total needs a wider gap than a spread to "
                    "mean the same thing, so these are not simply ranked "
                    "by points apart.",
    "running_empty": "Nothing tracked yet. This fills in once the board "
                     "is up and I've had a day to watch it move.",
    "running_new": "new today",
    "running_held": "holding",
    "running_since": "tracked {days} days",
    "running_stacked": "Worth saying: those 6 leans cover {games} games, "
                       "not 6. When a game shows up twice it is one "
                       "opinion sold to you twice, and the second one is "
                       "not a free roll.",
    "running_dropped_heading": "Fell off",
    "running_dropped_note": "These held a top 6 spot earlier in the week "
                            "and don't now. Usually that means the market "
                            "moved to my number, which is the market doing "
                            "its job and me having nothing left to say.",

    # The explainer. Every figure in it comes from the live measurement,
    # so the copy cannot drift from the arithmetic it is describing.
    "edge_heading": "What confidence actually means",
    "edge_body_1": "Every lean carries a number from 1 to 10. Here's how "
                   "I build it, because if you can't take a number apart "
                   "you've got no business betting it.",
    "edge_body_2": "I have a number for a game and the book has a number. "
                   "The gap between them is the edge, in points. Points "
                   "lie, though. Six points on a total is not the same "
                   "animal as six points on a spread, because totals swing "
                   "wider than spreads do.",
    "edge_body_3": "So I measure how far off I usually am, and score the "
                   "gap against that. Across {sample} games this week my "
                   "spread numbers sit about {spread_gap} points from the "
                   "book and my totals about {total_gap}. So six points on "
                   "a total is not even twice my normal miss. Sounds "
                   "enormous. It isn't.",
    "edge_body_4": "Confidence turns that into a 1 to 10. A gap the size "
                   "of my usual miss is a 5. Every extra miss worth of gap "
                   "adds another 1.5. That's the whole formula, there's no "
                   "second page. Confidence and edge are one fact said "
                   "twice, so if you only want to look at one number, look "
                   "at this one.",
    "edge_body_5": "What counts as good on this board. The middle lean is "
                   "a 5. The top tenth get to 6.5. The best thing up there "
                   "right now is a 7. If something ever came back a 9 I'd "
                   "assume my ratings broke before I'd assume the book left "
                   "four touchdowns lying on the table.",
    "edge_body_6": "And the ceiling. This number stops at {cap} however "
                   "big the gap gets, and the publish line is {publish}. "
                   "Nothing I compute can put a play on the card by "
                   "itself. That last half point has to come from "
                   "something the model cannot see, and that is the entire "
                   "point of the thing.",

    "board_heading": "This week's board",
    "board_note": "Every game on the slate, my number next to theirs, "
                  "whether or not it made the card. Strongest lean at the "
                  "top, weakest at the bottom, so the further you scroll "
                  "the less I have to say. If you like something I passed "
                  "on, that's your money and your business.",
    "board_empty": "No board yet. It goes up Monday of game week.",
    "board_stale": "The lines I have are {age} hours old, and I don't post "
                   "a number I can't stand behind. The board is down until "
                   "the next pull lands. That's not caution theater, that's "
                   "the difference between me and a tout.",
    "board_held": "{count} came off the book since the last pull, so I "
                  "pulled them here too. A number I can't verify is a "
                  "number you don't get: {games}.",
    "board_incoherent": "{count} I'm sitting out, and not because of the "
                        "line. My spread and my total disagreed with each "
                        "other about the same game, and a number that "
                        "argues with itself is worth nothing to you: "
                        "{games}. I'd rather show you an empty row than "
                        "two of my own numbers that can't both be right.",
    "board_moved": "Moved",
    "board_kick": "Kickoff",
    "board_my_spread": "My spread",
    "board_mkt_spread": "Their spread",
    "board_my_total": "My total",
    "board_mkt_total": "Their total",
    "board_col_line": "Favorite / total",
    "board_lean": "Where I lean",
    "board_no_lean": "Priced right. No lean.",

    "lock_title": "The Lock of the Week",
    "lock_note": "The Lock is whichever play I'm most confident in. It "
                 "gets the crown, not extra units, because doubling a bet "
                 "is how confident men go broke.",

    "live_heading": "Saturday, live",
    "live_note": "Scores update through the afternoon while games run. "
                 "Covering here is where the bet stands right now, not a "
                 "grade. Grading happens Sunday, by the machine, not by "
                 "me squinting at a scoreboard.",
    "live_covering": "covering",
    "live_not_covering": "not covering",
    "live_tied": "dead even",
    "live_final": "final",

    "card_heading": "The card",
    # The empty state shows all week before Friday, so it has to cover 2
    # truths at once: early in the week the card is not built yet, and
    # after Friday an empty card means the board was priced right.
    "card_empty": "The card gets built through the week. A Thursday game "
                  "that earns a spot goes up early, and the rest lands "
                  "Friday by 6. If nothing's here after that, the board's "
                  "priced right and I'm not inventing a play to fill the "
                  "space.",
    "card_short": "That's the card. A short week means the numbers were "
                  "fair. It doesn't mean I was out golfing.",
    "card_pending": "Still running",

    "book_heading": "The book",
    "book_empty": "Nothing has settled, so there's nothing to brag about "
                  "and nothing to apologize for. Ask me Sunday night.",

    "calibration_heading": "Do my numbers mean anything",
    "calibration_empty": "Nothing has graded. Right now an 8 and a 9 are "
                         "two numbers I wrote down with a lot of confidence "
                         "and no evidence.",
    "calibration_note": "Shadow picks under the publish line are in here on "
                        "purpose. That's how the line gets tested instead "
                        "of assumed. Units on the below-8 row are pretend, "
                        "because I don't stake what I don't publish.",

    "cumulative_heading": "Where you would be",
    "cumulative_empty": "Nothing has settled yet.",
    "cumulative_note": "Net units across settled plays, in the order they "
                       "settled. No smoothing, and the chart doesn't start "
                       "somewhere flattering.",

    "weekly_heading": "How the weeks went",
    "weekly_empty": "No weeks on the board yet.",

    "split_heading": "Where it came from",
    "split_empty": "Nothing settled to split yet.",

    "factors_heading": "What my reasons have been worth",
    "factors_empty": "This fills in once picks settle. Then we find out "
                     "which of my reasons were reasons and which were "
                     "vibes.",

    "lessons_heading": "What I got wrong",
    "lessons_empty": "Nothing to own up to yet. Give it a week.",

    "no_signal": "Too few picks to mean anything.",
    "no_signal_long": "Before anyone gets excited, that record hasn't "
                      "decided enough games to mean a thing. Swing a "
                      "couple of results either way and it covers "
                      "everything from losing money to printing it, "
                      "including the 52.5 percent you need just to break "
                      "even. So it's as consistent with me being lucky as "
                      "with me being good. I post it anyway, because a guy "
                      "who only shows you the good stretch is selling "
                      "something.",
    "no_signal_short": "Same warning as above. Not enough has decided for "
                       "these bars to be worth the ink.",

    # The row of figures used to read like column headers on a report.
    "stat_units": "Up",
    "stat_units_down": "Down",
    "stat_record": "Record",
    "stat_winrate": "Hit rate",
    "stat_roi": "Return",
    "stat_clv": "Line value",
    "stat_close": "Beat the close",
    "stat_pending": "Still out there",
    "col_reading": "What that tells you",
    "col_week": "Week",
    "col_plays": "Plays",
    "col_units": "Units",
    "col_kind": "Kind",
    "col_split": "Split",
    "col_reason": "Reason",

    # Even the provenance is him. It was the last line on the page still
    # written by a machine about a machine.
    "provenance": "I pulled the board at {board}, rebuilt this at {built}, "
                  "and there are {credits} credits left on the account. "
                  "Lines come from The Odds API, ratings and results from "
                  "CollegeFootballData.",
    "provenance_noboard": "I have not pulled a board yet. Lines come from "
                          "The Odds API, ratings and results from "
                          "CollegeFootballData.",

    "pick_numbers": "Here's the math I did before I liked it.",
    "sources_heading": "Where I got it",
    "sources_note": "Every link was opened and the quoted line checked "
                    "against the page before the pick went up. If I say a "
                    "coach said it, something went and read the page.",

    # Deliberately straight. Nobody should have to get past a bit to find
    # the helpline.
    "disclaimer": "These are opinions on football games, not investment "
                  "advice. This model is being tested in public and it has "
                  "been wrong. Bet only what you can afford to lose. If "
                  "gambling stops being fun, call 1-800-GAMBLER.",
}




def incoherent(g: dict) -> bool:
    """
    Whether a game's two halves disagree about the game.

    make_slate writes this flag, but the gate is recomputed here from the
    model's own numbers rather than trusted. A slate built before the
    flag existed carries no flag, and "the field is missing" must not read
    as "the game is fine". The page is the last thing standing between a
    broken number and a reader.
    """
    if g.get("incoherent"):
        return True
    m = g.get("model") or {}
    inp = m.get("inputs") or {}
    gap = m.get("coherence_gap")
    if gap is None:
        sp = m.get("projected_spread")
        hp, ap = inp.get("home_points"), inp.get("away_points")
        if None in (sp, hp, ap):
            return False
        gap = abs(-sp - (hp - ap))
    return gap > COHERENCE_TOLERANCE


def board_rows() -> list[dict]:
    """
    The whole week, one row per game, for the collapsible board. Logos come
    off the ESPN map, movement comes off the line history the fetcher has
    been appending all week: the first snapshot against the latest one, for
    the home spread and the total.
    """
    slate = store._load(store.DATA / "slate.json", {})
    history = store.load_line_history()
    logos = load_logos()
    known_locations = set(logos)
    market_by_event: dict[str, dict] = {}
    for bg in store.load_board().get("games", []):
        spread = total = None
        for ln in bg.get("lines", []):
            if ln.get("market") == "spreads" and ln.get("side") == bg.get("home_team"):
                spread = ln.get("point")
            elif ln.get("market") == "totals" and ln.get("side") == "Over":
                total = ln.get("point")
        market_by_event[bg.get("event_id")] = {"spread": spread, "total": total}
    rows = []
    for g in slate.get("slate", []):
        series = history.get(g.get("event_id"), [])
        move = {}
        if len(series) >= 2:
            first, last = series[0], series[-1]
            for label, key in (("spread", f"spreads|{g['home_team']}"),
                               ("total", "totals|Over")):
                a = (first.get(key) or {}).get("point")
                b = (last.get(key) or {}).get("point")
                if a is not None and b is not None and a != b:
                    move[label] = {"from": a, "to": b}
        model = g.get("model") or {}
        market = market_by_event.get(g.get("event_id"), {})
        rows.append({
            "market_spread": market.get("spread"),
            "market_total": market.get("total"),
            "event_id": g.get("event_id"),
            "kickoff": g.get("kickoff"),
            "matchup": g.get("matchup"),
            "home_team": g.get("home_team"),
            "away_team": g.get("away_team"),
            # Board names carry mascots and variant spellings, which is
            # exactly the problem canonical() exists for, so the logo
            # lookup goes through the full matcher rather than a half
            # reimplementation of it. A miss stays a miss: no logo beats a
            # wrong logo for the same reason None beats a guessed match.
            "home_logo": logos.get(
                canonical(g.get("home_team") or "", known_locations) or ""),
            "away_logo": logos.get(
                canonical(g.get("away_team") or "", known_locations) or ""),
            "neutral_site": g.get("neutral_site"),
            "projected_spread": model.get("projected_spread"),
            "projected_total": model.get("projected_total"),
            "home_short": model.get("home_team"),
            "away_short": model.get("away_team"),
            "incoherent": incoherent(g),
            "candidates": sorted(
                g.get("candidates") or [],
                key=lambda c: -(c.get("floor_confidence") or 0)),
            "movement": move,
        })
    # Best lean first, weakest last. Sorting by kickoff buried the best
    # thing on the board behind whatever happened to kick earliest, which
    # makes a reader scroll to find the point. Kickoff breaks ties and
    # still shows on every row, so nothing chronological is lost.
    def best_confidence(r):
        return max((c.get("floor_confidence") or 0)
                   for c in r["candidates"]) if r["candidates"] else -1

    rows.sort(key=lambda r: (-best_confidence(r), r.get("kickoff") or ""))

    # The staleness gate. A number only publishes when it can be verified
    # against the current pull. A game on the slate with no line in the
    # latest board has come off the book or failed its fetch, so it is
    # held back and named rather than shown with a number nobody can bet.
    # A slate built from a different pull than the board on disk means the
    # pipeline half updated, which is exactly the failure that once froze
    # the site for 2 days, so it is treated as stale outright.
    board_file = store.load_board()
    fetched_at = board_file.get("fetched_at")
    age_hours = None
    if fetched_at:
        try:
            import datetime as _dt
            fetched = _dt.datetime.fromisoformat(fetched_at)
            age_hours = round((_dt.datetime.now(_dt.timezone.utc)
                               - fetched).total_seconds() / 3600, 1)
        except ValueError:
            age_hours = None
    mismatched = bool(slate.get("board_fetched_at")
                      and fetched_at
                      and slate["board_fetched_at"] != fetched_at)
    board_stale = age_hours is None or age_hours > MAX_BOARD_AGE_HOURS \
        or mismatched

    def no_line(r):
        return r["market_spread"] is None and r["market_total"] is None

    held = [{"matchup": r["matchup"], "kickoff": r["kickoff"],
             "why": "incoherent" if r.get("incoherent") else "no_line"}
            for r in rows if no_line(r) or r.get("incoherent")]
    rows = [r for r in rows if not (no_line(r) or r.get("incoherent"))]

    return {"week": slate.get("week"), "season": slate.get("season"),
            "built_at": slate.get("built_at"), "rows": rows,
            "fetched_at": fetched_at, "age_hours": age_hours,
            "stale": board_stale, "mismatched": mismatched,
            "held": held}


# A line older than this is not current, whatever the workflow says. The
# daily pull runs every 24 hours, so 26 allows a slow runner and nothing
# more.
MAX_BOARD_AGE_HOURS = 26

RATING_LABEL = {"sp": "SP+", "fpi": "FPI", "srs": "SRS", "elo": "Elo"}

# Steve says the same thing six different ways or the card reads like a
# form letter. The variant is picked off the matchup so it is stable from
# pull to pull, which keeps a lean's defense from rewording itself every
# afternoon while the pick underneath it has not moved.
TOTAL_OPENERS = (
    "I got {home} for {hp} and {away} for {ap}. Call it {tot} on the "
    "scoreboard.",
    "Way I see it, {home} hangs {hp} and {away} answers with {ap}. "
    "That's {tot} between them.",
    "Pencil me in for {home} {hp}, {away} {ap}. Adds up to {tot}.",
)
SPREAD_OPENERS = (
    "I make it {fav} by {margin}{hfa}.",
    "On my sheet {fav} wins this by {margin}{hfa}.",
    "Give me {fav} minus {margin} on this one{hfa}.",
)
# How unusual a gap is still has to account for totals swinging wider
# than spreads, so the tier is chosen on the scaled figure. Only the
# points ever reach the page.
GAP_TALK = (
    (1.05, "which is nothing special, I see gaps like that all day"),
    (1.95, "which is a bigger gap than most games on my board"),
    (2.25, "which puts it in the top tenth of anything I'm looking at"),
    (99.0, "which is about as far apart as me and the book ever get"),
)


def half(v, signed: bool = False) -> str | None:
    """
    Every number on the page, rounded to the half point.

    A book prices in halves and a bettor reads in halves, so 6.83 on a
    confidence scale is false precision dressed as rigor. The model keeps
    its full value for sorting and for the publish gate. This is only what
    gets printed.
    """
    if v is None:
        return None
    r = round(float(v) * 2) / 2
    out = f"{r:.1f}"
    return f"+{out}" if signed and r > 0 else out


def bias_note(raw: float, published: float) -> str:
    """
    Say so when the bias correction moved the number.

    scripts/calibrate_model.py measures how far the model's numbers have
    landed from the market and subtracts that offset, so the raw ratings
    and the published figure never quite match. Leaving the gap unexplained
    invites the reader to check the arithmetic, find it off by half a
    point, and stop trusting the rest.
    """
    if half(raw) == half(published):
        return ""
    return (f" Straight off the ratings that's {half(raw)}, and I nudge it "
            f"for the way my numbers have been landing against the book.")


def defend(model: dict, market: str, side: str, market_line, sigma,
           model_number=None, edge_points=None) -> dict:
    """
    Steve's case for a lean, in numbers rather than adjectives.

    A lean has no research behind it, so the defense cannot be a story. It
    is the arithmetic that produced the disagreement, said out loud: what
    each team is worth, what that makes the game, what the book is asking,
    and how far apart those two are. Naming which ratings actually spoke
    matters most in week 1, when SRS and Elo are empty and a number that
    looks solid is standing on half its usual legs.

    The number quoted is the one the model published, not one rebuilt from
    the ratings. Those differ by the bias correction, and a defense that
    quietly disagrees with the figure printed beside it is worse than no
    defense at all.
    """
    inp = model.get("inputs") or {}
    home, away = model.get("home_team"), model.get("away_team")
    hfa = model.get("hfa_applied")
    seed = sum(ord(c) for c in f"{home}{away}{market}{side}")
    sources = []
    for key, label in RATING_LABEL.items():
        h = (inp.get("home_components") or {}).get(key)
        a = (inp.get("away_components") or {}).get(key)
        if h is not None and a is not None:
            sources.append(label)

    line = None
    if market == "total":
        hp, ap = inp.get("home_points"), inp.get("away_points")
        if hp is not None and ap is not None:
            raw = hp + ap
            tot = float(model_number) if model_number is not None else raw
            line = TOTAL_OPENERS[seed % len(TOTAL_OPENERS)].format(
                home=home, away=away, hp=half(hp), ap=half(ap), tot=half(tot))
            line += bias_note(raw, tot)
    else:
        hr, ar = inp.get("home_rating"), inp.get("away_rating")
        if hr is not None and ar is not None:
            # Quote the published number, not one rebuilt here. A reader
            # should not have to add the ratings gap to the home field
            # bump, and the two would not agree anyway.
            raw = (hr - ar) + (hfa or 0)
            margin = abs(float(model_number)) if model_number is not None \
                else abs(raw)
            fav = home if (model_number is not None
                           and float(model_number) < 0) or (
                              model_number is None and raw >= 0) else away
            hfa_txt = (f", and that's after I already spot the other guy "
                       f"{half(hfa)} for being at home" if hfa and fav != home
                       else f", {half(hfa)} of which is just for sleeping "
                       f"at home"
                       if hfa else ", neutral field, nobody gets a bump")
            line = SPREAD_OPENERS[seed % len(SPREAD_OPENERS)].format(
                fav=fav, margin=half(margin), hfa=hfa_txt)
            line += bias_note(abs(raw), margin)

    against = ""
    if market_line is None and market != "moneyline":
        against = ""
    elif market == "moneyline":
        against = "No number to haggle over here, you're just taking a side."
    elif market == "total":
        against = f"Book's asking {market_line}."
    else:
        against = f"Book's got it at {abs(market_line)}."

    unusual = ""
    if sigma is not None and edge_points is not None:
        how = next(t for cut, t in GAP_TALK if abs(float(sigma)) < cut)
        unusual = (f"That leaves {half(abs(float(edge_points)))} points "
                   f"between us, {how}.")

    built = ""
    if sources:
        missing = [l for l in RATING_LABEL.values() if l not in sources]
        built = f"Numbers come off {' and '.join(sources)}."
        if missing:
            built += (f" {' and '.join(missing)} haven't said a word yet, "
                      f"so I'm working off {len(sources)} opinions instead "
                      f"of 4. Bet it accordingly.")

    return {"line": line, "against": against, "unusual": unusual,
            "built": built,
            "text": " ".join(t for t in (line, against, unusual, built) if t)}


def hoist_built(defenses: list) -> str | None:
    """
    Pull the ratings caveat off the rows when it is the same on all of
    them. In week 1 every game is missing SRS and Elo, so repeating that
    line under each pick buries the part that differs. It moves above the
    card instead, where a reader takes it once and applies it to
    everything below.
    """
    kept = [d for d in defenses if d and d.get("built")]
    if len(kept) < 2 or len({d["built"] for d in kept}) != 1:
        return None
    shared = kept[0]["built"]
    for d in kept:
        d["built"] = ""
        d["text"] = " ".join(t for t in (d.get("line"), d.get("against"),
                                         d.get("unusual")) if t)
    return shared


def pick_defense(slate_by_event: dict, p: dict) -> dict | None:
    """
    The same case, for a pick that made the published card.

    Joins back to the slate by event id, then to the specific candidate by
    market, period and side, so the defense on the card is the arithmetic
    the running card was already showing. A pick whose game has aged off
    the slate gets no defense rather than one built on a stale number.
    """
    g = slate_by_event.get(p.get("event_id"))
    if not g or incoherent(g):
        # The researched reason still stands on its own sources. The
        # model's number does not, and printing it here is how two plays
        # on one game end up quoting different margins.
        return None
    cand = next((c for c in g.get("candidates") or []
                 if c.get("market") == p.get("market")
                 and c.get("period") == p.get("period")
                 and c.get("side") == p.get("side")), None)
    line = (cand or {}).get("market_line")
    if line is None and p.get("market") != "moneyline":
        line = p.get("close_line")
    return defend(g.get("model") or {}, p.get("market"), p.get("side"),
                  line, (cand or {}).get("edge_sigma"),
                  p.get("model_number") or (cand or {}).get("model_number"),
                  (cand or {}).get("edge_points") or p.get("edge"))


RUNNING_FILE = store.DATA / "running_card.json"


def running_card() -> dict | None:
    """
    The 6 leans the model likes best right now, with what each looked like
    when it first appeared. The movement is the point: a lean the market
    has caught up to is no longer a lean, and saying so is the difference
    between tracking edge and remembering a hunch.
    """
    raw = store._load(RUNNING_FILE, None)
    if not raw or not raw.get("card"):
        return None
    leans = raw.get("leans", {})
    # The running card carries only the lean. The teams, their logos and
    # the model behind the number live on the slate, so join back to it.
    slate_by_event = {g["event_id"]: g
                      for g in (store._load(store.DATA / "slate.json", {})
                                .get("slate", []))}
    logos = load_logos()
    known_locations = set(logos)

    def shape(key):
        e = leans.get(key)
        if not e:
            return None
        g = slate_by_event.get(e.get("event_id")) or {}
        # The running card keeps its own file, so a game gated off the
        # board would otherwise walk straight back on here. This is the
        # pair the reader actually sees side by side.
        if incoherent(g):
            return None
        moved_conf = None
        if e.get("first_confidence") is not None \
                and e.get("confidence") is not None:
            moved_conf = round(e["confidence"] - e["first_confidence"], 1)
        moved_line = None
        if e.get("first_line") is not None and e.get("line") is not None \
                and e["first_line"] != e["line"]:
            moved_line = {"from": e["first_line"], "to": e["line"]}
        return {
            "matchup": e.get("matchup"), "side": e.get("side"),
            "market": e.get("market"), "period": e.get("period"),
            "line": e.get("line"), "price": e.get("price"),
            "confidence": e.get("confidence"), "sigma": e.get("sigma"),
            "edge_points": e.get("edge_points"),
            "model_number": e.get("model_number"),
            "first_seen": e.get("first_seen"),
            "first_confidence": e.get("first_confidence"),
            "moved_confidence": moved_conf, "moved_line": moved_line,
            "days_tracked": len(e.get("history") or []),
            "rank": e.get("rank"), "peak_rank": e.get("peak_rank"),
            "on_board": e.get("on_board", True),
            "home_team": g.get("home_team"), "away_team": g.get("away_team"),
            "home_logo": logos.get(
                canonical(g.get("home_team") or "", known_locations) or ""),
            "away_logo": logos.get(
                canonical(g.get("away_team") or "", known_locations) or ""),
            "defense": defend(g.get("model") or {}, e.get("market"),
                              e.get("side"), e.get("line"), e.get("sigma"),
                              e.get("model_number"), e.get("edge_points")),
        }

    card = [c for c in (shape(k) for k in raw["card"]) if c]
    dropped = [c for c in (shape(k) for k in raw.get("dropped", [])) if c]
    if not card:
        return None
    dropped.sort(key=lambda c: c.get("confidence") or 0, reverse=True)
    caveat = hoist_built([c.get("defense") for c in card])
    return {"caveat": caveat,
            "updated_at": raw.get("updated_at"),
            "board_fetched_at": raw.get("board_fetched_at"),
            "season": raw.get("season"), "week": raw.get("week"),
            "card": card, "dropped": dropped[:4],
            "tracked": len(leans)}


LIVE_FILE = store.DATA / "live_scores.json"
LIVE_FRESH_MINUTES = 90


def live_strip(picks: list[dict]) -> dict | None:
    """
    Saturday's scoreboard, joined to the card. Informational only: the
    covering flag here never grades anything, because grading belongs to
    the grader and quarter detail this feed does not carry.
    """
    import datetime as _dt
    raw = store._load(LIVE_FILE, None)
    if not raw or not raw.get("fetched_at"):
        return None
    try:
        fetched = _dt.datetime.fromisoformat(raw["fetched_at"])
        age = (_dt.datetime.now(_dt.timezone.utc) - fetched).total_seconds() / 60
    except ValueError:
        return None
    if age > LIVE_FRESH_MINUTES:
        return None
    by_event = {g.get("event_id"): g for g in raw.get("games", [])}
    rows = []
    for p in picks:
        if not p.get("live") or p.get("result") != "pending":
            continue
        g = by_event.get(p.get("event_id"))
        if not g or g.get("home_score") is None:
            continue
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        covering = None
        if p.get("period") == "full" and p.get("line") is not None:
            if p.get("market") in ("spread", "Spread"):
                team = hs if p["side"] == g.get("home_team") else as_
                opp = as_ if p["side"] == g.get("home_team") else hs
                m = team - opp + float(p["line"])
                covering = "covering" if m > 0 else ("tied" if m == 0 else "not covering")
            elif p.get("market") in ("total", "Total"):
                t = hs + as_
                over = str(p["side"]).lower().startswith("o")
                if t == float(p["line"]):
                    covering = "tied"
                else:
                    covering = ("covering" if (t > float(p["line"])) == over
                                else "not covering")
        rows.append({"pick_id": p.get("id"), "matchup": p.get("matchup"),
                     "title": p.get("title"), "home_score": hs, "away_score": as_,
                     "completed": bool(g.get("completed")), "covering": covering})
    if not rows:
        return None
    return {"fetched_at": raw["fetched_at"], "rows": rows}


def lock_id(picks: list[dict], season, week) -> str | None:
    """
    The Lock of the Week is computed, never remembered: the most confident
    live pick on the current week's card, whenever the page renders. Ties
    break on the size of the edge, then on who was published first, so the
    crown cannot flicker between renders.
    """
    field = [p for p in picks
             if p.get("live") and p.get("season") == season and p.get("week") == week]
    if not field:
        return None
    field.sort(key=lambda p: (-float(p.get("confidence") or 0),
                              -abs(float(p.get("edge") or 0)),
                              p.get("placed_at") or "~"))
    return field[0].get("id")


def build_payload() -> dict:
    picks = store.load_picks()
    slate_by_event = {g["event_id"]: g
                      for g in (store._load(store.DATA / "slate.json", {})
                                .get("slate", []))}
    memory = store.load_memory()
    board = store.load_board()

    live = [p for p in picks if p.get("live")]
    settled = [p for p in live if p.get("result") in ("win", "loss", "push")]

    weeks = sorted(
        {(p.get("season"), p.get("week")) for p in live if p.get("week") is not None},
        key=lambda t: (t[0] or 0, t[1] or 0),
    )
    current = weeks[-1] if weeks else (None, None)

    cal_rows = calibration_table(picks)  # includes shadow picks on purpose
    for r in cal_rows:
        r["breakeven"] = BREAKEVEN

    by_factor = []
    for name, b in (memory.get("factor_scorecard") or {}).items():
        by_factor.append({"factor": name, **b})
    by_factor.sort(key=lambda r: r.get("units", 0), reverse=True)

    pick_rows = [p for p in picks]
    lock = lock_id(picks, current[0], current[1])

    payload = {
        "generated_at": store.now_iso(),
        "board": board_rows(),
        "running": running_card(),
        # The scale the site explains, read off the live measurement rather
        # than written into the copy, since dispersion moves every week.
        "scale": {
            "cap": 7.5,
            "publish": store.LIVE_THRESHOLD,
            "spread_gap": (load_calibration().get("spreads") or {}).get("sigma"),
            "total_gap": (load_calibration().get("totals") or {}).get("sigma"),
            "sample": (load_calibration().get("spreads") or {}).get("n"),
        },
        "live": live_strip(picks),
        "lock_id": lock,
        "board_fetched_at": board.get("fetched_at"),
        "credits_remaining": (board.get("quota") or {}).get("remaining"),
        "live_threshold": store.LIVE_THRESHOLD,
        "target_picks": store.TARGET_PICKS,
        "breakeven": BREAKEVEN,
        "overall": annotate(summarize(settled)),
        "overall_shadow": annotate(summarize(
            [p for p in picks if not p.get("live")
             and p.get("result") in ("win", "loss", "push")]
        )),
        "pending_count": sum(1 for p in live if p.get("result") == "pending"),
        "weeks": [{"season": s, "week": w} for s, w in weeks],
        "current": {"season": current[0], "week": current[1]},
        "weekly": weekly_rows(picks),
        "cumulative": cumulative_units(picks),
        "calibration": cal_rows,
        "by_market": breakdown(settled, "market"),
        "by_period": breakdown(settled, "period"),
        "by_factor": by_factor,
        "lessons": (memory.get("lessons") or [])[:12],
        "picks": [
            {
                "id": p.get("id"),
                "season": p.get("season"),
                "week": p.get("week"),
                "matchup": p.get("matchup"),
                "kickoff": p.get("kickoff"),
                "title": pick_title(p),
                "title_full": pick_full(p),
                "side": p.get("side"),
                "market": MARKET_LABEL.get(p.get("market"), p.get("market")),
                "period": PERIOD_LABEL.get(p.get("period"), p.get("period")),
                "line": p.get("line"),
                "price": p.get("price"),
                "confidence": p.get("confidence"),
                "units": p.get("units"),
                "model_number": p.get("model_number"),
                "edge": p.get("edge"),
                "close_line": p.get("close_line"),
                "clv_points": p.get("clv_points"),
                "result": p.get("result"),
                "result_label": RESULT_LABEL.get(p.get("result"), "?"),
                "units_net": p.get("units_net"),
                "final_score": p.get("final_score"),
                "rationale": p.get("rationale"),
                "factors": sorted((p.get("factors") or {}).keys()),
                # Shown on the card. Every one was opened and checked by
                # scripts/verify_sources.py before the pick was logged, so
                # showing them is the difference between a claim and a
                # receipt.
                "sources": p.get("sources") or [],
                # The model's own case, next to the researched reason. A
                # pick defended only by prose is one you cannot check.
                "defense": pick_defense(slate_by_event, p),
                "live": p.get("live"),
            }
            for p in sorted(picks, key=lambda x: (x.get("kickoff") or ""))
        ],
    }
    # Same treatment the running card gets: the ratings caveat sits once
    # above the card rather than under every ticket.
    payload["card_caveat"] = hoist_built(
        [p["defense"] for p in payload["picks"] if p.get("live")])
    return payload


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<meta name="description" content="__TAGLINE__">
<style>
  :root {
    --navy: #0e1a2f;
    --navy-2: #142442;
    --green: #1f3a2b;
    --green-2: #2a4d38;
    --cream: #f4ecd8;
    --paper: #f6efdd;
    --ink: #16233b;
    --ink-2: #46506a;
    --dim: #93a1b8;
    --gold: #c9a961;
    --gold-2: #e0c489;
    --win: #2f6d3d;
    --loss: #a83b31;
    --push: #6d7688;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    color: var(--cream);
    background:
      radial-gradient(1200px 600px at 50% -10%, #1a2c4c 0%, transparent 70%),
      var(--navy);
    font: 17px/1.6 "Iowan Old Style", "Palatino Linotype", Palatino,
          Georgia, "Times New Roman", serif;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 880px; margin: 0 auto; padding: 40px 22px 90px; }

  /* ------------------------------------------------ the masthead */
  header { text-align: center; padding-bottom: 10px; }
  header img {
    width: min(320px, 72vw); height: auto;
    filter: drop-shadow(0 8px 22px rgba(0,0,0,.45));
  }
  .tagline {
    font-size: clamp(22px, 3.4vw, 30px); line-height: 1.3;
    margin: 10px auto 12px; max-width: 30ch; color: var(--cream);
    text-wrap: balance;
  }
  .subhead {
    color: var(--dim); font-size: 15px; margin: 0 auto; max-width: 56ch;
    font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  .rule {
    height: 2px; margin: 30px auto 0; max-width: 320px;
    background: linear-gradient(90deg, transparent, var(--gold), transparent);
  }

  /* ------------------------------------- headings as banner ribbons */
  h2 {
    display: inline-block; margin: 56px 0 18px; padding: 7px 20px 8px;
    background: var(--green); color: var(--gold-2);
    font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
    font-size: 12px; font-weight: 800; letter-spacing: .18em;
    text-transform: uppercase; border-radius: 2px;
    box-shadow: inset 0 0 0 1px var(--green-2), 0 2px 0 rgba(0,0,0,.3);
  }
  h2::before, h2::after { content: "\2605"; opacity: .55; margin: 0 9px; font-size: 9px; }
  .said { font-size: 18px; max-width: 60ch; margin: 0 0 8px; }
  .quiet {
    color: var(--dim); font-size: 14px; max-width: 64ch;
    font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  .weekline {
    color: var(--gold); font-size: 12px; letter-spacing: .12em;
    text-transform: uppercase; margin: 0 0 22px;
    font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }

  /* --------------------------------------------- the pad, not a box */
  .tickets { display: grid; gap: 26px; }
  .ticket {
    position: relative; background: var(--paper); color: var(--ink);
    padding: 26px 28px 20px; border-radius: 2px;
    box-shadow: 0 10px 26px rgba(0,0,0,.4);
    /* faint ruling, the way a pad is ruled */
    background-image: repeating-linear-gradient(
      to bottom, transparent 0 30px, rgba(22,35,59,.06) 30px 31px);
    background-position: 0 74px;
  }
  /* torn top edge instead of a border */
  .ticket::before {
    content: ""; position: absolute; left: 0; right: 0; top: -5px; height: 6px;
    background: repeating-radial-gradient(
      circle at 5px 6px, var(--paper) 0 4.2px, transparent 4.4px 100%);
    background-size: 10px 6px;
  }
  .ticket::after {
    content: ""; position: absolute; left: 0; right: 0; top: 0; height: 4px;
    background: var(--gold);
  }
  .ticket.win::after { background: var(--win); }
  .ticket.loss::after { background: var(--loss); }
  .ticket.push::after { background: var(--push); }

  .tick-top {
    display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 14px;
  }
  .play {
    font-size: clamp(24px, 3.6vw, 32px); font-weight: 700; line-height: 1.15;
    letter-spacing: -.015em; color: var(--ink);
  }
  .price {
    color: #8a6410; font-size: 18px; font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .badge {
    margin-left: auto; font-size: 10px; letter-spacing: .14em;
    text-transform: uppercase; padding: 4px 11px; border-radius: 999px;
    font-weight: 800; color: #fff; background: var(--push);
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .badge.win { background: var(--win); }
  .badge.loss { background: var(--loss); }
  .badge.push { background: var(--push); }
  .badge.pending { background: transparent; color: #8a8168;
                   box-shadow: inset 0 0 0 1px #cbbf9f; }
  .matchup {
    color: #7b7360; font-size: 13px; letter-spacing: .06em;
    text-transform: uppercase; margin: 6px 0 16px;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .why { margin: 0 0 16px; max-width: 62ch; color: #223049; font-size: 17px; }
  .why.numbers {
    font-size: 14px; color: #4a5468; border-left: 2px solid #d9cdb2;
    padding-left: 14px; font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .meta {
    display: flex; flex-wrap: wrap; gap: 6px 22px; font-size: 13px;
    color: #7b7360; font-variant-numeric: tabular-nums;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .meta b { color: var(--ink); font-weight: 700; }
  .srcs {
    margin-top: 12px; font-size: 12px; color: #7b7360;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .srcs a { color: #8a6410; text-decoration: none; border-bottom: 1px solid #d3c39a; }
  .srcs a:hover { border-bottom-color: #8a6410; }
  .sign {
    margin-top: 10px; text-align: right; font-size: 27px; color: #5c5443;
    font-family: "Snell Roundhand", "Brush Script MT", "Segoe Script", cursive;
  }

  /* ----------------------------------------- the book, without boxes */
  .stats {
    display: flex; flex-wrap: wrap; gap: 0;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .stats { gap: 22px 0; }
  .stat {
    padding: 2px 26px; border-left: 1px solid #26364f;
  }
  .stat:first-child { padding-left: 0; border-left: 0; }
  .stat .k {
    font-size: 10px; letter-spacing: .16em; text-transform: uppercase;
    color: var(--dim); margin-bottom: 2px;
  }
  .stat .v { font-size: 28px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .stat.big .v { font-size: 46px; line-height: 1.05; }
  .v.pos { color: #7ecf84; } .v.neg { color: #ec8478; }

  table {
    width: 100%; border-collapse: collapse; font-size: 14px;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  th, td {
    text-align: left; padding: 10px 12px; border-bottom: 1px solid #22314a;
    font-variant-numeric: tabular-nums;
  }
  th {
    color: var(--gold); font-weight: 700; font-size: 11px;
    letter-spacing: .1em; text-transform: uppercase; border-bottom-color: var(--gold);
  }
  tr:last-child td { border-bottom: 0; }
  .nosig { color: var(--dim); font-size: 12px; }
  .chart { margin: 4px 0 6px; }
  svg { display: block; width: 100%; height: auto; }

  /* ------------------------------------------- the running card */
  .lean {
    padding: 22px 0 20px; border-bottom: 1px solid #22314a;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .lean:last-child { border-bottom: 0; }
  .leanhead { display: flex; align-items: center; gap: 18px; }
  .conf { flex: none; width: 72px; text-align: center; }
  .conf .n {
    font-size: 34px; font-weight: 800; line-height: 1;
    font-variant-numeric: tabular-nums; color: var(--gold-2);
  }
  .conf .lbl {
    font-size: 9px; letter-spacing: .14em; text-transform: uppercase;
    color: var(--dim); margin-top: 4px;
  }
  .teams {
    display: flex; align-items: center; gap: 12px; flex: 1 1 auto;
    min-width: 0; flex-wrap: wrap;
  }
  .team { display: flex; align-items: center; gap: 10px; }
  .team img { width: 42px; height: 42px; object-fit: contain; flex: none; }
  .team .nm { font-size: 18px; font-weight: 700; color: var(--cream); }
  .vs {
    color: var(--dim); font-size: 12px; letter-spacing: .14em;
    text-transform: uppercase;
  }
  .leanbody { padding-left: 90px; }
  .leanplay {
    font-size: 20px; font-weight: 800; color: var(--gold-2); margin-top: 12px;
  }
  .leanplay .price { font-size: 15px; }
  .leandef {
    font-size: 14px; color: var(--cream); margin-top: 8px; max-width: 62ch;
    line-height: 1.55;
  }
  .leanmeta {
    font-size: 12px; color: var(--dim); margin-top: 6px;
    font-variant-numeric: tabular-nums;
  }
  .leanmove { font-size: 12px; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .caveat.dark { color: #6b5a2e; border-left-color: #b39a55; }
  .caveat {
    font-size: 13px; color: var(--gold-2); margin: 0 0 18px;
    border-left: 2px solid var(--gold-2); padding-left: 12px; max-width: 62ch;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  @media (max-width: 620px) {
    .leanbody { padding-left: 0; }
    .team img { width: 32px; height: 32px; }
    .team .nm { font-size: 15px; }
    /* Let the two teams stack. Left to wrap on their own, a long name
       pushes the "at" up onto the away team's line, where it reads as
       part of that name instead of the thing separating them. */
    .teams { flex-direction: column; align-items: flex-start; gap: 6px; }
    .vs { padding-left: 6px; }
  }
  .up { color: #7ecf84; } .down { color: #ec8478; } .flat { color: var(--dim); }
  .lean.gone { opacity: .55; }
  .dropped { margin-top: 26px; }
  .dropped h3 {
    font-size: 11px; letter-spacing: .16em; text-transform: uppercase;
    color: var(--dim); margin: 0 0 6px;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }

  /* ------------------------------------------------ the explainer */
  .edge p { max-width: 64ch; }
  .scale {
    display: flex; gap: 0; margin: 18px 0 8px; flex-wrap: wrap;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .rung { padding: 8px 18px; border-left: 2px solid #22314a; }
  .rung:first-child { border-left: 0; padding-left: 0; }
  .rung .v { font-size: 21px; font-weight: 800; color: var(--gold-2);
             font-variant-numeric: tabular-nums; }
  .rung .k { font-size: 10px; letter-spacing: .12em; text-transform: uppercase;
             color: var(--dim); margin-top: 2px; }
  .rung.bar .v { color: var(--cream); }

  /* ------------------------------------------------ the board */
  .game {
    border-bottom: 1px solid #22314a;
  }
  .game summary {
    list-style: none; cursor: pointer; display: flex; align-items: center;
    gap: 14px; padding: 14px 4px; font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .game summary::-webkit-details-marker { display: none; }
  .game summary:hover { background: rgba(201,169,97,.05); }
  .glogo { width: 34px; height: 34px; object-fit: contain; flex: none; }
  .gteams {
    flex: 1 1 auto; min-width: 0; display: flex; align-items: center;
    gap: 8px; flex-wrap: wrap;
  }
  .gside { display: inline-flex; align-items: center; gap: 8px; }
  .gteams .away, .gteams .home { font-size: 15px; font-weight: 600; }
  .gat { color: var(--dim); font-size: 12px; padding: 0 2px; }
  .gnums {
    text-align: right; font-variant-numeric: tabular-nums; flex: none;
    font-size: 14px; color: var(--cream);
  }
  .gnums .lbl { color: var(--dim); font-size: 10px; letter-spacing: .1em;
                text-transform: uppercase; }
  .gkick { color: var(--dim); font-size: 12px; flex: none; width: 86px;
           text-align: right; }
  .gcaret { color: var(--gold); flex: none; transition: transform .15s; }
  .game[open] .gcaret { transform: rotate(90deg); }
  .gbody {
    padding: 4px 6px 18px 54px;
    font-family: ui-sans-serif, -apple-system, sans-serif; font-size: 14px;
  }
  .gbody table { max-width: 460px; margin-bottom: 10px; }
  .gbody td, .gbody th { padding: 6px 10px; }
  .gmove { color: var(--gold-2); font-size: 13px; margin: 6px 0; }
  .glean { color: var(--cream); font-size: 14px; margin: 6px 0 0; }
  .glean b { color: var(--gold-2); }

  /* ------------------------------------------------ the lock */
  .ticket.lock {
    border: 2px solid var(--gold);
    box-shadow: 0 0 0 4px rgba(201,169,97,.15), 0 14px 32px rgba(0,0,0,.5);
  }
  .lockbanner {
    text-align: center; margin: 0 0 6px;
  }
  .lockbanner span {
    display: inline-block; background: var(--gold); color: #1c1508;
    font-family: ui-sans-serif, -apple-system, sans-serif;
    font-size: 12px; font-weight: 800; letter-spacing: .22em;
    text-transform: uppercase; padding: 6px 22px; border-radius: 2px;
  }
  .lockbanner span::before, .lockbanner span::after {
    content: "\2605"; margin: 0 10px; font-size: 10px;
  }

  /* ------------------------------------------------ saturday live */
  .live {
    background: var(--green); border: 1px solid var(--green-2);
    border-radius: 4px; padding: 14px 18px; margin: 40px 0 0;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .live h3 {
    margin: 0 0 8px; color: var(--gold-2); font-size: 12px;
    letter-spacing: .18em; text-transform: uppercase;
  }
  .liverow {
    display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline;
    padding: 6px 0; border-top: 1px solid var(--green-2); font-size: 14px;
  }
  .liverow:first-of-type { border-top: 0; }
  .livescore { font-weight: 800; font-variant-numeric: tabular-nums; }
  .livetag { font-size: 11px; letter-spacing: .1em; text-transform: uppercase; }
  .livetag.covering { color: #8fdc96; }
  .livetag.notcovering { color: #f0a49b; }
  .livetag.tied, .livetag.final { color: var(--dim); }

  footer {
    margin-top: 72px; padding-top: 26px;
    border-top: 1px solid #22314a; color: var(--dim); font-size: 13px;
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  footer .warn { color: var(--cream); margin-top: 14px; max-width: 68ch; }
  @media (max-width: 620px) {
    .wrap { padding: 28px 16px 70px; }
    .stat { padding-right: 18px; margin-right: 18px; }
    .ticket { padding: 22px 20px 18px; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <img src="__LOGO__" alt="__NAME__, __KICKER__">
    <p class="tagline">__TAGLINE__</p>
    <p class="subhead">__SUBHEAD__</p>
    <div class="rule"></div>
  </header>

  <div id="livewrap"></div>

  <h2 id="card-h">The card</h2>
  <div id="card"></div>

  <h2 id="running-h">The running card</h2>
  <div id="running"></div>

  <h2 id="board-h">The board</h2>
  <div id="board"></div>

  <h2 id="book-h">The book</h2>
  <div id="book"></div>

  <h2 id="cal-h">Do my numbers mean anything</h2>
  <div id="cal"></div>

  <h2 id="cum-h">Where you would be</h2>
  <div id="cum"></div>

  <h2 id="wk-h">Week by week</h2>
  <div id="wk"></div>

  <h2 id="split-h">Where it came from</h2>
  <div id="split"></div>

  <h2 id="fac-h">What my reasons have been worth</h2>
  <div id="fac"></div>

  <h2 id="les-h">What I got wrong</h2>
  <div id="les"></div>

  <h2 id="edge-h">What confidence actually means</h2>
  <div id="edge"></div>

  <footer>
    <p id="about" class="warn"></p>
    <div id="prov"></div>
    <p class="warn" id="warn"></p>
  </footer>

</div>
<script>
const DATA = __DATA__;
const V = __VOICE__;

const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const el = id => document.getElementById(id);
const half = n => (Math.round(Number(n) * 2) / 2).toFixed(1);
const num = n => (n == null ? "n/a" : half(n));
const signed = n => (n == null ? "n/a"
  : (Number(n) > 0 ? "+" : "") + half(n));

for (const [id, key] of [["running-h","running_heading"],["edge-h","edge_heading"],
  ["board-h","board_heading"],["card-h","card_heading"],["book-h","book_heading"],
  ["cal-h","calibration_heading"],["cum-h","cumulative_heading"],
  ["wk-h","weekly_heading"],["split-h","split_heading"],
  ["fac-h","factors_heading"],["les-h","lessons_heading"]]) {
  if (el(id)) el(id).textContent = V[key];
}

function say(text, cls) {
  return `<p class="${cls || "said"}">${esc(text)}</p>`;
}

// The week is quoted in Eastern everywhere on this page, so the day has
// to be Eastern too. A reader in Los Angeles at 9pm Thursday is not owed
// Friday's card, and one in London at 2am Friday is not owed it either.
// If the lookup fails, show: a section wrongly visible is a smaller
// failure than a card wrongly hidden.
function cardWindow() {
  try {
    const day = new Date().toLocaleDateString("en-US",
      { timeZone: "America/New_York", weekday: "short" });
    return ["Fri", "Sat", "Sun"].includes(day);
  } catch (e) {
    return true;
  }
}

function hide(id) {
  for (const e of [el(id + "-h"), el(id)]) if (e) e.style.display = "none";
}

/* ---------------------------------------------------------- the card */
function ticket(p) {
  const r = p.result || "pending";
  const settled = ["win","loss","push"].includes(r);
  const badge = settled
    ? `<span class="badge ${r}">${esc(p.result_label)} ${signed(p.units_net)}u</span>`
    : `<span class="badge pending">${esc(V.card_pending)}</span>`;
  const srcs = (p.sources || []).filter(s => s && s.url).map(s =>
    `<a href="${esc(s.url)}" target="_blank" rel="noopener">${
      esc(s.publisher || new URL(s.url).hostname)}</a>`).join(" &middot; ");
  return `
  <div class="ticket ${settled ? r : ""}">
    <div class="tick-top">
      <span class="play">${esc(p.title)}</span>
      <span class="price">${p.price > 0 ? "+" : ""}${esc(p.price)}</span>
      ${badge}
    </div>
    <div class="matchup">${esc(p.matchup)} &middot; ${esc(p.market)} ${esc(p.period)}</div>
    ${p.rationale ? `<p class="why">${esc(p.rationale)}</p>` : ""}
    ${p.defense && p.defense.text
      ? `<p class="why numbers">${esc(V.pick_numbers)} ${esc(p.defense.text)}</p>`
      : ""}
    <div class="meta">
      <span>Confidence <b>${num(p.confidence, 1)}</b></span>
      <span>Stake <b>${num(p.units, 1)}u</b></span>
      ${p.model_number != null ? `<span>My number <b>${num(p.model_number, 1)}</b></span>` : ""}
      ${p.clv_points != null ? `<span>Closing line value <b>${signed(p.clv_points, 1)}</b></span>` : ""}
      ${p.final_score ? `<span>Final <b>${esc(p.final_score)}</b></span>` : ""}
    </div>
    ${srcs ? `<div class="srcs">${esc(V.sources_heading)}: ${srcs}</div>` : ""}
    <div class="sign">&mdash; ${esc(V.sign_off)}</div>
  </div>`;
}

(function renderCard() {
  const cur = DATA.current || {};
  const live = (DATA.picks || []).filter(p =>
    p.live && p.season === cur.season && p.week === cur.week);
  if (!live.length) {
    if (!cardWindow()) { hide("card"); return; }
    el("card").innerHTML = say(V.card_empty);
    return;
  }
  const staked = live.reduce((a, p) => a + (p.units || 0), 0);
  // The Lock leads. It is computed server side as the most confident
  // pick of the week, and here it only gets the crown and the top slot.
  const lock = live.find(p => p.id === DATA.lock_id);
  const rest = live.filter(p => p.id !== DATA.lock_id);
  const lockHtml = lock
    ? `<div class="lockbanner"><span>${esc(V.lock_title)}</span></div>` +
      ticket(lock).replace('class="ticket', 'class="ticket lock') +
      `<p class="quiet" style="margin:10px 0 4px">${esc(V.lock_note)}</p>`
    : "";
  el("card").innerHTML =
    `<p class="weekline">Season ${esc(cur.season)} &middot; Week ${esc(cur.week)} &middot; ` +
    `${live.length} ${live.length === 1 ? "play" : "plays"} &middot; ${num(staked, 1)} units</p>` +
    (DATA.card_caveat
      ? `<p class="caveat dark">${esc(DATA.card_caveat)}</p>` : "") +
    `<div class="tickets">${lockHtml}${rest.map(ticket).join("")}</div>` +
    (live.length < DATA.target_picks ? say(V.card_short, "quiet") : "") +
    `<p class="quiet" style="margin-top:10px">${esc(V.sources_note)}</p>`;
})();

/* ---------------------------------------------------------- the book */
(function renderBook() {
  const o = DATA.overall || {};
  if (!o.picks) { hide("book"); return; }
  const upLabel = (o.units < 0) ? V.stat_units_down : V.stat_units;
  const stats = [
    [upLabel, signed(o.units), true, o.units],
    [V.stat_record, `${o.wins}-${o.losses}${o.pushes ? "-" + o.pushes : ""}`, false, null],
    [V.stat_winrate, o.win_pct != null ? o.win_pct + "%" : "n/a", false, null],
    [V.stat_roi, o.roi != null ? signed(o.roi, 1) + "%" : "n/a", false, o.roi],
    [V.stat_clv, o.avg_clv != null ? signed(o.avg_clv, 2) : "n/a", false, o.avg_clv],
    [V.stat_close, o.beat_close_pct != null ? o.beat_close_pct + "%" : "n/a", false, null],
    [V.stat_pending, DATA.pending_count, false, null],
  ];
  el("book").innerHTML =
    `<div class="stats">${stats.map(([k, v, big, sign]) => `
      <div class="stat ${big ? "big" : ""}">
        <div class="k">${esc(k)}</div>
        <div class="v ${sign == null ? "" : (sign > 0 ? "pos" : sign < 0 ? "neg" : "")}">${esc(v)}</div>
      </div>`).join("")}</div>` +
    (o.verdict === "no_signal" ? say(V.no_signal_long, "quiet") : "");
})();

/* ------------------------------------------------------- calibration */
(function renderCal() {
  const rows = (DATA.calibration || []).filter(r => r.picks);
  if (!rows.length) { hide("cal"); return; }
  // BREAKEVEN comes through as a percentage, 52.4, not a fraction.
  const be = DATA.breakeven || 52.4;
  const w = 700, h = 34 * rows.length + 30, lab = 92, max = 100;
  const bars = rows.map((r, i) => {
    const y = i * 34 + 8, len = (r.win_pct / max) * (w - lab - 60);
    return `<rect x="${lab}" y="${y}" width="${Math.max(len, 1)}" height="18" rx="2"
              fill="${r.win_pct >= be ? "#4f9a5c" : "#b4463c"}" opacity=".92"/>
            <text x="0" y="${y + 14}" fill="#93a1b8" font-size="12">${esc(r.bucket)}</text>
            <text x="${lab + Math.max(len, 1) + 8}" y="${y + 14}" fill="#f4ecd8" font-size="12">
              ${r.win_pct}% (${r.wins}-${r.losses})</text>`;
  }).join("");
  const bx = lab + (be / max) * (w - lab - 60);
  el("cal").innerHTML = `<div class="chart">
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="win rate by confidence bucket">
      ${bars}
      <line x1="${bx}" y1="0" x2="${bx}" y2="${h - 24}" stroke="#c9a961"
            stroke-width="1.5" stroke-dasharray="4 4"/>
      <text x="${bx + 6}" y="${h - 10}" fill="#c9a961" font-size="11">
        breakeven ${be.toFixed(1)}%</text>
    </svg></div>` +
    (rows.some(r => r.verdict === "no_signal") ? say(V.no_signal_short, "quiet") : "") +
    say(V.calibration_note, "quiet");
})();

/* -------------------------------------------------------- cumulative */
(function renderCum() {
  const pts = DATA.cumulative || [];
  if (pts.length < 2) { hide("cum"); return; }
  const w = 700, h = 200, pad = 28;
  const ys = pts.map(p => p.units);
  const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), span = (hi - lo) || 1;
  const X = i => pad + (i / (pts.length - 1)) * (w - pad * 2);
  const Y = v => h - pad - ((v - lo) / span) * (h - pad * 2);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.units).toFixed(1)}`).join("");
  const last = ys[ys.length - 1];
  el("cum").innerHTML = `<div class="chart">
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="net units over time">
      <line x1="${pad}" y1="${Y(0)}" x2="${w - pad}" y2="${Y(0)}" stroke="#22314a"/>
      <path d="${d}" fill="none" stroke="${last >= 0 ? "#7ecf84" : "#ec8478"}" stroke-width="2.5"/>
      <text x="${w - pad}" y="${Y(last) - 10}" text-anchor="end"
            fill="${last >= 0 ? "#7ecf84" : "#ec8478"}" font-size="13">${signed(last)}u</text>
    </svg></div>` + say(V.cumulative_note, "quiet");
})();

/* ------------------------------------------------------------ tables */
function table(cols, rows, emptyText) {
  if (!rows.length) return say(emptyText);
  return `<table><thead><tr>${cols.map(c => `<th>${esc(c[0])}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
const sigCell = r => r.verdict && r.verdict !== "beats_breakeven"
  && r.verdict !== "below_breakeven"
  ? `<span class="nosig">${esc(V.no_signal)}</span>`
  : esc(r.reading || "");

if ((DATA.weekly || []).length) {
  el("wk").innerHTML = table([
    [V.col_week, r => `${esc(r.season)} wk ${esc(r.week)}`],
    [V.col_plays, r => esc(r.picks)],
    [V.stat_record, r => `${esc(r.wins)}-${esc(r.losses)}`],
    [V.col_units, r => signed(r.units)],
  ], DATA.weekly, V.weekly_empty);
} else hide("wk");

const splitRows = [
  ...(DATA.by_market || []).map(r => ({...r, kind: "Market"})),
  ...(DATA.by_period || []).map(r => ({...r, kind: "Period"})),
];
if (!splitRows.length) hide("split");
else el("split").innerHTML = table([
      [V.col_kind, r => esc(r.kind)],
      [V.col_split, r => esc(r.market || r.period)],
      [V.stat_record, r => `${esc(r.wins)}-${esc(r.losses)}`],
      [V.col_units, r => signed(r.units)],
      [V.col_reading, sigCell],
    ], splitRows, V.split_empty);

if ((DATA.by_factor || []).length) {
  el("fac").innerHTML = table([
    [V.col_reason, r => esc(r.factor)],
    [V.col_plays, r => esc(r.picks)],
    [V.stat_record, r => `${esc(r.wins)}-${esc(r.losses)}`],
    [V.col_units, r => signed(r.units)],
    [V.col_reading, sigCell],
  ], DATA.by_factor, V.factors_empty);
} else hide("fac");

if ((DATA.lessons || []).length) {
  el("les").innerHTML = DATA.lessons.map(l =>
    `<p class="said">${esc(l.lesson)}</p>` +
    `<p class="quiet">Week ${esc(l.week)}, ${esc(l.season)}.</p>`).join("");
} else hide("les");





/* ------------------------------------------------- the running card */
(function renderRunning() {
  const R = DATA.running;
  if (!R || !R.card || !R.card.length) { hide("running"); return; }
  const fmt = c => c.market === "total"
    ? `${esc(c.side)} ${c.line}`
    : `${esc(c.side)} ${c.line > 0 ? "+" : ""}${c.line}`;
  const move = c => {
    if (c.days_tracked <= 1) return `<span class="flat">${esc(V.running_new)}</span>`;
    const d = c.moved_confidence;
    const cls = d > 0 ? "up" : d < 0 ? "down" : "flat";
    const arrow = d > 0 ? "&uarr;" : d < 0 ? "&darr;" : "&rarr;";
    const line = c.moved_line
      ? ` &middot; line ${c.moved_line.from} to ${c.moved_line.to}` : "";
    // Once both figures round to the same half point, "from 7.0" on a
    // row already showing 7.0 claims a move that did not happen.
    const from = num(c.first_confidence) === num(c.confidence)
      ? `<span class="flat">${esc(V.running_held)}</span>`
      : `<span class="${cls}">${arrow} from ${num(c.first_confidence)}</span>`;
    return `${from} <span class="flat">${esc(
      V.running_since.replace("{days}", c.days_tracked))}${line}</span>`;
  };
  const badge = (src, name) => `<div class="team">${
    src ? `<img src="${esc(src)}" alt="${esc(name)}" loading="lazy">`
        : ""}<span class="nm">${esc(name)}</span></div>`;
  const row = (c, gone) => `<div class="lean ${gone ? "gone" : ""}">
    <div class="leanhead">
      <div class="conf"><div class="n">${num(c.confidence, 1)}</div>
        <div class="lbl">conf</div></div>
      <div class="teams">
        ${badge(c.away_logo, c.away_team || c.matchup)}
        <span class="vs">at</span>
        ${badge(c.home_logo, c.home_team || "")}
      </div>
    </div>
    <div class="leanbody">
      <div class="leanplay">${fmt(c)} <span class="price">${
        c.price > 0 ? "+" : ""}${esc(c.price)}</span></div>
      ${c.defense && c.defense.text
        ? `<div class="leandef">${esc(c.defense.text)}</div>` : ""}
      <div class="leanmeta">${esc(c.market)} ${esc(c.period)} &middot; my
        number ${num(c.model_number)}${c.edge_points == null ? "" :
        ` &middot; ${num(Math.abs(c.edge_points))} points apart`}</div>
      <div class="leanmove">${move(c)}</div>
    </div></div>`;
  const dropped = (R.dropped || []).length
    ? `<div class="dropped"><h3>${esc(V.running_dropped_heading)}</h3>` +
      R.dropped.map(c => row(c, true)).join("") +
      say(V.running_dropped_note, "quiet") + `</div>`
    : "";
  const games = new Set(R.card.map(c => c.matchup)).size;
  const stacked = games < R.card.length
    ? say(V.running_stacked.replace("{games}", games), "quiet")
    : "";
  const caveat = R.caveat ? `<p class="caveat">${esc(R.caveat)}</p>` : "";
  const note = V.running_note.replace("{n}",
    R.card.length === 1 ? "one lean" : `${R.card.length} leans`);
  el("running").innerHTML =
    `<p class="quiet" style="margin-bottom:14px">${esc(note)}</p>` +
    caveat + R.card.map(c => row(c, false)).join("") + stacked + dropped;
})();

/* ---------------------------------------------------- the explainer */
(function renderEdge() {
  const sc = DATA.scale || {};
  const fill = t => t
    .replace("{sample}", sc.sample == null ? "the board" : sc.sample)
    .replace("{spread_gap}", sc.spread_gap == null ? "about 3" : num(sc.spread_gap))
    .replace("{total_gap}", sc.total_gap == null ? "about 3" : num(sc.total_gap))
    .replace("{cap}", num(sc.cap))
    .replace("{publish}", num(sc.publish));
  const rungs = [
    ["5.0", "a typical lean"],
    ["6.5", "top tenth of the board"],
    ["7.0", "best thing up there"],
    [num(sc.cap), "my ceiling"],
    [num(sc.publish), "publish line"],
  ];
  el("edge").innerHTML = `<div class="edge">` +
    [1, 2, 3, 4].map(i => say(fill(V["edge_body_" + i]))).join("") +
    `<div class="scale">${rungs.map(([val, lbl], i) => `
      <div class="rung ${i >= 3 ? "bar" : ""}">
        <div class="v">${val}</div>
        <div class="k">${lbl}</div>
      </div>`).join("")}</div>` +
    say(fill(V.edge_body_5)) + say(fill(V.edge_body_6)) + `</div>`;
})();

/* ------------------------------------------------------- the board */
(function renderBoard() {
  const b = DATA.board || {};
  const rows = b.rows || [];
  if (b.stale) {
    el("board").innerHTML = say(
      V.board_stale.replace("{age}", b.age_hours == null ? "unknown" : b.age_hours));
    return;
  }
  if (!rows.length) { el("board").innerHTML = say(V.board_empty); return; }
  const holdNote = (why, copy) => {
    const hits = (b.held || []).filter(h => h.why === why);
    if (!hits.length) return "";
    const LIST = 6;
    const named = hits.slice(0, LIST).map(h => h.matchup).join("; ");
    const rest = hits.length - LIST;
    return say(copy
      .replace("{count}", hits.length === 1 ? "1 game" : hits.length + " games")
      .replace("{games}", rest > 0 ? `${named}, and ${rest} more` : named),
      "quiet");
  };
  const heldNote = holdNote("no_line", V.board_held)
    + holdNote("incoherent", V.board_incoherent);
  // The folded week 1 genuinely spans 2 weekends, so the day alone reads
  // as a sorting bug. The date settles it.
  const kick = iso => {
    try {
      return new Date(iso).toLocaleString(undefined,
        { weekday: "short", month: "numeric", day: "numeric",
          hour: "numeric", minute: "2-digit" });
    } catch (e) { return ""; }
  };
  const logo = (src, alt) => src
    ? `<img class="glogo" src="${esc(src)}" alt="${esc(alt)}" loading="lazy">`
    : `<span class="glogo"></span>`;
  const fmtpt = v => v == null ? "n/a" : signed(v);
  // A spread is stored from the home team's side, negative when the home
  // team lays. On its own that is a number with no subject.
  const fav = (g, sp) => {
    if (sp == null) return "n/a";
    if (Number(sp) === 0) return "pick 'em";
    const who = Number(sp) < 0 ? (g.home_short || g.home_team)
                               : (g.away_short || g.away_team);
    return `${esc(who)} ${num(-Math.abs(Number(sp)))}`;
  };
  const html = rows.map(g => {
    const best = (g.candidates || [])[0];
    const lean = best
      ? `<p class="glean">${esc(V.board_lean)}: <b>${esc(best.side)} ` +
        `${best.market === "total" ? best.bet_line : fmtpt(best.bet_line)}` +
        `</b> &middot; ${num(Math.abs(best.edge_points))} points apart ` +
        `&middot; confidence ${num(best.floor_confidence)}</p>`
      : `<p class="glean">${esc(V.board_no_lean)}</p>`;

    const move = Object.entries(g.movement || {}).map(([k, m]) =>
      `${k} ${fmtpt(m.from)} &rarr; ${fmtpt(m.to)}`).join(", ");
    return `<details class="game">
      <summary>
        <span class="gcaret">&#9656;</span>
        <div class="gteams">
          <span class="gside">${logo(g.away_logo, g.away_team)}
            <span class="away">${esc(g.away_team)}</span></span>
          <span class="gat">at</span>
          <span class="gside">${logo(g.home_logo, g.home_team)}
            <span class="home">${esc(g.home_team)}</span></span>${
            g.neutral_site ? ' <span class="gat">(neutral)</span>' : ""}</div>
        <div class="gnums">
          <div class="lbl">${esc(V.board_col_line)}</div>
          ${fav(g, g.market_spread)} &middot; ${
            g.market_total == null ? "n/a" : num(g.market_total)}
        </div>
        <div class="gkick">${kick(g.kickoff)}</div>
      </summary>
      <div class="gbody">
        <table><thead><tr><th></th>
          <th>${esc(V.board_mkt_spread)}</th><th>${esc(V.board_my_spread)}</th>
          <th>${esc(V.board_mkt_total)}</th><th>${esc(V.board_my_total)}</th>
        </tr></thead><tbody><tr><td></td>
          <td>${fav(g, g.market_spread)}</td>
          <td>${fav(g, g.projected_spread)}</td>
          <td>${g.market_total == null ? "n/a" : num(g.market_total)}</td>
          <td>${g.projected_total == null ? "n/a" : num(g.projected_total)}</td>
        </tr></tbody></table>
        ${move ? `<p class="gmove">${esc(V.board_moved)}: ${move}</p>` : ""}
        ${lean}
      </div>
    </details>`;
  }).join("");
  el("board").innerHTML =
    `<p class="quiet" style="margin-bottom:12px">${esc(V.board_note)}</p>` +
    html + heldNote;
})();

/* -------------------------------------------------- saturday live */
(function renderLive() {
  const L = DATA.live;
  if (!L || !L.rows || !L.rows.length) return;
  const tag = c => {
    if (c === "covering") return `<span class="livetag covering">${esc(V.live_covering)}</span>`;
    if (c === "not covering") return `<span class="livetag notcovering">${esc(V.live_not_covering)}</span>`;
    if (c === "tied") return `<span class="livetag tied">${esc(V.live_tied)}</span>`;
    return "";
  };
  el("livewrap").innerHTML = `<div class="live">
    <h3>${esc(V.live_heading)}</h3>
    ${L.rows.map(r => `<div class="liverow">
      <span>${esc(r.title)}</span>
      <span class="livescore">${esc(r.matchup)}: ${r.away_score}&ndash;${r.home_score}</span>
      ${r.completed ? `<span class="livetag final">${esc(V.live_final)}</span>` : tag(r.covering)}
    </div>`).join("")}
    <p class="quiet" style="margin:10px 0 0">${esc(V.live_note)}</p>
  </div>`;
})();

/* ------------------------------------------------------------ footer */
const when = iso => new Date(iso).toLocaleString(undefined,
  { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
el("prov").textContent = DATA.board_fetched_at
  ? V.provenance
      .replace("{board}", when(DATA.board_fetched_at))
      .replace("{built}", when(DATA.generated_at))
      .replace("{credits}", DATA.credits_remaining == null
        ? "an unknown number of" : DATA.credits_remaining)
  : V.provenance_noboard;
el("about").textContent = V.about;
el("warn").textContent = V.disclaimer;
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    log = RunLog("build_site", dry_run=args.dry_run)
    store.set_dry_run(args.dry_run, log)

    payload = build_payload()
    store.write_json(SITE / "data.json", payload)
    # The character is injected, never hard coded into the markup, so
    # swapping who speaks is a change to VOICE and nothing else.
    html = (HTML
            .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
            .replace("__VOICE__", json.dumps(VOICE, separators=(",", ":")))
            .replace("__NAME__", VOICE["name"])
            .replace("__TAGLINE__", VOICE["tagline"])
            .replace("__SUBHEAD__", VOICE["subhead"])
            .replace("__KICKER__", VOICE["kicker"])
            .replace("__LOGO__", VOICE["logo"])
            .replace("__PAGE_TITLE__", VOICE["page_title"]))
    store.write_text(SITE / "index.html", html)

    print(json.dumps({
        "dry_run": args.dry_run,
        "wrote": [] if args.dry_run else ["site/index.html", "site/data.json"],
        "bytes": len(html),
        "live_picks": sum(1 for p in payload["picks"] if p["live"]),
        "weeks": len(payload["weeks"]),
        "record": payload["overall"],
        "board_stale": payload["board"].get("stale"),
        "board_age_hours": payload["board"].get("age_hours"),
        "games_held_back": [h["matchup"] for h in payload["board"].get("held", [])],
    }, indent=2))
    log.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
