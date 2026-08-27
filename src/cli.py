"""What an entrypoint does with a command-line argument it does not have.

Every tool in this repository is configured by environment variable and none
of them reads `sys.argv` at all. That is a deliberate design -- the scheduled
task registered by `scripts/install_agent_task.ps1` passes no arguments and
persists `COMPANY_OPS_*` to the user environment instead -- but until C47 it
had a silent edge:

    python run_company_ops.py --dry-run

ran a **full production run**. Real git push, real Notion writes. The flag was
not rejected, not warned about, not mentioned; it was never looked at. An
operator reaching for `--dry-run` before a first production run is reaching
for exactly the safety this had none of, and the tool answered by doing the
unsafe thing and reporting success.

`--help` was the same shape from the other side: it printed
`COMPANY_OPS_HISTORY_START_DATE 환경변수가 없습니다`, which is a true
sentence about a question nobody asked.

So this refuses, and names the variables the tool actually reads. Refusing
rather than warning, because the two mistakes this catches -- believing in a
flag that does nothing, and asking for help -- are both cases where doing the
work is the wrong answer.

Nothing supplies an argument today (measured: no `sys.argv`, `argparse` or
`ArgumentParser` anywhere in the four entrypoints, and the installer's action
is `python run_agent.py` with no arguments), so this can only ever fire on a
mistake.

Each entrypoint takes `argv` as a parameter defaulting to empty, and reads
`sys.argv` only at its `__main__` guard. That is not decoration: a caller with
no command line -- every test that drives `main()` in-process, and any future
library use -- has no arguments to be refused, while `sys.argv` under pytest
holds pytest's own flags. Reading the global inside `main()` made twenty-five
existing tests fail by refusing `-q -k some_test_name`, which is the honest
demonstration that the command line belongs at the boundary and nowhere else.

A leaf module, like `oplog` and `runsummary`: it imports nothing from this
project and every entrypoint sits above it.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Sequence

# The exit code every entrypoint already documents for this class of problem
# (`AGENT.md` §6: `1` 설정 오류). A new code would have to be documented, and
# a mistyped flag is a configuration mistake -- the same thing an unset
# `COMPANY_OPS_PROFILE` is.
CONFIG_ERROR_EXIT = 1


def unexpected_arguments(
    argv: Sequence[str], *, tool: str, configured_by: Sequence[str]
) -> str | None:
    """The message to print for a rejected invocation, or `None` to proceed.

    `argv` is the whole of `sys.argv`; the program name is skipped here so no
    caller has to remember to slice it.

    `configured_by` is the tool's own environment variables, in the order its
    documentation lists them. They are named in the message because "this
    takes no arguments" leaves an operator with nowhere to go, and the next
    thing they need is the name of the knob that does exist.
    """
    arguments = list(argv[1:])
    if not arguments:
        return None

    asked_for_help = any(item in ("-h", "--help", "/?", "help") for item in arguments)
    lead = (
        f"{tool}은(는) 명령줄 인자를 받지 않습니다."
        if not asked_for_help
        else f"{tool}에는 --help가 없습니다 — 명령줄 인자를 받지 않습니다."
    )
    names = ", ".join(configured_by) if configured_by else "(없음)"
    return (
        f"{lead} 받은 인자: {' '.join(arguments)}\n"
        f"설정은 전부 환경변수로 합니다: {names}\n"
        f"사용법은 AGENT.md를 보세요."
    )


# ------------------------------------------------ the output-side boundary

#: What a tool exits with when the program reading its output ended first.
#:
#: **Why this is not 0 (C118).** Measured, on this machine:
#:
#:     python ops_status.py | head -3
#:
#:     OSError: [Errno 22] Invalid argument      <- inside the report
#:     OSError: [Errno 22] Invalid argument      <- inside the handler for it
#:     Exception ignored on flushing sys.stdout
#:     exit 120
#:
#: `120` is the interpreter's "an exception escaped at shutdown"; it is not
#: one of this project's codes and says nothing to a person or a wrapper.
#:
#: `0` would be worse. For `ops_status.py` a `0` is a **claim** — "사람이 지금
#: 할 일은 없다" — and the report never finished being computed, let alone
#: read. Reporting the all-clear because nobody was listening is the shape
#: this project keeps removing.
#:
#: `2` rather than a fifth number: docs/14 §4 already spends 2 on "the run
#: did not deliver what it exists for", and a report that reached no reader
#: is exactly that. `1` stays configuration, `3` stays "something needs a
#: person" — a judgement this state has not reached.
OUTPUT_LOST_EXIT = 2


def output_is_gone(stream=None) -> bool:
    """True when the program on the other end of `stream` has closed it.

    **Measured rather than guessed at, and that is the whole point.** On
    Windows a write to a pipe whose reader has exited raises

        OSError(22, 'Invalid argument')

    — **not** `BrokenPipeError`, whose `errno` (`EPIPE`, 32) is what the
    portable recipes match on. And `EINVAL` is a real disk error too, so an
    errno test here would either miss this on Windows or start calling
    genuine I/O failures "the reader left".

    So this asks the stream instead. Measured on the same run:

        write("")     OK      <- buffered, proves nothing
        write("x")    OK      <- buffered, proves nothing
        flush()       raises  <- reaches the OS every time
        stdout.closed False   <- proves nothing either

    `flush()` is the one probe that touches the file descriptor, and on a
    healthy stream it is free.
    """
    target = sys.stdout if stream is None else stream
    try:
        target.flush()
    except OSError:
        return True
    return False


def run_entrypoint(main_fn: Callable[[Sequence[str]], int], argv: Sequence[str]) -> int:
    """Call a tool's `main()` and turn a lost output stream into an answer.

    Only that. An `OSError` this cannot attribute to the output side is
    re-raised untouched — a disk or permission failure must keep its
    traceback, and a handler that swallowed both would be the "did the unsafe
    thing and reported success" shape this module was written about.

    The `dup2` is CPython's own recipe for this state. Without it the
    interpreter flushes `sys.stdout` once more on the way out, that flush
    raises again, and the process exits `120` no matter what is returned
    here — so the return value below would be a number nobody ever sees.
    """
    try:
        return main_fn(argv)
    except OSError:
        if not output_is_gone():
            raise

    # stderr is usually still a terminal in `tool | head` — that is where the
    # operator is. Guarded anyway: `tool > file 2>&1` loses both at once, and
    # a report about a lost stream must not be the thing that crashes.
    try:
        print(
            "[중단] 출력을 받던 프로그램이 먼저 끝났습니다 (파이프가 닫혔습니다). "
            "보고를 끝내지 못했으므로 이 실행은 아무 판정도 하지 않았습니다.",
            file=sys.stderr,
        )
        sys.stderr.flush()
    except OSError:
        pass

    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except OSError:
        pass
    return OUTPUT_LOST_EXIT
