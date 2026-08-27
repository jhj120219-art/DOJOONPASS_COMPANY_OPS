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
    9. Operations Dashboard 준비 상태 안내 (읽기 전용 진단, 생성하지 않음)
"""

from __future__ import annotations

import sys
from typing import Sequence
from pathlib import Path

# Same fix as run_company_ops.py (this Sprint's audit): this script's own
# status output can include real Notion API error text, and Windows'
# console defaults to the system's legacy codepage, not UTF-8 — a
# non-ASCII character in that text would raise UnicodeEncodeError on
# stdout's default strict error handling and crash the script.
# `line_buffering=True` (C80): without it Python block-buffers stdout
# whenever it is not a terminal, while stderr stays unbuffered — so
# under `> log 2>&1`, which is how a scheduled run is captured, the two
# streams reorder against each other. The other three entrypoints have
# had this since the Sprint that measured it; this one was outside that
# fix's hand-written roster. Measured with this file's own prologue:
#
#     0: [FAILED] Notion API error: 429 rate limited
#     1: Health Check: PASS (database_id=abc)
#
# The failure above the line it follows.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from notion import (  # noqa: E402
    BootstrapReadiness,
    NotionAPIError,
    NotionClient,
    NotionConfig,
    NotionConfigError,
    RealNotionTransport,
    bootstrap_database,
    diagnose_dashboard_bootstrap,
    format_report,
)

# The same two rules `run_company_ops.py` applies to the strings it prints,
# for strings of the same origin — and applied here for the same reason it
# could not be applied inside `notion/`: that package may import only
# `events` (LayeringInvariantTests), so the guard belongs at the sink, and
# this script is a sink.
from oplog import one_line, redact  # noqa: E402
from cli import CONFIG_ERROR_EXIT, unexpected_arguments  # noqa: E402


def _safe(value: object) -> str:
    """A remote-authored string, rendered so it cannot forge a report line.

    Everything this script prints about the workspace comes back over the
    network: a Notion error body, a `parent.type`, a Page title someone else
    named, the `{exc}` embedded in a diagnosis's `required_action`. Each is
    the same class of string `oplog.append_line()` guards and every one of
    them reached stdout guarded by nothing — the blind spot C31 §7/§8 closed
    at `run_company_ops.py`'s two sinks and did not look for at this one.

    Both halves matter, and both are measurable here:

        one_line  a Page titled "Ops\\n  다음 할 일     : 없음 — 설정 완료"
                  writes that second line into this report, in the exact
                  position and wording of the line an operator reads to
                  decide whether anything is left to do.
        redact    a proxy answering in Notion's place is free to echo the
                  request headers back, and `NotionAPIError` carries up to
                  400 bytes of that body verbatim — docs/04 §56.
    """
    return redact(one_line(value))


def _safe_block(text: str) -> str:
    """A multi-line report, guarded line by line.

    `format_report()` promises exactly one line per Property, and its
    `detail` field carries a Notion API error body on the Title-rename
    failure path. Guarding the block as a whole would collapse the report
    into one line; guarding each line keeps the format and still confines a
    forged line to the row it came from.
    """
    return "\n".join(_safe(line) for line in text.splitlines())


#: A bootstrap that reached the Database and could not bring every Property
#: to spec.
#:
#: **Why this exists (C117).** `main()` printed `FAILED=N` in its summary
#: line and returned **0** for any N. The only Property that can reach
#: `FAILED` without raising is the Title -- `_bootstrap_title_property()`
#: returns it when the rename `"Name" -> "Project"` is refused -- and that is
#: precisely the step this module was written to automate, because
#: `bootstrap.py`'s own docstring records that "this exact manual step was
#: attempted twice by a human operator and did not take effect either time".
#:
#: The consequence is not cosmetic. `notion/properties.py` writes every
#: PROJECTS row as `{"Project": _title(...)}`, so a Database whose Title is
#: still called something else fails **every** later Notion Sync. Exiting 0
#: told the operator the automated step had worked and sent them on to the
#: next line of docs/13 -- repeating, with a green light on top, exactly the
#: history the automation exists to end.
#:
#: 3 rather than a new number, for the reason docs/14 §4 gives: "`3`은
#: `ops_status.py`의 기존 '사람이 확인해야 함'과 같은 뜻이다 -- 두 진입점이
#: 같은 숫자로 같은 말을 한다." The Database exists and is reachable; some of
#: it is not at spec, which is a person's problem and not a crash.
DEGRADED_EXIT = 3


def main(argv: Sequence[str] = ()) -> int:
    refusal = unexpected_arguments(
        argv,
        tool="init_notion.py",
        # The names `NotionConfig.from_env()` actually reads. They were
        # `COMPANY_OPS_NOTION_API_TOKEN` / `COMPANY_OPS_NOTION_PROJECTS_DB`,
        # which nothing has ever read — this file's own §13 docstring names
        # the real two, four lines from here, and the message contradicted
        # it. An operator who followed the message got the same
        # `NotionConfigError` they were trying to fix.
        # `EnvironmentContractTests` now checks the list
        # against the read sites.
        configured_by=(
            "NOTION_API_TOKEN",
            "NOTION_PROJECTS_DATABASE_ID",
            "NOTION_OPS_RUNS_DATABASE_ID",
        ),
    )
    if refusal is not None:
        print(f"[FAILED] {refusal}", file=sys.stderr)
        return CONFIG_ERROR_EXIT

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
        print(f"[FAILED] Health Check: {_safe(health.error)}", file=sys.stderr)
        return 1
    print(f"Health Check: PASS (database_id={_safe(health.database_id)})")

    # 3-7. Database 조회 -> Title Rename(필요 시) -> Spec 비교 -> 없는 Property만 생성
    try:
        result = bootstrap_database(client)
    except NotionAPIError as exc:
        print(f"[FAILED] Notion API error: {_safe(exc)}", file=sys.stderr)
        return 1

    # 8. 결과 Report 출력
    print(_safe_block(format_report(result)))
    print()
    print(
        f"EXISTS={len(result.existing)} "
        f"CREATED={len(result.created)} "
        f"RENAMED={len(result.renamed)} "
        f"SKIPPED={len(result.skipped)} "
        f"FAILED={len(result.failed)}"
    )

    # 9. Operations Dashboard 준비 상태 안내.
    #
    # `diagnose_dashboard_bootstrap()` answers exactly the question an
    # operator has at this moment — "can the OPS_* databases be created, and
    # if not, what do I do?" — and was exported, tested, and never called by
    # anything. A diagnosis nobody runs diagnoses nothing.
    #
    # Read-only and advisory: it inspects the workspace and prints. It does
    # not create a Database, does not choose a parent Page (that is an
    # operator decision), and its outcome never changes this script's exit
    # code — PROJECTS bootstrap succeeding is what this command is for, and
    # the Dashboard is an independent, optional layer.
    print()
    print("Operations Dashboard 준비 상태:")
    # A client bound to OPS_RUNS when the deployment has one, so the
    # diagnosis can look at the database instead of assuming it is absent.
    # `config.ops_runs_database_id` is None when the variable is unset, and
    # the diagnosis then answers exactly as it always has.
    #
    # Measured (C114): on this workspace the variable WAS set, OPS_RUNS did
    # exist with all 22 columns correct, and this line printed "the creation
    # step is yours to perform. Then set NOTION_OPS_RUNS_DATABASE_ID" —
    # an instruction that creates a duplicate database nothing can delete.
    ops_runs_client = (
        NotionClient(transport=transport, database_id=config.ops_runs_database_id)
        if config.ops_runs_database_id
        else None
    )
    diagnosis = diagnose_dashboard_bootstrap(client, ops_runs_client=ops_runs_client)
    print(f"  readiness      : {diagnosis.readiness.value}")
    print(f"  reference 부모 : {_safe(diagnosis.reference_parent_type)}")
    # The Page list answers one question — "where would the database go?" —
    # and ALREADY_CREATED is the answer that it goes nowhere, because it is
    # already somewhere. Printing 5-of-168 candidate Pages under that heading
    # invites exactly the creation this readiness exists to prevent.
    if diagnosis.hostable_pages and diagnosis.readiness is not BootstrapReadiness.ALREADY_CREATED:
        print("  사용 가능한 Page:")
        for page in diagnosis.hostable_pages[:5]:
            print(f"    - {_safe(page.title)} ({_safe(page.page_id)})")
        # The list is truncated at five, and saying so is the difference
        # between "these are the Pages" and "these are five of them". An
        # operator who does not see the Page they shared would otherwise
        # conclude the sharing did not take.
        if len(diagnosis.hostable_pages) > 5:
            print(
                f"    ... 외 {len(diagnosis.hostable_pages) - 5}개 "
                "(Notion /search 1페이지 = 최대 100개까지만 조회한다)"
            )
    if not diagnosis.search_available:
        print("  (이 integration은 Workspace 검색 권한이 없어 Page 목록을 확인하지 못했다)")
    print(f"  다음 할 일     : {_safe(diagnosis.required_action)}")

    # The exit code, from the bootstrap this command exists to perform. See
    # `DEGRADED_EXIT`.
    #
    # `result.failed` only -- not the readiness printed just above, which
    # the comment at step 9 already excludes on purpose, and not `skipped`,
    # which means the Title was already named `Project` and is the good
    # outcome rather than a missed one.
    if result.failed:
        print(
            f"[DEGRADED] Property {len(result.failed)}개가 Spec에 맞춰지지 "
            f"못했다: {', '.join(_safe(name) for name in result.failed)}. "
            "위 Report의 해당 줄에 이유가 있다. "
            "Title이 여기 있으면 이후 Notion Sync는 전부 실패한다 "
            "(모든 Row 쓰기가 'Project' Property를 쓴다).",
            file=sys.stderr,
        )
        return DEGRADED_EXIT

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
