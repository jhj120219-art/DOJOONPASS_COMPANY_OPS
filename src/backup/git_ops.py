"""Git operations for the Backup Working Copy (docs/08_BACKUP_SPEC.md
sections 5, 12, 23-25, 32-36).

Thin subprocess wrappers around `git status`, `git add`, `git commit`,
and `git push` only. Every call raises GitOperationError on a non-zero
exit code with git's own stderr attached — no error is caught and
silenced here (per this Sprint's instructions).

What this module never does, in any code path:
    - `git pull` (never called, anywhere)
    - `git push --force` / `--force-with-lease` (no force option exists
      in git_push()'s implementation at all)
    - `git reset --hard`, `git checkout -- .`, `git clean -fd`,
      `git restore .` (section 5's forbidden list) — none of these are
      implemented in this module
    - automatic merge/conflict resolution on a rejected push (section
      32-36) — a rejection is simply a non-zero exit from `git push`,
      which surfaces as GitOperationError like any other failure

Repository URL and branch name are never inferred or hardcoded here —
`git push` relies entirely on whatever upstream tracking branch the
Working Copy's remote was already configured with (section 30).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class GitOperationError(Exception):
    """Raised when a git subprocess call exits with a non-zero status."""


@dataclass(frozen=True)
class GitStatusResult:
    has_changes: bool
    changed_files: tuple[str, ...]
    deleted_files: tuple[str, ...]


def _run_git(args: Sequence[str], repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitOperationError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _parse_porcelain(output: str) -> GitStatusResult:
    changed: list[str] = []
    deleted: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        code, path = line[:2], line[3:]
        if "D" in code:
            deleted.append(path)
        else:
            changed.append(path)
    return GitStatusResult(
        has_changes=bool(changed or deleted),
        changed_files=tuple(changed),
        deleted_files=tuple(deleted),
    )


def git_status(repo_dir: Path) -> GitStatusResult:
    """docs/08 section 25: check for changes before deciding to commit."""
    output = _run_git(["status", "--porcelain"], repo_dir)
    return _parse_porcelain(output)


def git_add_all(repo_dir: Path) -> None:
    """docs/08 section 12 Flow's "git add" step."""
    _run_git(["add", "-A"], repo_dir)


def git_commit(repo_dir: Path, message: str) -> str:
    """docs/08 section 25: no changes -> no commit is created.

    Returns the new commit's hash, or an empty string if there was
    nothing to commit (checked via the same status parsing git_status()
    uses, so this is safe to call regardless of what the caller already
    checked).
    """
    status = _parse_porcelain(_run_git(["status", "--porcelain"], repo_dir))
    if not status.has_changes:
        return ""

    _run_git(["commit", "-m", message], repo_dir)
    return _run_git(["rev-parse", "HEAD"], repo_dir).strip()


def git_push(repo_dir: Path) -> None:
    """docs/08 section 12, 32-36: plain push only, no pull, no force.

    A rejected push (section 32) is not retried, merged, or force-pushed
    here — it simply raises GitOperationError like any other failure,
    and the Working Copy / Local Master are left untouched because no
    other command in this module ever mutates them on failure.
    """
    _run_git(["push"], repo_dir)
