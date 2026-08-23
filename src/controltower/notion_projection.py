"""The Control Tower's Dashboard Model, projected onto Notion databases.

What was missing
----------------
`controltower/dashboard.py` builds the whole Control Tower as data and
`to_payload()` serialises it. Nothing consumed that payload. Measured on
HEAD: `to_payload` is the one entry in `DeadCapabilityInventoryTests.EXPECTED`
recorded as "waiting on a credentialled sink, not on a decision", and the
chain the request states —

    Desktop 1/2/4 -> Execution Evidence -> Control Tower
        -> Company / Team / Project / Sprint -> Dashboard -> Notion

stopped one arrow short of Notion. `ops_status.py` renders the model to a
terminal; `controltower/projection.py` puts two of its facts on the
`OPS_RUNS` row. The panels themselves — the Company / Team / Project view a
person would actually open — had no Notion representation at all.

This module is that representation, and it is a **contract**, not a write
that happens on every run. See "What this deliberately does not do".

One arrangement, one projection
-------------------------------
Everything here is derived from `DashboardModel`, and specifically from
`to_payload()` rather than from the model's own attributes. That is a
security property, not a convenience:

    to_payload()  applies `redact(one_line(...))` to every authored value,
                  bounds every evidence list at `EVIDENCE_IN_PAYLOAD`, and
                  is deterministic by construction.

Reading the model's fields directly here would have made this a second
boundary that has to remember the same rules — and `_UNAUTHORED_KEYS` exists
because the first draft of that list, written the other way round, leaked a
secret-shaped `project_id` through a row key. One boundary, and this side of
it never sees an un-redacted string. `ASecretShapedValueNeverReachesNotionTests`
holds it.

A column that gains no property is a column that vanishes
---------------------------------------------------------
`PANEL_PROJECTIONS` declares, for every panel and every column that panel
announces, the Notion property it becomes and that property's type. Both
directions are gated (`EveryColumnHasAPropertyTests`): a column added to
`controltower/dashboard.py` without a mapping fails here rather than being
silently dropped on the way to Notion, and a mapping with no column fails
rather than creating a property nothing ever fills.

That is the failure mode `DashboardSchemaMappingTests` was written for one
layer down, and the reason `projection.py` refuses to invent a column.

Unsourced layers get no database
---------------------------------
`COMPANY_GOALS` and `SPRINTS` are `PanelStatus.UNSOURCED` — this system has
no Goal, Sprint or Task anywhere, not in `events.Event` and not in Company
History. They therefore get **no** Notion database.

That is a deliberate reversal of the mistake `CONTRACTED_DATABASES` records:
`bootstrap_dashboard_databases()` used to create four databases that no code
writes, leaving an operator with permanently empty tables and a prose
warning as the only explanation. An empty database is indistinguishable from
a broken one. `UNSOURCED_LAYER_NOTES` says what is missing and what decision
would supply it, in the same words the panel says it in, and creates nothing.

What this deliberately does not do
----------------------------------
It is **not** wired into `app/runner.py` and its databases are **not** in
`notion/dashboard.CONTRACTED_DATABASES`.

It also lives in `controltower/` rather than in `notion/`, and that is the
layering rather than a preference: `LayeringInvariantTests.ALLOWED`
gives `notion` exactly one edge (`events`), while `controltower` already
imports `notion` for docs/04 §20-28's blocker rule. A `notion` module that
imported the Dashboard Model would close the first import cycle this
project has ever had. Same argument, same direction, as
`controltower/projection.py`.

docs/14 §1 fixes the Operational Projection as `Notion (PROJECTS /
OPS_RUNS)` — two databases, named. Adding five more is a change to that
table, which is a specification decision and not one this module may take
(`ControlTowerDatabasesAreNotContractedYetTests` pins it, and will fail the
day docs/14 is widened, which is the point: the gate names the decision
rather than hiding it).

So the schema, the property mapping, the payload, the validation and the
write path are all complete and all exercised end to end against the
in-memory transport. What is missing is a sanctioned place to put them and
a credential to reach it — the same two things `bootstrap_dashboard_databases()`
has been waiting for since C31, and both are recorded in BACKLOG.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from oplog import bounded, one_line, redact

from notion.client import NotionClient
from notion.properties import RICH_TEXT_LIMIT, fit_key, fit_properties

from .dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    UNSOURCED_LAYERS,
    DashboardModel,
    PanelStatus,
)

# ---------------------------------------------------------------- vocabulary


class PropertyType(enum.Enum):
    """The Notion property types this projection uses.

    Five of the many Notion has, and the restriction is the same one
    `notion/dashboard.py` states: only shapes the API accepts when
    *creating* a database. Notion's `status` type in particular cannot be
    created through the API at all, which is why a bounded vocabulary is a
    `SELECT` here and never a status.
    """

    TITLE = "title"
    RICH_TEXT = "rich_text"
    NUMBER = "number"
    DATE = "date"
    CHECKBOX = "checkbox"
    SELECT = "select"


#: The title property of every Control Tower database.
#:
#: One name across all five, because it holds the same thing in all five: the
#: `DashboardRow.key`, which the Dashboard Model already defines as "the row's
#: identity in its panel — so a consumer can diff two payloads without
#: positional matching". That is exactly what `find_or_create_by_title()`
#: needs, so the projection gets idempotency for free instead of inventing a
#: second identity beside the one the model already has.
ROW_KEY_PROPERTY = "Row Key"

#: Properties every row of every panel carries, on top of its own columns.
#:
#: `Generated At` is the one that had to be here rather than on some header
#: row. These databases are written by `find_or_create_by_title()`, so a row
#: is updated in place and **never disappears**: a project that stops being
#: reported keeps its last row forever, and a projection that stopped running
#: two weeks ago looks exactly like one that ran this morning over quiet
#: evidence. The timestamp is the only thing that tells those apart, and it
#: has to be per-row because it is per-row that the question gets asked.
#:
#: `Coverage Complete` carries `Coverage.complete` onto every row for the
#: same reason the model carries `unreadable` at the top rather than inside a
#: panel: it qualifies every number in all of them. A row reading `Events 0`
#: means something different when the evidence it was counted from is gone.
#:
#: `Present` and `Retired At` are the reconciliation pair, and they exist
#: because find-then-update cannot see a row whose subject is gone. A RISK row
#: is written when a project reports BLOCKED and there is nothing to update it
#: with when the project reports RESUMED — the risk simply stops being
#: produced, and the row sits in Notion reporting a solved problem forever.
#: Measured end to end: after a RESUMED Event the CT_PROJECTS row went
#: `BLOCKED -> ACTIVE` with an empty `Blocker`, and the CT_RISKS row beside it
#: still said `OPEN_BLOCKER`.
#:
#: Marked, never deleted. This repository does not delete (BACKLOG B-7 is a
#: standing example), a retired row is the record that the risk *was* open,
#: and `Retired At` is when it stopped being — which is the one fact an
#: archived page would take with it. An operator's Notion view filters on
#: `Present`.
COMMON_PROPERTIES: dict[str, PropertyType] = {
    ROW_KEY_PROPERTY: PropertyType.TITLE,
    "Generated At": PropertyType.DATE,
    "Coverage Complete": PropertyType.CHECKBOX,
    # Why `Coverage Complete` is false, when it is.
    #
    # It has two causes and they call for opposite reactions: a file that
    # could not be read is damage an operator goes and looks at, while "the
    # Company-History question was never asked" means this projection ran
    # without the one enrichment `ops_status.py` performs. One checkbox
    # cannot say which, and an operator hunting a corrupt file that does not
    # exist is worse served than one told nobody looked.
    "History Checked": PropertyType.CHECKBOX,
    "Present": PropertyType.CHECKBOX,
    "Retired At": PropertyType.DATE,
    "Evidence": PropertyType.RICH_TEXT,
    "Evidence Count": PropertyType.NUMBER,
    "Evidence Truncated": PropertyType.CHECKBOX,
}


@dataclass(frozen=True)
class PanelProjection:
    """How one panel becomes one Notion database."""

    #: Notion database name. Prefixed `CT_` throughout so that `CT_PROJECTS`
    #: can never be mistaken for the spec's `PROJECTS` database, which holds
    #: Current State written one Event at a time by `notion/sync.py` and is a
    #: completely different table with a completely different owner.
    database: str
    #: Human title of the panel, carried over so the two cannot drift.
    title: str
    #: `column -> (Notion property name, type)`. Must cover the panel's
    #: `columns` exactly — both directions are gated.
    columns: dict[str, tuple[str, PropertyType]]
    #: The panel column whose value is already the row key, if any. It gets
    #: no property of its own: `Row Key` is that value, and writing it twice
    #: is two places for one fact to drift. `None` means the row key is a
    #: composite the panel has no single column for (RISKS).
    key_column: str | None = None


# `evidence_count` on METRICS maps to the shared `Evidence Count` property
# rather than to one of its own. The two are the same number by construction
# — the panel sets `evidence_count` to `len(metric.evidence)` and puts those
# same refs on the row — and `TheMetricsEvidenceCountIsTheSharedOneTests`
# holds that so the shortcut cannot become a disagreement.
PANEL_PROJECTIONS: dict[str, PanelProjection] = {
    "METRICS": PanelProjection(
        database="CT_METRICS",
        title="KPI",
        key_column="key",
        columns={
            "key": (ROW_KEY_PROPERTY, PropertyType.TITLE),
            "label": ("Label", PropertyType.RICH_TEXT),
            "value": ("Value", PropertyType.NUMBER),
            # Free-text provenance, deliberately not named `Source`: the
            # DESKTOPS panel has a `source` column meaning `DESKTOP_1`, and
            # `controltower/dashboard.py` renamed this one for exactly that
            # collision.
            "derived_from": ("Derived From", PropertyType.RICH_TEXT),
            "evidence_count": ("Evidence Count", PropertyType.NUMBER),
        },
    ),
    "TEAMS": PanelProjection(
        database="CT_TEAMS",
        title="Team",
        key_column="team",
        columns={
            "team": (ROW_KEY_PROPERTY, PropertyType.TITLE),
            "display_name": ("Display Name", PropertyType.RICH_TEXT),
            "events": ("Events", PropertyType.NUMBER),
            "projects": ("Projects", PropertyType.RICH_TEXT),
            "blocked_projects": ("Blocked Projects", PropertyType.RICH_TEXT),
            "blocked_project_count": ("Blocked Project Count", PropertyType.NUMBER),
            "last_seen": ("Last Seen", PropertyType.DATE),
            "has_activity": ("Has Activity", PropertyType.CHECKBOX),
            # Always null. The column exists because the request asks for a
            # team's current Sprint and this system has none; a consumer that
            # receives the property empty learns that, and one that never
            # sees it learns nothing. Same argument the panel makes.
            "current_sprint": ("Current Sprint", PropertyType.RICH_TEXT),
        },
    ),
    "PROJECTS": PanelProjection(
        database="CT_PROJECTS",
        title="Project",
        key_column="project_id",
        columns={
            "project_id": (ROW_KEY_PROPERTY, PropertyType.TITLE),
            "teams": ("Teams", PropertyType.RICH_TEXT),
            "events": ("Events", PropertyType.NUMBER),
            "status": ("Status", PropertyType.SELECT),
            "state": ("State", PropertyType.SELECT),
            # Authored text, so RICH_TEXT and never SELECT — a select option
            # is a vocabulary, and a person's sentence is not one.
            "blocker": ("Blocker", PropertyType.RICH_TEXT),
            "blocker_team": ("Blocker Team", PropertyType.SELECT),
            "blocked_since": ("Blocked Since", PropertyType.DATE),
            "days_blocked": ("Days Blocked", PropertyType.NUMBER),
            "first_seen": ("First Seen", PropertyType.DATE),
            "last_seen": ("Last Seen", PropertyType.DATE),
            "days_idle": ("Days Idle", PropertyType.NUMBER),
            "completed_at": ("Completed At", PropertyType.DATE),
            "milestones": ("Milestones", PropertyType.RICH_TEXT),
            "sprint": ("Sprint", PropertyType.RICH_TEXT),
        },
    ),
    "DESKTOPS": PanelProjection(
        database="CT_DESKTOPS",
        title="Desktop",
        key_column="source",
        columns={
            "source": (ROW_KEY_PROPERTY, PropertyType.TITLE),
            "expected_team": ("Expected Team", PropertyType.SELECT),
            "display_name": ("Display Name", PropertyType.RICH_TEXT),
            "events": ("Events", PropertyType.NUMBER),
            "projects": ("Projects", PropertyType.RICH_TEXT),
            "last_seen": ("Last Seen", PropertyType.DATE),
            "days_silent": ("Days Silent", PropertyType.NUMBER),
            "has_activity": ("Has Activity", PropertyType.CHECKBOX),
            "role_mismatches": ("Role Mismatches", PropertyType.NUMBER),
            "mismatched_event_ids": ("Mismatched Event IDs", PropertyType.RICH_TEXT),
        },
    ),
    "RISKS": PanelProjection(
        database="CT_RISKS",
        title="Risk / Blocker",
        # No key column: a RISKS row key is `BLOCKER:<project>` /
        # `MISMATCH:<event>` / `CONFLICT:<event>`, a composite no single
        # column carries. `Row Key` holds it and `Kind` holds the half that
        # is a vocabulary.
        key_column=None,
        columns={
            "kind": ("Kind", PropertyType.SELECT),
            "project_id": ("Project ID", PropertyType.RICH_TEXT),
            "team": ("Team", PropertyType.SELECT),
            "blocker": ("Blocker", PropertyType.RICH_TEXT),
            "since": ("Since", PropertyType.DATE),
            "days_open": ("Days Open", PropertyType.NUMBER),
            "event_id": ("Event ID", PropertyType.RICH_TEXT),
            "source": ("Source", PropertyType.SELECT),
            "claimed_role": ("Claimed Role", PropertyType.SELECT),
            "expected_role": ("Expected Role", PropertyType.SELECT),
            "kept": ("Kept File", PropertyType.RICH_TEXT),
            "ignored": ("Ignored File", PropertyType.RICH_TEXT),
        },
    ),
}

#: Panels that **have** a source and deliberately get no Notion database,
#: with the reason. Keyed by panel key.
#:
#: The third state, and it needs a name for the same reason `UNSOURCED` did:
#: "sourced and projected", "unsourced", and "sourced but not projected" are
#: three different facts, and without a name for the third one a missing
#: database is indistinguishable from a forgotten one.
#: `EveryColumnHasAPropertyTests` holds every sourced panel
#: to being in exactly one of the first and third.
#:
#: Both entries are the same finding. ACTIVITY and COMPLETIONS are keyed by
#: `event_id` — a row per Event — and a Notion database of them would grow
#: one row per Event **forever**, because this repository does not delete
#: (docs/10 §46 forbids removing a collected Event, and the one deletion this
#: codebase has is a decision still open as BACKLOG B-7). The panels
#: themselves are bounded at `RECENT_LIMIT`; the *table* would not be,
#: because every Event that ages out of the window has already been written.
#:
#: That is docs/14 §3's own sentence one layer out — "Manifest는 Event 1건당
#: 줄을 쓰지 않는다 … 작업량에 비례해 커지는 것은 로그이며, 그러면 Manifest일
#: 수 없다" — and `EVIDENCE_IN_PAYLOAD` is the same rule applied to the
#: payload after it produced 2.0 MB at 6,000 Events.
#:
#: And it does not merely grow, it **breaks**, at a number this code can
#: state: `_retire_absent_rows()` lists the whole database every sync,
#: `RealNotionTransport.list_pages()` stops at `_SEARCH_PAGE_LIMIT` × 100 =
#: 1,000 rows, and a truncated listing makes reconciliation decline to run
#: at all (correctly — the alternative is retiring every row it did not
#: see). So past 1,000 Events the retirement stops, and the `Present`
#: filter an operator's view is built on quietly fills with stale rows.
#: `TheActivityTableWouldBreakAtAThousandRowsTests` measures it.
#:
#: What the operator loses is small and named: Notion still carries
#: `Last Seen` per project, per team and per Desktop (CT_PROJECTS /
#: CT_TEAMS / CT_DESKTOPS), and one `OPS_RUNS` row per execution. What it
#: does not carry is the Event-by-Event feed, which is on the Control Tower
#: screen and in `to_payload()` — both bounded — and whose source is
#: `runtime/events/processed/` either way.
UNPROJECTED_PANELS: dict[str, str] = {
    "ACTIVITY": (
        "Event 1건당 행 하나 — Notion Database가 Event 수만큼 무한히 자란다. "
        "이 저장소는 지우지 않으므로 창 밖으로 밀려난 행도 남고, 1,000행을 넘으면 "
        "`list_pages()`가 잘려 조정 pass 자체가 멈춘다(그때부터 `Present` 뷰가 "
        "조용히 낡는다). 화면과 payload에는 `RECENT_LIMIT`로 묶여 그대로 있다."
    ),
    "COMPLETIONS": (
        "ACTIVITY와 같은 이유 — 같은 행을 다른 기준으로 고른 것이므로 같은 "
        "무한 성장을 한다."
    ),
}

#: What an unsourced layer would need, in the panel's own words, and the fact
#: that it gets no database. Keyed by the `UNSOURCED_LAYERS` entry so a layer
#: that gains a source has one place to be removed from.
#:
#: Written as data rather than prose in a docstring because the alternative
#: is what `bootstrap_dashboard_databases()` had: four empty tables and a
#: paragraph in docs/13 as the only thing explaining them.
UNSOURCED_LAYER_NOTES: dict[str, str] = {
    layer: (
        "원천이 없다 — Event Schema(docs/02)에도 Company Repository에도 이 계층이 "
        "없다. Notion Database를 만들지 않는다: 아무 코드도 쓰지 않는 빈 표는 "
        "고장난 표와 구별되지 않는다(notion/dashboard.CONTRACTED_DATABASES 참조). "
        "원천을 정하는 것은 승인이 필요한 결정이며 BACKLOG에 있다."
    )
    for layer in UNSOURCED_LAYERS
}


def control_tower_databases() -> dict[str, dict[str, Any]]:
    """The five databases, in `create_database()`'s property-payload shape.

    Built from `PANEL_PROJECTIONS` rather than written out, so the schema and
    the payload cannot disagree about a property's type — the disagreement
    that produces an HTTP 400 on the first real run and nowhere earlier.
    """
    schemas: dict[str, dict[str, Any]] = {}
    for projection in PANEL_PROJECTIONS.values():
        properties: dict[str, Any] = {
            name: {kind.value: {}} for name, kind in COMMON_PROPERTIES.items()
        }
        for name, kind in projection.columns.values():
            properties[name] = {kind.value: {}}
        schemas[projection.database] = properties
    return schemas


# ---------------------------------------------------------------- payload


def _text(value: Any) -> str:
    """One panel value rendered as Notion text.

    A list becomes a comma-separated string. The panels put lists in
    `teams` / `projects` / `milestones` / `mismatched_event_ids`, and Notion
    has no list-of-strings property that can be created through the API
    (`multi_select` can, but its option names may not contain commas and
    these values are authored `project_id`s, so one comma in a project name
    would refuse the whole row).

    `None` becomes `""` rather than being omitted: an absent property and an
    empty one read the same in Notion's UI, but only the empty one proves the
    projection considered the column.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value)
    return str(value)


