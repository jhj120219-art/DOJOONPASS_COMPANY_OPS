"""File-based mutual exclusion (docs/07_SCHEDULER_CATCHUP_SPEC.md sections 24-28).

Prevents two run_once() calls (e.g. a manual/startup trigger overlapping
with a scheduled one, section 23) from processing the same dates at once.
This is a lock *file* checked at the start/end of one synchronous call —
not a resident process, not a Thread, not polling.

Section 27 requires checking whether the recorded process is actually
still running ("단순 시간 경과만으로 실행 중인 정상 Process를 강제
종료하지 않는다") rather than deciding staleness from elapsed time alone.
`_is_process_running()` implements that check with stdlib-only calls (no
psutil): `tasklist` on Windows (this project's target OS, per
DOJOONPASS_COMPANY_OPS environment), `os.kill(pid, 0)` elsewhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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


def try_acquire_lock(lock_path: Path, *, now: datetime) -> bool:
    """Return True if the lock was acquired, False if another run holds it."""
    if lock_path.exists():
        lock_data = _read_lock(lock_path)
        pid = lock_data.get("process_id") if lock_data is not None else None
        if _is_process_running(pid):
            return False
        # missing/unparseable lock, or recorded process not running -> stale
        # -> fall through and take over the lock (section 27)

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
