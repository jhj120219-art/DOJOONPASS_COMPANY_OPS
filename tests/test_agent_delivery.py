"""`src/agent/delivery.py` — detection of BACKLOG E-9/E-9b delivery failures.

`sent/` records that `transport.send()` did not raise. It does not record
that the Event reached the sync folder in readable form, and those are
different claims: `OneDriveTransport.send()` skips writing when the
destination already exists in ANY shape, and the OneDrive client produces
such shapes on its own — Files On-Demand placeholders are 0 bytes,
interrupted transfers truncate, conflict resolution leaves residue.

Measured end to end on a real Agent run before this module existed:

    sync folder file                 still 0 bytes  -- never delivered
    agent/sent/                      contains the event_id
    last_successful_collection_date  advanced past that date
    Agent exit code / log            0 / COLLECTED
    any warning anywhere             none

The delivery contract is NOT changed here — fixing it means overwriting a
sync-folder entry, which is the race E-9 is blocked on. What changes is
that the failure is now visible.

The design question this module has to get right is **absence**. Once
Desktop 4 promotes a file out of the sync folder it is legitimately gone,
and that is the steady state — so a missing destination must never be
reported. Only a destination that is present and is not the Event is
reportable. Both halves are asserted below, because a checker that cried
wolf on every consumed Event would be turned off within a day.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent.delivery import DeliveryProblem, find_undelivered_events  # noqa: E402
from events import create_event  # noqa: E402
from transport.onedrive import safe_event_filename  # noqa: E402


class DeliveryTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sent = self.root / "sent"
        self.sync = self.root / "sync"
        self.sent.mkdir()
        self.sync.mkdir()

    def _event(self, event_id="E-1"):
        return create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="PRJ",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="delivery probe",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-01T10:00:00+09:00",
        )

    def _file_as_sent(self, event):
        (self.sent / safe_event_filename(event.event_id)).write_text(
            event.to_json(), encoding="utf-8"
        )

    def _destination(self, event):
        return self.sync / safe_event_filename(event.event_id)

    def _run(self):
        return find_undelivered_events(sent_dir=self.sent, sync_folder=self.sync)


class CleanCaseTests(DeliveryTestCase):
    """The two shapes that must never be reported."""

    def test_a_delivered_event_is_clean(self):
        event = self._event()
        self._file_as_sent(event)
        self._destination(event).write_text(event.to_json(), encoding="utf-8")

        result = self._run()

        self.assertTrue(result.is_clean)
        self.assertEqual(result.checked, 1)
        self.assertEqual(result.absent, 0)

    def test_a_consumed_event_is_clean_and_counted_as_absent(self):
        """Desktop 4 promoting the file out of the sync folder is the
        STEADY STATE, not a fault. Reporting it would make every healthy
        deployment permanently noisy — and a checker that fires on the
        normal case gets switched off."""
        event = self._event()
        self._file_as_sent(event)

        result = self._run()

        self.assertTrue(result.is_clean)
        self.assertEqual(result.absent, 1)

    def test_a_sanitised_event_id_still_matches_its_destination(self):
        """`send()` writes through `safe_event_filename()`. A naive
        `f"{event_id}.json"` lookup would report every delivered Event with
        a path-unsafe id as undelivered."""
        event = self._event("E/UNSAFE:ID*1")
        self._file_as_sent(event)
        self._destination(event).write_text(event.to_json(), encoding="utf-8")

        self.assertTrue(self._run().is_clean)


class FailureShapeTests(DeliveryTestCase):
    """Every shape `send()`'s existence short-circuit lets through.

    Each was measured against the real `OneDriveTransport.send()`: all four
    return without raising, and the Agent files the Event as sent.
    """

    def test_a_zero_byte_placeholder_is_detected(self):
        """The Files On-Demand shape — the one reproduced end to end."""
        event = self._event()
        self._file_as_sent(event)
        self._destination(event).write_bytes(b"")

        result = self._run()

        self.assertEqual(len(result.undelivered), 1)
        self.assertEqual(result.undelivered[0].problem, DeliveryProblem.EMPTY)
        self.assertEqual(result.undelivered[0].event_id, "E-1")

    def test_a_directory_at_the_destination_is_detected(self):
        event = self._event()
        self._file_as_sent(event)
        self._destination(event).mkdir()

        result = self._run()

        self.assertEqual(result.undelivered[0].problem, DeliveryProblem.NOT_A_FILE)

    def test_unrelated_content_is_detected(self):
        event = self._event()
        self._file_as_sent(event)
        self._destination(event).write_text("not an event", encoding="utf-8")

        result = self._run()

        self.assertEqual(result.undelivered[0].problem, DeliveryProblem.UNREADABLE)

    def test_a_different_event_at_the_destination_is_detected(self):
        """Valid JSON, valid Event, wrong Event — the shape a filename or
        parse check alone would pass."""
        event = self._event()
        self._file_as_sent(event)
        self._destination(event).write_text(
            self._event("SOMEONE-ELSE").to_json(), encoding="utf-8"
        )

        result = self._run()

        self.assertEqual(result.undelivered[0].problem, DeliveryProblem.DIFFERENT_EVENT)


class AccuracyTests(DeliveryTestCase):
    def test_mixed_state_is_counted_exactly(self):
        """Ground-truth arithmetic, matching the live measurement:
        checked == absent + undelivered + clean, with no double counting."""
        delivered = self._event("E-OK")
        self._file_as_sent(delivered)
        self._destination(delivered).write_text(delivered.to_json(), encoding="utf-8")

        consumed = self._event("E-GONE")
        self._file_as_sent(consumed)

        broken = self._event("E-BROKEN")
        self._file_as_sent(broken)
        self._destination(broken).write_bytes(b"")

        result = self._run()

        self.assertEqual(result.checked, 3)
        self.assertEqual(result.absent, 1)
        self.assertEqual([u.event_id for u in result.undelivered], ["E-BROKEN"])
        clean = result.checked - result.absent - len(result.undelivered)
        self.assertEqual(clean, 1)

    def test_a_damaged_sent_record_yields_no_verdict(self):
        """The local record is the source of the event_id. If it cannot be
        read, "delivered" and "not delivered" are both unfounded — inventing
        either would be worse than saying nothing."""
        (self.sent / "damaged.json").write_text("{not json", encoding="utf-8")

        result = self._run()

        self.assertTrue(result.is_clean)
        self.assertEqual(result.checked, 0)

    def test_an_unset_sync_folder_is_not_an_error(self):
        """Desktop 4 runs `ops_status` without an Agent sync folder."""
        result = find_undelivered_events(
            sent_dir=self.sent, sync_folder=self.root / "nope"
        )

        self.assertTrue(result.is_clean)
        self.assertEqual(result.checked, 0)


class DetectionOnlyTests(DeliveryTestCase):
    """E-9's fix is a race decision. This module must not make it."""

    def test_the_scan_changes_nothing_on_disk(self):
        event = self._event()
        self._file_as_sent(event)
        self._destination(event).write_bytes(b"")
        before = {
            str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() else b"<dir>")
            for p in self.root.rglob("*")
        }

        self._run()

        after = {
            str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() else b"<dir>")
            for p in self.root.rglob("*")
        }
        self.assertEqual(before, after)

    def test_the_module_never_re_sends(self):
        """Re-sending means overwriting a sync-folder entry — precisely the
        race E-9 is blocked on. A source-level guard so a future edit has to
        remove this deliberately.

        Checked against the AST rather than the source text: this module's
        docstring necessarily *discusses* `transport.send()` and writing, so
        a substring search flags its own explanation. A guard that fires on
        prose would be removed for being wrong rather than fixed.
        """
        import ast
        import inspect

        import agent.delivery as delivery

        tree = ast.parse(inspect.getsource(delivery))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called.add(getattr(func, "attr", None) or getattr(func, "id", None))

        for mutating in ("send", "write_text", "write_bytes", "replace", "unlink", "mkdir"):
            with self.subTest(call=mutating):
                self.assertNotIn(mutating, called)


