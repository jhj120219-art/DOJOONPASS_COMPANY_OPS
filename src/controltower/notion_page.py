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

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from oplog import one_line, redact

from .attention import rank as _attention_rank
from .attention import severity as _attention_severity
from .attention import tally as _attention_tally

# ------------------------------------------------------------------ limits

#: The idle threshold `ops_status.py` uses for a silent Desktop.
#:
#: Restated here rather than imported, and that is a compromise worth
#: naming: `ops_status.py` is an entrypoint, and a library module importing
#: one would invert the layering `LayeringInvariantTests` holds. The value is
#: pinned against its source by
#: `test_the_quiet_threshold_matches_ops_status` so the two cannot drift.
SILENT_AFTER_DAYS = 3

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
    ("RISKS", ("kind", "project_id", "team", "blocker", "days_open")),
    ("PROJECTS", ("project_id", "status", "teams", "events", "last_seen", "days_idle")),
    ("TEAMS", ("display_name", "events", "projects", "blocked_project_count", "last_seen")),
    ("DESKTOPS", ("source", "display_name", "events", "last_seen", "days_silent")),
    ("METRICS", ("label", "value", "evidence_count")),
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


def _heading(content: str, level: int = 2) -> dict[str, Any]:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": [_text(content)]}}


def _paragraph(content: str = "", *, link: str | None = None) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [_text(content, link=link)] if content else []},
    }


def _bullet(content: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [_text(content)]},
    }


def _callout(content: str, emoji: str, colour: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [_text(content)],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": colour,
        },
    }


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


def _panel_table(payload: Mapping[str, Any], panel: Mapping[str, Any], columns: Sequence[str]):
    """One panel as (blocks, warnings)."""
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []

    title = str(panel.get("title") or panel.get("key"))
    blocks.append(_heading(f"{title}  ·  {panel.get('key')}", 3))

    absence = _absence_note(payload, panel)
    if absence is not None:
        blocks.append(_callout(absence, "⚪", "gray_background"))
        return blocks, warnings

    available = list(panel.get("columns") or [])
    shown = [c for c in columns if c in available] or available[:6]
    rows = list(panel.get("rows") or [])
    total = len(rows)
    visible = rows[:MAX_TABLE_ROWS]

    table_rows = [[_fmt((r.get("values") or {}).get(c)) for c in shown] for r in visible]
    blocks.append(_table(shown, table_rows))

    if total > len(visible):
        blocks.append(
            _paragraph(
                f"위 표는 {total}건 중 {len(visible)}건만 보여준다. "
                "전체는 Dashboard(브라우저)에서 확인한다."
            )
        )
    dropped = [c for c in available if c not in shown]
    if dropped:
        blocks.append(_paragraph(f"이 표에 싣지 않은 열: {', '.join(dropped)}"))
    return blocks, warnings


