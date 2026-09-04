"""The Control Tower as a Notion **page** a person reads.

What was missing
----------------
`controltower/notion_projection.py` projects the Dashboard Model onto Notion
*databases* — rows and properties. That is the right shape for a machine and
the wrong shape for the question this module answers, which is "open Notion
and see how the company is doing". It also cannot run here: its five
databases are outside docs/14 §1's contracted `PROJECTS / OPS_RUNS`, and
measured on this workspace the integration can see exactly **one** top-level
object — the PROJECTS database itself.

So nothing in this repository could put a readable page in Notion. Measured
on HEAD before this module existed: `NotionTransport` had no block-level
operation at all — every method moved a database row's *properties*, and a
page body is reached through different endpoints entirely.

One model, two sinks
--------------------
Everything here is rendered from the payload `dashboard_server.gather()`
builds — the same dict the browser page renders, not a second derivation of
the rollup. That is the point rather than a convenience: a Notion page and a
browser page built from two derivations can disagree about the company, and
then a person has two Control Towers and no way to tell which is lying.
Reading `to_payload()` also inherits its security property, the one
`notion_projection.py` states: every authored value has already been through
`redact(one_line(...))` on the way out, so this side never handles an
un-redacted string.

Where it goes, and why there
----------------------------
Notion's API refuses a `workspace` parent for page creation, so the only
pages this can create are children of a page the integration already has.
The parent is therefore **discovered** rather than configured: the PROJECTS
row whose `Project ID` is `COMPANY_OPS`. That needs no new environment
variable, no new database, and no id pasted into a file — and when the row is
absent the publisher says so instead of guessing.

Idempotent by construction
--------------------------
`publish()` finds the child page by title and rewrites its body; it creates
the page only when no such child exists. Running it a hundred times leaves
one page. Notion has no "replace the body" call, so the old blocks are
archived one at a time — archived, not destroyed, and recoverable from the
page's own history, which is what makes a re-render an update rather than a
loss.

A number is not a verdict
-------------------------
The rule `controltower/dashboard.py` was written for governs this page too,
and it is the reason for `_absence_note()`: `UNSOURCED` means "this system
has no source for that" and empty means "nothing happened", they render
identically anywhere the distinction is dropped, and they mean opposite
things. A period filter that excludes everything is a third case again. Each
one gets its own sentence here; none of them is allowed to render as `0`.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from oplog import one_line, redact

from . import verdict as _verdict
from .attention import DOMAIN_LABELS as _DOMAIN_LABELS
from .attention import SYSTEM as _SYSTEM
from .attention import COMPANY as _COMPANY
from .attention import domain as _attention_domain
from .attention import next_action as _attention_action
from .attention import rank as _attention_rank
from .attention import severity as _attention_severity
from .attention import tally as _attention_tally
from .columns import labels as _column_labels
from .dashboard import is_settled as _is_settled

# ------------------------------------------------------------------ limits

#: The idle threshold `ops_status.py` uses for a silent Desktop.
#:
#: Restated here rather than imported, and that is a compromise worth
#: naming: `ops_status.py` is an entrypoint, and a library module importing
#: one would invert the layering `LayeringInvariantTests` holds. The value is
#: pinned against its source by
#: `test_the_quiet_threshold_matches_ops_status` so the two cannot drift.
SILENT_AFTER_DAYS = 3


def _metric_cite(evidence: int, value: object) -> str:
    """The evidence clause beside one KPI number, in the Notion callout.

    Byte-identical wording to `dashboard_server._kpi_cite()`, which carries
    the reasoning: a non-zero number with no per-item refs is *derived*, not
    unsourced, and saying `증거 파일 없음` about it was false on two of the
    nine metrics. The two surfaces are held to the same three states by
    `test_controltower_notion_page.py`, because a COO reading Notion and an
    operator reading the browser must not be told different things about the
    same number (C110's lesson, and C133/C134's).
    """
    if evidence:
        return f"   ·  증거 {evidence}건"
    if value:
        return "   ·  파일을 세지 않는 파생값 — 아래 근거 참조"
    return "   ·  증거 파일 없음"


#: Notion refuses more than this many children in one append.
MAX_CHILDREN_PER_APPEND = 100

#: Notion's cap on one rich-text item. `notion.properties` enforces the same
#: number on the property side; imported rather than restated would be
#: better, but that module's constant is about *properties* and this is about
#: block content — the same limit reached by a different endpoint.
RICH_TEXT_LIMIT = 2000

#: How many rows of a long panel reach the page.
#:
#: Not a display preference. Notion charges an append per 100 blocks and a
#: table row is a block, so an unbounded ACTIVITY panel would turn one
#: publish into dozens of API calls and a page nobody scrolls. The count that
#: was truncated is always stated — a table that silently shows the first
#: twenty of two hundred is the "reported success while doing less" shape.
MAX_TABLE_ROWS = 20

#: The panels this page renders, in reading order, with the columns worth a
#: person's attention. The Dashboard Model carries more columns than a page
#: can show; picking here rather than rendering all of them keeps the table
#: readable, and `_panel_table()` states the ones it left out.
PANEL_LAYOUT: tuple[tuple[str, tuple[str, ...]], ...] = (
    # `detail` joined in C149, and leaving it out was a measured mistake
    # rather than a hypothetical one: this layout selects five of thirteen
    # columns, and the three risk kinds added in the same change put their
    # entire content in `detail`. A `PENDING_DECISION` row rendered without
    # it says "GROWTH · CMO · 7일" and never says **what** decision — on the
    # one surface this company's non-developers read, and for the row a CEO
    # is most likely to act on. `_panel_table()` does announce the dropped
    # columns in a line underneath, which turns an invisible loss into a
    # visible one and is not the same as showing the value.
    ("RISKS", ("kind", "project_id", "team", "blocker", "detail", "days_open")),
    ("PROJECTS", ("project_id", "state", "blocker", "days_blocked", "days_idle", "last_seen")),
    ("TEAMS", ("display_name", "events", "projects", "blocked_project_count", "last_seen")),
    ("DESKTOPS", ("source", "display_name", "events", "last_seen", "days_silent")),
    ("METRICS", ("label", "value", "evidence_count")),
    # `requires` is on the layout and it is the widest column here on
    # purpose: for twenty-two of the twenty-nine KPIs it is the entire
    # content of the row. A KPI table that showed only 지표 / 값 would print
    # `DATA REQUIRED` twenty-two times and tell a reader nothing about what
    # would have to exist to change that (C149).
    ("ROLE_KPI", ("label", "reading", "requires")),
    # `dN_base` beside every `dN`, and the pairing is the point rather than a
    # preference: the reading alone ("D+30 33.3%") is the one number on this
    # page a person will quote in a meeting, and it means something different
    # over three matured Projects than over eleven. The `dN_retained` columns
    # are the ones left out — the pair `dN` / `dN_base` already says
    # "33.3% of 3", and `_panel_table()` names the dropped columns underneath.
    (
        "COHORT",
        (
            "cohort", "size",
            "d1", "d1_base", "d1_settled",
            "d7", "d7_base", "d7_settled",
            "d30", "d30_base", "d30_settled",
        ),
    ),
    ("CODE_CHANGES", ("at", "author", "subject", "files")),
    ("ACTIVITY", ("at", "source", "team", "project_id", "event_type", "summary")),
    ("COMPLETIONS", ("at", "source", "team", "project_id", "event_type", "summary")),
)


class ControlTowerPageError(RuntimeError):
    """Raised when the page cannot be published for a reason a person fixes."""


@dataclass(frozen=True)
class PublishResult:
    """What one `publish()` did, in terms a caller can report without
    re-reading Notion."""

    page_id: str
    url: str | None
    created: bool
    blocks_written: int
    blocks_archived: int
    title: str
    #: Non-fatal facts the caller should surface — a truncated listing, a
    #: panel that could not be rendered. Never a reason to fail: a Control
    #: Tower that refuses to publish because one panel is odd is worse than
    #: one that publishes and says which panel was odd.
    warnings: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------- the redaction boundary


def _safe(text: object) -> str:
    """An ATTENTION message, made safe for a sink that leaves this machine.

    Everything else this module renders arrives through `to_payload()`, which
    applies `redact(one_line(...))` to every authored field. **The ATTENTION
    list does not.** It is built by `ops_status.py` and handed to
    `dashboard_server.gather()` beside the model, so it never passes that
    boundary.

    `ops_status.py` does not redact it, and that is deliberate rather than an
    oversight — `_authored()`'s docstring records the reasoning: the sink
    applies `one_line()` to every message but not `redact()`, because "almost
    every message is built from paths, ids and counts, and over-redacting a
    path an operator has to go and open costs more than it protects."

    That trade is right for a terminal on the operator's own machine. It is
    the wrong trade here, and nothing about it was re-weighed when this
    module gave those same strings a second sink: **the same sentence that
    stays on one desktop in a terminal lands in a shared Notion workspace
    from here.** Same text, different blast radius.

    Measured (C109) rather than argued. Of 67 `attention.append/extend` sites
    in `ops_status.py`, **zero** call `redact()`; 21 interpolate authored
    fields, and some — the KEEP-candidate line at `ops_status.py:2539` among
    them — interpolate a raw `event_id`. An `event_id` is set by whichever
    Desktop wrote the Event and `validate_event()` only type-checks it, so a
    secret-shaped one reaches Notion verbatim. `_authored()`'s own docstring
    records that this exact shape has already been found once here.

    So the boundary redacts, rather than asking sixty-seven call sites to
    remember that their text now leaves the building. That is the argument
    `transport._error_detail()` already makes for its own output — "an error
    message should not depend on every future caller remembering to".

    Over-redaction is the correct direction at *this* sink specifically: the
    Notion page is a summary, and the operator who needs the unredacted path
    has `ops_status.py` on the machine that holds it.
    """
    return redact(one_line(text))


# ------------------------------------------------------------ block makers


def _text(
    content: str, *, bold: bool = False, code: bool = False, link: str | None = None
) -> dict[str, Any]:
    """One rich-text run, optionally a hyperlink.

    Measured against the live API before it was used: a `text.link.url`
    round-trips and comes back as `href` on read. Worth checking rather than
    assuming, because a link that silently degrades to plain text is a
    pointer a reader cannot follow and cannot tell is broken.
    """
    trimmed = content if len(content) <= RICH_TEXT_LIMIT else content[: RICH_TEXT_LIMIT - 1] + "…"
    text: dict[str, Any] = {"content": trimmed}
    if link:
        text["link"] = {"url": link}
    return {
        "type": "text",
        "text": text,
        "annotations": {"bold": bold, "code": code},
    }


#: `**bold**` and `` `code` `` — this project's convention, in one regex.
#:
#: Non-greedy and anchored on the markers themselves, so an unmatched
#: marker is left alone as ordinary text rather than swallowing the rest of
#: the line. `ops_status.py` writes balanced pairs; a truncated line may not,
#: and the failure mode of a greedy match there is an entire alert rendered
#: in bold.
_MARKUP = re.compile(r"\*\*(.+?)\*\*|`(.+?)`", re.S)


def _rich(content: str) -> list[dict[str, Any]]:
    """`content` as Notion rich-text runs, honouring the markup in it.

    Returns one run for plain stretches and one per emphasised span. The
    total is what `RICH_TEXT_LIMIT` bounds per run — splitting cannot make a
    single run longer than it was.

    Nothing here can introduce emphasis the author did not write: the only
    inputs that become annotations are the spans between their own markers.

    **Which text gets this, and which deliberately does not.** It is applied
    to the strings written in this project's convention — `ops_status.py`'s
    ATTENTION lines, its NOTION block, and the Model's panel notes. It is
    **not** applied to `blocker` or `summary`, which a person typed into a
    Signal: an asterisk there is an asterisk they meant, and stripping it
    would delete their text rather than render it.
    """
    runs: list[dict[str, Any]] = []
    cursor = 0
    for match in _MARKUP.finditer(content):
        if match.start() > cursor:
            runs.append(_text(content[cursor : match.start()]))
        bold, code = match.group(1), match.group(2)
        if bold is not None:
            runs.append(_text(bold, bold=True))
        else:
            runs.append(_text(code, code=True))
        cursor = match.end()
    if cursor < len(content):
        runs.append(_text(content[cursor:]))
    return runs or [_text(content)]


def _heading(content: str, level: int = 2) -> dict[str, Any]:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": [_text(content)]}}


def _paragraph(content: str = "", *, link: str | None = None) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [_text(content, link=link)] if content else []},
    }


def _bullet(content: str, *, markup: bool = False) -> dict[str, Any]:
    """One bullet. `markup=True` for text an author wrote in the project's
    `**bold**` / `` `code` `` convention — ATTENTION lines and panel notes."""
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": _rich(content) if markup else [_text(content)]
        },
    }


def _callout(
    content: str, emoji: str, colour: str, *, markup: bool = False
) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich(content) if markup else [_text(content)],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": colour,
        },
    }


def _toggle_heading(content: str, children: Sequence[dict[str, Any]], level: int = 3):
    """A heading that reads as a heading and folds like a toggle.

    `heading_N` with `is_toggleable: true`, which is how Notion itself
    builds a collapsible section — verified against this workspace before
    it was used, along with the two-level nesting a table inside one needs
    (`heading_3 > table > table_row`). That check was not optional:
    `publish()` archives the live page **before** it appends the new body,
    so a block type the API refuses does not fail safely — it leaves the
    company's Control Tower empty (the C113 shape).

    Empty `children` would render a toggle that opens onto nothing, so the
    caller gets a plain heading instead.
    """
    key = f"heading_{level}"
    body: dict[str, Any] = {"rich_text": [_text(content)]}
    if children:
        body["is_toggleable"] = True
        body["children"] = list(children)
    return {"object": "block", "type": key, key: body}


def _divider() -> dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> dict[str, Any]:
    def row(cells: Sequence[str], bold: bool = False) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": [[_text(str(c), bold=bold)] for c in cells]},
        }

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(headers),
            "has_column_header": True,
            "has_row_header": False,
            "children": [row(headers, bold=True)] + [row(r) for r in rows],
        },
    }


# --------------------------------------------------------------- rendering


def _fmt(value: Any) -> str:
    """One cell, as text.

    `None` becomes an em dash rather than the string `"None"`: a table full
    of the word None reads as data and is not.
    """
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(v) for v in value) if value else "—"
    text = str(value)
    return text if text else "—"


def _absence_note(payload: Mapping[str, Any], panel: Mapping[str, Any]) -> str | None:
    """Why this panel is empty — or None when it is not empty.

    Three different facts render as zero rows and mean opposite things, and
    `controltower/dashboard.py`'s own docstring is why they are separated
    here rather than counted together:

        UNSOURCED       this system has no source for that layer at all
        window filter   Events exist, none of them in the requested period
        no Events yet   the pipeline is configured and nothing has arrived
    """
    if panel.get("rows"):
        return None
    if str(panel.get("status")) != "SOURCED":
        note = panel.get("note") or ""
        return f"원천 없음 — 이 시스템에는 이 계층의 데이터가 존재하지 않는다. {note}".strip()
    window = payload.get("window") or {}
    if window.get("since") or window.get("until"):
        return (
            f"기간 내 Event 없음 — {_fmt(window.get('since'))} ~ {_fmt(window.get('until'))} "
            "범위에 해당하는 Event가 없다. 다른 기간에는 있을 수 있다."
        )
    model = payload.get("model") or {}
    if not model.get("events_read"):
        return "아직 입력되지 않음 — 수집된 Event가 하나도 없다."
    return "해당 없음 — Event는 있으나 이 표에 들어갈 항목이 없다 (예: 열려 있는 Blocker 0건)."


def _panel_table(
    payload: Mapping[str, Any],
    panel: Mapping[str, Any],
    columns: Sequence[str],
    *,
    heading: int | None = 3,
):
    """One panel as (blocks, warnings).

    `heading=None` renders the table with no heading of its own, for a
    caller that has already written one (a toggle heading, say).
    """
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []

    # The panel key is **not** in the heading any more (C134). `RISKS`,
    # `COMPLETIONS`, `METRICS` are the Model's identifiers; on the surface
    # this company's non-developers read they were noise beside a title
    # that already said the same thing in Korean.
    title = str(panel.get("title") or panel.get("key"))
    if heading is not None:
        blocks.append(_heading(title, heading))

    absence = _absence_note(payload, panel)
    if absence is not None:
        blocks.append(_callout(absence, "⚪", "gray_background", markup=True))
        return blocks, warnings

    available = list(panel.get("columns") or [])
    shown = [c for c in columns if c in available] or available[:6]
    rows = list(panel.get("rows") or [])
    total = len(rows)
    visible = rows[:MAX_TABLE_ROWS]

    table_rows = [[_fmt((r.get("values") or {}).get(c)) for c in shown] for r in visible]
    # Headers a person can read, not the Model's field names. Measured on
    # the published page before C134: `display_name`, `blocked_project_count`,
    # `days_silent`, `evidence_count` were the column headings on the one
    # surface this company's non-developers read.
    blocks.append(_table(_column_labels(shown), table_rows))

    if total > len(visible):
        blocks.append(
            _paragraph(
                f"위 표는 {total}건 중 {len(visible)}건만 보여준다. "
                "전체는 Dashboard(브라우저)에서 확인한다."
            )
        )
    dropped = [c for c in available if c not in shown]
    if dropped:
        # Named the way a reader would name them. This line used to print
        # `key, derived_from` — internal identifiers — under every panel.
        blocks.append(
            _paragraph(f"이 표에 싣지 않은 열: {', '.join(_column_labels(dropped))}")
        )
    return blocks, warnings


def _operational_block_lines(payload: Mapping[str, Any], key: str) -> list[str]:
    """One of `ops_status.py`'s rendered blocks, as its own lines.

    `gather()` carries these beside the model: the operational blocks,
    captured as the text `ops_status.py` printed. They are **not** part of
    `to_payload()`, so — like the ATTENTION list — nothing has redacted them
    (see `_safe()`), and the caller must.

    Returned as lines rather than one string so each becomes its own Notion
    paragraph. `one_line()` would otherwise escape the newlines into literal
    `\\n` and render the whole block as a single unreadable run.

    The heading and its rule are dropped: this page supplies its own
    heading, and repeating the terminal's would put two titles on one
    section.
    """
    for block in payload.get("blocks") or ():
        if block.get("key") != key:
            continue
        lines = (block.get("text") or "").splitlines()
        return [
            line for line in lines[2:]
            if line.strip() and set(line.strip()) != {"-"}
        ]
    return []


def build_control_tower_blocks(
    payload: Mapping[str, Any], *, dashboard_url: str | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """The whole page, as Notion blocks, plus any non-fatal warnings."""
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []

    model = payload.get("model") or {}
    coverage = model.get("coverage") or {}
    attention = list(payload.get("attention") or [])
    window = payload.get("window") or {}

    panels = {p.get("key"): p for p in model.get("panels") or []}
    layout = dict(PANEL_LAYOUT)

    def metric(key: str):
        """One METRICS row's value, or None when the Model did not carry it.

        `None`, never 0. A number this page could not find is not a number
        that measured zero, and every other honesty rule here depends on
        that distinction holding.
        """
        panel = panels.get("METRICS")
        for row in (panel or {}).get("rows") or []:
            values = row.get("values") or {}
            if values.get("key") == key:
                return values.get("value")
        return None

    def metric_row(key: str):
        panel = panels.get("METRICS")
        for row in (panel or {}).get("rows") or []:
            if (row.get("values") or {}).get("key") == key:
                return row
        return None

    # ================================================== (1) STATUS
    #
    # One line, and it is the only thing a reader who has ten seconds will
    # take away. Vocabulary from `controltower/verdict.py` so the browser
    # page and this one cannot describe one company in two languages.
    counts = _attention_tally(attention)
    severe = counts.get("P1", 0) + counts.get("?", 0)
    if payload.get("model_error"):
        tone = "bad"
        headline = (
            "Control Tower 모델을 만들지 못했다 — 아래 지표는 비어 있거나 "
            # An exception message, which is authored text as surely as a
            # blocker is: it routinely quotes the value that broke.
            f"불완전하다: {_safe(payload['model_error'])}"
        )
        warnings.append("model_error present")
    elif severe:
        tone = "bad"
        headline = (
            f"{len(attention)}건, 그중 P1 {counts.get('P1', 0)}건"
            + (f" · 미분류 {counts['?']}건" if counts.get("?") else "")
            + " · 아래 ②를 위에서부터 읽는다."
        )
    elif attention:
        tone = "warn"
        headline = f"{len(attention)}건 · 사람이 확인해야 할 항목이 있다."
    elif not model.get("events_read"):
        # "할 일 없음"과 "셀 것이 없음"은 다르다. An empty corpus with an
        # empty ATTENTION list is not a healthy company; it is a company
        # nobody has evidence about, and the green callout said the first.
        #
        # **And when git has something, this page can say which of the two
        # it is.** That is the whole reason `CODE_CHANGES` exists, and this
        # is the one line on the page where it pays: Events 0 with commits
        # on the same days is not a quiet company, it is delivery that did
        # not arrive — the failure that has no signal anywhere else.
        #
        # Measured on the live tree with a one-day window (2026-09-02):
        # `events_read: 0`, `CODE_CHANGES rows=1`, one commit touching 21
        # files. Before this branch the page said only "셀 Event가 없다".
        tone = "warn"
        headline = (
            "셀 Event가 없다 — '문제 없음'이 아니라 '판단할 증거가 없다'. "
            "수집된 Event가 하나도 없어 아래 숫자는 전부 0이다."
        )
        code_rows = len((panels.get("CODE_CHANGES") or {}).get("rows") or [])
        if code_rows:
            headline += (
                f" **그런데 같은 기간 Git에는 commit이 {code_rows}건 있다** — "
                "일이 없었던 것이 아니라 보고가 도착하지 않았을 가능성이 크다. "
                "⑤의 Git 표와 Desktop별 보고 현황을 함께 본다."
            )
    else:
        tone = "ok"
        headline = (
            "ATTENTION 없음 — 자동 점검이 문제를 찾지 못했다. "
            "이것은 '확인된 항목이 없다'는 뜻이며 회사가 잘 돌아간다는 뜻은 아니다."
        )
    blocks.append(
        _callout(
            f"{_verdict.word(tone)} — {headline}",
            _verdict.emoji(tone),
            _verdict.colour(tone),
        )
    )

    blocks.append(_heading("① 지금 상태", 2))
    open_blockers = metric("open_blockers")
    silent_rows = [
        r for r in (panels.get("DESKTOPS") or {}).get("rows") or []
        if isinstance((r.get("values") or {}).get("days_silent"), (int, float))
        and not isinstance((r.get("values") or {}).get("days_silent"), bool)
        and (r.get("values") or {}).get("days_silent") >= SILENT_AFTER_DAYS
    ]
    fleet = len((panels.get("DESKTOPS") or {}).get("rows") or [])
    blocks.extend(
        _bullet(line)
        for line in (
            f"전체 상태: {_verdict.word(tone)}",
            f"마지막 갱신: {_fmt(payload.get('generated_at'))} "
            "(사람이 publish_control_tower.py를 실행한 시각)",
            "열려 있는 Blocker: "
            + ("확인 불가 — 이 값을 읽지 못했다" if open_blockers is None
               else f"{open_blockers}건"),
            # Who the work is for, before how urgent it is (C147).
            #
            # The severity line below counts across both audiences, and on a
            # real tree that reads badly: "즉시 조치(P1): 6건" where all six
            # were Company Ops maintenance and the two things needing a
            # person were in the P2 half. This section is the one a reader
            # takes in at a glance, so it says the split ② is now grouped by
            # — otherwise ① keeps the confusion ② no longer has.
            #
            # Stated as a plain count, not a verdict: "회사 0건" is a real
            # and good answer, and it must not read as "nothing is known".
            "지금 봐야 할 것: 회사 "
            + f"{sum(1 for line in attention if _attention_domain(line) == _COMPANY)}건"
            + " · 시스템(Company Ops 자체) "
            + f"{sum(1 for line in attention if _attention_domain(line) == _SYSTEM)}건",
            f"그중 즉시 조치(P1): {counts.get('P1', 0)}건 · 확인 필요(P2): "
            f"{counts.get('P2', 0)}건"
            + (f" · 미분류: {counts['?']}건" if counts.get("?") else ""),
            f"{SILENT_AFTER_DAYS}일 이상 조용한 Desktop: {len(silent_rows)}/{fleet}",
            # Asked for by the brief, and the honest answer is that there is
            # no such thing here to report. `SPRINTS` is UNSOURCED: neither
            # the Event Schema nor the Company Repository has a Sprint or a
            # Cycle, so a number in this slot would be invented.
            "현재 Cycle / Sprint: 원천 없음 — 이 시스템에 Sprint 계층이 없다 "
            "(⑥의 '원천이 없는 계층' 참조)",
        )
    )

    # ================================================== (2) ATTENTION
    #
    # **Ranked, labelled, and each line carries its remedy.** The ranking
    # rule is `controltower/attention.py`, shared with the browser page so
    # two surfaces cannot order one list differently. A line the rule does
    # not recognise is `?` and sorts to the top rather than being filed as
    # minor.
    #
    # Truncation follows the rank, not arrival order — if only twenty of
    # forty fit, the twenty that fit must be the twenty that matter.
    blocks.append(
        _heading(
            f"② 지금 봐야 할 것 — {len(attention)}건" if attention else "② 지금 봐야 할 것",
            2,
        )
    )
    if attention:
        blocks.append(
            _paragraph(
                " · ".join(
                    f"{level} {counts[level]}건"
                    for level in ("P1", "?", "P2")
                    if counts.get(level)
                )
                + " — 심각도와 다음 행동은 이 페이지의 분류이며 각 줄에 근거를 붙였다. "
                "Event Schema에도 Run Manifest에도 심각도 필드는 없다."
            )
        )
        # **Audience first, severity within it.**
        #
        # Severity alone was the whole order until C147, and severity answers
        # "how broken is the pipeline" — correctly, and `attention.RULES`
        # defends it: a blocked Project is P2 because the pipeline is working
        # perfectly on work a person has stopped. For `ops_status.py`, read
        # by an operator, that is the right ordering.
        #
        # This page is the one a CEO opens, and its own callout says to read
        # this section from the top. Measured on a tree with two real
        # blockers and a stopped pipeline at once:
        #
        #     🔴 즉시 조치 (6건)   6/6 about Company Ops itself
        #     🟡 확인 필요 (6건)   ...3rd and 4th: the two blocked Projects,
        #                          one of them the CEO's own pending approval
        #
        # Six tool-maintenance items before the thing that needed them. No
        # line was wrong; the page had one axis and two jobs for it.
        #
        # So the company's list comes first and keeps its own severity order
        # inside. Nothing about severity changed, `ops_status.py` and the
        # browser page are untouched, and the `attention` module stays the
        # single place that reads a line.
        shown = sorted(attention, key=_attention_rank)[:MAX_TABLE_ROWS]
        for audience in (_COMPANY, _SYSTEM):
            in_audience = [i for i in shown if _attention_domain(i) == audience]
            if not in_audience:
                continue
            # The heading earns its place only when both groups are present:
            # with one group it is a title over the whole section, which is
            # what `②` already is.
            if any(
                _attention_domain(i) != audience for i in shown
            ):
                blocks.append(
                    _paragraph(
                        f"{_DOMAIN_LABELS[audience]} — {len(in_audience)}건"
                    )
                )
            for group, emoji, title in (
                ("P1", "🔴", "즉시 조치"),
                ("?", "🟣", "분류하지 못함 — 직접 읽어야 한다"),
                ("P2", "🟡", "확인 필요"),
            ):
                items = [
                    i for i in in_audience if _attention_severity(i)[0] == group
                ]
                if not items:
                    continue
                blocks.append(_paragraph(f"{emoji} {title} ({len(items)}건)"))
                for item in items:
                    level, why = _attention_severity(item)
                    prefix = f"[{level}] " + (f"({why}) " if why else "(분류 불가) ")
                    action = _attention_action(item)
                    suffix = (
                        f" → 다음 행동: {_safe(action)}"
                        if action
                        else " → 다음 행동: 이 페이지가 정해 두지 않았다 — 줄 전문을 "
                        "읽고 사람이 판단한다."
                    )
                    # **The remedy is what survives the 2,000-character cut, not
                    # what falls off it.** `_text()` trims from the right and the
                    # remedy is on the right, so a long line would have lost
                    # exactly the sentence saying what to do — on exactly the
                    # item where a reader most needs it. Nothing bounds `blocker`
                    # or `summary` upstream, so the long case is reachable.
                    room = RICH_TEXT_LIMIT - len(prefix) - len(suffix)
                    text = _safe(item)
                    if room > 0 and len(text) > room:
                        text = text[: max(0, room - 1)] + "…"
                    blocks.append(_bullet(prefix + text + suffix, markup=True))
        if len(attention) > MAX_TABLE_ROWS:
            blocks.append(
                _paragraph(
                    f"ATTENTION {len(attention)}건 중 심각한 {MAX_TABLE_ROWS}건만 "
                    "표시했다. 전체는 Dashboard(브라우저)에서 확인한다."
                )
            )
    else:
        blocks.append(_paragraph("없음. 사람이 지금 확인해야 할 항목은 없다."))

    # ================================================== (3) KPI
    #
    # Five callouts, not a nine-row table. A person reads one big number far
    # faster than a ten-column grid, and the executive-dashboard rule this
    # was measured against puts three to five headline numbers above the
    # fold. The full nine stay one toggle away in ⑥ rather than being cut.
    #
    # Every tile carries a **word** for its state as well as a colour: three
    # of the nine have a direction and six are volume, and a reader must not
    # have to guess which kind they are looking at.
    blocks.append(_heading("③ 핵심 숫자", 2))
    # Whether there is anything to count at all. Every verdict below is
    # conditioned on it — see `verdict.metric_verdict()`.
    measured = bool(model.get("events_read"))
    metrics_panel = panels.get("METRICS")
    if metrics_panel is None or not (metrics_panel.get("rows") or []):
        blocks.append(
            _callout(
                "지표를 만들지 못했다 — 아래 ⑥에서 원인을 확인한다.",
                "⚪",
                "gray_background",
            )
        )
    else:
        if not measured:
            blocks.append(
                _callout(
                    "아래 0은 '일이 없었다'가 아니라 '셀 Event가 없다'는 뜻이다 — "
                    "수집된 Event가 하나도 없어 어떤 숫자도 판정할 수 없다.",
                    "🟡",
                    "yellow_background",
                )
            )
        for key, icon in _verdict.HEADLINE_METRICS:
            row = metric_row(key)
            if row is None:
                continue
            values = row.get("values") or {}
            value = values.get("value")
            word, metric_tone = _verdict.metric_verdict(key, value, measured=measured)
            evidence = row.get("evidence_count", 0)
            blocks.append(
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "rich_text": [
                            _text(f"{_fmt(values.get('label'))}   "),
                            _text(_fmt(value), bold=True),
                            _text(f"   {word}"),
                            _text(_metric_cite(evidence, value)),
                        ],
                        "icon": {"type": "emoji", "emoji": icon},
                        "color": _verdict.colour(metric_tone),
                    },
                }
            )

    # --------------------------------------------- ③b role KPI (C149)
    #
    # Inside ③ rather than as a section of its own, and that is the whole
    # design: ③ *is* the KPI section, and a fourth heading would have put
    # "the numbers" and "the KPIs" in two places on a page whose stated job
    # is to be read in ten seconds.
    #
    # Three toggles, one per officer, closed by default. The five callouts
    # above are what everyone reads; this is what each of the three opens
    # when the answer they need is not in those five.
    #
    # The tally callout is computed **here**, from the rows, and not carried
    # on the panel: `_role_kpi_panel()`'s note deliberately has no count in
    # it, because panel metadata is the one thing `to_payload()` never
    # redacts and a note whose text moves with the evidence would break that
    # claim. Counting rows is the renderer's job.
    role_kpi = panels.get("ROLE_KPI")
    if role_kpi is None:
        warnings.append("panel ROLE_KPI missing from the model")
    else:
        kpi_rows = list(role_kpi.get("rows") or [])
        measurable = sum(
            1 for row in kpi_rows if (row.get("values") or {}).get("measured")
        )
        blocks.append(
            _callout(
                f"역할별 KPI {len(kpi_rows)}개 중 {measurable}개만 이 시스템이 "
                "계산할 수 있다. 나머지는 값 대신 DATA REQUIRED를 싣고, 무엇이 "
                "있어야 답할 수 있는지 각 행에 적는다 — 추정치를 넣지 않는다. "
                "이것은 결함이 아니라 이 시스템이 실행을 재고 사업을 재지 "
                "않는다는 사실이다.",
                "⚪",
                "gray_background",
            )
        )
        for role, title in (
            ("CEO", "CEO 관점 — 회사가 어떻게 되고 있는가"),
            ("CTO", "CTO 관점 — 개발이 어떻게 흐르고 있는가"),
            ("COO", "COO 관점 — 무엇이 막혀 있고 무엇을 기다리는가"),
        ):
            subset = [
                row
                for row in kpi_rows
                if (row.get("values") or {}).get("role") == role
            ]
            if not subset:
                # Present-and-empty rather than absent, the distinction
                # `daily/role_summary.py` argues for: a missing toggle and a
                # role with no KPIs look identical, and only one is fine.
                blocks.append(
                    _toggle_heading(
                        title,
                        [
                            _callout(
                                f"{role} KPI가 모델에 없다 — 이 페이지가 만든 "
                                "빈 칸이 아니라 읽지 못한 것이다.",
                                "🟡",
                                "yellow_background",
                            )
                        ],
                    )
                )
                continue
            role_blocks, role_warnings = _panel_table(
                payload,
                # A view of the panel narrowed to one role. `_panel_table()`
                # is handed the same shape it always gets rather than being
                # taught about roles: it is the one place that decides how a
                # panel becomes a table, and a second table builder here
                # would be the second opinion this module avoids everywhere
                # else.
                {**role_kpi, "rows": subset},
                layout["ROLE_KPI"],
                heading=None,
            )
            warnings.extend(role_warnings)
            blocks.append(_toggle_heading(title, role_blocks))

    # --------------------------------------------- ③c cohort
    #
    # Inside ③ for ③b's reason, and last inside it: it is the same Projects
    # read a third way, and it is the only thing on this page that answers
    # "나아지고 있는가" rather than "지금 얼마인가". A toggle, closed, because
    # a trend is what somebody opens after the five callouts have told them
    # nothing is on fire.
    #
    # The sentence above the toggle is the one thing a reader must not have to
    # open the table for: a `DATA REQUIRED` in this table is **not** a zero,
    # and every other number on this page is a count where zero means zero.
    cohort = panels.get("COHORT")
    if cohort is None:
        warnings.append("panel COHORT missing from the model")
    elif not cohort.get("rows"):
        blocks.append(
            _paragraph(
                "Cohort: 아직 Cohort를 이룰 Project가 없다 — 원천이 없는 것이 "
                "아니라 이 기간까지의 증거에 처음 나타난 Project가 없다."
            )
        )
    else:
        cohort_blocks, cohort_warnings = _panel_table(
            payload, cohort, layout["COHORT"], heading=None
        )
        warnings.extend(cohort_warnings)
        blocks.append(
            _toggle_heading(
                "시작한 달로 묶어 본 진행 (Cohort)",
                [
                    _callout(
                        "각 Project의 **첫 Event가 속한 달**로 묶고, 그때까지 "
                        "**아직 돌아가고 있던** Project가 그 뒤 N일 안에 다시 "
                        "움직였는지를 본다. 분모(`D+N 분모`)는 Cohort 크기가 "
                        "아니다 — 창이 아직 지나지 않은 것과, 창 안에 완료·취소로 "
                        "**끝난 것**(`D+N 종료`)을 뺀 수다. 끝난 것을 빼지 않으면 "
                        "취소된 Project가 '계속 움직였다'로, 첫날 끝낸 Project가 "
                        "'멈췄다'로 세어진다 — 둘 다 거꾸로다. 아직 지나지 않은 "
                        "창은 0%가 아니라 DATA REQUIRED이고, 창 안에 전부 "
                        "끝났으면 해당 없음이다. 단위는 고객이 아니라 Project다.",
                        "⚪",
                        "gray_background",
                    ),
                    *cohort_blocks,
                ],
            )
        )

    # ================================================== (4) PROJECTS
    blocks.append(_heading("④ Project", 2))
    projects = panels.get("PROJECTS")
    if projects is None:
        warnings.append("panel PROJECTS missing from the model")
    else:
        project_blocks, project_warnings = _panel_table(
            payload, projects, layout["PROJECTS"], heading=None
        )
        blocks.extend(project_blocks)
        warnings.extend(project_warnings)
        # Owner and Next Action are asked for and are **not** derived. The
        # Model carries `teams` (which Desktop reported it), and that is not
        # the same as a person who owns the outcome; a next action per
        # project exists nowhere in this system. Saying so once beats an
        # empty column that reads as "nobody is on it".
        blocks.append(
            _paragraph(
                "Owner / Next Action 열은 없다 — 이 시스템에 Project 담당자와 "
                "다음 작업의 원천이 없다. Team은 그 Event를 보고한 Desktop이지 "
                "책임자가 아니다. 막힌 Project의 다음 행동은 ②에 있다. "
                # C148. The same species of gap as the two named above, in
                # the one place a reader is most likely to fill it in
                # themselves: a table read top to bottom looks ranked.
                #
                # It is not. `_projects_panel()` orders blocked-first, then
                # longest-idle, then id — chosen so the table does not
                # reshuffle between runs, and its docstring says so. An
                # Event carries thirteen fields and none of them is a
                # priority, an owner, or a due date, so **importance has no
                # source here at all** — the same reason Owner does not.
                #
                # Said rather than fixed: inventing a rank from elapsed days
                # would be this page's first made-up fact, which is the line
                # `승인 병목 · 다음 Sprint` already refuses to cross.
                "**이 표의 순서는 중요도가 아니다** — 막힌 Project 먼저, 그다음 "
                "오래 조용한 순이다(실행마다 순서가 바뀌지 않게 한 것이다). "
                "Event에는 우선순위 필드가 없어 이 시스템은 무엇이 더 중요한지 "
                "알지 못한다. 중요도는 사람이 정한다."
            )
        )

    # ================================================== (5) ACTIVITY
    blocks.append(_heading("⑤ 최근 변화", 2))
    activity = panels.get("ACTIVITY")
    if activity is None:
        warnings.append("panel ACTIVITY missing from the model")
    else:
        panel_blocks, panel_warnings = _panel_table(
            payload, activity, layout["ACTIVITY"], heading=None
        )
        blocks.extend(panel_blocks)
        warnings.extend(panel_warnings)

    # --------------------------------------------- ⑤b D+1 git changes (C149)
    #
    # Inside ⑤ for ③b's reason: this section *is* "최근 변화", and git is the
    # other record of the same days. Above the toggle is the sentence that
    # matters — a count, or the fact that git could not be asked — because
    # the point of the whole panel is what happens on a day nobody reported
    # an Event: every table above reads that day as quiet, and this one does
    # not.
    code = panels.get("CODE_CHANGES")
    if code is None:
        warnings.append("panel CODE_CHANGES missing from the model")
    else:
        blocks.append(
            _paragraph(
                # The window is in `note` and always printed, because this
                # panel covers whatever period the page was asked for — see
                # `_code_changes_panel()` on why it is not called "D+1".
                "Git 기준 변경: " + _fmt(code.get("note"))
                + " — 위 표는 사람이 **보고한** 것이고, 이것은 저장소에 "
                "**실제로 기록된** 것이다. 둘이 어긋나면 어긋난 것 자체가 사실이다."
            )
        )
        code_blocks, code_warnings = _panel_table(
            payload, code, layout["CODE_CHANGES"], heading=None
        )
        warnings.extend(code_warnings)
        blocks.append(_toggle_heading("Commit 목록", code_blocks))

    # ================================================== (6) DETAILS
    #
    # Everything a reader consults rather than scans, behind toggle headings
    # so the page stops at ⑤ for anyone who does not need it. Nothing is
    # deleted by the move: every panel the Model builds still renders
    # exactly once, and the sections that used to sit between ATTENTION and
    # the panels are here in full.
    blocks.append(_divider())
    blocks.append(_heading("⑥ 상세 — 필요할 때만 펼친다", 2))

    scope_children = [
        _bullet(line)
        for line in (
            f"마지막 갱신: {_fmt(payload.get('generated_at'))}",
            f"데이터 기간: {_fmt(window.get('since'))} ~ {_fmt(window.get('until'))}"
            + (
                "  (필터 없음 = 전체 기간)"
                if not (window.get("since") or window.get("until"))
                else ""
            ),
            f"읽은 Event: {_fmt(model.get('events_read'))}건",
            f"증거 범위: {_fmt(coverage.get('evidence_from'))} ~ "
            f"{_fmt(coverage.get('evidence_to'))}",
            f"읽을 수 없던 파일: {_fmt(coverage.get('unreadable'))}건"
            f" · 중복 파일: {_fmt(coverage.get('duplicates'))}건",
        )
    ]
    if not coverage.get("history_checked"):
        scope_children.append(
            _callout(
                "Company History를 아무도 확인하지 않았다 — 위 숫자는 "
                "'빈틈 없음'을 뜻하지 않는다.",
                "🟡",
                "yellow_background",
            )
        )
    elif not coverage.get("complete"):
        scope_children.append(
            _callout(
                "증거 범위에 빈틈이 있다"
                + (
                    f" — {coverage.get('history_uncovered_from')}부터 확인되지 않았다."
                    if coverage.get("history_uncovered_from")
                    else "."
                ),
                "🟡",
                "yellow_background",
            )
        )
    blocks.append(_toggle_heading("이 화면의 범위 · 데이터 Coverage", scope_children))

    # Is this real work, or a probe? The honest answer is that the system
    # cannot tell: `events.Event` has no field marking an Event as a probe,
    # so any label here would be this module guessing from a name.
    blocks.append(
        _toggle_heading(
            "이 데이터는 실제 업무인가",
            [
                _callout(
                    "이 시스템은 구별하지 못한다. Event Schema(docs/02)에 Event가 "
                    "실제 업무인지 Engineering Probe인지 표시하는 필드가 없다. "
                    "④의 Project 목록을 보고 사람이 판단해야 한다.",
                    "⚪",
                    "gray_background",
                )
            ],
        )
    )

    if metrics_panel is not None:
        full_kpi, kpi_warnings = _panel_table(
            payload, metrics_panel, layout["METRICS"], heading=None
        )
        warnings.extend(kpi_warnings)
        # The title says the relationship out loud. ③ shows five of these
        # nine and this shows all nine, which is "summary first, detail on
        # demand" and not a second copy — but a reader who meets the same
        # five numbers twice with nothing said is entitled to read it as
        # one, and "동일 정보 반복 금지" is the rule it would be breaking.
        _total_metrics = len(metrics_panel.get("rows") or [])
        _headline_metrics = sum(
            1 for key, _icon in _verdict.HEADLINE_METRICS if metric_row(key)
        )
        blocks.append(
            _toggle_heading(
                f"전체 지표 ({_total_metrics}개"
                + (f" — ③의 {_headline_metrics}개를 포함한다)"
                   if _total_metrics > _headline_metrics else ")"),
                full_kpi,
            )
        )

    for key, title in (
        ("RISKS", "Risk / Blocker 상세"),
        # A filtered view of ⑤, not a second source. Kept because "무엇이
        # 끝났는가" is its own question, folded because the rows are already
        # above.
        ("COMPLETIONS", "최근 완료만 따로 보기"),
        ("TEAMS", "팀별 진행현황"),
        ("DESKTOPS", "Desktop별 보고 현황"),
    ):
        panel = panels.get(key)
        if panel is None:
            warnings.append(f"panel {key} missing from the model")
            continue
        panel_blocks, panel_warnings = _panel_table(
            payload, panel, layout[key], heading=None
        )
        warnings.extend(panel_warnings)
        blocks.append(_toggle_heading(title, panel_blocks))

    unsourced = [
        p
        for p in model.get("panels") or []
        if str(p.get("status")) != "SOURCED" and p.get("key") not in layout
    ]
    if unsourced:
        blocks.append(
            _toggle_heading(
                f"원천이 없는 계층 ({len(unsourced)}개)",
                [
                    _paragraph(
                        "비어 있는 것이 아니라 물어볼 곳이 없다 — 이 시스템에 "
                        "해당 계층의 데이터가 존재하지 않는다."
                    )
                ]
                + [
                    _bullet(
                        f"{panel.get('title')} — "
                        f"{panel.get('note') or '이 시스템에 원천이 없다.'}",
                        markup=True,
                    )
                    for panel in unsourced
                ],
            )
        )

    # Approvals / next work: deliberately not derived. "Which decision is
    # blocking what" lives in BACKLOG.md and is written by a person;
    # inventing it from Events would be this page's first fabricated fact.
    blocks.append(
        _toggle_heading(
            "승인 병목 · 다음 Sprint",
            [
                _callout(
                    "자동화하지 않는다. 승인 대기 항목과 다음 Sprint는 사람이 "
                    "BACKLOG.md에 적는 판단이며, Event에서 유도하면 이 화면의 "
                    "첫 번째 지어낸 사실이 된다.",
                    "⚪",
                    "gray_background",
                )
            ],
        )
    )

    # Two different syncs share the word, and conflating them is how an
    # operator reads "up to date" off a page that is not:
    #
    #   Runner Notion Sync   writes Event state onto the PROJECTS **rows**,
    #                        on the Runner's schedule.
    #   this page's publish  rewrites this page, only when a person runs
    #                        `publish_control_tower.py`.
    #
    # The first can be broken for days while the second keeps succeeding.
    sync_children = [
        _bullet(
            f"이 페이지가 쓰인 시각: {_fmt(payload.get('generated_at'))} "
            "(publish_control_tower.py 실행 시각)"
        )
    ]
    sync_lines = _operational_block_lines(payload, "NOTION")
    if sync_lines:
        sync_children.append(
            _paragraph("Runner의 Notion Sync — PROJECTS Row에 Event 상태를 쓰는 쪽:")
        )
        sync_children.extend(
            _bullet(_safe(line), markup=True) for line in sync_lines
        )
    else:
        sync_children.append(
            _callout(
                "Runner의 Notion Sync 상태를 읽지 못했다 — 이 페이지가 최신이어도 "
                "Row 데이터는 오래됐을 수 있다.",
                "⚪",
                "gray_background",
            )
        )
    sync_children.append(
        _paragraph(
            "두 시각은 다른 것이다. 이 페이지는 사람이 명령을 실행할 때 갱신되고, "
            "PROJECTS Row는 Runner가 돌 때 갱신된다 — 한쪽이 며칠 멈춰 있어도 "
            "다른 쪽은 정상으로 보인다."
        )
    )
    blocks.append(_toggle_heading("동기화 상태", sync_children))

    # The live Dashboard, and it is **not** a link — that is the honest
    # part. `dashboard_server.py` binds 127.0.0.1 and is not configurable,
    # so the address opens the Control Tower only on the machine running
    # the server. A clickable link here fails for every other reader.
    reach_children = []
    if dashboard_url:
        reach_children.append(
            _callout(
                f"{dashboard_url} — 단, 이 주소는 Dashboard 서버를 켠 그 컴퓨터에서만 "
                "열린다. 127.0.0.1(loopback) 전용이며 다른 기기에서는 열리지 않는다.",
                "🖥",
                "blue_background",
            )
        )
    reach_children.extend(
        (
            _paragraph("그 컴퓨터에서 실행: python dashboard_server.py  (종료는 Ctrl+C)"),
            _paragraph(
                "같은 사실을 터미널에서만 보려면: python ops_status.py "
                "(ATTENTION이 있으면 exit 3)"
            ),
            _paragraph(
                "이 Notion 페이지를 지금 상태로 갱신하려면: python publish_control_tower.py"
            ),
        )
    )
    blocks.append(_toggle_heading("실시간 화면(Dashboard)에 가려면", reach_children))

    blocks.append(_divider())
    blocks.append(
        _paragraph(
            "이 페이지는 Dashboard와 같은 모델(dashboard_server.gather())에서 "
            "렌더링되며, publish_control_tower.py를 실행할 때만 갱신된다. "
            "스스로 갱신되지 않는다 — 위의 '마지막 갱신'이 이 숫자들이 언제의 것인지 말한다."
        )
    )
    return blocks, warnings


# ------------------------------------------------- the PROJECTS table view

#: The prefix on any `Notes` value this tool wrote.
#:
#: Short on purpose: `Notes` is a **column**, so whatever goes in it is read
#: sideways in a table row, and a full sentence of provenance would push the
#: fact off the visible width. It serves the same purpose as
#: `ROW_PAGE_MARKER` — telling this tool's own writing from a person's — and
#: obeys the same rule: a value that does not start with it is never
#: overwritten.
NOTE_MARKER = "[CT]"

#: `Notes` is a `rich_text` column, so Notion's ordinary cap applies. Nothing
#: here comes close; the bound exists so a long blocker sentence cannot make
#: the whole publish fail on a column nobody was reading anyway.
NOTE_LIMIT = 300


def build_project_note(payload: Mapping[str, Any], project_id: str) -> str | None:
    """One line of Control Tower status, for the PROJECTS `Notes` column.

    Why a column and not more page body. The row *properties* the Runner
    syncs (docs/04 §43) answer "what state is this project in". They do not
    answer "does it need me" — `days_idle`, the open-blocker age and the
    Event count are Control Tower derivations that live nowhere in Notion.
    Until now a person had to open each project to find out, and the table
    view — the thing Notion actually shows when you open a database — was
    silent about all of it.

    `Notes` is the column for it. docs/04 §43 lists the eleven properties V1
    Event Sync automates and `Notes` is not among them; §44 and §45 list what
    is reserved for COO and CEO judgement and `Notes` is not among those
    either. It is genuinely unassigned, and measured on this workspace it is
    empty on every row.

    "Unassigned" is not "ours", which is why `NOTE_MARKER` exists and why
    `publish_project_notes()` refuses to overwrite anything that does not
    carry it.

    Returns None when the Control Tower has no source for this project — the
    same restraint `publish_project_rows()` keeps, for the same reason.
    """
    model = payload.get("model") or {}
    panels = {p.get("key"): p for p in model.get("panels") or []}
    project = next(
        (
            r
            for r in (panels.get("PROJECTS") or {}).get("rows") or []
            if (r.get("values") or {}).get("project_id") == project_id
        ),
        None,
    )
    if project is None:
        return None
    values = project.get("values") or {}

    parts: list[str] = [NOTE_MARKER]

    blocker = values.get("blocker")
    if blocker:
        days = values.get("days_blocked")
        parts.append(
            f"\u26d4 Blocker {_fmt(days)}일째: {one_line_head(str(blocker), 60)}"
        )
    elif _is_settled(values.get("state")):
        # A project that has **ended** is not quiet, it is finished — and this
        # sentence is written into the company's own Notion row, where a
        # warning triangle is read as something to do. Measured before this,
        # on a project that shipped in March: `⚠ 186일째 조용함`.
        #
        # `state` rather than a second reading of `completed_at` / `status`:
        # the Model already folded those into one word and
        # `dashboard.is_settled()` is where that word is interpreted (C28).
        done = "완료" if values.get("state") == "COMPLETE" else "취소"
        when = str(values.get("completed_at") or values.get("last_seen") or "")[:10]
        parts.append("✅ " + done + (f" {when}" if when else ""))
    else:
        idle = values.get("days_idle")
        if isinstance(idle, int):
            # The threshold `ops_status.py` already uses for a silent
            # Desktop. Borrowed rather than re-chosen: two views disagreeing
            # about what counts as "quiet" is worse than either threshold.
            parts.append(
                (f"\u26a0 {idle}일째 조용함" if idle >= SILENT_AFTER_DAYS else f"{idle}일째 조용함")
            )

    parts.append(f"Event {_fmt(values.get('events'))}건")
    if values.get("status"):
        parts.append(_fmt(values.get("status")))

    generated = str(payload.get("generated_at") or "")
    if generated:
        parts.append(f"갱신 {generated[5:16].replace('T', ' ')}")

    note = " · ".join(parts)
    return note if len(note) <= NOTE_LIMIT else note[: NOTE_LIMIT - 1] + "\u2026"


def publish_project_notes(
    *, client, payload: Mapping[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Write each sourced project's status line into its `Notes` column.

    Returns `(written, skipped_hand_written)`.

    Independent of `publish_project_rows()` and its guard, deliberately: a
    person can type in the `Notes` column without touching the page body, or
    the other way round, and one refusal must not silence the other.
    """
    model = payload.get("model") or {}
    panels = {p.get("key"): p for p in model.get("panels") or []}
    sourced = {
        (r.get("values") or {}).get("project_id")
        for r in (panels.get("PROJECTS") or {}).get("rows") or []
    }

    written: list[str] = []
    hand_written: list[str] = []

    for row in client.list_pages():
        properties = row.get("properties") or {}
        project_id = "".join(
            item.get("plain_text", "")
            for item in (properties.get("Project ID") or {}).get("rich_text") or ()
        )
        if project_id not in sourced:
            continue
        existing = "".join(
            item.get("plain_text", "")
            for item in (properties.get("Notes") or {}).get("rich_text") or ()
        )
        if existing.strip() and not existing.startswith(NOTE_MARKER):
            hand_written.append(project_id)
            continue
        note = build_project_note(payload, project_id)
        if note is None:
            continue
        client.update_project(
            row["id"],
            {"Notes": {"rich_text": [{"type": "text", "text": {"content": note}}]}},
        )
        written.append(project_id)

    return tuple(written), tuple(hand_written)


