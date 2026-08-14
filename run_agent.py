"""Multi-Desktop Agent Entrypoint.

    python run_agent.py

One non-looping `agent.run_once()` call. Run it on Desktop 1, 2, 3, and 4
— the same script, the same code, only the environment differs:

    COMPANY_OPS_PROFILE=DESKTOP_1     -> CTO Backend
    COMPANY_OPS_PROFILE=DESKTOP_2     -> CMO
    COMPANY_OPS_PROFILE=DESKTOP_3     -> CTO Frontend
    COMPANY_OPS_PROFILE=DESKTOP_4     -> COO

The role is NOT configured separately. It comes from the profile table in
`src/reporter/profiles.py`, which is docs/02_EVENT_SCHEMA.md §8's own
source->role table — so a Desktop cannot be pointed at a role the Event
Schema does not sanction, and two machines cannot disagree about who
DESKTOP_2 is. `COMPANY_OPS_PROFILE` is the variable `.env.example` and
`reporter/profiles.py` already defined; no second identity variable was
invented for the Agent.

Desktop 4 is not a special case. It runs the same Agent and sends through
the same Transport, pointed at its own `runtime/events/transport/`
directory — where `transport.run_intake()` (called by
`run_company_ops.py`) already promotes files to `incoming/`. The COO's own
Events therefore travel the identical path as Desktop 1/2/3's, with no
bypass and no second code path to keep correct.

Like `run_company_ops.py`, this script does not loop, sleep, or register
itself with Windows Task Scheduler. Registration is an operational step —
`scripts/install_agent_task.ps1` performs it, and docs/07 §53 defines the
trigger it uses.

Exit codes:
    0   COMPLETED, or skipped because another Agent run holds the lock
    1   configuration error (bad/missing environment)
    2   FAILED — nothing was lost; the outbox still holds the work and the
        next run resumes from the same date
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

# Same reason as run_company_ops.py: this script's operator-facing messages
# are Korean and Windows' console defaults to the system's legacy codepage,
# under which printing an em dash raises UnicodeEncodeError and kills the
# process. Forcing UTF-8 keeps the output honest on any console.
# `line_buffering=True` keeps this script's own output in the order it was
# written. Python block-buffers stdout when it is not a terminal and leaves
# stderr unbuffered, so under the redirection a scheduled task actually uses
# (`>log 2>&1`) every stderr line overtakes the stdout lines around it.
# Measured: a failure message printed above the context line explaining it.
# That is the one reading an operator gets from a captured log, and it makes
# a report look like it describes the wrong thing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent import AgentStatus, DateOutcome, run_once  # noqa: E402
from agent.state import AgentStateError, AgentStateMismatchError  # noqa: E402
from reporter.profiles import ReporterConfigError, resolve_profile  # noqa: E402
from transport import OneDriveTransport  # noqa: E402

# "One item, one line" for anything this script prints that was read back
# out of a file, the same rule `oplog.append_line()` applies to every logged
# line and `ops_status.py` applies to its ATTENTION block. `agent.py` already
# `redact()`s these strings at the point it builds them; what it does not do
# is stop one of them ending a line.
from oplog import one_line  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"

SYNC_FOLDER_ENV_VAR = "COMPANY_OPS_AGENT_SYNC_FOLDER"
START_DATE_ENV_VAR = "COMPANY_OPS_AGENT_START_DATE"


class ConfigurationError(Exception):
    """A missing or unusable environment value. Reported, never guessed."""


def _resolve_sync_folder() -> Path:
    raw = os.environ.get(SYNC_FOLDER_ENV_VAR)
    if not raw:
        raise ConfigurationError(
            f"{SYNC_FOLDER_ENV_VAR} 환경변수가 없습니다. Event를 어느 폴더로 "
            f"보낼지는 추측하지 않습니다 — Desktop 4와 공유되는 OneDrive Sync "
            f"폴더 경로를 설정하세요."
        )
    return Path(raw)


def _resolve_start_date() -> date:
    """The first date this Desktop should ever collect. Never guessed.

    Exactly docs/07 §50's rule, which `run_company_ops.py` already applies
    to `COMPANY_OPS_HISTORY_START_DATE`: on a first-ever run there is no
    state to derive a start from, and picking one silently would either
    invent history or skip it. A separate variable from the Desktop 4 one
    because a Desktop can join the company later than Company History
    started.
    """
    raw = os.environ.get(START_DATE_ENV_VAR)
    if not raw:
        raise ConfigurationError(
            f"{START_DATE_ENV_VAR} 환경변수가 없습니다. 이 Desktop이 언제부터 "
            f"수집을 시작해야 하는지는 추측하지 않습니다(docs/07 §50) — "
            f"YYYY-MM-DD 형식으로 설정하세요."
        )
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{START_DATE_ENV_VAR} 형식이 올바르지 않습니다: {raw!r} (YYYY-MM-DD 필요)"
        ) from exc


def main() -> int:
    try:
        profile = resolve_profile()
        sync_folder = _resolve_sync_folder()
        start_date = _resolve_start_date()
    except (ConfigurationError, ReporterConfigError) as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return 1

    agent_dir = RUNTIME_DIR / "agent"
    transport = OneDriveTransport(
        sync_folder=sync_folder, outgoing_dir=agent_dir / "outgoing"
    )

    try:
        result = run_once(
            transport=transport,
            agent_start_date=start_date,
            profile=profile,
            signals_dir=agent_dir / "signals",
            rejected_signals_dir=agent_dir / "signals_rejected",
            outbox_dir=agent_dir / "outbox",
            sent_dir=agent_dir / "sent",
            state_path=agent_dir / "state" / "agent_state.json",
            lock_path=agent_dir / "locks" / "agent.lock",
            log_path=agent_dir / "logs" / "agent.log",
        )
    except AgentStateMismatchError as exc:
        # A raw traceback for an expected operational condition reads like
        # the system broke. This one is not exotic: `install_agent_task.ps1`
        # writes COMPANY_OPS_PROFILE into the user environment, and its own
        # documentation warns that previewing an install with the wrong
        # -DesktopId used to repoint a machine's identity. Re-running it with
        # the wrong ID and then letting the scheduled task fire is exactly
        # how an operator reaches this.
        #
        # Reported, never auto-resolved. `state.ensure_desktop()` refuses the
        # mismatch precisely because silently accepting it would let one
        # Desktop inherit another's `last_successful_collection_date` and
        # skip every date up to it — losing those Events with no error
        # anywhere. Deleting or rewriting the file here would be that same
        # data loss, performed helpfully.
        print(f"[FAILED] {exc}", file=sys.stderr)
        print(
            "\n이 머신의 Agent state는 위 Desktop에 묶여 있습니다. 둘 중 하나입니다:\n"
            "  - COMPANY_OPS_PROFILE이 잘못 설정됐다 → 원래 Desktop ID로 되돌린다.\n"
            "  - 이 머신의 역할이 실제로 바뀌었다 → 기존 state 파일을 사람이\n"
            "    확인한 뒤 옮기거나 보관한다.\n"
            "state 파일은 자동으로 지우거나 고치지 않습니다 — 그렇게 하면 아직\n"
            "수집되지 않은 날짜가 조용히 건너뛰어질 수 있습니다.",
            file=sys.stderr,
        )
        return 1
    except AgentStateError as exc:
        # The other half of the same exception: the state file is damaged
        # rather than pointed at the wrong Desktop (truncated by a power
        # cut, a partially restored backup, an unparseable date field).
        #
        # This used to fall into the branch above and be answered with
        # "COMPANY_OPS_PROFILE이 잘못 설정됐다" — advice about an environment
        # variable that is already correct, for a file the operator has not
        # been told is unreadable. Two different accidents, opposite fixes.
        #
        # Same restraint applies: the file is not deleted or rewritten here.
        # It holds `last_successful_collection_date`, and guessing a
        # replacement would silently skip every date up to the guess.
        print(f"[FAILED] {exc}", file=sys.stderr)
        print(
            "\nAgent state 파일을 읽을 수 없습니다. Desktop 설정 문제가 아니므로\n"
            "COMPANY_OPS_PROFILE을 바꿔도 해결되지 않습니다.\n"
            "  - 파일 내용을 사람이 확인한다(마지막 수집 날짜가 들어 있다).\n"
            "  - 복구할 수 없으면 파일을 옮겨 두고, 이 Desktop이 다시 수집해야 할\n"
            "    첫 날짜를 COMPANY_OPS_AGENT_START_DATE로 지정해 다시 실행한다.\n"
            "state 파일은 자동으로 지우거나 고치지 않습니다 — 그렇게 하면 아직\n"
            "수집되지 않은 날짜가 조용히 건너뛰어질 수 있습니다.",
            file=sys.stderr,
        )
        return 1

    print(f"Agent: {result.desktop_id} ({result.role}) -> {result.status.value}")

    if result.status is AgentStatus.SKIPPED_ALREADY_RUNNING:
        print("[SKIPPED] 다른 Agent 실행이 이미 진행 중입니다(Lock 획득 실패).")
        return 0

    for date_result in result.dates:
        detail = (
            f"events={len(date_result.event_ids)} "
            f"already_sent={len(date_result.already_sent)} "
            f"rejected={len(date_result.rejected_signals)}"
        )
        marker = "  " if date_result.outcome is not DateOutcome.FAILED else "! "
        print(f"{marker}{date_result.date.isoformat()}: {date_result.outcome.value} {detail}")
        for error in date_result.errors:
            # A Signal filename, a parse error, or a Transport failure — all
            # read back from disk, none constrained to one line. A forged
            # line here imitates a `  <date>: <outcome>` result row in the
            # report an operator reads to decide whether a date was collected.
            print(f"    - {one_line(error)}")

    print(f"last_successful_collection_date = {result.last_successful_collection_date}")

    if result.status is AgentStatus.FAILED:
        print(
            f"[FAILED] {result.error}\n"
            f"        Event는 유실되지 않았습니다 — outbox에 남아 있으며 다음 "
            f"실행에서 같은 날짜부터 다시 시도합니다.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
