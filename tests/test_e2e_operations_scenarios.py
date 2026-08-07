"""docs/10_E2E_OPERATIONS_SPEC.md Scenario Conformance Tests (Audit Sprint).

docs/10 numbers 25 E2E scenarios. The existing suite and this Sprint's
`test_e2e_disaster_scenarios.py` cover the destructive ones; this file covers
the operational ones that still had no automated check, including section 12's
own 11-item acceptance checklist for Scenario 1 — the spec's definition of
"a normal Event worked end to end".

Covered here:
    section 11-12 (Scenario 1)   정상 Event + 11개 검증 항목
    section 13    (Scenario 2)   중요하지 않은 Event -> DROP
    section 14    (Scenario 3)   Blocker
    section 15    (Scenario 4)   Blocker Resolution
    section 16    (Scenario 5)   CEO Decision
    section 20-22 (Scenario 8/9) Desktop 4 OFF, 여러 날
    section 40-41 (Scenario 25)  Reporter 미설치 Desktop / Partial Deployment
    section 59                   운영 중 확인해야 할 최소 상태 (observability)

Plus a characterization test for audit finding BUG-14 (a date-only
"Last Updated" from Notion breaks the Late Event guard).

Real filesystem and real git; InMemoryNotionTransport only (docs/10 section
10). Nothing here changes production code, Runtime behaviour, or any spec.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.runner import run_once  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from events import create_event  # noqa: E402
from history import HistoryDecision, HistoryFilter  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    InMemoryNotionTransport,
    NotionClient,
    SyncStatus,
)
from reporter import Reporter  # noqa: E402
from scheduler import SchedulerStatus  # noqa: E402


class OperationsScenarioTestCase(unittest.TestCase):
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
        self.logs_dir = self.root / "runtime" / "logs"
        self.collector_log_path = self.logs_dir / "collector.log"
        self.notion_sync_log_path = self.logs_dir / "notion_sync.log"
        self.collector_state_path = self.root / "runtime" / "state" / "collector_state.json"
        self.keep_dir = self.root / "runtime" / "history_candidates" / "keep"
        self.review_dir = self.root / "runtime" / "history_candidates" / "review"
        self.scheduler_state_path = self.root / "runtime" / "state" / "daily_history_state.json"
        self.backup_state_path = self.root / "runtime" / "state" / "backup_state.json"
        self.notion_retry_queue_path = self.root / "runtime" / "state" / "notion_retry_queue.json"

        self.notion_transport = InMemoryNotionTransport()
        self.notion_sync = ExecutionPlanSync(
            client=NotionClient(transport=self.notion_transport, database_id="DB-1")
        )

    def _run_git(self, args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _init_backup_git_repo(self, working_copy_dir: Path) -> None:
        self._run_git(["init", "--bare", "-b", "main", str(self.bare_remote_dir)], cwd=self.root)
        self._run_git(["init", "-b", "main"], cwd=working_copy_dir)
        self._run_git(["config", "user.email", "test@example.invalid"], cwd=working_copy_dir)
        self._run_git(["config", "user.name", "Ops Scenario Test"], cwd=working_copy_dir)
        self._run_git(["remote", "add", "origin", str(self.bare_remote_dir)], cwd=working_copy_dir)
        (working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        self._run_git(["add", "-A"], cwd=working_copy_dir)
        self._run_git(["commit", "-m", "init"], cwd=working_copy_dir)
        self._run_git(["push", "-u", "origin", "main"], cwd=working_copy_dir)

    def _deliver(self, *, profile="DESKTOP_3", **overrides):
        """Write one Event into incoming/ the way a Reporter on that Desktop
        would have produced it."""
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        data = dict(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="Search UI implementation completed.",
            milestone="Search UI",
            evidence=["TypeScript PASS"],
            history_candidate=True,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        data.update(overrides)
        _, path = Reporter(profile=profile).report_and_write(
            directory=self.incoming_dir, **data
        )
        return path

    def _run(self, *, now=None, history_start_date=date(2026, 8, 5), notion_sync=None):
        return run_once(
            local_master_dir=self.local_master_dir,
            backup_working_copy_dir=self.backup_working_copy_dir,
            history_start_date=history_start_date,
            runner_lock_path=self.runner_lock_path,
            now=now or datetime(2026, 8, 6, 11, 0).astimezone(),
            transport_dir=self.transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            collector_log_path=self.collector_log_path,
            collector_state_path=self.collector_state_path,
            notion_sync=notion_sync,
            notion_sync_log_path=self.notion_sync_log_path,
            notion_retry_queue_path=self.notion_retry_queue_path,
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
            scheduler_state_path=self.scheduler_state_path,
            backup_state_path=self.backup_state_path,
        )

    def _notion_row(self):
        pages = list(self.notion_transport._pages.values())
        self.assertEqual(len(pages), 1, "expected exactly one PROJECTS row")
        return pages[0]["properties"]

    def _daily(self, day="2026-08-05"):
        return (self.local_master_dir / "daily" / f"{day}.md").read_text(encoding="utf-8")


class Scenario1NormalEventTests(OperationsScenarioTestCase):
    """docs/10 sections 11-12. Section 12 lists 11 things to confirm; each is
    one assertion below, in the spec's own order."""

    def setUp(self):
        super().setUp()
        self._deliver(event_id="OPS-S1-001")
        self.result = self._run(notion_sync=self.notion_sync)
        self.candidate = json.loads(
            (self.keep_dir / "HIST-OPS-S1-001.json").read_text(encoding="utf-8")
        )
        self.row = self._notion_row()

    def test_01_event_id(self):
        self.assertEqual(self.candidate["event_id"], "OPS-S1-001")

    def test_02_timestamp(self):
        self.assertEqual(self.candidate["timestamp"], "2026-08-05T10:00:00+09:00")

    def test_03_project(self):
        self.assertEqual(
            self.row["Project ID"]["rich_text"][0]["text"]["content"], "SEARCH_FRONTEND"
        )
        self.assertEqual(
            self.row["Project"]["title"][0]["text"]["content"], "Search Frontend"
        )

    def test_04_owner(self):
        self.assertEqual(self.row["Owner"]["select"]["name"], "CTO Frontend")

    def test_05_event_type(self):
        self.assertEqual(
            self.row["Last Event Type"]["select"]["name"], "MILESTONE_COMPLETED"
        )

    def test_06_evidence(self):
        self.assertEqual(self.candidate["evidence"], ["TypeScript PASS"])

    def test_07_collector_accepted(self):
        self.assertEqual(self.result[1].accepted, 1)

    def test_08_no_duplicate(self):
        self.assertEqual(self.result[1].duplicate, 0)
        self.assertEqual(self.result[1].rejected, 0)
        self.assertEqual(self.result[1].failed, 0)

    def test_09_notion_reflected(self):
        self.assertEqual(self.result[4][0].status, SyncStatus.NOTION_CREATED)

    def test_10_history_reflected(self):
        self.assertEqual(self.candidate["filter_result"], "KEEP")

    def test_11_local_saved(self):
        self.assertIn("Search UI implementation completed.", self._daily())
        self.assertEqual(self.result[2].status, SchedulerStatus.COMPLETED)
        self.assertEqual(self.result[3].final_status, BackupStatus.SUCCESS)


