"""`scripts/install_publish_task.ps1` — the third scheduled task.

**The registration this script performs was already a documented step with
no script.** `AGENT.md` §6c tells an operator to register
`publish_control_tower.py` "in Task Scheduler beside `run_company_ops.py`",
and that tool was given exit code 3 (DEGRADED) *for that deployment* — its
own docstring says "Task Scheduler's only automatic health signal is the
exit code". So the tool was built for a schedule that nothing created, and
every operator had to build the task by hand from prose. That is the gap the
Runner installer closed for Desktop 4 ("Desktop 4's task had to be built by
hand from the runbook's prose"), left open for the third job.

What it costs to leave open is the point of publishing at all: the Notion
Control Tower page is the seat for everyone who does **not** open a
terminal, and until it is scheduled that page only refreshes when somebody
opens one. `dashboard_server.py` renders that state on its own face —
"자동 실행: 없음 — 스스로 갱신되지 않는다".

These tests follow the two existing installer suites: static assertions on
the properties those two paid for (the `-User` logon scope, IgnoreNew,
StartWhenAvailable, verify-after-register), a real `-WhatIf` execution, and
— for the part no static reading can settle — an execution of the exact
command line the script builds.
"""

# `str | None` in a helper signature below. Without this, that annotation
# is evaluated at import time and aborts pytest collection for the WHOLE
# suite on Python < 3.10 -- see
# `EveryTrackedModuleParsesOnThisInterpreterTests`.
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_publish_task.ps1"
ENTRYPOINT = REPO_ROOT / "publish_control_tower.py"

sys.path.insert(0, str(REPO_ROOT / "src"))

