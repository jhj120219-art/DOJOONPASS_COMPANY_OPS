"""Atomic Lock unit tests (docs/07_SCHEDULER_CATCHUP_SPEC.md sections 24-28).

`try_acquire_lock()` was rewritten to acquire through a single atomic
`os.open(..., O_CREAT | O_EXCL)` (CEO-approved: Lock 원자성). The concurrency
behaviour is covered by process-level tests in
test_architecture_invariants.py; what this file covers is the *decision
logic* around that one atomic call, which those tests exercise only
incidentally:

    already held by a live process   -> False, lock untouched
    held by a dead process (stale)   -> taken over (section 27)
    unparseable lock                 -> treated as stale (section 27)
    stale lock that changed underneath us -> not taken over
    a lock path that cannot be removed    -> False, never an exception
    a lock path the OS rejects            -> False, never an exception

Every branch here is reached with real files and real OS errors — no mocks,
consistent with the rest of this suite.
"""

import inspect
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scheduler.lock import (  # noqa: E402
    _read_lock,
    _take_over_stale,
    is_locked,
    lock_held_since,
    release_lock,
    stale_lock_cannot_be_cleared,
    try_acquire_lock,
)

NOW = datetime(2026, 8, 5, 11, 0).astimezone()
DEAD_PID = 999999


class LockTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lock_path = self.root / "locks" / "company_ops.lock"

    def _write_lock(self, payload):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(json.dumps(payload), encoding="utf-8")

    def _stale_payload(self):
        return {"process_id": DEAD_PID, "created_at": "2020-01-01T00:00:00+09:00"}