# ------------------------------------------------------ project row pages

#: The first line of any row-page body this tool wrote.
#:
#: It is a permission slip, not decoration. A PROJECTS row is a page a
#: person can open and type in, and re-rendering it means archiving whatever
#: is already there. Without a way to recognise its own work, this would
#: eventually delete somebody's notes — so it rewrites a body **only** when
#: the body is empty or begins with this exact sentence, and reports the row
#: it left alone otherwise.
ROW_PAGE_MARKER = "이 내용은 publish_control_tower.py가 자동으로 씁니다. 직접 편집하면 다음 실행에서 덮어씁니다."


@dataclass(frozen=True)
class RowPageResult:
    """One pass over the PROJECTS rows."""

    written: tuple[str, ...] = field(default_factory=tuple)
    #: Rows skipped because a person had written in them. Never an error —
    #: the person's text wins, and the caller says which rows are stale.
    skipped_hand_written: tuple[str, ...] = field(default_factory=tuple)
    #: Rows in Notion the Control Tower has no source for. Left untouched:
    #: writing "no evidence" into a row this system never produced would be
    #: claiming authority over somebody else's data.
    skipped_unsourced: tuple[str, ...] = field(default_factory=tuple)
    blocks_archived: int = 0


def _is_ours(blocks: Sequence[Mapping[str, Any]]) -> bool:
    """True when this body is empty or was written by this tool."""
    if not blocks:
        return True
    first = blocks[0]
    body = first.get(first.get("type")) or {}
    text = "".join(
        (item.get("plain_text") or (item.get("text") or {}).get("content") or "")
        for item in (body.get("rich_text") or ())
    )
    return ROW_PAGE_MARKER in text