import schedtask  # noqa: E402


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _script_code() -> str:
    """The script minus comment-based help and `#` comments.

    The `.NOTES` block explains at length that this installer handles no
    Notion token; a naive scan reads that explanation as the violation it
    describes. Only what PowerShell would execute is searched.
    """
    text = re.sub(r"<#.*?#>", "", _script_text(), flags=re.DOTALL)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class ScriptPresenceTests(unittest.TestCase):
    def test_the_script_exists(self):
        self.assertTrue(SCRIPT.is_file(), f"missing: {SCRIPT}")

    def test_it_is_readable_as_utf8_and_pure_ascii(self):
        """`APowerShellScriptStaysPureAsciiTests` owns the rule; this states
        the dependency, because a non-ASCII character here reaches the
        operator as mojibake under Windows PowerShell 5.1 — and `.SYNOPSIS`
        is a surface `Get-Help` renders."""
        text = _script_text()
        self.assertEqual([c for c in text if ord(c) > 127], [])
        self.assertFalse(SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_the_entrypoint_it_registers_is_there(self):
        self.assertTrue(ENTRYPOINT.is_file())
        self.assertIn("publish_control_tower.py", _script_code())


@unittest.skipUnless(sys.platform == "win32", "PowerShell parser is Windows-only here")
class SyntaxTests(unittest.TestCase):
    def test_powershell_parses_the_script_without_errors(self):
        """The real parser. A regex cannot tell a working script from one
        with an unbalanced brace."""
        command = (
            "$errors = $null; $tokens = $null; "
            "$null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{SCRIPT.as_posix()}', [ref]$tokens, [ref]$errors); "
            "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 } "
            "else { Write-Output 'PARSE_OK' }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=180,
        )
        self.assertIn("PARSE_OK", result.stdout, result.stdout + result.stderr)


class TaskDefinitionTests(unittest.TestCase):
    """The properties the other two installers paid for, restated here.

    Every one of them was a real failure on a real machine, and none of them
    is visible from a script that merely "looks right".
    """

    def test_the_logon_trigger_is_scoped_to_this_user(self):
        """Without `-User` the trigger is machine-wide: Windows refuses it
        from a non-elevated session, and it would fire at any account's
        logon. That single missing argument is why the Agent installer had
        never registered a task on any non-administrator machine."""
        self.assertIn(
            "New-ScheduledTaskTrigger -AtLogOn -User $currentUser", _script_code()
        )

    def test_a_second_instance_is_dropped_rather_than_started(self):
        """This tool takes no lock of its own — it is read-only except for
        its own Notion page — so this setting is the only thing stopping two
        publishes rewriting that page at once."""
        self.assertIn("-MultipleInstances IgnoreNew", _script_code())

    def test_a_missed_trigger_fires_at_the_next_opportunity(self):
        self.assertIn("-StartWhenAvailable", _script_code())

    def test_the_registration_is_verified_after_it_is_made(self):
        """`Register-ScheduledTask` can return without throwing and leave no
        task. Reporting success then sends the operator away believing the
        Control Tower refreshes itself while nothing does."""
        code = _script_code()
        self.assertLess(
            code.index("Register-ScheduledTask"),
            code.index("$registered = Get-ScheduledTask"),
        )
        self.assertIn("does not exist. Nothing is scheduled.", code)

    def test_a_refusal_explains_itself_before_it_suggests_elevation(self):
        """The Agent installer's lesson: "this environment cannot register
        tasks at all" was recorded as the diagnosis and was not true.
        Elevation is the last candidate, not the first."""
        code = _script_code()
        self.assertLess(code.index("Get-Service Schedule"), code.index("elevated"))

    def test_the_registration_is_the_only_thing_shouldprocess_guards(self):
        """Because it is the only thing this script changes. Unlike the
        other two installers it writes no environment variable, so `-WhatIf`
        has exactly one effect to suppress."""
        self.assertEqual(_script_code().count("$PSCmdlet.ShouldProcess"), 1)


class NoSecretIsHandledTests(unittest.TestCase):
    """`publish_control_tower.py` needs `NOTION_API_TOKEN`; this must not
    touch it. A parameter would put the token in the command line, the
    process list and PowerShell history."""

    def test_no_environment_variable_is_written_at_all(self):
        self.assertNotIn("SetEnvironmentVariable", _script_code())

    def test_no_token_shaped_parameter_exists(self):
        code = _script_code()
        for forbidden in ("$Token", "$ApiToken", "$NotionToken", "$Secret"):
            with self.subTest(parameter=forbidden):
                self.assertNotIn(forbidden, code)

    def test_the_operator_is_told_what_is_still_required(self):
        """Refusing to store the secrets is only half of it. A script that
        registers a task which will exit 1 every morning, and says nothing
        about why, has arranged a silent daily failure."""
        code = _script_code()
        self.assertIn("NOTION_API_TOKEN", code)
        self.assertIn("NOTION_PROJECTS_DATABASE_ID", code)


@unittest.skipUnless(sys.platform == "win32", "Task Scheduler is Windows-only")
class WhatIfChangesNothingTests(unittest.TestCase):
    """`-WhatIf` really executed, not read.

    "정적 검증했다" and "동작한다" are different claims (BACKLOG E-6): the
    Agent installer passed 22 static tests while being unable to register a
    task on any machine. This runs the script.
    """

    TASK = schedtask.PUBLISH_TASK_NAME

    def _run_whatif(self, extra: str = "") -> subprocess.CompletedProcess:
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f"& '{SCRIPT.as_posix()}' {extra} -WhatIf; "
            f"if (Get-ScheduledTask -TaskName '{self.TASK}' -ErrorAction SilentlyContinue) "
            "{ Write-Output 'TASK_REGISTERED' } else { Write-Output 'NO_TASK' }"
        )
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=180,
        )

    def test_the_script_runs_to_completion_under_whatif(self):
        result = self._run_whatif()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NO_TASK", result.stdout)

    def test_the_registration_is_announced_as_would_be_done(self):
        """Asserted on the **untranslated** part of the announcement: the
        operation and target names come from this script, so they are the
        same in every locale. A draft that matched "What if:" would pass on
        an English machine and fail on the Korean Desktops this project
        actually deploys to."""
        lowered = self._run_whatif().stdout.lower()
        self.assertIn(self.TASK.lower(), lowered)
        self.assertIn("register scheduled task", lowered)

    def test_a_custom_time_still_builds(self):
        result = self._run_whatif("-DailyAt 09:30")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NO_TASK", result.stdout)


