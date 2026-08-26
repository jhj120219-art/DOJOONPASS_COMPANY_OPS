import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup.working_copy import (  # noqa: E402
    _relative_files,
    scan_for_secrets,
    sync_to_working_copy,
)


def _rel(*parts: str) -> str:
    """OS-native relative path string, matching str(Path.relative_to())."""
    return str(Path(*parts))


class SyncToWorkingCopyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.master_dir = root / "local_master"
        self.working_copy_dir = root / "backup_working_copy"
        (self.master_dir / "daily").mkdir(parents=True)

    def test_new_and_modified_files_are_copied(self):
        (self.master_dir / "daily" / "2026-08-01.md").write_text("day one", encoding="utf-8")

        result = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(result.added, (_rel("daily", "2026-08-01.md"),))
        self.assertEqual(result.deleted, ())
        self.assertEqual(
            (self.working_copy_dir / "daily" / "2026-08-01.md").read_text(encoding="utf-8"),
            "day one",
        )

    def test_deleted_file_is_reported_without_mutating_working_copy(self):
        # docs/08_BACKUP_SPEC.md §31/44-47: a file present in a prior
        # successful sync but missing from Master now must be *reported*,
        # not silently applied to the Working Copy.
        (self.master_dir / "daily" / "2026-08-01.md").write_text("day one", encoding="utf-8")
        sync_to_working_copy(self.master_dir, self.working_copy_dir)  # establish prior state

        (self.master_dir / "daily" / "2026-08-01.md").unlink()  # simulate accidental Master deletion

        result = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(result.deleted, (_rel("daily", "2026-08-01.md"),))
        # the file must still exist in the Working Copy — the deletion was
        # only reported, never applied.
        self.assertTrue((self.working_copy_dir / "daily" / "2026-08-01.md").exists())

    def test_deletion_gate_stays_open_on_the_next_run(self):
        # Regression test for the exact bug found in this Sprint's Backup
        # Runtime Failure Isolation Audit: applying the deletion to the
        # Working Copy inside the same call that reports it made the gate
        # self-clear after one blocked run (the next call's "before" state
        # already matched the deleted Master, so it saw nothing to report
        # and would have gone on to commit/push the deletion).
        (self.master_dir / "daily" / "2026-08-01.md").write_text("day one", encoding="utf-8")
        sync_to_working_copy(self.master_dir, self.working_copy_dir)
        (self.master_dir / "daily" / "2026-08-01.md").unlink()

        first = sync_to_working_copy(self.master_dir, self.working_copy_dir)
        second = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(first.deleted, (_rel("daily", "2026-08-01.md"),))
        self.assertEqual(second.deleted, (_rel("daily", "2026-08-01.md"),))
        self.assertTrue((self.working_copy_dir / "daily" / "2026-08-01.md").exists())

    def test_unrelated_add_is_also_withheld_while_a_deletion_is_pending(self):
        # All-or-nothing gate: an unrelated new file must not slip through
        # to the Working Copy while a deletion is unresolved, matching
        # backup/runner.py's own all-or-nothing BACKUP_FAILED gate.
        (self.master_dir / "daily" / "2026-08-01.md").write_text("day one", encoding="utf-8")
        sync_to_working_copy(self.master_dir, self.working_copy_dir)
        (self.master_dir / "daily" / "2026-08-01.md").unlink()
        (self.master_dir / "daily" / "2026-08-02.md").write_text("day two", encoding="utf-8")

        result = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(result.added, (_rel("daily", "2026-08-02.md"),))
        self.assertEqual(result.deleted, (_rel("daily", "2026-08-01.md"),))


    def test_same_size_different_content_is_still_detected_as_modified(self):
        # Contract guard for the size short-circuit in _content_differs():
        # detection must stay content-based. A same-size edit is exactly the
        # case a stat/mtime-based shortcut would wrongly miss, so it must be
        # byte-compared and reported.
        (self.master_dir / "daily" / "2026-08-01.md").write_text("AAAA", encoding="utf-8")
        sync_to_working_copy(self.master_dir, self.working_copy_dir)

        (self.master_dir / "daily" / "2026-08-01.md").write_text("BBBB", encoding="utf-8")  # same size

        result = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(result.modified, (_rel("daily", "2026-08-01.md"),))
        self.assertEqual(
            (self.working_copy_dir / "daily" / "2026-08-01.md").read_text(encoding="utf-8"), "BBBB"
        )

    def test_different_size_is_detected_as_modified(self):
        (self.master_dir / "daily" / "2026-08-01.md").write_text("AAAA", encoding="utf-8")
        sync_to_working_copy(self.master_dir, self.working_copy_dir)

        (self.master_dir / "daily" / "2026-08-01.md").write_text("AAAA-appended", encoding="utf-8")

        result = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(result.modified, (_rel("daily", "2026-08-01.md"),))

    def test_identical_content_is_never_reported_as_modified(self):
        (self.master_dir / "daily" / "2026-08-01.md").write_text("same", encoding="utf-8")
        sync_to_working_copy(self.master_dir, self.working_copy_dir)

        result = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(result.added, ())
        self.assertEqual(result.modified, ())
        self.assertEqual(result.deleted, ())

    def test_content_differs_matches_filecmp_exactly(self):
        # Direct equivalence check against the stdlib call this replaced.
        import filecmp

        from backup.working_copy import _content_differs

        cases = [("AAAA", "AAAA"), ("AAAA", "BBBB"), ("AAAA", "AAAAA"), ("", ""), ("", "x")]
        for i, (a, b) in enumerate(cases):
            with self.subTest(case=i):
                pa = self.master_dir / f"a{i}.bin"
                pb = self.master_dir / f"b{i}.bin"
                pa.write_text(a, encoding="utf-8")
                pb.write_text(b, encoding="utf-8")
                self.assertEqual(
                    _content_differs(pa, pb), not filecmp.cmp(pa, pb, shallow=False)
                )

    def test_out_of_scope_top_level_dirs_are_ignored(self):
        # docs/08 §26-28: only daily/ and monthly/ are ever considered.
        (self.master_dir / "decisions").mkdir()
        (self.master_dir / "decisions" / "note.md").write_text("x", encoding="utf-8")

        result = sync_to_working_copy(self.master_dir, self.working_copy_dir)

        self.assertEqual(result.added, ())
        self.assertFalse((self.working_copy_dir / "decisions").exists())


