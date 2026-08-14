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
from collector.runtime import is_readable_event_file  # reuse Collector's own test
from transport.intake import _is_parseable_json  # reuse intake's own test
from transport.intake import is_incomplete_write as _is_incomplete_write

# Measured on a cold cache at 5,000 files: 8 workers -> 4.7 s, 16 -> 3.3 s,
# 32 and 64 -> 3.3 s. The plateau is at 16, so there is nothing to gain from
# a larger pool and every reason not to spawn one on the operator's own
# desktop. Bounded by CPU count as well so a small machine does not get a
# pool wildly out of proportion to it.
#
# What the pool is worth, re-measured with benchmark ordering controlled —
# the earlier figure was inflated by it. Comparing a cold serial pass against
# a threaded pass over the files that serial pass had just cached makes the
# pool look ~8x better than it is; running the two orders on separate fresh
# file sets, whichever goes SECOND wins, because the dominant cost is the
# cold first open. Like for like, at 20,000 files:
#
#     cold serial   8.9 s      cold threaded   2.1 s     -> 4.2x, the real win
#     warm threaded 1.1 s      warm serial     0.94 s    -> pool costs 19%
#
# Cold is the operational case (this view reads files an earlier run wrote,
# and `processed/` accumulates for months), so the pool stays. The warm
# number is not a reason to remove it — it is the case not worth optimising.
# Recorded because the inflated figure nearly bought its own removal: C27
# measured the warm direction first and briefly had it 16x "slower".
# Full table and reasoning in BACKLOG section D.
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
    # Staging files (`.tmp-…json`) left in `transport/` by a writer that was
    # killed between its write and its `os.replace`. `run_intake()` skips
    # them by name, so they are *not* in flight and are excluded from
    # `awaiting_intake` — counting them there would hold the backlog at a
    # number no run can ever reduce, which is the exact failure this class's
    # docstring was written about. Surfaced as their own count because
    # nothing else on disk ever removes them.
    incomplete: int = 0
    # Files in `transport/` whose name already exists in `incoming/`,
    # `processed/` or `rejected/`. `run_intake()` skips them and never
    # deletes, so each one sits there for good — and a re-sent Event, the
    # outbox's own designed recovery from a crash mid-send, produces exactly
    # one. Excluded from `awaiting_intake` for the same reason `unparseable`
    # is: the file is not queued, it is already handled. See
    # `_count_transport()` for the measurement.
    already_collected: int = 0
    # The other half of the same verdict: a file in `transport/` blocked by a
    # downstream twin that is NOT the same Event — a directory, a 0-byte
    # placeholder, a different Event under a colliding name (BUG-53 /
    # BUG-47). That is an undelivered Event being silently suppressed, so
    # unlike `already_collected` it needs a human and stays in ATTENTION.
    suppressed: int = 0
    # Files in `incoming/` the Collector cannot read at all (undecodable
    # bytes, or an entry that is not a readable file). `run_once()` records
    # FAILED and leaves them, so they never reach `processed/` or
    # `rejected/` — the read is deterministic, so this repeats every run,
    # forever. Excluded from `awaiting_collection` for `unparseable`'s
    # reason, not `name_collision`'s: nothing is pending a decision here,
    # the file is simply parked. Measured — three consecutive runs,
    # `failed=1` every time, `incoming=1` on screen every time, no reason
    # given anywhere.
    #
    # Note this is a *different* predicate from `unparseable`: a valid-UTF-8
    # file holding invalid JSON is REJECTED by `collector.collect()` and
    # leaves `incoming/` on the first run, so counting it here would report
    # a file that is on its way out as stuck.
    unreadable_incoming: int = 0
    # The same staging files one directory earlier, in `incoming/` — the
    # window between the reporter dying and the next Collector run moving
    # them to `rejected/`.
    #
    # Three directories can hold `.tmp-…json` residue and two of them were
    # already naming it correctly (`incomplete` for `transport/`,
    # `rejected_incomplete_write` for `rejected/`). `incoming/` — the one
    # `write_event_json()` actually stages into — called it an Event.
    # Measured, one staging file and nothing else:
    #
    #     awaiting_collection=1  is_clear=False
    #     -> ATTENTION "Collector가 아직 가져가지 않은 Event 1건"
    #
    # `awaiting_collection` means *promoted by intake but not collected*,
    # and a staging file was never promoted — the local reporter wrote it
    # straight into `incoming/`. So it is not that number being reported
    # loosely; it is a file that does not belong in that number at all.
    #
    # Unlike its two siblings this one clears on the next run, because
    # `collector/runtime.run_once()` does consume it (docs/03's decision,
    # not a reader-side filter). One run of a wrong name, in the window
    # right after a crash — which is exactly when someone is reading this.
    incoming_incomplete_write: int = 0
    # Staging files (`.tmp-…json`) sitting in `rejected/`, which are not
    # rejected Events.
    #
    # C27 §8 measured this and left it named wrong. `write_event_json()`'s
    # default directory is `runtime/events/incoming/` and it `mkstemp`s
    # there, so a Desktop 4 reporter killed mid-write leaves a staging file
    # in the directory the Collector reads. `collector/runtime.run_once()`
    # deliberately does not skip it (docs/03's pipeline decides what it
    # consumes, and changing that is not a reader-side filter), so a
    # truncated one is REJECTED and moved here under its staging name.
    #
    # C27's own words for what remains: *"남는 것은 잘못 이름 붙은 경보
    # 하나"* — ATTENTION said "Collector가 거부한 Event 1건", and no Event
    # was rejected; a write stopped. Separating the count needs none of the
    # pipeline decision C27 was blocked on: it changes what the report is
    # called, not what the Collector consumes.
    #
    # Excluded from `rejected` rather than added to it, for `unparseable`'s
    # reason: the two need different sentences. A rejected Event means a
    # Desktop sent something the schema refused; this means a write on this
    # machine never finished, and the file is safe to delete.
    rejected_incomplete_write: int = 0
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

        Six of the counts above are deliberately outside this: those files
        are not in flight, they are parked, and including them would make
        `is_clear` permanently False for a condition no run can resolve.

            unparseable               intake judged it and will not promote it
            incomplete                a write that never committed; not an Event
            already_collected         the same Event, already handled
            suppressed                blocked by a name that is not it
            unreadable_incoming       the Collector cannot read it at all
            incoming_incomplete_write the same non-Event as `incomplete`,
                                      one directory further along

        `incoming_incomplete_write` is the one exception to the "no run can
        resolve it" reasoning: the next Collector run does move it. It is
        excluded anyway, because `is_clear` asks whether work is in flight
        and a write that never committed is not work — the same answer
        `incomplete` already gets for the same file in `transport/`.

        Three of these joined `unparseable` here rather than `future_dated`
        and `name_collision`, which stay counted: those two are stuck on
        open decisions (BUG-30 / BUG-43), so what "in flight" means for them
        is not settled. For these five it is — nothing pending will move
        them, and `suppressed` / `unreadable_incoming` still raise ATTENTION
        on their own, which is where a human-needed condition belongs.
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


