"""Late Event update tests (docs/06_DAILY_HISTORY_SPEC.md §36-40, §57, §65).

Closes audit finding BUG-17 (P0): an Event whose date was already closed
reached keep/ and Notion but never Company History, while every indicator
reported success.

Each of the spec's five steps gets its own coverage here:

    §37  기존 파일 존재 확인 / 기존 내용 보호 / 중복 확인 / 추가 / Metadata
    §38  동일 event_id는 다시 추가하지 않는다
    §39  Last Updated At + Late Events Added
    §40  조용한 덮어쓰기 금지
    §57  Manual Edit 보존
    §41  실패 시 기존 History 유지, 다음 실행 재시도
"""

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import (  # noqa: E402
    LATE_SECTION_TITLE,
    LateUpdateOutcome,
    append_late_events,
    existing_event_ids,
    generate_daily_history,
    select_late_candidates,
    update_daily_history,
)
from history import HistoryCandidate, HistoryDecision  # noqa: E402
from history.file_repository import FileHistoryRepository  # noqa: E402

DAY = date(2026, 8, 1)
NOW = datetime(2026, 8, 7, 12, 15).astimezone()


def candidate(event_id, *, summary=None, hour=10, category="MILESTONE", project="PRJ_A"):
    return HistoryCandidate(
        history_id=f"HIST-{event_id}",
        event_id=event_id,
        timestamp=f"{DAY.isoformat()}T{hour:02d}:00:00+09:00",
        category=category,
        project_id=project,
        role="CTO_BACKEND",
        summary=summary or f"work {event_id}",
        evidence=(),
        filter_result=HistoryDecision.KEEP,
    )


class LateUpdateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.daily_dir = self.root / "daily"
        self.repo = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )

    def close_day(self, *candidates):
        for item in candidates:
            self.repo.save(item)
        generate_daily_history(
            self.repo, DAY, output_dir=self.daily_dir, generated_at="2026-08-02T11:00:00+09:00"
        )
        return (self.daily_dir / f"{DAY.isoformat()}.md")

    def update(self, *late, now=NOW):
        for item in late:
            self.repo.save(item)
        return update_daily_history(self.repo, DAY, output_dir=self.daily_dir, now=now)


class EventIdParsingTests(unittest.TestCase):
    def test_item_block_event_ids_are_found(self):
        markdown = "### P\n\n- did a thing\n- Owner: COO\n- Event ID: EVT-1\n"
        self.assertEqual(existing_event_ids(markdown), {"EVT-1"})

    def test_evidence_lines_are_not_mistaken_for_item_blocks(self):
        """Evidence renders as `- <event_id>: <text>`, which must not be read
        as the Event being present in its own right."""
        markdown = "## Evidence\n\n- EVT-9: pytest PASS\n"
        self.assertEqual(existing_event_ids(markdown), set())

    def test_an_empty_document_has_no_event_ids(self):
        self.assertEqual(existing_event_ids(""), set())


