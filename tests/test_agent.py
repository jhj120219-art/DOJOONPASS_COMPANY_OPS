"""Multi-Desktop Agent unit tests.

Covers the Agent's own contract in isolation: date arithmetic, Signal
validation, outbox durability, state advancement, and the failure modes
the Agent exists to survive. Cross-Desktop delivery into a real Desktop 4
pipeline is tested separately in
tests/test_agent_multi_desktop_e2e.py.

No mocks of Company Ops code — the only doubles are Transports, which
stand in for a network/OneDrive that this environment cannot make fail on
demand.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import (  # noqa: E402
    AgentState,
    AgentStateError,
    AgentStatus,
    DateOutcome,
    derive_event_id,
    ensure_desktop,
    load_state,
    parse_signal,
    pending,
    pending_dates,
    run_once,
    save_state,
)
from agent.signals import SignalError, find_secret_material, load_signals  # noqa: E402
from events import Event  # noqa: E402
from transport import Transport, TransportError  # noqa: E402

# Secret-shaped fixtures, assembled at runtime rather than written out.
#
# tests/test_repository_hygiene.py::test_no_secret_material_in_any_tracked_file
# scans every tracked file for exactly these shapes, and it cannot tell a
# test fixture from a real leaked credential — nor should it try, because a
# guard with an exemption list is a guard someone will eventually route a
# real secret through. Splitting the literal keeps the guard absolute: the
# assembled value is what the code under test sees, and no matching string
# ever exists on disk.
FAKE_NOTION_TOKEN = "ntn_" + "ABCDEFGHIJKLMNOP1234"
FAKE_LEGACY_TOKEN = "secret_" + "ABCDEFGHIJKLMNOP"
FAKE_BEARER = "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz12"
FAKE_GITHUB_PAT = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
FAKE_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----"
FAKE_ENV_ASSIGNMENT = "NOTION_API_TOKEN=" + "hunter2hunter2"


class RecordingTransport(Transport):
    """Accepts every Event and remembers it, in delivery order."""

    def __init__(self):
        self.delivered: list[Event] = []

    def send(self, event: Event) -> None:
        self.delivered.append(event)

    @property
    def event_ids(self) -> list[str]:
        return [event.event_id for event in self.delivered]


class BrokenTransport(Transport):
    """A network/OneDrive outage: every send fails, nothing is delivered."""

    def __init__(self, message="network is down"):
        self.message = message
        self.attempts = 0

    def send(self, event: Event) -> None:
        self.attempts += 1
        raise TransportError(self.message)


class FailsOnDateTransport(RecordingTransport):
    """Delivers everything except Events timestamped on one specific date."""

    def __init__(self, broken_date: date):
        super().__init__()
        self.broken_date = broken_date

    def send(self, event: Event) -> None:
        if datetime.fromisoformat(event.timestamp).date() == self.broken_date:
            raise TransportError(f"delivery refused for {self.broken_date}")
        super().send(event)


class MisbehavingTransport(Transport):
    """Violates the Transport interface by raising a non-TransportError.

    The Agent must still keep the Event rather than crash out of the batch.
    """

    def send(self, event: Event) -> None:
        raise RuntimeError("transport blew up in an undocumented way")


class AgentTestCase(unittest.TestCase):
    PROFILE = "DESKTOP_1"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.signals_dir = self.root / "signals"
        self.rejected_dir = self.root / "signals_rejected"
        self.outbox_dir = self.root / "outbox"
        self.sent_dir = self.root / "sent"
        self.state_path = self.root / "state" / "agent_state.json"
        self.lock_path = self.root / "locks" / "agent.lock"
        self.log_path = self.root / "logs" / "agent.log"

    def write_signal(self, day: date, name: str, **overrides) -> Path:
        payload = {
            "project_id": "SEARCH_BACKEND",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "summary": f"{name} on {day.isoformat()}",
            "history_candidate": True,
        }
        payload.update(overrides)
        payload = {k: v for k, v in payload.items() if v is not _OMIT}
        directory = self.signals_dir / day.isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    # 2026-08-08 by default so a test that cares about one date gets exactly
    # that date's result at dates[0]; tests exercising a multi-day catch-up
    # pass an earlier start_date explicitly.
    def run_agent(self, transport, *, now: datetime, start_date=date(2026, 8, 8), profile=None):
        return run_once(
            transport=transport,
            agent_start_date=start_date,
            profile=profile or self.PROFILE,
            now=now,
            signals_dir=self.signals_dir,
            rejected_signals_dir=self.rejected_dir,
            outbox_dir=self.outbox_dir,
            sent_dir=self.sent_dir,
            state_path=self.state_path,
            lock_path=self.lock_path,
            log_path=self.log_path,
        )

    def state(self) -> AgentState:
        return load_state(self.state_path)


class _Omit:
    pass


_OMIT = _Omit()


class CatchupDateArithmeticTests(unittest.TestCase):
    """The worked example from the brief, plus its boundaries."""

    def test_the_worked_example(self):
        self.assertEqual(
            pending_dates(
                last_successful_collection_date=date(2026, 8, 7),
                start_date=date(2026, 1, 1),
                now=datetime(2026, 8, 11, 9, 0),
            ),
            [date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10)],
        )

    def test_today_is_never_collected(self):
        """docs/07 §18: a day still in progress is not a finished day."""
        dates = pending_dates(
            last_successful_collection_date=date(2026, 8, 9),
            start_date=date(2026, 1, 1),
            now=datetime(2026, 8, 10, 23, 59),
        )
        self.assertNotIn(date(2026, 8, 10), dates)

    def test_a_first_ever_run_uses_the_configured_start_date(self):
        self.assertEqual(
            pending_dates(
                last_successful_collection_date=None,
                start_date=date(2026, 8, 8),
                now=datetime(2026, 8, 10, 9, 0),
            ),
            [date(2026, 8, 8), date(2026, 8, 9)],
        )

    def test_a_second_run_on_the_same_day_collects_nothing(self):
        self.assertEqual(
            pending_dates(
                last_successful_collection_date=date(2026, 8, 9),
                start_date=date(2026, 1, 1),
                now=datetime(2026, 8, 10, 18, 0),
            ),
            [],
        )

    def test_a_future_state_date_never_walks_backwards(self):
        self.assertEqual(
            pending_dates(
                last_successful_collection_date=date(2027, 1, 1),
                start_date=date(2026, 1, 1),
                now=datetime(2026, 8, 10, 9, 0),
            ),
            [],
        )


class SignalValidationTests(unittest.TestCase):
    def _parse(self, payload, *, target=date(2026, 8, 8), signal_id="s1"):
        return parse_signal(
            json.dumps(payload), signal_id=signal_id, target_date=target
        )

    def _valid(self, **overrides):
        payload = {
            "project_id": "P",
            "event_type": "COMPLETED",
            "status": "COMPLETED",
            "summary": "done",
            "history_candidate": True,
        }
        payload.update(overrides)
        return payload

    def test_a_minimal_signal_parses(self):
        signal = self._parse(self._valid())
        self.assertEqual(signal.signal_id, "s1")
        self.assertEqual(signal.date, date(2026, 8, 8))

    def test_a_signal_may_not_claim_an_identity(self):
        for forbidden in ("source", "role", "event_id", "schema_version"):
            with self.subTest(field=forbidden):
                with self.assertRaises(SignalError) as ctx:
                    self._parse(self._valid(**{forbidden: "DESKTOP_9"}))
                self.assertIn(forbidden, str(ctx.exception))

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(SignalError):
            self._parse(self._valid(desktop_id="DESKTOP_2"))

    def test_a_missing_required_field_is_refused(self):
        for missing in ("project_id", "event_type", "status", "summary"):
            with self.subTest(field=missing):
                payload = self._valid()
                del payload[missing]
                with self.assertRaises(SignalError):
                    self._parse(payload)

    def test_a_timestamp_on_a_different_date_is_refused(self):
        """Daily History buckets by the Event's own timestamp (docs/06 §12),
        so a Signal filed under one date but stamped another would be
        marked collected on one day and rendered on another."""
        with self.assertRaises(SignalError) as ctx:
            self._parse(self._valid(timestamp="2026-08-09T10:00:00+09:00"))
        self.assertIn("not on the signal's date", str(ctx.exception))

    def test_a_naive_timestamp_is_refused(self):
        with self.assertRaises(SignalError):
            self._parse(self._valid(timestamp="2026-08-08T10:00:00"))

    def test_malformed_json_is_refused_without_crashing(self):
        with self.assertRaises(SignalError):
            parse_signal("{not json", signal_id="s1", target_date=date(2026, 8, 8))

    def test_deeply_nested_json_is_refused_not_crashed_on(self):
        """`json.loads` raises RecursionError, not ValueError, here. Left
        uncaught, one corrupt Signal file takes down the whole Agent run
        instead of being rejected on its own."""
        deep = "[" * 200_000 + "]" * 200_000

        with self.assertRaises(SignalError):
            parse_signal(deep, signal_id="s1", target_date=date(2026, 8, 8))

    def test_secret_scanning_survives_deep_but_parseable_nesting(self):
        nested = "x"
        for _ in range(5000):
            nested = [nested]

        # No RecursionError, and the walk still reaches the bottom.
        self.assertEqual(find_secret_material({"evidence": nested}), ())


class SecretSafetyTests(unittest.TestCase):
    """The standing rule: no token, key, or .env value is ever collected,
    transported, or logged."""

    SECRETS = (
        FAKE_NOTION_TOKEN,
        FAKE_LEGACY_TOKEN,
        FAKE_BEARER,
        FAKE_PRIVATE_KEY,
        FAKE_GITHUB_PAT,
        FAKE_ENV_ASSIGNMENT,
    )

    def test_every_secret_shape_is_detected_in_a_summary(self):
        for secret in self.SECRETS:
            with self.subTest(secret=secret[:12]):
                found = find_secret_material({"summary": f"worked on this: {secret}"})
                self.assertTrue(found, f"undetected: {secret[:12]}")

    def test_a_secret_nested_in_evidence_is_detected(self):
        found = find_secret_material(
            {"evidence": ["fine", ["also fine", FAKE_NOTION_TOKEN]]}
        )
        self.assertTrue(found)

    def test_the_detector_never_returns_the_secret_itself(self):
        """Reporting a detected secret must not be the thing that writes it
        into a log file."""
        secret = FAKE_NOTION_TOKEN
        found = find_secret_material({"summary": secret})
        for pattern in found:
            self.assertNotIn(secret, pattern)

    def test_the_detector_covers_the_repository_hygiene_patterns(self):
        """tests/test_repository_hygiene.py enforces three patterns over
        tracked files. A Signal travels off this machine into Company
        History, so it must be held to at least the same bar; this pins
        that the two lists cannot silently drift apart."""
        from agent.signals import _SECRET_PATTERNS

        hygiene_patterns = (
            r"\bntn_[A-Za-z0-9]{10,}",
            r"\bsecret_[A-Za-z0-9]{10,}",
            r"Bearer\s+[A-Za-z0-9._-]{20,}",
        )
        for pattern in hygiene_patterns:
            self.assertIn(pattern, _SECRET_PATTERNS)

    def test_ordinary_work_text_is_not_flagged(self):
        for benign in (
            "Closed Beta Scope 확정.",
            "search API p95 latency 320ms -> 180ms",
            "secret sauce discussion with CEO",
            "reviewed the token bucket rate limiter",
        ):
            with self.subTest(text=benign):
                self.assertEqual(find_secret_material({"summary": benign}), ())


class NormalRunTests(AgentTestCase):
    def test_a_normal_day_is_collected_and_delivered(self):
        self.write_signal(date(2026, 8, 8), "milestone-a")
        self.write_signal(date(2026, 8, 8), "milestone-b")
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.desktop_id, "DESKTOP_1")
        self.assertEqual(result.role, "CTO_BACKEND")
        self.assertEqual([d.outcome for d in result.dates], [DateOutcome.COLLECTED])
        self.assertEqual(len(transport.delivered), 2)
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 8))
        self.assertEqual(pending(self.outbox_dir), ())

    def test_identity_comes_from_the_profile_not_the_signal(self):
        self.write_signal(date(2026, 8, 8), "a")
        transport = RecordingTransport()

        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0), profile="DESKTOP_2")

        self.assertEqual(transport.delivered[0].source, "DESKTOP_2")
        self.assertEqual(transport.delivered[0].role, "CMO")

    def test_the_coo_desktop_runs_the_same_agent(self):
        self.write_signal(date(2026, 8, 8), "coo-work", project_id="COMPANY_OPS")
        transport = RecordingTransport()

        result = self.run_agent(
            transport, now=datetime(2026, 8, 9, 9, 0), profile="DESKTOP_4"
        )

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(transport.delivered[0].source, "DESKTOP_4")
        self.assertEqual(transport.delivered[0].role, "COO")

    def test_an_event_without_its_own_timestamp_lands_on_the_signal_date(self):
        """Otherwise a catch-up of 08-08 would file its Events under the day
        the PC happened to be switched back on."""
        self.write_signal(date(2026, 8, 8), "a")
        transport = RecordingTransport()

        self.run_agent(transport, now=datetime(2026, 8, 11, 9, 0))

        delivered = datetime.fromisoformat(transport.delivered[0].timestamp)
        self.assertEqual(delivered.date(), date(2026, 8, 8))
        self.assertEqual(delivered.timetz().replace(tzinfo=None), time(0, 0))
        self.assertIsNotNone(delivered.tzinfo)

    def test_a_signal_may_carry_its_own_timestamp(self):
        self.write_signal(
            date(2026, 8, 8), "a", timestamp="2026-08-08T20:31:00+09:00"
        )
        transport = RecordingTransport()

        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(transport.delivered[0].timestamp, "2026-08-08T20:31:00+09:00")


class NoActivityTests(AgentTestCase):
    def test_a_day_with_no_signals_is_a_normal_no_activity_day(self):
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual([d.outcome for d in result.dates], [DateOutcome.NO_ACTIVITY])
        self.assertEqual(transport.delivered, [])
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 8))

    def test_no_activity_emits_no_event_at_all(self):
        """`events.schema.EVENT_TYPES` has no NO_ACTIVITY value and adding
        one is a docs/02 change. It is also unnecessary: Desktop 4's Daily
        History already renders a day with no candidates as an Empty Day
        (docs/06 §25)."""
        from events import EVENT_TYPES

        self.assertNotIn("NO_ACTIVITY", EVENT_TYPES)

        transport = RecordingTransport()
        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))
        self.assertEqual(transport.delivered, [])

    def test_a_run_of_empty_days_still_advances_to_yesterday(self):
        transport = RecordingTransport()

        result = self.run_agent(
            transport, now=datetime(2026, 8, 6, 9, 0), start_date=date(2026, 8, 1)
        )

        self.assertEqual(
            [d.date for d in result.dates],
            [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3), date(2026, 8, 4),
             date(2026, 8, 5)],
        )
        self.assertTrue(all(d.outcome is DateOutcome.NO_ACTIVITY for d in result.dates))


class PcWasOffTests(AgentTestCase):
    def test_one_missed_day_is_caught_up(self):
        self.write_signal(date(2026, 8, 8), "a")
        self.write_signal(date(2026, 8, 9), "b")
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 10, 9, 0))

        self.assertEqual([d.date for d in result.dates][-2:], [date(2026, 8, 8), date(2026, 8, 9)])
        self.assertEqual(len(transport.delivered), 2)
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 9))

    def test_three_missed_days_are_caught_up_oldest_first(self):
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_successful_collection_date=date(2026, 8, 7)),
        )
        for day, name in (
            (date(2026, 8, 8), "eight"),
            (date(2026, 8, 9), "nine"),
            (date(2026, 8, 10), "ten"),
        ):
            self.write_signal(day, name)
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 11, 9, 0))

        self.assertEqual(
            [d.date for d in result.dates],
            [date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10)],
        )
        self.assertEqual(
            [datetime.fromisoformat(e.timestamp).date() for e in transport.delivered],
            [date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10)],
        )
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 10))

    def test_a_gap_with_activity_on_only_some_days_is_handled(self):
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_successful_collection_date=date(2026, 8, 7)),
        )
        self.write_signal(date(2026, 8, 9), "only-one-day")
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 11, 9, 0))

        self.assertEqual(
            [(d.date, d.outcome) for d in result.dates],
            [
                (date(2026, 8, 8), DateOutcome.NO_ACTIVITY),
                (date(2026, 8, 9), DateOutcome.COLLECTED),
                (date(2026, 8, 10), DateOutcome.NO_ACTIVITY),
            ],
        )
        self.assertEqual(len(transport.delivered), 1)


class LastSuccessfulPointTests(AgentTestCase):
    """The brief's invariant: 8/8 성공, 8/9 실패 -> last_successful stays 8/8."""

    def _three_days_of_signals(self):
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_successful_collection_date=date(2026, 8, 7)),
        )
        for day in (date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10)):
            self.write_signal(day, f"work-{day.day}")

    def test_a_mid_range_failure_does_not_advance_the_state(self):
        self._three_days_of_signals()
        transport = FailsOnDateTransport(date(2026, 8, 9))

        result = self.run_agent(transport, now=datetime(2026, 8, 11, 9, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.failed_date, date(2026, 8, 9))
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 8))

    def test_the_failed_date_is_not_skipped_on_the_next_run(self):
        self._three_days_of_signals()
        broken = FailsOnDateTransport(date(2026, 8, 9))
        self.run_agent(broken, now=datetime(2026, 8, 11, 9, 0))

        healed = RecordingTransport()
        result = self.run_agent(healed, now=datetime(2026, 8, 11, 18, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual([d.date for d in result.dates], [date(2026, 8, 9), date(2026, 8, 10)])
        self.assertEqual(
            sorted(datetime.fromisoformat(e.timestamp).date() for e in healed.delivered),
            [date(2026, 8, 9), date(2026, 8, 10)],
        )
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 10))

    def test_a_later_date_is_never_delivered_before_an_earlier_failure_clears(self):
        """Otherwise 08-10's Events would reach Company History while
        08-09's were still stuck locally, and the state would be a lie."""
        self._three_days_of_signals()
        transport = FailsOnDateTransport(date(2026, 8, 9))

        self.run_agent(transport, now=datetime(2026, 8, 11, 9, 0))

        delivered_dates = {
            datetime.fromisoformat(e.timestamp).date() for e in transport.delivered
        }
        self.assertNotIn(date(2026, 8, 10), delivered_dates)


