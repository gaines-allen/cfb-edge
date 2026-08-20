"""
Shape validation on every external response.

Every bug this system has shipped came from an upstream field meaning
something other than what the code assumed. SP+ offense turned out to be
points scored rather than a differential, which produced 83 point totals
against a 48.5 market and made every candidate an Over. Team names turned
out to carry mascots, so "Alabama Crimson Tide" never matched "Alabama".
Neither raised anything. Both resolved to a plausible number and flowed
onward.

So the rule here is that a missing or renamed field raises. Nothing returns
None, nothing falls back to a default, and nothing gets skipped quietly.
ShapeError names the endpoint, the path to the field, what was expected and
what arrived, because the point of raising is that a human can fix it in
under a minute.

Presence and type checks are the easy half. The half that catches the bugs
above is semantic: a column of SP+ offense ratings whose median sits near 0
is a differential no matter what the field is called, and a CFBD team name
that the ESPN mascot map can shorten is carrying a mascot no matter what
the docs say.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Iterable, Sequence

from .teams import MASCOT_MAP, normalize


class ShapeError(RuntimeError):
    """An upstream payload is not the shape the code was written against."""


def fail(endpoint: str, path: str, expected: str, got: Any) -> None:
    shown = repr(got)
    if len(shown) > 160:
        shown = shown[:157] + "..."
    raise ShapeError(
        f"{endpoint}: {path} expected {expected}, got {shown}. "
        f"The upstream payload changed shape. Fix the parser or the "
        f"validator in scripts/lib/schema.py, never by loosening the check."
    )


# ------------------------------------------------------------ primitives

NUMBER = (int, float)


def need_list(obj: Any, endpoint: str, path: str = "$",
              min_len: int = 1) -> list:
    if not isinstance(obj, list):
        fail(endpoint, path, "a list", obj)
    if len(obj) < min_len:
        fail(endpoint, path, f"at least {min_len} row(s)", f"{len(obj)} rows")
    return obj


def need_dict(obj: Any, endpoint: str, path: str) -> dict:
    if not isinstance(obj, dict):
        fail(endpoint, path, "an object", obj)
    return obj


def need(row: dict, key: str, endpoint: str, path: str,
         types: tuple = (str,), allow_none: bool = False) -> Any:
    """One required field. A missing key and a null both raise."""
    if key not in row:
        fail(endpoint, f"{path}.{key}", f"the key to be present ({_names(types)})",
             f"keys {sorted(row)[:12]}")
    v = row[key]
    if v is None:
        if allow_none:
            return None
        fail(endpoint, f"{path}.{key}", _names(types), None)
    if types and not isinstance(v, types):
        fail(endpoint, f"{path}.{key}", _names(types), v)
    if str in types and isinstance(v, str) and not v.strip():
        fail(endpoint, f"{path}.{key}", "a non-empty string", v)
    return v


def need_one_of(row: dict, keys: Sequence[str], endpoint: str, path: str,
                types: tuple = (str,), allow_none: bool = False) -> Any:
    """
    CFBD renamed its game fields from home_team to homeTeam, and the code
    reads both. Either spelling satisfies this, neither does not.
    """
    present = [k for k in keys if k in row and row[k] is not None]
    if not present:
        fail(endpoint, f"{path}.[{' | '.join(keys)}]",
             f"one of these keys to carry {_names(types)}",
             f"keys {sorted(row)[:12]}")
    return need(row, present[0], endpoint, path, types, allow_none)


def need_iso(row: dict, key: str, endpoint: str, path: str) -> str:
    v = need(row, key, endpoint, path, (str,))
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        fail(endpoint, f"{path}.{key}", "an ISO 8601 timestamp", v)
    return v


def _names(types: tuple) -> str:
    return " or ".join(t.__name__ for t in types) if types else "a value"


def _median(values: Iterable[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, NUMBER)]
    return statistics.median(nums) if len(nums) >= 5 else None


# ----------------------------------------------------------- team names

def assert_no_mascot(name: str, endpoint: str, path: str) -> str:
    """
    CFBD publishes locations, so a CFBD name must never carry a mascot.
    strip_mascot is driven by the ESPN map, which publishes location and
    mascot separately, so this compares against real data rather than a
    guess about how many trailing words a mascot runs.
    """
    if not isinstance(name, str) or not name.strip():
        fail(endpoint, path, "a non-empty team name", name)
    shortened = MASCOT_MAP.get(normalize(name))
    if shortened and normalize(shortened) != normalize(name):
        fail(endpoint, path,
             f"a bare school name, since {endpoint} publishes locations",
             f"{name!r}, which the ESPN map shortens to {shortened!r}")
    return name


# ------------------------------------------------------- The Odds API

ODDS_FEATURED = {"spreads", "totals", "h2h"}
ODDS_PERIOD = {"spreads_h1", "totals_h1", "spreads_h2", "totals_h2"}
ODDS_MARKETS = ODDS_FEATURED | ODDS_PERIOD

POINT_MARKETS = {"spreads", "totals", "spreads_h1", "totals_h1",
                 "spreads_h2", "totals_h2"}


def validate_odds_event(ev: Any, endpoint: str = "the-odds-api /odds",
                        path: str = "$", require_bookmakers: bool = True) -> dict:
    ev = need_dict(ev, endpoint, path)
    need(ev, "id", endpoint, path, (str,))
    need_iso(ev, "commence_time", endpoint, path)
    home = need(ev, "home_team", endpoint, path, (str,))
    away = need(ev, "away_team", endpoint, path, (str,))
    if normalize(home) == normalize(away):
        fail(endpoint, f"{path}.home_team", "two different teams",
             f"{home!r} against {away!r}")

    books = ev.get("bookmakers")
    if books is None:
        if require_bookmakers:
            fail(endpoint, f"{path}.bookmakers", "a list of bookmakers", None)
        return ev
    need_list(books, endpoint, f"{path}.bookmakers", min_len=0)

    for bi, bk in enumerate(books):
        bpath = f"{path}.bookmakers[{bi}]"
        need_dict(bk, endpoint, bpath)
        need(bk, "key", endpoint, bpath, (str,))
        markets = need(bk, "markets", endpoint, bpath, (list,))
        for mi, mk in enumerate(markets):
            _validate_odds_market(mk, endpoint, f"{bpath}.markets[{mi}]")
    return ev


def _validate_odds_market(mk: Any, endpoint: str, path: str) -> None:
    need_dict(mk, endpoint, path)
    key = need(mk, "key", endpoint, path, (str,))
    if key not in ODDS_MARKETS:
        fail(endpoint, f"{path}.key",
             f"one of {sorted(ODDS_MARKETS)}", key)
    outcomes = need(mk, "outcomes", endpoint, path, (list,))
    if len(outcomes) < 2:
        fail(endpoint, f"{path}.outcomes", "at least 2 sides", outcomes)

    points = []
    for oi, oc in enumerate(outcomes):
        opath = f"{path}.outcomes[{oi}]"
        need_dict(oc, endpoint, opath)
        need(oc, "name", endpoint, opath, (str,))
        # This is the one that mattered. The parser did int(oc.get("price", 0)),
        # so a renamed price became a 0 that reached payout() as a real number.
        price = need(oc, "price", endpoint, opath, NUMBER)
        if isinstance(price, bool) or abs(float(price)) < 100:
            fail(endpoint, f"{opath}.price",
                 "American odds, so 100 or more either side of 0", price)
        if key in POINT_MARKETS:
            points.append(need(oc, "point", endpoint, opath, NUMBER))
        elif oc.get("point") is not None:
            fail(endpoint, f"{opath}.point",
                 f"no point, since {key} is a moneyline", oc.get("point"))

    # A spread's 2 sides mirror each other and a total's 2 sides match.
    # Either check failing means the sign convention moved under us.
    if key.startswith("spreads") and len(points) == 2:
        if abs(points[0] + points[1]) > 1e-6:
            fail(endpoint, f"{path}.outcomes[].point",
                 "2 spreads that sum to 0", points)
    if key.startswith("totals") and len(points) == 2:
        if abs(points[0] - points[1]) > 1e-6:
            fail(endpoint, f"{path}.outcomes[].point",
                 "2 identical totals", points)


def validate_odds_board(raw: Any, endpoint: str = "the-odds-api /odds",
                        min_events: int = 1) -> list:
    events = need_list(raw, endpoint, "$", min_len=min_events)
    for i, ev in enumerate(events):
        validate_odds_event(ev, endpoint, f"$[{i}]", require_bookmakers=False)
    return events


def validate_odds_scores(raw: Any,
                         endpoint: str = "the-odds-api /scores") -> list:
    rows = need_list(raw, endpoint, "$", min_len=0)
    for i, ev in enumerate(rows):
        path = f"$[{i}]"
        need_dict(ev, endpoint, path)
        need(ev, "id", endpoint, path, (str,))
        need(ev, "home_team", endpoint, path, (str,))
        need(ev, "away_team", endpoint, path, (str,))
        if "completed" in ev and not isinstance(ev["completed"], bool):
            fail(endpoint, f"{path}.completed", "a boolean", ev["completed"])
    return rows


# ------------------------------------------------------------ CFBD games

def validate_cfbd_games(rows: Any, endpoint: str = "cfbd /games",
                        min_rows: int = 1, check_mascots: bool = True) -> list:
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    for i, g in enumerate(rows):
        path = f"$[{i}]"
        need_dict(g, endpoint, path)
        need(g, "id", endpoint, path, (int, str))
        need_one_of(g, ("season", "year"), endpoint, path, (int,))
        need_one_of(g, ("week",), endpoint, path, (int,))
        home = need_one_of(g, ("homeTeam", "home_team"), endpoint, path, (str,))
        away = need_one_of(g, ("awayTeam", "away_team"), endpoint, path, (str,))
        if check_mascots:
            assert_no_mascot(home, endpoint, f"{path}.homeTeam")
            assert_no_mascot(away, endpoint, f"{path}.awayTeam")

        completed = g.get("completed")
        if completed is None or not isinstance(completed, bool):
            fail(endpoint, f"{path}.completed", "a boolean", completed)

        neutral = g.get("neutralSite", g.get("neutral_site"))
        if neutral is not None and not isinstance(neutral, bool):
            fail(endpoint, f"{path}.neutralSite", "a boolean or absent", neutral)

        if completed:
            need_one_of(g, ("homePoints", "home_points"), endpoint, path, (int,))
            need_one_of(g, ("awayPoints", "away_points"), endpoint, path, (int,))
        _validate_line_scores(g, endpoint, path, "home", completed)
        _validate_line_scores(g, endpoint, path, "away", completed)
    return rows


def _validate_line_scores(g: dict, endpoint: str, path: str, side: str,
                          completed: bool) -> None:
    """
    Quarter scores are the only reason a first half pick can ever settle,
    so a string where a list belongs has to raise rather than reach
    half_points and come back None.
    """
    key = next((k for k in (f"{side}LineScores", f"{side}_line_scores")
                if k in g), None)
    if key is None:
        return
    v = g[key]
    if v is None:
        return
    if not isinstance(v, list):
        fail(endpoint, f"{path}.{key}", "a list of quarter scores or null", v)
    for j, q in enumerate(v):
        if q is not None and (isinstance(q, bool) or not isinstance(q, int)):
            fail(endpoint, f"{path}.{key}[{j}]", "an integer", q)
    # No minimum length. A game called for weather stops early and CFBD
    # reports the quarters that were played, which is real rather than a
    # shape change. Kentucky 31 Southern Miss 0 in 2024 week 1 ran 3
    # quarters. See test_scoring_edge_cases.py for what that does to an
    # h2 grade, which is a scoring bug and not a validation one.


# ------------------------------------------------------------ CFBD lines

def validate_cfbd_lines(rows: Any, endpoint: str = "cfbd /lines",
                        min_rows: int = 1) -> list:
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    priced = 0
    for i, g in enumerate(rows):
        path = f"$[{i}]"
        need_dict(g, endpoint, path)
        need_one_of(g, ("homeTeam", "home_team"), endpoint, path, (str,))
        need_one_of(g, ("awayTeam", "away_team"), endpoint, path, (str,))
        lines = g.get("lines")
        if lines is None:
            continue
        need_list(lines, endpoint, f"{path}.lines", min_len=0)
        for j, ln in enumerate(lines):
            lpath = f"{path}.lines[{j}]"
            need_dict(ln, endpoint, lpath)
            need(ln, "provider", endpoint, lpath, (str,))
            for fld in ("spread", "overUnder", "spreadOpen", "overUnderOpen"):
                v = ln.get(fld)
                if v is None:
                    continue
                if isinstance(v, str):
                    try:
                        float(v)
                    except ValueError:
                        fail(endpoint, f"{lpath}.{fld}", "a number", v)
                elif not isinstance(v, NUMBER):
                    fail(endpoint, f"{lpath}.{fld}", "a number or null", v)
            if ln.get("spread") is not None or ln.get("overUnder") is not None:
                priced += 1
    # A week whose games have been played must carry prices somewhere,
    # because this is the closing line source the backtest reads and an
    # all null response would silently score nothing. A future week
    # legitimately carries nulls, since books have not posted yet.
    played = any(g.get("homeScore") is not None or g.get("home_score") is not None
                 for g in rows if isinstance(g, dict))
    if rows and played and priced == 0:
        fail(endpoint, "$[].lines[].spread",
             "at least 1 priced number across a week that has been played, "
             "since this is the closing line source the backtest reads",
             "every row carried nulls")
    return rows


# ---------------------------------------------------------- CFBD ratings

def validate_sp_ratings(rows: Any, endpoint: str = "cfbd /ratings/sp",
                        min_rows: int = 0) -> list:
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    offense, defense, overall = [], [], []
    for i, r in enumerate(rows):
        path = f"$[{i}]"
        need_dict(r, endpoint, path)
        team = need(r, "team", endpoint, path, (str,))
        assert_no_mascot(team, endpoint, f"{path}.team")
        overall.append(_rating_of(r, "rating", endpoint, path))
        offense.append(_side_rating(r, "offense", endpoint, path))
        defense.append(_side_rating(r, "defense", endpoint, path))

    # The bug this exists for. SP+ offense is points scored, which centres
    # around 28. A differential centres on 0. Treating one as the other
    # produced 83 point totals against a 48.5 market.
    om = _median(offense)
    if om is not None and om < 12.0:
        fail(endpoint, "$[].offense.rating",
             "points scored per game, so a median above 12",
             f"a median of {om:.1f}, which is a differential, not points")
    dm = _median(defense)
    if dm is not None and dm < 12.0:
        fail(endpoint, "$[].defense.rating",
             "points allowed per game, so a median above 12",
             f"a median of {dm:.1f}, which is a differential, not points")
    rm = _median(overall)
    if rm is not None and abs(rm) > 12.0:
        fail(endpoint, "$[].rating",
             "an overall differential, so a median within 12 of 0",
             f"a median of {rm:.1f}, which looks like points scored")
    return rows


def _side_rating(r: dict, side: str, endpoint: str, path: str) -> float | None:
    """model.py reads either a nested object or a bare number, so both pass."""
    if side not in r:
        fail(endpoint, f"{path}.{side}",
             "an offense/defense object or a bare rating",
             f"keys {sorted(r)[:12]}")
    v = r[side]
    if isinstance(v, dict):
        return _rating_of(v, "rating", endpoint, f"{path}.{side}")
    if isinstance(v, NUMBER):
        return float(v)
    if v is None:
        return None
    fail(endpoint, f"{path}.{side}", "an object or a number", v)


def _rating_of(row: dict, key: str, endpoint: str, path: str) -> float | None:
    if key not in row:
        fail(endpoint, f"{path}.{key}", "a numeric rating",
             f"keys {sorted(row)[:12]}")
    v = row[key]
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, NUMBER):
        fail(endpoint, f"{path}.{key}", "a number", v)
    return float(v)


def validate_fpi_ratings(rows: Any, endpoint: str = "cfbd /ratings/fpi",
                         min_rows: int = 0) -> list:
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    seen = 0
    for i, r in enumerate(rows):
        path = f"$[{i}]"
        need_dict(r, endpoint, path)
        assert_no_mascot(need(r, "team", endpoint, path, (str,)),
                         endpoint, f"{path}.team")
        v = r.get("fpi")
        if v is None:
            v = (r.get("resumeRanks") or {}).get("fpi")
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, NUMBER):
                fail(endpoint, f"{path}.fpi", "a number or null", v)
            seen += 1
    if rows and seen == 0:
        fail(endpoint, "$[].fpi",
             "at least 1 numeric fpi across the response",
             "every row was null, so the field was renamed")
    return rows


def validate_srs_ratings(rows: Any, endpoint: str = "cfbd /ratings/srs",
                         min_rows: int = 0) -> list:
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    for i, r in enumerate(rows):
        path = f"$[{i}]"
        need_dict(r, endpoint, path)
        assert_no_mascot(need(r, "team", endpoint, path, (str,)),
                         endpoint, f"{path}.team")
        _rating_of(r, "rating", endpoint, path)
    return rows


def validate_elo_ratings(rows: Any, endpoint: str = "cfbd /ratings/elo",
                         min_rows: int = 0, week: int | None = None) -> list:
    """
    Elo is the one genuinely time-varying rating, so the backtest leans on
    it. If a weekly pull comes back carrying a different week, every
    as-of number in the backtest is silently wrong.
    """
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    values = []
    for i, r in enumerate(rows):
        path = f"$[{i}]"
        need_dict(r, endpoint, path)
        assert_no_mascot(need(r, "team", endpoint, path, (str,)),
                         endpoint, f"{path}.team")
        v = r.get("elo", r.get("rating"))
        if v is None:
            fail(endpoint, f"{path}.elo", "a numeric Elo", f"keys {sorted(r)[:12]}")
        if isinstance(v, bool) or not isinstance(v, NUMBER):
            fail(endpoint, f"{path}.elo", "a number", v)
        values.append(float(v))
        if week is not None and r.get("week") is not None:
            if int(r["week"]) != int(week):
                fail(endpoint, f"{path}.week",
                     f"week {week}, the week that was asked for", r["week"])
    m = _median(values)
    if m is not None and not (1000.0 <= m <= 2200.0):
        fail(endpoint, "$[].elo",
             "an Elo rating, so a median between 1000 and 2200", f"{m:.0f}")
    return rows


def validate_cfbd_calendar(rows: Any, endpoint: str = "cfbd /calendar",
                           min_rows: int = 1) -> list:
    """
    make_slate.py falls back to no week filter when this is missing, which
    turns off the guard against rating a November game off a preseason
    number. So the window fields are required, not optional.
    """
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    for i, w in enumerate(rows):
        path = f"$[{i}]"
        need_dict(w, endpoint, path)
        need_one_of(w, ("week",), endpoint, path, (int,))
        need_one_of(w, ("seasonType", "season_type"), endpoint, path, (str,))
        first = need_one_of(w, ("firstGameStart", "first_game_start"),
                            endpoint, path, (str,))
        last = need_one_of(w, ("lastGameStart", "last_game_start"),
                           endpoint, path, (str,))
        for label, v in (("firstGameStart", first), ("lastGameStart", last)):
            try:
                datetime.fromisoformat(v.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                fail(endpoint, f"{path}.{label}", "an ISO 8601 timestamp", v)
    return rows


def validate_fbs_teams(rows: Any, endpoint: str = "cfbd /teams/fbs",
                       min_rows: int = 0) -> list:
    rows = need_list(rows, endpoint, "$", min_len=min_rows)
    for i, r in enumerate(rows):
        path = f"$[{i}]"
        need_dict(r, endpoint, path)
        assert_no_mascot(need(r, "school", endpoint, path, (str,)),
                         endpoint, f"{path}.school")
    return rows


# ------------------------------------------------------------------ ESPN

def validate_espn_teams(payload: Any, endpoint: str = "espn /teams",
                        expected: int | None = None) -> list:
    """
    Undocumented and unsanctioned, so it changes shape without warning. It
    also caps at 500 and truncates alphabetically, and asking for limit=900
    silently returns the top 25, which is the trap build_aliases.py pages
    around.
    """
    payload = need_dict(payload, endpoint, "$")
    sports = need(payload, "sports", endpoint, "$", (list,))
    need_list(sports, endpoint, "$.sports")
    leagues = need(sports[0], "leagues", endpoint, "$.sports[0]", (list,))
    need_list(leagues, endpoint, "$.sports[0].leagues")
    league = need_dict(leagues[0], endpoint, "$.sports[0].leagues[0]")
    teams = league.get("teams")
    if teams is None:
        fail(endpoint, "$.sports[0].leagues[0].teams", "a list of teams", None)
    need_list(teams, endpoint, "$.sports[0].leagues[0].teams", min_len=0)

    if expected is not None and 0 < len(teams) < expected and len(teams) == 25:
        fail(endpoint, "$.sports[0].leagues[0].teams",
             f"{expected} teams for the requested page size",
             "25, which is the silent truncation this endpoint does when "
             "the limit is too high")

    for i, wrapper in enumerate(teams):
        path = f"$.sports[0].leagues[0].teams[{i}]"
        need_dict(wrapper, endpoint, path)
        t = need(wrapper, "team", endpoint, path, (dict,))
        need(t, "id", endpoint, f"{path}.team", (str, int))
        # location and name are the whole trick. location is "Alabama" and
        # name is "Crimson Tide", and that split is what builds the mascot
        # map. If either goes away, the map cannot be rebuilt at all.
        need(t, "location", endpoint, f"{path}.team", (str,))
        need(t, "displayName", endpoint, f"{path}.team", (str,))
        if "name" in t and t["name"] is not None and not isinstance(t["name"], str):
            fail(endpoint, f"{path}.team.name", "a string or absent", t["name"])
    return teams
