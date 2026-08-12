"""Read-only status snapshot of this machine's Agent.

Answers the questions an operator actually asks when something feels wrong,
using only files the Agent already writes:

    언제 마지막으로 실행됐나          state.last_run
    어디까지 수집했나                 state.last_successful_collection_date
    아직 안 한 날짜가 있나            catchup.pending_dates()
    보내지 못한 Event가 쌓여 있나     outbox/
    사람이 봐야 할 Signal이 있나      signals_rejected/
    한참 안 돌았나                    now - last_run

Nothing here writes, moves, deletes, locks, or sends. It can be called
while the Agent is running, from another process, without any risk to a
run in progress — which is the whole point: a diagnostic that can itself
break the thing it is diagnosing is not a diagnostic.

Deliberately no new stored state. Every field is derived from what the
Agent already persists, so this module cannot drift out of sync with
reality and adds nothing to recover after a crash. A `pending_signals`
count is NOT reported: knowing it would mean parsing every Signal file,
which is the Agent's job and would make a read-only status call able to
report Signals it has not validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from .agent import DEFAULT_REJECTED_SIGNALS_DIR
from .catchup import pending_dates
from .outbox import DEFAULT_OUTBOX_DIR, DEFAULT_SENT_DIR, pending
from .state import DEFAULT_STATE_PATH, AgentStateError, load_state


@dataclass(frozen=True)
class AgentStatusSnapshot:
    desktop_id: str | None
    last_run: str | None
    last_successful_collection_date: date_type | None
    pending_dates: tuple[date_type, ...]
    outbox_count: int
    sent_count: int
    rejected_signal_count: int
    state_error: str | None = None

    @property
    def has_undelivered_events(self) -> bool:
        """Events created but not yet accepted by Transport.

        Not the same as "something is broken" — a run that has just staged
        its Events is momentarily in this state. It is only a problem in
        combination with a stale `last_run`, which is what
        `needs_attention()` weighs.
        """
        return self.outbox_count > 0

    @property
    def has_uncollected_dates(self) -> bool:
        return bool(self.pending_dates)

    def days_since_last_run(self, now: datetime) -> int | None:
        """Whole days since the Agent last completed a run, or None if never.

        Returns None rather than 0 for a never-run Agent, because "brand new"
        and "ran a moment ago" call for opposite reactions.
        """
        if self.last_run is None:
            return None
        try:
            parsed = datetime.fromisoformat(self.last_run)
        except ValueError:
            return None
        if parsed.tzinfo is None or now.tzinfo is None:
            parsed = parsed.replace(tzinfo=None)
            reference = now.replace(tzinfo=None)
        else:
            reference = now
        return max(0, (reference.date() - parsed.date()).days)

    def needs_attention(self, now: datetime, *, stale_after_days: int = 2) -> tuple[str, ...]:
        """Plain-language reasons a human should look at this Desktop.

        Empty means "nothing here needs a person". Ordered most-serious
        first. `stale_after_days` defaults to 2 rather than 1 because a
        machine that is simply off for a weekend is normal in this
        deployment (docs/07 §58) and a status view that cries wolf every
        Monday gets ignored.
        """
        reasons: list[str] = []
        if self.state_error is not None:
            reasons.append(f"agent state unreadable: {self.state_error}")

        # A collection date in the future is a silent, permanent stop, and
        # every other signal here reports it as perfect health.
        #
        # `agent.run_once()` never writes one — it caps at `now` — but clock
        # skew on a machine that has since been corrected, or a state file
        # restored from a newer backup, can put one there. `pending_dates()`
        # then computes `start > end` and correctly returns nothing, so:
        # last_run is recent, outbox is empty, pending is zero. The Desktop
        # looks healthier than a working one, and it will not collect again
        # until the calendar reaches that date — possibly years.
        #
        # Detection only. `pending_dates()`'s refusal to walk backwards is
        # the safe behaviour and is left exactly as it is; nothing here
        # rewrites the state or reprocesses a date. Same restraint as
        # `scheduler/consistency.py`: report, never repair.
        collected_through = self.last_successful_collection_date
        if collected_through is not None and collected_through > now.date():
            reasons.append(
                f"agent state says it has collected through {collected_through.isoformat()}, "
                f"which is in the future (today is {now.date().isoformat()}) — nothing will "
                f"be collected until that date arrives"
            )

        if self.outbox_count:
            reasons.append(
                f"{self.outbox_count} event(s) created but not delivered"
            )
        if self.rejected_signal_count:
            reasons.append(
                f"{self.rejected_signal_count} signal(s) rejected and awaiting a human"
            )
        elapsed = self.days_since_last_run(now)
        if elapsed is None:
            reasons.append("agent has never completed a run")
        elif elapsed >= stale_after_days:
            reasons.append(f"agent has not run for {elapsed} day(s)")
        if self.pending_dates:
            reasons.append(f"{len(self.pending_dates)} date(s) not yet collected")
        return tuple(reasons)


def _count_json(directory: Path) -> int:
    path = Path(directory)
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.glob("*.json"))


def _count_rejected_signals(rejected_dir: Path) -> int:
    path = Path(rejected_dir)
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.rglob("*.json"))


def read_status(
    *,
    agent_start_date: date_type | None = None,
    now: datetime | None = None,
    state_path: Path | None = None,
    outbox_dir: Path | None = None,
    sent_dir: Path | None = None,
    rejected_signals_dir: Path | None = None,
) -> AgentStatusSnapshot:
    """Build the snapshot. Never raises for a damaged state file.

    A corrupted `agent_state.json` is exactly when someone needs this view
    most, so `AgentStateError` is captured into `state_error` rather than
    propagated — unlike `agent.run_once()`, which must refuse to proceed on
    a state it cannot trust. Reading is safe where acting is not.

    `pending_dates` is only computed when `agent_start_date` is supplied,
    since a first-ever run has no other way to know where counting starts
    (docs/07 §50: never guessed).
    """
    now = now or datetime.now().astimezone()
    state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    outbox_dir = Path(outbox_dir) if outbox_dir is not None else DEFAULT_OUTBOX_DIR
    sent_dir = Path(sent_dir) if sent_dir is not None else DEFAULT_SENT_DIR
    rejected_signals_dir = (
        Path(rejected_signals_dir)
        if rejected_signals_dir is not None
        else DEFAULT_REJECTED_SIGNALS_DIR
    )

    state_error: str | None = None
    try:
        state = load_state(state_path)
    except AgentStateError as exc:
        state_error = str(exc)
        state = None

    upcoming: tuple[date_type, ...] = ()
    if state is not None and agent_start_date is not None:
        upcoming = tuple(
            pending_dates(
                last_successful_collection_date=state.last_successful_collection_date,
                start_date=agent_start_date,
                now=now,
            )
        )

    return AgentStatusSnapshot(
        desktop_id=state.desktop_id if state is not None else None,
        last_run=state.last_run if state is not None else None,
        last_successful_collection_date=(
            state.last_successful_collection_date if state is not None else None
        ),
        pending_dates=upcoming,
        outbox_count=len(pending(outbox_dir)),
        sent_count=_count_json(sent_dir),
        rejected_signal_count=_count_rejected_signals(rejected_signals_dir),
        state_error=state_error,
    )
