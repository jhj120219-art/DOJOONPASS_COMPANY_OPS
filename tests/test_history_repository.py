import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryDecision,
    HistoryFilter,
    HistoryRepository,
)


def sample_event(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-MILESTONE-001",
        "timestamp": "2026-08-01T20:00:00+09:00",
        "source": "DESKTOP_3",
        "role": "CTO_FRONTEND",
        "project_id": "SEARCH_FRONTEND",
        "event_type": "MILESTONE_COMPLETED",
        "status": "IN_PROGRESS",
        "milestone": "Search UI",
        "summary": "Search UI implementation completed",
        "blocker": None,
        "evidence": ["TypeScript PASS"],
        "history_candidate": True,
    }
    data.update(overrides)
    return Event.from_dict(data)


class RepositoryTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.keep_dir = root / "keep"
        self.review_dir = root / "review"
        self.repo = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)
        self.filter = HistoryFilter()


class KeepSaveTests(RepositoryTestCase):
    def test_keep_candidate_is_saved(self):
        event = sample_event(event_id="TEST-KEEP-001")
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.KEEP)

        stored = self.repo.save(result.candidate)

        self.assertTrue(stored)
        self.assertTrue((self.keep_dir / f"{result.candidate.history_id}.json").exists())
        self.assertFalse(self.review_dir.exists())


class ReviewSaveTests(RepositoryTestCase):
    def test_review_candidate_is_saved(self):
        event = sample_event(
            event_id="TEST-REVIEW-001",
            event_type="COMPLETED",
            status="COMPLETED",
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.REVIEW)

        stored = self.repo.save(result.candidate)

        self.assertTrue(stored)
        self.assertTrue((self.review_dir / f"{result.candidate.history_id}.json").exists())
        self.assertFalse(self.keep_dir.exists())


class DropNotSavedTests(RepositoryTestCase):
    def test_drop_candidate_is_not_saved(self):
        event = sample_event(
            event_id="TEST-DROP-001",
            event_type="STARTED",
            status="IN_PROGRESS",
            evidence=[],
            history_candidate=False,
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.DROP)

        stored = self.repo.save(result.candidate)

        self.assertFalse(stored)
        self.assertFalse(self.keep_dir.exists())
        self.assertFalse(self.review_dir.exists())
        self.assertEqual(self.repo.list(), [])


class JsonStorageTests(RepositoryTestCase):
    def test_saved_file_is_valid_json_matching_candidate(self):
        event = sample_event(event_id="TEST-JSON-001", summary="검색 UI 구현 완료")
        result = self.filter.evaluate(event)
        self.repo.save(result.candidate)

        path = self.keep_dir / f"{result.candidate.history_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["event_id"], "TEST-JSON-001")
        self.assertEqual(data["summary"], "검색 UI 구현 완료")
        self.assertEqual(data["filter_result"], "KEEP")

    def test_get_round_trips_the_same_candidate(self):
        event = sample_event(event_id="TEST-JSON-002")
        result = self.filter.evaluate(event)
        self.repo.save(result.candidate)

        fetched = self.repo.get(result.candidate.history_id)
        self.assertEqual(fetched, result.candidate)


