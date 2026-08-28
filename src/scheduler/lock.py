"""File-based mutual exclusion (docs/07_SCHEDULER_CATCHUP_SPEC.md sections 24-28).

Prevents two run_once() calls (e.g. a manual/startup trigger overlapping
with a scheduled one, section 23) from processing the same dates at once.
This is a lock *file* taken and released around one synchronous call — not
a resident process, not a Thread, not polling.

Acquisition is a single atomic `os.open(..., O_CREAT | O_EXCL)`. That is
the whole basis of section 25's invariant ("Lock으로 하나의 Runner만
실행되게 한다"): the OS lets exactly one caller create the file, so exactly
one caller can be told it holds the lock. An earlier version checked
`lock_path.exists()` and later wrote through `os.replace()`; nothing joined
those two steps, so runs starting together all passed the check and all
believed they had acquired it, and Windows additionally raised
PermissionError on the contended replace. Both failure modes are gone with
O_EXCL — a loser simply gets FileExistsError, which is exactly the "someone
else holds it" signal.

Section 27 requires checking whether the recorded process is actually
still running ("단순 시간 경과만으로 실행 중인 정상 Process를 강제
종료하지 않는다") rather than deciding staleness from elapsed time alone.
`_is_process_running()` implements that check with stdlib-only calls (no
psutil): `tasklist` on Windows (this project's target OS, per
DOJOONPASS_COMPANY_OPS environment), `os.kill(pid, 0)` elsewhere.
Taking over a stale lock is itself made safe by `_take_over_stale()`, which
removes it only if it has not changed since it was read — otherwise two
runs that both judged the same lock stale would delete each other's fresh
lock and both proceed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = PROJECT_ROOT / "runtime" / "locks" / "company_ops.lock"


def _long_path(path: Path) -> Path:
    """A Path usable even past Windows' 260-character MAX_PATH.

    Lock filenames are not chosen by this module, so a legal-but-long name
    (or one with characters like `:` that create an NTFS alternate data
    stream) must not be mistaken for an acquisition failure. The `\\\\?\\`
    prefix lifts the limit, but only for an absolute, backslash-separated
    path, so it is built from the resolved path rather than the raw input.

    An input that *already* carries the prefix is returned unchanged. This
    used to be a documented non-case ("`Path.resolve()` strips an existing
    `\\\\?\\` prefix, so there is nothing to special-case") — that claim is
    false on the Python this project runs (verified on 3.13: `resolve()`
    preserves the prefix). The prefixed path then still began with `\\\\`,
    so it took the UNC branch below and came out as
    `\\\\?\\UNC\\?\\C:\\...` — not a path any Windows API accepts. Every
    `os.open`/`unlink`/`read_text` on it fails with OSError, which
    `try_acquire_lock()` reports as "someone else holds the lock": a Runner
    given an already-prefixed `lock_path` could never acquire it and would
    skip every single run, silently, forever.
    """
    if sys.platform != "win32":
        return path
    resolved = path.resolve()
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        return resolved
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def _read_lock(lock_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(_long_path(lock_path).read_text(encoding="utf-8"))
    # `RecursionError` is what `json.loads()` answers a deeply nested file
    # with, and it is a `RuntimeError` rather than a `ValueError` — so the
    # conversion below, which is the whole of what this promises about a
    # file it cannot read, used to be skipped for that one shape. One home
    # for the reasoning: `ADeeplyNestedStateFileReadsLikeAnyOtherCorruptOneTests`
    # (C65), which also holds the roster this is one of nine entries in.
    except (OSError, ValueError, RecursionError):
        return None


def _is_process_running(pid: object) -> bool:
    """docs/07_SCHEDULER_CATCHUP_SPEC.md §27: Lock에 기록된 Process가
    실제로 실행 중인지 확인한다."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            # BUG-54. The probe learned nothing -- it did not learn that the
            # process is gone. Answering False here means "stale", and stale
            # means the caller unlinks a live holder's lock and takes the
            # critical section: two Runners at once, which is the BUG-18/BUG-20
            # condition the O_EXCL rewrite was written to remove. That rewrite
            # does not help, because the second run legitimately unlinks the
            # first one's lock and creates its own.
            #
            # Reachable without anything unusual. `tasklist` enumerates every
            # process under a 5 s timeout, and the moment it is most likely to
            # be slow -- heavy load -- is the moment two scheduled runs are
            # most likely to overlap. A stripped PATH or a hardened image with
            # no `tasklist` fails this way permanently.
            #
            # The direction is the whole thing, and the costs are not
            # symmetric: skipping a run costs one cycle, and the next run
            # catches up (docs/07 §20). Taking a live holder's lock costs
            # Company History -- measured at up to 36% of History Candidates
            # lost under that condition.
            #
            # This is not a new policy. It is the answer this same function
            # already gives on POSIX, four lines below: `PermissionError` from
            # `os.kill(pid, 0)` means "a process is there and it is not ours",
            # and that arm returns True. Windows was the only branch that
            # turned "I cannot tell" into "it is gone".
            #
            # Why the objection recorded against this no longer holds. It was
            # that a probe which keeps failing would leave a lock nobody can
            # ever reclaim, silently. Two checks written since then make that
            # condition loud instead: `ops_status.py`'s
            # LOCK_STUCK_AFTER_HOURS line reads `lock_held_since()` (which
            # answers, on this path, precisely because the holder now counts
            # as alive) and names this shape in as many words, and the
            # Runner/Agent silence checks raise a run that stops happening
            # after SILENT_AFTER_DAYS. A permanently unreclaimable lock is now
            # reported; a broken mutual exclusion still would not be.
            return True
        return result.returncode == 0 and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _take_over_stale(lock_path: Path, observed: dict[str, Any] | None) -> bool:
    """Remove a lock judged stale, but only if it has not changed since.

    Two runs can both read the same stale lock and both decide to take it
    over. If each then unlinked unconditionally, the second unlink would
    delete the *fresh* lock the first had just created, and both would
    believe they hold it. Re-reading and comparing before the unlink means a
    lock that someone else already replaced is left alone.
    """
    if _read_lock(lock_path) != observed:
        return False
    try:
        os.unlink(_long_path(lock_path))
    except OSError:
        return False
    return True


def try_acquire_lock(lock_path: Path, *, now: datetime) -> bool:
    """Return True if the lock was acquired, False if another run holds it.

    Acquisition is a single atomic `os.open(..., O_CREAT | O_EXCL)`: the OS
    guarantees that exactly one caller can create the file, so exactly one
    caller can ever be told True for a given lock.

    This replaces a check-then-write sequence (`if lock_path.exists()` …
    later … `os.replace(tmp, lock_path)`) that was not atomic. Nothing joined
    the two steps, so several runs starting together all passed the existence
    check and all "acquired"; `os.replace()` overwrites unconditionally, so
    the last writer merely won the file while every caller had already been
    told True. Measured before this change (8 processes x 12 trials): 2-3
    simultaneous holders in every trial, and zero clean denials. On Windows a
    contended `os.replace()` additionally raised PermissionError, so a
    contended acquisition could crash the Runner instead of returning False —
    O_CREAT|O_EXCL has no such failure mode, it simply reports FileExistsError
    which is exactly the "someone else holds it" signal.

    docs/07_SCHEDULER_CATCHUP_SPEC.md §25 states the invariant this restores:
    "Lock으로 하나의 Runner만 실행되게 한다" — one system-wide holder.
    §27's rule is unchanged: a lock whose recorded process is no longer
    running is stale and may be taken over, never a lock judged only by
    elapsed time.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"process_id": os.getpid(), "created_at": now.isoformat(timespec="seconds")}

    # At most two passes: the second exists only to retry once after a stale
    # lock was cleared. A further collision means another run got there first.
    for _ in range(2):
        try:
            fd = os.open(_long_path(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            observed = _read_lock(lock_path)
            pid = observed.get("process_id") if observed is not None else None
            if _is_process_running(pid):
                return False
            # Unparseable lock, or recorded process not running -> stale (§27).
            if not _take_over_stale(lock_path, observed):
                return False
            continue
        except OSError:
            return False

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except BaseException:
            try:
                os.unlink(_long_path(lock_path))
            except OSError:
                pass
            raise

        # Confirm the lock on disk is still ours. A run that was concurrently
        # taking over a stale lock could have unlinked and recreated it in the
        # instant between our create and our write; if so, it holds the lock
        # and we do not.
        confirmed = _read_lock(lock_path)
        if confirmed is None or confirmed.get("process_id") != os.getpid():
            return False
        return True

    return False


def is_locked(lock_path: Path) -> bool:
    """Is a live process holding this lock right now? Read-only.

    Deliberately not `try_acquire_lock()`. That call *competes* for the
    lock: it creates the file when free and can take over one it judges
    stale. A status view must never do either — an operator running
    `ops_status.py` while a Runner works must not be able to disturb it, and
    the docstring of that script promises exactly that.

    So this only reads: the lock file exists, it parses, and the process id
    inside belongs to something currently running. Anything else is False,
    including a lock left behind by a dead process — which is the same
    judgement `try_acquire_lock()` makes (§27), reached without acting on it.

    Nothing here decides staleness or timing. `lock_held_since()` answers
    the "for how long" question separately.
    """
    observed = _read_lock(lock_path)
    if observed is None:
        return False
    return _is_process_running(observed.get("process_id"))


def lock_held_since(lock_path: Path) -> datetime | None:
    """When the live holder acquired this lock, or None if nobody holds it.

    Reads the `created_at` field the lock file has always carried — no new
    field, so `LockFileContractTests`' pinned on-disk shape is untouched.
    None also covers a lock whose `created_at` is missing or unparseable: a
    time this cannot read is not a time to report.

    What this is for: `_is_process_running()` checks that *a* process has
    that pid, not that it is the same process that wrote the lock. After a
    power cut the dead Runner's pid stays in the file, and once Windows
    reassigns that number to something unrelated the lock looks held
    forever — every run then skips, silently, until a human deletes the
    file. Making the identity check exact means widening the lock file's
    contract, which is a decision (BACKLOG). Noticing that a lock has been
    held implausibly long needs neither.
    """
    observed = _read_lock(lock_path)
    if observed is None or not _is_process_running(observed.get("process_id")):
        return None
    created_at = observed.get("created_at")
    if not isinstance(created_at, str):
        return None
    try:
        return datetime.fromisoformat(created_at)
    except ValueError:
        return None


def stale_lock_cannot_be_cleared(lock_path: Path) -> bool:
    """Is there a lock file that no run will ever be able to take over?

    Read-only, like `is_locked()` and `lock_held_since()`: it removes
    nothing, takes nothing, and changes no attribute.

    The condition is narrow on purpose, and every part of it is required:

        the lock file exists
        the process it records is NOT running  -> §27 says it is stale
        the file is not writable               -> `os.unlink()` will fail

    A stale lock on its own is ordinary — the next run takes it over, which
    is exactly what §27 provides for, so reporting that would be noise. What
    this reports is a stale lock that the takeover *cannot* remove, and that
    is permanent by construction: the recorded process is dead, so nobody
    will ever rewrite the file, and the attribute stops every unlink.

    Why it needed its own detector (BUG-42). `try_acquire_lock()` answers
    False, and False downstream means "another run holds it" — so the Runner
    reads a permanent, unrecoverable condition as routine contention and
    skips, on schedule, forever. Measured: `try_acquire_lock()` False on
    every call, and **both existing detectors blind to it** —
    `is_locked()` False and `lock_held_since()` None, because both key on a
    *live* process and this one is dead. A lock-skipped run also writes no
    manifest (docs/14 §7, deliberately), so nothing else was left to notice.

    Reachable without anyone doing anything unusual: files restored from a
    Windows backup commonly come back read-only, and sync clients and
    antivirus tools both set the attribute.

    Detection only. Whether the Runner may strip an attribute from a file it
    did not create, and whether `try_acquire_lock()` should distinguish
    "contended" from "cannot proceed", are the two decisions BUG-42 records —
    neither is taken here.
    """
    path = _long_path(lock_path)
    observed = _read_lock(lock_path)
    if observed is None and not path.exists():
        return False
    if observed is not None and _is_process_running(observed.get("process_id")):
        return False
    return not os.access(path, os.W_OK)


def release_lock(lock_path: Path) -> None:
    try:
        _long_path(lock_path).unlink()
    except OSError:
        pass
