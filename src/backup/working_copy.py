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
import os
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

# Byte-identical copy of `reporter.local_output.INCOMPLETE_WRITE_PREFIX` /
# `is_incomplete_write()`, which carries the full reasoning. Copied rather
# than imported because `backup` is a leaf package with no project imports at
# all (LayeringInvariantTests); `IncompleteWriteInvariantTests` asserts the
# copies agree.
INCOMPLETE_WRITE_PREFIX = ".tmp-"


def is_incomplete_write(name: str) -> bool:
    """Whether `name` is an atomic writer's staging file, not a finished artifact."""
    return name.startswith(INCOMPLETE_WRITE_PREFIX)


def _is_in_scope(rel_path: str) -> bool:
    """docs/08 section 26: only `daily/` and `monthly/` are in scope.

    A staging file (`.tmp-…md`) under either directory is excluded. This is
    not a narrowing of section 26's "포함" list but the same reading section
    27 already applies to everything else under Master: what is in scope is
    *Company History*, and a file an atomic writer has not committed yet is
    not history — `daily/generator.py` and `monthly/generator.py` both
    `mkstemp(dir=<the in-scope directory>)`, so the residue of a run killed
    mid-write is this pipeline's own by-product sitting in its own output
    directory.

    Measured before this exclusion existed, with a `.tmp-xyz.md` left in
    `master/daily/`:

        sync_to_working_copy()   added=('daily/.tmp-xyz.md', …)  -> committed
                                 and pushed to the backup remote as a
                                 truncated day of Company History
        then removed from Master  deleted=('daily/.tmp-xyz.md',) -> the
                                 deletion gate (sections 43-47) fails the
                                 backup, and because `sync_to_working_copy()`
                                 applies nothing while `deleted` is
                                 non-empty, it fails on every subsequent run

    The second line is the reason this could not be left as cosmetic noise:
    deleting the garbage — the only sane operator response to it — is what
    turns it into a permanent BACKUP_FAILED. Excluding it on both sides
    (Master *and* Working Copy, since both go through this predicate) means
    neither direction can arm that trap.
    """
    parts = Path(rel_path).parts
    if not parts or parts[0] not in _ALLOWED_TOP_LEVEL_DIRS:
        return False
    return not is_incomplete_write(parts[-1])


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
    """Regular files only, in scope.

    A symlink/junction is excluded even when it resolves to a file
    (`path.is_symlink()` checked before `path.is_file()`, since
    `Path.is_file()` follows the link). Without this, a link placed under
    `daily/`/`monthly/` pointing outside `root` would have its TARGET's
    content copied into the Working Copy and pushed to the git remote,
    while `scan_for_secrets()` — filename-based, see that function's
    docstring — checks only the link's own name and cannot see what it
    resolves to. Reproduced end to end this Sprint: a symlink named
    `notes.md` pointing at an external `.env` was not flagged by the scan
    and its content was copied into the Working Copy verbatim.

    Walked with `os.scandir` rather than `rglob("*")`, and only into the
    top-level names `_is_in_scope()` can accept. The result is the same set
    by construction — every path this skips has a `parts[0]` that predicate
    already rejects — and `RelativeFilesWalkTests` asserts it against the
    `rglob` form over an adversarial tree rather than by argument.

    Two reasons it was worth doing rather than left alone.

      * `rglob("*")` walks **everything**, and the Working Copy is a git
        repository: `.git/` is the largest directory in it and not one file
        of it can ever be in scope. Every entry there cost two stat calls
        (`is_symlink()`, then `is_file()`) to be thrown away.
      * The cost is paid twice per Runner execution and, since C45, once per
        `ops_status.py` invocation as well — and that script's whole premise
        is that a person runs it first, casually.

    Measured on this machine, warm, listing Master **and** Working Copy
    (which is what one `sync_to_working_copy()` and one `ops_status.py`
    each do):

        one year    730 files, 600-object .git   55.4 ms -> 2.6 ms   21.3x
        three years 2190 files                   82.6 ms -> 6.3 ms   13.2x
        ten years   7300 files                  271.1 ms -> 23.1 ms  11.7x

    Within `ops_status._print_history()` that is 29.6% of the block down to
    1.9% at one year, and 29.2% down to 3.2% at ten.

    Same shape, and the same reason, as the `glob+is_file` -> `scandir` swap
    `ops_status._daily_dates()` already carries (16x there).

    A *file* named `daily` sitting directly in the root is still considered,
    not pruned: `_is_in_scope("daily")` is True (`parts[0]` is `daily` and
    the basename is not a staging name), so the walk keeps any top-level
    entry whose NAME is in scope and only decides afterwards whether it is a
    directory to descend or a file to test. Dropping it would have been a
    behaviour change hidden inside an optimisation.
    """
    root = Path(root)
    if not root.is_dir():
        return set()

    result: set[str] = set()

    def _walk(directory: Path, prefix: str) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            rel = f"{prefix}{entry.name}" if prefix else entry.name
            try:
                # `is_symlink()` first, and `follow_symlinks=False` on the
                # directory test, for the reason above: a link is refused,
                # never followed, in either shape.
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    _walk(Path(entry.path), rel + os.sep)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            if _is_in_scope(rel):
                result.add(rel)

    try:
        top = list(os.scandir(root))
    except OSError:
        return set()
    for entry in top:
        if entry.name not in _ALLOWED_TOP_LEVEL_DIRS:
            # Nothing under it can pass `_is_in_scope()`, which tests
            # `parts[0]` — so this is the whole of `.git/`, and every other
            # out-of-scope tree, never opened.
            continue
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                _walk(Path(entry.path), entry.name + os.sep)
                continue
            if not entry.is_file():
                continue
        except OSError:
            continue
        if _is_in_scope(entry.name):
            result.add(entry.name)
    return result


