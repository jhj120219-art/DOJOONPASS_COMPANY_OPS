"""Company Control Tower — read-only rollups over Execution Evidence.

Writes nothing, stores nothing, decides nothing. See `rollup.py` for what it
derives, what it deliberately does not invent, and why every number carries
the files it was counted from, and `dashboard.py` for the same facts arranged
into the panels a Control Tower has, with the payload a projection consumes,
and `projection.py` for the model's landing on the two Notion surfaces
docs/14 §1 contracts.

`notion_projection.py` is deliberately **not** re-exported here. It is the
model's landing on five databases docs/14 §1 does not contract yet, and
importing it pulls `notion.client` into every `import controltower` — a cost
the read-only rollups have no reason to pay. A caller that wants it asks for
it by name (`from controltower import notion_projection`), which is also the
one place a reader learns it is a separate contract.
"""

from .cohort import (
    COHORT_UNIT,
    COHORT_WINDOWS,
    Cohort,
    CohortAnalysis,
    CohortWindow,
    build_cohort_analysis,
)
from .dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    DashboardModel,
    DashboardPanel,
    DashboardRow,
    PanelStatus,
    build_dashboard,
    evidence_window,
    unsourced_layer_coverage,
)
from .projection import (
    OPS_RUNS_CONTROL_TOWER_COLUMNS,
    ops_runs_fields,
)
from .rollup import (
    CompanyRollup,
    DesktopRollup,
    DuplicateEvent,
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
    "COHORT_UNIT",
    "COHORT_WINDOWS",
    "Cohort",
    "CohortAnalysis",
    "CohortWindow",
    "CompanyRollup",
    "DASHBOARD_SCHEMA_VERSION",
    "DashboardModel",
    "DashboardPanel",
    "DashboardRow",
    "OPS_RUNS_CONTROL_TOWER_COLUMNS",
    "DesktopRollup",
    "DuplicateEvent",
    "EvidenceRef",
    "Metric",
    "PairMismatch",
    "ProjectRollup",
    "ROLE_FOR_SOURCE",
    "Risk",
    "PanelStatus",
    "TeamRollup",
    "UNSOURCED_LAYERS",
    "build_cohort_analysis",
    "build_company_rollup",
    "build_dashboard",
    "event_instant_key",
    "evidence_window",
    "ops_runs_fields",
    "read_events",
    "unsourced_layer_coverage",
]
