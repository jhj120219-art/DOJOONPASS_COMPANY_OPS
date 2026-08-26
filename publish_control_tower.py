"""Publish the Control Tower to Notion as a page a person reads.

    python publish_control_tower.py

Takes no command-line arguments, like every other tool here (`src/cli.py`
says why). It reads `NOTION_API_TOKEN` and `NOTION_PROJECTS_DATABASE_ID` —
the same two `init_notion.py` and `run_company_ops.py` read — and nothing
else. There is deliberately no variable for *where* the page goes; see
"Where the page goes" below.

Why this exists
---------------
`dashboard_server.py` puts the Control Tower in a browser on this machine.
That is one seat, on one desktop, behind a loopback address that cannot be
exposed without a deployment decision. The COO seat this project is written
for is a Notion workspace, and until now nothing in this repository could
put a readable page there: `controltower/notion_projection.py` projects onto
*databases* (rows and properties, and five of them, outside docs/14 §1's
contracted two), and `NotionTransport` had no block-level operation at all.

What it writes, and what it will not
------------------------------------
One child page, titled `Control Tower`, rendered from the payload
`dashboard_server.gather()` builds — the same model the browser page
renders. It creates **no database**, adds **no row** to PROJECTS, and writes
no Event. Run it a hundred times and there is one page: `publish()` finds
the page by title and rewrites its body.

Where the page goes, and why it is not configurable
---------------------------------------------------
Notion's API refuses a `workspace` parent for page creation, so a page can
only be created inside a page the integration has already been granted. On
this workspace, measured: the integration can see exactly one top-level
object, the PROJECTS database, and eight pages that are its rows.

So the parent is **discovered**: the PROJECTS row whose `Project ID` is
`COMPANY_OPS`. That is a fact this deployment already has, which is better
than a fifth environment variable holding a pasted id — an id in a file
goes stale silently, and a lookup that fails says which row is missing.

`--page-id` does not exist for `dashboard_server.py`'s `--host` reason: this
page carries `blocker` text and `project_id`s a person typed on another
Desktop, and making its destination a one-word choice is how it ends up
somewhere nobody meant.

Read-only with respect to everything except its own page
--------------------------------------------------------
Nothing here writes an Event, touches `runtime/`, acquires the Runner lock,
or changes a PROJECTS row. It reads the local evidence, reads its own page,
and rewrites its own page. It is safe to run while a Runner is working — the
same guarantee `ops_status.py` makes, for the same reason (it is the same
code underneath).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import dashboard_server  # noqa: E402
from cli import CONFIG_ERROR_EXIT, unexpected_arguments  # noqa: E402
from controltower.notion_page import (  # noqa: E402
    ControlTowerPageError,
    publish,
    publish_database_summary,
    publish_project_notes,
    publish_project_rows,
)
from notion.client import NotionClient  # noqa: E402
from notion.config import NotionConfig, NotionConfigError  # noqa: E402
from notion.transport import NotionAPIError, RealNotionTransport  # noqa: E402

#: Anything that reached Notion and came back wrong. Distinct from
#: `CONFIG_ERROR_EXIT`, which `cli.py` owns and every tool imports rather
#: than restates -- a second copy of that number is how two tools start
#: disagreeing about what "1" means.
FAILED_EXIT = 1

#: The PROJECTS row whose page hosts the Control Tower. See the module
#: docstring for why this is discovered rather than configured.
PARENT_PROJECT_ID = "COMPANY_OPS"

PAGE_TITLE = "Control Tower"


def main(argv=()) -> int:
    refusal = unexpected_arguments(
        argv,
        tool="publish_control_tower.py",
        configured_by=("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID"),
    )
    if refusal is not None:
        print(f"[FAILED] {refusal}", file=sys.stderr)
        return CONFIG_ERROR_EXIT

    try:
        config = NotionConfig.from_env()
    except NotionConfigError as exc:
        # The same sentence `run_company_ops.py` prints, for the same state,
        # and it names the file rather than only the variables: `.env` is
        # deliberately not auto-loaded (`.env.example` header), and an
        # operator looking at a filled `.env` needs to be told that.
        print(
            f"[FAILED] Notion 미설정 — {exc}. .env는 자동으로 읽히지 않는다; "
            "셸에서 export하거나 실행 스크립트가 직접 읽어야 한다.",
            file=sys.stderr,
        )
        return CONFIG_ERROR_EXIT

    transport = RealNotionTransport(api_token=config.api_token)
    client = NotionClient(transport=transport, database_id=config.projects_database_id)

    health = client.health_check()
    if not health.ok:
        print(
            f"[FAILED] PROJECTS Database에 접근하지 못했다: {health.error}",
            file=sys.stderr,
        )
        return FAILED_EXIT

    try:
        parent = client.find_project(PARENT_PROJECT_ID)
    except NotionAPIError as exc:
        print(f"[FAILED] PROJECTS 조회 실패: {exc}", file=sys.stderr)
        return FAILED_EXIT
    if parent is None:
        print(
            f"[FAILED] Project ID가 {PARENT_PROJECT_ID!r}인 Row가 PROJECTS에 없다 — "
            "이 페이지를 붙일 곳이 없다. 해당 Row가 생긴 뒤 다시 실행한다.",
            file=sys.stderr,
        )
        return FAILED_EXIT

    payload = dashboard_server.gather(datetime.now().astimezone())

    # The address the operator would actually type, read from the server
    # module rather than restated here. `dashboard_server.py` owns the port
    # and its one environment variable; a second copy of either is how the
    # page starts advertising an address nothing listens on.
    raw_port = os.environ.get(dashboard_server.PORT_ENV_VAR, "").strip()
    port = raw_port if raw_port.isdigit() else str(dashboard_server.DEFAULT_PORT)
    dashboard_url = f"http://127.0.0.1:{port}/"

    try:
        result = publish(
            transport=transport,
            parent_page_id=parent["id"],
            payload=payload,
            title=PAGE_TITLE,
            dashboard_url=dashboard_url,
        )
    except ControlTowerPageError as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return FAILED_EXIT
    except NotionAPIError as exc:
        print(f"[FAILED] Notion API: {exc}", file=sys.stderr)
        return FAILED_EXIT

    # Each sourced project's evidence, into its own PROJECTS row page.
    #
    # Non-fatal for the same reason the summary is: the row bodies are an
    # addition to the Control Tower, and losing them must not cost the page
    # itself. A row a person has typed in is skipped and named, never
    # overwritten.
    rows_result = None
    rows_error = None
    try:
        rows_result = publish_project_rows(
            transport=transport,
            client=client,
            payload=payload,
            # The page was published first precisely so its address exists
            # to link to here.
            control_tower_url=result.url,
            dashboard_url=dashboard_url,
        )
    except (ControlTowerPageError, NotionAPIError) as exc:
        rows_error = str(exc)

    # The `Notes` column, so the PROJECTS **table view** — the thing Notion
    # shows when you open a database — says which projects need someone.
    # Non-fatal like the two surfaces around it.
    notes_written = notes_skipped = ()
    notes_error = None
    try:
        notes_written, notes_skipped = publish_project_notes(
            client=client, payload=payload
        )
    except NotionAPIError as exc:
        notes_error = str(exc)

    # The database description, after the page and never instead of it.
    #
    # Order matters. The description ends with "the full view is in that
    # child page", so writing it first would point at a page that is still
    # the previous run's. And it is not fatal: a summary line that could not
    # be written is worth a warning, while refusing to publish the whole
    # Control Tower over it would be trading the report for its abstract.
    summary_chars = None
    summary_error = None
    try:
        summary_chars = publish_database_summary(
            transport=transport,
            database_id=config.projects_database_id,
            payload=payload,
            page_hint=PAGE_TITLE,
            dashboard_url=dashboard_url,
        )
    except NotionAPIError as exc:
        summary_error = str(exc)

    verb = "생성" if result.created else "갱신"
    print(f"Control Tower Notion 페이지 {verb} 완료")
    print(f"  page_id      : {result.page_id}")
    if result.url:
        print(f"  url          : {result.url}")
    print(f"  블록 기록    : {result.blocks_written}")
    print(f"  블록 보관    : {result.blocks_archived}")
    print(f"  ATTENTION    : {len(payload.get('attention') or [])}건")
    if summary_error is None:
        print(f"  DB 설명 갱신 : {summary_chars}자 (PROJECTS Database 상단)")
    else:
        print(f"  DB 설명      : 실패 — {summary_error}", file=sys.stderr)
    if notes_error is not None:
        print(f"  Notes 열      : 실패 — {notes_error}", file=sys.stderr)
    else:
        print(f"  Notes 열     : {len(notes_written)}건 갱신 (표 화면에 바로 보인다)")
        if notes_skipped:
            print("  ! 사람이 쓴 Notes가 있어 건드리지 않은 Row: "
                  + ", ".join(notes_skipped), file=sys.stderr)
    if rows_error is not None:
        print(f"  Project Row  : 실패 — {rows_error}", file=sys.stderr)
    elif rows_result is not None:
        print(f"  Project Row  : {len(rows_result.written)}건 갱신 "
              f"(블록 보관 {rows_result.blocks_archived})")
        if rows_result.skipped_hand_written:
            print("  ! 사람이 쓴 내용이 있어 건드리지 않은 Row: "
                  + ", ".join(rows_result.skipped_hand_written), file=sys.stderr)
        if rows_result.skipped_unsourced:
            print("    원천 없어 손대지 않은 Row: "
                  + ", ".join(rows_result.skipped_unsourced))
    for warning in result.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
