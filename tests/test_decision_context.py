import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import render_daily_markdown  # noqa: E402
from events import Event  # noqa: E402
from history import HistoryCandidate, HistoryDecision, HistoryFilter  # noqa: E402


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


def make_candidate(**overrides) -> HistoryCandidate:
    fields = {
        "history_id": "HIST-TEST-001",
        "event_id": "TEST-001",
        "timestamp": "2026-08-10T10:00:00+09:00",
        "category": "DECISION",
        "project_id": "DOJOONPASS_PRODUCT",
        "role": "COO",
        "summary": "CEO approved Closed Beta scope",
        "evidence": ("CEO Approval",),
        "filter_result": HistoryDecision.KEEP,
    }
    fields.update(overrides)
    return HistoryCandidate(**fields)


class HistoryCandidateSchemaTests(unittest.TestCase):
    def test_new_fields_default_to_none(self):
        candidate = make_candidate()
        self.assertIsNone(candidate.decision_context)
        self.assertIsNone(candidate.expected_outcome)
        self.assertIsNone(candidate.actual_outcome)
        self.assertIsNone(candidate.lessons_learned)

    def test_new_fields_can_be_set(self):
        candidate = make_candidate(
            decision_context="Beta 범위를 먼저 제한하여 실제 사용자 검증을 우선하기로 결정",
            expected_outcome="초기 운영 리스크 감소",
            actual_outcome=None,
            lessons_learned=None,
        )
        self.assertEqual(
            candidate.decision_context, "Beta 범위를 먼저 제한하여 실제 사용자 검증을 우선하기로 결정"
        )
        self.assertEqual(candidate.expected_outcome, "초기 운영 리스크 감소")


class SerializationRoundTripTests(unittest.TestCase):
    def test_round_trip_with_all_new_fields_none(self):
        candidate = make_candidate()
        restored = HistoryCandidate.from_dict(candidate.to_dict())
        self.assertEqual(candidate, restored)

    def test_round_trip_with_all_new_fields_populated(self):
        candidate = make_candidate(
            decision_context="왜 시작했는지에 대한 맥락",
            expected_outcome="기대했던 결과",
            actual_outcome="실제 결과",
            lessons_learned="배운 점",
        )
        restored = HistoryCandidate.from_dict(candidate.to_dict())
        self.assertEqual(candidate, restored)
        self.assertEqual(restored.decision_context, "왜 시작했는지에 대한 맥락")
        self.assertEqual(restored.lessons_learned, "배운 점")

    def test_to_dict_includes_new_field_keys_even_when_none(self):
        candidate = make_candidate()
        data = candidate.to_dict()
        self.assertIn("decision_context", data)
        self.assertIn("expected_outcome", data)
        self.assertIn("actual_outcome", data)
        self.assertIn("lessons_learned", data)
        self.assertIsNone(data["decision_context"])

    def test_from_dict_tolerates_missing_new_field_keys(self):
        # a candidate JSON file written before this Phase (no decision
        # context keys at all) must still load without error.
        legacy_data = {
            "history_id": "HIST-LEGACY-001",
            "event_id": "TEST-LEGACY-001",
            "timestamp": "2026-08-01T20:00:00+09:00",
            "category": "MILESTONE",
            "project_id": "SEARCH_FRONTEND",
            "role": "CTO_FRONTEND",
            "summary": "Search UI implementation completed",
            "evidence": ["TypeScript PASS"],
            "filter_result": "KEEP",
        }
        candidate = HistoryCandidate.from_dict(legacy_data)
        self.assertIsNone(candidate.decision_context)
        self.assertIsNone(candidate.expected_outcome)


