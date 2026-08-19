"""Company / Team / Project rollup, derived from Execution Evidence.

Why this exists
---------------
Every view this project has is *operational*. `ops_status.py` answers "is the
pipeline healthy", `app/desktop_activity.py` answers "is each Desktop
reporting", `daily/role_summary.py` answers "what did each role do **on one
date**". Nothing answers the questions a Control Tower is for:

    which projects are moving, and which have stopped
    which of them is blocked, on what, and since when
    which team is silent
    what completed this period

Every fact needed for those is already on the Event (`project_id`, `role`,
`event_type`, `status`, `milestone`, `blocker`, `timestamp`), and the
Events are already on disk in `runtime/events/processed/` — which docs/14 §2
classifies as Execution Evidence, the record of what the pipeline actually
accepted. So this module invents no measurement and stores no state; it
groups what is already there.

What it deliberately does NOT invent
------------------------------------
A full Control Tower is usually drawn as

    Company Goal -> Team Goal -> Project -> Sprint -> Task -> result -> KPI

Four of those seven have **no source in this system**, and saying so is more
useful than filling them in:

    Company Goal   no field, no file, no spec section
    Team Goal      no field, no file, no spec section
    Sprint         appears in this repository only as a word in comments
    Task           `BACKLOG.md` is the *development* backlog, not company work

`UNSOURCED_LAYERS` names them, and `NoInventedLayersTests` asserts this
module keeps its hands off them. Where their truth should live is a real
decision and not a small one: docs/14 §1 fixes Notion as
"**View이며 절대 Source가 아니다**", so it cannot be typed into Notion and
called authoritative — it would have to become a Company Repository artifact
or an Event Schema field. BACKLOG carries the question.

The three layers that DO have a source — Team (`role`), Project
(`project_id`), and the execution results themselves — are what this builds,
and KPI/Risk are derived from those rather than declared.

Traceability is the point, not a feature
----------------------------------------
A Control Tower number nobody can trace is a rumour. Every rollup and every
metric here carries `EvidenceRef`s: the `event_id`, the file under
`processed/` it was read from, and its timestamp. "Why is Open Blockers 3?"
is answerable by opening three named files.

No second opinion about state
-----------------------------
Whether an Event opens or clears a blocker, and whether it completes a
project, is already decided by `notion/properties._type_specific_properties()`
— it is docs/04 §20-28's rule, and `ExecutionPlanSync` has been applying it
to the PROJECTS View since long before this module. Restating it here would
be a second rule to keep in step (C28), so this reads that function's own
answer instead: a `Blocker` property with empty `rich_text` is a clear, one
with content is a set, an absent one changes nothing, and a `Completed Date`
means the project completed. If §20-28 changes, this follows.

Never raises
------------
A derivation over evidence a pipeline wrote is read while things are going
wrong, so an unreadable file is reported (`unreadable`) rather than thrown.
Same posture as `history/reconciliation.py`, and for the same reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from events import ROLES, Event
from notion.properties import ROLE_DISPLAY_NAMES, _type_specific_properties

# docs/02 §8's Desktop->role table, imported rather than restated. That
# module's own comment says it "only pairs them the way the spec already
# does", so asking it is asking the spec; a copy here would be the second
# rule to keep in step (C28). The edge `controltower -> reporter` exists for
# this table alone — `reporter` is a writer package, but `profiles.py` is
# pure vocabulary and nothing else in it is used.
from reporter.profiles import PROFILES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "runtime" / "events" / "processed"

# The layers a Control Tower usually has and this system has no source for.
# Named so a view can say so out loud instead of showing an empty panel that
# reads as "nothing is happening".
UNSOURCED_LAYERS: tuple[str, ...] = ("COMPANY_GOAL", "TEAM_GOAL", "SPRINT", "TASK")

# Stable presentation order, the same one `daily/role_summary.ROLE_ORDER`
# uses and for the same reason: a report that reorders itself between runs is
# harder to read than one that does not. Imported rather than restated would
# have meant `controltower -> daily`, an edge the layering table does not
# have and that this module does not otherwise need.
TEAM_ORDER: tuple[str, ...] = ("CTO_BACKEND", "CTO_FRONTEND", "CMO", "COO")

# `source` -> the role that Desktop owns, and the reverse. Derived from
# `PROFILES` so a fifth Desktop appears here the moment docs/02 §8 gains one.
ROLE_FOR_SOURCE: dict[str, str] = {p.source: p.role for p in PROFILES.values()}
SOURCE_FOR_ROLE: dict[str, str] = {p.role: p.source for p in PROFILES.values()}

# Presentation order for Desktops, matching `TEAM_ORDER` through the table
# above so the two lists read down the page in the same order.
DESKTOP_ORDER: tuple[str, ...] = tuple(
    SOURCE_FOR_ROLE[role] for role in TEAM_ORDER if role in SOURCE_FOR_ROLE
)


@dataclass(frozen=True)
class EvidenceRef:
    """One Event, and the file it was read out of."""

    event_id: str
    at: str
    path: str

    def describe(self) -> str:
        return f"{self.event_id} ({self.at}) {self.path}"


@dataclass(frozen=True)
class ProjectRollup:
    project_id: str
    teams: tuple[str, ...] = ()
    event_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    status: str | None = None
    open_blocker: str | None = None
    open_blocker_since: str | None = None
    open_blocker_evidence: EvidenceRef | None = None
    completed_at: str | None = None
    milestones: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return self.open_blocker is not None

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def days_since_last_event(self, now: datetime) -> int | None:
        return _whole_days_between(self.last_seen, now)

    def days_blocked(self, now: datetime) -> int | None:
        return _whole_days_between(self.open_blocker_since, now)


@dataclass(frozen=True)
class TeamRollup:
    team: str
    display_name: str
    projects: tuple[str, ...] = ()
    event_count: int = 0
    blocked_projects: tuple[str, ...] = ()
    last_seen: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def has_activity(self) -> bool:
        """False for a team that produced nothing in the period.

        Present-and-empty rather than absent, exactly as
        `daily/role_summary.py` argues: "CMO 활동 없음" and "CMO를 집계하는
        것을 잊었다" look identical in output that omits the row, and only one
        of them is fine.
        """
        return self.event_count > 0

    # No `days_silent()` here on purpose. How long a team has been quiet is
    # the COMPANY block's line already (`source` -> `role` is 1:1, docs/02
    # §8), and this module deliberately does not alert on team silence — so
    # an accessor for it would be a capability with no caller by design.


@dataclass(frozen=True)
class PairMismatch:
    """An Event claiming a `role` the Desktop it came from does not own.

    docs/02 §8 fixes the Desktop->role table and `reporter/profiles.py`
    applies it when an Event is *created*. `validate_event()` checks the two
    fields **independently** — each must be in its own allowed set — and
    never checks the pair, so an Event that crossed OneDrive, was written by
    hand into `incoming/` (which docs/11 permits), or came back from a
    partial restore can carry `source=DESKTOP_1, role=CMO` and be accepted
    everywhere.

    Measured, one such Event through the real readers:

        validate_event()          no errors
        Control Tower             the work is the CMO team's
        `desktop_activity`        the work is DESKTOP_1's
        Notion PROJECTS row       Owner CMO / Source DESKTOP_1

    — the row carries the contradiction and nothing flagged it. That is the
    "Desktop별 데이터 혼입" this layer exists to make impossible to miss.

    Reported, never rejected: turning it into a validation error would send
    the Event to `rejected/` and out of Company History altogether, and
    choosing "lose the record" over "record it under the wrong owner" is a
    decision (BACKLOG). This names it instead.
    """

    event_id: str
    claimed_role: str
    source: str
    expected_role: str
    evidence: EvidenceRef


@dataclass(frozen=True)
class DesktopRollup:
    """One Desktop's slice — the layer under Team.

    Team and Desktop are the same partition while every Event obeys docs/02
    §8 (`source` -> `role` is 1:1), and that is exactly why both are here:
    when they stop agreeing, only having one of them makes the disagreement
    invisible. `mismatched` is the Events that made them disagree.
    """

    source: str
    expected_team: str
    display_name: str
    projects: tuple[str, ...] = ()
    event_count: int = 0
    last_seen: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    mismatched: tuple[PairMismatch, ...] = ()

    @property
    def has_activity(self) -> bool:
        return self.event_count > 0

    def days_silent(self, now: datetime) -> int | None:
        """Whole days since this Desktop's most recent Event, or None.

        The Control Tower's "stale 여부" for a Desktop. Displayed, never
        alerted on — the COMPANY block owns that alert and `source` is its
        key too, so a second line would be a second opinion about one fact.
        """
        return _whole_days_between(self.last_seen, now)


@dataclass(frozen=True)
class Risk:
    """An open blocker. The only risk this system has a source for.

    docs/02 requires a `BLOCKED` Event to carry a non-null `blocker`, so the
    text is the reporter's own words rather than a category this module
    invented.
    """

    project_id: str
    team: str
    blocker: str
    since: str
    evidence: EvidenceRef

    def days_open(self, now: datetime) -> int | None:
        return _whole_days_between(self.since, now)


@dataclass(frozen=True)
class Metric:
    """One number, with the sentence that says where it came from.

    `source` is not decoration. A Control Tower number nobody can trace is a
    rumour, and `evidence` is the list of files that produced this one.
    """

    key: str
    label: str
    value: int
    source: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class CompanyRollup:
    projects: tuple[ProjectRollup, ...] = ()
    teams: tuple[TeamRollup, ...] = ()
    risks: tuple[Risk, ...] = ()
    metrics: tuple[Metric, ...] = ()
    desktops: tuple[DesktopRollup, ...] = ()
    mismatches: tuple[PairMismatch, ...] = ()
    events_read: int = 0
    unreadable: tuple[tuple[str, str], ...] = ()
    since: date_type | None = None
    until: date_type | None = None

    def project(self, project_id: str) -> ProjectRollup | None:
        for item in self.projects:
            if item.project_id == project_id:
                return item
        return None

    def metric(self, key: str) -> Metric | None:
        for item in self.metrics:
            if item.key == key:
                return item
        return None

    @property
    def silent_teams(self) -> tuple[str, ...]:
        return tuple(team.team for team in self.teams if not team.has_activity)


def _whole_days_between(iso: str | None, now: datetime) -> int | None:
    """Whole days from `iso` to `now`, or None if that cannot be told.

    Naive/aware mixing is answered `None` rather than guessed at. An Event's
    timestamp always carries an offset (docs/02), so this only bites when a
    caller passes a naive `now` — and quietly dropping an offset would move
    the answer by up to a day in whichever direction the guess went, which is
    the mistake `notion/sync._as_comparable_timestamp()` refuses to make.
    """
    if not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if (parsed.tzinfo is None) != (now.tzinfo is None):
        return None
    return max(0, (now.date() - parsed.date()).days)


def _event_date(event: Event) -> date_type | None:
    try:
        return datetime.fromisoformat(event.timestamp).date()
    except (TypeError, ValueError):  # pragma: no cover - validate_event blocks it
        return None


def event_instant_key(event: Event) -> tuple[int, object]:
    """Sort key: by instant when the timestamp parses, by text when it does not.

    Public because two things now need "the order these Events happened in":
    this fold, and `app/runner.py`'s Notion sync, which must apply a batch
    oldest-first or the Late Event guard skips a contemporaneous Event as
    though it were late (C47). One rule, one place.


    Same two-tier shape as `history/result.HistoryCandidate._sort_key()`, and
    for the same reason — one unparseable value must not decide the order of
    everything around it.
    """
    try:
        parsed = datetime.fromisoformat(event.timestamp)
    except (TypeError, ValueError):  # pragma: no cover - see below
        # Unreachable from `build_company_rollup()`, and the reason is worth
        # writing down because it is an invariant rather than an accident:
        # every Event that reaches this sort has already been through
        # `_event_date()`, which calls the *same* `fromisoformat` and sends
        # the failures to `unreadable`. Kept because this function is a sort
        # key — one that raises would take the whole rollup down over a value
        # it was written to tolerate.
        return (1, str(event.timestamp))
    if parsed.tzinfo is None:
        # Reachable: a naive timestamp still has a date, so it is in the
        # period. It cannot be placed on the timeline against offset-aware
        # ones, so it sorts after them by text rather than being guessed at.
        return (1, str(event.timestamp))
    return (0, parsed.timestamp())


def _blocker_change(event: Event) -> tuple[bool, str | None]:
    """`(changes_blocker, new_value)` — read out of docs/04 §20-28's own rule.

    `_type_specific_properties()` is what `ExecutionPlanSync` applies to the
    PROJECTS View, so asking it is asking the rule rather than a copy of it:

        Blocker absent           this Event does not change blocker state
        Blocker rich_text []     cleared
        Blocker rich_text [...]  set to that text
    """
    properties = _type_specific_properties(event)
    blocker = properties.get("Blocker")
    if blocker is None:
        return False, None
    items = blocker.get("rich_text") or []
    if not items:
        return True, None
    text = "".join(item.get("text", {}).get("content", "") for item in items)
    return True, text or None


def _completes(event: Event) -> bool:
    """Whether this Event completes the project — §25's own answer."""
    return "Completed Date" in _type_specific_properties(event)


