"""Validation of scripts/install_runner_task.ps1 (C136).

Desktop 4's Runner is the one job that turns collected Events into Company
History, backs it up and syncs Notion. docs/11 §19-20 names its task
(`DOJOONPASS_COMPANY_OPS_DAILY`, daily 11:00 plus a Startup Catch-up
trigger) — and until C136 there was no installer for it. The **secondary**
job, the reporting-side Agent on Desktops 1-3, had a tested one; the primary
one had to be built by hand from the runbook's prose.

That asymmetry is the risk this file closes. Every lesson the Agent
installer paid for applies to Desktop 4 and applies harder, because there is
exactly one of it:

    -User on the logon trigger    without it the trigger is machine-wide,
                                  which a non-administrator cannot register
                                  at all (measured in C13) and which would
                                  fire this Desktop's Runner at any account's
                                  logon, with none of its configuration
    MultipleInstances IgnoreNew   docs/07 §55, the Windows-level half of the
                                  duplicate protection whose other half is
                                  the application's system-wide lock
    StartWhenAvailable            docs/11 §20's whole point: an 11:00 the PC
                                  slept through must still run
    ShouldProcess on env writes   -WhatIf must change nothing. The Agent
                                  installer's equivalent lines once rewrote
                                  user environment variables during a preview

Same shape as `test_install_agent_task_script.py`: the script is not
executed for real, because registering a task is a system change that is
awkward to undo — except under `-WhatIf`, which runs every line and skips
only the two guarded effects. That mode is what caught an Agent installer
which parsed cleanly and could never have registered anything.
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_runner_task.ps1"
ENTRYPOINT = REPO_ROOT / "run_company_ops.py"

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
        the dependency, because a non-ASCII character here would reach the
        operator as mojibake under Windows PowerShell 5.1 (C136)."""
        text = _script_text()
        self.assertEqual([c for c in text if ord(c) > 127], [])
        self.assertFalse(SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf"))


class SyntaxTests(unittest.TestCase):
    def test_powershell_parses_the_script_without_errors(self):
        """The real parser, not a regex."""
        command = (
            "$errs = $null; "
            "$null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{SCRIPT.as_posix()}', [ref]$null, [ref]$errs); "
            "if (@($errs).Count -gt 0) "
            "{ Write-Output \"ERRORS:$(@($errs).Count)\"; "
            "foreach ($e in $errs) { Write-Output $e.Message } } "
            "else { Write-Output 'OK' }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=180,
        )
        self.assertIn("OK", result.stdout, result.stdout + result.stderr)


class WhatIfExecutionTests(unittest.TestCase):
    """Run every line, register nothing, change nothing.

    Safe only because the environment write is ShouldProcess-guarded — the
    same guard whose absence made the Agent installer's equivalent test a
    hazard to the developer's own environment.
    """

    NAME = "COMPANY_OPS_HISTORY_START_DATE"
    TASK = "DOJOONPASS_COMPANY_OPS_DAILY"

    def _run_whatif(self, extra: str = "") -> subprocess.CompletedProcess:
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f"$before = [Environment]::GetEnvironmentVariable('{self.NAME}','User'); "
            f"& '{SCRIPT.as_posix()}' -HistoryStartDate 2026-08-10 {extra} -WhatIf; "
            f"$after = [Environment]::GetEnvironmentVariable('{self.NAME}','User'); "
            "if ($before -ne $after) { Write-Output 'SIDE_EFFECT' } "
            "else { Write-Output 'NO_SIDE_EFFECT' }; "
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

    def test_whatif_changes_no_environment_variable(self):
        result = self._run_whatif()
        self.assertIn("NO_SIDE_EFFECT", result.stdout, result.stdout + result.stderr)

    def test_both_effects_are_announced_as_would_be_done(self):
        """An operator previewing an install must be told about the
        environment write too, not only the task.

        Asserted on the **untranslated** part of the announcement. A first
        draft looked for the literal "What if:" and failed on this machine,
        where PowerShell is localised: it prints
        `WhatIf: 대상 "..." 에서 "..." 작업을 수행합니다.` That draft would
        have passed on an English machine and failed on the Korean Desktops
        this project actually deploys to — the same locale trap
        `backup/git_ops.py` removes with `LC_ALL=C`, arriving here through a
        test instead of through the code.

        The operation and target names come from this script, so they are
        the same in every locale.
        """
        result = self._run_whatif()
        lowered = result.stdout.lower()

        self.assertIn(self.NAME.lower(), lowered)
        self.assertIn(self.TASK.lower(), lowered)
        self.assertIn("register scheduled task", lowered)
        self.assertIn("user environment", lowered)

    def test_a_custom_daily_time_still_builds(self):
        result = self._run_whatif("-DailyAt 09:30")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NO_TASK", result.stdout)

    def test_a_malformed_start_date_is_refused_before_anything_runs(self):
        command = (
            f"& '{SCRIPT.as_posix()}' -HistoryStartDate 'not-a-date' -WhatIf; "
            "Write-Output \"EXIT:$LASTEXITCODE\""
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=180,
        )
        self.assertNotIn("Registered:", result.stdout)

    def test_an_impossible_date_that_matches_the_pattern_is_still_refused(self):
        """`ValidatePattern` only checks the shape. 2026-02-30 is shaped like
        a date and is not one, and a Runner that cannot parse its start date
        stops at the configuration check every single morning."""
        command = (
            f"try {{ & '{SCRIPT.as_posix()}' -HistoryStartDate '2026-02-30' -WhatIf }} "
            "catch { Write-Output 'THREW' }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=180,
        )
        self.assertIn("THREW", result.stdout, result.stdout + result.stderr)
        self.assertNotIn("Registered:", result.stdout)


class ParameterContractTests(unittest.TestCase):
    def test_the_start_date_is_shape_checked(self):
        self.assertIn("ValidatePattern", _script_code())

    def test_the_start_date_is_mandatory(self):
        self.assertIn("Mandatory = $true", _script_code())

    def test_whatif_is_supported(self):
        self.assertIn("SupportsShouldProcess", _script_code())

    def test_the_daily_time_defaults_to_the_spec_hour(self):
        """docs/07 section 4 and docs/11 section 19 both fix the regular run
        at 11:00, and this checks the installer against **them**.

        It used to check it against `'11:00'` written here -- a third copy of
        a value the specs own. The docstring already said where the value
        comes from; nothing read it. Change the spec and this test goes on
        passing while the installer keeps registering the old hour, which is
        the shape C139 spent its time removing on the PowerShell/Python
        boundary.

        The time is read out of the installer and looked for in both specs.
        Derived from the script rather than restated, so the assertion is
        about whatever the installer actually defaults to.
        """
        match = re.search(r"\$DailyAt\s*=\s*'([0-9]{2}:[0-9]{2})'", _script_code())
        self.assertIsNotNone(match, "the runner installer has no $DailyAt default")
        default = match.group(1)

        for spec in ("07_SCHEDULER_CATCHUP_SPEC.md", "11_DEPLOYMENT_RUNBOOK.md"):
            with self.subTest(spec=spec):
                self.assertIn(
                    default,
                    (REPO_ROOT / "docs" / spec).read_text(encoding="utf-8"),
                    f"the installer defaults to {default} and docs/{spec} "
                    f"does not name that time",
                )


class EntrypointContractTests(unittest.TestCase):
    def test_the_entrypoint_it_launches_exists(self):
        self.assertTrue(ENTRYPOINT.is_file())

    def test_it_launches_the_runner_not_the_agent(self):
        code = _script_code()
        self.assertIn("run_company_ops.py", code)
        self.assertNotIn("run_agent.py", code)

    def test_it_refuses_to_register_a_missing_entrypoint(self):
        self.assertIn("not found at", _script_code())

    def test_it_refuses_to_register_without_python_on_path(self):
        self.assertIn("was not found on PATH", _script_code())

    def test_the_variable_it_persists_is_the_one_the_entrypoint_reads(self):
        """A rename on either side produces a task that starts, fails its
        configuration check and exits 1 every morning — silently, because
        nobody watches a scheduled task's exit code."""
        entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

        self.assertIn("COMPANY_OPS_HISTORY_START_DATE", _script_code())
        self.assertIn("COMPANY_OPS_HISTORY_START_DATE", entrypoint)

    def test_the_variable_is_documented_in_env_example(self):
        template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("COMPANY_OPS_HISTORY_START_DATE", template)

    def test_the_task_launches_with_no_arguments(self):
        """Every entrypoint here refuses arguments it cannot honour
        (`cli.unexpected_arguments`), so the action must pass only the
        script path — an extra argument would make the task exit 1 forever.

        **The shape this reads changed in C138 and the property did not.**
        The action used to be `python.exe "$entrypoint"` and is now
        `cmd.exe /c ""python" "$entrypoint" >> "log" 2>&1"`, because a
        scheduled run had nowhere to print and its failures left no
        explanation anywhere. Everything after the entrypoint is cmd's
        redirection, which cmd consumes.

        So this asserts the same thing about the new string: the entrypoint
        is followed immediately by the redirection, with no token in
        between. It is the cheap half. The expensive half — running the
        command line and reading `sys.argv` back — is
        `TheScheduledRunHasSomewhereToPrintTests
        ::test_the_entrypoint_still_receives_no_arguments`, and it is what
        actually proves it: a static reading of cmd quoting is a guess.
        """
        code = _script_code()
        self.assertRegex(code, r'"\{1\}" >> ')
        self.assertIn("$commandLine", code)
        self.assertNotIn("-Argument \"`\"$entrypoint`\"\"", code)


class NoSecretIsHandledTests(unittest.TestCase):
    """The Runner needs NOTION_API_TOKEN; this installer must not touch it.

    A parameter would put the token in the command line, the process list
    and PowerShell history. `.env.example` already states that nothing
    auto-loads `.env` and the operator exports these themselves.
    """

    def test_no_notion_variable_is_written(self):
        code = _script_code()
        self.assertNotIn("SetEnvironmentVariable('NOTION", code)
        self.assertNotIn('SetEnvironmentVariable("NOTION', code)

    def test_no_token_shaped_parameter_exists(self):
        code = _script_code()
        for forbidden in ("$Token", "$ApiToken", "$NotionToken", "$Secret"):
            with self.subTest(parameter=forbidden):
                self.assertNotIn(forbidden, code)

    def test_the_operator_is_told_what_is_still_missing(self):
        """Silently omitting them would leave an operator believing the
        install is complete while Notion Sync never runs."""
        code = _script_code()
        self.assertIn("NOTION_API_TOKEN", code)
        self.assertIn("NOTION_PROJECTS_DATABASE_ID", code)


class SchedulerPolicyTests(unittest.TestCase):
    """docs/07 §53-55 and docs/11 §19-20, read off the script."""

    def test_it_registers_the_daily_trigger_the_runbook_names(self):
        self.assertIn("New-ScheduledTaskTrigger -Daily -At $DailyAt", _script_code())

    def test_it_also_registers_the_startup_catch_up_trigger(self):
        """docs/11 §20: for the morning the PC was off at 11:00."""
        self.assertIn("-AtLogOn", _script_code())

    def test_the_logon_trigger_is_scoped_to_this_user(self):
        """C13's lesson, and the reason the Agent installer had never
        registered on a non-administrator machine."""
        self.assertRegex(_script_code(), r"-AtLogOn\s+-User\s+\$currentUser")

    def test_duplicate_protection_exists_at_both_layers(self):
        """docs/07 §55: the Windows setting on top of the app's own lock."""
        self.assertIn("-MultipleInstances IgnoreNew", _script_code())

        lock = (REPO_ROOT / "src" / "scheduler" / "lock.py").read_text(encoding="utf-8")
        self.assertIn("O_EXCL", lock)

    def test_a_missed_run_fires_at_the_next_opportunity(self):
        self.assertIn("-StartWhenAvailable", _script_code())

    def test_a_delay_is_applied_on_the_logon_trigger_only(self):
        """docs/07 §54. On the trigger, not the settings set — the settings
        CIM instance has no such property, and assigning to it threw before
        `Register-ScheduledTask` was ever reached in the Agent installer."""
        code = _script_code()
        self.assertIn('$logonTrigger.Delay = "PT$($DelayMinutes)M"', code)
        self.assertNotIn("RandomDelay", code)
        self.assertNotIn("$dailyTrigger.Delay", code)

    def test_the_task_name_is_the_one_the_runbook_names(self):
        """docs/11 section 19 names the task an operator will look for in
        Task Scheduler. That contract is worth keeping and it now has a
        different subject.

        This used to read the name out of *this script* and check the
        runbook said the same. The script no longer contains it -- it asks
        `src/schedtask.py`, which is the single place the name is written
        (see `test_schedtask.py::TaskNamesMatchTheInstallersTests`). So the
        pair to hold in step is docs and that module, and the script's part
        is only that it asks.
        """
        import schedtask

        runbook = (REPO_ROOT / "docs" / "11_DEPLOYMENT_RUNBOOK.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(schedtask.RUNNER_TASK_NAME, runbook)
        self.assertIn("import schedtask", _script_code())
        self.assertIn("schedtask.RUNNER_TASK_NAME", _script_code())

    def test_the_task_does_not_keep_the_machine_awake(self):
        """docs/07 §58: the PC may be off; catch-up is the safety net."""
        code = _script_code()
        self.assertNotIn("-WakeToRun", code)
        self.assertNotIn("RunOnlyIfIdle", code)

    def test_an_execution_time_limit_bounds_a_hung_run(self):
        self.assertIn("-ExecutionTimeLimit", _script_code())


class RegistrationFailureHandlingTests(unittest.TestCase):
    def test_a_refused_registration_is_diagnosed_rather_than_echoed(self):
        code = _script_code()
        self.assertIn("Get-Service Schedule", code)
        self.assertIn("Nothing was registered", code)

    def test_a_silent_non_registration_is_caught(self):
        """`Register-ScheduledTask` can return without throwing and leave no
        task. Reporting success there sends the operator away believing
        Company History is scheduled when nothing is."""
        code = _script_code()
        self.assertIn("does not exist. Nothing is scheduled", code)

    def test_the_advice_does_not_send_the_operator_for_admin_rights_first(self):
        """The Agent installer's old message asserted elevation was needed;
        re-measurement showed a non-administrator registers per-user tasks
        fine and the real cause was a missing `-User`. The ordering here puts
        the likely checks first."""
        code = _script_code()
        elevated = code.find("elevated PowerShell")
        service = code.find("Get-Service Schedule")
        self.assertGreater(elevated, service, "elevation is offered before the cheap checks")



class TheScheduledRunHasSomewhereToPrintTests(unittest.TestCase):
    """The action's output used to go nowhere, and the repository knew it.

    Five entrypoints carry a measured comment about `line_buffering=True`
    being needed because "under `> log 2>&1`, which is how a scheduled run
    is captured, the two streams reorder against each other". No installer
    ever set up that redirection. The action was `python.exe <entrypoint>`,
    so a scheduled task's stdout and stderr were written to handles nothing
    read.

    What that loses is the entire diagnosis for failures that happen outside
    the application. Every other thing `ops_status.py` can tell an operator
    is derived from a file this system wrote, and a run that dies before it
    writes one leaves only a `LastTaskResult` number — for the likeliest
    failure of all, an unset `COMPANY_OPS_*`, that number is `1` every
    morning while the sentence naming the missing variable is discarded.

    These tests assert the property (the run has somewhere to print, and the
    exit code still means what docs/14 §4 says) rather than the exact
    string, except where the string is the thing that can silently be wrong
    — cmd's quoting.
    """

    ENTRYPOINT_NAME = 'run_company_ops.py'
    LOG_NAME = 'scheduled_runner.log'

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
    def test_the_log_lives_under_the_runtime_tree(self):
        """Beside `collector.log` and the Run Manifest, which is where
        `ops_status.py` and an operator already look."""
        self.assertIn("runtime\\logs", _script_code())

    def test_the_log_directory_is_created_before_the_task_is_registered(self):
        """`runtime/` is git-ignored, so on a fresh clone it does not exist.
        `>>` fails when the directory is absent — without this the first
        scheduled run on a new machine would fail at the redirection itself,
        which is the opposite of what the redirection is for."""
        code = _script_code()
        self.assertIn("New-Item -ItemType Directory -Path $logDir", code)
        self.assertLess(
            code.index("New-Item -ItemType Directory -Path $logDir"),
            code.index("Register-ScheduledTask"),
        )

    def test_the_shell_is_named_by_path_and_not_left_to_path_lookup(self):
        """A task started by the scheduler service does not necessarily have
        the PATH an interactive shell has."""
        code = _script_code()
        self.assertIn("$comspec = $env:ComSpec", code)
        self.assertIn("System32\\cmd.exe", code)
        self.assertIn("-Execute $comspec", code)

    def test_redirection_is_append_not_overwrite(self):
        """`>` would let the next morning's run erase yesterday's traceback,
        and the record of a failure is exactly what this exists to keep."""
        code = _script_code()
        self.assertNotRegex(code, r"[^>]> \"\{2\}")
        self.assertIn('>> "{2}"', code)

    # ------------------------------------------------ the string that runs

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

    def test_a_preview_creates_no_log_directory(self):
        """`-WhatIf` exists to change nothing, and the Agent installer's
        history in this file is what that rule was written from: its
        environment writes once ran during a preview.

        `New-Item` inherits `$WhatIfPreference` from this script's
        `CmdletBinding`, which is a propagation rather than a guard anybody
        wrote — so it is measured here rather than assumed."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "runtime" / "logs"
            probe = Path(tmp) / "probe.ps1"
            probe.write_text(
                "[CmdletBinding(SupportsShouldProcess = $true)]\n"
                "param([string]$Dir)\n"
                "if (-not (Test-Path -LiteralPath $Dir)) {\n"
                "    New-Item -ItemType Directory -Path $Dir -Force | Out-Null\n"
                "}\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(probe),
                 "-Dir", str(target), "-WhatIf"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertFalse(target.exists(), "a preview created a directory")
            # The same lines without -WhatIf must actually create it, or the
            # assertion above would pass for the wrong reason.
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(probe),
                 "-Dir", str(target)],
                capture_output=True, text=True, timeout=180,
            )
            self.assertTrue(target.is_dir())

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
        self.assertIsNotNone(schedtask.scheduled_log_name(schedtask.RUNNER_TASK_NAME))
