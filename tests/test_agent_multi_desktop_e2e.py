"""Multi-Desktop end-to-end: four Agents -> OneDrive -> COO Company Ops.

The full path README section 4 draws, with no mocked Company Ops code at
any stage:

    Desktop 1 (CTO Backend)  ┐
    Desktop 2 (CMO)          ├─ agent.run_once()
    Desktop 3 (CTO Frontend) │     -> outbox/ -> OneDriveTransport
    Desktop 4 (COO)          ┘        -> shared OneDrive sync folder
                                          -> Desktop 4 transport/
                                             -> app.runner.run_once()
                                                (intake -> Collector ->
                                                 History Filter -> Daily ->
                                                 Backup, real git)

Honest scope note, carried over from
tests/test_desktop3_to_desktop4_transport.py: this environment is one
machine with no OneDrive account, so real cloud latency cannot be
exercised. The single shared folder stands in for the cloud, and
`_sync_cloud()` for the OneDrive client's propagation to each Desktop's
local mirror. Everything Company Ops code is actually responsible for is
real, including the git backup.

Notion is exercised through InMemoryNotionTransport (docs/10 §10), the
same double every other E2E file here uses.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import AgentState, AgentStatus, DateOutcome, load_state, save_state  # noqa: E402
from agent.state import AgentStateError  # noqa: E402
from agent import run_once as agent_run_once  # noqa: E402
from app.runner import run_once as company_ops_run_once  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from collector.runtime import RuntimeOutcome  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    InMemoryNotionTransport,
    NotionClient,
)
from transport import OneDriveTransport, Transport, TransportError  # noqa: E402

# Assembled at runtime, never written out as one literal — see the same
# constant in tests/test_agent.py for why
# test_repository_hygiene.py::test_no_secret_material_in_any_tracked_file
# must not have to tell a fixture from a real credential.
FAKE_NOTION_TOKEN = "ntn_" + "ABCDEFGHIJKLMNOP1234"
SECRET_PREFIX = "ntn" + "_"

DESKTOPS = (
    ("DESKTOP_1", "CTO_BACKEND", "SEARCH_BACKEND"),
    ("DESKTOP_2", "CMO", "CONTENT_OS"),
    ("DESKTOP_3", "CTO_FRONTEND", "SEARCH_FRONTEND"),
    ("DESKTOP_4", "COO", "COMPANY_OPS"),
)


class MultiDesktopTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        # The one physical folder standing in for "the OneDrive cloud".
        self.cloud = self.root / "onedrive_cloud"
        self.cloud.mkdir(parents=True, exist_ok=True)

        # --- Desktop 4 / Company Ops ---
        d4 = self.root / "desktop4"
        self.local_master_dir = d4 / "local_master"
        self.local_master_dir.mkdir(parents=True, exist_ok=True)
        self.backup_working_copy_dir = d4 / "backup_working_copy"
        self.backup_working_copy_dir.mkdir(parents=True, exist_ok=True)
        self.bare_remote_dir = d4 / "backup_remote.git"
        self._init_backup_git_repo(self.backup_working_copy_dir)

        self.runner_lock_path = d4 / "runtime" / "locks" / "company_ops.lock"
        self.d4_transport_dir = d4 / "runtime" / "events" / "transport"
        self.incoming_dir = d4 / "runtime" / "events" / "incoming"
        self.processed_dir = d4 / "runtime" / "events" / "processed"
        self.rejected_dir = d4 / "runtime" / "events" / "rejected"
        self.collector_log_path = d4 / "runtime" / "logs" / "collector.log"
        self.collector_state_path = d4 / "runtime" / "state" / "collector_state.json"
        self.keep_dir = d4 / "runtime" / "history_candidates" / "keep"
        self.review_dir = d4 / "runtime" / "history_candidates" / "review"
        self.scheduler_state_path = d4 / "runtime" / "state" / "daily_history_state.json"
        self.backup_state_path = d4 / "runtime" / "state" / "backup_state.json"

        self.notion_transport = InMemoryNotionTransport()
        self.notion_sync = ExecutionPlanSync(
            client=NotionClient(transport=self.notion_transport, database_id="DB-MULTI")
        )

    # ------------------------------------------------------------------ git

    def _run_git(self, args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _init_backup_git_repo(self, working_copy_dir: Path) -> None:
        self._run_git(["init", "--bare", "-b", "main", str(self.bare_remote_dir)], cwd=self.root)
        self._run_git(["init", "-b", "main"], cwd=working_copy_dir)
        self._run_git(["config", "user.email", "test@example.invalid"], cwd=working_copy_dir)
        self._run_git(["config", "user.name", "Multi Desktop Test"], cwd=working_copy_dir)
        self._run_git(["remote", "add", "origin", str(self.bare_remote_dir)], cwd=working_copy_dir)
        (working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        self._run_git(["add", "-A"], cwd=working_copy_dir)
        self._run_git(["commit", "-m", "init"], cwd=working_copy_dir)
        self._run_git(["push", "-u", "origin", "main"], cwd=working_copy_dir)

    # -------------------------------------------------------------- desktops

    def desktop_dir(self, desktop_id: str) -> Path:
        return self.root / desktop_id.lower() / "runtime" / "agent"

    def write_signal(self, desktop_id: str, day: date, name: str, **overrides) -> Path:
        project = next(p for d, _, p in DESKTOPS if d == desktop_id)
        payload = {
            "project_id": project,
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "summary": f"{desktop_id} {name} on {day.isoformat()}",
            "milestone": name,
            "evidence": ["tests PASS"],
            "history_candidate": True,
        }
        payload.update(overrides)
        directory = self.desktop_dir(desktop_id) / "signals" / day.isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_agent(self, desktop_id: str, *, now: datetime, start_date: date, transport=None):
        agent_dir = self.desktop_dir(desktop_id)
        return agent_run_once(
            transport=transport
            or OneDriveTransport(
                sync_folder=self.cloud, outgoing_dir=agent_dir / "outgoing"
            ),
            agent_start_date=start_date,
            profile=desktop_id,
            now=now,
            signals_dir=agent_dir / "signals",
            rejected_signals_dir=agent_dir / "signals_rejected",
            outbox_dir=agent_dir / "outbox",
            sent_dir=agent_dir / "sent",
            state_path=agent_dir / "state" / "agent_state.json",
            lock_path=agent_dir / "locks" / "agent.lock",
            log_path=agent_dir / "logs" / "agent.log",
        )

    def _sync_cloud(self):
        """OneDrive propagating the cloud folder into Desktop 4's mirror."""
        self.d4_transport_dir.mkdir(parents=True, exist_ok=True)
        for path in self.cloud.glob("*.json"):
            shutil.copy2(path, self.d4_transport_dir / path.name)

    # ------------------------------------------------------------ company ops

    def run_company_ops(self, *, now: datetime, history_start_date: date, notion=True):
        return company_ops_run_once(
            local_master_dir=self.local_master_dir,
            backup_working_copy_dir=self.backup_working_copy_dir,
            history_start_date=history_start_date,
            runner_lock_path=self.runner_lock_path,
            now=now,
            transport_dir=self.d4_transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            collector_log_path=self.collector_log_path,
            collector_state_path=self.collector_state_path,
            notion_sync=self.notion_sync if notion else None,
            notion_sync_log_path=self.root / "notion_sync.log",
            late_update_log_path=self.root / "daily_late_update.log",
            monthly_state_path=self.root / "monthly_history_state.json",
            run_summary_path=self.root / "last_run.json",
            notion_retry_queue_path=self.root / "notion_retry_queue.json",
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
            scheduler_state_path=self.scheduler_state_path,
            backup_state_path=self.backup_state_path,
        )

    def deliver_and_collect(self, *, now: datetime, history_start_date: date, notion=True):
        """One full hop: cloud -> Desktop 4 mirror -> Company Ops Runner."""
        self._sync_cloud()
        self._age_transport_files()
        return self.run_company_ops(
            now=now, history_start_date=history_start_date, notion=notion
        )

    def _age_transport_files(self):
        """Backdate mtimes so `run_intake`'s stability window has passed.

        `DEFAULT_STABLE_AFTER_SECONDS` is 5.0 real seconds. Backdating is
        what every other transport test in this suite does rather than
        sleeping — the code under test is unchanged, only the clock the
        files claim.
        """
        import os
        import time

        old = time.time() - 3600
        for path in self.d4_transport_dir.glob("*.json"):
            os.utime(path, (old, old))

    def daily(self, day: date) -> str:
        return (self.local_master_dir / "daily" / f"{day.isoformat()}.md").read_text(
            encoding="utf-8"
        )


