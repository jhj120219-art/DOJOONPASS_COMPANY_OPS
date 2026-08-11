"""Observability tests: agent/status.py and app/desktop_activity.py.

Both are read-only views over data the system already writes. What matters
about them, and what is pinned here:

    they never write, move, lock, or delete anything
    they still answer when the underlying data is damaged
    a Desktop that has reported nothing is REPORTED, not omitted
    "needs attention" fires on real trouble and stays quiet otherwise
"""

import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import AgentState, save_state  # noqa: E402
from agent.status import read_status  # noqa: E402
from app.desktop_activity import read_company_activity  # noqa: E402
from events import SOURCES, create_event  # noqa: E402

NOW = datetime(2026, 8, 10, 9, 0).astimezone()


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
        for directory, count in ((self.transport, 3), (self.incoming, 2), (self.rejected, 1)):
            directory.mkdir(parents=True, exist_ok=True)
            for index in range(count):
                (directory / f"{index}.json").write_text("{}", encoding="utf-8")

        backlog = self.snapshot().backlog

        self.assertEqual(backlog.awaiting_intake, 3)
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
        import os
        import time

        path = self.processed / f"{event_id}.json"
        when = time.time() - days_ago * 86400
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

        import inspect

        source = inspect.getsource(module.main)
        for name in ("_print_company", "_print_history", "_print_agent"):
            with self.subTest(view=name):
                self.assertIn(f"{name}(now)", source)

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

        self.assertEqual(module._print_history(NOW), [])


if __name__ == "__main__":
    unittest.main()


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
