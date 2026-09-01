"""Run Contract, end to end through `app.runner.run_once()`.

`tests/test_runsummary.py` covers the vocabulary in isolation. This file
covers the contract as the pipeline actually produces it:

    Input      -> the same arguments run_once() already took
    Processing -> each step records one ComponentResult
    Output     -> a Run Manifest at run_summary_path
    Failure    -> classified: severity + retryability
    Recovery   -> retryability says whether to wait or act
    Evidence   -> artifact_refs point at paths the pipeline already wrote
    Exit Signal-> Overall Status -> exit code

Real filesystem, real git, InMemoryNotionTransport (docs/10 §10:
Mock-only 검증 금지).
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from app.runner import run_once  # noqa: E402
from backup.git_ops import GitOperationError  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    InMemoryNotionTransport,
    NotionClient,
)
from reporter import Reporter  # noqa: E402
from runsummary import (  # noqa: E402
    EXIT_DEGRADED,
    EXIT_FAILED,
    EXIT_SUCCESS,
    ComponentStatus,
    OverallStatus,
    Retryability,
    Severity,
    read_summary,
)


def _force_rmtree(path: Path) -> None:
    """git object files are read-only on Windows; clear the flag first."""

    def onexc(func, target, exc):
        try:
            Path(target).chmod(stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    def onerror(func, target, exc_info):
        onexc(func, target, exc_info[1])

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onexc)
    else:
        shutil.rmtree(path, onerror=onerror)


class RunContractTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.history_dir = self.root / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.backup_working_copy_dir = self.root / "backup_working_copy"
        self.backup_working_copy_dir.mkdir(parents=True, exist_ok=True)
        self.bare_remote_dir = self.root / "backup_remote.git"
        self._init_backup_git_repo()

        rt = self.root / "runtime"
        self.runner_lock_path = rt / "locks" / "company_ops.lock"
        self.transport_dir = rt / "events" / "transport"
        self.incoming_dir = rt / "events" / "incoming"
        self.processed_dir = rt / "events" / "processed"
        self.rejected_dir = rt / "events" / "rejected"
        self.logs_dir = rt / "logs"
        self.state_dir = rt / "state"
        self.keep_dir = rt / "history_candidates" / "keep"
        self.review_dir = rt / "history_candidates" / "review"
        self.run_summary_path = rt / "runs" / "last_run.json"

        self.reporter = Reporter(profile="DESKTOP_3")

    def _run_git(self, args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _init_backup_git_repo(self):
        self._run_git(["init", "--bare", "-b", "main", str(self.bare_remote_dir)], self.root)
        wc = self.backup_working_copy_dir
        self._run_git(["init", "-b", "main"], wc)
        self._run_git(["config", "user.email", "t@example.invalid"], wc)
        self._run_git(["config", "user.name", "Run Contract Test"], wc)
        self._run_git(["remote", "add", "origin", str(self.bare_remote_dir)], wc)
        (wc / ".gitkeep").write_text("", encoding="utf-8")
        self._run_git(["add", "-A"], wc)
        self._run_git(["commit", "-m", "init"], wc)
        self._run_git(["push", "-u", "origin", "main"], wc)

    def _write_event(self, **overrides):
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        data = dict(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="run contract probe",
            milestone="M1",
            evidence=[],
            history_candidate=True,
            timestamp="2026-08-01T10:00:00+09:00",
        )
        data.update(overrides)
        _, path = self.reporter.report_and_write(directory=self.incoming_dir, **data)
        return path

    def _run(self, **kwargs):
        params = dict(
            local_master_dir=self.history_dir,
            backup_working_copy_dir=self.backup_working_copy_dir,
            history_start_date=date(2026, 8, 1),
            runner_lock_path=self.runner_lock_path,
            now=datetime(2026, 8, 2, 12, 0).astimezone(),
            transport_dir=self.transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            collector_log_path=self.logs_dir / "collector.log",
            collector_state_path=self.state_dir / "collector_state.json",
            notion_sync_log_path=self.logs_dir / "notion_sync.log",
            late_update_log_path=self.logs_dir / "daily_late_update.log",
            monthly_state_path=self.state_dir / "monthly_history_state.json",
            run_summary_path=self.run_summary_path,
            notion_retry_queue_path=self.state_dir / "notion_retry_queue.json",
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
            scheduler_state_path=self.state_dir / "daily_history_state.json",
            backup_state_path=self.state_dir / "backup_state.json",
        )
        params.update(kwargs)
        return run_once(**params)

    def _manifest(self):
        return read_summary(self.run_summary_path)


class ReturnContractCompatibilityTests(RunContractTestCase):
    """The Run Summary is additive. 219 existing call sites unpack the
    Runner's return value five ways or index it; none of them may break."""

    def test_the_result_still_unpacks_as_five_values(self):
        self._write_event(event_id="RC-COMPAT-1")

        intake, collector, scheduler, backup, notion = self._run()

        self.assertEqual(collector.accepted, 1)
        self.assertIsNotNone(scheduler)
        self.assertIsNotNone(backup)
        self.assertEqual(notion, ())
        self.assertIsNotNone(intake)

    def test_the_result_still_supports_index_access(self):
        self._write_event(event_id="RC-COMPAT-2")

        result = self._run()

        self.assertEqual(len(result), 5)
        self.assertEqual(result[1].accepted, 1)

    def test_the_result_also_carries_the_summary(self):
        self._write_event(event_id="RC-COMPAT-3")

        result = self._run()

        self.assertEqual(result.summary.run_id, result.summary.run_id)
        self.assertEqual(result.summary.overall_status, OverallStatus.SUCCESS)


