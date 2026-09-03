"""`controltower/kpi.py` — the role KPI set, and its refusals.

Two properties matter more than any individual number here.

**A KPI never renders as a plausible zero.** The failure this module is
built against is not a crash; it is a CEO reading `Runway 0` — or worse
`Runway 12` — off a system that has no financial data at all. So the tests
below check the *refusal path* at least as hard as the measured one.

**A measured KPI is the rollup's own number.** Not a similar number computed
here. Two counts of one thing shown side by side is the C28 defect at its
worst, because a dashboard is exactly where the two would appear together.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from controltower.kpi import (  # noqa: E402
    DATA_REQUIRED,
    MEASURED,
    ROLES,
    build_kpi_set,
)
from controltower.rollup import build_company_rollup  # noqa: E402
from delivery import Commit, GitActivity  # noqa: E402
from events import create_event  # noqa: E402

NOW = datetime.fromisoformat("2026-08-20T10:00:00+09:00")

SOURCE_FOR_ROLE = {
    "CTO_BACKEND": "DESKTOP_1",
    "CMO": "DESKTOP_2",
    "CTO_FRONTEND": "DESKTOP_3",
    "COO": "DESKTOP_4",
}


class KpiTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.processed = Path(tmp.name)

    def put(self, event_id, project, role, event_type, status, day, **extra):
        event = create_event(
            source=SOURCE_FOR_ROLE[role],
            role=role,
            project_id=project,
            event_type=event_type,
            status=status,
            summary=extra.pop("summary", None) or f"summary for {event_id}",
            history_candidate=True,
            event_id=event_id,
            timestamp=f"2026-08-{day:02d}T09:00:00+09:00",
            **extra,
        )
        (self.processed / f"{event_id}.json").write_text(
            event.to_json(), encoding="utf-8"
        )
        return event

    def kpis(self, *, activity=None):
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)
        return build_kpi_set(rollup, now=NOW, activity=activity)


class NothingIsInventedTests(KpiTestCase):
    def test_every_ceo_kpi_is_data_required(self):
        """The finding, asserted rather than described: this system measures
        execution and does not measure the business.

        If a CEO KPI ever becomes measurable, this test fails and whoever
        made it measurable has to say where the money data came from.
        """
        for kpi in self.kpis().for_role("CEO"):
            with self.subTest(kpi=kpi.key):
                self.assertEqual(kpi.status, DATA_REQUIRED)
                self.assertIsNone(kpi.value)
                self.assertEqual(kpi.rendered(), "DATA REQUIRED")

    def test_every_dora_kpi_is_data_required_even_with_git_activity(self):
        """The temptation this module exists to refuse.

        Git is right there, and commits are countable. A commit is not a
        deployment, so Deployment Frequency stays refused **while git is
        available and non-empty** — which is the only state in which the
        shortcut is tempting.
        """
        activity = GitActivity(
            available=True,
            since=date(2026, 8, 1),
            until=date(2026, 8, 20),
            commits=tuple(
                Commit(sha=f"{i:040d}", at="2026-08-10T09:00:00+09:00",
                       author="a", subject="s", files=("f.py",))
                for i in range(9)
            ),
        )
        kpis = self.kpis(activity=activity)

        for key in (
            "deployment_frequency",
            "change_lead_time",
            "change_failure_rate",
            "failed_deployment_recovery_time",
            "deployment_rework_rate",
            "reliability_slo",
            "critical_technical_debt",
        ):
            with self.subTest(kpi=key):
                self.assertEqual(kpis.get(key).status, DATA_REQUIRED)
        # And the honest number is there instead, under a name nobody can
        # mistake for DORA.
        self.assertEqual(kpis.get("code_commits").value, 9)

    def test_every_data_required_kpi_says_what_it_would_need(self):
        """`requires` is the whole value of a refusal. "Not available" with
        no sentence is a blank with extra steps."""
        for kpi in self.kpis().data_required:
            with self.subTest(kpi=kpi.key):
                self.assertGreater(
                    len(kpi.requires.strip()),
                    20,
                    f"{kpi.key} refuses without saying what is missing",
                )

    #: Everything this system can answer when it has both Events and git.
    #: Written out rather than derived, so the sweep below cannot shrink in
    #: silence: a KPI that stops being measurable has to be removed from
    #: this list by hand, which is the review.
    EVERY_MEASURABLE = (
        "blocked_items",
        "critical_risk_count",
        "open_issues",
        "pending_decisions",
        "issue_aging",
        "decision_aging",
        "unexecuted_decisions",
        "unassigned_items",
        "execution_aging",
        "execution_completion_rate",
        "code_commits",
        "code_files_changed",
        "code_contributors",
    )

    def _everything_measurable(self):
        """A company and a git log that between them measure all ten.

        Without this the sweeps below ran over whatever an **empty** tree
        happens to measure — six of the ten, all of them zeros — while their
        names claimed "every measured KPI". `execution_completion_rate` and
        the three git numbers were never once checked, which is the vacuous
        pass this file's own docstring is about, in this file.
        """
        self.put("A1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put("A2", "PAY", "COO", "COMPLETED", "COMPLETED", 6)
        self.put("A3", "OPSX", "COO", "BLOCKED", "BLOCKED", 7, blocker="waiting")
        self.put("A4", "GROWTH", "CMO", "ISSUE_RAISED", "IN_PROGRESS", 8)
        self.put("A5", "LEGAL", "COO", "DECISION_REQUIRED", "IN_PROGRESS", 9)
        self.put("A6", "VENDOR", "COO", "AT_RISK", "AT_RISK", 10)
        # Approved and left undone, so `unexecuted_decisions` and
        # `execution_aging` are exercised rather than passing on zero.
        self.put("A7", "ROLLOUT", "COO", "DECISION_APPROVED", "IN_PROGRESS", 11)
        activity = GitActivity(
            available=True,
            since=date(2026, 8, 1),
            until=date(2026, 8, 20),
            commits=(
                Commit(sha="a" * 40, at="2026-08-10T09:00:00+09:00",
                       author="one", subject="s", files=("a.py",)),
            ),
        )
        return self.kpis(activity=activity)

    def test_the_fixture_measures_every_kpi_that_can_be_measured(self):
        """Guards the two sweeps below against running over a subset.

        Equality, not a subset check: a KPI that becomes measurable and is
        not listed fails here rather than slipping past the sweeps unchecked.
        """
        measured = {kpi.key for kpi in self._everything_measurable().measured}

        self.assertEqual(measured, set(self.EVERY_MEASURABLE))

    def test_every_measured_kpi_says_where_its_number_came_from(self):
        kpis = self._everything_measurable()

        self.assertEqual(len(kpis.measured), len(self.EVERY_MEASURABLE))
        for kpi in kpis.measured:
            with self.subTest(kpi=kpi.key):
                self.assertGreater(len(kpi.source.strip()), 10)

    def test_no_measured_kpi_renders_as_the_refusal_wording(self):
        """The pair of `test_every_ceo_kpi_is_data_required`. A measured KPI
        that rendered `DATA REQUIRED` would make the two states
        indistinguishable from the reader's side, which is the whole
        contract `Kpi.rendered()` exists to hold."""
        for kpi in self._everything_measurable().measured:
            with self.subTest(kpi=kpi.key):
                self.assertNotEqual(kpi.rendered(), "DATA REQUIRED")
                self.assertIsNotNone(kpi.value)

    def test_no_kpi_claims_to_reach_a_goal(self):
        """`UNSOURCED_LAYERS` says Goal has no source. A KPI claiming to
        connect to one would be this module's first invented fact."""
        for kpi in self.kpis().kpis:
            with self.subTest(kpi=kpi.key):
                self.assertNotIn("Goal", kpi.chain)


