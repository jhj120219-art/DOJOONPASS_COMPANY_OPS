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
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# The repository root too: this file imports a root-level script
# (`ops_status.py` and friends live beside `src/`, not in it). Under
# pytest the rootdir is already on `sys.path`, so the omission only
# surfaced once `python tests/<file>.py` started running the whole
# file instead of stopping at a stray `unittest.main()` (C38).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    def test_a_summary_that_reads_like_a_label_is_still_a_summary(self):
        """REGRESSION. `_first_bullet()` used to skip any bullet matching the
        shape `^[A-Z][A-Za-z ]+:[ \\t]` — "does this look like a label" rather
        than "is this one of our labels".

        An ordinary summary of `Fixed: login token refresh loop.` matches it.
        So did every other bullet in the block (`Owner:`, `Event ID:`), which
        meant `_first_bullet()` returned None, the item was dropped, and the
        Event vanished from Monthly History with no warning anywhere —
        `Consolidated Items` simply counted one fewer. Nothing about the
        input is crafted; this is how engineers write summaries.
        """
        body = (
            "# Title\n\n## Issues\n\n### Auth Service\n\n"
            "- Fixed: login token refresh loop.\n"
            "- Owner: CTO Backend\n- Event ID: EVT-1\n"
        )
        document = self._daily(body)

        self.assertEqual([i.event_id for i in document.items], ["EVT-1"])
        self.assertEqual(document.items[0].summary, "Fixed: login token refresh loop.")

    def test_the_common_label_shaped_summaries_all_survive(self):
        """The shape is not rare. Each of these lost its whole item."""
        for summary in (
            "Fixed: login token refresh loop.",
            "Decision: adopt the new deploy runbook.",
            "Resolved: duplicate charge on retry.",
            "Note: campaign paused until Q4.",
            "TODO: split the auth service.",
            "Launch: closed beta opened to 50 users.",
            "Beta: onboarding flow shipped.",
        ):
            with self.subTest(summary=summary):
                body = (
                    "# Title\n\n## Milestones\n\n### P\n\n"
                    f"- {summary}\n- Owner: COO\n- Event ID: EVT-S\n"
                )
                document = self._daily(body)

                self.assertEqual(len(document.items), 1)
                self.assertEqual(document.items[0].summary, summary)

    def test_the_renderer_s_own_label_bullets_are_still_skipped(self):
        """The reason the scan exists at all: docs/06 §57 lets a human edit
        the Daily, so a label bullet may sit above the summary. Every label
        `daily/markdown._render_item_block()` can write must still be
        skipped, or a hand-reordered block would report `Owner: COO` as its
        summary."""
        body = (
            "# Title\n\n## Decisions\n\n### P\n\n"
            "- Owner: COO\n"
            "- Event ID: EVT-REORDERED\n"
            "- Decision Context: budget review\n"
            "- Expected Outcome: 20% lower spend\n"
            "- Actual Outcome: 18% lower spend\n"
            "- Lessons Learned: measure earlier\n"
            "- The real summary sits last.\n"
        )
        document = self._daily(body)

        self.assertEqual(len(document.items), 1)
        self.assertEqual(document.items[0].summary, "The real summary sits last.")

    def test_the_skipped_labels_are_exactly_the_ones_the_renderer_writes(self):
        """Pins the two lists together. A label added to
        `daily/markdown._render_item_block()` and not here would be reported
        as an item's summary; one removed there and left here would swallow a
        real summary. Either way the drift is silent, so it fails here."""
        import inspect
        import re

        from daily import markdown as daily_markdown
        from monthly.parser import _ITEM_LABELS

        source = inspect.getsource(daily_markdown._render_item_block)
        written = set(re.findall(r'f"- ([A-Za-z ]+): \{', source))

        self.assertEqual(written, set(_ITEM_LABELS))

    def test_an_item_with_an_empty_event_id_is_not_consolidated(self):
        """CHARACTERIZATION — asserts today's behaviour, deliberately.

        `_EVENT_ID_LINE` here uses `(\\S.*?)`, the same shape C31 loosened in
        `daily/late_events.existing_event_ids()`. It is deliberately NOT
        loosened here, because the two regexes answer different questions.

        `late_events` asks "is this Event already in this document" (docs/06
        §38's duplicate guard), and for a rendered `- Event ID: ` the answer
        is plainly yes — leaving it strict made the item re-appended on every
        run (`EmptyEventIdIsStillAnEventIdTests`).

        This one asks "may this be consolidated" (docs/09 §59, keyed on
        event_id). A hand-edited Daily's blank `- Event ID:` line cannot be
        told apart from "I do not know the id", and consolidating several of
        those under `""` would merge distinct items into one. Which reading
        is right is A-15's wall — whether the schema should accept an empty
        `event_id` at all — and it is recorded in BACKLOG C31 §2.

        Measured: a Daily carrying two item blocks, one with a blank id,
        contributes one item to Monthly. If this starts failing, that
        decision was made and BACKLOG must be updated with it.
        """
        body = (
            "# H\n\n## Milestones\n\n"
            "### P\n\n- Real work happened.\n- Owner: COO\n- Event ID: \n\n"
            "### Q\n\n- Other work.\n- Owner: COO\n- Event ID: EVT-2\n"
        )
        document = self._daily(body)

        self.assertEqual([i.event_id for i in document.items], ["EVT-2"])

    def test_the_daily_side_keeps_the_same_item(self):
        """The other half of the asymmetry, asserted in one place so the pair
        is visible rather than inferred from two files."""
        from daily.late_events import existing_event_ids

        body = (
            "# H\n\n## Milestones\n\n"
            "### P\n\n- Real work happened.\n- Owner: COO\n- Event ID: \n"
        )

        self.assertEqual(existing_event_ids(body), {""})

    def test_a_bullet_shaped_summary_inflates_the_unconsolidated_count(self):
        """CHARACTERIZATION — asserts today's behaviour, deliberately.

        The other half of `test_daily_late_events.py::
        OnlyItemBlocksCarryLabelsTests`. `render_daily_markdown()` repeats
        every summary RAW in `## Summary`, so a summary of `- Event ID: L1`
        lands there as a bare line that spells a label. The Daily side read
        it as one and lost a real late Event (fixed there); this side counts
        it, and the count is what `MONTHLY_UNCONSOLIDATED` reports.

        Measured, one ordinary KEEP Candidate, four summaries:

            'Shipped it.'      items 1   unconsolidated 0
            'Event ID: L1'     items 1   unconsolidated 0   <- prose rule
            '- Event ID: L1'   items 1   unconsolidated 1   <- this
            '- Owner: COO'     items 1   unconsolidated 0

        So the operator is told to open a Daily file that lost nothing, on
        every rebuild of that month, forever.

        Left as it is on purpose. This counter deliberately scans the WHOLE
        document rather than the sections it walked — that is what catches a
        section that ended early, taking innocent Events with it — and its
        own contract says over-counting is the safe direction. Narrowing it
        to `### ` blocks (the fix used on the Daily side, where the failure
        was *data loss* rather than a false alarm) would trade a real
        guarantee for precision. BACKLOG records the candidate fix; if this
        test starts failing, that decision was made and BACKLOG must say so.
        """
        from daily.markdown import render_daily_markdown

        measured = {}
        for summary in ("Shipped it.", "Event ID: L1", "- Event ID: L1", "- Owner: COO"):
            document = self._daily(
                render_daily_markdown(
                    date(2026, 8, 5),
                    [
                        HistoryCandidate(
                            history_id="HIST-E1", event_id="E1",
                            timestamp="2026-08-05T10:00:00+09:00",
                            category="MILESTONE", project_id="P", role="COO",
                            summary=summary, evidence=(),
                            filter_result=HistoryDecision.KEEP,
                        )
                    ],
                    "g",
                )
            )
            measured[summary] = (len(document.items), document.unconsolidated)

        self.assertEqual(
            measured,
            {
                "Shipped it.": (1, 0),
                "Event ID: L1": (1, 0),
                "- Event ID: L1": (1, 1),
                "- Owner: COO": (1, 0),
            },
        )

    def test_an_item_block_with_no_summary_bullet_is_dropped_and_counted(self):
        """The remaining `summary is None` drop, which nothing executed.

        Found by tracing which `src/` lines the suite runs: `_first_bullet()`'s
        `return None` and the `if summary is None: continue` that consumes it
        were never reached by any test. C31 §1 narrowed *why* that branch
        fires — a label-shaped summary no longer triggers it — but the branch
        itself remained, untested, and it is a silent drop.

        Reachable by hand edit (docs/06 §57): an item block whose prose bullet
        was deleted while its `- Owner:` / `- Event ID:` lines stayed.

        Both halves are asserted: the item really is dropped (today's
        behaviour, unchanged), and the loss is now **counted** rather than
        silent. If the drop is ever fixed, the first assertion flips and this
        record is forced up to date.
        """
        body = (
            "# T\n\n## Milestones\n\n### P\n\n- Owner: COO\n- Event ID: EVT-NOSUM\n"
        )

        document = self._daily(body)

        self.assertEqual(document.items, ())
        self.assertEqual(document.unconsolidated, 1)

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


