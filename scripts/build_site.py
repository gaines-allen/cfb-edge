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
# Steve books. He does not sell. The whole promise sits in the tagline: a
# bookie takes the other side of your bet and never shows you his record,
# and this one posts his either way. The swagger only means something while
# the honest copy survives, so the empty states, the losing weeks and the
# "this means nothing yet" lines are his too, not exceptions carved out of
# him. A man who is actually good does not need a play every Wednesday.
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
    "tagline": "Six plays. Every number. Every result, "
               "including the ugly ones.",
    "subhead": "Nothing goes up under 8.0 out of 10, the card is posted "
               "before kickoff, and the record underneath it is the same "
               "one whether the week went well or not.",

    # Openly a persona. A page built on not overclaiming cannot open by
    # implying a real handicapper is behind it.
    "about": "Steve is the voice of this system, not a person. The numbers "
             "come from a ratings model, the reasons come from research "
             "that is checked against its sources before anything is "
             "posted, and every figure about the record is computed from "
             "the ledger rather than written by hand.",

    "card_heading": "Today's picks",
    "card_empty": "Nothing this week. The board is priced right, and I do "
                  "not hand you a play just because it is Wednesday.",
    "card_short": "That is the card. A short week means the numbers were "
                  "fair, not that I clocked off early.",
    "card_pending": "Still running",

    "book_heading": "The book",
    "book_empty": "Nothing has settled yet. Ask me again Sunday night "
                  "and there will be a number here, whichever way it "
                  "went.",

    "calibration_heading": "Do my numbers mean anything",
    "calibration_empty": "Nothing has graded, so an 8 and a 9 are two "
                         "numbers I wrote down and nothing more.",
    "calibration_note": "Shadow picks rated under the publish line are in "
                        "here on purpose. That is how the line gets tested "
                        "instead of assumed. Units on the below-8 row are "
                        "notional, since nothing under the line is staked.",

    "cumulative_heading": "Where you would be",
    "cumulative_empty": "Nothing has settled yet.",
    "cumulative_note": "Net units across settled plays, in the order they "
                       "settled.",

    "weekly_heading": "Week by week",
    "weekly_empty": "No weeks on the board yet.",

    "split_heading": "Where it came from",
    "split_empty": "Nothing settled to split yet.",

    "factors_heading": "What my reasons have been worth",
    "factors_empty": "This fills in once picks start settling.",

    "lessons_heading": "What I got wrong",
    "lessons_empty": "Nothing to own up to yet. Give it time.",

    "no_signal": "Too few decided picks to mean anything.",
    "no_signal_long": "That record has not decided enough games to mean "
                      "anything. The 95 percent range on it still covers "
                      "the 52.4 percent you need to break even, so it "
                      "tells you nothing either way. I post it because "
                      "hiding it would be worse, not because it proves "
                      "something.",
    "no_signal_short": "Same caveat as above. Not enough has decided for "
                       "these bars to mean anything yet.",

    "sources_heading": "Where I got it",
    "sources_note": "Every link was opened and the quoted line checked "
                    "against the page before the pick went up.",

    # Deliberately out of character. Nobody should have to read a persona
    # to find the helpline.
    "disclaimer": "These are opinions on football games, not investment "
                  "advice. This model is being tested in public and it has "
                  "been wrong. Bet only what you can afford to lose. If "
                  "gambling stops being fun, call 1-800-GAMBLER.",
}


def build_payload() -> dict:
    picks = store.load_picks()
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

    return {
        "generated_at": store.now_iso(),
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
                "live": p.get("live"),
            }
            for p in sorted(picks, key=lambda x: (x.get("kickoff") or ""))
        ],
    }


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ - the card</title>
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

  <h2 id="card-h">The card</h2>
  <div id="card"></div>

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
const num = (n, d = 2) => (n == null ? "n/a" : Number(n).toFixed(d));
const signed = (n, d = 2) => (n == null ? "n/a" : (n > 0 ? "+" : "") + Number(n).toFixed(d));

for (const [id, key] of [["card-h","card_heading"],["book-h","book_heading"],
  ["cal-h","calibration_heading"],["cum-h","cumulative_heading"],
  ["wk-h","weekly_heading"],["split-h","split_heading"],
  ["fac-h","factors_heading"],["les-h","lessons_heading"]]) {
  if (el(id)) el(id).textContent = V[key];
}