def build_project_row_blocks(
    payload: Mapping[str, Any],
    project_id: str,
    *,
    control_tower_url: str | None = None,
    dashboard_url: str | None = None,
) -> list[dict[str, Any]]:
    """One project's own evidence, for the body of its PROJECTS row.

    The row's *properties* already carry Status, Owner and Last Event — the
    Runner writes those every sync (docs/04 §6). What they cannot carry is
    **why**: which Events produced that state, and which files they came
    from. That is the evidence a person asks for the moment they doubt a
    status, and until now clicking a project in Notion showed an empty page.
    """
    model = payload.get("model") or {}
    panels = {p.get("key"): p for p in model.get("panels") or []}

    project = next(
        (
            r
            for r in (panels.get("PROJECTS") or {}).get("rows") or []
            if (r.get("values") or {}).get("project_id") == project_id
        ),
        None,
    )
    if project is None:
        return []
    values = project.get("values") or {}

    blocks: list[dict[str, Any]] = [
        _callout(
            f"{ROW_PAGE_MARKER}  ·  마지막 갱신 {_fmt(payload.get('generated_at'))}",
            "🤖",
            "gray_background",
        ),
        _paragraph(
            f"상태 {_fmt(values.get('status'))}"
            f"  ·  팀 {_fmt(values.get('teams'))}"
            f"  ·  Event {_fmt(values.get('events'))}건"
            f"  ·  마지막 {_fmt(values.get('last_seen'))}"
            # Same correction as `build_project_note()`, on the page a person
            # lands on from the row: "N일째 조용함" about a finished project is
            # a false alarm, and this surface repeated it word for word.
            + (
                f"  ·  끝난 뒤 {_fmt(values.get('days_idle'))}일"
                if _is_settled(values.get("state"))
                else f"  ·  {_fmt(values.get('days_idle'))}일째 조용함"
            )
        ),
    ]

    if values.get("blocker"):
        blocks.append(
            _callout(
                f"Blocker: {_fmt(values.get('blocker'))}"
                f"  (담당 {_fmt(values.get('blocker_team'))},"
                f" {_fmt(values.get('days_blocked'))}일째)",
                "⛔",
                "red_background",
            )
        )

    milestones = values.get("milestones")
    if milestones:
        blocks.append(_paragraph(f"Milestone: {_fmt(milestones)}"))

    # This project's Events, newest first, from the panel the page already
    # renders — never a second pass over the rollup.
    activity = [
        r
        for r in (panels.get("ACTIVITY") or {}).get("rows") or []
        if (r.get("values") or {}).get("project_id") == project_id
    ]
    blocks.append(_heading("이 Project의 Event", 3))
    if not activity:
        blocks.append(
            _callout(_absence_note(payload, panels.get("ACTIVITY") or {}) or
                     "해당 없음 — 이 기간에 이 Project의 Event가 없다.",
                     "⚪", "gray_background")
        )
    else:
        shown = activity[:MAX_TABLE_ROWS]
        blocks.append(
            _table(
                ("at", "source", "event_type", "summary", "event_id"),
                [
                    [
                        _fmt((r.get("values") or {}).get(c))
                        for c in ("at", "source", "event_type", "summary", "event_id")
                    ]
                    for r in shown
                ],
            )
        )
        if len(activity) > len(shown):
            blocks.append(
                _paragraph(f"{len(activity)}건 중 {len(shown)}건만 표시했다.")
            )

    # Evidence: the files behind the numbers above.
    evidence = project.get("evidence") or []
    blocks.append(_heading("Evidence (파일)", 3))
    if not evidence:
        blocks.append(_paragraph("없음."))
    else:
        for ref in evidence[:MAX_TABLE_ROWS]:
            blocks.append(
                _bullet(
                    f"{_fmt(ref.get('path'))}  ·  {_fmt(ref.get('event_id'))}"
                    f"  ·  {_fmt(ref.get('at'))}"
                )
            )
        total = project.get("evidence_count")
        if isinstance(total, int) and total > len(evidence):
            blocks.append(
                _paragraph(
                    f"증거 {total}건 중 {len(evidence)}건만 실렸다 "
                    "(Dashboard payload의 상한). 전체는 runtime/events/processed/."
                )
            )

    # A way back out.
    #
    # Someone who clicked into a project to check one number is one click
    # from the evidence and, until this, no clicks from anything else. The
    # company-wide view and the live screen are both elsewhere, and a page
    # that shows a detail without saying what it is a detail *of* leaves a
    # reader to navigate by memory.
    if control_tower_url or dashboard_url:
        blocks.append(_divider())
    if control_tower_url:
        blocks.append(
            _paragraph("← 전사 Control Tower (전체 현황)", link=control_tower_url)
        )
    if dashboard_url:
        blocks.append(
            _paragraph(
                f"실시간 화면: {dashboard_url} "
                "(Dashboard 서버를 켠 그 컴퓨터에서만 열린다)"
            )
        )
    return blocks


