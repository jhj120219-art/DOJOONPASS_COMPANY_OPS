"""Company Control Tower rollup tests (C46).

`src/controltower/rollup.py` is the first view in this project that answers a
*business* question rather than an operational one. Three properties matter
more than the shapes it returns, and each has its own class below:

    the state is FOLDED, not taken from the last Event
        a project BLOCKED on Monday and RESUMED on Wednesday is not blocked,
        and only replaying the sequence says so

    the rule comes from one place
        whether an Event opens or clears a blocker is docs/04 §20-28's, and
        `notion/properties._type_specific_properties()` is where it lives.
        This module reads that function's answer; a test drives both.

    nothing is invented
        Goal / Team Goal / Sprint / Task have no source in this system, and
        the module must not quietly fill them in.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controltower import (  # noqa: E402
    ROLE_FOR_SOURCE,
    UNSOURCED_LAYERS,
    build_company_rollup,
    read_events,
)
from controltower.rollup import RECENT_LIMIT  # noqa: E402
from events import ROLES, create_event  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=KST)

ROLE_CYCLE = ("CTO_BACKEND", "CTO_FRONTEND", "CMO", "COO")

SOURCE_FOR_ROLE = {
    "CTO_BACKEND": "DESKTOP_1",
    "CMO": "DESKTOP_2",
    "CTO_FRONTEND": "DESKTOP_3",
    "COO": "DESKTOP_4",
}


class ControlTowerTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.processed = Path(tmp.name) / "processed"
        self.processed.mkdir(parents=True)

    def put(self, event_id, project, role, event_type, status, day, **extra):
        # `day` places the Event; an explicit `timestamp` or `summary`
        # overrides the default built from it. Two Events on one day need
        # distinct instants to have an order at all, which the panels that
        # list them in order depend on.
        event = create_event(
            source=SOURCE_FOR_ROLE[role],
            role=role,
            project_id=project,
            event_type=event_type,
            status=status,
            summary=extra.pop("summary", None) or f"summary for {event_id}",
            history_candidate=True,
            event_id=event_id,
            timestamp=(
                extra.pop("timestamp", None) or f"2026-08-{day:02d}T09:00:00+09:00"
            ),
            **extra,
        )
        (self.processed / f"{event_id}.json").write_text(
            event.to_json(), encoding="utf-8"
        )
        return event

    def rollup(self, **kwargs):
        return build_company_rollup(processed_dir=self.processed, now=NOW, **kwargs)


class BlockerStateIsFoldedTests(ControlTowerTestCase):
    """The property a "latest Event wins" rollup gets wrong.

    Taking the newest Event's fields would report BRAND as blocked forever
    after one `BLOCKED`, because a `RESUMED` Event carries no blocker text of
    its own — the *absence* is the signal, and only replaying the sequence
    reads it. `ExecutionPlanSync` applies Events to the PROJECTS row in turn
    for exactly this reason.
    """

    def test_a_resumed_project_is_not_blocked(self):
        self.put("E1", "BRAND", "CMO", "BLOCKED", "BLOCKED", 6, blocker="budget")
        self.put("E2", "BRAND", "CMO", "RESUMED", "IN_PROGRESS", 12)

        project = self.rollup().project("BRAND")

        self.assertFalse(project.is_blocked)
        self.assertIsNone(project.open_blocker)
        self.assertEqual(self.rollup().risks, ())

    def test_a_project_blocked_again_after_resuming_is_blocked(self):
        self.put("E1", "BRAND", "CMO", "BLOCKED", "BLOCKED", 6, blocker="budget")
        self.put("E2", "BRAND", "CMO", "RESUMED", "IN_PROGRESS", 12)
        self.put("E3", "BRAND", "CMO", "BLOCKED", "BLOCKED", 14, blocker="legal review")

        project = self.rollup().project("BRAND")

        self.assertTrue(project.is_blocked)
        self.assertEqual(project.open_blocker, "legal review")
        self.assertEqual(project.open_blocker_since, "2026-08-14T09:00:00+09:00")

    def test_the_blocker_survives_events_that_do_not_touch_it(self):
        """`STARTED` / `DECISION_APPROVED` change no blocker state — §21/§28
        add no property at all — so a blocker must not be cleared by one."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker="vendor key")
        self.put("E2", "SEARCH", "CTO_BACKEND", "DECISION_APPROVED", "IN_PROGRESS", 11)

        project = self.rollup().project("SEARCH")

        self.assertTrue(project.is_blocked)
        self.assertEqual(project.open_blocker, "vendor key")
        self.assertEqual(project.open_blocker_since, "2026-08-09T09:00:00+09:00")

    def test_completing_a_project_clears_its_blocker(self):
        """§25 clears Blocker as well as writing Completed Date."""
        self.put("E1", "PAY", "CTO_FRONTEND", "BLOCKED", "BLOCKED", 6, blocker="waiting")
        self.put("E2", "PAY", "CTO_FRONTEND", "COMPLETED", "COMPLETED", 14)

        project = self.rollup().project("PAY")

        self.assertFalse(project.is_blocked)
        self.assertTrue(project.is_complete)
        self.assertEqual(project.completed_at, "2026-08-14T09:00:00+09:00")

    def test_issue_resolved_while_still_blocked_does_not_clear_it(self):
        """§27's own qualifier: an ISSUE_RESOLVED whose `status` is still
        BLOCKED does not unblock the project. The rule is read out of
        `_type_specific_properties()`, so this is really a test that this
        module asks it rather than guessing."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker="vendor key")
        self.put("E2", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "BLOCKED", 11)

        self.assertTrue(self.rollup().project("SEARCH").is_blocked)

    def test_issue_resolved_when_no_longer_blocked_does_clear_it(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker="vendor key")
        self.put("E2", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "IN_PROGRESS", 11)

        self.assertFalse(self.rollup().project("SEARCH").is_blocked)

    def test_the_fold_follows_the_event_instant_not_the_filename(self):
        """Files are read in name order; state must be folded in time order.
        Here the *earlier* filename carries the *later* Event."""
        self.put("A_LATER", "SEARCH", "CTO_BACKEND", "RESUMED", "IN_PROGRESS", 14)
        self.put("Z_EARLIER", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker="k")

        self.assertFalse(self.rollup().project("SEARCH").is_blocked)


class OneRuleForBlockerStateTests(ControlTowerTestCase):
    """The rollup and the Notion View must agree, because they are the same
    rule — not because someone kept two copies in step.

    Driven over every `event_type`: whatever
    `_type_specific_properties()` says about `Blocker`, the rollup does.
    """

    CASES = (
        ("STARTED", "IN_PROGRESS", None),
        ("BLOCKED", "BLOCKED", "the blocker text"),
        ("RESUMED", "IN_PROGRESS", None),
        ("DECISION_APPROVED", "IN_PROGRESS", None),
        ("MILESTONE_COMPLETED", "IN_PROGRESS", None),
        ("ISSUE_RESOLVED", "IN_PROGRESS", None),
        ("COMPLETED", "COMPLETED", None),
        ("CANCELLED", "CANCELLED", None),
    )

    def test_each_event_type_lands_where_the_notion_rule_puts_it(self):
        from notion.properties import _type_specific_properties

        for event_type, status, blocker in self.CASES:
            with self.subTest(event_type=event_type):
                shutil.rmtree(self.processed)
                self.processed.mkdir(parents=True)
                # Always start blocked, so "clears" and "leaves alone" are
                # distinguishable outcomes rather than both looking empty.
                self.put("E0", "P", "CTO_BACKEND", "BLOCKED", "BLOCKED", 1, blocker="original")
                event = self.put(
                    "E1", "P", "CTO_BACKEND", event_type, status, 5,
                    blocker=blocker,
                    milestone="M" if event_type == "MILESTONE_COMPLETED" else None,
                )

                properties = _type_specific_properties(event)
                project = self.rollup().project("P")

                if "Blocker" not in properties:
                    self.assertEqual(project.open_blocker, "original")
                elif not properties["Blocker"]["rich_text"]:
                    self.assertIsNone(project.open_blocker)
                else:
                    self.assertEqual(project.open_blocker, blocker)

    def test_completion_follows_the_same_function(self):
        from notion.properties import _type_specific_properties

        for event_type, status, _blocker in self.CASES:
            with self.subTest(event_type=event_type):
                shutil.rmtree(self.processed)
                self.processed.mkdir(parents=True)
                event = self.put(
                    "E1", "P", "CTO_BACKEND", event_type, status, 5,
                    blocker="b" if event_type == "BLOCKED" else None,
                    milestone="M" if event_type == "MILESTONE_COMPLETED" else None,
                )

                expected = "Completed Date" in _type_specific_properties(event)

                self.assertEqual(self.rollup().project("P").is_complete, expected)


class TraceabilityTests(ControlTowerTestCase):
    """A Control Tower number nobody can trace is a rumour."""

    def test_every_project_carries_the_files_it_was_built_from(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("E2", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5, milestone="M")

        project = self.rollup().project("SEARCH")

        self.assertEqual([ref.event_id for ref in project.evidence], ["E1", "E2"])
        self.assertEqual([ref.path for ref in project.evidence], ["E1.json", "E2.json"])
        for ref in project.evidence:
            with self.subTest(event=ref.event_id):
                self.assertTrue((self.processed / ref.path).is_file())

    def test_a_blocker_names_the_event_that_declared_it(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker="vendor key")

        risk = self.rollup().risks[0]

        self.assertEqual(risk.evidence.event_id, "E1")
        self.assertEqual(risk.evidence.path, "E1.json")
        self.assertIn("E1", risk.evidence.describe())
        self.assertEqual(risk.blocker, "vendor key")
        self.assertEqual(risk.days_open(NOW), 10)

    def test_every_metric_states_where_it_came_from(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5, milestone="M")

        for metric in self.rollup().metrics:
            with self.subTest(metric=metric.key):
                self.assertTrue(metric.source.strip(), metric.key)

    def test_the_counted_metrics_carry_the_files_they_counted(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5, milestone="M")
        self.put("E2", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "IN_PROGRESS", 6)

        rollup = self.rollup()

        for key in ("events", "milestones_completed", "issues_resolved", "open_blockers"):
            with self.subTest(metric=key):
                metric = rollup.metric(key)
                self.assertEqual(len(metric.evidence), metric.value, key)


class EveryMetricIsClassifiedByHowItCitesItsFilesTests(ControlTowerTestCase):
    """`Metric` says a Control Tower number nobody can trace is a rumour, and
    `evidence` is what makes it traceable. Three tests guarded that property
    and all three were maintained by hand.

        rollup, test_the_counted_metrics_carry_the_files_they_counted
            ("events", "milestones_completed", "issues_resolved", "open_blockers")
        rollup, test_every_counted_metric_still_carries_as_many_files_as_it_counts
            ("events", "projects_completed", "open_blockers")
        dashboard, test_every_row_carries_the_evidence_it_was_built_from
            `if panel.key in ("METRICS",): continue`  — the whole panel

    Union of the two key lists: **five of nine**. The third does not narrow,
    it skips the panel outright — to accommodate the two metrics that
    legitimately have no evidence, it stops checking the seven that do.

    Measured on a rollup with one of everything:

        events                     5 / 5     decisions_approved      1 / 1
        projects_active            3 / 0     issues_resolved         1 / 1
        projects_completed         1 / 1     open_blockers           1 / 1
        milestones_completed       1 / 1     teams_silent            1 / 0
                                             desktop_role_mismatches 1 / 1

    So `decisions_approved` and `desktop_role_mismatches` carry their files
    correctly and **nothing at either layer checks that they do**, and a
    tenth metric added tomorrow would land in neither list nor the skip.

    This sweeps instead. Every metric either counts its own evidence or is
    named below with the reason it cannot — the C66 lesson about rosters
    written by hand, applied to the layer that publishes the numbers.

    `evidence_count` is not internal: `dashboard._metrics_panel()` puts
    `len(metric.evidence)` on the row and `notion_projection` maps it to the
    shared `Evidence Count` property, so this is what a COO reads beside the
    number in Notion.
    """

    #: `key: why one ref per counted thing is impossible`. Not a permit to
    #: drop evidence — both entries count something other than Events, and
    #: `test_every_row_carries_the_evidence_it_was_built_from`'s own note
    #: already says inventing refs for them "would be the invention this
    #: module refuses".
    COUNTS_SOMETHING_OTHER_THAN_EVENTS = {
        "projects_active": (
            "distinct project_ids, not Events — many files produce one "
            "count, so one ref per unit does not exist"
        ),
        "teams_silent": (
            "an absence. A team that reported nothing has no files behind "
            "it, and citing the Events it did not send is not possible"
        ),
    }

    def _one_of_everything(self):
        """A rollup where every evidenced metric is non-zero.

        Without that this class passes on `0 == 0` for whichever metric the
        fixture happens not to exercise — the vacuous pass its own subject
        matter is about.
        """
        self.put("E1", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5, milestone="M")
        self.put("E2", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "IN_PROGRESS", 6)
        self.put("E3", "PAY", "CMO", "DECISION_APPROVED", "IN_PROGRESS", 7)
        self.put("E4", "OPSX", "COO", "BLOCKED", "BLOCKED", 8, blocker="waiting")
        self.put("E5", "PAY", "CMO", "COMPLETED", "COMPLETED", 9)

        # `put()` derives `source` from `role`, so a pair mismatch has to be
        # written directly — DESKTOP_1 owns CTO_BACKEND (docs/02 §8).
        mismatched = create_event(
            source="DESKTOP_1",
            role="CMO",
            project_id="SEARCH",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="claims a role its Desktop does not own",
            history_candidate=True,
            event_id="M1",
            timestamp="2026-08-04T09:00:00+09:00",
        )
        (self.processed / "M1.json").write_text(mismatched.to_json(), encoding="utf-8")
        return self.rollup()

    def test_the_fixture_exercises_every_evidenced_metric(self):
        """The vacuous-pass guard, first: a metric left at zero would satisfy
        `len(evidence) == value` without ever citing anything."""
        rollup = self._one_of_everything()
        zero = sorted(
            metric.key
            for metric in rollup.metrics
            if metric.key not in self.COUNTS_SOMETHING_OTHER_THAN_EVENTS
            and metric.value == 0
        )
        self.assertEqual(zero, [], f"these would pass on 0 == 0: {zero}")

    def test_the_sweep_sees_every_metric(self):
        """And guards against the sweep itself matching nothing."""
        rollup = self._one_of_everything()
        self.assertGreaterEqual(len(rollup.metrics), 9)
        self.assertEqual(
            len({metric.key for metric in rollup.metrics}), len(rollup.metrics)
        )

    def test_every_metric_counts_its_evidence_or_says_why_it_cannot(self):
        rollup = self._one_of_everything()
        wrong = sorted(
            f"{metric.key}: value={metric.value} evidence={len(metric.evidence)}"
            for metric in rollup.metrics
            if metric.key not in self.COUNTS_SOMETHING_OTHER_THAN_EVENTS
            and len(metric.evidence) != metric.value
        )
        self.assertEqual(
            wrong,
            [],
            "a KPI whose Evidence Count disagrees with its own value, and "
            f"no entry saying why: {wrong}",
        )

    def test_the_roster_names_nothing_that_is_gone(self):
        """The other direction. An entry for a metric that now carries its
        evidence — or no longer exists — is the stale claim this replaces."""
        rollup = self._one_of_everything()
        by_key = {metric.key: metric for metric in rollup.metrics}

        for key, why in self.COUNTS_SOMETHING_OTHER_THAN_EVENTS.items():
            with self.subTest(metric=key):
                self.assertIn(key, by_key, "rostered metric no longer exists")
                self.assertEqual(
                    len(by_key[key].evidence),
                    0,
                    f"{key} now carries evidence; drop it from the roster",
                )
                self.assertGreater(len(why), 40, "a reason, not a label")

    def test_every_cited_file_is_a_file(self):
        """The half the count cannot check. `Evidence Count` reaching Notion
        is only worth reading while the refs behind it resolve — the
        dashboard panel test asserts exactly this for every panel except the
        one it skips."""
        rollup = self._one_of_everything()

        for metric in rollup.metrics:
            for ref in metric.evidence:
                with self.subTest(metric=metric.key, path=ref.path):
                    self.assertTrue((self.processed / ref.path).is_file())

    def test_every_metric_says_where_it_came_from(self):
        """`source` is the sentence beside the number, and an empty one turns
        the row back into the rumour `Metric` opens by refusing."""
        for metric in self._one_of_everything().metrics:
            with self.subTest(metric=metric.key):
                self.assertGreater(len(metric.source.strip()), 10)


class EveryCitedFileIsAnInstanceOfWhatTheMetricCountsTests(ControlTowerTestCase):
    """The half `EveryMetricIsClassifiedByHowItCitesItsFilesTests` cannot see.

    That class asks two things of every KPI: `len(evidence) == value`, and
    that each cited path is a file. Both can hold while the citation is
    **wrong**. Measured — `milestones_completed` made to cite the
    `ISSUE_RESOLVED` files instead of its own:

        count still equal        every cited file still exists
        tests/test_controltower*.py  ->  480 passed

    Nothing in the Control Tower suite noticed. `Metric` opens by saying a
    number nobody can trace is a rumour; a number tracing to the wrong files
    is a rumour with a citation, which is worse — it is checkable and wrong,
    and `evidence_count` reaching Notion makes it look verified.

    So this reads each cited file back and asks whether that Event is an
    instance of what the metric counts.

    **The predicates are the production ones, not copies.** `_completes()`
    and `_blocker_change()` are what `_roll_metrics()` and `_roll_projects()`
    themselves use — asking them is asking the rule rather than a second
    opinion of it (the C28 rule), so this cannot drift from the code it
    checks. Where the rollup compares an `event_type` literal, so does this:
    that comparison **is** the contract, not an implementation of it.
    """

    #: metric key -> what a file it cites must be.
    #:
    #: Exhaustive by `test_every_metric_is_classified`: a tenth metric fails
    #: here until someone says what its evidence means.
    def _predicates(self):
        from controltower.rollup import _blocker_change, _completes

        return {
            # Counts Events as such, so any Event is a valid citation.
            "events": lambda event: True,
            "projects_completed": _completes,
            "milestones_completed": lambda event: event.event_type == "MILESTONE_COMPLETED",
            "decisions_approved": lambda event: event.event_type == "DECISION_APPROVED",
            "issues_resolved": lambda event: event.event_type == "ISSUE_RESOLVED",
            # The risk's evidence is the Event that *set* the blocker, which
            # is exactly what `_blocker_change()` reports a value for.
            "open_blockers": lambda event: _blocker_change(event)[1] is not None,
            "desktop_role_mismatches": (
                lambda event: ROLE_FOR_SOURCE.get(event.source) != event.role
            ),
        }

    #: Carry no evidence at all — the reason is in
    #: `EveryMetricIsClassifiedByHowItCitesItsFilesTests`, and there is
    #: nothing here for a predicate to be about.
    NO_EVIDENCE = ("projects_active", "teams_silent")

    def _one_of_everything(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5, milestone="M")
        self.put("E2", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "IN_PROGRESS", 6)
        self.put("E3", "PAY", "CMO", "DECISION_APPROVED", "IN_PROGRESS", 7)
        self.put("E4", "OPSX", "COO", "BLOCKED", "BLOCKED", 8, blocker="waiting")
        self.put("E5", "PAY", "CMO", "COMPLETED", "COMPLETED", 9)

        mismatched = create_event(
            source="DESKTOP_1",
            role="CMO",
            project_id="SEARCH",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="claims a role its Desktop does not own",
            history_candidate=True,
            event_id="M1",
            timestamp="2026-08-04T09:00:00+09:00",
        )
        (self.processed / "M1.json").write_text(mismatched.to_json(), encoding="utf-8")
        return self.rollup()

    def _event_at(self, ref):
        from events import Event

        return Event.from_json(
            (self.processed / ref.path).read_text(encoding="utf-8")
        )

    def test_every_metric_is_classified(self):
        """The roster is exhaustive or this class is decoration."""
        rollup = self._one_of_everything()
        known = set(self._predicates()) | set(self.NO_EVIDENCE)
        unclassified = sorted({m.key for m in rollup.metrics} - known)

        self.assertEqual(
            unclassified,
            [],
            f"a KPI whose evidence nobody has said the meaning of: {unclassified}",
        )

    def test_the_fixture_exercises_every_predicate(self):
        """Vacuous-pass guard. A metric left at zero cites nothing, and a
        predicate applied to nothing is a predicate never run."""
        rollup = self._one_of_everything()
        empty = sorted(
            key for key in self._predicates()
            if not rollup.metric(key).evidence
        )
        self.assertEqual(empty, [], f"these predicates were never applied: {empty}")

    def test_every_cited_file_is_an_instance_of_what_is_counted(self):
        rollup = self._one_of_everything()
        predicates = self._predicates()
        wrong = []

        for metric in rollup.metrics:
            predicate = predicates.get(metric.key)
            if predicate is None:
                continue
            for ref in metric.evidence:
                event = self._event_at(ref)
                if not predicate(event):
                    wrong.append(f"{metric.key} cites {ref.event_id} ({event.event_type})")

        self.assertEqual(
            wrong,
            [],
            "a KPI citing a file that is not an instance of what it counts — "
            f"traceable to the wrong evidence is worse than untraceable: {wrong}",
        )

    def test_the_check_would_notice_a_swapped_citation(self):
        """Guards the guard, and it is the whole point.

        The measured hole was `milestones_completed` citing the
        `ISSUE_RESOLVED` file: right count, real file, wrong Event. The
        predicate has to reject that pairing, or this class passes for the
        same reason the suite already did.
        """
        rollup = self._one_of_everything()
        predicates = self._predicates()

        milestone_ref = rollup.metric("milestones_completed").evidence[0]
        resolved_ref = rollup.metric("issues_resolved").evidence[0]

        self.assertTrue(
            predicates["milestones_completed"](self._event_at(milestone_ref)),
            "the honest pairing must pass",
        )
        self.assertFalse(
            predicates["milestones_completed"](self._event_at(resolved_ref)),
            "the swapped pairing must fail",
        )
        self.assertFalse(
            predicates["open_blockers"](self._event_at(milestone_ref)),
            "a milestone did not set a blocker",
        )
        self.assertFalse(
            predicates["desktop_role_mismatches"](self._event_at(milestone_ref)),
            "an Event whose Desktop owns its role is not a mismatch",
        )

    def test_the_ref_names_the_file_it_was_read_out_of(self):
        """`EvidenceRef` says it is "One Event, and the file it was read out
        of". A ref whose id or instant disagrees with the file at its path
        would send a reader to the wrong place while looking resolvable."""
        rollup = self._one_of_everything()

        for metric in rollup.metrics:
            for ref in metric.evidence:
                with self.subTest(metric=metric.key, ref=ref.event_id):
                    event = self._event_at(ref)
                    self.assertEqual(event.event_id, ref.event_id)
                    self.assertEqual(event.timestamp, ref.at)


class ThePredicatesTheEvidenceGateBorrowsAreThemselvesPinnedTests(ControlTowerTestCase):
    """`EveryCitedFileIsAnInstanceOfWhatTheMetricCountsTests` deliberately
    reuses `_completes()` and `_blocker_change()` rather than copying them,
    so it cannot drift from the rollup. That buys accuracy and costs
    independence: if either predicate were wrong, the gate would agree with
    it and both would be wrong together.

    Most of that risk is already covered one layer down.
    `test_spec_conformance.py::…EXPECTED_EXTRA` pins
    `notion/properties._type_specific_properties()` per event type against
    docs/04 §21-28, and asserts the mapping covers **every** member of
    `EVENT_TYPES` — so the table the two predicates read cannot quietly gain
    or lose a row.

    What that does not pin is the two-line step from that table to the
    predicate: **which key** each one looks for. `_completes()` is
    `"Completed Date" in _type_specific_properties(event)`; point it at
    `"Blocker"` instead and every BLOCKED project becomes a completed one,
    `projects_completed` counts them, and the evidence gate — reusing the
    same predicate — agrees.

    So this enumerates `EVENT_TYPES` independently and states the answer for
    each, from docs/04 rather than from the predicate.
    """

    #: docs/04 §25 is the only section that writes `Completed Date`. §26
    #: (CANCELLED) deliberately does not — a cancelled project is not a
    #: completed one, and `projects_completed` is a KPI a COO reads.
    COMPLETES = {"COMPLETED"}

    #: §22 sets a Blocker; §23, §25 and §27 clear it (§27 only when the
    #: Event's own status is no longer BLOCKED). Everything else leaves
    #: blocker state alone.
    SETS_A_BLOCKER = {"BLOCKED"}
    CLEARS_A_BLOCKER = {"RESUMED", "COMPLETED", "ISSUE_RESOLVED"}

    STATUS_FOR = {"COMPLETED": "COMPLETED", "CANCELLED": "CANCELLED", "BLOCKED": "BLOCKED"}

    def _event(self, event_type):
        return create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="SEARCH",
            event_type=event_type,
            status=self.STATUS_FOR.get(event_type, "IN_PROGRESS"),
            summary="predicate guard",
            milestone="M1" if event_type == "MILESTONE_COMPLETED" else None,
            blocker="B" if event_type == "BLOCKED" else None,
            history_candidate=True,
            event_id=f"PRED-{event_type}",
            timestamp="2026-08-05T09:00:00+09:00",
        )

    def test_this_guard_covers_every_event_type(self):
        """The roster is the schema's, not one written here — a ninth Event
        type has to be classified before this passes again."""
        from events import EVENT_TYPES

        self.assertEqual(
            self.COMPLETES | self.SETS_A_BLOCKER | self.CLEARS_A_BLOCKER
            | {"STARTED", "CANCELLED", "MILESTONE_COMPLETED", "DECISION_APPROVED"},
            set(EVENT_TYPES),
        )

    def test_exactly_one_event_type_completes_a_project(self):
        from controltower.rollup import _completes
        from events import EVENT_TYPES

        completing = {
            event_type
            for event_type in EVENT_TYPES
            if _completes(self._event(event_type))
        }
        self.assertEqual(completing, self.COMPLETES)

    def test_cancelled_is_not_completed(self):
        """Stated on its own because it is the one a reader would guess wrong,
        and because `projects_completed` is a number a COO acts on."""
        from controltower.rollup import _completes
        from events import EVENT_TYPES

        self.assertFalse(_completes(self._event("CANCELLED")))
        self.assertTrue(_completes(self._event("COMPLETED")))

    def test_exactly_the_documented_types_change_blocker_state(self):
        from controltower.rollup import _blocker_change
        from events import EVENT_TYPES

        sets_it, clears_it = set(), set()
        for event_type in EVENT_TYPES:
            changes, value = _blocker_change(self._event(event_type))
            if not changes:
                continue
            (sets_it if value else clears_it).add(event_type)

        self.assertEqual(sets_it, self.SETS_A_BLOCKER)
        self.assertEqual(clears_it, self.CLEARS_A_BLOCKER)

    def test_issue_resolved_still_blocked_does_not_clear_it(self):
        """§27's own exception, and the one case where the Event's `status`
        overrides its type. Kept here because `open_blockers` — and the
        evidence gate that borrows this predicate — both depend on it."""
        from controltower.rollup import _blocker_change

        still_blocked = create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="SEARCH",
            event_type="ISSUE_RESOLVED",
            status="BLOCKED",
            summary="one of several issues",
            history_candidate=True,
            event_id="PRED-IR-BLOCKED",
            timestamp="2026-08-05T09:00:00+09:00",
        )

        self.assertEqual(_blocker_change(still_blocked), (False, None))


class TheRiskNamesTheTeamThatReportedItTests(ControlTowerTestCase):
    """C48: `Risk.team` was "whichever team logged the newest Event".

    `_roll_risks()` read `project.teams[-1]`, and `ProjectRollup.teams` is
    *every* role that has touched the project, in first-seen order. So on a
    project two teams share, one unrelated Event from the other team moved
    the blocker's owner — measured:

        E1  PAY  CTO_BACKEND  BLOCKED           blocker="vendor key missing"
        E2  PAY  CMO          DECISION_APPROVED
        -> Risk.team == "CMO"

    and `ops_status.py` prints that name inside the ATTENTION line that tells
    a team the blocker stays open "그 팀이 RESUMED / ISSUE_RESOLVED /
    COMPLETED를 보고할 때까지". It was telling the wrong team.
    """

    def test_the_blocker_belongs_to_the_team_that_declared_it(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "PAY", "CMO", "DECISION_APPROVED", "IN_PROGRESS", 8)

        rollup = self.rollup()

        self.assertEqual(rollup.project("PAY").teams, ("CTO_BACKEND", "CMO"))
        self.assertEqual([risk.team for risk in rollup.risks], ["CTO_BACKEND"])
        self.assertEqual(rollup.project("PAY").open_blocker_team, "CTO_BACKEND")

    def test_a_reblock_by_another_team_moves_the_owner(self):
        """The owner follows the Event that is *currently* holding it open."""
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "PAY", "CTO_BACKEND", "RESUMED", "IN_PROGRESS", 8)
        self.put("E3", "PAY", "CMO", "BLOCKED", "BLOCKED", 10, blocker="legal review")

        rollup = self.rollup()

        self.assertEqual([risk.team for risk in rollup.risks], ["CMO"])
        self.assertEqual(rollup.risks[0].evidence.event_id, "E3")

    def test_clearing_the_blocker_clears_its_owner(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "PAY", "CTO_BACKEND", "RESUMED", "IN_PROGRESS", 8)

        project = self.rollup().project("PAY")

        self.assertIsNone(project.open_blocker_team)
        self.assertEqual(self.rollup().risks, ())

    def test_the_owner_reaches_attention(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "PAY", "CMO", "DECISION_APPROVED", "IN_PROGRESS", 8)

        rollup = self.rollup()
        from notion.properties import ROLE_DISPLAY_NAMES

        self.assertEqual(
            ROLE_DISPLAY_NAMES.get(rollup.risks[0].team),
            ROLE_DISPLAY_NAMES["CTO_BACKEND"],
        )


class CompletionEvidenceNamesTheCompletingEventTests(ControlTowerTestCase):
    """C48: `projects_completed` cited the wrong file.

    The metric's evidence was `p.open_blocker_evidence or p.evidence[-1]` —
    an expression that, for a *completion* count, prefers the file that
    declared a **blocker** and otherwise names whatever the project did last.
    Measured:

        C1  SEARCH  COMPLETED           on the 5th
        C2  SEARCH  DECISION_APPROVED   on the 9th
        -> evidence named C2

    Traceability is this module's stated point ("open three named files"), and
    a named file that is not the reason for the number is worse than no name:
    it is checked once, found irrelevant, and then not checked again.
    """

    def test_the_completion_names_the_event_that_completed_it(self):
        self.put("C1", "SEARCH", "CTO_BACKEND", "COMPLETED", "COMPLETED", 5)
        self.put("C2", "SEARCH", "CTO_BACKEND", "DECISION_APPROVED", "IN_PROGRESS", 9)

        rollup = self.rollup()

        self.assertEqual(rollup.project("SEARCH").completed_evidence.event_id, "C1")
        self.assertEqual(
            [ref.event_id for ref in rollup.metric("projects_completed").evidence],
            ["C1"],
        )

    def test_a_blocker_opened_after_completion_is_not_the_completion(self):
        self.put("D1", "OPSX", "COO", "COMPLETED", "COMPLETED", 5)
        self.put("D2", "OPSX", "COO", "BLOCKED", "BLOCKED", 9, blocker="reopened for audit")

        rollup = self.rollup()

        self.assertEqual(
            [ref.event_id for ref in rollup.metric("projects_completed").evidence],
            ["D1"],
        )
        self.assertEqual([risk.evidence.event_id for risk in rollup.risks], ["D2"])

    def test_a_project_that_never_completed_has_no_completion_evidence(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        self.assertIsNone(self.rollup().project("SEARCH").completed_evidence)
        self.assertEqual(self.rollup().metric("projects_completed").evidence, ())

    def test_the_completion_evidence_is_a_file_that_exists(self):
        self.put("C1", "SEARCH", "CTO_BACKEND", "COMPLETED", "COMPLETED", 5)

        ref = self.rollup().project("SEARCH").completed_evidence

        self.assertTrue((self.processed / ref.path).is_file())
        self.assertEqual(ref.at, self.rollup().project("SEARCH").completed_at)

    def test_every_counted_metric_still_carries_as_many_files_as_it_counts(self):
        """The property the broken expression happened to satisfy, kept."""
        self.put("C1", "SEARCH", "CTO_BACKEND", "COMPLETED", "COMPLETED", 5)
        self.put("C2", "PAY", "CMO", "COMPLETED", "COMPLETED", 6)
        self.put("C3", "OPSX", "COO", "BLOCKED", "BLOCKED", 7, blocker="waiting")

        rollup = self.rollup()

        for key in ("events", "projects_completed", "open_blockers"):
            with self.subTest(metric=key):
                metric = rollup.metric(key)
                self.assertEqual(len(metric.evidence), metric.value, key)


class RecentSlicesAreCutFromTheWholePeriodTests(ControlTowerTestCase):
    """`_roll_recent()` — the one derivation here that does not fold.

    Everything else in this module collapses Events into a state or a count.
    These two keep them whole, because "무엇이 일어났는가" and "무엇이 끝났는가"
    are questions a fold has already thrown the answer away for.
    """

    def _fill(self, count, *, event_type="STARTED", status="IN_PROGRESS", start=0):
        for index in range(count):
            self.put(
                f"E{start + index:03d}",
                "PAY",
                "CTO_BACKEND",
                event_type,
                status,
                5,
                timestamp=f"2026-08-05T{index % 24:02d}:{index % 60:02d}:00+09:00",
            )

    def test_recent_is_newest_first(self):
        self.put("OLD", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put("NEW", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9)

        self.assertEqual(
            [entry.event_id for entry in self.rollup().recent], ["NEW", "OLD"]
        )

    def test_recent_is_bounded_and_events_read_is_not(self):
        """The bound is on the slice, never on the count behind it. A panel
        that reported `20` as its total would be saying something false about
        a busy month."""
        self._fill(RECENT_LIMIT + 7)

        rollup = self.rollup()
        self.assertEqual(len(rollup.recent), RECENT_LIMIT)
        self.assertEqual(rollup.events_read, RECENT_LIMIT + 7)

    def test_completions_are_cut_after_filtering_not_before(self):
        """The whole reason `completions` is its own slice.

        One completion at the bottom of the period, then `RECENT_LIMIT`
        noisier Events on top. Filtering `recent` would find nothing; cutting
        the filtered list finds it.
        """
        self.put(
            "DONE", "PAY", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5,
            milestone="M1", timestamp="2026-08-05T00:00:00+09:00",
        )
        self._fill(RECENT_LIMIT + 3, start=100)

        rollup = self.rollup()
        self.assertNotIn("DONE", [entry.event_id for entry in rollup.recent])
        self.assertEqual(
            [entry.event_id for entry in rollup.completions], ["DONE"]
        )

    def test_the_completion_total_is_the_true_one(self):
        self._fill(RECENT_LIMIT + 4, event_type="MILESTONE_COMPLETED")

        rollup = self.rollup()
        self.assertEqual(len(rollup.completions), RECENT_LIMIT)
        self.assertEqual(rollup.completions_total, RECENT_LIMIT + 4)

    def test_only_the_two_finishing_types_count(self):
        for index, event_type in enumerate(
            ("STARTED", "BLOCKED", "RESUMED", "CANCELLED", "ISSUE_RESOLVED",
             "DECISION_APPROVED")
        ):
            extra = {}
            status = "IN_PROGRESS"
            if event_type == "BLOCKED":
                extra["blocker"] = "b"
                status = "BLOCKED"
            if event_type == "CANCELLED":
                status = "CANCELLED"
            self.put(
                f"X{index}", "PAY", "CTO_BACKEND", event_type, status, 5,
                timestamp=f"2026-08-05T{index:02d}:00:00+09:00", **extra,
            )

        self.assertEqual(self.rollup().completions, ())
        self.assertEqual(self.rollup().completions_total, 0)

    def test_a_folded_duplicate_is_not_two_entries(self):
        """`_roll_recent()` runs after `_fold_duplicates()`, so one Event
        that arrived as two files is one row — the same rule every other
        number here follows since C50."""
        event = self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        (self.processed / "a-copy.json").write_text(
            event.to_json(), encoding="utf-8"
        )

        rollup = self.rollup()
        self.assertEqual([e.event_id for e in rollup.recent], ["E1"])
        self.assertEqual(len(rollup.duplicates), 1)

    def test_an_entry_keeps_the_event_verbatim(self):
        """Unredacted on the rollup, on purpose: `to_payload()` is the
        boundary, and a rollup that rewrote its own evidence would make the
        `EvidenceRef` useless for finding the file."""
        self.put(
            "E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 5,
            blocker="벤더 대기", summary="키 대기",
        )

        entry = self.rollup().recent[0]
        self.assertEqual(entry.summary, "키 대기")
        self.assertEqual(entry.blocker, "벤더 대기")
        self.assertEqual(entry.project_id, "PAY")
        self.assertEqual(entry.evidence.event_id, "E1")

    def test_the_period_filter_applies_to_both_slices(self):
        """`since` / `until` bound by the Event's own date, so a slice that
        ignored them would report work from outside the period a caller
        asked about."""
        self.put("IN", "PAY", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5,
                 milestone="M")
        self.put("OUT", "PAY", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 20,
                 milestone="M")

        rollup = self.rollup(until=date(2026, 8, 10))
        self.assertEqual([e.event_id for e in rollup.recent], ["IN"])
        self.assertEqual([e.event_id for e in rollup.completions], ["IN"])
        self.assertEqual(rollup.completions_total, 1)

    def test_an_empty_period_produces_empty_slices_not_an_error(self):
        rollup = self.rollup()

        self.assertEqual(rollup.recent, ())
        self.assertEqual(rollup.completions, ())
        self.assertEqual(rollup.completions_total, 0)


class ARefusedEvidenceDirectoryIsReportedNotFatalTests(ControlTowerTestCase):
    """`read_events()` has a channel for "I could not read this" and the
    `is_dir()` above it used to throw the answer away.

    `Path.is_dir()` re-raises a permission error — `pathlib._abc`'s ignored
    set is `(ENOENT, ENOTDIR, EBADF, ELOOP)` and `EACCES` is not in it. So
    the old shape guarded `os.scandir` and not the `is_dir()` one line
    above, which raises the same class.

    Here that cost more than a traceback. This function already reports an
    unusable path through `unreadable`, which the Control Tower renders as
    "읽지 못한 파일 N건 — 아래 숫자는 그만큼 적다": the numbers stay, and they
    come with the sentence that says they are a lower bound. The pre-check
    turned that into nothing at all.

    The directory most likely to answer access-denied is the one this
    project reads across a network — `events/transport/` is the shared
    OneDrive folder (AGENT.md §1) and `processed/` sits beside it.
    """

    def _refusing(self, target):
        """`os.scandir` raising `PermissionError` for one directory."""
        import os as os_module

        real = os_module.scandir
        resolved = Path(target).resolve()

        def fake(path=".", *args, **kwargs):
            try:
                same = Path(path).resolve() == resolved
            except (OSError, ValueError, TypeError):
                same = False
            if same:
                raise PermissionError(f"refused: {path}")
            return real(path, *args, **kwargs)

        os_module.scandir = fake
        self.addCleanup(setattr, os_module, "scandir", real)

    def test_a_refused_directory_becomes_an_unreadable_entry(self):
        from controltower.rollup import read_events

        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self._refusing(self.processed)

        pairs, unreadable = read_events(self.processed)

        self.assertEqual(pairs, ())
        self.assertEqual(len(unreadable), 1)
        self.assertIn("refused", unreadable[0][1])

    def test_the_rollup_says_the_numbers_are_short(self):
        """The point of reporting rather than returning empty: the view can
        tell an operator that zero is not the truth."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self._refusing(self.processed)

        rollup = self.rollup()

        self.assertEqual(rollup.events_read, 0)
        self.assertEqual(len(rollup.unreadable), 1)

    def test_the_dashboard_carries_it_as_incomplete_coverage(self):
        """One layer out: `Coverage.complete` is what tells a consumer the
        panels are a lower bound rather than a quiet week."""
        from controltower import build_dashboard

        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self._refusing(self.processed)

        model = build_dashboard(self.rollup(), now=NOW)

        self.assertFalse(model.coverage.complete)
        self.assertEqual(model.coverage.unreadable, 1)

    def test_a_missing_directory_stays_silent(self):
        """Not damage. Desktop 1/2/3 are reporting machines and have no
        `processed/` at all — reporting that as unreadable would put a
        permanent ATTENTION line on every one of them."""
        from controltower.rollup import read_events

        pairs, unreadable = read_events(self.processed.parent / "not-there")

        self.assertEqual(pairs, ())
        self.assertEqual(unreadable, ())

    def test_a_file_wearing_the_directory_name_stays_silent(self):
        """`NotADirectoryError`, which `is_dir()` also answered False for.
        Keeping it silent preserves the old behaviour for the case that is
        not a permission problem."""
        from controltower.rollup import read_events

        impostor = self.processed.parent / "impostor"
        impostor.write_text("not a directory", encoding="utf-8")

        pairs, unreadable = read_events(impostor)

        self.assertEqual(pairs, ())
        self.assertEqual(unreadable, ())