class RendererToParserRoundTripTests(unittest.TestCase):
    """Everything `daily/markdown.py` can write must come back.

    C31 §1 found the Daily→Monthly seam losing items to a shape heuristic.
    This is the property test that whole class of defect fails: render a
    maximal Daily — all four categories, mixed project-id casings, every
    optional Decision Context field, evidence, and a late item — then parse
    it and require every Event back with its category, project, owner and
    summary intact.

    A unit test per drop reason can only cover the reasons someone thought
    of. This covers the seam.
    """

    def _candidate(self, event_id, category, project_id, summary, *, full=False):
        return HistoryCandidate(
            history_id=f"HIST-{event_id}", event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00", category=category,
            project_id=project_id, role="CTO_BACKEND", summary=summary,
            evidence=("pytest PASS", "tsc PASS"),
            filter_result=HistoryDecision.KEEP,
            decision_context="budget review" if full else None,
            expected_outcome="20% lower spend" if full else None,
            actual_outcome="18% lower spend" if full else None,
            lessons_learned="measure earlier" if full else None,
        )

    def _rendered(self):
        from daily.late_events import append_late_events
        from daily.markdown import render_daily_markdown

        candidates = [
            self._candidate("EVT-D", "DECISION", "SEARCH_BACKEND",
                            "Adopted the runbook.", full=True),
            self._candidate("EVT-M", "MILESTONE", "content_os", "Closed beta shipped."),
            self._candidate("EVT-I", "ISSUE", "PAYMENTS", "Fixed: token refresh loop."),
            self._candidate("EVT-L", "LEARNING", "growth", "Note: users misread status."),
        ]
        text = render_daily_markdown(
            date(2026, 8, 5), candidates, "2026-08-06T11:00:00+09:00"
        )
        return append_late_events(
            text,
            (self._candidate("EVT-LATE", "MILESTONE", "ops", "Resolved: duplicate charge."),),
            now_iso="2026-08-07T11:00:00+09:00",
        )

    def test_every_rendered_event_survives_the_parse(self):
        document = parse_daily_markdown(self._rendered(), target_date=date(2026, 8, 5))

        self.assertEqual(
            sorted(i.event_id for i in document.items),
            ["EVT-D", "EVT-I", "EVT-L", "EVT-LATE", "EVT-M"],
        )

    def test_each_one_keeps_its_category_project_owner_and_summary(self):
        by_id = {i.event_id: i for i in parse_daily_markdown(
            self._rendered(), target_date=date(2026, 8, 5)
        ).items}

        expected = {
            "EVT-D": ("DECISION", "Search Backend", "Adopted the runbook."),
            "EVT-M": ("MILESTONE", "Content Os", "Closed beta shipped."),
            "EVT-I": ("ISSUE", "Payments", "Fixed: token refresh loop."),
            "EVT-L": ("LEARNING", "Growth", "Note: users misread status."),
            "EVT-LATE": ("MILESTONE", "Ops", "Resolved: duplicate charge."),
        }
        for event_id, (category, project, summary) in expected.items():
            with self.subTest(event_id=event_id):
                item = by_id[event_id]
                self.assertEqual(item.category, category)
                self.assertEqual(item.project, project)
                self.assertEqual(item.summary, summary)
                self.assertEqual(item.owner, "CTO Backend")

    def test_a_fully_understood_document_reports_nothing_unconsolidated(self):
        """The detector's own false-alarm guard, on the maximal document."""
        self.assertEqual(
            parse_daily_markdown(self._rendered(), target_date=date(2026, 8, 5)).unconsolidated,
            0,
        )