def publish_project_rows(
    *,
    transport,
    client,
    payload: Mapping[str, Any],
    control_tower_url: str | None = None,
    dashboard_url: str | None = None,
) -> RowPageResult:
    """Write each sourced project's evidence into its own PROJECTS row page.

    Only rows the Control Tower has a source for are touched, and only when
    their body is empty or carries `ROW_PAGE_MARKER`. Everything else is
    reported and left exactly as it was.
    """
    model = payload.get("model") or {}
    panels = {p.get("key"): p for p in model.get("panels") or []}
    sourced = [
        (r.get("values") or {}).get("project_id")
        for r in (panels.get("PROJECTS") or {}).get("rows") or []
    ]

    written: list[str] = []
    hand_written: list[str] = []
    unsourced: list[str] = []
    archived = 0

    for row in client.list_pages():
        properties = row.get("properties") or {}
        project_id = "".join(
            item.get("plain_text", "")
            for item in (properties.get("Project ID") or {}).get("rich_text") or ()
        )
        if project_id not in sourced:
            unsourced.append(project_id or "(no Project ID)")
            continue

        existing = transport.list_block_children(row["id"])
        if getattr(transport, "block_children_truncated", False):
            raise ControlTowerPageError(
                f"{project_id} Row의 블록 목록을 끝까지 읽지 못했다 — "
                "다시 렌더링하면 내용이 두 벌이 된다. 갱신을 중단했다."
            )
        # A child page is not body text this tool wrote, and archiving one
        # would take the Control Tower page with it.
        body = [b for b in existing if b.get("type") != "child_page"]
        if not _is_ours(body):
            hand_written.append(project_id)
            continue

        blocks = build_project_row_blocks(
            payload,
            project_id,
            control_tower_url=control_tower_url,
            dashboard_url=dashboard_url,
        )
        if not blocks:
            continue
        for block in body:
            transport.delete_block(block["id"])
            archived += 1
        for group in _chunk(blocks, MAX_CHILDREN_PER_APPEND):
            transport.append_block_children(row["id"], group)
        written.append(project_id)

    return RowPageResult(
        written=tuple(written),
        skipped_hand_written=tuple(hand_written),
        skipped_unsourced=tuple(unsourced),
        blocks_archived=archived,
    )