class NetworkFailureTests(AgentTestCase):
    def test_a_total_outage_loses_nothing(self):
        self.write_signal(date(2026, 8, 8), "a")
        self.write_signal(date(2026, 8, 8), "b")
        transport = BrokenTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertIsNone(self.state().last_successful_collection_date)
        self.assertEqual(len(pending(self.outbox_dir)), 2)

    def test_the_outbox_is_retried_before_any_new_date(self):
        self.write_signal(date(2026, 8, 8), "a")
        self.run_agent(BrokenTransport(), now=datetime(2026, 8, 9, 9, 0))
        self.assertEqual(len(pending(self.outbox_dir)), 1)

        healed = RecordingTransport()
        result = self.run_agent(healed, now=datetime(2026, 8, 9, 12, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(healed.delivered), 1)
        self.assertEqual(pending(self.outbox_dir), ())
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 8))

    def test_an_outage_that_persists_across_many_runs_still_loses_nothing(self):
        self.write_signal(date(2026, 8, 8), "a")
        for hour in (9, 12, 15):
            result = self.run_agent(BrokenTransport(), now=datetime(2026, 8, 9, hour, 0))
            self.assertEqual(result.status, AgentStatus.FAILED)

        self.assertEqual(len(pending(self.outbox_dir)), 1)
        healed = RecordingTransport()
        self.run_agent(healed, now=datetime(2026, 8, 9, 18, 0))
        self.assertEqual(len(healed.delivered), 1)

    def test_a_transport_that_breaks_its_own_interface_still_keeps_the_event(self):
        self.write_signal(date(2026, 8, 8), "a")

        result = self.run_agent(MisbehavingTransport(), now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(len(pending(self.outbox_dir)), 1)
        self.assertIsNone(self.state().last_successful_collection_date)


class DuplicateSuppressionTests(AgentTestCase):
    def test_the_same_signal_always_becomes_the_same_event_id(self):
        self.assertEqual(
            derive_event_id(source="DESKTOP_1", target_date=date(2026, 8, 8), signal_id="a"),
            derive_event_id(source="DESKTOP_1", target_date=date(2026, 8, 8), signal_id="a"),
        )

    def test_different_desktops_dates_or_signals_never_collide(self):
        base = dict(source="DESKTOP_1", target_date=date(2026, 8, 8), signal_id="a")
        variants = [
            {**base, "source": "DESKTOP_2"},
            {**base, "target_date": date(2026, 8, 9)},
            {**base, "signal_id": "b"},
        ]
        ids = {derive_event_id(**base)} | {derive_event_id(**v) for v in variants}
        self.assertEqual(len(ids), 4)

    def test_a_second_run_does_not_resend_an_already_delivered_event(self):
        self.write_signal(date(2026, 8, 8), "a")
        transport = RecordingTransport()
        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))
        self.assertEqual(len(transport.delivered), 1)

        # Roll the state back, as if the state save had been lost, and
        # re-run the same date.
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_successful_collection_date=date(2026, 8, 7)),
        )
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 12, 0))

        self.assertEqual(len(transport.delivered), 1, "the Event was sent twice")
        self.assertEqual(result.dates[-1].already_sent, tuple(transport.event_ids))

    def test_re_running_a_collected_date_produces_no_second_event(self):
        self.write_signal(date(2026, 8, 8), "a")
        first = RecordingTransport()
        self.run_agent(first, now=datetime(2026, 8, 9, 9, 0))

        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_successful_collection_date=date(2026, 8, 7)),
        )
        second = RecordingTransport()
        self.run_agent(second, now=datetime(2026, 8, 9, 12, 0))

        self.assertEqual(second.delivered, [])


