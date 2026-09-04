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
import businessdate  # noqa: E402
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
from controltower import (  # noqa: E402
    build_company_rollup,
    build_dashboard,
    evidence_window,
)
from delivery import read_git_activity  # noqa: E402
from controltower.cohort import COHORT_WINDOWS  # noqa: E402
from controltower.columns import LABELS as _column_labels  # noqa: E402
from controltower.kpi import DATA_REQUIRED_READING  # noqa: E402
from controltower import verdict as _verdict  # noqa: E402
from controltower.attention import KIND_LABELS as _ATTENTION_KIND_LABELS  # noqa: E402
from controltower.attention import RANK as _ATTENTION_RANK  # noqa: E402
from controltower.attention import kind as _attention_kind  # noqa: E402
from controltower.attention import next_action as _attention_action  # noqa: E402
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

# The blocks of `ops_status.py::main()`, in its order. CONTROL TOWER is
# marked because this page renders its panels itself and keeps the text only
# as the parity check described in the module docstring.
_BLOCKS: tuple[tuple[str, str, bool], ...] = (
    ("COMPANY", "COMPANY — Desktop 4가 수집한 Event", False),
    ("HISTORY", "HISTORY — Company Repository", False),
    ("CONTROL TOWER", "CONTROL TOWER — 터미널 출력 (패널 대조용)", True),
    ("LAST RUN", "LAST RUN — Run Manifest", False),
    # The only block on this page whose evidence is not a file this system
    # wrote. It is here because the page and the Notion Control Tower it
    # feeds are read by the people who never open a terminal, and "nothing
    # is scheduled" is the one condition that makes every other block on
    # the page stop changing without any of them saying so.
    ("SCHEDULE", "SCHEDULE — Windows Task Scheduler 등록 상태", False),
    ("NOTION", "NOTION — Sync / Retry Queue", False),
    ("AGENT", "AGENT — 이 머신의 Agent", False),
)

