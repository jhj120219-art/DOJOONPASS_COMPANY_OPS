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
import subprocess
import sys
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

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

from agent.delivery import find_undelivered_events  # noqa: E402
from agent.status import read_status  # noqa: E402
from app.desktop_activity import read_company_activity  # noqa: E402
from app.runner import DEFAULT_RUN_SUMMARY_PATH, PIPELINE_COMPONENTS  # noqa: E402
from backup.state import BackupStateError  # noqa: E402
from daily.markdown import summary_line_indices  # noqa: E402
from backup.state import load_state as load_backup_state  # noqa: E402
from backup.working_copy import scan_for_secrets  # noqa: E402

# The gate's own name list, not a second opinion about what a secret looks
# like — same reason `_count_transport` reuses intake's parse test. A history
# path is compared by its basename, exactly as `scan_for_secrets()` does.
from backup.working_copy import _looks_like_secret  # noqa: E402

# The scope set the Backup gate enforces, imported rather than restated so a
# third scope directory is diagnosed without editing this file.
from backup.working_copy import _ALLOWED_TOP_LEVEL_DIRS  # noqa: E402
from history.file_repository import is_incomplete_write  # noqa: E402
from history.reconciliation import find_orphaned_events  # noqa: E402
from monthly import MonthlyStateError, monthly_history_path  # noqa: E402
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
from oplog import one_line  # noqa: E402
from scheduler.lock import (  # noqa: E402
    is_locked,
    lock_held_since,
    stale_lock_cannot_be_cleared,
)

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"


def _agent_dir() -> Path:
    """`runtime/agent`, derived when asked rather than frozen at import.

    It used to be a module-level constant. That made `RUNTIME_DIR` a knob
    that only half worked: redirecting it — which is how every test and probe
    isolates this view — left `AGENT_DIR` pointing at the developer's real
    `runtime/agent`, so the AGENT block silently reported the live machine
    while every other block reported the fixture.

    Measured, and not hypothetically: a probe written during C31 set
    `RUNTIME_DIR` to a temp tree holding a future-dated `agent_state.json`,
    read back "agent has not run for 3 day(s)" from this repository's own
    runtime, and nearly recorded a working check as missing.

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
        return []
    candidates = [
        path
        for path in local_master.rglob("*.md")
        if path.is_file() and not is_incomplete_write(path.name)
    ]
    if last_backup is None:
        return sorted(candidates)
    reference = last_backup
    newer = []
    for path in candidates:
        try:
            written = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        if reference.tzinfo is None:
            written = written.replace(tzinfo=None)
        if written > reference:
            newer.append(path)
    return sorted(newer)


def _secrets_ever_committed(working_copy: Path) -> tuple[str, ...]:
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
    if not working_copy.is_dir():
        return ()
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
        return ()
    if result.returncode != 0 or not result.stdout:
        return ()
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
    return tuple(sorted(seen))


def _secret_names_the_gate_will_not_recognise(root: Path) -> tuple[str, ...]:
    """Files the Backup gate's own name list would match — except for case.

    `_looks_like_secret()` compares names exactly. Windows compares them
    case-insensitively, so on the platform docs/11 deploys to, a file named
    `ID_RSA` **is** a file named `id_rsa` and the gate does not think so.

    Measured, eight files written into a `daily/` directory (in scope,
    docs/08 §26):

        on disk   .env  CREDENTIALS.JSON  ID_RSA  server.PEM
        flagged   daily\\.env

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


def _junctions_in_scope(local_master: Path) -> tuple[tuple[str, str], ...]:
    """`(path, target)` for directory junctions inside the backup scope.

    A-19/BUG-57 states the exposure; this states that it is happening. The
    two are different, and only the second needs no decision.

    Re-measured (C29) through the real sync, with a junction under `daily/`
    pointing outside Local Master:

        Path.is_symlink()            False   <- the sync's guard misses it
        os.path.isjunction()         True    <- stdlib knows exactly
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

    `os.path.isjunction()` is Python 3.12+; on anything older this reports
    nothing rather than guessing.
    """
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is None or not local_master.is_dir():
        return ()
    found: list[tuple[str, str]] = []
    for name in sorted(_ALLOWED_TOP_LEVEL_DIRS):
        scoped = local_master / name
        if not scoped.exists():
            continue
        candidates = [scoped] + (
            sorted(p for p in scoped.rglob("*")) if scoped.is_dir() else []
        )
        for path in candidates:
            try:
                if not isjunction(path):
                    continue
                target = os.path.realpath(path)
            except OSError:
                continue
            found.append((str(path.relative_to(local_master)), target))
    return tuple(found)


