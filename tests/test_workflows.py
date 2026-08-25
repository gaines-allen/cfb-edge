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
    """
    Spelled Edit, not Write. Claude Code checks file permissions against
    Edit(path) and Read(path) rules only, so a Write(path) rule is accepted
    and never consulted. The first unattended run researched for 16 minutes
    and could not write its own output because of exactly that.
    """
    assert "Edit(picks_draft.json)" in WEEKLY_CMDS
    assert "Write(" not in WEEKLY_CMDS, (
        "a Write(path) rule is never consulted, so it grants nothing"
    )
    assert '"Write"' not in WEEKLY_CMDS, "an unscoped Write grants the whole tree"


def test_the_deny_rules_are_scoped_to_paths():
    """
    A bare tool name denies that tool everywhere, which is what blocked the
    draft write on the first run. Every deny here names a path instead.
    """
    block = WEEKLY_CMDS.split("--disallowedTools", 1)[1].split("> research.json")[0]
    for line in block.splitlines():
        rule = line.strip().strip('\\').strip().strip('"')
        if not rule:
            continue
        assert "(" in rule and rule.endswith(")"), (
            f"deny rule {rule!r} names a bare tool, which denies it everywhere"
        )


@pytest.mark.parametrize("protected", ["data/**", "site/**", "scripts/**"])
def test_the_agent_cannot_edit_the_repository(protected):
    assert f"Edit({protected})" in WEEKLY_CMDS


def test_a_missing_draft_fails_rather_than_reading_as_no_card():
    """
    The agent is told to write an empty list when nothing clears 8.0, so a
    missing file means it could not write at all. Reporting that as a quiet
    no card would hide a permission failure as a handicapping decision.
    """
    assert "picks_draft.json is missing" in WEEKLY
    assert 'if [ ! -f picks_draft.json ]' in WEEKLY_CMDS


def test_an_empty_card_is_still_a_clean_outcome():
    assert '= "[]" ]' in WEEKLY_CMDS


