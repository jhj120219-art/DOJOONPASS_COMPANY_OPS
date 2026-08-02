import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event, EventValidationError  # noqa: E402
from reporter import (  # noqa: E402
    DEFAULT_EVENT_OUTPUT_DIR,
    PROFILES,
    DesktopProfile,
    Reporter,
    ReporterConfigError,
    read_event_json,
    write_event_json,
)
from reporter.local_output import PROJECT_ROOT  # noqa: E402
from reporter.profiles import PROFILE_ENV_VAR  # noqa: E402


class ReporterProfileTests(unittest.TestCase):
    def test_desktop_1_profile_event(self):
        reporter = Reporter(profile="DESKTOP_1")
        event = reporter.report(
            project_id="AUCTION_CRAWLER",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Auction crawler batch job started",
            history_candidate=False,
        )
        self.assertEqual(event.source, "DESKTOP_1")
        self.assertEqual(event.role, "CTO_BACKEND")

    def test_desktop_2_profile_event(self):
        reporter = Reporter(profile="DESKTOP_2")
        event = reporter.report(
            project_id="CONTENT_OS",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Content OS campaign planning started",
            history_candidate=False,
        )
        self.assertEqual(event.source, "DESKTOP_2")
        self.assertEqual(event.role, "CMO")

    def test_desktop_3_profile_event(self):
        reporter = Reporter(profile="DESKTOP_3")
        event = reporter.report(
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Search UI implementation started",
            history_candidate=False,
        )
        self.assertEqual(event.source, "DESKTOP_3")
        self.assertEqual(event.role, "CTO_FRONTEND")

    def test_explicit_profile_object_accepted(self):
        custom = DesktopProfile(name="DESKTOP_3", source="DESKTOP_3", role="CTO_FRONTEND")
        reporter = Reporter(profile=custom)
        self.assertEqual(reporter.profile, PROFILES["DESKTOP_3"])

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ReporterConfigError):
            Reporter(profile="DESKTOP_9")

    def test_missing_profile_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PROFILE_ENV_VAR, None)
            with self.assertRaises(ReporterConfigError):
                Reporter(profile=None)

    def test_profile_from_env_var(self):
        with mock.patch.dict(os.environ, {PROFILE_ENV_VAR: "DESKTOP_2"}):
            reporter = Reporter()
            self.assertEqual(reporter.profile, PROFILES["DESKTOP_2"])


