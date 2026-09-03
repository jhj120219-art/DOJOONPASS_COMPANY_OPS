"""Ten company situations, put through the whole read path at once.

Why this file exists separately from the panel tests
----------------------------------------------------
Every other Control Tower suite fixes one panel and asks whether it is
right. None of them asks the question a person actually has: **I created
three blocked projects — does the screen show three?**

That question fails differently from a panel bug. Nothing raises, no
assertion about a single row is violated, and each layer is individually
correct; the loss happens *between* layers — a filter that drops one, a
bound that cuts one, a fold that merges two, a sort that hides one below a
limit. C50 is the precedent in this repository: duplicate Events inflated
every rollup number while every panel test passed.

So this builds one company with ten simultaneous situations in it, runs the
real `build_company_rollup -> build_dashboard -> to_payload` path once, and
counts. The count is the assertion.

The ten (the request's own list)
--------------------------------
    1  a healthy project             6  a decision approved
    2  three blocked projects        7  a critical risk (at risk, not stopped)
    3  an issue raised               8  a KPI with a number
    4  an issue resolved             9  a KPI with no data
    5  a decision required          10  a D+1 change from git

Three blocked, not one, on purpose: one is not enough to catch an off-by-one
and it is the exact shape the request names.
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

from controltower import build_company_rollup, build_dashboard  # noqa: E402
from controltower.kpi import DATA_REQUIRED, MEASURED, build_kpi_set  # noqa: E402
from delivery import Commit, GitActivity  # noqa: E402
from events import create_event  # noqa: E402

NOW = datetime.fromisoformat("2026-08-20T10:00:00+09:00")

SOURCE_FOR_ROLE = {
    "CTO_BACKEND": "DESKTOP_1",
    "CMO": "DESKTOP_2",
    "CTO_FRONTEND": "DESKTOP_3",
    "COO": "DESKTOP_4",
}

#: The commits behind scenario 10. A fixture rather than this repository's
#: real log: a scenario test whose expected numbers changed every time
#: somebody committed would be rewritten instead of read.
GIT = GitActivity(
    available=True,
    since=date(2026, 8, 19),
    until=date(2026, 8, 19),
    commits=(
        Commit(
            sha="a" * 40,
            at="2026-08-19T18:00:00+09:00",
            author="Backend",
            subject="search: fix ranking regression",
            files=("src/search/rank.py", "tests/test_rank.py"),
        ),
        Commit(
            sha="b" * 40,
            at="2026-08-19T11:00:00+09:00",
            author="Frontend",
            subject="checkout: new payment step",
            files=("src/web/checkout.tsx",),
        ),
    ),
)


class CompanyScenarioTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.processed = Path(tmp.name)
        self._build_company()

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

    def _build_company(self):
        # 1. A healthy project: started, a milestone, still moving.
        self.put("H1", "SEARCH", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put(
            "H2", "SEARCH", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 12,
            milestone="ranking v2",
        )

        # 2. THREE blocked projects. Different teams and different days, so a
        # fold keyed on the wrong field, a sort that truncates, or a filter
        # that keeps only the newest would each drop a different one.
        self.put("B1", "PAY", "COO", "BLOCKED", "BLOCKED", 8, blocker="vendor key missing")
        self.put("B2", "BRAND", "CMO", "BLOCKED", "BLOCKED", 11, blocker="legal review")
        self.put(
            "B3", "MOBILE", "CTO_FRONTEND", "BLOCKED", "BLOCKED", 14,
            blocker="app store account suspended",
        )

        # 3. An Issue raised and left open.
        self.put(
            "I1", "BILLING", "CTO_BACKEND", "ISSUE_RAISED", "IN_PROGRESS", 10,
            summary="invoice totals drift under concurrent writes",
        )

        # 4. An Issue raised and resolved — must NOT appear as open.
        self.put("I2", "SEARCH", "CTO_BACKEND", "ISSUE_RAISED", "IN_PROGRESS", 6)
        self.put("I3", "SEARCH", "CTO_BACKEND", "ISSUE_RESOLVED", "IN_PROGRESS", 9)

        # 5. A Decision required and still pending.
        self.put(
            "D1", "GROWTH", "CMO", "DECISION_REQUIRED", "IN_PROGRESS", 13,
            summary="closed beta scope: 3 features or 5",
        )

        # 6. A Decision required and approved — must NOT appear as pending.
        self.put("D2", "LEGAL", "COO", "DECISION_REQUIRED", "IN_PROGRESS", 7)
        self.put("D3", "LEGAL", "COO", "DECISION_APPROVED", "IN_PROGRESS", 15)

        # 6b. A Decision approved and never carried out, and one carried
        # out. Approval used to close the lifecycle, so "decided and not
        # done" left every list at the moment it became a problem.
        self.put("X1", "ROLLOUT", "COO", "DECISION_APPROVED", "IN_PROGRESS", 11,
                 summary="approved: ship v2 to all users")
        self.put("X2", "HIRING", "COO", "DECISION_APPROVED", "IN_PROGRESS", 9)
        self.put("X3", "HIRING", "COO", "EXECUTED", "IN_PROGRESS", 12,
                 summary="req posted, 3 candidates in pipe")

        # 6c. An Issue somebody actually took. Raised by CMO, assigned to
        # CTO_BACKEND — the cross-team case, where `role` carries a fact
        # `ISSUE_RAISED` alone cannot.
        self.put("A1", "MOBILE_UX", "CMO", "ISSUE_RAISED", "IN_PROGRESS", 8,
                 summary="checkout drop-off on small screens")
        self.put("A2", "MOBILE_UX", "CTO_FRONTEND", "ASSIGNED", "IN_PROGRESS", 10)

        # 7. A project at risk: moving, and likely to stop. Before C149 this
        # had to be reported as either "fine" or "already blocked".
        self.put(
            "R1", "VENDOR", "COO", "AT_RISK", "AT_RISK", 16,
            summary="contract renewal unsigned, expires in 14 days",
        )

    def rollup(self):
        return build_company_rollup(processed_dir=self.processed, now=NOW)

    def model(self):
        return build_dashboard(self.rollup(), now=NOW, activity=GIT)

    def payload(self):
        return self.model().to_payload()

    def panel(self, key):
        for panel in self.payload()["panels"]:
            if panel["key"] == key:
                return panel
        raise AssertionError(f"panel {key} is not in the payload")

    def risk_rows(self, kind):
        return [
            row
            for row in self.panel("RISKS")["rows"]
            if row["values"]["kind"] == kind
        ]

    def metric(self, key):
        for row in self.panel("METRICS")["rows"]:
            if row["values"]["key"] == key:
                return row["values"]["value"]
        raise AssertionError(f"metric {key} is not in the payload")


class ThreeBlockedProjectsAreThreeEverywhereTests(CompanyScenarioTestCase):
    """Scenario 2, and the reason this file exists.

    Counted at four independent places. They are built by four different
    code paths — a fold, a derived list, a metric, a panel — so a loss in any
    one of them shows up as a disagreement rather than as a uniformly wrong
    number.
    """

    def test_the_project_fold_sees_three(self):
        blocked = [p for p in self.rollup().projects if p.is_blocked]

        self.assertEqual(
            sorted(p.project_id for p in blocked), ["BRAND", "MOBILE", "PAY"]
        )

    def test_the_risk_list_has_three(self):
        self.assertEqual(len(self.rollup().risks), 3)

    def test_the_metric_says_three(self):
        self.assertEqual(self.metric("open_blockers"), 3)

    def test_the_risks_panel_renders_three_blocker_rows(self):
        rows = self.risk_rows("OPEN_BLOCKER")

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            sorted(r["values"]["project_id"] for r in rows),
            ["BRAND", "MOBILE", "PAY"],
        )

    def test_each_blocker_keeps_its_own_text_and_its_own_team(self):
        """The loss a count cannot see: three rows, all carrying the newest
        blocker. `open_blocker_team` exists because `teams[-1]` produced
        exactly that."""
        rows = {r["values"]["project_id"]: r["values"] for r in self.risk_rows("OPEN_BLOCKER")}

        self.assertEqual(rows["PAY"]["blocker"], "vendor key missing")
        self.assertEqual(rows["PAY"]["team"], "COO")
        self.assertEqual(rows["BRAND"]["blocker"], "legal review")
        self.assertEqual(rows["BRAND"]["team"], "CMO")
        self.assertEqual(rows["MOBILE"]["blocker"], "app store account suspended")
        self.assertEqual(rows["MOBILE"]["team"], "CTO_FRONTEND")

    def test_each_blocked_project_is_aged_from_its_own_event(self):
        """One shared `since` would make all three the same age, and the
        oldest blocker is the one a COO acts on first."""
        rows = {r["values"]["project_id"]: r["values"] for r in self.risk_rows("OPEN_BLOCKER")}

        self.assertEqual(rows["PAY"]["days_open"], 12)
        self.assertEqual(rows["BRAND"]["days_open"], 9)
        self.assertEqual(rows["MOBILE"]["days_open"], 6)


class OpenLifecyclesShowAndClosedOnesDoNotTests(CompanyScenarioTestCase):
    """Scenarios 3-6. The pair matters more than either half: a system that
    showed every Issue would be as wrong as one that showed none, and only
    building both in one company catches a fold that ignores the closing
    Event."""

    def test_only_the_unresolved_issues_are_open(self):
        """SEARCH's Issue was resolved and is gone; BILLING's and
        MOBILE_UX's are not. Assignment does not close one, which is why
        MOBILE_UX is here despite having an owner."""
        rows = self.risk_rows("OPEN_ISSUE")

        self.assertEqual(
            sorted(r["values"]["project_id"] for r in rows),
            ["BILLING", "MOBILE_UX"],
        )
        self.assertEqual(self.metric("issues_open"), 2)

    def test_only_the_undecided_decision_is_pending(self):
        rows = self.risk_rows("PENDING_DECISION")

        self.assertEqual([r["values"]["project_id"] for r in rows], ["GROWTH"])
        self.assertEqual(self.metric("decisions_pending"), 1)

    def test_the_resolved_issue_is_still_counted_as_resolved(self):
        """Closing it removes it from *open*, not from the record. A company
        that resolved an Issue did work, and a dashboard that forgets it
        under-reports the week."""
        self.assertEqual(self.metric("issues_raised"), 3)
        self.assertEqual(self.metric("issues_resolved"), 1)

    def test_the_approved_decisions_are_still_counted_as_approved(self):
        """Three approvals: LEGAL, ROLLOUT, HIRING. Two are still waiting to
        be carried out and one was — and all three were approvals, which is
        what this metric counts."""
        self.assertEqual(self.metric("decisions_approved"), 3)
        self.assertEqual(self.metric("decisions_executed"), 1)
        self.assertEqual(self.metric("decisions_unexecuted"), 2)

    def test_an_assigned_issue_names_its_owner_and_an_unowned_one_says_so(self):
        """The distinction an aging list is read to make.

        "10일째 열려 있는 Issue: BILLING [CMO]" said the same thing whether
        CMO had been working on it for ten days or nobody had looked once,
        and those are opposite situations with opposite next actions.

        The cross-team case is the one that proves `role` carries a real
        fact: MOBILE_UX was raised by CMO and taken by CTO_FRONTEND, so the
        owner is not the raiser and could not have been inferred.
        """
        rows = {
            r["values"]["project_id"]: r["values"]
            for r in self.risk_rows("OPEN_ISSUE")
        }

        self.assertIn("담당 CTO Frontend", rows["MOBILE_UX"]["detail"])
        self.assertIn("미배정", rows["BILLING"]["detail"])
        # `team` still means the raiser on both rows — one column, one
        # meaning. Ownership lives in `detail`.
        self.assertEqual(rows["MOBILE_UX"]["team"], "CMO")

    def test_assignment_does_not_close_the_issue_or_restart_its_age(self):
        """Assignment is neither an opening nor a closing. MOBILE_UX was
        raised on the 8th and taken on the 10th; the age must still run from
        the 8th, or "how long has this been open" quietly becomes "how long
        since somebody picked it up"."""
        row = next(
            r["values"]
            for r in self.risk_rows("OPEN_ISSUE")
            if r["values"]["project_id"] == "MOBILE_UX"
        )

        self.assertEqual(row["days_open"], 12)  # 8/8 -> 8/20

    def test_an_executed_decision_leaves_the_unexecuted_list(self):
        """HIRING was approved and then executed. If `EXECUTED` did not
        close the lifecycle, every decision the company ever carried out
        would pile up as outstanding work."""
        unexecuted = {
            r["values"]["project_id"] for r in self.risk_rows("UNEXECUTED_DECISION")
        }

        self.assertIn("ROLLOUT", unexecuted)
        self.assertIn("LEGAL", unexecuted)
        self.assertNotIn("HIRING", unexecuted)

    def test_the_open_rows_carry_the_words_a_person_wrote(self):
        """`detail`, not `blocker`. A pending Decision's own summary filed
        under a column headed "Blocker" would claim the project is stopped."""
        issue = next(
            r["values"]
            for r in self.risk_rows("OPEN_ISSUE")
            if r["values"]["project_id"] == "BILLING"
        )
        decision = self.risk_rows("PENDING_DECISION")[0]["values"]

        self.assertIn(
            "invoice totals drift under concurrent writes", issue["detail"]
        )
        self.assertIsNone(issue["blocker"])
        self.assertIn("closed beta scope: 3 features or 5", decision["detail"])
        self.assertIsNone(decision["blocker"])
        # Nobody has taken either one, and the row says so rather than
        # leaving the owner blank — a blank owner and an owner nobody
        # rendered look identical (C149).
        self.assertIn("미배정", issue["detail"])
        self.assertIn("미배정", decision["detail"])