# ------------------------------------------------- the database description

#: Notion's cap on one description item, measured against the live API:
#: `body.description[0].text.content.length should be <= 2000`. Several
#: items concatenate, so the summary is split rather than truncated.
DESCRIPTION_ITEM_LIMIT = RICH_TEXT_LIMIT


def build_database_summary(
    payload: Mapping[str, Any],
    *,
    page_hint: str | None = None,
    dashboard_url: str | None = None,
) -> list[dict[str, Any]]:
    """The Control Tower in one paragraph, for the PROJECTS description.

    Why this surface matters more than its size suggests. Measured on this
    workspace: the integration can see exactly **one** top-level object, the
    PROJECTS database. So the description — the paragraph Notion renders
    directly under the database title — is the first and, without
    navigating, the only thing a person reads when they open Notion. It was
    empty.

    The full page (`publish()`) is still where the tables live. This is the
    line that tells someone whether they need to go there.

    It says the same things in the same words as the page, including the two
    it must refuse to answer: whether the rows are real business work, and
    what the next task is. A summary that quietly drops the caveats the full
    view carries is worse than no summary — it is the confident half of an
    honest report.
    """
    model = payload.get("model") or {}
    coverage = model.get("coverage") or {}
    attention = list(payload.get("attention") or [])
    window = payload.get("window") or {}
    panels = {p.get("key"): p for p in model.get("panels") or []}

    def rows_of(key):
        return len((panels.get(key) or {}).get("rows") or [])

    def open_blockers() -> int:
        """Rows of `RISKS` that are actually **Blockers**.

        `RISKS` is a union of three row shapes — `OPEN_BLOCKER`,
        `ROLE_MISMATCH`, `EVENT_ID_CONFLICT` — and this summary counted all
        of them as blockers (C134). Measured: one open Blocker beside two
        role mismatches rendered `열린 Blocker 3` on the PROJECTS database
        description, which is the **first** thing a person sees when they
        open Notion (this integration can see exactly one top-level object).

        Overstating a blocker count is the direction that costs someone an
        afternoon: they go looking for two projects that are not stuck.
        """
        return sum(
            1
            for row in (panels.get("RISKS") or {}).get("rows") or []
            if (row.get("values") or {}).get("kind") == "OPEN_BLOCKER"
        )

    lines: list[str] = []

    if payload.get("model_error"):
        lines.append("[Control Tower] 모델을 만들지 못했다 — 아래 숫자는 신뢰할 수 없다.")
    elif attention:
        lines.append(f"[Control Tower] 주의 {len(attention)}건 — 사람이 확인해야 한다.")
    else:
        lines.append(
            "[Control Tower] ATTENTION 없음 — 자동 점검이 문제를 찾지 못했다는 "
            "뜻이며, 회사가 잘 돌아간다는 뜻은 아니다."
        )

    lines.append(f"마지막 갱신 {_fmt(payload.get('generated_at'))}")

    events = model.get("events_read")
    if payload.get("model_error"):
        pass
    elif not events:
        # The three absences the page distinguishes, kept distinct here too.
        if window.get("since") or window.get("until"):
            lines.append(
                f"기간 내 Event 없음 ({_fmt(window.get('since'))}~"
                f"{_fmt(window.get('until'))}) — 다른 기간에는 있을 수 있다."
            )
        else:
            lines.append("아직 입력되지 않음 — 수집된 Event가 하나도 없다.")
    else:
        scope = (
            f"Event {events}건"
            f" ({_fmt(coverage.get('evidence_from'))}~{_fmt(coverage.get('evidence_to'))})"
            f" · Project {rows_of('PROJECTS')}"
            f" · 열린 Blocker {open_blockers()}"
        )
        if window.get("since") or window.get("until"):
            scope += f" · 기간필터 {_fmt(window.get('since'))}~{_fmt(window.get('until'))}"
        lines.append(scope)

    if not coverage.get("history_checked"):
        lines.append("Company History를 아무도 확인하지 않았다 — 빈틈 없음을 뜻하지 않는다.")
    elif not coverage.get("complete"):
        lines.append("증거 범위에 빈틈이 있다.")

    # The caveat that must survive summarising.
    lines.append(
        "이 Row들이 실제 업무인지 Engineering Probe인지 이 시스템은 구별하지 "
        "못한다 (Event Schema에 해당 필드가 없다)."
    )

    if attention:
        lines.append(f"가장 먼저: {one_line_head(_safe(attention[0]))}")

    # Kept as a sentence, not a link.
    #
    # A database `description` is one rich-text array, and `_split()` may cut
    # it anywhere to respect Notion's per-item cap — which would sever a
    # link mid-run and leave half of it as plain text. The link belongs on
    # the row pages, where blocks are separate and nothing is chopped.
    lines.append(
        "전체 화면: 이 Database의 COMPANY_OPS Row 안 "
        f"'{page_hint or 'Control Tower'}' 하위 페이지 (각 Project Row 하단에 링크)."
    )
    if dashboard_url:
        lines.append(
            f"실시간 화면: {dashboard_url} (서버를 켠 컴퓨터에서만 열린다)"
        )
    lines.append(
        "이 설명은 publish_control_tower.py를 실행할 때만 갱신된다 — "
        "스스로 갱신되지 않는다."
    )

    text = "\n".join(lines)
    return [
        {"type": "text", "text": {"content": part}}
        for part in _split(text, DESCRIPTION_ITEM_LIMIT)
    ]


