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

import json
from dataclasses import dataclass, asdict
from pathlib import Path

# A blended rating and an SP+ only points split will never agree exactly,
# and they do not have to. Across the live board they land 1.9 points
# apart at the median and never more than 6. Past this they are not two
# readings of one game, they are two games, and publishing both is how a
# page ends up saying a team wins by 56 and by 28 in the same column.
COHERENCE_TOLERANCE = 4.0

CALIBRATION_FILE = Path(__file__).resolve().parents[2] / "data" / "model_calibration.json"

# Home field in FBS has compressed over the last decade. This is a starting
# value; the grader adjusts it in memory.json once there is a sample.
DEFAULT_HFA = 2.2
NEUTRAL_HFA = 0.0

RATING_WEIGHTS = {"sp": 0.45, "fpi": 0.25, "srs": 0.20, "elo": 0.10}

# Elo points per point of scoring margin, used to put Elo on a common scale.
ELO_PER_POINT = 25.0


def load_calibration() -> dict:
    """
    Bias and sigma per market, measured by scripts/calibrate_model.py.
    Missing file means no correction and no z scores, which is safe: the
    handicapper then sees raw points and the floor confidence stays low.
    """
    try:
        return json.loads(CALIBRATION_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def z_score(edge_pts: float | None, market: str,
            calibration: dict | None = None) -> float | None:
    """
    How unusual is this disagreement. A six point gap on a total sounds huge
    and is under two sigma, which is the whole reason this function exists.
    """
    if edge_pts is None:
        return None
    cal = calibration if calibration is not None else load_calibration()
    entry = cal.get("totals" if market == "total" else "spreads") or {}
    sigma = entry.get("sigma")
    if not sigma:
        return None
    return round(edge_pts / float(sigma), 2)


@dataclass
class Projection:
    home_team: str
    away_team: str
    projected_spread: float | None      # negative = home favored
    projected_total: float | None
    projected_h1_spread: float | None
    projected_h1_total: float | None
    hfa_applied: float
    # How far the two halves of the model disagree about this game, in
    # points. The spread comes off a blended rating, the total comes off
    # SP+ offense and defense, and they are only allowed to describe the
    # same game. None when either half is missing.
    coherence_gap: float | None
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

    # An empty response from any 1 source is normal, since SRS and FPI have
    # nothing to say before a season is played. All 4 coming back empty is
    # not normal, and the old behaviour was to build an empty book, project
    # every game as None, and drop the whole slate without saying why.
    if not book:
        raise ValueError(
            "No ratings loaded from any of SP+, FPI, SRS or Elo. The rating "
            "book is empty, so every projection would be None and the slate "
            "would come back empty with no reason given. Check CFBD_API_KEY "
            "and the season argument."
        )
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
                 league_avg_total: float = 54.0,
                 calibrate: bool = True) -> Projection:
    hr = blended_rating(book.get(home, {}))
    ar = blended_rating(book.get(away, {}))
    applied_hfa = NEUTRAL_HFA if neutral else hfa

    spread = None
    if hr is not None and ar is not None:
        # Negative means the home team is laying points.
        spread = -round((hr - ar) + applied_hfa, 1)

    # SP+ offense and defense are projected points scored and points allowed
    # against an average opponent, not differentials. So each side's expected
    # points is the average of its own offense and the opponent's defense.
    #
    # An earlier version treated them as differentials and produced 83 point
    # totals against a 48.5 market, which is the model being wrong rather
    # than the market. Caught on the first live slate.
    total = None
    home_points = away_points = None
    h_off = (book.get(home, {}) or {}).get("sp_off")
    h_def = (book.get(home, {}) or {}).get("sp_def")
    a_off = (book.get(away, {}) or {}).get("sp_off")
    a_def = (book.get(away, {}) or {}).get("sp_def")
    #
    # Averaging the two halved every margin. Work it through: the margin
    # that formula produces is ((h_off + a_def) - (a_off + h_def)) / 2,
    # which is exactly half the rating differential the spread is built
    # from. So the spread model and the totals model described two
    # different games, and the site printed both. Ohio State by 56.4 on
    # one row and by 28.6 on the next, same matchup, same page.
    #
    # An offense rated 41 scores 41 against an average defense. Facing a
    # defense that allows 36.7 where average allows 27, it scores 41 plus
    # that 9.7 difference. Adjust, do not average. The margin then equals
    # the rating differential by construction, so the two halves agree.
    league_pts = float(league_avg_total) / 2.0
    if None not in (h_off, h_def, a_off, a_def):
        try:
            home_points = float(h_off) + (float(a_def) - league_pts)
            away_points = float(a_off) + (float(h_def) - league_pts)
            # A projection below a shutout is not a projection. Clamping
            # breaks the agreement above, which is why coherent() checks
            # the result rather than trusting it.
            home_points = max(home_points, 0.0)
            away_points = max(away_points, 0.0)
            total = round(home_points + away_points, 1)
        except (TypeError, ValueError):
            total = home_points = away_points = None

    # Centre the model on the market. A constant offset is not an edge, and
    # leaving it in means every total looks like an under.
    cal = load_calibration() if calibrate else {}
    if cal:
        tb = (cal.get("totals") or {}).get("bias")
        sb = (cal.get("spreads") or {}).get("bias")
        if total is not None and tb is not None:
            total = round(total - float(tb), 1)
        if spread is not None and sb is not None:
            spread = round(spread - float(sb), 1)

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
        coherence_gap=(
            None if None in (spread, home_points, away_points)
            else round(abs(-spread - (home_points - away_points)), 2)),
        inputs={
            "home_rating": round(hr, 2) if hr is not None else None,
            "away_rating": round(ar, 2) if ar is not None else None,
            "home_points": round(home_points, 1) if home_points is not None else None,
            "away_points": round(away_points, 1) if away_points is not None else None,
            "home_components": book.get(home, {}),
            "away_components": book.get(away, {}),
        },
        confidence_inputs={
            "ratings_present_home": sum(
                1 for k in RATING_WEIGHTS if book.get(home, {}).get(k) is not None),
            "ratings_present_away": sum(
                1 for k in RATING_WEIGHTS if book.get(away, {}).get(k) is not None),
            "calibrated": bool(cal),
        },
    )


def edge_vs_market(projected: float | None, market_line: float | None
                   ) -> float | None:
    """Points of disagreement. Positive means the model likes our side."""
    if projected is None or market_line is None:
        return None
    return round(projected - market_line, 2)


def suggested_confidence(edge_pts: float | None, market: str,
                         ratings_complete: bool = True,
                         calibration: dict | None = None) -> float:
    """
    A floor, not a verdict, and deliberately hard to move.

    Scored in standard deviations rather than points, because points are
    misleading: measured dispersion between this model and the market is
    around 3 points on totals and 2.8 on spreads, so a "six point edge" is
    under two sigma and most of it is noise. Two sigma gets you to roughly
    6.5, which is still below the publish threshold. Nothing here alone
    should ever clear 8. That has to be earned with research the model
    cannot do.
    """
    if edge_pts is None:
        return 0.0

    z = z_score(edge_pts, market, calibration)
    if z is None:
        # Uncalibrated. Fall back to a blunt points scale and cap it lower,
        # since we have no idea how unusual this gap is.
        base = 4.0 + min(abs(edge_pts), 10.0) * 0.20
        return round(min(base, 6.5), 1)

    base = 3.5 + min(abs(z), 4.0) * 1.5
    if not ratings_complete:
        base -= 0.8
    return round(min(base, 7.5), 1)
