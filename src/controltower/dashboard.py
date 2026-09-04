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
    Notion Sync    `Notion Synced` / `Notion Skipped` / `Notion Retried` /
                   `Notion Unreadable` / `Notion Queued`
    Desktop        `Desktops Reporting` / `Role Mismatches`  (C47)

Every name above is a real `OPS_RUNS` column and
`TheOperationalHalfReallyIsInOpsRunsTests` checks each one against
`notion/dashboard.DASHBOARD_DATABASES`. That gate is not decoration: this
list is the **reason** those facts are absent from the panels below, so a
column renamed out from under it would turn the justification false and the
fact would then reach nobody — the model would not carry it and the row
would not have it. Four of the five Notion Sync names were written in
shorthand (`Skipped`, `Retried`, …) until the gate was added, which is
exactly how far a list nothing reads can drift while still reading fine.

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

from businessdate import business_date
from oplog import bounded, one_line, redact

from delivery import GitActivity
from notion.properties import ROLE_DISPLAY_NAMES

from .cohort import COHORT_UNIT, COHORT_WINDOWS, build_cohort_analysis
from .kpi import ROLES as KPI_ROLES
from .kpi import build_kpi_set
from .rollup import (
    RECENT_LIMIT,
    UNSOURCED_LAYERS,
    CompanyRollup,
    EvidenceRef,
)

