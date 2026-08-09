import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import transport.intake as intake_module  # noqa: E402
from transport import run_intake  # noqa: E402


class IntakeTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.transport_dir = root / "transport"
        self.incoming_dir = root / "incoming"
        self.processed_dir = root / "processed"
        self.rejected_dir = root / "rejected"
        self.transport_dir.mkdir(parents=True)

    def _write(self, directory: Path, name: str, content: str, age_seconds: float = 10.0):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(content, encoding="utf-8")
        old_time = time.time() - age_seconds
        os.utime(path, (old_time, old_time))
        return path

    def _run(self, **kwargs):
        return run_intake(
            transport_dir=self.transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            **kwargs,
        )


class MovesStableValidFilesTests(IntakeTestCase):
    def test_stable_valid_file_is_moved(self):
        self._write(self.transport_dir, "TEST-001.json", json.dumps({"a": 1}))
        summary = self._run()

        self.assertEqual(summary.moved, ("TEST-001.json",))
        self.assertFalse((self.transport_dir / "TEST-001.json").exists())
        self.assertTrue((self.incoming_dir / "TEST-001.json").exists())

    def test_content_is_preserved_exactly(self):
        original = json.dumps({"event_id": "TEST-001", "summary": "검색 UI 구현 완료"}, ensure_ascii=False)
        self._write(self.transport_dir, "TEST-001.json", original)
        self._run()
        moved_content = (self.incoming_dir / "TEST-001.json").read_text(encoding="utf-8")
        self.assertEqual(moved_content, original)

    def test_multiple_files_all_moved(self):
        for i in range(3):
            self._write(self.transport_dir, f"TEST-{i:03d}.json", json.dumps({"i": i}))
        summary = self._run()
        self.assertEqual(len(summary.moved), 3)
        self.assertEqual(len(list(self.incoming_dir.glob("*.json"))), 3)


class NotStableTests(IntakeTestCase):
    def test_recently_modified_file_is_not_moved(self):
        self._write(self.transport_dir, "TEST-FRESH-001.json", json.dumps({"a": 1}), age_seconds=0.0)
        summary = self._run(stable_after_seconds=5.0)

        self.assertEqual(summary.moved, ())
        self.assertEqual(summary.skipped_not_stable, ("TEST-FRESH-001.json",))
        self.assertTrue((self.transport_dir / "TEST-FRESH-001.json").exists())

    def test_file_becomes_eligible_once_stable_threshold_passes(self):
        self._write(self.transport_dir, "TEST-LATER-001.json", json.dumps({"a": 1}), age_seconds=0.0)
        first = self._run(stable_after_seconds=5.0)
        self.assertEqual(first.moved, ())

        # simulate time passing by re-running with a shorter threshold —
        # equivalent to the same wall-clock time later
        second = self._run(stable_after_seconds=0.0)
        self.assertEqual(second.moved, ("TEST-LATER-001.json",))


class InvalidJsonTests(IntakeTestCase):
    def test_unparseable_file_is_left_in_place(self):
        self._write(self.transport_dir, "TEST-BAD-001.json", "{not valid json")
        summary = self._run()

        self.assertEqual(summary.moved, ())
        self.assertEqual(summary.skipped_invalid, ("TEST-BAD-001.json",))
        self.assertTrue((self.transport_dir / "TEST-BAD-001.json").exists())
        self.assertFalse((self.incoming_dir / "TEST-BAD-001.json").exists())

    def test_invalid_file_is_never_deleted(self):
        self._write(self.transport_dir, "TEST-BAD-002.json", "not json at all")
        self._run()
        self.assertTrue((self.transport_dir / "TEST-BAD-002.json").exists())


class DuplicateSafetyTests(IntakeTestCase):
    def test_file_already_in_incoming_is_skipped(self):
        self._write(self.transport_dir, "TEST-DUP-001.json", json.dumps({"a": 1}))
        self._write(self.incoming_dir, "TEST-DUP-001.json", json.dumps({"a": 1}))

        summary = self._run()

        self.assertEqual(summary.moved, ())
        self.assertEqual(summary.skipped_already_present, ("TEST-DUP-001.json",))
        # the transport/ copy is left alone, not deleted
        self.assertTrue((self.transport_dir / "TEST-DUP-001.json").exists())

    def test_file_already_processed_is_skipped(self):
        self._write(self.transport_dir, "TEST-DUP-002.json", json.dumps({"a": 1}))
        self._write(self.processed_dir, "TEST-DUP-002.json", json.dumps({"a": 1}))

        summary = self._run()
        self.assertEqual(summary.skipped_already_present, ("TEST-DUP-002.json",))

    def test_file_already_rejected_is_skipped(self):
        self._write(self.transport_dir, "TEST-DUP-003.json", "{bad json")
        self._write(self.rejected_dir, "TEST-DUP-003.json", "{bad json")

        summary = self._run()
        self.assertEqual(summary.skipped_already_present, ("TEST-DUP-003.json",))


