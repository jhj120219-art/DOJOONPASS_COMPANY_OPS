"""Transport interface Reporter sends Events through.

No concrete production Transport is chosen yet (GitHub / OneDrive / USB /
SharedFolder are all still open — see the Event Transport analysis). This
module defines only the seam Reporter plugs into, so a real Transport can
be dropped in later without touching Reporter or the Event Schema.

A Transport's job is exactly: attempt to deliver one Event. Everything
about *how* delivery happens (retry, local queueing, dedup, security) is
each concrete Transport's own responsibility — this interface intentionally
knows nothing about any of it.
"""

from __future__ import annotations

import abc

from events import Event


class TransportError(Exception):
    """Raised by a Transport implementation when it cannot deliver an Event."""


class Transport(abc.ABC):
    """Reporter's only dependency for handing an Event off for delivery.

    Concrete implementations (e.g. GitHubTransport, USBTransport,
    SharedFolderTransport) are chosen and built in a later phase.
    """

    @abc.abstractmethod
    def send(self, event: Event) -> None:
        """Attempt to deliver `event`. Raise TransportError on failure."""
        raise NotImplementedError
