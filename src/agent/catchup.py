"""Boot Catch-up date arithmetic for the Multi-Desktop Agent.

Deliberately a pure function over dates — no clock gate, no timer, no
sleep, no Windows Task Scheduler awareness. The Agent runs when the PC is
switched on; what it must then answer is only "which calendar dates have
not been collected yet?", which is arithmetic, not scheduling. This is
the same stance `scheduler/scheduler.py` already takes for Daily History
(docs/07 §21: the eligible range is calendar-date math, not a clock-hour
gate), reused here for the sending side.

The rule, matching `scheduler._pending_dates()` exactly so that a Desktop
and Desktop 4 can never disagree about which dates exist:

    start = last_successful_collection_date + 1 day
            (or `start_date` on a first-ever run — never guessed,
             docs/07 §50)
    end   = today - 1 day
            (today is never collected — docs/07 §18: a day still in
             progress is not a finished day)

So with last success 2026-08-07 and "now" 2026-08-11, the pending dates
are 08-08, 08-09, 08-10 — oldest first, no gaps.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from businessdate import clock_date


def pending_dates(
    *,
    last_successful_collection_date: date | None,
    start_date: date,
    now: datetime,
) -> list[date]:
    """Every not-yet-collected calendar date, oldest first.

    Returns an empty list when nothing is pending — including the case
    where `last_successful_collection_date` is already at or past
    yesterday (a PC switched on twice in one day collects nothing the
    second time), and the case where a state file somehow records a
    future date (then `start > end` and no date is produced; this
    function never walks backwards).
    """
    start = (
        last_successful_collection_date + timedelta(days=1)
        if last_successful_collection_date is not None
        else start_date
    )
    end = clock_date(now) - timedelta(days=1)

    dates: list[date] = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    return dates
