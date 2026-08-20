"""Notion Client (docs/04_NOTION_SYNC_SPEC.md §66 items 1-5): authentication,
database connectivity, health check, and project row lookup/create/update —
built on top of NotionTransport. This module does not decide Create vs
Update policy or Event-to-Property mapping; that is ExecutionPlanSync's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .properties import fit_key
from .transport import NotionAPIError, NotionTransport


@dataclass(frozen=True)
class HealthCheckResult:
    """docs §66 items 1-2: Notion API 연결 성공 + PROJECTS Database 접근 성공."""

    ok: bool
    database_id: str
    error: str | None = None


class NotionClient:
    def __init__(self, *, transport: NotionTransport, database_id: str):
        self._transport = transport
        self._database_id = database_id

    def health_check(self) -> HealthCheckResult:
        try:
            self._transport.retrieve_database(self._database_id)
        except NotionAPIError as exc:
            return HealthCheckResult(ok=False, database_id=self._database_id, error=str(exc))
        return HealthCheckResult(ok=True, database_id=self._database_id)

    def get_database_parent(self) -> Mapping[str, Any]:
        """The `parent` object of the Database this client is bound to.

        Notion returns one of:
            {"type": "page_id",   "page_id": "..."}    -> nested under a Page
            {"type": "workspace", "workspace": true}   -> at workspace root

        Used by `notion.dashboard` to place the OPS_* databases beside an
        already-existing database instead of inventing a new location.
        """
        database = self._transport.retrieve_database(self._database_id)
        return database.get("parent", {})

    def search_pages(self) -> list[Mapping[str, Any]]:
        """Pages this integration can see (read-only diagnostic)."""
        return self._transport.search_pages()

    def get_database_schema(self) -> Mapping[str, Any]:
        """Notion Database Auto Bootstrap: 현재 Property 정의(스키마) 조회.

        `retrieve_database()`가 반환하는 전체 Database 객체의 `properties`
        키 — Property 값이 아니라 Property *정의*다.
        """
        database = self._transport.retrieve_database(self._database_id)
        return database.get("properties", {})

    def rename_property(self, *, current_name: str, new_name: str) -> Mapping[str, Any]:
        """Notion Database Auto Bootstrap V1.1 (CEO/COO 정책 변경): Title
        Property에 한해서만 예외적으로 허용되는 Rename. 이 메서드 자체는
        어떤 Property 이름이든 바꿀 수 있는 범용 기능이며, "Title에만
        적용한다"는 제약은 호출자(`notion.bootstrap`)의 책임이다 — 다른
        Property는 여전히 `create_database_properties()`만 사용해야 한다.
        """
        return self._transport.update_database(
            self._database_id, {current_name: {"name": new_name}}
        )

    def create_database_properties(self, properties: Mapping[str, Any]) -> Mapping[str, Any]:
        """Notion Database Auto Bootstrap: 없는 Property만 새로 정의한다.

        호출자(notion.bootstrap)가 이미 존재하는 Property 이름을 여기 넘기지
        않는 것으로 "기존 Property를 절대 수정하지 않는다"를 보장한다 — 이
        메서드 자체는 무엇이 이미 존재하는지 판단하지 않는다.
        """
        return self._transport.update_database(self._database_id, properties)

    def find_project(self, project_id: str) -> Mapping[str, Any] | None:
        """docs §7: `project_id`로 기존 PROJECTS Row를 검색한다. 없으면 None.

        `fit_key()` because the row was **written** through it — a
        `project_id` past Notion's 2,000-character text limit is stored
        shortened (`properties.RICH_TEXT_LIMIT`), and looking it up by the
        full value would find nothing and create a second row on every single
        run. The write side and this side call the same pure function so that
        "the key we store is the key we look up" needs no shared state; the
        function is a no-op for every ordinary id.
        """
        filter_ = {
            "property": "Project ID",
            "rich_text": {"equals": fit_key(project_id)},
        }
        response = self._transport.query_database(self._database_id, filter_)
        results = response.get("results") or []
        return results[0] if results else None

    def create_project(self, properties: Mapping[str, Any]) -> Mapping[str, Any]:
        """docs §6 CREATE branch.

        Generic "create one row in the database this client is bound to" —
        named for its original PROJECTS use. `notion.dashboard` reuses it
        with a client bound to an OPS_* database instead of adding a
        second, identical method.
        """
        return self._transport.create_page(self._database_id, properties)

    def find_by_title(self, *, property_name: str, value: str) -> Mapping[str, Any] | None:
        """The row whose **title** property `property_name` equals `value`.

        `find_project()` cannot be reused for this: it filters on
        `rich_text`, and a database's title property is a different Notion
        type with a different filter key. OPS_RUNS keys its rows by `Run ID`,
        which is the title, so without this there was no way to ask "does a
        row for this run already exist?".
        """
        filter_ = {"property": property_name, "title": {"equals": value}}
        response = self._transport.query_database(self._database_id, filter_)
        results = response.get("results") or []
        return results[0] if results else None

    def find_or_create_by_title(
        self, *, property_name: str, value: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Return the existing row for `value`, creating it only if absent.

        The guard `notion.sync.ExecutionPlanSync` has always had, for the
        same reason: a write can reach Notion and still surface as an
        exception (a reset connection after the request landed, a proxy
        timing out on the response). A retry then creates a second row for
        work that was already recorded, and nothing downstream can tell the
        two apart.

        Not a transaction — two callers racing can still both see "absent".
        Nothing here races: `record_run()` and `drain_pending()` run inside
        one Runner execution, and the Runner holds the Scheduler lock.
        """
        existing = self.find_by_title(property_name=property_name, value=value)
        if existing is not None:
            return existing
        return self.create_project(properties)

    def create_database(
        self, *, parent_page_id: str, title: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Create a brand-new Database under `parent_page_id`.

        Used only by `notion.dashboard`'s one-time bootstrap (CEO Decision
        ④). Unlike every other method here, this does not act on
        `self._database_id` — it creates a database that does not exist yet.
        """
        return self._transport.create_database(parent_page_id, title, properties)

    def update_project(self, page_id: str, properties: Mapping[str, Any]) -> Mapping[str, Any]:
        """docs §6 UPDATE branch."""
        return self._transport.update_page(page_id, properties)
