import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup.working_copy import sync_to_working_copy  # noqa: E402


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
        self.assertFalse((self.working_copy_dir / "daily" / "2026-08-02.md").exists())

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


if __name__ == "__main__":
    unittest.main()