# `MAJOR.MINOR`, and both halves now have a rule a test enforces.
#
#     MINOR   the payload gained something. A reader written against an
#             earlier version still finds every key it knew, so it keeps
#             working — but something new exists and nobody was told.
#     MAJOR   a key, panel or column was **removed or renamed**, or changed
#             type. A reader written against the old number breaks.
#
# Until C52 this was a decorative field. It said "1.0" through C49 adding the
# whole `coverage` block, C50 adding `coverage.duplicates`, and C52 adding
# three panels and two columns — three shape changes, none of them announced,
# because nothing compared the number to the shape. That is the drift a
# version string exists to make visible and this one was hiding.
#
# `ThePayloadShapeIsPinnedToItsVersionTests` closes it from here: it records
# the structural fingerprint (top-level keys, coverage keys, row keys,
# evidence keys, panel keys, every panel's columns) beside the version it
# belongs to. Change the shape without changing the number and it fails;
# remove or rename anything without changing MAJOR and it fails; change the
# number without recording the new shape and it fails.
#
# 1.1 rather than 1.0: every change since the field was introduced has been
# additive. Rather than invent the two intermediate numbers nobody published,
# this records one minor bump and starts counting properly.
#
# 1.2 adds `coverage.history_checked`. Additive — a 1.1 reader finds every
# key it knew — so MINOR. The *value* of `coverage.complete` also changes for
# a model nobody enriched (it was `true`, it is now `false`), and that is the
# defect being fixed rather than a contract break: no reader was entitled to
# the old answer, because no reader could tell it apart from a checked one.
#
# 1.3 adds two panels (`ROLE_KPI`, `CODE_CHANGES`) and their columns.
# Additive — a 1.2 reader finds every panel and every column it knew, and
# `project_panels()` skips a panel key it has no projection for — so MINOR.
#
# The panel *order* changes, which is a shape change and is why the number
# moves at all: both new panels sit next to the ones they extend rather than
# at the end (`ROLE_KPI` beside `METRICS`, `CODE_CHANGES` beside it), because
# the order is what a person reads down and appending to the tail would put
# the KPI section below the Event feed.
#
# Same role as `runsummary.SCHEMA_VERSION`: the payload is read by something
# that was written against a version of it.
DASHBOARD_SCHEMA_VERSION = "1.5"

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
        # `events.EVENT_TYPES`, a frozenset of eight words `validate_event()`
        # enforces. Added when the ACTIVITY panel first needed it as a Notion
        # `select`, and found by the gate that says a select column must be
        # on this list — which is what that gate is for. It belongs here on
        # the same footing as `status`: neither can carry a character a
        # person typed.
        "event_type",
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
    # Whether anybody actually asked the Company-History question.
    #
    # `with_history_coverage()`'s docstring claimed calling it with `None`
    # was "still meaningful … the difference between 'asked and there is no
    # gap' and 'never asked'". It was not: both left
    # `history_uncovered_from` at `None`, so the two payloads were
    # byte-identical and no consumer could tell them apart. The distinction
    # the docstring described had nowhere to live.
    #
    # That mattered because of what `complete` did with it. A model nobody
    # enriched answered `complete = True` — "this is the whole picture" —
    # about a question nobody had asked. `ops_status.py` is the only
    # production caller of `with_history_coverage()`, and
    # `notion_projection` is the intended second consumer: wired as it
    # stood, every Notion row would have carried `Coverage Complete = true`
    # on coverage nobody verified.
    #
    # This is the same distinction the panels make between empty and
    # `UNSOURCED`, which this module treats as load-bearing everywhere else.
    # It now has a field.
    history_checked: bool = False

    @property
    def complete(self) -> bool:
        """True when every number in the model covers everything it claims to.

        Three ways to fall short, and a consumer's next question is the same
        for all of them: "is what I am looking at the whole picture?"

            unreadable                a file is there and could not be used
            history_uncovered_from    the work is recorded and its evidence
                                      is gone
            history_checked is False  nobody looked

        The third is not a lesser member. "I did not check" and "I checked
        and it is fine" are different answers, and only one of them justifies
        the word *complete*; conflating them is how a model that never
        consulted Company History came to report full coverage.

        `duplicates` is still deliberately not an input — see its comment: a
        folded duplicate makes the numbers right, not partial.
        """
        return (
            self.unreadable == 0
            and self.history_uncovered_from is None
            and self.history_checked
        )


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
        self, history_uncovered_from: date_type | None, *, checked: bool = True
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

        `checked=False` is the **third** answer, and C68 added it because the
        first two were being made to cover it. The caller derives this fact by
        reading `local_master/daily/`, and that read can fail: a directory it
        cannot list, a file it cannot open. Both used to come back as `None`
        — the same value as "asked, and there is no gap" — so a tree whose
        Company History could not be read reported `complete = True`.

        Measured on one tree, 18-day-old history with work in it and evidence
        starting later:

            files readable      gap 2026-08-01, complete False, screen warns
            files unreadable    gap None,       complete True,  screen silent

        That is the same conversion `history_checked` was introduced to
        remove one level up — "nobody asked" reported as "asked and fine" —
        arriving in the input to the very field that fixed it. Keyword-only
        and defaulted to True so that every caller that genuinely did read
        the directory reads as it did before.
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
                # The half that was missing. Without it, calling this with
                # `None` left the model byte-identical to one nobody called
                # it on, and `complete` said yes to both.
                history_checked=checked,
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
                "history_checked": self.coverage.history_checked,
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
                #
                # **`bounded()` runs LAST, and the order is the point (C127).**
                # Inside `redact()`'s argument it cuts the string *before*
                # anything looks for a secret in it, and every pattern here
                # has a minimum length — `ntn_[A-Za-z0-9]{10,}`. Measured, a
                # 48-character token straddling the 600-character cut:
                #
                #     surviving chars   bounded-first     redact-first
                #      4               `ntn_`            `[RED`
                #      8               `ntn_AAAA`        `[REDACTE`
                #     13               `ntn_AAAAAAAAA`   `[REDACTED]`
                #     20               `[REDACTED]`      `[REDACTED]`
                #
                # Bounding first leaks up to 13 characters of a live
                # credential to a Notion property; bounding last can only
                # truncate the marker, which is cosmetic. Whether a token was
                # redacted must not depend on where it happens to sit
                # relative to character 600.
                {
                    "file": redact(one_line(name)),
                    "reason": bounded(redact(one_line(str(reason)))),
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


_JUDGEMENT_NOTE = (
    "docs/04 §44 '자동화하지 않는 COO 판단'과 docs/04 §68 'V1에서 만들지 않는 것'이 "
    "Critical Path / Launch Readiness / COO Recommendation / Go·No-Go / "
    "CEO Decision Required를 Event만으로 결정하지 않는다고 고정하고, docs/03 §4가 "
    "Collector 쪽에서 같은 것을 한 번 더 적는다. 여기서 계산하면 세 명세를 동시에 "
    "어긴다. 완료 조건은 그 목록에 없는데 이유가 다르다 — 명세가 거절해서가 아니라 "
    "아무 명세도 다루지 않기 때문이다: 완료는 docs/04 §25의 `Completed Date`로 "
    "**보고**될 뿐 **정의**되지 않으며, 무엇을 done으로 볼지 적는 Event 필드도 "
    "Company History 필드도 없다.\n"
    "어느 쪽도 '아직 계산하지 못했다'가 아니라 '사람이 정한다'이다. 그 판단이 "
    "어디에 적혀야 권위를 갖는지는 Goal과 똑같이 열려 있는 질문이고 — docs/14 §1이 "
    "Notion을 View로 고정하므로 Notion에 적어 넣는 것으로는 답이 되지 않는다 — "
    "BACKLOG가 그 결정을 들고 있다."
)


def build_dashboard(
    rollup: CompanyRollup,
    *,
    now: datetime,
    activity: GitActivity | None = None,
) -> DashboardModel:
    """Arrange one `CompanyRollup` into the Control Tower's panels.

    `now` is the instant every age in the model is measured against, and is
    the caller's rather than this function's — see `DashboardModel`.

    `activity` is the git side of the D+1 report (C149). Optional, and its
    absence is *rendered* rather than hidden: `_code_changes_panel()` says
    "nobody asked git", which is a different sentence from "git says nothing
    changed" and from "git could not be read". A caller that does not have
    it loses one panel's rows, not the dashboard.
    """
    return DashboardModel(
        generated_at=now.isoformat(),
        panels=(
            _goals_panel(),
            _metrics_panel(rollup),
            _role_kpi_panel(rollup, now, activity),
            _cohort_panel(rollup, now),
            _code_changes_panel(activity),
            _teams_panel(rollup),
            _projects_panel(rollup, now),
            _sprints_panel(),
            _desktops_panel(rollup, now),
            _risks_panel(rollup, now),
            _activity_panel(rollup),
            _completions_panel(rollup),
            _judgements_panel(),
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
        return business_date(datetime.fromisoformat(iso))
    except (TypeError, ValueError):  # pragma: no cover - see above
        return None


def evidence_window(rollup: CompanyRollup) -> tuple[date_type | None, date_type | None]:
    """`(first day, last day)` the rollup's evidence actually covers.

    Every Event belongs to exactly one `project_id`, so the earliest and
    latest project timestamps are the earliest and latest Event — no second
    pass over the Events for a number the fold already has.

    Public because two things need it, and the second one arrived in C149:
    `_coverage()` puts it on the model, and `dashboard_server` needs it to
    ask git about **the same days the panels cover**. A caller computing its
    own min/max would be a second opinion about which day the evidence
    starts, and the two disagreeing would put the git half of the D+1 report
    on a different day from the Event half — the one defect that report
    cannot survive, because its whole value is that the two are comparable.

    `(None, None)` for a rollup with no projects: there is no window, which
    is a different thing from an empty one.
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
    if not days:
        return None, None
    return min(days), max(days)


def _coverage(rollup: CompanyRollup) -> Coverage:
    """The evidence range and what qualifies it, for the model's header."""
    first, last = evidence_window(rollup)
    return Coverage(
        evidence_from=first.isoformat() if first is not None else None,
        evidence_to=last.isoformat() if last is not None else None,
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


_ROLE_KPI_COLUMNS = (
    "role",
    "key",
    "label",
    "definition",
    "measured",
    # `reading`, not `value`, and the name is load-bearing. `value` is a
    # numeric column across this model — `ThePayloadIsJsonAndDeterministic
    # Tests.test_numbers_stay_numbers` asserts every `value` in every panel
    # is an `int`, because a projection writes it into a Notion `number`
    # property and a stringified count fails at the API rather than in a
    # test. This column is a *rendering* that is deliberately sometimes the
    # words `DATA REQUIRED`, so it must not claim that contract's name. The
    # number itself is already in `METRICS` for every measured KPI, cited to
    # the same files.
    "reading",
    "chain",
    "derived_from",
    "requires",
    "evidence_count",
)


def _role_kpi_panel(
    rollup: CompanyRollup, now: datetime, activity: GitActivity | None
) -> DashboardPanel:
    """① CEO / CTO / COO KPI — measured where possible, refused out loud.

    A second KPI panel beside `METRICS`, and the two are not duplicates.
    `METRICS` is the count layer: nine numbers with no owner, which is the
    right shape for evidence and the wrong shape for a person. This is the
    *role* layer — the standard KPI set each of the three officers would be
    asked about anywhere else — and most of it this system cannot answer.

    Nothing here is recomputed. `kpi.build_kpi_set()` reads `METRICS`' own
    rows by key and carries their evidence, so a number shown twice on this
    dashboard is the same number from the same files (C28). What this panel
    adds is the rows `METRICS` structurally cannot have: the twenty-two KPIs
    with **no source at all**, which have no `Metric` to be a row of, and
    which are the single most useful thing on the page — they say what this
    system does not know.

    `measured` is a boolean column rather than a status word so a reader
    sorting on it gets the two groups; `value` is the rendered string, and
    for an unmeasured KPI it is `DATA REQUIRED` and never `0`.
    `Kpi.rendered()` is the one place that decides that spelling.
    """
    kpis = build_kpi_set(rollup, now=now, activity=activity)
    rows = tuple(
        DashboardRow(
            key=f"{kpi.role}:{kpi.key}",
            values={
                "role": kpi.role,
                "key": kpi.key,
                "label": kpi.label,
                "definition": kpi.definition,
                "measured": kpi.is_measured,
                "reading": kpi.rendered(),
                "chain": kpi.chain,
                "derived_from": kpi.source,
                "requires": kpi.requires,
                "evidence_count": len(kpi.evidence),
            },
            evidence=kpi.evidence,
        )
        # Grouped by role in `ROLES`' order, and inside a role in the order
        # `build_kpi_set()` emits — which is the standard order each set is
        # usually quoted in. Not sorted by value or by measured-ness: a KPI
        # list that reorders itself as the numbers move is one a reader has
        # to re-learn every morning.
        for role in KPI_ROLES
        for kpi in kpis.for_role(role)
    )
    return DashboardPanel(
        key="ROLE_KPI",
        title="CEO / CTO / COO KPI",
        status=PanelStatus.SOURCED,
        columns=_ROLE_KPI_COLUMNS,
        rows=rows,
        source="controltower/kpi.py — 계산된 값은 전부 METRICS의 같은 수이며 "
        "같은 증거 파일을 든다. 새로 세는 것은 없다.",
        # Deliberately carries **no count**. The first draft said "N개 중
        # M개만 계산할 수 있다", and `AuthoredValuesAreRedactedOnTheWayOut
        # Tests` caught it: `title` / `source` / `note` / `columns` are the
        # only payload strings `_out()` never redacts, on the stated grounds
        # that this module wrote them and no Event can reach them. A note
        # whose text moves with the evidence is not a string this module
        # wrote — and the gate proves that claim by building a model over
        # poisoned Events and requiring byte-identical panel metadata.
        #
        # The tally is a *reading* of the rows and belongs to whoever
        # renders them; `measured` is a column, so any renderer can count it.
        note="계산할 수 없는 KPI는 값 대신 DATA REQUIRED를 싣는다. 각 행의 "
        "requires가 무엇이 있어야 답할 수 있는지 적는다 — 추정치를 넣지 않는다.",
    )


#: One column per window, in `COHORT_WINDOWS`' order, and three columns per
#: window rather than one.
#:
#: `dN` is the *rendering* — a percentage or the words `DATA REQUIRED` — and it
#: is deliberately not called `value`, for `_ROLE_KPI_COLUMNS`' stated reason:
#: `value` is a numeric column across this model and a projection writes it into
#: a Notion `number` property.
#:
#: `dN_base` is the denominator and it is the column that makes the panel
#: honest. A retention of 33.3% over three matured members and one over eleven
#: are different claims, and a table that showed only the percentage would let
#: the first be read as the second — which is the same conversion
#: `issues_open`'s label was rewritten to prevent.
#:
#: `dN_settled` is the second half of that honesty and it was added by the
#: audit that found the rate itself wrong: it is the members whose Project
#: **ended** inside the window, which are neither retained nor lost. Without
#: the column, `size - base` would say "still inside the window" about
#: Projects that had in fact finished — the good outcome, reported as a gap.
_COHORT_COLUMNS: tuple[str, ...] = ("cohort", "size") + tuple(
    part
    for days in COHORT_WINDOWS
    for part in (
        f"d{days}",
        f"d{days}_retained",
        f"d{days}_base",
        f"d{days}_settled",
    )
)

_COHORT_NOTE = (
    "Cohort = Project의 **첫 Event**가 속한 달이고, D+N은 **그때까지 아직 "
    "돌아가고 있던** Project가 첫날 **이후** N일 안에 Event를 한 번이라도 더 "
    "남겼는지다. 분모(`dN_base`)는 Cohort 크기가 아니다 — 창이 아직 지나지 않은 "
    "구성원과, 창 안에 완료·취소로 **끝난** 구성원(`dN_settled`)을 뺀 수다. "
    "끝난 것을 빼는 이유는 측정된 것이다: 빼기 전에는 취소된 Project가 '계속 "
    "움직였다'로, 첫날 완료한 Project가 '멈췄다'로 세어졌다. 둘 다 거꾸로다. "
    "아직 지나지 않은 창은 0%가 아니라 DATA REQUIRED이고, 창 안에 전부 끝났으면 "
    "해당 없음이다. 단위는 고객이 아니라 Project다: 이 시스템에 고객이라는 개체가 "
    "없다는 것은 ROLE_KPI의 retention / churn / NRR 행이 이미 DATA REQUIRED로 "
    "말하고 있다.\n"
    "**낮은 D+N을 보면 다음에 볼 곳은 PROJECTS 표다** — 분모에 남은 것은 그 달에 "
    "시작해 아직 끝나지 않은 Project이고, 그중 조용한 것이 이 수를 낮춘 것이다. "
    "그 표는 이미 막힌 것 먼저, 그다음 오래 조용한 순으로 정렬돼 있다. 막혀 있다면 "
    "RISKS 표와 ②의 다음 행동이 이유를 들고 있고, 막히지도 않았는데 조용하다면 "
    "이 시스템에는 그 이유를 아는 원천이 없다 — 사람이 물어봐야 한다."
)


def _cohort_panel(rollup: CompanyRollup, now: datetime) -> DashboardPanel:
    """① Cohort — the question a period total cannot answer.

    Every other number on this dashboard is bounded by `since`/`until` and says
    what happened *in a period*. This one groups Projects by when they first
    appeared and follows each group forward, which is how "이번 달 Event 40건"
    becomes "시작한 일의 절반은 일주일 안에 멈춘다".

    Nothing is recounted: `cohort.build_cohort_analysis()` reads
    `state_projects` — the fold the rollup already made — and their
    `EvidenceRef.at`. There is no second read of `processed/` and no second
    definition of when a Project started (C28).

    SOURCED even with no rows, and the distinction is the usual one: an empty
    table means no Project has appeared yet, which is a true statement about a
    real source. `PanelStatus.UNSOURCED` would say the opposite — that there is
    no such thing as a Project here.
    """
    analysis = build_cohort_analysis(rollup, now=now)
    rows = []
    for cohort in analysis.cohorts:
        values: dict[str, Any] = {"cohort": cohort.key, "size": cohort.size}
        # Driven by the windows the analysis produced, not by `COHORT_WINDOWS`
        # with a fallback for a window that might be missing. There is no such
        # fallback to write: `build_cohort_analysis()` emits one window per
        # entry for every cohort. And if that ever stopped being true, this
        # shape fails loudly at `EveryRowFillsTheColumnsItsPanelDeclaresTests`
        # — the row would be short a column its panel declares — where a
        # defensive `else 0` would instead put a confident zero on screen for a
        # window nobody computed. That is the exact conversion this whole panel
        # exists to refuse, arriving through the guard meant to prevent it.
        for window in cohort.windows:
            values[f"d{window.days}"] = window.rendered()
            values[f"d{window.days}_retained"] = window.retained
            values[f"d{window.days}_base"] = window.base
            values[f"d{window.days}_settled"] = window.settled
        rows.append(
            DashboardRow(key=cohort.key, values=values, evidence=cohort.evidence)
        )
    return DashboardPanel(
        key="COHORT",
        title="Cohort — 시작한 일이 계속 움직이는가",
        status=PanelStatus.SOURCED,
        columns=_COHORT_COLUMNS,
        rows=tuple(rows),
        source=(
            f"controltower/cohort.py — 단위는 {COHORT_UNIT}이고, 각 Project의 "
            "첫 Event(rollup의 first_seen)와 그 Project가 Event를 남긴 날들"
            "(EvidenceRef.at)만으로 계산한다. 새로 세는 Event는 없다."
        ),
        note=_COHORT_NOTE,
    )


_CODE_CHANGE_COLUMNS = ("commit", "at", "author", "subject", "files")


def _code_changes_panel(activity: GitActivity | None) -> DashboardPanel:
    """개발 변경 (Git) — what git says changed, beside what people reported.

    **Not titled "D+1", and running it once is why.** This panel covers
    whatever window the caller asked the panels for, and the unbounded
    default is the whole evidence range — measured on the live tree, the
    title said `D+1 개발 변경` above `2026-08-05 ~ 2026-08-10 · commit 6건`,
    a window twenty-four days wide and twenty-four days old. A reader who
    trusts the title reads six commits as yesterday's work.

    D+1 is a *use* of this panel, not its definition: ask for a one-day
    window and this is the D+1 report (docs/15). The window is always in
    `note`, so the panel says what it actually covers rather than promising
    a period it does not control.

    The half of "어제 무엇이 변경됐는가" that Events cannot answer. Every
    other panel here is built from Execution Events, which exist only when
    somebody chose to report one; a day nobody reported is indistinguishable
    from a day nothing happened, and that is precisely the day an operator
    most needs to tell those apart.

    `PanelStatus.SOURCED` in all three cases, including the two failures,
    and the distinction is carried in `note` rather than in `status`:
    `UNSOURCED` means "this system has no source for this", and git *is* a
    source — it was asked and could not answer, or was never asked. Marking
    a temporary read failure `UNSOURCED` would file it beside Company Goal,
    which is a permanent architectural fact.

    Bounded by `RECENT_LIMIT`, for `_activity_panel()`'s reason: a busy day
    would otherwise put several hundred rows on a page meant to be read in
    ten seconds. `note` always carries the true total, so the bound is
    visible rather than silent.
    """
    if activity is None:
        return DashboardPanel(
            key="CODE_CHANGES",
            title="개발 변경 (Git)",
            status=PanelStatus.SOURCED,
            columns=_CODE_CHANGE_COLUMNS,
            rows=(),
            source="delivery/git_activity.py — 로컬 저장소의 git log",
            note="Git에 물어보지 않았다 — 이 보고를 만든 호출자가 Git 활동을 "
            "전달하지 않았다. '변경 없음'이 아니다.",
        )
    if not activity.available:
        return DashboardPanel(
            key="CODE_CHANGES",
            title="개발 변경 (Git)",
            status=PanelStatus.SOURCED,
            columns=_CODE_CHANGE_COLUMNS,
            rows=(),
            source="delivery/git_activity.py — 로컬 저장소의 git log",
            # `redact` as well as `one_line`: git quotes paths back in its
            # error messages, and a path under this repository can be
            # secret-shaped for exactly the reason `to_payload()` gives about
            # Event filenames. Same order as there — redact last, so a marker
            # cannot be split by a length cut before anything looks for it.
            note=f"Git을 읽지 못했다: {redact(one_line(str(activity.reason)))} — "
            "'변경 없음'이 아니다.",
        )

    shown = activity.commits[:RECENT_LIMIT]
    rows = tuple(
        DashboardRow(
            key=commit.short_sha,
            values={
                "commit": commit.short_sha,
                "at": commit.at,
                "author": commit.author,
                "subject": commit.subject,
                "files": len(commit.files),
            },
        )
        for commit in shown
    )
    window = ""
    if activity.since is not None and activity.until is not None:
        window = f"{activity.since.isoformat()} ~ {activity.until.isoformat()} · "
    note = (
        f"{window}commit {activity.commit_count}건 · "
        f"바뀐 파일 {len(activity.files_changed)}개 · "
        f"작성자 {len(activity.authors)}명"
    )
    if activity.commit_count > len(shown):
        note += f" (아래는 최근 {len(shown)}건만)"
    if not activity.commit_count:
        note += " — 이 기간에 commit이 없었다 (Git은 정상적으로 읽혔다)."
    return DashboardPanel(
        key="CODE_CHANGES",
        title="개발 변경 (Git)",
        status=PanelStatus.SOURCED,
        columns=_CODE_CHANGE_COLUMNS,
        rows=rows,
        source="delivery/git_activity.py — 로컬 저장소의 git log. Event가 아니라 "
        "실제 commit이며, 배포가 아니라 코드 변경이다.",
        note=note,
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
PROJECT_STATES: tuple[str, ...] = (
    "BLOCKED",
    "AT_RISK",
    "COMPLETE",
    "CANCELLED",
    "ACTIVE",
)

#: The states that mean **this project's lifecycle has ended**.
#:
#: One roster, because four surfaces were each deciding it privately by not
#: deciding it at all — they read `days_idle` as "days stalled" without asking
#: whether the project had finished. Measured on one tree:
#:
#:     PROJECTS row order   SHIPPED_LONG_AGO (COMPLETE, 186일)  <- first
#:                          KILLED_LONG_AGO  (CANCELLED, 183일)
#:                          REALLY_STALLED   (ACTIVE, 21일)     <- the actual
#:                          HEALTHY          (ACTIVE, 1일)         problem
#:     Notion `Notes`       "⚠ 186일째 조용함" on a project that shipped
#:     Notion row page      the same sentence again
#:
#: The first is the worst: `ops_status.py` prints only the first
#: `_CONTROL_TOWER_PROJECT_LINES` rows, so enough finished projects push the
#: stalled ones off the terminal entirely — a COO reads a list headed by work
#: that is done and never sees the work that stopped.
#:
#: Not `("COMPLETE", "CANCELLED")` written at each call site: that is how the
#: four disagreements happened. `BLOCKED` and `AT_RISK` are deliberately absent
#: — a blocked project has not ended, it is the one that needs somebody.
SETTLED_STATES: frozenset[str] = frozenset({"COMPLETE", "CANCELLED"})


def is_settled(state: str | None) -> bool:
    """Whether `state` (a `PROJECT_STATES` word) means the project has ended.

    Takes the *word* rather than a `ProjectRollup` so the two surfaces that
    only ever see a payload row — `notion_page.build_project_note()` and the
    row page — can ask the same question as the model-side caller. Asking
    `settled_at` there would mean putting a new column on the panel for a fact
    `state` already carries.
    """
    return state in SETTLED_STATES


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

    **AT_RISK was added in the same change that added the Event type, and
    only after reproducing that exact paragraph a second time.** docs/04
    §28.1 gives `AT_RISK` no property of its own either, so it is read off
    `status` for CANCELLED's reason — and without a branch here it came out
    `ACTIVE`:

        GONE     status=CANCELLED    state=CANCELLED
        VENDOR   status=AT_RISK      state=ACTIVE     <- in flight, it said

    Same consequence, and worse for this state than for CANCELLED: a
    cancelled project needs nobody, and an at-risk one is the single project
    a COO can still save. `dashboard_server._project_states()` counts these
    words for the summary tiles and `CT_PROJECTS.State` is a Notion select
    built from them, so both would have reported zero projects at risk while
    the Risk table listed them.

    It sits directly under BLOCKED and above COMPLETE for the reason BLOCKED
    is above COMPLETE: a project that finished and was then reported at risk
    is a thing a person has to look at, and `is_complete` never goes back to
    False.

    **COMPLETE also requires the last reported `status` to still say so, and
    precedence was hiding the reason (C149).** `is_complete` is "a Completed
    Date was written, ever" — the right meaning for `projects_completed`,
    which counts completions in a period and cites the file that made each
    one. It is the wrong meaning for a *current state* column. Measured, one
    project, two Events:

        COMPLETED(5th) then STARTED(12th, IN_PROGRESS)
            state=COMPLETE   status=IN_PROGRESS

    two columns side by side contradicting each other. The BLOCKED and
    AT_RISK branches above answered the same defect for the two states that
    happen to outrank COMPLETE; a project simply running again outranks
    nothing, so it fell through.

    The clause invents no rule, and it lives on the model as
    `ProjectRollup.completion_stands` — see there for why, and for the two
    other places that were making the same mistake and were fixed with it.
    The completion **count** and the completion **state** stay different
    numbers, which is exactly what they mean.
    """
    if project.is_blocked:
        return "BLOCKED"
    if project.is_at_risk:
        return "AT_RISK"
    if project.completion_stands:
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
        """Blocked, then running (longest quiet first), then ended.

        Three tiers, not two. The second boundary is the one this table was
        getting wrong: "quiet" for a **finished** project is not a fact about
        the company, it is elapsed time since it ended, and sorting on it put
        work that is done above work that has stopped — see `SETTLED_STATES`
        for the measurement.

        `is_blocked` is tested first rather than `state`'s precedence being
        restated, and the two cannot disagree because `_project_state()` puts
        BLOCKED first for its own stated reason. So a project that completed
        and was then blocked stays in tier 0, where a person will see it.

        Inside the settled tier the sign flips: **most recently ended first.**
        What shipped last week is worth a glance; what shipped in March is the
        least interesting row on the page.
        """
        idle = project.days_since_last_event(now) or 0
        if project.is_blocked:
            return (0, 0, project.project_id)
        if is_settled(_project_state(project)):
            return (2, idle, project.project_id)
        return (1, -idle, project.project_id)

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


#: `OpenItem.kind` -> the RISKS row kind a person reads.
#:
#: A table rather than a conditional, because C149 added a third lifecycle
#: (`DECISION_EXECUTION`) to the two the conditional was written for, and the
#: `else` branch would have filed it as an open Issue — silently, with the
#: right count and the wrong word. That is the same shape of defect as the
#: `else` in `ops_status.py`'s RISKS dispatch, found in the same change.
#: A `KeyError` here is loud and immediate; a wrong label is neither.
def _open_item_detail(item) -> str:
    """One line: what it says, how many are folded behind it, who owns it.

    Ownership is here rather than in the `team` column on purpose. `team` is
    who **raised** it and has meant that since the column existed; making it
    sometimes mean "owner" would give one column two meanings and no way for
    a reader to tell which one they are looking at. This line can say both.

    `미배정` is stated rather than left blank, because a blank owner and an
    owner nobody rendered look identical — the distinction this module makes
    everywhere else between "asked and there is none" and "never asked".
    """
    parts = [item.summary]
    if item.occurrences > 1:
        parts.append(f"(외 {item.occurrences - 1}건 더 열려 있다)")
    if item.is_assigned:
        parts.append(f"· 담당 {ROLE_DISPLAY_NAMES.get(item.assigned_team, item.assigned_team)}")
    else:
        parts.append("· 미배정 — 아무도 맡지 않았다")
    return " ".join(parts)


_OPEN_ITEM_RISK_KIND: dict[str, str] = {
    "ISSUE": "OPEN_ISSUE",
    "DECISION": "PENDING_DECISION",
    "DECISION_EXECUTION": "UNEXECUTED_DECISION",
}


_RISK_COLUMNS = (
    "kind",
    "project_id",
    "team",
    "blocker",
    # The words a person wrote about why this row exists, for the three kinds
    # C149 added. A separate column from `blocker` rather than reusing it,
    # and the reason is the header: `columns.LABELS` renders `blocker` as
    # "Blocker", so a pending Decision's summary shown there would be
    # labelled as the thing stopping the project — which is a different and
    # false claim. `blocker` stays exactly what docs/02 §23 says it is.
    "detail",
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
                    # The blocker text *is* this row's detail, and it is
                    # already in its own column. Null rather than a copy:
                    # one fact, one place.
                    "detail": None,
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
    # The three kinds C149 made expressible. Every one of them was a real
    # company state this panel could not show, and the reason was never the
    # panel: the Event vocabulary had no way to say them.
    #
    #     AT_RISK           a project reported as likely to stop. Before, a
    #                       project was either fine or already BLOCKED, and
    #                       "fine" is where a COO can still do something.
    #     PENDING_DECISION  somebody is waiting on a decision. docs/02 §19
    #                       explicitly refused to record this as
    #                       DECISION_APPROVED and gave it nowhere else to go.
    #     OPEN_ISSUE        an Issue was raised and nothing has closed it.
    #     UNEXECUTED_DECISION  a decision was approved and nobody has done
    #                       it. Approval used to close the lifecycle, so
    #                       "decided and not done" left every list at the
    #                       moment it started being a problem.
    #
    # Ordered after the blockers and before the machine-level rows, because
    # that is decreasing urgency to the person reading: stopped, about to
    # stop, waiting on a person, unresolved. `_roll_open_items()` already
    # sorted the last two oldest-first.
    # `state_projects`, not `projects`: a risk is a state, and `projects` is
    # the activity fold (C152). On a windowed view the two differ exactly
    # when it matters — a project that became at risk before the window is
    # still at risk inside it.
    for project in rollup.state_projects or rollup.projects:
        if not project.is_at_risk or project.is_blocked or project.completion_stands:
            continue
        rows.append(
            DashboardRow(
                key=f"AT_RISK:{project.project_id}",
                values={
                    "kind": "AT_RISK",
                    "project_id": project.project_id,
                    "team": project.at_risk_team or "",
                    "blocker": None,
                    # docs/04 §28.1 gives AT_RISK no property of its own, so
                    # the risk is described in the Event's `summary` and the
                    # fold carries it. `detail` rather than `blocker`,
                    # because the project is **not** blocked and the column
                    # headed "Blocker" would say it is.
                    "detail": project.at_risk_summary,
                    "since": project.at_risk_since,
                    "days_open": project.days_at_risk(now),
                    "event_id": (
                        project.at_risk_evidence.event_id
                        if project.at_risk_evidence
                        else ""
                    ),
                    "source": None,
                    "claimed_role": None,
                    "expected_role": None,
                    "kept": None,
                    "ignored": None,
                },
                evidence=(
                    (project.at_risk_evidence,) if project.at_risk_evidence else ()
                ),
            )
        )
    for item in rollup.open_items:
        rows.append(
            DashboardRow(
                key=f"{item.kind}:{item.project_id}",
                values={
                    "kind": _OPEN_ITEM_RISK_KIND[item.kind],
                    "project_id": item.project_id,
                    "team": item.team,
                    "blocker": None,
                    # The newest opening's words, plus how many older ones
                    # are folded behind it. `OpenItem.occurrences` explains
                    # why the fold cannot keep them apart; saying "외 N건"
                    # is the difference between a fold and a loss.
                    "detail": _open_item_detail(item),
                    "since": item.since,
                    "days_open": item.age_days(now),
                    "event_id": item.evidence.event_id,
                    "source": None,
                    "claimed_role": None,
                    "expected_role": None,
                    "kept": None,
                    "ignored": None,
                },
                evidence=(item.evidence,),
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
                    "detail": None,
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
                    "detail": None,
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
        source="열린 Blocker(docs/02가 요구하는 사람이 쓴 텍스트) + 위험하다고 "
        "보고된 Project + 닫히지 않은 Decision/Issue + docs/02 §8을 어긴 "
        "source/role 짝 + 하나의 event_id를 두고 내용이 다른 파일 둘",
        note=(
            "실패 / Pending / Recovery는 여기 없다 — 전부 Run Manifest의 사실이고 "
            "OPS_RUNS 행(`Failed Steps` / `Notion Queued` / `Reused Days` / "
            "`Deleted Files`)이 이미 나른다. stale은 DESKTOPS의 `days_silent`다. "
            "이 표는 **기간이 아니라 시점**이다 — 창의 끝(`until`)까지 열려 있고 "
            "닫히지 않은 것을 전부 보여준다. 창 이전에 막히거나 제기된 것도 "
            "여전히 열려 있으면 여기 있다(C152)."
        ),
    )


_ACTIVITY_COLUMNS = (
    "event_id",
    "at",
    "source",
    "team",
    "project_id",
    "event_type",
    "status",
    "summary",
    "milestone",
    # The panel's own bound, repeated on every row.
    #
    # Panel-level facts have nowhere else to go — a Notion database is rows —
    # and this is the same choice `Coverage Complete` makes for the same
    # stated reason: it is per-row that the question gets asked. Twenty rows
    # with no "of 340" beside them is precisely the false reading
    # `evidence_truncated` exists to prevent one level down.
    "of_total",
    "truncated",
)


def _activity_row(entry, *, total: int) -> DashboardRow:
    """One `ActivityEntry` as a row. Shared by both activity panels.

    `team` rather than `role` as the column name, because that is what the
    TEAMS panel calls the same value and `_out()` decides redaction by column
    name alone — two names for one thing is how an exemption list becomes a
    leak, and one name for two things is the collision `derived_from` was
    renamed to avoid.
    """
    return DashboardRow(
        key=entry.event_id,
        values={
            "event_id": entry.event_id,
            "at": entry.at,
            "source": entry.source,
            "team": entry.role,
            "project_id": entry.project_id,
            "event_type": entry.event_type,
            "status": entry.status,
            "summary": entry.summary,
            # Present-and-null on the rows that have no milestone, so every
            # row of the panel has one shape.
            "milestone": entry.milestone,
            "of_total": total,
            "truncated": total > RECENT_LIMIT,
        },
        evidence=(entry.evidence,) if entry.evidence is not None else (),
    )


# The bound, stated once, in prose that does not move.
#
# The first draft interpolated the counts into this string and
# `AuthoredValuesAreRedactedOnTheWayOutTests` refused it — correctly. That
# sweep builds one model over clean Events and one over poisoned Events and
# demands the panel **metadata** (`title` / `source` / `note` / `columns`)
# come out byte-identical, which is what makes "this module wrote these
# strings, so `_out()` need not redact them" a checkable claim rather than an
# assertion. A note carrying a count is not a leak, but it destroys the check
# that would catch one: the comparison can no longer tell "a number moved"
# from "a secret got in".
#
# So the numbers live where numbers live — on the rows, as `of_total` and
# `truncated`, exactly as `evidence_count` / `evidence_truncated` already do
# one level down.
_RECENT_NOTE = (
    f"최근 {RECENT_LIMIT}건까지만 싣는다 (`rollup.RECENT_LIMIT`) — 작업량에 비례해 "
    "커지는 것은 로그이며 그러면 Dashboard일 수 없다(docs/14 §3). 각 행의 "
    "`of_total` / `truncated`가 실제 총계를 말한다. 잘려 나간 것은 잃은 것이 "
    "아니다: 원본은 runtime/events/processed/ 에 있고 history_candidate라면 "
    "Company History에도 있다."
)


def _activity_panel(rollup: CompanyRollup) -> DashboardPanel:
    """최근 활동 — the newest Events, in the order they happened.

    The first panel here that is a **list of Events** rather than a fold over
    them, and the request asks for it in all three of its dashboards. Every
    other panel answers "what is the state of X"; a fold cannot answer "what
    happened lately", because the answer is the individual Events.

    Newest first, which is the opposite of `rollup`'s internal order and the
    right one for a reader: the row a person wants is at the top.

    Bounded at `RECENT_LIMIT` and saying so — see the constant for why, and
    `_truncation_note()` for why the sentence is unconditional.
    """
    rows = tuple(
        _activity_row(entry, total=rollup.events_read) for entry in rollup.recent
    )
    return DashboardPanel(
        key="ACTIVITY",
        title="최근 활동",
        status=PanelStatus.SOURCED,
        columns=_ACTIVITY_COLUMNS,
        rows=rows,
        source="수집된 Event를 시각 역순으로 — 접지 않고 그대로",
        note=_RECENT_NOTE,
    )


def _completions_panel(rollup: CompanyRollup) -> DashboardPanel:
    """최근 완료 — the newest Events that finished something.

    Not a filter a consumer could apply to ACTIVITY itself, and that is the
    point: ACTIVITY is bounded, so on a busy week every completion falls off
    the end of it and "최근 완료" would report nothing on exactly the weeks
    it matters. `rollup.completions` is cut from the whole period.

    `COMPLETED` and `MILESTONE_COMPLETED` both, per
    `rollup.COMPLETION_EVENT_TYPES` — the second is 14 of the 16 Events on
    this repository's own evidence, and a panel that showed only finished
    *projects* would have been empty while the company completed fourteen
    things. `CANCELLED` is not a completion; `PROJECT_STATES` keeps the same
    distinction.
    """
    rows = tuple(
        _activity_row(entry, total=rollup.completions_total)
        for entry in rollup.completions
    )
    return DashboardPanel(
        key="COMPLETIONS",
        title="최근 완료",
        status=PanelStatus.SOURCED,
        columns=_ACTIVITY_COLUMNS,
        rows=rows,
        source="event_type이 COMPLETED / MILESTONE_COMPLETED인 Event, 시각 역순",
        note=_RECENT_NOTE,
    )


def _judgements_panel() -> DashboardPanel:
    """COO 판단 — present, empty, and naming three specs.

    The request's Company Dashboard asks for Critical Path and its Project
    Dashboard asks for 완료 조건. Neither has a source, and until now neither
    was **declared** to have none — which this module treats as the worst of
    the three states: a consumer cannot tell "nobody decided" from "the model
    forgot".

    Separate from `_goals_panel()` because the two are unsourced for
    different reasons. A Goal has no source *yet* and BACKLOG carries the
    open question of where to put one. These are refused on purpose:
    docs/04 §44, docs/04 §68 and docs/03 §4 each say they are not derived
    from Events. Merging them would put "undecided" and "decided not to" in
    one panel, and the second is not waiting for anything.
    """
    return DashboardPanel(
        key="JUDGEMENTS",
        title="COO 판단 (자동화하지 않음)",
        status=PanelStatus.UNSOURCED,
        source="",
        note=_JUDGEMENT_NOTE,
        unsourced_layers=("CRITICAL_PATH", "COMPLETION_CRITERIA"),
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
    "evidence_window",
    "unsourced_layer_coverage",
]
