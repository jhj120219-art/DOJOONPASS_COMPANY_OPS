import re
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import (  # noqa: E402
    LateUpdateOutcome,
    generate_daily_history,
    update_daily_history,
)
from events import Event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
    HistoryFilter,
    HistoryReviewer,
    HistoryReviewError,
    RepositoryHistoryReviewer,
)


def sample_event(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-DECISION-001",
        "timestamp": "2026-08-10T10:00:00+09:00",
        "source": "DESKTOP_4",
        "role": "COO",
        "project_id": "DOJOONPASS_PRODUCT",
        "event_type": "DECISION_APPROVED",
        "status": "IN_PROGRESS",
        "milestone": None,
        "summary": "CEO approved Closed Beta scope",
        "blocker": None,
        "evidence": ["CEO Approval"],
        "history_candidate": True,
    }
    data.update(overrides)
    return Event.from_dict(data)


class ReviewTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        self.filter = HistoryFilter()
        self.reviewer = RepositoryHistoryReviewer(self.repo)
        self.daily_dir = root / "daily"

    def _seed_keep_candidate(self, **overrides) -> str:
        event = sample_event(**overrides)
        candidate = self.filter.evaluate(event).candidate
        self.repo.save(candidate)
        return candidate.history_id

    def _seed_review_candidate(self, **overrides) -> str:
        defaults = dict(
            event_id="TEST-COMPLETE-001",
            event_type="COMPLETED",
            status="COMPLETED",
        )
        defaults.update(overrides)
        event = sample_event(**defaults)
        candidate = self.filter.evaluate(event).candidate
        self.repo.save(candidate)
        return candidate.history_id


class ListReviewableTests(ReviewTestCase):
    def test_lists_keep_and_review_together(self):
        keep_id = self._seed_keep_candidate()
        review_id = self._seed_review_candidate()

        ids = {c.history_id for c in self.reviewer.list_reviewable()}
        self.assertEqual(ids, {keep_id, review_id})

    def test_filters_to_keep_only(self):
        keep_id = self._seed_keep_candidate()
        self._seed_review_candidate()

        results = self.reviewer.list_reviewable(decision=HistoryDecision.KEEP)
        self.assertEqual([c.history_id for c in results], [keep_id])

    def test_filters_to_review_only(self):
        self._seed_keep_candidate()
        review_id = self._seed_review_candidate()

        results = self.reviewer.list_reviewable(decision=HistoryDecision.REVIEW)
        self.assertEqual([c.history_id for c in results], [review_id])

    def test_drop_candidates_never_appear(self):
        # DROP is never stored in the Repository in the first place, so
        # there is nothing to filter out here — confirm the list is empty
        # for a decision that was never persisted.
        self._seed_keep_candidate()
        self.assertEqual(self.reviewer.list_reviewable(decision=HistoryDecision.DROP), [])


