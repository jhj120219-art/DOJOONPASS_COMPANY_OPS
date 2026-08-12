"""Multi-Desktop Agent state: last_successful_collection_date only.

Deliberately the same shape, the same atomic-write idiom, and the same
typed-error contract as `scheduler/state.py` and `collector/state.py` —
this is a third instance of an established pattern, not a new one.

What it stores and why each field is here:

    desktop_id
        Which Desktop this state file belongs to. The Agent's outbox and
        the OneDrive sync folder live near each other on disk, and a state
        file copied (or synced) to the wrong machine would silently make
        that machine believe another Desktop's dates were already
        collected — which is exactly a data-loss shape. Recorded so
        `ensure_desktop()` can refuse instead.

    last_successful_collection_date
        The last calendar date whose Events were fully collected AND
        fully handed to Transport. It advances only after both, per
        docs/07 §29's per-date save rule, and never past a failure —
        that is what makes "8/8 성공, 8/9 실패 -> 다음 실행은 8/9부터"
        true rather than aspirational.

    last_run
        A record for a human reading the file. Nothing branches on it,
        exactly as `collector.state.PersistentSeenEventStore.last_run`
        already documents about itself.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "runtime" / "agent" / "state" / "agent_state.json"


class AgentStateError(ValueError):
    """Raised when agent_state.json exists but cannot be read as valid Agent
    state (bad JSON, wrong shape, wrong field types, wrong Desktop).

    Never raised for a simply-missing file — that starts an empty state,
    matching `scheduler.state.load_state()` and
    `collector.state.PersistentSeenEventStore._load()`.
    """


class AgentStateMismatchError(AgentStateError):
    """The state file is perfectly valid and belongs to another Desktop.

    A subclass rather than a separate exception so every existing
    `except AgentStateError` keeps catching it — nothing about the refusal
    changes. What changes is that a caller can now tell the two apart, and
    they need opposite things from an operator:

        mismatch    the file is fine; the *identity* is wrong. Fix
                    COMPANY_OPS_PROFILE, or move the file aside if this
                    machine really did change role.
        corruption  the identity is not the issue; the file is unreadable.
                    Changing COMPANY_OPS_PROFILE does nothing at all.

    `run_agent.py` gave the first message for both, so an operator whose
    state file had been truncated by a power cut was told to go and check
    an environment variable that was already correct.
    """


@dataclass
class AgentState:
    desktop_id: str | None = None
    last_successful_collection_date: date | None = None
    last_run: str | None = None


def load_state(state_path: Path) -> AgentState:
    if not state_path.exists():
        return AgentState()

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AgentStateError(f"agent state file is corrupted: {state_path} ({exc})") from exc

    if not isinstance(data, dict):
        raise AgentStateError(f"agent state file must contain a JSON object: {state_path}")

    for field_name in ("desktop_id", "last_successful_collection_date", "last_run"):
        value = data.get(field_name)
        if value is not None and not isinstance(value, str):
            raise AgentStateError(
                f"agent state file has an invalid {field_name} field: {state_path}"
            )

    raw_date = data.get("last_successful_collection_date")
    try:
        parsed = date.fromisoformat(raw_date) if raw_date else None
    except ValueError as exc:
        raise AgentStateError(
            f"agent state file has an unparseable last_successful_collection_date: "
            f"{state_path} ({exc})"
        ) from exc

    return AgentState(
        desktop_id=data.get("desktop_id"),
        last_successful_collection_date=parsed,
        last_run=data.get("last_run"),
    )


def ensure_desktop(state: AgentState, desktop_id: str, *, state_path: Path) -> None:
    """Refuse a state file that belongs to a different Desktop.

    A first-ever state (`desktop_id is None`) matches anything — it is
    adopted by whichever Desktop writes to it first. After that the
    binding is permanent for that file: silently accepting a mismatch
    would let one Desktop inherit another's
    `last_successful_collection_date` and skip every date up to it,
    losing those Events with no error anywhere.
    """
    if state.desktop_id is not None and state.desktop_id != desktop_id:
        raise AgentStateMismatchError(
            f"agent state file belongs to {state.desktop_id!r}, not {desktop_id!r}: "
            f"{state_path}"
        )


def save_state(state_path: Path, state: AgentState) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "desktop_id": state.desktop_id,
        "last_successful_collection_date": (
            state.last_successful_collection_date.isoformat()
            if state.last_successful_collection_date is not None
            else None
        ),
        "last_run": state.last_run,
    }
    fd, tmp_path = tempfile.mkstemp(dir=state_path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, state_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
