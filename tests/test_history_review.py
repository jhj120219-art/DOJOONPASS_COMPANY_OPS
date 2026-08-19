import contextlib
import io
import json
import re
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

from daily import (  # noqa: E402
    LateUpdateOutcome,
    generate_daily_history,
    update_daily_history,
)
from events import Event, create_event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
    HistoryFilter,
    HistoryReviewer,
    HistoryReviewError,
    RepositoryHistoryReviewer,
)


NOW = datetime(2026, 8, 10, 9, 0).astimezone()


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


class ReviewCandidatesReachNothingTests(unittest.TestCase):
    """CHARACTERIZATION (BACKLOG E-20): a REVIEW candidate has no path into
    Company History, and until now no counter either.

    `HistoryFilter` sends BLOCKED / COMPLETED / CANCELLED to REVIEW —
    docs/05 §24 names exactly those three, and the filter quotes it. What
    happens to them afterwards is the gap:

        generate_daily_history()   reads `decision=KEEP` only
        submit_review()            writes the four Decision Context fields
                                   and never touches `filter_result`
        anything else              there is nothing else

    So a candidate that lands in `review/` stays there. Measured end to end:
    a COMPLETED Event was absent from its Daily file, a human filled in its
    Decision Context, and it was still absent after two further runs.

    That is not obviously wrong — docs/05 §24 says these need a person, and
    `history/review.py` states plainly that "promoting a REVIEW candidate to
    KEEP is not part of this Phase". What was wrong is that the pile was
    invisible while every comparable pile had a counter (`rejected/`,
    `signals_rejected/`, orphaned Events). docs/05 §50 makes the count the
    signal — "REVIEW가 너무 많다 -> 자동화 실패 신호" — and nothing read it.

    The counter is added here. Promotion is not: deciding what a completed
    review *means* for an already-closed Daily file is a policy question
    (BACKLOG E-20), and §50 points at tightening the rules rather than
    building a promotion path anyway.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.keep = self.root / "keep"
        self.review = self.root / "review"
        self.repo = FileHistoryRepository(keep_dir=self.keep, review_dir=self.review)
        self.filter = HistoryFilter()

    def _event(self, event_id, event_type, **overrides):
        data = dict(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="P",
            event_type=event_type,
            status="IN_PROGRESS",
            summary=f"{event_id} summary",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        data.update(overrides)
        return create_event(**data)

    def test_the_three_review_types_never_produce_a_keep_candidate(self):
        cases = (
            ("RV-BLOCKED", "BLOCKED", {"status": "BLOCKED", "blocker": "b"}),
            ("RV-COMPLETED", "COMPLETED", {"status": "COMPLETED"}),
            ("RV-CANCELLED", "CANCELLED", {"status": "CANCELLED"}),
        )
        for event_id, event_type, extra in cases:
            with self.subTest(event_type=event_type):
                result = self.filter.evaluate(self._event(event_id, event_type, **extra))
                self.assertEqual(result.decision, HistoryDecision.REVIEW)

        self.assertEqual(self.repo.list(decision=HistoryDecision.KEEP), [])

    def test_a_review_candidate_is_absent_from_the_generated_daily(self):
        keep_event = self._event("RV-KEEP", "MILESTONE_COMPLETED", milestone="M")
        review_event = self._event("RV-DONE", "COMPLETED", status="COMPLETED")
        self.repo.save(self.filter.evaluate(keep_event).candidate)
        self.repo.save(self.filter.evaluate(review_event).candidate)

        path = generate_daily_history(
            self.repo,
            date(2026, 8, 5),
            output_dir=self.root / "daily",
            generated_at="2026-08-06T11:00:00+09:00",
        )

        text = path.read_text(encoding="utf-8")
        self.assertIn("RV-KEEP", text)
        self.assertNotIn("RV-DONE", text)

    def test_submitting_a_review_does_not_change_the_decision(self):
        """The step that looks like it should promote it."""
        event = self._event("RV-DONE", "COMPLETED", status="COMPLETED")
        self.repo.save(self.filter.evaluate(event).candidate)
        reviewer = RepositoryHistoryReviewer(self.repo)

        reviewer.submit_review(
            "HIST-RV-DONE",
            decision_context="reviewed by the COO",
            lessons_learned="shipped",
        )

        stored = self.repo.get("HIST-RV-DONE")
        self.assertEqual(stored.filter_result, HistoryDecision.REVIEW)
        self.assertEqual(stored.decision_context, "reviewed by the COO")
        # Still not a KEEP candidate, so still not renderable.
        self.assertEqual(self.repo.list(decision=HistoryDecision.KEEP), [])

    def test_a_reviewed_candidate_is_still_absent_after_regenerating(self):
        event = self._event("RV-DONE", "COMPLETED", status="COMPLETED")
        self.repo.save(self.filter.evaluate(event).candidate)
        RepositoryHistoryReviewer(self.repo).submit_review(
            "HIST-RV-DONE", decision_context="reviewed"
        )

        path = generate_daily_history(
            self.repo,
            date(2026, 8, 5),
            output_dir=self.root / "daily",
            generated_at="2026-08-06T11:00:00+09:00",
        )

        self.assertNotIn("RV-DONE", path.read_text(encoding="utf-8"))

    def test_the_candidate_is_not_lost_only_unreachable(self):
        """The distinction that keeps this a visibility gap rather than data
        loss: the record is durable and a human can still read it."""
        event = self._event("RV-DONE", "COMPLETED", status="COMPLETED")
        self.repo.save(self.filter.evaluate(event).candidate)

        stored = self.repo.get("HIST-RV-DONE")

        self.assertIsNotNone(stored)
        self.assertEqual(stored.summary, "RV-DONE summary")
        self.assertEqual(len(self.repo.list(decision=HistoryDecision.REVIEW)), 1)

    def test_reconciliation_does_not_report_it_as_an_orphan(self):
        """Why no existing detector caught this: the candidate *exists*, so
        `find_orphaned_events()` is correct to stay quiet. The gap is that
        nothing else was looking."""
        from history.reconciliation import find_orphaned_events

        processed = self.root / "processed"
        processed.mkdir(parents=True)
        event = self._event("RV-DONE", "COMPLETED", status="COMPLETED")
        (processed / "RV-DONE.json").write_text(event.to_json(), encoding="utf-8")
        self.repo.save(self.filter.evaluate(event).candidate)

        result = find_orphaned_events(
            processed_dir=processed, keep_dir=self.keep, review_dir=self.review
        )

        self.assertTrue(result.is_clean)


class ReviewBacklogInStatusViewTests(unittest.TestCase):
    """The counter docs/05 §50 asks for.

    §50 states the rule as a signal — "REVIEW가 너무 많다 -> 자동화 실패
    신호" — and warns against a structure where "COO가 매일 수십 개의 REVIEW를
    수동 처리해야" work. Neither can be acted on without someone seeing the
    number, and `ops_status.py` referenced `review/` only as an argument to
    the reconciliation scan.
    """

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_review", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _module_with(self, review_files):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        review = module.RUNTIME_DIR / "history_candidates" / "review"
        review.mkdir(parents=True)
        for index in range(review_files):
            (review / f"HIST-R{index}.json").write_text("{}", encoding="utf-8")
        return module

    def _run(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), attention

    def test_the_count_is_always_printed(self):
        printed, _ = self._run(self._module_with(0))

        self.assertIn("검토 대기 Candidate : 0", printed)

    def test_a_waiting_candidate_reaches_attention(self):
        printed, attention = self._run(self._module_with(3))

        self.assertIn("검토 대기 Candidate : 3", printed)
        line = next(item for item in attention if "검토" in item)
        self.assertIn("3건", line)
        self.assertIn("Company History에 없고", line)

    def test_an_empty_review_directory_raises_no_attention(self):
        """A counter that always fires is one nobody reads."""
        _printed, attention = self._run(self._module_with(0))

        self.assertEqual([item for item in attention if "검토" in item], [])

    def test_a_missing_review_directory_is_zero_not_an_error(self):
        """On a machine that has never produced a REVIEW candidate the
        directory does not exist, and the status view must still answer."""
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"

        printed, attention = self._run(module)

        self.assertIn("검토 대기 Candidate : 0", printed)
        self.assertEqual([item for item in attention if "검토" in item], [])


class ReviewAlertClearsWhenTheWorkIsDoneTests(unittest.TestCase):
    """The same defect as C26's Working Copy false alarm, in C22's counter.

    C22 put the whole `review/` pile in ATTENTION. Running `review_cli.py` —
    the documented correct action — does not empty it: `submit_review()`
    fills Decision Context and never touches `filter_result`, so the file
    stays in `review/` (BACKLOG E-20, there is no promotion path). Measured:
    the warning stood after a completed review, and no operator action short
    of moving the file by hand would ever remove it.

    Two different things were being reported as one:

        not yet reviewed   work waiting for a person — doing it clears this
        already reviewed   E-20's open decision — nothing an operator does
                           today clears it, so it is a fact for the block,
                           not an alert

    "Reviewed" is read back from the stored candidate rather than tracked
    separately: at least one Decision Context field set is exactly what
    `submit_review()` leaves behind.

    docs/05 §50's signal ("REVIEW가 너무 많다 -> 자동화 실패 신호") is
    unharmed — the total is still printed, now with the split beside it.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        self.keep = self.runtime / "history_candidates" / "keep"
        self.review = self.runtime / "history_candidates" / "review"
        self.keep.mkdir(parents=True)
        self.review.mkdir(parents=True)
        (self.runtime / "state").mkdir(parents=True)
        self.repo = FileHistoryRepository(keep_dir=self.keep, review_dir=self.review)
        self.filter = HistoryFilter()

    def _park(self, event_id):
        event = create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="P",
            event_type="COMPLETED",
            status="COMPLETED",
            summary=f"{event_id} done",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        self.repo.save(self.filter.evaluate(event).candidate)

    def _view(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_reviewsplit", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [item for item in attention if "검토" in item]

    def test_an_unreviewed_candidate_raises_the_alert(self):
        self._park("RV-1")

        _printed, attention = self._view()

        self.assertTrue(attention)
        self.assertIn("1건", attention[0])

    def test_doing_the_review_clears_the_alert(self):
        """The property the C22 version did not have."""
        self._park("RV-1")
        RepositoryHistoryReviewer(self.repo).submit_review(
            "HIST-RV-1", decision_context="reviewed by the COO"
        )

        _printed, attention = self._view()

        self.assertEqual(attention, [])

    def test_any_decision_context_field_counts_as_reviewed(self):
        """`submit_review()` lets a reviewer fill whichever field applies, so
        the split must not insist on a particular one."""
        for index, field in enumerate(
            ("decision_context", "expected_outcome", "actual_outcome", "lessons_learned")
        ):
            with self.subTest(field=field):
                self.setUp()
                self._park("RV-1")
                RepositoryHistoryReviewer(self.repo).submit_review(
                    "HIST-RV-1", **{field: "filled in"}
                )
                _printed, attention = self._view()
                self.assertEqual(attention, [])

    def test_a_partly_reviewed_pile_alerts_only_on_the_remainder(self):
        self._park("RV-1")
        self._park("RV-2")
        RepositoryHistoryReviewer(self.repo).submit_review(
            "HIST-RV-1", decision_context="done"
        )

        printed, attention = self._view()

        self.assertIn("미검토 1 / 검토됨 1", printed)
        self.assertTrue(attention)
        self.assertIn("1건", attention[0])

    def test_the_total_is_still_printed_for_section_50s_signal(self):
        """docs/05 §50 keys on the pile being large, not on how much of it
        has been read. The number it needs must survive the split."""
        self._park("RV-1")
        self._park("RV-2")
        for event_id in ("HIST-RV-1", "HIST-RV-2"):
            RepositoryHistoryReviewer(self.repo).submit_review(
                event_id, decision_context="done"
            )

        printed, attention = self._view()

        self.assertIn("검토 대기 Candidate : 2", printed)
        self.assertIn("검토됨 2", printed)
        self.assertEqual(attention, [])

    def test_an_unreadable_candidate_counts_as_waiting(self):
        """It needs a person either way, and the view must still answer —
        `FileHistoryRepository.list()` would raise here (BUG-38), which is
        why it is not used."""
        (self.review / "broken.json").write_text("{not json", encoding="utf-8")

        printed, attention = self._view()

        self.assertIn("검토 대기 Candidate : 1", printed)
        self.assertTrue(attention)

    def test_an_empty_review_directory_says_nothing(self):
        printed, attention = self._view()

        self.assertIn("검토 대기 Candidate : 0", printed)
        self.assertEqual(attention, [])


class ReviewedButNotRenderedTests(unittest.TestCase):
    """C33 §3: Decision Context a human wrote that Company History never got.

    Unlike E-17 and A-20, the content lost here is **human-authored** — the
    most expensive kind this pipeline handles, and the only kind no re-run
    can reproduce.

    The capability is fully built and, for a KEEP Candidate, unreachable.
    `review_cli.py` prompts for four fields, `history.review` stores them,
    `daily/markdown.py` renders each one when present — and the timing makes
    the middle unable to reach the end:

        step 5   writes the Candidate
        step 6   renders that date          <- same run, seconds later
        human reviews                       <- the only window that exists
        step 6.5 merges only *new* Events onto a closed date (§38 skips an
                 event_id the file already has)
        step 6   refuses to overwrite an existing Daily file

    Measured end to end before this check existed: review stored True,
    re-read from disk True, `update_daily_history` NO_LATE_EVENTS,
    `generate_daily_history` FileExistsError, Decision Context in the Daily
    file False, Daily file unchanged. `_kept_but_not_rendered()` reported
    clean — correctly; the `event_id` really is in the file.

    Detection only. Both repairs are decisions and are recorded in BACKLOG.
    """

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.keep = root / "keep"
        self.daily = root / "daily"
        self.keep.mkdir(parents=True)
        self.daily.mkdir(parents=True)

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_review", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _candidate(self, event_id="EVT-R1", when="2026-08-10", **review):
        payload = {
            "history_id": f"HIST-{event_id}",
            "event_id": event_id,
            "timestamp": f"{when}T10:00:00+09:00",
            "category": "DECISION",
            "project_id": "PRJ",
            "role": "COO",
            "summary": "CEO approved Closed Beta scope",
            "evidence": [],
            "filter_result": "KEEP",
            "decision_context": None,
            "expected_outcome": None,
            "actual_outcome": None,
            "lessons_learned": None,
        }
        payload.update(review)
        (self.keep / f"HIST-{event_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return payload

    def _daily(self, when="2026-08-10", *, event_id="EVT-R1", extra_lines=()):
        lines = [
            f"# Company History — {when}",
            "",
            "## Decisions",
            "",
            "### PRJ",
            "- CEO approved Closed Beta scope",
            "- Owner: COO",
            f"- Event ID: {event_id}",
        ]
        lines.extend(extra_lines)
        (self.daily / f"{when}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _check(self):
        module = self._module()
        candidates, _unreadable = module._read_keep_candidates(self.keep)
        return module._reviewed_but_not_rendered(candidates, self.daily)

    def test_a_review_the_daily_file_does_not_carry_is_reported(self):
        self._candidate(decision_context="Board asked for 4 weeks; CEO cut it to 2.")
        self._daily()

        stranded = self._check()

        self.assertEqual(len(stranded), 1, stranded)
        self.assertIn("EVT-R1", stranded[0])
        self.assertIn("Decision Context", stranded[0])

    def test_every_missing_field_is_named(self):
        """An operator has to know which of the four to re-enter."""
        self._candidate(
            decision_context="ctx",
            expected_outcome="500 signups",
            lessons_learned="restate the metric",
        )
        self._daily()

        stranded = self._check()

        self.assertEqual(len(stranded), 1)
        for label in ("Decision Context", "Expected Outcome", "Lessons Learned"):
            self.assertIn(label, stranded[0])
        self.assertNotIn("Actual Outcome", stranded[0])

    def test_a_rendered_review_is_not_reported(self):
        """The other direction, and the one that keeps the check usable: a
        Candidate reviewed BEFORE its day was closed renders normally and
        must stay silent."""
        self._candidate(decision_context="ctx")
        self._daily(extra_lines=["- Decision Context: ctx"])

        self.assertEqual(self._check(), ())

    def test_partially_rendered_reports_only_the_missing_half(self):
        self._candidate(decision_context="ctx", actual_outcome="shipped")
        self._daily(extra_lines=["- Decision Context: ctx"])

        stranded = self._check()

        self.assertEqual(len(stranded), 1)
        self.assertIn("Actual Outcome", stranded[0])
        self.assertNotIn("Decision Context", stranded[0])

    def test_an_unreviewed_candidate_is_never_reported(self):
        self._candidate()
        self._daily()

        self.assertEqual(self._check(), ())

    def test_a_day_with_no_daily_file_yet_is_not_a_loss(self):
        """The Scheduler window. This Candidate WILL carry its context when
        the day is closed — reporting it would be an alert that clears
        itself, which this view refuses on principle."""
        self._candidate(decision_context="ctx")

        self.assertEqual(self._check(), ())

    def test_an_empty_string_is_not_a_review(self):
        """`daily/markdown.py` renders each field only `if candidate.<field>`,
        so an empty string is never rendered and must not be looked for."""
        self._candidate(decision_context="", expected_outcome="   ")
        self._daily()

        stranded = self._check()

        self.assertEqual([s for s in stranded if "Decision Context" in s], [])

    def test_a_non_string_review_value_is_ignored_rather_than_crashing(self):
        """A hand-edited or restored Candidate file is a DR path. This view's
        contract is to answer even when the evidence is damaged."""
        self._candidate(decision_context=42, expected_outcome=["a", "b"])
        self._daily()

        self.assertEqual(self._check(), ())

    def test_a_summary_that_imitates_the_label_line_cannot_mask_a_loss(self):
        """The trap C30 hit one function over: the renderer writes a summary
        raw as its block's first bullet, so a summary reading
        `Decision Context: ctx` produces a line identical to the real one.
        Summary lines are excluded from the comparison for exactly this."""
        self._candidate(decision_context="ctx")
        (self.daily / "2026-08-10.md").write_text(
            "\n".join(
                [
                    "# Company History — 2026-08-10",
                    "",
                    "## Decisions",
                    "",
                    "### PRJ",
                    "- Decision Context: ctx",   # the SUMMARY, not the label
                    "- Owner: COO",
                    "- Event ID: EVT-R1",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        stranded = self._check()

        self.assertEqual(len(stranded), 1, stranded)
        self.assertIn("Decision Context", stranded[0])

    def test_a_multi_line_value_is_compared_on_its_label_line(self):
        """The renderer writes the value raw, so only its first line lands on
        the label line. Comparing the whole value would report every
        multi-line review as lost."""
        self._candidate(decision_context="first line\nsecond line")
        self._daily(extra_lines=["- Decision Context: first line", "second line"])

        self.assertEqual(self._check(), ())

    def test_two_candidates_on_one_day_are_judged_separately(self):
        """Presence of *a* label line is not enough — it might belong to the
        other Candidate."""
        self._candidate(event_id="EVT-A", decision_context="ctx-a")
        self._candidate(event_id="EVT-B", decision_context="ctx-b")
        self._daily(extra_lines=["- Decision Context: ctx-a", "- Event ID: EVT-B"])

        stranded = self._check()

        self.assertEqual(len(stranded), 1, stranded)
        self.assertIn("EVT-B", stranded[0])

    def test_the_labels_come_from_the_review_cli_not_a_second_list(self):
        """Asking exactly what the renderer answers. Three modules name these
        four fields; a fourth private copy is how they drift."""
        from review_cli import _REVIEW_FIELDS

        module = self._module()

        self.assertIs(module._REVIEW_FIELDS, _REVIEW_FIELDS)

    def test_the_labels_match_what_the_renderer_actually_writes(self):
        """The pairing is only useful if `daily/markdown.py` writes the same
        strings. Checked against the renderer's source rather than assumed."""
        from review_cli import _REVIEW_FIELDS

        renderer = (
            Path(__file__).resolve().parents[1] / "src" / "daily" / "markdown.py"
        ).read_text(encoding="utf-8")

        for field, label in _REVIEW_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f'f"- {label}: {{candidate.{field}}}"', renderer)

    def test_the_check_is_wired_into_the_history_block(self):
        """A detector nothing runs detects nothing."""
        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("_reviewed_but_not_rendered(keep_candidates, daily_dir)", source)

    def test_the_shared_reader_is_still_read_once(self):
        """The fourth element rides along on the existing single pass. A
        third read of every Candidate would undo the 24.3s -> 5.9s that
        reader exists for."""
        import inspect

        module = self._module()
        source = inspect.getsource(module._read_keep_candidates)

        # The construction, not the word — the docstring names the class too.
        self.assertEqual(source.count("with ThreadPoolExecutor("), 1)
        self.assertIn("_REVIEW_FIELDS", source)


if __name__ == "__main__":
    unittest.main()
