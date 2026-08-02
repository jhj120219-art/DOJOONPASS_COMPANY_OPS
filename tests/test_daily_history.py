import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import generate_daily_history, render_daily_markdown  # noqa: E402
from events import Event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
    HistoryFilter,
)


def sample_event(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-MILESTONE-001",
        "timestamp": "2026-08-05T20:00:00+09:00",
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


def make_candidate(**overrides) -> HistoryCandidate:
    fields = {
        "history_id": "HIST-TEST-001",
        "event_id": "TEST-001",
        "timestamp": "2026-08-05T20:00:00+09:00",
        "category": "MILESTONE",
        "project_id": "SEARCH_FRONTEND",
        "role": "CTO_FRONTEND",
        "summary": "Search UI implementation completed",
        "evidence": ("TypeScript PASS",),
        "filter_result": HistoryDecision.KEEP,
    }
    fields.update(overrides)
    return HistoryCandidate(**fields)


class DailyGeneratorTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.keep_dir = root / "keep"
        self.review_dir = root / "review"
        self.daily_dir = root / "daily"
        self.repo = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)
        self.filter = HistoryFilter()

    def _generate(self, target_date, **kwargs):
        return generate_daily_history(
            self.repo, target_date, output_dir=self.daily_dir, **kwargs
        )


class DailyGenerationTests(DailyGeneratorTestCase):
    def test_daily_file_is_created(self):
        event = sample_event(event_id="TEST-DAILY-001")
        self.repo.save(self.filter.evaluate(event).candidate)

        path = self._generate(date(2026, 8, 5))

        self.assertTrue(path.exists())
        self.assertEqual(path.name, "2026-08-05.md")
        self.assertEqual(path.parent, self.daily_dir)

    def test_empty_day_still_generates_a_file(self):
        path = self._generate(date(2026, 8, 5))

        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("No material company history recorded.", content)
        self.assertIn("- Event Count: 0", content)

    def test_existing_daily_file_is_not_silently_overwritten(self):
        self._generate(date(2026, 8, 5))
        with self.assertRaises(FileExistsError):
            self._generate(date(2026, 8, 5))

    def test_overwrite_flag_allows_regeneration(self):
        self._generate(date(2026, 8, 5))
        path = self._generate(date(2026, 8, 5), overwrite=True)
        self.assertTrue(path.exists())

    def test_no_leftover_temp_files(self):
        event = sample_event(event_id="TEST-DAILY-002")
        self.repo.save(self.filter.evaluate(event).candidate)
        self._generate(date(2026, 8, 5))
        self.assertEqual(list(self.daily_dir.glob(".tmp-*")), [])


class KeepOnlyTests(DailyGeneratorTestCase):
    def test_only_keep_candidates_are_included(self):
        keep_event = sample_event(event_id="TEST-KEEP-001")
        review_event = sample_event(
            event_id="TEST-REVIEW-001", event_type="COMPLETED", status="COMPLETED"
        )
        self.repo.save(self.filter.evaluate(keep_event).candidate)
        self.repo.save(self.filter.evaluate(review_event).candidate)

        path = self._generate(date(2026, 8, 5))
        content = path.read_text(encoding="utf-8")

        self.assertIn("TEST-KEEP-001", content)
        self.assertNotIn("TEST-REVIEW-001", content)
        self.assertIn("- Event Count: 1", content)

    def test_review_only_day_renders_as_empty(self):
        review_event = sample_event(
            event_id="TEST-REVIEW-002", event_type="BLOCKED", status="BLOCKED", blocker="x"
        )
        self.repo.save(self.filter.evaluate(review_event).candidate)

        path = self._generate(date(2026, 8, 5))
        content = path.read_text(encoding="utf-8")

        self.assertIn("No material company history recorded.", content)


class DateFilteringTests(DailyGeneratorTestCase):
    def test_candidate_on_a_different_date_is_excluded(self):
        event = sample_event(event_id="TEST-OTHER-DAY-001", timestamp="2026-08-06T09:00:00+09:00")
        self.repo.save(self.filter.evaluate(event).candidate)

        path = self._generate(date(2026, 8, 5))
        content = path.read_text(encoding="utf-8")

        self.assertIn("No material company history recorded.", content)

    def test_late_boundary_timestamp_is_grouped_by_event_date_not_run_date(self):
        event = sample_event(event_id="TEST-LATE-001", timestamp="2026-08-05T23:59:00+09:00")
        self.repo.save(self.filter.evaluate(event).candidate)

        path = self._generate(date(2026, 8, 5))
        content = path.read_text(encoding="utf-8")
        self.assertIn("TEST-LATE-001", content)


