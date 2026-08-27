"""How an ATTENTION line is ranked, for every surface that shows one.

`ops_status.main()` returns `list[str]`. Nothing upstream carries a
severity — not the Event, not the Run Manifest, not the Dashboard Model — so
any ranking is a **reading**, and this module is the one place that reading
is written down.

Three surfaces render the same list, and before C129 each rendered it flat:

    ops_status.py            the terminal ATTENTION block
    dashboard_server.py      the browser page
    controltower/notion_page the bullets on the page the workspace reads

The browser page was fixed first and the classifier lived there. That put it
in an **entrypoint**, which `controltower/notion_page.py` sits below and
cannot import — so the company-facing surface would have kept the flat list
while the local one improved. A rule two renderers need belongs under both.

**P1 is "work is not reaching Company History, or the pipeline is stopped".**
Every phrase in the table is one `ops_status.py` already writes for exactly
that condition; none was invented here. A line that matches nothing is `?`,
never quietly filed as minor — the same posture `PanelStatus.UNSOURCED`
takes for "no source" versus "zero".
"""

from __future__ import annotations

#: `(phrase, severity, why)`. Order matters only in that the first match
#: wins, and the P1 phrases are listed first for that reason.
RULES: tuple[tuple[str, str, str], ...] = (
    ("복구되지 않는다", "P1", "재실행으로 복구되지 않음"),
    ("History에 들어가지 못한", "P1", "Company History에 도달하지 못함"),
    ("Daily History에 없다", "P1", "Company History에 도달하지 못함"),
    ("실행되지 않았다", "P1", "파이프라인이 돌지 않음"),
    ("수집되지 않으며", "P1", "다음 실행에서도 수집되지 않음"),
    ("거부한 Event", "P1", "Event가 거부됨"),
    ("제거할 수 없다", "P1", "모든 실행이 조용히 건너뛰어짐"),
    ("아무것도 오지 않은", "P2", "침묵 — 원인은 아직 알 수 없음"),
    ("전달되지 않았다", "P2", "설정이 전달되지 않음"),
    ("시작조차 되지 못한", "P2", "앞 단계가 중단시킴"),
    ("사람이 확인해야 한다", "P2", "사람 확인 필요"),
)

#: Sort order. `?` sits with P1 rather than at the bottom: an unclassified
#: line must not be able to hide, and the badge says it is unclassified so
#: nobody reads it as a verdict.
RANK = {"P1": 0, "?": 1, "P2": 2}

UNCLASSIFIED = "?"


def severity(line: str) -> tuple[str, str | None]:
    """`(P1 | P2 | ?, the phrase it matched)` for one ATTENTION line."""
    for marker, level, why in RULES:
        if marker in line:
            return level, why
    return UNCLASSIFIED, None


def rank(line: str) -> int:
    """Sort key: P1 and unclassified first, P2 last."""
    return RANK[severity(line)[0]]


def tally(lines) -> dict[str, int]:
    """`{severity: count}` over `lines`, for a one-line summary."""
    counts: dict[str, int] = {}
    for line in lines:
        level = severity(line)[0]
        counts[level] = counts.get(level, 0) + 1
    return counts