class CrashRecoveryTests(AgentTestCase):
    def test_an_event_staged_but_never_sent_is_delivered_on_the_next_run(self):
        """Simulates a crash between the outbox write and the send."""
        from agent.outbox import stage
        from reporter import Reporter

        event = Reporter(profile="DESKTOP_1").report(
            project_id="P",
            event_type="COMPLETED",
            status="COMPLETED",
            summary="staged before the crash",
            history_candidate=True,
            event_id=derive_event_id(
                source="DESKTOP_1", target_date=date(2026, 8, 8), signal_id="a"
            ),
            timestamp="2026-08-08T10:00:00+09:00",
        )
        stage(event, self.outbox_dir)
        self.write_signal(date(2026, 8, 8), "a")

        transport = RecordingTransport()
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(transport.event_ids, [event.event_id])
        self.assertEqual(pending(self.outbox_dir), ())

    def test_a_crash_between_two_dates_does_not_recollect_the_finished_one(self):
        self.write_signal(date(2026, 8, 8), "a")
        self.write_signal(date(2026, 8, 9), "b")
        transport = FailsOnDateTransport(date(2026, 8, 9))

        self.run_agent(transport, now=datetime(2026, 8, 10, 9, 0))
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 8))

        healed = RecordingTransport()
        self.run_agent(healed, now=datetime(2026, 8, 10, 12, 0))

        self.assertEqual(
            [datetime.fromisoformat(e.timestamp).date() for e in healed.delivered],
            [date(2026, 8, 9)],
        )

    def test_an_unreadable_outbox_file_is_never_silently_dropped(self):
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        (self.outbox_dir / "corrupt.json").write_text("{not an event", encoding="utf-8")

        result = self.run_agent(RecordingTransport(), now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertTrue((self.outbox_dir / "corrupt.json").exists())
        self.assertIsNone(self.state().last_successful_collection_date)


class RejectedSignalTests(AgentTestCase):
    def test_a_secret_bearing_signal_is_never_transported(self):
        self.write_signal(
            date(2026, 8, 8), "leaky", summary=f"token is {FAKE_NOTION_TOKEN}"
        )
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(transport.delivered, [])
        self.assertEqual(result.dates[0].rejected_signals, ("leaky.json",))
        self.assertTrue((self.rejected_dir / "2026-08-08" / "leaky.json").exists())
        self.assertFalse((self.signals_dir / "2026-08-08" / "leaky.json").exists())

    def test_a_rejected_signal_never_reaches_the_outbox_or_the_log(self):
        secret = FAKE_NOTION_TOKEN
        self.write_signal(date(2026, 8, 8), "leaky", summary=f"token is {secret}")

        self.run_agent(RecordingTransport(), now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(pending(self.outbox_dir), ())
        self.assertNotIn(secret, self.log_path.read_text(encoding="utf-8"))

    def test_one_bad_signal_does_not_stall_the_good_ones(self):
        self.write_signal(date(2026, 8, 8), "good")
        (self.signals_dir / "2026-08-08" / "bad.json").write_text("{oops", encoding="utf-8")
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(transport.delivered), 1)
        self.assertEqual(result.dates[0].rejected_signals, ("bad.json",))
        self.assertEqual(self.state().last_successful_collection_date, date(2026, 8, 8))

    def test_a_signal_that_is_not_a_valid_event_is_rejected_not_crashed_on(self):
        """docs/02's BLOCKED-needs-a-blocker rule lives in events/schema.py;
        signals.py deliberately does not duplicate it, so the Agent has to
        cope with the Event build failing."""
        self.write_signal(
            date(2026, 8, 8), "blocked-no-blocker", event_type="BLOCKED", status="BLOCKED"
        )
        transport = RecordingTransport()

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(transport.delivered, [])
        self.assertEqual(result.dates[0].rejected_signals, ("blocked-no-blocker.json",))
        self.assertTrue(
            (self.rejected_dir / "2026-08-08" / "blocked-no-blocker.json").exists()
        )

    def test_a_rejected_signal_is_preserved_not_deleted(self):
        self.write_signal(date(2026, 8, 8), "bad", event_type="NOT_A_TYPE")

        self.run_agent(RecordingTransport(), now=datetime(2026, 8, 9, 9, 0))

        preserved = self.rejected_dir / "2026-08-08" / "bad.json"
        self.assertTrue(preserved.exists())
        self.assertIn("NOT_A_TYPE", preserved.read_text(encoding="utf-8"))


class StateSafetyTests(AgentTestCase):
    def test_a_state_file_from_another_desktop_is_refused(self):
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_2", last_successful_collection_date=date(2026, 8, 8)),
        )

        with self.assertRaises(AgentStateError):
            self.run_agent(RecordingTransport(), now=datetime(2026, 8, 10, 9, 0))

    def test_the_lock_is_released_even_when_the_state_is_refused(self):
        save_state(self.state_path, AgentState(desktop_id="DESKTOP_2"))

        with self.assertRaises(AgentStateError):
            self.run_agent(RecordingTransport(), now=datetime(2026, 8, 10, 9, 0))

        self.assertFalse(self.lock_path.exists())

    def test_a_first_ever_state_file_adopts_this_desktop(self):
        self.run_agent(RecordingTransport(), now=datetime(2026, 8, 3, 9, 0))
        self.assertEqual(self.state().desktop_id, "DESKTOP_1")

    def test_a_corrupted_state_file_is_reported_not_guessed_at(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(AgentStateError):
            self.run_agent(RecordingTransport(), now=datetime(2026, 8, 10, 9, 0))

    def test_an_unparseable_date_in_state_is_reported(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"last_successful_collection_date": "2026-08-32"}), encoding="utf-8"
        )

        with self.assertRaises(AgentStateError):
            load_state(self.state_path)

    def test_ensure_desktop_adopts_an_unbound_state(self):
        state = AgentState()
        ensure_desktop(state, "DESKTOP_3", state_path=self.state_path)  # no raise

    def test_a_failing_run_still_records_last_run_without_advancing(self):
        self.write_signal(date(2026, 8, 8), "a")

        self.run_agent(BrokenTransport(), now=datetime(2026, 8, 9, 9, 0))

        state = self.state()
        self.assertIsNotNone(state.last_run)
        self.assertIsNone(state.last_successful_collection_date)


