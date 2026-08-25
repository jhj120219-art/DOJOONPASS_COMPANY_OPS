"""Control Tower Dashboard — the same facts as `ops_status.py`, in a browser.

    python dashboard_server.py            http://127.0.0.1:8765

Takes no command-line arguments, like every other tool here (`src/cli.py`
says why). The one knob is `COMPANY_OPS_DASHBOARD_PORT`, and it exists for
the one thing an operator cannot work around: another process already on the
port, which would otherwise make this tool unusable rather than
inconvenient.

Why this exists
---------------
`controltower/dashboard.py` has been the Control Tower *as data* since C48 —
panels, rows, evidence refs, coverage, and `to_payload()` for a sink that is
not a terminal. It had exactly two consumers: `ops_status.py`, which prints
it, and `controltower/notion_projection.py`, which needs a credentialled
Workspace this repository does not have (BACKLOG A-8). So the one seat that
is supposed to *watch* the company — the COO seat — could only watch it by
running a script in a console.

This is the third consumer, and deliberately the smallest one that can
exist: it takes the model `ops_status.py` already builds, and renders it as
HTML. It derives nothing, measures nothing, and writes nothing.

Read-only, and provably so
--------------------------
Only GET is answered; everything else gets 405. Nothing here opens a file for
writing, acquires a lock, or contacts Notion, so it is safe to run while a
Runner or an Agent is working — the same guarantee `ops_status.py` makes at
the top of its own docstring, for the same reason (it is the same code).

Bound to `127.0.0.1`, and that alone is not enough
--------------------------------------------------
Binding stops another machine. It does not stop a browser on this one:
DNS rebinding makes `attacker.example` resolve to 127.0.0.1, and then the
attacker's own page can read this one's body same-origin. `_ALLOWED_HOSTS`
is the other half — measured, `Host: evil.example.com` used to get the whole
page.

Bound to `127.0.0.1` and not configurable
-----------------------------------------
This page carries `blocker` text and `project_id`s a person typed on another
Desktop, plus the evidence filenames underneath them. `to_payload()` redacts
authored fields on the way out, but redaction is a net, not a wall, and a
Control Tower is a description of a company's internal state whether or not
any single string in it is a secret. A `--host` flag would make exposing all
of that a one-word decision, so there is no `--host` flag. Someone who needs
this on another machine is making a deployment decision (auth, TLS, a
reverse proxy) and should make it deliberately — BACKLOG.

Where each part of the page comes from
--------------------------------------
    상단 배지 / ATTENTION   the ATTENTION list `ops_status.py::main()` builds,
                            from the same six block renderers, in the same
                            order. Empty list <-> exit 0.
    COVERAGE / KPI / 패널   `build_dashboard(...).to_payload()` — the Dashboard
                            Model, not a second derivation of the rollup.
    운영 블록               the block renderers' own stdout, captured verbatim.

The operational half is captured as text rather than re-modelled. That is the
deliberate choice: `ops_status.py`'s COMPANY / HISTORY / LAST RUN / NOTION /
AGENT blocks have no data model between them and the screen, and inventing
one here would put a second opinion about a run on this page — the exact
failure `controltower/dashboard.py`'s own docstring exists to prevent. Text
that came out of the one renderer cannot disagree with it.

CONTROL TOWER is the one block rendered *twice*: as panels above, and as the
terminal's own text inside a collapsed `<details>`. It is not redundancy for
its own sake — it is the check that the panels and the screen say the same
thing, available to a person on the page itself.

The page does not refresh itself, and that is the decision
-----------------------------------------------------------
Every number here is a snapshot of one instant, and the failure an operator
actually suffers from a wall-mounted Control Tower is **believing a stale
screen is current** — reading 이상 없음 off a page rendered three hours ago.

Silent polling does not fix that; it moves it. A page that reloads itself
has a second failure the first one does not: when the server is gone the
reload replaces the last known state with a browser error page, so the moment
the tool breaks is the moment its history disappears. A stale snapshot whose
age is visible is strictly more useful than that.

So: no auto-reload, and the snapshot's age is on the page instead — counted
client-side from `generated_at`, ticking, and amber past `_STALE_AFTER_S`.
`새로고침` is one click and it is the operator's click. The ISO timestamp
stays in the markup, so the page still says when it was built with scripting
off — the age is an addition to it, never a replacement.

An UNSOURCED panel does not render as an empty one
--------------------------------------------------
`PanelStatus.UNSOURCED` means "this system has no source for that", and an
empty panel means "nothing happened". They render identically anywhere the
distinction is dropped, and they mean opposite things. Here an unsourced
panel gets its own card, its own colour, and its `note` — never a zero.
The same rule governs `coverage.complete`: `history_checked=False` is
"아무도 확인하지 않았다", and it is shown as a warning, never as 정상.
"""

from __future__ import annotations

import html
import io
import json
import os
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse
from contextlib import redirect_stdout
from datetime import date as date_type
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# The whole point of this module: it consumes `ops_status.py`, it does not
# reimplement it. Importing it runs no report — the module body is imports,
# constants and `def`s, and `main()` is behind `if __name__ == "__main__"`.
import ops_status  # noqa: E402

from cli import CONFIG_ERROR_EXIT, unexpected_arguments  # noqa: E402
from controltower import build_company_rollup, build_dashboard  # noqa: E402
from oplog import MAX_LOG_ERROR  # noqa: E402

DEFAULT_PORT = 8765
PORT_ENV_VAR = "COMPANY_OPS_DASHBOARD_PORT"

# When a snapshot stops being "now" on screen. Five minutes because that is
# roughly the point at which a person who walked away and came back would be
# wrong to trust the page, and because nothing here changes faster than a
# Runner does — the pipeline it reports runs daily. It is a display
# threshold and nothing is derived from it.
_STALE_AFTER_S = 300

# Serialises the stdout capture in `gather()`, and nothing else.
#
# `redirect_stdout` rebinds **process-global** `sys.stdout`, and
# `ThreadingHTTPServer` runs every request in its own thread. Two requests
# overlapping is not exotic -- a second browser tab, a reload while the first
# is still rendering, or the operator opening `/api/dashboard.json` beside
# the page.
#
# Measured on this machine before this lock existed, concurrent GETs:
#
#     2 requests   12 blocks:  2 carried another request's text, 2 blank
#     4 requests   24 blocks:  8 carried another request's text, 4 blank
#
# One section of the report printed under another section's heading, in the
# one view AGENT.md section 6 tells an operator to read first -- the "a report
# describing the wrong event" shape this project has removed twice already
# (`oplog.one_line()` for logs, C71 for the ATTENTION block).
#
# And a second symptom, which is what makes this a leak rather than a display
# bug. Each context manager restores what **it** saved, so two interleaved
# threads leave the outer one restoring a buffer the inner one installed:
#
#     4 requests   sys.stdout left as a discarded StringIO
#
# From that moment every print() in the process goes nowhere -- including
# this server's own console output -- permanently, and nothing raises.
# Measured: the probe that found this went silent mid-run and its own
# remaining results were never printed.
#
# A lock rather than an explicit stream, because the alternative is changing
# the six `ops_status` renderers to take a file argument, and consuming
# that module **unchanged** is the whole reason the operational half is
# captured text and not a second data model. A lock rather than a
# single-threaded server, because that would let one slow request stall
# every other. The cost is that concurrent requests queue, which for a
# single-operator tool is the cheap side.
_CAPTURE_LOCK = threading.Lock()

