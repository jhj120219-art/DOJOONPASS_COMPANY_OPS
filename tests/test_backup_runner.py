import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup.git_ops import GitOperationError  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from backup.runner import run_once  # noqa: E402
from backup.state import load_state  # noqa: E402
from backup.working_copy import MasterDirectoryError  # noqa: E402


def _rel(*parts: str) -> str:
    """OS-native relative path string, matching str(Path.relative_to())."""
    return str(Path(*parts))


def _run_git(args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


class BackupRunnerTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.master_dir = self.root / "local_master"
        self.working_copy_dir = self.root / "backup_working_copy"
        self.state_path = self.root / "state" / "backup_state.json"
        (self.master_dir / "daily").mkdir(parents=True)

    def _init_working_copy_with_remote(self):
        self.working_copy_dir.mkdir(parents=True, exist_ok=True)
        bare_remote_dir = self.root / "remote.git"
        _run_git(["init", "--bare", "-b", "main", str(bare_remote_dir)], cwd=self.root)
        _run_git(["init", "-b", "main"], cwd=self.working_copy_dir)
        _run_git(["config", "user.email", "test@example.invalid"], cwd=self.working_copy_dir)
        _run_git(["config", "user.name", "Backup Runner Test"], cwd=self.working_copy_dir)
        _run_git(["remote", "add", "origin", str(bare_remote_dir)], cwd=self.working_copy_dir)
        (self.working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.working_copy_dir)
        _run_git(["commit", "-m", "init"], cwd=self.working_copy_dir)
        _run_git(["push", "-u", "origin", "main"], cwd=self.working_copy_dir)
        return bare_remote_dir


class MasterDirectoryMissingTests(BackupRunnerTestCase):
    def test_missing_master_dir_raises(self):
        missing_master = self.root / "does_not_exist"
        with self.assertRaises(MasterDirectoryError):
            run_once(missing_master, self.working_copy_dir, state_path=self.state_path)


class NoChangesTests(BackupRunnerTestCase):
    def test_empty_master_with_no_prior_working_copy_is_not_required(self):
        self._init_working_copy_with_remote()

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertEqual(entry.final_status, BackupStatus.NOT_REQUIRED)
        state = load_state(self.state_path)
        self.assertEqual(state.backup_status, BackupStatus.NOT_REQUIRED)


class SuccessTests(BackupRunnerTestCase):
    def test_new_daily_file_is_committed_and_pushed(self):
        bare_remote_dir = self._init_working_copy_with_remote()
        (self.master_dir / "daily" / "2026-08-06.md").write_text("day", encoding="utf-8")

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path, run_id="test-run-1")

        self.assertEqual(entry.final_status, BackupStatus.SUCCESS)
        self.assertIsNotNone(entry.commit_hash)
        state = load_state(self.state_path)
        self.assertEqual(state.backup_status, BackupStatus.SUCCESS)
        self.assertEqual(state.last_backup_commit, entry.commit_hash)

        remote_log = _run_git(["log", "-1", "--format=%H"], cwd=bare_remote_dir)
        self.assertEqual(remote_log.strip(), entry.commit_hash)


class DeleteProtectionTests(BackupRunnerTestCase):
    def test_deleted_master_file_blocks_without_committing(self):
        self._init_working_copy_with_remote()
        (self.master_dir / "daily" / "2026-08-06.md").write_text("day", encoding="utf-8")
        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)  # establish prior state

        (self.master_dir / "daily" / "2026-08-06.md").unlink()

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertEqual(entry.final_status, BackupStatus.FAILED)
        self.assertEqual(entry.deleted_files, (_rel("daily", "2026-08-06.md"),))
        state = load_state(self.state_path)
        self.assertEqual(state.backup_status, BackupStatus.FAILED)


class MassModificationTests(BackupRunnerTestCase):
    def test_more_than_threshold_modified_files_blocks_without_committing(self):
        self._init_working_copy_with_remote()
        for i in range(301):
            (self.master_dir / "daily" / f"2026-01-{i:03d}.md").write_text("v1", encoding="utf-8")
        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)  # establish prior state

        for i in range(301):
            (self.master_dir / "daily" / f"2026-01-{i:03d}.md").write_text("v2", encoding="utf-8")

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertEqual(entry.final_status, BackupStatus.FAILED)
        self.assertIn("mass modification", entry.push_result)
        state = load_state(self.state_path)
        self.assertEqual(state.backup_status, BackupStatus.FAILED)