class TheMeasuredOnesAreTheRollupsOwnNumbersTests(KpiTestCase):
    """Not "agree with", but "are". Each assertion reads the `Metric` and the
    `Kpi` and requires the same object identity of evidence, so a KPI that
    recounted the Events could not pass by arriving at the same total."""

    def _rollup_and_kpis(self):
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)
        return rollup, build_kpi_set(rollup, now=NOW)

    def test_blocked_items_is_the_open_blockers_metric(self):
        self.put("E1", "PAY", "COO", "BLOCKED", "BLOCKED", 10, blocker="legal")
        rollup, kpis = self._rollup_and_kpis()

        metric = rollup.metric("open_blockers")
        kpi = kpis.get("blocked_items")

        self.assertEqual(kpi.value, metric.value)
        self.assertEqual(kpi.evidence, metric.evidence)
        self.assertEqual(kpi.source, metric.source)

    def test_open_issues_is_the_issues_open_metric(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "ISSUE_RAISED", "IN_PROGRESS", 10)
        rollup, kpis = self._rollup_and_kpis()

        self.assertEqual(
            kpis.get("open_issues").evidence, rollup.metric("issues_open").evidence
        )
        self.assertEqual(kpis.get("open_issues").value, 1)


class AgingIsMeasuredFromTheOpeningEventTests(KpiTestCase):
    """The two KPIs C149's Event vocabulary made computable at all.

    Before `ISSUE_RAISED` and `DECISION_REQUIRED` existed, an Issue had no
    recordable start; its age was not unimplemented, it was undefined.
    """

    def test_issue_aging_is_the_days_since_the_issue_was_raised(self):
        self.put("E1", "SEARCH", "CTO_BACKEND", "ISSUE_RAISED", "IN_PROGRESS", 10)

        kpi = self.kpis().get("issue_aging")

        self.assertEqual(kpi.status, MEASURED)
        self.assertEqual(kpi.value, 10)  # 8/10 -> 8/20

    def test_decision_aging_is_the_days_since_the_decision_was_required(self):
        self.put("E1", "PAY", "COO", "DECISION_REQUIRED", "IN_PROGRESS", 15)

        self.assertEqual(self.kpis().get("decision_aging").value, 5)

    def test_a_resolved_issue_is_not_aged(self):
        """The fold, from the KPI's side: closing the lifecycle removes it,
        so aging does not report a settled Issue as still open."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "ISSUE_RAISED", "IN_PROGRESS", 10)
        self.put("E2", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "IN_PROGRESS", 12)

        kpis = self.kpis()

        self.assertEqual(kpis.get("open_issues").value, 0)
        self.assertEqual(kpis.get("issue_aging").value, 0)

    def test_a_rejected_decision_closes_it_exactly_as_an_approval_does(self):
        """The asymmetry C149 removed. A rejection settles a Decision; not
        recording it left the decision permanently pending, which is a false
        statement about a company that has actually decided."""
        self.put("E1", "PAY", "COO", "DECISION_REQUIRED", "IN_PROGRESS", 10)
        self.put("E2", "PAY", "COO", "DECISION_REJECTED", "IN_PROGRESS", 12)

        self.assertEqual(self.kpis().get("pending_decisions").value, 0)

    def test_a_reopened_issue_is_aged_from_the_reopening(self):
        """Not from the first raising. An Issue that was resolved and came
        back has not been open the whole time, and reporting the older date
        would overstate the age by exactly the time it was closed."""
        self.put("E1", "SEARCH", "CTO_BACKEND", "ISSUE_RAISED", "IN_PROGRESS", 2)
        self.put("E2", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "IN_PROGRESS", 4)
        self.put("E3", "SEARCH", "CTO_BACKEND", "ISSUE_RAISED", "IN_PROGRESS", 18)

        self.assertEqual(self.kpis().get("issue_aging").value, 2)

    def test_two_open_issues_on_one_project_do_not_read_as_one_resolved(self):
        """The measured misreading. Before the labels named their unit:

            제기된 Issue     2
            열려 있는 Issue   1
            해결된 Issue     0

        A reader subtracts without being asked and concludes one was
        resolved. None was. The count is right — one project has something
        open — and the *word* beside it was the lie.

        Checked at the metric layer rather than the KPI's, because that is
        where the three numbers appear together and where the subtraction
        happens.
        """
        self.put("I1", "BILLING", "COO", "ISSUE_RAISED", "IN_PROGRESS", 10,
                 summary="invoice totals drift")
        self.put("I2", "BILLING", "COO", "ISSUE_RAISED", "IN_PROGRESS", 12,
                 summary="refunds double-charge")
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)

        self.assertEqual(rollup.metric("issues_raised").value, 2)
        self.assertEqual(rollup.metric("issues_open").value, 1)
        self.assertEqual(rollup.metric("issues_resolved").value, 0)
        # The unit is in the label, so the three numbers cannot be read as a
        # subtraction that never happened.
        self.assertIn("Project", rollup.metric("issues_open").label)
        self.assertNotIn("Project", rollup.metric("issues_raised").label)

    def test_the_folded_openings_are_counted_not_dropped(self):
        """`occurrences` is what makes the fold a fold rather than a loss.

        The older Issue's own words cannot be kept — no Event field
        identifies an Issue across two Events, so a later `ISSUE_RESOLVED`
        would not say which one it closed. The count can be kept, and is.
        """
        self.put("I1", "BILLING", "COO", "ISSUE_RAISED", "IN_PROGRESS", 10,
                 summary="invoice totals drift")
        self.put("I2", "BILLING", "COO", "ISSUE_RAISED", "IN_PROGRESS", 12,
                 summary="refunds double-charge")
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)

        item = rollup.open_items[0]
        self.assertEqual(item.occurrences, 2)
        # The newest, so the age is not overstated by the time the older one
        # spent before this one was raised.
        self.assertEqual(item.summary, "refunds double-charge")

    def test_a_single_opening_is_not_dressed_up_as_a_fold(self):
        """The other side: `occurrences` must be 1 on the ordinary case, or
        every row would carry a meaningless "외 0건"."""
        self.put("I1", "BILLING", "COO", "ISSUE_RAISED", "IN_PROGRESS", 10)
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)

        self.assertEqual(rollup.open_items[0].occurrences, 1)

    def test_nothing_open_is_a_measured_zero_and_not_a_refusal(self):
        """The distinction that decides whether a reader trusts a `0`. The
        window was read and held nothing open — that is an answer."""
        kpi = self.kpis().get("issue_aging")

        self.assertEqual(kpi.status, MEASURED)
        self.assertEqual(kpi.value, 0)
        self.assertIn("없다", kpi.source)

    def test_the_oldest_open_item_is_the_one_reported(self):
        """Not a mean. The decision a COO makes off this number is about one
        item — the one that has waited longest — and a mean hides it behind
        the ones that were settled quickly."""
        self.put("E1", "A", "COO", "DECISION_REQUIRED", "IN_PROGRESS", 2)
        self.put("E2", "B", "COO", "DECISION_REQUIRED", "IN_PROGRESS", 18)

        self.assertEqual(self.kpis().get("decision_aging").value, 18)


class TheGitKpisSayWhatTheyAreTests(KpiTestCase):
    def test_no_git_activity_is_data_required_and_not_zero(self):
        kpis = self.kpis(activity=None)

        for key in ("code_commits", "code_files_changed", "code_contributors"):
            with self.subTest(kpi=key):
                kpi = kpis.get(key)
                self.assertEqual(kpi.status, DATA_REQUIRED)
                self.assertIsNone(kpi.value)

    def test_an_unreadable_git_carries_gits_own_reason(self):
        kpis = self.kpis(
            activity=GitActivity(available=False, reason="git is not installed")
        )

        kpi = kpis.get("code_commits")
        self.assertEqual(kpi.status, DATA_REQUIRED)
        self.assertIn("git is not installed", kpi.requires)

    def test_a_readable_but_empty_git_is_a_measured_zero(self):
        """The other half. "Nobody committed yesterday" is a fact about the
        company; "git could not be read" is a fact about this program. They
        must not render the same."""
        kpis = self.kpis(
            activity=GitActivity(
                available=True, since=date(2026, 8, 19), until=date(2026, 8, 19)
            )
        )

        kpi = kpis.get("code_commits")
        self.assertEqual(kpi.status, MEASURED)
        self.assertEqual(kpi.value, 0)
        self.assertEqual(kpi.rendered(), "0")

    def test_the_source_line_denies_being_a_deployment_number(self):
        """The label alone is not enough — somebody reading a dashboard will
        ask whether this is the DORA number. The row answers."""
        kpis = self.kpis(
            activity=GitActivity(
                available=True, since=date(2026, 8, 1), until=date(2026, 8, 2)
            )
        )

        self.assertIn("배포가 아니라", kpis.get("code_commits").source)


class TheSetItselfIsWellFormedTests(KpiTestCase):
    def test_every_kpi_belongs_to_a_declared_role(self):
        for kpi in self.kpis().kpis:
            with self.subTest(kpi=kpi.key):
                self.assertIn(kpi.role, ROLES)

    def test_every_role_has_at_least_one_kpi(self):
        """A role with none would render as an empty section, which reads as
        "nothing to report" rather than "not written"."""
        for role in ROLES:
            with self.subTest(role=role):
                self.assertTrue(self.kpis().for_role(role))

    def test_keys_are_unique(self):
        keys = [kpi.key for kpi in self.kpis().kpis]

        self.assertEqual(len(keys), len(set(keys)))

    def test_measured_and_data_required_partition_the_set(self):
        kpis = self.kpis()

        self.assertEqual(
            len(kpis.measured) + len(kpis.data_required), len(kpis.kpis)
        )

    def test_the_set_is_deterministic(self):
        """Two builds over one tree must produce the same rows in the same
        order, or a diff between two published dashboards means nothing."""
        self.put("E1", "PAY", "COO", "DECISION_REQUIRED", "IN_PROGRESS", 10)

        first = [(k.key, k.rendered()) for k in self.kpis().kpis]
        second = [(k.key, k.rendered()) for k in self.kpis().kpis]

        self.assertEqual(first, second)

    def test_an_empty_company_refuses_the_completion_rate_rather_than_saying_zero(self):
        """0/0 is not 0%. A rate over no projects does not exist, and "0%"
        would read as "we finished nothing", which is a different and false
        claim about a week nobody worked."""
        kpi = self.kpis().get("execution_completion_rate")

        self.assertEqual(kpi.status, DATA_REQUIRED)
        self.assertIn("분모", kpi.requires)

    def test_the_completion_rate_is_a_percentage_of_projects_that_moved(self):
        self.put("E1", "A", "COO", "STARTED", "IN_PROGRESS", 10)
        self.put("E2", "B", "COO", "COMPLETED", "COMPLETED", 11)

        kpi = self.kpis().get("execution_completion_rate")

        self.assertEqual(kpi.status, MEASURED)
        self.assertEqual(kpi.value, 50.0)
        self.assertEqual(kpi.rendered(), "50.0%")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
