"""Company Ops Status — read-only. Prints, changes nothing.

    python ops_status.py

Three views, all built only from files that already exist:

    COMPANY   what Desktop 4 knows about every Desktop, from the Events it
              has collected (src/app/desktop_activity.py)
    HISTORY   where the Company Repository stands — Daily count, Monthly
              whether a month is waiting to be consolidated or rebuilt
    AGENT     this machine's own Agent, if one is configured here
              (src/agent/status.py)

Run it on Desktop 4 and all three appear. Run it on Desktop 1/2/3 and the
COMPANY and HISTORY views are empty (that machine collects and consolidates
nothing) while the AGENT view answers "is my own delivery stuck?".

Why this exists
---------------
An Agent's success or failure was recorded only on its own machine. The COO
seat, which is where someone would act on "Desktop 1 has not delivered for
three days", could not see it. This does not add a heartbeat or any new
Event — it reads the Events already collected and the Agent state already
written. See src/app/desktop_activity.py on why silence is reported rather
than diagnosed.

Nothing here acquires a lock, so it is safe to run while a Runner or Agent
is working. Nothing here writes, so it is safe to run at any time.

Exit codes:
    0   nothing needs a person
    1   configuration error
    3   at least one thing needs a person (see the ATTENTION section)
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Mapping, NamedTuple, Sequence

# `line_buffering=True` keeps this script's own output in the order it was
# written. Python block-buffers stdout when it is not a terminal and leaves
# stderr unbuffered, so under the redirection a scheduled task actually uses
# (`>log 2>&1`) every stderr line overtakes the stdout lines around it.
# Measured: a failure message printed above the context line explaining it.
# That is the one reading an operator gets from a captured log, and it makes
# a report look like it describes the wrong thing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import businessdate  # noqa: E402
from agent.delivery import find_undelivered_events  # noqa: E402
from agent.status import read_status  # noqa: E402
from app.desktop_activity import read_company_activity  # noqa: E402
from app.runner import DEFAULT_RUN_SUMMARY_PATH, PIPELINE_COMPONENTS  # noqa: E402
from backup.state import BackupStateError  # noqa: E402
from daily.markdown import (  # noqa: E402
    # `_display_project_name` is private and imported anyway, for C28's
    # rule: the detector below has to fold names with the **renderer's**
    # transform, and a second copy of `.title()` here would be a second
    # opinion about what Company History calls a project. Same reason
    # `controltower/rollup.py` imports `notion.properties.
    # _type_specific_properties`.
    _display_project_name,
    item_block_bounds,
    summary_line_indices,
)
from daily.late_events import existing_event_ids  # noqa: E402
from backup.state import load_state as load_backup_state  # noqa: E402
from backup.working_copy import scan_for_secrets  # noqa: E402

# The gate's own name list, not a second opinion about what a secret looks
# like — same reason `_count_transport` reuses intake's parse test. A history
# path is compared by its basename, exactly as `scan_for_secrets()` does.
from backup.working_copy import _looks_like_secret  # noqa: E402

# The scope set the Backup gate enforces, imported rather than restated so a
# third scope directory is diagnosed without editing this file.
from backup.working_copy import _ALLOWED_TOP_LEVEL_DIRS  # noqa: E402

# The gate's own listing, for the same reason as the two above. `deleted` in
# `sync_to_working_copy()` is literally `_relative_files(working_copy) -
# _relative_files(master)`, so asking it here is not a second opinion about
# which files count as Company History — it is the same question, asked
# read-only. Anything else (a `glob("*.md")` of my own) would drift from the
# scope, symlink and `.tmp-` rules that function already applies.
from backup.working_copy import _relative_files  # noqa: E402
from history.file_repository import is_incomplete_write  # noqa: E402
from history.result import candidate_errors  # noqa: E402
from history.reconciliation import find_orphaned_events  # noqa: E402

# The field->label pairing `review_cli.py` prompts with and
# `daily/markdown.py` renders with. Imported rather than restated so
# `_reviewed_but_not_rendered()` asks exactly what the renderer answers —
# the same rule `_kept_but_not_rendered()` follows for the Event ID line.
from review_cli import _REVIEW_FIELDS  # noqa: E402

# The two Notion queues. `notion` imports only `events`
# (LayeringInvariantTests), so reading them from here adds no cycle — and
# their loaders are the same ones the Runner uses, not a second parser.
from notion.dashboard_pending import DashboardPendingError  # noqa: E402
from notion.dashboard_pending import load_pending as load_dashboard_pending  # noqa: E402
from notion.retry_queue import RetryQueueError  # noqa: E402
from notion.retry_queue import load_queue as load_notion_retry_queue  # noqa: E402
from monthly import MonthlyStateError, monthly_history_path  # noqa: E402

# The parser Monthly consolidates with, so "an item" means the same thing
# here as it does there — see `_monthly_lags_its_daily_source()`.
from monthly.parser import read_daily_document  # noqa: E402
from monthly import load_state as load_monthly_state  # noqa: E402
from runsummary import (  # noqa: E402
    ComponentStatus,
    OverallStatus,
    Retryability,
    RunSummaryError,
    read_summary,
)
from scheduler.consistency import (  # noqa: E402
    ConsistencyStatus,
    check_state_consistency,
)
from oplog import bounded, one_line, redact  # noqa: E402

# The only reader in this repository of state that lives outside it.
# Every other block here is derived from files this system wrote, and
# that is exactly the evidence a task which never started the process
# does not produce. A leaf, so it adds no edge that could form a cycle.
import schedtask  # noqa: E402

# The same compiled rule the Agent refuses a Signal with and `redact()`
# scrubs a log line with. A second opinion about what a secret looks like is
# how the door that refuses and the door that reports drift apart (C28).
from oplog import SECRET_RE  # noqa: E402

# The business layer of the view. `controltower` writes nothing and reads the
# same `processed/` directory this file already reads twice — see
# `_print_control_tower()`, which renders from `build_dashboard()`'s model so
# that the screen and any projection of it are one arrangement of one fold.
from controltower import (  # noqa: E402
    UNSOURCED_LAYERS,
    build_company_rollup,
    build_dashboard,
    read_events,
    unsourced_layer_coverage,
)
from notion.properties import ROLE_DISPLAY_NAMES  # noqa: E402
from cli import (  # noqa: E402
    CONFIG_ERROR_EXIT,
    output_is_gone,
    run_entrypoint,
    unexpected_arguments,
)  # noqa: E402
from scheduler.lock import (  # noqa: E402
    is_locked,
    lock_held_since,
    stale_lock_cannot_be_cleared,
)

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"

# How many Project lines CONTROL TOWER prints before it says "외 N건". A
# status block a person scrolls is a block a person stops reading; the full
# list is one `python -c` away and the blocked ones sort to the top.
_CONTROL_TOWER_PROJECT_LINES = 8


def _agent_dir() -> Path:
    """`runtime/agent`, derived when asked rather than frozen at import.

    It used to be a module-level constant. That made `RUNTIME_DIR` a knob
    that only half worked: redirecting it — which is how every test and probe
    isolates this view — left `AGENT_DIR` pointing at the developer's real
    `runtime/agent`, so the AGENT block silently reported the live machine
    while every other block reported the fixture.

    Measured, and not hypothetically: a probe written during C31 set
    `RUNTIME_DIR` to a temp tree holding a future-dated `agent_state.json`,
    read back "agent has not run for 3 day(s)" (그때의 문구; C120에서 한국어로 바뀌었다) from this
    repository's own runtime, and nearly recorded a working check as
    missing.

    This is C13's 결함 2 in a second place, and its wording applies verbatim:
    *"Reaching for the default inside here made this function depend on a
    path its caller never named: measured, a test calling it directly picked
    up the repository's own live manifest — which said SUCCESS — and got
    exit 0 for a Backup failure."* Deriving on call makes `RUNTIME_DIR` the
    single knob for everything this module owns, so isolating the view can no
    longer be half-done.

    The rule was already written down **in this file**, on
    `_runner_lock_path()`: *"Resolved per call, not at import: `RUNTIME_DIR`
    is rebound by tests … and a path frozen at import would keep pointing at
    the old one."* This applies that rule to the one place that missed it
    rather than inventing one — the same shape as C20 §3 (the spec named two
    files and only one was in the list) and C27 §4 (the Runner lock was
    watched and the Agent lock was not).

    `DEFAULT_RUN_SUMMARY_PATH` is deliberately NOT folded in. It belongs to
    `app.runner`, which decides where the manifest lives; re-deriving it from
    `RUNTIME_DIR` would be a second opinion about another module's layout. It
    stays its own knob, and the tests that exercise LAST RUN set it.
    """
    return RUNTIME_DIR / "agent"


# A Desktop that is simply switched off for a weekend is normal in this
# deployment (docs/07 section 58), so silence is only worth flagging after
# more than a couple of days. A threshold that fires every Monday gets
# ignored, and an ignored alert is worse than none.
SILENT_AFTER_DAYS = 3

# How long a held Runner lock stops looking like work and starts looking
# like a lock nobody will ever release. Measured elsewhere in this project:
# a 20,000-Event batch reconciles in seconds, and the git subprocess timeout
# is 300 s, so a real run is orders of magnitude under this. Generous on
# purpose — the cost of firing early is telling an operator to interrupt a
# healthy long run.
LOCK_STUCK_AFTER_HOURS = 2

# How far ahead of the real clock `backup_state.last_successful_backup` has
# to be before it is clock skew rather than a race with a Runner that is
# still finishing. See the check itself for the reasoning; the short version
# is that the harm scales with the distance — an hour ahead heals itself,
# months ahead does not.
CLOCK_AHEAD_TOLERANCE_HOURS = 1

# The line `daily/markdown.py` writes for each rendered Candidate.
# Kept as one constant because `_kept_but_not_rendered()` has to ask
# exactly what the renderer answers — a looser test reported a
# stranded Event as rendered (C30).
_EVENT_ID_LINE_PREFIX = "- Event ID: "

# Same sizing and the same measured reason as
# `app/desktop_activity.py`, `history/reconciliation.py` and
# `agent/delivery.py`: the cost of these scans is the file OPEN, and a
# pool of 16 is where it plateaus on this machine.
_READ_WORKERS = max(4, min(16, (os.cpu_count() or 4) * 2))


def _would_reach_the_commit(working_copy: Path, candidates: tuple[str, ...]) -> tuple[str, ...]:
    """Of `candidates`, the ones git would actually put in a commit.

    `scan_for_secrets()` answers "is this a secret-shaped filename", which
    is the right question for the Backup gate and the wrong one for this
    report. What reaches the remote is what `git add -A` stages, and git
    ignores whatever `.gitignore` tells it to — including the very entries
    docs/08 §28 asks a Backup Repo to carry (`.env`, `.env.*`, `*.tmp`,
    `*.log`).

    Without this the report was a standing false alarm for a *correctly
    configured* Working Copy: measured, an operator who added §28's
    `.gitignore` still saw "이 파일들은 ... 원격에 올라간다" on every run,
    for a file git was correctly refusing to commit. That is the
    alert-that-cannot-clear this project keeps warning about
    (`IntakeBacklog`'s own docstring), introduced by the C24 check itself.

    git is asked rather than second-guessed. `ls-files -c -o
    --exclude-standard` lists exactly what is tracked plus what is untracked
    and not ignored — the set `add -A` would end up with. Parsing
    `.gitignore` here instead would be a second opinion about git's rules,
    which is the disagreement this codebase closes elsewhere by reusing the
    authority (`_count_transport` reuses intake's own parse test).

    Fail-safe: any failure — no git, not a repository, a timeout — returns
    the candidates unchanged, so a broken probe over-reports rather than
    hiding a real exposure. Only run when there is something to check, so a
    clean Working Copy costs no subprocess at all.
    """
    if not candidates:
        return ()
    try:
        result = subprocess.run(
            ["git", "ls-files", "-c", "-o", "--exclude-standard"],
            cwd=working_copy,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return candidates
    if result.returncode != 0 or result.stdout is None:
        return candidates
    listed = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return tuple(
        name for name in candidates if name.replace("\\", "/") in listed
    )


def _daily_dates(daily_dir: Path) -> list[date]:
    """Every date `daily_dir` actually holds a Daily History file for.

    `os.scandir` rather than `glob` + `is_file()`: the directory entry
    already carries the file-or-directory answer, so asking `is_file()` on a
    `Path` costs a second stat per file. Measured, and the reason this is
    not the tidier `glob`:

        730 files    glob+is_file 10.90 ms    scandir 0.69 ms   (16x)
        3650 files   glob+is_file 58.75 ms    scandir 3.48 ms   (17x)

    Ten years of Daily History is the second row, against a whole-view
    baseline of ~44 ms on this machine. The two forms were asserted to
    return identical lists before the swap.

    A directory named `2026-08-05.md` is excluded, for the reason C31 wrote
    across six other call sites: it exists, and it is not a day of Company
    History.
    """
    found: list[date] = []
    try:
        entries = list(os.scandir(daily_dir))
    except OSError:
        return []
    for entry in entries:
        if not entry.name.endswith(".md") or is_incomplete_write(entry.name):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        try:
            found.append(date.fromisoformat(entry.name[:-3]))
        except ValueError:
            continue  # a hand-added note, not a day of History
    return sorted(found)


def _holes_in_the_daily_sequence(daily_dir: Path) -> tuple[str, ...]:
    """Dates inside the closed range that have no Daily History file.

    The Daily filenames must form an unbroken run of dates. docs/07 §30's
    "close in order, leave no gap" is one half of that, and the other is
    that `generate_daily_history()` writes a file for a day with no work
    too — an empty day is recorded, not skipped, which is exactly what
    docs/09 §72 says the empty *month* file is for. So a date sitting
    between two days that do have files was closed, had a file, and no
    longer does.

    Nothing was looking. Measured on ten closed days with 08-04..08-06
    removed — the shape a partial restore, a half-synced OneDrive folder, or
    a hand deletion (docs/06 §57 permits editing, and deleting is an edit)
    leaves behind:

        check_state_consistency()   CONSISTENT
        ATTENTION                   nothing about the three days
        Scheduler next run          starts at last_close + 1, never returns

    Three days of Company History gone, permanently, with every indicator
    healthy. `check_state_consistency()` is not wrong — §47 asks it whether
    the *last* closed day has a file, and it does — it simply never had the
    interior in view.

    Bounded by the files themselves rather than by
    `COMPANY_OPS_HISTORY_START_DATE`, which is often unset (see
    `_history_start_date()`) and would make this check disappear when it is.
    The earliest file present is a lower bound that needs no configuration
    and cannot be wrong: whatever came before it is outside this machine's
    History, and a gap strictly between two present days is not.

    A missing *suffix* is deliberately not reported here — a run that failed
    part-way leaves one, it is the normal retry shape, and the next run
    fills it. Only the interior.

    Verified against this machine's own runtime before being written:
    `local_master/daily` and `backup_working_copy/daily` both hold
    2026-08-05..2026-08-10 with no hole, so the premise this check rests on
    is true of the real tree and not only of a fixture.
    """
    days = _daily_dates(daily_dir)
    if len(days) < 2:
        return ()
    present = set(days)
    span = (days[-1] - days[0]).days + 1
    return tuple(
        (days[0] + timedelta(days=offset)).isoformat()
        for offset in range(span)
        if (days[0] + timedelta(days=offset)) not in present
    )


def _holes_in_the_monthly_sequence(monthly_dir: Path) -> tuple[str, ...]:
    """Months inside the consolidated range that have no Monthly file.

    The exact sibling of `_holes_in_the_daily_sequence()`, and it rests on
    the same two facts: `pending_months()` consolidates oldest-first without
    skipping, and docs/09 §72 writes a file for a month with no material
    history too — precisely so that "nothing happened" and "we forgot" stay
    distinguishable. So the Monthly filenames are a contiguous run of months
    and an interior gap is a file that was there.

    Nothing was looking here either. Measured with 2026-01..2026-08
    consolidated and 04/05 deleted: no ATTENTION line mentions them,
    `pending_months()` starts *after* `last_successful_monthly_close` so no
    run revisits them, and the state-vs-history check asks only about the
    last closed month.

    The remedy is better than Daily's and is worth stating, because it is
    exact: Monthly is derived wholly from the Daily files (docs/09 §12-13),
    so marking the month dirty rebuilds it. Measured end to end —

        delete 2026-07.md      plain re-run: statuses [] , still missing
        mark_month_dirty()     MONTHLY_GENERATED, file back, EVT-1 in it

    — which is the whole point of Monthly being a derived artifact. Daily
    cannot promise that, and this function's message does not pretend it
    can.
    """
    if not monthly_dir.is_dir():
        return ()
    keys: list[tuple[int, int]] = []
    try:
        entries = list(os.scandir(monthly_dir))
    except OSError:
        return ()
    for entry in entries:
        if not entry.name.endswith(".md") or is_incomplete_write(entry.name):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        try:
            year, month = entry.name[:-3].split("-")
            keys.append((int(year), int(month)))
        except ValueError:
            continue
    if len(keys) < 2:
        return ()
    keys.sort()
    present = set(keys)
    (first_year, first_month), (last_year, last_month) = keys[0], keys[-1]
    span = (last_year - first_year) * 12 + (last_month - first_month) + 1
    missing = []
    for offset in range(span):
        index = (first_year * 12 + first_month - 1) + offset
        candidate = (index // 12, index % 12 + 1)
        if candidate not in present:
            missing.append(f"{candidate[0]:04d}-{candidate[1]:02d}")
    return tuple(missing)


def _history_gone_from_local_master(local_master: Path, working_copy: Path) -> tuple[str, ...]:
    """Company History the Backup Working Copy holds and Local Master no longer does.

    The mirror image of `_history_newer_than_the_last_backup()`. That one asks
    "is what is on this machine actually off it"; this asks **"is what got off
    this machine still on it"** — and until now nothing did.

    Why the two hole checks cannot answer it. `_holes_in_the_daily_sequence()`
    bounds its range by the files that are present, and states the premise
    plainly: *"whatever came before it is outside this machine's History"*.
    That is exactly false when the days that went missing are the **earliest**
    ones — a partial restore that stopped part-way, a OneDrive folder that
    synced from the top, a hand deletion of "the old ones". The first present
    file simply moves forward and the range moves with it, so a missing prefix
    is silence by construction. `check_state_consistency()` covers the last
    closed day and `_kept_but_not_rendered()` skips a date with no file, so
    neither reaches it either.

    Measured, `2026-08-01.md` replaced by a **directory of the same name**
    (the shape C31 chased across six call sites, and the one a half-finished
    copy leaves) with 08-01..08-04 closed:

        _holes_in_the_daily_sequence()      ()
        _kept_but_not_rendered()            ()
        check_state_consistency()           CONSISTENT
        _daily_counts_more_than_it_shows()  ()
        _misnamed_scope_directories()       ()
        ATTENTION                           nothing naming 2026-08-01

    A whole day of Company History, gone, with every Company History
    indicator green. Backup does fail — its deletion gate (docs/08 §31) sees
    the same thing — but the manifest's `reason` is the only place the
    filename appears and `_print_last_run()` deliberately does not print
    `reason`, so what an operator reads is `backup: BACKUP_FAILED`, which is
    the same line a credential failure produces. The Runner's own comment
    beside that gate names precisely that confusion as the thing it was
    written to remove.

    Why the answer needs no configuration and cannot be wrong. The Working
    Copy is written one direction only, and `sync_to_working_copy()` never
    deletes from it — a detected deletion makes it apply *nothing* (docs/08
    §31/§44-47). So it is a monotonic record of every Company History file
    that ever reached backup scope, and a name in it that Local Master does
    not have is a file that existed and does not now.

    `_relative_files()` is the gate's own listing rather than a `glob()` of
    my own, so "Company History" means here exactly what it means there:
    inside `daily/`/`monthly/`, a regular file, not a symlink, not `.tmp-`.
    A directory wearing a day's name is therefore absent on the Master side
    and present on the Working Copy side, which is the measurement above.

    Detection only, like every check in this file. What to do about it is an
    operator decision (docs/10 §64), and restoring the file is the operator's
    to make — the Working Copy still has it, which is why the caller says so.
    """
    working_copy = Path(working_copy)
    local_master = Path(local_master)
    if not working_copy.is_dir() or not local_master.is_dir():
        return ()
    return tuple(sorted(_relative_files(working_copy) - _relative_files(local_master)))


def _history_newer_than_the_last_backup(local_master: Path, last_backup: datetime | None):
    """Company History files written after the last successful backup.

    Answers the one question `ops_status.py` could not: **is what is on this
    machine actually off it?** `backup_state.json` has held the answer since
    the Backup step was written and nothing has ever read it —
    `test_runner_failure_paths.py` says so in as many words about BUG-55:
    "the one artifact that would betray it is `last_successful_backup` never
    advancing, which nothing surfaces."

    BUG-55 is exactly what this catches. `working_copy._is_in_scope()`
    compares `parts[0]` against `{"daily", "monthly"}` case-sensitively, so a
    `Daily/` directory — which docs/11 has a human create — is out of scope
    on a filesystem that considers it the same directory. Measured, three
    consecutive runs: `BACKUP_NOT_REQUIRED` every time, an empty remote, and
    this view reporting "daily 파일: 1" with ATTENTION clear.

    **A clock threshold would be the wrong instrument.** Company History that
    has not changed does not need backing up, so "the last backup was N days
    ago" is normal on a quiet week and would be a standing false alarm. The
    condition that is never normal is *newer history than the last successful
    push* — it clears the moment a backup succeeds and cannot fire while
    nothing is being written.

    Scanned by extension across the whole of Local Master rather than through
    `_is_in_scope()`: reusing the scope predicate would inherit the very
    case-sensitivity that causes BUG-55, and this check would go blind to the
    defect it exists for. Staging files are excluded — an unfinished write is
    not history (C27).
    """
    if not local_master.is_dir():
        # No Local Master: an absent subject, not a failed read. Zero skips.
        return [], 0
    candidates = [
        path
        for path in local_master.rglob("*.md")
        if path.is_file() and not is_incomplete_write(path.name)
    ]
    if last_backup is None:
        return sorted(candidates), 0
    reference = last_backup
    newer = []
    skipped = 0
    for path in candidates:
        try:
            # `tz=KST` rather than a bare `.astimezone()` on a naive value
            # (C135): an mtime is an epoch, so it names an instant, and the
            # frame it is read in should be this project's stated one rather
            # than whatever zone the machine is set to. The comparison below
            # is instant-based either way -- this removes the naive-then-
            # assume-local step, not a defect.
            written = datetime.fromtimestamp(
                path.stat().st_mtime, tz=businessdate.KST
            )
        except OSError:
            # C68: counted, not swallowed. This function answers "is what is
            # on this machine actually off it?", and a file whose mtime
            # cannot be read is a file this answer does not cover. Dropping
            # it silently makes the list *shorter*, which is the direction
            # that reads as reassurance.
            skipped += 1
            continue
        if reference.tzinfo is None:
            written = written.replace(tzinfo=None)
        if written > reference:
            newer.append(path)
    return sorted(newer), skipped


def _secrets_ever_committed(working_copy: Path) -> "tuple[tuple[str, ...], bool]":
    """Secret-shaped paths that exist anywhere in this repository's history.

    `_would_reach_the_commit()` answers "what will the NEXT commit carry".
    The remote's history is a different question, and not asking it made the
    Working Copy report clear for the wrong reason.

    Measured: a `.env` holding a Notion token reached the remote (E-21), the
    report fired, and then the operator did what the message leads to —
    deleted the file. The warning went away. `git show HEAD:.env` on the
    remote still returned the token in full. **The alert cleared because the
    local file was gone, not because the exposure was.**

    That is the one case where "the warning disappeared" is the most
    dangerous possible answer, and it is exactly the case an operator is
    most likely to produce, because deleting the file is the obvious move.

    `rev-list --all --objects` is used rather than `log --name-only`: it
    answers precisely "every path that has ever existed in history" and
    measured cheapest of the three shapes tried — 0.19 s versus 0.34 s at
    3,000 commits (about eight years of daily Company History), and
    pathspec-bounded `log` was slowest of all because it forces a per-commit
    diff. Cost grows with commits, which grow one per day.

    **Unlike `_would_reach_the_commit()`, this is not free when clean**, and
    that trade is deliberate rather than overlooked. That probe short-circuits
    on an empty candidate list, so a healthy Working Copy costs no subprocess
    at all; this one has to ask git before it knows there is nothing to say,
    because the condition it reports is precisely the one where nothing is on
    disk. Measured end to end on `main()`:

        working copy commits    0      100     1,000    3,000
        whole command        0.026s  0.085s   0.229s   0.369s

    Sub-second at eight years of daily backups, on a command an operator runs
    interactively. No cheaper query answers the question — the alternative is
    not asking it, which is the state this function exists to end.

    **This cannot fire on a healthy machine.** A Working Copy carrying
    docs/08 §28's `.gitignore` never commits such a path, so history never
    contains one. It appears only after a real exposure — which is why it is
    allowed to stand in ATTENTION rather than being softened into a block
    line: it is not the standing-alert-on-a-correct-machine shape C26
    removed.

    Fail-safe direction is the opposite of `_would_reach_the_commit()`'s, on
    purpose. That probe filters a set it was handed, so failing open keeps a
    real exposure visible. This one *adds* a claim about history; if git
    cannot answer, asserting a leak would be inventing one. Silence returns
    the report to exactly today's behaviour, and the present-file gate is
    unaffected.

    Known limit: it reads the local Working Copy's history. A Working Copy
    re-created from scratch (docs/08 §30 permits it) has no old commits even
    though the remote still does. Nothing here can see that without network
    access to the remote, which this read-only view does not take.

    Case-folded as well as exact (E-24). The gate's comparison is
    case-sensitive and Windows is not, so `daily/ID_RSA` is precisely the
    path that reaches the remote — measured, BACKUP_SUCCESS with the key
    readable via `git show`. Matching only the exact spelling would leave
    this report blind at the one place the leak actually happens.

    Widening the *report* is not widening the *gate*. `scan_for_secrets()` is
    untouched: nothing here can make a backup fail, which is the property
    that puts E-24's real fix behind a decision. And over-reporting is the
    direction this project already chose for every secret-shaped signal —
    `_would_reach_the_commit()` falls back to over-reporting when git cannot
    answer, and `oplog.SECRET_PATTERNS` deliberately over-matches, for the
    same asymmetry: an unnecessary rotation costs one afternoon, an
    unreported one costs a live credential.
    """
    # C70: `(paths, checked)`, because four different situations used to
    # return the same `()` and only two of them meant "none".
    #
    #     no Working Copy directory     nothing to check      -> checked
    #     no `.git` yet                 no history exists     -> checked
    #     git could not be run          **could not check**   -> NOT checked
    #     git answered non-zero         **could not check**   -> NOT checked
    #
    # The last two are the ones that mattered. Measured on a repository that
    # really had committed a private key: the report says `('id_rsa',)`
    # normally and `()` the moment git cannot answer — byte-identical to a
    # history that was read and found clean.
    #
    # This function's own docstring above names that outcome: "the warning
    # disappeared" is "the most dangerous possible answer". It was producing
    # it. And the paragraph below the list states the principle the fix
    # follows — `_would_reach_the_commit()` over-reports when git cannot
    # answer, because an unnecessary rotation costs an afternoon and an
    # unreported one costs a live credential. Over-reporting is not available
    # here (without git there is no history to enumerate), so the honest
    # answer is to say the check did not happen.
    #
    # Splitting "no `.git`" out first is what keeps this from becoming a
    # permanent caveat: a Working Copy that has never been initialised has no
    # history to hold a secret, exactly as an absent directory does not. Same
    # distinction C68 drew with `FileNotFoundError`.
    if not working_copy.is_dir():
        return (), True
    if not (working_copy / ".git").exists():
        return (), True
    try:
        result = subprocess.run(
            ["git", "rev-list", "--all", "--objects"],
            cwd=working_copy,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return (), False
    if result.returncode != 0:
        return (), False
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        # `<oid>` for commits, `<oid> <path>` for everything with a name.
        _, _, path = line.partition(" ")
        path = path.strip()
        if not path:
            continue
        name = PurePosixPath(path).name
        if _looks_like_secret(name) or _looks_like_secret(name.lower()):
            seen.add(path)
    # An empty `stdout` with a zero exit is a repository with no commits yet:
    # read, and genuinely nothing in it.
    return tuple(sorted(seen)), True


def _secret_names_the_gate_will_not_recognise(root: Path) -> tuple[str, ...]:
    """Files the Backup gate's own name list would match — except for case.

    `_looks_like_secret()` compares names exactly. Windows compares them
    case-insensitively, so on the platform docs/11 deploys to, a file named
    `ID_RSA` **is** a file named `id_rsa` and the gate does not think so.

    Measured, eight files written into a `daily/` directory (in scope,
    docs/08 §26):

        on disk   .env  CREDENTIALS.JSON  ID_RSA  server.PEM
        flagged   daily//.env

    `.env`, `.ENV` and `.Env` collapsed into one file, which is the point —
    the filesystem already treats the name as case-insensitive. The other
    three are ordinary distinct files carrying the exact names docs/08 §29
    asks this gate to catch, and all three passed it: `run_once()` returns
    BACKUP_SUCCESS and `git add -A` commits them.

    This is BUG-55's root (a case-sensitive comparison against a
    case-insensitive filesystem) at a second location. BUG-55 is about which
    directories get *backed up*; this is about which files get *blocked*.

    **Detection only, deliberately.** Case-folding the comparison would give
    the gate a new way to return BACKUP_FAILED, which is precisely E-15's
    documented harm — a false positive there stops Company History reaching
    the remote at all, and every candidate fix for that pair is recorded as
    needing a decision. So this reports and changes nothing, the same
    treatment `_misnamed_scope_directories()` gives BUG-55.

    The name list is imported from the gate rather than restated, for the
    reason the import block gives: a second opinion about what a secret looks
    like is how the two drift apart.

    `.git/` is skipped, for the reason `_would_reach_the_commit()`'s own
    caller already gives: git never lists anything inside its own storage, so
    a secret-shaped name there is not on its way anywhere, and it is 93% of
    the walk on this machine's Working Copy today (90 of 97 files) — a share
    that only grows as backup history accumulates.
    """
    if not root.is_dir():
        return ()
    found: list[str] = []
    for path in root.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        name = path.name
        if _looks_like_secret(name):
            continue  # the gate already sees this one
        if _looks_like_secret(name.lower()):
            found.append(str(path.relative_to(root)))
    return tuple(sorted(found))


def _authored(text: str) -> str:
    """`one_line()` and `redact()` for a value a person wrote on another Desktop.

    `main()`'s ATTENTION sink applies `one_line()` to every message and
    deliberately does NOT apply `redact()`, for the reason recorded there:
    almost every message is built from paths, ids and counts, and
    over-redacting a path an operator has to go and open costs more than it
    protects. That reasoning held one assumption -- that an **id** is not
    content -- and it is false.

    `event_id` and `project_id` are ordinary strings that `validate_event()`
    only type-checks. On the Agent's own door a Signal may not set `event_id`
    at all (`FORBIDDEN_SIGNAL_FIELDS`) and its content is scanned, but an
    Event arriving from another Desktop sets both fields itself and is never
    scanned -- the same asymmetry `_secret_shaped_event_content()` measures.
    A regression test caught this the honest way: it asserted the new secret
    report does not print the string it found, and the *orphan* line two
    blocks above printed the same Event's id raw.

    So every site that prints an Event-authored identifier calls this, and
    the sink stays as it is: a message that carries authored text redacts at
    the place it is produced, exactly as `_print_control_tower()`'s blocker
    line already did.

    `bounded()` for the same reason `oplog.append_line()` applies it: nothing
    this system reports is allowed to be as long as whatever produced it.
    Nothing bounds `blocker`, `summary` or `project_id` — docs/02 gives them
    no maximum and `validate_event()` only type-checks — and C71 bounded the
    **number** of RISKS lines in ATTENTION without bounding the **length** of
    any one of them. Five is a small number times an unbounded one.

    Measured on three blocked Projects carrying a 100,000-character
    `blocker`: one ATTENTION line of 100,176 characters, three of them, in
    the block whose entire job is telling an operator what to do now — and in
    the log a scheduled run redirects to disk. `MAX_LOG_ERROR` (600) is the
    cap this project already chose for exactly this shape, and the truncation
    is visible, so the line still names the Project and the evidence file
    that holds the whole text.
    """
    return bounded(redact(one_line(text)))


def _one_per_event(items, key):
    """`items` collapsed to the first entry per `key`, order preserved.

    **Why this exists (C77).** Two of the ATTENTION lines in this view are
    built by walking `processed/` file by file, and `processed/` can hold two
    files for one `event_id` -- that is not a corruption case, it is the
    ordinary state this view already reports as `중복 파일` in the COMPANY and
    CONTROL TOWER blocks, and the deployment runtime is in it right now.

    Both lines then show the first five. Measured, with one duplicated Event
    (six copies) and five genuinely different ones:

        orphan line     "Event 11건" and the same id five times;
                        EVT-REALLY-LOST-0..4 appear nowhere on the page
        secret line     "11건" and the same id five times;
                        five other leaked credentials named nowhere

    Both lines end in an instruction about a *thing*, not a file -- "사람이
    확인해야 한다" for a lost Event, "자격증명을 교체해야 한다" for a leaked
    credential. Repeating one and hiding four is the exact skim-training
    failure C26 named, with the added cost that what is hidden is the part
    nobody knows about.

    The counts are folded the same way and for the same reason: C51 already
    settled this for the two blocks above ("위 숫자는 Event당 한 번만
    센다"), and this is that decision applied to the third and fourth
    readers of the same directory rather than a new one.

    Nothing is silently collapsed: each caller says how many files stood
    behind the folded number when the two differ.
    """
    seen = set()
    kept = []
    for item in items:
        identity = key(item)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(item)
    return kept


# Which Event fields carry text a person wrote, and therefore could carry a
# credential. Not a judgement call left loose: `EveryEventTextFieldIsScanned`
# compares this tuple against `Event.to_dict()`, so a string field added to
# the schema is either scanned here or fails the suite. `evidence` is handled
# separately below because it is a list, not a string.
_EVENT_TEXT_FIELDS = ("event_id", "project_id", "milestone", "summary", "blocker")


#: The four Decision Context fields a person fills in through
#: `src/review_cli.py`. Named here rather than imported so this stays a
#: read-only view — `HistoryCandidate` owns them, and
#: `test_the_field_roster_is_the_one_review_cli_offers` checks the two agree.
_DECISION_CONTEXT_FIELDS = (
    "decision_context",
    "expected_outcome",
    "actual_outcome",
    "lessons_learned",
)


def _secret_shaped_decision_context(
    keep_dir: Path,
    review_dir: Path,
) -> tuple[tuple[tuple[str, str], ...], int]:
    """`(history_id, fields)` for Candidates whose typed prose is secret-shaped.

    **The third door, and the only one with neither a refusal nor a report
    (C125).** `_secret_shaped_event_content()` below names two ways text
    reaches Company History and what happens at each:

        Signal typed on this machine    `find_secret_material()` REFUSES it
        Event from another Desktop      nothing reads the content; that
                                        detector reports it

    Decision Context is a third, and it is the one a person writes **as
    prose, deliberately**, through `review_cli.py`. "토큰을 교체했다. 새 값은
    …" is an ordinary sentence to write in a lessons-learned field.

    Measured end to end in a temp tree, with a token typed into
    `decision_context`:

        submit_review()                 accepted, nothing scanned
        candidate on disk               holds the token
        _secret_shaped_event_content()  blind — it reads `processed/`
                                        Events, and this is a Candidate
        Daily History markdown          **token present**, SECRET_RE matches

    So the material is recognisable and nothing was looking. Company History
    is synced to the Working Copy, committed, pushed to the backup remote
    (`scan_for_secrets()` compares filenames, never content) and rendered
    into the Notion page.

    **This reports; it refuses nothing** — the same posture, and for the same
    reason, as the Event detector: refusing here would be `review_cli.py`
    discarding a paragraph the person just typed, and that is a decision
    recorded in BACKLOG rather than taken in a status view. What
    `review_cli.py` does now is *warn at the moment of typing*, which costs
    nothing and refuses nothing.

    Reads both `keep/` and `review/`: a REVIEW candidate is equally on disk
    and equally headed for Company History once someone decides on it.

    The matched text is never returned, for the reason
    `find_secret_material()` states: a report of a leaked credential must not
    become the second copy of it.

    Returns `(rows, unchecked)`. The second number is Candidates this could
    not read, and it is **counted rather than skipped** — a file nobody
    opened is not a file with no secret in it, which is the silent-loss
    direction this project keeps removing. `ASilentlyDroppedEntryIsAROSTER…`
    is the gate that said so when the first draft dropped them: the
    `count += 1` spelling is what its classifier reads as "recorded" (C88).
    """
    found: list[tuple[str, str]] = []
    unchecked = 0
    for directory in (keep_dir, review_dir):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            # `FileHistoryRepository.save()` stages into these directories,
            # so a killed run leaves a `.tmp-…json`. It is residue rather
            # than a Candidate, and `_incomplete_writes()` already reports
            # it — the same skip `_split_reviewed()` makes twenty lines up.
            if is_incomplete_write(path.name):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                # `RecursionError` too, and it is not defensive padding:
                # `json.loads` raises it rather than `ValueError` on deeply
                # nested input, and a Candidate file is untrusted the moment
                # a person or another tool can write one. Same triple as
                # `_split_reviewed()` twenty lines up.
                unchecked += 1
                continue
            if not isinstance(data, dict):
                unchecked += 1
                continue
            fields = [
                name
                for name in _DECISION_CONTEXT_FIELDS
                if isinstance(data.get(name), str) and SECRET_RE.search(data[name])
            ]
            if fields:
                found.append((str(data.get("history_id") or path.name), "/".join(fields)))
    return tuple(sorted(found)), unchecked


def _secret_shaped_event_content(
    processed_dir: Path,
) -> tuple[tuple[str, str, str, str], ...]:
    """`(event_id, source, filename, fields)` for Events whose text is secret-shaped.

    Every other secret report in this file is about a **filename**. This one
    is about **content**, and it exists because the project's strongest
    secret guard is applied at exactly one of the two doors an Event can come
    through.

        Signal written on this machine   `find_secret_material()` scans the
                                         whole payload and `parse_signal()`
                                         REFUSES it. Nothing is sent.
        Event arriving over OneDrive     `validate_event()` type-checks
        from another Desktop, or a       fields and cross-checks event_type
        file written by hand             against status. It reads no content
                                         at all.

    Measured end to end, an Event carrying a secret-shaped `summary` that did
    not pass through this machine's Agent:

        validate_event()          no errors
        Daily History written     yes, with the string in it
        pushed to the remote      yes (`git show origin/main:daily/...`)
        scan_for_secrets()        () -- it compares names, never content
        oplog.redact() on a log   [REDACTED]

    So the one place the string is scrubbed is the log, and the one place it
    is kept forever is Company History and the backup remote.

    **This reports; it refuses nothing.** Making `validate_event()` reject it
    would send the Event to `rejected/` and delete that work from Company
    History, which is the same trade recorded for the source/role mismatch --
    a policy decision, not a code cleanup, and SKIPped for a decision.

    Cost, measured over 2,000 Events: 90 ms to read, 25 ms to scan. The scan
    is one pass of the combined `SECRET_RE` over the Event's own strings;
    running `find_secret_material()` per Event instead costs 193 ms for the
    same answer, because it applies seven uncompiled patterns to every string
    separately. The pattern *names* are what that function returns and they
    are regexes -- an operator needs the field name, which this gives.

    The matched text is never returned, for `find_secret_material()`'s stated
    reason: a report of a leaked credential must not become the second copy
    of it. Callers still redact what they print, because `event_id` and
    `project_id` are themselves scanned fields.
    """
    pairs, _ = read_events(processed_dir)
    found: list[tuple[str, str, str, str]] = []
    for event, filename in pairs:
        texts = {name: (getattr(event, name, None) or "") for name in _EVENT_TEXT_FIELDS}
        joined = "\n".join(texts.values())
        if event.evidence:
            joined = joined + "\n" + "\n".join(event.evidence)
        if not SECRET_RE.search(joined):
            continue
        fields = [name for name, text in texts.items() if text and SECRET_RE.search(text)]
        if any(SECRET_RE.search(item) for item in event.evidence):
            fields.append("evidence")
        found.append((event.event_id, event.source, filename, "/".join(fields)))
    return tuple(found)


def _is_junction(path: Path) -> bool:
    """Whether `path` is an NTFS junction, on every interpreter this runs on.

    **C70: this detector was blind on the machine it runs on.**
    `os.path.isjunction()` is Python 3.12+, and `_junctions_in_scope()` used
    to return `(), 0` when it was absent — "0 found", with no caveat, which
    is byte-for-byte what a clean machine prints. The deployment runtime was
    Python 3.9.7 when C70 measured this, so the one detector that reports the
    exposure documented above had never once fired here. Measured before the
    fix, a real junction under `daily/` pointing at a tree that is not Company
    History:

        _junctions_in_scope(local_master)  ->  found=(), skipped=0
        the screen                         ->  "junction 노출 : 0건 발견"

    The old comment said "on Python < 3.12 there is no way to ask". That was
    not true. `os.lstat()` has carried `st_reparse_tag` since 3.8 and
    `stat.IO_REPARSE_TAG_MOUNT_POINT` has existed just as long; together they
    are exactly what CPython 3.12 implements `isjunction()` with. Measured on
    3.9.7:

        junction          st_reparse_tag 0xa0000003  (MOUNT_POINT)  -> True
        directory symlink st_reparse_tag 0xa000000c  (SYMLINK)      -> False
        file symlink      st_reparse_tag 0xa000000c  (SYMLINK)      -> False
        ordinary dir/file no tag                                    -> False

    **The tag, not the reparse-point bit.** `tests/test_runner_failure_paths.
    _is_junction()` tests the bit alone, which is right for a helper checking
    a junction it just created — but the bit is set for symlinks too, and a
    detector that called a symlink a junction would report an exposure the
    backup does not have: `backup/working_copy._relative_files()` already
    excludes symlinks. Over-reporting here is not the safe direction, it is
    the direction that trains an operator to skim the section (C26).

    Non-Windows interpreters have neither attribute and get `False`, which is
    correct rather than a fallback: a junction is an NTFS construct.

    **C76: the deployment runtime moved to 3.13.14 (BACKLOG D), so the
    stdlib branch below is now the one that runs here** and the detector is
    live for the first time. Nothing in this function changed -- C70 wrote
    both halves precisely so the move would need no edit. The reparse-tag
    fallback stays and stays tested: `test_an_older_interpreter_still_sees_
    the_junction` injects `isjunction = None` to reach it, which is what
    keeps it from rotting on a machine that no longer takes that path.
    """
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        # Kept as the primary so a 3.12+ machine answers with the stdlib's
        # own implementation rather than this project's copy of it.
        return isjunction(path)
    tag = getattr(path.lstat(), "st_reparse_tag", None)
    if tag is None:
        return False
    return tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", object())


def _projects_sharing_one_history_heading(rollup) -> tuple[tuple[str, ...], ...]:
    """Groups of distinct `project_id`s that Company History renders under
    one heading.

    `daily/markdown._render_item_block()` renders each project section as a
    `###` heading built by `_display_project_name(project_id)`, and that
    transform is `.replace("_", " ")` followed by `.title()`. It is not
    injective: `PRJ_ALPHA`, `prj_alpha` and `Prj_Alpha` are three
    different `project_id`s and one heading.

    **Measured end to end (C90).** Three Events, one per spelling, one day:

        Events written                3 distinct project_id
        Control Tower / PROJECTS      3 projects
        Company History               3 sections, all `### Prj Alpha`
        Monthly parser                3 items, **1 distinct project**

    No Event is lost — all three are in the Daily file with their own
    `Event ID:` line. What diverges is a **number the COO reads**: the
    Control Tower says three projects moved and Monthly History says one,
    about the same month.

    `project_id` is typed by a person on every Signal, so this is reachable
    in a way E-22's `event_id` collision is not: the Agent derives
    `event_id` as a lowercase uuid5 and a Signal may not set it at all,
    while `project_id` has no such narrowing.

    **Reported, never repaired.** Making the transform injective rewrites
    every heading in existing Company History, and teaching Monthly to key
    on something else means changing what the Daily document carries —
    docs/06's format. Both are decisions (BACKLOG). This says only that two
    projects are sharing a heading, which is the one thing an operator can
    act on: pick one spelling.
    """
    by_heading: dict[str, set] = {}
    for project in getattr(rollup, "projects", ()):  # pragma: no branch
        project_id = getattr(project, "project_id", None)
        if not isinstance(project_id, str):
            continue
        by_heading.setdefault(_display_project_name(project_id), set()).add(
            project_id
        )
    return tuple(
        tuple(sorted(ids))
        for _heading, ids in sorted(by_heading.items())
        if len(ids) > 1
    )


def _junctions_in_scope(local_master: Path) -> tuple[tuple[tuple[str, str], ...], int]:
    """`(path, target)` for directory junctions inside the backup scope.

    A-19/BUG-57 states the exposure; this states that it is happening. The
    two are different, and only the second needs no decision.

    Re-measured (C29) through the real sync, with a junction under `daily/`
    pointing outside Local Master:

        Path.is_symlink()            False   <- the sync's guard misses it
        _is_junction()               True    <- the reparse tag knows exactly
        sync_to_working_copy() added daily/linked/notes.md,
                                     daily/linked/private.md
        scan_for_secrets(master)     ()      <- nothing flagged

    So content that does not live under Local Master is copied into the
    Working Copy and pushed, and the two existing guards both stay quiet:
    `_relative_files()` excludes symlinks and a junction is not one, and the
    secret scan only reacts to secret-*shaped names*. Ordinary files pass
    silently.

    **This reports; it does not refuse.** Whether a redirected History
    directory is a legitimate layout is A-19's deployment decision — the
    BACKLOG records that refusing it was implemented once and reverted for
    exactly that reason, because redirecting `daily/` to another drive for
    disk space is a real use. Nothing here changes what Backup copies.

    Printed as a fact rather than raised as ATTENTION, following C26's rule:
    on a deliberately redirected deployment no operator action would clear
    it, and a permanent ATTENTION entry trains people to skim the section.
    What the line gives is the one thing that was missing — that the
    redirect exists, and where it points, so a junction nobody meant to
    create is visible.

    Answered on **every** interpreter this runs on — see `_is_junction()`.
    """
    if not local_master.is_dir():
        # An absent subject, not a failed read: there is no Company History
        # here to be redirected, so nothing was skipped.
        return (), 0
    found: list[tuple[str, str]] = []
    skipped = 0
    for name in sorted(_ALLOWED_TOP_LEVEL_DIRS):
        scoped = local_master / name
        if not scoped.exists():
            continue
        candidates = [scoped] + (
            sorted(p for p in scoped.rglob("*")) if scoped.is_dir() else []
        )
        for path in candidates:
            try:
                if not _is_junction(path):
                    continue
                target = os.path.realpath(path)
            except OSError:
                # C68: counted. This is the one detector on this screen whose
                # subject is an *exposure* — a junction is how Company History
                # that lives outside Local Master gets committed and pushed
                # (A-19/BUG-57). An entry dropped here makes the exposure list
                # shorter, and a shorter exposure list is indistinguishable
                # from a safer machine.
                skipped += 1
                continue
            found.append((str(path.relative_to(local_master)), target))
    return tuple(found), skipped


def _misnamed_scope_directories(
    local_master: Path,
) -> "tuple[tuple[tuple[str, str], ...], bool]":
    """`(actual, expected)` for directories that differ from an in-scope name
    only by case.

    Turns BUG-55 from "something is wrong" into "rename this directory".

    `working_copy._is_in_scope()` compares the first path component against
    `_ALLOWED_TOP_LEVEL_DIRS` exactly, while docs/11's deployment steps have a
    human create those directories and Windows treats `Daily` and `daily` as
    one. The result is a directory that every other part of the system —
    including this view's own `daily 파일` count, which uses `glob()` — reads
    happily, and that Backup silently never copies.

    C27 made the consequence visible ("Company History that never reached the
    remote"). What it could not say is *why*, so an operator had to notice the
    capital letter in a filename and know what it meant. This names the fix.

    The allowed set is imported from the module that enforces it rather than
    restated, so a third scope directory would be diagnosed without touching
    this function.

    Detection only. Case-folding the comparison in `_is_in_scope()` is BUG-55's
    own open decision — it changes which files Backup covers — and renaming a
    directory under Local Master is an operator action this code must not take
    (docs/08 §13/§46: Company History is never rewritten by the program).
    """
    # C70: `(found, checked)`. This is a detector, and answering "none" when
    # it could not look is the one failure a detector must not have. Measured
    # on a Local Master holding `Monthly/` beside `daily/`:
    #
    #     listable     (('Monthly', 'monthly'),)
    #     unlistable   ()                        <- identical to a clean tree
    #
    # What goes missing with it is not cosmetic. The line this feeds says the
    # directory "is never backed up and Backup keeps reporting SUCCESS"
    # (BUG-55) — losing the detection leaves exactly that state unannounced.
    #
    # An absent Local Master stays `checked`: there is no tree to be
    # misnamed, so a machine where Backup was never configured grows no
    # caveat. Same line C68 drew, and the same one part 3 drew with `.git`.
    if not local_master.is_dir():
        return (), True
    found: list[tuple[str, str]] = []
    try:
        entries = sorted(local_master.iterdir())
    except OSError:
        return (), False
    for entry in entries:
        if not entry.is_dir() or entry.name in _ALLOWED_TOP_LEVEL_DIRS:
            continue
        folded = entry.name.casefold()
        for allowed in sorted(_ALLOWED_TOP_LEVEL_DIRS):
            if folded == allowed.casefold():
                found.append((entry.name, allowed))
                break
    return tuple(found), True


def _split_reviewed(review_dir: Path) -> tuple[int, int]:
    """(not yet reviewed, already reviewed) in `review/`.

    "Reviewed" means a human has written at least one Decision Context
    field — exactly what `RepositoryHistoryReviewer.submit_review()` does,
    read back from the stored candidate rather than tracked separately.

    The split exists so the alert can clear. A candidate nobody has looked
    at is work waiting for a person, and doing that work removes it from the
    count. A candidate that HAS been reviewed is still in `review/` — there
    is no promotion path (BACKLOG E-20) — and alerting on it would stand
    forever no matter what the operator did.

    Unreadable files count as not-yet-reviewed: they need a person either
    way, and a diagnostic must answer when part of the evidence is damaged.
    `FileHistoryRepository.list()` is deliberately not used — it raises on
    the first unreadable candidate (BUG-38), which would take the whole
    status view down.
    """
    if not review_dir.is_dir():
        return (0, 0)
    waiting = reviewed = 0
    for path in sorted(review_dir.glob("*.json")):
        if is_incomplete_write(path.name):
            # `FileHistoryRepository.save()` stages into this directory, so a
            # killed run leaves a `.tmp-…json` here. Counting it as "not yet
            # reviewed" would put a person-shaped alert on a file no person
            # can review — the alert-that-cannot-clear this function's own
            # docstring exists to avoid.
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            waiting += 1
            continue
        if isinstance(data, dict) and any(
            data.get(field) is not None
            for field in (
                "decision_context",
                "expected_outcome",
                "actual_outcome",
                "lessons_learned",
            )
        ):
            reviewed += 1
        else:
            waiting += 1
    return (waiting, reviewed)


def _runner_lock_path() -> Path:
    """Resolved per call, not at import: `RUNTIME_DIR` is rebound by tests
    (and would be by any future relocation), and a path frozen at import
    would keep pointing at the old one."""
    return RUNTIME_DIR / "locks" / "company_ops.lock"


def _agent_start_date() -> date | None:
    raw = os.environ.get("COMPANY_OPS_AGENT_START_DATE")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _history_start_date() -> date | None:
    """`COMPANY_OPS_HISTORY_START_DATE`, or None when unset/unparseable.

    Byte-for-byte the shape of `_agent_start_date()` directly above, and that
    is the point. BACKLOG recorded this detection as blocked on a decision —
    *"설정이 없을 때 무엇을 보고할지가 또 하나의 판단"* — but this module had
    already made that decision twice, for `COMPANY_OPS_AGENT_START_DATE` and
    `COMPANY_OPS_AGENT_SYNC_FOLDER`: read it, and when it is not there say so
    and skip the computation rather than guessing or alerting.

    Applying an answer this file already gives is not a new policy. What was
    missing was noticing that the answer existed.
    """
    raw = os.environ.get("COMPANY_OPS_HISTORY_START_DATE")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class StoredCandidate(NamedTuple):
    """The part of a stored KEEP Candidate the status view needs.

    A bare 3-tuple until C33, unpacked positionally at every call site.
    Adding `reviewed` made it a 4-tuple and broke all of them at once — the
    second time the shape had grown — so it is named now. The next check
    that needs a fifth fact adds a field and touches nothing else, which is
    the property the bare tuple did not have.

    `NamedTuple`, deliberately, and **not** `@dataclass`. This module begins
    with `from __future__ import annotations`, so every annotation is a
    string; `dataclasses` resolves those against `sys.modules[cls.__module__]`
    while checking for `KW_ONLY`, and every test helper here loads this file
    through `importlib.util.spec_from_file_location(...)` + `exec_module()`
    **without registering the module in `sys.modules` first**. Under that
    loader the lookup yields None and the decorator dies with
    `AttributeError: 'NoneType' object has no attribute '__dict__'`.
    Measured: adding the first dataclass to this file failed 293 tests
    across `test_observability.py` and `test_history_review.py`, none of
    them about candidates. `NamedTuple` does no such resolution.

    Not `HistoryCandidate`: that one is the full domain object, and
    `FileHistoryRepository.list()` raises on the first unreadable file
    (BUG-38), which would take the whole status view down. This is
    deliberately the subset three read-only checks can agree on, parsed
    defensively.
    """

    stem: str
    event_id: str
    when: date
    # `(label, value)` for the Decision Context fields a human filled in —
    # empty for the overwhelming majority of Candidates, which nobody has
    # reviewed.
    reviewed: tuple[tuple[str, str], ...] = ()


def _read_keep_candidates(
    keep_dir: Path,
) -> tuple[tuple[StoredCandidate, ...], tuple[str, ...]]:
    """A `StoredCandidate` for every readable KEEP Candidate, read once.

    Three checks need these files — `_candidates_before()` (BUG-46),
    `_kept_but_not_rendered()` (E-17) and `_reviewed_but_not_rendered()`
    (C33 §3) — and reading them twice was measured,
    cold, at 24.3 s for 5,000 Candidates. Warm it was 0.28 s, an 87x
    difference: the cost is the file *open*, exactly as this repository's
    `_READ_WORKERS` comments have said since C21, and exactly the bias C27
    caught in the old thread-pool figures. So both numbers here are cold,
    each on its own freshly written tree.

    Two minimal changes, both reusing what is already here rather than new
    machinery:

      * read once and share, instead of once per check;
      * the same `ThreadPoolExecutor` + `_READ_WORKERS` idiom that
        `app/desktop_activity.py`, `history/reconciliation.py` and
        `agent/delivery.py` already use for the identical reason.

    Measured cold at 5,000 Candidates / 730 Daily files:

        both checks, serial, reading separately     24.3 s
        both checks, shared threaded read            5.9 s

    `FileHistoryRepository.list()` is still not used: it raises on the first
    unreadable Candidate (BUG-38), which would take the whole status view
    down. A Candidate that cannot be parsed is dropped here rather than
    guessed at — neither check can claim a fact about a file it could not
    read.
    """
    if not keep_dir.is_dir():
        return (), ()
    paths = [
        path
        for path in sorted(keep_dir.glob("*.json"))
        if not is_incomplete_write(path.name)
    ]
    if not paths:
        return (), ()

    def _read(path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return None
        if not isinstance(data, dict):
            return None
        # The repository's own predicate, not a second opinion (C28's rule).
        # This used to check `timestamp` and `event_id` and nothing else, so a
        # Candidate whose `summary` or `project_id` had the wrong type counted
        # as perfectly readable here — and stopped every date in the pipeline.
        # Measured (C44), one such file beside one ordinary Candidate:
        #
        #     Runner        daily FAILED, 0 Daily files, exit 2, permanently
        #     this view     "Candidate 정합성 : OK", nothing else
        #
        # `candidate_errors()` is what `FileHistoryRepository` refuses on, so
        # the two now answer the same question the same way and the ATTENTION
        # line below fires for exactly the files that stop the Scheduler.
        if candidate_errors(data):
            return None
        try:
            when = businessdate.business_date(datetime.fromisoformat(data["timestamp"]))
        except (TypeError, ValueError):
            return None
        event_id = data["event_id"]
        # Fourth element: the Decision Context a human has filled in, as
        # `(label, value)` pairs for the fields that are actually populated.
        # Carried by this reader rather than by a second pass for the reason
        # this function exists at all — the cost is the file OPEN, measured
        # cold at 24.3 s for 5,000 Candidates read twice against 5.9 s read
        # once. A third check reading them a third time would undo that.
        reviewed = tuple(
            (label, value)
            for field, label in _REVIEW_FIELDS
            if isinstance(value := data.get(field), str) and value
        )
        return StoredCandidate(path.stem, event_id, when, reviewed)

    with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
        # `map` preserves input order, so the sorted-filename ordering of the
        # results is identical to the serial version's.
        results = list(pool.map(_read, paths))
    parsed = tuple(item for item in results if item is not None)
    unreadable = tuple(
        path.name for path, item in zip(paths, results) if item is None
    )
    return parsed, unreadable


def _candidates_before(
    candidates: tuple[StoredCandidate, ...], start: date
) -> tuple[str, ...]:
    """KEEP Candidates dated before `start` — stored, and unrenderable.

    BUG-46's permanent half. C22 measured that the description was wider than
    the defect: a Candidate dated in the *future* is only delayed (the
    Scheduler renders it once that date is yesterday), but one dated before
    `history_start_date` is permanent, because the Scheduler never goes
    earlier than that date. `find_orphaned_events()` reports clean for these
    — correctly, the Candidate exists — so nothing anywhere says the Event
    will never appear in Company History.

    Reachable through ordinary misconfiguration rather than corruption: a
    Desktop whose `COMPANY_OPS_AGENT_START_DATE` is earlier than Desktop 4's
    `COMPANY_OPS_HISTORY_START_DATE` delivers Events for dates Desktop 4 will
    never render, and every step reports success.

    `FileHistoryRepository.list()` is deliberately not used, for the reason
    `_split_reviewed()` gives: it raises on the first unreadable Candidate
    (BUG-38), which would take the whole status view down. A Candidate whose
    date cannot be read is skipped rather than guessed at — this function
    reports a fact, and an unreadable file is not evidence of one.
    """
    return tuple(
        f"{item.stem} ({item.when.isoformat()})"
        for item in candidates
        if item.when < start
    )


def _label_lines(lines: list[str]) -> set[str]:
    """The stripped lines of a rendered Daily that are the renderer's labels.

    Both detectors below ask the same question — "did the renderer write this
    exact line" — and both used to ask it of the WHOLE document minus the
    item-block summaries. Two things are wrong with that, and they are one
    root:

        `## Summary`   `render_daily_markdown()` repeats every candidate's
                       summary there RAW. A summary of `- Event ID: EVT-B`
                       lands as a bare line identical to EVT-B's own label,
                       and `summary_line_indices()` cannot reach it — that
                       rule walks `### ` blocks and the Summary section has
                       none.
        `## Evidence`  `- <event_id>: <text>`, which spells a label exactly
                       when an `event_id` is the literal `Event ID`.

    Measured on `_kept_but_not_rendered()`, EVT-A rendered with three
    different summaries and EVT-B genuinely absent from the file:

        'Shipped it.'         ('EVT-B (2026-08-05)',)
        'Event ID: EVT-B'     ('EVT-B (2026-08-05)',)   <- the C30 fix
        '- Event ID: EVT-B'   ()                        <- silenced

    The third row is E-17's data loss going unreported again, switched off by
    one ordinary Candidate's summary — the very failure the long comment in
    `_kept_but_not_rendered()` says it closed, arriving through the section
    one layer up. `daily/late_events.existing_event_ids()` lost a real late
    Event to the same line; that is where the shared rule now lives.

    Confining the set to `### ` item blocks is the whole fix: a label is only
    ever written inside one (`daily/markdown._render_item_block()`), so
    nothing genuinely rendered leaves the set and nothing outside a block can
    join it.
    """
    summaries = summary_line_indices(lines)
    return {
        lines[index].strip()
        for start, end in item_block_bounds(lines)
        for index in range(start, end)
        if index not in summaries
    }


def _kept_but_not_rendered(
    candidates: tuple[StoredCandidate, ...], daily_dir: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """KEEP Candidates whose date **has** a Daily file that does not carry them.

    E-17's data loss, made visible. Its own measurement ends with the
    sentence that matters: *"파일을 고쳐도 아무 일도 일어나지 않고, 모든 지표가
    정상을 보고하는 채로 Company History에 Event 하나가 비어 있다."* Nothing
    reported it, so nobody could act on it.

    **The verdict is decidable between runs, which is why this needs no
    policy decision.** Step 5 writes Candidates, step 6 renders the dates the
    Scheduler closed, and step 6.5 merges anything landing on an
    already-closed date — all in one run. So once a run has finished, a
    Candidate whose Daily file *exists* and does not contain its `event_id`
    was not merged, and nothing will retry it: step 6.5's target dates are
    only the ones this run collected (`kept_dates`), so a later run has no
    reason to look at that date again.

    A Candidate whose Daily file does **not** exist is excluded — it is
    simply not rendered yet (the Scheduler window), or it predates
    `history_start_date`, which `_candidates_before()` reports on its own
    terms.

    The one window where this can read false is the same one
    `find_orphaned_events()` documents: a Runner part-way between step 5 and
    step 6.5. Handled the same way — the caller appends a "Runner is running"
    note rather than suppressing the list, because a real loss hidden behind
    "probably just running" is far worse than a caveat.

    Verified on this machine's own runtime before being written: 13 of 14
    stored Candidates were present in their Daily file, and the fourteenth
    was genuinely absent — E-17's shape, sitting there unreported.

    Daily files are read once per date rather than once per Candidate, and
    matching is on `event_id` because that is what `daily/markdown.py` writes
    (`- Event ID: {candidate.event_id}`).
    """
    if not daily_dir.is_dir():
        # An absent subject, not a failed read (C68's asymmetry).
        return (), ()
    by_date: dict[str, list[str]] = {}
    for item in candidates:
        by_date.setdefault(item.when.isoformat(), []).append(item.event_id)

    stranded: list[str] = []
    unreadable: list[str] = []
    for when, event_ids in sorted(by_date.items()):
        rendered = daily_dir / f"{when}.md"
        if not rendered.is_file():
            continue
        try:
            text = rendered.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # C92: named, not swallowed. Every Candidate of this date
            # would otherwise be treated as rendered, so the stranded
            # list gets shorter by exactly the ones nobody could check
            # -- and a shorter list of losses is what a healthier
            # machine looks like. C68 built the answer for this shape;
            # C91 applied it to the Monthly lag check; this is the
            # third of the three the AST sweep found.
            unreadable.append(when)
            continue
        # Whole lines, not a substring search. `E-1` is a substring of the
        # line rendered for `E-10`, so a substring test reported a genuinely
        # stranded `E-1` as fine — measured, with ordinary sequential ids and
        # no crafted input (C30).
        #
        # The comparison is built the way the renderer builds it — take the
        # id, make the line — rather than by taking the line apart. C30 did
        # the latter (`startswith(prefix)`, then slice the prefix off), and
        # the prefix it had to slice ends in a space, so a rendered
        # `- Event ID: ` (an `event_id` of `""`, which `validate_event()`
        # accepts — BACKLOG A-15) did not start with it. The Candidate was in
        # its Daily file and this reported it as permanently lost, with a
        # message telling the operator no run will ever fix it. Constructing
        # the line has no such edge: whatever the renderer wrote for an id,
        # this writes the same thing.
        #
        # Summary lines excluded for the same reason, one layer up. The
        # renderer writes a summary raw as its block's first bullet, so a
        # Candidate whose summary reads `Event ID: EVT-B` renders a line
        # identical to EVT-B's own. Measured — EVT-A rendered with that
        # summary, EVT-B genuinely absent from the file:
        #
        #     summary `Event ID: EVT-B`   ->  ()
        #     summary `Shipped it.`       ->  ('EVT-B (2026-08-05)',)
        #
        # This function exists to catch exactly that loss (E-17's shape),
        # and one ordinary summary switched it off for the Candidate it
        # named. Excluding summaries cannot go the other way: a summary is
        # never the renderer's label line, so nothing genuinely rendered is
        # removed from the set.
        #
        # Measured, whole function, before -> after:
        #
        #      14 days x  5 Candidates    0.84 ->  0.99 ms
        #     365 days x 10 Candidates   24.09 -> 30.05 ms
        #
        # The file read still dominates; the second row is a year of stored
        # Candidates, well past what this repository holds (13 when the
        # function was written).
        lines = text.splitlines()
        rendered_lines = _label_lines(lines)
        stranded.extend(
            f"{_authored(event_id)} ({when})"
            for event_id in event_ids
            if f"{_EVENT_ID_LINE_PREFIX}{event_id}".strip() not in rendered_lines
        )
    return tuple(stranded), tuple(unreadable)


def _reviewed_but_not_rendered(
    candidates: tuple[StoredCandidate, ...], daily_dir: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Decision Context a human wrote that Company History does not carry.

    C33 §3, and unlike its two siblings this one loses **human-authored**
    content, which is the most expensive kind this pipeline handles.

    The capability is fully built and, for a KEEP Candidate, unreachable.
    `review_cli.py` prompts for the four fields, `history.review` stores
    them, `daily/markdown.py` renders each one when present — and nothing
    connects the middle to the end:

        step 5   writes the Candidate
        step 6   renders that date's Daily file   <- same run, seconds later
        ...
        a human reviews the Candidate             <- the only window there is
        step 6.5 merges Late Events into an already-closed date, but only
                 for dates *this run* collected, and its §38 guard skips an
                 `event_id` the file already has
        step 6   refuses to overwrite an existing Daily file

    Measured end to end. A `DECISION_APPROVED` Event, filtered KEEP,
    rendered; then `submit_review()` with three of the four fields:

        review stored (returned object)          True
        re-read from disk                        True
        update_daily_history                     NO_LATE_EVENTS
        generate_daily_history                   FileExistsError
        Decision Context in Company History      False
        Daily file changed at all                False

    Nothing warned. `_kept_but_not_rendered()` asks whether the Candidate's
    `event_id` is in the file — it is — so its verdict is clean, and the
    reviewer's own return value says success.

    **Detection only**, the same restraint `scheduler/consistency.py`,
    `history/reconciliation.py` and `agent/delivery.py` all apply and for
    the same reason: every repair is a decision. Re-rendering from the
    review layer would put Company History writes in a module whose
    docstring says it "only operates on what a HistoryRepository already has
    stored"; teaching step 6.5 to refresh items it already merged would
    redefine docs/06 §37's "Late Event" from *new* to *changed*. Both are
    recorded in BACKLOG rather than chosen here.

    Matched by building the line the renderer builds, for the reason
    `_kept_but_not_rendered()` gives at length: taking a rendered line apart
    has edges, constructing it has none. A multi-line value is compared on
    its first line, which is what the renderer put on the label line.
    """
    if not daily_dir.is_dir():
        # An absent subject, not a failed read (C68's asymmetry).
        return (), ()

    by_date: dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]] = {}
    for item in candidates:
        if item.reviewed:
            by_date.setdefault(item.when.isoformat(), []).append(
                (item.event_id, item.reviewed)
            )

    stranded: list[str] = []
    unreadable: list[str] = []
    for when, entries in sorted(by_date.items()):
        rendered = daily_dir / f"{when}.md"
        # Not yet rendered is not a loss — the Scheduler window, exactly as
        # `_kept_but_not_rendered()` treats it. Such a Candidate WILL carry
        # its Decision Context when the day is closed.
        if not rendered.is_file():
            continue
        try:
            text = rendered.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # C92, and here the lost content is human-authored, which
            # this function's own docstring calls the most expensive
            # kind the pipeline handles.
            unreadable.append(when)
            continue
        lines = text.splitlines()
        rendered_lines = _label_lines(lines)
        for event_id, reviewed in entries:
            missing = [
                label
                for label, value in reviewed
                if f"- {label}: {value}".splitlines()[0].strip() not in rendered_lines
            ]
            if missing:
                stranded.append(
                    f"{_authored(event_id)} ({when}): "
                    + ", ".join(_authored(item) for item in missing)
                )
    return tuple(stranded), tuple(unreadable)


_EVENT_COUNT_LINE_PREFIX = "- Event Count: "


def _daily_counts_more_than_it_shows(daily_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """Daily files whose own two numbers disagree. `(date, claimed, carried)`.

    The Daily counterpart of `_monthly_counts_more_than_it_shows()`, and it
    was missing while the Monthly one existed.

    A Daily file states its own total — `daily/markdown._metadata_block()`
    writes `- Event Count: {len(candidates)}`, and `late_events` rewrites it
    to `len(existing_event_ids(...))` on every late update. It also carries
    the Event IDs themselves, and `existing_event_ids()` is the function that
    reads them back. As generated the two agree, which is what makes this
    decidable inside one file with no window and no second opinion: the
    comparison reuses the very function §38's duplicate guard uses, rather
    than counting `- Event ID:` lines a second way.

    **Three real losses reach an operator through it, and none of them had a
    reporter before.**

    1. A KEEP Candidate with `category=None` is dropped from every category
       section by `render_daily_markdown()` — its Event ID never reaches the
       file at all. `test_daily_history.py::
       test_a_category_less_keep_candidate_silently_loses_its_detail`
       characterizes the loss; nothing reported it. Measured: one such
       Candidate alone in a day gives `Event Count: 1` and zero ids.

    2. **BUG-11/27's silent half.** A summary carrying a newline and a
       `- Event ID: VICTIM` line forges a structure line, and the forgery
       does two things beyond the recorded ones: §38's guard then believes
       VICTIM is already in the document, so a genuinely late VICTIM is
       **never appended, on that run and every later one**, and
       `_kept_but_not_rendered()` — the detector for exactly that loss —
       reports clean, because the id it looks for is in the file. Measured,
       one ordinary Candidate whose summary was `did work\n- Event ID: VICTIM`:

           existing_event_ids(day)          {'EVT-1', 'VICTIM'}
           select_late_candidates(day, ...) ()          <- never appended
           _kept_but_not_rendered(...)      ()          <- reports clean
           - Event Count: 1                             <- and two ids

       The last line is the one thing the forgery cannot fake, because the
       renderer wrote it from the candidate list rather than from the text.

    3. An item block deleted by hand (docs/06 §57 permits the edit) without
       correcting the count.

    Reported in **both** directions, unlike the Monthly sibling. There the
    excluded direction (`claimed < rendered`) is a hand edit that ADDED an
    item, which is not a loss. Here the same inequality is the forgery in (2)
    — the file carries an id the run never produced — and it is the more
    expensive of the two, so silencing it to avoid an occasional hand-edit
    report would be the wrong way round.

    Escaping the renderer is BUG-11/27's decision and is not taken here; this
    only counts, which C31 already established as the part that needs no
    decision.

    Threaded on the same `_READ_WORKERS` idiom as its Monthly sibling and for
    the same measured reason — the cost is the file OPEN. This is the first
    check that reads EVERY Daily file (`_kept_but_not_rendered()` reads only
    the days that have stored Candidates), so it was measured before being
    accepted, warm, five items per day:

          365 days   31 ms      2,920 days (8 years)   231 ms

    and split, at 2,920 days: 150 ms reading, 50 ms parsing. The read
    dominates, which is why the parse is left precise.

    A two-tier fast path — count `- Event ID:` lines cheaply, run
    `existing_event_ids()` only for files that already look wrong — was
    rejected for the reason the Monthly sibling rejects its own: the cheap
    count and the precise one disagree in both directions (a duplicated id
    makes the set smaller, a summary-shaped line makes the scan larger), so
    the fast path can agree with `Event Count` on a file the precise pass
    would report. It would buy ~50 ms at a scale eight years away by
    reopening the silencing direction this check exists to close.
    """
    if not daily_dir.is_dir():
        return ()
    paths = [
        path
        for path in sorted(daily_dir.glob("*.md"))
        if not is_incomplete_write(path.name)
    ]
    if not paths:
        return ()

    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
        texts = list(pool.map(_read, paths))

    mismatched: list[tuple[str, int, int]] = []
    for path, text in zip(paths, texts):
        if text is None:
            continue
        claimed = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(_EVENT_COUNT_LINE_PREFIX):
                try:
                    claimed = int(stripped[len(_EVENT_COUNT_LINE_PREFIX):].strip())
                except ValueError:
                    claimed = None
                break
        if claimed is None:
            # A file whose metadata line is missing or unparseable is skipped
            # rather than guessed at: this reports a disagreement between two
            # numbers, and one number it could not read is not a
            # disagreement. Same rule as the Monthly sibling.
            continue
        carried = len(existing_event_ids(text))
        if claimed != carried:
            mismatched.append((path.stem, claimed, carried))
    return tuple(mismatched)


_CONSOLIDATED_ITEMS_LINE_PREFIX = "- Consolidated Items: "


def _monthly_counts_more_than_it_shows(monthly_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """Monthly files claiming more items than they actually carry.

    `(key, claimed, rendered)` per month, for months where the two disagree.

    A Monthly file states its own total — `monthly/markdown._metadata_block()`
    writes `- Consolidated Items: {len(items)}` — and renders one
    `- Event ID:` line per item it files under a section. Both come from the
    same `render_monthly_markdown()` call on the same list, so **as
    generated** they cannot disagree — which is what makes this decidable
    with no window: nothing has to be read twice and nothing outside the one
    file is consulted.

    "As generated" is the whole of that guarantee, and it used to be written
    here as "no false-positive case to caveat", which is more than it earns.
    The file then lives on disk, where docs/06 §57 and docs/11 §71 permit
    the COO to edit official History by hand. Measured, three items rendered
    and one item block deleted by hand:

        as generated        ()
        one block deleted   ('2026-08', 3, 2)

    That is reported, and reporting it is right — the file's own total now
    contradicts its contents, and an operator who deleted an item without
    updating the number should be told. But it is a hand edit rather than a
    loss the pipeline caused, and it stays on screen until the number is
    corrected. The opposite direction (`claimed < rendered`, an item block
    *added* by hand) is excluded below for that same reason.

    They can nonetheless disagree, and the way they do is a silent loss.
    `render_monthly_markdown()` files an item only when
    `item.category in by_category` (DECISION / MILESTONE / ISSUE / LEARNING);
    anything else is dropped from every section while `len(items)` still
    counts it. Measured — two items in, one with `category="Decision"`:

        Consolidated Items: 2
        sections rendered  : Major Decisions, Source Records, Metadata
        EVT-2 present      : False

    and `consolidate_month()` returned `MONTHLY_GENERATED, item_count=2`.

    Reachable without corruption. A `## Late Events` item states its own
    category on a `- Category:` bullet in the Daily file (docs/06 §37,
    docs/09 §12-13), `monthly/parser.py` takes that bullet's text verbatim,
    and docs/06 §57 / docs/11 §71 explicitly permit the COO to edit a Daily
    History by hand. One hand-typed `- Category: Decision` therefore deletes
    that Event from the month.

    Detection only. Which section an unrecognised category belongs in is a
    docs/09 §14 rendering decision — the exact sibling of the Daily-side drop
    `tests/test_daily_history.py::
    test_a_category_less_keep_candidate_silently_loses_its_detail`
    characterizes. That one at least leaves the summary in `## Summary`;
    Monthly has no equivalent, so nothing of the item survives.

    A file whose metadata line is missing or unparseable is skipped rather
    than guessed at: this reports a discrepancy between two numbers, and one
    number it could not read is not a discrepancy.

    One direction only. `claimed < rendered` means the file carries more than
    it was generated with, which is a hand edit (docs/06 §57's Monthly
    equivalent) rather than a loss, and reporting it would put a standing
    line in front of an operator who did exactly what the spec allows.

    **What this cannot see, measured rather than assumed.** A summary is
    rendered unescaped (BUG-11/27, an open docs/06 rendering decision), so a
    summary containing a newline and `- Event ID: …` adds a line this counts.
    Measured — two items, one dropped for its category and one whose summary
    carries a forged line:

        - Consolidated Items: 2
        `- Event ID: ` lines    2
        EVT-2 in the file       False
        this check              () — silent

    That route needs a hand-edited Monthly file, because the pipeline cannot
    deliver a multi-line summary here: `monthly/parser.py` is line-based, so
    a Daily summary carrying a newline loses everything after the first line
    (and the item itself, counted as `unconsolidated`). It can only raise
    `rendered`, so it **hides** a shortfall rather than inventing one: this
    check can be silenced but cannot cry wolf, which is the safer of the two
    directions for an alert. Counting `### ` headings instead would be
    defeated by the same root. Closing it is BUG-11/27's decision, not this
    function's.

    What did **not** need a newline, a hand edit, or anything crafted was a
    summary shaped like one of these two lines — `Event ID: X` silenced a
    shortfall and `Consolidated Items: 999` produced `('2026-08', 999, 1)`
    on a perfectly good month, breaking the "cannot cry wolf" half outright.
    `summary_line_indices()` closes both below.

    Threaded on the same `_READ_WORKERS` idiom as `_read_keep_candidates()`
    and for the same measured reason — the cost is the file OPEN, so the
    figures only mean anything cold, each on its own freshly written tree:

        24 months x  30 items   serial 124.7 ms   threaded  5.6 ms
        120 months x 60 items   serial 667.9 ms   threaded 14.4 ms

    against a whole-view baseline of ~44 ms on this machine. Serial, two
    years of Monthly History would have tripled the cost of `ops_status.py`;
    threaded it is inside the noise. On this machine's actual runtime the
    check is 0.014 ms.

    `summary_line_indices()` adds CPU rather than I/O, so it does not thread
    away — measured in-process, no file access, against the bare line scan
    it sits beside:

        24 months x 30 items (225 lines)   scan 0.24 ms   + 1.14 ms
        120 months x 60 items (435 lines)  scan 2.28 ms   + 11.38 ms

    Two years is the scale this project is at and the whole check stays at
    ~5.9 ms there. Ten years costs ~17 ms more than it used to. That was
    taken over a two-tier fast path (cheap scan first, precise pass only for
    files about to be reported) because the fast path reopens the silencing
    direction to buy a saving at a scale a decade away.
    """
    if not monthly_dir.is_dir():
        return ()
    paths = [
        path
        for path in sorted(monthly_dir.glob("*.md"))
        if not is_incomplete_write(path.name)
    ]
    if not paths:
        return ()

    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
        # `map` preserves input order, so the sorted-filename ordering of the
        # findings is identical to the serial version's.
        texts = list(pool.map(_read, paths))

    mismatched: list[tuple[str, int, int]] = []
    for path, text in zip(paths, texts):
        if text is None:
            continue
        claimed = None
        rendered = 0
        lines = text.splitlines()
        # An item's summary is rendered raw as its block's first bullet, so a
        # summary reading `Event ID: X` or `Consolidated Items: 999` is
        # byte-identical to the lines this counts. Both directions measured,
        # one item per file:
        #
        #     summary `Consolidated Items: 999`  ->  ('2026-08', 999, 1)
        #     summary `Event ID: EXTRA`          ->  ()  (shortfall hidden)
        #
        # The first is the one that matters: this function's contract below
        # is that it can be silenced but cannot cry wolf, and that ordinary
        # summary put "998 items missing" in front of an operator.
        summaries = summary_line_indices(lines)
        for index, line in enumerate(lines):
            if index in summaries:
                continue
            stripped = line.strip()
            if stripped.startswith(_EVENT_ID_LINE_PREFIX):
                rendered += 1
            elif claimed is None and stripped.startswith(_CONSOLIDATED_ITEMS_LINE_PREFIX):
                try:
                    claimed = int(stripped[len(_CONSOLIDATED_ITEMS_LINE_PREFIX):].strip())
                except ValueError:
                    claimed = None
                    break
        if claimed is not None and claimed > rendered:
            mismatched.append((path.stem, claimed, rendered))
    return tuple(mismatched)


def _rendered_event_ids(markdown: str) -> set[str]:
    """The `- Event ID:` values a rendered document actually files.

    Shared by the Monthly reader below and written the way its sibling
    `_monthly_counts_more_than_it_shows()` counts them, for the reason that
    function measured: an item's summary is rendered raw as its block's
    first bullet, so a summary reading `Event ID: X` is byte-identical to a
    real label line. `summary_line_indices()` is what tells the two apart,
    and using anything else here would be a second opinion about a
    distinction this file has already had to get right twice.
    """
    lines = markdown.splitlines()
    summaries = summary_line_indices(lines)
    found: set[str] = set()
    for index, line in enumerate(lines):
        if index in summaries:
            continue
        stripped = line.strip()
        if stripped.startswith(_EVENT_ID_LINE_PREFIX):
            found.add(stripped[len(_EVENT_ID_LINE_PREFIX):].strip())
    return found


def _monthly_lags_its_daily_source(
    daily_dir: Path, monthly_dir: Path, dirty_months: tuple[str, ...] = ()
) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], int]:
    """Event IDs a month's Daily files carry that its Monthly does not.

    `(key, event_ids)` per month, for consolidated months only.

    The third link in a chain whose first two were already watched, and the
    only one that crosses files:

        Daily files (the source)   ->  Consolidated Items (what the run saw)
                                   ->  rendered items (what got written)

    `_monthly_counts_more_than_it_shows()` compares the second against the
    third, inside one file. Nothing compared the first against anything, and
    docs/09 §12-13 makes that the comparison that matters: Monthly is derived
    **wholly** from the Daily files, so a Daily item that is not in its
    month's Monthly is Company History that exists in one official record and
    not the other.

    **Measured, and it needs no corruption — only an edit two specs allow.**
    docs/06 §57 and docs/11 §71 both permit the COO to edit a Daily History
    by hand. With July consolidated on 08-03 and one item added to
    `2026-07-30.md` afterwards:

        run 08-04   exit 0   Monthly has it: False
        run 08-05   exit 0   Monthly has it: False
        ATTENTION            nothing naming it

    and nothing ever revisits that month: `pending_months()` starts *after*
    `last_successful_monthly_close`, and the only thing that reopens a closed
    month is `mark_month_dirty()`, which `app/runner.py` calls for the dates
    a **Late Event** changed — not for a date a person changed. The Late
    Event path is therefore already correct and deliberately left alone here;
    what has no route back is the hand edit.

    The remedy is exact, which is why this is worth reporting rather than
    only recording: Monthly is a derived artifact, so `mark_month_dirty()`
    plus one run rebuilds it — the same sentence
    `_holes_in_the_monthly_sequence()` already earns.

    **Not a second opinion.** The Daily side is read with
    `monthly.parser.read_daily_document()` — the parser Monthly itself
    consolidates with — so "an item" means exactly what it means there, and
    a *set* of event_ids is §59's one-id-one-entry rule by construction. The
    Monthly side is its own `- Event ID:` lines with `summary_line_indices()`
    applied, the same way the sibling check reads them.

    One direction only, and it is the safe one. A summary shaped like
    `- Event ID: X` on the Monthly side can only make that set *larger*, so
    it can hide a finding, never invent one — the same asymmetry the sibling
    check reasons about, pointing the same way. The reverse direction
    (Monthly carries an id no Daily has) is not reported: that is a hand-edited
    Monthly, which is the direction the sibling already declines for.

    **Cost, measured rather than reasoned about.** Parsing every Daily file
    is 28.9 ms per year and 258 ms per decade on this machine — the same
    order as `_daily_counts_more_than_it_shows()`, which already reads them
    all. It is not paid on a healthy tree: a Monthly can only fall behind its
    source if a Daily file changed *after* it was written, so a month whose
    Daily files are all older than its Monthly is skipped on the strength of
    one `st_mtime` each, all of them from one directory read. Measured:

        healthy (nothing examined)  1 year  1.4 ms   10 years  14.1 ms
        every month examined, warm  1 year 24.5 ms   10 years 251.6 ms

    mtime is a **prefilter** here and never the verdict
    — every month it lets through is decided by reading the files — which is
    the distinction `backup/working_copy._content_differs()` draws when it
    refuses mtime for a decision. A restore that rewrites every mtime costs
    one full pass and reports nothing.

    Months listed in `dirty_months` are skipped: the next run rebuilds them,
    and an alert that the next run clears is the kind this file keeps warning
    about. Those are not counted in `skipped` either — a month deliberately
    left for the next run is not a month this failed to read.

    **`skipped` counts every read that shortened the answer, not only the
    mtime one.** C68 added the counter for the `st_mtime` loop above and
    stopped there; three further reads in this function could each fail and
    still return a verdict. Measured against a tree whose 2026-07 Monthly is
    genuinely missing an Event:

        control, everything readable          finding ('E-LATE',)  skipped 0
        the Daily carrying it is corrupt      finding ()           skipped 0
        the Monthly itself is corrupt         finding ()           skipped 0
        the Monthly cannot be stat-ed         finding ()           skipped 0

    All three said `0 found, 0 skipped` — the screen a healthy machine
    prints — about a month with a real hole in it. Counting them does not
    find the hole, and is not meant to: it stops the answer from claiming a
    completeness it does not have.
    """
    daily_dir = Path(daily_dir)
    monthly_dir = Path(monthly_dir)
    if not daily_dir.is_dir() or not monthly_dir.is_dir():
        # An absent subject, not a failed read: with no Daily or no Monthly
        # directory there is no lag to find. Zero skips.
        return (), 0

    dirty = set(dirty_months)
    days_by_month: dict[str, list[date]] = {}
    for day in _daily_dates(daily_dir):
        days_by_month.setdefault(f"{day.year:04d}-{day.month:02d}", []).append(day)

    # One directory read for every mtime, not one `stat()` per day. Same
    # reason `_daily_dates()` above stopped using `glob() + is_file()`: on
    # Windows `DirEntry.stat()` answers from the listing that was already
    # fetched, and the per-file form is what costs. Measured over ten years
    # of Daily History, healthy tree: 63.6 ms -> 14.1 ms. Which names count as
    # a day is still `_daily_dates()`' answer — this only carries times.
    mtimes: dict[str, float] = {}
    skipped = 0
    try:
        for entry in os.scandir(daily_dir):
            try:
                mtimes[entry.name] = entry.stat().st_mtime
            except OSError:
                # C68: counted. A day with no mtime falls to the `0.0`
                # default below, which makes "this Daily is newer than the
                # Monthly" false — so an unreadable day silently argues that
                # the Monthly is up to date.
                skipped += 1
                continue
    except OSError:
        # The directory itself is unreadable: nothing was checked.
        return (), 1

    findings: list[tuple[str, tuple[str, ...]]] = []
    for key, days in sorted(days_by_month.items()):
        if key in dirty:
            continue
        monthly_path = monthly_dir / f"{key}.md"
        try:
            if not monthly_path.is_file():
                # Absent, not unreadable. C68's asymmetry: "there is
                # nothing to look at" must never read as "I failed to
                # look", or an unconsolidated month grows a caveat.
                continue
            monthly_mtime = monthly_path.stat().st_mtime
        except OSError:
            # C91: counted. Without this the month is never compared and
            # the screen still says the check ran.
            skipped += 1
            continue

        # The prefilter: nothing can have fallen behind a Monthly that is
        # newer than every Daily file it was built from.
        if not any(
            mtimes.get(f"{day.isoformat()}.md", 0.0) > monthly_mtime for day in days
        ):
            continue

        try:
            monthly_text = monthly_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # C91: counted, same reason as the stat above.
            skipped += 1
            continue
        monthly_ids = _rendered_event_ids(monthly_text)

        source_ids: set[str] = set()
        for day in days:
            try:
                document = read_daily_document(daily_dir / f"{day.isoformat()}.md", day)
            except Exception:  # noqa: BLE001
                # C91: counted. The original comment — an unreadable Daily
                # has its own reporters, so naming it again here would be
                # the second opinion this module keeps removing — is still
                # true, and it is still not the whole answer. This day's
                # ids never enter `source_ids`, so `missing` is computed
                # against a SHORTER source: the month can be declared
                # current on the strength of the one file nobody could
                # read. "Somebody else names the file" and "this verdict
                # saw everything" are different claims, and only the
                # first of them was true. Measured: with the Daily that
                # carries the missing Event corrupted, the finding went
                # from `('E-LATE',)` to `()` with `skipped` still 0.
                skipped += 1
                continue
            source_ids.update(item.event_id for item in document.items)

        missing = tuple(sorted(source_ids - monthly_ids))
        if missing:
            findings.append((key, missing))
    return tuple(findings), skipped


def _source_note(*breakdowns) -> str:
    """` (DESKTOP_1=2 unattributed=1)`, or empty when nothing is attributed.

    Merged across the breakdowns given so one ATTENTION sentence covering
    two piles names each Desktop once rather than twice. Empty rather than
    "(unknown)" when there is nothing to say — a parenthetical that always
    appears is one an operator stops reading.
    """
    merged: dict[str, int] = {}
    unattributed = 0
    for breakdown in breakdowns:
        for source, count in breakdown.by_source:
            merged[source] = merged.get(source, 0) + count
        unattributed += breakdown.unattributed

    parts = [f"{source}={count}" for source, count in sorted(merged.items())]
    if unattributed:
        parts.append(f"출처불명={unattributed}")
    return f" ({' '.join(parts)})" if parts else ""


def _print_company(now: datetime) -> list[str]:
    snapshot = read_company_activity(
        processed_dir=RUNTIME_DIR / "events" / "processed",
        transport_dir=RUNTIME_DIR / "events" / "transport",
        incoming_dir=RUNTIME_DIR / "events" / "incoming",
        rejected_dir=RUNTIME_DIR / "events" / "rejected",
    )
    attention: list[str] = []

    print("COMPANY — Desktop 4가 수집한 Event 기준")
    print("-" * 60)
    for activity in snapshot.desktops:
        if not activity.has_ever_reported:
            print(f"  {activity.source:<11} 수집된 Event 없음")
            continue
        silent = activity.days_silent(now)
        arrival = activity.days_since_arrival(now)
        roles = "/".join(activity.roles) or "-"
        arrival_note = "" if arrival is None else f" 도착 {arrival}일 전"
        print(
            f"  {activity.source:<11} events={activity.event_count:<6} "
            f"role={roles:<14} 작업일 {silent}일 전{arrival_note}"
        )

    if snapshot.duplicate_event_files:
        # The same sentence the CONTROL TOWER block prints about its own
        # fold, at the block that does the other one. Without it the two
        # blocks disagree about how many Events a Desktop sent and nothing on
        # the page says why — measured at C51 on this repository's own
        # evidence: COMPANY said DESKTOP_4 events=2, CONTROL TOWER said 1.
        #
        # A qualifier, not an ATTENTION line: folding made the number right.
        print(
            f"  중복 파일          : {len(snapshot.duplicate_event_files)}건 "
            "(같은 event_id를 가진 파일이 processed/ 에 둘 이상 있다 — "
            "위 숫자는 Event당 한 번만 센다)"
        )

    silent_sources = snapshot.silent_for(now, days=SILENT_AFTER_DAYS)
    if silent_sources:
        # Split the silent Desktops by whether anything arrived recently.
        # Both groups are still reported — the arrival time narrows the
        # explanation, it never clears the flag.
        caught_up = [
            s
            for s in silent_sources
            if snapshot.for_source(s).caught_up_recently(now, days=SILENT_AFTER_DAYS)
        ]
        truly_quiet = [s for s in silent_sources if s not in caught_up]

        if truly_quiet:
            attention.append(
                f"{SILENT_AFTER_DAYS}일 이상 아무것도 오지 않은 Desktop: "
                f"{', '.join(truly_quiet)} (꺼져 있거나, 보고할 일이 없었거나, "
                f"Agent가 멈췄다 — 현재 데이터로는 여기까지만 말할 수 있다)"
            )
        if caught_up:
            attention.append(
                f"작업일은 {SILENT_AFTER_DAYS}일 이상 지났지만 최근 파일이 도착한 "
                f"Desktop: {', '.join(caught_up)} (꺼져 있다가 밀린 분을 보낸 것으로 "
                f"보인다 — Agent는 살아 있다)"
            )

    backlog = snapshot.backlog
    print()
    print(
        f"  backlog: transport={backlog.awaiting_intake} "
        f"incoming={backlog.awaiting_collection} rejected={backlog.rejected}"
        + (f" unparseable={backlog.unparseable}" if backlog.unparseable else "")
        + (
            f" unreadable_incoming={backlog.unreadable_incoming}"
            if backlog.unreadable_incoming
            else ""
        )
        + (f" future_dated={backlog.future_dated}" if backlog.future_dated else "")
        + (f" name_collision={backlog.name_collision}" if backlog.name_collision else "")
        + (f" incomplete={backlog.incomplete}" if backlog.incomplete else "")
        + (
            f" already_collected={backlog.already_collected}"
            if backlog.already_collected
            else ""
        )
        + (
            f" incoming_incomplete_write={backlog.incoming_incomplete_write}"
            if backlog.incoming_incomplete_write
            else ""
        )
        + (
            f" rejected_incomplete_write={backlog.rejected_incomplete_write}"
            if backlog.rejected_incomplete_write
            else ""
        )
    )
    # Who each pile came from (BACKLOG E-10). The counts above are the
    # authority and are unchanged; these lines only answer "which Desktop",
    # which previously required opening runtime/events/ by hand — and that
    # is the difference between one Desktop misbehaving and every Desktop
    # hitting the same problem, two situations needing opposite reactions.
    for label, breakdown in (
        ("transport", backlog.awaiting_intake_sources),
        ("incoming", backlog.awaiting_collection_sources),
        ("rejected", backlog.rejected_sources),
    ):
        if breakdown.total:
            print(f"           {label:<10} {breakdown.describe()}")

    if not backlog.is_clear:
        # `future_dated` is appended to the same sentence rather than given
        # its own: without it "transport=1" stands forever with no reason,
        # which is the standing-alert-with-no-explanation shape the
        # `unparseable` split was created to remove (see `IntakeBacklog`).
        reasons = []
        if backlog.future_dated:
            reasons.append(
                f"{backlog.future_dated}건은 파일 시각이 이 머신의 시계보다 앞서 "
                f"있어 시계가 따라잡을 때까지 수집되지 않는다 "
                f"(보낸 Desktop의 시계 확인 필요)"
            )
        if backlog.name_collision:
            reasons.append(
                f"{backlog.name_collision}건은 같은 이름이 이미 processed/ 또는 "
                f"rejected/에 있어 매 실행 실패한다 — 재실행으로 해결되지 않는다"
                f"(BACKLOG BUG-43)"
            )
        stalled = f" — 그중 {'; 그중 '.join(reasons)}" if reasons else ""
        attention.append(
            f"수집되지 않고 남은 Event: transport={backlog.awaiting_intake} "
            f"incoming={backlog.awaiting_collection}"
            + _source_note(backlog.awaiting_intake_sources, backlog.awaiting_collection_sources)
            + stalled
        )
    if backlog.rejected:
        attention.append(
            f"Collector가 거부한 Event {backlog.rejected}건"
            + _source_note(backlog.rejected_sources)
            + " — 사람이 확인해야 한다"
        )
    if backlog.incoming_incomplete_write:
        # The same file as the sentence below, one directory earlier, in the
        # window between the reporter dying and the next run moving it. It
        # used to be counted as "an Event the Collector has not taken yet",
        # which named a non-Event and held `is_clear` False for it.
        attention.append(
            f"incoming/에 중단된 쓰기 잔여물 {backlog.incoming_incomplete_write}건 — "
            f"수집을 기다리는 Event가 아니다. Desktop 4의 reporter가 쓰기 도중에 "
            f"죽으면 남는 staging 파일(`.tmp-…json`)이고, 다음 실행에서 Collector가 "
            f"`rejected/`로 옮긴다. 보낸 Desktop을 확인할 필요는 없고 지워도 안전하다"
        )
    if backlog.rejected_incomplete_write:
        # A different sentence, because it is a different fact and a
        # different action. C27 §8 measured that a truncated staging file in
        # `incoming/` is REJECTED and lands in `rejected/` under its staging
        # name, and that ATTENTION then reported it as a rejected Event —
        # C27's own words, *"잘못 이름 붙은 경보 하나"*. No Event was
        # rejected; a write on this machine stopped. Nothing sent it and no
        # Desktop needs looking at.
        attention.append(
            f"rejected/에 중단된 쓰기 잔여물 {backlog.rejected_incomplete_write}건 — "
            f"거부된 Event가 아니다. Desktop 4의 reporter가 쓰기 도중에 죽으면 "
            f"`incoming/`에 남는 staging 파일(`.tmp-…json`)이고, Collector가 그것을 "
            f"읽어 여기로 옮긴 것이다. 보낸 Desktop을 확인할 필요는 없고 지워도 "
            f"안전하다"
        )
    if backlog.unparseable or backlog.unreadable_incoming:
        # Reported for the right reason. These used to be counted as
        # "awaiting intake", which said an Event was queued for collection
        # when in fact it had been judged unparseable and would never be
        # collected — a standing alert no run could clear.
        #
        # `incoming/`의 같은 조건을 같은 줄에 넣는다. 운영자에게는 하나의
        # 사실이기 때문이다 — "읽을 수 없는 파일이 파이프라인 어딘가에
        # 박혀 있고, 어떤 실행도 그것을 움직이지 못한다". 두 단계가 각각
        # 자기 술어로 판정하지만(intake는 JSON까지, Collector는 디코딩까지)
        # 결과는 같다: 사람이 옮기거나 지워야 한다.
        where = []
        if backlog.unparseable:
            where.append(f"transport {backlog.unparseable}건")
        if backlog.unreadable_incoming:
            where.append(f"incoming {backlog.unreadable_incoming}건")
        attention.append(
            f"읽을 수 없는 파일 {' / '.join(where)} — 수집되지 않으며 "
            f"다음 실행에서도 그대로다. 사람이 확인해 옮기거나 지워야 한다"
        )
    if backlog.already_collected:
        # Not ATTENTION: the twin was confirmed to carry the same event_id,
        # so this is the outbox re-sending after a crash mid-send — designed
        # behaviour needing no action. Printed anyway, because otherwise a
        # file sits in transport/ forever with nothing saying why.
        print(
            f"           transport에 이미 수집된 Event {backlog.already_collected}건 "
            f"— 재전송된 중복이며 승격되지 않는다(정상)"
        )
    if backlog.suppressed:
        # The dangerous half of the same skip, and the reason the benign half
        # could be taken out of ATTENTION at all.
        attention.append(
            f"transport의 Event {backlog.suppressed}건이 같은 이름의 다른 파일에 막혀 "
            f"승격되지 않는다 — 그 파일은 같은 Event가 아니다(디렉터리·0바이트·다른 "
            f"event_id). 재실행으로 해결되지 않으며 전달되지 않은 Event다 "
            f"(BACKLOG BUG-53/BUG-47)"
        )
    if backlog.incomplete:
        # A write this pipeline started and never finished (killed between
        # `mkstemp` and `os.replace`). Named, not merely counted, because the
        # operator action is the opposite of every other line here: these are
        # not Events waiting for something, they are garbage, and deleting
        # them is both safe and the only thing that clears this line.
        attention.append(
            f"transport에 완료되지 않은 쓰기 잔여물 {backlog.incomplete}건 "
            f"(.tmp-*.json) — 중단된 실행이 남긴 것으로 Event가 아니다. "
            f"수집되지 않으며 지워도 안전하다"
        )
    if snapshot.unreadable_events:
        attention.append(
            f"읽을 수 없는 processed Event {len(snapshot.unreadable_events)}건: "
            f"{', '.join(snapshot.unreadable_events[:5])}"
        )
    return attention


def _print_history(now: datetime) -> list[str]:
    """Where Company History actually stands, from the state files.

    Reads `monthly_history_state.json` and the Company Repository directories —
    no new state, no new policy. Answers the two questions the COO would
    otherwise have to answer by listing directories: is last month written,
    and is anything waiting to be rebuilt.
    """
    attention: list[str] = []
    local_master = RUNTIME_DIR / "local_master"
    daily_dir = local_master / "daily"
    monthly_dir = local_master / "monthly"

    # `.tmp-*.md` is a Daily/Monthly generator write that never committed, not
    # a day or a month of Company History. Excluded here for the same reason
    # `backup.working_copy` excludes it from backup scope: counting it would
    # report history this project does not have, and for `monthly_files` the
    # `stem` of one would be displayed as if it were a month.
    #
    # `is_file()` on top of that, and it is the same rule every other reader
    # of these two directories already applies — `_daily_dates()` excludes a
    # directory ("it exists, and it is not a day of Company History"),
    # `_holes_in_the_monthly_sequence()` calls `entry.is_file()`, and
    # `backup.working_copy._relative_files()` takes regular files only. These
    # two counts were the last readers without it, which
    # `_misnamed_scope_directories()`'s docstring already named in passing
    # ("including this view's own `daily 파일` count, which uses `glob()`").
    #
    # Measured with `2026-08-01.md` replaced by a directory: this line said
    # `daily 파일 : 5` for four days of Company History, one line above
    # `daily state 정합성 : CONSISTENT` — a count that disagreed with the
    # detector printed beneath it, in the direction that hides a loss.
    daily_count = (
        sum(
            1
            for p in daily_dir.glob("*.md")
            if not is_incomplete_write(p.name) and p.is_file()
        )
        if daily_dir.is_dir()
        else 0
    )
    monthly_files = (
        sorted(
            p.stem
            for p in monthly_dir.glob("*.md")
            if not is_incomplete_write(p.name) and p.is_file()
        )
        if monthly_dir.is_dir()
        else []
    )

    # Candidates parked for a human. `HistoryFilter` sends BLOCKED /
    # COMPLETED / CANCELLED here (docs/05 §24), and nothing in the pipeline
    # renders them: `generate_daily_history()` reads only KEEP, and
    # `submit_review()` fills Decision Context without touching
    # `filter_result`, so a reviewed candidate stays REVIEW. Measured — a
    # COMPLETED Event was still absent from its Daily file after a review
    # and two further runs.
    #
    # docs/05 §50 makes the count itself the point: "REVIEW가 너무 많다 ->
    # 자동화 실패 신호", and "COO가 매일 수십 개의 REVIEW를 수동 처리해야
    # 하는 구조를 만들지 않는다". That signal had no reader — this was the
    # one pile of human-owned work with no counter, while `rejected/`,
    # `signals_rejected/` and orphaned Events all had one (BACKLOG E-20).
    # Split by whether a human has actually been through them. The pile as
    # a whole is not an actionable alert — E-20's decision is what would
    # empty it, and nothing an operator does today can. What IS actionable
    # is the part nobody has looked at yet, and that part clears the moment
    # they do.
    #
    # C22 alerted on the whole pile, so running `review_cli.py` — the
    # documented correct action — left the warning standing forever
    # (`submit_review()` fills Decision Context without touching
    # `filter_result`, so the file never leaves `review/`). Measured. That
    # is the alert-that-cannot-clear this project keeps warning about, and
    # C26 found the same shape in the Working Copy check.
    review_dir = RUNTIME_DIR / "history_candidates" / "review"
    review_waiting, review_done = _split_reviewed(review_dir)
    review_count = review_waiting + review_done

    # BUG-46's permanent half: Candidates the Scheduler will never reach.
    # Unset variable -> say so and compute nothing, exactly as this file
    # already does for the two Agent variables.
    keep_dir = RUNTIME_DIR / "history_candidates" / "keep"
    keep_candidates, unreadable_candidates = _read_keep_candidates(keep_dir)
    history_start = _history_start_date()
    stranded = _candidates_before(keep_candidates, history_start) if history_start else ()

    print("HISTORY — Company Repository")
    print("-" * 60)
    print(f"  daily 파일          : {daily_count}")
    print(f"  monthly 파일        : {len(monthly_files)}")
    if monthly_files:
        print(f"                        {', '.join(monthly_files[-6:])}")
    print(
        f"  검토 대기 Candidate : {review_count}"
        + (f" (미검토 {review_waiting} / 검토됨 {review_done})" if review_done else "")
    )
    if history_start is None:
        print("  (COMPANY_OPS_HISTORY_START_DATE 미설정 — 시작일 이전 Candidate는 "
              "계산되지 않음)")
    elif stranded:
        print(f"  시작일 이전 Candidate: {len(stranded)}")
        attention.append(
            f"Company History 시작일({history_start.isoformat()})보다 이른 KEEP "
            f"Candidate {len(stranded)}건: {', '.join(stranded[:5])}"
            f"{' 외' if len(stranded) > 5 else ''} — Scheduler는 시작일 이전으로 "
            f"가지 않으므로 이 Event들은 **어떤 실행에서도** Daily History에 "
            f"들어가지 않는다. 보내는 Desktop의 COMPANY_OPS_AGENT_START_DATE가 "
            f"이 시작일보다 이르면 그 차이가 원인이다(BACKLOG BUG-46)"
        )
    # A Candidate neither check could read. Both of them drop such a file —
    # neither can claim a fact about bytes it could not parse — so without
    # this line it is reported by nothing, while "Candidate 정합성: OK" sits
    # two lines below.
    #
    # The consequence is wider than "the next Scheduler step fails", which is
    # what this line said when C28 added it. Measured (C29): the Scheduler
    # builds its keep index **once per batch, before the date loop**, so one
    # unreadable Candidate stops *every* date, not only its own.
    #
    #     no corruption          COMPLETED, 9 dates generated, 9 Daily files
    #     one corrupt Candidate  FAILED,    0 dates generated, 0 Daily files
    #
    # That is true of a Candidate whose JSON will not parse (BUG-38) and of
    # one whose `timestamp` will not parse (A-7) — the index reads both.
    # Company History stops advancing entirely until a human moves the file.
    if unreadable_candidates:
        print(f"  읽을 수 없는 Candidate: {len(unreadable_candidates)}")
        attention.append(
            f"읽을 수 없는 KEEP Candidate {len(unreadable_candidates)}건: "
            # `_authored()`, because a Candidate's filename is Event-authored
            # (C111). `file_repository.safe_candidate_filename()` returns
            # `f"{history_id}.json"` verbatim whenever the id is
            # filesystem-safe, and `history_id` comes from the Event — which
            # another Desktop wrote and `validate_event()` only type-checks.
            # Reproduced: an Event whose `event_id` was token-shaped put
            # `ntn_….json` straight into this line.
            f"{', '.join(_authored(name) for name in unreadable_candidates[:5])}"
            f"{' 외' if len(unreadable_candidates) > 5 else ''} — Scheduler는 "
            f"배치마다 keep 인덱스를 **한 번** 만들므로 이 파일 하나 때문에 "
            f"**모든 날짜의** Daily History 생성이 멈춘다(실측: 9일치 → 0일치). "
            f"사람이 확인해 옮기거나 고쳐야 한다(BACKLOG A-7 / BUG-38)"
        )

    # E-17: stored as Company History, absent from the day it belongs to.
    unrendered, unreadable_for_keep = _kept_but_not_rendered(
        keep_candidates, daily_dir
    )
    if unrendered:
        print(f"  Daily 미반영 KEEP   : {len(unrendered)}")
        running = (
            " (Runner 실행 중 — 완료 후 재확인 권장)"
            if is_locked(_runner_lock_path())
            else ""
        )
        attention.append(
            f"KEEP Candidate {len(unrendered)}건이 저장돼 있는데 그 날짜의 Daily "
            # These are `event_id`s, which is the case `_authored()`'s own
            # docstring was written for — "the *orphan* line two blocks above
            # printed the same Event's id raw". This line is a third instance
            # of the same shape and was missed when that one was fixed.
            f"History에 없다: {', '.join(_authored(i) for i in unrendered[:5])}"
            f"{' 외' if len(unrendered) > 5 else ''} — 그 날짜는 이미 렌더링됐고, "
            f"Late Event 병합(6.5단계)의 대상은 **그 실행이 수집한 날짜뿐**이라 "
            f"어떤 실행도 이것만 따로 넣지는 않는다(BACKLOG E-17). 다만 같은 "
            f"날짜의 Event가 나중에 하나라도 더 수집되면 그때 **함께 들어간다** "
            f"(실측: 방치된 EVT-S가 뒤늦은 EVT-N과 같이 "
            f"`added_event_ids=('EVT-S','EVT-N')`으로 병합됐다). 지난 날짜라면 "
            f"그런 Event가 오지 않는 것이 보통이므로 사람이 확인해야 "
            f"한다{running}"
        )
    # C33 §3: the reviewer wrote it, Company History never got it.
    #
    # Placed beside E-17's check because they are the same question one
    # level apart — that one asks whether the Candidate reached the Daily
    # file, this asks whether the Candidate's *content* did. The running
    # caveat is shared for the same reason: a Runner between step 5 and step
    # 6 has not rendered the day yet.
    # C85 finished the sentence this alert used to stop halfway through.
    # "유실은 아니다" was true and was the reassuring half:
    # `runtime/history_candidates/keep/` is a **sibling** of the backup
    # source (`runtime/local_master/`), so no Backup scope can reach it,
    # and `runtime/` is `.gitignore`d so the repository does not carry it
    # either. A-14's table records both. One copy, one machine — not lost,
    # and not the same as safe. An alert that says the first without the
    # second tells an operator to relax about the asset README RULE 11/12
    # calls the company's most important.
    unrendered_review, unreadable_for_review = _reviewed_but_not_rendered(
        keep_candidates, daily_dir
    )
    # ONE line for both, because it is one fact about one set of files.
    # Two counters would report the same unreadable Daily twice -- both
    # detectors walk the same dates over the same directory -- and a
    # second opinion on "which files could not be read" is exactly what
    # C28 keeps out of this file. A union of dates cannot double-count.
    unreadable_daily = sorted(set(unreadable_for_keep) | set(unreadable_for_review))
    if unreadable_daily:
        print(
            f"  Daily 대조 불가    : {len(unreadable_daily)}일 — 위 두 판정은 "
            f"그만큼 덜 본 결과다 ({', '.join(unreadable_daily[:3])}"
            + (" 외" if len(unreadable_daily) > 3 else "")
            + ")"
        )
    if unrendered_review:
        print(f"  검토 미반영         : {len(unrendered_review)}")
        attention.append(
            f"사람이 입력한 Decision Context {len(unrendered_review)}건이 Company "
            f"History에 반영되지 않았다: "
            f"{', '.join(_authored(i) for i in unrendered_review[:5])}"
            f"{' 외' if len(unrendered_review) > 5 else ''} — Daily 파일은 이미 "
            f"렌더링됐고, Late Event 병합은 **새 Event**만 대상이라 어떤 실행도 "
            f"이 내용을 넣지 않는다. 내용 자체는 "
            f"runtime/history_candidates/keep/에 남아 있으니 지금 유실된 것은 "
            f"아니다 — 단, 그곳은 **Backup 대상도 아니고 저장소에도 없다** "
            f"(docs/08 §26-28은 daily/·monthly/만 동기하고 runtime/은 .gitignore된다). "
            f"이 내용은 이 머신 한 곳에만 있어 디스크가 사라지면 사라진다 "
            f"(BACKLOG C33 §3, A-14)"
        )
    if review_waiting:
        attention.append(
            f"사람 검토를 기다리는 History Candidate {review_waiting}건 "
            f"(runtime/history_candidates/review/) — BLOCKED/COMPLETED/CANCELLED는 "
            f"자동 규칙으로 판정하지 않는다(docs/05 §24). 이 건들은 아직 Company "
            f"History에 없고 어떤 실행도 넣지 않는다(BACKLOG E-20)"
        )

    # Is Company History actually off this machine? Read-only, from state the
    # Backup step already writes — no new artifact and no new decision.
    try:
        backup_state = load_backup_state(RUNTIME_DIR / "state" / "backup_state.json")
    except BackupStateError as exc:
        print("  마지막 성공 백업    : 읽을 수 없음")
        attention.append(f"backup state 파일이 손상됨: {exc}")
        backup_state = None
    if backup_state is not None:
        last_backup = backup_state.last_successful_backup
        print(
            f"  마지막 성공 백업    : "
            f"{last_backup.isoformat(timespec='seconds') if last_backup else '아직 없음'}"
            + (f" ({backup_state.backup_status.value})" if backup_state.backup_status else "")
        )
        # The same future-dated-pointer family as the two state pointers
        # below, and the worst member of it: the other two stop *work*, this
        # one silences a *safety check*.
        #
        # `_history_newer_than_the_last_backup()` asks "was this file written
        # after the last successful push". A `last_successful_backup` ahead of
        # the calendar makes that true of nothing, so the check that exists to
        # say "this Company History is only on this machine" returns clean
        # forever. Measured, one real never-pushed Daily present:
        #
        #     last_successful_backup 2026-08-01   -> 1 alert (correct)
        #     last_successful_backup 2027-05-01   -> 0 alerts
        #
        # `backup/state.py` writes this from the run's own clock, so the same
        # skew that produces a future Daily Close pointer produces this too —
        # and a restored `backup_state.json` carries it across machines.
        #
        # Reported before the check it disables, so the operator reads why the
        # line below is silent rather than trusting the silence. Detection
        # only: correcting the timestamp is deciding when the last real backup
        # happened, which nothing here knows.
        # Two deliberate differences from the two date pointers below.
        #
        # Compared against the **real** clock, not the caller's `now`. This
        # value and the file mtimes it is weighed against are both real-time
        # measurements; `now` is the view's date reference. Mixing the two is
        # the trap `_healthy_backup_state()` in the tests names in as many
        # words ("Anchoring one of them to the pinned clock and the other to
        # the wall clock"). In production they are the same value.
        #
        # And with a tolerance, because this one has sub-second resolution
        # and a real race. `ops_status.py` promises it is safe to run while
        # the Runner is running, and `main()` takes its clock reading once at
        # the top — so a Backup finishing a few hundred milliseconds later
        # legitimately writes a timestamp after it. That is not skew.
        #
        # The tolerance is not arbitrary: the harm scales with the distance.
        # A timestamp an hour ahead blinds the unbacked-History check for an
        # hour and then heals itself; one months ahead blinds it until the
        # calendar arrives, which is the condition worth a line in ATTENTION.
        # An hour is far beyond any run's duration (the git subprocess
        # timeout alone is 300 s) and far below "effectively permanent".
        if last_backup is not None:
            wall_clock = businessdate.now()
            reference = (
                wall_clock
                if last_backup.tzinfo is not None
                else wall_clock.replace(tzinfo=None)
            )
            if last_backup > reference + timedelta(hours=CLOCK_AHEAD_TOLERANCE_HOURS):
                print(
                    f"  마지막 성공 백업    : 미래 시각 "
                    f"({last_backup.isoformat(timespec='seconds')})"
                )
                attention.append(
                    f"backup state가 미래 시각을 마지막 성공 백업으로 기록하고 있다: "
                    f"{last_backup.isoformat(timespec='seconds')} (지금은 "
                    f"{reference.isoformat(timespec='seconds')}) — 이 값보다 나중에 "
                    f"쓰인 History만 '미백업'으로 잡히므로, **그 시각이 올 때까지 "
                    f"미백업 History 검사가 무엇도 보고하지 못한다.** 즉 아래 줄이 "
                    f"조용한 것은 안전하다는 뜻이 아니다. 시계가 앞섰다가 교정됐거나 "
                    f"state 파일을 그런 머신에서 복원한 경우다 — 사람이 확인해야 한다"
                )

        unbacked, unbacked_skipped = _history_newer_than_the_last_backup(
            local_master, last_backup
        )
        if unbacked_skipped:
            # Said before the list, because it is a statement *about* the
            # list: this check answers "is what is on this machine actually
            # off it?", and a file whose mtime could not be read is one the
            # answer does not cover. Dropping it silently shortens the list,
            # which is the direction that reads as reassurance.
            attention.append(
                f"백업 대조에서 {unbacked_skipped}건을 확인하지 못했다 — 아래 "
                "'원격 백업에 도달하지 않은' 목록은 그만큼 짧다"
            )
        if unbacked:
            names = ", ".join(str(p.relative_to(local_master)) for p in unbacked[:5])
            attention.append(
                f"원격 백업에 도달하지 않은 Company History {len(unbacked)}건: {names}"
                f"{' 외' if len(unbacked) > 5 else ''} — "
                + (
                    "이 머신에서 한 번도 백업이 성공한 적이 없다"
                    if last_backup is None
                    else f"마지막 성공 백업({last_backup.isoformat(timespec='seconds')}) "
                    f"이후에 쓰였다"
                )
                + ". Backup이 SUCCESS/NOT_REQUIRED를 보고하고 있어도 이 파일들은 "
                "이 머신에만 있다"
            )

        # Redirected History directories, stated as a fact (A-19). Not an
        # alert: whether the redirect is legitimate is a deployment decision,
        # and on a machine that meant it no action would ever clear the line.
        junctions, junctions_skipped = _junctions_in_scope(local_master)
        for where, target in junctions:
            print(f"           junction {where} -> {target}")
        if junctions_skipped:
            print(
                f"           junction 검사 {junctions_skipped}건 확인 못 함 "
                "— 이 목록은 노출을 전부 담고 있지 않다"
            )

        # ...and, when it is the case-fold cause, say exactly what to rename.
        # Without this the operator has to notice a capital letter in a
        # filename and know what it means.
        misnamed, misnamed_checked = _misnamed_scope_directories(local_master)
        if not misnamed_checked:
            attention.append(
                f"Local Master(`{local_master}`)를 나열하지 못해 백업 범위 밖 "
                "디렉터리를 확인 못 함 — 이것은 '없음'이 아니다. 읽을 수 있게 "
                "된 뒤 다시 확인해야 한다(BACKLOG BUG-55)"
            )
        for actual, expected in misnamed:
            attention.append(
                f"Local Master의 `{actual}/`는 백업 범위 밖이다 — Backup은 "
                f"`{expected}/`만 본다(docs/08 §26, 대소문자 구분). 이 디렉터리의 "
                f"Company History는 한 번도 백업되지 않으며 Backup은 계속 "
                f"SUCCESS/NOT_REQUIRED를 보고한다. `{expected}/`로 이름을 바꿔야 "
                f"한다(BACKLOG BUG-55)"
            )

    # `state = None` rather than `return attention` (C146).
    #
    # This handler used to leave the whole block. `_print_history()` runs
    # from here to line ~3580, so one unreadable file skipped **every
    # remaining question the report asks about Company History** — and said
    # nothing about having skipped them. That is the shape `_block()`'s own
    # docstring is written against: *"a partial report presented as
    # complete, which is the silent-loss shape this project keeps
    # removing."*
    #
    # Measured on one tree, corrupting only this file and changing nothing
    # else — 13 ATTENTION lines became 10, and these four went silent:
    #
    #     Daily State와 실제 History가 어긋난다        (state claims a day that is gone)
    #     Daily History 시퀀스에 구멍 2일              (Company History no run rebuilds)
    #     Daily State가 미래 날짜를 …                  (Daily generation stopped entirely)
    #     History Candidate의 Decision Context에       (a credential on its way to the
    #     Secret 형태의 문자열 1건                      backup remote)
    #
    # None of the four has anything to do with Monthly. The last one is the
    # worst: the report stops looking for leaked credentials because a
    # different state file will not parse — and a damaged
    # `monthly_history_state.json` also stops the Monthly pipeline, so it can
    # sit there for a long time while it does that.
    #
    # `None`, not an empty `MonthlyState()`: an invented "nothing has been
    # consolidated" would make this view *report* things it does not know
    # ("monthly 파일은 있는데 state에는 통합 기록이 없다"), which is the
    # guessing docs/10 §46 forbids. The three places below that need the file
    # say so instead.
    state = None
    try:
        state = load_monthly_state(RUNTIME_DIR / "state" / "monthly_history_state.json")
    except MonthlyStateError as exc:
        print("  monthly state       : 읽을 수 없음")
        attention.append(f"monthly state 파일이 손상됨: {exc}")

    # docs/10 §48: "Runner 시작 시 최소 확인 가능: State Last Success ->
    # Corresponding Local History 존재?". `scheduler/consistency.py`
    # implements exactly that check and is fully tested, but nothing ever
    # called it — a corruption detector that never runs detects nothing.
    #
    # Reported here rather than wired into the Runner on purpose. That module
    # refuses to enter Scheduler's control flow because deciding what to *do*
    # about an inconsistency is an operator call (§49 "History가 State보다
    # 우선", §64 "COO가 개입해야 하는 상황"). A read-only status view is the
    # one place that reports without deciding.
    consistency = check_state_consistency(
        RUNTIME_DIR / "state" / "daily_history_state.json", daily_dir
    )
    print(f"  daily state 정합성  : {consistency.status.value}")
    if consistency.status is ConsistencyStatus.STATE_INCONSISTENCY:
        attention.append(
            f"Daily State와 실제 History가 어긋난다: {consistency.detail}"
        )
    elif consistency.status is ConsistencyStatus.STATE_UNREADABLE:
        attention.append(f"Daily State를 읽을 수 없다: {consistency.detail}")

    # The interior of the closed range, which the check above never had in
    # view — see `_holes_in_the_daily_sequence()`.
    holes = _holes_in_the_daily_sequence(daily_dir)
    if holes:
        print(f"  Daily 시퀀스 구멍   : {len(holes)}")
        # Where the missing days might still be, asked rather than assumed.
        # The Backup Working Copy is right here and is already listed for
        # the un-backed check, so naming which ones survive there turns a
        # diagnosis into an instruction. `git` history may hold others.
        backup_daily = RUNTIME_DIR / "backup_working_copy" / "daily"
        recoverable = sorted(
            {day.isoformat() for day in _daily_dates(backup_daily)} & set(holes)
        )
        where = (
            f"그 중 {len(recoverable)}건은 Backup Working Copy에 아직 있다"
            f"({', '.join(recoverable[:5])}{' 외' if len(recoverable) > 5 else ''})"
            if recoverable
            else "Backup Working Copy에도 없다 — 원격 git history를 확인해야 한다"
        )
        attention.append(
            f"Daily History 시퀀스에 구멍 {len(holes)}일: "
            f"{', '.join(holes[:5])}{' 외' if len(holes) > 5 else ''} — 그 날짜들은 "
            f"닫혔고 파일이 있었는데 지금 없다(빈 날에도 파일은 쓰인다). Scheduler는 "
            f"마지막 Daily Close **다음** 날짜부터 처리하므로 **어떤 실행도 이 날들을 "
            f"다시 만들지 않는다**, 그리고 정합성 검사는 마지막 날짜만 보므로 계속 "
            f"CONSISTENT를 보고한다. 부분 복원·동기화 누락·손편집 삭제가 남기는 "
            f"모양이다. {where}"
        )

    # The days the check above cannot bound — see
    # `_history_gone_from_local_master()`. A missing *prefix* moves the range
    # instead of leaving a hole in it, so the earliest days are the ones no
    # Company History indicator can see going.
    gone = _history_gone_from_local_master(
        local_master, RUNTIME_DIR / "backup_working_copy"
    )
    if gone:
        print(f"  Master에서 사라짐   : {len(gone)}")
        attention.append(
            f"Backup Working Copy에는 있고 Local Master에는 없는 Company History "
            f"{len(gone)}건: {', '.join(one_line(name) for name in gone[:5])}"
            + (" 외" if len(gone) > 5 else "")
            + " — 그 파일들은 백업에 도달했었고 지금 이 머신에 없다. Working Copy는 "
            "한 방향으로만 쓰이고 삭제를 반영하지 않으므로 아직 거기에 남아 있다. "
            "Backup은 이 상태에서 add/commit/push를 전부 중단하므로(docs/08 §31) "
            "**복구하기 전까지 이후 History도 원격에 가지 않는다**. Daily 시퀀스 "
            "구멍 검사는 파일이 있는 범위만 보므로 사라진 것이 가장 이른 날짜들이면 "
            "아무것도 보고하지 않고, 그 날짜 이름의 **디렉터리**가 대신 서 있는 "
            "경우도 같다"
        )

    # A Daily Close pointer dated in the future — a silent, permanent stop
    # that every other indicator reports as perfect health.
    #
    # `agent/status.py` already answers this exact question for the Agent's
    # own state file, in these words: *"agent state says it has collected
    # through X, which is in the future … nothing will be collected until
    # that date arrives"*. The Runner's own state file makes the identical
    # claim and nobody had asked it. Applying an answer this project has
    # already given is not a new policy (C28 §6's rule).
    #
    # `check_state_consistency()` cannot see it: it asks only whether the
    # claimed Daily file exists, and in the reachable version of this the
    # file does exist — the Scheduler wrote it while the clock was skewed.
    # Measured, pointer `2026-12-25` with that file present, "now"
    # 2026-08-14, one KEEP Candidate waiting for 2026-08-12:
    #
    #     scheduler.run_once()   COMPLETED, generated=()
    #     state consistency      CONSISTENT
    #     ATTENTION              (nothing)
    #
    # Company History stops for four months and every signal reads green,
    # because `_generate_pending_dates()` computes `start = pointer + 1 day`
    # and `end = yesterday`, so `start > end` and the loop runs zero times.
    # It never walks backwards, which is the right behaviour — and that is
    # exactly why nothing recovers on its own.
    #
    # Reachable through clock skew that was later corrected (a dead CMOS
    # battery, an NTP jump, a VM resumed with a stale clock) or a state file
    # restored from a machine that had one — the same two causes C17 records
    # for the Agent side.
    #
    # Detection only, and it cannot false-alarm: `end` is always yesterday,
    # so no healthy run can ever set this pointer past today. Repairing it
    # would mean deciding which date Company History should resume from,
    # which is docs/10 §46's prohibition and §64's operator call.
    close = consistency.last_successful_daily_close
    if close is not None and close > businessdate.clock_date(now):
        print(f"  daily state 정합성  : 미래 날짜 ({close.isoformat()})")
        attention.append(
            f"Daily State가 미래 날짜를 마지막 Daily Close로 기록하고 있다: "
            f"{close.isoformat()} (오늘은 {businessdate.clock_date(now).isoformat()}) — Scheduler는 "
            f"그 다음 날부터 어제까지를 처리하므로 **그 날짜가 올 때까지 어떤 "
            f"Daily History도 생성되지 않는다.** 그동안 수집된 Event는 전부 "
            f"Candidate로만 쌓이고, Scheduler는 COMPLETED를, 정합성 검사는 "
            f"CONSISTENT를 계속 보고한다. 시계가 앞섰다가 교정됐거나 state 파일을 "
            f"그런 머신에서 복원한 경우다 — 사람이 state 파일을 확인해야 한다"
        )

    # The same §48 check, aimed at the pair nobody aimed it at.
    #
    # `check_state_consistency()` compares Scheduler state against the Daily
    # file it claims exists. `monthly_history_state.json` makes the identical
    # kind of claim — `last_successful_monthly_close` says "this month is
    # consolidated" — and nothing compared it to anything. §48 does not say
    # "daily only".
    #
    # It cannot be a false alarm. The pointer advances on exactly two
    # outcomes, `MONTHLY_GENERATED` (the file was just written) and
    # `MONTHLY_UNCHANGED` (the file was already there), so the file existed
    # at the moment the pointer was set. Missing now means it was lost
    # afterwards — and `run_once()` will never revisit that month, because
    # `pending_months()` starts after the pointer.
    #
    # Measured, `last_successful_monthly_close="2026-07"` with the file
    # removed: `monthly_run_once()` returned no results at all, the view
    # printed "monthly 파일: 0" and "마지막 통합한 달: 2026-07" two lines
    # apart, and ATTENTION was empty. A month of Company History was gone
    # with every indicator healthy.
    #
    # Detection only, like every other check in this block. Regenerating it
    # is docs/10 §46's prohibition ("프로그램이 임의로 ... 다시 생성하면 안
    # 된다") and §49's operator call.
    #
    # Scope matches §48's exactly — the watermark month, not every month
    # below it. An earlier month lost while the pointer moved on is the same
    # limitation the Daily check has, and widening it here would be
    # inventing scope rather than applying the spec's.
    # `None` when the state file above could not be read — every check under
    # this pointer is then skipped, and only those.
    closed = state.last_successful_monthly_close if state is not None else None
    if closed is not None:
        expected_monthly = monthly_history_path(monthly_dir, closed)
        if not expected_monthly.is_file():
            print(f"  monthly state 정합성: STATE_INCONSISTENCY ({closed}.md 없음)")
            attention.append(
                f"Monthly State와 실제 History가 어긋난다: state는 {closed}을 "
                f"통합 완료로 기록하지만 {expected_monthly}가 없다 — 어떤 실행도 "
                f"그 달을 다시 만들지 않는다(pending_months()가 이 포인터 다음부터 "
                f"시작한다). 사람이 확인해야 한다"
            )

        # The Daily pointer's future-dated twin, for the same reason and with
        # the same restraint. `pending_months()` starts at the month AFTER
        # this pointer and stops at the last closed month, so a pointer ahead
        # of the calendar makes that range empty — `monthly_run_once()`
        # returns no results at all and this view prints the pointer as if it
        # were an achievement. Measured, pointer `2027-06` with the file
        # present and "now" 2026-08: `results=()`, no ATTENTION, and every
        # month from 2026-08 onward silently never consolidated.
        #
        # Strictly *after* the current month, so it cannot false-alarm: §49
        # forbids consolidating a month still in progress, so a healthy run
        # can never set this past the previous month.
        current_month_key = f"{now.year:04d}-{now.month:02d}"
        if closed > current_month_key:
            print(f"  monthly state 정합성: 미래 달 ({closed})")
            attention.append(
                f"Monthly State가 미래의 달을 통합 완료로 기록하고 있다: {closed} "
                f"(이번 달은 {current_month_key}) — pending_months()는 이 포인터 "
                f"**다음** 달부터 시작하므로 그때까지 **어떤 달도 통합되지 않는다.** "
                f"Daily는 계속 쌓이고 Monthly만 영구히 멈춘 채 모든 지표가 정상을 "
                f"보고한다. 시계가 앞섰다가 교정됐거나 state 파일을 그런 머신에서 "
                f"복원한 경우다 — 사람이 state 파일을 확인해야 한다"
            )

    # BACKLOG A-20: an Event consumed by the Collector whose History
    # Candidate was never written is lost from Company History permanently —
    # the event_id is already marked seen, so no later run reconsiders it.
    # The Run Manifest reports that a run failed and which component
    # aborted, but names no Event; this answers "which one".
    #
    # Reported, never repaired — the same restraint as the consistency check
    # above, for the same reason. Re-processing an orphan would be deciding
    # A-20's open question by implementation.
    reconciliation = find_orphaned_events(
        processed_dir=RUNTIME_DIR / "events" / "processed",
        keep_dir=RUNTIME_DIR / "history_candidates" / "keep",
        review_dir=RUNTIME_DIR / "history_candidates" / "review",
    )
    print(
        f"  Candidate 정합성    : "
        f"{'OK' if reconciliation.is_clean else 'ORPHANED_EVENT'} "
        f"(파일 {reconciliation.checked}건 확인)"
    )
    # `파일`, not `Event`, and the word is the fix (C77). `checked` is
    # `len(paths)` -- what this detector inspected -- while the COMPANY and
    # CONTROL TOWER blocks above print `Event N건` meaning **distinct**
    # Events, folded. On the deployment runtime those two numbers differ by
    # exactly the duplicate-file count (17 files, 16 Events), so one screen
    # carried the same word with two meanings and nothing said which was
    # which. The number is unchanged and still worth printing: it is what
    # says this scan looked at something.
    distinct_orphans = _one_per_event(reconciliation.orphaned, lambda o: o.event_id)
    if reconciliation.orphaned:
        for orphan in distinct_orphans[:5]:
            # `one_line()` for the reason `main()`'s ATTENTION loop gives:
            # `event_id` arrives from another Desktop and a newline inside one
            # forges a whole line of this block. The `!` prefix and the fixed
            # indentation are exactly what a forged line would imitate.
            print(f"                        ! {_authored(orphan.event_id)} "
                  f"[{orphan.decision.value}]")
        # `find_orphaned_events()` is a pure function and knows nothing about
        # the lock, but the pipeline it inspects has a window where a
        # perfectly healthy Event looks orphaned: Collector moves the WHOLE
        # batch into `processed/` (step 4) before the History Filter loop
        # starts writing Candidates one at a time (step 5). Run this view
        # during a large catch-up — the usage `ops_status.py` explicitly
        # promises is safe — and Events whose Candidate turn has simply not
        # come yet are reported as permanently lost.
        #
        # The list is NOT filtered or suppressed: a real loss hidden behind
        # "probably just running" is far worse than a false alarm, and this
        # cannot tell the two apart. A sentence is added, nothing is removed.
        running = " (Runner 실행 중 — 완료 후 재확인 권장)" if is_locked(_runner_lock_path()) else ""
        # Counted per Event, not per file, and the file count follows when
        # the two differ -- see `_one_per_event()`.
        duplicated = len(reconciliation.orphaned) - len(distinct_orphans)
        also = f" (파일 {len(reconciliation.orphaned)}건 — 같은 Event가 둘 이상의 " \
               f"파일로 있다)" if duplicated else ""
        attention.append(
            f"수집됐지만 History에 들어가지 못한 Event {len(distinct_orphans)}건{also}: "
            f"{', '.join(_authored(o.event_id) for o in distinct_orphans[:5])}"
            f"{' 외' if len(distinct_orphans) > 5 else ''} — 재실행으로 "
            f"복구되지 않는다(BACKLOG A-20). 사람이 확인해야 한다" + running
        )
    if reconciliation.unreadable:
        # Naming the files, which this line did not. `UnreadableEvent`
        # carries `event_path` precisely so a person can go and open it, and
        # that field had no reader anywhere (C32 §20's sweep). Every sibling
        # ATTENTION line in this view names up to five items; this one said
        # "N건" and left the operator to find them.
        #
        # The *filename*, not an `event_id` — the file is the thing that
        # could not be parsed, so there is no id to quote. `one_line()` all
        # the same: `main()`'s sink applies it to the whole message, and a
        # name assembled here should not depend on that staying true.
        names = ", ".join(
            one_line(item.event_path.name) for item in reconciliation.unreadable[:5]
        )
        attention.append(
            f"processed에 읽을 수 없는 Event {len(reconciliation.unreadable)}건: "
            f"{names}{' 외' if len(reconciliation.unreadable) > 5 else ''} — "
            f"History 반영 여부를 판단할 수 없다"
        )

    # Secret-shaped content *inside an Event*, which no gate in this project
    # looks at. See `_secret_shaped_event_content()` for the measurement: the
    # Agent refuses a Signal carrying one, and an Event that arrived from
    # another Desktop is never scanned at all, so the string reaches Company
    # History and the backup remote intact.
    #
    # C49 added the third destination, and it is the one an operator's
    # rotation checklist is most likely to miss because it is not a file:
    # **the Notion PROJECTS row**. `ExecutionPlanSync` writes the Event's own
    # text into it, unmodified. Measured, one Event of each shape through the
    # real sync:
    #
    #     event_id     -> `Last Event ID`      leaks
    #     project_id   -> `Project ID` + Title leaks
    #     milestone    -> `Current Milestone`  leaks
    #     blocker      -> `Blocker`            leaks
    #     summary      -> (no property)        does not
    #
    # Four of the five fields scanned above. Notion is a third party with its
    # own retention and its own copy, and "고쳐도 원격 history에는 남는다"
    # was true of exactly one of the two remotes this text reaches.
    #
    # Said conditionally, because this tool cannot know whether the run that
    # accepted the Event had Notion configured — it reads `processed/`, and a
    # deployment without a token is supported (docs/04). The sentence names
    # the destination and the condition; deciding to redact on the way out
    # would be the pipeline rewriting a person's own words, which docs/06 §57
    # is about (BACKLOG).
    #
    # Everything printed is redacted, including the ids -- `event_id` and
    # `project_id` are among the fields scanned, so quoting one raw is exactly
    # how this report would become the second copy of a leaked credential.
    # The Candidate side of the same question (C125). Placed beside the Event
    # detector because an operator reading either line takes the same action
    # — rotate the credential — and because the two doors are only
    # distinguishable if both are reported.
    secret_context, unchecked_context = _secret_shaped_decision_context(
        RUNTIME_DIR / "history_candidates" / "keep",
        RUNTIME_DIR / "history_candidates" / "review",
    )
    if unchecked_context:
        # A fact, not an ATTENTION line, following C26's rule: an unreadable
        # Candidate is already an ATTENTION item from
        # `_candidate_consistency()`, and a second alarm for one action is
        # how a section stops being read. What this adds is the *scope* of
        # the answer above it — "no secret found" is only about the files
        # that could be opened.
        print(
            f"  Decision Context 미확인: {unchecked_context}건 "
            f"(읽지 못한 Candidate — 위 Secret 점검은 이 파일들을 보지 못했다)"
        )
    if secret_context:
        named_context = ", ".join(
            f"{_authored(history_id)}({fields})"
            for history_id, fields in secret_context[:5]
        )
        attention.append(
            f"History Candidate의 Decision Context에 Secret 형태의 문자열 "
            f"{len(secret_context)}건: {named_context}"
            f"{' 외' if len(secret_context) > 5 else ''} — 이 필드는 사람이 "
            f"`review_cli.py`로 직접 타이핑한 산문이고, **들어올 때 아무것도 "
            f"검사하지 않는다**. Signal은 그 자리에서 거부되고(`find_secret_material()`), "
            f"다른 Desktop의 Event는 최소한 아래 줄이 보고하지만, 이 경로는 "
            f"둘 다 없었다(C125). 실측: Daily History에 그대로 렌더링되고 "
            f"Company Repository -> Working Copy -> backup 원격까지 간다 "
            f"(`scan_for_secrets()`는 이름만 본다). 해당 자격증명을 **교체**해야 "
            f"한다 — Candidate를 고쳐도 이미 렌더링된 Daily와 원격 history에는 "
            f"남는다"
        )

    secret_events = _secret_shaped_event_content(RUNTIME_DIR / "events" / "processed")
    if secret_events:
        # One line per leaked credential, not per file holding it (C77). The
        # instruction this alert ends with is "자격증명을 교체해야 한다",
        # and an operator can only act on each credential once; measured, a
        # duplicated Event took all five slots and five *other* leaked
        # credentials were named nowhere on the page.
        distinct_secrets = _one_per_event(secret_events, lambda row: row[0])
        duplicated_secret_files = len(secret_events) - len(distinct_secrets)
        also = (
            f" (파일 {len(secret_events)}건 — 같은 Event가 둘 이상의 파일로 있다)"
            if duplicated_secret_files else ""
        )
        named = ", ".join(
            f"{_authored(event_id)}({_authored(source)}, {fields})"
            for event_id, source, _name, fields in distinct_secrets[:5]
        )
        attention.append(
            f"Event 내용에 Secret 형태의 문자열 {len(distinct_secrets)}건{also}: {named}"
            f"{' 외' if len(distinct_secrets) > 5 else ''} — 이 Desktop의 Agent는 "
            f"Signal을 그 자리에서 거부하지만(`find_secret_material()`), 다른 "
            f"Desktop에서 온 Event나 손으로 쓴 파일은 `validate_event()`만 거치고 "
            f"그것은 내용을 읽지 않는다. 실측: Daily History에 그대로 쓰이고 "
            f"backup 원격까지 push된다(`scan_for_secrets()`는 이름만 본다). "
            f"**Notion Sync가 설정된 배포에서는 PROJECTS 행에도 그대로 들어간다** "
            f"— event_id/project_id/milestone/blocker 네 필드가 그대로 쓰인다"
            f"(summary는 Property가 없어 가지 않는다). "
            f"해당 자격증명을 **교체**해야 한다 — 파일을 고쳐도 원격 history와 "
            f"Notion Workspace에는 남는다. 거부로 바꾸면 그 Event가 "
            f"`rejected/`로 가서 Company History에서 사라지므로 결정이 "
            f"필요하다(BACKLOG)"
        )

    # Secret names the gate's own list holds but its comparison misses.
    #
    # Both roots, because they fail for different reasons and the action is
    # the same: Local Master is what the gate scans (so a case variant there
    # passes the gate, is synced, and is pushed), and the Working Copy is
    # what git commits (E-21's ungated route, whose report above uses the
    # same case-sensitive scan and is therefore blind in the same way).
    #
    # Not folded into the E-21 line: that one says "the gate did not look
    # here", this one says "the gate looked and did not recognise it", and
    # only the second is still true after E-15/E-21 are decided.
    #
    # The Working Copy list goes through `_would_reach_the_commit()` for the
    # same reason the E-21 line does — what matters there is what git stages,
    # and C26 measured that reporting without asking git is a standing false
    # alarm on a correctly configured repo (docs/08 §28's `.gitignore`).
    # Local Master has no repository to ask; there the fact is that sync will
    # copy the file and the gate will not stop it.
    for label, root, ask_git in (
        ("Local Master", local_master, False),
        ("Backup Working Copy", RUNTIME_DIR / "backup_working_copy", True),
    ):
        unrecognised = _secret_names_the_gate_will_not_recognise(root)
        if unrecognised and ask_git:
            unrecognised = _would_reach_the_commit(root, unrecognised)
        if unrecognised:
            attention.append(
                f"{label}에 Backup Secret 게이트가 **이름을 알아보지 못하는** 파일 "
                f"{len(unrecognised)}건: {', '.join(unrecognised[:5])}"
                f"{' 외' if len(unrecognised) > 5 else ''} — 게이트의 이름 목록"
                f"(docs/08 §29)에는 들어 있지만 비교가 대소문자를 구분하고 Windows "
                f"파일시스템은 구분하지 않는다. 즉 `id_rsa`는 막히고 `ID_RSA`는 "
                f"BACKUP_SUCCESS로 원격에 올라간다(BUG-55와 같은 뿌리, 다른 위치). "
                f"게이트를 바꾸면 새로운 BACKUP_FAILED 조건이 생기므로(E-15) 여기서는 "
                f"보고만 한다 — 파일 이름을 소문자로 바꾸거나 옮겨야 한다"
            )

    # Secret-shaped files sitting in the Backup Working Copy (E-21).
    #
    # `backup.run_once()` scans **Local Master**, but `git add -A` commits
    # the **Working Copy** — so a file that reached the Working Copy by any
    # route other than sync is ungated and gets pushed. Measured: a `.env`
    # and an `id_rsa` placed there went to the remote while the backup
    # reported BACKUP_SUCCESS.
    #
    # This changes no gate: `scan_for_secrets()` is applied here exactly as
    # it is applied to Master, with the same decided list of names, to a
    # directory nobody was looking at. Reporting is late by construction —
    # a scheduled Backup may already have pushed — but late is the
    # difference between rotating a leaked credential and never knowing.
    # Choosing what the gate guards is the decision E-15/E-21 record.
    working_copy = RUNTIME_DIR / "backup_working_copy"
    if working_copy.is_dir():
        exposed = _would_reach_the_commit(
            working_copy, scan_for_secrets(working_copy)
        )
        if exposed:
            attention.append(
                f"Backup Working Copy에 Secret 형태의 파일 {len(exposed)}건: "
                f"{', '.join(exposed[:5])}"
                f"{' 외' if len(exposed) > 5 else ''} — Backup은 Local Master만 "
                f"검사하고 git은 이 파일들을 무시하지 않으므로 `git add -A`로 "
                f"원격에 올라간다(BACKLOG E-21). 이미 push됐다면 자격증명 교체가 "
                f"필요하다 — 파일을 지우는 것만으로는 원격 history에서 사라지지 "
                f"않는다"
            )

        # What the history already carries, which deleting the file does not
        # change. Reported separately because the action is different: the
        # line above is "stop it from going out", this one is "it is already
        # out". Measured — the file-present warning cleared the moment the
        # operator deleted it, while `git show HEAD:.env` on the remote still
        # returned the token.
        leaked, leak_checked = _secrets_ever_committed(working_copy)
        if not leak_checked:
            attention.append(
                "Backup 원격 history의 Secret 검사를 확인 못 함(git이 "
                f"응답하지 않음, {working_copy}) — 이것은 '없음'이 아니라 "
                "'확인 못 함'이다. git이 답할 수 있게 된 뒤 다시 확인해야 하며, "
                "그전까지 이 항목에 대해서는 아무 것도 보장되지 않는다"
            )
        if leaked:
            attention.append(
                f"Backup 원격 history에 이미 들어간 Secret 형태 경로 {len(leaked)}건: "
                f"{', '.join(leaked[:5])}"
                f"{' 외' if len(leaked) > 5 else ''} — 파일을 지워도, 지금 Working "
                f"Copy에 없어도 원격 history에는 남아 있다. 해당 자격증명을 "
                f"**교체**해야 하며, 이 줄은 history를 다시 쓰기 전까지 사라지지 "
                f"않는다(BACKLOG E-21)"
            )

        # Staging files that reached the Working Copy and are carried by git.
        #
        # This exists because of what C27 changed, not in spite of it.
        # `working_copy._is_in_scope()` now excludes `.tmp-*`, which removes
        # the trap where cleaning up such a file armed a permanent
        # BACKUP_FAILED (the deletion gate). But exclusion cuts both ways:
        # a staging file that was already synced and committed by the
        # pre-C27 code is now outside `_relative_files()` on BOTH sides, so
        # `sync_to_working_copy()` reports nothing about it, ever.
        #
        # Measured, a `daily/.tmp-abc123.md` holding a truncated day already
        # in the commit: sync returned added/modified/deleted all empty,
        # `scan_for_secrets()` found nothing (it is not secret-shaped), and
        # ATTENTION was empty — truncated Company History sitting in the
        # backup remote with no trace anywhere.
        #
        # That is the failure mode C24 and C26 are about, produced by this
        # Sprint's own fix. A change that removes a bad signal owes a good
        # one in its place.
        #
        # Same git-aware probe as above, for the same reason: only files git
        # actually carries are reported, so a `.gitignore` that covers them
        # makes this silent. Deleting them is safe and is what clears it —
        # they are not Company History, and Master's copy (if any) is
        # untouched.
        # `.git/` is skipped. It is git's own storage, not Working Copy
        # content, and `git ls-files` never lists anything inside it — so on
        # the normal path those entries would be filtered out anyway. The
        # reason to skip them here rather than rely on that: when git cannot
        # answer, `_would_reach_the_commit()` fails safe by returning the
        # candidates unchanged, and on a real repository with a missing or
        # timed-out git that would report git's internals as residue. It is
        # also 93% of the walk on this machine's Working Copy today (90 of
        # 97 files), and that share only grows as backup history accumulates.
        residue = _would_reach_the_commit(
            working_copy,
            tuple(
                sorted(
                    str(path.relative_to(working_copy))
                    for path in working_copy.rglob("*")
                    if ".git" not in path.parts
                    and path.is_file()
                    and is_incomplete_write(path.name)
                )
            ),
        )
        if residue:
            attention.append(
                f"Backup Working Copy에 완료되지 않은 쓰기 잔여물 {len(residue)}건: "
                f"{', '.join(residue[:5])}"
                f"{' 외' if len(residue) > 5 else ''} — 중단된 실행이 남긴 것으로 "
                f"Company History가 아니다. git이 커밋 대상으로 들고 있으므로 "
                f"원격에 잘린 내용이 들어간다. 지워도 안전하다"
            )

    # A Daily whose own two numbers disagree — the Daily counterpart of the
    # Monthly check above, which existed while this one did not.
    daily_mismatch = _daily_counts_more_than_it_shows(daily_dir)
    if daily_mismatch:
        # C71: bounded for `_RECENT_ON_SCREEN`'s own stated reason — a
        # section that can push the ATTENTION block off the top is a screen
        # nobody scrolls back up. The ATTENTION line below already cuts at
        # five and says "외"; the printed list above it did not, and this one
        # grows with **days** of Company History (a renderer that wrote the
        # count wrong once wrote it wrong for every day it rendered).
        for key, claimed, carried in daily_mismatch[:_RECENT_ON_SCREEN]:
            print(f"  Daily 항목 불일치   : {key} (Event Count {claimed} / 기록된 id {carried})")
        if len(daily_mismatch) > _RECENT_ON_SCREEN:
            print(
                f"  Daily 항목 불일치   : 외 "
                f"{len(daily_mismatch) - _RECENT_ON_SCREEN}건 "
                f"(총 {len(daily_mismatch)}건)"
            )
        attention.append(
            f"Daily History의 자기 숫자가 어긋난 날 {len(daily_mismatch)}건: "
            + ", ".join(
                f"{key}({claimed}→{carried})" for key, claimed, carried in daily_mismatch[:5]
            )
            + (" 외" if len(daily_mismatch) > 5 else "")
            + " — `- Event Count:`는 그날 렌더러가 받은 Candidate 수이고 뒤의 수는 "
            "파일이 실제로 들고 있는 Event ID 수다. **적게 들고 있으면** 그 Event가 "
            "Company History에 없다는 뜻이다(Category가 네 값 밖이면 어느 Section에도 "
            "들어가지 않는다 — 해당 Candidate의 `category`를 확인한다). **많이 들고 "
            "있으면** 어떤 Event의 `summary`/`project_id`/`event_id`에 개행이 들어가 "
            "`- Event ID:` 줄을 위조한 것이다(BACKLOG BUG-11/27) — 그 경우 그 id로 "
            "**나중에 도착하는 진짜 Event가 영원히 추가되지 않고**, 그 손실은 이 줄 "
            "말고는 어디에도 보고되지 않는다. 손으로 항목 블록을 지운 경우도 앞쪽에 "
            "해당한다(docs/06 §57)"
        )

    # A Monthly that has fallen behind the Daily files it is derived from —
    # the link the two checks around it cannot see, because both compare a
    # document with itself.
    # Skipped, and said out loud, when the state file is unreadable.
    # `dirty_months` is what tells this check which months are *known* to be
    # awaiting a rebuild; passing `()` for "I could not read it" would report
    # every such month as a divergence — a false alarm invented out of a
    # missing input, which is the opposite of the mistake this whole
    # correction is about.
    if state is None:
        print(
            "  Monthly 원본 대조   : 확인 못 함 — monthly state를 읽지 못해 "
            "재생성 대기 중인 달을 가려낼 수 없다"
        )
        lagging, lagging_skipped = [], 0
    else:
        lagging, lagging_skipped = _monthly_lags_its_daily_source(
            daily_dir, monthly_dir, dirty_months=tuple(state.dirty_months)
        )
    if lagging_skipped:
        print(
            f"  Monthly 원본 대조   : {lagging_skipped}건 확인 못 함 — "
            "아래 판정은 그만큼 덜 본 결과다"
        )
    if lagging:
        # Same bound, same reason (C71). Grows with months rather than days,
        # which is slower and still unbounded.
        for key, event_ids in lagging[:_RECENT_ON_SCREEN]:
            print(f"  Monthly 원본 미반영 : {key} ({len(event_ids)}건)")
        if len(lagging) > _RECENT_ON_SCREEN:
            print(
                f"  Monthly 원본 미반영 : 외 {len(lagging) - _RECENT_ON_SCREEN}건 "
                f"(총 {len(lagging)}건)"
            )
        attention.append(
            f"Daily에는 있는데 그 달 Monthly에는 없는 Event {sum(len(i) for _k, i in lagging)}건: "
            + ", ".join(
                f"{key}({', '.join(_authored(e) for e in event_ids[:3])}"
                + (" 외" if len(event_ids) > 3 else "")
                + ")"
                for key, event_ids in lagging[:3]
            )
            + (" 외" if len(lagging) > 3 else "")
            + " — Monthly는 Daily에서 **전부** 파생된다(docs/09 §12-13). 그 달이 "
            "통합된 뒤에 Daily가 바뀌면(docs/06 §57 / docs/11 §71이 허용하는 손편집) "
            "**어떤 실행도 그 달을 다시 만들지 않는다** — `pending_months()`는 마지막 "
            "통합 **다음** 달부터 시작하고, 닫힌 달을 다시 여는 것은 Late Event가 "
            "바꾼 날짜에 대한 `mark_month_dirty()`뿐이다. 복구는 정확하다: 그 달을 "
            "dirty로 표시하고 한 번 실행하면 Monthly가 자기 원본과 다시 같아진다"
        )

    # A Monthly that counted an item it did not write down.
    shortfall = _monthly_counts_more_than_it_shows(monthly_dir)
    if shortfall:
        # Same bound, same reason (C71).
        for key, claimed, rendered in shortfall[:_RECENT_ON_SCREEN]:
            print(f"  Monthly 항목 누락   : {key} ({claimed}건 중 {rendered}건만 기록)")
        if len(shortfall) > _RECENT_ON_SCREEN:
            print(
                f"  Monthly 항목 누락   : 외 {len(shortfall) - _RECENT_ON_SCREEN}건 "
                f"(총 {len(shortfall)}건)"
            )
        attention.append(
            "Monthly History가 스스로 센 항목보다 적게 기록한 달 "
            f"{len(shortfall)}건: "
            + ", ".join(
                f"{key}({claimed}→{rendered})" for key, claimed, rendered in shortfall[:5]
            )
            + (" 외" if len(shortfall) > 5 else "")
            + " — 그 달의 Event가 Monthly에서 통째로 사라졌다(Daily 쪽과 달리 "
            "요약조차 남지 않는다). 원인은 둘 중 하나이고 조치가 서로 다르다. "
            "(1) 렌더러가 DECISION/MILESTONE/ISSUE/LEARNING 외의 Category를 어느 "
            "Section에도 넣지 않고 버리는데 `Consolidated Items`는 그것까지 세는 "
            "경우 — 해당 달 Daily의 `- Category:` 줄이 네 값 중 하나인지 확인한다. "
            "이건 **다시 만들어도 같은 결과**다. (2) Monthly 파일이 손으로 편집돼 "
            "항목 블록이 빠진 경우(docs/06 §57 / docs/11 §71이 허용한다) — 이건 "
            "그 달을 dirty로 표시하고 다시 실행하면 **복구된다**(실측: 항목 블록 "
            "하나를 지우면 그냥 재실행은 그대로 두고, 강제 rebuild가 되살린다). "
            "이 검사는 두 숫자가 어긋난 사실만 알 수 있고 둘 중 어느 쪽인지는 "
            "말할 수 없다"
        )

    if state is not None:
        print(f"  마지막 통합한 달    : {state.last_successful_monthly_close}")
    if state is not None and state.dirty_months:
        print(f"  재생성 대기         : {', '.join(state.dirty_months)}")

        # "자동 처리된다" is true of every dirty month except one kind, and
        # for that kind it is a false statement in ATTENTION.
        #
        # `monthly/generator.py`'s dirty loop refuses a month that predates
        # `history_start_date` (§85-86: never invent a month the system does
        # not cover), returns MONTHLY_PENDING, and **deliberately leaves the
        # flag in place** — its own comment says silently forgetting it
        # "would hide a state file that needs a person". The Runner then
        # classifies PENDING as not-a-failure (correct for the ordinary case,
        # where Daily Catch-up will fill a gap), logs one line to
        # `late_update.log`, and moves on. Nothing reads that log, so the
        # person it was left for never hears about it.
        #
        # Reachable through a hand-edited or restored state file, which is a
        # DR path rather than a corruption. Unblocked here by the same
        # `_history_start_date()` that unblocked BUG-46 — one decision was
        # holding two detections.
        unresolvable: list[str] = []
        if history_start is not None:
            first_month = (history_start.year, history_start.month)
            for key in state.dirty_months:
                try:
                    year, month = (int(part) for part in key.split("-", 1))
                except ValueError:
                    continue
                if (year, month) < first_month:
                    unresolvable.append(key)

        automatic = [key for key in state.dirty_months if key not in unresolvable]
        if automatic:
            attention.append(
                f"Late Event로 다시 만들어야 할 달: {', '.join(automatic)} "
                f"(다음 Runner 실행에서 자동 처리된다)"
            )
        if unresolvable:
            attention.append(
                f"재생성 대기로 남아 있지만 **어떤 실행도 처리할 수 없는** 달: "
                f"{', '.join(unresolvable)} — Company History 시작일"
                f"({history_start.isoformat()}) 이전이라 Monthly 생성이 거부되고"
                f"(docs/09 §85-86) 플래그는 그대로 남는다. state 파일을 사람이 "
                f"확인해야 한다"
            )

    # The interior of the consolidated range — Daily's hole check, one level
    # up. See `_holes_in_the_monthly_sequence()`.
    monthly_holes = _holes_in_the_monthly_sequence(monthly_dir)
    if monthly_holes:
        print(f"  Monthly 시퀀스 구멍 : {len(monthly_holes)}")
        attention.append(
            f"Monthly History 시퀀스에 구멍 {len(monthly_holes)}달: "
            f"{', '.join(monthly_holes[:5])}"
            f"{' 외' if len(monthly_holes) > 5 else ''} — 그 달들은 통합됐고 파일이 "
            f"있었는데 지금 없다(중요한 일이 없던 달에도 파일은 쓰인다, docs/09 §72). "
            f"`pending_months()`는 마지막 통합한 달 **다음**부터 시작하므로 어떤 "
            f"실행도 이 달들을 다시 만들지 않는다. **다만 Monthly는 Daily에서만 "
            f"파생되므로(docs/09 §12-13) 복구된다** — 그 달을 dirty로 표시하고 다시 "
            f"실행하면 내용까지 그대로 돌아온다(실측: 삭제 후 그냥 재실행은 그대로, "
            f"dirty 표시 후 MONTHLY_GENERATED). 해당 달 Daily가 남아 있는지 먼저 "
            f"확인해야 한다"
        )

    # A month that closed but was never consolidated is the one case worth
    # flagging: it means Daily Coverage never became COMPLETE for it.
    last_closed = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    last_closed_key = f"{last_closed[0]:04d}-{last_closed[1]:02d}"
    if state is None:
        # Both branches below read the pointer; neither can be answered.
        pass
    elif state.last_successful_monthly_close is None:
        if monthly_files:
            attention.append(
                "monthly 파일은 있는데 state에는 통합 기록이 없다 — state 파일 확인 필요"
            )
    elif state.last_successful_monthly_close < last_closed_key:
        attention.append(
            f"{last_closed_key} Monthly가 아직 없다 (마지막 통합: "
            f"{state.last_successful_monthly_close}) — 그 달 Daily가 아직 "
            f"완전하지 않다는 뜻이다"
        )

    return attention


def _notion_retry_queue_path() -> Path:
    """`runtime/state/notion_retry_queue.json`, derived per call.

    Not `notion.retry_queue.DEFAULT_QUEUE_PATH`: that constant is frozen at
    import from the *package's* project root, so redirecting `RUNTIME_DIR` —
    the single knob every test and probe uses to isolate this view — would
    leave this one block reading the developer's live queue while every
    other block read the fixture. That is C31 §10's trap verbatim, and
    `_agent_dir()` above carries the full reasoning.
    """
    return RUNTIME_DIR / "state" / "notion_retry_queue.json"


def _dashboard_pending_path() -> Path:
    """`runtime/state/dashboard_pending.json`, derived per call — see above."""
    return RUNTIME_DIR / "state" / "dashboard_pending.json"


def _queue_age_days(added_at: str, now: datetime) -> float | None:
    """Whole-ish days since `added_at`, or None if it cannot be read.

    `added_at` is read back out of a JSON file that `load_queue()`
    shape-checks but never validates as a timestamp, so the naive/aware
    guard is the same one `_print_last_run()` applies to `summary.started_at`
    for the same reason: a hand-edited or restored state file can carry an
    offset-less timestamp, and comparing it to an aware `now` raises
    TypeError.
    """
    try:
        added = datetime.fromisoformat(added_at)
    except (TypeError, ValueError):
        return None
    return (_comparable(now, added) - added).total_seconds() / 86400


#: The variables `notion/config.py` requires before any projection runs.
_NOTION_REQUIRED = ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID")


def _last_run_notion_outcome() -> tuple[str, int, int, int] | None:
    """What the last run actually did to Notion, from the Run Manifest.

    Returns `(run_id, created, updated, skipped_old)` when the last run's
    `notion_sync` component succeeded and carries C104's counts, else None.

    Why the screen wants it (C104). C103 closed the case where two zeroes
    read as health when nothing had been tried; what it could not do was say
    the opposite. `_notion_credentials_exported_but_never_exercised()` goes
    quiet as soon as any run attempts the step -- correctly, because a failed
    attempt is reported elsewhere -- and a **succeeded** attempt then produced
    no line at all. So the best state the system can be in, "a run reached
    Notion and here is what it wrote", was the one state this block never
    mentioned.

    That evidence is durable and already on disk. It needs no network, which
    is why this is not the decision E-27 is waiting for: the manifest is a
    local file the run itself wrote, and reading it is what LAST RUN already
    does.

    Returns None rather than zeros for a manifest written before C104: those
    components carry `processed` and nothing else, and inventing `created=0`
    for them would report "wrote nothing" about a run that may well have
    written plenty. Absent stays absent.
    """
    try:
        summary = read_summary(DEFAULT_RUN_SUMMARY_PATH)
    except (RunSummaryError, OSError, ValueError):
        return None
    if summary is None:
        return None
    component = summary.component("notion_sync")
    if component is None or component.status is not ComponentStatus.SUCCESS:
        return None
    metrics = component.metrics
    if not isinstance(metrics, Mapping):
        return None
    counts = []
    for key in ("created", "updated", "skipped_old"):
        value = metrics.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            # Pre-C104 manifest, or a hand-edited one. Both are "this file
            # cannot answer the question", which is not the same as zero.
            return None
        counts.append(value)
    return (str(summary.run_id), counts[0], counts[1], counts[2])


def _notion_credentials_exported_but_never_exercised() -> bool:
    """Credentials this process can see, that no run has yet reached Notion with.

    **Never returns, reads back, logs or compares a value.** Only whether the
    two names are set and non-blank, which is the same question
    `NotionConfig.from_env()` asks and the same restraint its sibling
    `_notion_credentials_present_but_unexported()` keeps.

    Why it exists (C103). C90 closed the case where `.env` holds working
    credentials and the process cannot see them: the screen said
    **미설정** while the operator looked at a configured file. Measured this
    cycle, the state one step *after* that fix has the opposite problem and
    a worse shape:

        export a token, run this view

        NOTION — Retry Queue
          대기 중 Event       : 0
          Dashboard 밀린 기록 : 0

    Byte for byte what a healthy Notion prints, and no ATTENTION line. The
    token in that measurement was invalid — the live API answered
    `401 Unauthorized`, which `sync.PERMANENTLY_REFUSING_STATUS_CODES`
    classifies as never clearing by retrying — and every automatic signal
    read clean. The unexported case is loud and the *refused* case is
    silent, which is the wrong way round: one of them a scheduled Runner
    recovers from by itself and the other needs a person.

    The reason the block cannot tell is structural rather than an
    oversight. Both of its numbers come from durable local artefacts — the
    retry queue and the pending-Dashboard file — and both are written **by a
    run**. Before the first run under new credentials, they are empty
    because nothing has happened yet, and empty is exactly what a healthy
    Notion also looks like.

    So this does not ask Notion anything. `dashboard_server.py`'s docstring
    promises the page "does not contact Notion", and adding a network call
    to a status view is a decision about what this tool is (BACKLOG E-27) —
    not one to take in passing while fixing what it says. What it reports is
    the thing the local evidence *can* establish: these credentials have not
    been exercised, so nothing on this screen is evidence about them.

    Deliberately narrow. It is silent as soon as any run has actually
    attempted the Notion step, whatever the outcome — a failed attempt is
    reported by the retry queue and by LAST RUN, and a second voice on a
    question already answered is the alarm people learn to skim.
    """
    if any(not (os.environ.get(name) or "").strip() for name in _NOTION_REQUIRED):
        return False
    try:
        summary = read_summary(DEFAULT_RUN_SUMMARY_PATH)
    except (RunSummaryError, OSError, ValueError):
        # LAST RUN reports an unreadable manifest itself. Staying quiet here
        # keeps this block from becoming a second opinion about that file.
        return False
    if summary is None:
        # No run has ever finished. The credentials are certainly unexercised,
        # and saying so is the whole point — "no manifest" is not "fine".
        return True
    component = summary.component("notion_sync")
    if component is None:
        # The step never started: an earlier one aborted the run. Same
        # conclusion, reached the other way.
        return True
    # The enum, not its spelling: `read_summary()` validates this field into
    # `ComponentStatus`, and matching on text would keep passing if the
    # value ever stopped being one.
    return component.status is ComponentStatus.SKIPPED


def _notion_credentials_present_but_unexported() -> tuple[str, ...]:
    """Names that `.env` fills in and this process cannot see.

    **Never returns, reads back, logs or compares a value.** Only whether the
    line has one, and only for the two names above — `.env` is the one file
    in this tree that holds a real credential, and a status view is the last
    place that should be handling one.

    Why it exists (C90). `.env` is deliberately not auto-loaded — the
    template says so and this project has kept it that way. The cost was
    invisible: with working credentials sitting in `.env`, `from_env()`
    raises, `run_company_ops.py` prints "Notion 미설정 … 건너뜁니다", and the
    Run Manifest records `notion_sync: SKIPPED`. The screen then tells an
    operator **미설정** — *not configured* — while they are looking at a
    `.env` that is configured, and their Notion projection quietly stops
    being written.

    Measured on this deployment: `.env` held a valid token and database id,
    the PROJECTS database was reachable and 15 days behind the Control
    Tower, and every run had reported the same untroubling word.

    "Missing" and "present but not exported" need opposite reactions, and
    only one of them is a configuration decision. This tells them apart.
    """
    # `RUNTIME_DIR.parent`, never `PROJECT_ROOT`. They are the same directory
    # in production and they are **not** the same under a test or a probe:
    # `RUNTIME_DIR` is the knob this view is redirected by (see `_agent_dir()`
    # and `RuntimeDirIsTheOnlyKnobTests`), and `PROJECT_ROOT` freezes at
    # import. The first draft of this function used `PROJECT_ROOT` and twelve
    # existing tests failed at once — every one of them a fixture that had no
    # `.env` reading this repository's real one. That is C31's incident, and
    # C88 added a gate for it three cycles ago; this function walked straight
    # into it anyway, which is the argument for the gate rather than against.
    try:
        lines = (RUNTIME_DIR.parent / ".env").read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, ValueError):
        # No `.env`, or one this process cannot read: nothing to say. A
        # status view must not fail because a file it merely hoped for is
        # absent.
        return ()
    filled = set()
    for line in lines:
        stripped = line.strip()
        # No `startswith("#")` test, and that is measured rather than
        # forgotten (the same finding as C84's bool guard). A commented line
        # yields the name `"# NOTION_API_TOKEN"`, which is not one of the two
        # names below, so the comment can never change this function's
        # answer. A mutation removing the check failed nothing; the branch
        # was unreachable. `test_comments_are_not_settings` asserts the
        # answer instead of guarding a path nothing can take.
        if not stripped or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        # `.strip()` on the value only to decide "is there anything here" —
        # the value itself is dropped on the next line and never leaves.
        if name.strip() in _NOTION_REQUIRED and value.strip():
            filled.add(name.strip())
    return tuple(
        name
        for name in _NOTION_REQUIRED
        if name in filled and not os.environ.get(name)
    )