def read_events(processed_dir: Path) -> tuple[tuple[tuple[Event, str], ...], tuple[tuple[str, str], ...]]:
    """`((event, filename), ...), ((filename, reason), ...)` from `processed/`.

    Staging residue is excluded the way every other reader of a pipeline
    directory excludes it. `processed/` can hold one: a `.tmp-…json` left in
    `incoming/` by a killed writer is complete JSON often enough that the
    Collector accepts it and moves it here under the staging name.
    """
    processed_dir = Path(processed_dir)
    if not processed_dir.is_dir():
        return (), ()
    try:
        entries = sorted(os.scandir(processed_dir), key=lambda e: e.name)
    except OSError as exc:
        return (), ((str(processed_dir), str(exc)),)

    paths: list[tuple[str, str]] = []
    for entry in entries:
        if not entry.name.endswith(".json") or entry.name.startswith(".tmp-"):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        paths.append((entry.name, entry.path))
    if not paths:
        return (), ()

    events: list[tuple[Event, str]] = []
    unreadable: list[tuple[str, str]] = []
    # Read serially, and that is a measurement rather than an oversight.
    #
    # `history/reconciliation.py`, `agent/delivery.py` and
    # `app/desktop_activity.py` all thread their pass over this same
    # directory, with the same stated reason — "the cost is the file OPEN".
    # Copying that here made it **slower**, measured inside `ops_status.py`,
    # warm:
    #
    #        500 Events   27.2 ms serial   ->  42.2 ms threaded
    #      2,000 Events  114.6 ms serial   -> 175.0 ms threaded
    #
    # The difference is what each pass does with the bytes. Those three read
    # a file and ask a cheap question of it; this one runs
    # `Event.from_json()`, which is a JSON parse plus `validate_event()` —
    # real CPU under the GIL, so the pool serialises the work it was supposed
    # to overlap and charges for the hand-off. The pattern is right for the
    # passes that measured it and wrong here; the number is why.
    for name, path in paths:
        try:
            events.append((Event.from_json(Path(path).read_text(encoding="utf-8")), name))
        except Exception as exc:  # noqa: BLE001 — any unreadable shape is reported
            unreadable.append((name, str(exc)))
    return tuple(events), tuple(unreadable)


