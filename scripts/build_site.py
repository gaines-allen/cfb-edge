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
from lib.store import LIVE_THRESHOLD  # noqa: E402
from lib.teams import canonical, load_logos, normalize, strip_mascot  # noqa: E402
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
    # Describes what the page does rather than promising disclosure it
    # does not make. Every clause here is checkable against the page:
    # the slate goes up, the leans move daily, the card lands Wednesday.
    "tagline": "0-2. Down two units.",
    "subhead": "Hawai'i got buried and NC State scored eight damn points. The board is "
               "innocent. I am currently the idiot.",

    # Openly a persona. A page built on not overclaiming cannot open by
    # implying a real handicapper is behind it, and the joke lands better
    # when it is honest about itself.
    "about": "Steve is the voice of this system, not a person. Nobody's "
             "back there with a cigar. The numbers come from a ratings "
             "model, the reasons come from research that is checked "
             "against its sources before anything is posted, and every "
             "figure about the record is computed from the ledger rather "
             "than typed in by someone having a good week.",

    "page_title": "Steve's Bar Tab | College football picks",
    "running_heading": "Six arguments before kickoff",
    "running_note": "Six leans. Not picks. These are still on the bar, so yell now.",
    "running_stale": "These lines are old. I am loud, not stupid. Come back after the next pull.",
    "running_empty": "Nothing yet. Either the board is late or I finally learned restraint.",
    "running_new": "new today",
    "running_held": "holding",
    "running_since": "tracked {days} days",
    "running_stacked": "Six leans, {games} games. One game snuck in twice. Nice try.",
    "running_dropped_heading": "The book sobered up",
    "running_dropped_note": "I liked these earlier. The line moved. Argument over.",

    # The explainer. Every figure in it comes from the live measurement,
    # so the copy cannot drift from the arithmetic it is describing.
    "edge_heading": "Confidence, since you asked",
    "edge_body_1": "The book has a number. I have one too. The gap is the argument.",
    "edge_body_2": "Totals swing more than spreads, so six points means less on a total. "
                   "I divide the gap by the usual miss for that market.",
    "edge_body_3": "This week the usual miss is {spread_gap} points on spreads and "
                   "{total_gap} on totals across {sample} games. That is one fact said "
                   "twice because the markets move differently.",
    "edge_body_4": "One usual miss gets a 5. Each extra miss adds 1.5. The formula stops at {cap}.",
    "edge_body_5": "A 5 is ordinary. A 6.5 is near the top of this board. A 7 has my attention.",
    "edge_body_6": "The formula stops at {cap}. The publish line is {publish}. Math "
                   "cannot clear it alone. A live bet needs current reporting.",

    "board_heading": "The TV wall",
    "board_note": "My number next to theirs. The loud stuff is up top.",
    "board_empty": "No board. Go bother the bartender.",
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
    "board_open": "Turn on all {count} games",
    "ledger_open": "Open the ugly math",
    "board_moved": "Moved",
    "board_kick": "Kickoff",
    "board_my_spread": "My spread",
    "board_mkt_spread": "Their spread",
    "board_my_total": "My total",
    "board_mkt_total": "Their total",
    "board_col_line": "Favorite / total",
    "board_lean": "Where I lean",
    "board_no_lean": "Priced right. No lean.",

    "lock_title": "The one I yelled loudest",
    "lock_note": "The crown changes the volume. The stake stays put.",

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
    # Shown Wednesday through Sunday, so it has to cover 2 truths at
    # once: before the card lands it is not built yet, and after it lands
    # an empty card means the board was priced right.
    "card_empty": "The card lands Wednesday afternoon and covers Thursday "
                  "night through Sunday. If nothing's here after that, "
                  "the board's priced right and I'm not inventing a play "
                  "to fill the space.",
    "card_short": "Two bets. Both got smoked. This week owes me a chair.",
    "card_pending": "Still running",

    "book_heading": "The damage",
    "book_empty": "Nothing has settled, so there's nothing to brag about "
                  "and nothing to apologize for. Ask me Sunday night.",

    "calibration_heading": "Do my numbers mean anything",
    "calibration_empty": "Nothing has graded. Right now an 8 and a 9 are "
                         "two numbers I wrote down with a lot of confidence "
                         "and no evidence.",
    "calibration_note": "Shadow picks under the publish line are in here on "
                        "purpose. That's how the line gets tested instead "
                        "of assumed. Units on the rows under {publish} are "
                        "pretend, "
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
    "research_heading": "Whether the research is worth anything",
    "research_empty": "Nothing has settled, so there is no way to tell yet.",
    "research_note": "The model stops at 7.5 and the card starts at {publish}, so "
                     "research is the only thing that can put a play up. "
                     "That makes it the layer most worth checking and the "
                     "one easiest to fool, because a determined search "
                     "finds a supporting quote for almost anything. So it "
                     "keeps score too: picks with research against picks "
                     "without, and every outlet I have leaned on.",
    "col_research": "Where it came from",
    "col_gap": "How far apart",
    "col_hit": "Hit rate",
    "factors_empty": "This fills in once picks settle. Then we find out "
                     "which of my reasons were reasons and which were "
                     "vibes.",

    "lessons_heading": "What I got wrong",
    "lessons_empty": "Nothing to own up to yet. Give it a week.",

    "no_signal": "Can't tell either way.",
    "verdict_losing": "Loses money.",
    "verdict_winning": "Makes money.",
    "no_signal_long": "Two games prove almost nothing. The 95 percent range still crosses "
                      "the 52.4 percent break-even line. Call it 52.5 with a dull pencil. "
                      "I could still be lucky. Good has not made the trip yet. That "
                      "caveat belongs down here, where it cannot hide the bill.",
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

    "pick_line_moved": "Booked at {taken}. The number is {now} now, so "
                       "this is graded at the price it went up at, not "
                       "the one you are looking at.",
    "pick_numbers": "Here's the math I did before I liked it.",
    "sources_heading": "Receipts",
    "sources_note": "Every live pick has a current source. Open the receipts if you want "
                    "the paperwork.",

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


def same_side(a, b) -> bool:
    """
    Whether two side labels name the same bet.

    The ledger stores the odds board's full name, "NC State Wolfpack", and
    a candidate stores the model's short one, "NC State". An exact match
    silently found nothing, which is why the card showed no model number
    and could not tell its line had moved 9 points.

    Resolved through canonical(), the same mascot map the logos use, not
    by prefix. A prefix rule accepts "NC State Wolfpack" and also accepts
    "Ohio" for "Ohio State", which are different schools. That is the
    exact failure teams.py refuses to allow, and it does not become safe
    for being written somewhere else. Over and Under match as themselves.
    """
    if a is None or b is None:
        return False
    if a == b:
        return True
    known = set(load_logos())
    ca, cb = canonical(str(a), known), canonical(str(b), known)
    return bool(ca) and ca == cb


