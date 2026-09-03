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
import os
from pathlib import Path
from typing import Any

from .runlog import RunLog

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

# Tests point this at a temp file to seed a ledger. Nothing in
# production sets it, and dry runs never write, so the override cannot
# reach the real ledger by accident.
PICKS = Path(os.environ.get("CFB_EDGE_PICKS", str(DATA / "picks.json")))
LINE_HISTORY = DATA / "line_history.json"
MEMORY = DATA / "memory.json"
BOARD = DATA / "board.json"
RESULTS = DATA / "results.json"

# A pick only goes live at this confidence or above. Everything below is
# still recorded as a shadow pick so the model can find out whether the
# threshold is set in the right place.
# The publish line. A pick at or above this is staked and goes on the
# card; below it rides as a shadow pick that is graded but not staked.
#
# Lowered from 8.0 on 28 August. What stops the ratings model publishing
# on its own is not this number, it is the rule in card_rules.py that a
# live pick must name a researched factor beyond the rating gap and cite
# a source that verify_sources.py can open and confirm. That rule keys off
# whether a pick is live, not off any particular value here, so it holds
# at 7.0 exactly as it held at 8.0.
LIVE_THRESHOLD = 7.0
TARGET_PICKS = 6


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------- dry run

# One switch, checked by every write in the repo. A dry run has to leave
# data/ and site/ byte identical, so every writer routes through here rather
# than calling write_text on its own.
_DRY_RUN = False
_LOG: RunLog | None = None


def set_dry_run(on: bool, log: RunLog | None = None) -> None:
    global _DRY_RUN, _LOG
    _DRY_RUN = bool(on)
    if log is not None:
        _LOG = log


def is_dry_run() -> bool:
    return _DRY_RUN


def _report(path: Path, payload: str, wrote: bool) -> None:
    if _LOG is not None:
        _LOG.write(path, size_bytes=len(payload.encode()), skipped=not wrote)


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def write_text(path: Path, text: str) -> bool:
    """
    The one place anything in this repo touches disk. Returns True when it
    wrote and False when a dry run skipped it.
    """
    if _DRY_RUN:
        _report(path, text, wrote=False)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    _report(path, text, wrote=True)
    return True


def write_json(path: Path, obj: Any, indent: int | None = 2,
               sort_keys: bool = False) -> bool:
    return write_text(path, json.dumps(obj, indent=indent, sort_keys=sort_keys))


def _save(path: Path, obj: Any) -> None:
    write_json(path, obj)


# ---------------------------------------------------------------- picks

def load_picks() -> list[dict]:
    return _load(PICKS, [])


def save_picks(picks: list[dict]) -> None:
    _save(PICKS, picks)


def new_pick(season: int, week: int, event_id: str, matchup: str,
             kickoff: str, market: str, period: str, side: str,
             line: float | None, price: int, confidence: float,
             units: float, rationale: str, factors: dict,
             model_number: float | None = None,
             sources: list[dict] | None = None,
             live: bool | None = None) -> dict:
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
        # The card is the 6 best rated games of the week, so the caller
        # decides which rows are on it after ranking them all. The
        # threshold stays as the fallback for any caller that does not
        # rank, and as the floor below which nothing is publishable at
        # all.
        "live": (float(confidence) >= LIVE_THRESHOLD if live is None
                 else bool(live)),
        "units": float(units),
        "model_number": model_number,
        "edge": (round(model_number - line, 2)
                 if model_number is not None and line is not None else None),
        "rationale": rationale,
        "factors": factors,
        # Where the researched claim came from, so the grader can tell a
        # sourced read from a hunch when it scores the factor scorecard.
        "sources": sources or [],
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
    "research_scorecard": {},
    "lessons": [],
    "active_adjustments": [],
    "notes": (
        "Written by the grader after each slate settles. The handicapper is "
        "required to read this file before rating any pick. Every row "
        "carries a verdict beside its record. Read the verdict. A row "
        "marked no_signal is not weak evidence, it is no evidence: its 95 "
        "percent interval still contains the 52.4 percent needed to break "
        "even. Only beats_breakeven and below_breakeven are evidence, and "
        "both take several hundred decided picks to appear."
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
