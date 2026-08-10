"""What Desktop 4 can tell about every other Desktop, from data it already has.

The gap this closes
-------------------
An Agent's own status lives on its own machine (`agent/status.py`). The COO
cannot read Desktop 1's `agent_state.json` — it is on Desktop 1, which may
be switched off. So "Desktop 1 has been failing to deliver for three days"
was invisible from the only seat that would act on it.

Desktop 4 is not, however, blind. Every Event it has ever collected carries
`source`, `role`, and `timestamp`, and those files are still on disk in
`runtime/events/processed/`. From them Desktop 4 can derive, with no new
Event type, no heartbeat, no new wire format, and no change to any spec:

    which Desktops have ever reported
    when each one was last heard from
    how many days of silence that is
    which Desktops are currently silent
    how much is queued but not yet collected (transport/ + incoming/)
    how much was refused (rejected/)

Silence is reported, never interpreted
--------------------------------------
"Desktop 2 has sent nothing for four days" has at least three innocent
explanations — the machine was off, the CMO did no Event-worthy work, or
the Agent is broken — and this module deliberately does not guess which.
Fully separating them needs a heartbeat, and a heartbeat needs an Event
type `events.schema.EVENT_TYPES` does not have (docs/02 §9's restraint rule
applies), so that is a decision for the CEO/COO, recorded in BACKLOG.md
rather than invented here.

One of the three can nevertheless be told apart with what is already on
disk. An Event carries the date the WORK happened; the file carries the
time it ARRIVED. A Desktop that was off for a week and then caught up
delivers week-old work today — identical to a broken Desktop if you look
only at the work dates, and obviously different if you also look at when
the files turned up. `days_since_arrival()` supplies that second fact.

It is treated as evidence rather than measurement: the arrival time comes
from file mtime carried across OneDrive, which this code cannot verify was
preserved. So it is always reported *next to* the silence, never in place
of it, and it never suppresses an alarm — a false reassurance about a dead
Desktop would be worse than the false alarm it replaced.

Cost note
---------
This reads and parses every file in `processed/`, which grows without
bound. Measured serially on this machine: 5.4 ms per file, essentially all
of it the file *open* rather than the JSON parse — 24 s at 5,000 Events and
107 s at 20,000. A status command that takes two minutes is one nobody
runs, which would have quietly undone the point of having it.

The work is pure I/O — the reads block, they do not compute — so it is done
on a small thread pool: 24 s -> 3.3 s at 5,000 files and 107 s -> ~18 s at
20,000, measured cold. Results are byte-identical because the parsed values
are folded back in sorted filename order exactly as before. No retention
policy, no cache file, no change to what is reported — just not waiting on
one file open at a time.

This treatment is deliberately confined to the status view. The same
per-file cost exists in `outbox.drain()` and `transport.run_intake()`, but
those are write paths whose ordering and per-file failure isolation are
part of their contract; parallelising them would trade a real guarantee for
speed nobody has asked for. A read-only diagnostic has no such contract.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from events import SOURCES

# Measured on a cold cache at 5,000 files: 8 workers -> 4.7 s, 16 -> 3.3 s,
# 32 and 64 -> 3.3 s. The plateau is at 16, so there is nothing to gain from
# a larger pool and every reason not to spawn one on the operator's own
# desktop. Bounded by CPU count as well so a small machine does not get a
# pool wildly out of proportion to it.
_READ_WORKERS = max(4, min(16, (os.cpu_count() or 4) * 2))

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "runtime" / "events" / "processed"
DEFAULT_TRANSPORT_DIR = PROJECT_ROOT / "runtime" / "events" / "transport"
DEFAULT_INCOMING_DIR = PROJECT_ROOT / "runtime" / "events" / "incoming"
DEFAULT_REJECTED_DIR = PROJECT_ROOT / "runtime" / "events" / "rejected"


@dataclass(frozen=True)
class DesktopActivity:
    source: str
    event_count: int = 0
    roles: tuple[str, ...] = ()
    first_event_at: str | None = None
    last_event_at: str | None = None
    last_arrival_at: float | None = None

    @property
    def has_ever_reported(self) -> bool:
        return self.event_count > 0

    @property
    def last_event_date(self) -> date_type | None:
        if self.last_event_at is None:
            return None
        try:
            return datetime.fromisoformat(self.last_event_at).date()
        except ValueError:
            return None

    def days_silent(self, now: datetime) -> int | None:
        """Whole days since this Desktop's most recent Event, or None if it
        has never reported at all.

        Measured on the Event's own `timestamp` — the day the WORK happened,
        which is what Company History is organised by. See
        `days_since_arrival()` for the other, quite different, question.
        """
        last = self.last_event_date
        if last is None:
            return None
        return max(0, (now.date() - last).days)

    def days_since_arrival(self, now: datetime) -> int | None:
        """Whole days since a file from this Desktop last appeared here.

        A weaker fact than `days_silent()`, and deliberately kept separate
        from it. Its source is the modification time of the collected Event
        file, which is set when the sending Agent wrote it and is carried
        through the copy/move chain — but that chain crosses OneDrive, and
        this code cannot verify that OneDrive preserved it. So it is
        evidence, not a measurement, and nothing below ever silences an
        alarm on the strength of it.

        What it is good for is telling two very different situations apart:

            work 5 days old, file 5 days old   nothing has come from this
                                               Desktop at all
            work 5 days old, file 1 day old    the Desktop was off and has
                                               since caught up — it is alive

        Both look identical without it, and only the first is worrying.
        """
        if self.last_arrival_at is None:
            return None
        arrived = datetime.fromtimestamp(self.last_arrival_at, tz=now.tzinfo)
        return max(0, (now.date() - arrived.date()).days)

    def caught_up_recently(self, now: datetime, *, days: int) -> bool:
        """True when this Desktop looks silent by work date but a file from
        it arrived inside `days`. Reported alongside the silence, never
        instead of it."""
        silent = self.days_silent(now)
        arrival = self.days_since_arrival(now)
        if silent is None or arrival is None:
            return False
        return silent >= days > arrival


@dataclass(frozen=True)
class IntakeBacklog:
    """Work that has arrived but is not yet Company History.

    `awaiting_intake` is the OneDrive-side mirror Desktop 4 has not promoted
    yet; `awaiting_collection` has been promoted but not collected. Both
    being non-zero right after a run means something stopped the Runner
    partway; both being zero is the steady state.
    """

    awaiting_intake: int = 0
    awaiting_collection: int = 0
    rejected: int = 0

    @property
    def is_clear(self) -> bool:
        return not (self.awaiting_intake or self.awaiting_collection)


@dataclass(frozen=True)
class CompanyActivitySnapshot:
    desktops: tuple[DesktopActivity, ...]
    backlog: IntakeBacklog
    unreadable_events: tuple[str, ...] = field(default_factory=tuple)

    def for_source(self, source: str) -> DesktopActivity:
        for activity in self.desktops:
            if activity.source == source:
                return activity
        raise KeyError(source)

    @property
    def never_reported(self) -> tuple[str, ...]:
        return tuple(a.source for a in self.desktops if not a.has_ever_reported)

    def silent_for(self, now: datetime, *, days: int) -> tuple[str, ...]:
        """Desktops whose last Event is at least `days` old, plus any that
        have never reported. Ordered as `desktops` is."""
        result = []
        for activity in self.desktops:
            silent = activity.days_silent(now)
            if silent is None or silent >= days:
                result.append(activity.source)
        return tuple(result)


def _read_one(path: Path):
    """Parse one collected Event file, returning `(data, mtime)`.

    `data` is None if the file cannot be read. Both failure kinds collapse
    to None on purpose: from a diagnostic's point of view "the disk refused
    it" and "it is not JSON" are the same answer — this file cannot
    contribute, and a human should be told it exists.

    `mtime` is read in the same worker because it answers a question the
    Event's own content cannot: WHEN this file showed up, as opposed to
    when the work it describes happened. See `DesktopActivity.last_arrival_at`.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8")), path.stat().st_mtime
    except (OSError, ValueError):
        return None, None