class AcquireTests(LockTestCase):
    def test_a_fresh_lock_is_acquired_and_records_this_process(self):
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

        data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(data["process_id"], os.getpid())
        self.assertEqual(data["created_at"], NOW.isoformat(timespec="seconds"))

    def test_the_parent_directory_is_created_when_missing(self):
        self.assertFalse(self.lock_path.parent.exists())
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        self.assertTrue(self.lock_path.exists())

    def test_a_lock_held_by_a_live_process_is_denied(self):
        self._write_lock({"process_id": os.getpid(), "created_at": "2026-08-05T10:00:00+09:00"})

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        # The holder's lock must survive the denied attempt untouched.
        data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(data["created_at"], "2026-08-05T10:00:00+09:00")

    def test_a_stale_lock_is_taken_over(self):
        """docs/07 section 27: staleness is decided by whether the recorded
        process is running, never by elapsed time."""
        self._write_lock(self._stale_payload())

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        self.assertEqual(
            json.loads(self.lock_path.read_text(encoding="utf-8"))["process_id"],
            os.getpid(),
        )

    def test_an_unparseable_lock_is_treated_as_stale(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("!!! not json !!!", encoding="utf-8")

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_lock_with_a_non_integer_pid_is_treated_as_stale(self):
        self._write_lock({"process_id": "not-a-pid", "created_at": "2020-01-01T00:00:00+09:00"})

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_lock_with_no_pid_field_is_treated_as_stale(self):
        self._write_lock({"created_at": "2020-01-01T00:00:00+09:00"})

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_acquire_release_acquire_round_trips(self):
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        release_lock(self.lock_path)
        self.assertFalse(self.lock_path.exists())
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_an_unremovable_stale_lock_is_denied_rather_than_raising(self):
        """A path that exists but cannot be replaced must return False, not
        propagate an OSError into the Runner. Here the lock path is a
        directory: `os.open(..., O_EXCL)` refuses it and `os.unlink()` cannot
        remove it."""
        self.lock_path.mkdir(parents=True, exist_ok=True)

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        self.assertTrue(self.lock_path.is_dir())

    def test_a_misconfigured_parent_path_surfaces_instead_of_skipping(self):
        """Characterization, and unchanged by the atomicity rewrite: if the
        directory the lock lives in is itself an existing *file*, the
        `parent.mkdir()` at the top raises before any acquisition is
        attempted.

        Deliberately not converted into a quiet `False`. Returning False
        means "another run holds it", so a broken deployment would look like
        a Runner that skips every single execution with no explanation. A
        raised error names the real problem instead.
        """
        not_a_directory = self.root / "notadir"
        not_a_directory.write_text("x", encoding="utf-8")

        with self.assertRaises(OSError):
            try_acquire_lock(not_a_directory / "company_ops.lock", now=NOW)

    def test_unusual_but_legal_filenames_still_work(self):
        """Windows accepts several names that look invalid (a colon creates an
        alternate data stream, trailing dots are stripped, long names are
        fine here), so they must not be mistaken for a failure path."""
        for name in ("has:colon.lock", "trailing.dot.lock.", "L" * 200 + ".lock"):
            with self.subTest(name=name):
                path = self.root / "locks" / name
                self.assertTrue(try_acquire_lock(path, now=NOW))
                release_lock(path)

    def test_losing_the_stale_takeover_race_is_denied_rather_than_acquired(self):
        """The BUG-19 branch, driven deterministically.

        Two runs can both read the same stale lock and both decide to take it
        over. The loser must come away with False — if it instead retried and
        acquired, both runs would believe they hold the lock and the whole
        point of the lock would be gone.

        The 80-process stress test exercises this path only by chance; here it
        is forced. `_read_lock` is made to report a stale lock on the first
        read (so takeover is attempted) and a *live* one on the second (the
        winner's), which is exactly what the loser observes.
        """
        import scheduler.lock as lock_module

        self._write_lock({"process_id": os.getpid(), "created_at": "2026-08-05T10:00:00+09:00"})
        original = lock_module._read_lock
        reads = {"n": 0}

        def racing_read(path):
            reads["n"] += 1
            if reads["n"] == 1:
                return self._stale_payload()  # looks stale -> takeover attempted
            return original(path)  # by now the winner holds it

        lock_module._read_lock = racing_read
        self.addCleanup(setattr, lock_module, "_read_lock", original)

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        # The winner's lock must be exactly as it was.
        self.assertEqual(
            json.loads(self.lock_path.read_text(encoding="utf-8"))["created_at"],
            "2026-08-05T10:00:00+09:00",
        )

    def test_a_takeover_that_wins_the_race_does_acquire(self):
        """The mirror case, so the test above cannot pass by simply never
        acquiring: when nothing changed underneath, the retry succeeds."""
        self._write_lock(self._stale_payload())

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        self.assertEqual(
            json.loads(self.lock_path.read_text(encoding="utf-8"))["process_id"],
            os.getpid(),
        )

    def test_a_lock_recreated_between_our_create_and_our_write_is_conceded(self):
        """`os.open(O_EXCL)` creating the file is not by itself proof that we
        hold it: a run taking over a stale lock could unlink and recreate it in
        the instant before we write. The post-write confirmation exists for
        that window, and conceding is the only safe answer.
        """
        import scheduler.lock as lock_module

        original = lock_module._read_lock
        lock_module._read_lock = lambda path: {
            "process_id": os.getpid() + 1,
            "created_at": "2026-08-05T11:00:00+09:00",
        }
        self.addCleanup(setattr, lock_module, "_read_lock", original)

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_an_unreadable_confirmation_is_also_conceded(self):
        import scheduler.lock as lock_module

        original = lock_module._read_lock
        lock_module._read_lock = lambda path: None
        self.addCleanup(setattr, lock_module, "_read_lock", original)

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_second_collision_after_a_takeover_gives_up(self):
        """The retry loop runs at most twice. If the lock is still there after
        a takeover, someone else got in first — give up rather than loop."""
        import scheduler.lock as lock_module

        self._write_lock(self._stale_payload())
        original = lock_module._take_over_stale
        # Reports success without removing the file, so the retry collides again.
        lock_module._take_over_stale = lambda path, observed: True
        self.addCleanup(setattr, lock_module, "_take_over_stale", original)

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_failed_write_leaves_no_orphan_lock_behind(self):
        """`os.open(O_EXCL)` has already created the file by the time the write
        runs. If the write then fails, the file must not survive — an empty
        lock nobody holds would block every future run, and no operator would
        know why."""
        import scheduler.lock as lock_module

        original = lock_module.json.dump

        def failing_dump(payload, handle):
            raise OSError("disk full")

        lock_module.json.dump = failing_dump
        self.addCleanup(setattr, lock_module.json, "dump", original)

        with self.assertRaises(OSError):
            try_acquire_lock(self.lock_path, now=NOW)
        self.assertFalse(self.lock_path.exists())

    def test_release_is_idempotent_and_never_raises(self):
        release_lock(self.lock_path)  # never acquired
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        release_lock(self.lock_path)
        release_lock(self.lock_path)  # already gone
        self.assertFalse(self.lock_path.exists())


class ReadOnlyLockTests(LockTestCase):
    """BUG-42 (behaviour NOT FIXED; the silence WAS fixed in C23).

    A read-only stale lock still stops the Runner forever — everything below
    asserts exactly that, unchanged. What is no longer true is the second
    half of the original title, "and nothing anywhere says so":
    `stale_lock_cannot_be_cleared()` now detects the condition and
    `ops_status.py` reports it in ATTENTION
    (`StaleLockCannotBeClearedTests` below, and
    `test_observability.py::LastRunUnclearableLockTests`).

    Detection was the one option that is neither of the two decisions this
    docstring closes with — it strips no attribute and changes no return
    contract.

    CHARACTERIZATION: asserts today's behaviour.

    Taking over a stale lock means `os.unlink()`, and on Windows unlink fails
    with PermissionError on a file carrying the read-only attribute. The
    takeover therefore fails, `try_acquire_lock()` returns False, and it will
    return False on every future run — the recorded process is dead, so no
    other run will ever clear it either.

    Returning False rather than raising is the right call in isolation (an
    earlier test in this file pins that deliberately). What makes this severe
    is what False means downstream: "another run holds the lock". So the
    Runner treats a permanent, unrecoverable condition as routine contention.

    Then the Observability Audit's findings compound it:

        a lock-skipped run writes NO artifact at all (no log, no state)
        run_company_ops.py prints "[SKIPPED]" to stdout, which Task
            Scheduler does not capture by default
        main() returns 0, so Last Run Result reads success (BUG-36)

    The result is a Runner that reports success on schedule, forever, while
    executing nothing. No Event is collected, no Daily History is written, no
    Backup is pushed — and every automatic signal says the system is healthy.

    Reachable without anyone doing anything unusual: files restored from a
    Windows backup commonly come back read-only, and sync clients and
    antivirus tools both set the attribute.

    Not fixed: clearing the attribute before unlink is one line, but deciding
    whether the Runner may forcibly strip attributes from a file it did not
    create is a policy question, and the better fix is probably to distinguish
    "contended" from "cannot proceed" in the return value — which changes
    try_acquire_lock's contract.
    """

    def _make_read_only(self, path):
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(self._restore, path)

    @staticmethod
    def _restore(path):
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass

    def test_a_writable_stale_lock_is_taken_over(self):
        """Baseline — the same lock without the attribute is recoverable."""
        self._write_lock(self._stale_payload())

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_read_only_stale_lock_is_never_taken_over(self):
        self._write_lock(self._stale_payload())
        self._make_read_only(self.lock_path)

        # Not once...
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        # ...and not on any later run either: nothing about the file changed.
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        self.assertTrue(self.lock_path.exists())

    def test_the_holder_pid_is_dead_so_nothing_else_will_clear_it(self):
        """Why it is permanent rather than merely slow: the recorded process
        does not exist, so there is no holder to release it."""
        from scheduler.lock import _is_process_running

        self._write_lock(self._stale_payload())
        self._make_read_only(self.lock_path)

        self.assertFalse(_is_process_running(DEAD_PID))
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_release_lock_cannot_clear_it_either(self):
        """release_lock swallows the error, so even an explicit release leaves
        the file in place."""
        self._write_lock(self._stale_payload())
        self._make_read_only(self.lock_path)

        release_lock(self.lock_path)  # must not raise

        self.assertTrue(self.lock_path.exists())

    def test_clearing_the_attribute_restores_normal_operation(self):
        """Confirms the attribute is the whole cause — useful for the runbook."""
        self._write_lock(self._stale_payload())
        os.chmod(self.lock_path, stat.S_IREAD)
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

        os.chmod(self.lock_path, stat.S_IWRITE)

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))