def _print_notion(now: datetime) -> list[str]:
    """The two Notion queues — and the two fields they write that nothing read.

    `RetryQueueEntry` records `added_at` and `attempt_count` on every upsert,
    and `PendingDashboardRecord` records the same pair. Grepping the whole
    repository for a *consumer* of either finds none: they are written by the
    queue modules, round-tripped through JSON, and read by no log line, no
    status view and no test outside the queue modules' own. That is BUG-39's
    shape — a value computed and discarded — in the one place where it costs
    the most.

    What it costs: BUG-13 established that `NOTION_RETRY_REQUIRED` covers
    both "Notion was briefly down" and "Notion will refuse this forever", and
    fixed the *reason string* so the log could tell them apart. The queue's
    own two fields answer the other half of the same question — how long has
    this been stuck, and how many times has it been tried — and reached
    nobody.

    The Run Manifest's `queued=` metric is not a substitute, three ways over:
    it is the *last run's* count, it appears only when `notion_sync` is a
    non-SUCCESS component, and it cannot see a queued entry whose
    `to_event()` fails — `app/runner.py` counts that one as
    `notion_unreadable` and leaves it in the queue, where it stays forever
    while every counter reports zero queued.

    Read-only and never fatal, like every other block here: a damaged queue
    file is reported (docs/10 §46 — the program never deletes it).
    """
    attention: list[str] = []
    print("NOTION — Retry Queue")
    print("-" * 60)

    try:
        entries = load_notion_retry_queue(_notion_retry_queue_path())
    except RetryQueueError as exc:
        print(f"  손상된 Retry Queue: {one_line(exc)}")
        attention.append(
            f"Notion Retry Queue 파일을 읽을 수 없다 ({_notion_retry_queue_path()}) — "
            f"Runner는 이 파일을 읽지 못하면 4단계에서 실패한다. 사람이 확인해야 한다"
        )
        entries = []

    print(f"  대기 중 Event       : {len(entries)}")
    if entries:
        ages = [(_queue_age_days(e.added_at, now), e) for e in entries]
        datable = [(age, e) for age, e in ages if age is not None]
        undatable = len(ages) - len(datable)
        attempts = max(e.attempt_count for e in entries)
        print(f"  최대 재시도 횟수    : {attempts}")
        if datable:
            oldest_age, oldest = max(datable, key=lambda pair: pair[0])
            print(
                f"  가장 오래된 항목    : {one_line(oldest.added_at)} "
                f"({oldest_age:.1f}일, event {_authored(oldest.event_id)})"
            )
            if oldest_age >= SILENT_AFTER_DAYS:
                # `SILENT_AFTER_DAYS` reused rather than a new threshold
                # invented — the same choice `_print_last_run()` made, and
                # for a compatible reason. An entry that has survived that
                # many days has survived many scheduled runs, so it is not
                # the outage the queue exists to ride out.
                attention.append(
                    f"Notion Retry Queue에 {oldest_age:.1f}일째 남아 있는 Event가 있다 "
                    f"({_authored(oldest.event_id)}, 재시도 {oldest.attempt_count}회) — "
                    f"일시적 장애라면 이미 빠져나갔을 시간이다. Notion이 영구히 "
                    f"거부하는 요청일 수 있으니 notion_sync.log의 REASON을 확인해야 "
                    f"한다(BACKLOG BUG-13)"
                )
        if undatable:
            # A `added_at` this cannot parse is skipped rather than guessed
            # at, and said out loud — the same treatment `_print_last_run()`
            # gives an unparseable `started_at`.
            print(f"  (added_at을 읽을 수 없는 항목 {undatable}건)")

    try:
        pending_records = load_dashboard_pending(_dashboard_pending_path())
    except DashboardPendingError as exc:
        print(f"  손상된 Dashboard 대기열: {one_line(exc)}")
        attention.append(
            f"Dashboard pending 파일을 읽을 수 없다 ({_dashboard_pending_path()}) — "
            f"`drain_pending()`은 이것을 '비었음'으로 취급하므로 밀린 기록은 "
            f"영원히 재시도되지 않는다. 사람이 확인해야 한다"
        )
        pending_records = []

    print(f"  Dashboard 밀린 기록 : {len(pending_records)}")
    if pending_records:
        print(
            f"  최대 재시도 횟수    : "
            f"{max(r.attempt_count for r in pending_records)}"
        )
        # `PendingDashboardRecord.queued_at` had no reader either — the same
        # sweep, the same shape, and reporting the retry queue's age while
        # ignoring this one would have left the asymmetry inside the block
        # that was just added to remove it. Age matters more here than for
        # the Event queue: a Dashboard record Notion permanently refuses (a
        # Select value it will not accept, say) comes back every run with
        # nothing but `attempt_count` climbing, which is exactly what
        # `DrainPendingResult.last_reason` was added for.
        ages = [
            (age, record)
            for age, record in (
                (_queue_age_days(r.queued_at, now), r) for r in pending_records
            )
            if age is not None
        ]
        if ages:
            oldest_age, oldest = max(ages, key=lambda pair: pair[0])
            print(
                f"  가장 오래된 기록    : {one_line(oldest.queued_at)} "
                f"({oldest_age:.1f}일, run {one_line(oldest.run_id)})"
            )
            if oldest_age >= SILENT_AFTER_DAYS:
                attention.append(
                    f"Dashboard 기록 하나가 {oldest_age:.1f}일째 Notion에 반영되지 "
                    f"못하고 있다 (run {one_line(oldest.run_id)}, 재시도 "
                    f"{oldest.attempt_count}회) — notion_sync.log의 "
                    f"`DASHBOARD DRAIN_PENDING ... REASON`을 확인해야 한다. "
                    # The single most likely REASON, and the one that used to
                    # have no exit: OPS_RUNS grew from 13 columns to 17 across
                    # C32/C33, and a database created before a widening gets
                    # a 400 on every run forever. Pointing at the fix from the
                    # line the operator actually reads is the whole point —
                    # the reason was already legible, the way out was not.
                    f"열이 모자란 Database가 흔한 원인이며 그 경우 "
                    f"docs/13 §3-⑧-4가 고치는 명령이다"
                )

    unexported = _notion_credentials_present_but_unexported()
    if unexported:
        print(
            "  자격증명            : .env에 있으나 이 프로세스에 전달되지 않았다 "
            f"({', '.join(unexported)}) — Notion 단계는 '미설정'으로 건너뛴다"
        )
        attention.append(
            f"Notion 자격증명이 .env에 있는데 환경변수로 전달되지 않았다 "
            f"({', '.join(unexported)}) — 그래서 모든 실행이 Notion 단계를 "
            "'미설정'으로 건너뛰었고 Notion의 Control Tower는 그만큼 오래됐다. "
            ".env는 자동으로 읽히지 않는다(.env.example 머리말); 셸에서 export하거나 "
            "실행 스크립트가 직접 읽어야 한다"
        )

    elif _notion_credentials_exported_but_never_exercised():
        # `elif`: when the credentials are not exported at all, the branch
        # above already says so and says more. Two lines about one
        # configuration would train an operator to read neither.
        print(
            "  자격증명            : 이 프로세스에 전달돼 있으나, 이 자격증명으로 "
            "Notion 단계를 시도한 실행이 아직 없다"
        )
        print(
            "  위 두 숫자          : 아직 아무 실행도 Notion에 닿지 않았으므로 "
            "'정상'의 근거가 아니다"
        )
        attention.append(
            "Notion 자격증명이 전달돼 있지만 그것으로 Notion 단계를 시도한 실행이 "
            "아직 없다 — 위의 '대기 중 Event 0 / Dashboard 밀린 기록 0'은 Notion이 "
            "정상이라는 뜻이 **아니다**. 두 숫자는 실행이 남긴 파일에서 오는데 그런 "
            "실행이 없었다. 토큰이 틀렸거나 Database가 공유되지 않았다면(401/403/404) "
            "이 화면은 그대로 조용하고 Runner만 매번 실패한다. run_company_ops.py를 "
            "한 번 실행해 실제로 도달하는지 확인해야 한다"
        )

    else:
        # Neither missing nor unexercised. If the last run reached Notion and
        # said what it wrote, say so -- see `_last_run_notion_outcome()` for
        # why the good state deserves a line of its own.
        outcome = _last_run_notion_outcome()
        if outcome is not None:
            run_id, created, updated, skipped_old = outcome
            print(
                f"  마지막 Notion 반영  : Row 생성 {created} / 갱신 {updated} / "
                f"넘어감(더 오래된 Event) {skipped_old} (run {one_line(run_id)})"
            )
            if created == 0 and updated == 0:
                # Not an ATTENTION. "Notion is already current" is the normal
                # steady state of a system whose Events stop arriving, and an
                # alarm that fires on the ordinary case is the alarm people
                # stop reading. It is said, and it is not shouted.
                print(
                    "                        (그 실행은 Notion을 바꾸지 않았다 — "
                    "이미 최신이었거나 도착한 Event가 모두 더 오래됐다)"
                )

    attention.extend(_same_instant_skips_from_the_last_run())
    return attention