class ConcurrencyTests(AgentTestCase):
    def test_a_second_agent_run_is_skipped_while_one_holds_the_lock(self):
        from scheduler.lock import release_lock, try_acquire_lock

        self.write_signal(date(2026, 8, 8), "a")
        try_acquire_lock(self.lock_path, now=datetime(2026, 8, 9, 9, 0))
        try:
            transport = RecordingTransport()
            result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))
        finally:
            release_lock(self.lock_path)

        self.assertEqual(result.status, AgentStatus.SKIPPED_ALREADY_RUNNING)
        self.assertEqual(transport.delivered, [])
        self.assertIsNone(self.state().last_successful_collection_date)

    def test_the_agent_lock_is_not_the_desktop4_runner_lock(self):
        """They protect different critical sections and may legitimately be
        held at once — on Desktop 4 the Agent and the Runner can overlap."""
        from agent import DEFAULT_LOCK_PATH as AGENT_LOCK
        from scheduler.lock import DEFAULT_LOCK_PATH as RUNNER_LOCK

        self.assertNotEqual(AGENT_LOCK, RUNNER_LOCK)


class LogRedactionTests(AgentTestCase):
    """The Agent's own log must not become the leak.

    A Signal's content is never logged, but its filename is — and nothing
    stops an operator from naming a file after the token they were working
    with.
    """

    def test_a_secret_in_a_signal_filename_is_redacted_in_the_log(self):
        secret = FAKE_NOTION_TOKEN
        directory = self.signals_dir / "2026-08-08"
        directory.mkdir(parents=True, exist_ok=True)
        # Not valid JSON, so it is rejected and its name reaches the log.
        (directory / f"{secret}.json").write_text("{oops", encoding="utf-8")

        self.run_agent(RecordingTransport(), now=datetime(2026, 8, 9, 9, 0))

        self.assertNotIn(secret, self.log_path.read_text(encoding="utf-8"))
        self.assertIn("[REDACTED]", self.log_path.read_text(encoding="utf-8"))

    def test_redact_leaves_ordinary_text_alone(self):
        from agent import redact

        self.assertEqual(redact("milestone-a.json"), "milestone-a.json")


