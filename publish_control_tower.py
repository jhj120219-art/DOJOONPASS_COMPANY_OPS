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

Exit codes
----------
    0   the page and all three surfaces around it were written
    1   configuration error -- an argument, or Notion is not configured
    2   (not used here; the Runner spends it for "a run produced nothing")
    3   DEGRADED: the page was written, something around it was not

`3` matters because AGENT.md 6c says to register this beside
`run_company_ops.py` in Task Scheduler, whose only automatic health signal
is "Last Run Result". The three writes after the page are non-fatal by
design; until C116 that also made them **invisible**, because a publish that
lost the `Notes` column, every Project Row body and the Database description
still exited 0. Each of the three names itself on stderr, and the last line
of a degraded run lists them together.

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
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import dashboard_server  # noqa: E402
import businessdate  # noqa: E402
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
from oplog import one_line, redact  # noqa: E402

#: Anything that reached Notion and came back wrong. Distinct from
#: `CONFIG_ERROR_EXIT`, which `cli.py` owns and every tool imports rather
#: than restates -- a second copy of that number is how two tools start
#: disagreeing about what "1" means.
FAILED_EXIT = 1

#: A publish that reached Notion, wrote the Control Tower page, and could not
#: write one of the surfaces around it.
#:
#: **Why this exists (C116).** The three writes after `publish()` are
#: deliberately non-fatal -- losing the `Notes` column must not cost the page
#: itself -- and each of them prints `실패 -- <reason>` to stderr. `main()`
#: then returned **0** for all of them. AGENT.md 6c tells the operator to
#: register this tool "in Task Scheduler beside `run_company_ops.py`", and
#: Task Scheduler's only automatic health signal is the exit code: a
#: scheduled publish whose Notes column, Project Rows and Database
#: description all failed reported `0x0 / success`, every day, forever.
#:
#: That is verbatim the defect `run_company_ops._report_run_summary()` was
#: written for -- "the failures that were handled *gracefully* were exactly
#: the ones that became invisible" -- left standing in the other tool that
#: writes to Notion.
#:
#: 3 rather than a new number, because this project already spends it:
#: `ops_status.py` exit 3 and the Runner's DEGRADED both mean "something
#: needs a person, the thing itself is intact", which is exactly this state.
#: 1 stays reserved for a configuration error, and 2 for a run that produced
#: nothing.
DEGRADED_EXIT = 3

