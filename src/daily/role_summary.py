"""Per-role view of one day's Company History. Read-only, writes nothing.

Why this is a separate function and not a change to the Daily Markdown
--------------------------------------------------------------------
docs/06_DAILY_HISTORY_SPEC.md fixes the Daily History file's structure —
`## Summary`, then `Decisions / Milestones / Issues / Learnings`, in that
order — and tests/test_spec_conformance.py asserts it section by section.
Re-cutting that file by role would be a spec change, so it is not done
here.

What was actually missing was not a different file: it was a way to ask
"what did each role do on this date, and which roles were silent?"
without re-parsing rendered Markdown. Every fact needed to answer that is
already carried on each `HistoryCandidate` (`role`, `category`,
`project_id`, `summary`, `event_id`), so this module only groups what is
already there.

Deliberately NOT invented here
------------------------------
A richer per-role taxonomy — CTO "테스트/버그", CMO "콘텐츠/실험" — has no
representation in the data. `HistoryCandidate.category` is exactly
DECISION / MILESTONE / ISSUE / LEARNING (docs/05), and mapping a CMO
Event onto "콘텐츠" versus "실험" would mean deciding a new classification
policy. That decision is not this module's to make; it is recorded in
BACKLOG.md as needing approval. Until then a role's work is grouped by
the categories the schema actually defines, which is enough to answer
"활동 / 완료 / 이슈" for every role.

A role that did nothing is reported as present-and-empty rather than
omitted. "CMO 활동 없음" and "CMO를 집계하는 것을 잊었다" look identical in
an output that simply leaves the role out, and only one of them is fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from typing import Mapping, Sequence

from events import ROLES
from history import HistoryCandidate

# Stable presentation order: the execution roles first, then COO. Matches
# the order README section 3 lists the Desktops in, so a report reads the
# same way every day.
ROLE_ORDER = ("CTO_BACKEND", "CTO_FRONTEND", "CMO", "COO")

CATEGORY_ORDER = ("DECISION", "MILESTONE", "ISSUE", "LEARNING")


@dataclass(frozen=True)
class RoleActivity:
    """One role's slice of one date.

    `has_activity` is False for a role that produced no KEEP candidate that
    day — the NO_ACTIVITY case, represented explicitly rather than by
    absence.
    """

    role: str
    candidates: tuple[HistoryCandidate, ...] = ()
    by_category: Mapping[str, tuple[HistoryCandidate, ...]] = field(default_factory=dict)

    @property
    def has_activity(self) -> bool:
        return bool(self.candidates)

    @property
    def projects(self) -> tuple[str, ...]:
        seen: list[str] = []
        for candidate in self.candidates:
            if candidate.project_id not in seen:
                seen.append(candidate.project_id)
        return tuple(seen)

    def of_category(self, category: str) -> tuple[HistoryCandidate, ...]:
        return tuple(self.by_category.get(category, ()))


@dataclass(frozen=True)
class DailyRoleSummary:
    date: date_type
    roles: tuple[RoleActivity, ...]

    @property
    def active_roles(self) -> tuple[str, ...]:
        return tuple(role.role for role in self.roles if role.has_activity)

    @property
    def silent_roles(self) -> tuple[str, ...]:
        return tuple(role.role for role in self.roles if not role.has_activity)

    def for_role(self, role: str) -> RoleActivity:
        for activity in self.roles:
            if activity.role == role:
                return activity
        raise KeyError(role)


def _candidate_date(candidate: HistoryCandidate) -> date_type:
    return datetime.fromisoformat(candidate.timestamp).date()


def build_role_summary(
    candidates: Sequence[HistoryCandidate], target_date: date_type
) -> DailyRoleSummary:
    """Group `target_date`'s candidates by role, every role always present.

    Selection matches `daily.generator.generate_daily_history()` exactly —
    a candidate belongs to the date its own Event `timestamp` falls on
    (docs/06 §12), not the date this runs — so the summary and the Daily
    History file can never describe different sets of work. Ordering
    within a role is by timestamp, the same as the rendered file.

    A role in `events.ROLES` that is not in `ROLE_ORDER` is still included,
    after the known ones: adding a role to the schema must not silently
    drop it from every report.
    """
    known = list(ROLE_ORDER)
    known.extend(sorted(role for role in ROLES if role not in ROLE_ORDER))

    matching = [c for c in candidates if _candidate_date(c) == target_date]
    # Same ordering rule as the rendered file, and now literally the same
    # key — see `HistoryCandidate.chronological_key`.
    matching.sort(key=lambda candidate: candidate.chronological_key)

    activities: list[RoleActivity] = []
    for role in known:
        mine = tuple(c for c in matching if c.role == role)
        by_category: dict[str, tuple[HistoryCandidate, ...]] = {}
        for category in CATEGORY_ORDER:
            bucket = tuple(c for c in mine if c.category == category)
            if bucket:
                by_category[category] = bucket
        activities.append(
            RoleActivity(role=role, candidates=mine, by_category=by_category)
        )

    return DailyRoleSummary(date=target_date, roles=tuple(activities))