function say(text, cls) {
  return `<p class="${cls || "said"}">${esc(text)}</p>`;
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
  if (!live.length) { el("card").innerHTML = say(V.card_empty); return; }
  const staked = live.reduce((a, p) => a + (p.units || 0), 0);
  el("card").innerHTML =
    `<p class="weekline">Season ${esc(cur.season)} &middot; Week ${esc(cur.week)} &middot; ` +
    `${live.length} ${live.length === 1 ? "play" : "plays"} &middot; ${num(staked, 1)} units</p>` +
    `<div class="tickets">${live.map(ticket).join("")}</div>` +
    (live.length < DATA.target_picks ? say(V.card_short, "quiet") : "") +
    `<p class="quiet" style="margin-top:10px">${esc(V.sources_note)}</p>`;
})();

/* ---------------------------------------------------------- the book */
(function renderBook() {
  const o = DATA.overall || {};
  if (!o.picks) { el("book").innerHTML = say(V.book_empty); return; }
  const stats = [
    ["Units", signed(o.units), true, o.units],
    ["Record", `${o.wins}-${o.losses}${o.pushes ? "-" + o.pushes : ""}`, false, null],
    ["Win rate", o.win_pct != null ? o.win_pct + "%" : "n/a", false, null],
    ["ROI", o.roi != null ? signed(o.roi, 1) + "%" : "n/a", false, o.roi],
    ["Avg CLV", o.avg_clv != null ? signed(o.avg_clv, 2) : "n/a", false, o.avg_clv],
    ["Beat close", o.beat_close_pct != null ? o.beat_close_pct + "%" : "n/a", false, null],
    ["Still running", DATA.pending_count, false, null],
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
  if (!rows.length) { el("cal").innerHTML = say(V.calibration_empty); return; }
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
  if (pts.length < 2) { el("cum").innerHTML = say(V.cumulative_empty); return; }
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

el("wk").innerHTML = table([
  ["Week", r => `${esc(r.season)} wk ${esc(r.week)}`],
  ["Plays", r => esc(r.picks)],
  ["Record", r => `${esc(r.wins)}-${esc(r.losses)}`],
  ["Units", r => signed(r.units)],
], DATA.weekly || [], V.weekly_empty);

const splitRows = [
  ...(DATA.by_market || []).map(r => ({...r, kind: "Market"})),
  ...(DATA.by_period || []).map(r => ({...r, kind: "Period"})),
];
el("split").innerHTML = splitRows.length
  ? table([
      ["Kind", r => esc(r.kind)],
      ["Split", r => esc(r.market || r.period)],
      ["Record", r => `${esc(r.wins)}-${esc(r.losses)}`],
      ["Units", r => signed(r.units)],
      ["Reading", sigCell],
    ], splitRows, V.split_empty)
  : say(V.split_empty);

el("fac").innerHTML = table([
  ["Reason", r => esc(r.factor)],
  ["Plays", r => esc(r.picks)],
  ["Record", r => `${esc(r.wins)}-${esc(r.losses)}`],
  ["Units", r => signed(r.units)],
  ["Reading", sigCell],
], DATA.by_factor || [], V.factors_empty);

el("les").innerHTML = (DATA.lessons || []).length
  ? (DATA.lessons || []).map(l =>
      `<p class="said">${esc(l.lesson)}</p>` +
      `<p class="quiet">Week ${esc(l.week)}, ${esc(l.season)}.</p>`).join("")
  : say(V.lessons_empty);

/* ------------------------------------------------------------ footer */
el("prov").textContent =
  `Built ${new Date(DATA.generated_at).toLocaleString()}. ` +
  `Board pulled ${DATA.board_fetched_at ? new Date(DATA.board_fetched_at).toLocaleString() : "never"}. ` +
  `Odds API credits left ${DATA.credits_remaining == null ? "unknown" : DATA.credits_remaining}. ` +
  `Lines from The Odds API, ratings and results from CollegeFootballData.`;
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
            .replace("__LOGO__", VOICE["logo"]))
    store.write_text(SITE / "index.html", html)

    print(json.dumps({
        "dry_run": args.dry_run,
        "wrote": [] if args.dry_run else ["site/index.html", "site/data.json"],
        "bytes": len(html),
        "live_picks": sum(1 for p in payload["picks"] if p["live"]),
        "weeks": len(payload["weeks"]),
        "record": payload["overall"],
    }, indent=2))
    log.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
