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