def _same_instant_skips_from_the_last_run() -> list[str]:
    """E-23's divergence, taken out of the Run Manifest where it was already
    written and read by nobody.

    C40 made the count exist: `app/runner.py` recognises a same-instant skip
    by the note `notion/sync.py` attaches (never by re-deriving the
    comparison, which is why the two answers cannot drift) and records it as
    the `notion_sync` component's `same_instant_skips` metric, absent on a
    run where it did not happen.

    Nothing then read it. `_print_last_run()` prints a component's metrics
    only when the component FAILED — deliberately, so the block stays short —
    and a skip is not a failure: docs/04 §35 calls it "적용하지 않았다", the
    Runner records `recorder.ok()`, the run exits 0. So the number went to
    disk on every affected run and no view has ever shown it.

    **What it means, measured rather than restated.** Two Events of the same
    project at the same instant — the *normal* case, because a Signal with no
    timestamp of its own gets that date's midnight for every Signal of that
    day (docs/06 §12) — driven through the real `ExecutionPlanSync`:

        E1 STARTED  IN_PROGRESS   -> NOTION_CREATED
        E2 BLOCKED  BLOCKED       -> NOTION_SKIPPED_OLD_EVENT

        Notion row   Status IN_PROGRESS   Blocker (none)
        on disk      Status BLOCKED       Blocker "budget"

    E-23 records this as losing "Notion 쪽 Current State의 **최신성**" — the
    View being one Event behind. That is not the whole of it: the row can
    show the *opposite of the risk state*, a blocked project reported as
    healthy, and for anyone reading Notion as a Control Tower that is the one
    number that must not be wrong.

    The skip changes nothing on disk, which is the half that makes this
    reportable at all: the CONTROL TOWER block above reads the same Events
    and shows that project as BLOCKED, so the two blocks of this view
    disagree in public rather than the divergence living only in Notion.
    (Whether the second Event also reaches *Company History* is a different
    rule — the History Filter's — and E-23's own measurement uses KEEP
    Events, where it does.)

    Per run, and it clears by itself: the next run's manifest carries no such
    metric, and C43 measured that one further Event for that project restores
    the row completely. So this is a line about *this* run, not a standing
    alarm — and the action it names is the mitigation AGENT.md §3 already
    documents, giving that day's Signals an explicit `timestamp`.

    The decision that would remove the divergence is E-23's and is not taken
    here: all three candidate fixes change a spec.
    """
    try:
        summary = read_summary(DEFAULT_RUN_SUMMARY_PATH)
    except (RunSummaryError, OSError, ValueError):
        # The manifest's own reader already reports an unreadable manifest in
        # LAST RUN; a second line for the same file would be a second opinion.
        return []
    if summary is None:
        return []
    component = summary.component("notion_sync")
    if component is None or not isinstance(component.metrics, Mapping):
        return []
    skipped = component.metrics.get("same_instant_skips")
    if not isinstance(skipped, int) or skipped <= 0:
        return []
    print(
        f"  같은 instant 미반영 : {skipped} (마지막 실행)"
    )
    return [
        f"마지막 실행에서 Event {skipped}건이 Notion 프로젝트 행에 반영되지 않았다 "
        "— 같은 프로젝트·같은 instant의 두 번째 Event이기 때문이다(docs/04 §29-30의 "
        "\"동시\" 규칙, BACKLOG E-23). **어긋난 것은 Notion 쪽 행뿐이다** — 이 "
        "건너뜀은 디스크의 무엇도 바꾸지 않는다. 그리고 그 행은 한 Event 뒤처진 "
        "정도가 아니라 **상태 자체가 다를 수 있다**(실측: 두 번째 Event가 BLOCKED "
        "였을 때 Notion 행은 IN_PROGRESS에 Blocker 없음 — 위 CONTROL TOWER 블록은 "
        "같은 Project를 BLOCKED로 보여준다). 그 프로젝트에 Event가 하나 더 도착하면 "
        "행이 따라잡는다. 반복된다면 그 날짜 Signal에 `timestamp`를 명시하면 이 "
        "경로를 타지 않는다(AGENT.md §3)"
    ]