class SecretScanGateTests(BackupRunnerTestCase):
    def test_secret_like_file_in_master_blocks_without_committing(self):
        self._init_working_copy_with_remote()
        (self.master_dir / ".env").write_text("NOTION_API_TOKEN=secret", encoding="utf-8")

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertEqual(entry.final_status, BackupStatus.FAILED)
        self.assertIn("secret files detected", entry.push_result)
        state = load_state(self.state_path)
        self.assertEqual(state.backup_status, BackupStatus.FAILED)


class PushFailureTests(BackupRunnerTestCase):
    def test_push_failure_sets_pending_state_and_raises(self):
        # Working Copy is a real repo but has no remote configured at all,
        # so git_push() fails -> backup/runner.py must record PENDING
        # before letting GitOperationError propagate (its own documented
        # contract: "errors must not be hidden").
        self.working_copy_dir.mkdir(parents=True)
        _run_git(["init", "-b", "main"], cwd=self.working_copy_dir)
        _run_git(["config", "user.email", "test@example.invalid"], cwd=self.working_copy_dir)
        _run_git(["config", "user.name", "Backup Runner Test"], cwd=self.working_copy_dir)
        (self.working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.working_copy_dir)
        _run_git(["commit", "-m", "init"], cwd=self.working_copy_dir)

        (self.master_dir / "daily" / "2026-08-06.md").write_text("day", encoding="utf-8")

        with self.assertRaises(GitOperationError):
            run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(state.backup_status, BackupStatus.PENDING)
        # docs/08 §16: Local Master itself is never touched by a Backup failure.
        self.assertTrue((self.master_dir / "daily" / "2026-08-06.md").exists())


class AuthVsTransientFailureStateTests(BackupRunnerTestCase):
    """docs/08 §19 vs §21/§62 at the Runner level: which BackupStatus gets
    persisted decides whether the next Runner retries or waits for a human."""

    def _prepare_repo_without_remote(self):
        self.working_copy_dir.mkdir(parents=True)
        _run_git(["init", "-b", "main"], cwd=self.working_copy_dir)
        _run_git(["config", "user.email", "test@example.invalid"], cwd=self.working_copy_dir)
        _run_git(["config", "user.name", "Backup Runner Test"], cwd=self.working_copy_dir)
        (self.working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.working_copy_dir)
        _run_git(["commit", "-m", "init"], cwd=self.working_copy_dir)
        (self.master_dir / "daily" / "2026-08-06.md").write_text("day", encoding="utf-8")

    def test_authentication_failure_is_recorded_as_failed_not_pending(self):
        from backup import git_ops

        self._prepare_repo_without_remote()
        original_push = git_ops.git_push

        def fake_push(repo_dir):
            raise git_ops.GitOperationError(
                "git push failed (exit 128): fatal: Authentication failed for "
                "'https://github.com/example/backup.git'"
            )

        import backup.runner as backup_runner

        backup_runner.git_push = fake_push
        try:
            with self.assertRaises(git_ops.GitOperationError):
                run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)
        finally:
            backup_runner.git_push = original_push

        state = load_state(self.state_path)
        # §62: no infinite retry loop — this needs a human to renew the credential.
        self.assertEqual(state.backup_status, BackupStatus.FAILED)

    def test_transient_failure_is_still_recorded_as_pending(self):
        from backup import git_ops

        self._prepare_repo_without_remote()
        original_push = git_ops.git_push

        def fake_push(repo_dir):
            raise git_ops.GitOperationError(
                "git push failed (exit 128): fatal: unable to access "
                "'https://github.com/example/backup.git': Could not resolve host: github.com"
            )

        import backup.runner as backup_runner

        backup_runner.git_push = fake_push
        try:
            with self.assertRaises(git_ops.GitOperationError):
                run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)
        finally:
            backup_runner.git_push = original_push

        state = load_state(self.state_path)
        # §19: transient — the next Runner retries automatically.
        self.assertEqual(state.backup_status, BackupStatus.PENDING)

    def test_local_master_is_untouched_by_either_failure(self):
        self._prepare_repo_without_remote()

        with self.assertRaises(GitOperationError):
            run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertTrue((self.master_dir / "daily" / "2026-08-06.md").exists())


