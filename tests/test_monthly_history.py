"""Monthly History tests (docs/09_MONTHLY_HISTORY_SPEC.md).

docs/09 §91-104 name fourteen Mock Tests and §105 gives a Test Matrix.
Each is covered below, plus the parser and state behaviour they rest on.

    §91  Normal Month            §98  Issue Lifecycle
    §92  Missing Daily           §99  Open Issue
    §93  Empty Daily             §100 Product Evolution
    §94  PC OFF on 1st           §101 No KPI
    §95  Multiple Missing Months §102 Late Event
    §96  Current Month           §103 AI Failure
    §97  Major Decision          §104 Backup Failure

Three of those (§98-100) and §101 describe sections V1 cannot derive by
rule; the tests below pin that they are OMITTED rather than fabricated,
which is what §14/§30/§64/§65 require and what markdown.py documents.
"""

import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import generate_daily_history, update_daily_history  # noqa: E402
from history import HistoryCandidate, HistoryDecision  # noqa: E402
from history.file_repository import FileHistoryRepository  # noqa: E402
from monthly import (  # noqa: E402
    NO_MATERIAL_HISTORY_SENTENCE,
    MonthlyState,
    MonthlyStateError,
    MonthlyStatus,
    check_coverage,
    consolidate_month,
    load_state,
    mark_month_dirty,
    month_key,
    parse_daily_markdown,
    pending_months,
    run_once,
    save_state,
)

START = date(2026, 8, 1)

# A file that exists and is readable at the OS level but is not valid UTF-8.
# Distinct from a *missing* file, which is a coverage gap rather than a
# corruption, and from an *empty* file, which decodes fine to no content.
UNDECODABLE_BYTES = b"\xff\xfe\x00 not utf-8 \xff"


def candidate(event_id, *, day, category="MILESTONE", project="SEARCH_BACKEND",
              role="CTO_BACKEND", summary=None, hour=10):
    return HistoryCandidate(
        history_id=f"HIST-{event_id}",
        event_id=event_id,
        timestamp=f"{day.isoformat()}T{hour:02d}:00:00+09:00",
        category=category,
        project_id=project,
        role=role,
        summary=summary or f"work {event_id}",
        evidence=(),
        filter_result=HistoryDecision.KEEP,
    )


class MonthlyTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.daily_dir = self.root / "daily"
        self.monthly_dir = self.root / "monthly"
        self.state_path = self.root / "state" / "monthly_history_state.json"
        self.repo = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )

    def fill_month(self, year, month, *, days_with_work=(), skip_days=(), category="MILESTONE"):
        """Write a full month of Daily files, with work on the named days."""
        import calendar

        _, last = calendar.monthrange(year, month)
        for day_number in range(1, last + 1):
            day = date(year, month, day_number)
            if day_number in skip_days:
                continue
            if day_number in days_with_work:
                self.repo.save(
                    candidate(
                        f"EVT-{year}{month:02d}{day_number:02d}",
                        day=day,
                        category=category,
                    )
                )
            generate_daily_history(
                self.repo,
                day,
                output_dir=self.daily_dir,
                generated_at=f"{year:04d}-{month:02d}-{day_number:02d}T11:00:00+09:00",
            )

    def consolidate(self, year, month, *, now=None, allow_update=False):
        return consolidate_month(
            year=year,
            month=month,
            daily_dir=self.daily_dir,
            monthly_dir=self.monthly_dir,
            history_start_date=START,
            now=now or datetime(year, month, 28, 11, 0).astimezone(),
            allow_update=allow_update,
        )

    def run_monthly(self, *, now, history_start_date=START):
        return run_once(
            daily_dir=self.daily_dir,
            monthly_dir=self.monthly_dir,
            history_start_date=history_start_date,
            now=now,
            state_path=self.state_path,
        )

    def monthly_text(self, year, month):
        return (self.monthly_dir / f"{month_key(year, month)}.md").read_text(encoding="utf-8")


