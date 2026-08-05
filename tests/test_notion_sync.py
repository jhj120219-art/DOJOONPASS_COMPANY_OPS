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