# The six blocks of `ops_status.py::main()`, in its order. CONTROL TOWER is
# marked because this page renders its panels itself and keeps the text only
# as the parity check described in the module docstring.
_BLOCKS: tuple[tuple[str, str, bool], ...] = (
    ("COMPANY", "COMPANY — Desktop 4가 수집한 Event", False),
    ("HISTORY", "HISTORY — Company Repository", False),
    ("CONTROL TOWER", "CONTROL TOWER — 터미널 출력 (패널 대조용)", True),
    ("LAST RUN", "LAST RUN — Run Manifest", False),
    ("NOTION", "NOTION — Sync / Retry Queue", False),
    ("AGENT", "AGENT — 이 머신의 Agent", False),
)

_RENDERERS = {
    "COMPANY": ops_status._print_company,
    "HISTORY": ops_status._print_history,
    "CONTROL TOWER": ops_status._print_control_tower,
    "LAST RUN": ops_status._print_last_run,
    "NOTION": ops_status._print_notion,
    "AGENT": ops_status._print_agent,
}


# --------------------------------------------------------------- gathering


def build_model_payload(
    now: datetime,
    *,
    since: date_type | None = None,
    until: date_type | None = None,
) -> dict[str, Any]:
    """The Dashboard Model for this instant, as `to_payload()` gives it.

    The three lines below are `_print_control_tower()`'s own three, in its
    order and with its arguments — including `with_history_coverage()`, which
    is not optional decoration. Without it `history_checked` stays False and
    `coverage.complete` is False for a perfectly healthy tree; with it, False
    means what it says. Skipping it would have made this page's one
    honesty-critical field permanently pessimistic, which is the same
    "nobody asked" reported as an answer that C68 removed one level down.
    """
    rollup = build_company_rollup(
        processed_dir=ops_status.RUNTIME_DIR / "events" / "processed",
        now=now,
        # Bounded by the Event's own work date, not by when the file arrived
        # — `build_company_rollup()` states why (docs/06 section 12): arrival
        # would put a Desktop that was switched off for a week in the wrong
        # period.
        since=since,
        until=until,
    )
    model = build_dashboard(rollup, now=now)
    older, history_readable = ops_status._company_history_older_than_the_evidence(
        ops_status.RUNTIME_DIR / "local_master" / "daily",
        ops_status._event_day(model.coverage.evidence_from),
    )
    return model.with_history_coverage(older, checked=history_readable).to_payload()


def gather(
    now: datetime,
    *,
    since: date_type | None = None,
    until: date_type | None = None,
) -> dict[str, Any]:
    """Everything the page shows, for one instant.

    Every block is run through `ops_status._block()`, which is what turns a
    damaged tree into a reported section rather than a traceback — the page
    inherits that guarantee by using it rather than calling the renderers
    directly.

    The stdout capture is serialised (`_CAPTURE_LOCK`) and the model
    build deliberately is not: the lock exists for one process-global --
    `sys.stdout` -- and holding it over pure computation would queue
    requests for nothing.

    The model is built inside its own `try`. A failure here must not blank
    the operational blocks: the day the Control Tower model raises is a day
    an operator still needs LAST RUN and AGENT, and a page that shows nothing
    because one panel could not be built is a page that hides seven working
    ones.
    """
    started = time.perf_counter()
    blocks: list[dict[str, Any]] = []
    attention: list[str] = []
    # Held across all six blocks rather than re-taken per block: the gap
    # between two blocks is a gap in which another thread can install its own
    # buffer, and the restore-what-I-saved defect needs only that.
    with _CAPTURE_LOCK:
        for key, title, parity in _BLOCKS:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                items = ops_status._block(key, _RENDERERS[key], now)
            attention.extend(items)
            blocks.append(
                {
                    "key": key,
                    "title": title,
                    "parity": parity,
                    "text": buffer.getvalue().rstrip("\n"),
                    "attention": len(items),
                }
            )

    payload: dict[str, Any] | None = None
    model_error: str | None = None
    try:
        payload = build_model_payload(now, since=since, until=until)
    except Exception:  # noqa: BLE001 — reported on the page, never swallowed
        model_error = traceback.format_exc()

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        # The window the *panels* cover. Carried out of `gather()` rather
        # than read off the model, because the operational blocks below are
        # **not** filtered — `ops_status.py` has no period concept and this
        # module consumes it unchanged — and the page has to be able to say
        # which half of itself the window applies to.
        "window": {
            "since": since.isoformat() if since is not None else None,
            "until": until.isoformat() if until is not None else None,
        },
        # How long this page cost to build. Reported rather than hidden:
        # the cost is linear in the number of Event files, and a page that
        # takes seconds with nothing said is a page an operator reads as
        # hung. A number turns "is this broken" into "there is a lot of
        # evidence".
        #
        # Measured on this machine, warm (every file already read once):
        #
        #        200 events     78 ms          2,000 events    630 ms
        #     10,000 events  3,130 ms          ~310 us per Event, linear
        #
        # and the page itself stays flat at ~96 KB, because the panels are
        # capped (`rollup.RECENT_LIMIT`). What grows is the reading.
        #
        # Split at 2,000, warm: HISTORY 272 ms, COMPANY 134, this model 111,
        # CONTROL TOWER 106, the other three ~0. No block dominates -- the
        # cost tracks **passes over `processed/`**, of which one page makes
        # five: HISTORY 2, COMPANY 1, CONTROL TOWER 1, and 1 for the panels
        # here. Four of the five are `ops_status.py` reading its own
        # evidence and a terminal run pays them too.
        #
        # A **cold** first read of a tree nobody has touched costs about
        # three times that (10,000 events: 9,389 ms vs 3,130 ms warm). That
        # is the filesystem, not this code -- measured by reversing the
        # block order, which moved the whole penalty onto whichever block
        # read first (CONTROL TOWER went 106 ms -> 4,660 ms by being moved
        # to the front). An earlier note here blamed the COMPANY block for
        # three quarters of the time; that was this artefact, and COMPANY is
        # a fifth of it.
        "build_ms": round((time.perf_counter() - started) * 1000),
        "attention": [ops_status.one_line(item) for item in attention],
        "blocks": blocks,
        "model": payload,
        "model_error": model_error,
    }


