"""Backup Status (docs/08_BACKUP_SPEC.md section 17).

Only the four states section 17 lists as the minimum ("최소 다음 상태를
사용한다") are implemented here. Section 34's optional
BACKUP_REVIEW_REQUIRED is intentionally not added — section 34 itself
allows folding that scenario into BACKUP_FAILED with an error reason
when a separate state isn't needed, which is the choice made for this
Sprint.
"""

from __future__ import annotations

import enum


class BackupStatus(enum.Enum):
    NOT_REQUIRED = "BACKUP_NOT_REQUIRED"
    PENDING = "BACKUP_PENDING"
    SUCCESS = "BACKUP_SUCCESS"
    FAILED = "BACKUP_FAILED"