class AllFourDesktopsTests(MultiDesktopTestCase):
    def test_every_desktop_reaches_company_history_through_one_pipeline(self):
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, day, "work")

        for desktop_id, _, _ in DESKTOPS:
            result = self.run_agent(
                desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day
            )
            self.assertEqual(result.status, AgentStatus.COMPLETED, desktop_id)

        runner_result = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )
        intake, collector, scheduler, backup, notion_results = runner_result

        self.assertEqual(len(intake.moved), 4)
        self.assertEqual(collector.accepted, 4)
        self.assertEqual(collector.rejected, 0)
        self.assertEqual(collector.failed, 0)

        markdown = self.daily(day)
        for desktop_id, role, project in DESKTOPS:
            with self.subTest(desktop=desktop_id):
                self.assertIn(project.replace("_", " ").title(), markdown)
        self.assertIn("CTO Backend", markdown)
        self.assertIn("CTO Frontend", markdown)
        self.assertIn("CMO", markdown)
        self.assertIn("COO", markdown)

        self.assertEqual(len(notion_results), 4)
        self.assertEqual(backup.final_status, BackupStatus.SUCCESS)

    def test_a_role_with_no_activity_simply_does_not_appear(self):
        """docs/06 §25's Empty Day, applied per role: a silent role is
        normal, and needs no NO_ACTIVITY Event on the wire to be correct."""
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "backend-work")

        for desktop_id, _, _ in DESKTOPS:
            result = self.run_agent(
                desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day
            )
            self.assertEqual(result.status, AgentStatus.COMPLETED, desktop_id)
            expected = (
                DateOutcome.COLLECTED if desktop_id == "DESKTOP_1" else DateOutcome.NO_ACTIVITY
            )
            self.assertEqual(result.dates[0].outcome, expected, desktop_id)

        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        markdown = self.daily(day)
        self.assertIn("CTO Backend", markdown)
        self.assertNotIn("CMO", markdown)

    def test_a_day_where_no_desktop_did_anything_still_produces_a_daily_file(self):
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)

        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        self.assertIn("No material company history recorded.", self.daily(day))


class ConcurrentDesktopDeliveryTests(MultiDesktopTestCase):
    def test_four_desktops_writing_the_same_folder_lose_nothing(self):
        """All four Agents deliver into one OneDrive folder in the same
        window — the realistic shape of "everyone switched their PC on this
        morning"."""
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            for index in range(3):
                self.write_signal(desktop_id, day, f"task-{index}")

        for desktop_id, _, _ in DESKTOPS:
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)

        self.assertEqual(len(list(self.cloud.glob("*.json"))), 12)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )

        self.assertEqual(collector.accepted, 12)
        markdown = self.daily(day)
        for desktop_id, _, _ in DESKTOPS:
            for index in range(3):
                with self.subTest(desktop=desktop_id, task=index):
                    self.assertIn(f"{desktop_id} task-{index}", markdown)

    def test_two_desktops_can_never_produce_the_same_event_id(self):
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, day, "same-filename")
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)

        delivered = sorted(path.name for path in self.cloud.glob("*.json"))
        self.assertEqual(len(delivered), 4, "identically-named Signals collided")
        self.assertEqual(len(set(delivered)), 4)


class DuplicateArrivalTests(MultiDesktopTestCase):
    def test_the_same_event_delivered_twice_is_recorded_once(self):
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)

        first = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )
        self.assertEqual(first[1].accepted, 1)

        # OneDrive re-delivers the very same file (a resync, a restored
        # backup, a manual copy) after Desktop 4 already consumed it.
        second = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 12, 0), history_start_date=day
        )

        self.assertEqual(second[1].accepted, 0)
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 1)

    def test_an_agent_rerun_after_a_lost_state_save_produces_no_duplicate(self):
        """The crash shape that a random event_id would have turned into two
        History entries for one real milestone."""
        from agent import AgentState, save_state

        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)

        state_path = self.desktop_dir("DESKTOP_1") / "state" / "agent_state.json"
        save_state(state_path, AgentState(desktop_id="DESKTOP_1"))
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 10, 0), start_date=day)

        self.assertEqual(len(list(self.cloud.glob("*.json"))), 1)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )
        self.assertEqual(collector.accepted, 1)
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 1)


class OneDriveConflictFileTests(MultiDesktopTestCase):
    """OneDrive's own conflict resolution, which four Agents writing one
    folder makes considerably more likely.

    When OneDrive cannot reconcile two versions of a file it keeps both,
    renaming one to `<name>-DESKTOP-ABC123.json`. That copy carries a
    different FILENAME but the SAME `event_id`, so the filename-based
    "already arrived" check in `transport/intake.py` does not catch it —
    Collector's event_id dedup is what has to, and this pins that it does.
    """

    def test_a_onedrive_conflict_copy_produces_no_duplicate_history(self):
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)

        original = next(iter(self.cloud.glob("*.json")))
        conflict = self.cloud / f"{original.stem}-DESKTOP-ABC123.json"
        shutil.copy2(original, conflict)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )

        self.assertEqual(collector.accepted, 1)
        self.assertEqual(collector.duplicate, 1)
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 1)
        # `Event Count` rather than counting the summary text: docs/06's
        # template prints each summary twice by design (once under
        # `## Summary`, once in the item block), so a text count cannot
        # distinguish one candidate from two.
        self.assertIn("- Event Count: 1", self.daily(day))


class Desktop4ConcurrencyTests(MultiDesktopTestCase):
    """On Desktop 4 the Agent and the Company Ops Runner both exist. They
    guard different critical sections with different lock files, so one
    must never block the other."""

    def test_the_agent_can_run_while_the_runner_holds_its_lock(self):
        from scheduler.lock import release_lock, try_acquire_lock

        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_4", day, "coo-work")

        try_acquire_lock(self.runner_lock_path, now=datetime(2026, 8, 9, 9, 0))
        try:
            result = self.run_agent(
                "DESKTOP_4", now=datetime(2026, 8, 9, 9, 0), start_date=day
            )
        finally:
            release_lock(self.runner_lock_path)

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(list(self.cloud.glob("*.json"))), 1)

    def test_the_runner_can_run_while_the_agent_holds_its_lock(self):
        from scheduler.lock import release_lock, try_acquire_lock

        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)

        agent_lock = self.desktop_dir("DESKTOP_4") / "locks" / "agent.lock"
        agent_lock.parent.mkdir(parents=True, exist_ok=True)
        try_acquire_lock(agent_lock, now=datetime(2026, 8, 9, 11, 0))
        try:
            result = self.deliver_and_collect(
                now=datetime(2026, 8, 9, 11, 0), history_start_date=day
            )
        finally:
            release_lock(agent_lock)

        self.assertIsNotNone(result)
        self.assertEqual(result[1].accepted, 1)