class HistoryFilterNotBrokenTests(unittest.TestCase):
    """Confirms Phase 4.7 does not break History Filter (task requirement 4)."""

    def setUp(self):
        self.filter = HistoryFilter()

    def test_filter_still_produces_valid_decisions(self):
        event = sample_event()
        result = self.filter.evaluate(event)
        self.assertEqual(result.decision, HistoryDecision.KEEP)
        self.assertEqual(result.candidate.category, "DECISION")

    def test_filter_output_has_no_decision_context_yet(self):
        # Event Schema was deliberately not changed this Phase (see
        # report), so HistoryFilter has no source data for these fields.
        event = sample_event()
        candidate = self.filter.evaluate(event).candidate
        self.assertIsNone(candidate.decision_context)
        self.assertIsNone(candidate.expected_outcome)
        self.assertIsNone(candidate.actual_outcome)
        self.assertIsNone(candidate.lessons_learned)

    def test_filter_all_rule_branches_still_work(self):
        # a lightweight regression sweep across the existing rule table,
        # confirming the new optional fields didn't disturb any branch.
        cases = [
            ("DECISION_APPROVED", "IN_PROGRESS", HistoryDecision.KEEP),
            ("MILESTONE_COMPLETED", "IN_PROGRESS", HistoryDecision.KEEP),
            ("ISSUE_RESOLVED", "IN_PROGRESS", HistoryDecision.KEEP),
            ("STARTED", "IN_PROGRESS", HistoryDecision.DROP),
            ("RESUMED", "IN_PROGRESS", HistoryDecision.DROP),
            ("BLOCKED", "BLOCKED", HistoryDecision.REVIEW),
            ("COMPLETED", "COMPLETED", HistoryDecision.REVIEW),
            ("CANCELLED", "CANCELLED", HistoryDecision.REVIEW),
        ]
        for event_type, status, expected in cases:
            with self.subTest(event_type=event_type):
                event = sample_event(
                    event_id=f"TEST-{event_type}-001",
                    event_type=event_type,
                    status=status,
                    blocker="x" if event_type == "BLOCKED" else None,
                    history_candidate=(event_type not in ("STARTED", "RESUMED")),
                )
                result = self.filter.evaluate(event)
                self.assertEqual(result.decision, expected)


class DailyMarkdownRenderingTests(unittest.TestCase):
    def test_decision_context_line_rendered_when_present(self):
        candidate = make_candidate(decision_context="Beta 범위를 제한하여 검증을 우선함")
        markdown = render_daily_markdown(date(2026, 8, 10), [candidate], "2026-08-11T11:00:00+09:00")
        self.assertIn("- Decision Context: Beta 범위를 제한하여 검증을 우선함", markdown)

    def test_all_four_new_lines_rendered_when_present(self):
        candidate = make_candidate(
            decision_context="context",
            expected_outcome="expected",
            actual_outcome="actual",
            lessons_learned="lessons",
        )
        markdown = render_daily_markdown(date(2026, 8, 10), [candidate], "2026-08-11T11:00:00+09:00")
        self.assertIn("- Decision Context: context", markdown)
        self.assertIn("- Expected Outcome: expected", markdown)
        self.assertIn("- Actual Outcome: actual", markdown)
        self.assertIn("- Lessons Learned: lessons", markdown)

    def test_new_lines_omitted_when_absent_not_fabricated(self):
        candidate = make_candidate()  # all four fields None
        markdown = render_daily_markdown(date(2026, 8, 10), [candidate], "2026-08-11T11:00:00+09:00")
        self.assertNotIn("Decision Context:", markdown)
        self.assertNotIn("Expected Outcome:", markdown)
        self.assertNotIn("Actual Outcome:", markdown)
        self.assertNotIn("Lessons Learned:", markdown)

    def test_partial_fields_render_only_the_ones_present(self):
        candidate = make_candidate(decision_context="context only", actual_outcome=None)
        markdown = render_daily_markdown(date(2026, 8, 10), [candidate], "2026-08-11T11:00:00+09:00")
        self.assertIn("- Decision Context: context only", markdown)
        self.assertNotIn("Expected Outcome:", markdown)
        self.assertNotIn("Actual Outcome:", markdown)
        self.assertNotIn("Lessons Learned:", markdown)

    def test_existing_daily_output_unchanged_for_candidates_without_decision_context(self):
        # exact same expectation as Phase 4.4's own format test — this
        # Phase must not alter output for candidates that don't carry the
        # new fields (which, currently, is all of them from HistoryFilter).
        candidate = make_candidate(category="MILESTONE", summary="Search UI implementation completed")
        markdown = render_daily_markdown(date(2026, 8, 10), [candidate], "2026-08-11T11:00:00+09:00")
        self.assertIn("- Search UI implementation completed\n", markdown)
        self.assertNotIn("Decision Context:", markdown)


if __name__ == "__main__":
    unittest.main()
