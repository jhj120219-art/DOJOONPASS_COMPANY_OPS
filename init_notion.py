"""Notion Database Auto Bootstrap CLI (docs/04_NOTION_SYNC_SPEC.md §8).

    python init_notion.py

One-time setup script, not part of the Runtime pipeline (Runner never
imports this). Creates whichever of the 11 PROJECTS Database Properties
(§8) are missing in NOTION_PROJECTS_DATABASE_ID and leaves every existing
Property untouched. Safe to run more than once — see
src/notion/bootstrap.py's module docstring for exactly which two Property
types are chosen as Select rather than the literal §8 wording, and why.

실행 순서 (사용자 확정, V1.1에서 Health Check/Title Rename 단계 추가):
    1. 환경변수 확인 (NOTION_API_TOKEN, NOTION_PROJECTS_DATABASE_ID)
    2. Health Check (Authentication + Database 접근)
    3. Database 조회 (현재 Property 목록)
    4. Title Rename (Title Property 이름이 "Project"가 아니면 자동 Rename — V1.1)
    5. Spec와 비교 (나머지 Property)
    6. 없는 Property만 생성
    7. 존재하는 Property는 그대로 유지
    8. 결과 Report 출력
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from notion import (  # noqa: E402
    NotionAPIError,
    NotionClient,
    NotionConfig,
    NotionConfigError,
    RealNotionTransport,
    bootstrap_database,
    format_report,
)


def main() -> int:
    # 1. 환경변수 확인
    try:
        config = NotionConfig.from_env()
    except NotionConfigError as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return 1

    transport = RealNotionTransport(api_token=config.api_token)
    client = NotionClient(transport=transport, database_id=config.projects_database_id)

    # 2. Health Check (Authentication + Database 접근)
    health = client.health_check()
    if not health.ok:
        print(f"[FAILED] Health Check: {health.error}", file=sys.stderr)
        return 1
    print(f"Health Check: PASS (database_id={health.database_id})")

    # 3-7. Database 조회 -> Title Rename(필요 시) -> Spec 비교 -> 없는 Property만 생성
    try:
        result = bootstrap_database(client)
    except NotionAPIError as exc:
        print(f"[FAILED] Notion API error: {exc}", file=sys.stderr)
        return 1

    # 8. 결과 Report 출력
    print(format_report(result))
    print()
    print(
        f"EXISTS={len(result.existing)} "
        f"CREATED={len(result.created)} "
        f"RENAMED={len(result.renamed)} "
        f"SKIPPED={len(result.skipped)} "
        f"FAILED={len(result.failed)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
