"""Execution Event schema, validation, and serialization (docs/02_EVENT_SCHEMA.md)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

import businessdate

SUPPORTED_SCHEMA_VERSION = "1.0"

SOURCES = frozenset({"DESKTOP_1", "DESKTOP_2", "DESKTOP_3", "DESKTOP_4"})
ROLES = frozenset({"CTO_BACKEND", "CTO_FRONTEND", "CMO", "COO"})
# docs/02 §11. Every lifecycle this vocabulary can express has both of its
# ends here, which was not true before C149.
#
# The eight types this file shipped with are all *past tense*: something
# finished, resolved, or was approved. A company does not only produce
# outcomes — it produces the open states that precede them, and those are
# what a COO and a CEO actually act on. Three lifecycles were half-written:
#
#     Issue      ISSUE_RESOLVED existed; nothing could say an Issue was
#                RAISED, so "how long has this been open" had no start date
#                and Issue Aging was uncomputable in principle, not just
#                unimplemented. `ASSIGNED` is the step between: an Issue
#                nobody has taken and an Issue being worked on looked
#                identical, and they are the two halves an aging list is
#                read to tell apart.
#     Decision   DECISION_APPROVED existed; docs/02 §19 goes out of its way
#                to say "CEO Decision Required" must NOT be recorded as
#                DECISION_APPROVED — and then gave it nowhere else to go.
#                A rejection had the same problem: recording it as an
#                approval is false, and omitting it makes a decision that
#                was actually settled look permanently pending.
#                And the lifecycle closed one step early: an approval is
#                not the work. `EXECUTED` is the other end of it — a
#                decision approved and never carried out is one of the most
#                ordinary ways a company stalls, and it was invisible
#                because approval removed it from every list.
#     Project    BLOCKED (stopped) existed; AT_RISK (moving, but likely to
#                stop) did not, so the one state a COO can still act on
#                early had to be reported as either "fine" or "stopped".
#
# The four added here close exactly those three gaps and nothing more. Each
# is modelled on a type that already exists rather than inventing a new
# shape: ISSUE_RAISED / DECISION_REQUIRED / DECISION_REJECTED carry no
# property of their own (like DECISION_APPROVED, docs/04 §28), and AT_RISK
# is a state-setting event that pins its own `status` (like COMPLETED
# §25 and CANCELLED §26). No new Event field was added for any of them.
EVENT_TYPES = frozenset(
    {
        "STARTED",
        "AT_RISK",
        "BLOCKED",
        "RESUMED",
        "MILESTONE_COMPLETED",
        "COMPLETED",
        "CANCELLED",
        "ISSUE_RAISED",
        "ASSIGNED",
        "ISSUE_RESOLVED",
        "DECISION_REQUIRED",
        "DECISION_APPROVED",
        "DECISION_REJECTED",
        "EXECUTED",
    }
)

# `AT_RISK` sits between IN_PROGRESS and BLOCKED: work is still moving, and
# something known is likely to stop it. It is a `status` and not only an
# event_type for the same reason BLOCKED is — a project stays at risk across
# the later Events that do not mention the risk, and `status` is the field
# that survives. Notion needs no migration for it: `bootstrap.py` declares
# `"Status": {"select": {}}` with no fixed option list, so the API creates
# the option on first write.
STATUSES = frozenset(
    {"NOT_STARTED", "IN_PROGRESS", "AT_RISK", "BLOCKED", "COMPLETED", "CANCELLED"}
)

REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "timestamp",
    "source",
    "role",
    "project_id",
    "event_type",
    "status",
    "summary",
    "history_candidate",
)


class EventValidationError(ValueError):
    """Raised when Event data fails docs/02_EVENT_SCHEMA.md validation rules."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def generate_event_id() -> str:
    return str(uuid.uuid4())


def current_timestamp() -> str:
    return businessdate.now().isoformat(timespec="seconds")


def _timestamp_error(value: Any) -> str | None:
    if not isinstance(value, str):
        return "timestamp must be an ISO-8601 string"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return f"timestamp is not valid ISO-8601: {value!r}"
    if parsed.tzinfo is None:
        return f"timestamp must include a timezone offset: {value!r}"
    return None