class EmptyEventIdIsStillAnEventIdTests(LateUpdateTestCase):
    """REGRESSION. §38's duplicate guard could not read what §18's renderer
    wrote, and the result was unbounded growth of a Company History file.

    `validate_event()` rejects only a *missing* `event_id`
    (`data.get(field) is None`), so `""` is a valid Event today. Whether it
    should be is docs/02's decision (BACKLOG A-15) and is not touched here.

    `daily/markdown.py` renders it as `- Event ID: ` -- the line exists, the
    value is empty. `existing_event_ids()` matched `(\\S.*)`, which requires
    at least one non-space character, so it saw no id there. Measured, one
    such Candidate on an already-closed date, three ordinary runs:

        run 1  UPDATED_LATE_EVENT ('',)
        run 2  UPDATED_LATE_EVENT ('',)
        run 3  UPDATED_LATE_EVENT ('',)
        -> 3 identical `## Late Events` blocks for ONE Candidate
        -> `- Late Events Added: 3` beside `- Event Count: 1`

    Rendered every time, recognised none of the times. `total_events` is
    computed from the same function, so the file's own two Metadata numbers
    contradicted each other inside it.
    """

    def test_an_empty_event_id_is_read_back_from_the_rendered_line(self):
        self.assertEqual(existing_event_ids("- Event ID: \n"), {""})
        self.assertEqual(existing_event_ids("- Event ID:\n"), {""})

    def test_an_ordinary_id_is_unaffected(self):
        markdown = "### P\n\n- did a thing\n- Owner: COO\n- Event ID: EVT-1\n"
        self.assertEqual(existing_event_ids(markdown), {"EVT-1"})

    def test_evidence_lines_are_still_not_mistaken_for_item_blocks(self):
        """The loosened group must not loosen what the line has to start
        with — Evidence renders as `- <event_id>: <text>`."""
        self.assertEqual(existing_event_ids("## Evidence\n\n- EVT-9: pytest PASS\n"), set())

    def _three_runs(self):
        """One stored Candidate, three runs that revisit the date. Saving it
        once is the point — the duplication came from the *file*, not from
        the Repository holding three records."""
        self.close_day(candidate("EVT-OK"))
        self.repo.save(candidate(""))
        return [
            update_daily_history(
                self.repo,
                DAY,
                output_dir=self.daily_dir,
                now=datetime(2026, 8, 3 + offset, 11, 0).astimezone(),
            )
            for offset in range(3)
        ]

    def test_it_is_added_once_and_not_again(self):
        outcomes = self._three_runs()

        self.assertEqual(
            [o.outcome for o in outcomes],
            [
                LateUpdateOutcome.UPDATED_LATE_EVENT,
                LateUpdateOutcome.NO_LATE_EVENTS,
                LateUpdateOutcome.NO_LATE_EVENTS,
            ],
        )

    def test_the_file_carries_one_block_and_a_matching_count(self):
        self._three_runs()

        text = (self.daily_dir / f"{DAY.isoformat()}.md").read_text(encoding="utf-8")

        self.assertEqual(text.count("- Event ID:"), 2)
        self.assertIn("- Late Events Added: 1", text)
        self.assertIn("- Event Count: 2", text)


class SelectionTests(unittest.TestCase):
    def test_an_event_already_in_the_file_is_not_selected(self):
        markdown = "- Event ID: EVT-1\n"
        selected = select_late_candidates(markdown, [candidate("EVT-1"), candidate("EVT-2")])
        self.assertEqual([c.event_id for c in selected], ["EVT-2"])

    def test_duplicates_within_the_input_are_collapsed(self):
        selected = select_late_candidates("", [candidate("EVT-1"), candidate("EVT-1")])
        self.assertEqual([c.event_id for c in selected], ["EVT-1"])

    def test_selection_is_ordered_by_timestamp(self):
        selected = select_late_candidates(
            "", [candidate("EVT-LATE", hour=18), candidate("EVT-EARLY", hour=9)]
        )
        self.assertEqual([c.event_id for c in selected], ["EVT-EARLY", "EVT-LATE"])