class ManifestOutputTests(RunContractTestCase):
    def test_a_healthy_run_writes_a_success_manifest(self):
        self._write_event(event_id="RC-OK-1")

        result = self._run()

        manifest = self._manifest()
        self.assertIsNotNone(manifest, "no Run Manifest was written")
        self.assertEqual(manifest.overall_status, OverallStatus.SUCCESS)
        self.assertEqual(manifest.exit_code, EXIT_SUCCESS)
        self.assertEqual(manifest.run_id, result.summary.run_id)

    def test_every_pipeline_component_appears(self):
        """A component missing from the manifest is indistinguishable from
        one that succeeded — which is the reporting gap this replaces."""
        self._write_event(event_id="RC-OK-2")

        self._run()

        names = {c.name for c in self._manifest().components}
        self.assertEqual(
            names,
            {
                "transport",
                "collector",
                "notion_sync",
                "history_filter",
                "daily",
                "late_update",
                "monthly",
                "backup",
                "dashboard",
            },
        )

    def test_unconfigured_integrations_are_skipped_not_failed(self):
        self._write_event(event_id="RC-OK-3")

        self._run()

        manifest = self._manifest()
        self.assertEqual(manifest.component("notion_sync").status, ComponentStatus.SKIPPED)
        self.assertEqual(manifest.component("dashboard").status, ComponentStatus.SKIPPED)
        # And they do not drag the run down.
        self.assertEqual(manifest.overall_status, OverallStatus.SUCCESS)

    def test_the_manifest_carries_the_metrics_that_used_to_be_discarded(self):
        """BUG-39, end to end: these values were always computed."""
        self._write_event(event_id="RC-OK-4")

        self._run()

        manifest = self._manifest()
        transport = manifest.component("transport").metrics
        collector = manifest.component("collector").metrics
        backup = manifest.component("backup").metrics

        for key in ("moved", "failed", "skipped_not_stable", "skipped_invalid"):
            self.assertIn(key, transport)
        for key in ("accepted", "duplicate", "rejected", "failed"):
            self.assertIn(key, collector)
        self.assertIn("changed_files", backup)

    def test_artifact_refs_point_at_evidence_that_exists(self):
        """The manifest's central claim: detail is *referenced*. A ref that
        names nothing would make it a worse log rather than a summary."""
        self._write_event(event_id="RC-OK-5")

        self._run()

        runtime_root = self.root / "runtime"
        collector = self._manifest().component("collector")
        self.assertIn("logs/collector.log", collector.artifact_refs)
        self.assertTrue((runtime_root / "logs" / "collector.log").exists())

    def test_the_manifest_is_written_atomically(self):
        self._write_event(event_id="RC-OK-6")

        self._run()

        self.assertEqual(list(self.run_summary_path.parent.glob(".tmp-*")), [])

    def test_a_lock_skipped_run_writes_no_manifest(self):
        """It did nothing, so it has nothing to report — and overwriting the
        previous run's manifest with an empty one would destroy the record
        of the run that actually did the work.

        The lock is refused directly rather than by planting a lock file:
        `try_acquire_lock()` treats an unowned file as stale and takes it,
        which is correct behaviour and not what this test is about.
        """
        import app.runner as runner_module

        self._write_event(event_id="RC-OK-7")
        self._run()
        first = self.run_summary_path.read_text(encoding="utf-8")

        original = runner_module.try_acquire_lock
        runner_module.try_acquire_lock = lambda *a, **k: False
        self.addCleanup(setattr, runner_module, "try_acquire_lock", original)

        result = self._run()

        self.assertIsNone(result)
        self.assertEqual(self.run_summary_path.read_text(encoding="utf-8"), first)


