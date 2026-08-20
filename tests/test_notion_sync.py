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


class TheSameInstantDivergenceHealsTests(unittest.TestCase):
    """E-23's blast radius, narrowed by measurement rather than argument —
    the same treatment C27 gave E-22.

    The record says what diverges (Company History keeps both same-instant
    Events, the Notion row keeps the first) and it stops there. What it does
    not say is **how long the divergence lasts**, and that is the fact the
    open decision most depends on.

    Measured end to end (C43): two Signals for one project on one date with
    no timestamps of their own, delivered by the real Agent, collected by the
    real Runner, synced through `ExecutionPlanSync` —

        Company History  2026-08-05.md carries BOTH Event IDs
        Notion row       Last Event ID = the first
        manifest         notion_sync SUCCESS, same_instant_skips=1

    and then one ordinary later Event for the same project:

        Notion row       Last Event ID = the later Event
        Last Updated     the later Event's timestamp

    **So the divergence is bounded, not permanent.** docs/14 §1 defines Notion
    as *Current State* — a View, never a Source — and Current State converges
    on the next Event for that project. What is permanently absent from the
    View is only "a second Event at that instant was also applied", which a
    Current State projection is not meant to carry at all; the log of that
    lives in Company History, which kept both.

    That does not close E-23 — which of two same-instant Events *is* Current
    State is still docs/04 §29-30 vs docs/06 §12's decision, and the first
    winning rather than the last is still arbitrary. It changes how urgent it
    is, and that belongs in the record rather than in someone's head.
    """

    def _sync(self):
        transport = InMemoryNotionTransport()
        return transport, ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )

    def _event(self, event_id, timestamp, summary="s"):
        return create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary=summary,
            evidence=[],
            history_candidate=True,
            event_id=event_id,
            timestamp=timestamp,
        )

    def _last_event_id(self, transport):
        page = list(transport._pages.values())[0]
        value = page["properties"].get("Last Event ID", {})
        return "".join(part["text"]["content"] for part in value.get("rich_text", []))

    MIDNIGHT = "2026-08-05T00:00:00+09:00"

    def test_the_second_same_instant_event_is_skipped(self):
        """The premise, restated here so this class stands on its own."""
        transport, sync = self._sync()

        first = sync.sync(self._event("EVT-1", self.MIDNIGHT))
        second = sync.sync(self._event("EVT-2", self.MIDNIGHT))

        self.assertIs(first.status, SyncStatus.NOTION_CREATED)
        self.assertIs(second.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        self.assertEqual(self._last_event_id(transport), "EVT-1")

    def test_one_later_event_restores_agreement(self):
        """The fact this class exists to record."""
        transport, sync = self._sync()
        sync.sync(self._event("EVT-1", self.MIDNIGHT))
        sync.sync(self._event("EVT-2", self.MIDNIGHT))

        later = sync.sync(self._event("EVT-3", "2026-08-05T09:00:00+09:00"))

        self.assertIs(later.status, SyncStatus.NOTION_UPDATED)
        self.assertEqual(self._last_event_id(transport), "EVT-3")

    def test_it_heals_even_when_the_later_event_arrives_days_afterwards(self):
        """A Desktop that was off does not make the divergence permanent —
        the guard compares timestamps, and a later work date is later."""
        transport, sync = self._sync()
        sync.sync(self._event("EVT-1", self.MIDNIGHT))
        sync.sync(self._event("EVT-2", self.MIDNIGHT))

        sync.sync(self._event("EVT-4", "2026-08-11T00:00:00+09:00"))

        self.assertEqual(self._last_event_id(transport), "EVT-4")

    def test_a_second_project_is_never_affected(self):
        """The guard is per project row, so one project's tie cannot hold
        another project's Events out of the View."""
        transport = InMemoryNotionTransport()
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )
        sync.sync(self._event("EVT-1", self.MIDNIGHT))
        sync.sync(self._event("EVT-2", self.MIDNIGHT))

        other = create_event(
            source="DESKTOP_2", role="CMO", project_id="CONTENT_OS",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS", summary="s",
            evidence=[], history_candidate=True, event_id="EVT-OTHER",
            timestamp=self.MIDNIGHT,
        )
        result = sync.sync(other)

        self.assertIs(result.status, SyncStatus.NOTION_CREATED)
        self.assertEqual(len(transport._pages), 2)

    def test_what_stays_lost_is_only_the_intermediate_state(self):
        """Stated as an assertion so the narrowing cannot be read as "E-23 is
        harmless": after healing, nothing in the View records that EVT-2 was
        ever applied. Company History is where that lives."""
        transport, sync = self._sync()
        sync.sync(self._event("EVT-1", self.MIDNIGHT, summary="first work"))
        sync.sync(self._event("EVT-2", self.MIDNIGHT, summary="second work"))
        sync.sync(self._event("EVT-3", "2026-08-05T09:00:00+09:00", summary="third work"))

        import json

        page = list(transport._pages.values())[0]
        rendered = json.dumps(page["properties"], ensure_ascii=False)

        self.assertNotIn("EVT-2", rendered)
        self.assertNotIn("second work", rendered)


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
    reflects only the first. The skip itself stays — it is docs/04 §29-30's
    decision, and the run is still SUCCESS, because
    `NOTION_SKIPPED_OLD_EVENT` is not in `app/runner.py::_FAILED_SYNC_STATUSES`
    and a View lagging its Source is not a failed run.

    **What C40 changed is that it is no longer indistinguishable.** Measured
    before, the two skips were byte-identical in the returned object:

        same instant      NOTION_SKIPPED_OLD_EVENT   error=None
        genuinely older   NOTION_SKIPPED_OLD_EVENT   error=None

    so nothing downstream could separate the guard working as specified from
    the Source and the View quietly diverging. The equal case now carries its
    reason on `SyncResult.error` — the same channel the unreadable-timestamp
    note already uses — which reaches `notion_sync.log` through
    `_log_notion_sync()` and reaches the Run Manifest as the `notion_sync`
    component's `same_instant_skips` metric. No status was added (docs §32-37
    enumerates them) and no comparison was changed.

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

    def test_the_same_instant_skip_says_why_and_the_older_one_does_not(self):
        """C40. The decision is identical for both; the report is not.

        A genuinely older Event is the guard doing its job (§63) and needs no
        explanation. An equal timestamp is BACKLOG E-23 — two different
        Events, neither of them late, one of them dropped from the View — and
        that is the one an operator would have to be told about before they
        could ever ask why the Notion row disagrees with Company History.
        """
        sync, _client, _ = _make_sync()
        sync.sync(_event(event_id="EVT-TIE-1", timestamp=self.STAMP))

        same_instant = sync.sync(
            _event(event_id="EVT-TIE-2", summary="second", timestamp=self.STAMP)
        )
        genuinely_older = sync.sync(
            _event(
                event_id="EVT-TIE-3",
                summary="late",
                timestamp="2026-08-09T09:00:00+09:00",
            )
        )

        self.assertEqual(same_instant.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        self.assertEqual(genuinely_older.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        self.assertIsNone(genuinely_older.error)
        self.assertIsNotNone(same_instant.error)
        self.assertIn("same-instant skip", same_instant.error)
        self.assertIn("E-23", same_instant.error)

    def test_the_duplicate_event_id_skip_is_still_silent(self):
        """§62's dedup is the third skip, and it is not this. Re-delivering
        the *same* Event is the outbox working (a crash between send and
        filing re-sends), so it must not start reporting a divergence."""
        sync, _client, _ = _make_sync()
        first = _event(event_id="EVT-TIE-1", timestamp=self.STAMP)
        sync.sync(first)

        again = sync.sync(first)

        self.assertEqual(again.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)
        self.assertIsNone(again.error)

    def test_the_note_is_ascii_so_it_cannot_break_a_console(self):
        """It reaches stdout through `run_company_ops.py`'s result lines.
        That entrypoint reconfigures stdout to UTF-8 at import
        (`test_run_company_ops_encoding.py`), but a note that needs the fix
        to be safe is one more thing depending on it for no reason."""
        sync, _client, _ = _make_sync()
        sync.sync(_event(event_id="EVT-TIE-1", timestamp=self.STAMP))

        result = sync.sync(_event(event_id="EVT-TIE-2", timestamp=self.STAMP))

        result.error.encode("ascii")

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


def _existing_row(transport, *, last_updated, last_event_id_items=None):
    """One PROJECTS row already in Notion, with values a *person* could have
    put there. docs/04 §43 ("수동 입력과 자동 입력") and §45 both say this
    database has human writers as well as this code."""
    transport.create_page(
        "DB-1",
        {
            "Project ID": {"rich_text": [{"text": {"content": "SEARCH_FRONTEND"}}]},
            "Last Updated": {"date": last_updated},
            "Last Event ID": {
                "rich_text": last_event_id_items
                if last_event_id_items is not None
                else [{"text": {"content": "EVT-OLD"}, "plain_text": "EVT-OLD"}]
            },
        },
    )


class LateEventGuardSurvivesAHumanEditedDateTests(unittest.TestCase):
    """C32 §7 (P0): one click in Notion's date picker parked an Event forever.

    `_update()`'s Late Event comparison parsed the *stored* `Last Updated`
    with `datetime.fromisoformat()` and compared it to the Event's own
    timestamp. The Event's side is safe — `events.schema._timestamp_error()`
    requires ISO-8601 *with an offset*. The Notion side is not: it is
    whatever is in the cell, and a person is allowed to edit it.

    Measured against `ExecutionPlanSync.sync()` before the fix:

        {"start": "2026-08-17"}            TypeError  (naive vs aware)
        {"start": "2026-08-17T09:00:00"}   TypeError  (naive vs aware)
        {"start": "yesterday"}             ValueError

    `sync()` catches only `NotionAPIError`, so all three escaped the module.
    `app/runner.py` converts the escape into NOTION_FAILED with retryability
    UNKNOWN and queues the Event — where it fails identically on every
    subsequent run, because retrying changes nothing about a stored date.

    The first row is not exotic: it is what Notion's date picker writes when
    a person selects a day and does not set a time.
    """

    def _sync_against(self, stored_date):
        sync, client, transport = _make_sync()
        _existing_row(transport, last_updated=stored_date)
        return sync.sync(_event(timestamp="2026-08-17T10:00:00+09:00")), client

    def test_a_date_with_no_time_no_longer_raises(self):
        result, _ = self._sync_against({"start": "2026-08-17"})

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)

    def test_a_local_datetime_with_no_offset_no_longer_raises(self):
        result, _ = self._sync_against({"start": "2026-08-17T09:00:00"})

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)

    def test_a_value_that_is_not_a_timestamp_at_all_no_longer_raises(self):
        result, _ = self._sync_against({"start": "yesterday"})

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)

    def test_the_run_is_self_healing(self):
        """Proceeding is not just "not crashing": the update writes a proper
        `Last Updated`, so the next Event for this project compares normally
        and the guard is back on. A refusal would have left the cell — and
        therefore the failure — exactly as it was."""
        _, client = self._sync_against({"start": "2026-08-17"})

        stored = client.find_project("SEARCH_FRONTEND")["properties"]["Last Updated"]
        self.assertEqual(stored["date"]["start"], "2026-08-17T10:00:00+09:00")

    def test_skipping_the_guard_is_recorded_rather_than_silent(self):
        """The protection really was off for this one Event. That has to
        leave a trace, or a Current State overwritten by a late Event has no
        explanation anywhere."""
        result, _ = self._sync_against({"start": "2026-08-17"})

        self.assertIsNotNone(result.error)
        self.assertIn("Late Event guard skipped", result.error)

    def test_the_trace_reaches_notion_sync_log(self):
        """`_log_notion_sync()` used to attach `REASON` only for failure
        statuses, which would have dropped this line — a successful sync with
        something to say was a case that had never existed before."""
        import tempfile

        from app.runner import _log_notion_sync

        result, _ = self._sync_against({"start": "2026-08-17"})
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "notion_sync.log"
            _log_notion_sync(log_path, result)
            written = log_path.read_text(encoding="utf-8")

        self.assertIn("NOTION_RESULT NOTION_UPDATED", written)
        self.assertIn("REASON", written)
        self.assertIn("Late Event guard skipped", written)

    def test_a_healthy_sync_still_carries_no_reason(self):
        """The original rule stands: no empty field on every line."""
        sync, _client, _transport = _make_sync()

        result = sync.sync(_event())

        self.assertIsNone(result.error)

    def test_a_comparable_stored_date_still_protects_current_state(self):
        """The guard itself is unchanged for every value this system wrote."""
        sync, client, transport = _make_sync()
        _existing_row(transport, last_updated={"start": "2026-08-20T10:00:00+09:00"})

        result = sync.sync(
            _event(
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="late",
                timestamp="2026-08-17T10:00:00+09:00",
            )
        )

        self.assertEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

    def test_a_naive_stored_date_is_not_quietly_localised(self):
        """Rejected alternative, pinned. Reading "2026-08-17" as midnight in
        the Event's own offset would look tidier and would move the Late
        Event boundary by up to a day in a direction nobody chose. Unknown is
        reported as unknown — this Event applies, and says why."""
        from notion.sync import _as_comparable_timestamp

        self.assertIsNone(
            _as_comparable_timestamp("2026-08-17", "2026-08-17T10:00:00+09:00")
        )