def build_company_rollup(
    *,
    processed_dir: Path | None = None,
    now: datetime,
    since: date_type | None = None,
    until: date_type | None = None,
    events: Sequence[tuple[Event, str]] | None = None,
) -> CompanyRollup:
    """Roll every accepted Event up to Project, Team and Company.

    `since` / `until` bound the period **by the Event's own date** — the day
    the work happened — not by when the file arrived. That is docs/06 §12's
    rule for which day an Event belongs to, and using arrival instead would
    put a Desktop that was switched off for a week in the wrong period.

    `events` is for callers that already read the directory; when omitted the
    read happens here. Nothing is cached: this is a status derivation, and a
    cached answer to "what is happening now" is the wrong kind of wrong.
    """
    if events is None:
        pairs, unreadable = read_events(
            processed_dir if processed_dir is not None else DEFAULT_PROCESSED_DIR
        )
    else:
        pairs, unreadable = tuple(events), ()

    in_period: list[tuple[Event, str]] = []
    # An Event whose own date cannot be read cannot be placed in a period —
    # but it must not vanish quietly. It joins `unreadable`, which is the
    # field that already means "this one is in the directory and this rollup
    # could not use it", and which the view renders as "아래 숫자는 그만큼
    # 적다". Silently dropping it would make `events_read` a number that
    # disagrees with the directory for a reason nobody could see.
    #
    # `read_events()` cannot produce one — `Event.from_json()` runs
    # `validate_event()`, which requires an ISO-8601 timestamp with an offset
    # — so this is reachable only through the `events=` seam, where a caller
    # hands over objects it built itself.
    undated: list[tuple[str, str]] = []
    for event, name in pairs:
        day = _event_date(event)
        if day is None:
            undated.append((name, f"timestamp is not a date: {event.timestamp!r}"))
            continue
        if since is not None and day < since:
            continue
        if until is not None and day > until:
            continue
        in_period.append((event, name))

    in_period.sort(key=lambda pair: (event_instant_key(pair[0]), pair[0].event_id))

    projects = _roll_projects(in_period)
    teams = _roll_teams(in_period, projects)
    desktops = _roll_desktops(in_period)
    mismatches = tuple(
        mismatch for desktop in desktops for mismatch in desktop.mismatched
    )
    risks = _roll_risks(projects)
    metrics = _roll_metrics(projects, teams, risks, in_period, mismatches)

    return CompanyRollup(
        projects=projects,
        teams=teams,
        desktops=desktops,
        mismatches=mismatches,
        risks=risks,
        metrics=metrics,
        events_read=len(in_period),
        unreadable=tuple(unreadable) + tuple(undated),
        since=since,
        until=until,
    )


