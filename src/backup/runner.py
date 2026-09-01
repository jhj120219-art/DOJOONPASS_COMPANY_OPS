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

import businessdate
from businessdate import clock_date
from .git_ops import (
    GitOperationError,
    check_working_copy_is_a_git_repository,
    count_unpushed_commits,
    git_add_all,
    git_commit,
    git_head_commit,
    git_push,
    git_status,
    is_permanent_failure,
)
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
    now = now or businessdate.now()
    resolved_state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    resolved_run_id = run_id or now.isoformat(timespec="seconds")
    resolved_source = source or str(master_dir)
    backup_start = now

    # 1. State Load
    state = load_state(resolved_state_path)

    # 2. Master Directory Validation
    check_master_directory(master_dir)

    # 2b. Working Copy Validation (Audit finding, this Sprint): every git
    # command below runs with cwd=working_copy_dir. If that directory has no
    # `.git` of its own, git silently walks up to the nearest ancestor
    # repository instead — for a Working Copy nested inside this project's
    # own checkout, that is this project's own repository. Failing fast here
    # means a missing `git init` in the Working Copy surfaces as a named,
    # caught GitOperationError before any git command runs, never as a
    # commit/push landing somewhere unintended.
    check_working_copy_is_a_git_repository(working_copy_dir)

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
            backup_end=businessdate.now(),
            final_status=final_status,
        )
        state.backup_status = final_status
        save_state(resolved_state_path, state)
        return entry

    # 3. Working Copy Sync
    sync_result = sync_to_working_copy(master_dir, working_copy_dir)

    # `changed_files` on the two blocked paths below is the SYNC's per-file
    # list, not git's — and that is deliberate, because on these paths there
    # is no commit for git's list to describe. Neither `git add -A` nor
    # `git status` runs after this point, so "what the commit carries" has no
    # answer; "what the sync had prepared when it was stopped" does, and it
    # is what an operator needs in order to know what did not get backed up.
    #
    # Step 7's SUCCESS entry reads git instead, for the opposite reason: there
    # the commit exists and is the only thing that cannot understate what was
    # pushed (it includes files that reached the Working Copy by routes the
    # sync never saw — BACKLOG E-21). So one field name carries two meanings,
    # keyed by `final_status`, and both are the right meaning for their own
    # path. `ChangedFilesMeansTheSameThingAsTheStatusTests` pins the pair.
    #
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
            backup_end=businessdate.now(),
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
            backup_end=businessdate.now(),
            final_status=final_status,
        )
        state.backup_status = final_status
        save_state(resolved_state_path, state)
        return entry

    # 5. git status
    status = git_status(working_copy_dir)

    # 6. 변경 없음 -> BACKUP_NOT_REQUIRED
    #
    #    예외: 직전 실행이 BACKUP_PENDING으로 끝났다면 commit은 이미 만들어졌고
    #    push만 실패한 상태다. 그 경우 Working Copy는 깨끗하지만 Remote는 뒤처져
    #    있으므로, `git status`만 보고 NOT_REQUIRED로 끝내면 docs/08 §19가 약속한
    #    "다음 Runner에서 재시도"가 영원히 일어나지 않는다(Audit BUG-1). 게다가
    #    backup_status가 PENDING -> NOT_REQUIRED로 덮어써져 미완료 신호까지
    #    사라진다. CEO 승인 A안: State가 PENDING이면 push를 먼저 재시도한다.
    if not status.has_changes:
        if state.backup_status is BackupStatus.PENDING:
            try:
                git_push(working_copy_dir)
            except GitOperationError as exc:
                # 7번 단계와 동일한 분류 규칙(docs/08 §19 vs §21/§62).
                state.backup_status = (
                    BackupStatus.FAILED
                    if is_permanent_failure(exc)
                    else BackupStatus.PENDING
                )
                save_state(resolved_state_path, state)
                raise

            final_status = BackupStatus.SUCCESS
            backup_end = businessdate.now()
            entry = BackupLogEntry(
                run_id=resolved_run_id,
                backup_start=backup_start,
                source=resolved_source,
                changed_files=(),
                deleted_files=(),
                commit_hash=git_head_commit(working_copy_dir),
                push_result="SUCCESS (pending backup retried)",
                backup_end=backup_end,
                final_status=final_status,
            )
            state.last_successful_backup = backup_end
            state.last_backup_commit = entry.commit_hash
            state.backup_status = final_status
            save_state(resolved_state_path, state)
            return entry

        # BUG-1 반쪽만 닫혀 있었다. 위 분기는 미push commit을 들고 있을 수 있는
        # **두** 상태 중 하나(PENDING)만 본다. 같은 분류기의 반대편 팔이 쓰는
        # BACKUP_FAILED도 정확히 같은 모양을 남긴다 — commit은 만들어졌고 push만
        # 실패했으며, 그래서 Working Copy는 깨끗하다.
        #
        # 실측(실제 git, 실제 remote):
        #
        #   1) 정상                      BACKUP_SUCCESS
        #   2) remote 파손 + 새 Daily    push 실패, state=BACKUP_PENDING,
        #                                미push commit 1건
        #   3) 변경 없음                 재시도 -> 또 실패, PENDING 유지  (옳다)
        #   4) 변경 없음, state=FAILED   **BACKUP_NOT_REQUIRED**, 미push commit 1건
        #
        # 4번이 결함이다. Company History가 remote에 없는데 이 실행은 초록을
        # 보고하고, 게다가 `save_state`가 FAILED를 NOT_REQUIRED로 **덮어써서**
        # 그 실패가 있었다는 사실 자체를 지운다. `ops_status.py`의 ATTENTION은
        # 그 다음부터 아무 말도 하지 않는다. README RULE 7이 금지하는 조용한
        # 손실이고, docs/08 §62가 겨냥한 자격증명 실패가 바로 이 경로로 들어온다.
        #
        # push를 다시 시도하지는 **않는다.** §62가 금지하는 것이 그것이고,
        # §21은 "사람이 개입해야 한다"고 못박는다. 고치는 것은 재시도가 아니라
        # **거짓말**이다: 실패는 서 있는 채로 보고되고 상태는 그대로 남는다.
        #
        # `count_unpushed_commits()`에게 묻는다 — state 파일이 아니라 저장소에.
        # 사람이 손으로 push해 놓았다면 remote는 최신이고 NOT_REQUIRED가 참이다.
        # None(upstream 없음 등)은 0으로 읽지 않는다: "알 수 없다"는 "도달했다"가
        # 아니고, 그 경우 초록을 보고하는 것이 정확히 이 결함이다.
        if state.backup_status is BackupStatus.FAILED:
            unpushed = count_unpushed_commits(working_copy_dir)
            if unpushed != 0:
                entry = BackupLogEntry(
                    run_id=resolved_run_id,
                    backup_start=backup_start,
                    source=resolved_source,
                    changed_files=(),
                    deleted_files=(),
                    commit_hash=git_head_commit(working_copy_dir),
                    push_result=(
                        "이전 실행의 BACKUP_FAILED가 아직 해소되지 않았다: "
                        + (
                            f"remote에 도달하지 않은 commit {unpushed}건"
                            if unpushed is not None
                            else "remote와 비교할 수 없다 (upstream 미설정)"
                        )
                        + ". docs/08 §21 — 사람이 개입해야 한다"
                    ),
                    backup_end=businessdate.now(),
                    final_status=BackupStatus.FAILED,
                )
                # 상태는 건드리지 않는다. 이 분기의 요점이 그것이다.
                return entry

        final_status = BackupStatus.NOT_REQUIRED
        entry = BackupLogEntry(
            run_id=resolved_run_id,
            backup_start=backup_start,
            source=resolved_source,
            changed_files=(),
            deleted_files=(),
            commit_hash=None,
            push_result=None,
            backup_end=businessdate.now(),
            final_status=final_status,
        )
        state.backup_status = final_status
        save_state(resolved_state_path, state)
        return entry

    # 7. 변경 존재 -> add -> commit -> push -> BACKUP_SUCCESS
    try:
        git_add_all(working_copy_dir)
        # What actually went into the commit, per file.
        #
        # The status at step 5 cannot answer that. `git status --porcelain`
        # collapses an untracked DIRECTORY into one entry — measured, three
        # brand-new Daily files in a working copy that has never had a
        # `daily/` committed:
        #
        #     before add   ?? daily/                       1 entry
        #     after add    A  daily/2026-08-01.md          3 entries
        #                  A  daily/2026-08-02.md
        #                  A  daily/2026-08-03.md
        #
        # and it was the first list that reached `BackupLogEntry`, the Run
        # Manifest metric `changed_files`, and OPS_BACKUP's `Changed Files`.
        # So a backup carrying a whole restored Company History reported
        # `changed_files=1` and named `daily/` rather than one day of it —
        # BUG-39 added that metric precisely to answer "what did this backup
        # carry", and on the runs where the answer matters most it did not.
        #
        # Not a rare shape. It is every FIRST backup of a working copy the
        # operator has just `git init`-ed (docs/08 §30), every disaster
        # restore that re-inits one (docs/10 §45), and the run that first
        # writes `monthly/` — which happens once in ordinary operation. Once
        # a directory holds one tracked file, git lists new files inside it
        # individually and the count was right all along, which is why this
        # only ever showed up on the runs nobody had a baseline for.
        #
        # Taken from git rather than from `sync_result` on purpose, even
        # though that object has per-file `added`/`modified` lists and the
        # two FAILED branches above already use them. `git add -A` stages the
        # WHOLE working copy, so anything that arrived there by a route other
        # than the sync — an operator's own file, an editor swap file, the
        # stray `.env` of BACKLOG E-21 — is in the commit and is in this
        # list, and is in no sync result. Reporting the commit's own contents
        # is the only version of this number that cannot understate what was
        # pushed.
        #
        # No new git command: `status --porcelain` is already in the approved
        # set (`test_spec_conformance.py::
        # test_git_ops_runs_only_the_approved_command_set`), and this is a
        # second call to it. Measured on this machine against a Backup step
        # that already spawns four git processes and a push:
        #
        #     empty working copy          15.2 / 15.5 / 18.5 ms
        #     2,688 files (7.4 years of
        #     Daily History), first ever  15.7 ms before add, 30.7 ms after
        #
        # The second row is also this fix at its largest: that run used to
        # report `changed_files=1` and now reports 2,688.
        staged = git_status(working_copy_dir)
        commit_hash = git_commit(
            working_copy_dir,
            _commit_message(
                clock_date(now),
                has_added=bool(sync_result.added),
                has_modified=bool(sync_result.modified),
            ),
        )
        git_push(working_copy_dir)
    except GitOperationError as exc:
        # Audit Sprint 1: record the attempt before propagating the same
        # exception, instead of leaving State unchanged. last_successful_backup
        # and last_backup_commit are intentionally left as loaded — only a
        # successful run below updates them.
        #
        # docs/08 §19 vs §21/§62: a transient failure (Internet OFF, GitHub
        # outage, rejected push) is BACKUP_PENDING and retried by the next
        # Runner; an authentication/permission failure is BACKUP_FAILED,
        # because retrying it on a schedule cannot fix it and would build
        # exactly the infinite retry loop §62 forbids. The operator has to
        # renew the credential (docs/12 §12: "사람이 Token/인증 갱신 후 수동
        # 재개").
        state.backup_status = (
            BackupStatus.FAILED
            if is_permanent_failure(exc)
            else BackupStatus.PENDING
        )
        save_state(resolved_state_path, state)
        raise

    final_status = BackupStatus.SUCCESS
    backup_end = businessdate.now()
    entry = BackupLogEntry(
        run_id=resolved_run_id,
        backup_start=backup_start,
        source=resolved_source,
        changed_files=staged.changed_files,
        deleted_files=staged.deleted_files,
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
