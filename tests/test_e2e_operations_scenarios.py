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
    NotionAPIError,
    NotionClient,
    SyncStatus,
)
from reporter import Reporter  # noqa: E402
from scheduler import SchedulerStatus  # noqa: E402



def _rmtree_ignoring_readonly(target) -> None:
    """`shutil.rmtree`, retrying a read-only entry after clearing the bit.

    Needed at all because these fixtures contain real git repositories, and
    git leaves `.git/objects` entries read-only on Windows — a plain
    `rmtree` of one raises `PermissionError`.

    Needed **here**, once, because the keyword that does it changed name:
    `onexc=` (callback takes the exception) is Python 3.12+, and `onerror=`
    (callback takes an `exc_info` triple) is what every earlier version has.
    On the interpreter this was written for — Anaconda 3.9.7, the
    deployment runtime at the time — `onexc=` is not an ignored keyword but
    a `TypeError`, raised inside `tearDownClass`, which pytest reports as an
    ERROR against **every test in the class**. (The current runtime is
    3.13.14, BACKLOG D, so it is the `onerror=` half that would now be the
    `TypeError`. The dispatch below is written for both and needs no edit;
    this note exists so the paragraph is not read as a claim about today's
    machine.)

    Measured at HEAD 43771a9: three teardowns, ten errors, not one of them
    about the code under test. Three copies of the callback existed and all
    three had the same bug, which is why this is one function now.
    """
    import shutil
    import stat as _stat

    def _retry(func, path, _exc):
        try:
            Path(path).chmod(_stat.S_IWRITE)
            func(path)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(target, onexc=_retry)
    else:
        shutil.rmtree(
            target, onerror=lambda func, path, info: _retry(func, path, info[1])
        )


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
            late_update_log_path=self.logs_dir / "daily_late_update.log",
            monthly_state_path=self.logs_dir / "monthly_history_state.json",
            run_summary_path=self.logs_dir / "last_run.json",
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
    """Audit finding GAP-10, now closed.

    events.schema allows source=DESKTOP_4 and role=COO, and docs/10 section 16
    (Scenario 5, CEO Decision) plus section 66 (COO 권한) both describe
    COO-originated Events. reporter.profiles.PROFILES nevertheless mapped only
    DESKTOP_1/2/3, so there was no supported producer for a COO Event: it
    could only be created by calling events.create_event() directly and
    writing the JSON by hand.

    The Multi-Desktop Agent made that gap operational rather than theoretical
    — the COO Desktop runs the same Agent as every other Desktop, and without
    a profile it could not report its own execution work at all. The profile
    was therefore added, pairing two values docs/02 §8/§9 already allow. No
    new source, role, or Event type exists as a result.
    """

    def test_schema_allows_desktop4_and_coo(self):
        from events import ROLES, SOURCES

        self.assertIn("DESKTOP_4", SOURCES)
        self.assertIn("COO", ROLES)

    def test_every_schema_source_has_exactly_one_reporter_profile(self):
        from events import ROLES, SOURCES
        from reporter.profiles import PROFILES, resolve_profile

        self.assertEqual(sorted(PROFILES), sorted(SOURCES))
        for name, profile in PROFILES.items():
            with self.subTest(profile=name):
                self.assertEqual(profile.name, name)
                self.assertIn(profile.source, SOURCES)
                self.assertIn(profile.role, ROLES)

        coo = resolve_profile("DESKTOP_4")
        self.assertEqual(coo.source, "DESKTOP_4")
        self.assertEqual(coo.role, "COO")

    def test_an_unknown_profile_name_is_still_refused(self):
        from reporter.profiles import ReporterConfigError, resolve_profile

        with self.assertRaises(ReporterConfigError):
            resolve_profile("DESKTOP_5")

    def test_a_coo_decision_event_can_now_be_produced_through_reporter(self):
        """The gap's actual consequence: producing a COO Event no longer
        requires bypassing Reporter and hand-writing the identity fields."""
        from events import validate_event
        from reporter import Reporter

        event = Reporter(profile="DESKTOP_4").report(
            project_id="CLOSED_BETA",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            summary="Closed Beta Scope 확정.",
            history_candidate=True,
            event_id="GAP10-002",
            timestamp="2026-08-05T10:00:00+09:00",
        )
        self.assertEqual(event.source, "DESKTOP_4")
        self.assertEqual(event.role, "COO")
        self.assertEqual(validate_event(event.to_dict()), [])

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

    def test_a_failed_sync_logs_the_reason_without_leaking_a_credential(self):
        """§56 on the path that actually writes free-form text.

        The test above covers a *successful* sync, whose log line is built
        entirely from closed values (an event_id, a project_id, an enum) —
        there is nothing there that could carry a secret. A failed sync now
        appends ` REASON <text>`, and that text originates outside this
        system: it is the remote response body, truncated to 400 chars by
        `notion/transport.py::_error_detail()`.

        Normally that is Notion's own JSON, which cannot contain the token
        (the token travels in a *request* header). The case worth pinning is
        the one the transport already anticipates in its comments — a proxy
        or captive portal answering instead of Notion, which is free to echo
        request headers back. That is the only realistic way a credential
        could reach this log, so it is what the probe imitates.
        """
        token = "ntn_" + "A1b2C3d4E5f6G7h8"

        class ProxyEchoTransport(InMemoryNotionTransport):
            def query_database(self, database_id, filter_):
                raise NotionAPIError(
                    "Notion API returned 502: Bad Gateway | "
                    "<html><body>Proxy denied the upstream request<br>"
                    f"Authorization: Bearer {token}</body></html>",
                    status_code=502,
                )

        sync = ExecutionPlanSync(
            client=NotionClient(transport=ProxyEchoTransport(), database_id="DB-1")
        )
        self._deliver(event_id="OPS-OBS-004")
        self._run(notion_sync=sync)

        log = self.notion_sync_log_path.read_text(encoding="utf-8")

        # The diagnosis still reaches the operator — that is the whole point
        # of the REASON field, and a redaction that removed it would be a
        # different bug.
        self.assertIn("NOTION_RESULT NOTION_RETRY_REQUIRED", log)
        self.assertIn("502", log)

        # But the credential does not.
        self.assertNotIn(token, log)
        for secret_marker in ("ntn_", "Bearer", "secret_", "NOTION_API_TOKEN"):
            self.assertNotIn(secret_marker, log)


