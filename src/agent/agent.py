"""Multi-Desktop Agent — one `run_once()` call, then it exits.

This is the sending side of the architecture README section 4 already
draws. Desktop 1/2/3 (and Desktop 4 for the COO's own work) each run the
*same* Agent; only the Desktop Profile and the paths differ.

    Signals (runtime/agent/signals/<date>/*.json)
        -> Reporter            (identity + Event Schema validation)
        -> outbox/             (durable, atomic, survives a crash)
        -> Transport           (OneDrive sync folder)
        -> sent/
              ... OneDrive ...
        -> Desktop 4: run_intake -> incoming/ -> Collector -> History
                                             -> Daily -> Backup -> Notion

Nothing downstream of Transport is re-implemented, re-decided, or
duplicated here. Every one of those stages already exists and is used
unchanged.

What this module does NOT do, on purpose
----------------------------------------
* It does not loop, sleep, poll, or register itself with Windows Task
  Scheduler. `collector/runtime.py`, `scheduler/scheduler.py`, and
  `run_company_ops.py` all take the same stance: how often to run is an
  operational decision made outside the code (docs/11 owns it).
* It does not infer that work happened. See `signals.py` for why.
* It does not emit a NO_ACTIVITY Event. `EVENT_TYPES` in
  `events/schema.py` has no such value and docs/02 §9's rule is
  "필요성이 실제로 확인되기 전까지 Role을 추가하지 않는다" — the same
  restraint applies to Event types, so adding one is a docs/02 change and
  therefore out of scope here. It is also unnecessary: a date with no
  Signals is recorded as collected with zero Events, and Desktop 4's
  Daily History already renders exactly that day as "No material company
  history recorded." (docs/06 §25's Empty Day). NO_ACTIVITY is reported
  in this module's own result object for the operator, and needs no wire
  representation to work.

The two invariants everything else here serves
----------------------------------------------
1. `last_successful_collection_date` never moves past a date whose Events
   were not all delivered. 8/8 succeeds and 8/9 fails => the state still
   reads 8/8, and the next run starts again at 8/9.
2. Re-running is always safe. `event_id` is derived deterministically
   from (Desktop, date, Signal filename), so the same Signal always
   becomes the same Event — a retry after a crash is recognised as a
   duplicate by Transport Intake and Collector instead of producing a
   second History entry.
"""

from __future__ import annotations

import enum
import os
import uuid
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, time
from pathlib import Path

from events import EventValidationError
from reporter import Reporter
from reporter.profiles import DesktopProfile, resolve_profile
from scheduler.lock import release_lock, try_acquire_lock
from transport import Transport

