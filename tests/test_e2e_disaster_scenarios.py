"""E2E Disaster Scenario Tests (Audit Sprint).

docs/10_E2E_OPERATIONS_SPEC.md defines 25 numbered E2E scenarios. The existing
suite covers the ordinary ones; the destructive ones — where the remote
diverges, the Working Copy is destroyed, or Desktop 4 is lost entirely — had
no automated coverage at all, even though they are the scenarios that decide
whether README RULE 3 ("GitHub가 Local Master를 자동으로 덮어쓰지 않는다") and
RULE 9 ("Data Safety가 Convenience보다 우선한다") actually hold.

Covered here:
    docs/10 section 30 (Scenario 16)  Remote Divergence
    docs/10 section 32 (Scenario 18)  Backup Working Copy 손상
    docs/10 section 45               Desktop 4 복구
    docs/10 section 28 (Scenario 14) / section 46-49  crash + State 우선순위

Real filesystem and real git throughout; no mocks (docs/10 section 10).
Nothing here changes production code, Runtime behaviour, or any spec.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.runner import run_once  # noqa: E402
from backup.git_ops import GitOperationError  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from reporter import Reporter  # noqa: E402
from scheduler.consistency import ConsistencyStatus, check_state_consistency  # noqa: E402


def _force_rmtree(path: Path) -> None:
    """git object files are read-only on Windows; clear the flag first.

    shutil.rmtree's `onexc` callback was added in Python 3.12; `onerror`
    (deprecated there, still the only option before it) has a different
    callback signature, so which kwarg to pass has to be chosen at runtime.
    """

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


class DisasterScenarioTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.local_master_dir = self.root / "local_master"
        self.local_master_dir.mkdir(parents=True, exist_ok=True)
        self.backup_working_copy_dir = self.root / "backup_working_copy"
        self.backup_working_copy_dir.mkdir(parents=True, exist_ok=True)
        self.bare_remote_dir = self.root / "backup_remote.git"
        self._init_backup_git_repo(self.backup_working_copy_dir)

        self.runner_lock_path = self.root / "runtime" / "locks" / "company_ops.lock"
        self.transport_dir = self.root / "runtime" / "transport"
        self.incoming_dir = self.root / "runtime" / "events" / "incoming"
        self.processed_dir = self.root / "runtime" / "events" / "processed"
        self.rejected_dir = self.root / "runtime" / "events" / "rejected"
        self.collector_log_path = self.root / "runtime" / "logs" / "collector.log"
        self.collector_state_path = self.root / "runtime" / "state" / "collector_state.json"
        self.keep_dir = self.root / "runtime" / "history_candidates" / "keep"
        self.review_dir = self.root / "runtime" / "history_candidates" / "review"
        self.scheduler_state_path = self.root / "runtime" / "state" / "daily_history_state.json"
        self.backup_state_path = self.root / "runtime" / "state" / "backup_state.json"

        self.reporter = Reporter(profile="DESKTOP_3")

    def _run_git(self, args, cwd, check=True):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def _init_backup_git_repo(self, working_copy_dir: Path) -> None:
        self._run_git(["init", "--bare", "-b", "main", str(self.bare_remote_dir)], cwd=self.root)
        self._run_git(["init", "-b", "main"], cwd=working_copy_dir)
        self._run_git(["config", "user.email", "test@example.invalid"], cwd=working_copy_dir)
        self._run_git(["config", "user.name", "Disaster Test"], cwd=working_copy_dir)
        self._run_git(["remote", "add", "origin", str(self.bare_remote_dir)], cwd=working_copy_dir)
        (working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        self._run_git(["add", "-A"], cwd=working_copy_dir)
        self._run_git(["commit", "-m", "init"], cwd=working_copy_dir)
        self._run_git(["push", "-u", "origin", "main"], cwd=working_copy_dir)

    def _write_event(self, **overrides):
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        data = dict(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="disaster scenario event",
            milestone="M1",
            evidence=[],
            history_candidate=True,
            timestamp="2026-08-01T10:00:00+09:00",
        )
        data.update(overrides)
        _, path = self.reporter.report_and_write(directory=self.incoming_dir, **data)
        return path

    def _run(self, *, now=None, history_start_date=date(2026, 8, 1)):
        return run_once(
            local_master_dir=self.local_master_dir,
            backup_working_copy_dir=self.backup_working_copy_dir,
            history_start_date=history_start_date,
            runner_lock_path=self.runner_lock_path,
            now=now or datetime(2026, 8, 2, 12, 0).astimezone(),
            transport_dir=self.transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            collector_log_path=self.collector_log_path,
            late_update_log_path=self.collector_log_path.parent / "daily_late_update.log",
            monthly_state_path=self.collector_log_path.parent / "monthly_history_state.json",
            run_summary_path=self.collector_log_path.parent / "last_run.json",
            collector_state_path=self.collector_state_path,
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
            scheduler_state_path=self.scheduler_state_path,
            backup_state_path=self.backup_state_path,
        )

    def _daily_snapshot(self) -> dict:
        daily_dir = self.local_master_dir / "daily"
        if not daily_dir.is_dir():
            return {}
        return {p.name: p.read_text(encoding="utf-8") for p in daily_dir.glob("*.md")}


class RemoteDivergenceTests(DisasterScenarioTestCase):
    """docs/10 section 30 (Scenario 16). Expected: 자동 Pull 금지, 자동 Merge
    금지, Force Push 금지, Local Master 변경 금지."""

    def _push_an_outsider_commit(self):
        clone = self.root / "someone_else"
        self._run_git(["clone", str(self.bare_remote_dir), str(clone)], cwd=self.root)
        self._run_git(["config", "user.email", "other@example.invalid"], cwd=clone)
        self._run_git(["config", "user.name", "Someone Else"], cwd=clone)
        (clone / "daily").mkdir(parents=True, exist_ok=True)
        (clone / "daily" / "OUTSIDER.md").write_text("edited elsewhere", encoding="utf-8")
        self._run_git(["add", "-A"], cwd=clone)
        self._run_git(["commit", "-m", "outsider commit"], cwd=clone)
        self._run_git(["push"], cwd=clone)

    def test_diverged_remote_makes_the_push_fail(self):
        self._write_event(event_id="DISASTER-DIV-001")
        self._run()
        self._push_an_outsider_commit()

        self._write_event(event_id="DISASTER-DIV-002", timestamp="2026-08-02T10:00:00+09:00")
        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 3, 12, 0).astimezone())

    def test_local_master_is_never_modified_by_a_diverged_remote(self):
        """README RULE 3: GitHub가 Local Master를 자동으로 덮어쓰지 않는다."""
        self._write_event(event_id="DISASTER-DIV-003")
        self._run()
        before = self._daily_snapshot()
        self._push_an_outsider_commit()

        self._write_event(event_id="DISASTER-DIV-004", timestamp="2026-08-02T10:00:00+09:00")
        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 3, 12, 0).astimezone())

        after = self._daily_snapshot()
        for name, content in before.items():
            self.assertIn(name, after)
            self.assertEqual(after[name], content)
        self.assertFalse((self.local_master_dir / "daily" / "OUTSIDER.md").exists())

    def test_no_pull_merge_or_reset_is_ever_performed(self):
        """docs/08 section 5's forbidden commands must not appear in the
        Working Copy's reflog after a rejected push."""
        self._write_event(event_id="DISASTER-DIV-005")
        self._run()
        self._push_an_outsider_commit()

        self._write_event(event_id="DISASTER-DIV-006", timestamp="2026-08-02T10:00:00+09:00")
        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 3, 12, 0).astimezone())

        reflog = self._run_git(["reflog"], cwd=self.backup_working_copy_dir).stdout.lower()
        for forbidden in ("pull", "merge", "rebase", "reset"):
            self.assertNotIn(forbidden, reflog)

    def test_divergence_is_classified_pending_not_failed(self):
        """Audit finding BUG-8: docs/10 section 30 asks for "Backup
        Review/Failed", but a rejected push carries no authentication marker,
        so is_authentication_failure() returns False and the run is recorded
        as BACKUP_PENDING — i.e. it will be retried on every future run, the
        loop shape docs/08 section 62 forbids.

        docs/08 section 34 does permit folding BACKUP_REVIEW_REQUIRED into
        BACKUP_FAILED, so the missing state is not itself the defect; the
        classification as *transient* is.
        """
        self._write_event(event_id="DISASTER-DIV-007")
        self._run()
        self._push_an_outsider_commit()

        self._write_event(event_id="DISASTER-DIV-008", timestamp="2026-08-02T10:00:00+09:00")
        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 3, 12, 0).astimezone())

        state = json.loads(self.backup_state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["backup_status"], BackupStatus.PENDING.value)