def one_line_head(text: str, limit: int = 110) -> str:
    """The first sentence of an ATTENTION item, on one line.

    Bounded rather than whole: these run to several hundred characters
    because each names its own remedy, and the description has room for one
    pointer, not one essay. The full text is on the page.
    """
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def _split(text: str, size: int) -> list[str]:
    """`text` in chunks no Notion item will refuse.

    Split rather than truncated: every line here was chosen because someone
    needs it, and silently dropping the tail is the failure this project
    keeps finding. In practice one chunk is enough — the guard is for the
    day an ATTENTION list grows.
    """
    if len(text) <= size:
        return [text]
    return [text[i : i + size] for i in range(0, len(text), size)]


def publish_database_summary(
    *,
    transport,
    database_id: str,
    payload: Mapping[str, Any],
    page_hint: str | None = None,
    dashboard_url: str | None = None,
) -> int:
    """Write the summary onto the database description. Returns its length.

    Idempotent by nature rather than by bookkeeping: the description is one
    value and this replaces it, so running twice leaves what running once
    left. Nothing is appended and no schema property is touched — the
    endpoint is shared with `update_database()` and that separation is why
    the two are different methods.
    """
    rich_text = build_database_summary(
        payload, page_hint=page_hint, dashboard_url=dashboard_url
    )
    transport.set_database_description(database_id, rich_text)
    return sum(len((item.get("text") or {}).get("content") or "") for item in rich_text)


