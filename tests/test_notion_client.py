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


if __name__ == "__main__":
    unittest.main()