def validate_event(data: Mapping[str, Any]) -> list[str]:
    """Return a list of validation error messages; empty means the Event is valid."""
    errors: list[str] = []

    for field_name in REQUIRED_FIELDS:
        if data.get(field_name) is None:
            errors.append(f"missing required field: {field_name}")

    schema_version = data.get("schema_version")
    if schema_version is not None and schema_version != SUPPORTED_SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {schema_version!r}")

    for field_name, allowed in (
        ("source", SOURCES),
        ("role", ROLES),
        ("event_type", EVENT_TYPES),
        ("status", STATUSES),
    ):
        value = data.get(field_name)
        if value is None:
            continue
        # `isinstance` before the membership test, not after. `data` is
        # untrusted JSON from another Desktop, so `value` can be a list or a
        # dict — a JSON array or object where a string was expected — and
        # `value not in allowed` against a frozenset raises
        # `TypeError: unhashable type` for either shape instead of returning
        # an ordinary validation error.
        #
        # The three fields below (`event_id` / `project_id` / `summary`)
        # already got an `isinstance` guard; these four did not, because they
        # have an allowed set and the membership test *looked* like it was
        # doing the type work. It is not: a frozenset lookup needs a hashable
        # key before it can answer at all.
        #
        # Measured through the real Collector on this tree, one crafted Event
        # (`"role": ["CTO_BACKEND"]`) beside one ordinary one:
        #
        #     accepted=1 failed=1
        #     incoming/   bad.json      <- still there, every run, forever
        #     rejected/   (empty)
        #     collector.log  FAILED bad.json: unhashable type: 'list'
        #
        # Two things are wrong with that. docs/03 §7 sends an invalid Event to
        # `rejected/` and lets the run continue; this one is FAILED instead,
        # so the file stays in `incoming/` and fails identically on every
        # retry. And the only trace is a bare Python message that names the
        # exception rather than the field, so nothing tells an operator that
        # the problem is the *shape* of `role`.
        #
        # Needs no crafted input beyond a hand-written or restored Event with
        # one bracket too many.
        if not isinstance(value, str) or value not in allowed:
            errors.append(f"invalid {field_name}: {value!r}")

    timestamp = data.get("timestamp")
    if timestamp is not None:
        timestamp_error = _timestamp_error(timestamp)
        if timestamp_error:
            errors.append(timestamp_error)

    history_candidate = data.get("history_candidate")
    if history_candidate is not None and not isinstance(history_candidate, bool):
        errors.append("history_candidate must be a boolean")

    evidence = data.get("evidence")
    if evidence is not None and (
        not isinstance(evidence, (list, tuple))
        or not all(isinstance(item, str) for item in evidence)
    ):
        errors.append("evidence must be a list of strings")

    for field_name in ("milestone", "blocker"):
        value = data.get(field_name)
        if value is not None and not isinstance(value, str):
            errors.append(f"{field_name} must be a string or null")

    # docs/02 §4's table declares these three `string`, and this function was
    # the only place that did not enforce it. Every other typed field was
    # covered — `timestamp` by `_timestamp_error()`, `milestone` / `blocker`
    # directly above, `evidence` below, `history_candidate` by its own check,
    # and `source` / `role` / `event_type` / `status` implicitly, because a
    # non-string cannot be in a frozenset of strings. These three were
    # checked for presence only.
    #
    # It is a trust boundary: an Event arrives as JSON written on another
    # Desktop and crosses OneDrive, so its types are whatever that file says.
    # The Signal layer does not close it either — `agent.signals.parse_signal()`
    # validates the field *set*, not the field types.
    #
    # Measured through the real Runner, one crafted Event beside one ordinary
    # one, before this check existed:
    #
    #     summary=12345    Collector ACCEPTED -> KEEP Candidate stored ->
    #                      daily FAILED "sequence item 2: expected str
    #                      instance, int found" -> 0 Daily files, exit 2
    #     project_id=7     ACCEPTED -> notion_sync AND daily both FAILED
    #                      ("'int' object has no attribute 'replace'") ->
    #                      0 Daily files, exit 2
    #     event_id=99      TypeError escapes run_once() entirely, from
    #                      `collector/state.py`'s sorted() over mixed int and
    #                      str ids — the run dies inside step 3
    #
    # The first two are the worse pair: the Candidate is written to `keep/`,
    # so **every later run fails the same way** and Company History stops
    # advancing until a human deletes a file. One malformed Event from one
    # Desktop takes the pipeline down, and takes that run's innocent Events
    # with it.
    #
    # Refusing them here routes them where docs/03 §7 already sends an
    # invalid Event — REJECTED, moved to `rejected/`, the run continuing —
    # instead of into a CRITICAL step. Nothing about *empty* strings changes:
    # `""` is still accepted, which is BACKLOG A-15's separate, still-open
    # question.
    for field_name in ("event_id", "project_id", "summary"):
        value = data.get(field_name)
        if value is not None and not isinstance(value, str):
            errors.append(f"{field_name} must be a string")

    event_type = data.get("event_type")
    status = data.get("status")

    if event_type == "BLOCKED" and not data.get("blocker"):
        errors.append("BLOCKED event requires a non-null blocker")

    # The three state-setting types pin their own status, so a report cannot
    # say "this project is at risk" while also saying it is COMPLETED.
    # AT_RISK joins COMPLETED/CANCELLED here rather than joining BLOCKED's
    # rule above, because a risk is described in `summary` — it has no
    # dedicated field and needs none (see EVENT_TYPES' note).
    if event_type == "AT_RISK" and status != "AT_RISK":
        errors.append("AT_RISK event requires status = AT_RISK")

    if event_type == "COMPLETED" and status != "COMPLETED":
        errors.append("COMPLETED event requires status = COMPLETED")

    if event_type == "CANCELLED" and status != "CANCELLED":
        errors.append("CANCELLED event requires status = CANCELLED")

    return errors