class WorkingCopyDestroyedTests(DisasterScenarioTestCase):
    """docs/10 section 32 (Scenario 18). Expected: Local Master 영향 없음,
    Working Copy는 다시 구성 가능해야 한다."""

    def test_local_master_survives_a_destroyed_working_copy(self):
        self._write_event(event_id="DISASTER-WC-001")
        self._run()
        before = self._daily_snapshot()
        self.assertTrue(before)

        _force_rmtree(self.backup_working_copy_dir)

        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 3, 12, 0).astimezone())

        after = self._daily_snapshot()
        for name, content in before.items():
            self.assertEqual(after.get(name), content)

    def test_working_copy_is_not_silently_reconstructed(self):
        """The Runner does not re-init the repo behind the operator's back —
        rebuilding it is a deliberate human step (docs/08 section 30)."""
        self._write_event(event_id="DISASTER-WC-002")
        self._run()
        _force_rmtree(self.backup_working_copy_dir)

        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 3, 12, 0).astimezone())

        self.assertFalse((self.backup_working_copy_dir / ".git").exists())

    def test_history_candidates_survive_a_destroyed_working_copy(self):
        self._write_event(event_id="DISASTER-WC-003")
        self._run()
        _force_rmtree(self.backup_working_copy_dir)

        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 3, 12, 0).astimezone())

        self.assertTrue((self.keep_dir / "HIST-DISASTER-WC-003.json").exists())