def _event_id_of(path: Path) -> str | None:
    """The `event_id` inside `path`, or None if it cannot be read as one.

    Not a validation step — it never decides whether a file is a good Event.
    It exists to answer one question `_count_transport()` has to answer about
    two files that share a name: are they the same Event?
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("event_id")
    return value if isinstance(value, str) else None


def _count_transport(
    directory: Path, downstream: tuple[Path, ...] = ()
) -> tuple[int, int, int, int, int, int, SourceBreakdown]:
    """(promotable, unparseable, future_dated, incomplete, already_collected,
    suppressed, who-sent-the-promotable).

    Applies `transport.run_intake()`'s own parse test rather than a second
    opinion about what "valid" means, so this view cannot disagree with the
    step it is reporting on. `is_incomplete_write()` is imported from the
    same module for the same reason — intake skips those files, and a view
    that counted them as promotable would report a backlog the step it
    reports on has already decided it will never touch.

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

    `already_collected` is the same shape, reached by the most ordinary event
    in the system. `run_intake()` leaves a file whose name already exists in
    `incoming/`/`processed/`/`rejected/` exactly where it is — forever, since
    nothing deletes from `transport/` — and a re-sent Event is precisely that
    file. `agent/outbox.py`'s docstring calls a re-send "harmless at every
    downstream layer" and cites this very skip as the reason; that is true of
    the pipeline and was never checked against the view. Measured, one
    duplicate re-send after its original was collected:

        run 1..3   moved=0  skipped_already_present=1
                   awaiting_intake=1, is_clear=False, every run

    so a crash between "Transport accepted" and "moved to sent/" — the case
    the outbox is *designed* to recover from by re-sending — permanently
    parks ATTENTION on "수집되지 않고 남은 Event", with no explanation and no
    run able to clear it.

    It is therefore excluded from `awaiting_intake` for `unparseable`'s
    reason rather than counted for BUG-30's: intake's verdict here is
    deterministic and nothing downstream ever removes the file that produces
    it, so the Event is not queued — it is already handled. When the twin
    *is* still in `incoming/`, `awaiting_collection` already counts the work,
    so no in-flight signal is lost by this.

    `downstream` is intake's own list, in intake's own order, checked with
    intake's own existence test — this view must not hold a second opinion
    about what "already present" means.

    `suppressed` is the half of that verdict which is NOT benign, and
    separating it is the reason this can be excluded from `awaiting_intake`
    at all. intake's test is name-based (BUG-53), so "already present" covers
    two situations a single count would merge:

        the twin carries the same event_id      a re-sent duplicate. Nothing
                                                to do; it is already handled
        the twin is a directory, a 0-byte
        placeholder, or a different Event
        (BUG-53 / BUG-47 / a Windows
        case-insensitive filename collision)    a real, undelivered Event is
                                                being suppressed by a file
                                                that is not it — silent loss

    Merging them would have traded one false alert for one missing alert:
    every one of the second kind used to raise ATTENTION (as a permanently
    stuck `awaiting_intake`) for the wrong reason, and would have gone quiet
    for a wrong reason instead. So the twin is opened and the two `event_id`s
    compared. Equal means duplicate; anything else — unequal, unreadable, not
    a file — means suppressed, and stays in ATTENTION.

    Comparing ids rather than trusting the filename is also what catches the
    case-only collision: `safe_event_filename()` appends a digest whenever it
    has to change an id, so two distinct ids cannot share a name — except on
    Windows, where `EVT-a.json` and `EVT-A.json` are one path. Nothing else
    in the pipeline can see that.

    The cost is up to two extra file reads per entry in `transport/`, which
    holds only what is in flight — normally nothing, briefly a handful. Same
    trade, for the same reason, as the second read `_attribute()` makes.
    """
    promotable: list[Path] = []
    unparseable = 0
    future_dated = 0
    incomplete = 0
    already_collected = 0
    suppressed = 0
    now = time.time()
    for entry in _json_paths(directory):
        if _is_incomplete_write(entry.name):
            incomplete += 1
            continue
        twin = next(
            (target / entry.name for target in downstream if (target / entry.name).exists()),
            None,
        )
        if twin is not None:
            mine = _event_id_of(entry)
            if mine is not None and mine == _event_id_of(twin):
                already_collected += 1
            else:
                suppressed += 1
            continue
        try:
            if entry.stat().st_mtime > now:
                future_dated += 1
        except OSError:
            pass
        if _is_parseable_json(entry):
            promotable.append(entry)
        else:
            unparseable += 1
    return (
        len(promotable),
        unparseable,
        future_dated,
        incomplete,
        already_collected,
        suppressed,
        _attribute(promotable),
    )


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

    (
        promotable,
        unparseable,
        future_dated,
        incomplete,
        already_collected,
        suppressed,
        promotable_sources,
    ) = _count_transport(
        transport_dir,
        # Same three directories, in the same order, as
        # `transport.run_intake()`'s own `already_elsewhere` check.
        (incoming_dir, processed_dir, rejected_dir),
    )
    all_incoming_paths = _json_paths(incoming_dir)
    # A staging file in `incoming/` is not an Event awaiting collection —
    # see `IntakeBacklog.incoming_incomplete_write`. Same split, and for the
    # same reason, as `rejected_paths` below.
    incoming_paths = [
        p for p in all_incoming_paths if not _is_incomplete_write(p.name)
    ]
    incoming_incomplete = len(all_incoming_paths) - len(incoming_paths)
    all_rejected_paths = _json_paths(rejected_dir)
    # A staging file that was rejected is not a rejected Event — see
    # `IntakeBacklog.rejected_incomplete_write`. Split here rather than at
    # the two use sites below so the count and the attribution cannot
    # disagree about which files they describe.
    rejected_paths = [p for p in all_rejected_paths if not _is_incomplete_write(p.name)]
    rejected_incomplete = len(all_rejected_paths) - len(rejected_paths)

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
    # `all_rejected_paths`, not the filtered list: a name is taken in
    # `rejected/` whether or not the file that took it is a staging one, and
    # `run_once()` refuses the destination either way.
    taken_names = {path.name for path in processed_paths} | {
        path.name for path in all_rejected_paths
    }
    # `all_incoming_paths`, not the filtered list, for the mirror image of
    # the reason `all_rejected_paths` is used just above: `run_once()`
    # refuses a taken destination whatever the source file is, so a staging
    # file in `incoming/` is stuck on this forever exactly as an Event would
    # be. Narrowing the *count* of Events awaiting collection must not
    # narrow this check.
    name_collision = sum(1 for path in all_incoming_paths if path.name in taken_names)

    # Files the Collector cannot read at all, asked with the Collector's own
    # read. Split out of `awaiting_collection` rather than added to it: they
    # are not queued work, they are parked — `run_once()` fails on them
    # identically on every run and leaves them where they are. Without this,
    # one such file held `awaiting_collection` at 1 and `is_clear` at False
    # across three consecutive runs with nothing saying why.
    #
    # `incoming/` is drained every run and normally holds nothing, so the
    # extra read is paid only when there is something stuck — which is
    # exactly when an operator is looking.
    unreadable_incoming_paths = [
        path for path in incoming_paths if not is_readable_event_file(path)
    ]
    return CompanyActivitySnapshot(
        desktops=desktops,
        backlog=IntakeBacklog(
            awaiting_intake=promotable,
            unparseable=unparseable,
            future_dated=future_dated,
            incomplete=incomplete,
            already_collected=already_collected,
            suppressed=suppressed,
            awaiting_collection=len(incoming_paths) - len(unreadable_incoming_paths),
            unreadable_incoming=len(unreadable_incoming_paths),
            name_collision=name_collision,
            rejected=len(rejected_paths),
            incoming_incomplete_write=incoming_incomplete,
            rejected_incomplete_write=rejected_incomplete,
            awaiting_intake_sources=promotable_sources,
            # Attributed over the same set the count above uses, so
            # `SourceBreakdown.total` keeps its promise of equalling it.
            awaiting_collection_sources=_attribute(
                [p for p in incoming_paths if p not in set(unreadable_incoming_paths)]
            ),
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
