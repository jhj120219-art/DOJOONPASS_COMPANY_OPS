"""Execution Event -> Notion PROJECTS property mapping
(docs/04_NOTION_SYNC_SPEC.md §8-28).

Only the V1 automated Property set (§43) is built here: Project, Project ID,
Owner, Source, Status, Current Milestone, Blocker, Last Updated, Completed
Date, Last Event ID, Last Event Type. §44-45 fields (Critical Path, Launch
Readiness, CEO-reserved fields, etc.) are explicitly not automated by V1
Event Sync and are not touched by this module.
"""

from __future__ import annotations

import hashlib
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


# Notion's own cap on the text of ONE `rich_text` / `title` item. A value
# over it is not truncated by Notion and it is not partially accepted — the
# whole request comes back HTTP 400, and `sync.PERMANENTLY_REFUSING_STATUS_CODES`
# classifies 400 as an answer that will not change by retrying. So an Event
# whose payload is one character too long is an Event that:
#
#     never reaches the PROJECTS View          the row keeps yesterday's state
#     stays in `notion_retry_queue.json`       nothing ever removes it (A-22)
#     holds ATTENTION open as PERMANENT        an alert nobody can clear
#
# and none of that is visible as "the text was too long" anywhere except in
# the 400's own body.
#
# **Every authored field this module sends can exceed it.** `validate_event()`
# type-checks `blocker`, `milestone`, `project_id` and `event_id` and bounds
# none of them (docs/02 §4 declares them `string`, with no length). Measured
# through the real builders: a 3,600-character `blocker` validates, folds,
# and builds a 3,600-character `Blocker` property; the same holds for a
# 2,500-character `milestone`, `project_id` (which lands in *two* properties,
# `Project` and `Project ID`) and `event_id`.
#
# C49 bounded the one string this project *generates* for Notion
# (`controltower/projection.RICH_TEXT_LIMIT`, `Desktops Reporting`) for
# exactly this reason. These are the ones a **person types**, on another
# Desktop, and a pasted stack trace in a `blocker` is the ordinary way to
# reach 2,000 characters — not an adversarial one.
#
# The number lives here rather than in `controltower/` because it is a fact
# about the Notion API and this is the module that speaks it; `projection.py`
# imports it rather than restating it (C28: no second opinion).
RICH_TEXT_LIMIT = 2000


def _fit_text(text: str) -> str:
    """`text`, short enough for one Notion text item, and **visibly** so.

    Truncation ends with `…`, never silently: Notion is a View and the Event
    file under `runtime/events/processed/` is the Source (docs/14 §1), so
    nothing is lost by shortening the View — but a reader who cannot tell a
    2,000-character blocker from a blocker that *was* 2,000 characters is
    being told something false. Same posture, and the same `…`, as
    `controltower/projection._desktops_reporting()`.

    A non-string is handed back untouched rather than raising, and that is a
    deliberate limit on this change's blast radius. `PropertyHelperNullGuardTests`
    pins what these builders do with a null — `_title(None)` emits
    `{"content": None}` — and records *why* it cannot happen through the
    production path. This function is about **length**; turning a null into a
    `TypeError` here would convert that verified non-defect into an
    uncaught exception inside a pipeline step, which is a different change
    that nobody asked for.
    """
    if not isinstance(text, str) or len(text) <= RICH_TEXT_LIMIT:
        return text
    return text[: RICH_TEXT_LIMIT - 1] + "…"


def fit_key(text: str) -> str:
    """Same bound, for a value Notion rows are **looked up** by.

    Public where `_fit_text()` is not, and the asymmetry is the contract:
    `NotionClient.find_project()` has to shorten a filter value exactly the
    way this module shortened the stored one, so this is the module's one
    export on the subject. `DeadCapabilityInventoryTests` is what keeps that
    honest — it reported `_fit_text` (then public) the moment it had no
    caller outside
    `fit_properties()`.

    `Project ID` is the property `NotionClient.find_project()` filters on and
    `Last Event ID` is the one docs/04 §62's duplicate guard compares, so for
    these two "short enough" is not the only requirement — two different
    values must not become the same string. Plain truncation would merge two
    projects whose ids agree on their first 1,999 characters into one row,
    which is a worse failure than the 400 it replaces: the 400 writes
    nothing, and a merged row writes one project's state over another's.

    The tail is therefore a digest of the **whole** value, so the mapping is
    injective for every input anyone will ever have while staying a pure
    function of the input — which is what lets the write side and the lookup
    side agree without either of them storing anything.

    Non-strings pass through for `_fit_text()`'s reason.
    """
    if not isinstance(text, str) or len(text) <= RICH_TEXT_LIMIT:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return text[: RICH_TEXT_LIMIT - len(digest) - 1] + "…" + digest


# The two properties Notion rows are **looked up** or **compared** by, and
# therefore the two that `fit_properties()` shortens with `fit_key()` rather
# than `_fit_text()`. Named here so the payload boundary and
# `NotionClient.find_project()` cannot disagree about which they are.
KEY_PROPERTIES: frozenset = frozenset({"Project ID", "Last Event ID"})