class CentralCollectionFailureTests(MultiDesktopTestCase):
    def test_desktop4_off_for_three_days_loses_nothing(self):
        """README RULE 7 from the sending side: the Agents keep working and
        keep delivering into OneDrive while Company Ops is switched off."""
        days = [date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10)]
        for day in days:
            self.write_signal("DESKTOP_1", day, "daily-work")
            self.write_signal("DESKTOP_2", day, "campaign")

        for desktop_id in ("DESKTOP_1", "DESKTOP_2"):
            result = self.run_agent(
                desktop_id, now=datetime(2026, 8, 11, 9, 0), start_date=days[0]
            )
            self.assertEqual(result.status, AgentStatus.COMPLETED)
            self.assertEqual([d.date for d in result.dates], days)

        # Desktop 4 comes back on the 11th and catches up in one run.
        _, collector, scheduler, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 11, 11, 0), history_start_date=days[0]
        )

        self.assertEqual(collector.accepted, 6)
        self.assertEqual(list(scheduler.generated_dates), days)
        for day in days:
            with self.subTest(day=day):
                markdown = self.daily(day)
                self.assertIn("daily-work", markdown)
                self.assertIn("campaign", markdown)

    def test_a_desktop4_runner_failure_leaves_the_events_recoverable(self):
        """"중앙 수집 실패": the Runner cannot finish, so nothing is filed —
        but the Events are still on disk and a later run picks them up."""
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self._sync_cloud()
        self._age_transport_files()

        # A concurrent Runner holds the lock: run_once() returns None
        # without touching a single Event.
        from scheduler.lock import release_lock, try_acquire_lock

        try_acquire_lock(self.runner_lock_path, now=datetime(2026, 8, 9, 11, 0))
        try:
            blocked = self.run_company_ops(
                now=datetime(2026, 8, 9, 11, 0), history_start_date=day
            )
        finally:
            release_lock(self.runner_lock_path)

        self.assertIsNone(blocked)
        self.assertEqual(len(list(self.d4_transport_dir.glob("*.json"))), 1)

        recovered = self.run_company_ops(
            now=datetime(2026, 8, 9, 12, 0), history_start_date=day
        )
        self.assertEqual(recovered[1].accepted, 1)
        self.assertIn("work", self.daily(day))

    def test_history_survives_a_local_master_wipe_via_the_git_backup(self):
        """History 복구: Local Master is Primary (README RULE 2) and GitHub
        is its off-device copy (RULE 3). Losing the former must not lose
        Company History."""
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "irreplaceable")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        original = self.daily(day)
        self.assertIn("irreplaceable", original)

        shutil.rmtree(self.local_master_dir)

        restored = self.root / "restored"
        self._run_git(["clone", str(self.bare_remote_dir), str(restored)], cwd=self.root)
        recovered = (restored / "daily" / f"{day.isoformat()}.md").read_text(encoding="utf-8")

        self.assertEqual(recovered, original)


class OneDriveTimingTests(MultiDesktopTestCase):
    """The states a real OneDrive folder passes through that a local copy
    never does: a file still arriving, a placeholder, a truncated write, and
    Desktops whose deliveries land out of chronological order."""

    def test_a_file_still_being_synced_is_left_alone_and_picked_up_later(self):
        """`run_intake`'s stability window exists for exactly this: a file
        OneDrive is still writing must not be handed to the Collector."""
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self._sync_cloud()  # arrives, but mtime is "just now"

        first = self.run_company_ops(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )
        self.assertEqual(len(first[0].moved), 0)
        self.assertEqual(len(first[0].skipped_not_stable), 1)
        self.assertEqual(first[1].accepted, 0)

        self._age_transport_files()  # the sync finished a while ago now
        second = self.run_company_ops(
            now=datetime(2026, 8, 9, 12, 0), history_start_date=day
        )

        self.assertEqual(len(second[0].moved), 1)
        self.assertEqual(second[1].accepted, 1)
        self.assertIn("DESKTOP_1 work", self.daily(day))

    def test_a_partially_written_file_is_never_collected_and_recovers(self):
        """A truncated JSON document is what a half-synced Event looks like.
        It must not be collected, must not be deleted, and must go through
        once it is complete."""
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)

        source = next(iter(self.cloud.glob("*.json")))
        complete = source.read_text(encoding="utf-8")
        self.d4_transport_dir.mkdir(parents=True, exist_ok=True)
        partial = self.d4_transport_dir / source.name
        partial.write_text(complete[: len(complete) // 2], encoding="utf-8")
        self._age_transport_files()

        first = self.run_company_ops(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )
        self.assertEqual(len(first[0].skipped_invalid), 1)
        self.assertEqual(first[1].accepted, 0)
        self.assertEqual(first[1].rejected, 0, "a half-written file must not be rejected")
        self.assertTrue(partial.exists(), "the partial file was deleted")

        partial.write_text(complete, encoding="utf-8")
        self._age_transport_files()
        second = self.run_company_ops(
            now=datetime(2026, 8, 9, 12, 0), history_start_date=day
        )

        self.assertEqual(second[1].accepted, 1)
        self.assertIn("DESKTOP_1 work", self.daily(day))

    def test_a_zero_byte_placeholder_is_not_mistaken_for_an_event(self):
        day = date(2026, 8, 8)
        self.d4_transport_dir.mkdir(parents=True, exist_ok=True)
        (self.d4_transport_dir / "placeholder.json").write_text("", encoding="utf-8")
        self._age_transport_files()

        result = self.run_company_ops(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )

        self.assertEqual(len(result[0].skipped_invalid), 1)
        self.assertEqual(result[1].accepted, 0)
        self.assertEqual(result[1].rejected, 0)
        self.assertTrue((self.d4_transport_dir / "placeholder.json").exists())

    def test_events_arriving_out_of_date_order_still_land_on_the_right_day(self):
        """Desktop 2 delivers 08-10 while Desktop 1 is still offline with its
        08-08 work. Before the Late Event fix the older day was already
        closed and its Event was lost; now both days are correct."""
        early, late = date(2026, 8, 8), date(2026, 8, 10)
        self.write_signal("DESKTOP_1", early, "the-older-work")
        self.write_signal("DESKTOP_2", late, "the-newer-work")

        # Desktop 2 delivers first and Desktop 4 closes both days.
        self.run_agent("DESKTOP_2", now=datetime(2026, 8, 11, 9, 0), start_date=early)
        self.deliver_and_collect(
            now=datetime(2026, 8, 11, 10, 0), history_start_date=early
        )
        self.assertIn("the-newer-work", self.daily(late))
        self.assertIn("No material company history recorded.", self.daily(early))

        # Desktop 1 comes back the next day with the older date's work.
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 12, 9, 0), start_date=early)
        self.deliver_and_collect(
            now=datetime(2026, 8, 12, 10, 0), history_start_date=early
        )

        self.assertIn("the-older-work", self.daily(early))
        self.assertIn("- Late Events Added: 1", self.daily(early))
        # The newer day is untouched by the older day's repair.
        self.assertIn("the-newer-work", self.daily(late))
        self.assertNotIn("the-older-work", self.daily(late))


class RebootCatchupTests(MultiDesktopTestCase):
    """"Desktop 재부팅 후 Catch-up": every Agent run is a fresh process, so
    a reboot is simply the next run reading state from disk. What must
    survive the restart is the outbox, the sent/ record, and the collection
    date."""

    def test_a_reboot_mid_outage_resumes_without_loss_or_duplication(self):
        from transport import Transport, TransportError

        class Down(Transport):
            def send(self, event):
                raise TransportError("no network yet after boot")

        early = date(2026, 8, 8)
        for day in (early, date(2026, 8, 9)):
            self.write_signal("DESKTOP_1", day, f"work-{day.day}")

        # Boot 1: network not up yet.
        first = self.run_agent(
            "DESKTOP_1", now=datetime(2026, 8, 10, 9, 0), start_date=early, transport=Down()
        )
        self.assertEqual(first.status, AgentStatus.FAILED)

        agent_dir = self.desktop_dir("DESKTOP_1")
        outbox_after_boot1 = sorted(p.name for p in (agent_dir / "outbox").glob("*.json"))
        self.assertEqual(len(outbox_after_boot1), 1)

        state_path = agent_dir / "state" / "agent_state.json"
        state_after_boot1 = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsNone(state_after_boot1["last_successful_collection_date"])

        # Boot 2: same machine, new process, network up.
        second = self.run_agent(
            "DESKTOP_1", now=datetime(2026, 8, 10, 9, 30), start_date=early
        )
        self.assertEqual(second.status, AgentStatus.COMPLETED)
        self.assertEqual([d.date for d in second.dates], [early, date(2026, 8, 9)])
        self.assertEqual(len(list(self.cloud.glob("*.json"))), 2)

        # Boot 3: nothing left to do, and nothing re-sent.
        third = self.run_agent(
            "DESKTOP_1", now=datetime(2026, 8, 10, 18, 0), start_date=early
        )
        self.assertEqual(third.status, AgentStatus.COMPLETED)
        self.assertEqual(third.dates, ())
        self.assertEqual(len(list(self.cloud.glob("*.json"))), 2)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 10, 19, 0), history_start_date=early
        )
        self.assertEqual(collector.accepted, 2)
        self.assertEqual(collector.duplicate, 0)

    def test_a_reboot_after_days_off_catches_up_every_missed_date(self):
        early = date(2026, 8, 5)
        days = [early + timedelta(days=offset) for offset in range(5)]
        for day in days:
            self.write_signal("DESKTOP_3", day, f"frontend-{day.day}")

        result = self.run_agent(
            "DESKTOP_3", now=datetime(2026, 8, 10, 9, 0), start_date=early
        )

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual([d.date for d in result.dates], days)

        self.deliver_and_collect(now=datetime(2026, 8, 10, 11, 0), history_start_date=early)
        for day in days:
            with self.subTest(day=day):
                self.assertIn(f"frontend-{day.day}", self.daily(day))


