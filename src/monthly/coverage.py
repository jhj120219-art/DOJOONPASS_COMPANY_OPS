"""Daily Coverage for one month (docs/09 §10, §38-39, §85).

docs/09 §10 makes Daily completeness a precondition, not a nicety: a Monthly
built while two days of that month are still missing would silently under-
report the month, and nothing downstream would ever notice. So the order is
fixed —

    Missing Daily 탐지 -> Daily Catch-up -> Daily Complete -> Monthly 생성

— and this module answers only the first question.

An **Empty Daily counts as covered** (§11). A day on which nothing material
happened still produces a file saying so, and treating that as a gap would
make every quiet month permanently un-consolidatable.

A month that starts mid-way is covered from `history_start_date` onward
(§85), not from the 1st: Company History did not exist before that date, and
demanding files for days that predate the system would block the very first
Monthly forever.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path


@dataclass(frozen=True)
class DailyCoverage:
    year: int
    month: int
    expected_dates: tuple[date_type, ...] = field(default_factory=tuple)
    present_dates: tuple[date_type, ...] = field(default_factory=tuple)
    missing_dates: tuple[date_type, ...] = field(default_factory=tuple)
    starts_mid_month: bool = False

    @property
    def is_complete(self) -> bool:
        """docs/09 §38: only COMPLETE permits an official Monthly."""
        return not self.missing_dates

    @property
    def status(self) -> str:
        return "COMPLETE" if self.is_complete else "INCOMPLETE"

    @property
    def description(self) -> str:
        """The one-line form written into Monthly Metadata (§37).

        §85 requires a mid-month start to be visible in the Metadata rather
        than left for a reader to infer from a short date range.
        """
        if not self.expected_dates:
            return f"{self.status} (0 days expected)"
        first = self.expected_dates[0].isoformat()
        last = self.expected_dates[-1].isoformat()
        covered = f"{len(self.present_dates)}/{len(self.expected_dates)} days"
        span = f"{first} ~ {last}"
        if self.starts_mid_month:
            return f"{self.status} ({covered}, {span}, partial month from history start)"
        return f"{self.status} ({covered}, {span})"


def month_dates(year: int, month: int) -> list[date_type]:
    _, last_day = calendar.monthrange(year, month)
    return [date_type(year, month, day) for day in range(1, last_day + 1)]


def check_coverage(
    daily_dir: Path,
    year: int,
    month: int,
    *,
    history_start_date: date_type | None = None,
    today: date_type | None = None,
) -> DailyCoverage:
    """Which of the month's days have a Daily History file.

    `history_start_date` trims the expected range at the front (§85).
    `today` trims it at the back, which only matters for the current month —
    a month still in progress is never consolidated (§49), but a caller may
    still want to see how far it has got.
    """
    daily_dir = Path(daily_dir)
    expected = month_dates(year, month)

    starts_mid_month = False
    if history_start_date is not None:
        before = len(expected)
        expected = [day for day in expected if day >= history_start_date]
        starts_mid_month = len(expected) != before and bool(expected)

    if today is not None:
        expected = [day for day in expected if day <= today]

    present = [day for day in expected if (daily_dir / f"{day.isoformat()}.md").is_file()]
    missing = [day for day in expected if day not in set(present)]

    return DailyCoverage(
        year=year,
        month=month,
        expected_dates=tuple(expected),
        present_dates=tuple(present),
        missing_dates=tuple(missing),
        starts_mid_month=starts_mid_month,
    )
