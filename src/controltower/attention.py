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
    # C133. One `event_id`, two files with different contents: the Control
    # Tower counts one and does **not** count the other, and which one it
    # counts is decided by filename order. That is a file whose work is
    # absent from every rollup on the screen -- "work is not reaching
    # Company History" by the letter of this module's own P1 definition.
    ("같은 event_id를 두고 내용이 다른", "P1", "같은 id의 파일 둘 — 한쪽이 세어지지 않음"),
    # C133. An open Blocker is the most actionable line this list carries
    # and it was **unclassified** -- measured on a probe tree, it rendered
    # with a `?` badge and "이 화면이 분류하지 못한 줄".
    #
    # P2 rather than P1, and the distinction is worth stating: P1 here
    # means the *pipeline* is broken, and a blocked Project is a pipeline
    # working perfectly on work that a person has stopped. Promoting it
    # would redefine P1 for the ten lines that already use it. The
    # Dashboard gives open Blockers their own never-folded section
    # instead, which is where that emphasis belongs.
    ("막혀 있는 Project", "P2", "업무가 멈춰 있음 — 사람이 풀어야 한다"),
    ("Desktop과 role이 어긋난", "P2", "Desktop↔role 불일치 — 집계가 갈라짐"),
    # The reassuring twin of `아무것도 오지 않은`: the Desktop was off and
    # has just sent its backlog. Left unclassified it sorted to the top
    # beside the genuine faults.
    ("밀린 분을 보낸 것으로", "P2", "밀린 보고가 도착함 — Agent는 살아 있다"),
    ("아무것도 오지 않은", "P2", "침묵 — 원인은 아직 알 수 없음"),
    ("전달되지 않았다", "P2", "설정이 전달되지 않음"),
    # C133, found by reading the **published Notion page** rather than the
    # code: this rendered as `[?] (분류 불가)` on the surface the company
    # reads, directly under a sentence that ends "run_company_ops.py를 한 번
    # 실행해 실제로 도달하는지 확인해야 한다". The line knew its own remedy
    # and the classifier did not.
    #
    # P2: nothing is broken. What is true is that the two zero counts above
    # it are not evidence of health, which is a thing to verify rather than
    # a thing to repair.
    ("Notion 단계를 시도한 실행이", "P2", "Notion 도달 여부가 아직 확인되지 않음"),
    ("시작조차 되지 못한", "P2", "앞 단계가 중단시킴"),
    # C133. The review queue was **unclassified** until now — measured:
    # `사람 검토를 기다리는 History Candidate 3건` matched no phrase and came
    # out `?`, which sorts with P1 and renders as "이 화면이 분류하지 못한
    # 줄". It is neither. It is the one condition on this list that is not a
    # fault at all: `docs/05 §24` says BLOCKED/COMPLETED/CANCELLED are not
    # decided by rule, so those Candidates are **work waiting for a person**,
    # and P2 ("사람 확인 필요") is exactly that tier. Listed before the
    # broader `사람이 확인해야 한다` because that phrase is appended to
    # several fault lines too and would otherwise claim this one first.
    ("검토를 기다리", "P2", "사람 검토 대기"),
    ("사람이 확인해야 한다", "P2", "사람 확인 필요"),
)

#: Sort order. `?` sits with P1 rather than at the bottom: an unclassified
#: line must not be able to hide, and the badge says it is unclassified so
#: nobody reads it as a verdict.
RANK = {"P1": 0, "?": 1, "P2": 2}

UNCLASSIFIED = "?"