#: `_BLOCKS`'s key -> the `ops_status.py` renderer that produces it.
#:
#: `SCHEDULE` is the one entry that costs a subprocess (measured: 0.6 s for
#: a `powershell -NoProfile` query of three task names). That is affordable
#: precisely because of the decision recorded at the top of this file —
#: "the page does not refresh itself" — so the cost is paid once per page an
#: operator deliberately opened, not on a timer.
_RENDERERS = {
    "COMPANY": ops_status._print_company,
    "HISTORY": ops_status._print_history,
    "CONTROL TOWER": ops_status._print_control_tower,
    "LAST RUN": ops_status._print_last_run,
    "SCHEDULE": ops_status._print_schedule,
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
    # The D+1 half Events cannot answer. Read over the **same window** the
    # panels cover, so "어제 무엇이 변경됐는가" and "어제 무엇이 보고됐는가"
    # are answers about one day rather than two.
    #
    # When the caller gave no window, the git side is asked for the same
    # unbounded range the rollup used — `since=None` means "everything on
    # disk" there, and `read_git_activity()` requires concrete dates, so the
    # window is taken from the evidence the rollup actually found. With no
    # evidence at all there is nothing to align to and git is not asked; the
    # panel says "물어보지 않았다", which is true.
    evidence_from, evidence_to = evidence_window(rollup)
    window_since = since or evidence_from
    window_until = until or evidence_to
    activity = (
        read_git_activity(since=window_since, until=window_until)
        if window_since is not None and window_until is not None
        else None
    )
    model = build_dashboard(rollup, now=now, activity=activity)
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

    # --- Which Notion credentials **this process** can see.
    #
    # Not the values, and never the values: only whether the two names
    # `NotionConfig.from_env()` requires are set and non-blank, which is the
    # same restraint `ops_status._notion_credentials_exported_but_never_
    # exercised()` keeps one layer down.
    #
    # Why the screen needs it (C133). Measured, same instant, same company:
    # the browser page said `ATTENTION 1건` and the Notion page said `2건`.
    # Neither was wrong. `publish_control_tower.py` had been started from a
    # shell with the token exported and this server had not, so the NOTION
    # block raised a line in one process and not the other — and neither
    # surface said which of the two states it had rendered under. Two
    # screens describing one company must not be able to disagree in silence.
    #
    # `_NOTION_REQUIRED` rather than two names spelled again here: a third
    # variable added to that tuple must reach this line without an edit, or
    # this becomes a check that quietly stopped covering the thing it names.
    facts["notion_credentials"] = all(
        (os.environ.get(name) or "").strip()
        for name in ops_status._NOTION_REQUIRED
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
    # Held across every block rather than re-taken per block: the gap
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
        #
        # **"About three times" is the floor, not the range (C151.)** That
        # figure is a tree gone cold between runs. A tree whose files were
        # *just written* is far worse -- re-measured on this machine,
        # `read_events()` over 6,000 freshly created Event files:
        #
        #     first read (just written)   25,809 ms
        #     second read (same files)       419 ms
        #     third read                     402 ms
        #
        # 60x, not 3x, and it is entirely first-touch: the second read is
        # already at the warm figure. This is the shape a Runner meets --
        # `collector` writes into `processed/` and a later step in the same
        # run reads it back -- so the number an operator sees on the first
        # dashboard after a large collection is this one, not the 3,130 ms
        # above. Nothing here can avoid it; it is recorded so the next
        # person to time this page does not read it as a regression.
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
    # `warn`, not `bad`: the project is still moving. Painting it the same
    # red as a stopped one would teach a reader that red means "look
    # eventually", which is the reading `verdict.py` spends its length
    # preventing.
    "AT_RISK": "warn",
    "CANCELLED": "warn",
    "COMPLETE": "ok",
    "ACTIVE": "neutral",
    "OPEN_BLOCKER": "bad",
    "ROLE_MISMATCH": "warn",
    "DUPLICATE_EVENT": "warn",
}

_PANEL_ORDER = (
    "METRICS",
    # Directly after METRICS, because it is the same numbers with an owner
    # attached plus the ones nobody can compute — a reader who has just seen
    # the counts is the reader who needs to know which of them their role
    # actually answers for (C149).
    "ROLE_KPI",
    # Third, and after the two count panels rather than before them: a cohort
    # is a *reading* of the same Projects over time, and it only means
    # something to a reader who has just seen how many there are. It is the
    # one panel here that answers "이게 나아지고 있는가" instead of "지금
    # 얼마인가".
    "COHORT",
    "RISKS",
    "PROJECTS",
    "TEAMS",
    "DESKTOPS",
    "ACTIVITY",
    "COMPLETIONS",
    # Below the Event feed rather than above it: this is the *other* record
    # of the same days, and the Events are the one a person acts on. It is
    # here at all because a day nobody reported and a day nothing happened
    # look identical in every panel above (C149).
    "CODE_CHANGES",
    "COMPANY_GOALS",
    "SPRINTS",
    "JUDGEMENTS",
)

#: What a person calls each Model column.
#:
#: Moved to `controltower/columns.py` in C134 and imported back under the
#: old name. The Notion page renders the same tables and could not reach
#: this dict — it lived in an entrypoint — so it printed raw field names
#: (`display_name`, `days_silent`) as headers on the surface this company's
#: non-developers read. A name two renderers need belongs under both, which
#: is the argument `attention.py` already made for the severity rule.
_COLUMN_LABELS = _column_labels


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


#: A short all-caps token — a state, a status, a role, a risk kind.
#:
#: These are the words a reader scans a table *for*, and they are the ones
#: `overflow-wrap:anywhere` was breaking down the middle: measured on a probe
#: tree at 1440px, `IN_PROGRESS` rendered as `IN_PROG` / `RESS` and
#: `COMPLETED` as `COMPLETE` / `D` (C133).
#:
#: Recognised by shape rather than by a roster, because the roster would have
#: to list `PROJECT_STATES`, `events.STATUSES`, `events.ROLES` and the three
#: `RISKS` kinds, and would silently stop covering whichever of them changed.
#:
#: **Bounded at 24 characters**, which is what keeps this from undoing C129:
#: a long `project_id` or `event_id` is also all-caps and must still wrap, or
#: one of them sets the table's width and pushes every later column
#: off-screen. Every state word this project defines is under 16.
_TOKEN = re.compile(r"^[A-Z][A-Z0-9_]{0,23}$")


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
    classes = []
    if state:
        classes.append(f"state {state}")
    # A one-element list of tokens is still one token on the screen. `teams`
    # arrives as `["CTO_BACKEND"]` and `_e()` renders it as `CTO_BACKEND`, so
    # a check against the raw value missed it and the column broke as
    # `CTO_BAC` / `KEND` beside a `Blocker Team` column that did not
    # (measured on a probe tree). The check follows what a reader sees.
    token = value
    if isinstance(token, (list, tuple)) and len(token) == 1:
        token = token[0]
    if isinstance(token, str) and _TOKEN.match(token):
        classes.append("tok")
    cls = f" class='{' '.join(classes)}'" if classes else ""
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
      * with one row, only a column that is **empty** folds. Every column of
        a single row is trivially constant, so the general rule would make
        the table vanish; an always-empty column is different, because there
        is no value in it to lose (C133).

    That second half is not a refinement for its own sake. `RISKS` is a
    union of three row shapes — `OPEN_BLOCKER`, `ROLE_MISMATCH`,
    `EVENT_ID_CONFLICT` — so a single open Blocker fills five of its twelve
    columns and *cannot* fill the rest. Measured on a probe tree: one row,
    six columns of `—`, on the table this page puts at the top because it is
    the one a reader must not scroll past.
    """
    if len(columns) < 2:
        return list(columns), []

    single = len(rows) < 2
    kept: list[str] = []
    folded: list[tuple[str, Any]] = []
    for index, column in enumerate(columns):
        values = [(row.get("values") or {}).get(column) for row in rows]
        first = values[0] if values else None
        constant = all(value == first for value in values)
        empty = all(value is None or value == "" for value in values)
        if index == 0 or not values or not constant or (single and not empty):
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
        f"<p class='note folded'>모든 행({row_count:,}건)에서 값이 같거나 "
        f"비어 있는 열은 표에서 한 줄로 접었다: {' · '.join(parts)}</p>"
    )


#: The columns a reader decides on, first — per panel (C133).
#:
#: **Reordering only. Nothing is dropped.** The temptation on a wide table is
#: to pick five columns and hide the rest, and this project has a standing
#: reason not to: a column that stops being rendered stops being checked, and
#: the next audit reads a narrower table as the whole truth. `PROJECTS`
#: carries fifteen columns and the operator's question is "which project is
#: in trouble and since when" — so `state` / `blocker` / `days_blocked` lead,
#: and `first_seen` / `milestones` / `sprint` follow rather than vanish.
#:
#: Columns absent from a list keep their model order, after the listed ones,
#: so a column added upstream still appears without an edit here.
_PANEL_COLUMN_ORDER: dict[str, tuple[str, ...]] = {
    "PROJECTS": (
        "project_id",
        "state",
        "status",
        "blocker",
        "days_blocked",
        "days_idle",
        "last_seen",
        "teams",
    ),
    "RISKS": ("kind", "project_id", "blocker", "days_open", "since", "team"),
    "DESKTOPS": (
        "source",
        "days_silent",
        "has_activity",
        "last_seen",
        "events",
        "expected_team",
        "display_name",
    ),
}


def _ordered_columns(key: str, columns: Sequence[str]) -> list[str]:
    """`columns`, with this panel's decision columns brought to the front."""
    preferred = _PANEL_COLUMN_ORDER.get(key)
    if not preferred:
        return list(columns)
    present = [c for c in preferred if c in columns]
    return present + [c for c in columns if c not in present]


def _panel_table_html(panel: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    """The `<table>` for `rows` of `panel`, with its folded-column line.

    Extracted from `_panel_html()` because a second caller arrived and the
    alternative was a second table builder: `_role_kpi_html()` draws the same
    panel as **two** tables — the KPIs this system can answer, and the ones it
    cannot — and a copy of this loop would be two renderings of one panel that
    could disagree about column order, labels or folding.

    `rows` is passed rather than read off `panel` for exactly that: the caller
    decides which subset this table is.
    """
    columns = _ordered_columns(
        str(panel.get("key") or ""), list(panel.get("columns") or [])
    )
    columns, folded = _fold_constant_columns(columns, rows)
    header = "".join(
        f"<th>{html.escape(_COLUMN_LABELS.get(c, c))}</th>" for c in columns
    )
    lines = []
    for row in rows:
        values = row.get("values") or {}
        cells = "".join(_cell(c, values.get(c)) for c in columns)
        lines.append(f"<tr>{cells}{_evidence_cell(row)}</tr>")
    return (
        "<div class='scroll'><table><thead><tr>"
        f"{header}<th>증거</th></tr></thead><tbody>"
        + "".join(lines)
        + "</tbody></table></div>"
        + _folded_html(folded, len(rows))
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

    body = (
        _panel_table_html(panel, rows)
        if rows
        else (
            "<p class='empty'>해당 없음 — 이 기간의 증거에 이 항목이 "
            "<b>하나도 없었다</b>. (원천은 있다)</p>"
        )
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


#: Which KPIs have a **direction**, and which are only volume (C133).
#:
#: The requirement this answers is "숫자만 보여주지 말고 정상 / 주의 / 위험의
#: 의미를 명확하게 한다", and before this every tile was a bare count. Nine
#: numbers with no direction make the reader supply nine judgements, and the
#: reader is the person the screen was supposed to save that work for.
#:
#: **Only three of the nine have a direction, and pretending otherwise would
#: be the worse failure.** `기록된 Event 0` is not bad — a quiet week is a
#: quiet week — and painting it amber would teach an operator to ignore
#: amber. So the other six are labelled `참고` in words: they are volume, and
#: the screen says so rather than leaving the reader to guess whether a
#: silent tile means healthy or unmeasured.
#:
#: Keyed on `key`, not on `label`: the label is Korean prose that a later
#: wording change would silently break, and a verdict that quietly stopped
#: applying is exactly the "정상을 보고하는 채로" failure this project keeps
#: removing elsewhere.
#: Moved to `controltower/verdict.py` in C134 and imported back under the
#: old name. The Notion page shows the same nine numbers and could not reach
#: this set — it lived in an entrypoint — so one surface read them with a
#: verdict and the other bare. One vocabulary, one place.
_KPI_LOWER_IS_BETTER = _verdict.METRIC_LOWER_IS_BETTER


def _kpi_verdict(key: str, value: Any, *, measured: bool = True) -> tuple[str, str]:
    """`(word, tone)` for one KPI tile — never a colour on its own.

    WCAG 1.4.1: colour must not be the only carrier of a state, so every
    tile says its verdict in a word as well as in a border. Measured before
    this: the page's twelve state colours had no textual twin anywhere
    outside the ATTENTION badges.

    `measured=False` (an empty corpus) suppresses the verdict entirely —
    see `verdict.metric_verdict()` for why `0 정상` over no evidence is the
    one reading this page must not produce. It matters more here since
    C133: the coverage banner that used to carry that warning is in ⑦ now,
    so these tiles are where a reader meets the zeros first.
    """
    return _verdict.metric_verdict(key, value, measured=measured)


def _kpi_cite(count: int, value: Any) -> str:
    """The evidence line under one KPI tile.

    Three states, not two (C135). This said `증거 {n}건` or `증거 파일 없음`,
    and the second was a **false sentence** for two of the nine metrics.
    Measured on the live tree:

        기록된 Event      16   증거 16건
        움직인 Project     4   증거 파일 없음      <- not true

    `움직인 Project` is the number of distinct `project_id`s among those same
    16 files. `rollup._roll_metrics()` gives it no `evidence` refs **on
    purpose** and the reason is good — it counts projects, not Events, so
    "one file per counted thing" does not exist and inventing refs is the
    invention that module refuses. But "carries no per-item refs" and "has no
    evidence" are different claims, and only the first is true. `Metric`'s own
    docstring says a Control Tower number nobody can trace is a rumour; this
    line was calling a perfectly traceable number a rumour, on the surface a
    person actually reads.

    So a non-zero number with no refs says what it is — derived rather than
    counted — and the tile's `derived_from` sentence, already on the page,
    says derived from what. Only a **zero** keeps `증거 파일 없음`, where it
    is true: nothing happened, so there is nothing to cite.
    """
    if count:
        return f"<span class='cite'>증거 {count}건</span>"
    if value:
        return "<span class='cite derived'>파일을 세지 않는 파생값 — 아래 근거 참조</span>"
    return "<span class='cite none'>증거 파일 없음</span>"


def _kpi_html(panel: Mapping[str, Any] | None, *, measured: bool = True) -> str:
    """KPI tiles, each carrying its verdict and the file count behind it.

    A number with no evidence is marked rather than hidden. `Metric` declares
    that an untraceable number is a rumour; a tile that looked the same
    whether it cited 14 files or none would undo that declaration on the one
    surface a person actually reads.

    `derived_from` stays **visible** rather than moving into a `title=`
    tooltip. It is long and it is spec prose, and the temptation was to hide
    it — but a tooltip is unreachable on a phone and invisible to anyone not
    hovering, and "클릭해야만 알 수 있는 핵심 정보를 만들지 않는다" is a rule
    this page is measured against. It is set small and last instead, which
    costs a reader nothing who is not asking for it.
    """
    if panel is None:
        return ""
    tiles = []
    sources = []
    for row in panel.get("rows") or []:
        values = row.get("values") or {}
        count = row.get("evidence_count", 0)
        value = values.get("value")
        word, tone = _kpi_verdict(
            str(values.get("key") or ""), value, measured=measured
        )
        zero = "zero" if value == 0 else "live"
        cite = _kpi_cite(count, value)
        tiles.append(
            f"<div class='kpi {zero} k-{tone}'>"
            f"<div class='kpi-top'><span class='kpi-value'>{_e(value)}</span>"
            f"<span class='verdict-word {tone}'>{word}</span></div>"
            f"<div class='kpi-label'>{_e(values.get('label'))}</div>"
            f"{cite}"
            "</div>"
        )
        sources.append(
            f"<dt>{_e(values.get('label'))}</dt>"
            f"<dd class='kpi-src'>{_e(values.get('derived_from'))}</dd>"
        )
    return (
        f"<section class='kpis'>{''.join(tiles)}</section>"
        f"<details class='fold-section defs'><summary>"
        f"<span class='fold-h2'>각 지표가 무엇에서 나온 숫자인가</span>"
        f"<span class='sub'>{len(sources)}개</span></summary>"
        f"<dl class='kpi-defs'>{''.join(sources)}</dl></details>"
    )


#: The colour of each D+N series, and the one place it is decided.
#:
#: Colour is never the only carrier here (WCAG 1.4.1): every bar is labelled
#: with its own D+N under the axis, carries its reading as text above it, and
#: the same rows are in the table underneath. The chart is a second reading of
#: the table it sits on, not a replacement for it — which is also why the table
#: is not folded away behind a `<details>`.
_COHORT_SERIES_COLOURS: dict[int, str] = {1: "#58a6ff", 7: "#7ee787", 30: "#e3b341"}

# Chart geometry. Fixed numbers rather than a layout engine: this is one small
# SVG with no script, and a `viewBox` makes it scale.
_CH_BAR = 20
_CH_GAP = 6
_CH_PAD = 26  # between one cohort's group and the next
_CH_PLOT = 150  # 0% .. 100%
_CH_TOP = 18  # room for the value label above a full-height bar
_CH_LEFT = 38  # y-axis labels
_CH_FOOT = 34  # cohort name + series legend row


def _cohort_bar(
    x: int, base_y: int, days: int, reading, retained, base, settled
) -> str:
    """One bar — or, when there is no rate, the absence of one.

    A window with no rate is drawn as a **dashed empty column** carrying its
    own words, never as a zero-height bar. They would be the same pixels, and
    they are opposite claims: "아무도 다시 움직이지 않았다" against "이 창은
    아직 지나지 않았다" and against "창 안에 전부 끝났다".
    `cohort.CohortWindow.rendered()` already decided which of the three this
    is, so the branch here is on **its answer** rather than on a second reading
    of `base` — one place decides, and a renderer that re-derived it could
    disagree with the table directly underneath.

    The bar's *height* is `retained / base` while its *label* is the `dN`
    string the model rendered. Both come from the same two integers, so they
    cannot disagree about the company — the height simply does not round to one
    decimal, because a rectangle is not a claim a reader quotes.
    """
    colour = _COHORT_SERIES_COLOURS.get(days, "#8b949e")
    text = str(reading)
    if not text.endswith("%") or not isinstance(base, int) or base <= 0:
        why = (
            f"{days}일이 지난 구성원이 아직 없다"
            if text == DATA_REQUIRED_READING
            else f"{days}일 안에 전부 완료·취소로 끝났다 ({settled} Project)"
        )
        middle = f"{base_y - _CH_PLOT / 2:.0f}"
        return (
            f"<g><title>D+{days}: {html.escape(text)} — {html.escape(why)}"
            "</title>"
            f"<rect x='{x}' y='{base_y - _CH_PLOT}' width='{_CH_BAR}' "
            f"height='{_CH_PLOT}' fill='none' stroke='#484f58' "
            "stroke-dasharray='3 3'/>"
            f"<text x='{x + _CH_BAR / 2:.1f}' y='{middle}' "
            "text-anchor='middle' font-size='9' fill='#8b949e'"
            f" transform='rotate(-90 {x + _CH_BAR / 2:.1f} {middle})'>"
            f"{html.escape(text)}</text></g>"
        )
    height = _CH_PLOT * (retained or 0) / base
    top = base_y - height
    # The tooltip carries `settled` too, and it is the number that stops the
    # bar being misread on the one cohort where it matters: `0.0% (0/1)` beside
    # `종료 4` is a cohort that mostly *finished*, and without the third figure
    # it reads as a cohort that mostly died.
    ended = f" · 창 안에 끝남 {settled}" if settled else ""
    return (
        f"<g><title>D+{days}: {html.escape(text)} "
        f"({retained}/{base} Project){html.escape(ended)}</title>"
        f"<rect x='{x}' y='{top:.1f}' width='{_CH_BAR}' "
        f"height='{max(height, 1):.1f}' fill='{colour}' rx='2'/>"
        f"<text x='{x + _CH_BAR / 2:.1f}' y='{top - 5:.1f}' text-anchor='middle' "
        f"font-size='10' fill='#e6edf3'>{html.escape(text)}</text></g>"
    )


def _cohort_chart(rows: Sequence[Mapping[str, Any]]) -> str:
    """The COHORT panel's rows as one grouped bar chart.

    X is the cohort, Y is retention, and the three bars in each group are
    D+1 / D+7 / D+30 — the comparison the table can only be read down a row at
    a time. No library and no script: an inline `<svg>` with a `viewBox`, so it
    scales, prints, and works with scripting off like the rest of this page.

    Empty when there are no rows. A chart drawn over nothing is an empty grid
    that reads as "retention is zero", and `_panel_html()`'s own "해당 없음 —
    이 기간의 증거에 이 항목이 하나도 없었다" is the true sentence for it.
    """
    if not rows:
        return ""
    group = _CH_BAR * len(COHORT_WINDOWS) + _CH_GAP * (len(COHORT_WINDOWS) - 1)
    width = _CH_LEFT + len(rows) * (group + _CH_PAD) + _CH_PAD
    height = _CH_TOP + _CH_PLOT + _CH_FOOT
    base_y = _CH_TOP + _CH_PLOT

    parts = []
    for percent in (0, 25, 50, 75, 100):
        y = base_y - _CH_PLOT * percent / 100
        parts.append(
            f"<line x1='{_CH_LEFT}' y1='{y:.1f}' x2='{width - 6}' y2='{y:.1f}' "
            "stroke='#21262d'/>"
            f"<text x='{_CH_LEFT - 6}' y='{y + 3.5:.1f}' text-anchor='end' "
            f"font-size='10' fill='#6e7681'>{percent}%</text>"
        )

    for index, row in enumerate(rows):
        values = row.get("values") or {}
        left = _CH_LEFT + _CH_PAD / 2 + index * (group + _CH_PAD)
        for slot, days in enumerate(COHORT_WINDOWS):
            parts.append(
                _cohort_bar(
                    int(left + slot * (_CH_BAR + _CH_GAP)),
                    base_y,
                    days,
                    values.get(f"d{days}"),
                    values.get(f"d{days}_retained"),
                    values.get(f"d{days}_base"),
                    values.get(f"d{days}_settled") or 0,
                )
            )
        centre = left + group / 2
        parts.append(
            f"<text x='{centre:.1f}' y='{base_y + 15}' text-anchor='middle' "
            f"font-size='11' fill='#e6edf3'>{_e(values.get('cohort'))}</text>"
            f"<text x='{centre:.1f}' y='{base_y + 28}' text-anchor='middle' "
            f"font-size='10' fill='#8b949e'>Project "
            f"{_e(values.get('size'))}</text>"
        )

    legend = " · ".join(
        f"<span class='ch-key'><i style='background:"
        f"{_COHORT_SERIES_COLOURS.get(days, '#8b949e')}'></i>D+{days}</span>"
        for days in COHORT_WINDOWS
    )
    return (
        "<figure class='cohort-chart'>"
        # `width='100%'` with a `min-width` of the drawn width, and the pair is
        # what makes both ends work. Percentage alone squeezes twenty cohorts
        # into the card and the bars become 8px slivers with unreadable labels;
        # a fixed pixel width alone leaves a single cohort as a 162px stamp in
        # a 1400px card. Together the chart fills the card when it fits and
        # scrolls inside `.cohort-chart` when it does not — which is the rule
        # every wide table on this page already follows.
        f"<svg viewBox='0 0 {width} {height}' width='100%' height='{height}' "
        f"style='min-width:{width}px' "
        "role='img' preserveAspectRatio='xMinYMid meet' "
        "aria-label='Cohort별 D+1 / D+7 / D+30 지속률'>"
        f"<line x1='{_CH_LEFT}' y1='{base_y}' x2='{width - 6}' y2='{base_y}' "
        "stroke='#484f58'/>" + "".join(parts) + "</svg>"
        f"<figcaption class='ch-legend'>{legend}"
        " · <span class='ch-key'><i class='ch-none'></i>점선 = 비율이 없다 "
        "(창이 아직 지나지 않았거나, 창 안에 전부 끝났다 — 0%가 아니다)</span>"
        "</figcaption></figure>"
    )


def _cohort_html(panel: Mapping[str, Any]) -> str:
    """The COHORT panel: the chart, then the table it was drawn from.

    Both, in that order, and neither behind a disclosure. The chart is what
    makes three cohorts comparable at a glance; the table is where the
    denominators live, and a reader who does not check `dN_base` will misread
    the chart the first time a cohort is young.
    """
    return _cohort_chart(panel.get("rows") or []) + _panel_html(panel)


def _role_kpi_html(panel: Mapping[str, Any]) -> str:
    """CEO / CTO / COO KPI — the answers first, the refusals one click down.

    Same rows, same order, same panel card. What changes is what a reader
    meets, and it was measured on the live page: ⑤ 핵심 지표 rendered this
    panel as **one flat table of 35 rows, 22 of them `DATA REQUIRED`**, above
    the fold and unfolded. So two thirds of the section a CEO opens to find
    out how the company is doing was a list of things this system cannot
    measure — each row true, each row correct to keep, and together a wall
    that buries the thirteen numbers that *are* answers.

    The Notion page has drawn the same panel correctly since C149: a one-line
    tally, then the detail behind toggles. Two surfaces, one model, and only
    one of them readable — so this is the browser page catching up rather than
    a new idea.

    **Nothing is hidden and nothing is dropped.** The refusals keep their own
    table with `requires` on every row — which is the single most useful column
    on this page, because it says what would have to exist — and the summary
    line above says how many there are before anyone opens it. That is the
    distinction this project keeps: a refusal is a finding, and a finding
    nobody can reach past is a wall.

    The tally is computed here rather than carried on the panel, for
    `_role_kpi_panel()`'s stated reason: panel metadata is the one payload text
    `to_payload()` never redacts, so a note whose wording moves with the
    evidence would break that claim. Counting rows is the renderer's job — and
    `notion_page.py` already counts them the same way.
    """
    rows = list(panel.get("rows") or [])
    measured = [r for r in rows if (r.get("values") or {}).get("measured")]
    refused = [r for r in rows if not (r.get("values") or {}).get("measured")]
    status = str(panel.get("status"))
    cls = _STATUS_CLASS.get(status, "neutral")
    head = (
        f"<div class='panel {cls}'>"
        "<div class='panel-head'>"
        f"<h3>{html.escape(str(panel.get('title')))}"
        f"<span class='pkey'>{html.escape(str(panel.get('key')))}</span></h3>"
        f"<span class='badge {cls}'>{html.escape(status)}</span></div>"
    )
    tally = (
        f"<p class='sub'>{len(rows)}개 중 <b>{len(measured)}개</b>를 이 시스템이 "
        f"계산할 수 있다. 나머지 {len(refused)}개는 값 대신 DATA REQUIRED를 "
        "싣는다 — 결함이 아니라, 이 시스템이 실행을 재고 사업을 재지 않는다는 "
        "사실이다.</p>"
    )
    body = (
        _panel_table_html(panel, measured)
        if measured
        else "<p class='empty'>이 기간의 증거로 계산할 수 있는 KPI가 하나도 없다.</p>"
    )
    folded = ""
    if refused:
        folded = (
            "<details class='fold-section'><summary>"
            f"<span class='fold-h2'>계산할 수 없는 KPI {len(refused)}개 — "
            "무엇이 있어야 답할 수 있는가</span>"
            f"<span class='sub'>{len(refused)}개</span></summary>"
            + _panel_table_html(panel, refused)
            + "</details>"
        )
    note = panel.get("note")
    note_html = f"<p class='note'>{_inline_markup(str(note))}</p>" if note else ""
    source = panel.get("source")
    src_html = (
        f"<p class='source'>출처: {_inline_markup(str(source))}</p>" if source else ""
    )
    return head + tally + body + folded + note_html + src_html + "</div>"


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
        now = businessdate.business_date(datetime.fromisoformat(str(model.get("generated_at"))))
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

    # ---- 다음 행동 (C133) --------------------------------------------
    #
    # The line that was missing. Every item on this list described a
    # condition and none of them said what a person does about it, so the
    # screen ended where the reader's work began. It is **never folded**,
    # including behind the P2 disclosure above: an item worth showing is an
    # item worth showing the remedy for, and a remedy one click away is a
    # remedy an operator scanning the page does not have.
    #
    # `None` is rendered as its own sentence rather than omitted. A silent
    # gap reads as "there is nothing to do"; the honest reading of an
    # unclassified line is that this screen has no remedy for it, and the
    # reader has to go and read.
    action = _attention_action(line)
    action_html = (
        f"<p class='att-do'><b>다음 행동</b> {_inline_markup(action)}</p>"
        if action
        else "<p class='att-do none'><b>다음 행동</b> 이 화면이 정해 두지 "
        "않았다 — 줄 전문을 읽고 사람이 판단한다.</p>"
    )
    return (
        f"<li class='att {level.lower() if level != '?' else 'unknown'}'>"
        f"<div class='att-tags'>{tag}{origin}{reason}</div>"
        f"<div class='att-body'>{body}</div>{action_html}</li>"
    )


def _attention_html(
    attention: Sequence[str], blocks: Sequence[Mapping[str, Any]] = ()
) -> str:
    """지금 해야 할 일 — never collapsed, and grouped by what it asks for.

    Two groups, because they are two different kinds of work and mixing them
    makes both harder to act on: something is broken and must be repaired,
    versus something is waiting for a person to decide. The split is narrow
    on purpose — see `controltower.attention.DECIDE_MARKERS` for why it is
    the review queue and nothing else.
    """
    if not attention:
        return (
            "<section class='attention clear' id='attention'>"
            "<h2>② 지금 해야 할 일 — 없음</h2>"
            "<p>사람이 지금 할 일은 없다. "
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

    grouped = [
        (group, [pair for pair in ranked if _attention_kind(pair[0]) == group])
        for group in ("FIX", "DECIDE")
    ]
    grouped = [(group, rows) for group, rows in grouped if rows]
    # A heading over the only group on the page is chrome, not structure:
    # measured with one item, the section carried a heading, a group heading
    # and a count above a single line. The split earns its heading exactly
    # when there is something to split from.
    label = len(grouped) > 1
    groups = []
    for group, rows in grouped:
        items = "".join(_attention_item(line, source) for line, source in rows)
        if label:
            groups.append(
                f"<h3 class='att-group {group.lower()}'>"
                f"{html.escape(_ATTENTION_KIND_LABELS[group])} "
                f"<span class='sub'>{len(rows)}건</span></h3>"
            )
        groups.append(f"<ul class='att-list'>{items}</ul>")

    return (
        f"<section class='attention' id='attention'>"
        f"<h2>② 지금 해야 할 일 — ATTENTION {len(attention)}건</h2>"
        f"<p class='sub'>사람이 지금 확인해야 하는 것 (ops_status exit 3). "
        f"{tally}</p>"
        f"<p class='sub'>심각도와 다음 행동은 <strong>이 화면의 분류</strong>다 — "
        f"각 배지 옆에 무엇을 보고 그렇게 분류했는지 적혀 있고, 분류하지 못한 "
        f"줄은 <span class='sev unknown'>?</span>로 맨 위에 남는다.</p>"
        + "".join(groups)
        + "</section>"
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


def _panel_of(model: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    """One panel of the model by key, or `None` if the model has no such panel."""
    for panel in model.get("panels") or []:
        if panel.get("key") == key:
            return panel
    return None


def _kpi_value(model: Mapping[str, Any], key: str) -> Any:
    """One METRICS row's value, or `None` when the model did not carry it.

    `None`, never 0. A metric this screen could not find is not a metric that
    measured zero, and the rest of this file spends most of its length on
    that distinction.
    """
    metrics = _panel_of(model, "METRICS")
    if metrics is None:
        return None
    for row in metrics.get("rows") or []:
        values = row.get("values") or {}
        if values.get("key") == key:
            return values.get("value")
    return None


def _project_states(model: Mapping[str, Any]) -> dict[str, int]:
    """`{state: count}` over the PROJECTS panel's rows."""
    panel = _panel_of(model, "PROJECTS")
    counts: dict[str, int] = {}
    if panel is None:
        return counts
    for row in panel.get("rows") or []:
        state = str((row.get("values") or {}).get("state") or "—")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _silent_desktops(model: Mapping[str, Any]) -> int:
    """Desktops past this project's own silence threshold.

    `isinstance` excludes `bool` deliberately: `True >= 3` is `False` in
    Python but `True` is not a number of days, and a column that changed
    shape upstream must not be silently counted as "not silent".
    """
    panel = _panel_of(model, "DESKTOPS")
    if panel is None:
        return 0
    silent = 0
    for row in panel.get("rows") or []:
        value = (row.get("values") or {}).get("days_silent")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value >= ops_status.SILENT_AFTER_DAYS:
                silent += 1
    return silent


#: The verdict word and its shape -- the page's whole vocabulary of states.
#:
#: A word **and** a shape, never a colour alone: WCAG 1.4.1 makes colour an
#: addition to a state rather than the state itself, and this page carried a
#: dozen state colours whose only textual twin lived inside the ATTENTION
#: badges. The shapes are geometric rather than pictorial so they render at
#: the weight of the text beside them in any font this page falls back to.
#:
#: **Derived from `controltower/verdict.py`, not spelled again here (C134).**
#: The Notion page renders the same three states as `🔴 / 🟡 / 🟢` with the
#: same three words, and C134 introduced that module to hold them — but left
#: this dict in place, so the words existed twice and only one of the two was
#: reachable from the Notion side. The project's own dead-capability
#: inventory caught it: `verdict.shape()` was defined and never called,
#: which is what "I moved the rule and did not finish moving the caller"
#: looks like from the outside.
_VERDICTS = {
    tone: (_verdict.shape(tone), _verdict.word(tone))
    for tone in _verdict.STATES
}


def _verdict_pill(tone: str, detail: str) -> str:
    """The header's one-glance answer: shape, word, and the count behind it."""
    icon, word = _verdict.shape(tone), _verdict.word(tone)
    return (
        f"<div class='verdict {tone}'><span class='v-icon' aria-hidden='true'>"
        f"{icon}</span><span class='v-word'>{word}</span>"
        f"<span class='v-detail'>{html.escape(detail)}</span></div>"
    )


def company_verdict(data: Mapping[str, Any]) -> tuple[str, str, str]:
    """`(tone, word, sentence)` -- the one state this whole page reports.

    Derived from **ATTENTION severity and the evidence count**, which is what
    the header pill, the NOW section and `ops_status.py`'s exit code all say.
    Three renderings of one fact, never a fourth opinion.

    **"할 일 없음"과 "셀 것이 없음"을 구별한다.** The superseded
    version's first draft went straight from `not attention` to a green
    "지금 사람이 할 일은 없다". Measured on an empty tree -- no Events
    at all, which is what a machine on its first day has -- that is what it
    said, in green, above six zeroes. Every word true about a field and
    false about the company. The evidence count decides first, and only then
    the ATTENTION severity.
    """
    attention = list(data.get("attention") or [])
    model = data.get("model")
    # A model that could not be built answers nothing -- not zero. The
    # ATTENTION list is still real (it comes from `ops_status.py`'s own
    # renderers, which ran), so it still decides; what must not happen is
    # the fall-through to "셀 Event가 없다", which is a statement about the
    # company derived from a computation that failed.
    if model is None:
        if attention:
            return "bad", "조치 필요", (
                f"Control Tower Model을 만들지 못했다 — ATTENTION {len(attention)}건은 "
                "그대로 유효하다"
            )
        return "warn", "주의", (
            "Control Tower Model을 만들지 못했다 — 이 화면의 판정을 세울 수 없다"
        )
    p1 = sum(1 for line in attention if attention_severity(line)[0] == "P1")
    unknown = sum(1 for line in attention if attention_severity(line)[0] == "?")
    if p1 or unknown:
        detail = f"P1 {p1}건" + (f" · 미분류 {unknown}건" if unknown else "")
        return "bad", "조치 필요", f"조치가 필요하다 — {detail}"
    if attention:
        return "warn", "주의", f"확인이 필요하다 — {len(attention)}건"
    if not model.get("events_read"):
        # The same sentence the Notion page carries, and for the same reason
        # (C149): Events 0 with commits on the same days is not a quiet
        # company, it is delivery that did not arrive — the failure with no
        # other signal anywhere on this page. Measured on a one-day window
        # over the live tree: `events_read: 0`, one commit, 21 files.
        code_rows = 0
        for panel in model.get("panels") or []:
            if panel.get("key") == "CODE_CHANGES":
                code_rows = len(panel.get("rows") or [])
                break
        if code_rows:
            return "warn", "주의", (
                f"Event는 0건인데 같은 기간 Git에는 commit이 {code_rows}건 있다 "
                "— 일이 없었던 것이 아니라 보고가 도착하지 않았을 가능성이 크다"
            )
        return "warn", "주의", (
            "셀 Event가 없다 — '문제 없음'이 아니라 '판단할 증거가 없다'"
        )
    return "ok", "정상", "지금 사람이 할 일은 없다"


def _count_cell(value: Any, unit: str, tone: str, empty_note: str | None = None) -> str:
    """A count with its state in a word, or an honest 기록 없음.

    `None` is never rendered as `0`. That is the rule the whole file follows
    and the one a count tile is most likely to break, because a zero and a
    number nobody could read look identical once they are both grey.
    """
    if value is None:
        return _missing("이 값을 읽지 못했다")
    body = (
        f"<span class='state {tone}'>{html.escape(str(value))}"
        f"{html.escape(unit)}</span>"
        f"<span class='verdict-word {tone}'>{_VERDICTS[tone][1]}</span>"
    )
    if empty_note:
        body += f"<span class='sev-why'> {html.escape(empty_note)}</span>"
    return body


def _now_html(data: Mapping[str, Any]) -> str:
    """① 지금 회사 상태 -- the five-second answer, and nothing else (C133).

    The screen's first section, and the only one a reader who has five
    seconds will look at. What it holds is decided by that budget rather
    than by what happens to be available: the executive-dashboard rule this
    was measured against puts five to nine tiles on a primary view, and the
    superseded COMPANY strip spent three of its six on this machine's own
    housekeeping -- the Agent, the last backup, and the evidence date range.

    Those three did not stop being true; they stopped being *first*. They
    are in ④ 실행 · 자동화 now, beside the Runner state they belong with,
    which also removed this page's habit of answering "is the Runner
    alright" in two places that could disagree.

    Every tile carries a **word** for its state as well as a colour, and a
    tile whose value this machine has no record of says so rather than
    rendering a zero.
    """
    # `model is None` means `build_dashboard()` raised. Every tile below
    # that reads the model must then say 기록 없음 rather than a number --
    # see `company_verdict()` for the measurement.
    built = data.get("model") is not None
    model = data.get("model") or {}
    ops = data.get("ops") or {}
    attention = list(data.get("attention") or [])
    tone, _word, state = company_verdict(data)

    p1 = sum(1 for line in attention if attention_severity(line)[0] == "P1")
    decide = sum(1 for line in attention if _attention_kind(line) == "DECIDE")

    tiles = []

    # ① 지금 문제가 있는가
    tiles.append(
        _fact(
            "조치 필요",
            _count_cell(
                len(attention),
                "건",
                "bad" if p1 else ("warn" if attention else "ok"),
                None if attention else "지금 없음",
            ),
            "bad" if p1 else ("warn" if attention else ""),
        )
    )
    tiles.append(
        _fact(
            "사람 판단 대기",
            _count_cell(
                decide, "건", "warn" if decide else "ok", None if decide else "없음"
            ),
            "warn" if decide else "",
        )
    )

    # ② 무엇이 막혀 있는가
    blockers = _kpi_value(model, "open_blockers") if built else None
    tiles.append(
        _fact(
            "열려 있는 Blocker",
            _count_cell(
                blockers,
                "건",
                "bad" if blockers else "ok",
                "없음" if blockers == 0 else None,
            ),
            "bad" if blockers else "",
        )
    )

    # ③ 프로젝트는 어디까지 왔는가
    states = _project_states(model) if built else {}
    active = states.get("ACTIVE", 0)
    blocked = states.get("BLOCKED", 0)
    if not built:
        project_cell = _missing("Model을 만들지 못해 셀 수 없었다")
        project_tone = "warn"
    elif not states:
        project_cell = (
            "<span class='nil'>—</span>"
            "<span class='sev-why'> 이 기간에 움직인 Project가 없다</span>"
        )
        project_tone = ""
    else:
        project_cell = (
            f"<span class='state {'bad' if blocked else 'neutral'}'>"
            f"{active + blocked}</span>"
            f"<span class='sev-why'> 진행 {active} · 막힘 {blocked}</span>"
        )
        project_tone = "bad" if blocked else ""
    tiles.append(_fact("움직이는 Project", project_cell, project_tone))

    # ⑤ 시스템은 도는가 -- one tile here; the detail is in ④.
    run = ops.get("run")
    if run is None:
        runner = _missing("Run Manifest가 아직 없다")
        runner_tone = "warn"
    elif isinstance(run, dict) and run.get("error"):
        runner = _missing("Run Manifest를 읽지 못했다")
        runner_tone = "warn"
    else:
        days = run.get("days_ago")
        stale = days is not None and days >= 2
        # `_e()`, not `str()`. A Run Manifest that carries no
        # `overall_status` rendered the literal word `None` on the tile a
        # five-second reader looks at first — measured with `ops={"run": {}}`.
        # Every other value on this page goes through `_e()`, which is where
        # the em-dash-for-nothing rule lives; this one had been spelled out
        # by hand and missed it.
        runner = (
            f"<span class='state {'bad' if stale else 'ok'}'>"
            f"{_e(run.get('overall_status'))}</span>"
            + (
                f"<span class='sev-why'> {days}일 전</span>"
                if days is not None
                else ""
            )
        )
        runner_tone = "bad" if stale else ""
    tiles.append(_fact("마지막 실행", runner, runner_tone))

    # 침묵은 고장과 다르다 -- 세기만 하고 원인을 주장하지 않는다.
    if not built:
        tiles.append(
            _fact("조용한 Desktop", _missing("Model을 만들지 못해 셀 수 없었다"), "warn")
        )
    else:
        silent = _silent_desktops(model)
        fleet = len((_panel_of(model, "DESKTOPS") or {}).get("rows") or [])
        tiles.append(
            _fact(
                "조용한 Desktop",
                _count_cell(silent, f"/{fleet}", "warn" if silent else "ok")
                + f"<span class='sev-why'> {ops_status.SILENT_AFTER_DAYS}일 이상 "
                "무응답</span>",
                "warn" if silent else "",
            )
        )

    return (
        "<section class='company' id='company'>"
        "<h2>① 지금 회사 상태</h2>"
        f"<p class='company-state {tone}'>{html.escape(state)}"
        f"<span class='v-icon' aria-hidden='true'>{_VERDICTS[tone][0]}</span></p>"
        "<p class='sub'>이 줄은 아래 ②의 심각도에서 나온다 — 헤더 배지와 "
        "<code>ops_status.py</code>의 종료 코드가 말하는 것과 같은 사실이다.</p>"
        f"<div class='company-grid'>{''.join(tiles)}</div></section>"
    )


def _notion_sync_html(data: Mapping[str, Any]) -> str:
    """The two Notion syncs, side by side, never one status (C129).

    The distinction AGENT.md §6c spends a paragraph on:

        Runner의 Notion Sync   PROJECTS **Row**에 Event 상태를 쓴다.
                               Runner 일정으로 돌다.
        Dashboard publish      Notion **페이지**를 다시 쓴다.
                               사람이 명령을 실행할 때만 돌아간다.

    "앞의 것이 며칠 멈춰 있어도 뒤의 것은 계속 성공한다" -- so one status for
    both would be false about whichever one you did not mean.

    **The publish side has no local record, and that is said rather than
    guessed.** `publish_control_tower.py` writes its timestamp onto the
    Notion page and nothing on this machine, so this screen cannot report a
    last-publish time. Inventing one, or reusing the Runner's, is the exact
    merge this pair exists to prevent.

    C133 moved this out of a top-level section of its own and into ④, beside
    the Runner state it is about. It was the third-highest thing on the page
    and it is a subsystem's health -- priority ⑤ in the order this screen
    now follows, not ②.
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

    # **Whether, never what.** The two names, set and non-blank, and
    # nothing about their contents reaches this page.
    #
    # This exists because the two zeroes above it are not evidence of
    # health until a run has actually reached Notion, and because the
    # Notion page discloses this and the browser page did not — measured,
    # the same instant gave `ATTENTION 1건` here and `2건` there (C133).
    seen = ops.get("notion_credentials")
    if seen is None:
        credentials = _missing("확인하지 못했다")
    elif seen:
        credentials = (
            "<span class='state ok'>이 프로세스에 전달됨</span>"
        )
    else:
        credentials = (
            "<span class='state warn'>이 프로세스에 없음</span>"
            "<span class='sev-why'> — .env는 자동으로 읽히지 않는다. 이 화면의 "
            "ATTENTION은 그만큼 적을 수 있다</span>"
        )

    runner_card = (
        "<div class='sync'><h4>Runner의 Notion Sync</h4>"
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
        f"<dt>자격증명</dt><dd>{credentials}</dd>"
        "</dl></div>"
    )

    publish_card = (
        "<div class='sync'><h4>Dashboard publish</h4>"
        "<p class='who'>Notion <strong>페이지</strong>를 다시 쓴다 · "
        "사람이 <code>publish_control_tower.py</code>를 실행할 때만 돌아간다</p><dl>"
        "<dt>마지막 발행</dt><dd>"
        + _missing("이 머신에 기록이 없다 — 시각은 Notion 페이지 맨 위 '마지막 갱신'에 있다")
        + "</dd>"
        "<dt>자동 실행</dt><dd><span class='state warn'>없음</span>"
        "<span class='sev-why'> — 스스로 갱신되지 않는다</span></dd>"
        "<dt>지금 갱신하려면</dt><dd><code>python publish_control_tower.py</code></dd>"
        "</dl></div>"
    )

    return (
        "<div class='sync-pair'><h3>Notion — 두 가지다</h3>"
        "<p class='sub'>이 둘은 서로 다른 것을 서로 다른 일정으로 쓴다. "
        "한쪽이 며칠 멈춰 있어도 다른 쪽은 계속 성공하므로, 하나의 상태로 합치면 "
        "둘 중 어느 쪽에 대해서도 거짓이 된다.</p>"
        f"<div class='sync-grid'>{runner_card}{publish_card}</div></div>"
    )


def _execution_html(data: Mapping[str, Any]) -> str:
    """④ 실행 · 자동화 -- is the machinery running, and when did it last run.

    One section for every automated thing this company owns, because the
    reader's question is one question. Before C133 the answer was spread
    over four places: a Runner tile in the COMPANY strip, a NOTION SYNC
    section of its own two screens up, an AGENT paragraph inside a `<pre>`
    at the bottom, and a backup tile back in COMPANY -- so "자동화는
    정상인가" took four scroll positions and two rendering styles to
    answer.

    **다음 실행 is not on this screen, and the reason is stated rather
    than left as an omission.** Nothing in this repository holds a schedule:
    the Runner is started by a Windows Scheduled Task and the Agent by
    another, both outside the tree, and `scheduler/` is a catch-up
    calculation over dates that have already passed rather than a plan for
    dates that have not. A "다음 실행 —" cell would be a field this
    system cannot fill, and a blank one reads as a system that forgot.
    """
    ops = data.get("ops") or {}
    run = ops.get("run")

    # ---- Runner -------------------------------------------------------
    if run is None:
        runner_rows = (
            "<dt>마지막 실행</dt><dd>"
            + _missing("Run Manifest가 아직 없다 — 이 머신에서 한 번도 돌지 않았다")
            + "</dd>"
        )
    elif isinstance(run, dict) and run.get("error"):
        runner_rows = (
            "<dt>마지막 실행</dt><dd>"
            + _missing("Run Manifest를 읽지 못했다")
            + "</dd><dt>읽기 오류</dt><dd><code>"
            + html.escape(str(run.get("error")))
            + "</code></dd>"
        )
    else:
        days = run.get("days_ago")
        stale = days is not None and days >= 2
        status = str(run.get("overall_status") or "—")
        failed = run.get("failed") or []
        runner_rows = (
            "<dt>마지막 실행</dt><dd>"
            + html.escape(str(run.get("started_at") or "—"))
            + (
                f"<span class='sev-why'> {days}일 전</span>"
                if days is not None
                else ""
            )
            + "</dd><dt>결과</dt><dd>"
            + f"<span class='state {'bad' if stale or status == 'FAILED' else 'ok'}'>"
            + html.escape(status)
            + "</span></dd><dt>실패한 단계</dt><dd>"
            + (
                "<span class='state bad'>"
                + html.escape(", ".join(str(f) for f in failed))
                + "</span>"
                if failed
                else "<span class='state ok'>없음</span>"
            )
            + "</dd>"
        )
    runner_card = (
        "<div class='card'><h4>Runner</h4>"
        "<p class='who'>Event 수집 → Company History → Backup · "
        "<code>python run_company_ops.py</code></p>"
        f"<dl>{runner_rows}"
        "<dt>다음 실행</dt><dd>"
        + _missing("이 저장소에 일정이 없다 — Windows 작업 스케줄러가 가지고 있다")
        + "</dd></dl></div>"
    )

    # ---- Agent / Backup ------------------------------------------------
    agent = ops.get("agent")
    if not agent:
        agent_rows = (
            "<dt>상태</dt><dd>"
            + _missing("Agent state를 읽지 못했다 — 이 머신에 Agent가 없을 수도 있다")
            + "</dd>"
        )
    else:
        days = agent.get("days_ago")
        agent_rows = (
            "<dt>Desktop</dt><dd>"
            + html.escape(str(agent.get("desktop_id") or "—"))
            + "</dd><dt>마지막 실행</dt><dd>"
            + (
                f"<span class='state {'bad' if days >= 2 else 'ok'}'>{days}일 전</span>"
                if days is not None
                else "<span class='nil'>실행 기록 없음</span>"
            )
            + "</dd><dt>보내지 못한 Event</dt><dd>"
            + _count_cell(agent.get("outbox"), "건", "bad" if agent.get("outbox") else "ok")
            + "</dd>"
        )

    backup = ops.get("last_backup")
    if not backup:
        backup_row = "<dt>마지막 성공 백업</dt><dd>" + _missing(
            "성공한 백업이 아직 없다"
        ) + "</dd>"
    else:
        days = backup.get("days_ago")
        backup_row = (
            "<dt>마지막 성공 백업</dt><dd>"
            f"<span class='state {'warn' if (days or 0) >= 2 else 'ok'}'>"
            f"{days}일 전</span>"
            f"<span class='sev-why'> {html.escape(str(backup.get('at') or ''))}</span>"
            "</dd>"
        )

    machine_card = (
        "<div class='card'><h4>이 머신 — Agent / Backup</h4>"
        "<p class='who'>Agent는 이 Desktop의 Event를 보내고, Backup은 "
        "Company History를 이 머신 밖으로 내보낸다</p>"
        f"<dl>{agent_rows}{backup_row}</dl></div>"
    )

    return (
        "<section class='window exec' id='execution'>"
        "<h2>④ 실행 · 자동화</h2>"
        "<p class='sub'>이 회사의 자동화된 것들이 도는지, 그리고 마지막으로 "
        "돌았던 것이 언제인지. 숫자가 아니라 <b>기록</b>이 없는 칸은 그렇게 적혀 "
        "있다 — 0으로 바꾸지 않는다.</p>"
        f"<div class='sync-grid'>{runner_card}{machine_card}</div>"
        + _notion_sync_html(data)
        + "</section>"
    )


#: Where each panel goes on the page (C133).
#:
#: The superseded page rendered all ten in one flat `<h2>패널</h2>` list, in
#: model order, so `SPRINTS` -- a layer this system has **no source for** --
#: got the same card, the same width and the same position as `PROJECTS`,
#: which is the thing the company is actually made of. Measured on the empty
#: tree: the three unsourced panels were 38% of the rendered panel area.
#:
#: The routing is by **question**, not by shape:
#:
#:     ACTION     something is wrong right now                (never folded)
#:     PROJECTS   where the work has got to                   (its own section)
#:     RECENT     what changed lately                         (folded when empty)
#:     EVIDENCE   how the numbers were reached                (folded)
#:
#: `RISKS` moves between the first and the last depending on whether it has
#: rows, because an open Blocker is priority ① and an empty Risk table is
#: reference. Nothing is dropped from the page by this map -- every panel
#: still renders exactly once, which `EveryPanelReachesTheScreenTests`
#: checks against the model rather than against this dict.
_PANEL_PLACEMENT = {
    "METRICS": "KPI",
    # C149. Beside the numbers it frames, not in EVIDENCE where the fallback
    # would have put it: it is the section a CEO / CTO / COO opens to find
    # out which of these numbers is theirs, and what this system cannot
    # answer for them at all.
    "ROLE_KPI": "KPI",
    # Beside the numbers it re-reads. Not ACTION: a falling retention is a
    # trend to decide about, not an item to work today, and putting a trend at
    # the top of ② would push a real Blocker down the page.
    "COHORT": "KPI",
    "RISKS": "ACTION",
    "PROJECTS": "PROJECTS",
    "ACTIVITY": "RECENT",
    "COMPLETIONS": "RECENT",
    # Git's account of the same days as ACTIVITY's Event feed. RECENT is
    # exactly the question it answers.
    "CODE_CHANGES": "RECENT",
    "TEAMS": "EVIDENCE",
    "DESKTOPS": "EVIDENCE",
    "COMPANY_GOALS": "EVIDENCE",
    "SPRINTS": "EVIDENCE",
    "JUDGEMENTS": "EVIDENCE",
}


def panel_placement(panel: Mapping[str, Any]) -> str:
    """Which region of the page one panel belongs in.

    A panel this map has never heard of goes to `EVIDENCE`, not nowhere: an
    unknown panel is one nobody has decided about, and dropping it would
    make adding a panel upstream a silent no-op on the only surface a person
    reads.
    """
    key = str(panel.get("key") or "")
    where = _PANEL_PLACEMENT.get(key, "EVIDENCE")
    if where == "ACTION" and not (panel.get("rows") or []):
        # An empty Risk table is not an alarm. It is the sentence "이 기간의
        # 증거에 이 항목이 하나도 없었다", which belongs with the other
        # things a reader checks rather than at the top of the screen.
        return "EVIDENCE"
    return where


def _blockers_html(panels: Sequence[Mapping[str, Any]]) -> str:
    """Open Blockers and integrity faults -- never behind a disclosure.

    The rule this satisfies is explicit: P1 / Blocker / 승인 필요 items must
    not be collapsed. Rendered only when there is something in it, so a
    clean company does not carry an empty red box it will learn to ignore.
    """
    if not panels:
        return ""
    total = sum(len(p.get("rows") or []) for p in panels)
    return (
        "<section class='blockers' id='blockers'>"
        f"<h2>②-b 막혀 있는 것 · 무결성 결함 — {total}건</h2>"
        "<p class='sub'>사람이 쓴 Blocker 문장과, 증거 자체가 서로 어긋나는 "
        "경우다. 앞의 것은 업무가 멈춰 있다는 뜻이고, 뒤의 것은 이 화면의 "
        "숫자를 믿기 어렵다는 뜻이다.</p>"
        + "".join(_panel_html(p) for p in panels)
        + "</section>"
    )


def _projects_html(panels: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> str:
    """③ 진행 중인 Project -- promoted out of the panel list (C133).

    The one section a portfolio reader opens the page for, and it used to be
    the fourth card in an alphabetically-ordered stack. It gets its own
    heading, a state tally a reader can take in without reading the table,
    and the decision columns first (`_PANEL_COLUMN_ORDER`).

    The tally is **counted from the rendered rows**, so it cannot disagree
    with the table under it. A summary derived separately from the thing it
    summarises is the failure this whole project keeps removing.
    """
    if not panels:
        return ""
    states = _project_states(model)
    order = ("BLOCKED", "ACTIVE", "COMPLETE", "CANCELLED")
    tone_for = {
        "BLOCKED": "bad",
        "ACTIVE": "neutral",
        "COMPLETE": "ok",
        "CANCELLED": "warn",
    }
    chips = "".join(
        f"<span class='chip {tone_for.get(state, 'info')}'>"
        f"{html.escape(state)} <b>{states[state]}</b></span>"
        for state in order
        if states.get(state)
    ) + "".join(
        f"<span class='chip info'>{html.escape(state)} <b>{count}</b></span>"
        for state, count in sorted(states.items())
        if state not in order
    )
    # No sentence of its own when there is nothing to summarise: the
    # panel below says "해당 없음 — 이 기간의 증거에 이 항목이 하나도
    # 없었다. (원천은 있다)", which is the more precise of the two, and
    # two sentences about one emptiness is the duplication this redesign
    # exists to remove.
    summary = f"<p class='chips'>{chips}</p>" if chips else ""
    return (
        "<section class='projects' id='projects'>"
        "<h2>③ 진행 중인 Project</h2>"
        + summary
        + "".join(_panel_html(p) for p in panels)
        + "</section>"
    )


def _recent_html(panels: Sequence[Mapping[str, Any]]) -> str:
    """⑥ 최근 변화 -- open when something changed, folded when nothing did.

    `rollup.RECENT_LIMIT` already stops this growing with the workload. What
    it did not stop was two twenty-row tables sitting at full height above
    the KPI tiles on a screen where nothing had happened. A `<details>` that
    is `open` exactly when it has rows costs a reader nothing either way.
    """
    if not panels:
        return ""
    rows = sum(len(p.get("rows") or []) for p in panels)
    return (
        f"<details class='fold-section' id='recent'{' open' if rows else ''}>"
        f"<summary><span class='fold-h2'>⑥ 최근 변화</span>"
        f"<span class='sub'>{rows}건</span></summary>"
        + "".join(_panel_html(p) for p in panels)
        + "</details>"
    )


def _evidence_html(
    panels: Sequence[Mapping[str, Any]],
    model: Mapping[str, Any] | None,
    window: Mapping[str, Any] | None,
    blocks: Sequence[Mapping[str, Any]],
) -> str:
    """⑦ 근거 · 상세 -- everything a reader consults rather than scans.

    Four things live here and each was previously competing for the top of
    the page:

        데이터 Coverage   how much of the evidence these numbers cover
        기간 필터          the control, not the content
        나머지 패널        TEAMS / DESKTOPS, and the layers with no source
        터미널 출력        `ops_status.py`'s blocks, verbatim

    The last is the largest change. Six `<pre>` blocks of terminal text were
    the bottom third of the page, and three of them said what ① and ④ now
    say in fields -- so a reader met the Runner's state twice in two
    renderings that could disagree. They stay because they are the parity
    check between this screen and the terminal, and because a block whose
    shape this page does not model is still readable there. They are folded
    because "긴 로그를 첫 화면에 노출하지 않는다" is the rule.

    **The period filter opens itself when a period is set.** A control that
    is silently narrowing every number above it must not be the one thing
    behind a disclosure.
    """
    windowed = bool((window or {}).get("since") or (window or {}).get("until"))
    unsourced = [p for p in panels if str(p.get("status")) == "UNSOURCED"]
    sourced = [p for p in panels if str(p.get("status")) != "UNSOURCED"]

    parts = []
    if model is not None:
        parts.append(_coverage_html(model))
    parts.append(
        f"<details class='fold-section'{' open' if windowed else ''}>"
        "<summary><span class='fold-h2'>기간 필터</span>"
        + (
            "<span class='sub warn-text'>적용 중</span>"
            if windowed
            else "<span class='sub'>전체 기간</span>"
        )
        + "</summary>"
        + _window_html(window)
        + "</details>"
    )
    if sourced:
        parts.append(
            "<details class='fold-section'><summary>"
            "<span class='fold-h2'>나머지 패널</span>"
            f"<span class='sub'>{len(sourced)}개</span></summary>"
            + "".join(_panel_html(p) for p in sourced)
            + "</details>"
        )
    if unsourced:
        parts.append(
            "<details class='fold-section'><summary>"
            "<span class='fold-h2'>원천이 없는 계층</span>"
            f"<span class='sub'>{len(unsourced)}개 — 비어 있는 것이 아니라 "
            "물어볼 곳이 없다</span></summary>"
            + "".join(_panel_html(p) for p in unsourced)
            + "</details>"
        )
    if blocks:
        parts.append(
            "<details class='fold-section'><summary>"
            "<span class='fold-h2'>터미널 출력 (ops_status.py 그대로)</span>"
            f"<span class='sub'>{len(blocks)}개 블록</span></summary>"
            + _blocks_html(blocks)
            + "</details>"
        )
    return (
        "<section class='evidence' id='evidence'>"
        "<h2>⑦ 근거 · 상세</h2>"
        "<p class='sub'>위의 판단을 의심할 때 여는 곳이다. 매일 읽을 것은 아니다.</p>"
        + "".join(parts)
        + "</section>"
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
    rendered page, every block).

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
            else "<span class='badge ok'>정상</span>"
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
/* ------------------------------------------------ verdict pill (C133)
   Shape + word + count, in that order. WCAG 1.4.1: the colour is the
   third carrier of this state, never the only one. `margin-left:auto`
   keeps it right-aligned on a wide header and the narrow rule below
   drops it onto its own full-width row rather than letting it wrap
   into the timestamp. */
.verdict{margin-left:auto;padding:7px 14px;border-radius:999px;font-size:13px;
 display:flex;align-items:center;gap:8px;border:1px solid #30363d;background:#21262d}
.verdict .v-icon{font-size:11px;line-height:1}
.verdict .v-word{font-weight:800;letter-spacing:.5px}
.verdict .v-detail{color:#8b949e;font-size:12px;white-space:nowrap}
.verdict.bad{background:#4a1418;color:#ff9d9d;border-color:#8b2b32}
.verdict.bad .v-detail{color:#e8a9a9}
.verdict.warn{background:#2a2113;color:#e3b341;border-color:#7a5c11}
.verdict.warn .v-detail{color:#c9ad72}
.verdict.ok{background:#12261a;color:#7ee787;border-color:#2b6a3b}
.verdict.ok .v-detail{color:#8fb99a}
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
.kpi-src{font-size:11.5px;color:#8b949e;margin:0;overflow-wrap:anywhere}
.kpi-defs{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;
 margin:0;font-size:12px}
.kpi-defs dt{color:#e6edf3;white-space:nowrap}
.defs{background:#0d1117}
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
/* ------------------------------------------------ COHORT chart
   An inline SVG, no library and no script. `overflow-x:auto` so a year of
   cohorts scrolls sideways inside its own card instead of widening the page,
   which is the rule every wide table on this screen already follows. */
.cohort-chart{margin:0 0 10px;padding:0;overflow-x:auto}
.cohort-chart svg{display:block}
.ch-legend{color:#8b949e;font-size:11.5px;margin-top:4px;display:flex;
 flex-wrap:wrap;gap:4px 10px;align-items:center}
.ch-key{display:inline-flex;align-items:center;gap:5px}
.ch-key i{width:10px;height:10px;border-radius:2px;display:inline-block}
.ch-key i.ch-none{background:none;border:1px dashed #484f58}
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
.sev-src{font-size:11.5px;font-family:Consolas,monospace;color:#8b949e;
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
.fact-item .cov-v{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px}

/* ---------------------------------------------- NOTION sync (C129)
   Two syncs, side by side, never one status. */
.sync-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
.sync,.card{background:#0d1117;border:1px solid #21262d;border-radius:6px;
 padding:10px 12px}
.card h4{margin:0 0 2px;font-size:13px}
.card .who{font-size:11px;color:#6e7681;margin:0 0 8px}
.card dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:3px 10px;
 font-size:12px}
.card dt{color:#8b949e;white-space:nowrap}
.card dd{margin:0;overflow-wrap:anywhere}
.sync h3{margin:0 0 2px;font-size:13px}
.sync .who{font-size:11px;color:#6e7681;margin:0 0 8px}
.sync dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:12px}
.sync dt{color:#8b949e;white-space:nowrap}
.sync dd{margin:0;overflow-wrap:anywhere}

/* ---------------------------------------------- table legibility (C129)
   `td` had no break rule, so one long unbroken id widened the whole table
   and pushed every later column off-screen. */
td{overflow-wrap:anywhere}
/* ...but `anywhere` also lets the browser compute a cell's min-content as a
   single character, so a fifteen-column table compresses every column until
   short words break too. Measured on a probe tree at 1440px: `BLOCKED`
   rendered as `BLOCK` / `ED`, `COMPLETE` as `COMPL` / `ETE`, `IN_PROGRESS`
   as `IN_PROG` / `RESS` — the state words a reader scans the table for,
   split down the middle (C133).

   Two rules, not a retreat from `anywhere`: a floor under every cell so a
   column cannot be squeezed to nothing (the table scrolls inside its own
   `.scroll` container instead, which is what that container is for), and
   `nowrap` on the state words, which are short by construction and are the
   one thing in the table that must be readable at a glance. A 400-character
   `event_id` still wraps — that is what `anywhere` is still here for. */
th,td{min-width:5.5em}
td.state,td.tok{white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ts{white-space:nowrap;font-variant-numeric:tabular-nums;color:#c9d1d9}
.ts .ts-y{color:#6e7681}
.idcell{font-family:Consolas,monospace;font-size:11.5px}
.folded{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline}
.fold{background:#0d1117;border:1px solid #21262d;border-radius:4px;padding:1px 8px;
 font-size:11.5px}
.fold b{color:#8b949e;font-weight:600;margin-right:4px}

/* ================================================ C133 layout
   The page is seven regions in priority order rather than one flat
   stack of cards. These rules are what make the order visible: a
   region a reader must not miss is a `<section>` with a heading, and
   a region they consult is a `<details>` that looks like one. */

/* A keyboard user's way past the header straight to the work. The page
   had no skip link and the first tab stop was the JSON link. */
.skip{position:absolute;left:-9999px;top:0;z-index:10;padding:8px 14px;
 background:#1f6feb;color:#fff;border-radius:0 0 6px 0}
.skip:focus{left:0}

/* The verdict word beside a number or a state. The point of this class
   is that it exists at all: before it, twelve states on this page were
   carried by colour alone. */
.verdict-word{font-size:11px;font-weight:800;letter-spacing:.4px;
 padding:1px 7px;border-radius:4px;white-space:nowrap;border:1px solid transparent}
.verdict-word.ok{background:#12261a;color:#7ee787;border-color:#2b6a3b}
.verdict-word.warn{background:#2a2113;color:#e3b341;border-color:#7a5c11}
.verdict-word.bad{background:#4a1418;color:#ff9d9d;border-color:#8b2b32}
.verdict-word.info{background:#1c2129;color:#8b949e;border-color:#30363d}
.warn-text{color:#e3b341}

/* ---- (1) NOW ------------------------------------------------------- */
.company-state{display:flex;align-items:center;gap:8px}
.company-state .v-icon{font-size:12px}

/* ---- (2) ACTION ---------------------------------------------------- */
/* The remedy line. Never folded, including inside a P2's disclosure:
   an item worth showing is an item worth showing the remedy for. */
.att-do{margin:8px 0 0;padding:7px 10px;border-radius:5px;background:#0d1117;
 border-left:3px solid #58a6ff;color:#c9d1d9;font-size:12.5px;overflow-wrap:anywhere}
.att-do b{color:#58a6ff;margin-right:8px;font-size:11px;letter-spacing:.5px}
.att-do.none{border-left-color:#553a68;color:#a894b8}
.att-do.none b{color:#c9a0dc}
.att-group{font-size:13px;margin:14px 0 0;color:#ffd7d5;display:flex;
 align-items:baseline;gap:10px;font-weight:700}
.att-group.decide{color:#d8c8e8}
.att-group .sub{font-weight:400}

/* ---- (2b) BLOCKERS -------------------------------------------------- */
.blockers{background:#1d1113;border:1px solid #5c2126;border-radius:8px;
 padding:14px 18px;margin-bottom:16px}
.blockers h2{color:#ff9d9d;border:0;margin:0 0 4px}

/* ---- (3) PROJECTS --------------------------------------------------- */
.projects{margin-bottom:8px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
.chip{font-size:12px;padding:3px 10px;border-radius:999px;
 border:1px solid #30363d;background:#161b22;color:#8b949e;white-space:nowrap}
.chip b{margin-left:5px;font-size:13px}
.chip.bad{border-color:#8b2b32;background:#2d1113;color:#ff9d9d}
.chip.warn{border-color:#7a5c11;background:#2a2113;color:#e3b341}
.chip.ok{border-color:#2b6a3b;background:#12261a;color:#7ee787}
.chip.neutral{border-color:#1f4b7a;background:#0e1c2b;color:#79c0ff}

/* ---- (4) EXECUTION -------------------------------------------------- */
.exec .sync-grid{margin-bottom:12px}
.sync-pair{border-top:1px solid #21262d;padding-top:12px}
.sync-pair h3{font-size:13px;margin:0 0 4px;color:#e6edf3}
.sync h4{margin:0 0 2px;font-size:13px}

/* ---- (5) KPI -------------------------------------------------------- */
.kpi-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.kpi.k-warn{border-color:#7a5c11}
.kpi.k-bad{border-color:#8b2b32}
.kpi-src{max-width:100%}

/* ---- (6)(7) folded regions ------------------------------------------ */
/* A `<details>` that reads as a heading, so a reader can tell what is
   behind it without opening it. `list-style` is reset on both the
   element and the ::-webkit- pseudo because the two engines disagree. */
.fold-section{border:1px solid #21262d;border-radius:8px;margin-bottom:10px;
 background:#0f1319}
.fold-section>summary{cursor:pointer;padding:10px 16px;display:flex;
 align-items:baseline;gap:12px;flex-wrap:wrap;list-style:none}
.fold-section>summary::-webkit-details-marker{display:none}
.fold-section>summary::before{content:"\25b8";color:#8b949e;font-size:11px;
 display:inline-block;transition:transform .12s}
.fold-section[open]>summary::before{transform:rotate(90deg)}
.fold-section>summary:hover{background:#161b22}
.fold-h2{font-size:13px;font-weight:700;letter-spacing:.6px;color:#c9d1d9;
 text-transform:uppercase}
.fold-section>*:not(summary){margin-left:16px;margin-right:16px}
.fold-section>*:last-child{margin-bottom:14px}
.evidence>.fold-section .coverage,.evidence>.fold-section .window{margin-top:0}

/* ---------------------------------------------- narrow screens (C129)
   There was no @media rule at all. */
@media (max-width:760px){
  header{padding:10px 14px;gap:8px}
  header h1{font-size:16px}
  /* Full width and centred, so the one thing a phone must show is the
     one thing that cannot be pushed off the row by a long timestamp. */
  .verdict{margin-left:0;width:100%;justify-content:center}
  main{padding:14px 14px 48px}
  .kpis{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
  .kpi-value{font-size:22px}
  .company-grid,.cov-grid{grid-template-columns:1fr 1fr}
  .sync-grid{grid-template-columns:1fr}
  .att-tags{gap:6px}
  table{font-size:12px}
  th,td{padding:5px 7px}
  /* The indent that separates a folded region's body from its summary
     costs 32px of a 360px screen, which is a column of a table. */
  .fold-section>*:not(summary){margin-left:8px;margin-right:8px}
  .blockers{padding:12px 12px}
  .sync dl{grid-template-columns:1fr;gap:0 0}
  .sync dt{margin-top:6px}
}
@media (max-width:420px){
  .cov-grid,.kpis{grid-template-columns:1fr}
  /* NOT `.company-grid`. Measured at 390px with one tile per row: the first
     ATTENTION item sat at y=881, below the fold of every phone, and ATTENTION is
     the section a five-second reader is supposed to reach. Two columns of
     ~175px hold every tile in this strip; the KPI tiles carry a 28px number
     and genuinely need the width. */
  .company-grid{grid-template-columns:1fr 1fr}
  .company .sub{font-size:11px}
  .verdict .v-detail{display:none}
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
    """The page, in the order a person reads it (C133).

    Seven regions, and the order is the answer to a question rather than a
    tour of the data model:

        ① 지금 회사 상태     is anything wrong                (5 seconds)
        ② 지금 해야 할 일     what do I do about it            (never folded)
        ②-b 막혀 있는 것     what is stuck                    (only when it is)
        ③ 진행 중인 Project   where has the work got to
        ④ 실행 · 자동화      is the machinery running
        ⑤ 핵심 지표            the numbers, each with a verdict
        ⑥ 최근 변화            what changed                     (folded when empty)
        ⑦ 근거 · 상세           how any of it was reached        (folded)

    The superseded order was COMPANY, ATTENTION, NOTION SYNC, 기간 필터,
    Coverage, KPI, 패널, 운영 상태 -- a subsystem's sync status third, a
    form control fourth, and the projects the company is made of somewhere
    inside a flat stack of ten equal cards.

    Nothing is deleted by the reordering. Every panel the model builds still
    renders exactly once; the six terminal blocks are still verbatim; the
    `CONTROL TOWER` parity check is still there. What changed is which of
    them a reader meets first.
    """
    attention = data.get("attention") or []
    model = data.get("model")
    tone, _word, sentence = company_verdict(data)
    verdict = _verdict_pill(
        tone,
        f"ATTENTION {len(attention)}건" if attention else "지금 할 일 없음",
    )

    if model is not None:
        panels = list(model.get("panels") or [])
        by_key = {p["key"]: p for p in panels}
        ordered = [by_key[k] for k in _PANEL_ORDER if k in by_key]
        ordered += [p for p in panels if p["key"] not in _PANEL_ORDER]
        regions: dict[str, list[Mapping[str, Any]]] = {
            "KPI": [],
            "ACTION": [],
            "PROJECTS": [],
            "RECENT": [],
            "EVIDENCE": [],
        }
        for panel in ordered:
            regions[panel_placement(panel)].append(panel)
        # `regions["KPI"]` was computed and **never read** — this section
        # rendered `by_key["METRICS"]` directly, so any second panel mapped
        # to the KPI region was dropped from the page in silence. Nothing
        # caught it: `EveryPanelReachesTheScreenTests` passed because METRICS
        # was the only panel that had ever been mapped there, and the map is
        # what a person edits when adding one. Found in C149 while placing
        # `ROLE_KPI` — which would have vanished.
        #
        # METRICS keeps its tile rendering (`_kpi_html`, which reads the
        # `key`/`value` shape and paints a verdict); every other KPI-region
        # panel renders as an ordinary table. Neither is dropped.
        kpi_html = (
            "<section class='kpi-section' id='kpi'>"
            "<h2>⑤ 핵심 지표</h2>"
            "<p class='sub'>숫자 옆의 낱말이 이 화면의 판정이다. 증거가 하나도 "
            "없으면 어느 숫자도 판정하지 않는다. 방향이 있는 "
            "지표만 <b>정상 / 주의</b>로 읽히고, 나머지는 <b>참고</b>다 — "
            "조용한 주가 나쁜 주는 아니기 때문이다.</p>"
            + "".join(
                _kpi_html(panel, measured=bool(model.get("events_read")))
                if panel["key"] == "METRICS"
                # The one panel whose comparison is across rows rather than
                # down one, so it gets a chart above its table. Everything
                # else in this region is a table and stays one.
                else _cohort_html(panel)
                if panel["key"] == "COHORT"
                # 35 rows, 22 of them DATA REQUIRED, is a wall rather than a
                # panel — measured on the live page. Same rows, answers first.
                else _role_kpi_html(panel)
                if panel["key"] == "ROLE_KPI"
                else _panel_html(panel)
                for panel in regions["KPI"]
            )
            + "</section>"
        )
        schema = html.escape(str(model.get("schema_version")))
        middle = (
            _blockers_html(regions["ACTION"])
            + _projects_html(regions["PROJECTS"], model)
            + _execution_html(data)
            + kpi_html
            + _recent_html(regions["RECENT"])
            + _evidence_html(
                regions["EVIDENCE"], model, data.get("window"), data.get("blocks") or []
            )
        )
    else:
        middle = (
            "<section class='error'><h2>Control Tower Model을 만들지 못했다</h2>"
            "<p>아래 운영 블록은 그대로 유효하다. 이 화면의 패널·KPI·Coverage만 "
            "이번 요청에서 비어 있다.</p>"
            f"<pre>{html.escape(str(data.get('model_error') or ''))}</pre></section>"
            + _execution_html(data)
            + _evidence_html([], None, data.get("window"), data.get("blocks") or [])
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
        # this server answered **404** -- measured in its own log, on the
        # line after the page request. A 404 in the network tab beside a
        # status screen is one more thing an operator has to rule out.
        # A data: URI so nothing new is served and nothing is fetched;
        # the three bars are the P1/P2/OK colours the page already uses.
        "<link rel='icon' href='data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20viewBox=%220%200%2016%2016%22%3E%3Crect%20width=%2216%22%20height=%2216%22%20rx=%223%22%20fill=%22%230d1117%22/%3E%3Crect%20x=%223%22%20y=%227%22%20width=%222.5%22%20height=%226%22%20fill=%22%237ee787%22/%3E%3Crect%20x=%226.75%22%20y=%224%22%20width=%222.5%22%20height=%229%22%20fill=%22%23e3b341%22/%3E%3Crect%20x=%2210.5%22%20y=%229%22%20width=%222.5%22%20height=%224%22%20fill=%22%23ff7b72%22/%3E%3C/svg%3E'>"
        f"<style>{_CSS}</style></head><body>"
        "<a class='skip' href='#attention'>바로 ② 해야 할 일로</a>"
        "<header><h1>DOJOONPASS Control Tower</h1>"
        f"<span class='meta'><time id='gen' datetime='{generated}'>{generated}</time>"
        "<span id='age' hidden></span>"
        f" · schema {schema}"
        " · <a href='/api/dashboard.json'>JSON</a>"
        " · <a href='/'>새로고침</a>"
        f"{build}</span>"
        f"{verdict}</header><main>"
        + _now_html(data)
        + _attention_html(attention, data.get("blocks") or [])
        + middle
        + "<footer>읽기 전용. 이 화면은 아무것도 쓰지 않고, 잠그지 않고, "
        "Notion에 접속하지 않는다. 숫자의 출처는 "
        "<code>runtime/events/processed/</code>이고, ⑦의 터미널 출력은 "
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
        now = businessdate.now()
        # **Reading the data and building the body are one try (C146).**
        #
        # They were two, and only the first one was guarded. `gather()`
        # answered a failure with 500 and the traceback — the posture this
        # file states in as many words one screen up, *"reported on the page,
        # never swallowed"* — while `render_html()` and `json.dumps()` were
        # called outside it. An exception in either escaped `do_GET()`,
        # `BaseHTTPRequestHandler` wrote nothing, and the socket closed.
        #
        # Measured against the running server, `render_html` made to raise:
        #
        #     gather() raises        HTTP/1.0 500, 1,007 bytes, names the error
        #     render_html() raises   **no status line, 0 bytes**
        #     (same request, /api/dashboard.json)   HTTP/1.0 200
        #
        # So the browser got "this site can't be reached" for a *running*
        # server holding readable data, and the one artefact that could have
        # said otherwise went to stderr. `render_html()` formats untrusted
        # Event content — `_authored()`, folding, the KPI tiles, the
        # attention grouping — which is exactly where this project keeps
        # finding the unexpected type.
        #
        # `_send()` stays outside: a write that fails because the client
        # disconnected is not a rendering failure, and answering it with a
        # second `_send()` on a dead socket would be the worse error.
        try:
            data = gather(now, since=since, until=until)
            if path == "/api/dashboard.json":
                body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            elif path == "/":
                body = render_html(data).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            else:
                # Unchanged: an unknown path still pays for `gather()` before
                # it is refused, because the refusal is decided here and not
                # before — moving it would be a different change.
                body = None
        except Exception:  # noqa: BLE001
            detail = traceback.format_exc()
            self._send(
                500,
                f"<pre>{html.escape(detail)}</pre>".encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if body is None:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, body, content_type)

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
