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

from typing import Sequence

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
