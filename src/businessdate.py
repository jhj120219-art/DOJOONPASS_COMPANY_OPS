"""Business Date — the one place that turns an instant into a calendar day.

docs/06_DAILY_HISTORY_SPEC.md §9 and docs/07_SCHEDULER_CATCHUP_SPEC.md §4 both
fix this project's timezone at `Asia/Seoul` (UTC+09:00). Until this module
existed, nothing enforced that: every business date came from `.date()` on
whatever offset the value happened to carry, and every production "now" came
from `datetime.now().astimezone()` — the *machine's* zone. Both are the
correct answer only while every machine involved is set to KST, which is an
assumption about deployment, not a property of the code.

Measured, before this module (2026-08-28, C135):

    2026-08-28T01:00:00+09:00   ->  2026-08-28   (daily/generator._candidate_date)
    2026-08-27T16:00:00+00:00   ->  2026-08-27   (the *same instant*)

    TZ unset  datetime.now().astimezone()  ->  2026-08-28T08:48+09:00
    TZ=UTC0   datetime.now().astimezone()  ->  2026-08-27T23:48+00:00

The first pair misfiles an Event into the wrong Daily History day. The second
moves the Scheduler's whole catch-up window (`now.date() - 1`) back by a day.
`events.schema._timestamp_error()` requires *an* offset and deliberately
accepts a non-KST one, so the first pair is reachable from a Desktop whose
clock zone is not Seoul — a laptop taken abroad, a VM that defaults to UTC, a
restored or hand-written Event.

Four functions, and the split between the last two is the point:

    now()             production's "what time is it" — KST, on any machine
    to_kst()          the same instant, re-expressed in Seoul's offset
    business_date()   **data**'s day: an Event `timestamp` this machine parsed.
                      A naive value is refused, because the offset it was
                      written with is genuinely unknown.
    clock_date()      **this system's own clock**'s day: the injected `now`
                      every entrypoint takes. A naive value is read as KST,
                      because §9 says which wall this project reads.

Those last two agree for every aware value; `clock_date()`'s docstring carries
why the naive case differs, and why that is a stated default rather than a
guess.

**This module does not parse.** Callers keep their own `fromisoformat` and
their own error handling, because what counts as a readable timestamp is a
Spec question this project has deliberately left open (BACKLOG A-24), and
answering it here would turn a visible REJECTED into a silent `unreadable` at
every one of its call sites at once. This module only converts, and it refuses
to guess.

A leaf, like `oplog` and `runsummary`: everything that dates anything sits
above it, so it may sit under none of them, and it imports nothing from this
project.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta, timezone

#: docs/06 §9 — "기본 Timezone: Asia/Seoul", i.e. UTC+09:00.
#:
#: A fixed offset rather than `zoneinfo.ZoneInfo("Asia/Seoul")` on purpose:
#: Korea has observed no DST since 1988, so the two agree for every instant
#: this system will ever see, and `ZoneInfo` needs a tz database that Windows
#: does not ship (it would need `tzdata`, and `test_repository_hygiene.py
#: ::test_src_imports_only_the_standard_library` exists to keep this project
#: dependency-free).
KST = timezone(timedelta(hours=9), "KST")


def now() -> datetime:
    """The current instant, expressed in KST, on any machine.

    Replaces `datetime.now().astimezone()` everywhere a *production default*
    was needed. Callers that already take an injected `now` keep taking it —
    this is only what they fall back to.
    """
    return datetime.now(KST)


def to_kst(moment: datetime) -> datetime:
    """Same instant, re-expressed in KST. Raises on a naive input."""
    if moment.tzinfo is None:
        raise ValueError(
            "cannot convert a naive datetime to KST: the offset it was "
            f"written with is unknown ({moment.isoformat()!r})"
        )
    return moment.astimezone(KST)


def business_date(moment: datetime) -> date_type:
    """The Seoul calendar day `moment` fell on.

    This is the function that decides which Daily History an Event belongs to
    (docs/06 §12), how many days something has been silent, and where the
    Scheduler's catch-up window ends.

    Raises `ValueError` for a naive datetime rather than reading `.date()` off
    it. A naive value has no instant, so "which day was that in Seoul" has no
    answer; guessing one would move the result by up to a day in whichever
    direction the guess went. `notion/sync._as_comparable_timestamp()` refuses
    the same guess for the same reason, and callers that can genuinely see a
    naive value (Notion returns some) must decide at their own boundary.

    Use `clock_date()` instead for an injected "what time is it" — see the
    note there for why those two are different questions.
    """
    return to_kst(moment).date()


def clock_date(now: datetime) -> date_type:
    """The Seoul calendar day of a wall-clock reading.

    The same answer as `business_date()` for an aware value, and the reason
    both exist is the naive case, where they deliberately differ:

        business_date(naive)  ->  ValueError
        clock_date(naive)     ->  read as KST

    That is not `business_date()`'s refusal relaxed by the back door; the two
    take different *kinds* of value.

    `business_date()` dates **data** — a `timestamp` that some other machine
    wrote and this one parsed. docs/02's schema requires that value to carry
    an offset, so a naive one means the data is wrong or was mangled in
    transit, and reading a Seoul day off it would file real work on a day it
    did not happen. Refusing is the only honest answer.

    `clock_date()` dates **this system's own clock** — the `now` parameter
    every entrypoint takes so its behaviour can be pinned in a test. A naive
    value there is not damaged data; it is an unqualified wall-clock reading,
    and docs/06 §9 says which wall this project reads: Asia/Seoul. Applying a
    stated default is not the same act as guessing at an unknown.

    The hazard worth naming: `datetime.utcnow()` returns a naive value that is
    *not* local, and passing it here is read as KST and lands nine hours out.
    Nothing in this project calls it (`businessdate.now()` is what production
    uses, and it is aware), and no strictness here would catch that mistake
    anyway — an aware UTC `now` is accepted by both functions and is correct.
    """
    if now.tzinfo is None:
        return now.date()
    return business_date(now)
