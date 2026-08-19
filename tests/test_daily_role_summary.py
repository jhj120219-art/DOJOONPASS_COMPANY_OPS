"""Per-role Daily view tests (src/daily/role_summary.py).

The brief's Daily Report shape wants each role's day answered separately,
including "활동이 없는 역할은 정상적으로 NO_ACTIVITY로 처리한다". What is
pinned here is that the grouping agrees with the Daily History file it is
derived from, and that a silent role is reported as silent rather than
omitted.
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily import ROLE_ORDER, build_role_summary, generate_daily_history  # noqa: E402
from daily.late_events import select_late_candidates  # noqa: E402
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


class MixedOffsetOrderingTests(unittest.TestCase):
    """Company History must be ordered by when things happened, not by how
    the timestamp happens to spell.

    Every renderer here orders a day's items by `timestamp`, and all three
    used the raw string. That is correct only while every Event carries the
    same UTC offset, and the schema deliberately does not require one —
    `test_spec_conformance.py::test_the_schema_accepts_a_non_kst_offset`
    pins `+00:00` / `-05:00` / `+05:30` as accepted, and a Signal may state
    its own `timestamp`. `app/desktop_activity._before()` had already
    stopped string-comparing this exact field, and said why; `daily/` had
    not.

        2026-08-05T01:00:00+00:00   01:00 UTC   happened second
        2026-08-05T09:00:00+09:00   00:00 UTC   happened first

    Both fall on 2026-08-05 in their own offsets, so both land in the same
    Daily file, and as text the second one sorts first. The Source of Truth
    document then lists the day's events in the wrong order.

    Grouping is deliberately untouched: a candidate still belongs to the day
    its own offset says (docs/06 §12 — the day the work happened where it
    happened). Only the order within that day is fixed.
    """

    EARLIER = "2026-08-05T09:00:00+09:00"  # 00:00 UTC
    LATER = "2026-08-05T01:00:00+00:00"  # 01:00 UTC

    def _candidate(self, event_id, timestamp, role="CTO_BACKEND"):
        return HistoryCandidate(
            history_id=f"HIST-{event_id}",
            event_id=event_id,
            timestamp=timestamp,
            category="MILESTONE",
            project_id="SEARCH_BACKEND",
            role=role,
            summary=f"{event_id} work",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def test_the_string_order_really_is_the_wrong_order(self):
        """The premise, pinned so the fix cannot be mistaken for cosmetics."""
        first = self._candidate("FIRST", self.EARLIER)
        second = self._candidate("SECOND", self.LATER)

        by_text = sorted([first, second], key=lambda c: c.timestamp)
        by_instant = sorted([first, second], key=lambda c: c.chronological_key)

        self.assertEqual([c.event_id for c in by_text], ["SECOND", "FIRST"])
        self.assertEqual([c.event_id for c in by_instant], ["FIRST", "SECOND"])

    def test_the_daily_file_lists_them_in_the_order_they_happened(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        repo.save(self._candidate("SECOND", self.LATER))
        repo.save(self._candidate("FIRST", self.EARLIER))

        path = generate_daily_history(
            repo,
            date(2026, 8, 5),
            output_dir=root / "daily",
            generated_at="2026-08-06T11:00:00+09:00",
        )

        text = path.read_text(encoding="utf-8")
        self.assertLess(text.index("FIRST work"), text.index("SECOND work"))

    def test_both_still_land_on_the_same_day(self):
        """Grouping is by the offset-local date and must not change — a fix
        that reordered the day by silently regrouping it would be worse than
        the defect."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        repo.save(self._candidate("FIRST", self.EARLIER))
        repo.save(self._candidate("SECOND", self.LATER))

        path = generate_daily_history(
            repo,
            date(2026, 8, 5),
            output_dir=root / "daily",
            generated_at="2026-08-06T11:00:00+09:00",
        )

        text = path.read_text(encoding="utf-8")
        self.assertIn("Event Count: 2", text)

    def test_late_events_are_appended_in_the_order_they_happened(self):
        markdown = (
            "# DOJOONPASS Company History — 2026-08-05\n\n"
            "## Summary\n\nexisting\n\n"
            "## Metadata\n\n"
            "- History Date: 2026-08-05\n"
            "- Generated At: 2026-08-06T11:00:00+09:00\n"
            "- Event Count: 0\n"
        )
        candidates = [
            self._candidate("SECOND", self.LATER),
            self._candidate("FIRST", self.EARLIER),
        ]

        selected = select_late_candidates(markdown, candidates)

        self.assertEqual([c.event_id for c in selected], ["FIRST", "SECOND"])

    def test_the_role_summary_uses_the_same_order_as_the_file(self):
        candidates = [
            self._candidate("SECOND", self.LATER),
            self._candidate("FIRST", self.EARLIER),
        ]

        summary = build_role_summary(candidates, date(2026, 8, 5))

        backend = next(a for a in summary.roles if a.role == "CTO_BACKEND")
        self.assertEqual([c.event_id for c in backend.candidates], ["FIRST", "SECOND"])

    def test_an_unparseable_timestamp_sorts_last_and_never_raises(self):
        """Only reachable through a hand-edited Candidate file, but a
        renderer that raises on one damaged record loses the whole day."""
        good = self._candidate("GOOD", self.EARLIER)
        broken = self._candidate("BROKEN", "not-a-timestamp")

        ordered = sorted([broken, good], key=lambda c: c.chronological_key)

        self.assertEqual([c.event_id for c in ordered], ["GOOD", "BROKEN"])

    def test_a_timestamp_with_no_offset_sorts_last_rather_than_being_guessed(self):
        """`validate_event()` requires an offset, so this only arrives via a
        hand-edited file. Assuming a timezone for it would silently place it
        somewhere it may not belong."""
        aware = self._candidate("AWARE", self.EARLIER)
        naive = self._candidate("NAIVE", "2026-08-05T00:00:00")

        ordered = sorted([naive, aware], key=lambda c: c.chronological_key)

        self.assertEqual([c.event_id for c in ordered], ["AWARE", "NAIVE"])

    def test_same_offset_ordering_is_unchanged(self):
        """The overwhelmingly common case must be byte-identical to before."""
        early = self._candidate("EARLY", "2026-08-05T09:00:00+09:00")
        late = self._candidate("LATE", "2026-08-05T18:00:00+09:00")

        self.assertEqual(
            [c.event_id for c in sorted([late, early], key=lambda c: c.chronological_key)],
            [c.event_id for c in sorted([late, early], key=lambda c: c.timestamp)],
        )

    def test_the_key_is_a_total_order_across_every_shape(self):
        """A sort key that cannot compare two of its own values raises
        mid-render, which is the one outcome a damaged record must not
        cause."""
        shapes = [
            self.EARLIER,
            self.LATER,
            "2026-08-05T00:00:00",
            "not-a-timestamp",
            "",
        ]
        candidates = [self._candidate(f"E{i}", ts) for i, ts in enumerate(shapes)]

        ordered = sorted(candidates, key=lambda c: c.chronological_key)

        self.assertEqual(len(ordered), len(shapes))