def _ref(event: Event, name: str) -> EvidenceRef:
    return EvidenceRef(event_id=event.event_id, at=event.timestamp, path=name)


def _roll_projects(pairs: Sequence[tuple[Event, str]]) -> tuple[ProjectRollup, ...]:
    """One rollup per `project_id`, folding the Events in instant order.

    Blocker and completion are folded rather than taken from the last Event:
    a project BLOCKED on Monday and RESUMED on Wednesday is not blocked, and
    only replaying the sequence says so. That is the same reason
    `ExecutionPlanSync` applies each Event to the PROJECTS row in turn
    instead of writing the newest one's fields.
    """
    order: list[str] = []
    state: dict[str, dict] = {}
    for event, name in pairs:
        key = event.project_id
        if key not in state:
            order.append(key)
            state[key] = {
                "teams": [],
                "count": 0,
                "first": event.timestamp,
                "last": None,
                "status": None,
                "blocker": None,
                "blocker_since": None,
                "blocker_ref": None,
                "completed_at": None,
                "milestones": [],
                "evidence": [],
            }
        bucket = state[key]
        bucket["count"] += 1
        bucket["last"] = event.timestamp
        bucket["status"] = event.status
        if event.role not in bucket["teams"]:
            bucket["teams"].append(event.role)
        bucket["evidence"].append(_ref(event, name))

        changes, value = _blocker_change(event)
        if changes:
            bucket["blocker"] = value
            bucket["blocker_since"] = event.timestamp if value else None
            bucket["blocker_ref"] = _ref(event, name) if value else None
        if _completes(event):
            bucket["completed_at"] = event.timestamp
        if event.event_type == "MILESTONE_COMPLETED" and event.milestone:
            if event.milestone not in bucket["milestones"]:
                bucket["milestones"].append(event.milestone)

    return tuple(
        ProjectRollup(
            project_id=key,
            teams=tuple(state[key]["teams"]),
            event_count=state[key]["count"],
            first_seen=state[key]["first"],
            last_seen=state[key]["last"],
            status=state[key]["status"],
            open_blocker=state[key]["blocker"],
            open_blocker_since=state[key]["blocker_since"],
            open_blocker_evidence=state[key]["blocker_ref"],
            completed_at=state[key]["completed_at"],
            milestones=tuple(state[key]["milestones"]),
            evidence=tuple(state[key]["evidence"]),
        )
        for key in sorted(order)
    )


