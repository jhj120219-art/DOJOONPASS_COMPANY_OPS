"""`src/schedtask.py` — reading back the two scheduled tasks that start everything.

Two kinds of test here, and the split is deliberate.

Most of them inject `run`, so the whole classifier is exercised against every
answer Windows can give — including the ones that cannot be produced on
demand (a disabled task, a task Windows terminated, a task that exited 1).
Registering a real scheduled task to produce them would change the machine's
configuration, and this module exists to *notice* such changes; a test suite
that installs and removes scheduled tasks is a test suite that can leave one
behind.

The rest talk to the real Task Scheduler, because "we parsed a fixture we
wrote ourselves" is exactly the illusion this repository keeps finding
(BACKLOG E-6: "정적 검증했다와 동작한다는 다르다"). They assert only what is
true on any Windows machine — that the query runs, answers for every name
asked, and that a name nothing registered comes back `NOT_REGISTERED` rather
than as an error — so they need no fixture and change nothing.
"""

# `str | None` in `_drive()`'s signature below. Without this, that
# annotation is evaluated at import time and aborts pytest collection
# for the WHOLE suite on Python < 3.10 — see
# `EveryTrackedModuleParsesOnThisInterpreterTests`. It arrived when the
# executed command-line tests moved here from the three installer test
# files, one of which already carried this import for the same reason.
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

import schedtask  # noqa: E402

ON_WINDOWS = os.name == "nt"


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """A `subprocess.run` stand-in returning one fixed result."""

    def run(*args, **kwargs):
        run.calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=returncode,
            stdout=stdout, stderr=stderr,
        )

    run.calls = []
    return run


def _row(name: str, **fields) -> str:
    import json

    row = {"name": name, "present": True}
    row.update(fields)
    return json.dumps(row)


