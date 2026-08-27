import contextlib
import io
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
        self.assertEqual(result, review_cli.ReviewSession(updated=0, failed=()))
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

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.failed, ())
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

        self.assertEqual(result.updated, 0)
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

        self.assertEqual(result.updated, 0)
        self.assertEqual(result.failed, (), "a skip is not a save failure")
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

        self.assertEqual(result.updated, 1)
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

        def _record(reviewer, **kwargs):
            # Returns a real `ReviewSession`, not `None`: `main()` reads
            # `.failed` off this value to choose its exit code, and a stub
            # that answered `None` would make the wiring tests fail for a
            # reason that has nothing to do with wiring.
            self.captured.append(reviewer)
            return self.session

        self.session = review_cli.ReviewSession()
        review_cli.run_interactive_review = _record
        self.addCleanup(setattr, review_cli, "run_interactive_review", real)

    def test_a_session_with_unsaved_context_does_not_report_success(self):
        """The defect (C117): `main()` threw this value away and returned 0.
        A session in which the operator typed Decision Context into three
        candidates and none of them reached disk ended like a clean one."""
        self.session = review_cli.ReviewSession(updated=0, failed=("HIST-1",))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = review_cli.main()

        self.assertEqual(code, review_cli.DEGRADED_EXIT)
        self.assertIn("저장되지", err.getvalue())

    def test_a_clean_session_still_reports_success(self):
        """The antecedent, and the two cases that must not be degraded: an
        empty candidate list and a session the operator skipped through both
        leave `failed` empty."""
        for session in (
            review_cli.ReviewSession(),
            review_cli.ReviewSession(updated=0, failed=()),
            review_cli.ReviewSession(updated=3, failed=()),
        ):
            with self.subTest(session=session):
                self.session = session
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = review_cli.main()

                self.assertEqual(code, 0)
                self.assertEqual(err.getvalue(), "")

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

    def test_the_module_guard_raises_the_code_main_returns(self):
        """Line-for-line the one statement no import can execute. Asserted as
        source rather than run, because running it means running the whole
        module as `__main__` — a second import of a module that reconfigures
        `sys.stdout` at import time.

        **It used to be a bare `main()` (C79).** That is what made this the
        one entrypoint whose refusal could not reach the operating system:
        `main()` returning 1 and the process exiting 0 is the defect wearing
        the fix's clothes. The four siblings all spell it
        `raise SystemExit(main(sys.argv))`, and `sys.argv` is passed here for
        their reason too — `cli.py`'s docstring records that reading the
        global inside `main()` made twenty-five tests fail by refusing
        pytest's own flags, so the command line belongs at the boundary.
        """
        source = (
            Path(__file__).resolve().parents[1] / "src" / "review_cli.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'if __name__ == "__main__":\n    raise SystemExit(main(sys.argv))\n',
            source,
        )
        self.assertNotIn('if __name__ == "__main__":\n    main()\n', source)

    def test_an_unexpected_argument_is_refused_before_any_candidate_is_shown(self):
        """C79, in-process. The subprocess half lives in
        `AnEntrypointRefusesArgumentsItCannotHonourTests`; this one states
        the property that matters here — the refusal happens before
        `list_reviewable()`, so nothing about the operator's Decision Context
        is read, printed, or prompted for.

        Measured before the fix: `python src/review_cli.py --help` printed a
        real KEEP Candidate and stopped at the edit prompt.
        """
        code = review_cli.main(["review_cli.py", "--help"])

        self.assertEqual(code, 1)
        self.assertEqual(
            self.captured, [],
            "a reviewer was built for an invocation that should have been "
            "refused",
        )

    def test_no_arguments_still_runs_the_review(self):
        """The other side of the boundary: the ordinary invocation is
        untouched, and still returns 0."""
        code = review_cli.main()

        self.assertEqual(code, 0)
        self.assertEqual(len(self.captured), 1)


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
        self.assertEqual(updated.updated, 2)
        self.assertEqual(updated.failed, ("HIST-1",))

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

        self.assertEqual(updated.updated, 0)
        self.assertEqual(set(updated.failed), {"HIST-1", "HIST-2", "HIST-3"})
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


