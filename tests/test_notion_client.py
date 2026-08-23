"""docs/04_NOTION_SYNC_SPEC.md §66 items 1-5: Notion API 연결, Database 접근,
Project 검색/생성/수정 — NotionClient가 InMemoryNotionTransport 위에서 이
동작들을 만족하는지 확인한다. 실제 Notion Workspace 없이 실행된다.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notion import InMemoryNotionTransport, NotionClient, NotionConfig, NotionConfigError  # noqa: E402


class NotionConfigTests(unittest.TestCase):
    def test_from_env_success(self):
        config = NotionConfig.from_env(
            {"NOTION_API_TOKEN": "secret_x", "NOTION_PROJECTS_DATABASE_ID": "db-1"}
        )
        self.assertEqual(config.api_token, "secret_x")
        self.assertEqual(config.projects_database_id, "db-1")

    def test_from_env_missing_token(self):
        with self.assertRaises(NotionConfigError):
            NotionConfig.from_env({"NOTION_PROJECTS_DATABASE_ID": "db-1"})

    def test_from_env_missing_database_id(self):
        with self.assertRaises(NotionConfigError):
            NotionConfig.from_env({"NOTION_API_TOKEN": "secret_x"})

    def test_from_env_missing_both(self):
        with self.assertRaises(NotionConfigError):
            NotionConfig.from_env({})


class NotionClientHealthCheckTests(unittest.TestCase):
    """docs §66 items 1-2: Notion API 연결 성공 + PROJECTS Database 접근 성공."""

    def test_health_check_success(self):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="DB-1")

        result = client.health_check()

        self.assertTrue(result.ok)
        self.assertEqual(result.database_id, "DB-1")
        self.assertIsNone(result.error)

    def test_health_check_failure_does_not_raise(self):
        transport = InMemoryNotionTransport()
        transport.fail_next_call = True
        client = NotionClient(transport=transport, database_id="DB-1")

        result = client.health_check()

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)


class NotionClientProjectLookupTests(unittest.TestCase):
    """docs §66 items 3-5: 신규 생성 / 기존 검색 / 기존 Update."""

    def test_find_project_not_found_returns_none(self):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="DB-1")

        self.assertIsNone(client.find_project("SEARCH_FRONTEND"))

    def test_create_then_find_project(self):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="DB-1")

        client.create_project(
            {"Project ID": {"rich_text": [{"text": {"content": "SEARCH_FRONTEND"}}]}}
        )
        found = client.find_project("SEARCH_FRONTEND")

        self.assertIsNotNone(found)
        self.assertEqual(
            found["properties"]["Project ID"]["rich_text"][0]["text"]["content"],
            "SEARCH_FRONTEND",
        )

    def test_update_project(self):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="DB-1")

        page = client.create_project(
            {
                "Project ID": {"rich_text": [{"text": {"content": "SEARCH_FRONTEND"}}]},
                "Status": {"select": {"name": "IN_PROGRESS"}},
            }
        )
        client.update_project(page["id"], {"Status": {"select": {"name": "BLOCKED"}}})

        found = client.find_project("SEARCH_FRONTEND")
        self.assertEqual(found["properties"]["Status"]["select"]["name"], "BLOCKED")


class InMemoryTransportDatabaseIsolationTests(unittest.TestCase):
    """One transport, several databases — the way production wires it.

    `run_company_ops.py` builds ONE transport and hands it to two
    NotionClients: one bound to the PROJECTS database, one to OPS_RUNS. Real
    Notion keeps those apart by database id.

    `InMemoryNotionTransport` used to ignore `database_id` entirely and keep
    every page in a single pool, so `query_database()` answered from all of
    them. A test that mirrored the production wiring would therefore find an
    OPS_RUNS run record when it asked PROJECTS for a project — and, because
    the shapes overlap, would very likely *pass* while the real API returned
    nothing.

    A test double that is wrong in the direction of "everything is found" is
    the dangerous direction: it hides missing writes rather than inventing
    failures. These tests pin the isolation so it cannot regress quietly.
    """

    def _project_id(self, value):
        return {"Project ID": {"rich_text": [{"text": {"content": value}}]}}

    def test_a_row_in_one_database_is_not_visible_from_another(self):
        transport = InMemoryNotionTransport()
        projects = NotionClient(transport=transport, database_id="projects-db")
        ops_runs = NotionClient(transport=transport, database_id="ops-runs-db")

        ops_runs.create_project(self._project_id("SEARCH_FRONTEND"))

        self.assertIsNone(projects.find_project("SEARCH_FRONTEND"))

    def test_each_database_finds_its_own_row(self):
        transport = InMemoryNotionTransport()
        projects = NotionClient(transport=transport, database_id="projects-db")
        ops_runs = NotionClient(transport=transport, database_id="ops-runs-db")

        projects.create_project(self._project_id("SEARCH_FRONTEND"))
        ops_runs.create_project(self._project_id("SEARCH_FRONTEND"))

        in_projects = projects.find_project("SEARCH_FRONTEND")
        in_ops_runs = ops_runs.find_project("SEARCH_FRONTEND")

        self.assertIsNotNone(in_projects)
        self.assertIsNotNone(in_ops_runs)
        # Same Project ID, genuinely different rows — not one row seen twice.
        self.assertNotEqual(in_projects["id"], in_ops_runs["id"])

    def test_update_still_addresses_a_page_by_id_across_databases(self):
        """Real Notion updates a page by page id, with no database in the
        request — so isolation must apply to the query, not to the update."""
        transport = InMemoryNotionTransport()
        ops_runs = NotionClient(transport=transport, database_id="ops-runs-db")

        page = ops_runs.create_project(self._project_id("SEARCH_FRONTEND"))
        ops_runs.update_project(page["id"], {"Status": {"select": {"name": "BLOCKED"}}})

        found = ops_runs.find_project("SEARCH_FRONTEND")
        self.assertEqual(found["properties"]["Status"]["select"]["name"], "BLOCKED")


class TheDoubleMatchesTheWayNotionMatchesTests(unittest.TestCase):
    """C64. A double that is easier to satisfy than the API is a blind spot.

    `InMemoryNotionTransport.query_database()` answers a `title` / `rich_text`
    `equals` filter, and it used to read the value as
    `items[0]["text"]["content"]`. The live API compares the **concatenated
    plain text** of every item, and Notion stores one item per run of
    identical formatting — an item that is not literal text (a mention, an
    equation) carries no `"text"` key at all.
    `notion/properties._extract_rich_text()` records the measurement that
    established this for the read side.

    The gap is not academic and it is not symmetrical. The double answered
    "no rows" where Notion answers with the row, so a test could only ever
    reproduce the *loud* half of a mismatch — a lookup that misses and
    creates a duplicate. It could not reproduce the quiet half, which is what
    C64 found in `controltower/notion_projection`: the row **is** found
    through the real filter and refreshed, and then retired seconds later by
    a reader that could not read its key back. Stating that defect needed a
    hand-rolled client stand-in because this double could not express it.

    These tests are that expressiveness, held in place.
    """

    KEY = "SEARCH_BACKEND"

    SHAPES = {
        "one plain item, as the API writes it": [
            {"type": "text", "text": {"content": KEY}, "plain_text": KEY},
        ],
        "two formatting runs": [
            {"type": "text", "text": {"content": "SEARCH_"}, "plain_text": "SEARCH_"},
            {"type": "text", "text": {"content": "BACKEND"}, "plain_text": "BACKEND"},
        ],
        "a mention run, which carries no text key": [
            {"type": "mention", "mention": {"type": "page", "page": {"id": "p"}},
             "plain_text": KEY},
        ],
        "text and an equation": [
            {"type": "text", "text": {"content": "SEARCH_"}, "plain_text": "SEARCH_"},
            {"type": "equation", "equation": {"expression": "BACKEND"},
             "plain_text": "BACKEND"},
        ],
        "no plain_text at all (an older double's page)": [
            {"text": {"content": "SEARCH_"}},
            {"text": {"content": "BACKEND"}},
        ],
    }

    def _transport_holding(self, items, kind):
        transport = InMemoryNotionTransport()
        page = transport.create_page("db-1", {"Project ID": {kind: items}})
        return transport, page

    def test_every_shape_notion_would_match_is_matched(self):
        for label, items in self.SHAPES.items():
            with self.subTest(shape=label):
                transport, page = self._transport_holding(items, "rich_text")
                response = transport.query_database(
                    "db-1", {"property": "Project ID", "rich_text": {"equals": self.KEY}}
                )
                self.assertEqual(
                    [row["id"] for row in response["results"]], [page["id"]], label
                )

    def test_the_same_holds_for_a_title_property(self):
        """`find_by_title()` is the lookup the Control Tower projection uses,
        and a title is the same array of items under a different key."""
        for label, items in self.SHAPES.items():
            with self.subTest(shape=label):
                transport, page = self._transport_holding(items, "title")
                response = transport.query_database(
                    "db-1", {"property": "Project ID", "title": {"equals": self.KEY}}
                )
                self.assertEqual([row["id"] for row in response["results"]], [page["id"]])

    def test_a_different_value_still_does_not_match(self):
        """Precision. A reader that concatenated its way to a match on
        everything would pass every test above."""
        for label, items in self.SHAPES.items():
            with self.subTest(shape=label):
                transport, _ = self._transport_holding(items, "rich_text")
                response = transport.query_database(
                    "db-1",
                    {"property": "Project ID", "rich_text": {"equals": "SOMETHING_ELSE"}},
                )
                self.assertEqual(response["results"], [])

    def test_a_value_with_nothing_readable_is_not_the_empty_key(self):
        """`None`, not `""`. The two are different answers to the filter, and
        an empty `project_id` is a real row key with an open decision on it
        (BACKLOG C54 §5) — turning "unreadable" into "the empty key" would
        file a row under it."""
        transport = InMemoryNotionTransport()
        transport.create_page(
            "db-1", {"Project ID": {"rich_text": [{"type": "mention", "mention": {}}]}}
        )

        response = transport.query_database(
            "db-1", {"property": "Project ID", "rich_text": {"equals": ""}}
        )

        self.assertEqual(response["results"], [])

    def test_the_sync_layer_sees_one_row_rather_than_two(self):
        """The consequence at the layer that cares. `find_project()` missing a
        row it wrote is a duplicated row on every run — the failure
        `find_or_create_by_title()` exists to prevent, arriving through the
        double instead of through Notion.
        """
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="db-1")
        transport.create_page(
            "db-1",
            {
                "Project ID": {
                    "rich_text": [
                        {"type": "mention", "mention": {}, "plain_text": self.KEY}
                    ]
                }
            },
        )

        self.assertIsNotNone(client.find_project(self.KEY))


class NotionClientTitleLookupTests(unittest.TestCase):
    """`find_by_title()` / `find_or_create_by_title()`.

    `find_project()` could not serve OPS_RUNS: it filters on a `rich_text`
    property, and OPS_RUNS keys its rows by `Run ID`, which is the database's
    *title* property — a different Notion type with a different filter key.
    Without a title-aware lookup there was no way to ask "has this run
    already been recorded?", which is why `record_run()` had no
    find-before-create step and could duplicate a row.
    """

    def setUp(self):
        self.transport = InMemoryNotionTransport()
        self.client = NotionClient(transport=self.transport, database_id="ops-runs-db")

    def _run_row(self, run_id, overall="OK"):
        return {
            "Run ID": {"title": [{"type": "text", "text": {"content": run_id}}]},
            "Overall": {"select": {"name": overall}},
        }

    def test_a_missing_title_is_none_rather_than_an_error(self):
        self.assertIsNone(self.client.find_by_title(property_name="Run ID", value="nope"))

    def test_an_existing_row_is_found_by_its_title(self):
        created = self.client.create_project(self._run_row("run-1"))

        found = self.client.find_by_title(property_name="Run ID", value="run-1")

        self.assertIsNotNone(found)
        self.assertEqual(found["id"], created["id"])

    def test_find_or_create_creates_only_when_absent(self):
        first = self.client.find_or_create_by_title(
            property_name="Run ID", value="run-1", properties=self._run_row("run-1")
        )
        second = self.client.find_or_create_by_title(
            property_name="Run ID", value="run-1", properties=self._run_row("run-1")
        )

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.transport._pages), 1)

    def test_find_or_create_does_not_overwrite_the_row_it_finds(self):
        """It returns the existing row untouched. Updating it would be a
        different operation with a different risk — this guard exists to
        avoid a second row, not to re-state the first one."""
        self.client.create_project(self._run_row("run-1", overall="FAIL"))

        found = self.client.find_or_create_by_title(
            property_name="Run ID", value="run-1", properties=self._run_row("run-1", overall="OK")
        )

        self.assertEqual(found["properties"]["Overall"]["select"]["name"], "FAIL")

    def test_the_lookup_is_scoped_to_this_client_s_database(self):
        """One transport serves several databases in production
        (`run_company_ops.py` builds one and binds two clients to it), so a
        run recorded in OPS_RUNS must not answer a lookup in PROJECTS."""
        other = NotionClient(transport=self.transport, database_id="projects-db")
        self.client.create_project(self._run_row("run-1"))

        self.assertIsNone(other.find_by_title(property_name="Run ID", value="run-1"))

    def test_a_rich_text_property_of_the_same_name_is_not_mistaken_for_a_title(self):
        """The filter names both the property and its type. A row carrying
        `Run ID` as rich_text — the shape OPS_NOTION_SYNC uses — must not
        satisfy a title lookup, or the guard would find the wrong row and
        skip a create that should have happened."""
        self.client.create_project(
            {"Run ID": {"rich_text": [{"type": "text", "text": {"content": "run-1"}}]}}
        )

        self.assertIsNone(self.client.find_by_title(property_name="Run ID", value="run-1"))

    def test_find_project_still_works_for_rich_text(self):
        """The generalised transport filter must not break the original
        caller it was hard-coded for."""
        self.client.create_project(
            {"Project ID": {"rich_text": [{"type": "text", "text": {"content": "PRJ-1"}}]}}
        )

        self.assertIsNotNone(self.client.find_project("PRJ-1"))
        self.assertIsNone(self.client.find_project("PRJ-2"))


if __name__ == "__main__":
    unittest.main()
