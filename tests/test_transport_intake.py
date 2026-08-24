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
    def test_a_file_whose_stat_fails_is_not_promoted(self):
        """C49: found by branch coverage — `_is_stable()`'s `except OSError`
        had never been executed.

        The answer it gives is the safe one and that is the point: a file
        this process cannot even stat is treated as **not stable**, so it
        stays in `transport/` and is re-judged next run rather than being
        promoted into `incoming/` on no evidence.

        Ordinary on this project's transport. `transport/` is an OneDrive
        folder (docs/11); a placeholder being hydrated, a file another
        process holds, or one deleted between the listing and the check all
        produce exactly this.
        """
        path = self._write(self.transport_dir, "unstattable.json", '{"a": 1}')

        # `Path.stat`, not `os.stat` — and the difference is an interpreter
        # version, not a style choice. `_is_stable()` calls `path.stat()`,
        # and only on Python 3.11+ does that reach the module-level
        # `os.stat` a test can rebind; 3.9 and 3.10's `pathlib` capture the
        # function on `_NormalAccessor` at import time, so patching `os.stat`
        # there changes nothing and the file is promoted as though it had
        # stat'ed cleanly — the test failed while asserting the very
        # behaviour it was written to protect. It was written on 3.9.7; the
        # current runtime is 3.13.14 (BACKLOG D), where patching `os.stat`
        # would happen to work. Patching the method the production code
        # actually calls works on every version, which is why the move
        # needed no edit here.
        real_stat = Path.stat
        blocked = str(path)

        def failing_stat(self, *args, **kwargs):
            # Only this one path, and only in `transport/`. A broader match
            # also breaks the `incoming/` check below, which would make the
            # test pass for the wrong reason.
            if str(self) == blocked:
                raise OSError(5, "cannot stat")
            return real_stat(self, *args, **kwargs)

        Path.stat = failing_stat
        self.addCleanup(setattr, Path, "stat", real_stat)

        summary = self._run()

        # Restored before asserting: `Path.exists()` is itself a `stat`, so
        # the assertion would otherwise raise the injected error instead of
        # answering the question.
        Path.stat = real_stat

        self.assertEqual(summary.moved, ())
        self.assertTrue(path.exists(), "the file was moved on no evidence")
        self.assertFalse((self.incoming_dir / path.name).exists())

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


class LiveProducerRaceTests(unittest.TestCase):
    """A sender is still writing into the directory the intake is draining.

    `TransportIntakeConcurrencySafetyTests` races four *consumers* against
    each other on files that already exist. This is the other axis and the
    one the atomic-write discipline was built for: a real OneDrive-style
    sender (`transport.onedrive`, in separate OS processes) producing into
    the folder while `run_intake()` + the Collector drain it in this one.

    Three guards have to hold together, and none of them was ever exercised
    against a live writer:

        `.tmp-…json` is skipped          a staging file is a write in
                                         progress, not an Event
        `_is_parseable_json()`           a file that is not yet complete
                                         JSON is left where it is
        `os.replace()` commits           the name only ever appears once the
                                         bytes are all there

    Run with `stable_after_seconds=0` on purpose — the most aggressive
    setting there is. The stability window is what usually hides a torn read
    behind a delay, and switching it off means only the atomic-write
    discipline is left holding the line.

    What is asserted is the property, not a duration: every Event the
    senders reported delivering is accepted **exactly once**, none is
    rejected as malformed, and every accepted file still carries the whole
    payload its sender wrote (a 400-character `evidence` entry, large enough
    that a torn read could not go unnoticed).

    Small on purpose: two senders, twenty Events each. Measured at four
    senders x sixty during the Sprint that wrote it — 240 Events, 240
    accepted, 0 duplicates, 0 rejected, 0 torn — and the assertions are the
    same at any N.
    """

    SENDERS = 2
    PER_SENDER = 20
    DEADLINE_SECONDS = 90

    SENDER_SOURCE = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[5])
from events import create_event
from transport.onedrive import OneDriveTransport

sync, outgoing, prefix, count = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
transport = OneDriveTransport(sync_folder=sync, outgoing_dir=outgoing)
for i in range(count):
    transport.send(create_event(
        source="DESKTOP_1", role="CTO_BACKEND", project_id="RACE",
        event_type="MILESTONE_COMPLETED", status="IN_PROGRESS",
        summary="race event", milestone="M", history_candidate=True,
        timestamp="2026-08-01T10:00:00+09:00",
        event_id="%s-%04d" % (prefix, i), evidence=["e" * 400],
    ))
