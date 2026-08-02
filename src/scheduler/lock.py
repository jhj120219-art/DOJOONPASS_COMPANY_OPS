"""File-based mutual exclusion (docs/07_SCHEDULER_CATCHUP_SPEC.md sections 24-28).

Prevents two run_once() calls (e.g. a manual/startup trigger overlapping
with a scheduled one, section 23) from processing the same dates at once.
This is a lock *file* checked at the start/end of one synchronous call —
not a resident process, not a Thread, not polling.

Simplification versus the spec's literal "check whether the process is
actually still running" (section 27): verifying PID liveness needs
OS-specific APIs this project has no other reason to depend on (no
psutil; `os.kill(pid, 0)` is not a safe/reliable liveness check on
Windows). Instead, a lock older than STALE_AFTER is treated as abandoned.
A full run_once() call (rendering a handful of Markdown files) finishes in
well under a second in this codebase, so a multi-hour threshold cannot
mistake a real in-progress run for a stale one. See this Phase's [ISSUES].
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = PROJECT_ROOT / "runtime" / "locks" / "company_ops.lock"
STALE_AFTER = timedelta(hours=1)


def _read_lock_created_at(lock_path: Path) -> datetime | None:
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["created_at"])
    except (OSError, ValueError, KeyError):
        return None


def try_acquire_lock(lock_path: Path, *, now: datetime) -> bool:
    """Return True if the lock was acquired, False if another run holds it."""
    if lock_path.exists():
        created_at = _read_lock_created_at(lock_path)
        if created_at is not None and (now - created_at) < STALE_AFTER:
            return False
        # missing/unparseable/stale -> fall through and take over the lock

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"process_id": os.getpid(), "created_at": now.isoformat(timespec="seconds")}
    fd, tmp_path = tempfile.mkstemp(dir=lock_path.parent, prefix=".tmp-", suffix=".lock")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, lock_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return True


def release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except OSError:
        pass
