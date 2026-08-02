"""Reporter Core (docs/02_EVENT_SCHEMA.md section 39).

Reporter turns an explicit, already-known work Signal into a validated
Execution Event. It does not watch the filesystem, processes, keyboard,
screen, or AI conversations, and it does not infer that work happened —
the caller supplies the Signal explicitly. Reporter never decides Critical
Path, Launch Readiness, or Decision approval, and it never talks to
Collector, Notion, or GitHub — this phase stops at a local Event Object.

All Event structure and validation is Phase 1's src/events; Reporter does
not duplicate it.

Reporter can optionally be wired to a Transport (src/transport) to hand a
created Event off for delivery. No concrete Transport is chosen or built
here — only the seam, so GitHub/USB/SharedFolder/etc. can be plugged in
later without changing Reporter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from events import Event, EventValidationError, create_event
from transport import Transport

from .local_output import DEFAULT_EVENT_OUTPUT_DIR, read_event_json, write_event_json
from .profiles import DesktopProfile, PROFILES, ReporterConfigError, resolve_profile

__all__ = [
    "Reporter",
    "DesktopProfile",
    "PROFILES",
    "ReporterConfigError",
    "EventValidationError",
    "DEFAULT_EVENT_OUTPUT_DIR",
    "read_event_json",
    "write_event_json",
]


class Reporter:
    def __init__(
        self,
        profile: str | DesktopProfile | None = None,
        transport: Transport | None = None,
    ):
        self.profile = resolve_profile(profile)
        self.transport = transport

    def report(
        self,
        *,
        project_id: str,
        event_type: str,
        status: str,
        summary: str,
        milestone: str | None = None,
        blocker: str | None = None,
        evidence: Sequence[str] | None = None,
        history_candidate: bool,
        event_id: str | None = None,
        timestamp: str | None = None,
    ) -> Event:
        """Validate a work Signal against the Event Schema and return the Event.

        Raises EventValidationError for an invalid Signal — no field is ever
        auto-corrected to force an invalid Signal into a valid Event.
        """
        return create_event(
            source=self.profile.source,
            role=self.profile.role,
            project_id=project_id,
            event_type=event_type,
            status=status,
            summary=summary,
            milestone=milestone,
            blocker=blocker,
            evidence=evidence,
            history_candidate=history_candidate,
            event_id=event_id,
            timestamp=timestamp,
        )

    def report_and_write(
        self,
        *,
        directory: Path | None = None,
        overwrite: bool = False,
        **report_kwargs,
    ) -> tuple[Event, Path]:
        """report() and then write the Event locally (see local_output.write_event_json)."""
        event = self.report(**report_kwargs)
        path = write_event_json(event, directory=directory, overwrite=overwrite)
        return event, path

    def send(self, event: Event) -> None:
        """Hand an already-created Event to this Reporter's configured Transport.

        Reporter does not implement delivery itself. Raises ReporterConfigError
        if no Transport was configured; propagates TransportError unchanged if
        the configured Transport fails to deliver.
        """
        if self.transport is None:
            raise ReporterConfigError(
                "no Transport configured on this Reporter; pass transport=... "
                "to Reporter() before calling send()/report_and_send()"
            )
        self.transport.send(event)

    def report_and_send(self, **report_kwargs) -> Event:
        """report() and then send() through this Reporter's configured Transport."""
        event = self.report(**report_kwargs)
        self.send(event)
        return event
