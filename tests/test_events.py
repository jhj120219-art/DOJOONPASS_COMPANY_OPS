import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import (  # noqa: E402
    Event,
    EventValidationError,
    create_event,
    generate_event_id,
    validate_event,
)


def sample_data(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-MILESTONE-001",
        "timestamp": "2026-08-01T20:00:00+09:00",
        "source": "DESKTOP_3",
        "role": "CTO_FRONTEND",
        "project_id": "SEARCH_FRONTEND",
        "event_type": "MILESTONE_COMPLETED",
        "status": "IN_PROGRESS",
        "milestone": "Search UI",
        "summary": "Search UI implementation completed",
        "blocker": None,
        "evidence": ["TypeScript PASS"],
        "history_candidate": True,
    }
    data.update(overrides)
    return data


class EventCreationTests(unittest.TestCase):
    def test_valid_started_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-START-001",
                event_type="STARTED",
                status="IN_PROGRESS",
                blocker=None,
                evidence=[],
                history_candidate=False,
            )
        )
        self.assertEqual(event.event_type, "STARTED")

    def test_valid_blocked_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-BLOCK-001",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="auction_item synchronization mismatch",
                history_candidate=True,
            )
        )
        self.assertEqual(event.blocker, "auction_item synchronization mismatch")

    def test_valid_resumed_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-RESUME-001",
                event_type="RESUMED",
                status="IN_PROGRESS",
                blocker=None,
                history_candidate=False,
            )
        )
        self.assertIsNone(event.blocker)

    def test_valid_milestone_completed_event(self):
        event = Event.from_dict(sample_data())
        self.assertEqual(event.milestone, "Search UI")

    def test_valid_completed_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-COMPLETE-001",
                event_type="COMPLETED",
                status="COMPLETED",
                milestone="Search Frontend Integration",
                evidence=["TypeScript PASS", "Integration Test PASS"],
            )
        )
        self.assertEqual(event.status, "COMPLETED")


class EventValidationTests(unittest.TestCase):
    def test_missing_required_field_rejected(self):
        data = sample_data()
        del data["summary"]
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_invalid_event_type_rejected(self):
        data = sample_data(event_type="RANDOM_TYPE")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_invalid_status_rejected(self):
        data = sample_data(status="ALMOST_DONE")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_invalid_timestamp_rejected(self):
        for bad_timestamp in ("2026-08-01", "8월 1일", "오후 8시", "not-a-date"):
            with self.subTest(bad_timestamp=bad_timestamp):
                data = sample_data(timestamp=bad_timestamp)
                with self.assertRaises(EventValidationError):
                    Event.from_dict(data)

    def test_blocked_without_blocker_rejected(self):
        data = sample_data(event_type="BLOCKED", status="BLOCKED", blocker=None)
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_completed_status_mismatch_rejected(self):
        data = sample_data(event_type="COMPLETED", status="NOT_STARTED")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_cancelled_status_mismatch_rejected(self):
        data = sample_data(event_type="CANCELLED", status="IN_PROGRESS")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_unsupported_schema_version_rejected(self):
        data = sample_data(schema_version="2.0")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_validate_event_reports_multiple_errors(self):
        data = sample_data(event_type="BAD_TYPE", status="BAD_STATUS")
        del data["project_id"]
        errors = validate_event(data)
        self.assertGreaterEqual(len(errors), 3)


class EventIdentityTests(unittest.TestCase):
    def test_generated_event_ids_are_unique(self):
        ids = {generate_event_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_event_id_is_preserved(self):
        event = create_event(
            source="DESKTOP_3",
            role="CTO_FRONTEND",
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Search UI implementation started",
            history_candidate=False,
            event_id="TEST-START-001",
        )
        self.assertEqual(event.event_id, "TEST-START-001")

    def test_create_event_generates_id_and_timestamp_when_omitted(self):
        event = create_event(
            source="DESKTOP_3",
            role="CTO_FRONTEND",
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Search UI implementation started",
            history_candidate=False,
        )
        self.assertTrue(event.event_id)
        parsed = datetime.fromisoformat(event.timestamp)
        self.assertIsNotNone(parsed.tzinfo)


class EventSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_meaning(self):
        original = Event.from_dict(sample_data())
        restored = Event.from_json(original.to_json())
        self.assertEqual(original, restored)

    def test_round_trip_preserves_unicode(self):
        data = sample_data(
            summary="검색 UI 구현 완료",
            blocker=None,
            milestone="검색 UI",
        )
        original = Event.from_dict(data)
        raw = original.to_json()
        self.assertNotIn("\\u", raw)
        restored = Event.from_json(raw)
        self.assertEqual(restored.summary, "검색 UI 구현 완료")
        self.assertEqual(restored.milestone, "검색 UI")


if __name__ == "__main__":
    unittest.main()