class NoProductionCallerTests(unittest.TestCase):
    """A-3's partial implementation reaches no operator, verified rather than
    remembered.

    BACKLOG A-3 records the SKIP correctly — a per-role taxonomy needs a
    docs/05 classification policy — and then says `role_summary.py`
    "제공한다" the grouping it *can* do with the existing vocabulary. True of
    the function; not true of the system. **Nothing in production calls it**,
    so no operator has ever seen a role summary.

    That is A-16 / E-20's shape (implemented, exported, never invoked), and
    C29 §3 established how to check it: with AST, because grep was wrong once
    before and mistook an export for a call. Here the only calls that exist
    are the module's own two constructors, inside the function itself.

    Wiring it up is not available without approval — *where* a role summary
    would be rendered is A-3/A-4, a docs/06 change. What this test does is
    stop the record from drifting: if a caller ever appears, this fails and
    A-3 must be rewritten to say so.
    """

    TARGETS = {"build_role_summary", "DailyRoleSummary", "RoleActivity"}

    def _production_files(self):
        root = Path(__file__).resolve().parents[1]
        return [
            p
            for p in list((root / "src").glob("**/*.py")) + list(root.glob("*.py"))
            if "__pycache__" not in str(p)
        ]

    def test_nothing_in_production_calls_the_role_summary(self):
        import ast

        callers = []
        for path in self._production_files():
            if path.name == "role_summary.py":
                continue  # its own constructors
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else getattr(func, "attr", None)
                )
                if name in self.TARGETS:
                    callers.append(f"{path.name}:{node.lineno}")

        self.assertEqual(
            callers,
            [],
            "a production caller appeared — BACKLOG A-3 says there is none",
        )

    def test_it_is_nonetheless_exported(self):
        """The half that makes the claim easy to misread: it is importable
        and re-exported, which looks like availability."""
        import daily

        self.assertIn("build_role_summary", daily.__all__)
        self.assertTrue(callable(daily.build_role_summary))

    def test_and_it_works_when_called(self):
        """So the record says "unwired", not "broken"."""
        candidate = HistoryCandidate(
            history_id="H", event_id="E", timestamp="2026-08-05T10:00:00+09:00",
            category="MILESTONE", project_id="P", role="COO", summary="s",
            evidence=(), filter_result=HistoryDecision.KEEP,
        )

        summary = build_role_summary([candidate], date(2026, 8, 5))

        self.assertEqual(summary.active_roles, ("COO",))
        self.assertEqual(
            set(summary.silent_roles), set(ROLE_ORDER) - {"COO"}
        )


if __name__ == "__main__":
    unittest.main()