class RepositoryUntouchedTests(DailyGeneratorTestCase):
    def test_repository_contents_unchanged_after_generation(self):
        event = sample_event(event_id="TEST-REPO-001")
        self.repo.save(self.filter.evaluate(event).candidate)

        before = self.repo.list()
        self._generate(date(2026, 8, 5))
        after = self.repo.list()

        self.assertEqual(before, after)

    def test_repository_has_no_write_delete_methods_called(self):
        # HistoryRepository only exposes save/get/list; the generator must
        # only ever call list(). Wrapping the repo lets us assert that.
        class SpyRepository:
            def __init__(self, real):
                self._real = real
                self.save_calls = 0
                self.list_calls = 0

            def save(self, *args, **kwargs):
                self.save_calls += 1
                return self._real.save(*args, **kwargs)

            def get(self, *args, **kwargs):
                return self._real.get(*args, **kwargs)

            def list(self, *args, **kwargs):
                self.list_calls += 1
                return self._real.list(*args, **kwargs)

        event = sample_event(event_id="TEST-REPO-002")
        self.repo.save(self.filter.evaluate(event).candidate)

        spy = SpyRepository(self.repo)
        generate_daily_history(spy, date(2026, 8, 5), output_dir=self.daily_dir)

        self.assertEqual(spy.save_calls, 0)
        self.assertGreaterEqual(spy.list_calls, 1)


class MarkdownFormatTests(unittest.TestCase):
    def test_header_matches_spec_format(self):
        markdown = render_daily_markdown(date(2026, 8, 5), [], "2026-08-06T11:00:00+09:00")
        self.assertTrue(markdown.startswith("# DOJOONPASS Company History — 2026-08-05"))

    def test_no_extra_top_level_sections_are_invented(self):
        candidate = make_candidate()
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        section_headers = re.findall(r"^## (.+)$", markdown, re.MULTILINE)
        allowed = {"Summary", "Decisions", "Milestones", "Issues", "Learnings", "Evidence", "Metadata"}
        self.assertTrue(set(section_headers).issubset(allowed), section_headers)

    def test_milestone_candidate_produces_milestones_section(self):
        candidate = make_candidate(category="MILESTONE")
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("## Milestones", markdown)
        self.assertNotIn("## Decisions", markdown)
        self.assertNotIn("## Issues", markdown)
        self.assertNotIn("## Learnings", markdown)

    def test_decision_candidate_produces_decisions_section(self):
        candidate = make_candidate(
            history_id="HIST-TEST-DEC",
            event_id="TEST-DEC-001",
            category="DECISION",
            summary="Closed Beta scope approved by CEO",
        )
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("## Decisions", markdown)

    def test_issue_candidate_produces_issues_section(self):
        candidate = make_candidate(
            history_id="HIST-TEST-ISSUE",
            event_id="TEST-ISSUE-001",
            category="ISSUE",
            summary="Synchronization issue affected data reliability",
        )
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("## Issues", markdown)

    def test_learning_candidate_produces_learnings_section(self):
        candidate = make_candidate(
            history_id="HIST-TEST-LEARN",
            event_id="TEST-LEARN-001",
            category="LEARNING",
            summary="Beta users had difficulty understanding auction status terminology",
        )
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("## Learnings", markdown)

    def test_evidence_section_lists_event_id_and_evidence_text(self):
        candidate = make_candidate(evidence=("TypeScript PASS", "Integration Test PASS"))
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("## Evidence", markdown)
        self.assertIn("TEST-001: TypeScript PASS", markdown)
        self.assertIn("TEST-001: Integration Test PASS", markdown)

    def test_evidence_section_omitted_when_no_evidence(self):
        candidate = make_candidate(evidence=())
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertNotIn("## Evidence", markdown)

    def test_metadata_section_present_with_required_fields(self):
        candidate = make_candidate()
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("## Metadata", markdown)
        self.assertIn("- History Date: 2026-08-05", markdown)
        self.assertIn("- Generated At: 2026-08-06T11:00:00+09:00", markdown)
        self.assertIn("- Source: DOJOONPASS Company Ops", markdown)
        self.assertIn("- Event Count: 1", markdown)

    def test_korean_summary_is_preserved_verbatim(self):
        candidate = make_candidate(summary="검색 UI 구현 완료")
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("검색 UI 구현 완료", markdown)

    def test_summary_text_is_not_altered_or_embellished(self):
        candidate = make_candidate(summary="Search UI implementation completed")
        markdown = render_daily_markdown(date(2026, 8, 5), [candidate], "2026-08-06T11:00:00+09:00")
        self.assertIn("- Search UI implementation completed\n", markdown)


class DailyPathSafetyTests(unittest.TestCase):
    def test_daily_module_does_not_import_collector_transport_or_reporter(self):
        daily_src = Path(__file__).resolve().parents[1] / "src" / "daily"
        forbidden = (
            re.compile(r"^\s*import\s+(collector|transport|reporter)\b", re.MULTILINE),
            re.compile(r"^\s*from\s+(collector|transport|reporter)\b", re.MULTILINE),
        )
        for py_file in daily_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for pattern in forbidden:
                self.assertIsNone(
                    pattern.search(content), f"{py_file} unexpectedly imports {pattern.pattern}"
                )

    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        daily_src = Path(__file__).resolve().parents[1] / "src" / "daily"
        forbidden = ("C:\\Users", "D:\\", "OneDrive\\")
        for py_file in daily_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
            for token in forbidden:
                self.assertNotIn(
                    token, code_without_docstrings, f"{token} found in {py_file} (outside docstrings)"
                )


if __name__ == "__main__":
    unittest.main()