class SymlinkSignalTests(AgentTestCase):
    """A link renamed to something innocuous is invisible to every
    name-based check while its target's content is what gets shipped.
    `backup/working_copy.scan_for_secrets()` already refuses links under a
    Company History tree; the Signals directory is held to the same rule.
    """

    def _make_symlink(self, target: Path, link: Path) -> bool:
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            return False
        return True

    def test_a_symlinked_signal_is_refused_and_never_followed(self):
        secret_file = self.root / "outside.json"
        secret_file.write_text(
            json.dumps(
                {
                    "project_id": "P",
                    "event_type": "COMPLETED",
                    "status": "COMPLETED",
                    "summary": "content from outside the signals directory",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )
        link = self.signals_dir / "2026-08-08" / "linked.json"
        if not self._make_symlink(secret_file, link):
            self.skipTest("this environment does not permit creating symlinks")

        transport = RecordingTransport()
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(transport.delivered, [])
        self.assertEqual(result.dates[0].rejected_signals, ("linked.json",))
        self.assertTrue(any("symlink" in e for e in result.dates[0].errors))

    def test_rejecting_a_symlink_never_touches_its_target(self):
        target = self.root / "important.json"
        target.write_text("{}", encoding="utf-8")
        link = self.signals_dir / "2026-08-08" / "linked.json"
        if not self._make_symlink(target, link):
            self.skipTest("this environment does not permit creating symlinks")

        self.run_agent(RecordingTransport(), now=datetime(2026, 8, 9, 9, 0))

        self.assertTrue(target.exists(), "the link's target was moved or removed")
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")


class SymlinkRefusalBranchTests(AgentTestCase):
    """The refusal branch itself, without needing symlink privilege.

    `SymlinkSignalTests` above is the honest end-to-end version and skips on
    a machine that cannot create a symlink — which is every machine without
    Developer Mode, including this one. That left the branch that actually
    performs the refusal completely unexecuted: a typo in it would ship.

    So the filesystem answer is substituted rather than the code under test.
    `load_signals()` runs for real, on a real directory, with real files;
    only `Path.is_symlink` is made to say yes. That is the one fact the
    environment will not supply, and it is exactly the fact the branch keys
    on.
    """

    def _load_with_symlinks_reported(self, names):
        """Run the real loader with `is_symlink()` true for `names`."""
        import agent.signals as signals_module

        original = Path.is_symlink
        targeted = set(names)

        def fake_is_symlink(self):
            if self.name in targeted:
                return True
            return original(self)

        Path.is_symlink = fake_is_symlink
        try:
            return signals_module.load_signals(self.signals_dir, date(2026, 8, 8))
        finally:
            Path.is_symlink = original

    def test_a_signal_reported_as_a_symlink_is_refused(self):
        self.write_signal(date(2026, 8, 8), "linked")

        valid, invalid = self._load_with_symlinks_reported({"linked.json"})

        self.assertEqual(valid, ())
        self.assertEqual(len(invalid), 1)
        path, error = invalid[0]
        self.assertEqual(path.name, "linked.json")
        self.assertIn("symlink", str(error))

    def test_the_symlink_is_never_read(self):
        """A name-based check must refuse before the content is touched —
        reading it is the whole exposure the refusal exists to prevent."""
        self.write_signal(date(2026, 8, 8), "linked")

        opened: list[str] = []
        original_read = Path.read_text

        def tracking_read_text(self, *args, **kwargs):
            opened.append(self.name)
            return original_read(self, *args, **kwargs)

        Path.read_text = tracking_read_text
        try:
            self._load_with_symlinks_reported({"linked.json"})
        finally:
            Path.read_text = original_read

        self.assertNotIn("linked.json", opened)

    def test_a_symlink_never_blocks_the_real_signals_beside_it(self):
        self.write_signal(date(2026, 8, 8), "genuine")
        self.write_signal(date(2026, 8, 8), "linked")

        valid, invalid = self._load_with_symlinks_reported({"linked.json"})

        self.assertEqual([signal.signal_id for signal in valid], ["genuine"])
        self.assertEqual([path.name for path, _ in invalid], ["linked.json"])

    def test_the_refusal_survives_a_whole_agent_run(self):
        """End to end through `run_once()`: refused, moved aside for a human,
        never delivered — the same treatment any unusable Signal gets."""
        import agent.signals as signals_module

        self.write_signal(date(2026, 8, 8), "genuine")
        self.write_signal(date(2026, 8, 8), "linked")

        original = Path.is_symlink

        def fake_is_symlink(self):
            return self.name == "linked.json" or original(self)

        transport = RecordingTransport()
        Path.is_symlink = fake_is_symlink
        try:
            result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))
        finally:
            Path.is_symlink = original

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(transport.delivered), 1)
        self.assertEqual(result.dates[0].rejected_signals, ("linked.json",))
        self.assertTrue((self.rejected_dir / "2026-08-08" / "linked.json").exists())

    def test_the_production_check_is_the_one_being_exercised(self):
        """Guard against the substitution drifting away from the real code:
        `load_signals` must still key the refusal on `is_symlink()`."""
        import inspect

        import agent.signals as signals_module

        source = inspect.getsource(signals_module.load_signals)
        self.assertIn("path.is_symlink()", source)


