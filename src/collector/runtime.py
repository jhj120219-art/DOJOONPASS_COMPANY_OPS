"""Collector Runtime Core (docs/03_COLLECTOR_SPEC.md, narrowed per Phase 4.1).

One thing, done once per call: scan `runtime/events/incoming/`, run each
file through the existing Collector Interface (src/collector/collector.py,
unchanged), and move it to `processed/` or `rejected/` based on the result.

This is a single pass, not a watcher. Nothing here loops, polls, threads,
or stays resident — the caller decides when to call `run_once()` again
(that decision belongs to a future Scheduler, not this module).

Explicitly not this phase's job: persisting duplicate-check state across
runs (`SeenEventStore` is still whatever the caller injects into the
`Collector` — a fresh in-memory one is fine here), History/Notion routing,
retry scheduling beyond "a FAILED file is simply left in incoming/ for the
next run to pick up naturally".
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .collector import Collector
from .result import CollectorStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INCOMING_DIR = PROJECT_ROOT / "runtime" / "events" / "incoming"
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "runtime" / "events" / "processed"
DEFAULT_REJECTED_DIR = PROJECT_ROOT / "runtime" / "events" / "rejected"
DEFAULT_LOG_PATH = PROJECT_ROOT / "runtime" / "logs" / "collector.log"


class RuntimeOutcome(enum.Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProcessedFile:
    source_path: Path
    destination_path: Path | None
    outcome: RuntimeOutcome
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeSummary:
    accepted: int
    duplicate: int
    rejected: int
    failed: int
    files: tuple[ProcessedFile, ...]


def _log(log_path: Path, message: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def run_once(
    *,
    collector: Collector,
    incoming_dir: Path | None = None,
    processed_dir: Path | None = None,
    rejected_dir: Path | None = None,
    log_path: Path | None = None,
) -> RuntimeSummary:
    """Scan `incoming_dir` once and route each file to processed/rejected.

    A single file's failure (unreadable, internal Collector error, or a
    destination name collision) is recorded and skipped, leaving the
    original untouched in `incoming_dir` for a future run to retry — it
    never stops the rest of the batch.
    """
    incoming_dir = Path(incoming_dir) if incoming_dir is not None else DEFAULT_INCOMING_DIR
    processed_dir = Path(processed_dir) if processed_dir is not None else DEFAULT_PROCESSED_DIR
    rejected_dir = Path(rejected_dir) if rejected_dir is not None else DEFAULT_REJECTED_DIR
    log_path = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH

    incoming_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    _log(log_path, "COLLECTOR START")

    results: list[ProcessedFile] = []

    for path in sorted(incoming_dir.glob("*.json")):
        _log(log_path, f"PROCESSING {path.name}")

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            _log(log_path, f"FAILED {path.name}: could not read file ({exc})")
            results.append(ProcessedFile(path, None, RuntimeOutcome.FAILED, (str(exc),)))
            continue

        try:
            result = collector.collect(raw)
        except Exception as exc:  # noqa: BLE001
            # One malformed/unlucky Event must never stop the rest of the
            # batch (docs/03_COLLECTOR_SPEC.md section 53). The file stays
            # in incoming/ so the next run retries it.
            _log(log_path, f"FAILED {path.name}: {exc}")
            results.append(ProcessedFile(path, None, RuntimeOutcome.FAILED, (str(exc),)))
            continue

        if result.status is CollectorStatus.REJECTED:
            outcome = RuntimeOutcome.REJECTED
            target_dir = rejected_dir
            _log(log_path, f"REJECTED {path.name}: {'; '.join(result.errors)}")
        elif result.status is CollectorStatus.DUPLICATE:
            outcome = RuntimeOutcome.DUPLICATE
            target_dir = processed_dir
            _log(log_path, f"DUPLICATE {result.event.event_id}")
        else:
            outcome = RuntimeOutcome.ACCEPTED
            target_dir = processed_dir
            _log(log_path, f"ACCEPTED {result.event.event_id}")

        destination = target_dir / path.name
        if destination.exists():
            _log(log_path, f"FAILED {path.name}: destination already exists ({destination})")
            results.append(
                ProcessedFile(path, None, RuntimeOutcome.FAILED, ("destination already exists",))
            )
            continue

        try:
            os.replace(path, destination)
        except OSError as exc:
            _log(log_path, f"FAILED {path.name}: could not move file ({exc})")
            results.append(ProcessedFile(path, None, RuntimeOutcome.FAILED, (str(exc),)))
            continue

        results.append(ProcessedFile(path, destination, outcome, result.errors))

    summary = RuntimeSummary(
        accepted=sum(1 for r in results if r.outcome is RuntimeOutcome.ACCEPTED),
        duplicate=sum(1 for r in results if r.outcome is RuntimeOutcome.DUPLICATE),
        rejected=sum(1 for r in results if r.outcome is RuntimeOutcome.REJECTED),
        failed=sum(1 for r in results if r.outcome is RuntimeOutcome.FAILED),
        files=tuple(results),
    )
    _log(
        log_path,
        "COLLECTOR FINISHED "
        f"accepted={summary.accepted} duplicate={summary.duplicate} "
        f"rejected={summary.rejected} failed={summary.failed}",
    )
    return summary
