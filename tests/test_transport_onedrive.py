import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event  # noqa: E402
from transport import OneDriveTransport, TransportError  # noqa: E402


def sample_event(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-ONEDRIVE-001",
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
    return Event.from_dict(data)


class OneDriveTransportTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.outgoing_dir = root / "outgoing"
        self.sync_folder = root / "sync"
        self.transport = OneDriveTransport(sync_folder=self.sync_folder, outgoing_dir=self.outgoing_dir)


class SendTests(OneDriveTransportTestCase):
    def test_writes_to_outgoing_dir(self):
        event = sample_event()
        self.transport.send(event)
        self.assertTrue((self.outgoing_dir / "TEST-ONEDRIVE-001.json").exists())

    def test_writes_to_sync_folder(self):
        event = sample_event()
        self.transport.send(event)
        self.assertTrue((self.sync_folder / "TEST-ONEDRIVE-001.json").exists())

    def test_sync_folder_content_round_trips_to_the_same_event(self):
        event = sample_event()
        self.transport.send(event)
        raw = (self.sync_folder / "TEST-ONEDRIVE-001.json").read_text(encoding="utf-8")
        self.assertEqual(Event.from_json(raw), event)

    def test_korean_summary_survives_the_copy(self):
        event = sample_event(event_id="TEST-ONEDRIVE-002", summary="검색 UI 구현 완료")
        self.transport.send(event)
        raw = (self.sync_folder / "TEST-ONEDRIVE-002.json").read_text(encoding="utf-8")
        self.assertIn("검색 UI 구현 완료", raw)

    def test_resending_same_event_id_is_idempotent(self):
        event = sample_event()
        self.transport.send(event)
        self.transport.send(event)  # simulate retry
        raw = (self.sync_folder / "TEST-ONEDRIVE-001.json").read_text(encoding="utf-8")
        self.assertEqual(Event.from_json(raw), event)

    def test_no_leftover_temp_files(self):
        self.transport.send(sample_event())
        self.assertEqual(list(self.outgoing_dir.glob(".tmp-*")), [])
        self.assertEqual(list(self.sync_folder.glob(".tmp-*")), [])

    def test_write_failure_raises_transport_error(self):
        # point outgoing_dir at a path that can never become a directory
        # (a file already occupies that name)
        root = self.outgoing_dir.parent
        blocked = root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        transport = OneDriveTransport(sync_folder=self.sync_folder, outgoing_dir=blocked / "nested")

        with self.assertRaises(TransportError):
            transport.send(sample_event())


class OneDriveTransportPathSafetyTests(unittest.TestCase):
    def test_no_hardcoded_absolute_windows_paths(self):
        onedrive_file = Path(__file__).resolve().parents[1] / "src" / "transport" / "onedrive.py"
        content = onedrive_file.read_text(encoding="utf-8")
        code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
        for token in ("C:\\Users", "D:\\", "OneDrive\\"):
            self.assertNotIn(token, code_without_docstrings)

    def test_does_not_import_collector_reporter_or_daily(self):
        onedrive_file = Path(__file__).resolve().parents[1] / "src" / "transport" / "onedrive.py"
        content = onedrive_file.read_text(encoding="utf-8")
        forbidden = re.compile(r"^\s*(import|from)\s+(collector|reporter|daily|scheduler)\b", re.MULTILINE)
        self.assertIsNone(forbidden.search(content))


if __name__ == "__main__":
    unittest.main()