# ---------------------------------------------------------------- publish


def _chunk(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def find_child_page(transport, parent_page_id: str, title: str) -> str | None:
    """The id of the child page called `title`, or None.

    Matching on the title is what makes `publish()` idempotent, and it is
    matched exactly: a fuzzy match would eventually adopt a page a person
    created and rewrite its body.
    """
    for block in transport.list_block_children(parent_page_id):
        if block.get("type") != "child_page":
            continue
        if (block.get("child_page") or {}).get("title") == title:
            return block.get("id")
    return None


def publish(
    *,
    transport,
    parent_page_id: str,
    payload: Mapping[str, Any],
    title: str = "Control Tower",
    dashboard_url: str | None = None,
) -> PublishResult:
    """Render `payload` into the child page `title`, creating it if absent."""
    blocks, warnings = build_control_tower_blocks(payload, dashboard_url=dashboard_url)

    existing_id = find_child_page(transport, parent_page_id, title)
    if existing_id is None:
        first, rest = blocks[:MAX_CHILDREN_PER_APPEND], blocks[MAX_CHILDREN_PER_APPEND:]
        page = transport.create_child_page(parent_page_id, title, first)
        page_id = page.get("id")
        for group in _chunk(rest, MAX_CHILDREN_PER_APPEND):
            transport.append_block_children(page_id, group)
        return PublishResult(
            page_id=page_id,
            url=page.get("url"),
            created=True,
            blocks_written=len(blocks),
            blocks_archived=0,
            title=title,
            warnings=tuple(warnings),
        )

    # Re-render: archive what is there, then write the new body.
    #
    # Archive-then-write rather than write-then-archive on purpose. The
    # reverse order shows a reader two copies of the Control Tower for the
    # duration, and if the archiving half fails it leaves them permanently —
    # a page that says two different things about the company is worse than
    # a page that is briefly empty.
    old = transport.list_block_children(existing_id)
    if getattr(transport, "block_children_truncated", False):
        # Every block not seen here would survive the re-render and the page
        # would grow a second copy of itself. Refusing is the only safe
        # answer; it is also recoverable, which a doubled page is not.
        raise ControlTowerPageError(
            "기존 페이지의 블록 목록을 끝까지 읽지 못했다 — 다시 렌더링하면 "
            "페이지가 두 벌이 된다. 갱신을 중단했다."
        )
    archived = 0
    for block in old:
        transport.delete_block(block["id"])
        archived += 1
    try:
        for group in _chunk(blocks, MAX_CHILDREN_PER_APPEND):
            transport.append_block_children(existing_id, group)
    except Exception as exc:  # noqa: BLE001
        # The window this ordering deliberately opens, made speakable.
        #
        # Archive-then-write is the right order (see above), but "briefly
        # empty" is only brief if someone runs this again — and until C113
        # nothing said the page had been emptied. Measured: an append that
        # failed after the archive left the live page at **0 blocks**, and
        # the caller printed only the API error. An operator who then opened
        # Notion found a blank Control Tower with no way to know whether the
        # company had no state or the tool had eaten it.
        #
        # Re-raised, not handled: the failure is real and the caller must
        # still fail. What is added is the one fact the caller could not
        # have known.
        raise ControlTowerPageError(
            f"페이지 본문을 다시 쓰던 중 실패했다 — 기존 블록 {archived}개는 이미 "
            f"보관됐으므로 지금 이 페이지는 **비어 있다**. 원인을 해결한 뒤 "
            f"publish_control_tower.py를 다시 실행하면 복구된다. 원인: {exc}"
        ) from exc

    # Retrieved rather than constructed. The address is needed for the links
    # the row pages carry, and `create_child_page()` only hands it back on
    # creation — every later run takes this branch, which is every run after
    # the first. Best effort: a page that renders is worth more than a link.
    try:
        url = transport.retrieve_page(existing_id).get("url")
    except Exception:  # noqa: BLE001
        url = None

    return PublishResult(
        page_id=existing_id,
        url=url,
        created=False,
        blocks_written=len(blocks),
        blocks_archived=archived,
        title=title,
        warnings=tuple(warnings),
    )
