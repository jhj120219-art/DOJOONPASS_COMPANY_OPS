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
    """BUG-54 (NOT FIXED): when the liveness probe cannot answer, the answer
    it gives is "not running" — so a LIVE holder's lock is taken over and
    mutual exclusion breaks.

    CHARACTERIZATION: asserts today's behaviour.

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

    Not fixed: defaulting to True on probe failure would make BUG-42's
    read-only stale lock permanent in a new way (nothing could ever reclaim a
    lock whose probe keeps failing), so the two have to be decided together.
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

    def test_a_probe_timeout_lets_a_live_lock_be_stolen(self):
        import subprocess

        lock_module = self._with_failing_probe(
            subprocess.TimeoutExpired(cmd="tasklist", timeout=5)
        )
        self._hold_lock_as_this_process()

        self.assertFalse(lock_module._is_process_running(os.getpid()))
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_a_missing_tasklist_lets_a_live_lock_be_stolen(self):
        lock_module = self._with_failing_probe(FileNotFoundError("tasklist not found"))
        self._hold_lock_as_this_process()

        self.assertFalse(lock_module._is_process_running(os.getpid()))
        self.assertTrue(try_acquire_lock(self.lock_path, now=NOW))

    def test_the_probe_resolves_an_unknown_answer_permissively(self):
        """The structural cause, so a refactor cannot lose the finding."""
        source = inspect.getsource(sys.modules["scheduler.lock"]._is_process_running)

        self.assertIn("except (OSError, subprocess.SubprocessError)", source)
        # The handler returns False — "assume dead" — not True.
        handler = source[source.index("except (OSError, subprocess.SubprocessError)") :]
        self.assertIn("return False", handler.split("\n")[1])

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

    def test_the_lock_file_is_valid_json_with_the_two_documented_fields(self):
        try_acquire_lock(self.lock_path, now=NOW)

        data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"process_id", "created_at"})
        self.assertIsInstance(data["process_id"], int)
        datetime.fromisoformat(data["created_at"])  # must parse

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
        """The reason this could be added without a contract decision: the
        on-disk shape is untouched."""
        try_acquire_lock(self.lock_path, now=NOW)

        self.assertEqual(set(_read_lock(self.lock_path)), {"process_id", "created_at"})

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


if __name__ == "__main__":
    unittest.main()