class Scenario2TrivialEventTests(OperationsScenarioTestCase):
    """docs/10 section 13: 수집은 되지만 Company History에는 들어가지 않는다."""

    def test_trivial_event_is_collected_but_dropped(self):
        self._deliver(
            event_id="OPS-S2-001",
            event_type="STARTED",
            summary="CSS spacing 수정",
            milestone=None,
            evidence=[],
        )
        result = self._run()

        self.assertEqual(result[1].accepted, 1)
        self.assertEqual(list(self.keep_dir.glob("*.json")), [])
        self.assertEqual(list(self.review_dir.glob("*.json")), [])
        self.assertNotIn("CSS spacing", self._daily())


class Scenario3BlockerTests(OperationsScenarioTestCase):
    """docs/10 section 14: Collector -> Notion -> Current Blocker."""

    def test_blocker_reaches_the_notion_current_blocker_field(self):
        self._deliver(
            event_id="OPS-S3-001",
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="auction -> auction_item synchronization failure",
            summary="sync 실패",
            milestone=None,
            evidence=[],
        )
        self._run(notion_sync=self.notion_sync)

        row = self._notion_row()
        self.assertEqual(
            row["Blocker"]["rich_text"][0]["text"]["content"],
            "auction -> auction_item synchronization failure",
        )
        self.assertEqual(row["Status"]["select"]["name"], "BLOCKED")

    def test_blocker_is_routed_to_review_not_automatically_to_daily(self):
        """Characterization. Section 14 says a Blocker reaches Daily History
        "중요도 기준 충족 시", but no automatic importance rule exists:
        docs/05 section 24 classifies every BLOCKED event as REVIEW, so it
        waits for a human (review_cli) instead of appearing in Company
        History on its own.
        """
        self._deliver(
            event_id="OPS-S3-002",
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="sync failure",
            summary="blocked work",
            milestone=None,
            evidence=[],
        )
        self._run()

        self.assertTrue((self.review_dir / "HIST-OPS-S3-002.json").exists())
        self.assertEqual(list(self.keep_dir.glob("*.json")), [])
        self.assertNotIn("blocked work", self._daily())


