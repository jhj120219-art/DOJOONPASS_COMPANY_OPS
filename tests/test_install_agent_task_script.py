"""Static validation of scripts/install_agent_task.ps1.

The script registers a Windows Scheduled Task. Actually running it makes a
system change that is awkward to undo, so it is not executed here — but it
is the single piece of the deployment that turns "the Agent works" into
"the Agent runs by itself", and until this file existed it was the only
executable in the repository with no coverage at all.

What is checked without running it:

    it parses                     (the real PowerShell parser, not a regex)
    its parameters validate       ValidateSet / ValidatePattern are present
    it targets a real entrypoint  run_agent.py exists at the path it builds
    its env var names match       the names run_agent.py actually reads
    duplicate protection          docs/07 §55's two layers
    missed-run recovery           StartWhenAvailable, so a PC that was off
                                  still runs at the next opportunity
    it handles no secrets         nothing token-shaped is written anywhere
    -WhatIf works                 SupportsShouldProcess, so an operator can
                                  see what would be registered first

The env-var check is the one that would actually bite: the installer
persists three variables and `run_agent.py` reads three, and a rename on
either side produces a task that starts, fails on configuration, and exits
1 on every logon — silently, because nobody watches a scheduled task's exit
code.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_agent_task.ps1"

sys.path.insert(0, str(REPO_ROOT / "src"))


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _script_code() -> str:
    """The script with its comment-based help and `#` comments removed.

    Needed by the secret checks: the `.NOTES` block explains at length that
    the installer handles no Notion token, and a naive substring scan reads
    that explanation as the violation it is describing. Only what PowerShell
    would actually execute should be searched.
    """
    text = re.sub(r"<#.*?#>", "", _script_text(), flags=re.DOTALL)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class ScriptPresenceTests(unittest.TestCase):
    def test_the_script_exists_and_is_utf8(self):
        self.assertTrue(SCRIPT.is_file(), f"missing: {SCRIPT}")
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertGreater(len(text), 500)
        self.assertFalse(SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf"))


@unittest.skipUnless(sys.platform == "win32", "PowerShell parser is Windows-only here")
class SyntaxTests(unittest.TestCase):
    def test_powershell_parses_the_script_without_errors(self):
        """The real parser. A regex cannot tell a working script from one
        with an unbalanced brace, and this file is never exercised by any
        other test."""
        command = (
            "$errors = $null; $tokens = $null; "
            f"$null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{SCRIPT.as_posix()}', [ref]$tokens, [ref]$errors); "
            "if ($errors) { $errors | ForEach-Object { Write-Output $_.Message }; exit 1 } "
            "else { Write-Output 'OK'; exit 0 }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)


@unittest.skipUnless(sys.platform == "win32", "the installer is Windows-only")
class WhatIfExecutionTests(unittest.TestCase):
    """Actually RUN the installer, in -WhatIf mode.

    Static checks passed on a script that could not work. The delay was
    applied with
    `$settings.CimInstanceProperties.Item('RandomDelay').Value = ...`, but
    `New-ScheduledTaskSettingsSet` exposes no such property — the lookup
    returned $null and the assignment threw PropertyNotFound *before*
    `Register-ScheduledTask` was ever reached. Every static assertion about
    the file's text still passed, because the text did contain
    "RandomDelay". The installer had never been executed.

    -WhatIf is the strongest check available without changing the machine:
    every line runs, every cmdlet validates its arguments, and the two
    ShouldProcess-guarded effects are skipped. It is only safe because the
    environment-variable writes are now guarded too — they used to happen
    unconditionally, so this test would itself have rewritten the
    developer's user environment.
    """

    NAMES = (
        "COMPANY_OPS_PROFILE",
        "COMPANY_OPS_AGENT_SYNC_FOLDER",
        "COMPANY_OPS_AGENT_START_DATE",
    )
    TASK = "DOJOONPASS_COMPANY_OPS_AGENT_DESKTOP_2"

    def _run_whatif(self, extra: str = "") -> subprocess.CompletedProcess:
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "$names = " + ",".join(f"'{n}'" for n in self.NAMES) + "; "
            "$before = @{}; foreach ($n in $names) "
            "{ $before[$n] = [Environment]::GetEnvironmentVariable($n,'User') }; "
            f"& '{SCRIPT.as_posix()}' -DesktopId DESKTOP_2 "
            f"-SyncFolder 'C:/Temp/CompanyOpsWhatIfProbe' -StartDate 2026-08-10 {extra} -WhatIf; "
            "$changed = @(); foreach ($n in $names) "
            "{ if ([Environment]::GetEnvironmentVariable($n,'User') -ne $before[$n]) "
            "{ $changed += $n } }; "
            "if ($changed.Count -gt 0) { Write-Output \"SIDE_EFFECT:$($changed -join ',')\" } "
            "else { Write-Output 'NO_SIDE_EFFECT' }; "
            f"if (Get-ScheduledTask -TaskName '{self.TASK}' -ErrorAction SilentlyContinue) "
            "{ Write-Output 'TASK_REGISTERED' } else { Write-Output 'NO_TASK' }"
        )
        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_the_script_runs_to_completion_under_whatif(self):
        result = self._run_whatif()
        combined = result.stdout + result.stderr

        self.assertNotIn("PropertyNotFound", combined)
        self.assertNotIn("CommandNotFoundException", combined)
        self.assertNotIn("ParameterBindingException", combined)
        self.assertEqual(result.returncode, 0, combined)
        self.assertIn("NO_TASK", result.stdout, combined)

    def test_whatif_changes_no_environment_variable(self):
        """-WhatIf must be a preview. Previewing an install with the wrong
        -DesktopId used to repoint the machine's Agent identity for real."""
        result = self._run_whatif()

        self.assertIn("NO_SIDE_EFFECT", result.stdout, result.stdout + result.stderr)

    def test_whatif_registers_no_task(self):
        result = self._run_whatif()

        self.assertIn("NO_TASK", result.stdout, result.stdout + result.stderr)
        self.assertNotIn("TASK_REGISTERED", result.stdout)

    def test_both_effects_are_announced_as_would_be_done(self):
        """Each guarded effect reports itself, so the operator sees both the
        environment write and the registration before agreeing to either.

        Asserted on the operation/target strings **the script supplies** to
        `ShouldProcess`, never on PowerShell's own preview prefix. That prefix
        is localized: measured on one machine, one script, one PowerShell
        5.1.26100.8875, only the parent process's UI culture differing --

            ko-KR   WhatIf: 대상 "user environment"에서 ... 수행합니다.
            en-US   What if: Performing the operation ... on target ...

        The old assertion was `assertIn("WhatIf", combined)`, which is the
        ko-KR spelling; it passes only where Windows displays in a language
        that leaves the prefix untranslated and fails on every English
        machine -- including the ones docs/11 deploys to. A test that pins
        the console language instead of the script's behaviour reports the
        wrong thing in both directions.

        The four strings below are written in `install_agent_task.ps1` and
        are echoed verbatim by every locale.
        """
        result = self._run_whatif()
        combined = result.stdout + result.stderr

        self.assertIn("Set COMPANY_OPS_* variables", combined)
        self.assertIn("user environment", combined)
        self.assertIn("Register scheduled task", combined)
        self.assertIn(self.TASK, combined)

    def test_the_optional_daily_trigger_also_builds(self):
        """-DailyAt takes a different cmdlet path; a bad argument there would
        only surface at install time on the one machine that uses it."""
        result = self._run_whatif(extra="-DailyAt '11:00'")
        combined = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, combined)
        self.assertIn("NO_SIDE_EFFECT", result.stdout)

    def test_an_invalid_desktop_id_is_refused_before_anything_runs(self):
        script = (
            f"& '{SCRIPT.as_posix()}' -DesktopId DESKTOP_9 "
            "-SyncFolder 'C:/Temp/x' -StartDate 2026-08-10 -WhatIf"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True,
            text=True,
            timeout=180,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Register scheduled task", result.stdout)

    def test_an_invalid_start_date_is_refused(self):
        script = (
            f"& '{SCRIPT.as_posix()}' -DesktopId DESKTOP_2 "
            "-SyncFolder 'C:/Temp/x' -StartDate '10/08/2026' -WhatIf"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True,
            text=True,
            timeout=180,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Register scheduled task", result.stdout)


class ParameterContractTests(unittest.TestCase):
    def test_the_desktop_id_is_restricted_to_the_real_profiles(self):
        """A typo'd Desktop id must fail at registration, not silently
        produce an Agent that cannot resolve its profile."""
        from reporter.profiles import PROFILES

        text = _script_text()
        match = re.search(r"ValidateSet\(([^)]*)\)", text)
        self.assertIsNotNone(match, "DesktopId has no ValidateSet")
        allowed = set(re.findall(r"'([A-Z_0-9]+)'", match.group(1)))
        self.assertEqual(allowed, set(PROFILES))

    def test_the_start_date_is_shape_checked(self):
        self.assertIn("ValidatePattern", _script_text())
        self.assertIn(r"^\d{4}-\d{2}-\d{2}$", _script_text())

    def test_the_three_required_parameters_are_mandatory(self):
        text = _script_text()
        for name in ("DesktopId", "SyncFolder", "StartDate"):
            with self.subTest(parameter=name):
                block = text[: text.index(f"${name}")]
                self.assertIn("Mandatory = $true", block.rsplit("[Parameter", 1)[-1])

    def test_whatif_is_supported(self):
        """An operator must be able to see what would be registered before
        changing anything on the machine."""
        text = _script_text()
        self.assertIn("SupportsShouldProcess = $true", text)
        self.assertIn("$PSCmdlet.ShouldProcess(", text)


class ATypoInTheSyncFolderIsSaidOutLoudTests(unittest.TestCase):
    """The one installer mistake nothing downstream can catch.

    Measured against this tree — `OneDriveTransport.send()` into a directory
    that does not exist:

        target exists before   False
        send()                 -> None      (success)
        target exists after    True
        files written          ['E1.json']

    `_write_atomic()` creates whatever directory it is handed
    (`mkdir(parents=True, exist_ok=True)`), so a typo'd `-SyncFolder` never
    fails. The Agent reports COLLECTED, its outbox drains, and every run on
    that machine looks correct — while the Events land in a folder nothing
    syncs to Desktop 4. Only Desktop 4's generic "this Desktop has been
    silent" alarm would eventually notice, days later, naming the wrong
    cause.

    **A warning rather than a throw, deliberately.** OneDrive may not have
    created or synced the folder yet at install time, which is a legitimate
    "install now, folder appears shortly" sequence — the same stance this
    script already takes toward timing it cannot control (`-DelayMinutes`).
    Refusing to install would break that case;
    `test_the_check_does_not_block_the_install` is what holds the
    distinction.
    """

    def test_the_installer_checks_the_folder_at_all(self):
        self.assertIn("Test-Path -LiteralPath $SyncFolder", _script_text())

    def test_it_warns_rather_than_throwing(self):
        text = _script_text()
        block = text[text.index("Test-Path -LiteralPath $SyncFolder"):]
        block = block[: block.index("}") + 1]

        self.assertIn("Write-Warning", block)
        self.assertNotIn("throw", block)

    def test_the_warning_says_what_goes_wrong_rather_than_that_something_did(self):
        """A path that does not exist is not itself an error — the operator
        needs the consequence to decide whether to care."""
        text = _script_text()
        line = next(
            l for l in text.splitlines() if "Write-Warning" in l and "SyncFolder" in l
        )

        self.assertIn("Desktop 4", line, "names who never sees the Events")
        self.assertIn("report success", line, "names why nothing else will catch it")
        self.assertIn("typo", line)

    def test_the_check_does_not_block_the_install(self):
        """The legitimate case: a fresh OneDrive share still syncing. The
        warning must say so, or an operator will treat it as a failure and
        stop."""
        line = next(
            l for l in _script_text().splitlines()
            if "Write-Warning" in l and "SyncFolder" in l
        )

        self.assertIn("normal", line)
        self.assertIn("ignored", line)

    def test_the_premise_the_transport_really_does_create_it(self):
        """Guards the guard. If the transport refused a missing folder the
        warning would be about nothing, and this class should be deleted
        rather than kept passing."""
        import tempfile

        sys.path.insert(0, str(REPO_ROOT / "src"))
        from events import create_event
        from transport.onedrive import OneDriveTransport

        scratch = Path(tempfile.mkdtemp())
        target = scratch / "OneDrve" / "CompanyOpsEvents"
        self.assertFalse(target.exists())

        # `outgoing_dir` is not optional decoration here (C123). Left out, it
        # defaults to `DEFAULT_OUTGOING_DIR` — `runtime/events/outgoing/` in
        # **this repository** — and this test wrote its fixture Event into the
        # live tree on every run. Measured: `E1.json` (`project_id "P"`,
        # `summary "s"`) was sitting there, beside a second one from
        # `test_observability.py`, when the session went looking.
        OneDriveTransport(
            sync_folder=target, outgoing_dir=scratch / "outgoing"
        ).send(
            create_event(
                source="DESKTOP_1",
                role="CTO_BACKEND",
                project_id="P",
                event_type="STARTED",
                status="IN_PROGRESS",
                summary="s",
                history_candidate=True,
                event_id="E1",
                timestamp="2026-08-15T09:00:00+09:00",
            )
        )

        self.assertTrue(target.is_dir(), "the transport created the typo'd folder")
        self.assertEqual([path.name for path in target.iterdir()], ["E1.json"])


class EntrypointContractTests(unittest.TestCase):
    def test_the_entrypoint_it_launches_exists(self):
        self.assertIn("run_agent.py", _script_text())
        self.assertTrue((REPO_ROOT / "run_agent.py").is_file())

    def test_it_refuses_to_register_a_missing_entrypoint(self):
        text = _script_text()
        self.assertIn("Test-Path -LiteralPath $entrypoint", text)
        self.assertIn("throw", text)

    def test_it_refuses_to_register_without_python_on_path(self):
        self.assertIn("Get-Command python", _script_text())

    def test_the_environment_variable_names_match_the_entrypoint(self):
        """The drift that would break every Desktop silently.

        The installer persists these three; `run_agent.py` reads them. A
        rename on either side yields a task that starts, fails on
        configuration, and exits 1 at every logon with nobody watching.
        """
        entrypoint = (REPO_ROOT / "run_agent.py").read_text(encoding="utf-8")
        script = _script_text()

        persisted = set(
            re.findall(r"SetEnvironmentVariable\(\s*'([A-Z_]+)'", script)
        )
        self.assertEqual(
            persisted,
            {
                "COMPANY_OPS_PROFILE",
                "COMPANY_OPS_AGENT_SYNC_FOLDER",
                "COMPANY_OPS_AGENT_START_DATE",
            },
        )
        for name in persisted:
            with self.subTest(variable=name):
                self.assertIn(name, entrypoint, f"{name} is set but never read")

    def test_every_variable_the_entrypoint_requires_is_persisted(self):
        """The other direction: a variable the Agent needs but the installer
        never sets produces the same silent failure."""
        script = _script_text()
        for name in (
            "COMPANY_OPS_PROFILE",
            "COMPANY_OPS_AGENT_SYNC_FOLDER",
            "COMPANY_OPS_AGENT_START_DATE",
        ):
            with self.subTest(variable=name):
                self.assertIn(name, script)

    def test_the_variables_are_documented_in_env_example(self):
        example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for name in (
            "COMPANY_OPS_PROFILE",
            "COMPANY_OPS_AGENT_SYNC_FOLDER",
            "COMPANY_OPS_AGENT_START_DATE",
        ):
            with self.subTest(variable=name):
                self.assertIn(name, example)


class SchedulerPolicyTests(unittest.TestCase):
    """docs/07 §53-58: the trigger shape this deployment settled on."""

    def test_it_registers_a_logon_trigger(self):
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", _script_text())

    def test_duplicate_protection_exists_at_both_layers(self):
        """§55: Windows-level protection AND the application's own lock."""
        text = _script_text()
        self.assertIn("MultipleInstances IgnoreNew", text)
        # The application-level half lives in the Agent; assert it is real
        # rather than only described in the script's comments.
        from agent import DEFAULT_LOCK_PATH

        self.assertTrue(str(DEFAULT_LOCK_PATH).endswith("agent.lock"))

    def test_a_missed_trigger_runs_at_the_next_opportunity(self):
        """§56: a PC that was off must not simply skip that run — that is
        what makes the schedule not a single point of failure."""
        self.assertIn("StartWhenAvailable", _script_text())

    def test_a_startup_delay_is_applied_on_the_trigger(self):
        """§54: right after logon, network and OneDrive may not be ready.

        Checked against executable code, and against the *mechanism* rather
        than a keyword. The previous version asserted that the text
        contained "RandomDelay", which stayed true while the delay was being
        applied through a CIM property that does not exist — the assertion
        passed on a script that crashed before registering anything.
        """
        code = _script_code()
        self.assertIn("DelayMinutes", code)
        self.assertIn("$logonTrigger.Delay", code)
        self.assertIn('"PT$($DelayMinutes)M"', code)

    def test_the_delay_is_not_applied_through_a_settings_cim_property(self):
        """The specific shape that failed: `New-ScheduledTaskSettingsSet`
        has no RandomDelay property, so indexing it yields $null and the
        assignment throws before Register-ScheduledTask is reached."""
        code = _script_code()
        self.assertNotIn("CimInstanceProperties", code)

    def test_the_task_name_is_namespaced_per_desktop(self):
        """Four Desktops each register their own task; one shared name would
        mean the last install silently replaced the others."""
        text = _script_text()
        self.assertIn("DOJOONPASS_COMPANY_OPS_AGENT_$DesktopId", text)

    def test_the_task_does_not_keep_the_machine_awake(self):
        """docs/07 §58: OFF 허용 + Catch-up. A wake timer would contradict
        the deployment decision this whole design rests on."""
        text = _script_code()
        self.assertNotIn("WakeToRun", text)
        self.assertNotIn("-Wake", text)

    def test_an_execution_time_limit_bounds_a_hung_run(self):
        self.assertIn("ExecutionTimeLimit", _script_text())


class RegistrationFailureHandlingTests(unittest.TestCase):
    """Registration is the one step that can fail on a healthy machine for
    reasons the operator can act on, and it used to report the WRONG ones.

    The previous version of this docstring recorded, as verified fact, that
    `Register-ScheduledTask` refuses everything from a non-elevated session
    "even for a minimal `cmd.exe /c exit` task, with and without an explicit
    -User or -Principal", and concluded the refusal was about where the task
    is written. That was measured once and then trusted.

    Re-measured on the same machine, non-elevated:

        cmd.exe /c exit + Once trigger              registers
        + full SettingsSet / -Force / -Description  registers
        Daily -At 09:00                             registers
        AtLogOn -User <me>                          registers
        AtLogOn   (no -User)                        Access is denied
        AtStartup                                   Access is denied

    Only the machine-wide trigger shapes are refused. The installer used the
    unscoped `-AtLogOn`, so it could never have registered on ANY
    non-administrator machine — and the failure was filed as "this
    environment cannot register tasks", which sent every subsequent reader
    looking for an administrator instead of at one missing argument.

    The lesson worth keeping: a measurement recorded as a conclusion
    ("cannot") outlives the evidence it came from. This class now asserts
    the trigger scope and the corrected message, so the claim is re-checked
    on every run rather than remembered.

    Registration itself is exercised for real outside the suite; no test
    here registers anything, because a test that succeeded would leave a
    scheduled task on the machine running it.
    """

    def test_registration_failure_is_explained_not_re_raised_bare(self):
        code = _script_code()
        register_at = code.index("Register-ScheduledTask")
        tail = code[register_at:]

        self.assertIn("catch", tail)
        self.assertIn("Scheduled task registration was refused", tail)

    def test_the_explanation_names_concrete_next_steps(self):
        code = _script_code()
        for hint in (
            "Get-Service Schedule",
            "elevated PowerShell",
            # The throwaway-task probe. It is the step that distinguishes a
            # blanket restriction from a trigger-scope problem, which is the
            # distinction the old message got wrong.
            "Register-ScheduledTask -TaskName Probe1",
        ):
            with self.subTest(hint=hint):
                self.assertIn(hint, code)

    def test_the_explanation_no_longer_claims_elevation_is_the_first_answer(self):
        """The previous message asserted that even a bare
        `Register-ScheduledTask ... cmd.exe /c exit` fails identically for a
        non-elevated session, concluded the cause was "where the task is
        written", and put "run as administrator" first.

        Re-measured on a real non-elevated session: that bare call
        *succeeds*, and so does every trigger shape except a machine-wide
        one. The actual cause was this script's own trigger missing `-User`
        — an operator following the old advice would have gone hunting for
        admin rights they did not need, to fix a bug in this file.

        So elevation must still be offered, but last.
        """
        code = _script_code()
        elevated_at = code.index("elevated PowerShell")
        service_at = code.index("Get-Service Schedule")

        self.assertLess(service_at, elevated_at, "elevation is still suggested first")
        self.assertIn("non-administrator CAN normally register", code)

    def test_the_logon_trigger_is_scoped_to_the_invoking_user(self):
        """Two independent reasons, both load-bearing.

        Correctness: an unscoped `-AtLogOn` fires at ANY user's logon, and
        the Agent reads its identity and sync folder from the *user*
        environment this script writes. Firing under another account would
        run this Desktop's Agent with none of its configuration.

        Registrability: an any-user logon trigger is machine-wide, so
        Windows refuses it without elevation. Measured — this single missing
        argument is why the installer had never registered a task on any
        non-administrator machine.
        """
        code = _script_code()

        self.assertIn("New-ScheduledTaskTrigger -AtLogOn -User $currentUser", code)
        self.assertIn(r'$currentUser = "$env:USERDOMAIN\$env:USERNAME"', code)

    def test_the_explanation_says_what_was_and_was_not_done(self):
        """An operator who has just seen a failure needs to know whether the
        machine was left half-configured."""
        code = _script_code()
        self.assertIn("Nothing was registered", code)
        self.assertIn("idempotent", code)

    def test_a_silent_non_registration_is_caught(self):
        """Register-ScheduledTask returning without error while the task does
        not exist would send the operator away believing the Agent is
        scheduled. That is precisely the silent failure this step exists to
        prevent."""
        code = _script_code()
        self.assertIn("Get-ScheduledTask -TaskName $taskName", code)
        self.assertIn("does not exist", code)

    def test_the_verification_happens_after_registration(self):
        code = _script_code()
        self.assertLess(
            code.index("Register-ScheduledTask"),
            code.index("$registered = Get-ScheduledTask"),
        )

    def test_success_is_only_reported_after_the_task_is_confirmed(self):
        code = _script_code()
        self.assertLess(
            code.index("$registered = Get-ScheduledTask"),
            code.index('Write-Host "Registered:'),
        )


class SecretSafetyTests(unittest.TestCase):
    def test_the_script_never_handles_a_secret(self):
        """An Agent needs an identifier, a folder, and a date. Notion tokens
        are Desktop 4's business and must never reach a Desktop 1 installer."""
        text = _script_code()
        for forbidden in (
            "NOTION_API_TOKEN",
            "NOTION_PROJECTS_DATABASE_ID",
            "NOTION_OPS_RUNS_DATABASE_ID",
            "Password",
            "-Credential",
        ):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, text)

    def test_no_secret_shaped_literal_is_present(self):
        text = _script_text()  # help text included: a pasted token would land there too
        for pattern in (
            r"\bntn_[A-Za-z0-9]{10,}",
            r"\bsecret_[A-Za-z0-9]{10,}",
            r"Bearer\s+[A-Za-z0-9._-]{20,}",
        ):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))

    def test_it_registers_the_task_for_the_current_user_only(self):
        """No -User SYSTEM: the Agent writes into the operator's own
        OneDrive folder, which a SYSTEM-context task cannot reach."""
        text = _script_code()
        self.assertNotIn("-User SYSTEM", text)
        self.assertNotIn("RunLevel Highest", text)


if __name__ == "__main__":
    unittest.main()