class TheAtRiskProjectIsVisibleAndIsNotBlockedTests(CompanyScenarioTestCase):
    """Scenario 7. The state whose whole point is that it is *not* the same
    as BLOCKED — a COO can still act on it."""

    def test_it_appears_as_its_own_risk_kind(self):
        rows = self.risk_rows("AT_RISK")

        self.assertEqual([r["values"]["project_id"] for r in rows], ["VENDOR"])
        self.assertEqual(rows[0]["values"]["days_open"], 4)
        self.assertEqual(rows[0]["values"]["team"], "COO")

    def test_the_row_says_what_the_risk_is(self):
        """Measured on a real end-to-end run before `at_risk_summary`
        existed, the row read `AT_RISK  VENDOR  1일` and nothing else — the
        two open-state kinds beside it carried a person's own words and this
        one carried none. A risk row a reader cannot act on is the row that
        teaches them to skip the table."""
        row = self.risk_rows("AT_RISK")[0]["values"]

        self.assertEqual(
            row["detail"], "contract renewal unsigned, expires in 14 days"
        )
        # In `detail` and not in `blocker`: the project is not blocked, and
        # the column headed "Blocker" would say it is.
        self.assertIsNone(row["blocker"])

    def test_it_is_not_counted_among_the_blockers(self):
        """The three blocked projects stay three. An at-risk project folded
        into `open_blockers` would inflate the number a COO triages on."""
        self.assertEqual(self.metric("open_blockers"), 3)
        self.assertEqual(self.metric("projects_at_risk"), 1)

    def test_the_projects_panel_does_not_call_it_blocked(self):
        row = next(
            r
            for r in self.panel("PROJECTS")["rows"]
            if r["values"]["project_id"] == "VENDOR"
        )

        self.assertEqual(row["values"]["status"], "AT_RISK")
        self.assertIsNone(row["values"]["blocker"])

    def test_the_state_column_says_at_risk_and_not_active(self):
        """The defect C149 reproduced in its own change, found by turning
        the CANCELLED branch's own paragraph on the state it had just added.

        `status` is the last reported value and the screen prints it
        directly, so a `state` that disagreed was invisible on the terminal
        and wrong everywhere else: `dashboard_server._project_states()`
        counts these words for the summary tiles and `CT_PROJECTS.State` is
        a Notion select built from them. Both said the company had **no**
        projects at risk while the Risk table listed one.

        Asserted against `PROJECT_STATES` membership as well as the literal,
        so a state word renamed upstream fails here rather than silently
        becoming a value no renderer has a colour for.
        """
        from controltower.dashboard import PROJECT_STATES

        row = next(
            r
            for r in self.panel("PROJECTS")["rows"]
            if r["values"]["project_id"] == "VENDOR"
        )

        self.assertEqual(row["values"]["state"], "AT_RISK")
        self.assertIn(row["values"]["state"], PROJECT_STATES)
        # And the blocked one still reads BLOCKED — a fix that made
        # everything AT_RISK would satisfy the assertion above.
        blocked = next(
            r
            for r in self.panel("PROJECTS")["rows"]
            if r["values"]["project_id"] == "PAY"
        )
        self.assertEqual(blocked["values"]["state"], "BLOCKED")


