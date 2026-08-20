"""The Control Tower as data — panels, and the payload that leaves the machine.

Why this exists
---------------
`rollup.py` answers the business questions from Execution Evidence, and
`ops_status.py` prints the answers. Between those two there was nothing: the
renderer reached into `CompanyRollup` field by field and composed sentences,
so "what the Control Tower shows" existed only as terminal output. Anything
else that wants the same view — a Notion projection above all — would have
had to re-derive it from the rollup, and two derivations of one view is how
the screen and the projection start disagreeing about the same day.

This is that missing layer: the rollup arranged into the panels a Control
Tower has, once, with a serialisation (`to_payload()`) for a sink that is not
a terminal. It measures nothing, reads no file, and stores no state — it
takes a `CompanyRollup` and rearranges it.

The panels are the request's own five
-------------------------------------
    ① COMPANY GOALS          목표 / 진행률 / KPI / 담당 Team
    ② TEAM DASHBOARD         팀별 진행률 / 현재 Sprint / Blocker
    ③ PROJECT / SPRINT       Sprint / 기간 / Backlog / 완료·진행·대기
    ④ DESKTOP / OPS          Desktop 1·2·4 / Agent / Runner / Last Run
    ⑤ RISK / BLOCKER         실패 / Pending / Stale / Decision Required

and the honest answer to two of them is "this system has no source for that".
`PanelStatus.UNSOURCED` is a first-class value for exactly that reason. An
empty panel and an unsourced panel render identically if the model cannot
tell them apart, and they mean opposite things — "아무 일도 없었다" versus
"물어볼 곳이 없다". `UNSOURCED_LAYERS` names the four layers
(`COMPANY_GOAL`, `TEAM_GOAL`, `SPRINT`, `TASK`), and
`EveryUnsourcedLayerIsClaimedByExactlyOnePanelTests` holds this module to
covering each of them exactly once — so the day one of them gains a source,
the model has a place to put it and a test that says so.

Where ⑤'s operational half lives, and why it is not here
-------------------------------------------------------
The request's ④/⑤ also ask for Agent state, Runner state, Last Run, Backup,
Delivery, Recovery and Notion Sync. Every one of those already reaches Notion
through the `OPS_RUNS` row `notion/dashboard.record_run()` writes:

    Daily          `Generated Days` / `Reused Days`
    Delivery       `Transport Moved` / `Transport Blocked`
    History        `Accepted` / `Duplicate` / `Rejected` / `Failed Steps`
    Backup         `Backup Status` / `Deleted Files`
    Recovery       `Reused Days` / `Deleted Files`
    Notion Sync    `Notion Synced` / `Skipped` / `Retried` / `Unreadable` /
                   `Queued`
    Desktop        `Desktops Reporting` / `Role Mismatches`  (C47)

Restating them here would be a second opinion about a run, and `runsummary`
is deliberately a leaf so that every reporter reads the same manifest. This
module covers the half that had no projection at all — the *work*.

Redaction happens on the way out, not on the way in
---------------------------------------------------
`project_id`, `blocker`, `milestone` and `event_id` are strings a person
typed on another Desktop; `validate_event()` only type-checks them, so any of
them can carry a credential (measured, C47 §11-12). The model therefore keeps
them **verbatim** — a rollup that quietly rewrote its own evidence would make
`EvidenceRef` unusable for finding the file — and `to_payload()`, which is
the boundary where the value leaves this machine, applies
`redact(one_line(...))` to precisely the authored fields. That is the same
split `ops_status.py::_authored()` makes and for the same stated reason:
a message that carries authored text redacts at the place it is produced.

Nothing here has a Notion client
--------------------------------
`to_payload()` is the hand-off contract for a projection that needs a
credentialled Workspace, which this repository does not have (BACKLOG A-8).
It is exercised as the serialisation of the very model `ops_status.py` puts
on screen, so the two cannot drift; the missing piece is a sink, not a
derivation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from datetime import date as date_type
from datetime import datetime
from typing import Any, Mapping

from oplog import bounded, one_line, redact

from .rollup import (
    UNSOURCED_LAYERS,
    CompanyRollup,
    EvidenceRef,
)

# Bumped when the shape of `to_payload()` changes in a way a consumer would
# have to be taught. Same role as `runsummary.SCHEMA_VERSION`: the payload is
# read by something that was written against a version of it.
DASHBOARD_SCHEMA_VERSION = "1.0"

# How many `EvidenceRef`s one payload row carries. The model keeps every one
# of them; this bounds only what leaves the machine.
#
# docs/14 §3 already settled this argument for the Run Manifest — "Manifest는
# Event 1건당 줄을 쓰지 않는다 … 작업량에 비례해 커지는 것은 로그이며, 그러면
# Manifest일 수 없다" — and the first version of this payload broke it.
# Measured at 6,000 Events: every Event appears in four rows (its metric, its
# project, its team, its Desktop), so `to_payload()` produced **2.0 MB** and
# took 382 ms, both growing without bound. With this cap the same evidence
# costs 27 KB.
#
# Nothing is hidden by the cap: `evidence_count` is always the true total and
# `evidence_truncated` says when the list is short, so a consumer can never
# read "5 files" as "all the files". The refs that are dropped are still on
# this machine in `processed/`, which is where a person goes anyway — the
# payload's job is to say *which number came from where*, and the first few
# named files plus an honest count do that.
EVIDENCE_IN_PAYLOAD = 5


class PanelStatus(enum.Enum):
    """Why a panel has the rows it has — including none.

    `SOURCED` with no rows is a true and useful statement ("no project moved
    in this period"). `UNSOURCED` is a different statement about a different
    thing, and the whole point of separating them is that an operator who
    cannot tell the two apart will read the second as the first.
    """

    SOURCED = "SOURCED"
    UNSOURCED = "UNSOURCED"


# Fields whose value cannot have come from an Event, and are therefore the
# only ones `_out()` is allowed to leave un-redacted. Everything else in a
# payload row goes through `redact()`.
#
# This list is the small one on purpose, and the first draft had it the other
# way round — an `AUTHORED_KEYS` allow-list of the fields to redact. The test
# suite broke it immediately: `DashboardRow.key` is the `project_id` on the
# PROJECTS panel, so a secret-shaped project name reached the payload as a row
# key while its own `project_id` value was redacted beside it. A list of what
# to protect has to be complete to work, and every column added later is a
# chance to forget; a list of what is *provably* safe fails closed instead.
#
# Each name here is a value `validate_event()` constrains to a fixed set
# (`events.SOURCES`, `events.ROLES`, `events.STATUSES`), a word this module
# itself chose (`state`, `kind`), or a display name out of
# `notion.properties.ROLE_DISPLAY_NAMES`. None of them can carry text a
# person typed.
_UNAUTHORED_KEYS: frozenset[str] = frozenset(
    {
        "team",
        "teams",
        "display_name",
        "expected_team",
        "source",
        "status",
        "state",
        "kind",
        "claimed_role",
        "expected_role",
        "blocker_team",
    }
)


@dataclass(frozen=True)
class DashboardRow:
    """One line of a panel.

    `key` is the row's identity in its panel — `project_id`, `role`,
    `source` — so a consumer can diff two payloads without positional
    matching. `values` is an ordered mapping because the panel declares its
    own `columns` and the two are checked against each other
    (`EveryRowFillsTheColumnsItsPanelDeclaresTests`); a row that grew a field
    its panel never announced would otherwise reach a Notion projection as a
    column nobody created.
    """

    key: str
    values: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class DashboardPanel:
    key: str
    title: str
    status: PanelStatus
    columns: tuple[str, ...] = ()
    rows: tuple[DashboardRow, ...] = ()
    # Where the rows came from, in one sentence — the same discipline
    # `rollup.Metric.source` applies to a number. A panel whose provenance is
    # not written down is one nobody can check.
    source: str = ""
    # For an UNSOURCED panel: what is missing and what decision would supply
    # it. For a SOURCED panel: a qualifier on the rows, or None.
    note: str | None = None
    # The `UNSOURCED_LAYERS` entries this panel accounts for. Empty for a
    # sourced panel.
    unsourced_layers: tuple[str, ...] = ()

    # No `is_sourced` accessor. `panel.status is PanelStatus.SOURCED` is the
    # whole of it, and this project has removed such accessors before for the
    # reason `TeamRollup` states about `days_silent()`: a wrapper nobody
    # calls is a capability with no caller, and it reads as though somebody
    # needed it. Branch coverage over `src/controltower/` found this one.


@dataclass(frozen=True)
class Coverage:
    """What the numbers in this model do and do not cover.

    The panels answer "what happened"; this answers "over what". They are
    different questions and only one of them survives a restore.

    `runtime/events/processed/` is Execution Evidence (docs/14 §2) and Backup
    scope is `daily/` and `monthly/` only (docs/08 §26), so a machine
    restored from the remote gets its whole Company History back and **none
    of its Events**. Every panel then says zero, truthfully, about a company
    that did a great deal — and nothing in the panels themselves can tell
    that apart from a quiet week. `history_uncovered_from` is what tells them
    apart.

    It is a **qualifier, not an alert**: nothing brings those Events back, and
    a standing alarm nobody can clear is the thing this project keeps
    removing. Same posture as `unreadable`.

    `history_uncovered_from` comes from the caller because it is a fact about
    Company History, which lives in `local_master/daily/` — a directory this
    module does not read and must not start reading (it takes a rollup and
    rearranges it). `ops_status.py` already derives it; `with_history_coverage()`
    is how it hands it over.
    """

    evidence_from: str | None = None
    evidence_to: str | None = None
    unreadable: int = 0
    history_uncovered_from: str | None = None
    # Files whose `event_id` another file already carried. Counted once by
    # the rollup and reported here rather than dropped in silence — see
    # `rollup.DuplicateEvent` for how two files come to claim one Event.
    #
    # Deliberately NOT an input to `complete`: a folded duplicate makes the
    # numbers *right*, not partial, and a qualifier that fires on a correct
    # answer is the standing alarm this project keeps removing. The half that
    # is a real problem — two files, one id, different contents — is a RISKS
    # row instead, where an operator has something to do about it.
    duplicates: int = 0

    @property
    def complete(self) -> bool:
        """True when every number in the model covers everything it claims to.

        Two quite different ways to be incomplete, and both belong here
        because a consumer's next question is the same for either: "is what I
        am looking at the whole picture?"

            unreadable                a file is there and could not be used
            history_uncovered_from    the work is recorded and its evidence
                                      is gone
        """
        return self.unreadable == 0 and self.history_uncovered_from is None


@dataclass(frozen=True)
class DashboardModel:
    """The whole Control Tower, arranged, for one moment.

    `generated_at` is the `now` the caller passed, not a clock read here: a
    derivation that reads the clock cannot be tested for the answer it gives
    at a given instant, and every age in this model (`days_blocked`,
    `days_silent`) is measured against it.
    """

    generated_at: str
    panels: tuple[DashboardPanel, ...] = ()
    events_read: int = 0
    # `(filename, reason)` for evidence that is on disk and could not be
    # used. Carried at the top of the model rather than inside a panel
    # because it qualifies every number in all of them — exactly the sentence
    # `ops_status.py` prints as "아래 숫자는 그만큼 적다".
    unreadable: tuple[tuple[str, str], ...] = ()
    since: str | None = None
    until: str | None = None
    coverage: Coverage = field(default_factory=Coverage)
    schema_version: str = DASHBOARD_SCHEMA_VERSION

    def with_history_coverage(
        self, history_uncovered_from: date_type | None
    ) -> "DashboardModel":
        """This model, plus the one coverage fact it cannot derive itself.

        Returns a new model rather than mutating: everything here is frozen,
        and a view that could be edited after it was built is a view two
        readers can disagree about.

        Called with `None` — the ordinary case, where Company History starts
        no earlier than the evidence — this is still meaningful and still
        worth calling: it is the difference between "asked and there is no
        gap" and "never asked", which is the same distinction the panels make
        between empty and unsourced.
        """
        return replace(
            self,
            coverage=replace(
                self.coverage,
                history_uncovered_from=(
                    history_uncovered_from.isoformat()
                    if history_uncovered_from is not None
                    else None
                ),
            ),
        )

    def panel(self, key: str) -> DashboardPanel | None:
        for item in self.panels:
            if item.key == key:
                return item
        return None

    @property
    def unsourced_panels(self) -> tuple[DashboardPanel, ...]:
        return tuple(p for p in self.panels if p.status is PanelStatus.UNSOURCED)

    def to_payload(self) -> dict[str, Any]:
        """The model as JSON-safe data, with authored text redacted.

        Deterministic by construction: every container below is a list built
        in the model's own order, and no `set` or `dict` iteration order
        reaches the output. Two runs over the same evidence produce the same
        bytes, which is what makes a diff between two payloads mean a
        difference in the work.
        """
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "since": self.since,
            "until": self.until,
            "events_read": self.events_read,
            "coverage": {
                "evidence_from": self.coverage.evidence_from,
                "evidence_to": self.coverage.evidence_to,
                "unreadable": self.coverage.unreadable,
                "duplicates": self.coverage.duplicates,
                "history_uncovered_from": self.coverage.history_uncovered_from,
                "complete": self.coverage.complete,
            },
            "unreadable": [
                # Both fields redacted, and the first draft got this wrong on
                # the reasoning "a filename and an exception message are not
                # authored Event text". Both are.
                #
                #   file    the Event file is named after the Event
                #           (`safe_event_filename()`), so a secret-shaped
                #           `event_id` is a secret-shaped filename;
                #   reason  `validate_event()` **echoes the value it
                #           rejected** — `invalid source: '…'`,
                #           `timestamp is not valid ISO-8601: '…'`.
                #
                # Measured: one hand-written file in `processed/` (which
                # docs/11 permits, and which is also what a partial restore
                # leaves) with a credential-shaped `source` put that
                # credential into the payload twice.
                #
                # `bounded()` as well, for `oplog.append_line()`'s reason: an
                # `except Exception` catches anything at all, and a report
                # that can grow without limit is one that fills a disk.
                {
                    "file": redact(one_line(name)),
                    "reason": redact(one_line(bounded(str(reason)))),
                }
                for name, reason in self.unreadable
            ],
            "panels": [
                {
                    "key": panel.key,
                    "title": panel.title,
                    "status": panel.status.value,
                    "source": panel.source,
                    "note": panel.note,
                    "columns": list(panel.columns),
                    "unsourced_layers": list(panel.unsourced_layers),
                    "rows": [
                        {
                            # Always redacted, never conditionally: the row
                            # key is the `project_id` on one panel and a
                            # composite of it on another.
                            "key": redact(one_line(row.key)),
                            "values": {
                                name: _out(name, row.values.get(name))
                                for name in panel.columns
                            },
                            "evidence": [
                                {
                                    # `path` too. The collected file is named
                                    # after the Event, so a secret-shaped
                                    # `event_id` is a secret-shaped filename
                                    # — and `ops_status.py` already redacts
                                    # `EvidenceRef.describe()` whole for that
                                    # reason. The un-redacted refs stay on
                                    # the model for a reader that is on this
                                    # machine already.
                                    "event_id": redact(one_line(ref.event_id)),
                                    "at": redact(one_line(ref.at)),
                                    "path": redact(one_line(ref.path)),
                                }
                                for ref in row.evidence[:EVIDENCE_IN_PAYLOAD]
                            ],
                            # Always the true total, never the length of the
                            # list above — see `EVIDENCE_IN_PAYLOAD`.
                            "evidence_count": len(row.evidence),
                            "evidence_truncated": len(row.evidence)
                            > EVIDENCE_IN_PAYLOAD,
                        }
                        for row in panel.rows
                    ],
                }
                for panel in self.panels
            ],
        }


def _out(name: str, value: Any) -> Any:
    """One value on its way out of the machine.

    Numbers, booleans and `None` pass through — there is nothing in an `int`
    to redact, and turning a count into a string would make the payload
    harder to consume than it is to protect (a projection writes these into
    Notion `number` properties).

    Every string is `one_line()`d, always: a newline inside a `project_id` is
    accepted by `validate_event()` (BACKLOG A-15) and one reached an
    ATTENTION line as a forged report before C47 caught it. Redaction is the
    default on top of that, and `_UNAUTHORED_KEYS` is the short list of
    exemptions — see the comment there for why the exemption list is the one
    that is short.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    protect = name not in _UNAUTHORED_KEYS
    if isinstance(value, (tuple, list)):
        return [redact(one_line(item)) if protect else one_line(item) for item in value]
    return redact(one_line(value)) if protect else one_line(value)


# ------------------------------------------------------------------ panels

_GOAL_NOTE = (
    "이 시스템에는 Company Goal / Team Goal의 원천이 없다 — Event Schema에도 "
    "Company Repository에도 그 계층이 없고, docs/14 §1이 Notion을 "
    "'View이며 절대 Source가 아니다'로 고정하므로 Notion에 적어 넣고 권위로 삼을 수도 "
    "없다. 원천을 Company Repository 산출물로 둘지 Event Schema 필드로 둘지가 "
    "명세 결정이며 BACKLOG에 있다."
)

_SPRINT_NOTE = (
    "이 시스템에는 Sprint / Task(회사 업무)의 원천이 없다 — `Sprint`는 이 저장소에서 "
    "주석 속 개발 용어로만 등장하고 `BACKLOG.md`는 **개발** 백로그다. Project는 "
    "원천이 있으므로 PROJECTS 패널에 있다. 결정과 조건은 BACKLOG."
)


def build_dashboard(rollup: CompanyRollup, *, now: datetime) -> DashboardModel:
    """Arrange one `CompanyRollup` into the Control Tower's panels.

    `now` is the instant every age in the model is measured against, and is
    the caller's rather than this function's — see `DashboardModel`.
    """
    return DashboardModel(
        generated_at=now.isoformat(),
        panels=(
            _goals_panel(),
            _metrics_panel(rollup),
            _teams_panel(rollup),
            _projects_panel(rollup, now),
            _sprints_panel(),
            _desktops_panel(rollup, now),
            _risks_panel(rollup, now),
        ),
        events_read=rollup.events_read,
        unreadable=rollup.unreadable,
        since=rollup.since.isoformat() if rollup.since is not None else None,
        until=rollup.until.isoformat() if rollup.until is not None else None,
        coverage=_coverage(rollup),
    )


def _evidence_day(iso: str | None) -> date_type | None:
    """The date part of a project's `first_seen` / `last_seen`.

    Both guards below are unreachable from `build_dashboard()`, and the
    reason is an invariant rather than an accident — the same one
    `rollup.event_instant_key()` writes down. `_roll_projects()` sets
    `first`/`last` from `event.timestamp` for every project it creates, and
    every Event that reaches it has already been through `_event_date()`,
    which calls the **same** `fromisoformat` and sends the failures to
    `unreadable`. So a project cannot exist with a missing or unparseable
    timestamp.

    Kept anyway, for that function's reason: this feeds a min/max over a
    whole company's evidence, and one bad value taking the view down is
    exactly what the never-raises posture exists to prevent.
    """
    if not iso:  # pragma: no cover - see above
        return None
    try:
        return datetime.fromisoformat(iso).date()
    except (TypeError, ValueError):  # pragma: no cover - see above
        return None


def _coverage(rollup: CompanyRollup) -> Coverage:
    """The evidence range, off the projects the rollup already folded.

    Every Event belongs to exactly one `project_id`, so the earliest and
    latest project timestamps are the earliest and latest Event — no second
    pass over the Events for a number the fold already has.
    """
    days = [
        day
        for project in rollup.projects
        for day in (
            _evidence_day(project.first_seen),
            _evidence_day(project.last_seen),
        )
        if day is not None
    ]
    return Coverage(
        evidence_from=min(days).isoformat() if days else None,
        evidence_to=max(days).isoformat() if days else None,
        unreadable=len(rollup.unreadable),
        duplicates=len(rollup.duplicates),
    )


def _goals_panel() -> DashboardPanel:
    """① COMPANY GOALS — present, empty, and saying why.

    The KPI half of ① is a separate panel (`METRICS`) rather than rows here,
    because the two have opposite provenance: every KPI is counted from
    evidence and carries the files it was counted from, while a Goal is a
    thing somebody decides. Merging them would put derived numbers and an
    empty declaration in one panel and invite reading the first as the
    second — which is the mistake `rollup._roll_metrics` already refuses when
    it declines to attach a target to any of them.
    """
    return DashboardPanel(
        key="COMPANY_GOALS",
        title="전사 목표",
        status=PanelStatus.UNSOURCED,
        source="",
        note=_GOAL_NOTE,
        unsourced_layers=("COMPANY_GOAL", "TEAM_GOAL"),
    )


# `derived_from` rather than `source`, and the rename is not cosmetic:
# `source` is already a column on the DESKTOPS panel where it means a
# `events.SOURCES` value, and `_out()` decides what to redact by column name
# alone. One name meaning "DESKTOP_1" on one panel and a free-text provenance
# sentence on another is exactly the collision that turns an exemption list
# into a leak.
_METRIC_COLUMNS = ("key", "label", "value", "derived_from", "evidence_count")


def _metrics_panel(rollup: CompanyRollup) -> DashboardPanel:
    """① KPI — derived, never declared, and never given a target.

    `rollup.Metric` already carries the sentence saying where each number
    came from and the files it was counted from; this panel changes neither.
    `evidence_count` rather than the refs themselves in `values`, with the
    refs on the row where every other panel puts them, so one shape holds for
    all rows.

    A target would make these KPIs in the usual sense, and a target is a
    Goal — see `_goals_panel()`.
    """
    rows = tuple(
        DashboardRow(
            key=metric.key,
            values={
                "key": metric.key,
                "label": metric.label,
                "value": metric.value,
                "derived_from": metric.source,
                "evidence_count": len(metric.evidence),
            },
            evidence=metric.evidence,
        )
        for metric in rollup.metrics
    )
    return DashboardPanel(
        key="METRICS",
        title="KPI",
        status=PanelStatus.SOURCED,
        columns=_METRIC_COLUMNS,
        rows=rows,
        source="rollup.Metric — 전부 증거에 대한 count이며 각자 세어진 파일을 들고 있다",
        note="target은 없다. target은 Goal이고 Goal은 원천이 없다 (COMPANY_GOALS 참조).",
    )


_TEAM_COLUMNS = (
    "team",
    "display_name",
    "events",
    "projects",
    "blocked_projects",
    "blocked_project_count",
    "last_seen",
    "has_activity",
    "current_sprint",
)


def _teams_panel(rollup: CompanyRollup) -> DashboardPanel:
    """② TEAM DASHBOARD — every role in `events.ROLES`, silent ones included.

    `current_sprint` is present and always `None`. That is deliberate and it
    is not the same as omitting the column: the request asks for it, this
    system has no Sprint, and a consumer that gets the column with a null
    learns that; a consumer that never sees the column learns nothing and may
    invent one. The `SPRINTS` panel carries the explanation.
    """
    rows = tuple(
        DashboardRow(
            key=team.team,
            values={
                "team": team.team,
                "display_name": team.display_name,
                "events": team.event_count,
                "projects": list(team.projects),
                "blocked_projects": list(team.blocked_projects),
                "blocked_project_count": len(team.blocked_projects),
                "last_seen": team.last_seen,
                "has_activity": team.has_activity,
                "current_sprint": None,
            },
            evidence=team.evidence,
        )
        for team in rollup.teams
    )
    return DashboardPanel(
        key="TEAMS",
        title="팀별 진행현황",
        status=PanelStatus.SOURCED,
        columns=_TEAM_COLUMNS,
        rows=rows,
        source="Event의 role별 집계 (docs/02 §8이 Desktop→role을 1:1로 고정한다)",
        note=(
            "`current_sprint`는 항상 null이다 — SPRINTS 패널 참조. 팀의 침묵은 "
            "여기서 세기만 하고 경보하지 않는다: `source`→`role`이 1:1이므로 "
            "조용한 팀은 COMPANY 블록이 이미 보고하는 조용한 Desktop이다."
        ),
    )


_PROJECT_COLUMNS = (
    "project_id",
    "teams",
    "events",
    "status",
    "state",
    "blocker",
    "blocker_team",
    "blocked_since",
    "days_blocked",
    "first_seen",
    "last_seen",
    "days_idle",
    "completed_at",
    "milestones",
    "sprint",
)


# The lifecycle words this module derives, in the order that decides them.
# Deliberately not `events.STATUSES`: two of the four are folded facts that no
# single Event carries (`BLOCKED` survives the Events that do not touch it;
# `COMPLETE` is docs/04 §25's Completed Date), and the project's own
# last-reported `status` sits in the adjacent column for the finer
# distinction. `ACTIVE` therefore covers both `NOT_STARTED` and
# `IN_PROGRESS` — "created and never moved" and "in flight" are different
# facts and `status` is where they are told apart.
PROJECT_STATES: tuple[str, ...] = ("BLOCKED", "COMPLETE", "CANCELLED", "ACTIVE")


def _project_state(project) -> str:
    """The one word for this project's state, in `PROJECT_STATES`' order.

    BLOCKED wins over COMPLETE because a project that was completed and then
    blocked again (an Event sequence `_roll_projects` folds without
    complaint) is a thing a person has to look at, and `is_complete` never
    goes back to False once a `Completed Date` is written.

    CANCELLED is read off the last reported `status` and not off an Event
    type, because docs/04 §26 gives `CANCELLED` **no property of its own** —
    `_type_specific_properties()` returns nothing for it, so there is no
    folded fact to read and `status` is the only place the cancellation
    exists. Without this branch a cancelled project came out `ACTIVE`: the
    screen never showed it (it prints `status` directly) but a projection
    reading `state` would have said a cancelled project was in flight.
    """
    if project.is_blocked:
        return "BLOCKED"
    if project.is_complete:
        return "COMPLETE"
    if project.status == "CANCELLED":
        return "CANCELLED"
    return "ACTIVE"


def _projects_panel(rollup: CompanyRollup, now: datetime) -> DashboardPanel:
    """③ PROJECT — the half of the request that has a source.

    Row order is the one `ops_status.py` prints in, and for the same two
    reasons: blocked first, then longest-quiet first. A dashboard that
    reorders itself between runs is harder to read than one that does not,
    and the projection and the screen ordering the same rows differently
    would be one more thing to reconcile.
    """

    def _order(project):
        idle = project.days_since_last_event(now)
        return (
            0 if project.is_blocked else 1,
            -(idle if idle is not None else 0),
            project.project_id,
        )

    rows = tuple(
        DashboardRow(
            key=project.project_id,
            values={
                "project_id": project.project_id,
                "teams": list(project.teams),
                "events": project.event_count,
                "status": project.status,
                "state": _project_state(project),
                "blocker": project.open_blocker,
                "blocker_team": project.open_blocker_team,
                "blocked_since": project.open_blocker_since,
                "days_blocked": project.days_blocked(now),
                "first_seen": project.first_seen,
                "last_seen": project.last_seen,
                "days_idle": project.days_since_last_event(now),
                "completed_at": project.completed_at,
                "milestones": list(project.milestones),
                "sprint": None,
            },
            evidence=project.evidence,
        )
        for project in sorted(rollup.projects, key=_order)
    )
    return DashboardPanel(
        key="PROJECTS",
        title="Project",
        status=PanelStatus.SOURCED,
        columns=_PROJECT_COLUMNS,
        rows=rows,
        source="Event의 project_id별 fold (docs/04 §20-28을 Event 순서대로 적용)",
        note=(
            "상태는 마지막 Event가 아니라 접어서 구한다 — 월요일 BLOCKED, 수요일 "
            "RESUMED인 Project는 막혀 있지 않다. `sprint`는 항상 null이다."
        ),
    )


def _sprints_panel() -> DashboardPanel:
    """③의 나머지 — Sprint / Task. Present, empty, and saying why."""
    return DashboardPanel(
        key="SPRINTS",
        title="Sprint / Task",
        status=PanelStatus.UNSOURCED,
        source="",
        note=_SPRINT_NOTE,
        unsourced_layers=("SPRINT", "TASK"),
    )


_DESKTOP_COLUMNS = (
    "source",
    "expected_team",
    "display_name",
    "events",
    "projects",
    "last_seen",
    "days_silent",
    "has_activity",
    "role_mismatches",
    "mismatched_event_ids",
)


def _desktops_panel(rollup: CompanyRollup, now: datetime) -> DashboardPanel:
    """④ DESKTOP — every Desktop in docs/02 §8's table, silent ones included.

    Counted by `source`, never by `role`: believing the `role` field is
    precisely how one Desktop's work silently becomes another team's, which
    is what `PairMismatch` exists to make visible.

    What this panel does NOT carry, and why: the Agent's own state
    (`agent_state.json`) lives on the reporting machine and Desktop 4 cannot
    read it, and the Runner's state is the Run Manifest, which reaches Notion
    as the `OPS_RUNS` row. Putting either here would mean inventing a value
    for a machine this one cannot see — the failure mode this whole module is
    arranged against.
    """
    rows = tuple(
        DashboardRow(
            key=desktop.source,
            values={
                "source": desktop.source,
                "expected_team": desktop.expected_team,
                "display_name": desktop.display_name,
                "events": desktop.event_count,
                "projects": list(desktop.projects),
                "last_seen": desktop.last_seen,
                "days_silent": desktop.days_silent(now),
                "has_activity": desktop.has_activity,
                "role_mismatches": len(desktop.mismatched),
                "mismatched_event_ids": [m.event_id for m in desktop.mismatched],
            },
            evidence=desktop.evidence,
        )
        for desktop in rollup.desktops
    )
    return DashboardPanel(
        key="DESKTOPS",
        title="Desktop",
        status=PanelStatus.SOURCED,
        columns=_DESKTOP_COLUMNS,
        rows=rows,
        source="Event의 source별 집계 — 그 Event를 보낸 기계",
        note=(
            "Agent 상태와 Runner 상태는 여기 없다: Agent state는 보고한 기계 위에 "
            "있어 Desktop 4가 읽을 수 없고, Runner state는 Run Manifest이며 "
            "OPS_RUNS 행으로 이미 Notion에 간다."
        ),
    )


_RISK_COLUMNS = (
    "kind",
    "project_id",
    "team",
    "blocker",
    "since",
    "days_open",
    "event_id",
    "source",
    "claimed_role",
    "expected_role",
    # `EVENT_ID_CONFLICT` only: the two filenames, so "which file did the
    # Control Tower believe" is answerable without a second tool.
    "kept",
    "ignored",
)


def _risks_panel(rollup: CompanyRollup, now: datetime) -> DashboardPanel:
    """⑤ RISK / BLOCKER — the two risks this system has a source for.

    `OPEN_BLOCKER` is a person's own words on a `BLOCKED` Event (docs/02
    requires the text), and the pipeline never clears it by itself.
    `ROLE_MISMATCH` is an Event whose `source`/`role` pair contradicts docs/02
    §8 — reported, never rejected, because rejecting it would delete the work
    from Company History (the decision is in BACKLOG).

    The request's other four (실패 / Pending / Stale / Recovery) are all
    operational and all already in the `OPS_RUNS` row: `Failed Steps`,
    `Notion Queued`, `Reused Days`, `Deleted Files`. `days_silent` on the
    DESKTOPS panel is the stale one. None of them is restated here.
    """
    rows: list[DashboardRow] = []
    for risk in rollup.risks:
        rows.append(
            DashboardRow(
                key=f"BLOCKER:{risk.project_id}",
                values={
                    "kind": "OPEN_BLOCKER",
                    "project_id": risk.project_id,
                    "team": risk.team,
                    "blocker": risk.blocker,
                    "since": risk.since,
                    "days_open": risk.days_open(now),
                    "event_id": risk.evidence.event_id,
                    # A blocker is a fact about a project, not about a
                    # machine. The three Desktop columns below belong to the
                    # other kind and are null here rather than absent, so
                    # every row of this panel has the same shape — a
                    # projection builds one table, not two.
                    "source": None,
                    "claimed_role": None,
                    "expected_role": None,
                    # Only `EVENT_ID_CONFLICT` fills these; present-and-null
                    # here so every row of this panel has one shape.
                    "kept": None,
                    "ignored": None,
                },
                evidence=(risk.evidence,),
            )
        )
    for mismatch in rollup.mismatches:
        rows.append(
            DashboardRow(
                key=f"MISMATCH:{mismatch.event_id}",
                values={
                    "kind": "ROLE_MISMATCH",
                    "project_id": None,
                    # The team docs/02 §8 says owns this Desktop — i.e. where
                    # the work actually belongs. `claimed_role` is what the
                    # Event says instead, and keeping both is the whole
                    # point: the row is the disagreement.
                    "team": mismatch.expected_role,
                    "blocker": None,
                    "since": mismatch.evidence.at,
                    "days_open": None,
                    "event_id": mismatch.event_id,
                    "source": mismatch.source,
                    "claimed_role": mismatch.claimed_role,
                    "expected_role": mismatch.expected_role,
                    "kept": None,
                    "ignored": None,
                },
                evidence=(mismatch.evidence,),
            )
        )
    for duplicate in rollup.duplicates:
        # Only the contradicting half. An identical twin is a duplicate the
        # pipeline already handled and the fold already counted once; saying
        # so on a Risk panel would be an alert with no action behind it. Two
        # files claiming one `event_id` with *different* contents is a
        # different sentence: one of them is not the Event it says it is, and
        # which one the Control Tower counted is arbitrary.
        if duplicate.identical:
            continue
        rows.append(
            DashboardRow(
                key=f"CONFLICT:{duplicate.event_id}",
                values={
                    "kind": "EVENT_ID_CONFLICT",
                    "project_id": None,
                    "team": "",
                    "blocker": None,
                    "since": None,
                    "days_open": None,
                    "event_id": duplicate.event_id,
                    "source": None,
                    "claimed_role": None,
                    "expected_role": None,
                    "kept": duplicate.kept,
                    "ignored": duplicate.ignored,
                },
            )
        )
    return DashboardPanel(
        key="RISKS",
        title="Risk / Blocker",
        status=PanelStatus.SOURCED,
        columns=_RISK_COLUMNS,
        rows=tuple(rows),
        source="열린 Blocker(docs/02가 요구하는 사람이 쓴 텍스트) + docs/02 §8을 "
        "어긴 source/role 짝 + 하나의 event_id를 두고 내용이 다른 파일 둘",
        note=(
            "실패 / Pending / Recovery는 여기 없다 — 전부 Run Manifest의 사실이고 "
            "OPS_RUNS 행(`Failed Steps` / `Notion Queued` / `Reused Days` / "
            "`Deleted Files`)이 이미 나른다. stale은 DESKTOPS의 `days_silent`다."
        ),
    )


# Every layer this system has no source for is claimed by exactly one panel
# above. Stated here as data so the test does not have to know which panel
# owns which layer — it asks the model.
def unsourced_layer_coverage(model: DashboardModel) -> dict[str, str]:
    """`layer -> panel key`, for every layer any panel declares unsourced."""
    coverage: dict[str, str] = {}
    for panel in model.panels:
        for layer in panel.unsourced_layers:
            coverage[layer] = panel.key
    return coverage


__all__ = [
    "Coverage",
    "DASHBOARD_SCHEMA_VERSION",
    "EVIDENCE_IN_PAYLOAD",
    "PROJECT_STATES",
    "DashboardModel",
    "DashboardPanel",
    "DashboardRow",
    "PanelStatus",
    "UNSOURCED_LAYERS",
    "build_dashboard",
    "unsourced_layer_coverage",
]