class TaskNamesMatchTheInstallersTests(unittest.TestCase):
    """There is one place these names live, and the installers ask it.

    **This class used to compare two copies.** The task name and the log
    filename were literals in `src/schedtask.py` *and* in each
    `scripts/install_*_task.ps1`, and the tests here read both and asserted
    they agreed. That catches drift; it does not prevent it, and a
    repository whose whole discipline is removing hand-written rosters had
    grown one across two languages.

    They now derive. Each installer runs

        python -c "import schedtask; print(...); print(scheduled_log_name(...))"

    right after it resolves python on PATH -- which it already required,
    because it bakes `$python.Source` into the action it registers. So the
    dependency is not new; the second copy is simply gone.

    `schedtask` is the source rather than the scripts because it is the side
    that *reasons* about the values: `ops_status.py` must know the name to
    ask Windows about it, `scheduled_log_name()` maps a name to its log, and
    `agent_task_name()` builds the per-Desktop form. PowerShell writes them
    down once, at install time.

    So what is asserted here changed shape. Not "the two agree" but "there
    is only one", which is a property a literal cannot satisfy by accident:
    no installer may contain one of these strings at all, and each must ask.
    The end-to-end proof that asking works is in each installer's own
    `-WhatIf` test, which runs the real script and reads the name Windows
    was going to be given.
    """

    @staticmethod
    def _installers():
        return sorted((REPO_ROOT / "scripts").glob("install_*_task.ps1"))

    @staticmethod
    def _code(path):
        """The script with comment-based help and `#` comments removed.

        Every rule below also appears in prose explaining it -- including
        the very literals this class forbids, quoted in `.NOTES` for an
        operator to paste into `Get-ScheduledTask`. A scan over raw text
        would read that documentation as the violation it describes.
        """
        text = re.sub(r"<#.*?#>", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    def test_the_scan_finds_every_installer(self):
        """Guards the guard: every assertion below is a negative over this
        list. Named individually so a renamed installer fails here rather
        than shrinking the sweep in silence."""
        self.assertEqual(
            {path.name for path in self._installers()},
            {
                "install_runner_task.ps1",
                "install_agent_task.ps1",
                "install_publish_task.ps1",
            },
        )

    def test_the_comment_stripper_would_notice_a_literal_that_is_only_documented(self):
        """The predicate this class rests on. `install_agent_task.ps1`'s
        `.NOTES` block really does contain the task name, for an operator to
        copy -- so a scan that could not tell help text from code would fail
        on a correct script."""
        raw = (REPO_ROOT / "scripts" / "install_agent_task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("DOJOONPASS_COMPANY_OPS_AGENT_*", raw)
        self.assertNotIn(
            "DOJOONPASS_COMPANY_OPS_AGENT_*",
            self._code(REPO_ROOT / "scripts" / "install_agent_task.ps1"),
        )

    def test_no_installer_hard_codes_a_task_name(self):
        """The property that replaced "the two copies agree". A literal here
        is a second source of truth, and this is what stops one coming back.
        """
        for path in self._installers():
            with self.subTest(installer=path.name):
                self.assertNotIn("DOJOONPASS_COMPANY_OPS", self._code(path))

    def test_no_installer_hard_codes_a_log_filename(self):
        """`scheduled_log_name()` is the one mapping from task to log, and
        `ops_status.py` reads that file when a scheduled run fails. A second
        spelling of the name sends an operator to a file nothing writes."""
        for path in self._installers():
            with self.subTest(installer=path.name):
                # The *filename*, not the function that returns it: the
                # probe legitimately contains `scheduled_log_name(`, and a
                # substring test for "scheduled_" flagged all three correct
                # scripts on the first run.
                self.assertEqual(
                    re.findall(r"scheduled_\w+\.log", self._code(path)), []
                )

    def test_every_installer_asks_this_module_for_both(self):
        """Structural: it is not enough that the literals are gone -- the
        values have to come from somewhere, and this is where."""
        for path in self._installers():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertIn("import schedtask", code)
                self.assertIn("schedtask.scheduled_log_name(", code)
                self.assertIn("$taskName = $taskFacts[0].Trim()", code)
                self.assertIn("$logPath = Join-Path $logDir $logFileName", code)

    def test_every_installer_refuses_to_register_if_it_cannot_ask(self):
        """The failure that matters. A probe that returned nothing and was
        not checked would register a task named the empty string -- or worse,
        succeed with a name nothing monitors. Refusing is right: if
        `import schedtask` fails from this checkout, the entrypoint the task
        would run imports the same tree every morning."""
        for path in self._installers():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertIn("IsNullOrWhiteSpace($taskFacts[0])", code)
                self.assertLess(
                    code.index("IsNullOrWhiteSpace($taskFacts[0])"),
                    code.index("Register-ScheduledTask"),
                )

    def test_this_module_names_a_log_for_every_task_it_knows(self):
        """The Python side's own consistency, which the installers now
        depend on: `scheduled_log_name()` must answer for every name in the
        table, and the answers must be distinct -- two tasks appending to
        one file would interleave, and the tail printed for a failed Runner
        could be the Agent's."""
        names = [
            schedtask.RUNNER_TASK_NAME,
            schedtask.PUBLISH_TASK_NAME,
            schedtask.agent_task_name("DESKTOP_1"),
        ]
        logs = [schedtask.scheduled_log_name(name) for name in names]

        self.assertNotIn(None, logs)
        self.assertEqual(sorted(logs), sorted(set(logs)), logs)
        self.assertEqual(len(schedtask.SCHEDULED_LOG_NAMES), len(names))

    def test_every_desktop_id_produces_a_queryable_name(self):
        """The names are interpolated into a PowerShell literal, and
        `build_query` refuses anything `_TASK_NAME_RE` rejects. A Desktop id
        the project actually uses must not be one of them -- otherwise the
        Agent block reports UNKNOWN on a correctly installed machine."""
        from reporter.profiles import PROFILES

        for desktop_id in PROFILES:
            with self.subTest(desktop=desktop_id):
                schedtask.build_query([schedtask.agent_task_name(desktop_id)])

    # ------------------------------------- the string cmd actually receives

    _COMMAND_LINE_RE = re.compile(r"^\$commandLine\s*=\s*('.+?')\s*-f", re.M)

    def _declared_command_line(self, path):
        source = path.read_text(encoding="utf-8")
        found = self._COMMAND_LINE_RE.findall(source)
        self.assertEqual(len(found), 1, f"{path.name}: expected one $commandLine")
        return found[0]

    def test_every_installer_builds_the_same_command_line(self):
        """One shape, three installers.

        **This is what makes the four executed tests below cover all three
        without running three times.** C138 first wrote them out per
        installer -- twelve PowerShell launches, ~1.2 s each, asserting one
        identical property three times over. That is the same hand-copied
        roster this file removed for task names, wearing a test's clothes.

        If a fourth installer ever declares a different shape this fails,
        and whoever wrote it decides: make it the same, or give it its own
        executed coverage. What it cannot do is arrive unnoticed.
        """
        declared = {
            path.name: self._declared_command_line(path)
            for path in self._installers()
        }

        self.assertEqual(
            len(set(declared.values())),
            1,
            f"installers build different command lines: {declared}",
        )

    @property
    def COMMAND_LINE_PATTERN(self):
        """The shape, read off disk rather than restated here.

        The tests below execute what the installers actually build. Pinning
        a copy in this file would let them go on passing against a shape no
        installer produces.
        """
        return self._declared_command_line(self._installers()[0])

    def _drive(self, program: str, python: str | None = None):
        """Run the declared command line against a probe program.

        Through `Start-Process`, which is how Task Scheduler observes an
        action: the argument string reaches the shell verbatim and the exit
        code comes back from the process. Registering a real scheduled task
        to test cmd's quoting would change the machine's configuration for
        nothing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(program, encoding="utf-8")
            # A directory with a space in it. `Documents and Settings`-shaped
            # paths are exactly what unquoted redirection breaks on, and that
            # failure would only appear on the machine that had one.
            log_dir = Path(tmp) / "log dir"
            log_dir.mkdir()
            log = log_dir / "scheduled.log"

            driver = Path(tmp) / "drive.ps1"
            driver.write_text(
                "param([string]$Py, [string]$Entry, [string]$Log)\n"
                "$commandLine = " + self.COMMAND_LINE_PATTERN
                + " -f $Py, $Entry, $Log\n"
                "$p = Start-Process -FilePath $env:ComSpec "
                "-ArgumentList $commandLine -NoNewWindow -Wait -PassThru\n"
                'Write-Output "EXIT:$($p.ExitCode)"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(driver),
                 "-Py", python or sys.executable, "-Entry", str(probe),
                 "-Log", str(log)],
                capture_output=True, text=True, timeout=180,
            )
            written = (
                log.read_text(encoding="utf-8", errors="replace")
                if log.is_file() else None
            )
            return result, written

    @unittest.skipUnless(ON_WINDOWS, "cmd.exe is Windows-only")
    def test_both_streams_reach_the_log(self):
        result, written = self._drive(
            "import sys\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "sys.stderr.reconfigure(encoding='utf-8')\n"
            "print('to stdout')\n"
            "print('to stderr', file=sys.stderr)\n"
        )
        self.assertIsNotNone(written, result.stdout + result.stderr)
        self.assertIn("to stdout", written)
        self.assertIn("to stderr", written, "2>&1 did not reach the same file")

    @unittest.skipUnless(ON_WINDOWS, "cmd.exe is Windows-only")
    def test_the_entrypoint_still_receives_no_arguments(self):
        """Every entrypoint refuses arguments it cannot honour
        (`cli.unexpected_arguments`), so a redirection token leaking into
        `sys.argv` would make the task exit 1 forever -- the failure this
        redirection exists to make visible, caused by the redirection
        itself. Measured rather than reasoned about: cmd consumes `>>`, the
        path and `2>&1`, and passes on nothing."""
        _result, written = self._drive(
            "import sys\nprint('ARGV=' + repr(sys.argv[1:]))\n"
        )
        self.assertEqual((written or "").strip(), "ARGV=[]")

    @unittest.skipUnless(ON_WINDOWS, "cmd.exe is Windows-only")
    def test_the_exit_code_survives_the_shell(self):
        """`LastTaskResult` is how the SCHEDULE block decides a scheduled run
        failed, and docs/14 section 4 gives each number a meaning -- `3` is
        `publish_control_tower.py`'s DEGRADED, which exists for this
        deployment specifically. A wrapper that swallowed or rewrote the
        code would make every one of those readings a lie."""
        for code in (1, 3, 7):
            with self.subTest(exit_code=code):
                result, _written = self._drive(f"import sys\nsys.exit({code})\n")
                self.assertIn(
                    f"EXIT:{code}", result.stdout, result.stdout + result.stderr
                )

    @unittest.skipUnless(ON_WINDOWS, "cmd.exe is Windows-only")
    def test_a_python_that_is_not_there_is_recorded_rather_than_lost(self):
        """The failure with no other witness: nothing under `runtime/` is
        written, no log line, no manifest -- before the redirection, the
        whole of the evidence was a `LastTaskResult` of 1."""
        with tempfile.TemporaryDirectory() as tmp:
            _result, written = self._drive(
                "print('unreachable')\n",
                python=str(Path(tmp) / "no-such-python.exe"),
            )
        self.assertTrue(written, "nothing was recorded at all")


class TheQueryRefusesNamesItCannotSafelyEmbedTests(unittest.TestCase):
    """`build_query` interpolates into a single-quoted PowerShell string.

    A name holding a quote would end that literal and the rest would be
    executed. The names are built from `COMPANY_OPS_PROFILE`, an environment
    variable — `resolve_profile()` constrains it today, but this module
    cannot see that its caller checked.
    """

    def test_a_name_that_could_end_the_literal_is_refused(self):
        with self.assertRaises(ValueError):
            schedtask.build_query(["X'; Remove-Item C:\\ -Recurse; '"])

    def test_a_name_with_a_space_is_refused(self):
        """Real machines have such tasks — measured on this one, every task
        in the root folder has a space or a brace. Ours never do, and a
        pattern that admitted them would admit the quote too."""
        with self.assertRaises(ValueError):
            schedtask.build_query(["OneDrive Startup Task"])

    def test_an_empty_name_is_refused(self):
        with self.assertRaises(ValueError):
            schedtask.build_query([""])

    def test_query_reports_the_refusal_instead_of_raising(self):
        """`query()` is called from a diagnostic that must still answer.
        The refusal becomes `query_error`, not an exception."""
        result = schedtask.query(["bad name"], run=_completed(), is_windows=True)
        self.assertIsNotNone(result["bad name"].query_error)
        self.assertEqual(schedtask.classify(result["bad name"]), schedtask.UNKNOWN)

    def test_a_refused_name_never_reaches_powershell(self):
        run = _completed()
        schedtask.query(["bad name"], run=run, is_windows=True)
        self.assertEqual(run.calls, [], "the refusal must precede the subprocess")

    def test_the_query_pins_the_root_task_folder(self):
        """Without `-TaskPath '\\'`, `Get-ScheduledTask -TaskName X` matches
        any folder, and a same-named task elsewhere in the tree would be
        reported as ours. Both installers register in the root."""
        script = schedtask.build_query(["A"])
        self.assertEqual(script.count("-TaskPath '\\'"), 2, script)


class ClassifyAnswersInTheOrderTheQuestionsMatterTests(unittest.TestCase):
    """The ordering inside `classify()` is the substance, not the mapping.

    A disabled task keeps whatever `LastTaskResult` it had when it last ran,
    which is usually `0`. Asking about the result before asking whether the
    task can still fire reports a task that will never run again as HEALTHY —
    the exact silent-success shape this module was written against.
    """

    def test_a_query_that_did_not_answer_is_unknown_not_absent(self):
        status = schedtask.ScheduledTaskStatus(
            name="T", present=False, query_error="powershell을 실행할 수 없습니다"
        )
        self.assertEqual(schedtask.classify(status), schedtask.UNKNOWN)
        self.assertNotIn(schedtask.UNKNOWN, schedtask.NEEDS_ATTENTION)

    def test_windows_saying_the_task_is_absent_is_not_registered(self):
        status = schedtask.ScheduledTaskStatus(name="T", present=False)
        self.assertEqual(schedtask.classify(status), schedtask.NOT_REGISTERED)
        self.assertIn(schedtask.NOT_REGISTERED, schedtask.NEEDS_ATTENTION)

    def test_a_disabled_task_that_last_succeeded_is_still_disabled(self):
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Disabled", last_result=0,
            last_run="2026-08-30T11:00:00+09:00",
        )
        self.assertEqual(schedtask.classify(status), schedtask.DISABLED)

    def test_a_registered_task_that_has_never_run_is_not_a_failure(self):
        """Every correctly installed task is in this state until its first
        trigger. Raising it would make a good install look broken."""
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Ready",
            last_result=schedtask.RESULT_HAS_NOT_RUN,
            last_run="1999-11-30T00:00:00+09:00",
        )
        self.assertEqual(schedtask.classify(status), schedtask.NEVER_RUN)
        self.assertNotIn(schedtask.NEVER_RUN, schedtask.NEEDS_ATTENTION)

    def test_a_running_task_is_not_a_failure(self):
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Running",
            last_result=schedtask.RESULT_STILL_RUNNING,
            last_run="2026-08-31T11:00:00+09:00",
        )
        self.assertEqual(schedtask.classify(status), schedtask.RUNNING)
        self.assertNotIn(schedtask.RUNNING, schedtask.NEEDS_ATTENTION)

    def test_a_skipped_trigger_is_not_a_failure(self):
        """`-MultipleInstances IgnoreNew` (docs/07 section 55) working as
        intended: the previous run was still going, so Windows dropped the
        new trigger. Reporting that as a failed run would raise ATTENTION
        every time a catch-up overran."""
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Ready",
            last_result=schedtask.RESULT_ALREADY_RUNNING,
            last_run="2026-08-31T11:00:00+09:00",
        )
        self.assertEqual(schedtask.classify(status), schedtask.HEALTHY)

    def test_a_terminated_run_is_reported_separately_from_a_failed_one(self):
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Ready",
            last_result=schedtask.RESULT_TERMINATED,
            last_run="2026-08-31T11:00:00+09:00",
        )
        self.assertEqual(schedtask.classify(status), schedtask.LAST_RUN_TERMINATED)
        self.assertIn(schedtask.LAST_RUN_TERMINATED, schedtask.NEEDS_ATTENTION)

    def test_this_projects_own_exit_codes_are_reported_as_failures(self):
        """`LastTaskResult` *is* the process exit code for anything that
        started. 1/2/3 are this project's own (AGENT.md section 6), and a
        deployment whose python cannot find its configuration exits 1 every
        single morning while leaving nothing in `runtime/` to show for it."""
        for code in (1, 2, 3):
            with self.subTest(code=code):
                status = schedtask.ScheduledTaskStatus(
                    name="T", present=True, state="Ready", last_result=code,
                    last_run="2026-08-31T11:00:00+09:00",
                )
                self.assertEqual(
                    schedtask.classify(status), schedtask.LAST_RUN_FAILED
                )

    def test_a_successful_run_is_healthy(self):
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Ready", last_result=0,
            last_run="2026-08-31T11:00:00+09:00",
            next_run="2026-09-01T11:00:00+09:00",
        )
        self.assertEqual(schedtask.classify(status), schedtask.HEALTHY)

    def test_every_verdict_classify_can_return_is_named_in_the_module(self):
        """A verdict added without a name would be compared against a
        literal in `ops_status.py` and silently never match."""
        source = ast.parse((SRC / "schedtask.py").read_text(encoding="utf-8"))
        returned = set()
        for node in ast.walk(source):
            if isinstance(node, ast.FunctionDef) and node.name == "classify":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Name):
                        returned.add(inner.value.id)
        self.assertTrue(returned, "the scan found no verdicts — it has gone blind")
        for name in sorted(returned):
            with self.subTest(verdict=name):
                self.assertTrue(
                    hasattr(schedtask, name),
                    f"classify() returns {name}, which the module does not define",
                )


class TheNeverRunSentinelIsNotAAaRunTests(unittest.TestCase):
    """Windows reports `1999-11-30T00:00:00` as `LastRunTime` for a task that
    has never run. Measured on this machine, on a vendor task installed and
    never triggered. Printed verbatim it claims the job last ran in 1999."""

    def test_the_sentinel_is_not_treated_as_a_run(self):
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, last_run="1999-11-30T00:00:00.0000000+09:00"
        )
        self.assertFalse(status.has_ever_run)

    def test_a_real_run_is(self):
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, last_run="2026-08-28T08:02:07.0000000+09:00"
        )
        self.assertTrue(status.has_ever_run)

    def test_no_last_run_at_all_is_not_a_run(self):
        self.assertFalse(
            schedtask.ScheduledTaskStatus(name="T", present=True).has_ever_run
        )

    def test_the_sentinel_offset_is_not_assumed(self):
        """The sentinel is a *local* midnight, so its offset is whatever the
        reading machine's is. Matching the whole instant would work on this
        machine and silently stop working on a UTC one."""
        for offset in ("+09:00", "+00:00", "-05:00"):
            with self.subTest(offset=offset):
                status = schedtask.ScheduledTaskStatus(
                    name="T", present=True,
                    last_run=f"1999-11-30T00:00:00.0000000{offset}",
                )
                self.assertFalse(status.has_ever_run)


class DescribeResultNamesWhatAnOperatorCanLookUpTests(unittest.TestCase):
    def test_the_hex_form_is_present_for_an_unknown_code(self):
        """Task Scheduler's own UI shows hex; the decimal PowerShell returns
        matches nothing an operator can search for."""
        self.assertIn("0x80070002", schedtask.describe_result(0x80070002))

    def test_a_negative_signed_result_is_shown_unsigned(self):
        """PowerShell returns the HRESULT range as a signed 32-bit int.
        `-2147024894` printed as-is matches nothing."""
        self.assertIn("0x80070002", schedtask.describe_result(-2147024894))

    def test_the_non_failure_codes_do_not_read_as_failures(self):
        for code in sorted(schedtask.NON_FAILURE_RESULTS):
            with self.subTest(code=code):
                self.assertNotIn("실패", schedtask.describe_result(code))

    def test_no_result_at_all_is_said_rather_than_guessed(self):
        self.assertEqual(schedtask.describe_result(None), "결과 없음")


class ParsingWhatWindowsActuallyWroteTests(unittest.TestCase):
    """The NDJSON contract, against rows in the exact shape measured on this
    machine (see the module docstring for why it is NDJSON and not an array).
    """

    #: Byte-for-byte what `build_query`'s script produced here for a real
    #: registered task. Kept verbatim rather than rebuilt from a dict: the
    #: point of the fixture is that it is what Windows wrote, not what this
    #: test thinks Windows writes.
    REAL_ROW = (
        '{"name":"T","present":true,"state":"Ready","last_result":0,'
        '"last_run":"2026-08-28T08:02:07.0000000+09:00","next_run":null,"missed":0}'
    )
    REAL_ABSENT_ROW = '{"name":"U","present":false}'

    def test_a_real_row_parses_into_every_field(self):
        status = schedtask.parse_query_output(self.REAL_ROW, ["T"])["T"]
        self.assertEqual(
            (status.present, status.state, status.last_result, status.missed_runs),
            (True, "Ready", 0, 0),
        )
        self.assertEqual(status.last_run, "2026-08-28T08:02:07.0000000+09:00")
        self.assertIsNone(status.next_run)
        self.assertIsNone(status.query_error)

    def test_a_real_absent_row_is_absent_and_not_an_error(self):
        status = schedtask.parse_query_output(self.REAL_ABSENT_ROW, ["U"])["U"]
        self.assertFalse(status.present)
        self.assertIsNone(status.query_error)

    def test_every_requested_name_gets_an_entry(self):
        result = schedtask.parse_query_output(self.REAL_ROW, ["T", "U"])
        self.assertEqual(sorted(result), ["T", "U"])

    def test_a_name_the_query_did_not_answer_for_is_unknown_not_absent(self):
        """The distinction the whole module turns on: `NOT_REGISTERED` is a
        statement about the machine, and a missing row is a statement about
        the query. Reporting the second as the first is a false alarm about
        the one subject an operator would drop everything for."""
        status = schedtask.parse_query_output(self.REAL_ROW, ["T", "U"])["U"]
        self.assertIsNotNone(status.query_error)
        self.assertEqual(schedtask.classify(status), schedtask.UNKNOWN)

    def test_one_mangled_line_does_not_lose_the_other_answers(self):
        text = "not json at all\n" + self.REAL_ROW + "\n"
        result = schedtask.parse_query_output(text, ["T"])
        self.assertTrue(result["T"].present)

    def test_deeply_nested_json_does_not_crash_the_parser(self):
        """BUG-40's shape: `json.loads` raises `RecursionError`, not
        `ValueError`, and a handler catching only the latter turns a
        malformed input into a crash inside a diagnostic."""
        text = "[" * 5000 + "]" * 5000 + "\n" + self.REAL_ROW
        result = schedtask.parse_query_output(text, ["T"])
        self.assertTrue(result["T"].present)

    def test_a_json_row_that_is_not_an_object_is_skipped(self):
        result = schedtask.parse_query_output('["T"]\n' + self.REAL_ROW, ["T"])
        self.assertTrue(result["T"].present)

    def test_a_boolean_where_a_result_code_belongs_is_not_read_as_one(self):
        """`True` is an `int` in Python and would become `1` — this
        project's configuration-error exit code. A type confusion must not
        become a specific diagnosis of a failure that did not happen."""
        row = _row("T", state="Ready", last_result=True, last_run="2026-08-31T11:00:00+09:00")
        status = schedtask.parse_query_output(row, ["T"])["T"]
        self.assertIsNone(status.last_result)
        self.assertNotEqual(schedtask.classify(status), schedtask.LAST_RUN_FAILED)

    def test_a_string_where_a_result_code_belongs_is_dropped(self):
        row = _row("T", state="Ready", last_result="0")
        self.assertIsNone(schedtask.parse_query_output(row, ["T"])["T"].last_result)

    def test_empty_output_makes_every_name_unknown(self):
        result = schedtask.parse_query_output("", ["T", "U"])
        for name, status in result.items():
            with self.subTest(name=name):
                self.assertEqual(schedtask.classify(status), schedtask.UNKNOWN)


class AnUnanswerableQueryIsAnAnswerTests(unittest.TestCase):
    """`query()` never raises. It is called from `ops_status.py`, whose whole
    contract is to keep answering when part of its evidence is unavailable —
    a report that died because PowerShell was missing would be reporting on
    itself."""

    NAMES = ("DOJOONPASS_COMPANY_OPS_DAILY",)

    def test_a_missing_powershell_is_reported_not_raised(self):
        def run(*args, **kwargs):
            raise FileNotFoundError(2, "The system cannot find the file specified")

        status = schedtask.query(self.NAMES, run=run, is_windows=True)[self.NAMES[0]]
        self.assertIsNotNone(status.query_error)
        self.assertIn("powershell", status.query_error)

    def test_a_denied_powershell_is_reported_not_raised(self):
        """`PermissionError` is an `OSError` too, and a handler written for
        `FileNotFoundError` alone would let it escape."""

        def run(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        status = schedtask.query(self.NAMES, run=run, is_windows=True)[self.NAMES[0]]
        self.assertIsNotNone(status.query_error)

    def test_a_timeout_is_reported_not_raised(self):
        def run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="powershell", timeout=30.0)

        status = schedtask.query(self.NAMES, run=run, is_windows=True)[self.NAMES[0]]
        self.assertIn("30", status.query_error)

    def test_the_query_is_bounded_so_a_diagnostic_cannot_hang(self):
        run = _completed(stdout=_row("DOJOONPASS_COMPANY_OPS_DAILY", state="Ready"))
        schedtask.query(self.NAMES, run=run, is_windows=True)
        _, kwargs = run.calls[0]
        self.assertEqual(kwargs["timeout"], schedtask.QUERY_TIMEOUT_SECONDS)

    def test_the_subprocess_cannot_fail_to_decode(self):
        """`git_ops._run_git()`'s lesson: without an explicit codec the
        decode happens in subprocess's reader thread, the exception never
        reaches this caller, and `stdout` silently becomes `None`."""
        run = _completed(stdout="")
        schedtask.query(self.NAMES, run=run, is_windows=True)
        _, kwargs = run.calls[0]
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_a_failed_query_with_no_output_is_unknown(self):
        run = _completed(returncode=1, stderr="Access is denied")
        status = schedtask.query(self.NAMES, run=run, is_windows=True)[self.NAMES[0]]
        self.assertIsNotNone(status.query_error)
        self.assertIn("Access is denied", status.query_error)

    def test_a_nonzero_exit_that_still_produced_answers_keeps_them(self):
        """PowerShell can write a warning to stderr and exit non-zero while
        the objects it already emitted are correct. Discarding good answers
        because the shell was unhappy afterwards loses the whole report."""
        run = _completed(
            stdout=_row("DOJOONPASS_COMPANY_OPS_DAILY", state="Ready", last_result=0,
                        last_run="2026-08-31T11:00:00+09:00"),
            stderr="some warning",
            returncode=1,
        )
        status = schedtask.query(self.NAMES, run=run, is_windows=True)[self.NAMES[0]]
        self.assertIsNone(status.query_error)
        self.assertEqual(schedtask.classify(status), schedtask.HEALTHY)

    def test_a_stderr_that_would_forge_a_line_is_flattened(self):
        """The message ends up in an ATTENTION list where one item is one
        line. `ops_status.py` flattens at its own sink too; this is the
        producer half, the way `run_company_ops._print_result()` does it."""
        run = _completed(returncode=1, stderr="denied\n  ! 모든 검사 통과")
        status = schedtask.query(self.NAMES, run=run, is_windows=True)[self.NAMES[0]]
        self.assertNotIn("\n", status.query_error)

    def test_a_stdout_of_none_is_survived(self):
        """The state `git_ops` documents: a decode failure inside
        subprocess's reader thread leaves `stdout` as `None`."""
        run = _completed()
        run.calls = []

        def none_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=None, stderr=None)

        status = schedtask.query(self.NAMES, run=none_run, is_windows=True)[self.NAMES[0]]
        self.assertEqual(schedtask.classify(status), schedtask.UNKNOWN)

    def test_a_non_windows_machine_is_not_asked(self):
        """No subprocess at all — not "it failed", but "this question does
        not apply here". Scheduled execution is configured on Windows only."""
        run = _completed()
        status = schedtask.query(self.NAMES, run=run, is_windows=False)[self.NAMES[0]]
        self.assertEqual(run.calls, [])
        self.assertIn("Windows", status.query_error)

    def test_nothing_in_the_module_writes_or_registers(self):
        """The whole module is read-only by design. A future edit that
        called `Register-ScheduledTask`, `Unregister-`, `Enable-`, `Disable-`
        or `Start-ScheduledTask` would change the machine from inside a
        diagnostic — and this file's own tests would keep passing."""
        source = (SRC / "schedtask.py").read_text(encoding="utf-8")
        script = schedtask.build_query(["A"])
        for verb in ("Register-", "Unregister-", "Enable-", "Disable-",
                     "Start-Scheduled", "Stop-Scheduled", "Set-Scheduled"):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, script)
        # Belt and braces on the module itself: the cmdlet names appear in
        # prose, so only the executable half is checked.
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                target = node.func
                called = (
                    target.attr if isinstance(target, ast.Attribute)
                    else getattr(target, "id", None)
                )
                with self.subTest(call=called):
                    self.assertNotIn(
                        called,
                        # `replace` and `rename` are deliberately absent:
                        # `Path.replace` is a write, `str.replace` is not,
                        # and an AST that cannot tell them apart reports the
                        # stderr flattening in `query()` as a disk write.
                        # Measured — it did, on the first run. A gate that
                        # fires on correct code is a gate that gets deleted.
                        {"open", "write_text", "write_bytes", "mkdir",
                         "unlink", "rmtree", "touch", "symlink_to"},
                    )


