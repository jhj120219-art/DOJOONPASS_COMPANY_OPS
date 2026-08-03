"""Local Master -> Backup Working Copy sync (docs/08_BACKUP_SPEC.md
sections 9-13, 30, 43-45, 48).

One-directional only: Local Master -> Backup Working Copy. Copying
Working Copy -> Local Master is never implemented here (section 13's
explicit prohibition). No git commands are used in this module — git
status/add/commit/push belongs to git_ops.py (out of scope this Sprint).

Deletion handling: section 43-45 describe detecting a Local Master file
deletion by comparing against the Backup Working Copy's existing state,
and section 49's "Master suddenly empty" is the extreme case of the same
comparison (every previously-known file reported deleted at once).
Both are computed here the same way: the Working Copy already holds the
result of the last sync, so diffing its current files against Master's
current files before overwriting it is exactly that comparison — no git
command is needed for it. What to do about a non-empty `deleted` result
(stop before commit/push, per section 44-47) is left to runner.py; this
module only computes and reports it via WorkingCopySyncResult.deleted.

Secret Scan (section 29) is not implemented here — Issue #3 (what action
to take on a match) is still unresolved, per this Sprint's instructions.

Backup target scope (sections 26-28): only files under `daily/` and
`monthly/` (section 26's "포함" list) are considered at all — by either
Master or Working Copy — for copying, modifying, or deletion detection.
`decisions/` is not included (section 26 marks it as conditional/"필요
시", not part of the required baseline). Anything else directly under
Master (stray files, `.env`, logs, caches, etc. per section 27) is
simply never looked at by this module, since it was never in scope to
begin with — the sync algorithm and deletion-detection algorithm
themselves are unchanged; only which files they ever see is restricted.
"""

from __future__ import annotations

import filecmp
import shutil
from dataclasses import dataclass
from pathlib import Path


class MasterDirectoryError(Exception):
    """Raised when the Local Master directory does not exist (section 48)."""


@dataclass(frozen=True)
class WorkingCopySyncResult:
    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]


_ALLOWED_TOP_LEVEL_DIRS = frozenset({"daily", "monthly"})


def _is_in_scope(rel_path: str) -> bool:
    """docs/08 section 26: only `daily/` and `monthly/` are in scope."""
    parts = Path(rel_path).parts
    return bool(parts) and parts[0] in _ALLOWED_TOP_LEVEL_DIRS


def _relative_files(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    result: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if _is_in_scope(rel):
            result.add(rel)
    return result


_SECRET_EXACT_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "id_rsa",
        "id_ed25519",
    }
)
_SECRET_SUFFIXES = (".pem", ".p12", ".key")


def _looks_like_secret(name: str) -> bool:
    return name in _SECRET_EXACT_NAMES or name.endswith(_SECRET_SUFFIXES)


def scan_for_secrets(root: Path) -> tuple[str, ...]:
    """docs/08 section 29: detect known secret-like filenames only.

    Detection only. No exception is raised, nothing is excluded from a
    sync, nothing is logged, and this is not called from
    sync_to_working_copy(), check_master_directory(), or runner.py —
    what to do about a match is Issue #3, still unresolved.
    """
    if not root.is_dir():
        return ()
    matches = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and _looks_like_secret(path.name)
    ]
    return tuple(sorted(matches))


def check_master_directory(master_dir: Path) -> None:
    """Raise MasterDirectoryError if `master_dir` does not exist (section 48).

    "기존에는 History가 있었는데 갑자기 비어있는" 경우(section 49)는 여기서
    다루지 않는다 — 그 판단은 sync_to_working_copy()의 `deleted` 결과로
    이루어진다(모듈 docstring 참고).
    """
    if not master_dir.is_dir():
        raise MasterDirectoryError(f"Local Master directory not found: {master_dir}")


def sync_to_working_copy(master_dir: Path, working_copy_dir: Path) -> WorkingCopySyncResult:
    """Copy Local Master into the Backup Working Copy, one direction only.

    Compares the Working Copy's files as they exist *before* this call
    against Master's current files to compute added/modified/deleted,
    then makes the Working Copy match Master exactly (new/changed files
    copied in, files no longer in Master removed from the Working Copy).
    """
    working_copy_dir.mkdir(parents=True, exist_ok=True)

    master_files = _relative_files(master_dir)
    existing_files = _relative_files(working_copy_dir)

    added: list[str] = []
    modified: list[str] = []
    for rel_path in sorted(master_files):
        src = master_dir / rel_path
        dst = working_copy_dir / rel_path
        if rel_path not in existing_files:
            added.append(rel_path)
        elif not filecmp.cmp(src, dst, shallow=False):
            modified.append(rel_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    deleted = sorted(existing_files - master_files)
    for rel_path in deleted:
        target = working_copy_dir / rel_path
        if target.exists():
            target.unlink()

    return WorkingCopySyncResult(added=tuple(added), modified=tuple(modified), deleted=tuple(deleted))