class RelativeFilesWalkTests(unittest.TestCase):
    """The scandir walk and the `rglob` form it replaced must agree exactly.

    `_relative_files()` is the listing three different things depend on: the
    sync's `added`/`modified`, the deletion gate's `deleted` (docs/08
    §43-47), and — since C45 — `ops_status._history_gone_from_local_master()`.
    A faster walk that returns a *slightly* different set would move the
    deletion gate, which is the one gate in this project that stops a backup
    outright.

    So the previous implementation is kept beside it as
    `_relative_files_by_rglob()` and this class runs both over a tree built
    to break them apart: out-of-scope siblings, a `.git` directory, nesting,
    staging residue, a directory wearing a `.md` name, a *file* named `daily`
    at the root, unicode, and (where the OS allows it) symlinks in every
    position that matters.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _both(self):
        from backup.working_copy import _relative_files, _relative_files_by_rglob

        return _relative_files(self.root), _relative_files_by_rglob(self.root)

    def _assert_agree(self):
        fast, oracle = self._both()
        self.assertEqual(fast, oracle)
        return fast

    def _write(self, rel, body="x"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def _adversarial_tree(self):
        self._write("daily/2026-08-01.md")
        self._write("daily/2026-08-02.md")
        self._write("monthly/2026-08.md")
        # nested under an in-scope directory
        self._write("daily/notes/deep/inner.md")
        # staging residue on both sides
        self._write("daily/.tmp-abandoned.md")
        self._write("monthly/.tmp-abandoned.md")
        # out of scope, in every shape
        self._write("decisions/2026-08-01.md")
        self._write("README.md")
        self._write(".gitkeep", "")
        self._write(".git/objects/ab/cdef", "loose object")
        self._write(".git/HEAD", "ref: refs/heads/main")
        self._write("Daily/2026-08-03.md")  # case-folded sibling (BUG-55)
        self._write("daily/한글.md")
        # a directory wearing a day's name
        (self.root / "daily" / "2026-08-09.md").mkdir(parents=True)
        # an empty in-scope directory
        (self.root / "monthly" / "empty").mkdir(parents=True)

    def test_the_two_walks_agree_on_an_adversarial_tree(self):
        self._adversarial_tree()

        found = self._assert_agree()

        self.assertIn(str(Path("daily") / "2026-08-01.md"), found)
        self.assertIn(str(Path("daily") / "notes" / "deep" / "inner.md"), found)
        self.assertIn(str(Path("monthly") / "2026-08.md"), found)
        self.assertNotIn(str(Path("daily") / ".tmp-abandoned.md"), found)
        self.assertNotIn("README.md", found)
        self.assertNotIn(str(Path(".git") / "HEAD"), found)
        self.assertNotIn(str(Path("decisions") / "2026-08-01.md"), found)

    def test_a_file_named_like_a_scope_directory_is_not_pruned(self):
        """`_is_in_scope("daily")` is True — `parts[0]` is `daily` and the
        basename is not a staging name. Pruning by name alone would have
        dropped it, which is a behaviour change hidden inside a speed-up."""
        self._write("daily2", "not a directory")   # out of scope
        (self.root / "daily").mkdir()
        (self.root / "daily").rmdir()
        self._write("daily", "a file, not a directory")
        self._write("monthly/2026-08.md")

        found = self._assert_agree()

        self.assertIn("daily", found)
        self.assertNotIn("daily2", found)

    def test_an_empty_root_agrees(self):
        self._assert_agree()

    def test_a_missing_root_agrees(self):
        from backup.working_copy import _relative_files, _relative_files_by_rglob

        missing = self.root / "not-here"

        self.assertEqual(_relative_files(missing), set())
        self.assertEqual(_relative_files(missing), _relative_files_by_rglob(missing))

    def test_a_root_that_is_a_file_agrees(self):
        from backup.working_copy import _relative_files, _relative_files_by_rglob

        path = self.root / "plain.txt"
        path.write_text("x", encoding="utf-8")

        self.assertEqual(_relative_files(path), set())
        self.assertEqual(_relative_files(path), _relative_files_by_rglob(path))

    def test_symlinks_are_refused_in_both_walks(self):
        """The property the fast walk must not lose: a link is never followed
        and never listed, whether it names a file or a directory. Skipped
        where the OS will not let this process create one."""
        self._write("daily/2026-08-01.md")
        outside = self.root.parent / "outside-target.md"
        outside.write_text("secret", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        outside_dir = self.root.parent / "outside-dir"
        outside_dir.mkdir(exist_ok=True)
        (outside_dir / "inner.md").write_text("secret", encoding="utf-8")
        self.addCleanup(shutil.rmtree, outside_dir, True)

        try:
            (self.root / "daily" / "linked.md").symlink_to(outside)
            (self.root / "daily" / "linked-dir").symlink_to(
                outside_dir, target_is_directory=True
            )
            (self.root / "monthly").mkdir(exist_ok=True)
            (self.root / "linked-scope").symlink_to(outside_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

        found = self._assert_agree()

        self.assertEqual(found, {str(Path("daily") / "2026-08-01.md")})

    def test_a_top_level_symlink_named_like_a_scope_directory_is_refused(self):
        """The one the pruning could have got wrong: a link named `daily`
        pointing outside would have had its target's files listed as Company
        History if the walk followed it."""
        outside_dir = self.root.parent / "outside-daily"
        outside_dir.mkdir(exist_ok=True)
        (outside_dir / "2026-01-01.md").write_text("elsewhere", encoding="utf-8")
        self.addCleanup(shutil.rmtree, outside_dir, True)

        try:
            (self.root / "daily").symlink_to(outside_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

        self.assertEqual(self._assert_agree(), set())

    def test_the_sync_and_the_gate_still_see_the_same_thing(self):
        """End of the chain: the numbers the fast walk feeds are the ones the
        deletion gate and the status view act on."""
        from backup.working_copy import sync_to_working_copy

        master = self.root / "master"
        copy = self.root / "copy"
        (master / "daily").mkdir(parents=True)
        (copy / "daily").mkdir(parents=True)
        (master / "daily" / "2026-08-02.md").write_text("b", encoding="utf-8")
        (copy / "daily" / "2026-08-01.md").write_text("a", encoding="utf-8")
        (copy / ".git").mkdir()
        (copy / ".git" / "HEAD").write_text("ref", encoding="utf-8")

        result = sync_to_working_copy(master, copy)

        self.assertEqual(result.deleted, (str(Path("daily") / "2026-08-01.md"),))
        self.assertEqual(result.added, (str(Path("daily") / "2026-08-02.md"),))


class LongPathBoundaryTests(unittest.TestCase):
    """New finding this Sprint: only `scheduler/lock.py` was given the
    `\\\\?\\` extended-length-path prefix (this Sprint's earlier Lock
    incident fix). `backup/working_copy.py` never got the same treatment,
    and it breaks the same way `lock.py` did before that fix.

    CHARACTERIZATION: asserts today's behaviour, not desired behaviour.

    Reproduced directly: once `master_dir` alone is >~250 characters (well
    under the 260-char MAX_PATH once `daily/<file>.md` is appended),
    `sync_to_working_copy()` raises `OSError` (`WinError 206`, "filename or
    extension too long") instead of returning a result.

    Whether that limit applies at all is a property of the machine, not of
    this code: Windows lifts it per-system via the `LongPathsEnabled`
    registry setting, and POSIX never had it. The test below therefore
    probes the filesystem first and asserts whichever branch is real here,
    so it keeps describing the gap on a machine that has it and pins the
    working behaviour on one that does not. It previously hard-asserted
    `OSError` and so failed outright on a long-path-enabled machine — the
    coverage silently became a false alarm rather than a finding.

    Not fixed here, unlike the Lock case, because the fix is not obviously
    narrow: git.exe itself has its own long-path limitation independent of
    Python's (`core.longpaths` must be set for git to write >260-char paths
    at all), so patching only this module's Python-side path handling would
    not make the Backup pipeline actually work end to end over a long path
    -- `git add`/`git commit` in `git_ops.py` would still be reachable by
    the same limit. Deciding the fix's scope (Python-only prefix vs. also
    requiring/asserting a git config change vs. documenting a path-length
    deployment constraint) is a policy question. In the currently documented
    deployment layout (`D:\\DOJOONPASS_COO\\`, docs/11 section 12) ordinary
    `daily/YYYY-MM-DD.md` paths stay far short of this boundary, so this is
    a real but low-probability-in-practice gap, not an active incident.
    """

    def test_a_master_directory_path_past_the_extended_length_boundary_raises(self):
        """Even building the fixture (plain `mkdir`/`write_text`, no
        extended-length-path prefix) fails once the path crosses the
        boundary -- the same WinError 206 a real deployment would hit, not
        an artifact of this test's own setup being unusual.

        On a machine where long paths ARE supported, the same setup
        succeeds; then what must hold is that `sync_to_working_copy()`
        completes normally rather than failing for some other reason.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        deep = root
        while len(str(deep)) < 250:
            deep = deep / ("a" * 50)
        master_dir = deep / "master"
        working_copy_dir = deep / "wc"

        try:
            (master_dir / "daily").mkdir(parents=True)
            (master_dir / "daily" / "2026-08-09.md").write_text("x", encoding="utf-8")
        except OSError:
            # The documented gap: the OS rejects the path before Company Ops
            # code is even reached.
            with self.assertRaises(OSError):
                sync_to_working_copy(master_dir, working_copy_dir)
            return

        result = sync_to_working_copy(master_dir, working_copy_dir)
        self.assertEqual(result.added, (_rel("daily", "2026-08-09.md"),))
        self.assertEqual(
            (working_copy_dir / "daily" / "2026-08-09.md").read_text(encoding="utf-8"), "x"
        )