def _property(kind: PropertyType, value: Any) -> dict[str, Any]:
    """One Notion property payload for one already-redacted value.

    Every branch is null-safe, and that is load-bearing rather than
    defensive: the panels are full of present-and-null columns on purpose
    (`sprint`, `current_sprint`, `days_open` on a mismatch row, all three
    Desktop columns on a blocker row), because "every row of this panel has
    the same shape" is a property the model states and a projection has to
    keep. Notion accepts `null` for `date` and `select`; it does not accept
    an empty `select` **name**, which is why a falsy select becomes `None`
    and not `{"name": ""}`.
    """
    if kind is PropertyType.NUMBER:
        return {"number": value if isinstance(value, (int, float)) and not isinstance(value, bool) else None}
    if kind is PropertyType.CHECKBOX:
        return {"checkbox": bool(value)}
    if kind is PropertyType.DATE:
        text = _text(value)
        return {"date": {"start": text} if text else None}
    if kind is PropertyType.SELECT:
        text = _text(value)
        return {"select": {"name": text} if text else None}
    if kind is PropertyType.TITLE:
        return {"title": [{"text": {"content": _text(value)}}]}
    return {"rich_text": [{"text": {"content": _text(value)}}]}


def _is_iso_8601(text: str) -> bool:
    """Whether Notion would read `text` as a date.

    `datetime.fromisoformat()` rather than a regex: it is the same parser
    `events.schema._timestamp_error()` validates an Event's timestamp with,
    so "an Event timestamp is a valid Notion date" is true by construction
    instead of by two patterns agreeing. On Python 3.9 it is the stricter of
    the two — it refuses the trailing `Z` this project never emits — and
    stricter is the safe direction for a check whose job is to refuse before
    the API does.
    """
    try:
        datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return False
    return True