class ProductionEntrypointE2ETests(unittest.TestCase):
    """`python run_company_ops.py` — the command Windows Task Scheduler runs.

    Every other test in this repository calls `app.runner.run_once()` with
    all nineteen paths passed explicitly. That is the right way to test the
    pipeline and it is NOT what production does: production runs this script,
    which passes three paths and lets sixteen defaults come from six other
    modules' frozen `PROJECT_ROOT` constants (C34 §3). A line-coverage pass
    including the root scripts found `main()`'s body had never executed —
    the wiring between the two was covered by nothing.

    It cannot be exercised in place. `_one_runtime_root_or_refuse()` exists
    precisely to stop `RUNTIME_DIR` being rebound, because doing that once
    ran a REAL pipeline that advanced the live watermark past History it had
    written into a temp tree. So the whole repository is COPIED — `src/` and
    the script — and the copy is run as a subprocess. Both roots then move
    together, the guard passes for the right reason, and nothing outside the
    temp directory is touched.

    Notion is left unconfigured, which is also the only Notion state a test
    may create here: the alternative reaches a real workspace.
    """

    @classmethod
    def setUpClass(cls):
        import shutil

        cls._tmp = tempfile.TemporaryDirectory()
        cls.sandbox = Path(cls._tmp.name) / "repo"
        repo = Path(__file__).resolve().parents[1]
        shutil.copytree(
            repo / "src",
            cls.sandbox / "src",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copy2(repo / "run_company_ops.py", cls.sandbox / "run_company_ops.py")

        working_copy = cls.sandbox / "runtime" / "backup_working_copy"
        working_copy.mkdir(parents=True)
        bare = Path(cls._tmp.name) / "remote.git"
        cls._git(["init", "--bare", "-b", "main", str(bare)], Path(cls._tmp.name))
        cls._git(["init", "-b", "main"], working_copy)
        cls._git(["config", "user.email", "t@example.invalid"], working_copy)
        cls._git(["config", "user.name", "Entrypoint E2E"], working_copy)
        cls._git(["remote", "add", "origin", str(bare)], working_copy)
        (working_copy / ".gitkeep").write_text("", encoding="utf-8")
        cls._git(["add", "-A"], working_copy)
        cls._git(["commit", "-m", "init"], working_copy)
        cls._git(["push", "-u", "origin", "main"], working_copy)
        cls.bare = bare

        cls.first = cls._run_entrypoint()
        cls.second = cls._run_entrypoint()

    @classmethod
    def tearDownClass(cls):
        _rmtree_ignoring_readonly(cls._tmp.name)

    @classmethod
    def _git(cls, args, cwd):
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError("git %s: %s" % (" ".join(args), result.stderr))
        return result.stdout

    @classmethod
    def _environment(cls, *, start_date="2026-08-01"):
        """The scheduled task's environment, minus everything Notion.

        Unset rather than blanked: `NotionConfig.from_env()` distinguishes
        the two, and "never set" is the pre-Notion deployment this run is
        meant to be.
        """
        import os

        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("NOTION_")
        }
        env.pop("COMPANY_OPS_HISTORY_START_DATE", None)
        if start_date is not None:
            env["COMPANY_OPS_HISTORY_START_DATE"] = start_date
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    @classmethod
    def _run_entrypoint(cls, **kwargs):
        return subprocess.run(
            [sys.executable, "run_company_ops.py"],
            cwd=cls.sandbox,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=cls._environment(**kwargs),
            timeout=300,
        )

    def _scheduler_line(self, completed):
        return next(
            item
            for item in completed.stdout.splitlines()
            if item.startswith("Daily History (Scheduler):")
        )

    # ---- what the scheduled task actually gets --------------------------

    def test_the_scheduled_task_sees_exit_zero(self):
        self.assertEqual(self.first.returncode, 0, self.first.stderr)

    def test_an_unconfigured_notion_is_reported_as_information_not_failure(self):
        """README RULE 9 / docs/11 §18: Company History records before Notion
        exists, and the message must not read like a fault."""
        self.assertIn("[INFO]", self.first.stdout)
        self.assertNotIn("[FAILED]", self.first.stdout)
        self.assertNotIn("Traceback", self.first.stderr)

    def test_company_history_is_written_under_the_runtime_root(self):
        daily = sorted(
            path.name
            for path in (self.sandbox / "runtime" / "local_master" / "daily").glob("*.md")
        )

        self.assertIn("2026-08-01.md", daily)
        self.assertGreater(len(daily), 1)

    def test_the_state_files_land_in_the_same_tree_as_the_history(self):
        """C34 §3's whole point, asserted from the filesystem. Sixteen of the
        nineteen paths are module defaults; this is what proves they resolve
        to the copy's own runtime tree and not to the real repository's."""
        state = self.sandbox / "runtime" / "state"

        self.assertTrue((state / "daily_history_state.json").is_file())
        self.assertTrue((state / "backup_state.json").is_file())
        self.assertTrue((self.sandbox / "runtime" / "runs" / "last_run.json").is_file())

    def test_the_backup_reached_the_remote(self):
        listed = self._git(["ls-tree", "-r", "--name-only", "main"], self.bare).split()

        self.assertIn("daily/2026-08-01.md", listed)

    def test_the_scheduler_line_is_readable_and_counts_first(self):
        """AGENT.md §6a-3's instruction is "compare the two numbers", and
        this is the line it is about — printed by the real entrypoint rather
        than reconstructed."""
        line = self._scheduler_line(self.first)

        self.assertNotIn("datetime.date(", line)
        self.assertRegex(line, r"generated=\d+ \(2026-08-01,")

    def test_a_second_run_adds_nothing_and_still_exits_zero(self):
        """Idempotency, through the entrypoint rather than through
        `run_once()`. The second run closes no new dates and must not report
        a failure for having nothing to do."""
        self.assertEqual(self.second.returncode, 0, self.second.stderr)

        line = self._scheduler_line(self.second)

        self.assertIn("generated=0", line)

    def test_the_manifest_the_exit_code_came_from_is_on_disk(self):
        """docs/14 §3: the process's answer and the manifest's must be the
        same answer. The entrypoint derives its code from the manifest, so
        the two can only disagree if the manifest is missing."""
        manifest = json.loads(
            (self.sandbox / "runtime" / "runs" / "last_run.json").read_text(
                encoding="utf-8"
            )
        )
        names = {component["name"] for component in manifest["components"]}

        self.assertIn("backup", names)
        self.assertIn("daily", names)

    def test_the_missing_start_date_gate_stops_the_scheduled_task(self):
        """The one configuration error this entrypoint can hit, exercised as
        the scheduled task would: no run, exit 1, and the reason on stderr
        where a captured log keeps it."""
        result = self._run_entrypoint(start_date=None)

        self.assertEqual(result.returncode, 1)
        self.assertIn("COMPANY_OPS_HISTORY_START_DATE", result.stderr)


