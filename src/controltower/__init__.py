"""Company Control Tower — read-only rollups over Execution Evidence.

Writes nothing, stores nothing, decides nothing. See `rollup.py` for what it
derives, what it deliberately does not invent, and why every number carries
the files it was counted from.
"""

from .rollup import (
    CompanyRollup,
    DesktopRollup,
    EvidenceRef,
    Metric,
    PairMismatch,
    ProjectRollup,
    Risk,
    ROLE_FOR_SOURCE,
    TeamRollup,
    UNSOURCED_LAYERS,
    build_company_rollup,
    event_instant_key,
    read_events,
)

__all__ = [
    "CompanyRollup",
    "DesktopRollup",
    "EvidenceRef",
    "Metric",
    "PairMismatch",
    "ProjectRollup",
    "ROLE_FOR_SOURCE",
    "Risk",
    "TeamRollup",
    "UNSOURCED_LAYERS",
    "build_company_rollup",
    "event_instant_key",
    "read_events",
]