def _evidence_text(evidence: Sequence[Mapping[str, Any]]) -> str:
    """The row's evidence refs as one line — `event_id@at -> path`, joined.

    Same three fields `EvidenceRef.describe()` uses and in the same order, so
    a person reading the Notion cell and a person reading `ops_status.py` are
    reading the same sentence. Already capped at `EVIDENCE_IN_PAYLOAD` by
    `to_payload()`; `Evidence Count` carries the true total beside it.
    """
    return " | ".join(
        f"{ref.get('event_id', '')}@{ref.get('at', '')} -> {ref.get('path', '')}"
        for ref in evidence
    )


@dataclass(frozen=True)
class ProjectedRow:
    """One Notion row, ready to write, and where it came from."""

    database: str
    panel: str
    #: The value `find_or_create_by_title()` looks the row up by. Already
    #: `fit_key()`-shortened, so the write side and any later lookup agree.
    row_key: str
    properties: dict[str, Any] = field(default_factory=dict)


def project_panels(model: DashboardModel) -> list[ProjectedRow]:
    """Every sourced panel of `model`, as Notion rows.

    Built from `model.to_payload()`, never from the model's attributes — see
    the module docstring. Deterministic: panels in the model's order, rows in
    each panel's order, properties in `PANEL_PROJECTIONS`' declaration order.

    An unsourced panel produces nothing at all, not an empty row. Its layers
    are in `UNSOURCED_LAYER_NOTES`.

    A panel this module has no projection for is **skipped rather than
    guessed**. That was once an unreachable defence — every panel was either
    projected or unsourced — and it is now the ordinary path for the two in
    `UNPROJECTED_PANELS`, which have a real source and deliberately get no
    Notion database. `EveryPanelIsAccountedForTests` holds every panel to
    being in exactly one of the three states, so a panel reaching this branch
    is either one of those two or a hand-built model's.
    """
    payload = model.to_payload()
    generated_at = payload.get("generated_at")
    coverage = payload.get("coverage") or {}
    coverage_complete = bool(coverage.get("complete"))
    history_checked = bool(coverage.get("history_checked"))

    rows: list[ProjectedRow] = []
    for panel in payload.get("panels", []):
        if panel.get("status") != PanelStatus.SOURCED.value:
            continue
        mapping = PANEL_PROJECTIONS.get(panel.get("key", ""))
        if mapping is None:
            continue
        for row in panel.get("rows", []):
            values = row.get("values", {})
            evidence = row.get("evidence", [])
            # `fit_key()`, not `_fit_text()`, and applied here rather than
            # left to `fit_properties()` at the end.
            #
            # This value is what `find_by_title()` looks the row up by, so
            # "short enough" is not the only requirement — two different keys
            # must not become one string. `project_id` has no length limit
            # anywhere in `validate_event()`, so two projects agreeing on
            # their first 1,999 characters would share a row and write one
            # project's state over the other's. `fit_key()` appends a digest
            # of the whole value, which makes the mapping injective.
            #
            # Before the payload because `sync_control_tower()` passes
            # `row.row_key` to the lookup: the value written and the value
            # searched for are then the same object, not two functions that
            # have to agree. `notion.properties.KEY_PROPERTIES` does the same
            # job for the PROJECTS database and does not name `Row Key`,
            # which is why this cannot be left to it.
            row_key = fit_key(_text(row.get("key")))
            properties: dict[str, Any] = {
                ROW_KEY_PROPERTY: _property(PropertyType.TITLE, row_key),
                "Generated At": _property(PropertyType.DATE, generated_at),
                "Coverage Complete": _property(
                    PropertyType.CHECKBOX, coverage_complete
                ),
                "History Checked": _property(
                    PropertyType.CHECKBOX, history_checked
                ),
                # True on everything this projection produces. A row goes
                # False only by `_retire_absent_rows()`, and a row that comes
                # back — a project blocked again — is written True again by
                # this very line, with `Retired At` cleared.
                "Present": _property(PropertyType.CHECKBOX, True),
                "Retired At": _property(PropertyType.DATE, None),
                "Evidence": _property(PropertyType.RICH_TEXT, _evidence_text(evidence)),
                "Evidence Count": _property(
                    PropertyType.NUMBER, row.get("evidence_count", 0)
                ),
                "Evidence Truncated": _property(
                    PropertyType.CHECKBOX, row.get("evidence_truncated", False)
                ),
            }
            for column, (name, kind) in mapping.columns.items():
                if column == mapping.key_column:
                    continue  # `Row Key` already holds it.
                properties[name] = _property(kind, values.get(column))
            rows.append(
                ProjectedRow(
                    database=mapping.database,
                    panel=panel["key"],
                    row_key=row_key,
                    # The same bound `notion/sync.py` applies to the PROJECTS
                    # payload, applied here for the same reason and at the
                    # same place — the end of the builder, never inside
                    # `_property()`. A `blocker` is authored text with no
                    # length limit anywhere in `validate_event()`, and one
                    # character over 2,000 is an HTTP 400 for the whole row.
                    properties=fit_properties(properties),
                )
            )
    return rows


