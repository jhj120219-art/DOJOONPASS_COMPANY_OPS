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
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from events import SOURCES
from transport.intake import _is_parseable_json  # reuse intake's own test

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
class SourceBreakdown:
    """Which Desktops a directory's files claim to come from.

    Attribution is best-effort by construction, and the two ways it can fail
    are kept apart from each other on purpose:

        by_source      files whose `source` is one of `events.SOURCES`
        unattributed   everything else

    "Everything else" covers a file that is not JSON at all, one that is
    JSON but not an object, one with no `source` field, and one whose
    `source` is a string no Desktop is allowed to send. Those are different
    accidents but the same answer to the only question asked here — this
    file cannot be blamed on a Desktop — and collapsing them keeps the count
    honest instead of inventing a Desktop for it.

    A `source` outside `SOURCES` is deliberately NOT reported under the name
    it claims. Every file counted here failed to become an Event, so its
    contents are untrusted input; echoing an arbitrary string from one into
    an operator's terminal is the same mistake `oplog.append_line()` escapes
    against. The count still surfaces — as `unattributed`, which is the true
    statement — and the file itself is still on disk for a human to open.

    `total` always equals the plain file count the caller already had, so
    adding this breakdown can never change what the backlog numbers say.
    """

    by_source: tuple[tuple[str, int], ...] = ()
    unattributed: int = 0

    @property
    def total(self) -> int:
        return sum(count for _, count in self.by_source) + self.unattributed

    def describe(self) -> str:
        """`"DESKTOP_1=2 DESKTOP_3=1 unattributed=1"`, empty when nothing.

        Neutral wording on purpose: this module is data, and the operator-
        facing sentence it lands in belongs to `ops_status.py`.
        """
        parts = [f"{source}={count}" for source, count in self.by_source]
        if self.unattributed:
            parts.append(f"unattributed={self.unattributed}")
        return " ".join(parts)


@dataclass(frozen=True)
class IntakeBacklog:
    """Work that has arrived but is not yet Company History.

    `awaiting_intake` is the OneDrive-side mirror Desktop 4 has not promoted
    yet; `awaiting_collection` has been promoted but not collected. Both
    being non-zero right after a run means something stopped the Runner
    partway; both being zero is the steady state.

    `unparseable` is counted separately, and that separation is the point.
    `transport.run_intake()` leaves a file it cannot parse where it is —
    never promoted, never moved, never deleted — and re-judges it on every
    run. Counting those as "awaiting intake" made the sentence above false:
    measured, a single 0-byte file (the shape OneDrive Files On-Demand
    produces) held `awaiting_intake` at 1 across four consecutive clean
    runs, so ATTENTION reported "수집되지 않고 남은 Event" forever with
    nothing wrong and nothing an operator could do to clear it.

    An alert that cannot clear is worse than no alert: ATTENTION is where
    real problems appear, and a permanent entry trains people to skim past
    it. Such a file still needs a human — it is simply a different message,
    the one `rejected` already gets.
    """

    awaiting_intake: int = 0
    awaiting_collection: int = 0
    rejected: int = 0
    unparseable: int = 0
    # Files whose mtime is ahead of this machine's clock, so
    # `run_intake._is_stable()` holds them back until wall-clock time
    # catches up (BUG-30). Counted, never subtracted from
    # `awaiting_intake`: they really are still queued, and the missing
    # information was never the number — it was why the number is stuck.
    future_dated: int = 0
    # Files in `incoming/` whose name is already taken in `processed/` or
    # `rejected/`. `collector/runtime.run_once()` will not overwrite a
    # destination, so each one fails on every run and never leaves
    # `incoming/` (BUG-43). Counted for the same reason as `future_dated`:
    # the backlog number is correct, the reason it is stuck was missing.
    name_collision: int = 0
    # Which Desktop each of the three in-flight piles came from. The totals
    # above are unchanged and remain the authority; these only say who.
    #
    # Without them the counts are company-wide sums, so "Collector가 거부한
    # Event 3건" left an operator to open `runtime/events/rejected/` by hand
    # to learn whether that was one Desktop misbehaving or three Desktops
    # each hitting the same schema change. Those two situations need
    # opposite reactions and looked identical (BACKLOG E-10).
    #
    # `unparseable` gets no breakdown: a file intake could not parse is one
    # whose `source` cannot be read either, so every one of them would land
    # in `unattributed` and the field would say nothing the count does not.
    awaiting_intake_sources: "SourceBreakdown" = field(default_factory=lambda: SourceBreakdown())
    awaiting_collection_sources: "SourceBreakdown" = field(
        default_factory=lambda: SourceBreakdown()
    )
    rejected_sources: "SourceBreakdown" = field(default_factory=lambda: SourceBreakdown())

    @property
    def is_clear(self) -> bool:
        """Nothing is in flight.

        `unparseable` is deliberately excluded: those files are not in
        flight, they are parked. Including them would make `is_clear`
        permanently False for a condition no run can resolve.
        """
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
    except (OSError, ValueError, RecursionError):
        # `RecursionError` for the same reason `ValueError` is here: it is
        # what `json.loads()` raises on deeply nested input, and it is a
        # `RuntimeError` subclass rather than a `ValueError` one. Collapsing
        # it to None keeps the promise three lines up — "this file cannot
        # contribute, and a human should be told it exists" — instead of
        # taking the whole COMPANY view down with it.
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


