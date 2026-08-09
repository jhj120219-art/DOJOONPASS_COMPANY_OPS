import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup.git_ops import (  # noqa: E402
    GitOperationError,
    WorkingCopyNotAGitRepositoryError,
    check_working_copy_is_a_git_repository,
    git_add_all,
    git_commit,
    git_push,
    git_status,
    is_authentication_failure,
)


def _run_git(args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


class GitOpsTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo_dir = Path(tmp.name) / "repo"
        self.repo_dir.mkdir()
        _run_git(["init", "-b", "main"], cwd=self.repo_dir)
        _run_git(["config", "user.email", "test@example.invalid"], cwd=self.repo_dir)
        _run_git(["config", "user.name", "Git Ops Test"], cwd=self.repo_dir)


class GitStatusTests(GitOpsTestCase):
    def test_clean_repo_has_no_changes(self):
        (self.repo_dir / "a.txt").write_text("a", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.repo_dir)
        _run_git(["commit", "-m", "init"], cwd=self.repo_dir)

        status = git_status(self.repo_dir)

        self.assertFalse(status.has_changes)
        self.assertEqual(status.changed_files, ())
        self.assertEqual(status.deleted_files, ())

    def test_new_untracked_file_is_reported_as_changed(self):
        (self.repo_dir / "new.txt").write_text("new", encoding="utf-8")

        status = git_status(self.repo_dir)

        self.assertTrue(status.has_changes)
        self.assertIn("new.txt", status.changed_files)
        self.assertEqual(status.deleted_files, ())

    def test_deleted_tracked_file_is_reported_as_deleted(self):
        (self.repo_dir / "a.txt").write_text("a", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.repo_dir)
        _run_git(["commit", "-m", "init"], cwd=self.repo_dir)
        (self.repo_dir / "a.txt").unlink()

        status = git_status(self.repo_dir)

        self.assertTrue(status.has_changes)
        self.assertIn("a.txt", status.deleted_files)
        self.assertEqual(status.changed_files, ())


class GitCommitTests(GitOpsTestCase):
    def test_commit_with_no_staged_changes_returns_empty_string(self):
        # docs/08_BACKUP_SPEC.md §25: no changes -> no commit is created.
        commit_hash = git_commit(self.repo_dir, "should not create a commit")
        self.assertEqual(commit_hash, "")

    def test_commit_with_staged_changes_returns_a_commit_hash(self):
        (self.repo_dir / "a.txt").write_text("a", encoding="utf-8")
        git_add_all(self.repo_dir)

        commit_hash = git_commit(self.repo_dir, "add a.txt")

        self.assertEqual(len(commit_hash), 40)
        log_output = _run_git(["log", "-1", "--format=%H"], cwd=self.repo_dir)
        self.assertEqual(commit_hash, log_output.strip())


class GitPushFailureTests(GitOpsTestCase):
    def test_push_without_a_remote_raises_git_operation_error(self):
        # No `origin` configured -> git push fails with a non-zero exit.
        # Section 32's "rejected push" failure mode surfaces the same way:
        # a plain GitOperationError, never retried/merged/force-pushed here.
        (self.repo_dir / "a.txt").write_text("a", encoding="utf-8")
        git_add_all(self.repo_dir)
        git_commit(self.repo_dir, "add a.txt")

        with self.assertRaises(GitOperationError) as ctx:
            git_push(self.repo_dir)
        self.assertIn("push", str(ctx.exception))

    def test_push_rejected_by_remote_raises_git_operation_error(self):
        # A genuine rejected push: the bare remote has a commit the local
        # branch does not, so a plain (non-force) push is rejected.
        bare_remote_dir = self.repo_dir.parent / "remote.git"
        _run_git(["init", "--bare", "-b", "main", str(bare_remote_dir)], cwd=self.repo_dir.parent)

        other_clone_dir = self.repo_dir.parent / "other_clone"
        _run_git(["clone", str(bare_remote_dir), str(other_clone_dir)], cwd=self.repo_dir.parent)
        _run_git(["config", "user.email", "other@example.invalid"], cwd=other_clone_dir)
        _run_git(["config", "user.name", "Other Clone"], cwd=other_clone_dir)
        (other_clone_dir / "remote-only.txt").write_text("x", encoding="utf-8")
        _run_git(["add", "-A"], cwd=other_clone_dir)
        _run_git(["commit", "-m", "remote-only commit"], cwd=other_clone_dir)
        _run_git(["push", "origin", "main"], cwd=other_clone_dir)

        _run_git(["remote", "add", "origin", str(bare_remote_dir)], cwd=self.repo_dir)
        (self.repo_dir / "local-only.txt").write_text("y", encoding="utf-8")
        git_add_all(self.repo_dir)
        git_commit(self.repo_dir, "local-only commit")

        with self.assertRaises(GitOperationError):
            git_push(self.repo_dir)

        # git_ops never pulls/merges/force-pushes to resolve this itself.
        log_output = _run_git(["log", "--oneline"], cwd=self.repo_dir)
        self.assertNotIn("remote-only commit", log_output)


class AuthenticationFailureClassificationTests(unittest.TestCase):
    """docs/08 §19 vs §21/§62: credential problems are BACKUP_FAILED (no
    infinite retry); transient problems are BACKUP_PENDING (retry later)."""

    def test_credential_failures_are_recognized(self):
        for message in (
            "git push failed (exit 128): fatal: Authentication failed for 'https://github.com/x'",
            "git push failed (exit 128): could not read Username for 'https://github.com'",
            "git push failed (exit 128): Permission denied (publickey).",
            "git push failed (exit 128): remote: Support for password authentication was removed",
            "git push failed (exit 128): remote: Permission to x.git denied, 403 Forbidden",
        ):
            with self.subTest(message=message):
                self.assertTrue(is_authentication_failure(message))

    def test_transient_failures_are_not_treated_as_auth_failures(self):
        for message in (
            "git push failed (exit 128): fatal: unable to access: Could not resolve host: github.com",
            "git push failed (exit 1): ! [rejected] main -> main (fetch first)",
            "git push failed (exit 128): fatal: the remote end hung up unexpectedly",
            "git push failed (exit 128): Connection timed out",
            "git push failed (exit 1): error: failed to push some refs",
        ):
            with self.subTest(message=message):
                self.assertFalse(is_authentication_failure(message))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(is_authentication_failure("FATAL: AUTHENTICATION FAILED"))


class GitPushSuccessTests(GitOpsTestCase):
    def test_push_to_a_reachable_remote_succeeds(self):
        bare_remote_dir = self.repo_dir.parent / "remote.git"
        _run_git(["init", "--bare", "-b", "main", str(bare_remote_dir)], cwd=self.repo_dir.parent)
        _run_git(["remote", "add", "origin", str(bare_remote_dir)], cwd=self.repo_dir)

        (self.repo_dir / "a.txt").write_text("a", encoding="utf-8")
        git_add_all(self.repo_dir)
        git_commit(self.repo_dir, "add a.txt")

        _run_git(["push", "-u", "origin", "main"], cwd=self.repo_dir)  # establish tracking once
        (self.repo_dir / "b.txt").write_text("b", encoding="utf-8")
        git_add_all(self.repo_dir)
        git_commit(self.repo_dir, "add b.txt")

        git_push(self.repo_dir)  # must not raise

        remote_log = _run_git(["log", "-1", "--format=%s"], cwd=bare_remote_dir)
        self.assertEqual(remote_log.strip(), "add b.txt")


class WorkingCopyGitRepositoryGuardTests(unittest.TestCase):
    """Incident finding (this Sprint): a Working Copy with no `.git` of its
    own lets every git command below silently walk up to whatever ancestor
    repository git finds instead (verified: `git add -A` / `git commit` /
    `git push` all landed in the *caller's* real, unrelated repository and
    pushed to its real `origin`). This guard makes that precondition
    checked and named instead of silently assumed."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_directory_with_no_git_of_its_own_is_rejected(self):
        working_copy = self.root / "backup_working_copy"
        working_copy.mkdir()

        with self.assertRaises(WorkingCopyNotAGitRepositoryError):
            check_working_copy_is_a_git_repository(working_copy)

    def test_a_directory_that_does_not_exist_yet_is_rejected_cleanly(self):
        """First-ever setup, before anything has run `mkdir` on the Working
        Copy path at all: must raise the same named error, not something
        unexpected like FileNotFoundError."""
        never_created = self.root / "backup_working_copy"
        self.assertFalse(never_created.exists())

        with self.assertRaises(WorkingCopyNotAGitRepositoryError):
            check_working_copy_is_a_git_repository(never_created)

    def test_a_properly_initialised_working_copy_passes(self):
        working_copy = self.root / "backup_working_copy"
        working_copy.mkdir()
        _run_git(["init", "-b", "main"], cwd=working_copy)

        check_working_copy_is_a_git_repository(working_copy)  # must not raise

    def test_the_error_names_the_exact_directory_and_never_touches_an_ancestor_repo(self):
        """The incident, reproduced end to end and proven contained: an
        ancestor repository (simulating the caller's own real checkout)
        with a working_copy_dir nested inside it that was never git-init'd.
        Before this guard, `backup.runner.run_once()` would reach `git
        status`/`git add -A`/`git commit` with cwd=working_copy_dir and git
        would silently operate on the ancestor repository instead."""
        _run_git(["init", "-b", "main"], cwd=self.root)
        _run_git(["config", "user.email", "t@example.invalid"], cwd=self.root)
        _run_git(["config", "user.name", "guard test"], cwd=self.root)
        (self.root / "pre-existing.txt").write_text("ancestor repo content", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.root)
        _run_git(["commit", "-m", "ancestor repo initial commit"], cwd=self.root)
        ancestor_head_before = _run_git(["rev-parse", "HEAD"], cwd=self.root).strip()

        master_dir = self.root / "local_master"
        working_copy_dir = self.root / "backup_working_copy"  # deliberately not git-init'd
        master_dir.mkdir()
        (master_dir / "daily").mkdir()
        (master_dir / "daily" / "2026-08-09.md").write_text("history", encoding="utf-8")
        working_copy_dir.mkdir()

        from backup.runner import run_once as backup_run_once

        with self.assertRaises(WorkingCopyNotAGitRepositoryError):
            backup_run_once(master_dir, working_copy_dir, state_path=self.root / "backup_state.json")

        ancestor_head_after = _run_git(["rev-parse", "HEAD"], cwd=self.root).strip()
        self.assertEqual(ancestor_head_before, ancestor_head_after)
        ancestor_status = _run_git(["status", "--porcelain"], cwd=self.root)
        # Only the untracked local_master/ directory this test created —
        # nothing STAGED (an "A " or "M " prefix) from the guarded backup
        # attempt.
        for line in ancestor_status.splitlines():
            self.assertTrue(line.startswith("??"), f"unexpected staged change: {line}")


if __name__ == "__main__":
    unittest.main()