class SpecSequenceTests(LateUpdateTestCase):
    def test_a_late_event_is_added_with_updated_metadata(self):
        path = self.close_day(candidate("EVT-1", summary="on-time work"))
        before = path.read_text(encoding="utf-8")

        result = self.update(candidate("EVT-2", summary="late work", hour=18))

        self.assertEqual(result.outcome, LateUpdateOutcome.UPDATED_LATE_EVENT)
        self.assertEqual(result.added_event_ids, ("EVT-2",))

        after = path.read_text(encoding="utf-8")
        self.assertIn("late work", after)
        self.assertIn(LATE_SECTION_TITLE, after)
        self.assertIn("- Last Updated At: " + NOW.isoformat(timespec="seconds"), after)
        self.assertIn("- Late Events Added: 1", after)
        self.assertIn("- Event Count: 2", after)
        # §40: Generated At records when the day was first closed and is
        # never overwritten.
        self.assertIn("- Generated At: 2026-08-02T11:00:00+09:00", after)
        self.assertNotEqual(after, before)

    def test_the_original_content_survives_verbatim(self):
        """§37 기존 내용 보호: every line that was there before is still
        there, in the same order."""
        path = self.close_day(
            candidate("EVT-1", summary="first"), candidate("EVT-2", summary="second", hour=12)
        )
        before_lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("- Event Count:")
        ]

        self.update(candidate("EVT-3", summary="late", hour=18))

        after_lines = path.read_text(encoding="utf-8").splitlines()
        # Original lines appear, in order, as a subsequence of the new file.
        iterator = iter(after_lines)
        for line in before_lines:
            self.assertTrue(
                any(line == candidate_line for candidate_line in iterator),
                f"original line vanished: {line!r}",
            )

    def test_the_same_event_is_never_added_twice(self):
        """§38."""
        path = self.close_day(candidate("EVT-1"))
        self.update(candidate("EVT-2", hour=18))
        once = path.read_text(encoding="utf-8")

        second = self.update()

        self.assertEqual(second.outcome, LateUpdateOutcome.NO_LATE_EVENTS)
        self.assertEqual(path.read_text(encoding="utf-8"), once)
        # Count the Event ID line, not the bare id — the default summary
        # here also contains it.
        self.assertEqual(once.count("- Event ID: EVT-2"), 1)

    def test_nothing_is_written_when_there_are_no_late_events(self):
        path = self.close_day(candidate("EVT-1"))
        before = path.read_text(encoding="utf-8")
        mtime_before = path.stat().st_mtime_ns

        result = self.update()

        self.assertEqual(result.outcome, LateUpdateOutcome.NO_LATE_EVENTS)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime_before)

    def test_a_missing_file_is_left_to_the_generator(self):
        result = update_daily_history(self.repo, DAY, output_dir=self.daily_dir, now=NOW)

        self.assertEqual(result.outcome, LateUpdateOutcome.NO_EXISTING_FILE)
        self.assertFalse((self.daily_dir / f"{DAY.isoformat()}.md").exists())

    def test_a_second_late_update_extends_the_same_section(self):
        path = self.close_day(candidate("EVT-1"))
        self.update(candidate("EVT-2", hour=15))
        self.update(candidate("EVT-3", hour=18), now=datetime(2026, 8, 9, 9, 0).astimezone())

        after = path.read_text(encoding="utf-8")
        self.assertEqual(after.count(LATE_SECTION_TITLE), 1)
        self.assertIn("EVT-2", after)
        self.assertIn("EVT-3", after)
        # §39's counter accumulates rather than reporting only the last batch.
        self.assertIn("- Late Events Added: 2", after)
        self.assertIn("- Event Count: 3", after)
        self.assertIn("- Last Updated At: 2026-08-09T09:00:00", after)

    def test_only_this_date_s_candidates_are_considered(self):
        path = self.close_day(candidate("EVT-1"))
        other_day = HistoryCandidate(
            history_id="HIST-OTHER",
            event_id="EVT-OTHER",
            timestamp="2026-08-05T10:00:00+09:00",
            category="MILESTONE",
            project_id="PRJ_A",
            role="COO",
            summary="different day entirely",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

        result = self.update(other_day)

        self.assertEqual(result.outcome, LateUpdateOutcome.NO_LATE_EVENTS)
        self.assertNotIn("different day entirely", path.read_text(encoding="utf-8"))

    def test_an_empty_day_can_receive_its_first_late_event(self):
        """docs/06 §25's Empty Day is still a closed day: a Desktop that was
        offline all week produces exactly this shape."""
        path = self.close_day()
        self.assertIn("No material company history recorded.", path.read_text(encoding="utf-8"))

        result = self.update(candidate("EVT-LATE", summary="turned out there was work"))

        self.assertEqual(result.outcome, LateUpdateOutcome.UPDATED_LATE_EVENT)
        after = path.read_text(encoding="utf-8")
        self.assertIn("turned out there was work", after)
        self.assertIn("- Event Count: 1", after)


class ManualEditPreservationTests(LateUpdateTestCase):
    """docs/06 §57 / docs/11 §71: the COO may edit official History by hand,
    and a Late Event update must not be an excuse to discard those edits.
    This is the reason the file is appended to rather than re-rendered from
    the Repository — a re-render would be simpler and would silently undo
    every line below."""

    def test_a_hand_written_paragraph_survives(self):
        path = self.close_day(candidate("EVT-1", summary="machine-recorded"))
        edited = path.read_text(encoding="utf-8").replace(
            "## Metadata",
            "## COO Note\n\n비고: 이 마일스톤은 CEO 승인 이후로 미뤄졌다.\n\n## Metadata",
        )
        path.write_text(edited, encoding="utf-8")

        self.update(candidate("EVT-2", summary="late", hour=18))

        after = path.read_text(encoding="utf-8")
        self.assertIn("## COO Note", after)
        self.assertIn("비고: 이 마일스톤은 CEO 승인 이후로 미뤄졌다.", after)
        self.assertIn("late", after)

    def test_a_hand_corrected_summary_is_not_reverted(self):
        path = self.close_day(candidate("EVT-1", summary="typo hree"))
        path.write_text(
            path.read_text(encoding="utf-8").replace("typo hree", "typo here (corrected)"),
            encoding="utf-8",
        )

        self.update(candidate("EVT-2", hour=18))

        after = path.read_text(encoding="utf-8")
        self.assertIn("typo here (corrected)", after)
        self.assertNotIn("typo hree", after)

    def test_a_hand_removed_metadata_block_is_rebuilt_not_skipped(self):
        """§40 still applies: whatever the file looks like, the fact that it
        changed must be recorded somewhere."""
        path = self.close_day(candidate("EVT-1"))
        text = path.read_text(encoding="utf-8")
        path.write_text(text[: text.index("## Metadata")], encoding="utf-8")

        self.update(candidate("EVT-2", hour=18))

        after = path.read_text(encoding="utf-8")
        self.assertIn("## Metadata", after)
        self.assertIn("- Last Updated At: ", after)
        self.assertIn("- Late Events Added: 1", after)


class FailurePathTests(LateUpdateTestCase):
    """docs/06 §41: a History write failure keeps the existing History,
    deletes no candidate, and is retried next run."""

    def test_a_repository_failure_is_reported_and_changes_nothing(self):
        path = self.close_day(candidate("EVT-1"))
        before = path.read_text(encoding="utf-8")

        class ExplodingRepository:
            def list(self, decision=None):
                raise RuntimeError("repository unavailable")

            def save(self, *a, **kw):
                raise NotImplementedError

            def get(self, *a, **kw):
                raise NotImplementedError

        result = update_daily_history(
            ExplodingRepository(), DAY, output_dir=self.daily_dir, now=NOW
        )

        self.assertEqual(result.outcome, LateUpdateOutcome.FAILED)
        self.assertIn("repository unavailable", result.error)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_failure_is_retried_successfully_on_the_next_run(self):
        path = self.close_day(candidate("EVT-1"))
        self.repo.save(candidate("EVT-2", summary="late", hour=18))

        class ExplodingRepository:
            def list(self, decision=None):
                raise RuntimeError("transient")

            def save(self, *a, **kw):
                raise NotImplementedError

            def get(self, *a, **kw):
                raise NotImplementedError

        failed = update_daily_history(
            ExplodingRepository(), DAY, output_dir=self.daily_dir, now=NOW
        )
        self.assertEqual(failed.outcome, LateUpdateOutcome.FAILED)

        recovered = update_daily_history(
            self.repo, DAY, output_dir=self.daily_dir, now=NOW
        )

        self.assertEqual(recovered.outcome, LateUpdateOutcome.UPDATED_LATE_EVENT)
        self.assertIn("late", path.read_text(encoding="utf-8"))


class PureFunctionTests(unittest.TestCase):
    def test_appending_nothing_returns_the_input_unchanged(self):
        markdown = "# Title\n\n## Metadata\n\n- Event Count: 0\n"
        self.assertEqual(
            append_late_events(markdown, [], now_iso="2026-08-07T12:15:00+09:00"), markdown
        )

    def test_the_late_section_is_placed_before_metadata(self):
        markdown = "# Title\n\n## Metadata\n\n- Event Count: 0\n"
        updated = append_late_events(
            markdown, [candidate("EVT-1")], now_iso="2026-08-07T12:15:00+09:00"
        )
        self.assertLess(updated.index(LATE_SECTION_TITLE), updated.index("## Metadata"))

    def test_the_result_always_ends_with_exactly_one_newline(self):
        markdown = "# Title\n\n## Metadata\n\n- Event Count: 0\n"
        updated = append_late_events(
            markdown, [candidate("EVT-1")], now_iso="2026-08-07T12:15:00+09:00"
        )
        self.assertTrue(updated.endswith("\n"))
        self.assertFalse(updated.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()


class MetadataNotLastTests(LateUpdateTestCase):
    """`_metadata_bounds()` claims to handle a file whose `## Metadata` is not
    the last section. Nothing executed that claim.

    Its docstring says: *"`end` is exclusive and stops at the next `## `
    heading so a file whose Metadata is not last is still handled
    correctly."* Found by tracing which `src/` lines the suite runs — the
    loop body that implements "stop at the next heading" was never reached,
    so the sentence was an assertion about untested code.

    It is not a hypothetical arrangement. docs/06 §57 and docs/11 §71 let the
    COO edit a Daily by hand, and appending a note section after Metadata is
    the most natural way to do that.

    Behaviour is correct — measured, not assumed. Pinned so it stays that
    way, because getting it wrong would rewrite or truncate whatever a human
    put after the Metadata block.
    """

    HAND_EDITED = (
        "# DOJOONPASS Company History — 2026-08-01\n"
        "\n"
        "## Metadata\n"
        "\n"
        "- History Date: 2026-08-01\n"
        "- Generated At: 2026-08-02T11:00:00+09:00\n"
        "- Event Count: 0\n"
        "\n"
        "## COO Note\n"
        "\n"
        "이 아래는 사람이 직접 쓴 것이다.\n"
    )

    def test_the_metadata_block_ends_at_the_next_heading(self):
        from daily.late_events import _metadata_bounds

        lines = self.HAND_EDITED.splitlines()

        start, end = _metadata_bounds(lines)

        self.assertEqual(lines[start].strip(), "## Metadata")
        self.assertEqual(lines[end].strip(), "## COO Note")

    def test_the_late_section_is_inserted_before_metadata(self):
        updated = append_late_events(
            self.HAND_EDITED, (candidate("EVT-LATE"),), now_iso=NOW.isoformat()
        )

        self.assertLess(updated.index(LATE_SECTION_TITLE), updated.index("## Metadata"))

    def test_the_hand_written_trailing_section_is_untouched(self):
        """§57's whole point: a re-render would discard it."""
        updated = append_late_events(
            self.HAND_EDITED, (candidate("EVT-LATE"),), now_iso=NOW.isoformat()
        )

        self.assertIn("## COO Note", updated)
        self.assertIn("이 아래는 사람이 직접 쓴 것이다.", updated)

    def test_the_metadata_fields_are_updated_in_place_not_appended_after_it(self):
        updated = append_late_events(
            self.HAND_EDITED, (candidate("EVT-LATE"),), now_iso=NOW.isoformat()
        )
        lines = updated.splitlines()
        note_at = next(i for i, line in enumerate(lines) if line.strip() == "## COO Note")

        for field in ("- Last Updated At:", "- Late Events Added:", "- Event Count:"):
            with self.subTest(field=field):
                at = next(i for i, line in enumerate(lines) if line.startswith(field))
                self.assertLess(at, note_at, f"{field} landed after the hand-written section")


class SummaryShapedLikeALabelTests(unittest.TestCase):
    """§38's duplicate guard reads `- Event ID:` lines out of the rendered
    file -- and the renderer writes the summary raw, as the block's first
    bullet. A summary of `Event ID: EVT-999` is therefore byte-identical to
    the label bullet below it.

    Measured before the fix, one ordinary KEEP Candidate:

        existing_event_ids(day)              {'EVT-1', 'EVT-999'}
        select_late_candidates(day, EVT-999) ()        <- never appended
        append_late_events(...)              file unchanged
        `- Event Count: 3`                             <- two real Events

    A genuinely late EVT-999 is dropped on arrival and on every run that
    revisits the date, with no counter and no log line -- §38 believes it is
    doing its job. Nothing about the summary is crafted; `Event ID: EVT-999`
    is also just how someone writes a note to themselves. Seen from the
    security side it is Event ID spoofing: one Event can suppress a later
    one by naming it.

    Fixed in `daily/markdown.summary_line_indices()`, which is the renderer's
    own rule for which bullet is the summary.
    """

    def _closed_day(self, summary):
        from daily.markdown import render_daily_markdown

        return render_daily_markdown(DAY, [candidate("EVT-1", summary=summary)], "gen")

    def test_a_summary_naming_another_event_does_not_suppress_it(self):
        for summary in (
            "Event ID: EVT-999",
            "Event ID:  EVT-999 ",
            "Event ID:\tEVT-999",
        ):
            with self.subTest(summary=summary):
                day = self._closed_day(summary)

                selected = select_late_candidates(day, [candidate("EVT-999")])

                self.assertEqual([c.event_id for c in selected], ["EVT-999"])
                self.assertNotIn("EVT-999", existing_event_ids(day))

    def test_the_late_event_actually_reaches_the_file(self):
        day = self._closed_day("Event ID: EVT-999")

        updated = append_late_events(
            day, select_late_candidates(day, [candidate("EVT-999")]), now_iso="X"
        )

        self.assertNotEqual(updated, day)
        self.assertIn(LATE_SECTION_TITLE, updated)

    def test_event_count_is_not_inflated(self):
        """This half needs no exact match -- any summary opening
        `Event ID: ` used to add a phantom id to the set."""
        day = self._closed_day("Event ID: EVT-999 was the blocker.")

        updated = append_late_events(
            day, select_late_candidates(day, [candidate("EVT-2")]), now_iso="X"
        )

        self.assertIn("- Event Count: 2", updated)

    def test_a_late_item_can_carry_the_same_shape(self):
        """The `## Late Events` section's blocks also open with `### `, so
        the rule has to reach them too."""
        day = self._closed_day("Shipped it.")
        updated = append_late_events(
            day, [candidate("EVT-2", summary="Event ID: EVT-3")], now_iso="X"
        )

        self.assertEqual(existing_event_ids(updated), {"EVT-1", "EVT-2"})
        self.assertEqual(
            [c.event_id for c in select_late_candidates(updated, [candidate("EVT-3")])],
            ["EVT-3"],
        )

    def test_a_real_duplicate_is_still_suppressed(self):
        """The guard must still guard -- the fix narrows it, not disables
        it."""
        day = self._closed_day("Event ID: EVT-999")

        self.assertEqual(select_late_candidates(day, [candidate("EVT-1")]), ())

    def test_a_hand_edited_block_still_yields_its_event_id(self):
        """docs/06 §57. A label bullet moved above the summary is still a
        label; the block's real id must not go missing, or the item would be
        appended a second time."""
        markdown = (
            "# T\n\n## Milestones\n\n### P\n\n"
            "- Owner: CTO Backend\n- the real summary\n- Event ID: EVT-H\n"
        )

        self.assertEqual(existing_event_ids(markdown), {"EVT-H"})


class LateSeamFuzzTests(unittest.TestCase):
    """Seeded fuzz over render -> select -> append -> parse, the whole seam.

    Three properties, all of which the enumerated tests above check one
    example of:

        no real late Event is suppressed
        a second late update on the same file is a no-op   (§38)
        `- Event Count:` equals the number of real Events

    plus the Monthly parser recovering every Event from the result, since
    that is what the Daily file exists for.

    Pre-fix, over these same 1,000 documents: **98 suppressed late Events
    and 429 wrong Event Counts.** Seeded, so it is the same documents on
    every machine and every day.
    """

    SEED = 20260814
    SUMMARIES = (
        "Shipped it.", "Fixed: login token refresh loop.", "Note: paused.",
        "한글 요약입니다.", "Owner: measured it.", "Event ID: measured it.",
        "Category: measured it.", "Decision Context: measured it.",
        "Expected Outcome: measured it.", "Actual Outcome: measured it.",
        "Lessons Learned: measured it.", "Event ID: E3", "Event ID: L1",
        "Owner: CTO Backend", "- leading dash", "## prose hash",
    )
    PROJECTS = ("SEARCH_BACKEND", "content_os", "한글프로젝트")
    CATEGORIES = ("DECISION", "MILESTONE", "ISSUE", "LEARNING")
    TRIALS = 1000

    def _trials(self):
        import random

        from daily.markdown import render_daily_markdown

        rng = random.Random(self.SEED)
        for _ in range(self.TRIALS):
            def make(event_id):
                return candidate(
                    event_id,
                    summary=rng.choice(self.SUMMARIES),
                    hour=rng.randint(0, 23),
                    category=rng.choice(self.CATEGORIES),
                    project=rng.choice(self.PROJECTS),
                )

            closed = [make("E%d" % i) for i in range(rng.randint(1, 4))]
            late = [make("L%d" % i) for i in range(rng.randint(1, 3))]
            yield closed, late, render_daily_markdown(DAY, closed, "gen")

    def test_no_real_late_event_is_suppressed(self):
        suppressed = [
            sorted({c.event_id for c in late} - {c.event_id for c in selected})
            for _closed, late, day in self._trials()
            for selected in [select_late_candidates(day, late)]
            if {c.event_id for c in selected} != {c.event_id for c in late}
        ]

        self.assertEqual(
            suppressed[:5], [], "%d of %d documents suppressed a real late Event"
            % (len(suppressed), self.TRIALS)
        )

    def test_a_second_late_update_is_a_no_op_and_the_count_is_right(self):
        from monthly.parser import parse_daily_markdown

        repeated, miscounted, unparsed = [], [], []
        for closed, late, day in self._trials():
            updated = append_late_events(
                day, select_late_candidates(day, late), now_iso="X"
            )
            again = select_late_candidates(updated, late)
            if again:
                repeated.append(sorted(c.event_id for c in again))
            real = {c.event_id for c in closed} | {c.event_id for c in late}
            line = next(
                l for l in updated.splitlines() if l.startswith("- Event Count:")
            )
            if int(line.split(":")[1]) != len(real):
                miscounted.append((line, len(real)))
            document = parse_daily_markdown(updated, target_date=DAY)
            if {i.event_id for i in document.items} != real:
                unparsed.append(sorted(real - {i.event_id for i in document.items}))

        self.assertEqual(repeated[:5], [], "%d re-added a late Event" % len(repeated))
        self.assertEqual(miscounted[:5], [], "%d wrong Event Count" % len(miscounted))
        self.assertEqual(unparsed[:5], [], "%d lost before Monthly" % len(unparsed))
