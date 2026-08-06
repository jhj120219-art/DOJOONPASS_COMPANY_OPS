"""Notion API HTTP transport (docs/04_NOTION_SYNC_SPEC.md §3, §38, §66 items 1-2).

Defines only the seam NotionClient depends on: retrieve/query/create/update
calls against the Notion REST API. RealNotionTransport is the live network
implementation (stdlib-only, no third-party HTTP dependency — this project
has none). InMemoryNotionTransport is a test/dev double so the Mock Tests in
docs §57-64 never need a live Notion workspace — same role
InMemorySeenEventStore plays for Collector.
"""

from __future__ import annotations

import abc
import json
import urllib.error
import urllib.request
from typing import Any, Mapping

NOTION_API_BASE_URL = "https://api.notion.com/v1"
NOTION_API_VERSION = "2022-06-28"


class NotionAPIError(Exception):
    """Raised when a Notion API call fails (network error or non-2xx response)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class NotionTransport(abc.ABC):
    """Low-level Notion REST API operations NotionClient depends on."""

    @abc.abstractmethod
    def retrieve_database(self, database_id: str) -> Mapping[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def query_database(
        self, database_id: str, filter_: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def create_page(
        self, database_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def update_database(
        self, database_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Add/update Database-level property *definitions* (schema), not a
        page's values. Used only by notion.bootstrap (Database Auto
        Bootstrap) — ExecutionPlanSync never calls this."""
        raise NotImplementedError

    def search_pages(self) -> list[Mapping[str, Any]]:
        """Pages this integration can actually see (read-only).

        Deliberately NOT an abstractmethod: it is a diagnostic capability
        used only by `notion.dashboard`'s readiness check, and making it
        abstract would break every existing NotionTransport double that has
        no reason to implement it. Implementations that can search override
        this; the rest inherit "I cannot search", which the diagnosis
        reports as UNKNOWN rather than treating as an error.
        """
        raise NotImplementedError("this transport cannot search the workspace")

    @abc.abstractmethod
    def create_database(
        self, parent_page_id: str, title: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Create a brand-new Database under `parent_page_id`.

        Used only by notion.dashboard's one-time bootstrap (CEO Decision ④
        Operations Dashboard) — never by ExecutionPlanSync or the Runtime
        pipeline, which only ever read/write rows in databases that already
        exist."""
        raise NotImplementedError


class RealNotionTransport(NotionTransport):
    """Live Notion REST API transport using only the standard library."""

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = NOTION_API_BASE_URL,
        timeout: float = 10.0,
    ):
        self._api_token = api_token
        self._base_url = base_url
        self._timeout = timeout

    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Notion-Version": NOTION_API_VERSION,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise NotionAPIError(
                f"Notion API returned {exc.code}: {exc.reason}", status_code=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise NotionAPIError(f"Notion API request failed: {exc.reason}") from exc

    def retrieve_database(self, database_id: str) -> Mapping[str, Any]:
        return self._request("GET", f"/databases/{database_id}")

    def query_database(
        self, database_id: str, filter_: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._request("POST", f"/databases/{database_id}/query", {"filter": filter_})

    def create_page(
        self, database_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        body = {"parent": {"database_id": database_id}, "properties": properties}
        return self._request("POST", "/pages", body)

    def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def update_database(
        self, database_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._request("PATCH", f"/databases/{database_id}", {"properties": properties})

    def create_database(
        self, parent_page_id: str, title: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        }
        return self._request("POST", "/databases", body)

    def search_pages(self) -> list[Mapping[str, Any]]:
        response = self._request(
            "POST",
            "/search",
            {"filter": {"value": "page", "property": "object"}, "page_size": 100},
        )
        return list(response.get("results") or [])


def _rich_text_value(prop: Mapping[str, Any] | None) -> str | None:
    if not prop:
        return None
    items = prop.get("rich_text") or []
    if not items:
        return None
    return items[0].get("text", {}).get("content")


class InMemoryNotionTransport(NotionTransport):
    """In-memory NotionTransport double for Mock Tests (docs §57-65).

    Not for production use — RealNotionTransport is the live implementation.
    Set `fail_next_call = True` before a call to simulate Mock Test 8
    (§64, Notion API Failure) on whichever call comes next, regardless of
    method; it resets itself after firing once. To target one specific
    method instead (e.g. only `update_database`, for Title Rename Failure
    tests), set `fail_next_method = "update_database"` instead — the two
    flags are independent and either can trigger a failure.
    """

    def __init__(
        self,
        *,
        initial_properties: Mapping[str, Any] | None = None,
        parent: Mapping[str, Any] | None = None,
    ) -> None:
        # Simulated Database `parent`. Defaults to workspace root, which is
        # what the real PROJECTS database actually reports — tests that need
        # a Page-hosted database pass parent={"type": "page_id", ...}.
        self._parent: dict[str, Any] = dict(parent or {"type": "workspace", "workspace": True})
        # Pages `search_pages()` reports, in the shape Notion's /search
        # returns. Empty by default, matching a workspace where nothing has
        # been shared with the integration.
        self.searchable_pages: list[dict[str, Any]] = []
        self._pages: dict[str, dict] = {}
        self._next_id = 1
        self._next_database_id = 1
        # Databases created via create_database(), keyed by the id handed
        # back to the caller: {"title": ..., "properties": ...}. Lets a test
        # assert what notion.dashboard's bootstrap actually created.
        self.created_databases: dict[str, dict[str, Any]] = {}
        self.fail_next_call = False
        self.fail_next_method: str | None = None
        # Simulated Database schema (docs/04 §8 Property definitions), for
        # notion.bootstrap's Mock Tests. A real, freshly created Notion
        # database always starts with exactly one Title property (default
        # name "Name") — callers that want to simulate that pass
        # initial_properties={"Name": {"title": {}}}.
        self._schema_properties: dict[str, Any] = dict(initial_properties or {})

    def _maybe_fail(self, method_name: str) -> None:
        if self.fail_next_method == method_name:
            self.fail_next_method = None
            raise NotionAPIError("simulated Notion API failure", status_code=503)
        if self.fail_next_call:
            self.fail_next_call = False
            raise NotionAPIError("simulated Notion API failure", status_code=503)

    def retrieve_database(self, database_id: str) -> Mapping[str, Any]:
        self._maybe_fail("retrieve_database")
        return {
            "object": "database",
            "id": database_id,
            "parent": dict(self._parent),
            "properties": dict(self._schema_properties),
        }

    def update_database(
        self, database_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Mirrors the real Notion API's two distinct meanings for an entry
        in `properties`, keyed by the property's *current* name:

            {"name": "<new name>"}   -> rename only, definition unchanged
            {"<type>": {...}}        -> create a new property under this key

        (docs/notion API "Update a database": renaming addresses a property
        by its existing name/id and sets a new "name" field, without a type
        key — that shape is otherwise indistinguishable from "give this
        brand-new key a definition" without this check.)
        """
        self._maybe_fail("update_database")
        for key, value in properties.items():
            if set(value.keys()) == {"name"} and key in self._schema_properties:
                definition = self._schema_properties.pop(key)
                self._schema_properties[value["name"]] = definition
            else:
                self._schema_properties[key] = value
        return self.retrieve_database(database_id)

    def query_database(
        self, database_id: str, filter_: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._maybe_fail("query_database")
        target = filter_.get("rich_text", {}).get("equals")
        matches = [
            page
            for page in self._pages.values()
            if _rich_text_value(page["properties"].get("Project ID")) == target
        ]
        return {"results": matches}

    def create_page(
        self, database_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._maybe_fail("create_page")
        page_id = f"mock-page-{self._next_id}"
        self._next_id += 1
        page = {"id": page_id, "properties": dict(properties)}
        self._pages[page_id] = page
        return page

    def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._maybe_fail("update_page")
        if page_id not in self._pages:
            raise NotionAPIError(f"unknown page_id: {page_id}", status_code=404)
        self._pages[page_id]["properties"].update(properties)
        return self._pages[page_id]

    def search_pages(self) -> list[Mapping[str, Any]]:
        self._maybe_fail("search_pages")
        return [dict(page) for page in self.searchable_pages]

    def create_database(
        self, parent_page_id: str, title: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._maybe_fail("create_database")
        database_id = f"mock-db-{self._next_database_id}"
        self._next_database_id += 1
        self.created_databases[database_id] = {
            "parent_page_id": parent_page_id,
            "title": title,
            "properties": dict(properties),
        }
        return {
            "object": "database",
            "id": database_id,
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": dict(properties),
        }