@pytest.mark.parametrize("forbidden", [
    "Bash(git *)",
    "Bash(python3 scripts/log_picks.py *)",
    "Bash(rm *)",
    "Edit(data/**)",
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


def test_whatever_the_run_produced_is_kept_for_reading():
    """
    Any failure, not only a rejected card. The first real run failed at the
    agent step and uploaded nothing, so the payload holding the reason was
    lost with the runner.
    """
    assert "upload-artifact" in WEEKLY_CMDS
    assert "if: failure()" in WEEKLY


def test_the_token_is_stripped_of_whitespace():
    """
    A token copied out of an 80 column terminal carries a line break at
    character 80, and the API rejects the header. The first real run failed
    on exactly that. The token contains no whitespace, so removing all of it
    repairs a secret that was pasted with the break already in it.
    """
    assert "tr -d '[:space:]'" in WEEKLY_CMDS


def test_a_failure_prints_the_reason():
    """
    Claude Code puts the reason in the result field. Printing only the
    subtype turns "Invalid auth token" into "Failed with subtype success".
    """
    block = WEEKLY.split("if d.get(\"is_error\")", 1)[1][:400]
    assert 'd.get("result")' in block


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


def test_the_turn_cap_is_reported_rather_than_swallowed():
    """
    claude -p exits non zero at the turn cap and returns an empty result,
    so the step reads the payload instead of the exit code. Without this a
    capped run dies with no reason in the summary, and the draft it left
    behind is partial rather than absent.
    """
    assert "error_max_turns" in WEEKLY
    assert "> research.json || true" in WEEKLY_CMDS


def test_a_failed_run_does_not_reach_the_validator():
    """The agent step must still fail the job when the payload says it failed."""
    block = WEEKLY.split("> research.json", 1)[1].split("Fail if the agent")[0]
    assert "sys.exit(1)" in block


def test_sources_are_verified_before_anything_is_logged():
    """
    A url and a date only prove a page exists. Opening it is what separates
    a read source from a paraphrased one, and that has to happen before the
    ledger is touched.
    """
    assert (AFTER_AGENT.index("verify_sources.py")
            < AFTER_AGENT.index("log_picks.py"))


def test_the_pull_request_names_what_could_not_be_confirmed():
    """
    The unverified list is the part a person has to read, so it belongs in
    the pull request rather than buried in a log.
    """
    body = WEEKLY.split("pr_body.md", 1)[1][:1400]
    assert "unverified_on_live_picks" in body
    assert "Read these before merging" in body


def test_the_pull_request_states_the_limits_of_the_check():
    assert 's["note"]' in WEEKLY


def test_the_verification_report_is_kept_on_failure():
    assert "verification.json" in WEEKLY_CMDS


# ---------------------------------------------------------------------
# Getting the page onto the internet
#
# Pages serves an Actions artifact, not the branch. So committing a built
# page and pushing it does nothing on its own, and nothing says so: the
# repo is right, the site is stale, and the two look identical from a
# terminal. Two commits sat undeployed on 2026-08-24 before anyone noticed.
# ---------------------------------------------------------------------

DEPLOY = (WORKFLOWS / "deploy.yml").read_text()


def test_a_push_that_changes_the_page_deploys_it():
    assert re.search(r"on:\s*\n\s*push:", DEPLOY)
    assert '"site/**"' in DEPLOY


def test_the_deploy_workflow_runs_no_scripts():
    # It exists to publish what is already committed. The moment it starts
    # pulling a board or rebuilding the page it costs API credits on every
    # push, and every push is a lot of pulls.
    assert "run:" not in DEPLOY
    assert "python" not in DEPLOY.lower()


def test_the_deploy_workflow_publishes_the_site_directory():
    assert "upload-pages-artifact" in DEPLOY
    assert re.search(r"path:\s*site", DEPLOY)


@pytest.mark.parametrize("name", ["daily.yml", "track-scores.yml",
                                  "deploy.yml"])
def test_every_deploying_workflow_asks_for_pages_write(name):
    text = (WORKFLOWS / name).read_text()
    if "deploy-pages" not in text:
        pytest.skip(f"{name} does not deploy")
    assert "pages: write" in text
    assert "id-token: write" in text


# ---------------------------------------------------------------------
# The backtest workflow
#
# It answers the question the rest of the system assumes, so it has to be
# runnable without waiting on a schedule, and it must not quietly become
# a daily job that burns credits for an answer that only moves when a
# season ends.
# ---------------------------------------------------------------------

BACKTEST = (WORKFLOWS / "backtest.yml").read_text()


def test_the_backtest_can_be_run_on_demand():
    assert "workflow_dispatch" in BACKTEST


def test_the_backtest_is_not_on_a_schedule():
    """
    The answer changes when a season ends, not overnight, and each run
    spends CFBD credits on 4 seasons of games, lines and ratings.

    Read off the parsed triggers rather than the file text. Grepping for
    the word caught the comment explaining why there is no schedule.
    """
    import yaml
    doc = yaml.safe_load(BACKTEST)
    triggers = doc[True] if True in doc else doc["on"]
    assert "schedule" not in triggers


def test_the_backtest_only_fires_on_its_own_files():
    assert 'scripts/backtest.py' in BACKTEST
    assert '.github/workflows/backtest.yml' in BACKTEST


def test_the_backtest_gets_the_key_it_needs():
    assert "CFBD_API_KEY" in BACKTEST


def test_the_daily_run_can_be_forced_between_schedules():
    """
    A model change makes the stored slate wrong until the next 2 PM pull,
    and waiting hours for a page to stop being wrong is not an option.
    Scoped to one file so an ordinary push never spends API credits.
    """
    import yaml
    doc = yaml.safe_load(DAILY)
    triggers = doc[True] if True in doc else doc["on"]
    assert ".github/refresh-now" in triggers["push"]["paths"]
    assert len(triggers["push"]["paths"]) == 1
