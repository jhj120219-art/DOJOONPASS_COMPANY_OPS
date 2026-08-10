"""Per-role Daily view tests (src/daily/role_summary.py).

The brief's Daily Report shape wants each role's day answered separately,
including "활동이 없는 역할은 정상적으로 NO_ACTIVITY로 처리한다". What is
pinned here is that the grouping agrees with the Daily History file it is
derived from, and that a silent role is reported as silent rather than
omitted.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import ROLE_ORDER, build_role_summary, generate_daily_history  # noqa: E402
from events import ROLES  # noqa: E402
from history import HistoryCandidate, HistoryDecision  # noqa: E402
from history.file_repository import FileHistoryRepository  # noqa: E402


def candidate(
    *, index, role, category="MILESTONE", day=date(2026, 8, 8), hour=10, project="PRJ"
):
    return HistoryCandidate(
        history_id=f"HIST-ROLE-{index:03d}",
        event_id=f"ROLE-{index:03d}",
        timestamp=f"{day.isoformat()}T{hour:02d}:00:00+09:00",
        category=category,
        project_id=project,
        role=role,
        summary=f"{role} did thing {index}",
        evidence=(),
        filter_result=HistoryDecision.KEEP,
    )


class RoleSummaryTests(unittest.TestCase):
    def test_every_schema_role_is_always_present(self):
        summary = build_role_summary([], date(2026, 8, 8))

        self.assertEqual({a.role for a in summary.roles}, set(ROLES))
        self.assertEqual([a.role for a in summary.roles][: len(ROLE_ORDER)], list(ROLE_ORDER))

    def test_a_silent_role_is_reported_as_silent_not_omitted(self):
        summary = build_role_summary([candidate(index=1, role="CTO_BACKEND")], date(2026, 8, 8))

        self.assertEqual(summary.active_roles, ("CTO_BACKEND",))
        self.assertEqual(set(summary.silent_roles), set(ROLES) - {"CTO_BACKEND"})
        self.assertFalse(summary.for_role("CMO").has_activity)
        self.assertEqual(summary.for_role("CMO").candidates, ())

    def test_a_day_where_nobody_worked_has_every_role_silent(self):
        summary = build_role_summary([], date(2026, 8, 8))

        self.assertEqual(summary.active_roles, ())
        self.assertEqual(set(summary.silent_roles), set(ROLES))

    def test_candidates_are_grouped_by_role_and_category(self):
        candidates = [
            candidate(index=1, role="CTO_BACKEND", category="MILESTONE"),
            candidate(index=2, role="CTO_BACKEND", category="ISSUE"),
            candidate(index=3, role="CMO", category="MILESTONE"),
            candidate(index=4, role="COO", category="DECISION"),
        ]

        summary = build_role_summary(candidates, date(2026, 8, 8))

        backend = summary.for_role("CTO_BACKEND")
        self.assertEqual(len(backend.candidates), 2)
        self.assertEqual(len(backend.of_category("MILESTONE")), 1)
        self.assertEqual(len(backend.of_category("ISSUE")), 1)
        self.assertEqual(backend.of_category("LEARNING"), ())
        self.assertEqual(len(summary.for_role("COO").of_category("DECISION")), 1)

    def test_only_the_target_date_is_included(self):
        candidates = [
            candidate(index=1, role="CMO", day=date(2026, 8, 8)),
            candidate(index=2, role="CMO", day=date(2026, 8, 9)),
        ]

        summary = build_role_summary(candidates, date(2026, 8, 8))

        self.assertEqual(len(summary.for_role("CMO").candidates), 1)
        self.assertEqual(summary.for_role("CMO").candidates[0].event_id, "ROLE-001")

    def test_a_role_s_candidates_are_ordered_by_timestamp(self):
        candidates = [
            candidate(index=1, role="CMO", hour=18),
            candidate(index=2, role="CMO", hour=9),
        ]

        summary = build_role_summary(candidates, date(2026, 8, 8))

        self.assertEqual(
            [c.event_id for c in summary.for_role("CMO").candidates],
            ["ROLE-002", "ROLE-001"],
        )

    def test_projects_are_deduplicated_in_first_seen_order(self):
        candidates = [
            candidate(index=1, role="CMO", hour=9, project="CONTENT_OS"),
            candidate(index=2, role="CMO", hour=10, project="BRAND"),
            candidate(index=3, role="CMO", hour=11, project="CONTENT_OS"),
        ]

        summary = build_role_summary(candidates, date(2026, 8, 8))

        self.assertEqual(summary.for_role("CMO").projects, ("CONTENT_OS", "BRAND"))

    def test_an_unknown_role_would_still_be_reported(self):
        """A role added to events.ROLES must not silently vanish from every
        report just because ROLE_ORDER was not updated with it."""
        import daily.role_summary as module

        original = module.ROLE_ORDER
        try:
            module.ROLE_ORDER = ("CTO_BACKEND",)
            summary = module.build_role_summary([], date(2026, 8, 8))
        finally:
            module.ROLE_ORDER = original

        self.assertEqual({a.role for a in summary.roles}, set(ROLES))


class AgreementWithTheDailyFileTests(unittest.TestCase):
    """The summary and the rendered Daily History must never describe
    different sets of work — same date rule, same candidates."""

    def test_the_summary_covers_exactly_what_the_daily_file_renders(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")

        candidates = [
            candidate(index=1, role="CTO_BACKEND", day=date(2026, 8, 8)),
            candidate(index=2, role="CMO", day=date(2026, 8, 8)),
            candidate(index=3, role="COO", day=date(2026, 8, 9)),
        ]
        for item in candidates:
            repo.save(item)

        path = generate_daily_history(repo, date(2026, 8, 8), output_dir=root / "daily")
        markdown = path.read_text(encoding="utf-8")

        summary = build_role_summary(
            repo.list(decision=HistoryDecision.KEEP), date(2026, 8, 8)
        )

        rendered_ids = {c.event_id for role in summary.roles for c in role.candidates}
        self.assertEqual(rendered_ids, {"ROLE-001", "ROLE-002"})
        for event_id in rendered_ids:
            self.assertIn(event_id, markdown)
        self.assertNotIn("ROLE-003", markdown)
        self.assertFalse(summary.for_role("COO").has_activity)


if __name__ == "__main__":
    unittest.main()
