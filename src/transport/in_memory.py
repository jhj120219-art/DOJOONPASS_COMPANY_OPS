"""InMemoryTransport — a test/local-development double for the Transport interface.

This is NOT a production Transport candidate (see the Event Transport
analysis for the real candidates: GitHub / OneDrive / USB / SharedFolder).
It exists only so Reporter-to-Transport wiring can be exercised in tests
without committing to any of them.
"""

from __future__ import annotations

from events import Event

from .interface import Transport


class InMemoryTransport(Transport):
    """Collects sent Events in a local list. Test/dev use only."""

    def __init__(self) -> None:
        self.sent: list[Event] = []

    def send(self, event: Event) -> None:
        self.sent.append(event)
