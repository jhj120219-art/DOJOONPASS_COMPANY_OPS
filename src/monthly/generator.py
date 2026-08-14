"""Monthly History consolidation (docs/09_MONTHLY_HISTORY_SPEC.md).

Consolidates one month of Daily History into `monthly/YYYY-MM.md`, and
catches up every month that was missed while the PC was off.

The rules this implements, and where each comes from:

    §10, §38-39   Daily must be COMPLETE first; otherwise MONTHLY_PENDING
    §12-13        the input is the Daily files, never raw Events
    §40-44        PENDING / GENERATED / UPDATED / FAILED
    §47-49, §90   state-based catch-up, oldest month first, never the
                  current month
    §54-57        a Daily changed after the fact marks the month DIRTY;
                  the next run rebuilds it into UPDATED
    §58           an update never silently replaces the previous file
    §59           one event_id contributes exactly one entry
    §71-73        a month with nothing material still gets a file
    §74, §44      a save failure leaves the existing Monthly untouched

Nothing here loops, sleeps, or registers with an OS scheduler — the same
stance `scheduler/scheduler.py` and `agent/agent.py` take. `app/runner.py`
decides when this runs, and docs/09 §50-51 fix where in the sequence.
"""

from __future__ import annotations

import enum
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from .coverage import DailyCoverage, check_coverage
from .markdown import METADATA_TITLE, MonthlyItem, render_monthly_markdown
from .parser import DailyParseError, read_daily_document
from .state import (
    DEFAULT_STATE_PATH,
    load_state,
    month_key,
    parse_month_key,
    save_state,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MONTHLY_DIR = PROJECT_ROOT / "runtime" / "monthly"


def monthly_history_path(monthly_dir: Path, key: str) -> Path:
    """Where the Monthly History file for `key` (`"YYYY-MM"`) lives.

    One derivation, named. It was written out twice inside this module
    already, and a *reader* needs it too: `monthly_history_state.json`'s
    `last_successful_monthly_close` is a claim about a file, and anything
    checking that claim (docs/10 §48, "State Last Success -> Corresponding
    Local History 존재?") has to look in the same place the writer wrote.
    """
    return Path(monthly_dir) / f"{key}.md"


class MonthlyStatus(enum.Enum):
    """docs/09 §40-44. `GENERATED` covers §73's empty month too — the spec
    permits collapsing `MONTHLY_GENERATED_EMPTY` into "GENERATED with 0
    material items", and `item_count` on the result carries that."""

    MONTHLY_GENERATED = "MONTHLY_GENERATED"
    MONTHLY_UPDATED = "MONTHLY_UPDATED"
    MONTHLY_PENDING = "MONTHLY_PENDING"
    MONTHLY_FAILED = "MONTHLY_FAILED"
    MONTHLY_UNCHANGED = "MONTHLY_UNCHANGED"


@dataclass(frozen=True)
class MonthlyResult:
    year: int
    month: int
    status: MonthlyStatus
    item_count: int = 0
    source_dates: tuple[date_type, ...] = field(default_factory=tuple)
    coverage: DailyCoverage | None = None
    path: Path | None = None
    error: str | None = None
    # Per-day `"YYYY-MM-DD: N"` for days whose Daily file carries more
    # `- Event ID:` lines than this consolidation could turn into items.
    #
    # Not an error and not a status change: the month was still built from
    # everything the parser understood, which is what docs/09 §12-13 asks
    # for. It is the one fact the result could not otherwise carry — a Daily
    # item that did not reach Monthly leaves no trace in `item_count`,
    # because `item_count` counts what arrived, not what was sent.
    #
    # Free. `read_daily_document()` already parses every day of the month;
    # this is a number that parse computes on the way past.
    unconsolidated_days: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return month_key(self.year, self.month)


@dataclass(frozen=True)
class MonthlyRunResult:
    results: tuple[MonthlyResult, ...] = field(default_factory=tuple)
    last_successful_monthly_close: str | None = None

    @property
    def generated(self) -> tuple[str, ...]:
        return tuple(
            r.key
            for r in self.results
            if r.status in (MonthlyStatus.MONTHLY_GENERATED, MonthlyStatus.MONTHLY_UPDATED)
        )

    @property
    def pending(self) -> tuple[str, ...]:
        return tuple(r.key for r in self.results if r.status is MonthlyStatus.MONTHLY_PENDING)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def pending_months(
    *,
    last_successful_monthly_close: str | None,
    history_start_date: date_type,
    now: datetime,
) -> list[tuple[int, int]]:
    """Every closed month not yet consolidated, oldest first (§47-49, §90).

    "Closed" means strictly before the current month — §49 forbids
    consolidating a month still in progress, however late in it the run
    happens. On a first-ever run counting starts at `history_start_date`'s
    own month (§85-86: the system never invents months that predate it).
    """
    if last_successful_monthly_close is not None:
        year, month = parse_month_key(last_successful_monthly_close)
        current = _next_month(year, month)
    else:
        current = (history_start_date.year, history_start_date.month)

    last_closed = _previous_month(now.year, now.month)

    months: list[tuple[int, int]] = []
    while (current[0], current[1]) <= (last_closed[0], last_closed[1]):
        months.append(current)
        current = _next_month(*current)
    return months


def consolidate_month(
    *,
    year: int,
    month: int,
    daily_dir: Path,
    monthly_dir: Path,
    history_start_date: date_type,
    now: datetime,
    allow_update: bool = False,
) -> MonthlyResult:
    """Build (or rebuild) one month's Monthly History file.

    Returns rather than raises for every failure — §44 and §74 require that
    a failed consolidation leave the existing Monthly file in place and be
    retried later, which a return value gives the caller for free.

    `allow_update=False` (the default) refuses to touch an existing file:
    that is the §58/§79 protection for the normal catch-up path. The DIRTY
    rebuild path passes True, and even then the previous content is not
    silently replaced — the new file records `Last Updated At`.
    """
    daily_dir = Path(daily_dir)
    monthly_dir = Path(monthly_dir)
    final_path = monthly_history_path(monthly_dir, month_key(year, month))

    coverage = check_coverage(
        daily_dir, year, month, history_start_date=history_start_date
    )
    if not coverage.is_complete:
        # §39: Daily Catch-up comes first; until it succeeds this month is
        # PENDING, not FAILED — nothing is wrong, it is just not ready.
        return MonthlyResult(
            year=year,
            month=month,
            status=MonthlyStatus.MONTHLY_PENDING,
            coverage=coverage,
            error=(
                f"daily history incomplete: {len(coverage.missing_dates)} day(s) missing "
                f"({', '.join(d.isoformat() for d in coverage.missing_dates[:5])}"
                f"{' ...' if len(coverage.missing_dates) > 5 else ''})"
            ),
        )

    # `is_file()`, not `exists()`. "Is this month already consolidated" is a
    # question about a Monthly History document; a directory named
    # `2026-08.md` is not one. Measured with a complete month of Daily files
    # and such a directory present: `MONTHLY_UNCHANGED` — the month was
    # silently never written, and the catch-up pointer advanced past it
    # because UNCHANGED counts as success (see `run_once()`). With
    # `is_file()` the month is built, `os.replace()` onto the directory
    # fails, and the run reports MONTHLY_FAILED with the reason.
    already_exists = final_path.is_file()
    if already_exists and not allow_update:
        return MonthlyResult(
            year=year,
            month=month,
            status=MonthlyStatus.MONTHLY_UNCHANGED,
            coverage=coverage,
            path=final_path,
        )

    try:
        items: list[MonthlyItem] = []
        seen_event_ids: set[str] = set()
        source_dates: list[date_type] = []
        unconsolidated: list[str] = []

        for day in coverage.present_dates:
            document = read_daily_document(daily_dir / f"{day.isoformat()}.md", day)
            if document.unconsolidated:
                # Carried out, never raised. §44/§74 want a failed month to
                # leave the previous file alone and retry later; this is not
                # a failure — the month is built from everything the parser
                # understood. What it must not do is stay silent, which is
                # what it did before this line existed.
                unconsolidated.append(f"{day.isoformat()}: {document.unconsolidated}")
            contributed = False
            for entry in document.items:
                # §59: one event_id, one entry — however many Daily files
                # mention it (a re-collected Event, a restored backup).
                if entry.event_id in seen_event_ids:
                    continue
                seen_event_ids.add(entry.event_id)
                items.append(
                    MonthlyItem(
                        event_id=entry.event_id,
                        category=entry.category,
                        project=entry.project,
                        summary=entry.summary,
                        owner=entry.owner,
                        source_date=day,
                    )
                )
                contributed = True
            if contributed:
                # §36: list the days that actually supplied content, not
                # every empty day in the month.
                source_dates.append(day)

        generated_at = now.isoformat(timespec="seconds")
        last_updated_at = None
        if already_exists:
            # §58: preserve the original Generated At and record the update.
            previous_generated = _existing_generated_at(final_path)
            if previous_generated:
                generated_at = previous_generated
            last_updated_at = now.isoformat(timespec="seconds")

        markdown = render_monthly_markdown(
            year=year,
            month=month,
            items=items,
            source_dates=source_dates,
            generated_at=generated_at,
            coverage=coverage.description,
            last_updated_at=last_updated_at,
        )

        monthly_dir.mkdir(parents=True, exist_ok=True)
        # Named before it is hit. `os.replace()` onto a directory fails with
        # a bare `[WinError 5] 액세스가 거부되었습니다: '…\\.tmp-xxxx.md'`,
        # which names the temp file this function is about to delete and says
        # nothing about the month or what is blocking it. Same reasoning, same
        # wording, as `daily/generator.generate_daily_history()`.
        if final_path.exists() and not final_path.is_file():
            raise OSError(
                f"a non-file is in the way of monthly history: {final_path} — this "
                "month cannot be written there until it is moved"
            )
        fd, tmp_path = tempfile.mkstemp(dir=monthly_dir, prefix=".tmp-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(markdown)
            os.replace(tmp_path, final_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
    except (DailyParseError, OSError, ValueError) as exc:
        return MonthlyResult(
            year=year,
            month=month,
            status=MonthlyStatus.MONTHLY_FAILED,
            coverage=coverage,
            error=str(exc),
        )

    return MonthlyResult(
        year=year,
        month=month,
        status=(
            MonthlyStatus.MONTHLY_UPDATED if already_exists else MonthlyStatus.MONTHLY_GENERATED
        ),
        item_count=len(items),
        source_dates=tuple(source_dates),
        coverage=coverage,
        path=final_path,
        unconsolidated_days=tuple(unconsolidated),
    )


def _existing_generated_at(path: Path) -> str | None:
    """The `Generated At` already recorded in a Monthly file, or None.

    Carried forward on an update so that §58's pairing stays meaningful:
    `Generated At` says when the month was first closed, `Last Updated At`
    says when a Late Event changed it. Overwriting the former would erase
    exactly the fact the section exists to keep.

    Every failure yields None rather than propagating. This is a *best
    effort* on a file that is about to be replaced anyway: it used to catch
    only `OSError`, so a previous Monthly that was not valid UTF-8 raised
    `UnicodeDecodeError` (a ValueError) out of here and failed the entire
    rebuild — the one operation that would have repaired the corrupted file.
    Losing one metadata line is a far smaller harm than a month that can
    never be regenerated.

    Read from inside `## Metadata` only, not from the first matching line in
    the file. `render_monthly_markdown()` writes an item's summary raw as its
    block's first bullet, so a Daily summary of
    `Generated At: 1999-01-01T00:00:00+09:00` is rendered as a line
    indistinguishable from this field — and it sits *above* the Metadata
    block. Measured, one item carrying that summary:

        - Generated At: 1999-01-01T00:00:00+09:00   <- the item's summary
        - Generated At: 2026-09-01T02:00:00+09:00   <- the real field
        _existing_generated_at() -> '1999-01-01T00:00:00+09:00'

    The next rebuild of that month — an ordinary §58 dirty rebuild after a
    Late Event — then writes the forged value in as the month's real
    `Generated At`, and it stays: once it is in the Metadata block, removing
    the offending Event does not bring the original back. §58's whole point
    is that `Generated At` records when the month was first closed, so this
    is History integrity rather than cosmetics, and it needs no corruption
    or hand edit to reach — any Event author can write that summary.

    The **last** Metadata block wins rather than the first, which closes the
    same forgery via BUG-11/27's route. A summary is rendered unescaped, so
    one containing a newline and a literal `## Metadata` heading creates a
    real second block — but only ever above this one, because
    `render_monthly_markdown()` appends the Metadata block last on both of
    its paths (empty month and populated month alike). Preferring the last
    block therefore needs no escaping decision, which is BUG-11/27's to make.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    found: str | None = None
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            inside = stripped == METADATA_TITLE
            continue
        if inside and stripped.startswith("- Generated At:"):
            found = stripped.split(":", 1)[1].strip()
    return found


def run_once(
    *,
    daily_dir: Path,
    monthly_dir: Path,
    history_start_date: date_type,
    now: datetime | None = None,
    state_path: Path | None = None,
) -> MonthlyRunResult:
    """Consolidate every month that is due, then rebuild any dirty month.

    Order matters. Catch-up runs oldest-first and stops advancing at the
    first month that is not ready (§48's ordering, and the same "close in
    order, leave no gap" rule `scheduler.py` applies to days). Dirty
    rebuilds run afterwards and are independent — a month that is dirty has
    already been consolidated once, so it never blocks a later month.
    """
    now = now or datetime.now().astimezone()
    state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    state = load_state(state_path)

    results: list[MonthlyResult] = []

    for year, month in pending_months(
        last_successful_monthly_close=state.last_successful_monthly_close,
        history_start_date=history_start_date,
        now=now,
    ):
        result = consolidate_month(
            year=year,
            month=month,
            daily_dir=daily_dir,
            monthly_dir=monthly_dir,
            history_start_date=history_start_date,
            now=now,
        )
        results.append(result)

        if result.status in (
            MonthlyStatus.MONTHLY_GENERATED,
            MonthlyStatus.MONTHLY_UNCHANGED,
        ):
            state.last_successful_monthly_close = result.key
            # A month that was already on disk (UNCHANGED) still advances
            # the pointer: a crash between writing the file and saving state
            # must not leave that month re-processed forever.
            #
            # But only a month that was actually (re)built may have its DIRTY
            # flag cleared. UNCHANGED means the file was NOT rebuilt — the
            # existing one was left exactly as it was — so a DIRTY flag on it
            # is still true, and clearing it here discarded the one signal
            # that would have rebuilt it. Measured: a Monthly written by a run
            # that died before saving state, then a Late Event for that month;
            # this branch advanced the pointer and cleared DIRTY, and the
            # Late Event stayed in Daily and never reached Monthly, with
            # `last_successful_monthly_close` reporting the month as closed.
            # The dirty loop below now sees it and rebuilds it in the same run.
            if result.status is MonthlyStatus.MONTHLY_GENERATED:
                state.clear_dirty(result.key)
            save_state(state_path, state)
            continue

        # PENDING or FAILED — stop. Consolidating a later month while an
        # earlier one is still missing would leave a permanent hole that
        # nothing revisits.
        break

    first_month = (history_start_date.year, history_start_date.month)
    for key in list(state.dirty_months):
        year, month = parse_month_key(key)

        # §85-86: never invent a month that predates the system.
        # `pending_months()` applies this to the catch-up; the dirty loop
        # takes its months from the state file instead, so a hand-edited or
        # restored state naming a pre-history month walked straight past the
        # rule and wrote a Monthly for a month Company History does not
        # cover. Coverage says "complete" for such a month — there are zero
        # days to require — so nothing downstream refused it either.
        #
        # Reported rather than dropped, and the flag is deliberately left
        # in place: no run can resolve this one, and silently forgetting it
        # would hide a state file that needs a person.
        if (year, month) < first_month:
            results.append(
                MonthlyResult(
                    year=year,
                    month=month,
                    status=MonthlyStatus.MONTHLY_PENDING,
                    error=(
                        f"{key} predates the history start date "
                        f"({history_start_date.isoformat()}); Company History does not "
                        f"cover it and no run can consolidate it"
                    ),
                )
            )
            continue

        result = consolidate_month(
            year=year,
            month=month,
            daily_dir=daily_dir,
            monthly_dir=monthly_dir,
            history_start_date=history_start_date,
            now=now,
            allow_update=True,
        )
        results.append(result)
        # UPDATED is the normal outcome; GENERATED means the file was not
        # there and has just been built from scratch. Both leave a Monthly
        # that reflects Daily as it stands right now, which is exactly what
        # the DIRTY flag asks for, so both clear it. Only GENERATED was
        # missing here, so a dirty month whose file had been lost was
        # rebuilt correctly and then rebuilt again on every following run —
        # harmless work that also left `dirty_months` permanently non-empty,
        # which `ops_status.py` reports as "재생성 대기".
        if result.status in (MonthlyStatus.MONTHLY_UPDATED, MonthlyStatus.MONTHLY_GENERATED):
            state.clear_dirty(key)
            save_state(state_path, state)

    return MonthlyRunResult(
        results=tuple(results),
        last_successful_monthly_close=state.last_successful_monthly_close,
    )


def mark_month_dirty(
    state_path: Path, day: date_type, *, monthly_dir: Path | None = None
) -> bool:
    """Record that `day`'s Daily History changed after its Monthly was written.

    Called when a Late Event updates a Daily file (docs/09 §54-56). A no-op
    when that month has not been consolidated yet — the pending catch-up
    will read the updated Daily anyway, and marking it would only cause a
    redundant rebuild.

    "Not consolidated yet" has two indicators, and the state pointer is only
    one of them. The other is whether the Monthly file is on disk, and where
    they disagree the file wins — docs/10 §49's "History가 State보다
    우선", applied here rather than only at the Daily level.

    That disagreement is reachable: `run_once()` writes the Monthly file and
    *then* saves state, so a run that dies between the two leaves a real
    Monthly for a month the pointer says is still open. A Late Event landing
    before the next consolidation was then judged by the pointer alone,
    marked nothing, and the catch-up that followed found the file already
    present and left it untouched (MONTHLY_UNCHANGED) while advancing the
    pointer past it. Reproduced: the Late Event stayed in Daily, never
    reached Monthly, and every state field reported a healthy closed month.

    `monthly_dir=None` keeps the old state-only judgement, for callers that
    have no Monthly directory to consult. `app/runner.py` passes it.
    """
    state_path = Path(state_path)
    state = load_state(state_path)
    key = month_key(day.year, day.month)

    consolidated = (
        state.last_successful_monthly_close is not None
        and key <= state.last_successful_monthly_close
    )
    if not consolidated and monthly_dir is not None:
        # `is_file()` for the same reason `consolidate_month()` uses it: the
        # fact this is reaching for is "a Monthly History document is on
        # disk", and only a file is one.
        consolidated = monthly_history_path(monthly_dir, key).is_file()

    if not consolidated:
        return False
    if not state.mark_dirty(key):
        return False

    save_state(state_path, state)
    return True