class DailyReportTests(MultiDesktopTestCase):
    """What the COO can actually see about yesterday, built from real
    collected Events rather than fixtures.

    Scope note: the report is keyed by ROLE, not by Desktop, because
    `HistoryCandidate` carries `role` and not `source` (docs/05). In the
    current profile table those are one-to-one, so "CTO Backend" and
    "Desktop 1" name the same thing — but they would stop being the same if
    two Desktops ever shared a role. Carrying `source` onto the candidate is
    a docs/05 change and is recorded in BACKLOG.md rather than done here.
    """

    def _summary_for(self, day):
        from daily import build_role_summary
        from history import HistoryDecision
        from history.file_repository import FileHistoryRepository

        repo = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)
        return build_role_summary(repo.list(decision=HistoryDecision.KEEP), day)

    def test_every_role_is_reported_for_a_four_desktop_day(self):
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, day, "work")
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        summary = self._summary_for(day)

        self.assertEqual(
            sorted(summary.active_roles),
            ["CMO", "COO", "CTO_BACKEND", "CTO_FRONTEND"],
        )
        self.assertEqual(summary.silent_roles, ())
        for _, role, project in DESKTOPS:
            with self.subTest(role=role):
                activity = summary.for_role(role)
                self.assertTrue(activity.has_activity)
                self.assertEqual(activity.projects, (project,))

    def test_a_role_that_did_nothing_is_reported_as_silent(self):
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_2", day, "campaign")
        for desktop_id, _, _ in DESKTOPS:
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        summary = self._summary_for(day)

        self.assertEqual(summary.active_roles, ("CMO",))
        self.assertEqual(
            sorted(summary.silent_roles), ["COO", "CTO_BACKEND", "CTO_FRONTEND"]
        )
        self.assertFalse(summary.for_role("COO").has_activity)

    def test_many_events_from_one_role_are_all_reported(self):
        day = date(2026, 8, 8)
        for index in range(5):
            self.write_signal("DESKTOP_1", day, f"task-{index}")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        activity = self._summary_for(day).for_role("CTO_BACKEND")

        self.assertEqual(len(activity.candidates), 5)
        self.assertEqual(len(activity.of_category("MILESTONE")), 5)

    def test_a_duplicate_event_is_reported_once(self):
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 12, 0), history_start_date=day)

        activity = self._summary_for(day).for_role("CTO_BACKEND")

        self.assertEqual(len(activity.candidates), 1)

    def test_a_rejected_event_never_reaches_the_report(self):
        """An Event the Collector refuses is not company history. It must be
        absent from the report, not present-and-marked."""
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "good")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)

        self._sync_cloud()
        self.d4_transport_dir.mkdir(parents=True, exist_ok=True)
        (self.d4_transport_dir / "hand-written-invalid.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "event_id": "INVALID-001",
                    "timestamp": "2026-08-08T10:00:00+09:00",
                    "source": "DESKTOP_9",
                    "role": "WIZARD",
                    "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "should never be reported",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )
        self._age_transport_files()
        _, collector, _, _, _ = self.run_company_ops(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )

        self.assertEqual(collector.rejected, 1)
        self.assertEqual(collector.accepted, 1)

        summary = self._summary_for(day)
        all_summaries = [
            c.summary for role in summary.roles for c in role.candidates
        ]
        self.assertNotIn("should never be reported", all_summaries)
        self.assertEqual(summary.active_roles, ("CTO_BACKEND",))

    def test_each_catch_up_date_gets_its_own_report(self):
        days = [date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10)]
        self.write_signal("DESKTOP_1", days[0], "backend")
        self.write_signal("DESKTOP_2", days[2], "campaign")
        for desktop_id in ("DESKTOP_1", "DESKTOP_2"):
            self.run_agent(desktop_id, now=datetime(2026, 8, 11, 9, 0), start_date=days[0])
        self.deliver_and_collect(
            now=datetime(2026, 8, 11, 11, 0), history_start_date=days[0]
        )

        first, middle, last = (self._summary_for(day) for day in days)

        self.assertEqual(first.active_roles, ("CTO_BACKEND",))
        self.assertEqual(middle.active_roles, ())
        self.assertEqual(last.active_roles, ("CMO",))
        # Every role is still listed on the silent day — a day nobody worked
        # must read as "nobody worked", not as a missing report.
        self.assertEqual(len(middle.roles), len(first.roles))

    def test_a_late_event_reaches_both_the_daily_file_and_the_report(self):
        """The two views must not disagree: a Desktop that was offline
        across a Daily Close is exactly when they would drift."""
        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "on-time")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        self.assertEqual(self._summary_for(day).active_roles, ("CTO_BACKEND",))

        # Desktop 2 was off; it delivers the same day's work two days later.
        self.write_signal("DESKTOP_2", day, "late-campaign")
        self.run_agent("DESKTOP_2", now=datetime(2026, 8, 11, 9, 0), start_date=day)
        self.deliver_and_collect(
            now=datetime(2026, 8, 11, 11, 0), history_start_date=day
        )

        markdown = self.daily(day)
        self.assertIn("late-campaign", markdown)
        self.assertIn("- Late Events Added: 1", markdown)

        summary = self._summary_for(day)
        self.assertEqual(sorted(summary.active_roles), ["CMO", "CTO_BACKEND"])
        self.assertEqual(summary.for_role("CMO").projects, ("CONTENT_OS",))


class LateEventAcrossNotionAndHistoryTests(MultiDesktopTestCase):
    """A late Event lands in Company History but must NOT rewind Notion.

    The two destinations disagree on purpose, and the disagreement is the
    architecture rather than a bug:

        Notion  = Current State (README RULE 1). An Event from three days
                  ago must not overwrite what is true today, so docs/04
                  §29-30's Late Event guard skips it.
        History = the permanent record (RULE 2). Every Event belongs in the
                  day it happened, however late it arrives (docs/06 §37).

    Pinned here so that a future attempt to make one "consistent" with the
    other has to argue with a failing test first.
    """

    def test_a_late_event_updates_history_but_not_notion_current_state(self):
        from notion import SyncStatus

        early, late = date(2026, 8, 8), date(2026, 8, 10)

        # Desktop 1 reports on the newer day first — that becomes Notion's
        # current state for the project.
        self.write_signal("DESKTOP_1", late, "current-state-work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 11, 9, 0), start_date=early)
        self.deliver_and_collect(
            now=datetime(2026, 8, 11, 10, 0), history_start_date=early
        )

        # Then the older day's work arrives, after both dailies are closed.
        self.write_signal("DESKTOP_1", early, "older-work")
        agent_dir = self.desktop_dir("DESKTOP_1")
        (agent_dir / "state" / "agent_state.json").write_text(
            json.dumps({"desktop_id": "DESKTOP_1", "last_successful_collection_date": None}),
            encoding="utf-8",
        )
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 12, 9, 0), start_date=early)
        _, _, _, _, sync_results = self.deliver_and_collect(
            now=datetime(2026, 8, 12, 10, 0), history_start_date=early
        )

        # Company History took it.
        self.assertIn("older-work", self.daily(early))
        self.assertIn("- Late Events Added: 1", self.daily(early))

        # Notion refused to rewind.
        self.assertTrue(sync_results, "the late Event never reached Notion Sync")
        self.assertIn(
            SyncStatus.NOTION_SKIPPED_OLD_EVENT,
            {result.status for result in sync_results},
        )

    def test_an_on_time_event_still_updates_notion(self):
        """The guard above must not be so broad that ordinary Events stop
        reaching Notion."""
        from notion import SyncStatus

        day = date(2026, 8, 8)
        self.write_signal("DESKTOP_1", day, "work")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        _, _, _, _, sync_results = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )

        self.assertEqual(
            {r.status for r in sync_results}, {SyncStatus.NOTION_CREATED}
        )


