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
    "blocker_team": "Blocker Team",
    "blocked_since": "막힌 시점",
    "days_blocked": "막힌 일수",
    "first_seen": "처음",
    "days_idle": "정지 일수",
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
}


def label(column: str) -> str:
    """What a person calls `column`, or the key itself if nobody named it."""
    return LABELS.get(column, column)


def labels(columns) -> list[str]:
    """`label()` over a sequence, in order."""
    return [label(c) for c in columns]
