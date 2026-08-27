"""Transport interface Reporter sends Events through.

The seam Reporter plugs into. A Transport's job is exactly: attempt to
deliver one Event. Everything about *how* delivery happens (retry, local
queueing, dedup, security) is each concrete Transport's own responsibility —
this interface intentionally knows nothing about any of it.

The production implementation is `OneDriveTransport` (COO Architecture
Decision, Phase 5.1 / 5.15 / 5.2). `run_agent.py` builds it, AGENT.md §1
draws the path it writes into, and `COMPANY_OPS_AGENT_SYNC_FOLDER` is the
folder it hands files to. `InMemoryTransport` is a test double and says so.

**This paragraph replaced one that was three decisions out of date (C122).**
Every clause of it was false — the choice was made and shipped, the three
classes it offered were never written, and the document it cited is not in
`docs/`. A reader who opened the seam's own definition to find out what
delivers an Event was told the question was still open and pointed at four
things that do not exist. What it said, quoted whole:

    No concrete production Transport is chosen yet (GitHub / OneDrive / USB /
    SharedFolder are all still open — see the Event Transport analysis) ...
    so a real Transport can be dropped in later
    ...
    Concrete implementations (e.g. GitHubTransport, USBTransport,
    SharedFolderTransport) are chosen and built in a later phase.

`TransportSeamNamesWhatActuallyDeliversTests` now checks both halves against
the tree, so this cannot go stale again in silence. **Every class name in
that quotation is indented on purpose** — the check reads claims at the
margin and treats an indented block as a quotation, so the record of the
defect cannot be mistaken for the defect.
"""

from __future__ import annotations

import abc

from events import Event


class TransportError(Exception):
    """Raised by a Transport implementation when it cannot deliver an Event."""


class Transport(abc.ABC):
    """Reporter's only dependency for handing an Event off for delivery.

    Two implementations exist: `OneDriveTransport` (production) and
    `InMemoryTransport` (test double). This used to name three more, all
    candidates that were never written — see the quotation in the module
    docstring above (C122).
    """

    @abc.abstractmethod
    def send(self, event: Event) -> None:
        """Attempt to deliver `event`. Raise TransportError on failure."""
        raise NotImplementedError