class MonthlyConsolidationE2ETests(MultiDesktopTestCase):
    """Monthly History through the real Runner (docs/09 §50-51).

    docs/09 §50 fixes where Monthly sits in the sequence — after Daily
    Catch-up, before Backup — and §51 requires it to be the *same* Runner
    rather than a second competing process. Both are exercised here rather
    than asserted about the source.
    """

    def _fill_month(self, year, month, *, work_days=()):
        """Seed a complete month of Daily files directly.

        Driving 31 Runner passes would test the Scheduler, not Monthly, and
        would dominate the suite's runtime. The Daily files are what Monthly
        consumes (docs/09 §12-13), so producing them with the real Daily
        generator is the faithful fixture.
        """
        import calendar

        from daily import generate_daily_history
        from history import HistoryCandidate, HistoryDecision
        from history.file_repository import FileHistoryRepository

        repo = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)
        _, last = calendar.monthrange(year, month)
        for day_number in range(1, last + 1):
            day = date(year, month, day_number)
            if day_number in work_days:
                repo.save(
                    HistoryCandidate(
                        history_id=f"HIST-M-{day_number:02d}",
                        event_id=f"EVT-M-{day_number:02d}",
                        timestamp=f"{day.isoformat()}T10:00:00+09:00",
                        category="MILESTONE",
                        project_id="SEARCH_BACKEND",
                        role="CTO_BACKEND",
                        summary=f"month work on day {day_number}",
                        evidence=(),
                        filter_result=HistoryDecision.KEEP,
                    )
                )
            generate_daily_history(
                repo, day, output_dir=self.local_master_dir / "daily"
            )

    def test_the_runner_consolidates_a_closed_month_and_backs_it_up(self):
        self._fill_month(2026, 8, work_days=(5, 12, 20))

        result = self.run_company_ops(
            now=datetime(2026, 9, 1, 11, 0).astimezone(),
            history_start_date=date(2026, 8, 1),
        )
        self.assertIsNotNone(result)
        backup_entry = result[3]

        monthly_file = self.local_master_dir / "monthly" / "2026-08.md"
        self.assertTrue(monthly_file.exists(), "the Runner did not consolidate the month")
        text = monthly_file.read_text(encoding="utf-8")
        self.assertIn("## Major Milestones", text)
        self.assertIn("- Consolidated Items: 3", text)
        self.assertIn("- Daily Coverage: COMPLETE", text)

        # docs/09 §50: Monthly precedes Backup, so the new file reaches the
        # remote in the same run rather than waiting for the next one.
        self.assertEqual(backup_entry.final_status, BackupStatus.SUCCESS)
        cloned = self.root / "monthly_clone"
        self._run_git(["clone", str(self.bare_remote_dir), str(cloned)], cwd=self.root)
        self.assertTrue((cloned / "monthly" / "2026-08.md").exists())

    def test_the_current_month_is_not_consolidated_by_the_runner(self):
        self._fill_month(2026, 8, work_days=(5,))

        self.run_company_ops(
            now=datetime(2026, 8, 25, 11, 0).astimezone(),
            history_start_date=date(2026, 8, 1),
        )

        self.assertFalse((self.local_master_dir / "monthly" / "2026-08.md").exists())

    def test_a_late_event_rebuilds_the_month_in_the_same_run(self):
        """docs/09 §54-57 end to end: a Desktop delivers August work in
        September, after August's Monthly was already written."""
        self._fill_month(2026, 8, work_days=(5,))
        self.run_company_ops(
            now=datetime(2026, 9, 1, 11, 0).astimezone(),
            history_start_date=date(2026, 8, 1),
        )
        monthly_file = self.local_master_dir / "monthly" / "2026-08.md"
        before = monthly_file.read_text(encoding="utf-8")
        self.assertNotIn("very late milestone", before)

        # Desktop 2 was offline all of August and delivers on 09-03.
        self.write_signal(
            "DESKTOP_2", date(2026, 8, 20), "late-one", summary="very late milestone"
        )
        self.run_agent("DESKTOP_2", now=datetime(2026, 9, 3, 9, 0), start_date=date(2026, 8, 20))
        self.deliver_and_collect(
            now=datetime(2026, 9, 3, 15, 20).astimezone(),
            history_start_date=date(2026, 8, 1),
        )

        after = monthly_file.read_text(encoding="utf-8")
        self.assertIn("very late milestone", after)
        # §58: the update is recorded, and the original close time survives.
        self.assertIn("- Generated At: 2026-09-01T11:00:00", after)
        self.assertIn("- Last Updated At: 2026-09-03T15:20:00", after)
        # The Daily it came from says so too — the two never disagree.
        daily = self.daily(date(2026, 8, 20))
        self.assertIn("very late milestone", daily)
        self.assertIn("- Late Events Added: 1", daily)

    def test_a_missing_daily_is_caught_up_before_the_month_is_consolidated(self):
        """docs/09 §10's ordering, end to end:

            Missing Daily 탐지 -> Daily Catch-up -> Daily Complete -> Monthly

        The Runner puts Daily Catch-up (step 6) ahead of Monthly (step 6.7),
        so a gap present at the start of the run is already repaired by the
        time coverage is checked. That is why the month consolidates in ONE
        pass rather than staying PENDING until the next one — and it is the
        whole reason the two steps are ordered this way.
        """
        self._fill_month(2026, 8, work_days=(5,))
        missing = self.local_master_dir / "daily" / "2026-08-30.md"
        missing.unlink()
        self.assertFalse(missing.exists())

        self.run_company_ops(
            now=datetime(2026, 9, 1, 11, 0).astimezone(),
            history_start_date=date(2026, 8, 1),
        )

        self.assertTrue(missing.exists(), "Daily Catch-up did not restore the gap")
        monthly_file = self.local_master_dir / "monthly" / "2026-08.md"
        self.assertTrue(monthly_file.exists())
        self.assertIn("- Daily Coverage: COMPLETE", monthly_file.read_text(encoding="utf-8"))

    def test_a_month_whose_gap_cannot_be_repaired_stays_pending(self):
        """The other half of §39: when Daily Catch-up cannot fill the gap,
        no Monthly is written at all rather than a short one.

        The gap is made unrepairable by telling the Scheduler that day is
        already closed, which is exactly the state a crash between writing
        the Daily and saving Scheduler state would leave behind.
        """
        self._fill_month(2026, 8, work_days=(5,))
        (self.local_master_dir / "daily" / "2026-08-30.md").unlink()
        self.scheduler_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.scheduler_state_path.write_text(
            json.dumps({"last_successful_daily_close": "2026-08-31"}), encoding="utf-8"
        )

        self.run_company_ops(
            now=datetime(2026, 9, 1, 11, 0).astimezone(),
            history_start_date=date(2026, 8, 1),
        )

        self.assertFalse((self.local_master_dir / "monthly" / "2026-08.md").exists())