def _agent_lock_path() -> Path:
    """The Agent's own lock — NOT the Runner's.

    `agent/agent.py` reuses `scheduler.lock` unchanged but against
    `runtime/agent/locks/agent.lock`, deliberately a different file: the two
    protect different critical sections and run on different machines.
    """
    return _agent_dir() / "locks" / "agent.lock"


def _print_agent(now: datetime) -> list[str]:
    agent_dir = _agent_dir()
    if not agent_dir.exists():
        print("AGENT — 이 머신에는 Agent가 설정되어 있지 않다 (runtime/agent 없음)")
        return []

    snapshot = read_status(
        agent_start_date=_agent_start_date(),
        now=now,
        state_path=agent_dir / "state" / "agent_state.json",
        outbox_dir=agent_dir / "outbox",
        sent_dir=agent_dir / "sent",
        rejected_signals_dir=agent_dir / "signals_rejected",
        signals_dir=agent_dir / "signals",
    )

    print("AGENT — 이 머신의 Agent")
    print("-" * 60)
    # Both are strings taken out of `agent_state.json` with only a type
    # check (`agent/state.load_state()`), so a hand-edited or restored state
    # file can put a line break in either. Same rule as `main()`'s ATTENTION
    # loop, applied where it costs nothing.
    print(f"  desktop_id          : {one_line(snapshot.desktop_id)}")
    print(f"  last_run            : {one_line(snapshot.last_run)}")
    print(f"  마지막 수집 날짜    : {snapshot.last_successful_collection_date}")
    print(f"  미수집 날짜         : {len(snapshot.pending_dates)}")
    if snapshot.pending_dates:
        shown = ", ".join(d.isoformat() for d in snapshot.pending_dates[:7])
        more = " ..." if len(snapshot.pending_dates) > 7 else ""
        print(f"                        {shown}{more}")
    print(f"  outbox (미전송)     : {snapshot.outbox_count}")
    print(f"  sent (전송 완료)    : {snapshot.sent_count}")
    print(f"  거부된 Signal       : {snapshot.rejected_signal_count}")
    # Signals filed where no target date will ever read them (C84).
    # `load_signals()` reads exactly `signals/<YYYY-MM-DD>/*.json`; a
    # `*.json` anywhere else under `signals/` is not queued, it is
    # unreachable. Printed unconditionally, beside the other Signal count,
    # because 0 here is the reassuring answer and this line is where an
    # operator already looks for Signal trouble.
    print(f"  읽힐 수 없는 Signal : {snapshot.unreachable_signal_count}")
    # C95, and the other half of the line above. That one is about a
    # Signal filed where no date will read it; this one is about a
    # Signal filed in a **correct** date directory that the watermark
    # has already passed. Printed unconditionally for the same reason:
    # 0 is the reassuring answer and this is where an operator already
    # looks for Signal trouble.
    print(
        f"  지난 날짜의 미전달  : "
        f"{snapshot.undelivered_closed_signal_count}"
    )

    signal_attention: list[str] = []
    if snapshot.unreachable_signal_count:
        # Measured with the real entrypoint (C84): the same Signal content
        # filed four ways, one run.
        #
        #     signals/2026-08-21/s.json   COLLECTED and delivered
        #     signals/toplevel.json       never read
        #     signals/2026-8-21/s.json    never read  (unpadded month/day)
        #     signals/august-21/s.json    never read
        #
        # The three that were never read were not moved, not rejected, not
        # logged. The run reported COMPLETED with exit 0, and the watermark
        # advanced **past** the date the work belonged to, so no later run
        # reconsiders it. Every field of the snapshot said all-clear:
        # `rejected_signal_count=0`, `outbox_count=0`, `pending_dates=()`.
        #
        # Signal authoring is by hand today (BACKLOG A-11), so filing one a
        # directory too high is the ordinary mistake rather than an exotic
        # one, and what is lost is something a person typed.
        #
        # Reported, never repaired: collecting such a file, or moving it to
        # `signals_rejected/`, decides what a misfiled Signal *means*, and
        # that is a decision (BACKLOG). This says only that it is there.
        signal_attention.append(
            f"어느 날짜로도 수집되지 않는 Signal {snapshot.unreachable_signal_count}건이 "
            f"`signals/` 에 있다 — Agent는 `signals/<YYYY-MM-DD>/*.json` 만 "
            f"읽는다. 그 밖의 파일은 전달도 거부도 로깅도 되지 않으며, "
            f"수집 날짜가 이미 그 날짜를 지나갔다면 어느 실행도 다시 보지 "
            f"않는다. 사람이 날짜 디렉토리로 옮긴 뒤 다시 실행해야 한다"
        )

    if snapshot.undelivered_closed_signal_count:
        # Measured with the real entrypoint (C95), and unlike the alert
        # above this one needs **no mistake by anybody**:
        #
        #     08:00  the scheduled run collects 2026-08-23
        #     09:00  the person writes up the afternoon into
        #            signals/2026-08-23/afternoon.json
        #     09:00  run 2, and two more runs on later days: COMPLETED,
        #            delivered stays 1
        #
        # `pending_dates()` ends at yesterday and never walks backwards
        # (docs/07 section 50), so once the watermark reaches a date,
        # nothing added to it is ever read again. The file was not
        # delivered, not rejected, not logged; outbox 0, rejected 0,
        # unreachable 0, pending_dates (), needs_attention (). Work a
        # person typed, with every diagnostic reading all-clear.
        #
        # Writing up yesterday after this morning's run is not an
        # exotic operation. It is the shape of an ordinary working day.
        #
        # Reported, never repaired: re-reading a closed date would
        # re-derive Events the Collector has already seen, and
        # `pending_dates()`' refusal to walk backwards is a deliberate
        # rule. What a late Signal *means* is a decision (BACKLOG).
        signal_attention.append(
            f"수집이 끝난 날짜에 미전달 Signal "
            f"{snapshot.undelivered_closed_signal_count}건이 있다 — 디렉토리 "
            f"이름도 파일 이름도 올바르지만, 그 날짜는 이미 수집이 "
            f"끝나서 어느 실행도 다시 읽지 않는다(다시 수집하려면 사람이 "
            f"아직 수집되지 않은 날짜로 옮겨야 한다). 전달도 거부도 로깅도 "
            f"되지 않았다"
        )

    # BACKLOG E-9/E-9b: `sent/` records that `transport.send()` did not
    # raise — not that the Event reached the sync folder in readable form.
    # `send()` skips writing when the destination already exists in ANY
    # shape, and the OneDrive client produces such shapes on its own
    # (Files On-Demand placeholders are 0 bytes, interrupted transfers
    # truncate). Measured: an Event filed as sent while the destination
    # stayed 0 bytes, with the collection date advanced past it and no
    # warning anywhere.
    #
    # A MISSING destination is not reported — that is what a normally
    # consumed Event looks like. Only a destination that is present and is
    # not the Event is reportable, and all four such shapes were measured
    # to be distinguishable from both clean cases.
    #
    # Reported, never re-sent: re-sending means overwriting a sync-folder
    # entry, which is exactly the race E-9 is blocked on.
    delivery_attention: list[str] = []
    sync_folder = os.environ.get("COMPANY_OPS_AGENT_SYNC_FOLDER")
    if sync_folder:
        delivery = find_undelivered_events(
            sent_dir=agent_dir / "sent", sync_folder=Path(sync_folder)
        )
        # Three outcomes, not two. `is_clean` now also covers records this
        # scan could not read, and "UNDELIVERED" would be the wrong word for
        # those — it claims a delivery verdict the scan explicitly does not
        # have. Same distinction `_print_history()` already draws between
        # `reconciliation.orphaned` and `reconciliation.unreadable`.
        if delivery.undelivered:
            verdict = "UNDELIVERED"
        elif delivery.unreadable_records:
            verdict = "UNKNOWN"
        else:
            verdict = "OK"
        print(
            f"  전달 정합성         : {verdict} "
            f"(확인 {delivery.checked}건, 이미 수거됨 {delivery.absent}건"
            + (
                f", 읽을 수 없음 {len(delivery.unreadable_records)}건"
                if delivery.unreadable_records
                else ""
            )
            + ")"
        )
        for item in delivery.undelivered[:5]:
            # Same rule, same origin: this `event_id` is read back out of a
            # file in `sent/` and is not constrained to one line.
            print(f"                        ! {_authored(item.event_id)} [{item.problem}]")
        if delivery.undelivered:
            delivery_attention.append(
                f"전송 완료로 기록됐지만 sync 폴더에 도착하지 않은 Event "
                f"{len(delivery.undelivered)}건: "
                f"{', '.join(_authored(i.event_id) for i in delivery.undelivered[:5])} — "
                f"자동 재전송되지 않는다(BACKLOG E-9). 사람이 확인해야 한다"
            )
        # The sibling of the `reconciliation.unreadable` line in
        # `_print_history()`, and it did not exist. A damaged file in
        # `sent/` was dropped by `find_undelivered_events()` without a
        # count — so that Event's delivery went unchecked and this section
        # printed OK. `event_id` cannot be quoted here for the obvious
        # reason: it is what could not be read. The filename can, and it is
        # what a person has to open.
        if delivery.unreadable_records:
            delivery_attention.append(
                f"읽을 수 없는 전송 기록 {len(delivery.unreadable_records)}건 "
                f"(runtime/agent/sent/): "
                f"{', '.join(r.sent_record.name for r in delivery.unreadable_records[:5])}"
                f"{' 외' if len(delivery.unreadable_records) > 5 else ''} — 해당 "
                f"Event가 sync 폴더에 도착했는지 판단할 수 없다. 사람이 확인해야 한다"
            )
    else:
        print("  전달 정합성         : 확인 불가 (COMPANY_OPS_AGENT_SYNC_FOLDER 미설정)")
        # Printed and, until C146, raised nothing — while this file raises
        # ATTENTION for the two other "확인 못 함" cases it has (the Local
        # Master listing, the backup remote's secret history), on the stated
        # ground that *"이것은 '없음'이 아니라 '확인 못 함'이다"*.
        #
        # It matters more here than in either of those. This check exists
        # because BACKLOG E-9b measured the failure it is the only detector
        # for, end to end on a real Agent run:
        #
        #     sync folder file                  still 0 bytes -- never delivered
        #     agent/sent/                       contains the event_id
        #     last_successful_collection_date   advanced past that date
        #     Agent exit code / log             0 / COLLECTED
        #     any warning anywhere              none
        #
        # An unset environment variable turns that detector off, and the one
        # line saying so sat in the body of the report rather than in the
        # section an operator reads for what needs doing.
        #
        # Guarded on `sent_count`, not raised unconditionally: a Desktop that
        # has never delivered an Event has nothing for this check to verify,
        # and a standing alarm there is the alert-that-cannot-clear this file
        # keeps removing (C26). With deliveries on disk the sentence is
        # exact — these are records of Events this machine reported as
        # delivered, and nothing here can say whether any of them arrived.
        if snapshot.sent_count:
            delivery_attention.append(
                f"전달 정합성을 확인할 수 없다 — 이 머신은 Event "
                f"{snapshot.sent_count}건을 전송 완료로 기록했지만 "
                f"COMPANY_OPS_AGENT_SYNC_FOLDER가 없어 그중 무엇이 실제로 "
                f"도착했는지 검사하지 못한다(BACKLOG E-9/E-9b). 이것은 "
                f"'문제 없음'이 아니라 '확인 못 함'이다"
            )
    # Unconditional on the count, because the count is exactly what is
    # meaningless without it — and the previous condition made this line
    # unreachable.
    #
    # `read_status()` computes `pending_dates` ONLY when `agent_start_date`
    # is supplied ("since a first-ever run has no other way to know where
    # counting starts (docs/07 §50: never guessed)"), and this block passes
    # it `_agent_start_date()`. So `snapshot.pending_dates` non-empty already
    # implies the variable is set, and `and _agent_start_date() is None` made
    # the whole condition a contradiction.
    #
    # What that cost is the thing this line exists to prevent. A Desktop with
    # the variable unset printed
    #
    #     미수집 날짜         : 0
    #
    # which is byte-identical to a Desktop that is fully caught up — the
    # absence of a measurement reading as a healthy measurement, on the
    # machine that PRODUCES Company History. Measured, with the variable
    # unset and `last_successful_collection_date` five days back: `0`, and
    # nothing else.
    #
    # `_print_history()`'s sibling for `COMPANY_OPS_HISTORY_START_DATE` has
    # always been written the right way round (`if history_start is None:`),
    # which is the shape copied here.
    if _agent_start_date() is None:
        print("  (COMPANY_OPS_AGENT_START_DATE 미설정 — 미수집 날짜는 계산되지 않음)")

    # The same two lock conditions `_print_last_run()` reports for the
    # Runner, against the Agent's own lock file — which nothing reported.
    #
    # The asymmetry mattered more than it looked. A stuck Runner lock stops
    # Desktop 4; a stuck Agent lock stops a Desktop that *produces* Company
    # History, and `run_agent.py` returns **exit 0** for
    # `SKIPPED_ALREADY_RUNNING` (its own module docstring: "0 COMPLETED, or
    # skipped because another Agent run holds the lock"). So Task Scheduler
    # records success on every run while the Agent collects nothing.
    #
    # Measured: a lock file recording a dead pid, made read-only —
    # `stale_lock_cannot_be_cleared()` returns True, and the AGENT section
    # printed nothing about it. The only trace was `needs_attention()`'s
    # "이 머신의 Agent가 N일째 실행되지 않았다", which needs N days to appear
    # and names a symptom rather than the cause.
    #
    # Read-only, decides nothing, takes nothing — `is_locked()` /
    # `lock_held_since()` / `stale_lock_cannot_be_cleared()` are the
    # non-competing readers, and this script promises it is safe to run
    # while an Agent works.
    lock_attention: list[str] = []
    agent_lock = _agent_lock_path()
    if stale_lock_cannot_be_cleared(agent_lock):
        print("  Agent Lock          : 남아 있으나 제거할 수 없음 (읽기 전용)")
        lock_attention.append(
            f"Agent Lock 파일이 남아 있고 제거할 수 없다 ({agent_lock}) — 기록된 "
            f"프로세스는 이미 종료됐지만 파일이 읽기 전용이라 어떤 실행도 인수하지 "
            f"못한다. 모든 Agent 실행이 '다른 Agent 실행 중'으로 조용히 건너뛰어지고 "
            f"exit code는 0이다. 사람이 파일을 확인해 지워야 한다"
        )
    else:
        agent_held_since = lock_held_since(agent_lock)
        if agent_held_since is not None:
            reference = now if agent_held_since.tzinfo else now.replace(tzinfo=None)
            held_hours = (reference - agent_held_since).total_seconds() / 3600
            print(
                f"  Agent Lock          : 보유 중 (획득 "
                f"{agent_held_since.isoformat(timespec='seconds')})"
            )
            if held_hours >= LOCK_STUCK_AFTER_HOURS:
                lock_attention.append(
                    f"Agent Lock이 {held_hours:.1f}시간째 잡혀 있다 (획득 "
                    f"{agent_held_since.isoformat(timespec='seconds')}) — 실제로 긴 "
                    f"실행이 진행 중이거나, 죽은 Agent의 PID가 재사용돼 Lock이 영구히 "
                    f"잡힌 것으로 보이는 상태다. 확인이 필요하다"
                )

    return (
        list(snapshot.needs_attention(now))
        + signal_attention
        + delivery_attention
        + lock_attention
    )


