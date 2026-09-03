"""`delivery/git_activity.py` — the D+1 half that does not come from Events.

Every test here drives a **real git repository** built in a temp directory.
Nothing is mocked, and the reason is the defect this module shipped with and
that these tests were written after finding: the first `_LOG_FORMAT` put the
record separator at the *end*, and `--name-only` prints a commit's files
*after* its formatted line — so every file list was attributed to the next
commit and the newest commit reported zero files. `commit_count` was right,
`files_changed` was silently empty, and a stub returning canned text would
have agreed with the parser instead of catching it. Only git's own output
has that shape.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from delivery import GitActivity, read_git_activity  # noqa: E402
from delivery.git_activity import _parse_records  # noqa: E402


def _git(args, cwd, env=None):
    environment = dict(os.environ)
    environment.update(env or {})
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=environment,
    )


class GitTestCase(unittest.TestCase):
    """A real repository, with commits placed on chosen days.

    Author date and committer date are both pinned, and this cost a
    debugging round: `git log --since/--until` filters on the **committer**
    date, `%aI` prints the **author** date, and `git commit --date` sets only
    the author date. A fixture that pins one leaves the other at "now", so
    every commit falls outside the window and the module looks broken while
    the fixture is. `GIT_COMMITTER_DATE` is the other half.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        _git(["init", "-q", "-b", "main"], self.repo)
        _git(["config", "user.email", "fixture@example.invalid"], self.repo)
        _git(["config", "user.name", "Fixture Author"], self.repo)

    def commit(self, *, day, files, subject="a change", author="Fixture Author"):
        for name in files:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{subject}\n{name}\n", encoding="utf-8")
        _git(["add", "-A"], self.repo)
        when = f"{day.isoformat()}T12:00:00+09:00"
        _git(
            [
                "-c",
                f"user.name={author}",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                subject,
                "--date",
                when,
            ],
            self.repo,
            env={"GIT_COMMITTER_DATE": when},
        )

    def read(self, since, until):
        return read_git_activity(since=since, until=until, repo_dir=self.repo)


class TheFilesOfACommitBelongToThatCommitTests(GitTestCase):
    """The measured defect, pinned. Two commits, different file counts.

    A record separator at the end of the format shifts every file list by
    one, which this catches from either direction: the newest commit loses
    its files and the oldest gains somebody else's.
    """

    def test_each_commit_carries_its_own_files(self):
        self.commit(day=date(2026, 8, 10), files=("a.txt",), subject="first")
        self.commit(
            day=date(2026, 8, 11), files=("b.txt", "c/d.txt"), subject="second"
        )

        activity = self.read(date(2026, 8, 1), date(2026, 8, 31))
        by_subject = {c.subject: c for c in activity.commits}

        self.assertEqual(set(by_subject), {"first", "second"})
        self.assertEqual(by_subject["first"].files, ("a.txt",))
        self.assertEqual(sorted(by_subject["second"].files), ["b.txt", "c/d.txt"])

    def test_distinct_files_are_counted_once_across_commits(self):
        """`files_changed` is a set, not a sum. One file touched twice is one
        file that changed, and summing would inflate the only number in this
        dataclass a person reads as a size."""
        self.commit(day=date(2026, 8, 10), files=("a.txt",), subject="first")
        self.commit(day=date(2026, 8, 11), files=("a.txt", "b.txt"), subject="second")

        activity = self.read(date(2026, 8, 1), date(2026, 8, 31))

        self.assertEqual(activity.commit_count, 2)
        self.assertEqual(activity.files_changed, ("a.txt", "b.txt"))


class TheWindowIsInclusiveAndInTheBusinessZoneTests(GitTestCase):
    def setUp(self):
        super().setUp()
        for day in (9, 10, 11):
            self.commit(
                day=date(2026, 8, day), files=(f"f{day}.txt",), subject=f"day {day}"
            )

    def test_both_ends_are_included(self):
        activity = self.read(date(2026, 8, 9), date(2026, 8, 11))

        self.assertEqual(
            sorted(c.subject for c in activity.commits), ["day 10", "day 11", "day 9"]
        )

    def test_a_single_day_window_returns_only_that_day(self):
        """The D+1 case itself. `until` inclusive is the whole point: a
        caller that says "yesterday" means the whole of yesterday, and an
        exclusive bound would drop everything after midnight."""
        activity = self.read(date(2026, 8, 10), date(2026, 8, 10))

        self.assertEqual([c.subject for c in activity.commits], ["day 10"])

    def test_a_window_before_every_commit_is_available_and_empty(self):
        """The distinction the whole dataclass exists for: nothing happened
        is `available=True` with no commits, and is not a failure."""
        activity = self.read(date(2026, 7, 1), date(2026, 7, 31))

        self.assertTrue(activity.available)
        self.assertIsNone(activity.reason)
        self.assertEqual(activity.commits, ())
        self.assertEqual(activity.files_changed, ())


