import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event  # noqa: E402
from history import HistoryDecision, HistoryFilter  # noqa: E402


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


class KeepDecisionTests(unittest.TestCase):
    def setUp(self):
        self.filter = HistoryFilter()

    def test_decision_approved_is_keep(self):
        event = sample_event(
            event_id="TEST-DECISION-001",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            milestone=None,
            summary="Closed Beta scope approved by CEO",
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.KEEP)
        self.assertEqual(result.candidate.filter_result, HistoryDecision.KEEP)
        self.assertEqual(result.candidate.category, "DECISION")

    def test_milestone_completed_is_keep(self):
        event = sample_event()
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.KEEP)
        self.assertEqual(result.candidate.category, "MILESTONE")

    def test_issue_resolved_is_keep(self):
        event = sample_event(
            event_id="TEST-ISSUE-001",
            event_type="ISSUE_RESOLVED",
            status="IN_PROGRESS",
            milestone=None,
            summary="auction_item synchronization issue resolved",
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.KEEP)
        self.assertEqual(result.candidate.category, "ISSUE")


class DropDecisionTests(unittest.TestCase):
    def setUp(self):
        self.filter = HistoryFilter()

    def test_started_is_drop(self):
        event = sample_event(
            event_id="TEST-START-001",
            event_type="STARTED",
            status="IN_PROGRESS",
            evidence=[],
            history_candidate=False,
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.DROP)

    def test_resumed_is_drop(self):
        event = sample_event(
            event_id="TEST-RESUME-001",
            event_type="RESUMED",
            status="IN_PROGRESS",
            blocker=None,
            history_candidate=False,
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.DROP)

    def test_started_is_drop_even_if_history_candidate_forced_true(self):
        # STARTED normally ships with history_candidate=false, but the
        # automatic DROP rule must hold even if some caller sets it true.
        event = sample_event(
            event_id="TEST-START-002",
            event_type="STARTED",
            status="IN_PROGRESS",
            evidence=[],
            history_candidate=True,
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.DROP)


class HistoryCandidateFalseTests(unittest.TestCase):
    def setUp(self):
        self.filter = HistoryFilter()

    def test_history_candidate_false_is_drop_regardless_of_event_type(self):
        event = sample_event(
            event_id="TEST-COMPLETE-001",
            event_type="COMPLETED",
            status="COMPLETED",
            history_candidate=False,
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.DROP)
        self.assertEqual(result.reason, "history_candidate is false")

    def test_history_candidate_false_short_circuits_keep_rule(self):
        event = sample_event(
            event_id="TEST-DECISION-002",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            milestone=None,
            summary="Some decision",
            history_candidate=False,
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.DROP)


class ReviewDecisionTests(unittest.TestCase):
    def setUp(self):
        self.filter = HistoryFilter()

    def test_blocked_is_review(self):
        event = sample_event(
            event_id="TEST-BLOCK-001",
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="auction_item synchronization mismatch",
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.REVIEW)
        self.assertEqual(result.candidate.category, "ISSUE")

    def test_completed_is_review(self):
        event = sample_event(
            event_id="TEST-COMPLETE-001",
            event_type="COMPLETED",
            status="COMPLETED",
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.REVIEW)
        self.assertEqual(result.candidate.category, "MILESTONE")

    def test_cancelled_is_review(self):
        event = sample_event(
            event_id="TEST-CANCEL-001",
            event_type="CANCELLED",
            status="CANCELLED",
            milestone=None,
            summary="Feature deprecated due to strategy change",
        )
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.REVIEW)
        self.assertIsNone(result.candidate.category)


class HistoryCandidateShapeTests(unittest.TestCase):
    def setUp(self):
        self.filter = HistoryFilter()

    def test_history_id_is_traceable_to_event_id(self):
        event = sample_event(event_id="TEST-MILESTONE-042")
        result = self.filter.evaluate(event)
        self.assertEqual(result.candidate.event_id, "TEST-MILESTONE-042")
        self.assertIn("TEST-MILESTONE-042", result.candidate.history_id)

    def test_candidate_fields_come_from_the_original_event(self):
        event = sample_event(
            project_id="SEARCH_FRONTEND",
            role="CTO_FRONTEND",
            summary="Search UI implementation completed",
            evidence=["TypeScript PASS"],
            timestamp="2026-08-01T20:00:00+09:00",
        )
        result = self.filter.evaluate(event)
        candidate = result.candidate
        self.assertEqual(candidate.project_id, "SEARCH_FRONTEND")
        self.assertEqual(candidate.role, "CTO_FRONTEND")
        self.assertEqual(candidate.summary, "Search UI implementation completed")
        self.assertEqual(candidate.evidence, ("TypeScript PASS",))
        self.assertEqual(candidate.timestamp, "2026-08-01T20:00:00+09:00")

    def test_candidate_does_not_invent_content_not_in_the_event(self):
        event = sample_event(summary="Search UI implementation completed")
        result = self.filter.evaluate(event)
        self.assertEqual(result.candidate.summary, event.summary)


class HistoryFilterBoundaryTests(unittest.TestCase):
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
        # Desktop 4 Local Master path for context, e.g. "D:\DOJOONPASS_COO\
        # history\ ... that path is intentionally not used yet." Only the
        # executable code below the docstring must never construct such a
        # path itself.
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


if __name__ == "__main__":
    unittest.main()