class AWindowedViewStillKnowsWhatIsOpenTests(unittest.TestCase):
    """`until` bounds a state; `since` does not.

    The D+1 report is a one-day window, and it is the page docs/15 makes the
    daily read. Measured before this, on a company with one blocker, one open
    Issue, one pending Decision, one approved-but-unexecuted Decision and one
    at-risk project — asking for a single day on which none of them was
    opened:

        whole period   blockers=1 issues_open=1 pending=1 unexec=1 at_risk=1
        D+1 (one day)  blockers=0 issues_open=0 pending=0 unexec=0 at_risk=0
                       RISKS table empty

    Every one of those labels is a state word. The page told a COO the
    company had nothing open.

    The cause was a category error rather than a policy: an open state has
    no "how far back". Nothing is *blocked only since yesterday* — it is
    blocked or not, as of a date. So `until` applies to the state fold and
    `since` does not, while activity keeps both.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.processed = Path(tmp.name)
        specs = [
            ("B1", "PAY", "BLOCKED", "BLOCKED", 8, {"blocker": "vendor key"}),
            ("I1", "BILLING", "ISSUE_RAISED", "IN_PROGRESS", 10, {}),
            ("D1", "GROWTH", "DECISION_REQUIRED", "IN_PROGRESS", 11, {}),
            ("X1", "ROLLOUT", "DECISION_APPROVED", "IN_PROGRESS", 12, {}),
            ("R1", "VENDOR", "AT_RISK", "AT_RISK", 12, {}),
            # The only Event inside the window, and it opens nothing.
            ("N1", "SEARCH", "MILESTONE_COMPLETED", "IN_PROGRESS", 19, {"milestone": "M"}),
        ]
        for eid, pid, event_type, status, day, extra in specs:
            event = create_event(
                source="DESKTOP_4", role="COO", project_id=pid,
                event_type=event_type, status=status, summary=eid,
                history_candidate=True, event_id=eid,
                timestamp=f"2026-08-{day:02d}T09:00:00+09:00", **extra
            )
            (self.processed / f"{eid}.json").write_text(
                event.to_json(), encoding="utf-8"
            )

    def _rollup(self, since=None, until=None):
        return build_company_rollup(
            processed_dir=self.processed, now=NOW, since=since, until=until
        )

    DAY = date(2026, 8, 19)

    def test_a_one_day_window_still_reports_every_open_item(self):
        windowed = self._rollup(since=self.DAY, until=self.DAY)

        for key in (
            "open_blockers",
            "issues_open",
            "decisions_pending",
            "decisions_unexecuted",
            "projects_at_risk",
        ):
            with self.subTest(metric=key):
                self.assertEqual(windowed.metric(key).value, 1)

    def test_the_windowed_numbers_match_the_unbounded_ones(self):
        """Stated as agreement rather than as five literals: a state is a
        state, so narrowing the window must not change it at all."""
        whole = self._rollup()
        windowed = self._rollup(since=self.DAY, until=self.DAY)

        for key in (
            "open_blockers",
            "issues_open",
            "decisions_pending",
            "decisions_unexecuted",
            "projects_at_risk",
        ):
            with self.subTest(metric=key):
                self.assertEqual(
                    windowed.metric(key).value, whole.metric(key).value
                )

    def test_activity_is_still_bounded_by_the_window(self):
        """The other half, and the reason this is not simply "ignore
        `since`". Activity really is a period question — a fix that widened
        everything would make "what happened yesterday" mean "everything"."""
        windowed = self._rollup(since=self.DAY, until=self.DAY)

        self.assertEqual(windowed.events_read, 1)
        self.assertEqual([p.project_id for p in windowed.projects], ["SEARCH"])
        self.assertEqual(windowed.metric("projects_active").value, 1)

    def test_the_risk_table_is_not_empty_in_the_window(self):
        """The surface a COO actually reads."""
        model = build_dashboard(
            self._rollup(since=self.DAY, until=self.DAY), now=NOW
        )
        kinds = sorted({row.values["kind"] for row in model.panel("RISKS").rows})

        self.assertEqual(
            kinds,
            ["AT_RISK", "OPEN_BLOCKER", "OPEN_ISSUE", "PENDING_DECISION",
             "UNEXECUTED_DECISION"],
        )

    def test_until_still_bounds_the_state(self):
        """`until` is the bound a state does have. Asking as of the 9th —
        before the Issue was raised on the 10th — must not see it."""
        early = self._rollup(until=date(2026, 8, 9))

        self.assertEqual(early.metric("open_blockers").value, 1)   # blocked on the 8th
        self.assertEqual(early.metric("issues_open").value, 0)     # raised on the 10th


class TheThreeSurfacesAgreeAboutOneProjectTests(unittest.TestCase):
    """One company state must not read three ways.

    The defect, measured: a project reported COMPLETED and later reported
    AT_RISK came out

        PROJECTS panel    state=AT_RISK
        RISKS panel       (empty)
        projects_at_risk  0

    Nothing raised, every layer was individually defensible, and a COO
    reading the Risk table would not see a project the Project table calls
    at risk. The cause was one conflation made three times: `is_complete`
    means "a Completed Date was written, ever" — right for counting
    completions — and two guards were reading it as "is currently
    complete". C150 fixed the third and left these two.

    Asserted as agreement between the surfaces rather than as three
    separate expected values, because the property that matters is that
    they cannot disagree — a future change that moves all three together is
    fine, and one that moves two of them is the bug.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.processed = Path(tmp.name)

    def _state(self, specs):
        for i, (event_type, status, day, extra) in enumerate(specs):
            event = create_event(
                source="DESKTOP_1", role="CTO_BACKEND", project_id="P",
                event_type=event_type, status=status, summary="s",
                history_candidate=True, event_id=f"E{i}",
                timestamp=f"2026-08-{day:02d}T09:00:00+09:00", **extra
            )
            (self.processed / f"E{i}.json").write_text(
                event.to_json(), encoding="utf-8"
            )
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)
        model = build_dashboard(rollup, now=NOW)
        return (
            model.panel("PROJECTS").rows[0].values["state"],
            [row.values["kind"] for row in model.panel("RISKS").rows],
            rollup.metric("projects_at_risk").value,
        )

    def test_a_completed_project_reported_at_risk_reaches_every_surface(self):
        state, kinds, metric = self._state(
            [("COMPLETED", "COMPLETED", 5, {}), ("AT_RISK", "AT_RISK", 12, {})]
        )

        self.assertEqual(state, "AT_RISK")
        self.assertEqual(kinds, ["AT_RISK"])
        self.assertEqual(metric, 1)

    def test_the_completion_is_still_counted(self):
        """The other half. Fixing the state must not cost the count: the
        project really did complete once, and `projects_completed` cites the
        file that says so."""
        for i, (event_type, status, day) in enumerate(
            [("COMPLETED", "COMPLETED", 5), ("AT_RISK", "AT_RISK", 12)]
        ):
            event = create_event(
                source="DESKTOP_1", role="CTO_BACKEND", project_id="P",
                event_type=event_type, status=status, summary="s",
                history_candidate=True, event_id=f"C{i}",
                timestamp=f"2026-08-{day:02d}T09:00:00+09:00",
            )
            (self.processed / f"C{i}.json").write_text(
                event.to_json(), encoding="utf-8"
            )
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)

        metric = rollup.metric("projects_completed")
        self.assertEqual(metric.value, 1)
        self.assertEqual([ref.event_id for ref in metric.evidence], ["C0"])

    def test_a_project_completed_after_being_at_risk_is_not_at_risk(self):
        """The direction that must not flip. A fix that made everything
        AT_RISK would satisfy the first test and destroy the field."""
        state, kinds, metric = self._state(
            [("AT_RISK", "AT_RISK", 5, {}), ("COMPLETED", "COMPLETED", 12, {})]
        )

        self.assertEqual(state, "COMPLETE")
        self.assertEqual(kinds, [])
        self.assertEqual(metric, 0)

    def test_blocked_still_outranks_at_risk(self):
        """Precedence is unchanged — it lives in `PROJECT_STATES`' order and
        `completion_stands` deliberately does not repeat it."""
        state, kinds, metric = self._state(
            [("AT_RISK", "AT_RISK", 5, {}),
             ("BLOCKED", "BLOCKED", 12, {"blocker": "vendor"})]
        )

        self.assertEqual(state, "BLOCKED")
        self.assertEqual(kinds, ["OPEN_BLOCKER"])
        self.assertEqual(metric, 0)

    def test_a_restarted_project_is_not_shown_as_complete(self):
        """C150's case, kept here beside the others so the whole
        state/status contradiction family is one file to read."""
        state, _kinds, _metric = self._state(
            [("COMPLETED", "COMPLETED", 5, {}), ("STARTED", "IN_PROGRESS", 12, {})]
        )

        self.assertEqual(state, "ACTIVE")


