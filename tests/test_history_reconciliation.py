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
from collector.state import PersistentSeenEventStore  # noqa: E402
from events import Event  # noqa: E402
from history.filter import HistoryFilter  # noqa: E402


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


class RetentionErasesTheEvidenceOfALossTests(unittest.TestCase):
    """A-20's detector depends on an artifact B-6 is deciding whether to
    delete. NOT FIXED; characterised, because the two are one decision.

    A-20/BUG-25: the Collector marks an `event_id` seen and moves the file to
    `processed/` before step 5 writes the History Candidate. Anything that
    ends the run between those two points loses the Event from Company
    History permanently — the seen store means no later run reconsiders it.

    `find_orphaned_events()` is the answer to "which Event went missing", and
    it works by scanning `processed/` and recomputing the decision from the
    Event itself. **The Event file is the evidence.** The seen store, which
    is what makes the loss permanent, holds only ids.

    B-6 (보존 정책) is the open decision about deleting `processed/`,
    `sent/`, `transport/`, `rejected/` and the collector state, all of which
    grow without bound. Measured here:

        processed file present   find_orphaned_events -> ['EVT-LOST']
        seen store               knows EVT-LOST
        file deleted (retention) find_orphaned_events -> []
        seen store               STILL knows EVT-LOST

    So retention would not lose any *data* that was not already lost — but it
    would erase the only record that it *was* lost, while leaving the seen
    store entry that guarantees no run will ever bring it back. A loss that
    was detectable becomes a loss that is not.

    **Why the detector cannot simply read the seen store instead.** The store
    holds ids and nothing else. `find_orphaned_events()` recomputes each
    Event's decision with `HistoryFilter` precisely because nothing records
    it, and a DROP Event correctly has no Candidate. Without the Event's
    content there is no way to tell "lost" from "correctly dropped", and DROP
    is the common case — a seen-store-based detector would report every
    dropped Event as an orphan. The dependency on `processed/` is not an
    oversight; it is what makes the report accurate.

    **What this adds to B-6:** whichever way retention is decided, `processed/`
    is not merely a duplicate-suppression aid. It is A-20's evidence, and
    deleting it silently narrows what the system can still tell an operator
    about a loss that already happened.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.processed = self.root / "processed"
        self.keep = self.root / "keep"
        self.review = self.root / "review"
        for directory in (self.processed, self.keep, self.review):
            directory.mkdir(parents=True)

    def _consumed_event(self, event_id="EVT-LOST"):
        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="PRJ",
            event_type="MILESTONE_COMPLETED", status="COMPLETED",
            summary="work that is now lost", history_candidate=True,
            event_id=event_id, timestamp="2026-08-10T10:00:00+09:00",
        )
        path = self.processed / f"{event_id}.json"
        path.write_text(event.to_json(), encoding="utf-8")
        store = PersistentSeenEventStore(state_path=self.root / "collector_state.json")
        store.mark_seen(event_id)
        return path, store

    def _orphans(self):
        result = find_orphaned_events(
            processed_dir=self.processed, keep_dir=self.keep, review_dir=self.review
        )
        return [o.event_id for o in result.orphaned]

    def test_the_loss_is_detectable_while_the_event_file_survives(self):
        self._consumed_event()

        self.assertEqual(self._orphans(), ["EVT-LOST"])

    def test_deleting_the_processed_file_makes_the_loss_undetectable(self):
        path, _store = self._consumed_event()
        self.assertEqual(self._orphans(), ["EVT-LOST"])

        path.unlink()

        self.assertEqual(self._orphans(), [])

    def test_but_the_loss_itself_is_still_permanent(self):
        """The half that does not go away: the seen store still holds the id,
        so no run will reconsider the Event, and no Candidate exists."""
        path, store = self._consumed_event()

        path.unlink()

        self.assertTrue(store.is_seen("EVT-LOST"))
        self.assertFalse((self.keep / "HIST-EVT-LOST.json").exists())
        self.assertFalse((self.review / "HIST-EVT-LOST.json").exists())

    def test_the_seen_store_alone_cannot_replace_the_evidence(self):
        """Why the detector is not simply re-pointed at the seen store: a
        DROP Event is *correctly* without a Candidate, and the store cannot
        tell the two apart."""
        dropped = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="PRJ",
            event_type="STARTED", status="IN_PROGRESS",
            summary="routine", history_candidate=False,
            event_id="EVT-DROPPED", timestamp="2026-08-10T10:00:00+09:00",
        )
        (self.processed / "EVT-DROPPED.json").write_text(
            dropped.to_json(), encoding="utf-8"
        )
        self._consumed_event()

        # With the Events present the detector separates them correctly.
        self.assertEqual(self._orphans(), ["EVT-LOST"])

        # The seen store, which is all that would remain, holds both ids and
        # nothing that distinguishes them.
        store = PersistentSeenEventStore(state_path=self.root / "collector_state.json")
        store.mark_seen("EVT-DROPPED")
        self.assertTrue(store.is_seen("EVT-LOST"))
        self.assertTrue(store.is_seen("EVT-DROPPED"))

    def test_the_decision_is_recomputed_not_remembered(self):
        """The property that makes `processed/` sufficient evidence:
        `HistoryFilter.evaluate()` derives the decision from the Event alone,
        so the detector reaches the same verdict the lost run would have."""
        path, _store = self._consumed_event()
        event = Event.from_json(path.read_text(encoding="utf-8"))

        first = HistoryFilter().evaluate(event).decision
        second = HistoryFilter().evaluate(event).decision

        self.assertIs(first, second)
        self.assertIs(first, HistoryDecision.KEEP)


class TwoIdsThatDifferOnlyInCaseTests(unittest.TestCase):
    """E-22, the same-batch path — and the correction it forces.

    E-22 records the *cross-batch* case: `EVT-A` already downstream, `EVT-a`
    arrives, `run_intake()` calls it `skipped_already_present`, it is never
    collected, and — in E-22's words — there is *"no failed step, no
    abnormal exit code"*.

    **Both ids arriving in one batch is a different path, and that sentence
    is false for it.** Measured through the real `run_company_ops.py`, three
    Events in one batch (`twin`, `TWIN`, `ORDINARY`):

        run 1   process exit 2, manifest FAILED
                history_filter STEP_ABORTED / CRITICAL
                  FileExistsError: history candidate already stored: HIST-TWIN.json
                keep/   HIST-twin.json only
                daily/  empty — no Company History written at all
                remote  .gitkeep only
        run 2   process exit 0, manifest SUCCESS, Company History rendered
                and pushed

    Three facts E-22 does not carry:

    1. It is a **CRITICAL abort with exit 2**, not a silent skip.
    2. The abort happens *inside* step 5's loop, so **every Event after the
       collision in that batch loses its Candidate permanently** while being
       marked seen. `ORDINARY` had nothing to do with the collision.
    3. `find_orphaned_events()` **could not see the collided Event.**
       `safe_candidate_filename("HIST-TWIN")` resolves to the path
       `HIST-twin.json` already occupies, `is_file()` says yes, and the one
       detector for this loss reported clean. Measured before C89:
       `orphaned=['ORDINARY']` — `TWIN` missing from a list whose entire
       job is to name what Company History lost.

    **C89 fixed (3) and nothing else.** Making the ids collision-proof is
    E-22's decision and still SKIPped — every candidate change breaks an
    approved contract. What needed no decision was the detector: at most one
    of a colliding group can own that file, so the others are orphaned no
    matter what the filesystem says, and that is answerable from the Events
    alone.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.processed = self.root / "processed"
        self.keep = self.root / "keep"
        self.review = self.root / "review"
        for directory in (self.processed, self.keep, self.review):
            directory.mkdir(parents=True)
        self._files: dict[str, str] = {}

    def _event(self, event_id, **overrides):
        payload = {
            "schema_version": "1.0",
            "event_id": event_id,
            "timestamp": "2026-08-18T10:00:00+09:00",
            "source": "DESKTOP_1",
            "role": "CTO_BACKEND",
            "project_id": "PRJ-A",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "summary": f"work {event_id}",
            "milestone": "M",
            "blocker": None,
            "evidence": [],
            "history_candidate": True,
        }
        payload.update(overrides)
        # The *file* name must be distinct even when the ids differ only in
        # case -- a first draft used `event_id.lower()` and the two Events
        # folded into one file before the collision under test could happen.
        # A fixture the filesystem folds cannot demonstrate a fold.
        self._files[event_id] = f"evt{len(self._files):02d}.json"
        (self.processed / self._files[event_id]).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _candidate_for(self, event_id):
        """Write the Candidate the pipeline would write for `event_id`."""
        from history import FileHistoryRepository, HistoryFilter
        from events import Event

        payload = json.loads(
            (self.processed / self._files[event_id]).read_text(encoding="utf-8")
        )
        candidate = HistoryFilter().evaluate(Event.from_dict(payload)).candidate
        FileHistoryRepository(
            keep_dir=self.keep, review_dir=self.review
        ).save(candidate, overwrite=True)

    def _run(self):
        return find_orphaned_events(
            processed_dir=self.processed,
            keep_dir=self.keep,
            review_dir=self.review,
        )

    def test_the_filesystem_really_does_fold_the_two_names(self):
        """The premise. On a case-sensitive filesystem this whole class is
        about nothing, so it says so rather than failing obscurely."""
        (self.keep / "HIST-twin.json").write_text("{}", encoding="utf-8")
        if not (self.keep / "HIST-TWIN.json").exists():
            self.skipTest("case-sensitive filesystem; E-22 does not arise here")
        (self.keep / "HIST-twin.json").unlink()

    def test_the_collided_event_is_reported_even_though_the_file_is_there(self):
        """C89. Before it, this returned `['ORDINARY']` and `TWIN` — the
        Event that actually lost its Candidate to the collision — was
        absent."""
        self._event("twin")
        self._event("TWIN")
        self._candidate_for("twin")
        if not (self.keep / "HIST-TWIN.json").exists():
            self.skipTest("case-sensitive filesystem; E-22 does not arise here")

        result = self._run()

        self.assertEqual(
            sorted(o.event_id for o in result.orphaned), ["TWIN"],
            "the Event whose Candidate the collision took is not reported",
        )
        self.assertEqual(result.checked, 2)

    def test_the_survivor_is_not_reported(self):
        """Precision. One of the two does own that file, and calling it
        orphaned would be a false alarm on a correct state."""
        self._event("twin")
        self._event("TWIN")
        self._candidate_for("twin")
        if not (self.keep / "HIST-TWIN.json").exists():
            self.skipTest("case-sensitive filesystem")

        reported = {o.event_id for o in self._run().orphaned}

        self.assertNotIn("twin", reported)

    def test_two_files_carrying_one_id_are_not_a_collision(self):
        """The same `event_id` twice is a duplicate *file*, which
        `rollup.DuplicateEvent` reports and which is not a lost Event. This
        must not start firing on it."""
        self._event("solo")
        self._candidate_for("solo")
        # a second file, same id, different filename
        payload = (self.processed / self._files["solo"]).read_text(encoding="utf-8")
        (self.processed / "solo-copy.json").write_text(payload, encoding="utf-8")

        self.assertEqual(self._run().orphaned, ())

    def test_an_ordinary_orphan_is_still_reported(self):
        """The pre-existing behaviour, unchanged — and the Event that the
        measured abort orphaned as collateral."""
        self._event("ordinary")

        self.assertEqual(
            [o.event_id for o in self._run().orphaned], ["ordinary"]
        )

    def test_a_three_way_collision_reports_two(self):
        """At most one member of a colliding group can own the file, so a
        group of three loses two."""
        for event_id in ("trip", "TRIP", "TriP"):
            self._event(event_id)
        self._candidate_for("trip")
        if not (self.keep / "HIST-TRIP.json").exists():
            self.skipTest("case-sensitive filesystem")

        reported = sorted(o.event_id for o in self._run().orphaned)

        self.assertEqual(len(reported), 2, reported)
        self.assertNotIn("trip", reported)



if __name__ == "__main__":
    unittest.main()
