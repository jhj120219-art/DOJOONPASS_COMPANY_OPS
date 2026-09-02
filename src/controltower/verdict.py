"""One vocabulary of states, for every surface that shows one.

Three surfaces render this company's health and each had invented its own
words for it:

    ops_status.py            이상 없음
    dashboard_server.py      정상 / 주의 / 조치 필요  (C133)
    controltower/notion_page 주의 N건 / ATTENTION 없음

"상태는 일관된 아이콘/텍스트로 표현한다" cannot be satisfied by a rule each
renderer keeps privately — the words drift the moment one of them is edited,
and a reader moving between the browser page and the Notion page has to
learn two vocabularies for one fact.

So: three states, one word and one shape each, defined here.

**The shape is not decoration.** WCAG 1.4.1 makes colour an addition to a
state rather than the state itself, and Notion's callout colours are the
only styling that surface has — a red callout and an amber one are
indistinguishable to a reader who cannot separate them, and identical in a
screenshot pasted into a chat. The emoji carries the state on its own.

`METRIC_LOWER_IS_BETTER` lives here for the same reason: the browser page
grew it in C133 and the Notion page could not reach it, so the same nine
numbers carried a verdict on one surface and were bare on the other.
"""

from __future__ import annotations

from typing import Any

#: `{tone: (word, browser shape, Notion emoji, Notion callout colour)}`.
#:
#: `bad`/`warn`/`ok` rather than red/amber/green: the tone names what the
#: state *is*, and the colours are one rendering of it. A renderer that
#: cannot do colour still has the word and the shape.
STATES: dict[str, tuple[str, str, str, str]] = {
    "bad": ("조치 필요", "●", "🔴", "red_background"),
    "warn": ("주의", "▲", "🟡", "orange_background"),
    "ok": ("정상", "■", "🟢", "green_background"),
    "info": ("참고", "·", "⚪", "gray_background"),
}

#: The metrics where a smaller number is a better company.
#:
#: **Only three of the nine have a direction, and pretending otherwise would
#: be the worse failure.** `기록된 Event 0` is not bad — a quiet week is a
#: quiet week — and painting it amber teaches an operator to ignore amber.
#: The other six are volume, and both surfaces say so in a word rather than
#: leaving a reader to guess whether a silent number means healthy or
#: unmeasured.
#:
#: Keyed on the Model's `key`, never on the Korean `label`: a wording change
#: upstream would silently stop the verdict applying, which is the
#: "정상을 보고하는 채로" failure this project keeps removing elsewhere.
METRIC_LOWER_IS_BETTER = frozenset(
    {"open_blockers", "teams_silent", "desktop_role_mismatches"}
)

#: The five numbers a reader gets before they scroll, in reading order.
#:
#: Five, not nine: the executive-dashboard rule this was measured against
#: puts three to five headline numbers above the fold, and the full nine
#: stay one toggle away rather than being cut. Ordered by what a person
#: acts on — a Blocker is a decision, an Event count is context.
#:
#: **`events` was one of the five and is not any more (C148).** That comment
#: already called it context, and it is narrower than context: it counts the
#: *files this run read*, which is the definition of machine data. The brief
#: this page is written for is explicit that an executive summary carries
#: Status / Blocker / Decision / Recent Change / Risk, not the reader's own
#: instrumentation.
#:
#: Measured on a simulated month of real company work — five projects, three
#: blockers, one completion, one approved decision:
#:
#:     ③ (above the fold)   열려 있는 Blocker 2 · 움직인 Project 5 ·
#:                          완료된 Milestone 2 · 조용한 Team 0 ·
#:                          **기록된 Event 9**
#:     ⑥ (collapsed)        **완료된 Project 1** · **승인된 Decision 1** ·
#:                          해결된 Issue 1
#:
#: So the month's largest business outcome was one toggle away while "we read
#: nine files" was not. `projects_completed` takes the slot: it is the
#: counterpart to `open_blockers` — what finished against what is stuck.
#:
#: **Nothing is lost by dropping it.** The number stays in the full nine one
#: toggle down, and `데이터 Coverage` reports the same count again as "읽은
#: Event". Nor was it carrying the empty-corpus signal, which is the reason
#: worth checking before touching this list: measured on an empty tree, ③
#: prints its own banner ("아래 0은 '일이 없었다'가 아니라 '셀 Event가
#: 없다'는 뜻이다") and every metric reads 판정 보류 — neither depends on this
#: entry.
#:
#: No direction is claimed for it (`METRIC_LOWER_IS_BETTER` is unchanged).
#: More completions is not automatically better — a quarter can legitimately
#: finish nothing — and inventing a verdict here is the "정상을 보고하는 채로"
#: failure this file exists to avoid.
HEADLINE_METRICS: tuple[tuple[str, str], ...] = (
    ("open_blockers", "🚧"),
    ("projects_active", "📁"),
    ("projects_completed", "✅"),
    ("milestones_completed", "🎯"),
    ("teams_silent", "🔕"),
)


def metric_verdict(key: str, value: Any, *, measured: bool = True) -> tuple[str, str]:
    """`(word, tone)` for one metric — never a colour on its own.

    `measured=False` when the corpus this number was counted over is
    **empty**, and it is checked before anything else. Measured on an empty
    tree: `열려 있는 Blocker  0  정상`. The field is true and the sentence is
    false about the company — there are no Blockers because there are no
    Events, not because anybody cleared them. That is C77's conversion, and
    a verdict is exactly where it must not happen: 정상 is the one word a
    reader takes at face value.

    A value this module cannot compare is not silently called 정상 either: a
    non-number where a count belongs means the Model changed shape, and
    reporting that as healthy is the one answer that cannot be right.
    """
    if not measured:
        # "판정 보류", not "증거 없음": the tile already carries a per-metric
        # `증거 N건` cite beside it, and two identical phrases on one line
        # read as a rendering fault rather than as two facts. This word says
        # what the *verdict* is doing — withholding — which is the thing the
        # cite does not say.
        return "판정 보류", "info"
    if key not in METRIC_LOWER_IS_BETTER:
        return STATES["info"][0], "info"
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "판정 불가", "warn"
    return (STATES["ok"][0], "ok") if value == 0 else (STATES["warn"][0], "warn")


def word(tone: str) -> str:
    """The one word this project uses for `tone`."""
    return STATES.get(tone, STATES["info"])[0]


def shape(tone: str) -> str:
    """The geometric mark for `tone` — for a surface that renders text."""
    return STATES.get(tone, STATES["info"])[1]


def emoji(tone: str) -> str:
    """The emoji for `tone` — for Notion, whose callouts take one."""
    return STATES.get(tone, STATES["info"])[2]


def colour(tone: str) -> str:
    """The Notion callout colour for `tone`."""
    return STATES.get(tone, STATES["info"])[3]
