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

from .properties import RICH_TEXT_LIMIT

NOTION_API_BASE_URL = "https://api.notion.com/v1"
NOTION_API_VERSION = "2022-06-28"


class NotionAPIError(Exception):
    """Raised when a Notion API call fails (network error or non-2xx response)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


# Enough of Notion's error body to name the offending property, not enough to
# paste a whole proxy HTML page into a console or a SyncResult.
_MAX_ERROR_DETAIL = 400


def _error_detail(exc: "urllib.error.HTTPError") -> str:
    """Notion's own explanation of a rejected request, as a suffix.

    Best effort by design: reading it must never turn one failure into two,
    so every problem here yields an empty string and the caller still gets
    the status code it already had.

    Never carries a credential — the API token travels in a request header,
    and this reads the *response*.
    """
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return ""
    if not body:
        return ""
    if len(body) > _MAX_ERROR_DETAIL:
        body = body[:_MAX_ERROR_DETAIL] + "..."
    # ASCII separator on purpose. This string reaches `SyncResult.error`,
    # which `run_company_ops.py` prints; that script forces UTF-8 on stdout,
    # but an error message should not depend on every future caller
    # remembering to.
    return f" | {body}"


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
        # Set by `search_pages()`: True when it stopped at
        # `_SEARCH_PAGE_LIMIT` with more results still available. Declared
        # here so a caller can read it before the first search rather than
        # hitting AttributeError.
        self.search_truncated = False

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
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # `exc.reason` is only the status text ("Bad Request"). Notion puts
            # the actionable part — which property it rejected and why — in the
            # response body, and discarding it left an operator with nothing to
            # act on. That matters most for a permanent 4xx, which the retry
            # queue will otherwise re-send on every run forever (BUG-13).
            raise NotionAPIError(
                f"Notion API returned {exc.code}: {exc.reason}{_error_detail(exc)}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise NotionAPIError(f"Notion API request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            # urllib does NOT wrap a read timeout in URLError — it escapes as a
            # bare TimeoutError. Verified against a socket that accepts and
            # never replies. A timeout is the most likely real failure of all,
            # and it was the one this class did not convert, so callers that
            # catch NotionAPIError (ExecutionPlanSync, bootstrap, dashboard)
            # saw an exception type they do not handle.
            raise NotionAPIError(
                f"Notion API request timed out after {self._timeout}s"
            ) from exc
        except OSError as exc:
            # Anything else the socket layer raises. Same reasoning: this
            # class's contract is "network problems become NotionAPIError".
            raise NotionAPIError(f"Notion API request failed: {exc}") from exc

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            # A 200 whose body is not JSON — a captive portal, a proxy error
            # page, a truncated response. Previously escaped as JSONDecodeError
            # or UnicodeDecodeError, neither of which any caller expects.
            raise NotionAPIError(
                f"Notion API returned a body that is not JSON: {exc}"
            ) from exc

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

    # Notion's /search returns at most 100 results per request and reports
    # `has_more` / `next_cursor`. Both were ignored, so a workspace with more
    # than 100 shared pages answered "these are the pages" with a truncated
    # list and no sign of it — and the one question this call exists to
    # answer is "is the Company Ops page shared with this integration?",
    # which a truncated list answers with a confident, wrong "no".
    #
    # Bounded rather than unbounded: this is a diagnostic, it runs inside an
    # operator command, and a paging loop against a remote API is exactly
    # where an unbounded `while` becomes a hang. Ten requests is 1,000 pages,
    # far past any workspace this is used on, and stopping there is reported
    # by `search_truncated` rather than hidden.
    _SEARCH_PAGE_LIMIT = 10

    def search_pages(self) -> list[Mapping[str, Any]]:
        results: list[Mapping[str, Any]] = []
        cursor: str | None = None
        self.search_truncated = False
        for _ in range(self._SEARCH_PAGE_LIMIT):
            body: dict[str, Any] = {
                "filter": {"value": "page", "property": "object"},
                "page_size": 100,
            }
            if cursor is not None:
                body["start_cursor"] = cursor
            response = self._request("POST", "/search", body)
            results.extend(response.get("results") or [])
            if not response.get("has_more"):
                return results
            cursor = response.get("next_cursor")
            if not cursor:
                # `has_more` with no cursor is a response this cannot page
                # through. Reported as truncated rather than looped on.
                break
        self.search_truncated = True
        return results


def _text_value(prop: Mapping[str, Any] | None, kind: str) -> str | None:
    """The plain text of a `rich_text` or `title` property value.

    Notion stores both as the same array of rich-text items under different
    keys, so one reader serves both — which is the point: `query_database()`
    below used to understand only `rich_text`, and OPS_RUNS keys its rows by
    a `title` property.

    Replaces `_rich_text_value()`, which this generalises and which had no
    callers left once `query_database()` stopped hard-coding the type. Two
    readers that differ only in which key they hard-code is the shape that
    drifts.
    """
    if not prop:
        return None
    items = prop.get(kind) or []
    if not items:
        return None
    return items[0].get("text", {}).get("content")


class InMemoryNotionTransport(NotionTransport):
    """In-memory NotionTransport double for Mock Tests (docs §57-65).

    Not for production use — RealNotionTransport is the live implementation.

    It enforces the API's own 2,000-character cap on one `rich_text` /
    `title` item, answering a longer one with the HTTP 400 the live API
    answers with. That is not decoration: a double which accepts what Notion
    refuses is the reason `notion/properties.py` could send four unbounded
    authored fields for the whole life of this project and every test still
    pass (C50). Three separate test modules had grown their own strict
    subclass by then, each a copy of the same eight lines; the rule belongs
    to the double.

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
        # Which database each page was created in. Kept beside `_pages`
        # rather than inside the page dict so that what `create_page()` /
        # `update_page()` hand back stays byte-for-byte what Notion returns.
        #
        # Why it has to exist at all: one transport instance can serve
        # several databases, because that is how production wires it —
        # `run_company_ops.py` builds ONE RealNotionTransport and hands it to
        # two NotionClients, one bound to PROJECTS and one to OPS_RUNS. Real
        # Notion keeps those apart by database id; this double used to ignore
        # the id completely, so every page lived in one undifferentiated pool
        # and `query_database()` would happily return an OPS_RUNS run record
        # as the answer to "find the PROJECTS row for project X".
        #
        # No test does that today (checked), which is exactly why it was
        # worth fixing now: the trap only springs for a test that mirrors the
        # production wiring — the most natural test anyone would write next —
        # and it springs as a silent pass, not a failure.
        self._page_database: dict[str, str] = {}
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
        """Match on whichever property the filter names, in either text type.

        This used to hard-code `"Project ID"` and `rich_text`, which was the
        only filter anything sent. It silently answered every other filter
        with "no rows" — including a lookup by a `title` property, the shape
        OPS_RUNS uses for its `Run ID`. A find-before-create built on that
        would have found nothing, always, and duplicated every row while its
        tests passed.
        """
        self._maybe_fail("query_database")
        property_name = filter_.get("property")
        kind = "title" if "title" in filter_ else "rich_text"
        target = (filter_.get(kind) or {}).get("equals")
        matches = [
            page
            for page_id, page in self._pages.items()
            # Scoped to the database being queried, like the real API.
            if self._page_database.get(page_id) == database_id
            and _text_value(page["properties"].get(property_name), kind) == target
        ]
        return {"results": matches}

    def _refuse_over_long_text(self, properties: Mapping[str, Any] | None) -> None:
        """Notion's cap on one text item, applied the way the API applies it.

        The whole request is refused, not the one property, and the status is
        400 — which is what `sync.PERMANENTLY_REFUSING_STATUS_CODES` reads to
        tell "retry this" from "this will never work".

        The limit is imported rather than restated: it is the same fact
        `properties.fit_properties()` uses on the way out, and two copies of a
        number is how the double and the builder start disagreeing about what
        the API accepts (C28).
        """
        for name, value in (properties or {}).items():
            if not isinstance(value, Mapping):
                continue
            for kind in ("title", "rich_text"):
                for item in value.get(kind) or ():
                    content = (item.get("text") or {}).get("content") or ""
                    if len(content) > RICH_TEXT_LIMIT:
                        raise NotionAPIError(
                            "body failed validation: "
                            f"properties.{name}.{kind}[0].text.content.length "
                            f"should be \u2264 {RICH_TEXT_LIMIT}, instead was "
                            f"{len(content)}.",
                            status_code=400,
                        )

    def create_page(
        self, database_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._maybe_fail("create_page")
        self._refuse_over_long_text(properties)
        page_id = f"mock-page-{self._next_id}"
        self._next_id += 1
        page = {"id": page_id, "properties": dict(properties)}
        self._pages[page_id] = page
        self._page_database[page_id] = database_id
        return page

    def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._maybe_fail("update_page")
        self._refuse_over_long_text(properties)
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