@unittest.skipUnless(ON_WINDOWS, "Task Scheduler exists on Windows only")
class TheRealTaskSchedulerAnswersTests(unittest.TestCase):
    """Against the actual Task Scheduler, with no fixture and no fake `run`.

    BACKLOG E-6 is the reason this class exists: the Agent installer passed
    22 static tests while being unable to register a task on any machine.
    "정적 검증했다" and "동작한다" are different claims, and every one of the
    tests above makes only the first.

    Nothing here asserts a *particular* task exists — that would be a claim
    about this machine's configuration, and it is exactly the claim the
    module is supposed to be able to answer either way. What is asserted is
    that the query completes, that it answers for every name asked, and that
    an unregistered name is `NOT_REGISTERED` and not an error.
    """

    def test_a_name_nothing_registered_comes_back_not_registered(self):
        name = "DOJOONPASS_COMPANY_OPS_PROBE_THAT_IS_NOT_REGISTERED"
        status = schedtask.query([name])[name]
        self.assertIsNone(
            status.query_error,
            f"the real query failed rather than answering: {status.query_error}",
        )
        self.assertEqual(schedtask.classify(status), schedtask.NOT_REGISTERED)

    def test_the_real_query_answers_for_every_name_in_one_batch(self):
        names = [
            "DOJOONPASS_COMPANY_OPS_PROBE_A",
            "DOJOONPASS_COMPANY_OPS_PROBE_B",
            schedtask.RUNNER_TASK_NAME,
        ]
        result = schedtask.query(names)
        self.assertEqual(sorted(result), sorted(names))
        for name, status in result.items():
            with self.subTest(name=name):
                self.assertIsNone(status.query_error)

    def test_the_real_query_returns_within_the_reports_patience(self):
        """It is called from a tool a person is waiting on. Measured cost on
        this machine: 0.87 s for a two-name batch."""
        import time

        started = time.monotonic()
        schedtask.query([schedtask.RUNNER_TASK_NAME])
        self.assertLess(time.monotonic() - started, schedtask.QUERY_TIMEOUT_SECONDS)

    def test_the_real_query_parses_a_task_that_does_exist(self):
        """The `present=True` half, which no unregistered name can reach.

        Windows ships tasks of its own, so one is borrowed read-only rather
        than registered. Skipped rather than failed when none has a name the
        query's own pattern admits: on this machine every root-folder task
        has a space or a brace, which `_TASK_NAME_RE` refuses on purpose.
        """
        listing = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-ScheduledTask | ForEach-Object { $_.TaskPath + '|' + $_.TaskName }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=schedtask.QUERY_TIMEOUT_SECONDS,
        )
        borrowed = None
        for line in (listing.stdout or "").splitlines():
            path, _, name = line.strip().partition("|")
            if path == "\\" and re.match(r"\A[A-Za-z0-9_.-]{1,200}\Z", name):
                borrowed = name
                break
        if borrowed is None:
            self.skipTest("no root-folder task on this machine has a queryable name")

        status = schedtask.query([borrowed])[borrowed]
        self.assertIsNone(status.query_error)
        self.assertTrue(status.present, f"{borrowed} is registered but read as absent")
        self.assertIsNotNone(status.state)
        self.assertIn(
            schedtask.classify(status),
            {schedtask.HEALTHY, schedtask.NEVER_RUN, schedtask.RUNNING,
             schedtask.DISABLED, schedtask.LAST_RUN_FAILED,
             schedtask.LAST_RUN_TERMINATED},
        )


