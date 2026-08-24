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

    Cost, measured in an isolated runtime rather than reasoned about (C38).
    `ops_status.py` calls this in the same command that calls
    `app.desktop_activity.read_company_activity()`, and both read every file
    in `processed/` — which looks like paying twice and is not:

        read every processed Event, cold      5.09 s   (6,000 files)
        the same read again, warm             0.43 s
        this function, own read (warm)        0.40 s
        this function, handed the parsed data 0.02 s

    The first read pays the cold open; the OS page cache absorbs the second.
    Sharing one read between the two consumers was implemented, measured on
    alternating orders, and reverted: 3% of the command, for a parameter
    crossing the `app` -> `history` boundary into a data-loss detector whose
    blind spots have already had to be closed twice. What actually costs is
    the first, unavoidable pass over a directory that never shrinks — which
    is BACKLOG B-6's retention decision, not an optimisation.
    """
    processed_dir = Path(processed_dir)
    if not processed_dir.is_dir():
        return ReconciliationResult()

    history_filter = HistoryFilter()
    orphaned: list[OrphanedEvent] = []
    unreadable: list[UnreadableEvent] = []
    #: casefolded Candidate path -> the Events that expect to own it. More
    #: than one distinct `event_id` in a group is C89's collision.
    claimed: dict[str, list] = {}

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
        # `is_file()`, not `exists()`. The question is "was a Candidate
        # written for this Event", and a directory carrying the Candidate's
        # name answers "is this name taken" instead. Measured: one genuinely
        # orphaned Event, reported correctly with nothing there, and reported
        # by nothing once a directory of that name existed — A-20's detector
        # silenced by the presence of something that is not a Candidate.
        if not expected.is_file():
            orphaned.append(
                OrphanedEvent(
                    event_id=event.event_id,
                    event_path=event_path,
                    decision=decision,
                    expected_candidate_path=expected,
                )
            )
        else:
            claimed.setdefault(str(expected).casefold(), []).append(
                (event, event_path, decision, expected)
            )

    # Two Events whose Candidate paths differ only in case (C89).
    #
    # `is_file()` above answers "is there a file at this path", and on a
    # case-insensitive filesystem — which is the deployment target
    # (docs/11) — `HIST-twin.json` and `HIST-TWIN.json` are one path. So a
    # second Event whose id differs from the first only in case finds the
    # *other* Event's Candidate sitting there and is reported as fine.
    #
    # Measured through the real entrypoint, `twin` and `TWIN` in one batch:
    #
    #     run 1   exit 2, history_filter STEP_ABORTED (FileExistsError)
    #             keep/ = HIST-twin.json only
    #     run 2   exit 0, SUCCESS, Company History rendered and pushed
    #     TWIN    no Candidate, ever, and this function reported clean
    #
    # At most one of a colliding group can own that file, so every other
    # member is orphaned no matter what is on disk. Reported by identity
    # rather than by asking the filesystem again, because the filesystem is
    # what cannot tell them apart.
    #
    # No extra reads: the group is built from Events this pass already
    # parsed, keyed by the path it already computed.
    for members in claimed.values():
        if len(members) < 2:
            continue
        if len({event.event_id for event, _p, _d, _e in members}) < 2:
            # The same id twice is a duplicate *file*, not a collision of two
            # Events, and `rollup.DuplicateEvent` is where that is reported.
            continue
        # The first is the one the file can belong to; the rest cannot.
        for event, event_path, decision, expected in members[1:]:
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
