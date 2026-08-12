"""State Consistency Check tests (docs/10 §46-49).

Detection only — these tests also pin the "never repairs" contract.
"""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scheduler.consistency import (  # noqa: E402
    ConsistencyStatus,
    check_state_consistency,
)
from scheduler.state import (  # noqa: E402
    SchedulerState,
    SchedulerStateError,
    load_state as load_scheduler_state,
    save_state,
)
from agent.signals import SignalError, parse_signal  # noqa: E402
from agent.state import AgentStateError, load_state as load_agent_state  # noqa: E402
from events import validate_event  # noqa: E402
from monthly import (  # noqa: E402
    MonthlyState,
    MonthlyStateError,
    load_state as load_monthly_state,
    parse_month_key,
)
from notion.dashboard_pending import (  # noqa: E402
    DashboardPendingError,
    load_pending as load_dashboard_pending,
)
from notion.retry_queue import (  # noqa: E402
    RetryQueueError,
    load_queue as load_retry_queue,
)
from runsummary import RunSummaryError, read_summary  # noqa: E402


class StateConsistencyTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.state_path = root / "state" / "daily_history_state.json"
        self.daily_dir = root / "daily"
        self.daily_dir.mkdir(parents=True)

    def _write_state(self, close_date):
        save_state(self.state_path, SchedulerState(last_successful_daily_close=close_date))

    def _write_daily(self, day):
        (self.daily_dir / f"{day.isoformat()}.md").write_text("history", encoding="utf-8")


class ConsistentTests(StateConsistencyTestCase):
    def test_state_matching_an_existing_daily_file_is_consistent(self):
        self._write_state(date(2026, 8, 10))
        self._write_daily(date(2026, 8, 10))

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.CONSISTENT)
        self.assertTrue(result.is_consistent)
        self.assertEqual(result.last_successful_daily_close, date(2026, 8, 10))


class InconsistentTests(StateConsistencyTestCase):
    def test_state_without_its_daily_file_is_flagged(self):
        # docs/10 §47's exact scenario.
        self._write_state(date(2026, 8, 10))

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.STATE_INCONSISTENCY)
        self.assertFalse(result.is_consistent)
        self.assertEqual(result.last_successful_daily_close, date(2026, 8, 10))
        self.assertIn("2026-08-10", str(result.expected_history_path))

    def test_detection_never_creates_or_deletes_anything(self):
        # §46: the program must not regenerate or delete History on its own.
        self._write_state(date(2026, 8, 10))
        self._write_daily(date(2026, 8, 9))
        before = sorted(p.name for p in self.daily_dir.iterdir())
        state_before = self.state_path.read_bytes()

        check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(sorted(p.name for p in self.daily_dir.iterdir()), before)
        self.assertEqual(self.state_path.read_bytes(), state_before)


