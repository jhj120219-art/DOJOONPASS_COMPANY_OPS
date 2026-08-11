"""`src/history/reconciliation.py` — detection of BACKLOG A-20's loss window.

The pipeline consumes an Event before it records the Candidate built from
it. Anything that ends the run between those two points leaves the Event in
`processed/` with its `event_id` already marked seen, so no later run
reconsiders it and it never reaches Company History.

That window is not closed here — closing it is a Collector contract change
or a new recovery pass, both decisions (A-20). What is closed is the
question A-20 records as unanswered: **which Event went missing.** The Run
Manifest says a run failed and names the aborting component; it names no
Event.

Detection only. These tests also pin that it stays that way: a module that
quietly re-processed an orphan would be deciding A-20 by implementation.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from events import create_event  # noqa: E402
from history import HistoryDecision  # noqa: E402
from history.file_repository import safe_candidate_filename  # noqa: E402
from history.reconciliation import find_orphaned_events  # noqa: E402
from reporter.local_output import safe_event_filename  # noqa: E402


class ReconciliationTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.processed = self.root / "processed"
        self.keep = self.root / "keep"
        self.review = self.root / "review"
        for directory in (self.processed, self.keep, self.review):
            directory.mkdir(parents=True)

    def _event(self, event_id, event_type="MILESTONE_COMPLETED", **overrides):
        data = dict(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="PRJ",
            event_type=event_type,
            status="IN_PROGRESS",
            summary="reconciliation probe",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-01T10:00:00+09:00",
        )
        data.update(overrides)
        event = create_event(**data)
        # Same filename derivation the pipeline uses. Writing the raw
        # event_id here would fail for exactly the ids this suite needs to
        # cover — an unsafe one is not a legal filename.
        (self.processed / safe_event_filename(event_id)).write_text(
            event.to_json(), encoding="utf-8"
        )
        return event

    def _write_candidate(self, event_id, directory):
        path = directory / safe_candidate_filename(f"HIST-{event_id}")
        path.write_text(json.dumps({"history_id": f"HIST-{event_id}"}), encoding="utf-8")
        return path

    def _run(self):
        return find_orphaned_events(
            processed_dir=self.processed, keep_dir=self.keep, review_dir=self.review
        )


class OrphanDetectionTests(ReconciliationTestCase):
    def test_a_kept_event_with_its_candidate_is_clean(self):
        self._event("E-OK-1")
        self._write_candidate("E-OK-1", self.keep)

        result = self._run()

        self.assertTrue(result.is_clean)
        self.assertEqual(result.checked, 1)

    def test_a_kept_event_without_its_candidate_is_orphaned(self):
        """The A-20 signature, reproduced on the real runtime in C15:
        `processed/fi-crash.json` with `history_candidates/keep/` empty."""
        self._event("E-LOST-1")

        result = self._run()

        self.assertEqual(len(result.orphaned), 1)
        orphan = result.orphaned[0]
        self.assertEqual(orphan.event_id, "E-LOST-1")
        self.assertEqual(orphan.decision, HistoryDecision.KEEP)
        self.assertEqual(orphan.expected_candidate_path.parent, self.keep)

    def test_a_review_event_without_its_candidate_is_orphaned(self):
        """REVIEW candidates are stored too, so their absence is a loss for
        the same reason — the decision context never reaches a human."""
        self._event("E-REVIEW-1", event_type="BLOCKED", blocker="waiting")

        result = self._run()

        self.assertEqual(len(result.orphaned), 1)
        self.assertEqual(result.orphaned[0].decision, HistoryDecision.REVIEW)
        self.assertEqual(result.orphaned[0].expected_candidate_path.parent, self.review)

    def test_a_dropped_event_is_never_reported(self):
        """`FileHistoryRepository.save()` stores only KEEP and REVIEW, so a
        DROP Event correctly has no Candidate. Reporting it would make the
        common case look broken — the fastest way to get this check
        ignored."""
        self._event("E-DROP-1", event_type="STARTED")
        self._event("E-DROP-2", history_candidate=False)

        result = self._run()

        self.assertTrue(result.is_clean, f"unexpected orphans: {result.orphaned}")
        self.assertEqual(result.checked, 2)

    def test_only_the_missing_one_is_reported(self):
        self._event("E-MIX-1")
        self._write_candidate("E-MIX-1", self.keep)
        self._event("E-MIX-2")
        self._event("E-MIX-3", event_type="STARTED")

        result = self._run()

        self.assertEqual([o.event_id for o in result.orphaned], ["E-MIX-2"])
        self.assertEqual(result.checked, 3)

    def test_a_sanitised_event_id_still_matches_its_candidate(self):
        """The repository writes through `safe_candidate_filename()`, so a
        naive `HIST-{event_id}.json` lookup would report a present Candidate
        as missing for every id containing a path-unsafe character."""
        event_id = "E/UNSAFE:ID*1"
        self._event(event_id)
        self._write_candidate(event_id, self.keep)

        result = self._run()

        self.assertTrue(result.is_clean, f"false orphan: {result.orphaned}")


class UnreadableEventTests(ReconciliationTestCase):
    def test_an_unreadable_processed_file_is_reported_separately(self):
        """"We cannot tell whether this one is missing" is a different
        statement from "this one is missing". Collapsing them would inflate
        a number an operator is meant to act on."""
        (self.processed / "broken.json").write_text("{not json", encoding="utf-8")

        result = self._run()

        self.assertEqual(result.orphaned, ())
        self.assertEqual(len(result.unreadable), 1)
        self.assertFalse(result.is_clean)

    def test_an_unreadable_file_does_not_stop_the_scan(self):
        """One damaged file must not hide every other orphan behind it."""
        (self.processed / "broken.json").write_text("{not json", encoding="utf-8")
        self._event("E-AFTER-1")

        result = self._run()

        self.assertEqual([o.event_id for o in result.orphaned], ["E-AFTER-1"])
        self.assertEqual(len(result.unreadable), 1)


class DetectionOnlyTests(ReconciliationTestCase):
    """A-20's fix is a decision. This module must not make it by accident."""

    def test_the_scan_changes_nothing_on_disk(self):
        self._event("E-READONLY-1")
        self._event("E-READONLY-2", event_type="STARTED")
        before = {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

        self._run()

        after = {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)

    def test_no_candidate_is_created_for_an_orphan(self):
        """The tempting "helpful" repair. Writing the Candidate here would
        re-enter Company History from a status view, bypassing the pipeline
        that owns that decision."""
        self._event("E-NOREPAIR-1")

        self._run()

        self.assertEqual(list(self.keep.glob("*.json")), [])
        self.assertEqual(list(self.review.glob("*.json")), [])

    def test_the_module_performs_no_writes_at_all(self):
        """Source-level guard: a future edit that adds a repair path has to
        remove this assertion deliberately."""
        import inspect

        import history.reconciliation as reconciliation

        source = inspect.getsource(reconciliation)
        for write in ("write_text(", "write_bytes(", "mkdir(", "os.replace", "unlink("):
            with self.subTest(call=write):
                self.assertNotIn(write, source)

    def test_a_missing_processed_directory_is_not_an_error(self):
        """Desktop 1/2/3 have no processed/ at all — a status view must not
        raise there."""
        result = find_orphaned_events(
            processed_dir=self.root / "nope",
            keep_dir=self.keep,
            review_dir=self.review,
        )

        self.assertTrue(result.is_clean)
        self.assertEqual(result.checked, 0)


class ThreadedReadEquivalenceTests(ReconciliationTestCase):
    """Threading this scan changed how long it takes, never what it finds.

    The reads run in a pool because a file open costs ~5.3 ms on this
    machine and `ops_status.py` runs this on every invocation — measured
    serially, 20,000 events took 116 s. The same trade, with the same
    measurement behind it, was already adopted in `app/desktop_activity.py`.

    An optimisation on a correctness check is only safe if the result is
    provably identical, so this compares the real implementation against a
    deliberately serial one over the same tree.
    """

    def _serial(self):
        """What the pre-threading loop did, written out."""
        from events import Event
        from history.filter import HistoryFilter
        from history.result import HistoryDecision

        orphans = []
        for path in sorted(self.processed.glob("*.json")):
            try:
                event = Event.from_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            decision = HistoryFilter().evaluate(event).decision
            if decision is HistoryDecision.KEEP:
                target = self.keep
            elif decision is HistoryDecision.REVIEW:
                target = self.review
            else:
                continue
            expected = target / safe_candidate_filename(f"HIST-{event.event_id}")
            if not expected.exists():
                orphans.append(event.event_id)
        return orphans

    def test_threaded_and_serial_agree_including_order(self):
        for i in range(60):
            self._event(f"E-{i:03d}", event_type="MILESTONE_COMPLETED")
            if i % 3:
                self._write_candidate(f"E-{i:03d}", self.keep)
        for i in range(60, 75):
            self._event(f"E-{i:03d}", event_type="STARTED")
        for i in range(75, 85):
            self._event(f"E-{i:03d}", event_type="BLOCKED", blocker="b")
        (self.processed / "damaged.json").write_text("{not json", encoding="utf-8")

        threaded = [o.event_id for o in self._run().orphaned]

        self.assertEqual(threaded, self._serial())
        self.assertTrue(threaded, "the fixture produced no orphans to compare")

    def test_an_empty_directory_needs_no_pool(self):
        result = self._run()

        self.assertTrue(result.is_clean)
        self.assertEqual(result.checked, 0)


if __name__ == "__main__":
    unittest.main()