class AFailureIsReportedAndNotRaisedTests(GitTestCase):
    """`read_git_activity()` never raises — see the module docstring. Each
    case below asserts the *reason* is specific enough to act on, because an
    `available=False` with a vague sentence is only marginally better than a
    traceback."""

    def test_a_directory_that_is_not_a_repository(self):
        with tempfile.TemporaryDirectory() as plain:
            activity = read_git_activity(
                since=date(2026, 8, 1), until=date(2026, 8, 2), repo_dir=Path(plain)
            )

        self.assertFalse(activity.available)
        self.assertIn("rev-parse", activity.reason)

    def test_a_directory_that_does_not_exist(self):
        activity = read_git_activity(
            since=date(2026, 8, 1),
            until=date(2026, 8, 2),
            repo_dir=self.repo / "nowhere",
        )

        self.assertFalse(activity.available)
        self.assertTrue(activity.reason)

    def test_an_inverted_window_is_refused_before_git_is_asked(self):
        """Named as itself rather than passed to git, which would answer an
        empty log — indistinguishable from a quiet week."""
        activity = self.read(date(2026, 8, 11), date(2026, 8, 9))

        self.assertFalse(activity.available)
        self.assertIn("inverted", activity.reason)

    def test_a_failure_carries_the_window_and_the_directory(self):
        """So the sentence in the report says *what* could not be read."""
        activity = self.read(date(2026, 8, 11), date(2026, 8, 9))

        self.assertEqual(activity.since, date(2026, 8, 11))
        self.assertEqual(activity.until, date(2026, 8, 9))
        self.assertEqual(activity.repo_dir, str(self.repo))


class TheParserSurvivesTextItDidNotExpectTests(unittest.TestCase):
    """`_parse_records()` is reached with whatever git printed.

    Not reachable from a healthy `git log`, and that is exactly why it must
    not raise: the day the format and the parse stop agreeing is a day the
    D+1 report should still render something.
    """

    def test_empty_output_is_no_commits(self):
        self.assertEqual(_parse_records(""), ())

    def test_a_record_with_too_few_fields_is_skipped_not_guessed(self):
        record = "\x1eonly-a-sha\n\nfile.txt"

        self.assertEqual(_parse_records(record), ())

    def test_a_subject_containing_a_newline_does_not_shift_the_fields(self):
        """The separator is an ASCII control character precisely because a
        subject can contain anything a person typed. A tab or a pipe would
        split here and move every later field by one."""
        raw = (
            "\x1eabc123\x1f2026-08-10T12:00:00+09:00\x1fAuthor\x1fsubject | with | pipes"
            "\n\nsrc/a.py\n"
        )

        commits = _parse_records(raw)

        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0].subject, "subject | with | pipes")
        self.assertEqual(commits[0].files, ("src/a.py",))

    def test_short_sha_is_the_first_eight_characters(self):
        raw = "\x1e" + "f" * 40 + "\x1f2026-08-10T12:00:00+09:00\x1fA\x1fs\n"

        self.assertEqual(_parse_records(raw)[0].short_sha, "f" * 8)


class TheDataclassKeepsItsTwoAnswersApartTests(unittest.TestCase):
    """`available` is the field every consumer must branch on, so the two
    empty-looking states are asserted against each other rather than
    separately."""

    def test_an_unavailable_reading_is_not_an_empty_one(self):
        unread = GitActivity(available=False, reason="git is not installed")
        quiet = GitActivity(available=True, commits=())

        self.assertEqual(unread.commit_count, quiet.commit_count)
        self.assertEqual(unread.files_changed, quiet.files_changed)
        # Identical on every count, and different on the one field that says
        # what the counts mean. A consumer reading only the counts cannot
        # tell them apart, which is why `available` exists.
        self.assertNotEqual(unread.available, quiet.available)
        self.assertIsNotNone(unread.reason)
        self.assertIsNone(quiet.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
