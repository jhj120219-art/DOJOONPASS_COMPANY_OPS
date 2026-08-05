"""docs/04_NOTION_SYNC_SPEC.md §8 — Notion Database Auto Bootstrap.

Uses InMemoryNotionTransport exclusively — no real Notion API call.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notion import (  # noqa: E402
    TARGET_PROPERTIES,
    InMemoryNotionTransport,
    NotionAPIError,
    NotionClient,
    PropertyOutcome,
    bootstrap_database,
    format_report,
)


class BootstrapFreshDatabaseTests(unittest.TestCase):
    """A brand-new Notion database always starts with exactly one Title
    property, default-named "Name". V1.1 (CEO/COO policy change, this
    Sprint): the Title property is the single exception where Bootstrap
    auto-renames an existing property — every other Property is still
    only ever created, never modified."""

    def _make_client(self):
        transport = InMemoryNotionTransport(initial_properties={"Name": {"type": "title", "title": {}}})
        return NotionClient(transport=transport, database_id="DB-1"), transport

    def test_all_missing_properties_are_created_and_title_is_renamed(self):
        client, transport = self._make_client()

        result = bootstrap_database(client)

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(len(result.reports), len(TARGET_PROPERTIES))

        # Title Property ("Name") gets auto-renamed to "Project" (V1.1).
        self.assertEqual(by_name["Project"].outcome, PropertyOutcome.RENAMED)
        self.assertIn("Name", by_name["Project"].detail)
        self.assertIn("Project", by_name["Project"].detail)

        # every other target Property was missing -> CREATED
        for name in TARGET_PROPERTIES:
            if name == "Project":
                continue
            self.assertEqual(by_name[name].outcome, PropertyOutcome.CREATED, name)

        # actually landed in the (simulated) Database schema
        schema = client.get_database_schema()
        for name in TARGET_PROPERTIES:
            self.assertIn(name, schema)

        # "Name" no longer exists as a key -- it was renamed, not duplicated
        self.assertNotIn("Name", schema)
        self.assertEqual(schema["Project"]["type"], "title")

    def test_status_and_last_event_type_created_as_select(self):
        client, _ = self._make_client()
        bootstrap_database(client)

        schema = client.get_database_schema()
        self.assertEqual(schema["Status"], {"select": {}})
        self.assertEqual(schema["Last Event Type"], {"select": {}})


class BootstrapProjectAlreadyNamedTests(unittest.TestCase):
    """작업 6, 시나리오 2: 이미 Project인 경우 Skip (아무 작업도 하지 않는다)."""

    def test_title_already_named_project_is_skipped_not_renamed(self):
        transport = InMemoryNotionTransport(initial_properties={"Project": {"type": "title", "title": {}}})
        client = NotionClient(transport=transport, database_id="DB-1")

        result = bootstrap_database(client)

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(by_name["Project"].outcome, PropertyOutcome.SKIPPED)
        self.assertIsNone(by_name["Project"].detail)
        for name in TARGET_PROPERTIES:
            if name == "Project":
                continue
            self.assertEqual(by_name[name].outcome, PropertyOutcome.CREATED, name)

        # no rename call side-effect: schema unchanged for the Title entry
        schema = client.get_database_schema()
        self.assertEqual(schema["Project"], {"type": "title", "title": {}})


class BootstrapTitleRenameFailureTests(unittest.TestCase):
    """작업 6, 시나리오 3: Rename 실패 시 Runtime 중단 없이 Error 반환."""

    def test_rename_api_failure_returns_failed_outcome_without_raising(self):
        transport = InMemoryNotionTransport(initial_properties={"Name": {"type": "title", "title": {}}})
        transport.fail_next_method = "update_database"  # only the rename call fails; retrieve_database succeeds
        client = NotionClient(transport=transport, database_id="DB-1")

        try:
            result = bootstrap_database(client)
        except NotionAPIError:
            self.fail("bootstrap_database() must not let a Title rename NotionAPIError propagate")

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(by_name["Project"].outcome, PropertyOutcome.FAILED)
        self.assertIsNotNone(by_name["Project"].detail)

        # the rest of Bootstrap still ran normally despite the Title failure
        for name in TARGET_PROPERTIES:
            if name == "Project":
                continue
            self.assertEqual(by_name[name].outcome, PropertyOutcome.CREATED, name)

        # Title Property itself was left exactly as it was (rename never took effect)
        schema = client.get_database_schema()
        self.assertEqual(schema["Name"], {"type": "title", "title": {}})
        self.assertNotIn("Project", schema)


class BootstrapExistingPropertiesNeverModifiedTests(unittest.TestCase):
    def test_existing_property_definition_is_left_byte_for_byte_unchanged(self):
        # a hand-made "Owner" Select with real options already configured —
        # bootstrap must never touch it, even though TARGET_PROPERTIES'
        # payload for "Owner" is a bare {"select": {}}.
        existing_owner = {
            "type": "select",
            "select": {
                "options": [
                    {"name": "CTO Backend", "color": "blue"},
                    {"name": "COO", "color": "green"},
                ]
            },
        }
        transport = InMemoryNotionTransport(
            initial_properties={
                "Project": {"type": "title", "title": {}},
                "Owner": existing_owner,
            }
        )
        client = NotionClient(transport=transport, database_id="DB-1")

        result = bootstrap_database(client)

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(by_name["Owner"].outcome, PropertyOutcome.EXISTS)

        schema = client.get_database_schema()
        self.assertEqual(schema["Owner"], existing_owner)


class BootstrapIdempotencyTests(unittest.TestCase):
    """검증: 동일한 초기화를 2회 연속 실행 -> 두 번째 실행에서는 변경사항이 없어야 한다."""

    def test_second_run_creates_nothing(self):
        transport = InMemoryNotionTransport(initial_properties={"Name": {"type": "title", "title": {}}})
        client = NotionClient(transport=transport, database_id="DB-1")

        first = bootstrap_database(client)
        schema_after_first = dict(client.get_database_schema())

        second = bootstrap_database(client)
        schema_after_second = dict(client.get_database_schema())

        first_by_name = {r.name: r for r in first.reports}
        self.assertEqual(first_by_name["Project"].outcome, PropertyOutcome.RENAMED)
        self.assertGreater(len(first.created), 0)

        self.assertEqual(second.created, ())
        self.assertEqual(second.renamed, ())
        # every non-Title target Property is now EXISTS; Project (now
        # correctly named after run 1) is SKIPPED on run 2 -- not renamed again.
        second_by_name = {r.name: r for r in second.reports}
        for name in TARGET_PROPERTIES:
            if name == "Project":
                self.assertEqual(second_by_name[name].outcome, PropertyOutcome.SKIPPED)
            else:
                self.assertEqual(second_by_name[name].outcome, PropertyOutcome.EXISTS, name)

        # schema is byte-for-byte identical across both runs (no drift)
        self.assertEqual(schema_after_first, schema_after_second)


class FormatReportTests(unittest.TestCase):
    def test_format_report_lists_every_property_in_spec_order(self):
        transport = InMemoryNotionTransport(initial_properties={"Project": {"type": "title", "title": {}}})
        client = NotionClient(transport=transport, database_id="DB-1")
        result = bootstrap_database(client)

        text = format_report(result)
        lines = text.splitlines()

        self.assertEqual(len(lines), len(TARGET_PROPERTIES))
        self.assertTrue(lines[0].startswith("Project "))
        self.assertIn("SKIPPED", lines[0])
        self.assertTrue(lines[1].startswith("Project ID "))
        self.assertIn("CREATED", lines[1])


if __name__ == "__main__":
    unittest.main()
