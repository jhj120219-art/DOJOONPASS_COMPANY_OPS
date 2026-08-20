"""The Dashboard Model, projected onto the surfaces Notion actually has.

Why this is a module and not two lines in the Runner
----------------------------------------------------
docs/14 §1 fixes the Operational Projection as `Notion (PROJECTS /
OPS_RUNS)`, and two things already write to it:

    PROJECTS   `notion/sync.ExecutionPlanSync` — one Event at a time, the
               Current State of a project. Event-driven, not derived from a
               rollup, and not this module's business.
    OPS_RUNS   `notion/dashboard.record_run()` — one row per execution.

C47 put two Control Tower facts on that row (`Desktops Reporting`,
`Role Mismatches`) and `app/runner.py` computed them inline. C48 replaced
the inline counting with one `build_company_rollup()` fold, which removed
the second *count* — but the row's **representation** was still assembled in
the Runner, next to eight unrelated pipeline numbers, out of a rollup rather
than out of the Dashboard Model the screen renders.

That is the same shape C48 removed from `ops_status.py`, one layer over:

    rollup ─┬─> Dashboard Model ──> screen
            └─> (the Runner's own formatting) ──> Notion row

Two consumers of one fold, arranged separately, and the only thing keeping
them in step was a test comparing them afterwards. This closes the fork:

    rollup ──> Dashboard Model ─┬─> screen
                                └─> ops_runs_fields() ──> Notion row

so "the row equals the model" is true by construction rather than by
inspection, and a panel that changes shape takes the row with it.

What it deliberately does NOT do
--------------------------------
It writes nothing and holds no client. `record_run()` owns the write, its
retry queue and its never-raises contract (CEO Decision ④); this only
decides what the Control Tower's columns should say.

It also invents no column. `OPS_RUNS_CONTROL_TOWER_COLUMNS` names the two
that exist, `ContractedColumnsExistTests` checks them against
`notion/dashboard.DASHBOARD_DATABASES`, and adding a third here without
adding it there fails rather than producing an HTTP 400 on the first real
run — the failure mode `DashboardSchemaMappingTests` was written for.
"""

from __future__ import annotations

from typing import Any

from .dashboard import DashboardModel

# The `OPS_RUNS` columns this projection fills, mapped from the keyword
# `record_run()` takes to the Notion property it lands in. Both halves are
# named because both can drift, and only one of them is checkable against a
# schema.
OPS_RUNS_CONTROL_TOWER_COLUMNS: dict[str, str] = {
    "desktops_reporting": "Desktops Reporting",
    "role_mismatches": "Role Mismatches",
}

# Notion's own cap on the text of one `rich_text` item. A value over it is
# rejected by the API, and this projection's one text column grows with
# `events.SOURCES` — four Desktops is ~50 characters today, but the column
# exists precisely because that set is a schema value that can grow (C47).
#
# Truncation is visible, never silent: the string ends with `…` so a reader
# can tell the difference between "four Desktops reported" and "the value
# did not fit". Nothing is lost that is not elsewhere — the same counts are
# on the Dashboard Model's DESKTOPS panel and in the Run Manifest.
RICH_TEXT_LIMIT = 2000


def _desktops_reporting(model: DashboardModel) -> str:
    """`"DESKTOP_1:3 DESKTOP_2:1"` — which Desktops contributed to this run.

    Sorted by `source`, not by the panel's presentation order (which follows
    docs/02 §8's role table), so the same run always renders the same string
    and a diff between two rows means a difference in the work.

    Silent Desktops are dropped rather than written as `DESKTOP_3:0`. The
    panel returns every Desktop present-and-empty because a *status view*
    must not confuse "no activity" with "not counted"; an `OPS_RUNS` row is
    a per-run record, and a Desktop that sent nothing to this run did not
    report to it.
    """
    panel = model.panel("DESKTOPS")
    rows = panel.rows if panel is not None else ()
    text = " ".join(
        f"{row.key}:{row.values['events']}"
        for row in sorted(rows, key=lambda item: item.key)
        if row.values["events"]
    )
    if len(text) <= RICH_TEXT_LIMIT:
        return text
    return text[: RICH_TEXT_LIMIT - 1] + "…"


def _role_mismatches(model: DashboardModel) -> int:
    """Events in this run whose `source`/`role` pair contradicts docs/02 §8.

    Counted off the DESKTOPS panel rather than off the RISKS panel, even
    though both carry them: DESKTOPS counts by the Desktop that **sent** the
    Event, which is the partition this column is about, and RISKS mixes them
    with open blockers. `TheTwoPanelsAgreeAboutMismatchesTests` holds the two
    to the same number so the choice cannot quietly become a difference.
    """
    panel = model.panel("DESKTOPS")
    if panel is None:
        return 0
    return sum(row.values["role_mismatches"] for row in panel.rows)


def ops_runs_fields(model: DashboardModel) -> dict[str, Any]:
    """The Control Tower half of one `OPS_RUNS` row, as `record_run()` kwargs.

    Keyed by the parameter name rather than the Notion column so a caller
    reads as an ordinary call; `OPS_RUNS_CONTROL_TOWER_COLUMNS` carries the
    mapping to the column, and a test checks that side against the schema.

    `model` must be built from **this run's** Events. A model over the whole
    of `processed/` would put an all-time number on a per-run row — the
    mistake `app/runner.py`'s own comment warns about, and the reason the
    Runner folds the Events it just accepted rather than re-reading the
    directory.
    """
    return {
        "desktops_reporting": _desktops_reporting(model),
        "role_mismatches": _role_mismatches(model),
    }


# No `contracted_columns()` accessor here. It existed for one draft, read
# `DASHBOARD_DATABASES[OPS_RUNS]` back and returned the two entries — and
# nothing but a test ever called it, which is the shape
# `DeadCapabilityInventoryTests` exists to catch. The declaration this module
# owes the rest of the project is `OPS_RUNS_CONTROL_TOWER_COLUMNS`; the
# schema is `notion/dashboard.py`'s, and a reader who wants both asks each of
# them once. `ContractedColumnsExistTests` does exactly that.

__all__ = [
    "OPS_RUNS_CONTROL_TOWER_COLUMNS",
    "RICH_TEXT_LIMIT",
    "ops_runs_fields",
]
