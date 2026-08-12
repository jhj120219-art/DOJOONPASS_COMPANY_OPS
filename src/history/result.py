"""History Filter's data contract (docs/05_HISTORY_PIPELINE_SPEC.md sections 21-27).

HistoryCandidate is not a stored History record — it is the in-memory
"candidate under consideration" object described in section 27. Saving
anything to disk (Daily History Markdown, Local Master) is explicitly a
later phase's job, not this one's.

Per section 36, this implementation uses only `filter_result` on
HistoryCandidate and omits the separate `decision` field the spec allows
skipping ("필요하면 구현에서는 filter_result만 사용하고 별도 decision
Field는 생략할 수 있다").

Phase 4.7 (Decision Context Integration) added `decision_context`,
`expected_outcome`, `actual_outcome`, and `lessons_learned` — all optional,
per the updated section 27 field list and the nullable pattern in section
38's worked example. HistoryFilter has no source data for these (Event
Schema was not changed — see this Phase's report), so every candidate it
produces still has all four as None; the fields exist here so a
HistoryCandidate CAN carry this content once something populates it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


class HistoryDecision(enum.Enum):
    KEEP = "KEEP"
    DROP = "DROP"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class HistoryCandidate:
    """Fields per docs/05_HISTORY_PIPELINE_SPEC.md section 27.

    `category` is one of DECISION/MILESTONE/ISSUE/LEARNING when the source
    event_type maps cleanly to one (section 6-9), or None when it doesn't
    (STARTED/RESUMED are always DROP and never really become History;
    CANCELLED's category is left undefined by the spec — see this Phase's
    [ISSUES] report).
    """

    history_id: str
    event_id: str
    timestamp: str
    category: str | None
    project_id: str
    role: str
    summary: str
    evidence: tuple[str, ...]
    filter_result: HistoryDecision
    decision_context: str | None = None
    expected_outcome: str | None = None
    actual_outcome: str | None = None
    lessons_learned: str | None = None

    @property
    def chronological_key(self) -> tuple:
        """Sort key that orders candidates by the instant they describe.

        Every place that renders Company History orders a day's items by
        `timestamp`, and all of them used the raw string. That is only
        correct while every Event carries the same UTC offset, and the
        schema deliberately does not require one:
        `tests/test_spec_conformance.py::test_the_schema_accepts_a_non_kst_offset`
        pins `+00:00` / `-05:00` / `+05:30` as accepted, and a Signal may
        state its own `timestamp`. `app/desktop_activity._before()` already
        parses rather than string-compares, and says why; the three renderers
        in `daily/` did not.

        Measured: `2026-08-05T01:00:00+00:00` and `2026-08-05T09:00:00+09:00`
        are 01:00 and 00:00 UTC, so the second happened first — and sorted as
        strings the first one wins. Both fall on 2026-08-05 in their own
        offsets, so both land in the same Daily file and are rendered in the
        wrong order.

        Two buckets rather than one comparison so the key is a total order.
        A timestamp that cannot be parsed, or that carries no offset (only
        reachable through a hand-edited Candidate file — `validate_event()`
        requires one), has no instant to compare, so it sorts after
        everything that does, by its raw text. Damaged records landing last
        is deterministic; letting them silently reorder good ones is not.

        Grouping is untouched: `_candidate_date()` still buckets by the
        offset-local date, which is the day the work happened where it
        happened (docs/06 §12). Only the order within a day changes.
        """
        try:
            parsed = datetime.fromisoformat(self.timestamp)
        except (TypeError, ValueError):
            return (1, str(self.timestamp))
        if parsed.tzinfo is None:
            return (1, str(self.timestamp))
        return (0, parsed.astimezone(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "history_id": self.history_id,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "category": self.category,
            "project_id": self.project_id,
            "role": self.role,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "decision_context": self.decision_context,
            "expected_outcome": self.expected_outcome,
            "actual_outcome": self.actual_outcome,
            "lessons_learned": self.lessons_learned,
            "filter_result": self.filter_result.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HistoryCandidate":
        return cls(
            history_id=data["history_id"],
            event_id=data["event_id"],
            timestamp=data["timestamp"],
            category=data.get("category"),
            project_id=data["project_id"],
            role=data["role"],
            summary=data["summary"],
            evidence=tuple(data.get("evidence") or ()),
            decision_context=data.get("decision_context"),
            expected_outcome=data.get("expected_outcome"),
            actual_outcome=data.get("actual_outcome"),
            lessons_learned=data.get("lessons_learned"),
            filter_result=HistoryDecision(data["filter_result"]),
        )


@dataclass(frozen=True)
class HistoryFilterResult:
    """Outcome of a single HistoryFilter.evaluate() call.

    `candidate` is the persistable-shaped record (section 27). `reason` is
    a short, human-readable explanation of which rule fired — useful for
    debugging/audit, but not part of the History Candidate schema itself.
    """

    decision: HistoryDecision
    candidate: HistoryCandidate
    reason: str