# ---------------------------------------------------------------- validation


def _item_text(prop: Mapping[str, Any], kind: str) -> str:
    return "".join(
        (item.get("text") or {}).get("content") or "" for item in (prop.get(kind) or [])
    )


def validate_rows(rows: Iterable[ProjectedRow]) -> list[str]:
    """Every reason Notion would refuse these rows, as messages. Empty is good.

    This exists because the alternative is finding out on the first real run,
    against a live Workspace, one row at a time — and a 400 is classified
    `PERMANENT` by `sync.PERMANENTLY_REFUSING_STATUS_CODES`, so the row never
    updates again and the queue entry never clears (BACKLOG A-22). Every
    check below is a rule the live API enforces and the in-memory double
    cannot fully stand in for.

    What is checked, and why each one is a real refusal rather than a
    preference:

        unknown property        Notion 400s on a property the database does
                                not declare
        wrong type              a `number` payload against a `rich_text`
                                property is a 400
        text over the limit     `RICH_TEXT_LIMIT`, the defect C50 measured
        empty select name       Notion refuses `{"name": ""}`; a null select
                                is how "no value" is spelled
        comma in a select name  select and multi-select option names may not
                                contain commas
        a date that is not one   `date.start` must be ISO 8601; anything else
                                is a 400, and `_property()` will put any
                                string there
        duplicate row key       two rows with one title: the first write
                                creates, the second finds it and overwrites,
                                and one of the two subjects' state is simply
                                gone. Same merged-row failure `fit_key()`
                                prevents for a long key, arriving instead
                                from two genuinely different rows
    """
    schemas = control_tower_databases()
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()

    for row in rows:
        schema = schemas.get(row.database)
        if schema is None:
            errors.append(f"{row.panel}: no database named {row.database!r}")
            continue

        identity = (row.database, row.row_key)
        if identity in seen:
            errors.append(
                f"{row.database}: two rows share the row key {row.row_key!r} — "
                "one would overwrite the other"
            )
        seen.add(identity)

        for name, prop in row.properties.items():
            declared = schema.get(name)
            if declared is None:
                errors.append(f"{row.database}.{name}: not in the database schema")
                continue
            kind = next(iter(declared))
            if kind not in prop:
                errors.append(
                    f"{row.database}.{name}: schema says {kind}, payload is "
                    f"{sorted(prop)}"
                )
                continue
            if kind in ("title", "rich_text"):
                text = _item_text(prop, kind)
                if len(text) > RICH_TEXT_LIMIT:
                    errors.append(
                        f"{row.database}.{name}: {len(text)} characters, over "
                        f"the {RICH_TEXT_LIMIT} Notion accepts"
                    )
            elif kind == "select":
                option = prop.get("select")
                if option is not None:
                    option_name = option.get("name") or ""
                    if not option_name:
                        errors.append(
                            f"{row.database}.{name}: empty select name — use a "
                            "null select for 'no value'"
                        )
                    if "," in option_name:
                        errors.append(
                            f"{row.database}.{name}: select name {option_name!r} "
                            "contains a comma, which Notion refuses"
                        )
            elif kind == "date":
                # A `date` whose `start` is not ISO 8601 is an HTTP 400, and
                # the docstring above claims to list **every** reason Notion
                # would refuse a row. It did not list this one: measured, a
                # row carrying `"not-a-date"` and `"2026-13-45T99:00:00"`
                # came back clean.
                #
                # Nothing produces such a value today, and that is checked
                # rather than asserted — `EveryDateThisProjectionEmitsIsISOTests`
                # runs the real fold and reads the dates back out. This is
                # here for the column that does not exist yet: `_property()`
                # turns any string into a `date.start`, so the first DATE
                # column fed by something other than an Event timestamp
                # would reach Notion, 400, and be classified PERMANENT with
                # nothing on this side having said why.
                value = prop.get("date")
                if value is not None:
                    start = value.get("start")
                    if not isinstance(start, str) or not _is_iso_8601(start):
                        errors.append(
                            f"{row.database}.{name}: {start!r} is not an "
                            "ISO 8601 date, which Notion refuses"
                        )

        missing = sorted(set(schema) - set(row.properties))
        if missing:
            errors.append(
                f"{row.database} row {row.row_key!r}: no value for {missing} — "
                "a column the projection declared and did not fill"
            )
    return errors