def fit_properties(properties: dict) -> dict:
    """One PROJECTS payload, with every text value bounded for the API.

    **Applied on the way out, never on the way in** — the same split
    `controltower/dashboard.to_payload()` makes for redaction, and for a
    reason measured rather than assumed. The first version of this bound sat
    inside `_rich_text()`, which looks like the payload boundary and is not:
    `controltower/rollup._blocker_change()` calls
    `_type_specific_properties()` to ask docs/04 §20-28's *rule* what an
    Event does to blocker state (C28 — no second opinion about it), so a
    truncation there shortened `ProjectRollup.open_blocker` as well. The
    Control Tower would then have been reporting a blocker it had the full
    text of, cut to Notion's convenience, on a screen that never talks to
    Notion. Measured through the real pipeline: a 3,411-character blocker
    came back off the rollup at 2,000.

    So `_rich_text()` / `_title()` stay verbatim statements of the spec's
    property mapping, and the two `build_*_properties()` functions — the only
    two callers that are actually building a request — end here.
    """
    fitted: dict = {}
    for name, value in properties.items():
        shorten = fit_key if name in KEY_PROPERTIES else _fit_text
        if not isinstance(value, dict):  # pragma: no cover - builders emit dicts
            fitted[name] = value
            continue
        for kind in ("title", "rich_text"):
            items = value.get(kind)
            if not items:
                continue
            value = dict(value)
            value[kind] = [
                dict(item, text=dict(item["text"], content=shorten(item["text"]["content"])))
                if isinstance(item.get("text"), dict)
                else item
                for item in items
            ]
        fitted[name] = value
    return fitted


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
    # ASSIGNED(§18.1)도 추가 Property가 없다. 누가 맡았는지는 Event의
    # `role`이고, docs/02 §8이 source→role을 고정하므로 그 값은 **실제로 그
    # 일을 받은 팀**이다 — 다른 팀이 대신 주장하면 PairMismatch로 잡힌다.
    #
    # **다만 그 role이 PROJECTS Row의 `Owner`를 바꾸지는 않는다.** `Owner`는
    # `build_create_properties()`에만 있고 §9-12에 따라 최초 생성 시점 정보다
    # (바로 아래 `build_update_properties()`의 주석이 그 근거를 적는다).
    # 그러므로 담당 이동은 Control Tower의 `OpenItem.assigned_team`이 나르고,
    # Notion Row의 Owner를 매번 덮어쓰는 것은 spec 변경이며 하지 않았다.
    #
    # STARTED(§21), CANCELLED(§26), DECISION_APPROVED(§28)는 공통 필드
    # (Status/Last Updated/Last Event ID/Last Event Type) 외 추가 Property가 없다.
    #
    # C149가 더한 넷도 같다. `AT_RISK`는 CANCELLED와 같은 자리에 선다 — 자기
    # `status`를 고정하는 것이 전부이고(그 값은 위 공통 필드의 `Status`가 이미
    # 쓴다), 위험의 내용은 `summary`에 있다. `ISSUE_RAISED`는 **Blocker를 쓰지
    # 않는다**: 제기된 Issue가 곧 프로젝트를 멈춘 것은 아니고, 멈췄다면 그것을
    # 말하는 Event는 §22의 `BLOCKED`다. 여기서 Blocker를 쓰면 두 Event Type이
    # 같은 사실을 주장하게 되고, §27이 ISSUE_RESOLVED에 대해 피한 바로 그
    # 겹침이 된다. `DECISION_REQUIRED` / `DECISION_REJECTED`는 §28의
    # DECISION_APPROVED와 대칭이므로 추가 Property가 없다. `EXECUTED`도 같다 —
    # 승인된 Decision이 실제로 실행됐다는 사실이며, 그 자체로 Project의
    # 상태를 바꾸지 않는다(바꿨다면 그것을 말하는 Event가 따로 있다).

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
    return fit_properties(properties)


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
    return fit_properties(properties)


def _extract_rich_text(prop: dict | None) -> str | None:
    """The whole text of a `rich_text` property value.

    Notion does not store a rich_text value as one item. It stores one item
    per *run of identical formatting*, and items that are not literal text
    (a mention, an equation) carry no `"text"` key at all. Reading
    `items[0]["text"]["content"]` therefore returned a prefix, or None,
    for values a person had touched — and docs/04 §43 says people do touch
    this database.

    Measured against `ExecutionPlanSync._update()`: with `Last Event ID`
    holding the same id in two formatting runs (`EVT-` + `1`), §62's
    duplicate guard compared `"EVT-"` to `"EVT-1"`, missed, and re-applied
    an Event it had already applied. Same with the id stored as a mention.
    (§29-30's timestamp guard usually catches the re-application a step
    later, which is why this stayed invisible — but "usually" is not what
    §62 promises, and the two guards exist because neither covers the
    other's case.)

    `plain_text` first because that is the field Notion always fills and the
    one `dashboard._page_title()` already reads for the title property —
    same Notion shape, same answer, and now the same way of asking.
    `text.content` remains as a fallback so a hand-built payload without
    `plain_text` still reads.
    """
    if not prop:
        return None
    items = prop.get("rich_text") or []
    if not items:
        return None
    parts = []
    for item in items:
        text = item.get("plain_text")
        if text is None:
            text = (item.get("text") or {}).get("content") or ""
        parts.append(text)
    return "".join(parts)


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
