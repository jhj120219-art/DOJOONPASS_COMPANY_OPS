"""Control Tower Dashboard — the same facts as `ops_status.py`, in a browser.

    python dashboard_server.py            http://127.0.0.1:8765

Three paths, and nothing else (everything else is 404):

    /                     the page
    /api/dashboard.json   the same facts as JSON — `to_payload()`, verbatim
    /healthz              `ok`, for "is it up?" and nothing more

`/healthz` is answered **before** the period filter is parsed, so it stays
`200 ok` even when the query string is nonsense — a liveness check that
turned red because someone typed a bad date would be reporting on the wrong
thing. It is behind the same `Host` gate as everything else, and it costs
2 bytes and ~2ms against the page's ~25 KB and ~9ms (measured, C115), which
is the reason to point a script at it rather than at `/`.

It was undocumented until C115 — present, tested, and named nowhere a person
looks, which is the same as absent for anyone deciding how to monitor this.

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
Only GET is answered; everything else gets 405 — including HEAD, OPTIONS and
TRACE, which used to reach `BaseHTTPRequestHandler`'s own 501 because the
refusal was a hand-written roster of four method names and this sentence was
a claim about all of them (C115). A HEAD refusal carries the headers and no
body. Nothing here opens a file for
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
import re
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

# The same loaders `ops_status.py` uses, so `operational_facts()` reads the
# files rather than that module's rendering of them (C129).
from agent.status import read_status as read_agent_status  # noqa: E402
from app.runner import DEFAULT_RUN_SUMMARY_PATH  # noqa: E402
from backup.state import load_state as load_backup_state  # noqa: E402
from notion.dashboard_pending import load_pending as load_dashboard_pending  # noqa: E402
from notion.retry_queue import load_queue as load_notion_retry_queue  # noqa: E402
from oplog import bounded as oplog_bounded  # noqa: E402
from oplog import one_line as oplog_one_line  # noqa: E402
from oplog import redact as oplog_redact  # noqa: E402
from runsummary import read_summary as read_run_summary  # noqa: E402

from cli import CONFIG_ERROR_EXIT, unexpected_arguments  # noqa: E402
from controltower import build_company_rollup, build_dashboard  # noqa: E402
from controltower.attention import RANK as _ATTENTION_RANK  # noqa: E402
from controltower.attention import severity as _attention_severity  # noqa: E402
from oplog import MAX_LOG_ERROR  # noqa: E402

DEFAULT_PORT = 8765
PORT_ENV_VAR = "COMPANY_OPS_DASHBOARD_PORT"


def resolve_port(env=None):
    """The port this Dashboard will serve on, or `None` when
    `COMPANY_OPS_DASHBOARD_PORT` holds something that is not one.

    **One copy, because there are two consumers (C116).**
    `publish_control_tower.py` has to name the Dashboard's address on a Notion
    page, and its comment said it read the port "from the server module rather
    than restated here ... a second copy of either is how the page starts
    advertising an address nothing listens on". It then restated the *parsing*:

        dashboard_server.main()      int(raw), refused unless 1 <= port <= 65535
        publish_control_tower.main() raw if raw.isdigit() else str(DEFAULT_PORT)

    The two answers differ on real values, and every divergence lands on the
    Notion page — which is the surface the whole workspace reads, not this
    machine's terminal:

        COMPANY_OPS_DASHBOARD_PORT   server        the published page said
        "99999"                      refuses (1)   http://127.0.0.1:99999/
        "0"                          refuses (1)   http://127.0.0.1:0/
        "-1"                         refuses (1)   http://127.0.0.1:8765/

    The first two are the comment's own failure verbatim: an address nothing
    can ever listen on, published where a click costs a person a trip. The
    third is quieter and no better — the operator said the default port was
    unusable and the page advertised the default port.

    `None` rather than a raised error or a fallback: the two callers need
    different answers to the same fact. The server cannot start, so it
    refuses; the page publishes fine without an address, and
    `notion_page.py` guards every use of `dashboard_url` with `if
    dashboard_url:` already, so omitting it is a supported state rather than
    a hole.

    The acceptance itself is unchanged from what `main()` did before, on
    purpose. Widening or narrowing what counts as a port is a separate
    decision from making the two tools agree, and doing both at once would
    leave neither measured.
    """
    source = os.environ if env is None else env
    raw = source.get(PORT_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return port

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


def operational_facts(now: datetime) -> dict[str, Any]:
    """The Runner / Notion / Agent / Backup state, as **fields** (C129).

    Everything on this screen used to come from one of two places: the
    Dashboard Model (structured) or `ops_status.py`'s terminal output
    (prose, dropped into a `<pre>`). The nine sections this Dashboard is
    required to show include COMPANY and NOTION SYNC, and both live entirely
    in that prose — so a reader had to parse a paragraph to learn whether the
    Runner had run.

    This reads the same files `ops_status.py` reads, through the same
    loaders, and returns their fields. **It parses no prose.** A source that
    cannot be read reports `None` with a reason rather than a zero, because
    "no record" and "nothing happened" are different answers and this
    project spends a lot of effort keeping them apart.

    Read-only, like everything else here.
    """
    facts: dict[str, Any] = {}

    # --- Run Manifest: the Runner's own account of its last run.
    try:
        summary = read_run_summary(DEFAULT_RUN_SUMMARY_PATH)
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed
        facts["run"] = {"error": oplog_bounded(oplog_redact(oplog_one_line(exc)))}
        summary = None
    else:
        if summary is None:
            facts["run"] = None
        else:
            components = [
                {
                    "name": component.name,
                    "status": getattr(component.status, "value", str(component.status)),
                    "classification": (
                        getattr(component.failure, "classification", None)
                        if component.failure
                        else None
                    ),
                }
                for component in summary.components
            ]
            started = summary.started_at
            facts["run"] = {
                "started_at": started,
                "finished_at": summary.finished_at,
                "overall_status": getattr(
                    summary.overall_status, "value", str(summary.overall_status)
                ),
                "exit_code": summary.exit_code,
                "components": components,
                "days_ago": _days_between(started, now),
                "failed": [c["name"] for c in components if c["status"] == "FAILED"],
                "notion_sync": next(
                    (c["status"] for c in components if c["name"] == "notion_sync"),
                    None,
                ),
            }

    # --- Notion retry queue and the Dashboard's pending rows.
    #
    # The paths come from `ops_status.py` rather than being spelled again
    # here: it derives them per call from `RUNTIME_DIR` on purpose (its own
    # `_agent_dir()` note says why), and a second copy is how this screen
    # would start reading a different tree than the block beneath it.
    #
    # **`except Exception` around the loader only**, never around the path.
    # The first draft wrapped both and passed no path at all; every one of
    # these three came back `None`, which this screen renders as "기록 없음"
    # — a *wrong* answer that looks like a calm one, and exactly the
    # empty-versus-fine confusion the rest of this file exists to prevent.
    # Measured: the live tree has a queue of 0 and a backup from 2026-08-24,
    # and all three read `None` until the paths were passed.
    for key, loader, path in (
        ("notion_queue", load_notion_retry_queue, ops_status._notion_retry_queue_path()),
        ("notion_pending", load_dashboard_pending, ops_status._dashboard_pending_path()),
    ):
        try:
            facts[key] = len(loader(path))
        except Exception:  # noqa: BLE001
            facts[key] = None

    # --- Backup: the last time Company History left this machine.
    #
    # The key is `last_backup`, not `backup` (C129).
    # `BackupLogIsNeverPersistedTests` reports **any** call taking `"backup"`
    # as a bare positional argument, and its docstring records that the
    # breadth was accepted because it measured "zero hits across the 80
    # files this scans". `ops.get("backup")` would have been the first, and
    # the honest fix is to not make a deliberately broad security gate pay
    # for a dict key — especially when the longer name is the better one:
    # this holds the last *successful* backup, not "backup".
    try:
        backup = load_backup_state(
            ops_status.RUNTIME_DIR / "state" / "backup_state.json"
        )
    except Exception:  # noqa: BLE001
        facts["last_backup"] = None
    else:
        # `last_successful_backup`, not `..._at`. The first draft guessed the
        # name, `getattr` answered its default, and the screen said
        # "백업 기록 없음" while `backup_state.json` held a success from
        # 2026-08-24 (C129). A `getattr` default is a silent rename detector
        # that never fires, so the attribute is read directly now.
        last = backup.last_successful_backup
        facts["last_backup"] = (
            None
            if last is None
            else {
                "at": last.isoformat(timespec="seconds"),
                "days_ago": _days_between(last.isoformat(), now),
                "status": getattr(
                    backup.backup_status, "value", str(backup.backup_status)
                ),
            }
        )

    # --- This machine's Agent.
    try:
        agent = read_agent_status(now=now)
    except Exception:  # noqa: BLE001
        facts["agent"] = None
    else:
        facts["agent"] = {
            "desktop_id": agent.desktop_id,
            "last_run": agent.last_run,
            "days_ago": agent.days_since_last_run(now),
            "outbox": agent.outbox_count,
            "pending_dates": len(agent.pending_dates),
        }

    return facts


def _days_between(iso: str | None, now: datetime) -> float | None:
    """Whole-ish days from `iso` to `now`, or `None` if it will not parse."""
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    reference = now if moment.tzinfo else now.replace(tzinfo=None)
    return round((reference - moment).total_seconds() / 86400, 1)


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

    try:
        ops = operational_facts(now)
    except Exception:  # noqa: BLE001 — a fact this screen could not read must
        ops = {}       # not cost the seven sections that did read

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        # The Runner / Notion / Agent / Backup facts, as fields. See
        # `operational_facts()` for why this is not read out of the prose
        # blocks below it.
        "ops": ops,
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


#: Columns whose values are timestamps, rendered compactly.
#:
#: Measured on the live screen: PROJECTS carried four of these and ACTIVITY
#: one, each `2026-08-05T18:00:00+09:00` — **25 characters** in a table that
#: already needed sideways scrolling. The year and the offset are the same
#: on every row of a Dashboard covering days, so they are dimmed rather than
#: dropped: `08-05 18:00` is what a person reads, and the full value is in
#: the cell's `title` and in `/api/dashboard.json` untouched.
_TIME_COLUMNS = frozenset(
    {"at", "last_seen", "first_seen", "blocked_since", "completed_at"}
)

_ISO = re.compile(r"^(\d{4})-(\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2})?(.*)$")


def _timestamp_cell(value: Any) -> str | None:
    """A compact rendering of an ISO timestamp, or `None` if it is not one."""
    match = _ISO.match(str(value).strip())
    if match is None:
        return None
    year, day, clock, _rest = match.groups()
    return (
        f"<span class='ts' title='{html.escape(str(value))}'>"
        f"<span class='ts-y'>{year}-</span>{day} {clock}</span>"
    )


def _cell(column: str, value: Any) -> str:
    if column in _TIME_COLUMNS and value not in (None, ""):
        compact = _timestamp_cell(value)
        if compact is not None:
            return f"<td>{compact}</td>"
    state = _STATE_CLASS.get(str(value)) or _verdict_class(column, value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Right-aligned and tabular so a column of counts can be compared
        # down the page instead of read one row at a time.
        #
        # **After the verdict, not before.** The first draft returned here
        # first and silently dropped the colouring from every numeric
        # verdict column — `days_silent`, which is the whole point of the
        # DESKTOPS table, stopped being marked. Two tests caught it.
        cls = f"num state {state}" if state else "num"
        return f"<td class='{cls}'>{_e(value)}</td>"
    cls = f" class='state {state}'" if state else ""
    return f"<td{cls}>{_e(value)}</td>"


def _fold_constant_columns(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> tuple[list[str], list[tuple[str, Any]]]:
    """Split columns into the ones worth a grid and the ones worth one line.

    **A column whose value is identical in every row is a caption, not a
    column** (C129). Measured on the live screen before this:

        PROJECTS     16 columns, 6 of them `—` in all four rows
        ACTIVITY     12 columns, two of them (`of_total`, `truncated`)
                     literally the same value on all sixteen
        COMPLETIONS  the same two

    The cost was not the pixels — it was that the table needed sideways
    scrolling, so `Project`, `상태` and `Blocker` could not be read at the
    same time as the row they belonged to. Sixteen repetitions of `16` and
    sixteen of `아니오` were paying for that.

    Nothing is dropped: the folded columns are printed under the table with
    the value they hold, and `/api/dashboard.json` is untouched.

    Two rules keep this from hiding something real:
      * the first column is never folded — it identifies the row;
      * fewer than two rows folds nothing, because with one row *every*
        column is trivially constant and the table would vanish.
    """
    if len(rows) < 2 or len(columns) < 2:
        return list(columns), []

    kept: list[str] = []
    folded: list[tuple[str, Any]] = []
    for index, column in enumerate(columns):
        values = [(row.get("values") or {}).get(column) for row in rows]
        first = values[0]
        if index == 0 or any(value != first for value in values):
            kept.append(column)
        else:
            folded.append((column, first))
    return kept, folded


def _folded_html(folded: Sequence[tuple[str, Any]], row_count: int) -> str:
    """The folded columns, each with the one value it held."""
    if not folded:
        return ""
    parts = []
    for column, value in folded:
        label = html.escape(_COLUMN_LABELS.get(column, column))
        parts.append(f"<span class='fold'><b>{label}</b> {_e(value)}</span>")
    return (
        f"<p class='note folded'>모든 행({row_count:,}건)이 같은 값인 열은 "
        f"표에서 한 줄로 접었다: {' · '.join(parts)}</p>"
    )


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
            + f"<p class='note'>{_inline_markup(str(panel.get('note') or ''))}</p>"
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
        columns, folded = _fold_constant_columns(columns, rows)
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
            + _folded_html(folded, len(rows))
        )

    # `_inline_markup()`, not `html.escape()` (C129). These notes are written
    # in the same `**bold**` / `` `code` `` convention `ops_status.py` uses,
    # and escaping alone left the markers showing — measured on the live page
    # after the ATTENTION fix: two `**` pairs survived, both in panel notes.
    # One convention, one renderer for it.
    note = panel.get("note")
    note_html = f"<p class='note'>{_inline_markup(str(note))}</p>" if note else ""
    source = panel.get("source")
    src_html = (
        f"<p class='source'>출처: {_inline_markup(str(source))}</p>" if source else ""
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


#: How this screen sorts an ATTENTION line.
#:
#: The rule itself moved to `controltower/attention.py` in C129, because the
#: Notion page renders the same list and sits below this entrypoint — a rule
#: only one of two renderers can reach is a rule the other one will not
#: follow. What stays here is the presentation.
def attention_severity(line: str) -> tuple[str, str | None]:
    """`(P1 | P2 | ?, the phrase it matched)`. See `controltower.attention`."""
    return _attention_severity(line)


_SEVERITY_RANK = _ATTENTION_RANK


#: Where a line came from, reconstructed from `blocks[i]["attention"]`.
#:
#: Exact rather than guessed: `gather()` extends the flat list block by
#: block and records each block's count in the same pass, so the counts
#: partition the list in order. Shown because "어디서 온 경보인가" is the
#: first thing an operator needs in order to act on it.
def attention_sources(
    attention: Sequence[str], blocks: Sequence[Mapping[str, Any]]
) -> list[str | None]:
    sources: list[str | None] = []
    for block in blocks:
        count = int(block.get("attention") or 0)
        sources.extend([str(block.get("key") or "")] * count)
    if len(sources) != len(attention):
        # The partition did not add up — a caller built `attention` some
        # other way. Attribute nothing rather than attribute wrongly.
        return [None] * len(attention)
    return sources


#: Longer than this and a **P2** line gets a "전체 보기" disclosure.
#:
#: **P1 and unclassified are never folded (C130).** Measured on the rendered
#: page: two ATTENTION items were behind a disclosure, and one of them was a
#: P1 — `KEEP Candidate 1건이 저장돼 있는데 그 날짜의 Daily History에 없다`,
#: whose tail carries the only sentence saying what recovers it. An operator
#: scanning the screen for what is wrong would have read the first 150
#: characters of the most serious item and had to click for the rest.
#:
#: Folding exists so one long paragraph cannot push the other eight items
#: below the fold, and that reason applies to the ones that can afford to
#: wait. It does not apply to the ones the screen exists to show. The
#: threshold is also higher than the 150 the first version used: at the
#: rendered width a P2 line runs to about 210 characters before it costs a
#: second row of the layout.
_ATTENTION_HEAD = 210


def _inline_markup(text: str) -> str:
    """`**bold**` and `` `code` `` from an ATTENTION line, as HTML.

    `ops_status.py` writes these lines for a terminal **and** for this
    screen, and it uses the two markers throughout. Rendered raw they showed
    as literal asterisks and backticks — measured on the live page, seven of
    the nine lines carried at least one. Escaping happens first, so nothing
    here can introduce markup that was not already the author's.
    """
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _drop_unpaired(text: str) -> str:
    """Remove a marker the truncation cut in half.

    Cutting at 150 characters lands mid-`**` about as often as not, and
    `_inline_markup()` can only match pairs — so the head rendered as
    `대상은 **그 실행이 수집한…`, literal asterisks and all, which is the
    defect the markup rendering was added to remove. Measured on the live
    screen: the one 362-character line did exactly this.

    Only the trailing odd marker goes; everything already paired is left
    alone, so nothing the author balanced is changed.
    """
    for marker in ("**", "`"):
        if text.count(marker) % 2:
            text = marker.join(text.split(marker)[:-1]).rstrip()
    return text


def _attention_item(line: str, source: str | None) -> str:
    level, why = attention_severity(line)
    tag = f"<span class='sev {level.lower() if level != '?' else 'unknown'}'>{level}</span>"
    reason = f"<span class='sev-why'>{html.escape(why)}</span>" if why else (
        "<span class='sev-why'>이 화면이 분류하지 못한 줄 — 내용을 직접 읽어야 한다</span>"
    )
    origin = (
        f"<span class='sev-src'>{html.escape(source)}</span>" if source else ""
    )
    # Only a P2 may be folded. See `_ATTENTION_HEAD`.
    foldable = level == "P2" and len(line) > _ATTENTION_HEAD
    head = line[:_ATTENTION_HEAD].rstrip() if foldable else line
    body = _inline_markup(_drop_unpaired(head) if foldable else line)
    if foldable:
        body += (
            "…<details class='more'><summary>전체 보기 "
            f"({len(line):,}자)</summary><p>{_inline_markup(line)}</p></details>"
        )
    return (
        f"<li class='att {level.lower() if level != '?' else 'unknown'}'>"
        f"<div class='att-tags'>{tag}{origin}{reason}</div>"
        f"<div class='att-body'>{body}</div></li>"
    )


def _attention_html(
    attention: Sequence[str], blocks: Sequence[Mapping[str, Any]] = ()
) -> str:
    if not attention:
        return (
            "<section class='attention clear' id='attention'><h2>ATTENTION</h2>"
            "<p>없음 — 사람이 지금 할 일은 없다. "
            "<span class='sub'>(ops_status exit 0에 해당)</span></p></section>"
        )
    sources = attention_sources(attention, blocks)
    ranked = sorted(
        zip(attention, sources),
        key=lambda pair: _SEVERITY_RANK[attention_severity(pair[0])[0]],
    )
    counts: dict[str, int] = {}
    for line in attention:
        counts[attention_severity(line)[0]] = (
            counts.get(attention_severity(line)[0], 0) + 1
        )
    tally = " · ".join(
        f"<span class='sev {level.lower() if level != '?' else 'unknown'}'>"
        f"{level}</span> {counts[level]}건"
        for level in ("P1", "?", "P2")
        if counts.get(level)
    )
    items = "".join(_attention_item(line, source) for line, source in ranked)
    return (
        f"<section class='attention' id='attention'>"
        f"<h2>ATTENTION — {len(attention)}건</h2>"
        f"<p class='sub'>사람이 지금 확인해야 하는 것 (ops_status exit 3). "
        f"{tally}</p>"
        f"<p class='sub'>심각도는 <strong>이 화면의 분류</strong>다 — 각 배지 옆에 "
        f"무엇을 보고 그렇게 분류했는지 적혀 있고, 분류하지 못한 줄은 "
        f"<span class='sev unknown'>?</span>로 맨 위에 남는다.</p>"
        f"<ul class='att-list'>{items}</ul></section>"
    )


def _fact(label: str, value: str, tone: str = "") -> str:
    """One tile in the COMPANY strip.

    `fact-item`, not `cov-item` (C129). The first draft reused the coverage
    tile's class because it looks the same, and an existing test asserting
    "the coverage tile is not amber" started failing on a COMPANY tile that
    legitimately was. Two sections that mean different things should not
    answer to the same selector — a shared class turns every assertion about
    one of them into an assertion about both.
    """
    cls = f"fact-item {tone}".strip()
    return (
        f"<div class='{cls}'><span class='cov-l'>{html.escape(label)}</span>"
        f"<span class='cov-v'>{value}</span></div>"
    )


def _missing(reason: str) -> str:
    """A value this machine has no record of — never rendered as a zero."""
    return f"<span class='nil'>기록 없음</span><span class='sev-why'> {html.escape(reason)}</span>"


def _company_html(data: Mapping[str, Any]) -> str:
    """COMPANY — the one-line verdict and the numbers behind it (C129).

    The required first section, and it did not exist. What stood in for it
    was the `COMPANY` **prose block** at the bottom of the page, inside a
    `<pre>`; a reader asking "회사가 지금 어떤 상태인가" had to read a
    paragraph and hold four other paragraphs in their head.

    Every value here is a field from `operational_facts()` or a count this
    page already had. The verdict is derived from **ATTENTION severity**,
    which is the same thing the header badge and `ops_status`'s exit code
    say — three renderings of one fact, not a fourth opinion.
    """
    attention = list(data.get("attention") or [])
    ops = data.get("ops") or {}
    model = data.get("model") or {}
    coverage = model.get("coverage") or {}
    p1 = sum(1 for line in attention if attention_severity(line)[0] == "P1")
    unknown = sum(1 for line in attention if attention_severity(line)[0] == "?")

    # **"할 일 없음"과 "셀 것이 없음"을 구별한다.**
    #
    # The first draft went straight from `not attention` to a green "지금
    # 사람이 할 일은 없다". Measured on an empty tree — no Events at all,
    # which is what a machine on its first day has — that is what it said,
    # in green, above six zeroes.
    #
    # That is the conversion C77 removed one section down, arriving in the
    # section added to summarise it: every word was true about a field
    # (`attention` really is empty) and the sentence was false about the
    # company (nobody has looked, because there is nothing to look at). The
    # evidence count decides first, and only then the ATTENTION severity.
    if p1 or unknown:
        tone, state = "bad", f"조치가 필요하다 — P1 {p1}건" + (
            f" · 미분류 {unknown}건" if unknown else ""
        )
    elif attention:
        tone, state = "warn", f"확인이 필요하다 — {len(attention)}건"
    elif not model.get("events_read"):
        tone, state = "warn", (
            "셀 Event가 없다 — '문제 없음'이 아니라 '판단할 증거가 없다'"
        )
    else:
        tone, state = "ok", "지금 사람이 할 일은 없다"

    run = ops.get("run")
    if run is None:
        runner = _missing("Run Manifest가 아직 없다")
    elif isinstance(run, dict) and run.get("error"):
        runner = _missing("Run Manifest를 읽지 못했다")
    else:
        days = run.get("days_ago")
        runner = (
            f"<span class='state {'bad' if (days or 0) >= 2 else 'ok'}'>"
            f"{run.get('overall_status')}</span>"
            + (f" · {days}일 전" if days is not None else "")
        )

    agent = ops.get("agent")
    if not agent:
        agent_cell = _missing("Agent state를 읽지 못했다")
    else:
        days = agent.get("days_ago")
        agent_cell = (
            f"{html.escape(str(agent.get('desktop_id') or '—'))} · "
            + (
                f"<span class='state {'bad' if days is not None and days >= 2 else 'ok'}'>"
                f"{days}일 전</span>"
                if days is not None
                else "<span class='nil'>실행 기록 없음</span>"
            )
        )

    backup = ops.get("last_backup")
    backup_cell = (
        _missing("성공한 백업이 아직 없다")
        if not backup
        else (
            f"<span class='state {'warn' if (backup.get('days_ago') or 0) >= 2 else 'ok'}'>"
            f"{backup.get('days_ago')}일 전</span>"
        )
    )

    desktops = next(
        (
            panel
            for panel in (model.get("panels") or [])
            if panel.get("key") == "DESKTOPS"
        ),
        None,
    )
    silent = 0
    if desktops:
        for row in desktops.get("rows") or []:
            value = (row.get("values") or {}).get("days_silent")
            if isinstance(value, (int, float)) and value >= 3:
                silent += 1

    facts = "".join(
        (
            _fact("Runner 마지막 실행", runner, "bad" if run and (run.get("days_ago") or 0) >= 2 else ""),
            _fact("이 머신의 Agent", agent_cell),
            _fact("마지막 성공 백업", backup_cell),
            _fact("수집된 Event", f"{model.get('events_read', 0):,}건"),
            _fact(
                "증거 기간",
                f"{html.escape(str(coverage.get('evidence_from') or '—'))} ~ "
                f"{html.escape(str(coverage.get('evidence_to') or '—'))}",
            ),
            _fact(
                "3일 이상 조용한 Desktop",
                f"<span class='state {'warn' if silent else 'ok'}'>{silent}</span>",
                "warn" if silent else "",
            ),
        )
    )
    return (
        f"<section class='company' id='company'><h2>COMPANY — 지금 상태</h2>"
        f"<p class='company-state {tone}'>{html.escape(state)}</p>"
        f"<p class='sub'>이 줄은 아래 ATTENTION의 심각도에서 나온다 — 헤더 배지와 "
        f"<code>ops_status.py</code>의 종료 코드가 말하는 것과 같은 사실이다.</p>"
        f"<div class='company-grid'>{facts}</div></section>"
    )


def _notion_sync_html(data: Mapping[str, Any]) -> str:
    """NOTION SYNC — the two syncs, side by side, never merged (C129).

    The required ninth section, and it did not exist on this screen either.
    The distinction it keeps is the one AGENT.md §6c spends a paragraph on:

        Runner의 Notion Sync   PROJECTS **Row**에 Event 상태를 쓴다.
                               Runner 일정으로 돈다.
        Dashboard publish      Notion **페이지**를 다시 쓴다.
                               사람이 명령을 실행할 때만 돈다.

    "앞의 것이 며칠 멈춰 있어도 뒤의 것은 계속 성공한다" — so one status for
    both would be false about whichever one you did not mean.

    **The publish side has no local record, and that is said rather than
    guessed.** `publish_control_tower.py` writes its timestamp onto the
    Notion page and nothing on this machine, so this screen cannot report a
    last-publish time. Inventing one, or reusing the Runner's, is the exact
    merge this section exists to prevent. Giving it a local receipt would
    add a new `runtime/` artifact, which docs/14 §2's taxonomy makes a
    decision rather than a cleanup (BACKLOG).
    """
    ops = data.get("ops") or {}
    run = ops.get("run") or {}
    queue = ops.get("notion_queue")
    pending = ops.get("notion_pending")

    sync_status = run.get("notion_sync") if isinstance(run, dict) else None
    if sync_status is None:
        runner_state = _missing("실행 기록이 없다")
    elif sync_status == "SKIPPED":
        runner_state = (
            "<span class='state warn'>SKIPPED</span>"
            "<span class='sev-why'> — 미설정으로 건너뛰었다 (실패가 아니다)</span>"
        )
    elif sync_status == "SUCCESS":
        runner_state = "<span class='state ok'>SUCCESS</span>"
    else:
        runner_state = f"<span class='state bad'>{html.escape(str(sync_status))}</span>"

    def count(value: object, unit: str) -> str:
        if value is None:
            return _missing("읽지 못했다")
        tone = "bad" if isinstance(value, int) and value else "ok"
        return f"<span class='state {tone}'>{value}{unit}</span>"

    runner_card = (
        "<div class='sync'><h3>Runner의 Notion Sync</h3>"
        "<p class='who'>PROJECTS <strong>Row</strong>에 Event 상태를 쓴다 · "
        "Runner가 돌 때 갱신된다</p><dl>"
        f"<dt>마지막 실행</dt><dd>"
        + (
            f"{html.escape(str(run.get('started_at')))} "
            f"({run.get('days_ago')}일 전)"
            if run.get("started_at")
            else _missing("Run Manifest가 없다")
        )
        + "</dd>"
        f"<dt>그 실행의 결과</dt><dd>{runner_state}</dd>"
        f"<dt>대기 중 Event</dt><dd>{count(queue, '건')}</dd>"
        f"<dt>밀린 Dashboard 기록</dt><dd>{count(pending, '건')}</dd>"
        "</dl></div>"
    )

    publish_card = (
        "<div class='sync'><h3>Dashboard publish</h3>"
        "<p class='who'>Notion <strong>페이지</strong>를 다시 쓴다 · "
        "사람이 <code>publish_control_tower.py</code>를 실행할 때만 돈다</p><dl>"
        "<dt>마지막 발행</dt><dd>"
        + _missing("이 머신에 기록이 없다 — 시각은 Notion 페이지 맨 위 '마지막 갱신'에 있다")
        + "</dd>"
        "<dt>자동 실행</dt><dd><span class='state warn'>없음</span>"
        "<span class='sev-why'> — 스스로 갱신되지 않는다</span></dd>"
        "<dt>지금 갱신하려면</dt><dd><code>python publish_control_tower.py</code></dd>"
        "</dl></div>"
    )

    return (
        "<section class='window' id='notion'><h2>NOTION SYNC — 두 가지다</h2>"
        "<p class='sub'>이 둘은 서로 다른 것을 서로 다른 일정으로 쓴다. "
        "한쪽이 며칠 멈춰 있어도 다른 쪽은 계속 성공하므로, 하나의 상태로 합치면 "
        "둘 중 어느 쪽에 대해서도 거짓이 된다.</p>"
        f"<div class='sync-grid'>{runner_card}{publish_card}</div></section>"
    )


def _strip_block_heading(text: str, key: str) -> str:
    """Drop the heading `ops_status.py` prints, which this card already shows.

    Each `_print_*` opens with its own title and a rule, because in a
    terminal there is nothing else to separate the sections:

        COMPANY — Desktop 4가 수집한 Event 기준
        ------------------------------------------------------------
          DESKTOP_1   events=9 …

    On this page the card's `<h3>` says the same thing two lines above, so
    every block opened with its title twice and a row of sixty dashes —
    **twelve lines of the operational area saying nothing** (measured on the
    rendered page, six blocks).

    Removed only when both lines are actually there and the second really is
    a rule: a block whose shape changes keeps its text untouched rather than
    losing a line of content to a guess.
    """
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    if not lines[0].strip().startswith(key):
        return text
    rule = lines[1].strip()
    if len(rule) < 10 or set(rule) != {"-"}:
        return text
    return "\n".join(lines[2:]).lstrip("\n")


def _blocks_html(blocks: Sequence[Mapping[str, Any]]) -> str:
    parts = []
    for block in blocks:
        text = html.escape(
            _strip_block_heading(block.get("text") or "", str(block.get("key") or ""))
        )
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
.kpi-src{font-size:11px;color:#6e7681;margin-top:6px;overflow-wrap:anywhere}
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

/* ---------------------------------------------- ATTENTION severity (C129)
   The screen's loudest element on purpose: the operator's first question is
   "무엇이 문제인가", and before this the nine lines were an undifferentiated
   <ol> in which a 396-character paragraph sat between two one-line alerts. */
.att-list{list-style:none;margin:10px 0 0;padding:0;display:flex;
 flex-direction:column;gap:8px}
.att{background:#1d1113;border:1px solid #5c2126;border-left:4px solid #8b2b32;
 border-radius:6px;padding:9px 12px}
.att.p2{border-left-color:#7a5c11;background:#1c1710;border-color:#4a3a12}
.att.unknown{border-left-color:#553a68;background:#191320;border-color:#3f2d4d}
.att-tags{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.att-body{color:#ffd7d5;overflow-wrap:anywhere}
.att.p2 .att-body{color:#f0d9a0}
.att.unknown .att-body{color:#d8c8e8}
.sev{font-size:11px;font-weight:800;letter-spacing:.5px;padding:1px 8px;
 border-radius:4px;background:#8b2b32;color:#fff;white-space:nowrap}
.sev.p2{background:#7a5c11;color:#fff}
.sev.unknown{background:#553a68;color:#fff}
.sev-src{font-size:10.5px;font-family:Consolas,monospace;color:#8b949e;
 border:1px solid #30363d;border-radius:4px;padding:0 6px;white-space:nowrap}
.sev-why{font-size:11px;color:#8b949e}
.att details.more{margin-top:6px}
.att details.more summary{color:#58a6ff;font-size:11.5px;cursor:pointer}
.att details.more p{margin:6px 0 0;color:#c9d1d9}

/* ---------------------------------------------- COMPANY strip (C129) */
.company{background:#161b22;border:1px solid #30363d;border-radius:8px;
 padding:14px 18px;margin-bottom:16px}
.company h2{margin-top:0}
.company-state{font-size:16px;font-weight:700;margin:0 0 4px}
.company-state.bad{color:#ff7b72}
.company-state.warn{color:#e3b341}
.company-state.ok{color:#7ee787}
.company-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
 gap:8px;margin-top:12px}
.fact-item{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:8px 10px;
 display:flex;flex-direction:column;gap:2px}
.fact-item.warn{border-color:#7a5c11;background:#1c1710}
.fact-item.bad{border-color:#8b2b32;background:#1d1113}

/* ---------------------------------------------- NOTION sync (C129)
   Two syncs, side by side, never one status. */
.sync-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
.sync{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:10px 12px}
.sync h3{margin:0 0 2px;font-size:13px}
.sync .who{font-size:11px;color:#6e7681;margin:0 0 8px}
.sync dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:12px}
.sync dt{color:#8b949e;white-space:nowrap}
.sync dd{margin:0;overflow-wrap:anywhere}

/* ---------------------------------------------- table legibility (C129)
   `td` had no break rule, so one long unbroken id widened the whole table
   and pushed every later column off-screen. */
td{overflow-wrap:anywhere}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ts{white-space:nowrap;font-variant-numeric:tabular-nums;color:#c9d1d9}
.ts .ts-y{color:#6e7681}
.idcell{font-family:Consolas,monospace;font-size:11.5px}
.folded{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline}
.fold{background:#0d1117;border:1px solid #21262d;border-radius:4px;padding:1px 8px;
 font-size:11.5px}
.fold b{color:#8b949e;font-weight:600;margin-right:4px}

/* ---------------------------------------------- narrow screens (C129)
   There was no @media rule at all. */
@media (max-width:760px){
  header{padding:10px 14px;gap:8px}
  header h1{font-size:16px}
  .verdict{margin-left:0;width:100%;text-align:center}
  main{padding:14px 14px 48px}
  .kpis{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
  .kpi-value{font-size:22px}
  .company-grid,.cov-grid{grid-template-columns:1fr 1fr}
  .sync-grid{grid-template-columns:1fr}
  .att-tags{gap:6px}
  table{font-size:12px}
  th,td{padding:5px 7px}
}
@media (max-width:420px){
  .company-grid,.cov-grid,.kpis{grid-template-columns:1fr}
}
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
        # An inline favicon. A browser asks for one on every load and
        # this server answered **404** — measured in its own log, on the
        # line after the page request. A 404 in the network tab beside a
        # status screen is one more thing an operator has to rule out.
        # A data: URI so nothing new is served and nothing is fetched;
        # the three bars are the P1/P2/OK colours the page already uses.
        "<link rel='icon' href='data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20viewBox=%220%200%2016%2016%22%3E%3Crect%20width=%2216%22%20height=%2216%22%20rx=%223%22%20fill=%22%230d1117%22/%3E%3Crect%20x=%223%22%20y=%227%22%20width=%222.5%22%20height=%226%22%20fill=%22%237ee787%22/%3E%3Crect%20x=%226.75%22%20y=%224%22%20width=%222.5%22%20height=%229%22%20fill=%22%23e3b341%22/%3E%3Crect%20x=%2210.5%22%20y=%229%22%20width=%222.5%22%20height=%224%22%20fill=%22%23ff7b72%22/%3E%3C/svg%3E'>"
        f"<style>{_CSS}</style></head><body>"
        "<header><h1>DOJOONPASS Control Tower</h1>"
        f"<span class='meta'><time id='gen' datetime='{generated}'>{generated}</time>"
        "<span id='age' hidden></span>"
        f" · schema {schema}"
        " · <a href='/api/dashboard.json'>JSON</a>"
        " · <a href='/'>새로고침</a>"
        f"{build}</span>"
        f"{verdict}</header><main>"
        + _company_html(data)
        + _attention_html(attention, data.get("blocks") or [])
        + _notion_sync_html(data)
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
        # A HEAD response carries headers and no body (RFC 9110 §9.3.2).
        # Python's own `send_error()` makes exactly this exception for
        # exactly this reason; writing the bytes anyway leaves a client
        # reading a body the status line promised was not coming.
        #
        # **What `Content-Length` above means here, corrected (C121).** This
        # comment used to claim it "still describes what a GET *would*
        # return, which is the point of the method". Measured against the
        # running server:
        #
        #     GET  /   200  Content-Length: 53140   body 53140 bytes
        #     HEAD /   405  Content-Length: 9       body 0 bytes
        #
        # It describes the **405 representation** (`b"read-only"`), because
        # a HEAD here never reaches `do_GET()` — `__getattr__` routes every
        # non-GET method, HEAD included, to `do_POST()`'s refusal. That is
        # correct: a `Content-Length` belongs to the representation the
        # status line is about, and this one is about a 405. What was wrong
        # was the sentence, which named the one property this response does
        # not have — and it sat directly above the assertion that measures
        # the true one (`test_a_head_request_answers_without_a_body`
        # asserted `Content-Length: 9` under a comment saying "what a GET
        # would return").
        #
        # This is C115's own defect shape reproduced by C115's fix: a
        # universal claim in prose that the code beneath it does not make.
        if self.command != "HEAD":
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

    def __getattr__(self, name: str):
        """Any method that is not GET is refused with 405 — by construction.

        This used to be a hand-written roster (`do_PUT = do_DELETE =
        do_PATCH = do_POST`), and this module's own docstring makes the
        universal claim above it: *"Only GET is answered; everything else
        gets 405."* Measured against the running server, that claim was
        false for three methods, because `BaseHTTPRequestHandler` answers a
        `do_*` it cannot find with its own 501:

            GET 200 · POST/PUT/DELETE/PATCH 405 · **HEAD/OPTIONS/TRACE 501**

        The one that costs something is HEAD: `curl -I <url>` is the most
        common way anyone checks whether a local server is up, and it
        replied `501 Unsupported method ('HEAD')` — which reads as "this
        program is broken", not "this program is read-only".

        The roster was the defect, so the roster is gone rather than
        extended by three. `handle_one_request()` dispatches by
        `hasattr(self, "do_" + command)`, so answering here makes the
        docstring's claim structurally true for every method that exists
        now and every one that does not exist yet.

        Scoped strictly to the `do_` prefix: a catch-all `__getattr__` on a
        handler would swallow genuine `AttributeError`s from the rest of
        this class, turning a typo into a silent 405.
        """
        if name.startswith("do_"):
            return self.do_POST
        raise AttributeError(name)

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

    # Refused rather than defaulted. An operator who set this set it because
    # the default port is taken, and quietly serving on the taken port
    # instead is the "did the unsafe thing and reported success" shape
    # `src/cli.py` was written about. `resolve_port()` holds the parsing so
    # that `publish_control_tower.py` cannot answer this differently.
    port = resolve_port()
    if port is None:
        raw = os.environ.get(PORT_ENV_VAR, "").strip()
        print(
            f"[FAILED] {PORT_ENV_VAR}={raw!r} 은(는) 포트 번호가 아닙니다 "
            "(1-65535).",
            file=sys.stderr,
        )
        return CONFIG_ERROR_EXIT

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