# ---------------------------------------------------------------- write


def _reason(exc: BaseException) -> str:
    """An exception rendered for a `ProjectionResult`, safely.

    The same treatment `DashboardModel.to_payload()` gives an `unreadable`
    reason, and for the same measured reason. A `NotionAPIError` carries the
    remote **response body**, and `oplog.append_line()`'s docstring records
    what that can contain: a proxy or captive portal answering in Notion's
    place is free to echo request headers back, and one 502 page containing
    `Authorization: Bearer ntn_...` put the token straight into
    notion_sync.log.

    Applied here rather than left to the caller because these strings have no
    caller yet (§ the module docstring). A field that is safe only if
    whoever wires it up remembers is a field that will be logged unredacted
    once — and `record_run()`'s `error` reaches a log through
    `_log_dashboard()`, which redacts, so a reader would reasonably assume
    this one is the same shape.

    `bounded()` too: an `except Exception` catches anything, and a result a
    caller may print must not be able to grow without limit.
    """
    return redact(one_line(bounded(f"{type(exc).__name__}: {exc}")))


class ProjectionOutcome(enum.Enum):
    RECORDED = "RECORDED"
    SKIPPED_NOT_CONFIGURED = "SKIPPED_NOT_CONFIGURED"
    REFUSED_INVALID = "REFUSED_INVALID"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProjectionResult:
    outcome: ProjectionOutcome
    created: int = 0
    updated: int = 0
    skipped: int = 0
    #: Rows found in Notion that this projection no longer produces, marked
    #: `Present = false`. A resolved blocker is the ordinary case.
    retired: int = 0
    #: Databases whose rows could not be listed, so nothing could be retired
    #: in them. Named rather than counted: "reconciliation did not run" is a
    #: sentence an operator has to be able to act on, and a `0` in `retired`
    #: is indistinguishable from "nothing needed retiring".
    unreconciled: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    # No `ok` and no `written` accessor. Both were written, and branch
    # coverage found neither had a caller outside a test on its first run —
    # the shape `DeadCapabilityInventoryTests` catches, and the reason
    # `TeamRollup.days_silent()` and `projection.contracted_columns()` were
    # both removed. `outcome is ProjectionOutcome.RECORDED` and
    # `created + updated` are each the whole of what the wrapper did, and a
    # wrapper nobody calls reads as though somebody needed it.
    #
    # The four counts stay separate on purpose besides: `created` and
    # `updated` answer different questions on a projection whose whole point
    # is that rows outlive runs — a second run that reports `created > 0` is
    # reporting that a row it should have found was not there.


