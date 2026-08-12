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
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import AgentState, save_state  # noqa: E402
from agent.status import read_status  # noqa: E402
from app.desktop_activity import read_company_activity  # noqa: E402
from app.runner import PIPELINE_COMPONENTS  # noqa: E402
from events import SOURCES, create_event  # noqa: E402
from runsummary import (  # noqa: E402
    ComponentResult,
    ComponentStatus,
    Failure,
    Retryability,
    RunSummary,
    Severity,
    write_summary,
)

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
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_last_run()
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

        original = activity._json_paths

        def _with_a_vanished_entry(directory):
            paths = original(directory)
            if Path(directory) == transport:
                return sorted(paths + [vanished])
            return paths

        activity._json_paths = _with_a_vanished_entry
        self.addCleanup(setattr, activity, "_json_paths", original)

        promotable, unparseable, future_dated, breakdown = activity._count_transport(
            transport
        )

        # The vanished entry is counted as unparseable, not crashed on, and
        # the real file is still attributed.
        self.assertEqual(promotable, 1)
        self.assertEqual(unparseable, 1)
        self.assertEqual(future_dated, 0)
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
