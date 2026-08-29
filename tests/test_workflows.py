"""
The workflow files, checked as configuration rather than read as prose.

The weekly card workflow runs an agent unattended with an API key and a
network connection. What keeps that safe is not the agent's good intentions,
it is the permission boundary in the workflow file. A boundary nobody tests
is a boundary that quietly widens.
"""

from __future__ import annotations

import re
import sys

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


# ---------------------------------------------------------------------
# The environment
#
# pyyaml was imported by 2 tests here and declared nowhere. It happened to
# be installed locally, so the suite passed on this machine and failed on
# every CI runner for 4 commits, which also took the daily job down with
# it, because that job runs the same suite before it will publish.
#
# A test passing locally and failing in CI is the worst failure mode
# available: it looks like the code is fine.
# ---------------------------------------------------------------------

def test_every_import_the_suite_needs_is_declared():
    """
    Walks the real import graph with ast rather than a regex, because a
    regex over the source matched prose inside docstrings and reported a
    module named "the".

    Third party is decided by whether a module lives in site-packages,
    which is the only part of this that holds across versions. Asking
    whether it sits under the stdlib path does not: from 3.11 os and io
    are frozen into the interpreter and report an origin of "frozen", so
    that check called them undeclared dependencies and failed CI while
    passing here. sys.stdlib_module_names, the obvious answer, arrived in
    3.10 and CI still runs 3.9.
    """
    import ast
    import importlib.util

    declared = {
        re.split(r"[><=~\[]", line, 1)[0].strip().lower().replace("-", "_")
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    # Import name where it differs from the name pip installs under.
    aliases = {"yaml": "pyyaml", "dateutil": "python_dateutil",
               "bs4": "beautifulsoup4"}
    local = {p.stem for p in (ROOT / "scripts").rglob("*.py")}
    local |= {p.stem for p in (ROOT / "tests").rglob("*.py")}
    local |= {"lib", "conftest"}

    def is_third_party(name: str) -> bool:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError, ModuleNotFoundError):
            # Not importable here at all, so a runner will not have it
            # either unless it is declared.
            return True
        if spec is None:
            return True
        origin = getattr(spec, "origin", None) or ""
        paths = list(getattr(spec, "submodule_search_locations", None) or [])
        where = " ".join([origin] + paths)
        return "site-packages" in where or "dist-packages" in where

    missing = set()
    for path in sorted(list((ROOT / "tests").rglob("*.py"))
                       + list((ROOT / "scripts").rglob("*.py"))):
        tree = ast.parse(path.read_text(), filename=str(path))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module:
                    names.add(node.module.split(".")[0])
        for mod in names:
            if mod in local or mod == "__future__":
                continue
            if not is_third_party(mod):
                continue
            if aliases.get(mod, mod).lower() not in declared:
                missing.add(f"{mod} (in {path.name})")
    assert not missing, (
        f"imported but absent from requirements.txt: {sorted(missing)}. "
        f"This passes locally and fails on every runner.")


# ---------------------------------------------------------------------
# What the Friday card covers
#
# A CFBD week is not always a weekend. Week 1 of 2026 spans 29 August to
# 8 September and holds 44 games across two weekends. Handing the whole
# week to the Friday run would publish a card on 28 August whose plays
# mostly kick on 5 September.
# ---------------------------------------------------------------------

RESEARCH = (ROOT / ".claude" / "commands" / "research-card.md").read_text()


def para(anchor: str) -> str:
    """
    One scope paragraph, flattened.

    Anchored on the sentence rather than the bare token, because both
    tokens also appear in the line listing them and slicing from there
    returned "`scope thursday` or".
    """
    start = RESEARCH.index(anchor)
    end = RESEARCH.index("\n\n## ", start)
    return " ".join(RESEARCH[start:end].split())


def test_the_friday_card_covers_the_coming_weekend_not_the_whole_week():
    full = para("`scope full`, or no scope,")
    assert "within 4 days" in full
    assert "whole slate" not in full


def test_the_thursday_run_still_only_takes_the_early_games():
    thu = para("`scope thursday` is the early run")
    assert "kicking before Friday" in thu


def test_a_game_already_published_is_never_rated_twice():
    assert "already carrying a published pick" in RESEARCH


