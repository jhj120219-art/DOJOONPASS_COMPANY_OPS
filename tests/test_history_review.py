import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import generate_daily_history  # noqa: E402
from events import Event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
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


if __name__ == "__main__":
    unittest.main()