class ProcessProbeFailureTests(LockTestCase):
    """BUG-54 (FIXED): when the liveness probe cannot answer, it no longer
    answers "not running" — so a LIVE holder's lock is left alone.

    GUARANTEE. This was a characterization until the direction was flipped;
    the measurement it recorded is kept below because it is the argument.

    docs/07 section 27 decides staleness by whether the recorded process is
    running. On Windows that is:

        subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=5)
        ...
        except (OSError, subprocess.SubprocessError):
            return False

    `subprocess.TimeoutExpired` is a `SubprocessError`, so a probe that times
    out returns False — indistinguishable from "that process is gone". False
    means stale, stale means take it over.

    Measured with the probe made to fail while THIS process holds the lock:

        tasklist times out     -> _is_process_running=False, lock TAKEN OVER
        tasklist not found     -> _is_process_running=False, lock TAKEN OVER
        normal (control)       -> _is_process_running=True,  correctly denied

    So two Runners end up in the critical section — the BUG-18/BUG-20
    condition the approved Lock 원자성 work removed. The O_EXCL rewrite made
    ACQUISITION atomic, which stops two runs from both creating the lock; it
    does not help here, because the second run legitimately unlinks the first
    one's lock and creates its own. Measured cost of that condition earlier in
    this Sprint: up to 36% of History Candidates lost.

    Reachability is not theoretical. `tasklist` enumerates every process and
    is given 5 seconds; the moment it is most likely to be slow is heavy
    system load, which is also when two scheduled runs are most likely to
    overlap. A service context with a stripped PATH, or a hardened image
    without tasklist, fails the same way permanently.

    The direction is the whole defect: an UNKNOWN answer is resolved
    permissively ("assume dead, take the lock") rather than conservatively
    ("assume alive, skip this run"). Skipping a run costs one cycle; taking a
    live lock costs Company History.

    **Why this stopped being a decision.** The recorded objection was that
    defaulting to True would make a lock whose probe keeps failing
    unreclaimable, in a new way and just as silently -- so it had to be
    settled together with BUG-42. Two things turned out to be true.

    The coupling to BUG-42 is not real. BUG-42's probe *succeeds* and
    correctly says "dead"; what fails there is the `os.unlink()` of a
    read-only file. This finding is about the probe failing. They are
    different steps, and answering True here leaves BUG-42 exactly as it was.

    The silence is no longer real either, and that is what changed since the
    objection was written. A lock this path holds is reported: `ops_status.py`
    reads `lock_held_since()` -- which answers on this path precisely because
    the holder counts as alive -- and raises LOCK_STUCK_AFTER_HOURS, whose own
    message names this shape ("죽은 Agent의 PID가 재사용돼 Lock이 영구히 잡힌
    것으로 보이는 상태"). The Runner and Agent silence checks raise a run that
    stops happening after SILENT_AFTER_DAYS. Neither check existed when BUG-54
    was recorded.

    And it was never a new policy. `_is_process_running()` already answers
    True for the one "cannot fully tell" case it could see on POSIX --
    `PermissionError` from `os.kill(pid, 0)`, a process that exists and is not
    ours. Windows was the only branch that read "I cannot tell" as "it is
    gone". `test_the_posix_branch_already_answered_this_way` pins that, since
    it is the reason this is a transplant rather than an invention.
    """

    def _hold_lock_as_this_process(self):
        self._write_lock(
            {"process_id": os.getpid(), "created_at": "2026-08-05T10:00:00+09:00"}
        )

    def _with_failing_probe(self, exception):
        """Make only the tasklist call fail, leaving everything else real."""
        import subprocess

        import scheduler.lock as lock_module

        real_run = subprocess.run

        def fake_run(*args, **kwargs):
            if args and isinstance(args[0], list) and args[0][:1] == ["tasklist"]:
                raise exception
            return real_run(*args, **kwargs)

        subprocess.run = fake_run
        self.addCleanup(setattr, subprocess, "run", real_run)
        return lock_module

    def test_a_live_holder_is_normally_respected(self):
        """Control — with the probe working, the lock is denied."""
        self._hold_lock_as_this_process()

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_probe_timeout_does_not_let_a_live_lock_be_stolen(self):
        import subprocess

        lock_module = self._with_failing_probe(
            subprocess.TimeoutExpired(cmd="tasklist", timeout=5)
        )
        self._hold_lock_as_this_process()

        self.assertTrue(lock_module._is_process_running(os.getpid()))
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_missing_tasklist_does_not_let_a_live_lock_be_stolen(self):
        lock_module = self._with_failing_probe(FileNotFoundError("tasklist not found"))
        self._hold_lock_as_this_process()

        self.assertTrue(lock_module._is_process_running(os.getpid()))
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_the_probe_resolves_an_unknown_answer_conservatively(self):
        """The structural half, so a refactor cannot flip the direction back
        without saying so."""
        source = inspect.getsource(sys.modules["scheduler.lock"]._is_process_running)

        self.assertIn("except (OSError, subprocess.SubprocessError)", source)
        handler = source[source.index("except (OSError, subprocess.SubprocessError)") :]
        self.assertIn("return True", handler)
        self.assertNotIn(
            "return False",
            handler[: handler.index("return True")],
            "the unknown-answer handler must not fall out as 'dead'",
        )

    def test_the_posix_branch_already_answered_this_way(self):
        """Why this is a transplant and not an invention: the same function
        already resolved its one other "cannot fully tell" case as alive."""
        source = inspect.getsource(sys.modules["scheduler.lock"]._is_process_running)

        after_permission_error = source[source.index("except PermissionError") :]
        self.assertIn("return True", after_permission_error.split("\n")[1])

    def test_a_lock_held_by_an_unanswerable_probe_is_still_visible(self):
        """The objection this fix had to clear. If a probe keeps failing the
        lock is never reclaimed, so the condition must not be silent --
        `ops_status.py` reads exactly this to raise its
        LOCK_STUCK_AFTER_HOURS line, and it can only answer while the holder
        counts as alive."""
        from scheduler.lock import lock_held_since

        self._with_failing_probe(FileNotFoundError("tasklist not found"))
        self._hold_lock_as_this_process()

        self.assertIsNotNone(lock_held_since(self.lock_path))

    def test_the_probe_is_accurate_when_it_can_run(self):
        """The finding is about the failure path only — the probe itself is
        correct, and locale-independent because it looks for the pid digits
        rather than parsing tasklist's (translated) message."""
        import scheduler.lock as lock_module

        self.assertTrue(lock_module._is_process_running(os.getpid()))
        self.assertFalse(lock_module._is_process_running(DEAD_PID))

    @unittest.skipUnless(sys.platform == "win32", "tasklist is Windows-only")
    def test_the_property_that_makes_it_locale_independent(self):
        """C31: the claim above is right, and narrower than it reads.

        `_is_process_running()` asks `str(pid) in result.stdout`. That is
        substring matching against a **localized** subprocess output, and it
        is only safe while the no-match message contains no digits. If a
        Windows build or a translation ever renders it as, say, "0 tasks
        found", every dead pid would answer *running* — the recorded holder
        would never be judged stale, `try_acquire_lock()` would return False
        forever, and every scheduled run would skip silently. That is BUG-42's
        outcome reached by a different route, and nothing anywhere states the
        assumption it rests on.

        Measured on this machine (UI culture ko-KR), the message really does
        carry no digits:

            정보: 실행 중인 작업 중 지정된 조건에 일치하는 작업이 없습니다.

        Asserted rather than argued, against whatever locale the suite
        actually runs under, so the day it stops being true this fails
        instead of the Runner jamming.
        """
        import subprocess

        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {DEAD_PID}", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            any(character.isdigit() for character in result.stdout),
            "tasklist's no-match message now contains digits; "
            "`_is_process_running()`'s substring test can no longer tell a "
            f"dead pid from a live one: {result.stdout!r}",
        )