class SignalPathTests(AgentTestCase):
    """A Signal carries the path it was read from, so rejecting it later
    never depends on guessing `<dir>/<stem>.json` — which is only correct
    while every Signal is named exactly that way, and a Signal that has to
    be rejected is the one least likely to be."""

    def test_an_unusually_named_signal_is_still_moved_to_rejected(self):
        directory = self.signals_dir / "2026-08-08"
        directory.mkdir(parents=True, exist_ok=True)
        # A stem containing a dot: rebuilding "<stem>.json" happens to work
        # here, but the point is that the real path is used, not rebuilt.
        path = directory / "release.v2.json"
        path.write_text(
            json.dumps(
                {
                    "project_id": "P",
                    "event_type": "BLOCKED",
                    "status": "BLOCKED",
                    "summary": "blocked with no blocker",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

        result = self.run_agent(RecordingTransport(), now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.dates[0].rejected_signals, ("release.v2.json",))
        self.assertTrue((self.rejected_dir / "2026-08-08" / "release.v2.json").exists())
        self.assertFalse(path.exists())

    def test_load_signals_reports_the_real_path(self):
        path = self.write_signal(date(2026, 8, 8), "a")

        valid, _ = load_signals(self.signals_dir, date(2026, 8, 8))

        self.assertEqual(valid[0].path, path)


class LoadSignalsTests(AgentTestCase):
    def test_a_missing_date_directory_is_not_an_error(self):
        valid, invalid = load_signals(self.signals_dir, date(2026, 8, 8))
        self.assertEqual((valid, invalid), ((), ()))

    def test_signals_are_returned_in_stable_filename_order(self):
        for name in ("c", "a", "b"):
            self.write_signal(date(2026, 8, 8), name)

        valid, _ = load_signals(self.signals_dir, date(2026, 8, 8))

        self.assertEqual([s.signal_id for s in valid], ["a", "b", "c"])

    def test_loading_never_moves_or_deletes_a_signal(self):
        path = self.write_signal(date(2026, 8, 8), "a")
        load_signals(self.signals_dir, date(2026, 8, 8))
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()


class AgentEntrypointStateMismatchTests(unittest.TestCase):
    """`run_agent.py` must report a Desktop-identity mismatch, not traceback.

    `state.ensure_desktop()` refuses to run an Agent against another
    Desktop's state file, and that refusal is load-bearing: accepting it
    would let this Desktop inherit the other's
    `last_successful_collection_date` and skip every date up to it, losing
    those Events with no error anywhere.

    But the entrypoint caught only `ConfigurationError` and
    `ReporterConfigError`, so the refusal surfaced as a raw Python
    traceback. That is not an exotic path: `install_agent_task.ps1` writes
    COMPANY_OPS_PROFILE into the *user* environment, and its own docs warn
    that previewing an install with the wrong -DesktopId used to repoint a
    machine's identity for real. Re-running the installer with the wrong ID
    and letting the scheduled task fire is exactly how an operator gets
    here — and a traceback tells them the system broke rather than that
    they set one variable wrong.

    Found by running it, not by reading it.
    """

    def _entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "run_agent.py"
        spec = importlib.util.spec_from_file_location("run_agent_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_entrypoint_handles_the_mismatch_error(self):
        import inspect

        module = self._entrypoint()
        source = inspect.getsource(module.main)

        self.assertIn("AgentStateError", source)
        self.assertIn("return 1", source)

    def test_the_message_names_both_desktop_ids(self):
        """An operator needs to know which identity the file holds and which
        one was asked for — one of the two is the mistake."""
        from agent.state import AgentState, AgentStateError, ensure_desktop

        with self.assertRaises(AgentStateError) as caught:
            ensure_desktop(
                AgentState(desktop_id="DESKTOP_4"),
                "DESKTOP_1",
                state_path=Path("agent_state.json"),
            )

        message = str(caught.exception)
        self.assertIn("DESKTOP_4", message)
        self.assertIn("DESKTOP_1", message)
        self.assertIn("agent_state.json", message)

    def test_the_entrypoint_never_repairs_the_state_file(self):
        """The dangerous 'helpful' fix. Rewriting or deleting the state file
        to make the run proceed is the very data loss `ensure_desktop()`
        exists to prevent, performed on purpose."""
        import inspect

        source = inspect.getsource(self._entrypoint().main)
        after_catch = source[source.index("AgentStateError"):]

        for repair in ("unlink(", "save_state(", "write_text(", "remove("):
            with self.subTest(repair=repair):
                self.assertNotIn(repair, after_catch)


class CorruptStateGuidanceTests(unittest.TestCase):
    """A damaged state file and a state file belonging to another Desktop
    are the same exception and need opposite fixes.

    `run_agent.py` caught `AgentStateError` in one branch and answered every
    instance of it with the identity-mismatch script:

        - COMPANY_OPS_PROFILE이 잘못 설정됐다 → 원래 Desktop ID로 되돌린다.

    For a state file truncated by a power cut that advice is simply wrong.
    The variable is already correct, changing it fixes nothing, and the
    operator is never told the file is unreadable — the one fact that would
    have led them anywhere. Reached the same way as the mismatch case: by
    running it rather than reading it.

    `AgentStateMismatchError` subclasses `AgentStateError`, so every
    existing `except AgentStateError` is unaffected and nothing about the
    refusal itself changes.
    """

    def _run_entrypoint(self, state_text, *, profile="DESKTOP_1"):
        import importlib.util

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        state_path = root / "runtime" / "agent" / "state" / "agent_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(state_text, encoding="utf-8")

        path = Path(__file__).resolve().parents[1] / "run_agent.py"
        spec = importlib.util.spec_from_file_location("run_agent_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = root / "runtime"

        env = {
            "COMPANY_OPS_PROFILE": profile,
            "COMPANY_OPS_AGENT_SYNC_FOLDER": str(root / "cloud"),
            "COMPANY_OPS_AGENT_START_DATE": "2026-08-01",
        }
        errors = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with contextlib.redirect_stderr(errors):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = module.main()
        return code, errors.getvalue(), state_path

    def test_a_mismatched_desktop_still_gets_the_identity_guidance(self):
        code, errors, _ = self._run_entrypoint(
            json.dumps({"desktop_id": "DESKTOP_4"}), profile="DESKTOP_1"
        )

        self.assertEqual(code, 1)
        self.assertIn("COMPANY_OPS_PROFILE", errors)
        self.assertIn("DESKTOP_4", errors)

    def test_a_corrupt_state_file_is_named_as_corrupt(self):
        code, errors, _ = self._run_entrypoint("{not json")

        self.assertEqual(code, 1)
        self.assertIn("읽을 수 없습니다", errors)

    def test_a_corrupt_state_file_is_not_blamed_on_the_profile_variable(self):
        """The defect itself: advice about a variable that is already
        correct sends an operator down the wrong path entirely."""
        _code, errors, _ = self._run_entrypoint("{not json")

        self.assertIn("COMPANY_OPS_PROFILE을 바꿔도 해결되지 않습니다", errors)
        self.assertNotIn("원래 Desktop ID로 되돌린다", errors)

    def test_an_unparseable_date_field_is_treated_as_corruption_not_identity(self):
        """The subtler shape: valid JSON, correct Desktop, one bad field."""
        _code, errors, _ = self._run_entrypoint(
            json.dumps(
                {"desktop_id": "DESKTOP_1", "last_successful_collection_date": "not-a-date"}
            )
        )

        self.assertIn("읽을 수 없습니다", errors)
        self.assertNotIn("원래 Desktop ID로 되돌린다", errors)

    def test_neither_branch_repairs_the_state_file(self):
        """The file holds `last_successful_collection_date`. Guessing a
        replacement silently skips every date up to the guess — the data
        loss `ensure_desktop()` exists to prevent, performed helpfully."""
        for text in ("{not json", json.dumps({"desktop_id": "DESKTOP_4"})):
            with self.subTest(state=text[:12]):
                _code, _errors, state_path = self._run_entrypoint(text)
                self.assertEqual(state_path.read_text(encoding="utf-8"), text)

    def test_the_mismatch_error_is_still_an_agent_state_error(self):
        """Existing callers catch the base class; the split must not change
        what they catch."""
        from agent.state import AgentState, AgentStateError, AgentStateMismatchError, ensure_desktop

        self.assertTrue(issubclass(AgentStateMismatchError, AgentStateError))
        with self.assertRaises(AgentStateError):
            ensure_desktop(
                AgentState(desktop_id="DESKTOP_4"),
                "DESKTOP_1",
                state_path=Path("agent_state.json"),
            )

    def test_corruption_does_not_raise_the_mismatch_subclass(self):
        from agent.state import AgentStateMismatchError, load_state

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "agent_state.json"
        path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(Exception) as caught:
            load_state(path)
        self.assertNotIsInstance(caught.exception, AgentStateMismatchError)


class EventIdCannotCaseCollideFromTheAgentTests(unittest.TestCase):
    """E-22's blast radius, narrowed by measurement rather than argument.

    E-22 (C27): two `event_id`s differing only by case become one path on a
    case-insensitive filesystem, so the second Event is skipped as
    already-present and never reaches Company History. The characterization
    lives in `test_observability.py::SuppressedDeliveryTests`.

    What that record did not establish is **how an Event with such an id
    could exist here**. Measured now, from the derivation itself:

        derive_event_id() -> uuid5(namespace, "source|date|signal_id")
        str(uuid5(...))   -> lowercase hex, charset [0-9a-f-] only

    So no two Agent-derived ids can differ only by case — they are all
    lowercase. And the input cannot smuggle one in either: `signal_id` is the
    Signal *file's* stem, and on the case-insensitive filesystem where E-22
    exists two files whose names differ only by case are one file. On a
    case-sensitive one they are two files, giving two entirely different
    uuid5 values rather than case-variants.

    `event_id` is also a **forbidden field in a Signal** (AGENT.md §3: a
    Signal carrying it is rejected outright, not silently ignored), so an
    operator cannot supply one.

    **Conclusion: E-22 is unreachable through the Desktop 1-3 Signal path.**
    It requires an Event whose `event_id` was set directly with mixed case —
    the `reporter` API's `create_event(event_id=...)`, or tooling outside
    this repository writing into the transport folder. That is a real but
    much narrower surface than "any Event", and it is why E-22 sits below
    BUG-55 in the priority list rather than above it.

    Pinned rather than argued: if the derivation ever stops being uuid5 —
    switching to a hash rendered in mixed case, or to the Signal's own text —
    this fails and the narrowing has to be re-established.
    """

    SIGNAL_IDS = (
        "search-api-done",
        "Search-API-Done",
        "SEARCH_API_DONE",
        "a",
        "긴-한글-시그널",
    )

    def _ids(self):
        return [
            derive_event_id(source=source, target_date=date(2026, 8, 10), signal_id=name)
            for source in ("DESKTOP_1", "DESKTOP_2", "DESKTOP_3")
            for name in self.SIGNAL_IDS
        ]

    def test_every_derived_id_is_lowercase(self):
        for event_id in self._ids():
            with self.subTest(event_id=event_id):
                self.assertEqual(event_id, event_id.lower())

    def test_the_charset_cannot_produce_a_case_variant(self):
        charset = set("".join(self._ids()))

        self.assertTrue(charset <= set("0123456789abcdef-"), sorted(charset))

    def test_no_two_derived_ids_differ_only_by_case(self):
        ids = self._ids()
        folded = [event_id.casefold() for event_id in ids]

        self.assertEqual(len(set(ids)), len(set(folded)))

    def test_signals_whose_names_differ_only_by_case_do_not_produce_variants(self):
        """The one input that could plausibly carry case into the id."""
        lower = derive_event_id(
            source="DESKTOP_1", target_date=date(2026, 8, 10), signal_id="report"
        )
        upper = derive_event_id(
            source="DESKTOP_1", target_date=date(2026, 8, 10), signal_id="REPORT"
        )

        self.assertNotEqual(lower, upper)
        # Not case-variants of each other — entirely different values.
        self.assertNotEqual(lower.casefold(), upper.casefold())

    def test_a_signal_may_not_supply_its_own_event_id(self):
        """AGENT.md §3: identity fields are refused outright, so the narrow
        surface stays narrow."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(
                json.dumps(
                    {
                        "project_id": "PRJ",
                        "event_type": "MILESTONE_COMPLETED",
                        "status": "IN_PROGRESS",
                        "summary": "s",
                        "event_id": "EVT-a",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SignalError):
                parse_signal(
                    path.read_text(encoding="utf-8"),
                    signal_id="s",
                    target_date=date(2026, 8, 10),
                    path=path,
                )
