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
        for py_file in history_src.glob("*.py"):
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
        for py_file in history_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
            for token in forbidden:
                self.assertNotIn(
                    token, code_without_docstrings, f"{token} found in {py_file} (outside docstrings)"
                )


if __name__ == "__main__":
    unittest.main()