def board_line_now(slate_by_event: dict, p: dict):
    """
    Today's number for a pick, when it is not the number that was taken.

    NC State went up at +4.5 on 28 August and the board was +13.5 by the
    29th. Both true, 9 points apart, sitting on the same page with nothing
    saying one was the entry price. Returns None when they agree, so the
    page only speaks up when there is something to explain.
    """
    g = slate_by_event.get(p.get("event_id"))
    if not g or p.get("line") is None:
        return None
    cand = next((c for c in g.get("candidates") or []
                 if c.get("market") == p.get("market")
                 and c.get("period") == p.get("period")
                 and same_side(c.get("side"), p.get("side"))), None)
    now = (cand or {}).get("bet_line")
    if now is None:
        return None
    return None if abs(float(now) - float(p["line"])) < 0.25 else now


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
                 and same_side(c.get("side"), p.get("side"))), None)
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
            "kickoff": e.get("kickoff"),
            "home_team": g.get("home_team"), "away_team": g.get("away_team"),
            # The school without the mascot, so Steve can name the other
            # side in a sentence without saying "West Virginia
            # Mountaineers is laying 21.5". strip_mascot is the same map
            # the logos resolve through, so the short name is never
            # guessed here.
            "home_school": strip_mascot(g.get("home_team") or "") or None,
            "away_school": strip_mascot(g.get("away_team") or "") or None,
            "home_logo": logos.get(
                canonical(g.get("home_team") or "", known_locations) or ""),
            "away_logo": logos.get(
                canonical(g.get("away_team") or "", known_locations) or ""),
            "defense": defend(g.get("model") or {}, e.get("market"),
                              e.get("side"), e.get("line"), e.get("sigma"),
                              e.get("model_number"), e.get("edge_points")),
        }

    # A lean on a game that has kicked is not a lean, it is a memory.
    # Best bets carried NC State at Virginia for 18 hours after kickoff
    # and Memphis at UNLV for 11, recommending numbers nobody could take.
    # The board hides a game the moment it starts and this has to match,
    # or the same page tells you a game is unavailable and worth betting.
    def not_started(c):
        import datetime as _dt
        k = c.get("kickoff")
        if not k:
            return True
        try:
            when = _dt.datetime.fromisoformat(str(k).replace("Z", "+00:00"))
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return when > _dt.datetime.now(_dt.timezone.utc)

    # One pick per game, enforced again here rather than trusted. The
    # file is written by another script on another schedule, and the page
    # is the last thing between a duplicate and a reader.
    card, spoken_for = [], set()
    for c in (shape(k) for k in raw["card"]):
        if not c or not not_started(c):
            continue
        key = (c.get("home_team"), c.get("away_team"), c.get("matchup"))
        if key in spoken_for:
            continue
        spoken_for.add(key)
        card.append(c)
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


