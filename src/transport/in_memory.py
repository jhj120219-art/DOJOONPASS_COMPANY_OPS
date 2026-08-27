"""InMemoryTransport — a test/local-development double for the Transport interface.

Not a production Transport, and not a candidate for becoming one: it keeps
Events in a list that dies with the process. The production Transport is
`OneDriveTransport`, which `run_agent.py` builds.

This exists so Reporter-to-Transport wiring can be exercised without
touching a sync folder. The second sentence here used to read:

    This is NOT a production Transport candidate (see the Event Transport
    analysis for the real candidates: GitHub / OneDrive / USB / SharedFolder).

— a document that is not in `docs/`, and a decision that had already been
made (C122).
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
