"""Execution Event schema, validation, and serialization (docs/02_EVENT_SCHEMA.md)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

SUPPORTED_SCHEMA_VERSION = "1.0"

SOURCES = frozenset({"DESKTOP_1", "DESKTOP_2", "DESKTOP_3", "DESKTOP_4"})
ROLES = frozenset({"CTO_BACKEND", "CTO_FRONTEND", "CMO", "COO"})
EVENT_TYPES = frozenset(
    {
        "STARTED",
        "BLOCKED",
        "RESUMED",
        "MILESTONE_COMPLETED",
        "COMPLETED",
        "CANCELLED",
        "ISSUE_RESOLVED",
        "DECISION_APPROVED",
    }
)
STATUSES = frozenset({"NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED"})

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
    return datetime.now().astimezone().isoformat(timespec="seconds")


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
        if value is not None and value not in allowed:
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