def _attribute(paths: list[Path]) -> SourceBreakdown:
    """Which Desktop each of `paths` claims to come from. Never raises.

    Reads through the same thread pool the `processed/` scan uses, and folds
    the results back in the given order, so the answer does not depend on
    how the pool happened to schedule the reads.

    Only a `source` that is an actual member of `events.SOURCES` is
    attributed — see `SourceBreakdown` for why a file claiming anything else
    is counted rather than quoted.
    """
    counts: dict[str, int] = {}
    unattributed = 0
    for _path, (data, _mtime) in _read_all(paths):
        source = data.get("source") if isinstance(data, dict) else None
        if isinstance(source, str) and source in SOURCES:
            counts[source] = counts.get(source, 0) + 1
        else:
            unattributed += 1
    return SourceBreakdown(by_source=tuple(sorted(counts.items())), unattributed=unattributed)


def _json_paths(directory: Path) -> list[Path]:
    """Every `*.json` in `directory`, sorted; empty when it does not exist.

    Replaces the pair of separate `glob()` passes the count and the
    attribution would otherwise each make. One listing is not a tidiness
    preference here: two would let a file appear between them, and then
    `SourceBreakdown.total` would disagree with the count it is breaking
    down — a view contradicting itself is exactly what this view exists to
    catch elsewhere.
    """
    path = Path(directory)
    if not path.is_dir():
        return []
    return sorted(path.glob("*.json"))


