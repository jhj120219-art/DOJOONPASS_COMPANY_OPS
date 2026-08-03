"""Backup State (docs/08_BACKUP_SPEC.md section 22).

Holds exactly the three fields section 22's example shows:
last_successful_backup, last_backup_commit, backup_status. Load/save
shape mirrors this project's other state files (e.g.
scheduler/state.py) for the same reason: atomic write, missing file
means "no state yet".
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .result import BackupStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "runtime" / "state" / "backup_state.json"


@dataclass
class BackupState:
    last_successful_backup: datetime | None = None
    last_backup_commit: str | None = None
    backup_status: BackupStatus | None = None


def load_state(state_path: Path) -> BackupState:
    if not state_path.exists():
        return BackupState()

    data = json.loads(state_path.read_text(encoding="utf-8"))
    timestamp_value = data.get("last_successful_backup")
    status_value = data.get("backup_status")
    return BackupState(
        last_successful_backup=(
            datetime.fromisoformat(timestamp_value) if timestamp_value else None
        ),
        last_backup_commit=data.get("last_backup_commit"),
        backup_status=BackupStatus(status_value) if status_value else None,
    )


def save_state(state_path: Path, state: BackupState) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_successful_backup": (
            state.last_successful_backup.isoformat()
            if state.last_successful_backup is not None
            else None
        ),
        "last_backup_commit": state.last_backup_commit,
        "backup_status": (
            state.backup_status.value if state.backup_status is not None else None
        ),
    }
    fd, tmp_path = tempfile.mkstemp(dir=state_path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, state_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