def _operational_block_lines(payload: Mapping[str, Any], key: str) -> list[str]:
    """One of `ops_status.py`'s rendered blocks, as its own lines.

    `gather()` carries these beside the model: the six operational blocks,
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

    # ---- 1. overall state -------------------------------------------------
    if payload.get("model_error"):
        blocks.append(
            _callout(
                "Control Tower 모델을 만들지 못했다 — 아래 운영 지표는 비어 있거나 "
                # An exception message, which is authored text as surely
                # as a blocker is: it routinely quotes the value that broke.
                f"불완전하다: {_safe(payload['model_error'])}",
                "\U0001f6d1",
                "red_background",
            )
        )
        warnings.append("model_error present")
    elif attention:
        counts = _attention_tally(attention)
        severe = counts.get("P1", 0) + counts.get("?", 0)
        blocks.append(
            _callout(
                (
                    f"주의 {len(attention)}건 — 그중 P1 {counts.get('P1', 0)}건"
                    + (f" · 미분류 {counts['?']}건" if counts.get("?") else "")
                    + ". 아래 ATTENTION을 위에서부터 읽는다."
                )
                if severe
                else f"주의 {len(attention)}건 — 사람이 확인해야 할 항목이 있다.",
                "⚠️",
                "red_background" if severe else "orange_background",
            )
        )
    else:
        blocks.append(
            _callout(
                "ATTENTION 없음 — 자동 점검이 문제를 찾지 못했다. "
                "이것은 '확인된 항목이 없다'는 뜻이며 회사가 잘 돌아간다는 뜻은 아니다.",
                "✅",
                "green_background",
            )
        )

    # ---- 2. when, and over what ------------------------------------------
    blocks.append(_heading("이 화면의 범위", 2))
    scope = [
        f"마지막 갱신: {_fmt(payload.get('generated_at'))}",
        f"데이터 기간: {_fmt(window.get('since'))} ~ {_fmt(window.get('until'))}"
        + ("  (필터 없음 = 전체 기간)" if not (window.get("since") or window.get("until")) else ""),
        f"읽은 Event: {_fmt(model.get('events_read'))}건",
        f"증거 범위: {_fmt(coverage.get('evidence_from'))} ~ {_fmt(coverage.get('evidence_to'))}",
        f"읽을 수 없던 파일: {_fmt(coverage.get('unreadable'))}건"
        f" · 중복 파일: {_fmt(coverage.get('duplicates'))}건",
    ]
    blocks.extend(_bullet(s) for s in scope)

    if not coverage.get("history_checked"):
        blocks.append(
            _callout(
                "Company History를 아무도 확인하지 않았다 — 아래 숫자는 "
                "'빈틈 없음'을 뜻하지 않는다.",
                "⚠️",
                "yellow_background",
            )
        )
    elif not coverage.get("complete"):
        blocks.append(
            _callout(
                "증거 범위에 빈틈이 있다"
                + (
                    f" — {coverage.get('history_uncovered_from')}부터 확인되지 않았다."
                    if coverage.get("history_uncovered_from")
                    else "."
                ),
                "⚠️",
                "yellow_background",
            )
        )

    # ---- 3. is this real work, or a probe? -------------------------------
    #
    # The mission asks the page to say. The honest answer is that the system
    # cannot: `events.Event` has no field marking an Event as a probe, so
    # any label here would be this module guessing from a name. Reporting
    # the *absence of the distinction* is the same answer `UNSOURCED` gives
    # a missing layer, and it is the only one that cannot be wrong.
    blocks.append(_heading("이 데이터는 실제 업무인가", 2))
    blocks.append(
        _callout(
            "이 시스템은 구별하지 못한다. Event Schema(docs/02)에 Event가 실제 업무인지 "
            "Engineering Probe인지 표시하는 필드가 없다. 아래 Project 목록을 보고 "
            "사람이 판단해야 한다.",
            "⚪",
            "gray_background",
        )
    )
    projects_panel = next(
        (p for p in model.get("panels") or [] if p.get("key") == "PROJECTS"), None
    )
    if projects_panel and projects_panel.get("rows"):
        ids = [
            _fmt((r.get("values") or {}).get("project_id"))
            for r in projects_panel["rows"]
        ]
        blocks.append(_paragraph("현재 집계된 Project ID: " + ", ".join(ids)))

    # ---- 4. attention ----------------------------------------------------
    #
    # **Ranked, and the ranking is labelled (C129).** This was a flat bullet
    # list in the order `ops_status.py` happened to build it, so on the page
    # the whole workspace reads, "Runner가 열흘째 실행되지 않았다" sat below
    # "3일 이상 조용한 Desktop" and looked the same as it.
    #
    # `controltower/attention.py` holds the rule, shared with the browser
    # page so the two surfaces cannot rank the same list differently. The
    # prefix says which reading it is; a line the rule does not recognise is
    # `?` and sorts to the top rather than being filed as minor.
    #
    # Truncation follows the rank, not the arrival order — if only twenty of
    # forty fit, the twenty that fit must be the twenty that matter.
    blocks.append(_heading("ATTENTION", 2))
    if attention:
        counts = _attention_tally(attention)
        summary = " · ".join(
            f"{level} {counts[level]}건"
            for level in ("P1", "?", "P2")
            if counts.get(level)
        )
        blocks.append(
            _paragraph(
                f"{summary} — 심각도는 이 페이지의 분류이며 각 줄에 근거를 "
                "붙였다. Event Schema에도 Run Manifest에도 심각도 필드는 없다."
            )
        )
        for item in sorted(attention, key=_attention_rank)[:MAX_TABLE_ROWS]:
            level, why = _attention_severity(item)
            prefix = f"[{level}] " + (f"({why}) " if why else "(분류 불가) ")
            blocks.append(_bullet(prefix + _safe(item)))
        if len(attention) > MAX_TABLE_ROWS:
            blocks.append(
                _paragraph(
                    f"ATTENTION {len(attention)}건 중 심각한 {MAX_TABLE_ROWS}건만 "
                    "표시했다. 전체는 Dashboard(브라우저)에서 확인한다."
                )
            )
    else:
        blocks.append(_paragraph("없음."))

    # ---- 5. the panels ---------------------------------------------------
    blocks.append(_divider())
    panels = {p.get("key"): p for p in model.get("panels") or []}
    for key, columns in PANEL_LAYOUT:
        panel = panels.get(key)
        if panel is None:
            warnings.append(f"panel {key} missing from the model")
            continue
        panel_blocks, panel_warnings = _panel_table(payload, panel, columns)
        blocks.extend(panel_blocks)
        warnings.extend(panel_warnings)

    # ---- 6. layers this system has no source for -------------------------
    unsourced = [
        p for p in model.get("panels") or []
        if str(p.get("status")) != "SOURCED" and p.get("key") not in dict(PANEL_LAYOUT)
    ]
    if unsourced:
        blocks.append(_divider())
        blocks.append(_heading("원천이 없는 계층", 2))
        for panel in unsourced:
            blocks.append(
                _bullet(
                    f"{panel.get('title')} ({panel.get('key')}) — "
                    f"{panel.get('note') or '이 시스템에 원천이 없다.'}"
                )
            )

    # ---- 7. approvals / next work ----------------------------------------
    #
    # Deliberately not derived. "Which decision is blocking what" lives in
    # BACKLOG.md and is written by a person; inventing it from Events would
    # be this page's first fabricated fact.
    blocks.append(_divider())
    blocks.append(_heading("승인 병목 · 다음 작업", 2))
    blocks.append(
        _callout(
            "자동화하지 않는다. 승인 대기 항목과 다음 Sprint는 사람이 BACKLOG.md에 "
            "적는 판단이며, Event에서 유도하면 이 화면의 첫 번째 지어낸 사실이 된다.",
            "⚪",
            "gray_background",
        )
    )

    # ---- 7b. is the projection itself working? ---------------------------
    #
    # Two different syncs share the word, and conflating them is how an
    # operator reads "up to date" off a page that is not:
    #
    #   Runner Notion Sync   writes Event state onto the PROJECTS **rows**,
    #                        on the Runner's schedule. This is the one the
    #                        block below reports.
    #   this page's publish  rewrites this page, only when a person runs
    #                        `publish_control_tower.py`.
    #
    # The first can be broken for days while the second keeps succeeding —
    # this page would render perfectly and the row data underneath it would
    # be stale. So both timestamps are shown, and they are labelled as the
    # different things they are.
    blocks.append(_divider())
    blocks.append(_heading("동기화 상태", 2))
    blocks.append(
        _bullet(
            f"이 페이지가 쓰인 시각: {_fmt(payload.get('generated_at'))} "
            "(publish_control_tower.py 실행 시각)"
        )
    )
    sync_lines = _operational_block_lines(payload, "NOTION")
    if sync_lines:
        blocks.append(
            _paragraph("Runner의 Notion Sync — PROJECTS Row에 Event 상태를 쓰는 쪽:")
        )
        for line in sync_lines:
            blocks.append(_bullet(_safe(line)))
    else:
        blocks.append(
            _callout(
                "Runner의 Notion Sync 상태를 읽지 못했다 — 이 페이지가 최신이어도 "
                "Row 데이터는 오래됐을 수 있다.",
                "⚪",
                "gray_background",
            )
        )
    blocks.append(
        _paragraph(
            "두 시각은 다른 것이다. 이 페이지는 사람이 명령을 실행할 때 갱신되고, "
            "PROJECTS Row는 Runner가 돌 때 갱신된다 — 한쪽이 며칠 멈춰 있어도 "
            "다른 쪽은 정상으로 보인다."
        )
    )

    # ---- 8. how to reach the live Dashboard ------------------------------
    #
    # Asked for explicitly, and it is not a link — that is the honest part.
    # `dashboard_server.py` binds 127.0.0.1 and is not configurable
    # (its own docstring says why), so the address below opens the Control
    # Tower **only on the machine running the server**. A clickable link
    # here would be a link that fails for every other reader, and a page
    # that hands someone a dead link has told them something false about
    # the system.
    blocks.append(_divider())
    blocks.append(_heading("실시간 화면(Dashboard)에 가려면", 2))
    if dashboard_url:
        blocks.append(
            _callout(
                f"{dashboard_url} — 단, 이 주소는 Dashboard 서버를 켠 그 컴퓨터에서만 "
                "열린다. 127.0.0.1(loopback) 전용이며 다른 기기에서는 열리지 않는다.",
                "🖥",
                "blue_background",
            )
        )
    blocks.append(_paragraph("그 컴퓨터에서 실행: python dashboard_server.py  (종료는 Ctrl+C)"))
    blocks.append(
        _paragraph(
            "같은 사실을 터미널에서만 보려면: python ops_status.py "
            "(ATTENTION이 있으면 exit 3)"
        )
    )
    blocks.append(
        _paragraph(
            "이 Notion 페이지를 지금 상태로 갱신하려면: python publish_control_tower.py"
        )
    )

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
            f"  ·  {_fmt(values.get('days_idle'))}일째 조용함"
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
            f" · 열린 Blocker {rows_of('RISKS')}"
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
