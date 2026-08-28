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
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_runner_task.ps1"
ENTRYPOINT = REPO_ROOT / "run_company_ops.py"

sys.path.insert(0, str(REPO_ROOT / "src"))


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
        """docs/07 §4 and docs/11 §19 both fix the regular run at 11:00."""
        self.assertRegex(_script_code(), r"\$DailyAt\s*=\s*'11:00'")


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
        script path — an extra argument would make the task exit 1 forever."""
        code = _script_code()
        self.assertRegex(code, r"-Argument\s+\"`\"\$entrypoint`\"\"")


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
        self.assertIn("DOJOONPASS_COMPANY_OPS_DAILY", _script_code())
        runbook = (REPO_ROOT / "docs" / "11_DEPLOYMENT_RUNBOOK.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("DOJOONPASS_COMPANY_OPS_DAILY", runbook)

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
