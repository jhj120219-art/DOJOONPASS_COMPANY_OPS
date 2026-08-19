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
    """What one Daily Close pass did.

    `generated_dates` and `reused_dates` are a partition of the dates this
    run closed, split by whether it actually wrote the file (C39).

    They used to be one list, and it was called `generated_dates` while
    containing both. The conflation was documented in `scheduler.py`'s loop
    ("Either just generated, or the file already existed … either way this
    date is now done") and correct for what the loop needed — a closed date
    is closed — but every *reporter* downstream said "generated" about dates
    nothing had generated.

    Rare after a crash (§28: one day), systematic after a disaster restore.
    Measured, restoring three days of Company History from the backup remote
    and running once, exactly as docs/10 §45 prescribes:

        generated_dates   2026-08-01 … 2026-08-05      (five)
        files written     2026-08-05                   (one)
        Dashboard         Generated Days: 5
        manifest          SUCCESS / exit 0

    The four restored days came back from git, not from this pipeline —
    which could not have rebuilt them, since History Candidates are not in
    the backup (docs/08 §26). So the one run an operator scrutinises hardest
    reported the largest possible activity, and reported it as *generation*,
    on a Desktop whose History had just been recovered from elsewhere.
    """

    status: SchedulerStatus
    generated_dates: tuple[date, ...]
    reused_dates: tuple[date, ...] = ()
    failed_date: date | None = None
    error: str | None = None

    @property
    def closed_dates(self) -> tuple[date, ...]:
        """Every date this run advanced the watermark past, in order.

        The union the old `generated_dates` used to be. Callers asking "how
        far did Daily Close get" want this one; callers asking "what did
        this run write" want `generated_dates`.
        """
        return tuple(sorted(self.generated_dates + self.reused_dates))
