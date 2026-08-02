"""Transport Intake — Desktop 4 receiving side (Phase 5.15 / 5.2).

Moves complete Event files from the local OneDrive-synced buffer
(runtime/events/transport/ — the Desktop 4-side mirror of the same cloud
folder OneDriveTransport.send() writes into) to runtime/events/incoming/,
where Collector Runtime (Phase 4.1, unchanged) already knows to look.

This module owns exactly one judgment call: "is this file done arriving,
or could OneDrive still be mid-sync?" It never validates Event content —
that is Collector's job once a file reaches incoming/. It stays inside
the `transport` package rather than `collector` on purpose, preserving
the "Collector doesn't know Transport" boundary this project has enforced
since Phase 3 (collector/*.py never imports transport, and vice versa —
the directory-existence checks below only touch Path objects, never
import the collector package).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSPORT_DIR = PROJECT_ROOT / "runtime" / "events" / "transport"
DEFAULT_INCOMING_DIR = PROJECT_ROOT / "runtime" / "events" / "incoming"
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "runtime" / "events" / "processed"
DEFAULT_REJECTED_DIR = PROJECT_ROOT / "runtime" / "events" / "rejected"
DEFAULT_STABLE_AFTER_SECONDS = 5.0


@dataclass(frozen=True)
class IntakeSummary:
    moved: tuple[str, ...]
    skipped_not_stable: tuple[str, ...]
    skipped_already_present: tuple[str, ...]
    skipped_invalid: tuple[str, ...]
    failed: tuple[str, ...]


def _is_stable(path: Path, *, now: float, stable_after_seconds: float) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return (now - mtime) >= stable_after_seconds


def _is_parseable_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return True


def run_intake(
    *,
    transport_dir: Path | None = None,
    incoming_dir: Path | None = None,
    processed_dir: Path | None = None,
    rejected_dir: Path | None = None,
    stable_after_seconds: float = DEFAULT_STABLE_AFTER_SECONDS,
) -> IntakeSummary:
    """Scan transport_dir once and move every stable, valid-JSON file that
    isn't already present in incoming/processed/rejected into incoming_dir.

    A file that fails any check is simply left in transport_dir — it is
    re-evaluated the next time run_intake() is called. Nothing is ever
    deleted here.
    """
    transport_dir = Path(transport_dir) if transport_dir is not None else DEFAULT_TRANSPORT_DIR
    incoming_dir = Path(incoming_dir) if incoming_dir is not None else DEFAULT_INCOMING_DIR
    processed_dir = Path(processed_dir) if processed_dir is not None else DEFAULT_PROCESSED_DIR
    rejected_dir = Path(rejected_dir) if rejected_dir is not None else DEFAULT_REJECTED_DIR

    transport_dir.mkdir(parents=True, exist_ok=True)
    incoming_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    moved: list[str] = []
    skipped_not_stable: list[str] = []
    skipped_already_present: list[str] = []
    skipped_invalid: list[str] = []
    failed: list[str] = []

    for path in sorted(transport_dir.glob("*.json")):
        already_elsewhere = any(
            (directory / path.name).exists()
            for directory in (incoming_dir, processed_dir, rejected_dir)
        )
        if already_elsewhere:
            # event_id-based duplicate safety: this event_id has already
            # arrived (or already been processed) once — Collector's own
            # dedup would catch this too, but there is no reason to hand
            # it a redundant copy.
            skipped_already_present.append(path.name)
            continue

        if not _is_stable(path, now=now, stable_after_seconds=stable_after_seconds):
            skipped_not_stable.append(path.name)
            continue

        if not _is_parseable_json(path):
            skipped_invalid.append(path.name)
            continue

        try:
            os.replace(path, incoming_dir / path.name)
        except OSError:
            failed.append(path.name)
            continue
        moved.append(path.name)

    return IntakeSummary(
        moved=tuple(moved),
        skipped_not_stable=tuple(skipped_not_stable),
        skipped_already_present=tuple(skipped_already_present),
        skipped_invalid=tuple(skipped_invalid),
        failed=tuple(failed),
    )