def _roll_teams(
    pairs: Sequence[tuple[Event, str]], projects: Sequence[ProjectRollup]
) -> tuple[TeamRollup, ...]:
    """One rollup per role in `events.ROLES`, including the silent ones.

    `projects` is passed in rather than recomputed. It was recomputed once —
    folding every Event a second time for an answer the caller already had.

    Worth stating what that was and was not worth, because the first estimate
    of it was wrong. Measured at 6,000 Events:

        reading `processed/`   292.4 ms
        the project fold         6.1 ms
        whole rollup, no read   32.2 ms

    So this removed ~6 ms, not the ~150 ms a "half the work" reading would
    predict: the cost of this module is the file OPEN, exactly as
    `history/reconciliation.py` and `ops_status._daily_dates()` already
    measured for the same directory. The duplication is gone because one
    derivation with two callers is better than two derivations, not because
    it was slow.
    """
    buckets: dict[str, dict] = {
        role: {"projects": [], "count": 0, "last": None, "evidence": []}
        for role in ROLES
    }
    blocked_by_team: dict[str, list[str]] = {role: [] for role in ROLES}
    for event, name in pairs:
        bucket = buckets.setdefault(
            event.role, {"projects": [], "count": 0, "last": None, "evidence": []}
        )
        blocked_by_team.setdefault(event.role, [])
        bucket["count"] += 1
        bucket["last"] = event.timestamp
        if event.project_id not in bucket["projects"]:
            bucket["projects"].append(event.project_id)
        bucket["evidence"].append(_ref(event, name))

    # A team's blocked projects come from the folded project state, not from
    # counting BLOCKED Events — a project that was blocked and resumed is not
    # a blocked project, and the two answers differ exactly when it matters.
    for project in projects:
        if not project.is_blocked:
            continue
        for team in project.teams:
            # No `if not in` guard: `projects` holds one entry per
            # `project_id` and `ProjectRollup.teams` is deduped as it is
            # built, so a pair can only arrive once. The guard was there and
            # branch coverage showed it never took its other side -- a
            # de-duplication of something already unique reads as though
            # duplicates were possible here, which is the wrong thing to
            # tell the next reader of this fold.
            blocked_by_team.setdefault(team, []).append(project.project_id)

    ordered = list(TEAM_ORDER) + sorted(set(buckets) - set(TEAM_ORDER))
    return tuple(
        TeamRollup(
            team=role,
            display_name=ROLE_DISPLAY_NAMES.get(role, role),
            projects=tuple(buckets[role]["projects"]),
            event_count=buckets[role]["count"],
            blocked_projects=tuple(blocked_by_team.get(role, ())),
            last_seen=buckets[role]["last"],
            evidence=tuple(buckets[role]["evidence"]),
        )
        for role in ordered
        if role in buckets
    )