# ---------------------------------------------------------------- HTML bits

_STATUS_CLASS = {"SOURCED": "ok", "UNSOURCED": "unsourced"}

# Columns whose value is a state word the eye should be able to find without
# reading. Everything else renders plain — colouring every cell colours none.
_STATE_CLASS = {
    "BLOCKED": "bad",
    "CANCELLED": "warn",
    "COMPLETE": "ok",
    "ACTIVE": "neutral",
    "OPEN_BLOCKER": "bad",
    "ROLE_MISMATCH": "warn",
    "DUPLICATE_EVENT": "warn",
}

_PANEL_ORDER = (
    "METRICS",
    "RISKS",
    "PROJECTS",
    "TEAMS",
    "DESKTOPS",
    "ACTIVITY",
    "COMPLETIONS",
    "COMPANY_GOALS",
    "SPRINTS",
    "JUDGEMENTS",
)

_COLUMN_LABELS = {
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


# How much of one authored string a table cell shows.
#
# `to_payload()` applies `one_line()` and `redact()` to every authored value
# and deliberately does **not** bound the length: it is the faithful record
# of what the Event said, and a Notion projection consuming it needs the
# whole string (Notion truncates at 2,000 itself). A rendered page is the
# other case — it is a thing a person downloads.
#
# Nothing bounds `blocker`, `summary` or `project_id`: docs/02 gives them no
# maximum and `validate_event()` only type-checks. Measured, three blocked
# Projects each carrying a 100,000-character `blocker`: the blob reached the
# page **nine** times — a RISKS row, a PROJECTS row and an ATTENTION line
# each — for a 0.89 MB page. One Event with a 1,000,000-character blocker
# made a 1.98 MB one.
#
# `MAX_LOG_ERROR` (600) rather than a number invented here: it is the cap
# this project already chose for the same shape, in `oplog.bounded()`. The
# cut is announced rather than silent, and it names the true length — the
# `evidence_truncated` rule, applied to a value instead of a list. The whole
# string stays one click away in the evidence file the same row names, which
# is `one_line()`'s own argument for escaping rather than stripping.
_CELL_CHARS = MAX_LOG_ERROR


# Characters that occupy no width and therefore make two different strings
# look like one.
#
# `oplog.one_line()` escapes what can end **or reorder** a line — newlines,
# the separators `str.splitlines()` breaks on, and the bidi controls. By that
# function's own stated contract a zero-width space is out of scope: it ends
# nothing and reorders nothing. On a *log line* that is right.
#
# On a table it is not. Measured: two Events whose `project_id` differs only
# by U+200B produce two PROJECTS rows, one reading `SEARCH_BACKEND` with 3
# Events and one reading `SEARCH_BACKEND` with 1 — the same name twice, and
# nothing on the page saying why. An operator cannot tell which row is the
# project they mean, and "the same thing appears twice with different
# numbers" is the exact reading this whole page is built to prevent.
#
# Revealed rather than stripped, which is `one_line()`'s own rule and for its
# own reason: the real value stays recoverable, so the row still names
# something a person can search the evidence file for. Category `Cf` is the
# rule rather than a hand-written list — it is what Unicode calls a format
# character, and a list would go stale the day one is added.
def _reveal_invisible(text: str) -> str:
    if not any(unicodedata.category(char) == "Cf" for char in text):
        return text
    return "".join(
        f"<U+{ord(char):04X}>" if unicodedata.category(char) == "Cf" else char
        for char in text
    )


def _clip(text: str) -> tuple[str, int | None]:
    """`(what to show, the true length if it was cut)`."""
    if len(text) <= _CELL_CHARS:
        return text, None
    return text[:_CELL_CHARS], len(text)


def _e(value: Any) -> str:
    """One value, escaped, with `None` and empty rendered as an em-dash.

    `None` is shown as `—` rather than as `None` or as blank: blank reads as
    "nothing to say", and this project spends a lot of effort keeping "no
    value" distinguishable from "zero".
    """
    if value is None or value == "":
        return "<span class='nil'>—</span>"
    if value is True:
        return "예"
    if value is False:
        return "아니오"
    if isinstance(value, (list, tuple)):
        if not value:
            return "<span class='nil'>—</span>"
        value = ", ".join(str(item) for item in value)
    shown, full = _clip(_reveal_invisible(str(value)))
    if full is None:
        return html.escape(shown)
    return (
        html.escape(shown)
        + f"<span class='clip'>… 앞 {_CELL_CHARS:,}자만 "
        f"표시 (총 {full:,}자) — 원문은 "
        "이 행의 증거 파일에</span>"
    )


def _evidence_cell(row: Mapping[str, Any]) -> str:
    """The provenance of one row, openable.

    `evidence_count` is the true total and the list is capped at
    `EVIDENCE_IN_PAYLOAD`, so a truncated list says so — a page that showed
    five of forty without a word would make "증거 5건" a wrong number rather
    than a short one.
    """
    refs = row.get("evidence") or []
    total = row.get("evidence_count", 0)
    if not total:
        return "<td class='ev'><span class='nil'>증거 없음</span></td>"
    lines = "".join(
        f"<li><code>{html.escape(str(ref.get('event_id')))}</code>"
        f"<span class='ev-at'>{html.escape(str(ref.get('at')))}</span>"
        f"<span class='ev-path'>{html.escape(str(ref.get('path')))}</span></li>"
        for ref in refs
    )
    more = ""
    if row.get("evidence_truncated"):
        more = f"<li class='ev-more'>… 총 {total}건 중 {len(refs)}건만 표시</li>"
    return (
        f"<td class='ev'><details><summary>{total}건</summary>"
        f"<ul class='ev-list'>{lines}{more}</ul></details></td>"
    )


# Two columns of the Desktop panel that are read as a verdict, not as data.
#
# `days_silent` is a number the reader has to compare against a threshold
# they are holding in their head, and `has_activity` is the difference
# between a Desktop that went quiet and one that has **never** reported at
# all. Measured on a mixed fleet (one reporting today, one at 20 days, one
# never seen): every cell rendered in the same weight, and the only thing
# separating them was the ATTENTION list further up the page.
#
# The same reasoning, and the same threshold, as the evidence-age tile:
# `SILENT_AFTER_DAYS` is already this project's answer to "how long is too
# long without a report". Nothing is invented and no other column is
# touched — colouring every number colours none.
# No `isinstance(value, bool)` guard on the day count, and that is a measured
# decision rather than an oversight. The first draft had one -- `True` is `1`
# in Python, and the mistake is a classic. A mutation removing it changed no
# outcome and failed no test: a bool is 0 or 1, `SILENT_AFTER_DAYS` is 3, and
# `days_silent` is `int | None` by the panel's own column contract. A guard
# that cannot fire is a guard nobody can justify, which is what this project
# removed from `DashboardPanel` for the same reason.
def _verdict_class(column: str, value: Any) -> str | None:
    if column == "days_silent" and isinstance(value, int):
        return "warn" if value > ops_status.SILENT_AFTER_DAYS else None
    if column == "has_activity" and value is False:
        return "warn"
    return None


def _cell(column: str, value: Any) -> str:
    state = _STATE_CLASS.get(str(value)) or _verdict_class(column, value)
    cls = f" class='state {state}'" if state else ""
    return f"<td{cls}>{_e(value)}</td>"


def _panel_html(panel: Mapping[str, Any]) -> str:
    status = str(panel.get("status"))
    cls = _STATUS_CLASS.get(status, "neutral")
    rows = panel.get("rows") or []
    head = (
        f"<div class='panel {cls}'>"
        f"<div class='panel-head'>"
        f"<h3>{html.escape(str(panel.get('title')))}"
        f"<span class='pkey'>{html.escape(str(panel.get('key')))}</span></h3>"
        f"<span class='badge {cls}'>{html.escape(status)}</span></div>"
    )

    if status == "UNSOURCED":
        # No table, no zero, no "0건". The note is the whole content, and the
        # layers it accounts for are named so the card cannot be read as an
        # empty panel that happens to have a comment on it.
        layers = ", ".join(panel.get("unsourced_layers") or [])
        return (
            head
            + "<p class='unsourced-line'>이 시스템에는 이 계층의 <b>원천이 없다</b>. "
            "비어 있는 것이 아니라 물어볼 곳이 없다.</p>"
            + (f"<p class='layers'>해당 계층: <code>{html.escape(layers)}</code></p>" if layers else "")
            + f"<p class='note'>{html.escape(str(panel.get('note') or ''))}</p>"
            + "</div>"
        )

    body = ""
    if not rows:
        body = (
            "<p class='empty'>해당 없음 — 이 기간의 증거에 이 항목이 "
            "<b>하나도 없었다</b>. (원천은 있다)</p>"
        )
    else:
        columns = list(panel.get("columns") or [])
        header = "".join(
            f"<th>{html.escape(_COLUMN_LABELS.get(c, c))}</th>" for c in columns
        )
        lines = []
        for row in rows:
            values = row.get("values") or {}
            cells = "".join(_cell(c, values.get(c)) for c in columns)
            lines.append(f"<tr>{cells}{_evidence_cell(row)}</tr>")
        body = (
            "<div class='scroll'><table><thead><tr>"
            f"{header}<th>증거</th></tr></thead><tbody>"
            + "".join(lines)
            + "</tbody></table></div>"
        )

    note = panel.get("note")
    note_html = f"<p class='note'>{html.escape(str(note))}</p>" if note else ""
    source = panel.get("source")
    src_html = (
        f"<p class='source'>출처: {html.escape(str(source))}</p>" if source else ""
    )
    return head + body + note_html + src_html + "</div>"


def _kpi_html(panel: Mapping[str, Any] | None) -> str:
    """KPI tiles, each carrying the file count it was counted from.

    A number with no evidence is marked rather than hidden. `Metric` declares
    that an untraceable number is a rumour; a tile that looked the same
    whether it cited 14 files or none would undo that declaration on the one
    surface a person actually reads.
    """
    if panel is None:
        return ""
    tiles = []
    for row in panel.get("rows") or []:
        values = row.get("values") or {}
        count = row.get("evidence_count", 0)
        value = values.get("value")
        tone = "zero" if value == 0 else "live"
        cite = (
            f"<span class='cite'>증거 {count}건</span>"
            if count
            else "<span class='cite none'>증거 파일 없음</span>"
        )
        tiles.append(
            f"<div class='kpi {tone}'>"
            f"<div class='kpi-value'>{_e(value)}</div>"
            f"<div class='kpi-label'>{_e(values.get('label'))}</div>"
            f"{cite}"
            f"<div class='kpi-src' title='{html.escape(str(values.get('derived_from') or ''))}'>"
            f"{_e(values.get('derived_from'))}</div>"
            "</div>"
        )
    return f"<section class='kpis'>{''.join(tiles)}</section>"


def _evidence_age_days(model: Mapping[str, Any]) -> int | None:
    """How many days ago the newest Event in this corpus was written.

    `None` when there is no evidence, or when either end of the comparison
    cannot be parsed — a number that could not be computed must not be shown
    as a number, which is the same rule `history_checked` follows one layer
    down.
    """
    newest = (model.get("coverage") or {}).get("evidence_to")
    if not newest:
        return None
    try:
        then = date_type.fromisoformat(str(newest))
        now = datetime.fromisoformat(str(model.get("generated_at"))).date()
    except (TypeError, ValueError):
        return None
    return max(0, (now - then).days)


def _coverage_html(model: Mapping[str, Any]) -> str:
    """What the numbers cover — and, when they do not, that they do not.

    Three separate reasons collapse into `complete=False` and the strip
    names each one, because "일부 파일을 읽지 못했다" and "Company History를
    확인하지 못했다" call for different actions from the person reading.
    """
    cov = model.get("coverage") or {}
    # How old the newest Event is, said as a number of days rather than left
    # as a date the reader has to subtract from today.
    #
    # The page already applies this reasoning to itself (`_AGE_SCRIPT`: nobody
    # subtracts two timestamps at a glance) and it matters more here. That
    # badge says when the **page** was built; this says when the **company**
    # last reported, and every KPI above is a count over exactly this range.
    #
    # Measured on the deployment tree: `증거 범위 2026-08-05 ~ 2026-08-10`
    # over `완료된 Milestone 14` and `완전`, on 2026-08-25. Every word true,
    # and the newest evidence fifteen days old with nothing on the numbers
    # saying so. The ATTENTION block did say all four Desktops were silent —
    # in a list of eight items, detached from the counts it qualifies.
    #
    # `SILENT_AFTER_DAYS` rather than a threshold invented here: it is this
    # project's own answer to "how long is too long without a report", and
    # the tile turns amber exactly when the Desktop that reported most
    # recently has crossed it.
    age = _evidence_age_days(model)
    complete = bool(cov.get("complete"))
    checked = bool(cov.get("history_checked"))
    unreadable = cov.get("unreadable") or 0
    duplicates = cov.get("duplicates") or 0

    reasons = []
    if unreadable:
        reasons.append(
            f"읽지 못한 증거 파일 {unreadable}건 — 아래 숫자는 그만큼 적다"
        )
    if not checked:
        reasons.append(
            "Company History를 확인하지 못했다 — 증거 범위 밖의 과거 작업이 "
            "있는지 <b>판정하지 못했다</b>"
        )
    if cov.get("history_uncovered_from"):
        reasons.append(
            f"증거보다 오래된 Company History가 있다 "
            f"({html.escape(str(cov['history_uncovered_from']))} 이전) — "
            "그 기간의 Event는 이 화면에 없다"
        )

    # `complete` answers "are there known gaps in what was read", and with an
    # empty `processed/` the honest answer to that is yes — nothing failed to
    # be read. Rendered as the green all-clear it becomes a different and
    # false sentence: "이 화면의 숫자는 증거 전체를 덮는다" over a screen whose
    # every number is 0, which reads as 아무 일도 없었다 rather than 읽을 것이
    # 없었다. Measured: the empty-tree fault injection produced exactly that
    # screen — green banner, nine zero KPIs, `증거 범위 — ~ —`.
    #
    # So emptiness gets its own banner ahead of both. The model is not wrong
    # and is not second-guessed here; this is the renderer refusing to state
    # a true field in a way that answers a question nobody asked.
    windowed = bool(model.get("since") or model.get("until"))
    if not model.get("events_read"):
        # Two different facts, and wiring the period filter is what made them
        # differ. With no window, zero Events means `processed/` is empty.
        # With a window, it means **this window** is empty and the directory
        # may be full — measured: 2026-08-20..25 over the deployment tree
        # gives `events_read` 0 beside sixteen files on disk. Saying "the
        # directory is empty" there would be a false sentence produced by a
        # true field, which is the failure this banner exists to prevent.
        cause = (
            "이 <b>기간</b>에 Event가 없다 — 다른 기간에는 있을 수 있다"
            if windowed
            else "<code>runtime/events/processed/</code> 가 비어 있다"
        )
        banner = (
            "<div class='cov-verdict warn'>"
            + ("이 기간에는 증거가 <b>하나도 없다</b>" if windowed
               else "증거가 <b>하나도 없다</b>")
            + "<ul><li>아래의 0은 '일이 없었다'가 아니라 "
            "<b>'셀 Event가 없다'</b>는 뜻이다 — " + cause + "</li>"
            + ("".join(f"<li>{r}</li>" for r in reasons))
            + "</ul></div>"
        )
    elif complete:
        # "증거 전체" means "everything that was read", and with a window on
        # that is the window rather than the corpus. Unqualified, a filtered
        # reader can take the green banner as "this covers everything the
        # company did" — the same true-field-wrong-sentence shape the empty
        # banner two branches up exists to prevent.
        banner = (
            "<div class='cov-verdict ok'>이 화면의 숫자는 "
            + ("이 <b>기간</b>의 증거 전체를 덮는다" if windowed else "증거 전체를 덮는다")
            + "</div>"
        )
    else:
        banner = (
            "<div class='cov-verdict warn'>이 화면의 숫자는 <b>전부가 아니다</b>"
            + (
                "<ul>" + "".join(f"<li>{r}</li>" for r in reasons) + "</ul>"
                if reasons
                else ""
            )
            + "</div>"
        )

    def item(label: str, value: str, tone: str = "") -> str:
        return (
            f"<div class='cov-item {tone}'><span class='cov-l'>{label}</span>"
            f"<span class='cov-v'>{value}</span></div>"
        )

    unreadable_list = ""
    if model.get("unreadable"):
        rows = "".join(
            f"<li><code>{html.escape(str(u.get('file')))}</code> — "
            f"{html.escape(str(u.get('reason')))}</li>"
            for u in model["unreadable"]
        )
        unreadable_list = (
            f"<details class='unreadable'><summary>읽지 못한 파일 "
            f"{len(model['unreadable'])}건</summary><ul>{rows}</ul></details>"
        )

    return (
        "<section class='coverage'>"
        "<h2>데이터 Coverage</h2>"
        + banner
        + "<div class='cov-grid'>"
        + item("집계한 Event", f"{model.get('events_read', 0)}건")
        + item(
            "증거 범위",
            f"{_e(cov.get('evidence_from'))} ~ {_e(cov.get('evidence_to'))}"
            + (
                ""
                if age is None
                else "<span class='age-note'>"
                + ("이 기간의 " if windowed else "")
                + f"마지막 증거 {age}일 전</span>"
            ),
            "warn" if age is not None and age > ops_status.SILENT_AFTER_DAYS else "",
        )
        + item("읽지 못한 파일", f"{unreadable}건", "bad" if unreadable else "")
        + item("중복 파일", f"{duplicates}건", "warn" if duplicates else "")
        + item(
            "Company History 확인",
            "확인함" if checked else "확인 못 함",
            "" if checked else "warn",
        )
        + item(
            "완전성",
            "완전"
            if complete and model.get("events_read")
            else ("증거 없음" if not model.get("events_read") else "불완전"),
            "" if complete and model.get("events_read") else "warn",
        )
        + "</div>"
        + unreadable_list
        + "</section>"
    )


def _window_html(window: Mapping[str, Any] | None) -> str:
    """The period control, and the sentence that keeps it honest.

    The window bounds the **panels** and nothing else. The operational blocks
    lower down are `ops_status.py`'s own output and that module has no period
    concept — this one consumes it unchanged, which is the whole reason the
    operational half is captured text rather than a second data model. A page
    showing `2026-08-01 ~ 2026-08-07` above a LAST RUN block describing this
    morning, with nothing saying which is which, would be one screen making
    two different claims about "when".
    """
    window = window or {}
    since = window.get("since") or ""
    until = window.get("until") or ""
    active = bool(since or until)
    scope = (
        "<p class='sub'>이 기간은 <b>위쪽 KPI·패널에만</b> 적용된다. 아래 "
        "‘운영 상태’ 블록은 <code>ops_status.py</code>의 출력 그대로이며 "
        "기간 개념이 없다 — 언제나 현재 상태다.</p>"
        if active
        else "<p class='sub'>기간을 비워 두면 전체 기간이다. Event의 "
        "<b>작업일</b> 기준으로 자른다(도착일이 아니라).</p>"
    )
    reset = (
        " <a class='reset' href='/'>전체 기간으로</a>" if active else ""
    )
    return (
        "<section class='window'>"
        + (
            f"<h2>기간 — {html.escape(since or '처음')} ~ "
            f"{html.escape(until or '지금')}</h2>"
            if active
            else "<h2>기간 — 전체</h2>"
        )
        + "<form method='get' action='/'>"
        f"<label>since <input type='date' name='since' value='{html.escape(since)}'></label>"
        f"<label>until <input type='date' name='until' value='{html.escape(until)}'></label>"
        "<button type='submit'>적용</button>"
        + reset
        + "</form>"
        + scope
        + "</section>"
    )


def _attention_html(attention: Sequence[str]) -> str:
    if not attention:
        return (
            "<section class='attention clear'><h2>ATTENTION</h2>"
            "<p>없음 — 사람이 지금 할 일은 없다. "
            "<span class='sub'>(ops_status exit 0에 해당)</span></p></section>"
        )
    items = "".join(f"<li>{html.escape(item)}</li>" for item in attention)
    return (
        f"<section class='attention'><h2>ATTENTION — {len(attention)}건</h2>"
        f"<p class='sub'>사람이 지금 확인해야 하는 것. (ops_status exit 3에 해당)</p>"
        f"<ol>{items}</ol></section>"
    )


def _blocks_html(blocks: Sequence[Mapping[str, Any]]) -> str:
    parts = []
    for block in blocks:
        text = html.escape(block.get("text") or "")
        count = block.get("attention") or 0
        flag = (
            f"<span class='badge bad'>ATTENTION {count}</span>"
            if count
            else "<span class='badge ok'>이상 없음</span>"
        )
        if block.get("parity"):
            parts.append(
                "<details class='opsblock parity'><summary>"
                f"{html.escape(str(block['title']))} {flag}</summary>"
                f"<pre>{text}</pre></details>"
            )
        else:
            parts.append(
                f"<div class='opsblock'><div class='panel-head'>"
                f"<h3>{html.escape(str(block['title']))}</h3>{flag}</div>"
                f"<pre>{text}</pre></div>"
            )
    return (
        "<section class='ops' id='ops'><h2>운영 상태</h2>"
        f"{''.join(parts)}</section>"
    )


_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0d1117;color:#e6edf3;
 font-family:"Segoe UI","Malgun Gothic",system-ui,sans-serif;font-size:14px;line-height:1.5}
