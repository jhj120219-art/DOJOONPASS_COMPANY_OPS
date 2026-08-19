"""Notion Database Auto Bootstrap (docs/04_NOTION_SYNC_SPEC.md §8, §66-item-2).

Creates whichever of the 11 PROJECTS Database Properties (§8) are missing,
using only property-type payloads the Notion API actually accepts to
*create* — and never touches a Property that already exists (by name).
This is a one-time setup helper, not part of the Runtime pipeline: nothing
here is called by `src/app/runner.py` or `ExecutionPlanSync`.

Two spec-driven type choices that are NOT literal §8 wording, both already
required for consistency with the existing, tested `properties.py`/
`sync.py` code:

    Status          -> created as Select, not Notion's "status" type.
                       §8 itself allows "Status 또는 Select", and the
                       Notion API does not support creating a new
                       "status"-type property at all (existing ones can be
                       read/written, but not instantiated via the API) —
                       so Select is the only creatable option. `sync.py`
                       already writes Status as `{"select": ...}`
                       (properties.py::_select), so this keeps the two
                       consistent.
    Last Event Type -> Select, per §8's own table (not Rich Text).

Project (Title) is a special case: every Notion database always has
exactly one Title property already, so it can never be "created" here —
only recognized (name is already "Project") or renamed.

V1.1 policy change (CEO/COO decision, this Sprint — "Bootstrap Title
Property Auto Rename"): the prior absolute principle "Property 이름을
임의로 변경하지 않는다" is narrowed to a single, explicit exception —
the Title property only. Reason: this exact manual step ("Name" ->
"Project" in the Notion UI) was attempted twice by a human operator and
did not take effect either time (see docs/13_NOTION_ENVIRONMENT_SETUP.md
history), so it is automated here instead of being retried manually
again. Every other Property is untouched by this module exactly as
before — this exception does not widen to any other Property's name,
type, or options.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from .client import NotionClient
from .transport import NotionAPIError

# docs/04_NOTION_SYNC_SPEC.md §8, in table order. Each value is the exact
# Notion API property-schema payload used to *create* that property.
TARGET_PROPERTIES: dict[str, dict[str, Any]] = {
    "Project": {"title": {}},
    "Project ID": {"rich_text": {}},
    "Owner": {"select": {}},
    "Source": {"select": {}},
    "Status": {"select": {}},
    "Current Milestone": {"rich_text": {}},
    "Blocker": {"rich_text": {}},
    "Last Updated": {"date": {}},
    "Completed Date": {"date": {}},
    "Last Event ID": {"rich_text": {}},
    "Last Event Type": {"select": {}},
}

TITLE_PROPERTY_NAME = "Project"


class PropertyOutcome(enum.Enum):
    EXISTS = "EXISTS"
    CREATED = "CREATED"
    SKIPPED = "SKIPPED"
    RENAMED = "RENAMED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PropertyBootstrapReport:
    name: str
    outcome: PropertyOutcome
    detail: str | None = None


@dataclass(frozen=True)
class BootstrapResult:
    reports: tuple[PropertyBootstrapReport, ...]

    @property
    def created(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.reports if r.outcome is PropertyOutcome.CREATED)

    @property
    def existing(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.reports if r.outcome is PropertyOutcome.EXISTS)

    @property
    def skipped(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.reports if r.outcome is PropertyOutcome.SKIPPED)

    @property
    def renamed(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.reports if r.outcome is PropertyOutcome.RENAMED)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.reports if r.outcome is PropertyOutcome.FAILED)


def _has_title_property(current_properties: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Every Notion database has exactly one Title property already. Returns
    (True, its name) if found among `current_properties`, else (False, None).
    """
    for name, definition in current_properties.items():
        if definition.get("type") == "title":
            return True, name
    return False, None


def diff_properties(
    current_properties: Mapping[str, Any],
    *,
    targets: Mapping[str, dict[str, Any]] | None = None,
    title_property: str | None = None,
) -> tuple[dict[str, dict[str, Any]], list[PropertyBootstrapReport]]:
    """§4 Spec 비교: 없는 Property만 골라 생성 대상(payload dict)과, 이미
    존재하는 Property의 EXISTS 결과를 돌려준다. CREATED 여부는 실제 생성
    호출 이후에만 확정되므로 이 함수가 직접 보고하지 않는다.

    Title은 여기서 다루지 않는다 — V1.1부터 `_bootstrap_title_property`가
    전담한다(생성이 아니라 Rename 대상이므로 이 함수의 "없으면 생성" 로직과
    성격이 다르다).

    `targets`/`title_property`는 C36에서 매개변수가 됐다. `notion.dashboard`의
    `OPS_RUNS`도 같은 질문("어느 Property가 빠져 있는가")을 하게 됐고, 그 답을
    두 번 쓰는 것이 곧 두 답이 갈라지는 방법이다. 기본값은 §8의 PROJECTS
    그대로라 기존 호출자는 한 글자도 바뀌지 않는다.
    """
    targets = TARGET_PROPERTIES if targets is None else targets
    title_property = TITLE_PROPERTY_NAME if title_property is None else title_property

    to_create: dict[str, dict[str, Any]] = {}
    decided: list[PropertyBootstrapReport] = []

    for name, payload in targets.items():
        if name == title_property:
            continue

        if name in current_properties:
            decided.append(PropertyBootstrapReport(name, PropertyOutcome.EXISTS))
            continue

        to_create[name] = payload

    return to_create, decided