def _event_day(iso: str | None) -> date | None:
    """`Coverage.evidence_from` / `evidence_to` as a `date`, or None.

    **This reads a date, not a timestamp**, and the difference cost four
    tests in C135. `dashboard._coverage()` builds both fields as
    `min(days).isoformat()` over values that already went through
    `_evidence_day()` -- so they arrive as `"2026-08-01"`, and the Seoul
    conversion has already happened one layer down. Converting again here is
    not merely redundant: `datetime.fromisoformat("2026-08-01")` is *naive*,
    `businessdate.business_date()` refuses a naive value on purpose, and the
    refusal landed in the `except` below as `None`. The caller reads that
    `None` as "there is no evidence to compare against" and printed the
    "증거 범위 밖" qualifier over a perfectly healthy tree, naming the same
    date on both sides of the sentence.

    Both shapes are accepted, because both have been true of this seam: a
    bare date is returned as itself, and a timestamp is converted to its
    Seoul day the way every other date in this file is. The date is tried
    first -- `date.fromisoformat` rejects a full timestamp, so the two
    branches cannot both claim the same value.
    """
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        pass
    try:
        return businessdate.business_date(datetime.fromisoformat(iso))
    except (TypeError, ValueError):
        return None


def _company_history_older_than_the_evidence(
    daily_dir: Path, earliest_event
) -> "tuple[date | None, bool]":
    """`(earliest uncovered day or None, whether the question was answered)`.

    **The second element is why this returns a pair (C68).** Both failures
    below used to return `None`, which is also what "checked, and there is no
    gap" returns, so `Coverage.complete` came back True for a tree whose
    Company History nobody could read:

        the directory cannot be listed      permissions, a moved path
        a Daily file cannot be opened       one bad file is enough

    Measured on one tree — 18 days of history with work in it, evidence
    starting later — with the reads made to fail: gap `None`, `complete`
    True, and the screen printed nothing at all. Readable, the same tree
    gives gap `2026-08-01` and a printed qualifier.

    A **missing** directory is not that case and is reported as answered: a
    machine with no `local_master/daily/` has no Company History for the
    evidence to fail to cover, which is a real answer rather than a failure
    to look. `FileNotFoundError` is separated from the rest for exactly that
    reason — the same split `controltower.read_events()` makes.

    **Why this can happen at all, and why it is not a bug to fix here.**
    `runtime/events/processed/` is Execution Evidence (docs/14 §2) and Backup
    scope is `daily/` and `monthly/` only (docs/08 §26). So a machine restored
    from the remote gets its whole Company History back and **none of its
    Events** — and the Control Tower, which reads that directory and nothing
    else, then answers `Event 0건 / 움직인 Project 0 / 모든 Team 활동 없음`.

    Measured on a restored-shaped tree, 18 days of Daily History with work in
    them: exactly that, with nothing on screen distinguishing it from a
    company that did nothing. B-6's retention decision produces the same
    shape deliberately.

    So this is a **qualifier, not an alert**. There is no action that brings
    the Events back, and a standing alarm nobody can clear is the thing this
    file keeps removing. What the caller does with it is add "the numbers
    below only cover what evidence is left" — the same treatment as an
    unreadable file.

    The comparison is against the earliest Daily that **carries work**, not
    the earliest Daily file: `generate_daily_history()` writes a file for an
    empty day too (docs/09 §72), so a `history_start_date` earlier than the
    first Event is ordinary and must not read as missing evidence. Ids are
    read with `_rendered_event_ids()` — the same reader the Monthly checks
    use, so "an item" means one thing.

    Cheap by construction: it stops at the first Daily that has work, which
    on any tree is at most a few reads.
    """
    # Listability is asked first and separately, because `_daily_dates()`
    # answers `[]` to both "empty" and "cannot look" — and those are the two
    # this function now has to tell apart. One extra `scandir` on a directory
    # this function was already walking; the docstring's "cheap by
    # construction" still holds.
    try:
        os.scandir(daily_dir).close()
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False

    checked = True
    for day in _daily_dates(daily_dir):
        if earliest_event is not None and day >= earliest_event:
            return None, checked
        try:
            text = (daily_dir / f"{day.isoformat()}.md").read_text(encoding="utf-8")
        except (OSError, ValueError):
            # Skipped as before — one unreadable day must not stop the scan —
            # but no longer in silence. A gap found after this point is still
            # a real gap; a *clean* answer after it is not one this function
            # is entitled to give.
            checked = False
            continue
        if _rendered_event_ids(text):
            return day, checked
    return None, checked


