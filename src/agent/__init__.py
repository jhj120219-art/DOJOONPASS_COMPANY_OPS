"""Multi-Desktop Agent package.

One Agent, run on every Desktop; only the Desktop Profile
(`reporter/profiles.py`) and the paths differ. See `agent.py` for the
pipeline and the two invariants it exists to hold.
"""

from .agent import (
    DEFAULT_LOCK_PATH,
    DEFAULT_LOG_PATH,
    DEFAULT_REJECTED_SIGNALS_DIR,
    EVENT_ID_NAMESPACE,
    AgentRunResult,
    AgentStatus,
    DateOutcome,
    DateResult,
    derive_event_id,
    run_once,
)
from .catchup import pending_dates
from .outbox import (
    DEFAULT_OUTBOX_DIR,
    DEFAULT_SENT_DIR,
    DrainSummary,
    drain,
    is_sent,
    pending,
    stage,
)
from .signals import (
    ALLOWED_SIGNAL_FIELDS,
    DEFAULT_SIGNALS_DIR,
    FORBIDDEN_SIGNAL_FIELDS,
    Signal,
    SignalError,
    find_secret_material,
    load_signals,
    parse_signal,
    redact,
)
from .state import (
    DEFAULT_STATE_PATH,
    AgentState,
    AgentStateError,
    ensure_desktop,
    load_state,
    save_state,
)
from .status import AgentStatusSnapshot, read_status

__all__ = [
    "ALLOWED_SIGNAL_FIELDS",
    "DEFAULT_LOCK_PATH",
    "DEFAULT_LOG_PATH",
    "DEFAULT_OUTBOX_DIR",
    "DEFAULT_REJECTED_SIGNALS_DIR",
    "DEFAULT_SENT_DIR",
    "DEFAULT_SIGNALS_DIR",
    "DEFAULT_STATE_PATH",
    "EVENT_ID_NAMESPACE",
    "FORBIDDEN_SIGNAL_FIELDS",
    "AgentRunResult",
    "AgentState",
    "AgentStateError",
    "AgentStatus",
    "AgentStatusSnapshot",
    "DateOutcome",
    "DateResult",
    "DrainSummary",
    "Signal",
    "SignalError",
    "derive_event_id",
    "drain",
    "ensure_desktop",
    "find_secret_material",
    "is_sent",
    "load_signals",
    "load_state",
    "parse_signal",
    "pending",
    "pending_dates",
    "read_status",
    "redact",
    "run_once",
    "save_state",
    "stage",
]
