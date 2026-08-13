"""History Candidate reconciliation — detection only (BACKLOG A-20).

An Event that crosses the Collector but whose History Candidate is never
written is lost from Company History permanently. The pipeline consumes
before it records:

    Collector    marks the event_id seen, moves the file to processed/
    ...
    step 5       writes the History Candidate

Anything that ends the run between those two points leaves the Event in
`processed/` with no Candidate. The `event_id` is already in the seen
store, so no later run reconsiders it — measured: `accepted=0` forever, and
the Event never appears in Daily History. BACKLOG A-20 has the full
reproduction; `tests/test_runner_failure_paths.py::ConsumedEventWithoutCandidateTests`
pins it.

What this module adds is the answer to the one question A-20 records as
unanswered: **which Event went missing.** The Run Manifest already reports
that a run failed and which component aborted, but it names no Event, and
re-running recovers nothing.

Detection only, deliberately — the same restraint, for the same reason, as
`scheduler/consistency.py`. This module reports; it never re-processes,
re-collects, rewrites the seen store, moves a file, or repairs anything.
Closing the window means either persisting the Candidate before/atomically
with `mark_seen()` (a Collector contract change) or adding a recovery pass
(a new mechanism). Both are decisions, and wiring either into the Runner's
control flow from here would be making that decision by implementation.

A `DROP` Event correctly has no Candidate — `FileHistoryRepository.save()`
stores only `KEEP` and `REVIEW` — so only those two are ever reported.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from events import Event

from .file_repository import safe_candidate_filename
from .filter import HistoryFilter
from .result import HistoryDecision


# Same pool sizing, and for the same measured reason, as
# `app/desktop_activity.py`. This scan reads every file in `processed/`, and
# on this machine that costs ~5.3 ms per file — almost entirely file OPEN,
# not JSON parsing. The pool is worth 4.2x on a cold cache at 20,000 files
# (8.9 s -> 2.1 s); the larger factor this comment used to quote came from
# a benchmark whose serial pass ran cold and whose threaded pass ran on the
# cache that pass had just warmed — see `app/desktop_activity.py`'s
# `_READ_WORKERS` and BACKLOG section D. `ops_status.py` runs this on every invocation and is
# documented as the "check this first" view, so a two-minute status command
# would simply not be used.
#
# Threading is safe precisely where this sits: a pure read path whose output
# is aggregated, not ordered by completion. The same treatment was
# deliberately NOT applied to `outbox.drain()` / `run_intake()`, which are
# write paths where ordering and per-file failure isolation are contract.
_READ_WORKERS = max(4, min(16, (os.cpu_count() or 4) * 2))


@dataclass(frozen=True)
class OrphanedEvent:
    """An Event that was consumed but never became a Candidate."""

    event_id: str
    event_path: Path
    decision: HistoryDecision
    expected_candidate_path: Path


@dataclass(frozen=True)
class UnreadableEvent:
    """A file in `processed/` that can no longer be read as an Event.

    Reported separately rather than counted as an orphan: "we cannot tell
    whether this one is missing" is a different statement from "this one is
    missing", and collapsing them would inflate a number an operator is
    meant to act on.
    """

    event_path: Path
    reason: str


@dataclass(frozen=True)
class ReconciliationResult:
    orphaned: tuple[OrphanedEvent, ...] = ()
    unreadable: tuple[UnreadableEvent, ...] = ()
    checked: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.orphaned and not self.unreadable


def find_orphaned_events(
    *,
    processed_dir: Path,
    keep_dir: Path,
    review_dir: Path,
) -> ReconciliationResult:
    """Every consumed Event whose Candidate is missing.

    The decision is recomputed with `HistoryFilter` rather than remembered,
    because nothing records it: the Candidate *is* the record, and it is
    precisely what is absent. `HistoryFilter.evaluate()` is pure and derives
    its decision from the Event alone, so recomputing gives the same answer
    the lost run would have produced.

    The expected filename comes from `safe_candidate_filename()` — the same
    function the repository writes with — so a sanitised `event_id` cannot
    make a present Candidate look missing.
    """
    processed_dir = Path(processed_dir)
    if not processed_dir.is_dir():
        return ReconciliationResult()

    history_filter = HistoryFilter()
    orphaned: list[OrphanedEvent] = []
    unreadable: list[UnreadableEvent] = []

    paths = sorted(processed_dir.glob("*.json"))
    checked = len(paths)

    def _read(path: Path):
        """(event, error). Reads only — the verdict is decided serially
        below so the logic stays in one place and stays deterministic."""
        try:
            return Event.from_json(path.read_text(encoding="utf-8")), None
        except Exception as exc:  # noqa: BLE001 — any unreadable shape counts
            return None, str(exc)

    if paths:
        with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
            # `map` preserves input order, so the sorted-filename ordering
            # of the results is identical to the serial version's.
            reads = list(pool.map(_read, paths))
    else:
        reads = []

    for event_path, (event, error) in zip(paths, reads):
        if event is None:
            unreadable.append(UnreadableEvent(event_path=event_path, reason=error))
            continue

        decision = history_filter.evaluate(event).decision
        if decision is HistoryDecision.KEEP:
            target_dir = Path(keep_dir)
        elif decision is HistoryDecision.REVIEW:
            target_dir = Path(review_dir)
        else:
            # DROP never produces a Candidate — its absence is correct.
            continue

        expected = target_dir / safe_candidate_filename(f"HIST-{event.event_id}")
        if not expected.exists():
            orphaned.append(
                OrphanedEvent(
                    event_id=event.event_id,
                    event_path=event_path,
                    decision=decision,
                    expected_candidate_path=expected,
                )
            )

    return ReconciliationResult(
        orphaned=tuple(orphaned),
        unreadable=tuple(unreadable),
        checked=checked,
    )