class AgentToCollectorContractTests(MultiDesktopTestCase):
    def test_a_signal_the_agent_rejects_never_reaches_the_cloud(self):
        day = date(2026, 8, 8)
        self.write_signal(
            "DESKTOP_1", day, "leaky", summary=f"deploy key {FAKE_NOTION_TOKEN}"
        )
        self.write_signal("DESKTOP_1", day, "clean")

        result = self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.dates[0].rejected_signals, ("leaky.json",))
        delivered = list(self.cloud.glob("*.json"))
        self.assertEqual(len(delivered), 1)
        self.assertNotIn(SECRET_PREFIX, delivered[0].read_text(encoding="utf-8"))

    def test_no_secret_reaches_the_backup_remote(self):
        day = date(2026, 8, 8)
        self.write_signal(
            "DESKTOP_1", day, "leaky", summary=f"token {FAKE_NOTION_TOKEN}"
        )
        self.write_signal("DESKTOP_1", day, "clean")
        self.run_agent("DESKTOP_1", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        cloned = self.root / "audit_clone"
        self._run_git(["clone", str(self.bare_remote_dir), str(cloned)], cwd=self.root)
        for path in cloned.rglob("*.md"):
            with self.subTest(path=path.name):
                self.assertNotIn(SECRET_PREFIX, path.read_text(encoding="utf-8"))

    def test_every_agent_produced_event_is_accepted_by_the_collector(self):
        """The Agent and the Collector must agree on what a valid Event is —
        an Agent that could produce a REJECTED Event would be silently
        losing work."""
        day = date(2026, 8, 8)
        cases = [
            ("started", {"event_type": "STARTED", "status": "IN_PROGRESS"}),
            (
                "blocked",
                {"event_type": "BLOCKED", "status": "BLOCKED", "blocker": "API quota"},
            ),
            ("resumed", {"event_type": "RESUMED", "status": "IN_PROGRESS"}),
            ("completed", {"event_type": "COMPLETED", "status": "COMPLETED"}),
            ("cancelled", {"event_type": "CANCELLED", "status": "CANCELLED"}),
            (
                "decision",
                {"event_type": "DECISION_APPROVED", "status": "IN_PROGRESS"},
            ),
            (
                "resolved",
                {"event_type": "ISSUE_RESOLVED", "status": "IN_PROGRESS"},
            ),
            (
                "milestone",
                {"event_type": "MILESTONE_COMPLETED", "status": "IN_PROGRESS"},
            ),
        ]
        for name, overrides in cases:
            self.write_signal("DESKTOP_4", day, name, **overrides)

        result = self.run_agent("DESKTOP_4", now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.assertEqual(result.dates[0].rejected_signals, ())
        self.assertEqual(len(result.dates[0].event_ids), len(cases))

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )

        self.assertEqual(collector.rejected, 0)
        self.assertEqual(collector.failed, 0)
        self.assertEqual(collector.accepted, len(cases))
        self.assertTrue(
            all(f.outcome is RuntimeOutcome.ACCEPTED for f in collector.files)
        )


class _FailingTransport(Transport):
    """A Transport that refuses everything — one Desktop's network is down.

    `TransportError` is what `OneDriveTransport` raises when the sync folder
    is unreachable, so this is the shape the Agent already handles rather
    than a new one invented for the test.
    """

    def __init__(self):
        self.attempts = 0

    def send(self, event) -> None:
        self.attempts += 1
        raise TransportError("simulated: the sync folder is unreachable")


class DesktopFaultIsolationTests(MultiDesktopTestCase):
    """One Desktop breaking must cost exactly one Desktop.

    Four Agents share nothing but a cloud folder: no lock, no state file, no
    process. That is the design, and these tests are the check that the
    design actually holds once real faults are injected rather than
    reasoned about — every case below breaks Desktop 2 in a different way
    and then asserts that Desktops 1, 3 and 4 reach Company History for the
    same date, unaffected, in the same run.

    The second half of each test matters as much as the first: the broken
    Desktop must be *contained*, not silently dropped. Its work stays on its
    own disk, its state does not advance past what it delivered, and it
    recovers on a later run.
    """

    DAY = date(2026, 8, 8)
    RUN_AT = datetime(2026, 8, 9, 11, 0)
    AGENT_AT = datetime(2026, 8, 9, 9, 0)

    def _signals_everywhere(self):
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, self.DAY, "work")

    def _run_the_healthy_three(self):
        for desktop_id, _, _ in DESKTOPS:
            if desktop_id == "DESKTOP_2":
                continue
            result = self.run_agent(
                desktop_id, now=self.AGENT_AT, start_date=self.DAY
            )
            self.assertEqual(result.status, AgentStatus.COMPLETED, desktop_id)

    def _assert_the_healthy_three_are_in_history(self):
        markdown = self.daily(self.DAY)
        for desktop_id, _, _ in DESKTOPS:
            with self.subTest(desktop=desktop_id):
                if desktop_id == "DESKTOP_2":
                    self.assertNotIn(f"{desktop_id} work", markdown)
                else:
                    self.assertIn(f"{desktop_id} work", markdown)

    def _agent_state(self, desktop_id):
        return load_state(self.desktop_dir(desktop_id) / "state" / "agent_state.json")

    # ------------------------------------------------------------------

    def test_a_corrupt_outbox_on_one_desktop_holds_back_only_that_desktop(self):
        """An Event file in `outbox/` that cannot be parsed is never sent and
        never deleted (`drain()`'s `unreadable`). That Desktop must not
        advance its collection date — and must not stop the other three."""
        self._signals_everywhere()

        outbox = self.desktop_dir("DESKTOP_2") / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / "corrupt.json").write_text("{not an event", encoding="utf-8")

        broken = self.run_agent("DESKTOP_2", now=self.AGENT_AT, start_date=self.DAY)
        self._run_the_healthy_three()

        # Contained: the run stops before any date is even considered — the
        # outbox is drained first, and a non-clear drain means "do not
        # advance past work still sitting locally". So no date closed, no
        # date was attempted, and the file is still there for a human.
        self.assertEqual(broken.status, AgentStatus.FAILED)
        self.assertEqual(broken.dates, ())
        self.assertIsNone(broken.last_successful_collection_date)
        self.assertEqual(len(broken.drain_summaries[0].unreadable), 1)
        self.assertTrue((outbox / "corrupt.json").exists())

        _, collector, _, backup, _ = self.deliver_and_collect(
            now=self.RUN_AT, history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted, 3)
        self.assertEqual(collector.failed, 0)
        self.assertEqual(backup.final_status, BackupStatus.SUCCESS)
        self._assert_the_healthy_three_are_in_history()

    def test_a_network_failure_on_one_desktop_holds_back_only_that_desktop(self):
        """Desktop 2's OneDrive folder is unreachable. Its Event stays in its
        own outbox; nothing about the other three changes."""
        self._signals_everywhere()

        failing = _FailingTransport()
        broken = self.run_agent(
            "DESKTOP_2", now=self.AGENT_AT, start_date=self.DAY, transport=failing
        )
        self._run_the_healthy_three()

        self.assertGreater(failing.attempts, 0)
        self.assertEqual(broken.dates[0].outcome, DateOutcome.FAILED)
        self.assertIsNone(broken.last_successful_collection_date)
        self.assertEqual(
            len(list((self.desktop_dir("DESKTOP_2") / "outbox").glob("*.json"))), 1
        )

        _, collector, _, _, _ = self.deliver_and_collect(
            now=self.RUN_AT, history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted, 3)
        self._assert_the_healthy_three_are_in_history()

    def test_the_network_failed_desktop_recovers_on_its_next_run(self):
        """Containment must not become loss: once the folder is back, the
        held Event is delivered and reaches the same Daily file as a Late
        Event."""
        self._signals_everywhere()
        self.run_agent(
            "DESKTOP_2",
            now=self.AGENT_AT,
            start_date=self.DAY,
            transport=_FailingTransport(),
        )
        self._run_the_healthy_three()
        self.deliver_and_collect(now=self.RUN_AT, history_start_date=self.DAY)
        self.assertNotIn("DESKTOP_2 work", self.daily(self.DAY))

        recovered = self.run_agent(
            "DESKTOP_2", now=datetime(2026, 8, 9, 15, 0), start_date=self.DAY
        )

        self.assertEqual(recovered.status, AgentStatus.COMPLETED)
        self.assertEqual(recovered.last_successful_collection_date, self.DAY)
        self.assertEqual(
            len(list((self.desktop_dir("DESKTOP_2") / "outbox").glob("*.json"))), 0
        )

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 16, 0), history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted, 1)
        self.assertIn("DESKTOP_2 work", self.daily(self.DAY))

    def test_a_rejected_signal_on_one_desktop_holds_back_only_that_signal(self):
        """A Signal the Agent refuses is quarantined on its own machine. The
        Desktop's *other* Signal for that date still ships, and the other
        three Desktops are untouched."""
        self._signals_everywhere()
        self.write_signal(
            "DESKTOP_2",
            self.DAY,
            "leaky",
            summary="token " + SECRET_PREFIX + "ABCDEFGHIJKLMNOP1234",
        )

        broken = self.run_agent("DESKTOP_2", now=self.AGENT_AT, start_date=self.DAY)
        self._run_the_healthy_three()

        self.assertEqual(broken.status, AgentStatus.COMPLETED)
        self.assertEqual(len(broken.dates[0].rejected_signals), 1)
        self.assertEqual(len(broken.dates[0].event_ids), 1)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=self.RUN_AT, history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted, 4)
        self.assertEqual(collector.rejected, 0)
        markdown = self.daily(self.DAY)
        self.assertIn("DESKTOP_2 work", markdown)
        self.assertNotIn(SECRET_PREFIX, markdown)

    def test_a_corrupt_state_file_on_one_desktop_holds_back_only_that_desktop(self):
        """The Agent refuses to run against a state it cannot trust — the
        same stance `scheduler` takes (BUG-3). It must refuse locally."""
        self._signals_everywhere()
        state_path = self.desktop_dir("DESKTOP_2") / "state" / "agent_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not json", encoding="utf-8")

        # `load_state()` refuses a state it cannot parse, and `run_once()`
        # lets that out rather than guessing — the same stance BUG-3 settled
        # for the Scheduler. `run_agent.py` turns it into exit 1 with a
        # message; what matters here is that it stays on this machine.
        with self.assertRaises(AgentStateError):
            self.run_agent("DESKTOP_2", now=self.AGENT_AT, start_date=self.DAY)
        self._run_the_healthy_three()

        # Nothing was deleted: the damaged file is still there for a human.
        self.assertEqual(state_path.read_text(encoding="utf-8"), "{not json")
        # And the refusing Agent released its lock, so it is not wedged.
        self.assertFalse(
            (self.desktop_dir("DESKTOP_2") / "locks" / "agent.lock").exists()
        )

        _, collector, _, _, _ = self.deliver_and_collect(
            now=self.RUN_AT, history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted, 3)
        self._assert_the_healthy_three_are_in_history()

    def test_a_lock_left_by_a_live_process_on_one_desktop_isolates_that_desktop(self):
        """Each Agent locks only its own machine. A Desktop whose previous
        run is still going skips this one; the others never notice."""
        self._signals_everywhere()
        lock_path = self.desktop_dir("DESKTOP_2") / "locks" / "agent.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps(
                {"process_id": os.getpid(), "created_at": self.AGENT_AT.isoformat()}
            ),
            encoding="utf-8",
        )

        broken = self.run_agent("DESKTOP_2", now=self.AGENT_AT, start_date=self.DAY)
        self._run_the_healthy_three()

        self.assertEqual(broken.status, AgentStatus.SKIPPED_ALREADY_RUNNING)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=self.RUN_AT, history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted, 3)
        self._assert_the_healthy_three_are_in_history()

    def test_every_desktop_keeps_its_own_state_when_one_of_them_fails(self):
        """The shared-nothing property stated as one assertion: a failure on
        Desktop 2 leaves the other three's collection dates exactly where a
        clean run would."""
        self._signals_everywhere()
        self.run_agent(
            "DESKTOP_2",
            now=self.AGENT_AT,
            start_date=self.DAY,
            transport=_FailingTransport(),
        )
        self._run_the_healthy_three()

        for desktop_id, _, _ in DESKTOPS:
            with self.subTest(desktop=desktop_id):
                state = self._agent_state(desktop_id)
                if desktop_id == "DESKTOP_2":
                    self.assertIsNone(state.last_successful_collection_date)
                else:
                    self.assertEqual(state.last_successful_collection_date, self.DAY)
                self.assertEqual(state.desktop_id, desktop_id)