class ProjectIdBreaksTheMonthlyParseTests(unittest.TestCase):
    """NEW, **P0**. One Event's `project_id` drops its innocent siblings.

    CHARACTERIZATION of the loss plus a regression test for the detection.

    BUG-11/27 names `summary` and `evidence` as the fields rendered into
    Daily Markdown without escaping; C30 §4 added `event_id`. **`project_id`
    has never been named**, and it is the worst of the four: the other three
    corrupt their own item, this one can delete other Events.

    `daily/markdown._render_item_block()` writes it as a `### ` heading via
    `_display_project_name()`, and `validate_event()` constrains `project_id`
    only to "present and non-null" — measured, a value containing a newline
    is ACCEPTED by the schema, so it crosses the transport from another
    Desktop.

    Blast radius, measured with three ordinary Events where only the first
    has a crafted `project_id`:

        ordinary                     3/3 survive
        newline only                 3/3 survive
        `\\n\\n- INJECTED`             3/3 survive, EVT-1 summary hijacked
        `\\n\\n### Second Block`       3/3 survive, EVT-1 summary hijacked
        `\\n\\n## Metadata`            **0/3 survive**

    The last row is the finding. A `## ` heading closes the category section,
    so every item block after it — including the two innocent Events — falls
    outside every consolidatable section and vanishes from Monthly History.
    `consolidate_month()` returned MONTHLY_GENERATED.

    `.title()` in `_display_project_name()` accidentally blunts part of it:
    it lowercases `Event ID:` to `Event Id:`, so a *ghost item* cannot be
    forged this way. That is luck, not a defence, and it is written down here
    so nobody mistakes it for one.

    NOT FIXED — escaping the renderer is docs/06's contract (BUG-11/27) and
    constraining `project_id` is docs/02's (A-15). What needed no decision is
    that the loss is now counted (`DailyDocument.unconsolidated`).
    """

    def _document(self, project_id):
        from daily.markdown import render_daily_markdown

        candidates = [
            HistoryCandidate(
                history_id=f"H{i}", event_id=f"EVT-{i}",
                timestamp="2026-08-05T10:00:00+09:00", category="MILESTONE",
                project_id=pid, role="COO", summary=f"work {i}", evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
            for i, pid in enumerate((project_id, "P2", "P3"), start=1)
        ]
        return parse_daily_markdown(
            render_daily_markdown(date(2026, 8, 5), candidates, "g"),
            target_date=date(2026, 8, 5),
        )

    def test_the_schema_accepts_a_project_id_with_a_newline(self):
        """Reachability. If this starts failing, A-15/docs/02 was decided."""
        from events import validate_event

        self.assertEqual(
            validate_event({
                "schema_version": "1.0", "event_id": "E",
                "timestamp": "2026-08-05T10:00:00+09:00", "source": "DESKTOP_1",
                "role": "CTO_BACKEND", "project_id": "P\n## Decisions",
                "event_type": "MILESTONE_COMPLETED", "status": "IN_PROGRESS",
                "summary": "s", "history_candidate": True,
            }),
            [],
        )

    def test_a_heading_in_one_project_id_drops_every_sibling(self):
        document = self._document("P\n\n## Metadata\n\n- x")

        self.assertEqual([i.event_id for i in document.items], [])

    def test_the_loss_is_counted_rather_than_silent(self):
        """The half that needed no decision."""
        document = self._document("P\n\n## Metadata\n\n- x")

        self.assertEqual(document.unconsolidated, 3)

    def test_the_lesser_shapes_corrupt_only_their_own_item(self):
        for project_id, expected_summary in (
            ("P\n\n- INJECTED SUMMARY", "Injected Summary"),
            ("P\n\n### Second Block\n\n- other", "Other"),
        ):
            with self.subTest(project_id=project_id):
                document = self._document(project_id)

                self.assertEqual(len(document.items), 3)
                first = next(i for i in document.items if i.event_id == "EVT-1")
                self.assertEqual(first.summary, expected_summary)

    def test_an_ordinary_project_id_is_untouched(self):
        document = self._document("PAYMENTS")

        self.assertEqual(len(document.items), 3)
        self.assertEqual(document.unconsolidated, 0)
        self.assertEqual(
            next(i for i in document.items if i.event_id == "EVT-1").summary, "work 1"
        )

    def test_a_ghost_item_cannot_be_forged_through_this_field(self):
        """`.title()` lowercases `Event ID:` to `Event Id:`, which the
        parser's label regex does not match. Pinned because it is the one
        thing keeping this from being a phantom-Event vector, and it is an
        accident of a display helper rather than a guard."""
        document = self._document(
            "P\n\n### Ghost\n\n- forged\n- Owner: COO\n- Event ID: EVT-GHOST"
        )

        self.assertNotIn("EVT-GHOST", [i.event_id for i in document.items])


class UnconsolidatedReachesTheOperatorTests(MonthlyTestCase):
    """The last link: the count has to arrive somewhere a person reads.

    A number on a result object that nobody writes down is BUG-39's exact
    shape, and this sprint added the number — so it owes the sink. AGENT.md
    §6a already sends an operator to `daily_late_update.log` for "돌긴 돌았는데
    뭔가 안 됐다", which is precisely this situation, and Monthly failures
    already go there. No new artifact, no new format.
    """

    def _month_with_a_broken_project_id(self):
        import calendar

        self.repo.save(
            candidate(
                "EVT-BROKEN", day=date(2026, 8, 5), category="MILESTONE",
                project="P\n\n## Metadata\n\n- x",
            )
        )
        self.repo.save(candidate("EVT-INNOCENT", day=date(2026, 8, 5), category="MILESTONE"))
        _, last = calendar.monthrange(2026, 8)
        for day_number in range(1, last + 1):
            generate_daily_history(
                self.repo, date(2026, 8, day_number),
                output_dir=self.daily_dir, generated_at="g",
            )

    def test_the_consolidation_carries_the_count_out(self):
        self._month_with_a_broken_project_id()

        result = self.consolidate(2026, 8)

        self.assertIs(result.status, MonthlyStatus.MONTHLY_GENERATED)
        self.assertEqual(result.item_count, 0)
        self.assertEqual(result.unconsolidated_days, ("2026-08-05: 2",))

    def test_a_healthy_month_carries_nothing(self):
        self.fill_month(2026, 8, days_with_work=(5, 12))

        result = self.consolidate(2026, 8)

        self.assertEqual(result.unconsolidated_days, ())

    def test_the_runner_writes_it_to_the_log_operators_are_told_to_read(self):
        import app.runner as runner_module

        self._month_with_a_broken_project_id()
        log_path = self.root / "daily_late_update.log"

        for month_result in run_once(
            daily_dir=self.daily_dir,
            monthly_dir=self.monthly_dir,
            history_start_date=START,
            now=datetime(2026, 9, 2, 11, 0).astimezone(),
            state_path=self.state_path,
        ).results:
            if month_result.unconsolidated_days:
                runner_module._log_late_update(
                    log_path,
                    f"MONTHLY_UNCONSOLIDATED {month_result.key} "
                    f"{', '.join(month_result.unconsolidated_days)}",
                )

        written = log_path.read_text(encoding="utf-8")
        self.assertIn("MONTHLY_UNCONSOLIDATED 2026-08", written)
        self.assertIn("2026-08-05: 2", written)

    def test_the_runner_really_contains_that_call(self):
        """The half the test above cannot prove by construction."""
        import inspect

        import app.runner as runner_module

        source = inspect.getsource(runner_module.run_once)

        self.assertIn("month_result.unconsolidated_days", source)
        self.assertIn("MONTHLY_UNCONSOLIDATED", source)


class LabelShapedSummaryEndToEndTests(MonthlyTestCase):
    """The parser defect, exercised through the real pipeline rather than a
    hand-written document.

    Repository -> `generate_daily_history()` -> Daily Markdown ->
    `run_once()` -> Monthly Markdown. Nothing is hand-edited and nothing is
    crafted: the only unusual thing about the Event is that its summary
    begins `Fixed: `, which is how a real engineer writes one.

    Before the fix the Daily file was correct and complete, the run reported
    `MONTHLY_GENERATED`, and the Event was simply not in the Monthly. The
    Daily said `Event Count: 2` and the Monthly said `Consolidated Items: 1`
    two files apart, and nothing compared them.
    """

    LABEL_SHAPED = "Fixed: login token refresh loop."
    ORDINARY = "Adopted the new deploy runbook."

    def _run_the_month(self):
        self.repo.save(
            candidate(
                "EVT-LABELSHAPED",
                day=date(2026, 8, 5),
                category="ISSUE",
                summary=self.LABEL_SHAPED,
            )
        )
        self.repo.save(
            candidate(
                "EVT-ORDINARY",
                day=date(2026, 8, 6),
                category="DECISION",
                summary=self.ORDINARY,
            )
        )
        self.fill_month(2026, 8)
        return self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

    def test_the_daily_file_carries_it_either_way(self):
        """Isolates where the loss was. Daily was never the problem."""
        self._run_the_month()

        daily = (self.daily_dir / "2026-08-05.md").read_text(encoding="utf-8")

        self.assertIn(self.LABEL_SHAPED, daily)
        self.assertIn("- Event ID: EVT-LABELSHAPED", daily)

    def test_it_reaches_the_monthly_history(self):
        result = self._run_the_month()

        self.assertEqual(result.generated, ("2026-08",))
        text = self.monthly_text(2026, 8)

        self.assertIn("EVT-LABELSHAPED", text)
        self.assertIn(self.LABEL_SHAPED, text)
        self.assertIn("## Major Issues & Resolutions", text)

    def test_the_consolidated_count_matches_what_was_written(self):
        """The half of the damage an operator could conceivably have seen —
        and only by opening two files and counting."""
        self._run_the_month()
        text = self.monthly_text(2026, 8)

        self.assertIn("- Consolidated Items: 2", text)
        self.assertEqual(text.count("- Event ID: "), 2)

    def test_a_late_arriving_one_reaches_it_too(self):
        """The `## Late Events` path renders through the same item template,
        so it lost the same items — and a late Event is the case with no
        second chance at all."""
        self.fill_month(2026, 8)
        self.repo.save(
            candidate(
                "EVT-LATE-LABELSHAPED",
                day=date(2026, 8, 5),
                category="MILESTONE",
                summary="Resolved: the duplicate charge on retry.",
            )
        )
        update_daily_history(
            self.repo,
            date(2026, 8, 5),
            output_dir=self.daily_dir,
            now=datetime(2026, 8, 7, 11, 0).astimezone(),
        )

        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        text = self.monthly_text(2026, 8)

        self.assertIn("EVT-LATE-LABELSHAPED", text)
        self.assertIn("Resolved: the duplicate charge on retry.", text)


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

        # 2026-07 predates `history_start_date` (2026-08-01), so no run can
        # consolidate it: it reports PENDING and the flag survives for a
        # human to resolve.
        #
        # This used to be true for the wrong reason. The dirty loop had no
        # §85-86 guard — `pending_months()` has one, the dirty loop took its
        # months straight from the state file — and coverage reports
        # "complete" for a month with zero required days, so 2026-07 was
        # actually GENERATED: a Monthly file for a month Company History does
        # not cover. The flag survived only because clearing it was itself
        # missing for the GENERATED case, and the assertion below could not
        # tell the two apart. Both are now checked.
        pre_history = next(r for r in result.results if r.key == "2026-07")
        self.assertEqual(pre_history.status, MonthlyStatus.MONTHLY_PENDING)
        self.assertIn("predates", pre_history.error)
        self.assertFalse((self.monthly_dir / "2026-07.md").exists())
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


class StateLagsTheMonthlyFileTests(MonthlyTestCase):
    """A Monthly file exists for a month the state says is still open.

    `run_once()` writes the Monthly file and *then* saves state, so a run
    that dies between the two leaves exactly that. The recovery for it was
    already designed — the catch-up sees the file, reports
    MONTHLY_UNCHANGED, and advances the pointer rather than rebuilding
    forever — and it is correct as far as it goes.

    What it did not survive was a Late Event arriving in that window. Two
    separate steps each consulted the stale pointer and each drew a
    defensible conclusion:

        mark_month_dirty()   "2026-08 > last close (none) -> not consolidated
                              yet, the catch-up will read the new Daily" -> no
                              DIRTY flag
        the catch-up         "the file is already there" -> MONTHLY_UNCHANGED,
                              pointer advanced, DIRTY cleared

    Between them the month closed with a Monthly that does not contain a
    Late Event its own Daily does — permanently, and with every state field
    reporting a healthy closed month. README RULE 7 is what that breaks.

    Both halves are fixed here: the file's existence now counts as
    "consolidated" (docs/10 §49, History over State), and MONTHLY_UNCHANGED
    no longer clears a DIRTY flag it did not act on.
    """

    def _august_with_one_event(self):
        self.repo.save(candidate("EVT-ONTIME", day=date(2026, 8, 5), summary="on-time work"))
        import calendar

        _, last = calendar.monthrange(2026, 8)
        for n in range(1, last + 1):
            generate_daily_history(
                self.repo,
                date(2026, 8, n),
                output_dir=self.daily_dir,
                generated_at=f"2026-08-{n:02d}T11:00:00+09:00",
            )

    def _crash_after_writing_the_monthly(self):
        """Consolidate without going through `run_once()`, so no state is
        saved — the on-disk shape a run killed mid-step leaves behind."""
        result = self.consolidate(2026, 8, now=datetime(2026, 9, 1, 11, 0).astimezone())
        self.assertEqual(result.status, MonthlyStatus.MONTHLY_GENERATED)
        self.assertFalse(self.state_path.exists())

    def _late_event(self):
        self.repo.save(candidate("EVT-LATE", day=date(2026, 8, 5), summary="late work"))
        outcome = update_daily_history(
            self.repo,
            date(2026, 8, 5),
            output_dir=self.daily_dir,
            now=datetime(2026, 9, 2, 11, 0).astimezone(),
        )
        self.assertEqual(outcome.added_event_ids, ("EVT-LATE",))

    def test_a_late_event_reaches_monthly_even_when_state_lags_the_file(self):
        self._august_with_one_event()
        self._crash_after_writing_the_monthly()
        self._late_event()

        self.assertTrue(
            mark_month_dirty(self.state_path, date(2026, 8, 5), monthly_dir=self.monthly_dir)
        )
        self.run_monthly(now=datetime(2026, 9, 2, 11, 30).astimezone())

        self.assertIn("late work", self.monthly_text(2026, 8))
        self.assertIn(
            "late work", (self.daily_dir / "2026-08-05.md").read_text(encoding="utf-8")
        )

    def test_the_month_still_closes_and_the_flag_is_cleared(self):
        """Repair must not turn into a rebuild every run."""
        self._august_with_one_event()
        self._crash_after_writing_the_monthly()
        self._late_event()
        mark_month_dirty(self.state_path, date(2026, 8, 5), monthly_dir=self.monthly_dir)

        first = self.run_monthly(now=datetime(2026, 9, 2, 11, 30).astimezone())
        self.assertEqual(first.last_successful_monthly_close, "2026-08")
        self.assertEqual(load_state(self.state_path).dirty_months, [])

        second = self.run_monthly(now=datetime(2026, 9, 3, 11, 30).astimezone())
        self.assertEqual(second.results, ())

    def test_an_existing_monthly_file_counts_as_consolidated(self):
        self._august_with_one_event()
        self._crash_after_writing_the_monthly()

        self.assertTrue(
            mark_month_dirty(self.state_path, date(2026, 8, 5), monthly_dir=self.monthly_dir)
        )
        self.assertEqual(load_state(self.state_path).dirty_months, ["2026-08"])

    def test_a_month_with_no_monthly_file_is_still_not_marked(self):
        """The optimisation this guard exists for must survive: a month that
        genuinely has not been consolidated needs no DIRTY flag, because the
        pending catch-up reads the updated Daily on its own."""
        self._august_with_one_event()

        self.assertFalse(
            mark_month_dirty(self.state_path, date(2026, 8, 5), monthly_dir=self.monthly_dir)
        )

    def test_without_monthly_dir_the_old_state_only_judgement_is_kept(self):
        """Callers that have no Monthly directory to consult keep the
        previous behaviour rather than getting a silent change."""
        self._august_with_one_event()
        self._crash_after_writing_the_monthly()

        self.assertFalse(mark_month_dirty(self.state_path, date(2026, 8, 5)))

    def test_unchanged_no_longer_clears_a_dirty_flag_it_did_not_act_on(self):
        """The second half, isolated: the catch-up may advance the pointer
        over a file it did not rebuild, but it may not declare that file
        clean."""
        self._august_with_one_event()
        self._crash_after_writing_the_monthly()
        self._late_event()
        mark_month_dirty(self.state_path, date(2026, 8, 5), monthly_dir=self.monthly_dir)

        result = self.run_monthly(now=datetime(2026, 9, 2, 11, 30).astimezone())

        statuses = [r.status for r in result.results]
        self.assertIn(MonthlyStatus.MONTHLY_UNCHANGED, statuses)
        self.assertIn(MonthlyStatus.MONTHLY_UPDATED, statuses)

    def test_a_generated_month_still_clears_its_dirty_flag(self):
        """The clear that was correct stays correct: a month built from
        scratch already contains everything Daily holds."""
        self._august_with_one_event()
        state = MonthlyState(last_successful_monthly_close="2026-08", dirty_months=["2026-08"])
        save_state(self.state_path, state)

        # Pointer says 2026-08 is closed but no file exists — the dirty loop
        # builds it, and the flag must not survive that.
        self.run_monthly(now=datetime(2026, 9, 2, 11, 30).astimezone())

        self.assertEqual(load_state(self.state_path).dirty_months, [])
        self.assertTrue((self.monthly_dir / "2026-08.md").exists())


class LabelNamedSummaryTests(unittest.TestCase):
    """A summary may legitimately open with one of the renderer's own label
    words. Four of the seven are domain-natural here.

    C31 §1 narrowed the shape test `^[A-Z][A-Za-z ]+:` to the exact label
    set, which fixed `Fixed: ` -- and left every *real* label name still
    losing its item. Measured, all seven dropped:

        Owner: ...   Event ID: ...   Category: ...   Decision Context: ...
        Expected Outcome: ...   Actual Outcome: ...   Lessons Learned: ...

    `Lessons Learned: ...` is how a LEARNING item's summary reads and
    `Decision Context: ...` is how a DECISION's does, so this is not an
    exotic input -- it is the vocabulary docs/05 gives these categories.

    The renderer's **order** settles it without guessing: it writes its
    labels once each, in `_ITEM_LABELS`' sequence, so a first bullet whose
    label sits later in that sequence than one below it -- or that repeats a
    label appearing below it -- is something the renderer never wrote, which
    leaves prose as the only explanation.
    """

    def _round_trip(self, summary, *, category="LEARNING"):
        from daily.markdown import render_daily_markdown

        item = HistoryCandidate(
            history_id="H", event_id="EVT-REAL",
            timestamp="2026-08-05T10:00:00+09:00", category=category,
            project_id="P", role="COO", summary=summary, evidence=(),
            filter_result=HistoryDecision.KEEP,
        )
        return parse_daily_markdown(
            render_daily_markdown(date(2026, 8, 5), [item], "g"),
            target_date=date(2026, 8, 5),
        )

    def test_every_label_name_survives_as_a_summary(self):
        from monthly.parser import _ITEM_LABELS

        for label in _ITEM_LABELS:
            with self.subTest(label=label):
                summary = label + ": measured this and acted on it."

                document = self._round_trip(summary)

                self.assertEqual(len(document.items), 1, label + " lost its item")
                self.assertEqual(document.items[0].summary, summary)

    def test_the_item_keeps_its_own_event_id(self):
        """The second half. `Event ID: ...` as a summary made that line the
        FIRST `- Event ID:` in the block, so the item was consolidated under
        an id of "measured it." -- and docs/09 §59 de-duplicates on it."""
        document = self._round_trip("Event ID: measured this and acted on it.")

        self.assertEqual(document.items[0].event_id, "EVT-REAL")

    def test_owner_is_not_hijacked_either(self):
        document = self._round_trip("Owner: measured this and acted on it.")

        self.assertEqual(document.items[0].owner, "COO")

    def test_a_reordered_hand_edit_still_finds_the_real_summary(self):
        """docs/06 §57. A label bullet moved above the summary must still be
        treated as a label -- the order rule must not swallow this case."""
        body = (
            "# T\n\n## Milestones\n\n### P\n\n"
            "- Owner: COO\n- the real summary\n- Event ID: EVT-H\n"
        )

        document = parse_daily_markdown(body, target_date=date(2026, 8, 5))

        self.assertEqual([i.event_id for i in document.items], ["EVT-H"])
        self.assertEqual(document.items[0].summary, "the real summary")

    def test_a_block_with_only_label_bullets_is_still_dropped(self):
        """The order rule must not invent a summary out of a label bullet."""
        body = "# T\n\n## Milestones\n\n### P\n\n- Owner: COO\n- Event ID: EVT-N\n"

        document = parse_daily_markdown(body, target_date=date(2026, 8, 5))

        self.assertEqual(document.items, ())
        self.assertEqual(document.unconsolidated, 1)

    def test_the_label_sequence_is_the_renderers_own(self):
        """The order is load-bearing now, not just the set. Extracted from
        `_render_item_block()`'s source so the two cannot drift."""
        import inspect
        import re

        from daily import markdown as daily_markdown
        from monthly.parser import _ITEM_LABELS

        written = re.findall(
            r'f"- ([A-Za-z ]+): \{',
            inspect.getsource(daily_markdown._render_item_block),
        )

        self.assertEqual(written, list(_ITEM_LABELS))

    def test_the_two_readers_of_this_format_agree(self):
        """`daily/markdown.py` and `monthly/parser.py` both have to answer
        "which bullet is the summary", and they cannot share the code:
        `monthly` is a declared leaf (test_architecture_invariants
        `ALLOWED["monthly"] == set()`) so that consolidation cannot reach
        past the Daily text into `history`/`events` — docs/09 §12-13.

        So they are two implementations of one rule, and this is what keeps
        them together. Behaviour, not source: the tuple equality above
        catches a renamed label, this catches a rule that drifts.
        """
        from daily.markdown import ITEM_LABELS, label_position
        from monthly.parser import _ITEM_LABELS, _label_position

        self.assertEqual(ITEM_LABELS, _ITEM_LABELS)

        cases = ["", "a", "- x", "## x", "Fixed: y", "owner: x", "Owner", "Owner:x"]
        cases += [label + suffix for label in ITEM_LABELS for suffix in (":", ": v", ":\tv", "s: v", ": ")]

        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(label_position(text), _label_position(text))

    def test_both_readers_pick_the_same_summary_bullet(self):
        """The rule itself, not just its label lookup — `_first_bullet()`
        against `summary_line_indices()` over every block arrangement the
        order/duplicate rule distinguishes."""
        from daily.markdown import summary_line_indices
        from monthly.parser import _first_bullet

        blocks = (
            ["- plain", "- Owner: COO", "- Event ID: E1"],
            ["- Owner: COO", "- plain", "- Event ID: E1"],
            ["- Lessons Learned: x", "- Owner: COO", "- Event ID: E1"],
            ["- Owner: COO", "- Owner: COO", "- Event ID: E1"],
            ["- Owner: COO", "- Event ID: E1"],
            ["- Event ID: E1", "- Owner: COO"],
            ["- Event ID: E1"],
            # The sole-identifier override, both sides of it.
            ["- Event ID: E1", "- Owner: COO", "- the summary"],
            ["- Event ID: E1", "- Event ID: E2", "- the summary"],
            ["- Event ID: prose", "- Owner: COO", "- Event ID: E1"],
            ["- Owner: prose", "- Owner: COO", "- Event ID: E1"],
            ["- Lessons Learned: prose", "- Owner: COO", "- Event ID: E1"],
            [],
        )
        for block in blocks:
            with self.subTest(block=block):
                lines = ["# T", "", "## Milestones", "", "### P", ""] + block

                index, _text = _first_bullet(lines, 6, len(lines))
                mine = summary_line_indices(lines)

                self.assertEqual(mine, set() if index is None else {index})


class CoverageCanBeTrimmedAtTheBackTests(unittest.TestCase):
    """`check_coverage(today=...)` — declared, documented, and passed by
    nobody.

    Found by an AST sweep of every keyword-only parameter in `src/` against
    every call site in `src/`, the root entrypoints and the whole test suite
    (C43). 275 parameters, 17 that no production caller passes, and **two
    that nothing anywhere passes**. This is one of them; the other is
    `agent.status.needs_attention(stale_after_days=)`.

    That matters here more than "unused code" usually does, because this
    parameter trims the set of days a month is **expected** to have — the set
    `consolidate_month()` compares against what is on disk to decide whether
    a month is complete enough to consolidate at all (docs/09 §10/§39). A
    caller who started passing it could make an incomplete month look
    complete, and no test would have noticed.

    Not removed: deleting a documented capability is a decision, and this
    repository already keeps one such case on record rather than taking it
    (`notion.dashboard_pending.remove_pending()`, BACKLOG B-7). Exercised
    instead, so the capability is known to work and so the first real caller
    inherits a test rather than a surprise.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.daily = self.root / "daily"
        self.daily.mkdir()

    def _write(self, *days):
        for day in days:
            (self.daily / f"{day}.md").write_text("# d\n", encoding="utf-8")

    def test_without_it_a_month_in_progress_is_incomplete(self):
        """The behaviour every caller gets today."""
        self._write("2026-08-01", "2026-08-02")

        coverage = check_coverage(self.daily, 2026, 8)

        self.assertFalse(coverage.is_complete)
        self.assertEqual(len(coverage.missing_dates), 29)

    def test_with_it_the_same_month_reads_complete(self):
        """What the parameter does, stated as an assertion rather than as a
        docstring sentence."""
        self._write("2026-08-01", "2026-08-02")

        coverage = check_coverage(self.daily, 2026, 8, today=date(2026, 8, 2))

        self.assertTrue(coverage.is_complete)
        self.assertEqual(coverage.missing_dates, ())
        self.assertEqual(len(coverage.present_dates), 2)

    def test_it_still_reports_a_hole_inside_the_trimmed_range(self):
        """Trimming the back must not trim the middle — a gap before `today`
        is still a gap, which is the property that makes the parameter safe
        at all."""
        self._write("2026-08-01", "2026-08-03")

        coverage = check_coverage(self.daily, 2026, 8, today=date(2026, 8, 3))

        self.assertFalse(coverage.is_complete)
        self.assertEqual([d.isoformat() for d in coverage.missing_dates], ["2026-08-02"])

    def test_it_composes_with_the_front_trim(self):
        """Both ends at once, since `history_start_date` is the one every
        production caller does pass."""
        self._write("2026-08-05", "2026-08-06")

        coverage = check_coverage(
            self.daily, 2026, 8,
            history_start_date=date(2026, 8, 5), today=date(2026, 8, 6),
        )

        self.assertTrue(coverage.is_complete)
        self.assertTrue(coverage.starts_mid_month)

    def test_no_production_caller_passes_it(self):
        """The premise, from the source. If this starts failing, a caller
        appeared and the tests above stopped being characterization —
        BACKLOG must then say who calls it and why."""
        import ast

        repo = Path(__file__).resolve().parents[1]
        sources = [p for p in (repo / "src").rglob("*.py") if "__pycache__" not in str(p)]
        sources += [
            repo / name
            for name in ("ops_status.py", "run_company_ops.py", "run_agent.py")
        ]

        callers = []
        for path in sources:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and any(
                    keyword.arg == "today" for keyword in node.keywords
                ):
                    callers.append(str(path.relative_to(repo)))

        self.assertEqual(callers, [])

    def test_a_month_in_progress_is_kept_out_by_a_different_rule(self):
        """And why the dead parameter is not a hole: what stops the current
        month being consolidated is `pending_months()`'s calendar arithmetic
        (docs/09 §49), not this trim."""
        from monthly.generator import pending_months

        months = pending_months(
            last_successful_monthly_close=None,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 8, 20, 12, 0),
        )

        self.assertEqual(months, [])


class RendererParserFuzzTests(unittest.TestCase):
    """Seeded fuzz over renderer -> parser. The enumerated cases above cover
    the shapes someone thought of; this looks for the ones nobody did.

    Two populations, two different promises:

        benign       what the renderer produces from ordinary Events --
                     must round-trip EXACTLY. Zero loss, not "counted loss".
        adversarial  summaries and project_ids carrying newlines and
                     headings (BUG-11/27, C31 §16 -- an open docs/06
                     decision). Loss is expected; going UNCOUNTED is not.

    Seeded, so it is the same documents on every machine and every day -- a
    fuzz whose corpus moves is the time bomb this sprint removed from
    `ArrivalVersusWorkDateTests`.

    The benign figure is what made the label-name defect visible: it sat at
    641 losing documents in 4,000 while every enumerated test passed.
    """

    SEED = 20260814
    CATEGORIES = ("DECISION", "MILESTONE", "ISSUE", "LEARNING")
    BENIGN_SUMMARIES = (
        "Shipped it.", "Fixed: login token refresh loop.", "Note: paused.",
        "한글 요약입니다.", "A" * 300, "a", "- leading dash", "## prose hash",
        "tabs\tinside", "  spaced  ", "Resolved #1234 — duplicate charge.",
        "- Event ID: E0", "- Owner: COO",
        "Owner: measured it.", "Event ID: measured it.", "Category: measured it.",
        "Decision Context: measured it.", "Expected Outcome: measured it.",
        "Actual Outcome: measured it.", "Lessons Learned: measured it.",
    )
    BENIGN_PROJECTS = ("SEARCH_BACKEND", "content_os", "a", "한글프로젝트", "P-1")
    ADVERSARIAL_SUMMARIES = (
        "x\n\n## Metadata\n\n- y", "x\n\n### Block\n\n- z", "x\n- INJECTED",
        "x\n- Event ID: FORGED", "x\r\n## Notes", "x\n",
    )
    ADVERSARIAL_PROJECTS = (
        "P\n\n## Metadata\n\n- x", "P\n\n### Other\n\n- y", "P\rQ",
    )

    def _documents(self, summaries, projects, trials):
        import random

        from daily.markdown import render_daily_markdown

        rng = random.Random(self.SEED)
        for _ in range(trials):
            candidates = [
                HistoryCandidate(
                    history_id="H%d" % i, event_id="E%d" % i,
                    timestamp="2026-08-05T%02d:00:00+09:00" % rng.randint(0, 23),
                    category=rng.choice(self.CATEGORIES),
                    project_id=rng.choice(projects), role="COO",
                    summary=rng.choice(summaries), evidence=(),
                    filter_result=HistoryDecision.KEEP,
                )
                for i in range(rng.randint(1, 5))
            ]
            document = parse_daily_markdown(
                render_daily_markdown(date(2026, 8, 5), candidates, "g"),
                target_date=date(2026, 8, 5),
            )
            missing = {c.event_id for c in candidates} - {
                i.event_id for i in document.items
            }
            yield candidates, document, missing

    def test_benign_documents_round_trip_exactly(self):
        losing = [
            sorted(missing)
            for _c, _d, missing in self._documents(
                self.BENIGN_SUMMARIES, self.BENIGN_PROJECTS, 2000
            )
            if missing
        ]

        self.assertEqual(
            losing[:5], [], "%d of 2000 benign documents lost items" % len(losing)
        )

    def test_adversarial_loss_is_never_uncounted(self):
        silent = [
            (sorted(missing), document.unconsolidated)
            for _c, document, missing in self._documents(
                self.BENIGN_SUMMARIES + self.ADVERSARIAL_SUMMARIES,
                self.BENIGN_PROJECTS + self.ADVERSARIAL_PROJECTS,
                2000,
            )
            if missing and document.unconsolidated < len(missing)
        ]

        self.assertEqual(
            silent[:5], [], "%d documents lost items silently" % len(silent)
        )

    def test_the_adversarial_population_really_does_lose_items(self):
        """Otherwise the test above passes by testing nothing."""
        losing = sum(
            1
            for _c, _d, missing in self._documents(
                self.BENIGN_SUMMARIES + self.ADVERSARIAL_SUMMARIES,
                self.BENIGN_PROJECTS + self.ADVERSARIAL_PROJECTS,
                2000,
            )
            if missing
        )

        self.assertGreater(
            losing, 100, "the adversarial corpus stopped being adversarial"
        )


class ForgedGeneratedAtTests(MonthlyTestCase):
    """§58 pairs `Generated At` (when the month was first closed) with
    `Last Updated At` (when a Late Event changed it), and a rebuild carries
    the former forward by reading it back out of the old file.

    `render_monthly_markdown()` writes an item's summary raw as its block's
    first bullet, so a Daily summary of `Generated At: 1999-...` renders a
    line indistinguishable from the field -- and above it, since Metadata is
    the last block. `_existing_generated_at()` returned the first match.

    Measured before the fix, one item with that summary:

        - Generated At: 1999-01-01T00:00:00+09:00   <- the summary
        - Generated At: 2026-09-01T02:00:00+09:00   <- the field
        _existing_generated_at() -> '1999-01-01T00:00:00+09:00'

    The next ordinary dirty rebuild then writes the forged value in as the
    month's real `Generated At`, and it stays: deleting the offending Event
    does not bring the original back, because by then the Metadata block
    itself holds the forgery. No corruption and no hand edit needed -- any
    Event author can write that summary.
    """

    FORGED = "1999-01-01T00:00:00+09:00"

    def _month_with_summary(self, summary):
        self.repo.save(
            candidate("EVT-F", day=date(2026, 8, 5), summary=summary)
        )
        self.fill_month(2026, 8, days_with_work=())
        return self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

    def test_a_summary_cannot_forge_the_months_generated_at(self):
        from monthly.generator import _existing_generated_at

        self._month_with_summary("Generated At: " + self.FORGED)

        read_back = _existing_generated_at(self.monthly_dir / "2026-08.md")

        self.assertNotEqual(read_back, self.FORGED)
        self.assertIn("- Generated At: %s" % read_back, self.monthly_text(2026, 8))

    def test_the_forgery_does_not_survive_a_rebuild(self):
        """The end of the path, through the ordinary §58 dirty rebuild that
        makes the forgery permanent."""
        self._month_with_summary("Generated At: " + self.FORGED)
        # `[-1]`, not `next()`: the forged summary line is rendered ABOVE the
        # Metadata block, so the first match in the file is the forgery. That
        # is the whole defect, and it catches the test too.
        original = [
            line
            for line in self.monthly_text(2026, 8).splitlines()
            if line.startswith("- Generated At: ")
        ][-1]
        self.assertNotIn(self.FORGED, original)

        self.repo.save(candidate("EVT-LATE", day=date(2026, 8, 20)))
        update_daily_history(
            self.repo,
            date(2026, 8, 20),
            output_dir=self.daily_dir,
            now=datetime(2026, 9, 3, 15, 0).astimezone(),
        )
        mark_month_dirty(self.state_path, date(2026, 8, 20))
        self.run_monthly(now=datetime(2026, 9, 3, 16, 0).astimezone())

        text = self.monthly_text(2026, 8)
        self.assertEqual(
            [l for l in text.splitlines() if l.startswith("- Generated At: ")][-1],
            original,
        )
        self.assertNotIn("- Generated At: %s\n" % self.FORGED, text.split("## Metadata")[-1])
        self.assertIn("- Last Updated At: ", text)

    def test_the_pipeline_cannot_deliver_a_multi_line_summary_here(self):
        """Scoping the fix honestly. BUG-11/27 is about a summary rendered
        unescaped, and the obvious worry is a summary carrying a literal
        `## Metadata` heading -- but it cannot arrive by this path.
        `parse_daily_markdown()` is line-based, so the Daily->Monthly seam
        keeps only the first line of one. Measured, that summary end to end:

            items                []      (dropped, BUG-11/27)
            unconsolidated       1       (counted, C31 §16)
            a newline reaching a MonthlyItem.summary: False

        So a second Metadata block inside a Monthly file means a hand-edited
        Monthly file, which is what the test below covers.
        """
        from daily.markdown import render_daily_markdown

        candidates = [
            candidate(
                "EVT-N",
                day=date(2026, 8, 5),
                summary="x\n\n## Metadata\n\n- Generated At: %s" % self.FORGED,
            )
        ]

        document = parse_daily_markdown(
            render_daily_markdown(date(2026, 8, 5), candidates, "gen"),
            target_date=date(2026, 8, 5),
        )

        self.assertEqual([i.summary for i in document.items], [])
        self.assertEqual(document.unconsolidated, 1)

    def test_the_last_metadata_block_wins(self):
        """A hand-edited Monthly (docs/06 §57's Monthly equivalent) carrying
        a second Metadata block. The real one is always last —
        `render_monthly_markdown()` appends it last on both of its paths — so
        preferring the last block is decidable here with no escaping
        decision, which is BUG-11/27's to make and not this module's.
        """
        from monthly.generator import _existing_generated_at

        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        path = self.monthly_dir / "2026-08.md"
        body = path.read_text(encoding="utf-8")
        real = [l for l in body.splitlines() if l.startswith("- Generated At: ")][-1]
        path.write_text(
            "# T\n\n## Metadata\n\n- Generated At: %s\n\n%s" % (self.FORGED, body),
            encoding="utf-8",
        )

        self.assertEqual("- Generated At: %s" % _existing_generated_at(path), real)

    def test_an_ordinary_month_still_carries_its_generated_at_forward(self):
        """The fix narrows the read; §58 must still work."""
        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        original = next(
            line
            for line in self.monthly_text(2026, 8).splitlines()
            if line.startswith("- Generated At: ")
        )

        self.repo.save(candidate("EVT-LATE", day=date(2026, 8, 20)))
        update_daily_history(
            self.repo,
            date(2026, 8, 20),
            output_dir=self.daily_dir,
            now=datetime(2026, 9, 3, 15, 0).astimezone(),
        )
        mark_month_dirty(self.state_path, date(2026, 8, 20))
        self.run_monthly(now=datetime(2026, 9, 3, 16, 0).astimezone())

        self.assertIn(original, self.monthly_text(2026, 8))

    def test_the_metadata_title_is_the_renderers_own(self):
        """Reader and writer share one constant so they cannot drift."""
        from monthly.markdown import METADATA_TITLE

        self.fill_month(2026, 8, days_with_work=(5,))
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        self.assertIn("\n%s\n" % METADATA_TITLE, self.monthly_text(2026, 8))


class ReorderedLabelIsStillALabelTests(unittest.TestCase):
    """The blind spot the label-order rule created, found by asking what
    else could explain an arrangement the renderer cannot produce.

    The rule reasoned: the renderer writes its labels once each, in order,
    after the summary -- so a first bullet whose label sits later in that
    sequence than one below it is not something the renderer wrote, which
    leaves prose as the only explanation. The last step is wrong. docs/06
    section 57 permits a hand edit, and a hand edit can move a label bullet
    above the summary; that produces the identical arrangement.

    Measured, `- Event ID: EVT-H` moved above `- Owner:`:

        existing_event_ids()        set()       <- the block's id, gone
        select_late_candidates()    ['EVT-H']   <- re-added, EVERY run
        Monthly                     dropped

    An unbounded duplicate in a Company History file: the same defect the
    empty-`event_id` fix closed (section 38's guard reading back what the
    renderer wrote), arriving by a different door. Before the label-order
    rule existed this reader found the id, so this was a regression that
    rule introduced.

    The repair is one override, and it is decidable rather than heuristic:
    **an exclusion must never leave a block with no identifier.** If the
    bullet about to be called prose carries the block's only `Event ID:`, it
    is the label, because nothing else in the block can be. When a second
    `Event ID:` bullet exists below, the first really is prose -- that is
    the case the order rule was written for, and it still holds.
    """

    def _document(self, block):
        return "# T\n\n## Milestones\n\n### P\n\n" + "\n".join(block) + "\n"

    def _candidate(self):
        return HistoryCandidate(
            history_id="HIST-EVT-H", event_id="EVT-H",
            timestamp="2026-08-05T10:00:00+09:00", category="MILESTONE",
            project_id="P", role="COO", summary="late", evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def test_a_reordered_event_id_is_not_read_as_prose(self):
        from daily.late_events import existing_event_ids

        body = self._document(["- Event ID: EVT-H", "- Owner: COO", "- the summary"])

        self.assertEqual(existing_event_ids(body), {"EVT-H"})

    def test_the_late_event_is_not_re_added(self):
        """The consequence, which is what makes this worth a test: an
        unbounded duplicate, once per run that revisits the date."""
        from daily.late_events import select_late_candidates

        for block in (
            ["- Event ID: EVT-H", "- Owner: COO", "- the summary"],
            ["- Event ID: EVT-H", "- Owner: COO"],
            ["- Event ID: EVT-H"],
        ):
            with self.subTest(block=block):
                body = self._document(block)

                self.assertEqual(
                    select_late_candidates(body, [self._candidate()]), ()
                )

    def test_the_whole_item_is_recovered_not_merely_the_id(self):
        """Strictly better than before, not merely less wrong: this block
        used to lose all three fields."""
        document = parse_daily_markdown(
            self._document(["- Event ID: EVT-H", "- Owner: COO", "- the summary"]),
            target_date=date(2026, 8, 5),
        )

        self.assertEqual(
            [(i.event_id, i.owner, i.summary) for i in document.items],
            [("EVT-H", "COO", "the summary")],
        )
        self.assertEqual(document.unconsolidated, 0)

    def test_a_second_event_id_below_still_means_the_first_is_prose(self):
        """The override must not undo the defect the order rule fixed."""
        document = parse_daily_markdown(
            self._document(
                ["- Event ID: measured it.", "- Owner: COO", "- Event ID: EVT-H"]
            ),
            target_date=date(2026, 8, 5),
        )

        self.assertEqual(
            [(i.event_id, i.summary) for i in document.items],
            [("EVT-H", "Event ID: measured it.")],
        )

    def test_a_prose_summary_is_not_counted_as_a_lost_item(self):
        """`unconsolidated` counts `- Event ID:` lines against items, and a
        summary reading `Event ID: …` is such a line. Reporting a loss on a
        document that consolidated everything it had is the cry-wolf
        direction, on the one counter that exists to be believed."""
        document = parse_daily_markdown(
            self._document(
                ["- Event ID: measured it.", "- Owner: COO", "- Event ID: EVT-H"]
            ),
            target_date=date(2026, 8, 5),
        )

        self.assertEqual(len(document.items), 1)
        self.assertEqual(document.unconsolidated, 0)

    def test_the_counter_still_reports_the_losses_it_is_for(self):
        """Both directions. The three shapes it must keep catching."""
        for label, block, expected in (
            ("no summary bullet", ["- Event ID: EVT-H", "- Owner: COO"], 1),
            ("one id twice", ["- s", "- Event ID: EVT-A", "- Event ID: EVT-B"], 1),
            ("renderer output", ["- s", "- Owner: COO", "- Event ID: EVT-H"], 0),
        ):
            with self.subTest(label=label):
                document = parse_daily_markdown(
                    self._document(block), target_date=date(2026, 8, 5)
                )

                self.assertEqual(document.unconsolidated, expected)

    def test_a_line_outside_every_section_is_still_counted(self):
        """The counter scans the whole document on purpose -- a section that
        ended early puts its lines outside the walk. Excluding prose
        summaries must not narrow that."""
        body = (
            "# T\n\n## Milestones\n\n### P\n\n- s1\n- Event ID: E1\n"
            "\n## Metadata\n\n- Event ID: E3\n"
        )

        document = parse_daily_markdown(body, target_date=date(2026, 8, 5))

        self.assertEqual([i.event_id for i in document.items], ["E1"])
        self.assertEqual(document.unconsolidated, 1)


class MonthlyShortfallHasTwoCausesTests(MonthlyTestCase):
    """The shortfall check knows two numbers disagree. It used to tell the
    operator *why*, with certainty, and prescribe one action.

    The old sentence named the unrecognised-category cause and closed with
    "다시 만들어도 같은 결과가 나온다" -- re-generating gives the same
    result. Both halves are wrong for the other cause. docs/06 §57 and
    docs/11 §71 let the COO edit official History by hand, and a deleted
    item block produces the identical discrepancy. Measured, three items
    rendered, one block deleted by hand:

        as generated       ()
        block deleted      ('2026-08', 3, 2)
        plain re-run       month already closed, statuses [] -- stays deleted
        forced rebuild     3 items, EVT-1 back

    So for that cause a rebuild **does** fix it, and "check the Category
    lines" sends the operator hunting through a month of Daily files for a
    bad value that is not there.

    This pins what the two causes actually do, so the sentence cannot drift
    back to asserting one of them.
    """

    def _monthly_path(self):
        return self.monthly_dir / "2026-08.md"

    def _three_item_month(self):
        for index in range(3):
            self.repo.save(
                candidate(
                    f"EVT-{index}",
                    day=date(2026, 8, 5),
                    hour=10 + index,
                    category="DECISION",
                )
            )
        self.fill_month(2026, 8, days_with_work=())
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())
        return self._monthly_path().read_text(encoding="utf-8")

    def _shortfall(self):
        from ops_status import _monthly_counts_more_than_it_shows

        return _monthly_counts_more_than_it_shows(self.monthly_dir)

    def test_a_generated_month_is_quiet(self):
        self._three_item_month()

        self.assertEqual(self._shortfall(), ())

    def test_a_hand_deleted_block_is_reported(self):
        original = self._three_item_month()
        edited = original.replace(
            "- Owner: CTO Backend\n- Event ID: EVT-1\n- Source: 2026-08-05.md\n\n", ""
        )
        self.assertNotEqual(edited, original, "the fixture stopped matching")
        self._monthly_path().write_text(edited, encoding="utf-8")

        self.assertEqual(self._shortfall(), (("2026-08", 3, 2),))

    def test_a_plain_re_run_does_not_repair_it(self):
        """The month is already closed and not dirty, so nothing revisits
        it -- which is why the discrepancy persists at all."""
        original = self._three_item_month()
        self._monthly_path().write_text(
            original.replace(
                "- Owner: CTO Backend\n- Event ID: EVT-1\n- Source: 2026-08-05.md\n\n",
                "",
            ),
            encoding="utf-8",
        )

        result = self.run_monthly(now=datetime(2026, 9, 2, 11, 0).astimezone())

        self.assertEqual([r.status for r in result.results], [])
        self.assertEqual(self._shortfall(), (("2026-08", 3, 2),))

    def test_a_forced_rebuild_does_repair_it(self):
        """The half the old sentence denied."""
        original = self._three_item_month()
        self._monthly_path().write_text(
            original.replace(
                "- Owner: CTO Backend\n- Event ID: EVT-1\n- Source: 2026-08-05.md\n\n",
                "",
            ),
            encoding="utf-8",
        )
        mark_month_dirty(self.state_path, date(2026, 8, 5))

        self.run_monthly(now=datetime(2026, 9, 3, 11, 0).astimezone())

        self.assertIn("EVT-1", self._monthly_path().read_text(encoding="utf-8"))
        self.assertEqual(self._shortfall(), ())

    def test_the_unrecognised_category_cause_survives_a_rebuild(self):
        """The other half, and the reason the two need different actions.
        A hand-typed `- Category:` outside the four is in the Daily file, so
        rebuilding reproduces the drop exactly."""
        self.repo.save(candidate("EVT-K", day=date(2026, 8, 5), category="DECISION"))
        self.fill_month(2026, 8, days_with_work=())
        daily = self.daily_dir / "2026-08-05.md"
        # A `## Late Events` item states its own category on a bullet, and
        # docs/06 §57 lets a human type it. `Decision` is not `DECISION`, so
        # the parser passes it through verbatim and the Monthly renderer
        # files it under no section while `len(items)` still counts it.
        edited = (
            daily.read_text(encoding="utf-8")
            .replace("## Decisions", "## Late Events")
            .replace(
                "- Event ID: EVT-K",
                "- Event ID: EVT-K\n- Category: Decision",
            )
        )
        self.assertIn("- Category: Decision", edited)
        daily.write_text(edited, encoding="utf-8")
        self.run_monthly(now=datetime(2026, 9, 1, 11, 0).astimezone())

        before = self._shortfall()
        self.assertEqual(
            before, (("2026-08", 1, 0),), "the fixture stopped reproducing the cause"
        )

        mark_month_dirty(self.state_path, date(2026, 8, 5))
        self.run_monthly(now=datetime(2026, 9, 3, 11, 0).astimezone())

        self.assertEqual(self._shortfall(), before)

    def test_the_message_names_both_causes(self):
        """The sentence is the deliverable here, so it is asserted."""
        import importlib.util
        import inspect

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_two_causes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = inspect.getsource(module._print_history)

        self.assertIn("다시 만들어도 같은 결과", source)
        self.assertIn("복구된다", source)
        self.assertIn("둘 중 어느 쪽인지는", source)


if __name__ == "__main__":
    unittest.main()
