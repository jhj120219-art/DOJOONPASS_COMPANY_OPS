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

# `-> str | None` below is a PEP 604 annotation, and a method signature is
# evaluated at `def` time. Without this import that is a `TypeError` on any
# interpreter older than 3.10 -- raised during *collection*, which aborts the
# whole run rather than failing one file (C50). Every other module in this
# tree already carries it; this one was the exception.
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# The repository root too — `ops_status.py` lives beside `src/`, and one
# test below reads a Daily file through the operator-facing detector
# rather than re-deriving what it would have said.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
        """The fixture is a whole item block, which is the only place the
        renderer writes a label at all — `item_block_bounds()` now says so
        structurally, so a bare line with no `### ` above it is prose and
        would not exercise this rule at all
        (`OnlyItemBlocksCarryLabelsTests`)."""
        for line in ("- Event ID: ", "- Event ID:"):
            with self.subTest(line=line):
                block = "## Milestones\n\n### P\n\n- did a thing\n" + line + "\n"

                self.assertEqual(existing_event_ids(block), {""})

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
        markdown = "## Milestones\n\n### P\n\n- did a thing\n- Event ID: EVT-1\n"
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


class AnEmptyDayStopsSayingItIsEmptyTests(LateUpdateTestCase):
    """C135, found by reading this repository's own Company History.

    `runtime/local_master/daily/2026-08-09.md`, as it stood on disk:

        # DOJOONPASS Company History — 2026-08-09

        No material company history recorded.

        ## Late Events
        … eight milestone items …

        - Event Count: 8

    An Empty Day (docs/06 §25) is a closed day that had no candidates — a
    Desktop offline all week produces exactly that shape, and
    `test_an_empty_day_can_receive_its_first_late_event` above already drives
    the case where its work arrives afterwards. What that test never asked is
    whether the day is still *described* as empty once it is not, so the
    sentence stayed while the items and `Event Count` went in around it.

    README RULE 2 makes Company History the record of last resort. A day with
    eight recorded milestones read as a day when nothing happened, directly
    above the eight and directly above its own count.

    **Nothing was lost, and the reason is worth keeping:** `monthly/parser`
    computes `is_empty_day = EMPTY_DAY_MARKER in text and not items`, so a
    stale marker beside real items never made consolidation skip the day.
    Verified on the file above — 8 items parsed, `is_empty_day` False. This
    is a repair to what a person reads.
    """

    SENTENCE = "No material company history recorded."

    def test_the_sentence_is_gone_once_the_day_has_an_item(self):
        path = self.close_day()
        self.assertIn(self.SENTENCE, path.read_text(encoding="utf-8"))

        self.update(candidate("EVT-LATE", summary="turned out there was work"))

        after = path.read_text(encoding="utf-8")
        self.assertNotIn(self.SENTENCE, after)
        self.assertIn("turned out there was work", after)
        self.assertIn("- Event Count: 1", after)

    def test_a_day_that_is_still_empty_keeps_the_sentence(self):
        """Precision. Removing it unconditionally would leave a genuinely
        quiet day describing itself as nothing at all, which is the opposite
        error and the one docs/06 §25 exists to prevent."""
        path = self.close_day()

        self.assertIn(self.SENTENCE, path.read_text(encoding="utf-8"))

    def test_a_second_late_update_does_not_reintroduce_it(self):
        path = self.close_day()
        self.update(candidate("EVT-A", summary="first late item"))
        self.update(candidate("EVT-B", summary="second late item"))

        after = path.read_text(encoding="utf-8")
        self.assertNotIn(self.SENTENCE, after)
        self.assertIn("first late item", after)
        self.assertIn("second late item", after)

    def test_the_document_still_reads_as_one_paragraph_break(self):
        """The sentence stood alone, so removing it must not leave the blank
        line it sat between doubled up."""
        path = self.close_day()
        self.update(candidate("EVT-LATE", summary="work"))

        after = path.read_text(encoding="utf-8")
        self.assertNotIn("\n\n\n", after)

    def test_monthly_still_reads_every_item_from_the_repaired_document(self):
        """The end the fix must not have moved. Consolidation was already
        safe against the stale marker; it must stay safe without it."""
        from monthly.parser import read_daily_document

        path = self.close_day()
        self.update(candidate("EVT-LATE", summary="turned out there was work"))

        document = read_daily_document(path, target_date=DAY)

        self.assertFalse(document.is_empty_day)
        self.assertEqual([item.event_id for item in document.items], ["EVT-LATE"])

    def test_a_hand_written_line_quoting_the_sentence_survives(self):
        """docs/06 §57 / docs/11 §71: the COO may edit official History by
        hand. Only the machine's own standalone line is removed — a sentence
        a person wrote that happens to contain those words keeps its text.
        """
        path = self.close_day()
        original = path.read_text(encoding="utf-8")
        path.write_text(
            original + f"\n\nCOO note: I checked and {self.SENTENCE} was wrong.\n",
            encoding="utf-8",
        )

        self.update(candidate("EVT-LATE", summary="work"))

        after = path.read_text(encoding="utf-8")
        self.assertIn(
            f"COO note: I checked and {self.SENTENCE} was wrong.", after
        )


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


