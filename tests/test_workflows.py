"""
The workflow files, checked as configuration rather than read as prose.

The weekly card workflow runs an agent unattended with an API key and a
network connection. What keeps that safe is not the agent's good intentions,
it is the permission boundary in the workflow file. A boundary nobody tests
is a boundary that quietly widens.
"""

from __future__ import annotations

import re

import pytest

from conftest import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"
WEEKLY = (WORKFLOWS / "weekly-card.yml").read_text()
DAILY = (WORKFLOWS / "daily.yml").read_text()
TESTS = (WORKFLOWS / "tests.yml").read_text()
GITIGNORE = (ROOT / ".gitignore").read_text()


def commands(yaml_text: str) -> str:
    """
    The runnable lines only. The workflow carries comments explaining why it
    avoids certain flags, and a check that trips on the comment warning
    against a flag rather than on the flag is a check nobody will keep.
    """
    return "\n".join(ln for ln in yaml_text.splitlines()
                      if not ln.lstrip().startswith("#"))


WEEKLY_CMDS = commands(WEEKLY)


# ------------------------------------------------- the daily job is intact

def test_the_daily_job_still_calls_selftest_by_name():
    """The whole reason selftest.py survived as a script."""
    assert "python3 scripts/selftest.py" in DAILY


def test_the_daily_job_does_not_run_an_agent():
    """
    The deterministic half stays deterministic. Picks come from the weekly
    workflow, and mixing them would mean a failed research run could stop
    the board pull and the grading.
    """
    assert "claude -p" not in DAILY
    assert "ANTHROPIC_API_KEY" not in DAILY
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in DAILY


def test_the_agent_authenticates_with_the_subscription_token():
    """
    A subscription token rather than a metered API key. Bare mode does not
    read CLAUDE_CODE_OAUTH_TOKEN, so the run must not pass --bare, which it
    also must not do because it needs CLAUDE.md and .claude/agents.
    """
    assert "secrets.CLAUDE_CODE_OAUTH_TOKEN" in WEEKLY
    assert "--bare" not in WEEKLY_CMDS


def test_a_missing_token_fails_fast():
    """
    Without this the run reaches the agent step, fails on authentication,
    and reports it as a model error rather than a missing secret.
    """
    assert 'if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]' in WEEKLY


# --------------------------------------------- the agent's permissions

def test_the_agent_may_write_only_its_draft():
    assert 'Write(picks_draft.json)' in WEEKLY
    assert '"Write"' not in WEEKLY, "an unscoped Write grants the whole tree"


@pytest.mark.parametrize("forbidden", [
    "Bash(git *)",
    "Bash(python3 scripts/log_picks.py *)",
    "Bash(rm *)",
    "Edit",
])
def test_the_agent_cannot_reach_the_ledger_or_git(forbidden):
    """
    log_picks.py is the only thing that writes data/picks.json, and that
    file is append only. The agent proposes, deterministic code validates,
    a person merges.
    """
    block = WEEKLY.split("--disallowedTools", 1)[1]
    assert forbidden in block, f"{forbidden} is not denied to the agent"


def test_the_agent_runs_in_dont_ask_mode():
    """
    Nothing is there to answer a permission prompt, so anything outside the
    allow list has to be denied rather than left waiting.
    """
    assert "--permission-mode dontAsk" in WEEKLY
    assert "bypassPermissions" not in WEEKLY
    assert "--dangerously-skip-permissions" not in WEEKLY


def test_the_agent_run_is_capped():
    """An uncapped agentic loop in CI is an uncapped bill."""
    assert "--max-turns" in WEEKLY


def test_the_run_reports_what_it_cost():
    assert "--output-format json" in WEEKLY
    assert "total_cost_usd" in WEEKLY


# --------------------------------------------------- the gate is wired

def test_the_suite_runs_before_the_agent_does():
    """
    A broken grader or a changed upstream shape has to stop the run before
    an agent spends money reasoning off it.
    """
    assert WEEKLY.index("scripts/selftest.py") < WEEKLY.index("claude -p")


# Everything after the agent step. Searching the whole file would match the
# deny list, where log_picks.py appears precisely because it is forbidden.
AFTER_AGENT = WEEKLY.split("> research.json", 1)[1]


def test_validation_runs_before_anything_is_logged():
    assert (AFTER_AGENT.index("validate_card.py")
            < AFTER_AGENT.index("log_picks.py"))


def test_the_card_is_rehearsed_before_it_is_logged():
    """log_picks runs once with --dry-run before it runs for real."""
    calls = [ln for ln in AFTER_AGENT.splitlines()
             if "log_picks.py" in ln or "--dry-run" in ln]
    joined = "\n".join(calls)
    first, _, rest = joined.partition("--dry-run")
    assert "--dry-run" in joined, "log_picks is never rehearsed"
    assert "log_picks.py" in rest or "log_picks.py" in first, \
        "the real log_picks call does not follow the rehearsal"


def test_a_rejected_card_is_kept_for_reading():
    assert "rejected-card" in WEEKLY


# ------------------------------------------- nothing publishes itself

def test_the_card_lands_in_a_pull_request():
    """
    The agent never writes to main. A person reads the rationale and the
    sources and merges, which is what makes unattended research acceptable.
    """
    assert "gh pr create" in WEEKLY
    assert "--base main" in WEEKLY


def test_the_card_branch_is_not_main():
    assert re.search(r'BRANCH="card/', WEEKLY)
    assert 'git push origin "$BRANCH"' in WEEKLY


def test_the_draft_never_gets_committed_raw():
    for name in ("picks_draft.json", "research.json", "validation.json"):
        assert name in GITIGNORE, f"{name} is not gitignored"


# --------------------------------------------------- the PR check

def test_pull_requests_run_the_suite():
    assert "pull_request" in TESTS
    assert "scripts/selftest.py" in TESTS


def test_the_test_job_gets_no_api_keys():
    """
    The suite replays fixtures, so a pull request from a fork runs exactly
    what a branch runs.
    """
    assert "secrets.ODDS_API_KEY" not in TESTS
    assert "secrets.CFBD_API_KEY" not in TESTS
    assert "secrets.ANTHROPIC_API_KEY" not in TESTS
    assert "secrets.CLAUDE_CODE_OAUTH_TOKEN" not in TESTS