def _roll_desktops(pairs: Sequence[tuple[Event, str]]) -> tuple[DesktopRollup, ...]:
    """One rollup per Desktop in docs/02 §8's table, including silent ones.

    Keyed on `source` — the machine the Event came from — which is the one
    field neither `role` nor `project_id` can be substituted for. An Event
    whose `role` is not the one that Desktop owns is counted **here**, under
    the Desktop that sent it, and recorded as a `PairMismatch`: the alternative
    is to believe the `role` field, which is precisely how one Desktop's work
    silently becomes another team's.

    A `source` outside the table is possible only if `events.SOURCES` grows
    without `PROFILES` following. It is kept rather than dropped, with
    `expected_team` empty, so a Desktop this project has not heard of shows
    up as itself instead of disappearing.
    """
    buckets: dict[str, dict] = {
        source: {"projects": [], "count": 0, "last": None, "evidence": [], "bad": []}
        for source in ROLE_FOR_SOURCE
    }
    for event, name in pairs:
        bucket = buckets.setdefault(
            event.source,
            {"projects": [], "count": 0, "last": None, "evidence": [], "bad": []},
        )
        bucket["count"] += 1
        bucket["last"] = event.timestamp
        if event.project_id not in bucket["projects"]:
            bucket["projects"].append(event.project_id)
        reference = _ref(event, name)
        bucket["evidence"].append(reference)
        expected = ROLE_FOR_SOURCE.get(event.source)
        if expected is not None and event.role != expected:
            bucket["bad"].append(
                PairMismatch(
                    event_id=event.event_id,
                    claimed_role=event.role,
                    source=event.source,
                    expected_role=expected,
                    evidence=reference,
                )
            )

    ordered = list(DESKTOP_ORDER) + sorted(set(buckets) - set(DESKTOP_ORDER))
    return tuple(
        DesktopRollup(
            source=source,
            expected_team=ROLE_FOR_SOURCE.get(source, ""),
            display_name=ROLE_DISPLAY_NAMES.get(
                ROLE_FOR_SOURCE.get(source, ""), source
            ),
            projects=tuple(buckets[source]["projects"]),
            event_count=buckets[source]["count"],
            last_seen=buckets[source]["last"],
            evidence=tuple(buckets[source]["evidence"]),
            mismatched=tuple(buckets[source]["bad"]),
        )
        for source in ordered
        if source in buckets
    )