class ATaskCanRunPerfectlyAndSayNothingTests(unittest.TestCase):
    """`discards_console_output()` / `redirect_target()`.

    A scheduled task can be registered, enabled and firing on time while
    throwing away everything its process prints -- and until C138 that was
    true of every task this project registers, because the action both
    installers built was `python.exe <entrypoint>` with nothing after it.

    It matters for exactly the failures nothing else here can see. Every
    other line `ops_status.py` prints is derived from a file the pipeline
    wrote; a run that dies before writing one -- python off PATH, a moved
    working directory, an unset `COMPANY_OPS_*` -- leaves a
    `LastTaskResult` and nothing else. The discarded stream was the whole
    diagnosis.

    Windows keeps the action it was given, so updating the repository does
    not update an already-registered task. That is why this is checked at
    all rather than assumed from the installer's current source.
    """

    #: Measured on this machine, from a real registered task read through
    #: `build_query()`: a vendor task with no redirection at all.
    REAL_ACTION_WITHOUT_REDIRECTION = (
        "C:\\Users\\user\\AppData\\Local\\Microsoft\\OneDrive\\26.153.0809.0004"
        "\\OneDriveLauncher.exe /startInstances"
    )

    #: The shape both installers register since C138.
    ACTION_WITH_REDIRECTION = (
        'C:\\Windows\\System32\\cmd.exe /c ""C:\\Python\\python.exe" '
        '"C:\\repo\\run_company_ops.py" >> '
        '"C:\\repo\\runtime\\logs\\scheduled_runner.log" 2>&1"'
    )

    def _present(self, action):
        return schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Ready", last_result=0,
            last_run="2026-08-31T11:00:00+09:00", action_command=action,
        )

    def test_an_action_with_no_redirection_discards(self):
        self.assertIs(
            schedtask.discards_console_output(
                self._present(self.REAL_ACTION_WITHOUT_REDIRECTION)
            ),
            True,
        )

    def test_the_installers_action_does_not(self):
        self.assertIs(
            schedtask.discards_console_output(
                self._present(self.ACTION_WITH_REDIRECTION)
            ),
            False,
        )

    def test_an_operators_own_redirection_counts(self):
        """Detected by the redirection, not by our log's name. An operator
        is free to point the output elsewhere; what must not pass unremarked
        is output going nowhere. Naming our file here would report a
        deliberate choice as a fault."""
        self.assertIs(
            schedtask.discards_console_output(
                self._present('cmd.exe /c ""py.exe" "x.py" >> "D:\\my.log" 2>&1"')
            ),
            False,
        )

    def test_an_unreadable_action_is_unknown_rather_than_fine(self):
        """Three-valued on purpose. "We could not check" and "it is fine"
        are the two answers this module refuses to confuse anywhere else,
        and defaulting to False here would report an unchecked task as
        checked."""
        self.assertIsNone(
            schedtask.discards_console_output(self._present(None))
        )

    def test_an_absent_task_is_unknown(self):
        self.assertIsNone(
            schedtask.discards_console_output(
                schedtask.ScheduledTaskStatus(name="T", present=False)
            )
        )

    def test_a_failed_query_is_unknown(self):
        self.assertIsNone(
            schedtask.discards_console_output(
                schedtask.ScheduledTaskStatus(
                    name="T", present=False, query_error="powershell 없음"
                )
            )
        )

    # ------------------------------------------------ where it redirects to

    def test_the_target_is_read_out_of_the_action(self):
        """So the path shown to an operator is the path their machine
        actually writes. A guessed path sends someone looking for a file
        that was never written."""
        self.assertEqual(
            schedtask.redirect_target(self.ACTION_WITH_REDIRECTION),
            "C:\\repo\\runtime\\logs\\scheduled_runner.log",
        )

    def test_a_path_with_a_space_survives(self):
        self.assertEqual(
            schedtask.redirect_target('cmd /c ""p" "e" >> "C:\\a b\\x.log" 2>&1"'),
            "C:\\a b\\x.log",
        )

    def test_a_single_arrow_is_recognised_too(self):
        """An operator who wrote `>` rather than `>>` still has a file, and
        this is about naming it, not about approving of it."""
        self.assertEqual(
            schedtask.redirect_target('cmd /c ""p" "e" > "C:\\x.log" 2>&1"'),
            "C:\\x.log",
        )

    def test_an_action_without_redirection_names_no_file(self):
        self.assertIsNone(
            schedtask.redirect_target(self.REAL_ACTION_WITHOUT_REDIRECTION)
        )

    def test_no_action_at_all_names_no_file(self):
        self.assertIsNone(schedtask.redirect_target(None))
        self.assertIsNone(schedtask.redirect_target(""))

    # ----------------------------------------------------------- the query

    def test_the_query_asks_for_the_action(self):
        script = schedtask.build_query(["A"])
        self.assertIn("$t.Actions", script)
        self.assertIn("action_count", script)

    def test_an_action_is_parsed_out_of_a_real_row(self):
        """Byte-for-byte a row this machine produced, for a task registered
        by its vendor."""
        row = (
            '{"name":"T","present":true,"state":"Ready","last_result":0,'
            '"last_run":"2026-08-28T08:02:07.0000000+09:00","next_run":null,'
            '"missed":0,"action":"C:\\\\OneDriveLauncher.exe /startInstances",'
            '"action_count":1}'
        )
        status = schedtask.parse_query_output(row, ["T"])["T"]
        self.assertIn("OneDriveLauncher.exe", status.action_command)
        self.assertIs(schedtask.discards_console_output(status), True)

    def test_a_row_with_no_action_field_leaves_the_answer_unknown(self):
        """An older query, a task whose actions could not be read: the field
        is absent and the answer must not become "fine"."""
        row = (
            '{"name":"T","present":true,"state":"Ready","last_result":0,'
            '"last_run":"2026-08-28T08:02:07+09:00"}'
        )
        status = schedtask.parse_query_output(row, ["T"])["T"]
        self.assertIsNone(status.action_command)
        self.assertIsNone(schedtask.discards_console_output(status))

    # ------------------------------------------- the count that must travel

    @staticmethod
    def _row(action, count):
        return json.dumps(
            {
                "name": "T",
                "present": True,
                "state": "Ready",
                "last_result": 0,
                "last_run": "2026-08-28T08:02:07+09:00",
                "action": action,
                "action_count": count,
            }
        )

    #: A registered action of each shape, as the installers write them.
    _REDIRECTED = 'cmd.exe /c ""C:\\py.exe" "C:\\r\\run_company_ops.py" >> "C:\\r\\l.log" 2>&1"'
    _DISCARDING = 'C:\\py.exe C:\\r\\run_company_ops.py'

    def test_the_action_count_reaches_the_status(self):
        """`_ROW` has always emitted this field and the parser used to drop
        it, so the comment promising the caller "can see that this is a
        partial view" described something that did not happen."""
        status = schedtask.parse_query_output(self._row(self._DISCARDING, 2), ["T"])["T"]
        self.assertEqual(status.action_count, 2)

    def test_a_task_with_a_second_action_gets_no_verdict_on_its_output(self):
        """The defect this closes. The query reads action one; a `False`
        here says "this task keeps its output" about a task whose other
        action may not, and `ops_status` prints that as settled."""
        for action in (self._REDIRECTED, self._DISCARDING):
            with self.subTest(action=action):
                status = schedtask.parse_query_output(self._row(action, 2), ["T"])["T"]
                self.assertIsNone(schedtask.discards_console_output(status))

    def test_the_single_action_answers_are_untouched(self):
        """The counterpart, and what keeps the fix from being a mute button:
        one action is what all three installers register, and both verdicts
        must still be given there."""
        self.assertIs(
            schedtask.discards_console_output(
                schedtask.parse_query_output(self._row(self._DISCARDING, 1), ["T"])["T"]
            ),
            True,
        )
        self.assertIs(
            schedtask.discards_console_output(
                schedtask.parse_query_output(self._row(self._REDIRECTED, 1), ["T"])["T"]
            ),
            False,
        )

    def test_an_unreadable_count_keeps_the_previous_behaviour(self):
        """Direction of the change, asserted rather than described: only a
        count *known* to exceed one withholds the answer. A row from a query
        that never sent the field must not become a new silence."""
        row = json.dumps(
            {
                "name": "T",
                "present": True,
                "state": "Ready",
                "action": self._DISCARDING,
            }
        )
        status = schedtask.parse_query_output(row, ["T"])["T"]
        self.assertIsNone(status.action_count)
        self.assertIs(schedtask.discards_console_output(status), True)