class ParserTests(unittest.TestCase):
    """Monthly reads the Daily files (docs/09 §12-13), which a human is
    allowed to edit (docs/06 §57). The parser must degrade, not fail."""

    def _daily(self, body):
        return parse_daily_markdown(body, target_date=date(2026, 8, 5))

    def test_items_are_read_from_their_category_section(self):
        body = (
            "# DOJOONPASS Company History — 2026-08-05\n\n"
            "## Decisions\n\n### Closed Beta\n\n- Scope confirmed.\n"
            "- Owner: COO\n- Event ID: EVT-1\n\n"
            "## Milestones\n\n### Search\n\n- UI done.\n"
            "- Owner: CTO Frontend\n- Event ID: EVT-2\n"
        )
        document = self._daily(body)

        self.assertEqual(len(document.items), 2)
        decision = next(i for i in document.items if i.event_id == "EVT-1")
        self.assertEqual(decision.category, "DECISION")
        self.assertEqual(decision.project, "Closed Beta")
        self.assertEqual(decision.summary, "Scope confirmed.")
        self.assertEqual(decision.owner, "COO")
        milestone = next(i for i in document.items if i.event_id == "EVT-2")
        self.assertEqual(milestone.category, "MILESTONE")

    def test_a_late_item_carries_its_own_category(self):
        """`## Late Events` mixes categories, so each item states its own."""
        body = (
            "# Title\n\n## Late Events\n\n### Content Os\n\n- Campaign shipped.\n"
            "- Owner: CMO\n- Event ID: EVT-LATE\n- Category: DECISION\n"
        )
        document = self._daily(body)

        self.assertEqual(len(document.items), 1)
        self.assertEqual(document.items[0].category, "DECISION")
        self.assertTrue(document.items[0].is_late)

    def test_a_late_item_without_a_category_is_skipped_not_guessed(self):
        body = (
            "# Title\n\n## Late Events\n\n### P\n\n- Something.\n"
            "- Owner: COO\n- Event ID: EVT-OLD\n"
        )
        self.assertEqual(self._daily(body).items, ())

    def test_an_item_without_an_event_id_is_skipped(self):
        """Monthly de-duplicates by event_id (§59); an entry that cannot be
        de-duplicated must not be consolidated."""
        body = "# Title\n\n## Milestones\n\n### P\n\n- No id here.\n- Owner: COO\n"
        self.assertEqual(self._daily(body).items, ())

    def test_the_empty_day_marker_is_recognised(self):
        body = (
            "# DOJOONPASS Company History — 2026-08-05\n\n"
            "No material company history recorded.\n\n## Metadata\n\n- Event Count: 0\n"
        )
        document = self._daily(body)

        self.assertTrue(document.is_empty_day)
        self.assertFalse(document.has_material_history)

    def test_evidence_and_metadata_sections_contribute_nothing(self):
        body = (
            "# Title\n\n## Evidence\n\n- EVT-9: pytest PASS\n\n"
            "## Metadata\n\n- History Date: 2026-08-05\n"
        )
        self.assertEqual(self._daily(body).items, ())

    def test_a_hand_written_section_is_ignored_without_error(self):
        body = (
            "# Title\n\n## Milestones\n\n### P\n\n- Real.\n- Owner: COO\n- Event ID: EVT-1\n\n"
            "## COO Note\n\n비고: 이 부분은 사람이 직접 쓴 것이다.\n"
        )
        document = self._daily(body)

        self.assertEqual([i.event_id for i in document.items], ["EVT-1"])

    def test_the_section_titles_match_the_daily_renderer(self):
        """The inverse mapping is a literal here; if the Daily renderer's
        titles change, historical Monthly output would silently
        re-categorise. This makes that a failing test instead."""
        from daily.markdown import _SECTION_TITLE_BY_CATEGORY
        from monthly.parser import CATEGORY_BY_SECTION_TITLE

        self.assertEqual(
            {title: category for category, title in _SECTION_TITLE_BY_CATEGORY.items()},
            CATEGORY_BY_SECTION_TITLE,
        )


