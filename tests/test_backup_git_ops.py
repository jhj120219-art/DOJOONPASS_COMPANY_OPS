import os
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


class NonBlockingGuaranteeTests(unittest.TestCase):
    """A git call must always end.

    `app/runner.py` holds the system-wide lock for the whole Backup step, so
    a git command that blocks blocks every future Runner run as well — worse
    than the infinite retry loop docs/08 section 62 forbids, because a retry
    loop at least keeps collecting Events.

    Two ways a push could block indefinitely, neither previously closed:
    a credential prompt (terminal or the Windows Git Credential Manager
    dialog), and a remote that accepts a connection and then stalls.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        for args in (
            ["init", "-q", "-b", "main", "."],
            ["config", "user.email", "t@example.invalid"],
            ["config", "user.name", "Timeout Test"],
        ):
            subprocess.run(["git", *args], cwd=self.repo, capture_output=True, check=True)

    def test_every_git_call_carries_a_timeout(self):
        import inspect

        from backup import git_ops

        source = inspect.getsource(git_ops._run_git)
        self.assertIn("timeout=_GIT_TIMEOUT_SECONDS", source)
        self.assertIn("subprocess.TimeoutExpired", source)

    def test_a_timeout_becomes_a_git_operation_error(self):
        """Not a raw TimeoutExpired: `backup/runner.py` classifies
        GitOperationError, and anything else would escape that handling and
        take the Runner down mid-Backup.

        The property under test is the **conversion**, so the timeout is
        injected rather than raced for. This test used to set
        `_GIT_TIMEOUT_SECONDS = 0.001` and run a real `git status`, which is
        a race and not a guarantee: `subprocess.run()` enforces its deadline
        at the `communicate()` wait, so a child that has already exited
        returns normally no matter how long it took. Measured at HEAD before
        this change — 20 consecutive runs of this one test, standalone, on an
        otherwise idle machine:

            9 passed, then `AssertionError: GitOperationError not raised`

        A ~10-40% flake, and it fired in the full suite (C50). A flaky test
        is worse than a missing one: it trains a reader to re-run rather than
        to look, which is the habit that lets a real regression through.

        That the *deadline is wired up at all* is a separate property and is
        pinned separately, by `test_every_git_call_carries_a_timeout` above —
        which reads `_run_git`'s source for `timeout=_GIT_TIMEOUT_SECONDS`
        and `subprocess.TimeoutExpired`. Between the two, nothing is lost.
        """
        import subprocess

        from backup import git_ops

        real_run = subprocess.run

        def _always_times_out(command, *args, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 300.0))

        subprocess.run = _always_times_out
        self.addCleanup(setattr, subprocess, "run", real_run)

        with self.assertRaises(GitOperationError) as caught:
            git_ops._run_git(["status", "--porcelain"], self.repo)

        self.assertIn("timed out", str(caught.exception))

    def test_the_injected_timeout_is_the_one_the_real_call_would_hit(self):
        """The injection above must not drift from what production passes.

        `_run_git()` hands `subprocess.run` its `timeout=` keyword, and this
        reads the value back out of the call it actually makes — so a rename
        or a dropped keyword fails here rather than leaving the test above
        asserting a conversion that no real call can reach.
        """
        import subprocess

        from backup import git_ops

        real_run = subprocess.run
        seen = {}

        def _record(command, *args, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))

        subprocess.run = _record
        self.addCleanup(setattr, subprocess, "run", real_run)

        with self.assertRaises(GitOperationError):
            git_ops._run_git(["status", "--porcelain"], self.repo)

        self.assertEqual(seen.get("timeout"), git_ops._GIT_TIMEOUT_SECONDS)

    def test_a_timeout_is_not_mistaken_for_an_authentication_failure(self):
        """A timeout is transient -> BACKUP_PENDING (retry next run). An auth
        failure is permanent -> BACKUP_FAILED. Misclassifying the first as
        the second would abandon a backup that would have succeeded."""
        from backup import git_ops

        message = (
            "git push timed out after 300s (no output; the remote may be "
            "unreachable or waiting for credentials)"
        )
        self.assertFalse(git_ops.is_authentication_failure(message))

    def test_interactive_prompts_are_disabled_for_every_call(self):
        """`_AUTH_FAILURE_MARKERS` already listed "terminal prompts disabled"
        — the message git emits only when GIT_TERMINAL_PROMPT=0. Nothing set
        it, so that marker could never match and the condition it describes
        hung instead of failing."""
        from backup import git_ops

        environment = git_ops._git_environment()

        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GCM_INTERACTIVE"], "never")

    def test_the_marker_the_environment_enables_is_actually_matched(self):
        from backup import git_ops

        self.assertTrue(
            git_ops.is_authentication_failure(
                "fatal: could not read Username for 'https://github.com': "
                "terminal prompts disabled"
            )
        )

    def test_git_answers_in_the_language_the_markers_are_written_in(self):
        """`_AUTH_FAILURE_MARKERS` is a list of English phrases, so every
        classification depends on git speaking English.

        git translates its messages when a catalog for the caller's locale is
        installed. Where one is, no marker matches a real credential failure,
        `is_authentication_failure()` answers False, and docs/08 §19 sends the
        run to BACKUP_PENDING — the unbounded retry loop §62 forbids, reached
        by the one route the classification cannot see.

        The environment pins the locale, so this asserts the pin and not the
        packaging of whatever git happens to be installed.
        """
        from backup import git_ops

        environment = git_ops._git_environment()

        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["LANG"], "C")

    def test_a_localised_caller_environment_does_not_reach_git(self):
        """The pin has to survive an operator (or a Task Scheduler profile)
        whose own environment names a language. Overriding an inherited
        value is the whole point — `dict(os.environ)` copies it in first."""
        import os
        import unittest.mock

        from backup import git_ops

        with unittest.mock.patch.dict(
            os.environ, {"LC_ALL": "ko_KR.UTF-8", "LANG": "ko_KR.UTF-8"}
        ):
            environment = git_ops._git_environment()

        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["LANG"], "C")

    def test_the_markers_still_match_under_the_pinned_locale(self):
        """Pinning the locale is only worth anything if the phrases it
        guarantees are the phrases the list holds. Real git/GCM output.

        Deliberately only the cases the marker list already catches. Which
        *other* messages ought to be caught is BUG-52, a classification
        decision, and it is characterized where it belongs
        (`test_runner_failure_paths.py::AuthFailureClassificationTests`).
        The locale pin does not touch it: `remote: Permission to x/y.git
        denied to user.` is missed in every locale, English included.
        """
        from backup import git_ops

        for message in (
            "remote: Support for password authentication was removed on "
            "August 13, 2021.",
            "fatal: Authentication failed for 'https://github.com/x/y.git/'",
            "git@github.com: Permission denied (publickey).",
            "fatal: could not read Username for 'https://github.com': "
            "terminal prompts disabled",
        ):
            with self.subTest(message=message[:40]):
                self.assertTrue(git_ops.is_authentication_failure(message))

    def test_the_git_environment_does_not_discard_the_rest_of_the_environment(self):
        """git needs PATH, HOME/USERPROFILE, and on Windows SystemRoot. A
        replaced (rather than extended) environment breaks git itself."""
        import os

        from backup import git_ops

        environment = git_ops._git_environment()

        for key in list(os.environ)[:5]:
            with self.subTest(key=key):
                self.assertIn(key, environment)

    def test_a_normal_call_still_works_under_the_hardened_environment(self):
        """The guard must not break the ordinary path."""
        from backup import git_ops

        result = git_ops.git_status(self.repo)

        self.assertFalse(result.has_changes)

    def test_no_credential_prompt_can_block_a_push(self):
        """End to end: a remote that would require credentials fails rather
        than waiting for an answer nobody is there to give."""
        subprocess.run(
            ["git", "remote", "add", "origin", "https://127.0.0.1:1/private.git"],
            cwd=self.repo,
            capture_output=True,
            check=True,
        )
        (self.repo / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "init"], cwd=self.repo, capture_output=True, check=True
        )

        from backup import git_ops

        with self.assertRaises(GitOperationError):
            git_ops.git_push(self.repo)


class PorcelainRenameBoundaryTests(unittest.TestCase):
    """CHARACTERIZATION — records today's parsing, not desired parsing.

    `_parse_porcelain()` splits each line as `code = line[:2]`,
    `path = line[3:]`. For a rename git writes `R  old -> new`, so the whole
    `"old -> new"` string becomes one entry — not a path that exists.

    Unreachable in production, verified rather than assumed:
    `sync_to_working_copy()` never deletes from the Working Copy (docs/08
    section 31/44-47 — it *reports* a deletion and the backup runner stops).
    The Working Copy therefore only ever gains or updates files, and git
    needs a delete plus an add of similar content to detect a rename at all.

    NOT FIXED: how a rename should be represented in `changed_files` (one
    entry? two? which name?) is a reporting decision, and the code path
    cannot currently be reached to justify making it. Pinned so that a
    future change which DOES make renames reachable — anything that lets the
    Working Copy lose a file — shows up here rather than as a nonsense path
    in a backup log.
    """

    def test_a_rename_becomes_one_entry_that_is_not_a_path(self):
        from backup.git_ops import _parse_porcelain

        result = _parse_porcelain("R  daily/old.md -> daily/new.md")

        self.assertEqual(result.changed_files, ("daily/old.md -> daily/new.md",))
        self.assertEqual(result.deleted_files, ())
        self.assertTrue(result.has_changes)

    def test_a_copy_parses_the_same_way(self):
        from backup.git_ops import _parse_porcelain

        result = _parse_porcelain("C  daily/a.md -> daily/b.md")

        self.assertEqual(result.changed_files, ("daily/a.md -> daily/b.md",))

    def test_ordinary_statuses_parse_correctly(self):
        """The reachable cases, which Backup actually produces."""
        from backup.git_ops import _parse_porcelain

        for line, changed, deleted in (
            ("A  daily/new.md", ("daily/new.md",), ()),
            (" M daily/mod.md", ("daily/mod.md",), ()),
            ("?? daily/untracked.md", ("daily/untracked.md",), ()),
            (" D daily/gone.md", (), ("daily/gone.md",)),
        ):
            with self.subTest(line=line):
                result = _parse_porcelain(line)
                self.assertEqual(result.changed_files, changed)
                self.assertEqual(result.deleted_files, deleted)

    def test_the_working_copy_sync_never_deletes_which_is_why_this_is_unreachable(self):
        """The structural reason. If this ever stops being true, the rename
        parse above becomes reachable and the characterization must be
        revisited."""
        import inspect

        from backup import working_copy

        source = inspect.getsource(working_copy.sync_to_working_copy)
        for destructive in ("unlink(", "rmtree(", "os.remove("):
            with self.subTest(call=destructive):
                self.assertNotIn(destructive, source)


class _RealBackupFixture(GitOpsTestCase):
    """Harness only: a real Working Copy, a real bare remote, real git.

    Declared separately for the reason
    `test_runner_notion_integration.RunnerNotionTestCase` gives — a second
    suite needs these three helpers, and inheriting the suite that had them
    would re-run every one of its tests. It subclasses `GitOpsTestCase`
    rather than mixing in, because the repo fixture is `setUp`'s job.
    """

    def _setup_master_and_remote(self):
        master = self.repo_dir.parent / "local_master"
        (master / "daily").mkdir(parents=True)
        bare = self.repo_dir.parent / "remote.git"
        _run_git(["init", "--bare", "-b", "main", str(bare)], cwd=self.repo_dir.parent)
        _run_git(["remote", "add", "origin", str(bare)], cwd=self.repo_dir)
        # docs/08 §30 operator setup: an initialised, tracking Working Copy.
        (self.repo_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(["add", "-A"], cwd=self.repo_dir)
        _run_git(["commit", "-m", "init"], cwd=self.repo_dir)
        _run_git(["push", "-u", "origin", "main"], cwd=self.repo_dir)
        return master, bare

    def _remote_files(self, bare):
        return sorted(_run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=bare).split())

    def _backup(self, master, run_id):
        import backup.runner as backup_runner

        return backup_runner.run_once(
            master_dir=master,
            working_copy_dir=self.repo_dir,
            state_path=self.repo_dir.parent / "backup_state.json",
            run_id=run_id,
        )


class StagingResidueThroughTheRealBackupTests(_RealBackupFixture):
    """C27's highest-severity fix, verified end to end instead of at the seam.

    The unit coverage stops at `sync_to_working_copy()`. What that cannot
    answer is the question docs/08 §29 and BACKLOG E-21 are actually about:
    **does what `BACKUP_SUCCESS` claims match what is in the remote commit?**
    `git add -A` stages the Working Copy, not the sync result, so the two
    are only equal if nothing reaches the Working Copy by another route.

    Three runs against a real bare remote, in order, because the second and
    third only mean something after the first:

        run 1  Master holds a real day and a `.tmp-` staging file left by a
               killed run          -> BACKUP_SUCCESS, and the remote commit
                                      contains the day and NOT the staging
                                      file
        run 2  the operator deletes the staging file from Master — the only
               sane response to garbage
                                   -> BACKUP_NOT_REQUIRED, deleted=()

               Pre-C27 this was the trap: the staging file HAD been synced,
               so removing it from Master reported a deletion, and the
               deletion gate (§43-47) applies nothing while `deleted` is
               non-empty — every subsequent run failed. Cleaning up the
               garbage was what broke Backup permanently.

        run 3  a REAL day is deleted from Master
                                   -> BACKUP_FAILED, deleted names that day,
                                      and the remote still holds it

    Run 3 is the half that makes run 2 safe to assert. An exclusion that
    also swallowed real deletions would pass runs 1 and 2 and destroy the
    protection §43-47 exists for.
    """


    def test_the_remote_commit_matches_what_backup_success_claims(self):
        from backup.result import BackupStatus

        master, bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-13.md").write_text("# real day\n", encoding="utf-8")
        (master / "daily" / ".tmp-killed.md").write_text(
            "# DOJOONPASS Company Hist", encoding="utf-8"
        )

        entry = self._backup(master, "RUN-1")

        self.assertIs(entry.final_status, BackupStatus.SUCCESS)
        self.assertEqual(entry.push_result, "SUCCESS")
        remote = self._remote_files(bare)
        self.assertIn("daily/2026-08-13.md", remote)
        self.assertEqual([f for f in remote if ".tmp-" in f], [], remote)

    def test_cleaning_the_staging_file_up_does_not_break_backup(self):
        """The trap C27 removed, stated as the operator's action."""
        from backup.result import BackupStatus

        master, _bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-13.md").write_text("# real day\n", encoding="utf-8")
        staged = master / "daily" / ".tmp-killed.md"
        staged.write_text("# DOJOONPASS Company Hist", encoding="utf-8")
        self._backup(master, "RUN-1")

        staged.unlink()
        entry = self._backup(master, "RUN-2")

        self.assertEqual(entry.deleted_files, ())
        self.assertIsNot(entry.final_status, BackupStatus.FAILED)

    def test_deleting_a_real_day_still_fails_the_backup(self):
        """The guard on the guard. An exclusion that swallowed real
        deletions would pass both tests above and silently remove the
        protection docs/08 §43-47 exists for."""
        from backup.result import BackupStatus

        master, bare = self._setup_master_and_remote()
        day = master / "daily" / "2026-08-13.md"
        day.write_text("# real day\n", encoding="utf-8")
        self._backup(master, "RUN-1")

        day.unlink()
        entry = self._backup(master, "RUN-2")

        self.assertIs(entry.final_status, BackupStatus.FAILED)
        self.assertEqual(len(entry.deleted_files), 1, entry.deleted_files)
        self.assertIn("2026-08-13.md", entry.deleted_files[0])
        # ...and the remote is untouched: the gate stops before push.
        self.assertIn("daily/2026-08-13.md", self._remote_files(bare))

    def test_a_staging_file_alone_is_not_a_backup(self):
        """Master holding only a staging file means there is no Company
        History to back up — not a backup of one file."""
        master, bare = self._setup_master_and_remote()
        (master / "daily" / ".tmp-killed.md").write_text("partial", encoding="utf-8")

        self._backup(master, "RUN-1")

        self.assertEqual(self._remote_files(bare), [".gitkeep"])

    def test_only_the_case_of_a_secret_filename_decides_whether_it_leaks(self):
        """NEW, **security**. CHARACTERIZATION — asserts today's behaviour.

        Same content, same in-scope directory, same run: `daily/id_rsa` is
        stopped by docs/08 §29's gate and `daily/ID_RSA` reaches the remote.
        The name list is right; the comparison is case-sensitive and Windows
        is not, so which one an operator happens to type decides whether the
        gate protects them.

        Asserted through the real remote rather than at `scan_for_secrets()`,
        because the seam cannot answer the question that matters — whether
        the key is readable from the backup. It is:
        `git show main:daily/ID_RSA` returns the key material.

        BUG-55's root at a second location. Not fixed here: case-folding the
        comparison gives the gate a new BACKUP_FAILED condition, which is
        E-15's documented harm, and that pair is recorded as needing a
        decision. `ops_status._secret_names_the_gate_will_not_recognise()`
        reports it meanwhile. If this test starts failing, the gate became
        case-insensitive and BACKLOG must be updated with it.
        """
        from backup.result import BackupStatus

        KEY = "-----BEGIN OPENSSH PRIVATE KEY-----"
        master, bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-13.md").write_text("# real day\n", encoding="utf-8")
        (master / "daily" / "ID_RSA").write_text(KEY, encoding="utf-8")

        entry = self._backup(master, "RUN-1")

        self.assertIs(entry.final_status, BackupStatus.SUCCESS)
        self.assertEqual(entry.push_result, "SUCCESS")
        self.assertIn("daily/ID_RSA", self._remote_files(bare))
        self.assertEqual(
            _run_git(["show", "main:daily/ID_RSA"], cwd=bare).strip(), KEY
        )

    def test_the_exact_case_of_the_same_file_is_stopped_before_push(self):
        """The control the test above needs. Only the case differs.

        A separate fixture on purpose: on Windows `ID_RSA` and `id_rsa` are
        one path, so the two cannot coexist — which is itself the point.
        """
        from backup.result import BackupStatus

        master, bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-13.md").write_text("# real day\n", encoding="utf-8")
        (master / "daily" / "id_rsa").write_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----", encoding="utf-8"
        )

        entry = self._backup(master, "RUN-1")

        self.assertIs(entry.final_status, BackupStatus.FAILED)
        self.assertIn("secret", (entry.push_result or "").lower())
        self.assertEqual(self._remote_files(bare), [".gitkeep"])


