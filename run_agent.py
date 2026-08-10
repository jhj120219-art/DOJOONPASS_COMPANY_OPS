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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent import AgentStatus, DateOutcome, run_once  # noqa: E402
from reporter.profiles import ReporterConfigError, resolve_profile  # noqa: E402
from transport import OneDriveTransport  # noqa: E402

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
            print(f"    - {error}")

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