class ThreadedReadEquivalenceTests(DeliveryTestCase):
    """Same trade, same guard as `history/reconciliation.py`: the pool
    changed how long this takes, never what it reports.

    Measured serially, 20,000 sent records took 118 s; `ops_status.py` runs
    this every time it is invoked.
    """

    def _serial(self):
        results = []
        for record in sorted(self.sent.glob("*.json")):
            try:
                event_id = json.loads(record.read_text(encoding="utf-8"))["event_id"]
            except Exception:
                continue
            destination = self.sync / safe_event_filename(event_id)
            if not destination.exists():
                continue
            if not destination.is_file():
                results.append(event_id)
                continue
            try:
                raw = destination.read_text(encoding="utf-8")
                if not raw.strip() or json.loads(raw).get("event_id") != event_id:
                    results.append(event_id)
            except Exception:
                results.append(event_id)
        return results

    def test_threaded_and_serial_agree_including_order(self):
        for i in range(40):
            event = self._event(f"E-{i:03d}")
            self._file_as_sent(event)
            if i % 4 == 0:
                self._destination(event).write_bytes(b"")          # undelivered
            elif i % 4 == 1:
                self._destination(event).write_text(event.to_json(), encoding="utf-8")
            elif i % 4 == 2:
                self._destination(event).write_text("junk", encoding="utf-8")  # undelivered
            # i % 4 == 3 -> absent (consumed)
        (self.sent / "damaged.json").write_text("{not json", encoding="utf-8")

        threaded = [u.event_id for u in self._run().undelivered]

        self.assertEqual(threaded, self._serial())
        self.assertTrue(threaded, "the fixture produced no failures to compare")

    def test_counts_are_consistent_under_threading(self):
        for i in range(30):
            event = self._event(f"E-{i:03d}")
            self._file_as_sent(event)
            if i % 2:
                self._destination(event).write_text(event.to_json(), encoding="utf-8")

        result = self._run()

        self.assertEqual(result.checked, 30)
        self.assertEqual(result.absent, 15)
        self.assertEqual(result.undelivered, ())


if __name__ == "__main__":
    unittest.main()