class ChangedFilesIsWhatTheCommitCarriesTests(_RealBackupFixture):
    """REGRESSION. `BACKUP_SUCCESS` counted directories, not files.

    `git status --porcelain` collapses an untracked DIRECTORY into a single
    entry, and step 5's status — taken BEFORE `git add -A` — was the list
    that reached `BackupLogEntry.changed_files`, the Run Manifest's
    `changed_files` metric, and OPS_BACKUP's `Changed Files` column.

    Measured, three brand-new Daily files in a Working Copy that has never
    had a `daily/` committed:

        before add   ?? daily/                    -> changed_files ('daily/',)
        after  add   A  daily/2026-08-01.md          changed_files 3 names
                     A  daily/2026-08-02.md
                     A  daily/2026-08-03.md

    So the manifest read `changed_files=1` for a backup that pushed three
    days, and named a directory rather than any of them. BUG-39 added that
    metric to answer "what did this backup carry"; on the runs where the
    answer matters most it did not — the FIRST backup after the operator's
    `git init` (docs/08 §30), a disaster restore that re-inits one
    (docs/10 §45), and the run that first writes `monthly/`, which happens
    once in ordinary operation.

    Not a data loss: every file was committed and pushed correctly. It is
    the report that was wrong, in the direction that understates.

    The fix takes a second `status --porcelain` between the add and the
    commit — no new git command, so the approved-command-set gate is
    untouched — and deliberately keeps reading GIT rather than
    `sync_result`, because `git add -A` stages the whole Working Copy and
    only git's own list includes what arrived there by another route
    (BACKLOG E-21's stray files).
    """

    def _days(self, master, *names):
        for name in names:
            (master / "daily" / name).write_text("# " + name + "\n", encoding="utf-8")

    def test_a_first_backup_names_every_file_not_the_directory(self):
        master, bare = self._setup_master_and_remote()
        self._days(master, "2026-08-01.md", "2026-08-02.md", "2026-08-03.md")

        entry = self._backup(master, "RUN-1")

        self.assertEqual(
            sorted(entry.changed_files),
            ["daily/2026-08-01.md", "daily/2026-08-02.md", "daily/2026-08-03.md"],
        )

    def test_the_count_matches_the_remote_commit(self):
        """The property worth having, stated against the remote itself
        rather than against another local list."""
        master, bare = self._setup_master_and_remote()
        self._days(master, "2026-08-01.md", "2026-08-02.md", "2026-08-03.md")

        entry = self._backup(master, "RUN-1")

        pushed = [f for f in self._remote_files(bare) if f != ".gitkeep"]
        self.assertEqual(len(entry.changed_files), len(pushed))
        self.assertEqual(sorted(entry.changed_files), pushed)

    def test_the_premise_git_really_does_collapse_an_untracked_directory(self):
        """Stated from git, not assumed. If git stops collapsing, the second
        status call becomes redundant rather than wrong — and this test says
        which world we are in."""
        master, _bare = self._setup_master_and_remote()
        self._days(master, "2026-08-01.md", "2026-08-02.md", "2026-08-03.md")
        from backup.working_copy import sync_to_working_copy

        sync_to_working_copy(master, self.repo_dir)
        before = _run_git(["status", "--porcelain"], cwd=self.repo_dir).split()
        _run_git(["add", "-A"], cwd=self.repo_dir)
        after = [
            line[3:]
            for line in _run_git(["status", "--porcelain"], cwd=self.repo_dir).splitlines()
        ]

        self.assertEqual(before, ["??", "daily/"])
        self.assertEqual(sorted(after), sorted(["daily/2026-08-0%d.md" % n for n in (1, 2, 3)]))

    def test_a_later_backup_into_a_tracked_directory_was_never_wrong(self):
        """The half that explains why this hid for so long: once `daily/`
        holds one tracked file, git lists new files inside it individually
        and the old code's count was already right."""
        master, _bare = self._setup_master_and_remote()
        self._days(master, "2026-08-01.md")
        self._backup(master, "RUN-1")
        self._days(master, "2026-08-02.md", "2026-08-03.md")

        entry = self._backup(master, "RUN-2")

        self.assertEqual(
            sorted(entry.changed_files), ["daily/2026-08-02.md", "daily/2026-08-03.md"]
        )

    def test_a_stray_working_copy_file_is_still_reported(self):
        """E-21's file reaches the commit through `git add -A` and through no
        sync result. Reading git rather than `sync_result` is what keeps it
        in this list."""
        master, bare = self._setup_master_and_remote()
        self._days(master, "2026-08-01.md")
        (self.repo_dir / "scratch.log").write_text("stray\n", encoding="utf-8")

        entry = self._backup(master, "RUN-1")

        self.assertIn("scratch.log", entry.changed_files)
        self.assertIn("scratch.log", self._remote_files(bare))

    def test_a_deletion_made_in_the_working_copy_is_still_reported(self):
        """The other column of the same status.

        A file deleted from the Working Copy but still present in Master is
        simply re-copied by `sync_to_working_copy()` before git ever looks,
        so it is NOT a deletion (asserted below, because the premise matters).
        A stray file that only ever existed in the Working Copy is different:
        nothing re-creates it, `git add -A` stages its removal, and it must
        stay visible in this list.
        """
        master, _bare = self._setup_master_and_remote()
        self._days(master, "2026-08-01.md")
        (self.repo_dir / "scratch.log").write_text("stray\n", encoding="utf-8")
        self._backup(master, "RUN-1")

        # Re-copied from Master: not a deletion.
        (self.repo_dir / "daily" / "2026-08-01.md").unlink()
        # Only ever in the Working Copy: a real deletion.
        (self.repo_dir / "scratch.log").unlink()
        self._days(master, "2026-08-02.md")

        entry = self._backup(master, "RUN-2")

        self.assertEqual(entry.deleted_files, ("scratch.log",))
        self.assertIn("daily/2026-08-01.md", self._remote_files(_bare))


