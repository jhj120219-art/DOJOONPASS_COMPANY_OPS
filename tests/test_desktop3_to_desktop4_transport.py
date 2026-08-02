"""Phase 5.2 — Desktop 3 -> Desktop 4 transport test.

Honest scope note: this dev environment is a single machine with no real
OneDrive account or second physical desktop, so true cross-network/cloud
latency cannot be exercised here (that requires Phase 5.2's real-hardware
follow-up, once Desktop 3/4 are actually provisioned). What IS exercised
end-to-end with real code (no mocks) is everything Company Ops code is
actually responsible for:

    Reporter (Desktop 3)
        -> OneDriveTransport.send()
        -> runtime/events/outgoing/           (Desktop 3 local buffer)
        -> shared sync_folder                 (stands in for the OneDrive
                                                 cloud-synced folder both
                                                 desktops' local OneDrive
                                                 clients would mirror)
        -> Transport Intake (Desktop 4)        (reads the SAME sync_folder
                                                 as its local mirror)
        -> runtime/events/transport/           (Desktop 4 local buffer)
        -> runtime/events/incoming/
        -> Collector Runtime (Phase 4.1, unchanged)
        -> runtime/events/processed/
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector import Collector, InMemorySeenEventStore  # noqa: E402
from collector import run_once as collector_run_once  # noqa: E402
from events import Event  # noqa: E402
from reporter import Reporter  # noqa: E402
from transport import OneDriveTransport, run_intake  # noqa: E402


class Desktop3ToDesktop4TransportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        # Desktop 3 side
        self.desktop3_outgoing = root / "desktop3" / "runtime" / "events" / "outgoing"

        # the one physical folder standing in for "the OneDrive cloud" —
        # in reality this is two separate local mirrors (one per desktop)
        # kept identical by OneDrive itself; a single shared folder is the
        # closest local approximation available in this environment.
        self.shared_sync_folder = root / "shared_onedrive_sync"

        # Desktop 4 side
        self.desktop4_transport = root / "desktop4" / "runtime" / "events" / "transport"
        self.desktop4_incoming = root / "desktop4" / "runtime" / "events" / "incoming"
        self.desktop4_processed = root / "desktop4" / "runtime" / "events" / "processed"
        self.desktop4_rejected = root / "desktop4" / "runtime" / "events" / "rejected"
        self.desktop4_log = root / "desktop4" / "runtime" / "logs" / "collector.log"

    def _sync_cloud_to_desktop4(self):
        """Stands in for OneDrive propagating shared_sync_folder to
        Desktop 4's own local mirror — represented here as a plain copy
        into desktop4_transport, since this environment cannot run two
        real OneDrive clients against one cloud account."""
        self.desktop4_transport.mkdir(parents=True, exist_ok=True)
        for path in self.shared_sync_folder.glob("*.json"):
            shutil.copy2(path, self.desktop4_transport / path.name)

    def test_real_event_travels_from_desktop3_reporter_to_desktop4_processed(self):
        # --- Desktop 3: Reporter creates and sends a real Event ---
        transport = OneDriveTransport(
            sync_folder=self.shared_sync_folder, outgoing_dir=self.desktop3_outgoing
        )
        reporter = Reporter(profile="DESKTOP_3", transport=transport)
        event = reporter.report_and_send(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            milestone="Search UI",
            summary="Search UI implementation completed",
            evidence=["TypeScript PASS"],
            history_candidate=True,
            event_id="D3D4-TRANSPORT-001",
        )

        self.assertTrue((self.desktop3_outgoing / "D3D4-TRANSPORT-001.json").exists())
        self.assertTrue((self.shared_sync_folder / "D3D4-TRANSPORT-001.json").exists())

        # --- cloud sync propagates to Desktop 4's local mirror ---
        self._sync_cloud_to_desktop4()
        self.assertTrue((self.desktop4_transport / "D3D4-TRANSPORT-001.json").exists())

        # --- Desktop 4: Transport Intake promotes the stable file ---
        intake_summary = run_intake(
            transport_dir=self.desktop4_transport,
            incoming_dir=self.desktop4_incoming,
            processed_dir=self.desktop4_processed,
            rejected_dir=self.desktop4_rejected,
            stable_after_seconds=0.0,  # file is already complete in this test
        )
        self.assertEqual(intake_summary.moved, ("D3D4-TRANSPORT-001.json",))
        self.assertTrue((self.desktop4_incoming / "D3D4-TRANSPORT-001.json").exists())

        # --- Desktop 4: Collector Runtime processes it (Phase 4.1, unchanged) ---
        collector = Collector(seen_store=InMemorySeenEventStore())
        collector_summary = collector_run_once(
            collector=collector,
            incoming_dir=self.desktop4_incoming,
            processed_dir=self.desktop4_processed,
            rejected_dir=self.desktop4_rejected,
            log_path=self.desktop4_log,
        )
        self.assertEqual(collector_summary.accepted, 1)

        processed_event = Event.from_json(
            (self.desktop4_processed / "D3D4-TRANSPORT-001.json").read_text(encoding="utf-8")
        )
        self.assertEqual(processed_event, event)
        self.assertEqual(processed_event.source, "DESKTOP_3")
        self.assertEqual(processed_event.summary, "Search UI implementation completed")

    def test_retry_after_simulated_network_gap_does_not_duplicate(self):
        transport = OneDriveTransport(
            sync_folder=self.shared_sync_folder, outgoing_dir=self.desktop3_outgoing
        )
        reporter = Reporter(profile="DESKTOP_3", transport=transport)
        event = reporter.report(
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            milestone="Search UI",
            summary="Search UI implementation started",
            evidence=[],
            history_candidate=False,
            event_id="D3D4-RETRY-001",
        )

        # simulate a network gap: first send attempt reaches outgoing/ and
        # the shared folder, but Desktop 4 hasn't synced/intaken yet when
        # the caller (naively) retries the exact same send
        transport.send(event)
        transport.send(event)  # retry

        self._sync_cloud_to_desktop4()
        run_intake(
            transport_dir=self.desktop4_transport,
            incoming_dir=self.desktop4_incoming,
            processed_dir=self.desktop4_processed,
            rejected_dir=self.desktop4_rejected,
            stable_after_seconds=0.0,
        )

        collector = Collector(seen_store=InMemorySeenEventStore())
        summary = collector_run_once(
            collector=collector,
            incoming_dir=self.desktop4_incoming,
            processed_dir=self.desktop4_processed,
            rejected_dir=self.desktop4_rejected,
            log_path=self.desktop4_log,
        )

        # exactly one file existed to process (retry was idempotent at the
        # Transport layer already), and Collector accepted it exactly once
        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.duplicate, 0)
        self.assertEqual(len(list(self.desktop4_processed.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
