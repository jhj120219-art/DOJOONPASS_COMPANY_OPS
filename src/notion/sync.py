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


@dataclass(frozen=True)
class SyncResult:
    status: SyncStatus
    event_id: str
    project_id: str
    page_id: str | None = None
    error: str | None = None


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
        if extract_last_event_id(existing) == event.event_id:
            return SyncResult(
                status=SyncStatus.NOTION_SKIPPED_OLD_EVENT,
                event_id=event.event_id,
                project_id=event.project_id,
                page_id=page_id,
            )

        # §29-30 Late Event 보호: 현재 Last Updated보다 과거/동시 timestamp인
        # Event는 Current State를 되돌리지 않는다.
        current_last_updated = extract_last_updated(existing)
        if current_last_updated is not None and datetime.fromisoformat(
            event.timestamp
        ) <= datetime.fromisoformat(current_last_updated):
            return SyncResult(
                status=SyncStatus.NOTION_SKIPPED_OLD_EVENT,
                event_id=event.event_id,
                project_id=event.project_id,
                page_id=page_id,
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
            )
        return SyncResult(
            status=SyncStatus.NOTION_UPDATED,
            event_id=event.event_id,
            project_id=event.project_id,
            page_id=page_id,
        )