class EmptyAndIdempotentTests(IntakeTestCase):
    def test_empty_transport_directory_is_not_an_error(self):
        summary = self._run()
        self.assertEqual(
            (summary.moved, summary.skipped_not_stable, summary.skipped_already_present, summary.skipped_invalid, summary.failed),
            ((), (), (), (), ()),
        )

    def test_running_twice_in_a_row_only_moves_once(self):
        self._write(self.transport_dir, "TEST-IDEMPOTENT-001.json", json.dumps({"a": 1}))
        first = self._run()
        second = self._run()

        self.assertEqual(first.moved, ("TEST-IDEMPOTENT-001.json",))
        self.assertEqual(second.moved, ())
        self.assertEqual(len(list(self.incoming_dir.glob("*.json"))), 1)


class IntakeReplaceFailureTests(IntakeTestCase):
    """`os.replace()` failing partway through the scan — found via `python -m
    trace --count` to have zero coverage anywhere in the suite (the only
    existing OSError-adjacent coverage is BUG-53's existence check, a
    different code path entirely). A real-world trigger: antivirus or a
    backup tool holding a transient lock on the destination directory."""

    def test_a_replace_failure_is_recorded_and_the_file_is_not_lost(self):
        event_file = self.transport_dir / "E1.json"
        event_file.write_text('{"a": 1}', encoding="utf-8")
        old_time = time.time() - 100
        os.utime(event_file, (old_time, old_time))

        original_replace = intake_module.os.replace

        def failing_replace(*args, **kwargs):
            raise OSError("simulated replace failure")

        intake_module.os.replace = failing_replace
        self.addCleanup(setattr, intake_module.os, "replace", original_replace)

        summary = run_intake(
            transport_dir=self.transport_dir, incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir, rejected_dir=self.rejected_dir,
            stable_after_seconds=1.0,
        )

        self.assertEqual(summary.moved, ())
        self.assertEqual(summary.failed, ("E1.json",))
        self.assertTrue(event_file.exists(), "the file must stay in transport/, not be lost")

    def test_a_later_run_recovers_once_the_failure_is_gone(self):
        event_file = self.transport_dir / "E1.json"
        event_file.write_text('{"a": 1}', encoding="utf-8")
        old_time = time.time() - 100
        os.utime(event_file, (old_time, old_time))

        original_replace = intake_module.os.replace
        intake_module.os.replace = lambda *a, **kw: (_ for _ in ()).throw(OSError("simulated"))
        run_intake(
            transport_dir=self.transport_dir, incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir, rejected_dir=self.rejected_dir,
            stable_after_seconds=1.0,
        )
        intake_module.os.replace = original_replace

        summary = run_intake(
            transport_dir=self.transport_dir, incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir, rejected_dir=self.rejected_dir,
            stable_after_seconds=1.0,
        )

        self.assertEqual(summary.moved, ("E1.json",))


class IntakePathSafetyTests(unittest.TestCase):
    def test_no_hardcoded_absolute_windows_paths(self):
        intake_file = Path(__file__).resolve().parents[1] / "src" / "transport" / "intake.py"
        content = intake_file.read_text(encoding="utf-8")
        code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
        for token in ("C:\\Users", "D:\\", "OneDrive\\"):
            self.assertNotIn(token, code_without_docstrings)

    def test_does_not_import_collector_reporter_or_daily(self):
        intake_file = Path(__file__).resolve().parents[1] / "src" / "transport" / "intake.py"
        content = intake_file.read_text(encoding="utf-8")
        forbidden = re.compile(r"^\s*(import|from)\s+(collector|reporter|daily|scheduler)\b", re.MULTILINE)
        self.assertIsNone(forbidden.search(content))


if __name__ == "__main__":
    unittest.main()