class AFailedUpdateIsRetryRequiredNotLostTests(unittest.TestCase):
    """C49: found by branch coverage — `_update()`'s `except NotionAPIError`
    had never been executed.

    The create path's failure was covered; the update path's was not, and
    updates are the ordinary case — a project is created once and updated
    every time it moves. docs/04 §38 is explicit that a Notion failure must
    never delete or reject the Event, so what this branch returns decides
    whether a moved project's Event is retried or silently dropped.
    """

    def _sync_with_failing_update(self):
        sync, _client, transport = _make_sync()
        _existing_row(
            transport, last_updated={"start": "2026-08-01T10:00:00+09:00"}
        )
        transport.fail_next_method = "update_page"
        event = _event(event_id="EVT-UPDATE-FAIL", timestamp="2026-08-17T10:00:00+09:00")
        return sync.sync(event)

    def test_the_event_is_marked_for_retry(self):
        result = self._sync_with_failing_update()

        self.assertEqual(result.status, SyncStatus.NOTION_RETRY_REQUIRED)

    def test_the_result_still_names_the_event_and_project(self):
        """The retry queue is keyed by these; a result that lost them would
        queue nothing."""
        result = self._sync_with_failing_update()

        self.assertEqual(result.event_id, "EVT-UPDATE-FAIL")
        self.assertEqual(result.project_id, "SEARCH_FRONTEND")
        self.assertIsNotNone(result.page_id)

    def test_the_reason_survives_for_the_log(self):
        """Without it a permanent refusal and a network blip look identical
        — the distinction BUG-13's REASON exists for."""
        result = self._sync_with_failing_update()

        self.assertTrue(result.error)
        self.assertIn("simulated", result.error)

    def test_nothing_raises_out_of_sync(self):
        """docs/04 §38: a Notion failure must not stop the Runner."""
        try:
            self._sync_with_failing_update()
        except Exception as exc:  # pragma: no cover - the assertion is the point
            self.fail(f"sync() raised {exc!r}")


