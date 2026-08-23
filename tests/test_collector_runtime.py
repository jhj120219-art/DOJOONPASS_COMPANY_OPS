import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector import (  # noqa: E402
    Collector,
    InMemorySeenEventStore,
    RuntimeOutcome,
    run_once,
)


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


class CollectorRuntimeTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.incoming = root / "incoming"
        self.processed = root / "processed"
        self.rejected = root / "rejected"
        self.log_path = root / "logs" / "collector.log"
        self.incoming.mkdir(parents=True)
        self.collector = Collector(seen_store=InMemorySeenEventStore())

    def _write_incoming(self, filename: str, content: str) -> Path:
        path = self.incoming / filename
        path.write_text(content, encoding="utf-8")
        return path

    def _run(self):
        return run_once(
            collector=self.collector,
            incoming_dir=self.incoming,
            processed_dir=self.processed,
            rejected_dir=self.rejected,
            log_path=self.log_path,
        )


class AcceptedFlowTests(CollectorRuntimeTestCase):
    def test_valid_event_moves_to_processed(self):
        self._write_incoming(
            "TEST-MILESTONE-001.json",
            json.dumps(sample_event_dict(), ensure_ascii=False),
        )
        summary = self._run()

        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.duplicate, 0)
        self.assertEqual(summary.rejected, 0)
        self.assertEqual(summary.failed, 0)
        self.assertFalse((self.incoming / "TEST-MILESTONE-001.json").exists())
        self.assertTrue((self.processed / "TEST-MILESTONE-001.json").exists())

    def test_moved_file_content_is_preserved_byte_for_byte(self):
        original = json.dumps(sample_event_dict(), ensure_ascii=False, indent=2)
        self._write_incoming("TEST-MILESTONE-001.json", original)
        self._run()
        moved = (self.processed / "TEST-MILESTONE-001.json").read_text(encoding="utf-8")
        self.assertEqual(moved, original)

    def test_korean_summary_survives_the_move(self):
        self._write_incoming(
            "TEST-KOREAN-001.json",
            json.dumps(
                sample_event_dict(event_id="TEST-KOREAN-001", summary="검색 UI 구현 완료"),
                ensure_ascii=False,
            ),
        )
        self._run()
        moved = json.loads((self.processed / "TEST-KOREAN-001.json").read_text(encoding="utf-8"))
        self.assertEqual(moved["summary"], "검색 UI 구현 완료")


class RejectedFlowTests(CollectorRuntimeTestCase):
    def test_malformed_json_moves_to_rejected(self):
        self._write_incoming("TEST-BAD-JSON-001.json", "{not valid json")
        summary = self._run()

        self.assertEqual(summary.rejected, 1)
        self.assertFalse((self.incoming / "TEST-BAD-JSON-001.json").exists())
        self.assertTrue((self.rejected / "TEST-BAD-JSON-001.json").exists())

    def test_missing_required_field_moves_to_rejected(self):
        data = sample_event_dict(event_id="TEST-MISSING-FIELD-001")
        del data["summary"]
        self._write_incoming("TEST-MISSING-FIELD-001.json", json.dumps(data, ensure_ascii=False))
        summary = self._run()

        self.assertEqual(summary.rejected, 1)
        self.assertTrue((self.rejected / "TEST-MISSING-FIELD-001.json").exists())

    def test_rejected_files_are_never_deleted(self):
        self._write_incoming("TEST-BAD-JSON-002.json", "not json at all")
        self._run()
        self.assertTrue((self.rejected / "TEST-BAD-JSON-002.json").exists())


class DuplicateFlowTests(CollectorRuntimeTestCase):
    def test_second_incoming_file_with_same_event_id_is_duplicate(self):
        data = sample_event_dict(event_id="TEST-DUPLICATE-001")
        self._write_incoming("a-first-delivery.json", json.dumps(data, ensure_ascii=False))
        self._write_incoming("b-second-delivery.json", json.dumps(data, ensure_ascii=False))

        summary = self._run()

        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.duplicate, 1)
        self.assertTrue((self.processed / "a-first-delivery.json").exists())
        self.assertTrue((self.processed / "b-second-delivery.json").exists())