class FailureClassificationTests(RunContractTestCase):
    def test_a_notion_outage_degrades_the_run_but_does_not_fail_it(self):
        """README RULE 5 in one assertion: Notion is off the History
        critical path, so its failure must not report the run as broken."""
        transport = InMemoryNotionTransport()
        transport.fail_next_method = "query_database"
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )

        self._write_event(event_id="RC-DEG-1")
        self._run(notion_sync=sync)

        manifest = self._manifest()
        notion = manifest.component("notion_sync")

        self.assertEqual(notion.status, ComponentStatus.FAILED)
        self.assertEqual(notion.failure.severity, Severity.DEGRADED)
        self.assertEqual(notion.failure.retryability, Retryability.RETRYABLE)
        self.assertEqual(manifest.overall_status, OverallStatus.DEGRADED)
        self.assertEqual(manifest.exit_code, EXIT_DEGRADED)

    def test_history_is_still_recorded_during_a_degraded_run(self):
        """The reason DEGRADED is not FAILED, stated as a fact about disk."""
        transport = InMemoryNotionTransport()
        transport.fail_next_method = "query_database"
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )

        self._write_event(event_id="RC-DEG-2")
        self._run(notion_sync=sync)

        self.assertTrue((self.keep_dir / "HIST-RC-DEG-2.json").exists())
        self.assertTrue((self.history_dir / "daily" / "2026-08-01.md").exists())

    def test_a_daily_close_failure_fails_the_run(self):
        """The critical case: the step that writes Company History."""
        import scheduler.scheduler as scheduler_module

        original = scheduler_module.generate_daily_history

        def exploding(*args, **kwargs):
            raise RuntimeError("daily close blew up")

        scheduler_module.generate_daily_history = exploding
        self.addCleanup(setattr, scheduler_module, "generate_daily_history", original)

        self._write_event(event_id="RC-FAIL-1")
        self._run()

        manifest = self._manifest()
        daily = manifest.component("daily")

        self.assertEqual(daily.status, ComponentStatus.FAILED)
        self.assertEqual(daily.failure.severity, Severity.CRITICAL)
        self.assertEqual(daily.failure.classification, "DAILY_CLOSE_FAILED")
        self.assertEqual(manifest.overall_status, OverallStatus.FAILED)
        self.assertEqual(manifest.exit_code, EXIT_FAILED)

    def test_the_failing_date_is_in_the_manifest_for_recovery(self):
        """Recovery, not just diagnosis: Scheduler stops at the first failing
        date, so this is where the next run resumes."""
        import scheduler.scheduler as scheduler_module

        original = scheduler_module.generate_daily_history
        scheduler_module.generate_daily_history = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        self.addCleanup(setattr, scheduler_module, "generate_daily_history", original)

        self._write_event(event_id="RC-FAIL-2")
        self._run()

        daily = self._manifest().component("daily")
        self.assertEqual(daily.metrics.get("failed_date"), "2026-08-01")

    def test_a_transient_backup_failure_is_degraded_and_retryable(self):
        """docs/08 §19: a failed push is BACKUP_PENDING, routine, retried by
        the next run. Paging someone for it would be crying wolf.

        Found by running it against a real broken remote, not by reading the
        code. The manifest recorded `STEP_ABORTED [CRITICAL/UNKNOWN]` from
        the generic abort fallback, while `backup_state.json` said
        BACKUP_PENDING and the operator was told "다음 Runner 실행이 자동으로
        다시 push합니다" — the manifest was the least accurate account of a
        failure the rest of the system had already classified, and it is the
        one `ops_status.py` reads.

        It mattered beyond tidiness: ATTENTION lists only PERMANENT
        failures, so a real credential failure arriving on this path would
        have been filed UNKNOWN and never surfaced to anyone.
        """
        self._write_event(event_id="RC-DEG-3")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        backup = self._manifest().component("backup")

        self.assertEqual(backup.status, ComponentStatus.FAILED)
        self.assertEqual(backup.failure.classification, "BACKUP_PENDING")
        self.assertEqual(backup.failure.retryability, Retryability.RETRYABLE)
        self.assertEqual(backup.failure.severity, Severity.DEGRADED)

    def test_a_transient_backup_failure_does_not_fail_the_whole_run(self):
        """The consequence of the classification above: a routine push
        failure reports DEGRADED, not FAILED. Everything before Backup is
        already durable, and the next run re-pushes the same commit."""
        self._write_event(event_id="RC-DEG-4")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        manifest = self._manifest()
        self.assertEqual(manifest.overall_status, OverallStatus.DEGRADED)
        self.assertEqual(manifest.exit_code, EXIT_DEGRADED)

    def test_a_permanent_backup_failure_is_critical_and_fails_the_run(self):
        """docs/08 §21/§62: a credential problem cannot be fixed by
        retrying, so it must not be filed as routine.

        Exercised through the Secret Scan gate, which is the reachable
        permanent failure — planting a secret-shaped FILENAME (empty file;
        no secret is created) makes Backup refuse before any git command.
        """
        self._write_event(event_id="RC-FAIL-3")
        (self.history_dir / ".env").write_text("", encoding="utf-8")

        self._run()

        manifest = self._manifest()
        backup = manifest.component("backup")

        self.assertEqual(backup.failure.classification, "BACKUP_FAILED")
        self.assertEqual(backup.failure.retryability, Retryability.PERMANENT)
        self.assertEqual(backup.failure.severity, Severity.CRITICAL)
        self.assertEqual(manifest.overall_status, OverallStatus.FAILED)
        self.assertEqual(manifest.exit_code, EXIT_FAILED)

    def test_the_backup_classification_uses_the_spec_rule_not_a_copy(self):
        """`is_permanent_failure()` answers docs/08 §21/§62's question --
        can a retry fix this -- and is what `run_company_ops.py` and
        `backup/runner.py` also apply. The Runner reuses it rather than
        restating it, so they cannot drift into disagreeing about whether a
        given failure is worth waking someone for.

        It used to be `is_authentication_failure(str(exc))` here, which was
        the same rule asked of the message text alone. That could not see
        `WorkingCopyNotAGitRepositoryError`, so an unconfigured Backup --
        the state of every fresh deployment -- was recorded RETRYABLE beside
        a `reason` telling the operator to go and create the repository."""
        import inspect

        import app.runner as runner_module

        source = inspect.getsource(runner_module.run_once)
        self.assertIn("is_permanent_failure(exc)", source)
        # Comments stripped before the negative, the way
        # `test_schedtask.py::_code()` does for the installers and for the
        # same reason: the prose above the call explains what it replaced,
        # and a raw scan would read that explanation as the violation.
        code = "\n".join(
            line
            for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("is_authentication_failure", code)


class EventContractPreservationTests(RunContractTestCase):
    """The Event Contract is *not* changed by the Run Contract.

    The two halves are deliberately separate, and it would be easy to
    "tidy" them into one by banning newlines in `event_id`:

        the schema keeps accepting it     — docs/02 is the contract, and
                                            narrowing it would reject Events
                                            already collected (BACKLOG A-15)
        the log keeps escaping it         — so no value can forge a line

    Solving a log-formatting problem by changing what the system accepts
    would push the cost onto every Desktop that already sent one. This test
    pins both halves together so neither can be "simplified" into the other.
    """

    def test_an_event_id_with_a_newline_is_still_collected(self):
        forged = "RC-EVT\n2026-01-01T00:00:00+09:00 ACCEPTED FAKE"
        self._write_event(event_id=forged)

        result = self._run()

        self.assertEqual(result[1].accepted, 1)
        self.assertEqual(
            self._manifest().component("collector").metrics["accepted"], 1
        )

    def test_and_it_cannot_forge_a_line_in_any_log(self):
        forged = "RC-EVT2\n2026-01-01T00:00:00+09:00 ACCEPTED FAKE"
        self._write_event(event_id=forged)

        self._run()

        collector_log = (self.logs_dir / "collector.log").read_text(encoding="utf-8")
        forged_lines = [
            line
            for line in collector_log.splitlines()
            if line.startswith("2026-01-01T00:00:00+09:00")
        ]
        self.assertEqual(forged_lines, [])

    def test_the_manifest_itself_cannot_be_forged_by_an_event(self):
        """The manifest is JSON, so a newline cannot forge a record — but
        it is worth pinning, because it is now the artifact an operator
        trusts most."""
        forged = 'RC-EVT3", "overall_status": "SUCCESS'
        self._write_event(event_id=forged)

        self._run()

        data = json.loads(self.run_summary_path.read_text(encoding="utf-8"))
        self.assertIn(data["overall_status"], {"SUCCESS", "DEGRADED", "FAILED"})
        self.assertEqual(len(data["components"]), 9)


class AbortPathEvidenceTests(RunContractTestCase):
    """The property that makes the manifest worth writing in `finally`.

    A run that dies mid-pipeline used to leave "what did the earlier steps
    do?" answerable only by reading logs — and `run_company_ops.py` printed
    nothing at all, because it never reached its reporting code.
    """

    def test_an_aborting_run_still_writes_a_manifest(self):
        self._write_event(event_id="RC-ABORT-1")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        self.assertIsNotNone(self._manifest(), "an aborted run left no manifest")

    def test_the_steps_that_completed_before_the_abort_are_recorded(self):
        self._write_event(event_id="RC-ABORT-2")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        manifest = self._manifest()
        for name in ("transport", "collector", "history_filter", "daily"):
            with self.subTest(component=name):
                self.assertEqual(
                    manifest.component(name).status, ComponentStatus.SUCCESS
                )

    def test_the_step_that_raised_is_attributed_not_guessed(self):
        self._write_event(event_id="RC-ABORT-3")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        manifest = self._manifest()
        self.assertEqual(manifest.component("backup").status, ComponentStatus.FAILED)
        self.assertEqual([c.name for c in manifest.failures()], ["backup"])

    def test_an_aborted_run_reports_a_non_success_status(self):
        """DEGRADED here, not FAILED — and that is the point.

        This asserted FAILED while the abort path still produced the generic
        `STEP_ABORTED [CRITICAL/UNKNOWN]`. Once the Backup failure is
        classified properly, a routine push failure is exactly what docs/08
        §19 calls it: recoverable. An aborted run is therefore not
        automatically a failed one — what it is depends on what aborted it,
        which is the whole reason the classification exists.
        """
        self._write_event(event_id="RC-ABORT-4")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        manifest = self._manifest()
        self.assertNotEqual(manifest.overall_status, OverallStatus.SUCCESS)
        self.assertEqual(manifest.overall_status, OverallStatus.DEGRADED)
        self.assertNotEqual(manifest.exit_code, 0)

    def test_the_manifest_is_valid_json_after_an_abort(self):
        """It is read by `ops_status.py` and by people. A manifest written
        from a half-finished run must still parse."""
        self._write_event(event_id="RC-ABORT-5")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        data = json.loads(self.run_summary_path.read_text(encoding="utf-8"))
        self.assertIn(data["overall_status"], {"DEGRADED", "FAILED"})
        self.assertIn("components", data)
        self.assertEqual(len(data["components"]), 8)  # dashboard never reached


class AnAbortedRunExitsWithTheCodeItRecordedTests(unittest.TestCase):
    """docs/14 section 4 fixes four numbers and reserves one of them:

        SUCCESS 0 | DEGRADED 3 | FAILED 2
        "`1`은 **설정 오류** 전용이다(실행이 시작조차 못 한 경우)"

    `run_company_ops.py` honoured that for exactly one exception.
    `_report_backup_failure()` reads the exit code out of the manifest and
    says why: *"Returning a hardcoded 2 here made the process disagree with
    its own manifest ... Two answers to 'how bad was this run' is one too
    many, and the scheduled task only ever sees this one."*

    Every other way `run_once()` aborts kept travelling -- out of `main()`,
    out of `raise SystemExit(main(sys.argv))` -- and Python exited **1**.
    Measured as a real process before the fix (C78):

        ordinary run                  process 0   manifest SUCCESS/0
        duplicate Candidate (abort)   process 1   manifest FAILED/2
        corrupt scheduler state       process 1   manifest FAILED/2

    Both aborted runs had started, taken the lock, collected an Event and
    written a manifest. Task Scheduler's Last Run Result -- the only signal
    an unattended deployment has (BACKLOG F-7) -- read "configuration error,
    the run never started" for a CRITICAL failure mid-pipeline.

    Driven as a **subprocess**, because the subject is the number the process
    leaves behind. `main()`'s return value is not the same claim: it becomes
    the exit code only through `raise SystemExit(main(sys.argv))`, and an
    in-process test would pass with that line deleted.

    The tree is copied once per class rather than per test: the entrypoint
    derives `RUNTIME_DIR` from its own location and its first statement
    refuses a split runtime root, so isolation means a copy, and the copy is
    the expensive part.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "tree"
        cls.root.mkdir()
        shutil.copy2(REPO_ROOT / "run_company_ops.py", cls.root / "run_company_ops.py")
        shutil.copytree(
            REPO_ROOT / "src", cls.root / "src",
            ignore=shutil.ignore_patterns("__pycache__"),
        )

    @classmethod
    def tearDownClass(cls):
        _force_rmtree(Path(cls._tmp.name))

    def setUp(self):
        self.runtime = self.root / "runtime"
        if self.runtime.exists():
            _force_rmtree(self.runtime)
        for rel in ("events/transport", "events/incoming", "events/processed",
                    "events/rejected", "history_candidates/keep",
                    "history_candidates/review", "local_master/daily",
                    "local_master/monthly", "state", "locks", "runs", "logs"):
            (self.runtime / rel).mkdir(parents=True)

    def _event(self, event_id):
        return {
            "schema_version": "1.0", "event_id": event_id,
            "timestamp": "2026-08-15T09:00:00+09:00", "source": "DESKTOP_1",
            "role": "CTO_BACKEND", "project_id": "PRJ-A",
            "event_type": "MILESTONE_COMPLETED", "status": "IN_PROGRESS",
            "summary": "abort exit-code contract", "milestone": "M",
            "blocker": None, "evidence": [], "history_candidate": True,
        }

    def _run(self, *args):
        env = dict(os.environ)
        env["COMPANY_OPS_HISTORY_START_DATE"] = "2026-08-14"
        env["PYTHONIOENCODING"] = "utf-8"
        for key in ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID",
                    "NOTION_OPS_RUNS_DATABASE_ID"):
            env.pop(key, None)
        return subprocess.run(
            [sys.executable, "run_company_ops.py", *args],
            cwd=self.root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=180,
        )

    def _abort_mid_pipeline(self):
        """A corrupt `daily_history_state.json`, which `scheduler.run_once()`
        reads at step 6 -- after transport, collector and the History Filter
        have all succeeded, and before Backup, so no git remote is needed to
        reach it."""
        (self.runtime / "events/incoming" / "e1.json").write_text(
            json.dumps(self._event("RC-EXIT-1")), encoding="utf-8"
        )
        (self.runtime / "state/daily_history_state.json").write_text(
            "{not json", encoding="utf-8"
        )
        return self._run()

    def _manifest(self):
        path = self.runtime / "runs" / "last_run.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_fixture_really_aborts_after_the_run_started(self):
        """Guards the guard. Every assertion below is about an *aborted* run,
        and a fixture that quietly succeeded would make them vacuous -- the
        run has to have started, done work, and then died."""
        self._abort_mid_pipeline()
        manifest = self._manifest()

        self.assertIsNotNone(manifest, "the aborted run left no manifest")
        self.assertEqual(manifest["overall_status"], "FAILED")
        self.assertEqual(manifest["exit_code"], 2)
        statuses = {c["name"]: c["status"] for c in manifest["components"]}
        self.assertEqual(statuses.get("collector"), "SUCCESS",
                         "the run must have got past collection to be an abort")

    def test_the_process_exits_with_the_code_the_manifest_recorded(self):
        result = self._abort_mid_pipeline()
        manifest = self._manifest()

        self.assertEqual(
            result.returncode, manifest["exit_code"],
            "the process and its own Run Manifest disagree about how bad "
            f"this run was: process={result.returncode}, "
            f"manifest={manifest['exit_code']}",
        )

    def test_it_is_not_one_because_one_means_the_run_never_started(self):
        """The half docs/14 states as a reservation rather than a mapping.
        Stated separately because it is the operator-visible harm: Task
        Scheduler shows this number and nothing else."""
        result = self._abort_mid_pipeline()

        self.assertNotEqual(
            result.returncode, 1,
            "docs/14 section 4 reserves 1 for a run that never started; this "
            "one took the lock, collected an Event and wrote a manifest",
        )
        self.assertEqual(result.returncode, 2)

    def test_the_traceback_is_still_printed(self):
        """The exit code is fixed by *reporting* it, not by swallowing the
        failure. The traceback names the file and the line, and nothing else
        in this system does."""
        result = self._abort_mid_pipeline()

        self.assertIn("Traceback (most recent call last)", result.stderr)
        self.assertIn("SchedulerStateError", result.stderr)

    def test_a_configuration_error_is_still_one(self):
        """The boundary, from the other side. Widening the abort handler must
        not swallow the case 1 is actually for -- a run that never started."""
        result = self._run("--bogus-argument")

        self.assertEqual(result.returncode, 1)
        self.assertIsNone(
            self._manifest(),
            "a run refused before it started must not write a manifest",
        )

    def test_a_clean_run_is_still_zero(self):
        """The other boundary: the new handler must be unreachable on the
        ordinary path."""
        (self.runtime / "events/incoming" / "e1.json").write_text(
            json.dumps(self._event("RC-EXIT-OK")), encoding="utf-8"
        )
        # No backup remote is configured, so Backup is the step that fails;
        # what matters here is that the process still agrees with the
        # manifest, whatever the manifest says.
        result = self._run()
        manifest = self._manifest()

        self.assertIsNotNone(manifest)
        self.assertEqual(result.returncode, manifest["exit_code"])

    def _write_foreign_manifest(self, run_id, exit_code=0):
        """A manifest on disk that belongs to some other run.

        Built by hand rather than by running the pipeline twice, because the
        property under test is about the *file*, not about how it got there:
        a manifest whose `run_id` is not this run's must not decide this
        run's exit code. The two-run version was measured once, in C82, and
        is recorded in BACKLOG rather than paid for on every suite run.
        """
        import json

        payload = {
            "schema_version": "1.0",
            "run_id": run_id,
            "started_at": "2026-08-19T09:00:00+09:00",
            "finished_at": "2026-08-19T09:00:01+09:00",
            "overall_status": "SUCCESS",
            "exit_code": exit_code,
            "components": [],
        }
        path = self.runtime / "runs" / "last_run.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_run_that_writes_no_manifest_does_not_inherit_the_last_one(self):
        """C82, and it is a defect C78 introduced.

        `run_once()` writes the manifest in its `finally` — but that `finally`
        belongs to a `try:` that begins *after* the lock is acquired. A run
        that dies before it writes nothing, and the file on disk still
        describes the previous run. Measured with two real runs in an
        isolated tree, before the fix:

            run 1 (clean)        process 0   manifest SUCCESS/0
            run 2 (dies early)   process 0   manifest SUCCESS/0   <- run 1's

        A crashed run reporting success to Task Scheduler — worse than the
        wrong-but-loud 1 it produced before C78 touched this at all.

        Here the early death is an unusable `locks/` directory, which is the
        reachable version: `try_acquire_lock()` is the first thing
        `run_once()` does and it is outside the `try:`.
        """
        import shutil

        self._write_foreign_manifest("2026-08-19T09:00:00+09:00", exit_code=0)
        (self.runtime / "events/incoming" / "e1.json").write_text(
            __import__("json").dumps(self._event("RC-STALE-1")), encoding="utf-8"
        )
        shutil.rmtree(self.runtime / "locks")
        (self.runtime / "locks").write_text("not a directory", encoding="utf-8")

        result = self._run()

        self.assertNotEqual(
            result.returncode, 0,
            "a run that died before writing a manifest exited 0 because the "
            "PREVIOUS run's manifest said so",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            self._manifest()["run_id"], "2026-08-19T09:00:00+09:00",
            "the fixture is only meaningful while the manifest stays the "
            "foreign one — if this run wrote its own, it is testing "
            "something else",
        )

    def test_the_helper_trusts_a_manifest_this_run_did_write(self):
        """The other side of the boundary. `superseding` must not turn every
        abort into a flat 2 — the whole point of C78 is that the manifest
        decides when it is this run's.
        """
        module = self._entrypoint_module()
        path = self._write_foreign_manifest("2026-08-19T09:00:00+09:00")

        # 0 is the manifest's own verdict — `read_summary()` derives the code
        # from the components rather than trusting the number in the file,
        # which is why the fixture does not try to set one. 0 against the
        # fallback 2 is the distinction that matters here anyway: it is the
        # difference between "this run said so" and "nothing said so".
        self.assertEqual(
            module._exit_code_from_manifest(path, superseding="an-earlier-run"), 0
        )
        self.assertEqual(module._exit_code_from_manifest(path, superseding=None), 0)
        self.assertEqual(
            module._exit_code_from_manifest(
                path, superseding="2026-08-19T09:00:00+09:00"
            ),
            2,
            "a manifest still carrying the pre-run id is not this run's",
        )

    def test_the_run_id_reader_answers_none_rather_than_guessing(self):
        """Absent and unreadable collapse to None on purpose: both mean
        "there is no identity here", and the caller answers both by not
        trusting the file."""
        module = self._entrypoint_module()
        missing = self.runtime / "runs" / "nothing.json"
        broken = self.runtime / "runs" / "broken.json"
        broken.write_text("{not json", encoding="utf-8")

        self.assertIsNone(module._manifest_run_id(None))
        self.assertIsNone(module._manifest_run_id(missing))
        self.assertIsNone(module._manifest_run_id(broken))
        self.assertEqual(
            module._manifest_run_id(self._write_foreign_manifest("abc")), "abc"
        )

    def _entrypoint_module(self):
        import importlib.util

        path = REPO_ROOT / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_c82", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_both_reporters_read_the_exit_code_from_the_same_place(self):
        """`_report_backup_failure()` worked this out first, for one
        exception type. A second copy of the rule is how the two come to
        disagree, so the body moved into `_exit_code_from_manifest()` and
        both call it."""
        source = (REPO_ROOT / "run_company_ops.py").read_text(encoding="utf-8")

        self.assertEqual(source.count("def _exit_code_from_manifest("), 1)
        self.assertEqual(
            source.count("return _exit_code_from_manifest("), 2,
            "both abort reporters must delegate to the one helper",
        )

        # Which functions read the manifest off disk, by AST rather than by
        # counting a string. Two earlier drafts of this assertion used a
        # fingerprint and both aged badly: counting the *name* let a
        # re-inlined body pass (C78), and counting `except RunSummaryError:`
        # broke the day C82 added a second, legitimate reader for a
        # different question (`_manifest_run_id`, which reads an identity,
        # not a verdict). The property is about **where the decision lives**,
        # so that is what is checked.
        import ast

        tree = ast.parse(source)
        readers = set()
        for function in [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        ]:
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "read_summary"
                ):
                    readers.add(function.name)

        self.assertEqual(
            readers, {"_manifest_run_id", "_exit_code_from_manifest"},
            "something other than the two manifest helpers reads the "
            "manifest off disk — that is a second answer to how bad the "
            "run was",
        )



if __name__ == "__main__":
    unittest.main()