class Scenario4BlockerResolutionTests(OperationsScenarioTestCase):
    """docs/10 section 15: Notion -> Resolved, History -> Resolution 기록."""

    def test_resolution_clears_the_notion_blocker_and_is_kept_in_history(self):
        self._deliver(
            event_id="OPS-S4-001",
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="sync failure",
            summary="blocked",
            milestone=None,
            evidence=[],
        )
        self._run(notion_sync=self.notion_sync)

        self._deliver(
            event_id="OPS-S4-002",
            event_type="ISSUE_RESOLVED",
            status="IN_PROGRESS",
            summary="sync 문제 해결",
            milestone=None,
            evidence=[],
            timestamp="2026-08-06T10:00:00+09:00",
        )
        self._run(
            now=datetime(2026, 8, 7, 11, 0).astimezone(), notion_sync=self.notion_sync
        )

        row = self._notion_row()
        self.assertEqual(row["Blocker"]["rich_text"], [])
        self.assertEqual(row["Status"]["select"]["name"], "IN_PROGRESS")
        self.assertTrue((self.keep_dir / "HIST-OPS-S4-002.json").exists())
        self.assertIn("sync 문제 해결", self._daily("2026-08-06"))

    def test_issue_resolved_while_still_blocked_keeps_the_blocker(self):
        """docs/04 section 27: 모든 ISSUE_RESOLVED가 상태를 바꾼다고 가정하지
        않고 Event의 실제 status를 따른다."""
        self._deliver(
            event_id="OPS-S4-003",
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="still broken",
            summary="blocked",
            milestone=None,
            evidence=[],
        )
        self._run(notion_sync=self.notion_sync)

        self._deliver(
            event_id="OPS-S4-004",
            event_type="ISSUE_RESOLVED",
            status="BLOCKED",
            summary="partial fix, still blocked",
            milestone=None,
            evidence=[],
            timestamp="2026-08-06T10:00:00+09:00",
        )
        self._run(
            now=datetime(2026, 8, 7, 11, 0).astimezone(), notion_sync=self.notion_sync
        )

        row = self._notion_row()
        self.assertEqual(
            row["Blocker"]["rich_text"][0]["text"]["content"], "still broken"
        )


