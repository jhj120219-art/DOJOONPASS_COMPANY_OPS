"""History Filter Core (docs/05_HISTORY_PIPELINE_SPEC.md sections 10-26).

HistoryFilter decides KEEP / DROP / REVIEW for one already-ACCEPTED Event.
It does not decide anything else: no Markdown, no Daily/Monthly History,
no Notion, no Backup, no AI/LLM summarization. It does not know Collector,
Transport, or Reporter exist — it only depends on Phase 1's `events`.

Automatic rules implemented here (no scoring system — docs/05 section 20
explicitly forbids one; three outcomes are enough):

    history_candidate == false          -> DROP   (docs/02 section 36)
    DECISION_APPROVED                   -> KEEP   (docs/05 section 25)
    MILESTONE_COMPLETED, ISSUE_RESOLVED -> KEEP   (docs/05 section 25,
                                                    "주요" qualifier not
                                                    auto-detectable without
                                                    AI, which this Phase
                                                    forbids — every event of
                                                    this type is treated as
                                                    KEEP; see [ISSUES])
    STARTED, RESUMED                    -> DROP   (docs/05 section 26)
    BLOCKED, COMPLETED, CANCELLED       -> REVIEW (docs/05 section 24 names
                                                    exactly these three as
                                                    its own REVIEW examples:
                                                    "장기 영향이 불확실한
                                                    BLOCKED", "의미가 애매한
                                                    COMPLETED", "중요도가
                                                    애매한 CANCELLED")
"""

from __future__ import annotations

from events import Event

from .result import HistoryCandidate, HistoryDecision, HistoryFilterResult

_KEEP_EVENT_TYPES = frozenset({"DECISION_APPROVED", "MILESTONE_COMPLETED", "ISSUE_RESOLVED"})
_DROP_EVENT_TYPES = frozenset({"STARTED", "RESUMED"})

_CATEGORY_BY_EVENT_TYPE = {
    "DECISION_APPROVED": "DECISION",
    "MILESTONE_COMPLETED": "MILESTONE",
    "COMPLETED": "MILESTONE",
    "ISSUE_RESOLVED": "ISSUE",
    "BLOCKED": "ISSUE",
}


class HistoryFilter:
    def evaluate(self, event: Event) -> HistoryFilterResult:
        if not event.history_candidate:
            decision = HistoryDecision.DROP
            reason = "history_candidate is false"
        elif event.event_type in _KEEP_EVENT_TYPES:
            decision = HistoryDecision.KEEP
            reason = f"automatic KEEP rule for event_type={event.event_type}"
        elif event.event_type in _DROP_EVENT_TYPES:
            decision = HistoryDecision.DROP
            reason = f"automatic DROP rule for event_type={event.event_type}"
        else:
            decision = HistoryDecision.REVIEW
            reason = f"no automatic rule for event_type={event.event_type}; needs human review"

        candidate = HistoryCandidate(
            history_id=f"HIST-{event.event_id}",
            event_id=event.event_id,
            timestamp=event.timestamp,
            category=_CATEGORY_BY_EVENT_TYPE.get(event.event_type),
            project_id=event.project_id,
            role=event.role,
            summary=event.summary,
            evidence=event.evidence,
            filter_result=decision,
        )
        return HistoryFilterResult(decision=decision, candidate=candidate, reason=reason)