class PendingRetryClassifiesTheSameWayTests(BackupRunnerTestCase):
    """The **second** place docs/08 §19 vs §21/§62 is decided, and the one
    nothing asked about.

    `run_once()` writes that classification twice. The one above
    (`AuthVsTransientFailureStateTests`) is step 7's — a run with changes
    whose add/commit/push failed. The other is step 6's PENDING retry: a
    working copy with NOTHING to commit, whose previous run left the state
    PENDING because only the push failed. That path re-pushes first (BUG-1),
    and when THAT push fails it makes the same auth-vs-transient decision in
    its own `except`.

    A branch-coverage pass (C43) found only one side of it had ever run:
    `test_pending_retry_that_still_fails_stays_pending` covers the transient
    side, and nothing covered the credential side. The cost of the two
    drifting apart is exactly what §62 forbids — a push that can never
    succeed, retried on every scheduled run forever, with the state saying
    "the next run will fix it".

    Both cases here start from a state file that already says PENDING and a
    clean working copy, which is the only way to reach that branch.
    """

    def _pending_state_and_clean_copy(self):
        """A Working Copy with nothing to commit, and a state that says the
        previous run's push failed."""
        from backup.state import BackupState, save_state

        self.working_copy_dir.mkdir(parents=True)
        _run_git(["init", "-b", "main"], cwd=self.working_copy_dir)
        _run_git(["config", "user.email", "test@example.invalid"], cwd=self.working_copy_dir)
        _run_git(["config", "user.name", "Backup Runner Test"], cwd=self.working_copy_dir)
        (self.working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.working_copy_dir)
        _run_git(["commit", "-m", "init"], cwd=self.working_copy_dir)
        save_state(self.state_path, BackupState(backup_status=BackupStatus.PENDING))

    def _run_with_push(self, message):
        from backup import git_ops

        import backup.runner as backup_runner

        original_push = backup_runner.git_push

        def fake_push(repo_dir):
            raise git_ops.GitOperationError(message)

        backup_runner.git_push = fake_push
        try:
            with self.assertRaises(git_ops.GitOperationError):
                run_once(
                    self.master_dir, self.working_copy_dir, state_path=self.state_path
                )
        finally:
            backup_runner.git_push = original_push
        return load_state(self.state_path)

    def test_the_premise_this_branch_needs_is_real(self):
        """Stated first, because the test below is meaningless if the run
        took step 7 instead: nothing to commit, state already PENDING."""
        from backup.git_ops import git_status

        self._pending_state_and_clean_copy()

        self.assertFalse(git_status(self.working_copy_dir).has_changes)
        self.assertIs(load_state(self.state_path).backup_status, BackupStatus.PENDING)

    def test_a_credential_failure_on_the_retry_stops_being_pending(self):
        self._pending_state_and_clean_copy()

        state = self._run_with_push(
            "git push failed (exit 128): fatal: Authentication failed for "
            "'https://github.com/example/backup.git'"
        )

        self.assertIs(state.backup_status, BackupStatus.FAILED)

    def test_a_transient_failure_on_the_retry_stays_pending(self):
        """The side that already had a test, kept here so the pair is
        readable in one place — a classification is only right relative to
        the other answer."""
        self._pending_state_and_clean_copy()

        state = self._run_with_push(
            "git push failed (exit 128): fatal: unable to access "
            "'https://github.com/example/backup.git': Could not resolve host"
        )

        self.assertIs(state.backup_status, BackupStatus.PENDING)

    def test_the_two_places_use_the_same_rule(self):
        """Written twice in one function, so they can drift. Compared from
        the source rather than trusted: `DuplicatedRulesStayInStepTests` makes
        the same argument for the two `safe_event_filename()` copies."""
        import inspect

        import backup.runner as backup_runner

        source = inspect.getsource(backup_runner.run_once)

        self.assertEqual(source.count("is_permanent_failure(exc)"), 2)
        self.assertEqual(source.count("else BackupStatus.PENDING"), 2)
        # The rule these two share must be the shared one, not a local
        # re-spelling of it. Both sites once read
        # `is_authentication_failure(str(exc))`, which was the same rule and
        # an incomplete one: it could only see a permanent failure in the
        # words git printed, so `WorkingCopyNotAGitRepositoryError` -- raised
        # by this module before any git command -- was classified transient
        # and retried forever.
        #
        # Comments stripped first, as `test_schedtask.py::_code()` does: the
        # prose explaining the change names the thing it forbids.
        code = "\n".join(
            line
            for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("is_authentication_failure", code)



class AFailedBackupIsNotQuietlyDowngradedToNotRequiredTests(BackupRunnerTestCase):
    """Step 6 decided "nothing to do" from `backup_state.json` alone, and one
    of the two states that can hold an unpushed commit was missing from it.

    `git status --porcelain` answers "does the working *tree* differ from the
    last commit". After a failed push those two questions come apart: the tree
    is clean and the Backup is still not backed up. Step 6 handled that for
    BACKUP_PENDING (BUG-1) and not for BACKUP_FAILED -- which the other arm of
    the very same classifier writes, for exactly the same shape of event: the
    commit was created, only the push failed.

    Measured before the fix, with real git against a real remote:

        run                            reported              unpushed
        healthy                        BACKUP_SUCCESS               0
        new Daily, remote broken       raises, state=PENDING        1
        nothing new (state PENDING)    raises, state=PENDING        1   correct
        nothing new (state FAILED)     **BACKUP_NOT_REQUIRED**      1   defect

    Two things were wrong with the last row. The run's own verdict was green
    -- that is what the Run Manifest and the exit code carry -- while Company
    History sat only on one disk; and `save_state` then overwrote FAILED with
    NOT_REQUIRED, **erasing the record that a backup had ever failed**. The
    credential failure docs/08 section 62 is written about arrives by this
    exact route.

    One signal did survive, and saying so is the difference between this
    class and an overstatement. `ops_status._history_newer_than_the_last_backup()`
    still fires, because it reads `last_successful_backup`, which neither the
    old behaviour nor the fix touches (measured: it names the unbacked
    `2026-08-20.md` exactly). So the defect is narrower than "nobody was
    told": what was missing is the Backup's own verdict. The surviving net is
    an indirect mtime-versus-pointer comparison that `ops_status.py`'s own
    comment records can be blinded permanently by a single future-dated
    `last_successful_backup` (measured there: 1 alert -> 0), and that neither
    the Manifest nor the exit code consults at all.

    The fix does not push again -- section 62 forbids retrying that, and
    section 21 says a person must act. It removes the false statement, not the
    policy: the failure stands and the state is left alone.

    The question is put to git (`count_unpushed_commits`) rather than to the
    state file, so an operator who pushed by hand gets the truthful
    NOT_REQUIRED back. `test_a_hand_pushed_remote_is_allowed_to_be_current`
    is that half; without it this class would be satisfied by a fix that
    simply never says NOT_REQUIRED again.
    """

    def _failed_backup_with_an_unpushed_commit(self):
        """The state a failed push leaves: a commit the remote does not have,
        a clean tree, and a state file that says FAILED.

        Built by actually failing a push rather than by writing the state
        file, so the premise cannot drift away from what the code produces.
        """
        remote = self._init_working_copy_with_remote()
        (self.master_dir / "daily" / "2026-08-19.md").write_text("# 19\n", encoding="utf-8")
        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        # The remote stops being a repository. A real misconfiguration; no
        # patching of this project's own code.
        broken = self.root / "not_a_repository"
        broken.mkdir()
        _run_git(["remote", "set-url", "origin", str(broken)], cwd=self.working_copy_dir)

        (self.master_dir / "daily" / "2026-08-20.md").write_text("# 20\n", encoding="utf-8")
        with self.assertRaises(GitOperationError):
            run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        # That push failure classifies as PENDING today (BUG-52 -- a
        # misconfiguration is not a credential problem, and the classifier has
        # no third answer). This class is about what step 6 does with FAILED,
        # so the state is put into the shape the *credential* arm writes.
        from backup.state import BackupState, save_state

        loaded = load_state(self.state_path)
        save_state(
            self.state_path,
            BackupState(
                last_successful_backup=loaded.last_successful_backup,
                last_backup_commit=loaded.last_backup_commit,
                backup_status=BackupStatus.FAILED,
            ),
        )
        return remote

    def _unpushed(self):
        out = _run_git(["log", "--oneline", "origin/main..HEAD"], cwd=self.working_copy_dir)
        return [line for line in out.strip().splitlines() if line]

    def test_the_premise_is_real_a_commit_exists_that_the_remote_does_not_have(self):
        """Stated first: everything below is vacuous if the push actually
        succeeded or no commit was ever made."""
        self._failed_backup_with_an_unpushed_commit()

        from backup.git_ops import git_status

        self.assertFalse(git_status(self.working_copy_dir).has_changes)
        self.assertEqual(len(self._unpushed()), 1)
        self.assertIs(load_state(self.state_path).backup_status, BackupStatus.FAILED)

    def test_a_run_with_nothing_new_still_reports_the_failure(self):
        self._failed_backup_with_an_unpushed_commit()

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(entry.final_status, BackupStatus.FAILED)

    def test_the_failure_is_not_erased_from_the_state(self):
        """The half that made the defect silent rather than merely wrong."""
        self._failed_backup_with_an_unpushed_commit()

        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(load_state(self.state_path).backup_status, BackupStatus.FAILED)

    def test_the_entry_says_what_is_still_unpushed(self):
        """`push_result` is what `app/runner.py` copies into the Run
        Manifest's `reason`, and an empty one is how BUG-39 hid a deletion."""
        self._failed_backup_with_an_unpushed_commit()

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIsNotNone(entry.push_result)
        self.assertIn("1", entry.push_result)
        self.assertIn("21", entry.push_result)
        self.assertIsNotNone(entry.commit_hash)

    def test_it_does_not_push_again(self):
        """docs/08 section 62. The fix is to stop lying, not to start
        retrying -- a credential failure retried on every scheduled run is the
        loop section 62 names, and this branch is reached by that failure.

        Asserted by the outcome a retry would have produced: the remote is
        still broken, so an attempted push would raise instead of returning,
        and the remote would still be missing the commit either way.
        """
        self._failed_backup_with_an_unpushed_commit()

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(entry.final_status, BackupStatus.FAILED)
        self.assertEqual(len(self._unpushed()), 1)

    def test_a_hand_pushed_remote_is_allowed_to_be_current(self):
        """The other direction, and the reason this asks git rather than the
        state file. An operator who fixed the remote and pushed by hand has a
        Backup that IS current; reporting FAILED forever would be the same
        kind of false statement pointing the other way."""
        remote = self._failed_backup_with_an_unpushed_commit()
        _run_git(["remote", "set-url", "origin", str(remote)], cwd=self.working_copy_dir)
        _run_git(["push", "origin", "main"], cwd=self.working_copy_dir)
        self.assertEqual(self._unpushed(), [])

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(entry.final_status, BackupStatus.NOT_REQUIRED)
        self.assertIs(load_state(self.state_path).backup_status, BackupStatus.NOT_REQUIRED)

    def test_a_failed_backup_with_no_commit_behind_it_is_allowed_to_clear(self):
        """FAILED is also written by the gates that stop before any git
        command -- secret scan (section 29), deletion (section 31), mass
        modification (section 46). Those leave NO commit, so once the
        condition is gone there is genuinely nothing to push, and holding
        FAILED would be a permanent false alarm of the kind C26 warns trains
        people to skim ATTENTION."""
        from backup.state import BackupState, save_state

        self._init_working_copy_with_remote()
        (self.master_dir / "daily" / "2026-08-19.md").write_text("# 19\n", encoding="utf-8")
        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)
        self.assertEqual(self._unpushed(), [])

        save_state(self.state_path, BackupState(backup_status=BackupStatus.FAILED))

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(entry.final_status, BackupStatus.NOT_REQUIRED)