class AtomicSaveTests(RepositoryTestCase):
    def test_no_leftover_temp_files_after_save(self):
        event = sample_event(event_id="TEST-ATOMIC-001")
        result = self.filter.evaluate(event)
        self.repo.save(result.candidate)

        remaining = list(self.keep_dir.glob(".tmp-*"))
        self.assertEqual(remaining, [])

    def test_duplicate_save_without_overwrite_is_rejected(self):
        event = sample_event(event_id="TEST-ATOMIC-002")
        result = self.filter.evaluate(event)
        self.repo.save(result.candidate)

        with self.assertRaises(FileExistsError):
            self.repo.save(result.candidate)

    def test_duplicate_save_with_overwrite_succeeds(self):
        event = sample_event(event_id="TEST-ATOMIC-003")
        result = self.filter.evaluate(event)
        self.repo.save(result.candidate)
        stored_again = self.repo.save(result.candidate, overwrite=True)
        self.assertTrue(stored_again)

    def test_a_crash_during_the_atomic_replace_leaves_nothing_behind(self):
        """Found via `python -m trace --count` to have zero coverage anywhere
        in the suite: the cleanup half of the atomic write (`except
        BaseException: os.remove(tmp_path); raise`) had never actually been
        exercised by a real failure during `os.replace()` — every other
        AtomicSaveTests case checks the happy path's absence of leftovers,
        not what happens when the commit itself is interrupted."""
        import history.file_repository as repo_module

        event = sample_event(event_id="TEST-ATOMIC-CRASH-001")
        result = self.filter.evaluate(event)

        original_replace = repo_module.os.replace
        repo_module.os.replace = lambda *a, **kw: (_ for _ in ()).throw(
            KeyboardInterrupt("simulated crash before the atomic replace commits")
        )
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.repo.save(result.candidate)
        finally:
            repo_module.os.replace = original_replace

        self.assertEqual(list(self.keep_dir.glob("*.json")), [])
        self.assertEqual(list(self.keep_dir.glob(".tmp-*")), [])

        # Recovery: saving again (no crash this time) succeeds cleanly.
        self.assertTrue(self.repo.save(result.candidate))
        self.assertIsNotNone(self.repo.get(result.candidate.history_id))


class RepositoryLookupTests(RepositoryTestCase):
    def test_get_missing_candidate_returns_none(self):
        self.assertIsNone(self.repo.get("HIST-DOES-NOT-EXIST"))

    def test_list_returns_all_stored_candidates(self):
        keep_event = sample_event(event_id="TEST-LIST-KEEP-001")
        review_event = sample_event(
            event_id="TEST-LIST-REVIEW-001", event_type="COMPLETED", status="COMPLETED"
        )
        self.repo.save(self.filter.evaluate(keep_event).candidate)
        self.repo.save(self.filter.evaluate(review_event).candidate)

        all_candidates = self.repo.list()
        self.assertEqual(len(all_candidates), 2)

    def test_list_filters_by_decision(self):
        keep_event = sample_event(event_id="TEST-LIST-KEEP-002")
        review_event = sample_event(
            event_id="TEST-LIST-REVIEW-002", event_type="COMPLETED", status="COMPLETED"
        )
        self.repo.save(self.filter.evaluate(keep_event).candidate)
        self.repo.save(self.filter.evaluate(review_event).candidate)

        keep_only = self.repo.list(decision=HistoryDecision.KEEP)
        review_only = self.repo.list(decision=HistoryDecision.REVIEW)

        self.assertEqual([c.event_id for c in keep_only], ["TEST-LIST-KEEP-002"])
        self.assertEqual([c.event_id for c in review_only], ["TEST-LIST-REVIEW-002"])

    def test_list_for_drop_is_always_empty(self):
        self.assertEqual(self.repo.list(decision=HistoryDecision.DROP), [])

    def test_list_on_empty_repository_is_empty(self):
        self.assertEqual(self.repo.list(), [])


class FilterToRepositoryIntegrationTests(RepositoryTestCase):
    def test_full_pipeline_buckets_correctly(self):
        events = [
            sample_event(event_id="TEST-PIPE-KEEP", event_type="DECISION_APPROVED", milestone=None),
            sample_event(
                event_id="TEST-PIPE-REVIEW", event_type="BLOCKED", status="BLOCKED", blocker="x"
            ),
            sample_event(
                event_id="TEST-PIPE-DROP",
                event_type="RESUMED",
                status="IN_PROGRESS",
                blocker=None,
                history_candidate=False,
            ),
        ]

        for event in events:
            result = self.filter.evaluate(event)
            self.repo.save(result.candidate)

        self.assertEqual(len(self.repo.list(decision=HistoryDecision.KEEP)), 1)
        self.assertEqual(len(self.repo.list(decision=HistoryDecision.REVIEW)), 1)
        self.assertEqual(len(self.repo.list()), 2)


