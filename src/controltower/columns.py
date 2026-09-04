"""What each Dashboard Model column is called, for every surface that shows one.

The Model's column names are **field names** — `days_silent`, `of_total`,
`blocker_team`. They are the right names in `to_payload()` and the wrong
names on a screen a person reads, and two surfaces render them:

    dashboard_server.py             the browser table headers
    controltower/notion_page.py     the Notion table headers

Only the first had a translation. Measured on the published page, C134: the
Notion tables carried `display_name`, `blocked_project_count`,
`days_silent`, `evidence_count` as headers, and every panel ended with the
line `이 표에 싣지 않은 열: key, derived_from` — internal identifiers, on the
one surface this company's non-developers read.

The map lived in `dashboard_server.py`, which is an **entrypoint**;
`notion_page.py` sits below it and cannot import it — the same shape that
put `attention.py` here in C129. A name two renderers need belongs under
both.

`label()` falls back to the key rather than raising: a column added upstream
must still render, and a header showing a field name is a smaller failure
than a page that will not build.
"""

from __future__ import annotations


#: `{column key: what a person calls it}`.
LABELS: dict[str, str] = {
    "key": "키",
    "label": "지표",
    "value": "값",
    "derived_from": "무엇에서 나온 숫자인가",
    "evidence_count": "증거",
    "team": "Team",
    "display_name": "이름",
    "events": "Event",
    "projects": "Project",
    "blocked_projects": "막힌 Project",
    "blocked_project_count": "막힌 수",
    "last_seen": "마지막",
    "has_activity": "활동",
    "current_sprint": "Sprint",
    "project_id": "Project",
    "teams": "Team",
    "status": "상태",
    "state": "판정",
    "blocker": "Blocker",
    # RISKS의 OPEN_ISSUE / PENDING_DECISION이 든 사람의 말 (C149).
    # `blocker`와 같은 칸을 쓰지 않는 이유는 머리글이다 — 기다리는 Decision의
    # 요약을 "Blocker"라고 붙여 놓으면 틀린 주장을 하게 된다.
    "detail": "내용",
    "blocker_team": "Blocker Team",
    "blocked_since": "막힌 시점",
    "days_blocked": "막힌 일수",
    "first_seen": "처음",
    # "정지 일수" was a claim rather than a name: the field is days since the
    # last Event, which means "stalled" only for a project that has not ended.
    # A finished project carried `정지 186일` under it.
    "days_idle": "마지막 이후",
    "completed_at": "완료",
    "milestones": "Milestone",
    "sprint": "Sprint",
    "source": "Desktop",
    "expected_team": "기대 Team",
    "days_silent": "무응답 일수",
    "role_mismatches": "role 불일치",
    "mismatched_event_ids": "불일치 Event",
    "kind": "종류",
    "since": "발생",
    "days_open": "경과 일수",
    "event_id": "Event ID",
    "claimed_role": "주장 role",
    "expected_role": "기대 role",
    "kept": "보관",
    "ignored": "무시",
    "at": "시각",
    "event_type": "종류",
    "summary": "요약",
    "milestone": "Milestone",
    "of_total": "전체 중",
    "truncated": "잘림",
    # ROLE_KPI (C149). `role`은 KPI를 읽는 사람이고, `measured`는 이 수를
    # 실제로 계산했는지다 — 계산하지 못한 행은 `reading`이 값 대신
    # `DATA REQUIRED`를 들고 `requires`가 무엇이 있어야 답할 수 있는지
    # 적는다.
    "role": "역할",
    "definition": "정의",
    "measured": "계산됨",
    "reading": "값",
    "chain": "연결",
    "requires": "필요한 원천",
    # COHORT. `size`는 Cohort 전체 인원이고 `dN_base`는 N일이 실제로 지난
    # 인원 — 둘이 다를 수 있다는 것이 이 표의 핵심이라 머리글에서 갈라 놓는다.
    # `dN`은 값이 아니라 '읽는 값'이라 `reading`과 같은 이름을 쓴다.
    "cohort": "Cohort",
    "size": "Project 수",
    "d1": "D+1",
    "d1_retained": "D+1 지속",
    "d1_base": "D+1 분모",
    "d1_settled": "D+1 종료",
    "d7": "D+7",
    "d7_retained": "D+7 지속",
    "d7_base": "D+7 분모",
    "d7_settled": "D+7 종료",
    "d30": "D+30",
    "d30_retained": "D+30 지속",
    "d30_base": "D+30 분모",
    "d30_settled": "D+30 종료",
    # CODE_CHANGES (C149). Event가 아니라 git commit이다.
    "commit": "Commit",
    "author": "작성자",
    "subject": "제목",
    "files": "파일",
}


def label(column: str) -> str:
    """What a person calls `column`, or the key itself if nobody named it."""
    return LABELS.get(column, column)


def labels(columns) -> list[str]:
    """`label()` over a sequence, in order."""
    return [label(c) for c in columns]