class SubmitReviewTests(ReviewTestCase):
    def test_sets_all_four_fields(self):
        history_id = self._seed_keep_candidate()

        updated = self.reviewer.submit_review(
            history_id,
            decision_context="Beta 범위를 먼저 제한하여 실제 사용자 검증을 우선하기로 결정",
            expected_outcome="초기 운영 리스크 감소",
            actual_outcome="리스크 감소 확인됨",
            lessons_learned="작은 범위로 시작하는 것이 유효했다",
        )

        self.assertEqual(
            updated.decision_context, "Beta 범위를 먼저 제한하여 실제 사용자 검증을 우선하기로 결정"
        )
        self.assertEqual(updated.expected_outcome, "초기 운영 리스크 감소")
        self.assertEqual(updated.actual_outcome, "리스크 감소 확인됨")
        self.assertEqual(updated.lessons_learned, "작은 범위로 시작하는 것이 유효했다")

    def test_persists_to_repository(self):
        history_id = self._seed_keep_candidate()
        self.reviewer.submit_review(history_id, decision_context="persisted context")

        reloaded = self.repo.get(history_id)
        self.assertEqual(reloaded.decision_context, "persisted context")

    def test_partial_update_leaves_other_fields_untouched(self):
        history_id = self._seed_keep_candidate()
        self.reviewer.submit_review(
            history_id, decision_context="first pass context", expected_outcome="first pass outcome"
        )

        updated = self.reviewer.submit_review(history_id, actual_outcome="later actual outcome")

        self.assertEqual(updated.decision_context, "first pass context")
        self.assertEqual(updated.expected_outcome, "first pass outcome")
        self.assertEqual(updated.actual_outcome, "later actual outcome")
        self.assertIsNone(updated.lessons_learned)

    def test_explicit_none_clears_a_previously_set_field(self):
        history_id = self._seed_keep_candidate()
        self.reviewer.submit_review(history_id, decision_context="will be cleared")

        updated = self.reviewer.submit_review(history_id, decision_context=None)
        self.assertIsNone(updated.decision_context)

    def test_no_arguments_leaves_candidate_unchanged(self):
        history_id = self._seed_keep_candidate()
        before = self.repo.get(history_id)
        after = self.reviewer.submit_review(history_id)
        self.assertEqual(before, after)

    def test_does_not_change_filter_result_or_core_fields(self):
        history_id = self._seed_keep_candidate()
        before = self.repo.get(history_id)

        updated = self.reviewer.submit_review(history_id, decision_context="context")

        self.assertEqual(updated.filter_result, before.filter_result)
        self.assertEqual(updated.category, before.category)
        self.assertEqual(updated.summary, before.summary)
        self.assertEqual(updated.event_id, before.event_id)
        self.assertEqual(updated.evidence, before.evidence)

    def test_review_candidate_can_also_be_annotated(self):
        history_id = self._seed_review_candidate()
        updated = self.reviewer.submit_review(history_id, decision_context="review annotation")
        self.assertEqual(updated.filter_result, HistoryDecision.REVIEW)
        self.assertEqual(updated.decision_context, "review annotation")

    def test_unknown_history_id_raises(self):
        with self.assertRaises(HistoryReviewError):
            self.reviewer.submit_review("HIST-DOES-NOT-EXIST", decision_context="x")

    def test_korean_text_round_trips_through_storage(self):
        history_id = self._seed_keep_candidate()
        self.reviewer.submit_review(history_id, lessons_learned="검증 우선 전략이 유효했다")
        reloaded = self.repo.get(history_id)
        self.assertEqual(reloaded.lessons_learned, "검증 우선 전략이 유효했다")


class ReviewerInterfaceTests(unittest.TestCase):
    def test_reviewer_is_abstract(self):
        with self.assertRaises(TypeError):
            HistoryReviewer()


class ReviewToDailyIntegrationTests(ReviewTestCase):
    def test_reviewed_fields_appear_in_generated_daily_markdown(self):
        history_id = self._seed_keep_candidate(event_id="TEST-INTEGRATION-001")
        self.reviewer.submit_review(
            history_id,
            decision_context="검증된 맥락",
            expected_outcome="기대 결과",
        )

        path = generate_daily_history(self.repo, date(2026, 8, 10), output_dir=self.daily_dir)
        content = path.read_text(encoding="utf-8")

        self.assertIn("- Decision Context: 검증된 맥락", content)
        self.assertIn("- Expected Outcome: 기대 결과", content)

    def test_unreviewed_keep_candidate_still_renders_without_new_fields(self):
        self._seed_keep_candidate(event_id="TEST-INTEGRATION-002")
        path = generate_daily_history(self.repo, date(2026, 8, 10), output_dir=self.daily_dir)
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("Decision Context:", content)


class ReviewPathSafetyTests(unittest.TestCase):
    def test_review_module_does_not_import_collector_transport_or_reporter(self):
        history_src = Path(__file__).resolve().parents[1] / "src" / "history"
        forbidden = (
            re.compile(r"^\s*import\s+(collector|transport|reporter)\b", re.MULTILINE),
            re.compile(r"^\s*from\s+(collector|transport|reporter)\b", re.MULTILINE),
        )
        review_file = history_src / "review.py"
        content = review_file.read_text(encoding="utf-8")
        for pattern in forbidden:
            self.assertIsNone(pattern.search(content), f"{review_file} unexpectedly imports")

    def test_no_hardcoded_absolute_windows_paths(self):
        review_file = Path(__file__).resolve().parents[1] / "src" / "history" / "review.py"
        content = review_file.read_text(encoding="utf-8")
        code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
        for token in ("C:\\Users", "D:\\", "OneDrive\\"):
            self.assertNotIn(token, code_without_docstrings)


