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


def _read_lock(lock_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
            return False
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
        os.unlink(lock_path)
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
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
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
                os.unlink(lock_path)
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


def release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except OSError:
        pass