class WhatTheRegisteredActionActuallyRunsTests(unittest.TestCase):
    """`action_entrypoint()`.

    Both installers bake absolute paths into the action -- interpreter,
    entrypoint, working directory, log. Windows keeps them exactly as given,
    so moving, renaming or re-cloning the repository leaves a task pointing
    at a directory that is no longer there.

    What makes that worth detecting rather than waiting for is *how* it
    fails. The redirection target lives inside the same vanished directory,
    so `>>` cannot open it, cmd exits 1, and the log that would have said
    "can't open file" was never written. From the report's side that is
    `LAST_RUN_FAILED, exit 1` with an empty log -- a failure with its reason
    removed. The registered path is the reason.
    """

    CURRENT = (
        'C:\\Windows\\System32\\cmd.exe /c ""C:\\Python\\python.exe" '
        '"C:\\repo\\run_company_ops.py" >> '
        '"C:\\repo\\runtime\\logs\\scheduled_runner.log" 2>&1"'
    )

    #: What every task registered before C138 looks like.
    LEGACY = 'C:\\Python\\python.exe "C:\\repo\\run_agent.py"'

    def test_the_entrypoint_is_read_from_the_current_action_shape(self):
        self.assertEqual(
            schedtask.action_entrypoint(self.CURRENT), "C:\\repo\\run_company_ops.py"
        )

    def test_the_entrypoint_is_read_from_the_legacy_action_shape(self):
        """A machine whose installer predates the redirection still has a
        task, and its path can be just as stale."""
        self.assertEqual(
            schedtask.action_entrypoint(self.LEGACY), "C:\\repo\\run_agent.py"
        )

    def test_the_redirection_target_is_not_mistaken_for_the_entrypoint(self):
        """Three quoted paths in the current shape and only one is a `.py`.
        A scan that took the first or the last quoted token would name the
        interpreter or the log."""
        self.assertTrue(schedtask.action_entrypoint(self.CURRENT).endswith(".py"))

    def test_a_path_with_a_space_survives(self):
        self.assertEqual(
            schedtask.action_entrypoint(
                'cmd /c ""py.exe" "C:\\my repo\\run_agent.py" >> "C:\\l.log" 2>&1"'
            ),
            "C:\\my repo\\run_agent.py",
        )

    def test_an_action_that_runs_no_python_file_names_nothing(self):
        for action in (None, "", "C:\\Python\\python.exe", "notepad.exe"):
            with self.subTest(action=action):
                self.assertIsNone(schedtask.action_entrypoint(action))

    def test_an_unquoted_path_is_not_guessed_at(self):
        """`redirect_target()`'s rule, for the same reason: every Windows
        path may hold a space, so an unquoted scan would have to guess where
        the path ended. Both installers quote."""
        self.assertIsNone(
            schedtask.action_entrypoint("python.exe C:\\repo\\run_agent.py")
        )