class FailureIsolationTests(CollectorRuntimeTestCase):
    def test_one_bad_file_does_not_block_other_files(self):
        self._write_incoming("aa-broken.json", "{not valid json")
        self._write_incoming(
            "bb-good.json", json.dumps(sample_event_dict(event_id="TEST-GOOD-001"), ensure_ascii=False)
        )

        summary = self._run()

        self.assertEqual(summary.rejected, 1)
        self.assertEqual(summary.accepted, 1)
        self.assertTrue((self.processed / "bb-good.json").exists())

    def test_destination_collision_is_recorded_as_failed_not_overwritten(self):
        data = sample_event_dict(event_id="TEST-COLLISION-001")
        self.processed.mkdir(parents=True)
        existing = self.processed / "same-name.json"
        existing.write_text("PRE-EXISTING CONTENT", encoding="utf-8")

        self._write_incoming("same-name.json", json.dumps(data, ensure_ascii=False))
        summary = self._run()

        self.assertEqual(summary.failed, 1)
        self.assertEqual(existing.read_text(encoding="utf-8"), "PRE-EXISTING CONTENT")
        # original stays in incoming so a future run can retry once the collision is resolved
        self.assertTrue((self.incoming / "same-name.json").exists())

    def test_empty_incoming_directory_is_not_an_error(self):
        summary = self._run()
        self.assertEqual((summary.accepted, summary.duplicate, summary.rejected, summary.failed), (0, 0, 0, 0))
        self.assertEqual(summary.files, ())


class LoggingTests(CollectorRuntimeTestCase):
    def test_log_file_records_start_and_finish(self):
        self._write_incoming(
            "TEST-MILESTONE-001.json", json.dumps(sample_event_dict(), ensure_ascii=False)
        )
        self._run()

        log_contents = self.log_path.read_text(encoding="utf-8")
        self.assertIn("COLLECTOR START", log_contents)
        self.assertIn("ACCEPTED TEST-MILESTONE-001", log_contents)
        self.assertIn("COLLECTOR FINISHED", log_contents)


