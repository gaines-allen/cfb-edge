"""
The running card, and the confidence scale it displays.

Two things are being guarded. First, that a lean the market has caught up
to actually leaves the card and says so, because a card that silently
reshuffles is how a tout claims they were never wrong. Second, that the
explainer's numbers come from the live measurement rather than the copy,
since dispersion is remeasured weekly and prose does not update itself.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import build_running_card as RC  # noqa: E402
import build_site as B  # noqa: E402
from lib.model import load_calibration, suggested_confidence, z_score  # noqa: E402


# ------------------------------------ confidence is edge, restated

def test_confidence_is_a_pure_function_of_sigma():
    """
    The site says confidence is 3.5 plus 1.5 a sigma. If that stops being
    true the explainer becomes a lie, so it is pinned here.
    """
    cal = {"spreads": {"bias": 0.0, "sigma": 2.724},
           "totals": {"bias": 0.0, "sigma": 3.292}}
    for edge in (0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
        z = z_score(edge, "spread", cal)
        expected = round(min(3.5 + min(abs(z), 4.0) * 1.5, 7.5), 1)
        assert suggested_confidence(edge, "spread", True, cal) == expected


def test_the_same_points_mean_different_things_by_market():
    """
    The reason the page leads with confidence rather than points. Six
    points is a bigger deal on a spread than on a total, because totals
    swing wider.
    """
    cal = {"spreads": {"bias": 0.0, "sigma": 2.724},
           "totals": {"bias": 0.0, "sigma": 3.292}}
    spread = suggested_confidence(6.0, "spread", True, cal)
    total = suggested_confidence(6.0, "total", True, cal)
    assert spread > total


def test_confidence_still_stops_below_the_publish_line():
    cal = {"spreads": {"bias": 0.0, "sigma": 0.5}}
    assert suggested_confidence(40.0, "spread", True, cal) == 7.5
    assert 7.5 < 8.0


def test_the_explainer_reads_its_numbers_from_the_measurement():
    """
    The typical miss is remeasured every week. Numbers typed into the copy
    would drift away from the arithmetic they claim to describe.
    """
    scale = B.build_payload()["scale"]
    cal = load_calibration()
    assert scale["spread_gap"] == (cal.get("spreads") or {}).get("sigma")
    assert scale["total_gap"] == (cal.get("totals") or {}).get("sigma")
    for token in ("{spread_gap}", "{total_gap}", "{sample}"):
        assert token in B.VOICE["edge_body_3"]
    assert "{cap}" in B.VOICE["edge_body_6"]
    assert "{publish}" in B.VOICE["edge_body_6"]


def test_the_explainer_says_the_two_numbers_are_one_fact():
    body = " ".join(B.VOICE[f"edge_body_{i}"] for i in range(1, 7)).lower()
    assert "one fact said twice" in body
    # The explainer has to teach the scale without naming it. A reader who
    # has to look up a Greek letter to read a betting page has been sold a
    # lecture, not a pick.
    assert "sigma" not in body


# ----------------------------------------------- the card moves

def lean(event_id, side, market="spread", conf=6.0, sigma=1.7, line=-6.5):
    return {"event_id": event_id, "matchup": f"A{event_id} @ B{event_id}",
            "kickoff": "2026-08-29T16:00:00Z",
            "candidates": [{"market": market, "period": "full", "side": side,
                            "bet_line": line, "market_line": line,
                            "price": -110, "floor_confidence": conf,
                            "edge_sigma": sigma, "edge_points": sigma * 2.7,
                            "model_number": line - 2}]}


def slate_of(rows, at="2026-08-24T14:00:00+00:00"):
    return {"season": 2026, "week": 1, "board_fetched_at": at, "slate": rows}


def build(tmp_path, slates):
    """Run the builder over a sequence of daily slates."""
    import os
    slate_path = ROOT / "data" / "slate.json"
    run_path = ROOT / "data" / "running_card.json"
    keep_slate = slate_path.read_bytes()
    keep_run = run_path.read_bytes() if run_path.exists() else None
    env = dict(os.environ)
    try:
        run_path.unlink(missing_ok=True)
        for s in slates:
            slate_path.write_text(json.dumps(s))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "build_running_card.py")],
                capture_output=True, text=True, cwd=str(ROOT), env=env)
            assert proc.returncode == 0, proc.stderr[-400:]
        return json.loads(run_path.read_text())
    finally:
        slate_path.write_bytes(keep_slate)
        if keep_run is not None:
            run_path.write_bytes(keep_run)
        else:
            run_path.unlink(missing_ok=True)


def test_the_card_holds_the_best_six(tmp_path):
    rows = [lean(str(i), f"Side{i}", conf=7.0 - i * 0.2) for i in range(9)]
    got = build(tmp_path, [slate_of(rows)])
    assert len(got["card"]) == 6
    first = got["leans"][got["card"][0]]
    assert first["confidence"] == 7.0


def test_a_lean_the_market_catches_falls_off_and_is_named(tmp_path):
    """
    The whole reason this file exists. Monday's best lean sits at 7.0. By
    Thursday the market has moved to the model's number, the gap is gone,
    and it has to leave the card visibly rather than quietly.
    """
    monday = [lean("hot", "Fading", conf=7.0, sigma=2.3)] + \
             [lean(str(i), f"Side{i}", conf=6.5 - i * 0.1) for i in range(8)]
    thursday = [lean("hot", "Fading", conf=3.8, sigma=0.2)] + \
               [lean(str(i), f"Side{i}", conf=6.5 - i * 0.1) for i in range(8)]
    got = build(tmp_path, [slate_of(monday),
                           slate_of(thursday, "2026-08-27T14:00:00+00:00")])
    key = next(k for k in got["leans"] if "Fading" in k)
    entry = got["leans"][key]
    assert entry["peak_rank"] == 1
    assert entry["rank"] > 6
    assert key in got["dropped"]
    assert key not in got["card"]


def test_a_lean_keeps_the_number_it_entered_at(tmp_path):
    """
    Movement is the story, so the first reading has to survive later runs
    untouched, the same way a published pick freezes at its number.
    """
    monday = [lean("m", "Holding", conf=6.8, sigma=2.2, line=-6.5)]
    friday = [lean("m", "Holding", conf=5.2, sigma=1.1, line=-8.5)]
    got = build(tmp_path, [slate_of(monday),
                           slate_of(friday, "2026-08-28T14:00:00+00:00")])
    e = got["leans"][got["card"][0]]
    assert e["first_confidence"] == 6.8
    assert e["first_line"] == -6.5
    assert e["confidence"] == 5.2
    assert e["line"] == -8.5
    assert len(e["history"]) == 2


def test_a_new_week_starts_a_clean_card(tmp_path):
    """Carrying last week's leans would make a dead entry look like it held."""
    wk1 = slate_of([lean("a", "Old", conf=7.0)])
    wk2 = slate_of([lean("b", "New", conf=6.0)], "2026-09-01T14:00:00+00:00")
    wk2["week"] = 2
    got = build(tmp_path, [wk1, wk2])
    assert got["week"] == 2
    assert all("Old" not in k for k in got["leans"])


def test_two_runs_on_one_pull_do_not_double_the_history(tmp_path):
    """The daily job can be dispatched twice. That is not 2 days of data."""
    s = slate_of([lean("a", "Once", conf=6.0)])
    got = build(tmp_path, [s, s])
    assert len(got["leans"][got["card"][0]]["history"]) == 1


# ------------------------------------------- what the page shows

def test_the_page_says_leans_are_not_picks():
    note = B.VOICE["running_note"].lower()
    assert "not picks" in note


def test_stacked_games_are_called_out():
    """
    6 leans across 4 games is not 6 independent bets, and the page says so
    rather than letting the count imply more than it holds.
    """
    assert "{games}" in B.VOICE["running_stacked"]
    assert "new Set(R.card.map" in B.HTML


def test_the_running_card_hides_when_empty():
    assert 'hide("running")' in B.HTML


def test_the_daily_job_updates_the_running_card():
    daily = (ROOT / ".github" / "workflows" / "daily.yml").read_text()
    assert "build_running_card.py" in daily
    assert daily.index("make_slate.py") < daily.index("build_running_card.py")
    assert daily.index("build_running_card.py") < daily.index("build_site.py")
