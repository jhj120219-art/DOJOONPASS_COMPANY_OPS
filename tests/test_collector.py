import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector import (  # noqa: E402
    Collector,
    CollectorError,
    CollectorStatus,
    InMemorySeenEventStore,
    SeenEventStore,
)
from events import Event  # noqa: E402


def sample_event_dict(**overrides):
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


class CollectorAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.collector = Collector(seen_store=InMemorySeenEventStore())

    def test_started_event_accepted(self):
        result = self.collector.collect(
            sample_event_dict(
                event_id="TEST-START-001",
                event_type="STARTED",
                status="IN_PROGRESS",
                evidence=[],
                history_candidate=False,
            )
        )
        self.assertEqual(result.status, CollectorStatus.ACCEPTED)
        self.assertIsInstance(result.event, Event)

    def test_blocked_event_accepted(self):
        result = self.collector.collect(
            sample_event_dict(
                event_id="TEST-BLOCK-001",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="auction_item synchronization mismatch",
            )
        )
        self.assertEqual(result.status, CollectorStatus.ACCEPTED)

    def test_resumed_event_accepted(self):
        result = self.collector.collect(
            sample_event_dict(
                event_id="TEST-RESUME-001",
                event_type="RESUMED",
                status="IN_PROGRESS",
                blocker=None,
                history_candidate=False,
            )
        )
        self.assertEqual(result.status, CollectorStatus.ACCEPTED)

    def test_milestone_completed_event_accepted(self):
        result = self.collector.collect(sample_event_dict())
        self.assertEqual(result.status, CollectorStatus.ACCEPTED)

    def test_completed_event_accepted(self):
        result = self.collector.collect(
            sample_event_dict(
                event_id="TEST-COMPLETE-001",
                event_type="COMPLETED",
                status="COMPLETED",
            )
        )
        self.assertEqual(result.status, CollectorStatus.ACCEPTED)

    def test_json_string_input_accepted(self):
        raw = json.dumps(sample_event_dict(event_id="TEST-JSON-001"), ensure_ascii=False)
        result = self.collector.collect(raw)
        self.assertEqual(result.status, CollectorStatus.ACCEPTED)
        self.assertEqual(result.event.event_id, "TEST-JSON-001")


class CollectorRejectionTests(unittest.TestCase):
    def setUp(self):
        self.collector = Collector(seen_store=InMemorySeenEventStore())

    def test_malformed_json_rejected(self):
        result = self.collector.collect("{not valid json")
        self.assertEqual(result.status, CollectorStatus.REJECTED)
        self.assertTrue(result.errors)
        self.assertIsNone(result.event)

    def test_non_object_json_rejected(self):
        result = self.collector.collect("[1, 2, 3]")
        self.assertEqual(result.status, CollectorStatus.REJECTED)
        self.assertIsNone(result.event)

    def test_missing_required_field_rejected(self):
        data = sample_event_dict()
        del data["summary"]
        result = self.collector.collect(data)
        self.assertEqual(result.status, CollectorStatus.REJECTED)

    def test_invalid_event_type_rejected(self):
        result = self.collector.collect(sample_event_dict(event_type="NOT_A_TYPE"))
        self.assertEqual(result.status, CollectorStatus.REJECTED)

    def test_invalid_status_rejected(self):
        result = self.collector.collect(sample_event_dict(status="NOT_A_STATUS"))
        self.assertEqual(result.status, CollectorStatus.REJECTED)

    def test_invalid_timestamp_rejected(self):
        result = self.collector.collect(sample_event_dict(timestamp="2026-08-01"))
        self.assertEqual(result.status, CollectorStatus.REJECTED)

    def test_blocked_without_blocker_rejected(self):
        result = self.collector.collect(
            sample_event_dict(event_type="BLOCKED", status="BLOCKED", blocker=None)
        )
        self.assertEqual(result.status, CollectorStatus.REJECTED)

    def test_rejected_result_preserves_raw_input(self):
        data = sample_event_dict()
        del data["summary"]
        result = self.collector.collect(data)
        self.assertEqual(result.raw_input, data)


class CollectorDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.collector = Collector(seen_store=InMemorySeenEventStore())

    def test_second_delivery_of_same_event_id_is_duplicate(self):
        data = sample_event_dict(event_id="TEST-DUPLICATE-001")
        first = self.collector.collect(data)
        second = self.collector.collect(data)
        self.assertEqual(first.status, CollectorStatus.ACCEPTED)
        self.assertEqual(second.status, CollectorStatus.DUPLICATE)
        self.assertEqual(second.event.event_id, "TEST-DUPLICATE-001")

    def test_duplicate_result_has_no_errors(self):
        data = sample_event_dict(event_id="TEST-DUPLICATE-002")
        self.collector.collect(data)
        second = self.collector.collect(data)
        self.assertEqual(second.errors, ())

    def test_different_collector_instances_do_not_share_state(self):
        data = sample_event_dict(event_id="TEST-DUPLICATE-003")
        self.collector.collect(data)
        other = Collector(seen_store=InMemorySeenEventStore())
        result = other.collect(data)
        self.assertEqual(result.status, CollectorStatus.ACCEPTED)


class CollectorInternalFailureTests(unittest.TestCase):
    def test_seen_store_failure_raises_collector_error_not_reject(self):
        class BrokenStore(SeenEventStore):
            def is_seen(self, event_id: str) -> bool:
                raise RuntimeError("state file unreadable")

            def mark_seen(self, event_id: str) -> None:
                raise RuntimeError("state file unreadable")

        collector = Collector(seen_store=BrokenStore())
        with self.assertRaises(CollectorError):
            collector.collect(sample_event_dict(event_id="TEST-INTERNAL-FAIL-001"))

    def test_mark_seen_failure_alone_raises_collector_error(self):
        """`BrokenStore` above fails `is_seen()` first, so `collect()` never
        actually reaches the separate `mark_seen()` try/except (confirmed via
        `python -m trace` coverage this Sprint: that branch had zero
        executions across the whole suite). Isolates it: `is_seen()`
        succeeds (not a duplicate), only `mark_seen()` fails."""

        class MarkSeenBrokenStore(SeenEventStore):
            def is_seen(self, event_id: str) -> bool:
                return False

            def mark_seen(self, event_id: str) -> None:
                raise RuntimeError("state file unwritable")

        collector = Collector(seen_store=MarkSeenBrokenStore())
        with self.assertRaises(CollectorError) as caught:
            collector.collect(sample_event_dict(event_id="TEST-INTERNAL-FAIL-002"))

        self.assertIn("TEST-INTERNAL-FAIL-002", str(caught.exception))


class CollectorBoundaryTests(unittest.TestCase):
    def test_collector_module_does_not_import_transport_or_reporter(self):
        collector_src = Path(__file__).resolve().parents[1] / "src" / "collector"
        forbidden = (
            re.compile(r"^\s*import\s+transport\b", re.MULTILINE),
            re.compile(r"^\s*from\s+transport\b", re.MULTILINE),
            re.compile(r"^\s*import\s+reporter\b", re.MULTILINE),
            re.compile(r"^\s*from\s+reporter\b", re.MULTILINE),
        )
        for py_file in collector_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for pattern in forbidden:
                self.assertIsNone(
                    pattern.search(content),
                    f"{py_file} unexpectedly imports transport/reporter",
                )

    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        collector_src = Path(__file__).resolve().parents[1] / "src" / "collector"
        forbidden = ("C:\\Users", "D:\\", "OneDrive\\")
        for py_file in collector_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, content, f"{token} found in {py_file}")


if __name__ == "__main__":
    unittest.main()
