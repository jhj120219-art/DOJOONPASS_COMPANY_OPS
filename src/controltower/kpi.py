"""CEO / CTO / COO KPI, and an honest account of which ones this system
cannot compute.

Why this exists
---------------
`rollup._roll_metrics()` already derives numbers from Execution Evidence,
and it is deliberately unlabelled: nine counts with no owner and no target,
because a target is a Goal and this system has no source for Goals.

That is correct and it is not enough for a person. "열려 있는 Blocker 3"
does not tell a CEO whether the company is healthy, and it does not tell a
CTO whether delivery is getting worse. What a role needs is a named,
standard KPI set — the same one they would be asked about anywhere else —
answered where it can be answered and refused, out loud, where it cannot.

So this module adds exactly one thing to the rollup: **framing**. Every
measured KPI here is a number `rollup` already computed, cited to the same
evidence. Nothing is re-derived from the Event files (C28), and nothing is
estimated.

DATA REQUIRED is the point, not a gap
-------------------------------------
Most of a CEO's standard KPI set — Revenue, MRR, ARR, Customers,
Retention, Churn, NRR, Conversion, Cash, Runway — has **no source in this
system at all**. Not "not implemented": there is no field, no file, and no
spec section anywhere in this repository from which any of them could be
computed, and `rollup.UNSOURCED_LAYERS` already says the same thing one
layer down about Company Goal and Team Goal.

The tempting move is to show a plausible number anyway. That is the one
thing this module must never do, and the reason is `Metric`'s own: a number
nobody can trace is a rumour, and a *fabricated* number that looks traceable
is a rumour a CEO will act on. So an unsourceable KPI carries
`status=DATA_REQUIRED`, no value at all, and `requires` — the sentence
naming what would have to exist for it to be answerable.

The result is a finding rather than an empty screen: **this system measures
execution, and does not measure the business.** Twelve of the CEO's KPIs are
DATA REQUIRED and every one of the COO's is answerable, which is an accurate
description of what has been built.

What C149 changed
-----------------
Four of the COO's KPIs — Issue Aging, Decision Aging, Open Issues, Pending
Decisions — were not merely unimplemented before C149. `EVENT_TYPES` had
`ISSUE_RESOLVED` with no `ISSUE_RAISED` and `DECISION_APPROVED` with no
`DECISION_REQUIRED`, so an Issue had no recordable start and its age was
uncomputable *in principle*. Adding the opening halves of those lifecycles
is what moved these four out of DATA REQUIRED, and it is why the fix was to
the Event vocabulary rather than to this file.

The chain, where there is one
-----------------------------
The request asks whether each KPI connects
`Metric -> Goal -> Initiative -> Project -> Issue -> Action`. Answered per
KPI in `chain`, and answered honestly: no KPI here reaches `Goal`, because
Goal has no source (`UNSOURCED_LAYERS`). What the measured ones do reach is
`Metric -> Project -> Issue`, via `EvidenceRef` — every number opens into
the named Event files behind it, and each of those names a `project_id`.
Saying "connected to Goal" of any of them would be the invention this
module refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from delivery import GitActivity

from .rollup import CompanyRollup, EvidenceRef, OpenItem

#: The three roles this framework is written for. Deliberately not
#: `events.ROLES`: those are the four *reporting* teams a Desktop maps to
#: (docs/02 §8), and these are the three people who read the dashboard. A
#: CMO KPI set would be a fourth section and it is not written, because
#: nothing in this system sources a marketing number either — saying so
#: here is more useful than an empty CMO panel.
ROLES: tuple[str, ...] = ("CEO", "CTO", "COO")

MEASURED = "MEASURED"
DATA_REQUIRED = "DATA_REQUIRED"


@dataclass(frozen=True)
class Kpi:
    """One KPI, measured or refused.

    Exactly one of `value` / `requires` is meaningful, and `status` says
    which. A caller must not fall back to rendering `value` when it is None
    — that is how "0" ends up on screen for a KPI nobody can compute, which
    is worse than the blank it replaces because zero is a claim.
    """

    key: str
    role: str
    label: str
    definition: str
    status: str
    value: float | int | None = None
    unit: str = ""
    #: Where a MEASURED value came from, in the same voice as `Metric.source`.
    source: str = ""
    #: What would have to exist for a DATA_REQUIRED one to be answerable.
    #: Not "TODO" — the name of the missing source.
    requires: str = ""
    #: How far up `Metric -> Goal -> Initiative -> Project -> Issue -> Action`
    #: this KPI actually reaches. "Metric" alone for one with no evidence.
    chain: str = "Metric"
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def is_measured(self) -> bool:
        return self.status == MEASURED

    def rendered(self) -> str:
        """The value as a person should see it, or `DATA REQUIRED`.

        One place, so that a screen, a Notion page and a Markdown report
        cannot disagree about how a refusal is spelled — and so that none of
        them can accidentally spell it `0`.
        """
        if not self.is_measured or self.value is None:
            return "DATA REQUIRED"
        if isinstance(self.value, float):
            return f"{self.value:.1f}{self.unit}"
        return f"{self.value}{self.unit}"


@dataclass(frozen=True)
class KpiSet:
    kpis: tuple[Kpi, ...] = ()
    #: The window the measured ones were computed over, carried so a reader
    #: can tell "no open Issues" from "no open Issues *in the last 7 days*".
    #: `_roll_open_items()`'s docstring explains why that bound is real.
    since: object | None = None
    until: object | None = None

    def for_role(self, role: str) -> tuple[Kpi, ...]:
        return tuple(kpi for kpi in self.kpis if kpi.role == role)

    def get(self, key: str) -> Kpi | None:
        for kpi in self.kpis:
            if kpi.key == key:
                return kpi
        return None

    @property
    def measured(self) -> tuple[Kpi, ...]:
        return tuple(kpi for kpi in self.kpis if kpi.is_measured)

    @property
    def data_required(self) -> tuple[Kpi, ...]:
        return tuple(kpi for kpi in self.kpis if not kpi.is_measured)


def _required(
    key: str, role: str, label: str, definition: str, requires: str
) -> Kpi:
    return Kpi(
        key=key,
        role=role,
        label=label,
        definition=definition,
        status=DATA_REQUIRED,
        requires=requires,
        chain="Metric",
    )


#: Every CEO KPI in the standard set, and the source each one would need.
#:
#: All twelve are DATA REQUIRED, and that is not an oversight to be worked
#: around — it is the finding. This system reads Execution Events and git
#: commits. Neither carries money, a customer, or a contract, so no
#: arrangement of what is on disk produces any of these. `requires` names
#: what would have to arrive first, so the list reads as a decision waiting
#: to be made rather than as twelve failures.
_CEO_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "north_star",
        "North Star Metric",
        "회사가 하나만 본다면 보는 수",
        "North Star가 무엇인지 선언된 곳이 없다. Company Goal은 "
        "rollup.UNSOURCED_LAYERS가 이미 '필드도 파일도 명세 절도 없다'고 "
        "적어 둔 층이고, 이것은 그 층의 첫 줄이다.",
    ),
    (
        "revenue",
        "Revenue",
        "기간 매출",
        "매출 데이터 원본(회계/결제 시스템)이 이 저장소에 없다. Event Schema "
        "13개 필드 중 금액을 담는 필드가 없다.",
    ),
    (
        "mrr",
        "MRR",
        "월 반복 매출",
        "구독/계약 원본이 없다. Revenue와 같은 원본이 필요하다.",
    ),
    (
        "arr",
        "ARR",
        "연 반복 매출",
        "MRR이 있어야 파생된다. 같은 원본이 필요하다.",
    ),
    (
        "customers_active",
        "Active Customers",
        "기간 내 활성 고객 수",
        "고객이라는 개체가 이 시스템에 없다. project_id는 내부 프로젝트 "
        "식별자이고 고객 식별자가 아니다.",
    ),
    (
        "retention",
        "Retention",
        "유지율",
        "고객 개체와 기간별 활성 여부가 모두 필요하다. 둘 다 없다.",
    ),
    (
        "churn",
        "Churn",
        "이탈률",
        "Retention과 같은 원본이 필요하다.",
    ),
    (
        "nrr",
        "NRR",
        "순 매출 유지율",
        "고객별 매출 시계열이 필요하다. 고객도 매출도 없다.",
    ),
    (
        "conversion",
        "Conversion",
        "전환율",
        "유입과 전환을 세는 원본(제품 분석/CRM)이 없다.",
    ),
    (
        "cash",
        "Cash",
        "현재 현금",
        "재무 원본(은행 계좌 / 회계 시스템)이 이 저장소에 없다. Event도 "
        "Commit도 잔액을 담지 않는다.",
    ),
    (
        "runway",
        "Runway",
        "남은 개월 수",
        "Cash와 월 소진액이 있어야 파생된다. 둘 다 없다.",
    ),
    (
        "strategic_goal_progress",
        "Strategic Goal Progress",
        "전사 목표 달성률",
        "Goal 자체에 원본이 없다(rollup.UNSOURCED_LAYERS). 진행률은 "
        "목표가 선언된 뒤에야 계산할 수 있고, docs/14 §1이 Notion을 "
        "'View이며 절대 Source가 아니다'로 고정하므로 Notion에 입력하는 "
        "것으로는 원본이 되지 않는다.",
    ),
)


#: The CTO's DORA-derived delivery KPIs, and why git alone does not answer
#: them. This is the distinction the whole set turns on: git knows what
#: **changed**, and DORA measures what was **deployed**. A commit is not a
#: deployment, and treating one as the other would make Deployment Frequency
#: a synonym for "how often somebody typed `git commit`" — a number that
#: looks like DORA, moves like nothing in production, and would be acted on.
_CTO_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "deployment_frequency",
        "Deployment Frequency",
        "배포 빈도 (DORA)",
        "배포 사건을 기록하는 원본이 없다. Commit은 배포가 아니다 — "
        "Commit 수로 대체하면 DORA처럼 보이지만 production과 아무 관계가 "
        "없는 수가 된다. 배포 파이프라인 로그 또는 배포 전용 Event Type이 "
        "필요하다.",
    ),
    (
        "change_lead_time",
        "Change Lead Time",
        "Commit에서 production까지 걸린 시간 (DORA)",
        "Commit 시각은 git이 안다(delivery/git_activity.py). 배포 시각을 "
        "아는 원본이 없어 차이를 계산할 수 없다. 절반만 있는 값이다.",
    ),
    (
        "change_failure_rate",
        "Change Failure Rate",
        "배포 중 장애로 이어진 비율 (DORA)",
        "배포 원본과 장애 원본이 모두 필요하다. 둘 다 없다.",
    ),
    (
        "failed_deployment_recovery_time",
        "Failed Deployment Recovery Time",
        "장애 배포 복구까지 걸린 시간 (DORA)",
        "배포 장애의 시작과 끝을 기록하는 원본이 없다.",
    ),
    (
        "deployment_rework_rate",
        "Deployment Rework Rate",
        "롤백/핫픽스 비율",
        "배포 원본이 없다. 되돌린 Commit은 git에서 보이지만 그것은 "
        "배포 rework가 아니라 코드 rework다.",
    ),
    (
        "reliability_slo",
        "Reliability / SLO",
        "서비스 가용성",
        "운영 중인 서비스의 가용성을 재는 원본(모니터링)이 없다. "
        "ops_status.py는 이 파이프라인의 건강 상태를 재고 회사 서비스의 "
        "가용성을 재지 않는다.",
    ),
    (
        "critical_technical_debt",
        "Critical Technical Debt",
        "즉시 갚아야 할 기술 부채",
        "BACKLOG.md는 이 시스템의 개발 backlog이고 회사 기술 부채 대장이 "
        "아니며, 항목에 severity가 붙어 있지 않다. '치명적'을 세려면 "
        "분류된 원본이 필요하다.",
    ),
)


def _oldest_age(items: tuple[OpenItem, ...], now: datetime) -> int | None:
    """The largest age among `items`, or None when none can be aged.

    `max` over the ages rather than the age of `items[0]`: the fold sorts
    oldest-first, but an item whose timestamp cannot be parsed sorts last
    while having no age at all, and reading position 0 would silently make
    the answer depend on the sort rather than on the ages.
    """
    ages = [age for age in (item.age_days(now) for item in items) if age is not None]
    return max(ages) if ages else None


def _git_kpis(activity: GitActivity | None) -> list[Kpi]:
    """The CTO KPIs git can actually answer — and none of them is DORA.

    Every DORA metric in `_CTO_DEFINITIONS` stays DATA REQUIRED, because git
    knows what changed and DORA measures what was deployed. These three are
    a different claim: **code change volume**, labelled as such, so that the
    CTO panel is not seven refusals with nothing beside them.

    Naming matters more here than usual. "Deployment Frequency: 9" built
    from nine commits would be read as a DORA number by anyone who has seen
    DORA, and acted on. "Commits: 9" cannot be misread.

    A `GitActivity` that could not be read produces DATA REQUIRED carrying
    git's own reason, not zero — `GitActivity.available` exists precisely so
    "nothing was committed" and "git could not be read" stay different
    answers.
    """
    specs = (
        ("code_commits", "Commits", "이 기간에 기록된 commit 수"),
        ("code_files_changed", "Files Changed", "이 기간에 바뀐 서로 다른 파일 수"),
        ("code_contributors", "Contributors", "이 기간에 commit한 사람 수"),
    )
    if activity is None:
        return [
            _required(
                key,
                "CTO",
                label,
                definition,
                "Git 활동이 이 보고에 전달되지 않았다 "
                "(delivery.read_git_activity()를 호출하지 않은 호출자).",
            )
            for key, label, definition in specs
        ]
    if not activity.available:
        return [
            _required(
                key,
                "CTO",
                label,
                definition,
                f"Git을 읽을 수 없다: {activity.reason}",
            )
            for key, label, definition in specs
        ]

    window = ""
    if activity.since is not None and activity.until is not None:
        window = f" ({activity.since.isoformat()} ~ {activity.until.isoformat()})"
    values = (
        activity.commit_count,
        len(activity.files_changed),
        len(activity.authors),
    )
    return [
        Kpi(
            key=key,
            role="CTO",
            label=label,
            definition=definition,
            status=MEASURED,
            value=value,
            unit="",
            source=f"git log{window} — 배포가 아니라 코드 변경량이다",
            # Stops at Metric on purpose. A commit names files, not a
            # `project_id`, and mapping paths to Projects would be a
            # second, invented opinion about which work a change belongs
            # to — the Events already carry that answer.
            chain="Metric",
        )
        for (key, label, definition), value in zip(specs, values)
    ]


def build_kpi_set(
    rollup: CompanyRollup,
    *,
    now: datetime,
    activity: GitActivity | None = None,
) -> KpiSet:
    """The CEO / CTO / COO KPI set for `rollup`'s window.

    Every measured KPI reads a `Metric` the rollup already produced, by key,
    and carries that Metric's own evidence. Nothing here re-counts Events:
    two counts of the same thing is the C28 defect, and it is worse for a
    KPI than anywhere else, because the two would be shown side by side.

    `activity` is optional and its absence is reported rather than hidden —
    a caller that does not pass it gets three DATA REQUIRED rows saying so,
    not three zeros.
    """
    kpis: list[Kpi] = []

    for key, label, definition, requires in _CEO_DEFINITIONS:
        kpis.append(_required(key, "CEO", label, definition, requires))
    for key, label, definition, requires in _CTO_DEFINITIONS:
        kpis.append(_required(key, "CTO", label, definition, requires))
    kpis.extend(_git_kpis(activity))

    open_issues = tuple(item for item in rollup.open_items if item.kind == "ISSUE")
    open_decisions = tuple(item for item in rollup.open_items if item.kind == "DECISION")
    unexecuted = tuple(
        item for item in rollup.open_items if item.kind == "DECISION_EXECUTION"
    )

    def _metric(key: str):
        return rollup.metric(key)

    def _from_metric(
        kpi_key: str, role: str, label: str, definition: str, metric_key: str, unit: str = ""
    ) -> Kpi:
        metric = _metric(metric_key)
        if metric is None:  # pragma: no cover - a rollup always carries these
            return _required(
                kpi_key,
                role,
                label,
                definition,
                f"rollup에 metric '{metric_key}'가 없다",
            )
        return Kpi(
            key=kpi_key,
            role=role,
            label=label,
            definition=definition,
            status=MEASURED,
            value=metric.value,
            unit=unit,
            source=metric.source,
            chain="Metric -> Project -> Issue" if metric.evidence else "Metric",
            evidence=metric.evidence,
        )

    kpis.extend(
        [
            _from_metric(
                "blocked_items",
                "COO",
                "Blocked Items",
                "지금 막혀 있는 Project 수",
                "open_blockers",
                "건",
            ),
            _from_metric(
                "critical_risk_count",
                "COO",
                "Open Risk Count",
                "막혔거나 위험하다고 보고된 Project 수",
                "projects_at_risk",
                "건",
            ),
            _from_metric(
                "open_issues",
                "COO",
                "Open Issues (Project 수)",
                "제기되고 아직 해결되지 않은 Issue를 가진 Project 수 — "
                "Event에 Issue 식별자가 없어 한 Project의 여러 Issue는 "
                "한 건으로 센다",
                "issues_open",
                "건",
            ),
            _from_metric(
                "unassigned_items",
                "COO",
                "Unassigned Open Items",
                "열려 있는 Issue/Decision 중 아무도 맡지 않은 것 — 제기한 팀은 "
                "맡은 팀이 아니다",
                "items_unassigned",
                "건",
            ),
            _from_metric(
                "unexecuted_decisions",
                "COO",
                "Unexecuted Decisions (Project 수)",
                "승인됐지만 아직 실행되지 않은 Decision을 가진 Project 수 — "
                "승인은 일이 아니다",
                "decisions_unexecuted",
                "건",
            ),
            _from_metric(
                "pending_decisions",
                "COO",
                "Pending Decisions (Project 수)",
                "요청되고 아직 승인/거절되지 않은 Decision을 가진 Project 수 — "
                "Event에 Decision 식별자가 없어 한 Project의 여러 Decision은 "
                "한 건으로 센다",
                "decisions_pending",
                "건",
            ),
        ]
    )

    # Aging: the two KPIs C149's Event vocabulary made computable at all.
    # Reported as the **oldest** open item rather than a mean, because the
    # decision a COO makes off this number is about one item — the one that
    # has been waiting longest — and a mean hides exactly that item behind
    # the ones that were settled quickly.
    for kpi_key, label, definition, items, kind in (
        (
            "issue_aging",
            "Issue Aging",
            "가장 오래 열려 있는 Issue의 나이",
            open_issues,
            "ISSUE_RAISED",
        ),
        (
            "decision_aging",
            "Decision Aging",
            "가장 오래 기다리는 Decision의 나이",
            open_decisions,
            "DECISION_REQUIRED",
        ),
        (
            "execution_aging",
            "Execution Aging",
            "승인되고 가장 오래 실행되지 않은 Decision의 나이",
            unexecuted,
            "DECISION_APPROVED",
        ),
    ):
        age = _oldest_age(items, now)
        if not items:
            # Nothing open is a real, measured answer of zero — not a
            # refusal. A DATA REQUIRED here would say "we cannot tell", when
            # in fact the window was read and held nothing open.
            kpis.append(
                Kpi(
                    key=kpi_key,
                    role="COO",
                    label=label,
                    definition=definition,
                    status=MEASURED,
                    value=0,
                    unit="일",
                    source=f"이 기간에 닫히지 않은 {kind}가 없다",
                    chain="Metric",
                )
            )
        elif age is None:  # pragma: no cover - validate_event blocks the shape
            kpis.append(
                _required(
                    kpi_key,
                    "COO",
                    label,
                    definition,
                    f"열린 {kind}가 {len(items)}건 있지만 timestamp를 읽을 수 "
                    "없어 나이를 계산할 수 없다",
                )
            )
        else:
            kpis.append(
                Kpi(
                    key=kpi_key,
                    role="COO",
                    label=label,
                    definition=definition,
                    status=MEASURED,
                    value=age,
                    unit="일",
                    source=f"가장 오래된 미해결 {kind}의 timestamp와 현재 시각의 차이",
                    chain="Metric -> Project -> Issue",
                    evidence=tuple(item.evidence for item in items),
                )
            )

    # Execution Completion Rate: completed projects over projects that moved.
    # A percentage and not a count, because the count alone answers a
    # different question — five completions is good in a week with six
    # projects and bad in a week with sixty.
    active = _metric("projects_active")
    completed = _metric("projects_completed")
    if active is not None and completed is not None and active.value > 0:
        kpis.append(
            Kpi(
                key="execution_completion_rate",
                role="COO",
                label="Execution Completion Rate",
                definition="이 기간에 움직인 Project 중 완료된 비율",
                status=MEASURED,
                value=round(100.0 * completed.value / active.value, 1),
                unit="%",
                source="projects_completed / projects_active — 둘 다 이 기간 "
                "Event에서 파생된 수이며, 분모는 '움직인 Project'이지 "
                "'회사의 모든 Project'가 아니다",
                chain="Metric -> Project",
                evidence=completed.evidence,
            )
        )
    else:
        kpis.append(
            _required(
                "execution_completion_rate",
                "COO",
                "Execution Completion Rate",
                "이 기간에 움직인 Project 중 완료된 비율",
                "이 기간에 움직인 Project가 없어 분모가 0이다. 비율이 없는 "
                "것이지 0%인 것이 아니다.",
            )
        )

    kpis.extend(
        [
            _required(
                "critical_project_on_time_rate",
                "COO",
                "Critical Project On-time Rate",
                "기한 내 완료된 중요 Project 비율",
                "Event Schema에 기한(due date)이 없고 Project의 중요도를 "
                "선언하는 곳도 없다. 완료 시각은 docs/04 §25의 Completed "
                "Date로 알 수 있지만, 비교할 기한이 없다.",
            ),
            _required(
                "process_cycle_time",
                "COO",
                "Process Cycle Time",
                "Project 시작에서 완료까지 걸린 시간",
                "STARTED와 COMPLETED가 모두 같은 기간 안에 있어야 계산할 수 "
                "있는데, 이 기간 밖에서 시작된 Project는 시작 시각이 창 "
                "밖에 있다(_roll_open_items의 같은 한계). 기간에 의존하지 "
                "않는 Project 개체가 필요하다.",
            ),
            _required(
                "operational_failure_sla",
                "COO",
                "Operational Failure / SLA",
                "SLA를 어긴 운영 실패 건수",
                "SLA가 선언된 곳이 없다. ops_status.py는 이 파이프라인의 "
                "실패를 세지만 회사 운영 SLA를 세지 않는다.",
            ),
        ]
    )

    return KpiSet(kpis=tuple(kpis), since=rollup.since, until=rollup.until)
