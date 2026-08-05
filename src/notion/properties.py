"""Execution Event -> Notion PROJECTS property mapping
(docs/04_NOTION_SYNC_SPEC.md §8-28).

Only the V1 automated Property set (§43) is built here: Project, Project ID,
Owner, Source, Status, Current Milestone, Blocker, Last Updated, Completed
Date, Last Event ID, Last Event Type. §44-45 fields (Critical Path, Launch
Readiness, CEO-reserved fields, etc.) are explicitly not automated by V1
Event Sync and are not touched by this module.
"""

from __future__ import annotations

from typing import Any, Mapping

from events import Event

# docs §11: role -> 사람이 읽기 쉬운 표시명. Spec은 이 네 개만 정의한다
# (docs/02_EVENT_SCHEMA.md ROLES와 동일한 집합).
ROLE_DISPLAY_NAMES = {
    "CTO_BACKEND": "CTO Backend",
    "CTO_FRONTEND": "CTO Frontend",
    "CMO": "CMO",
    "COO": "COO",
}


def humanize_project_id(project_id: str) -> str:
    """§9-10: Project(표시용 이름)를 project_id로부터 만든다.

    Spec은 "Search Frontend" <- "SEARCH_FRONTEND" 같은 예시만 줄 뿐 정확한 변환
    규칙을 정의하지 않는다. underscore -> space + Title Case를 최소 근사치로
    사용한다(예: CONTENT_OS -> "Content Os", 대문자 약어 보존은 하지 않음).
    실제 서비스 표기와 다르면 수동으로 Notion에서 고칠 수 있다 — Property는
    표시용이고 project_id가 시스템 식별자다(§9-10).
    """

    return project_id.replace("_", " ").title()


def _title(text: str) -> dict:
    return {"title": [{"text": {"content": text}}]}


def _rich_text(text: str) -> dict:
    if not text:
        return {"rich_text": []}
    return {"rich_text": [{"text": {"content": text}}]}


def _select(name: str) -> dict:
    return {"select": {"name": name}}


def _date(iso_timestamp: str) -> dict:
    return {"date": {"start": iso_timestamp}}


def _type_specific_properties(event: Event) -> dict[str, Any]:
    """§20-28: Event Type별 추가 Property."""
    properties: dict[str, Any] = {}

    if event.event_type == "BLOCKED":
        # §22
        properties["Blocker"] = _rich_text(event.blocker or "")
    elif event.event_type == "RESUMED":
        # §23
        properties["Blocker"] = _rich_text("")
    elif event.event_type == "MILESTONE_COMPLETED":
        # §24
        if event.milestone:
            properties["Current Milestone"] = _rich_text(event.milestone)
    elif event.event_type == "COMPLETED":
        # §25
        properties["Completed Date"] = _date(event.timestamp)
        properties["Blocker"] = _rich_text("")
    elif event.event_type == "ISSUE_RESOLVED":
        # §27: "모든 ISSUE_RESOLVED가 Project 상태를 변경한다고 가정하지 않는다.
        # Event의 실제 status를 따른다" — status가 더 이상 BLOCKED가 아닐 때만
        # Blocker를 지운다.
        if event.status != "BLOCKED":
            properties["Blocker"] = _rich_text("")
    # STARTED(§21), CANCELLED(§26), DECISION_APPROVED(§28)는 공통 필드
    # (Status/Last Updated/Last Event ID/Last Event Type) 외 추가 Property가 없다.

    return properties


def build_create_properties(event: Event, *, project_name: str) -> dict[str, Any]:
    """§8, §21: Row가 없을 때 CREATE에 사용하는 전체 Property."""
    properties: dict[str, Any] = {
        "Project": _title(project_name),
        "Project ID": _rich_text(event.project_id),
        "Owner": _select(ROLE_DISPLAY_NAMES.get(event.role, event.role)),
        "Source": _select(event.source),
        "Status": _select(event.status),
        "Last Updated": _date(event.timestamp),
        "Last Event ID": _rich_text(event.event_id),
        "Last Event Type": _select(event.event_type),
    }
    properties.update(_type_specific_properties(event))
    return properties


def build_update_properties(event: Event) -> dict[str, Any]:
    """§20-28: 기존 Row가 있을 때 UPDATE에 사용하는 Property.

    Project/Project ID/Owner/Source는 §9-12에서 최초 생성 시점 정보로만
    설명되며, 매 Update마다 다시 덮어써야 한다는 근거가 spec에 없으므로
    포함하지 않는다.
    """
    properties: dict[str, Any] = {
        "Status": _select(event.status),
        "Last Updated": _date(event.timestamp),
        "Last Event ID": _rich_text(event.event_id),
        "Last Event Type": _select(event.event_type),
    }
    properties.update(_type_specific_properties(event))
    return properties


def _extract_rich_text(prop: dict | None) -> str | None:
    if not prop:
        return None
    items = prop.get("rich_text") or []
    if not items:
        return None
    return items[0].get("text", {}).get("content")


def extract_last_updated(project_row: Mapping[str, Any]) -> str | None:
    """§16, §29-30 Late Event 보호 비교에 쓰는 저장된 Last Updated ISO timestamp."""
    props = project_row.get("properties", {})
    date_prop = props.get("Last Updated") or {}
    date_value = date_prop.get("date")
    return date_value.get("start") if date_value else None


def extract_last_event_id(project_row: Mapping[str, Any]) -> str | None:
    """§18, §62 Duplicate 방어 확인에 쓰는 저장된 Last Event ID."""
    props = project_row.get("properties", {})
    return _extract_rich_text(props.get("Last Event ID"))
