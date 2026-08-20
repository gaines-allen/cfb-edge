"""
Structured logging.

The record has to carry the run id, the endpoint, the credit cost and the
row count, because a failing daily job used to print a stack trace and
nothing else. It also has to keep off stdout, which the GitHub Action and
the four agents parse.
"""

from __future__ import annotations

import json

from lib.runlog import RunLog, reset_run_id, run_id


def records(capsys) -> list[dict]:
    err = capsys.readouterr().err
    return [json.loads(line) for line in err.splitlines() if line.startswith("{")]


def test_a_call_record_carries_the_four_required_fields(capsys):
    log = RunLog("t", to_file=False)
    with log.call("/sports/x/odds", {"markets": "spreads"}) as c:
        c.rows = 110
        c.credit_cost = 3
    rec = records(capsys)[0]
    for field in ("run_id", "endpoint", "credit_cost", "rows"):
        assert rec[field] is not None, field
    assert rec["endpoint"] == "/sports/x/odds"
    assert rec["credit_cost"] == 3
    assert rec["rows"] == 110


def test_the_run_id_is_stable_across_records(capsys):
    log = RunLog("t", to_file=False)
    log.event("one")
    log.event("two")
    recs = records(capsys)
    assert recs[0]["run_id"] == recs[1]["run_id"]


def test_run_id_comes_from_the_environment_when_set(monkeypatch):
    monkeypatch.setenv("RUN_ID", "abc123abc123")
    reset_run_id()
    try:
        assert run_id() == "abc123abc123"
    finally:
        reset_run_id()


def test_nothing_reaches_stdout(capsys):
    """
    The workflow and the agents parse stdout, so that contract does not
    change. Every structured record goes to stderr.
    """
    log = RunLog("t", to_file=False)
    log.event("hello", rows=1)
    log.finish()
    assert capsys.readouterr().out == ""


def test_a_key_is_never_echoed(capsys):
    """The Odds API takes apiKey as a query parameter."""
    log = RunLog("t", to_file=False)
    with log.call("/odds", {"apiKey": "supersecret", "markets": "spreads"}):
        pass
    line = capsys.readouterr().err
    assert "supersecret" not in line
    assert "<redacted>" in line


def test_an_exception_is_recorded_and_reraised(capsys):
    log = RunLog("t", to_file=False)
    try:
        with log.call("/boom"):
            raise ValueError("upstream fell over")
    except ValueError:
        pass
    rec = records(capsys)[0]
    assert rec["status"] == "error"
    assert "upstream fell over" in rec["error"]


def test_credits_accumulate_across_a_run(capsys):
    log = RunLog("t", to_file=False)
    for _ in range(3):
        with log.call("/odds") as c:
            c.credit_cost = 3
    assert log.credits_spent == 9
    log.finish()
    assert records(capsys)[-1]["credits_spent"] == 9


def test_a_skipped_write_is_visible(capsys):
    log = RunLog("t", dry_run=True, to_file=False)
    log.write("data/board.json", rows=110, skipped=True)
    rec = records(capsys)[0]
    assert rec["skipped"] is True
    assert rec["dry_run"] is True