class CountUnpushedCommitsTests(BackupRunnerTestCase):
    """The new question, asked of real repositories in every shape step 6 can
    meet it in. `None` is not a number and must never be read as zero -- that
    reading is exactly the false green this was written to remove."""

    def test_a_pushed_branch_counts_zero(self):
        from backup.git_ops import count_unpushed_commits

        self._init_working_copy_with_remote()

        self.assertEqual(count_unpushed_commits(self.working_copy_dir), 0)

    def test_a_local_commit_that_never_reached_the_remote_counts_one(self):
        from backup.git_ops import count_unpushed_commits

        self._init_working_copy_with_remote()
        (self.working_copy_dir / "a.md").write_text("a", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.working_copy_dir)
        _run_git(["commit", "-m", "a"], cwd=self.working_copy_dir)

        self.assertEqual(count_unpushed_commits(self.working_copy_dir), 1)

    def test_no_upstream_cannot_be_answered(self):
        """`git init` + `git remote add`, never pushed -- the first-run shape
        docs/11 section 26 is about. git has nothing to compare against, and
        answering 0 here would report a Backup as current that has never
        reached a remote at all."""
        from backup.git_ops import count_unpushed_commits

        self.working_copy_dir.mkdir(parents=True)
        _run_git(["init", "-b", "main"], cwd=self.working_copy_dir)
        _run_git(["config", "user.email", "test@example.invalid"], cwd=self.working_copy_dir)
        _run_git(["config", "user.name", "Backup Runner Test"], cwd=self.working_copy_dir)
        (self.working_copy_dir / "a.md").write_text("a", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.working_copy_dir)
        _run_git(["commit", "-m", "a"], cwd=self.working_copy_dir)

        self.assertIsNone(count_unpushed_commits(self.working_copy_dir))

    def test_it_does_not_raise_on_a_directory_that_is_not_a_repository(self):
        """Called on the failure path, where a second exception would replace
        a reportable state with an unreportable one."""
        from backup.git_ops import count_unpushed_commits

        plain = self.root / "plain"
        plain.mkdir()

        self.assertIsNone(count_unpushed_commits(plain))