def _row_key_of(page: Any) -> str:
    """The `Row Key` title of one page **Notion returned**.

    `plain_text`, never `text.content`, and that is the whole of the second
    defect this function was rewritten for. Notion stores a title as one item
    per run of identical formatting, and an item that is not literal text — a
    mention, an equation — carries no `"text"` key at all.
    `notion/properties._extract_rich_text()` measured exactly this shape
    against `ExecutionPlanSync._update()` and reads `plain_text` because of
    it, and `notion/dashboard._page_title()` does the same. This was the
    third reader of the same Notion answer and the only one still reading the
    key that can be absent.

    What that cost, measured end to end: Notion's `title equals` filter
    compares plain text, so `sync_control_tower()` **finds** the row and
    refreshes it — and then this pass reads a key that is not the one it just
    wrote, does not find it among the live keys, and retires the row. A live
    `CT_PROJECTS` row went `Present = false` with `Retired At` stamped in the
    same sync that had updated it, and every later run repeated it. An
    operator's view filters on `Present`, so a live project leaves the
    Control Tower and the only trace is `retired` counting one higher.

    Raises `TypeError` on a shape Notion does not document rather than
    answering `""`. The distinction is load-bearing: `""` is not a live row
    key (`TheTitleColumnMayNeverBeNullTests` holds every projected row to a
    non-empty one), so an unreadable page answered as `""` would be **retired
    on the strength of not being understood**. The caller turns the exception
    into "this listing could not be read" instead, which retires nothing.
    """
    if not isinstance(page, Mapping):
        raise TypeError(f"page is {type(page).__name__}, not an object")
    properties = page.get("properties") or {}
    if not isinstance(properties, Mapping):
        raise TypeError(f"properties is {type(properties).__name__}, not an object")
    prop = properties.get(ROW_KEY_PROPERTY) or {}
    if not isinstance(prop, Mapping):
        raise TypeError(
            f"{ROW_KEY_PROPERTY} is {type(prop).__name__}, not an object"
        )
    items = prop.get("title") or []
    if not isinstance(items, (list, tuple)):
        raise TypeError(
            f"{ROW_KEY_PROPERTY}.title is {type(items).__name__}, not a list"
        )
    parts: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError(
                f"{ROW_KEY_PROPERTY}.title item is {type(item).__name__}, "
                "not an object"
            )
        text = item.get("plain_text")
        if text is None:
            inner = item.get("text")
            text = inner.get("content") if isinstance(inner, Mapping) else None
        parts.append(text if isinstance(text, str) else "")
    return "".join(parts)