class MultiDesktopDateEdgeTests(MultiDesktopTestCase):
    """Dates that are not "yesterday": the future, the distant past, and a
    Desktop whose clock disagrees with everyone else's."""

    def test_a_future_dated_signal_is_not_collected_and_affects_nobody(self):
        """docs/07 §18: a day still in progress is not a finished day, and a
        day that has not happened at all certainly is not. The Signal is
        left where it is — not rejected, not deleted, not sent — and no
        other Desktop is affected."""
        day = date(2026, 8, 8)
        future = date(2026, 12, 25)
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, day, "work")
        self.write_signal("DESKTOP_2", future, "christmas")

        for desktop_id, _, _ in DESKTOPS:
            result = self.run_agent(
                desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day
            )
            self.assertEqual(result.status, AgentStatus.COMPLETED, desktop_id)

        # Four Events, one per Desktop — the future Signal is not among them.
        self.assertEqual(len(list(self.cloud.glob("*.json"))), 4)
        future_signal = (
            self.desktop_dir("DESKTOP_2") / "signals" / future.isoformat() / "christmas.json"
        )
        self.assertTrue(future_signal.exists(), "a future Signal must not be consumed")

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=day
        )

        self.assertEqual(collector.accepted, 4)
        self.assertNotIn("christmas", self.daily(day))

    def test_a_desktop_ninety_days_behind_catches_up_without_touching_the_others(self):
        """The long-outage case: one machine switched off for a quarter,
        every other Desktop reporting daily throughout. Its catch-up must
        produce one Event per missed date, in order, and must not disturb
        the dates the others already closed."""
        start = date(2026, 5, 1)
        recent = date(2026, 7, 29)
        now = datetime(2026, 7, 30, 9, 0)

        # Desktops 1/3/4 are up to date.
        for desktop_id, _, _ in DESKTOPS:
            if desktop_id == "DESKTOP_2":
                continue
            self.write_signal(desktop_id, recent, "work")
            state_path = self.desktop_dir(desktop_id) / "state" / "agent_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            save_state(
                state_path,
                AgentState(
                    desktop_id=desktop_id,
                    last_successful_collection_date=recent - timedelta(days=1),
                ),
            )
            self.run_agent(desktop_id, now=now, start_date=start)

        # Desktop 2 has been off since the start date and has one Signal
        # from early on plus one from last week.
        self.write_signal("DESKTOP_2", date(2026, 5, 2), "before-the-outage")
        self.write_signal("DESKTOP_2", date(2026, 7, 20), "just-before-return")

        caught_up = self.run_agent("DESKTOP_2", now=now, start_date=start)

        self.assertEqual(caught_up.status, AgentStatus.COMPLETED)
        self.assertEqual(caught_up.last_successful_collection_date, date(2026, 7, 29))
        # 90 calendar dates walked (05-01 .. 07-29), oldest first, no gaps.
        walked = [d.date for d in caught_up.dates]
        self.assertEqual(walked, sorted(walked))
        self.assertEqual(walked[0], start)
        self.assertEqual(walked[-1], date(2026, 7, 29))
        self.assertEqual(len(walked), 90)
        collected = [d for d in caught_up.dates if d.outcome is DateOutcome.COLLECTED]
        self.assertEqual([d.date for d in collected], [date(2026, 5, 2), date(2026, 7, 20)])

        # The three healthy Desktops still say exactly what they said before.
        for desktop_id, _, _ in DESKTOPS:
            if desktop_id == "DESKTOP_2":
                continue
            with self.subTest(desktop=desktop_id):
                state = load_state(
                    self.desktop_dir(desktop_id) / "state" / "agent_state.json"
                )
                self.assertEqual(state.last_successful_collection_date, recent)

    def test_a_desktop_whose_clock_runs_ahead_does_not_skew_the_others(self):
        """Clock skew between Desktops is real (four machines, no NTP
        guarantee). An Agent run with a `now` a day ahead collects one extra
        date — its own — and every other Desktop's dates are unchanged."""
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, day, "work")
            self.write_signal(desktop_id, date(2026, 8, 9), "next-day")

        ahead = self.run_agent(
            "DESKTOP_2", now=datetime(2026, 8, 10, 9, 0), start_date=day
        )
        for desktop_id, _, _ in DESKTOPS:
            if desktop_id == "DESKTOP_2":
                continue
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)

        self.assertEqual(ahead.last_successful_collection_date, date(2026, 8, 9))
        for desktop_id, _, _ in DESKTOPS:
            if desktop_id == "DESKTOP_2":
                continue
            with self.subTest(desktop=desktop_id):
                state = load_state(
                    self.desktop_dir(desktop_id) / "state" / "agent_state.json"
                )
                self.assertEqual(state.last_successful_collection_date, day)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 10, 11, 0), history_start_date=day
        )

        # 4 for 08-08 plus Desktop 2's extra 08-09 — no loss, no duplicate.
        self.assertEqual(collector.accepted, 5)
        self.assertEqual(collector.duplicate, 0)
        self.assertIn("DESKTOP_2 next-day", self.daily(date(2026, 8, 9)))


