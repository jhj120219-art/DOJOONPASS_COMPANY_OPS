"""Execution Plan Sync (docs/04_NOTION_SYNC_SPEC.md §3, §6, §29-37): syncs one
Execution Event into the Notion PROJECTS Database Current State.

This module only implements the Event -> Notion Row Create/Update decision
(§6), the Late Event guard (§29-30), and a defensive duplicate check (§62).
It does not decide whether an Event should be synced at all — that judgment
(ACCEPTED/DUPLICATE/REJECTED) is Collector's, made upstream — and it does
not touch History (§3: Notion and History are separate flows).

Event Source for this Sprint is COMPANY_OPS's own Execution Event stream
only (docs task scope item 5) — this module takes an already-parsed `Event`
and has no opinion about where it came from.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from events import Event

from .client import NotionClient
from .properties import (
    build_create_properties,
    build_update_properties,
    extract_last_event_id,
    extract_last_updated,
    fit_key,
    humanize_project_id,
)
from .transport import NotionAPIError


class SyncStatus(enum.Enum):
    """docs §32-37."""

    NOTION_CREATED = "NOTION_CREATED"
    NOTION_UPDATED = "NOTION_UPDATED"
    NOTION_SKIPPED_OLD_EVENT = "NOTION_SKIPPED_OLD_EVENT"
    NOTION_RETRY_REQUIRED = "NOTION_RETRY_REQUIRED"
    NOTION_FAILED = "NOTION_FAILED"


# HTTP statuses that mean "retrying this exact request will never succeed".
#
# BUG-13 named the problem: `NOTION_RETRY_REQUIRED` reports a Notion that was
# briefly down and a request Notion will refuse forever with the same word.
# Its fix appended the reason *string* to the log so a person could tell them
# apart by reading prose. The machine-readable signal that says the same
# thing — `NotionAPIError.status_code` — was already being set by the
# transport and read by **no production code at all** (C33 §5).
#
# Deliberately a short, explicit list rather than "any 4xx":
#
#     400  malformed request — a property value Notion will not accept
#     401  the token is wrong or expired
#     403  the integration is not permitted on this database
#     404  the database or page id does not exist (or was unshared)
#
# Every one needs a person to change something; none of them clears by
# waiting. 408 and 429 are 4xx that *do* clear by waiting, and 409 is a
# conflict a retry can win, so all three stay out. Anything unlisted — every
# 5xx, a timeout, a reset connection, no status at all — keeps today's
# retryable reading.
#
# The asymmetry that made this worth doing: `backup/git_ops` already
# classifies permanent-vs-transient (`is_authentication_failure()`) off
# git's English prose, and C31 §6 went to the trouble of removing that
# classifier's locale dependency. The Notion side had a better signal than
# prose and used none of it.
PERMANENTLY_REFUSING_STATUS_CODES = (400, 401, 403, 404)


@dataclass(frozen=True)
class SyncResult:
    status: SyncStatus
    event_id: str
    project_id: str
    page_id: str | None = None
    error: str | None = None
    # The HTTP status Notion answered with, when there was one. None for a
    # network failure, a timeout, or a non-HTTP error — which is itself
    # information: "no status" means the request never got an answer, and
    # that is the retryable case.
    status_code: int | None = None

    @property
    def is_permanently_refused(self) -> bool:
        """Notion answered, and the answer will not change by retrying."""
        return self.status_code in PERMANENTLY_REFUSING_STATUS_CODES


def _as_comparable_timestamp(stored: str, against: str) -> datetime | None:
    """The stored `Last Updated`, parsed so it can be compared with `against`
    — or None when it cannot be.

    Two separate ways a Notion-side value defeats a plain
    `datetime.fromisoformat()` comparison, and both are ordinary rather than
    adversarial:

        "2026-08-17"            Notion's date picker with no time. Parses,
                                but naive — comparing it to the Event's
                                tz-aware timestamp raises TypeError.
        "yesterday"             any value fromisoformat cannot read at all,
                                including one written by a different tool.

    `against` is the Event's own timestamp, which `events.schema` guarantees
    is ISO-8601 *with* an offset — so it is only inspected here to decide
    whether a naive stored value is comparable, never parsed as a fallback.
    A naive stored value is deliberately NOT localised to the Event's offset:
    guessing a timezone for a human's date entry would silently move the
    Late Event boundary by up to a day in whichever direction the guess went.
    Unknown is reported as unknown.
    """
    try:
        parsed = datetime.fromisoformat(stored)
    except (TypeError, ValueError):
        return None
    if (parsed.tzinfo is None) != (datetime.fromisoformat(against).tzinfo is None):
        return None
    return parsed


class ExecutionPlanSync:
    def __init__(self, *, client: NotionClient):
        self._client = client

    def sync(self, event: Event) -> SyncResult:
        """docs §6: project_id로 기존 Row를 찾아 없으면 CREATE, 있으면 UPDATE."""
        try:
            existing = self._client.find_project(event.project_id)
        except NotionAPIError as exc:
            # §38: Notion API 실패 시 Event REJECTED/삭제 금지, Retry 필요.
            # 이 함수는 Event를 지우지 않는다 — 호출자가 원본 Event를 보존한다.
            return SyncResult(
                status=SyncStatus.NOTION_RETRY_REQUIRED,
                event_id=event.event_id,
                project_id=event.project_id,
                error=str(exc),
                status_code=exc.status_code,
            )

        if existing is None:
            return self._create(event)
        return self._update(event, existing)

    def _create(self, event: Event) -> SyncResult:
        properties = build_create_properties(
            event, project_name=humanize_project_id(event.project_id)
        )
        try:
            page = self._client.create_project(properties)
        except NotionAPIError as exc:
            return SyncResult(
                status=SyncStatus.NOTION_RETRY_REQUIRED,
                event_id=event.event_id,
                project_id=event.project_id,
                error=str(exc),
                status_code=exc.status_code,
            )
        return SyncResult(
            status=SyncStatus.NOTION_CREATED,
            event_id=event.event_id,
            project_id=event.project_id,
            page_id=page.get("id"),
        )

    def _update(self, event: Event, existing: dict) -> SyncResult:
        page_id = existing.get("id")

        # §62 Duplicate 방어: 이미 반영된 event_id가 다시 도착하면 재적용하지 않는다.
        #
        # `fit_key()` on this side too, for `NotionClient.find_project()`'s
        # reason: an `event_id` past Notion's 2,000-character text limit is
        # **stored** shortened (C50), so comparing the stored value against
        # the raw id would never match and this guard would quietly stop
        # guarding. §29-30's timestamp guard usually catches the
        # re-application a step later — but the two exist precisely because
        # neither covers the other's case, and the case this one is alone in
        # is a stored `Last Updated` that cannot be compared (a human's
        # date-picker entry, docs/04 §43), where the code below proceeds.
        if extract_last_event_id(existing) == fit_key(event.event_id):
            return SyncResult(
                status=SyncStatus.NOTION_SKIPPED_OLD_EVENT,
                event_id=event.event_id,
                project_id=event.project_id,
                page_id=page_id,
            )

        # §29-30 Late Event 보호: 현재 Last Updated보다 과거/동시 timestamp인
        # Event는 Current State를 되돌리지 않는다.
        current_last_updated = extract_last_updated(existing)
        comparison_note: str | None = None
        if current_last_updated is not None:
            stored = _as_comparable_timestamp(current_last_updated, event.timestamp)
            if stored is None:
                # docs/04 §43 says humans write in this database too, and
                # §45 reserves fields for them — so the value read back here
                # is not always one this system wrote.
                #
                # Measured against `ExecutionPlanSync.sync()` before this
                # guard existed. Notion's date picker used *without a time*
                # stores `{"start": "2026-08-17"}`, which parses to a naive
                # datetime, and comparing it to the Event's tz-aware
                # timestamp raises:
                #
                #     TypeError: can't compare offset-naive and offset-aware
                #
                # `sync()` catches only `NotionAPIError`, so that escaped the
                # module entirely. `app/runner.py` turns it into
                # NOTION_FAILED with retryability UNKNOWN, and the Event goes
                # into the retry queue — where it fails identically on every
                # run, forever, because nothing about a stored date changes
                # by retrying. One click in Notion's date picker was enough
                # to park an Event permanently.
                #
                # Treated as "unknown", which is the same epistemic state as
                # the cleared-date case immediately above it, and gets the
                # same answer: proceed. That also makes it self-healing —
                # the update below writes a proper `Last Updated`, so the
                # next Event for this project compares normally.
                #
                # Not silent: the reason travels on `SyncResult.error`, and
                # `app/runner._log_notion_sync()` writes it to
                # notion_sync.log whenever it is set.
                comparison_note = (
                    f"Late Event guard skipped: stored Last Updated "
                    f"{current_last_updated!r} is not a comparable ISO-8601 "
                    f"timestamp with an offset"
                )
            elif datetime.fromisoformat(event.timestamp) <= stored:
                # The decision is docs/04 §29-30's and is not touched here:
                # past *or equal* does not move Current State. What is added
                # is which of the two it was (C40).
                #
                # They are different events with the same verdict:
                #
                #   strictly older   a genuine Late Event. §63's case, the
                #                    guard working, nothing lost — Notion
                #                    correctly keeps the newer state.
                #   equal            BACKLOG E-23. Two *different* Events at
                #                    the same instant, which is the normal
                #                    path rather than a rare tie: a Signal
                #                    with no timestamp of its own gets that
                #                    date's midnight (docs/06 §12), the same
                #                    value for every Signal of that day. So
                #                    for one project, only the day's first
                #                    Event reaches Notion. Company History
                #                    keeps both.
                #
                # Measured before this branch existed — the two were
                # byte-identical in the returned object:
                #
                #   same instant #2   NOTION_SKIPPED_OLD_EVENT  error=None
                #   genuinely older   NOTION_SKIPPED_OLD_EVENT  error=None
                #
                # Nothing downstream could separate a View working as
                # specified from a View silently diverging from the Source.
                # The status is unchanged (docs §32-37 enumerates it, and
                # adding a value there is a spec change); the note is not a
                # new decision, only the reason for the existing one, and it
                # travels the way `comparison_note` below already does.
                same_instant = datetime.fromisoformat(event.timestamp) == stored
                return SyncResult(
                    status=SyncStatus.NOTION_SKIPPED_OLD_EVENT,
                    event_id=event.event_id,
                    project_id=event.project_id,
                    page_id=page_id,
                    error=(
                        "same-instant skip: another Event already set Current "
                        f"State at {event.timestamp} (BACKLOG E-23); Company "
                        "History keeps both, the Notion row keeps the first"
                        if same_instant
                        else None
                    ),
                )

        properties = build_update_properties(event)
        try:
            self._client.update_project(page_id, properties)
        except NotionAPIError as exc:
            return SyncResult(
                status=SyncStatus.NOTION_RETRY_REQUIRED,
                event_id=event.event_id,
                project_id=event.project_id,
                page_id=page_id,
                error=str(exc),
                status_code=exc.status_code,
            )
        return SyncResult(
            status=SyncStatus.NOTION_UPDATED,
            event_id=event.event_id,
            project_id=event.project_id,
            page_id=page_id,
            error=comparison_note,
        )
