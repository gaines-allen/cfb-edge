"""
Grading and bookkeeping math.

Everything the grader agent needs to turn a final score into a settled
ticket: spread, total, moneyline, first half, second half, plus payout,
closing line value, and the calibration bookkeeping that lets the model
find out whether a "9" is actually a 9.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["win", "loss", "push", "pending", "void"]


# ---------------------------------------------------------------- payouts

def payout(units: float, price: int, outcome: Outcome) -> float:
    """Net units won or lost. A push returns the stake, so net is 0."""
    if outcome == "win":
        return units * (price / 100.0) if price > 0 else units * (100.0 / abs(price))
    if outcome == "loss":
        return -units
    return 0.0


def breakeven(price: int) -> float:
    """Win rate needed to break even at this price."""
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


# ---------------------------------------------------------------- periods

def half_points(line_scores: list[int] | None, half: int) -> int | None:
    """
    Collapse quarter scores into a half. line_scores is [Q1, Q2, Q3, Q4, OT...].
    Overtime is credited to the second half, which matches how books settle
    2H wagers, while first half is untouched by OT.
    """
    if not line_scores:
        return None
    q = [int(x or 0) for x in line_scores]
    if half == 1:
        if len(q) < 2:
            return None
        return q[0] + q[1]
    if half == 2:
        if len(q) < 4:
            return None
        return sum(q[2:])
    return None


def period_scores(home_ls: list[int] | None, away_ls: list[int] | None,
                  period: str, home_final: int | None, away_final: int | None
                  ) -> tuple[int | None, int | None]:
    """Resolve the (home, away) score for 'full' | 'h1' | 'h2'."""
    if period == "full":
        return home_final, away_final
    half = 1 if period == "h1" else 2
    return half_points(home_ls, half), half_points(away_ls, half)


# ---------------------------------------------------------------- grading

def grade_spread(team_score: int, opp_score: int, spread: float) -> Outcome:
    """spread is the number laid or taken by the bet team (-7.5 lays 7.5)."""
    margin = (team_score - opp_score) + spread
    if margin > 0:
        return "win"
    if margin < 0:
        return "loss"
    return "push"


def grade_total(home_score: int, away_score: int, line: float,
                direction: str) -> Outcome:
    total = home_score + away_score
    if total == line:
        return "push"
    over = total > line
    want_over = direction.lower().startswith("o")
    return "win" if over == want_over else "loss"


def grade_moneyline(team_score: int, opp_score: int) -> Outcome:
    if team_score > opp_score:
        return "win"
    if team_score < opp_score:
        return "loss"
    return "push"


def grade_pick(pick: dict, game: dict) -> Outcome:
    """
    pick:  {"market": "spread"|"total"|"moneyline",
            "period": "full"|"h1"|"h2",
            "side": team name or "Over"/"Under",
            "line": float|None}
    game:  {"home_team","away_team","home_score","away_score",
            "home_line_scores":[...], "away_line_scores":[...], "completed":bool}
    """
    if not game.get("completed"):
        return "pending"

    hs, as_ = period_scores(
        game.get("home_line_scores"), game.get("away_line_scores"),
        pick.get("period", "full"),
        game.get("home_score"), game.get("away_score"),
    )
    if hs is None or as_ is None:
        # Completed game but no quarter detail yet, so a half bet cannot settle.
        return "pending"

    market = pick["market"]
    if market == "total":
        return grade_total(hs, as_, float(pick["line"]), pick["side"])

    side = pick["side"]
    is_home = side == game["home_team"]
    if not is_home and side != game["away_team"]:
        return "void"  # team name did not match the game, do not guess

    team, opp = (hs, as_) if is_home else (as_, hs)

    if market == "moneyline":
        return grade_moneyline(team, opp)
    if market == "spread":
        return grade_spread(team, opp, float(pick["line"]))
    return "void"


# ---------------------------------------------------------------- CLV

def clv_points(market: str, side: str, bet_line: float | None,
               close_line: float | None) -> float | None:
    """
    Points of closing line value, signed so positive always means the number
    we took was better than the number the market settled on.
    """
    if bet_line is None or close_line is None:
        return None
    if market == "spread":
        # Took -7.5, closed -9.5 -> we got 2 points the better of it.
        return round(bet_line - close_line, 2)
    if market == "total":
        if side.lower().startswith("o"):
            return round(close_line - bet_line, 2)   # over wants a lower number
        return round(bet_line - close_line, 2)       # under wants a higher one
    return None


def clv_cents(bet_price: int, close_price: int) -> int:
    """Price movement in cents, positive when we beat the close."""
    return int(bet_price - close_price)


# ---------------------------------------------------------- calibration

def confidence_bucket(conf: float) -> str:
    """Buckets used for the calibration table on the dashboard."""
    c = float(conf)
    if c >= 9.5:
        return "9.5-10"
    if c >= 9.0:
        return "9.0-9.4"
    if c >= 8.5:
        return "8.5-8.9"
    if c >= 8.0:
        return "8.0-8.4"
    return "below-8"


def summarize(picks: list[dict]) -> dict:
    """Roll a list of settled picks into a record."""
    graded = [p for p in picks if p.get("result") in ("win", "loss", "push")]
    wins = sum(1 for p in graded if p["result"] == "win")
    losses = sum(1 for p in graded if p["result"] == "loss")
    pushes = sum(1 for p in graded if p["result"] == "push")

    units = round(sum(float(p.get("units_net", 0.0)) for p in graded), 2)
    risked = round(sum(float(p.get("units", 1.0)) for p in graded
                       if p["result"] != "push"), 2)
    roi = round(100.0 * units / risked, 2) if risked else 0.0

    decided = wins + losses
    win_pct = round(100.0 * wins / decided, 1) if decided else 0.0

    clvs = [p["clv_points"] for p in graded if p.get("clv_points") is not None]
    beat_close = sum(1 for c in clvs if c > 0)

    return {
        "picks": len(graded),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_pct": win_pct,
        "units": units,
        "risked": risked,
        "roi": roi,
        "avg_clv": round(sum(clvs) / len(clvs), 2) if clvs else None,
        "beat_close_pct": round(100.0 * beat_close / len(clvs), 1) if clvs else None,
    }


def calibration_table(picks: list[dict]) -> list[dict]:
    """
    Did an 8 win less often than a 10? This is the table that answers it,
    and the one the handicapper is required to read before making picks.
    """
    order = ["9.5-10", "9.0-9.4", "8.5-8.9", "8.0-8.4", "below-8"]
    buckets: dict[str, list[dict]] = {k: [] for k in order}
    for p in picks:
        if p.get("result") not in ("win", "loss", "push"):
            continue
        buckets.setdefault(confidence_bucket(p.get("confidence", 0)), []).append(p)

    rows = []
    for name in order:
        group = buckets.get(name, [])
        if not group:
            continue
        s = summarize(group)
        s["bucket"] = name
        rows.append(s)
    return rows


def breakdown(picks: list[dict], key: str) -> list[dict]:
    """Record split by any pick field, e.g. market, period, or side_role."""
    groups: dict[str, list[dict]] = {}
    for p in picks:
        if p.get("result") not in ("win", "loss", "push"):
            continue
        groups.setdefault(str(p.get(key, "unknown")), []).append(p)

    rows = []
    for name, group in sorted(groups.items()):
        s = summarize(group)
        s[key] = name
        rows.append(s)
    return sorted(rows, key=lambda r: r["units"], reverse=True)
