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
    ops_runs_database_id: str | None = None
    """Operations Dashboard OPS_RUNS Database ID (CEO Decision ④).

    Optional on purpose. Notion Dashboard Production 연결 (CEO 승인 A안):
    `record_run()` and its pending-retry queue were fully implemented and
    tested, but no entrypoint ever built a client for them, so the Dashboard
    had never executed against real Notion. Wiring it through configuration —
    rather than hardcoding an id or inventing a second config source — keeps
    the project's existing rule that the Runner never decides for itself
    whether Notion is configured.

    A deployment that does not set NOTION_OPS_RUNS_DATABASE_ID simply runs
    without the Dashboard, exactly as it does today: `run_once()` already
    skips the whole step when `dashboard_client is None`, and `record_run()`
    reports SKIPPED_NOT_CONFIGURED. Nothing breaks for existing installs.
    """

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "NotionConfig":
        source = env if env is not None else os.environ
        token = source.get("NOTION_API_TOKEN")
        database_id = source.get("NOTION_PROJECTS_DATABASE_ID")
        # `not value` already rejected "" — but not "   ", which is the same
        # mistake with an invisible character and was accepted as a real
        # token. A blank value then failed as a 401 at the first API call,
        # became NOTION_RETRY_REQUIRED, and queued every Event forever
        # because nothing caps that retry. Refusing it here turns an
        # invisible typo into one loud message before anything runs.
        #
        # Deliberately narrow: a value that is blank is treated as absent,
        # and nothing else is touched. `"  ntn_x  "` and `'"ntn_x"'` are
        # still passed through byte for byte — trimming or unquoting a value
        # that does contain characters would be second-guessing what the
        # operator set, and is recorded in BACKLOG.md rather than decided
        # here.
        missing = [
            name
            for name, value in (
                ("NOTION_API_TOKEN", token),
                ("NOTION_PROJECTS_DATABASE_ID", database_id),
            )
            if not value or not value.strip()
        ]
        if missing:
            raise NotionConfigError(
                f"missing or blank required environment variable(s): {', '.join(missing)}"
            )

        # Same rule for the optional one, for the same reason: `"" -> unset`
        # was already the behaviour, and a whitespace id is not a different
        # intention — it just produced a Dashboard pointed at a 404.
        ops_runs = source.get("NOTION_OPS_RUNS_DATABASE_ID")
        return cls(
            api_token=token,
            projects_database_id=database_id,
            # Optional: absent means "run without the Operations Dashboard".
            ops_runs_database_id=ops_runs if (ops_runs and ops_runs.strip()) else None,
        )
