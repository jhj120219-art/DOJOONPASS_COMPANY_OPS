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
        # A machine that produced Company History also ran Backup: it is a
        # step in the same pipeline, not an optional one, and it writes state
        # on failure as well as success. History with no backup state at all
        # describes a machine where Backup has never run — a real condition,
        # and now a reported one.
        _healthy_backup_state(state)

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

        self.assertEqual(module._secrets_ever_committed(self.wc), ("notes/id_rsa",))

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

        self.assertEqual(module._secrets_ever_committed(self.wc), ())


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
            self._module()._misnamed_scope_directories(master), (("Daily", "daily"),)
        )

    def test_it_covers_every_scope_directory_not_just_daily(self):
        master = self._master("daily", "MONTHLY")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master), (("MONTHLY", "monthly"),)
        )

    def test_both_wrong_at_once_are_both_named(self):
        master = self._master("Daily", "Monthly")

        self.assertEqual(
            self._module()._misnamed_scope_directories(master),
            (("Daily", "daily"), ("Monthly", "monthly")),
        )

    # ---- the false-alarm guard -----------------------------------------

    def test_correctly_named_directories_say_nothing(self):
        master = self._master("daily", "monthly")

        self.assertEqual(self._module()._misnamed_scope_directories(master), ())

    def test_a_legitimately_out_of_scope_directory_is_not_flagged(self):
        """docs/08 §26 marks `decisions/` conditional, not required. Being
        out of scope is not the defect — *looking* in scope is."""
        master = self._master("daily", "monthly", "decisions")

        self.assertEqual(self._module()._misnamed_scope_directories(master), ())

    def test_a_merely_similar_name_is_not_flagged(self):
        master = self._master("daily", "monthly", "dailies")

        self.assertEqual(self._module()._misnamed_scope_directories(master), ())

    def test_a_file_with_a_scope_name_is_not_a_directory_problem(self):
        """Only directories can hold Company History, so only directories are
        diagnosed. Note the fixture cannot also create `monthly/`: on a
        case-insensitive filesystem — the one this defect exists on — a file
        named `Monthly` and a directory named `monthly` are one path."""
        master = self._master("daily")
        (master / "Monthly").write_text("not a directory", encoding="utf-8")

        self.assertEqual(self._module()._misnamed_scope_directories(master), ())

    def test_an_empty_or_missing_master_says_nothing(self):
        module = self._module()

        self.assertEqual(module._misnamed_scope_directories(self._master()), ())
        self.assertEqual(
            module._misnamed_scope_directories(Path(tempfile.mkdtemp()) / "nope"), ()
        )

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


class KeptButNotRenderedTests(unittest.TestCase):
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
        body = [f"# DOJOONPASS Company History — {day}", "", "## Milestones", ""]
        for event_id in event_ids:
            body.append(f"- Event ID: {event_id}")
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
            if "RUNTIME_DIR" in names:
                frozen.extend(
                    t.id for t in node.targets if isinstance(t, ast.Name)
                )

        self.assertEqual(
            frozen,
            [],
            "these freeze a path from RUNTIME_DIR at import time; derive them "
            f"in a function instead (see `_agent_dir()`): {frozen}",
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

    def _lines(self, runtime):
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_junction", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
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
        self.assertTrue(os.path.isjunction(link))
        self.assertTrue(any("linked" in name for name in result.added), result.added)
        self.assertEqual(scan_for_secrets(master), (), "ordinary names are not flagged")

    def test_it_reports_nothing_when_the_platform_cannot_answer(self):
        """`os.path.isjunction()` is Python 3.12+. Older interpreters get
        silence rather than a guess."""
        import importlib.util

        runtime, _outside = self._runtime()
        (runtime / "local_master" / "daily").mkdir()
        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_junction_old", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with mock.patch.object(os.path, "isjunction", None, create=True):
            self.assertEqual(
                module._junctions_in_scope(runtime / "local_master"), ()
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
        return _kept_but_not_rendered(
            tuple((f"s{i}", e, date(2026, 8, 5)) for i, e in enumerate(stored)),
            directory,
        )

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

        return _kept_but_not_rendered(
            ((f"HIST-{event_id}", event_id, date(2026, 8, 5)),), self.daily_dir
        )

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