#: What a person does about a line, keyed on the phrase that classified it.
#:
#: A dashboard that says only *what is wrong* leaves the reader to work out
#: *what to do*, and the portfolio-reporting rule this project was measured
#: against is explicit about it: every red or amber entry needs one line
#: saying what happens next. Before this, none of the eleven did.
#:
#: **Keyed on `RULES`'s own phrases, deliberately.** A second table matched
#: independently would drift from the severity table it sits beside, and
#: then one line could carry a P1 badge with a P2's remedy. One match, two
#: answers: `severity()` and `next_action()` walk the same tuple.
#:
#: Each entry names an entrypoint that exists in this repository, or names
#: no command at all. A dashboard that tells an operator to run something
#: that is not there is worse than one that stays quiet — so where the
#: remedy is a judgement rather than a command, the text says to read and
#: judge, and stops.
ACTIONS: dict[str, str] = {
    "복구되지 않는다": (
        "재실행으로는 낫지 않는다 — 줄이 지목한 파일·날짜를 직접 열어 확인한다."
    ),
    "History에 들어가지 못한": (
        "그 날짜의 Event가 더 수집되면 함께 들어간다. 지난 날짜라면 "
        "`runtime/history_candidates/keep/` 의 해당 건을 사람이 처리한다."
    ),
    "Daily History에 없다": (
        "그 날짜의 Event가 더 수집되면 함께 들어간다. 지난 날짜라면 "
        "`runtime/history_candidates/keep/` 의 해당 건을 사람이 처리한다."
    ),
    "실행되지 않았다": (
        "`python run_company_ops.py` 로 한 번 돌린다. 계속 반복되면 "
        "예약 작업(Windows 작업 스케줄러)이 꺼져 있는지 확인한다."
    ),
    "수집되지 않으며": (
        "줄이 지목한 파일을 열어 형식을 고치거나, 필요 없으면 지운다."
    ),
    "거부한 Event": (
        "거부된 파일의 사유를 읽고 스키마에 맞게 고친 뒤 다시 넣는다 "
        "(docs/02)."
    ),
    "제거할 수 없다": (
        "그 Lock을 잡고 있는 프로세스가 정말 도는지 확인한 뒤, 죽었으면 "
        "Lock 파일을 지운다."
    ),
    "아무것도 오지 않은": (
        "그 Desktop에서 Agent가 도는지 확인한다 (`python run_agent.py`). "
        "꺼져 있었을 뿐일 수도 있다 — 이 줄만으로는 고장인지 알 수 없다."
    ),
    "Notion 단계를 시도한 실행이": (
        "`python run_company_ops.py` 를 한 번 돌려 Notion에 실제로 닿는지 "
        "확인한다 — 토큰이나 Database 공유 설정이 틀렸다면 그때 드러난다."
    ),
    "전달되지 않았다": (
        "`.env` 의 값을 이 프로세스의 환경변수로 넘긴 뒤 다시 실행한다."
    ),
    "시작조차 되지 못한": (
        "앞 단계의 실패를 먼저 고친다 — 이 단계는 그 결과일 뿐이다."
    ),
    "같은 event_id를 두고 내용이 다른": (
        "두 파일을 열어 보고, 그 Event가 아닌 쪽을 "
        "`runtime/events/processed/` 에서 치운다."
    ),
    "막혀 있는 Project": (
        "Blocker 문장이 지목한 것을 사람이 푸는 수밖에 없다 — "
        "파이프라인은 이것을 스스로 지우지 않는다. 그 Team이 RESUMED / "
        "ISSUE_RESOLVED / COMPLETED를 보고해야 닫힌다."
    ),
    "Desktop과 role이 어긋난": (
        "건별로 보기 전에 그 Desktop의 role 설정을 먼저 확인한다 — "
        "한 대가 잘못 설정되면 그 Desktop의 모든 Event가 여기 들어온다."
    ),
    "밀린 분을 보낸 것으로": (
        "지금 할 일은 없다. Agent는 살아 있고 밀렸던 보고가 막 도착했다는 "
        "뜻이다 — 다음 실행에서 이 줄이 사라지는지만 확인한다."
    ),
    "검토를 기다리": (
        "`python src/review_cli.py` 로 내용을 읽고 KEEP/IGNORE를 정한다 "
        "(AGENT.md §5b). 고장이 아니라 사람이 할 일이다."
    ),
    "사람이 확인해야 한다": (
        "줄 전문을 읽고 사람이 판단한다 — 자동으로 처리되지 않는다."
    ),
}

#: The line asks for a **judgement**, not a repair.
#:
#: Narrow on purpose. `사람이 확인해야 한다` is *appended* to several broken
#: -state lines in `ops_status.py` (a rejected Event, a damaged state file),
#: so it does not separate the two — measured. The review queue does: those
#: Candidates are not a fault, they are work waiting for a person, and
#: `docs/05 §24` is why they are not decided automatically.
DECIDE_MARKERS: tuple[str, ...] = ("검토를 기다리",)

#: What this screen calls each group.
KIND_LABELS = {
    "FIX": "조치 — 사람이 손대야 한다",
    "DECIDE": "판단 — 사람이 정해야 한다",
}


def next_action(line: str) -> str | None:
    """One line saying what a person does about `line`, or `None`.

    `None` rather than a guess: a line `severity()` could not classify is
    one this module has no reading of, and inventing a remedy for it would
    be the same failure `UNCLASSIFIED` exists to prevent one field over.
    """
    for marker, _level, _why in RULES:
        if marker in line:
            return ACTIONS.get(marker)
    return None


def kind(line: str) -> str:
    """`FIX` or `DECIDE` — whether the line asks for a repair or a decision."""
    return "DECIDE" if any(m in line for m in DECIDE_MARKERS) else "FIX"


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