class Scenario5CeoDecisionTests(OperationsScenarioTestCase):
    """docs/10 section 16: Current State 반영 + Daily History 반영."""

    def _deliver_coo_decision(self, **overrides):
        """A COO Decision cannot be produced through Reporter — see
        `ReporterProfileCoverageTests` below (audit finding GAP-10). The Event
        is therefore constructed directly, which is exactly what an operator
        would have to do today.
        """
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        data = dict(
            source="DESKTOP_4",
            role="COO",
            project_id="CLOSED_BETA",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            summary="Closed Beta Scope 확정.",
            history_candidate=True,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        data.update(overrides)
        event = create_event(**data)
        path = self.incoming_dir / f"{event.event_id}.json"
        path.write_text(event.to_json(), encoding="utf-8")
        return path

    def test_decision_is_kept_and_rendered_in_the_decisions_section(self):
        self._deliver_coo_decision(event_id="OPS-S5-001")
        result = self._run(notion_sync=self.notion_sync)

        candidate = json.loads(
            (self.keep_dir / "HIST-OPS-S5-001.json").read_text(encoding="utf-8")
        )
        self.assertEqual(candidate["category"], "DECISION")
        self.assertIn("## Decisions", self._daily())
        self.assertIn("Closed Beta Scope 확정.", self._daily())
        self.assertEqual(result[4][0].status, SyncStatus.NOTION_CREATED)


class ReporterProfileCoverageTests(unittest.TestCase):
    """Audit finding GAP-10.

    events.schema allows source=DESKTOP_4 and role=COO, and docs/10 section 16
    (Scenario 5, CEO Decision) plus section 66 (COO 권한) both describe
    COO-originated Events. But reporter.profiles.PROFILES maps only
    DESKTOP_1/2/3, so there is no supported producer for a COO Event: it can
    only be created by calling events.create_event() directly and writing the
    JSON by hand.

    Recorded as characterization, not asserted as correct — README section 3
    does describe Desktop 4 as Company Ops' own host rather than a reporting
    Desktop, so a missing profile may well be intentional. What is not
    recorded anywhere is how a CEO Decision Event is meant to be produced.
    """

    def test_schema_allows_desktop4_and_coo(self):
        from events import ROLES, SOURCES

        self.assertIn("DESKTOP_4", SOURCES)
        self.assertIn("COO", ROLES)

    def test_reporter_has_no_profile_for_desktop4_or_coo(self):
        from reporter.profiles import PROFILES, ReporterConfigError, resolve_profile

        self.assertEqual(sorted(PROFILES), ["DESKTOP_1", "DESKTOP_2", "DESKTOP_3"])
        self.assertNotIn("COO", {p.role for p in PROFILES.values()})
        with self.assertRaises(ReporterConfigError):
            resolve_profile("DESKTOP_4")

    def test_a_coo_decision_event_is_still_schema_valid_when_built_directly(self):
        from events import validate_event

        event = create_event(
            source="DESKTOP_4",
            role="COO",
            project_id="CLOSED_BETA",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            summary="Closed Beta Scope 확정.",
            history_candidate=True,
            event_id="GAP10-001",
            timestamp="2026-08-05T10:00:00+09:00",
        )
        self.assertEqual(validate_event(event.to_dict()), [])


class Scenario8And9Desktop4OffTests(OperationsScenarioTestCase):
    """docs/10 sections 20-22: Desktop 4가 여러 날 꺼져 있어도 Event와 History가
    손실되지 않는다 (README RULE 7)."""

    def test_events_waiting_while_desktop4_was_off_are_all_collected(self):
        for day in range(5, 10):
            self._deliver(
                event_id=f"OPS-OFF-{day:02d}",
                summary=f"work on day {day}",
                timestamp=f"2026-08-0{day}T10:00:00+09:00",
            )

        result = self._run(now=datetime(2026, 8, 10, 11, 0).astimezone())

        self.assertEqual(result[1].accepted, 5)
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 5)

    def test_missing_daily_history_is_caught_up_in_order_and_never_includes_today(self):
        for day in range(5, 10):
            self._deliver(
                event_id=f"OPS-OFF2-{day:02d}",
                summary=f"work on day {day}",
                timestamp=f"2026-08-0{day}T10:00:00+09:00",
            )

        result = self._run(now=datetime(2026, 8, 10, 11, 0).astimezone())

        generated = [d.isoformat() for d in result[2].generated_dates]
        self.assertEqual(
            generated,
            ["2026-08-05", "2026-08-06", "2026-08-07", "2026-08-08", "2026-08-09"],
        )
        self.assertNotIn("2026-08-10", generated)
        for day in range(5, 10):
            self.assertIn(f"work on day {day}", self._daily(f"2026-08-0{day}"))

    def test_state_records_the_last_closed_day_only(self):
        self._deliver(event_id="OPS-OFF3-001")
        self._run(now=datetime(2026, 8, 10, 11, 0).astimezone())

        state = json.loads(self.scheduler_state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["last_successful_daily_close"], "2026-08-09")


class Scenario25PartialDeploymentTests(OperationsScenarioTestCase):
    """docs/10 sections 40-41: Reporter가 설치되지 않은 Desktop이 있어도
    Partial Deployment는 허용된다."""

    def test_events_from_only_some_desktops_are_processed_normally(self):
        self._deliver(profile="DESKTOP_3", event_id="OPS-S25-D3", project_id="SEARCH_FRONTEND")
        self._deliver(profile="DESKTOP_1", event_id="OPS-S25-D1", project_id="CRAWLING")

        result = self._run()

        self.assertEqual(result[1].accepted, 2)
        self.assertEqual(result[1].failed, 0)
        # Nothing anywhere expects DESKTOP_2 / DESKTOP_4 to have reported.
        self.assertEqual(result[2].status, SchedulerStatus.COMPLETED)
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)

    def test_each_desktop_maps_to_its_own_source_and_role(self):
        self._deliver(profile="DESKTOP_1", event_id="OPS-S25-A", project_id="CRAWLING")
        self._run(notion_sync=self.notion_sync)

        row = self._notion_row()
        self.assertEqual(row["Source"]["select"]["name"], "DESKTOP_1")
        self.assertEqual(row["Owner"]["select"]["name"], "CTO Backend")


