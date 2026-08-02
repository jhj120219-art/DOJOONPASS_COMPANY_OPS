"""Outcome of a single Scheduler run_once() call."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date


class SchedulerStatus(enum.Enum):
    COMPLETED = "COMPLETED"
    SKIPPED_ALREADY_RUNNING = "SKIPPED_ALREADY_RUNNING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class SchedulerRunResult:
    status: SchedulerStatus
    generated_dates: tuple[date, ...]
    failed_date: date | None = None
    error: str | None = None