class CoverageTests(MonthlyTestCase):
    """docs/09 §10, §38-39, §85."""

    def test_a_full_month_is_complete(self):
        self.fill_month(2026, 8, days_with_work=(5,))

        coverage = check_coverage(self.daily_dir, 2026, 8, history_start_date=START)

        self.assertTrue(coverage.is_complete)
        self.assertEqual(coverage.status, "COMPLETE")
        self.assertEqual(len(coverage.expected_dates), 31)

    def test_a_missing_day_makes_it_incomplete(self):
        self.fill_month(2026, 8, days_with_work=(5,), skip_days=(30, 31))

        coverage = check_coverage(self.daily_dir, 2026, 8, history_start_date=START)

        self.assertFalse(coverage.is_complete)
        self.assertEqual(
            coverage.missing_dates, (date(2026, 8, 30), date(2026, 8, 31))
        )

    def test_an_empty_daily_counts_as_covered(self):
        """§11: a day with nothing material still produced a file."""
        self.fill_month(2026, 8)

        coverage = check_coverage(self.daily_dir, 2026, 8, history_start_date=START)

        self.assertTrue(coverage.is_complete)

    def test_a_mid_month_history_start_trims_the_expected_range(self):
        """§85: days that predate Company History are not gaps."""
        import calendar

        _, last = calendar.monthrange(2026, 8)
        for day_number in range(15, last + 1):
            generate_daily_history(
                self.repo, date(2026, 8, day_number), output_dir=self.daily_dir
            )

        coverage = check_coverage(
            self.daily_dir, 2026, 8, history_start_date=date(2026, 8, 15)
        )

        self.assertTrue(coverage.is_complete)
        self.assertTrue(coverage.starts_mid_month)
        self.assertIn("partial month from history start", coverage.description)


class PendingMonthTests(unittest.TestCase):
    """docs/09 §47-49, §90: state-based, oldest first, never the current."""

    def test_the_current_month_is_never_included(self):
        months = pending_months(
            last_successful_monthly_close="2026-07",
            history_start_date=START,
            now=datetime(2026, 9, 15, 11, 0),
        )
        self.assertEqual(months, [(2026, 8)])

    def test_a_run_late_in_the_month_still_excludes_it(self):
        months = pending_months(
            last_successful_monthly_close="2026-08",
            history_start_date=START,
            now=datetime(2026, 9, 30, 23, 59),
        )
        self.assertEqual(months, [])

    def test_several_missed_months_come_out_oldest_first(self):
        """§48's worked example."""
        months = pending_months(
            last_successful_monthly_close="2026-08",
            history_start_date=START,
            now=datetime(2026, 12, 3, 11, 0),
        )
        self.assertEqual(months, [(2026, 9), (2026, 10), (2026, 11)])

    def test_a_first_ever_run_starts_at_the_history_start_month(self):
        """§86: months predating Company Ops are never invented."""
        months = pending_months(
            last_successful_monthly_close=None,
            history_start_date=date(2026, 8, 15),
            now=datetime(2026, 10, 2, 11, 0),
        )
        self.assertEqual(months, [(2026, 8), (2026, 9)])

    def test_a_year_boundary_is_crossed_correctly(self):
        months = pending_months(
            last_successful_monthly_close="2026-11",
            history_start_date=START,
            now=datetime(2027, 2, 3, 11, 0),
        )
        self.assertEqual(months, [(2026, 12), (2027, 1)])


class MockTestNormalMonth(MonthlyTestCase):
    """docs/09 §91."""

    def test_a_complete_month_is_consolidated(self):
        self.fill_month(2026, 8, days_with_work=(5, 12, 20))

        result = self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.generated, ("2026-08",))
        self.assertEqual(result.last_successful_monthly_close, "2026-08")
        self.assertEqual(load_state(self.state_path).last_successful_monthly_close, "2026-08")

        text = self.monthly_text(2026, 8)
        self.assertIn("# DOJOONPASS Company History — 2026-08", text)
        self.assertIn("## Major Milestones", text)
        self.assertIn("- History Month: 2026-08", text)
        self.assertIn("- Daily Coverage: COMPLETE", text)
        self.assertIn("- Consolidated Items: 3", text)

    def test_source_records_list_only_the_days_that_contributed(self):
        """§36: 모든 Empty Daily를 나열할 필요는 없다."""
        self.fill_month(2026, 8, days_with_work=(5, 12))

        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        text = self.monthly_text(2026, 8)

        self.assertIn("## Source Records", text)
        self.assertIn("- 2026-08-05.md", text)
        self.assertIn("- 2026-08-12.md", text)
        self.assertNotIn("- 2026-08-06.md", text)

    def test_each_category_lands_in_its_monthly_section(self):
        for day_number, category in ((3, "DECISION"), (7, "MILESTONE"), (9, "ISSUE"),
                                     (11, "LEARNING")):
            self.repo.save(
                candidate(
                    f"EVT-CAT-{day_number}",
                    day=date(2026, 8, day_number),
                    category=category,
                )
            )
        self.fill_month(2026, 8)

        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        text = self.monthly_text(2026, 8)

        self.assertIn("## Major Decisions", text)
        self.assertIn("## Major Milestones", text)
        self.assertIn("## Major Issues & Resolutions", text)
        self.assertIn("## Key Learnings", text)