class StaleTakeoverTests(LockTestCase):
    """`_take_over_stale()` exists to stop two runs that both judged the same
    lock stale from both removing it — the second would delete the fresh lock
    the first had just created, and both would believe they hold it."""

    def test_a_stale_lock_is_removed_when_unchanged(self):
        payload = self._stale_payload()
        self._write_lock(payload)

        self.assertTrue(_take_over_stale(self.lock_path, payload))
        self.assertFalse(self.lock_path.exists())

    def test_a_lock_that_changed_underneath_is_left_alone(self):
        """The race this guard closes: someone else already took it over."""
        observed = self._stale_payload()
        self._write_lock({"process_id": os.getpid(), "created_at": "2026-08-05T11:00:00+09:00"})

        self.assertFalse(_take_over_stale(self.lock_path, observed))
        # The new holder's lock must still be there.
        self.assertEqual(
            json.loads(self.lock_path.read_text(encoding="utf-8"))["process_id"],
            os.getpid(),
        )

    def test_a_lock_that_cannot_be_removed_reports_failure(self):
        self.lock_path.mkdir(parents=True, exist_ok=True)

        self.assertFalse(_take_over_stale(self.lock_path, None))
        self.assertTrue(self.lock_path.is_dir())

    def test_an_unparseable_lock_matches_a_none_observation(self):
        """`_read_lock()` returns None for unreadable content, so the
        comparison still holds and the takeover proceeds."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("garbage", encoding="utf-8")

        self.assertIsNone(_read_lock(self.lock_path))
        self.assertTrue(_take_over_stale(self.lock_path, None))
        self.assertFalse(self.lock_path.exists())


class SeenStoreRollbackContractTests(unittest.TestCase):
    """`SeenEventStore.unmark_seen()` is deliberately NOT abstract: a store
    that cannot roll back (or a double that has no need to) inherits a no-op
    rather than being forced to implement it — the same choice
    `NotionTransport.search_pages()` makes.

    That default is what `collector/runtime.py` relies on when it calls
    `collector.unmark_seen()` unconditionally on the move-failure path, so it
    needs a test of its own; the two shipped stores both override it.
    """

    def test_the_base_class_default_is_a_silent_no_op(self):
        from collector.seen_store import SeenEventStore

        class MinimalStore(SeenEventStore):
            """Implements only the two abstract methods."""

            def __init__(self):
                self.seen = set()

            def is_seen(self, event_id):
                return event_id in self.seen

            def mark_seen(self, event_id):
                self.seen.add(event_id)

        store = MinimalStore()
        store.mark_seen("E-1")

        # Must not raise, and must leave the store untouched.
        self.assertIsNone(store.unmark_seen("E-1"))
        self.assertTrue(store.is_seen("E-1"))
        self.assertIsNone(store.unmark_seen("never-seen"))

    def test_the_in_memory_store_really_rolls_back(self):
        from collector.seen_store import InMemorySeenEventStore

        store = InMemorySeenEventStore()
        store.mark_seen("E-1")
        store.unmark_seen("E-1")

        self.assertFalse(store.is_seen("E-1"))
        store.unmark_seen("E-1")  # idempotent

    def test_the_persistent_store_rollback_survives_a_reload(self):
        from collector.state import PersistentSeenEventStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "collector_state.json"

        store = PersistentSeenEventStore(state_path=path)
        store.mark_seen("E-1")
        store.mark_seen("E-2")
        store.unmark_seen("E-1")

        reloaded = PersistentSeenEventStore(state_path=path)
        self.assertFalse(reloaded.is_seen("E-1"))
        self.assertTrue(reloaded.is_seen("E-2"))

    def test_rolling_back_an_unknown_id_writes_nothing(self):
        from collector.state import PersistentSeenEventStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "collector_state.json"

        store = PersistentSeenEventStore(state_path=path)
        store.mark_seen("E-1")
        before = path.read_text(encoding="utf-8")

        store.unmark_seen("never-marked")

        self.assertEqual(path.read_text(encoding="utf-8"), before)


class LockFileContractTests(LockTestCase):
    """The on-disk shape other code and operators rely on."""

    #: Every field `try_acquire_lock()` writes. Pinned exactly, so a fourth
    #: cannot appear without somebody deciding it should -- an operator
    #: reads this file by hand and `ops_status.py` parses it.
    #:
    #: `image_name` joined in C138 and is **not** a contract change:
    #: docs/07 section 26 says "Lock에는 **최소한** 다음 정보를 기록할 수
    #: 있다" and lists two, so it states a minimum rather than a schema. What
    #: the third field implements is section 27's own question -- "해당
    #: Process가 실제 실행 중인가?" -- which a bare pid cannot answer once
    #: Windows has reassigned it. See `APidComesBackAsSomebodyElseTests`
    #: for the measurement.
    WRITTEN_FIELDS = {"process_id", "created_at", "image_name"}

    #: The two docs/07 section 26 names. Required rather than merely
    #: allowed: `ops_status.lock_held_since()` reads `created_at` and
    #: `_is_process_running()` reads `process_id`, and a lock missing either
    #: is one neither can act on.
    REQUIRED_FIELDS = {"process_id", "created_at"}

    def test_the_lock_file_is_valid_json_with_the_two_documented_fields(self):
        try_acquire_lock(self.lock_path, now=NOW)

        data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertLessEqual(self.REQUIRED_FIELDS, set(data))
        self.assertEqual(set(data), self.WRITTEN_FIELDS)
        self.assertIsInstance(data["process_id"], int)
        datetime.fromisoformat(data["created_at"])  # must parse

    def test_the_recorded_image_name_is_a_bare_filename(self):
        """It is passed to `tasklist`'s IMAGENAME filter, which matches a
        filename and not a path. Recording `sys.executable` whole would make
        every probe fall through to the pid-only question while looking as
        though it had been narrowed."""
        try_acquire_lock(self.lock_path, now=NOW)

        image = json.loads(self.lock_path.read_text(encoding="utf-8"))["image_name"]
        self.assertNotIn(os.sep, image)
        self.assertEqual(image, os.path.basename(sys.executable))

    def test_no_temporary_files_are_left_behind(self):
        """The previous implementation staged through `tempfile.mkstemp()`;
        the atomic one writes the final path directly."""
        try_acquire_lock(self.lock_path, now=NOW)

        leftovers = [p.name for p in self.lock_path.parent.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_a_denied_acquisition_creates_nothing(self):
        self._write_lock({"process_id": os.getpid(), "created_at": "2026-08-05T10:00:00+09:00"})
        before = sorted(p.name for p in self.lock_path.parent.iterdir())

        try_acquire_lock(self.lock_path, now=NOW)

        self.assertEqual(sorted(p.name for p in self.lock_path.parent.iterdir()), before)


@unittest.skipUnless(sys.platform == "win32", "extended-length path prefix is Windows-only")
class LongPathHelperTests(unittest.TestCase):
    """`_long_path()` is what makes `test_unusual_but_legal_filenames_still_work`
    pass a 200+ character name — this pins its two branches directly,
    including the UNC one no lock path in this project actually takes
    (runtime/locks is always local), so it stays covered on its own."""

    def test_a_local_path_gets_the_bare_extended_prefix(self):
        from scheduler.lock import _long_path

        result = _long_path(Path(r"C:\some\lock\dir\company_ops.lock"))

        self.assertTrue(str(result).startswith("\\\\?\\"))
        self.assertNotIn("UNC", str(result))

    def test_a_unc_path_gets_the_unc_extended_prefix(self):
        from scheduler.lock import _long_path

        result = _long_path(Path(r"\\fakeserver\fakeshare\locks\company_ops.lock"))

        self.assertTrue(str(result).startswith("\\\\?\\UNC\\"))
        self.assertIn("fakeserver", str(result))
        self.assertIn("fakeshare", str(result))

    def test_an_already_extended_path_still_comes_out_extended(self):
        """An input that already carries the `\\\\?\\` prefix is returned
        unchanged — exactly one prefix, and never the UNC form.

        This test previously asserted the same expectation but passed for
        the wrong reason on Python < 3.11, where `Path.resolve()` stripped
        the prefix so the plain branch re-added it. On 3.13 `resolve()`
        preserves it; the still-`\\\\`-leading text then took the UNC branch
        and produced `\\\\?\\UNC\\?\\C:\\...`, a path no Windows API accepts.
        Every lock operation on it raises OSError, which `try_acquire_lock()`
        reads as "another Runner holds it" — so a Runner configured with an
        already-prefixed lock path would skip every run forever.
        """
        from scheduler.lock import _long_path

        already = Path("\\\\?\\C:\\some\\lock\\dir\\company_ops.lock")

        result = _long_path(already)

        self.assertEqual(str(result), "\\\\?\\C:\\some\\lock\\dir\\company_ops.lock")
        self.assertNotIn("UNC", str(result))

    @unittest.skipUnless(sys.platform == "win32", "extended-length paths are Windows-only")
    def test_a_lock_at_an_already_extended_path_can_actually_be_acquired(self):
        """The operational consequence of the branch above, end to end: a
        Runner handed an already-prefixed lock path must be able to take,
        hold, and release the lock like any other. Before the fix this
        returned False on the very first attempt — indistinguishable from
        "another Runner is running", so the real Runner never ran at all.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        lock_path = Path("\\\\?\\" + str(Path(tmp.name).resolve() / "company_ops.lock"))
        now = datetime(2026, 8, 10, 11, 0)

        self.assertTrue(try_acquire_lock(lock_path, now=now))
        try:
            self.assertFalse(try_acquire_lock(lock_path, now=now))
        finally:
            release_lock(lock_path)
        self.assertTrue(try_acquire_lock(lock_path, now=now))
        release_lock(lock_path)