# ---------------------------------------------------------------------
# The watchdog
#
# Added after 26 August, when a failed run published nothing and the page
# sat stale for 3 hours with nobody looking. A watchdog that reports
# healthy when it cannot tell is worse than none, so these check the ways
# it could go quiet.
# ---------------------------------------------------------------------

WATCHDOG = (WORKFLOWS / "watchdog.yml").read_text()


def workflow_names() -> set:
    import yaml
    names = set()
    for f in WORKFLOWS.glob("*.yml"):
        doc = yaml.safe_load(f.read_text())
        if doc and doc.get("name"):
            names.add(doc["name"])
    return names


def test_every_workflow_the_watchdog_waits_on_actually_exists():
    """
    A workflow_run trigger naming a workflow that does not exist never
    fires and never says so. The first draft watched "Track scores". The
    file is called track-scores.yml and the workflow is named "Saturday
    tracking", so the one trigger meant to catch a silent failure would
    itself have failed silently.
    """
    import yaml
    doc = yaml.safe_load(WATCHDOG)
    triggers = doc[True] if True in doc else doc["on"]
    watched = set(triggers["workflow_run"]["workflows"])
    missing = watched - workflow_names()
    assert not missing, f"watching workflows that do not exist: {missing}"


def test_the_watchdog_watches_everything_that_publishes():
    import yaml
    doc = yaml.safe_load(WATCHDOG)
    triggers = doc[True] if True in doc else doc["on"]
    watched = set(triggers["workflow_run"]["workflows"])
    # Any workflow that can write to the repo is one whose failure means
    # the page did not move.
    for name, f in (("Daily update", "daily.yml"),
                    ("Weekly card", "weekly-card.yml"),
                    ("Saturday tracking", "track-scores.yml")):
        assert name in watched, f"{f} can publish and is unwatched"


def test_the_watchdog_can_open_and_close_an_issue():
    import yaml
    doc = yaml.safe_load(WATCHDOG)
    assert doc["jobs"]["check"]["permissions"].get("issues") == "write"
    assert "gh issue create" in WATCHDOG
    assert "gh issue close" in WATCHDOG
    # One alarm at a time, reopened as a comment rather than a second
    # issue, or a week of downtime becomes a week of duplicate mail.
    assert "gh issue comment" in WATCHDOG


def test_the_watchdog_runs_on_its_own_schedule_too():
    """
    workflow_run only fires when a run finishes. A cron that stops firing
    altogether, or Pages failing to deploy a successful build, produces no
    run to hang a trigger on.
    """
    import yaml
    doc = yaml.safe_load(WATCHDOG)
    triggers = doc[True] if True in doc else doc["on"]
    assert triggers.get("schedule")
    # Frequency is covered by
    # test_the_watchdog_checks_often_enough_to_catch_a_skipped_run. What
    # matters here is that it does not depend on another workflow running
    # in order to notice that no workflow ran.


def test_the_watchdog_never_recovers_from_its_own_trigger():
    """
    A failing daily job leaves the page old. Recovering on workflow_run
    would dispatch that job again the instant it failed, and again after
    that, forever. Recovery is limited to scheduled and manual checks.
    """
    step = WATCHDOG[WATCHDOG.index("Pull the data the scheduler skipped"):]
    step = step[:step.index("- name: Raise the alarm")]
    assert "github.event_name != 'workflow_run'" in step
    assert "gh workflow run daily.yml" in step


def test_the_watchdog_may_dispatch_a_workflow():
    import yaml
    doc = yaml.safe_load(WATCHDOG)
    assert doc["jobs"]["check"]["permissions"].get("actions") == "write"


def test_the_watchdog_checks_often_enough_to_catch_a_skipped_run():
    """
    Its own crons are as best effort as the ones it watches. On 27 August
    the midnight check fired at 08:37, so two checks a day meant a single
    slip removed most of the day's coverage.
    """
    import yaml
    doc = yaml.safe_load(WATCHDOG)
    triggers = doc[True] if True in doc else doc["on"]
    crons = [c["cron"] for c in triggers["schedule"]]
    assert any("*/4" in c or "*/3" in c or "*/2" in c for c in crons), crons