class MissingOrDamagedStateTests(StateConsistencyTestCase):
    def test_missing_state_file_is_not_an_inconsistency(self):
        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.NO_STATE)

    def test_state_with_no_recorded_close_is_not_an_inconsistency(self):
        save_state(self.state_path, SchedulerState(last_successful_daily_close=None))

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.NO_STATE)

    def test_corrupted_state_is_reported_not_raised(self):
        # §46: a damaged state file is an outcome to report, not a crash.
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not valid json", encoding="utf-8")

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.STATE_UNREADABLE)
        self.assertIsNotNone(result.detail)

    def test_corrupted_state_file_is_left_untouched(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not valid json", encoding="utf-8")

        check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{not valid json")


class SchedulerBehaviourUnchangedTests(StateConsistencyTestCase):
    def test_consistency_module_does_not_import_scheduler_run_once(self):
        # The check must stay a report, not a gate: wiring it into
        # Scheduler's control flow would be a policy change (docs/10 §64
        # makes an inconsistency an operator decision).
        source = (
            Path(__file__).resolve().parents[1] / "src" / "scheduler" / "consistency.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("from .scheduler import", source)
        self.assertNotIn("run_once", source)


class NeverExercisedRejectionTests(unittest.TestCase):
    """Validation branches that no test had ever executed.

    Found by tracing which lines of `src/` the whole suite never runs. What
    came back was a cluster of *rejection* paths — the branches that exist
    precisely to refuse untrusted or damaged input — sitting at 0 executions
    across 1,550 tests.

    That is a different kind of gap from an untested happy path. Every one of
    these guards a boundary: `validate_event()` judges Events that crossed
    OneDrive from another Desktop, `parse_signal()` judges files an operator's
    tooling wrote, and the four state loaders judge files that survive
    crashes and restores. If one of them were inverted or unreachable,
    nothing in the suite would notice — the system would simply start
    accepting something it documents as invalid, quietly.

    Nothing here changes behaviour. Each test asserts the refusal that is
    already written, so that it is a checked fact rather than a believed one.
    """

    # ------------------------------------------------ events/schema.py

    def _event_payload(self, **overrides):
        payload = {
            "schema_version": "1.0",
            "event_id": "EVT-1",
            "timestamp": "2026-08-05T10:00:00+09:00",
            "source": "DESKTOP_1",
            "role": "CTO_BACKEND",
            "project_id": "P",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "summary": "work",
            "history_candidate": True,
        }
        payload.update(overrides)
        return payload

    def test_a_non_string_timestamp_is_rejected(self):
        """The Event arrives as JSON from another Desktop, so `timestamp`
        can be a number, a list, or null — none of which `fromisoformat()`
        would survive if this branch were missing."""
        for value in (1754380800, ["2026-08-05T10:00:00+09:00"], {"at": "now"}, True):
            with self.subTest(timestamp=value):
                errors = validate_event(self._event_payload(timestamp=value))
                self.assertIn("timestamp must be an ISO-8601 string", errors)

    def test_a_non_boolean_history_candidate_is_rejected(self):
        """`"true"` and `1` are the two shapes a hand-written or
        loosely-typed producer actually sends, and both would be truthy if
        this branch were missing — silently promoting an Event into Company
        History that was never meant to be there."""
        for value in ("true", 1, "yes", []):
            with self.subTest(history_candidate=value):
                errors = validate_event(self._event_payload(history_candidate=value))
                self.assertIn("history_candidate must be a boolean", errors)

    def test_evidence_that_is_not_a_list_of_strings_is_rejected(self):
        for value in ("tests PASS", [1, 2], ["ok", None], {"a": "b"}):
            with self.subTest(evidence=value):
                errors = validate_event(self._event_payload(evidence=value))
                self.assertIn("evidence must be a list of strings", errors)

    def test_a_non_string_milestone_or_blocker_is_rejected(self):
        for field_name in ("milestone", "blocker"):
            for value in (1, ["a"], {"x": 1}):
                with self.subTest(field=field_name, value=value):
                    errors = validate_event(self._event_payload(**{field_name: value}))
                    self.assertIn(f"{field_name} must be a string or null", errors)

    def test_a_valid_event_still_passes_every_one_of_those_checks(self):
        """The other half: guards that reject everything are not guards."""
        self.assertEqual(
            validate_event(
                self._event_payload(
                    evidence=["tests PASS"], milestone="M1", blocker=None
                )
            ),
            [],
        )

    # ------------------------------------------------ agent/signals.py

    def _signal(self, payload, *, target=date(2026, 8, 5)):
        return parse_signal(
            json.dumps(payload), signal_id="s", target_date=target, path=Path("s.json")
        )

    def test_a_signal_that_is_not_a_json_object_is_rejected(self):
        for payload in ([1, 2, 3], "a string", 42, None):
            with self.subTest(payload=payload):
                with self.assertRaises(SignalError) as caught:
                    self._signal(payload)
                self.assertIn("must be a JSON object", str(caught.exception))

    def test_a_signal_with_a_non_boolean_history_candidate_is_rejected(self):
        with self.assertRaises(SignalError) as caught:
            self._signal(
                {
                    "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "work",
                    "history_candidate": "true",
                }
            )
        self.assertIn("history_candidate must be a boolean", str(caught.exception))

    def test_a_signal_with_a_non_string_timestamp_is_rejected(self):
        with self.assertRaises(SignalError) as caught:
            self._signal(
                {
                    "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "work",
                    "history_candidate": True,
                    "timestamp": 1754380800,
                }
            )
        self.assertIn("timestamp must be an ISO-8601 string", str(caught.exception))

    def test_a_signal_with_an_unparseable_timestamp_is_rejected(self):
        with self.assertRaises(SignalError) as caught:
            self._signal(
                {
                    "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "work",
                    "history_candidate": True,
                    "timestamp": "2026-08-32T99:00:00+09:00",
                }
            )
        self.assertIn("not valid ISO-8601", str(caught.exception))

    # ------------------------------------------------ the state loaders

    def _write(self, name, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_every_state_loader_refuses_a_json_document_that_is_not_an_object(self):
        """All five state files share one contract (docs/10 §46): a damaged
        file is reported, never deleted. A JSON array parses cleanly, so it
        gets past `json.loads` and only this check stops it."""
        cases = (
            (load_agent_state, AgentStateError, "agent_state.json"),
            (load_monthly_state, MonthlyStateError, "monthly_history_state.json"),
            (load_scheduler_state, SchedulerStateError, "daily_history_state.json"),
            (load_retry_queue, RetryQueueError, "notion_retry_queue.json"),
            (load_dashboard_pending, DashboardPendingError, "dashboard_pending.json"),
            (read_summary, RunSummaryError, "last_run.json"),
        )
        for loader, error, name in cases:
            with self.subTest(loader=loader.__name__):
                path = self._write(name, "[1, 2, 3]")
                with self.assertRaises(error):
                    loader(path)
                # Never deleted — the operator has to be able to look at it.
                self.assertTrue(path.exists())

    def test_the_agent_state_refuses_a_field_of_the_wrong_type(self):
        path = self._write("agent_state.json", json.dumps({"desktop_id": 1}))

        with self.assertRaises(AgentStateError) as caught:
            load_agent_state(path)
        self.assertIn("desktop_id", str(caught.exception))

    def test_the_scheduler_state_refuses_a_field_of_the_wrong_type(self):
        path = self._write(
            "daily_history_state.json",
            json.dumps({"last_successful_daily_close": 20260805}),
        )

        with self.assertRaises(SchedulerStateError):
            load_scheduler_state(path)

    def test_the_monthly_state_refuses_a_malformed_month_key(self):
        for payload in (
            {"last_successful_monthly_close": "2026-8"},
            {"last_successful_monthly_close": 202608},
            {"dirty_months": ["2026-13-01"]},
            {"dirty_months": "2026-07"},
            {"dirty_months": [None]},
        ):
            with self.subTest(payload=payload):
                path = self._write("monthly_history_state.json", json.dumps(payload))
                with self.assertRaises(MonthlyStateError):
                    load_monthly_state(path)

    def test_the_dashboard_pending_queue_refuses_a_non_list_entries_field(self):
        """The sibling of the retry-queue check below. Both queues share the
        same file shape and the same contract; only one of them was covered
        when C22 swept the loaders."""
        path = self._write(
            "dashboard_pending.json", json.dumps({"entries": {"a": 1}})
        )

        with self.assertRaises(DashboardPendingError):
            load_dashboard_pending(path)

    def test_the_retry_queue_refuses_a_non_list_entries_field(self):
        path = self._write(
            "notion_retry_queue.json", json.dumps({"entries": {"a": 1}})
        )

        with self.assertRaises(RetryQueueError):
            load_retry_queue(path)

    def test_the_run_manifest_refuses_a_malformed_component(self):
        """A manifest with a component missing `name`, or carrying a status
        no enum knows, must be reported rather than half-parsed —
        `ops_status.py` prints whatever comes back."""
        for components in (
            [{"status": "SUCCESS"}],
            [{"name": "collector", "status": "NOT_A_STATUS"}],
            [{"name": "collector", "status": "FAILED", "failure": {"severity": "X"}}],
        ):
            with self.subTest(components=components):
                path = self._write(
                    "last_run.json",
                    json.dumps(
                        {
                            "run_id": "R",
                            "started_at": "2026-08-05T11:00:00+09:00",
                            "finished_at": "2026-08-05T11:01:00+09:00",
                            "components": components,
                        }
                    ),
                )
                with self.assertRaises(RunSummaryError):
                    read_summary(path)

    def test_parse_month_key_refuses_anything_that_is_not_yyyy_mm(self):
        for value in ("2026", "2026-8", "26-08", "2026-08-01", "", "abcd-ef"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_month_key(value)

    def test_marking_an_already_dirty_month_reports_no_new_information(self):
        """The return value exists so a caller can skip a pointless save;
        nothing had ever taken the False branch."""
        state = MonthlyState(last_successful_monthly_close="2026-08")

        self.assertTrue(state.mark_dirty("2026-08"))
        self.assertFalse(state.mark_dirty("2026-08"))
        self.assertEqual(state.dirty_months, ["2026-08"])


if __name__ == "__main__":
    unittest.main()