class AnUnpushedCommitIsReportedWhateverTheStateFileSaysTests(BackupRunnerTestCase):
    """The same lie, one state over (C146).

    `AFailedBackupIsNotQuietlyDowngradedToNotRequiredTests` closed this for
    `BACKUP_FAILED` and wrote the reason down in one sentence: *"the question
    is put to git rather than to the state file."* The code then asked the
    state file whether to ask git — `if state.backup_status is FAILED` — so
    every other way of holding an unpushed commit was untouched.

    The one that matters is a state file with **no** `backup_status` at all.
    `load_state()` returns `None` for that field, and three ordinary things
    produce it:

        the state file is gone          `runtime/` is .gitignore'd, so a
                                        restore that brings the Working Copy
                                        back need not bring the state
        a person repaired it            docs/10 §46 expects hand-edited
                                        state, and `controltower/attention`'s
                                        own remedy for a corrupt
                                        `backup_state.json` tells them to
        a partial restore               the same file, half of it

    Measured with real git and a real (local, bare) remote, before the fix:

        run                                   reported              unpushed
        healthy                               BACKUP_SUCCESS               0
        new Daily, remote broken              raises, PENDING              1
        nothing new, no `backup_status`       **BACKUP_NOT_REQUIRED**      1

    The third row is the defect, and it is worse than the FAILED one it
    mirrors: there is no prior FAILED in the file for `ops_status.py` to
    notice, so the green verdict is the only verdict anywhere. `save_state`
    then writes NOT_REQUIRED and the tree settles into "backed up".

    What the fix does **not** do is invent an alarm out of "unknown".
    `count_unpushed_commits()` answers `None` for a repository with no
    upstream, and a Working Copy in that shape has never pushed anything —
    a brand-new one included. Reporting FAILED there on every quiet run is
    the standing alert C26 warns trains people to skim the section, so
    `None` stays confined to the FAILED arm, where a real failure is already
    on record. A count greater than zero is unambiguous in every state, and
    widening it to every state is the whole of the change.
    """

    def _clean_tree_holding_an_unpushed_commit(self):
        """A commit the remote does not have, a clean tree — built by
        actually failing a push, so the premise cannot drift from what the
        code produces."""
        remote = self._init_working_copy_with_remote()
        (self.master_dir / "daily" / "2026-08-19.md").write_text("# 19\n", encoding="utf-8")
        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        broken = self.root / "not_a_repository_either"
        broken.mkdir()
        _run_git(["remote", "set-url", "origin", str(broken)], cwd=self.working_copy_dir)

        (self.master_dir / "daily" / "2026-08-20.md").write_text("# 20\n", encoding="utf-8")
        with self.assertRaises(GitOperationError):
            run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)
        return remote

    def _drop_backup_status(self):
        """What a lost, hand-repaired or partially restored state file looks
        like. Written as JSON rather than through `save_state()` because
        `save_state()` cannot produce it — which is the point: this shape
        comes from outside the program."""
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        raw.pop("backup_status", None)
        self.state_path.write_text(json.dumps(raw), encoding="utf-8")

    def _unpushed(self):
        out = _run_git(
            ["log", "--oneline", "origin/main..HEAD"], cwd=self.working_copy_dir
        )
        return [line for line in out.strip().splitlines() if line]

    def test_the_premise_is_real(self):
        """Vacuous otherwise: no commit, or a state that still says FAILED,
        and every assertion below would be about the arm already fixed."""
        self._clean_tree_holding_an_unpushed_commit()
        self._drop_backup_status()

        from backup.git_ops import git_status

        self.assertFalse(git_status(self.working_copy_dir).has_changes)
        self.assertEqual(len(self._unpushed()), 1)
        self.assertIsNone(load_state(self.state_path).backup_status)

    def test_a_run_with_nothing_new_reports_the_unpushed_commit(self):
        self._clean_tree_holding_an_unpushed_commit()
        self._drop_backup_status()

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(entry.final_status, BackupStatus.FAILED)
        self.assertIsNotNone(entry.push_result)
        self.assertIn("1", entry.push_result)
        self.assertIn("21", entry.push_result)
        self.assertIsNotNone(entry.commit_hash)

    def test_the_state_is_not_overwritten_with_green(self):
        """The half that made it silent. `save_state` writing NOT_REQUIRED
        is what left no trace anywhere that a backup had not reached the
        remote."""
        self._clean_tree_holding_an_unpushed_commit()
        self._drop_backup_status()

        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIsNot(
            load_state(self.state_path).backup_status, BackupStatus.NOT_REQUIRED
        )

    def test_it_does_not_push_again(self):
        """docs/08 §62, same as the FAILED arm: the fix is to stop lying,
        not to start retrying."""
        self._clean_tree_holding_an_unpushed_commit()
        self._drop_backup_status()

        run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertEqual(len(self._unpushed()), 1)

    def test_a_hand_pushed_remote_is_still_allowed_to_be_current(self):
        """The other direction, and the reason this asks git. Without it the
        fix would be satisfied by never saying NOT_REQUIRED again."""
        remote = self._clean_tree_holding_an_unpushed_commit()
        self._drop_backup_status()
        _run_git(["remote", "set-url", "origin", str(remote)], cwd=self.working_copy_dir)
        _run_git(["push", "origin", "main"], cwd=self.working_copy_dir)
        self.assertEqual(self._unpushed(), [])

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(entry.final_status, BackupStatus.NOT_REQUIRED)

    def test_a_working_copy_with_no_upstream_is_not_turned_into_an_alarm(self):
        """The boundary the fix deliberately does not cross. `None` means
        "git cannot tell me", and a Working Copy that has never pushed —
        including a brand-new one — answers `None` forever. Raising FAILED
        on every quiet run for that is a standing alert, not a finding, so
        `None` stays confined to the arm where a failure is already on
        record."""
        from backup.git_ops import count_unpushed_commits

        self.working_copy_dir.mkdir(parents=True, exist_ok=True)
        _run_git(["init", "-b", "main"], cwd=self.working_copy_dir)
        _run_git(["config", "user.email", "test@example.invalid"], cwd=self.working_copy_dir)
        _run_git(["config", "user.name", "Backup Runner Test"], cwd=self.working_copy_dir)
        (self.working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.working_copy_dir)
        _run_git(["commit", "-m", "init"], cwd=self.working_copy_dir)

        self.assertIsNone(count_unpushed_commits(self.working_copy_dir))

        entry = run_once(self.master_dir, self.working_copy_dir, state_path=self.state_path)

        self.assertIs(entry.final_status, BackupStatus.NOT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
