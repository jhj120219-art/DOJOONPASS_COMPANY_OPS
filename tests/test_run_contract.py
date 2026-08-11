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
        """`is_authentication_failure()` is docs/08 §21's own rule and is
        what `run_company_ops.py` already applies. The Runner reuses it
        rather than restating it, so the two cannot drift into disagreeing
        about whether a given failure is worth waking someone for."""
        import inspect

        import app.runner as runner_module

        source = inspect.getsource(runner_module.run_once)
        self.assertIn("is_authentication_failure(str(exc))", source)


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


if __name__ == "__main__":
    unittest.main()