class TheKpiPanelShowsBothKindsOfAnswerTests(CompanyScenarioTestCase):
    """Scenarios 8 and 9, side by side — which is the only arrangement that
    catches the failure worth catching: a refusal rendered as `0`."""

    def kpi_row(self, key):
        for row in self.panel("ROLE_KPI")["rows"]:
            if row["values"]["key"] == key:
                return row["values"]
        raise AssertionError(f"kpi {key} is not in the payload")

    def test_a_measured_kpi_shows_its_number(self):
        row = self.kpi_row("blocked_items")

        self.assertTrue(row["measured"])
        self.assertEqual(row["reading"], "3건")

    def test_an_unmeasurable_kpi_shows_the_words_and_never_a_zero(self):
        row = self.kpi_row("runway")

        self.assertFalse(row["measured"])
        self.assertEqual(row["reading"], "DATA REQUIRED")
        self.assertNotEqual(row["reading"], "0")

    def test_the_refusal_says_what_would_answer_it(self):
        self.assertIn("Cash", self.kpi_row("runway")["requires"])

    def test_aging_is_measured_over_this_company(self):
        # 12일, not 10: MOBILE_UX was raised on the 8th and is still open,
        # so it is now the oldest. Aging reports the oldest open item, not
        # the most recently raised one.
        self.assertEqual(self.kpi_row("issue_aging")["reading"], "12일")
        self.assertEqual(self.kpi_row("decision_aging")["reading"], "7일")

    def test_the_two_kinds_both_occur_in_one_company(self):
        """Guards the two tests above from being vacuous on a fixture that
        happened to produce only one kind."""
        kpis = build_kpi_set(self.rollup(), now=NOW, activity=GIT)

        self.assertTrue(kpis.measured)
        self.assertTrue(kpis.data_required)
        self.assertTrue(any(k.status == MEASURED for k in kpis.for_role("COO")))
        self.assertTrue(all(k.status == DATA_REQUIRED for k in kpis.for_role("CEO")))