class NothingHereNeedsWindowsToBeImportedTests(unittest.TestCase):
    """The OS boundary, stated as a property rather than as a convention.

    This module is the only one in the project that reaches outside the
    process for data, and the thing it reaches is Windows-only. That makes
    it the one module where an import-time platform dependency could get in
    -- `winreg`, `ctypes.windll`, a `pywin32` name -- and where it would not
    fail on the machines this project is developed and deployed on. It would
    fail on a CI container, at **collection** time, taking the whole suite
    with it.

    So the boundary is drawn in one place and only one: `query()` decides,
    at call time, whether to run a subprocess. Everything else here -- the
    verdicts, the parser, the log names, the action reading -- is text
    handling that works anywhere.
    """

    #: Modules whose *import* fails off Windows. The distinction matters and
    #: the first draft of this class got it wrong.
    #:
    #: `msvcrt` was in this set and the test failed on the first run.
    #: Measured, rather than assumed from the failure: `msvcrt` is pulled in
    #: by the standard library's own `subprocess`, inside its
    #: `if _mswindows:` branch --
    #:
    #:     import subprocess          -> msvcrt in sys.modules: True
    #:     Lib/subprocess.py:71           import msvcrt
    #:
    #: -- so it is stdlib taking a guarded Windows branch, not a dependency
    #: this module introduced, and `subprocess` imports fine everywhere.
    #: Listing it made the gate fire on correct code, which is how a gate
    #: gets deleted rather than heeded.
    #:
    #: What belongs here is the set that has no such guard behind it:
    #: `winreg` is Windows-only stdlib with no POSIX counterpart, and the
    #: `win32*` names are pywin32, which is both third-party (forbidden
    #: outright by `test_src_imports_only_the_standard_library`) and
    #: Windows-only.
    WOULD_NOT_IMPORT_ELSEWHERE = frozenset(
        {"winreg", "win32api", "win32com", "win32con", "pywintypes", "pythoncom"}
    )

    def test_the_forbidden_set_names_something_real(self):
        """Guards the guard: a set that shrank to nothing would make the
        sweep below pass for any module at all."""
        self.assertIn("winreg", self.WOULD_NOT_IMPORT_ELSEWHERE)
        self.assertNotIn(
            "msvcrt",
            self.WOULD_NOT_IMPORT_ELSEWHERE,
            "see the comment above: msvcrt comes from stdlib subprocess",
        )

    #: **There is deliberately no subprocess import-probe here, and finding
    #: that out is the reason this note exists.**
    #:
    #: The first draft ran `import schedtask` in a fresh interpreter and
    #: compared `sys.modules` before and after. Mutation-tested by injecting
    #: `import winreg` into the module, it did **not** fire. Measured:
    #:
    #:     python    -c "'winreg' in sys.modules"   ->  True
    #:     python -S -c "'winreg' in sys.modules"   ->  True
    #:
    #: Windows loads `winreg` before any user code runs (`encodings` reaches
    #: for it to resolve the console codepage), so it is in the "before"
    #: snapshot and can never appear in the difference. `-S` does not help.
    #: The probe was structurally incapable of seeing the one case it was
    #: written for.
    #:
    #: Its only other property — "pulls in nothing third-party" — is already
    #: held, over this exact file, by
    #: `test_repository_hygiene.py::DependencyGuardTests
    #: ::test_src_imports_only_the_standard_library`, which walks every
    #: shipped file with `ast` and compares against `sys.stdlib_module_names`.
    #:
    #: So it was removed rather than kept as a green test that proves
    #: nothing. The AST test below is what actually holds the property, and
    #: it fires on the injected `winreg` immediately.

    def test_no_windows_only_import_appears_anywhere_in_the_source(self):
        """The AST half, so a *function-local* Windows import — which the
        test above would not see unless that function ran — is caught too."""
        tree = ast.parse((SRC / "schedtask.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        self.assertTrue(imported, "the scan found no imports — it has gone blind")
        # `ctypes` joins the set here and not above: it imports fine on any
        # platform, so the import probe cannot object to it, but the only
        # reason this module would reach for it is `windll` — a Windows API
        # call by another route, and the boundary this class exists to keep
        # in one place.
        self.assertEqual(
            imported & (self.WOULD_NOT_IMPORT_ELSEWHERE | {"ctypes"}), set()
        )

    def test_the_platform_decision_is_made_in_exactly_one_place(self):
        """`os.name`/`sys.platform` read anywhere else would be a second
        boundary, and two boundaries drift. `query()` is the one that
        decides, and it takes `is_windows` so a test can decide for it."""
        source = (SRC / "schedtask.py").read_text(encoding="utf-8")

        self.assertEqual(source.count('os.name == "nt"'), 1)
        self.assertNotIn("sys.platform", source)

    def test_a_non_windows_machine_is_answered_without_a_subprocess(self):
        """Not "it fails gracefully" — it does not ask. Scheduled execution
        is configured on Windows only, so the honest answer is that the
        question does not apply here, and it costs no process to say so."""
        run = _completed()
        result = schedtask.query(
            [schedtask.RUNNER_TASK_NAME, schedtask.PUBLISH_TASK_NAME],
            prefixes=(schedtask.AGENT_TASK_PREFIX,),
            run=run,
            is_windows=False,
        )

        self.assertEqual(run.calls, [])
        self.assertEqual(len(result), 2)
        for name, status in result.items():
            with self.subTest(task=name):
                self.assertEqual(schedtask.classify(status), schedtask.UNKNOWN)
                self.assertIn("Windows", status.query_error)

    def test_every_pure_helper_answers_on_any_platform(self):
        """The text-handling half, which is most of this module. None of it
        may depend on the platform, because `ops_status.py` renders these
        strings wherever it runs."""
        status = schedtask.ScheduledTaskStatus(
            name="T", present=True, state="Ready", last_result=0,
            last_run="2026-08-31T11:00:00+09:00",
            action_command='cmd /c ""py" "C:\\r\\run.py" >> "C:\\r\\l.log" 2>&1"',
        )

        self.assertEqual(schedtask.classify(status), schedtask.HEALTHY)
        self.assertEqual(schedtask.redirect_target(status.action_command), "C:\\r\\l.log")
        self.assertEqual(
            schedtask.action_entrypoint(status.action_command), "C:\\r\\run.py"
        )
        self.assertIs(schedtask.discards_console_output(status), False)
        self.assertIn("정상", schedtask.describe_result(0))
        self.assertTrue(status.has_ever_run)


if __name__ == "__main__":
    unittest.main()