class LogInjectionTests(CollectorRuntimeTestCase):
    """collector.log was forgeable through `event_id` — reproduced, then fixed.

    Same untrusted input as BUG-6 in `app/runner.py`: the Event file crosses
    the OneDrive transport from another Desktop, and `Event.from_json()` puts
    no single-line constraint on `event_id`. Two call sites here interpolate
    it raw:

        _log(log_path, f"ACCEPTED {result.event.event_id}")
        _log(log_path, f"DUPLICATE {result.event.event_id}")

    This log is worse to forge than the Runner's, because the forged line is
    an *outcome claim*. The reproduction put

        2026-01-01T00:00:00+09:00 ACCEPTED EVT-TOTALLY-FINE

    into collector.log — a byte-for-byte plausible record of an Event that
    never existed, in the file an operator reads to decide whether collection
    is healthy.

    Fixed at the writer (`oplog.append_line`), not at the two call sites, so
    a third interpolation added later cannot reintroduce it.
    """

    FORGED_ID = "X\n2026-01-01T00:00:00+09:00 ACCEPTED EVT-TOTALLY-FINE"

    def _write_forged(self, filename="forged.json"):
        event = sample_event_dict()
        event["event_id"] = self.FORGED_ID
        return self._write_incoming(filename, json.dumps(event, ensure_ascii=False))

    def _log_lines(self):
        return self.log_path.read_text(encoding="utf-8").splitlines()

    def test_a_newline_in_event_id_does_not_forge_an_accepted_line(self):
        self._write_forged()

        summary = self._run()

        # The Event is still accepted and still logged — escaping removes the
        # forgery without removing the record.
        self.assertEqual(summary.accepted, 1)
        forged = [
            ln for ln in self._log_lines() if ln.startswith("2026-01-01T00:00:00+09:00")
        ]
        self.assertEqual(forged, [], f"a forged line survived: {self._log_lines()}")

    def test_the_injected_text_is_kept_inline_and_recoverable(self):
        self._write_forged()

        self._run()

        accepted = [ln for ln in self._log_lines() if " ACCEPTED " in ln]
        self.assertEqual(len(accepted), 1)
        self.assertIn("ACCEPTED X\\n2026-01-01T00:00:00+09:00", accepted[0])

    def test_a_duplicate_of_a_forged_id_is_also_safe(self):
        """The second raw-`event_id` call site. The same Event twice is a
        DUPLICATE, which takes a different branch to a different log line."""
        self._write_forged("first.json")
        self._run()
        self._write_forged("second.json")

        summary = self._run()

        self.assertEqual(summary.duplicate, 1)
        forged = [
            ln for ln in self._log_lines() if ln.startswith("2026-01-01T00:00:00+09:00")
        ]
        self.assertEqual(forged, [])

    def test_every_line_is_one_record(self):
        """The property that actually matters, stated directly: however many
        Events were processed, the log has exactly as many lines as records
        written — no Event can add an extra one."""
        self._write_forged()

        self._run()

        lines = self._log_lines()
        # START + PROCESSING + ACCEPTED + FINISHED
        self.assertEqual(len(lines), 4, f"unexpected line count: {lines}")
        for line in lines:
            self.assertRegex(line, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_an_ordinary_event_id_is_written_unchanged(self):
        """Escaping must not make the common case unreadable."""
        self._write_incoming(
            "TEST-MILESTONE-001.json", json.dumps(sample_event_dict(), ensure_ascii=False)
        )

        self._run()

        self.assertIn("ACCEPTED TEST-MILESTONE-001", "\n".join(self._log_lines()))


class RuntimePathSafetyTests(unittest.TestCase):
    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        runtime_module = Path(__file__).resolve().parents[1] / "src" / "collector" / "runtime.py"
        content = runtime_module.read_text(encoding="utf-8")
        for token in ("C:\\Users", "D:\\", "OneDrive\\"):
            self.assertNotIn(token, content, f"{token} found in {runtime_module}")


class EveryFileThatEntersIsAccountedForExactlyOnceTests(unittest.TestCase):
    """The tests above check the paths **one at a time** — valid goes to
    `processed/`, malformed to `rejected/`, a repeat is DUPLICATE, a
    destination collision is FAILED. Each says where one kind of file goes.

    None of them says that **every** file goes somewhere. A `continue` added
    tomorrow that skips a file without counting it satisfies all of them: the
    valid file still lands in `processed/`, the malformed one still lands in
    `rejected/`, and the skipped one is simply in nobody's assertion.

    So this states the invariant instead of the cases:

        entered  ==  accepted + duplicate + rejected + failed
        entered  ==  processed/ + rejected/ + still in incoming/

    Both are needed. The first catches a file moved without being counted;
    the second catches one deleted rather than moved.

    `incoming/` is not required to be empty: a destination collision is
    FAILED and deliberately **leaves** the file there, which is how the next
    run retries it. That is why the second line counts what is left rather
    than asserting zero.

    A DUPLICATE is filed into `processed/` under its incoming name, so two
    files can sit there for one `event_id` — that is conservation, not loss,
    and `rollup.DuplicateEvent` exists to report it.
    """

    BASE = {
        "schema_version": "1.0",
        "source": "DESKTOP_1",
        "role": "CTO_BACKEND",
        "project_id": "P",
        "event_type": "STARTED",
        "status": "IN_PROGRESS",
        "summary": "conservation",
        "history_candidate": True,
        "timestamp": "2026-08-05T09:00:00+09:00",
    }

    def _tree(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for name in ("incoming", "processed", "rejected", "logs"):
            (root / name).mkdir(parents=True)
        return root

    def _run(self, root, store=None):
        store = store or InMemorySeenEventStore()
        summary = run_once(
            collector=Collector(seen_store=store),
            incoming_dir=root / "incoming",
            processed_dir=root / "processed",
            rejected_dir=root / "rejected",
            log_path=root / "logs" / "collector.log",
        )
        return summary, store

    @staticmethod
    def _counts(root):
        return {
            name: len(list((root / name).iterdir()))
            for name in ("incoming", "processed", "rejected")
        }

    def _assert_conserved(self, root, entered, summary):
        counted = (
            summary.accepted + summary.duplicate + summary.rejected + summary.failed
        )
        self.assertEqual(
            counted,
            entered,
            f"{entered} files entered and {counted} were counted — one was "
            "handled without being reported in any bucket",
        )
        after = self._counts(root)
        placed = after["processed"] + after["rejected"] + after["incoming"]
        self.assertEqual(
            placed,
            entered,
            f"{entered} files entered and {placed} are on disk — a file was "
            f"neither moved nor left behind: {after}",
        )

    def _put(self, root, name, payload):
        (root / "incoming" / name).write_text(payload, encoding="utf-8")

    def _ok(self, event_id):
        return json.dumps(dict(self.BASE, event_id=event_id))

    def test_a_healthy_batch_is_conserved(self):
        root = self._tree()
        for index in range(3):
            self._put(root, f"e{index}.json", self._ok(f"E{index}"))
        summary, _ = self._run(root)

        self._assert_conserved(root, 3, summary)
        self.assertEqual(summary.accepted, 3)

    def test_a_mixed_batch_is_conserved(self):
        """One of each shape the Collector knows how to refuse."""
        root = self._tree()
        self._put(root, "good.json", self._ok("GOOD"))
        self._put(
            root,
            "invalid.json",
            json.dumps(dict(self.BASE, event_id="BAD", role=["CTO_BACKEND"])),
        )
        self._put(root, "unparseable.json", "{ broken")
        self._put(root, "empty.json", "")
        summary, _ = self._run(root)

        self._assert_conserved(root, 4, summary)
        self.assertEqual((summary.accepted, summary.rejected), (1, 3))

    def test_a_duplicate_is_conserved_not_dropped(self):
        root = self._tree()
        self._put(root, "first.json", self._ok("SAME"))
        summary, store = self._run(root)
        self._assert_conserved(root, 1, summary)

        self._put(root, "second.json", self._ok("SAME"))
        summary, _ = self._run(root, store)

        self.assertEqual(summary.duplicate, 1)
        after = self._counts(root)
        self.assertEqual(after["processed"], 2, "both files are kept")
        self.assertEqual(after["incoming"], 0)

    def test_a_collision_leaves_the_file_rather_than_losing_it(self):
        """FAILED is the one outcome that does not move the file, and the
        invariant has to allow for it or it would forbid the retry."""
        root = self._tree()
        (root / "processed" / "clash.json").write_text("{}", encoding="utf-8")
        self._put(root, "clash.json", self._ok("CLASH"))
        summary, _ = self._run(root)

        self.assertEqual(summary.failed, 1)
        after = self._counts(root)
        self.assertEqual(after["incoming"], 1, "left for the next run")
        self.assertEqual(
            summary.accepted + summary.duplicate + summary.rejected + summary.failed,
            1,
        )

    def test_the_invariant_can_actually_disagree(self):
        """Guards the guard. Both halves are equalities over numbers this
        test computes itself, so a mistake would make them trivially true —
        this shows each side moving on its own."""
        root = self._tree()
        for index in range(2):
            self._put(root, f"e{index}.json", self._ok(f"E{index}"))
        summary, _ = self._run(root)
        self._assert_conserved(root, 2, summary)

        with self.assertRaises(AssertionError):
            self._assert_conserved(root, 3, summary)

        (root / "processed" / "e0.json").unlink()
        with self.assertRaises(AssertionError):
            self._assert_conserved(root, 2, summary)


if __name__ == "__main__":
    unittest.main()
