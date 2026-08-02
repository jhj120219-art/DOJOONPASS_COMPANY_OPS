import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import review_cli  # noqa: E402
from events import Event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryFilter,
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


def make_input_fn(responses):
    iterator = iter(responses)

    def _input(prompt=""):
        try:
            return next(iterator)
        except StopIteration:
            raise AssertionError(f"no more canned input available for prompt: {prompt!r}")

    return _input


def make_print_fn():
    lines = []

    def _print(*args, **kwargs):
        lines.append(" ".join(str(a) for a in args))

    _print.lines = lines
    return _print


class SpyReviewer:
    def __init__(self, real):
        self._real = real
        self.calls = []

    def list_reviewable(self, decision=None):
        self.calls.append(("list_reviewable", decision))
        return self._real.list_reviewable(decision=decision)

    def submit_review(self, history_id, **kwargs):
        self.calls.append(("submit_review", history_id, kwargs))
        return self._real.submit_review(history_id, **kwargs)


class ReviewCliTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        self.filter = HistoryFilter()
        self.reviewer = RepositoryHistoryReviewer(self.repo)

    def _seed_keep_candidate(self, **overrides) -> str:
        event = sample_event(**overrides)
        candidate = self.filter.evaluate(event).candidate
        self.repo.save(candidate)
        return candidate.history_id


class EmptyRepositoryTests(ReviewCliTestCase):
    def test_no_candidates_prints_message_and_returns_zero(self):
        print_fn = make_print_fn()
        result = review_cli.run_interactive_review(
            self.reviewer, input_fn=make_input_fn([]), print_fn=print_fn
        )
        self.assertEqual(result, 0)
        self.assertTrue(any("없습니다" in line for line in print_fn.lines))


class FullReviewTests(ReviewCliTestCase):
    def test_all_four_fields_are_saved(self):
        history_id = self._seed_keep_candidate()
        responses = [
            "",  # proceed = yes (Enter)
            "왜 시작했는가에 대한 맥락",  # decision_context
            "기대 결과",  # expected_outcome
            "실제 결과",  # actual_outcome
            "배운 점",  # lessons_learned
        ]
        result = review_cli.run_interactive_review(
            self.reviewer, input_fn=make_input_fn(responses), print_fn=make_print_fn()
        )

        self.assertEqual(result, 1)
        updated = self.repo.get(history_id)
        self.assertEqual(updated.decision_context, "왜 시작했는가에 대한 맥락")
        self.assertEqual(updated.expected_outcome, "기대 결과")
        self.assertEqual(updated.actual_outcome, "실제 결과")
        self.assertEqual(updated.lessons_learned, "배운 점")

    def test_blank_fields_leave_candidate_unchanged(self):
        history_id = self._seed_keep_candidate()
        responses = ["", "", "", "", ""]  # proceed=yes, all four fields blank

        result = review_cli.run_interactive_review(
            self.reviewer, input_fn=make_input_fn(responses), print_fn=make_print_fn()
        )

        self.assertEqual(result, 0)
        candidate = self.repo.get(history_id)
        self.assertIsNone(candidate.decision_context)

    def test_dash_explicitly_clears_a_field(self):
        history_id = self._seed_keep_candidate()
        self.reviewer.submit_review(history_id, decision_context="will be cleared")

        responses = ["", "-", "", "", ""]  # proceed=yes, clear decision_context, rest blank
        review_cli.run_interactive_review(
            self.reviewer, input_fn=make_input_fn(responses), print_fn=make_print_fn()
        )

        self.assertIsNone(self.repo.get(history_id).decision_context)

    def test_skipping_a_candidate_leaves_it_unmodified(self):
        history_id = self._seed_keep_candidate()
        responses = ["n"]  # decline to review

        result = review_cli.run_interactive_review(
            self.reviewer, input_fn=make_input_fn(responses), print_fn=make_print_fn()
        )

        self.assertEqual(result, 0)
        candidate = self.repo.get(history_id)
        self.assertIsNone(candidate.decision_context)

    def test_partial_field_update(self):
        history_id = self._seed_keep_candidate()
        responses = ["", "only decision context", "", "", ""]

        review_cli.run_interactive_review(
            self.reviewer, input_fn=make_input_fn(responses), print_fn=make_print_fn()
        )

        updated = self.repo.get(history_id)
        self.assertEqual(updated.decision_context, "only decision context")
        self.assertIsNone(updated.expected_outcome)


class MultipleCandidatesTests(ReviewCliTestCase):
    def test_two_candidates_processed_in_order(self):
        first_id = self._seed_keep_candidate(event_id="TEST-FIRST-001")
        second_id = self._seed_keep_candidate(
            event_id="TEST-SECOND-001", event_type="ISSUE_RESOLVED", milestone=None
        )

        responses = [
            "",  # candidate 1: proceed
            "context one",
            "",
            "",
            "",
            "n",  # candidate 2: skip
        ]
        result = review_cli.run_interactive_review(
            self.reviewer, input_fn=make_input_fn(responses), print_fn=make_print_fn()
        )

        self.assertEqual(result, 1)
        self.assertEqual(self.repo.get(first_id).decision_context, "context one")
        self.assertIsNone(self.repo.get(second_id).decision_context)


class OnlyReviewerMethodsCalledTests(ReviewCliTestCase):
    def test_only_list_reviewable_and_submit_review_are_called(self):
        self._seed_keep_candidate()
        spy = SpyReviewer(self.reviewer)
        responses = ["", "context", "", "", ""]

        review_cli.run_interactive_review(spy, input_fn=make_input_fn(responses), print_fn=make_print_fn())

        called_methods = {call[0] for call in spy.calls}
        self.assertEqual(called_methods, {"list_reviewable", "submit_review"})


class ReviewCliBoundaryTests(unittest.TestCase):
    def test_does_not_import_daily_scheduler_collector_transport_or_reporter(self):
        cli_path = Path(__file__).resolve().parents[1] / "src" / "review_cli.py"
        content = cli_path.read_text(encoding="utf-8")
        forbidden = re.compile(
            r"^\s*(import|from)\s+(daily|scheduler|collector|transport|reporter)\b", re.MULTILINE
        )
        self.assertIsNone(forbidden.search(content), "review_cli.py imports a forbidden module")

    def test_no_hardcoded_absolute_windows_paths(self):
        cli_path = Path(__file__).resolve().parents[1] / "src" / "review_cli.py"
        content = cli_path.read_text(encoding="utf-8")
        code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
        for token in ("C:\\Users", "D:\\", "OneDrive\\"):
            self.assertNotIn(token, code_without_docstrings)


if __name__ == "__main__":
    unittest.main()