# How many rows of 최근 활동 / 최근 완료 this block prints.
#
# Smaller than `rollup.RECENT_LIMIT`, which bounds the *model*. The panel is
# what a projection consumes; this is a glance in a terminal that already
# prints six other sections, and a screen where one section can push the
# ATTENTION block off the top is a screen nobody scrolls back up.
#
# The true total is printed beside the label whenever it is larger, so five
# lines can never read as "five things happened" — the same rule
# `of_total` / `truncated` follow on the rows.
_RECENT_ON_SCREEN = 5

# The same rule, applied to the block the rule above exists to protect (C71).
#
# `_RECENT_ON_SCREEN`'s own note says a section that can push the ATTENTION
# block off the top is "a screen nobody scrolls back up". ATTENTION was the
# one list with no such bound: `_print_control_tower()` appended one message
# per `RISKS` row, and RISKS carries one row per role-mismatched **Event**.
#
# Measured — mismatched Events against ATTENTION lines:
#
#      1 event  ->   3 lines        60 events  ->  62 lines
#     10 events ->  12 lines      1,000 events -> ~1,002 lines
#
# Linear, and the trigger is ordinary rather than exotic: one Desktop
# configured with the wrong `role` makes **every** Event it sends a mismatch
# (`validate_event()` checks the two fields separately and never the pair).
# The section that exists to say "사람이 지금 할 일" then becomes unreadable
# exactly when there is most to do.
#
# Not a new policy — the same number and the same "총 N건" disclosure the
# loop above already uses, so five lines can never read as "five things".
_RISKS_IN_ATTENTION = _RECENT_ON_SCREEN


def _comparable(reference: datetime, other: datetime) -> datetime:
    """`reference`, made safe to subtract `other` from.

    Python raises `TypeError` on **any** comparison or subtraction between a
    naive and an aware datetime, and this view reads timestamps back out of
    files it does not own — a Run Manifest, a lock file, a retry-queue
    entry. Each is written with an offset by this system and each can arrive
    without one: hand-edited (docs/11 permits it), restored from a machine
    whose clock had no zone, or written by an older build. The exception
    would then come out of the tool an operator opens **because** something
    already looks wrong.

    One helper, three callers. It was five lines copied three times
    (`_queue_age_days`, the Runner-lock age, the last-run age), and each
    copy's docstring said "the same guard X uses for the same reason" —
    which is the shape C28 names and `DuplicatedRulesStayInStepTests` exists
    to catch. Prose saying two things are the same is not the same as their
    being one thing, and branch coverage showed it: the second arm — an
    aware stored value against a naive reference — had never run in any of
    the three.

    Both directions matter and they resolve oppositely. A naive stored value
    drags the reference down to naive, because there is no offset to invent.
    A naive **reference** is lifted to aware instead, because the caller's
    `now` is a local wall clock -- and docs/06 section 9 says which wall this
    project reads: Asia/Seoul. It used to lift with a bare `.astimezone()`,
    which reads the *machine's* clock zone; on a Runner that was not in Seoul
    that put the reference up to nine hours from where every other date in
    this file sits, and did it silently (C135). `businessdate.clock_date()`
    reads a naive `now` the same way, for the same stated reason.

    The fourth copy of this rule lives on `AgentStatusSnapshot`, where it is a
    method on the value it guards rather than a helper over two of them.
    """
    if other.tzinfo is None:
        return reference.replace(tzinfo=None)
    if reference.tzinfo is None:
        return reference.replace(tzinfo=businessdate.KST)
    return reference