class RepositoryBoundaryTests(unittest.TestCase):
    def test_repository_is_abstract(self):
        with self.assertRaises(TypeError):
            HistoryRepository()

    def test_history_module_does_not_import_collector_transport_or_reporter(self):
        history_src = Path(__file__).resolve().parents[1] / "src" / "history"
        forbidden = (
            re.compile(r"^\s*import\s+(collector|transport|reporter)\b", re.MULTILINE),
            re.compile(r"^\s*from\s+(collector|transport|reporter)\b", re.MULTILINE),
        )
        # Materialised and asserted non-empty first: an empty glob (a renamed
        # or moved package) would make every assertion below run zero times and
        # the test pass while enforcing nothing.
        sources = sorted(history_src.glob("*.py"))
        self.assertTrue(sources, f"no sources under {history_src}")
        for py_file in sources:
            content = py_file.read_text(encoding="utf-8")
            for pattern in forbidden:
                self.assertIsNone(
                    pattern.search(content), f"{py_file} unexpectedly imports {pattern.pattern}"
                )

    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        # Module docstrings may legitimately *discuss* the (not-yet-used)
        # Desktop 4 Local Master path for context; only executable code
        # must never construct such a path itself.
        history_src = Path(__file__).resolve().parents[1] / "src" / "history"
        forbidden = ("C:\\Users", "D:\\", "OneDrive\\")
        # Materialised and asserted non-empty first: an empty glob (a renamed
        # or moved package) would make every assertion below run zero times and
        # the test pass while enforcing nothing.
        sources = sorted(history_src.glob("*.py"))
        self.assertTrue(sources, f"no sources under {history_src}")
        for py_file in sources:
            content = py_file.read_text(encoding="utf-8")
            code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
            for token in forbidden:
                self.assertNotIn(
                    token, code_without_docstrings, f"{token} found in {py_file} (outside docstrings)"
                )