def _bootstrap_title_property(
    client: NotionClient,
    current_properties: Mapping[str, Any],
    title_property: str | None = None,
) -> PropertyBootstrapReport:
    """V1.1 정책 변경(CEO/COO, 이번 Sprint)에 따른 Title 전용 예외 처리.

    - 이미 이름이 목표 이름이면: 아무 작업도 하지 않고 SKIPPED(작업 3).
    - 다른 이름(예: `Name`)이면: `rename_property()`로 자동 Rename 시도.
      성공하면 RENAMED.
    - Rename이 실패하면(NotionAPIError) 예외를 여기서 흡수하고 FAILED로
      보고한다 — Runtime을 중단시키지 않는다(작업 6 "Rename 실패 시 Runtime
      중단 없이 Error 반환").
    - Title Property가 전혀 없는 경우(이론상 발생하지 않음 — Notion이 항상
      정확히 하나를 강제 생성)는 방어적으로 FAILED 처리한다.

    `title_property`가 매개변수인 이유는 `diff_properties()`와 같다 —
    `OPS_RUNS`의 Title은 `Run ID`이고, 손으로 만든 Database는 Notion이 강제한
    `Name`을 그대로 갖고 있을 가능성이 높다. Notion API는 두 번째 Title을
    만들 수 없으므로 그 경우 Rename이 유일한 길이다.
    """
    title_property = TITLE_PROPERTY_NAME if title_property is None else title_property
    title_exists, title_name = _has_title_property(current_properties)

    if not title_exists:
        return PropertyBootstrapReport(
            title_property,
            PropertyOutcome.FAILED,
            detail="no Title property found; Notion API cannot add one via update",
        )

    if title_name == title_property:
        return PropertyBootstrapReport(title_property, PropertyOutcome.SKIPPED)

    try:
        client.rename_property(current_name=title_name, new_name=title_property)
    except NotionAPIError as exc:
        return PropertyBootstrapReport(
            title_property,
            PropertyOutcome.FAILED,
            detail=f"rename {title_name!r} -> {title_property!r} failed: {exc}",
        )

    return PropertyBootstrapReport(
        title_property,
        PropertyOutcome.RENAMED,
        detail=f"renamed {title_name!r} -> {title_property!r}",
    )


def bootstrap_database(client: NotionClient) -> BootstrapResult:
    """실행 순서: Database 조회 -> Title Rename(필요 시, V1.1) -> 나머지
    Property Spec 비교 -> 없는 Property만 생성 -> 존재하는 Property는 그대로 둔다.
    """
    current_properties = client.get_database_schema()

    title_report = _bootstrap_title_property(client, current_properties)

    if title_report.outcome is PropertyOutcome.RENAMED:
        # The rename just mutated the live Database. If the old Title name
        # happened to collide with another §8 Target Property (e.g. a Title
        # named "Status"), diffing against the pre-rename snapshot would
        # still see that name as EXISTS and skip creating the real "Status"
        # property, which the rename just consumed. Re-fetching reflects
        # what the Database actually looks like now.
        current_properties = client.get_database_schema()

    to_create, decided = diff_properties(current_properties)
    reports: list[PropertyBootstrapReport] = [title_report, *decided]

    if to_create:
        client.create_database_properties(to_create)
        for name in to_create:
            reports.append(PropertyBootstrapReport(name, PropertyOutcome.CREATED))

    # TARGET_PROPERTIES 순서대로 정렬해서 보고한다(§8 표 순서, 출력 예시와 일치).
    order = {name: i for i, name in enumerate(TARGET_PROPERTIES)}
    reports.sort(key=lambda r: order[r.name])
    return BootstrapResult(reports=tuple(reports))


def format_report(result: BootstrapResult) -> str:
    """출력 예시 형식: 'Project ............. EXISTS' 한 줄씩."""
    if not result.reports:
        return ""
    width = max(len(r.name) for r in result.reports) + 2
    lines = []
    for r in result.reports:
        dots = "." * (width - len(r.name) + 3)
        line = f"{r.name} {dots} {r.outcome.value}"
        if r.detail:
            line += f"  ({r.detail})"
        lines.append(line)
    return "\n".join(lines)
