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
