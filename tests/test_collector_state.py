import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector import (  # noqa: E402
    Collector,
    CollectorStateError,
    CollectorStatus,
    PersistentSeenEventStore,
)


def sample_event_dict(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-STATE-001",
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


class PersistentSeenEventStoreTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_path = Path(tmp.name) / "state" / "collector_state.json"


class StateSaveTests(PersistentSeenEventStoreTestCase):
    def test_missing_state_file_starts_empty_with_no_error(self):
        store = PersistentSeenEventStore(state_path=self.state_path)
        self.assertFalse(store.is_seen("TEST-STATE-001"))
        self.assertFalse(self.state_path.exists())

    def test_mark_seen_persists_event_id_to_disk(self):
        store = PersistentSeenEventStore(state_path=self.state_path)
        store.mark_seen("TEST-STATE-001")

        self.assertTrue(self.state_path.exists())
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn("TEST-STATE-001", data["processed_event_ids"])

    def test_directory_is_created_if_missing(self):
        self.assertFalse(self.state_path.parent.exists())
        store = PersistentSeenEventStore(state_path=self.state_path)
        store.mark_seen("TEST-STATE-001")
        self.assertTrue(self.state_path.parent.exists())


class RestartDurabilityTests(PersistentSeenEventStoreTestCase):
    def test_duplicate_check_survives_a_fresh_store_instance(self):
        first_process = PersistentSeenEventStore(state_path=self.state_path)
        first_process.mark_seen("TEST-STATE-001")

        second_process = PersistentSeenEventStore(state_path=self.state_path)
        self.assertTrue(second_process.is_seen("TEST-STATE-001"))
        self.assertFalse(second_process.is_seen("TEST-STATE-999"))

    def test_multiple_marked_ids_all_survive_restart(self):
        first_process = PersistentSeenEventStore(state_path=self.state_path)
        for event_id in ("A-001", "A-002", "A-003"):
            first_process.mark_seen(event_id)

        second_process = PersistentSeenEventStore(state_path=self.state_path)
        for event_id in ("A-001", "A-002", "A-003"):
            self.assertTrue(second_process.is_seen(event_id))

    def test_collector_reports_duplicate_after_simulated_restart(self):
        data = sample_event_dict()

        collector_before_restart = Collector(
            seen_store=PersistentSeenEventStore(state_path=self.state_path)
        )
        first_result = collector_before_restart.collect(data)
        self.assertEqual(first_result.status, CollectorStatus.ACCEPTED)

        # simulate the process restarting: a brand new Collector + brand new
        # PersistentSeenEventStore pointed at the same file on disk
        collector_after_restart = Collector(
            seen_store=PersistentSeenEventStore(state_path=self.state_path)
        )
        second_result = collector_after_restart.collect(data)
        self.assertEqual(second_result.status, CollectorStatus.DUPLICATE)


class LastRunTests(PersistentSeenEventStoreTestCase):
    def test_record_run_persists_last_run(self):
        store = PersistentSeenEventStore(state_path=self.state_path)
        store.record_run("2026-08-01T11:00:00+09:00")

        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["last_run"], "2026-08-01T11:00:00+09:00")

    def test_last_run_survives_restart(self):
        first_process = PersistentSeenEventStore(state_path=self.state_path)
        first_process.record_run("2026-08-01T11:00:00+09:00")

        second_process = PersistentSeenEventStore(state_path=self.state_path)
        self.assertEqual(second_process.last_run, "2026-08-01T11:00:00+09:00")

    def test_record_run_without_explicit_timestamp_uses_now(self):
        store = PersistentSeenEventStore(state_path=self.state_path)
        store.record_run()
        self.assertIsNotNone(store.last_run)
        self.assertIsInstance(store.last_run, str)

    def test_missing_state_file_has_no_last_run(self):
        store = PersistentSeenEventStore(state_path=self.state_path)
        self.assertIsNone(store.last_run)


class CorruptedStateTests(PersistentSeenEventStoreTestCase):
    def _write_raw(self, content: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(content, encoding="utf-8")

    def test_invalid_json_raises_collector_state_error(self):
        self._write_raw("{not valid json")
        with self.assertRaises(CollectorStateError):
            PersistentSeenEventStore(state_path=self.state_path)

    def test_non_object_json_raises_collector_state_error(self):
        self._write_raw("[1, 2, 3]")
        with self.assertRaises(CollectorStateError):
            PersistentSeenEventStore(state_path=self.state_path)

    def test_wrong_type_processed_event_ids_raises_collector_state_error(self):
        self._write_raw(json.dumps({"last_run": None, "processed_event_ids": "not-a-list"}))
        with self.assertRaises(CollectorStateError):
            PersistentSeenEventStore(state_path=self.state_path)

    def test_wrong_type_last_run_raises_collector_state_error(self):
        self._write_raw(json.dumps({"last_run": 12345, "processed_event_ids": []}))
        with self.assertRaises(CollectorStateError):
            PersistentSeenEventStore(state_path=self.state_path)

    def test_corrupted_state_does_not_delete_the_bad_file(self):
        self._write_raw("{not valid json")
        try:
            PersistentSeenEventStore(state_path=self.state_path)
        except CollectorStateError:
            pass
        self.assertTrue(self.state_path.exists())
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{not valid json")


class AtomicSaveTests(PersistentSeenEventStoreTestCase):
    def test_no_leftover_temp_files_after_save(self):
        store = PersistentSeenEventStore(state_path=self.state_path)
        store.mark_seen("TEST-STATE-001")
        store.record_run("2026-08-01T11:00:00+09:00")

        remaining = list(self.state_path.parent.glob(".tmp-*"))
        self.assertEqual(remaining, [])

    def test_state_file_is_always_fully_valid_json_after_each_save(self):
        store = PersistentSeenEventStore(state_path=self.state_path)
        for i in range(5):
            store.mark_seen(f"TEST-STATE-{i:03d}")
            # every save must leave a fully parseable file, never a partial write
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.assertIn(f"TEST-STATE-{i:03d}", data["processed_event_ids"])


class StatePathSafetyTests(unittest.TestCase):
    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        state_module = Path(__file__).resolve().parents[1] / "src" / "collector" / "state.py"
        content = state_module.read_text(encoding="utf-8")
        for token in ("C:\\Users", "D:\\", "OneDrive\\"):
            self.assertNotIn(token, content, f"{token} found in {state_module}")


if __name__ == "__main__":
    unittest.main()