class MockTestMissingDaily(MonthlyTestCase):
    """docs/09 §92: Daily 누락 -> MONTHLY_PENDING, 파일 생성 금지."""

    def test_a_missing_daily_blocks_consolidation(self):
        self.fill_month(2026, 8, days_with_work=(5,), skip_days=(30,))

        result = self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.pending, ("2026-08",))
        self.assertEqual(result.generated, ())
        self.assertFalse((self.monthly_dir / "2026-08.md").exists())
        self.assertIsNone(load_state(self.state_path).last_successful_monthly_close)

    def test_it_succeeds_once_the_daily_catch_up_fills_the_gap(self):
        self.fill_month(2026, 8, days_with_work=(5,), skip_days=(30,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        generate_daily_history(self.repo, date(2026, 8, 30), output_dir=self.daily_dir)
        result = self.run_monthly(now=datetime(2026, 9, 2, 11, 0).astimezone())

        self.assertEqual(result.generated, ("2026-08",))
        self.assertTrue((self.monthly_dir / "2026-08.md").exists())


class MockTestEmptyMonth(MonthlyTestCase):
    """docs/09 §71-73, §93."""

    def test_a_month_with_no_material_history_still_gets_a_file(self):
        """§72: "해당 월 누락"과 "중요한 변화 없음"을 구분하기 위해서다."""
        self.fill_month(2026, 8)

        result = self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.generated, ("2026-08",))
        text = self.monthly_text(2026, 8)
        self.assertIn(NO_MATERIAL_HISTORY_SENTENCE, text)
        self.assertIn("- Consolidated Items: 0", text)
        self.assertNotIn("## Major Milestones", text)


class MockTestPcOffAndCatchup(MonthlyTestCase):
    """docs/09 §94, §95, §46-48, §90."""

    def test_a_pc_off_on_the_first_is_caught_up_later(self):
        self.fill_month(2026, 8, days_with_work=(5,))

        result = self.run_monthly(now=datetime(2026, 9, 3, 9, 0).astimezone())

        self.assertEqual(result.generated, ("2026-08",))

    def test_several_missed_months_are_consolidated_oldest_first(self):
        for month in (8, 9, 10):
            self.fill_month(2026, month, days_with_work=(5,))

        result = self.run_monthly(now=datetime(2026, 11, 4, 9, 0).astimezone())

        self.assertEqual(result.generated, ("2026-08", "2026-09", "2026-10"))
        self.assertEqual(load_state(self.state_path).last_successful_monthly_close, "2026-10")

    def test_a_gap_stops_the_catch_up_rather_than_leaving_a_hole(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        self.fill_month(2026, 9, days_with_work=(5,), skip_days=(20,))
        self.fill_month(2026, 10, days_with_work=(5,))

        result = self.run_monthly(now=datetime(2026, 11, 4, 9, 0).astimezone())

        self.assertEqual(result.generated, ("2026-08",))
        self.assertEqual(result.pending, ("2026-09",))
        self.assertFalse((self.monthly_dir / "2026-10.md").exists())
        self.assertEqual(load_state(self.state_path).last_successful_monthly_close, "2026-08")


class MockTestCurrentMonth(MonthlyTestCase):
    """docs/09 §49, §96."""

    def test_the_current_month_is_not_consolidated(self):
        self.fill_month(2026, 8, days_with_work=(5,))

        result = self.run_monthly(now=datetime(2026, 8, 20, 11, 0).astimezone())

        self.assertEqual(result.generated, ())
        self.assertFalse((self.monthly_dir / "2026-08.md").exists())


class MockTestLateEventAndDirty(MonthlyTestCase):
    """docs/09 §54-58, §102: a Daily changed after its Monthly was written."""

    def _close_august_then_add_a_late_event(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        before = self.monthly_text(2026, 8)
        self.assertNotIn("EVT-LATE", before)

        self.repo.save(
            candidate("EVT-LATE", day=date(2026, 8, 20), category="DECISION",
                      summary="late but important decision")
        )
        outcome = update_daily_history(
            self.repo,
            date(2026, 8, 20),
            output_dir=self.daily_dir,
            now=datetime(2026, 9, 3, 15, 20).astimezone(),
        )
        self.assertEqual(outcome.outcome.value, "UPDATED_LATE_EVENT")
        return before

    def test_a_late_event_marks_the_month_dirty(self):
        self._close_august_then_add_a_late_event()

        marked = mark_month_dirty(self.state_path, date(2026, 8, 20))

        self.assertTrue(marked)
        self.assertEqual(load_state(self.state_path).dirty_months, ["2026-08"])

    def test_the_next_run_rebuilds_a_dirty_month(self):
        before = self._close_august_then_add_a_late_event()
        mark_month_dirty(self.state_path, date(2026, 8, 20))

        result = self.run_monthly(now=datetime(2026, 9, 3, 16, 0).astimezone())

        self.assertIn("2026-08", result.generated)
        after = self.monthly_text(2026, 8)
        self.assertIn("late but important decision", after)
        self.assertIn("## Major Decisions", after)
        self.assertNotEqual(after, before)
        self.assertEqual(load_state(self.state_path).dirty_months, [])

    def test_an_update_records_that_it_changed(self):
        """§58: 기존 Monthly를 아무 기록 없이 조용히 덮어쓰지 않는다."""
        self._close_august_then_add_a_late_event()
        mark_month_dirty(self.state_path, date(2026, 8, 20))

        self.run_monthly(now=datetime(2026, 9, 3, 16, 0).astimezone())
        text = self.monthly_text(2026, 8)

        self.assertIn("- Generated At: 2026-09-01T11:00:00", text)
        self.assertIn("- Last Updated At: 2026-09-03T16:00:00", text)

    def test_a_month_never_consolidated_is_not_marked_dirty(self):
        """The pending catch-up will read the updated Daily anyway."""
        self.fill_month(2026, 8, days_with_work=(5,))

        self.assertFalse(mark_month_dirty(self.state_path, date(2026, 8, 20)))
        self.assertEqual(load_state(self.state_path).dirty_months, [])


class DuplicateProtectionTests(MonthlyTestCase):
    """docs/09 §59: 동일 Daily Event가 여러 번 Monthly에 반복되지 않는다."""

    def test_one_event_id_produces_one_entry(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        # The same event_id also appears in another day's file, as a
        # restored backup or a hand-merge would leave it.
        other = self.daily_dir / "2026-08-06.md"
        other.write_text(
            "# DOJOONPASS Company History — 2026-08-06\n\n"
            "## Milestones\n\n### Search Backend\n\n- work EVT-20260805.\n"
            "- Owner: CTO Backend\n- Event ID: EVT-20260805\n",
            encoding="utf-8",
        )

        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        text = self.monthly_text(2026, 8)

        self.assertEqual(text.count("- Event ID: EVT-20260805"), 1)
        self.assertIn("- Consolidated Items: 1", text)

    def test_rerunning_does_not_touch_an_existing_month(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        before = self.monthly_text(2026, 8)

        save_state(self.state_path, MonthlyState())
        result = self.run_monthly(now=datetime(2026, 9, 2, 11, 0).astimezone())

        self.assertEqual(self.monthly_text(2026, 8), before)
        self.assertEqual(
            [r.status for r in result.results], [MonthlyStatus.MONTHLY_UNCHANGED]
        )
        self.assertEqual(load_state(self.state_path).last_successful_monthly_close, "2026-08")


class OmittedSectionTests(MonthlyTestCase):
    """docs/09 §98-101 describe sections V1 cannot derive by rule.

    §14 says an empty section is not padded, and §30/§64/§65 forbid the
    alternative — a machine writing a plausible sentence nobody observed.
    Pinned so that "the section is missing" reads as a deliberate, spec-
    backed omission rather than an oversight.
    """

    def test_no_kpi_section_is_invented(self):
        """§101 / §30: 자료가 없으면 숫자를 만들어내지 않는다."""
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        text = self.monthly_text(2026, 8)
        self.assertNotIn("KPI", text)
        self.assertNotIn("Conversion", text)
        self.assertNotIn("Retention", text)

    def test_no_narrative_sections_are_invented(self):
        """§100 Product Evolution and §15 Executive Summary both need a
        judgement; §108 rules out a narrative generator in V1."""
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        text = self.monthly_text(2026, 8)
        self.assertNotIn("## Product Evolution", text)
        self.assertNotIn("## Executive Summary", text)

    def test_no_open_risk_or_carryover_is_invented(self):
        """§99 Open Issue / §32-34 need issue-to-resolution pairing, which
        `HistoryCandidate` cannot express (it carries category, not
        event_type)."""
        self.repo.save(candidate("EVT-ISSUE", day=date(2026, 8, 5), category="ISSUE"))
        self.fill_month(2026, 8)
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        text = self.monthly_text(2026, 8)
        self.assertIn("## Major Issues & Resolutions", text)
        self.assertNotIn("## Open Risks", text)
        self.assertNotIn("## Next-Month Carryover", text)

    def test_the_empty_month_summary_is_the_spec_s_own_sentence(self):
        """§71 supplies the exact wording, so that one case IS mechanical."""
        self.fill_month(2026, 8)
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertIn(
            "No material company-level changes were recorded during this month.",
            self.monthly_text(2026, 8),
        )

    def test_no_ai_or_llm_client_is_imported_by_monthly(self):
        """§63: the rule-based path must work with no AI at all.

        Checked against real import statements rather than a substring
        sweep — the modules legitimately *discuss* why there is no LLM, and
        a text scan would flag that prose as the violation it is warning
        about.
        """
        import ast

        forbidden = {"openai", "anthropic", "langchain", "llm", "gpt", "transformers"}
        for path in (Path(__file__).resolve().parents[1] / "src" / "monthly").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    with self.subTest(module=path.name, imported=name):
                        self.assertNotIn(name.lower(), forbidden)


class FailurePathTests(MonthlyTestCase):
    """docs/09 §44, §74: a failure never deletes or corrupts what exists."""

    def _corrupt_a_daily(self, day: date) -> None:
        """Leave the file present but undecodable.

        A *missing* Daily is a different situation entirely (COVERAGE
        INCOMPLETE -> PENDING, tested above); this is the one where the day
        looks covered but its content cannot be read, which must be FAILED
        so a human is told rather than the month being quietly short.
        """
        (self.daily_dir / f"{day.isoformat()}.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")

    def test_an_undecodable_daily_fails_without_writing_a_monthly(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        self._corrupt_a_daily(date(2026, 8, 5))

        result = self.consolidate(2026, 8, now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.status, MonthlyStatus.MONTHLY_FAILED)
        self.assertFalse((self.monthly_dir / "2026-08.md").exists())

    def test_a_failed_rebuild_keeps_the_previous_monthly(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        before = self.monthly_text(2026, 8)

        self._corrupt_a_daily(date(2026, 8, 5))

        result = self.consolidate(
            2026, 8, now=datetime(2026, 9, 3, 11, 0).astimezone(), allow_update=True
        )

        self.assertEqual(result.status, MonthlyStatus.MONTHLY_FAILED)
        self.assertEqual(self.monthly_text(2026, 8), before)

    def test_a_failed_month_does_not_advance_the_state(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        self._corrupt_a_daily(date(2026, 8, 5))

        result = self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.generated, ())
        self.assertIsNone(load_state(self.state_path).last_successful_monthly_close)

    def test_a_corrupted_state_file_is_reported_not_guessed_at(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(MonthlyStateError):
            load_state(self.state_path)

    def test_an_invalid_month_key_in_state_is_rejected(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"last_successful_monthly_close": "2026-13"}), encoding="utf-8"
        )

        with self.assertRaises(MonthlyStateError):
            load_state(self.state_path)


class ValidationTests(MonthlyTestCase):
    """docs/09 §81's post-generation checks."""

    def test_the_generated_file_satisfies_every_validation_item(self):
        self.fill_month(2026, 8, days_with_work=(5, 12))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        path = self.monthly_dir / "2026-08.md"
        text = path.read_text(encoding="utf-8")

        self.assertTrue(path.exists())                       # 파일이 생성됐는가
        self.assertIn("2026-08", text)                       # 올바른 대상 월인가
        self.assertIn("- Daily Coverage: COMPLETE", text)    # Coverage COMPLETE인가
        self.assertGreater(len(text), 200)                   # 비정상적으로 Empty인가
        self.assertIn("## Metadata", text)                   # Metadata가 존재하는가
        self.assertIn("## Source Records", text)             # Source 추적 가능한가

    def test_the_file_is_utf8_and_ends_with_one_newline(self):
        """§80 Encoding."""
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        raw = (self.monthly_dir / "2026-08.md").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))


class MonthlyFaultInjectionTests(MonthlyTestCase):
    """Faults specific to the Monthly layer, beyond the failure paths above.

    The standard is the same one the rest of the system is held to: no
    loss, no duplication, no state advanced on a failure, and the next run
    recovers.
    """

    def test_a_zero_byte_daily_is_treated_as_present_but_empty(self):
        """A truncated-to-nothing Daily is readable and parses to no items.
        It must not crash the month, and it must not silently look like a
        day that had work."""
        self.fill_month(2026, 8, days_with_work=(5, 12))
        (self.daily_dir / "2026-08-12.md").write_text("", encoding="utf-8")

        result = self.consolidate(2026, 8, now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.status, MonthlyStatus.MONTHLY_GENERATED)
        self.assertEqual(result.item_count, 1)
        text = self.monthly_text(2026, 8)
        self.assertNotIn("2026-08-12.md", text.split("## Source Records")[1])

    def test_a_monthly_directory_blocked_by_a_file_fails_safely(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        self.monthly_dir.parent.mkdir(parents=True, exist_ok=True)
        self.monthly_dir.write_text("a file where the directory must be", encoding="utf-8")

        result = self.consolidate(2026, 8, now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.status, MonthlyStatus.MONTHLY_FAILED)
        self.assertTrue(self.monthly_dir.is_file(), "the blocking file was clobbered")

    def test_a_crash_between_writing_and_saving_state_does_not_duplicate(self):
        """The file is on disk but state never advanced — the shape a kill
        between the two leaves. The next run must recognise the month as
        done rather than rebuild or duplicate it."""
        self.fill_month(2026, 8, days_with_work=(5,))
        self.consolidate(2026, 8, now=datetime(2026, 9, 1, 11, 0).astimezone())
        before = self.monthly_text(2026, 8)
        self.assertIsNone(load_state(self.state_path).last_successful_monthly_close)

        result = self.run_monthly(now=datetime(2026, 9, 2, 11, 0).astimezone())

        self.assertEqual(
            [r.status for r in result.results], [MonthlyStatus.MONTHLY_UNCHANGED]
        )
        self.assertEqual(self.monthly_text(2026, 8), before)
        self.assertEqual(load_state(self.state_path).last_successful_monthly_close, "2026-08")

    def test_a_corrupted_existing_monthly_is_rebuilt_without_losing_the_new_content(self):
        """`_existing_generated_at()` reads the old file to carry its
        Generated At forward. If that file is unreadable the rebuild must
        still produce a correct Monthly rather than abort."""
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        (self.monthly_dir / "2026-08.md").write_bytes(UNDECODABLE_BYTES)

        self.repo.save(candidate("EVT-NEW", day=date(2026, 8, 20), summary="rebuilt entry"))
        update_daily_history(
            self.repo,
            date(2026, 8, 20),
            output_dir=self.daily_dir,
            now=datetime(2026, 9, 3, 15, 0).astimezone(),
        )
        mark_month_dirty(self.state_path, date(2026, 8, 20))

        result = self.run_monthly(now=datetime(2026, 9, 3, 16, 0).astimezone())

        self.assertIn("2026-08", result.generated)
        text = self.monthly_text(2026, 8)
        self.assertIn("rebuilt entry", text)
        self.assertIn("- Last Updated At: ", text)
        self.assertEqual(load_state(self.state_path).dirty_months, [])

    def test_a_dirty_month_that_fails_stays_dirty(self):
        """A failed rebuild must not clear the flag — otherwise the month is
        left permanently disagreeing with its Daily and nothing retries."""
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        mark_month_dirty(self.state_path, date(2026, 8, 5))
        self._corrupt_a_daily(date(2026, 8, 5))

        result = self.run_monthly(now=datetime(2026, 9, 3, 16, 0).astimezone())

        self.assertTrue(
            any(r.status is MonthlyStatus.MONTHLY_FAILED for r in result.results)
        )
        self.assertEqual(load_state(self.state_path).dirty_months, ["2026-08"])

    def _corrupt_a_daily(self, day):
        (self.daily_dir / f"{day.isoformat()}.md").write_bytes(UNDECODABLE_BYTES)

    def test_a_dirty_month_never_consolidated_is_not_a_crash(self):
        """A state file that names a dirty month with no Monthly file — a
        hand edit, or a restored backup — must not break the run."""
        self.fill_month(2026, 8, days_with_work=(5,))
        save_state(
            self.state_path,
            MonthlyState(last_successful_monthly_close="2026-08", dirty_months=["2026-07"]),
        )

        result = self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        # 2026-07 has no Daily coverage at all, so it reports PENDING and the
        # flag survives for a human to resolve.
        self.assertTrue(
            any(r.key == "2026-07" for r in result.results)
        )
        self.assertEqual(load_state(self.state_path).dirty_months, ["2026-07"])

    def test_a_future_dated_state_never_walks_backwards(self):
        self.fill_month(2026, 8, days_with_work=(5,))
        save_state(
            self.state_path, MonthlyState(last_successful_monthly_close="2027-01")
        )

        result = self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertEqual(result.results, ())
        self.assertFalse((self.monthly_dir / "2026-08.md").exists())


class MonthlyIsNotNotionTests(unittest.TestCase):
    """docs/09 §82-84 are negative requirements, and the current behaviour
    already satisfies them. Pinned because the natural "improvement" someone
    reaches for later — syncing Monthly into Notion — is the thing the spec
    rules out.

        §82  Notion is "지금 상태", Monthly is "지난달 변화". Roles differ,
             and "Notion 내용을 Monthly 원본으로 직접 Dump하지 않는다."
        §83  Monthly is not the COO's judgement report.
        §84  Monthly is not automatically a CEO report.

    Monthly History is an Evidence Layer (§65). Anything built on top of it
    is a separate artefact, produced deliberately by a person.
    """

    def test_monthly_output_is_never_sent_to_notion(self):
        import inspect

        from app import runner as runner_module

        source = inspect.getsource(runner_module.run_once)
        monthly_step = source[source.index("6.7. Monthly") : source.index("# 7. Backup")]
        for forbidden in ("notion_sync", "dashboard", "NotionClient"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, monthly_step)

    def test_the_monthly_package_cannot_reach_notion_at_all(self):
        import ast

        for path in (Path(__file__).resolve().parents[1] / "src" / "monthly").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    with self.subTest(module=path.name, imported=name):
                        self.assertNotEqual(name, "notion")

    def test_monthly_makes_no_management_judgement(self):
        """§65: Monthly History는 Evidence Layer다. 경영판단을 자동 확정하지
        않는다."""
        from monthly.markdown import SECTION_TITLE_BY_CATEGORY

        rendered_sections = set(SECTION_TITLE_BY_CATEGORY.values())
        for judgement in ("Launch Go", "No-Go", "Company Health", "Recommendation"):
            self.assertNotIn(judgement, rendered_sections)


if __name__ == "__main__":
    unittest.main()