class TheDPlusOneChangesAreOnThePageTests(CompanyScenarioTestCase):
    """Scenario 10. Git's account of the same days, beside the Events'."""

    def test_every_commit_reaches_the_panel(self):
        rows = self.panel("CODE_CHANGES")["rows"]

        self.assertEqual([r["values"]["commit"] for r in rows], ["a" * 8, "b" * 8])

    def test_the_note_carries_the_totals_a_person_reads(self):
        note = self.panel("CODE_CHANGES")["note"]

        self.assertIn("commit 2건", note)
        self.assertIn("바뀐 파일 3개", note)
        self.assertIn("작성자 2명", note)

    def test_the_panel_says_which_window_it_covers_and_does_not_claim_d_plus_one(self):
        """Measured on the live tree, when the title still said D+1:

            D+1 개발 변경 (Git)
            2026-08-05 ~ 2026-08-10 · commit 6건 · 바뀐 파일 98개

        Twenty-four days wide and twenty-four days old, under a title that
        promises yesterday. This panel covers whatever window the caller
        asked the *panels* for; D+1 is a use of it, not its definition, so
        the window has to be on the panel and the claim has to be off the
        title.
        """
        panel = self.panel("CODE_CHANGES")

        self.assertNotIn("D+1", panel["title"])
        self.assertIn("2026-08-19", panel["note"])

    def test_the_git_kpis_count_the_same_commits(self):
        kpis = build_kpi_set(self.rollup(), now=NOW, activity=GIT)

        self.assertEqual(kpis.get("code_commits").value, 2)
        self.assertEqual(kpis.get("code_files_changed").value, 3)
        self.assertEqual(kpis.get("code_contributors").value, 2)