class Desktop4RecoveryTests(DisasterScenarioTestCase):
    """docs/10 section 45. The backup remote is the only survivor of a total
    Desktop 4 loss — what does it actually restore?"""

    def test_company_history_is_restored_byte_for_byte_from_the_remote(self):
        for day in (1, 2, 3):
            self._write_event(
                event_id=f"DISASTER-REC-{day:03d}",
                timestamp=f"2026-08-0{day}T10:00:00+09:00",
            )
        self._run(now=datetime(2026, 8, 5, 12, 0).astimezone())
        original = self._daily_snapshot()
        self.assertEqual(len(original), 4)

        # Total loss of Desktop 4: everything but the remote.
        _force_rmtree(self.local_master_dir)
        _force_rmtree(self.backup_working_copy_dir)

        restored = self.root / "restored_master"
        self._run_git(["clone", str(self.bare_remote_dir), str(restored)], cwd=self.root)
        recovered = {
            p.name: p.read_text(encoding="utf-8") for p in (restored / "daily").glob("*.md")
        }

        self.assertEqual(recovered, original)

    def test_raw_events_and_candidates_are_not_in_the_backup(self):
        """Audit finding R06b: docs/08 section 26 limits the backup to daily/
        and monthly/. Raw Events, History Candidates, and runtime state are
        therefore lost with the disk — docs/10 section 50 ("Raw Event 보존")
        has no backup path behind it.
        """
        self._write_event(event_id="DISASTER-REC-100")
        self._run()

        restored = self.root / "restored_check"
        self._run_git(["clone", str(self.bare_remote_dir), str(restored)], cwd=self.root)

        self.assertTrue((restored / "daily").is_dir())
        self.assertFalse((restored / "events").exists())
        self.assertFalse((restored / "history_candidates").exists())
        self.assertFalse((restored / "state").exists())



