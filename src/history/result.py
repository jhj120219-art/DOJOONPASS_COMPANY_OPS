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


# The fields whose wrong type or absence the Company History renderer cannot
# survive — measured, not guessed. See `candidate_errors()`.
_REQUIRED_CANDIDATE_STRINGS = ("history_id", "event_id", "timestamp", "project_id", "summary")


def candidate_errors(data: Mapping[str, Any]) -> list[str]:
    """Why this stored Candidate cannot be rendered, or `[]` if it can.

    Same shape and same job as `events.validate_event()`, one layer in — and
    it is needed for the same reason. A Candidate file is JSON on disk under
    `runtime/history_candidates/`, and docs/11 §71 explicitly permits the COO
    to edit it by hand. `HistoryCandidate.from_dict()` reads whatever the
    file says: it type-checks nothing, and a missing key raises a bare
    `KeyError` naming only the key.

    **Measured through the real Runner (C44)**, one hand-edited KEEP
    Candidate beside one ordinary one:

        summary=12345      daily FAILED "sequence item 2: expected str
                           instance, int found" -> 0 Daily files, exit 2
        project_id=7       daily FAILED -> 0 Daily files, exit 2
        timestamp=5        daily FAILED -> 0 Daily files, exit 2
        summary missing    daily FAILED (KeyError) -> 0 Daily files, exit 2

    and in every one of those the ordinary Candidate was not rendered either:
    the Scheduler builds the KEEP index **once per batch**, so one file stops
    **every** date (the same blast radius A-7 / BUG-38 record for a Candidate
    whose JSON or timestamp will not parse). It is permanent — the file stays
    in `keep/`, so the next run dies identically.

    What the operator was told: `sequence item 2: expected str instance, int
    found`, in the manifest's `reason` and in `daily_late_update.log`. Not the
    file, not the field, not even that a Candidate was involved. And
    `ops_status.py` said `Candidate 정합성 : OK`, because its own reader only
    checks `timestamp` and `event_id`.

    **This list is deliberately the BLOCKING set, not every type in the
    dataclass.** Three shapes the renderer survives are left out on purpose,
    because refusing them would turn a survivable corruption into a stopped
    pipeline — the exact harm this function exists to reduce:

        role=5         renders `- Owner: 5`
        category=9     the item is dropped from every section, which
                       `ops_status._daily_counts_more_than_it_shows()`
                       already reports (C43)
        evidence="ab"  `tuple("ab")` becomes two evidence lines; recorded in
                       BACKLOG as a known limit rather than closed here

    A `timestamp` that is a string but not ISO-8601 is deliberately absent
    too, and for a sharper reason: A-7 already records that case, the index
    build already raises on it with `isoformat` in the message, and
    `test_scheduler.py::OneCorruptCandidateStopsEveryDateTests` pins both.
    Adding it here would move where that raise happens — `list()` would start
    refusing a Candidate it used to return — which is a contract change this
    function has no reason to make. The type check above covers the case that
    was actually unhandled (`timestamp` not a string at all).

    So this changes **what a failure says and who sees it**, never whether a
    run fails.

    Costs nothing worth measuring against the read it rides on — a handful of
    dict lookups per Candidate, against a file open:

        1,000 Candidates   `list()` 52.2 ms   this function 0.5 ms total
        5,000 Candidates   `list()` 255.5 ms  this function 2.6 ms total
    """
    errors: list[str] = []
    for field_name in _REQUIRED_CANDIDATE_STRINGS:
        value = data.get(field_name)
        if value is None:
            errors.append(f"missing required field: {field_name}")
        elif not isinstance(value, str):
            errors.append(f"{field_name} must be a string")

    filter_result = data.get("filter_result")
    if filter_result is None:
        errors.append("missing required field: filter_result")
    else:
        try:
            HistoryDecision(filter_result)
        except ValueError:
            errors.append(f"invalid filter_result: {filter_result!r}")

    return errors


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