@dataclass(frozen=True)
class Event:
    """An immutable Execution Event, per docs/02_EVENT_SCHEMA.md section 3."""

    schema_version: str
    event_id: str
    timestamp: str
    source: str
    role: str
    project_id: str
    event_type: str
    status: str
    summary: str
    history_candidate: bool
    milestone: str | None = None
    blocker: str | None = None
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Event":
        errors = validate_event(data)
        if errors:
            raise EventValidationError(errors)
        return cls(
            schema_version=data["schema_version"],
            event_id=data["event_id"],
            timestamp=data["timestamp"],
            source=data["source"],
            role=data["role"],
            project_id=data["project_id"],
            event_type=data["event_type"],
            status=data["status"],
            summary=data["summary"],
            history_candidate=data["history_candidate"],
            milestone=data.get("milestone"),
            blocker=data.get("blocker"),
            evidence=tuple(data.get("evidence") or ()),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "role": self.role,
            "project_id": self.project_id,
            "event_type": self.event_type,
            "status": self.status,
            "milestone": self.milestone,
            "summary": self.summary,
            "blocker": self.blocker,
            "evidence": list(self.evidence),
            "history_candidate": self.history_candidate,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "Event":
        return cls.from_dict(json.loads(raw))


def create_event(
    *,
    source: str,
    role: str,
    project_id: str,
    event_type: str,
    status: str,
    summary: str,
    history_candidate: bool,
    milestone: str | None = None,
    blocker: str | None = None,
    evidence: Sequence[str] | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
    schema_version: str = SUPPORTED_SCHEMA_VERSION,
) -> Event:
    """Build and validate a new Event, generating event_id/timestamp when omitted."""
    data = {
        "schema_version": schema_version,
        "event_id": event_id or generate_event_id(),
        "timestamp": timestamp or current_timestamp(),
        "source": source,
        "role": role,
        "project_id": project_id,
        "event_type": event_type,
        "status": status,
        "milestone": milestone,
        "summary": summary,
        "blocker": blocker,
        "evidence": list(evidence) if evidence is not None else [],
        "history_candidate": history_candidate,
    }
    return Event.from_dict(data)