class ChangedFilesMeansTheSameThingAsTheStatusTests(_RealBackupFixture):
    """CHARACTERIZATION — one field name, two meanings, keyed by status.

    `ChangedFilesIsWhatTheCommitCarriesTests` fixed the SUCCESS path to report
    git's own staged list. The two paths that stop BEFORE any git command —
    the deletion gate (docs/08 §31, §44-47) and the mass-modification guard
    (§46-47) — still report `sync_result.added + modified`, and that asymmetry
    was noted in C42 and pinned by nothing.

    Both are right for their own path, which is why this characterises rather
    than unifies:

        SUCCESS         a commit exists. Only git's list cannot understate
                        what was pushed — it includes whatever reached the
                        Working Copy by routes the sync never saw (E-21).
        blocked before  no commit exists, and `git add -A` never ran. git's
        any git command  list would describe a staging area that was never
                        built; the sync's list describes what was prepared
                        and did not get backed up, which is the question.

    Measured through the real Runner (C43), a Daily file deleted from Local
    Master after a successful backup:

        final_status   BACKUP_FAILED
        deleted_files  ('daily/2026-08-01.md',)
        manifest       classification BACKUP_FAILED, CRITICAL/PERMANENT,
                       reason naming the file, metrics
                       {'changed_files': 1, 'deleted_files': 1}
        Dashboard      Backup Status BACKUP_FAILED, Deleted Files 1, FAIL
    """

    def test_the_deletion_gate_reports_what_the_sync_prepared(self):
        master, _bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-01.md").write_text("# a\n", encoding="utf-8")
        self._backup(master, "RUN-1")

        (master / "daily" / "2026-08-02.md").write_text("# b\n", encoding="utf-8")
        (master / "daily" / "2026-08-01.md").unlink()
        entry = self._backup(master, "RUN-2")

        self.assertEqual(entry.final_status.value, "BACKUP_FAILED")
        # Native separators, because these names come from `pathlib` walking
        # the Working Copy — not from git, which always answers in POSIX
        # form. That is the third face of the same split (source, meaning,
        # separator) and it is asserted rather than normalised: nothing
        # compares the two lists, and a test that hid the difference would
        # be the reason nobody noticed if something started to.
        self.assertEqual(entry.deleted_files, (os.path.join("daily", "2026-08-01.md"),))
        # The day the sync had prepared, from the sync — no commit exists.
        self.assertEqual(entry.changed_files, (os.path.join("daily", "2026-08-02.md"),))
        self.assertIsNone(entry.commit_hash)

    def test_the_two_paths_do_not_even_spell_paths_the_same_way(self):
        """Stated on its own so the split cannot be read as cosmetic.

        git answers `daily/2026-08-01.md` on every platform;
        `sync_to_working_copy()` builds its lists from `pathlib`, which on
        Windows spells the same file `daily\2026-08-01.md`. Both reach an
        operator — the manifest's `reason` names deleted files, and
        `ChangedFilesIsWhatTheCommitCarriesTests` names committed ones.
        """
        master, _bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-01.md").write_text("# a\n", encoding="utf-8")
        committed = self._backup(master, "RUN-1")

        (master / "daily" / "2026-08-01.md").unlink()
        blocked = self._backup(master, "RUN-2")

        self.assertIn("daily/2026-08-01.md", committed.changed_files)
        self.assertEqual(
            blocked.deleted_files, (os.path.join("daily", "2026-08-01.md"),)
        )

    def test_the_remote_is_untouched_on_that_path(self):
        """The property the field is describing: nothing was pushed, so
        `changed_files` names what did NOT get backed up."""
        master, bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-01.md").write_text("# a\n", encoding="utf-8")
        self._backup(master, "RUN-1")
        before = self._remote_files(bare)

        (master / "daily" / "2026-08-02.md").write_text("# b\n", encoding="utf-8")
        (master / "daily" / "2026-08-01.md").unlink()
        self._backup(master, "RUN-2")

        self.assertEqual(self._remote_files(bare), before)

    def test_the_success_path_reports_git_instead(self):
        """The contrast in one place, so the two meanings are readable
        together rather than inferred from two classes."""
        master, _bare = self._setup_master_and_remote()
        (master / "daily" / "2026-08-01.md").write_text("# a\n", encoding="utf-8")
        (self.repo_dir / "stray.txt").write_text("only in the working copy\n", encoding="utf-8")

        entry = self._backup(master, "RUN-1")

        self.assertEqual(entry.final_status.value, "BACKUP_SUCCESS")
        # `stray.txt` is in no sync result and IS in the commit.
        self.assertIn("stray.txt", entry.changed_files)
        self.assertIn("daily/2026-08-01.md", entry.changed_files)

if __name__ == "__main__":
    unittest.main()