def _safe(value: object) -> str:
    """A remote-authored string, rendered so it can neither forge a report
    line nor carry a credential out of this process.

    **Every sink in this file was missing it (C124), and it is the file that
    talks to Notion.** `init_notion.py` grew exactly this function for
    exactly these values, and its docstring already named the gap:

        the blind spot C31 §7/§8 closed at `run_company_ops.py`'s two sinks
        and **did not look for at this one**

    It did not look for it here either. This tool was written later
    (C105/C106) with the same two sinks and neither guard.

    **Measured, not argued.** A fake transport raising what a broken or
    hostile proxy answers with — `docs/04 §56` is about precisely this, and
    `NotionAPIError` carries up to 400 bytes of the body verbatim — through
    the real `main()`:

        [FAILED] PROJECTS Database에 접근하지 못했다: ... | 502 Bad Gateway
        Upstream request was:
          Authorization: Bearer <the token, all 48 characters of it>
          다음 할 일     : 없음 — 설정 완료

    (The token is described rather than spelled here on purpose:
    `SecretExposureGuardTests.test_no_secret_material_in_any_tracked_file`
    scans every tracked file for that exact shape, and it caught the first
    draft of this docstring. A fixture and a leak look identical to a
    scanner, which is what makes the scanner worth having.)

    One `print()`, five lines on stderr. Both halves fired:

        redact    the API token this process had just sent reached the
                  operator's screen in full — and `tool > log 2>&1` puts it
                  on disk, which is the shape `oplog` exists to stop
        one_line  the forged line is in `init_notion.py`'s own format for
                  "what is left to do", down to the column

    **Why redact here when `ops_status.py` deliberately does not.** That
    decision (pinned by `test_ops_status_still_does_not_redact_its_own_sink`)
    is about messages "built from paths, ids and counts" on the machine that
    holds them — over-redacting a path the operator is about to open costs
    more than it protects. Nothing here is local: every value below came
    back over the network, and a response body is the one string that can
    contain what this process sent.

    The composition is the same one line as `init_notion._safe`,
    `notion_page._safe` and `ops_status._authored`. What has to hold is that
    both halves are applied, not the order they are applied in —
    `RedactionIsSafeInEitherOrderTests` measures that, and `src/agent/agent.py`
    is the caller that relies on it (it redacts here and flattens at the
    sink). The reason for guarding belongs at each sink, and this is this
    sink's.
    """
    return redact(one_line(value))


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
            f"[FAILED] PROJECTS Database에 접근하지 못했다: {_safe(health.error)}",
            file=sys.stderr,
        )
        return FAILED_EXIT

    try:
        parent = client.find_project(PARENT_PROJECT_ID)
    except NotionAPIError as exc:
        print(f"[FAILED] PROJECTS 조회 실패: {_safe(exc)}", file=sys.stderr)
        return FAILED_EXIT
    if parent is None:
        print(
            f"[FAILED] Project ID가 {PARENT_PROJECT_ID!r}인 Row가 PROJECTS에 없다 — "
            "이 페이지를 붙일 곳이 없다. 해당 Row가 생긴 뒤 다시 실행한다.",
            file=sys.stderr,
        )
        return FAILED_EXIT

    payload = dashboard_server.gather(businessdate.now())

    # The address the operator would actually type, resolved by the server
    # module rather than restated here. `dashboard_server.py` owns the port,
    # its one environment variable **and what counts as a port**; a second
    # copy of any of the three is how the page starts advertising an address
    # nothing listens on.
    #
    # That is not hypothetical -- it is what this block did until C116. It
    # read the two names from the server module and then re-implemented the
    # parsing as `raw.isdigit()`, so `COMPANY_OPS_DASHBOARD_PORT=99999`
    # published `http://127.0.0.1:99999/` to the whole workspace while
    # `dashboard_server.py` refused to start on that value at all. The
    # comment above was already true about the constants and already false
    # about the answer.
    #
    # No address at all when the value is not a port. Every use of
    # `dashboard_url` in `notion_page.py` is guarded by `if dashboard_url:`,
    # so omitting it is a supported state -- and an omitted address costs a
    # reader nothing, while a wrong one costs them a trip.
    port = dashboard_server.resolve_port()
    port_error = None
    if port is None:
        raw_port = os.environ.get(dashboard_server.PORT_ENV_VAR, "").strip()
        dashboard_url = None
        port_error = (
            f"{dashboard_server.PORT_ENV_VAR}={raw_port!r} 은(는) 포트 번호가 "
            "아니다 (1-65535) — Dashboard 서버는 이 값으로 뜨지 못하므로 "
            "주소를 페이지에 싣지 않았다"
        )
    else:
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
        print(f"[FAILED] {_safe(exc)}", file=sys.stderr)
        return FAILED_EXIT
    except NotionAPIError as exc:
        print(f"[FAILED] Notion API: {_safe(exc)}", file=sys.stderr)
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
    print(f"  page_id      : {_safe(result.page_id)}")
    if result.url:
        print(f"  url          : {_safe(result.url)}")
    print(f"  블록 기록    : {result.blocks_written}")
    print(f"  블록 보관    : {result.blocks_archived}")
    print(f"  ATTENTION    : {len(payload.get('attention') or [])}건")
    if summary_error is None:
        print(f"  DB 설명 갱신 : {summary_chars}자 (PROJECTS Database 상단)")
    else:
        print(f"  DB 설명      : 실패 — {_safe(summary_error)}", file=sys.stderr)
    if notes_error is not None:
        print(f"  Notes 열     : 실패 — {_safe(notes_error)}", file=sys.stderr)
    else:
        print(f"  Notes 열     : {len(notes_written)}건 갱신 (표 화면에 바로 보인다)")
        if notes_skipped:
            print("  ! 사람이 쓴 Notes가 있어 건드리지 않은 Row: "
                  + ", ".join(_safe(name) for name in notes_skipped),
                  file=sys.stderr)
    if rows_error is not None:
        print(f"  Project Row  : 실패 — {_safe(rows_error)}", file=sys.stderr)
    elif rows_result is not None:
        print(f"  Project Row  : {len(rows_result.written)}건 갱신 "
              f"(블록 보관 {rows_result.blocks_archived})")
        if rows_result.skipped_hand_written:
            print("  ! 사람이 쓴 내용이 있어 건드리지 않은 Row: "
                  + ", ".join(_safe(name) for name in rows_result.skipped_hand_written),
                  file=sys.stderr)
        if rows_result.skipped_unsourced:
            print("    원천 없어 손대지 않은 Row: "
                  + ", ".join(_safe(name) for name in rows_result.skipped_unsourced))
    if port_error is not None:
        print(f"  ! {port_error}", file=sys.stderr)
    for warning in result.warnings:
        print(f"  ! {_safe(warning)}", file=sys.stderr)

    # The exit code, from what actually got written. See `DEGRADED_EXIT`.
    #
    # The two `skipped_*` tuples are deliberately not in here. A row skipped
    # because a person wrote in it is this tool doing the thing it promises
    # (AGENT.md 6c: "사람이 쓴 내용은 이깁니다"), and a row with no source is
    # data this system never produced -- neither is a failure, and putting
    # either in ATTENTION-shaped territory is how a signal stops being read.
    # `result.warnings` is out for the reason its own field docstring gives:
    # "Never a reason to fail".
    #
    # Named in the order they are printed above, so the last stderr line and
    # this one cannot disagree about what went wrong.
    degraded = [
        name
        for name, error in (
            ("DB 설명", summary_error),
            ("Notes 열", notes_error),
            ("Project Row", rows_error),
            (dashboard_server.PORT_ENV_VAR, port_error),
        )
        if error is not None
    ]
    if degraded:
        print(
            "실행 상태: DEGRADED — Control Tower 페이지는 갱신됐지만 다음은 "
            f"쓰이지 못했다: {', '.join(degraded)}",
            file=sys.stderr,
        )
        return DEGRADED_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