a{color:#58a6ff}
code{font-family:Consolas,"Courier New",monospace;font-size:12px;color:#a5d6ff}
header{position:sticky;top:0;z-index:5;background:#161b22;border-bottom:1px solid #30363d;
 padding:14px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
header h1{margin:0;font-size:18px;letter-spacing:.5px}
header .meta{color:#8b949e;font-size:12px}
.verdict{margin-left:auto;padding:8px 16px;border-radius:6px;font-weight:700;font-size:14px}
.verdict.bad{background:#4a1418;color:#ff9d9d;border:1px solid #8b2b32}
.verdict.ok{background:#12261a;color:#7ee787;border:1px solid #2b6a3b}
main{padding:20px 24px 60px;max-width:1500px;margin:0 auto}
h2{font-size:15px;text-transform:uppercase;letter-spacing:1px;color:#8b949e;
 margin:28px 0 10px;border-bottom:1px solid #21262d;padding-bottom:6px}
section:first-of-type h2{margin-top:0}
.attention{background:#2d1113;border:1px solid #8b2b32;border-radius:8px;padding:14px 18px}
.attention h2{color:#ff9d9d;border:0;margin:0 0 6px}
.attention ol{margin:8px 0 0;padding-left:22px}
.attention li{margin:6px 0;color:#ffd7d5}
.attention.clear{background:#12261a;border-color:#2b6a3b}
.attention.clear h2{color:#7ee787}
.sub{color:#8b949e;font-size:12px;margin:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
.kpi{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px}
.kpi-value{font-size:28px;font-weight:700;line-height:1.1}
.kpi.zero .kpi-value{color:#6e7681}
.kpi-label{font-size:13px;color:#e6edf3;margin-top:2px}
.cite{display:inline-block;margin-top:6px;font-size:11px;padding:1px 7px;border-radius:9px;
 background:#1f3a24;color:#7ee787}
.cite.none{background:#2a2113;color:#e3b341}
.kpi-src{font-size:11px;color:#6e7681;margin-top:6px;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
.coverage{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px}
.coverage h2{margin-top:0}
.cov-verdict{padding:8px 12px;border-radius:6px;margin-bottom:12px;font-weight:600}
.cov-verdict.ok{background:#12261a;color:#7ee787;border:1px solid #2b6a3b}
.cov-verdict.warn{background:#2a2113;color:#e3b341;border:1px solid #7a5c11}
.cov-verdict ul{margin:8px 0 0;padding-left:20px;font-weight:400;color:#f0d9a0}
.cov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.cov-item{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:8px 10px;
 display:flex;flex-direction:column;gap:2px}
.cov-item.warn{border-color:#7a5c11;background:#1c1710}
.cov-item.bad{border-color:#8b2b32;background:#1d1113}
.cov-l{font-size:11px;color:#8b949e}
.cov-v{font-size:15px;font-weight:600}
.panel,.opsblock{background:#161b22;border:1px solid #30363d;border-radius:8px;
 padding:12px 16px;margin-bottom:12px}
.panel.unsourced{background:#15161a;border-style:dashed;border-color:#484f58}
.panel-head{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.panel-head h3{margin:0;font-size:14px;display:flex;align-items:baseline;gap:8px}
.pkey{font-size:11px;color:#6e7681;font-family:Consolas,monospace}
.badge{margin-left:auto;font-size:11px;padding:2px 9px;border-radius:10px;
 border:1px solid #30363d;color:#8b949e;white-space:nowrap}
.badge.ok{background:#12261a;color:#7ee787;border-color:#2b6a3b}
.badge.unsourced{background:#21262d;color:#c9a0dc;border-color:#553a68}
.badge.bad{background:#4a1418;color:#ff9d9d;border-color:#8b2b32}
.unsourced-line{color:#c9a0dc;margin:4px 0}
.layers{margin:4px 0;font-size:12px;color:#8b949e}
.note{color:#8b949e;font-size:12px;margin:8px 0 0}
.source{color:#6e7681;font-size:11px;margin:6px 0 0;font-style:italic}
.empty{color:#e3b341;margin:6px 0}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{text-align:left;color:#8b949e;font-weight:600;padding:6px 10px;
 border-bottom:1px solid #30363d;white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid #21262d;vertical-align:top}
tr:hover td{background:#1c2129}
.nil{color:#6e7681}
.state{font-weight:700}
.state.bad{color:#ff7b72}
.state.warn{color:#e3b341}
.state.ok{color:#7ee787}
.state.neutral{color:#79c0ff}
.ev details{cursor:pointer}
.ev summary{color:#58a6ff;font-size:11.5px}
.ev-list{margin:6px 0 0;padding-left:16px}
.ev-list li{margin:3px 0;font-size:11px;color:#8b949e}
.ev-at{margin-left:8px;color:#6e7681}
.ev-path{margin-left:8px;color:#7ee787}
.ev-more{color:#e3b341}
.clip{color:#e3b341;font-size:11px;margin-left:4px}
.age-note{display:block;font-size:11px;color:#8b949e;font-weight:400}
.window{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 18px;
 margin-top:16px}
.window h2{margin:0 0 8px}
.window form{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.window label{font-size:12px;color:#8b949e;display:flex;gap:6px;align-items:center}
.window input{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px;
 padding:4px 8px;font-family:inherit}
.window button{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:5px;
 padding:5px 14px;cursor:pointer}
.window button:hover{background:#30363d}
.reset{font-size:12px}
pre{margin:0;white-space:pre-wrap;word-break:break-word;font-size:12px;
 font-family:Consolas,"Courier New",monospace;color:#c9d1d9}
.opsblock.parity{padding:10px 16px}
.opsblock.parity summary{cursor:pointer;font-size:14px;font-weight:600;
 display:flex;align-items:center;gap:10px}
.opsblock.parity pre{margin-top:10px}
.unreadable{margin-top:10px}
.unreadable summary{color:#ff7b72;cursor:pointer}
.error{background:#2d1113;border:1px solid #8b2b32;border-radius:8px;padding:14px}
#age{margin-left:6px;padding:1px 8px;border-radius:9px;background:#21262d;color:#8b949e}
#age.stale{background:#2a2113;color:#e3b341;font-weight:600}
footer{color:#6e7681;font-size:11.5px;margin-top:32px;border-top:1px solid #21262d;
 padding-top:12px}
"""


# The only script on the page, and it reads one attribute and writes one
# element. It is `hidden` until the script runs, so a browser with scripting
# off shows the ISO timestamp alone rather than an empty badge claiming
# nothing — the same rule the panels follow about empty and unsourced.
_AGE_SCRIPT = (
    "<script>(function(){"
    "var g=document.getElementById('gen'),a=document.getElementById('age');"
    "if(!g||!a)return;"
    "var t=Date.parse(g.getAttribute('datetime'));"
    "if(isNaN(t))return;"
    "a.hidden=false;"
    "function tick(){"
    "var s=Math.max(0,Math.round((Date.now()-t)/1000));"
    "var txt=s<60?s+'초 전':(s<3600?Math.floor(s/60)+'분 전'"
    ":Math.floor(s/3600)+'시간 '+Math.floor((s%3600)/60)+'분 전');"
    "a.textContent=txt+' 기준';"
    "a.className=s>" + str(_STALE_AFTER_S) + "?'stale':'';"
    "}tick();setInterval(tick,10000);})();</script>"
)


def render_html(data: Mapping[str, Any]) -> str:
    attention = data.get("attention") or []
    model = data.get("model")
    verdict = (
        f"<div class='verdict bad'>사람 확인 필요 — ATTENTION {len(attention)}건</div>"
        if attention
        else "<div class='verdict ok'>이상 없음 — 지금 할 일 없음</div>"
    )

    if model is not None:
        panels = {p["key"]: p for p in model.get("panels") or []}
        ordered = [panels[k] for k in _PANEL_ORDER if k in panels]
        ordered += [p for p in (model.get("panels") or []) if p["key"] not in _PANEL_ORDER]
        model_html = (
            _coverage_html(model)
            + "<h2>KPI — Control Tower</h2>"
            + _kpi_html(panels.get("METRICS"))
            + "<h2 id='panels'>패널</h2>"
            + "".join(
                _panel_html(p) for p in ordered if p["key"] != "METRICS"
            )
        )
        schema = html.escape(str(model.get("schema_version")))
    else:
        model_html = (
            "<section class='error'><h2>Control Tower Model을 만들지 못했다</h2>"
            "<p>아래 운영 블록은 그대로 유효하다. 이 화면의 패널·KPI·Coverage만 "
            "이번 요청에서 비어 있다.</p>"
            f"<pre>{html.escape(str(data.get('model_error') or ''))}</pre></section>"
        )
        schema = "—"

    generated = html.escape(str(data.get("generated_at")))
    ms = data.get("build_ms")
    build = f" · {ms}ms에 생성" if ms is not None else ""
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>DOJOONPASS Control Tower</title>"
        f"<style>{_CSS}</style></head><body>"
        "<header><h1>DOJOONPASS Control Tower</h1>"
        f"<span class='meta'><time id='gen' datetime='{generated}'>{generated}</time>"
        "<span id='age' hidden></span>"
        f" · schema {schema}"
        " · <a href='/api/dashboard.json'>JSON</a>"
        " · <a href='/'>새로고침</a>"
        f"{build}</span>"
        f"{verdict}</header><main>"
        + _attention_html(attention)
        + _window_html(data.get("window"))
        + model_html
        + _blocks_html(data.get("blocks") or [])
        + "<footer>읽기 전용. 이 화면은 아무것도 쓰지 않고, 잠그지 않고, "
        "Notion에 접속하지 않는다. 숫자의 출처는 "
        "<code>runtime/events/processed/</code>이고, 운영 블록은 "
        "<code>ops_status.py</code>의 출력 그대로다. 이 화면은 스스로 갱신하지 "
        "않는다 — 위의 경과 시간이 이 숫자들이 언제의 것인지 말한다.</footer>"
        "</main>" + _AGE_SCRIPT + "</body></html>"
    )


# ------------------------------------------------------------------ server


# The host names this server will answer to.
#
# Binding to `127.0.0.1` stops a packet from another machine. It does **not**
# stop a browser on this machine, and that is the gap DNS rebinding walks
# through: a page the operator visits resolves `attacker.example` to
# 127.0.0.1, fetches `http://attacker.example:8765/api/dashboard.json`, and
# because the browser thinks the origin is `attacker.example` the response is
# same-origin and the attacker's script can **read the body**. The body is
# this company's internal state — `project_id`s, the `blocker` sentences
# people wrote on other Desktops, Desktop names, evidence filenames.
#
# Measured before this list existed: `Host: evil.example.com` and
# `Host: internal.corp` both got 200 and the full 51 KB page.
#
# The check is the standard one and it is cheap: a rebinding attack must send
# the attacker's own hostname in `Host` (the browser sets it from the URL and
# script cannot override it), so refusing every name that is not a loopback
# name closes it. A missing `Host` is allowed — HTTP/1.0 clients omit it and
# an absent header cannot carry an attacker's name.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


class WindowError(ValueError):
    """A `?since=`/`?until=` this server will not guess at."""


def parse_window(query: str) -> tuple[date_type | None, date_type | None]:
    """`?since=YYYY-MM-DD&until=YYYY-MM-DD` -> a pair of dates.

    Every failure raises rather than falling back to the whole period, and
    that is the whole design of this function. A page that quietly ignored a
    mistyped date would show every Event the company ever recorded while the
    operator believed they were looking at one week — the numbers would be
    real, the question they answer would be the wrong one, and nothing on the
    screen would say so. `cli.unexpected_arguments()` refuses a flag it
    cannot honour for the same reason and this is the same mistake over HTTP.

    Unknown parameters are refused too. There are exactly two knobs; a third
    is a typo, and silently dropping it is the same silence.
    """
    if not query:
        return None, None
    parsed = urllib.parse.parse_qs(query, keep_blank_values=True, strict_parsing=False)
    unknown = sorted(set(parsed) - {"since", "until"})
    if unknown:
        raise WindowError(f"모르는 조건입니다: {', '.join(unknown)} (since, until 만 받습니다)")

    bounds: dict[str, date_type | None] = {"since": None, "until": None}
    for name in ("since", "until"):
        values = parsed.get(name) or []
        if len(values) > 1:
            raise WindowError(f"{name} 가 여러 번 왔습니다 — 하나만 보내세요")
        raw = (values[0] if values else "").strip()
        if not raw:
            continue
        try:
            bounds[name] = date_type.fromisoformat(raw)
        except ValueError:
            raise WindowError(f"{name}={raw!r} 는 날짜가 아닙니다 (YYYY-MM-DD)") from None

    since, until = bounds["since"], bounds["until"]
    if since is not None and until is not None and since > until:
        raise WindowError(f"기간이 거꾸로입니다: {since} 부터 {until} 까지")
    return since, until


class _Server(ThreadingHTTPServer):
    """`ThreadingHTTPServer` that refuses to be the second one on a port.

    The stdlib sets `allow_reuse_address = 1` on `HTTPServer`, and on Windows
    `SO_REUSEADDR` does **not** mean what it means on POSIX. There it relaxes
    TIME_WAIT; here it permits binding a port that is **actively in use**. So
    two of these start happily on 8765, the OS hands each new connection to
    whichever it likes, and the `except OSError` in `main()` -- the one that
    prints "port already in use, set COMPANY_OPS_DASHBOARD_PORT" -- can never
    fire on the platform this project actually runs on (AGENT.md section 2b:
    Windows Task Scheduler).

    Measured on this machine:

        ThreadingHTTPServer            second bind on 127.0.0.1:8765 -> OK
        allow_reuse_address = False    second bind -> [Errno 10048] refused

    Why it matters more than tidiness. The second instance is the one an
    operator starts after editing something, or from another checkout, and
    `RUNTIME_DIR` is resolved per process. A Control Tower answering from a
    stale process, intermittently, with nothing on the page saying which one
    replied, is the "the screen is not about the tree you think it is" shape
    this project spends its effort removing.

    The cost is stated rather than hidden: on POSIX this can make an
    immediate restart fail while the listening port is in TIME_WAIT. For a
    tool restarted by hand, refusing to start a silent duplicate is the
    cheaper error of the two, and it says so on stderr.
    """

    allow_reuse_address = False


class _Handler(BaseHTTPRequestHandler):
    server_version = "ControlTower/1.0"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This page is a snapshot of a moment. A cached one is a Control
        # Tower showing yesterday, which is worse than showing nothing.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _host_allowed(self) -> bool:
        """Whether `Host` names this machine's loopback interface."""
        header = self.headers.get("Host")
        if header is None:
            return True
        host = header.strip()
        if host.startswith("["):                      # [::1]:8765
            host = host[: host.find("]") + 1]
        elif ":" in host:                             # 127.0.0.1:8765
            host = host.rsplit(":", 1)[0]
        return host.lower() in _ALLOWED_HOSTS

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        if not self._host_allowed():
            # 403 rather than 404: refusing is the answer, and saying so is
            # what makes a misconfigured reverse proxy debuggable instead of
            # mysterious. The body names no internal state.
            self._send(
                403,
                "이 서버는 127.0.0.1 로만 접속할 수 있습니다.".encode("utf-8"),
                "text/plain; charset=utf-8",
            )
            return
        raw_path, _, query = self.path.partition("?")
        path = raw_path.rstrip("/") or "/"
        if path == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        try:
            since, until = parse_window(query)
        except WindowError as exc:
            # 400 and the reason, rather than the whole period. See
            # `parse_window()` for why silence is the worse answer.
            self._send(
                400,
                f"[거절] {exc}".encode("utf-8"),
                "text/plain; charset=utf-8",
            )
            return
        now = datetime.now().astimezone()
        try:
            data = gather(now, since=since, until=until)
        except Exception:  # noqa: BLE001
            detail = traceback.format_exc()
            self._send(
                500,
                f"<pre>{html.escape(detail)}</pre>".encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/dashboard.json":
            body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path != "/":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, render_html(data).encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        # The host check is not repeated here on purpose: these methods
        # already answer nothing but a refusal, and a rebinding attacker
        # learns strictly less from 405 than from being told the host was
        # wrong. What must never leak is the body of a GET.
        self._send(405, b"read-only", "text/plain; charset=utf-8")

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")


def main(argv: Sequence[str] = ()) -> int:
    refusal = unexpected_arguments(
        argv,
        tool="dashboard_server.py",
        # Spelled out rather than `(PORT_ENV_VAR,)`, and not by accident:
        # `EnvironmentContractTests._advertised_variables()` reads this tuple
        # out of the AST and can only see string constants. Through a name it
        # sees an empty list, and every check that this tool advertises a real
        # variable — one `.env.example` documents and something reads — would
        # pass over nothing. The constant below is the same string and
        # `test_every_variable_a_tool_advertises_is_one_something_reads`
        # is what fails if the two ever differ.
        configured_by=("COMPANY_OPS_DASHBOARD_PORT",),
    )
    if refusal is not None:
        print(f"[FAILED] {refusal}", file=sys.stderr)
        return CONFIG_ERROR_EXIT

    raw = os.environ.get(PORT_ENV_VAR, "").strip()
    if raw:
        # Refused rather than defaulted. An operator who set this set it
        # because the default port is taken, and quietly serving on the taken
        # port instead is the "did the unsafe thing and reported success"
        # shape `src/cli.py` was written about.
        try:
            port = int(raw)
        except ValueError:
            port = -1
        if not 1 <= port <= 65535:
            print(
                f"[FAILED] {PORT_ENV_VAR}={raw!r} 은(는) 포트 번호가 아닙니다 "
                "(1-65535).",
                file=sys.stderr,
            )
            return CONFIG_ERROR_EXIT
    else:
        port = DEFAULT_PORT

    try:
        server = _Server(("127.0.0.1", port), _Handler)
    except OSError as exc:
        print(
            f"[FAILED] 127.0.0.1:{port} 을(를) 열지 못했습니다: {exc}. "
            f"다른 포트를 쓰려면 {PORT_ENV_VAR} 를 설정하세요.",
            file=sys.stderr,
        )
        return CONFIG_ERROR_EXIT
    url = f"http://127.0.0.1:{port}/"
    print(f"DOJOONPASS Control Tower — {url}")
    print("읽기 전용. Ctrl+C로 종료.")
    # No `finally` around this, and the reason is a project invariant rather
    # than taste: `EveryFinallyKnowsWhatRunsBeforeItTests` holds every
    # `finally` in production code to being a lock release, so a second kind
    # would either weaken that roster or hide inside it. Nothing here needs
    # one - `serve_forever()` returns on Ctrl+C and the close below runs, and
    # on any other exception the process is ending anyway, which closes the
    # listening socket. A `finally` would buy the difference between "the OS
    # reclaims the port" and "this line reclaims it", which is nothing.
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료했다.")
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
