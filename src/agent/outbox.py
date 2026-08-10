"""Durable local Event outbox for the Multi-Desktop Agent.

The Agent's guarantee is "an Event that was created is never lost, even if
the network, OneDrive, or Desktop 4 is unavailable". That guarantee lives
here, and it is built out of exactly two directories:

    runtime/agent/outbox/   an Event created but not yet handed to Transport
    runtime/agent/sent/     an Event Transport accepted

An Event is written to `outbox/` **before** any send is attempted, and
moved to `sent/` **after** the send returns. Both steps are atomic (temp
file + `os.replace`), so a crash at any instant leaves the Event in
exactly one of: not created, in outbox (will be retried), in sent (done).
There is no state in which it exists half-written or not at all.

Why re-sending is safe
----------------------
A crash between "Transport accepted" and "moved to sent/" leaves the Event
in `outbox/`, so the next run sends it again. That is harmless at every
downstream layer, and each layer was already built that way:

    OneDriveTransport._write_atomic()  same filename already present -> no-op
    transport.run_intake()             already in incoming/processed/rejected
                                       -> skipped_already_present
    Collector                          event_id already seen -> DUPLICATE

so a duplicate delivery costs one redundant file copy and produces no
duplicate History and no duplicate Notion write. Losing the Event would
cost Company History permanently. The asymmetry is why the outbox errs
towards re-sending.

Filenames are `safe_event_filename(event_id)`, reused verbatim from
`reporter/local_output.py` rather than re-derived — the outbox, the
OneDrive staging folder, and Desktop 4's incoming/ must agree on what one
Event's file is called, or the dedup above silently stops working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from events import Event, EventValidationError
from reporter.local_output import safe_event_filename, write_event_json
from transport import Transport, TransportError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTBOX_DIR = PROJECT_ROOT / "runtime" / "agent" / "outbox"
DEFAULT_SENT_DIR = PROJECT_ROOT / "runtime" / "agent" / "sent"


@dataclass(frozen=True)
class DrainSummary:
    sent: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]
    unreadable: tuple[tuple[str, str], ...]

    @property
    def is_clear(self) -> bool:
        """True when nothing is left waiting in the outbox.

        An unreadable file counts as *not* clear: it is still occupying the
        outbox and still needs a human, so a run that produced one must not
        report success and must not advance the collection date past it.
        """
        return not self.failed and not self.unreadable


def stage(event: Event, outbox_dir: Path) -> Path:
    """Persist `event` in the outbox, atomically and idempotently.

    Re-staging an Event that is already in the outbox is a no-op rather
    than an error. The Agent derives `event_id` deterministically, so a
    re-run of the same date re-stages byte-identical Events; treating that
    as a collision would turn a normal retry into a failure. Same
    reasoning, and the same "same event_id always means the same content"
    justification, as `transport.onedrive._write_atomic()`.
    """
    outbox_dir = Path(outbox_dir)
    existing = outbox_dir / safe_event_filename(event.event_id)
    if existing.exists():
        return existing
    try:
        return write_event_json(event, directory=outbox_dir, overwrite=False)
    except FileExistsError:
        # Only the idempotent case may be swallowed: the Event file appeared
        # between the check above and the write. Anything else raising
        # FileExistsError means the Event was NOT persisted — notably
        # `mkdir(parents=True)` hitting a plain file where `outbox_dir` must
        # be, which fault injection caught reporting a phantom success and
        # letting the date advance with the Event nowhere on disk.
        if not existing.exists():
            raise
        return existing


def pending(outbox_dir: Path) -> tuple[Path, ...]:
    """Every Event file still waiting to be sent, in stable filename order."""
    outbox_dir = Path(outbox_dir)
    if not outbox_dir.is_dir():
        return ()
    return tuple(sorted(outbox_dir.glob("*.json")))


def is_sent(event_id: str, sent_dir: Path) -> bool:
    """Whether this event_id was already delivered in an earlier run.

    Lets the Agent skip re-staging an Event it has already sent — the
    "이미 처리된 Event 재전송 방지" requirement — without consulting
    Desktop 4, which may be switched off.
    """
    return (Path(sent_dir) / safe_event_filename(event_id)).exists()


def drain(
    transport: Transport,
    *,
    outbox_dir: Path,
    sent_dir: Path,
) -> DrainSummary:
    """Try to deliver every pending Event, oldest filename first.

    One Event's failure never stops the batch (the same isolation rule
    `collector/runtime.py::run_once()` applies per file): a failed send
    leaves that Event in the outbox and moves on. The caller decides what a
    non-clear summary means for state — `agent.py` treats it as "do not
    advance the collection date".

    A file that cannot be read or parsed is reported separately from a
    delivery failure and is also left in place. It is never deleted and
    never sent: an Event the Agent cannot understand must not be silently
    dropped from Company History.
    """
    outbox_dir = Path(outbox_dir)
    sent_dir = Path(sent_dir)

    sent: list[str] = []
    failed: list[tuple[str, str]] = []
    unreadable: list[tuple[str, str]] = []

    try:
        sent_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Nothing can be filed, so nothing should be sent: delivering an
        # Event we then cannot record as delivered means re-sending it on
        # every future run, forever. Report the whole batch as failed and
        # leave every Event in the outbox — this function's contract is to
        # report failures, not to raise them at the Agent.
        return DrainSummary(
            sent=(),
            failed=tuple(
                (path.stem, f"sent directory unusable: {exc}") for path in pending(outbox_dir)
            )
            or ((str(sent_dir), f"sent directory unusable: {exc}"),),
            unreadable=(),
        )

    for path in pending(outbox_dir):
        try:
            event = Event.from_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, EventValidationError) as exc:
            unreadable.append((path.name, str(exc)))
            continue

        try:
            transport.send(event)
        except TransportError as exc:
            failed.append((event.event_id, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001
            # A Transport that raises something other than TransportError is
            # violating its own interface, but the Agent's job is still to
            # keep the Event rather than crash out of the loop and abandon
            # the rest of the batch.
            failed.append((event.event_id, str(exc)))
            continue

        destination = sent_dir / path.name
        try:
            os.replace(path, destination)
        except OSError as exc:
            # Delivered, but the local bookkeeping move failed. The Event
            # stays in the outbox and will be re-sent next run — see this
            # module's docstring for why that is the safe direction.
            failed.append((event.event_id, f"delivered but could not be filed: {exc}"))
            continue

        sent.append(event.event_id)

    return DrainSummary(sent=tuple(sent), failed=tuple(failed), unreadable=tuple(unreadable))