class InputThatEndsBeforeTheCandidatesDoTests(ReviewCliTestCase):
    """Found by running the real command (C117), not by reading it.

        printf 'n\\nn\\nn\\n' | python src/review_cli.py

    ended on the fourth candidate with a raw `EOFError` traceback and exit
    **1** — the code this project reserves for a configuration error. Every
    non-terminal invocation reaches it: a pipe, a redirect, a task with no
    console. And if the input ran out *between* the field prompts, the
    paragraph the person had already typed went with it, even though this
    file's save-failure path goes out of its way to echo exactly that text
    back ("Decision Context is what README RULE 11/12 call the company's
    most valuable asset").

    **No test could have caught it.** Every fixture here supplies
    `input_fn=make_input_fn(...)`, which answers `""` forever once its list
    is exhausted — the one thing the real `input()` never does. The fixture
    and the operator's terminal disagreed about the most ordinary edge there
    is, and the fixture is the one the suite believed.
    """

    def _raising_input(self, answers, exc=EOFError):
        it = iter(answers)

        def _input(_prompt):
            try:
                return next(it)
            except StopIteration:
                raise exc()

        return _input

    def test_the_session_ends_without_a_traceback(self):
        for _ in range(3):
            self._seed_keep_candidate(event_id=f"TEST-{_}-001")

        session = review_cli.run_interactive_review(
            self.reviewer,
            input_fn=self._raising_input(["n"]),
            print_fn=make_print_fn(),
        )

        self.assertEqual(session.updated, 0)
        self.assertEqual(session.failed, ())
        self.assertEqual(len(session.unreached), 2)

    def test_the_candidate_it_stopped_on_counts_as_unreached(self):
        """It was displayed, so it is tempting to call it offered. It was
        not: nobody answered for it, and it is still on disk unchanged."""
        first = self._seed_keep_candidate(event_id="TEST-FIRST-001")
        second = self._seed_keep_candidate(
            event_id="TEST-SECOND-001", event_type="ISSUE_RESOLVED", milestone=None
        )

        session = review_cli.run_interactive_review(
            self.reviewer,
            input_fn=self._raising_input(["n"]),
            print_fn=make_print_fn(),
        )

        self.assertIn(second, session.unreached)
        self.assertNotIn(first, session.unreached)

    def test_text_typed_before_the_input_ran_out_is_echoed_back(self):
        """The whole reason this branch exists. Those words reached no
        `submit_review()` call, so the terminal is the only place they are."""
        self._seed_keep_candidate()
        print_fn = make_print_fn()

        review_cli.run_interactive_review(
            self.reviewer,
            # proceed, then one field, then the input ends.
            input_fn=self._raising_input(["", "타이핑한 결정 맥락"]),
            print_fn=print_fn,
        )

        output = chr(10).join(print_fn.lines)
        self.assertIn("타이핑한 결정 맥락", output)
        self.assertIn("[중단]", output)

    def test_an_interrupt_is_handled_the_same_way(self):
        """Ctrl+C leaves the operator in the same place a closed pipe does —
        a list that was not finished — and used to leave the same traceback."""
        self._seed_keep_candidate()

        session = review_cli.run_interactive_review(
            self.reviewer,
            input_fn=self._raising_input([], exc=KeyboardInterrupt),
            print_fn=make_print_fn(),
        )

        self.assertEqual(len(session.unreached), 1)

    def test_a_session_that_ran_to_the_end_reaches_everything(self):
        """The antecedent. Without it, a `run_interactive_review()` that
        reported every candidate unreached would pass every test above."""
        self._seed_keep_candidate(event_id="TEST-ONE-001")
        self._seed_keep_candidate(
            event_id="TEST-TWO-001", event_type="ISSUE_RESOLVED", milestone=None
        )

        session = review_cli.run_interactive_review(
            self.reviewer,
            input_fn=self._raising_input(["n", "n"]),
            print_fn=make_print_fn(),
        )

        self.assertEqual(session.unreached, ())
        self.assertEqual(session.failed, ())

    def test_the_summary_says_the_session_stopped_early(self):
        """A count on screen as well as in the exit code: the operator who
        piped answers in is looking at the terminal, not at `$?`."""
        for index in range(4):
            self._seed_keep_candidate(event_id=f"TEST-{index}-001")
        print_fn = make_print_fn()

        review_cli.run_interactive_review(
            self.reviewer,
            input_fn=self._raising_input(["n"]),
            print_fn=print_fn,
        )

        output = chr(10).join(print_fn.lines)
        self.assertIn("검토하지 못한 Candidate: 3건", output)

    def test_main_does_not_report_success_for_a_session_it_could_not_finish(self):
        """The exit code, which is where the original defect landed: `1`,
        from an uncaught `EOFError`, for a run that had nothing wrong with
        its configuration."""
        real = review_cli.run_interactive_review
        review_cli.run_interactive_review = lambda reviewer, **kw: (
            review_cli.ReviewSession(updated=1, failed=(), unreached=("HIST-9",))
        )
        self.addCleanup(setattr, review_cli, "run_interactive_review", real)

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = review_cli.main()

        self.assertEqual(code, review_cli.DEGRADED_EXIT)
        self.assertIn("검토하지 못한", err.getvalue())