class TheScheduledRunHasSomewhereToPrintTests(unittest.TestCase):
    """Without a redirection, a publish leaves only its exit code.

    That bites harder here than for the other two. This tool's normal report
    is four lines of "which surface was written", and its DEGRADED path
    names the surfaces that were **not** — all of it stdout and stderr. An
    unredirected publish that lost the `Notes` column, every Project Row
    body and the Database description would tell an operator "3" and nothing
    else.
    """

    LOG_NAME = "scheduled_publish.log"

    #: The **executed** half of this contract lives in
    #: `test_schedtask.py::TaskNamesMatchTheInstallersTests` and covers all
    #: three installers from one place.
    #:
    #: It was written here first, once per installer: twelve PowerShell
    #: launches at ~1.2 s each, asserting one identical property three times
    #: over. Measured with `--durations`, those twelve were the twelve
    #: slowest tests in these files. Worse than the cost, it was three
    #: hand-copies of a contract -- the roster shape this Sprint spent its
    #: time removing, in a test file.
    #:
    #: What stays here is the static half, which is per-installer by nature:
    #: **this** script builds the shape, redirects, appends rather than
    #: overwrites, names the shell by path, and creates the log directory
    #: first. The sweep over there asserts all three declare the *same*
    #: shape and then executes it -- so an installer that declared a
    #: different one fails there rather than going untested here.


    def test_the_action_redirects_the_console_output_to_a_file(self):
        """The redirection target is `$logPath`, which is
        `$logDir` + the filename `schedtask.scheduled_log_name()` returned.

        This used to assert the literal filename appeared in the script.
        It no longer does, and that is the point -- the name lives in
        `src/schedtask.py` alone now (see
        `test_schedtask.py::TaskNamesMatchTheInstallersTests`). What is
        checkable here is that the action redirects at all, and that it
        redirects to the derived path rather than to one this script made
        up.
        """
        code = _script_code()
        self.assertIn("2>&1", code)
        self.assertIn('>> "{2}"', code)
        self.assertIn("$logPath = Join-Path $logDir $logFileName", code)
    def test_redirection_is_append_not_overwrite(self):
        """`>` would let the next morning's publish erase yesterday's
        DEGRADED report, and that report is the only place the failed
        surfaces are named."""
        self.assertIn('>> "{2}"', _script_code())

    def test_the_log_lives_under_the_runtime_tree(self):
        self.assertIn("runtime\\logs", _script_code())

    def test_the_log_directory_is_created_before_the_task_is_registered(self):
        """`runtime/` is git-ignored, so on a fresh clone it does not exist,
        and `>>` fails when the directory is absent."""
        code = _script_code()
        self.assertIn("New-Item -ItemType Directory -Path $logDir", code)
        self.assertLess(
            code.index("New-Item -ItemType Directory -Path $logDir"),
            code.index("Register-ScheduledTask"),
        )

    def test_the_shell_is_named_by_path_and_not_left_to_path_lookup(self):
        code = _script_code()
        self.assertIn("$comspec = $env:ComSpec", code)
        self.assertIn("System32\\cmd.exe", code)
        self.assertIn("-Execute $comspec", code)

    def test_the_log_path_is_the_one_python_expects_to_find(self):
        """`ops_status.py` prints the end of this file when a scheduled run
        failed, and for the failures that leave nothing under `runtime/` it
        is the only evidence there is.

        There is nothing to compare any more: the script asks
        `schedtask.scheduled_log_name()` for the name, so the two cannot
        disagree. What is asserted is that it asks -- and that the answer,
        for this installer's task, is a name at all rather than `None`,
        which is what the script would silently write into its path if the
        task were one `schedtask` does not know.
        """
        code = _script_code()
        self.assertIn("schedtask.scheduled_log_name(", code)
        self.assertIsNotNone(schedtask.scheduled_log_name(schedtask.PUBLISH_TASK_NAME))
