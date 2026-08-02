"""Phase 4.95 — End-to-End Local Validation.

Wires every real component built so far into one pipeline run, entirely
inside a temp directory: no network, no GitHub, no Backup, no Transport.

    Reporter.report_and_write()   (Event, written locally — Transport never
                                    invoked)
        -> runtime/events/incoming/
    collector.runtime.run_once()  (Collector Runtime Core)
        -> runtime/events/processed/
    Event.from_json() on the processed file
        -> HistoryFilter.evaluate()
        -> FileHistoryRepository.save()
    review_cli.run_interactive_review()  (History Review CLI, canned input)
        -> RepositoryHistoryReviewer.submit_review()
    daily.generate_daily_history()  (standalone Daily Generator call)
        -> runtime/daily/YYYY-MM-DD.md
    scheduler.run_once()  (Scheduler / Catch-up Core)
        -> adopts the already-generated date, catches up the rest
"""

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import review_cli  # noqa: E402
from collector import Collector, InMemorySeenEventStore, run_once as collector_run_once  # noqa: E402
from daily import generate_daily_history  # noqa: E402
from events import Event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryDecision,
    HistoryFilter,
    RepositoryHistoryReviewer,
)
from reporter import DesktopProfile, Reporter  # noqa: E402
from scheduler import SchedulerStatus, load_state, run_once as scheduler_run_once  # noqa: E402


class EndToEndLocalPipelineTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.incoming_dir = self.root / "runtime" / "events" / "incoming"
        self.processed_dir = self.root / "runtime" / "events" / "processed"
        self.rejected_dir = self.root / "runtime" / "events" / "rejected"
        self.collector_log_path = self.root / "runtime" / "logs" / "collector.log"
        self.keep_dir = self.root / "runtime" / "history_candidates" / "keep"
        self.review_dir = self.root / "runtime" / "history_candidates" / "review"
        self.daily_dir = self.root / "runtime" / "daily"
        self.scheduler_state_path = self.root / "runtime" / "state" / "daily_history_state.json"
        self.scheduler_lock_path = self.root / "runtime" / "locks" / "company_ops.lock"

        self.repository = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)
        self.history_filter = HistoryFilter()
        self.collector = Collector(seen_store=InMemorySeenEventStore())

    # -- pipeline steps, each using real component code -----------------

    def _create_event_locally(self, reporter: Reporter, **report_kwargs) -> Path:
        _, path = reporter.report_and_write(directory=self.incoming_dir, **report_kwargs)
        return path

    def _run_collector(self):
        return collector_run_once(
            collector=self.collector,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            log_path=self.collector_log_path,
        )

    def _load_processed_event(self, event_id: str) -> Event:
        path = self.processed_dir / f"{event_id}.json"
        return Event.from_json(path.read_text(encoding="utf-8"))

    def _filter_and_store(self, event: Event) -> str:
        result = self.history_filter.evaluate(event)
        self.repository.save(result.candidate)
        return result.candidate.history_id

    # -- the actual end-to-end scenario ----------------------------------

    def test_full_pipeline_two_days_of_real_work(self):
        # Step 1: create test Events via Reporter (Transport never used)
        desktop3 = Reporter(profile="DESKTOP_3")
        milestone_path = self._create_event_locally(
            desktop3,
            event_id="E2E-MILESTONE-001",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            milestone="Search UI",
            summary="Search UI implementation completed",
            evidence=["TypeScript PASS"],
            history_candidate=True,
            timestamp="2026-08-01T20:00:00+09:00",
        )
        self.assertTrue(milestone_path.exists())

        coo_profile = DesktopProfile(name="DESKTOP_4", source="DESKTOP_4", role="COO")
        desktop4 = Reporter(profile=coo_profile)
        self._create_event_locally(
            desktop4,
            event_id="E2E-DECISION-001",
            project_id="DOJOONPASS_PRODUCT",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            milestone=None,
            summary="CEO approved Closed Beta scope",
            evidence=["CEO Approval"],
            history_candidate=True,
            timestamp="2026-08-02T10:00:00+09:00",
        )

        # Step 2: Collector Runtime processes both incoming Events
        collector_summary = self._run_collector()
        self.assertEqual(collector_summary.accepted, 2)
        self.assertEqual(collector_summary.rejected, 0)
        self.assertEqual(collector_summary.failed, 0)
        self.assertEqual(list(self.incoming_dir.glob("*.json")), [])

        milestone_event = self._load_processed_event("E2E-MILESTONE-001")
        decision_event = self._load_processed_event("E2E-DECISION-001")

        # Step 3 + 4: History Filter, then Repository storage
        milestone_history_id = self._filter_and_store(milestone_event)
        decision_history_id = self._filter_and_store(decision_event)

        stored_keep = self.repository.list(decision=HistoryDecision.KEEP)
        self.assertEqual(len(stored_keep), 2)

        # Step 5: Review CLI fills in Decision Context for both candidates.
        # FileHistoryRepository.list() returns candidates in filename
        # order, and history_id is "HIST-<event_id>", so
        # HIST-E2E-DECISION-001 (D) is visited before
        # HIST-E2E-MILESTONE-001 (M) — the canned input order below must
        # match that, not the order the Events were created in.
        reviewer = RepositoryHistoryReviewer(self.repository)
        canned_inputs = iter(
            [
                "",  # DECISION candidate: proceed
                "Beta 범위를 제한해 실제 사용자 검증을 우선하기로 결정",  # decision_context
                "초기 운영 리스크 감소",  # expected_outcome
                "",  # actual_outcome (blank = keep)
                "",  # lessons_learned (blank = keep)
                "",  # MILESTONE candidate: proceed
                "Search UI를 먼저 완성해 다른 팀의 통합 작업 블로커를 해소하기 위함",  # decision_context
                "다른 팀 통합 착수 가능",  # expected_outcome
                "",
                "",
            ]
        )
        review_cli.run_interactive_review(
            reviewer,
            input_fn=lambda prompt="": next(canned_inputs),
            print_fn=lambda *a, **k: None,
        )

        reviewed_milestone = self.repository.get(milestone_history_id)
        reviewed_decision = self.repository.get(decision_history_id)
        self.assertIn("블로커를 해소", reviewed_milestone.decision_context)
        self.assertIn("Beta 범위를 제한", reviewed_decision.decision_context)

        # Step 6: run the Daily Generator directly for 2026-08-01
        first_day_path = generate_daily_history(
            self.repository, date(2026, 8, 1), output_dir=self.daily_dir
        )
        self.assertTrue(first_day_path.exists())
        first_day_content = first_day_path.read_text(encoding="utf-8")
        self.assertIn("E2E-MILESTONE-001", first_day_content)
        self.assertNotIn("E2E-DECISION-001", first_day_content)
        self.assertIn("블로커를 해소", first_day_content)

        # Step 7: Scheduler run_once() — "now" is 2026-08-03, so it must
        # adopt the already-generated 08-01 (crash-recovery path) and
        # catch up the still-missing 08-02 on its own.
        result = scheduler_run_once(
            self.repository,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 8, 3, 11, 0),
            state_path=self.scheduler_state_path,
            lock_path=self.scheduler_lock_path,
            daily_output_dir=self.daily_dir,
        )
        self.assertEqual(result.status, SchedulerStatus.COMPLETED)
        # generated_dates lists every date now confirmed complete this run
        # — 08-01 was adopted (pre-existing file, not recreated; verified
        # below via untouched content) and 08-02 was freshly generated.
        self.assertEqual(result.generated_dates, (date(2026, 8, 1), date(2026, 8, 2)))

        state = load_state(self.scheduler_state_path)
        self.assertEqual(state.last_successful_daily_close, date(2026, 8, 2))

        # Step 8: validate the final Daily Markdown for both dates
        second_day_path = self.daily_dir / "2026-08-02.md"
        self.assertTrue(second_day_path.exists())
        second_day_content = second_day_path.read_text(encoding="utf-8")

        self.assertTrue(second_day_content.startswith("# DOJOONPASS Company History — 2026-08-02"))
        self.assertIn("E2E-DECISION-001", second_day_content)
        self.assertIn("CEO approved Closed Beta scope", second_day_content)
        self.assertIn("- Decision Context: Beta 범위를 제한해 실제 사용자 검증을 우선하기로 결정", second_day_content)
        self.assertIn("- Expected Outcome: 초기 운영 리스크 감소", second_day_content)
        self.assertIn("## Decisions", second_day_content)
        self.assertNotIn("E2E-MILESTONE-001", second_day_content)

        self.assertIn("## Milestones", first_day_content)
        self.assertIn("- Decision Context:", first_day_content)

        # original first-day file must not have been touched/regenerated
        # by the Scheduler's crash-recovery adoption path
        self.assertEqual(first_day_path.read_text(encoding="utf-8"), first_day_content)

        # no leftover temp files anywhere in the pipeline
        for directory in (
            self.processed_dir,
            self.keep_dir,
            self.review_dir,
            self.daily_dir,
            self.scheduler_state_path.parent,
        ):
            self.assertEqual(list(directory.glob(".tmp-*")), [])

    def test_rejected_event_never_reaches_history(self):
        desktop3 = Reporter(profile="DESKTOP_3")
        # write a structurally invalid Event directly into incoming/,
        # bypassing Reporter's own validation, to exercise Collector's
        # REJECTED path within the same local pipeline
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        (self.incoming_dir / "E2E-INVALID-001.json").write_text("{not valid json", encoding="utf-8")

        valid_path = self._create_event_locally(
            desktop3,
            event_id="E2E-VALID-001",
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            milestone="Search UI",
            summary="Search UI implementation started",
            evidence=[],
            history_candidate=False,
            timestamp="2026-08-01T09:00:00+09:00",
        )
        self.assertTrue(valid_path.exists())

        summary = self._run_collector()
        self.assertEqual(summary.rejected, 1)
        self.assertEqual(summary.accepted, 1)
        self.assertTrue((self.rejected_dir / "E2E-INVALID-001.json").exists())

        started_event = self._load_processed_event("E2E-VALID-001")
        result = self.history_filter.evaluate(started_event)
        self.assertEqual(result.decision, HistoryDecision.DROP)
        stored = self.repository.save(result.candidate)
        self.assertFalse(stored)
        self.assertEqual(self.repository.list(), [])


if __name__ == "__main__":
    unittest.main()