class SecretShapedProseIsNamedBeforeItIsStoredTests(ReviewCliTestCase):
    """The warning at the one moment it can still be retyped (C125).

    Decision Context is the third door text takes into Company History, and
    the only one that had neither a refusal nor a report. A Signal typed on
    this machine is refused outright by `find_secret_material()`; an Event
    from another Desktop is at least reported by
    `ops_status._secret_shaped_event_content()`. This field — the one a
    person writes as prose — was accepted unscanned and rendered straight
    into the Daily History that is pushed to the backup remote.

    **A warning rather than a refusal, deliberately.** `oplog` records that
    its patterns over-match on purpose — "a work note reading 'auth token:
    rotated' is refused even though it carries no secret" — and that bargain
    was struck for a Signal, which is a short structured record. Refusing a
    lessons-learned paragraph on the same grounds is a different bargain and
    a policy decision (BACKLOG). What the person gets is the fact, while
    they are still at the keyboard.
    """

    TOKEN = "ntn_" + "P" * 44

    def _review(self, typed):
        printed = make_print_fn()
        answers = iter(["", typed, "", "", ""])
        review_cli._review_one(
            self.reviewer,
            self.repo.get(self._seed_keep_candidate()),
            input_fn=lambda prompt: next(answers, ""),
            print_fn=printed,
        )
        return chr(10).join(printed.lines)

    def test_a_secret_shaped_field_is_named(self):
        output = self._review(f"새 토큰은 {self.TOKEN} 이다.")

        self.assertIn("[주의]", output)
        self.assertIn("Decision Context", output)

    def test_the_warning_does_not_repeat_the_secret(self):
        """The rule `find_secret_material()` states: a report of a leaked
        credential must not become the second copy of it. Here it would be a
        copy on the operator's screen and in their scrollback."""
        output = self._review(f"새 토큰은 {self.TOKEN} 이다.")

        warning = [line for line in output.splitlines() if "[주의]" in line]
        self.assertTrue(warning)
        self.assertNotIn(self.TOKEN, chr(10).join(warning))

    def test_it_warns_and_still_saves(self):
        """A warning that also refused would silently discard a paragraph
        somebody typed — which is the decision this deliberately does not
        take."""
        history_id = self._seed_keep_candidate(event_id="TEST-SAVE-001")
        answers = iter(["", f"값은 {self.TOKEN}", "", "", ""])
        review_cli._review_one(
            self.reviewer,
            self.repo.get(history_id),
            input_fn=lambda prompt: next(answers, ""),
            print_fn=make_print_fn(),
        )

        self.assertIn(self.TOKEN, self.repo.get(history_id).decision_context)

    def test_ordinary_prose_gets_no_warning(self):
        """The control, and the one that matters most for a warning: an
        alert on every review note is an alert nobody reads."""
        output = self._review("Notion Integration 토큰을 교체하기로 했다.")

        self.assertNotIn("[주의]", output)

    def test_the_warning_comes_before_the_write_not_before_the_message(self):
        """Order is the whole value — and the order that matters is against
        `submit_review()`, not against the confirmation line.

        Measured: a mutation moving the warning to sit between the save and
        the `저장되었습니다` message passed the first version of this test,
        which compared the two *printed lines*. By then the token is on
        disk, which is the moment the warning exists to precede. So this
        records the real event instead — the reviewer is a spy and the
        timeline holds both the prints and the write.
        """
        events = []

        class _Spy:
            def __init__(inner, real):
                inner._real = real

            def list_reviewable(inner, decision=None):
                return inner._real.list_reviewable(decision)

            def submit_review(inner, history_id, **updates):
                events.append("WRITE")
                return inner._real.submit_review(history_id, **updates)

        answers = iter(["", f"값은 {self.TOKEN}", "", "", ""])
        review_cli._review_one(
            _Spy(self.reviewer),
            self.repo.get(self._seed_keep_candidate()),
            input_fn=lambda prompt: next(answers, ""),
            print_fn=lambda *a: events.append(
                "WARN" if any("[주의]" in str(x) for x in a) else "print"
            ),
        )

        self.assertIn("WARN", events)
        self.assertIn("WRITE", events)
        self.assertLess(
            events.index("WARN"),
            events.index("WRITE"),
            f"the warning came after the write: {events}",
        )



if __name__ == "__main__":
    unittest.main()
