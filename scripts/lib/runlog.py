"""
Structured logging for every run.

Two problems this solves. The first is that a failing daily job printed a
stack trace and nothing else, so nobody could tell which endpoint died or
how many credits it burned on the way down. The second is that credit spend
was invisible until the monthly quota ran out. The Odds API free tier is 500
credits a month and CFBD is 1,000 calls, so a run that quietly costs 30
instead of 3 matters.

Every record carries the run id, the endpoint, the credit cost and the row
count. Records go to stderr as one JSON object per line, and to a JSONL file
under data/.cache/runs/ that is gitignored. Nothing goes to stdout, because
the GitHub Action and the four agents parse stdout and that contract does
not change.

    log = RunLog("fetch_odds", dry_run=False)
    with log.call("/sports/americanfootball_ncaaf/odds") as c:
        games = client.board()
        c.rows = len(games)
        c.credit_cost = client.quota.last_cost
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "data" / ".cache" / "runs"

_RUN_ID: str | None = None


def run_id() -> str:
    """
    One id per process, stable across every record it writes.

    RUN_ID from the environment wins, so a GitHub Actions run can thread its
    own id through and the logs from all 6 steps join up.
    """
    global _RUN_ID
    if _RUN_ID is None:
        _RUN_ID = os.environ.get("RUN_ID") or uuid.uuid4().hex[:12]
    return _RUN_ID


def reset_run_id() -> None:
    """Only the tests use this, so 2 runs in one process do not share an id."""
    global _RUN_ID
    _RUN_ID = None


class Call:
    """
    One external request, held open while it runs so the caller can fill in
    what it cost and how much came back.

    credit_cost is the charged amount, not an estimate. The Odds API reports
    it in the x-requests-last header. CFBD charges 1 per live call and 0 on
    a cache hit, so a cached call records 0 and sets cached to True.
    """

    def __init__(self, endpoint: str, params: dict | None = None):
        self.endpoint = endpoint
        self.params = params or {}
        self.rows: int | None = None
        self.credit_cost: int | None = None
        self.credits_remaining: int | None = None
        self.cached: bool = False
        self.status: str = "ok"
        self.error: str | None = None
        self.started = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


class _CallContext:
    def __init__(self, log: "RunLog", call: Call):
        self.log = log
        self.call = call

    def __enter__(self) -> Call:
        return self.call

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.call.status = "error"
            self.call.error = f"{exc_type.__name__}: {exc}"
        self.log._emit("api_call", {
            "endpoint": self.call.endpoint,
            "params": _safe_params(self.call.params),
            "rows": self.call.rows,
            "credit_cost": self.call.credit_cost,
            "credits_remaining": self.call.credits_remaining,
            "cached": self.call.cached,
            "ms": self.call.ms,
            "status": self.call.status,
            "error": self.call.error,
        })
        return False


def _safe_params(params: dict) -> dict:
    """Never echo a key. The Odds API takes apiKey as a query parameter."""
    out = {}
    for k, v in params.items():
        if k.lower() in ("apikey", "api_key", "key", "authorization", "token"):
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


class RunLog:
    def __init__(self, script: str, dry_run: bool = False,
                 to_stderr: bool = True, to_file: bool = True):
        self.script = script
        self.dry_run = dry_run
        self.run_id = run_id()
        self.to_stderr = to_stderr
        self.to_file = to_file
        self._credits = 0
        self._path: Path | None = None
        if to_file:
            try:
                RUN_DIR.mkdir(parents=True, exist_ok=True)
                self._path = RUN_DIR / f"{self.run_id}.jsonl"
            except OSError:
                self._path = None

    # ---------- emit ----------

    def _emit(self, event: str, fields: dict[str, Any]) -> None:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": self.run_id,
            "script": self.script,
            "event": event,
            "dry_run": self.dry_run,
        }
        rec.update(fields)
        if fields.get("credit_cost"):
            self._credits += int(fields["credit_cost"])
        line = json.dumps(rec, default=str)
        if self.to_stderr:
            print(line, file=sys.stderr)
        if self._path is not None:
            try:
                with self._path.open("a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    # ---------- public ----------

    def call(self, endpoint: str, params: dict | None = None) -> _CallContext:
        return _CallContext(self, Call(endpoint, params))

    def event(self, event: str, **fields: Any) -> None:
        self._emit(event, fields)

    def write(self, path: str | Path, rows: int | None = None,
              size_bytes: int | None = None, skipped: bool = False) -> None:
        """
        Every file write goes through here, including the ones a dry run
        skips. skipped True is what a dry run looks like in the log.
        """
        self._emit("write", {
            "path": str(path),
            "rows": rows,
            "size_bytes": size_bytes,
            "skipped": skipped,
        })

    def rows(self, what: str, n: int, **fields: Any) -> None:
        self._emit("rows", {"what": what, "rows": n, **fields})

    def error(self, message: str, **fields: Any) -> None:
        self._emit("error", {"message": message, **fields})

    def finish(self, status: str = "ok", **fields: Any) -> None:
        self._emit("finish", {"status": status,
                              "credits_spent": self._credits, **fields})

    @property
    def credits_spent(self) -> int:
        return self._credits
