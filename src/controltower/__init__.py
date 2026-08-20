"""Company Control Tower — read-only rollups over Execution Evidence.

Writes nothing, stores nothing, decides nothing. See `rollup.py` for what it
derives, what it deliberately does not invent, and why every number carries
the files it was counted from, and `dashboard.py` for the same facts arranged
into the panels a Control Tower has, with the payload a projection consumes,
and `projection.py` for the model's landing on the two Notion surfaces
docs/14 §1 contracts.
"""

from .dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    DashboardModel,
    DashboardPanel,
    DashboardRow,
    PanelStatus,
    build_dashboard,
    unsourced_layer_coverage,
)
from .projection import (
    OPS_RUNS_CONTROL_TOWER_COLUMNS,
    ops_runs_fields,
)
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
    "DASHBOARD_SCHEMA_VERSION",
    "DashboardModel",
    "DashboardPanel",
    "DashboardRow",
    "OPS_RUNS_CONTROL_TOWER_COLUMNS",
    "DesktopRollup",
    "EvidenceRef",
    "Metric",
    "PairMismatch",
    "ProjectRollup",
    "ROLE_FOR_SOURCE",
    "Risk",
    "PanelStatus",
    "TeamRollup",
    "UNSOURCED_LAYERS",
    "build_company_rollup",
    "build_dashboard",
    "event_instant_key",
    "ops_runs_fields",
    "read_events",
    "unsourced_layer_coverage",
]