def _roll_risks(projects: Sequence[ProjectRollup]) -> tuple[Risk, ...]:
    risks: list[Risk] = []
    for project in projects:
        if not project.is_blocked or project.open_blocker_evidence is None:
            continue
        risks.append(
            Risk(
                project_id=project.project_id,
                team=project.teams[-1] if project.teams else "",
                blocker=project.open_blocker or "",
                since=project.open_blocker_since or "",
                evidence=project.open_blocker_evidence,
            )
        )
    return tuple(risks)


def _roll_metrics(
    projects: Sequence[ProjectRollup],
    teams: Sequence[TeamRollup],
    risks: Sequence[Risk],
    pairs: Sequence[tuple[Event, str]],
    mismatches: Sequence[PairMismatch] = (),
) -> tuple[Metric, ...]:
    """The KPI layer, derived rather than declared.

    Every one of these is a count over evidence this function was handed, and
    each carries the files it was counted from. There is deliberately no
    target/threshold on any of them: a target is a Goal, and this system has
    no source for Goals (`UNSOURCED_LAYERS`). Inventing one here would put a
    number on screen that nobody agreed to.
    """
    milestone_refs = tuple(
        _ref(event, name)
        for event, name in pairs
        if event.event_type == "MILESTONE_COMPLETED"
    )
    resolved_refs = tuple(
        _ref(event, name) for event, name in pairs if event.event_type == "ISSUE_RESOLVED"
    )
    decision_refs = tuple(
        _ref(event, name)
        for event, name in pairs
        if event.event_type == "DECISION_APPROVED"
    )
    completed = tuple(p for p in projects if p.is_complete)

    return (
        Metric(
            key="events",
            label="기록된 Event",
            value=len(pairs),
            source="runtime/events/processed/ 안의 수집된 Event 파일",
            evidence=tuple(_ref(event, name) for event, name in pairs),
        ),
        Metric(
            key="projects_active",
            label="움직인 Project",
            value=len(projects),
            source="그 Event들의 서로 다른 project_id",
        ),
        Metric(
            key="projects_completed",
            label="완료된 Project",
            value=len(completed),
            source="docs/04 §25가 Completed Date를 쓰게 하는 Event(COMPLETED)",
            evidence=tuple(
                p.open_blocker_evidence or p.evidence[-1] for p in completed if p.evidence
            ),
        ),
        Metric(
            key="milestones_completed",
            label="완료된 Milestone",
            value=len(milestone_refs),
            source="event_type=MILESTONE_COMPLETED",
            evidence=milestone_refs,
        ),
        Metric(
            key="decisions_approved",
            label="승인된 Decision",
            value=len(decision_refs),
            source="event_type=DECISION_APPROVED",
            evidence=decision_refs,
        ),
        Metric(
            key="issues_resolved",
            label="해결된 Issue",
            value=len(resolved_refs),
            source="event_type=ISSUE_RESOLVED",
            evidence=resolved_refs,
        ),
        Metric(
            key="open_blockers",
            label="열려 있는 Blocker",
            value=len(risks),
            source="docs/04 §20-28을 Event 순서대로 적용한 뒤에도 남아 있는 Blocker",
            evidence=tuple(risk.evidence for risk in risks),
        ),
        Metric(
            key="teams_silent",
            label="조용한 Team",
            value=len([team for team in teams if not team.has_activity]),
            source="events.ROLES 중 이 기간에 Event가 하나도 없는 role",
        ),
        Metric(
            key="desktop_role_mismatches",
            label="Desktop과 role이 어긋난 Event",
            value=len(mismatches),
            source="docs/02 §8의 Desktop→role 표(reporter/profiles.PROFILES)와 "
            "Event의 role이 다른 경우",
            evidence=tuple(mismatch.evidence for mismatch in mismatches),
        ),
    )