class TheTwoDoorsDisagreeAboutAnEmptyProjectIdTests(ControlTowerTestCase):
    """CHARACTERIZATION (A-15). The Agent refuses an empty `project_id`; the
    Collector accepts one.

    Measured at both doors:

        agent.signals.load_signals()   invalid — "missing required field(s):
                                       ['project_id']". `REQUIRED_SIGNAL_FIELDS`
                                       is checked with `not data.get(name)`,
                                       so `""` counts as missing.
        events.validate_event()        no errors. docs/02 §4 says `string`,
                                       and `""` is one.

    So a person typing a Signal on their own Desktop cannot create this, and
    an Event arriving from anywhere else can: a hand-placed file (docs/11
    permits one), a partial restore, or a Desktop running different code.
    That is the same send-side/receive-side asymmetry
    `TransportSanitisationAsymmetryTests` and
    `ANonStringSignalFieldIsRefusedOnTheSendingSideTests` already record for
    other fields — this is the one for `project_id`.

    **Nothing is hidden, and that is the finding.** The first reading of this
    was "an unnamed project is counted and never shown"; printing the raw
    section disproved it — the row is there, with a blank name column, and
    the count includes it truthfully. There is no silent loss to fix, so
    nothing here is fixed. What was missing is the record: the behaviour is
    pinned so a change to it has to be deliberate, and closing the door is
    A-15's decision (docs/02 contract change), not this module's.
    """

    def _empty_project_event(self, event_id):
        return self.put(
            event_id, "", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12
        )

    # ----------------------------------------------------- the two doors
    def test_the_agent_refuses_an_empty_project_id(self):
        import datetime as datetime_module
        import json as json_module
        import tempfile as tempfile_module

        from agent.signals import load_signals

        with tempfile_module.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026-08-12"
            day.mkdir()
            (day / "s.json").write_text(
                json_module.dumps(
                    {
                        "project_id": "",
                        "event_type": "STARTED",
                        "status": "IN_PROGRESS",
                        "summary": "work with no project",
                        "history_candidate": True,
                    }
                ),
                encoding="utf-8",
            )
            valid, invalid = load_signals(
                Path(tmp), datetime_module.date(2026, 8, 12)
            )

        self.assertEqual(valid, ())
        self.assertEqual(len(invalid), 1)
        self.assertIn("project_id", str(invalid[0][1]))

    def test_the_collector_door_accepts_one(self):
        """`validate_event()` is what an Event from another Desktop meets."""
        from events import validate_event

        self.assertEqual(
            validate_event(
                {
                    "schema_version": "1.0",
                    "event_id": "E-1",
                    "timestamp": "2026-08-12T10:00:00+09:00",
                    "source": "DESKTOP_1",
                    "role": "CTO_BACKEND",
                    "project_id": "",
                    "event_type": "STARTED",
                    "status": "IN_PROGRESS",
                    "summary": "x",
                    "history_candidate": True,
                }
            ),
            [],
        )

    # ------------------------------------------------ what it then does
    def test_it_becomes_a_project_of_its_own(self):
        self._empty_project_event("A")
        self.put("B", "REAL", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        rollup = self.rollup()

        self.assertEqual(
            sorted((p.project_id, p.event_count) for p in rollup.projects),
            [("", 1), ("REAL", 1)],
        )

    def test_it_is_counted_in_the_company_metric(self):
        """Truthfully — a project's worth of Events happened. The number is
        not wrong; the project just has no name to look it up by."""
        self._empty_project_event("A")
        self.put("B", "REAL", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        self.assertEqual(self.rollup().metric("projects_active").value, 2)

    def test_two_of_them_fold_into_one_unnamed_project(self):
        """`""` is a key like any other, so two nameless Events are one
        nameless project rather than two."""
        self._empty_project_event("A")
        self._empty_project_event("B")

        rollup = self.rollup()

        self.assertEqual(
            sorted((p.project_id, p.event_count) for p in rollup.projects),
            [("", 2)],
        )

    def test_it_never_absorbs_a_named_project(self):
        """The failure that would matter. If an empty id were treated as a
        wildcard or normalised away, a real project's Events would land on
        it — which is the merged-row loss `fit_key()` prevents one layer
        out, arriving from the other end."""
        self._empty_project_event("A")
        self.put("B", "REAL", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        named = self.rollup().project("REAL")

        self.assertIsNotNone(named)
        self.assertEqual(named.event_count, 1)

    def test_whitespace_is_a_different_project_from_empty(self):
        """No trimming anywhere in the fold. Recorded because the obvious
        "tidy-up" — stripping the id — would silently merge two projects a
        person meant to keep apart, and that is a policy change wearing a
        cleanup's clothes."""
        self._empty_project_event("A")
        self.put("B", "   ", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        self.assertEqual(
            sorted(p.project_id for p in self.rollup().projects), ["", "   "]
        )

    def test_the_row_is_rendered_rather_than_dropped(self):
        """The correction to this test class's own first reading.

        "Counted but never shown" would be a real defect — a number an
        operator cannot reconcile with the list beneath it. It is not what
        happens: the row prints with a blank name column. Pinned so a future
        change that *does* drop it fails here.
        """
        from controltower import build_dashboard

        self._empty_project_event("A")
        self.put("B", "REAL", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        rows = build_dashboard(self.rollup(), now=NOW).panel("PROJECTS").rows

        self.assertEqual(len(rows), 2)
        self.assertIn("", [row.key for row in rows])


class NoInventedLayersTests(unittest.TestCase):
    """Goal / Team Goal / Sprint / Task have no source in this system.

    The failure this guards against is the tempting one: a Control Tower that
    shows an empty Goals panel reads as "목표가 없다", and one that invents a
    Goal from, say, `milestone` reads as authoritative. Saying "물어볼 곳이
    없다" is the only true option until the decision in BACKLOG is taken.
    """

    def test_the_unsourced_layers_are_named(self):
        """Six, and the last two are unsourced for a different reason.

        The first four are layers of the work hierarchy that have no source
        *yet*. `CRITICAL_PATH` and `COMPLETION_CRITERIA` are COO judgements
        that three specs say are **not derived** — docs/03 §4, docs/04 §44,
        docs/04 §68 — so deriving one here would contradict the spec rather
        than get ahead of it. They are on this list because the request's
        Dashboard asks for both, and a field the model neither sources nor
        declares is one a consumer cannot tell from an oversight.
        """
        self.assertEqual(
            set(UNSOURCED_LAYERS),
            {
                "COMPANY_GOAL",
                "TEAM_GOAL",
                "SPRINT",
                "TASK",
                "CRITICAL_PATH",
                "COMPLETION_CRITERIA",
            },
        )

    def test_the_rollup_exposes_no_goal_or_sprint_field(self):
        from controltower import rollup as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        for banned in ("goal", "sprint", "kpi_target", "task"):
            with self.subTest(term=banned):
                # As a *field or function name*, not as prose in a docstring.
                self.assertNotIn(f"def {banned}", code.lower())
                self.assertNotIn(f"{banned}:", code.lower().replace("unsourced", ""))

    def test_the_event_schema_still_has_no_source_for_them(self):
        """The premise. If a `sprint` or `goal` field is ever added to the
        Event Schema, this fails and the module above should grow a layer
        rather than keep saying there is none."""
        from events.schema import REQUIRED_FIELDS

        fields = set(REQUIRED_FIELDS) | {"milestone", "blocker", "evidence"}

        for banned in ("goal", "sprint", "task", "kpi", "team"):
            with self.subTest(field=banned):
                self.assertNotIn(banned, fields)


class RollupBoundariesTests(ControlTowerTestCase):
    def test_an_empty_directory_is_answerable(self):
        rollup = self.rollup()

        self.assertEqual(rollup.projects, ())
        self.assertEqual(rollup.risks, ())
        self.assertEqual(rollup.events_read, 0)
        self.assertEqual(rollup.metric("events").value, 0)

    def test_a_missing_directory_is_answerable(self):
        shutil.rmtree(self.processed)

        rollup = self.rollup()

        self.assertEqual(rollup.projects, ())
        self.assertEqual(rollup.unreadable, ())

    def test_an_unreadable_file_is_reported_not_raised(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        (self.processed / "BROKEN.json").write_bytes(bytes([0xFF, 0xFE]) + b" nope")

        rollup = self.rollup()

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual([name for name, _reason in rollup.unreadable], ["BROKEN.json"])

    def test_a_staging_named_file_in_processed_is_an_event(self):
        """C64. `processed/` is the one pipeline directory a writer never
        stages into, so a `.tmp-…json` here is a collected Event under an
        inherited name — parsed, validated, marked seen and given a History
        Candidate by the Collector before it was moved.

        Skipping it silently made the Control Tower short by one with
        `unreadable` empty, which is the conversion C62 removed one loop
        further into this same function.
        """
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("E2", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        shutil.move(
            str(self.processed / "E2.json"), str(self.processed / ".tmp-abc.json")
        )

        rollup = self.rollup()

        self.assertEqual(rollup.events_read, 2)
        self.assertEqual(rollup.unreadable, ())
        self.assertEqual(rollup.duplicates, ())
        self.assertIn(
            ".tmp-abc.json",
            [ref.path for ref in rollup.project("SEARCH").evidence],
        )

    def test_the_old_fixture_could_not_have_seen_the_skip(self):
        """Why this went unexamined, pinned so it cannot recur.

        `test_staging_residue_is_not_an_event` copied one Event to a second
        name and asserted `events_read == 1`. Both files carried **one**
        `event_id`, so the duplicate fold produced that 1 on its own — the
        assertion held whether the skip ran or not, which is what a test that
        names a behaviour and cannot observe it looks like (C57).

        This states the fold's answer directly, so the number in that
        assertion is attributed to the mechanism that actually produces it.
        """
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        shutil.copy(self.processed / "E1.json", self.processed / "copy.json")

        rollup = self.rollup()

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual(len(rollup.duplicates), 1)
        self.assertTrue(rollup.duplicates[0].identical)

    def test_the_three_readers_of_processed_now_agree(self):
        """The finding itself: one directory, three readers, two answers.

        Measured on HEAD with two ordinary Events and one staging-named one —
        Control Tower 2, the COMPANY block 3, reconciliation 3 — on one
        `ops_status.py` screen, and only the Control Tower short.
        """
        from app.desktop_activity import _json_paths
        from history.reconciliation import find_orphaned_events

        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("E2", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        shutil.move(
            str(self.processed / "E2.json"), str(self.processed / ".tmp-abc.json")
        )

        control_tower = self.rollup().events_read
        company_block = len(_json_paths(self.processed))
        reconciliation = find_orphaned_events(
            processed_dir=self.processed,
            keep_dir=self.processed.parent / "keep",
            review_dir=self.processed.parent / "review",
        ).checked

        self.assertEqual(
            (control_tower, company_block, reconciliation), (2, 2, 2)
        )

    def test_every_role_appears_even_when_silent(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)

        rollup = self.rollup()

        self.assertEqual({team.team for team in rollup.teams}, set(ROLES))
        self.assertEqual(rollup.silent_teams, ("CTO_FRONTEND", "CMO", "COO"))
        self.assertEqual(rollup.metric("teams_silent").value, 3)

    def test_the_period_is_bounded_by_the_events_own_date(self):
        """docs/06 §12's rule: an Event belongs to the day the work happened,
        not the day the file arrived. A Desktop switched off for a week would
        otherwise land in the wrong period."""
        self.put("OLD", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("NEW", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 15)

        rollup = self.rollup(since=date(2026, 8, 10))

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual(
            [ref.event_id for ref in rollup.project("SEARCH").evidence], ["NEW"]
        )

    def test_a_naive_now_does_not_produce_a_wrong_age(self):
        """Rather than silently dropping the offset — the mistake
        `notion/sync._as_comparable_timestamp()` refuses to make."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker="k")

        risk = self.rollup().risks[0]

        self.assertIsNone(risk.days_open(datetime(2026, 8, 19, 9, 0)))
        self.assertEqual(risk.days_open(NOW), 10)

    def test_a_project_touched_by_two_teams_lists_both(self):
        self.put("E1", "SHARED", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("E2", "SHARED", "CTO_FRONTEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5, milestone="M")

        rollup = self.rollup()

        self.assertEqual(rollup.project("SHARED").teams, ("CTO_BACKEND", "CTO_FRONTEND"))
        backend = next(t for t in rollup.teams if t.team == "CTO_BACKEND")
        self.assertEqual(backend.projects, ("SHARED",))

    def test_a_blocked_shared_project_is_blocked_for_both_teams(self):
        self.put("E1", "SHARED", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("E2", "SHARED", "CTO_FRONTEND", "BLOCKED", "BLOCKED", 5, blocker="k")

        rollup = self.rollup()

        for role in ("CTO_BACKEND", "CTO_FRONTEND"):
            with self.subTest(team=role):
                team = next(t for t in rollup.teams if t.team == role)
                self.assertEqual(team.blocked_projects, ("SHARED",))

    def test_read_events_and_build_agree(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("E2", "PAY", "CTO_FRONTEND", "STARTED", "IN_PROGRESS", 2)

        pairs, unreadable = read_events(self.processed)

        self.assertEqual(unreadable, ())
        self.assertEqual(
            build_company_rollup(now=NOW, events=pairs).events_read,
            self.rollup().events_read,
        )




class LookupsAndBoundariesTests(ControlTowerTestCase):
    """The small answers a view asks for, and the shapes that have no answer.

    Each of these is a branch a caller can reach without doing anything odd:
    asking about a project that had no Events this period, asking for a
    metric key that is not there, and rendering a project whose timestamp
    cannot be compared with `now`.
    """

    def test_an_unknown_project_is_none_not_an_error(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)

        self.assertIsNone(self.rollup().project("NEVER_HEARD_OF_IT"))

    def test_an_unknown_metric_key_is_none_not_an_error(self):
        """`ops_status.py` looks metrics up by key; a renamed one must come
        back as absent rather than raise inside the status view."""
        self.assertIsNone(self.rollup().metric("velocity"))

    def test_a_team_with_no_events_has_no_last_seen(self):
        team = next(t for t in self.rollup().teams if t.team == "COO")

        self.assertIsNone(team.last_seen)
        self.assertFalse(team.has_activity)
        self.assertEqual(team.projects, ())

    def test_an_unparseable_timestamp_answers_none_rather_than_guessing(self):
        from controltower.rollup import _whole_days_between

        self.assertIsNone(_whole_days_between(None, NOW))
        self.assertIsNone(_whole_days_between("", NOW))
        self.assertIsNone(_whole_days_between("yesterday", NOW))
        self.assertEqual(_whole_days_between("2026-08-09T09:00:00+09:00", NOW), 10)

    def test_a_directory_wearing_an_event_filename_is_not_an_event(self):
        """C31's rule, applied to this reader too: it exists, and it is not an
        Event. `is_file()` before the read, so the directory neither becomes a
        rollup nor an `unreadable` entry that a person would go looking for."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        (self.processed / "EVT-DIR.json").mkdir()

        rollup = self.rollup()

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual(rollup.unreadable, ())

    def test_a_period_that_excludes_everything_is_answerable(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)

        rollup = self.rollup(since=date(2026, 9, 1))

        self.assertEqual(rollup.events_read, 0)
        self.assertEqual(rollup.projects, ())
        self.assertEqual(rollup.silent_teams, ("CTO_BACKEND", "CTO_FRONTEND", "CMO", "COO"))

    def test_until_bounds_the_period_at_the_back(self):
        self.put("EARLY", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("LATE", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 15,
                 milestone="M")

        rollup = self.rollup(until=date(2026, 8, 10))

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual(rollup.project("SEARCH").milestones, ())

    def test_a_timestamp_the_fold_cannot_compare_does_not_take_it_down(self):
        """`Event.from_json()` will not produce these — `validate_event()`
        requires an ISO-8601 timestamp with an offset — but
        `build_company_rollup(events=...)` takes objects a caller built, and
        the dataclass itself validates nothing.

        The property is the one `history/result.HistoryCandidate._sort_key()`
        already holds: one unparseable value must not decide the order of
        everything around it, and must not raise. It sorts last, by text.
        """
        from events import Event

        def raw(event_id, timestamp, event_type="STARTED", status="IN_PROGRESS", **extra):
            return Event(
                schema_version="1.0", event_id=event_id, timestamp=timestamp,
                source="DESKTOP_1", role="CTO_BACKEND", project_id="P",
                event_type=event_type, status=status, summary="s",
                history_candidate=True, **extra,
            )

        pairs = [
            (raw("BAD", "yesterday"), "BAD.json"),
            (raw("NAIVE", "2026-08-05T09:00:00"), "NAIVE.json"),
            (raw("GOOD", "2026-08-09T09:00:00+09:00", "BLOCKED", "BLOCKED",
                 blocker="vendor key"), "GOOD.json"),
        ]

        rollup = build_company_rollup(now=NOW, events=pairs)
        project = rollup.project("P")

        # A naive timestamp still has a *date*, so it is placed — after the
        # comparable one, by the two-tier sort key, so one unorderable value
        # does not decide the order of everything around it.
        self.assertEqual([ref.event_id for ref in project.evidence], ["GOOD", "NAIVE"])
        self.assertEqual(project.event_count, 2)
        self.assertEqual(rollup.events_read, 2)

        # The one with no readable date at all is REPORTED, not dropped: the
        # difference between `events_read` and the directory has to have a
        # reason a person can see.
        self.assertEqual([name for name, _why in rollup.unreadable], ["BAD.json"])
        self.assertIn("timestamp", rollup.unreadable[0][1])

    def test_a_repeated_milestone_is_listed_once(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 1,
                 milestone="Index rebuild")
        self.put("E2", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5,
                 milestone="Index rebuild")

        self.assertEqual(self.rollup().project("SEARCH").milestones, ("Index rebuild",))

    def test_a_milestone_event_with_no_milestone_adds_nothing(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 1)

        self.assertEqual(self.rollup().project("SEARCH").milestones, ())



class CompanyHistoryCanOutliveTheEvidenceTests(unittest.TestCase):
    """A restored machine gets its Company History back and none of its Events.

    `runtime/events/processed/` is Execution Evidence (docs/14 §2) and Backup
    scope is `daily/` and `monthly/` only (docs/08 §26). So after a machine
    loss the Control Tower — which reads that directory and nothing else —
    answers `Event 0건 / 움직인 Project 0 / 모든 Team 활동 없음` for a company
    that has months of recorded work. Measured on an 18-day restored-shaped
    tree: exactly that, with nothing distinguishing it from a company that
    did nothing.

    A **qualifier, not an alert**: nothing brings those Events back, and a
    standing alarm nobody can clear is what this file keeps removing. B-6's
    retention decision produces the same shape on purpose.

    The comparison is against the earliest Daily that *carries work*, not the
    earliest Daily file — `generate_daily_history()` writes a file for an
    empty day too, so a `history_start_date` earlier than the first Event is
    ordinary and must not read as missing evidence. That is the false
    positive this class spends a test on.
    """

    # Built from lists: the escapes belong to the document, not to this file.
    EMPTY_DAY = chr(10).join([
        "# H", "", "## Metadata", "", "- Event Count: 0", "",
    ])
    DAY_WITH_WORK = chr(10).join([
        "# H", "", "## Milestones", "", "### OPS", "",
        "- work", "- Owner: COO", "- Event ID: OLD-{day}", "",
        "## Metadata", "", "- Event Count: 1", "",
    ])

    def _runtime(self, *, events_from=None, empty_prefix=0):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (runtime / rel).mkdir(parents=True)
        for day in range(1, 19):
            if day <= empty_prefix:
                body = self.EMPTY_DAY
            else:
                body = self.DAY_WITH_WORK.format(day=day)
            (runtime / "local_master" / "daily" / f"2026-08-{day:02d}.md").write_text(
                body, encoding="utf-8"
            )
        if events_from is not None:
            for day in range(events_from, 19):
                event = create_event(
                    source="DESKTOP_4", role="COO", project_id="OPS",
                    event_type="STARTED", status="IN_PROGRESS", summary="s",
                    history_candidate=True, event_id=f"E{day:02d}",
                    timestamp=f"2026-08-{day:02d}T10:00:00+09:00",
                )
                (runtime / "events" / "processed" / f"E{day:02d}.json").write_text(
                    event.to_json(), encoding="utf-8"
                )
        return runtime

    def _lines(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_evidence", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_control_tower(NOW)
        return buffer.getvalue(), attention

    def test_a_restored_machine_says_the_evidence_is_gone(self):
        printed, attention = self._lines(self._runtime())

        self.assertIn("Event 0건", printed)
        self.assertIn("증거 범위 밖", printed)
        self.assertIn("2026-08-01", printed)
        self.assertIn("하나도 남아 있지 않다", printed)
        # A qualifier, not an alert.
        self.assertEqual(attention, [])

    def test_a_pruned_evidence_directory_says_where_it_now_starts(self):
        printed, _attention = self._lines(self._runtime(events_from=10))

        self.assertIn("증거 범위 밖", printed)
        self.assertIn("2026-08-10부터", printed)

    def test_a_healthy_tree_says_nothing(self):
        printed, attention = self._lines(self._runtime(events_from=1))

        self.assertNotIn("증거 범위 밖", printed)
        self.assertEqual(attention, [])

    def test_empty_daily_files_before_the_first_event_are_not_missing_evidence(self):
        """`history_start_date` earlier than the first Event is ordinary —
        docs/09 §72 writes a file for an empty day too. Comparing against the
        earliest Daily *file* instead of the earliest one carrying work would
        report every such install as having lost evidence."""
        printed, attention = self._lines(
            self._runtime(events_from=5, empty_prefix=4)
        )

        self.assertNotIn("증거 범위 밖", printed)
        self.assertEqual(attention, [])

    def test_a_machine_with_no_company_history_says_nothing(self):
        """A fresh install has neither, and must not be told it lost
        something."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "events" / "processed").mkdir(parents=True)
        (runtime / "local_master" / "daily").mkdir(parents=True)

        printed, attention = self._lines(runtime)

        self.assertNotIn("증거 범위 밖", printed)
        self.assertEqual(attention, [])


class DesktopLayerTests(ControlTowerTestCase):
    """The layer under Team, and the reason it is not redundant.

    While every Event obeys docs/02 §8, `source` -> `role` is 1:1 and Team and
    Desktop are the same partition. That is exactly why both are here: the
    moment they stop agreeing, having only one of them makes the disagreement
    invisible — and `validate_event()` checks each field against its own
    allowed set and **never the pair**, so an Event can say it came from
    DESKTOP_1 and did the CMO's work and be accepted everywhere.
    """

    def _rewrite_source(self, event, event_id, source):
        """Write `event` back with a different `source`, the way a hand-written
        or restored file arrives. `create_event()` cannot produce this — the
        Reporter pairs the two from `PROFILES` — so the file is written
        directly, which is the one path that can."""
        data = event.to_dict()
        data["source"] = source
        (self.processed / f"{event_id}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return data

    def test_each_desktop_carries_its_own_events(self):
        self.put("D1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("D2", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 2)
        self.put("D4", "OPS", "COO", "STARTED", "IN_PROGRESS", 3)

        rollup = self.rollup()
        by_source = {d.source: d for d in rollup.desktops}

        self.assertEqual(by_source["DESKTOP_1"].projects, ("SEARCH",))
        self.assertEqual(by_source["DESKTOP_2"].projects, ("BRAND",))
        self.assertEqual(by_source["DESKTOP_4"].projects, ("OPS",))
        self.assertEqual(by_source["DESKTOP_1"].expected_team, "CTO_BACKEND")

    def test_a_desktop_that_sent_nothing_is_present_and_empty(self):
        self.put("D1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)

        by_source = {d.source: d for d in self.rollup().desktops}

        self.assertIn("DESKTOP_3", by_source)
        self.assertFalse(by_source["DESKTOP_3"].has_activity)
        self.assertIsNone(by_source["DESKTOP_3"].days_silent(NOW))

    def test_stale_is_measured_from_the_events_own_date(self):
        self.put("D1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9)

        desktop = next(d for d in self.rollup().desktops if d.source == "DESKTOP_1")

        self.assertEqual(desktop.days_silent(NOW), 10)
        self.assertIsNone(
            desktop.days_silent(datetime(2026, 8, 19, 9, 0)),
            "a naive `now` is answered None rather than guessed at",
        )

    def test_an_event_claiming_another_desktops_role_is_reported(self):
        from events import validate_event

        event = self.put("MIX", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 5)
        data = self._rewrite_source(event, "MIX", "DESKTOP_1")

        self.assertEqual(validate_event(data), [], "both fields are legal on their own")

        rollup = self.rollup()

        self.assertEqual(len(rollup.mismatches), 1)
        mismatch = rollup.mismatches[0]
        self.assertEqual(mismatch.event_id, "MIX")
        self.assertEqual(mismatch.source, "DESKTOP_1")
        self.assertEqual(mismatch.claimed_role, "CMO")
        self.assertEqual(mismatch.expected_role, "CTO_BACKEND")
        self.assertEqual(mismatch.evidence.path, "MIX.json")
        self.assertEqual(rollup.metric("desktop_role_mismatches").value, 1)

    def test_a_mismatched_event_counts_under_the_desktop_that_sent_it(self):
        """Believing the `role` field is precisely how one Desktop's work
        silently becomes another team's."""
        event = self.put("MIX", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 5)
        self._rewrite_source(event, "MIX", "DESKTOP_1")

        rollup = self.rollup()
        by_source = {d.source: d for d in rollup.desktops}

        self.assertEqual(by_source["DESKTOP_1"].event_count, 1)
        self.assertEqual(by_source["DESKTOP_2"].event_count, 0)
        self.assertEqual(len(by_source["DESKTOP_1"].mismatched), 1)
        # The Team fold still believes the field, and that disagreement is the
        # finding rather than a bug in either fold: one answers "which machine
        # sent this", the other "which role does it claim".
        self.assertEqual(
            next(t for t in rollup.teams if t.team == "CMO").event_count, 1
        )

    def test_a_conforming_event_produces_no_mismatch(self):
        for desktop, role in ROLE_FOR_SOURCE.items():
            with self.subTest(desktop=desktop):
                shutil.rmtree(self.processed)
                self.processed.mkdir(parents=True)
                self.put("E1", "P", role, "STARTED", "IN_PROGRESS", 1)

                self.assertEqual(self.rollup().mismatches, ())

    def test_an_unknown_source_is_kept_rather_than_dropped(self):
        """Reachable only if `events.SOURCES` grows without `PROFILES`
        following. A Desktop this project has not heard of must show up as
        itself, not disappear — and must not be called a mismatch, because
        there is no expected role to compare it against."""
        from events import Event

        event = self.put("E1", "P", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        data = event.to_dict()
        data["source"] = "DESKTOP_9"
        data["evidence"] = tuple(data.get("evidence") or ())
        unknown = Event(**data)

        rollup = build_company_rollup(now=NOW, events=[(unknown, "E1.json")])
        by_source = {d.source: d for d in rollup.desktops}

        self.assertIn("DESKTOP_9", by_source)
        self.assertEqual(by_source["DESKTOP_9"].event_count, 1)
        self.assertEqual(by_source["DESKTOP_9"].expected_team, "")
        self.assertEqual(rollup.mismatches, ())

    def test_the_table_is_the_specs_own(self):
        """Not a copy kept in step by hand — `reporter/profiles.PROFILES` is
        docs/02 §8 verbatim, and this reads it."""
        from reporter.profiles import PROFILES

        self.assertEqual(
            ROLE_FOR_SOURCE, {p.source: p.role for p in PROFILES.values()}
        )

    def test_the_presentation_order_is_the_one_it_claims_to_share(self):
        """C48: `TEAM_ORDER`'s comment says it is "the same one
        `daily/role_summary.ROLE_ORDER` uses and for the same reason", and it
        is restated rather than imported because the layering table has no
        `controltower -> daily` edge. A claim with nothing checking it is how
        two copies drift; this is the check the comment implies.
        """
        from controltower.rollup import TEAM_ORDER
        from daily import ROLE_ORDER

        self.assertEqual(TEAM_ORDER, ROLE_ORDER)

    def test_every_role_has_a_place_in_the_order(self):
        """A role added to `events.ROLES` and not here still appears — the
        fold appends the unknown ones, sorted, after the known ones — but it
        appears in a different place than the rest of the project shows it.
        Named so the addition is a decision rather than a surprise."""
        from controltower.rollup import TEAM_ORDER

        self.assertEqual(set(TEAM_ORDER), set(ROLES))

    def test_every_desktop_has_a_place_in_the_order(self):
        """The same for `source`. `reporter/profiles.py`'s own comment records
        the time this went wrong in the other direction — "DESKTOP_4 was
        missing here even though docs/02 §8 lists it as an allowed source"."""
        from controltower.rollup import DESKTOP_ORDER
        from events import SOURCES

        self.assertEqual(set(DESKTOP_ORDER), set(SOURCES))


class DesktopBlockTests(ControlTowerTestCase):
    """The Desktop rows and the mismatch alert, through the view."""

    def setUp(self):
        super().setUp()
        runtime = self.processed.parent / "runtime"
        (runtime / "events").mkdir(parents=True)
        self.processed.rename(runtime / "events" / "processed")
        self.processed = runtime / "events" / "processed"

    def _run(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_desktop", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.processed.parent.parent
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_control_tower(NOW)
        return buffer.getvalue(), attention

    def _mix(self):
        event = self.put("MIX", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 5)
        data = event.to_dict()
        data["source"] = "DESKTOP_1"
        (self.processed / "MIX.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def test_every_desktop_gets_a_row(self):
        self.put("D1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)

        printed, _attention = self._run()

        for desktop in ROLE_FOR_SOURCE:
            with self.subTest(desktop=desktop):
                self.assertIn(desktop, printed)
        self.assertIn("이 기간 Event 없음", printed)

    def test_a_mismatch_reaches_attention_naming_both_roles(self):
        self._mix()

        printed, attention = self._run()

        lines = [a for a in attention if "role이 어긋난" in a]
        self.assertEqual(len(lines), 1, attention)
        self.assertIn("MIX", lines[0])
        self.assertIn("DESKTOP_1", lines[0])
        self.assertIn("CMO", lines[0])
        self.assertIn("CTO_BACKEND", lines[0])
        self.assertIn("MIX.json", lines[0])
        self.assertIn("role 어긋남 1", printed)

    def test_a_conforming_runtime_raises_no_desktop_alert(self):
        self.put("D1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("D2", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 2)

        _printed, attention = self._run()

        self.assertEqual([a for a in attention if "role이 어긋난" in a], [])

    def test_a_silent_desktop_is_shown_but_not_alerted(self):
        """`source` is the COMPANY block's key too, so an alert here would be
        a second opinion about one fact."""
        self.put("D1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)

        printed, attention = self._run()

        self.assertIn("DESKTOP_3", printed)
        self.assertEqual(attention, [])


class TheRowIsExactlyTheFoldOverWhatReachedItTests(unittest.TestCase):
    """Seeded property test: the Notion PROJECTS row equals the Control Tower
    fold over the Events that were **applied** — never anything else.

    Two derivations of one project's state now exist: `ExecutionPlanSync`
    writes it into the View one Event at a time, and `controltower` folds it
    off disk. `OneRuleForBlockerStateTests` above holds them to the same rule
    one `event_type` at a time; this holds them to it over *sequences*, which
    is where an ordering or fold mistake would actually live.

    The property is stated so that the one legitimate divergence is part of
    it rather than an exception to it:

        Notion row  ==  fold(Events the guard did not skip)

    So a skip explains a difference from the full fold, and **nothing else may**.
    If some other drift existed — a rule applied in one place and not the
    other, an ordering difference, a field one side forgets — this fails even
    on sequences full of skips.

    Measured over 300 seeds while it was written: 18 sequences with no skip
    at all and 282 with at least one, and the equality held on every one.
    Sixty are kept here (0.8 s); the seeds are fixed, so a failure is
    reproducible by number.
    """

    SEEDS = 60
    TYPES = (
        "STARTED", "BLOCKED", "RESUMED", "DECISION_APPROVED",
        "MILESTONE_COMPLETED", "ISSUE_RESOLVED", "COMPLETED", "CANCELLED",
    )

    def _status_for(self, event_type, rnd):
        if event_type in ("COMPLETED", "CANCELLED", "BLOCKED"):
            return event_type
        return rnd.choice(["NOT_STARTED", "IN_PROGRESS", "BLOCKED"])

    def _row_state(self, properties):
        items = (properties.get("Blocker") or {}).get("rich_text") or []
        return {
            "blocker": "".join(
                i.get("text", {}).get("content", "") for i in items
            ) or None,
            "completed_at": (
                (properties.get("Completed Date") or {}).get("date") or {}
            ).get("start"),
        }

    def test_the_notion_row_is_the_fold_over_the_applied_events(self):
        import random

        from notion import (
            ExecutionPlanSync,
            InMemoryNotionTransport,
            NotionClient,
            SyncStatus,
        )

        with_skip = without_skip = 0
        for seed in range(self.SEEDS):
            rnd = random.Random(seed)
            transport = InMemoryNotionTransport()
            sync = ExecutionPlanSync(
                client=NotionClient(transport=transport, database_id="DB")
            )
            applied, skipped, stamps = [], [], []
            for index in range(rnd.randint(2, 10)):
                event_type = rnd.choice(self.TYPES)
                # Collisions on purpose: a repeated instant is E-23's shape,
                # and `0` is over-weighted because that is what a Signal with
                # no timestamp of its own gets (docs/06 §12).
                stamp = "2026-09-%02dT%02d:00:00+09:00" % (
                    rnd.randint(1, 6), rnd.choice([0, 0, 9, 12])
                )
                stamps.append(stamp)
                event = create_event(
                    source=SOURCE_FOR_ROLE[ROLE_CYCLE[index % 4]],
                    role=ROLE_CYCLE[index % 4],
                    project_id="P",
                    event_type=event_type,
                    status=self._status_for(event_type, rnd),
                    summary=f"s{index}",
                    history_candidate=True,
                    event_id=f"E{index:02d}",
                    timestamp=stamp,
                    blocker=f"blk{index}" if event_type == "BLOCKED" else None,
                    milestone=f"M{index}" if event_type == "MILESTONE_COMPLETED" else None,
                )
                if sync.sync(event).status is SyncStatus.NOTION_SKIPPED_OLD_EVENT:
                    skipped.append(event.event_id)
                else:
                    applied.append((event, f"E{index:02d}.json"))

            with_skip, without_skip = (
                (with_skip + 1, without_skip) if skipped else (with_skip, without_skip + 1)
            )
            pages = [
                page for page in transport._pages.values()
                if "Project ID" in page.get("properties", {})
            ]
            with self.subTest(seed=seed):
                self.assertEqual(len(pages), 1, "one project, one row")
                folded = build_company_rollup(now=NOW, events=applied).project("P")
                self.assertEqual(
                    self._row_state(pages[0]["properties"]),
                    {
                        "blocker": folded.open_blocker if folded else None,
                        "completed_at": folded.completed_at if folded else None,
                    },
                    f"seed={seed} skipped={skipped} stamps={stamps}",
                )

        # Guards the guard: a run where the Late Event guard never fired, and
        # a run where it did, must both be in the sample — otherwise this is
        # only testing one half of the property.
        self.assertGreater(with_skip, 0)
        self.assertGreater(without_skip, 0)


class ControlTowerBlockTests(ControlTowerTestCase):
    """The view. `ops_status.py`'s CONTROL TOWER block, driven end to end."""

    def _run(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_ct", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.processed.parent.parent
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_control_tower(NOW)
        return buffer.getvalue(), attention

    def setUp(self):
        super().setUp()
        # `_print_control_tower()` reads RUNTIME_DIR/events/processed
        runtime = self.processed.parent / "runtime"
        (runtime / "events").mkdir(parents=True)
        self.processed.rename(runtime / "events" / "processed")
        self.processed = runtime / "events" / "processed"

    def test_the_recent_lists_reach_the_screen(self):
        """The two panels that reach the screen and **not** Notion.

        Every other Control Tower panel is projected to a `CT_*` database;
        these two are not, because a Notion table keyed by `event_id` grows
        one row per Event forever and its reconciliation stops working past
        1,000 rows (`notion_projection.UNPROJECTED_PANELS` carries the
        measurement). So the terminal is the only place they appear, and a
        panel nothing renders would be a capability with no reader.
        """
        self.put("E1", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS",
                 9, milestone="M1")

        printed, _ = self._run()

        self.assertIn("최근 활동", printed)
        self.assertIn("최근 완료", printed)
        self.assertIn("summary for E1", printed)

    def test_the_screen_says_how_many_it_is_not_showing(self):
        """Five lines must never read as "five things happened"."""
        for index in range(9):
            self.put(
                f"E{index}", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9,
                timestamp=f"2026-08-09T{index:02d}:00:00+09:00",
            )

        printed, _ = self._run()

        self.assertIn("최근 활동 (총 9건)", printed)

    def test_a_complete_list_does_not_claim_a_total(self):
        """A qualifier that always appears is one an operator stops
        reading — the same rule the duplicate line follows."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9)

        printed, _ = self._run()

        self.assertIn("최근 활동", printed)
        self.assertNotIn("최근 활동 (총", printed)

    def test_an_empty_company_prints_neither_list(self):
        """Sourced-and-empty is a true statement, and the block already says
        `집계 대상 : Event 0건` two lines up. A header with nothing under it
        would be noise."""
        printed, _ = self._run()

        self.assertNotIn("최근 활동", printed)
        self.assertNotIn("최근 완료", printed)

    def test_an_authored_summary_is_redacted_on_the_screen(self):
        """`summary` is the first authored sentence this block prints, and
        `_authored()` is the reason it is safe to."""
        self.put(
            "E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9,
            summary="token " + "ntn" + "_" + "A" * 24,
        )

        printed, _ = self._run()

        self.assertNotIn("ntn_" + "A" * 24, printed)
        self.assertIn("[REDACTED]", printed)

    def test_a_newline_in_a_summary_cannot_forge_a_line(self):
        self.put(
            "E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9,
            summary="real\n    2026-01-01T00:00:00+09:00  DESKTOP_9  FORGED",
        )

        printed, _ = self._run()

        # The forged text is still *in* the output, escaped, on the tail of
        # the real line — that is `one_line()`'s promise, not a miss: keeping
        # the value recoverable is what makes docs/04 §55's "기록한다" honest.
        # What must not exist is a **line that begins** with the forged
        # timestamp, because that is what a reader takes for a second entry.
        self.assertNotIn("\n    2026-01-01T00:00:00+09:00", printed)
        self.assertIn("real\\n", printed)
        self.assertEqual(
            len([line for line in printed.splitlines() if "DESKTOP_9" in line]), 1
        )

    def test_the_two_reasons_for_no_source_are_printed_apart(self):
        """Goal / Sprint / Task have no source **yet**; Critical Path and
        완료 조건 are refused by three specs. One sentence for both made the
        screen call a rule an omission."""
        printed, _ = self._run()

        self.assertIn("원천 없음", printed)
        self.assertIn("자동화 안 함", printed)
        self.assertIn("CRITICAL_PATH", printed)
        self.assertIn("docs/04 §44", printed)
        automated = printed.split("자동화 안 함")[1].split("\n")[0]
        self.assertNotIn("COMPANY_GOAL", automated)

    def test_a_group_that_empties_stops_being_printed(self):
        """The day a layer gains a source.

        Both "원천 없음" and "자동화 안 함" are printed only when their group
        has members, and with today's model both always do — so the empty
        arms had never run. They are not decoration: the whole point of
        reading the layers off the model rather than off the constant is that
        a layer which gained a source stops being announced, and a line
        reading `(원천 없음 : )` with nothing after it would be the announcement
        continuing in a worse form.

        Simulated by emptying `UNSOURCED_LAYERS` in the loaded module, which
        is what "every layer gained a source" looks like from this function.
        """
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_empty", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.processed.parent.parent
        module.UNSOURCED_LAYERS = ()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module._print_control_tower(NOW)
        printed = buffer.getvalue()

        self.assertNotIn("원천 없음", printed)
        self.assertNotIn("자동화 안 함", printed)
        # The block itself still rendered — this is the layers disappearing,
        # not the function failing.
        self.assertIn("CONTROL TOWER", printed)

    def test_two_files_with_one_id_and_different_contents_reach_attention(self):
        """`EVENT_ID_CONFLICT` — the half of a duplicate that is a real
        problem.

        Two files claiming one `event_id` with the **same** contents is a
        duplicate the pipeline handled and the fold counted once; saying so
        would be an alarm with no action. Different contents means one of
        them is not the Event it says it is, and which one the Control Tower
        counted is decided by filename order — the only case where a person
        has to open both files.
        """
        event = self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9)
        impostor = event.to_dict()
        impostor["project_id"] = "SOMETHING_ELSE"
        (self.processed / "a-different-file.json").write_text(
            json.dumps(impostor, ensure_ascii=False), encoding="utf-8"
        )

        _, attention = self._run()

        conflict = [line for line in attention if "event_id" in line]
        self.assertEqual(len(conflict), 1, attention)
        self.assertIn("E1", conflict[0])
        self.assertIn("a-different-file.json", conflict[0])

    def test_an_identical_twin_raises_no_attention(self):
        """The other half, so the test above is about the difference."""
        event = self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 9)
        (self.processed / "a-copy.json").write_text(
            event.to_json(), encoding="utf-8"
        )

        printed, attention = self._run()

        self.assertEqual([line for line in attention if "event_id" in line], [])
        self.assertIn("중복 파일", printed)

    def test_an_open_blocker_reaches_attention_with_its_evidence(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker="vendor key")

        printed, attention = self._run()

        self.assertEqual(len(attention), 1, attention)
        self.assertIn("SEARCH", attention[0])
        self.assertIn("vendor key", attention[0])
        self.assertIn("E1.json", attention[0])
        self.assertIn("10일째", attention[0])
        self.assertIn("CONTROL TOWER", printed)

    def test_a_resumed_project_raises_nothing(self):
        self.put("E1", "BRAND", "CMO", "BLOCKED", "BLOCKED", 6, blocker="budget")
        self.put("E2", "BRAND", "CMO", "RESUMED", "IN_PROGRESS", 12)

        printed, attention = self._run()

        self.assertEqual(attention, [])
        self.assertIn("열려 있는 Blocker   : 0", printed)

    def test_a_silent_team_is_counted_but_not_alerted(self):
        """`source` -> `role` is 1:1 (docs/02 §8), so the COMPANY block
        already alerts on this Desktop. Two lines for one fact is the second
        opinion this project keeps removing."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)

        printed, attention = self._run()

        self.assertIn("이 기간 활동 없음", printed)
        self.assertEqual(attention, [])

    def test_an_empty_runtime_still_prints_the_block(self):
        printed, attention = self._run()

        self.assertIn("CONTROL TOWER", printed)
        self.assertIn("Event 0건", printed)
        self.assertEqual(attention, [])

    def test_a_fresh_install_with_no_runtime_at_all_still_answers(self):
        """The first thing docs/11 has an operator run, on a machine where
        nothing has run yet. Every other block already survives it; a new one
        that raises here would make the whole view unusable exactly when it
        is being set up."""
        import contextlib
        import importlib.util

        missing = self.processed.parent.parent / "not-created-yet"
        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_fresh", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = missing

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_control_tower(NOW)

        self.assertIn("CONTROL TOWER", buffer.getvalue())
        self.assertIn("Event 0건", buffer.getvalue())
        self.assertEqual(attention, [])

    def test_the_block_says_which_layers_have_no_source(self):
        printed, _attention = self._run()

        for layer in UNSOURCED_LAYERS:
            with self.subTest(layer=layer):
                self.assertIn(layer, printed)

    def test_an_unreadable_file_makes_the_numbers_a_declared_lower_bound(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        (self.processed / "BROKEN.json").write_bytes(bytes([0xFF, 0xFE]) + b" nope")

        printed, attention = self._run()

        self.assertIn("읽지 못한 파일 1건", printed)
        # ...and no second alert: the HISTORY block's Candidate 정합성 line
        # already names unreadable files in this directory.
        self.assertEqual(attention, [])

    def test_a_forged_blocker_cannot_add_a_line_to_the_report(self):
        """`blocker`, `project_id` and `summary` all cross OneDrive from
        another Desktop. The block's Project rows and the ATTENTION lines are
        exactly what a forged line would imitate."""
        forged = "x" + chr(10) + "    ! FORGED_PROJECT      CTO Backend  Event 9   BLOCKED"
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9, blocker=forged)

        printed, attention = self._run()

        self.assertNotIn("FORGED_PROJECT", printed)
        for line in attention:
            with self.subTest(line=line):
                self.assertNotIn(chr(10), line)

    def test_a_secret_shaped_blocker_is_redacted_before_it_is_printed(self):
        """`blocker` is Event *content*, typed by a person on another Desktop
        and carried across OneDrive. `ops_status.main()`'s ATTENTION sink
        deliberately does not redact — its rule is that messages are built
        from filenames, ids and counts — so the one message that carries
        content redacts at its source, exactly as
        `run_company_ops.py::_print_result()` does for `failure.reason`.

        Not hypothetical: "waiting for NOTION_API_TOKEN=… to be rotated" is a
        plausible thing to type into a blocker. The Agent's Signal layer
        refuses secret-shaped Signal content, but Desktop 4's own reporter
        and a hand-written Event do not go through it.
        """
        token = "ntn_" + "A" * 40
        self.put(
            "E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9,
            blocker=f"waiting for NOTION_API_TOKEN={token} to be rotated",
        )

        printed, attention = self._run()

        self.assertEqual(len(attention), 1)
        self.assertNotIn(token, attention[0])
        self.assertIn("[REDACTED]", attention[0])
        self.assertNotIn(token, printed)
        # ...and the rest of the sentence survives, so the line is still
        # something a person can act on.
        self.assertIn("SEARCH", attention[0])
        self.assertIn("rotated", attention[0])

    def test_the_rollup_itself_keeps_the_blocker_verbatim(self):
        """Redaction belongs to the *view*, not the derivation: the rollup is
        also read by tests and by anything that needs the real text, and a
        derivation that silently rewrites its input would be the harder bug
        to find."""
        token = "ntn_" + "A" * 40
        self.put("E1", "SEARCH", "CTO_BACKEND", "BLOCKED", "BLOCKED", 9,
                 blocker=f"key {token}")

        from controltower import build_company_rollup

        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)

        self.assertIn(token, rollup.project("SEARCH").open_blocker)

    def test_blocked_projects_sort_above_the_rest(self):
        self.put("E1", "QUIET", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        self.put("E2", "BLOCKED_ONE", "CTO_FRONTEND", "BLOCKED", "BLOCKED", 15, blocker="k")

        printed, _attention = self._run()

        lines = [line for line in printed.splitlines() if "Event " in line and "  " in line]
        blocked_at = next(i for i, line in enumerate(lines) if "BLOCKED_ONE" in line)
        quiet_at = next(i for i, line in enumerate(lines) if "QUIET" in line)
        self.assertLess(blocked_at, quiet_at)


class TheDirectoryItselfCanFailTests(unittest.TestCase):
    """C47 branch-coverage sweep: `read_events()`'s two OS error paths had
    never run.

    Both are the shape this project keeps finding: a rejection branch on an
    external boundary that every test walks past because the boundary behaves.
    `processed/` is a directory the Collector writes and a scheduled task
    reads, on Windows, over a path that can be a OneDrive-backed folder --
    `os.scandir()` failing on the directory, and `DirEntry.is_file()` failing
    on one entry mid-walk, are the two ways that goes wrong.

    What must hold is that neither takes the run down and neither silently
    reports an empty company:

        scandir fails      the directory is named in `unreadable`
        is_file fails      that entry is named in `unreadable`, and the
                           others are still read

    The second line said "that entry is skipped" until C62, and skipping is
    what it did — in silence. One failed `stat` and an Event file that is on
    disk and perfectly readable left no trace anywhere: 17 files, 16 counted,
    nothing saying a 17th was seen. Resilience was the right goal and silence
    was the wrong way to reach it; `unreadable` is the channel this function
    already had for it.

    Driven by patching `os.scandir`, because a real permission failure is not
    reproducible on this platform without changing ACLs on a live path.
    """

    def _processed(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        processed = root / "processed"
        processed.mkdir()
        return processed

    def _write(self, processed, name, event_id):
        (processed / name).write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "event_id": event_id,
                    "timestamp": "2026-08-09T10:00:00+09:00",
                    "source": "DESKTOP_2",
                    "role": "CMO",
                    "project_id": "PRJ",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "milestone": "M1",
                    "summary": "work",
                    "blocker": None,
                    "evidence": [],
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

    def test_a_directory_that_cannot_be_listed_is_reported_not_empty(self):
        processed = self._processed()
        self._write(processed, "a.json", "A")

        with mock.patch(
            "controltower.rollup.os.scandir", side_effect=OSError("access denied")
        ):
            events, unreadable = read_events(processed)

        self.assertEqual(events, ())
        self.assertEqual(len(unreadable), 1)
        self.assertIn("access denied", unreadable[0][1])

    def test_the_rollup_over_that_directory_says_it_could_not_read_it(self):
        """The half that decides whether anyone notices: an unreadable
        directory must not arrive at the view as `Event 0건`, which is what a
        company with nothing to report also looks like."""
        processed = self._processed()
        self._write(processed, "a.json", "A")

        with mock.patch(
            "controltower.rollup.os.scandir", side_effect=OSError("access denied")
        ):
            rollup = build_company_rollup(
                processed_dir=processed, now=datetime(2026, 8, 12, 9, 0).astimezone()
            )

        self.assertEqual(rollup.events_read, 0)
        self.assertEqual(len(rollup.unreadable), 1)

    def test_one_entry_that_cannot_be_stat_ed_does_not_hide_the_others(self):
        processed = self._processed()
        for name, event_id in (("a.json", "A"), ("b.json", "B"), ("c.json", "C")):
            self._write(processed, name, event_id)
        real_scandir = os.scandir

        class _Broken:
            def __init__(self, entry):
                self._entry = entry
                self.name = entry.name
                self.path = entry.path

            def is_file(self):
                raise OSError("stat failed")

        def _scandir(path):
            return [
                _Broken(entry) if entry.name == "b.json" else entry
                for entry in real_scandir(path)
            ]

        with mock.patch("controltower.rollup.os.scandir", _scandir):
            events, unreadable = read_events(processed)

        self.assertEqual(sorted(event.event_id for event, _n in events), ["A", "C"])
        self.assertEqual([name for name, _why in unreadable], ["b.json"])
        self.assertIn("stat failed", unreadable[0][1])

    def test_an_entry_that_cannot_be_stat_ed_is_not_reported_as_absent(self):
        """The half that decides whether anyone notices.

        `A` and `C` being read is resilience; `B` leaving no trace is data
        loss. Every entry that carried an event filename must come out of
        this function either as an Event or as a named failure, because the
        view's arithmetic is exactly that sum — and `Event 2건` is also what
        a quieter company looks like.
        """
        processed = self._processed()
        for name, event_id in (("a.json", "A"), ("b.json", "B"), ("c.json", "C")):
            self._write(processed, name, event_id)
        real_scandir = os.scandir

        class _Broken:
            def __init__(self, entry):
                self._entry = entry
                self.name = entry.name
                self.path = entry.path

            def is_file(self):
                raise OSError("stat failed")

        def _scandir(path):
            return [
                _Broken(entry) if entry.name == "b.json" else entry
                for entry in real_scandir(path)
            ]

        with mock.patch("controltower.rollup.os.scandir", _scandir):
            events, unreadable = read_events(processed)

        self.assertEqual(len(events) + len(unreadable), 3)

    def test_the_rollup_carries_that_failure_to_the_view(self):
        """`read_events()` reporting it is worth nothing if the layer above
        drops it on the way to the screen."""
        processed = self._processed()
        self._write(processed, "a.json", "A")
        self._write(processed, "b.json", "B")
        real_scandir = os.scandir

        class _Broken:
            def __init__(self, entry):
                self._entry = entry
                self.name = entry.name
                self.path = entry.path

            def is_file(self):
                raise OSError("stat failed")

        def _scandir(path):
            return [
                _Broken(entry) if entry.name == "b.json" else entry
                for entry in real_scandir(path)
            ]

        with mock.patch("controltower.rollup.os.scandir", _scandir):
            rollup = build_company_rollup(
                processed_dir=processed, now=datetime(2026, 8, 12, 9, 0).astimezone()
            )

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual(len(rollup.unreadable), 1)

    def test_a_directory_named_like_an_event_is_still_deliberately_silent(self):
        """The case C62 did **not** change, recorded so the difference is a
        decision rather than an inconsistency.

        A directory is a known answer to "is this an Event?" — no. A failed
        `stat` is no answer at all, and the entry behind it is as likely to
        be a good Event file as anything else. Every reader in this
        repository treats the first case this way
        (`backup/working_copy.py`, `ops_status.py` twice), and pointing an
        operator at a directory would waste the trip.
        """
        processed = self._processed()
        self._write(processed, "a.json", "A")
        (processed / "EVT-DIR.json").mkdir()

        events, unreadable = read_events(processed)

        self.assertEqual(len(events), 1)
        self.assertEqual(unreadable, ())


class TheProjectListSaysWhenItIsTruncatedTests(ControlTowerBlockTests):
    """C47 branch sweep: four display branches of the CONTROL TOWER block had
    never run, and one of them is a silent cap.

    `_CONTROL_TOWER_PROJECT_LINES` shows the eight projects an operator most
    likely wants -- blocked first, then longest-quiet. A ninth exists and the
    line that says so had no test, which is the shape this project treats as
    a defect in its own right: a bounded list that does not say it is bounded
    reads as "these are all of them".

    The other three are the empty-rollup paths (no team, no Desktop) and a
    completed project's line, which is the only project state the block can
    print that no test had ever produced.
    """

    def test_the_ninth_project_is_reported_as_a_count(self):
        for index in range(9):
            self.put(f"E{index}", f"P{index}", "COO", "STARTED", "IN_PROGRESS", 1 + index)

        printed, attention = self._run()

        self.assertIn("외 1건", printed)
        self.assertEqual(attention, [])

    def test_exactly_eight_projects_say_nothing_about_a_remainder(self):
        for index in range(8):
            self.put(f"E{index}", f"P{index}", "COO", "STARTED", "IN_PROGRESS", 1 + index)

        printed, _attention = self._run()

        self.assertNotIn("외 ", printed)

    def test_a_completed_project_shows_its_completion_date(self):
        self.put("E1", "SHIP", "COO", "STARTED", "IN_PROGRESS", 5)
        self.put("E2", "SHIP", "COO", "COMPLETED", "COMPLETED", 9)

        printed, attention = self._run()

        self.assertIn("완료", printed)
        self.assertIn("2026-08-09", printed)
        self.assertEqual(attention, [])

    def test_an_empty_runtime_names_every_team_and_desktop_as_silent(self):
        """Written the other way round first, asserting the headings were
        omitted -- and that was the wrong expectation. Both folds seed every
        entry in docs/02 §8's table, so an empty runtime says "asked all four
        Desktops, none reported" rather than saying nothing, which is the
        distinction this block exists to make.

        `Project` is the one section that really is omitted: a project only
        exists because an Event named it, so there is no table to seed from.
        """
        printed, attention = self._run()

        self.assertIn("Event 0건", printed)
        for source in ("DESKTOP_1", "DESKTOP_2", "DESKTOP_3", "DESKTOP_4"):
            self.assertIn(source, printed)
        self.assertEqual(printed.count("이 기간 활동 없음"), 4)
        self.assertEqual(printed.count("이 기간 Event 없음"), 4)
        self.assertNotIn("  Project\n", printed)
        self.assertEqual(attention, [])


class TheEvidenceRangeCheckSurvivesBadInputTests(CompanyHistoryCanOutliveTheEvidenceTests):
    """C47 branch sweep: three rejection paths of the evidence-range check had
    never run.

    All three read something off disk. `_event_day()` parses an Event's own
    timestamp string, and `_company_history_older_than_the_evidence()` opens
    Daily files by name. Both inputs are files a person can edit (docs/06 §57)
    and files a restore can leave half-written, which is the whole reason the
    guards are there -- and untested guards on exactly that kind of input are
    what this project's C20 sweep was about.

    What must hold in every case: the qualifier is a qualifier. It may be
    wrong-but-quiet about a damaged tree; it may not take the block down, and
    it may not turn into an alert nobody can clear.
    """

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_edges", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_an_unreadable_timestamp_is_not_a_date(self):
        """`_event_day()` is handed `ProjectRollup.first_seen`, which is an
        Event's `timestamp` field read back out of a JSON file. Every value
        below has been seen in this repository's own fixtures for damaged or
        hand-written Events."""
        module = self._module()
        for value in (None, "", "not a date", "2026-13-45T00:00:00+09:00", 7):
            with self.subTest(value=value):
                self.assertIsNone(module._event_day(value))

    def test_a_readable_timestamp_still_gives_its_day(self):
        """The control: a guard that returns None for everything would pass
        the test above and silence the qualifier forever."""
        module = self._module()

        self.assertEqual(
            module._event_day("2026-08-09T10:00:00+09:00"), date(2026, 8, 9)
        )

    def test_a_daily_file_that_cannot_be_opened_is_skipped_not_fatal(self):
        """A date-named **directory** standing where a Daily file belongs --
        the shape `_daily_dates()` and the backup scope checks already handle
        elsewhere. `read_text()` raises, and the scan must continue to the
        next day rather than take the block down or answer from that day.
        """
        module = self._module()
        runtime = self._runtime(events_from=12)
        daily = runtime / "local_master" / "daily"
        (daily / "2026-08-01.md").unlink()
        (daily / "2026-08-01.md").mkdir()

        printed, attention = self._lines(runtime)

        self.assertIn("증거 범위 밖", printed)
        # 08-01 could not be read, so the earliest day carrying work that it
        # could read is 08-02 -- reported, rather than the scan stopping.
        self.assertIn("2026-08-02", printed)
        self.assertEqual(attention, [])

    def test_every_daily_file_unreadable_leaves_the_block_quiet(self):
        """The far end of the same path: nothing can be read, so nothing can
        be claimed. Silence is the honest answer -- and the block still
        prints."""
        module = self._module()
        runtime = self._runtime(events_from=12)
        daily = runtime / "local_master" / "daily"
        for path in sorted(daily.glob("*.md")):
            path.unlink()
            path.mkdir()

        printed, attention = self._lines(runtime)

        self.assertIn("CONTROL TOWER", printed)
        self.assertNotIn("증거 범위 밖", printed)
        self.assertEqual(attention, [])

    def test_a_daily_file_that_is_not_utf8_is_skipped(self):
        """The other half of the same guard, and the one a date-named
        directory does not reach: `_daily_dates()` lists files, so a directory
        never gets as far as `read_text()`. A file whose bytes are not UTF-8
        does -- `UnicodeDecodeError` is a `ValueError`, which is why the
        clause names both. A truncated or half-restored Daily file is exactly
        this shape.
        """
        runtime = self._runtime(events_from=12)
        daily = runtime / "local_master" / "daily"
        (daily / "2026-08-01.md").write_bytes(
            b"# H" + bytes([10, 10]) + b"- Event ID: " + bytes([255, 254]) + bytes([10])
        )

        printed, attention = self._lines(runtime)

        self.assertIn("증거 범위 밖", printed)
        self.assertIn("2026-08-02", printed)
        self.assertEqual(attention, [])


class OneEventIsCountedOnceTests(ControlTowerTestCase):
    """C50: two files, one `event_id`, and every number was doubled.

    Not a hypothetical. `collector/runtime.py` files a DUPLICATE into
    `processed/` under **the incoming filename** rather than under
    `safe_event_filename(event_id)`, so the same Event arriving twice under
    two names leaves two files behind. Driven through the real Collector in
    `TheRealCollectorProducesTheSecondFileTests` below; the fixtures here
    place the two files directly so each property can be stated on its own.

    Measured before the fold, on one MILESTONE_COMPLETED Event:

        events_read              2
        PAY event_count          2
        metric milestones        2      <- a company KPI, doubled
        DESKTOP_1 event_count    2

    The Collector was right about all of it — it *detected* the duplicate and
    logged `duplicate=1` — and keeping the file rather than deleting it is
    docs/10 §46's rule. Only this module's counting was wrong.
    """

    def _twin(self, event, name):
        """A second file under `name` carrying the same Event."""
        (self.processed / name).write_text(event.to_json(), encoding="utf-8")

    def test_the_same_event_under_two_names_is_one_event(self):
        event = self.put("E1", "PAY", "CTO_BACKEND", "MILESTONE_COMPLETED",
                         "IN_PROGRESS", 12, milestone="M1")
        self._twin(event, "hand-copy.json")

        rollup = self.rollup()

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual(rollup.project("PAY").event_count, 1)
        self.assertEqual(rollup.metric("events").value, 1)
        self.assertEqual(rollup.metric("milestones_completed").value, 1)
        self.assertEqual(
            [d.event_count for d in rollup.desktops if d.source == "DESKTOP_1"], [1]
        )

    def test_the_folded_file_is_reported_rather_than_dropped(self):
        """A number that silently differs from the file count in the
        directory is one nobody can check — the same reason `unreadable`
        exists."""
        event = self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        self._twin(event, "hand-copy.json")

        duplicates = self.rollup().duplicates

        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].event_id, "E1")
        self.assertEqual(duplicates[0].kept, "E1.json")
        self.assertEqual(duplicates[0].ignored, "hand-copy.json")
        self.assertTrue(duplicates[0].identical)

    def test_two_different_events_are_still_two(self):
        """The guard must not fold anything but a genuine repeat."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        self.put("E2", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        rollup = self.rollup()

        self.assertEqual(rollup.events_read, 2)
        self.assertEqual(rollup.duplicates, ())

    def test_which_file_is_kept_does_not_change_between_runs(self):
        """`read_events()` returns the directory in name order and the sort is
        stable, so the same file is counted every time. A rollup that picked a
        different file each run would make two consecutive status views
        disagree for no reason a person could see."""
        event = self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        for name in ("aaa.json", "zzz.json", "mmm.json"):
            self._twin(event, name)

        kept = {self.rollup().duplicates[0].kept for _ in range(5)}

        self.assertEqual(kept, {"E1.json"})
        self.assertEqual(len(self.rollup().duplicates), 3)

    def test_a_conflicting_twin_is_named_as_a_conflict(self):
        """Same `event_id`, different contents: one of the two is not the
        Event it claims to be, and which one got counted is decided by
        filename order. That is a fact about the data, not about the fold."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        impostor = create_event(
            source="DESKTOP_2",
            role="CMO",
            project_id="BRAND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="not the same event at all",
            history_candidate=True,
            event_id="E1",
            timestamp="2026-08-12T09:00:00+09:00",
        )
        (self.processed / "zz-impostor.json").write_text(
            impostor.to_json(), encoding="utf-8"
        )

        duplicates = self.rollup().duplicates

        self.assertEqual(len(duplicates), 1)
        self.assertFalse(duplicates[0].identical)
        self.assertEqual(duplicates[0].kept, "E1.json")
        self.assertEqual(duplicates[0].ignored, "zz-impostor.json")

    def test_only_the_conflicting_kind_reaches_the_risk_panel(self):
        """An identical twin needs no operator. A contradicting one does."""
        from controltower import build_dashboard

        event = self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        self._twin(event, "benign-copy.json")

        panel = build_dashboard(self.rollup(), now=NOW).panel("RISKS")
        self.assertEqual(
            [r for r in panel.rows if r.values["kind"] == "EVENT_ID_CONFLICT"], []
        )

        impostor = create_event(
            source="DESKTOP_2", role="CMO", project_id="BRAND", event_type="STARTED",
            status="IN_PROGRESS", summary="different", history_candidate=True,
            event_id="E1", timestamp="2026-08-12T09:00:00+09:00",
        )
        (self.processed / "zz-impostor.json").write_text(
            impostor.to_json(), encoding="utf-8"
        )

        panel = build_dashboard(self.rollup(), now=NOW).panel("RISKS")
        conflicts = [r for r in panel.rows if r.values["kind"] == "EVENT_ID_CONFLICT"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].values["kept"], "E1.json")
        self.assertEqual(conflicts[0].values["ignored"], "zz-impostor.json")

    def test_the_coverage_counts_them_without_calling_the_view_incomplete(self):
        """A folded duplicate makes the numbers right, not partial. A
        qualifier that fires on a correct answer is the standing alarm this
        project keeps removing."""
        from controltower import build_dashboard

        event = self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        self._twin(event, "hand-copy.json")

        # `with_history_coverage(None)` holds the *other* input to `complete`
        # steady, so this test is about duplicates and nothing else. Since
        # C56 an unchecked model is incomplete for a reason unrelated to
        # folding, and leaving it unchecked here would make the assertion
        # below pass or fail for the wrong cause.
        coverage = (
            build_dashboard(self.rollup(), now=NOW)
            .with_history_coverage(None)
            .coverage
        )

        self.assertEqual(coverage.duplicates, 1)
        self.assertEqual(coverage.unreadable, 0)
        self.assertTrue(coverage.complete)


class TheRealCollectorProducesTheSecondFileTests(unittest.TestCase):
    """The premise, driven rather than asserted.

    `OneEventIsCountedOnceTests` places two files by hand. This runs the real
    `collector.run_once()` twice over the same Event under two filenames and
    checks that the second one really does end up in `processed/` — because
    if it did not, the fold above would be guarding against nothing.

    docs/11 permits writing into `incoming/` by hand, which is the shortest
    way to produce this; a re-sent OneDrive delivery under a different name
    and a partial restore produce the same shape.
    """

    def test_a_duplicate_the_collector_detects_still_lands_in_processed(self):
        from collector import Collector
        from collector.runtime import run_once as collector_run_once
        from collector.state import PersistentSeenEventStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        incoming, processed, rejected = root / "in", root / "proc", root / "rej"
        for directory in (incoming, processed, rejected):
            directory.mkdir(parents=True)

        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="PAY",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS",
            summary="one real event", history_candidate=True, milestone="M1",
            timestamp="2026-08-12T10:00:00+09:00",
        )
        first = incoming / "EVT-1.json"
        first.write_text(event.to_json(), encoding="utf-8")

        collector = Collector(
            seen_store=PersistentSeenEventStore(root / "collector_state.json")
        )
        run_one = collector_run_once(
            collector=collector, incoming_dir=incoming, processed_dir=processed,
            rejected_dir=rejected, log_path=root / "collector.log",
        )
        (incoming / "a-hand-placed-copy.json").write_text(
            event.to_json(), encoding="utf-8"
        )
        run_two = collector_run_once(
            collector=collector, incoming_dir=incoming, processed_dir=processed,
            rejected_dir=rejected, log_path=root / "collector.log",
        )

        self.assertEqual((run_one.accepted, run_one.duplicate), (1, 0))
        self.assertEqual((run_two.accepted, run_two.duplicate), (0, 1))
        self.assertEqual(
            sorted(p.name for p in processed.iterdir()),
            ["EVT-1.json", "a-hand-placed-copy.json"],
        )

        rollup = build_company_rollup(processed_dir=processed, now=NOW)

        self.assertEqual(rollup.events_read, 1)
        self.assertEqual(rollup.metric("milestones_completed").value, 1)
        self.assertEqual(len(rollup.duplicates), 1)
        self.assertTrue(rollup.duplicates[0].identical)


if __name__ == "__main__":
    unittest.main()
