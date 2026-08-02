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