class IsLockedTests(LockTestCase):
    """`is_locked()` — read-only "is a Runner working right now?".

    Exists because the only way to ask before was `try_acquire_lock()`,
    which *competes*: it creates the lock when free and can take over one it
    judges stale. `ops_status.py` promises an operator it "아무것도 쓰지 않고
    lock도 잡지 않는다", so it could not use that, and therefore could not
    tell a real orphaned Event from one whose History Candidate turn has
    simply not come yet during a large catch-up.
    """

    def test_no_lock_file_is_not_locked(self):
        self.assertFalse(is_locked(self.lock_path))

    def test_a_lock_held_by_this_live_process_is_locked(self):
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

        self.assertTrue(is_locked(self.lock_path))

    def test_a_lock_from_a_dead_process_is_not_locked(self):
        """Same judgement `try_acquire_lock()` makes under §27 — a lock whose
        recorded process is gone is not held — reached without acting on
        it."""
        self._write_lock(self._stale_payload())

        self.assertFalse(is_locked(self.lock_path))

    def test_an_unparseable_lock_is_not_locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("{not json", encoding="utf-8")

        self.assertFalse(is_locked(self.lock_path))

    def test_asking_neither_creates_nor_removes_the_lock(self):
        """The property that makes this safe to call from a status view: a
        Runner mid-run must not be disturbed by someone checking on it."""
        self.assertFalse(self.lock_path.exists())
        is_locked(self.lock_path)
        self.assertFalse(self.lock_path.exists())

        try_acquire_lock(self.lock_path, now=NOW)
        before = self.lock_path.read_bytes()

        is_locked(self.lock_path)

        self.assertTrue(self.lock_path.exists())
        self.assertEqual(self.lock_path.read_bytes(), before)

    def test_a_stale_lock_is_reported_without_being_taken_over(self):
        """`try_acquire_lock()` would delete this one. This must not."""
        self._write_lock(self._stale_payload())

        self.assertFalse(is_locked(self.lock_path))

        self.assertTrue(self.lock_path.exists())
        self.assertEqual(_read_lock(self.lock_path), self._stale_payload())


