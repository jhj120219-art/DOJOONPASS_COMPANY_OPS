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


class BackupStateError(ValueError):
    """Raised when backup_state.json exists but cannot be read as valid
    Backup state (bad JSON, wrong shape, unknown status value).

    Never raised for a simply-missing file — that starts an empty state.
    State Recovery 통일 (CEO 승인 A안): same contract and wording as
    `collector.state.CollectorStateError` and
    `scheduler.state.SchedulerStateError`, per docs/10 §46.
    """


@dataclass
class BackupState:
    last_successful_backup: datetime | None = None
    last_backup_commit: str | None = None
    backup_status: BackupStatus | None = None


def load_state(state_path: Path) -> BackupState:
    if not state_path.exists():
        return BackupState()

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupStateError(
            f"backup state file is corrupted: {state_path} ({exc})"
        ) from exc

    if not isinstance(data, dict):
        raise BackupStateError(
            f"backup state file must contain a JSON object: {state_path}"
        )

    timestamp_value = data.get("last_successful_backup")
    status_value = data.get("backup_status")
    commit_value = data.get("last_backup_commit")

    if commit_value is not None and not isinstance(commit_value, str):
        raise BackupStateError(
            f"backup state file has an invalid last_backup_commit field: {state_path}"
        )

    try:
        parsed_timestamp = (
            datetime.fromisoformat(timestamp_value) if timestamp_value else None
        )
    except (TypeError, ValueError) as exc:
        raise BackupStateError(
            f"backup state file has an unparseable last_successful_backup: "
            f"{state_path} ({exc})"
        ) from exc

    try:
        parsed_status = BackupStatus(status_value) if status_value else None
    except ValueError as exc:
        raise BackupStateError(
            f"backup state file has an unknown backup_status: {state_path} ({exc})"
        ) from exc

    return BackupState(
        last_successful_backup=parsed_timestamp,
        last_backup_commit=commit_value,
        backup_status=parsed_status,
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