def _relative_files_by_rglob(root: Path) -> set[str]:
    """The previous implementation, kept only as the optimisation's oracle.

    `_relative_files()` replaced this for speed; nothing in the pipeline
    calls this. `RelativeFilesWalkTests` runs both over an adversarial tree
    and asserts they agree, which is what makes "the result is the same set
    by construction" a checked claim rather than a sentence.
    """
    root = Path(root)
    if not root.is_dir():
        return set()
    result: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if _is_in_scope(rel):
            result.add(rel)
    return result


# docs/08 §29 writes down three example names it expects this scan to
# catch: `.env`, `credentials.json`, `token.json`. Only the first was here.
# The other two were measured undetected *inside `daily/`* — which is in
# backup scope (§26), so a file with either name would have been synced,
# committed and pushed to the remote by a gate whose whole job is to stop
# exactly that.
#
# Adding them implements §29 rather than extending it, which is the line
# this list stays on. `secrets.json`, `credentials.yml` and `token.txt` are
# deliberately NOT here: they are names this module's docstring measured as
# undetected, not names the spec asks for, and choosing them would be
# inventing policy. `SecretScanCoverageTests` keeps that gap characterised.
_SECRET_EXACT_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "credentials.json",
        "token.json",
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

    A symlink/junction is *always* flagged as a match here regardless of
    its own filename. This function can only judge a name, never a
    target (no content is read), so a link renamed to something
    innocuous is otherwise invisible to it while `_relative_files()`
    (working_copy.py, same Sprint) showed such a link's target content
    does get copied into the Working Copy. Nothing under a Company
    History master directory is expected to be a link, so treating any
    link as a match — failing the backup loudly — is the same posture
    section 29 already takes for a filename hit, applied to the one shape
    a filename check cannot see through.
    """
    if not root.is_dir():
        return ()
    matches = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_symlink() or (path.is_file() and _looks_like_secret(path.name))
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