class ADayWithCommitsAndNoEventsSaysSoTests(unittest.TestCase):
    """The one line the whole git half exists to make possible.

    Measured on the live tree with a one-day window (2026-09-02):

        events_read   0
        CODE_CHANGES  1 commit, 21 files

    Every Event-derived panel renders that day as quiet, and before this the
    strongest thing either surface said was "셀 Event가 없다" — true, and
    silent about the fact that git had the answer sitting next to it. That
    day is not a quiet company; it is delivery that did not arrive, and it
    is the failure with no other signal anywhere on the page.

    Driven through a synthetic payload rather than a fixture tree, because
    the branch is unreachable on any tree that has ATTENTION items — P1 wins
    the headline, as it should — and the real one has eight.
    """

    def _payload(self, code_rows):
        return {
            "generated_at": "2026-09-03T10:00:00+09:00",
            "attention": [],
            "window": {"since": "2026-09-02", "until": "2026-09-02"},
            "blocks": [],
            "ops": {},
            "model": {
                "events_read": 0,
                "coverage": {},
                "panels": [
                    {
                        "key": "CODE_CHANGES",
                        "title": "개발 변경 (Git)",
                        "status": "SOURCED",
                        "columns": ["commit"],
                        "note": "n",
                        "unsourced_layers": [],
                        "rows": [
                            {
                                "key": f"c{i}",
                                "values": {"commit": f"c{i}"},
                                "evidence": [],
                                "evidence_count": 0,
                                "evidence_truncated": False,
                            }
                            for i in range(code_rows)
                        ],
                    }
                ],
            },
        }

    def _entrypoint(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "dashboard_server_probe", REPO_ROOT / "dashboard_server.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["dashboard_server_probe"] = module
        spec.loader.exec_module(module)
        self.addCleanup(sys.modules.pop, "dashboard_server_probe", None)
        return module

    def test_the_browser_verdict_names_the_disagreement(self):
        module = self._entrypoint()

        _tone, _word, detail = module.company_verdict(self._payload(3))

        self.assertIn("commit이 3건", detail)
        self.assertIn("보고가 도착하지 않았", detail)

    def test_the_notion_headline_names_it_too(self):
        """Both surfaces or neither: a company whose Notion page and browser
        page disagree about the same day has two Control Towers."""
        from controltower.notion_page import build_control_tower_blocks

        blocks, _warnings = build_control_tower_blocks(self._payload(3))
        text = "".join(
            item["text"]["content"]
            for item in blocks[0]["callout"]["rich_text"]
        )

        self.assertIn("commit이 3건", text)

    def test_a_quiet_day_with_no_commits_says_only_what_it_knows(self):
        """The other side. With git also empty there is no disagreement to
        report, and inventing one would be worse than the silence it
        replaced."""
        from controltower.notion_page import build_control_tower_blocks

        module = self._entrypoint()
        _tone, _word, detail = module.company_verdict(self._payload(0))
        blocks, _warnings = build_control_tower_blocks(self._payload(0))
        text = "".join(
            item["text"]["content"]
            for item in blocks[0]["callout"]["rich_text"]
        )

        self.assertIn("셀 Event가 없다", detail)
        self.assertNotIn("commit", detail)
        self.assertNotIn("commit이", text)


class NothingIsLostBetweenTheLayersTests(CompanyScenarioTestCase):
    """The sweep. Every Event written is accounted for, and every row that
    should exist does — asserted as totals so a loss anywhere shows up even
    where no named test covers it."""

    def test_every_event_written_was_read(self):
        rollup = self.rollup()

        # 17: H1 H2 · B1 B2 B3 · I1 I2 I3 · D1 D2 D3 · X1 X2 X3 · A1 A2 · R1.
        # Written out because a bare number here is the one thing this file
        # must not be — the count is the assertion, so it has to be checkable
        # by reading.
        self.assertEqual(rollup.events_read, 17)
        self.assertEqual(rollup.unreadable, ())
        self.assertEqual(rollup.duplicates, ())

    def test_every_project_written_appears_exactly_once(self):
        ids = [r["values"]["project_id"] for r in self.panel("PROJECTS")["rows"]]

        self.assertEqual(
            sorted(ids),
            [
                "BILLING", "BRAND", "GROWTH", "HIRING", "LEGAL", "MOBILE",
                "MOBILE_UX", "PAY", "ROLLOUT", "SEARCH", "VENDOR",
            ],
        )
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_risks_panel_holds_every_situation_and_no_extras(self):
        """A per-kind census rather than a total, because a total hides a
        swap: one kind gaining a row while another loses one leaves the sum
        unchanged.
        """
        rows = self.panel("RISKS")["rows"]
        census = {}
        for row in rows:
            census[row["values"]["kind"]] = census.get(row["values"]["kind"], 0) + 1

        self.assertEqual(
            census,
            {
                "OPEN_BLOCKER": 3,
                "AT_RISK": 1,
                "OPEN_ISSUE": 2,
                "PENDING_DECISION": 1,
                # MOBILE_UX's Issue is assigned but still open, so it is an
                # OPEN_ISSUE too — assignment does not close anything.
                # LEGAL's and ROLLOUT's decisions were approved and never
                # executed; HIRING's was executed and is absent.
                # C149's second Decision half: approval no longer closes the
                # lifecycle, so this company has one decision made and not
                # done — which is the state the fixture always described and
                # the dashboard could not show.
                "UNEXECUTED_DECISION": 2,
            },
        )

    def test_every_risk_row_can_be_traced_to_a_file_on_disk(self):
        """A Control Tower number nobody can follow is a rumour
        (`rollup.Metric`). Followed here for real: each cited path is opened."""
        for row in self.panel("RISKS")["rows"]:
            with self.subTest(row=row["key"]):
                self.assertTrue(row["evidence"], row["key"])
                for ref in row["evidence"]:
                    self.assertTrue((self.processed / ref["path"]).is_file())

    def test_every_risk_kind_gets_a_sentence_written_for_that_kind(self):
        """The measured defect. `_print_control_tower()`'s RISKS dispatch was
        `OPEN_BLOCKER` / `EVENT_ID_CONFLICT` / **else**, and the `else` was
        the role-mismatch sentence. Every kind added after it was therefore
        announced as a Desktop/role mismatch — measured on the two kinds
        C149 added, before the fix:

            Desktop과 role이 어긋난 Event: R1 — None에서 왔는데 role은
            None이라고 말한다(docs/02 §8은 그 Desktop을 None로 정한다)

        A paragraph of confident, wrong diagnosis with three `None`s in it,
        on the line an operator reads first. Nothing raised; the row had
        every column the branch asked for, all of them null.

        Asserted over the kinds the payload actually produces, so a fourth
        kind added later has to be given a sentence or fail here.
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ops_status_probe", REPO_ROOT / "ops_status.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["ops_status_probe"] = module
        spec.loader.exec_module(module)
        self.addCleanup(sys.modules.pop, "ops_status_probe", None)

        runtime = self.processed.parent / "runtime_probe"
        (runtime / "events" / "processed").mkdir(parents=True, exist_ok=True)
        for path in self.processed.glob("*.json"):
            (runtime / "events" / "processed" / path.name).write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        module.RUNTIME_DIR = runtime

        import contextlib
        import io as _io

        with contextlib.redirect_stdout(_io.StringIO()):
            attention = module._print_control_tower(NOW)

        kinds = {r["values"]["kind"] for r in self.panel("RISKS")["rows"]}
        # The role-mismatch sentence must appear exactly as often as there
        # are role mismatches — which in this company is never.
        self.assertNotIn("ROLE_MISMATCH", kinds)
        self.assertEqual(
            [line for line in attention if "role이 어긋난" in line], []
        )
        # And each kind that *is* here has a line naming what it is.
        for kind, phrase in (
            ("OPEN_BLOCKER", "막혀 있는 Project"),
            ("AT_RISK", "위험하다고 보고된 Project"),
            ("OPEN_ISSUE", "열려 있는 Issue"),
            ("PENDING_DECISION", "기다리는 Decision"),
        ):
            with self.subTest(kind=kind):
                self.assertIn(kind, kinds, "the fixture stopped producing this kind")
                self.assertTrue(
                    any(phrase in line for line in attention),
                    f"{kind} produced no line of its own",
                )

    def test_every_risk_line_carries_a_severity_and_a_next_action(self):
        """A line with neither is the `?` badge, and these are the lines a
        COO reads first. Checked through the production classifier, not a
        copy of it."""
        from controltower.attention import UNCLASSIFIED, next_action, severity

        import contextlib
        import importlib.util
        import io as _io

        spec = importlib.util.spec_from_file_location(
            "ops_status_probe2", REPO_ROOT / "ops_status.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["ops_status_probe2"] = module
        spec.loader.exec_module(module)
        self.addCleanup(sys.modules.pop, "ops_status_probe2", None)

        runtime = self.processed.parent / "runtime_probe2"
        (runtime / "events" / "processed").mkdir(parents=True, exist_ok=True)
        for path in self.processed.glob("*.json"):
            (runtime / "events" / "processed" / path.name).write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        module.RUNTIME_DIR = runtime

        with contextlib.redirect_stdout(_io.StringIO()):
            attention = module._print_control_tower(NOW)

        self.assertTrue(attention, "this company produces no ATTENTION at all")
        for line in attention:
            with self.subTest(line=line[:60]):
                level, why = severity(line)
                self.assertNotEqual(level, UNCLASSIFIED, line)
                self.assertTrue(why)
                self.assertTrue(next_action(line), line)

    def test_the_healthy_project_is_not_reported_as_a_risk(self):
        """The other direction. A dashboard that lists everything is as
        useless as one that lists nothing."""
        risky = {r["values"]["project_id"] for r in self.panel("RISKS")["rows"]}

        self.assertNotIn("SEARCH", risky)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