# ---------------------------------------------------------------------
# Publishing with nobody in the room
#
# The pull request is gone, at the owner's direction, after 3 weeks in
# which it was the only thing between a finished card and the site and it
# never once opened on its own.
#
# What actually held that line was never the merge. It was the lane check,
# card_rules.py and verify_sources.py, all of which run before anything is
# written. These tests exist because that is now the whole of it: if one
# of them stops running, or the publish step stops depending on them, an
# agent puts unverified betting picks on a public site and nothing says so.
# ---------------------------------------------------------------------

def card_steps() -> list:
    import yaml
    return yaml.safe_load(WEEKLY)["jobs"]["card"]["steps"]


def step_index(fragment: str) -> int:
    """
    Exact name first, substring only as a fallback.

    A substring match quietly found the wrong step the moment a second one
    contained the word "published", and 4 tests started asserting against
    the rehearsal summary instead of the publish.
    """
    steps = card_steps()
    for i, st in enumerate(steps):
        if (st.get("name") or "").lower() == fragment.lower():
            return i
    hits = [i for i, st in enumerate(steps)
            if fragment.lower() in (st.get("name") or "").lower()]
    assert hits, f"no step matching {fragment!r}"
    assert len(hits) == 1, (
        f"{fragment!r} matches {[steps[i]['name'] for i in hits]}, "
        f"name the step exactly")
    return hits[0]


def test_nothing_is_published_before_the_gates_have_run():
    publish = step_index("Publish")
    for gate in ("outside its lane", "Validate the card",
                 "Verify every cited source"):
        assert step_index(gate) < publish, f"{gate} must precede the publish"


def test_the_publish_step_stands_down_when_there_is_no_card():
    step = card_steps()[step_index("Publish")]
    assert "no_card != 'true'" in step["if"]


def test_the_agent_is_still_confined_to_one_file():
    lane = card_steps()[step_index("outside its lane")]
    body = lane.get("run", "")
    assert "picks_draft.json" in body
    # And the check has to be able to fail the run, not just print.
    assert "exit 1" in body


def test_the_card_still_has_to_pass_the_rules_before_it_goes_up():
    validate = card_steps()[step_index("Validate the card")]
    assert "validate_card.py" in validate.get("run", "")


def test_every_cited_source_is_still_opened_before_it_goes_up():
    verify = card_steps()[step_index("Verify every cited source")]
    assert "verify_sources.py" in verify.get("run", "")


def test_the_ledger_is_written_by_log_picks_not_by_the_agent():
    # The agent writes gitignored scratch. Deterministic code decides what
    # of it reaches data/picks.json.
    log = card_steps()[step_index("Log the card")]
    assert "log_picks.py" in log.get("run", "")
    assert "--dry-run" in log.get("run", ""), "rehearse before touching the ledger"


def test_a_card_that_publishes_nothing_is_not_an_error():
    step = card_steps()[step_index("Publish")].get("run", "")
    assert "git diff --staged --quiet" in step
    assert "exit 0" in step


def test_the_publish_survives_main_moving_underneath_it():
    """
    The daily pull commits on its own schedule and a 45 minute research
    run is long enough to collide with one. Failing at the last step with
    the card already built is the one outcome worth engineering away.
    """
    step = card_steps()[step_index("Publish")].get("run", "")
    assert "git pull --rebase origin main" in step
    assert "for attempt in" in step
    assert "::error::" in step, "and say so if every retry fails"


def test_friday_runs_close_to_when_it_publishes():
    """
    Friday used to run at 14:00 Eastern to leave 4 hours to review a pull
    request. Nothing is reviewed now, so running that early only means
    publishing 4 hour old lines.
    """
    import yaml
    doc = yaml.safe_load(WEEKLY)
    triggers = doc[True] if True in doc else doc["on"]
    crons = [c["cron"] for c in triggers["schedule"]]
    friday = [c for c in crons if c.endswith("* * 5")]
    assert friday, "no Friday schedule"
    hour = int(friday[0].split()[1])
    # 6 PM Eastern is 22:00 UTC. Start late enough that the lines are
    # current, early enough that a 45 minute run lands before the hour.
    assert 20 <= hour <= 21, f"Friday runs at {hour}:00 UTC"