class LockHeldSinceTests(LockTestCase):
    """`lock_held_since()` — when the live holder took the lock.

    Detection for a failure the lock logic itself cannot fix without a
    contract change: `_is_process_running()` verifies that *a* process has
    the recorded pid, never that it is the one that wrote the lock. A Runner
    killed by a power cut leaves its pid behind, and once the OS reassigns
    that number every later run is denied the lock and skips — silently and
    permanently, since §27 forbids judging staleness by elapsed time alone.

    Reads only the `created_at` field the lock file has always carried, so
    `LockFileContractTests`' pinned on-disk shape is unchanged.
    """

    def test_no_lock_means_no_time(self):
        self.assertIsNone(lock_held_since(self.lock_path))

    def test_a_live_lock_reports_when_it_was_acquired(self):
        try_acquire_lock(self.lock_path, now=NOW)

        self.assertEqual(lock_held_since(self.lock_path), NOW)

    def test_a_dead_holder_reports_nothing(self):
        """Consistent with `is_locked()`: a lock nobody holds has no holding
        time, and reporting one would put a permanent entry in ATTENTION for
        a lock the next Runner will simply take over."""
        self._write_lock(self._stale_payload())

        self.assertIsNone(lock_held_since(self.lock_path))

    def test_an_unparseable_created_at_reports_nothing(self):
        self._write_lock({"process_id": os.getpid(), "created_at": "not-a-time"})

        self.assertIsNone(lock_held_since(self.lock_path))

    def test_a_missing_created_at_reports_nothing(self):
        self._write_lock({"process_id": os.getpid()})

        self.assertIsNone(lock_held_since(self.lock_path))

    def test_it_reads_no_field_the_lock_file_does_not_already_have(self):
        """The reason this could be added without a contract decision: it
        needs nothing that was not already being written.

        **This used to assert the lock file's whole shape**, which made it a
        second copy of `LockFileContractTests`'s pin -- and the copy failed
        when C138 added `image_name` for an unrelated reason, in a class
        whose subject is `lock_held_since()`. Two tests failing for one
        change is how a pin drifts into a nuisance. The shape belongs to the
        contract class; what belongs here is that this function reads only
        `created_at`, which is asserted directly.
        """
        self._write_lock(
            {"process_id": os.getpid(), "created_at": "2026-08-05T10:00:00+09:00"}
        )

        self.assertIsNotNone(lock_held_since(self.lock_path))

    def test_asking_changes_nothing(self):
        try_acquire_lock(self.lock_path, now=NOW)
        before = self.lock_path.read_bytes()

        lock_held_since(self.lock_path)

        self.assertEqual(self.lock_path.read_bytes(), before)


