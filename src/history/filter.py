"""History Filter Core (docs/05_HISTORY_PIPELINE_SPEC.md sections 10-26).

HistoryFilter decides KEEP / DROP / REVIEW for one already-ACCEPTED Event.
It does not decide anything else: no Markdown, no Daily/Monthly History,
no Notion, no Backup, no AI/LLM summarization. It does not know Collector,
Transport, or Reporter exist — it only depends on Phase 1's `events`.

Automatic rules implemented here (no scoring system — docs/05 section 20
explicitly forbids one; three outcomes are enough):

    history_candidate == false          -> DROP   (docs/02 section 36)
    DECISION_APPROVED                   -> KEEP   (docs/05 section 25)
    DECISION_REQUIRED, DECISION_REJECTED,
    EXECUTED, ISSUE_RAISED              -> KEEP   (C149 — the opening and
                                                    the refusal of a
                                                    lifecycle are worth
                                                    exactly what its
                                                    settlement is worth,
                                                    and Issue/Decision
                                                    Aging cannot be
                                                    computed from an end
                                                    with no beginning)
    MILESTONE_COMPLETED, ISSUE_RESOLVED -> KEEP   (docs/05 section 25,
                                                    "주요" qualifier not
                                                    auto-detectable without
                                                    AI, which this Phase
                                                    forbids — every event of
                                                    this type is treated as
                                                    KEEP; see [ISSUES])
    STARTED, RESUMED, ASSIGNED          -> DROP   (docs/05 section 26 —
                                                    progress, not outcome)
    AT_RISK, BLOCKED, COMPLETED,
    CANCELLED                           -> REVIEW (docs/05 section 24 names
                                                    exactly these four as
                                                    its own REVIEW examples:
                                                    "장기 영향이 불확실한
                                                    BLOCKED", "의미가 애매한
                                                    COMPLETED", "중요도가
                                                    애매한 CANCELLED", and
                                                    AT_RISK, added by C149
                                                    to the same list for the
                                                    same reason — "멈출 것
                                                    같다" is the most
                                                    uncertain of the four)
"""

from __future__ import annotations

from events import Event

from .result import HistoryCandidate, HistoryDecision, HistoryFilterResult

_KEEP_EVENT_TYPES = frozenset(
    {
        "DECISION_REQUIRED",
        "DECISION_APPROVED",
        "DECISION_REJECTED",
        "EXECUTED",
        "MILESTONE_COMPLETED",
        "ISSUE_RAISED",
        "ISSUE_RESOLVED",
    }
)
# `ASSIGNED` joins them (C149) and the reason is docs/05 §26's own: these
# are *progress* Events. An Issue changing hands is how the work moved, not
# what the company achieved, and Company History keeps outcomes. It still
# matters enormously **now** — `_roll_open_items()` reads it to tell an
# unowned Issue from an owned one — which is the distinction between a
# Control Tower (current state) and Company History (the long record).
_DROP_EVENT_TYPES = frozenset({"STARTED", "RESUMED", "ASSIGNED"})

# AT_RISK deliberately falls through to REVIEW rather than being listed
# here: docs/05 section 24's REVIEW examples are the ambiguous-impact
# states, and "likely to stop" is the most ambiguous of them all.
_CATEGORY_BY_EVENT_TYPE = {
    "DECISION_REQUIRED": "DECISION",
    "DECISION_APPROVED": "DECISION",
    "DECISION_REJECTED": "DECISION",
    "EXECUTED": "DECISION",
    "MILESTONE_COMPLETED": "MILESTONE",
    "COMPLETED": "MILESTONE",
    "ISSUE_RAISED": "ISSUE",
    "ISSUE_RESOLVED": "ISSUE",
    "AT_RISK": "ISSUE",
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
