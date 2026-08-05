"""Notion integration configuration (docs/04_NOTION_SYNC_SPEC.md §40-42).

Reads NOTION_API_TOKEN and NOTION_PROJECTS_DATABASE_ID from the process
environment. This module does not load .env files itself — see
.env.example for the variables a deployment sets before the real
(non-mock) path is used. Secret values never live in this repository
(docs §40-41).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class NotionConfigError(ValueError):
    """Raised when required Notion configuration is missing."""


@dataclass(frozen=True)
class NotionConfig:
    api_token: str
    projects_database_id: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "NotionConfig":
        source = env if env is not None else os.environ
        token = source.get("NOTION_API_TOKEN")
        database_id = source.get("NOTION_PROJECTS_DATABASE_ID")
        missing = [
            name
            for name, value in (
                ("NOTION_API_TOKEN", token),
                ("NOTION_PROJECTS_DATABASE_ID", database_id),
            )
            if not value
        ]
        if missing:
            raise NotionConfigError(
                f"missing required environment variable(s): {', '.join(missing)}"
            )
        return cls(api_token=token, projects_database_id=database_id)