class TrimmedMetadataFieldsAreRestoredTests(unittest.TestCase):
    """`_update_metadata()`'s docstring promises "a field that is missing (a
    hand-trimmed block) is inserted". It was true of two fields out of three.

    Found by branch coverage: of the six ways a Metadata block can be missing
    some subset of `Last Updated At` / `Late Events Added` / `Event Count`,
    the suite only ever ran two — all three present, and the first two both
    absent. The four mixed arrangements had never executed, and one of them
    was a defect rather than a gap.

    **`Event Count` was rewritten but never restored.** The loop that updates
    it simply `break`s when it finds the line and does nothing when it does
    not, and the insertion block below listed only the other two fields. The
    cost outlives the run: `ops_status._daily_counts_more_than_it_shows()`
    compares that number against the ids the file carries and *skips a file
    whose line is missing or unparseable*. So a Metadata block trimmed by
    hand — docs/06 §57 and docs/11 §71 both permit the edit, and dropping a
    machine-bookkeeping line is the most natural trim there is — silently
    switched off the only detector for three real losses (a `category=None`
    Candidate that reaches no Section, a forged `- Event ID:` line, a
    hand-deleted item block) for that day, for good.

    The no-block branch of the same function already writes all three when it
    builds a Metadata block from nothing, so the fix is the same field set
    reached the other way.
    """

    HEADER = (
        "# DOJOONPASS Company History — 2026-08-01\n"
        "\n"
        "## Milestones\n"
        "\n"
        "### PRJ_A\n"
        "\n"
        "- work EVT-1\n"
        "- Event ID: EVT-1\n"
        "\n"
        "## Metadata\n"
        "\n"
        "- History Date: 2026-08-01\n"
        "- Generated At: 2026-08-02T11:00:00+09:00\n"
        "- Source: DOJOONPASS Company Ops\n"
    )

    def _temp_dir(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _document(self, *fields: str) -> str:
        return self.HEADER + "".join(f"{line}\n" for line in fields)

    def _field(self, text: str, prefix: str) -> str | None:
        for line in text.splitlines():
            if line.strip().startswith(prefix):
                return line.strip()[len(prefix):].strip()
        return None

    def test_a_metadata_block_with_no_event_count_line_gets_one_back(self):
        markdown = self._document(
            "- Last Updated At: 2026-08-05T09:00:00+09:00",
            "- Late Events Added: 1",
        )

        updated = append_late_events(markdown, (candidate("EVT-LATE"),), now_iso=NOW.isoformat())

        self.assertEqual(self._field(updated, "- Event Count:"), "2")

    def test_the_restored_count_is_what_the_loss_detector_reads(self):
        """The line is not decoration — it is the detector's only input.

        `_daily_counts_more_than_it_shows()` reports `(date, claimed,
        carried)` and skips any file with no `- Event Count:` line. Before the
        fix this day was skipped forever; after it, the same day is compared
        again and reports clean, which is the state a healthy day should be
        in.
        """
        import ops_status

        markdown = self._document(
            "- Last Updated At: 2026-08-05T09:00:00+09:00",
            "- Late Events Added: 1",
        )
        daily_dir = self._temp_dir()
        path = daily_dir / f"{DAY.isoformat()}.md"

        path.write_text(markdown, encoding="utf-8")
        self.assertEqual(
            ops_status._daily_counts_more_than_it_shows(daily_dir),
            (),
            "a file with no count line is skipped, not reported — that is the blindness",
        )
        self.assertIsNone(self._field(markdown, "- Event Count:"))

        path.write_text(
            append_late_events(markdown, (candidate("EVT-LATE"),), now_iso=NOW.isoformat()),
            encoding="utf-8",
        )
        self.assertEqual(ops_status._daily_counts_more_than_it_shows(daily_dir), ())
        self.assertEqual(self._field(path.read_text(encoding="utf-8"), "- Event Count:"), "2")

    def test_the_restored_count_reports_a_real_loss_on_the_next_run(self):
        """And the point of restoring it: the detector can now see a loss.

        A hand-deleted item block leaves the count above what the file
        carries, which is docs/06 §57's third listed loss.
        """
        import ops_status

        markdown = self._document(
            "- Last Updated At: 2026-08-05T09:00:00+09:00",
            "- Late Events Added: 1",
        )
        updated = append_late_events(markdown, (candidate("EVT-LATE"),), now_iso=NOW.isoformat())
        # The operator deletes one item block by hand, leaving the count.
        without_item = updated.replace("- Event ID: EVT-1\n", "")

        daily_dir = self._temp_dir()
        (daily_dir / f"{DAY.isoformat()}.md").write_text(without_item, encoding="utf-8")

        self.assertEqual(
            ops_status._daily_counts_more_than_it_shows(daily_dir),
            ((DAY.isoformat(), 2, 1),),
        )

    def test_only_the_missing_last_updated_line_is_inserted(self):
        markdown = self._document(
            "- Late Events Added: 3",
            "- Event Count: 1",
        )

        updated = append_late_events(markdown, (candidate("EVT-LATE"),), now_iso=NOW.isoformat())

        self.assertEqual(self._field(updated, "- Last Updated At:"), NOW.isoformat())
        # accumulated, not replaced
        self.assertEqual(self._field(updated, "- Late Events Added:"), "4")
        self.assertEqual(self._field(updated, "- Event Count:"), "2")
        self.assertEqual(updated.count("- Late Events Added:"), 1)

    def test_only_the_missing_late_events_added_line_is_inserted(self):
        markdown = self._document(
            "- Last Updated At: 2026-08-05T09:00:00+09:00",
            "- Event Count: 1",
        )

        updated = append_late_events(markdown, (candidate("EVT-LATE"),), now_iso=NOW.isoformat())

        self.assertEqual(self._field(updated, "- Last Updated At:"), NOW.isoformat())
        self.assertEqual(self._field(updated, "- Late Events Added:"), "1")
        self.assertEqual(self._field(updated, "- Event Count:"), "2")
        self.assertEqual(updated.count("- Last Updated At:"), 1)

    def test_a_block_holding_all_three_is_updated_in_place_and_nothing_added(self):
        markdown = self._document(
            "- Last Updated At: 2026-08-05T09:00:00+09:00",
            "- Late Events Added: 2",
            "- Event Count: 1",
        )

        updated = append_late_events(markdown, (candidate("EVT-LATE"),), now_iso=NOW.isoformat())

        for prefix in ("- Last Updated At:", "- Late Events Added:", "- Event Count:"):
            with self.subTest(field=prefix):
                self.assertEqual(updated.count(prefix), 1)
        self.assertEqual(self._field(updated, "- Late Events Added:"), "3")
        self.assertEqual(self._field(updated, "- Event Count:"), "2")

    def test_every_inserted_field_lands_inside_the_metadata_block(self):
        """A hand-edited file may carry a section after Metadata; an inserted
        bookkeeping line must not cross into it."""
        markdown = self._document() + "\n## COO Note\n\n사람이 쓴 문단.\n"

        updated = append_late_events(markdown, (candidate("EVT-LATE"),), now_iso=NOW.isoformat())

        lines = updated.splitlines()
        note_at = next(i for i, line in enumerate(lines) if line.strip() == "## COO Note")
        for prefix in ("- Last Updated At:", "- Late Events Added:", "- Event Count:"):
            with self.subTest(field=prefix):
                at = next(i for i, line in enumerate(lines) if line.startswith(prefix))
                self.assertLess(at, note_at)
        self.assertIn("사람이 쓴 문단.", updated)


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


class OnlyItemBlocksCarryLabelsTests(unittest.TestCase):
    """REGRESSION. `SummaryShapedLikeALabelTests` closed the door inside the
    item block. `## Summary` is the same door one section up, and it was open.

    `render_daily_markdown()` writes the `## Summary` section by repeating
    every candidate's summary **raw** — no `- ` of its own, unlike the copy
    inside the item block, which is written as `- {summary}` and so reads
    `- - Event ID: …` for this input. So a summary that is itself a bullet
    lands in `## Summary` as a bare line indistinguishable from a label, and
    `summary_line_indices()` cannot reach it: that rule walks `### ` item
    blocks, and the Summary section has none.

    Measured, one ordinary KEEP Candidate whose summary was `- Event ID: L1`:

        ## Summary

        - Event ID: L1                    <- the summary, verbatim

        existing_event_ids(day)           {'E1', 'L1'}
        select_late_candidates(day, [L1]) ()      <- never appended
        append_late_events(...)           file unchanged

    A genuinely late L1 is dropped on arrival and on every run that revisits
    the date — §38's guard believing it is doing its job, with no counter and
    no log line. Nothing is hand-edited and nothing is crafted: this
    repository's own benign fuzz corpus already lists `- leading dash` as a
    realistic summary, and `Event ID: …` as another.

    Fixed by `daily/markdown.item_block_bounds()`: a label is only ever
    written inside a `### ` block, so only those lines are read as labels.
    """

    def _closed_day(self, summary):
        from daily.markdown import render_daily_markdown

        return render_daily_markdown(DAY, [candidate("EVT-1", summary=summary)], "gen")

    def test_a_bullet_summary_naming_another_event_does_not_suppress_it(self):
        for summary in (
            "- Event ID: EVT-999",
            "-  Event ID: EVT-999",
            "- Event ID:\tEVT-999",
        ):
            with self.subTest(summary=summary):
                day = self._closed_day(summary)

                self.assertNotIn("EVT-999", existing_event_ids(day))
                self.assertEqual(
                    [c.event_id for c in select_late_candidates(day, [candidate("EVT-999")])],
                    ["EVT-999"],
                )

    def test_the_suppressed_event_reaches_the_file_and_is_not_re_added(self):
        day = self._closed_day("- Event ID: EVT-999")

        updated = append_late_events(
            day, select_late_candidates(day, [candidate("EVT-999")]), now_iso="X"
        )

        self.assertIn(LATE_SECTION_TITLE, updated)
        self.assertIn("- Event Count: 2", updated)
        # The appended block carries its own label, inside a `### ` block, so
        # the guard sees it from now on — the loss is closed in one direction
        # without opening an unbounded duplicate in the other.
        self.assertEqual(select_late_candidates(updated, [candidate("EVT-999")]), ())

    def test_the_summary_section_is_where_it_came_from(self):
        """Names the mechanism, so a future change to the renderer that stops
        repeating summaries raw shows up here rather than silently making
        this class test nothing."""
        day = self._closed_day("- Event ID: EVT-999")
        lines = day.splitlines()
        start = lines.index("## Summary")

        self.assertEqual(lines[start + 2], "- Event ID: EVT-999")

    def test_an_evidence_line_can_no_longer_spell_a_label(self):
        """The Evidence section renders `- <event_id>: <text>`, which IS a
        label line when the id is literally `Event ID`. The docstring claimed
        Evidence lines "deliberately do not match"; only the block rule makes
        that structurally true rather than incidentally so."""
        from daily.markdown import render_daily_markdown

        spelling_a_label = candidate("Event ID", summary="Shipped it.")
        spelling_a_label = HistoryCandidate(
            **{**spelling_a_label.__dict__, "evidence": ("EVT-PHANTOM",)}
        )
        day = render_daily_markdown(DAY, [spelling_a_label], "gen")

        self.assertIn("- Event ID: EVT-PHANTOM", day)  # the Evidence line
        self.assertEqual(existing_event_ids(day), {"Event ID"})

    def test_hand_written_prose_outside_a_block_is_not_a_label(self):
        """docs/06 §57 permits a COO note anywhere. A note that mentions an
        Event must not silently become §38's record that the Event is here."""
        day = self._closed_day("Shipped it.")
        annotated = day.replace(
            "## Metadata",
            "## COO Notes\n\n- Event ID: EVT-777 was superseded.\n\n## Metadata",
        )

        self.assertEqual(existing_event_ids(annotated), {"EVT-1"})

    def test_the_cost_of_being_wrong_is_one_duplicate_and_then_it_stops(self):
        """The claim this narrowing rests on, measured instead of asserted.

        Confining the scan to `### ` blocks can miss a real label — a §57
        hand edit that deleted an item's `### <project>` heading leaves the
        block's `- Event ID:` outside every block. The Event is then appended
        again under `## Late Events`. That is the direction to be wrong in,
        and this is why: the appended block carries its OWN `### ` heading,
        so the guard sees it from the next run onwards. One duplicate, in a
        section named for it, and then it stops — against a phantom id, whose
        cost is an Event that never reaches Company History at all.
        """
        headless = (
            "# T\n\n## Milestones\n\n- the summary\n- Owner: COO\n- Event ID: EVT-H\n"
            "\n## Metadata\n\n- Event Count: 1\n"
        )

        self.assertEqual(existing_event_ids(headless), set())

        first = append_late_events(
            headless, select_late_candidates(headless, [candidate("EVT-H")]), now_iso="X"
        )

        self.assertIn(LATE_SECTION_TITLE, first)
        self.assertEqual(existing_event_ids(first), {"EVT-H"})

        # And it stops: the second pass finds nothing to add.
        self.assertEqual(select_late_candidates(first, [candidate("EVT-H")]), ())
        self.assertEqual(
            append_late_events(
                first, select_late_candidates(first, [candidate("EVT-H")]), now_iso="Y"
            ),
            first,
        )

    def test_a_real_label_in_a_real_block_is_still_found(self):
        """The guard must still guard — the fix narrows it, not disables it."""
        day = self._closed_day("Shipped it.")

        self.assertEqual(existing_event_ids(day), {"EVT-1"})
        self.assertEqual(select_late_candidates(day, [candidate("EVT-1")]), ())


class ACategoryLessLateItemTests(unittest.TestCase):
    """CHARACTERIZATION — the `- Category:` bullet's absent side.

    `_render_item_block(include_category=True)` writes the bullet only when
    the candidate actually has a category, and a branch-coverage pass (C43)
    found the falsy side had never run: no test had ever appended a late item
    with `category=None`.

    It is a reachable state, not a theoretical one — `test_daily_history.py::
    test_a_category_less_keep_candidate_silently_loses_its_detail` records
    the route (docs/11 §71 permits a human to promote a CANCELLED-derived
    Candidate by hand) and what it costs on the Daily-close path.

    The late path is **not the same**, and the asymmetry is worth having
    written down. Measured, one such Candidate arriving after the day closed:

        Daily close   summary survives in `## Summary`; Event ID, Owner and
                      every review field are lost entirely
        late append   the whole item block reaches `## Late Events` —
                      summary, Owner AND Event ID — because `## Late Events`
                      is not one of the four category sections
        Monthly       drops it (no `- Category:`, and guessing a heading
                      would be worse) and COUNTS it: `unconsolidated=1`,
                      which `app/runner.py` logs as MONTHLY_UNCONSOLIDATED
        next run      not re-added — the id is in the file, so §38 sees it

    So the loss is bounded to the Monthly, and it is not silent. Nothing here
    is fixed: what a Monthly should do with a category it does not recognise
    is docs/09 §14's decision, recorded as BACKLOG A-21 together with the
    Daily-side sibling.
    """

    def _candidate_without_category(self, event_id="L-NOCAT"):
        return HistoryCandidate(
            history_id=f"HIST-{event_id}",
            event_id=event_id,
            timestamp=f"{DAY.isoformat()}T11:00:00+09:00",
            category=None,
            project_id="PRJ_A",
            role="COO",
            summary="a category-less late candidate",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def _closed_day_with_late(self):
        from daily.markdown import render_daily_markdown

        day = render_daily_markdown(DAY, [candidate("EVT-1")], "gen")
        late = self._candidate_without_category()
        return day, late, append_late_events(
            day, select_late_candidates(day, [late]), now_iso="X"
        )

    def test_the_item_block_is_written_without_a_category_bullet(self):
        _day, _late, updated = self._closed_day_with_late()
        section = updated.split(LATE_SECTION_TITLE, 1)[1]

        self.assertIn("- Event ID: L-NOCAT", section)
        self.assertIn("- Owner: COO", section)
        self.assertIn("a category-less late candidate", section)
        self.assertNotIn("- Category:", section)

    def test_the_event_id_survives_here_unlike_the_daily_close_path(self):
        """The half that makes this worth recording: the same Candidate loses
        its id entirely when the day is closed WITH it, and keeps it when it
        arrives after."""
        from daily.markdown import render_daily_markdown

        closed_with_it = render_daily_markdown(
            DAY, [self._candidate_without_category()], "gen"
        )
        _day, _late, arrived_after = self._closed_day_with_late()

        self.assertNotIn("L-NOCAT", closed_with_it)
        self.assertIn("L-NOCAT", arrived_after)

    def test_it_is_not_re_added_on_a_later_run(self):
        """§38's guard reads the file, and the id IS in the file — so the
        bounded loss stays bounded rather than growing one block per run."""
        _day, late, updated = self._closed_day_with_late()

        self.assertIn("L-NOCAT", existing_event_ids(updated))
        self.assertEqual(select_late_candidates(updated, [late]), ())

    def test_monthly_drops_it_and_says_so(self):
        """The loss, and the counter that keeps it from being silent."""
        from monthly.parser import parse_daily_markdown

        _day, _late, updated = self._closed_day_with_late()
        document = parse_daily_markdown(updated, target_date=DAY)

        self.assertEqual([item.event_id for item in document.items], ["EVT-1"])
        self.assertEqual(document.unconsolidated, 1)

    def test_a_late_item_that_has_a_category_is_consolidated(self):
        """The control. Without it this class would pass if the bullet were
        never written at all."""
        from monthly.parser import parse_daily_markdown

        from daily.markdown import render_daily_markdown

        day = render_daily_markdown(DAY, [candidate("EVT-1")], "gen")
        late = candidate("L-CAT", category="DECISION")
        updated = append_late_events(
            day, select_late_candidates(day, [late]), now_iso="X"
        )
        document = parse_daily_markdown(updated, target_date=DAY)

        self.assertEqual(
            sorted((item.event_id, item.category) for item in document.items),
            [("EVT-1", "MILESTONE"), ("L-CAT", "DECISION")],
        )
        self.assertEqual(document.unconsolidated, 0)


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
        # A summary that is itself a bullet spelling a label. The corpus had
        # `- leading dash` and `Event ID: L1` separately and neither alone
        # reaches `## Summary`, where the renderer repeats summaries raw —
        # `OnlyItemBlocksCarryLabelsTests`.
        "- Event ID: L1", "- Event ID: E3", "- Owner: CTO Backend",
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


if __name__ == "__main__":
    unittest.main()
