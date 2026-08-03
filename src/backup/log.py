"""Backup Log entry data model (docs/08_BACKUP_SPEC.md section 68).

Only the in-memory record shape and its JSON (de)serialization are
implemented here. Per Issue #5 (unresolved: whether a run gets its own
file, whether entries are appended to one file, and what filename
scheme is used), no file I/O is implemented in this module — no file
creation, no directory creation, no append, no rotation, no
timestamp-based filenames. Where a log entry is actually written is left
to a later Sprint once that question is answered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .result import BackupStatus


@dataclass(frozen=True)
class BackupLogEntry:
    """Fields per docs/08_BACKUP_SPEC.md section 68's "최소 기록" list."""

    run_id: str
    backup_start: datetime
    source: str
    changed_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    commit_hash: str | None
    push_result: str | None
    backup_end: datetime
    final_status: BackupStatus

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "backup_start": self.backup_start.isoformat(),
            "source": self.source,
            "changed_files": list(self.changed_files),
            "deleted_files": list(self.deleted_files),
            "commit_hash": self.commit_hash,
            "push_result": self.push_result,
            "backup_end": self.backup_end.isoformat(),
            "final_status": self.final_status.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BackupLogEntry":
        return cls(
            run_id=data["run_id"],
            backup_start=datetime.fromisoformat(data["backup_start"]),
            source=data["source"],
            changed_files=tuple(data.get("changed_files") or ()),
            deleted_files=tuple(data.get("deleted_files") or ()),
            commit_hash=data.get("commit_hash"),
            push_result=data.get("push_result"),
            backup_end=datetime.fromisoformat(data["backup_end"]),
            final_status=BackupStatus(data["final_status"]),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "BackupLogEntry":
        return cls.from_dict(json.loads(text))