def _misnamed_scope_directories(local_master: Path) -> tuple[tuple[str, str], ...]:
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
    if not local_master.is_dir():
        return ()
    found: list[tuple[str, str]] = []
    try:
        entries = sorted(local_master.iterdir())
    except OSError:
        return ()
    for entry in entries:
        if not entry.is_dir() or entry.name in _ALLOWED_TOP_LEVEL_DIRS:
            continue
        folded = entry.name.casefold()
        for allowed in sorted(_ALLOWED_TOP_LEVEL_DIRS):
            if folded == allowed.casefold():
                found.append((entry.name, allowed))
                break
    return tuple(found)


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


def _read_keep_candidates(
    keep_dir: Path,
) -> tuple[tuple[tuple[str, str, date], ...], tuple[str, ...]]:
    """`(stem, event_id, date)` for every readable KEEP Candidate, read once.

    Two checks need these files — `_candidates_before()` (BUG-46) and
    `_kept_but_not_rendered()` (E-17) — and reading them twice was measured,
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
            when = datetime.fromisoformat(data["timestamp"]).date()
            event_id = data["event_id"]
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None
        if not isinstance(event_id, str):
            return None
        return (path.stem, event_id, when)

    with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
        # `map` preserves input order, so the sorted-filename ordering of the
        # results is identical to the serial version's.
        results = list(pool.map(_read, paths))
    parsed = tuple(item for item in results if item is not None)
    unreadable = tuple(
        path.name for path, item in zip(paths, results) if item is None
    )
    return parsed, unreadable


def _candidates_before(candidates: tuple[tuple[str, str, date], ...], start: date) -> tuple[str, ...]:
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
        f"{stem} ({when.isoformat()})"
        for stem, _event_id, when in candidates
        if when < start
    )


def _kept_but_not_rendered(
    candidates: tuple[tuple[str, str, date], ...], daily_dir: Path
) -> tuple[str, ...]:
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
        return ()
    by_date: dict[str, list[str]] = {}
    for _stem, event_id, when in candidates:
        by_date.setdefault(when.isoformat(), []).append(event_id)

    stranded: list[str] = []
    for when, event_ids in sorted(by_date.items()):
        rendered = daily_dir / f"{when}.md"
        if not rendered.is_file():
            continue
        try:
            text = rendered.read_text(encoding="utf-8")
        except (OSError, ValueError):
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
        summaries = summary_line_indices(lines)
        rendered_lines = {
            line.strip()
            for index, line in enumerate(lines)
            if index not in summaries
        }
        stranded.extend(
            f"{event_id} ({when})"
            for event_id in event_ids
            if f"{_EVENT_ID_LINE_PREFIX}{event_id}".strip() not in rendered_lines
        )
    return tuple(stranded)


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
    daily_count = (
        sum(1 for p in daily_dir.glob("*.md") if not is_incomplete_write(p.name))
        if daily_dir.is_dir()
        else 0
    )
    monthly_files = (
        sorted(p.stem for p in monthly_dir.glob("*.md") if not is_incomplete_write(p.name))
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
            f"{', '.join(unreadable_candidates[:5])}"
            f"{' 외' if len(unreadable_candidates) > 5 else ''} — Scheduler는 "
            f"배치마다 keep 인덱스를 **한 번** 만들므로 이 파일 하나 때문에 "
            f"**모든 날짜의** Daily History 생성이 멈춘다(실측: 9일치 → 0일치). "
            f"사람이 확인해 옮기거나 고쳐야 한다(BACKLOG A-7 / BUG-38)"
        )

    # E-17: stored as Company History, absent from the day it belongs to.
    unrendered = _kept_but_not_rendered(keep_candidates, daily_dir)
    if unrendered:
        print(f"  Daily 미반영 KEEP   : {len(unrendered)}")
        running = (
            " (Runner 실행 중 — 완료 후 재확인 권장)"
            if is_locked(_runner_lock_path())
            else ""
        )
        attention.append(
            f"KEEP Candidate {len(unrendered)}건이 저장돼 있는데 그 날짜의 Daily "
            f"History에 없다: {', '.join(unrendered[:5])}"
            f"{' 외' if len(unrendered) > 5 else ''} — 그 날짜는 이미 렌더링됐고, "
            f"Late Event 병합(6.5단계)의 대상은 **그 실행이 수집한 날짜뿐**이라 "
            f"어떤 실행도 이것만 따로 넣지는 않는다(BACKLOG E-17). 다만 같은 "
            f"날짜의 Event가 나중에 하나라도 더 수집되면 그때 **함께 들어간다** "
            f"(실측: 방치된 EVT-S가 뒤늦은 EVT-N과 같이 "
            f"`added_event_ids=('EVT-S','EVT-N')`으로 병합됐다). 지난 날짜라면 "
            f"그런 Event가 오지 않는 것이 보통이므로 사람이 확인해야 "
            f"한다{running}"
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
            wall_clock = datetime.now().astimezone()
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

        unbacked = _history_newer_than_the_last_backup(local_master, last_backup)
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
        for where, target in _junctions_in_scope(local_master):
            print(f"           junction {where} -> {target}")

        # ...and, when it is the case-fold cause, say exactly what to rename.
        # Without this the operator has to notice a capital letter in a
        # filename and know what it means.
        for actual, expected in _misnamed_scope_directories(local_master):
            attention.append(
                f"Local Master의 `{actual}/`는 백업 범위 밖이다 — Backup은 "
                f"`{expected}/`만 본다(docs/08 §26, 대소문자 구분). 이 디렉터리의 "
                f"Company History는 한 번도 백업되지 않으며 Backup은 계속 "
                f"SUCCESS/NOT_REQUIRED를 보고한다. `{expected}/`로 이름을 바꿔야 "
                f"한다(BACKLOG BUG-55)"
            )

    try:
        state = load_monthly_state(RUNTIME_DIR / "state" / "monthly_history_state.json")
    except MonthlyStateError as exc:
        print("  monthly state       : 읽을 수 없음")
        attention.append(f"monthly state 파일이 손상됨: {exc}")
        return attention

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
    if close is not None and close > now.date():
        print(f"  daily state 정합성  : 미래 날짜 ({close.isoformat()})")
        attention.append(
            f"Daily State가 미래 날짜를 마지막 Daily Close로 기록하고 있다: "
            f"{close.isoformat()} (오늘은 {now.date().isoformat()}) — Scheduler는 "
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
    closed = state.last_successful_monthly_close
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
        f"(Event {reconciliation.checked}건 확인)"
    )
    if reconciliation.orphaned:
        for orphan in reconciliation.orphaned[:5]:
            # `one_line()` for the reason `main()`'s ATTENTION loop gives:
            # `event_id` arrives from another Desktop and a newline inside one
            # forges a whole line of this block. The `!` prefix and the fixed
            # indentation are exactly what a forged line would imitate.
            print(f"                        ! {one_line(orphan.event_id)} "
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
        attention.append(
            f"수집됐지만 History에 들어가지 못한 Event {len(reconciliation.orphaned)}건: "
            f"{', '.join(o.event_id for o in reconciliation.orphaned[:5])}"
            f"{' 외' if len(reconciliation.orphaned) > 5 else ''} — 재실행으로 "
            f"복구되지 않는다(BACKLOG A-20). 사람이 확인해야 한다" + running
        )
    if reconciliation.unreadable:
        attention.append(
            f"processed에 읽을 수 없는 Event {len(reconciliation.unreadable)}건 — "
            f"History 반영 여부를 판단할 수 없다"
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
        leaked = _secrets_ever_committed(working_copy)
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

    # A Monthly that counted an item it did not write down.
    shortfall = _monthly_counts_more_than_it_shows(monthly_dir)
    if shortfall:
        for key, claimed, rendered in shortfall:
            print(f"  Monthly 항목 누락   : {key} ({claimed}건 중 {rendered}건만 기록)")
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

    print(f"  마지막 통합한 달    : {state.last_successful_monthly_close}")
    if state.dirty_months:
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
    if state.last_successful_monthly_close is None:
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
        print(
            f"  전달 정합성         : "
            f"{'OK' if delivery.is_clean else 'UNDELIVERED'} "
            f"(확인 {delivery.checked}건, 이미 수거됨 {delivery.absent}건)"
        )
        for item in delivery.undelivered[:5]:
            # Same rule, same origin: this `event_id` is read back out of a
            # file in `sent/` and is not constrained to one line.
            print(f"                        ! {one_line(item.event_id)} [{item.problem}]")
        if delivery.undelivered:
            delivery_attention.append(
                f"전송 완료로 기록됐지만 sync 폴더에 도착하지 않은 Event "
                f"{len(delivery.undelivered)}건: "
                f"{', '.join(i.event_id for i in delivery.undelivered[:5])} — "
                f"자동 재전송되지 않는다(BACKLOG E-9). 사람이 확인해야 한다"
            )
    else:
        print("  전달 정합성         : 확인 불가 (COMPANY_OPS_AGENT_SYNC_FOLDER 미설정)")
    if snapshot.pending_dates and _agent_start_date() is None:
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
    # "agent has not run for N day(s)", which needs N days to appear and
    # names a symptom rather than the cause.
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

    return list(snapshot.needs_attention(now)) + delivery_attention + lock_attention


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
    # `_is_process_running()` asks whether *a* process has the recorded pid,
    # not whether it is the one that wrote the lock. After a power cut the
    # dead Runner's pid stays in the file; once Windows reassigns that
    # number, every subsequent run is denied the lock and skips — silently,
    # forever, until someone deletes the file by hand. Making the identity
    # check exact means widening the lock file's pinned on-disk contract,
    # which is a decision and stays in BACKLOG.
    #
    # This decides nothing and takes nothing: it reports that a lock has
    # been held longer than plausible. A genuinely long run and a
    # pid-reuse ghost both deserve the same sentence — go and look.
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
        lock_reference = now or datetime.now().astimezone()
        if held_since.tzinfo is None:
            lock_reference = lock_reference.replace(tzinfo=None)
        elif lock_reference.tzinfo is None:
            lock_reference = lock_reference.astimezone()
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
        return [f"Run Manifest를 읽을 수 없다: {DEFAULT_RUN_SUMMARY_PATH}"]

    if summary is None:
        print("  아직 기록된 실행이 없다.")
        return attention

    # `started_at` and the component fields below are read back out of the
    # manifest file, which `read_summary()` does not constrain to one line.
    # Same rule as `main()`'s ATTENTION loop; a hand-edited or restored
    # manifest is a DR path, not an exotic one.
    print(f"  실행 시각   : {one_line(summary.started_at)}")

    # How long ago that was — the question this line never answered.
    #
    # The AGENT section has had "agent has not run for N day(s)" since it was
    # written. The Runner, which is the machine that actually assembles
    # Company History and pushes the Backup, had no equivalent: `started_at`
    # was printed and never compared to anything. So a Runner that simply
    # stops — a Task Scheduler task disabled after a password change, a
    # machine left asleep, the task deleted — leaves this block showing its
    # last SUCCESS, in green, forever.
    #
    # Measured on this machine: the last run was two days old and ATTENTION
    # carried "agent has not run for 2 day(s)" and nothing at all about the
    # Runner.
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
    reference = now or datetime.now().astimezone()
    try:
        started = datetime.fromisoformat(summary.started_at)
    except (TypeError, ValueError):
        started = None
    if started is not None:
        if started.tzinfo is None:
            reference = reference.replace(tzinfo=None)
        elif reference.tzinfo is None:
            reference = reference.astimezone()
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
        if component.metrics:
            rendered = " ".join(
                f"{key}={one_line(value)}" for key, value in sorted(component.metrics.items())
            )
            print(f"      {rendered}")
        if component.artifact_refs:
            print(f"      evidence: {', '.join(component.artifact_refs)}")

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


def main() -> int:
    now = datetime.now().astimezone()
    print(f"DOJOONPASS Company Ops — Status @ {now.isoformat(timespec='seconds')}")
    print()

    attention = _print_company(now)
    print()
    attention.extend(_print_history(now))
    print()
    attention.extend(_print_last_run(now))
    print()
    attention.extend(_print_agent(now))
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
        # `append_line()`. Every ATTENTION message is built from filenames,
        # ids and counts — never from a file's *contents* — and the two that
        # carry an exception message carry a state-file parse error, whose
        # text is positional ("Expecting ',' delimiter: line 3 column 5") and
        # quotes nothing. Over-redacting a path an operator has to act on
        # would cost more than it protects. If a message ever starts carrying
        # a response body, it needs `redact()` too — see
        # `run_company_ops.py::_print_result()`, where one already does.
        print(f"  ! {one_line(item)}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