def _read_all(paths: list[Path]) -> list[tuple[Path, tuple]]:
    """Read every path, in parallel, returning results in the given order.

    Order is preserved so the fold that consumes this stays deterministic:
    `unreadable_events` lists filenames in sorted order, and `first`/`last`
    tie-breaking behaves exactly as the previous serial loop did. Threads
    change only how long the reads take, never what they produce.
    """
    if not paths:
        return []
    with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
        return list(zip(paths, pool.map(_read_one, paths)))


def _count(directory: Path) -> int:
    path = Path(directory)
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.glob("*.json"))


def read_company_activity(
    *,
    processed_dir: Path | None = None,
    transport_dir: Path | None = None,
    incoming_dir: Path | None = None,
    rejected_dir: Path | None = None,
) -> CompanyActivitySnapshot:
    """Summarise every Desktop's delivery history from collected Events.

    Every source in `events.SOURCES` appears in the result, including ones
    that have never sent anything — a Desktop missing from a report and a
    Desktop that reported nothing look identical to a reader, and only one
    of those is fine.

    A processed file that cannot be read or parsed is counted in
    `unreadable_events` rather than raising. This is a diagnostic; it must
    still produce an answer when part of the evidence is damaged, and the
    damage is itself something worth reporting.
    """
    processed_dir = Path(processed_dir) if processed_dir is not None else DEFAULT_PROCESSED_DIR
    transport_dir = Path(transport_dir) if transport_dir is not None else DEFAULT_TRANSPORT_DIR
    incoming_dir = Path(incoming_dir) if incoming_dir is not None else DEFAULT_INCOMING_DIR
    rejected_dir = Path(rejected_dir) if rejected_dir is not None else DEFAULT_REJECTED_DIR

    counts: dict[str, int] = {}
    roles: dict[str, list[str]] = {}
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    last_arrival: dict[str, float] = {}
    unreadable: list[str] = []

    if processed_dir.is_dir():
        for path, (data, mtime) in _read_all(sorted(processed_dir.glob("*.json"))):
            if data is None:
                unreadable.append(path.name)
                continue
            if not isinstance(data, dict):
                unreadable.append(path.name)
                continue

            source = data.get("source")
            timestamp = data.get("timestamp")
            if not isinstance(source, str) or not isinstance(timestamp, str):
                unreadable.append(path.name)
                continue

            counts[source] = counts.get(source, 0) + 1

            role = data.get("role")
            if isinstance(role, str) and role not in roles.setdefault(source, []):
                roles[source].append(role)

            # String comparison is correct here only for same-offset
            # timestamps, so parse instead — Events can legitimately carry
            # different UTC offsets (tests/test_spec_conformance.py pins
            # that the schema accepts a non-KST offset).
            if source not in first_seen or _before(timestamp, first_seen[source]):
                first_seen[source] = timestamp
            if source not in last_seen or _before(last_seen[source], timestamp):
                last_seen[source] = timestamp

            if mtime is not None and mtime > last_arrival.get(source, 0.0):
                last_arrival[source] = mtime

    desktops = tuple(
        DesktopActivity(
            source=source,
            event_count=counts.get(source, 0),
            roles=tuple(roles.get(source, ())),
            first_event_at=first_seen.get(source),
            last_event_at=last_seen.get(source),
            last_arrival_at=last_arrival.get(source),
        )
        for source in sorted(SOURCES)
    )

    return CompanyActivitySnapshot(
        desktops=desktops,
        backlog=IntakeBacklog(
            awaiting_intake=_count(transport_dir),
            awaiting_collection=_count(incoming_dir),
            rejected=_count(rejected_dir),
        ),
        unreadable_events=tuple(unreadable),
    )


def _before(left: str, right: str) -> bool:
    """True when `left` is strictly earlier than `right`.

    Falls back to string order when either value cannot be parsed, so a
    hand-corrupted Event affects only its own ordering rather than
    collapsing the whole comparison.
    """
    try:
        return datetime.fromisoformat(left) < datetime.fromisoformat(right)
    except ValueError:
        return left < right
