"""
The ledger. Every agent reads and writes through here so the four of them
never disagree about what a pick record looks like.

Files under data/:
  picks.json        every pick ever made, graded or pending
  line_history.json daily snapshot of every number we are tracking
  memory.json       what the grader has learned, and what the handicapper must read
  board.json        the current FanDuel board
  results.json      final scores keyed by event
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

PICKS = DATA / "picks.json"
LINE_HISTORY = DATA / "line_history.json"
MEMORY = DATA / "memory.json"
BOARD = DATA / "board.json"
RESULTS = DATA / "results.json"

# A pick only goes live at this confidence or above. Everything below is
# still recorded as a shadow pick so the model can find out whether the
# threshold is set in the right place.
LIVE_THRESHOLD = 8.0
TARGET_PICKS = 6


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _save(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False))
    tmp.replace(path)


# ---------------------------------------------------------------- picks

def load_picks() -> list[dict]:
    return _load(PICKS, [])


def save_picks(picks: list[dict]) -> None:
    _save(PICKS, picks)


def new_pick(season: int, week: int, event_id: str, matchup: str,
             kickoff: str, market: str, period: str, side: str,
             line: float | None, price: int, confidence: float,
             units: float, rationale: str, factors: dict,
             model_number: float | None = None) -> dict:
    """
    market: spread | total | moneyline
    period: full | h1 | h2
    side:   team name, or "Over" / "Under"
    """
    return {
        "id": uuid.uuid4().hex[:12],
        "season": season,
        "week": week,
        "event_id": event_id,
        "matchup": matchup,
        "kickoff": kickoff,
        "market": market,
        "period": period,
        "side": side,
        "line": line,
        "price": price,
        "confidence": round(float(confidence), 1),
        "live": float(confidence) >= LIVE_THRESHOLD,
        "units": float(units),
        "model_number": model_number,
        "edge": (round(model_number - line, 2)
                 if model_number is not None and line is not None else None),
        "rationale": rationale,
        "factors": factors,
        "placed_at": now_iso(),
        "close_line": None,
        "close_price": None,
        "clv_points": None,
        "clv_cents": None,
        "result": "pending",
        "units_net": 0.0,
        "graded_at": None,
        "grader_note": None,
    }


def live_picks(picks: list[dict], season: int | None = None,
               week: int | None = None) -> list[dict]:
    out = [p for p in picks if p.get("live")]
    if season is not None:
        out = [p for p in out if p.get("season") == season]
    if week is not None:
        out = [p for p in out if p.get("week") == week]
    return out


# ---------------------------------------------------------------- board

def load_board() -> dict:
    return _load(BOARD, {"fetched_at": None, "games": []})


def save_board(games: list[dict], quota: dict | None = None) -> None:
    _save(BOARD, {"fetched_at": now_iso(), "quota": quota or {}, "games": games})


# ------------------------------------------------------- line history

def load_line_history() -> dict:
    return _load(LINE_HISTORY, {})


def append_line_snapshot(event_id: str, snapshot: dict) -> None:
    """One entry per event per fetch, so movement is reconstructable."""
    hist = load_line_history()
    series = hist.setdefault(event_id, [])
    stamped = {"at": now_iso(), **snapshot}
    if series:
        prev = {k: v for k, v in series[-1].items() if k != "at"}
        if prev == snapshot:
            return  # nothing moved, do not pad the file
    series.append(stamped)
    _save(LINE_HISTORY, hist)


# --------------------------------------------------------------- results

def load_results() -> dict:
    return _load(RESULTS, {})


def save_results(results: dict) -> None:
    _save(RESULTS, results)


# ---------------------------------------------------------------- memory

DEFAULT_MEMORY = {
    "updated_at": None,
    "seasons_tracked": [],
    "calibration": [],
    "by_market": [],
    "by_period": [],
    "by_role": [],
    "factor_scorecard": {},
    "lessons": [],
    "active_adjustments": [],
    "notes": (
        "Written by the grader after each slate settles. The handicapper is "
        "required to read this file before rating any pick."
    ),
}


def load_memory() -> dict:
    mem = _load(MEMORY, dict(DEFAULT_MEMORY))
    for k, v in DEFAULT_MEMORY.items():
        mem.setdefault(k, v)
    return mem


def save_memory(mem: dict) -> None:
    mem["updated_at"] = now_iso()
    _save(MEMORY, mem)


def add_lesson(text: str, season: int, week: int,
               evidence: str | None = None) -> None:
    mem = load_memory()
    mem["lessons"].insert(0, {
        "at": now_iso(),
        "season": season,
        "week": week,
        "lesson": text,
        "evidence": evidence,
    })
    mem["lessons"] = mem["lessons"][:60]
    save_memory(mem)
