"""
The baseline number. Not the pick - the anchor the handicapper argues
against. If the model says -9.4 and FanDuel says -6.5, that gap is where a
pick starts. The agent then has to justify it with news the model cannot
see: injuries, travel, motivation, scheme mismatch, weather.

Ratings are blended rather than trusted individually. SP+ is predictive and
opponent-adjusted, FPI is close behind, SRS is results-based and lags, Elo
is pure win/loss memory. Weighting them beats picking one.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

# Home field in FBS has compressed over the last decade. This is a starting
# value; the grader adjusts it in memory.json once there is a sample.
DEFAULT_HFA = 2.2
NEUTRAL_HFA = 0.0

RATING_WEIGHTS = {"sp": 0.45, "fpi": 0.25, "srs": 0.20, "elo": 0.10}

# Elo points per point of scoring margin, used to put Elo on a common scale.
ELO_PER_POINT = 25.0


@dataclass
class Projection:
    home_team: str
    away_team: str
    projected_spread: float | None      # negative = home favored
    projected_total: float | None
    projected_h1_spread: float | None
    projected_h1_total: float | None
    hfa_applied: float
    inputs: dict
    confidence_inputs: dict

    def to_dict(self) -> dict:
        return asdict(self)


def build_rating_book(sp: list[dict], fpi: list[dict], srs: list[dict],
                      elo: list[dict]) -> dict[str, dict]:
    """One row per team with whatever ratings we managed to load."""
    book: dict[str, dict] = {}

    for r in sp or []:
        t = r.get("team")
        if not t:
            continue
        book.setdefault(t, {})["sp"] = r.get("rating")
        book[t]["sp_off"] = (r.get("offense") or {}).get("rating") \
            if isinstance(r.get("offense"), dict) else r.get("offense")
        book[t]["sp_def"] = (r.get("defense") or {}).get("rating") \
            if isinstance(r.get("defense"), dict) else r.get("defense")

    for r in fpi or []:
        t = r.get("team")
        if t:
            book.setdefault(t, {})["fpi"] = r.get("fpi") or (
                r.get("resumeRanks") or {}).get("fpi")

    for r in srs or []:
        t = r.get("team")
        if t:
            book.setdefault(t, {})["srs"] = r.get("rating")

    for r in elo or []:
        t = r.get("team")
        if t:
            book.setdefault(t, {})["elo"] = r.get("elo") or r.get("rating")

    return book


def blended_rating(row: dict) -> float | None:
    """Weighted rating in points, renormalized over whatever is present."""
    if not row:
        return None
    total_w, acc = 0.0, 0.0
    for key, w in RATING_WEIGHTS.items():
        v = row.get(key)
        if v is None:
            continue
        val = float(v)
        if key == "elo":
            # Elo is a rating, not points. Center and scale it.
            val = (val - 1500.0) / ELO_PER_POINT
        acc += w * val
        total_w += w
    if total_w == 0:
        return None
    return acc / total_w


def project_game(home: str, away: str, book: dict[str, dict],
                 neutral: bool = False, hfa: float = DEFAULT_HFA,
                 league_avg_total: float = 54.0) -> Projection:
    hr = blended_rating(book.get(home, {}))
    ar = blended_rating(book.get(away, {}))
    applied_hfa = NEUTRAL_HFA if neutral else hfa

    spread = None
    if hr is not None and ar is not None:
        # Negative means the home team is laying points.
        spread = -round((hr - ar) + applied_hfa, 1)

    # Totals from SP+ offense and defense when available. SP+ offense is
    # points per drive above average, so this is a rough scaling, and the
    # handicapper is told to treat it as a prior, not a number to bet.
    total = None
    h_off = (book.get(home, {}) or {}).get("sp_off")
    h_def = (book.get(home, {}) or {}).get("sp_def")
    a_off = (book.get(away, {}) or {}).get("sp_off")
    a_def = (book.get(away, {}) or {}).get("sp_def")
    if None not in (h_off, h_def, a_off, a_def):
        try:
            off_edge = (float(h_off) - float(a_def)) + (float(a_off) - float(h_def))
            total = round(league_avg_total + off_edge * 0.5, 1)
        except (TypeError, ValueError):
            total = None

    # First half typically carries about 47% of full-game scoring and a
    # slightly compressed spread, since blowout garbage time lands after half.
    h1_spread = round(spread * 0.55, 1) if spread is not None else None
    h1_total = round(total * 0.47, 1) if total is not None else None

    return Projection(
        home_team=home,
        away_team=away,
        projected_spread=spread,
        projected_total=total,
        projected_h1_spread=h1_spread,
        projected_h1_total=h1_total,
        hfa_applied=applied_hfa,
        inputs={
            "home_rating": round(hr, 2) if hr is not None else None,
            "away_rating": round(ar, 2) if ar is not None else None,
            "home_components": book.get(home, {}),
            "away_components": book.get(away, {}),
        },
        confidence_inputs={
            "ratings_present_home": sum(
                1 for k in RATING_WEIGHTS if book.get(home, {}).get(k) is not None),
            "ratings_present_away": sum(
                1 for k in RATING_WEIGHTS if book.get(away, {}).get(k) is not None),
        },
    )


def edge_vs_market(projected: float | None, market_line: float | None
                   ) -> float | None:
    """Points of disagreement. Positive means the model likes our side."""
    if projected is None or market_line is None:
        return None
    return round(projected - market_line, 2)


def suggested_confidence(edge_pts: float | None, market: str,
                         ratings_complete: bool = True) -> float:
    """
    A floor, not a verdict. The agent starts here and then moves the number
    based on things the model cannot see. Deliberately conservative: a raw
    rating edge alone should almost never produce an 8.
    """
    if edge_pts is None:
        return 0.0
    e = abs(edge_pts)

    if market in ("spread",):
        base = 4.0 + min(e, 10.0) * 0.38
    elif market in ("total",):
        base = 3.8 + min(e, 12.0) * 0.32
    else:
        base = 3.5 + min(e, 10.0) * 0.30

    if not ratings_complete:
        base -= 0.8
    return round(min(base, 9.0), 1)