from .catchup import pending_dates
from .outbox import (
    DEFAULT_OUTBOX_DIR,
    DEFAULT_SENT_DIR,
    DrainSummary,
    drain,
    is_sent,
    stage,
)
from .signals import DEFAULT_SIGNALS_DIR, Signal, load_signals, redact
from .state import (
    DEFAULT_STATE_PATH,
    AgentState,
    ensure_desktop,
    load_state,
    save_state,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REJECTED_SIGNALS_DIR = PROJECT_ROOT / "runtime" / "agent" / "signals_rejected"
DEFAULT_LOG_PATH = PROJECT_ROOT / "runtime" / "agent" / "logs" / "agent.log"
DEFAULT_LOCK_PATH = PROJECT_ROOT / "runtime" / "agent" / "locks" / "agent.lock"

# A fixed, arbitrary namespace UUID. Its only requirement is that it never
# changes: every Agent on every Desktop must derive the same event_id for
# the same (Desktop, date, Signal), across machines and across reinstalls,
# or the duplicate suppression this module relies on stops working.
EVENT_ID_NAMESPACE = uuid.UUID("8f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f")


class AgentStatus(enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED_ALREADY_RUNNING = "SKIPPED_ALREADY_RUNNING"


class DateOutcome(enum.Enum):
    COLLECTED = "COLLECTED"
    NO_ACTIVITY = "NO_ACTIVITY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DateResult:
    date: date_type
    outcome: DateOutcome
    event_ids: tuple[str, ...] = ()
    already_sent: tuple[str, ...] = ()
    rejected_signals: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentRunResult:
    status: AgentStatus
    desktop_id: str
    role: str
    dates: tuple[DateResult, ...] = field(default_factory=tuple)
    drain_summaries: tuple[DrainSummary, ...] = field(default_factory=tuple)
    last_successful_collection_date: date_type | None = None
    failed_date: date_type | None = None
    error: str | None = None


def derive_event_id(*, source: str, target_date: date_type, signal_id: str) -> str:
    """The same Signal always becomes the same Event.

    `uuid4()` (what `events.generate_event_id()` does for an ad-hoc Event)
    would give a re-run a brand new id, so a crash between "sent" and
    "recorded as sent" would deliver the same work twice as two unrelated
    Events — two History entries, two Notion writes, one real milestone.
    A uuid5 over (Desktop, date, Signal filename) removes that failure mode
    entirely: the retry collides with itself and every dedup layer
    downstream recognises it.

    `source` rather than the role is the Desktop component, because two
    Desktops can legitimately carry the same role while two Signals on one
    Desktop cannot share a filename within one date.
    """
    return str(
        uuid.uuid5(EVENT_ID_NAMESPACE, f"{source}|{target_date.isoformat()}|{signal_id}")
    )


def _default_timestamp(target_date: date_type) -> str:
    """Local midnight on the Signal's own date, with the local UTC offset.

    A Signal may state its own `timestamp` (validated in `signals.py` to
    fall on that date). When it does not, the Event still needs one, and it
    must land in `target_date`'s bucket because Daily History groups by the
    Event's timestamp, not by when it was collected (docs/06 §12). Midnight
    is the one value on that date that is the same for every Signal and for
    every re-run — using "now" instead would make a catch-up of 08-08 file
    its Events under the day the PC happened to be switched back on.
    """
    return (
        datetime.combine(target_date, time(0, 0)).astimezone().isoformat(timespec="seconds")
    )


def _log(log_path: Path, message: str) -> None:
    """Append one timestamped line. Identical idiom to
    `collector/runtime.py::_log()`, including "logging must never be the
    thing that fails a run".

    Only identifiers, counts, and outcomes are ever written — never a
    Signal's text. `signals.py` already refuses secret-shaped content, and
    this keeps the log from becoming a second copy of business detail that
    nothing needs. The one operator-chosen value that does reach a log line
    is a Signal's filename, and every caller passes it through
    `signals.redact()` first.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def _reject_signal(path: Path, rejected_dir: Path, target_date: date_type) -> str:
    """Move an unusable Signal out of the way, preserving it for a human.

    Mirrors `collector/runtime.py`'s rejected/ routing: a Signal that
    cannot become an Event must not stall the date forever, and must not
    be deleted either. If the move fails the Signal simply stays where it
    is — it will be re-judged (and re-rejected) next run, which is noisy
    but never lossy.
    """
    destination_dir = Path(rejected_dir) / target_date.isoformat()
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / path.name
        if destination.exists():
            # Already rejected once under the same name; keep the original
            # copy rather than overwrite it.
            return path.name
        os.replace(path, destination)
    except OSError:
        pass
    return path.name


def _build_event(reporter: Reporter, signal: Signal, *, source: str):
    payload = dict(signal.payload)
    timestamp = payload.pop("timestamp", None) or _default_timestamp(signal.date)
    return reporter.report(
        project_id=payload["project_id"],
        event_type=payload["event_type"],
        status=payload["status"],
        summary=payload["summary"],
        milestone=payload.get("milestone"),
        blocker=payload.get("blocker"),
        evidence=payload.get("evidence"),
        history_candidate=bool(payload.get("history_candidate", False)),
        event_id=derive_event_id(
            source=source, target_date=signal.date, signal_id=signal.signal_id
        ),
        timestamp=timestamp,
    )


def run_once(
    *,
    transport: Transport,
    agent_start_date: date_type,
    profile: str | DesktopProfile | None = None,
    now: datetime | None = None,
    signals_dir: Path | None = None,
    rejected_signals_dir: Path | None = None,
    outbox_dir: Path | None = None,
    sent_dir: Path | None = None,
    state_path: Path | None = None,
    lock_path: Path | None = None,
    log_path: Path | None = None,
) -> AgentRunResult:
    """Collect and deliver every not-yet-collected date, oldest first.

    Returns rather than raises for every operational failure (network
    down, Desktop 4 unreachable, an unusable Signal) — the caller gets an
    `AgentRunResult` describing exactly how far it got. A corrupted state
    file is the one exception: `AgentStateError` propagates, because
    guessing what a damaged `last_successful_collection_date` meant is
    precisely how dates get skipped.
    """
    resolved_profile = resolve_profile(profile)
    now = now or datetime.now().astimezone()
    signals_dir = Path(signals_dir) if signals_dir is not None else DEFAULT_SIGNALS_DIR
    rejected_signals_dir = (
        Path(rejected_signals_dir)
        if rejected_signals_dir is not None
        else DEFAULT_REJECTED_SIGNALS_DIR
    )
    outbox_dir = Path(outbox_dir) if outbox_dir is not None else DEFAULT_OUTBOX_DIR
    sent_dir = Path(sent_dir) if sent_dir is not None else DEFAULT_SENT_DIR
    state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    lock_path = Path(lock_path) if lock_path is not None else DEFAULT_LOCK_PATH
    log_path = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH

    source = resolved_profile.source

    # A logon-triggered run and a manual run can start at the same moment.
    # Reuses `scheduler.lock` unchanged — the same O_CREAT|O_EXCL
    # acquisition and the same §27 stale-process rule, on the Agent's own
    # lock file (never Desktop 4's Runner lock: they protect different
    # critical sections and may legitimately run at once, even on Desktop 4).
    if not try_acquire_lock(lock_path, now=now):
        _log(log_path, f"AGENT {source} SKIPPED another agent run holds the lock")
        return AgentRunResult(
            status=AgentStatus.SKIPPED_ALREADY_RUNNING,
            desktop_id=source,
            role=resolved_profile.role,
        )

    try:
        state = load_state(state_path)
        ensure_desktop(state, source, state_path=state_path)
        state.desktop_id = source

        reporter = Reporter(profile=resolved_profile, transport=transport)
        _log(
            log_path,
            f"AGENT {source} START role={resolved_profile.role} "
            f"last_successful={state.last_successful_collection_date}",
        )

        drain_summaries: list[DrainSummary] = []

        # Anything left in the outbox is a previous run's undelivered work.
        # It is retried before any new date is considered, and if it still
        # cannot be delivered the run stops here — advancing past a date
        # whose Events are still sitting locally is the one thing that
        # would break invariant 1.
        leftover = drain(transport, outbox_dir=outbox_dir, sent_dir=sent_dir)
        if leftover.sent or not leftover.is_clear:
            drain_summaries.append(leftover)
        if not leftover.is_clear:
            _log(
                log_path,
                f"AGENT {source} FAILED outbox not clear "
                f"failed={len(leftover.failed)} unreadable={len(leftover.unreadable)}",
            )
            return _finish(
                state,
                state_path,
                now,
                AgentRunResult(
                    status=AgentStatus.FAILED,
                    desktop_id=source,
                    role=resolved_profile.role,
                    drain_summaries=tuple(drain_summaries),
                    last_successful_collection_date=state.last_successful_collection_date,
                    error=_describe_drain_failure(leftover),
                ),
            )

        dates = pending_dates(
            last_successful_collection_date=state.last_successful_collection_date,
            start_date=agent_start_date,
            now=now,
        )

        results: list[DateResult] = []
        for target_date in dates:
            date_result, summary = _collect_one_date(
                target_date,
                reporter=reporter,
                transport=transport,
                source=source,
                signals_dir=signals_dir,
                rejected_signals_dir=rejected_signals_dir,
                outbox_dir=outbox_dir,
                sent_dir=sent_dir,
                log_path=log_path,
            )
            results.append(date_result)
            if summary is not None:
                drain_summaries.append(summary)

            if date_result.outcome is DateOutcome.FAILED:
                # Dates close in order; stop rather than skip ahead and
                # leave a gap (the rule `scheduler.py` follows for Daily
                # History, applied to collection).
                _log(log_path, f"AGENT {source} FAILED date={target_date.isoformat()}")
                return _finish(
                    state,
                    state_path,
                    now,
                    AgentRunResult(
                        status=AgentStatus.FAILED,
                        desktop_id=source,
                        role=resolved_profile.role,
                        dates=tuple(results),
                        drain_summaries=tuple(drain_summaries),
                        last_successful_collection_date=(
                            state.last_successful_collection_date
                        ),
                        failed_date=target_date,
                        error="; ".join(date_result.errors) or "delivery failed",
                    ),
                )

            # Saved per date, not once at the end: a crash between two
            # dates must not re-collect the date that already succeeded
            # (docs/07 §29's rule for Daily History, same reason here).
            state.last_successful_collection_date = target_date
            save_state(state_path, state)
            _log(
                log_path,
                f"AGENT {source} {date_result.outcome.value} "
                f"date={target_date.isoformat()} events={len(date_result.event_ids)} "
                f"already_sent={len(date_result.already_sent)} "
                f"rejected={len(date_result.rejected_signals)}",
            )

        _log(
            log_path,
            f"AGENT {source} COMPLETED dates={len(results)} "
            f"last_successful={state.last_successful_collection_date}",
        )
        return _finish(
            state,
            state_path,
            now,
            AgentRunResult(
                status=AgentStatus.COMPLETED,
                desktop_id=source,
                role=resolved_profile.role,
                dates=tuple(results),
                drain_summaries=tuple(drain_summaries),
                last_successful_collection_date=state.last_successful_collection_date,
            ),
        )
    finally:
        release_lock(lock_path)


def _describe_drain_failure(summary: DrainSummary) -> str:
    parts = [f"{event_id}: {reason}" for event_id, reason in summary.failed]
    parts.extend(f"{name}: {reason}" for name, reason in summary.unreadable)
    return "; ".join(parts)


def _finish(
    state: AgentState, state_path: Path, now: datetime, result: AgentRunResult
) -> AgentRunResult:
    """Record that a run happened, then return `result` unchanged.

    Only `last_run` is written here. `last_successful_collection_date` is
    never touched on this path, so a failing run cannot advance it by
    passing through the bookkeeping step.
    """
    state.last_run = now.isoformat(timespec="seconds")
    try:
        save_state(state_path, state)
    except OSError:
        # Losing `last_run` costs a human one line of visibility. Losing
        # the run's actual result — which is already durable in outbox/,
        # sent/, and the per-date saves above — would cost data.
        pass
    return result


def _collect_one_date(
    target_date: date_type,
    *,
    reporter: Reporter,
    transport: Transport,
    source: str,
    signals_dir: Path,
    rejected_signals_dir: Path,
    outbox_dir: Path,
    sent_dir: Path,
    log_path: Path,
) -> tuple[DateResult, DrainSummary | None]:
    """Turn one date's Signals into delivered Events.

    Returns the date's result and the DrainSummary produced while
    delivering it (None when the date had nothing to deliver).
    """
    signals, invalid = load_signals(signals_dir, target_date)

    rejected: list[str] = []
    errors: list[str] = []
    for path, error in invalid:
        rejected.append(_reject_signal(path, rejected_signals_dir, target_date))
        errors.append(redact(str(error)))
        _log(log_path, f"AGENT {source} REJECTED_SIGNAL {redact(path.name)}")

    event_ids: list[str] = []
    already: list[str] = []
    for signal in signals:
        event_id = derive_event_id(
            source=source, target_date=target_date, signal_id=signal.signal_id
        )
        if is_sent(event_id, sent_dir):
            # Delivered by an earlier run. Re-sending would be harmless
            # (every downstream layer dedups by event_id) but pointless,
            # and skipping it is what makes a catch-up over many already
            # partly-collected dates cheap.
            already.append(event_id)
            continue

        try:
            event = _build_event(reporter, signal, source=source)
        except EventValidationError as exc:
            # The Signal parsed but does not describe a valid Event (e.g.
            # BLOCKED with no blocker — a docs/02 rule `signals.py`
            # deliberately does not duplicate). Same treatment as any
            # other unusable Signal.
            source_path = signal.path or (
                Path(signals_dir) / target_date.isoformat() / f"{signal.signal_id}.json"
            )
            rejected.append(_reject_signal(source_path, rejected_signals_dir, target_date))
            errors.append(redact(f"{signal.signal_id}: {exc}"))
            _log(
                log_path,
                f"AGENT {source} REJECTED_SIGNAL {redact(source_path.name)}",
            )
            continue

        try:
            stage(event, outbox_dir)
        except OSError as exc:
            # The outbox write is the durability boundary. If it fails the
            # Event does not exist yet, so the date has NOT been collected
            # and must not be marked as such.
            errors.append(redact(f"{signal.signal_id}: could not stage event ({exc})"))
            return (
                DateResult(
                    date=target_date,
                    outcome=DateOutcome.FAILED,
                    event_ids=tuple(event_ids),
                    already_sent=tuple(already),
                    rejected_signals=tuple(rejected),
                    errors=tuple(errors),
                ),
                None,
            )
        event_ids.append(event.event_id)

    if not event_ids:
        # Either no Signals at all, or every Signal was already delivered
        # or rejected. Nothing is pending for this date, so it is closed.
        outcome = (
            DateOutcome.NO_ACTIVITY if not already and not rejected else DateOutcome.COLLECTED
        )
        return (
            DateResult(
                date=target_date,
                outcome=outcome,
                already_sent=tuple(already),
                rejected_signals=tuple(rejected),
                errors=tuple(errors),
            ),
            None,
        )

    summary = drain(transport, outbox_dir=outbox_dir, sent_dir=sent_dir)
    if not summary.is_clear:
        errors.append(_describe_drain_failure(summary))
        return (
            DateResult(
                date=target_date,
                outcome=DateOutcome.FAILED,
                event_ids=tuple(event_ids),
                already_sent=tuple(already),
                rejected_signals=tuple(rejected),
                errors=tuple(errors),
            ),
            summary,
        )

    return (
        DateResult(
            date=target_date,
            outcome=DateOutcome.COLLECTED,
            event_ids=tuple(event_ids),
            already_sent=tuple(already),
            rejected_signals=tuple(rejected),
            errors=tuple(errors),
        ),
        summary,
    )