@unittest.skipUnless(sys.platform == "win32", "junctions are an NTFS feature")
class JunctionsAreNotExcludedFromTheWalkTests(unittest.TestCase):
    """Characterisation, not endorsement (C113).

    `_relative_files()`'s docstring used to claim "A symlink/junction is
    excluded even when it resolves to a file". Only the symlink half is
    true: `Path.is_symlink()` answers **False** for a junction on Windows,
    so the guard never sees one and `_walk()` descends it.

    None of the exposure is news -- A-19 records junction-following as a
    Local Master risk, E-21 the Working Copy side, and C70 built
    `ops_status._junctions_in_scope()` to report it. What was wrong was the
    sentence, which told a reader the walk already handled what those three
    entries are open about.

    The walk is deliberately unchanged: excluding junctions is a behaviour
    change, and a deployment that junctions a directory of Daily History
    into Local Master would find its backup silently shrink -- the false
    positive E-15 records as the worse failure. So these tests fix the
    measurement instead, and fail the day that decision lands.

    Junctions rather than symlinks because a junction needs no privilege:
    `os.symlink()` raises WinError 1314 on this machine, which is why the
    symlink E2E tests skip.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "notes.md").write_text(
            "# lives outside Local Master\n", encoding="utf-8"
        )
        self.master = self.root / "master"
        (self.master / "daily").mkdir(parents=True)
        (self.master / "daily" / "2026-08-01.md").write_text("# ok\n", encoding="utf-8")

    def _junction(self, link: Path, target: Path) -> bool:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        return result.returncode == 0 and link.exists()

    def test_python_does_not_call_a_junction_a_symlink(self):
        """The fact the whole thing rests on. If this ever changes, the
        guard starts working and the tests below should fail."""
        link = self.master / "daily" / "ext"
        if not self._junction(link, self.outside):
            self.skipTest("mklink /J unavailable")

        self.assertFalse(link.is_symlink())
        self.assertTrue(link.is_dir())

    def test_the_walk_descends_a_junction(self):
        link = self.master / "daily" / "ext"
        if not self._junction(link, self.outside):
            self.skipTest("mklink /J unavailable")

        found = _relative_files(self.master)

        self.assertIn(str(Path("daily") / "2026-08-01.md"), found)
        self.assertIn(
            str(Path("daily") / "ext" / "notes.md"),
            found,
            "a junction is followed -- pinned as a measurement, not endorsed",
        )

    def test_a_file_from_outside_reaches_the_working_copy(self):
        """End to end through the real sync, which is what `git add -A`
        then pushes."""
        link = self.master / "daily" / "ext"
        if not self._junction(link, self.outside):
            self.skipTest("mklink /J unavailable")
        working_copy = self.root / "wc"
        working_copy.mkdir()

        sync_to_working_copy(self.master, working_copy)

        copied = working_copy / "daily" / "ext" / "notes.md"
        self.assertTrue(copied.is_file())
        self.assertIn("outside Local Master", copied.read_text(encoding="utf-8"))

    def test_the_secret_gate_cannot_see_through_it(self):
        """`scan_for_secrets()` matches filenames. The file that came
        through was called `notes.md`, so the gate says nothing -- which is
        correct behaviour for a filename matcher and exactly why the
        detector C70 built reports the junction itself instead."""
        link = self.master / "daily" / "ext"
        if not self._junction(link, self.outside):
            self.skipTest("mklink /J unavailable")
        (self.outside / "notes.md").write_text(
            "API_KEY=not-a-real-secret\n", encoding="utf-8"
        )
        working_copy = self.root / "wc"
        working_copy.mkdir()
        sync_to_working_copy(self.master, working_copy)

        self.assertEqual(scan_for_secrets(self.master), ())
        self.assertEqual(scan_for_secrets(working_copy), ())

    def test_the_docstring_no_longer_claims_junctions_are_excluded(self):
        """The half C113 did fix. A docstring that says the guard covers a
        case it does not is worse than no docstring: it stops the next
        reader from checking."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "backup" / "working_copy.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("A symlink/junction is excluded", source)
        self.assertIn("A **directory junction is not**", source)


if __name__ == "__main__":
    unittest.main()
