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
from datetime import date, datetime
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
AGENT_DIR = RUNTIME_DIR / "agent"

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
        if path and _looks_like_secret(PurePosixPath(path).name):
            seen.add(path)
    return tuple(sorted(seen))


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
        # no crafted input. The renderer writes exactly
        # `- Event ID: {event_id}` (daily/markdown.py), so comparing whole
        # lines asks the same question the renderer answers.
        rendered_ids = {
            line.strip()[len(_EVENT_ID_LINE_PREFIX):]
            for line in text.splitlines()
            if line.strip().startswith(_EVENT_ID_LINE_PREFIX)
        }
        stranded.extend(
            f"{event_id} ({when})"
            for event_id in event_ids
            if event_id not in rendered_ids
        )
    return tuple(stranded)


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
            f"{' 외' if len(unrendered) > 5 else ''} — 그 날짜는 이미 렌더링됐고 "
            f"Late Event 병합은 재시도되지 않으므로(BACKLOG E-17) 어떤 실행도 "
            f"이것을 넣지 않는다. 사람이 확인해야 한다{running}"
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
            print(f"                        ! {orphan.event_id} [{orphan.decision.value}]")
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
    return AGENT_DIR / "locks" / "agent.lock"


def _print_agent(now: datetime) -> list[str]:
    if not AGENT_DIR.exists():
        print("AGENT — 이 머신에는 Agent가 설정되어 있지 않다 (runtime/agent 없음)")
        return []

    snapshot = read_status(
        agent_start_date=_agent_start_date(),
        now=now,
        state_path=AGENT_DIR / "state" / "agent_state.json",
        outbox_dir=AGENT_DIR / "outbox",
        sent_dir=AGENT_DIR / "sent",
        rejected_signals_dir=AGENT_DIR / "signals_rejected",
    )

    print("AGENT — 이 머신의 Agent")
    print("-" * 60)
    print(f"  desktop_id          : {snapshot.desktop_id}")
    print(f"  last_run            : {snapshot.last_run}")
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
            sent_dir=AGENT_DIR / "sent", sync_folder=Path(sync_folder)
        )
        print(
            f"  전달 정합성         : "
            f"{'OK' if delivery.is_clean else 'UNDELIVERED'} "
            f"(확인 {delivery.checked}건, 이미 수거됨 {delivery.absent}건)"
        )
        for item in delivery.undelivered[:5]:
            print(f"                        ! {item.event_id} [{item.problem}]")
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

    print(f"  실행 시각   : {summary.started_at}")

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
            print(f"  - {component.name}: SKIPPED (미설정)")
            continue
        failure = component.failure
        print(
            f"  ! {component.name}: {failure.classification} "
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
        print(f"  ! {item}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
