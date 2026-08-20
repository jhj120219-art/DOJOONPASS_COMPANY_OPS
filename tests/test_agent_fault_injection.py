"""Agent-layer fault injection and security probes.

Complements tests/test_runner_failure_paths.py (Desktop 4 side) and
tests/test_agent.py (the Agent's normal failure modes) with the faults that
only exist once an Agent is writing to a shared cloud folder on someone
else's machine:

    the shared folder is unreachable / not a directory / read-only
    the local outbox cannot be written
    the local state cannot be saved
    the sent/ bookkeeping move fails after a successful delivery
    a previous run died holding the lock
    a Windows junction is used where a file is expected

The standard applied to every one of them is the same: no Event may be
lost, and no Event may reach Company History twice.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import AgentState, AgentStatus, DateOutcome, load_state, run_once, save_state  # noqa: E402
from agent.outbox import drain, pending, stage  # noqa: E402
from events import Event, create_event  # noqa: E402
from transport import OneDriveTransport, Transport, TransportError  # noqa: E402


class RecordingTransport(Transport):
    def __init__(self):
        self.delivered: list[Event] = []

    def send(self, event: Event) -> None:
        self.delivered.append(event)

    @property
    def event_ids(self):
        return [e.event_id for e in self.delivered]


class FaultTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.signals = self.root / "signals"
        self.rejected = self.root / "signals_rejected"
        self.outbox = self.root / "outbox"
        self.sent = self.root / "sent"
        self.state_path = self.root / "state" / "agent_state.json"
        self.lock_path = self.root / "locks" / "agent.lock"
        self.log_path = self.root / "logs" / "agent.log"

    def write_signal(self, day: date, name: str, **overrides):
        payload = {
            "project_id": "P",
            "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS",
            "summary": f"{name} on {day.isoformat()}",
            "history_candidate": True,
        }
        payload.update(overrides)
        directory = self.signals / day.isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_agent(self, transport, *, now, start_date=date(2026, 8, 8), profile="DESKTOP_1"):
        return run_once(
            transport=transport,
            agent_start_date=start_date,
            profile=profile,
            now=now,
            signals_dir=self.signals,
            rejected_signals_dir=self.rejected,
            outbox_dir=self.outbox,
            sent_dir=self.sent,
            state_path=self.state_path,
            lock_path=self.lock_path,
            log_path=self.log_path,
        )


class SharedFolderFailureTests(FaultTestCase):
    """The OneDrive Sync Folder is on the operator's machine and can be
    missing, renamed, occupied by a file, or read-only. None of that may
    cost an Event."""

    def test_a_sync_folder_that_is_actually_a_file_fails_without_loss(self):
        blocker = self.root / "not_a_folder"
        blocker.write_text("this is a file, not the sync folder", encoding="utf-8")
        self.write_signal(date(2026, 8, 8), "a")

        transport = OneDriveTransport(
            sync_folder=blocker, outgoing_dir=self.root / "outgoing"
        )
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(len(pending(self.outbox)), 1)
        self.assertIsNone(load_state(self.state_path).last_successful_collection_date)

    def test_the_same_event_is_delivered_once_the_folder_comes_back(self):
        blocker = self.root / "sync"
        blocker.write_text("temporarily a file", encoding="utf-8")
        self.write_signal(date(2026, 8, 8), "a")

        broken = OneDriveTransport(sync_folder=blocker, outgoing_dir=self.root / "outgoing")
        self.run_agent(broken, now=datetime(2026, 8, 9, 9, 0))
        staged = {path.name for path in pending(self.outbox)}
        self.assertEqual(len(staged), 1)

        blocker.unlink()
        blocker.mkdir()
        healed = OneDriveTransport(sync_folder=blocker, outgoing_dir=self.root / "outgoing")
        result = self.run_agent(healed, now=datetime(2026, 8, 9, 12, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(pending(self.outbox), ())
        delivered = list(blocker.glob("*.json"))
        self.assertEqual(len(delivered), 1)
        self.assertEqual({p.name for p in delivered}, staged)

    @unittest.skipUnless(sys.platform == "win32", "uses a Windows directory junction")
    def test_a_junction_as_the_signals_date_directory_is_handled(self):
        """CHARACTERIZATION. `Path.is_symlink()` is False for a Windows
        junction, so the Agent's symlink refusal does not cover this shape.
        What is pinned is that the outcome is still safe: Signals read
        through a junction are validated and secret-scanned exactly like any
        other, so a junction can redirect *where* Signals are read from but
        cannot smuggle content past the checks.

        Creating a junction needs no special privilege, unlike a symlink —
        which is precisely why it is worth pinning rather than assuming.
        """
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "planted.json").write_text(
            json.dumps(
                {
                    "project_id": "P",
                    "event_type": "COMPLETED",
                    "status": "COMPLETED",
                    "summary": "read through a junction",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

        self.signals.mkdir(parents=True, exist_ok=True)
        link = self.signals / "2026-08-08"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(elsewhere)],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            self.skipTest(f"could not create a junction: {created.stderr.strip()}")

        transport = RecordingTransport()
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        # The Signal is read, but only because it is a valid, secret-free
        # Signal — identity still comes from the profile, not the file.
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(transport.delivered), 1)
        self.assertEqual(transport.delivered[0].source, "DESKTOP_1")
        self.assertEqual(transport.delivered[0].role, "CTO_BACKEND")

    @unittest.skipUnless(sys.platform == "win32", "uses a Windows directory junction")
    def test_a_secret_bearing_signal_behind_a_junction_is_still_refused(self):
        """The junction changes where Signals come from; it must not change
        what is allowed through."""
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "leak.json").write_text(
            json.dumps(
                {
                    "project_id": "P",
                    "event_type": "COMPLETED",
                    "status": "COMPLETED",
                    "summary": "token " + "ntn_" + "ABCDEFGHIJKLMNOP1234",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

        self.signals.mkdir(parents=True, exist_ok=True)
        link = self.signals / "2026-08-08"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(elsewhere)],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            self.skipTest(f"could not create a junction: {created.stderr.strip()}")

        transport = RecordingTransport()
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(transport.delivered, [])
        self.assertEqual(result.dates[0].rejected_signals, ("leak.json",))


class LocalWriteFailureTests(FaultTestCase):
    def test_an_unwritable_outbox_fails_the_date_without_advancing_state(self):
        """The outbox write is the durability boundary: if it fails the Event
        does not exist yet, so the date has NOT been collected."""
        self.write_signal(date(2026, 8, 8), "a")
        # A file where the outbox directory must be — mkdir will fail.
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        self.outbox.write_text("blocking file", encoding="utf-8")

        transport = RecordingTransport()
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(transport.delivered, [])
        self.assertIsNone(load_state(self.state_path).last_successful_collection_date)

    def test_a_failed_sent_move_keeps_the_event_for_the_next_run(self):
        """Delivered, but the local bookkeeping move failed. Re-sending is
        the safe direction: every downstream layer dedups by event_id, while
        dropping the Event would lose it permanently."""
        event = create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="P",
            event_type="COMPLETED",
            status="COMPLETED",
            summary="delivered but unfiled",
            history_candidate=True,
            event_id="FAULT-SENT-001",
            timestamp="2026-08-08T10:00:00+09:00",
        )
        stage(event, self.outbox)
        # A file where sent/ must be a directory: mkdir succeeds only if it
        # is absent, so the drain's move has nowhere to go.
        self.sent.parent.mkdir(parents=True, exist_ok=True)
        self.sent.write_text("blocking file", encoding="utf-8")

        transport = RecordingTransport()
        summary = drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertFalse(summary.is_clear)
        self.assertEqual(len(pending(self.outbox)), 1, "the Event was lost")

    def test_a_state_save_failure_never_loses_a_delivered_event(self):
        """If state cannot be saved after a date succeeds, the next run
        re-processes that date. Deterministic event_ids plus sent/ mean it
        re-delivers nothing."""
        self.write_signal(date(2026, 8, 8), "a")
        transport = RecordingTransport()
        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))
        self.assertEqual(len(transport.delivered), 1)

        # Simulate the save having been lost entirely.
        self.state_path.unlink()

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 12, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(transport.delivered), 1, "the Event was delivered twice")
        self.assertEqual(result.dates[0].outcome, DateOutcome.COLLECTED)
        self.assertEqual(len(result.dates[0].already_sent), 1)


class LockFaultTests(FaultTestCase):
    def test_a_lock_left_by_a_dead_process_is_taken_over(self):
        """A killed Agent leaves its lock file behind. docs/07 §27: a lock
        whose recorded process is gone is stale and may be taken over —
        otherwise one crash disables the Desktop permanently."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
            json.dumps({"process_id": 999999, "created_at": "2026-08-09T08:00:00+09:00"}),
            encoding="utf-8",
        )
        self.write_signal(date(2026, 8, 8), "a")

        transport = RecordingTransport()
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(transport.delivered), 1)

    def test_an_unparseable_lock_is_treated_as_stale(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("not json at all", encoding="utf-8")
        self.write_signal(date(2026, 8, 8), "a")

        result = self.run_agent(RecordingTransport(), now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)

    def test_the_lock_is_released_after_a_failed_run(self):
        self.write_signal(date(2026, 8, 8), "a")

        class Broken(Transport):
            def send(self, event):
                raise TransportError("down")

        result = self.run_agent(Broken(), now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertFalse(self.lock_path.exists(), "a failed run left the lock behind")


class PathTraversalTests(FaultTestCase):
    """Signal filenames and Event ids are the two operator-influenced values
    that reach a filesystem path."""

    def test_a_traversing_signal_filename_cannot_escape_the_outbox(self):
        """The Signal's stem feeds the deterministic event_id, and the
        event_id feeds the outbox filename via safe_event_filename(). A
        uuid5 is always hex, so the traversal cannot survive the hop — this
        pins that the hop is actually taken."""
        directory = self.signals / "2026-08-08"
        directory.mkdir(parents=True, exist_ok=True)
        # A legal filename on Windows cannot contain "..\\", so the realistic
        # hostile stem is one full of path-significant characters.
        hostile = directory / "..__..__etc__passwd.json"
        hostile.write_text(
            json.dumps(
                {
                    "project_id": "P",
                    "event_type": "COMPLETED",
                    "status": "COMPLETED",
                    "summary": "hostile filename",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )

        transport = RecordingTransport()
        result = self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(transport.delivered), 1)
        for path in pending(self.outbox) + tuple(self.sent.glob("*.json")):
            with self.subTest(path=path.name):
                self.assertNotIn("..", path.name)
        # Everything written stayed inside the directories we were given.
        for path in self.sent.glob("*.json"):
            self.assertEqual(path.parent.resolve(), self.sent.resolve())

    def test_an_event_id_is_always_a_uuid(self):
        import uuid

        from agent import derive_event_id

        for stem in ("../../etc/passwd", "a" * 500, "con", "..", "*?<>|"):
            with self.subTest(stem=stem):
                derived = derive_event_id(
                    source="DESKTOP_1", target_date=date(2026, 8, 8), signal_id=stem
                )
                # Raises for anything that is not a well-formed UUID.
                self.assertEqual(str(uuid.UUID(derived)), derived)


class SecretLeakTests(FaultTestCase):
    """The Agent must never write secret material anywhere it did not
    already exist — not to the outbox, not to the cloud, not to its log."""

    SECRET = "ntn_" + "ABCDEFGHIJKLMNOP1234"

    def test_no_agent_written_file_contains_the_secret(self):
        self.write_signal(date(2026, 8, 8), "leaky", summary=f"key {self.SECRET}")
        self.write_signal(date(2026, 8, 8), "clean")
        cloud = self.root / "cloud"

        transport = OneDriveTransport(
            sync_folder=cloud, outgoing_dir=self.root / "outgoing"
        )
        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        # Every file the Agent itself produced, anywhere under its tree.
        # signals/ is the operator's own input, and signals_rejected/ is the
        # deliberately preserved copy of it — neither is something the Agent
        # wrote, and the next test pins that the preserved copy stays local.
        produced = [
            path
            for path in self.root.rglob("*")
            if path.is_file()
            and self.signals not in path.parents
            and self.rejected not in path.parents
        ]
        self.assertTrue(produced, "the run produced no files at all")
        for path in produced:
            with self.subTest(path=str(path.relative_to(self.root))):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                self.assertNotIn(self.SECRET, text)

    def test_the_rejected_copy_keeps_the_secret_local_and_out_of_the_cloud(self):
        """The Signal is preserved for a human (never deleted), but the copy
        lives only under signals_rejected/ on this machine."""
        self.write_signal(date(2026, 8, 8), "leaky", summary=f"key {self.SECRET}")
        cloud = self.root / "cloud"
        cloud.mkdir()

        transport = OneDriveTransport(
            sync_folder=cloud, outgoing_dir=self.root / "outgoing"
        )
        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))

        preserved = self.rejected / "2026-08-08" / "leaky.json"
        self.assertTrue(preserved.exists())
        self.assertIn(self.SECRET, preserved.read_text(encoding="utf-8"))
        self.assertEqual(list(cloud.glob("*.json")), [])
        self.assertEqual(list((self.root / "outgoing").glob("*.json")), [])


class DeliveredButUnfiledTests(FaultTestCase):
    """The send succeeded and the `sent/` move did not.

    `test_a_failed_sent_move_keeps_the_event_for_the_next_run` above blocks
    `sent/` by putting a *file* where the directory belongs, so
    `sent_dir.mkdir()` fails at the top of `drain()` and it returns before
    calling the Transport at all. That is a real fault and worth keeping —
    but it is "nothing was delivered", not "delivered but unfiled", and the
    branch that handles the latter had never executed:

        failed.append((event.event_id, f"delivered but could not be filed: {exc}"))

    That branch is the one with a downstream consequence. The Event really
    is in the cloud, and it is also still in the outbox, so the next run
    sends it a second time — the case `outbox.py`'s docstring calls safe
    ("a duplicate delivery costs one redundant file copy and produces no
    duplicate History"). Nothing had checked either half.

    Injected by putting a non-empty directory exactly where the Event's file
    must be written: `os.replace()` refuses it, and no mock is involved —
    the send genuinely happens first.
    """

    def _blocking_directory_for(self, event):
        from reporter.local_output import safe_event_filename

        self.sent.mkdir(parents=True, exist_ok=True)
        blocked = self.sent / safe_event_filename(event.event_id)
        blocked.mkdir()
        (blocked / "occupied.txt").write_text("in the way", encoding="utf-8")
        return blocked

    def _event(self, event_id="FAULT-UNFILED-001"):
        return create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="P",
            event_type="COMPLETED",
            status="COMPLETED",
            summary="delivered but unfiled",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-08T10:00:00+09:00",
        )

    def test_the_event_is_delivered_and_then_reported_as_unfiled(self):
        event = self._event()
        stage(event, self.outbox)
        self._blocking_directory_for(event)
        transport = RecordingTransport()

        summary = drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)

        # It really was delivered — that is what makes this branch different
        # from the mkdir failure above.
        self.assertEqual(transport.event_ids, [event.event_id])
        self.assertFalse(summary.is_clear)
        self.assertEqual(len(summary.failed), 1)
        self.assertIn("delivered but could not be filed", summary.failed[0][1])
        self.assertEqual(summary.sent, ())

    def test_the_event_stays_in_the_outbox_rather_than_being_dropped(self):
        """Losing it would lose Company History; re-sending costs a file
        copy. `outbox.py` chooses re-sending, and this pins that choice."""
        event = self._event()
        stage(event, self.outbox)
        self._blocking_directory_for(event)

        drain(RecordingTransport(), outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertEqual(len(pending(self.outbox)), 1)

    def test_the_next_run_delivers_it_again_once_the_obstruction_clears(self):
        event = self._event()
        stage(event, self.outbox)
        blocked = self._blocking_directory_for(event)
        transport = RecordingTransport()
        drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)

        shutil.rmtree(blocked)
        again = drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertEqual(again.sent, (event.event_id,))
        self.assertEqual(pending(self.outbox), ())
        # Twice on the wire, deliberately — the downstream layers dedup.
        self.assertEqual(transport.event_ids, [event.event_id, event.event_id])

    def test_one_unfiled_event_does_not_stop_the_rest_of_the_batch(self):
        """Per-file isolation, the same rule `collector/runtime.py` applies:
        the blocked Event must not take its neighbours with it."""
        blocked_event = self._event("FAULT-UNFILED-BLOCKED")
        other = self._event("FAULT-UNFILED-OTHER")
        stage(blocked_event, self.outbox)
        stage(other, self.outbox)
        self._blocking_directory_for(blocked_event)
        transport = RecordingTransport()

        summary = drain(transport, outbox_dir=self.outbox, sent_dir=self.sent)

        self.assertEqual(summary.sent, (other.event_id,))
        self.assertEqual([f[0] for f in summary.failed], [blocked_event.event_id])
        self.assertEqual(len(pending(self.outbox)), 1)

    def test_the_agent_does_not_advance_its_date_past_an_unfiled_event(self):
        """The invariant the outbox exists for: a date must not close while
        its Events are still sitting locally."""
        self.write_signal(date(2026, 8, 8), "a")
        transport = RecordingTransport()
        self.run_agent(transport, now=datetime(2026, 8, 9, 9, 0))
        self.assertEqual(load_state(self.state_path).last_successful_collection_date,
                         date(2026, 8, 8))

        # Put the delivered Event back in the outbox and block its filing,
        # the shape a crash-plus-obstruction leaves behind.
        filed = list(self.sent.glob("*.json"))
        self.assertEqual(len(filed), 1)
        shutil.move(str(filed[0]), str(self.outbox / filed[0].name))
        blocked = self.sent / filed[0].name
        blocked.mkdir()
        (blocked / "occupied.txt").write_text("in the way", encoding="utf-8")
        save_state(
            self.state_path,
            AgentState(desktop_id="DESKTOP_1", last_successful_collection_date=None),
        )

        result = self.run_agent(transport, now=datetime(2026, 8, 9, 12, 0))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertIsNone(load_state(self.state_path).last_successful_collection_date)
        self.assertEqual(len(pending(self.outbox)), 1)


if __name__ == "__main__":
    unittest.main()
