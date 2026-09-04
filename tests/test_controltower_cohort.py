"""`controltower/cohort.py` — grouping Projects by when they started, and
following each group forward.

Three properties matter more than any individual percentage here.

**A window that has not elapsed is never a low score.** This is the failure the
module exists to prevent, and it is the one a reader cannot detect by looking:
`retained / size` over a cohort opened four days ago returns a small,
confident-looking number for a question nobody could have answered yet, and it
would be put next to last month's mature figure and read as a trend. Half the
tests below are about that single conversion.

**The cohort is not a second count of the Events.** Every number here is derived
from the fold `build_company_rollup()` already made — `first_seen` and the
`EvidenceRef.at` of each Project's own Events. If this module ever started
reading `processed/` itself it would be the C28 defect in the place it does the
most damage: two derivations of "when did this project start", shown on one
screen.

**Same evidence, same answer, always.** A cohort table is read as a trend across
months, so a number that moves when nothing moved is worse here than an obviously
wrong one.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from controltower.cohort import (  # noqa: E402
    COHORT_UNIT,
    COHORT_WINDOWS,
    DATA_REQUIRED,
    MEASURED,
    NOT_APPLICABLE_READING,
    build_cohort_analysis,
)
from controltower.dashboard import build_dashboard  # noqa: E402
from controltower.kpi import DATA_REQUIRED_READING  # noqa: E402
from controltower.rollup import build_company_rollup  # noqa: E402
from events import create_event  # noqa: E402

#: Late enough that every window over the August fixtures has elapsed, so a
#: `DATA REQUIRED` in a test below is the fixture's doing and never the
#: calendar's.
NOW = datetime.fromisoformat("2026-10-01T10:00:00+09:00")

SOURCE_FOR_ROLE = {
    "CTO_BACKEND": "DESKTOP_1",
    "CMO": "DESKTOP_2",
    "CTO_FRONTEND": "DESKTOP_3",
    "COO": "DESKTOP_4",
}


class CohortTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.processed = Path(tmp.name)
        self._seq = 0

    def put(self, project, when, *, event_id=None, role="CTO_BACKEND", **extra):
        """One Event for `project` at `when` (an ISO-8601 string).

        The timestamp is the whole subject of this module, so it is written out
        by the caller rather than assembled from a day number — a fixture that
        could only express one offset could not exercise the timezone rule.
        """
        self._seq += 1
        event_id = event_id or f"E{self._seq:03d}"
        event = create_event(
            source=SOURCE_FOR_ROLE[role],
            role=role,
            project_id=project,
            event_type=extra.pop("event_type", "MILESTONE_COMPLETED"),
            status=extra.pop("status", "IN_PROGRESS"),
            summary=f"summary for {event_id}",
            history_candidate=True,
            event_id=event_id,
            timestamp=when,
            **extra,
        )
        (self.processed / f"{event_id}.json").write_text(
            event.to_json(), encoding="utf-8"
        )
        return event

    def analysis(self, *, now=NOW, since=None, until=None):
        rollup = build_company_rollup(
            processed_dir=self.processed, now=now, since=since, until=until
        )
        return build_cohort_analysis(rollup, now=now)

    @staticmethod
    def one(cohort, days):
        """The `CohortWindow` for D+`days`.

        A test helper rather than a method on `Cohort`: production reads all
        three windows together (`_cohort_panel()` builds one column trio per
        window), so a single-window lookup in `src/` would be a capability with
        no caller — which is what `DeadCapabilityInventoryTests` refuses.
        """
        return next(w for w in cohort.windows if w.days == days)

    def window(self, cohort_key, days, *, now=NOW, since=None, until=None):
        analysis = self.analysis(now=now, since=since, until=until)
        cohort = next(c for c in analysis.cohorts if c.key == cohort_key)
        return self.one(cohort, days)


class TheCohortIsTheMonthOfTheFirstEventTests(CohortTestCase):
    """Membership. A Project is in exactly one cohort, decided by its first
    Event and by nothing else that happens to it afterwards."""

    def test_a_project_lands_in_the_month_it_first_appeared(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")

        analysis = self.analysis()

        self.assertEqual([c.key for c in analysis.cohorts], ["2026-08"])
        self.assertEqual(analysis.cohorts[0].members, ("PAY",))

    def test_later_events_do_not_move_a_project_to_a_later_cohort(self):
        """The mistake the windowed fold would make. A Project that started in
        July and worked all through August belongs to July — reporting it as an
        August start would make August look productive for work it did not
        begin, and would make July's cohort look smaller than it was."""
        self.put("PAY", "2026-07-30T09:00:00+09:00")
        self.put("PAY", "2026-08-20T09:00:00+09:00")

        analysis = self.analysis()

        self.assertEqual([c.key for c in analysis.cohorts], ["2026-07"])

    def test_a_since_bounded_rollup_still_puts_it_in_its_real_cohort(self):
        """`build_cohort_analysis()` reads `state_projects`, which ignores
        `since` — so asking the dashboard for "the last week" does not silently
        re-date every Project that started before it.

        Without this the same company would report a different cohort table for
        every window an operator picked, and each would look plausible."""
        self.put("PAY", "2026-07-30T09:00:00+09:00")
        self.put("PAY", "2026-08-20T09:00:00+09:00")

        analysis = self.analysis(since=date(2026, 8, 15))

        self.assertEqual([c.key for c in analysis.cohorts], ["2026-07"])

    def test_two_months_produce_two_cohorts_oldest_first(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("ADS", "2026-07-03T09:00:00+09:00")
        self.put("SEARCH", "2026-09-03T09:00:00+09:00")

        analysis = self.analysis()

        self.assertEqual(
            [c.key for c in analysis.cohorts], ["2026-07", "2026-08", "2026-09"]
        )

    def test_the_size_is_the_number_of_projects_not_of_events(self):
        """The unit is a Project. Ten Events from one Project is a cohort of
        one, and counting Events here would make a noisy Desktop look like a
        productive month."""
        for hour in range(9, 14):
            self.put("PAY", f"2026-08-03T{hour:02d}:00:00+09:00")
        self.put("ADS", "2026-08-04T09:00:00+09:00")

        cohort = self.analysis().cohorts[0]

        self.assertEqual(cohort.size, 2)
        self.assertEqual(cohort.members, ("ADS", "PAY"))

    def test_the_unit_is_declared_and_is_not_a_customer(self):
        """`kpi.py` refuses twelve CEO KPIs on the grounds that this system has
        no customer. This module must not be readable as having supplied one."""
        self.assertEqual(COHORT_UNIT, "PROJECT")
        self.assertEqual(self.analysis().unit, "PROJECT")


class RetentionIsALaterDayNotAnotherEventTests(CohortTestCase):
    """What D+N counts, at its two edges."""

    def test_a_second_event_the_next_day_is_retention_at_d1(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-04T09:00:00+09:00")

        window = self.window("2026-08", 1)

        self.assertEqual((window.retained, window.base), (1, 1))
        self.assertEqual(window.rendered(), "100.0%")

    def test_a_second_event_the_same_afternoon_is_not_retention(self):
        """The same day's work, not a return. Counting it would make every
        Project that logged twice on its first day look retained at D+1, which
        is the one thing D+1 is asked in order to rule out."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-03T18:00:00+09:00")

        window = self.window("2026-08", 1)

        self.assertEqual((window.retained, window.base), (0, 1))
        self.assertEqual(window.rendered(), "0.0%")

    def test_the_upper_bound_is_inclusive(self):
        """D+7 is "moved again within the week", so day 7 counts."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-10T09:00:00+09:00")

        self.assertEqual(self.window("2026-08", 7).retained, 1)

    def test_a_day_past_the_bound_does_not_count(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-11T09:00:00+09:00")

        self.assertEqual(self.window("2026-08", 7).retained, 0)
        self.assertEqual(self.window("2026-08", 30).retained, 1)

    def test_a_project_retained_at_d1_is_retained_at_d7_and_d30(self):
        """The windows are cumulative, not exclusive buckets. A reader compares
        the three bars of one cohort and would read a *fall* between D+1 and
        D+7 as work stopping; the numbers must only be able to fall for that
        reason."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-04T09:00:00+09:00")

        self.assertEqual(
            [self.window("2026-08", days).rendered() for days in COHORT_WINDOWS],
            ["100.0%", "100.0%", "100.0%"],
        )

    def test_the_rate_is_over_the_matured_members(self):
        """Two of four came back within a day, and all four have matured."""
        for project in ("A", "B", "C", "D"):
            self.put(project, "2026-08-03T09:00:00+09:00")
        self.put("A", "2026-08-04T09:00:00+09:00")
        self.put("B", "2026-08-04T09:00:00+09:00")

        window = self.window("2026-08", 1)

        self.assertEqual((window.retained, window.base), (2, 4))
        self.assertEqual(window.rendered(), "50.0%")


class AnUnelapsedWindowIsNotAZeroTests(CohortTestCase):
    """The module's whole correctness, from four directions.

    Measured on the fixture below without the maturity rule: a cohort opened
    the day before the analysis reports `D+30 0.0%` — a percentage over a
    question that has been open for one of its thirty days — and it renders
    beside a mature cohort's real figure with nothing to tell them apart.
    """

    def _one_day_old(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        return datetime.fromisoformat("2026-08-04T10:00:00+09:00")

    def test_an_unelapsed_window_is_data_required(self):
        now = self._one_day_old()

        window = self.window("2026-08", 30, now=now)

        self.assertEqual(window.status, DATA_REQUIRED)
        self.assertEqual(window.base, 0)
        self.assertIsNone(window.rate)

    def test_it_renders_the_words_rather_than_a_percentage(self):
        now = self._one_day_old()

        self.assertEqual(
            self.window("2026-08", 30, now=now).rendered(), DATA_REQUIRED_READING
        )

    def test_the_elapsed_window_of_the_same_cohort_is_still_measured(self):
        """Not all-or-nothing: D+1 has elapsed for this member and D+30 has
        not, so one is answered and the other refused — in the same row."""
        now = self._one_day_old()
        analysis = build_cohort_analysis(
            build_company_rollup(processed_dir=self.processed, now=now), now=now
        )
        cohort = analysis.cohorts[0]

        self.assertEqual(self.one(cohort, 1).status, MEASURED)
        self.assertEqual(self.one(cohort, 30).status, DATA_REQUIRED)

    def test_a_partly_matured_cohort_divides_by_the_matured_half(self):
        """The subtler half. Two Projects started, one three days ago and one
        yesterday; only the first has a D+2 answer. Dividing by two would
        report the second as lost."""
        self.put("OLD", "2026-08-01T09:00:00+09:00")
        self.put("OLD", "2026-08-02T09:00:00+09:00")
        self.put("NEW", "2026-08-03T09:00:00+09:00")
        now = datetime.fromisoformat("2026-08-04T10:00:00+09:00")

        window = self.window("2026-08", 1, now=now)

        self.assertEqual(window.base, 2)  # both have had their D+1 day
        self.assertEqual(window.retained, 1)

        # ...and at D+7 neither has, so there is nothing to divide.
        self.assertEqual(self.window("2026-08", 7, now=now).base, 0)

    def test_the_cohort_size_still_reports_every_member(self):
        """`size` is the cohort; `base` is who could be asked. A reader who
        cannot see both cannot tell a small cohort from a young one."""
        now = self._one_day_old()
        cohort = build_cohort_analysis(
            build_company_rollup(processed_dir=self.processed, now=now), now=now
        ).cohorts[0]

        self.assertEqual(cohort.size, 1)
        self.assertEqual(self.one(cohort, 30).base, 0)

    def test_a_zero_rate_is_still_a_real_answer(self):
        """The other side of the refusal. A matured window in which nobody came
        back is `0.0%` and *not* DATA REQUIRED — the two are opposite findings
        and the module must be able to say both."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")

        window = self.window("2026-08", 30)

        self.assertEqual(window.status, MEASURED)
        self.assertEqual(window.rendered(), "0.0%")

    def test_an_until_bounded_rollup_matures_against_until(self):
        """A rollup asked for "as of 8 August" holds no Event after the 8th, so
        a window ending on the 20th has not elapsed *for this corpus* even if
        today is October. Maturing against the wall clock would score every
        member as lost using evidence that was deliberately excluded."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-20T09:00:00+09:00")

        analysis = self.analysis(until=date(2026, 8, 8))

        self.assertEqual(analysis.as_of, date(2026, 8, 8))
        self.assertEqual(self.one(analysis.cohorts[0], 30).status, DATA_REQUIRED)
        # Unbounded, the same evidence answers it.
        self.assertEqual(self.window("2026-08", 30).retained, 1)


class AFinishedProjectHasNotStalledTests(CohortTestCase):
    """The defect the second audit found, and the four readings that fix it.

    Retention used to be "produced a later Event", full stop. Measured over one
    cohort of three — one Project completed the day it started, one cancelled on
    day three, one abandoned — the panel reported:

        D+7  33.3%   (1 of 3 "still moving")

    The Project counted as *moving* was the **cancelled** one; the Project
    counted as *stalled* was the one that **finished**. Both readings are
    backwards, and a COO acting on that number goes looking for the wrong
    Project. What makes it the worst kind of defect is that it is invisible:
    33.3% is a plausible number and nothing on the screen contradicted it.
    """

    def _three_outcomes(self):
        self.put("DONE_FAST", "2026-08-03T09:00:00+09:00", event_type="STARTED")
        self.put(
            "DONE_FAST", "2026-08-03T17:00:00+09:00",
            event_type="COMPLETED", status="COMPLETED",
        )
        self.put("KILLED", "2026-08-03T09:00:00+09:00", event_type="STARTED")
        self.put(
            "KILLED", "2026-08-05T09:00:00+09:00",
            event_type="CANCELLED", status="CANCELLED",
        )
        self.put("ABANDONED", "2026-08-03T09:00:00+09:00", event_type="STARTED")

    def test_a_completed_project_is_not_counted_as_stalled(self):
        self._three_outcomes()

        window = self.window("2026-08", 7)

        # Only ABANDONED was still running to be asked the question.
        self.assertEqual(window.base, 1)
        self.assertEqual(window.settled, 2)
        self.assertEqual(window.retained, 0)
        self.assertEqual(window.rendered(), "0.0%")

    def test_a_cancelled_project_is_not_counted_as_progress(self):
        """The other half, and the one that flattered the number: the
        cancellation Event *is* activity, so the old rule counted a killed
        Project as one that kept moving."""
        self._three_outcomes()

        self.assertEqual(self.window("2026-08", 7).retained, 0)
        # ...and it is not silently dropped either — it is in `settled`.
        self.assertEqual(self.window("2026-08", 7).settled, 2)
        self.assertEqual(self.window("2026-08", 7).elapsed, 3)

    def test_the_cohort_still_reports_every_member(self):
        """`settled` narrows the denominator, never the cohort. A reader must
        still be able to see that three Projects started."""
        self._three_outcomes()
        cohort = next(c for c in self.analysis().cohorts if c.key == "2026-08")

        self.assertEqual(cohort.size, 3)
        for window in cohort.windows:
            with self.subTest(days=window.days):
                self.assertEqual(window.elapsed, 3)
                self.assertEqual(window.base + window.settled, cohort.size)

    def test_a_project_settled_after_the_window_is_still_measured(self):
        """Settlement only excludes a member from the windows it happened
        inside. A Project that ran for three weeks and then completed was a
        real, answerable D+7 question while it was running."""
        self.put("LONG", "2026-08-03T09:00:00+09:00", event_type="STARTED")
        self.put("LONG", "2026-08-06T09:00:00+09:00")
        self.put(
            "LONG", "2026-08-25T09:00:00+09:00",
            event_type="COMPLETED", status="COMPLETED",
        )

        self.assertEqual(self.window("2026-08", 7).base, 1)
        self.assertEqual(self.window("2026-08", 7).retained, 1)
        self.assertEqual(self.window("2026-08", 7).settled, 0)
        # ...and by D+30 it had ended, so it leaves the rate.
        self.assertEqual(self.window("2026-08", 30).base, 0)
        self.assertEqual(self.window("2026-08", 30).settled, 1)

    def test_a_cohort_that_finished_reads_as_not_applicable(self):
        """The third rendering. Every member ended inside the window, so there
        was nobody left who could stall — that is the best outcome this panel
        can report and it must not be spelled `0%` or `DATA REQUIRED`."""
        self.put("A", "2026-08-03T09:00:00+09:00", event_type="STARTED")
        self.put(
            "A", "2026-08-04T09:00:00+09:00", event_type="COMPLETED",
            status="COMPLETED",
        )

        window = self.window("2026-08", 7)

        self.assertEqual((window.base, window.settled), (0, 1))
        self.assertEqual(window.rendered(), NOT_APPLICABLE_READING)
        self.assertNotEqual(window.rendered(), DATA_REQUIRED_READING)
        self.assertIsNone(window.rate)

    def test_an_unelapsed_window_still_says_data_required(self):
        """The two no-rate answers must stay apart. Same cohort, same code
        path, opposite sentences."""
        self.put("A", "2026-08-03T09:00:00+09:00", event_type="STARTED")
        now = datetime.fromisoformat("2026-08-04T10:00:00+09:00")

        window = self.window("2026-08", 30, now=now)

        self.assertEqual((window.base, window.settled, window.elapsed), (0, 0, 0))
        self.assertEqual(window.rendered(), DATA_REQUIRED_READING)

    def test_a_restarted_project_is_not_settled(self):
        """`settled_at` follows the rollup's fold, so a Project cancelled and
        then resumed is running again — and is back in the denominator. A
        cancellation that was reversed must not permanently remove a Project
        from the one measurement that would notice it stalling."""
        self.put(
            "REVIVED", "2026-08-03T09:00:00+09:00",
            event_type="CANCELLED", status="CANCELLED",
        )
        self.put("REVIVED", "2026-08-06T09:00:00+09:00", event_type="RESUMED")

        window = self.window("2026-08", 7)

        self.assertEqual((window.base, window.settled), (1, 0))
        self.assertEqual(window.retained, 1)


class TheEdgesOfTheCalendarTests(CohortTestCase):
    """Month boundaries, timezones, and dates that should not exist."""

    def test_the_last_instant_of_a_month_is_that_months_cohort(self):
        self.put("PAY", "2026-07-31T23:59:59+09:00")

        self.assertEqual([c.key for c in self.analysis().cohorts], ["2026-07"])

    def test_the_first_instant_of_a_month_is_the_next_cohort(self):
        self.put("PAY", "2026-08-01T00:00:00+09:00")

        self.assertEqual([c.key for c in self.analysis().cohorts], ["2026-08"])

    def test_the_same_instant_written_in_another_offset_lands_in_one_cohort(self):
        """docs/06 §9 fixes this project's day at Asia/Seoul, and a Desktop
        whose clock is set to UTC is reachable (a laptop taken abroad, a VM
        that defaults to UTC). `2026-07-31T16:00Z` **is** `2026-08-01T01:00`
        in Seoul, so it is an August start — reading `.date()` off the written
        offset would file it in July and split one company across two cohorts
        by where somebody was sitting."""
        self.put("PAY", "2026-07-31T16:00:00+00:00")

        self.assertEqual([c.key for c in self.analysis().cohorts], ["2026-08"])

    def test_a_retention_window_crosses_a_month_boundary(self):
        """The window follows the Project, not the calendar. A Project started
        on 30 August and moving on 2 September is retained at D+7 — trimming
        the window at the month end would report the busiest kind of Project as
        abandoned."""
        self.put("PAY", "2026-08-30T09:00:00+09:00")
        self.put("PAY", "2026-09-02T09:00:00+09:00")

        self.assertEqual(self.window("2026-08", 7).retained, 1)

    def test_an_event_dated_after_the_analysis_cannot_satisfy_a_window(self):
        """A clock behind a reporting Desktop is enough to produce one, and
        letting it count would make a cohort's retention depend on data from
        that cohort's future — the number would then change *backwards* when
        the analysis date caught up."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-05T09:00:00+09:00")
        now = datetime.fromisoformat("2026-08-04T10:00:00+09:00")

        self.assertEqual(self.window("2026-08", 1, now=now).retained, 0)

    def test_a_project_whose_first_event_is_in_the_future_gets_its_own_cohort(self):
        """Visible rather than filed away. A month that has not happened is an
        integrity problem an operator should meet — a reporting Desktop whose
        clock is fast — and a cohort row saying so is how they meet it. Every
        window of it is unelapsed, so nothing about it can read as a score."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("LATER", "2026-09-30T09:00:00+09:00")
        now = datetime.fromisoformat("2026-08-04T10:00:00+09:00")

        analysis = self.analysis(now=now)
        future = next(c for c in analysis.cohorts if c.key == "2026-09")

        self.assertEqual([c.key for c in analysis.cohorts], ["2026-08", "2026-09"])
        self.assertEqual(analysis.skipped, ())
        self.assertEqual(
            [w.rendered() for w in future.windows],
            [DATA_REQUIRED_READING] * len(COHORT_WINDOWS),
        )

    def test_a_future_project_cannot_lift_an_earlier_cohorts_retention(self):
        """The half that would be silent: it is in its own cohort and its
        Events are past `as_of`, so neither its membership nor its activity
        touches the cohort a reader is actually looking at."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("LATER", "2026-09-30T09:00:00+09:00")
        now = datetime.fromisoformat("2026-08-04T10:00:00+09:00")

        window = self.window("2026-08", 1, now=now)

        self.assertEqual((window.retained, window.base), (0, 1))

    def test_a_project_with_an_unreadable_first_timestamp_is_reported(self):
        """`validate_event()` blocks this shape at the Collector, so it is
        reachable only through a hand-built rollup — which is exactly the seam
        `build_company_rollup(events=...)` opens. It must not raise, and it
        must not silently shrink the cohort.

        A naive timestamp is the real case: it has no instant, so
        `business_date()` refuses to say which month it fell in rather than
        guessing by up to a day."""
        from controltower.rollup import ProjectRollup

        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)
        broken = ProjectRollup(project_id="BROKEN", first_seen="2026-08-03T09:00:00")
        rollup = type(rollup)(
            projects=(broken,), state_projects=(broken,), events_read=0
        )

        analysis = build_cohort_analysis(rollup, now=NOW)

        self.assertEqual(analysis.cohorts, ())
        self.assertEqual([name for name, _ in analysis.skipped], ["BROKEN"])
        self.assertIn("not a dated instant", analysis.skipped[0][1])

    def test_a_naive_now_still_produces_an_analysis(self):
        """Every other surface here takes an aware `now`; a caller that passes
        a naive one gets an answer rather than a traceback, for the rollup's
        own never-raises reason."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")

        analysis = build_cohort_analysis(
            build_company_rollup(processed_dir=self.processed, now=NOW),
            now=datetime(2026, 10, 1, 10, 0),
        )

        self.assertEqual(analysis.as_of, date(2026, 10, 1))
        self.assertEqual([c.key for c in analysis.cohorts], ["2026-08"])


class NothingIsCountedTwiceTests(CohortTestCase):
    """The duplicate rule, and where it is owned."""

    def test_one_event_arriving_as_two_files_is_one_event(self):
        """`build_company_rollup()` folds on `event_id` (C50) and this module
        reads what survived, so a duplicate cannot inflate a cohort or
        manufacture retention. Asserted here because the fold is upstream and
        an upstream guarantee nobody checks from downstream is one that can be
        removed without this table noticing."""
        event = self.put("PAY", "2026-08-03T09:00:00+09:00", event_id="DUP")
        (self.processed / "DUP-copy.json").write_text(
            event.to_json(), encoding="utf-8"
        )

        cohort = self.analysis().cohorts[0]

        self.assertEqual(cohort.size, 1)
        self.assertEqual(len(cohort.evidence), 1)
        self.assertEqual(self.one(cohort, 1).retained, 0)

    def test_a_duplicate_of_a_later_event_does_not_manufacture_retention(self):
        """The half that would be invisible: two files carrying one *second*
        Event still describe one return, on one day."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        event = self.put("PAY", "2026-08-04T09:00:00+09:00", event_id="DUP2")
        (self.processed / "DUP2-copy.json").write_text(
            event.to_json(), encoding="utf-8"
        )

        window = self.window("2026-08", 1)

        self.assertEqual((window.retained, window.base), (1, 1))

    def test_two_events_on_one_day_are_one_day(self):
        """Retention is "moved again on a later day", so the same day twice is
        the same day. A count of Events here would report 200% retention."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-04T09:00:00+09:00")
        self.put("PAY", "2026-08-04T15:00:00+09:00")

        window = self.window("2026-08", 1)

        self.assertLessEqual(window.retained, window.base)
        self.assertEqual(window.rendered(), "100.0%")


class TheAnswerDoesNotMoveOnItsOwnTests(CohortTestCase):
    """Determinism. A cohort table is read as a trend, so a number that changes
    when nothing changed is worse here than an obviously wrong one."""

    def test_two_builds_over_the_same_evidence_agree(self):
        for project, day in (("A", 1), ("B", 2), ("C", 3)):
            self.put(project, f"2026-08-{day:02d}T09:00:00+09:00")
            self.put(project, f"2026-08-{day + 1:02d}T09:00:00+09:00")

        first = self.analysis()
        second = self.analysis()

        self.assertEqual(first, second)

    def test_the_order_does_not_follow_the_filename(self):
        """Cohorts come out oldest first whatever order the directory is read
        in — a chart whose X axis reordered itself between runs would make a
        month-to-month comparison meaningless."""
        self.put("Z", "2026-09-01T09:00:00+09:00", event_id="AAA")
        self.put("A", "2026-07-01T09:00:00+09:00", event_id="ZZZ")

        self.assertEqual(
            [c.key for c in self.analysis().cohorts], ["2026-07", "2026-09"]
        )

    def test_an_empty_company_has_no_cohorts_and_does_not_raise(self):
        analysis = self.analysis()

        self.assertEqual(analysis.cohorts, ())
        self.assertEqual(analysis.skipped, ())
        self.assertEqual(analysis.windows, COHORT_WINDOWS)


class EveryCohortCitesTheEventThatPutItThereTests(CohortTestCase):
    """Traceability, in this module's own terms: "왜 이 Project가 8월인가" is
    answerable by opening one named file."""

    def test_each_member_contributes_its_first_event(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00", event_id="FIRST")
        self.put("PAY", "2026-08-09T09:00:00+09:00", event_id="LATER")

        cohort = self.analysis().cohorts[0]

        self.assertEqual([ref.event_id for ref in cohort.evidence], ["FIRST"])

    def test_the_cited_event_is_the_one_that_decided_the_cohort(self):
        """Not merely *an* Event of that Project: the citation and the cohort
        assignment must be the same fact, or a reader who opens the file finds
        a date that does not explain the row."""
        self.put("PAY", "2026-07-31T09:00:00+09:00", event_id="JULY")
        self.put("PAY", "2026-08-01T09:00:00+09:00", event_id="AUGUST")

        cohort = self.analysis().cohorts[0]

        self.assertEqual(cohort.key, "2026-07")
        self.assertEqual([ref.event_id for ref in cohort.evidence], ["JULY"])
        self.assertEqual(ref_day(cohort.evidence[0]), date(2026, 7, 31))


def ref_day(ref) -> date:
    return datetime.fromisoformat(ref.at).astimezone(timezone(timedelta(hours=9))).date()


class TheCohortReachesTheDashboardTests(CohortTestCase):
    """The panel, and the two things a renderer must not be able to do with it:
    show a rate without its denominator, or draw a refusal as a zero."""

    def model(self, *, now=NOW):
        return build_dashboard(
            build_company_rollup(processed_dir=self.processed, now=now), now=now
        )

    def test_the_panel_exists_and_is_sourced_even_when_empty(self):
        """Empty and unsourced mean opposite things. No Project yet is a true
        statement about a real source."""
        panel = self.model().panel("COHORT")

        self.assertIsNotNone(panel)
        self.assertEqual(panel.rows, ())
        self.assertEqual(panel.status.value, "SOURCED")
        self.assertTrue(panel.source)

    def test_one_row_per_cohort_keyed_by_the_month(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("ADS", "2026-07-03T09:00:00+09:00")

        rows = self.model().panel("COHORT").rows

        self.assertEqual([row.key for row in rows], ["2026-07", "2026-08"])

    def test_every_window_carries_its_reading_and_its_denominator(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-04T09:00:00+09:00")

        values = self.model().panel("COHORT").rows[0].values

        self.assertEqual(values["size"], 1)
        for days in COHORT_WINDOWS:
            with self.subTest(days=days):
                self.assertEqual(values[f"d{days}"], "100.0%")
                self.assertEqual(values[f"d{days}_retained"], 1)
                self.assertEqual(values[f"d{days}_base"], 1)

    def test_an_unelapsed_window_reaches_the_panel_as_words(self):
        """The refusal has to survive the whole way to the row, or the module's
        care about it buys nothing. `0` here would be a claim."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        now = datetime.fromisoformat("2026-08-04T10:00:00+09:00")

        values = self.model(now=now).panel("COHORT").rows[0].values

        self.assertEqual(values["d30"], DATA_REQUIRED_READING)
        self.assertEqual(values["d30_base"], 0)
        self.assertNotEqual(values["d30"], 0)

    def test_the_payload_keeps_the_refusal_and_the_counts(self):
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        now = datetime.fromisoformat("2026-08-04T10:00:00+09:00")

        payload = self.model(now=now).to_payload()
        row = next(
            row
            for panel in payload["panels"]
            if panel["key"] == "COHORT"
            for row in panel["rows"]
        )

        self.assertEqual(row["values"]["d30"], DATA_REQUIRED_READING)
        self.assertIsInstance(row["values"]["d30_base"], int)
        self.assertEqual(row["evidence_count"], 1)

    def test_the_panel_recounts_nothing_the_rollup_already_counted(self):
        """The C28 property, stated where it would break: the cohort's members
        are the rollup's own Projects, not a second grouping of the Events."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("ADS", "2026-08-04T09:00:00+09:00")

        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)
        analysis = build_cohort_analysis(rollup, now=NOW)

        self.assertEqual(
            sorted(m for c in analysis.cohorts for m in c.members),
            sorted(p.project_id for p in rollup.state_projects),
        )

    def test_the_customer_kpis_are_still_refused(self):
        """The regression that would be hardest to notice and worst to ship.

        A `Cohort` panel reading `D+7 100.0%` sits four rows from a `Retention`
        KPI reading `DATA REQUIRED`, and the temptation is to "fix" the second
        with the first. They are not the same measurement: one follows internal
        Projects, the other follows customers, and this system has no customer.
        Wiring the cohort into those rows would put a fabricated business
        number in front of a CEO with a citation attached."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-04T09:00:00+09:00")
        rows = {row.values["key"]: row for row in self.model().panel("ROLE_KPI").rows}

        for key in ("retention", "churn", "nrr", "customers_active"):
            with self.subTest(kpi=key):
                self.assertFalse(rows[key].values["measured"])
                self.assertEqual(rows[key].values["reading"], DATA_REQUIRED_READING)
                self.assertEqual(rows[key].evidence, ())

    def test_no_existing_metric_changed_meaning(self):
        """The regression this whole change is measured against: adding a
        cohort must not move a single number the Control Tower already
        reported."""
        self.put("PAY", "2026-08-03T09:00:00+09:00")
        self.put("PAY", "2026-08-04T09:00:00+09:00")
        model = self.model()

        metrics = {
            row.key: row.values["value"] for row in model.panel("METRICS").rows
        }

        self.assertEqual(metrics["events"], 2)
        self.assertEqual(metrics["projects_active"], 1)
        self.assertEqual(model.events_read, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
