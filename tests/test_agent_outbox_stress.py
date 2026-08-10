"""Agent outbox / Transport Intake stress tests.

Correctness at volume, not wall-clock thresholds. Timing assertions are
flaky on a shared machine and would either be so loose they catch nothing
or so tight they fail for unrelated reasons; the measured performance
numbers live in BACKLOG.md instead, where a human reads them. What is
asserted here is the property that must hold at any N:

    every Event is delivered exactly once
    no Event is lost by an outage, a crash, or a restart
    no Event is delivered twice by a retry
    a re-delivery of everything already sent adds nothing

Scale defaults to 300 so the whole suite stays under two minutes. Raise it
for a heavier run without editing anything:

    COMPANY_OPS_STRESS_N=10000 python -m pytest tests/test_agent_outbox_stress.py

Verified at 1,000 / 5,000 / 10,000 during the Sprint that added this file;
every assertion below held unchanged at each tier, and the timings are
recorded in BACKLOG.md section D. Delivery cost is linear in N (~5.4 ms per
Event, dominated by one file read plus one rename), so the higher tiers are
slow rather than interesting — which is exactly why they are not the
default.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.outbox import drain, is_sent, pending, stage  # noqa: E402
from events import Event, create_event  # noqa: E402
from transport import Transport, TransportError, run_intake  # noqa: E402

N = int(os.environ.get("COMPANY_OPS_STRESS_N", "300"))


class CountingTransport(Transport):
    """Records every delivery so "exactly once" can be asserted, not assumed."""

    def __init__(self):
        self.delivered: list[str] = []

    def send(self, event: Event) -> None:
        self.delivered.append(event.event_id)

    @property
    def unique(self) -> set[str]:
        return set(self.delivered)


class OutageTransport(CountingTransport):
    """Fails every `every`-th send; delivers the rest."""

    def __init__(self, every=3):
        super().__init__()
        self.every = every
        self.attempts = 0

    def send(self, event: Event) -> None:
        self.attempts += 1
        if self.attempts % self.every == 0:
            raise TransportError("intermittent outage")
        super().send(event)


class CrashingTransport(CountingTransport):
    """Delivers `before_crash` Events, then dies the way a killed process
    does — abruptly, mid-batch, with no chance to record anything."""

    def __init__(self, before_crash):
        super().__init__()
        self.before_crash = before_crash

    def send(self, event: Event) -> None:
        if len(self.delivered) >= self.before_crash:
            raise KeyboardInterrupt("simulated process kill")
        super().send(event)


class TotalOutageTransport(Transport):
    def __init__(self):
        self.attempts = 0

    def send(self, event: Event) -> None:
        self.attempts += 1
        raise TransportError("network is down")


class OutboxStressTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.outbox = self.root / "outbox"
        self.sent = self.root / "sent"

    def make_events(self, count, tag="STRESS"):
        return [
            create_event(
                source="DESKTOP_1",
                role="CTO_BACKEND",
                project_id="P",
                event_type="MILESTONE_COMPLETED",
                status="IN_PROGRESS",
                summary=f"{tag} event {index}",
                history_candidate=True,
                event_id=f"{tag}-{index:06d}",
                timestamp="2026-08-08T10:00:00+09:00",
            )
            for index in range(count)
        ]

    def stage_all(self, events):
        for event in events:
            stage(event, self.outbox)


class DeliveryAtScaleTests(OutboxStressTestCase):
    def test_every_event_is_delivered_exactly_once(self):
        events = self.make_events(N)
        self.stage_all(events)
        self.assertEqual(len(pending(self.outbox)), N)

        transport = CountingTransport()
        summary = drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertTrue(summary.is_clear)
        self.assertEqual(len(summary.sent), N)
        self.assertEqual(len(transport.delivered), N)
        self.assertEqual(len(transport.unique), N, "an Event was delivered twice")
        self.assertEqual(pending(self.outbox), ())
        self.assertEqual(len(list(self.sent.glob("*.json"))), N)

    def test_staging_is_idempotent_at_scale(self):
        events = self.make_events(N)
        self.stage_all(events)
        self.stage_all(events)
        self.stage_all(events)

        self.assertEqual(len(pending(self.outbox)), N)

    def test_every_delivered_event_is_recognised_as_sent(self):
        events = self.make_events(N)
        self.stage_all(events)
        drain(CountingTransport(), outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertTrue(all(is_sent(event.event_id, self.sent) for event in events))
        self.assertFalse(is_sent("NEVER-SENT", self.sent))


class OutageAtScaleTests(OutboxStressTestCase):
    def test_a_total_outage_loses_nothing(self):
        events = self.make_events(N)
        self.stage_all(events)

        transport = TotalOutageTransport()
        summary = drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertFalse(summary.is_clear)
        self.assertEqual(len(summary.failed), N)
        self.assertEqual(transport.attempts, N)
        self.assertEqual(len(pending(self.outbox)), N, "Events were lost by an outage")
        self.assertEqual(len(list(self.sent.glob("*.json"))), 0)

    def test_an_intermittent_outage_converges_with_no_duplicates(self):
        events = self.make_events(N)
        self.stage_all(events)

        delivered_ids: list[str] = []
        for _ in range(12):
            transport = OutageTransport(every=3)
            summary = drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)
            delivered_ids.extend(transport.delivered)
            if summary.is_clear:
                break

        self.assertEqual(pending(self.outbox), (), "the outbox never drained")
        self.assertEqual(len(delivered_ids), N)
        self.assertEqual(len(set(delivered_ids)), N, "an Event was delivered twice")

    def test_a_sustained_outage_across_many_runs_still_loses_nothing(self):
        events = self.make_events(N)
        self.stage_all(events)

        for _ in range(5):
            drain(TotalOutageTransport(), outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertEqual(len(pending(self.outbox)), N)

        recovered = CountingTransport()
        drain(recovered, outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertEqual(len(recovered.unique), N)
        self.assertEqual(pending(self.outbox), ())


class CrashAtScaleTests(OutboxStressTestCase):
    def test_a_crash_mid_drain_loses_nothing_and_duplicates_nothing(self):
        events = self.make_events(N)
        self.stage_all(events)
        crash_after = N // 2

        crashing = CrashingTransport(before_crash=crash_after)
        with self.assertRaises(KeyboardInterrupt):
            drain(crashing, outbox_dir=self.outbox, sent_dir=self.sent)

        # The kill left the outbox holding exactly what was not delivered.
        self.assertEqual(len(crashing.delivered), crash_after)
        self.assertEqual(len(pending(self.outbox)), N - crash_after)

        restarted = CountingTransport()
        summary = drain(restarted, outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertTrue(summary.is_clear)
        all_delivered = crashing.delivered + restarted.delivered
        self.assertEqual(len(all_delivered), N)
        self.assertEqual(len(set(all_delivered)), N, "the crash caused a duplicate")
        self.assertEqual(len(list(self.sent.glob("*.json"))), N)

    def test_restaging_after_a_crash_does_not_resend_delivered_events(self):
        """The Agent re-stages a date's Signals on restart. Everything
        already in sent/ must be skipped, or a long catch-up would re-deliver
        its whole history on every crash."""
        events = self.make_events(N)
        self.stage_all(events)
        drain(CountingTransport(), outbox_dir=self.outbox, sent_dir=self.sent)

        to_restage = [e for e in events if not is_sent(e.event_id, self.sent)]
        self.assertEqual(to_restage, [])

        second = CountingTransport()
        drain(second, outbox_dir=self.outbox, sent_dir=self.sent)
        self.assertEqual(second.delivered, [])


class IntakeAtScaleTests(unittest.TestCase):
    """Desktop 4's receiving side under the same volume."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.transport_dir = root / "transport"
        self.incoming = root / "incoming"
        self.processed = root / "processed"
        self.rejected = root / "rejected"
        for directory in (self.transport_dir, self.incoming, self.processed, self.rejected):
            directory.mkdir(parents=True, exist_ok=True)

    def deliver(self, count, tag="INTAKE"):
        for index in range(count):
            event = create_event(
                source="DESKTOP_2",
                role="CMO",
                project_id="P",
                event_type="MILESTONE_COMPLETED",
                status="IN_PROGRESS",
                summary=f"{tag} {index}",
                history_candidate=True,
                event_id=f"{tag}-{index:06d}",
                timestamp="2026-08-08T10:00:00+09:00",
            )
            (self.transport_dir / f"{event.event_id}.json").write_text(
                event.to_json(), encoding="utf-8"
            )
        self.age()

    def age(self):
        """Backdate mtimes past the stability window instead of sleeping."""
        old = time.time() - 3600
        for path in self.transport_dir.glob("*.json"):
            os.utime(path, (old, old))

    def run_intake(self):
        return run_intake(
            transport_dir=self.transport_dir,
            incoming_dir=self.incoming,
            processed_dir=self.processed,
            rejected_dir=self.rejected,
        )

    def test_all_events_are_promoted_exactly_once(self):
        self.deliver(N)

        summary = self.run_intake()

        self.assertEqual(len(summary.moved), N)
        self.assertEqual(len(list(self.incoming.glob("*.json"))), N)
        self.assertEqual(len(list(self.transport_dir.glob("*.json"))), 0)

    def test_a_full_redelivery_promotes_nothing_new(self):
        """OneDrive re-syncing the whole folder — the shape a restored
        backup or a re-linked account produces."""
        self.deliver(N)
        self.run_intake()
        for path in self.incoming.glob("*.json"):
            path.rename(self.processed / path.name)

        self.deliver(N)
        summary = self.run_intake()

        self.assertEqual(len(summary.moved), 0)
        self.assertEqual(len(summary.skipped_already_present), N)
        self.assertEqual(len(list(self.incoming.glob("*.json"))), 0)
        self.assertEqual(len(list(self.processed.glob("*.json"))), N)

    def test_an_accumulated_transport_directory_does_not_block_new_events(self):
        """Known backlog item: intake never deletes an already-present file,
        so transport/ grows. What must stay true is that the accumulation
        never stops a genuinely new Event from getting through."""
        self.deliver(N, tag="OLD")
        self.run_intake()
        for path in self.incoming.glob("*.json"):
            path.rename(self.processed / path.name)
        self.deliver(N, tag="OLD")  # re-delivered, will pile up

        self.deliver(5, tag="FRESH")
        summary = self.run_intake()

        self.assertEqual(len(summary.moved), 5)
        self.assertEqual(len(summary.skipped_already_present), N)
        self.assertEqual(len(list(self.incoming.glob("*.json"))), 5)


if __name__ == "__main__":
    unittest.main()