class StaleLockCannotBeClearedTests(LockTestCase):
    """`stale_lock_cannot_be_cleared()` — the BUG-42 blind spot, detected.

    A stale lock carrying the read-only attribute is the worst-shaped
    failure this system has: `try_acquire_lock()` answers False, and False
    downstream means "another run holds it", so the Runner treats a
    permanent condition as routine contention and skips forever. A
    lock-skipped run writes no manifest (docs/14 §7, deliberately), so
    nothing else is left to notice.

    Measured, and this is why a third detector was needed: **both existing
    ones are blind to it.** `is_locked()` returns False and
    `lock_held_since()` returns None, because each keys on a *live* process
    and this lock's process is dead. Everything C19 added for stuck locks
    looks straight past it.

    The condition detected here is narrow and permanent by construction —
    dead process, unwritable file — so it is never noise. Neither of BUG-42's
    two candidate fixes is taken: nothing strips an attribute, and
    `try_acquire_lock()`'s contract is untouched.
    """

    def _read_only(self, path):
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(self._restore_writable, path)

    @staticmethod
    def _restore_writable(path):
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass

    def test_no_lock_file_is_not_reported(self):
        self.assertFalse(stale_lock_cannot_be_cleared(self.lock_path))

    def test_an_ordinary_stale_lock_is_not_reported(self):
        """The next run takes this one over — that is §27 working, and
        reporting it would be noise on every crash recovery."""
        self._write_lock(self._stale_payload())

        self.assertFalse(stale_lock_cannot_be_cleared(self.lock_path))

    def test_a_lock_held_by_a_live_process_is_not_reported(self):
        try_acquire_lock(self.lock_path, now=NOW)

        self.assertFalse(stale_lock_cannot_be_cleared(self.lock_path))

    def test_a_read_only_stale_lock_is_reported(self):
        self._write_lock(self._stale_payload())
        self._read_only(self.lock_path)

        self.assertTrue(stale_lock_cannot_be_cleared(self.lock_path))

    def test_the_condition_is_exactly_the_one_that_blocks_takeover(self):
        """Writability is the whole difference: the same stale lock is taken
        over when it is writable and never when it is not."""
        self._write_lock(self._stale_payload())
        self._read_only(self.lock_path)

        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))
        self.assertTrue(self.lock_path.exists())

        self._restore_writable(self.lock_path)

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        self.assertFalse(stale_lock_cannot_be_cleared(self.lock_path))

    def test_the_two_existing_detectors_really_are_blind_to_it(self):
        """Pinned so nobody concludes the older detectors already cover this
        and deletes the new one."""
        self._write_lock(self._stale_payload())
        self._read_only(self.lock_path)

        self.assertFalse(is_locked(self.lock_path))
        self.assertIsNone(lock_held_since(self.lock_path))
        self.assertTrue(stale_lock_cannot_be_cleared(self.lock_path))

    def test_an_unparseable_read_only_lock_is_reported(self):
        """`_read_lock()` returns None for junk, which §27 already treats as
        stale — so an unremovable one is the same permanent condition."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("{not json", encoding="utf-8")
        self._read_only(self.lock_path)

        self.assertTrue(stale_lock_cannot_be_cleared(self.lock_path))

    def test_asking_neither_removes_the_lock_nor_changes_its_attributes(self):
        """Detection only — the restraint every other read-only helper here
        keeps."""
        self._write_lock(self._stale_payload())
        self._read_only(self.lock_path)
        before = self.lock_path.read_bytes()
        writable_before = os.access(self.lock_path, os.W_OK)

        stale_lock_cannot_be_cleared(self.lock_path)

        self.assertTrue(self.lock_path.exists())
        self.assertEqual(self.lock_path.read_bytes(), before)
        self.assertEqual(os.access(self.lock_path, os.W_OK), writable_before)


class APidComesBackAsSomebodyElseTests(LockTestCase):
    """BUG: a lock left behind by a killed Runner wedges the next one forever.

    `_is_process_running()` asked one question -- "is anything running with
    this pid" -- and a pid is not a durable name for a process. Windows
    reassigns them, low ones immediately after a reboot, and the lock file
    outlives the process by construction: `ExecutionTimeLimit` expiring
    (docs/07 section 55 registers one), a power cut, a reset. All three leave
    the file with its pid still in it.

    Measured on this machine before the fix, against a pid the OS had since
    handed to `svchost.exe`:

        lock:  {"process_id": 1336, "created_at": "2020-01-01T00:00:00+09:00"}
        _is_process_running(1336)  ->  True
        try_acquire_lock(...)      ->  False

    False from then on, every run, silently -- the Runner skips every trigger
    and only a person deleting the file undoes it. The `created_at` is five
    years old and nothing consults it, deliberately: section 27 forbids
    judging staleness by elapsed time, precisely so a slow run is never
    killed.

    The fix records the holder's executable beside its pid and asks about
    both. It can only turn "running" into "not running", and only when the
    pid belongs to a process running a different executable -- which cannot
    be the holder that wrote the lock. So the mutual exclusion this whole
    module exists for is untouched, which is the property the tests below
    spend most of their time on.
    """

    OTHER_IMAGE = "svchost.exe"

    def _a_live_pid_running_something_else(self):
        """A real pid on this machine belonging to a process that is not
        this interpreter. Read from the OS rather than invented: the whole
        defect is about what the OS reports, and a made-up pid would be
        answering the question with the fixture."""
        if sys.platform != "win32":
            self.skipTest("pid reuse is probed through tasklist, which is Windows-only")
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {self.OTHER_IMAGE}", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        self.skipTest(f"no {self.OTHER_IMAGE} process to borrow a pid from")

    def test_a_pid_now_owned_by_another_program_is_not_our_holder(self):
        pid = self._a_live_pid_running_something_else()
        from scheduler.lock import _is_process_running

        self.assertTrue(
            _is_process_running(pid),
            "the borrowed pid is not actually live; the test proves nothing",
        )
        self.assertFalse(_is_process_running(pid, "python.exe"))

    def test_the_wedged_lock_is_reclaimed(self):
        """The end-to-end shape of the defect: the next Runner acquires."""
        pid = self._a_live_pid_running_something_else()
        self._write_lock(
            {
                "process_id": pid,
                "created_at": "2020-01-01T00:00:00+09:00",
                "image_name": "python.exe",
            }
        )

        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        self.assertEqual(
            json.loads(self.lock_path.read_text(encoding="utf-8"))["process_id"],
            os.getpid(),
        )

    # ------------------------------------- every probe of "held" must agree

    def test_no_reader_disagrees_with_the_acquirer_about_one_lock_file(self):
        """The half of BUG-54 that was missed, stated as the property.

        `try_acquire_lock()`, `lock_held_since()` and
        `stale_lock_cannot_be_cleared()` were all given the image name;
        `is_locked()` was not, and it kept asking about the pid alone.
        Measured on one lock file before this was closed:

            lock_held_since  -> None    nobody holds it
            try_acquire_lock -> True    took it over: the holder is dead
            is_locked        -> True    **a live process is holding it**

        Asserted as agreement rather than as `is_locked() is False`, because
        a fourth reader added later has the same way of going wrong.
        """
        from scheduler.lock import is_locked, lock_held_since

        pid = self._a_live_pid_running_something_else()
        self._write_lock(
            {
                "process_id": pid,
                "created_at": "2020-01-01T00:00:00+09:00",
                "image_name": "python.exe",
            }
        )

        self.assertFalse(is_locked(self.lock_path))
        self.assertIsNone(lock_held_since(self.lock_path))
        # Last: it takes the lock over, which changes the file.
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_reused_pid_no_longer_excuses_an_alarm_about_lost_work(self):
        """What the disagreement actually cost. `ops_status.py` calls
        `is_locked()` only to append "(Runner 실행 중 — 완료 후 재확인 권장)"
        to two ATTENTION lines about work that may be gone, and the comment
        at one of them says the stake: "a real loss hidden behind 'probably
        just running' is far worse than a false alarm". A stale lock holding
        a reused pid attached that reassurance permanently, because nothing
        clears such a lock on its own."""
        from scheduler.lock import is_locked

        pid = self._a_live_pid_running_something_else()
        self._write_lock(
            {
                "process_id": pid,
                "created_at": "2020-01-01T00:00:00+09:00",
                "image_name": "python.exe",
            }
        )
        self.assertFalse(is_locked(self.lock_path))

    # --------------------------------------------- and nothing else changed

    def test_a_live_holders_lock_still_reads_as_held(self):
        """The direction guarantee for the reader, matching
        `test_a_live_holders_lock_is_still_refused` for the acquirer: this
        change may only turn True into False, and never for a real holder.
        An `is_locked()` that went False on a running Runner would tell an
        operator the lock is free while the Runner works."""
        from scheduler.lock import is_locked

        self._write_lock(
            {
                "process_id": os.getpid(),
                "created_at": "2026-08-05T10:00:00+09:00",
                "image_name": os.path.basename(sys.executable),
            }
        )
        self.assertTrue(is_locked(self.lock_path))

    def test_a_reader_of_a_lock_without_an_image_behaves_as_before(self):
        """Deployed machines hold such locks right now."""
        from scheduler.lock import is_locked

        self._write_lock(
            {"process_id": os.getpid(), "created_at": "2026-08-05T10:00:00+09:00"}
        )
        self.assertTrue(is_locked(self.lock_path))

    def test_a_live_holder_is_still_a_live_holder(self):
        """The property the whole module exists for. A narrower probe that
        reported a running Runner as stale would give two Runners at once --
        BUG-18/BUG-20, measured at up to 36% of History Candidates lost."""
        from scheduler.lock import _is_process_running

        self.assertTrue(
            _is_process_running(os.getpid(), os.path.basename(sys.executable))
        )

    def test_a_live_holders_lock_is_still_refused(self):
        self._write_lock(
            {
                "process_id": os.getpid(),
                "created_at": "2026-08-05T10:00:00+09:00",
                "image_name": os.path.basename(sys.executable),
            }
        )
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_lock_written_before_this_field_existed_behaves_as_before(self):
        """Deployed machines have such locks on disk right now. Falling back
        to the pid alone is the old behaviour exactly, not a degraded one."""
        from scheduler.lock import _is_process_running

        self.assertTrue(_is_process_running(os.getpid(), None))
        self._write_lock(
            {"process_id": os.getpid(), "created_at": "2026-08-05T10:00:00+09:00"}
        )
        self.assertFalse(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_dead_pid_is_still_stale_with_or_without_an_image(self):
        from scheduler.lock import _is_process_running

        for image in (None, "python.exe", "svchost.exe"):
            with self.subTest(image=image):
                self.assertFalse(_is_process_running(DEAD_PID, image))

    # ------------------------------------------------ what gets written down

    def test_the_holder_records_the_executable_it_is_running(self):
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))
        payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["image_name"], os.path.basename(sys.executable))

    def test_an_unusable_image_name_is_ignored_rather_than_asked_about(self):
        """A lock file is a file: hand-edited, restored, truncated, or
        written by a future version. A value the filter cannot use must fall
        back to the pid-only question, not become part of a `tasklist`
        invocation whose meaning nobody can predict."""
        from scheduler.lock import _is_process_running

        for junk in ("", "a b.exe", "..\\\\..\\\\x.exe", "x" * 500, 7, None, ["python.exe"]):
            with self.subTest(image=junk):
                self.assertTrue(
                    _is_process_running(os.getpid(), junk),
                    "an unusable image name must not make a live holder look dead",
                )

    def test_the_probe_still_answers_running_when_it_cannot_tell(self):
        """BUG-54's rule, unchanged by the extra filter: a probe that fails
        means "I do not know", and the safe reading of that is "held".
        Skipping a run costs one cycle; taking a live holder's lock costs
        Company History."""
        import scheduler.lock as lock_module

        def refusing(*args, **kwargs):
            raise FileNotFoundError(2, "tasklist not found")

        original = lock_module.subprocess.run
        lock_module.subprocess.run = refusing
        try:
            self.assertTrue(lock_module._is_process_running(DEAD_PID, "python.exe"))
        finally:
            lock_module.subprocess.run = original

    @unittest.skipUnless(sys.platform == "win32", "tasklist is Windows-only")
    def test_the_no_match_message_still_carries_no_digits(self):
        """The same locale property `test_the_property_that_makes_it_locale
        _independent` pins for the one-filter form, restated for the two-
        filter form because it is a *different* message path: this one is
        reached when the pid exists and the image does not match.

        Measured on this machine (ko-KR):

            정보: 실행 중인 작업 중 지정된 조건에 일치하는 작업이 없습니다.

        If that ever renders with a digit in it, `str(pid) in stdout` starts
        answering "running" for every mismatched image and this fix quietly
        stops working.
        """
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {os.getpid()}",
             "/FI", "IMAGENAME eq definitely-not-a-real-image.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            any(character.isdigit() for character in result.stdout),
            f"tasklist's no-match message now contains digits: {result.stdout!r}",
        )


if __name__ == "__main__":
    unittest.main()