class RepeatedDeliveryIsolationTests(MultiDesktopTestCase):
    """Re-sending the same work, from the angles the existing duplicate
    tests do not cover."""

    def test_resending_after_desktop4_consumed_the_file_creates_no_duplicate(self):
        """Desktop 4 moves a collected file out of the cloud folder's mirror,
        so a re-sending Agent finds the destination absent and writes it
        again. The Collector's seen-store is the layer that must catch
        it — and it must not disturb the other Desktops' Events in the same
        batch."""
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, day, "work")
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)

        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        # Desktop 2 re-sends: clear its `sent/` bookkeeping and re-run, the
        # shape a restored-from-backup Agent directory produces.
        sent_dir = self.desktop_dir("DESKTOP_2") / "sent"
        for path in sent_dir.glob("*.json"):
            path.unlink()
        state_path = self.desktop_dir("DESKTOP_2") / "state" / "agent_state.json"
        save_state(
            state_path,
            AgentState(desktop_id="DESKTOP_2", last_successful_collection_date=None),
        )

        again = self.run_agent("DESKTOP_2", now=datetime(2026, 8, 9, 13, 0), start_date=day)
        self.assertEqual(again.status, AgentStatus.COMPLETED)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 14, 0), history_start_date=day
        )

        self.assertEqual(collector.accepted, 0)
        self.assertEqual(collector.rejected, 0)
        self.assertEqual(collector.failed, 0)

        # Still one entry per Desktop. Counted by Event ID rather than by
        # summary text: docs/06's template prints a summary twice by design
        # (once under Summary, once under Milestones), so counting the text
        # would assert the template rather than the deduplication.
        markdown = self.daily(day)
        self.assertEqual(markdown.count("- Event ID:"), 4)
        self.assertIn("Event Count: 4", markdown)

    def test_two_desktops_resending_at_once_stay_independent(self):
        day = date(2026, 8, 8)
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, day, "work")
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 9, 0), start_date=day)
        self.deliver_and_collect(now=datetime(2026, 8, 9, 11, 0), history_start_date=day)

        for desktop_id in ("DESKTOP_1", "DESKTOP_3"):
            for path in (self.desktop_dir(desktop_id) / "sent").glob("*.json"):
                path.unlink()
            save_state(
                self.desktop_dir(desktop_id) / "state" / "agent_state.json",
                AgentState(desktop_id=desktop_id, last_successful_collection_date=None),
            )
            self.run_agent(desktop_id, now=datetime(2026, 8, 9, 13, 0), start_date=day)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 14, 0), history_start_date=day
        )

        self.assertEqual(collector.accepted, 0)
        markdown = self.daily(day)
        self.assertEqual(markdown.count("- Event ID:"), 4)
        self.assertIn("Event Count: 4", markdown)
        for desktop_id, _, _ in DESKTOPS:
            with self.subTest(desktop=desktop_id):
                self.assertIn(f"{desktop_id} work", markdown)


class CrashPointRecoveryTests(MultiDesktopTestCase):
    """A crash at each point in the Agent's commit sequence, replayed through
    the real Desktop 4 pipeline.

    `agent/outbox.py` writes the Event, sends it, then files it — three
    steps, so two windows:

        outbox/ written, send not yet made      already covered
                                                (test_agent.py::CrashRecoveryTests)
        send returned, sent/ move not yet made  <- here
        sent/ filed, state not yet saved        <- here

    The last two are covered as unit facts (`drain()` re-sends, `is_sent()`
    skips) but the *claim* the outbox docstring makes is about what happens
    downstream: "a duplicate delivery costs one redundant file copy and
    produces no duplicate History and no duplicate Notion write". That
    sentence can only be checked with the Collector, the History Filter, the
    Daily renderer and Notion all actually running, which is what these do.

    Each crash is reconstructed as the on-disk state it would leave, not
    simulated with a mock — that is the only form the next run can tell
    apart, and it is exactly what a killed process leaves behind.
    """

    DAY = date(2026, 8, 8)

    def _deliver_everyone(self, *, now=datetime(2026, 8, 9, 9, 0)):
        for desktop_id, _, _ in DESKTOPS:
            self.write_signal(desktop_id, self.DAY, "work")
            self.run_agent(desktop_id, now=now, start_date=self.DAY)

    def _history_entries(self):
        markdown = self.daily(self.DAY)
        return markdown.count("- Event ID:")

    def test_a_crash_between_send_and_filing_produces_no_duplicate_history(self):
        """The Event reached the cloud; the local bookkeeping move did not.
        The next run re-sends, and every dedup layer below has to absorb
        it."""
        self._deliver_everyone()

        # The crash: the Event is back in outbox/ as if `os.replace` never ran.
        agent_dir = self.desktop_dir("DESKTOP_2")
        sent_files = list((agent_dir / "sent").glob("*.json"))
        self.assertEqual(len(sent_files), 1)
        shutil.move(str(sent_files[0]), str(agent_dir / "outbox" / sent_files[0].name))

        replay = self.run_agent(
            "DESKTOP_2", now=datetime(2026, 8, 9, 10, 0), start_date=self.DAY
        )
        self.assertEqual(replay.status, AgentStatus.COMPLETED)
        # It really was sent a second time — this is not a no-op test.
        self.assertEqual(len(replay.drain_summaries[0].sent), 1)

        _, collector, _, backup, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=self.DAY
        )

        # Desktop 4 sees four Events, one of them arriving twice.
        self.assertEqual(collector.accepted + collector.duplicate, 4)
        self.assertEqual(collector.failed, 0)
        self.assertEqual(self._history_entries(), 4)
        self.assertEqual(backup.final_status, BackupStatus.SUCCESS)

    def test_a_crash_before_the_state_save_re_reads_the_date_without_resending(self):
        """The Event is filed as sent but the date never closed. The next run
        walks that date again and must recognise its own work — `is_sent()`
        is the layer that stops a second Event being created at all."""
        self._deliver_everyone()

        state_path = self.desktop_dir("DESKTOP_2") / "state" / "agent_state.json"
        save_state(
            state_path,
            AgentState(desktop_id="DESKTOP_2", last_successful_collection_date=None),
        )

        replay = self.run_agent(
            "DESKTOP_2", now=datetime(2026, 8, 9, 10, 0), start_date=self.DAY
        )

        self.assertEqual(replay.status, AgentStatus.COMPLETED)
        day_result = next(d for d in replay.dates if d.date == self.DAY)
        self.assertEqual(len(day_result.already_sent), 1)
        self.assertEqual(day_result.event_ids, ())
        # Nothing new reached the cloud, so nothing new can reach History.
        self.assertEqual(len(list(self.cloud.glob("*.json"))), 4)

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=self.DAY
        )
        self.assertEqual(collector.accepted, 4)
        self.assertEqual(self._history_entries(), 4)

    def test_a_crash_after_delivery_but_before_desktop4_collected_loses_nothing(self):
        """The other side of the same window: Desktop 4 dies after intake
        promoted the files but before the Collector ran. The files are in
        `incoming/`, and the next run picks them up."""
        self._deliver_everyone()
        self._sync_cloud()
        self._age_transport_files()

        # Intake only, then "crash" — no Collector, no History.
        from transport import run_intake

        intake = run_intake(
            transport_dir=self.d4_transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
        )
        self.assertEqual(len(intake.moved), 4)
        self.assertEqual(len(list(self.incoming_dir.glob("*.json"))), 4)

        _, collector, _, _, _ = self.run_company_ops(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted, 4)
        self.assertEqual(self._history_entries(), 4)

    def test_replaying_every_desktop_at_once_still_yields_one_entry_each(self):
        """The compound case: all four Desktops crashed in the same window
        and all four re-send together."""
        self._deliver_everyone()

        for desktop_id, _, _ in DESKTOPS:
            agent_dir = self.desktop_dir(desktop_id)
            for path in list((agent_dir / "sent").glob("*.json")):
                shutil.move(str(path), str(agent_dir / "outbox" / path.name))
            self.run_agent(
                desktop_id, now=datetime(2026, 8, 9, 10, 0), start_date=self.DAY
            )

        _, collector, _, _, _ = self.deliver_and_collect(
            now=datetime(2026, 8, 9, 11, 0), history_start_date=self.DAY
        )

        self.assertEqual(collector.accepted + collector.duplicate, 4)
        self.assertEqual(self._history_entries(), 4)
        for desktop_id, _, _ in DESKTOPS:
            with self.subTest(desktop=desktop_id):
                self.assertIn(f"{desktop_id} work", self.daily(self.DAY))


if __name__ == "__main__":
    unittest.main()
