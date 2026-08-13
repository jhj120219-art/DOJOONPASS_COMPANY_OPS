"""Delivery reconciliation — detection only (BACKLOG E-9 / E-9b).

`sent/` means exactly one thing: `transport.send()` returned without
raising. It does not mean the Event is in the sync folder in readable form.

`OneDriveTransport.send()` skips writing when the destination already
exists, in *any* shape (`_write_atomic`'s `final_path.exists()`
short-circuit). The sync folder is managed by the OneDrive client, which
produces exactly such shapes on its own: Files On-Demand placeholders are
0 bytes, interrupted transfers truncate, conflict resolution leaves
residue. So an Event can be filed as sent while the destination holds
something that is not it.

Measured end to end on a real Agent run (BACKLOG E-9b):

    sync folder file                     still 0 bytes  -- never delivered
    agent/sent/                          contains the event_id
    last_successful_collection_date      advanced past that date
    Agent exit code / log                0 / COLLECTED
    any warning anywhere                 none

This module answers "did that actually arrive?" for the shapes where the
answer is knowable.

**Absence is not a failure.** Once Desktop 4 promotes the file out of the
sync folder it is legitimately gone, and that is the steady state — so a
missing destination is reported as nothing at all. What is reportable is a
destination that is *present and is not the Event*: 0 bytes, a directory,
unrelated content, or a different `event_id`. Measured, all four are
distinguishable from both clean cases with no overlap.

Detection only, and deliberately so — same restraint as
`history/reconciliation.py` and `scheduler/consistency.py`. Re-sending
would mean overwriting a sync-folder entry, which is the race question
E-9 is blocked on (Phase 5.15's staging buffer exists because of it).
Deciding it here would be deciding it by implementation.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from transport.onedrive import safe_event_filename

# Same pool sizing and same measured reason as `app/desktop_activity.py`
# and `history/reconciliation.py`: this reads every record in `sent/` plus
# its destination, and on this machine a file open costs ~5.3 ms. The pool
# is worth 4.2x on a cold cache at 20,000 files (8.9 s -> 2.1 s); the larger
# factor this comment used to quote came from a cache-ordering artefact —
# see `app/desktop_activity.py`'s `_READ_WORKERS` and BACKLOG section D.
# Unthreaded, a directory of that size is unusable in a status
# view that is meant to be the first thing an operator runs.
#
# A pure read path whose output is aggregated, so threading changes only
# how long it takes. Write paths (`outbox.drain()`, `run_intake()`) were
# deliberately left serial for the opposite reason.
_READ_WORKERS = max(4, min(16, (os.cpu_count() or 4) * 2))


class DeliveryProblem:
    """Why a destination is not the Event that was filed as sent."""

    NOT_A_FILE = "NOT_A_FILE"
    EMPTY = "EMPTY"
    UNREADABLE = "UNREADABLE"
    DIFFERENT_EVENT = "DIFFERENT_EVENT"


@dataclass(frozen=True)
class UndeliveredEvent:
    event_id: str
    sent_record: Path
    destination: Path
    problem: str


@dataclass(frozen=True)
class DeliveryResult:
    undelivered: tuple[UndeliveredEvent, ...] = ()
    checked: int = 0
    # Filed as sent with no destination present. Counted, never reported:
    # this is what a normally-consumed Event looks like.
    absent: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.undelivered


def _problem(destination: Path, event_id: str) -> str | None:
    """None when the destination really is this Event."""
    if not destination.is_file():
        return DeliveryProblem.NOT_A_FILE
    try:
        raw = destination.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # `ValueError` covers `UnicodeDecodeError`. Without it a sync-folder
        # entry that is not valid UTF-8 escaped this function, then the
        # thread pool, then `find_undelivered_events()`, and crashed
        # `ops_status.py` — the read-only view whose entire contract is that
        # it answers even when the evidence is damaged.
        #
        # A truncated transfer is one of the shapes this module's own
        # docstring lists as the reason it exists, and `UNREADABLE` is one of
        # its four declared verdicts. Undecodable *is* unreadable; the guard
        # simply did not say so.
        return DeliveryProblem.UNREADABLE
    if not raw.strip():
        return DeliveryProblem.EMPTY
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        # Deeply nested JSON raises `RecursionError`, which is not a
        # `ValueError`. Both mean the destination is not this Event in
        # readable form, which is exactly what `UNREADABLE` reports.
        return DeliveryProblem.UNREADABLE
    if not isinstance(data, dict) or data.get("event_id") != event_id:
        return DeliveryProblem.DIFFERENT_EVENT
    return None


def find_undelivered_events(*, sent_dir: Path, sync_folder: Path) -> DeliveryResult:
    """Events filed as sent whose sync-folder destination is not them.

    The destination name comes from `safe_event_filename()` — the same
    derivation `OneDriveTransport.send()` writes with — so a sanitised
    `event_id` cannot make a delivered Event look undelivered.

    The `event_id` is read from the *sent record*, not from the filename,
    because the filename is a sanitised, lossy form of it.
    """
    sent_dir = Path(sent_dir)
    sync_folder = Path(sync_folder)
    if not sent_dir.is_dir() or not sync_folder.is_dir():
        return DeliveryResult()

    undelivered: list[UndeliveredEvent] = []
    checked = absent = 0

    records = sorted(sent_dir.glob("*.json"))

    def _verdict(record: Path):
        """(event_id, destination, problem). `event_id` None when the local
        record itself cannot be read.

        The whole per-record unit runs here — reading the record, stating
        the destination, reading it — because every one of those is a file
        open, and file opens are what this costs.
        """
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            event_id = data["event_id"]
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            # `RecursionError` for the same reason as in `_problem()` above:
            # deeply nested JSON does not raise `ValueError`.
            #
            # The local record itself is damaged. Not a delivery verdict —
            # `history/reconciliation.py` reports unreadable inputs
            # separately for the same reason, and inventing a verdict from
            # a file we cannot read would be the worst of both.
            return None, None, None
        destination = sync_folder / safe_event_filename(event_id)
        if not destination.exists():
            return event_id, destination, "ABSENT"
        return event_id, destination, _problem(destination, event_id)

    if records:
        with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
            # `map` preserves input order, so results stay in sorted
            # filename order exactly as the serial version produced them.
            verdicts = list(pool.map(_verdict, records))
    else:
        verdicts = []

    for record, (event_id, destination, problem) in zip(records, verdicts):
        if event_id is None:
            continue

        checked += 1
        if problem == "ABSENT":
            absent += 1
            continue

        if problem is not None:
            undelivered.append(
                UndeliveredEvent(
                    event_id=event_id,
                    sent_record=record,
                    destination=destination,
                    problem=problem,
                )
            )

    return DeliveryResult(
        undelivered=tuple(undelivered), checked=checked, absent=absent
    )