class ReporterEventGenerationTests(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter(profile="DESKTOP_3")

    def test_milestone_completed_event(self):
        event = self.reporter.report(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            milestone="Search UI",
            summary="Search UI implementation completed",
            evidence=["TypeScript PASS"],
            history_candidate=True,
        )
        self.assertEqual(event.event_type, "MILESTONE_COMPLETED")
        self.assertEqual(event.milestone, "Search UI")

    def test_blocked_with_blocker_event(self):
        event = self.reporter.report(
            project_id="SEARCH_FRONTEND",
            event_type="BLOCKED",
            status="BLOCKED",
            summary="Search API blocked by sync issue",
            blocker="auction_item synchronization mismatch",
            history_candidate=True,
        )
        self.assertEqual(event.blocker, "auction_item synchronization mismatch")

    def test_blocked_without_blocker_fails(self):
        with self.assertRaises(EventValidationError):
            self.reporter.report(
                project_id="SEARCH_FRONTEND",
                event_type="BLOCKED",
                status="BLOCKED",
                summary="Search API blocked",
                history_candidate=True,
            )

    def test_completed_event(self):
        event = self.reporter.report(
            project_id="SEARCH_FRONTEND",
            event_type="COMPLETED",
            status="COMPLETED",
            summary="Search frontend implementation completed",
            evidence=["TypeScript PASS", "Integration Test PASS"],
            history_candidate=True,
        )
        self.assertEqual(event.status, "COMPLETED")

    def test_completed_with_wrong_status_fails(self):
        with self.assertRaises(EventValidationError):
            self.reporter.report(
                project_id="SEARCH_FRONTEND",
                event_type="COMPLETED",
                status="IN_PROGRESS",
                summary="Search frontend implementation completed",
                history_candidate=True,
            )

    def test_decision_approved_event_can_be_recorded(self):
        # DECISION_APPROVED only records a Decision the CEO already made;
        # Reporter/COO do not approve anything by emitting this Event.
        coo_reporter = Reporter(
            profile=DesktopProfile(name="DESKTOP_4", source="DESKTOP_4", role="COO")
        )
        event = coo_reporter.report(
            project_id="COMPANY_OPS",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            summary="Closed Beta scope approved by CEO",
            history_candidate=True,
        )
        self.assertEqual(event.event_type, "DECISION_APPROVED")

    def test_korean_summary_and_evidence(self):
        event = self.reporter.report(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            milestone="검색 UI",
            summary="검색 UI 구현 완료",
            evidence=["타입스크립트 통과"],
            history_candidate=True,
        )
        self.assertEqual(event.summary, "검색 UI 구현 완료")
        self.assertEqual(event.evidence, ("타입스크립트 통과",))

    def test_event_id_is_generated(self):
        event = self.reporter.report(
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Search UI implementation started",
            history_candidate=False,
        )
        self.assertTrue(event.event_id)


class ReporterLocalOutputTests(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter(profile="DESKTOP_3")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = Path(tmp.name)

    def _sample_event(self, **overrides):
        kwargs = dict(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            milestone="Search UI",
            summary="Search UI implementation completed",
            evidence=["TypeScript PASS"],
            history_candidate=True,
        )
        kwargs.update(overrides)
        return self.reporter.report(**kwargs)

    def test_local_json_output(self):
        event = self._sample_event(event_id="TEST-LOCAL-OUTPUT-BASIC")
        path = write_event_json(event, directory=self.output_dir)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.output_dir)
        self.assertTrue(path.name.endswith(".json"))

    def test_round_trip_from_disk_preserves_meaning(self):
        event = self._sample_event(event_id="TEST-LOCAL-OUTPUT-ROUNDTRIP")
        path = write_event_json(event, directory=self.output_dir)
        restored = read_event_json(path)
        self.assertEqual(event, restored)

    def test_existing_event_file_is_not_silently_overwritten(self):
        event = self._sample_event(event_id="TEST-LOCAL-OUTPUT-001")
        write_event_json(event, directory=self.output_dir)
        with self.assertRaises(FileExistsError):
            write_event_json(event, directory=self.output_dir)

    def test_overwrite_flag_allows_explicit_replacement(self):
        event = self._sample_event(event_id="TEST-LOCAL-OUTPUT-002")
        write_event_json(event, directory=self.output_dir)
        path = write_event_json(event, directory=self.output_dir, overwrite=True)
        self.assertTrue(path.exists())

    def test_report_and_write_helper(self):
        event, path = self.reporter.report_and_write(
            directory=self.output_dir,
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Search UI implementation started",
            history_candidate=False,
            event_id="TEST-LOCAL-OUTPUT-003",
        )
        self.assertTrue(path.exists())
        self.assertEqual(Event.from_json(path.read_text(encoding="utf-8")), event)

    def test_filename_derived_from_event_id_not_summary(self):
        event = self._sample_event(
            event_id="TEST-SAFE-ID-001",
            summary="이 요약은 절대 파일명에 들어가면 안 된다!! ??",
        )
        path = write_event_json(event, directory=self.output_dir)
        self.assertEqual(path.name, "TEST-SAFE-ID-001.json")


class ReporterPathSafetyTests(unittest.TestCase):
    def test_default_output_dir_is_project_relative(self):
        self.assertTrue(DEFAULT_EVENT_OUTPUT_DIR.is_relative_to(PROJECT_ROOT))
        self.assertEqual(
            DEFAULT_EVENT_OUTPUT_DIR,
            PROJECT_ROOT / "runtime" / "events" / "incoming",
        )

    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        reporter_src = Path(__file__).resolve().parents[1] / "src" / "reporter"
        forbidden = ("C:\\Users", "D:\\", "OneDrive")
        for py_file in reporter_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, content, f"{token} found in {py_file}")


if __name__ == "__main__":
    unittest.main()
