import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event  # noqa: E402
from notion.retry_queue import dequeue, enqueue, load_queue, save_queue  # noqa: E402


def sample_event(event_id: str) -> Event:
    return Event.from_dict({
        "schema_version": "1.0",
        "event_id": event_id,
        "timestamp": "2026-08-01T10:00:00+09:00",
        "source": "DESKTOP_3",
        "role": "CTO_FRONTEND",
        "project_id": "SEARCH_FRONTEND",
        "event_type": "MILESTONE_COMPLETED",
        "status": "IN_PROGRESS",
        "milestone": "m",
        "summary": "s",
        "blocker": None,
        "evidence": ["x"],
        "history_candidate": True,
    })


class RetryQueueTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.queue_path = Path(tmp.name) / "state" / "notion_retry_queue.json"


class LoadEmptyQueueTests(RetryQueueTestCase):
    def test_missing_file_is_an_empty_queue(self):
        self.assertEqual(load_queue(self.queue_path), [])


class EnqueueDequeueTests(RetryQueueTestCase):
    def test_enqueue_then_load_round_trips(self):
        enqueue(self.queue_path, sample_event("EVT-1"), now=datetime(2026, 8, 1, 12, 0))

        entries = load_queue(self.queue_path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].event_id, "EVT-1")
        self.assertEqual(entries[0].project_id, "SEARCH_FRONTEND")
        self.assertEqual(entries[0].attempt_count, 1)
        self.assertEqual(entries[0].to_event().event_id, "EVT-1")

    def test_dequeue_removes_the_entry(self):
        enqueue(self.queue_path, sample_event("EVT-1"))
        dequeue(self.queue_path, "EVT-1")

        self.assertEqual(load_queue(self.queue_path), [])

    def test_dequeue_of_absent_event_id_is_a_no_op(self):
        enqueue(self.queue_path, sample_event("EVT-1"))
        dequeue(self.queue_path, "NEVER-QUEUED")  # must not raise, must not touch EVT-1

        entries = load_queue(self.queue_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].event_id, "EVT-1")

    def test_dequeue_on_empty_queue_is_a_no_op(self):
        dequeue(self.queue_path, "EVT-1")  # no file exists yet — must not raise
        self.assertEqual(load_queue(self.queue_path), [])


class DedupTests(RetryQueueTestCase):
    def test_re_enqueuing_the_same_event_id_is_an_upsert_not_a_duplicate(self):
        # docs/03_COLLECTOR_SPEC.md's event_id-based duplicate model, applied
        # here: a repeatedly-failing event must never accumulate more than
        # one queue entry.
        enqueue(self.queue_path, sample_event("EVT-1"))
        enqueue(self.queue_path, sample_event("EVT-1"))
        enqueue(self.queue_path, sample_event("EVT-1"))

        entries = load_queue(self.queue_path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].attempt_count, 3)

    def test_added_at_is_preserved_across_re_enqueues(self):
        enqueue(self.queue_path, sample_event("EVT-1"), now=datetime(2026, 8, 1, 9, 0))
        enqueue(self.queue_path, sample_event("EVT-1"), now=datetime(2026, 8, 2, 9, 0))

        entries = load_queue(self.queue_path)

        self.assertEqual(entries[0].added_at, datetime(2026, 8, 1, 9, 0).isoformat(timespec="seconds"))

    def test_multiple_distinct_events_coexist(self):
        enqueue(self.queue_path, sample_event("EVT-1"))
        enqueue(self.queue_path, sample_event("EVT-2"))
        dequeue(self.queue_path, "EVT-1")

        entries = load_queue(self.queue_path)

        self.assertEqual([e.event_id for e in entries], ["EVT-2"])


class PersistenceTests(RetryQueueTestCase):
    def test_save_queue_is_atomic_and_survives_reload(self):
        save_queue(self.queue_path, [])
        enqueue(self.queue_path, sample_event("EVT-1"))
        enqueue(self.queue_path, sample_event("EVT-2"))

        reloaded = load_queue(self.queue_path)

        self.assertEqual({e.event_id for e in reloaded}, {"EVT-1", "EVT-2"})


if __name__ == "__main__":
    unittest.main()