class RestoreThroughTheProductionEntrypointTests(unittest.TestCase):
    """docs/10 §45's restore, run the way an operator actually runs it.

    C39 measured this through `app.runner.run_once()` with all nineteen paths
    passed explicitly. That is not the command a restored Desktop 4 executes —
    Task Scheduler runs `run_company_ops.py`, which passes three and lets
    sixteen defaults come from six other modules (C34 §3). The restore had
    never been driven through it, so the one thing an operator does after
    losing the machine was covered by nothing end to end.

    The scenario is the real one and nothing is faked:

        run 1     an ordinary run builds Company History and pushes it
        disaster  the ENTIRE `runtime/` tree is deleted — History, state,
                  working copy, locks
        restore   `git clone` the backup remote, copy `daily/` back into
                  Local Master. That is docs/10 §45's procedure, and it
                  leaves the machine holding a complete Company History with
                  **no memory of having written it** (`runtime/state/` is
                  gone), which is the dangerous shape: a run that decided
                  those days were unwritten would overwrite real History
                  with empty days and push that to the only copy.
        run 2     the first run after the restore
        run 3     and the one after that, to show it settles

    Measured (C43): 17 restored files, **all 17 byte-identical afterwards**,
    none missing, no new file, watermark forward to the last restored day,
    remote unchanged, `generated=0 reused=17`, exit 0 throughout.
    """

    START_DATE = "2026-08-01"

    @classmethod
    def setUpClass(cls):
        import shutil

        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.sandbox = cls.tmp / "repo"
        repo = Path(__file__).resolve().parents[1]
        shutil.copytree(repo / "src", cls.sandbox / "src",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(repo / "run_company_ops.py", cls.sandbox / "run_company_ops.py")

        working_copy = cls.sandbox / "runtime" / "backup_working_copy"
        working_copy.mkdir(parents=True)
        cls.bare = cls.tmp / "remote.git"
        cls._git(["init", "--bare", "-b", "main", str(cls.bare)], cls.tmp)
        cls._init_working_copy(working_copy)
        (working_copy / ".gitkeep").write_text("", encoding="utf-8")
        cls._git(["add", "-A"], working_copy)
        cls._git(["commit", "-m", "init"], working_copy)
        cls._git(["push", "-u", "origin", "main"], working_copy)

        cls.first = cls._run()
        cls.daily_dir = cls.sandbox / "runtime" / "local_master" / "daily"
        cls.before = {p.name: cls._digest(p) for p in sorted(cls.daily_dir.glob("*.md"))}
        cls.remote_before = cls._remote_files()

        cls._lose_everything()
        cls._restore_from_the_remote()
        cls.restored_count = len(list(cls.daily_dir.glob("*.md")))
        cls.state_dir_after_restore = (cls.sandbox / "runtime" / "state").exists()

        cls.second = cls._run()
        cls.after = {p.name: cls._digest(p) for p in sorted(cls.daily_dir.glob("*.md"))}
        cls.third = cls._run()

    @classmethod
    def tearDownClass(cls):
        _rmtree_ignoring_readonly(cls._tmp.name)

    # -- scaffolding ------------------------------------------------------

    @classmethod
    def _git(cls, args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError("git %s: %s" % (" ".join(args), result.stderr))
        return result.stdout

    @classmethod
    def _init_working_copy(cls, path):
        cls._git(["init", "-b", "main"], path)
        cls._git(["config", "user.email", "t@example.invalid"], path)
        cls._git(["config", "user.name", "Restore E2E"], path)
        cls._git(["remote", "add", "origin", str(cls.bare)], path)

    @classmethod
    def _digest(cls, path):
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _remote_files(cls):
        return sorted(cls._git(["ls-tree", "-r", "--name-only", "main"], cls.bare).split())

    @classmethod
    def _run(cls):
        import os

        env = {k: v for k, v in os.environ.items() if not k.startswith("NOTION_")}
        env["COMPANY_OPS_HISTORY_START_DATE"] = cls.START_DATE
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([sys.executable, "run_company_ops.py"], cwd=cls.sandbox,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=300)

    @classmethod
    def _lose_everything(cls):
        _rmtree_ignoring_readonly(cls.sandbox / "runtime")

    @classmethod
    def _restore_from_the_remote(cls):
        import shutil

        working_copy = cls.sandbox / "runtime" / "backup_working_copy"
        working_copy.parent.mkdir(parents=True)
        cls._git(["clone", str(cls.bare), str(working_copy)], cls.tmp)
        cls._git(["config", "user.email", "t@example.invalid"], working_copy)
        cls._git(["config", "user.name", "Restore E2E"], working_copy)
        master = cls.sandbox / "runtime" / "local_master"
        master.mkdir(parents=True)
        shutil.copytree(working_copy / "daily", master / "daily")

    def _scheduler_line(self, completed):
        return next(item for item in completed.stdout.splitlines()
                    if item.startswith("Daily History (Scheduler):"))

    # -- the premise ------------------------------------------------------

    def test_the_restore_really_left_history_without_a_watermark(self):
        """If this stops holding, every assertion below is about a different
        situation than the one docs/10 §45 describes."""
        self.assertGreater(self.restored_count, 1)
        self.assertEqual(self.restored_count, len(self.before))
        self.assertFalse(self.state_dir_after_restore)

    # -- the property that matters ----------------------------------------

    def test_not_one_restored_byte_changes(self):
        changed = [n for n in self.before if self.after.get(n) != self.before[n]]

        self.assertEqual(changed, [], "restored Company History was rewritten")

    def test_nothing_restored_goes_missing(self):
        self.assertEqual(sorted(set(self.before) - set(self.after)), [])

    def test_no_empty_day_is_invented_for_a_restored_date(self):
        self.assertEqual(sorted(set(self.after) - set(self.before)), [])

    def test_the_run_reports_them_as_reused_not_generated(self):
        """AGENT.md §6a-3's instruction is "compare the two numbers", and a
        restore is the case it exists for: `reused` large, `generated` zero.
        The opposite would mean the pipeline was rebuilding History it cannot
        rebuild (Candidates are not in the backup, docs/08 §26)."""
        line = self._scheduler_line(self.second)

        self.assertIn("generated=0", line)
        self.assertRegex(line, r"reused=\d+ \(")
        self.assertNotIn("datetime.date(", line)

    def test_the_watermark_moves_to_the_last_restored_day(self):
        state = json.loads(
            (self.sandbox / "runtime" / "state" / "daily_history_state.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            state["last_successful_daily_close"],
            max(name[: -len(".md")] for name in self.after),
        )

    def test_the_remote_is_not_rewritten(self):
        self.assertEqual(self._remote_files(), self.remote_before)

    def test_the_restored_run_is_a_clean_success(self):
        self.assertEqual(self.second.returncode, 0, self.second.stderr)
        self.assertNotIn("Traceback", self.second.stderr)

    def test_and_it_settles(self):
        """The run after the restore closes nothing and still exits 0 — a
        restore that needed a second pass would be a restore that was not
        finished."""
        self.assertEqual(self.third.returncode, 0, self.third.stderr)
        self.assertIn("generated=0", self._scheduler_line(self.third))


class LateEventGuardCharacterizationTests(unittest.TestCase):
    """Audit finding BUG-14 / BUG-29 — FIXED in C32 §7. Was
    CHARACTERIZATION, now GUARANTEE.

    docs/04 sections 29-30's Late Event guard compares
    `datetime.fromisoformat(event.timestamp)` with the stored "Last Updated".
    Notion's date property can legitimately hold a date-only value
    ("2026-08-05") — that is what its date picker writes when no time is
    chosen — which parses to a naive datetime, and comparing it with the
    Event's timezone-aware timestamp raised TypeError.

    ExecutionPlanSync.sync() only catches NotionAPIError, so the TypeError
    escaped. app/runner.py's broad handler then recorded NOTION_FAILED and
    enqueued the event, which put it in the same unbounded retry loop as
    BUG-13 — for a condition that retrying can never resolve.

    `_as_comparable_timestamp()` now answers "can these two be compared?"
    *before* the comparison, and an unreadable stored value gets the same
    answer the already-existing `current_last_updated is None` branch gives:
    proceed, and say so on `SyncResult.error`. The structural half below is
    unchanged and still asserted — the fix is a parse check, not a wider
    `except`, which would also have hidden a genuine defect in the
    comparison itself.
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

    def test_a_date_only_last_updated_no_longer_escapes_sync(self):
        self.sync.sync(self._event("LG-004", "2026-08-05T10:00:00+09:00"))

        page = list(self.transport._pages.values())[0]
        page["properties"]["Last Updated"] = {"date": {"start": "2026-08-05"}}

        result = self.sync.sync(self._event("LG-005", "2026-08-06T10:00:00+09:00"))

        self.assertIs(result.status, SyncStatus.NOTION_UPDATED)
        self.assertIn("Late Event guard skipped", result.error or "")

    def test_the_event_does_not_land_in_the_unbounded_retry_loop(self):
        """BUG-14's actual damage. `NOTION_FAILED` carries retryability
        UNKNOWN and parks the Event in the retry queue, where it fails
        identically on every run because retrying cannot change a stored
        date."""
        self.sync.sync(self._event("LG-006", "2026-08-05T10:00:00+09:00"))
        page = list(self.transport._pages.values())[0]
        page["properties"]["Last Updated"] = {"date": {"start": "2026-08-05"}}

        result = self.sync.sync(self._event("LG-007", "2026-08-06T10:00:00+09:00"))

        from app.runner import _FAILED_SYNC_STATUSES

        self.assertNotIn(result.status, _FAILED_SYNC_STATUSES)

    def test_the_next_event_finds_a_comparable_value_again(self):
        """Self-healing, which is why proceeding beats refusing: the update
        writes a well-formed `Last Updated`, so the guard is back on for the
        Event after it."""
        self.sync.sync(self._event("LG-008", "2026-08-05T10:00:00+09:00"))
        page = list(self.transport._pages.values())[0]
        page["properties"]["Last Updated"] = {"date": {"start": "2026-08-05"}}
        self.sync.sync(self._event("LG-009", "2026-08-06T10:00:00+09:00"))

        late = self.sync.sync(self._event("LG-010", "2026-08-04T10:00:00+09:00"))

        self.assertIs(late.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        self.assertIsNone(late.error)

    def test_sync_only_guards_against_notion_api_error(self):
        """The structural reason the TypeError escapes."""
        import inspect

        source = inspect.getsource(ExecutionPlanSync.sync)
        self.assertIn("except NotionAPIError", source)
        self.assertNotIn("except Exception", source)


if __name__ == "__main__":
    unittest.main()