def _column(text: object, width: int) -> str:
    """One table cell: padded to `width`, and **always** followed by a space.

    The space is the point. `f"{value:<26}"` written straight against the
    next column reads correctly only while every value is shorter than its
    field, and silently runs the two together the moment one is not.
    Measured on the live tree (C135), the CONTROL TOWER Project table:

        COMPANY_OPS   COO/CTO Frontend/CTO Backend/CMOEvent 11  IN_PROGRESS

    `COO/CTO Frontend/CTO Backend/CMO` is 32 characters in a 26-wide field,
    so `CMO` and `Event` became one token and an operator cannot tell where
    the team list ends. It is the row for `COMPANY_OPS` — the project every
    Desktop reports on, so the most-populated row and the one that overflows
    first. Every day, on the panel this file exists to print.

    Padded rather than truncated: cutting the cell to fit would drop a team
    name, and a view that quietly shows three of four teams is the failure
    this file keeps removing (C77's two disagreeing numbers, C85's sentence
    that stopped at the reassuring half). A wide row is ugly; a wrong one is
    not recoverable by looking harder.
    """
    return f"{text:<{width}} "


def _print_control_tower(now: datetime) -> list[str]:
    """CONTROL TOWER — the business layer, which no view had.

    Every other block in this file is operational: is the pipeline healthy,
    is each Desktop reporting, did the last run succeed. None of them answers
    what a Control Tower is for — which projects are moving, which have
    stopped, which are blocked and on what, which team is silent, what
    completed. `controltower/rollup.py` derives all of that from the Events
    already in `runtime/events/processed/`, and this prints it.

    **Through the Dashboard Model, not around it.** `controltower/dashboard.py`
    arranges that rollup into the panels a Control Tower has, and this block
    renders those panels. The alternative — reaching into the rollup field by
    field here — is what it used to do, and it left "what the Control Tower
    shows" existing only as terminal output: a Notion projection of the same
    view would have had to derive it a second time from the same rollup, and
    two derivations of one view is how a screen and a projection start
    disagreeing about the same day. The KPI counts are the one thing still
    read off the rollup (`rollup.metric()`), and a test holds the panel's
    value for every key to that same number.

    **Numbers here are traceable by construction.** Every rollup carries the
    `event_id` and the file it was counted from, and the blocker lines quote
    both — "왜 이 숫자가 나오는가"는 파일 하나를 여는 것으로 끝난다.

    What raises ATTENTION, and what deliberately does not:

        열린 Blocker    yes. docs/02 makes a `BLOCKED` Event carry a
                        human-written `blocker`, and nothing in the pipeline
                        ever clears it — only a RESUMED / ISSUE_RESOLVED /
                        COMPLETED Event from a person does. That is exactly
                        this section's admission rule ("사람이 지금 할 일이
                        있는 것"), and it clears the moment the team reports.
                        No threshold is invented: an open blocker is open.
        조용한 Team     **no.** `source` -> `role` is 1:1 (docs/02 §8), so a
                        silent team is the silent Desktop the COMPANY block
                        already reports. Two lines for one fact is the second
                        opinion this project keeps removing; the count is
                        printed here and the alert stays there.
        읽을 수 없는 파일  **no.** The HISTORY block's Candidate 정합성 line
                        already names them. Printed here only as the reason
                        the numbers below are short of the truth.
    """
    rollup = build_company_rollup(
        processed_dir=RUNTIME_DIR / "events" / "processed", now=now
    )
    # Rendered from the Dashboard Model rather than from the rollup directly.
    # The screen and any projection of this block (a Notion Control Tower
    # above all) then read the *same* arrangement of the same facts, so
    # "what is on the screen" and "what leaves the machine" cannot drift into
    # two derivations -- which is the one failure a second consumer of a
    # rollup reliably produces.
    model = build_dashboard(rollup, now=now)
    attention: list[str] = []

    def _rows(key: str):
        panel = model.panel(key)
        return panel.rows if panel is not None else ()

    print("CONTROL TOWER — Company / Team / Project")
    print("-" * 60)

    if model.unreadable:
        # Not an alert of its own — see the docstring. Said here because it
        # is the one thing that makes every number below a lower bound.
        print(
            f"  집계 대상           : Event {model.events_read}건 (전체 기간, "
            f"읽지 못한 파일 {len(model.unreadable)}건 — 아래 숫자는 그만큼 적다)"
        )
    else:
        print(f"  집계 대상           : Event {model.events_read}건 (전체 기간)")

    # One Event that arrived as two files is counted once (C50), and that is
    # said out loud for the same reason `unreadable` is: a number that
    # silently differs from the file count in the directory is one nobody can
    # check. Not an alert — the pipeline did the right thing and there is
    # nothing for a person to do. The half that IS actionable, two files
    # claiming one `event_id` with different contents, comes through the
    # RISKS panel below like every other risk.
    if model.coverage.duplicates:
        print(
            f"  중복 파일           : {model.coverage.duplicates}건 (같은 event_id를 "
            "가진 파일이 processed/ 에 둘 이상 있다 — 위 숫자는 Event당 한 번만 "
            "센다)"
        )

    project_rows = _rows("PROJECTS")

    # Company History can outlive the evidence the Control Tower reads — see
    # `_company_history_older_than_the_evidence()`. Said as a qualifier on the
    # numbers rather than as an alert: nothing brings those Events back.
    #
    # The evidence range comes off the model (`coverage`), the Company History
    # side is read here, and the answer goes **back into the model** rather
    # than only onto the screen. That is the same rule the rest of this block
    # follows: a projection of this Control Tower needs the qualifier as much
    # as the terminal does, and deriving it twice is how the two would start
    # disagreeing about which days are covered.
    earliest_event = _event_day(model.coverage.evidence_from)
    older, history_readable = _company_history_older_than_the_evidence(
        RUNTIME_DIR / "local_master" / "daily", earliest_event
    )
    model = model.with_history_coverage(older, checked=history_readable)
    if not history_readable:
        # Said out loud rather than folded into the numbers. Without this
        # line the screen is byte-identical to a healthy one, which is the
        # whole defect: `complete` went back to True and nothing above it
        # changed. Not an ATTENTION — see this block's docstring on what is
        # admitted there — but the operator has to know the qualifier below
        # could not be computed.
        print(
            "  Company History     : 읽을 수 없다 — 아래 '증거 범위 밖' 판정을 "
            "하지 못했으므로 이 숫자들이 전부인지 확인되지 않았다 "
            "(local_master/daily 접근 실패)"
        )
    if model.coverage.history_uncovered_from is not None:
        print(
            f"  증거 범위 밖        : Company History는 "
            f"{model.coverage.history_uncovered_from}부터 일을 "
            f"기록하는데 이 계층이 읽는 Event는 "
            + (
                "하나도 남아 있지 않다"
                if model.coverage.evidence_from is None
                else f"{model.coverage.evidence_from}부터다"
            )
            + " — 위 숫자는 그 뒤만 덮는다 (`processed/`는 Backup 범위가 아니다, "
            "docs/08 §26)"
        )

    def _value(key: str) -> int:
        # Read off the rollup rather than out of the model's METRICS panel,
        # and the two are the same number by construction — `_metrics_panel()`
        # is built from `rollup.metrics` and nothing else.
        # `TheScreenAndThePayloadCarryTheSameFactsTests` asserts that equality
        # for every key, which is a stronger statement than "both call the
        # same accessor": it would still fail if the panel started deriving
        # its own.
        metric = rollup.metric(key)
        return metric.value if metric is not None else 0

    completed = _value("projects_completed")
    print(
        f"  움직인 Project      : {len(project_rows)}"
        + (f" (완료 {completed})" if completed else "")
    )

    # Two project_ids, one Company History heading (C90). Printed here
    # because this is the line whose number disagrees with Monthly's.
    shared_headings = _projects_sharing_one_history_heading(rollup)
    if shared_headings:
        total = sum(len(group) for group in shared_headings)
        print(
            f"  한 제목을 공유       : {total}개 project_id가 "
            f"{len(shared_headings)}개 제목으로 합쳐진다"
        )
        attention.append(
            f"서로 다른 project_id {total}개가 Company History에서 "
            f"{len(shared_headings)}개 제목으로 합쳐진다: "
            + "; ".join(
                " = ".join(_authored(pid) for pid in group)
                for group in shared_headings[:3]
            )
            + (" 외" if len(shared_headings) > 3 else "")
            + " — 제목은 `project_id`를 `.title()`로 표시하므로 대소문자·"
            "underscore만 다른 id는 한 제목이 된다. Event는 유실되지 않지만 "
            "**Control Tower와 Monthly History가 Project 수를 다르게 센다** "
            "(실측: Control Tower 3, Monthly 1). 한 철자로 통일해야 한다"
            "(BACKLOG)"
        )

    print(
        f"  Milestone/Decision/Issue: {_value('milestones_completed')} / "
        f"{_value('decisions_approved')} / {_value('issues_resolved')}"
    )
    print(f"  열려 있는 Blocker   : {_value('open_blockers')}")

    # No `if rows:` guard, and none below for Desktops: both folds seed every
    # entry in docs/02 §8's table and return it silent rather than absent.
    # That is the point of them -- "물어봤고 없다" and "묻지 않았다"
    # are different sentences, and only the first is true here. Branch
    # coverage found the guards; they had no other side to take.
    print("  Team")
    for row in _rows("TEAMS"):
        values = row.values
        silent = "" if values["has_activity"] else "   (이 기간 활동 없음)"
        blocked_count = values["blocked_project_count"]
        blocked = f"  막힌 Project {blocked_count}" if blocked_count else ""
        print(
            f"    {one_line(values['display_name']):<14} Event {values['events']:<4}"
            f"Project {len(values['projects'])}{blocked}{silent}"
        )

    # The layer under Team, and the reason both are here: `source` -> `role`
    # is 1:1 while every Event obeys docs/02 §8, so Team and Desktop are the
    # same partition — right up to the moment an Event says otherwise, which
    # is when having only one of them hides it.
    print("  Desktop")
    for row in _rows("DESKTOPS"):
        values = row.values
        silent = values["days_silent"]
        when = (
            f"마지막 {one_line(values['last_seen'])}"
            + (f" ({silent}일 전)" if silent else "")
            if values["last_seen"]
            else "이 기간 Event 없음"
        )
        mismatches = values["role_mismatches"]
        bad = f"  ! role 어긋남 {mismatches}" if mismatches else ""
        print(
            f"    {_column(one_line(values['source']), 11)}"
            f"{_column(one_line(values['display_name']), 13)}"
            f"Event {_column(values['events'], 3)}"
            f"Project {_column(len(values['projects']), 2)}{when}{bad}"
        )

    if project_rows:
        print("  Project")
        # The model already ordered these: blocked first, then by how long
        # they have been quiet — the two questions an operator actually opens
        # this block with. Ordering it here as well would be the second
        # derivation this refactor exists to remove.
        shown = project_rows[:_CONTROL_TOWER_PROJECT_LINES]
        for row in shown:
            values = row.values
            state_key = values["state"]
            marker = "!" if state_key == "BLOCKED" else " "
            teams = "/".join(
                one_line(ROLE_DISPLAY_NAMES.get(role, role)) for role in values["teams"]
            )
            if state_key == "BLOCKED":
                days = values["days_blocked"]
                state = "BLOCKED" + (f" {days}일째" if days is not None else "")
            elif state_key == "COMPLETE":
                state = f"완료 {one_line(values['completed_at'])}"
            else:
                idle = values["days_idle"]
                # The project's own last reported `status`, not a derived
                # word: "created and never moved" (NOT_STARTED) and "in
                # flight" (IN_PROGRESS) are different facts and only the
                # Event says which.
                state = (
                    f"{one_line(values['status'])} 마지막 {one_line(values['last_seen'])}"
                    + (f" ({idle}일 전)" if idle else "")
                )
            print(
                f"    {marker} {_column(_authored(values['project_id']), 20)}"
                f"{_column(teams, 25)}"
                f"Event {_column(values['events'], 3)}{state}"
            )
        if len(project_rows) > len(shown):
            print(f"      외 {len(project_rows) - len(shown)}건")

    # 최근 활동 / 최근 완료 — the two panels that are a *list of Events*
    # rather than a fold over them, and the two the request asks for by name.
    #
    # On the screen and **not** in Notion, which is the opposite of every
    # other panel here and is deliberate: a Notion database keyed by
    # `event_id` grows one row per Event forever (this repository does not
    # delete) and its reconciliation stops working past 1,000 rows, where
    # `list_pages()` truncates. `notion_projection.UNPROJECTED_PANELS`
    # carries that reasoning and the tests measure it. The terminal has no
    # such problem: it re-renders from scratch every run.
    #
    # Short on purpose. This block is a glance, not a log — the panels
    # themselves stop at `RECENT_LIMIT` and this prints fewer still, with the
    # true total beside it so five lines cannot read as "five things
    # happened". Whoever wants the rest opens `processed/`.
    for key, label in (("ACTIVITY", "최근 활동"), ("COMPLETIONS", "최근 완료")):
        rows = _rows(key)
        if not rows:
            continue
        shown = rows[:_RECENT_ON_SCREEN]
        total = shown[0].values["of_total"]
        suffix = f" (총 {total}건)" if total > len(shown) else ""
        print(f"  {label}{suffix}")
        for row in shown:
            values = row.values
            print(
                f"    {one_line(values['at'])}  {one_line(values['source']):<11}"
                f"{one_line(values['event_type']):<20} "
                f"{_authored(values['project_id'])} — {_authored(values['summary'])}"
            )

    # Bounded per kind rather than over the whole panel: an open-blocker
    # flood and a role-mismatch flood are different problems, and letting one
    # crowd the other out would trade the old failure for a subtler one.
    # Original row order is preserved — the overflow lines are appended after
    # the loop rather than interleaved.
    _risk_totals: dict[str, int] = {}
    for _row in _rows("RISKS"):
        _kind = _row.values["kind"]
        _risk_totals[_kind] = _risk_totals.get(_kind, 0) + 1
    _risk_shown: dict[str, int] = {}

    for row in _rows("RISKS"):
        values = row.values
        _seen = _risk_shown.get(values["kind"], 0) + 1
        _risk_shown[values["kind"]] = _seen
        if _seen > _RISKS_IN_ATTENTION:
            continue
        if values["kind"] == "OPEN_BLOCKER":
            days = values["days_open"]
            age = f"{days}일째 " if days is not None else ""
            attention.append(
                f"{age}막혀 있는 Project: {_authored(values['project_id'])} "
                f"[{one_line(ROLE_DISPLAY_NAMES.get(values['team'], values['team']))}] — "
                f"{_authored(values['blocker'])} "
                f"(증거 {_authored(row.evidence[0].describe())}) — "
                "Blocker는 파이프라인이 스스로 지우지 않는다. 그 팀이 RESUMED / "
                "ISSUE_RESOLVED / COMPLETED를 보고할 때까지 열려 있다"
            )
        elif values["kind"] == "EVENT_ID_CONFLICT":
            attention.append(
                f"같은 event_id를 두고 내용이 다른 파일이 둘 있다: "
                f"{_authored(values['event_id'])} — Control Tower는 "
                f"{_authored(values['kept'])}를 세었고 "
                f"{_authored(values['ignored'])}는 세지 않았다. 둘 중 하나는 "
                "자기가 말하는 그 Event가 아니며, 어느 쪽을 셀지는 파일 이름 "
                "순서가 정한다 — 두 파일을 열어 보고 아닌 쪽을 치워야 한다"
            )
        else:
            attention.append(
                f"Desktop과 role이 어긋난 Event: {_authored(values['event_id'])} — "
                f"{one_line(values['source'])}에서 왔는데 role은 "
                f"{one_line(values['claimed_role'])}이라고 말한다(docs/02 §8은 그 Desktop을 "
                f"{one_line(values['expected_role'])}로 정한다). 증거 "
                f"{_authored(row.evidence[0].describe())}. **이 Event의 작업은 위 Team "
                f"집계와 Desktop 집계에서 서로 다른 곳으로 간다** — Notion PROJECTS 행도 "
                f"Owner와 Source가 서로 다른 Desktop을 가리킨다. `validate_event()`는 두 "
                f"필드를 각각만 검사하고 짝은 검사하지 않으므로 손으로 쓴 Event나 복원된 "
                f"파일이 이 모양이 될 수 있다. 거부하지 않는 이유와 필요한 결정은 BACKLOG"
            )

    # One line per kind that was cut, naming the true total. Never silent:
    # a shorter list that does not say it is shorter is the failure this
    # file spends most of its length avoiding.
    for kind, total in sorted(_risk_totals.items()):
        if total <= _RISKS_IN_ATTENTION:
            continue
        hidden = total - _RISKS_IN_ATTENTION
        if kind == "ROLE_MISMATCH":
            attention.append(
                f"Desktop과 role이 어긋난 Event 총 {total}건 — 위 "
                f"{_RISKS_IN_ATTENTION}건 외 {hidden}건은 같은 종류다. 한 Desktop의 "
                "role 설정이 잘못되면 그 Desktop이 보내는 모든 Event가 여기 들어오므로, "
                "건별로 보기 전에 CONTROL TOWER의 Desktop 집계에서 한 source에 몰려 "
                "있는지 먼저 확인하라"
            )
        elif kind == "OPEN_BLOCKER":
            attention.append(
                f"막혀 있는 Project 총 {total}건 — 위 {_RISKS_IN_ATTENTION}건 외 "
                f"{hidden}건이 더 있다. Blocker는 파이프라인이 스스로 지우지 않으므로 "
                "이 수는 각 팀이 RESUMED / ISSUE_RESOLVED / COMPLETED를 보고할 "
                "때까지 줄지 않는다"
            )
        else:
            attention.append(
                f"{one_line(kind)} 총 {total}건 — 위 {_RISKS_IN_ATTENTION}건 외 "
                f"{hidden}건이 더 있다"
            )

    # The layers this system has no source for, said out loud. An empty panel
    # reads as "아무 일도 없다"; this says "물어볼 곳이 없다", which is a
    # different sentence and the true one. Which layers those are comes from
    # the model's own panels rather than from the constant alone, so a layer
    # that gained a source and lost its panel entry stops being announced
    # here too instead of being announced forever.
    # Two sentences, because the layers are unsourced for two different
    # reasons and one sentence made the screen say something false.
    #
    # Goal / Sprint / Task have no source **yet** — the decision of where one
    # would live is open and BACKLOG carries it. Critical Path and 완료 조건
    # are refused: docs/03 §4, docs/04 §44 and docs/04 §68 each say they are
    # not derived from Events, so "이 계층이 없다" reads as an omission when
    # it is a rule. Grouped by the panel that claims each layer, which is
    # already the model's own split (`_goals_panel` / `_sprints_panel` vs
    # `_judgements_panel`) rather than a second list kept in step by hand.
    coverage = unsourced_layer_coverage(model)

    def _layers_claimed_by(panel_key: str) -> list[str]:
        """The unsourced layers one panel accounts for, in the constant's
        order. Read off the model rather than from a second list here, so a
        layer that gained a source and lost its panel entry stops being
        announced instead of being announced forever."""
        return [
            layer
            for layer in UNSOURCED_LAYERS
            if coverage.get(layer) == panel_key
        ]

    refused = _layers_claimed_by("JUDGEMENTS")
    if refused:
        print(
            "  (자동화 안 함       : "
            + ", ".join(refused)
            + " — docs/03 §4 · docs/04 §44 · docs/04 §68이 Event만으로 결정하지 "
            "않는다고 고정한다. 사람이 정하는 것이며 어디에 적을지는 BACKLOG 참조)"
        )

    missing = [
        layer
        for layer in UNSOURCED_LAYERS
        if layer in coverage and layer not in refused
    ]
    if missing:
        print(
            "  (원천 없음          : "
            + ", ".join(missing)
            + " — Event Schema에도 Company Repository에도 이 계층이 없다. "
            "BACKLOG 참조)"
        )
    return attention


#: `backup_state.json`, as a name rather than a path fragment.
#:
#: Exists so the operator-facing message above can name the file without
#: spelling a path inside an f-string — see the comment at that print.
_BACKUP_STATE_FILENAME = "backup_state.json"


def _backup_succeeded_after(component, summary) -> str | None:
    """When a later, durable backup success contradicts this manifest.

    Returns the timestamp of that success, or None when there is nothing to
    say — a non-backup component, an unreadable state file, or a success
    that is not actually later than the run being reported.

    Only `backup` has a state file that outlives its run
    (`state/backup_state.json`, docs/08 §19-21), so only `backup` can be
    checked this way. The rest of the manifest is the only record of itself.

    Never fatal and never silent-on-error: a status view that cannot read an
    optional file reports nothing extra rather than losing the line it was
    already printing.
    """
    if component.name != "backup":
        return None
    try:
        state = load_backup_state(RUNTIME_DIR / "state" / "backup_state.json")
    except Exception:  # noqa: BLE001
        return None
    if state is None:
        return None
    succeeded_at = getattr(state, "last_successful_backup", None)
    if not succeeded_at:
        return None
    try:
        later = _comparable(
            datetime.fromisoformat(str(succeeded_at)),
            datetime.fromisoformat(str(summary.started_at)),
        )
        started = datetime.fromisoformat(str(summary.started_at))
    except (TypeError, ValueError):
        # A hand-edited or restored file is a DR path, not an exotic one.
        return None
    if later <= _comparable(started, later):
        return None
    return one_line(str(succeeded_at))


def _print_last_run(now: datetime | None = None) -> list[str]:
    """The last Runner execution, from its Run Manifest.

    This is the view that did not exist. `ops_status.py` could describe the
    *state* of things — how many Daily files, which Desktop is quiet — but
    not what the last run actually did, because nothing recorded it. A run
    whose Notion Sync failed and whose History succeeded looked identical to
    a clean one from here.

    Read-only and never fatal: a missing manifest means "no run has been
    recorded yet", and a damaged one is reported rather than repaired
    (docs/10 §46 — the program never deletes it).
    """
    attention: list[str] = []
    print("LAST RUN — Run Manifest")
    print("-" * 60)

    # A lock still held long after any real run could have finished.
    #
    # This used to record the pid-reuse defect as open and deferred: "making
    # the identity check exact means widening the lock file's pinned on-disk
    # contract, which is a decision and stays in BACKLOG." **C138 §3 made
    # that decision and implemented it** — docs/07 §26 lists what a lock may
    # record as a minimum, not as a schema, so writing the holder's image
    # name beside its pid implements §27's question rather than widening
    # anything. `_is_process_running()` now asks about both.
    #
    # What is left is narrower: a pid reused by a process running the *same*
    # executable, which this line still covers. It decides nothing and takes
    # nothing — it reports that a lock has been held longer than plausible,
    # and a genuinely long run and a ghost both deserve the same sentence:
    # go and look.
    # A stale lock nothing can remove. `lock_held_since()` below only sees a
    # lock a LIVE process holds, so this condition — dead process, file not
    # writable — is invisible to it, and `try_acquire_lock()` reports it as
    # ordinary contention. The Runner then skips on schedule forever while
    # every automatic signal reads healthy (BUG-42 / BACKLOG F-1).
    if stale_lock_cannot_be_cleared(_runner_lock_path()):
        print("  Runner Lock : 남아 있으나 제거할 수 없음 (읽기 전용)")
        attention.append(
            f"Runner Lock 파일이 남아 있고 제거할 수 없다 ({_runner_lock_path()}) — "
            f"기록된 프로세스는 이미 종료됐지만 파일이 읽기 전용이라 어떤 실행도 "
            f"인수하지 못한다. 모든 실행이 '다른 Runner 실행 중'으로 조용히 "
            f"건너뛰어진다. 사람이 파일을 확인해 지워야 한다"
        )

    held_since = lock_held_since(_runner_lock_path())
    if held_since is not None:
        # One clock for the whole function. This used to read
        # `datetime.now()` directly while the staleness check above uses the
        # `now` parameter, so the two ages in this block could be measured
        # against different references — harmless in production (both are
        # real now) and a trap in a fixture, which is exactly where a pinned
        # `started_at` compared against wall-clock time was already found.
        lock_reference = _comparable(now or businessdate.now(), held_since)
        held_hours = (lock_reference - held_since).total_seconds() / 3600
        print(f"  Runner Lock : 보유 중 (획득 {held_since.isoformat(timespec='seconds')})")
        if held_hours >= LOCK_STUCK_AFTER_HOURS:
            attention.append(
                f"Runner Lock이 {held_hours:.1f}시간째 잡혀 있다 (획득 "
                f"{held_since.isoformat(timespec='seconds')}) — 실제로 긴 실행이 "
                f"진행 중이거나, 죽은 Runner의 PID가 재사용돼 Lock이 영구히 "
                f"잡힌 것으로 보이는 상태다. 확인이 필요하다"
            )

    try:
        summary = read_summary(DEFAULT_RUN_SUMMARY_PATH)
    except RunSummaryError as exc:
        print(f"  손상된 Run Manifest: {exc}")
        # `attention + [...]`, not `[...]` (C146). A fresh list here threw
        # away everything this function had already found, and what it had
        # already found is the two Runner Lock alarms above — including the
        # one whose own comment says it is the only thing that can see the
        # condition: *"`try_acquire_lock()` reports it as ordinary
        # contention. The Runner then skips on schedule forever while every
        # automatic signal reads healthy (BUG-42 / BACKLOG F-1)."*
        #
        # The two co-occur by construction. A run killed mid-write leaves a
        # held lock **and** a truncated manifest, so the manifest damage was
        # silencing the alarm about the lock the same accident left behind.
        # Measured, a 48-hour-old lock held by a live process:
        #
        #     healthy manifest   Runner Lock이 48.0시간째 잡혀 있다  + 1 more
        #     corrupt manifest   (only) Run Manifest를 읽을 수 없다
        #
        # Same correction, same reason, as the monthly-state handler in
        # `_print_history()`: a check that cannot run must not take the
        # answered ones down with it.
        return attention + [f"Run Manifest를 읽을 수 없다: {DEFAULT_RUN_SUMMARY_PATH}"]

    if summary is None:
        print("  아직 기록된 실행이 없다.")
        return attention

    # `started_at` and the component fields below are read back out of the
    # manifest file, which `read_summary()` does not constrain to one line.
    # Same rule as `main()`'s ATTENTION loop; a hand-edited or restored
    # manifest is a DR path, not an exotic one.
    print(f"  실행 시각   : {one_line(summary.started_at)}")

    # How long that run took. `RunSummary.finished_at` is written on every
    # exit path — `run_once()` sets it in the same `finally` that writes the
    # manifest — and was read by nothing at all: not this view, not
    # `run_company_ops.py`, not a test. A field computed and discarded
    # (BUG-39's shape), found by a sweep of every dataclass field with no
    # production reader outside its own module (C32 §20).
    #
    # It is worth a line because the pipeline's cost is not constant: the
    # Backup step shells out to git with a 300 s timeout, `desktop_activity`
    # reads every file in `processed/` (which grows without bound), and the
    # Notion steps wait on a network. A run that used to take four seconds
    # and now takes four minutes is the earliest signal any of those is
    # degrading, and until now the two timestamps that say so were both on
    # disk and neither was ever subtracted.
    #
    # Resolution is one second, because `runsummary.now_iso()` formats with
    # `timespec="seconds"` — so an idle run reads `0.0s`. That is the true
    # answer at this resolution and the useful signal is the other end of
    # the scale (seconds becoming minutes); widening the manifest's
    # timestamp format to gain sub-second precision would be a schema
    # change for a number nobody needs.
    #
    # Unparseable or out-of-order values are skipped rather than guessed at,
    # exactly like the age check below: a restored manifest can carry either.
    try:
        finished = datetime.fromisoformat(summary.finished_at)
        began = datetime.fromisoformat(summary.started_at)
    except (TypeError, ValueError):
        finished = began = None
    if finished is not None and began is not None and (
        (finished.tzinfo is None) == (began.tzinfo is None)
    ):
        elapsed = (finished - began).total_seconds()
        if elapsed >= 0:
            print(f"  소요 시간   : {elapsed:.1f}s")

    # How long ago that was — the question this line never answered.
    #
    # The AGENT section has had an "N일째 실행되지 않았다" line since it was
    # written. The Runner, which is the machine that actually assembles
    # Company History and pushes the Backup, had no equivalent: `started_at`
    # was printed and never compared to anything. So a Runner that simply
    # stops — a Task Scheduler task disabled after a password change, a
    # machine left asleep, the task deleted — leaves this block showing its
    # last SUCCESS, in green, forever.
    #
    # Measured on this machine: the last run was two days old and ATTENTION
    # carried "agent has not run for 2 day(s)" (그때의 문구; C120에서 한국어로 바뀌었다) and nothing
    # at all about the Runner.
    #
    # `SILENT_AFTER_DAYS` is reused rather than a new threshold invented. Its
    # comment already states the reasoning this needs — a machine switched
    # off for a weekend is normal in this deployment (docs/07 §58), and a
    # threshold that fires every Monday gets ignored.
    #
    # A `started_at` this cannot parse is skipped rather than guessed at:
    # this view's contract is to answer even when part of the evidence is
    # damaged, and the manifest being unreadable is already reported above.
    # The naive/aware guard is the same one the lock check below uses — a
    # hand-edited manifest can carry an offset-less timestamp, and comparing
    # it to an aware `now` raises TypeError.
    reference = now or businessdate.now()
    try:
        started = datetime.fromisoformat(summary.started_at)
    except (TypeError, ValueError):
        started = None
    if started is not None:
        reference = _comparable(reference, started)
        age_days = (reference - started).total_seconds() / 86400
        if age_days >= SILENT_AFTER_DAYS:
            attention.append(
                f"Runner가 {age_days:.1f}일째 실행되지 않았다 (마지막 실행 "
                f"{summary.started_at}) — Company History도 Backup도 그동안 "
                f"진행되지 않았다. Task Scheduler 등록 상태를 확인해야 한다"
            )
    print(f"  종합 상태   : {summary.overall_status.value} (exit {summary.exit_code})")

    for component in summary.components:
        if component.status is ComponentStatus.SUCCESS:
            continue
        if component.status is ComponentStatus.SKIPPED:
            print(f"  - {one_line(component.name)}: SKIPPED (미설정)")
            continue
        failure = component.failure
        print(
            f"  ! {one_line(component.name)}: {one_line(failure.classification)} "
            f"[{failure.severity.value}/{failure.retryability.value}]"
        )
        superseded = _backup_succeeded_after(component, summary)
        if superseded is not None:
            # The one component with a durable state file of its own, so the
            # one whose manifest verdict can be checked against something.
            #
            # Measured on this deployment (C111): LAST RUN printed
            # `! backup: BACKUP_PENDING` while HISTORY, four blocks above,
            # printed `마지막 성공 백업 : 2026-08-24 (BACKUP_SUCCESS)` — seven
            # days *after* the run that failed. Both were true. The manifest
            # was written by a probe against a temp directory that no longer
            # exists, and nothing on the page said so, so the screen
            # contradicted itself and gave a reader no way to tell which half
            # was current.
            #
            # Says it; does not suppress it. Whether a superseded failure
            # should still be printed is a judgement about what LAST RUN
            # means, and the failure did happen — what was missing was not
            # the line but the fact standing next to it.
            # The filename is a plain literal, not an f-string component,
            # and that is not style. `BackupLogIsNeverPersistedTests` scans
            # for path-shaped expressions containing `backup` — the gate that
            # keeps E-14's unbuilt Backup Log from being built by accident —
            # and it cannot tell a message that mentions a path from code
            # that builds one. It flagged this line, correctly by its own
            # rule. Naming the file outside the f-string says the same thing
            # to a reader and nothing at all to the detector.
            print(
                f"      (그 뒤 {superseded} 에 백업이 성공했다 — "
                "state/" + _BACKUP_STATE_FILENAME
                + ". 이 실패는 지나간 것이다)"
            )
        # The failing step's own numbers. `ComponentResult.metrics` is
        # recorded by every `recorder.ok()/failed()` call and, until now, was
        # read by nothing outside the tests — the same BUG-39 shape this
        # project already fixed once for `IntakeSummary`, one layer up.
        #
        # It is the difference between "notion_sync is incomplete" and
        # "notion_sync is incomplete, 47 Events queued" — the second says
        # whether Company History is one Event behind or a month behind, and
        # only the failing components are printed so the block stays short.
        #
        # Values are this project's own counters, statuses and dates, never
        # Event content (`reason` carries that and is deliberately not
        # printed here). `oplog.one_line()` is applied anyway: this is a
        # persisted file rendered to a terminal, and the rule that nothing
        # read back from disk can forge a line should not depend on today's
        # metric list staying the way it is.
        #
        # That last sentence is about the *keys*, and the guard used to be on
        # the values only. `read_summary()` validates the three enums and
        # nothing else — `metrics` comes back as `c.get("metrics", {})`, so
        # both halves of every pair are whatever the JSON holds. A restored
        # or hand-edited manifest is a DR path, and a forged line here sits
        # inside the LAST RUN block indented exactly like a real metric row.
        #
        # And the sentence above is about the PAIRS. `metrics` itself is
        # `c.get("metrics", {})`, so it can also be not-a-mapping at all —
        # the one shape `.items()` cannot survive. Measured (C44), a manifest
        # whose `metrics` was a string:
        #
        #     AttributeError: 'str' object has no attribute 'items'
        #     -> out of `_print_last_run()`, out of `main()`
        #     -> the operator gets a traceback instead of ANY status
        #
        # That breaks this file's own contract twice over: it "must still
        # produce an answer when part of the evidence is damaged", and
        # docs/10 §46 names a damaged state file as something to REPORT, not
        # something to die on. It is a DR path — a restored or hand-edited
        # manifest — which is exactly when this view is read.
        #
        # Reported rather than skipped, for §46's reason: the rest of the
        # manifest still renders (which step failed, how badly), and the
        # damage gets a line of its own instead of disappearing.
        metrics = component.metrics if isinstance(component.metrics, Mapping) else None
        if metrics:
            rendered = " ".join(
                f"{one_line(key)}={one_line(value)}"
                for key, value in sorted(metrics.items())
            )
            print(f"      {rendered}")
        elif component.metrics:
            kind = type(component.metrics).__name__
            print(f"      metrics 읽을 수 없음 (JSON 객체가 아님: {kind})")
            attention.append(
                f"Run Manifest의 `{one_line(component.name)}` 단계 metrics가 손상됐다 "
                f"(JSON 객체가 아니라 {kind}) — 그 단계의 숫자는 읽을 수 없다. "
                f"나머지 Manifest는 그대로 유효하다. 복구된 파일이거나 손으로 편집된 "
                f"파일일 수 있다(docs/10 §46)"
            )
        if component.artifact_refs:
            # Same origin, same rule — `artifact_refs` is `tuple(c.get(...))`
            # with no validation either.
            print(
                "      evidence: "
                + ", ".join(one_line(ref) for ref in component.artifact_refs)
            )

    # Steps that never started. This loop only ever walked the components
    # that ARE in the manifest, so a run that aborted in Backup — taking
    # Dashboard with it, since the exception propagates out of `run_once()`
    # before `recorder.begin(C_DASHBOARD)` — showed eight components and
    # said nothing about the ninth. An operator reading LAST RUN saw a
    # Backup failure and no reason to think anything else had been missed,
    # while that run's Dashboard row was gone for good and not even queued
    # for retry (BACKLOG A-18).
    #
    # "Never started" is deliberately its own word rather than SKIPPED:
    # SKIPPED means the Runner reached the step and chose not to run it (no
    # Notion configured, say), which is fine. This means the step was never
    # reached, which is not.
    recorded = {component.name for component in summary.components}
    never_started = [name for name in PIPELINE_COMPONENTS if name not in recorded]
    if never_started:
        print(f"  ! 시작되지 못한 단계: {', '.join(never_started)}")
        attention.append(
            f"마지막 실행에서 시작조차 되지 못한 단계: {', '.join(never_started)} — "
            f"앞 단계가 중단시켰다. 그 단계의 결과는 이번 실행에서 기록되지 않았고 "
            f"자동으로 재시도되지도 않는다"
        )

    # Only a PERMANENT failure needs a person now. A RETRYABLE one is what
    # the next scheduled run is for, and listing it here would put a
    # standing item in ATTENTION that clears itself — the kind of alert
    # that trains people to ignore the section.
    for component in summary.failures():
        if component.failure.retryability is Retryability.PERMANENT:
            attention.append(
                f"{component.name}: {component.failure.classification} — "
                f"재시도로 해결되지 않는다"
            )
    if summary.overall_status is OverallStatus.FAILED and not attention:
        attention.append(
            f"마지막 실행이 FAILED로 끝났다 ({DEFAULT_RUN_SUMMARY_PATH.name} 참고)"
        )

    return attention



