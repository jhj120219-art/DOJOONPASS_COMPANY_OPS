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
        event = create_event(
            source=SOURCE_FOR_ROLE[role],
            role=role,
            project_id=project,
            event_type=event_type,
            status=status,
            summary=f"summary for {event_id}",
            history_candidate=True,
            event_id=event_id,
            timestamp=f"2026-08-{day:02d}T09:00:00+09:00",
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


class NoInventedLayersTests(unittest.TestCase):
    """Goal / Team Goal / Sprint / Task have no source in this system.

    The failure this guards against is the tempting one: a Control Tower that
    shows an empty Goals panel reads as "목표가 없다", and one that invents a
    Goal from, say, `milestone` reads as authoritative. Saying "물어볼 곳이
    없다" is the only true option until the decision in BACKLOG is taken.
    """

    def test_the_unsourced_layers_are_named(self):
        self.assertEqual(
            set(UNSOURCED_LAYERS), {"COMPANY_GOAL", "TEAM_GOAL", "SPRINT", "TASK"}
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

    def test_staging_residue_is_not_an_event(self):
        """`processed/` can hold a `.tmp-…json`: a staging file left in
        `incoming/` is complete JSON often enough that the Collector accepts
        it and moves it here under the staging name."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 1)
        shutil.copy(self.processed / "E1.json", self.processed / ".tmp-abc.json")

        self.assertEqual(self.rollup().events_read, 1)

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
        is_file fails      that entry is skipped, the others are still read

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
