"""Windows Task Scheduler, read back — the half of the deployment nothing looked at.

Everything this system does unattended is started by the Windows scheduled
tasks that `scripts/install_*_task.ps1` register:

    DOJOONPASS_COMPANY_OPS_DAILY            Desktop 4, the Runner
    DOJOONPASS_COMPANY_OPS_AGENT_<ID>       each Desktop's Agent
    DOJOONPASS_COMPANY_OPS_PUBLISH          the Control Tower page

The third is optional and the other two are not; `PUBLISH_TASK_NAME` says
why, and `ops_status._print_schedule()` is where that distinction is spent.

Until this module existed, **no Python in this repository had ever asked
Windows whether any of them was there.** The installers write the task and
verify their own write (`Get-ScheduledTask` immediately after
`Register-ScheduledTask`, because "reporting success on a task that is not
there would send the operator away believing Company History is scheduled
when nothing is"). After that one moment, nothing ever looks again.

**The gap is not hypothetical; the code names it.** `ops_status.py`'s Runner
staleness check ends its own message with an instruction it cannot carry out:

    Runner가 {n}일째 실행되지 않았다 … Task Scheduler 등록 상태를 확인해야 한다

That sentence is the whole of this project's answer to "the scheduled job
stopped", and it arrives only after `SILENT_AFTER_DAYS` have already been
lost, says nothing about *why*, and hands the actual check to a person who
must open a different tool.

**And there is a state where nothing else can see the failure at all.** Every
diagnosis `ops_status.py` makes is derived from files this system wrote. If
the task fires and `python` is not on PATH, or the task's working directory
is gone, or the task was disabled by a password change, the process either
never starts or dies before `oplog` opens a file. No log line, no manifest,
no lock — the run leaves *no evidence anywhere in `runtime/`*, and a tree
with no evidence is indistinguishable from a machine that was simply switched
off. Task Scheduler is the only witness, and it holds a `LastTaskResult` that
says exactly which of those happened.

So: read-only, one query, no registration and no repair.

**Why PowerShell and not `schtasks.exe`.** `schtasks` is the obvious choice —
one exe, no shell — and it is unusable here because its output is localized.
Measured on this machine (Korean Windows 11), asking for a task that does not
exist:

    schtasks /query /tn DOJOONPASS_COMPANY_OPS_DAILY /fo LIST /v
    오류: 잘못된 인수/옵션 …            (and the field names of a *successful*
                                          query are Korean too)

Parsing that means matching translated field labels, which is the same defect
`backup/git_ops._git_environment()` sets `LC_ALL=C` to avoid — a classifier
that quietly stops matching on a machine with a different message catalog.
`Get-ScheduledTask` / `Get-ScheduledTaskInfo` return *objects*, and the
property names this module reads (`State`, `LastTaskResult`, `NextRunTime`)
are invariant. The 0.6 s that `powershell -NoProfile` costs (measured) buys a
parser that does not depend on the machine's display language.

**Why NDJSON and not one JSON array.** Windows PowerShell 5.1's
`ConvertTo-Json` has no `-AsArray`, and it unwraps a single-element array
into a bare object — so a query for one task and a query for two tasks would
return different shapes, and the one-task shape is the one a single-Desktop
machine always takes. One compact object per line removes the special case.
The same call also formats every `DateTime` with `.ToString('o')`: 5.1's
`ConvertTo-Json` emits `/Date(943887600000)/` for a `DateTime` field, which is
a WCF serialization detail this module refuses to learn.

**This module does not decide what is wrong.** It reports what Windows said —
present, state, last result, last/next run — and `classify()` turns that into
the small vocabulary an operator needs. Whether a given verdict deserves to
be raised as ATTENTION belongs to the report that displays it, exactly as
`runsummary` describes a run and `ops_status.py` decides what to say about it.

A leaf, like `oplog`, `cli`, `runsummary` and `businessdate`: it imports
nothing from this project, so every consumer may sit above it and it closes
no cycle. It is deliberately **not** in `ALLOWED_LEAVES` (the list `monthly`
may import) — it reaches outside the process for data, which is precisely
what that list excludes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Callable, NamedTuple, Sequence

#: The Runner's task, from `scripts/install_runner_task.ps1`.
#:
#: **Not duplicated in the installer — the installer asks for it.** This is
#: the sole spelling of the name. Right after it finds python on PATH,
#: `scripts/install_runner_task.ps1` runs a two-line probe through it —
#: `import schedtask`, then print `RUNNER_TASK_NAME` and the
#: `scheduled_log_name()` for it — and refuses to register at all if that
#: comes back empty or non-zero. All three installers use the same shape.
#: The direction is this way because Python is the side that *reasons* about
#: these names (`scheduled_log_name()` maps name to log, `agent_task_name()`
#: builds the per-Desktop form, and `ops_status.py` must know the name to ask
#: Windows anything); PowerShell only writes it down once, at install time.
#:
#: Until C138 §15 there really were two copies held in step by a test that
#: grepped both sides. Comparing copies detects drift; it does not prevent
#: it. The copies are gone, and what stands in their place is the opposite
#: assertion — `test_no_installer_hard_codes_a_task_name` fails if a literal
#: comes back, and `test_every_installer_asks_this_module_for_both` fails if
#: an installer stops asking.
RUNNER_TASK_NAME = "DOJOONPASS_COMPANY_OPS_DAILY"

#: The Agent's task prefix. `agent_task_name()` appends the Desktop id, and
#: `scripts/install_agent_task.ps1` asks this module for the finished name
#: rather than building one of its own — see `RUNNER_TASK_NAME`.
AGENT_TASK_PREFIX = "DOJOONPASS_COMPANY_OPS_AGENT_"

#: The Control Tower publish, from `scripts/install_publish_task.ps1`.
#:
#: **Optional, and the only one of the three that is.** The Runner and the
#: Agent are how work becomes Company History; this is how the result
#: reaches the people who never open a terminal. An operator who reads the
#: browser Dashboard instead is not misconfigured, so its absence is
#: reported as a fact and never raised — see `ops_status._print_schedule()`.
PUBLISH_TASK_NAME = "DOJOONPASS_COMPANY_OPS_PUBLISH"

#: Where each installer sends the scheduled run's console output, relative
#: to `runtime/logs/`.
#:
#: **This file did not exist until C138, and neither did the redirection.**
#: The action both installers registered was `python.exe <entrypoint>` with
#: nothing after it, so a scheduled run's stdout and stderr went to handles
#: nothing read. Five entrypoints carry a measured comment about
#: `line_buffering=True` being needed "under `> log 2>&1`, which is how a
#: scheduled run is captured" — they were protecting the ordering of output
#: that no installer ever captured.
#:
#: It matters because it is the only record of the failures that leave no
#: other trace. `ops_status.py` derives every other thing it says from files
#: this system wrote, and a run that dies before writing one — python off
#: PATH, an unset `COMPANY_OPS_*`, an import error — leaves nothing but a
#: `LastTaskResult`. That number says "exit 1"; this file says which
#: variable was missing.
#:
#: Not duplicated in the installers either, for `RUNNER_TASK_NAME`'s reason:
#: each one calls `schedtask.scheduled_log_name()` for the filename it will
#: append to, and `test_no_installer_hard_codes_a_log_filename` fails if a
#: second spelling appears. A second spelling would send an operator — and
#: `ops_status._scheduled_log_tail()`, which reads this file when a
#: scheduled run fails — to a path nothing writes.
SCHEDULED_LOG_NAMES = {
    RUNNER_TASK_NAME: "scheduled_runner.log",
    AGENT_TASK_PREFIX: "scheduled_agent.log",
    PUBLISH_TASK_NAME: "scheduled_publish.log",
}


def scheduled_log_name(task_name: str) -> str | None:
    """The log file `task_name`'s scheduled action appends to, or None.

    `None` rather than a guess for a name neither installer registers: this
    is used to point an operator at a file, and naming one that does not
    exist sends them looking for evidence that was never written.
    """
    if task_name in SCHEDULED_LOG_NAMES:
        return SCHEDULED_LOG_NAMES[task_name]
    if task_name.startswith(AGENT_TASK_PREFIX):
        return SCHEDULED_LOG_NAMES[AGENT_TASK_PREFIX]
    return None


#: What Windows reports for a task that has been registered but never run.
#:
#: `0x00041303 SCHED_S_TASK_HAS_NOT_RUN`. Measured on this machine, on a task
#: installed by its vendor and never triggered:
#:
#:     LastTaskResult 267011   LastRunTime 1999-11-30T00:00:00+09:00
#:
#: Both halves matter. The result code is not a failure, and the timestamp is
#: a **sentinel, not a run** — a report that printed it verbatim would claim
#: the job last ran in 1999.
RESULT_HAS_NOT_RUN = 267011

#: `0x00041301 SCHED_S_TASK_READY` — the task is running right now. Reported
#: as a state, not a failure: a Runner catching up many days can legitimately
#: still be inside its two-hour `ExecutionTimeLimit` when this is read.
RESULT_STILL_RUNNING = 267009

#: `0x00041306 SCHED_S_TASK_TERMINATED` — Windows stopped the task, which is
#: what `ExecutionTimeLimit` expiring looks like from here. Distinguished from
#: an ordinary non-zero exit because the fix is different: the run was not
#: rejected, it was cut off part-way, and docs/07 §55's duplicate protection
#: means the *next* trigger is what resumes it.
RESULT_TERMINATED = 267014

#: `0x8004131F SCHED_E_ALREADY_RUNNING` — the previous instance was still
#: going when this trigger fired, and `-MultipleInstances IgnoreNew` dropped
#: the new one. Also not a failure: it is that setting working as docs/07 §55
#: intends, on top of the application's own lock.
RESULT_ALREADY_RUNNING = 2147750687

#: The `LastRunTime` Windows reports for a task that has never run. Compared
#: as a date prefix rather than a full instant: the sentinel is a local
#: midnight, so its offset is the reading machine's and its text is not fixed.
NEVER_RUN_SENTINEL_DATE = "1999-11-30"

#: Codes that are not a failed run. Everything else is reported as one,
#: including plain `1` — which is this project's own "설정 오류" exit code
#: (AGENT.md §6) and the single likeliest thing a broken deployment produces.
NON_FAILURE_RESULTS = frozenset(
    {0, RESULT_HAS_NOT_RUN, RESULT_STILL_RUNNING, RESULT_ALREADY_RUNNING}
)

#: What a task name may contain before it is embedded in the query script.
#:
#: Two independent jobs, and it is worth saying both because either alone
#: would justify a weaker pattern:
#:
#:   * **Injection.** The names are built from `AGENT_TASK_PREFIX` plus a
#:     Desktop id that ultimately comes from `COMPANY_OPS_PROFILE`, an
#:     environment variable. `resolve_profile()` constrains it today, but this
#:     module has no way to know that its caller checked, and the value is
#:     interpolated into a PowerShell string literal. A name that cannot hold
#:     a quote cannot end one.
#:   * **Encoding.** PowerShell writes captured stdout in the console's
#:     codepage (cp949 on this machine), not UTF-8. ASCII-only names keep the
#:     JSON this module parses ASCII, so the decode below cannot mangle the
#:     one field that identifies which task a row is about.
_TASK_NAME_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,200}\Z")

#: Long enough that a slow or contended machine is not reported as a failure,
#: short enough that `ops_status.py` — a tool a person is waiting on — cannot
#: hang on it. Measured cost of the real call on this machine: 0.61 s.
QUERY_TIMEOUT_SECONDS = 30.0


class ScheduledTaskStatus(NamedTuple):
    """What Windows said about one task, or why it could not be asked.

    `query_error` is not an alternative to the other fields, it is a
    *different kind of answer*: with it set, `present` is False because
    nothing is known, not because Windows said the task is absent. Conflating
    those two would report "아무것도 예약돼 있지 않다" on a machine where the
    query merely timed out — a false alarm about the one subject an operator
    would drop everything for.
    """

    name: str
    present: bool
    state: str | None = None
    last_result: int | None = None
    last_run: str | None = None
    next_run: str | None = None
    missed_runs: int | None = None
    query_error: str | None = None

    #: The registered action, as `Execute` followed by `Arguments`.
    #:
    #: Read because a task can be registered, enabled, and firing on time
    #: while still throwing away everything it prints. That is what every
    #: task registered before C138 does, and re-running an installer is the
    #: only thing that changes it -- Windows keeps the action it was given.
    #: `None` when the task is absent or the query could not read it.
    action_command: str | None = None

    #: How many actions the task has, of which `action_command` is the first.
    #:
    #: The query has always asked for this -- `_ROW` emits it, and says why:
    #: "reporting on its first action as though it were the whole thing would
    #: be a guess, so `action_count` travels with it and the caller can see
    #: that this is a partial view". **It did not travel.** The parser
    #: dropped the field, so `discards_console_output()` answered a flat
    #: `False` ("this task keeps its output") for a two-action task after
    #: reading one of them -- the exact confusion between "we could not
    #: check" and "it is fine" that this class refuses everywhere else.
    #:
    #: `None` for an absent task, a failed query, or output from a query
    #: that predates the field.
    action_count: int | None = None

    @property
    def has_ever_run(self) -> bool:
        """False for a task Windows has registered but never triggered.

        Reads the sentinel date rather than the result code because the two
        can disagree: a task that ran once and was then re-registered keeps
        its `LastRunTime` while `LastTaskResult` is reset.
        """
        if self.last_run is None:
            return False
        return not self.last_run.startswith(NEVER_RUN_SENTINEL_DATE)


def agent_task_name(desktop_id: str) -> str:
    """The Agent task name for a Desktop id, the way the installer builds it."""
    return f"{AGENT_TASK_PREFIX}{desktop_id}"


# ------------------------------------------------------------------ verdicts

#: `classify()`'s vocabulary. Strings rather than an Enum for the reason
#: `runsummary` gives for its own: the consumer is a report, the values are
#: printed, and a caller in `ops_status.py` compares them against literals.
UNKNOWN = "UNKNOWN"          # the query itself did not answer
NOT_REGISTERED = "NOT_REGISTERED"
DISABLED = "DISABLED"
NEVER_RUN = "NEVER_RUN"
LAST_RUN_FAILED = "LAST_RUN_FAILED"
LAST_RUN_TERMINATED = "LAST_RUN_TERMINATED"
RUNNING = "RUNNING"
HEALTHY = "HEALTHY"

#: Verdicts that mean a person has to do something. `RUNNING` and `NEVER_RUN`
#: are deliberately outside it: the first is normal, and the second is the
#: state every correctly-installed task is in until its first trigger fires —
#: raising it would make a successful install look broken for one morning.
#:
#: `UNKNOWN` is outside it too, and that is the harder call. A query that
#: could not run says nothing about the task, and a daily ATTENTION line on
#: every non-Windows checkout would be exactly the noise that gets the whole
#: block ignored. It is still *printed*, so the absence of an answer is
#: visible where the answer would have been.
NEEDS_ATTENTION = frozenset(
    {NOT_REGISTERED, DISABLED, LAST_RUN_FAILED, LAST_RUN_TERMINATED}
)


def classify(status: ScheduledTaskStatus) -> str:
    """One verdict for one task, in the order the questions actually matter.

    Ordered rather than scored: a disabled task's `LastTaskResult` is whatever
    it was when it last ran, often `0`, so asking about the result first
    reports a task that can never fire again as `HEALTHY`. Registration comes
    before enablement for the same reason.
    """
    if status.query_error is not None:
        return UNKNOWN
    if not status.present:
        return NOT_REGISTERED
    if (status.state or "").casefold() == "disabled":
        return DISABLED
    if status.last_result == RESULT_STILL_RUNNING or (
        (status.state or "").casefold() == "running"
    ):
        return RUNNING
    if status.last_result == RESULT_TERMINATED:
        return LAST_RUN_TERMINATED
    if not status.has_ever_run and status.last_result in (None, RESULT_HAS_NOT_RUN):
        return NEVER_RUN
    if status.last_result is not None and status.last_result not in NON_FAILURE_RESULTS:
        return LAST_RUN_FAILED
    return HEALTHY


def describe_result(code: int | None) -> str:
    """A `LastTaskResult` in words, including the ones that are not failures.

    The hex form is kept in every message: it is what Task Scheduler's own UI
    shows and what an operator will search for, and the decimal form
    PowerShell returns (`267011`) matches nothing.
    """
    if code is None:
        return "결과 없음"
    known = {
        0: "정상 종료",
        RESULT_HAS_NOT_RUN: "아직 한 번도 실행되지 않음",
        RESULT_STILL_RUNNING: "실행 중",
        RESULT_TERMINATED: "Windows가 중단시킴 (시간 제한 초과 등)",
        RESULT_ALREADY_RUNNING: "이전 실행이 아직 끝나지 않아 건너뜀",
    }
    # This project's own exit codes, from AGENT.md §6 / docs/14 §4. Named
    # because a scheduled task's `LastTaskResult` *is* the process exit code
    # for anything that actually started, and these three are the codes this
    # deployment produces on purpose.
    ours = {
        1: "설정 오류로 종료 (exit 1)",
        2: "FAILED로 종료 (exit 2)",
        3: "사람의 확인이 필요한 상태로 종료 (exit 3)",
    }
    if code in known:
        return known[code]
    if code in ours:
        return ours[code]
    # `& 0xFFFFFFFF` because PowerShell returns `LastTaskResult` as a signed
    # 32-bit value for the HRESULT range, and `-2147024894` printed as
    # `-0x7FF8FFFE` matches nothing an operator can look up.
    return f"실패 (0x{code & 0xFFFFFFFF:08X})"


# ------------------------------------------------------------------ the query


#: One task's row, as PowerShell. `$t` is the task and `$i` its info.
#:
#: A named constant because two loops emit it — the by-name pass and the
#: by-prefix discovery below — and a row shape that drifted between them
#: would give the same task two different sets of fields depending on how it
#: was found.
_ROW = (
    "  $i = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath '\\'"
    " -ErrorAction SilentlyContinue\n"
    "  [pscustomobject]@{\n"
    "    name = [string]$t.TaskName\n"
    "    present = $true\n"
    "    state = [string]$t.State\n"
    "    last_result = if ($null -eq $i) { $null } else { [int]$i.LastTaskResult }\n"
    "    last_run = if ($null -eq $i -or $null -eq $i.LastRunTime) { $null }"
    " else { $i.LastRunTime.ToString('o') }\n"
    "    next_run = if ($null -eq $i -or $null -eq $i.NextRunTime) { $null }"
    " else { $i.NextRunTime.ToString('o') }\n"
    "    missed = if ($null -eq $i) { $null } else { [int]$i.NumberOfMissedRuns }\n"
    # The first action only. All three installers register exactly one, and
    # a task with several is one a person built by hand -- reporting on its
    # first action as though it were the whole thing would be a guess, so
    # `action_count` travels with it and the caller can see that this is a
    # partial view. It reaches `ScheduledTaskStatus.action_count`, and
    # `discards_console_output()` declines to answer when it exceeds one.
    "    action = $(if (@($t.Actions).Count -gt 0) {"
    " $a = @($t.Actions)[0];"
    " (([string]$a.Execute) + ' ' + ([string]$a.Arguments)).Trim() } else { $null })\n"
    "    action_count = @($t.Actions).Count\n"
    "  } | ConvertTo-Json -Compress\n"
)


def build_query(names: Sequence[str], prefixes: Sequence[str] = ()) -> str:
    """The PowerShell that answers for `names`, one JSON object per line.

    Raises `ValueError` for a name `_TASK_NAME_RE` rejects — before anything
    is interpolated, so the refusal cannot be the thing that runs.

    `-TaskPath '\\'` pins the lookup to the root folder, which is where all
    three installers register (`Register-ScheduledTask` with no
    `-TaskPath`). Without it `Get-ScheduledTask -TaskName X` matches any
    folder, and a same-named task somewhere else in the tree would be
    reported as ours.

    **`prefixes` asks the other question.** By name alone this can only
    answer "is the task I expected registered", and the Agent's task name
    carries a Desktop id (`..._AGENT_DESKTOP_1`). So a machine re-installed
    with a different `-DesktopId` keeps the old task *and* gains a new one —
    two Agent tasks, both firing at logon — and a query naming only today's
    `COMPANY_OPS_PROFILE` sees one of them and calls the other absent.
    `run_agent.py` spends twenty lines on what that state costs
    (`AgentStateMismatchError`, and uncollected dates skipped with no error
    anywhere); nothing could see the pair that causes it.

    The `*` is appended here rather than accepted from a caller, so the only
    wildcard that can reach `Get-ScheduledTask` is this one — `_TASK_NAME_RE`
    already refuses a caller-supplied one.
    """
    for name in (*names, *prefixes):
        if not _TASK_NAME_RE.match(name):
            raise ValueError(
                f"조회할 수 없는 Task 이름입니다: {name!r} — "
                f"영문/숫자/._- 만 허용합니다"
            )

    # `SilentlyContinue` at script scope, plus `-ErrorAction` on each call: a
    # missing task must be an empty result, not a terminating error that
    # loses the answers for every *other* name in the batch.
    script = "$ErrorActionPreference = 'SilentlyContinue'\n"

    if names:
        literal = ", ".join(f"'{name}'" for name in names)
        script += (
            f"foreach ($n in @({literal})) {{\n"
            "  $t = Get-ScheduledTask -TaskName $n -TaskPath '\\'"
            " -ErrorAction SilentlyContinue\n"
            "  if ($null -eq $t) {\n"
            "    [pscustomobject]@{ name = $n; present = $false }"
            " | ConvertTo-Json -Compress\n"
            "    continue\n"
            "  }\n"
            + _ROW
            + "}\n"
        )

    if prefixes:
        prefix_literal = ", ".join(f"'{prefix}*'" for prefix in prefixes)
        script += (
            f"foreach ($p in @({prefix_literal})) {{\n"
            "  foreach ($t in @(Get-ScheduledTask -TaskPath '\\' -TaskName $p"
            " -ErrorAction SilentlyContinue)) {\n"
            + _ROW
            + "  }\n"
            "}\n"
        )

    return script


def parse_query_output(text: str, names: Sequence[str]) -> dict[str, ScheduledTaskStatus]:
    """NDJSON in, one status per requested name out.

    Every name in `names` gets an entry. A name the query did not answer for
    becomes `query_error` rather than `NOT_REGISTERED`, because those two are
    the difference between "Windows says nothing is scheduled" and "this
    module does not know" — see `ScheduledTaskStatus`.

    An unparseable line is skipped rather than raised on: the batch's other
    answers are still true, and losing all of them because one row was
    mangled is the shape `_print_*` blocks in `ops_status.py` exist to avoid.
    """
    found: dict[str, ScheduledTaskStatus] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            # `RecursionError` alongside `ValueError` for BUG-40's reason:
            # deeply nested JSON raises the former, and a handler that catches
            # only the latter turns a malformed input into a crash. Nothing
            # remote writes this stream, so it is a fence rather than a fix.
            continue
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str):
            continue
        present = bool(row.get("present"))
        if not present:
            found[name] = ScheduledTaskStatus(name=name, present=False)
            continue
        found[name] = ScheduledTaskStatus(
            name=name,
            present=True,
            state=_as_str(row.get("state")),
            last_result=_as_int(row.get("last_result")),
            last_run=_as_str(row.get("last_run")),
            next_run=_as_str(row.get("next_run")),
            missed_runs=_as_int(row.get("missed")),
            action_command=_as_str(row.get("action")),
            action_count=_as_int(row.get("action_count")),
        )

    result = {
        name: found.get(
            name,
            ScheduledTaskStatus(
                name=name,
                present=False,
                query_error="Task Scheduler 조회 결과에 이 Task에 대한 응답이 없습니다",
            ),
        )
        for name in names
    }
    # Rows the query found that nobody asked for by name: `build_query`'s
    # `prefixes` discovery. Added rather than dropped, because the whole
    # point of asking by prefix is to see the task whose name you could not
    # have predicted. `setdefault`, so an explicitly requested name keeps the
    # entry built for it above and a task matching both routes appears once.
    for name, status in found.items():
        result.setdefault(name, status)
    return result


def discards_console_output(status: ScheduledTaskStatus) -> bool | None:
    """Whether this task throws away everything its process prints.

    `None` when it cannot be told -- an absent task, a failed query, an
    action this could not read, or **a task with more than one action**.
    Three-valued rather than defaulting to False, because "we could not
    check" and "it is fine" are the two answers this module refuses to
    confuse everywhere else.

    The multi-action case is the one that used to be answered wrongly rather
    than not at all. `_ROW` reads the first action only -- all three
    installers register exactly one, so a task with several was built by
    hand -- and the count travels alongside it precisely so this function
    can decline. Answering `False` off one of two actions says "this task
    keeps its output" about a task half of which may not, and the caller
    (`ops_status._print_schedule()`) prints that as settled.

    **The question is real and its answer used to be yes for every task this
    project registers.** The action both installers built was
    `python.exe <entrypoint>` with nothing after it, so a scheduled run's
    stdout and stderr went to handles nothing read. For the failures that
    happen before the application can write a file -- `python` off PATH, a
    moved working directory, an unset `COMPANY_OPS_*` -- that discarded
    stream was the *entire* diagnosis.

    Detected by the redirection rather than by the log's name on purpose. An
    operator is free to point the output somewhere else; what must not pass
    unremarked is output going nowhere at all. Naming our own file here
    would report a deliberate choice as a fault, which is how a check earns
    its way into the list of things people ignore.
    """
    if status.query_error is not None or not status.present:
        return None
    if status.action_command is None:
        return None
    # `is not None` rather than a truth test: a count this module could not
    # read stays on the pre-existing behaviour (answer from the one action
    # it has), and only a count that is *known* to exceed one withholds the
    # answer. The change can therefore only turn a definite answer into
    # "unknown" -- it can never invent a fault on a correctly installed task.
    if status.action_count is not None and status.action_count > 1:
        return None
    return "2>&1" not in status.action_command


def redirect_target(action_command: str | None) -> str | None:
    """The file an action's `>>` or `>` sends output to, or `None`.

    Read out of the registered action rather than assumed, so the path this
    project *shows* an operator is the path their machine actually writes --
    including when they redirected somewhere of their own choosing. A
    guessed path sends someone looking for a file that was never written,
    which is the failure mode of every "see the log for details" that names
    the wrong log.

    Only the quoted form is recognised. Both installers emit it, every path
    on Windows can contain a space, and an unquoted scan would have to guess
    where the path ended.
    """
    if not action_command:
        return None
    match = re.search(r'>>?\s*"([^"]+)"', action_command)
    return match.group(1) if match else None


def action_entrypoint(action_command: str | None) -> str | None:
    """The `.py` file a registered action runs, or `None`.

    **Why a report needs this.** Both installers bake absolute paths into
    the action — the interpreter, the entrypoint, the working directory and
    the log. Windows keeps them exactly as given, so moving, renaming or
    re-cloning the repository leaves a task pointing at a directory that is
    no longer there.

    Every run then fails, and — this is the part worth the code — it fails
    with **no explanation reachable**. The redirection target is inside the
    same vanished directory, so `>>` cannot open it, cmd exits 1, and the
    log that would have said "can't open file" was never written. From
    `ops_status.py`'s side that is `LAST_RUN_FAILED, exit 1` with an empty
    log: a failure with the reason removed. Comparing this path against the
    checkout the report is running from names the cause outright.

    Both action shapes are read: the current
    `cmd.exe /c ""<python>" "<entry>" >> "<log>" 2>&1"` and the one
    registered before C138, `<python> "<entry>"`. The first quoted `.py` is
    the entrypoint in both, and the redirection target never ends in `.py`.

    Only the quoted form, for `redirect_target()`'s reason: every Windows
    path may contain a space, and an unquoted scan would have to guess where
    the path ended.
    """
    if not action_command:
        return None
    for candidate in re.findall(r'"([^"]+)"', action_command):
        if candidate.lower().endswith(".py"):
            return candidate
    return None


def _as_str(value) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value) -> int | None:
    # `bool` is an `int` in Python and `True` would become `1` — which is this
    # project's configuration-error exit code, i.e. it would be reported as a
    # specific failure that did not happen.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def query(
    names: Sequence[str],
    *,
    prefixes: Sequence[str] = (),
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    is_windows: bool | None = None,
) -> dict[str, ScheduledTaskStatus]:
    """Ask Windows about `names`. Never raises; an unanswerable query is an answer.

    `run` and `is_windows` are injected so the whole of this module can be
    tested without a scheduled task existing — which matters more here than
    usual, because registering one to test against would change the machine's
    configuration, and this module exists precisely to notice such changes.

    Every failure route returns `query_error` rather than propagating:
    `ops_status.py` is a diagnostic that must still answer when part of its
    evidence is unavailable, and a report that dies because PowerShell is
    missing would be reporting on itself.
    """
    if is_windows is None:
        is_windows = os.name == "nt"
    if not is_windows:
        return _all_unknown(
            names,
            "Windows가 아니므로 Task Scheduler를 조회하지 않았습니다 "
            "(예약 실행은 Windows에서만 구성됩니다)",
        )

    try:
        script = build_query(names, prefixes)
    except ValueError as exc:
        return _all_unknown(names, str(exc))

    try:
        result = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            # The same pair, for the same reason, as `git_ops._run_git()`:
            # without them `text=True` decodes with the locale codepage and a
            # `UnicodeDecodeError` is raised inside subprocess's reader
            # thread, where it never reaches this caller and `stdout` becomes
            # `None`. `errors="replace"` makes the decode unconditional.
            encoding="utf-8",
            errors="replace",
            timeout=QUERY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _all_unknown(
            names,
            f"Task Scheduler 조회가 {QUERY_TIMEOUT_SECONDS:.0f}초 안에 끝나지 "
            f"않았습니다 — 상태를 알 수 없습니다",
        )
    except OSError as exc:
        # `FileNotFoundError` (powershell not on PATH) is the expected member
        # of this family; the base class is caught because a denied execute
        # permission raises `PermissionError` and is the same kind of answer.
        return _all_unknown(names, f"powershell을 실행할 수 없습니다: {exc}")

    stdout = result.stdout or ""
    if result.returncode != 0 and not stdout.strip():
        stderr = (result.stderr or "").strip().replace("\n", " ")
        return _all_unknown(
            names,
            f"Task Scheduler 조회가 실패했습니다 (exit {result.returncode})"
            + (f": {stderr[:200]}" if stderr else ""),
        )
    return parse_query_output(stdout, names)


def _all_unknown(names: Sequence[str], reason: str) -> dict[str, ScheduledTaskStatus]:
    return {
        name: ScheduledTaskStatus(name=name, present=False, query_error=reason)
        for name in names
    }