class ReviewedContextReachesNothingTests(unittest.TestCase):
    """CHARACTERIZATION — records today's behaviour, not desired behaviour.

    README RULE 11/12 name Decision Context the company's most valuable
    asset: "회사의 가장 중요한 자산은 코드가 아니라 시간이 지나도 복원
    가능한 Decision Context이다". `RepositoryHistoryReviewer` is the only
    way to record it, since Event Schema was deliberately left without those
    fields.

    Measured below: once a COO fills them in, they reach nowhere a human
    will ever look.

        stored on the candidate            yes
        rendered into the Daily file       NO  (the file already exists;
                                                update_daily_history() sees
                                                the event_id is present and
                                                correctly reports
                                                NO_LATE_EVENTS)
        included in the git Backup         NO  (docs/08 §26-28 syncs only
                                                daily/ and monthly/, and
                                                candidates live under
                                                runtime/, outside Local
                                                Master entirely)

    So the one asset the system says matters most survives only as
    `runtime/history_candidates/keep/*.json` on Desktop 4 — the single
    location that is neither Company History nor backed up. A disk failure
    loses it and nothing reports the loss.

    NOT FIXED, because every route out is a decision this Sprint may not
    make:

        re-render the Daily      discards the COO's manual edits, which
                                 docs/06 §57 explicitly protects — the same
                                 reason Late Events are appended rather than
                                 re-rendered
        edit the item in place   text surgery on a hand-editable document
        extend Late Event update docs/06 §37 covers a late *Event*, not late
                                 *enrichment* of one already present
        back up candidates       docs/08 §26-28 fixes the backup scope

    Recorded in BACKLOG.md. Pinned here so the gap is visible to the next
    reader rather than discovered by a COO wondering where their reasoning
    went.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )
        self.day = date(2026, 8, 8)
        self.repo.save(
            HistoryCandidate(
                history_id="HIST-CTX",
                event_id="EVT-CTX",
                timestamp=f"{self.day.isoformat()}T10:00:00+09:00",
                category="DECISION",
                project_id="CLOSED_BETA",
                role="COO",
                summary="Closed Beta Scope confirmed.",
                evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
        )
        self.daily_path = generate_daily_history(
            self.repo, self.day, output_dir=self.root / "daily"
        )

    def _review(self):
        RepositoryHistoryReviewer(self.repo).submit_review(
            "HIST-CTX",
            decision_context="Shipped ahead of a competitor.",
            lessons_learned="Narrowing scope first would have been faster.",
        )

    def test_the_review_is_stored_on_the_candidate(self):
        """The half that works."""
        self._review()

        stored = self.repo.get("HIST-CTX")
        self.assertEqual(stored.decision_context, "Shipped ahead of a competitor.")
        self.assertEqual(
            stored.lessons_learned, "Narrowing scope first would have been faster."
        )

    def test_the_already_written_daily_never_receives_it(self):
        before = self.daily_path.read_text(encoding="utf-8")
        self._review()

        after = self.daily_path.read_text(encoding="utf-8")
        self.assertEqual(after, before)
        self.assertNotIn("Decision Context", after)
        self.assertNotIn("Shipped ahead of a competitor.", after)

    def test_the_late_event_update_correctly_declines_to_help(self):
        """`update_daily_history()` is not at fault — the event_id is already
        in the file, so NO_LATE_EVENTS is the right answer to the question it
        was asked. It simply is not the mechanism for this."""
        self._review()

        result = update_daily_history(
            self.repo,
            self.day,
            output_dir=self.root / "daily",
            now=datetime(2026, 8, 12, 10, 0).astimezone(),
        )

        self.assertEqual(result.outcome, LateUpdateOutcome.NO_LATE_EVENTS)
        self.assertNotIn(
            "Shipped ahead of a competitor.",
            self.daily_path.read_text(encoding="utf-8"),
        )

    def test_a_daily_rendered_after_the_review_would_include_it(self):
        """The renderer has always supported these fields — the gap is
        propagation into an existing file, not rendering."""
        self._review()

        fresh = generate_daily_history(
            self.repo, self.day, output_dir=self.root / "fresh"
        )

        self.assertIn("- Decision Context: Shipped ahead of a competitor.", fresh.read_text(encoding="utf-8"))

    def test_the_backup_scope_excludes_where_the_context_lives(self):
        """docs/08 §26-28: only daily/ and monthly/ are ever synced. The
        candidate store is not under Local Master at all."""
        from backup.working_copy import _ALLOWED_TOP_LEVEL_DIRS

        self.assertEqual(_ALLOWED_TOP_LEVEL_DIRS, frozenset({"daily", "monthly"}))
        self.assertNotIn("history_candidates", _ALLOWED_TOP_LEVEL_DIRS)


if __name__ == "__main__":
    unittest.main()