def _is_present(page: Mapping[str, Any]) -> bool:
    """Whether Notion's copy of this row still says `Present`.

    An **absent** property reads as True: a database created before the column
    existed, or a row a person added by hand, is present until something says
    otherwise, and the only thing False does here is skip the row.

    A property that is there but is not shaped like a Notion property raises,
    for `_row_key_of()`'s reason — that is the remote answering with something
    that is not Notion, and the caller's answer to that is to reconcile
    nothing rather than to guess per row.
    """
    properties = page.get("properties") or {}
    if not isinstance(properties, Mapping):
        raise TypeError(f"properties is {type(properties).__name__}, not an object")
    present = properties.get("Present")
    if present is None:
        return True
    if not isinstance(present, Mapping):
        raise TypeError(f"Present is {type(present).__name__}, not an object")
    return bool(present.get("checkbox", True))


def _retire_absent_rows(
    client: NotionClient, live_keys: set, generated_at: str | None
) -> tuple[int, bool, list[str]]:
    """Mark every row of `client`'s database that this run did not produce.

    Returns `(retired, reconciled, errors)`. `reconciled` is False when the
    transport cannot list the database, listed it only partially, or answered
    with something this code cannot read — a reconciliation over a listing it
    does not understand would retire every row it did not recognise, which is
    strictly worse than not reconciling at all. That is why the truncation
    flag is consulted rather than trusted to be absent, and why the listing is
    read through before the first write rather than row by row: the failure
    all three prevent is the loud one.

    Already-retired rows are left alone rather than re-written. A projection
    that touched every historical row on every run would make `Retired At`
    move forever and turn a quiet week into hundreds of API calls.
    """
    try:
        pages = client.list_pages()
    except NotImplementedError:
        return 0, False, []
    except Exception as exc:  # noqa: BLE001  (CEO ④)
        return 0, False, [_reason(exc)]

    if client.list_truncated:
        return 0, False, []

    # The whole listing is read before anything is retired, and the read is
    # guarded, because `list_pages()` hands back whatever the response body's
    # `results` held. `_reason()` above already records what that can be — "a
    # proxy or captive portal answering in Notion's place is free to echo
    # request headers back" — and a body shaped `{"results": ["..."]}` or
    # `{"results": {...}}` is the same actor answering.
    #
    # Measured on HEAD, twelve injected response shapes: **nine raised
    # `AttributeError` out of this function**, out of `sync_control_tower()`
    # — whose docstring says *Never raises* — and into the caller. That
    # contract is CEO Decision ④ ("Dashboard 기록 실패는 Runtime을 절대
    # 중단시키면 안 된다"), so the escape was not a rough edge; it was the one
    # promise this module makes to the Runner it is meant to be wired into.
    #
    # An unreadable listing gets the answer truncation already gets, for the
    # identical reason. Nothing has been written at this point, so refusing
    # costs exactly the pass and no state.
    try:
        entries = [(page, _row_key_of(page), _is_present(page)) for page in pages]
    except Exception as exc:  # noqa: BLE001  (CEO ④)
        return 0, False, [f"unreadable listing: {_reason(exc)}"]

    retired = 0
    errors: list[str] = []
    for page, key, present in entries:
        if key in live_keys:
            continue
        if not present:
            continue  # already retired; leave `Retired At` where it is
        try:
            client.update_project(
                page["id"],
                {
                    "Present": _property(PropertyType.CHECKBOX, False),
                    "Retired At": _property(PropertyType.DATE, generated_at),
                },
            )
        except Exception as exc:  # noqa: BLE001  (CEO ④)
            errors.append(f"retire {redact(one_line(key))}: {_reason(exc)}")
            continue
        retired += 1
    return retired, True, errors