class TheFirstRunAfterARestoreTests(DisasterScenarioTestCase):
    """docs/10 §45 restores Company History. Then what?

    `Desktop4RecoveryTests` above proves what the remote gives back —
    `daily/` byte-for-byte — and `test_raw_events_and_candidates_are_not_in_the_backup`
    proves what it does not: Events, Candidates and **all of `runtime/state/`**
    are gone with the disk. Nothing ran the pipeline afterwards, so the state
    a restored Desktop 4 actually boots into had never been exercised:

        daily/                     restored, complete
        state/daily_history_state.json   absent — the watermark is gone
        events/processed/, seen store    absent
        history_candidates/              absent

    The pointer that says "these days are done" is the one thing the backup
    deliberately does not carry, so the first run after a restore sees a full
    Company History and no memory of having written any of it. docs/10 §46
    forbids exactly the obvious failure ("프로그램이 임의로 History를 삭제하거나
    다시 생성하면 안 된다") — a run that decides those days are unwritten and
    regenerates them from the Candidates it no longer has would replace real
    History with empty days, and then push that over the only copy.

    Scheduled recovery makes this the normal path, not an exotic one: the
    Runner runs on a trigger, so the first post-restore run happens on its
    own, before anyone thinks to look.
    """

    DAYS = (1, 2, 3)
    RESTORE_AT = datetime(2026, 8, 6, 9, 0)

    def _build_and_lose_everything(self):
        for day in self.DAYS:
            self._write_event(
                event_id=f"RESTORE-{day:03d}",
                timestamp=f"2026-08-0{day}T10:00:00+09:00",
                summary=f"work on day {day}",
            )
        self._run(now=datetime(2026, 8, 5, 12, 0).astimezone())
        original = self._daily_snapshot()
        self.assertTrue(original, "precondition: the first run wrote Company History")

        # A disk loss takes the lot. Only the bare remote survives.
        _force_rmtree(self.local_master_dir)
        _force_rmtree(self.backup_working_copy_dir)
        _force_rmtree(self.root / "runtime")
        return original

    def _restore_from_the_remote(self):
        """docs/10 §45's restore, as an operator would do it: clone the
        remote into place. The Working Copy is re-initialised the same way a
        fresh install does, because it is not what carries History."""
        self._run_git(
            ["clone", str(self.bare_remote_dir), str(self.local_master_dir)],
            cwd=self.root,
        )
        self._run_git(
            ["clone", str(self.bare_remote_dir), str(self.backup_working_copy_dir)],
            cwd=self.root,
        )
        self._run_git(
            ["config", "user.email", "test@example.invalid"],
            cwd=self.backup_working_copy_dir,
        )
        self._run_git(
            ["config", "user.name", "Disaster Test"], cwd=self.backup_working_copy_dir
        )

    def test_the_restored_history_survives_the_first_run(self):
        original = self._build_and_lose_everything()
        self._restore_from_the_remote()

        self._run(now=self.RESTORE_AT.astimezone())

        after = self._daily_snapshot()
        for name, text in original.items():
            with self.subTest(day=name):
                self.assertIn(name, after, "a restored Daily History file disappeared")
                self.assertEqual(
                    after[name],
                    text,
                    "the first run after a restore rewrote real History",
                )

    def test_the_run_does_not_push_emptied_history_over_the_only_copy(self):
        """The consequence that cannot be undone. Whatever the run decides
        locally, the remote is the last copy of Company History — if the
        push carries emptied days, the restore has destroyed what it
        recovered."""
        original = self._build_and_lose_everything()
        self._restore_from_the_remote()

        self._run(now=self.RESTORE_AT.astimezone())

        verify = self.root / "verify_remote"
        self._run_git(["clone", str(self.bare_remote_dir), str(verify)], cwd=self.root)
        remote_daily = {
            p.name: p.read_text(encoding="utf-8") for p in (verify / "daily").glob("*.md")
        }

        for name, text in original.items():
            with self.subTest(day=name):
                self.assertEqual(remote_daily.get(name), text)

    def test_the_missing_watermark_is_visible_rather_than_assumed(self):
        """Whatever the run does, an operator has to be able to tell that
        this Desktop has no memory of the History it is holding."""
        self._build_and_lose_everything()
        self._restore_from_the_remote()

        self.assertFalse(self.scheduler_state_path.exists())

        result = self._run(now=self.RESTORE_AT.astimezone())

        self.assertIsNotNone(result)
        self.assertTrue(self.scheduler_state_path.exists())

        scheduler = result[2]
        # The measurement C39 acted on. Before the split this read
        # `generated_dates = 08-01 … 08-05` — five days "generated" by a run
        # that wrote one file, on a Desktop whose History had just come back
        # from git. It cannot have generated the other four: History
        # Candidates are not in the backup (docs/08 §26), so the material
        # those days are made of no longer exists on this machine.
        self.assertEqual(
            [d.isoformat() for d in scheduler.reused_dates],
            ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"],
        )
        self.assertEqual(
            [d.isoformat() for d in scheduler.generated_dates], ["2026-08-05"]
        )
        # The watermark ends up where a full close would have put it, which
        # is why the conflation was harmless to the pipeline and misleading
        # only to the person reading it.
        self.assertEqual(len(scheduler.closed_dates), 5)

    def test_the_manifest_does_not_claim_the_pipeline_rebuilt_the_history(self):
        """The number an operator checks a restore against.

        `app/runner.py` puts `generated_days` in the Run Manifest and
        `notion/dashboard.py` puts it in the `Generated Days` column. Both
        read `generated_dates`, so both said 5 for this run.
        """
        self._build_and_lose_everything()
        self._restore_from_the_remote()

        result = self._run(now=self.RESTORE_AT.astimezone())

        daily = result.summary.component("daily")
        self.assertEqual(daily.metrics["generated_days"], 1)
        self.assertEqual(daily.metrics["reused_days"], 4)


