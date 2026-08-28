"""State Consistency Check tests (docs/10 §46-49).

Detection only — these tests also pin the "never repairs" contract.
"""

import ast
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
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

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


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
    """The check must stay a report, not a gate: wiring it into Scheduler's
    control flow would be a policy change (docs/10 §64 makes an inconsistency
    an operator decision).

    Asked of the parsed module, not of its text. This was two `assertNotIn`
    calls against the raw source, and C31 tripped the second one by writing
    the words `scheduler.run_once()` **in a comment** explaining why a
    predicate had changed — the module still imported and called nothing.

    That is this repository's recurring defect in a test rather than in
    production: a substring standing in for a structural question (C30 §5's
    shape, and the reason C29 §3 moved an equivalent check to AST). A test
    that a comment can break teaches people not to write comments, and it can
    fail while the invariant holds — or hold while a `getattr(scheduler,
    "run_" + "once")` slips past it.
    """

    def _tree(self):
        import ast

        return ast.parse(
            (
                Path(__file__).resolve().parents[1]
                / "src"
                / "scheduler"
                / "consistency.py"
            ).read_text(encoding="utf-8")
        )

    def test_the_consistency_module_imports_nothing_from_scheduler(self):
        import ast

        imported = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(f"{node.module}.{alias.name}")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        self.assertNotIn("scheduler.run_once", imported)
        self.assertEqual(
            {name for name in imported if "scheduler" in name.split(".")[-1]},
            set(),
            imported,
        )

    def test_the_consistency_module_calls_nothing_named_run_once(self):
        import ast

        called = set()
        for node in ast.walk(self._tree()):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)

        self.assertNotIn("run_once", called)

    def test_a_comment_mentioning_run_once_does_not_break_the_invariant(self):
        """The regression itself: the module says the words today."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "scheduler" / "consistency.py"
        ).read_text(encoding="utf-8")

        self.assertIn("run_once", source, "this test is pointless if it does not")


class NonFileInTheWayTests(unittest.TestCase):
    """NEW, **P0**. A directory named like a Daily History file made every
    check that guards a missing day agree it was there.

    `exists()` answers "is this name taken". "Is this day of Company History
    written" is a different question, and a directory named `2026-08-12.md`
    answers the first yes and the second no. Four predicates were asking the
    first while meaning the second.

    Measured end to end, one KEEP Candidate waiting for 2026-08-12 and a
    directory of that name in the output directory:

        scheduler.run_once()           COMPLETED
        result.generated_dates         ('2026-08-12', '2026-08-13')
        check_state_consistency()      CONSISTENT
        (daily/'2026-08-12.md').is_file()   False

    The run **claimed to have generated a day it never wrote**, advanced
    `last_successful_daily_close` past it, and left the Candidate
    unreachable — docs/07 §30's "close in order, leave no gap" defeated by a
    gap the loop could not see. The Monthly sibling did the same one
    granularity up: a directory named `2026-08.md` produced
    `MONTHLY_UNCHANGED`, and UNCHANGED advances the catch-up pointer.

    Fixed by asking the question the code means. Nothing changes when the
    artifact is a real file; when it is not, the run stops and says what is
    in the way.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.daily = self.root / "daily"
        self.daily.mkdir()

    def _repository(self):
        from history import HistoryCandidate, HistoryDecision
        from history.file_repository import FileHistoryRepository

        repository = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )
        repository.save(
            HistoryCandidate(
                history_id="HIST-A", event_id="EVT-A",
                timestamp="2026-08-12T10:00:00+09:00", category="MILESTONE",
                project_id="P", role="COO", summary="real work",
                evidence=(), filter_result=HistoryDecision.KEEP,
            )
        )
        return repository

    def test_the_scheduler_does_not_report_a_day_it_did_not_write(self):
        from scheduler import run_once as scheduler_run_once

        (self.daily / "2026-08-12.md").mkdir()

        result = scheduler_run_once(
            self._repository(),
            history_start_date=date(2026, 8, 12),
            now=datetime(2026, 8, 14, 11, 0).astimezone(),
            state_path=self.root / "s.json",
            daily_output_dir=self.daily,
            already_locked=True,
        )

        self.assertEqual(result.generated_dates, ())
        self.assertEqual(result.failed_date, date(2026, 8, 12))
        self.assertIn("non-file is in the way", result.error)

    def test_the_pointer_does_not_advance_past_it(self):
        """The half that made it permanent: once the pointer passes a date,
        no later run reconsiders it."""
        from scheduler import run_once as scheduler_run_once
        from scheduler.state import load_state

        (self.daily / "2026-08-12.md").mkdir()
        state_path = self.root / "s.json"

        scheduler_run_once(
            self._repository(),
            history_start_date=date(2026, 8, 12),
            now=datetime(2026, 8, 14, 11, 0).astimezone(),
            state_path=state_path,
            daily_output_dir=self.daily,
            already_locked=True,
        )

        self.assertIsNone(load_state(state_path).last_successful_daily_close)

    def test_consistency_no_longer_calls_a_directory_a_written_day(self):
        (self.daily / "2026-08-10.md").mkdir()
        state_path = self.root / "s.json"
        state_path.write_text(
            json.dumps({"last_successful_daily_close": "2026-08-10"}), encoding="utf-8"
        )

        result = check_state_consistency(state_path, self.daily)

        self.assertEqual(result.status, ConsistencyStatus.STATE_INCONSISTENCY)

    def test_a_real_file_is_still_a_written_day(self):
        """The guard on the guard — the healthy path is untouched."""
        (self.daily / "2026-08-10.md").write_text("# real", encoding="utf-8")
        state_path = self.root / "s.json"
        state_path.write_text(
            json.dumps({"last_successful_daily_close": "2026-08-10"}), encoding="utf-8"
        )

        self.assertEqual(
            check_state_consistency(state_path, self.daily).status,
            ConsistencyStatus.CONSISTENT,
        )

    def test_the_monthly_sibling_fails_loudly_instead_of_unchanged(self):
        import calendar

        from monthly import MonthlyStatus, consolidate_month

        monthly = self.root / "monthly"
        monthly.mkdir()
        _, last = calendar.monthrange(2026, 8)
        for day in range(1, last + 1):
            (self.daily / f"2026-08-{day:02d}.md").write_text("# H\n", encoding="utf-8")
        (monthly / "2026-08.md").mkdir()

        result = consolidate_month(
            year=2026, month=8, daily_dir=self.daily, monthly_dir=monthly,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 9, 2, 11, 0).astimezone(),
        )

        self.assertIs(result.status, MonthlyStatus.MONTHLY_FAILED)
        self.assertIn("non-file is in the way", result.error)
        # And no staging residue is left behind for C27's checks to find.
        self.assertEqual([p.name for p in monthly.iterdir() if p.name.startswith(".tmp-")], [])

    def test_the_late_update_path_reports_the_right_thing(self):
        """It used to answer `FAILED: [Errno 13] Permission denied` naming a
        temp path — a report about the wrong file entirely."""
        from daily import LateUpdateOutcome, update_daily_history
        from history.file_repository import FileHistoryRepository

        (self.daily / "2026-08-20.md").mkdir()

        result = update_daily_history(
            FileHistoryRepository(
                keep_dir=self.root / "keep", review_dir=self.root / "review"
            ),
            date(2026, 8, 20),
            output_dir=self.daily,
            now=datetime(2026, 9, 2, 11, 0).astimezone(),
        )

        self.assertIs(result.outcome, LateUpdateOutcome.NO_EXISTING_FILE)
        self.assertIsNone(result.error)

    def test_the_orphan_detector_is_not_silenced_by_a_directory(self):
        """A-20's detector, same defect. It asks "was a Candidate written for
        this Event"; a directory carrying the Candidate's name answered "is
        this name taken" instead. Measured: the same genuinely orphaned Event
        reported correctly with nothing there, and reported by nothing once
        the directory existed."""
        from events import create_event
        from history.file_repository import safe_candidate_filename
        from history.reconciliation import find_orphaned_events

        processed, keep, review = (self.root / n for n in ("p", "k", "r"))
        for directory in (processed, keep, review):
            directory.mkdir()
        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="P",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS", summary="s",
            history_candidate=True, event_id="EVT-ORPHAN",
        )
        (processed / "EVT-ORPHAN.json").write_text(event.to_json(), encoding="utf-8")
        (keep / safe_candidate_filename("HIST-EVT-ORPHAN")).mkdir()

        result = find_orphaned_events(
            processed_dir=processed, keep_dir=keep, review_dir=review
        )

        self.assertEqual([o.event_id for o in result.orphaned], ["EVT-ORPHAN"])

    def test_a_real_candidate_file_still_clears_the_orphan_check(self):
        from events import create_event
        from history.file_repository import safe_candidate_filename
        from history.reconciliation import find_orphaned_events

        processed, keep, review = (self.root / n for n in ("p2", "k2", "r2"))
        for directory in (processed, keep, review):
            directory.mkdir()
        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="P",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS", summary="s",
            history_candidate=True, event_id="EVT-OK",
        )
        (processed / "EVT-OK.json").write_text(event.to_json(), encoding="utf-8")
        (keep / safe_candidate_filename("HIST-EVT-OK")).write_text("{}", encoding="utf-8")

        self.assertEqual(
            find_orphaned_events(
                processed_dir=processed, keep_dir=keep, review_dir=review
            ).orphaned,
            (),
        )

    def test_is_sent_does_not_treat_a_directory_as_a_delivered_event(self):
        """`is_sent()` asks "was this Event delivered". A directory of that
        name made it answer yes, which is the Agent deciding not to send an
        Event it never sent — and `sent/` is exactly where the outbox's
        "never lose an Event" guarantee is cashed."""
        from agent.outbox import is_sent
        from reporter.local_output import safe_event_filename

        sent = self.root / "sent"
        sent.mkdir()
        (sent / safe_event_filename("EVT-DIR")).mkdir()
        (sent / safe_event_filename("EVT-FILE")).write_text("{}", encoding="utf-8")

        self.assertFalse(is_sent("EVT-DIR", sent))
        self.assertTrue(is_sent("EVT-FILE", sent))

    def test_name_taken_questions_still_use_exists(self):
        """The other half of the rule, so the sweep is not over-applied.

        `collector/runtime.run_once()`'s destination guard and `intake`'s
        duplicate check ask "is this name taken", and a directory takes a
        name just as firmly as a file does. Narrowing those to `is_file()`
        would let a run try to write over a directory instead of refusing.

        **`outbox.stage()` used to be listed here and was mis-filed.** Its
        early return is not a refusal, it is an "already done, skip" fast
        path, and "already done" has to mean a real Event file — measured,
        a directory named `EVT-1.json` made `stage()` return success having
        written nothing. The fear this test recorded ("would let a run try
        to write over a directory") does not apply: the refusal lives one
        call down in `_write_atomic()`, which still asks `exists()` and
        still refuses. Both are asserted below so the two halves stay
        distinguishable.
        """
        import inspect

        from collector import runtime as collector_runtime
        from reporter import local_output

        self.assertIn(
            "destination.exists()", inspect.getsource(collector_runtime.run_once)
        )
        self.assertIn(
            "final_path.exists()", inspect.getsource(local_output.write_event_json)
        )

    def test_stage_asks_whether_the_event_is_persisted_not_whether_a_name_is_taken(self):
        """The corrected half. Behaviour, not source text: a directory in
        the way must reach the caller as an error rather than a Path, and an
        ordinary re-stage must still be the no-op the docstring promises."""
        from agent.outbox import stage
        from events import create_event
        from reporter.local_output import safe_event_filename

        event = create_event(
            source="DESKTOP_1", role="COO", project_id="P",
            event_type="COMPLETED", status="COMPLETED", summary="s",
            history_candidate=True, event_id="EVT-STAGE",
        )
        outbox_dir = self.root / "outbox"
        outbox_dir.mkdir()
        (outbox_dir / safe_event_filename("EVT-STAGE")).mkdir()

        with self.assertRaises(OSError):
            stage(event, outbox_dir)

        clean = self.root / "outbox_clean"
        clean.mkdir()
        first = stage(event, clean)
        self.assertTrue(first.is_file())
        self.assertEqual(stage(event, clean), first)


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


