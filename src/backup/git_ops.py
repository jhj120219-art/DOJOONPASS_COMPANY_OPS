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

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class GitOperationError(Exception):
    """Raised when a git subprocess call exits with a non-zero status."""


class WorkingCopyNotAGitRepositoryError(GitOperationError):
    """Raised when `working_copy_dir` has no `.git` of its own.

    git itself does not require the exact directory passed as `cwd` to be a
    repository root — it walks up through parent directories looking for
    the nearest `.git`. If the Backup Working Copy was never initialised as
    its own repository (e.g. a fresh `runtime/backup_working_copy/` that a
    setup step never ran `git init` in), every git command this module
    issues silently operates on whichever ancestor repository git finds
    instead — for a Working Copy nested inside this project's own checkout,
    that is this project's own repository. `git add -A` / `git commit` /
    `git push` would then act on the caller's real, unrelated working tree
    and its real `origin`, not the intended Backup destination — reachable
    exactly at first-run setup (docs/11 §24-26 Backup 연결), the moment this
    precondition is most likely to be unmet and least likely to be noticed
    before `git push` already ran.
    """


def check_working_copy_is_a_git_repository(working_copy_dir: Path) -> None:
    """Raise WorkingCopyNotAGitRepositoryError unless `working_copy_dir`
    itself (not some parent directory) is a git repository."""
    if not (working_copy_dir / ".git").exists():
        raise WorkingCopyNotAGitRepositoryError(
            f"Backup Working Copy is not a git repository: {working_copy_dir} "
            "(no .git found directly inside it - run `git init` and configure "
            "`origin` there first; docs/08 sections 12, 30)"
        )


# docs/08_BACKUP_SPEC.md §62 (Authentication 실패 -> BACKUP_FAILED, "무한
# Retry Loop를 만들지 않는다") vs §19 (Internet OFF / GitHub 장애 / Push
# 실패 -> BACKUP_PENDING, 다음 Runner에서 재시도). The two are decided from
# git's own stderr, since that is the only signal available here.
#
# Matching is substring-based and lowercase; these are the phrases git and
# the common credential helpers actually emit for a credential/permission
# problem, as opposed to a transient network or remote-side failure.
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "invalid username or password",
    "permission denied",
    "access denied",
    "403 forbidden",
    "support for password authentication was removed",
    "terminal prompts disabled",
    "no anonymous write access",
)


def is_authentication_failure(message: str) -> bool:
    """True when `message` (a GitOperationError's text) indicates a
    credential/permission problem rather than a transient failure.

    docs/08 §21 classifies "Permission 오류 / 인증 설정 오류" as
    BACKUP_FAILED — retrying those on a schedule cannot fix them and only
    produces the infinite retry loop §62 forbids.
    """
    lowered = message.lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


@dataclass(frozen=True)
class GitStatusResult:
    has_changes: bool
    changed_files: tuple[str, ...]
    deleted_files: tuple[str, ...]


# A git call must always end. The Runner holds the system-wide lock for the
# whole Backup step (app/runner.py step 7), so a git command that blocks
# blocks every future Runner execution too — strictly worse than the
# infinite retry loop docs/08 §62 forbids, because a retry loop at least
# keeps collecting Events.
#
# Two ways a push can block indefinitely, both closed below:
#
#   credential prompt   git asks on the terminal, or Git Credential Manager
#                       opens a GUI dialog. Neither has a timeout of its own,
#                       and a scheduled task has nobody to answer them.
#   network stall       a remote that accepts and never responds. git's own
#                       connect timeout covers a refused/black-holed address
#                       (measured: ~21 s), but not a connection that opens
#                       and then stops.
#
# 300 s is far above any healthy push of a few Markdown files and far below
# "never".
_GIT_TIMEOUT_SECONDS = 300.0


def _git_environment() -> dict[str, str]:
    """The environment every git call runs under: no interactive prompts.

    `_AUTH_FAILURE_MARKERS` already lists "terminal prompts disabled" — the
    message git emits when `GIT_TERMINAL_PROMPT=0` is set. Nothing set it,
    so that marker could never match and the condition it describes hung
    instead of failing. Setting it makes the classification reachable.

    `GCM_INTERACTIVE=never` covers the Windows Git Credential Manager,
    which does not consult `GIT_TERMINAL_PROMPT` and would otherwise open a
    dialog no scheduled task can answer.

    Passed as environment rather than `-c` flags on purpose: the command
    line stays exactly the approved set
    (tests/test_spec_conformance.py::test_git_ops_runs_only_the_approved_command_set).
    """
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "never"
    return environment


def _run_git(args: Sequence[str], repo_dir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            # git speaks UTF-8; the Windows default here does not. Without
            # this pair, `text=True` decodes with the locale codepage
            # (measured on this machine: cp949), and the failure mode is the
            # worst available — the decode happens in subprocess's reader
            # *thread*, so the `UnicodeDecodeError` never reaches the caller
            # and `result.stdout` silently becomes `None`. `_parse_porcelain`
            # would then raise AttributeError, which is not
            # `GitOperationError`, so it would escape `backup/runner.py`'s
            # classification and abort the run instead of being reported.
            #
            # Today's output is ASCII by a chain of defaults — Daily and
            # Monthly filenames are ISO dates, and `core.quotepath` escapes
            # non-ASCII paths — so this is removing a dependency on those
            # defaults rather than fixing a live break. `errors="replace"`
            # makes the guarantee unconditional: this function can no longer
            # fail to decode anything.
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        # Reported as an ordinary GitOperationError so backup/runner.py
        # classifies it the way it classifies any other non-auth failure:
        # BACKUP_PENDING, retried on the next run (docs/08 §19). A timeout is
        # transient by nature, and `is_authentication_failure()` correctly
        # does not match this message.
        raise GitOperationError(
            f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS:.0f}s "
            f"(no output; the remote may be unreachable or waiting for credentials)"
        ) from exc

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


def git_head_commit(repo_dir: Path) -> str:
    """The Working Copy's current HEAD commit hash.

    Uses the same `rev-parse HEAD` this module already runs inside
    git_commit() — no new git command is introduced. Needed by the Backup
    Pending retry path (docs/08 §19), which pushes a commit that a *previous*
    run created and therefore never recorded a hash for.
    """
    return _run_git(["rev-parse", "HEAD"], repo_dir).strip()


def git_push(repo_dir: Path) -> None:
    """docs/08 section 12, 32-36: plain push only, no pull, no force.

    A rejected push (section 32) is not retried, merged, or force-pushed
    here — it simply raises GitOperationError like any other failure,
    and the Working Copy / Local Master are left untouched because no
    other command in this module ever mutates them on failure.
    """
    _run_git(["push"], repo_dir)