class AnUnreadableCandidateDirectoryIsNotAnEmptyOneTests(RepositoryTestCase):
    """BUG: a `keep/` that could not be listed rendered days of Company
    History as empty, and the run reported COMPLETED.

    `list()` matched with `Path.glob("*.json")`, and `glob()` swallows the
    `OSError` it hits while scanning. Measured, one stored KEEP Candidate in
    a directory denied to this user:

        repository.list(KEEP)  ->  []            no error
        os.listdir()           ->  PermissionError (5)

    An empty list is not "no Company History for this day" -- it is what
    `scheduler.run_once()` renders as an empty day before advancing its
    watermark past it. Measured end to end through the real scheduler:

        status COMPLETED   generated ['2026-08-29', '2026-08-30']
        2026-08-29.md      "No material company history recorded."

    `list()` takes no date, so one unreadable directory does that to
    **every** pending date in the batch.

    The repair is only to let the failure out. `scheduler.run_once()`
    already wraps this call and returns FAILED without generating anything,
    and its own comment states the rule the call could not honour:
    "repository.list()도 다른 단계와 동일하게 실패를 감춰서는 안 된다".
    """

    def _deny_listing(self, directory):
        """Deny *list directory* only, and restore afterwards.

        `(RD)` rather than `(F)`: a full deny also removes the right to read
        the ACL, so the restore then fails (measured -- icacls rc=5) and the
        fixture leaves an unreadable directory behind.
        """
        import os
        import subprocess
        import sys

        user = os.environ.get("USERNAME")
        if sys.platform != "win32" or not user:
            self.skipTest("directory-listing denial is applied with icacls")
        denied = subprocess.run(
            ["icacls", str(directory), "/deny", f"{user}:(RD)"],
            capture_output=True, text=True,
        )
        if denied.returncode != 0:
            self.skipTest(f"could not deny listing: {denied.stdout.strip()[:80]}")
        self.addCleanup(
            subprocess.run,
            ["icacls", str(directory), "/remove:d", user],
            capture_output=True,
        )
        try:
            os.listdir(directory)
        except OSError:
            return
        self.skipTest("the deny did not take effect; the test would prove nothing")

    def _stored_candidate(self):
        result = self.filter.evaluate(sample_event(event_id="TEST-UNREADABLE-001"))
        self.repo.save(result.candidate)
        return result.candidate

    def test_listing_a_denied_directory_raises_instead_of_returning_nothing(self):
        self._stored_candidate()
        self.assertEqual(len(self.repo.list(HistoryDecision.KEEP)), 1)

        self._deny_listing(self.keep_dir)

        with self.assertRaises(OSError):
            self.repo.list(HistoryDecision.KEEP)

    def test_a_directory_that_is_simply_absent_is_still_not_an_error(self):
        """The other direction, and what stops this being a mute button. A
        `review/` nothing has ever written to does not exist, and asking for
        REVIEW Candidates there is an ordinary empty answer -- if that
        raised, every clean run would fail."""
        self.assertFalse(self.review_dir.exists())

        self.assertEqual(self.repo.list(HistoryDecision.REVIEW), [])

    def test_an_empty_directory_is_still_an_empty_answer(self):
        """A directory that exists and holds nothing is a real empty day."""
        self.keep_dir.mkdir(parents=True, exist_ok=True)

        self.assertEqual(self.repo.list(HistoryDecision.KEEP), [])

    def test_the_scheduler_writes_no_empty_day_and_holds_its_watermark(self):
        """The consequence, driven through the real scheduler rather than
        argued: the failure this surfaces has to reach the contract that was
        already written for it."""
        import json
        from datetime import date, datetime, timedelta, timezone

        from scheduler.scheduler import run_once

        candidate = self._stored_candidate()
        target = datetime.fromisoformat(candidate.timestamp).date()
        root = self.keep_dir.parent
        daily = root / "daily"; daily.mkdir()
        state = root / "scheduler_state.json"
        self._deny_listing(self.keep_dir)

        result = run_once(
            self.repo,
            history_start_date=target,
            now=datetime.combine(
                target + timedelta(days=2), datetime.min.time(),
                tzinfo=timezone(timedelta(hours=9)),
            ).replace(hour=11),
            state_path=state,
            lock_path=root / "scheduler.lock",
            daily_output_dir=daily,
        )

        self.assertEqual(result.status.name, "FAILED")
        self.assertEqual(result.generated_dates, ())
        self.assertFalse(
            (daily / f"{target.isoformat()}.md").exists(),
            "an empty day was written for a date whose Candidates were unreadable",
        )
        if state.exists():
            self.assertIsNone(
                json.loads(state.read_text(encoding="utf-8")).get("last_generated_date")
            )

    def test_the_work_arrives_once_the_directory_is_readable_again(self):
        """Failing loudly is only right if the Company History still gets
        written afterwards."""
        import os
        import subprocess
        from datetime import date, datetime, timedelta, timezone

        from scheduler.scheduler import run_once

        candidate = self._stored_candidate()
        target = datetime.fromisoformat(candidate.timestamp).date()
        root = self.keep_dir.parent
        daily = root / "daily"; daily.mkdir()
        now = datetime.combine(
            target + timedelta(days=2), datetime.min.time(),
            tzinfo=timezone(timedelta(hours=9)),
        ).replace(hour=11)
        kwargs = dict(
            history_start_date=target, now=now,
            state_path=root / "scheduler_state.json",
            lock_path=root / "scheduler.lock", daily_output_dir=daily,
        )
        self._deny_listing(self.keep_dir)
        self.assertEqual(run_once(self.repo, **kwargs).status.name, "FAILED")

        subprocess.run(
            ["icacls", str(self.keep_dir), "/remove:d", os.environ["USERNAME"]],
            capture_output=True,
        )
        second = run_once(self.repo, **kwargs)

        self.assertEqual(second.status.name, "COMPLETED")
        self.assertIn(target, second.generated_dates)
        self.assertIn(
            candidate.summary,
            (daily / f"{target.isoformat()}.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
