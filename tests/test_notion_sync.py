"""docs/04_NOTION_SYNC_SPEC.md §57-65 Mock Test 1-8.

Event Source는 이번 Sprint 범위(작업 5)에 따라 COMPANY_OPS 내부에서 직접 만든
Event만 사용한다 — DOJOONPASS/DOJOONPASS_CONTENT_OS Reporter는 연결하지 않는다.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import create_event  # noqa: E402
from notion import ExecutionPlanSync, InMemoryNotionTransport, NotionClient, SyncStatus  # noqa: E402


def _make_sync():
    transport = InMemoryNotionTransport()
    client = NotionClient(transport=transport, database_id="DB-1")
    sync = ExecutionPlanSync(client=client)
    return sync, client, transport


def _event(**overrides):
    data = dict(
        source="DESKTOP_3",
        role="CTO_FRONTEND",
        project_id="SEARCH_FRONTEND",
        event_type="STARTED",
        status="IN_PROGRESS",
        summary="Search Frontend event",
        history_candidate=True,
        timestamp="2026-08-01T10:00:00+09:00",
    )
    data.update(overrides)
    return create_event(**data)


class MockTest1ProjectCreate(unittest.TestCase):
    """docs §57."""

    def test_started_event_with_no_existing_row_creates_row(self):
        sync, client, _ = _make_sync()

        result = sync.sync(_event(event_type="STARTED", status="IN_PROGRESS"))

        self.assertEqual(result.status, SyncStatus.NOTION_CREATED)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "IN_PROGRESS")


class MockTest2Blocker(unittest.TestCase):
    """docs §58."""

    def test_blocked_event_sets_status_and_blocker(self):
        sync, client, _ = _make_sync()
        sync.sync(_event(timestamp="2026-08-01T10:00:00+09:00"))

        result = sync.sync(
            _event(
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="auction_item synchronization mismatch",
                timestamp="2026-08-01T11:00:00+09:00",
            )
        )

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "BLOCKED")
        self.assertEqual(
            page["properties"]["Blocker"]["rich_text"][0]["text"]["content"],
            "auction_item synchronization mismatch",
        )


class MockTest3Resume(unittest.TestCase):
    """docs §59."""

    def test_resumed_clears_status_and_blocker(self):
        sync, client, _ = _make_sync()
        sync.sync(_event(timestamp="2026-08-01T10:00:00+09:00"))
        sync.sync(
            _event(
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="issue",
                timestamp="2026-08-01T11:00:00+09:00",
            )
        )

        result = sync.sync(
            _event(event_type="RESUMED", status="IN_PROGRESS", timestamp="2026-08-01T12:00:00+09:00")
        )

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "IN_PROGRESS")
        self.assertEqual(page["properties"]["Blocker"]["rich_text"], [])


class MockTest4Milestone(unittest.TestCase):
    """docs §60."""

    def test_milestone_completed_updates_current_milestone(self):
        sync, client, _ = _make_sync()
        sync.sync(_event(timestamp="2026-08-01T10:00:00+09:00"))

        result = sync.sync(
            _event(
                event_type="MILESTONE_COMPLETED",
                status="IN_PROGRESS",
                milestone="Search UI",
                timestamp="2026-08-01T11:00:00+09:00",
            )
        )

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(
            page["properties"]["Current Milestone"]["rich_text"][0]["text"]["content"],
            "Search UI",
        )


class MockTest5Complete(unittest.TestCase):
    """docs §61."""

    def test_completed_sets_status_and_completed_date(self):
        sync, client, _ = _make_sync()
        sync.sync(_event(timestamp="2026-08-01T10:00:00+09:00"))

        result = sync.sync(
            _event(event_type="COMPLETED", status="COMPLETED", timestamp="2026-08-01T11:00:00+09:00")
        )

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "COMPLETED")
        self.assertEqual(
            page["properties"]["Completed Date"]["date"]["start"], "2026-08-01T11:00:00+09:00"
        )
        self.assertEqual(page["properties"]["Blocker"]["rich_text"], [])


class MockTest6Duplicate(unittest.TestCase):
    """docs §62."""

    def test_same_event_id_resynced_is_skipped(self):
        sync, client, _ = _make_sync()
        first = _event(timestamp="2026-08-01T10:00:00+09:00")
        sync.sync(first)

        # 동일 event_id가 재전달된 상황(정상적으로는 Collector가 걸러야 하지만
        # Notion Sync도 방어적으로 Last Event ID를 확인한다 — §62).
        duplicate = _event(
            event_id=first.event_id,
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="should not apply",
            timestamp="2026-08-01T10:00:00+09:00",
        )
        result = sync.sync(duplicate)

        self.assertEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "IN_PROGRESS")


class MockTest7LateEvent(unittest.TestCase):
    """docs §63."""

    def test_older_timestamp_event_does_not_revert_current_state(self):
        sync, client, _ = _make_sync()
        sync.sync(_event(timestamp="2026-08-05T18:00:00+09:00"))

        late = _event(
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="late",
            timestamp="2026-08-04T10:00:00+09:00",
        )
        result = sync.sync(late)

        self.assertEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "IN_PROGRESS")


class MockTest8APIFailure(unittest.TestCase):
    """docs §64."""

    def test_api_failure_returns_retry_required_without_raising(self):
        sync, _client, transport = _make_sync()
        transport.fail_next_call = True

        result = sync.sync(_event())

        self.assertEqual(result.status, SyncStatus.NOTION_RETRY_REQUIRED)
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main()


class SameTimestampDifferentEventTests(unittest.TestCase):
    """Two Events, one project, one day, identical timestamps — the second
    never reaches Notion. NOT FIXED; characterised.

    Both halves are correct by their own specification, and the loss lives in
    the seam between them.

    **docs/04 §29-30** — the Late Event guard: an Event whose timestamp is
    *past or simultaneous* with the project's `Last Updated` must not revert
    Current State. `notion/sync.py::_update()` implements that with `<=`, and
    "동시" (simultaneous) is written into the rule on purpose.

    **docs/06 §12 / `agent/agent.py::_default_timestamp()`** — a Signal with
    no timestamp of its own gets **midnight of its date**, deliberately: it
    is "the one value on that date that is the same for every Signal and for
    every re-run", so a catch-up files Events under the day the work happened
    rather than the day the PC was switched on.

    Put together, "동시" stops being a rare tie and becomes the normal case.
    Every Signal written for one date without an explicit timestamp produces
    the *same* timestamp, so for one project only the first Event of that day
    reaches Notion. Measured, two distinct `event_id`s, same project, both at
    `2026-08-10T00:00:00+09:00`:

        EVT-1   NOTION_CREATED
        EVT-2   NOTION_SKIPPED_OLD_EVENT
        update calls reaching Notion: 0

    What diverges: Company History keeps both (Daily History groups by
    timestamp and renders every KEEP candidate), while the Notion project row
    reflects only the first. Nothing reports it — `NOTION_SKIPPED_OLD_EVENT`
    is not in `app/runner.py::_FAILED_SYNC_STATUSES`, so the component
    records `recorder.ok(...)`, the run is SUCCESS, and the only trace is a
    line in `notion_sync.log` that nothing reads.

    **Why it is not fixed here.** Every candidate change is a spec change:

        `<=` -> `<`                  reverses docs/04 §29-30's explicit
                                     "동시" rule, and lets a genuinely
                                     simultaneous late Event overwrite
                                     Current State — the thing the guard
                                     exists to prevent
        finer default timestamps     abandons docs/06 §12's "same value for
                                     every Signal and every re-run", which
                                     is what makes catch-up deterministic
        tie-break on event_id        invents an ordering the specs do not
                                     define

    Distinct from the two skips already covered here: `MockTest6` is the same
    `event_id` arriving twice (§62 dedup, correct), and `MockTest7` is a
    genuinely *older* timestamp (§63, correct). This is neither — two
    different Events, neither of them late.
    """

    STAMP = "2026-08-10T00:00:00+09:00"

    def test_the_second_event_of_the_same_second_is_skipped(self):
        sync, client, _ = _make_sync()
        first = _event(event_id="EVT-TIE-1", summary="first", timestamp=self.STAMP)
        sync.sync(first)

        second = _event(
            event_id="EVT-TIE-2",
            summary="second",
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="second signal of the same day",
            timestamp=self.STAMP,
        )
        result = sync.sync(second)

        self.assertEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

    def test_notion_keeps_only_the_first_events_state(self):
        """The divergence, stated as what an operator would see in Notion."""
        sync, client, _ = _make_sync()
        sync.sync(_event(event_id="EVT-TIE-1", timestamp=self.STAMP))

        sync.sync(
            _event(
                event_id="EVT-TIE-2",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="never applied",
                timestamp=self.STAMP,
            )
        )

        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "IN_PROGRESS")

    def test_it_is_not_the_duplicate_event_id_rule(self):
        """§62 dedup would also skip, so the two must be told apart: these
        are different Events, and the reason they are skipped is the
        timestamp comparison, not the id."""
        sync, client, _ = _make_sync()
        first = _event(event_id="EVT-TIE-1", timestamp=self.STAMP)
        sync.sync(first)
        second = _event(event_id="EVT-TIE-2", timestamp=self.STAMP)

        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(sync.sync(second).status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

    def test_a_later_timestamp_in_the_same_day_does_apply(self):
        """The boundary: the guard is about the comparison, not the date. One
        second later and the same Event applies — which is why an explicit
        timestamp on the Signal avoids this entirely."""
        sync, client, _ = _make_sync()
        sync.sync(_event(event_id="EVT-TIE-1", timestamp=self.STAMP))

        result = sync.sync(
            _event(
                event_id="EVT-TIE-2",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="one second later",
                timestamp="2026-08-10T00:00:01+09:00",
            )
        )

        self.assertNotEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        page = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(page["properties"]["Status"]["select"]["name"], "BLOCKED")

    def test_the_agent_really_does_give_every_signal_the_same_timestamp(self):
        """The premise, taken from the Agent rather than assumed. If
        `_default_timestamp()` ever stops returning midnight, this stops
        being the normal case and this whole class should be revisited."""
        from datetime import date as date_type

        from agent.agent import _default_timestamp

        one = _default_timestamp(date_type(2026, 8, 10))
        two = _default_timestamp(date_type(2026, 8, 10))

        self.assertEqual(one, two)
        self.assertTrue(one.startswith("2026-08-10T00:00:00"), one)

    def test_the_skip_is_not_counted_as_a_failure_anywhere(self):
        """Why it is silent: the status is not in the runner's failed set, so
        the component reports SUCCESS and the run's exit code is 0."""
        from app.runner import _FAILED_SYNC_STATUSES

        self.assertNotIn(SyncStatus.NOTION_SKIPPED_OLD_EVENT, _FAILED_SYNC_STATUSES)
