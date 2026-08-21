import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# The repository root too: this file imports a root-level script
# (`ops_status.py` and friends live beside `src/`, not in it). Under
# pytest the rootdir is already on `sys.path`, so the omission only
# surfaced once `python tests/<file>.py` started running the whole
# file instead of stopping at a stray `unittest.main()` (C38).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review_cli  # noqa: E402
from events import Event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
    HistoryFilter,
    HistoryReviewer,
    RepositoryHistoryReviewer,
)
from review_cli import run_interactive_review  # noqa: E402


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


class MainWiresTheRealRepositoryToTheRealReviewerTests(unittest.TestCase):
    """`main()` is three lines and the suite had never run one of them.

    Every other test here drives `run_interactive_review()` with a spy, which
    is the right level for the behaviour — and leaves the composition
    completely unchecked. That is the half a wiring mistake lives in: a
    `main()` that built the reviewer around the wrong repository, or forgot
    to pass it, would fail only for the operator running the real command.
    BACKLOG C49 §11c listed these lines as "cheap to cover, left by
    priority".

    `run_interactive_review` is replaced rather than the repository stubbed,
    because the point is what `main()` **constructs**, not what the review
    then does — and a real interactive session would block on stdin.
    """

    def setUp(self):
        self.captured = []
        real = review_cli.run_interactive_review
        review_cli.run_interactive_review = self.captured.append
        self.addCleanup(setattr, review_cli, "run_interactive_review", real)

    def test_main_starts_a_review(self):
        review_cli.main()

        self.assertEqual(len(self.captured), 1)

    def test_the_reviewer_is_the_repository_backed_one(self):
        """Not a spy, not a bare `HistoryReviewer` — the concrete pairing the
        operator's command depends on."""
        review_cli.main()

        self.assertIsInstance(self.captured[0], RepositoryHistoryReviewer)

    def test_it_is_built_over_the_file_repository(self):
        """The reviewer holds a `FileHistoryRepository`, which is what makes
        the CLI read the Candidates that are actually on disk. Reached
        through the object rather than by patching the class, so the test
        checks the wiring instead of restating it."""
        review_cli.main()

        reviewer = self.captured[0]
        repository = next(
            value
            for value in vars(reviewer).values()
            if isinstance(value, FileHistoryRepository)
        )
        self.assertIsInstance(repository, FileHistoryRepository)

    def test_the_module_guard_calls_main_and_nothing_else(self):
        """Line-for-line the one statement no import can execute. Asserted as
        source rather than run, because running it means running the whole
        module as `__main__` — a second import of a module that reconfigures
        `sys.stdout` at import time."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "review_cli.py"
        ).read_text(encoding="utf-8")

        self.assertIn('if __name__ == "__main__":\n    main()\n', source)


class SaveFailureIsolationTests(unittest.TestCase):
    """One candidate's save failure must not end the session.

    Reproduced before the fix: `submit_review()` raising took the whole CLI
    down. The text the COO had just typed was gone, and every remaining
    candidate was abandoned without ever being offered.

    That is the same per-item isolation `collector/runtime.py`,
    `outbox.drain()`, and `monthly/generator.py` all apply — and it matters
    more here than in any of them, because the input came from a person.
    Decision Context is what README RULE 11/12 call the company's most
    valuable asset; losing a paragraph of it to a transient disk error is
    not an acceptable failure mode.
    """

    TYPED = "경쟁사 대비 출시 시점을 앞당기기 위해 범위를 줄였다"

    def _candidate(self, index):
        return HistoryCandidate(
            history_id=f"HIST-{index}",
            event_id=f"EVT-{index}",
            timestamp="2026-08-08T10:00:00+09:00",
            category="DECISION",
            project_id="P",
            role="COO",
            summary=f"item {index}",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def _run(self, failing_ids, count=3):
        saved = []

        class Reviewer(HistoryReviewer):
            def list_reviewable(inner, decision=None):
                return [self._candidate(i) for i in range(1, count + 1)]

            def submit_review(inner, history_id, **updates):
                if history_id in failing_ids:
                    raise OSError("disk full")
                saved.append(history_id)
                return self._candidate(0)

        printed = []
        # Per candidate: proceed(Enter), then the four field prompts.
        answers = []
        for _ in range(count):
            answers.extend(["", self.TYPED, "", "", ""])
        answers_iter = iter(answers)

        updated = run_interactive_review(
            Reviewer(),
            input_fn=lambda prompt: next(answers_iter, ""),
            print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        )
        return updated, saved, chr(10).join(printed)

    def test_a_save_failure_does_not_end_the_session(self):
        updated, saved, _ = self._run({"HIST-1"})

        self.assertEqual(saved, ["HIST-2", "HIST-3"])
        self.assertEqual(updated, 2)

    def test_the_failure_is_reported_not_silently_counted_as_a_skip(self):
        _, _, output = self._run({"HIST-1"})

        self.assertIn("[실패] HIST-1", output)
        self.assertIn("disk full", output)

    def test_the_typed_text_is_echoed_back_so_it_is_recoverable(self):
        """The whole point: the person's words must not simply vanish."""
        _, _, output = self._run({"HIST-1"})

        self.assertIn(self.TYPED, output)
        self.assertIn("Decision Context", output)

    def test_failures_are_named_again_in_the_summary(self):
        """A failure printed thirty candidates ago has scrolled off, and
        "저장됨 2건" alone reads like success."""
        _, _, output = self._run({"HIST-1", "HIST-3"})

        self.assertIn("저장 실패: 2건", output)
        self.assertIn("HIST-1", output.rsplit("리뷰 완료", 1)[1])
        self.assertIn("HIST-3", output.rsplit("리뷰 완료", 1)[1])

    def test_every_candidate_failing_still_completes_the_session(self):
        updated, saved, output = self._run({"HIST-1", "HIST-2", "HIST-3"})

        self.assertEqual(updated, 0)
        self.assertEqual(saved, [])
        self.assertIn("저장 실패: 3건", output)

    def test_a_clean_session_reports_no_failures(self):
        _, saved, output = self._run(set())

        self.assertEqual(saved, ["HIST-1", "HIST-2", "HIST-3"])
        self.assertNotIn("저장 실패", output)

    def test_a_skip_and_a_failure_are_distinguishable(self):
        from review_cli import ReviewOutcome

        self.assertNotEqual(ReviewOutcome.SKIPPED, ReviewOutcome.FAILED)
        self.assertEqual(
            {o.value for o in ReviewOutcome}, {"SAVED", "SKIPPED", "FAILED"}
        )


if __name__ == "__main__":
    unittest.main()
