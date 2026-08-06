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


if __name__ == "__main__":
    unittest.main()