class StateVersusHistoryTests(DisasterScenarioTestCase):
    """docs/10 sections 46-49: History가 State보다 우선하고, 프로그램이 임의로
    History를 삭제하거나 다시 생성하지 않는다."""

    def test_state_claiming_a_missing_history_file_is_reported(self):
        """docs/10 section 47's exact failure shape."""
        self._write_event(event_id="DISASTER-STATE-001")
        self._run(now=datetime(2026, 8, 5, 12, 0).astimezone())

        daily_dir = self.local_master_dir / "daily"
        newest = sorted(daily_dir.glob("*.md"))[-1]
        newest.unlink()

        result = check_state_consistency(self.scheduler_state_path, daily_dir)

        self.assertIs(result.status, ConsistencyStatus.STATE_INCONSISTENCY)
        self.assertEqual(result.expected_history_path, newest)

    def test_the_runner_does_not_consult_the_consistency_checker(self):
        """Audit finding GAP-2: scheduler/consistency.py has no production
        caller, so a detected inconsistency never reaches the Runner. The run
        proceeds normally and the deleted file is NOT regenerated (which is
        itself correct per section 46 — but nothing reports the problem).
        """
        self._write_event(event_id="DISASTER-STATE-002")
        self._run(now=datetime(2026, 8, 5, 12, 0).astimezone())

        daily_dir = self.local_master_dir / "daily"
        newest = sorted(daily_dir.glob("*.md"))[-1]
        newest.unlink()

        result = self._run(now=datetime(2026, 8, 6, 12, 0).astimezone())

        self.assertIsNotNone(result)
        self.assertFalse(newest.exists())

    def test_a_daily_file_written_before_a_crash_is_reused_not_rewritten(self):
        """docs/07 section 28: a run that wrote the .md but died before saving
        state must not produce a different file on the retry."""
        self._write_event(event_id="DISASTER-STATE-003")
        self._run(now=datetime(2026, 8, 5, 12, 0).astimezone())
        before = self._daily_snapshot()

        # Simulate the crash: roll state back, leave every .md in place.
        self.scheduler_state_path.write_text(
            json.dumps({"last_successful_daily_close": "2026-08-02"}), encoding="utf-8"
        )

        result = self._run(now=datetime(2026, 8, 5, 13, 0).astimezone())

        self.assertEqual(self._daily_snapshot(), before)
        # The two assertions used to contradict each other one line apart:
        # "not a single file changed" and "two dates were generated". C39
        # split the field, so the second one can now say what happened.
        self.assertEqual([d.isoformat() for d in result[2].generated_dates], [])
        self.assertEqual(
            [d.isoformat() for d in result[2].reused_dates],
            ["2026-08-03", "2026-08-04"],
        )
        self.assertEqual(
            [d.isoformat() for d in result[2].closed_dates],
            ["2026-08-03", "2026-08-04"],
        )


if __name__ == "__main__":
    unittest.main()