# The Task Scheduler query, behind one name so a test can answer it without a
# subprocess and without a task existing. The same shape as the `run`
# parameter `schedtask.query()` already takes, one level up: this block is
# reached through `_block()`, which passes only `now`.
SCHEDULE_QUERY = schedtask.query

#: Which installer registers each task SCHEDULE reports on. Two messages in
#: `_print_schedule()` name a script an operator is told to run, and a
#: message that names the wrong one sends them to register the wrong job.
_SCHEDULE_INSTALLERS = {"Runner": "runner", "Agent": "agent", "Publish": "publish"}


def _scheduled_desktop_id() -> str | None:
    """Which Agent task name this machine's Agent would have been given.

    `COMPANY_OPS_PROFILE` first, because that is the variable
    `install_agent_task.ps1` writes at the same moment it builds the task
    name out of the same `-DesktopId` — so where a task exists, this is the
    value it was named after.

    `agent_state.json` second, for the machine where the task is registered
    but the variable was lost (a new shell, a profile that was never
    reloaded, an operator running this from an editor). The state file's
    `desktop_id` is written by the Agent itself and `ensure_desktop()`
    refuses to let it drift, so it names the same Desktop.

    `None` when neither answers: this block then says it cannot check rather
    than checking a guessed name and reporting a real task as missing.
    """
    from_environment = os.environ.get("COMPANY_OPS_PROFILE", "").strip()
    if from_environment:
        return from_environment
    try:
        raw = json.loads(
            (_agent_dir() / "state" / "agent_state.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, RecursionError):
        # `RecursionError` for BUG-40's reason, the same as every other
        # `json.loads` in this file: deeply nested JSON raises it rather than
        # `ValueError`, and this view must answer even when the evidence is
        # damaged. The state file being unreadable is already reported by the
        # AGENT block; it must not also take this one down.
        return None
    if not isinstance(raw, dict):
        return None
    desktop_id = raw.get("desktop_id")
    if isinstance(desktop_id, str) and desktop_id.strip():
        return desktop_id.strip()
    return None


def _this_machine_runs_the_runner() -> tuple[bool, tuple[str, ...]]:
    """Whether Desktop 4's daily task belongs on this machine.

    Desktops 1-3 run only the Agent, so "DOJOONPASS_COMPANY_OPS_DAILY is not
    registered" is the correct state there, and raising it would be a false
    alarm on three machines out of four — the kind that gets a whole block
    ignored.

    Decided from evidence the Runner leaves and nothing else does: the
    Company History tree it writes, or a Run Manifest from a run that
    happened. `_print_agent()` makes the mirror-image judgement from
    `runtime/agent` existing, and this follows it deliberately rather than
    inventing a second convention.

    The cost of the remaining error is asymmetric and points this way. On a
    Desktop 4 that has never run, this answers False and the block raises
    nothing — one quiet morning until the first run, after which it is right
    forever. Answering True everywhere would put a permanent false ATTENTION
    on the three Desktops that must never see one.

    **Returns the probes it could not read, and that is not decoration.**
    Both `is_dir()` and `is_file()` re-raise `EACCES` (only the "not there"
    family is swallowed by `pathlib`), and the first draft of this function
    answered a refused probe with `pass` — so a permission change under
    `runtime/` made Desktop 4 look like Desktop 1, and the "Runner 예약
    실행이 등록돼 있지 않다" alarm went quiet for the exact machine it exists
    to protect. `ASilentlyDroppedEntryIsARosterNotAParagraphTests` caught it
    on the first run, which is what that class is for.
    """
    unreadable: list[str] = []
    for probe, is_present in (
        (RUNTIME_DIR / "local_master", Path.is_dir),
        (DEFAULT_RUN_SUMMARY_PATH, Path.is_file),
    ):
        try:
            if is_present(probe):
                return True, ()
        except OSError as exc:
            unreadable.append(f"{probe.name}: {exc.strerror or exc}")
    return False, tuple(unreadable)



#: How many trailing lines of a scheduled run's console log SCHEDULE prints.
#:
#: Small on purpose. This is a status screen, not a log viewer, and the
#: useful part of a failed run's output is at the end -- the traceback's last
#: line, or the `[FAILED] ...` sentence. The file itself is named so an
#: operator can read the rest.
_SCHEDULED_LOG_TAIL_LINES = 5

#: How much of the end of that file is read to find those lines.
#:
#: **The first draft read the whole file**, and that file is the one thing
#: this Sprint added with no bound on its size. It is appended to on every
#: scheduled run and nothing trims it (a retention policy for this system's
#: growing files is an open decision — BACKLOG E-2). At the daily cadence
#: the task fires that is a few megabytes a year, which is nothing; the case
#: that is not nothing is a task failing in a loop, or a run printing a
#: traceback per Event, which is exactly when an operator opens this screen.
#:
#: A diagnostic that reads an unbounded file into memory to print five lines
#: of it is the shape BUG-40 already cost this project once — one oversized
#: input taking down the tool people reach for when something else is
#: already broken. 64 KB holds far more than five lines of anything.
_SCHEDULED_LOG_TAIL_BYTES = 64 * 1024


def _tail_bytes(path: Path) -> str:
    """The last `_SCHEDULED_LOG_TAIL_BYTES` of `path`, decoded.

    Binary and seeked rather than `read_text()`, so the cost does not grow
    with a file nothing trims. See `_SCHEDULED_LOG_TAIL_BYTES`.

    The first line of the window is dropped when the window did not start at
    the beginning of the file: a byte offset lands mid-line, and mid-line is
    also mid-character in UTF-8 — `errors="replace"` would render the
    fragment as replacement characters and the report would print a line
    that was never written. Dropping one line to print four true ones is the
    right trade for a screen that exists to be believed.
    """
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        window = min(size, _SCHEDULED_LOG_TAIL_BYTES)
        handle.seek(size - window)
        raw = handle.read(window)
    text = raw.decode("utf-8", errors="replace")
    if window < size:
        _, _, text = text.partition("\n")
    return text


def _scheduled_log_tail(
    task_name: str, status=None
) -> "tuple[Path | None, tuple[str, ...], str | None]":
    """The end of the console log this task's scheduled action appends to.

    Returns `(path, lines, problem)`. `path` is `None` for a task neither
    installer registers — there is no log to name, and naming one would send
    an operator looking for evidence that was never written.

    **Why this is worth printing at all.** Every other line in this report
    comes from a file the pipeline wrote. The failures this block exists to
    catch are the ones where the pipeline never ran: `python` off PATH, a
    moved working directory, an unset `COMPANY_OPS_*`. Those leave a
    `LastTaskResult` and nothing else — the number says "exit 1" and this
    file says *which variable was missing*. The installers redirect the
    action's output here precisely so that sentence survives (C138).

    `redact()` as well as `one_line()` and `bounded()`, unlike this file's
    ATTENTION sink: this is a file's **contents**, not a filename or a count,
    and `run_company_ops.py` prints Notion failure reasons — remote response
    bodies — straight to the stream that lands in it.
    """
    # The path the *registered action* redirects to, when it can be read,
    # and only then the one this repository's installer would have used.
    #
    # The order matters and the reason is the same one `redirect_target()`
    # gives: an operator may have pointed the output somewhere of their own
    # choosing, and a report that showed them our path instead would send
    # them looking for a file that was never written. Falling back to the
    # installer's name covers the machine whose action this could not read.
    registered = schedtask.redirect_target(
        getattr(status, "action_command", None)
    )
    if registered:
        path = Path(registered)
    else:
        name = schedtask.scheduled_log_name(task_name)
        if name is None:
            return None, (), None
        path = RUNTIME_DIR / "logs" / name
    try:
        text = _tail_bytes(path)
    except FileNotFoundError:
        # Not damage. The task has never produced output, or was registered
        # by an installer older than the redirection. Said as itself, because
        # "no log" and "an unreadable log" call for different actions.
        return path, (), "아직 기록이 없다"
    except OSError as exc:
        return path, (), bounded(str(exc.strerror or exc))
    lines = [line for line in text.splitlines() if line.strip()]
    # `bounded` OUTSIDE `redact`, not inside it. The first draft here read
    # `redact(one_line(bounded(line)))`, which cuts the line before anything
    # has looked for a secret — a token straddling the cut is then never
    # matched and its head is printed. Every pattern in `SECRET_RE` has a
    # minimum length, so truncating first is exactly how to defeat it.
    # `test_oplog.py::BoundingBeforeRedactingDefeatsItTests` caught it, which
    # is the whole reason that gate sweeps production modules rather than
    # trusting each writer to remember the order.
    return path, tuple(
        bounded(redact(one_line(line)))
        for line in lines[-_SCHEDULED_LOG_TAIL_LINES:]
    ), None


def _entrypoint_is_another_checkout(status) -> str | None:
    """The action's entrypoint when it is not this checkout's, else `None`.

    `None` also when it cannot be told — an unreadable action, an
    unparseable path. This produces an ATTENTION line accusing the operator
    of having two copies, and that accusation must rest on two paths this
    actually compared.

    Compared case-insensitively and through `resolve()`, because Windows
    treats `C:/Repo` and `c:/repo` as one directory and because either
    side can arrive with `..` or a short (8.3) component. Two spellings of
    one directory reported as two checkouts would be a false alarm about the
    one thing that stops every scheduled run.
    """
    entrypoint = schedtask.action_entrypoint(
        getattr(status, "action_command", None)
    )
    if not entrypoint:
        return None
    try:
        registered_root = Path(entrypoint).resolve().parent
        here = PROJECT_ROOT.resolve()
    except OSError:
        # A path on a disconnected drive cannot be resolved, and "I could not
        # check" must not be reported as "it points somewhere else".
        return None
    if str(registered_root).casefold() == str(here).casefold():
        return None
    return entrypoint


def _print_schedule(now: datetime) -> list[str]:
    """SCHEDULE — is anything actually going to start tomorrow morning?

    Every other block in this report is derived from files this system wrote,
    which makes all of them blind to the same thing: a scheduled task that
    never starts the process leaves no file to read. `python` off PATH, a
    working directory that moved, a task disabled after a password change —
    the process dies before `oplog` opens anything, and `runtime/` is then
    indistinguishable from a machine that was switched off for the weekend.

    Windows is the only witness to that, and LAST RUN's staleness message has
    been telling an operator to go and ask it by hand ("Task Scheduler 등록
    상태를 확인해야 한다") since it was written. This block asks.

    Read-only, and `schedtask` carries a test asserting it stays that way. A
    diagnostic that repaired the thing it was diagnosing would be the one
    tool an operator cannot trust to leave the evidence alone.

    `now` is unused and still taken: `_block()` calls every renderer the same
    way, and a signature that opted out would make this the one block whose
    failure is not caught the way the others' are.
    """
    attention: list[str] = []

    runner_expected, runner_probe_errors = _this_machine_runs_the_runner()
    agent_expected = _agent_dir().exists()
    desktop_id = _scheduled_desktop_id()

    names: list[str] = [schedtask.RUNNER_TASK_NAME, schedtask.PUBLISH_TASK_NAME]
    expected_agent_task = (
        schedtask.agent_task_name(desktop_id) if desktop_id else None
    )
    if expected_agent_task is not None:
        names.append(expected_agent_task)

    # Asked by prefix as well, because the Agent's task name carries a
    # Desktop id and asking only for today's can only answer "is the one I
    # expected there". See `schedtask.build_query`.
    statuses = SCHEDULE_QUERY(names, prefixes=(schedtask.AGENT_TASK_PREFIX,))

    # What is actually registered, whatever it is called. Sorted so the
    # report is stable across runs; `present` filters out the by-name row
    # for a task that is not there, which would otherwise appear here as a
    # discovered one.
    registered_agent_tasks = sorted(
        name
        for name, status in statuses.items()
        if name.startswith(schedtask.AGENT_TASK_PREFIX) and status.present
    )
    agent_task = (
        registered_agent_tasks[0] if registered_agent_tasks else expected_agent_task
    )

    print("SCHEDULE — Windows Task Scheduler 등록 상태")
    print("-" * 60)

    if len(registered_agent_tasks) > 1:
        # Both fire at logon, and only one of them matches this machine's
        # `agent_state.json`. The other reaches `ensure_desktop()`, which
        # refuses — correctly, because accepting it would let one Desktop
        # inherit another's watermark and skip every date up to it with no
        # error anywhere (`run_agent.py` spends twenty lines on this). The
        # pair is what nothing could see: each task, asked about by name,
        # answers "registered and healthy".
        attention.append(
            "Agent 예약 실행이 둘 이상 등록돼 있다: "
            + ", ".join(one_line(name) for name in registered_agent_tasks)
            + " — 둘 다 로그온에 발화하고, 이 머신의 agent_state.json과 맞지 "
            "않는 쪽은 매번 거부된다. -DesktopId를 바꿔 다시 설치한 흔적이다. "
            "쓰지 않는 Task를 Unregister-ScheduledTask로 지워야 한다"
        )

    if (
        expected_agent_task is not None
        and registered_agent_tasks
        and expected_agent_task not in registered_agent_tasks
    ):
        # The single-task version of the same accident: the machine's
        # identity was repointed and the task was not, or the reverse.
        attention.append(
            f"등록된 Agent Task가 이 머신의 Desktop ID와 다르다 — 등록: "
            + ", ".join(one_line(name) for name in registered_agent_tasks)
            + f" / 이 머신: {one_line(expected_agent_task)}. 둘 중 하나가 "
            f"틀렸다. COMPANY_OPS_PROFILE을 되돌리거나 올바른 -DesktopId로 "
            f"scripts/install_agent_task.ps1을 다시 실행한다 — state 파일은 "
            f"직접 지우지 않는다"
        )

    for task_name, expected, label in (
        (schedtask.RUNNER_TASK_NAME, runner_expected, "Runner"),
        # Never "expected", and that is a judgement rather than an oversight.
        # The Control Tower publish is how the result reaches people who
        # never open a terminal — AGENT.md §6c tells an operator to register
        # it "beside run_company_ops.py", and this tool's exit code 3 exists
        # for that deployment — but an operator who reads the browser
        # Dashboard instead is not misconfigured. So its absence is printed
        # as a fact with the command that would change it, and never raised.
        #
        # Everything *else* about it is raised exactly like the other two: a
        # publish task that is disabled, failing, terminated, or throwing
        # its output away is a scheduled job that has stopped doing its job,
        # and that is not a preference.
        (schedtask.PUBLISH_TASK_NAME, False, "Publish"),
        (agent_task, agent_expected, "Agent"),
    ):
        if task_name is None:
            # Only reachable for the Agent, and only when neither
            # COMPANY_OPS_PROFILE nor the state file names a Desktop. Said
            # out loud: an unchecked task must not look like a checked one.
            print(
                "  Agent  : 확인 불가 — 이 머신의 Desktop ID를 알 수 없어 "
                "Task 이름을 만들 수 없다"
            )
            if agent_expected:
                attention.append(
                    "Agent 예약 실행을 확인할 수 없다 — COMPANY_OPS_PROFILE이 "
                    "설정돼 있지 않고 agent_state.json에서도 desktop_id를 읽지 "
                    "못했다. 이 머신의 Agent가 예약돼 있는지 알 수 없는 상태다"
                )
            continue

        status = statuses.get(
            task_name,
            schedtask.ScheduledTaskStatus(
                name=task_name, present=False, query_error="조회 결과 없음"
            ),
        )
        verdict = schedtask.classify(status)
        # One table rather than two conditionals: this name is interpolated
        # into two different messages, and two copies is how a third task
        # ends up named correctly in one of them and wrongly in the other.
        installer = _SCHEDULE_INSTALLERS[label]
        print(f"  {label:<7}: {one_line(task_name)} — {verdict}")

        if verdict == schedtask.UNKNOWN:
            print(f"           {one_line(status.query_error or '')}")
            continue

        if status.present:
            detail = f"           상태 {one_line(status.state or '?')}"
            if status.has_ever_run:
                detail += (
                    f" · 마지막 실행 {one_line(status.last_run or '?')} "
                    f"({schedtask.describe_result(status.last_result)})"
                )
            else:
                detail += " · 아직 실행된 적 없음"
            if status.next_run:
                detail += f" · 다음 실행 {one_line(status.next_run)}"
            print(detail)

        # A task can be registered, enabled and firing on time while
        # throwing away everything its process prints — which is what every
        # task this project registered before C138 does, because the action
        # was `python.exe <entrypoint>` with no redirection.
        #
        # It is checked here rather than folded into `classify()` because it
        # is not about whether the task runs. It is about whether the run
        # that fails will be able to say why: for the failures that happen
        # before the application writes anything — python off PATH, a moved
        # working directory, an unset COMPANY_OPS_* — the discarded stream
        # was the entire diagnosis.
        #
        # Windows keeps the action it was given, so this does not fix itself
        # when the repository is updated. Re-running the installer is what
        # changes it, and the message says so.
        # Where the registered action actually points. A task whose paths
        # were baked before the repository moved runs nothing and cannot say
        # so: its log lives in the vanished directory too, so `>>` fails and
        # the explanation is never written. See `schedtask.action_entrypoint`.
        elsewhere = _entrypoint_is_another_checkout(status)
        if elsewhere is not None:
            print(f"           실행 대상 {one_line(elsewhere)}")
            attention.append(
                f"{label} 예약 실행이 이 저장소가 아닌 곳을 실행한다 "
                f"({one_line(task_name)}): {one_line(elsewhere)} — "
                f"저장소를 옮겼거나 사본이 둘이다. 그 경로가 없으면 매 실행이 "
                f"실패하고 로그도 그 경로에 있어 아무 설명도 남지 않는다. "
                f"scripts/install_{installer}_task.ps1을 여기서 다시 실행하면 "
                f"이 저장소를 가리키게 된다"
            )

        # A task nobody here registered: all three installers write exactly
        # one action, so more than one means a person edited it. The query
        # reads the first action only, so the redirection check below
        # declines to answer (`schedtask.discards_console_output`). Said as
        # a fact and not raised — a second action is the operator's choice,
        # and the thing worth printing is that this check no longer covers
        # the whole task, rather than letting the line simply disappear.
        if status.present and (status.action_count or 0) > 1:
            print(
                f"           Action이 {status.action_count}개다 — 이 화면은 "
                f"첫 번째만 읽으므로 리디렉션 여부를 판정하지 않는다"
            )

        discards = schedtask.discards_console_output(status)
        if discards:
            print("           콘솔 출력을 남기지 않는다 (Action에 리디렉션 없음)")
            attention.append(
                f"{label} 예약 실행이 콘솔 출력을 버린다 "
                f"({one_line(task_name)}) — 지금 등록된 Action에 리디렉션이 "
                f"없어서, 프로세스가 시작조차 못 하고 끝난 실행은 종료 코드 "
                f"말고 아무 설명도 남기지 않는다. "
                f"scripts/install_{installer}_task.ps1을 다시 실행하면 "
                f"갱신된다(-Force, 멱등)"
            )

        if verdict not in schedtask.NEEDS_ATTENTION:
            continue

        if verdict in (schedtask.LAST_RUN_FAILED, schedtask.LAST_RUN_TERMINATED):
            # The console output of the run that failed. This is the only place
            # it exists: a run that died before `oplog` opened a file wrote
            # nothing under `runtime/` except this.
            log_path, tail, log_problem = _scheduled_log_tail(task_name, status)
            if log_path is not None:
                print(f"           로그 {log_path}")
                if log_problem is not None:
                    print(f"             ({log_problem})")
                for line in tail:
                    print(f"             | {line}")

        if verdict == schedtask.NOT_REGISTERED:
            if not expected:
                if label == "Publish" and runner_expected:
                    # Said, not raised. On Desktop 4 this is the difference
                    # between "the workspace sees today's state" and "the
                    # workspace sees whatever day somebody last opened a
                    # terminal" — worth putting in front of an operator, and
                    # not worth an alarm that never clears for the operator
                    # who deliberately reads the browser Dashboard instead.
                    print(
                        "           Notion Control Tower는 예약돼 있지 않다 — "
                        "사람이 실행할 때만 갱신된다"
                    )
                    print(
                        f"           예약하려면: "
                        f"scripts/install_{installer}_task.ps1"
                    )
                if label == "Runner" and runner_probe_errors:
                    # Not "this is not a Runner machine" — "this cannot be
                    # told apart from one". Raised rather than swallowed
                    # because the two answers differ by exactly the alarm
                    # Desktop 4 depends on, and a refused probe is why the
                    # first draft of `_this_machine_runs_the_runner()` was
                    # wrong.
                    attention.append(
                        "이 머신이 Runner(Desktop 4)인지 판단할 근거를 읽지 "
                        "못했다 (" + "; ".join(runner_probe_errors) + ") — "
                        "Runner 예약 실행이 등록돼 있지 않은데, 그것이 정상인지 "
                        "아닌지를 말할 수 없다"
                    )
                # Otherwise the normal state on the machines this job does
                # not belong to. Printed above as NOT_REGISTERED, not raised.
                continue
            attention.append(
                f"{label} 예약 실행이 등록돼 있지 않다 ({one_line(task_name)}) — "
                f"이 머신은 자동으로 아무것도 실행하지 않는다. "
                f"scripts/install_{installer}_task.ps1로 등록해야 한다"
            )
        elif verdict == schedtask.DISABLED:
            attention.append(
                f"{label} 예약 실행이 사용 안 함 상태다 ({one_line(task_name)}) — "
                f"등록은 돼 있지만 트리거가 발생해도 시작되지 않는다"
            )
        elif verdict == schedtask.LAST_RUN_TERMINATED:
            attention.append(
                f"{label}의 마지막 예약 실행을 Windows가 중단시켰다 "
                f"({one_line(task_name)}, 마지막 실행 "
                f"{one_line(status.last_run or '?')}) — 시간 제한을 넘겼을 수 "
                f"있다. 실행은 도중에 끊겼고 다음 트리거가 이어받는다"
            )
        else:  # LAST_RUN_FAILED
            attention.append(
                f"{label}의 마지막 예약 실행이 실패로 끝났다 "
                f"({one_line(task_name)}, 마지막 실행 "
                f"{one_line(status.last_run or '?')}): "
                f"{schedtask.describe_result(status.last_result)} — "
                f"프로세스가 시작조차 못 했다면 runtime/에는 이 로그 말고 아무 "
                f"기록도 남지 않는다: "
                f"{one_line(str(RUNTIME_DIR / 'logs' / (schedtask.scheduled_log_name(task_name) or '?')))}"
            )

    return attention


def _block(label: str, render, now: datetime) -> list[str]:
    """One section of the report, and the guarantee that it produces one.

    Every block here is written to answer even when part of the evidence is
    damaged, and a dozen `except OSError` arms inside them say so. They all
    guard the *second* call rather than the first: `_json_paths()` is the
    pattern —

        if not path.is_dir():          <- unguarded
            return []
        try:
            entries = list(os.scandir(path))
        except OSError:                <- guarded
            return []

    and `Path.is_dir()` does **not** swallow a permission error.
    `pathlib._abc._IGNORED_ERRNOS` is `(ENOENT, ENOTDIR, EBADF, ELOOP)`;
    `EACCES` is re-raised. So the guard is one line too late, at 36 call
    sites across this file and `app/desktop_activity.py`.

    That matters because the directory most likely to answer "access denied"
    is the one this project reads across a network: `events/transport/` is
    the shared OneDrive folder (AGENT.md §1), and a syncing or re-authorising
    OneDrive returns exactly that. The result was a traceback from the tool
    an operator opens **because** something already looks wrong.

    Fixed here rather than at the 36 sites, and the reason is not effort.
    Making each predicate return `False` on a refusal would turn "I could not
    read this" into "there is nothing here" — a partial report presented as
    complete, which is the silent-loss shape this project keeps removing. A
    block that cannot be read says so, raises ATTENTION, and the exit code
    follows.

    `OSError` only. A `TypeError` from a rollup is a bug in this program and
    must not be dressed up as a disk problem; docs/10 §46's contract is about
    damaged *evidence*.
    """
    try:
        return list(render(now))
    except OSError as exc:
        # An `OSError` from **writing** is not damaged evidence, and this arm
        # is the one place in this file that can mistake it for some (C118).
        #
        # Measured: `python ops_status.py | head -3` raised
        # `OSError(22, 'Invalid argument')` inside `_print_history()`'s own
        # `print()`. This handler caught it, reported
        # `HISTORY — 읽지 못했다`, and raised an ATTENTION line saying
        # "디스크나 권한 문제이며 사람이 확인해야 한다" — about a block whose
        # evidence was perfectly readable. Then its own `print()` failed the
        # same way, so the handler raised too: two tracebacks and exit 120.
        #
        # The docstring above already draws this line for the other case —
        # "a `TypeError` from a rollup is a bug in this program and must not
        # be dressed up as a disk problem". A closed pipe is the same
        # mis-attribution wearing `OSError`'s type.
        #
        # Re-raised rather than handled here: there is nothing to say and
        # nowhere to say it, and `run_entrypoint()` at the bottom of this
        # file owns what that means for the exit code.
        if output_is_gone():
            raise
        # `_authored()`: the path in the message can be an Event filename,
        # and `safe_event_filename()` builds those from `event_id` — a string
        # another Desktop chose and `validate_event()` only type-checks.
        detail = _authored(f"{type(exc).__name__}: {exc}")
        print(f"{label} — 읽지 못했다")
        print("-" * 60)
        print(f"  {detail}")
        return [
            f"{label} 블록을 읽지 못했다 ({detail}) — 이 섹션의 상태는 이번 "
            "출력에 없다. 디스크나 권한 문제이며 사람이 확인해야 한다"
        ]


def main(argv: Sequence[str] = ()) -> int:
    refusal = unexpected_arguments(
        argv,
        tool="ops_status.py",
        # `COMPANY_OPS_RUNTIME_DIR` used to head this list and nothing has
        # ever read it — `RUNTIME_DIR` is a constant here, deliberately (see
        # `_agent_dir()`'s note on why it is derived per call rather than
        # made a knob). Naming it was worse than naming nothing: the message
        # exists to give an operator "the name of the knob that does exist".
        configured_by=(
            "COMPANY_OPS_HISTORY_START_DATE",
            "COMPANY_OPS_AGENT_SYNC_FOLDER",
            "COMPANY_OPS_AGENT_START_DATE",
        ),
    )
    if refusal is not None:
        print(f"[FAILED] {refusal}", file=sys.stderr)
        return CONFIG_ERROR_EXIT

    now = businessdate.now()
    print(f"DOJOONPASS Company Ops — Status @ {now.isoformat(timespec='seconds')}")
    print()

    attention: list[str] = []
    for label, block in (
        ("COMPANY", _print_company),
        ("HISTORY", _print_history),
        ("CONTROL TOWER", _print_control_tower),
        ("LAST RUN", _print_last_run),
        ("SCHEDULE", _print_schedule),
        ("NOTION", _print_notion),
        ("AGENT", _print_agent),
    ):
        attention.extend(_block(label, block, now))
        print()

    if not attention:
        print("ATTENTION — 없음. 사람이 지금 할 일은 없다.")
        return 0

    print("ATTENTION")
    print("-" * 60)
    for item in attention:
        # `one_line()` at the sink, so "one item, one line" holds for every
        # ATTENTION message — including ones added years from now by someone
        # who never read this comment. That is the same argument
        # `oplog.append_line()` makes for logs, and this file already accepts
        # it for Run Manifest metrics ("nothing read back from disk can forge
        # a line should not depend on today's metric list staying the way it
        # is"). The metrics were the smaller half.
        #
        # Measured before this existed. `event_id` crosses the OneDrive
        # transport from another Desktop and docs/02 constrains it only to
        # "present and non-null" (BACKLOG A-15), so a newline inside one is
        # accepted, stored, and interpolated into these messages by
        # `_kept_but_not_rendered()`, `find_orphaned_events()` and
        # `_candidates_before()`. One KEEP Candidate whose id began
        # `"X\n  ! 모든 검사 통과 — 사람이 지금 할 일은 없다"` produced exactly
        # that line, standing on its own inside ATTENTION:
        #
        #     ! KEEP Candidate 1건이 저장돼 있는데 … 없다: X
        #     ! 모든 검사 통과 — 사람이 지금 할 일은 없다 (2026-08-05) — …
        #
        # BUG-6's shape, in the one view AGENT.md §6 tells an operator to read
        # first. `oplog.one_line()` closed it for `collector.log` (C10); the
        # renderer of this view had no equivalent.
        #
        # Escaped rather than stripped, for `one_line()`'s own reason: the
        # real id stays recoverable, so the message still names the file a
        # human has to go and find.
        #
        # `redact()` is deliberately NOT applied here, unlike in
        # `append_line()`. Almost every ATTENTION message is built from
        # filenames, ids and counts — never from a file's *contents* — and the
        # two that carry an exception message carry a state-file parse error,
        # whose text is positional ("Expecting ',' delimiter: line 3 column 5")
        # and quotes nothing. Over-redacting a path an operator has to act on
        # would cost more than it protects.
        #
        # The exceptions are handled where they are produced rather than
        # here, and there is more than one: `_print_control_tower()`'s blocker
        # line quotes a `blocker` string a person typed on another Desktop,
        # which is Event *content* and can carry anything — "waiting for
        # NOTION_API_TOKEN=… to be rotated" is a plausible thing to write.
        #
        # C47 corrected the sentence above it. "never from a file's contents"
        # rested on ids being machine-made, and they are not: `event_id` and
        # `project_id` are plain strings a Desktop sets for itself and
        # `validate_event()` only type-checks. Measured — an Event named after
        # the token it was about printed that token into this block and into
        # the log a scheduled run redirects to disk. Every site that prints an
        # Event-authored identifier now goes through `_authored()`.
        #
        # The sink itself stays un-redacted, which is still right: a path is
        # still a path, and a message that carries authored text redacts at
        # the place it is produced — exactly as
        # `run_company_ops.py::_print_result()` does for `failure.reason`.
        print(f"  ! {one_line(item)}")
    return 3


if __name__ == "__main__":
    # `run_entrypoint()` rather than `main()` directly: this is the tool an
    # operator pipes (`| head`, `| more`, a pager they quit out of), and the
    # exit code that state used to produce was `120` with two tracebacks.
    # See `cli.OUTPUT_LOST_EXIT` for why the answer is 2 and not 0.
    raise SystemExit(run_entrypoint(main, sys.argv))