def _count_transport(directory: Path) -> tuple[int, int, int, SourceBreakdown]:
    """(promotable, unparseable, future_dated, who-sent-the-promotable).

    Applies `transport.run_intake()`'s own parse test rather than a second
    opinion about what "valid" means, so this view cannot disagree with the
    step it is reporting on.

    The promotable files are then read a second time to attribute them.
    That is deliberate rather than merged into one read: the verdict has to
    come from intake's own predicate — a test pins that it does — and
    intake's predicate takes a path, not text. transport/ holds only what is
    in flight (normally nothing, briefly a handful), so the second read
    costs nothing worth trading that agreement for.

    `future_dated` is the count of files whose mtime is ahead of this clock.
    `run_intake._is_stable()` decides a file has finished arriving with
    `(now - mtime) >= stable_after_seconds`, which assumes mtime is in the
    past. OneDrive preserves the *sending* Desktop's mtime, so a Desktop
    whose clock runs fast stamps files in the future and the subtraction
    goes negative — the file is held back until wall-clock time catches up,
    which can be a day or a year (BUG-30).

    It is reported, not subtracted from `awaiting_intake`. Whether such a
    file is "in flight" is exactly the judgement BUG-30 records as open, and
    `unparseable` was excluded only because those files are provably parked
    forever. What was missing is not a different number — it is the reason
    the number does not move. Measured: three consecutive runs, `moved=0`
    every time, `transport=1` on screen every time, and nothing anywhere
    saying why.
    """
    promotable: list[Path] = []
    unparseable = 0
    future_dated = 0
    now = time.time()
    for entry in _json_paths(directory):
        try:
            if entry.stat().st_mtime > now:
                future_dated += 1
        except OSError:
            pass
        if _is_parseable_json(entry):
            promotable.append(entry)
        else:
            unparseable += 1
    return (len(promotable), unparseable, future_dated, _attribute(promotable))


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

    processed_paths = _json_paths(processed_dir)
    if processed_dir.is_dir():
        for path, (data, mtime) in _read_all(processed_paths):
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

    promotable, unparseable, future_dated, promotable_sources = _count_transport(
        transport_dir
    )
    incoming_paths = _json_paths(incoming_dir)
    rejected_paths = _json_paths(rejected_dir)

    # Files in `incoming/` that the Collector can never move, because
    # `collector/runtime.run_once()` refuses a destination whose name is
    # already taken and leaves the file where it is. The verdict does not
    # matter — ACCEPTED and DUPLICATE both target `processed/` — so a name
    # collision is a permanent FAILED, every run, forever (BUG-43).
    #
    # Free to compute: `processed_paths` is the list the scan above already
    # walked, and `rejected_paths` is needed for the rejected count anyway.
    # No directory is read a second time.
    #
    # Reported, not reclassified. `awaiting_collection` still counts these —
    # they really are sitting in `incoming/` — and `is_clear` is untouched.
    # Reconciling the two notions of "already handled" is the decision
    # BUG-43 records; the missing information was the reason the number
    # never moves.
    taken_names = {path.name for path in processed_paths} | {
        path.name for path in rejected_paths
    }
    name_collision = sum(1 for path in incoming_paths if path.name in taken_names)
    return CompanyActivitySnapshot(
        desktops=desktops,
        backlog=IntakeBacklog(
            awaiting_intake=promotable,
            unparseable=unparseable,
            future_dated=future_dated,
            awaiting_collection=len(incoming_paths),
            name_collision=name_collision,
            rejected=len(rejected_paths),
            awaiting_intake_sources=promotable_sources,
            awaiting_collection_sources=_attribute(incoming_paths),
            rejected_sources=_attribute(rejected_paths),
        ),
        unreadable_events=tuple(unreadable),
    )


def _before(left: str, right: str) -> bool:
    """True when `left` is strictly earlier than `right`.

    Falls back to string order when the two cannot be compared as instants,
    so a hand-corrupted Event affects only its own ordering rather than
    collapsing the whole comparison.

    `TypeError` alongside `ValueError`, because there are two ways the
    comparison can fail and the guard only covered one. `ValueError` is a
    value that does not parse; `TypeError` is
    "can't compare offset-naive and offset-aware datetimes", raised when one
    Event carries an offset and the other does not.

    That is reachable exactly where this function is meant to be robust.
    `validate_event()` requires an offset, but nothing re-validates a file
    already sitting in `processed/` — a legacy Event, a hand edit, or a
    restore from another tool can be naive. Measured: one such file beside a
    normal one raised out of `read_company_activity()` and took the whole
    COMPANY view of `ops_status.py` with it, which is the one thing a
    read-only diagnostic must never do (see this module's docstring: it
    "must still produce an answer when part of the evidence is damaged").
    """
    try:
        return datetime.fromisoformat(left) < datetime.fromisoformat(right)
    except (TypeError, ValueError):
        return left < right
