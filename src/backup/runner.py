"""Backup Runner orchestrator (docs/08_BACKUP_SPEC.md section 12 Flow).

Connects the already-built pieces (state, working_copy, git_ops, log) in
the exact order section 12 describes, plus section 43-47's deletion
safety gate. No new git flow, data model, or spec behaviour is added
here — this module only calls the existing functions in order.

Exceptions from check_master_directory() (section 48) and from any
git_ops call (e.g. a rejected push, section 32) are not caught here —
they propagate to the caller unchanged, per this Sprint's rule that
errors must not be hidden. Only the deletion case (section 43-47) is a
normal, non-exceptional termination, because section 12's own Flow
routes it to a BACKUP_FAILED result rather than an error.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .git_ops import GitOperationError, git_add_all, git_commit, git_push, git_status
from .log import BackupLogEntry
from .result import BackupStatus
from .state import DEFAULT_STATE_PATH, load_state, save_state
from .working_copy import check_master_directory, scan_for_secrets, sync_to_working_copy


# docs/08 section 46 uses "Daily History 300개가 갑자기 Modified" as its own
# example of an abnormal count; section 47 defers the exact threshold to
# "실제 운영 필요성이 확인된 후" (once real operational need is confirmed).
# No such confirmation has happened yet, so this constant is a placeholder
# taken directly from section 46's own example, not a newly invented number.
_MASS_MODIFICATION_THRESHOLD = 300


def _commit_message(today: date, *, has_added: bool, has_modified: bool) -> str:
    """docs/08 section 23-24's commit message templates.

    Distinguishes only the two variants decidable from data already in
    run_once()'s scope (sync_result.added vs .modified): a brand-new
    Daily History file uses section 23's normal template; an existing
    file being changed with no new file uses the "late update" template
    (section 24, matching section 65's Late Event Backup case). The
    catch-up variant (section 23/24's third template) is not
    implemented here — deciding a run covers a multi-day gap needs a
    Flow change outside this Sprint's scope.
    """
    if has_modified and not has_added:
        return f"backup: history late update {today.isoformat()}"
    return f"backup: company history through {today.isoformat()}"


def run_once(
    master_dir: Path,
    working_copy_dir: Path,
    *,
    state_path: Path | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> BackupLogEntry:
    now = now or datetime.now().astimezone()
    resolved_state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    resolved_run_id = run_id or now.isoformat(timespec="seconds")
    resolved_source = source or str(master_dir)
    backup_start = now

    # 1. State Load
    state = load_state(resolved_state_path)

    # 2. Master Directory Validation
    check_master_directory(master_dir)

    secret_matches = scan_for_secrets(master_dir)

    # Secret Scan gate (docs/08 section 29, policy confirmed this Sprint):
    # a known secret file blocks the run before Working Copy Sync or any
    # git command — git_status/git_add_all/git_commit/git_push are never
    # reached on this path.
    if secret_matches:
        final_status = BackupStatus.FAILED
        entry = BackupLogEntry(
            run_id=resolved_run_id,
            backup_start=backup_start,
            source=resolved_source,
            changed_files=(),
            deleted_files=(),
            commit_hash=None,
            push_result="secret files detected: " + ", ".join(secret_matches),
            backup_end=datetime.now().astimezone(),
            final_status=final_status,
        )
        state.backup_status = final_status
        save_state(resolved_state_path, state)
        return entry

    # 3. Working Copy Sync
    sync_result = sync_to_working_copy(master_dir, working_copy_dir)

    # 4. 삭제 파일 존재 -> add/commit/push 하지 않고 BACKUP_FAILED로 종료
    if sync_result.deleted:
        final_status = BackupStatus.FAILED
        entry = BackupLogEntry(
            run_id=resolved_run_id,
            backup_start=backup_start,
            source=resolved_source,
            changed_files=tuple(sync_result.added) + tuple(sync_result.modified),
            deleted_files=sync_result.deleted,
            commit_hash=None,
            push_result=None,
            backup_end=datetime.now().astimezone(),
            final_status=final_status,
        )
        state.backup_status = final_status
        save_state(resolved_state_path, state)
        return entry

    # Mass Modification 안전장치 (docs/08 section 46-47): abnormally many
    # modified files also blocks the run before any git command, same as
    # the deletion gate above.
    if len(sync_result.modified) > _MASS_MODIFICATION_THRESHOLD:
        final_status = BackupStatus.FAILED
        entry = BackupLogEntry(
            run_id=resolved_run_id,
            backup_start=backup_start,
            source=resolved_source,
            changed_files=tuple(sync_result.added) + tuple(sync_result.modified),
            deleted_files=(),
            commit_hash=None,
            push_result=(
                f"mass modification detected: {len(sync_result.modified)} files "
                f"modified (threshold {_MASS_MODIFICATION_THRESHOLD})"
            ),
            backup_end=datetime.now().astimezone(),
            final_status=final_status,
        )
        state.backup_status = final_status
        save_state(resolved_state_path, state)
        return entry

    # 5. git status
    status = git_status(working_copy_dir)

    # 6. 변경 없음 -> BACKUP_NOT_REQUIRED
    if not status.has_changes:
        final_status = BackupStatus.NOT_REQUIRED
        entry = BackupLogEntry(
            run_id=resolved_run_id,
            backup_start=backup_start,
            source=resolved_source,
            changed_files=(),
            deleted_files=(),
            commit_hash=None,
            push_result=None,
            backup_end=datetime.now().astimezone(),
            final_status=final_status,
        )
        state.backup_status = final_status
        save_state(resolved_state_path, state)
        return entry

    # 7. 변경 존재 -> add -> commit -> push -> BACKUP_SUCCESS
    try:
        git_add_all(working_copy_dir)
        commit_hash = git_commit(
            working_copy_dir,
            _commit_message(
                now.date(),
                has_added=bool(sync_result.added),
                has_modified=bool(sync_result.modified),
            ),
        )
        git_push(working_copy_dir)
    except GitOperationError:
        # Audit Sprint 1: record the attempt before propagating the same
        # exception, instead of leaving State unchanged. last_successful_backup
        # and last_backup_commit are intentionally left as loaded — only a
        # successful run below updates them.
        state.backup_status = BackupStatus.PENDING
        save_state(resolved_state_path, state)
        raise

    final_status = BackupStatus.SUCCESS
    backup_end = datetime.now().astimezone()
    entry = BackupLogEntry(
        run_id=resolved_run_id,
        backup_start=backup_start,
        source=resolved_source,
        changed_files=status.changed_files,
        deleted_files=status.deleted_files,
        commit_hash=commit_hash,
        push_result="SUCCESS",
        backup_end=backup_end,
        final_status=final_status,
    )

    # 8. State Save
    state.last_successful_backup = backup_end
    state.last_backup_commit = commit_hash
    state.backup_status = final_status
    save_state(resolved_state_path, state)

    return entry