def sync_control_tower(
    clients: Mapping[str, NotionClient] | None, model: DashboardModel
) -> ProjectionResult:
    """Write every projected row. Never raises.

    `clients` maps a database name from `control_tower_databases()` to a
    `NotionClient` bound to that database's id. A name with no client is
    counted in `skipped` and is not an error — a partial deployment is a
    supported shape here for the same reason an unconfigured Notion is
    (docs/04): the Control Tower's job is Company History, and a View that
    is only half wired must not be the thing that fails a run.

    Never raises, and the contract is inherited rather than invented: CEO
    Decision ④ — "Dashboard 기록 실패는 Runtime을 절대 중단시키면 안 된다" —
    is what `notion/dashboard.record_run()` is built around, and this writes
    to the same kind of sink for the same kind of reason.

    Validation runs **before** the first write, not per row. A payload that
    Notion will refuse is refused here as a whole, because a per-row check
    would write the rows that pass and leave the projection half-updated:
    some rows this morning's, some last week's, and nothing on the Notion
    side saying which is which. All-or-nothing is the only state a reader can
    interpret.
    """
    if not clients:
        return ProjectionResult(outcome=ProjectionOutcome.SKIPPED_NOT_CONFIGURED)

    try:
        rows = project_panels(model)
    except Exception as exc:  # noqa: BLE001  (CEO ④)
        return ProjectionResult(
            outcome=ProjectionOutcome.FAILED, errors=(_reason(exc),)
        )

    # The instant every retirement is stamped with, taken from the model
    # rather than from a clock read here — the same rule
    # `DashboardModel.generated_at` states, and the reason a `Retired At` can
    # be compared with the `Generated At` of the rows beside it.
    generated_at = model.generated_at

    invalid = validate_rows(rows)
    if invalid:
        return ProjectionResult(
            outcome=ProjectionOutcome.REFUSED_INVALID, errors=tuple(invalid)
        )

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []
    live_keys: dict = {}
    for row in rows:
        live_keys.setdefault(row.database, set()).add(row.row_key)
    for row in rows:
        client = clients.get(row.database)
        if client is None:
            skipped += 1
            continue
        try:
            # Find-then-**update**, not `find_or_create_by_title()`.
            #
            # That helper is right for `OPS_RUNS` and wrong for every
            # database here, and the difference is what the row key means.
            # A `Run ID` is unique to one execution, so a row that already
            # exists is a row that was already finished and re-writing it
            # would be the duplicate the helper exists to prevent.
            #
            # A Control Tower row key is a `project_id`, a team, a Desktop —
            # an identity that **outlives every run**. `find_or_create` would
            # create each row once and never touch it again: a project's
            # `Events`, `State`, `Blocker` and `Generated At` would freeze at
            # whatever they were the first time the projection ran, forever,
            # while the terminal beside it showed the truth. That is the
            # stale-View failure this projection exists to close, arrived at
            # by way of the wrong write primitive.
            existing = client.find_by_title(
                property_name=ROW_KEY_PROPERTY, value=row.row_key
            )
            if existing is None:
                client.create_project(row.properties)
                created += 1
            else:
                client.update_project(existing["id"], row.properties)
                updated += 1
        except Exception as exc:  # noqa: BLE001  (CEO ④)
            errors.append(f"{row.database}/{row.row_key}: {_reason(exc)}")

    # Reconciliation runs after every write, over every database this
    # deployment has a client for — including ones this run produced no rows
    # for, which is precisely when a stale row is most likely (`CT_RISKS` on
    # the first quiet day after a blocker cleared).
    retired = 0
    unreconciled: list[str] = []
    for database, client in sorted(clients.items()):
        if database not in control_tower_databases():
            continue
        count, reconciled, retire_errors = _retire_absent_rows(
            client, live_keys.get(database, set()), generated_at
        )
        retired += count
        errors.extend(f"{database}: {message}" for message in retire_errors)
        if not reconciled:
            unreconciled.append(database)

    outcome = ProjectionOutcome.FAILED if errors else ProjectionOutcome.RECORDED
    return ProjectionResult(
        outcome=outcome,
        created=created,
        updated=updated,
        skipped=skipped,
        retired=retired,
        unreconciled=tuple(unreconciled),
        errors=tuple(errors),
    )


__all__ = [
    "COMMON_PROPERTIES",
    "UNPROJECTED_PANELS",
    "DASHBOARD_SCHEMA_VERSION",
    "PANEL_PROJECTIONS",
    "ROW_KEY_PROPERTY",
    "UNSOURCED_LAYER_NOTES",
    "PanelProjection",
    "ProjectedRow",
    "ProjectionOutcome",
    "ProjectionResult",
    "PropertyType",
    "control_tower_databases",
    "project_panels",
    "sync_control_tower",
    "validate_rows",
]
