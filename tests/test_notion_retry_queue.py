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


class CorruptedQueueTests(RetryQueueTestCase):
    """Coverage gap found via `python -m trace` this Sprint: `load_queue()`
    had NO corruption test at all before this class. In particular the
    `except (AttributeError, KeyError, TypeError)` around
    `RetryQueueEntry.from_dict()` -- valid JSON, valid top-level shape, but
    one entry missing a required key -- had zero executions across the
    whole suite."""

    def test_invalid_json_raises_the_typed_error(self):
        import json as json_module

        from notion.retry_queue import RetryQueueError

        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text("{not valid json", encoding="utf-8")

        with self.assertRaises(RetryQueueError):
            load_queue(self.queue_path)

        self.assertEqual(self.queue_path.read_text(encoding="utf-8"), "{not valid json")

    def test_a_malformed_entry_inside_otherwise_valid_json_raises(self):
        import json as json_module

        from notion.retry_queue import RetryQueueError

        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text(
            json_module.dumps(
                {"entries": [{"project_id": "P", "event_data": {}, "added_at": "x", "attempt_count": 0}]}
            ),
            encoding="utf-8",
        )  # missing "event_id"

        with self.assertRaises(RetryQueueError) as caught:
            load_queue(self.queue_path)
        self.assertIn("malformed entry", str(caught.exception))


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


class IndexedUpsertRemoveTests(unittest.TestCase):
    """`build_index()` + the `index=` kwarg turn `app/runner.py`'s drain loop
    from O(n^2) list scans into O(n) (measured: draining 10,000 queued
    entries dropped from 3,637 ms to 14.4 ms). Additive on top of the
    existing Batch Save (CEO 승인 B안): entries' JSON shape and upsert/dedup
    semantics are unchanged, and every caller that omits `index` (enqueue(),
    dequeue(), every test above this class) keeps the exact original
    behaviour byte-for-byte, order included."""

    def test_indexed_and_unindexed_upsert_produce_identical_results(self):
        from notion.retry_queue import build_index, upsert_entry

        plain: list = []
        indexed: list = []
        idx = {}
        events = [sample_event(f"EVT-{i}") for i in range(20)]

        for event in events:
            upsert_entry(plain, event, now=datetime(2026, 8, 1, 9, 0))
        for event in events:
            upsert_entry(indexed, event, now=datetime(2026, 8, 1, 9, 0), index=idx)
        # A repeat (attempt_count increment path) on both.
        upsert_entry(plain, events[5], now=datetime(2026, 8, 2, 9, 0))
        upsert_entry(indexed, events[5], now=datetime(2026, 8, 2, 9, 0), index=idx)

        self.assertEqual(
            {e.event_id: e.to_dict() for e in plain},
            {e.event_id: e.to_dict() for e in indexed},
        )
        self.assertEqual(idx, build_index(indexed))

    def test_indexed_remove_finds_the_same_entries_as_unindexed(self):
        from notion.retry_queue import build_index, remove_entry, upsert_entry

        plain: list = []
        indexed: list = []
        idx = {}
        events = [sample_event(f"EVT-{i}") for i in range(15)]
        for event in events:
            upsert_entry(plain, event)
            upsert_entry(indexed, event, index=idx)

        # Remove every third id, then everything remaining, exercising the
        # swap-with-last path repeatedly (including removing the last element
        # and removing down to zero).
        to_remove = [e.event_id for e in events[::3]] + [e.event_id for e in events]
        seen = set()
        order = [eid for eid in to_remove if not (eid in seen or seen.add(eid))]

        for event_id in order:
            plain_removed = remove_entry(plain, event_id)
            indexed_removed = remove_entry(indexed, event_id, index=idx)
            self.assertEqual(plain_removed, indexed_removed, event_id)

        self.assertEqual({e.event_id for e in plain}, set())
        self.assertEqual({e.event_id for e in indexed}, set())
        self.assertEqual(idx, {})

    def test_removing_a_nonexistent_id_with_an_index_returns_false(self):
        from notion.retry_queue import remove_entry, upsert_entry

        entries: list = []
        idx = {}
        upsert_entry(entries, sample_event("EVT-1"), index=idx)

        self.assertFalse(remove_entry(entries, "EVT-DOES-NOT-EXIST", index=idx))
        self.assertEqual(len(entries), 1)

    def test_build_index_matches_a_linear_scan(self):
        from notion.retry_queue import build_index, upsert_entry

        entries: list = []
        for i in range(10):
            upsert_entry(entries, sample_event(f"EVT-{i}"))

        idx = build_index(entries)

        for i, entry in enumerate(entries):
            self.assertEqual(idx[entry.event_id], i)


if __name__ == "__main__":
    unittest.main()