class DuplicateGuardReadsWholeRichTextTests(unittest.TestCase):
    """C32 §8: §62's duplicate guard read only the first formatting run.

    Notion stores a rich_text value as one item per run of identical
    formatting, and non-text items (a mention, an equation) carry no `text`
    key at all. `_extract_rich_text()` read `items[0]["text"]["content"]`, so
    the same id stored as `EVT-` + `1` compared as `"EVT-"`.

    Low blast radius by luck rather than design: §29-30's timestamp guard
    usually catches the re-application one step later. The two guards exist
    because neither covers the other's case — §62 is what protects the
    same-second case §63 cannot see.
    """

    def _resync_with_stored_id(self, items):
        sync, _client, transport = _make_sync()
        event = _event(event_id="EVT-1", timestamp="2026-08-17T10:00:00+09:00")
        _existing_row(
            transport,
            # Deliberately OLDER than the Event, so §29-30 cannot mask a
            # §62 miss — only the id comparison can produce a skip here.
            last_updated={"start": "2026-08-01T10:00:00+09:00"},
            last_event_id_items=items,
        )
        return sync.sync(event)

    def test_an_id_split_across_two_formatting_runs_is_still_recognised(self):
        result = self._resync_with_stored_id(
            [
                {"text": {"content": "EVT-"}, "plain_text": "EVT-"},
                {"text": {"content": "1"}, "plain_text": "1"},
            ]
        )

        self.assertEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

    def test_an_id_stored_as_a_mention_is_still_recognised(self):
        """A rich_text item with no `text` key at all — `items[0]["text"]`
        returned `{}` and the guard compared against None."""
        result = self._resync_with_stored_id([{"type": "mention", "plain_text": "EVT-1"}])

        self.assertEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

    def test_a_payload_without_plain_text_still_reads(self):
        """Everything this repository writes builds `{"text": {"content"}}`
        with no `plain_text`, and so does every existing test double."""
        result = self._resync_with_stored_id([{"text": {"content": "EVT-1"}}])

        self.assertEqual(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

    def test_a_different_id_is_still_a_different_id(self):
        """The guard must not start matching everything."""
        result = self._resync_with_stored_id(
            [{"text": {"content": "EVT-OTHER"}, "plain_text": "EVT-OTHER"}]
        )

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)

    def test_a_row_with_an_empty_last_event_id_is_unknown_not_empty(self):
        """C49: found by branch coverage — `_extract_rich_text()`'s two
        early returns had never been executed.

        Both are reachable without corruption. docs/04 §43 says people write
        in this database too, so a hand-created row can carry an empty
        `Last Event ID`, and a Database created before that property existed
        carries none at all.

        The distinction matters because `event_id` is only type-checked —
        `EmptyEventIdIsStillAnEventIdTests` pins that `""` is a valid one. If
        the extractor answered `""` instead of `None`, an Event with an empty
        id meeting a row with an empty cell would be **skipped as a
        duplicate** having never been applied, and §29-30's timestamp guard
        cannot catch it: the row here is deliberately older than the Event.
        """
        sync, _client, transport = _make_sync()
        event = _event(event_id="", timestamp="2026-08-17T10:00:00+09:00")
        _existing_row(
            transport,
            last_updated={"start": "2026-08-01T10:00:00+09:00"},
            last_event_id_items=[],
        )

        result = sync.sync(event)

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)

    def test_a_row_with_no_last_event_id_property_is_unknown_too(self):
        """The other early return: the property is absent entirely."""
        sync, _client, transport = _make_sync()
        event = _event(event_id="", timestamp="2026-08-17T10:00:00+09:00")
        transport.create_page(
            "DB-1",
            {
                "Project ID": {"rich_text": [{"text": {"content": "SEARCH_FRONTEND"}}]},
                "Last Updated": {"date": {"start": "2026-08-01T10:00:00+09:00"}},
            },
        )

        result = sync.sync(event)

        self.assertEqual(result.status, SyncStatus.NOTION_UPDATED)

    def test_the_extractor_answers_none_rather_than_empty_string(self):
        """Stated directly as well, because the two tests above would also
        pass if the guard changed rather than the extractor."""
        from notion.properties import extract_last_event_id

        for row in (
            {"properties": {}},
            {"properties": {"Last Event ID": None}},
            {"properties": {"Last Event ID": {"rich_text": []}}},
            {"properties": {"Last Event ID": {}}},
        ):
            with self.subTest(row=row):
                self.assertIsNone(extract_last_event_id(row))


if __name__ == "__main__":
    unittest.main()