class ADeeplyNestedStateFileReadsLikeAnyOtherCorruptOneTests(unittest.TestCase):
    """C65. Nine loaders converted a corrupt state file into a typed error and
    let one input shape through untouched.

    Every one of them is the same three lines:

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise <Module>StateError(f"... is corrupted: {path} ({exc})")

    and that conversion is the whole of what each promises about a file it
    cannot read. `json.loads()` answers a deeply nested file with
    `RecursionError`, which is a `RuntimeError` and not in the tuple, so the
    promise was kept for a truncated file and broken for a nested one.

    BACKLOG F-6 already settled the rule for this exception — "함수 자신의
    문서가 '파싱할 수 없음'의 답을 이미 갖고 있는가" — and fixed the six
    call sites its sweep found. That sweep looked at readers of **untrusted
    pipeline files**; the state loaders have the identical shape and were not
    in its table. This is that sweep finished.

    Measured before the fix, all twelve entry points below diverged:

        garbage file    AgentStateError / BackupStateError / ... / returned
        nested file     RecursionError, out of json/decoder.py

    Reachability is the DR path this repository already names elsewhere —
    `monthly/generator.py` says of its own unresolvable flag that the way in
    is "손으로 고쳤거나 복원된 state 파일(DR 시나리오)". `agent/status.py`
    is the sharpest case: its docstring says "Never raises for a damaged
    state file. A corrupted `agent_state.json` is exactly when someone needs
    this view most", and `ops_status.py` calls it.

    Written as one gate over a roster rather than nine tests, because the
    property is "no loader in this family diverges" and a per-module test
    cannot fail when a **tenth** loader is added.
    """

    #: `(label, filename, callable)` — every reader that turns a state file
    #: into a value or a typed error. Extended when a state file is added;
    #: `test_the_roster_covers_every_loader_in_the_tree` is what notices.
    def _loaders(self):
        import agent.state
        import backup.state
        import monthly.state
        import runsummary
        import scheduler.state
        from agent.status import read_status
        from collector.state import PersistentSeenEventStore
        from notion.dashboard_pending import load_pending
        from notion.retry_queue import load_queue
        from scheduler.lock import (
            is_locked,
            lock_held_since,
            stale_lock_cannot_be_cleared,
        )

        return (
            ("agent.state", "agent_state.json", agent.state.load_state),
            ("backup.state", "backup_state.json", backup.state.load_state),
            ("monthly.state", "monthly_state.json", monthly.state.load_state),
            ("scheduler.state", "daily_history_state.json", scheduler.state.load_state),
            ("collector.state", "collector_state.json",
             lambda p: PersistentSeenEventStore(state_path=p).has_seen("X")),
            ("notion.retry_queue", "notion_retry_queue.json", load_queue),
            ("notion.dashboard_pending", "dashboard_pending.json", load_pending),
            ("runsummary", "last_run.json", runsummary.read_summary),
            ("scheduler.lock.is_locked", "run.lock", is_locked),
            ("scheduler.lock.held_since", "run.lock", lock_held_since),
            ("scheduler.lock.stale", "run.lock", stale_lock_cannot_be_cleared),
            ("agent.status.read_status", "agent_state.json",
             # `signals_dir` is not optional decoration (C126). Omitted, it
             # defaults to this repository's live `runtime/agent/signals/`,
             # and the counters it feeds then describe the operator's own
             # Signal files rather than this test's tree.
             lambda p: read_status(state_path=p, outbox_dir=p.parent / "outbox",
                                   sent_dir=p.parent / "sent",
                                   rejected_signals_dir=p.parent / "rejected",
                                   signals_dir=p.parent / "signals")),
        )

    #: Deep enough that `json.loads` gives up. The number is not tuned to the
    #: interpreter's limit — a remote or a corrupt file picks the depth, not
    #: this test — it is simply past any limit CPython ships with.
    DEEP = ("[" * 20000) + ("]" * 20000)
    GARBAGE = "{not json"

    # Not `_outcome`: `unittest.TestCase` already owns that attribute for
    # its own result bookkeeping, and shadowing it makes every subTest in
    # this class die with `'_Outcome' object is not callable` — measured
    # while writing this class, and a collision a reader would not guess.
    def _reading(self, loader, payload, name):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_text(payload, encoding="utf-8")
            try:
                loader(path)
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__
            return "returned"

    def test_no_loader_answers_a_nested_file_differently(self):
        """The property. Not "raises X" — that would pin nine different
        exception types and say nothing about the ones added later. The claim
        is that a nested file is **the same kind of event** as a garbage one
        for every loader, whatever that kind is for each."""
        for label, name, loader in self._loaders():
            with self.subTest(loader=label):
                self.assertEqual(
                    self._reading(loader, self.DEEP, name),
                    self._reading(loader, self.GARBAGE, name),
                    f"{label} treats a deeply nested state file as a different"
                    " kind of failure from a truncated one",
                )

    def test_no_loader_lets_a_recursion_error_out(self):
        """Stated directly too, because the comparison above would also pass
        if a future edit made *both* raise `RecursionError`."""
        for label, name, loader in self._loaders():
            with self.subTest(loader=label):
                self.assertNotEqual(
                    self._reading(loader, self.DEEP, name), "RecursionError"
                )

    def test_a_valid_state_file_is_still_read(self):
        """Precision: a guard that refused everything would pass both tests
        above. Only the loaders with a meaningful empty state are listed —
        the point is that the added clause changed nothing on the happy
        path."""
        import agent.state
        import backup.state

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_state.json"
            path.write_text('{"desktop_id": "DESKTOP_1"}', encoding="utf-8")
            self.assertEqual(agent.state.load_state(path).desktop_id, "DESKTOP_1")

            path = Path(tmp) / "backup_state.json"
            path.write_text("{}", encoding="utf-8")
            self.assertIsNotNone(backup.state.load_state(path))

    def test_the_tree_scan_the_roster_check_uses_finds_modules(self):
        """Guard against the guard silently matching nothing.

        `test_the_roster_covers_every_loader_in_the_tree` asserts a **negative** over this scan — "nothing in the tree
        does X" — and a negative over an empty set is true. Measured (C66):
        with tree discovery neutered, it passed while checking nothing.

        The trigger is ordinary rather than exotic, and this repository
        already names it: `TheScansThisFileTrustsAreNotEmptyTests` was
        written when `git ls-files` came back empty outside a checkout. A
        renamed or moved `src/` does the same thing to `rglob`, and this
        project is deliberately worked on from several machines
        (AGENT.md §1).
        """
        modules = [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]
        modules += sorted(REPO_ROOT.glob("*.py"))
        self.assertGreater(len(modules), 50)

        names = {p.name for p in modules}
        self.assertIn(
            "ops_status.py",
            names,
            "the sweep stopped reaching the root, where four json.loads calls "
            "on other machines' files live",
        )

    def test_the_roster_covers_every_loader_in_the_tree(self):
        """Guards the guard, the way `_atomic_writers()` does for the writers.

        A tenth state loader added with the old three-line shape would be
        invisible to every test above, because those iterate this roster. So
        the roster is checked against the tree: every `except` clause that
        catches `(OSError, ValueError)` around a `json.loads` must also catch
        `RecursionError`.

        **`src/` *and* the repository root, since C135.** The sweep read
        `src/` alone, and `ops_status.py` — which is not under `src/` — makes
        four `json.loads` calls on files other machines wrote. All four
        already catch `RecursionError`; somebody applied the fix there by
        hand when C22 closed BUG-40, and **nothing has kept them that way
        since.** That file is the read-only diagnostic whose own docstring
        promises it "must still produce an answer when part of the evidence
        is damaged", so a `RecursionError` from one deeply nested Event would
        take the whole status view down — the same failure BUG-40 caused for
        the Runner, on the tool an operator reaches for when the Runner is
        already broken.
        """
        offenders = []
        paths = [p for p in sorted(SRC.rglob("*.py")) if "__pycache__" not in p.parts]
        paths += sorted(REPO_ROOT.glob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                body = ast.Module(body=node.body, type_ignores=[])
                loads = any(
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("load", "loads")
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "json"
                    for call in ast.walk(body)
                )
                if not loads:
                    continue
                caught = set()
                for handler in node.handlers:
                    kind = handler.type
                    if kind is None:
                        caught.add("bare")
                    elif isinstance(kind, ast.Name):
                        caught.add(kind.id)
                    elif isinstance(kind, ast.Tuple):
                        caught.update(
                            e.id for e in kind.elts if isinstance(e, ast.Name)
                        )
                if "ValueError" not in caught:
                    # Catches something else, or nothing — a different
                    # decision, and one this gate has no opinion about.
                    continue
                if not caught & {"RecursionError", "RuntimeError", "Exception",
                                 "BaseException", "bare"}:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

        self.assertEqual(
            offenders,
            [],
            "a json.loads() guarded for ValueError and not for RecursionError:"
            f" {offenders} — see BACKLOG F-6 and C65",
        )


if __name__ == "__main__":
    unittest.main()