def fill_publish(text: str) -> str:
    """
    Put the live publish line into the copy.

    The number was typed into 3 sentences and lived in 2 more places in
    code. Lowering it once would have left the page promising a bar it no
    longer holds, which is the fifth time in a week one quantity in two
    places has caused a fault here.
    """
    return text.replace("{publish}", f"{LIVE_THRESHOLD:.1f}")


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

    by_research = [{"factor": name, **b}
                   for name, b in (memory.get("research_scorecard")
                                   or {}).items()]
    by_research.sort(key=lambda r: r.get("units", 0), reverse=True)

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
        "by_research": by_research,
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
                "board_line": board_line_now(slate_by_event, p),
                "live": p.get("live"),
            }
            for p in sorted(picks, key=lambda x: (x.get("kickoff") or ""))
        ],
    }
    # Same treatment the running card gets: the ratings caveat sits once
    # above the card rather than under every ticket.
    payload["proven"] = (payload.get("overall", {}) or {}).get(
        "verdict") == "beats_breakeven"
    payload["voice_publish"] = f"{LIVE_THRESHOLD:.1f}"
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
  .why.moved {
    font-size: 14px; color: #6b5a2e; border-left: 2px solid #b39a55;
    padding-left: 14px; font-family: ui-sans-serif, -apple-system, sans-serif;
  }
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
  .fold { margin: 0 0 34px; }
  .fold > summary {
    list-style: none; cursor: pointer; padding: 12px 0;
    border-top: 1px solid #22314a; border-bottom: 1px solid #22314a;
  }
  .fold > summary::-webkit-details-marker { display: none; }
  .foldlabel {
    font-family: ui-sans-serif, -apple-system, sans-serif; font-size: 12px;
    letter-spacing: .16em; text-transform: uppercase; color: var(--gold-2);
  }
  .foldlabel::before { content: "+ "; }
  .fold[open] > summary .foldlabel::before { content: "\2212 "; }
  .fold > summary:hover { background: rgba(201,169,97,.05); }
  .fold h3 {
    font-size: 20px; margin: 30px 0 10px; color: var(--cream);
  }
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

  /* The data obeys the grid. Steve does not. */
  html { scroll-behavior: smooth; }
  body {
    overflow-x: hidden;
    background:
      radial-gradient(900px 520px at 88% 2%, rgba(128,57,34,.22), transparent 68%),
      linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px),
      var(--navy);
    background-size: auto, 68px 100%, auto;
  }
  body::before {
    content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
    opacity: .12; mix-blend-mode: screen;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.28'/%3E%3C/svg%3E");
  }
  .wrap { max-width: 1220px; padding: 0 38px 110px; }
  .topbar {
    position: sticky; top: 0; z-index: 20; min-height: 54px;
    display: flex; align-items: center; justify-content: space-between; gap: 22px;
    margin: 0 -38px; padding: 0 38px; border-bottom: 1px solid rgba(201,169,97,.24);
    background: rgba(14,26,47,.94); backdrop-filter: blur(12px);
    font: 700 11px/1 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
    letter-spacing: .13em; text-transform: uppercase;
  }
  .bar-name { color: var(--cream); white-space: nowrap; }
  .bar-name i { color: var(--gold-2); font-style: normal; }
  nav { display: flex; align-items: center; gap: clamp(14px, 3vw, 34px); }
  nav a { color: var(--dim); text-decoration: none; padding: 21px 0 18px; }
  nav a:hover, nav a:focus-visible { color: var(--gold-2); }
  header {
    min-height: 590px; display: grid; grid-template-columns: minmax(0, 1.06fr) minmax(320px, .94fr);
    align-items: center; gap: 28px; position: relative; text-align: left; padding: 66px 0 78px;
    border-bottom: 1px solid rgba(201,169,97,.25); overflow: hidden;
  }
  .hero-copy { position: relative; z-index: 2; padding-left: clamp(0px, 3vw, 34px); }
  .eyebrow {
    margin: 0 0 17px; color: var(--gold); font: 800 11px/1.2 ui-sans-serif, sans-serif;
    letter-spacing: .2em; text-transform: uppercase;
  }
  .tagline {
    max-width: 11ch; margin: 0; font-size: clamp(46px, 6.6vw, 88px); line-height: .91;
    letter-spacing: -.045em; text-wrap: initial;
  }
  .subhead { max-width: 49ch; margin: 28px 0 0; font-size: 16px; line-height: 1.55; }
  .hero-art { position: relative; align-self: stretch; display: grid; place-items: center; }
  header img { width: min(460px, 45vw); transform: rotate(2.2deg); }
  .hero-art::before {
    content: "HE HAS A NUMBER"; position: absolute; right: -34px; top: 56px;
    color: rgba(224,196,137,.1); font: 900 clamp(56px, 8vw, 126px)/.78 ui-sans-serif, sans-serif;
    letter-spacing: -.07em; width: 5ch; transform: rotate(5deg); text-align: right;
  }
  .rule { display: none; }
  .bar-note {
    position: relative; width: min(330px, 80vw); margin: 26px 2% 4px auto;
    padding: 18px 22px; color: #302715; background: var(--gold-2);
    font: 800 16px/1.35 ui-sans-serif, -apple-system, sans-serif;
    transform: rotate(1.2deg); box-shadow: 7px 8px 0 rgba(0,0,0,.22);
  }
  .bar-note::after {
    content: ""; position: absolute; left: 24px; bottom: -14px;
    border: 15px solid transparent; border-top-color: var(--gold-2); border-left: 0;
  }
  h2 {
    display: block; width: max-content; max-width: 100%; margin: 104px 0 24px; padding: 0;
    background: transparent; box-shadow: none; color: var(--cream);
    font: 900 clamp(38px, 5vw, 67px)/.96 ui-sans-serif, -apple-system, sans-serif;
    letter-spacing: -.055em; text-transform: none;
  }
  h2::before { content: ""; display: block; width: 62px; height: 7px; margin: 0 0 13px; background: var(--gold); transform: rotate(-2deg); }
  h2::after { display: none; }
  #card-h { margin-left: 3%; }
  #card, #livewrap { width: min(920px, 92%); margin-left: 3%; }
  #running-h { margin-left: 18%; }
  #running { width: min(860px, 76%); margin-left: 18%; }
  #board-h { margin-left: 1%; }
  #board-alert, #board-fold { width: 100%; }
  #book-h, #book, #ledger-fold { margin-left: 11%; width: min(980px, 86%); }
  #edge-h, #edge { margin-left: 29%; width: min(700px, 68%); }
  .tickets { gap: 38px; }
  .ticket { border-radius: 0; padding: 34px 38px 26px; }
  .ticket:nth-of-type(even) { width: 92%; margin-left: 8%; transform: rotate(.25deg); }
  .ticket.lock { transform: rotate(-.35deg); }
  .badge { border-radius: 2px; }
  .lockbanner { text-align: left; margin: 0 0 13px -18px; transform: rotate(-1deg); }
  .lean { display: grid; grid-template-columns: 1fr; padding: 27px 0 29px; }
  .lean:nth-of-type(2n) { width: 88%; margin-left: 9%; }
  .leanhead { align-items: flex-start; }
  .conf { width: 92px; text-align: left; }
  .conf .n { font-size: 46px; }
  .leanbody { padding-left: 110px; }
  .leanplay { font-size: 24px; }
  .game summary { min-height: 78px; padding: 16px 12px; }
  .game:nth-child(even) { background: rgba(255,255,255,.018); }
  .stats { border-top: 1px solid #26364f; border-bottom: 1px solid #26364f; padding: 24px 0; }
  footer { width: min(900px, 82%); margin-left: auto; }

  @media (max-width: 760px) {
    .wrap { padding: 0 18px 76px; }
    .topbar { margin: 0 -18px; padding: 0 18px; overflow-x: auto; }
    .bar-name { display: none; }
    nav { width: 100%; justify-content: space-between; gap: 16px; }
    nav a { font-size: 10px; }
    header { min-height: auto; grid-template-columns: 1fr; padding: 48px 0 56px; gap: 12px; }
    .hero-copy { padding-left: 0; }
    .tagline { font-size: clamp(43px, 14vw, 70px); max-width: 10ch; }
    .hero-art { min-height: 330px; justify-items: end; }
    header img { width: min(380px, 92vw); }
    .hero-art::before { right: -6px; top: 10px; font-size: 78px; }
    .bar-note { margin-top: 8px; }
    h2 { margin-top: 78px; font-size: clamp(38px, 12vw, 56px); }
    #card-h, #card, #livewrap, #running-h, #running, #board-h, #board-alert,
    #board-fold, #book-h, #book, #ledger-fold, #edge-h, #edge, footer {
      width: 100%; margin-left: 0;
    }
    .ticket, .ticket:nth-of-type(even) { width: 100%; margin-left: 0; padding: 28px 21px 22px; }
    .ticket:nth-of-type(even), .ticket.lock { transform: none; }
    .lockbanner { margin-left: 0; }
    .lean:nth-of-type(2n) { width: 100%; margin-left: 0; }
    .leanhead { gap: 8px; }
    .conf { width: 68px; }
    .conf .n { font-size: 38px; }
    .leanbody { padding-left: 76px; }
    .gnums { display: none; }
    .gkick { width: auto; }
    .gbody { padding-left: 4px; overflow-x: auto; }
    table { display: block; max-width: 100%; overflow-x: auto; }
    .fold { max-width: 100%; overflow: hidden; }
    .stat { flex: 1 1 33%; padding: 5px 12px; margin: 0; }
    .stat:first-child { padding-left: 12px; }
  }

  /* Second pass: a scoreboard, a bar tab and six arguments. */
  :root {
    --navy: #090b0e;
    --navy-2: #12161b;
    --green: #151a18;
    --green-2: #303631;
    --cream: #f2e8cf;
    --paper: #eadfbe;
    --ink: #0c0e10;
    --ink-2: #31343a;
    --dim: #9e9a8e;
    --gold: #ff4f24;
    --gold-2: #ff6a44;
    --loss: #ff4f24;
    --win: #77ad75;
  }
  body {
    color: var(--cream);
    background:
      linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px),
      radial-gradient(900px 420px at 78% 0%, rgba(255,79,36,.15), transparent 70%),
      #090b0e;
    background-size: 100% 44px, auto, auto;
  }
  body::before { opacity: .09; }
  .wrap { max-width: 1380px; padding: 0 46px 120px; }
  .topbar {
    min-height: 58px; margin: 0 -46px; padding: 0 46px;
    border-bottom: 4px solid var(--cream); background: rgba(9,11,14,.96);
    backdrop-filter: none;
  }
  .bar-name { font-weight: 950; letter-spacing: .04em; }
  .bar-name i { color: var(--gold); }
  nav a { color: var(--cream); padding: 22px 0 18px; border-bottom: 4px solid transparent; }
  nav a:hover, nav a:focus-visible { color: var(--gold); border-bottom-color: var(--gold); }

  header.scoreboard-hero {
    min-height: 470px; grid-template-columns: minmax(0, 1fr) 190px 270px;
    gap: 0; padding: 50px 0 46px; border-bottom: 8px solid var(--gold);
    overflow: visible;
  }
  .hero-copy { padding: 0; }
  .eyebrow {
    margin: 0 0 14px; color: var(--gold); font-size: 13px; letter-spacing: .16em;
  }
  .tagline {
    max-width: 8.2ch; margin: 0; color: var(--cream);
    font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: clamp(72px, 8.7vw, 132px); font-weight: 900; line-height: .82;
    letter-spacing: -.035em; text-transform: uppercase;
  }
  .subhead {
    max-width: 47ch; margin: 27px 0 0; color: var(--cream);
    font-family: Georgia, "Times New Roman", serif; font-size: 21px; line-height: 1.35;
  }
  .hero-record {
    align-self: center; display: grid; gap: 0; margin: 0 20px 0 8px;
    padding: 19px 14px 14px; color: #090b0e; background: var(--gold);
    border: 4px solid var(--cream); box-shadow: 8px 8px 0 #000;
    font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    text-align: center; transform: rotate(1.4deg);
  }
  .hero-record span:first-child { font-size: 72px; line-height: .85; }
  .hero-record span:last-child { margin-top: 11px; font-size: 29px; line-height: 1; }
  .hero-art { min-width: 0; align-self: center; justify-items: end; }
  .hero-art::before { display: none; }
  header img { width: min(300px, 24vw); transform: rotate(3deg); }

  h2, #card-h, #running-h, #board-h, #book-h, #edge-h {
    width: auto; max-width: none; margin: 110px 0 28px; padding: 17px 0 0;
    color: var(--cream); background: transparent; border-top: 8px solid var(--cream);
    font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: clamp(54px, 7vw, 96px); line-height: .88; letter-spacing: -.025em;
    text-transform: uppercase; box-shadow: none;
    scroll-margin-top: 84px;
  }
  h2::before, h2::after { display: none; }
  .weekline {
    margin: 0 0 16px; color: var(--gold); font-weight: 900; font-size: 13px;
  }
  #card, #livewrap { width: 100%; margin: 0; }
  #running-h { width: 82%; margin-left: 12%; }
  #running { width: 88%; margin-left: 8%; display: grid; grid-template-columns: 1fr 1fr; column-gap: 58px; }
  #board-h, #board-alert, #board-fold { width: 100%; margin-left: 0; }
  #book-h, #book { width: 100%; margin-left: 0; }

  .tickets { gap: 58px; }
  .ticket, .ticket:nth-of-type(even) {
    width: 100%; margin: 0; border-radius: 0; transform: none;
  }
  .ticket.lock {
    padding: 46px 50px 38px; color: var(--ink); border: 7px solid var(--gold);
    background-color: var(--paper);
    background-image:
      repeating-linear-gradient(0deg, transparent 0 31px, rgba(12,14,16,.055) 31px 32px),
      radial-gradient(circle at 84% 13%, rgba(99,69,22,.10) 0 7%, transparent 7.5%);
    box-shadow: 14px 14px 0 rgba(0,0,0,.58); transform: rotate(-.18deg);
  }
  .ticket.lock::after { height: 0; }
  .ticket:not(.lock) {
    width: 82%; margin-left: 13%; padding: 38px 0 15px; color: var(--cream);
    background: transparent; border: 0; border-top: 5px solid var(--cream);
    box-shadow: none;
  }
  .ticket:not(.lock)::before, .ticket:not(.lock)::after { display: none; }
  .lockbanner {
    width: max-content; margin: -68px 0 26px -24px; padding: 8px 14px;
    color: #090b0e; background: var(--gold); font: 950 12px/1 ui-sans-serif, sans-serif;
    letter-spacing: .14em; text-align: left; text-transform: uppercase; transform: rotate(-1.4deg);
  }
  .tick-top { align-items: flex-start; gap: 18px; }
  .play {
    flex: 1 1 520px; color: var(--ink); font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: clamp(57px, 7vw, 102px); font-weight: 900; line-height: .88;
    letter-spacing: -.02em; text-transform: uppercase;
  }
  .ticket:not(.lock) .play { color: var(--cream); font-size: clamp(43px, 5vw, 70px); }
  .result-stamp {
    flex: none; padding: 9px 12px 7px; color: var(--cream); background: var(--ink);
    font: 950 16px/1 ui-sans-serif, sans-serif; letter-spacing: .08em; text-transform: uppercase;
    transform: rotate(1deg);
  }
  .result-stamp.loss { background: var(--loss); color: #090b0e; }
  .ticket:not(.lock) .result-stamp { color: #090b0e; background: var(--gold); }
  .matchup {
    margin: 15px 0 22px; color: #59564d; font-size: 12px; font-weight: 800;
  }
  .ticket:not(.lock) .matchup { color: var(--dim); }
  .why {
    max-width: 47ch; margin: 0 0 26px; color: var(--ink);
    font-family: Georgia, "Times New Roman", serif; font-size: 25px; line-height: 1.35;
  }
  .ticket:not(.lock) .why { max-width: 53ch; color: var(--cream); font-size: 21px; }
  .pick-facts {
    display: flex; flex-wrap: wrap; gap: 8px 25px; padding-top: 15px;
    border-top: 2px solid rgba(12,14,16,.24); color: #514e46;
    font: 13px/1.4 ui-sans-serif, sans-serif; font-variant-numeric: tabular-nums;
  }
  .ticket:not(.lock) .pick-facts { color: var(--dim); border-top-color: #34383e; }
  .pick-facts b { color: inherit; }
  .srcs {
    margin-top: 15px; color: #56534b; font: 12px/1.6 ui-sans-serif, sans-serif;
  }
  .srcs summary { width: max-content; cursor: pointer; color: inherit; font-weight: 900; text-transform: uppercase; }
  .srcs div { margin-top: 7px; }
  .srcs a { color: inherit; border-bottom-color: currentColor; }
  .ticket:not(.lock) .srcs { color: var(--dim); }
  .card-afterword { margin: 22px 0 0 13%; color: var(--gold); font: 800 14px/1.4 ui-sans-serif, sans-serif; }

  .lean, .lean:nth-of-type(2n) {
    width: 100%; margin: 0; padding: 27px 0 34px; display: grid;
    grid-template-columns: 62px minmax(0, 1fr); gap: 17px; border-top: 4px solid var(--cream);
    border-bottom: 0; font-family: ui-sans-serif, sans-serif;
  }
  .lean:nth-child(3), .lean:nth-child(4) { margin-top: 38px; }
  .lean:nth-child(5), .lean:nth-child(6) { margin-top: 38px; }
  .lean-index {
    color: var(--gold); font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: 50px; line-height: .9;
  }
  .lean-argument { min-width: 0; }
  .teams { flex-direction: row; align-items: center; gap: 9px; }
  .team { gap: 7px; }
  .team img { width: 28px; height: 28px; }
  .team .nm { font-size: 12px; line-height: 1.15; color: var(--dim); }
  .vs { padding: 0; font-size: 9px; }
  .leanplay {
    margin: 17px 0 0; color: var(--cream); font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: clamp(31px, 3.2vw, 47px); line-height: .92; text-transform: uppercase;
  }
  .leanplay .price { color: var(--gold); font: 800 13px/1 ui-sans-serif, sans-serif; }
  .leandef { max-width: 38ch; margin: 13px 0 0; color: var(--cream); font: 18px/1.42 Georgia, serif; }
  .leanmeta { margin-top: 13px; color: var(--dim); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }

  #board-fold {
    border: 4px solid var(--cream); background: rgba(9,11,14,.84);
  }
  #board-fold > summary { padding: 18px 22px; border: 0; background: var(--cream); }
  #board-fold .foldlabel { color: var(--ink); font-weight: 950; }
  #board { padding: 0 20px 16px; }
  .game { border-bottom: 2px solid #34383e; }
  .game summary { min-height: 70px; padding: 13px 4px; }
  .game summary:hover { background: rgba(255,79,36,.08); }
  .gcaret, .glean b, .gmove { color: var(--gold); }
  .gnums .lbl, .gkick { color: var(--dim); }

  .damage-copy {
    max-width: 21ch; margin: 0; padding: 35px 0 42px; color: var(--gold);
    border-bottom: 8px solid var(--gold);
    font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: clamp(55px, 7.3vw, 104px); line-height: .91; letter-spacing: -.02em;
    text-transform: uppercase;
  }

  /* The tab. A scoreboard, not a set of cards: hard rules across the top
     and bottom, one line per settled ticket, and the number that hurt sat
     on the right where a bar tab puts it. */
  .tab {
    margin: 0; border-bottom: 4px solid var(--cream);
    font-family: ui-sans-serif, -apple-system, sans-serif;
  }
  .tab-row {
    display: grid; align-items: baseline; gap: 4px 20px;
    grid-template-columns: minmax(0, 1fr) auto 82px 92px;
    padding: 19px 0; border-top: 2px solid #34383e;
    font-variant-numeric: tabular-nums;
  }
  .tab-row:first-child { border-top: 4px solid var(--cream); }
  .tab-play {
    min-width: 0; color: var(--cream);
    font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: clamp(23px, 2.4vw, 35px); line-height: 1;
    letter-spacing: -.01em; text-transform: uppercase;
  }
  .tab-score { color: var(--dim); font-size: 13px; }
  .tab-res {
    font-size: 13px; font-weight: 900; letter-spacing: .1em;
    text-transform: uppercase;
  }
  .tab-units { font-size: 25px; font-weight: 900; text-align: right; }
  .tab-row.loss .tab-res, .tab-row.loss .tab-units { color: var(--gold); }
  .tab-row.win .tab-res, .tab-row.win .tab-units { color: var(--win); }
  .tab-row.push .tab-res, .tab-row.push .tab-units { color: var(--dim); }
  .tab-row.total { border-top: 4px solid var(--cream); }
  .tab-row.total .tab-play {
    color: var(--dim); font-family: ui-sans-serif, -apple-system, sans-serif;
    font-size: 12px; font-weight: 900; letter-spacing: .16em;
    text-transform: uppercase;
  }
  .tab-row.total .tab-res { color: var(--cream); }
  .tab-row.total .tab-units { color: var(--gold); font-size: 31px; }

  .method {
    width: 94%; margin: 130px 0 0 3%; border: 6px solid var(--gold); background: #0f1216;
  }
  .method > summary {
    list-style: none; cursor: pointer; padding: 26px 30px; color: #090b0e; background: var(--gold);
    font-family: Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif;
    font-size: clamp(31px, 4vw, 54px); line-height: .95; text-transform: uppercase;
  }
  .method > summary::-webkit-details-marker { display: none; }
  .method > summary::before { content: "+ "; }
  .method[open] > summary::before { content: "- "; }
  .method-body { padding: 4px 34px 38px; }
  .method #edge-h {
    margin: 58px 0 25px; padding-top: 13px; border-top-width: 5px;
    font-size: clamp(42px, 5vw, 68px);
  }
  .method #edge, .method #ledger-fold, .method footer { width: 100%; margin-left: 0; }
  .method h3 {
    margin: 54px 0 14px; color: var(--cream); font: 900 25px/1.05 ui-sans-serif, sans-serif;
    letter-spacing: -.02em;
  }
  .method-copy {
    max-width: 64ch; margin: 35px 0 0; padding: 18px 0;
    border-top: 2px solid #383d44; border-bottom: 2px solid #383d44;
    color: var(--dim); font: 14px/1.5 ui-sans-serif, sans-serif;
  }
  .edge p { font-size: 17px; }
  .scale { border-top: 2px solid #383d44; border-bottom: 2px solid #383d44; padding: 15px 0; }
  .stats { margin-top: 10px; }
  footer { width: 100%; margin: 75px 0 0; }

  @media (max-width: 900px) {
    header.scoreboard-hero { grid-template-columns: minmax(0, 1fr) 160px; }
    .hero-art { display: none; }
    .hero-record span:first-child { font-size: 60px; }
    #running-h, #running { width: 100%; margin-left: 0; }
    #running { column-gap: 34px; }
    .ticket:not(.lock) { width: 92%; margin-left: 8%; }
  }
  @media (max-width: 680px) {
    .wrap { padding: 0 18px 80px; }
    .topbar { margin: 0 -18px; padding: 0 18px; min-height: 54px; overflow-x: auto; }
    nav { justify-content: flex-start; min-width: max-content; gap: 20px; }
    nav a { font-size: 10px; }
    header.scoreboard-hero {
      min-height: 0; grid-template-columns: 1fr; gap: 24px; padding: 42px 0 45px;
    }
    .tagline { max-width: 7.5ch; font-size: clamp(60px, 21vw, 86px); }
    .subhead { font-size: 18px; }
    .hero-record { width: 138px; margin: 0 0 0 auto; padding: 14px 10px 10px; }
    .hero-record span:first-child { font-size: 54px; }
    .hero-record span:last-child { font-size: 23px; }
    h2, #card-h, #running-h, #board-h, #book-h {
      margin-top: 82px; padding-top: 12px; border-top-width: 6px;
      font-size: clamp(49px, 17vw, 72px);
    }
    .ticket.lock { padding: 38px 20px 26px; border-width: 5px; box-shadow: 8px 8px 0 #000; }
    .lockbanner { margin: -57px 0 22px -9px; }
    .play, .ticket:not(.lock) .play { font-size: clamp(42px, 14vw, 60px); }
    .result-stamp { font-size: 13px; }
    .why, .ticket:not(.lock) .why { font-size: 19px; }
    .ticket:not(.lock) { width: 100%; margin-left: 0; }
    .pick-facts { display: grid; gap: 6px; }
    .card-afterword { margin-left: 0; }
    #running { grid-template-columns: 1fr; }
    .lean:nth-child(n) { margin-top: 0; }
    .lean { grid-template-columns: 48px minmax(0, 1fr); gap: 12px; padding-bottom: 28px; }
    .lean-index { font-size: 40px; }
    /* At this width the row wraps and strands the "at" beside the away
       team, sitting high off the baseline. Stack the three instead, so
       the matchup reads down the column the way it reads across on a
       wider screen. */
    .teams { flex-direction: column; align-items: flex-start; gap: 5px; }
    .vs { padding: 0; font-size: 9px; line-height: 1; }
    .team img { width: 24px; height: 24px; }
    .team .nm { font-size: 11px; }
    .leanplay { font-size: 34px; }
    .leandef { font-size: 17px; }
    #board { padding: 0 10px 12px; }
    .gnums { display: none; }
    .gkick { width: auto; }
    .gbody { padding-left: 2px; overflow-x: auto; }
    .method { width: 100%; margin-left: 0; border-width: 4px; }
    .method > summary { padding: 21px 18px; font-size: 34px; }
    .method-body { padding: 2px 16px 28px; }
    .tab-row { grid-template-columns: minmax(0, 1fr) auto; gap: 5px 14px; padding: 16px 0; }
    .tab-play { grid-column: 1 / -1; font-size: 31px; }
    .tab-score { grid-column: 1; font-size: 12px; }
    .tab-res { grid-column: 1; }
    .tab-units { grid-column: 2; grid-row: 2 / 4; align-self: center; font-size: 23px; }
    .tab-row.total .tab-units { font-size: 27px; }
    .rung { flex: 1 1 33%; padding: 8px 10px; }
    .stats { gap: 18px 0; }
    .stat, .stat:first-child { flex: 1 1 50%; padding: 5px 10px; }
    table { display: block; max-width: 100%; overflow-x: auto; }
  }
</style>
</head>
<body>
<div class="wrap">

  <div class="topbar">
    <div class="bar-name"><i>Steve's</i> College Football Bar</div>
    <nav aria-label="Jump to a section">
      <a href="#card-h">The card</a>
      <a href="#running-h">Arguments</a>
      <a href="#board-h">TV wall</a>
      <a href="#book-h">Damage</a>
    </nav>
  </div>

  <header class="scoreboard-hero">
    <div class="hero-copy">
      <p class="eyebrow">Week one &middot; final</p>
      <h1 class="tagline">__TAGLINE__</h1>
      <p class="subhead">__SUBHEAD__</p>
    </div>
    <div class="hero-record" aria-label="Current record">
      <span id="hero-record">0-2</span>
      <span id="hero-units">-2.0u</span>
    </div>
    <div class="hero-art">
      <img src="__LOGO__" alt="__NAME__, __KICKER__">
    </div>
  </header>

  <div id="livewrap"></div>

  <h2 id="card-h">The card</h2>
  <div id="card"></div>

  <h2 id="running-h">Six arguments before kickoff</h2>
  <div id="running"></div>

  <h2 id="board-h">The TV wall</h2>
  <div id="board-alert"></div>
  <details class="fold" id="board-fold">
    <summary><span class="foldlabel">__BOARD_OPEN__</span></summary>
    <div id="board"></div>
  </details>

  <h2 id="book-h">The damage</h2>
  <div id="book"></div>

  <details class="method" id="method-fold">
    <summary>Fine. Here's how the damn thing works.</summary>
    <div class="method-body">

    <p class="method-copy" id="source-policy"></p>

    <h3>All the ugly numbers</h3>
    <div id="record-details"></div>

    <div id="ledger-fold">

    <h3 id="cal-h">Do my numbers mean anything</h3>
    <div id="cal"></div>

    <h3 id="cum-h">Where you would be</h3>
    <div id="cum"></div>

    <h3 id="wk-h">Week by week</h3>
    <div id="wk"></div>

    <h3 id="split-h">Where it came from</h3>
    <div id="split"></div>

    <h3 id="fac-h">What my reasons have been worth</h3>
    <div id="fac"></div>

    <h3 id="res-h">Whether the research is worth anything</h3>
    <div id="res"></div>


    <h3 id="les-h">What I got wrong</h3>
    <div id="les"></div>
    </div>

    <h2 id="edge-h">Confidence, since you asked</h2>
    <div id="edge"></div>

    <footer>
      <p id="about" class="warn"></p>
      <div id="prov"></div>
      <p class="warn" id="warn"></p>
    </footer>
    </div>
  </details>

</div>
<script>
const DATA = __DATA__;
const V = __VOICE__;

const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const el = id => document.getElementById(id);
const MAX_BOARD_AGE_HOURS = __MAX_BOARD_AGE__;

// Age measured against the reader's clock, not the build's.
//
// DATA.board.stale was decided in Python when the page was made, and a
// page is only made when the job succeeds. So a job that stops running
// leaves the last good page up forever, reporting 0 hours on lines that
// could be a week old. The one case the check exists for is the one case
// it could not see.
function boardAgeHours() {
  const at = (DATA.board || {}).fetched_at;
  if (!at) return null;
  const t = Date.parse(at);
  return Number.isNaN(t) ? null : (Date.now() - t) / 3600000;
}
function boardStale() {
  const age = boardAgeHours();
  return Boolean((DATA.board || {}).stale) || age == null
    || age > MAX_BOARD_AGE_HOURS;
}
const half = n => (Math.round(Number(n) * 2) / 2).toFixed(1);
const num = n => (n == null ? "n/a" : half(n));
const signed = n => (n == null ? "n/a"
  : (Number(n) > 0 ? "+" : "") + half(n));

for (const [id, key] of [["running-h","running_heading"],["edge-h","edge_heading"],
  ["board-h","board_heading"],["card-h","card_heading"],["book-h","book_heading"],
  ["cal-h","calibration_heading"],["cum-h","cumulative_heading"],
  ["wk-h","weekly_heading"],["split-h","split_heading"],
  ["fac-h","factors_heading"],["res-h","research_heading"],
  ["les-h","lessons_heading"]]) {
  if (el(id)) el(id).textContent = V[key];
}

function say(text, cls) {
  return `<p class="${cls || "said"}">${esc(text)}</p>`;
}

// The week is quoted in Eastern everywhere on this page, so the day has
// to be Eastern too. A reader in Los Angeles at 9pm Tuesday is not owed
// Wednesday's card, and one in London at 2am Wednesday is not either.
// If the lookup fails, show: a section wrongly visible is a smaller
// failure than a card wrongly hidden.
function cardWindow() {
  try {
    const day = new Date().toLocaleDateString("en-US",
      { timeZone: "America/New_York", weekday: "short" });
    return ["Wed", "Thu", "Fri", "Sat", "Sun"].includes(day);
  } catch (e) {
    return true;
  }
}

function hide(id) {
  for (const e of [el(id + "-h"), el(id)]) if (e) e.style.display = "none";
}

/* ---------------------------------------------------------- the card */
const PICK_TAKES = {
  "5afedb05e61f": "Stanford laid 4.5 with a quarterback whose right knee had played as much football as I had for 20 months. Hawai'i had Micah Alejado, the same staff, and 4.5 points in its pocket. I took the Warriors. They lost by 10. Put that one on my tab.",
  "5273616f44e1": "Virginia put 16 players on the out list. Four were receivers. So was the kicker. NC State had CJ Bailey and 4.5 points. I took it. The Wolfpack scored eight damn points and lost by 26. That ticket got smoked.",
};

function ticket(p, featured) {
  const r = p.result || "pending";
  const settled = ["win","loss","push"].includes(r);
  const result = settled
    ? `${esc(p.result_label)} ${signed(p.units_net)}u`
    : esc(V.card_pending);
  const srcs = (p.sources || []).filter(s => s && s.url).map(s =>
    `<a href="${esc(s.url)}" target="_blank" rel="noopener">${
      esc(s.publisher || new URL(s.url).hostname)}</a>`).join(" &middot; ");
  const facts = [
    `Confidence <b>${num(p.confidence)}</b>`,
    `Stake <b>${num(p.units)}u</b>`,
    p.model_number == null ? "" : `My number <b>${num(p.model_number)}</b>`,
    p.board_line == null ? "" : `Now <b>${p.market === "Total" ? num(p.board_line) : signed(p.board_line)}</b>`,
    p.clv_points == null ? "" : `CLV <b>${signed(p.clv_points)}</b>`,
    p.final_score ? `Final <b>${esc(p.final_score)}</b>` : "",
  ].filter(Boolean);
  const take = PICK_TAKES[p.id] || (p.defense && p.defense.text
    ? p.defense.text : p.rationale) || "I took the number. The number can defend itself.";
  const modelCaseLabel = p.defense && p.defense.text ? V.pick_numbers : "";
  return `
  <article class="ticket ${featured ? "lock " : ""}${settled ? r : ""}"
           data-model-case-label="${esc(modelCaseLabel)}">
    ${featured ? `<div class="lockbanner">${esc(V.lock_title)}</div>` : ""}
    <div class="tick-top">
      <span class="play">${esc(p.title)}</span>
      <span class="result-stamp ${r}">${result}</span>
    </div>
    <div class="matchup">${esc(p.matchup)} &middot; ${esc(p.market)} ${esc(p.period)} &middot; ${
      p.price > 0 ? "+" : ""}${esc(p.price)}</div>
    <p class="why">${esc(take)}</p>
    <div class="pick-facts">${facts.map(f => `<span>${f}</span>`).join("")}</div>
    ${srcs ? `<details class="srcs"><summary>${esc(V.sources_heading)}</summary><div>${srcs}</div></details>` : ""}
  </article>`;
}

(function renderCard() {
  const cur = DATA.current || {};
  // The card is what is coming, plus the weekend just gone while its
  // results are still the story. The week number alone cannot do this.
  // CFBD week 1 runs 29 August to 8 September, so 2 separate weekends
  // share it, and on Wednesday the card was still presenting the
  // previous Saturday's settled losses as this week's plays.
  //
  // 48 hours past kickoff keeps a Saturday card up through Sunday, when
  // its results are what people came for, and drops it by Monday, when
  // the next one starts being built. The record keeps every pick
  // forever regardless. This is only what the card section shows.
  const CARD_TAIL_HOURS = 48;
  const stillCurrent = p => {
    if (!p.kickoff) return true;
    const ko = Date.parse(p.kickoff);
    if (Number.isNaN(ko)) return true;
    return (Date.now() - ko) / 36e5 < CARD_TAIL_HOURS;
  };
  const live = (DATA.picks || []).filter(p =>
    p.live && p.season === cur.season && p.week === cur.week
    && stillCurrent(p));
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
  const lockHtml = lock ? ticket(lock, true) : "";
  el("card").innerHTML =
    `<p class="weekline">Season ${esc(cur.season)} &middot; Week ${esc(cur.week)} &middot; ` +
    `${live.length} ${live.length === 1 ? "play" : "plays"} &middot; ${
      num(staked)} units</p>` +
    `<div class="tickets">${lockHtml}${rest.map(p => ticket(p, false)).join("")}</div>` +
    (live.length < DATA.target_picks ? say(V.card_short, "card-afterword") : "");
})();

/* ---------------------------------------------------------- the book */
(function renderBook() {
  const o = DATA.overall || {};
  if (!o.picks) { hide("book"); return; }
  const record = `${o.wins}-${o.losses}${o.pushes ? "-" + o.pushes : ""}`;
  if (el("hero-record")) el("hero-record").textContent = record;
  if (el("hero-units")) el("hero-units").textContent = `${signed(o.units)}u`;
  // The headline here used to be the hero sentence, printed a second time
  // at twice the size, behind a hardcoded test for the 0-2 record. So the
  // loudest repetition on the page was the page repeating itself, and the
  // line was going to quietly turn into something else the moment a third
  // pick settled. This reads off the ledger instead, and says the part the
  // hero does not: what is on the tab.
  const damage = !o.wins
    ? `${record}. Nothing cashed. Here is the tab.`
    : o.units < 0
      ? `${record}. ${num(Math.abs(o.units))} units gone. Here is the tab.`
      : o.units > 0
        ? `${record}. Up ${num(o.units)}. The tab is finally on the other side.`
        : `${record}. Dead even. Here is the tab.`;
  const settled = (DATA.picks || []).filter(p =>
    p.live && (p.result === "win" || p.result === "loss" || p.result === "push"));
  const tab = settled.length ? `<div class="tab">` + settled.map(p => `
      <div class="tab-row ${esc(p.result)}">
        <div class="tab-play">${esc(p.title)}</div>
        <div class="tab-score">${p.final_score ? esc(p.final_score) : ""}</div>
        <div class="tab-res">${esc(p.result_label)}</div>
        <div class="tab-units">${signed(p.units_net)}u</div>
      </div>`).join("") + `
      <div class="tab-row total">
        <div class="tab-play">${settled.length} settled</div>
        <div class="tab-score"></div>
        <div class="tab-res">${esc(record)}</div>
        <div class="tab-units">${signed(o.units)}u</div>
      </div></div>` : "";
  el("book").innerHTML = `<p class="damage-copy">${esc(damage)}</p>` + tab;
  const upLabel = (o.units < 0) ? V.stat_units_down : V.stat_units;
  const stats = [
    [upLabel, signed(o.units), true, o.units],
    [V.stat_record, record, false, null],
    [V.stat_winrate, o.win_pct != null ? o.win_pct + "%" : "n/a", false, null],
    [V.stat_roi, o.roi != null ? signed(o.roi, 1) + "%" : "n/a", false, o.roi],
    [V.stat_clv, o.avg_clv != null ? signed(o.avg_clv, 2) : "n/a", false, o.avg_clv],
    [V.stat_close, o.beat_close_pct != null ? o.beat_close_pct + "%" : "n/a", false, null],
    [V.stat_pending, DATA.pending_count, false, null],
  ];
  el("record-details").innerHTML =
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
const sigCell = r => {
  if (r.verdict === "below_breakeven")
    return `<span class="neg">${esc(V.verdict_losing)}</span>`;
  if (r.verdict === "beats_breakeven")
    return `<span class="pos">${esc(V.verdict_winning)}</span>`;
  if (r.verdict) return `<span class="nosig">${esc(V.no_signal)}</span>`;
  return esc(r.reading || "");
};

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

if ((DATA.by_research || []).length) {
  el("res").innerHTML = say(V.research_note, "quiet") + table([
    [V.col_research, r => esc(r.factor)],
    [V.col_plays, r => esc(r.picks)],
    [V.stat_record, r => `${esc(r.wins)}-${esc(r.losses)}`],
    [V.col_units, r => signed(r.units)],
    [V.col_reading, sigCell],
  ], DATA.by_research, V.research_empty);
} else hide("res");

if ((DATA.lessons || []).length) {
  el("les").innerHTML = DATA.lessons.map(l =>
    `<p class="said">${esc(l.lesson)}</p>` +
    `<p class="quiet">Week ${esc(l.week)}, ${esc(l.season)}.</p>`).join("");
} else hide("les");





/* ------------------------------------------------- the running card */
(function renderRunning() {
  const R = DATA.running;
  if (!R || !R.card || !R.card.length) { hide("running"); return; }
  if (boardStale()) {
    el("running").innerHTML = say(V.running_stale);
    return;
  }
  const fmt = c => c.market === "total"
    ? `${esc(c.side)} ${c.line}`
    : `${esc(c.side)} ${c.line > 0 ? "+" : ""}${c.line}`;
  const badge = (src, name) => `<div class="team">${
    src ? `<img src="${esc(src)}" alt="${esc(name)}" loading="lazy">`
        : ""}<span class="nm">${esc(name)}</span></div>`;
  // Steve's case for a lean, built only out of the numbers on its own row.
  //
  // The previous version picked a sentence by card position and wrote the
  // team names into the templates as literals, so slot 2 said "West
  // Virginia" and slot 3 said "Alabama" whatever games were actually
  // sitting there. Reorder the card and it argued the wrong game. Every
  // clause below resolves from the pick. Position only rotates which
  // shape gets used, so six leans in a row do not read like six copies of
  // one sentence.
  //
  // The gap is subtracted from the two numbers actually printed rather
  // than read off edge_points. Those two disagree by a rounding step, and
  // a reader who checks 15.5 against 10.5 and gets told 5.1 has been
  // handed a reason to distrust every other number on the page.
  //
  // The shape rotates by a lean's position among the leans that share its
  // pool, not by its position on the card. Hashing each matchup on its own
  // let two unders collide the first time the card reordered, and the page
  // said "is a lot of scoring to ask these two for" twice in six rows.
  // Counting within the pool cannot collide until there are more leans of
  // one kind than there are ways to say it.
  const poolKey = c => c.market === "total"
    ? (c.side === "Over" ? "over" : "under")
    : (c.line > 0 ? "dog" : "fav");
  const cardSeed = (() => {
    const key = R.card.map(c => c.matchup).join("|");
    let h = 0;
    for (let n = 0; n < key.length; n++) h = (h * 31 + key.charCodeAt(n)) >>> 0;
    return h;
  })();
  const poolOrdinal = (() => {
    const seen = {}, out = new Map();
    for (const c of R.card) {
      const k = poolKey(c);
      seen[k] = seen[k] || 0;
      out.set(c, seen[k]++);
    }
    return out;
  })();
  const shapeFor = (pool, c) =>
    pool[(cardSeed + (poolOrdinal.get(c) || 0)) % pool.length];
  const leanTake = c => {
    if (c.line == null || c.model_number == null) {
      return `I like ${c.side} here and the book has not talked me out of it.`;
    }
    const book = num(Math.abs(c.line));
    const mine = num(Math.abs(c.model_number));
    const gap = half(Math.abs(Number(book) - Number(mine))).replace(/[.]0$/, "");
    const home = c.home_school || c.home_team;
    const away = c.away_school || c.away_team;

    if (c.market === "total") {
      return shapeFor(c.side === "Over" ? [
        `I have ${mine} points in this game. The book stopped at ${book}. Over, before somebody checks the math.`,
        `${away} and ${home} are supposed to stay under ${book}. I make it ${mine}. Over.`,
        `The book is ${gap} points light on this one. My number is ${mine}. Over.`,
      ] : [
        `The book wants ${book} out of ${away} and ${home}. I have ${mine}. Under.`,
        `I make this game ${mine}. They hung ${book}. Somebody is ${gap} points off and it is not me. Under.`,
        `${book} is a lot of scoring to ask these two for. My number says ${mine}. Under.`,
      ], c);
    }

    // A positive line means the side is getting points, so the book's
    // favourite is the other team on the row. The opponent is matched on
    // the mascot-stripped school name, never on a prefix, and a row that
    // will not resolve falls through to a shape that names nobody.
    const foe = c.side === away ? home : (c.side === home ? away : null);
    if (c.line > 0) {
      if (!foe) {
        return `The book is asking ${book} here. I have ${mine}. Give me ${c.side} and the points.`;
      }
      return shapeFor([
        `${foe} is laying ${book}. I make it ${mine}. Give me ${c.side} and the points.`,
        `${book} is a lot of furniture to move. I have ${foe} by ${mine}. ${c.side} plus the points.`,
        `I have ${foe} by ${mine} and the book is asking ${book}. That is ${gap} points somebody left on the bar. Take ${c.side}.`,
      ], c);
    }
    return shapeFor([
      `${c.side} is only laying ${book}. I have them by ${mine}. Lay it.`,
      `The book will sell you ${c.side} at ${book}. My number says ${mine}. Lay the number.`,
      `${gap} points of charity sitting here. They have ${c.side} at ${book}, I have them by ${mine}. Lay it.`,
    ], c);
  };
  const movement = c => c.moved_line
    ? ` &middot; line ${num(c.moved_line.from)} to ${num(c.moved_line.to)}`
    : "";
  const indexOf = c => Math.max(0, R.card.indexOf(c));
  const row = (c, gone) => `<div class="lean ${gone ? "gone" : ""}"
    data-model-case="${c.defense && c.defense.text ? "available" : "none"}">
    <div class="lean-index">${String(indexOf(c) + 1).padStart(2, "0")}</div>
    <div class="lean-argument">
      <div class="teams">
        ${badge(c.away_logo, c.away_team || c.matchup)}
        <span class="vs">at</span>
        ${badge(c.home_logo, c.home_team || "")}
      </div>
      <h3 class="leanplay">${fmt(c)}</h3>
      <p class="leandef">${esc(leanTake(c))}</p>
      <div class="leanmeta">${num(c.confidence)} confidence &middot; ${
        esc(c.period)} &middot; ${c.price > 0 ? "+" : ""}${esc(c.price)}${movement(c)}</div>
    </div></div>`;
  const games = new Set(R.card.map(c => c.matchup)).size;
  const stacked = games < R.card.length
    ? say(V.running_stacked.replace("{games}", games), "quiet")
    : "";
  el("running").innerHTML =
    R.card.map(c => row(c, false)).join("") + stacked;
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
  if (boardStale()) {
    const age = boardAgeHours();
    el("board-alert").innerHTML = say(
      V.board_stale.replace("{age}",
        age == null ? "unknown" : Math.round(age)));
    hide("board-fold");
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
el("source-policy").textContent = V.sources_note;
(function foldEmpties() {
  for (const [fold, ids] of [
    ["board-fold", ["board"]],
    ["ledger-fold", ["cal", "cum", "wk", "split", "fac", "res", "les"]],
  ]) {
    const alive = ids.some(id => {
      const h = el(id + "-h"), b = el(id);
      return b && getComputedStyle(b).display !== "none"
        && (!h || getComputedStyle(h).display !== "none");
    });
    if (!alive && el(fold)) el(fold).style.display = "none";
  }
})();

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
            # Every line Steve says, not just the subhead. calibration_note
            # and research_note both carry {publish} and both went to the
            # browser raw, so the page printed the placeholder at a reader
            # in two places. Filling the whole dict here is the point of
            # fill_publish: one quantity, one substitution, no sentence
            # left holding a brace.
            .replace("__VOICE__", json.dumps(
                {k: fill_publish(v) if isinstance(v, str) else v
                 for k, v in VOICE.items()}, separators=(",", ":")))
            .replace("__NAME__", VOICE["name"])
            .replace("__TAGLINE__", VOICE["tagline"])
            .replace("__SUBHEAD__", fill_publish(VOICE["subhead"]))
            .replace("__KICKER__", VOICE["kicker"])
            .replace("__LOGO__", VOICE["logo"])
            .replace("__BOARD_OPEN__", VOICE["board_open"].format(
                count=len(payload["board"].get("rows") or [])))
            .replace("__LEDGER_OPEN__", VOICE["ledger_open"])
            .replace("__MAX_BOARD_AGE__", str(MAX_BOARD_AGE_HOURS))
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