"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sync = self.root / "sync"
        self.sync.mkdir(parents=True)
        self.incoming = self.root / "incoming"
        self.processed = self.root / "processed"
        self.rejected = self.root / "rejected"
        self.script = self.root / "race_sender.py"
        self.script.write_text(self.SENDER_SOURCE, encoding="utf-8")

    def _drain(self):
        """One intake + one Collector pass, exactly as the Runner does."""
        from collector import Collector
        from collector import run_once as collector_run_once
        from collector.state import PersistentSeenEventStore

        summary = run_intake(
            transport_dir=self.sync,
            incoming_dir=self.incoming,
            processed_dir=self.processed,
            rejected_dir=self.rejected,
            stable_after_seconds=0,
        )
        result = collector_run_once(
            collector=Collector(
                seen_store=PersistentSeenEventStore(
                    state_path=self.root / "collector_state.json"
                )
            ),
            incoming_dir=self.incoming,
            processed_dir=self.processed,
            rejected_dir=self.rejected,
            log_path=self.root / "collector.log",
        )
        return summary, result

    def _run_the_race(self):
        import subprocess

        src = str(Path(__file__).resolve().parents[1] / "src")
        processes = [
            subprocess.Popen(
                [sys.executable, str(self.script), str(self.sync),
                 str(self.root / f"out{i}"), f"R{i}", str(self.PER_SENDER), src],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for i in range(self.SENDERS)
        ]
        self.addCleanup(lambda: [p.kill() for p in processes if p.poll() is None])

        accepted, rejected, failed = [], [], []
        self.overlapped = 0
        deadline = time.time() + self.DEADLINE_SECONDS
        while time.time() < deadline:
            summary, result = self._drain()
            for entry in result.files:
                if entry.outcome.value == "ACCEPTED":
                    accepted.append(entry.destination_path.name)
                elif entry.outcome.value == "REJECTED":
                    rejected.append(entry.source_path.name)
                elif entry.outcome.value == "FAILED":
                    failed.append(entry.source_path.name)
            senders_done = all(p.poll() is not None for p in processes)
            if summary.moved and not senders_done:
                self.overlapped += len(summary.moved)
            if senders_done and not summary.moved and not result.files:
                break

        for process in processes:
            _out, err = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, err)

        # The senders may have finished after the last drain; settle.
        for _ in range(3):
            _summary, result = self._drain()
            for entry in result.files:
                if entry.outcome.value == "ACCEPTED":
                    accepted.append(entry.destination_path.name)
                elif entry.outcome.value == "REJECTED":
                    rejected.append(entry.source_path.name)

        return accepted, rejected, failed

    def test_every_event_arrives_exactly_once_while_the_senders_are_writing(self):
        accepted, rejected, failed = self._run_the_race()

        expected = {
            f"R{s}-{i:04d}.json"
            for s in range(self.SENDERS)
            for i in range(self.PER_SENDER)
        }

        self.assertEqual(rejected, [], "a file was promoted before it was complete")
        self.assertEqual(failed, [])
        self.assertEqual(set(accepted), expected)
        self.assertEqual(len(accepted), len(expected), "an Event was accepted twice")
        self.assertEqual(sorted(p.name for p in self.sync.glob("*")), [])
        self.assertEqual(sorted(p.name for p in self.incoming.glob("*")), [])
        # And the test was not vacuous: at least one Event was promoted out
        # of the folder while a sender process was still writing into it.
        # Without this the whole class degrades, silently, into "drain a
        # folder nobody is touching" — which the rest of this file already
        # covers.
        self.assertGreater(
            self.overlapped, 0, "no drain overlapped a live sender; the race never happened"
        )

    def test_no_accepted_file_carries_a_torn_payload(self):
        """The read the stability window would normally have hidden."""
        accepted, _rejected, _failed = self._run_the_race()

        for name in sorted(set(accepted)):
            with self.subTest(event=name):
                data = json.loads((self.processed / name).read_text(encoding="utf-8"))
                self.assertEqual(data["event_id"] + ".json", name)
                self.assertEqual(data["evidence"], ["e" * 400])


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
