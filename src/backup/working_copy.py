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

Secret Scan (section 29) IS implemented here, as `scan_for_secrets()`.
This paragraph used to say it was not, and that Issue #3 (what action to
take on a match) was unresolved — both claims are now false and the
function's own docstring was corrected earlier for the same reason.
Issue #3 was decided: backup/runner.py calls `scan_for_secrets()` at step
2 and a match FAILS the whole backup before Working Copy Sync or any git
command runs. Detection remains filename-based only; file contents are
never read (measured scope is in the function's docstring).

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


def _content_differs(src: Path, dst: Path) -> bool:
    """Exact, *content*-based comparison — never mtime/stat-signature based.

    Deliberately a thin named wrapper over `filecmp.cmp(shallow=False)`
    rather than a hand-rolled fast path. This Sprint measured an explicit
    size short-circuit here (`getsize(src) != getsize(dst)` before
    delegating) and found it to be a net ~19% *slowdown* at every scale
    from 30 to 10,000 files: `filecmp.cmp` already performs exactly that
    size check internally, even with shallow=False (CPython filecmp:
    `s1 = _sig(os.stat(f1)); ...; if s1[1] != s2[1]: return False`), so
    the extra stat calls were pure duplicated syscalls with no benefit.

    Detection stays content-based on purpose: switching to a stat/mtime
    signature (`shallow=True`) is ~9x faster but silently weakens what
    "modified" means, which is a Backup contract change, not an
    optimization — see this Sprint's report.
    """
    return not filecmp.cmp(src, dst, shallow=False)


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
    """docs/08 section 29: detect known secret-like FILENAMES only.

    Docstring corrected — every claim in the previous version is now false,
    and one of them was dangerous: it declared that no caller existed in
    backup/runner.py, that a sync excluded nothing, and that Issue #3 was
    still open. Issue #3 has since been decided: backup/runner.py
    calls this at step 2 and a match FAILS the whole backup before Working
    Copy Sync or any git command runs, recording the matched paths in the
    BackupLogEntry. Left as it was, the docstring invited someone to delete a
    live security gate as dead code.

    What it does NOT do, measured rather than assumed. Planting twelve
    realistic secrets under a master directory, this catches three:

        .env, .env.local, id_rsa                        detected (filename)
        secrets.json, credentials.yml, token.txt        NOT detected
        a Notion token / GitHub PAT / AWS key / RSA
        private key pasted into a Daily History file    NOT detected (0 of 6)

    File CONTENT is never read, so a secret pasted into Company History
    reaches the backup remote untouched. That is the documented design of
    section 29, not a defect in this function — but because a match now
    blocks the backup, the gate reads as stronger protection than it is.
    Widening it is a policy decision, not a code cleanup.
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
    against Master's current files to compute added/modified/deleted first,
    without touching disk. Section 31/44-47: if any deletion is detected,
    this call applies nothing at all (neither the deletion nor any
    unrelated add/modify) and just reports it — runner.py's caller then
    stops before commit/push. Applying the deletion to the Working Copy
    here regardless (as a prior version of this function did) would make
    the very next call's "before" state already match the deleted Master,
    so the next run would see no deletion to report and would silently
    finish committing/pushing it — the one-run block would not actually
    hold the line. Only once `deleted` comes back empty is the Working
    Copy actually brought in sync with Master (new/changed files copied in).
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
        elif _content_differs(src, dst):
            modified.append(rel_path)

    deleted = sorted(existing_files - master_files)
    if deleted:
        return WorkingCopySyncResult(added=tuple(added), modified=tuple(modified), deleted=tuple(deleted))

    for rel_path in added + modified:
        src = master_dir / rel_path
        dst = working_copy_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    return WorkingCopySyncResult(added=tuple(added), modified=tuple(modified), deleted=())