class OperationalObservabilityTests(OperationsScenarioTestCase):
    """docs/10 section 59: 운영 중 확인해야 할 최소 상태.

    Characterization of audit finding GAP-6: of the Runner's ten stages, only
    Collector and Notion Sync leave a durable on-disk trace. Scheduler,
    Backup, and Transport results exist only as return values, so after a run
    ends there is nothing on disk to reconstruct what they did.
    """

    def test_state_files_expose_the_minimum_operational_status(self):
        self._deliver(event_id="OPS-OBS-001")
        self._run(notion_sync=self.notion_sync)

        collector_state = json.loads(self.collector_state_path.read_text(encoding="utf-8"))
        scheduler_state = json.loads(self.scheduler_state_path.read_text(encoding="utf-8"))
        backup_state = json.loads(self.backup_state_path.read_text(encoding="utf-8"))

        self.assertIn("OPS-OBS-001", collector_state["processed_event_ids"])
        self.assertIsNotNone(collector_state["last_run"])
        self.assertEqual(scheduler_state["last_successful_daily_close"], "2026-08-05")
        self.assertEqual(backup_state["backup_status"], BackupStatus.SUCCESS.value)
        self.assertIsNotNone(backup_state["last_backup_commit"])

    def test_only_collector_and_notion_sync_write_log_files(self):
        self._deliver(event_id="OPS-OBS-002")
        self._run(notion_sync=self.notion_sync)

        written = sorted(p.name for p in self.logs_dir.glob("*.log"))
        self.assertEqual(written, ["collector.log", "notion_sync.log"])
        # No scheduler.log / backup.log / runner.log exists to write to.
        self.assertFalse((self.logs_dir / "scheduler.log").exists())
        self.assertFalse((self.logs_dir / "backup.log").exists())
        self.assertFalse((self.logs_dir / "runner.log").exists())

    def test_notion_sync_log_records_the_minimum_fields_and_no_secrets(self):
        """docs/04 sections 55-56."""
        self._deliver(event_id="OPS-OBS-003")
        self._run(notion_sync=self.notion_sync)

        line = self.notion_sync_log_path.read_text(encoding="utf-8").strip()
        self.assertIn("EVENT OPS-OBS-003", line)
        self.assertIn("PROJECT SEARCH_FRONTEND", line)
        self.assertIn("NOTION_RESULT NOTION_CREATED", line)
        for secret_marker in ("Bearer", "ntn_", "secret_", "NOTION_API_TOKEN"):
            self.assertNotIn(secret_marker, line)


class LateEventGuardCharacterizationTests(unittest.TestCase):
    """Audit finding BUG-14.

    docs/04 sections 29-30's Late Event guard compares
    `datetime.fromisoformat(event.timestamp)` with the stored "Last Updated".
    Notion's date property can legitimately hold a date-only value
    ("2026-08-05"), which parses to a naive datetime — comparing it with the
    Event's timezone-aware timestamp raises TypeError.

    ExecutionPlanSync.sync() only catches NotionAPIError, so the TypeError
    escapes. app/runner.py's broad handler then records NOTION_FAILED and
    enqueues the event, which puts it in the same unbounded retry loop as
    BUG-13 — for a condition that retrying can never resolve.
    """

    def setUp(self):
        self.transport = InMemoryNotionTransport()
        self.client = NotionClient(transport=self.transport, database_id="DB-1")
        self.sync = ExecutionPlanSync(client=self.client)

    def _event(self, event_id, timestamp):
        return create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="PRJ-LATE-GUARD",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="late guard probe",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp=timestamp,
        )

    def test_guard_works_when_last_updated_carries_an_offset(self):
        self.sync.sync(self._event("LG-001", "2026-08-05T10:00:00+09:00"))

        older = self.sync.sync(self._event("LG-002", "2026-08-04T10:00:00+09:00"))
        self.assertIs(older.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

        newer = self.sync.sync(self._event("LG-003", "2026-08-06T10:00:00+09:00"))
        self.assertIs(newer.status, SyncStatus.NOTION_UPDATED)

    def test_date_only_last_updated_raises_a_typeerror_out_of_sync(self):
        self.sync.sync(self._event("LG-004", "2026-08-05T10:00:00+09:00"))

        page = list(self.transport._pages.values())[0]
        page["properties"]["Last Updated"] = {"date": {"start": "2026-08-05"}}

        with self.assertRaises(TypeError):
            self.sync.sync(self._event("LG-005", "2026-08-06T10:00:00+09:00"))

    def test_sync_only_guards_against_notion_api_error(self):
        """The structural reason the TypeError escapes."""
        import inspect

        source = inspect.getsource(ExecutionPlanSync.sync)
        self.assertIn("except NotionAPIError", source)
        self.assertNotIn("except Exception", source)


if __name__ == "__main__":
    unittest.main()