def test_the_watchdog_knows_each_card_is_due_at_a_different_hour():
    step = WATCHDOG[WATCHDOG.index("Start the card if its schedule skipped"):]
    step = step[:step.index("- name: Raise the alarm")]
    assert "4) DUE=18" in step and "5) DUE=21" in step, \
        "one hour for both would start Friday early or recover Thursday late"


def test_the_pipeline_can_be_rehearsed_without_publishing():
    """
    The card goes straight to the site now, which collapsed "run it and
    see" and "put it live" into one action. Touching run-card-dry runs
    research, validation and source verification and stops before the
    ledger is written.
    """
    import yaml
    doc = yaml.safe_load(WEEKLY)
    triggers = doc[True] if True in doc else doc["on"]
    assert ".github/run-card-dry" in triggers["push"]["paths"]

    steps = {st.get("name"): st for st in doc["jobs"]["card"]["steps"]}
    # Read off the commit, not the event payload. The payload version
    # evaluated false on a commit that plainly added the file, and
    # published a card that was meant to be a rehearsal.
    mode = steps["Decide whether this is a rehearsal"]["run"]
    assert "git log -1 --name-only" in mode
    assert "grep -qx '.github/run-card-dry'" in mode, \
        "-qx, so a path merely containing the name cannot match"
    assert "inputs.research_only" in mode

    for name in ("Log the card and rebuild the page", "Publish"):
        assert "steps.mode.outputs.dry != 'true'" in steps[name]["if"], name
    # And the gates themselves must NOT be skipped, or the rehearsal
    # proves nothing about the thing it is rehearsing.
    for name in ("Fail if the agent wrote outside its lane",
                 "Validate the card", "Verify every cited source"):
        assert "mode" not in (steps[name].get("if") or ""), name


def test_the_rehearsal_flag_is_decided_before_anything_runs():
    # It gates the last 2 steps, so deciding it late would work. Deciding
    # it first means the run says which mode it is in before spending 15
    # minutes and 6 dollars finding out.
    import yaml
    names = [st.get("name") for st in
             yaml.safe_load(WEEKLY)["jobs"]["card"]["steps"] if st.get("name")]
    assert names[0] == "Decide whether this is a rehearsal"


def test_a_rehearsal_still_leaves_something_to_read():
    import yaml
    doc = yaml.safe_load(WEEKLY)
    steps = {st.get("name"): st for st in doc["jobs"]["card"]["steps"]}
    assert "steps.mode.outputs.dry == 'true'" in \
        steps["Keep whatever the run produced"]["if"]
    assert "Say what a rehearsal would have published" in steps


def test_a_summary_that_breaks_cannot_fail_a_published_card():
    """
    The card is live the moment the push lands. Anything after that which
    can raise would report a published card as a failed run, and send
    someone looking for a fault that is not there.
    """
    import yaml
    doc = yaml.safe_load(WEEKLY)
    steps = {st.get("name"): st for st in doc["jobs"]["card"]["steps"]}
    body = steps["Publish"]["run"]
    after_push = body[body.index("PUBLISHED=1"):]
    assert "|| true" in after_push


def test_the_grader_researches_the_whole_slate_not_only_the_card():
    """
    The card is 2 picks and the public record. The board is 40 games and
    the evidence. A grader that only reads the card learns nothing until
    January.
    """
    grader = (ROOT / ".claude" / "agents" / "grader.md").read_text()
    assert "week_review.json" in grader
    assert "5 worst misses" in grader
    # And it must not confuse the two.
    assert "The card is the record" in grader


def test_the_review_runs_before_the_page_is_rebuilt():
    import yaml
    doc = yaml.safe_load(DAILY)
    names = [s.get("name") for s in doc["jobs"]["update"]["steps"] if s.get("name")]
    assert "Score the model against every result" in names
    assert names.index("Score the model against every result") < \
        names.index("Rebuild the page")


def test_a_failed_review_cannot_stop_the_publish():
    # It is evidence for the grader, not a gate. A bad week of name
    # matching must not take the daily page down with it.
    import yaml
    doc = yaml.safe_load(DAILY)
    step = next(s for s in doc["jobs"]["update"]["steps"]
                if s.get("name") == "Score the model against every result")
    assert step.get("continue-on-error") is True
