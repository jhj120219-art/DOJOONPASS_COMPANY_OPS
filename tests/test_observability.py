"""Observability tests: agent/status.py and app/desktop_activity.py.

Both are read-only views over data the system already writes. What matters
about them, and what is pinned here:

    they never write, move, lock, or delete anything
    they still answer when the underlying data is damaged
    a Desktop that has reported nothing is REPORTED, not omitted
    "needs attention" fires on real trouble and stays quiet otherwise
"""

import contextlib
import io
import json
import os
import stat
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# The repository root as well, for the same reason `test_notion_dashboard.py`
# does it: ten tests in this file import `ops_status`, which lives beside
# `src/` rather than in it. Under pytest the rootdir is on `sys.path` and the
# omission never showed; run directly, those ten raised ModuleNotFoundError.
# They were invisible either way until the stray `unittest.main()` above them
# was moved to the end of the file — it had been cutting the direct run off
# at 44 of 411 tests and printing OK (C38).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import AgentState, save_state  # noqa: E402
from agent.status import read_status  # noqa: E402
from app.desktop_activity import read_company_activity  # noqa: E402
from app.runner import PIPELINE_COMPONENTS  # noqa: E402
from agent import find_secret_material  # noqa: E402
from oplog import one_line  # noqa: E402
from events import create_event  # noqa: E402
from events import SOURCES, create_event  # noqa: E402
from runsummary import (  # noqa: E402
    ComponentResult,
    ComponentStatus,
    Failure,
    Retryability,
    RunSummary,
    Severity,
    read_summary,
    write_summary,
)

NOW = datetime(2026, 8, 10, 9, 0).astimezone()


def _healthy_backup_state(state_dir, *, when=None):
    """Write the `backup_state.json` a machine that has backed up would have.

    Fixtures that create Company History and no backup state describe a
    machine on which the Backup step has never run. That is not a neutral
    omission — Backup is part of the same pipeline that writes the history
    and records state on failure as well as success, so the combination
    cannot be produced by any run, and `ops_status.py` now reports it (the
    files exist on one machine only). Two "needs no attention" fixtures were
    written that way and this is what they were missing.
    """
    # Defaults to real now, not `NOW`. The history files these fixtures
    # create carry real mtimes, and the fact being represented is "the backup
    # happened after the history was written" — ordering two real-time values
    # against each other. Anchoring one of them to the pinned clock and the
    # other to the wall clock is the same trap `LastRunViewTests` hit.
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "backup_state.json").write_text(
        json.dumps(
            {
                # Full precision, exactly as `backup/state.py` writes it
                # (`.isoformat()`, no timespec). Truncating to seconds here
                # put the backup *before* a file written in the same second
                # and reproduced the alarm this fixture is asserting is
                # absent — production keeps microseconds and has no such
                # window.
                "last_successful_backup": (
                    when or datetime.now().astimezone()
                ).isoformat(),
                "last_backup_commit": "0" * 40,
                "backup_status": "BACKUP_SUCCESS",
            }
        ),
        encoding="utf-8",
    )


class AgentStatusTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state_path = self.root / "state" / "agent_state.json"
        self.outbox = self.root / "outbox"
        self.sent = self.root / "sent"
        self.signals = self.root / "signals"
        self.rejected = self.root / "signals_rejected"

    def status(self, *, start_date=date(2026, 8, 1), now=NOW):
        return read_status(
            agent_start_date=start_date,
            now=now,
            state_path=self.state_path,
            outbox_dir=self.outbox,
            sent_dir=self.sent,
            rejected_signals_dir=self.rejected,
        )

    def touch(self, directory: Path, name: str):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text("{}", encoding="utf-8")


class TheStatusViewAnswersWhenTheDiskRefusesTests(unittest.TestCase):
    """`ops_status.py` has forty-one exception handlers and a dozen of them
    had never run.

    Every one is the same promise, written out in a dozen docstrings: *this
    is a diagnostic; it must still produce an answer when part of the
    evidence is damaged.* The promise is load-bearing in a way that is easy
    to under-rate — this is the tool an operator opens **because** something
    already looks wrong, so the run where a handler is missing is exactly the
    run where it is needed, and the failure is a traceback instead of a
    report.

    Covering each arm separately would be a dozen micro-tests about
    `os.scandir`. The property they share is one sentence, so it is driven as
    one: for each filesystem primitive the view uses, make it fail for
    **every path under the runtime tree** and assert the whole report still
    renders and still exits sanely.

    Scoped to the runtime tree rather than patched globally, for two reasons.
    It is the realistic fault — a permission change or a mount going away
    under `runtime/`, not `pathlib` breaking — and a global patch takes
    pytest's own imports down with it, which would make this test about the
    harness instead of about the subject.
    """

    #: Every block `main()` prints, in the order it prints them. Named so a
    #: block added later is covered by this sweep without anybody editing it
    #: — the loop asks the module, and `test_the_sweep_covers_every_block`
    #: checks the list against what `main()` actually calls.
    BLOCKS = (
        "_print_company",
        "_print_history",
        "_print_control_tower",
        "_print_last_run",
        "_print_notion",
        "_print_agent",
    )

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name) / "runtime"
        # A tree with something real in every corner, so the sweep is
        # breaking reads that would otherwise have succeeded. Faults over an
        # empty tree prove nothing: the early `is_dir()` guards return first
        # and no handler is reached.
        for relative in (
            "events/processed", "events/transport", "events/incoming",
            "events/rejected", "local_master/daily", "local_master/monthly",
            "history_candidates/keep", "history_candidates/review",
            "state", "logs", "runs", "agent/state", "agent/logs",
        ):
            (self.runtime / relative).mkdir(parents=True, exist_ok=True)

        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="PAY",
            event_type="BLOCKED", status="BLOCKED", blocker="vendor key",
            summary="blocked on the vendor", history_candidate=True,
            event_id="EVT-1", timestamp="2026-08-05T10:00:00+09:00",
        )
        (self.runtime / "events" / "processed" / "EVT-1.json").write_text(
            event.to_json(), encoding="utf-8"
        )
        (self.runtime / "local_master" / "daily" / "2026-08-05.md").write_text(
            "# 2026-08-05\n\n- EVT-1 blocked on the vendor\n", encoding="utf-8"
        )
        (self.runtime / "local_master" / "monthly" / "2026-08.md").write_text(
            "# 2026-08\n", encoding="utf-8"
        )
        (self.runtime / "runs" / "last_run.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": "RUN-1",
                    "started_at": "2026-08-05T10:00:00+09:00",
                    "finished_at": "2026-08-05T10:00:05+09:00",
                    "components": [{"name": "transport", "status": "SUCCESS"}],
                }
            ),
            encoding="utf-8",
        )
        (self.runtime / "state" / "notion_retry_queue.json").write_text(
            json.dumps({"entries": []}), encoding="utf-8"
        )
        (self.runtime / "agent" / "state" / "agent_state.json").write_text(
            json.dumps({"desktop_id": "DESKTOP_4", "last_run": None}), encoding="utf-8"
        )

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_faults", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        # A separate knob on purpose: `_agent_dir()`'s docstring records that
        # the manifest path "belongs to `app.runner`, which decides where the
        # manifest lives", so rebinding `RUNTIME_DIR` deliberately does not
        # move it. Without this line the LAST RUN block reads the real
        # repository's manifest and the sweep silently tests nothing there.
        module.DEFAULT_RUN_SUMMARY_PATH = self.runtime / "runs" / "last_run.json"
        return module

    def _under_runtime(self, target) -> bool:
        try:
            Path(target).resolve().relative_to(self.runtime.resolve())
        except (ValueError, OSError, TypeError):
            return False
        return True

    @contextlib.contextmanager
    def _refusing(self, *primitives):
        """Make each named primitive raise `OSError` for runtime-tree paths.

        `PermissionError` rather than a bare `OSError`: it is an `OSError`
        subclass, so a handler written for the base class catches it, and one
        written for something narrower does not — which is the mistake worth
        catching.
        """
        import os as os_module

        undo = []
        if "scandir" in primitives:
            real = os_module.scandir

            def fake_scandir(path=".", *args, **kwargs):
                if self._under_runtime(path):
                    raise PermissionError(f"refused: {path}")
                return real(path, *args, **kwargs)

            os_module.scandir = fake_scandir
            undo.append(lambda: setattr(os_module, "scandir", real))
        if "iterdir" in primitives:
            real_iterdir = Path.iterdir

            def fake_iterdir(self_path):
                if self._under_runtime(self_path):
                    raise PermissionError(f"refused: {self_path}")
                return real_iterdir(self_path)

            Path.iterdir = fake_iterdir
            undo.append(lambda: setattr(Path, "iterdir", real_iterdir))
        if "stat" in primitives:
            real_stat = Path.stat

            def fake_stat(self_path, *args, **kwargs):
                if self._under_runtime(self_path):
                    raise PermissionError(f"refused: {self_path}")
                return real_stat(self_path, *args, **kwargs)

            Path.stat = fake_stat
            undo.append(lambda: setattr(Path, "stat", real_stat))
        if "read_text" in primitives:
            real_read = Path.read_text

            def fake_read(self_path, *args, **kwargs):
                if self._under_runtime(self_path):
                    raise PermissionError(f"refused: {self_path}")
                return real_read(self_path, *args, **kwargs)

            Path.read_text = fake_read
            undo.append(lambda: setattr(Path, "read_text", real_read))
        try:
            yield
        finally:
            for restore in reversed(undo):
                restore()

    def _run_blocks(self, module):
        """Through `_block()`, which is where the guarantee lives.

        Calling the `_print_*` functions directly would test something else:
        each of them is allowed to raise on a refused disk (they contain 36
        unguarded `is_dir()` / `is_file()` predicates and `Path.is_dir()`
        re-raises `EACCES`). What `main()` promises is that the *report*
        survives, one section at a time.
        """
        labels = {
            "_print_company": "COMPANY",
            "_print_history": "HISTORY",
            "_print_control_tower": "CONTROL TOWER",
            "_print_last_run": "LAST RUN",
            "_print_notion": "NOTION",
            "_print_agent": "AGENT",
        }
        buffer = io.StringIO()
        attention = []
        with contextlib.redirect_stdout(buffer):
            for name in self.BLOCKS:
                attention.extend(
                    module._block(labels[name], getattr(module, name), NOW)
                )
        return buffer.getvalue(), attention

    # ------------------------------------------------------------ tests
    def test_the_sweep_covers_every_block_main_prints(self):
        """Guard against the list going stale. A block added to `main()` and
        not here would be swept by nothing and nobody would see it."""
        import inspect

        module = self._module()
        source = inspect.getsource(module.main)
        called = {
            name
            for name in dir(module)
            if name.startswith("_print_") and f", {name})" in source
        }

        self.assertEqual(called, set(self.BLOCKS))

    def test_the_healthy_tree_really_produces_a_report(self):
        """Control. Faults over a tree that produces nothing would prove
        nothing — the early `is_dir()` guards would return before any
        handler ran."""
        printed, _ = self._run_blocks(self._module())

        for heading in ("COMPANY", "HISTORY", "CONTROL TOWER", "LAST RUN"):
            with self.subTest(heading=heading):
                self.assertIn(heading, printed)
        self.assertIn("EVT-1", printed)

    def test_every_block_survives_each_primitive_refusing(self):
        for primitive in ("scandir", "iterdir", "stat", "read_text"):
            with self.subTest(primitive=primitive):
                module = self._module()
                with self._refusing(primitive):
                    printed, attention = self._run_blocks(module)
                self.assertIn("COMPANY", printed)
                self.assertIn("CONTROL TOWER", printed)
                self.assertIsInstance(attention, list)

    def test_every_block_survives_all_of_them_refusing_at_once(self):
        """The worst case an operator meets: the runtime directory is there
        and nothing under it can be read."""
        module = self._module()

        with self._refusing("scandir", "iterdir", "stat", "read_text"):
            printed, attention = self._run_blocks(module)

        for heading in ("COMPANY", "HISTORY", "CONTROL TOWER", "LAST RUN"):
            with self.subTest(heading=heading):
                self.assertIn(heading, printed)
        self.assertIsInstance(attention, list)

    def test_main_still_exits_with_a_code_rather_than_a_traceback(self):
        """The whole entry point, not just the blocks. An operator runs
        `python ops_status.py`; an uncaught `PermissionError` there is exit
        1 with a stack trace where a report belongs."""
        module = self._module()
        buffer = io.StringIO()

        with self._refusing("scandir", "iterdir", "stat", "read_text"):
            with contextlib.redirect_stdout(buffer):
                code = module.main(())

        self.assertNotEqual(code, 0, "a report missing every block is not a pass")
        self.assertIn("DOJOONPASS Company Ops", buffer.getvalue())
        self.assertIn("ATTENTION", buffer.getvalue())

    def test_a_refused_block_says_so_instead_of_reporting_zero(self):
        """The direction that matters more than not crashing.

        Making each predicate return `False` on a refusal — the obvious fix
        at the 36 call sites — would turn "I could not read this" into
        "there is nothing here": a partial report presented as complete,
        which is the silent-loss shape this project keeps removing. The block
        says it failed instead, and the Event on disk is neither counted nor
        quietly dropped.
        """
        module = self._module()

        with self._refusing("scandir", "iterdir", "stat", "read_text"):
            printed, attention = self._run_blocks(module)

        self.assertIn("읽지 못했다", printed)
        self.assertTrue(
            any("읽지 못했다" in item for item in attention),
            "an unreadable section must reach ATTENTION — otherwise a report "
            "missing a whole block exits 0",
        )
        self.assertNotIn("EVT-1", printed)

    def test_a_refused_block_does_not_take_the_healthy_ones_with_it(self):
        """One section at a time. `events/processed/` refusing must not cost
        the LAST RUN block, which reads a different file."""
        module = self._module()
        real_iterdir = Path.iterdir
        real_scandir = __import__("os").scandir
        processed = (self.runtime / "events" / "processed").resolve()

        def only_processed(target) -> bool:
            try:
                return Path(target).resolve() == processed
            except (OSError, ValueError, TypeError):
                return False

        import os as os_module

        def fake_scandir(path=".", *a, **k):
            if only_processed(path):
                raise PermissionError(f"refused: {path}")
            return real_scandir(path, *a, **k)

        os_module.scandir = fake_scandir
        self.addCleanup(setattr, os_module, "scandir", real_scandir)

        def fake_iterdir(self_path):
            if only_processed(self_path):
                raise PermissionError(f"refused: {self_path}")
            return real_iterdir(self_path)

        Path.iterdir = fake_iterdir
        self.addCleanup(setattr, Path, "iterdir", real_iterdir)

        printed, _ = self._run_blocks(module)

        self.assertIn("LAST RUN", printed)
        # The block read the fixture manifest, not a failure marker: its own
        # `started_at`. (`run_id` is not printed — the block reports the
        # instant and the verdict, which is what an operator reads.)
        self.assertIn("2026-08-05T10:00:00+09:00", printed)
        self.assertNotIn("LAST RUN — 읽지 못했다", printed)


class OneRuleForNaiveAndAwareTests(unittest.TestCase):
    """`ops_status._comparable()` — five lines that were written three times.

    `_queue_age_days()`, the Runner-lock age and the last-run age each
    carried an identical copy, and each copy's docstring said "the same
    guard X uses for the same reason". Prose saying two things are the same
    is not the same as their being one thing — that is C28's rule and the
    shape `DuplicatedRulesStayInStepTests` exists to catch — and branch
    coverage showed the cost: the **second** arm, an aware stored value
    against a naive reference, had never run in any of the three.

    Both directions are asserted here because they resolve oppositely, and
    getting either backwards is a `TypeError` out of the tool an operator
    opens *because* something already looks wrong.
    """

    AWARE = datetime(2026, 8, 20, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    NAIVE = datetime(2026, 8, 20, 9, 0)

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_comparable", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_naive_stored_value_drags_the_reference_down(self):
        """There is no offset to invent for the stored value, so the
        reference gives its own up."""
        result = self._module()._comparable(self.AWARE, self.NAIVE)

        self.assertIsNone(result.tzinfo)
        self.assertEqual((result - self.NAIVE).total_seconds(), 0)

    def test_a_naive_reference_is_lifted_instead(self):
        """The arm that had never run. The caller's `now` is a local wall
        clock, and `astimezone()` is what that means — dropping the stored
        value's offset instead would silently shift the age by hours."""
        result = self._module()._comparable(self.NAIVE, self.AWARE)

        self.assertIsNotNone(result.tzinfo)
        self.assertEqual(result, self.NAIVE.astimezone())

    def test_two_aware_datetimes_are_left_alone(self):
        module = self._module()
        other = self.AWARE - timedelta(days=1)

        self.assertIs(module._comparable(self.AWARE, other), self.AWARE)

    def test_two_naive_datetimes_are_left_comparable(self):
        module = self._module()
        other = self.NAIVE - timedelta(days=1)

        result = module._comparable(self.NAIVE, other)
        self.assertIsNone(result.tzinfo)
        self.assertEqual((result - other).days, 1)

    def test_no_pairing_raises(self):
        """The property the three call sites depend on: whatever mix of
        naive and aware arrives, a subtraction follows."""
        module = self._module()
        for reference in (self.AWARE, self.NAIVE):
            for other in (self.AWARE, self.NAIVE):
                with self.subTest(reference=reference, other=other):
                    (module._comparable(reference, other) - other).total_seconds()

    def test_the_queue_age_survives_a_hand_edited_entry(self):
        """One of the three callers, end to end. `load_queue()` shape-checks
        `added_at` and never validates it as a timestamp, so an offset-less
        one reaches this arithmetic from a file a person edited."""
        module = self._module()

        self.assertIsNotNone(
            module._queue_age_days("2026-08-19T09:00:00", self.AWARE)
        )
        self.assertIsNotNone(
            module._queue_age_days("2026-08-19T09:00:00+09:00", self.NAIVE)
        )

    def test_an_unparseable_added_at_is_none_rather_than_an_error(self):
        self.assertIsNone(
            self._module()._queue_age_days("last tuesday", self.AWARE)
        )


class ATimestampWithNoOffsetIsStillComparableTests(unittest.TestCase):
    """`days_since_last_run()`'s naive/aware normalisation, never executed.

    `agent_state.json` is written by `datetime.now().astimezone()`, so every
    `last_run` this project produces carries an offset and the aware path is
    the only one the suite ever took. The naive path is not dead: a
    hand-edited state file, a restore from a machine whose clock had no
    zone, or a state written by an older build all produce
    `2026-08-10T09:00:00`, and Python raises `TypeError` on **any**
    comparison between a naive and an aware datetime.

    That exception would come out of `ops_status.py`'s AGENT block — the
    view an operator opens **because** something already looks wrong. The
    guard turns it into an answer; nothing had checked that the answer is
    right.

    Both directions are covered, because `now` can be the naive one too:
    `read_status()` takes whatever the caller passes.
    """

    def _snapshot(self, last_run):
        from agent.status import AgentStatusSnapshot

        return AgentStatusSnapshot(
            desktop_id="DESKTOP_1",
            last_run=last_run,
            last_successful_collection_date=None,
            pending_dates=(),
            outbox_count=0,
            sent_count=0,
            rejected_signal_count=0,
        )

    def test_a_naive_last_run_against_an_aware_now(self):
        snapshot = self._snapshot("2026-08-10T09:00:00")
        now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone(timedelta(hours=9)))

        self.assertEqual(snapshot.days_since_last_run(now), 3)

    def test_an_aware_last_run_against_a_naive_now(self):
        snapshot = self._snapshot("2026-08-10T09:00:00+09:00")

        self.assertEqual(
            snapshot.days_since_last_run(datetime(2026, 8, 13, 9, 0)), 3
        )

    def test_both_naive(self):
        snapshot = self._snapshot("2026-08-10T09:00:00")

        self.assertEqual(
            snapshot.days_since_last_run(datetime(2026, 8, 13, 9, 0)), 3
        )

    def test_the_ordinary_aware_pair_is_unchanged(self):
        """The path every real state file takes, asserted beside the others
        so the guard is evidence about a difference rather than about one
        case."""
        snapshot = self._snapshot("2026-08-10T09:00:00+09:00")
        now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone(timedelta(hours=9)))

        self.assertEqual(snapshot.days_since_last_run(now), 3)

    def test_a_naive_pair_never_raises_on_comparison(self):
        """The property, stated directly: whatever mix arrives, this returns
        a number rather than a TypeError out of a status view."""
        for last_run in ("2026-08-13T09:00:00", "2026-08-13T09:00:00+09:00"):
            for now in (
                datetime(2026, 8, 13, 10, 0),
                datetime(2026, 8, 13, 10, 0, tzinfo=timezone(timedelta(hours=9))),
            ):
                with self.subTest(last_run=last_run, now=now):
                    self.assertEqual(self._snapshot(last_run).days_since_last_run(now), 0)


class AgentStatusTests(AgentStatusTestCase):
    def test_a_never_run_agent_is_distinguishable_from_a_healthy_one(self):
        snapshot = self.status()

        self.assertIsNone(snapshot.desktop_id)
        self.assertIsNone(snapshot.last_run)
        self.assertIsNone(snapshot.days_since_last_run(NOW))
        self.assertIn("agent has never completed a run", snapshot.needs_attention(NOW))

    def test_a_healthy_agent_needs_no_attention(self):
        save_state(
            self.state_path,
            AgentState(
                desktop_id="DESKTOP_1",
                last_successful_collection_date=date(2026, 8, 9),
                last_run=NOW.isoformat(timespec="seconds"),
            ),
        )

        snapshot = self.status()

        self.assertEqual(snapshot.desktop_id, "DESKTOP_1")
        self.assertEqual(snapshot.pending_dates, ())
        self.assertEqual(snapshot.needs_attention(NOW), ())
        self.assertEqual(snapshot.days_since_last_run(NOW), 0)

    def test_undelivered_events_are_surfaced(self):
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_run=NOW.isoformat(timespec="seconds")),
        )
        self.touch(self.outbox, "a.json")
        self.touch(self.outbox, "b.json")

        snapshot = self.status(start_date=date(2026, 8, 10))

        self.assertEqual(snapshot.outbox_count, 2)
        self.assertTrue(snapshot.has_undelivered_events)
        self.assertIn(
            "2 event(s) created but not delivered", snapshot.needs_attention(NOW)
        )

    def test_uncollected_dates_are_surfaced(self):
        save_state(
            self.state_path,
            AgentState(
                desktop_id="DESKTOP_1",
                last_successful_collection_date=date(2026, 8, 5),
                last_run=NOW.isoformat(timespec="seconds"),
            ),
        )

        snapshot = self.status()

        self.assertEqual(
            snapshot.pending_dates,
            (date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 8), date(2026, 8, 9)),
        )
        self.assertTrue(snapshot.has_uncollected_dates)
        self.assertIn("4 date(s) not yet collected", snapshot.needs_attention(NOW))

    def test_a_stale_agent_is_flagged_but_a_weekend_off_is_not(self):
        save_state(
            self.state_path,
            AgentState(
                desktop_id="DESKTOP_1",
                last_successful_collection_date=date(2026, 8, 9),
                last_run=datetime(2026, 8, 9, 9, 0).astimezone().isoformat(timespec="seconds"),
            ),
        )
        yesterday = self.status()
        self.assertEqual(yesterday.days_since_last_run(NOW), 1)
        self.assertEqual(yesterday.needs_attention(NOW), ())

        save_state(
            self.state_path,
            AgentState(
                desktop_id="DESKTOP_1",
                last_successful_collection_date=date(2026, 8, 9),
                last_run=datetime(2026, 8, 5, 9, 0).astimezone().isoformat(timespec="seconds"),
            ),
        )
        stale = self.status()
        self.assertEqual(stale.days_since_last_run(NOW), 5)
        self.assertIn("agent has not run for 5 day(s)", stale.needs_attention(NOW))

    def test_rejected_signals_are_surfaced(self):
        save_state(
            self.state_path,
            AgentState(
                desktop_id="DESKTOP_1",
                last_successful_collection_date=date(2026, 8, 9),
                last_run=NOW.isoformat(timespec="seconds"),
            ),
        )
        self.touch(self.rejected / "2026-08-08", "leaky.json")

        snapshot = self.status()

        self.assertEqual(snapshot.rejected_signal_count, 1)
        self.assertIn(
            "1 signal(s) rejected and awaiting a human", snapshot.needs_attention(NOW)
        )

    def test_a_corrupted_state_file_is_reported_not_raised(self):
        """Reading is safe where acting is not: agent.run_once() must refuse
        a state it cannot trust, but the diagnostic that explains why must
        still work."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not json", encoding="utf-8")

        snapshot = self.status()

        self.assertIsNotNone(snapshot.state_error)
        self.assertIsNone(snapshot.desktop_id)
        self.assertTrue(
            any("unreadable" in reason for reason in snapshot.needs_attention(NOW))
        )

    def test_pending_dates_are_not_guessed_without_a_start_date(self):
        """docs/07 §50: a first-ever run's start date is never invented, so a
        status view must not invent one either."""
        save_state(self.state_path, AgentState(desktop_id="DESKTOP_1"))

        snapshot = self.status(start_date=None)

        self.assertEqual(snapshot.pending_dates, ())

    def test_reading_status_writes_nothing(self):
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_run=NOW.isoformat(timespec="seconds")),
        )
        self.touch(self.outbox, "a.json")
        before = {
            path: path.stat().st_mtime_ns
            for path in self.root.rglob("*")
            if path.is_file()
        }

        self.status()

        after = {
            path: path.stat().st_mtime_ns
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_missing_directories_are_zero_not_an_error(self):
        snapshot = self.status()

        self.assertEqual(snapshot.outbox_count, 0)
        self.assertEqual(snapshot.sent_count, 0)
        self.assertEqual(snapshot.rejected_signal_count, 0)


class CompanyActivityTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.processed = self.root / "processed"
        self.transport = self.root / "transport"
        self.incoming = self.root / "incoming"
        self.rejected = self.root / "rejected"
        self.processed.mkdir(parents=True, exist_ok=True)

    def add_event(self, *, source, role, timestamp, event_id=None):
        event = create_event(
            source=source,
            role=role,
            project_id="PRJ",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary=f"{source} work",
            history_candidate=True,
            event_id=event_id or f"{source}-{timestamp}",
            timestamp=timestamp,
        )
        (self.processed / f"{event.event_id.replace(':', '_')}.json").write_text(
            event.to_json(), encoding="utf-8"
        )

    def snapshot(self):
        return read_company_activity(
            processed_dir=self.processed,
            transport_dir=self.transport,
            incoming_dir=self.incoming,
            rejected_dir=self.rejected,
        )


class TheDiagnosticSurvivesADamagedFileTests(CompanyActivityTestCase):
    """The three guards in `desktop_activity.py` that had never executed.

    BACKLOG C49 §11c classified the module's remaining unexecuted branches as
    "real conditions, cheap to cover, left by priority". Every one of them is
    a `processed/` file that is present and wrong — which is not exotic:
    docs/11 permits a hand-placed Event, a partial restore leaves whatever it
    managed to copy, and this whole module exists to keep answering when part
    of the evidence is damaged.

    A guard that has never run is a guard nobody has checked, and the failure
    mode if one is wrong is the worst kind for a status view: an exception
    out of the thing an operator runs *because* something is already broken.
    """

    def _write(self, name, payload):
        (self.processed / name).write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )

    def test_an_unparseable_timestamp_leaves_the_date_unknown_not_crashing(self):
        """`DesktopActivity.last_event_date` parses `last_event_at`, which is
        whatever the file said. `validate_event()` would have refused this,
        but nothing re-validates a file already sitting in `processed/`."""
        self._write(
            "bad-timestamp.json",
            {
                "schema_version": "1.0",
                "event_id": "EVT-BAD-TS",
                "timestamp": "yesterday afternoon",
                "source": "DESKTOP_1",
                "role": "CTO_BACKEND",
                "project_id": "PRJ",
                "event_type": "MILESTONE_COMPLETED",
                "status": "IN_PROGRESS",
                "summary": "x",
                "history_candidate": True,
            },
        )

        activity = self.snapshot().for_source("DESKTOP_1")

        self.assertEqual(activity.event_count, 1)
        self.assertIsNone(activity.last_event_date)

    def test_a_desktop_that_is_not_in_the_schema_is_a_key_error(self):
        """`for_source()` covers every `events.SOURCES` entry, so a miss means
        the caller asked for something that is not a Desktop. Raising beats
        returning an empty activity, which would read as "reported nothing"."""
        with self.assertRaises(KeyError):
            self.snapshot().for_source("DESKTOP_9")

    def test_a_json_file_that_is_not_an_object_cannot_answer_the_twin_question(self):
        """`_event_id_of()` decides whether two files sharing a name are the
        same Event. A file holding a JSON *list* parses fine and has no
        `event_id`; treating it as a match would count a suppressed collision
        as an already-collected one and clear an ATTENTION line that should
        stay."""
        from app import desktop_activity

        self._write("a-list.json", [1, 2, 3])

        self.assertIsNone(desktop_activity._event_id_of(self.processed / "a-list.json"))

    def test_the_same_file_is_still_reported_as_unreadable_where_it_matters(self):
        """The two guards answer differently on purpose: `_event_id_of()`
        returns None so the caller stays cautious, and the `processed/` scan
        counts the file in `unreadable_events` so a person is told it is
        there."""
        self._write("a-list.json", [1, 2, 3])

        self.assertIn("a-list.json", self.snapshot().unreadable_events)


class TheDuplicateQualifierReachesTheCompanyBlockTests(CompanyActivityTestCase):
    """The screen half of C51 §6.

    The fold itself is pinned by `TheTwoBlocksCountTheSameEventsTests`; this
    is the line that tells the operator it happened. Without it the COMPANY
    block reports `events=1` over a directory holding two files and nothing
    says why — which is the same silent difference the fold was fixing, moved
    one layer out.
    """

    def _block(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_dupline", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.root
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module._print_company(
                datetime(2026, 8, 20, 9, 0, tzinfo=timezone(timedelta(hours=9)))
            )
        return buffer.getvalue()

    def setUp(self):
        super().setUp()
        # `_print_company()` reads RUNTIME_DIR/events/*
        events = self.root / "events"
        events.mkdir(parents=True, exist_ok=True)
        self.processed.rename(events / "processed")
        self.processed = events / "processed"

    def test_a_duplicate_file_is_named_on_the_screen(self):
        self.add_event(
            source="DESKTOP_4", role="COO", timestamp="2026-08-05T10:00:00+09:00",
            event_id="EVT-TWICE",
        )
        (self.processed / "a-copy.json").write_text(
            (self.processed / "EVT-TWICE.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        printed = self._block()

        self.assertIn("중복 파일", printed)
        self.assertIn("1건", printed)
        self.assertIn("DESKTOP_4   events=1", printed)

    def test_a_clean_directory_prints_no_qualifier(self):
        """A qualifier that always appears is one an operator stops
        reading — the same rule the CONTROL TOWER block's copy follows."""
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND",
            timestamp="2026-08-05T10:00:00+09:00",
        )

        self.assertNotIn("중복 파일", self._block())


class TheTwoBlocksCountTheSameEventsTests(CompanyActivityTestCase):
    """One screen, two counters, and they used to disagree.

    `collector/runtime.py` moves a DUPLICATE into `processed/` as well —
    docs/10 §46 forbids deleting it — and names the destination after **the
    incoming file**, not after the `event_id`. So one Event arriving twice
    under two names leaves two files, which is a re-send, a partial restore,
    or the hand-placed copy docs/11 permits.

    C50 §8 found the COMPANY-wide effect of that in
    `controltower.build_company_rollup()` and folded it on `event_id` there.
    `read_company_activity()` was never folded, and the two counters print
    one below the other. Measured on this repository's own `processed/` at
    C51, with exactly one such pair:

        COMPANY block          DESKTOP_4 events=2
        CONTROL TOWER block    DESKTOP_4 Event 1        + "중복 파일 1건"

    An operator reading down the page met two answers to one question, and
    only the lower one carried an explanation.
    """

    #: Two files, one Event. The second name is what a re-delivery or a
    #: hand-placed copy looks like — the Collector never renames it.
    def _one_event_in_two_files(self, *, source="DESKTOP_4", role="COO"):
        self.add_event(
            source=source,
            role=role,
            timestamp="2026-08-05T10:00:00+09:00",
            event_id="EVT-TWICE",
        )
        original = self.processed / "EVT-TWICE.json"
        (self.processed / "a-hand-placed-copy.json").write_text(
            original.read_text(encoding="utf-8"), encoding="utf-8"
        )

    def test_one_event_in_two_files_is_counted_once(self):
        self._one_event_in_two_files()

        self.assertEqual(self.snapshot().for_source("DESKTOP_4").event_count, 1)

    def test_the_fold_is_reported_rather_than_silent(self):
        """`CompanyRollup.duplicates` states the rule this follows: an
        operator who sees `events=1` where the directory holds two files has
        to be able to find the second.

        *Which* of the two is named is decided by sorted filename order —
        the same thing that decides it in `build_company_rollup()`, and for
        identical copies it makes no difference to any number. It is asserted
        as "one of the two, not both" rather than by name so the test pins
        the property instead of the ordering."""
        self._one_event_in_two_files()

        snapshot = self.snapshot()
        self.assertEqual(len(snapshot.duplicate_event_files), 1)
        self.assertIn(
            snapshot.duplicate_event_files[0],
            ("EVT-TWICE.json", "a-hand-placed-copy.json"),
        )

    def test_a_clean_directory_reports_no_duplicates(self):
        """A qualifier that always appears is one an operator stops
        reading."""
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND", timestamp="2026-08-05T10:00:00+09:00"
        )

        self.assertEqual(self.snapshot().duplicate_event_files, ())

    def test_the_two_counters_agree_on_the_same_directory(self):
        """The property, asserted against the other counter rather than
        against a number this test chose. That is what makes it a guard on
        the *disagreement* instead of on one implementation."""
        from datetime import datetime, timedelta, timezone

        from controltower import build_company_rollup

        self._one_event_in_two_files()
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND", timestamp="2026-08-06T10:00:00+09:00"
        )

        now = datetime(2026, 8, 20, 9, 0, tzinfo=timezone(timedelta(hours=9)))
        snapshot = self.snapshot()
        rollup = build_company_rollup(processed_dir=self.processed, now=now)

        for desktop in rollup.desktops:
            with self.subTest(source=desktop.source):
                self.assertEqual(
                    snapshot.for_source(desktop.source).event_count,
                    desktop.event_count,
                )

    def test_the_arrival_time_does_not_depend_on_what_a_file_is_called(self):
        """The trap the first draft of this fold fell into.

        `last_arrival_at` answers "when did a file from this Desktop last
        show up", which is what separates the two ATTENTION sentences
        ("Agent가 멈췄다" from "밀린 분을 보낸 것으로 보인다"). Read off only
        the copy that survives the fold, the answer becomes whichever copy
        sorted first — so renaming a file would change an operational
        number. It is a max over every copy instead."""
        self._one_event_in_two_files()
        recent = time.time()
        old = recent - 86_400 * 30

        for name, when in (
            ("EVT-TWICE.json", old),
            ("a-hand-placed-copy.json", recent),
        ):
            os.utime(self.processed / name, (when, when))
        newest_first = self.snapshot().for_source("DESKTOP_4").last_arrival_at

        for name, when in (
            ("EVT-TWICE.json", recent),
            ("a-hand-placed-copy.json", old),
        ):
            os.utime(self.processed / name, (when, when))
        newest_second = self.snapshot().for_source("DESKTOP_4").last_arrival_at

        self.assertAlmostEqual(newest_first, newest_second, places=3)
        self.assertAlmostEqual(newest_first, recent, places=3)

    def test_a_duplicate_does_not_widen_the_event_date_range(self):
        """The copy carries the same timestamp, so `first_event_at` /
        `last_event_at` must not move — and a copy edited to a different
        date is a different Event by content, which is the Control Tower's
        `EVENT_ID_CONFLICT`, not this counter's business."""
        self._one_event_in_two_files()
        activity = self.snapshot().for_source("DESKTOP_4")

        self.assertEqual(activity.first_event_at, activity.last_event_at)

    def test_a_file_with_no_event_id_is_counted_rather_than_dropped(self):
        """It cannot be folded, and dropping it would make the block quieter
        than the directory. `unreadable` fails in the same direction."""
        (self.processed / "no-id.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": "2026-08-05T10:00:00+09:00",
                    "source": "DESKTOP_2",
                    "role": "CMO",
                    "project_id": "PRJ",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "no id",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

        snapshot = self.snapshot()
        self.assertEqual(snapshot.for_source("DESKTOP_2").event_count, 1)
        self.assertEqual(snapshot.duplicate_event_files, ())

    def test_three_files_of_one_event_leave_one_count_and_two_duplicates(self):
        self._one_event_in_two_files()
        (self.processed / "another-copy.json").write_text(
            (self.processed / "EVT-TWICE.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        snapshot = self.snapshot()
        self.assertEqual(snapshot.for_source("DESKTOP_4").event_count, 1)
        self.assertEqual(len(snapshot.duplicate_event_files), 2)


class CompanyActivityTests(CompanyActivityTestCase):
    def test_every_schema_source_appears_even_with_no_events(self):
        """A Desktop missing from a report and a Desktop that reported
        nothing look identical to a reader; only one of those is fine."""
        snapshot = self.snapshot()

        self.assertEqual({a.source for a in snapshot.desktops}, set(SOURCES))
        self.assertEqual(set(snapshot.never_reported), set(SOURCES))

    def test_counts_roles_and_bounds_are_derived_per_desktop(self):
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND", timestamp="2026-08-05T10:00:00+09:00"
        )
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND", timestamp="2026-08-09T18:00:00+09:00"
        )
        self.add_event(source="DESKTOP_2", role="CMO", timestamp="2026-08-07T10:00:00+09:00")

        snapshot = self.snapshot()

        one = snapshot.for_source("DESKTOP_1")
        self.assertEqual(one.event_count, 2)
        self.assertEqual(one.roles, ("CTO_BACKEND",))
        self.assertEqual(one.first_event_at, "2026-08-05T10:00:00+09:00")
        self.assertEqual(one.last_event_at, "2026-08-09T18:00:00+09:00")
        self.assertEqual(one.last_event_date, date(2026, 8, 9))
        self.assertEqual(one.days_silent(NOW), 1)

        self.assertEqual(snapshot.for_source("DESKTOP_2").event_count, 1)
        self.assertFalse(snapshot.for_source("DESKTOP_3").has_ever_reported)

    def test_the_newest_event_wins_regardless_of_filename_order(self):
        """Bounds come from parsed timestamps, not from the order files
        happen to be listed in."""
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-09T10:00:00+09:00",
            event_id="zzz-newest",
        )
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-01T10:00:00+09:00",
            event_id="aaa-oldest",
        )

        one = self.snapshot().for_source("DESKTOP_1")

        self.assertEqual(one.last_event_at, "2026-08-09T10:00:00+09:00")
        self.assertEqual(one.first_event_at, "2026-08-01T10:00:00+09:00")

    def test_different_utc_offsets_are_compared_correctly(self):
        """The schema accepts a non-KST offset, so string ordering is not
        enough — 09:00+00:00 is later than 17:00+09:00 on the same day."""
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-09T17:00:00+09:00",
            event_id="kst",
        )
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-09T09:00:00+00:00",
            event_id="utc",
        )

        self.assertEqual(
            self.snapshot().for_source("DESKTOP_1").last_event_at,
            "2026-08-09T09:00:00+00:00",
        )

    def test_silent_desktops_are_listed_with_the_never_reported_ones(self):
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND", timestamp="2026-08-09T10:00:00+09:00"
        )
        self.add_event(source="DESKTOP_2", role="CMO", timestamp="2026-08-01T10:00:00+09:00")

        silent = self.snapshot().silent_for(NOW, days=3)

        self.assertNotIn("DESKTOP_1", silent)
        self.assertIn("DESKTOP_2", silent)
        self.assertIn("DESKTOP_3", silent)
        self.assertIn("DESKTOP_4", silent)

    def test_backlog_counts_come_from_the_real_directories(self):
        # Names are unique per directory on purpose. This fixture originally
        # numbered every directory from 0, which made two of the three
        # transport files share a name with an `incoming/` file — the shape
        # `run_intake()` skips as already-present, so the view now (correctly)
        # counts them as `already_collected` rather than backlog. What this
        # test is about is that each count comes from its own directory, and
        # colliding names were never part of that.
        for directory, count in ((self.transport, 3), (self.incoming, 2), (self.rejected, 1)):
            directory.mkdir(parents=True, exist_ok=True)
            for index in range(count):
                (directory / f"{directory.name}-{index}.json").write_text(
                    "{}", encoding="utf-8"
                )

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 3)
        self.assertEqual(backlog.already_collected, 0)
        self.assertEqual(backlog.awaiting_collection, 2)
        self.assertEqual(backlog.rejected, 1)
        self.assertFalse(backlog.is_clear)

    def test_a_clear_backlog_is_the_steady_state(self):
        self.assertTrue(self.snapshot().backlog.is_clear)

    def test_a_damaged_processed_file_is_reported_not_fatal(self):
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND", timestamp="2026-08-09T10:00:00+09:00"
        )
        (self.processed / "corrupt.json").write_text("{not json", encoding="utf-8")
        (self.processed / "notanobject.json").write_text("[]", encoding="utf-8")
        (self.processed / "nosource.json").write_text(
            json.dumps({"timestamp": "2026-08-09T10:00:00+09:00"}), encoding="utf-8"
        )

        snapshot = self.snapshot()

        self.assertEqual(snapshot.for_source("DESKTOP_1").event_count, 1)
        self.assertEqual(
            sorted(snapshot.unreadable_events),
            ["corrupt.json", "nosource.json", "notanobject.json"],
        )

    def test_reading_activity_writes_nothing(self):
        self.add_event(
            source="DESKTOP_1", role="CTO_BACKEND", timestamp="2026-08-09T10:00:00+09:00"
        )
        before = {
            path: path.stat().st_mtime_ns
            for path in self.root.rglob("*")
            if path.is_file()
        }

        self.snapshot()

        after = {
            path: path.stat().st_mtime_ns
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_a_missing_processed_directory_is_not_an_error(self):
        shutil.rmtree(self.processed)

        snapshot = self.snapshot()

        self.assertEqual(set(snapshot.never_reported), set(SOURCES))


class ArrivalVersusWorkDateTests(CompanyActivityTestCase):
    """The one cause of silence that existing data CAN separate.

    An Event says when the work happened; the file says when it turned up.
    A Desktop that was off for a week and then caught up delivers week-old
    work today — indistinguishable from a dead Desktop if you only read the
    work dates. Comparing the two answers "is the Agent alive?" without any
    heartbeat, new Event type, or schema change.
    """

    def _age_file(self, event_id: str, *, days_ago: int):
        """Set the file's arrival time to `days_ago` days before **NOW**.

        Anchored to NOW, not to `time.time()`. Every assertion in this class
        is made against NOW (2026-08-10), so aging against the wall clock
        made the arrival age depend on the calendar date the suite happened
        to run on: `days_ago=6` meant "2026-08-04" on 2026-08-10 and
        "2026-08-08" on 2026-08-14, which is 2 days before NOW rather than
        6 after. Measured -- `test_a_desktop_that_caught_up_is_distinguished_
        from_a_dead_one` passed every day up to 2026-08-13 and failed on
        2026-08-14 with no code change, because `caught_up_recently(NOW,
        days=3)` compares `3 > arrival` and the drifting arrival crossed 3.

        Same class of fixture defect C27 section 12 fixed elsewhere; this one
        was missed because the wall clock only had to move four days for it
        to appear.
        """
        import os

        path = self.processed / f"{event_id}.json"
        when = NOW.timestamp() - days_ago * 86400
        os.utime(path, (when, when))

    def test_arrival_time_is_reported_separately_from_work_date(self):
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-04T10:00:00+09:00",
            event_id="OLD-WORK",
        )
        self._age_file("OLD-WORK", days_ago=0)

        activity = self.snapshot().for_source("DESKTOP_1")

        self.assertEqual(activity.days_silent(NOW), 6)
        self.assertIsNotNone(activity.last_arrival_at)
        self.assertLessEqual(activity.days_since_arrival(NOW), 1)

    def test_a_desktop_that_caught_up_is_distinguished_from_a_dead_one(self):
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-04T10:00:00+09:00",
            event_id="CAUGHT-UP",
        )
        self.add_event(
            source="DESKTOP_2",
            role="CMO",
            timestamp="2026-08-04T10:00:00+09:00",
            event_id="GONE-QUIET",
        )
        self._age_file("CAUGHT-UP", days_ago=0)
        self._age_file("GONE-QUIET", days_ago=6)

        snapshot = self.snapshot()

        # Both look equally silent by work date...
        self.assertEqual(snapshot.for_source("DESKTOP_1").days_silent(NOW), 6)
        self.assertEqual(snapshot.for_source("DESKTOP_2").days_silent(NOW), 6)
        # ...and only one of them has contacted us since.
        self.assertTrue(snapshot.for_source("DESKTOP_1").caught_up_recently(NOW, days=3))
        self.assertFalse(snapshot.for_source("DESKTOP_2").caught_up_recently(NOW, days=3))

    def test_a_healthy_desktop_is_not_labelled_as_caught_up(self):
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-10T10:00:00+09:00",
            event_id="FRESH",
        )
        self._age_file("FRESH", days_ago=0)

        activity = self.snapshot().for_source("DESKTOP_1")

        self.assertEqual(activity.days_silent(NOW), 0)
        self.assertFalse(activity.caught_up_recently(NOW, days=3))

    def test_a_never_reporting_desktop_has_no_arrival_time(self):
        activity = self.snapshot().for_source("DESKTOP_3")

        self.assertIsNone(activity.last_arrival_at)
        self.assertIsNone(activity.days_since_arrival(NOW))
        self.assertFalse(activity.caught_up_recently(NOW, days=3))

    def test_arrival_never_removes_a_desktop_from_the_silent_list(self):
        """The flag is narrowed, never cleared. A false reassurance about a
        dead Desktop would be worse than the false alarm it replaced."""
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-04T10:00:00+09:00",
            event_id="CAUGHT-UP",
        )
        self._age_file("CAUGHT-UP", days_ago=0)

        snapshot = self.snapshot()

        self.assertIn("DESKTOP_1", snapshot.silent_for(NOW, days=3))

    def test_the_newest_arrival_wins_regardless_of_work_date(self):
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-09T10:00:00+09:00",
            event_id="NEW-WORK-OLD-FILE",
        )
        self.add_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            timestamp="2026-08-01T10:00:00+09:00",
            event_id="OLD-WORK-NEW-FILE",
        )
        self._age_file("NEW-WORK-OLD-FILE", days_ago=8)
        self._age_file("OLD-WORK-NEW-FILE", days_ago=0)

        activity = self.snapshot().for_source("DESKTOP_1")

        self.assertEqual(activity.last_event_at, "2026-08-09T10:00:00+09:00")
        self.assertLessEqual(activity.days_since_arrival(NOW), 1)

    def test_an_unreadable_file_contributes_no_arrival_time(self):
        (self.processed / "broken.json").write_text("{not json", encoding="utf-8")

        snapshot = self.snapshot()

        self.assertEqual(snapshot.unreadable_events, ("broken.json",))
        for activity in snapshot.desktops:
            with self.subTest(source=activity.source):
                self.assertIsNone(activity.last_arrival_at)


class ParallelReadDeterminismTests(CompanyActivityTestCase):
    """The processed/ scan runs on a thread pool for speed (24 s -> 3.3 s at
    5,000 files, measured cold). Threads may change nothing but the timing.

    The risk with a pool is ordering: `unreadable_events` is a list, and
    first/last timestamp ties are resolved by iteration order. Both must
    match what a plain serial loop produces, or an operator comparing two
    runs would see the report shuffle for no reason.
    """

    def _serial_snapshot(self):
        """Same fold, same inputs, no pool."""
        import app.desktop_activity as module

        original = module._read_all
        module._read_all = lambda paths: [(p, module._read_one(p)) for p in paths]
        try:
            return self.snapshot()
        finally:
            module._read_all = original

    def test_the_pooled_result_matches_a_serial_read_exactly(self):
        for index in range(60):
            self.add_event(
                source=["DESKTOP_1", "DESKTOP_2", "DESKTOP_3", "DESKTOP_4"][index % 4],
                role=["CTO_BACKEND", "CMO", "CTO_FRONTEND", "COO"][index % 4],
                timestamp=f"2026-08-{(index % 28) + 1:02d}T10:00:00+09:00",
                event_id=f"EVT-{index:04d}",
            )
        for name in ("bad-a.json", "bad-b.json", "bad-c.json"):
            (self.processed / name).write_text("{not json", encoding="utf-8")

        pooled = self.snapshot()
        serial = self._serial_snapshot()

        self.assertEqual(pooled, serial)

    def test_unreadable_filenames_come_back_in_sorted_order(self):
        for name in ("zz.json", "aa.json", "mm.json"):
            (self.processed / name).write_text("{not json", encoding="utf-8")

        self.assertEqual(
            list(self.snapshot().unreadable_events), ["aa.json", "mm.json", "zz.json"]
        )

    def test_repeated_runs_produce_the_identical_snapshot(self):
        for index in range(40):
            self.add_event(
                source="DESKTOP_1",
                role="CTO_BACKEND",
                timestamp="2026-08-09T10:00:00+09:00",
                event_id=f"SAME-{index:04d}",
            )

        self.assertEqual(self.snapshot(), self.snapshot())

    def test_an_empty_directory_spawns_no_pool(self):
        """A status call on a fresh machine should not pay for threads it
        has no work for."""
        import app.desktop_activity as module

        self.assertEqual(module._read_all([]), [])

    def test_the_worker_count_is_bounded(self):
        import app.desktop_activity as module

        self.assertGreaterEqual(module._READ_WORKERS, 4)
        self.assertLessEqual(module._READ_WORKERS, 16)

    def test_the_reads_really_do_overlap(self):
        """C87. The pool is a **measured** decision and this is what stops it
        being optimised away by a warm-cache benchmark.

        Measured on this machine, `_read_one` over freshly written files:

            warm local   500 files   serial  23 ms   pool16   32 ms
            warm local  2000 files   serial  83 ms   pool16  126 ms
            cold local   500 files   serial 547 ms   pool16   93 ms   5.9x
            cold local  2000 files   serial 7537 ms  pool16  890 ms   8.5x

        Warm, the pool costs about 40 ms at two thousand files. Cold — the
        first read of files that just arrived, which is the ordinary case for
        a Runner and for an operator opening the dashboard after a reboot —
        it saves **seconds**. Three trials with the order alternated gave
        5.9x, 5.9x and 9.4x, so the direction is not an artefact of who ran
        first.

        And `_attribute()` runs this over `transport/`, which is the OneDrive
        Sync Folder (AGENT.md section 1). A simulated per-file latency of
        0.1 ms already makes the pool 6.6x faster; real OneDrive was not
        measured, deliberately — see BACKLOG.

        Asserted as overlap rather than as `ThreadPoolExecutor` appearing in
        the source, because what matters is that the reads happen at the same
        time, not how.
        """
        import app.desktop_activity as module
        import threading

        for index in range(24):
            self.add_event(
                source="DESKTOP_1",
                role="CTO_BACKEND",
                timestamp="2026-08-09T10:00:00+09:00",
                event_id=f"OVERLAP-{index:03d}",
            )
        paths = sorted(self.processed.glob("*.json"))
        self.assertGreaterEqual(len(paths), 24)

        lock = threading.Lock()
        live = 0
        peak = 0
        real = module._read_one

        def watched(path):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            try:
                time.sleep(0.01)          # long enough for overlap to show
                return real(path)
            finally:
                with lock:
                    live -= 1

        module._read_one = watched
        try:
            result = module._read_all(paths)
        finally:
            module._read_one = real

        self.assertEqual(len(result), len(paths))
        self.assertGreater(
            peak, 1, "_read_all read the files one at a time — the pool is "
            "gone, and the cold path costs seconds again"
        )

    def test_order_is_preserved_even_though_the_reads_overlap(self):
        """The property the pool must not cost. `unreadable_events` is
        reported in sorted order and `first`/`last` tie-breaking depends on
        it, so a pool that returned completion order would change answers
        rather than only timings."""
        import app.desktop_activity as module

        for index in range(12):
            self.add_event(
                source="DESKTOP_1",
                role="CTO_BACKEND",
                timestamp="2026-08-09T10:00:00+09:00",
                event_id=f"ORDER-{index:03d}",
            )
        paths = sorted(self.processed.glob("*.json"))

        returned = [path for path, _result in module._read_all(paths)]

        self.assertEqual(returned, paths)


class StateConsistencyInStatusTests(unittest.TestCase):
    """docs/10 §48's check finally has a caller.

    `scheduler/consistency.py` detects the corruption §47 names — state
    claiming a Daily Close whose file is gone — and was fully implemented
    and tested with **zero production callers**. A detector nothing runs
    detects nothing.

    It is surfaced in the status view rather than the Runner because that
    module deliberately refuses to enter Scheduler's control flow: §49 makes
    History authoritative over state and §64 puts the decision with the COO.
    Reporting is not deciding.
    """

    def _load(self, runtime_dir: Path):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_consistency", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime_dir
        return module

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "state").mkdir(parents=True)
        (runtime / "local_master" / "daily").mkdir(parents=True)
        return runtime

    def test_a_state_claiming_a_missing_daily_is_reported(self):
        runtime = self._runtime()
        (runtime / "state" / "daily_history_state.json").write_text(
            json.dumps({"last_successful_daily_close": "2026-08-09"}), encoding="utf-8"
        )

        module = self._load(runtime)
        attention = module._print_history(NOW)

        self.assertTrue(
            any("어긋난다" in item for item in attention),
            f"inconsistency not surfaced: {attention}",
        )

    def test_a_matching_state_and_history_needs_no_attention(self):
        runtime = self._runtime()
        (runtime / "state" / "daily_history_state.json").write_text(
            json.dumps({"last_successful_daily_close": "2026-08-09"}), encoding="utf-8"
        )
        (runtime / "local_master" / "daily" / "2026-08-09.md").write_text(
            "# ok", encoding="utf-8"
        )
        # mtime resolution: `_history_newer_than_the_last_backup()` orders the
        # file's real mtime against `_healthy_backup_state()`'s real
        # `datetime.now()` snapshot. Without a gap, the two land close enough
        # in wall-clock time that they can invert under load (observed
        # flake), the same trap `BackupAlertSweepTests` already guards with
        # `time.sleep(1.1)  # mtime resolution`.
        time.sleep(1.1)
        # See `_healthy_backup_state`: history that exists was also backed up.
        _healthy_backup_state(runtime / "state")

        module = self._load(runtime)

        self.assertEqual(module._print_history(NOW), [])

    def test_an_unreadable_daily_state_is_reported(self):
        runtime = self._runtime()
        (runtime / "state" / "daily_history_state.json").write_text(
            "{not json", encoding="utf-8"
        )

        module = self._load(runtime)
        attention = module._print_history(NOW)

        self.assertTrue(any("읽을 수 없다" in item for item in attention))

    def test_a_first_ever_run_is_not_an_inconsistency(self):
        """No state file yet is NO_STATE, not corruption — flagging it would
        make every fresh install look broken."""
        module = self._load(self._runtime())

        self.assertEqual(module._print_history(NOW), [])

    def test_the_status_view_never_repairs_anything(self):
        """The module's whole restraint: it reports, it does not fix."""
        runtime = self._runtime()
        state_file = runtime / "state" / "daily_history_state.json"
        state_file.write_text(
            json.dumps({"last_successful_daily_close": "2026-08-09"}), encoding="utf-8"
        )
        before = state_file.read_text(encoding="utf-8")

        module = self._load(runtime)
        module._print_history(NOW)

        self.assertEqual(state_file.read_text(encoding="utf-8"), before)
        self.assertEqual(
            list((runtime / "local_master" / "daily").glob("*.md")), []
        )


class StatusEntrypointTests(unittest.TestCase):
    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_entrypoint_imports_and_exposes_a_main(self):
        """ops_status.py is the only way an operator reaches any of the
        views, so an import-time break makes all of them invisible."""
        module = self._load_entrypoint()

        self.assertTrue(callable(module.main))
        self.assertEqual(module.SILENT_AFTER_DAYS, 3)

    def test_all_three_views_are_wired_into_main(self):
        module = self._load_entrypoint()

        for name in ("_print_company", "_print_history", "_print_agent"):
            with self.subTest(view=name):
                self.assertTrue(callable(getattr(module, name)))

        # Matched as "named in `main()`'s block table", not as the literal
        # call `_print_company(now)`. C55 wrapped every block in `_block()`
        # so a refused disk costs one section instead of the whole report,
        # and the calls stopped being written that way — a wiring check that
        # breaks on the *shape* of the call reports on the refactor rather
        # than on the wiring.
        import inspect

        source = inspect.getsource(module.main)
        for name in ("_print_company", "_print_history", "_print_agent"):
            with self.subTest(view=name):
                self.assertIn(f", {name})", source)
                self.assertIn("_block(label, block, now)", source)

    def test_the_history_view_survives_a_missing_local_master(self):
        """On Desktop 1/2/3 there is no Local Master at all; the view must
        report that rather than raise."""
        module = self._load_entrypoint()
        module.RUNTIME_DIR = Path(tempfile.mkdtemp()) / "runtime"
        self.addCleanup(shutil.rmtree, module.RUNTIME_DIR.parent, True)

        attention = module._print_history(NOW)

        self.assertEqual(attention, [])

    def test_the_history_view_reports_a_corrupted_monthly_state(self):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        state = module.RUNTIME_DIR / "state"
        state.mkdir(parents=True)
        (state / "monthly_history_state.json").write_text("{not json", encoding="utf-8")

        attention = module._print_history(NOW)

        self.assertTrue(any("손상" in item for item in attention))

    def test_the_history_view_flags_a_month_waiting_for_rebuild(self):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        state = module.RUNTIME_DIR / "state"
        state.mkdir(parents=True)
        (state / "monthly_history_state.json").write_text(
            json.dumps(
                {
                    "last_successful_monthly_close": "2026-07",
                    "dirty_months": ["2026-07"],
                }
            ),
            encoding="utf-8",
        )

        attention = module._print_history(NOW)

        self.assertTrue(any("2026-07" in item for item in attention))

    def test_the_history_view_flags_a_closed_month_never_consolidated(self):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        state = module.RUNTIME_DIR / "state"
        state.mkdir(parents=True)
        (state / "monthly_history_state.json").write_text(
            json.dumps({"last_successful_monthly_close": "2026-05", "dirty_months": []}),
            encoding="utf-8",
        )
        # The pointer's own month must have its file: `run_once()` advances
        # the pointer only on GENERATED or UNCHANGED, both of which leave the
        # file on disk. Without it this fixture describes a state no run can
        # produce, and the Monthly consistency check would (correctly) fire —
        # so the assertion below would pass partly for the wrong reason.
        monthly = module.RUNTIME_DIR / "local_master" / "monthly"
        monthly.mkdir(parents=True)
        (monthly / "2026-05.md").write_text("# 2026-05\n", encoding="utf-8")

        # NOW is 2026-08-10, so 2026-07 is the last closed month.
        attention = module._print_history(NOW)

        self.assertTrue(any("2026-07" in item for item in attention))

    def test_a_freshly_consolidated_month_needs_no_attention(self):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        state = module.RUNTIME_DIR / "state"
        state.mkdir(parents=True)
        (state / "monthly_history_state.json").write_text(
            json.dumps({"last_successful_monthly_close": "2026-07", "dirty_months": []}),
            encoding="utf-8",
        )
        # "Freshly consolidated" means the file exists — that is what
        # consolidation produces, and the pointer is never advanced without
        # it. Stating the pointer alone described a state no run can reach.
        monthly = module.RUNTIME_DIR / "local_master" / "monthly"
        monthly.mkdir(parents=True)
        (monthly / "2026-07.md").write_text("# 2026-07\n", encoding="utf-8")
        # mtime resolution: see the identical gap added in
        # StateConsistencyInStatusTests::test_a_matching_state_and_history_needs_no_attention
        # (both order this file's real mtime against `_healthy_backup_state()`'s
        # real `datetime.now()` snapshot, with no guaranteed gap otherwise).
        time.sleep(1.1)
        # A machine that produced Company History also ran Backup: it is a
        # step in the same pipeline, not an optional one, and it writes state
        # on failure as well as success. History with no backup state at all
        # describes a machine where Backup has never run — a real condition,
        # and now a reported one.
        _healthy_backup_state(state)

        self.assertEqual(module._print_history(NOW), [])


class UnparseableTransportFileTests(CompanyActivityTestCase):
    """A file `transport.run_intake()` cannot parse must not be reported as
    "awaiting collection" — it is never going to be collected.

    `run_intake()` leaves an unparseable file exactly where it is: never
    promoted, never moved, never deleted, and re-judged on every run. The
    backlog view counted every `*.json` in `transport/`, so one such file
    held `awaiting_intake` at 1 permanently.

    Measured on the real runtime with a single 0-byte file — the shape
    OneDrive Files On-Demand produces for a not-yet-downloaded placeholder:

        run 1..4   transport metrics {'skipped_invalid': 1}   every run
        ops_status ATTENTION: "수집되지 않고 남은 Event: transport=1"

    That sentence says an Event is queued for collection. It was not; it had
    been judged and parked. **An alert no run can clear is worse than no
    alert** — ATTENTION is where real problems surface, and a permanent
    entry teaches an operator to skim past the section. The file does still
    need a human; it needs a different sentence.
    """

    def _write(self, name, content):
        self.transport.mkdir(parents=True, exist_ok=True)
        (self.transport / name).write_text(content, encoding="utf-8")

    def test_an_unparseable_file_is_not_counted_as_awaiting_intake(self):
        self._write("zero.json", "")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 0)
        self.assertEqual(backlog.unparseable, 1)

    def test_a_valid_pending_file_is_still_counted_as_awaiting_intake(self):
        """The guard must not hide real backlog — that would be the opposite
        defect, and a worse one."""
        self._write("good.json", '{"event_id": "E-1"}')

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 1)
        self.assertEqual(backlog.unparseable, 0)

    def test_the_two_are_counted_independently(self):
        self._write("good.json", '{"event_id": "E-1"}')
        self._write("zero.json", "")
        self._write("truncated.json", '{"event_id": "E-2"')

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 1)
        self.assertEqual(backlog.unparseable, 2)

    def test_an_unparseable_file_alone_leaves_the_backlog_clear(self):
        """`is_clear` means "nothing in flight". A parked file is not in
        flight, and treating it as such made `is_clear` permanently False
        for a condition no run could resolve."""
        self._write("zero.json", "")

        self.assertTrue(self.snapshot().backlog.is_clear)

    def test_a_real_pending_file_does_make_the_backlog_unclear(self):
        self._write("good.json", '{"event_id": "E-1"}')

        self.assertFalse(self.snapshot().backlog.is_clear)

    def test_the_view_uses_intake_s_own_parse_test(self):
        """A second opinion about what "valid" means would let this view and
        the step it reports on disagree — the class of contradiction this
        Sprint was told to hunt for."""
        import inspect

        import app.desktop_activity as activity

        source = inspect.getsource(activity._count_transport)
        self.assertIn("_is_parseable_json", source)


class BacklogSourceAttributionTests(CompanyActivityTestCase):
    """BACKLOG E-10: the backlog counts said how much, never from whom.

    `IntakeBacklog.rejected` was a company-wide sum. "Collector가 거부한
    Event 3건" is the same sentence whether one Desktop is misbehaving or
    three Desktops are each hitting the same schema change, and those two
    need opposite reactions — one is a machine to go look at, the other is a
    change to roll back. Telling them apart meant opening
    `runtime/events/rejected/` by hand, which is the step a status view
    exists to remove.

    Pure aggregation: no new file, no new field on the wire, no policy. The
    totals are untouched and remain the authority — every test here asserts
    the breakdown adds back up to the count it explains.
    """

    def _write(self, directory, name, payload):
        directory.mkdir(parents=True, exist_ok=True)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (directory / name).write_text(text, encoding="utf-8")

    def _event(self, source, event_id):
        return {
            "schema_version": "1.0",
            "event_id": event_id,
            "timestamp": "2026-08-09T10:00:00+09:00",
            "source": source,
            "role": "CTO_BACKEND",
            "project_id": "PRJ",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "summary": "work",
            "history_candidate": True,
        }

    def test_rejected_events_are_attributed_to_the_desktop_that_sent_them(self):
        self._write(self.rejected, "a.json", self._event("DESKTOP_1", "E-1"))
        self._write(self.rejected, "b.json", self._event("DESKTOP_1", "E-2"))
        self._write(self.rejected, "c.json", self._event("DESKTOP_3", "E-3"))

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.rejected, 3)
        self.assertEqual(
            backlog.rejected_sources.by_source, (("DESKTOP_1", 2), ("DESKTOP_3", 1))
        )
        self.assertEqual(backlog.rejected_sources.unattributed, 0)

    def test_several_desktops_rejected_at_once_are_told_apart(self):
        """The situation E-10 was written for: a schema change lands and
        every Desktop starts failing at the same moment. The total alone
        cannot distinguish that from one broken machine."""
        for index, source in enumerate(sorted(SOURCES)):
            self._write(self.rejected, f"{index}.json", self._event(source, f"E-{index}"))

        breakdown = self.snapshot().backlog.rejected_sources

        self.assertEqual(dict(breakdown.by_source), {s: 1 for s in SOURCES})
        self.assertEqual(breakdown.total, 4)

    def test_transport_and_incoming_are_attributed_too(self):
        self._write(self.transport, "t.json", self._event("DESKTOP_2", "E-T"))
        self._write(self.incoming, "i.json", self._event("DESKTOP_4", "E-I"))

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake_sources.by_source, (("DESKTOP_2", 1),))
        self.assertEqual(backlog.awaiting_collection_sources.by_source, (("DESKTOP_4", 1),))

    def test_a_corrupted_rejected_event_is_counted_but_never_attributed(self):
        """A file that is not JSON has no readable `source`. It must still
        appear in the total — it is a real file needing a real human — and
        must not be blamed on whichever Desktop happens to be listed first.
        """
        self._write(self.rejected, "good.json", self._event("DESKTOP_1", "E-1"))
        self._write(self.rejected, "truncated.json", '{"source": "DESKTOP_1"')
        self._write(self.rejected, "empty.json", "")
        self._write(self.rejected, "list.json", "[1, 2, 3]")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.rejected, 4)
        self.assertEqual(backlog.rejected_sources.by_source, (("DESKTOP_1", 1),))
        self.assertEqual(backlog.rejected_sources.unattributed, 3)
        self.assertEqual(backlog.rejected_sources.total, backlog.rejected)

    def test_a_source_no_desktop_is_allowed_to_send_is_unattributed_not_quoted(self):
        """Rejection is often *because* the source is wrong, so this is a
        common shape rather than an exotic one — and every file counted here
        is untrusted input that failed validation. Echoing the string it
        claims into an operator's terminal is the mistake `oplog` escapes
        against, so the count surfaces and the string does not.
        """
        self._write(self.rejected, "a.json", self._event("DESKTOP_9", "E-1"))
        self._write(self.rejected, "b.json", self._event("", "E-2"))
        self._write(self.rejected, "c.json", {"event_id": "E-3"})
        self._write(self.rejected, "d.json", self._event(["DESKTOP_1"], "E-4"))

        breakdown = self.snapshot().backlog.rejected_sources

        self.assertEqual(breakdown.by_source, ())
        self.assertEqual(breakdown.unattributed, 4)
        self.assertNotIn("DESKTOP_9", breakdown.describe())

    def test_a_source_carrying_a_newline_cannot_forge_a_line_in_the_view(self):
        """The log-forgery shape (BUG-6) applied to this view: a `source`
        containing a newline would print a second, invented line of the
        breakdown if it were echoed. It is not in SOURCES, so it never is.
        """
        self._write(
            self.rejected, "a.json", self._event("DESKTOP_1\nDESKTOP_2=99", "E-1")
        )

        breakdown = self.snapshot().backlog.rejected_sources

        self.assertEqual(breakdown.unattributed, 1)
        self.assertNotIn("\n", breakdown.describe())

    def test_the_same_event_id_arriving_twice_is_counted_twice(self):
        """Two files are two files. The backlog reports what is on disk, not
        what would survive deduplication — a duplicate still occupies the
        directory and still needs clearing."""
        self._write(self.rejected, "first.json", self._event("DESKTOP_1", "E-SAME"))
        self._write(self.rejected, "second.json", self._event("DESKTOP_1", "E-SAME"))

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.rejected, 2)
        self.assertEqual(backlog.rejected_sources.by_source, (("DESKTOP_1", 2),))

    def test_an_unparseable_transport_file_is_not_given_a_breakdown(self):
        """`unparseable` deliberately has no `_sources` companion: a file
        intake could not parse is one whose `source` cannot be read either,
        so the breakdown would be all-unattributed and say nothing new. It
        must also stay out of the promotable breakdown."""
        self._write(self.transport, "zero.json", "")
        self._write(self.transport, "good.json", self._event("DESKTOP_2", "E-1"))

        backlog = self.snapshot().backlog

        self.assertEqual((backlog.awaiting_intake, backlog.unparseable), (1, 1))
        self.assertEqual(backlog.awaiting_intake_sources.by_source, (("DESKTOP_2", 1),))
        self.assertEqual(backlog.awaiting_intake_sources.total, backlog.awaiting_intake)

    def test_an_empty_backlog_produces_an_empty_breakdown(self):
        backlog = self.snapshot().backlog

        for breakdown in (
            backlog.awaiting_intake_sources,
            backlog.awaiting_collection_sources,
            backlog.rejected_sources,
        ):
            self.assertEqual(breakdown.total, 0)
            self.assertEqual(breakdown.describe(), "")

    def test_the_breakdown_always_adds_back_up_to_the_count(self):
        """The invariant that makes this safe to add: the numbers an
        operator already relied on cannot change meaning."""
        self._write(self.transport, "t1.json", self._event("DESKTOP_1", "E-1"))
        self._write(self.transport, "t2.json", '{"broken"')
        self._write(self.incoming, "i1.json", self._event("DESKTOP_2", "E-2"))
        self._write(self.rejected, "r1.json", self._event("DESKTOP_9", "E-3"))
        self._write(self.rejected, "r2.json", self._event("DESKTOP_3", "E-4"))

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake_sources.total, backlog.awaiting_intake)
        self.assertEqual(
            backlog.awaiting_collection_sources.total, backlog.awaiting_collection
        )
        self.assertEqual(backlog.rejected_sources.total, backlog.rejected)

    def test_attribution_does_not_change_is_clear(self):
        """`is_clear` answers "is anything in flight". Attribution says who,
        never whether."""
        self._write(self.rejected, "r.json", self._event("DESKTOP_1", "E-1"))

        self.assertTrue(self.snapshot().backlog.is_clear)

    def test_describe_lists_desktops_then_the_unattributed_remainder(self):
        self._write(self.rejected, "a.json", self._event("DESKTOP_3", "E-1"))
        self._write(self.rejected, "b.json", self._event("DESKTOP_1", "E-2"))
        self._write(self.rejected, "c.json", "")

        described = self.snapshot().backlog.rejected_sources.describe()

        self.assertEqual(described, "DESKTOP_1=1 DESKTOP_3=1 unattributed=1")


class BacklogAttributionInStatusViewTests(unittest.TestCase):
    """The operator-facing half of BACKLOG E-10.

    The breakdown only pays for itself if it reaches the screen an operator
    actually reads. `_print_company()` prints the COMPANY block and returns
    the ATTENTION lines; both are checked here, because a fact printed in
    the body but missing from ATTENTION is a fact nobody sees on the day it
    matters.
    """

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _runtime(self, module):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        return module.RUNTIME_DIR / "events"

    def _event(self, source, event_id):
        return json.dumps(
            {
                "schema_version": "1.0",
                "event_id": event_id,
                "timestamp": "2026-08-09T10:00:00+09:00",
                "source": source,
                "role": "CTO_BACKEND",
                "project_id": "PRJ",
                "event_type": "MILESTONE_COMPLETED",
                "status": "IN_PROGRESS",
                "summary": "work",
                "history_candidate": True,
            }
        )

    def _write(self, directory, name, text):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text, encoding="utf-8")

    def _run(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_company(NOW)
        return buffer.getvalue(), attention

    def test_the_rejected_attention_line_names_the_desktops(self):
        module = self._load_entrypoint()
        events = self._runtime(module)
        self._write(events / "rejected", "a.json", self._event("DESKTOP_1", "E-1"))
        self._write(events / "rejected", "b.json", self._event("DESKTOP_1", "E-2"))
        self._write(events / "rejected", "c.json", self._event("DESKTOP_3", "E-3"))

        printed, attention = self._run(module)

        rejected_line = next(item for item in attention if "거부한 Event" in item)
        self.assertIn("3건", rejected_line)
        self.assertIn("DESKTOP_1=2", rejected_line)
        self.assertIn("DESKTOP_3=1", rejected_line)
        self.assertIn("rejected", printed)

    def test_the_uncollected_attention_line_merges_both_piles(self):
        """transport and incoming share one ATTENTION sentence, so a Desktop
        appearing in both must be named once with the combined count rather
        than twice."""
        module = self._load_entrypoint()
        events = self._runtime(module)
        self._write(events / "transport", "t.json", self._event("DESKTOP_2", "E-1"))
        self._write(events / "incoming", "i.json", self._event("DESKTOP_2", "E-2"))

        _printed, attention = self._run(module)

        line = next(item for item in attention if "수집되지 않고 남은" in item)
        self.assertIn("DESKTOP_2=2", line)
        self.assertEqual(line.count("DESKTOP_2"), 1)

    def test_an_unattributable_rejected_event_is_reported_as_such(self):
        module = self._load_entrypoint()
        events = self._runtime(module)
        self._write(events / "rejected", "a.json", self._event("DESKTOP_9", "E-1"))

        _printed, attention = self._run(module)

        line = next(item for item in attention if "거부한 Event" in item)
        self.assertIn("출처불명=1", line)
        self.assertNotIn("DESKTOP_9", line)

    def test_a_clean_runtime_adds_no_backlog_line_at_all(self):
        """A note that appears whatever happens is one an operator stops
        reading, so an empty backlog produces no sentence — not one with an
        empty parenthetical. (The silence warning about four Desktops that
        have never reported is a different view's finding and is expected in
        an empty runtime.)"""
        module = self._load_entrypoint()
        self._runtime(module)

        printed, attention = self._run(module)

        self.assertEqual([item for item in attention if "Event" in item and "거부" in item], [])
        self.assertEqual([item for item in attention if "수집되지 않고" in item], [])
        self.assertNotIn("           ", printed)

    def test_the_printed_block_lists_each_pile_separately(self):
        module = self._load_entrypoint()
        events = self._runtime(module)
        self._write(events / "transport", "t.json", self._event("DESKTOP_1", "E-1"))
        self._write(events / "rejected", "r.json", self._event("DESKTOP_4", "E-2"))

        printed, _attention = self._run(module)

        self.assertIn("transport  DESKTOP_1=1", printed)
        self.assertIn("rejected   DESKTOP_4=1", printed)

    def test_the_totals_line_is_unchanged_by_attribution(self):
        """The numbers an operator already relied on keep their exact
        shape — this Sprint adds a line, it does not rewrite one."""
        module = self._load_entrypoint()
        events = self._runtime(module)
        self._write(events / "rejected", "r.json", self._event("DESKTOP_4", "E-2"))

        printed, _attention = self._run(module)

        self.assertIn("backlog: transport=0 incoming=0 rejected=1", printed)


class UnreadableLastRunTests(AgentStatusTestCase):
    """A `last_run` that is not a timestamp was reported as "never ran".

    `agent/state.load_state()` checks that `last_run` is a string and stops
    there. Its sibling `last_successful_collection_date` is additionally
    parsed and rejected if it will not — so of the two date fields in that
    file, one is validated and one is not, and a state file that a human
    edited (docs/11 §71), that a restore brought back from another version,
    or that any writer other than this Agent produced can load cleanly with
    `last_run` set to `2026-08-0`, `yesterday`, or `""`.

    `days_since_last_run()` answers None for all of those, and None was also
    the answer for "there is no last_run at all", so one branch reported
    both. Two costs, both measured:

        the sentence is false     `ops_status.py` prints the `last_run` line
                                  three lines above the ATTENTION block, so
                                  the view contradicted itself
        staleness goes unchecked  an Agent down for weeks got the newcomer's
                                  line instead of "has not run for N day(s)"

    Deliberately NOT fixed by validating the field on load: `last_run` is
    informational, `last_successful_collection_date` is what decides what
    gets collected, and refusing to start over a cosmetic corruption would
    turn a reporting problem into a stopped Agent.
    """

    UNREADABLE = ("2026-08-0", "yesterday", "2026-08-18 09:00 KST", "")

    def _state(self, last_run):
        save_state(
            self.state_path,
            AgentState(
                desktop_id="DESKTOP_1",
                last_successful_collection_date=date(2026, 8, 9),
                last_run=last_run,
            ),
        )

    def test_an_unreadable_last_run_is_not_reported_as_never_having_run(self):
        for value in self.UNREADABLE:
            with self.subTest(last_run=value):
                self._state(value)

                reasons = self.status().needs_attention(NOW)

                self.assertNotIn("agent has never completed a run", reasons)
                self.assertTrue(
                    any("last_run is not a timestamp" in reason for reason in reasons),
                    reasons,
                )

    def test_the_value_itself_is_not_quoted_into_the_attention_line(self):
        """`ops_status.main()`'s ATTENTION block states its messages are built
        from filenames, ids and counts rather than from file *contents*, and
        `last_run` is contents — it crosses no validation beyond `isinstance`
        and can carry anything, including a newline."""
        self._state(chr(10).join(["not-a-time", "! forged ATTENTION line"]))

        for reason in self.status().needs_attention(NOW):
            with self.subTest(reason=reason):
                self.assertNotIn("forged", reason)
                self.assertNotIn(chr(10), reason)

    def test_a_missing_last_run_still_reports_never_having_run(self):
        """The other half: the branch that was always right stays right."""
        self._state(None)

        reasons = self.status().needs_attention(NOW)

        self.assertIn("agent has never completed a run", reasons)
        self.assertFalse(any("not a timestamp" in reason for reason in reasons))

    def test_a_readable_last_run_is_unaffected(self):
        self._state(NOW.isoformat(timespec="seconds"))

        self.assertEqual(self.status().needs_attention(NOW), ())

    def test_the_snapshot_still_carries_the_raw_value_for_the_printed_line(self):
        """The operator's way to the actual value: `ops_status.py` prints
        `snapshot.last_run` verbatim (through `one_line()`), and that is what
        the new message points at."""
        self._state("yesterday")

        self.assertEqual(self.status().last_run, "yesterday")


class FutureCollectionDateTests(AgentStatusTestCase):
    """A collection date in the future is a permanent silent stop that every
    other health signal reports as perfect health.

    `agent.run_once()` never writes one — it caps at `now`. Clock skew on a
    machine since corrected, or a state file restored from a newer backup,
    can. `catchup.pending_dates()` then computes `start > end` and correctly
    returns nothing (it never walks backwards, and that safe behaviour is
    deliberately not touched here). The consequence is that `last_run` is
    recent, `outbox` is empty and `pending_dates` is zero: the Desktop looks
    *better* than a working one, while collecting nothing until the calendar
    reaches that date.

    Detection only — nothing below rewrites state or reprocesses a date.
    """

    def _state(self, collected_through):
        save_state(
            self.state_path,
            AgentState(
                desktop_id="DESKTOP_1",
                last_successful_collection_date=collected_through,
                last_run=NOW.isoformat(timespec="seconds"),
            ),
        )

    def test_a_future_collection_date_is_reported(self):
        self._state(date(2027, 1, 1))

        snapshot = self.status()

        self.assertEqual(snapshot.pending_dates, ())
        self.assertEqual(snapshot.outbox_count, 0)
        reasons = snapshot.needs_attention(NOW)
        self.assertTrue(any("2027-01-01" in reason for reason in reasons))
        self.assertTrue(any("future" in reason for reason in reasons))

    def test_one_day_into_the_future_is_already_reported(self):
        """There is no benign version of this. Tomorrow's date means today
        and tomorrow are both skipped."""
        self._state(NOW.date() + timedelta(days=1))

        self.assertTrue(
            any("future" in reason for reason in self.status().needs_attention(NOW))
        )

    def test_today_is_not_a_false_positive(self):
        """Today is the normal upper bound `run_once()` itself writes — the
        Agent collected everything up to yesterday and recorded it. Flagging
        it would fire on every healthy Desktop."""
        self._state(NOW.date())

        self.assertEqual(
            [r for r in self.status().needs_attention(NOW) if "future" in r], []
        )

    def test_yesterday_is_not_a_false_positive(self):
        self._state(NOW.date() - timedelta(days=1))

        self.assertEqual(
            [r for r in self.status().needs_attention(NOW) if "future" in r], []
        )

    def test_a_never_run_agent_is_not_reported_as_future_dated(self):
        """No state file at all means no date, not a future one."""
        self.assertEqual(
            [r for r in self.status().needs_attention(NOW) if "future" in r], []
        )

    def test_the_future_date_is_reported_ahead_of_softer_reasons(self):
        """Ordered most-serious first, as the method's contract states: a
        Desktop that will never collect again outranks one that has not run
        for a couple of days."""
        self._state(date(2027, 1, 1))
        self.touch(self.outbox, "e1.json")

        reasons = self.status().needs_attention(NOW)

        future_at = next(i for i, r in enumerate(reasons) if "future" in r)
        outbox_at = next(i for i, r in enumerate(reasons) if "not delivered" in r)
        self.assertLess(future_at, outbox_at)

    def test_detection_does_not_change_what_catchup_would_do(self):
        """The safe half of this behaviour stays untouched: nothing is
        reprocessed, and no date is walked backwards."""
        self._state(date(2027, 1, 1))

        snapshot = self.status()

        self.assertEqual(snapshot.pending_dates, ())
        self.assertEqual(snapshot.last_successful_collection_date, date(2027, 1, 1))


class LastRunViewTests(unittest.TestCase):
    """`ops_status.py::_print_last_run()`.

    This view had no test of its own anywhere in the repository, while
    `_print_company` / `_print_agent` / `_print_history` each had a
    dedicated class. It is also the only place an operator learns what the
    last execution actually did.

    The gap that mattered: the loop walked `summary.components`, so it could
    only report steps that were *recorded*. A run that aborted in Backup
    never reaches `recorder.begin(C_DASHBOARD)`, so the Dashboard step
    vanished from the manifest entirely — indistinguishable, on screen, from
    a run where Dashboard was fine. That run's Dashboard row is gone for
    good and is not even queued for retry (BACKLOG A-18), and LAST RUN said
    nothing at all about it.
    """

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _module_with_summary(self, summary):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        path = root / "runtime" / "state" / "last_run.json"
        module.DEFAULT_RUN_SUMMARY_PATH = path
        if summary is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_summary(path, summary)
        return module

    def _summary(self, components):
        """`overall_status` and `exit_code` are derived from the components
        rather than passed in — the manifest cannot disagree with itself, so
        a test cannot construct a contradiction the Runner never could."""
        return RunSummary(
            run_id="RUN-1",
            started_at="2026-08-10T09:00:00+09:00",
            finished_at="2026-08-10T09:01:00+09:00",
            components=tuple(components),
        )

    def _ok(self, name):
        return ComponentResult(name=name, status=ComponentStatus.SUCCESS)

    def _all_nine(self):
        return [self._ok(name) for name in PIPELINE_COMPONENTS]

    def _run(self, module):
        # `NOW`, not wall-clock time. `_summary()` pins `started_at` to a
        # fixed date, so letting `_print_last_run()` default to the real
        # clock made every assertion here depend on what day the suite is
        # run — the Runner-staleness check turned that latent dependency
        # into a failure, which is the useful half of finding it.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_last_run(NOW)
        return buffer.getvalue(), attention

    def test_no_recorded_run_is_reported_without_attention(self):
        module = self._module_with_summary(None)

        printed, attention = self._run(module)

        self.assertIn("아직 기록된 실행이 없다", printed)
        self.assertEqual(attention, [])

    def test_a_corrupted_manifest_is_reported_not_raised(self):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "last_run.json"
        path.write_text("{not json", encoding="utf-8")
        module.DEFAULT_RUN_SUMMARY_PATH = path

        printed, attention = self._run(module)

        self.assertIn("손상된 Run Manifest", printed)
        self.assertEqual(len(attention), 1)

    def test_a_clean_run_of_all_nine_components_needs_no_attention(self):
        module = self._module_with_summary(self._summary(self._all_nine()))

        printed, attention = self._run(module)

        self.assertIn("SUCCESS", printed)
        self.assertEqual(attention, [])

    def test_a_step_that_never_started_is_named(self):
        """The defect this class was written for: Backup aborts, Dashboard
        is never reached, and the manifest carries eight components."""
        components = [
            self._ok(name) for name in PIPELINE_COMPONENTS if name != "dashboard"
        ]
        components = [
            ComponentResult(
                name="backup",
                status=ComponentStatus.FAILED,
                failure=Failure(
                    classification="STEP_ABORTED",
                    severity=Severity.CRITICAL,
                    retryability=Retryability.RETRYABLE,
                    reason="the run aborted inside this step",
                ),
            )
            if c.name == "backup"
            else c
            for c in components
        ]
        module = self._module_with_summary(
            self._summary(components)
        )

        printed, attention = self._run(module)

        self.assertIn("시작되지 못한 단계: dashboard", printed)
        self.assertTrue(any("시작조차 되지 못한 단계" in item for item in attention))
        self.assertTrue(any("dashboard" in item for item in attention))

    def test_several_steps_that_never_started_are_all_named(self):
        module = self._module_with_summary(
            self._summary(
                [self._ok("transport"), self._ok("collector")]
            )
        )

        _printed, attention = self._run(module)

        line = next(item for item in attention if "시작조차" in item)
        for name in PIPELINE_COMPONENTS[2:]:
            with self.subTest(component=name):
                self.assertIn(name, line)

    def test_skipped_is_not_confused_with_never_started(self):
        """SKIPPED means the Runner reached the step and chose not to run it
        — a supported deployment without Notion. Never-started means the
        step was not reached. Reporting the first as the second would put a
        standing ATTENTION entry on every pre-Notion install."""
        components = [
            ComponentResult(name=name, status=ComponentStatus.SKIPPED)
            if name in ("notion_sync", "dashboard")
            else self._ok(name)
            for name in PIPELINE_COMPONENTS
        ]
        module = self._module_with_summary(self._summary(components))

        printed, attention = self._run(module)

        self.assertIn("notion_sync: SKIPPED", printed)
        self.assertEqual([i for i in attention if "시작조차" in i], [])

    def test_a_permanent_failure_reaches_attention(self):
        components = [self._ok(name) for name in PIPELINE_COMPONENTS]
        components[1] = ComponentResult(
            name="collector",
            status=ComponentStatus.FAILED,
            failure=Failure(
                classification="COLLECTOR_ABORTED",
                severity=Severity.CRITICAL,
                retryability=Retryability.PERMANENT,
                reason="disk is read-only",
            ),
        )
        module = self._module_with_summary(
            self._summary(components)
        )

        _printed, attention = self._run(module)

        self.assertTrue(any("재시도로 해결되지 않는다" in item for item in attention))

    def test_a_retryable_failure_alone_does_not_raise_a_standing_alert(self):
        """A RETRYABLE failure is what the next scheduled run is for. Listing
        it would create an ATTENTION entry that clears itself."""
        components = [self._ok(name) for name in PIPELINE_COMPONENTS]
        components[-2] = ComponentResult(
            name="backup",
            status=ComponentStatus.FAILED,
            failure=Failure(
                classification="BACKUP_PENDING",
                severity=Severity.DEGRADED,
                retryability=Retryability.RETRYABLE,
                reason="remote unreachable",
            ),
        )
        module = self._module_with_summary(
            self._summary(components)
        )

        _printed, attention = self._run(module)

        self.assertEqual([i for i in attention if "재시도로 해결되지 않는다" in i], [])

    def test_the_expected_component_list_matches_the_runner(self):
        """`PIPELINE_COMPONENTS` is derived from the Runner's own artifact
        table, so it cannot drift from the steps that actually record."""
        import app.runner as runner

        self.assertEqual(PIPELINE_COMPONENTS, tuple(runner._ARTIFACT_REFS))
        self.assertEqual(len(PIPELINE_COMPONENTS), 9)


class LastRunLockStuckTests(unittest.TestCase):
    """A Runner lock held far longer than any real run.

    `_is_process_running()` asks whether *a* process has the recorded pid,
    not whether it is the one that took the lock. A Runner killed by a power
    cut leaves its pid in the file; once the OS reassigns that number, every
    later run is denied the lock and skips — silently and permanently, since
    §27 forbids judging staleness by elapsed time alone.

    Nothing here judges staleness or touches the lock. It reports the one
    fact that is certain: this lock has been held for an implausible time,
    which is worth a look whether the cause is a genuinely long run or a
    ghost pid.
    """

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _module(self):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        module.DEFAULT_RUN_SUMMARY_PATH = root / "runtime" / "state" / "last_run.json"
        return module

    def _hold_lock(self, module, *, hours_ago):
        path = module._runner_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        acquired = datetime.now().astimezone() - timedelta(hours=hours_ago)
        path.write_text(
            json.dumps(
                {
                    "process_id": os.getpid(),
                    "created_at": acquired.isoformat(timespec="seconds"),
                }
            ),
            encoding="utf-8",
        )
        return acquired

    def _run(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_last_run()
        return buffer.getvalue(), attention

    def test_no_lock_says_nothing(self):
        module = self._module()

        printed, attention = self._run(module)

        self.assertNotIn("Runner Lock", printed)
        self.assertEqual(attention, [])

    def test_a_freshly_taken_lock_is_shown_but_not_flagged(self):
        """A run in progress is normal and must not become an alert."""
        module = self._module()
        self._hold_lock(module, hours_ago=0)

        printed, attention = self._run(module)

        self.assertIn("Runner Lock", printed)
        self.assertEqual([i for i in attention if "Lock" in i], [])

    def test_a_lock_held_past_the_threshold_is_flagged(self):
        module = self._module()
        self._hold_lock(module, hours_ago=module.LOCK_STUCK_AFTER_HOURS + 1)

        _printed, attention = self._run(module)

        self.assertTrue(any("Runner Lock" in item for item in attention))
        self.assertTrue(any("PID가 재사용" in item for item in attention))

    def test_a_lock_from_a_dead_process_is_not_flagged(self):
        """The next Runner takes that one over on its own (§27); flagging it
        would be a standing alert for a self-clearing condition."""
        module = self._module()
        path = module._runner_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"process_id": 999999, "created_at": "2020-01-01T00:00:00+09:00"}),
            encoding="utf-8",
        )

        printed, attention = self._run(module)

        self.assertNotIn("Runner Lock", printed)
        self.assertEqual([i for i in attention if "Lock" in i], [])

    def test_reporting_the_lock_never_takes_or_clears_it(self):
        module = self._module()
        self._hold_lock(module, hours_ago=5)
        before = module._runner_lock_path().read_bytes()

        self._run(module)

        self.assertEqual(module._runner_lock_path().read_bytes(), before)


class NaiveTimestampInProcessedEventsTests(CompanyActivityTestCase):
    """An Event in `processed/` whose timestamp has no UTC offset.

    `_before()` promises in its own docstring that "a hand-corrupted Event
    affects only its own ordering rather than collapsing the whole
    comparison", and guarded that with `except ValueError`. There are two
    ways the comparison fails, though: a value that does not parse
    (`ValueError`) and a naive/aware mix
    (`TypeError: can't compare offset-naive and offset-aware datetimes`).
    Only the first was caught.

    `validate_event()` requires an offset, so this cannot arrive through the
    Collector — but nothing re-validates a file already in `processed/`. A
    legacy Event, a hand edit, or a restore from another tool is naive, and
    that is precisely the "damaged evidence" this view exists to survive:
    one such file took the entire COMPANY view of `ops_status.py` down.

    Same defect family as the Notion Late Event guard
    (`test_notion_sync.py::LateEventGuardTimezoneTests`) — two
    `fromisoformat()` results compared without allowing for one being naive.
    """

    def _write(self, name, timestamp, source="DESKTOP_1"):
        self.processed.mkdir(parents=True, exist_ok=True)
        (self.processed / name).write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "event_id": name,
                    "timestamp": timestamp,
                    "source": source,
                    "role": "CTO_BACKEND",
                    "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "s",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

    def test_a_naive_timestamp_does_not_collapse_the_whole_view(self):
        self._write("aware.json", "2026-08-05T10:00:00+09:00")
        self._write("naive.json", "2026-08-06T10:00:00")

        snapshot = self.snapshot()

        activity = snapshot.for_source("DESKTOP_1")
        self.assertEqual(activity.event_count, 2)
        self.assertEqual(snapshot.unreadable_events, ())

    def test_the_other_desktops_are_still_reported(self):
        """The blast radius that mattered: one bad file on one Desktop was
        taking every Desktop's line with it."""
        self._write("naive.json", "2026-08-06T10:00:00", source="DESKTOP_1")
        self._write("ok.json", "2026-08-05T10:00:00+09:00", source="DESKTOP_2")

        snapshot = self.snapshot()

        self.assertEqual(snapshot.for_source("DESKTOP_2").event_count, 1)
        self.assertEqual(
            snapshot.for_source("DESKTOP_2").last_event_at, "2026-08-05T10:00:00+09:00"
        )

    def test_a_naive_only_desktop_still_reports_bounds(self):
        self._write("n1.json", "2026-08-05T10:00:00")
        self._write("n2.json", "2026-08-07T10:00:00")

        activity = self.snapshot().for_source("DESKTOP_1")

        self.assertEqual(activity.first_event_at, "2026-08-05T10:00:00")
        self.assertEqual(activity.last_event_at, "2026-08-07T10:00:00")

    def test_an_unparseable_timestamp_still_falls_back_as_before(self):
        """The half that already worked must keep working."""
        self._write("good.json", "2026-08-05T10:00:00+09:00")
        self._write("bad.json", "not-a-timestamp")

        activity = self.snapshot().for_source("DESKTOP_1")

        self.assertEqual(activity.event_count, 2)

    def test_the_whole_company_view_renders_with_a_naive_event(self):
        """End to end through the operator's actual entry point."""
        import contextlib
        import importlib.util

        self._write("naive.json", "2026-08-06T10:00:00")

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_naive", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.processed.parent

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module._print_company(NOW)

        self.assertIn("DESKTOP_1", buffer.getvalue())

class LastRunUnclearableLockTests(unittest.TestCase):
    """The operator-facing half of the BUG-42 detection.

    `try_acquire_lock()` answers False for an unclearable stale lock, and
    False means "another run holds it" — so the Runner skips on schedule,
    forever, writing no manifest (docs/14 §7). Every automatic signal reads
    healthy. This is the one place that can say otherwise.
    """

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_rolock", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _module_with_lock(self, payload, *, read_only):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        module.DEFAULT_RUN_SUMMARY_PATH = root / "runtime" / "runs" / "last_run.json"
        lock = module._runner_lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps(payload), encoding="utf-8")
        if read_only:
            os.chmod(lock, stat.S_IREAD)
            self.addCleanup(self._restore, lock)
        return module

    @staticmethod
    def _restore(path):
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass

    def _run(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_last_run()
        return buffer.getvalue(), attention

    def test_an_unclearable_stale_lock_reaches_attention(self):
        module = self._module_with_lock(
            {"process_id": 999999, "created_at": "2020-01-01T00:00:00+09:00"},
            read_only=True,
        )

        printed, attention = self._run(module)

        self.assertIn("제거할 수 없음", printed)
        line = next(item for item in attention if "Runner Lock" in item)
        self.assertIn("읽기 전용", line)
        self.assertIn("건너뛰어진다", line)

    def test_an_ordinary_stale_lock_says_nothing(self):
        """The next run takes that one over; reporting it would fire on
        every crash recovery."""
        module = self._module_with_lock(
            {"process_id": 999999, "created_at": "2020-01-01T00:00:00+09:00"},
            read_only=False,
        )

        printed, attention = self._run(module)

        self.assertNotIn("제거할 수 없음", printed)
        self.assertEqual([i for i in attention if "Runner Lock" in i], [])

    def test_a_live_lock_still_reports_only_its_hold_time(self):
        """The C19 detector and the C23 one must not double-report."""
        module = self._module_with_lock(
            {
                "process_id": os.getpid(),
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            read_only=False,
        )

        printed, attention = self._run(module)

        self.assertIn("Runner Lock : 보유 중", printed)
        self.assertNotIn("제거할 수 없음", printed)
        self.assertEqual([i for i in attention if "Runner Lock" in i], [])

    def test_reporting_it_does_not_touch_the_lock(self):
        module = self._module_with_lock(
            {"process_id": 999999, "created_at": "2020-01-01T00:00:00+09:00"},
            read_only=True,
        )
        lock = module._runner_lock_path()
        before = lock.read_bytes()

        self._run(module)

        self.assertTrue(lock.exists())
        self.assertEqual(lock.read_bytes(), before)
        self.assertFalse(os.access(lock, os.W_OK))

class FutureDatedTransportFileTests(CompanyActivityTestCase):
    """BUG-30's invisibility, closed. The stall itself is left alone.

    `run_intake._is_stable()` decides a file has finished arriving with
    `(now - mtime) >= stable_after_seconds`, which assumes mtime is in the
    past. OneDrive preserves the *sending* Desktop's mtime, so a Desktop
    whose clock runs fast stamps files in the future and the subtraction
    goes negative — the file is held until wall-clock time catches up, which
    can be a day or a year.

    Measured before: three consecutive runs, `moved=0` and
    `skipped_not_stable=1` every time, `transport=1` on the operator's
    screen every time, and nothing anywhere saying why. `skipped_not_stable`
    does reach the Run Manifest, but `_print_last_run()` prints only
    components that are NOT SUCCESS, and transport succeeds — so the one
    number that explains the stall never reaches a screen.

    That is the standing-alert-with-no-explanation shape `IntakeBacklog`
    already names in its own docstring, written for `unparseable`: "An alert
    that cannot clear is worse than no alert ... a permanent entry trains
    people to skim past it."

    What is NOT done here: `future_dated` is reported, never subtracted from
    `awaiting_intake` and never allowed to change `is_clear`. Whether such a
    file counts as "in flight" is the judgement BUG-30 records as open —
    `unparseable` was excluded only because those files are provably parked
    forever, and these are not. The missing information was never the
    number; it was the reason the number does not move.
    """

    def _write(self, name, *, seconds_ahead):
        self.transport.mkdir(parents=True, exist_ok=True)
        path = self.transport / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "event_id": name,
                    "timestamp": "2026-08-05T10:00:00+09:00",
                    "source": "DESKTOP_2",
                    "role": "CMO",
                    "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "s",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )
        stamp = time.time() + seconds_ahead
        os.utime(path, (stamp, stamp))
        return path

    def test_a_future_dated_file_is_counted(self):
        self._write("skew.json", seconds_ahead=86400)

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.future_dated, 1)
        self.assertEqual(backlog.awaiting_intake, 1)

    def test_an_ordinary_pending_file_is_not_counted(self):
        self._write("normal.json", seconds_ahead=-3600)

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.future_dated, 0)
        self.assertEqual(backlog.awaiting_intake, 1)

    def test_the_count_does_not_change_awaiting_intake_or_is_clear(self):
        """Reported, not reclassified — the open judgement stays open."""
        self._write("skew.json", seconds_ahead=86400)

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 1)
        self.assertFalse(backlog.is_clear)
        self.assertEqual(backlog.awaiting_intake_sources.total, backlog.awaiting_intake)

    def test_it_agrees_with_what_intake_actually_does(self):
        """The count is only worth anything if it predicts the stall."""
        from transport.intake import run_intake

        self._write("skew.json", seconds_ahead=86400)
        self._write("ready.json", seconds_ahead=-3600)

        for _ in range(3):
            summary = run_intake(
                transport_dir=self.transport,
                incoming_dir=self.incoming,
                processed_dir=self.processed,
                rejected_dir=self.rejected,
            )
            self.assertEqual(summary.skipped_not_stable, ("skew.json",))

        self.assertEqual(self.snapshot().backlog.future_dated, 1)

    def test_an_unparseable_future_dated_file_is_counted_in_both(self):
        """The two conditions are independent and a file can have both."""
        self.transport.mkdir(parents=True, exist_ok=True)
        path = self.transport / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        stamp = time.time() + 86400
        os.utime(path, (stamp, stamp))

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.unparseable, 1)
        self.assertEqual(backlog.future_dated, 1)
        self.assertEqual(backlog.awaiting_intake, 0)

    def test_an_empty_transport_directory_reports_zero(self):
        self.assertEqual(self.snapshot().backlog.future_dated, 0)


class FutureDatedInStatusViewTests(unittest.TestCase):
    """The sentence an operator actually reads."""

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_skew", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _module_with(self, *, seconds_ahead):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        transport = module.RUNTIME_DIR / "events" / "transport"
        transport.mkdir(parents=True)
        path = transport / "skew.json"
        path.write_text(json.dumps({"event_id": "SKEW-1"}), encoding="utf-8")
        stamp = time.time() + seconds_ahead
        os.utime(path, (stamp, stamp))
        return module

    def _run(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_company(datetime.now().astimezone())
        return buffer.getvalue(), attention

    def test_the_reason_is_attached_to_the_existing_sentence(self):
        printed, attention = self._run(self._module_with(seconds_ahead=86400))

        self.assertIn("future_dated=1", printed)
        line = next(item for item in attention if "수집되지 않고" in item)
        self.assertIn("시계", line)
        self.assertIn("1건", line)

    def test_an_ordinary_backlog_gets_no_extra_clause(self):
        """A clause that always appears is one nobody reads."""
        printed, attention = self._run(self._module_with(seconds_ahead=-3600))

        self.assertNotIn("future_dated", printed)
        line = next(item for item in attention if "수집되지 않고" in item)
        self.assertNotIn("시계", line)


class NameCollisionInIncomingTests(CompanyActivityTestCase):
    """BUG-43's invisibility, closed. The stuck loop itself is left alone.

    `collector/runtime.run_once()` refuses a destination whose name is
    already taken and leaves the file in `incoming/`. The verdict does not
    matter — ACCEPTED and DUPLICATE both target `processed/` — so a name
    collision is a permanent FAILED on every run.

    Measured over three consecutive runs: `accepted=0 failed=1` each time,
    the file still in `incoming/` each time. `ops_status.py` reported
    `incoming=1` each time, correctly, and said nothing about why. Its own
    ATTENTION line therefore stood forever with no way for an operator to
    learn that no future run would clear it.

    BUG-43's docstring calls the condition "at least visible" because
    `collector_summary.failed` is printed by `run_company_ops.py` — but that
    goes to stdout, which Task Scheduler does not capture, and the Run
    Manifest records it as a *metric on a SUCCESS component*, which
    `_print_last_run()` deliberately does not print. The same docstring says
    "the exit code is still 0 (BUG-36)"; BUG-36 was fixed, and the exit code
    is still 0 here — because the collector component is deliberately
    SUCCESS (docs/03 §53 per-file isolation). That is the right call and not
    what this closes.

    What is NOT done: `awaiting_collection` still counts these files and
    `is_clear` is untouched. Reconciling the two notions of "already
    handled" — rebuild state from `processed/`, or treat a name collision as
    a duplicate rather than a failure — is the decision BUG-43 records.
    """

    def _event(self, event_id):
        return json.dumps(
            {
                "schema_version": "1.0",
                "event_id": event_id,
                "timestamp": "2026-08-05T10:00:00+09:00",
                "source": "DESKTOP_1",
                "role": "CTO_BACKEND",
                "project_id": "P",
                "event_type": "MILESTONE_COMPLETED",
                "status": "IN_PROGRESS",
                "summary": "s",
                "history_candidate": True,
            }
        )

    def _write(self, directory, name, event_id="E-1"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(self._event(event_id), encoding="utf-8")

    def test_a_name_already_in_processed_is_counted(self):
        self._write(self.processed, "STUCK.json")
        self._write(self.incoming, "STUCK.json")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.name_collision, 1)
        self.assertEqual(backlog.awaiting_collection, 1)

    def test_a_name_already_in_rejected_is_counted_too(self):
        """`run_once()` checks the destination it is about to write, and a
        rejected Event's name blocks the rejected path the same way."""
        self._write(self.rejected, "STUCK.json")
        self._write(self.incoming, "STUCK.json")

        self.assertEqual(self.snapshot().backlog.name_collision, 1)

    def test_an_ordinary_pending_file_is_not_counted(self):
        self._write(self.incoming, "FRESH.json")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.name_collision, 0)
        self.assertEqual(backlog.awaiting_collection, 1)

    def test_the_count_does_not_change_awaiting_collection_or_is_clear(self):
        self._write(self.processed, "STUCK.json")
        self._write(self.incoming, "STUCK.json")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_collection, 1)
        self.assertFalse(backlog.is_clear)
        self.assertEqual(
            backlog.awaiting_collection_sources.total, backlog.awaiting_collection
        )

    def test_it_predicts_what_the_collector_actually_does(self):
        """A counter that does not match the step it explains is worse than
        none. Three runs, three identical failures, and the count says so
        before any of them."""
        from collector.collector import Collector
        from collector.runtime import run_once as collector_run_once
        from collector.seen_store import InMemorySeenEventStore

        self._write(self.processed, "STUCK.json")
        self._write(self.incoming, "STUCK.json")
        self._write(self.incoming, "FRESH.json", event_id="E-2")

        self.assertEqual(self.snapshot().backlog.name_collision, 1)

        for _ in range(3):
            summary = collector_run_once(
                collector=Collector(seen_store=InMemorySeenEventStore()),
                incoming_dir=self.incoming,
                processed_dir=self.processed,
                rejected_dir=self.rejected,
                log_path=self.root / "collector.log",
            )
            self.assertEqual(summary.failed, 1)
            self.assertTrue((self.incoming / "STUCK.json").exists())

    def test_several_collisions_are_counted_separately(self):
        for index in range(3):
            self._write(self.processed, f"S{index}.json")
            self._write(self.incoming, f"S{index}.json")
        self._write(self.incoming, "FRESH.json", event_id="E-9")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.name_collision, 3)
        self.assertEqual(backlog.awaiting_collection, 4)

    def test_an_empty_incoming_directory_reports_zero(self):
        self._write(self.processed, "ANY.json")

        self.assertEqual(self.snapshot().backlog.name_collision, 0)


class StuckIncomingInStatusViewTests(unittest.TestCase):
    """The sentence an operator reads when the backlog will never clear."""

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_collide", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _module(self, *, collide):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        events = module.RUNTIME_DIR / "events"
        for name in ("incoming", "processed", "rejected"):
            (events / name).mkdir(parents=True)
        payload = json.dumps({"event_id": "E-1", "source": "DESKTOP_1"})
        (events / "incoming" / "S.json").write_text(payload, encoding="utf-8")
        if collide:
            (events / "processed" / "S.json").write_text(payload, encoding="utf-8")
        return module

    def _run(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_company(datetime.now().astimezone())
        return buffer.getvalue(), attention

    def test_a_collision_explains_itself_in_the_backlog_sentence(self):
        printed, attention = self._run(self._module(collide=True))

        self.assertIn("name_collision=1", printed)
        line = next(item for item in attention if "수집되지 않고" in item)
        self.assertIn("재실행으로 해결되지 않는다", line)

    def test_an_ordinary_backlog_gets_no_extra_clause(self):
        printed, attention = self._run(self._module(collide=False))

        self.assertNotIn("name_collision", printed)
        line = next(item for item in attention if "수집되지 않고" in item)
        self.assertNotIn("재실행으로 해결되지 않는다", line)


class WorkingCopySecretExposureInStatusTests(unittest.TestCase):
    """E-21's detection half: somebody has to be looking at the directory
    git actually commits.

    `backup.run_once()` gates on **Local Master** while `git add -A` commits
    the **Working Copy**, so a secret-shaped file that reached the Working
    Copy by any route other than sync is pushed with the backup reporting
    BACKUP_SUCCESS. Nothing looked at that directory.

    No gate changes here. `scan_for_secrets()` is applied with the same
    decided list of names it already uses for Master, to a directory nobody
    was checking. The report is late by construction — a scheduled Backup
    may already have pushed — but late is the difference between rotating a
    leaked credential and never learning it left.
    """

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_wc", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _module(self, *, plant=()):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        working_copy = module.RUNTIME_DIR / "backup_working_copy"
        (working_copy / "daily").mkdir(parents=True)
        (working_copy / "daily" / "2026-08-05.md").write_text("# h", encoding="utf-8")
        (module.RUNTIME_DIR / "state").mkdir(parents=True, exist_ok=True)
        for name in plant:
            target = working_copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
        return module

    def _run(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), attention

    def test_a_secret_shaped_file_in_the_working_copy_reaches_attention(self):
        _printed, attention = self._run(self._module(plant=(".env", "notes/id_rsa")))

        line = next(item for item in attention if "Working Copy" in item)
        self.assertIn("2건", line)
        self.assertIn(".env", line)
        self.assertIn("자격증명 교체", line)

    def test_a_clean_working_copy_says_nothing(self):
        _printed, attention = self._run(self._module())

        self.assertEqual([item for item in attention if "Working Copy" in item], [])

    def test_an_absent_working_copy_is_not_an_error(self):
        """Desktop 1/2/3 have no Working Copy at all."""
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"

        _printed, attention = self._run(module)

        self.assertEqual([item for item in attention if "Working Copy" in item], [])

    def test_it_uses_the_same_predicate_the_gate_uses(self):
        """A second opinion about what counts as a secret would let this
        view and the gate disagree — the class of contradiction this
        codebase keeps closing."""
        import inspect

        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("scan_for_secrets(working_copy)", source)
        self.assertIn("from backup.working_copy import scan_for_secrets", source)

    def test_reporting_does_not_touch_the_working_copy(self):
        module = self._module(plant=(".env",))
        working_copy = module.RUNTIME_DIR / "backup_working_copy"
        before = sorted(str(p.relative_to(working_copy)) for p in working_copy.rglob("*"))

        self._run(module)

        after = sorted(str(p.relative_to(working_copy)) for p in working_copy.rglob("*"))
        self.assertEqual(before, after)


class GuardsAddedButNeverExecutedTests(unittest.TestCase):
    """Two defensive branches added in C19/C24 that no test had ever run.

    Found by the same never-executed-line trace that produced C22's
    inventory, turned on the code this project added rather than the code it
    inherited. A guard nobody has executed is a guess, and the whole point of
    C17's lesson is that "written" and "works" are different claims.

    Both are real races rather than paranoia:

        `_count_transport`'s stat guard   `run_intake()` MOVES files out of
                                          `transport/` while `ops_status.py`
                                          is listing it, and the module's
                                          contract is that it answers while a
                                          Runner is working
        `drain_pending`'s save guard      the queue file is rewritten after
                                          the retries have already happened,
                                          so a failure there loses the record
                                          of work that did occur
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_transport_file_that_vanishes_mid_scan_is_not_fatal(self):
        """`ops_status.py` promises to be safe to run while a Runner works,
        and `run_intake()` is moving files out of `transport/` at that very
        moment. A path that no longer exists when it is stat'd must not
        raise out of the view."""
        import app.desktop_activity as activity

        transport = self.root / "transport"
        transport.mkdir()
        real = transport / "real.json"
        real.write_text(json.dumps({"source": "DESKTOP_1"}), encoding="utf-8")
        vanished = transport / "gone.json"

        # mtime resolution: `_count_transport()` takes its own `now = time.time()`
        # snapshot internally, after this write. Normally that snapshot lands
        # safely after the file's real mtime, but with no gap the two land
        # close enough in wall-clock time to invert under load (observed
        # flake in the full suite) — the same trap `time.sleep(1.1)  # mtime
        # resolution` already guards elsewhere in this file.
        time.sleep(1.1)

        original = activity._json_paths

        def _with_a_vanished_entry(directory):
            paths = original(directory)
            if Path(directory) == transport:
                return sorted(paths + [vanished])
            return paths

        activity._json_paths = _with_a_vanished_entry
        self.addCleanup(setattr, activity, "_json_paths", original)

        (
            promotable,
            unparseable,
            future_dated,
            incomplete,
            already_collected,
            suppressed,
            breakdown,
        ) = activity._count_transport(transport)

        # The vanished entry is counted as unparseable, not crashed on, and
        # the real file is still attributed.
        self.assertEqual(promotable, 1)
        self.assertEqual(unparseable, 1)
        self.assertEqual(future_dated, 0)
        self.assertEqual(incomplete, 0)
        self.assertEqual(already_collected, 0)
        self.assertEqual(suppressed, 0)
        self.assertEqual(breakdown.by_source, (("DESKTOP_1", 1),))

    def test_the_whole_company_view_survives_the_same_race(self):
        import app.desktop_activity as activity

        transport = self.root / "transport"
        transport.mkdir()
        (transport / "real.json").write_text(
            json.dumps({"source": "DESKTOP_2"}), encoding="utf-8"
        )
        original = activity._json_paths

        def _with_a_vanished_entry(directory):
            paths = original(directory)
            if Path(directory) == transport:
                return sorted(paths + [transport / "gone.json"])
            return paths

        activity._json_paths = _with_a_vanished_entry
        self.addCleanup(setattr, activity, "_json_paths", original)

        snapshot = activity.read_company_activity(
            processed_dir=self.root / "processed",
            transport_dir=transport,
            incoming_dir=self.root / "incoming",
            rejected_dir=self.root / "rejected",
        )

        self.assertEqual(snapshot.backlog.awaiting_intake, 1)
        self.assertEqual(snapshot.backlog.unparseable, 1)

    def test_a_pending_queue_that_cannot_be_rewritten_reports_why(self):
        """`drain_pending()` retries first and saves afterwards, so a save
        failure means work happened that the file no longer records. The
        reason has to survive — that is what `last_reason` is for.

        `save_all` is replaced rather than the file sabotaged. The obvious
        trick (turn the file into a directory) makes `load_pending()` fail
        first and returns before the retry loop, so it exercises the
        corruption branch instead — this test passed that way at first,
        asserting the right-looking string through the wrong path. Reaching
        the save branch honestly needs the load to succeed and only the
        write to fail.
        """
        from notion import dashboard_pending
        from notion.dashboard_pending import drain_pending, save_pending

        path = self.root / "dashboard_pending.json"
        save_pending(path, run_id="R-1", properties={"i": 1})

        class Working:
            def find_or_create_by_title(self, *, property_name, value, properties):
                return {"id": "page-1"}

        original = dashboard_pending.save_all

        def _refuse(*args, **kwargs):
            raise OSError("simulated: the state directory is read-only")

        dashboard_pending.save_all = _refuse
        self.addCleanup(setattr, dashboard_pending, "save_all", original)

        result = drain_pending(path, Working())

        # The retry really happened — that is the point of keeping a reason.
        self.assertEqual(result.recorded, 1)
        self.assertIsNotNone(result.last_reason)
        self.assertIn("could not update the pending file", result.last_reason)
        self.assertIn("read-only", result.last_reason)

    def test_a_corrupt_queue_file_reports_its_own_reason_not_the_save_one(self):
        """The path the first attempt at the test above actually took, kept
        because the two must stay distinguishable: a queue file that cannot
        be READ returns before any retry, so its reason is corruption rather
        than a failed write."""
        from notion.dashboard_pending import drain_pending, save_pending

        path = self.root / "dashboard_pending.json"
        save_pending(path, run_id="R-1", properties={"i": 1})
        path.unlink()
        path.mkdir()

        class Working:
            def find_or_create_by_title(self, *, property_name, value, properties):
                return {"id": "page-1"}

        result = drain_pending(path, Working())

        self.assertEqual(result.recorded, 0)
        self.assertIn("corrupted", result.last_reason)
        self.assertNotIn("could not update", result.last_reason)

    def test_a_save_failure_never_masks_an_earlier_notion_failure(self):
        """Precedence, pinned at the source.

        Reaching this behaviourally needs a queue file that reads fine and
        then refuses to be rewritten in the same call — replacing it with a
        directory (the obvious trick) makes `load_pending()` fail first and
        returns before the retry loop ever runs, which the corruption test
        in `test_notion_dashboard.py` already covers.

        What matters is the precedence itself: an operator needs to know
        Notion refused the record, not that a file write failed afterwards.
        `last_reason or ...` is what guarantees the first cause wins, and a
        plain assignment there would silently reverse it.
        """
        import inspect

        from notion import dashboard_pending

        source = inspect.getsource(dashboard_pending.drain_pending)
        self.assertIn("last_reason = last_reason or", source)
        self.assertNotIn("last_reason = f\"could not update", source)


class GitignoredWorkingCopyFileTests(unittest.TestCase):
    """A defect in the C24 check itself, found in C26.

    C24 added "the Working Copy holds a secret-shaped file" to ATTENTION,
    on the strength of `scan_for_secrets()`. That predicate answers "is this
    a secret-shaped filename", which is the right question for the Backup
    gate and the wrong one for this report: what actually reaches the remote
    is what `git add -A` stages, and git ignores whatever `.gitignore` says.

    docs/08 §28 asks a Backup Repo to carry a `.gitignore` listing exactly
    `.env`, `.env.*`, `*.tmp`, `*.log`. Measured: an operator who follows
    that advice — the correct remediation — still saw
    "이 파일들은 ... 원격에 올라간다" on every run, for a file git was
    correctly refusing to commit. A standing alert, for a correctly
    configured machine, that no action could clear.

    That is the failure mode `IntakeBacklog`'s own docstring names ("An
    alert that cannot clear is worse than no alert ... a permanent entry
    trains people to skim past it"), introduced by the C24 check. It is
    worth saying plainly that this project's own instrumentation produced
    it.

    The fix asks git rather than parsing `.gitignore` — a second reader of
    git's rules would be the same disagreement the codebase closes elsewhere
    by reusing the authority.
    """

    TOKEN = "ntn_" + "G" * 40

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "state").mkdir(parents=True)
        self.wc = self.runtime / "backup_working_copy"
        (self.wc / "daily").mkdir(parents=True)
        (self.wc / "daily" / "2026-08-05.md").write_text("# h\n", encoding="utf-8")

    def _git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.wc,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _init_repo(self, *, gitignore=None):
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Gitignore Test")
        if gitignore is not None:
            (self.wc / ".gitignore").write_text(gitignore, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")

    def _plant(self, name):
        target = self.wc / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"TOKEN={self.TOKEN}\n", encoding="utf-8")

    def _warnings(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_gitignore", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return [item for item in attention if "Working Copy" in item]

    SECTION_28 = ".env\n.env.*\n*.tmp\n*.log\n__pycache__/\n.cache/\n"

    def test_a_gitignored_secret_is_not_reported(self):
        """The defect. Following docs/08 §28 must not produce a permanent
        false alarm."""
        self._init_repo(gitignore=self.SECTION_28)
        self._plant(".env")

        self.assertEqual(self._warnings(), [])

    def test_git_really_does_refuse_to_commit_it(self):
        """The premise of the test above, checked rather than assumed."""
        self._init_repo(gitignore=self.SECTION_28)
        self._plant(".env")

        self._git("add", "-A")
        self._git("commit", "-m", "second")

        committed = self._git("ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        self.assertNotIn(".env", committed)

    def test_a_secret_that_is_not_ignored_is_still_reported(self):
        """The guard must not swallow the real exposure. §28's list does not
        cover a private key placed in a subdirectory."""
        self._init_repo(gitignore=self.SECTION_28)
        self._plant("notes/id_rsa")

        warnings = self._warnings()

        self.assertTrue(warnings)
        self.assertIn("id_rsa", warnings[0])

    def test_only_the_unignored_ones_are_named(self):
        self._init_repo(gitignore=self.SECTION_28)
        self._plant(".env")
        self._plant("notes/id_rsa")

        warnings = self._warnings()

        self.assertTrue(warnings)
        self.assertIn("id_rsa", warnings[0])
        self.assertNotIn(".env", warnings[0])
        self.assertIn("1건", warnings[0])

    def test_without_a_gitignore_everything_is_still_reported(self):
        """The case C24 measured, unchanged."""
        self._init_repo()
        self._plant(".env")

        warnings = self._warnings()

        self.assertTrue(warnings)
        self.assertIn(".env", warnings[0])

    def test_a_tracked_secret_is_reported_even_if_a_rule_would_ignore_it(self):
        """git keeps committing a file it already tracks, whatever
        `.gitignore` says afterwards — so the report has to follow git, not
        the rules file."""
        self._init_repo()
        self._plant(".env")
        self._git("add", "-A")
        self._git("commit", "-m", "tracked")
        (self.wc / ".gitignore").write_text(self.SECTION_28, encoding="utf-8")

        warnings = self._warnings()

        self.assertTrue(warnings, "a tracked secret must still be reported")
        self.assertIn(".env", warnings[0])

    def test_a_working_copy_that_is_not_a_repository_over_reports(self):
        """Fail-safe. A probe that cannot answer must not hide an exposure —
        this is also the path every earlier test in this suite takes, since
        their fixtures never run `git init`."""
        self._plant(".env")

        warnings = self._warnings()

        self.assertTrue(warnings)
        self.assertIn(".env", warnings[0])


class GitAwareProbeShapeTests(unittest.TestCase):
    """Structural half of the fix."""

    SOURCE = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
        encoding="utf-8"
    )

    def test_the_probe_asks_git_rather_than_parsing_gitignore(self):
        self.assertIn("ls-files", self.SOURCE)
        self.assertIn("--exclude-standard", self.SOURCE)
        self.assertNotIn('open(".gitignore"', self.SOURCE)

    def test_the_probe_returns_the_candidates_unchanged_on_failure(self):
        """Fail-safe direction, pinned: over-report rather than hide."""
        import inspect
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_shape", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        source = inspect.getsource(module._would_reach_the_commit)
        self.assertIn("return candidates", source)

    def test_it_does_nothing_when_there_is_nothing_to_check(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_empty", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(
            module._would_reach_the_commit(Path("/nonexistent"), ()), ()
        )


class AbandonedStagingFileReportingTests(unittest.TestCase):
    """What the operator is told about a write that never committed.

    An atomic writer killed between `mkstemp` and `os.replace` leaves a
    `.tmp-*` file in the directory it was writing into, and nothing in this
    repository ever removes one. `IncompleteWriteInvariantTests` covers the
    pipeline side — no step consumes one as an artifact. This class covers
    the reporting side, where skipping a file silently is its own defect:
    the file still occupies the directory, and a view that simply stopped
    counting it would leave garbage accumulating with nothing saying so.

    Two properties, and they pull in opposite directions:

      * it must NOT be counted as work in flight — `awaiting_intake`, a
        daily file, a Candidate awaiting review. Each of those is a number
        an operator acts on, and no action reduces a count that includes an
        abandoned staging file, which is the alert-that-cannot-clear shape
        `IntakeBacklog`'s docstring and C26 both warn about;
      * it must still be NAMED, with the one instruction that differs from
        every other line in ATTENTION: this is not an Event waiting for
        something, it is garbage, and deleting it is safe.

    Measured before the fix: a single `.tmp-abc.json` in `transport/` held
    `awaiting_intake` at 1 and `is_clear` at False across consecutive clean
    runs, and a `.tmp-abc.md` in `local_master/daily/` was counted as a day
    of Company History that does not exist.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        for relative in (
            "events/transport",
            "events/incoming",
            "events/processed",
            "events/rejected",
            "history_candidates/review",
            "local_master/daily",
            "local_master/monthly",
            "state",
        ):
            (self.runtime / relative).mkdir(parents=True, exist_ok=True)

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_residue", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        return module

    def _run(self, printer):
        import contextlib

        module = self._module()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = getattr(module, printer)(NOW)
        return buffer.getvalue(), attention

    # ---- transport ----------------------------------------------------

    def test_a_staging_file_is_not_counted_as_awaiting_intake(self):
        (self.runtime / "events/transport/.tmp-killed.json").write_text(
            '{"source": "DESKTOP_1"}', encoding="utf-8"
        )

        output, attention = self._run("_print_company")

        self.assertIn("transport=0", output)
        self.assertIn("incomplete=1", output)
        self.assertNotIn(
            "수집되지 않고 남은 Event",
            " ".join(attention),
            "an abandoned staging file is not an Event waiting to be collected",
        )

    def test_a_staging_file_is_named_and_called_safe_to_delete(self):
        (self.runtime / "events/transport/.tmp-killed.json").write_text(
            '{"source": "DESKTOP_1"}', encoding="utf-8"
        )

        _output, attention = self._run("_print_company")

        residue = [item for item in attention if ".tmp-" in item]
        self.assertEqual(len(residue), 1, attention)
        self.assertIn("지워도 안전하다", residue[0])

    def test_a_clean_transport_directory_says_nothing(self):
        """The other half of C26's rule: the line must appear only when there
        is something to say, and must disappear once it is dealt with."""
        _output, attention = self._run("_print_company")
        self.assertEqual([item for item in attention if ".tmp-" in item], [])

    def test_deleting_it_clears_the_line(self):
        staged = self.runtime / "events/transport/.tmp-killed.json"
        staged.write_text('{"source": "DESKTOP_1"}', encoding="utf-8")
        self.assertTrue([i for i in self._run("_print_company")[1] if ".tmp-" in i])

        staged.unlink()

        self.assertEqual(
            [item for item in self._run("_print_company")[1] if ".tmp-" in item], []
        )

    def test_a_real_queued_event_is_still_reported(self):
        """The skip must not swallow the condition it sits next to."""
        real = self.runtime / "events/transport/EVT-1.json"
        real.write_text('{"source": "DESKTOP_1"}', encoding="utf-8")
        (self.runtime / "events/transport/.tmp-killed.json").write_text(
            '{"source": "DESKTOP_1"}', encoding="utf-8"
        )

        output, attention = self._run("_print_company")

        self.assertIn("transport=1", output)
        self.assertIn("incomplete=1", output)
        self.assertTrue([item for item in attention if "수집되지 않고 남은 Event" in item])

    # ---- Company History ----------------------------------------------

    def test_a_staging_file_is_not_counted_as_a_day_of_history(self):
        daily = self.runtime / "local_master/daily"
        (daily / "2026-08-12.md").write_text("# real\n", encoding="utf-8")
        (daily / ".tmp-killed.md").write_text("# part", encoding="utf-8")

        output, _attention = self._run("_print_history")

        self.assertIn("daily 파일          : 1", output)

    def test_a_staging_file_is_not_displayed_as_a_month(self):
        monthly = self.runtime / "local_master/monthly"
        (monthly / "2026-07.md").write_text("# real\n", encoding="utf-8")
        (monthly / ".tmp-killed.md").write_text("# part", encoding="utf-8")

        output, _attention = self._run("_print_history")

        self.assertIn("monthly 파일        : 1", output)
        self.assertNotIn(".tmp-killed", output)

    def test_a_staging_file_is_not_a_candidate_awaiting_a_human(self):
        """`FileHistoryRepository.save()` stages into `review/`, so this is
        the same directory. A person cannot review a file the pipeline
        abandoned, so alerting on it would stand forever."""
        review = self.runtime / "history_candidates/review"
        (review / ".tmp-killed.json").write_text('{"summary"', encoding="utf-8")

        output, attention = self._run("_print_history")

        self.assertIn("검토 대기 Candidate : 0", output)
        self.assertEqual([item for item in attention if "사람 검토를" in item], [])

    def test_a_real_candidate_next_to_it_is_still_reported(self):
        review = self.runtime / "history_candidates/review"
        (review / "HIST-1.json").write_text('{"summary": "s"}', encoding="utf-8")
        (review / ".tmp-killed.json").write_text('{"summary"', encoding="utf-8")

        output, attention = self._run("_print_history")

        self.assertIn("검토 대기 Candidate : 1", output)
        self.assertTrue([item for item in attention if "사람 검토를" in item])


class ResentDuplicateBacklogTests(CompanyActivityTestCase):
    """The outbox's designed recovery parked ATTENTION permanently.

    `agent/outbox.py` re-sends any Event still in `outbox/`, which is what a
    crash between "Transport accepted" and "moved to sent/" leaves behind.
    Its docstring says a duplicate delivery "costs one redundant file copy
    and produces no duplicate History", and names this skip as the reason:

        transport.run_intake()   already in incoming/processed/rejected
                                 -> skipped_already_present

    True of the pipeline. Never checked against the view. `run_intake()`
    leaves that file in `transport/` and nothing ever deletes from
    `transport/`, so the copy is not redundant for long — it is permanent.
    Measured, one re-send after its original had been collected:

        run 1..3   moved=0, skipped_already_present=1
                   awaiting_intake=1, is_clear=False, ATTENTION
                   "수집되지 않고 남은 Event: transport=1"   every run

    Nothing an operator does clears that, and the sentence is false: the
    Event was collected. This is the third instance of the same shape in this
    view — `unparseable`, `future_dated`/`name_collision`, and now the most
    ordinary trigger of all, a successful retry.

    Counted separately and excluded from `awaiting_intake`, following
    `unparseable` rather than `future_dated`: intake's verdict is
    deterministic and nothing removes the downstream twin that produces it,
    so the file is not queued work. When the twin is still in `incoming/`,
    `awaiting_collection` already counts it, so no in-flight signal is lost.
    """

    def _resend(self, name="EVT-1.json", *, collected_in="processed"):
        payload = '{"event_id": "EVT-1", "source": "DESKTOP_1"}'
        target = getattr(self, collected_in)
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(payload, encoding="utf-8")
        self.transport.mkdir(parents=True, exist_ok=True)
        (self.transport / name).write_text(payload, encoding="utf-8")

    def test_a_resent_duplicate_is_not_counted_as_awaiting_intake(self):
        self._resend()

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 0)
        self.assertEqual(backlog.already_collected, 1)

    def test_a_resent_duplicate_alone_leaves_the_backlog_clear(self):
        self._resend()

        self.assertTrue(self.snapshot().backlog.is_clear)

    def test_every_directory_intake_checks_counts(self):
        """intake checks incoming/, processed/ and rejected/. A view that
        checked fewer would call some of them backlog."""
        for directory in ("incoming", "processed", "rejected"):
            with self.subTest(directory=directory):
                self.setUp()
                self._resend(collected_in=directory)

                backlog = self.snapshot().backlog

                self.assertEqual(backlog.already_collected, 1)
                self.assertEqual(backlog.awaiting_intake, 0)

    def test_a_duplicate_of_something_still_in_incoming_is_still_in_flight(self):
        """Excluding it must not hide work. The twin in `incoming/` has not
        been collected yet, so `awaiting_collection` has to carry it."""
        self._resend(collected_in="incoming")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_collection, 1)
        self.assertFalse(backlog.is_clear)

    def test_a_genuinely_new_event_is_still_counted(self):
        """The guard must not swallow real backlog — the opposite defect."""
        self.transport.mkdir(parents=True, exist_ok=True)
        (self.transport / "EVT-NEW.json").write_text(
            '{"event_id": "EVT-NEW", "source": "DESKTOP_1"}', encoding="utf-8"
        )
        self._resend()

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 1)
        self.assertEqual(backlog.already_collected, 1)
        self.assertFalse(backlog.is_clear)

    def test_the_view_uses_intake_s_own_already_present_test(self):
        """Same reason `test_the_view_uses_intake_s_own_parse_test` exists: a
        second opinion about "already present" would let the view and the
        step disagree. intake checks `(directory / name).exists()` over the
        three directories; this view is handed those same three."""
        import inspect

        import app.desktop_activity as activity

        source = inspect.getsource(activity.read_company_activity)
        self.assertIn("(incoming_dir, processed_dir, rejected_dir)", source)

    def test_the_backlog_view_agrees_with_what_intake_actually_does(self):
        """Bound to intake's behaviour rather than to a copy of its rule:
        every file this view calls `already_collected` must be one
        `run_intake()` really refuses to promote, run for run."""
        from transport.intake import run_intake

        self._resend()

        for _ in range(3):
            summary = run_intake(
                transport_dir=self.transport,
                incoming_dir=self.incoming,
                processed_dir=self.processed,
                rejected_dir=self.rejected,
                stable_after_seconds=0,
            )
            backlog = self.snapshot().backlog

            self.assertEqual(summary.moved, ())
            self.assertEqual(len(summary.skipped_already_present), backlog.already_collected)
            self.assertEqual(backlog.awaiting_intake, 0)
            self.assertTrue(backlog.is_clear)


class SuppressedDeliveryTests(CompanyActivityTestCase):
    """The half of "already present" that is not a duplicate.

    `run_intake()` decides a `transport/` file is already handled by asking
    whether that *name* exists in `incoming/`/`processed/`/`rejected/`
    (BUG-53). Usually the twin really is the same Event, re-sent by the
    outbox — harmless. Sometimes it is not the same Event at all:

        a directory of that name                     BUG-47
        a 0-byte Files On-Demand placeholder         BUG-53
        a different event_id under a colliding name  Windows folds
                                                     `EVT-a.json` and
                                                     `EVT-A.json` into one
                                                     path, and
                                                     `safe_event_filename()`
                                                     preserves case

    In every one of those the Event in `transport/` has never been delivered
    and never will be, and nothing else in the pipeline can see it: the
    Collector never receives the file, so it is absent from Company History
    with no error anywhere.

    Before `already_collected` existed, all of these surfaced — badly, as a
    permanently stuck `awaiting_intake`, with a sentence ("수집되지 않고 남은
    Event") that was false for the common duplicate and accidentally true
    here. Taking the duplicate out of ATTENTION without separating these
    would have replaced a false alert with a missing one, so the twin is
    opened and the two `event_id`s compared.

    The case-collision row is the one nothing else could have caught:
    `safe_event_filename()` appends a digest whenever it changes an id
    precisely so two ids never share a name, and that guarantee simply does
    not hold on a case-insensitive filesystem.
    """

    MINE = '{"event_id": "EVT-1", "source": "DESKTOP_1"}'

    def _plant(self, twin_builder, *, name="EVT-1.json"):
        self.transport.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        twin_builder(self.processed / name)
        (self.transport / "EVT-1.json").write_text(self.MINE, encoding="utf-8")
        return self.snapshot().backlog

    def test_a_directory_of_the_same_name_is_a_suppressed_delivery(self):
        backlog = self._plant(lambda p: p.mkdir())

        self.assertEqual(backlog.suppressed, 1)
        self.assertEqual(backlog.already_collected, 0)

    def test_a_zero_byte_placeholder_is_a_suppressed_delivery(self):
        backlog = self._plant(lambda p: p.write_text("", encoding="utf-8"))

        self.assertEqual(backlog.suppressed, 1)
        self.assertEqual(backlog.already_collected, 0)

    def test_a_different_event_under_the_same_name_is_a_suppressed_delivery(self):
        backlog = self._plant(
            lambda p: p.write_text('{"event_id": "EVT-9"}', encoding="utf-8")
        )

        self.assertEqual(backlog.suppressed, 1)
        self.assertEqual(backlog.already_collected, 0)

    def test_a_case_only_filename_collision_is_a_suppressed_delivery(self):
        """Only reproducible where the filesystem folds case, which is the
        deployment target (docs/11: Windows). Elsewhere the two names are two
        files and there is nothing to suppress — so the test asserts the
        premise before asserting the verdict."""
        self.transport.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        (self.processed / "EVT-A.json").write_text(
            '{"event_id": "EVT-A", "source": "DESKTOP_1"}', encoding="utf-8"
        )
        if not (self.processed / "EVT-a.json").exists():
            self.skipTest("case-sensitive filesystem: no collision to observe")
        (self.transport / "EVT-a.json").write_text(
            '{"event_id": "EVT-a", "source": "DESKTOP_1"}', encoding="utf-8"
        )

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.suppressed, 1)
        self.assertEqual(backlog.already_collected, 0)
        self.assertEqual(backlog.awaiting_intake, 0)

    def test_a_true_duplicate_is_not_reported_as_suppressed(self):
        backlog = self._plant(lambda p: p.write_text(self.MINE, encoding="utf-8"))

        self.assertEqual(backlog.suppressed, 0)
        self.assertEqual(backlog.already_collected, 1)

    def test_intake_really_does_refuse_all_of_them(self):
        """The premise, checked rather than assumed: every shape above is one
        `run_intake()` skips as already-present, so the view is explaining a
        real verdict rather than inventing a category."""
        from transport.intake import run_intake

        shapes = {
            "directory": lambda p: p.mkdir(),
            "zero-byte": lambda p: p.write_text("", encoding="utf-8"),
            "other-event": lambda p: p.write_text('{"event_id": "EVT-9"}', encoding="utf-8"),
            "true-duplicate": lambda p: p.write_text(self.MINE, encoding="utf-8"),
        }
        for label, builder in shapes.items():
            with self.subTest(shape=label):
                self.setUp()
                self._plant(builder)

                summary = run_intake(
                    transport_dir=self.transport,
                    incoming_dir=self.incoming,
                    processed_dir=self.processed,
                    rejected_dir=self.rejected,
                    stable_after_seconds=0,
                )

                self.assertEqual(summary.moved, ())
                self.assertEqual(summary.skipped_already_present, ("EVT-1.json",))

    def test_a_suppressed_delivery_is_reported_to_the_operator(self):
        import contextlib
        import importlib.util

        self._plant(lambda p: p.mkdir())

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_suppressed", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.root
        module.read_company_activity = lambda **_: self.snapshot()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_company(NOW)

        self.assertTrue(
            [item for item in attention if "같은 이름의 다른 파일에 막혀" in item],
            attention,
        )


class DeliveryConsistencyIsRenderedTests(unittest.TestCase):
    """The AGENT section's delivery block — never executed.

    `agent/delivery.find_undelivered_events()` is thoroughly tested
    (`test_agent_delivery.py`), and its three-way result is the whole point:
    an Event recorded as sent that never reached the sync folder is silent
    data loss on the machine that PRODUCES Company History. What nothing ran
    was the block in `ops_status._print_agent()` that turns that result into
    a line and an ATTENTION entry — it is guarded by
    `COMPANY_OPS_AGENT_SYNC_FOLDER`, and no test had ever set it while
    calling `_print_agent()` (found by a line-coverage pass, C42).

    That matters here more than coverage usually does, because the claim
    about this renderer lives in a docstring in a *different file*:
    `test_agent_delivery.py::UnreadableSentRecordTests` says the defect it
    fixed was "`ops_status.py` prints `전달 정합성 : OK` — the same line it
    prints when every Event was checked and every one arrived". Whether it
    still prints something else was asserted nowhere.

    Three verdicts, both ATTENTION lines, and the unconfigured case.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        self.agent_dir = self.runtime / "agent"
        for relative in ("locks", "state", "outbox", "sent", "signals_rejected"):
            (self.agent_dir / relative).mkdir(parents=True, exist_ok=True)
        self.sync_folder = self.root / "cloud"
        self.sync_folder.mkdir()

    def _sent(self, event_id, *, payload=None):
        """A record in `sent/`, in the shape the Agent writes it.

        `reporter.local_output.safe_event_filename()` names it, reused rather
        than re-derived — the whole delivery check is a comparison between
        two filenames and a fixture with its own naming rule would compare
        nothing.
        """
        from reporter.local_output import safe_event_filename

        body = payload if payload is not None else json.dumps({"event_id": event_id})
        path = self.agent_dir / "sent" / safe_event_filename(event_id)
        path.write_text(body, encoding="utf-8")
        return path

    def _deliver(self, event_id):
        from reporter.local_output import safe_event_filename

        (self.sync_folder / safe_event_filename(event_id)).write_text(
            json.dumps({"event_id": event_id}), encoding="utf-8"
        )

    def _run(self, *, sync_folder=True):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_delivery", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime

        key = "COMPANY_OPS_AGENT_SYNC_FOLDER"
        original = os.environ.get(key)

        def restore():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original

        self.addCleanup(restore)
        if sync_folder:
            os.environ[key] = str(self.sync_folder)
        else:
            os.environ.pop(key, None)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_agent(NOW)
        return buffer.getvalue(), attention

    def _line(self, output):
        return next(
            item for item in output.splitlines() if "전달 정합성" in item
        )

    def test_everything_arrived_reads_ok(self):
        self._sent("EVT-1")
        self._deliver("EVT-1")

        output, attention = self._run()

        self.assertIn("OK", self._line(output))
        self.assertEqual([a for a in attention if "sync" in a or "전송" in a], [])

    def test_a_record_whose_event_never_arrived_reads_undelivered(self):
        """The loss this block exists for. The Agent moved the Event to
        `sent/` — its own durability boundary — and the file is not in the
        folder Desktop 4 reads."""
        self._sent("EVT-1")
        self._deliver("EVT-1")
        # A second Event recorded as sent, whose delivered copy is a
        # different Event: present by name, wrong by content.
        self._sent("EVT-2")
        from reporter.local_output import safe_event_filename

        (self.sync_folder / safe_event_filename("EVT-2")).write_text(
            json.dumps({"event_id": "SOMETHING-ELSE"}), encoding="utf-8"
        )

        output, attention = self._run()

        self.assertIn("UNDELIVERED", self._line(output))
        self.assertIn("! EVT-2", output)
        self.assertTrue(
            any("EVT-2" in item for item in attention),
            f"the loss did not reach ATTENTION: {attention}",
        )

    def test_an_unreadable_record_reads_unknown_not_ok(self):
        """C32 §13's fix, asserted where it is actually visible. "I could not
        check this one" must not print the line that means "I checked
        everything and it all arrived"."""
        self._sent("EVT-1")
        self._deliver("EVT-1")
        (self.agent_dir / "sent" / "damaged.json").write_text(
            "{not json", encoding="utf-8"
        )

        output, attention = self._run()

        self.assertIn("UNKNOWN", self._line(output))
        self.assertNotIn("OK", self._line(output))
        self.assertIn("읽을 수 없음 1건", self._line(output))
        self.assertTrue(
            any("damaged.json" in item for item in attention),
            f"the unreadable record did not reach ATTENTION: {attention}",
        )

    def test_undelivered_wins_over_unknown(self):
        """Both conditions at once. A verdict that reported UNKNOWN while an
        Event was known to be missing would bury the harder fact."""
        from reporter.local_output import safe_event_filename

        self._sent("EVT-2")
        (self.sync_folder / safe_event_filename("EVT-2")).write_text(
            json.dumps({"event_id": "SOMETHING-ELSE"}), encoding="utf-8"
        )
        (self.agent_dir / "sent" / "damaged.json").write_text(
            "{not json", encoding="utf-8"
        )

        output, attention = self._run()

        self.assertIn("UNDELIVERED", self._line(output))
        self.assertEqual(len([a for a in attention if "읽을 수 없는 전송 기록" in a]), 1)

    def test_an_absent_event_is_not_a_loss(self):
        """docs: a file already collected by Desktop 4 is *gone* from the
        sync folder, which is the pipeline working. Counting it as
        undelivered would make every healthy Desktop permanently red."""
        self._sent("EVT-1")

        output, attention = self._run()

        line = self._line(output)
        self.assertIn("OK", line)
        self.assertIn("이미 수거됨 1건", line)
        self.assertEqual([a for a in attention if "전송 완료로" in a], [])

    def test_an_unset_sync_folder_says_it_cannot_check(self):
        """The `else` arm. "확인 불가" and "OK" must not be the same line —
        a Desktop whose folder is unconfigured has had nothing verified."""
        self._sent("EVT-1")

        output, attention = self._run(sync_folder=False)

        self.assertIn("확인 불가", self._line(output))
        self.assertNotIn("OK", self._line(output))
        self.assertEqual([a for a in attention if "전송 완료로" in a], [])

    def test_the_undelivered_list_is_bounded_and_the_count_is_not(self):
        """Six losses, five names. The printed list is capped; the number in
        the ATTENTION line is the real total, because a cap that also capped
        the count would understate the loss."""
        from reporter.local_output import safe_event_filename

        for index in range(6):
            event_id = f"EVT-{index}"
            self._sent(event_id)
            (self.sync_folder / safe_event_filename(event_id)).write_text(
                json.dumps({"event_id": "OTHER"}), encoding="utf-8"
            )

        output, attention = self._run()

        printed = [item for item in output.splitlines() if item.strip().startswith("! EVT-")]
        self.assertEqual(len(printed), 5)
        self.assertTrue(
            any("6건" in item for item in attention),
            f"the ATTENTION line lost the real count: {attention}",
        )


class PendingDatesAreListedTests(unittest.TestCase):
    """The three lines under `미수집 날짜`, which nothing ran.

    A count on its own does not tell an operator whether a Desktop is one
    day behind or has been off since last month — and the dates are already
    in hand. The listing is capped at seven with a trailing `...`, and the
    cap is what makes it worth a test: a truncation that did not show it had
    truncated would read as the whole backlog.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name) / "runtime"
        self.agent_dir = self.runtime / "agent"
        for relative in ("locks", "state", "outbox", "sent", "signals_rejected"):
            (self.agent_dir / relative).mkdir(parents=True, exist_ok=True)

    def _run(self, *, last_collected, start_date):
        import contextlib
        import importlib.util

        (self.agent_dir / "state" / "agent_state.json").write_text(
            json.dumps(
                {
                    "desktop_id": "DESKTOP_1",
                    "last_successful_collection_date": last_collected,
                    "last_run": "2026-08-13T09:00:00+09:00",
                }
            ),
            encoding="utf-8",
        )

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_pending", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime

        key = "COMPANY_OPS_AGENT_START_DATE"
        original = os.environ.get(key)

        def restore():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original

        self.addCleanup(restore)
        if start_date is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = start_date

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module._print_agent(NOW)
        return buffer.getvalue()

    def test_a_short_backlog_lists_every_date(self):
        """`NOW` here is 2026-08-10 and today is never collected, so a
        watermark at 08-05 leaves 08-06 … 08-09 pending."""
        output = self._run(last_collected="2026-08-05", start_date="2026-08-01")

        self.assertIn("미수집 날짜         : 4", output)
        self.assertIn("2026-08-06, 2026-08-07, 2026-08-08, 2026-08-09", output)
        self.assertNotIn("...", output)

    def test_a_long_backlog_shows_that_it_was_truncated(self):
        output = self._run(last_collected="2026-07-01", start_date="2026-06-01")

        self.assertIn("2026-07-02, 2026-07-03", output)
        self.assertIn("...", output)

    def test_without_a_start_date_the_operator_is_told_why_it_is_zero(self):
        """REGRESSION. The note was unreachable, and it is the only thing
        separating "nothing is pending" from "nothing was computed".

        `read_status()` fills `pending_dates` only when it is given a start
        date, and `_print_agent()` gives it `_agent_start_date()`. So the old
        condition — `if snapshot.pending_dates and _agent_start_date() is
        None` — required a non-empty list *and* an unset variable, which
        cannot both hold. Measured with the variable unset and a watermark
        five days back:

            미수집 날짜         : 0

        and nothing else: byte-identical to a Desktop that is fully caught
        up, on a machine that produces Company History.
        """
        output = self._run(last_collected="2026-08-05", start_date=None)

        self.assertIn("미수집 날짜         : 0", output)
        self.assertIn("COMPANY_OPS_AGENT_START_DATE", output)

    def test_the_note_is_absent_when_the_variable_is_set(self):
        """The other direction: a configured Desktop must not carry a
        standing line telling it to configure something."""
        output = self._run(last_collected="2026-08-05", start_date="2026-08-01")

        self.assertNotIn("COMPANY_OPS_AGENT_START_DATE", output)

    def test_the_history_sibling_was_always_written_the_right_way_round(self):
        """Where the corrected shape comes from. Both notes answer the same
        question for two different variables, and only one of them could
        ever print."""
        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("if history_start is None:", source)
        self.assertIn("if _agent_start_date() is None:", source)
        self.assertNotIn(
            "if snapshot.pending_dates and _agent_start_date() is None:", source
        )


class MetricsOnASuccessfulStepReachNoViewTests(unittest.TestCase):
    """CHARACTERIZATION — asserts today's behaviour, deliberately.

    Every `recorder.ok()` call records metrics, and **no view prints them.**
    `_print_last_run()` skips SUCCESS components outright (`continue`), and
    `run_company_ops._report_run_summary()` prints only failures. So a metric
    a step records on its healthy path lives in `runtime/runs/last_run.json`
    and nowhere else.

    Mostly that is right and this test says so: `daily`'s counts, `backup`'s
    status and hash, `collector`'s four numbers and `transport`'s six are all
    on the Dashboard row, in the entrypoint's stdout, or both — printing them
    again would make the LAST RUN block long enough that nobody reads the
    part that matters ("only the failing components are printed so the block
    stays short").

    **One is not covered anywhere else, and it is the one that reports a
    divergence rather than activity:** `notion_sync.same_instant_skips`
    (BACKLOG E-23, made countable in C40). It counts Events that Company
    History kept and the Notion row did not — Source and View disagreeing —
    and it is recorded on the SUCCESS branch, because the skip IS a success
    by docs/04 §29-30. Measured: manifest `same_instant_skips=2`,
    `ops_status.py` LAST RUN block silent, Dashboard `Notion Skipped: 2` with
    no way to tell a genuine Late Event skip from a same-instant one.

    Not fixed here. Which of a healthy step's numbers deserve a line is a
    judgement, and E-23's own resolution — whether a same-instant skip should
    be a distinct outcome at all — is a spec decision that is still open. What
    is not a judgement is that the sweep which would have caught this could
    not see it: `WrittenAndNeverReadFieldTests` walked every **dataclass
    field** in `src/`, and metrics are dict keys.

    If this starts failing, a view for success metrics was added and BACKLOG
    must say which one and why.
    """

    def _module(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_metrics", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        # `DEFAULT_RUN_SUMMARY_PATH` is a second knob on purpose — see this
        # module's own note at the import ("deliberately NOT folded in").
        module.DEFAULT_RUN_SUMMARY_PATH = runtime / "runs" / "last_run.json"
        return module

    def _manifest(self, components):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "runs").mkdir(parents=True)
        (runtime / "runs" / "last_run.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": "R-1",
                    "started_at": "2026-08-10T08:00:00+09:00",
                    "finished_at": "2026-08-10T08:00:30+09:00",
                    "components": components,
                }
            ),
            encoding="utf-8",
        )
        return runtime

    def _last_run(self, components):
        import contextlib

        runtime = self._manifest(components)
        module = self._module(runtime)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module._print_last_run(NOW)
        return buffer.getvalue()

    def test_a_successful_steps_metrics_are_not_printed(self):
        output = self._last_run(
            [
                {
                    "name": "notion_sync",
                    "status": "SUCCESS",
                    "metrics": {"processed": 4, "same_instant_skips": 2},
                }
            ]
        )

        self.assertIn("SUCCESS", output)
        self.assertNotIn("same_instant_skips", output)
        self.assertNotIn("processed", output)

    def test_the_same_step_failing_does_print_them(self):
        """The contrast that makes the line above a choice rather than an
        oversight — the renderer works, it is only ever asked about
        failures."""
        output = self._last_run(
            [
                {
                    "name": "notion_sync",
                    "status": "FAILED",
                    "failure": {
                        "classification": "NOTION_SYNC_INCOMPLETE",
                        "reason": "503",
                        "retryability": "RETRYABLE",
                        "severity": "DEGRADED",
                    },
                    "metrics": {"processed": 4, "same_instant_skips": 2},
                }
            ]
        )

        self.assertIn("same_instant_skips=2", output)

    def test_the_runner_really_records_it_on_the_success_branch(self):
        """The premise, from `app/runner.py` rather than assumed: the metric
        this is about is attached to `recorder.ok()`, which is why no view
        asks for it."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "app" / "runner.py"
        ).read_text(encoding="utf-8")
        ok_call = source[source.index("recorder.ok(\n                    C_NOTION_SYNC"):]

        self.assertIn("same_instant_skips=same_instant_skips or None", ok_call[:4000])

    def test_the_dashboard_cannot_separate_the_two_kinds_of_skip(self):
        """The other view, so the gap is stated once and completely."""
        from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

        self.assertIn("Notion Skipped", DASHBOARD_DATABASES[OPS_RUNS])
        self.assertNotIn("Notion Same Instant Skips", DASHBOARD_DATABASES[OPS_RUNS])


class AHealthyRuntimeCanActuallyBeQuietTests(unittest.TestCase):
    """`main()`'s clean exit — the one path nothing ran.

    Every other test here plants a fault and checks that ATTENTION names it.
    That leaves the opposite property unasserted, and it is the one the whole
    section depends on: **a healthy system must be able to produce an empty
    ATTENTION list.** This file's own guidance says why — "지워지지 않는
    경보는 그 절을 대충 넘기도록 훈련시킨다" — and a view that can never be
    quiet is one an operator stops reading, at which point every real alarm
    in it is lost too.

    It is also the only exit-0 path in `ops_status.main()`, and a line
    coverage pass over the root scripts (C42) found neither it nor the
    sentence it prints had ever executed.

    Built against the real clock rather than a pinned `now`, because `main()`
    takes no `now` — it reads the clock itself. Everything in the fixture is
    therefore placed RELATIVE to that clock, and the one absolute fact
    (`daily/<yesterday>.md` was written before the last backup) is set with
    `os.utime` rather than left to the order the test happens to run in.
    """

    # docs/02 §8's own table, not an arbitrary pairing. It was arbitrary
    # until C47: this fixture called itself a *healthy* runtime while giving
    # DESKTOP_2 the CTO_FRONTEND role and DESKTOP_3 the CMO role, which §8
    # assigns the other way round. Nothing checked the pair — `validate_event()`
    # checks each field against its own allowed set and never the two together
    # — so the fixture passed for many Sprints while the COMPANY block printed
    # `DESKTOP_2 role=CTO_FRONTEND` in plain sight.
    #
    # `controltower`'s Desktop layer compares the pair against
    # `reporter/profiles.PROFILES` (which is §8 verbatim) and this fixture is
    # what it caught first. Corrected here rather than exempted: a fixture
    # that means "this is what healthy looks like" must not contain the one
    # shape that makes Team and Desktop aggregation disagree.
    ROLES = (
        ("DESKTOP_1", "CTO_BACKEND"),
        ("DESKTOP_2", "CMO"),
        ("DESKTOP_3", "CTO_FRONTEND"),
        ("DESKTOP_4", "COO"),
    )

    def _healthy_runtime(self, now):
        from app.runner import PIPELINE_COMPONENTS

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for relative in (
            "events/processed", "events/transport", "events/incoming",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "runs", "locks",
        ):
            (runtime / relative).mkdir(parents=True, exist_ok=True)

        # Every Desktop heard from today: silence is itself an ATTENTION
        # condition, so a runtime with no Events at all is not a quiet one.
        for index, (source, role) in enumerate(self.ROLES):
            (runtime / "events" / "processed" / f"EVT-{index}.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0", "event_id": f"EVT-{index}",
                        "source": source, "role": role, "project_id": "P",
                        "event_type": "STARTED", "status": "IN_PROGRESS",
                        "summary": "s", "evidence": [], "history_candidate": False,
                        "timestamp": now.isoformat(timespec="seconds"),
                    }
                ),
                encoding="utf-8",
            )

        yesterday = now.date() - timedelta(days=1)
        daily = runtime / "local_master" / "daily" / f"{yesterday.isoformat()}.md"
        daily.write_text("# ok\n", encoding="utf-8")
        # Written BEFORE the last backup — otherwise it is Company History
        # that exists only on this machine, which is an ATTENTION condition
        # and rightly so.
        stamp = (now - timedelta(hours=1)).timestamp()
        os.utime(daily, (stamp, stamp))

        (runtime / "state" / "daily_history_state.json").write_text(
            json.dumps({"last_successful_daily_close": yesterday.isoformat()}),
            encoding="utf-8",
        )
        (runtime / "state" / "backup_state.json").write_text(
            json.dumps(
                {
                    "last_successful_backup": now.isoformat(timespec="seconds"),
                    "last_backup_commit": "abc123",
                    "backup_status": "BACKUP_SUCCESS",
                }
            ),
            encoding="utf-8",
        )
        (runtime / "runs" / "last_run.json").write_text(
            json.dumps(
                {
                    "schema_version": 1, "run_id": "R-1",
                    "started_at": now.isoformat(timespec="seconds"),
                    "finished_at": now.isoformat(timespec="seconds"),
                    # All nine, because a step missing from the manifest is
                    # reported as "시작되지 못한 단계" — correctly.
                    "components": [
                        {"name": name, "status": "SUCCESS", "metrics": {}}
                        for name in PIPELINE_COMPONENTS
                    ],
                }
            ),
            encoding="utf-8",
        )
        return runtime

    def _run(self):
        import contextlib
        import importlib.util

        now = datetime.now().astimezone()
        runtime = self._healthy_runtime(now)

        for key in (
            "COMPANY_OPS_AGENT_START_DATE",
            "COMPANY_OPS_HISTORY_START_DATE",
            "COMPANY_OPS_AGENT_SYNC_FOLDER",
        ):
            original = os.environ.get(key)
            if original is not None:
                os.environ.pop(key)
                self.addCleanup(os.environ.__setitem__, key, original)

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_quiet", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        module.DEFAULT_RUN_SUMMARY_PATH = runtime / "runs" / "last_run.json"

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main()
        return buffer.getvalue(), code

    def test_a_healthy_runtime_needs_nobody_and_exits_zero(self):
        output, code = self._run()

        self.assertEqual(code, 0, output)
        self.assertIn("ATTENTION — 없음", output)

    def test_the_attention_block_itself_is_not_printed(self):
        """The two outputs must not both appear — an operator scanning for
        the word would find it either way."""
        output, _code = self._run()

        self.assertNotIn("  ! ", output)

    def test_every_section_still_ran(self):
        """A quiet report and a report that skipped its checks look the same
        from the exit code alone."""
        output, _code = self._run()

        for heading in ("COMPANY", "HISTORY", "LAST RUN", "NOTION", "AGENT"):
            with self.subTest(heading=heading):
                self.assertIn(heading, output)


class DailyCountsMoreThanItShowsTests(unittest.TestCase):
    """The Daily counterpart of `MonthlyCountsMoreThanItShowsTests`, which
    existed while this one did not.

    A Daily file states its own total (`- Event Count:`) and carries the
    Event IDs themselves. As generated the two agree, so a disagreement is
    decidable inside one file — no window, nothing else consulted, and the
    comparison reuses `existing_event_ids()`, the same function §38's
    duplicate guard reads the file with, rather than counting the lines a
    second way.

    Three real losses reach an operator through it, and none had a reporter:

        fewer ids than counted   a `category=None` KEEP Candidate is dropped
                                 from every category section, so its Event ID
                                 never reaches the file
                                 (`test_daily_history.py::test_a_category_
                                 less_keep_candidate_silently_loses_its_
                                 detail` characterizes the loss)
        more ids than counted    BUG-11/27: a newline in `summary` /
                                 `project_id` / `event_id` forges a
                                 `- Event ID:` line
        fewer ids than counted   an item block deleted by hand (docs/06 §57)

    The middle one is why this reports BOTH directions where the Monthly
    sibling reports only a shortfall — see the function's docstring.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.runtime = self.root / "runtime"
        for relative in (
            "history_candidates/keep", "history_candidates/review",
            "local_master/daily", "local_master/monthly", "state",
            "events/processed", "locks",
        ):
            (self.runtime / relative).mkdir(parents=True)
        self.daily = self.runtime / "local_master" / "daily"

    def _candidate(self, event_id, summary="did work", category="MILESTONE"):
        from history import HistoryCandidate, HistoryDecision

        return HistoryCandidate(
            history_id=f"HIST-{event_id}", event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00", category=category,
            project_id="PRJ_A", role="COO", summary=summary, evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def _write_day(self, day, candidates):
        from daily.markdown import render_daily_markdown

        (self.daily / f"{day}.md").write_text(
            render_daily_markdown(
                date(*(int(part) for part in day.split("-"))), candidates, "gen"
            ),
            encoding="utf-8",
        )

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_daily_count", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        return module

    def _report(self):
        return self._module()._daily_counts_more_than_it_shows(self.daily)

    def _alerts(self):
        import contextlib

        module = self._module()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [
            item for item in attention if "Daily History의 자기 숫자" in item
        ]

    # ---- the defect ------------------------------------------------------

    def test_a_forged_event_id_line_is_reported(self):
        """BUG-11/27's silent half. Measured alongside this: the forged id
        makes §38 refuse a genuinely late Event of that id forever, and
        `_kept_but_not_rendered()` reports clean because the id IS in the
        file. This line is the only thing that sees it."""
        self._write_day(
            "2026-08-05", [self._candidate("EVT-1", "did work\n- Event ID: VICTIM")]
        )

        self.assertEqual(self._report(), (("2026-08-05", 1, 2),))

    def test_the_two_existing_detectors_really_are_defeated_by_it(self):
        """The premise, so this class's reason for existing is asserted and
        not merely described."""
        from daily.late_events import existing_event_ids, select_late_candidates
        from daily.markdown import render_daily_markdown

        text = render_daily_markdown(
            date(2026, 8, 5),
            [self._candidate("EVT-1", "did work\n- Event ID: VICTIM")],
            "gen",
        )
        (self.daily / "2026-08-05.md").write_text(text, encoding="utf-8")
        module = self._module()
        stored = (
            module.StoredCandidate(stem="a", event_id="EVT-1", when=date(2026, 8, 5), reviewed=()),
            module.StoredCandidate(stem="b", event_id="VICTIM", when=date(2026, 8, 5), reviewed=()),
        )

        self.assertIn("VICTIM", existing_event_ids(text))
        self.assertEqual(
            select_late_candidates(text, [self._candidate("VICTIM", "genuinely late")]), ()
        )
        stranded, _unreadable = module._kept_but_not_rendered(stored, self.daily)
        self.assertEqual(stranded, ())

    def test_a_category_less_candidate_is_reported(self):
        """The other direction, and the loss `test_daily_history.py` already
        characterizes without anything reporting it."""
        self._write_day("2026-08-06", [self._candidate("EVT-2", category=None)])

        self.assertEqual(self._report(), (("2026-08-06", 1, 0),))

    def test_a_hand_deleted_item_block_is_reported(self):
        """docs/06 §57 permits the edit; the count then contradicts the file
        and an operator who removed an item without correcting it should be
        told, exactly as the Monthly sibling tells them."""
        self._write_day("2026-08-07", [self._candidate("A"), self._candidate("B")])
        text = (self.daily / "2026-08-07.md").read_text(encoding="utf-8")
        text = text.replace("- Event ID: B\n", "")
        (self.daily / "2026-08-07.md").write_text(text, encoding="utf-8")

        self.assertEqual(self._report(), (("2026-08-07", 2, 1),))

    # ---- the false-alarm guard ------------------------------------------

    def test_every_healthy_shape_is_clean(self):
        """The half that decides whether this is usable at all. A detector
        that fires on ordinary days is one an operator learns to skip."""
        from daily.late_events import append_late_events, select_late_candidates
        from daily.markdown import render_daily_markdown

        shapes = {}
        shapes["2026-09-01"] = render_daily_markdown(date(2026, 9, 1), [], "gen")
        shapes["2026-09-02"] = render_daily_markdown(
            date(2026, 9, 2),
            [self._candidate("A"), self._candidate("B"), self._candidate("C")],
            "gen",
        )
        base = render_daily_markdown(date(2026, 9, 3), [self._candidate("A")], "gen")
        once = append_late_events(
            base, select_late_candidates(base, [self._candidate("L1")]), now_iso="T1"
        )
        shapes["2026-09-03"] = once
        shapes["2026-09-04"] = append_late_events(
            once, select_late_candidates(once, [self._candidate("L2")]), now_iso="T2"
        )
        shapes["2026-09-05"] = render_daily_markdown(
            date(2026, 9, 5),
            [
                self._candidate("A", category="DECISION"),
                self._candidate("B", category="ISSUE"),
                self._candidate("C", category="LEARNING"),
                self._candidate("D"),
            ],
            "gen",
        )
        # An empty `event_id` is a valid Event today (BACKLOG A-15) and the
        # renderer writes `- Event ID: ` for it — the id set still has one
        # member, so this must not read as a loss.
        shapes["2026-09-06"] = render_daily_markdown(
            date(2026, 9, 6), [self._candidate("")], "gen"
        )
        for day, text in shapes.items():
            (self.daily / f"{day}.md").write_text(text, encoding="utf-8")

        self.assertEqual(self._report(), ())

    def test_a_file_with_no_event_count_line_is_skipped(self):
        """One number it could not read is not a disagreement — the same
        rule the Monthly sibling states."""
        (self.daily / "2026-08-08.md").write_text(
            "# hand-written note\n\n- Event ID: X\n", encoding="utf-8"
        )

        self.assertEqual(self._report(), ())

    def test_an_unparseable_count_is_skipped_too(self):
        """`- Event Count: many` — a hand edit that replaced the number with
        a word. One number it cannot read is not a disagreement, the same
        rule as a missing line, and the two arrive by different routes."""
        (self.daily / "2026-08-12.md").write_text(
            "# T\n\n## Milestones\n\n### P\n\n- s\n- Event ID: A\n\n"
            "## Metadata\n\n- Event Count: many\n",
            encoding="utf-8",
        )

        self.assertEqual(self._report(), ())

    def test_a_negative_or_padded_count_is_still_read(self):
        """The other side of the same parse: whitespace and a sign are still
        numbers, so they must be compared rather than skipped."""
        (self.daily / "2026-08-13.md").write_text(
            "# T\n\n## Milestones\n\n### P\n\n- s\n- Event ID: A\n\n"
            "## Metadata\n\n- Event Count:   2  \n",
            encoding="utf-8",
        )

        self.assertEqual(self._report(), (("2026-08-13", 2, 1),))

    def test_an_unreadable_file_is_skipped_not_raised(self):
        """This is a read-only diagnostic; damaged evidence must not take the
        view down."""
        self._write_day("2026-08-09", [self._candidate("A")])
        (self.daily / "2026-08-10.md").write_bytes(b"\xff\xfe not utf-8")

        self.assertEqual(self._report(), ())

    def test_a_staging_file_is_not_a_daily(self):
        """`.tmp-…md` is an unfinished write, not Company History — the same
        exclusion every other scanner in this file applies."""
        self._write_day("2026-08-11", [self._candidate("A")])
        (self.daily / ".tmp-half.md").write_text(
            "# T\n\n## Metadata\n\n- Event Count: 9\n", encoding="utf-8"
        )

        self.assertEqual(self._report(), ())

    # ---- it reaches the operator ----------------------------------------

    def test_it_is_printed_and_reaches_attention(self):
        self._write_day(
            "2026-08-05", [self._candidate("EVT-1", "did work\n- Event ID: VICTIM")]
        )

        output, attention = self._alerts()

        self.assertIn("Daily 항목 불일치", output)
        self.assertEqual(len(attention), 1, attention)
        self.assertIn("2026-08-05(1→2)", attention[0])
        self.assertIn("BUG-11/27", attention[0])

    def test_a_healthy_history_says_nothing(self):
        self._write_day("2026-08-05", [self._candidate("A")])

        output, attention = self._alerts()

        self.assertNotIn("Daily 항목 불일치", output)
        self.assertEqual(attention, [])


class ATypeBrokenCandidateReachesAttentionTests(unittest.TestCase):
    """C44. The status view called a pipeline-stopping Candidate readable.

    `_read_keep_candidates()` checked `timestamp` and `event_id` and nothing
    else, so a Candidate whose `summary` or `project_id` had the wrong type
    counted as a perfectly good record. Measured, one such file beside one
    ordinary Candidate:

        Runner      daily FAILED, 0 Daily files, exit 2 — and again next run
        this view   "Candidate 정합성 : OK", and no unreadable count

    The ATTENTION line for unreadable Candidates already existed and already
    said the right thing ("이 파일 하나 때문에 모든 날짜의 Daily History 생성이
    멈춘다"). It simply never fired for this shape. The reader now asks
    `history.result.candidate_errors()` — the same predicate
    `FileHistoryRepository` refuses on — so the two cannot disagree.
    """

    GOOD = {
        "history_id": "HIST-OK",
        "event_id": "EV-OK",
        "timestamp": "2026-08-01T10:00:00+09:00",
        "category": "MILESTONE",
        "project_id": "PRJ_OK",
        "role": "COO",
        "summary": "ordinary work",
        "evidence": [],
        "filter_result": "KEEP",
    }

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.runtime = self.root / "runtime"
        for relative in (
            "history_candidates/keep", "history_candidates/review",
            "local_master/daily", "local_master/monthly", "state",
            "events/processed", "locks",
        ):
            (self.runtime / relative).mkdir(parents=True)
        self.keep = self.runtime / "history_candidates" / "keep"

    def _write(self, name, **overrides):
        data = dict(self.GOOD)
        data["history_id"] = f"HIST-{name}"
        data["event_id"] = name
        data.update(overrides)
        (self.keep / f"HIST-{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def _alerts(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c44", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [
            item for item in attention if "읽을 수 없는 KEEP Candidate" in item
        ]

    def test_a_wrong_typed_field_is_reported_as_unreadable(self):
        self._write("EV-OK")
        self._write("EV-BAD", summary=12345)

        output, attention = self._alerts()

        self.assertIn("읽을 수 없는 Candidate: 1", output)
        self.assertEqual(len(attention), 1, attention)
        self.assertIn("HIST-EV-BAD.json", attention[0])

    def test_an_unparseable_timestamp_is_still_reported_here(self):
        """A-7's case, which `candidate_errors()` deliberately leaves out —
        the index build raises on it with `isoformat`, and moving that raise
        into `list()` would change a contract. This reader keeps its own
        check for it, so the status view names the file either way.
        """
        self._write("EV-OK")
        self._write("EV-A7", timestamp="not-a-timestamp")

        output, attention = self._alerts()

        self.assertIn("읽을 수 없는 Candidate: 1", output)
        self.assertIn("HIST-EV-A7.json", attention[0])

    def test_the_message_says_it_stops_every_date(self):
        """The blast radius is the actionable part — it is why this is
        ATTENTION and not a line on a screen."""
        self._write("EV-BAD", project_id=7)

        _output, attention = self._alerts()

        self.assertIn("모든 날짜의", attention[0])

    def test_a_healthy_repository_says_nothing(self):
        self._write("EV-OK")

        output, attention = self._alerts()

        self.assertNotIn("읽을 수 없는 Candidate", output)
        self.assertEqual(attention, [])

    def test_the_shapes_the_pipeline_survives_are_not_reported_here_either(self):
        """The two sides agree in both directions, which is the point of
        sharing the predicate rather than copying it."""
        for label, override in (
            ("role=int", {"role": 5}),
            ("category=int", {"category": 9}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self._write("EV-SOFT", **override)

                _output, attention = self._alerts()

                self.assertEqual(attention, [])


class ADamagedManifestDoesNotKillTheStatusViewTests(unittest.TestCase):
    """C44. `metrics` that is not a mapping took the whole view down.

    `read_summary()` states plainly that it "validates only the three enums";
    `metrics` comes back as `c.get("metrics", {})`, whatever the JSON holds.
    The renderer had already been hardened once for that — `one_line()` on
    every key and every value, so a forged newline cannot fake a metric row
    (C38) — and the hardening assumed the container was a mapping.

    Measured, a manifest whose `metrics` was a string:

        AttributeError: 'str' object has no attribute 'items'
        raised out of `_print_last_run()`, out of `main()`
        the operator gets a traceback instead of ANY status

    Two of this file's own promises broken at once: the view "must still
    produce an answer when part of the evidence is damaged", and docs/10 §46
    makes a damaged state file something to REPORT. It is a disaster-recovery
    path — a restored or hand-edited manifest — which is precisely when
    someone runs this command.
    """

    BASE = {
        "schema_version": 1,
        "run_id": "R-1",
        "started_at": "2026-08-05T10:00:00+09:00",
        "finished_at": "2026-08-05T10:00:01+09:00",
    }

    def _failing(self, name, metrics):
        return {
            "name": name,
            "status": "FAILED",
            "failure": {
                "classification": "NOTION_SYNC_INCOMPLETE",
                "reason": "r",
                "retryability": "RETRYABLE",
                "severity": "DEGRADED",
            },
            "metrics": metrics,
        }

    def _run(self, components):
        import contextlib
        import importlib.util

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "runs").mkdir(parents=True)
        (runtime / "state").mkdir()
        data = dict(self.BASE)
        data["components"] = components
        manifest = runtime / "runs" / "last_run.json"
        manifest.write_text(json.dumps(data), encoding="utf-8")

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_damaged", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        module.DEFAULT_RUN_SUMMARY_PATH = manifest
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_last_run(NOW)
        return buffer.getvalue(), attention

    DAMAGED = {"str": "oops", "list": [1, 2], "int": 7}

    def test_it_does_not_raise(self):
        for label, metrics in self.DAMAGED.items():
            with self.subTest(kind=label):
                output, _attention = self._run([self._failing("notion_sync", metrics)])

                self.assertIn("notion_sync", output)

    def test_the_damage_is_named_rather_than_skipped(self):
        output, attention = self._run([self._failing("notion_sync", "oops")])

        self.assertIn("metrics 읽을 수 없음", output)
        self.assertIn("str", output)
        self.assertTrue(
            any("metrics가 손상" in item and "notion_sync" in item for item in attention),
            attention,
        )

    def test_the_rest_of_the_manifest_still_renders(self):
        """The reason this reports instead of failing the read: a partially
        damaged manifest still says which step failed and how badly, and that
        is most of what the block is for."""
        output, _attention = self._run(
            [
                self._failing("notion_sync", "oops"),
                {
                    "name": "daily",
                    "status": "FAILED",
                    "failure": {
                        "classification": "DAILY_CLOSE_FAILED",
                        "reason": "r2",
                        "retryability": "RETRYABLE",
                        "severity": "CRITICAL",
                    },
                    "metrics": {"generated_days": 0},
                },
            ]
        )

        self.assertIn("DAILY_CLOSE_FAILED", output)
        self.assertIn("generated_days=0", output)

    def test_a_healthy_manifest_is_unchanged(self):
        output, attention = self._run(
            [self._failing("notion_sync", {"queued": 3, "processed": 4})]
        )

        self.assertIn("processed=4 queued=3", output)
        self.assertNotIn("읽을 수 없음", output)
        self.assertEqual([a for a in attention if "metrics가 손상" in a], [])

    def test_an_empty_metrics_object_is_not_damage(self):
        """`{}` is what a step with nothing to report writes, and `None` is
        what an older manifest has. Neither may raise the alarm."""
        for metrics in ({}, None):
            with self.subTest(metrics=metrics):
                output, attention = self._run([self._failing("notion_sync", metrics)])

                self.assertNotIn("읽을 수 없음", output)
                self.assertEqual([a for a in attention if "metrics가 손상" in a], [])

    def test_artifact_refs_as_a_string_is_a_known_cosmetic_limit(self):
        """CHARACTERIZATION, deliberately not fixed here.

        `read_summary()` does `tuple(c.get("artifact_refs", ()))`, so a string
        becomes a tuple of characters before the renderer ever sees it — the
        container type is already gone by then and no guard at this end can
        tell it from a real tuple. It prints `a, b, c` instead of crashing,
        so the cost is cosmetic; closing it means widening `read_summary()`'s
        stated contract, which is a bigger change than this defect earns.
        """
        component = self._failing("notion_sync", {})
        component["artifact_refs"] = "abc"

        output, _attention = self._run([component])

        self.assertIn("evidence: a, b, c", output)


class AgentLockIsReportedTests(unittest.TestCase):
    """The Runner's lock was watched; the Agent's was not.

    C23 closed BUG-42's silence for `runtime/locks/company_ops.lock`:
    `stale_lock_cannot_be_cleared()` and `lock_held_since()` both feed
    ATTENTION from `_print_last_run()`. `agent/agent.py` reuses the very same
    `scheduler.lock` module against its own file,
    `runtime/agent/locks/agent.lock`, and nothing looked at it.

    The asymmetry is the wrong way round. A stuck Runner lock stops the
    machine that *assembles* Company History, which the Run Manifest and
    every history counter notice. A stuck Agent lock stops a machine that
    *produces* it, and `run_agent.py` returns **exit 0** for
    `SKIPPED_ALREADY_RUNNING` — its own docstring says so ("0 COMPLETED, or
    skipped because another Agent run holds the lock"). Task Scheduler
    therefore records a successful run, every day, while nothing is
    collected.

    Measured before this: a lock file naming a dead pid, made read-only —
    `stale_lock_cannot_be_cleared()` returned True and the AGENT section
    printed nothing. The one trace anywhere was `needs_attention()`'s "agent
    has not run for N day(s)", which takes N days to appear and reports a
    symptom, not a cause.

    Read-only throughout: the three lock readers used here are the
    non-competing ones (`is_locked` / `lock_held_since` /
    `stale_lock_cannot_be_cleared`), never `try_acquire_lock()`, because this
    script promises it is safe to run while an Agent is working.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        self.agent_dir = self.runtime / "agent"
        for relative in ("locks", "state", "outbox", "sent", "signals_rejected"):
            (self.agent_dir / relative).mkdir(parents=True, exist_ok=True)
        self.lock = self.agent_dir / "locks" / "agent.lock"

    def _write_lock(self, *, pid, created_at, read_only=False):
        # `process_id` / `created_at` verbatim — the on-disk shape
        # `try_acquire_lock()` writes and `LockFileContractTests` pins. A
        # fixture inventing its own field names would test nothing.
        self.lock.write_text(
            json.dumps({"process_id": pid, "created_at": created_at}),
            encoding="utf-8",
        )
        if read_only:
            os.chmod(self.lock, stat.S_IREAD)
            self.addCleanup(self._make_writable)

    def _make_writable(self):
        """Cleanup that tolerates a test having already removed the file."""
        try:
            os.chmod(self.lock, stat.S_IWRITE)
        except OSError:
            pass

    def _run(self, now=None):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_agent_lock", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # `RUNTIME_DIR` alone. `AGENT_DIR` used to have to be set here too,
        # and setting only one of them silently pointed the AGENT block at
        # the developer's real `runtime/agent` — see
        # `RuntimeDirIsTheOnlyKnobTests`.
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_agent(now or NOW)
        return buffer.getvalue(), attention

    DEAD_PID = 999_999

    def test_a_stale_unremovable_agent_lock_reaches_attention(self):
        self._write_lock(pid=self.DEAD_PID, created_at=NOW.isoformat(), read_only=True)

        output, attention = self._run()

        self.assertIn("Agent Lock", output)
        self.assertTrue([a for a in attention if "Agent Lock 파일이 남아" in a], attention)

    def test_the_message_names_the_exit_code_that_hides_it(self):
        """The operator's problem is not that a run failed — it is that every
        run *succeeded*. Saying so is the whole point of the line."""
        self._write_lock(pid=self.DEAD_PID, created_at=NOW.isoformat(), read_only=True)

        _output, attention = self._run()

        message = next(a for a in attention if "Agent Lock 파일이 남아" in a)
        self.assertIn("exit code는 0", message)
        self.assertIn(str(self.lock), message)

    def test_it_names_the_agent_lock_not_the_runner_lock(self):
        """Two different files protecting two different critical sections. A
        report pointed at the wrong one would send an operator to a machine
        that is fine."""
        self._write_lock(pid=self.DEAD_PID, created_at=NOW.isoformat(), read_only=True)

        _output, attention = self._run()

        message = next(a for a in attention if "Agent Lock 파일이 남아" in a)
        self.assertIn("agent.lock", message)
        self.assertNotIn("company_ops.lock", message)

    def test_a_lock_held_far_too_long_reaches_attention(self):
        held_since = NOW - timedelta(hours=48)
        self._write_lock(pid=os.getpid(), created_at=held_since.isoformat())

        output, attention = self._run()

        self.assertIn("Agent Lock", output)
        self.assertTrue([a for a in attention if "Agent Lock이" in a], attention)

    def test_a_lock_held_briefly_is_shown_but_not_alerted(self):
        """A running Agent is normal. Alerting on it would be the standing
        alert this project keeps removing."""
        held_since = NOW - timedelta(minutes=2)
        self._write_lock(pid=os.getpid(), created_at=held_since.isoformat())

        output, attention = self._run()

        self.assertIn("Agent Lock          : 보유 중", output)
        self.assertEqual([a for a in attention if "Agent Lock" in a], [])

    def test_no_lock_file_says_nothing_at_all(self):
        output, attention = self._run()

        self.assertNotIn("Agent Lock", output)
        self.assertEqual([a for a in attention if "Agent Lock" in a], [])

    def test_removing_the_lock_clears_the_line(self):
        """C26's rule: the correct remediation — the one the message asks for
        — has to make the alert go away."""
        self._write_lock(pid=self.DEAD_PID, created_at=NOW.isoformat(), read_only=True)
        self.assertTrue([a for a in self._run()[1] if "Agent Lock" in a])

        os.chmod(self.lock, stat.S_IWRITE)
        self.lock.unlink()

        self.assertEqual([a for a in self._run()[1] if "Agent Lock" in a], [])

    def test_a_damaged_lock_file_does_not_break_the_view(self):
        """This view's contract is that it answers even when the evidence is
        damaged."""
        self.lock.write_text("{not json", encoding="utf-8")

        output, attention = self._run()

        self.assertIn("AGENT", output)
        self.assertIsInstance(attention, list)

    def test_the_agent_really_does_skip_on_a_lock_it_cannot_take(self):
        """The premise, checked rather than assumed: `run_once()` must
        actually refuse, or the report describes nothing."""
        from agent.agent import DEFAULT_LOCK_PATH  # noqa: F401
        from scheduler.lock import stale_lock_cannot_be_cleared, try_acquire_lock

        self._write_lock(pid=self.DEAD_PID, created_at=NOW.isoformat(), read_only=True)

        self.assertTrue(stale_lock_cannot_be_cleared(self.lock))
        self.assertFalse(try_acquire_lock(self.lock, now=NOW))


class UnreadableIncomingFileTests(CompanyActivityTestCase):
    """The `unparseable` fix, applied to the pile it was never applied to.

    `transport/` got this treatment when a 0-byte Files On-Demand
    placeholder held `awaiting_intake` at 1 forever. `incoming/` has the
    identical failure and kept the identical symptom:

        run 1..3   collector failed=1 every run, file never leaves incoming/
                   awaiting_collection=1, is_clear=False, every run
                   ATTENTION "수집되지 않고 남은 Event: incoming=1"

    `collector/runtime.run_once()` reads each file with
    `read_text(encoding="utf-8")`. When that raises it records FAILED and
    leaves the file — the read is deterministic and nothing rewrites the
    file, so this repeats forever. `name_collision` (BUG-43) covers a
    different permanent-FAILED cause and does not see this one.

    **The predicate had to be the Collector's, not intake's.** They disagree
    on a case that matters: a valid-UTF-8 file holding invalid JSON is
    `unparseable` to intake, but `collector.collect()` REJECTS it and moves
    it to `rejected/` on the first run. Reporting it as stuck would describe
    a file that is on its way out — the "view disagrees with the step"
    mistake this project keeps closing. So `is_readable_event_file()` is
    exported from `collector/runtime.py` and shares one read helper with
    `run_once()` itself.
    """

    UNDECODABLE = b'{"event_id": "\xff\xfe\x00bad"}'

    def _incoming(self, name, content):
        self.incoming.mkdir(parents=True, exist_ok=True)
        target = self.incoming / name
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def test_an_undecodable_file_is_not_counted_as_awaiting_collection(self):
        self._incoming("BAD-UTF8.json", self.UNDECODABLE)

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_collection, 0)
        self.assertEqual(backlog.unreadable_incoming, 1)

    def test_an_undecodable_file_alone_leaves_the_backlog_clear(self):
        self._incoming("BAD-UTF8.json", self.UNDECODABLE)

        self.assertTrue(self.snapshot().backlog.is_clear)

    def test_a_readable_file_is_still_counted(self):
        """The guard must not hide real backlog."""
        self._incoming("GOOD.json", '{"event_id": "E-1", "source": "DESKTOP_1"}')

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_collection, 1)
        self.assertEqual(backlog.unreadable_incoming, 0)
        self.assertFalse(backlog.is_clear)

    def test_invalid_json_that_is_valid_utf8_is_still_counted(self):
        """The case where the Collector's predicate and intake's disagree.
        `collector.collect()` REJECTS this and moves it out on the first
        run, so it is in flight, not parked."""
        self._incoming("BAD-JSON.json", '{"event_id"')

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.unreadable_incoming, 0)
        self.assertEqual(backlog.awaiting_collection, 1)

    def test_the_source_breakdown_still_matches_the_count(self):
        """`SourceBreakdown.total` promises to equal the count it breaks
        down. Splitting the count without splitting the attribution would
        have quietly broken that."""
        self._incoming("GOOD.json", '{"event_id": "E-1", "source": "DESKTOP_1"}')
        self._incoming("BAD-UTF8.json", self.UNDECODABLE)

        backlog = self.snapshot().backlog

        self.assertEqual(
            backlog.awaiting_collection_sources.total, backlog.awaiting_collection
        )

    def test_the_view_agrees_with_what_the_collector_actually_does(self):
        """Bound to behaviour, not to a copy of the rule: run the real
        Collector three times and check that what stays is what this view
        calls unreadable, and what leaves is what it calls backlog."""
        from collector.collector import Collector
        from collector.runtime import run_once as collector_run_once
        from collector.state import PersistentSeenEventStore

        self._incoming("BAD-UTF8.json", self.UNDECODABLE)
        self._incoming("BAD-JSON.json", '{"event_id"')
        store = PersistentSeenEventStore(state_path=self.root / "seen.json")

        for run in range(3):
            with self.subTest(run=run):
                collector_run_once(
                    collector=Collector(seen_store=store),
                    incoming_dir=self.incoming,
                    processed_dir=self.processed,
                    rejected_dir=self.rejected,
                    log_path=self.root / "collector.log",
                )
                backlog = self.snapshot().backlog

                self.assertEqual(
                    sorted(p.name for p in self.incoming.iterdir()), ["BAD-UTF8.json"]
                )
                self.assertEqual(backlog.unreadable_incoming, 1)
                self.assertEqual(backlog.awaiting_collection, 0)
                self.assertTrue(backlog.is_clear)

    def test_it_is_reported_to_the_operator(self):
        import contextlib
        import importlib.util

        self._incoming("BAD-UTF8.json", self.UNDECODABLE)

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_unreadable", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.root
        module.read_company_activity = lambda **_: self.snapshot()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_company(NOW)

        message = [a for a in attention if "읽을 수 없는 파일" in a]
        self.assertEqual(len(message), 1, attention)
        self.assertIn("incoming 1건", message[0])
        self.assertEqual([a for a in attention if "수집되지 않고 남은 Event" in a], [])

    def test_the_predicate_is_the_collectors_own(self):
        """A second opinion about "can this be read" is exactly the
        disagreement that produced the wrong count in the first place."""
        import inspect

        import app.desktop_activity as activity

        source = inspect.getsource(activity.read_company_activity)
        self.assertIn("is_readable_event_file", source)

        import collector.runtime as runtime

        self.assertIn("_read_event_text", inspect.getsource(runtime.run_once))
        self.assertIn("_read_event_text", inspect.getsource(runtime.is_readable_event_file))


class FailingComponentMetricsAreShownTests(unittest.TestCase):
    """The Run Manifest's richest field reached no reader.

    `recorder.ok()` / `recorder.failed()` take `**metrics` and every step in
    `app/runner.py` passes them — `queued`, `processed`, `accepted`,
    `failed`, `changed_files`, `generated_days`, `still_pending`,
    `failed_date`. They are written into `run_summary.json` and, before this,
    read by nothing outside the test suite.

    That is BUG-39's shape one layer up. BUG-39 was `IntakeSummary.failed` /
    `skipped_*` being computed and discarded; the fix routed them into the
    manifest. They arrived, and then stopped there.

    What it costs an operator, in the case that matters most: a Notion
    outage records

        ! notion_sync: NOTION_SYNC_INCOMPLETE [DEGRADED/RETRYABLE]

    identically whether one Event is queued or four hundred are. Those are
    different situations — "the next run will catch up" versus "Company
    History has been diverging from Notion for weeks" — and the number
    distinguishing them was already on disk. RETRYABLE keeps such a failure
    out of ATTENTION by design (docs/14 §5), so this line is the only place
    an operator can see it at all.

    Only non-SUCCESS components print metrics: the block deliberately hides
    healthy steps, and this must not turn it into a wall of numbers.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "state").mkdir(parents=True)
        (self.runtime / "locks").mkdir(parents=True)
        self.manifest = self.runtime / "state" / "run_summary.json"

    def _write_manifest(self, components):
        self.manifest.write_text(
            json.dumps(
                {
                    "run_id": "2026-08-13T09:00:00+09:00",
                    "started_at": "2026-08-13T09:00:00+09:00",
                    "finished_at": "2026-08-13T09:01:00+09:00",
                    "overall_status": "DEGRADED",
                    "exit_code": 3,
                    "components": components,
                }
            ),
            encoding="utf-8",
        )

    def _failing(self, name, metrics, *, retryability="RETRYABLE"):
        return {
            "name": name,
            "status": "FAILED",
            "metrics": metrics,
            "failure": {
                "classification": "NOTION_SYNC_INCOMPLETE",
                "severity": "DEGRADED",
                "retryability": retryability,
                "reason": "connection refused",
            },
        }

    def _run(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_metrics", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        module.DEFAULT_RUN_SUMMARY_PATH = self.manifest
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_last_run(NOW)
        return buffer.getvalue(), attention

    def test_a_failing_components_metrics_are_printed(self):
        self._write_manifest([self._failing("notion_sync", {"queued": 47, "processed": 50})])

        output, _attention = self._run()

        self.assertIn("queued=47", output)
        self.assertIn("processed=50", output)

    def test_the_number_that_distinguishes_one_from_four_hundred(self):
        """The point of the change, stated as a test: two runs whose
        classification line is identical must not read identically."""
        self._write_manifest([self._failing("notion_sync", {"queued": 1})])
        small, _ = self._run()
        self._write_manifest([self._failing("notion_sync", {"queued": 400})])
        large, _ = self._run()

        self.assertNotEqual(small, large)
        self.assertIn("queued=1", small)
        self.assertIn("queued=400", large)

    def test_metrics_are_printed_in_a_stable_order(self):
        self._write_manifest(
            [self._failing("notion_sync", {"queued": 2, "processed": 9, "accepted": 4})]
        )

        output, _attention = self._run()

        self.assertIn("accepted=4 processed=9 queued=2", output)

    def test_a_component_with_no_metrics_prints_no_extra_line(self):
        self._write_manifest([self._failing("notion_sync", {})])

        output, _attention = self._run()

        self.assertIn("notion_sync", output)
        self.assertNotIn("      \n", output)

    def test_successful_components_stay_hidden(self):
        """The block hides healthy steps on purpose; printing their metrics
        would undo that."""
        self._write_manifest(
            [
                {"name": "collector", "status": "SUCCESS", "metrics": {"accepted": 12}},
                self._failing("notion_sync", {"queued": 1}),
            ]
        )

        output, _attention = self._run()

        self.assertNotIn("accepted=12", output)
        self.assertIn("queued=1", output)

    def test_a_line_breaking_metric_value_cannot_forge_a_line(self):
        """Today every metric is one of this project's own counters. The
        escaping does not depend on that staying true — a manifest is a file
        read back from disk, and `oplog.one_line()` is this project's answer
        for anything rendered from one."""
        self._write_manifest(
            [self._failing("daily", {"failed_date": "2026-08-01\n  ! backup: ALL GOOD"})]
        )

        output, _attention = self._run()

        self.assertIn("\\n", output)
        self.assertNotIn("\n  ! backup: ALL GOOD", output)

    def test_the_failure_reason_is_still_not_printed_here(self):
        """`reason` is the one failure field that carries text from outside
        this system (a Notion API message, an exception string). It is
        unchanged by this — metrics only."""
        self._write_manifest([self._failing("notion_sync", {"queued": 1})])

        output, _attention = self._run()

        self.assertNotIn("connection refused", output)


class MonthlyStateConsistencyTests(unittest.TestCase):
    """docs/10 §48's check, aimed at the pair nobody aimed it at.

    `scheduler/consistency.py` implements §48 — "State Last Success ->
    Corresponding Local History 존재?" — and `ops_status.py` calls it, for
    the Daily pair. `monthly_history_state.json` makes the identical kind of
    claim: `last_successful_monthly_close` says a month is consolidated, and
    the artifact backing that claim is `monthly/<YYYY-MM>.md`. Nothing
    compared the two. §48 does not say "daily only".

    Why it is data loss rather than cosmetics: `run_once()` takes its
    catch-up months from `pending_months()`, which starts *after* the
    pointer. A month below the pointer is never revisited by any run, ever.
    Measured, pointer at `2026-07` with the file removed:

        monthly_run_once()   returned no results at all
        ops_status           "monthly 파일: 0" and "마지막 통합한 달: 2026-07"
                             printed two lines apart, nothing connecting them
        ATTENTION            empty

    A month of Company History gone, every indicator healthy.

    **It cannot be a false alarm, and that is checked below rather than
    asserted.** The pointer advances on exactly two outcomes —
    `MONTHLY_GENERATED` (file just written) and `MONTHLY_UNCHANGED` (file
    already there) — so the file existed when the pointer was set. Any other
    outcome breaks the loop without advancing. That property is what makes
    "pointer set, file absent" unambiguous, and C24/C26 are why it is
    tested: a detector whose clean case is not verified is a standing false
    alarm waiting to happen.

    Detection only, like every other check in this block. Regenerating the
    month is docs/10 §46's prohibition and §49's operator call.
    """

    NOW = datetime(2026, 8, 13, 9, 0).astimezone()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        self.daily = self.runtime / "local_master" / "daily"
        self.monthly = self.runtime / "local_master" / "monthly"
        for relative in (
            self.daily,
            self.monthly,
            self.runtime / "state",
            self.runtime / "events" / "processed",
            self.runtime / "history_candidates" / "keep",
            self.runtime / "history_candidates" / "review",
        ):
            relative.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime / "state" / "monthly_history_state.json"

    def _daily_month(self, year, month, days):
        for day in days:
            (self.daily / f"{year}-{month:02d}-{day:02d}.md").write_text(
                f"# DOJOONPASS Company History — {year}-{month:02d}-{day:02d}\n\n"
                f"## Summary\n\nwork\n",
                encoding="utf-8",
            )

    def _write_state(self, closed, dirty=()):
        self.state_path.write_text(
            json.dumps(
                {"last_successful_monthly_close": closed, "dirty_months": list(dirty)}
            ),
            encoding="utf-8",
        )

    def _run(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_monthly", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(self.NOW)
        return buffer.getvalue(), attention

    def _monthly_alerts(self, attention):
        return [a for a in attention if "Monthly State와 실제 History가 어긋난다" in a]

    def test_a_pointer_with_no_file_reaches_attention(self):
        self._daily_month(2026, 7, range(1, 4))
        self._write_state("2026-07")

        output, attention = self._run()

        self.assertIn("STATE_INCONSISTENCY", output)
        self.assertEqual(len(self._monthly_alerts(attention)), 1, attention)

    def test_the_message_says_no_run_will_fix_it(self):
        """The operator's question is "will this sort itself out?". For this
        condition the answer is no, and saying so is the point."""
        self._write_state("2026-07")

        _output, attention = self._run()

        message = self._monthly_alerts(attention)[0]
        self.assertIn("2026-07", message)
        self.assertIn("다시 만들지 않는다", message)

    def test_a_pointer_with_its_file_present_says_nothing(self):
        (self.monthly / "2026-07.md").write_text("# 2026-07\n", encoding="utf-8")
        self._write_state("2026-07")

        output, attention = self._run()

        self.assertNotIn("STATE_INCONSISTENCY", output)
        self.assertEqual(self._monthly_alerts(attention), [])

    def test_no_pointer_yet_says_nothing(self):
        """A first-ever run claims nothing, so there is nothing to contradict."""
        self._write_state(None)

        output, attention = self._run()

        self.assertNotIn("STATE_INCONSISTENCY", output)
        self.assertEqual(self._monthly_alerts(attention), [])

    def test_restoring_the_file_clears_the_line(self):
        """C26's rule. The remediation here is restoring the Monthly file
        from the backup remote, and that has to make the alert go away."""
        self._write_state("2026-07")
        self.assertTrue(self._monthly_alerts(self._run()[1]))

        (self.monthly / "2026-07.md").write_text("# 2026-07\n", encoding="utf-8")

        self.assertEqual(self._monthly_alerts(self._run()[1]), [])

    def test_a_real_consolidation_never_triggers_it(self):
        """The false-alarm guard, run against the real generator rather than
        a hand-written state file: consolidate a month for real, then check
        the view is silent."""
        from monthly import run_once as monthly_run_once

        self._daily_month(2026, 7, range(1, 32))
        self._write_state(None)

        result = monthly_run_once(
            daily_dir=self.daily,
            monthly_dir=self.monthly,
            state_path=self.state_path,
            now=self.NOW,
            history_start_date=date(2026, 7, 1),
        )

        self.assertTrue(result.results, "expected the month to consolidate")
        _output, attention = self._run()
        self.assertEqual(self._monthly_alerts(attention), [])

    def test_the_pointer_only_advances_when_the_file_exists(self):
        """The premise the check rests on, asserted directly: after any run,
        a set pointer implies its file is on disk."""
        from monthly import load_state as load_monthly_state
        from monthly import monthly_history_path
        from monthly import run_once as monthly_run_once

        # July complete, August incomplete -> the loop must stop at August.
        self._daily_month(2026, 7, range(1, 32))
        self._daily_month(2026, 8, [1])
        self._write_state(None)

        monthly_run_once(
            daily_dir=self.daily,
            monthly_dir=self.monthly,
            state_path=self.state_path,
            now=self.NOW,
            history_start_date=date(2026, 7, 1),
        )

        closed = load_monthly_state(self.state_path).last_successful_monthly_close
        self.assertIsNotNone(closed)
        self.assertTrue(monthly_history_path(self.monthly, closed).is_file())

    def test_the_view_looks_where_the_writer_writes(self):
        """A second opinion about the filename would make the check answer a
        question about a path that does not exist."""
        import inspect

        import monthly.generator as generator

        source = inspect.getsource(generator)
        self.assertIn("final_path = monthly_history_path(", source)
        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        self.assertIn("monthly_history_path(monthly_dir, closed)", path.read_text(encoding="utf-8"))


class CommittedStagingResidueTests(unittest.TestCase):
    """The signal C27's own fix removed, put back.

    C27 excluded `.tmp-*` from `working_copy._is_in_scope()`. That was right:
    it stopped a staging file from being synced and committed as Company
    History, and it disarmed the trap where *cleaning the file up* made the
    deletion gate fail every subsequent Backup.

    Exclusion cuts both ways. `_relative_files()` is applied to Master **and**
    to the Working Copy, so a staging file that the pre-C27 code already
    synced and committed is now outside both sides — `sync_to_working_copy()`
    reports nothing about it, forever.

    Measured, `daily/.tmp-abc123.md` holding a truncated day, already in the
    commit, running the post-C27 code:

        sync_to_working_copy()   added=() modified=() deleted=()
        scan_for_secrets(wc)     ()          -- it is not secret-shaped
        ops_status ATTENTION     []          -- nothing, anywhere

    Truncated Company History in the backup remote with no trace. That is
    exactly the shape C24 and C26 are about, and this time the instrument
    that went blind was this Sprint's own change. **A change that removes a
    bad signal owes a good one in its place.**

    The probe is `_would_reach_the_commit()`, the same git-aware one C26
    built for the secret report, for the same reason: what matters is what
    git carries, not what the filesystem holds. A `.gitignore` covering the
    file makes this silent, because then it really is not going anywhere.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "state").mkdir(parents=True)
        self.wc = self.runtime / "backup_working_copy"
        (self.wc / "daily").mkdir(parents=True)
        (self.wc / "daily" / "2026-08-13.md").write_text("# real\n", encoding="utf-8")

    def _git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.wc,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _init_repo(self, *, gitignore=None):
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Residue Test")
        if gitignore is not None:
            (self.wc / ".gitignore").write_text(gitignore, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")

    def _plant(self, name="daily/.tmp-abc123.md"):
        target = self.wc / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# DOJOONPASS Company Hist", encoding="utf-8")
        return target

    def _warnings(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_residue_wc", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return [item for item in attention if "완료되지 않은 쓰기 잔여물" in item]

    def test_committed_residue_is_reported(self):
        self._plant()
        self._init_repo()

        warnings = self._warnings()

        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn(".tmp-abc123.md", warnings[0])

    def test_the_message_says_it_is_safe_to_delete(self):
        """The operator action here is the opposite of every other Working
        Copy warning: this is garbage, not a credential to rotate."""
        self._plant()
        self._init_repo()

        self.assertIn("지워도 안전하다", self._warnings()[0])

    def test_sync_really_does_say_nothing_about_it(self):
        """The premise, checked rather than asserted: this is reported here
        precisely because the Backup path no longer can."""
        from backup.working_copy import scan_for_secrets, sync_to_working_copy

        master = self.runtime / "local_master"
        (master / "daily").mkdir(parents=True)
        (master / "daily" / "2026-08-13.md").write_text("# real\n", encoding="utf-8")
        self._plant()
        self._init_repo()

        result = sync_to_working_copy(master, self.wc)

        self.assertEqual((result.added, result.modified, result.deleted), ((), (), ()))
        self.assertEqual(scan_for_secrets(self.wc), ())

    def test_a_gitignored_staging_file_is_not_reported(self):
        """docs/08 §28's `.gitignore` lists `*.tmp` but not `.tmp-*`; an
        operator who adds a pattern that does cover them has genuinely fixed
        it, and the line must go quiet — C26's rule."""
        self._plant()
        self._init_repo(gitignore=".tmp-*\n")

        self.assertEqual(self._warnings(), [])

    def test_git_really_does_refuse_to_commit_it(self):
        """The premise of the test above."""
        self._plant()
        self._init_repo(gitignore=".tmp-*\n")

        committed = self._git("ls-tree", "-r", "--name-only", "HEAD").stdout.split()

        self.assertNotIn("daily/.tmp-abc123.md", committed)

    def test_deleting_it_clears_the_line(self):
        staged = self._plant()
        self._init_repo()
        self.assertTrue(self._warnings())

        staged.unlink()

        self.assertEqual(self._warnings(), [])

    def test_a_clean_working_copy_says_nothing(self):
        self._init_repo()

        self.assertEqual(self._warnings(), [])

    def test_real_company_history_is_never_reported(self):
        """The guard must not start calling Daily files garbage."""
        self._init_repo()

        warnings = self._warnings()

        self.assertEqual(warnings, [])
        committed = self._git("ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        self.assertIn("daily/2026-08-13.md", committed)

    def test_gits_own_storage_is_never_reported(self):
        """`.git/` is git's storage, not Working Copy content. On the normal
        path `git ls-files` would filter it out anyway; the reason to skip it
        explicitly is the fail-safe path, where a missing or timed-out git
        makes `_would_reach_the_commit()` return its candidates unchanged."""
        self._init_repo()
        internal = self.wc / ".git" / ".tmp-gitinternal.pack"
        internal.parent.mkdir(parents=True, exist_ok=True)
        internal.write_text("x", encoding="utf-8")

        self.assertEqual(self._warnings(), [])

    def test_gits_own_storage_is_not_reported_on_the_fail_safe_path_either(self):
        """No repository at all: the probe cannot ask git, so it reports its
        candidates as-is — and `.git/` must not be among them."""
        internal = self.wc / ".git" / ".tmp-gitinternal.pack"
        internal.parent.mkdir(parents=True, exist_ok=True)
        internal.write_text("x", encoding="utf-8")
        real = self._plant()

        warnings = self._warnings()

        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn(real.name, warnings[0])
        self.assertNotIn("gitinternal", warnings[0])

    def test_a_non_repository_working_copy_still_reports(self):
        """Fail-safe, same direction as C26's probe: a probe that cannot get
        an answer over-reports rather than going quiet."""
        self._plant()

        warnings = self._warnings()

        self.assertEqual(len(warnings), 1, warnings)

    def test_it_is_independent_of_the_secret_report(self):
        """Two different conditions, two different operator actions —
        rotate a credential versus delete a stray file. A staging file that
        is also secret-shaped must not collapse them."""
        self._plant("daily/.tmp-abc123.md")
        (self.wc / ".env").write_text("TOKEN=" + "x" * 40 + "\n", encoding="utf-8")
        self._init_repo()

        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_residue_both", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)

        self.assertTrue([a for a in attention if "Secret 형태의 파일" in a], attention)
        self.assertTrue([a for a in attention if "완료되지 않은 쓰기 잔여물" in a], attention)


class RunnerHasNotRunTests(unittest.TestCase):
    """The Agent has this check. The Runner — which does the work — did not.

    `AgentStatusSnapshot.needs_attention()` has reported "agent has not run
    for N day(s)" since it was written. `_print_last_run()` printed
    `started_at` and never compared it to anything, so a Runner that simply
    stops leaves the LAST RUN block showing its last SUCCESS, in green,
    indefinitely.

    That is the more dangerous half of the pair. The Runner is the machine
    that assembles Company History from collected Events, closes Daily and
    Monthly, and pushes the Backup. When it stops, all of that stops — and
    the ways it stops are ordinary Windows ones: a Task Scheduler task
    disabled after a password change (docs/11's own runbook covers
    re-registering it), a machine left asleep, the task deleted.

    Measured on this machine before the check existed: the last run was two
    days old, and ATTENTION carried "agent has not run for 2 day(s)" and
    nothing whatsoever about the Runner.

    Symmetric with the Agent Lock finding earlier this Sprint, in the
    opposite direction — the Runner had lock monitoring and no staleness
    check; the Agent had staleness and no lock monitoring. Neither gap was
    a decision; both were a check aimed at one of two targets.

    `SILENT_AFTER_DAYS` is reused rather than a new threshold chosen. Its
    existing comment is exactly the reasoning required here: a machine
    switched off for a weekend is normal in this deployment (docs/07 §58),
    and a threshold that fires every Monday gets ignored.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "state").mkdir(parents=True)
        (self.runtime / "locks").mkdir(parents=True)
        self.manifest = self.runtime / "state" / "run_summary.json"

    def _write_manifest(self, started_at):
        self.manifest.write_text(
            json.dumps(
                {
                    "run_id": str(started_at),
                    "started_at": started_at,
                    "finished_at": started_at,
                    "overall_status": "SUCCESS",
                    "exit_code": 0,
                    "components": [{"name": "collector", "status": "SUCCESS"}],
                }
            ),
            encoding="utf-8",
        )

    def _run(self, now=None):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_stale_runner", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        module.DEFAULT_RUN_SUMMARY_PATH = self.manifest
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_last_run(now or NOW)
        return buffer.getvalue(), [a for a in attention if "Runner가" in a]

    def _threshold(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_threshold", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.SILENT_AFTER_DAYS

    def test_a_runner_that_stopped_reaches_attention(self):
        self._write_manifest((NOW - timedelta(days=9)).isoformat())

        _output, alerts = self._run()

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("Runner가", alerts[0])

    def test_the_message_says_what_stopped_with_it(self):
        """"The Runner did not run" is only half the fact an operator needs;
        the other half is that Company History and Backup stopped too."""
        self._write_manifest((NOW - timedelta(days=9)).isoformat())

        _output, alerts = self._run()

        self.assertIn("Company History", alerts[0])
        self.assertIn("Backup", alerts[0])

    def test_a_recent_run_says_nothing(self):
        self._write_manifest((NOW - timedelta(hours=6)).isoformat())

        _output, alerts = self._run()

        self.assertEqual(alerts, [])

    def test_the_boundary_is_the_existing_silence_threshold(self):
        """No new number was invented. Just under fires nothing, just over
        fires — bound to the constant, not to a literal."""
        days = self._threshold()

        self._write_manifest((NOW - timedelta(days=days, hours=1)).isoformat())
        self.assertEqual(len(self._run()[1]), 1)

        self._write_manifest((NOW - timedelta(days=days, hours=-1)).isoformat())
        self.assertEqual(self._run()[1], [])

    def test_running_it_again_clears_the_line(self):
        """C26's rule. The remediation is re-registering the scheduled task,
        and the next run's manifest has to make this go away."""
        self._write_manifest((NOW - timedelta(days=9)).isoformat())
        self.assertTrue(self._run()[1])

        self._write_manifest(NOW.isoformat())

        self.assertEqual(self._run()[1], [])

    def test_no_manifest_at_all_is_not_reported_as_stale(self):
        """A first-ever install has no run to be stale, and the block already
        says "아직 기록된 실행이 없다"."""
        output, alerts = self._run()

        self.assertIn("아직 기록된 실행이 없다", output)
        self.assertEqual(alerts, [])

    def test_an_unparseable_timestamp_does_not_break_the_view(self):
        """This view answers even when part of the evidence is damaged."""
        self._write_manifest("not-a-timestamp")

        output, alerts = self._run()

        self.assertIn("LAST RUN", output)
        self.assertEqual(alerts, [])

    def test_a_naive_timestamp_does_not_raise(self):
        """A hand-edited or restored manifest can carry an offset-less
        timestamp, and comparing it to an aware `now` raises TypeError —
        the naive/aware mistake this repository has already made once."""
        self._write_manifest((NOW - timedelta(days=9)).replace(tzinfo=None).isoformat())

        _output, alerts = self._run()

        self.assertEqual(len(alerts), 1, alerts)

    def test_it_reports_a_stopped_runner_even_when_the_last_run_succeeded(self):
        """The whole point: SUCCESS is what makes this invisible. A failed
        run is already loud."""
        self._write_manifest((NOW - timedelta(days=9)).isoformat())

        output, alerts = self._run()

        self.assertIn("SUCCESS", output)
        self.assertTrue(alerts)


class UnbackedCompanyHistoryTests(unittest.TestCase):
    """"Is what is on this machine actually off it?" — the question the
    status view could not answer.

    `backup_state.json` has carried `last_successful_backup` since the Backup
    step was written and **no production code has ever read it**. The suite
    already says so, in the BUG-55 characterization: *"the one artifact that
    would betray it is `last_successful_backup` never advancing, which
    nothing surfaces."*

    BUG-55 is what that costs. `working_copy._is_in_scope()` compares
    `parts[0]` against `{"daily", "monthly"}` case-sensitively, and docs/11's
    deployment steps have a human create the directories. On a filesystem
    that folds case, a `Daily/` directory is the same directory to everything
    except that comparison. Reproduced end to end against a real bare remote,
    three consecutive runs:

        run 1..3   BACKUP_NOT_REQUIRED, changed=()
        remote     holds nothing
        state      last_successful_backup = None
        ops_status "daily 파일: 1", ATTENTION empty

    A real day of Company History, on one machine only, with every indicator
    green — and this view even counting the file, because `glob()` folds case
    where the scope check does not.

    **A clock threshold would have been the wrong instrument.** History that
    has not changed does not need backing up, so "the last backup was N days
    ago" is normal on a quiet week and would be a standing false alarm — the
    shape this project keeps removing. The condition that is never normal is
    *history newer than the last successful push*: it cannot fire while
    nothing is being written, and it clears the moment a backup succeeds.
    Both halves are asserted below, against the real Backup runner.

    The scan deliberately does NOT reuse `_is_in_scope()`. Doing so would
    inherit the case-sensitivity that causes BUG-55 and leave the check blind
    to the one defect it exists for.

    Detection only. Case-folding the scope comparison is BUG-55's own open
    decision (it changes which files Backup covers); this reports, and names
    the file, which is how an operator sees the wrong-case directory at all.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        self.master = self.runtime / "local_master"
        self.wc = self.runtime / "backup_working_copy"
        (self.runtime / "state").mkdir(parents=True)
        self.wc.mkdir(parents=True)
        self.state_path = self.runtime / "state" / "backup_state.json"
        self.remote = self.root / "remote.git"

    def _git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.strip()

    def _init_remote(self):
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(self.remote)],
            capture_output=True, check=True,
        )
        self._git(self.wc, "init", "-b", "main")
        self._git(self.wc, "config", "user.email", "test@example.invalid")
        self._git(self.wc, "config", "user.name", "Unbacked Test")
        self._git(self.wc, "remote", "add", "origin", str(self.remote))
        (self.wc / ".gitkeep").write_text("", encoding="utf-8")
        self._git(self.wc, "add", "-A")
        self._git(self.wc, "commit", "-m", "init")
        self._git(self.wc, "push", "-u", "origin", "main")

    def _backup(self, run_id="RUN"):
        import backup.runner as backup_runner

        return backup_runner.run_once(
            master_dir=self.master, working_copy_dir=self.wc,
            state_path=self.state_path, run_id=run_id,
        )

    def _alerts(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_unbacked", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(datetime.now().astimezone())
        return buffer.getvalue(), [a for a in attention if "원격 백업에 도달하지" in a]

    def _write_day(self, relative):
        target = self.master / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# a real day of history\n", encoding="utf-8")
        return target

    # ---- the defect ----------------------------------------------------

    def test_bug_55_history_is_reported_as_unbacked(self):
        from backup.result import BackupStatus

        self._init_remote()
        self._write_day("Daily/2026-08-13.md")  # wrong case, per docs/11 setup

        for run in range(3):
            entry = self._backup(f"RUN-{run}")
            self.assertIs(entry.final_status, BackupStatus.NOT_REQUIRED)
        self.assertEqual(
            sorted(self._git(self.remote, "ls-tree", "-r", "--name-only", "HEAD").split()),
            [".gitkeep"],
        )

        _output, alerts = self._alerts()

        self.assertEqual(len(alerts), 1, alerts)

    def test_the_alert_names_the_file_and_a_second_line_names_the_cause(self):
        """Two lines with two jobs, split in C28.

        This one states the consequence — Company History that is only on
        this machine — and names the file. That is true of *any* unbacked
        history, not only BUG-55. The cause (`Daily/` should be `daily/`)
        moved to its own line, because that line can say exactly what to
        rename and this one cannot. See `CaseFoldedScopeDirectoryTests`.
        """
        import contextlib
        import importlib.util

        self._init_remote()
        self._write_day("Daily/2026-08-13.md")
        self._backup()

        _output, alerts = self._alerts()
        self.assertIn("2026-08-13.md", alerts[0])

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_unbacked_pair", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        with contextlib.redirect_stdout(io.StringIO()):
            everything = module._print_history(datetime.now().astimezone())

        cause = [a for a in everything if "백업 범위 밖" in a]
        self.assertEqual(len(cause), 1, everything)
        self.assertIn("BUG-55", cause[0])
        self.assertIn("`daily/`", cause[0])

    def test_the_last_successful_backup_is_printed(self):
        """The number itself, which nothing showed."""
        self._init_remote()
        self._write_day("daily/2026-08-13.md")

        output, _alerts = self._alerts()
        self.assertIn("마지막 성공 백업", output)
        self.assertIn("아직 없음", output)

        self._backup()

        output, _alerts = self._alerts()
        self.assertNotIn("아직 없음", output)

    # ---- the false-alarm guard -----------------------------------------

    def test_a_successful_backup_clears_it(self):
        from backup.result import BackupStatus

        self._init_remote()
        self._write_day("daily/2026-08-13.md")
        self.assertEqual(len(self._alerts()[1]), 1)

        entry = self._backup()

        self.assertIs(entry.final_status, BackupStatus.SUCCESS)
        self.assertEqual(self._alerts()[1], [])

    def test_a_quiet_week_says_nothing(self):
        """The case a clock threshold would have got wrong. History that has
        not changed does not need backing up, and `BACKUP_NOT_REQUIRED` is
        the correct, healthy answer."""
        from backup.result import BackupStatus

        self._init_remote()
        self._write_day("daily/2026-08-13.md")
        self._backup("RUN-1")

        for run in range(3):
            entry = self._backup(f"QUIET-{run}")
            self.assertIs(entry.final_status, BackupStatus.NOT_REQUIRED)
            self.assertEqual(self._alerts()[1], [], f"quiet run {run}")

    def test_new_history_awaiting_its_backup_is_reported_then_clears(self):
        """Transient by design: between generation and the backup in the same
        run there is a real window where history is only on this machine."""
        self._init_remote()
        self._write_day("daily/2026-08-13.md")
        self._backup("RUN-1")

        time.sleep(1.1)  # mtime resolution
        self._write_day("daily/2026-08-14.md")
        self.assertEqual(len(self._alerts()[1]), 1)

        self._backup("RUN-2")

        self.assertEqual(self._alerts()[1], [])

    def test_an_empty_local_master_says_nothing(self):
        self._init_remote()

        self.assertEqual(self._alerts()[1], [])

    def test_a_staging_file_is_not_treated_as_unbacked_history(self):
        """An unfinished write is not Company History (C27), so it must not
        raise a backup alarm either."""
        self._init_remote()
        self._write_day("daily/2026-08-13.md")
        self._backup("RUN-1")

        time.sleep(1.1)
        (self.master / "daily" / ".tmp-killed.md").write_text("part", encoding="utf-8")

        self.assertEqual(self._alerts()[1], [])

    def test_the_check_never_consults_backup_status(self):
        """F-7/BUG-41 narrowed by measurement.

        BUG-41 is that `BACKUP_FAILED` is silently overwritten by a later
        run. Measured both ways against a real remote:

            remote comes back   run 2 pushes for real -> the overwrite is
                                CORRECT, and this check is correctly silent
            remote stays down   status stays PENDING, the file is not on the
                                remote -> this check fires and names it

        The point is that neither outcome depends on the status field: this
        check compares Company History against `last_successful_backup`, so
        whatever `backup_status` was overwritten with, unbacked history stays
        visible. That does not fix BUG-41 — the status is still overwritten —
        but it removes the consequence that made it dangerous.
        """
        from backup.result import BackupStatus

        self._init_remote()
        self._write_day("daily/2026-08-13.md")

        # A status claiming success, with nothing ever pushed.
        self.state_path.write_text(
            json.dumps(
                {
                    "last_successful_backup": None,
                    "last_backup_commit": None,
                    "backup_status": BackupStatus.SUCCESS.value,
                }
            ),
            encoding="utf-8",
        )

        _output, alerts = self._alerts()

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("2026-08-13.md", alerts[0])

    def test_a_damaged_backup_state_is_reported_not_raised(self):
        """This view answers even when part of the evidence is damaged."""
        self.state_path.write_text("{not json", encoding="utf-8")

        output, _alerts = self._alerts()

        self.assertIn("읽을 수 없음", output)


class SecretAlreadyInHistoryTests(unittest.TestCase):
    """The Working Copy report cleared for the wrong reason.

    C24 put "a secret-shaped file is in the Working Copy" in ATTENTION and
    C26 made it git-aware, so it now answers **what the next commit will
    carry**. The remote's history is a different question and nobody asked
    it. Measured end to end against a real bare remote:

        1. `.env` holding a Notion token reaches the Working Copy and is
           pushed (E-21)                     -> ATTENTION fires
        2. the operator deletes the file — the move the message leads to
                                             -> **ATTENTION clears**
        3. `git show HEAD:.env` on the remote still returns the token

    The alert went away because the local file was gone, not because the
    exposure was. That is the single worst thing "the warning disappeared"
    can mean, and step 2 is the most likely thing an operator does.

    **This cannot fire on a healthy machine**, which is why it is allowed to
    stand in ATTENTION rather than being softened into a block line. A
    Working Copy carrying docs/08 §28's `.gitignore` never commits such a
    path, so history never holds one — measured across seven configurations
    below. It is not the standing-alert-on-a-correct-machine shape C26
    removed; it appears only after a real leak.

    The two probes are deliberately independent and say different things:

        `_would_reach_the_commit()`   stop it from going out
        `_secrets_ever_committed()`   it is already out

    Fail-safe runs the *opposite* way from the older probe, on purpose.
    That one filters a set it was handed, so failing open keeps a real
    exposure visible; this one adds a claim about history, and asserting a
    leak because git could not answer would be inventing one.
    """

    TOKEN = "ntn_" + "G" * 40
    SECTION_28 = ".env\n.env.*\n*.tmp\n*.log\n"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "state").mkdir(parents=True)
        self.wc = self.runtime / "backup_working_copy"
        (self.wc / "daily").mkdir(parents=True)
        (self.wc / "daily" / "2026-08-13.md").write_text("# day\n", encoding="utf-8")

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd or self.wc, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.strip()

    def _init(self, *, gitignore=None):
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "History Probe Test")
        if gitignore is not None:
            (self.wc / ".gitignore").write_text(gitignore, encoding="utf-8")

    def _plant(self, name=".env"):
        target = self.wc / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"NOTION_API_TOKEN={self.TOKEN}\n", encoding="utf-8")
        return target

    def _commit(self, message="c"):
        self._git("add", "-A")
        self._git("commit", "-m", message)

    def _attention(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_history", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        with contextlib.redirect_stdout(io.StringIO()):
            items = module._print_history(NOW)
        return (
            [a for a in items if "history에 이미 들어간" in a],
            [a for a in items if "Secret 형태의 파일" in a],
        )

    def _case_variants(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_history_case", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        with contextlib.redirect_stdout(io.StringIO()):
            items = module._print_history(NOW)
        return [a for a in items if "알아보지 못하는" in a]

    def test_the_case_variant_report_asks_git_before_naming_a_file(self):
        """E-24's Working Copy half, held to C26's rule.

        The present-file report goes through `_would_reach_the_commit()`
        exactly as the E-21 line does. Without it, a Working Copy carrying
        docs/08 §28's `.gitignore` — the *correct* setup — would get a
        standing alert for a file git is refusing to commit, which is the
        alert-that-cannot-clear C26 removed once already.
        """
        self._init(gitignore="*.PEM\n")
        self._plant("notes/ID_RSA")   # git will commit this
        self._plant("IGNORED.PEM")    # git will not

        alerts = self._case_variants()

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("ID_RSA", alerts[0])
        self.assertNotIn("IGNORED.PEM", alerts[0])

    def test_git_s_own_storage_is_not_named_as_a_working_copy_secret(self):
        self._init()
        self._plant("notes/ID_RSA")
        self._commit()

        alerts = self._case_variants()

        self.assertEqual(len(alerts), 1, alerts)
        self.assertNotIn(".git", alerts[0])

    # ---- the defect ----------------------------------------------------

    def test_deleting_the_file_does_not_clear_the_history_exposure(self):
        """The whole finding, in one test."""
        self._init()
        planted = self._plant()
        self._commit()
        history, reaching = self._attention()
        self.assertEqual((len(history), len(reaching)), (1, 1))

        planted.unlink()

        history, reaching = self._attention()
        self.assertEqual(len(reaching), 0, "the older probe correctly goes quiet")
        self.assertEqual(len(history), 1, "the exposure is still real and still reported")

    def test_the_secret_really_is_still_readable_from_the_commit(self):
        """The premise, checked rather than asserted: this is reported
        because the bytes are still there, not because a name once was."""
        self._init()
        planted = self._plant()
        self._commit()
        planted.unlink()
        self._commit("remove it")

        blob = self._git("show", "HEAD~1:.env")

        self.assertIn(self.TOKEN, blob)

    def test_the_message_names_rotation_as_the_action(self):
        """Deleting is what an operator will try; rotating is what actually
        helps. The message has to say which."""
        self._init()
        self._plant()
        self._commit()

        history, _ = self._attention()

        self.assertIn("교체", history[0])
        self.assertIn(".env", history[0])

    def test_the_file_present_message_warns_that_deleting_is_not_enough(self):
        """The two lines have to agree, or the operator learns the wrong
        lesson from the one that appears first."""
        self._init()
        self._plant()

        _history, reaching = self._attention()

        self.assertIn("지우는 것만으로는", reaching[0])

    # ---- the false-alarm guard -----------------------------------------

    def test_a_healthy_repository_says_nothing(self):
        self._init(gitignore=self.SECTION_28)
        self._commit()

        history, reaching = self._attention()

        self.assertEqual((history, reaching), ([], []))

    def test_a_gitignored_secret_never_enters_history(self):
        """The correct configuration, with the secret sitting right there."""
        self._init(gitignore=self.SECTION_28)
        self._plant()
        self._commit()
        self._commit("again")

        history, reaching = self._attention()

        self.assertEqual(history, [])
        self.assertEqual(reaching, [])
        self.assertNotIn(".env", self._git("ls-tree", "-r", "--name-only", "HEAD").split())

    def test_a_secret_not_yet_committed_is_not_reported_as_history(self):
        """Two different facts: about to leak, versus already leaked."""
        self._init()
        self._plant()

        history, reaching = self._attention()

        self.assertEqual(history, [])
        self.assertEqual(len(reaching), 1)

    def test_a_non_repository_is_silent_about_history(self):
        """Fail-safe runs the other way here: git cannot answer, so no claim
        about history is made. The present-file gate is unaffected."""
        self._plant()

        history, reaching = self._attention()

        self.assertEqual(history, [])
        self.assertEqual(len(reaching), 1, "the older probe still over-reports")

    def test_a_secret_in_a_subdirectory_is_found(self):
        """History paths are compared by basename, exactly as
        `scan_for_secrets()` does."""
        self._init(gitignore=self.SECTION_28)
        self._plant("notes/id_rsa")
        self._commit()

        history, _reaching = self._attention()

        self.assertEqual(len(history), 1)
        self.assertIn("notes/id_rsa", history[0])

    def test_it_uses_the_gates_own_name_list(self):
        """A second opinion about what a secret looks like would let this
        report and the Backup gate disagree. The report imports the gate's
        own predicate rather than restating its list."""
        from backup.working_copy import _looks_like_secret

        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from backup.working_copy import _looks_like_secret", source)

        # And the predicate really is the gate's: a name the gate flags, and
        # one it does not.
        self.assertTrue(_looks_like_secret("id_rsa"))
        self.assertFalse(_looks_like_secret("2026-08-13.md"))

    def test_the_probe_returns_paths_not_just_names(self):
        """`notes/id_rsa` and `id_rsa` are different facts to an operator
        deciding which credential to rotate."""
        import importlib.util

        self._init()
        self._plant("notes/id_rsa")
        self._commit()

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(
            module._secrets_ever_committed(self.wc), (("notes/id_rsa",), True)
        )

    def test_a_case_variant_already_in_history_is_reported(self):
        """E-24. The gate's comparison is case-sensitive and Windows is not,
        so `daily/ID_RSA` is precisely the path that reaches the remote —
        measured, BACKUP_SUCCESS with the key readable via `git show`.
        Matching only the exact spelling left this report blind at the one
        place the leak actually happens.

        Widening the report is not widening the gate: `scan_for_secrets()`
        is untouched and nothing here can fail a backup, which is the
        property that keeps E-24's real fix behind a decision.
        """
        self._init(gitignore=self.SECTION_28)
        self._plant("daily/ID_RSA")
        self._commit()

        history, _reaching = self._attention()

        self.assertEqual(len(history), 1, history)
        self.assertIn("daily/ID_RSA", history[0])

    def test_an_ordinary_history_file_is_still_not_reported(self):
        """The guard on the widening. Case-folding must not start matching
        names that are not on the gate's list in any case."""
        import importlib.util

        self._init()
        self._plant("daily/2026-08-13.md")
        self._plant("daily/README.MD")
        self._commit()

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_probe_case", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module._secrets_ever_committed(self.wc), ((), True))


class CaseFoldedScopeDirectoryTests(unittest.TestCase):
    """BUG-55, from "something is wrong" to "rename this directory".

    C27 made the consequence visible: Company History that never reached the
    remote. It could not say *why*, so an operator had to notice a capital
    letter inside a filename (`Daily\\2026-08-13.md`) and know what it meant.

    `working_copy._is_in_scope()` compares the first path component against
    `_ALLOWED_TOP_LEVEL_DIRS` exactly. docs/11's deployment steps have a human
    create those directories, and Windows treats `Daily` and `daily` as one —
    so every other part of the system reads the directory happily, including
    this view's own `daily 파일` count (which uses `glob()`, and folds case),
    while Backup silently never copies it.

    The allowed set is imported from the module that enforces it. Restating
    `{"daily", "monthly"}` here would be a second opinion about backup scope,
    and a third scope directory would then be diagnosed nowhere.

    Detection only. Case-folding the comparison is BUG-55's own decision — it
    changes which files Backup covers — and renaming a directory under Local
    Master is an action this program must not take (docs/08 §13/§46: Company
    History is never rewritten by the program).
    """

    def _master(self, *names):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        master = root / "local_master"
        master.mkdir()
        for name in names:
            (master / name).mkdir()
        return master

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_casefold", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_case_folded_daily_is_diagnosed_with_its_correct_name(self):
        master = self._master("Daily", "monthly")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master),
            ((("Daily", "daily"),), True),
        )

    def test_it_covers_every_scope_directory_not_just_daily(self):
        master = self._master("daily", "MONTHLY")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master),
            ((("MONTHLY", "monthly"),), True),
        )

    def test_both_wrong_at_once_are_both_named(self):
        master = self._master("Daily", "Monthly")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master),
            ((("Daily", "daily"), ("Monthly", "monthly")), True),
        )

    # ---- the false-alarm guard -----------------------------------------

    def test_correctly_named_directories_say_nothing(self):
        master = self._master("daily", "monthly")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master), ((), True)
        )

    def test_a_legitimately_out_of_scope_directory_is_not_flagged(self):
        """docs/08 §26 marks `decisions/` conditional, not required. Being
        out of scope is not the defect — *looking* in scope is."""
        master = self._master("daily", "monthly", "decisions")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master), ((), True)
        )

    def test_a_merely_similar_name_is_not_flagged(self):
        master = self._master("daily", "monthly", "dailies")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master), ((), True)
        )

    def test_a_file_with_a_scope_name_is_not_a_directory_problem(self):
        """Only directories can hold Company History, so only directories are
        diagnosed. Note the fixture cannot also create `monthly/`: on a
        case-insensitive filesystem — the one this defect exists on — a file
        named `Monthly` and a directory named `monthly` are one path."""
        master = self._master("daily")
        (master / "Monthly").write_text("not a directory", encoding="utf-8")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master), ((), True)
        )

    def test_an_empty_or_missing_master_says_nothing(self):
        module = self._module()

        self.assertEqual(
            module._misnamed_scope_directories(self._master()), ((), True)
        )
        self.assertEqual(
            module._misnamed_scope_directories(Path(tempfile.mkdtemp()) / "nope"),
            ((), True),
        )

    def test_a_local_master_it_cannot_list_is_not_a_clean_one(self):
        """C70. The detector used to answer `()` for both "listed it, nothing
        misnamed" and "could not list it" — and the second is the state where
        a `Monthly/` sitting outside the backup scope goes unannounced while
        Backup keeps reporting SUCCESS (BUG-55).
        """
        master = self._master("daily", "Monthly")
        module = self._module()
        real = Path.iterdir

        def refuse(self):
            if self == master:
                raise PermissionError(13, "Access is denied")
            return real(self)

        self.assertEqual(
            module._misnamed_scope_directories(master),
            ((("Monthly", "monthly"),), True),
            "the premise: listable, and it really does find one",
        )

        Path.iterdir = refuse
        try:
            found, checked = module._misnamed_scope_directories(master)
        finally:
            Path.iterdir = real

        self.assertEqual(found, ())
        self.assertFalse(checked)

    def test_the_screen_says_it_rather_than_shortening_the_list(self):
        """Without the caller printing it, `checked=False` is invisible: the
        list is the same length a healthy tree produces."""
        import contextlib

        runtime = Path(tempfile.mkdtemp()) / "runtime"
        (runtime / "events" / "processed").mkdir(parents=True)
        master = runtime / "local_master"
        (master / "daily").mkdir(parents=True)
        module = self._module()
        real = Path.iterdir

        def render(broken):
            def refuse(self):
                if self == master:
                    raise PermissionError(13, "Access is denied")
                return real(self)

            previous = module.RUNTIME_DIR
            module.RUNTIME_DIR = runtime
            if broken:
                Path.iterdir = refuse
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    try:
                        module.main()
                    except SystemExit:
                        pass
            finally:
                Path.iterdir = real
                module.RUNTIME_DIR = previous
            return buffer.getvalue()

        marker = "백업 범위 밖 디렉터리를 확인 못 함"
        self.assertNotIn(marker, render(False))
        self.assertIn(marker, render(True))

    # ---- it really is the backup gate's own set ------------------------

    def test_the_allowed_set_comes_from_the_module_that_enforces_it(self):
        from backup.working_copy import _ALLOWED_TOP_LEVEL_DIRS, _is_in_scope

        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "from backup.working_copy import _ALLOWED_TOP_LEVEL_DIRS", source
        )
        # And the premise: the gate really does reject the case variant.
        for allowed in _ALLOWED_TOP_LEVEL_DIRS:
            with self.subTest(directory=allowed):
                self.assertTrue(_is_in_scope(f"{allowed}/2026-08-13.md"))
                self.assertFalse(_is_in_scope(f"{allowed.capitalize()}/2026-08-13.md"))

    def test_the_operator_message_names_both_the_wrong_and_right_name(self):
        import contextlib

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "state").mkdir(parents=True)
        (runtime / "local_master" / "Daily").mkdir(parents=True)
        (runtime / "local_master" / "Daily" / "2026-08-13.md").write_text(
            "# day\n", encoding="utf-8"
        )

        module = self._module()
        module.RUNTIME_DIR = runtime
        with contextlib.redirect_stdout(io.StringIO()):
            attention = module._print_history(NOW)

        message = next(a for a in attention if "백업 범위 밖" in a)
        self.assertIn("`Daily/`", message)
        self.assertIn("`daily/`", message)
        self.assertIn("BUG-55", message)


class CandidatesBeforeTheHistoryStartTests(unittest.TestCase):
    """BUG-46's permanent half, unblocked by noticing the decision was made.

    C22 narrowed BUG-46 by measurement: a KEEP Candidate dated in the
    *future* is only delayed — the Scheduler renders it once that date is
    yesterday — while one dated before `history_start_date` is **permanent**,
    because the Scheduler never goes earlier than that date.
    `find_orphaned_events()` reports clean for these (correctly: the
    Candidate exists), so nothing said the Event would never appear.

    C22 recorded the detection as blocked: *"설정이 없을 때 무엇을 보고할지가
    또 하나의 판단"*. **That judgement had already been made in this very
    file, twice** — `_agent_start_date()` and the sync-folder read both
    resolve an environment variable, and both answer "not set" by printing a
    note and computing nothing. `_history_start_date()` is byte-for-byte that
    shape. Applying an answer the module already gives is not a new policy;
    what was missing was noticing it existed.

    The same unblocking applies to the unresolvable `dirty_months` case
    recorded alongside it — one decision was holding two detections.

    Reachable through ordinary misconfiguration rather than corruption: a
    Desktop whose `COMPANY_OPS_AGENT_START_DATE` is earlier than Desktop 4's
    `COMPANY_OPS_HISTORY_START_DATE` delivers Events for dates Desktop 4 will
    never render, and every step of every run reports success.

    Detection only. What to *do* with a stranded Candidate is BUG-46/E-20's
    open decision; the message names the likely cause and stops there.
    """

    START = "2026-08-01"

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in ("history_candidates/keep", "history_candidates/review",
                    "local_master/daily", "local_master/monthly", "state",
                    "events/processed"):
            (runtime / rel).mkdir(parents=True)
        return runtime

    def _candidate(self, runtime, name, day):
        (runtime / "history_candidates" / "keep" / f"{name}.json").write_text(
            json.dumps(
                {
                    "history_id": name, "event_id": name.replace("HIST-", ""),
                    "timestamp": f"{day}T10:00:00+09:00", "category": "MILESTONE",
                    "project_id": "PRJ", "role": "COO", "summary": "s",
                    "evidence": [], "filter_result": "KEEP",
                }
            ),
            encoding="utf-8",
        )

    def _run(self, runtime, start):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_prehistory", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(
            os.environ,
            {} if start is None else {"COMPANY_OPS_HISTORY_START_DATE": start},
            clear=False,
        ):
            if start is None:
                os.environ.pop("COMPANY_OPS_HISTORY_START_DATE", None)
            spec.loader.exec_module(module)
            module.RUNTIME_DIR = runtime
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                attention = module._print_history(NOW)
        return buffer.getvalue(), [a for a in attention if "시작일" in a]

    # ---- the defect ----------------------------------------------------

    def test_a_candidate_before_the_start_date_is_reported(self):
        runtime = self._runtime()
        self._candidate(runtime, "HIST-OLD", "2026-07-20")

        _output, alerts = self._run(runtime, self.START)

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("HIST-OLD", alerts[0])
        self.assertIn("2026-07-20", alerts[0])

    def test_the_message_says_no_run_will_ever_render_it(self):
        runtime = self._runtime()
        self._candidate(runtime, "HIST-OLD", "2026-07-20")

        _output, alerts = self._run(runtime, self.START)

        self.assertIn("어떤 실행에서도", alerts[0])
        self.assertIn("BUG-46", alerts[0])

    def test_it_names_the_likely_misconfiguration(self):
        """The cause an operator can actually act on: two start dates that
        disagree across Desktops."""
        runtime = self._runtime()
        self._candidate(runtime, "HIST-OLD", "2026-07-20")

        _output, alerts = self._run(runtime, self.START)

        self.assertIn("COMPANY_OPS_AGENT_START_DATE", alerts[0])

    # ---- the false-alarm guard -----------------------------------------

    def test_a_candidate_after_the_start_date_is_not_reported(self):
        runtime = self._runtime()
        self._candidate(runtime, "HIST-OK", "2026-08-05")

        _output, alerts = self._run(runtime, self.START)

        self.assertEqual(alerts, [])

    def test_a_future_dated_candidate_is_not_reported(self):
        """C22's measurement: a future date is delayed, not lost — the
        Scheduler renders it once that day is yesterday. Reporting it would
        be an alert that clears itself, which is the noise this project
        removes."""
        runtime = self._runtime()
        self._candidate(runtime, "HIST-FUTURE", "2026-09-15")

        _output, alerts = self._run(runtime, self.START)

        self.assertEqual(alerts, [])

    def test_an_unset_variable_computes_nothing_and_says_so(self):
        """The behaviour this file already chose for its two Agent
        variables: report that the computation was skipped, do not guess and
        do not alert."""
        runtime = self._runtime()
        self._candidate(runtime, "HIST-OLD", "2026-07-20")

        output, alerts = self._run(runtime, None)

        self.assertIn("COMPANY_OPS_HISTORY_START_DATE 미설정", output)
        self.assertEqual(alerts, [])

    def test_an_unparseable_variable_is_treated_as_unset(self):
        runtime = self._runtime()
        self._candidate(runtime, "HIST-OLD", "2026-07-20")

        output, alerts = self._run(runtime, "not-a-date")

        self.assertIn("미설정", output)
        self.assertEqual(alerts, [])

    def test_an_unreadable_candidate_is_skipped_not_guessed(self):
        """`FileHistoryRepository.list()` would raise here (BUG-38) and take
        the view down. A file whose date cannot be read is not evidence of a
        stranded Event."""
        runtime = self._runtime()
        self._candidate(runtime, "HIST-OLD", "2026-07-20")
        (runtime / "history_candidates" / "keep" / "broken.json").write_text(
            "{not json", encoding="utf-8"
        )

        output, alerts = self._run(runtime, self.START)

        self.assertIn("HISTORY", output)
        self.assertEqual(len(alerts), 1)
        self.assertNotIn("broken", alerts[0])

    def test_a_staging_file_is_not_a_stranded_candidate(self):
        runtime = self._runtime()
        (runtime / "history_candidates" / "keep" / ".tmp-partial.json").write_text(
            json.dumps({"timestamp": "2026-07-20T10:00:00+09:00"}), encoding="utf-8"
        )

        _output, alerts = self._run(runtime, self.START)

        self.assertEqual(alerts, [])

    def test_the_resolver_matches_the_one_beside_it(self):
        """`_history_start_date()` deliberately mirrors `_agent_start_date()`
        — same read, same None on unset, same None on unparseable. That
        sameness is the argument that this needed no new decision."""
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_resolvers", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(module._history_start_date())
            self.assertIsNone(module._agent_start_date())
        with mock.patch.dict(
            os.environ,
            {
                "COMPANY_OPS_HISTORY_START_DATE": "bad",
                "COMPANY_OPS_AGENT_START_DATE": "bad",
            },
            clear=True,
        ):
            self.assertIsNone(module._history_start_date())
            self.assertIsNone(module._agent_start_date())
        with mock.patch.dict(
            os.environ,
            {
                "COMPANY_OPS_HISTORY_START_DATE": "2026-08-01",
                "COMPANY_OPS_AGENT_START_DATE": "2026-08-01",
            },
            clear=True,
        ):
            self.assertEqual(module._history_start_date(), date(2026, 8, 1))
            self.assertEqual(module._agent_start_date(), date(2026, 8, 1))


class UnresolvableDirtyMonthTests(CandidatesBeforeTheHistoryStartTests):
    """The second detection the same decision was holding.

    `monthly/generator.py`'s dirty loop refuses a month that predates
    `history_start_date` (docs/09 §85-86: never invent a month the system
    does not cover), returns MONTHLY_PENDING, and **deliberately leaves the
    flag in place** — its comment says silently forgetting it "would hide a
    state file that needs a person". The Runner then classifies PENDING as
    not-a-failure, which is right for the ordinary case (Daily Catch-up will
    fill a gap), writes one line to `late_update.log`, and moves on. Nothing
    reads that log.

    So the flag stayed, the person was never told, and ATTENTION said the
    opposite: *"다음 Runner 실행에서 자동 처리된다"* — a false statement for
    exactly the month no run can process.

    Unblocked by `_history_start_date()`, the same resolver that unblocked
    BUG-46. One decision was holding two detections, and the decision had
    already been made elsewhere in this file.
    """

    def _state(self, runtime, dirty):
        (runtime / "state" / "monthly_history_state.json").write_text(
            json.dumps({"last_successful_monthly_close": None, "dirty_months": dirty}),
            encoding="utf-8",
        )

    def _dirty_alerts(self, runtime, start):
        _output, _ = self._run(runtime, start)
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_dirty", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(
            os.environ,
            {} if start is None else {"COMPANY_OPS_HISTORY_START_DATE": start},
            clear=False,
        ):
            if start is None:
                os.environ.pop("COMPANY_OPS_HISTORY_START_DATE", None)
            spec.loader.exec_module(module)
            module.RUNTIME_DIR = runtime
            with contextlib.redirect_stdout(io.StringIO()):
                items = module._print_history(NOW)
        return (
            [a for a in items if "자동 처리된다" in a],
            [a for a in items if "어떤 실행도 처리할 수 없는" in a],
        )

    def test_an_ordinary_dirty_month_still_says_it_is_automatic(self):
        runtime = self._runtime()
        self._state(runtime, ["2026-08"])

        automatic, unresolvable = self._dirty_alerts(runtime, self.START)

        self.assertEqual(len(automatic), 1)
        self.assertEqual(unresolvable, [])

    def test_a_pre_history_dirty_month_is_not_called_automatic(self):
        """The false statement, removed."""
        runtime = self._runtime()
        self._state(runtime, ["2026-05"])

        automatic, unresolvable = self._dirty_alerts(runtime, self.START)

        self.assertEqual(automatic, [])
        self.assertEqual(len(unresolvable), 1)
        self.assertIn("2026-05", unresolvable[0])

    def test_a_mixed_state_separates_the_two(self):
        runtime = self._runtime()
        self._state(runtime, ["2026-05", "2026-08"])

        automatic, unresolvable = self._dirty_alerts(runtime, self.START)

        self.assertIn("2026-08", automatic[0])
        self.assertNotIn("2026-05", automatic[0])
        self.assertIn("2026-05", unresolvable[0])

    def test_without_the_start_date_no_month_is_called_unresolvable(self):
        """It cannot be judged, so no claim is made — today's behaviour."""
        runtime = self._runtime()
        self._state(runtime, ["2026-05"])

        automatic, unresolvable = self._dirty_alerts(runtime, None)

        self.assertEqual(len(automatic), 1)
        self.assertEqual(unresolvable, [])

    def test_a_malformed_month_key_never_reaches_this_check(self):
        """Measured rather than assumed: `monthly.load_state()` validates the
        `dirty_months` shape and raises, so the whole state is reported as
        damaged before any month is classified. The `continue` guard in the
        classifier is therefore belt-and-braces, not the thing that handles
        this — worth knowing, because a guard nobody can reach is a guard
        nobody maintains."""
        from monthly import MonthlyStateError
        from monthly import load_state as load_monthly_state

        runtime = self._runtime()
        self._state(runtime, ["not-a-month"])

        with self.assertRaises(MonthlyStateError):
            load_monthly_state(runtime / "state" / "monthly_history_state.json")

        automatic, unresolvable = self._dirty_alerts(runtime, self.START)
        self.assertEqual((automatic, unresolvable), ([], []))

    def test_the_generator_really_does_refuse_such_a_month(self):
        """The premise, from the generator rather than assumed."""
        import inspect

        import monthly.generator as generator

        source = inspect.getsource(generator.run_once)
        self.assertIn("predates the history start date", source)


class _KeptButNotRenderedFixture:
    """Harness only, deliberately not a TestCase.

    Split out for the reason `test_runner_notion_integration.RunnerNotionTestCase`
    gives: a second suite needs this fixture, and inheriting a TestCase to get
    it re-runs every test in the first one.
    """

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in ("history_candidates/keep", "history_candidates/review",
                    "local_master/daily", "local_master/monthly", "state",
                    "events/processed", "locks"):
            (runtime / rel).mkdir(parents=True)
        return runtime

    def _candidate(self, runtime, event_id, day):
        (runtime / "history_candidates" / "keep" / f"HIST-{event_id}.json").write_text(
            json.dumps(
                {
                    "history_id": f"HIST-{event_id}", "event_id": event_id,
                    "timestamp": f"{day}T10:00:00+09:00", "category": "MILESTONE",
                    "project_id": "PRJ", "role": "COO", "summary": "s",
                    "evidence": [], "filter_result": "KEEP",
                }
            ),
            encoding="utf-8",
        )

    def _daily(self, runtime, day, *event_ids):
        """Real item blocks, `### ` heading and summary bullet included.

        The fixture used to be a bare list of `- Event ID:` lines under
        `## Milestones`, which is not a shape `daily/markdown.py` can
        produce — and once `_label_lines()` confined the match to `### `
        item blocks (the fix for a summary written as a bullet silencing
        this very detector), a fixture with no block asserted nothing about
        a real Daily file.
        """
        body = [f"# DOJOONPASS Company History — {day}", "", "## Milestones", ""]
        for event_id in event_ids:
            body.extend(["### PRJ", "", "- s", "- Owner: COO", f"- Event ID: {event_id}", ""])
        (runtime / "local_master" / "daily" / f"{day}.md").write_text(
            "\n".join(body) + "\n", encoding="utf-8"
        )

    def _run(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_e17", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [a for a in attention if "Daily History에 없다" in a]





class KeptButNotRenderedTests(_KeptButNotRenderedFixture, unittest.TestCase):
    """E-17's loss, made visible. NOT fixed — reported.

    E-17: when `update_daily_history()` fails, that Late Event is never
    retried. Step 6.5's target dates are only the ones *this* run collected
    (`kept_dates`), so no later run has a reason to look at that date again.
    Its own measurement ends with the sentence that matters:

        파일을 고쳐도 아무 일도 일어나지 않고, **모든 지표가 정상을 보고하는
        채로** Company History에 Event 하나가 비어 있다.

    C20 corrected the classification (RETRYABLE -> PERMANENT) so the *failing
    run* shows up. What stayed invisible is the state afterwards: a Candidate
    stored as Company History, absent from the day it belongs to, with every
    later run reporting SUCCESS.

    **The verdict is decidable between runs, which is why this needed no
    policy decision.** Step 5 writes Candidates, step 6 renders the dates the
    Scheduler closed, and step 6.5 merges anything landing on an
    already-closed date — all within one run. So once a run has finished, a
    Candidate whose Daily file *exists* and does not contain its `event_id`
    was not merged, and nothing will retry it.

    A Candidate whose Daily file does not exist yet is excluded: that is the
    Scheduler window (not yet rendered), or BUG-46's pre-history case, which
    `_candidates_before()` reports on its own terms.

    **Verified against this machine's real runtime before being written**:
    13 of 14 stored Candidates were present in their Daily file, and the
    fourteenth was genuinely absent — E-17's shape, sitting there unreported.
    """

    # ---- the defect ----------------------------------------------------

    def test_a_candidate_missing_from_its_rendered_day_is_reported(self):
        runtime = self._runtime()
        self._candidate(runtime, "EVT-STRANDED", "2026-08-05")
        self._daily(runtime, "2026-08-05", "EVT-OTHER")

        _output, alerts = self._run(runtime)

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("EVT-STRANDED", alerts[0])
        self.assertIn("2026-08-05", alerts[0])

    def test_the_message_says_no_run_will_add_it(self):
        runtime = self._runtime()
        self._candidate(runtime, "EVT-STRANDED", "2026-08-05")
        self._daily(runtime, "2026-08-05")

        _output, alerts = self._run(runtime)

        self.assertIn("어떤 실행도", alerts[0])
        self.assertIn("E-17", alerts[0])

    # ---- the false-alarm guard -----------------------------------------

    def test_a_rendered_candidate_is_not_reported(self):
        runtime = self._runtime()
        self._candidate(runtime, "EVT-OK", "2026-08-05")
        self._daily(runtime, "2026-08-05", "EVT-OK")

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_a_candidate_whose_day_is_not_rendered_yet_is_not_reported(self):
        """The Scheduler window: no Daily file means not yet, not lost."""
        runtime = self._runtime()
        self._candidate(runtime, "EVT-PENDING", "2026-08-09")

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_several_candidates_on_one_day_are_judged_individually(self):
        runtime = self._runtime()
        self._candidate(runtime, "EVT-IN", "2026-08-05")
        self._candidate(runtime, "EVT-OUT", "2026-08-05")
        self._daily(runtime, "2026-08-05", "EVT-IN")

        _output, alerts = self._run(runtime)

        self.assertEqual(len(alerts), 1)
        self.assertIn("EVT-OUT", alerts[0])
        self.assertNotIn("EVT-IN", alerts[0])

    def test_an_unreadable_candidate_is_skipped(self):
        runtime = self._runtime()
        (runtime / "history_candidates" / "keep" / "broken.json").write_text(
            "{not json", encoding="utf-8"
        )

        output, alerts = self._run(runtime)

        self.assertIn("HISTORY", output)
        self.assertEqual(alerts, [])

    def test_an_unreadable_candidate_is_reported_rather_than_only_skipped(self):
        """The blind spot C28's own checks created, closed in the same Sprint.

        Both new checks drop a Candidate they cannot parse — neither can
        claim a fact about bytes it could not read. That left the file
        reported by nothing, with "Candidate 정합성: OK" two lines below.

        It is not harmless: `scheduler.run_once()` builds its keep index from
        `repository.list()`, which raises on the first unreadable Candidate
        (BUG-38), so the *next* run's Scheduler step fails. This names the
        file before that happens.
        """
        runtime = self._runtime()
        (runtime / "history_candidates" / "keep" / "HIST-BROKEN.json").write_text(
            "{truncated", encoding="utf-8"
        )

        output, _alerts = self._run(runtime)

        self.assertIn("읽을 수 없는 Candidate", output)

    def test_a_readable_candidate_is_not_reported_as_unreadable(self):
        runtime = self._runtime()
        self._candidate(runtime, "EVT-OK", "2026-08-05")
        self._daily(runtime, "2026-08-05", "EVT-OK")

        output, _alerts = self._run(runtime)

        self.assertNotIn("읽을 수 없는 Candidate", output)

    def test_a_staging_file_is_not_reported_as_unreadable(self):
        """`.tmp-` is an unfinished write, not a damaged Candidate (C27)."""
        runtime = self._runtime()
        (runtime / "history_candidates" / "keep" / ".tmp-x.json").write_text(
            "{truncated", encoding="utf-8"
        )

        output, _alerts = self._run(runtime)

        self.assertNotIn("읽을 수 없는 Candidate", output)

    def test_a_staging_file_is_not_a_stranded_candidate(self):
        runtime = self._runtime()
        (runtime / "history_candidates" / "keep" / ".tmp-x.json").write_text(
            json.dumps({"event_id": "E", "timestamp": "2026-08-05T10:00:00+09:00"}),
            encoding="utf-8",
        )
        self._daily(runtime, "2026-08-05")

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_a_running_runner_adds_the_caveat_without_hiding_the_list(self):
        """Same treatment `find_orphaned_events()` documents: a Runner
        between step 5 and step 6.5 can produce this transiently. A real loss
        hidden behind "probably just running" is worse than a caveat."""
        runtime = self._runtime()
        self._candidate(runtime, "EVT-STRANDED", "2026-08-05")
        self._daily(runtime, "2026-08-05")
        (runtime / "locks" / "company_ops.lock").write_text(
            json.dumps(
                {"process_id": os.getpid(), "created_at": NOW.isoformat(timespec="seconds")}
            ),
            encoding="utf-8",
        )

        _output, alerts = self._run(runtime)

        self.assertEqual(len(alerts), 1)
        self.assertIn("Runner 실행 중", alerts[0])

    def test_a_prefix_of_another_id_is_not_mistaken_for_rendered(self):
        """A false negative in this very check, found in C30.

        The first version asked `event_id not in text`. `E-1` is a substring
        of the line rendered for `E-10`, so a genuinely stranded `E-1` was
        reported as fine — with ordinary sequential ids and no crafted input.

        Whole lines are compared now, which is the same question the renderer
        answers: `daily/markdown.py` writes exactly `- Event ID: {event_id}`.

        C31 changed *how* that comparison is built, not what it asks. C30
        took the file's lines apart (`startswith(prefix)`, then slice the
        prefix off); the line is constructed the way the renderer constructs
        it now, because the prefix that had to be sliced ends in a space and
        an empty `event_id` therefore fell off the end of it. See
        `test_an_empty_event_id_that_was_rendered_is_not_reported`.
        """
        runtime = self._runtime()
        self._candidate(runtime, "E-1", "2026-08-05")
        self._candidate(runtime, "E-10", "2026-08-05")
        self._daily(runtime, "2026-08-05", "E-10")

        _output, alerts = self._run(runtime)

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("E-1 (", alerts[0])

    def test_both_ids_rendered_reports_neither(self):
        runtime = self._runtime()
        self._candidate(runtime, "E-1", "2026-08-05")
        self._candidate(runtime, "E-10", "2026-08-05")
        self._daily(runtime, "2026-08-05", "E-1", "E-10")

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_an_id_mentioned_in_prose_does_not_count_as_rendered(self):
        """Only the renderer's own line counts. A summary that happens to
        quote an id is not that id being rendered."""
        runtime = self._runtime()
        self._candidate(runtime, "EVT-QUOTED", "2026-08-05")
        (runtime / "local_master" / "daily" / "2026-08-05.md").write_text(
            "# DOJOONPASS Company History — 2026-08-05\n\n"
            "## Summary\n\nfollow-up to EVT-QUOTED\n",
            encoding="utf-8",
        )

        _output, alerts = self._run(runtime)

        self.assertEqual(len(alerts), 1, alerts)

    def test_the_match_is_on_the_id_the_renderer_writes(self):
        """`daily/markdown.py` writes `- Event ID: {event_id}`. Matching on
        anything else would drift from the renderer."""
        import inspect

        import daily.markdown as markdown

        self.assertIn("Event ID: {candidate.event_id}", inspect.getsource(markdown))

    def test_an_empty_event_id_that_was_rendered_is_not_reported(self):
        """The false positive C30's own fix introduced.

        C30 took the rendered line apart — `startswith(prefix)` then slice
        the prefix off — and the prefix it had to slice ends in a space. An
        `event_id` of `""` (which `validate_event()` accepts, BACKLOG A-15)
        renders as `- Event ID: `, whose stripped form is `- Event ID:` and
        does not start with `- Event ID: `. So a Candidate that was in its
        Daily file was reported as permanently lost, with a message telling
        the operator that no run will ever fix it.

        The comparison is built the way the renderer builds it now — take the
        id, make the line — so there is no prefix to fall off the end of.
        """
        runtime = self._runtime()
        self._candidate(runtime, "", "2026-08-05")
        self._daily(runtime, "2026-08-05", "")

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_an_empty_event_id_that_was_not_rendered_is_still_reported(self):
        """The guard above must not be a blanket exemption — an id of `""`
        that really is absent is the same loss as any other."""
        runtime = self._runtime()
        self._candidate(runtime, "", "2026-08-05")
        self._daily(runtime, "2026-08-05", "EVT-OTHER")

        _output, alerts = self._run(runtime)

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("2026-08-05", alerts[0])

    def test_a_trailing_space_in_an_id_still_matches_its_own_line(self):
        """Same class, other end: the renderer writes the id verbatim, and
        Markdown's trailing whitespace does not survive a `strip()` on either
        side. Constructing the line strips both, so they still meet."""
        runtime = self._runtime()
        self._candidate(runtime, "EVT-PAD ", "2026-08-05")
        self._daily(runtime, "2026-08-05", "EVT-PAD ")

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])


class ReviewedButNotRenderedIsRenderedTests(_KeptButNotRenderedFixture, unittest.TestCase):
    """C33 §3's detector — the block that prints it never ran.

    The loss is the most expensive kind this pipeline handles: Decision
    Context a HUMAN typed, stored by `history.review`, renderable by
    `daily/markdown.py`, and unreachable for a Candidate whose day is already
    closed. `_reviewed_but_not_rendered()` is well covered
    (`test_history_review.py`), and the two lines in `_print_history()` that
    turn its result into a screen line and an ATTENTION entry were covered by
    a **source-string assertion**:

        assertIn("_reviewed_but_not_rendered(keep_candidates, daily_dir)", source)

    which is a claim about how the call is written, not that anything is
    printed. A detector nothing prints detects nothing, and that is what the
    string was standing in for (C41 §1's shape, found by a line-coverage pass
    in C42).
    """

    def _reviewed_candidate(self, runtime, event_id, day, **fields):
        payload = {
            "history_id": f"HIST-{event_id}", "event_id": event_id,
            "timestamp": f"{day}T10:00:00+09:00", "category": "MILESTONE",
            "project_id": "PRJ", "role": "COO", "summary": "s",
            "evidence": [], "filter_result": "KEEP",
        }
        payload.update(fields)
        (runtime / "history_candidates" / "keep" / f"HIST-{event_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_a_stranded_decision_context_reaches_the_screen_and_attention(self):
        runtime = self._runtime()
        self._reviewed_candidate(
            runtime, "EVT-R", "2026-08-05", decision_context="정식 출시로 간다"
        )
        # The day is closed and carries the Candidate's own id — so
        # `_kept_but_not_rendered()` is clean and only the review check can
        # see the loss. That separation is the whole point of having two.
        self._daily(runtime, "2026-08-05", "EVT-R")

        output, _ = self._run(runtime)
        attention = self._review_alerts(runtime)

        self.assertIn("검토 미반영         : 1", output)
        self.assertEqual(len(attention), 1, attention)
        self.assertIn("EVT-R", attention[0])
        self.assertIn("Decision Context", attention[0])

    def test_a_rendered_decision_context_is_not_reported(self):
        """The other direction. A Candidate whose review DID reach the file
        must not leave a standing line — the renderer writes
        `- Decision Context: <value>` inside the item block, and that is what
        the check looks for."""
        runtime = self._runtime()
        self._reviewed_candidate(
            runtime, "EVT-R", "2026-08-05", decision_context="정식 출시로 간다"
        )
        (runtime / "local_master" / "daily" / "2026-08-05.md").write_text(
            "# H\n\n## Milestones\n\n### PRJ\n\n- s\n- Owner: COO\n"
            "- Event ID: EVT-R\n- Decision Context: 정식 출시로 간다\n",
            encoding="utf-8",
        )

        output, _ = self._run(runtime)

        self.assertNotIn("검토 미반영", output)
        self.assertEqual(self._review_alerts(runtime), [])

    def test_a_candidate_with_no_review_is_not_reported(self):
        """A KEEP Candidate nobody reviewed has nothing to strand."""
        runtime = self._runtime()
        self._candidate(runtime, "EVT-P", "2026-08-05")
        self._daily(runtime, "2026-08-05", "EVT-P")

        output, _ = self._run(runtime)

        self.assertNotIn("검토 미반영", output)
        self.assertEqual(self._review_alerts(runtime), [])

    def _review_alerts(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_review", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        with contextlib.redirect_stdout(io.StringIO()):
            attention = module._print_history(NOW)
        return [item for item in attention if "Decision Context" in item]


class MonthlySequenceHoleIsRenderedTests(unittest.TestCase):
    """The Monthly hole ATTENTION block — covered by nothing that ran it.

    `_holes_in_the_monthly_sequence()` has its own suite. What it feeds — a
    screen line and an ATTENTION entry inside `_print_history()` — did not,
    and a hole in the Monthly sequence means a consolidated month whose file
    is gone: `monthly_history_state.json` has already advanced past it, so no
    ordinary run rebuilds it. The message is the only place the remedy
    (`mark_month_dirty()`) is stated.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name) / "runtime"
        for relative in ("history_candidates/keep", "history_candidates/review",
                         "local_master/daily", "local_master/monthly", "state",
                         "events/processed", "locks"):
            (self.runtime / relative).mkdir(parents=True)

    def _months(self, *keys):
        for key in keys:
            (self.runtime / "local_master" / "monthly" / f"{key}.md").write_text(
                f"# {key}\n", encoding="utf-8"
            )

    def _run(self):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_monthly_hole", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [a for a in attention if "Monthly History 시퀀스" in a]

    def test_a_missing_month_in_the_middle_reaches_attention(self):
        self._months("2026-03", "2026-05")

        output, attention = self._run()

        self.assertIn("Monthly 시퀀스 구멍 : 1", output)
        self.assertEqual(len(attention), 1, attention)
        self.assertIn("2026-04", attention[0])

    def test_a_complete_sequence_says_nothing(self):
        """The endpoints are not holes — a month not yet consolidated is
        simply not consolidated yet, and reporting it would be a standing
        line every operator would learn to ignore."""
        self._months("2026-03", "2026-04", "2026-05")

        output, attention = self._run()

        self.assertNotIn("Monthly 시퀀스 구멍", output)
        self.assertEqual(attention, [])

    def test_the_message_names_what_to_do(self):
        """An ATTENTION line with no remedy sends an operator to guess. The
        remedy is measured in `MonthlySequenceHoleTests`; this is the half
        that gets it in front of a person."""
        self._months("2026-03", "2026-05")

        _output, attention = self._run()

        self.assertIn("dirty", attention[0])
        self.assertIn("Daily", attention[0])


class ASummaryCanNoLongerSilenceTheLossDetectorTests(
    _KeptButNotRenderedFixture, unittest.TestCase
):
    """REGRESSION. The detector above could be switched off, per Candidate,
    by an ordinary summary — and it was the *second* time that happened.

    C30 closed the first door: `render_daily_markdown()` writes a summary raw
    as its block's first bullet, so a summary of `Event ID: EVT-B` renders a
    line identical to EVT-B's own label. `summary_line_indices()` excludes
    that bullet, and the detector went back to reporting EVT-B.

    `## Summary` is the same door one section up, and it was open. That
    section repeats every summary RAW — no `- ` of its own — so a summary
    that is itself a bullet lands there as a bare line spelling a label, and
    `summary_line_indices()` cannot reach it: that rule walks `### ` item
    blocks and the Summary section has none.

    Measured, EVT-A rendered by the real renderer with three summaries, EVT-B
    genuinely absent from the same day's file:

        'Shipped it.'         ('EVT-B (2026-08-05)',)
        'Event ID: EVT-B'     ('EVT-B (2026-08-05)',)
        '- Event ID: EVT-B'   ()                        <- silenced

    E-17's data loss, unreported again, by one Candidate naming another. Seen
    from the security side it is the same spoofing vector
    `daily/late_events.existing_event_ids()` carries — which lost a real late
    Event to the identical line, and is where the shared rule now lives
    (`daily.markdown.item_block_bounds()`).

    Inherits the harness above so both detectors are exercised on documents
    the REAL renderer produced, not on the hand-built fixture.
    """

    def _render_real_daily(self, runtime, day, summary, *, present):
        from daily.markdown import render_daily_markdown
        from history import HistoryCandidate, HistoryDecision

        candidates = [
            HistoryCandidate(
                history_id=f"HIST-{present}", event_id=present,
                timestamp=f"{day}T10:00:00+09:00", category="MILESTONE",
                project_id="PRJ", role="COO", summary=summary, evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
        ]
        (runtime / "local_master" / "daily" / f"{day}.md").write_text(
            render_daily_markdown(date(*(int(p) for p in day.split("-"))), candidates, "g"),
            encoding="utf-8",
        )

    def _alerts_for(self, summary):
        runtime = self._runtime()
        self._candidate(runtime, "EVT-A", "2026-08-05")
        self._candidate(runtime, "EVT-B", "2026-08-05")
        self._render_real_daily(runtime, "2026-08-05", summary, present="EVT-A")
        _output, alerts = self._run(runtime)
        return alerts

    def test_a_bullet_shaped_summary_does_not_hide_the_stranded_candidate(self):
        for summary in ("Shipped it.", "Event ID: EVT-B", "- Event ID: EVT-B"):
            with self.subTest(summary=summary):
                alerts = self._alerts_for(summary)

                self.assertEqual(len(alerts), 1, alerts)
                self.assertIn("EVT-B", alerts[0])

    def test_the_rendered_candidate_is_never_reported(self):
        """The other direction: narrowing the match must not start reporting
        a Candidate the renderer really did write."""
        for summary in ("Shipped it.", "Event ID: EVT-B", "- Event ID: EVT-B"):
            with self.subTest(summary=summary):
                self.assertNotIn("EVT-A", "".join(self._alerts_for(summary)))

    def test_the_summary_section_is_where_it_came_from(self):
        """Names the mechanism, so a renderer that stops repeating summaries
        raw shows up here rather than quietly making this class vacuous."""
        from daily.markdown import render_daily_markdown
        from history import HistoryCandidate, HistoryDecision

        text = render_daily_markdown(
            date(2026, 8, 5),
            [
                HistoryCandidate(
                    history_id="HIST-EVT-A", event_id="EVT-A",
                    timestamp="2026-08-05T10:00:00+09:00", category="MILESTONE",
                    project_id="PRJ", role="COO", summary="- Event ID: EVT-B",
                    evidence=(), filter_result=HistoryDecision.KEEP,
                )
            ],
            "g",
        )
        lines = text.splitlines()

        self.assertEqual(lines[lines.index("## Summary") + 2], "- Event ID: EVT-B")


class RuntimeDirIsTheOnlyKnobTests(unittest.TestCase):
    """Redirecting `RUNTIME_DIR` must fully isolate this view.

    It used not to. `AGENT_DIR = RUNTIME_DIR / "agent"` was a module-level
    constant, so it froze at import: a caller that redirected `RUNTIME_DIR`
    got a fixture for three blocks and the **developer's real machine** for
    the AGENT block, with nothing saying so.

    Measured during C31, and not hypothetically — a probe pointed
    `RUNTIME_DIR` at a temp tree holding a future-dated `agent_state.json`,
    read back "agent has not run for 3 day(s)" from this repository's own
    runtime, and nearly recorded a working check as missing.

    C13's 결함 2 in a second place, and its wording applies verbatim:
    *"a test calling it directly picked up the repository's own live
    manifest — which said SUCCESS — and got exit 0 for a Backup failure."*

    Two properties, because either alone can rot. The path has to be derived
    on call, and no other module-level name may re-freeze it.
    """

    def _module(self, runtime):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_one_knob", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        return module

    def test_redirecting_runtime_dir_alone_isolates_the_agent_view(self):
        import contextlib

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        runtime.mkdir()

        module = self._module(runtime)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_agent(NOW)

        # An empty runtime has no agent at all. Reading anything else means
        # it reached outside the fixture.
        self.assertIn("Agent가 설정되어 있지 않다", buffer.getvalue())
        self.assertEqual(attention, [])

    @staticmethod
    def _live_identifiers():
        """Every string that appears **only** in this repository's real
        `processed/` — both the filename and the `event_id` inside it.

        Both, because they are not the same string here: several files are
        named `fi-crash.json` while carrying `event_id` `FI-CRASH-1`, and the
        blocks print the id rather than the filename. The first draft of this
        detector collected filenames alone and
        `test_the_leak_detector_would_actually_notice` failed it immediately
        — which is what that test is for.
        """
        import json

        processed = (
            Path(__file__).resolve().parents[1] / "runtime" / "events" / "processed"
        )
        found = set()
        for path in processed.glob("*.json"):
            found.add(path.stem)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            # `project_id` as well as `event_id`: the CONTROL TOWER block
            # prints counts and Project names and no ids at all, so an
            # id-only detector looks for something that block never emits.
            for field in ("event_id", "project_id"):
                if isinstance(data.get(field), str):
                    found.add(data[field])
        # Short strings would collide with ordinary words in the output.
        return sorted(i for i in found if len(i) >= 8)

    @staticmethod
    def _mentions(text, identifier):
        """Whether `text` names `identifier` as a whole token.

        A plain `in` is not enough and this is measured, not theoretical: the
        first draft flagged `_print_history` for leaking `COMPANY_OPS` into an
        empty fixture. It had not — the output contains the **environment
        variable name** `COMPANY_OPS_HISTORY_START_DATE`, and every count in
        the block was 0. A substring match turned a correct block into a
        reported leak, which is the shape of false alarm that gets a real
        detector switched off.
        """
        import re

        return re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(identifier) + r"(?![A-Za-z0-9_])",
            text,
        ) is not None

    def test_every_block_stays_inside_the_fixture_not_just_the_agent_one(self):
        """C88. The class above proves the seam for **one** block.

        That is the half a split would slip through. `ops_status.py` is 4,940
        lines and the obvious cleanup is to move the six block renderers into
        modules of their own — measured, the coupling allows it: 33 of the 38
        helpers the blocks reach belong to exactly one block and only five are
        shared. What the coupling number does not show is that a moved block
        would read **its own** module's `RUNTIME_DIR`, so
        `ops_status.RUNTIME_DIR = tmp` — which 99 sites across nine test files
        and `dashboard_server.py` do — would stop reaching it. The moved block
        would quietly report the developer's live `runtime/` while everything
        around it reported the fixture.

        That is C31's incident again (this class's own docstring), and the
        existing tests would not catch it: they redirect and then assert on
        the AGENT block alone.

        So this runs **all six** against an empty tree and asserts that none
        of them names anything only the real repository runtime contains.
        Generic rather than block-specific on purpose — a seventh block added
        later is covered without anyone remembering to add it here.
        """
        import contextlib

        real_ids = self._live_identifiers()
        if not real_ids:
            self.skipTest("no live runtime evidence to be leaked into the fixture")

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "events" / "processed").mkdir(parents=True)
        (runtime / "local_master" / "daily").mkdir(parents=True)

        module = self._module(runtime)
        renderers = (
            "_print_company", "_print_history", "_print_control_tower",
            "_print_last_run", "_print_notion", "_print_agent",
        )

        for name in renderers:
            with self.subTest(block=name):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    module._block(name, getattr(module, name), NOW)
                printed = buffer.getvalue()

                leaked = [i for i in real_ids if self._mentions(printed, i)]
                self.assertEqual(
                    leaked, [],
                    f"{name} reported {leaked} — evidence that exists only in "
                    "the repository's real runtime/, so this block did not "
                    "follow RUNTIME_DIR into the fixture",
                )

    def test_the_leak_detector_would_actually_notice(self):
        """Guards the guard. The assertion above is a `not in` over strings,
        which passes trivially if the block prints nothing at all or if the
        real tree happens to be empty. This drives the same detector at a
        tree that **does** contain the ids and shows it fails."""
        real_processed = (
            Path(__file__).resolve().parents[1] / "runtime" / "events" / "processed"
        )
        real_ids = self._live_identifiers()
        if not real_ids:
            self.skipTest("no live runtime evidence to detect")

        import contextlib

        module = self._module(real_processed.parents[1])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            for name in ("_print_company", "_print_history", "_print_control_tower"):
                module._block(name, getattr(module, name), NOW)

        self.assertTrue(
            any(self._mentions(buffer.getvalue(), i) for i in real_ids),
            "pointed at the real tree the blocks printed none of its "
            "identifiers — the check above is looking for something that "
            "never appears, and would pass over a real leak",
        )

    def test_the_agent_lock_path_follows_it_too(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        runtime.mkdir()

        module = self._module(runtime)

        self.assertEqual(
            module._agent_lock_path(), runtime / "agent" / "locks" / "agent.lock"
        )

    def test_no_module_level_constant_re_freezes_a_runtime_path(self):
        """The structural half. A new `FOO = RUNTIME_DIR / ...` at import
        time would reintroduce exactly this, and would pass every behavioural
        test above until somebody redirected only `RUNTIME_DIR`."""
        import ast

        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        frozen = []
        for node in tree.body:  # module level only
            if not isinstance(node, ast.Assign):
                continue
            names = {
                sub.id for sub in ast.walk(node.value) if isinstance(sub, ast.Name)
            }
            # `PROJECT_ROOT` as well as `RUNTIME_DIR`, measured (C88). A
            # mutation that froze one block's path as
            # `_FROZEN = PROJECT_ROOT / "runtime"` — which is exactly the
            # shape a module split produces — walked straight past a check
            # that knew only the `RUNTIME_DIR` spelling. Both names lead to
            # the same directory and only one of them was guarded.
            if names & {"RUNTIME_DIR", "PROJECT_ROOT"}:
                frozen.extend(
                    t.id
                    for t in node.targets
                    if isinstance(t, ast.Name) and t.id != "RUNTIME_DIR"
                )

        self.assertEqual(
            frozen,
            [],
            "these freeze a runtime path at import time; derive them in a "
            f"function instead (see `_agent_dir()`): {frozen}",
        )


class FutureDatedStatePointerTests(unittest.TestCase):
    """NEW. A state pointer dated ahead of the calendar stops Company History
    permanently, and every existing indicator calls it healthy.

    C17 found and reported this shape for the **Agent's** state file, and
    `agent/status.py` still says it in these words: *"agent state says it has
    collected through X, which is in the future … nothing will be collected
    until that date arrives"*. The Runner's own two state files make the
    identical claim and nobody had asked them.

    `scheduler._generate_pending_dates()` computes `start = pointer + 1 day`
    and `end = yesterday`, so a future pointer makes `start > end` and the
    loop runs zero times. `monthly.pending_months()` does the same one
    granularity up. Neither walks backwards — which is correct, and which is
    exactly why nothing recovers on its own.

    `check_state_consistency()` cannot see it: it asks only whether the
    claimed Daily file **exists**, and in the reachable version of this it
    does — the Scheduler wrote it while the clock was skewed.

    Measured end to end, pointer `2026-12-25` with that file present, "now"
    2026-08-14, one KEEP Candidate waiting for 2026-08-12:

        scheduler.run_once()   COMPLETED, generated=()
        state consistency      CONSISTENT
        ATTENTION              (nothing)

    Four months of Company History would not be written, and the Candidates
    would pile up unrendered with every signal green.

    Reachable through clock skew later corrected (a dead CMOS battery, an NTP
    jump, a VM resumed with a stale clock) or a state file restored from a
    machine that had one — the two causes C17 records for the Agent side.

    Detection only. Repairing means deciding which date Company History
    resumes from: docs/10 §46's prohibition and §64's operator call.
    """

    NOW = datetime(2026, 8, 14, 11, 0).astimezone()

    def _runtime(self, *, daily_pointer=None, monthly_pointer=None):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in ("history_candidates/keep", "history_candidates/review",
                    "local_master/daily", "local_master/monthly", "state",
                    "events/processed", "events/incoming", "events/transport",
                    "events/rejected", "locks"):
            (runtime / rel).mkdir(parents=True)
        if daily_pointer is not None:
            (runtime / "local_master" / "daily" / f"{daily_pointer}.md").write_text(
                "# d\n", encoding="utf-8"
            )
            (runtime / "state" / "daily_history_state.json").write_text(
                json.dumps({"last_successful_daily_close": daily_pointer}),
                encoding="utf-8",
            )
        if monthly_pointer is not None:
            (runtime / "local_master" / "monthly" / f"{monthly_pointer}.md").write_text(
                "# m\n\n## Metadata\n\n- Consolidated Items: 0\n", encoding="utf-8"
            )
            (runtime / "state" / "monthly_history_state.json").write_text(
                json.dumps(
                    {"last_successful_monthly_close": monthly_pointer, "dirty_months": []}
                ),
                encoding="utf-8",
            )
        return runtime

    def _alerts(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_future", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(self.NOW)
        return buffer.getvalue(), [a for a in attention if "미래" in a]

    # ---- the defect, through the real Scheduler --------------------------

    def test_the_scheduler_really_does_stop_and_report_success(self):
        """Reachability, not a fixture. This is why the alert is needed."""
        from history import HistoryCandidate, HistoryDecision
        from history.file_repository import FileHistoryRepository
        from scheduler import run_once as scheduler_run_once
        from scheduler.consistency import check_state_consistency

        runtime = self._runtime(daily_pointer="2026-12-25")
        daily_dir = runtime / "local_master" / "daily"
        state_path = runtime / "state" / "daily_history_state.json"
        repository = FileHistoryRepository(
            keep_dir=runtime / "history_candidates" / "keep",
            review_dir=runtime / "history_candidates" / "review",
        )
        repository.save(
            HistoryCandidate(
                history_id="HIST-A", event_id="EVT-A",
                timestamp="2026-08-12T10:00:00+09:00", category="MILESTONE",
                project_id="P", role="COO", summary="real work",
                evidence=(), filter_result=HistoryDecision.KEEP,
            )
        )

        result = scheduler_run_once(
            repository,
            history_start_date=date(2026, 8, 1),
            now=self.NOW,
            state_path=state_path,
            daily_output_dir=daily_dir,
            already_locked=True,
        )
        consistency = check_state_consistency(state_path, daily_dir)

        self.assertEqual(result.generated_dates, ())
        self.assertEqual(result.status.value, "COMPLETED")
        self.assertEqual(consistency.status.value, "CONSISTENT")
        self.assertFalse((daily_dir / "2026-08-12.md").exists())

    def test_a_future_daily_pointer_is_reported(self):
        runtime = self._runtime(daily_pointer="2026-12-25")

        output, alerts = self._alerts(runtime)

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("2026-12-25", alerts[0])
        self.assertIn("2026-08-14", alerts[0])
        self.assertIn("미래 날짜", output)

    def test_a_future_monthly_pointer_is_reported(self):
        runtime = self._runtime(monthly_pointer="2027-06")

        output, alerts = self._alerts(runtime)

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("2027-06", alerts[0])
        self.assertIn("미래 달", output)

    def test_both_are_reported_separately(self):
        runtime = self._runtime(daily_pointer="2026-12-25", monthly_pointer="2027-06")

        _output, alerts = self._alerts(runtime)

        self.assertEqual(len(alerts), 2, alerts)

    def test_the_message_says_no_run_will_resolve_it(self):
        runtime = self._runtime(daily_pointer="2026-12-25")

        _output, alerts = self._alerts(runtime)

        self.assertIn("사람이", alerts[0])
        self.assertIn("생성되지 않는다", alerts[0])

    # ---- the false-alarm guards ----------------------------------------

    def test_a_healthy_pair_is_not_reported(self):
        runtime = self._runtime(daily_pointer="2026-08-13", monthly_pointer="2026-07")

        _output, alerts = self._alerts(runtime)

        self.assertEqual(alerts, [])

    def test_the_boundary_is_not_reported(self):
        """`end` is always yesterday and §49 forbids the current month, so a
        pointer AT today / at this month cannot come from a healthy run
        either — but it also causes no permanent stop, and a check that fires
        one day early on a boundary is how a section stops being read."""
        runtime = self._runtime(daily_pointer="2026-08-14", monthly_pointer="2026-08")

        _output, alerts = self._alerts(runtime)

        self.assertEqual(alerts, [])

    def test_no_state_at_all_is_not_reported(self):
        runtime = self._runtime()

        _output, alerts = self._alerts(runtime)

        self.assertEqual(alerts, [])

    def test_the_agent_side_already_answered_this_question(self):
        """Pins the precedent this applies. If the Agent check is ever
        removed, that is a policy change and this stops being "applying an
        answer the project already gave"."""
        import inspect

        from agent.status import AgentStatusSnapshot

        source = inspect.getsource(AgentStatusSnapshot.needs_attention)

        self.assertIn("which is in the future", source)

    # ---- the third member: a future timestamp that BLINDS a check --------
    #
    # `backup_state.last_successful_backup` is compared against the **real**
    # clock, not this class's pinned `NOW` — it and the file mtimes it is
    # weighed against are both real-time measurements, and mixing the two is
    # the trap `_healthy_backup_state()` names. So these two fixtures are
    # unconditionally past and unconditionally future, at any date this
    # suite could ever run on. A value chosen relative to today is precisely
    # the time bomb this sprint removed from `ArrivalVersusWorkDateTests`.
    FAR_FUTURE_ISO = "9999-01-01T09:00:00+09:00"
    FAR_PAST_ISO = "2000-01-01T09:00:00+09:00"

    def _backup_runtime(self, last_backup_iso):
        runtime = self._runtime()
        (runtime / "local_master" / "daily" / "2026-08-13.md").write_text(
            "# a real, never-pushed day\n", encoding="utf-8"
        )
        (runtime / "state" / "backup_state.json").write_text(
            json.dumps(
                {
                    "last_successful_backup": last_backup_iso,
                    "backup_status": "BACKUP_SUCCESS",
                }
            ),
            encoding="utf-8",
        )
        return runtime

    def _backup_alerts(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_backup_future", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(self.NOW)
        return (
            [a for a in attention if "미래 시각" in a],
            [a for a in attention if "원격 백업에 도달하지 않은" in a],
        )

    def test_a_future_backup_timestamp_silences_the_unbacked_history_check(self):
        """CHARACTERIZATION of the damage — the worst member of this family.

        The two state pointers stop *work*; this one silences a *safety
        check*. `_history_newer_than_the_last_backup()` asks "was this written
        after the last successful push", and a timestamp ahead of the calendar
        makes that true of nothing. Measured with one real never-pushed Daily
        present:

            last_successful_backup 2026-08-01  -> 1 alert  (correct)
            last_successful_backup 2027-05-01  -> 0 alerts

        Company History that is only on this machine reads as safe.
        """
        _future, unbacked = self._backup_alerts(
            self._backup_runtime(self.FAR_FUTURE_ISO)
        )

        self.assertEqual(unbacked, [])

    def test_the_operator_is_told_why_that_check_is_silent(self):
        """The fix: the silence itself is reported, before the silent line."""
        future, unbacked = self._backup_alerts(
            self._backup_runtime(self.FAR_FUTURE_ISO)
        )

        self.assertEqual(len(future), 1, future)
        self.assertIn("9999-01-01", future[0])
        self.assertIn("안전하다는 뜻이 아니다", future[0])
        self.assertEqual(unbacked, [])

    def test_an_ordinary_backup_timestamp_is_not_reported(self):
        future, unbacked = self._backup_alerts(
            self._backup_runtime(self.FAR_PAST_ISO)
        )

        self.assertEqual(future, [])
        self.assertEqual(len(unbacked), 1, "the real check must still work")

    def test_a_runner_finishing_a_moment_later_is_not_skew(self):
        """The false alarm this check nearly shipped with, and the reason for
        the tolerance.

        `ops_status.py` promises it is safe to run while the Runner is
        running, and `main()` takes its clock reading once at the top. A
        Backup that completes a few hundred milliseconds later legitimately
        writes a `last_successful_backup` after that reading. Reporting it as
        clock skew would put a line in ATTENTION on a perfectly healthy
        machine, every time an operator ran the two together.

        Caught by two existing "needs no attention" fixtures failing, not by
        reading the code — `_healthy_backup_state()` writes real-clock now on
        purpose, which is the same situation one second wide.

        The harm scales with the distance, so the tolerance is the right
        instrument: an hour ahead blinds the unbacked-History check for an
        hour and heals itself; months ahead is what the alert is for.
        """
        import contextlib
        import importlib.util
        from datetime import timedelta

        path = Path(__file__).resolve().parents[1] / "ops_status.py"

        for label, delta, expected in (
            ("runner finishing", timedelta(seconds=1), 0),
            ("minor jitter", timedelta(minutes=5), 0),
            ("just inside", timedelta(minutes=59), 0),
            ("real skew", timedelta(hours=3), 1),
            ("gross skew", timedelta(days=300), 1),
        ):
            with self.subTest(case=label):
                stamp = (datetime.now().astimezone() + delta).isoformat()
                runtime = self._backup_runtime(stamp)
                spec = importlib.util.spec_from_file_location(
                    f"ops_status_tolerance_{label.replace(' ', '_')}", path
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.RUNTIME_DIR = runtime
                with contextlib.redirect_stdout(io.StringIO()):
                    attention = module._print_history(datetime.now().astimezone())

                self.assertEqual(
                    len([a for a in attention if "미래 시각" in a]), expected
                )

    def test_a_naive_timestamp_is_compared_without_raising(self):
        """`backup_state.json` can carry an offset-less timestamp through a
        hand edit, and comparing naive to aware raises TypeError — the same
        guard `_history_newer_than_the_last_backup()` already applies."""
        for iso, expect_future in (
            (self.FAR_FUTURE_ISO.removesuffix("+09:00"), 1),
            (self.FAR_PAST_ISO.removesuffix("+09:00"), 0),
        ):
            with self.subTest(iso=iso):
                future, _unbacked = self._backup_alerts(self._backup_runtime(iso))

                self.assertEqual(len(future), expect_future, future)


class AttentionLineForgeryTests(unittest.TestCase):
    """NEW, **security**. BUG-6's shape in the view an operator reads first.

    `event_id` crosses the OneDrive transport from another Desktop and
    docs/02 constrains it only to "present and non-null" (BACKLOG A-15), so a
    newline inside one is accepted, stored, and interpolated into
    `ops_status.py`'s ATTENTION messages by `_kept_but_not_rendered()`,
    `find_orphaned_events()` and `_candidates_before()`.

    Measured before the fix — one KEEP Candidate whose `event_id` began
    ``"X\\n  ! 모든 검사 통과 — 사람이 지금 할 일은 없다"``:

        ! KEEP Candidate 1건이 저장돼 있는데 … 없다: X
        ! 모든 검사 통과 — 사람이 지금 할 일은 없다 (2026-08-05) — …

    The second line is entirely attacker-authored, sits inside ATTENTION with
    the same `  ! ` prefix as a genuine finding, and says the opposite of what
    the section is reporting. AGENT.md §6 tells an operator to read this view
    **first**, which makes it the highest-value place in the system to forge
    a line.

    `oplog.one_line()` closed exactly this for `collector.log` (BUG-6 / C10)
    and this file already accepted the argument for Run Manifest metrics —
    *"the rule that nothing read back from disk can forge a line should not
    depend on today's metric list staying the way it is"*. The metrics were
    the smaller half; the ATTENTION lines carry the untrusted ids.

    Guarded at the sink (`main()`'s print loop), so a message added later is
    covered without its author having to know.
    """

    FORGED_TAIL = "  ! 모든 검사 통과 — 사람이 지금 할 일은 없다"

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in ("history_candidates/keep", "history_candidates/review",
                    "local_master/daily", "local_master/monthly", "state",
                    "events/processed", "events/incoming", "events/transport",
                    "events/rejected", "locks"):
            (runtime / rel).mkdir(parents=True)
        return runtime

    def _module(self, runtime):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_forgery", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        return module

    def _plant_candidate(self, runtime, event_id):
        (runtime / "history_candidates" / "keep" / "HIST-FORGE.json").write_text(
            json.dumps(
                {
                    "history_id": "HIST-FORGE", "event_id": event_id,
                    "timestamp": "2026-08-05T10:00:00+09:00", "category": "MILESTONE",
                    "project_id": "P", "role": "COO", "summary": "s",
                    "evidence": [], "filter_result": "KEEP",
                }
            ),
            encoding="utf-8",
        )
        (runtime / "local_master" / "daily" / "2026-08-05.md").write_text(
            "# H\n\n## Milestones\n\n- Event ID: OTHER\n", encoding="utf-8"
        )

    def _main_output(self, runtime):
        import contextlib

        module = self._module(runtime)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module.main()
        return buffer.getvalue()

    # ---- the defect ------------------------------------------------------

    def test_a_newline_in_an_event_id_cannot_forge_an_attention_line(self):
        runtime = self._runtime()
        self._plant_candidate(runtime, "X\n" + self.FORGED_TAIL)

        printed = self._main_output(runtime)

        forged = [
            line for line in printed.splitlines() if line == self.FORGED_TAIL
        ]
        self.assertEqual(forged, [], printed)
        # The id is escaped, not stripped — the message still names it.
        self.assertIn("\\n", printed)

    def test_every_attention_line_starts_with_the_marker(self):
        """The structural property, stated once: inside the ATTENTION block
        there is exactly one line per item and each carries the prefix."""
        runtime = self._runtime()
        self._plant_candidate(runtime, "X\n" + self.FORGED_TAIL)

        printed = self._main_output(runtime)
        block = printed.split("ATTENTION\n", 1)[1].splitlines()[1:]

        self.assertTrue(block)
        for line in block:
            with self.subTest(line=line[:40]):
                self.assertTrue(line.startswith("  ! "), line)

    def test_other_line_breaking_characters_are_covered_too(self):
        """`one_line()` escapes every character `str.splitlines()` breaks on,
        not just `\\n` — the reason it exists rather than a `replace()`."""
        for raw in ("A\rB", "A\x0bB", "A\x0cB", "A\x1cB", "A B", "AB"):
            with self.subTest(raw=repr(raw)):
                runtime = self._runtime()
                self._plant_candidate(runtime, raw)

                printed = self._main_output(runtime)
                block = printed.split("ATTENTION\n", 1)[1].splitlines()[1:]

                for line in block:
                    self.assertTrue(line.startswith("  ! "), line)

    def test_an_ordinary_id_is_printed_unchanged(self):
        """The guard must not rewrite normal messages."""
        runtime = self._runtime()
        self._plant_candidate(runtime, "EVT-ORDINARY")

        printed = self._main_output(runtime)

        self.assertIn("EVT-ORDINARY (2026-08-05)", printed)
        self.assertNotIn("\\n", printed)

    def test_the_orphaned_event_block_is_guarded_too(self):
        """Not only ATTENTION: the HISTORY block prints orphaned ids with the
        same `!` prefix and fixed indentation a forged line would imitate."""
        runtime = self._runtime()
        (runtime / "events" / "processed" / "EVT.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0", "event_id": "X\n" + self.FORGED_TAIL,
                    "timestamp": "2026-08-05T10:00:00+09:00", "source": "DESKTOP_1",
                    "role": "CTO_BACKEND", "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED", "status": "IN_PROGRESS",
                    "summary": "s", "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

        printed = self._main_output(runtime)

        self.assertEqual(
            [line for line in printed.splitlines() if line == self.FORGED_TAIL],
            [],
            printed,
        )


class RejectedStagingResidueTests(unittest.TestCase):
    """An alert that named the wrong thing, corrected without touching the
    pipeline it was blamed on.

    C27 §8 measured this and left it: `write_event_json()`'s default
    directory is `runtime/events/incoming/` and it `mkstemp`s there, so a
    Desktop 4 reporter killed mid-write leaves `.tmp-….json` in the one
    directory the Collector reads. `collector/runtime.run_once()`
    deliberately does not skip it, so a truncated one is REJECTED and moves
    to `rejected/` under its staging name — and ATTENTION then said

        Collector가 거부한 Event 1건 — 사람이 확인해야 한다

    C27's own summary of what remained: *"남는 것은 잘못 이름 붙은 경보
    하나"*. Nothing was rejected. A write on this machine stopped, no
    Desktop sent anything, and the sentence sends an operator to look at the
    wrong machine.

    C27 judged that correcting it "means changing what the Collector consumes
    from `incoming/`, which is docs/03's processing pipeline rather than a
    reader's filter". That is true of *stopping* the Collector from consuming
    them, and this sprint changed none of it — `ArchitectureInvariant`'s
    boundary test still pins that `run_once()` consumes them. It is not true
    of what the **report** calls the result.
    """

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in ("events/transport", "events/incoming", "events/processed",
                    "events/rejected", "history_candidates/keep",
                    "history_candidates/review", "local_master/daily",
                    "local_master/monthly", "state", "locks"):
            (runtime / rel).mkdir(parents=True)
        return runtime

    def _snapshot(self, runtime):
        from app.desktop_activity import read_company_activity

        return read_company_activity(
            processed_dir=runtime / "events" / "processed",
            transport_dir=runtime / "events" / "transport",
            incoming_dir=runtime / "events" / "incoming",
            rejected_dir=runtime / "events" / "rejected",
        )

    def _alerts(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_residue", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_company(NOW)
        return buffer.getvalue(), attention

    def test_a_staging_file_in_rejected_is_not_a_rejected_event(self):
        runtime = self._runtime()
        (runtime / "events" / "rejected" / ".tmp-abandoned.json").write_text(
            '{"event_id": "EV', encoding="utf-8"
        )

        backlog = self._snapshot(runtime).backlog

        self.assertEqual(backlog.rejected, 0)
        self.assertEqual(backlog.rejected_incomplete_write, 1)

    def test_the_alert_no_longer_claims_an_event_was_rejected(self):
        runtime = self._runtime()
        (runtime / "events" / "rejected" / ".tmp-abandoned.json").write_text(
            '{"event_id": "EV', encoding="utf-8"
        )

        output, attention = self._alerts(runtime)

        rejected_event_alerts = [a for a in attention if "거부한 Event" in a]
        residue_alerts = [a for a in attention if "중단된 쓰기 잔여물" in a]
        self.assertEqual(rejected_event_alerts, [])
        self.assertEqual(len(residue_alerts), 1, attention)
        self.assertIn("지워도", residue_alerts[0])
        self.assertIn("rejected_incomplete_write=1", output)

    def test_a_real_rejected_event_still_gets_its_own_sentence(self):
        """The guard on the split: the message that was right must stay."""
        runtime = self._runtime()
        (runtime / "events" / "rejected" / "badrole.json").write_text(
            json.dumps({"event_id": "EVT-BAD", "source": "DESKTOP_1"}), encoding="utf-8"
        )

        _output, attention = self._alerts(runtime)

        rejected_event_alerts = [a for a in attention if "거부한 Event" in a]
        self.assertEqual(len(rejected_event_alerts), 1, attention)
        self.assertIn("1건", rejected_event_alerts[0])
        self.assertEqual([a for a in attention if "중단된 쓰기 잔여물" in a], [])

    def test_both_kinds_are_reported_separately(self):
        runtime = self._runtime()
        (runtime / "events" / "rejected" / "badrole.json").write_text(
            json.dumps({"event_id": "EVT-BAD", "source": "DESKTOP_1"}), encoding="utf-8"
        )
        (runtime / "events" / "rejected" / ".tmp-abandoned.json").write_text(
            '{"event_id": "EV', encoding="utf-8"
        )

        backlog = self._snapshot(runtime).backlog

        self.assertEqual(backlog.rejected, 1)
        self.assertEqual(backlog.rejected_incomplete_write, 1)
        # The attribution describes the same set the count does.
        self.assertEqual(backlog.rejected_sources.total, 1)

    def test_a_staging_name_still_blocks_the_name_in_incoming(self):
        """`name_collision` asks a different question — whether the
        destination name is taken — and a staging file takes it just as
        firmly. Splitting the *count* must not narrow that check (BUG-43)."""
        runtime = self._runtime()
        (runtime / "events" / "rejected" / ".tmp-abandoned.json").write_text(
            '{"event_id": "EV', encoding="utf-8"
        )
        (runtime / "events" / "incoming" / ".tmp-abandoned.json").write_text(
            '{"event_id": "EV', encoding="utf-8"
        )

        backlog = self._snapshot(runtime).backlog

        self.assertEqual(backlog.name_collision, 1)

    def test_the_collector_boundary_is_unchanged(self):
        """This sprint changed the report, not what the Collector consumes.
        The boundary itself stays pinned where C27 put it."""
        import inspect

        from collector import runtime as collector_runtime

        source = inspect.getsource(collector_runtime.run_once)

        self.assertNotIn("is_incomplete_write", source)


class MonthlyCountsMoreThanItShowsTests(unittest.TestCase):
    """A Monthly History that counted an item it did not write down.

    The Daily-side sibling of this drop is already characterized
    (`test_daily_history.py::
    test_a_category_less_keep_candidate_silently_loses_its_detail`): a
    candidate whose category is not one of the four is filed under no
    section. Nobody had aimed the same question at Monthly, where it is
    strictly worse -- Daily at least leaves the bare summary in `## Summary`
    and the id in `## Evidence`, and Monthly has neither, so the Event
    disappears completely.

    Measured, `render_monthly_markdown()` with two items, one carrying
    `category="Decision"`:

        - Consolidated Items: 2
        sections            : Major Decisions, Source Records, Metadata
        `EVT-2` in the file : False

    and `consolidate_month()` returned `MONTHLY_GENERATED, item_count=2`.
    Every indicator healthy, one month of Company History one Event short.

    Reachable without corruption or an attacker. A `## Late Events` item
    states its own category on a `- Category:` bullet in the Daily file
    (docs/06 §37), `monthly/parser.py` reads that bullet verbatim, and
    docs/06 §57 / docs/11 §71 explicitly permit the COO to edit a Daily
    History by hand. One hand-typed `- Category: Decision` deletes that Event
    from the month, permanently -- rebuilding produces the same file.

    NOT FIXED, reported. Which section an unrecognised category belongs in is
    a docs/09 §14 rendering decision. What needed no decision is that the
    file states its own total two lines below the items it dropped.
    """

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in ("history_candidates/keep", "history_candidates/review",
                    "local_master/daily", "local_master/monthly", "state",
                    "events/processed", "locks"):
            (runtime / rel).mkdir(parents=True)
        return runtime

    def _monthly(self, runtime, key, *, claimed, event_ids):
        body = ["# DOJOONPASS Company History — " + key, "", "## Major Decisions", ""]
        for event_id in event_ids:
            body.extend([f"### P", "", "- s", f"- Event ID: {event_id}", ""])
        body.extend(["## Metadata", "", f"- History Month: {key}",
                     f"- Consolidated Items: {claimed}"])
        (runtime / "local_master" / "monthly" / f"{key}.md").write_text(
            "\n".join(body) + "\n", encoding="utf-8"
        )

    def _run(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_monthly_short", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [a for a in attention if "적게 기록한 달" in a]

    # ---- the defect, through the real renderer --------------------------

    def test_the_real_renderer_drops_an_unrecognised_category_and_still_counts_it(self):
        """Reachability, not a hand-built fixture. This is the defect."""
        from monthly.markdown import MonthlyItem, render_monthly_markdown

        items = [
            MonthlyItem(event_id="EVT-1", category="DECISION", project="Ops",
                        summary="kept", owner="COO", source_date=date(2026, 8, 5)),
            MonthlyItem(event_id="EVT-2", category="Decision", project="Ops",
                        summary="lost", owner="COO", source_date=date(2026, 8, 6)),
        ]
        text = render_monthly_markdown(
            year=2026, month=8, items=items,
            source_dates=[date(2026, 8, 5), date(2026, 8, 6)],
            generated_at="2026-09-01T11:00:00+09:00", coverage="COMPLETE",
        )

        self.assertIn("- Consolidated Items: 2", text)
        self.assertNotIn("EVT-2", text)
        self.assertNotIn("lost", text)
        self.assertEqual(text.count("- Event ID: "), 1)

    def test_a_shortfall_is_reported(self):
        runtime = self._runtime()
        self._monthly(runtime, "2026-08", claimed=2, event_ids=("EVT-1",))

        output, alerts = self._run(runtime)

        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("2026-08(2→1)", alerts[0])
        self.assertIn("Monthly 항목 누락", output)

    def test_the_message_says_a_rebuild_will_not_help(self):
        """The one thing an operator would try first, and it produces the
        same file — the category is in the Daily, not in the run."""
        runtime = self._runtime()
        self._monthly(runtime, "2026-08", claimed=3, event_ids=("EVT-1",))

        _output, alerts = self._run(runtime)

        self.assertIn("다시 만들어도 같은 결과", alerts[0])
        self.assertIn("- Category:", alerts[0])

    # ---- the false-alarm guards ----------------------------------------

    def test_a_consistent_month_is_not_reported(self):
        runtime = self._runtime()
        self._monthly(runtime, "2026-08", claimed=2, event_ids=("EVT-1", "EVT-2"))

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_an_empty_month_is_not_reported(self):
        """docs/09 §71-73: a month with nothing material still gets a file,
        with zero items and zero Event ID lines."""
        runtime = self._runtime()
        (runtime / "local_master" / "monthly" / "2026-07.md").write_text(
            "# DOJOONPASS Company History — 2026-07\n\n"
            "## Executive Summary\n\n"
            "No material company-level changes were recorded during this month.\n\n"
            "## Metadata\n\n- Consolidated Items: 0\n",
            encoding="utf-8",
        )

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_a_hand_added_entry_is_not_reported(self):
        """docs/06 §57's Monthly equivalent. More items than the count is an
        edit, not a loss, and a standing line for doing what the spec allows
        is the alert-that-cannot-clear this project keeps removing."""
        runtime = self._runtime()
        self._monthly(runtime, "2026-08", claimed=1, event_ids=("EVT-1", "EVT-HAND"))

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_a_month_with_no_count_line_is_skipped(self):
        runtime = self._runtime()
        (runtime / "local_master" / "monthly" / "2026-08.md").write_text(
            "# Title\n\n## Major Decisions\n\n- Event ID: EVT-1\n\n## Metadata\n\n"
            "- History Month: 2026-08\n",
            encoding="utf-8",
        )

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_an_unparseable_count_is_skipped_not_guessed(self):
        runtime = self._runtime()
        (runtime / "local_master" / "monthly" / "2026-08.md").write_text(
            "# Title\n\n## Metadata\n\n- Consolidated Items: many\n", encoding="utf-8"
        )

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_a_staging_file_is_not_a_month(self):
        """`.tmp-*.md` is an unfinished write (C27), and a truncated one is
        exactly "claims more than it shows" by construction."""
        runtime = self._runtime()
        (runtime / "local_master" / "monthly" / ".tmp-2026-08.md").write_text(
            "# Title\n\n## Metadata\n\n- Consolidated Items: 9\n", encoding="utf-8"
        )

        _output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])

    def test_an_undecodable_month_does_not_break_the_view(self):
        runtime = self._runtime()
        (runtime / "local_master" / "monthly" / "2026-08.md").write_bytes(
            b"\xff\xfe\x00 not utf-8 \xff"
        )

        output, alerts = self._run(runtime)

        self.assertEqual(alerts, [])
        self.assertIn("HISTORY", output)

    def test_a_forged_event_id_line_in_a_summary_hides_a_real_shortfall(self):
        """CHARACTERIZATION of this check's own limit — asked of it the same
        way every other detector in this repository is asked of itself.

        A summary is rendered unescaped (BUG-11/27, an open docs/06 rendering
        decision), so a summary carrying a newline and `- Event ID: …` adds a
        line this check counts. Measured, two items — one dropped for its
        category, one whose summary forges a line:

            - Consolidated Items: 2
            `- Event ID: ` lines    2
            EVT-2 in the file       False
            this check              () -- silent

        The direction matters and is the reason this is acceptable rather
        than a defect in the check: a forgery can only RAISE `rendered`, so
        it can silence this check and can never make it cry wolf. Counting
        `### ` headings instead is defeated by the same root — the defect is
        that a summary can write arbitrary Markdown at all.

        If this starts failing, either BUG-11/27 was closed (summaries are
        escaped) or the counting changed; both need BACKLOG updated.
        """
        from monthly.markdown import MonthlyItem, render_monthly_markdown

        runtime = self._runtime()
        text = render_monthly_markdown(
            year=2026,
            month=8,
            items=[
                MonthlyItem(event_id="EVT-1", category="DECISION", project="Ops",
                            summary="ok\n- Event ID: FORGED", owner="COO",
                            source_date=date(2026, 8, 5)),
                MonthlyItem(event_id="EVT-2", category="Decision", project="Ops",
                            summary="dropped by category", owner="COO",
                            source_date=date(2026, 8, 6)),
            ],
            source_dates=[date(2026, 8, 5)],
            generated_at="2026-09-01T11:00:00+09:00",
            coverage="COMPLETE",
        )
        (runtime / "local_master" / "monthly" / "2026-08.md").write_text(
            text, encoding="utf-8"
        )

        # The loss is real...
        self.assertIn("- Consolidated Items: 2", text)
        self.assertNotIn("EVT-2", text)
        # ...and this check cannot see it.
        _output, alerts = self._run(runtime)
        self.assertEqual(alerts, [])

    def test_the_count_line_matches_what_the_monthly_renderer_writes(self):
        """Both literals this check reads are the renderer's. If either moves,
        the check goes quiet rather than wrong, which is the failure mode
        worth a test."""
        import inspect

        import monthly.markdown as monthly_markdown

        source = inspect.getsource(monthly_markdown)

        self.assertIn("- Consolidated Items: {item_count}", source)
        self.assertIn("- Event ID: {item.event_id}", source)


class JunctionInBackupScopeTests(unittest.TestCase):
    """A-19/BUG-57 made visible without deciding it.

    A junction under a backup-scoped directory copies content from outside
    Local Master into the Working Copy and pushes it. Re-measured through the
    real sync (C29):

        Path.is_symlink()             False   <- the sync's guard misses it
        os.path.isjunction()          True    <- stdlib knows exactly
        sync_to_working_copy() added  daily/linked/notes.md,
                                      daily/linked/private.md
        scan_for_secrets(master)      ()      <- nothing flagged

    Both existing guards stay quiet by construction: `_relative_files()`
    excludes symlinks and a junction is not one, and the secret scan only
    reacts to secret-*shaped names*, so ordinary files pass silently. The
    BACKLOG's note that the scan "catches it" is true only for a file that is
    also secret-named.

    **Reported, never refused.** Whether a redirected History directory is a
    legitimate layout is A-19's deployment decision — the record says
    refusing it was implemented once and reverted for exactly that reason
    (redirecting `daily/` to another drive for disk space is a real use).
    Nothing here changes what Backup copies.

    Printed as a fact, not raised as ATTENTION, following C26: on a
    deliberately redirected deployment no operator action would clear it.
    What was missing is that the redirect exists and where it points.
    """

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in ("history_candidates/keep", "history_candidates/review",
                    "state", "events/processed"):
            (runtime / rel).mkdir(parents=True)
        (runtime / "local_master" / "monthly").mkdir(parents=True)
        outside = root / "outside"
        outside.mkdir()
        (outside / "notes.md").write_text("outside Local Master\n", encoding="utf-8")
        return runtime, outside

    def _junction(self, link: Path, target: Path):
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.skipTest("directory junctions are not available on this machine")

    @staticmethod
    def _ops_status():
        """A fresh `ops_status` module object.

        Loaded by path rather than imported so `RUNTIME_DIR` can be pointed
        at a temporary tree without touching the one every other test sees.
        """
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_junction", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _lines(self, runtime):
        import contextlib

        module = self._ops_status()
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        printed = [line.strip() for line in buffer.getvalue().splitlines() if "junction" in line]
        return printed, [a for a in attention if "junction" in a]

    def test_a_junction_inside_daily_is_reported_with_its_target(self):
        runtime, outside = self._runtime()
        daily = runtime / "local_master" / "daily"
        daily.mkdir()
        (daily / "2026-08-13.md").write_text("# d\n", encoding="utf-8")
        self._junction(daily / "linked", outside)

        printed, alerts = self._lines(runtime)

        self.assertEqual(len(printed), 1, printed)
        self.assertIn("daily", printed[0])
        self.assertIn("linked", printed[0])
        self.assertIn(str(outside), printed[0])
        self.assertEqual(alerts, [], "a deployment choice is not an alert")

    def test_a_whole_scope_directory_that_is_a_junction_is_reported(self):
        """The layout the record calls legitimate — redirecting `daily/` to
        another drive. Still stated, because the operator should be able to
        see it from the status view."""
        runtime, outside = self._runtime()
        self._junction(runtime / "local_master" / "daily", outside)

        printed, alerts = self._lines(runtime)

        self.assertEqual(len(printed), 1, printed)
        self.assertEqual(alerts, [])

    def test_an_ordinary_layout_says_nothing(self):
        runtime, _outside = self._runtime()
        daily = runtime / "local_master" / "daily"
        daily.mkdir()
        (daily / "2026-08-13.md").write_text("# d\n", encoding="utf-8")

        printed, alerts = self._lines(runtime)

        self.assertEqual((printed, alerts), ([], []))

    def test_the_sync_really_does_copy_through_it(self):
        """The premise, from the real sync rather than assumed — and the
        reason the two existing guards do not see it."""
        from backup.working_copy import scan_for_secrets, sync_to_working_copy

        runtime, outside = self._runtime()
        master = runtime / "local_master"
        daily = master / "daily"
        daily.mkdir()
        (daily / "2026-08-13.md").write_text("# d\n", encoding="utf-8")
        link = daily / "linked"
        self._junction(link, outside)
        wc = runtime / "wc"
        wc.mkdir()

        result = sync_to_working_copy(master, wc)

        self.assertFalse(link.is_symlink(), "a junction is not a symlink")
        # C70: confirmed on every interpreter now, through the detector's own
        # predicate rather than the 3.12-only stdlib call this used to guard on.
        self.assertTrue(self._ops_status()._is_junction(link))
        self.assertTrue(any("linked" in name for name in result.added), result.added)
        self.assertEqual(scan_for_secrets(master), (), "ordinary names are not flagged")

    def test_an_older_interpreter_still_sees_the_junction(self):
        """C70. **This test used to assert the opposite.**

        It was called `test_it_reports_nothing_when_the_platform_cannot_answer`
        and it pinned `found == ()` for an interpreter without
        `os.path.isjunction` — which was every interpreter this project ran
        on at the time (Python 3.9.7, BACKLOG D). So the detector's blindness
        on the deployment machine was not an oversight that slipped through:
        it was **held in place by a passing test**, while the two tests above
        skipped rather than catching it. Three tests, and the net effect was
        that a security detector never ran there and nothing said so.

        **C76: the deployment runtime is now 3.13.14, so the stdlib probe is
        present and the production path no longer reaches the fallback.**
        This test keeps reaching it on purpose, by injecting `None`. A
        fallback that only runs on machines the project has left is a
        fallback nobody would notice breaking — and the project is worked on
        from several machines (AGENT.md section 1), so "left" is not
        "gone".

        There was never a platform that could not be asked.
        `os.lstat().st_reparse_tag` has carried the answer since 3.8.
        """
        runtime, outside = self._runtime()
        daily = runtime / "local_master" / "daily"
        daily.mkdir()
        link = daily / "linked"
        self._junction(link, outside)
        module = self._ops_status()

        with mock.patch.object(os.path, "isjunction", None, create=True):
            found, skipped = module._junctions_in_scope(
                runtime / "local_master"
            )

        self.assertEqual(skipped, 0, "nothing failed to read")
        self.assertEqual(len(found), 1, found)
        self.assertIn("linked", found[0][0])
        self.assertEqual(found[0][1], os.path.realpath(link))

    def test_a_symlink_is_not_called_a_junction(self):
        """The over-correction guard, and the reason the fallback reads the
        reparse **tag** and not the reparse-point bit.

        Both set that bit. `backup/working_copy._relative_files()` already
        excludes symlinks, so reporting one as a junction would put a
        permanent exposure line on a machine that has no exposure — the
        skim-training failure C26 named.
        """
        runtime, outside = self._runtime()
        daily = runtime / "local_master" / "daily"
        daily.mkdir()
        try:
            os.symlink(outside, daily / "linked", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        module = self._ops_status()

        with mock.patch.object(os.path, "isjunction", None, create=True):
            found, skipped = module._junctions_in_scope(
                runtime / "local_master"
            )

        self.assertEqual((found, skipped), ((), 0))

    def test_an_absent_local_master_is_still_not_a_skipped_read(self):
        """C68's asymmetry, kept. Removing the interpreter branch must not
        turn "there is nothing to look at" into "I failed to look"."""
        runtime, _outside = self._runtime()
        module = self._ops_status()

        self.assertEqual(
            module._junctions_in_scope(runtime / "local_master" / "nope"),
            ((), 0),
        )


class MonthlyShortfallSummaryForgeryTests(unittest.TestCase):
    """The check above states it "can be silenced but cannot cry wolf". Half
    of that was false, and neither half needed a newline or a hand edit.

    An item's summary is rendered raw as its block's first bullet, so a
    summary reading `Consolidated Items: 999` is byte-identical to the
    metadata line the check reads -- and it comes first in the file, which
    is the one the check took. Measured, one perfectly good month, one item:

        summary `Consolidated Items: 999`  ->  ('2026-08', 999, 1)
        summary `Event ID: EXTRA`          ->  ()   (a shortfall hidden)

    The first put "a month recorded 998 items fewer than it counted" in
    front of an operator, on a month that lost nothing. A standing false
    ATTENTION line is how an operator learns to stop reading the section,
    which costs more than the check is worth.

    Both are closed by `daily/markdown.summary_line_indices()` -- the
    renderer's own rule for which bullet is a summary. The BUG-11/27 route
    (a summary carrying a real newline) stays open in the silencing
    direction and stays documented there; it needs a hand-edited Monthly,
    because `monthly/parser.py` is line-based.
    """

    def _detect(self, summaries):
        from monthly.markdown import MonthlyItem, render_monthly_markdown
        from ops_status import _monthly_counts_more_than_it_shows

        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        items = [
            MonthlyItem(event_id=f"EVT-{i}", category="DECISION", project="Ops",
                        summary=summary, owner="COO", source_date=date(2026, 8, 5))
            for i, summary in enumerate(summaries)
        ]
        (directory / "2026-08.md").write_text(
            render_monthly_markdown(
                year=2026, month=8, items=items,
                source_dates=[date(2026, 8, 5)],
                generated_at="2026-09-01T02:00:00+09:00", coverage="1/31",
            ),
            encoding="utf-8",
        )
        return _monthly_counts_more_than_it_shows(directory)

    def test_a_summary_cannot_forge_the_claimed_total(self):
        self.assertEqual(self._detect(["Consolidated Items: 999"]), ())

    def test_an_ordinary_month_is_still_quiet(self):
        self.assertEqual(self._detect(["shipped it", "and this"]), ())

    def test_a_real_shortfall_is_still_reported(self):
        """The fix narrows the read; the check must still fire."""
        from monthly.markdown import MonthlyItem, render_monthly_markdown
        from ops_status import _monthly_counts_more_than_it_shows

        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        items = [
            MonthlyItem(event_id="EVT-1", category="DECISION", project="Ops",
                        summary="kept", owner="COO", source_date=date(2026, 8, 5)),
            MonthlyItem(event_id="EVT-2", category="Decision", project="Ops",
                        summary="dropped", owner="COO", source_date=date(2026, 8, 5)),
        ]
        (directory / "2026-08.md").write_text(
            render_monthly_markdown(
                year=2026, month=8, items=items,
                source_dates=[date(2026, 8, 5)],
                generated_at="2026-09-01T02:00:00+09:00", coverage="1/31",
            ),
            encoding="utf-8",
        )

        self.assertEqual(
            _monthly_counts_more_than_it_shows(directory), (("2026-08", 2, 1),)
        )

    def test_a_real_shortfall_is_reported_even_beside_a_forged_summary(self):
        """The other direction, which only shows up when there IS something
        to hide: an extra `- Event ID:` line raises `rendered` past the
        genuine shortfall and the check goes quiet. Without the shortfall
        the same summary changes nothing, so this is the case that has to
        carry it."""
        from monthly.markdown import MonthlyItem, render_monthly_markdown
        from ops_status import _monthly_counts_more_than_it_shows

        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        items = [
            MonthlyItem(event_id="EVT-1", category="DECISION", project="Ops",
                        summary="Event ID: EXTRA", owner="COO",
                        source_date=date(2026, 8, 5)),
            MonthlyItem(event_id="EVT-2", category="Decision", project="Ops",
                        summary="dropped", owner="COO", source_date=date(2026, 8, 5)),
        ]
        (directory / "2026-08.md").write_text(
            render_monthly_markdown(
                year=2026, month=8, items=items,
                source_dates=[date(2026, 8, 5)],
                generated_at="2026-09-01T02:00:00+09:00", coverage="1/31",
            ),
            encoding="utf-8",
        )

        self.assertEqual(
            _monthly_counts_more_than_it_shows(directory), (("2026-08", 2, 1),)
        )


class StrandedCandidateHiddenByASummaryTests(unittest.TestCase):
    """`_kept_but_not_rendered()` is the detector for a KEEP Candidate that
    is stored but never reached its Daily file -- E-17's shape, and the one
    kind of loss no other check sees. It answers by looking for the exact
    line the renderer would have written for that `event_id`.

    The renderer writes a summary raw as its block's first bullet, so a
    Candidate whose summary reads `Event ID: EVT-B` renders that same line.
    Measured -- EVT-A rendered with that summary, EVT-B stored and genuinely
    absent from the file:

        summary `Event ID: EVT-B`   ->  ()
        summary `Shipped it.`       ->  ('EVT-B (2026-08-05)',)

    One ordinary summary switched the loss detector off for the Candidate it
    named. Silencing only -- a summary can add a line, never remove one --
    but for a detector whose whole job is to notice an absence, silencing is
    the harm.

    Fixed by excluding summary lines, which cannot go the other way: a
    summary is never the renderer's label line, so nothing genuinely
    rendered leaves the set. C30's empty-`event_id` case (BACKLOG A-15) is
    re-checked below for the same reason it was written -- this function has
    already been broken once by a change to how the line is matched.
    """

    def _candidate(self, event_id, summary):
        from history import HistoryCandidate, HistoryDecision

        return HistoryCandidate(
            history_id="HIST-" + event_id,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
            category="DECISION",
            project_id="OPS",
            role="COO",
            summary=summary,
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def _stranded(self, rendered, stored):
        from daily.markdown import render_daily_markdown
        from ops_status import _kept_but_not_rendered

        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        (directory / "2026-08-05.md").write_text(
            render_daily_markdown(date(2026, 8, 5), rendered, "gen"),
            encoding="utf-8",
        )
        from ops_status import StoredCandidate

        # C92: the second half is the dates it could not read;
        # `TheTwoRenderChecksNameTheDailyTheyCouldNotReadTests` holds it.
        stranded, _unreadable = _kept_but_not_rendered(
            tuple(
                StoredCandidate(f"s{i}", e, date(2026, 8, 5))
                for i, e in enumerate(stored)
            ),
            directory,
        )
        return stranded

    def test_a_summary_cannot_hide_a_stranded_candidate(self):
        found = self._stranded(
            [self._candidate("EVT-A", "Event ID: EVT-B")], ["EVT-A", "EVT-B"]
        )

        self.assertEqual(found, ("EVT-B (2026-08-05)",))

    def test_the_ordinary_case_reports_the_same_thing(self):
        """The forged summary must change nothing at all, not merely stop
        hiding -- same finding, same wording."""
        found = self._stranded(
            [self._candidate("EVT-A", "Shipped it.")], ["EVT-A", "EVT-B"]
        )

        self.assertEqual(found, ("EVT-B (2026-08-05)",))

    def test_nothing_is_reported_when_both_are_rendered(self):
        found = self._stranded(
            [
                self._candidate("EVT-A", "Event ID: EVT-B"),
                self._candidate("EVT-B", "and this"),
            ],
            ["EVT-A", "EVT-B"],
        )

        self.assertEqual(found, ())

    def test_an_empty_event_id_is_still_found_in_its_file(self):
        """C30's regression: `validate_event()` accepts `event_id=""`
        (BACKLOG A-15), and a matcher that slices a prefix ending in a space
        reported that Candidate as permanently lost while it sat in its own
        Daily file."""
        found = self._stranded([self._candidate("", "empty id")], [""])

        self.assertEqual(found, ())


class IncomingStagingResidueTests(RejectedStagingResidueTests):
    """The same residue one directory earlier, which the sprint that fixed
    `rejected/` did not look at.

    Three directories can hold `.tmp-….json` and two of them named it
    correctly: `incomplete` for `transport/`, `rejected_incomplete_write`
    for `rejected/`. `incoming/` -- the one `write_event_json()` actually
    stages into -- called it an Event. Measured, one staging file and
    nothing else in the whole runtime:

        awaiting_collection=1   is_clear=False
        -> ATTENTION "Collector가 아직 가져가지 않은 Event 1건"

    `awaiting_collection` is defined as *promoted by intake but not
    collected*, and a staging file was never promoted -- the local reporter
    wrote it straight into `incoming/`. So this is not a number reported
    loosely; it is a file that does not belong in that number.

    Unlike its two siblings this one clears by itself: the next Collector
    run consumes it (docs/03's decision, untouched here) and moves it to
    `rejected/`, where the sentence above already names it correctly. One
    run of a wrong name -- in the window right after a crash, which is
    exactly when someone is reading this view.

    Inherits the fixtures from the class above deliberately: same runtime,
    same snapshot, same alert capture, so the two halves cannot drift into
    testing different things about one file.
    """

    def _made(self, event_id):
        return create_event(
            source="DESKTOP_1", role="COO", project_id="OPS",
            event_type="COMPLETED", status="COMPLETED", summary="s",
            history_candidate=True, event_id=event_id,
        )

    def test_a_staging_file_in_incoming_is_not_an_awaiting_event(self):
        runtime = self._runtime()
        (runtime / "events" / "incoming" / ".tmp-abandoned.json").write_text(
            '{"event_id": "EV', encoding="utf-8"
        )

        backlog = self._snapshot(runtime).backlog

        self.assertEqual(backlog.awaiting_collection, 0)
        self.assertEqual(backlog.incoming_incomplete_write, 1)
        self.assertEqual(backlog.unreadable_incoming, 0)

    def test_a_complete_staging_file_counts_the_same(self):
        """The crash window is *after* the write and before `os.replace`, so
        the residue is usually valid JSON. It is still not an Event that
        intake promoted."""
        runtime = self._runtime()
        (runtime / "events" / "incoming" / ".tmp-whole.json").write_text(
            self._made("EVT-W").to_json(), encoding="utf-8"
        )

        backlog = self._snapshot(runtime).backlog

        self.assertEqual(backlog.awaiting_collection, 0)
        self.assertEqual(backlog.incoming_incomplete_write, 1)

    def test_it_does_not_hold_is_clear_false_on_its_own(self):
        runtime = self._runtime()
        (runtime / "events" / "incoming" / ".tmp-abandoned.json").write_text(
            "x", encoding="utf-8"
        )

        self.assertTrue(self._snapshot(runtime).backlog.is_clear)

    def test_a_real_event_beside_it_is_still_counted(self):
        """The fix narrows the count; it must not empty it. The source
        breakdown has to agree with the narrowed count too --
        `SourceBreakdown.total` promises to equal it."""
        runtime = self._runtime()
        incoming = runtime / "events" / "incoming"
        (incoming / ".tmp-abandoned.json").write_text("x", encoding="utf-8")
        (incoming / "EVT-R.json").write_text(
            self._made("EVT-R").to_json(), encoding="utf-8"
        )

        backlog = self._snapshot(runtime).backlog

        self.assertEqual(backlog.awaiting_collection, 1)
        self.assertEqual(backlog.incoming_incomplete_write, 1)
        self.assertFalse(backlog.is_clear)
        self.assertEqual(backlog.awaiting_collection_sources.total, 1)

    def test_the_operator_is_told_what_it_actually_is(self):
        runtime = self._runtime()
        (runtime / "events" / "incoming" / ".tmp-abandoned.json").write_text(
            "x", encoding="utf-8"
        )

        _printed, attention = self._alerts(runtime)

        residue = [line for line in attention if "incoming/에 중단된 쓰기 잔여물" in line]
        self.assertEqual(len(residue), 1, attention)
        self.assertIn("Event가 아니다", residue[0])
        self.assertEqual(
            [line for line in attention if "가져가지 않은 Event" in line], []
        )


class AStrandedCandidateIsRecoveredByACompanionTests(unittest.TestCase):
    """E-17's alert said "no run will insert this" and BACKLOG said "nothing
    will retry it". The premise under both is right -- step 6.5's targets
    are only the dates *that run* collected -- and the conclusion is one
    step too far.

    If any further Event dated that same day is collected later, the date
    joins `kept_dates`, and `select_late_candidates()` looks at **every**
    stored candidate for that date, not just the new one. The stranded one
    goes in with it. Measured:

        EVT-A stored -> Daily Close      2026-08-05.md written
        EVT-S stored after the close     detector: ('EVT-S (2026-08-05)',)
        EVT-N stored, same date, later   UPDATED_LATE_EVENT
                                         added_event_ids=('EVT-S', 'EVT-N')
                                         detector: ()

    So it cannot get in under its own power, and it does get in if a
    companion arrives. For a past date a companion usually never arrives,
    which is why the alert is right to exist -- but "no run will ever insert
    this" changes what a person does about it, and sends them to hand-edit a
    Company History file that a later run would have repaired.

    Nothing tested this path. It is the only automatic recovery E-17 has.
    """

    def setUp(self):
        from history import HistoryCandidate, HistoryDecision  # noqa: F401
        from history.file_repository import FileHistoryRepository

        self.FileHistoryRepository = FileHistoryRepository
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = self.FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )
        self.daily_dir = self.root / "daily"

    def _candidate(self, event_id, hour):
        from history import HistoryCandidate, HistoryDecision

        return HistoryCandidate(
            history_id="HIST-" + event_id, event_id=event_id,
            timestamp=f"2026-08-05T{hour:02d}:00:00+09:00", category="MILESTONE",
            project_id="P", role="COO", summary="s " + event_id, evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def _detector(self, event_id):
        from ops_status import _kept_but_not_rendered

        from ops_status import StoredCandidate

        # C92: see the sibling helper above on the second return value.
        stranded, _unreadable = _kept_but_not_rendered(
            (StoredCandidate(f"HIST-{event_id}", event_id, date(2026, 8, 5)),),
            self.daily_dir,
        )
        return stranded

    def _close_the_day_then_strand(self):
        from daily import generate_daily_history

        self.repo.save(self._candidate("EVT-A", 10))
        generate_daily_history(
            self.repo, date(2026, 8, 5), output_dir=self.daily_dir, generated_at="gen"
        )
        self.repo.save(self._candidate("EVT-S", 11))

    def test_the_stranded_candidate_is_detected(self):
        self._close_the_day_then_strand()

        self.assertEqual(self._detector("EVT-S"), ("EVT-S (2026-08-05)",))

    def test_a_companion_on_the_same_date_carries_it_in(self):
        from daily import update_daily_history

        self._close_the_day_then_strand()
        self.repo.save(self._candidate("EVT-N", 12))

        result = update_daily_history(
            self.repo, date(2026, 8, 5), output_dir=self.daily_dir,
            now=datetime(2026, 8, 9, 10, 0).astimezone(),
        )

        self.assertEqual(result.added_event_ids, ("EVT-S", "EVT-N"))
        self.assertEqual(self._detector("EVT-S"), ())

    def test_it_does_not_get_in_under_its_own_power(self):
        """The half the alert is right about: with no companion, running the
        late update for that date changes nothing, because nothing puts the
        date into `kept_dates` in the first place. Asserted at the step
        below that, so the test does not depend on how the Runner builds
        `kept_dates`."""
        from daily import LateUpdateOutcome, update_daily_history

        self.repo.save(self._candidate("EVT-A", 10))
        from daily import generate_daily_history

        generate_daily_history(
            self.repo, date(2026, 8, 5), output_dir=self.daily_dir, generated_at="gen"
        )
        before = (self.daily_dir / "2026-08-05.md").read_text(encoding="utf-8")

        result = update_daily_history(
            self.repo, date(2026, 8, 5), output_dir=self.daily_dir,
            now=datetime(2026, 8, 9, 10, 0).astimezone(),
        )

        self.assertIs(result.outcome, LateUpdateOutcome.NO_LATE_EVENTS)
        self.assertEqual(
            (self.daily_dir / "2026-08-05.md").read_text(encoding="utf-8"), before
        )

    def test_the_runner_only_visits_dates_it_collected(self):
        """The premise, pinned where it lives. If step 6.5 ever grew a
        different date source this whole finding changes, and that should
        fail here rather than be discovered by reading the comment."""
        import ast
        import inspect

        from app import runner

        source = inspect.getsource(runner.run_once)
        tree = ast.parse(inspect.cleandoc(source))
        loops = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "kept_date"
        ]

        self.assertEqual(len(loops), 1, "step 6.5's loop moved or was renamed")
        self.assertEqual(
            ast.unparse(loops[0].iter), "sorted(kept_dates)",
            "step 6.5 no longer iterates the dates this run collected",
        )


class SameInstantSkipReachesTheOperatorTests(unittest.TestCase):
    """E-23's divergence, out of the Run Manifest and onto the screen.

    C40 made the count exist and nothing read it: `_print_last_run()` prints
    a component's metrics only when the component FAILED, and a same-instant
    skip is not a failure (docs/04 §35 "적용하지 않았다", `recorder.ok()`,
    exit 0). So `same_instant_skips` went to `last_run.json` on every
    affected run and no view has ever shown it.

    Measured through the real Runner and the real `ExecutionPlanSync`, with
    the two Events two timestamp-less Signals on one date actually produce
    (docs/06 §12 gives both that date's midnight):

        manifest        notion_sync SUCCESS, {'processed': 2,
                                              'same_instant_skips': 1}
        Notion row      Status IN_PROGRESS, Blocker (none), Last Event EVT-A
        on disk         BLOCKED on "예산 승인 대기"

    E-23 records the loss as "Notion 쪽 Current State의 **최신성**". The row
    being one Event behind is not the whole of it — it can show the opposite
    of the risk state, and this repository now has a second view (CONTROL
    TOWER) that reads the same Events and says BLOCKED, so the two disagree
    in public.

    Not a standing alarm: the metric is per run and absent on the next one,
    and the action named is the mitigation AGENT.md §3 already documents.
    """

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (runtime / rel).mkdir(parents=True)
        return runtime

    def _module(self, runtime):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_e23", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        module.DEFAULT_RUN_SUMMARY_PATH = runtime / "runs" / "last_run.json"
        return module

    def _manifest(self, runtime, metrics, *, status=ComponentStatus.SUCCESS):
        write_summary(
            runtime / "runs" / "last_run.json",
            RunSummary(
                run_id="RUN-E23",
                started_at="2026-08-03T11:00:00+09:00",
                finished_at="2026-08-03T11:00:02+09:00",
                components=(
                    ComponentResult(name="notion_sync", status=status, metrics=metrics),
                ),
            ),
        )

    def _run(self, runtime):
        import contextlib

        module = self._module(runtime)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_notion(NOW)
        return buffer.getvalue(), [a for a in attention if "같은 instant" in a or "instant" in a]

    def test_the_count_reaches_the_screen_and_attention(self):
        runtime = self._runtime()
        self._manifest(runtime, {"processed": 2, "same_instant_skips": 1})

        printed, lines = self._run(runtime)

        self.assertIn("같은 instant 미반영 : 1", printed)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("E-23", lines[0])
        self.assertIn("timestamp", lines[0])

    def test_an_ordinary_run_says_nothing(self):
        """`app/runner.py` writes the metric as `or None`, so a run where it
        did not happen carries no key at all — and this must stay silent for
        it rather than print a standing zero."""
        runtime = self._runtime()
        self._manifest(runtime, {"processed": 2})

        printed, lines = self._run(runtime)

        self.assertNotIn("instant", printed)
        self.assertEqual(lines, [])

    def test_a_zero_is_treated_as_nothing_to_say(self):
        runtime = self._runtime()
        self._manifest(runtime, {"processed": 2, "same_instant_skips": 0})

        self.assertEqual(self._run(runtime)[1], [])

    def test_no_manifest_is_not_an_error_here(self):
        """LAST RUN already reports a missing or unreadable manifest; a
        second line for the same file would be a second opinion."""
        runtime = self._runtime()

        self.assertEqual(self._run(runtime)[1], [])

    def test_an_unreadable_manifest_is_not_an_error_here_either(self):
        runtime = self._runtime()
        (runtime / "runs" / "last_run.json").write_bytes(b"{not json")

        self.assertEqual(self._run(runtime)[1], [])

    def test_a_damaged_metrics_container_does_not_take_the_block_down(self):
        """`read_summary()` validates the three enums and nothing else, so
        `metrics` comes back as whatever the file holds — the DR path C44
        already had to defend `_print_last_run()` against."""
        runtime = self._runtime()
        path = runtime / "runs" / "last_run.json"
        self._manifest(runtime, {"processed": 2, "same_instant_skips": 1})
        data = json.loads(path.read_text(encoding="utf-8"))
        data["components"][0]["metrics"] = "not a mapping"
        path.write_text(json.dumps(data), encoding="utf-8")

        printed, lines = self._run(runtime)

        self.assertEqual(lines, [])
        self.assertIn("NOTION", printed)

    def test_a_forged_metric_value_cannot_make_a_number_up(self):
        runtime = self._runtime()
        self._manifest(runtime, {"same_instant_skips": "9999"})

        self.assertEqual(self._run(runtime)[1], [])


class SameInstantSkipEndToEndTests(unittest.TestCase):
    """The whole chain, driven by the real Runner: two timestamp-less Signals
    on one date -> the Late Event guard -> the manifest metric -> the view.

    The fixture is not crafted. `agent/agent.py::_default_timestamp()` gives
    every Signal of a date that date's midnight *on purpose* (docs/06 §12), so
    two Signals for one project on one day is exactly this.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        self.local_master = self.runtime / "local_master"
        self.local_master.mkdir(parents=True)
        self.working_copy = self.runtime / "backup_working_copy"
        self.working_copy.mkdir(parents=True)
        self._init_git()
        self.incoming = self.runtime / "events" / "incoming"
        self.incoming.mkdir(parents=True)

    def _git(self, args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _init_git(self):
        bare = self.root / "remote.git"
        self._git(["init", "--bare", "-b", "main", str(bare)], self.root)
        self._git(["init", "-b", "main"], self.working_copy)
        self._git(["config", "user.email", "t@example.invalid"], self.working_copy)
        self._git(["config", "user.name", "E23"], self.working_copy)
        self._git(["remote", "add", "origin", str(bare)], self.working_copy)
        (self.working_copy / ".gitkeep").write_text("", encoding="utf-8")
        self._git(["add", "-A"], self.working_copy)
        self._git(["commit", "-m", "init"], self.working_copy)
        self._git(["push", "-u", "origin", "main"], self.working_copy)

    MIDNIGHT = "2026-08-01T00:00:00+09:00"

    def test_the_second_signal_of_a_day_diverges_and_is_now_reported(self):
        import contextlib
        import importlib.util

        from app.runner import run_once
        from events import create_event
        from notion import ExecutionPlanSync, InMemoryNotionTransport, NotionClient
        from reporter import Reporter

        for event_id, event_type, status, extra in (
            ("EVT-A", "STARTED", "IN_PROGRESS", {}),
            ("EVT-B", "BLOCKED", "BLOCKED", {"blocker": "예산 승인 대기"}),
        ):
            Reporter(profile="DESKTOP_2").report_and_write(
                directory=self.incoming, project_id="BRAND", event_type=event_type,
                status=status, summary=f"s {event_id}", history_candidate=True,
                timestamp=self.MIDNIGHT, event_id=event_id, evidence=[], **extra,
            )

        transport = InMemoryNotionTransport()
        run_once(
            local_master_dir=self.local_master,
            backup_working_copy_dir=self.working_copy,
            history_start_date=date(2026, 8, 1),
            runner_lock_path=self.runtime / "locks" / "l.lock",
            now=datetime(2026, 8, 3, 11, 0).astimezone(),
            transport_dir=self.runtime / "transport",
            incoming_dir=self.incoming,
            processed_dir=self.runtime / "events" / "processed",
            rejected_dir=self.runtime / "events" / "rejected",
            collector_log_path=self.runtime / "logs" / "collector.log",
            collector_state_path=self.runtime / "state" / "collector_state.json",
            notion_sync=ExecutionPlanSync(
                client=NotionClient(transport=transport, database_id="DB")
            ),
            notion_sync_log_path=self.runtime / "logs" / "notion_sync.log",
            late_update_log_path=self.runtime / "logs" / "daily_late_update.log",
            monthly_state_path=self.runtime / "state" / "monthly_history_state.json",
            run_summary_path=self.runtime / "runs" / "last_run.json",
            notion_retry_queue_path=self.runtime / "state" / "queue.json",
            keep_dir=self.runtime / "history_candidates" / "keep",
            review_dir=self.runtime / "history_candidates" / "review",
            scheduler_state_path=self.runtime / "state" / "scheduler.json",
            backup_state_path=self.runtime / "state" / "backup.json",
        )

        summary = read_summary(self.runtime / "runs" / "last_run.json")
        component = summary.component("notion_sync")

        # The run is a success — which is precisely why nothing showed this.
        self.assertEqual(summary.exit_code, 0)
        self.assertIs(component.status, ComponentStatus.SUCCESS)
        self.assertEqual(component.metrics.get("same_instant_skips"), 1)

        # The Notion row shows the FIRST Event, not the state on disk.
        page = [
            p for p in transport._pages.values()
            if "Project ID" in p.get("properties", {})
        ][0]
        properties = page["properties"]
        blocker = (properties.get("Blocker") or {}).get("rich_text") or []
        self.assertEqual(
            (properties["Status"]["select"] or {}).get("name"), "IN_PROGRESS"
        )
        self.assertEqual(blocker, [], "the Notion row shows no blocker")

        # ...while the Control Tower, reading the same Events, says BLOCKED.
        from controltower import build_company_rollup

        rollup = build_company_rollup(
            processed_dir=self.runtime / "events" / "processed",
            now=datetime(2026, 8, 3, 11, 0).astimezone(),
        )
        self.assertTrue(rollup.project("BRAND").is_blocked)
        self.assertEqual(rollup.project("BRAND").open_blocker, "예산 승인 대기")

        # ...and the operator is told, which is the part that was missing.
        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_e23_e2e", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime
        module.DEFAULT_RUN_SUMMARY_PATH = self.runtime / "runs" / "last_run.json"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_notion(datetime(2026, 8, 3, 12, 0).astimezone())

        self.assertIn("같은 instant 미반영 : 1", buffer.getvalue())
        self.assertTrue(any("E-23" in line for line in attention), attention)

    def test_one_second_apart_takes_no_such_path(self):
        """The mitigation AGENT.md §3 documents, held to its promise."""
        from app.runner import run_once
        from events import create_event
        from notion import ExecutionPlanSync, InMemoryNotionTransport, NotionClient
        from reporter import Reporter

        for event_id, event_type, status, stamp, extra in (
            ("EVT-A", "STARTED", "IN_PROGRESS", "2026-08-01T00:00:00+09:00", {}),
            ("EVT-B", "BLOCKED", "BLOCKED", "2026-08-01T00:00:01+09:00",
             {"blocker": "예산 승인 대기"}),
        ):
            Reporter(profile="DESKTOP_2").report_and_write(
                directory=self.incoming, project_id="BRAND", event_type=event_type,
                status=status, summary=f"s {event_id}", history_candidate=True,
                timestamp=stamp, event_id=event_id, evidence=[], **extra,
            )

        transport = InMemoryNotionTransport()
        run_once(
            local_master_dir=self.local_master,
            backup_working_copy_dir=self.working_copy,
            history_start_date=date(2026, 8, 1),
            runner_lock_path=self.runtime / "locks" / "l.lock",
            now=datetime(2026, 8, 3, 11, 0).astimezone(),
            transport_dir=self.runtime / "transport",
            incoming_dir=self.incoming,
            processed_dir=self.runtime / "events" / "processed",
            rejected_dir=self.runtime / "events" / "rejected",
            collector_log_path=self.runtime / "logs" / "collector.log",
            collector_state_path=self.runtime / "state" / "collector_state.json",
            notion_sync=ExecutionPlanSync(
                client=NotionClient(transport=transport, database_id="DB")
            ),
            notion_sync_log_path=self.runtime / "logs" / "notion_sync.log",
            late_update_log_path=self.runtime / "logs" / "daily_late_update.log",
            monthly_state_path=self.runtime / "state" / "monthly_history_state.json",
            run_summary_path=self.runtime / "runs" / "last_run.json",
            notion_retry_queue_path=self.runtime / "state" / "queue.json",
            keep_dir=self.runtime / "history_candidates" / "keep",
            review_dir=self.runtime / "history_candidates" / "review",
            scheduler_state_path=self.runtime / "state" / "scheduler.json",
            backup_state_path=self.runtime / "state" / "backup.json",
        )

        component = read_summary(
            self.runtime / "runs" / "last_run.json"
        ).component("notion_sync")

        self.assertIsNone(component.metrics.get("same_instant_skips"))
        page = [
            p for p in transport._pages.values()
            if "Project ID" in p.get("properties", {})
        ][0]
        blocker = (page["properties"].get("Blocker") or {}).get("rich_text") or []
        self.assertEqual(
            "".join(i["text"]["content"] for i in blocker), "예산 승인 대기"
        )


class HoleInTheDailySequenceTests(unittest.TestCase):
    """Days of Company History that were closed, had a file, and no longer
    do -- with every indicator reporting health.

    docs/07 §30 closes days in order and never skips, and
    `generate_daily_history()` writes a file for a day with no work too, so
    the Daily filenames must form an unbroken run of dates. A date sitting
    *between* two days that do have files is therefore a day whose file was
    removed.

    Measured on ten closed days with 08-04..08-06 deleted -- the shape a
    partial restore, a half-synced OneDrive folder, or a hand deletion
    (docs/06 §57 permits editing, and deleting is an edit) leaves:

        check_state_consistency()   CONSISTENT
        ATTENTION                   nothing about the three days
        Scheduler next run          starts at last_close + 1, never returns

    Three days gone, permanently, silently. `check_state_consistency()` is
    not wrong -- §47 asks it whether the *last* closed day has a file, and
    it does. Nothing had the interior in view.

    Only the interior. A missing suffix is what a run that failed part-way
    leaves, it is the normal retry shape, and the next run fills it.
    """

    def _runtime(self, present, *, backup=()):
        from scheduler.state import SchedulerState
        from scheduler.state import save_state as save_scheduler_state

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "backup_working_copy/daily", "state",
            "locks", "runs", "logs",
        ):
            (runtime / rel).mkdir(parents=True)
        for day in present:
            (runtime / "local_master" / "daily" / f"{day}.md").write_text(
                "history", encoding="utf-8"
            )
        for day in backup:
            (runtime / "backup_working_copy" / "daily" / f"{day}.md").write_text(
                "history", encoding="utf-8"
            )
        save_scheduler_state(
            runtime / "state" / "daily_history_state.json",
            SchedulerState(last_successful_daily_close=date(2026, 8, 10)),
        )
        return runtime

    def _run(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_holes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [a for a in attention if "시퀀스에 구멍" in a]

    ALL = tuple(f"2026-08-{d:02d}" for d in range(1, 11))

    def test_an_interior_hole_is_reported(self):
        present = [d for d in self.ALL if d not in ("2026-08-04", "2026-08-05")]

        printed, holes = self._run(self._runtime(present))

        self.assertEqual(len(holes), 1, holes)
        self.assertIn("2026-08-04", holes[0])
        self.assertIn("2026-08-05", holes[0])
        self.assertIn("Daily 시퀀스 구멍   : 2", printed)

    def test_the_consistency_check_still_says_consistent(self):
        """The reason this had to be its own check rather than an extension
        of that one: §47's question is answered correctly and the days are
        still gone."""
        from scheduler.consistency import ConsistencyStatus, check_state_consistency

        runtime = self._runtime([d for d in self.ALL if d != "2026-08-04"])

        result = check_state_consistency(
            runtime / "state" / "daily_history_state.json",
            runtime / "local_master" / "daily",
        )

        self.assertIs(result.status, ConsistencyStatus.CONSISTENT)
        self.assertEqual(len(self._run(runtime)[1]), 1)

    def test_a_complete_sequence_is_quiet(self):
        printed, holes = self._run(self._runtime(self.ALL))

        self.assertEqual(holes, [])
        self.assertNotIn("시퀀스 구멍", printed)

    def test_a_missing_suffix_is_not_a_hole(self):
        """A run that failed part-way leaves this, and the next run fills
        it. Reporting it would be a standing alert on the normal case."""
        printed, holes = self._run(
            self._runtime([d for d in self.ALL if d < "2026-08-09"])
        )

        self.assertEqual(holes, [])

    def test_a_single_day_is_not_a_hole(self):
        self.assertEqual(self._run(self._runtime(["2026-08-05"]))[1], [])

    def test_an_empty_tree_is_quiet(self):
        self.assertEqual(self._run(self._runtime([]))[1], [])

    def test_the_message_says_where_the_days_might_still_be(self):
        """A diagnosis an operator cannot act on is half a finding. The
        Backup Working Copy is already on disk and already listed for the
        un-backed check."""
        present = [d for d in self.ALL if d not in ("2026-08-04", "2026-08-05")]

        _printed, holes = self._run(
            self._runtime(present, backup=["2026-08-04"])
        )

        self.assertIn("Backup Working Copy에 아직 있다", holes[0])
        self.assertIn("2026-08-04", holes[0].split("아직 있다")[1])

    def test_it_says_so_when_the_backup_does_not_have_them_either(self):
        present = [d for d in self.ALL if d != "2026-08-04"]

        _printed, holes = self._run(self._runtime(present))

        self.assertIn("Backup Working Copy에도 없다", holes[0])

    def test_a_directory_wearing_a_days_name_counts_as_missing(self):
        """C31's rule across six other call sites: it exists, and it is not
        a day of Company History."""
        import importlib.util

        runtime = self._runtime([d for d in self.ALL if d != "2026-08-04"])
        (runtime / "local_master" / "daily" / "2026-08-04.md").mkdir()

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_holes_dir", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(
            module._holes_in_the_daily_sequence(
                runtime / "local_master" / "daily"
            ),
            ("2026-08-04",),
        )

    def test_non_date_and_staging_names_are_ignored(self):
        import importlib.util

        runtime = self._runtime(self.ALL)
        daily = runtime / "local_master" / "daily"
        (daily / "notes.md").write_text("a hand-written note", encoding="utf-8")
        (daily / ".tmp-abandoned.md").write_text("residue", encoding="utf-8")

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_holes_odd", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module._holes_in_the_daily_sequence(daily), ())
        self.assertEqual(len(module._daily_dates(daily)), 10)


class HistoryGoneFromLocalMasterTests(unittest.TestCase):
    """The days the hole check cannot bound: a missing **prefix**.

    `_holes_in_the_daily_sequence()` bounds its range by the files that are
    present and says so plainly — *"whatever came before it is outside this
    machine's History"*. When the days that went missing are the earliest
    ones, the first present file simply moves forward and the range moves
    with it, so there is no interior gap to find. A partial restore that
    stopped part-way, a OneDrive folder that synced from the top, and a hand
    deletion of "the old ones" all leave exactly that shape.

    Measured through the real Runner with `2026-08-01.md` replaced by a
    **directory of the same name** (C31's shape, and what a half-finished
    copy leaves), 08-01..08-04 closed:

        _holes_in_the_daily_sequence()      ()
        _kept_but_not_rendered()            ()
        check_state_consistency()           CONSISTENT
        _daily_counts_more_than_it_shows()  ()
        _misnamed_scope_directories()       ()
        daily 파일                          5      <- counting the directory
        ATTENTION                           nothing naming 2026-08-01

    Backup does fail — its deletion gate sees the same thing — but the
    filename lives only in the manifest's `reason`, which
    `_print_last_run()` deliberately does not print, so what an operator
    reads is `backup: BACKUP_FAILED`: the same line a credential failure
    produces, which is exactly the confusion the Runner's comment beside
    that gate says it was written to remove.

    The answer needs no configuration. `sync_to_working_copy()` writes one
    direction and never deletes — a detected deletion makes it apply nothing
    at all (docs/08 §31/§44-47) — so the Working Copy is a monotonic record
    of every file that ever reached backup scope, and a name in it that
    Master does not have is a file that existed and does not now. The
    comparison reuses the gate's own listing (`_relative_files`) rather than
    a second `glob()`, so "Company History" means the same thing on both
    sides.
    """

    def _module(self, runtime):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_gone", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        return module

    def _runtime(self, *, master=(), backup=(), monthly_master=(), monthly_backup=()):
        from scheduler.state import SchedulerState
        from scheduler.state import save_state as save_scheduler_state

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "backup_working_copy/daily",
            "backup_working_copy/monthly", "state", "locks", "runs", "logs",
        ):
            (runtime / rel).mkdir(parents=True)
        for where, days in (
            (runtime / "local_master" / "daily", master),
            (runtime / "backup_working_copy" / "daily", backup),
        ):
            for day in days:
                (where / f"{day}.md").write_text("history", encoding="utf-8")
        for where, months in (
            (runtime / "local_master" / "monthly", monthly_master),
            (runtime / "backup_working_copy" / "monthly", monthly_backup),
        ):
            for month in months:
                (where / f"{month}.md").write_text("history", encoding="utf-8")
        save_scheduler_state(
            runtime / "state" / "daily_history_state.json",
            SchedulerState(last_successful_daily_close=date(2026, 8, 10)),
        )
        return runtime

    def _gone(self, runtime):
        module = self._module(runtime)
        return module._history_gone_from_local_master(
            runtime / "local_master", runtime / "backup_working_copy"
        )

    def _attention(self, runtime):
        import contextlib

        module = self._module(runtime)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)
        return buffer.getvalue(), [a for a in attention if "Local Master에는 없는" in a]

    ALL = tuple(f"2026-08-{d:02d}" for d in range(1, 11))

    def test_a_missing_prefix_is_reported(self):
        """The case the hole check cannot see at all."""
        runtime = self._runtime(master=self.ALL[2:], backup=self.ALL)

        module = self._module(runtime)
        daily = runtime / "local_master" / "daily"

        self.assertEqual(module._holes_in_the_daily_sequence(daily), ())
        self.assertEqual(
            self._gone(runtime),
            (str(Path("daily") / "2026-08-01.md"), str(Path("daily") / "2026-08-02.md")),
        )

    def test_a_directory_wearing_a_days_name_is_reported_as_gone(self):
        runtime = self._runtime(master=self.ALL, backup=self.ALL)
        target = runtime / "local_master" / "daily" / "2026-08-01.md"
        target.unlink()
        target.mkdir()

        self.assertEqual(self._gone(runtime), (str(Path("daily") / "2026-08-01.md"),))

    def test_the_attention_line_names_the_file_and_says_backup_is_blocked(self):
        runtime = self._runtime(master=self.ALL[1:], backup=self.ALL)

        printed, lines = self._attention(runtime)

        self.assertEqual(len(lines), 1, lines)
        self.assertIn("2026-08-01.md", lines[0])
        self.assertIn("docs/08 §31", lines[0])
        self.assertIn("Master에서 사라짐   : 1", printed)

    def test_a_monthly_file_counts_too(self):
        """Backup scope is `daily/` **and** `monthly/` (docs/08 §26), and the
        Monthly hole check has the same blind prefix."""
        runtime = self._runtime(
            master=self.ALL, backup=self.ALL,
            monthly_master=("2026-08",), monthly_backup=("2026-07", "2026-08"),
        )

        self.assertEqual(self._gone(runtime), (str(Path("monthly") / "2026-07.md"),))

    def test_a_master_that_is_ahead_of_the_backup_is_quiet(self):
        """The ordinary state between a run and its backup: Master has more,
        never less. That is `_history_newer_than_the_last_backup()`'s
        question, not this one."""
        runtime = self._runtime(master=self.ALL, backup=self.ALL[:5])

        self.assertEqual(self._gone(runtime), ())
        self.assertEqual(self._attention(runtime)[1], [])

    def test_an_identical_pair_is_quiet(self):
        runtime = self._runtime(master=self.ALL, backup=self.ALL)

        self.assertEqual(self._gone(runtime), ())

    def test_a_machine_with_no_working_copy_yet_is_quiet(self):
        """A fresh install, and a deployment that has never configured
        Backup. Neither is a loss, and a standing alert on either would be
        the alert-that-cannot-clear this file keeps warning about."""
        runtime = self._runtime(master=self.ALL)
        shutil.rmtree(runtime / "backup_working_copy")

        self.assertEqual(self._gone(runtime), ())
        self.assertEqual(self._attention(runtime)[1], [])

    def test_staging_residue_in_the_working_copy_is_not_company_history(self):
        """`.tmp-*.md` is a write that never committed. The gate's own
        listing excludes it on both sides, which is why this reuses it."""
        runtime = self._runtime(master=self.ALL, backup=self.ALL)
        (runtime / "backup_working_copy" / "daily" / ".tmp-abandoned.md").write_text(
            "residue", encoding="utf-8"
        )

        self.assertEqual(self._gone(runtime), ())

    def test_out_of_scope_files_in_the_working_copy_are_ignored(self):
        """git puts things in the Working Copy that Company History does not
        own — `.gitkeep`, a README. Backup scope is `daily/` and `monthly/`
        only (docs/08 §26), and the shared listing is what enforces it."""
        runtime = self._runtime(master=self.ALL, backup=self.ALL)
        (runtime / "backup_working_copy" / ".gitkeep").write_text("", encoding="utf-8")
        (runtime / "backup_working_copy" / "notes").mkdir()
        (runtime / "backup_working_copy" / "notes" / "x.md").write_text("n", encoding="utf-8")

        self.assertEqual(self._gone(runtime), ())

    def test_the_detector_agrees_with_the_gate_that_will_block_the_backup(self):
        """Not a second opinion: `sync_to_working_copy()`'s `deleted` is the
        same set difference, and a run would refuse on exactly these names."""
        from backup.working_copy import sync_to_working_copy

        runtime = self._runtime(master=self.ALL[2:], backup=self.ALL)

        result = sync_to_working_copy(
            runtime / "local_master", runtime / "backup_working_copy"
        )

        self.assertEqual(tuple(sorted(result.deleted)), self._gone(runtime))


class DailyAndMonthlyCountsExcludeDirectoriesTests(unittest.TestCase):
    """`daily 파일` / `monthly 파일` counted anything named `*.md`.

    Every other reader of these two directories already asks `is_file()` —
    `_daily_dates()` ("it exists, and it is not a day of Company History"),
    `_holes_in_the_monthly_sequence()`, and `working_copy._relative_files()`.
    These two counts were the last without it, which
    `_misnamed_scope_directories()`'s docstring already noted in passing.

    Measured with `2026-08-01.md` replaced by a directory: `daily 파일 : 5`
    for four days of Company History, printed one line above
    `daily state 정합성 : CONSISTENT` — a count that disagreed with the
    detector beneath it, in the direction that hides a loss.
    """

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (runtime / rel).mkdir(parents=True)
        return runtime

    def _printed(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_counts", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module._print_history(NOW)
        return buffer.getvalue()

    def test_a_directory_named_like_a_day_is_not_counted(self):
        runtime = self._runtime()
        daily = runtime / "local_master" / "daily"
        for day in ("2026-08-02", "2026-08-03"):
            (daily / f"{day}.md").write_text("history", encoding="utf-8")
        (daily / "2026-08-01.md").mkdir()

        self.assertIn("daily 파일          : 2", self._printed(runtime))

    def test_a_directory_named_like_a_month_is_not_listed(self):
        runtime = self._runtime()
        monthly = runtime / "local_master" / "monthly"
        (monthly / "2026-08.md").write_text("history", encoding="utf-8")
        (monthly / "2026-07.md").mkdir()

        printed = self._printed(runtime)

        self.assertIn("monthly 파일        : 1", printed)
        self.assertNotIn("2026-07", printed)

    def test_ordinary_files_are_still_counted(self):
        runtime = self._runtime()
        daily = runtime / "local_master" / "daily"
        for day in ("2026-08-01", "2026-08-02", "2026-08-03"):
            (daily / f"{day}.md").write_text("history", encoding="utf-8")
        (runtime / "local_master" / "monthly" / "2026-08.md").write_text("m", encoding="utf-8")

        printed = self._printed(runtime)

        self.assertIn("daily 파일          : 3", printed)
        self.assertIn("monthly 파일        : 1", printed)

    def test_the_count_agrees_with_the_detector_printed_beneath_it(self):
        """The property that was broken: two numbers in one block disagreeing
        about how many days of Company History exist."""
        import importlib.util

        runtime = self._runtime()
        daily = runtime / "local_master" / "daily"
        for day in ("2026-08-01", "2026-08-02", "2026-08-03"):
            (daily / f"{day}.md").write_text("history", encoding="utf-8")
        (daily / "2026-08-04.md").mkdir()

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_agree", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertIn("daily 파일          : 3", self._printed(runtime))
        self.assertEqual(len(module._daily_dates(daily)), 3)


class ADetectorSaysWhatItCouldNotCheckTests(unittest.TestCase):
    """C68. Three detectors on this screen went quiet when a read failed.

    C62 established the rule for evidence — an entry dropped in silence is
    worse than one reported — and C68 §1 applied it to Company History
    coverage. Counting the `except OSError: continue` handlers outside `src/`
    afterwards found ten more in this file, and three of them are
    **detectors**:

        _history_newer_than_the_last_backup   a file missing from the
                                              "not backed up" list
        _junctions_in_scope                   a junction missing from the
                                              exposure list
        _monthly_lags_its_daily_source        a day whose mtime falls back to
                                              0.0, which makes "the Daily is
                                              newer than the Monthly" false

    All three fail in the same direction: the list gets **shorter**. A
    shorter list of problems is indistinguishable from a healthier machine,
    which is the one failure mode a detector must not have.

    Each now returns `(result, skipped)` and each caller says the count out
    loud. Measured: the Monthly check went from `0 found` (silently assuming
    the Monthly was current) to `0 found, 1 skipped`.

    **What was already right, and is not changed here.** The candidate list
    in `_history_newer_than_the_last_backup()` calls `is_file()` unguarded,
    so a permission error there propagates — and `_block()` catches it and
    prints "HISTORY 블록을 읽지 못했다 … 이 섹션의 상태는 이번 출력에 없다".
    That is loud and correct; it was measured before assuming otherwise. The
    guard added inside the loop covers the narrower case that survives it: a
    file that passes `is_file()` and is gone by the time its mtime is read.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.local_master = self.root / "local_master"
        self.daily = self.local_master / "daily"
        self.monthly = self.local_master / "monthly"
        self.daily.mkdir(parents=True)
        self.monthly.mkdir(parents=True)
        (self.daily / "2026-07-01.md").write_text("# H\n", encoding="utf-8")
        (self.monthly / "2026-07.md").write_text("# M\n", encoding="utf-8")

    def _module(self):
        import ops_status

        return ops_status

    def test_the_monthly_check_counts_a_day_it_could_not_stat(self):
        """The one measured end to end. Without the count, a day with no
        mtime takes the `0.0` default and argues the Monthly is current."""
        module = self._module()
        real = os.scandir

        class Refusing:
            def __init__(self, entry):
                self._entry = entry
                self.name = entry.name

            def stat(self, *args, **kwargs):
                raise PermissionError(13, "Access is denied")

            def is_file(self, *args, **kwargs):
                return self._entry.is_file(*args, **kwargs)

        def wrap(path, *args, **kwargs):
            entries = real(path, *args, **kwargs)
            if str(path) == str(self.daily):
                return iter([Refusing(entry) for entry in entries])
            return entries

        os.scandir = wrap
        try:
            lagging, skipped = module._monthly_lags_its_daily_source(
                self.daily, self.monthly, dirty_months=()
            )
        finally:
            os.scandir = real

        self.assertEqual(lagging, ())
        self.assertEqual(skipped, 1)

    def test_the_junction_check_counts_a_probe_that_refused(self):
        """A probe that *refuses* — distinct from one that is absent.

        Injected rather than provoked, and that is not a limitation of the
        interpreter: a real `isjunction()` does not raise `PermissionError`
        on demand, so there is no way to reach this branch without standing
        one in. (C76 note: it used to say the real probe was absent on this
        interpreter. On 3.13.14 it is present — which changes why the
        injection is needed, not whether it is.)"""
        module = self._module()

        def refuse(path):
            raise PermissionError(13, "Access is denied")

        with mock.patch.object(os.path, "isjunction", refuse, create=True):
            found, skipped = module._junctions_in_scope(self.local_master)

        self.assertEqual(found, ())
        self.assertGreater(skipped, 0)

    def test_an_absent_subject_is_not_a_skipped_check(self):
        """The asymmetry, and the reason `skipped` is a count rather than a
        flag: "there is nothing to look at" must not read as "I failed to
        look", or every undeployed machine grows three permanent caveats."""
        module = self._module()
        missing = self.root / "not_deployed"

        found, skipped = module._junctions_in_scope(missing)
        self.assertEqual((found, skipped), ((), 0))

        lagging, skipped = module._monthly_lags_its_daily_source(
            missing / "daily", missing / "monthly", dirty_months=()
        )
        self.assertEqual((lagging, skipped), ((), 0))

        unbacked, skipped = module._history_newer_than_the_last_backup(missing, None)
        self.assertEqual((list(unbacked), skipped), ([], 0))

    def test_a_healthy_tree_reports_no_skips(self):
        """The control. A guard that counted every entry would make all three
        detectors permanently caveated, which is the standing alarm this file
        keeps removing."""
        module = self._module()

        _found, junction_skips = module._junctions_in_scope(self.local_master)
        _lagging, monthly_skips = module._monthly_lags_its_daily_source(
            self.daily, self.monthly, dirty_months=()
        )
        _unbacked, backup_skips = module._history_newer_than_the_last_backup(
            self.local_master, None
        )

        self.assertEqual((junction_skips, monthly_skips, backup_skips), (0, 0, 0))

    def test_the_screen_says_it_rather_than_shortening_a_list_in_silence(self):
        """The half a return value cannot do.

        Each caller prints the count. Without that the fix is invisible: the
        list is the same length it would have been, and the operator has no
        way to know it is short.
        """
        module = self._module()
        runtime = self.root / "runtime"
        (runtime / "events" / "processed").mkdir(parents=True)
        shutil.copytree(self.local_master, runtime / "local_master")

        real = os.scandir

        class Refusing:
            def __init__(self, entry):
                self._entry = entry
                self.name = entry.name

            def stat(self, *args, **kwargs):
                raise PermissionError(13, "Access is denied")

            def is_file(self, *args, **kwargs):
                return self._entry.is_file(*args, **kwargs)

        class RefusingScandir:
            """Mimics `os.scandir`'s iterator, `close()` included.

            The first draft returned a bare `iter([...])` and
            `_company_history_older_than_the_evidence()`'s listability probe
            called `.close()` on it. The double was the wrong shape, not the
            code — the same lesson C64 recorded about the Notion double.
            """

            def __init__(self, entries):
                self._entries = iter(entries)

            def __iter__(self):
                return self._entries

            def __next__(self):
                return next(self._entries)

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def wrap(path, *args, **kwargs):
            entries = real(path, *args, **kwargs)
            if str(path) == str(runtime / "local_master" / "daily"):
                return RefusingScandir([Refusing(entry) for entry in entries])
            return entries

        def render():
            buffer = io.StringIO()
            previous = module.RUNTIME_DIR
            module.RUNTIME_DIR = runtime
            try:
                with contextlib.redirect_stdout(buffer):
                    try:
                        module.main()
                    except SystemExit:
                        pass
            finally:
                module.RUNTIME_DIR = previous
            return buffer.getvalue()

        healthy = render()
        os.scandir = wrap
        try:
            broken = render()
        finally:
            os.scandir = real

        self.assertNotIn("확인 못 함", healthy)
        self.assertIn("확인 못 함", broken)


class TheTwoRenderChecksNameTheDailyTheyCouldNotReadTests(unittest.TestCase):
    """C92 (A-28): the last two of the three the C91 AST sweep found.

    `_kept_but_not_rendered()` is the detector for E-17 -- a KEEP Candidate
    stored as Company History and absent from the Daily file of the day it
    belongs to. `_reviewed_but_not_rendered()` is the same shape for
    Decision Context, which its own docstring calls the most expensive
    content this pipeline handles because a human wrote it.

    Both walked the dates, and both answered an unreadable Daily with a bare
    `continue`. Every Candidate of that date was then treated exactly like a
    Candidate that IS in its file: the stranded list came back shorter by
    precisely the ones nobody could check, and a shorter list of losses is
    what a healthier machine looks like.

    Measured -- one KEEP Candidate genuinely missing from its Daily:

        control, the Daily is readable    stranded ('EVT-LOST (2026-08-05)',)
        the Daily cannot be decoded       stranded ()

    Both now return `(stranded, unreadable_dates)`, and the caller prints one
    line naming the dates. **One line for both**, because it is one fact
    about one set of files: the two detectors walk the same dates over the
    same directory, so two independent counters would report the same file
    twice, and a second opinion on "which files could not be read" is what
    C28 keeps out of this module. A union of dates cannot double-count; a sum
    of two counts can, and `test_one_unreadable_file_is_one_line` is that
    difference stated as a number.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.daily = self.root / "daily"
        self.daily.mkdir(parents=True)
        self.when = date(2026, 8, 5)

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c92", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _write_daily(self, *event_ids):
        """The real renderer, so "rendered" means what the renderer means."""
        from daily.markdown import render_daily_markdown
        from history import HistoryCandidate, HistoryDecision

        candidates = [
            HistoryCandidate(
                history_id=f"HIST-{event_id}",
                event_id=event_id,
                timestamp="2026-08-05T10:00:00+09:00",
                category="MILESTONE",
                project_id="PRJ_A",
                role="COO",
                summary="did work",
                evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
            for event_id in event_ids
        ]
        (self.daily / f"{self.when.isoformat()}.md").write_text(
            render_daily_markdown(self.when, candidates, "gen"), encoding="utf-8"
        )

    def _corrupt_daily(self):
        (self.daily / f"{self.when.isoformat()}.md").write_bytes(b'\xff\xfe\x00 not utf-8 \xff')

    def _stored(self, module, event_id, reviewed=()):
        return (
            module.StoredCandidate(
                stem="s", event_id=event_id, when=self.when, reviewed=reviewed
            ),
        )

    # ---- _kept_but_not_rendered ------------------------------------------

    def test_the_control_finds_the_stranded_candidate(self):
        """Without this the rest proves nothing."""
        module = self._module()
        self._write_daily("EVT-PRESENT")

        stranded, unreadable = module._kept_but_not_rendered(
            self._stored(module, "EVT-LOST"), self.daily
        )

        self.assertEqual(stranded, ("EVT-LOST (2026-08-05)",))
        self.assertEqual(unreadable, ())

    def test_an_unreadable_daily_loses_the_finding_and_is_named(self):
        module = self._module()
        self._corrupt_daily()

        stranded, unreadable = module._kept_but_not_rendered(
            self._stored(module, "EVT-LOST"), self.daily
        )

        self.assertEqual(stranded, ())  # the loss is genuinely invisible
        self.assertEqual(unreadable, ("2026-08-05",))  # and the date is named

    # ---- _reviewed_but_not_rendered --------------------------------------

    def test_the_review_control_finds_the_missing_decision_context(self):
        module = self._module()
        self._write_daily("EVT-A")
        reviewed = (("Decision Context", "Board asked for 4 weeks."),)

        stranded, unreadable = module._reviewed_but_not_rendered(
            self._stored(module, "EVT-A", reviewed), self.daily
        )

        self.assertTrue(stranded)
        self.assertEqual(unreadable, ())

    def test_an_unreadable_daily_loses_the_review_finding_and_is_named(self):
        module = self._module()
        self._corrupt_daily()
        reviewed = (("Decision Context", "Board asked for 4 weeks."),)

        stranded, unreadable = module._reviewed_but_not_rendered(
            self._stored(module, "EVT-A", reviewed), self.daily
        )

        self.assertEqual(stranded, ())
        self.assertEqual(unreadable, ("2026-08-05",))

    # ---- what must NOT be named ------------------------------------------

    def test_a_day_not_yet_rendered_is_not_an_unreadable_day(self):
        """The Scheduler window. A date whose Daily file does not exist yet
        will carry its Candidate when the day is closed -- both functions
        already treat that as "not a loss", and it must not become "a file I
        failed to read" either, or every machine reports the current day."""
        module = self._module()  # no Daily file written at all

        for name in ("_kept_but_not_rendered", "_reviewed_but_not_rendered"):
            with self.subTest(function=name):
                reviewed = (("Decision Context", "x"),)
                stranded, unreadable = getattr(module, name)(
                    self._stored(module, "EVT-A", reviewed), self.daily
                )
                self.assertEqual((stranded, unreadable), ((), ()))

    def test_an_absent_directory_is_an_absent_subject(self):
        """C68's asymmetry, which both early returns had to learn too."""
        module = self._module()
        missing = self.root / "not_deployed"

        for name in ("_kept_but_not_rendered", "_reviewed_but_not_rendered"):
            with self.subTest(function=name):
                self.assertEqual(
                    getattr(module, name)(self._stored(module, "EVT-A"), missing),
                    ((), ()),
                )

    # ---- one file, one line ----------------------------------------------

    def test_one_unreadable_file_is_one_line(self):
        """The reason the caller unions rather than adds.

        Both detectors walk the same dates over the same directory, so one
        unreadable Daily is reported by both. Adding the two counts would
        tell the operator two files could not be read when one could not.
        """
        module = self._module()
        self._corrupt_daily()
        reviewed = (("Decision Context", "x"),)
        stored = self._stored(module, "EVT-A", reviewed)

        _kept, from_keep = module._kept_but_not_rendered(stored, self.daily)
        _review, from_review = module._reviewed_but_not_rendered(stored, self.daily)

        self.assertEqual(len(from_keep) + len(from_review), 2)  # the hazard
        self.assertEqual(len(set(from_keep) | set(from_review)), 1)  # the fix

    def test_the_caller_unions_the_two_rosters(self):
        """...and that the caller really does take the union. By AST: a
        `+` between the two would pass any test that only reads one of
        them."""
        import ast

        tree = ast.parse(
            (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
                encoding="utf-8"
            )
        )
        printer = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_print_history"
        )
        union = next(
            node
            for node in ast.walk(printer)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "unreadable_daily"
                for target in node.targets
            )
        )
        operators = {
            type(node.op).__name__
            for node in ast.walk(union)
            if isinstance(node, ast.BinOp)
        }

        self.assertIn("BitOr", operators)
        self.assertNotIn("Add", operators)


class TheMonthlyLagVerdictCountsEveryReadItLostTests(unittest.TestCase):
    """C91: C68 built the `skipped` counter for one read and left three.

    `ADetectorSaysWhatItCouldNotCheckTests` above states the principle -- a
    detector whose list gets shorter on a failed read is indistinguishable
    from a healthier machine -- and applies it to the `st_mtime` loop in
    `_monthly_lags_its_daily_source()`. Three further reads in that same
    function could each fail and still return a verdict:

        the Monthly's `is_file()`/`stat()`   -> the month is never compared
        the Monthly's `read_text()`          -> the month is never compared
        `read_daily_document()` per day      -> that day's ids never enter
                                                `source_ids`, so `missing`
                                                is computed against a
                                                SHORTER source

    The third is the one that does not merely skip a month: it returns a
    finding for the month while having read less than all of it.

    Measured against a tree whose 2026-07 Monthly genuinely lacks an Event
    its Daily carries:

        control, everything readable      finding ('E-LATE',)   skipped 0
        the Daily carrying it is corrupt  finding ()            skipped 0
        the Monthly itself is corrupt     finding ()            skipped 0
        the Monthly cannot be stat-ed     finding ()            skipped 0

    `0 found, 0 skipped` is the screen a healthy machine prints, and all
    three printed it about a month with a real hole in it.

    Counting does not find the hole and is not meant to. It stops the answer
    from claiming a completeness it does not have -- the caller already
    prints the count, so it reaches the operator the moment it is non-zero.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.daily = self.root / "daily"
        self.monthly = self.root / "monthly"
        self.daily.mkdir(parents=True)
        self.monthly.mkdir(parents=True)
        self._write_day(date(2026, 7, 29), "E-OK")
        self._write_day(date(2026, 7, 30), "E-LATE")
        # Consolidated before 07-30 was edited: it carries E-OK only.
        (self.monthly / "2026-07.md").write_text(
            "# 2026-07\n\n- Event ID: E-OK\n", encoding="utf-8"
        )
        self._age(self.monthly / "2026-07.md", 1_700_000_000)
        self._age(self.daily / "2026-07-29.md", 1_700_000_000)
        # Newer than the Monthly, so the mtime prefilter lets the month through.
        self._age(self.daily / "2026-07-30.md", 1_800_000_000)

    def _write_day(self, day, *event_ids):
        """Rendered by the real renderer, so the real parser can read it."""
        from daily.markdown import render_daily_markdown
        from history import HistoryCandidate, HistoryDecision

        candidates = [
            HistoryCandidate(
                history_id=f"HIST-{event_id}",
                event_id=event_id,
                timestamp="2026-07-30T10:00:00+09:00",
                category="MILESTONE",
                project_id="PRJ_A",
                role="COO",
                summary="did work",
                evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
            for event_id in event_ids
        ]
        (self.daily / f"{day.isoformat()}.md").write_text(
            render_daily_markdown(day, candidates, "gen"), encoding="utf-8"
        )

    @staticmethod
    def _age(path, stamp):
        os.utime(path, (stamp, stamp))

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c91", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run(self):
        return self._module()._monthly_lags_its_daily_source(
            self.daily, self.monthly, dirty_months=()
        )

    # ---- the control: the hole is real and is found ----------------------

    def test_the_hole_is_found_when_everything_is_readable(self):
        """Without this the rest proves nothing: a detector that never fires
        would satisfy every assertion below."""
        lagging, skipped = self._run()

        self.assertEqual(lagging, (("2026-07", ("E-LATE",)),))
        self.assertEqual(skipped, 0)

    # ---- the three reads C68 did not cover -------------------------------

    def test_a_corrupt_daily_shortens_the_finding_and_is_counted(self):
        """The one that returns a verdict for a month it did not fully read.

        `source_ids` loses E-LATE, so `missing` is empty and the month looks
        current. The count is the only thing standing between that and
        "2026-07 is fine".
        """
        (self.daily / "2026-07-30.md").write_bytes(b'\xff\xfe\x00 not utf-8 \xff')
        self._age(self.daily / "2026-07-30.md", 1_800_000_000)

        lagging, skipped = self._run()

        self.assertEqual(lagging, ())  # the finding is genuinely lost
        self.assertEqual(skipped, 1)  # and the answer says so

    def test_a_corrupt_monthly_is_counted(self):
        (self.monthly / "2026-07.md").write_bytes(b'\xff\xfe\x00 not utf-8 \xff')
        self._age(self.monthly / "2026-07.md", 1_700_000_000)

        lagging, skipped = self._run()

        self.assertEqual(lagging, ())
        self.assertEqual(skipped, 1)

    def test_a_monthly_that_cannot_be_stat_ed_is_counted(self):
        """Injected: a real file does not refuse `stat()` on demand, exactly
        as `ADetectorSaysWhatItCouldNotCheckTests` reasons about its own
        junction probe."""
        module = self._module()
        real_stat = Path.stat

        def refusing(self, *args, **kwargs):
            if self.name == "2026-07.md":
                raise PermissionError(13, "Access is denied")
            return real_stat(self, *args, **kwargs)

        with mock.patch.object(Path, "stat", refusing):
            lagging, skipped = module._monthly_lags_its_daily_source(
                self.daily, self.monthly, dirty_months=()
            )

        self.assertEqual(lagging, ())
        self.assertEqual(skipped, 1)

    # ---- the asymmetry the count must not lose ---------------------------

    def test_an_unconsolidated_month_is_not_a_skipped_check(self):
        """C68's asymmetry, restated where it is now easiest to break: a
        month with no Monthly file has nothing to lag behind. Counting it
        would put a permanent caveat on every machine whose current month is
        not closed yet."""
        (self.monthly / "2026-07.md").unlink()

        self.assertEqual(self._run(), ((), 0))

    def test_a_day_with_no_history_is_not_a_skipped_check(self):
        """`render_daily_markdown()` writes "No material company history
        recorded." for a day with no candidates, and the parser reads it as
        zero items rather than raising. If it raised, every ordinary quiet
        day would count as a failed read -- which is the standing alarm this
        file keeps removing."""
        self._write_day(date(2026, 7, 28))  # no candidates at all
        self._age(self.daily / "2026-07-28.md", 1_700_000_000)

        lagging, skipped = self._run()

        self.assertEqual(lagging, (("2026-07", ("E-LATE",)),))
        self.assertEqual(skipped, 0)

    # ---- the drift that hid it -------------------------------------------

    def test_both_c68_detectors_declare_the_pair_they_return(self):
        """C68 changed these functions to return `(result, skipped)` and left
        their annotations describing the old single value. An annotation that
        disagrees with the `return` is how a reader concludes there is no
        count to check -- which is how three uncounted reads sat in the
        middle of the one function that has a counter.

        By AST rather than by text: the annotation is a nested type
        expression, and matching it as a string is the mistake this session
        has already corrected three times.
        """
        import ast

        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }

        for name in ("_monthly_lags_its_daily_source", "_junctions_in_scope"):
            with self.subTest(function=name):
                node = functions[name]
                self.assertIsNotNone(node.returns, f"{name} declares no return type")
                self.assertIsInstance(node.returns, ast.Subscript)
                elements = node.returns.slice.elts
                self.assertEqual(len(elements), 2, f"{name} does not declare a pair")
                self.assertEqual(ast.unparse(elements[1]), "int")

                # ...and the function really does return two things.
                returns = [
                    child
                    for child in ast.walk(node)
                    if isinstance(child, ast.Return) and child.value is not None
                ]
                self.assertTrue(returns)
                for statement in returns:
                    self.assertIsInstance(statement.value, ast.Tuple)
                    self.assertEqual(len(statement.value.elts), 2)


class MonthlyLagsItsDailySourceTests(unittest.TestCase):
    """The third link in the chain, and the only one that crosses files.

        Daily files (the source)  ->  Consolidated Items  ->  rendered items

    `_monthly_counts_more_than_it_shows()` compares the second against the
    third, inside one file. Nothing compared the first against anything —
    and docs/09 §12-13 makes that the comparison that matters, because
    Monthly is derived *wholly* from the Daily files.

    Measured through the real Runner, with an edit two specs explicitly
    permit (docs/06 §57, docs/11 §71): July consolidated on 08-03, one item
    added to `2026-07-30.md` by hand afterwards —

        run 08-04   exit 0   Monthly has it: False
        run 08-05   exit 0   Monthly has it: False
        ATTENTION            nothing naming it

    and nothing ever revisits that month. `pending_months()` starts *after*
    `last_successful_monthly_close`, and the only thing that reopens a closed
    month is `mark_month_dirty()`, which the Runner calls for the dates a
    **Late Event** changed — not for a date a person changed. That Late Event
    path is correct and is pinned below unchanged; what had no route back is
    the hand edit.
    """

    def _module(self, runtime=None):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_lag", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if runtime is not None:
            module.RUNTIME_DIR = runtime
        return module

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.daily = self.root / "daily"
        self.monthly = self.root / "monthly"
        self.daily.mkdir(parents=True)
        self.monthly.mkdir(parents=True)
        # mtime is the prefilter, so every test places the two sides in
        # time on purpose rather than relying on the order they were written.
        self.base = time.time()

    DAILY = """# DOJOONPASS Company History — {day}

## Summary

{summary}

## Milestones

### OPS

- {summary}
- Owner: COO
- Event ID: {event_id}

## Metadata

- History Date: {day}
- Generated At: 2026-07-31T09:00:00+09:00
- Source: DOJOONPASS Company Ops
- Event Count: 1
"""

    EMPTY_DAILY = """# DOJOONPASS Company History — {day}

No material company history recorded.

## Metadata

- History Date: {day}
- Generated At: 2026-07-31T09:00:00+09:00
- Source: DOJOONPASS Company Ops
- Event Count: 0
"""

    MONTHLY = """# DOJOONPASS Company History — 2026-07

## Major Decisions

## Milestones

{items}

## Source Records

- 2026-07-01.md

## Metadata

- History Month: 2026-07
- Generated At: 2026-08-01T09:00:00+09:00
- Source: DOJOONPASS Company Ops
- Daily Coverage: COMPLETE (1/1 days, 2026-07-01 ~ 2026-07-01)
- Consolidated Items: {count}
"""

    MONTHLY_ITEM = """### OPS

- {summary}
- Owner: COO
- Event ID: {event_id}
- Source: {day}.md
"""

    # Built from a list rather than one literal: the escapes belong to the
    # document, not to this file.
    LATE_SECTION = chr(10).join([
        "## Late Events",
        "",
        "### OPS",
        "",
        "- late work",
        "- Owner: COO",
        "- Event ID: EVT-LATE",
        "- Category: MILESTONE",
        "",
        "## Metadata",
    ])

    def _write_day(self, day, event_id=None, summary="work", *, newer=False):
        """`newer=True` places this Daily file after the Monthly in time —
        the only shape the prefilter lets through."""
        body = (
            self.EMPTY_DAILY.format(day=day)
            if event_id is None
            else self.DAILY.format(day=day, event_id=event_id, summary=summary)
        )
        path = self.daily / f"{day}.md"
        path.write_text(body, encoding="utf-8")
        stamp = self.base + (300 if newer else -300)
        os.utime(path, (stamp, stamp))
        return path

    def _write_monthly(self, entries):
        items = "\n\n".join(
            self.MONTHLY_ITEM.format(event_id=e, summary=s, day=d) for e, s, d in entries
        )
        path = self.monthly / "2026-07.md"
        path.write_text(
            self.MONTHLY.format(items=items, count=len(entries)), encoding="utf-8"
        )
        os.utime(path, (self.base, self.base))
        return path

    def _lagging(self, dirty=()):
        # C68 made this a `(findings, skipped)` pair so an unreadable day
        # stops arguing that the Monthly is up to date. These tests are about
        # the findings; `MonthlyLagCountsWhatItCouldNotReadTests` covers the
        # second element.
        findings, _skipped = self._module()._monthly_lags_its_daily_source(
            self.daily, self.monthly, dirty_months=tuple(dirty)
        )
        return findings

    def test_an_item_added_after_consolidation_is_reported(self):
        self._write_day("2026-07-01", "EVT-1")
        self._write_monthly([("EVT-1", "work", "2026-07-01")])
        self._write_day("2026-07-02", "EVT-HAND", summary="a hand-written correction", newer=True)

        self.assertEqual(self._lagging(), (("2026-07", ("EVT-HAND",)),))

    def test_a_consistent_month_is_quiet(self):
        self._write_day("2026-07-01", "EVT-1")
        self._write_monthly([("EVT-1", "work", "2026-07-01")])

        self.assertEqual(self._lagging(), ())

    def test_a_month_whose_daily_files_are_all_older_is_not_even_read(self):
        """The prefilter. A Monthly newer than every Daily it was built from
        cannot have fallen behind, and the healthy tree is the common case."""
        self._write_day("2026-07-01", "EVT-1")
        self._write_monthly([])  # deliberately inconsistent...
        # ...but every Daily file predates it, so there is nothing to find.

        self.assertEqual(self._lagging(), ())

    def test_a_restore_that_rewrites_every_mtime_reports_nothing(self):
        """mtime is a prefilter and never the verdict — the month it lets
        through is decided by reading the files."""
        self._write_monthly([("EVT-1", "work", "2026-07-01")])
        self._write_day("2026-07-01", "EVT-1", newer=True)

        self.assertEqual(self._lagging(), ())

    def test_a_dirty_month_is_skipped(self):
        """The next run rebuilds it; an alert the next run clears is the kind
        this file keeps warning about."""
        self._write_day("2026-07-01", "EVT-1")
        self._write_monthly([("EVT-1", "work", "2026-07-01")])
        self._write_day("2026-07-02", "EVT-2", newer=True)

        self.assertEqual(self._lagging(), (("2026-07", ("EVT-2",)),))
        self.assertEqual(self._lagging(dirty=["2026-07"]), ())

    def test_a_month_with_no_monthly_file_is_skipped(self):
        """Not consolidated is not the same as behind."""
        self._write_day("2026-07-01", "EVT-1")

        self.assertEqual(self._lagging(), ())

    def test_a_bullet_shaped_summary_is_not_reported_as_missing(self):
        """`- Event ID: …` written as a *summary* is prose, and the parser
        this shares says so — otherwise every such day would report a
        permanent phantom loss."""
        self._write_day("2026-07-01", "EVT-1")
        self._write_monthly([("EVT-1", "Event ID: measured it.", "2026-07-01")])
        self._write_day("2026-07-02", "EVT-2", summary="Event ID: measured it.", newer=True)
        self._write_monthly(
            [
                ("EVT-1", "Event ID: measured it.", "2026-07-01"),
                ("EVT-2", "Event ID: measured it.", "2026-07-02"),
            ]
        )

        self.assertEqual(self._lagging(), ())

    def test_a_monthly_carrying_an_id_no_daily_has_is_not_reported(self):
        """One direction only, the same one the sibling check takes: a
        hand-edited Monthly is not a loss of Company History."""
        self._write_day("2026-07-01", "EVT-1", newer=True)
        self._write_monthly(
            [("EVT-1", "work", "2026-07-01"), ("EVT-GHOST", "invented", "2026-07-01")]
        )

        self.assertEqual(self._lagging(), ())

    def test_the_attention_line_names_the_month_the_event_and_the_remedy(self):
        import contextlib

        from scheduler.state import SchedulerState
        from scheduler.state import save_state as save_scheduler_state

        runtime = self.root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (runtime / rel).mkdir(parents=True)
        self.daily = runtime / "local_master" / "daily"
        self.monthly = runtime / "local_master" / "monthly"
        self._write_day("2026-07-01", "EVT-1")
        self._write_monthly([("EVT-1", "work", "2026-07-01")])
        self._write_day("2026-07-02", "EVT-HAND", newer=True)
        save_scheduler_state(
            runtime / "state" / "daily_history_state.json",
            SchedulerState(last_successful_daily_close=date(2026, 7, 2)),
        )

        module = self._module(runtime)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(NOW)

        lines = [a for a in attention if "그 달 Monthly에는 없는" in a]
        self.assertEqual(len(lines), 1, attention)
        self.assertIn("2026-07", lines[0])
        self.assertIn("EVT-HAND", lines[0])
        self.assertIn("dirty", lines[0])
        self.assertIn("Monthly 원본 미반영 : 2026-07 (1건)", buffer.getvalue())

    def test_a_daily_this_cannot_read_is_skipped_not_raised(self):
        """A corrupt Daily has its own reporters — `_daily_counts_more_than_
        it_shows()` skips it, `find_orphaned_events()` names it, and
        `read_daily_document()` raises a `DailyParseError` that names the
        file. What this must not do is die on it: the whole point of the
        status view is answering while part of the evidence is damaged
        (docs/10 §46).

        Skipping shrinks the source set, so it can hide a finding and never
        invent one — the same direction everything else here fails towards.
        """
        self._write_day("2026-07-01", "EVT-1")
        self._write_monthly([("EVT-1", "work", "2026-07-01")])
        broken = self._write_day("2026-07-02", "EVT-2", newer=True)
        broken.write_bytes(bytes([0xFF, 0xFE]) + b" not utf-8 at all")
        stamp = self.base + 300
        os.utime(broken, (stamp, stamp))

        self.assertEqual(self._lagging(), ())

    def test_a_monthly_this_cannot_read_is_skipped_not_raised(self):
        self._write_day("2026-07-01", "EVT-1")
        monthly = self._write_monthly([("EVT-1", "work", "2026-07-01")])
        self._write_day("2026-07-02", "EVT-2", newer=True)
        monthly.write_bytes(bytes([0xFF, 0xFE]) + b" not utf-8 at all")
        os.utime(monthly, (self.base, self.base))

        self.assertEqual(self._lagging(), ())

    def test_a_late_event_merged_into_a_consolidated_month_stays_quiet(self):
        """The path that is already correct, pinned so this check cannot start
        crying wolf on it: the Runner marks the month dirty for a Late Event
        and rebuilds the month in the same run.

        Driven through the real `consolidate_month()` rather than a
        hand-written Monthly — the point is that what a rebuild produces
        satisfies this check, not that a fixture does.

        `_examine()` re-stamps the Daily file after every mutation so the
        month is always read rather than prefiltered. Both writers here land
        on the wall clock, and which of two same-instant mtimes compares
        greater is not something a test may rest on; the property under test
        is about content.
        """
        from monthly import consolidate_month, mark_month_dirty
        from monthly.state import load_state

        state_path = self.root / "monthly_history_state.json"

        def _examine():
            stamp = time.time() + 600
            os.utime(self.daily / "2026-07-30.md", (stamp, stamp))
            return self._lagging()

        # `history_start_date` trims the month to these two days, so Daily
        # Coverage is COMPLETE and the month is consolidatable (docs/09 §85).
        self._write_day("2026-07-30", "EVT-1")
        self._write_day("2026-07-31")
        consolidate_month(
            year=2026, month=7, daily_dir=self.daily, monthly_dir=self.monthly,
            history_start_date=date(2026, 7, 30),
            now=datetime(2026, 8, 1, 9, 0).astimezone(),
        )
        self.assertEqual(_examine(), ())

        # a Late Event lands in a day of the already-consolidated month
        path = self.daily / "2026-07-30.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("## Metadata", self.LATE_SECTION),
            encoding="utf-8",
        )
        self.assertEqual(_examine(), (("2026-07", ("EVT-LATE",)),))

        # ...and what the Runner does about it
        mark_month_dirty(state_path, date(2026, 7, 30), monthly_dir=self.monthly)
        self.assertEqual(
            self._lagging(dirty=load_state(state_path).dirty_months),
            (),
            "a month awaiting rebuild must not be reported",
        )
        consolidate_month(
            year=2026, month=7, daily_dir=self.daily, monthly_dir=self.monthly,
            history_start_date=date(2026, 7, 30),
            now=datetime(2026, 8, 5, 9, 0).astimezone(), allow_update=True,
        )

        self.assertIn(
            "- Event ID: EVT-LATE",
            (self.monthly / "2026-07.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(_examine(), ())


class HoleInTheMonthlySequenceTests(unittest.TestCase):
    """The exact sibling of the Daily hole, one level up, and it was equally
    unwatched.

    `pending_months()` consolidates oldest-first without skipping and
    docs/09 §72 writes a file for a month with no material history too --
    precisely so "nothing happened" and "we forgot" stay distinguishable.
    So the Monthly filenames are a contiguous run of months, and an interior
    gap is a file that was there.

    Measured with 2026-01..2026-08 consolidated and 04/05 deleted: no
    ATTENTION line mentioned them, `pending_months()` starts *after*
    `last_successful_monthly_close` so nothing revisits them, and the
    state-vs-history check asks only about the last closed month.

    The remedy is exact, unlike Daily's, and the message says so because it
    was measured end to end: Monthly is derived wholly from the Daily files
    (docs/09 §12-13), so `mark_month_dirty()` plus a run rebuilds it,
    content included.
    """

    MONTHS = tuple(f"2026-{m:02d}" for m in range(1, 9))

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_month_holes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _monthly_dir(self, present):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        for key in present:
            (directory / f"{key}.md").write_text(
                "# x\n\n## Metadata\n\n- Consolidated Items: 0\n", encoding="utf-8"
            )
        return directory

    def test_an_interior_hole_is_reported(self):
        present = [m for m in self.MONTHS if m not in ("2026-04", "2026-05")]

        holes = self._module()._holes_in_the_monthly_sequence(
            self._monthly_dir(present)
        )

        self.assertEqual(holes, ("2026-04", "2026-05"))

    def test_a_complete_sequence_is_quiet(self):
        self.assertEqual(
            self._module()._holes_in_the_monthly_sequence(
                self._monthly_dir(self.MONTHS)
            ),
            (),
        )

    def test_a_missing_suffix_is_not_a_hole(self):
        present = [m for m in self.MONTHS if m < "2026-07"]

        self.assertEqual(
            self._module()._holes_in_the_monthly_sequence(
                self._monthly_dir(present)
            ),
            (),
        )

    def test_the_gap_is_counted_across_a_year_boundary(self):
        """Month arithmetic, not string arithmetic: 2025-12 -> 2026-02 is
        one missing month, and comparing keys as text would say otherwise."""
        holes = self._module()._holes_in_the_monthly_sequence(
            self._monthly_dir(["2025-11", "2025-12", "2026-02"])
        )

        self.assertEqual(holes, ("2026-01",))

    def test_a_single_month_and_an_empty_tree_are_quiet(self):
        module = self._module()

        self.assertEqual(
            module._holes_in_the_monthly_sequence(self._monthly_dir(["2026-03"])), ()
        )
        self.assertEqual(
            module._holes_in_the_monthly_sequence(self._monthly_dir([])), ()
        )
        self.assertEqual(
            module._holes_in_the_monthly_sequence(Path("no-such-directory")), ()
        )

    def test_a_directory_and_odd_names_are_handled(self):
        present = [m for m in self.MONTHS if m != "2026-04"]
        directory = self._monthly_dir(present)
        (directory / "2026-04.md").mkdir()
        (directory / "notes.md").write_text("a note", encoding="utf-8")
        (directory / ".tmp-abandoned.md").write_text("residue", encoding="utf-8")

        self.assertEqual(
            self._module()._holes_in_the_monthly_sequence(directory), ("2026-04",)
        )

    def test_the_message_names_the_remedy_that_was_measured(self):
        """`mark_month_dirty()` plus a run restores a deleted Monthly, file
        and content. Verified below rather than asserted in prose."""
        import calendar

        from daily import generate_daily_history
        from history import HistoryCandidate, HistoryDecision
        from history.file_repository import FileHistoryRepository
        from monthly import mark_month_dirty
        from monthly.generator import run_once as monthly_run_once

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        repository = FileHistoryRepository(
            keep_dir=root / "keep", review_dir=root / "review"
        )
        repository.save(
            HistoryCandidate(
                history_id="H1", event_id="EVT-1",
                timestamp="2026-07-05T10:00:00+09:00", category="DECISION",
                project_id="P", role="COO", summary="july work", evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
        )
        for month in (7, 8):
            for day in range(1, calendar.monthrange(2026, month)[1] + 1):
                generate_daily_history(
                    repository, date(2026, month, day),
                    output_dir=root / "daily", generated_at="gen",
                )
        state_path = root / "state" / "monthly.json"

        def run(now):
            return monthly_run_once(
                daily_dir=root / "daily", monthly_dir=root / "monthly",
                history_start_date=date(2026, 7, 1), now=now, state_path=state_path,
            )

        run(datetime(2026, 9, 1, 11, 0).astimezone())
        (root / "monthly" / "2026-07.md").unlink()

        plain = run(datetime(2026, 9, 2, 11, 0).astimezone())
        self.assertEqual([r.status for r in plain.results], [])
        self.assertFalse((root / "monthly" / "2026-07.md").exists())

        mark_month_dirty(state_path, date(2026, 7, 5))
        run(datetime(2026, 9, 3, 11, 0).astimezone())

        restored = (root / "monthly" / "2026-07.md").read_text(encoding="utf-8")
        self.assertIn("EVT-1", restored)


class NotionQueueVisibilityTests(unittest.TestCase):
    """C32 §14: the two Notion queues wrote `added_at` and `attempt_count`
    and nothing ever read them.

    Grepping the whole repository for a *consumer* of either field found
    none — written by `retry_queue.upsert_entry()` and
    `dashboard_pending.save_pending()`, round-tripped through JSON, and read
    by no log line, no status view and no test outside the queue modules'
    own. BUG-39's shape, in the place where it costs most.

    What it cost: BUG-13 established that `NOTION_RETRY_REQUIRED` covers both
    "Notion was briefly down" and "Notion will refuse this forever", and
    fixed the *reason string* so `notion_sync.log` could tell them apart. The
    queue's own two fields answer the other half — how long has this been
    stuck, how many times has it been tried — and reached nobody.

    The Run Manifest's `queued=` metric is not a substitute three ways over,
    and the last test here pins the third: an entry whose `to_event()` fails
    is counted by `app/runner.py` as `notion_unreadable`, is left in the
    queue, and appears in no `queued` count at all.
    """

    def _load(self, runtime_dir: Path):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_notion_queue", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime_dir
        return module

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "state").mkdir(parents=True)
        return runtime

    def _write_queue(self, runtime, entries):
        (runtime / "state" / "notion_retry_queue.json").write_text(
            json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
        )

    def _entry(self, event_id="EVT-1", *, added_at, attempt_count=1):
        return {
            "event_id": event_id,
            "project_id": "PRJ",
            "event_data": {"event_id": event_id},
            "added_at": added_at,
            "attempt_count": attempt_count,
        }

    def _run(self, runtime):
        module = self._load(runtime)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            attention = module._print_notion(NOW)
        return out.getvalue(), attention

    def test_an_empty_queue_says_so_and_needs_nobody(self):
        printed, attention = self._run(self._runtime())

        self.assertIn("대기 중 Event       : 0", printed)
        self.assertEqual(attention, [])

    def test_a_fresh_entry_is_reported_without_raising_attention(self):
        """The queue doing its job. An outage minutes old is what it is for,
        and an alert that fires on it is one people learn to skim."""
        runtime = self._runtime()
        self._write_queue(
            runtime,
            [self._entry(added_at=(NOW - timedelta(hours=2)).isoformat())],
        )

        printed, attention = self._run(runtime)

        self.assertIn("대기 중 Event       : 1", printed)
        self.assertEqual(attention, [])

    def test_a_long_stuck_entry_reaches_attention(self):
        """The case BUG-13 named and nothing could see: an entry that has
        outlived any plausible outage is probably a request Notion will keep
        refusing."""
        runtime = self._runtime()
        self._write_queue(
            runtime,
            [
                self._entry(
                    "EVT-STUCK",
                    added_at=(NOW - timedelta(days=9)).isoformat(),
                    attempt_count=37,
                )
            ],
        )

        printed, attention = self._run(runtime)

        self.assertIn("최대 재시도 횟수    : 37", printed)
        self.assertEqual(len(attention), 1)
        self.assertIn("EVT-STUCK", attention[0])
        self.assertIn("37", attention[0])

    def test_the_threshold_is_the_one_already_in_this_file(self):
        """`SILENT_AFTER_DAYS` reused rather than a new number invented —
        the same choice `_print_last_run()` made. Pinned so the two cannot
        drift into two different ideas of "too long"."""
        runtime = self._runtime()
        module = self._load(runtime)
        just_under = module.SILENT_AFTER_DAYS - 0.5
        just_over = module.SILENT_AFTER_DAYS + 0.5

        for days, expected in ((just_under, 0), (just_over, 1)):
            with self.subTest(days=days):
                self._write_queue(
                    runtime,
                    [self._entry(added_at=(NOW - timedelta(days=days)).isoformat())],
                )
                _, attention = self._run(runtime)
                self.assertEqual(len(attention), expected)

    def test_an_unparseable_added_at_is_skipped_and_said_out_loud(self):
        """A queue file is JSON that `load_queue()` shape-checks and never
        validates as timestamps. Guessing at one would be worse than saying
        it could not be read — but it must still be *counted*, or the entry
        disappears from the block that exists to count it."""
        runtime = self._runtime()
        self._write_queue(
            runtime,
            [
                self._entry("EVT-BAD", added_at="yesterday"),
                self._entry("EVT-ALSO-BAD", added_at=""),
            ],
        )

        printed, attention = self._run(runtime)

        self.assertIn("대기 중 Event       : 2", printed)
        self.assertIn("added_at을 읽을 수 없는 항목 2건", printed)
        # Nothing datable, so nothing can be called stale — but the two
        # entries are still on the count above, which is the point.
        self.assertEqual(attention, [])

    def test_a_naive_added_at_is_compared_rather_than_discarded(self):
        """The naive/aware guard, and the reason it is a guard rather than a
        rejection. `_print_last_run()` already treats a naive `started_at`
        this way — both sides are made naive and compared — so a state file
        restored from a backup that lost its offsets still ages correctly
        instead of silently dropping out of the check.

        Comparing the two directly is the TypeError BUG-29 was about, one
        module over; discarding the value instead would be the silence this
        whole block was added to remove.
        """
        runtime = self._runtime()
        self._write_queue(
            runtime,
            [self._entry("EVT-NAIVE", added_at="2026-08-01T10:00:00")],
        )

        printed, attention = self._run(runtime)

        self.assertNotIn("읽을 수 없는 항목", printed)
        self.assertIn("EVT-NAIVE", printed)
        self.assertEqual(len(attention), 1)

    def test_a_corrupt_queue_file_is_reported_not_raised(self):
        """This view's contract is that it answers even when the evidence is
        damaged — and a Retry Queue the Runner cannot read is a run that
        fails at step 4."""
        runtime = self._runtime()
        (runtime / "state" / "notion_retry_queue.json").write_text(
            "{not json", encoding="utf-8"
        )

        printed, attention = self._run(runtime)

        self.assertIn("손상된 Retry Queue", printed)
        self.assertTrue(any("읽을 수 없다" in item for item in attention))

    def test_a_corrupt_dashboard_pending_file_is_reported_not_raised(self):
        """Worse than the queue's, and quieter: `drain_pending()` absorbs a
        damaged file as "nothing to drain" (CEO ④ — a Dashboard problem must
        never interrupt the Runtime), so the backlog is never retried and
        nothing outside that one return value says so."""
        runtime = self._runtime()
        (runtime / "state" / "dashboard_pending.json").write_text(
            "{not json", encoding="utf-8"
        )

        printed, attention = self._run(runtime)

        self.assertIn("손상된 Dashboard 대기열", printed)
        self.assertTrue(any("영원히 재시도되지 않는다" in item for item in attention))

    def test_the_dashboard_backlog_depth_is_reported(self):
        runtime = self._runtime()
        (runtime / "state" / "dashboard_pending.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "run_id": "R-1",
                            "properties": {},
                            "queued_at": NOW.isoformat(),
                            "attempt_count": 12,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        printed, _ = self._run(runtime)

        self.assertIn("Dashboard 밀린 기록 : 1", printed)
        self.assertIn("최대 재시도 횟수    : 12", printed)

    def test_a_queued_entry_the_runner_cannot_parse_is_still_counted(self):
        """The third reason the manifest's `queued=` is not a substitute.

        `app/runner.py` step 4a calls `queued_entry.to_event()`; when that
        raises it records the id under `notion_unreadable`, logs
        `NOTION_UNREADABLE queued:<id>`, and **leaves the entry in the
        queue** — it never becomes a `SyncResult`, so it is in no `queued`
        count. Measured here as the shape it takes on disk: an entry whose
        `event_data` is not a valid Event.
        """
        runtime = self._runtime()
        self._write_queue(
            runtime,
            [
                {
                    "event_id": "EVT-UNPARSEABLE",
                    "project_id": "PRJ",
                    "event_data": {"event_id": "EVT-UNPARSEABLE"},  # not an Event
                    "added_at": (NOW - timedelta(days=30)).isoformat(),
                    "attempt_count": 1,
                }
            ],
        )

        from events import EventValidationError
        from notion.retry_queue import load_queue

        entry = load_queue(runtime / "state" / "notion_retry_queue.json")[0]
        with self.assertRaises(EventValidationError):
            entry.to_event()

        printed, attention = self._run(runtime)

        self.assertIn("대기 중 Event       : 1", printed)
        self.assertTrue(any("EVT-UNPARSEABLE" in item for item in attention))

    def test_the_block_is_wired_into_the_status_view(self):
        """A detector nothing runs detects nothing — the exact gap
        `diagnose_dashboard_bootstrap()` sat in until C31."""
        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        main_body = source.split("def main(", 1)[1]

        # See `StatusEntrypointTests.test_all_three_views_are_wired_into_main`
        # for why this matches the block table rather than a literal call.
        self.assertIn(", _print_notion)", main_body)

    def test_the_paths_are_derived_per_call_from_runtime_dir(self):
        """C31 §10's trap: a path frozen at import makes `RUNTIME_DIR` a knob
        that only half works, and this block would read the developer's live
        queue while every other block read the fixture."""
        runtime = self._runtime()
        module = self._load(runtime)

        self.assertEqual(
            module._notion_retry_queue_path(),
            runtime / "state" / "notion_retry_queue.json",
        )
        self.assertEqual(
            module._dashboard_pending_path(),
            runtime / "state" / "dashboard_pending.json",
        )

    def test_the_derived_paths_match_the_queue_modules_own_defaults(self):
        """Deriving from `RUNTIME_DIR` is a second statement of where these
        files live, so the two must be checked against each other — by
        basename and parent, which is all the two agree on once `RUNTIME_DIR`
        is redirected."""
        from notion.dashboard_pending import DEFAULT_DASHBOARD_PENDING_PATH
        from notion.retry_queue import DEFAULT_QUEUE_PATH

        runtime = self._runtime()
        module = self._load(runtime)

        for derived, default in (
            (module._notion_retry_queue_path(), DEFAULT_QUEUE_PATH),
            (module._dashboard_pending_path(), DEFAULT_DASHBOARD_PENDING_PATH),
        ):
            with self.subTest(default=default.name):
                self.assertEqual(derived.name, default.name)
                self.assertEqual(derived.parent.name, default.parent.name)


class ManifestRenderedByTwoEntrypointsTests(unittest.TestCase):
    """C32 §17: the Run Manifest is rendered twice, and only one renderer
    guarded what it printed.

    `run_summary.json` is read back by `ops_status.py::_print_last_run()` and
    by `run_company_ops.py::_report_run_summary()`. `read_summary()`
    validates exactly three fields — `status`, `severity`, `retryability`,
    all enums. `name`, `classification`, `reason`, `metrics` and
    `artifact_refs` come back as whatever the JSON holds.

    The two renderers disagreed about every one of them:

        field               ops_status.py        run_company_ops.py
        component.name      one_line()           raw
        classification      one_line()           raw
        reason              not printed at all   raw
        metrics key         raw                  not printed
        metrics value       one_line()           not printed
        artifact_refs       raw                  raw

    `reason` is the one that matters most. `app/runner.py` records
    `reason=queued[0].error` for NOTION_SYNC_INCOMPLETE, and that is the
    remote HTTP response body C31 §7 added `redact(one_line(...))` for —
    twenty lines above, in the same file. The body redacted on one line was
    printed in full, from disk, three functions later.

    A restored or hand-edited manifest is a DR path, not an exotic one, and
    the ops_status half's own comment already said so: *"the rule that
    nothing read back from disk can forge a line should not depend on today's
    metric list staying the way it is"* — which is about the metric *keys*,
    and the guard was on the values.
    """

    FAKE_TOKEN = "ghp_" + "c" * 30

    def _manifest(self, path: Path, *, reason="", name="notion_sync",
                  classification="NOTION_SYNC_INCOMPLETE", metrics=None,
                  artifact_refs=()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": "R-1",
                    "started_at": "2026-08-10T09:00:00+09:00",
                    "finished_at": "2026-08-10T09:00:05+09:00",
                    "components": [
                        {
                            "name": name,
                            "status": "FAILED",
                            "failure": {
                                "classification": classification,
                                "severity": "DEGRADED",
                                "retryability": "RETRYABLE",
                                "reason": reason,
                            },
                            "metrics": metrics or {},
                            "artifact_refs": list(artifact_refs),
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    # ---------------------------------------------------- run_company_ops.py

    def _report(self, manifest_path):
        import importlib.util

        from runsummary import read_summary

        path = Path(__file__).resolve().parents[1] / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_sink", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        summary = read_summary(manifest_path)

        class _Result(tuple):
            summary = None

        result = _Result(())
        result.summary = summary

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            module._report_run_summary(result)
        return out.getvalue()

    def test_a_remote_response_body_in_reason_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "run_summary.json",
                reason=(
                    "Notion API returned 502: Bad Gateway | "
                    f"Authorization: Bearer {self.FAKE_TOKEN}"
                ),
            )
            printed = self._report(manifest)

        self.assertNotIn(self.FAKE_TOKEN, printed)
        self.assertIn("[REDACTED]", printed)

    def test_a_reason_cannot_forge_a_component_row(self):
        forged = "  [backup] BACKUP_SUCCESS (severity=NONE, retry=NONE)"
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "run_summary.json", reason="502 Bad Gateway\n" + forged
            )
            printed = self._report(manifest)

        self.assertEqual([ln for ln in printed.splitlines() if ln == forged], [])
        self.assertIn("\\n", printed)

    def test_a_component_name_cannot_forge_the_overall_status_line(self):
        forged = "실행 상태: SUCCESS"
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "run_summary.json", name="notion_sync\n" + forged
            )
            printed = self._report(manifest)

        self.assertEqual(
            sum(1 for ln in printed.splitlines() if ln.startswith("실행 상태:")),
            1,
            printed,
        )

    def test_an_artifact_ref_cannot_forge_a_line(self):
        forged = "      evidence: nothing to see"
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "run_summary.json",
                reason="r",
                artifact_refs=("runtime/logs/x.log\n" + forged,),
            )
            printed = self._report(manifest)

        self.assertEqual([ln for ln in printed.splitlines() if ln == forged], [])

    # ---------------------------------------------------------- ops_status.py

    def _last_run(self, manifest_path):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_manifest_sink", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.DEFAULT_RUN_SUMMARY_PATH = manifest_path
        module.RUNTIME_DIR = manifest_path.parent.parent

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            module._print_last_run(NOW)
        return out.getvalue()

    def test_a_metric_key_cannot_forge_a_line(self):
        """The half the guard missed, under a comment about exactly it."""
        forged = "      queued=0 unreadable=0"
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json",
                reason="r",
                metrics={"queued\n" + forged: 47},
            )
            printed = self._last_run(manifest)

        self.assertEqual([ln for ln in printed.splitlines() if ln == forged], [])
        self.assertIn("\\n", printed)

    def test_a_metric_value_is_still_guarded(self):
        """The half that was already right — pinned so widening the guard
        did not narrow it."""
        forged = "      queued=0"
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json",
                reason="r",
                metrics={"queued": "47\n" + forged},
            )
            printed = self._last_run(manifest)

        self.assertEqual([ln for ln in printed.splitlines() if ln == forged], [])

    def test_an_artifact_ref_cannot_forge_a_line_in_the_status_view_either(self):
        forged = "  종합 상태   : SUCCESS (exit 0)"
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json",
                reason="r",
                artifact_refs=("runtime/logs/x.log\n" + forged,),
            )
            printed = self._last_run(manifest)

        self.assertEqual(
            sum(1 for ln in printed.splitlines() if ln == forged), 0, printed
        )

    def test_ops_status_still_does_not_print_reason(self):
        """The deliberate difference between the two renderers, pinned. This
        one avoids the problem rather than guarding it, and that choice is
        recorded in its own comment."""
        secret = "Bearer " + "d" * 40
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json", reason=secret
            )
            printed = self._last_run(manifest)

        self.assertNotIn(secret, printed)
        self.assertNotIn("[REDACTED]", printed)

    def test_read_summary_validates_only_the_three_enums(self):
        """Why every other field needs a guard at the sink: nothing upstream
        constrains them. Checked against the loader rather than assumed."""
        import inspect

        from runsummary import read_summary

        source = inspect.getsource(read_summary)

        for validated in ("ComponentStatus(", "Severity(", "Retryability("):
            with self.subTest(field=validated):
                self.assertIn(validated, source)
        for raw in ('c["name"]', 'c["failure"]["classification"]',
                    'c["failure"].get("reason", "")', 'c.get("metrics", {})'):
            with self.subTest(field=raw):
                self.assertIn(raw, source)


class BackupFailureReportSinkTests(unittest.TestCase):
    """C32 §18: `[FAILED] Backup: {exc}` printed another program's stderr raw.

    `backup/git_ops._run_git()` builds every `GitOperationError` as
    `f"git ... failed (exit {code}): {result.stderr.strip()}"` — multi-line
    output from a subprocess. On a push failure git echoes the remote URL,
    and a `https://<token>@github.com/...` remote carries the credential in
    it. `oplog.SECRET_PATTERNS` already knows the GitHub token shapes;
    nothing was applying them here, in the one message an operator reads when
    a Backup fails.

    The classification is deliberately still computed from the RAW message:
    `is_authentication_failure()` matches git's own wording, and running it
    on a redacted string would let a substitution eat the phrase the
    BACKUP_FAILED-vs-BACKUP_PENDING decision depends on.
    """

    FAKE_PAT = "ghp_" + "e" * 30

    def _report(self, message):
        import importlib.util

        from backup.git_ops import GitOperationError

        path = Path(__file__).resolve().parents[1] / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_backup", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = module._report_backup_failure(GitOperationError(message), None)
        return err.getvalue(), code

    def test_a_token_in_the_remote_url_is_redacted(self):
        printed, _ = self._report(
            "git push origin main failed (exit 128): "
            f"fatal: Authentication failed for 'https://{self.FAKE_PAT}@github.com/x/y.git'"
        )

        self.assertNotIn(self.FAKE_PAT, printed)
        self.assertIn("[REDACTED]", printed)

    def test_git_stderr_cannot_forge_a_line_of_the_report(self):
        """The forged line is one this report never writes — a reassurance
        that would tell an operator to stop looking. Deliberately not one of
        the report's own sentences: those appear legitimately, and a test
        that could not tell the two apart would prove nothing."""
        forged = "이 실패는 무시해도 됩니다 — 확인할 것 없음"
        printed, _ = self._report(
            "git push origin main failed (exit 128): remote rejected\n" + forged
        )

        self.assertEqual([ln for ln in printed.splitlines() if ln == forged], [])
        self.assertIn("\\n", printed)

    def test_the_classification_still_reads_the_raw_message(self):
        """Redacting before classifying would change the verdict, not just
        the wording. An auth failure must still be called permanent."""
        printed, _ = self._report(
            "git push origin main failed (exit 128): "
            f"fatal: Authentication failed for 'https://{self.FAKE_PAT}@github.com/x/y.git'"
        )

        self.assertIn("인증/권한 문제로 분류되어 BACKUP_FAILED", printed)

    def test_a_transient_failure_is_still_classified_transient(self):
        printed, _ = self._report(
            "git push origin main timed out after 300s (no output; the remote "
            "may be unreachable or waiting for credentials)"
        )

        self.assertIn("BACKUP_PENDING", printed)


class WrittenAndNeverReadFieldTests(unittest.TestCase):
    """C32 §20: a sweep of every dataclass field with no production reader.

    §14 found two such fields by hand (`RetryQueueEntry.added_at` and
    `attempt_count`). Rather than stop at two, every `@dataclass` field in
    `src/` and the root entrypoints was walked with AST and cross-checked
    against every attribute *load* in the same set — 255 fields, 36 modules.
    Thirty had no reader outside their own defining module.

    Most are legitimate: a field the defining module itself renders into a
    log line, a value carried for a caller that has not needed it yet, a
    known dead capability already recorded (`RoleActivity.by_category`,
    C31 §16). Three were not, and all three were the same shape — the file
    or the timestamp a person needs in order to act, recorded and never
    shown:

        RunSummary.finished_at              no reader at all, not even a test
        UnreadableEvent.event_path          ATTENTION said "N건", named none
        PendingDashboardRecord.queued_at    age reported for the sibling
                                            queue and not for this one

    The remaining twenty-seven are pinned in BACKLOG rather than here: this
    class exists for the three that were closed, so that closing them cannot
    quietly come undone.
    """

    def _load_status(self, runtime_dir=None):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_unread", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if runtime_dir is not None:
            module.RUNTIME_DIR = runtime_dir
        return module

    def _runtime(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        (runtime / "state").mkdir(parents=True)
        return runtime

    # ------------------------------------------------ RunSummary.finished_at

    def _manifest(self, path, *, started, finished):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": "R-1",
                    "started_at": started,
                    "finished_at": finished,
                    "components": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _last_run(self, manifest_path):
        module = self._load_status(manifest_path.parent.parent)
        module.DEFAULT_RUN_SUMMARY_PATH = manifest_path
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            module._print_last_run(NOW)
        return out.getvalue()

    def test_the_run_duration_is_shown(self):
        """Two timestamps were on disk and neither was ever subtracted."""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json",
                started="2026-08-10T09:00:00+09:00",
                finished="2026-08-10T09:02:03+09:00",
            )
            printed = self._last_run(manifest)

        self.assertIn("소요 시간   : 123.0s", printed)

    def test_an_unparseable_finished_at_is_skipped_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json",
                started="2026-08-10T09:00:00+09:00",
                finished="who knows",
            )
            printed = self._last_run(manifest)

        self.assertNotIn("소요 시간", printed)
        self.assertIn("실행 시각", printed)

    def test_a_mixed_naive_and_aware_pair_is_skipped(self):
        """Comparing them directly is the TypeError BUG-29 was about; a
        restored manifest can carry either shape."""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json",
                started="2026-08-10T09:00:00+09:00",
                finished="2026-08-10T09:02:03",
            )
            printed = self._last_run(manifest)

        self.assertNotIn("소요 시간", printed)

    def test_a_finished_at_before_started_at_is_not_reported_as_negative(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(
                Path(tmp) / "state" / "run_summary.json",
                started="2026-08-10T09:02:03+09:00",
                finished="2026-08-10T09:00:00+09:00",
            )
            printed = self._last_run(manifest)

        self.assertNotIn("소요 시간", printed)

    # --------------------------------------------- UnreadableEvent.event_path

    def test_an_unreadable_processed_event_is_named(self):
        """`UnreadableEvent` carries `event_path` so a person can open the
        file; the ATTENTION line said "N건" and named none. Every sibling
        line in this view names up to five."""
        runtime = self._runtime()
        processed = runtime / "events" / "processed"
        processed.mkdir(parents=True)
        (processed / "broken-event.json").write_text("{not json", encoding="utf-8")
        (runtime / "local_master" / "daily").mkdir(parents=True)
        _healthy_backup_state(runtime / "state")

        module = self._load_status(runtime)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            attention = module._print_history(NOW)

        named = [item for item in attention if "읽을 수 없는 Event" in item]
        self.assertEqual(len(named), 1, attention)
        self.assertIn("broken-event.json", named[0])

    # --------------------------------------- PendingDashboardRecord.queued_at

    def _pending(self, runtime, queued_at, *, attempt_count=1, run_id="R-OLD"):
        (runtime / "state" / "dashboard_pending.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "run_id": run_id,
                            "properties": {},
                            "queued_at": queued_at,
                            "attempt_count": attempt_count,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def _notion(self, runtime):
        module = self._load_status(runtime)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            attention = module._print_notion(NOW)
        return out.getvalue(), attention

    def test_the_oldest_pending_dashboard_record_is_dated(self):
        runtime = self._runtime()
        self._pending(runtime, (NOW - timedelta(hours=3)).isoformat())

        printed, attention = self._notion(runtime)

        self.assertIn("가장 오래된 기록", printed)
        self.assertEqual(attention, [])

    def test_a_long_stuck_dashboard_record_reaches_attention(self):
        """A record Notion permanently refuses comes back every run with
        nothing but `attempt_count` climbing — the diagnostic blank
        `DrainPendingResult.last_reason` was added for, and this is the line
        that points at it."""
        runtime = self._runtime()
        self._pending(
            runtime,
            (NOW - timedelta(days=11)).isoformat(),
            attempt_count=53,
            run_id="R-STUCK",
        )

        printed, attention = self._notion(runtime)

        self.assertEqual(len(attention), 1, attention)
        self.assertIn("R-STUCK", attention[0])
        self.assertIn("53", attention[0])
        self.assertIn("DRAIN_PENDING", attention[0])

    def test_the_stuck_record_line_names_the_command_that_fixes_it(self):
        """C36: the reason was already legible and the way out was not.

        `OPS_RUNS` grew 13 -> 17 columns across C32/C33, so a database made
        before a widening 400s on every run, forever. That is the most likely
        REASON behind a record stuck for days, and the line an operator reads
        should not stop at "go read the log". The pointer is only worth
        printing if it lands somewhere, so this checks the section exists.
        """
        runtime = self._runtime()
        self._pending(runtime, (NOW - timedelta(days=11)).isoformat())

        _, attention = self._notion(runtime)

        self.assertIn("docs/13 §3-⑧-4", attention[0])
        setup_doc = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "13_NOTION_ENVIRONMENT_SETUP.md"
        ).read_text(encoding="utf-8")
        self.assertIn("⑧-4", setup_doc)
        self.assertIn("bootstrap_dashboard_properties", setup_doc)

    def test_an_unparseable_queued_at_does_not_break_the_block(self):
        runtime = self._runtime()
        self._pending(runtime, "not a timestamp")

        printed, attention = self._notion(runtime)

        self.assertIn("Dashboard 밀린 기록 : 1", printed)
        self.assertNotIn("가장 오래된 기록", printed)
        self.assertEqual(attention, [])

    def test_the_two_queues_are_aged_by_the_same_helper(self):
        """The asymmetry this closed: reporting one queue's age and not the
        other's, inside the block added to remove exactly that."""
        import inspect

        module = self._load_status()
        source = inspect.getsource(module._print_notion)

        self.assertEqual(source.count("_queue_age_days("), 2)


class ReconciliationLockAwarenessTests(unittest.TestCase):
    """The orphan report's "Runner 실행 중" caveat, and the promise beside it.

    `find_orphaned_events()` compares consumed Events against stored
    Candidates, and a Runner part-way between step 4 and step 5 makes a
    perfectly healthy Event look orphaned: the Collector moves the whole
    batch into `processed/` before the History Filter writes Candidates one
    at a time. So `ops_status.py` appends a sentence when the Runner lock is
    held — and, deliberately, removes nothing:

        The list is NOT filtered or suppressed: a real loss hidden behind
        "probably just running" is far worse than a false alarm, and this
        cannot tell the two apart. A sentence is added, nothing is removed.

    **This class is written because BACKLOG cited it and it did not exist.**
    The A-20 entry lists `tests/test_observability.py::ReconciliationLockAwarenessTests`
    (4건) as the evidence for the ops_status half of `is_locked()`; sweeping
    every test name the BACKLOG cites found this one class missing, while
    `IsLockedTests` (the lock half, in `test_lock_atomicity.py`) was there.
    The behaviour was live and untested — including the "nothing is removed"
    half, which is the one that decides whether a data-loss report can be
    silenced by a lock file.

    The stale-lock case is the reason that matters most: a lock whose
    process is dead must not attach the caveat, or a crashed Runner would
    permanently excuse every orphan report on the machine.
    """

    def _load(self, runtime_dir: Path):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_orphan_lock", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = runtime_dir
        return module

    def _runtime(self, *, orphans=1):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        for rel in (
            "state",
            "locks",
            "local_master/daily",
            "events/processed",
            "history_candidates/keep",
            "history_candidates/review",
        ):
            (runtime / rel).mkdir(parents=True)
        # Consumed Events with no Candidate: A-20's shape, which is what the
        # orphan detector reports. `create_event` so the files are real
        # Events rather than a shape this test invented.
        for index in range(orphans):
            event = create_event(
                source="DESKTOP_1",
                role="COO",
                project_id="PRJ_ORPHAN",
                event_type="MILESTONE_COMPLETED",
                status="IN_PROGRESS",
                summary=f"orphan {index}",
                history_candidate=True,
                event_id=f"EVT-ORPHAN-{index}",
                timestamp="2026-08-05T10:00:00+09:00",
                milestone=f"M{index}",
            )
            (runtime / "events" / "processed" / f"{event.event_id}.json").write_text(
                json.dumps(event.to_dict()), encoding="utf-8"
            )
        _healthy_backup_state(runtime / "state")
        return runtime

    def _lock(self, runtime, *, pid):
        (runtime / "locks" / "company_ops.lock").write_text(
            json.dumps(
                {"process_id": pid, "created_at": NOW.isoformat(timespec="seconds")}
            ),
            encoding="utf-8",
        )

    def _orphan_alert(self, runtime):
        module = self._load(runtime)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            attention = module._print_history(NOW)
        matching = [item for item in attention if "History에 들어가지 못한" in item]
        self.assertEqual(len(matching), 1, attention)
        return matching[0]

    def test_no_lock_means_no_caveat(self):
        alert = self._orphan_alert(self._runtime())

        self.assertIn("EVT-ORPHAN-0", alert)
        self.assertNotIn("Runner 실행 중", alert)

    def test_a_live_lock_adds_the_caveat(self):
        runtime = self._runtime()
        self._lock(runtime, pid=os.getpid())

        alert = self._orphan_alert(runtime)

        self.assertIn("Runner 실행 중", alert)

    def test_the_caveat_never_shortens_the_list(self):
        """The half the comment insists on. Same runtime, same orphans, lock
        on and off — the only difference allowed is the appended sentence."""
        without = self._runtime(orphans=3)
        with_lock = self._runtime(orphans=3)
        self._lock(with_lock, pid=os.getpid())

        quiet = self._orphan_alert(without)
        running = self._orphan_alert(with_lock)

        self.assertTrue(running.startswith(quiet))
        self.assertEqual(running[len(quiet) :], " (Runner 실행 중 — 완료 후 재확인 권장)")
        for index in range(3):
            with self.subTest(orphan=index):
                self.assertIn(f"EVT-ORPHAN-{index}", running)

    def test_a_stale_lock_does_not_excuse_the_orphans(self):
        """A lock file whose process is gone is not a running Runner. If it
        attached the caveat anyway, one crashed run would put "probably just
        running" on every orphan report from then on — BUG-42's silence in a
        different costume."""
        runtime = self._runtime()
        # A pid that cannot be alive: 0 is never a user process, and
        # `is_locked()` is the function under test for exactly this.
        self._lock(runtime, pid=0)

        alert = self._orphan_alert(runtime)

        self.assertIn("EVT-ORPHAN-0", alert)
        self.assertNotIn("Runner 실행 중", alert)


class AGitThatCannotAnswerIsNotACleanHistoryTests(unittest.TestCase):
    """C70. The third option the pair above did not have.

    `TheTwoSecretProbesFailInOppositeDirectionsTests` argues — correctly —
    that the producer must not fail open, because "a producer that failed
    open would cry leak on every machine without git, and the alert nobody
    believes is the one nobody reads". This does not overturn that. It adds
    the answer that is neither silence nor an invented leak: **the check did
    not happen.**

    The gap was measured on a repository that really had committed a key:

        normal                     ('id_rsa',)
        git raises OSError         ()            <- identical to clean
        git exits 128              ()            <- identical to clean
        not a repository at all    ()            <- correctly nothing

    Three of those four printed nothing, and one of the three was a live
    credential in the remote history. `_secrets_ever_committed()`'s own
    docstring calls that outcome "the most dangerous possible answer" — it
    was producing it.

    **Why this does not become the alert nobody reads.** The `.git` check
    runs first, so a Working Copy that was never initialised — the ordinary
    state on any machine where Backup is not configured, and the stimulus
    the class above uses — is `checked` with nothing found, and says nothing.
    The caveat needs a repository that exists and a git that will not read
    it, which is a broken state rather than an unconfigured one. Same
    distinction C68 drew between an absent subject and a failed read.
    """

    def _load(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_leakcheck", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _repository_with_a_committed_key(self):
        import subprocess

        wc = Path(tempfile.mkdtemp()) / "wc"
        wc.mkdir()
        for args in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "ops@example.invalid"],
            ["git", "config", "user.name", "ops"],
        ):
            subprocess.run(args, cwd=wc, capture_output=True)
        (wc / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=wc, capture_output=True)
        result = subprocess.run(
            ["git", "commit", "-qm", "x"], cwd=wc, capture_output=True, text=True
        )
        if result.returncode != 0:
            self.skipTest(f"git could not commit here: {result.stderr.strip()}")
        return wc

    def test_the_premise_is_real(self):
        """Guards the guard: if the fixture did not actually commit a key the
        assertions below would pass against a genuinely clean history."""
        module = self._load()
        wc = self._repository_with_a_committed_key()

        self.assertEqual(module._secrets_ever_committed(wc), (("id_rsa",), True))

    def test_git_refusing_to_run_is_reported_as_unchecked(self):
        import subprocess

        module = self._load()
        wc = self._repository_with_a_committed_key()

        def refuse(*args, **kwargs):
            raise OSError(2, "git not found")

        with mock.patch.object(subprocess, "run", refuse):
            found, checked = module._secrets_ever_committed(wc)

        self.assertEqual(found, (), "it must still not invent a leak")
        self.assertFalse(checked, "and it must not claim the history is clean")

    def test_git_answering_non_zero_is_reported_as_unchecked(self):
        import subprocess

        module = self._load()
        wc = self._repository_with_a_committed_key()

        class Failed:
            returncode = 128
            stdout = ""
            stderr = "fatal: bad object"

        with mock.patch.object(subprocess, "run", lambda *a, **k: Failed()):
            found, checked = module._secrets_ever_committed(wc)

        self.assertEqual((found, checked), ((), False))

    def test_a_working_copy_that_was_never_a_repository_stays_silent(self):
        """The anti-noise half, and the reason the `.git` probe comes first."""
        module = self._load()
        wc = Path(tempfile.mkdtemp()) / "wc"
        wc.mkdir()

        self.assertEqual(module._secrets_ever_committed(wc), ((), True))

    def test_the_screen_says_it_rather_than_staying_quiet(self):
        """The half a return value cannot do. Without the caller printing it,
        `checked=False` changes nothing an operator can see."""
        import contextlib
        import subprocess

        module = self._load()
        wc = self._repository_with_a_committed_key()
        runtime = Path(tempfile.mkdtemp()) / "runtime"
        (runtime / "events" / "processed").mkdir(parents=True)
        shutil.copytree(wc, runtime / "backup_working_copy")

        def render():
            previous = module.RUNTIME_DIR
            module.RUNTIME_DIR = runtime
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    try:
                        module.main()
                    except SystemExit:
                        pass
            finally:
                module.RUNTIME_DIR = previous
            return buffer.getvalue()

        healthy = render()

        def refuse(*args, **kwargs):
            raise OSError(2, "git not found")

        with mock.patch.object(subprocess, "run", refuse):
            broken = render()

        marker = "원격 history의 Secret 검사를 확인 못 함"
        self.assertNotIn(marker, healthy)
        self.assertIn(marker, broken)


class TheTwoSecretProbesFailInOppositeDirectionsTests(unittest.TestCase):
    """When git cannot answer, one probe over-reports and the other goes
    silent — on purpose, and in the direction that keeps a real exposure
    visible in both cases.

        `_would_reach_the_commit(candidates)`  a FILTER over a set it was
                                               handed. Failing open returns
                                               the candidates unchanged, so
                                               a real secret stays named.
        `_secrets_ever_committed(working_copy)` a PRODUCER of a claim about
                                               git history. Failing open
                                               would invent a leak, so it
                                               returns nothing.

    `_secrets_ever_committed()`'s own docstring states the asymmetry and why
    ("That probe filters a set it was handed, so failing open keeps a real
    exposure visible. This one *adds* a claim about history; if git cannot
    answer, asserting a leak would be inventing one"). **Nothing tested it.**
    A line-coverage pass including the root scripts (C41) showed both
    fail-safe returns unexecuted, and the existing check at least
    (`_would_reach_the_commit(Path("/nonexistent"), ())`) passes an empty
    candidate set, which cannot tell failing open from failing closed.

    That matters because the two are one refactor apart from swapping. A
    filter that failed closed would delete a live-credential warning; a
    producer that failed open would cry leak on every machine without git,
    and the alert nobody believes is the one nobody reads.

    The stimulus is a directory that is not a git repository — `git rev-list`
    and `git ls-files` both exit non-zero. That is a real state: docs/08 §30
    permits re-creating the Working Copy, and `WorkingCopyDestroyedTests`
    already covers losing it.
    """

    SECRET = "daily/id_rsa"

    def _load(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_failsafe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _not_a_repository(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "daily").mkdir(parents=True)
        (root / "daily" / "id_rsa").write_text("not a real key", encoding="utf-8")
        return root

    def test_the_filter_fails_open_and_keeps_naming_the_file(self):
        module = self._load()
        working_copy = self._not_a_repository()

        self.assertEqual(
            module._would_reach_the_commit(working_copy, (self.SECRET,)),
            (self.SECRET,),
        )

    def test_the_producer_falls_silent_rather_than_inventing_a_leak(self):
        module = self._load()

        self.assertEqual(
            module._secrets_ever_committed(self._not_a_repository()), ((), True)
        )

    def test_a_missing_directory_is_handled_the_same_way_by_both(self):
        """The other unexercised guard, and the ordinary case on a machine
        where Backup was never configured."""
        module = self._load()
        missing = Path(tempfile.mkdtemp()) / "never_created"

        self.assertEqual(
            module._would_reach_the_commit(missing, (self.SECRET,)), (self.SECRET,)
        )
        self.assertEqual(module._secrets_ever_committed(missing), ((), True))

    def test_the_directions_really_are_opposite(self):
        """Stated as one assertion, so the pair cannot drift into agreeing.
        Two probes that both failed the same way would be a posture change,
        not a refactor, and this is where it would show."""
        module = self._load()
        working_copy = self._not_a_repository()

        filtered = module._would_reach_the_commit(working_copy, (self.SECRET,))
        produced, produced_checked = module._secrets_ever_committed(working_copy)

        self.assertTrue(filtered, "the filter must fail open")
        self.assertFalse(produced, "the producer must fail closed")
        # C70: and a directory that never was a repository is *checked* —
        # there is no history in it to hold a secret, so this stays silent
        # rather than growing the caveat added for a git that cannot answer.
        self.assertTrue(produced_checked)

    def test_the_working_copy_really_is_unreadable_by_git(self):
        """Guards the guard: if the directory were a valid repository the
        assertions above would be measuring the ordinary path and would pass
        for the wrong reason."""
        import subprocess

        working_copy = self._not_a_repository()

        result = subprocess.run(
            ["git", "rev-list", "--all", "--objects"],
            cwd=working_copy,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)


class SecretShapedEventContentTests(unittest.TestCase):
    """C47: the strongest secret guard in this project only covers one door.

    `parse_signal()` runs `find_secret_material()` over the whole payload and
    REFUSES a Signal carrying secret-shaped text. An Event arriving from
    another Desktop over OneDrive, or written by hand (docs/11 allows this on
    Desktop 4), never meets that function -- it meets `validate_event()`,
    which type-checks fields and cross-checks `event_type` against `status`
    and reads no content whatsoever.

    Measured end to end through the real Runner, one Event whose `summary`
    carries a secret-shaped string and which did not come from this machine's
    Agent:

        validate_event()          []          <- no errors
        Daily History written     yes, string intact
        git show origin/main:...  string intact
        scan_for_secrets()        ()          <- names only, never content
        oplog.redact() on a log   [REDACTED]

    The one place the string is scrubbed is the log; the one place it is kept
    forever is Company History and the backup remote.

    These tests fix the report, not a refusal. Refusing would route the Event
    to `rejected/` and delete that work from Company History -- the same trade
    the source/role mismatch records, a decision, SKIPped in BACKLOG.
    """

    SECRET = "ntn_" + "A1b2C3d4E5f6G7h8"

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_secret", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _processed(self, module):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        processed = module.RUNTIME_DIR / "events" / "processed"
        processed.mkdir(parents=True)
        return processed

    def _write(self, processed, name, **overrides):
        payload = {
            "schema_version": "1.0",
            "event_id": "E1",
            "timestamp": "2026-08-09T10:00:00+09:00",
            "source": "DESKTOP_2",
            "role": "CMO",
            "project_id": "PRJ",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "milestone": "M1",
            "summary": "ordinary work",
            "blocker": None,
            "evidence": [],
            "history_candidate": True,
        }
        payload.update(overrides)
        (processed / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return payload

    def test_the_third_destination_is_notion_and_the_report_says_so(self):
        """C49: the report named Company History and the backup remote, and
        stopped there. Measured through the real `ExecutionPlanSync`, four of
        the five scanned fields also reach the Notion PROJECTS row verbatim:

            event_id     -> `Last Event ID`       leaks
            project_id   -> `Project ID` + Title  leaks
            milestone    -> `Current Milestone`   leaks
            blocker      -> `Blocker`             leaks
            summary      -> (no property)         does not

        Notion is a third party with its own retention, and it is the copy an
        operator's rotation checklist is most likely to miss because it is not
        a file on their disk. Not fixed by redacting on the way out: that is
        the pipeline rewriting a person's own words, which docs/06 §57 is
        about, and the decision is in BACKLOG. Fixed by saying it.
        """
        from events import create_event
        from notion import ExecutionPlanSync, InMemoryNotionTransport, NotionClient

        secrets = {
            field: "ntn_" + letter * 20
            for field, letter in (
                ("event_id", "A"),
                ("project_id", "B"),
                ("milestone", "C"),
                ("summary", "D"),
                ("blocker", "E"),
            )
        }
        transport = InMemoryNotionTransport()
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="PROJECTS")
        )
        for event_type, status, extra, day in (
            ("BLOCKED", "BLOCKED", {"blocker": secrets["blocker"]}, 6),
            (
                "MILESTONE_COMPLETED",
                "IN_PROGRESS",
                {"milestone": secrets["milestone"]},
                7,
            ),
        ):
            sync.sync(
                create_event(
                    source="DESKTOP_1",
                    role="CTO_BACKEND",
                    project_id=secrets["project_id"],
                    event_type=event_type,
                    status=status,
                    summary=secrets["summary"],
                    history_candidate=True,
                    event_id=secrets["event_id"] + event_type,
                    timestamp=f"2026-08-0{day}T09:00:00+09:00",
                    **extra,
                )
            )

        written = str(transport._pages)
        reached = {
            field: token in written for field, token in secrets.items()
        }

        self.assertEqual(
            reached,
            {
                "event_id": True,
                "project_id": True,
                "milestone": True,
                "blocker": True,
                "summary": False,
            },
        )

    def test_the_attention_line_names_notion_as_a_destination(self):
        """The report is the fix, so the report is what is pinned."""
        from datetime import timezone

        module = self._load_entrypoint()
        processed = self._processed(module)
        self._write(processed, "leak.json", summary=f"rotate {self.SECRET}")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(
                datetime(2026, 8, 19, 9, 0, tzinfo=timezone(timedelta(hours=9)))
            )
        line = next(
            item for item in attention if "Secret 형태의 문자열" in item
        )

        self.assertIn("Notion", line)
        self.assertIn("PROJECTS", line)
        # ...and it still names the two it already named.
        self.assertIn("Daily History", line)
        self.assertIn("backup", line)
        # The secret itself is never quoted, here or anywhere else.
        self.assertNotIn(self.SECRET, line)

    def test_a_clean_processed_directory_says_nothing(self):
        """The property that decides whether this line is usable at all: on a
        healthy runtime it must be silent, or it joins the alerts operators
        learn to skim past."""
        module = self._load_entrypoint()
        processed = self._processed(module)
        for index in range(5):
            self._write(processed, f"e{index}.json", event_id=f"E{index}")

        self.assertEqual(module._secret_shaped_event_content(processed), ())

    def test_a_secret_in_the_summary_is_found_and_attributed(self):
        module = self._load_entrypoint()
        processed = self._processed(module)
        self._write(processed, "clean.json", event_id="CLEAN")
        self._write(
            processed,
            "leak.json",
            event_id="LEAK",
            summary=f"rotated the workspace token to {self.SECRET}",
        )

        found = module._secret_shaped_event_content(processed)

        self.assertEqual(len(found), 1)
        event_id, source, filename, fields = found[0]
        self.assertEqual((event_id, source, filename), ("LEAK", "DESKTOP_2", "leak.json"))
        self.assertEqual(fields, "summary")

    def test_the_matched_text_is_never_returned(self):
        """`find_secret_material()`'s own rule, applied here: a report of a
        leaked credential must not become the second copy of it."""
        module = self._load_entrypoint()
        processed = self._processed(module)
        self._write(processed, "leak.json", event_id="LEAK", summary=f"key {self.SECRET}")

        found = module._secret_shaped_event_content(processed)

        for value in found[0]:
            self.assertNotIn(self.SECRET, value)

    def test_every_text_carrying_field_is_scanned(self):
        """Each field separately, because a list that silently covers four of
        five is worse than no list -- it reads as complete."""
        module = self._load_entrypoint()
        cases = {
            "event_id": {"event_id": self.SECRET},
            "project_id": {"project_id": self.SECRET},
            "milestone": {"milestone": self.SECRET},
            "summary": {"summary": self.SECRET},
            "blocker": {"blocker": self.SECRET, "event_type": "BLOCKED",
                        "status": "BLOCKED"},
            "evidence": {"evidence": ["notes.md", self.SECRET]},
        }
        for field, overrides in cases.items():
            with self.subTest(field=field):
                processed = self._processed(module)
                self._write(processed, "e.json", **overrides)

                found = module._secret_shaped_event_content(processed)

                self.assertEqual(len(found), 1)
                self.assertIn(field, found[0][3])

    def test_the_field_list_covers_every_string_field_the_schema_has(self):
        """The guard that turns an explicit list into a maintained one.

        Scanning per field is 7.8x cheaper than handing the whole payload to
        `find_secret_material()` (25 ms vs 193 ms over 2,000 Events, measured),
        and the price of that is a list this file must keep true. So the list
        is compared against `Event.to_dict()` itself: a string field added to
        the schema is either scanned or fails here.

        The excluded names are the ones a person never writes -- schema
        constants, an ISO instant, and the two identity fields the Agent owns
        and a Signal is forbidden to set (`FORBIDDEN_SIGNAL_FIELDS`).
        """
        module = self._load_entrypoint()
        sample = create_event(
            source="DESKTOP_2", role="CMO", project_id="P",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS",
            summary="s", milestone="M", timestamp="2026-08-09T10:00:00+09:00",
            history_candidate=True,
        ).to_dict()
        machine_owned = {"schema_version", "timestamp", "source", "role",
                         "event_type", "status", "history_candidate"}
        text_fields = {
            name for name, value in sample.items()
            if name not in machine_owned
        }

        self.assertEqual(
            text_fields, set(module._EVENT_TEXT_FIELDS) | {"evidence"}
        )

    def test_the_rule_is_the_agents_rule(self):
        """One opinion about what a secret looks like. Every pattern the Agent
        refuses a Signal for is a pattern this finds in an Event -- otherwise
        the door that refuses and the door that reports drift apart, and the
        gap is exactly the credentials nobody hears about."""
        module = self._load_entrypoint()
        for pattern, sample in (
            ("ntn_", "ntn_" + "0123456789abcd"),
            ("secret_", "secret_" + "0123456789abcd"),
            ("Bearer", "Bearer " + "a" * 25),
            ("private key", "-----BEGIN RSA PRIVATE KEY-----"),
            ("gh PAT", "ghp_" + "b" * 25),
            ("API_KEY=", "NOTION_API_TOKEN=abc123"),
            ("PASSWORD:", "PASSWORD: hunter2"),
        ):
            with self.subTest(pattern=pattern):
                processed = self._processed(module)
                self._write(processed, "e.json", summary=f"note {sample}")

                self.assertEqual(
                    len(module._secret_shaped_event_content(processed)), 1
                )
                self.assertEqual(
                    find_secret_material({"summary": f"note {sample}"}) != (), True
                )

    def test_it_reaches_the_operators_screen_redacted(self):
        """The half that matters: found is not seen. And the ATTENTION line
        must not carry the string either -- `event_id` is a scanned field, so
        an Event named after the very token would otherwise print it."""
        module = self._load_entrypoint()
        processed = self._processed(module)
        (module.RUNTIME_DIR / "local_master" / "daily").mkdir(parents=True)
        (module.RUNTIME_DIR / "local_master" / "monthly").mkdir(parents=True)
        self._write(
            processed, "leak.json", event_id=self.SECRET, summary=f"tok {self.SECRET}"
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(
                datetime(2026, 8, 12, 9, 0).astimezone()
            )

        line = [item for item in attention if "Secret 형태의 문자열" in item]
        self.assertEqual(len(line), 1)
        self.assertNotIn(self.SECRET, line[0])
        self.assertIn("[REDACTED]", line[0])
        self.assertNotIn(self.SECRET, buffer.getvalue())

    def test_an_unreadable_file_does_not_hide_the_rest(self):
        """`read_events()` drops what it cannot parse and the HISTORY block
        already names those separately. What must not happen is one bad file
        suppressing the scan of the good ones."""
        module = self._load_entrypoint()
        processed = self._processed(module)
        (processed / "broken.json").write_text("{ not json", encoding="utf-8")
        self._write(processed, "leak.json", event_id="LEAK", summary=f"t {self.SECRET}")

        found = module._secret_shaped_event_content(processed)

        self.assertEqual([item[0] for item in found], ["LEAK"])

    def test_a_missing_directory_is_not_an_error(self):
        module = self._load_entrypoint()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)

        self.assertEqual(module._secret_shaped_event_content(root / "nope"), ())


class ThePrintedListsAboveAttentionAreBoundedTooTests(unittest.TestCase):
    """The other half of `_RECENT_ON_SCREEN`'s rule.

    Bounding ATTENTION itself is not enough: the rule is about a *section*
    that pushes ATTENTION off the top, and three lists inside
    `_print_history()` printed one line per finding with no bound while their
    ATTENTION counterparts cut at five and said "외".

        Daily 항목 불일치     one line per **day** of Company History
        Monthly 원본 미반영   one line per month
        Monthly 항목 누락     one line per month

    The first is the one that moves fast. A renderer that wrote
    `- Event Count:` wrong once wrote it wrong for every day it rendered, so
    the list is as long as the affected stretch — 365 lines a year — and the
    correctly-bounded ATTENTION line lands underneath all of them.

    Same bound and the same "총 N건" disclosure the ATTENTION lines already
    use, so nothing here decides a new display policy.
    """

    def _load(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_bounded", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _runtime(self, module):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)
        return module.RUNTIME_DIR

    @staticmethod
    def _miscounted_day(runtime, day):
        """A Daily whose own `- Event Count:` disagrees with the ids it carries."""
        document = (
            f"# DOJOONPASS Company History — 2026-08-{day:02d}\n"
            "\n## Items\n"
            "\n### Something happened\n"
            "- Event ID: E1\n"
            "- Owner: CTO Backend\n"
            "\n## Metadata\n"
            "\n"
            f"- History Date: 2026-08-{day:02d}\n"
            f"- Generated At: 2026-08-{day:02d}T20:00:00+09:00\n"
            "- Source: DOJOONPASS Company Ops\n"
            "- Event Count: 3\n"
        )
        (runtime / "local_master" / "daily" / f"2026-08-{day:02d}.md").write_text(
            document, encoding="utf-8"
        )

    def _render(self, module):
        import contextlib

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(
                datetime(2026, 8, 21, 9, 0).astimezone()
            )
        return buffer.getvalue(), attention

    def test_the_premise_is_real(self):
        """Guards the guard: if the fixture were not miscounted the bound
        below would be measuring an empty list."""
        module = self._load()
        runtime = self._runtime(module)
        self._miscounted_day(runtime, 5)

        printed, _attention = self._render(module)

        self.assertIn("Daily 항목 불일치", printed)

    def test_the_printed_list_does_not_grow_with_the_days(self):
        module = self._load()
        runtime = self._runtime(module)
        for day in range(1, 9):
            self._miscounted_day(runtime, day)

        printed, _attention = self._render(module)
        per_day = [
            line for line in printed.splitlines()
            if "Daily 항목 불일치" in line and "외" not in line
        ]

        self.assertEqual(len(per_day), module._RECENT_ON_SCREEN)

    def test_the_cut_says_how_many_it_cut(self):
        """A shorter list that does not say it is shorter is the failure this
        file spends most of its length avoiding."""
        module = self._load()
        runtime = self._runtime(module)
        for day in range(1, 9):
            self._miscounted_day(runtime, day)

        printed, _attention = self._render(module)
        overflow = [
            line for line in printed.splitlines()
            if "Daily 항목 불일치" in line and "외" in line
        ]

        self.assertEqual(len(overflow), 1, printed)
        self.assertIn("총 8건", overflow[0])
        self.assertIn("3건", overflow[0], "the omitted count")

    def test_a_short_list_is_printed_in_full(self):
        """The control. Below the bound nothing changes."""
        module = self._load()
        runtime = self._runtime(module)
        for day in range(1, 4):
            self._miscounted_day(runtime, day)

        printed, _attention = self._render(module)
        lines = [line for line in printed.splitlines() if "Daily 항목 불일치" in line]

        self.assertEqual(len(lines), 3)
        self.assertNotIn("외", "\n".join(lines))

    def test_the_attention_line_still_names_every_one_of_them(self):
        """Bounding the screen must not shorten the count ATTENTION reports —
        that line was already correct and is what the operator acts on."""
        module = self._load()
        runtime = self._runtime(module)
        for day in range(1, 9):
            self._miscounted_day(runtime, day)

        _printed, attention = self._render(module)
        line = [a for a in attention if "자기 숫자가 어긋난 날" in a]

        self.assertEqual(len(line), 1, attention)
        self.assertIn("8건", line[0])


class TheAttentionBlockCannotPushItselfOffTheTopTests(unittest.TestCase):
    """`_RECENT_ON_SCREEN` bounds the ACTIVITY and COMPLETIONS lists, and its
    note gives the reason: "a screen where one section can push the ATTENTION
    block off the top is a screen nobody scrolls back up".

    ATTENTION was the one list with no such bound. `_print_control_tower()`
    appended one message per `RISKS` row, and RISKS carries one row per
    role-mismatched **Event** — so the block written to say "사람이 지금 할 일"
    grew linearly with the problem it was reporting.

    Measured before the bound, mismatched Events against ATTENTION lines:

          1 ->    3        60 ->   62
         10 ->   12     1,000 -> ~1,002

    The trigger is ordinary. One Desktop configured with the wrong `role`
    makes every Event it sends a mismatch — `validate_event()` checks
    `source` and `role` separately and never the pair — so this is a
    misconfiguration, not an exotic input, and each of the thousand lines is
    the same long paragraph with a different id in it.

    Bounded per kind, with the true total named. The number and the "총 N건"
    disclosure are the ones the loop above already uses; nothing here is a
    new display policy.
    """

    def _load(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_attn", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _runtime(self, module):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)
        return module.RUNTIME_DIR

    @staticmethod
    def _mismatch(runtime, index):
        """One Event whose Desktop does not own the role it claims."""
        payload = {
            "schema_version": "1.0",
            "event_id": f"MM{index}",
            "timestamp": f"2026-08-{1 + index % 20:02d}T10:00:00+09:00",
            "source": "DESKTOP_1",
            "role": "CMO",
            "project_id": f"PRJ{index}",
            "event_type": "STARTED",
            "status": "IN_PROGRESS",
            "milestone": None,
            "summary": "work",
            "blocker": None,
            "evidence": [],
            "history_candidate": True,
        }
        (runtime / "events" / "processed" / f"MM{index}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    @staticmethod
    def _blocked(runtime, index):
        payload = {
            "schema_version": "1.0",
            "event_id": f"BB{index}",
            "timestamp": f"2026-08-{1 + index % 20:02d}T11:00:00+09:00",
            "source": "DESKTOP_2",
            "role": "CMO",
            "project_id": f"BLK{index}",
            "event_type": "BLOCKED",
            "status": "BLOCKED",
            "milestone": None,
            "summary": "work",
            "blocker": "waiting on someone",
            "evidence": [],
            "history_candidate": True,
        }
        (runtime / "events" / "processed" / f"BB{index}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _attention(self, module):
        import contextlib

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            return module._print_control_tower(
                datetime(2026, 8, 21, 9, 0).astimezone()
            )

    def test_the_block_does_not_grow_with_the_event_count(self):
        module = self._load()
        runtime = self._runtime(module)
        for index in range(60):
            self._mismatch(runtime, index)

        attention = self._attention(module)
        per_event = [a for a in attention if "role이 어긋난 Event:" in a]

        self.assertEqual(len(per_event), module._RISKS_IN_ATTENTION)
        self.assertLess(len(attention), 10, attention)

    def test_the_true_total_is_named_rather_than_dropped(self):
        """A shorter list that does not say it is shorter is the failure this
        whole file is about."""
        module = self._load()
        runtime = self._runtime(module)
        for index in range(60):
            self._mismatch(runtime, index)

        attention = self._attention(module)
        summary = [a for a in attention if "총 60건" in a]

        self.assertEqual(len(summary), 1, attention)
        self.assertIn("55건", summary[0], "the omitted count is stated")

    def test_a_short_list_is_not_truncated_and_says_no_total(self):
        """The control. Below the bound the block is exactly what it was."""
        module = self._load()
        runtime = self._runtime(module)
        for index in range(3):
            self._mismatch(runtime, index)

        attention = self._attention(module)

        self.assertEqual(len([a for a in attention if "role이 어긋난 Event:" in a]), 3)
        self.assertEqual([a for a in attention if "총 3건" in a], [])

    def test_one_flood_does_not_crowd_out_the_other_kind(self):
        """Bounded per kind on purpose. A role-mismatch flood must not push
        every open Blocker out of the block — they are different problems and
        an operator needs to see that both exist."""
        module = self._load()
        runtime = self._runtime(module)
        for index in range(40):
            self._mismatch(runtime, index)
        for index in range(2):
            self._blocked(runtime, index)

        attention = self._attention(module)

        self.assertEqual(
            len([a for a in attention if "막혀 있는 Project:" in a]),
            2,
            "both blockers survive the mismatch flood",
        )
        self.assertEqual(
            len([a for a in attention if "role이 어긋난 Event:" in a]),
            module._RISKS_IN_ATTENTION,
        )

    def test_the_summary_line_points_at_the_likely_cause(self):
        """The line replaces 55 paragraphs, so it has to be worth more than
        the first of them: one misconfigured Desktop is what produces this
        shape, and the screen says where to look."""
        module = self._load()
        runtime = self._runtime(module)
        for index in range(60):
            self._mismatch(runtime, index)

        summary = [a for a in self._attention(module) if "총 60건" in a][0]

        self.assertIn("role 설정", summary)
        self.assertIn("source", summary)


class CredentialsInDotEnvAreNotCredentialsInTheProcessTests(unittest.TestCase):
    """C90: the screen said 미설정 to an operator who had configured it.

    `.env` is deliberately not auto-loaded — the template says so and this
    project has kept it that way. The cost was invisible: with a valid token
    and database id sitting in `.env`, `NotionConfig.from_env()` raises,
    `run_company_ops.py` prints "Notion 미설정 … 건너뜁니다", and the Run
    Manifest records `notion_sync: SKIPPED`. The NOTION block said nothing.

    Measured on this deployment: `.env` held credentials that worked — they
    were used to write four Project rows into the live PROJECTS database —
    and that database was **15 days behind** the Control Tower, while every
    run reported the same untroubling word.

    "Missing" and "present but not exported" need opposite reactions, and
    only the first is a configuration decision.

    Two rules this must not break: it reports **names, never values**, and
    it follows `RUNTIME_DIR` like everything else in this view.
    """

    def _module(self, root):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_dotenv_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = root / "runtime"
        return module

    def _tree(self, dotenv=None):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "runtime" / "state").mkdir(parents=True)
        if dotenv is not None:
            (root / ".env").write_text(dotenv, encoding="utf-8")
        return root

    FILLED = ("NOTION_API_TOKEN=ntn_" + "A" * 24 + chr(10)
              + "NOTION_PROJECTS_DATABASE_ID=" + "b" * 32 + chr(10))

    def test_a_filled_dotenv_with_nothing_exported_is_reported(self):
        module = self._module(self._tree(self.FILLED))
        with mock.patch.dict(module.os.environ, {}, clear=True):
            self.assertEqual(
                module._notion_credentials_present_but_unexported(),
                ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID"),
            )

    def test_an_exported_variable_is_not_reported(self):
        """The control. Reporting a variable the process already has would
        send an operator to fix something that is not broken."""
        module = self._module(self._tree(self.FILLED))
        with mock.patch.dict(
            module.os.environ,
            {"NOTION_API_TOKEN": "x", "NOTION_PROJECTS_DATABASE_ID": "y"},
            clear=True,
        ):
            self.assertEqual(module._notion_credentials_present_but_unexported(), ())

    def test_a_blank_value_is_not_a_credential(self):
        """`.env.example` ships every key with an empty value. Treating that
        as configured would fire this on every fresh checkout."""
        blank = "NOTION_API_TOKEN=" + chr(10) + "NOTION_PROJECTS_DATABASE_ID=   " + chr(10)
        module = self._module(self._tree(blank))
        with mock.patch.dict(module.os.environ, {}, clear=True):
            self.assertEqual(module._notion_credentials_present_but_unexported(), ())

    def test_no_dotenv_at_all_says_nothing(self):
        module = self._module(self._tree(dotenv=None))
        with mock.patch.dict(module.os.environ, {}, clear=True):
            self.assertEqual(module._notion_credentials_present_but_unexported(), ())

    def test_comments_are_not_settings(self):
        """Recorded rather than guarded. A commented line parses to the name
        `"# NOTION_API_TOKEN"`, which is not one of the two required names,
        so a `startswith("#")` check cannot change the answer — a mutation
        removing it failed nothing. Both spellings are asserted here so the
        property is pinned without keeping an unreachable branch."""
        commented = ("# NOTION_API_TOKEN=ntn_commented_out" + chr(10)
                     + "#NOTION_PROJECTS_DATABASE_ID=abc" + chr(10))
        module = self._module(self._tree(commented))
        with mock.patch.dict(module.os.environ, {}, clear=True):
            self.assertEqual(module._notion_credentials_present_but_unexported(), ())

    def test_it_never_returns_a_value_only_a_name(self):
        """The rule that must not rot. This function touches the one file in
        the tree that holds a real credential."""
        secret = "ntn_" + "Z" * 24
        module = self._module(self._tree("NOTION_API_TOKEN=" + secret + chr(10)))
        with mock.patch.dict(module.os.environ, {}, clear=True):
            result = module._notion_credentials_present_but_unexported()

        self.assertEqual(result, ("NOTION_API_TOKEN",))
        for item in result:
            self.assertNotIn(secret, item)

    def test_it_follows_runtime_dir_and_not_the_frozen_project_root(self):
        """The defect this function shipped with for one test run. Reading
        `PROJECT_ROOT / .env` made twelve existing tests fail at once —
        every one a fixture with no `.env` reading the repository's real
        one. C31 again, and C88 had just added the gate for it."""
        module = self._module(self._tree(dotenv=None))
        with mock.patch.dict(module.os.environ, {}, clear=True):
            self.assertEqual(module._notion_credentials_present_but_unexported(), ())

    def test_an_unreadable_dotenv_does_not_break_the_block(self):
        """A status view must not fail because a file it merely hoped for
        cannot be read."""
        root = self._tree(dotenv=None)
        (root / ".env").mkdir()
        module = self._module(root)
        with mock.patch.dict(module.os.environ, {}, clear=True):
            self.assertEqual(module._notion_credentials_present_but_unexported(), ())

class AuthoredTextIsBoundedAsWellAsRedactedTests(unittest.TestCase):
    """C80: C71 bounded how MANY risk lines reach ATTENTION, not how LONG one is.

    `_RISKS_IN_ATTENTION` caps the RISKS panel at five lines per kind, and
    the comment above it explains why: a block that tells an operator what to
    do now is useless when it cannot be read. Five is a small number
    multiplied by an unbounded one — nothing bounds `blocker`, `summary` or
    `project_id`. docs/02 gives them no maximum and `validate_event()` only
    type-checks (`test_notion_sync.py` asserts exactly that, from the other
    side).

    Measured before this, three blocked Projects each carrying a
    100,000-character `blocker`: three ATTENTION lines of 100,176 characters
    — on the screen, and in the file a scheduled run redirects its output to.

    `_authored()` is where this belongs because it is already the one place
    an Event-authored value is prepared for a human to read, and
    `oplog.bounded()` is already the cap this project chose for the same
    shape (`append_line()`: nothing logged is unbounded).
    """

    def _authored(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import ops_status

        return ops_status._authored

    def test_a_short_value_is_untouched(self):
        """The control. A bound that shortened ordinary values would be
        visible in every message this project prints."""
        authored = self._authored()

        self.assertEqual(authored("SEARCH_BACKEND"), "SEARCH_BACKEND")

    def test_an_unbounded_value_is_cut_to_the_projects_own_cap(self):
        from oplog import MAX_LOG_ERROR

        authored = self._authored()

        result = authored("A" * 100_000)

        self.assertLessEqual(len(result), MAX_LOG_ERROR + 3)
        self.assertTrue(result.endswith("..."))

    def test_the_cut_happens_after_redaction_and_not_before(self):
        """Order matters, and the assertion has to be about a **fragment**.

        `bounded(redact(x))` redacts the whole string and then cuts, so a
        credential anywhere in it is already `[REDACTED]`.
        `redact(bounded(x))` cuts first, and a credential straddling the
        boundary is split -- `ntn_A` survives, because `SECRET_PATTERNS`
        needs ten characters after the prefix to recognise one.

        The first draft of this test asserted `assertNotIn(secret, result)`
        and **passed under both orders**: the full 28-character string is
        absent either way, since one of them cut it in half. A mutation
        swapping the two was what showed that -- the test was decorative for
        the property it was named after. The fragment is the subject.
        """
        from oplog import MAX_LOG_ERROR

        secret = "ntn_" + "A" * 24
        authored = self._authored()

        # A space before it: `SECRET_PATTERNS` anchors on ``, so a prefix
        # glued to a preceding word is a different case (not this one's).
        result = authored("x" * (MAX_LOG_ERROR - 6) + " " + secret + " tail")

        # The fragment is the whole subject: under the wrong order the tail
        # reads `... ntn_A...`, which is the front of a live credential.
        self.assertNotIn(secret, result)
        self.assertNotIn("ntn_", result)

        # `[REDACTED]` is deliberately NOT asserted on that string: the
        # marker itself lands on the boundary and comes back as `[REDA...`.
        # Asserted here instead, where the secret sits well inside the cap,
        # so "redaction happened" is checked rather than assumed.
        inside = authored(f"waiting for {secret} to be rotated")

        self.assertIn("[REDACTED]", inside)
        self.assertNotIn("ntn_", inside)

    def test_a_newline_still_cannot_forge_a_line(self):
        """The bound must not displace the other two rules `_authored()`
        applies."""
        authored = self._authored()

        self.assertNotIn("\n", authored("a\nb"))

class AnIdIsAlsoAuthoredTextTests(unittest.TestCase):
    """C47: the ATTENTION sink's stated reason for not redacting was wrong.

    `main()` applies `one_line()` to every ATTENTION message and deliberately
    not `redact()`, because "almost every message is built from filenames, ids
    and counts -- never from a file's contents". That rests on an id being
    machine-made. It is not: `event_id` and `project_id` are plain strings a
    Desktop sets for itself, `validate_event()` only type-checks them, and the
    Agent's own scan never sees an Event that came from somewhere else.

    Found by a test written for something else -- the new secret report
    asserted it does not print the string it found, and the orphan line two
    blocks above printed the same Event's id raw, into the console and into
    the file a scheduled run redirects its output to.

    Stated here as one property over the blocks that print an id, rather than
    as one case each: a secret-shaped identifier must appear nowhere an
    operator or a log file can see it.
    """

    SECRET = "ghp_" + "Q7wE9rT2yU4iO6pA8sD0fG"

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_authored", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _runtime(self, module):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)
        return module.RUNTIME_DIR

    def _event(self, **overrides):
        payload = {
            "schema_version": "1.0",
            "event_id": "E1",
            "timestamp": "2026-08-09T10:00:00+09:00",
            "source": "DESKTOP_2",
            "role": "CMO",
            "project_id": "PRJ",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "milestone": "M1",
            "summary": "work",
            "blocker": None,
            "evidence": [],
            "history_candidate": True,
        }
        payload.update(overrides)
        return payload

    def _capture(self, block, now):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = block(now)
        return buffer.getvalue(), list(attention or ())

    def test_the_helper_is_one_line_and_redact(self):
        """One rule, one place. Restating either half at a call site is how
        thirteen sites drift into twelve."""
        module = self._load_entrypoint()

        self.assertEqual(module._authored("a\nb"), one_line("a\nb"))
        self.assertEqual(module._authored(f"tok {self.SECRET}"), "tok [REDACTED]")

    def test_an_orphaned_event_named_after_a_token_is_redacted(self):
        """The exact case that was leaking: an Event in `processed/` with no
        Candidate, whose id is the credential."""
        module = self._load_entrypoint()
        runtime = self._runtime(module)
        (runtime / "events" / "processed" / "e.json").write_text(
            json.dumps(self._event(event_id=self.SECRET)), encoding="utf-8"
        )

        printed, attention = self._capture(
            module._print_history, datetime(2026, 8, 12, 9, 0).astimezone()
        )

        self.assertIn("ORPHANED_EVENT", printed)
        self.assertTrue(any("History에 들어가지 못한" in item for item in attention))
        self.assertNotIn(self.SECRET, printed)
        self.assertNotIn(self.SECRET, "\n".join(attention))

    def test_a_control_tower_project_named_after_a_token_is_redacted(self):
        module = self._load_entrypoint()
        runtime = self._runtime(module)
        (runtime / "events" / "processed" / "e.json").write_text(
            json.dumps(
                self._event(
                    project_id=self.SECRET, event_type="BLOCKED", status="BLOCKED",
                    blocker="waiting", milestone=None,
                )
            ),
            encoding="utf-8",
        )

        printed, attention = self._capture(
            module._print_control_tower, datetime(2026, 8, 12, 9, 0).astimezone()
        )

        self.assertIn("BLOCKED", printed)
        self.assertTrue(any("막혀 있는 Project" in item for item in attention))
        self.assertNotIn(self.SECRET, printed)
        self.assertNotIn(self.SECRET, "\n".join(attention))

    def test_a_role_mismatch_on_a_token_named_event_is_redacted(self):
        module = self._load_entrypoint()
        runtime = self._runtime(module)
        (runtime / "events" / "processed" / "e.json").write_text(
            json.dumps(self._event(event_id=self.SECRET, source="DESKTOP_1", role="CMO")),
            encoding="utf-8",
        )

        printed, attention = self._capture(
            module._print_control_tower, datetime(2026, 8, 12, 9, 0).astimezone()
        )

        self.assertTrue(any("role이 어긋난" in item for item in attention))
        self.assertNotIn(self.SECRET, printed)
        self.assertNotIn(self.SECRET, "\n".join(attention))

    def test_the_retry_queue_entry_is_redacted(self):
        module = self._load_entrypoint()
        runtime = self._runtime(module)
        (runtime / "state" / "notion_retry_queue.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "event_id": self.SECRET,
                            "added_at": "2026-07-01T09:00:00+09:00",
                            "attempt_count": 9,
                            "last_error": "boom",
                            "payload": {},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        printed, attention = self._capture(
            module._print_notion, datetime(2026, 8, 12, 9, 0).astimezone()
        )

        self.assertNotIn(self.SECRET, printed)
        self.assertNotIn(self.SECRET, "\n".join(attention))

    def test_no_block_prints_a_secret_shaped_id_anywhere(self):
        """The property, over every block that takes `now` and prints. One
        Event carrying the same string in each authored field at once: if any
        block grows a new line quoting one of them, this fails without anyone
        remembering to add a case."""
        module = self._load_entrypoint()
        runtime = self._runtime(module)
        (runtime / "events" / "processed" / "e.json").write_text(
            json.dumps(
                self._event(
                    event_id=self.SECRET, project_id=self.SECRET,
                    milestone=self.SECRET, summary=f"see {self.SECRET}",
                    event_type="BLOCKED", status="BLOCKED", blocker=self.SECRET,
                    evidence=[self.SECRET],
                )
            ),
            encoding="utf-8",
        )
        now = datetime(2026, 8, 12, 9, 0).astimezone()

        for name in (
            "_print_company", "_print_history", "_print_notion",
            "_print_control_tower",
        ):
            with self.subTest(block=name):
                printed, attention = self._capture(getattr(module, name), now)

                self.assertNotIn(self.SECRET, printed)
                self.assertNotIn(self.SECRET, "\n".join(attention))

class OneEventInTwoFilesIsOneLineTests(unittest.TestCase):
    """Two ATTENTION lines in this view walked `processed/` file by file and
    then showed the first five. `processed/` can hold two files for one
    `event_id`.

    That is not a corruption case. This view already reports it as
    `중복 파일` in the COMPANY and CONTROL TOWER blocks, C51 settled how
    those two should count ("위 숫자는 Event당 한 번만 센다"), and the
    deployment runtime is in that state right now -- `dup-bypass.json` and
    `f75edf1b-….json` are the same Event, so the real screen printed
    `Event 16건` in two blocks and `Event 17건` in a third.

    C51's sweep reached two of the four readers of that directory. These are
    the other two, and on them the cost is not a disagreeing number:

        orphan line   6 Events lost, 6 copies of one of them ->
                      "Event 11건" and one id five times.
                      EVT-REALLY-LOST-0..4 named **nowhere** on the page
        secret line   6 leaked credentials, 6 copies of one ->
                      "11건" and one id five times.
                      Five live credentials named **nowhere**

    Both lines end in an instruction about a thing rather than a file --
    "사람이 확인해야 한다" and "자격증명을 **교체**해야 한다" -- so an
    operator who follows them rotates one credential and leaves five live.
    """

    SECRET_PREFIX = "ntn_"

    def _load_entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c77", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _runtime(self, module):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in (
            "events/transport", "events/incoming", "events/processed",
            "events/rejected", "history_candidates/keep",
            "history_candidates/review", "local_master/daily",
            "local_master/monthly", "state", "locks", "runs", "logs",
        ):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)
        return module.RUNTIME_DIR

    def _event(self, **overrides):
        payload = {
            "schema_version": "1.0",
            "event_id": "E1",
            "timestamp": "2026-08-09T10:00:00+09:00",
            "source": "DESKTOP_2",
            "role": "CMO",
            "project_id": "PRJ",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "milestone": "M1",
            "summary": "work",
            "blocker": None,
            "evidence": [],
            "history_candidate": True,
        }
        payload.update(overrides)
        return payload

    def _capture(self, block, now):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = block(now)
        return buffer.getvalue(), list(attention or ())

    def _crowded_processed(self, module, *, secret=False):
        """One Event in six files, plus five genuinely different ones.

        The duplicated id sorts first on purpose: the bug is that its copies
        take every slot, and a fixture where it sorted last would pass
        against the broken code.
        """
        runtime = self._runtime(module)
        processed = runtime / "events" / "processed"

        def payload(event_id, index):
            extra = (
                {"summary": f"deploy {self.SECRET_PREFIX}0123456789ab{index}"}
                if secret else {}
            )
            return json.dumps(self._event(event_id=event_id, **extra))

        duplicated = payload("EVT-AAA-DUPLICATED", 0)
        for i in range(6):
            (processed / f"aaa-copy-{i}.json").write_text(duplicated, encoding="utf-8")
        for i in range(5):
            (processed / f"zzz-{i}.json").write_text(
                payload(f"EVT-REALLY-LOST-{i}", i + 1), encoding="utf-8"
            )
        return runtime

    #: `2026-08-12`, after the fixture's Events, so nothing is future-dated.
    NOW = datetime(2026, 8, 12, 9, 0)

    def test_the_fixture_really_is_eleven_files_and_six_events(self):
        """Guards the guard. Every assertion below is about the difference
        between those two numbers, and a fixture that lost it would make them
        all pass for the wrong reason."""
        module = self._load_entrypoint()
        runtime = self._crowded_processed(module)
        processed = runtime / "events" / "processed"

        files = sorted(processed.glob("*.json"))
        ids = {json.loads(p.read_text(encoding="utf-8"))["event_id"] for p in files}

        self.assertEqual(len(files), 11)
        self.assertEqual(len(ids), 6)

    def test_every_lost_event_gets_a_line_of_its_own(self):
        """The orphan block. Five slots, five **different** Events."""
        module = self._load_entrypoint()
        self._crowded_processed(module)

        printed, attention = self._capture(
            module._print_history, self.NOW.astimezone()
        )

        listed = [
            line.split("!")[1].split("[")[0].strip()
            for line in printed.splitlines()
            if line.strip().startswith("!")
        ]
        self.assertEqual(
            len(listed), len(set(listed)),
            f"the same Event is listed more than once: {listed}",
        )
        self.assertIn("EVT-AAA-DUPLICATED", listed)
        self.assertTrue(
            any(name.startswith("EVT-REALLY-LOST") for name in listed),
            f"a genuinely lost Event was crowded out by copies of another: {listed}",
        )

    def test_the_orphan_alert_counts_events_and_still_shows_the_files(self):
        module = self._load_entrypoint()
        self._crowded_processed(module)

        printed, attention = self._capture(
            module._print_history, self.NOW.astimezone()
        )
        line = next(item for item in attention if "History에 들어가지 못한" in item)

        self.assertIn("Event 6건", line)
        self.assertIn("파일 11건", line, "the file count must stay visible")
        self.assertNotIn("Event 11건", line)
        self.assertIn("EVT-REALLY-LOST", line)

    def test_the_coverage_count_says_files_because_that_is_what_it_counts(self):
        """`checked` is `len(paths)`. The COMPANY and CONTROL TOWER blocks
        print `Event N건` meaning distinct Events, so this line calling the
        file count `Event` put two meanings of one word on one screen --
        differing by exactly the duplicate count."""
        module = self._load_entrypoint()
        self._crowded_processed(module)

        printed, _ = self._capture(module._print_history, self.NOW.astimezone())
        line = next(l for l in printed.splitlines() if "Candidate 정합성" in l)

        self.assertIn("파일 11건 확인", line)
        self.assertNotIn("Event 11건", line)

    def test_every_leaked_credential_gets_a_slot(self):
        """The security half, and the reason it is the worse of the two: the
        alert's own instruction is to **rotate** the credential."""
        module = self._load_entrypoint()
        self._crowded_processed(module, secret=True)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module.main()
        text = buffer.getvalue()
        line = next(
            (l for l in text.splitlines() if "Secret 형태의 문자열" in l), ""
        )

        self.assertTrue(line, "the secret alert did not fire at all")
        self.assertIn("문자열 6건", line)
        self.assertIn("파일 11건", line)
        self.assertIn("EVT-REALLY-LOST", line)
        self.assertEqual(
            line.count("EVT-AAA-DUPLICATED"), 1,
            "one credential, named once",
        )

    def test_nothing_is_added_when_there_are_no_duplicates(self):
        """The qualifier has to mean something. A line that always carried
        "파일 N건" would be noise, and a reader would stop reading it."""
        module = self._load_entrypoint()
        runtime = self._runtime(module)
        for i in range(3):
            (runtime / "events" / "processed" / f"e{i}.json").write_text(
                json.dumps(self._event(event_id=f"EVT-{i}")), encoding="utf-8"
            )

        printed, attention = self._capture(
            module._print_history, self.NOW.astimezone()
        )
        line = next(item for item in attention if "History에 들어가지 못한" in item)

        self.assertIn("Event 3건", line)
        self.assertNotIn("파일", line)
        self.assertIn("파일 3건 확인", printed)

    def test_the_fold_keeps_the_first_of_each_and_the_order(self):
        """`_one_per_event()` on its own. Order is the operator-visible part
        -- the list is truncated at five, so a fold that reordered would
        change which Events are shown."""
        module = self._load_entrypoint()
        rows = [("a", 1), ("b", 2), ("a", 3), ("c", 4), ("b", 5)]

        folded = module._one_per_event(rows, lambda row: row[0])

        self.assertEqual(folded, [("a", 1), ("b", 2), ("c", 4)])
        self.assertEqual(module._one_per_event([], lambda row: row), [])



class ASignalWrittenAfterItsDateClosedIsCountedTests(unittest.TestCase):
    """C95. The other half of the class below, and this one needs no
    mistake by anybody.

    `ASignalNoDateWillEverReadIsCountedTests` is about a Signal filed where
    no target date will look: the top level, an unpadded date, a name that
    is not a date. Every case there starts with somebody typing the path
    wrong.

    This one starts with nothing wrong at all. `catchup.pending_dates()`
    ends at **yesterday** and never walks backwards (docs/07 section 50), so
    the moment the watermark reaches a date, a Signal added to that date's
    directory afterwards is never read again -- by any run, ever. The
    directory name is right, the filename is right, the content is valid.

    **Measured through the real entrypoint, `agent.run_once()`:**

        08:00  the scheduled run collects 2026-08-23   watermark 2026-08-23
        09:00  the person writes up the afternoon into
               signals/2026-08-23/afternoon.json
        09:00  run 2                 COMPLETED   delivered: still 1
        +1 day / +2 days, 2 runs     COMPLETED   delivered: still 1

        never delivered, never rejected, never named in the agent log
        outbox_count 0   rejected_signal_count 0   unreachable_signal_count 0
        pending_dates ()   needs_attention ()

    Writing up yesterday after this morning's run is the shape of an
    ordinary working day, and Signal authoring is by hand (BACKLOG A-11).
    What is lost is something a person typed, and every diagnostic read
    all-clear.

    **Counted, not repaired.** Re-reading a closed date re-derives Events
    the Collector has already seen, and `pending_dates()`' refusal to walk
    backwards is a deliberate rule with its own reasons. What a late Signal
    *means* is a decision (BACKLOG). That it is there is not.
    """

    def _agent_tree(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        agent = root / "agent"
        for rel in ("signals", "signals_rejected", "outbox", "sent", "state"):
            (agent / rel).mkdir(parents=True)
        return agent

    def _signal(self, path, summary="typed by a person"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "event_type": "MILESTONE_COMPLETED", "project_id": "PRJ",
                "status": "IN_PROGRESS", "summary": summary,
                "milestone": "M", "history_candidate": True,
            }),
            encoding="utf-8",
        )
        return path

    def _mark_delivered(self, agent, day, stem, source="DESKTOP_1"):
        """Through the same two functions the delivery path uses, so the
        test cannot agree with a spelling production does not use (C28)."""
        from agent.agent import derive_event_id
        from agent.outbox import safe_event_filename

        event_id = derive_event_id(source=source, target_date=day, signal_id=stem)
        (agent / "sent" / safe_event_filename(event_id)).write_text(
            "{}", encoding="utf-8"
        )

    def _count(self, agent, *, source="DESKTOP_1", collected_through=date(2026, 8, 23)):
        from agent.status import _count_undelivered_signals_in_closed_dates

        return _count_undelivered_signals_in_closed_dates(
            agent / "signals",
            agent / "sent",
            source=source,
            collected_through=collected_through,
        )

    # ---- the defect ------------------------------------------------------

    def test_a_signal_added_after_its_date_closed_is_counted(self):
        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-23" / "afternoon.json")

        self.assertEqual(self._count(agent), 1)

    def test_a_delivered_signal_on_the_same_closed_date_is_not_counted(self):
        """The half that must not fire, and the reason the count is exact
        rather than "how many files are still lying there": `load_signals()`
        never moves a Signal, so a collected one stays on disk. Being in
        `signals/` is normal; being in `signals/` *and* not in `sent/` is
        not."""
        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-23" / "standup.json")
        self._mark_delivered(agent, date(2026, 8, 23), "standup")

        self.assertEqual(self._count(agent), 0)

    def test_both_at_once(self):
        """The measured scenario: one collected, one written afterwards."""
        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-23" / "standup.json")
        self._mark_delivered(agent, date(2026, 8, 23), "standup")
        self._signal(agent / "signals" / "2026-08-23" / "afternoon.json")

        self.assertEqual(self._count(agent), 1)

    # ---- what must NOT be counted ----------------------------------------

    def test_a_date_the_watermark_has_not_reached_is_not_counted(self):
        """Still pending -- the next run reads it. Counting it would put a
        permanent alert on every machine that has today's Signals ready,
        which is the standing alarm this file keeps removing."""
        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-24" / "tomorrow.json")

        self.assertEqual(self._count(agent), 0)

    def test_a_first_ever_run_counts_nothing(self):
        """No watermark means no date is closed. A machine that has never
        collected must not report every Signal it is holding."""
        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-23" / "s.json")

        self.assertEqual(self._count(agent, collected_through=None), 0)

    def test_an_unknown_desktop_id_counts_nothing(self):
        """The id is half of `derive_event_id()`. Without it the predicate
        would be guessing, and a guess here reports work as lost that is
        sitting in `sent/` under a different id."""
        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-23" / "s.json")

        self.assertEqual(self._count(agent, source=None), 0)

    def test_a_misfiled_signal_belongs_to_the_other_counter(self):
        """The two counters partition the problem rather than overlap it.

        A `*.json` outside a valid date directory is
        `_count_unreachable_signals()`' subject, and reporting it twice
        would be the second opinion this project keeps removing (C28).

        The directories are C84's own catalogue, and they are the input that
        makes `_is_date_directory_name()` load-bearing rather than tidy:
        without it this function reaches `date.fromisoformat("august-21")`
        and raises. A first draft of this test used a top-level *file*,
        which the `is_dir()` branch already skips -- so a mutation deleting
        the filter passed. That is what the mutation matrix is for.
        """
        from agent.status import _count_unreachable_signals

        agent = self._agent_tree()
        self._signal(agent / "signals" / "toplevel.json")
        self._signal(agent / "signals" / "august-21" / "s.json")
        self._signal(agent / "signals" / "2026-8-21" / "s.json")

        self.assertEqual(self._count(agent), 0)
        self.assertEqual(_count_unreachable_signals(agent / "signals"), 3)

    # ---- through the real entrypoint -------------------------------------

    def test_the_real_agent_loses_it_and_the_counter_sees_it(self):
        """End to end, with `agent.run_once()` and a real transport.

        The assertion that matters is the pair: the Agent reports COMPLETED
        every time and the Signal is never delivered.
        """
        from agent.agent import run_once
        from agent.state import load_state
        from transport.onedrive import OneDriveTransport

        agent = self._agent_tree()
        root = agent.parent
        kw = dict(
            signals_dir=agent / "signals",
            rejected_signals_dir=agent / "signals_rejected",
            outbox_dir=agent / "outbox",
            sent_dir=agent / "sent",
            state_path=agent / "state" / "agent_state.json",
            lock_path=root / "locks" / "agent.lock",
            log_path=root / "logs" / "agent.log",
        )
        yesterday = date(2026, 8, 23)
        morning = datetime(2026, 8, 24, 8, 0).astimezone()
        transport = OneDriveTransport(root / "sync")

        self._signal(agent / "signals" / "2026-08-23" / "standup.json", "shipped it")
        first = run_once(transport=transport, agent_start_date=yesterday,
                         profile="DESKTOP_1", now=morning, **kw)
        self.assertEqual(first.status.value, "COMPLETED")
        self.assertEqual(len(list((agent / "sent").glob("*.json"))), 1)

        # ...and now the person writes up the afternoon.
        self._signal(agent / "signals" / "2026-08-23" / "afternoon.json", "closed it")
        for extra in range(3):
            later = run_once(
                transport=transport, agent_start_date=yesterday,
                profile="DESKTOP_1", now=morning + timedelta(days=extra), **kw
            )
            self.assertEqual(later.status.value, "COMPLETED")

        # Never delivered, by three more runs that all reported success.
        self.assertEqual(len(list((agent / "sent").glob("*.json"))), 1)
        self.assertFalse(list((agent / "signals_rejected").rglob("*.json")))
        self.assertNotIn(
            "afternoon", (root / "logs" / "agent.log").read_text(encoding="utf-8")
        )

        watermark = load_state(kw["state_path"]).last_successful_collection_date
        self.assertEqual(
            self._count(agent, collected_through=watermark),
            1,
            "the one thing that noticed",
        )

    def test_the_snapshot_carries_it_and_the_old_fields_still_read_clear(self):
        """The whole point: every other field says all-clear. If any of them
        had reported this, the counter would be a second opinion."""
        from agent.status import read_status

        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-23" / "afternoon.json")
        (agent / "state" / "agent_state.json").write_text(
            json.dumps({
                "desktop_id": "DESKTOP_1",
                "last_successful_collection_date": "2026-08-23",
                "last_run": "2026-08-24T08:00:00+09:00",
            }),
            encoding="utf-8",
        )

        snapshot = read_status(
            agent_start_date=date(2026, 8, 23),
            now=datetime(2026, 8, 24, 9, 0).astimezone(),
            state_path=agent / "state" / "agent_state.json",
            outbox_dir=agent / "outbox",
            sent_dir=agent / "sent",
            rejected_signals_dir=agent / "signals_rejected",
            signals_dir=agent / "signals",
        )

        self.assertEqual(snapshot.undelivered_closed_signal_count, 1)
        self.assertEqual(snapshot.outbox_count, 0)
        self.assertEqual(snapshot.rejected_signal_count, 0)
        self.assertEqual(snapshot.unreachable_signal_count, 0)
        self.assertEqual(snapshot.pending_dates, ())

    def test_the_operator_screen_prints_it_and_raises_attention(self):
        """A count that is computed and never shown is not a detector.

        C84's sibling test states the rule; C90's M4a/M4b mutations are why
        both halves are pinned here -- the line and the ATTENTION entry can
        be lost independently, and each of them alone is the whole fix.
        """
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c95", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in ("agent/signals", "agent/signals_rejected", "agent/outbox",
                    "agent/sent", "agent/state", "state", "logs", "runs",
                    "locks", "local_master/daily", "events/processed"):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)
        agent = module.RUNTIME_DIR / "agent"
        (agent / "state" / "agent_state.json").write_text(
            json.dumps({
                "desktop_id": "DESKTOP_1",
                "last_successful_collection_date": "2026-08-23",
                "last_run": "2026-08-24T08:00:00+09:00",
            }),
            encoding="utf-8",
        )
        now = datetime(2026, 8, 24, 9, 0).astimezone()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            clean = list(module._print_agent(now) or ())
        self.assertIn("\uc9c0\ub09c \ub0a0\uc9dc\uc758 \ubbf8\uc804\ub2ec  : 0", buffer.getvalue())
        self.assertFalse(
            [item for item in clean if "\ubbf8\uc804\ub2ec Signal" in item],
            "a clean tree raised the alert",
        )

        self._signal(agent / "signals" / "2026-08-23" / "afternoon.json")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            dirty = list(module._print_agent(now) or ())

        self.assertIn("\uc9c0\ub09c \ub0a0\uc9dc\uc758 \ubbf8\uc804\ub2ec  : 1", buffer.getvalue())
        alert = [item for item in dirty if "\ubbf8\uc804\ub2ec Signal" in item]
        self.assertEqual(len(alert), 1, dirty)
        # It has to say what to do, not only that something is wrong.
        self.assertIn("\uc218\uc9d1\ub418\uc9c0 \uc54a\uc740 \ub0a0\uc9dc", alert[0])


class TheDeliveredSetIsTheSameQuestionIsSentAsksTests(unittest.TestCase):
    """C101. The counter batches `sent/`, and this is what keeps that honest.

    `_count_undelivered_signals_in_closed_dates()` asked
    `outbox.is_sent(event_id, sent_dir)` once per Signal, which is one
    `is_file()` each. Measured on this machine, warm:

        signals    per-Signal is_sent    one directory listing
            200          5.9 ms                 2.6 ms
          1,000         27.9 ms                13.0 ms
          5,000        139.1 ms                59.1 ms
         20,000        559.5 ms               243.6 ms

    28 us per Signal down to 12 -- on a script whose premise is that a
    person runs it first, casually, and whose whole run is ~230 ms on a
    healthy tree. At three years of Signals the old form was 140 ms of that
    by itself.

    **Batching a predicate is how a second opinion gets made** (C28), so the
    two halves are pinned here rather than assumed:

      * the *name* still comes from `safe_event_filename()`, the function
        `is_sent()` itself calls -- not from a copy of its rule;
      * `is_file()` is still the test, not `exists()`. `is_sent()`'s own
        docstring records the measurement behind that: a **directory**
        carrying an Event's name made it answer True, which is the Agent
        declining to send an Event it never sent. A set built from a bare
        `scandir` would have re-introduced exactly that.

    If `is_sent()` ever becomes more than "the file named
    `safe_event_filename(event_id)` exists", the first test below fails and
    points at this counter.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.signals = self.root / "signals"
        self.sent = self.root / "sent"
        self.signals.mkdir()
        self.sent.mkdir()
        self.day = date(2026, 8, 23)

    def _signal(self, stem):
        directory = self.signals / self.day.isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{stem}.json").write_text(
            json.dumps({
                "event_type": "MILESTONE_COMPLETED", "project_id": "PRJ",
                "status": "IN_PROGRESS", "summary": "typed by a person",
                "milestone": "M", "history_candidate": True,
            }),
            encoding="utf-8",
        )

    def _filename_for(self, stem):
        from agent.agent import derive_event_id
        from agent.outbox import safe_event_filename

        return safe_event_filename(
            derive_event_id(source="DESKTOP_1", target_date=self.day, signal_id=stem)
        )

    def _count(self):
        from agent.status import _count_undelivered_signals_in_closed_dates

        return _count_undelivered_signals_in_closed_dates(
            self.signals, self.sent, source="DESKTOP_1", collected_through=self.day
        )

    def test_is_sent_is_still_only_that_file_existing(self):
        """The coupling, stated as behaviour rather than as a hope."""
        from agent.outbox import is_sent
        from agent.agent import derive_event_id

        event_id = derive_event_id(
            source="DESKTOP_1", target_date=self.day, signal_id="s"
        )
        self.assertFalse(is_sent(event_id, self.sent))

        (self.sent / self._filename_for("s")).write_text("{}", encoding="utf-8")

        self.assertTrue(is_sent(event_id, self.sent))

    def test_the_batched_set_agrees_with_is_sent_on_a_delivered_signal(self):
        self._signal("s")
        self.assertEqual(self._count(), 1)

        (self.sent / self._filename_for("s")).write_text("{}", encoding="utf-8")

        self.assertEqual(self._count(), 0)

    def test_a_directory_wearing_the_name_is_not_a_delivery(self):
        """The measurement `is_sent()`'s docstring carries, applied to the
        batched form. A set built from a bare `scandir` would call this
        delivered and the lost Signal would go unreported."""
        from agent.outbox import is_sent
        from agent.agent import derive_event_id

        self._signal("s")
        (self.sent / self._filename_for("s")).mkdir()

        self.assertFalse(
            is_sent(
                derive_event_id(
                    source="DESKTOP_1", target_date=self.day, signal_id="s"
                ),
                self.sent,
            )
        )
        self.assertEqual(self._count(), 1, "a directory counted as a delivery")

    def test_an_absent_sent_directory_means_nothing_was_delivered(self):
        """A fact, not a failure -- on a machine that has never delivered,
        every closed-date Signal really is undelivered."""
        self._signal("s")
        self.sent.rmdir()

        self.assertEqual(self._count(), 1)

    def test_an_entry_that_cannot_be_stat_ed_is_not_a_delivery(self):
        """A refusal is not evidence. Treating an unstat-able entry as a
        delivered Event would hide the lost Signal it is named after, which
        is the one direction this family of counters must never fail in.

        Injected: a real `DirEntry` does not refuse `is_file()` on demand,
        the same reasoning `ADetectorSaysWhatItCouldNotCheckTests` gives for
        its junction probe.
        """
        import agent.status as status_module

        self._signal("s")
        (self.sent / self._filename_for("s")).write_text("{}", encoding="utf-8")
        self.assertEqual(self._count(), 0)  # control: it IS delivered

        real = os.scandir

        class Refusing:
            def __init__(self, entry):
                self.name = entry.name

            def is_file(self, *args, **kwargs):
                raise PermissionError(13, "Access is denied")

        class RefusingScandir:
            def __init__(self, entries):
                self._entries = iter(entries)

            def __iter__(self):
                return self._entries

            def __next__(self):
                return next(self._entries)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def close(self):
                return None

        def wrap(path, *args, **kwargs):
            if Path(path) == self.sent:
                return RefusingScandir([Refusing(e) for e in real(path)])
            return real(path, *args, **kwargs)

        with mock.patch.object(status_module.os, "scandir", wrap):
            self.assertEqual(
                self._count(), 1, "an entry that refused counted as delivered"
            )

    def test_the_name_comes_from_the_namer_rather_than_a_copy(self):
        """Structural on purpose, and the mutation matrix is why.

        Every `event_id` this counter sees is a uuid5 from
        `derive_event_id()`, and `safe_event_filename()` is a no-op on those
        -- so a mutation replacing the call with `f"{event_id}.json"` passes
        every behavioural test here, because no input can tell them apart.
        The claim is not about a value this can produce; it is that the
        counter asks the **namer** rather than restating its rule, so a
        namer that starts shortening or escaping is followed rather than
        drifted from (C28).
        """
        import ast
        import inspect

        import agent.status as status_module

        source = inspect.getsource(
            status_module._count_undelivered_signals_in_closed_dates
        )
        called = {
            node.func.id
            for node in ast.walk(ast.parse(source.lstrip()))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertIn("safe_event_filename", called)
        self.assertIn("derive_event_id", called)

    def test_a_sent_directory_that_cannot_be_listed_makes_no_claim(self):
        """The other half. Reporting every Signal would be a false alarm the
        size of the tree; reporting none says nothing, which is the honest
        answer to "I could not look"."""
        import agent.status as status_module

        self._signal("s")
        real = os.scandir

        def refusing(path, *args, **kwargs):
            if Path(path) == self.sent:
                raise PermissionError(13, "Access is denied")
            return real(path, *args, **kwargs)

        with mock.patch.object(status_module.os, "scandir", refusing):
            self.assertEqual(self._count(), 0)


class ASignalNoDateWillEverReadIsCountedTests(unittest.TestCase):
    """`load_signals()` reads exactly `signals/<YYYY-MM-DD>/*.json`. A Signal
    filed anywhere else under `signals/` is not queued — it is unreachable.

    **Measured with the real entrypoint (C84).** The same Signal content
    filed four ways, one `run_agent.py` run:

        signals/2026-08-21/s.json   COLLECTED, delivered to the sync folder
        signals/toplevel.json       never read
        signals/2026-8-21/s.json    never read   (unpadded month/day)
        signals/august-21/s.json    never read

    The three that were never read were **not moved, not rejected, not
    logged**. The run printed `COMPLETED` and exited 0, and
    `last_successful_collection_date` advanced to 2026-08-23 — *past* the
    date the work belonged to — so no later run reconsiders it. Every field
    of the Agent snapshot said all-clear: `rejected_signal_count=0`,
    `outbox_count=0`, `pending_dates=()`.

    What is lost is something a person typed. Signal authoring is not
    automated (BACKLOG A-11), so filing one a directory too high is the
    ordinary mistake rather than an exotic one.

    **This class is about the count, not about a repair.** Collecting such a
    file, or moving it to `signals_rejected/`, decides what a misfiled Signal
    *means*, and that is a decision. Counting it is the move C19's
    `is_locked`, C22's review counter, C23's stale lock and C24's
    `name_collision` all made.

    **And it is not the `pending_signals` count `agent/status.py` refuses.**
    That refusal is about *parsing* every Signal to judge validity, which is
    the Agent's job. This is a directory listing: nothing is opened.
    """

    def _agent_tree(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        agent = root / "agent"
        for rel in ("signals", "signals_rejected", "outbox", "sent", "state"):
            (agent / rel).mkdir(parents=True)
        return agent

    @staticmethod
    def _signal(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "event_type": "MILESTONE_COMPLETED", "project_id": "PRJ",
                "status": "IN_PROGRESS", "summary": "typed by a person",
                "milestone": "M", "history_candidate": True,
            }),
            encoding="utf-8",
        )

    def _count(self, agent):
        from agent.status import _count_unreachable_signals

        return _count_unreachable_signals(agent / "signals")

    def test_a_correctly_filed_signal_is_not_counted(self):
        """The half that must not fire. `load_signals()` is deliberately
        side-effect free and never moves a Signal, so a collected one stays
        on disk — "still in `signals/`" is normal, and only the unreachable
        *location* is the signal."""
        agent = self._agent_tree()
        self._signal(agent / "signals" / "2026-08-21" / "s.json")

        self.assertEqual(self._count(agent), 0)

    def test_each_way_of_filing_it_out_of_reach_is_counted(self):
        agent = self._agent_tree()
        cases = {
            "top level": agent / "signals" / "toplevel.json",
            "unpadded date": agent / "signals" / "2026-8-21" / "s.json",
            "not a date": agent / "signals" / "august-21" / "s.json",
            "nested below a date": agent / "signals" / "2026-08-21" / "sub" / "s.json",
        }
        for label, path in cases.items():
            with self.subTest(filed=label):
                self._signal(path)
                self.assertEqual(
                    self._count(agent), 1,
                    f"a Signal filed {label} is unreachable and was not counted",
                )
                path.unlink()

    def test_a_date_shaped_name_the_agent_never_builds_is_still_unreachable(self):
        """`date.fromisoformat()` alone is too generous: on this interpreter
        it accepts `20260821` and `2026-W34-5`. `load_signals()` builds the
        directory it reads with `target_date.isoformat()`, which is always
        `YYYY-MM-DD`, so the round trip is the test rather than the parse."""
        from agent.status import _is_date_directory_name

        for name in ("2026-08-21",):
            with self.subTest(name=name):
                self.assertTrue(_is_date_directory_name(name))
        for name in ("20260821", "2026-W34-5", "2026-8-21", "august-21", ""):
            with self.subTest(name=name):
                self.assertFalse(_is_date_directory_name(name))

    def test_a_missing_or_unreadable_signals_directory_answers_zero(self):
        """A read-only diagnostic must not become the thing that fails.

        **The injection has to hit what the implementation calls (C87).** The
        first version of this test patched `Path.rglob`, which is what C84's
        counter used. C87 rewrote the counter on `os.scandir` for cost, and
        this test kept passing — over a tree with nothing in it, so 0 was
        the honest answer and the error path was never entered. A patched
        function nobody calls is the same vacuous pass as an empty scan.

        So the tree now holds an unreachable Signal, which makes 0 the *wrong*
        answer unless the failure is handled, and the patch is on `os.scandir`.
        """
        import os as os_module

        from agent import status as status_module
        from agent.status import _count_unreachable_signals

        agent = self._agent_tree()
        self.assertEqual(_count_unreachable_signals(agent / "nope"), 0)

        # With this file present, a working scan answers 1 -- so a 0 below can
        # only come from the error path, not from an empty directory.
        self._signal(agent / "signals" / "toplevel.json")
        self.assertEqual(_count_unreachable_signals(agent / "signals"), 1)

        real = status_module.os.scandir

        def boom(*args, **kwargs):
            raise PermissionError(13, "Access is denied")

        status_module.os = type(
            "_os", (), {"scandir": staticmethod(boom)}
        )()
        try:
            self.assertEqual(_count_unreachable_signals(agent / "signals"), 0)
        finally:
            status_module.os = os_module
        # and it still works afterwards, so the patch really was the cause
        self.assertEqual(_count_unreachable_signals(agent / "signals"), 1)

    def test_the_snapshot_carries_it_and_defaults_to_zero(self):
        """The field is defaulted so a caller that does not pass
        `signals_dir` keeps working rather than raising."""
        from agent.status import read_status

        agent = self._agent_tree()
        self._signal(agent / "signals" / "toplevel.json")

        without = read_status(
            state_path=agent / "state" / "agent_state.json",
            outbox_dir=agent / "outbox",
            sent_dir=agent / "sent",
            rejected_signals_dir=agent / "signals_rejected",
            signals_dir=agent / "signals",
        )
        self.assertEqual(without.unreachable_signal_count, 1)

    def test_the_operator_screen_prints_it_and_raises_attention(self):
        """The whole point: the number has to reach the screen an operator
        reads first (AGENT.md), and a non-zero one has to reach ATTENTION."""
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c84", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in ("agent/signals", "agent/signals_rejected", "agent/outbox",
                    "agent/sent", "agent/state", "state", "logs", "runs",
                    "locks", "local_master/daily", "events/processed"):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)

        now = datetime(2026, 8, 24, 9, 0).astimezone()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            clean = list(module._print_agent(now) or ())
        self.assertIn("읽힐 수 없는 Signal : 0", buffer.getvalue())
        self.assertFalse(
            [item for item in clean if "수집되지 않는 Signal" in item],
            "a clean tree raised the alert",
        )

        self._signal(module.RUNTIME_DIR / "agent" / "signals" / "toplevel.json")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            dirty = list(module._print_agent(now) or ())
        self.assertIn("읽힐 수 없는 Signal : 1", buffer.getvalue())
        alert = [item for item in dirty if "수집되지 않는 Signal" in item]
        self.assertEqual(len(alert), 1, dirty)
        self.assertIn("signals/<YYYY-MM-DD>", alert[0])


class ADirectoryTheScanCannotListCountsRatherThanVanishesTests(unittest.TestCase):
    """C102. Three of the four "cannot list this" paths answered **zero**.

    `_count_unreachable_signals()`'s own docstring promises *"Errors count
    rather than vanish, in both directions"*, and the branch for a date
    directory that will not list spells out why:

        "`continue`ing in silence here would have made the number *smaller*
         for a tree that got harder to read -- the direction that reads as
         reassurance"

    The same function's `_all_json_below()` did exactly that. `except
    OSError: return total` handed back whatever had been counted so far,
    which for a directory that refuses on the first call is 0.

    A line-coverage pass is what found it: every one of these branches was
    unexecuted, so the direction claim above was prose in all four places
    and code in one. Measured, three misfiled Signals with `os.scandir()`
    refusing one directory:

        whole non-date dir unlistable        healthy 3  ->  0
        subtree under a non-date dir          healthy 3  ->  0
        a nested dir inside a date dir        healthy 3  ->  0
        a date dir itself                     healthy 0  ->  1

    What an operator saw for the first three: `읽힐 수 없는 Signal : 0` and
    no ATTENTION — for Signals no date will ever read, in a directory
    nothing can look inside. Same shape as C62, C68 and C101's M5.

    Reachable without anything exotic. `signals/` is a directory a person
    files into by hand (BACKLOG A-11), and on Windows a restored ACL, an
    antivirus scanner or a sync client holding a handle all produce exactly
    this — as does the directory being removed between the parent's listing
    and this call.

    The root of `signals/` is deliberately NOT changed and still answers 0:
    there the *existence* of anything at all is unknown, so 1 would be a
    fabricated count rather than a refused one.
    `ASignalNoDateWillEverReadIsCountedTests::
    test_a_missing_or_unreadable_signals_directory_answers_zero` pins that,
    and this class pins the difference.
    """

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.signals = root / "agent" / "signals"
        self.signals.mkdir(parents=True)

    @staticmethod
    def _signal(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "event_type": "MILESTONE_COMPLETED", "project_id": "PRJ",
                "status": "IN_PROGRESS", "summary": "typed by a person",
                "milestone": "M", "history_candidate": True,
            }),
            encoding="utf-8",
        )

    def _count(self):
        from agent.status import _count_unreachable_signals

        return _count_unreachable_signals(self.signals)

    @contextlib.contextmanager
    def _refusing(self, directory):
        """`os.scandir()` refuses exactly `directory`, delegating otherwise.

        Narrow on purpose. `Path.glob`, `shutil.rmtree` and the temp-file
        machinery all reach `os.scandir` too, and a blanket refusal would
        make the assertion below true for a reason that has nothing to do
        with this function.
        """
        import agent.status as status_module

        real = os.scandir

        def refusing(path, *args, **kwargs):
            if Path(path) == Path(directory):
                raise PermissionError(13, "Access is denied")
            return real(path, *args, **kwargs)

        with mock.patch.object(status_module.os, "scandir", refusing):
            yield

    def test_each_unlistable_directory_is_counted_instead_of_dropped(self):
        """The measurement, all three shapes. The fixture holds THREE
        Signals so a healthy scan answers 3 — a 0 can then only come from
        the error path, and a 1 can only come from the refusal being
        counted. (C87's lesson: an injection over an empty tree proves
        nothing, because 0 was already the honest answer.)"""
        # Relative parts, resolved AFTER `setUp()` rebuilds the tree — a
        # first draft computed the paths once, against the temp directory of
        # the *previous* subtest, and every fixture assertion read 0.
        cases = {
            "a non-date directory": ("august-21",),
            "a subtree below a non-date directory": ("august-21", "sub"),
            "a directory nested inside a date directory": ("2026-08-21", "sub"),
        }
        for label, parts in cases.items():
            with self.subTest(unlistable=label):
                self.setUp()
                directory = self.signals.joinpath(*parts)
                for index in range(3):
                    self._signal(directory / f"s{index}.json")
                self.assertEqual(
                    self._count(), 3, "the fixture itself is wrong"
                )

                with self._refusing(directory):
                    self.assertEqual(
                        self._count(), 1,
                        "a directory that cannot be listed was reported as "
                        "nothing to look at",
                    )

    def test_it_never_answers_zero_where_a_working_scan_answers_more(self):
        """The direction, stated as the property rather than as a number.
        Any future re-implementation may count differently; none of them may
        count *down to nothing*."""
        directory = self.signals / "august-21"
        for index in range(3):
            self._signal(directory / f"s{index}.json")

        with self._refusing(directory):
            refused = self._count()

        self.assertGreater(refused, 0)
        self.assertLessEqual(refused, 3)

    def test_a_date_directory_that_will_not_list_still_counts_one(self):
        """The one branch that was already right, pinned so the fix above
        cannot be undone by making all four consistent in the wrong
        direction."""
        directory = self.signals / "2026-08-21"
        self._signal(directory / "s.json")
        self.assertEqual(self._count(), 0, "a filed Signal is reachable")

        with self._refusing(directory):
            self.assertEqual(self._count(), 1)

    def test_a_healthy_tree_is_untouched_by_the_change(self):
        """The false-alarm direction. Widening a data-loss counter without
        pinning this is how C26's "alert nobody reads" gets built."""
        self._signal(self.signals / "2026-08-21" / "s.json")
        self._signal(self.signals / "2026-08-22" / "s.json")

        self.assertEqual(self._count(), 0)

    def test_a_signal_nested_two_levels_below_a_non_date_directory_is_counted(self):
        """`_all_json_below()`'s own recursion, which nothing executed. The
        existing cases all stop one level down, so the line that descends
        was covered by no test at all."""
        self._signal(self.signals / "august-21" / "a" / "b" / "s.json")

        self.assertEqual(self._count(), 1)

    def test_an_entry_whose_type_cannot_be_read_is_counted_at_every_depth(self):
        """`entry.is_dir()` raising is a third shape, and it has its own
        `+= 1` at three separate depths. Injected on the DirEntry rather
        than on `scandir`, because that is the call that fails."""
        import agent.status as status_module

        deep = self.signals / "august-21" / "sub"
        inside_date = self.signals / "2026-08-21" / "sub"
        top = self.signals / "2026-08-22"
        for directory in (deep, inside_date):
            self._signal(directory / "s.json")
        top.mkdir(parents=True, exist_ok=True)

        refuse_for = {str(deep), str(inside_date), str(top)}
        real = os.scandir

        class _Entry:
            def __init__(self, entry):
                self.name = entry.name
                self.path = entry.path
                self._entry = entry

            def is_dir(self, *args, **kwargs):
                if self.path in refuse_for:
                    raise PermissionError(13, "Access is denied")
                return self._entry.is_dir(*args, **kwargs)

        @contextlib.contextmanager
        def wrapping(path, *args, **kwargs):
            with real(path, *args, **kwargs) as entries:
                yield [_Entry(entry) for entry in entries]

        with mock.patch.object(status_module.os, "scandir", wrapping):
            counted = self._count()

        self.assertEqual(counted, 3, "an entry of unknown type was dropped")


class TheClosedDateCounterErrorPathsActuallyRunTests(unittest.TestCase):
    """C102, the sibling. Six branches of
    `_count_undelivered_signals_in_closed_dates()` had never executed.

    The two counters in `agent/status.py` are one family and they make the
    same promise — a refused entry counts, it never quietly becomes a clean
    one. `ADirectoryTheScanCannotListCountsRatherThanVanishesTests` found
    that promise broken in the other one, in the branch whose neighbour
    argued *for* it. So this one was measured rather than read.

    **All six are correct, and this class is what makes that a measurement
    instead of a reading.** Every number below was taken by injection
    against a closed date holding three undelivered Signals — a fixture that
    answers 3 when nothing is refused, so neither 0 nor a coincidental 1
    could be mistaken for a pass:

        a date entry whose is_dir() refuses      healthy 3  ->  1
        a date directory that will not list      healthy 3  ->  1
        two of three signal is_file() refuse     healthy 3  ->  3
        a FILE named like a date directory       0 (it is not a date dir)
        a non-.json file in a closed date        1 (only the .json counts)
        a DIRECTORY named `x.json`               1 (is_file(), not exists())

    C101 closed this shape once already, on the `sent/` side
    (`_entry_is_file()`, its M5). The `signals/` side is the other half and
    it was still prose.
    """

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.agent = root / "agent"
        for rel in ("signals", "sent"):
            (self.agent / rel).mkdir(parents=True)
        self.closed = self.agent / "signals" / "2026-08-21"

    @staticmethod
    def _signal(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "event_type": "MILESTONE_COMPLETED", "project_id": "PRJ",
                "status": "IN_PROGRESS", "summary": "typed by a person",
                "milestone": "M", "history_candidate": True,
            }),
            encoding="utf-8",
        )

    def _count(self):
        from agent.status import _count_undelivered_signals_in_closed_dates

        return _count_undelivered_signals_in_closed_dates(
            self.agent / "signals",
            self.agent / "sent",
            source="DESKTOP_1",
            collected_through=date(2026, 8, 22),
        )

    def _three_undelivered(self):
        for index in range(3):
            self._signal(self.closed / f"s{index}.json")
        self.assertEqual(self._count(), 3, "the fixture itself is wrong")

    @contextlib.contextmanager
    def _refusing_scandir(self, directory):
        import agent.status as status_module

        real = os.scandir

        def refusing(path, *args, **kwargs):
            if Path(path) == Path(directory):
                raise PermissionError(13, "Access is denied")
            return real(path, *args, **kwargs)

        with mock.patch.object(status_module.os, "scandir", refusing):
            yield

    @contextlib.contextmanager
    def _refusing_type_of(self, *paths):
        """`DirEntry.is_dir()` / `is_file()` refuse for exactly these paths.

        Wrapping the entries rather than the call, because the entry is what
        fails: an antivirus or an ACL answers the listing and then refuses
        the stat behind one name in it.
        """
        import agent.status as status_module

        refuse = {str(Path(p)) for p in paths}
        real = os.scandir

        class _Entry:
            def __init__(self, entry):
                self.name = entry.name
                self.path = entry.path
                self._entry = entry

            def is_dir(self, *args, **kwargs):
                if self.path in refuse:
                    raise PermissionError(13, "Access is denied")
                return self._entry.is_dir(*args, **kwargs)

            def is_file(self, *args, **kwargs):
                if self.path in refuse:
                    raise PermissionError(13, "Access is denied")
                return self._entry.is_file(*args, **kwargs)

        class _Listing(list):
            """Iterable *and* a context manager. This function reads
            `sent/` with `with os.scandir(...)` and the date directories
            with `list(os.scandir(...))`, so a stand-in that supports only
            one of the two fails for a reason the test has no opinion
            about."""

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        def wrapping(path, *args, **kwargs):
            with real(path, *args, **kwargs) as entries:
                return _Listing(_Entry(entry) for entry in entries)

        with mock.patch.object(status_module.os, "scandir", wrapping):
            yield

    def test_a_date_entry_whose_type_cannot_be_read_is_counted(self):
        self._three_undelivered()

        with self._refusing_type_of(self.closed):
            self.assertEqual(self._count(), 1)

    def test_a_closed_date_directory_that_will_not_list_is_counted(self):
        self._three_undelivered()

        with self._refusing_scandir(self.closed):
            self.assertEqual(self._count(), 1)

    def test_a_signal_whose_type_cannot_be_read_is_counted_as_undelivered(self):
        """The `sent/` half of this is C101's M5. This is the `signals/`
        half: an entry that cannot be stat-ed is not evidence that the
        Signal was delivered."""
        self._three_undelivered()

        with self._refusing_type_of(self.closed / "s0.json", self.closed / "s1.json"):
            self.assertEqual(self._count(), 3)

    def test_a_file_named_like_a_date_directory_is_not_a_closed_date(self):
        """It is not a date *directory*, so `load_signals()` will never read
        it and this counter is not the one that owns it — the same split the
        `continue` above it names."""
        (self.agent / "signals" / "2026-08-21").write_text("{}", encoding="utf-8")

        self.assertEqual(self._count(), 0)

    def test_a_directory_that_is_not_a_date_belongs_to_the_other_counter(self):
        """The two counters partition the tree and neither may double-report.

        Added because the mutation that deletes the `_is_date_directory_name`
        guard survived every other test in this class — the file-shaped case
        above falls through the `is_dir()` check anyway, so it proved
        nothing about the guard.

        Both shapes matter and they fail differently without it. `2026-8-21`
        is accepted by `date.fromisoformat()` on this interpreter, so it
        would silently become a second report of a Signal
        `_count_unreachable_signals()` already owns. `august-21` is not, so
        it would raise `ValueError` out of a read-only status view.
        """
        for name in ("2026-8-21", "august-21"):
            with self.subTest(directory=name):
                self.setUp()
                self._signal(self.agent / "signals" / name / "s.json")

                self.assertEqual(self._count(), 0)

    def test_a_non_json_file_in_a_closed_date_is_not_a_signal(self):
        self._signal(self.closed / "s0.json")
        (self.closed / "notes.txt").write_text("a note", encoding="utf-8")

        self.assertEqual(self._count(), 1)

    def test_a_directory_wearing_a_signal_name_is_not_a_signal(self):
        """`is_file()`, not `exists()` — the same rule `is_sent()` records on
        the delivery side, applied on the reading side."""
        self._signal(self.closed / "s0.json")
        (self.closed / "dir.json").mkdir()

        self.assertEqual(self._count(), 1)


class TheDecisionContextAlertDoesNotStopAtTheReassuringHalfTests(unittest.TestCase):
    """The alert for reviewed-but-unrendered Decision Context used to end
    "유실은 아니지만" — *not lost, but*.

    Every word of that was true and it was the reassuring half. The content
    sits in `runtime/history_candidates/keep/`, which is:

        a **sibling** of the backup source (`runtime/local_master/`), so no
        Backup scope setting reaches it — docs/08 sections 26-28 sync
        `daily/` and `monthly/` and never mention `history_candidates/`

        under `runtime/`, which `.gitignore` excludes, so the repository does
        not carry it either

    A-14's own table records both rows. So this is one copy on one machine:
    not lost, and not the same as safe. README RULE 11/12 calls Decision
    Context the company's most important asset, and an operator reading
    "유실은 아니다" has been told to stop worrying about it.

    C85 changed no behaviour. The repair is A-14's, every route to it needs a
    decision, and this is the same restraint the alert already practises —
    it says what is true and asks for nothing. What changed is that it now
    says all of what is true.
    """

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c85", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_two_locations_really_are_out_of_reach(self):
        """The premise, checked rather than asserted — if either of these
        ever becomes false, the alert's new half is the thing that is wrong.
        """
        repo = Path(__file__).resolve().parents[1]

        gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("runtime/", gitignore.split())

        spec = (repo / "docs" / "08_BACKUP_SPEC.md").read_text(encoding="utf-8")
        self.assertNotIn("history_candidates", spec)

        module = self._module()
        candidates = module.RUNTIME_DIR / "history_candidates"
        local_master = module.RUNTIME_DIR / "local_master"
        self.assertEqual(candidates.parent, local_master.parent,
                         "they are siblings; neither contains the other")
        self.assertFalse(
            str(candidates).startswith(str(local_master)),
            "a Backup rooted at local_master cannot reach the Candidates",
        )

    def test_the_alert_says_it_is_neither_backed_up_nor_in_the_repository(self):
        source = (
            Path(__file__).resolve().parents[1] / "ops_status.py"
        ).read_text(encoding="utf-8")
        start = source.index("사람이 입력한 Decision Context")
        alert = source[start:start + 1200]

        self.assertIn("Backup 대상도 아니고", alert)
        self.assertIn(".gitignore", alert)
        self.assertIn("A-14", alert)

    def test_it_no_longer_says_plainly_that_nothing_is_lost(self):
        """The exact clause that was doing the reassuring. Pinned as an
        absence, because a future edit that shortens the sentence would
        most naturally shorten it back to this."""
        source = (
            Path(__file__).resolve().parents[1] / "ops_status.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("유실은 아니지만", source)

    def test_the_alert_still_fires_and_still_asks_for_nothing(self):
        """Behaviour is unchanged: same trigger, same restraint. Driven
        through the real block so the wording under test is the wording an
        operator gets.

        The Candidate is written by the real repository and reviewed by the
        real reviewer rather than hand-built as JSON — a first draft wrote
        the file by hand, got the shape wrong, and the view reported it as an
        *unreadable* Candidate instead. A fixture the production loader
        rejects tests the wrong branch.
        """
        from events import create_event
        from history import FileHistoryRepository, HistoryFilter
        from history.review import RepositoryHistoryReviewer

        module = self._module()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in ("events/processed", "history_candidates/keep",
                    "history_candidates/review", "local_master/daily",
                    "local_master/monthly", "state", "logs", "runs", "locks"):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)

        keep_dir = module.RUNTIME_DIR / "history_candidates" / "keep"
        review_dir = module.RUNTIME_DIR / "history_candidates" / "review"
        repository = FileHistoryRepository(keep_dir=keep_dir, review_dir=review_dir)

        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="PRJ",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS",
            summary="s", milestone="M",
            timestamp="2026-08-05T10:00:00+09:00", history_candidate=True,
        )
        candidate = HistoryFilter().evaluate(event).candidate
        repository.save(candidate)
        RepositoryHistoryReviewer(repository).submit_review(
            candidate.history_id, decision_context="why we chose this"
        )

        # The day is already rendered and already carries the event_id —
        # which is exactly what makes the review unreachable.
        (module.RUNTIME_DIR / "local_master" / "daily" / "2026-08-05.md").write_text(
            f"# 2026-08-05\n\n- Event ID: {event.event_id}\n", encoding="utf-8"
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = list(
                module._print_history(datetime(2026, 8, 12, 9, 0).astimezone()) or ()
            )

        alert = [item for item in attention if "Decision Context" in item]
        self.assertEqual(len(alert), 1, attention)
        self.assertIn("Backup 대상도 아니고", alert[0])
        self.assertIn("검토 미반영", buffer.getvalue())


class TwoProjectsUnderOneHistoryHeadingTests(unittest.TestCase):
    """`daily/markdown._display_project_name()` is `.replace("_", " ").title()`
    and it is not injective.

    **Measured end to end (C90).** Three Events, one per spelling of the same
    name, one day:

        Events written              3 distinct project_id
        Control Tower / PROJECTS    3 projects
        Company History             3 sections, all `### Prj Alpha`
        Monthly parser              3 items, **1 distinct project**

    No Event is lost — each is in the Daily file under its own
    `Event ID:` line. What diverges is a number the COO reads: the Control
    Tower says three projects moved and Monthly History says one, about the
    same month.

    **More reachable than E-22.** That entry's `event_id` collision is
    narrowed by construction — the Agent derives `event_id` as a lowercase
    uuid5 and `FORBIDDEN_SIGNAL_FIELDS` stops a Signal from setting it.
    `project_id` has no such narrowing: a person types it on every Signal.

    **Detection only.** Making the transform injective rewrites every
    heading in existing Company History, and keying Monthly on something
    else changes what the Daily document carries (docs/06's format). Both
    are decisions. This class pins the report.
    """

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_c90", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _runtime(self, module):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        module.RUNTIME_DIR = root / "runtime"
        for rel in ("events/processed", "history_candidates/keep",
                    "history_candidates/review", "local_master/daily",
                    "local_master/monthly", "state", "logs", "runs", "locks"):
            (module.RUNTIME_DIR / rel).mkdir(parents=True)
        return module.RUNTIME_DIR

    @staticmethod
    def _event(runtime, event_id, project_id):
        (runtime / "events" / "processed" / f"{event_id}.json").write_text(
            json.dumps({
                "schema_version": "1.0", "event_id": event_id,
                "timestamp": "2026-08-18T10:00:00+09:00",
                "source": "DESKTOP_1", "role": "CTO_BACKEND",
                "project_id": project_id,
                "event_type": "MILESTONE_COMPLETED", "status": "IN_PROGRESS",
                "summary": "s", "milestone": "M", "blocker": None,
                "evidence": [], "history_candidate": True,
            }),
            encoding="utf-8",
        )

    NOW = datetime(2026, 8, 19, 9, 0)

    def _render(self, module):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = list(
                module._print_control_tower(self.NOW.astimezone()) or ()
            )
        return buffer.getvalue(), attention

    def test_the_transform_really_is_not_injective(self):
        """The premise, taken from the renderer rather than restated. If
        `_display_project_name` ever becomes injective this fails, and the
        report below should go with it."""
        from daily.markdown import _display_project_name

        folded = {_display_project_name(p)
                  for p in ("PRJ_ALPHA", "prj_alpha", "Prj_Alpha")}

        self.assertEqual(len(folded), 1, folded)

    def test_a_collision_is_named_on_the_screen_and_in_attention(self):
        module = self._module()
        runtime = self._runtime(module)
        for event_id, project in (("E1", "PRJ_ALPHA"), ("E2", "prj_alpha"),
                                  ("E3", "Prj_Alpha"), ("E4", "OTHER")):
            self._event(runtime, event_id, project)

        printed, attention = self._render(module)

        self.assertIn("한 제목을 공유", printed)
        alert = [item for item in attention if "한 제목" in item or "제목으로" in item]
        self.assertEqual(len(alert), 1, attention)
        for project in ("PRJ_ALPHA", "prj_alpha", "Prj_Alpha"):
            with self.subTest(project_id=project):
                self.assertIn(project, alert[0])
        self.assertNotIn("OTHER", alert[0])

    def test_nothing_is_said_when_every_heading_is_its_own(self):
        """The qualifier has to mean something. Four ordinary project_ids
        must produce no line at all."""
        module = self._module()
        runtime = self._runtime(module)
        for event_id, project in (("E1", "ALPHA"), ("E2", "BETA"),
                                  ("E3", "GAMMA"), ("E4", "DELTA")):
            self._event(runtime, event_id, project)

        printed, attention = self._render(module)

        self.assertNotIn("한 제목을 공유", printed)
        self.assertEqual(
            [item for item in attention if "제목으로" in item], []
        )

    def test_the_underscore_half_is_covered_too(self):
        """`.replace("_", " ")` folds as well as `.title()` does: `PRJ_A` and
        `PRJ A` are two ids and one heading. Named because a reader who only
        knows about case would not expect it."""
        module = self._module()
        runtime = self._runtime(module)
        self._event(runtime, "E1", "PRJ_A")
        self._event(runtime, "E2", "PRJ A")

        printed, attention = self._render(module)

        self.assertIn("한 제목을 공유", printed)

    def test_the_grouping_is_by_heading_not_by_pairs(self):
        """Three spellings are one group of three, not three pairs — the
        count an operator reads has to be "3 ids, 1 heading"."""
        module = self._module()
        runtime = self._runtime(module)
        for event_id, project in (("E1", "PRJ_ALPHA"), ("E2", "prj_alpha"),
                                  ("E3", "Prj_Alpha")):
            self._event(runtime, event_id, project)

        printed, _attention = self._render(module)

        self.assertIn("3개 project_id가 1개 제목으로", printed)

    def test_the_detector_uses_the_renderers_own_transform(self):
        """C28: one opinion about what Company History calls a project. A
        second `.title()` in `ops_status.py` would drift from the renderer
        the day the renderer changes."""
        source = (
            Path(__file__).resolve().parents[1] / "ops_status.py"
        ).read_text(encoding="utf-8")

        self.assertIn("_display_project_name", source)

        # By AST, not by searching the text: the function's own docstring
        # quotes `.title()` where it explains the fold, and a first draft of
        # this assertion tripped on that prose. The claim is about what the
        # code *calls* -- the same correction C86 and C88 already made to two
        # other source-string assertions in this session.
        import ast as ast_module

        tree = ast_module.parse(source)
        function = next(
            node for node in ast_module.walk(tree)
            if isinstance(node, ast_module.FunctionDef)
            and node.name == "_projects_sharing_one_history_heading"
        )
        called = {
            getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            for node in ast_module.walk(function)
            if isinstance(node, ast_module.Call)
        }

        self.assertIn("_display_project_name", called)
        self.assertNotIn("title", called)
        self.assertNotIn("casefold", called)



if __name__ == "__main__":
    unittest.main()
