"""Cohort analysis over Execution Evidence — 시작한 일이 계속 움직이는가.

Why this exists
---------------
Every number this Control Tower produces is a **period total**. `_roll_metrics()`
counts what happened between `since` and `until`; `kpi.py` frames those counts
by role; `_roll_open_items()` says what is open right now. All of them answer
"이 기간에 무슨 일이 있었나", and none of them can answer the question a period
total structurally cannot:

    8월에 시작한 Project들은 지금도 움직이고 있는가?
    7월에 시작한 것들과 비교하면 나아지고 있는가?

That is a *cohort* question — it groups by when something entered the system
and then follows that same group forward — and it is the one shape that turns
"이번 달 Event 40건" into "시작한 일의 절반은 일주일 안에 멈춘다". The second
sentence has an action attached to it. The first does not.

Why the cohort unit is a Project, and what it is not
----------------------------------------------------
The usual cohort unit is a **customer**, acquired in month M. This system has
no customer: `kpi.py` already says so out loud and refuses twelve CEO KPIs on
exactly that ground — "고객이라는 개체가 이 시스템에 없다. project_id는 내부
프로젝트 식별자이고 고객 식별자가 아니다". Nothing here changes that, and this
module must not be read as having quietly supplied one. A user/customer cohort
stays DATA REQUIRED in `kpi.py`, where it already is.

What this system *does* have, on every Event and with no invention at all, is
`project_id` and `timestamp`. So the cohort is:

    unit         a Project
    acquisition  the calendar day (Asia/Seoul) of its **first** Event
    cohort       the month that day falls in
    retention    of the Projects still running, did it produce a **later**
                 Event inside D+N

Three of the four candidate units were considered and rejected on data rather
than on difficulty:

    사용자/고객 획득 월   no customer entity exists at all (kpi.py's own finding)
    Issue 생성 월        `ISSUE_RAISED` exists in the vocabulary (C149) and this
                         repository's evidence carries none, so every cohort
                         would be empty — and an Issue has no identifier across
                         two Events (`_OPEN_ITEM_LIFECYCLES`), so a "cohort of
                         Issues" could not be counted even where the Events did
                         exist. It would be a chart of zeros.
    Project 생성 월      every Event carries `project_id`; `_roll_projects()`
                         already folds `first_seen` and every Event's timestamp
                         per project. Real data, today.

Nothing is re-derived
---------------------
This reads `CompanyRollup.state_projects` and nothing else. `first_seen` is the
fold's own answer to "when did this project first appear", and the event days
come from `ProjectRollup.evidence`, whose `EvidenceRef.at` **is** the Event's
timestamp. So there is no second pass over `processed/`, no second definition
of a project's start, and no second duplicate-fold — `build_company_rollup()`
already folded one `event_id` arriving as two files (C50), and this counts what
survived that.

`state_projects` rather than `projects`, and the difference matters here more
than anywhere else: `projects` is the **windowed** fold, so a project that
started in June and moved in August would carry `first_seen = August` and land
in the wrong cohort. `state_projects` is the corpus up to `until` with no
`since`, which is the only corpus in which "first ever Event" means what it
says.

A window that has not elapsed is not a low score
------------------------------------------------
This is the whole correctness of the module. A cohort opened four days ago has
**no** D+30 answer: nothing about it is known, and the arithmetic
`retained / size` would confidently return a small percentage for a question
nobody could have answered yet. That number would then be compared against last
month's mature one, and the reading — "새로 시작한 일이 훨씬 빨리 죽는다" —
would have been produced entirely by the calendar.

So each window carries its own denominator. A member counts toward D+N only
when `first + N days <= as_of`; when no member does, the window is
`DATA_REQUIRED` and renders as `DATA REQUIRED` rather than as `0%` — the same
refusal `kpi.Kpi.rendered()` makes, for the same reason. `base` is on every row,
so a rate over three of eleven members can never be read as a rate over eleven.

A finished Project has not stalled, and a killed one is not progress
--------------------------------------------------------------------
The second thing the denominator has to exclude, and the one that was wrong
until the second audit. Retention was "produced a later Event", full stop, so
over one cohort of three — completed-on-day-one, cancelled-on-day-three,
abandoned — the panel reported `D+7 33.3%`, counting the **cancelled** Project
as still moving and the **finished** one as stalled. Both readings are
backwards, and a COO acting on that number goes looking for the wrong Project.

So a member whose lifecycle **ended inside the window** — `ProjectRollup.
settled_at`, which is the rollup's own COMPLETED/CANCELLED fold and not a
second reading of it — leaves the rate and is counted in `settled` instead.
What is left in `base` is the only population the question is about: Projects
that were still running and could therefore have stalled. `settled` is on every
row, so a cohort that finished its work is visible as that rather than as a
low score or a refusal.

Never raises, and never guesses at a date
-----------------------------------------
Same posture as the rollup it reads. A timestamp that cannot be parsed, or that
is naive (no offset — so there is no instant and `business_date()` refuses to
invent one), sends its Project to `skipped` with the reason rather than being
dropped or guessed at. That path is unreachable from the pipeline and says so
where it is written: the rollup's own `_event_date()` calls the same parser and
routes those Events to `CompanyRollup.unreadable`, which every view already
renders.

A first Event dated **after** the analysis date is a different case and is not
skipped at all — it gets its own cohort, every window of which is unelapsed and
therefore DATA REQUIRED. A month that has not happened, on screen, is the
integrity problem an operator should meet; a list nothing renders is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta

from businessdate import business_date

from .kpi import DATA_REQUIRED, DATA_REQUIRED_READING, MEASURED
from .rollup import CompanyRollup, EvidenceRef, ProjectRollup

#: The elapsed-time marks each cohort is followed to, in days after the day the
#: Project first appeared.
#:
#: Three rather than five (no D+60 / D+90), and the reason is this repository's
#: own evidence: it spans six days. A D+90 column would read `DATA REQUIRED` for
#: every cohort that will exist for the next quarter, which is a column that
#: teaches a reader to skip the table. D+1 / D+7 / D+30 are the three the
#: request names and the three a month-grained cohort can actually fill.
#:
#: A fixed tuple rather than a parameter: the Dashboard panel declares one
#: column per window, and `dashboard.DASHBOARD_SCHEMA_VERSION` pins those
#: columns. A caller-chosen window set would make the payload's shape depend on
#: its input, which is the one thing that fingerprint exists to prevent.
COHORT_WINDOWS: tuple[int, ...] = (1, 7, 30)

#: What this module counts. Not a customer — see the module docstring.
COHORT_UNIT = "PROJECT"

#: The third reading, beside a percentage and `DATA REQUIRED`.
#:
#: Spelled in this project's existing words: `dashboard_server._panel_html()`
#: already renders an empty sourced panel as "해당 없음 — 이 기간의 증거에 이
#: 항목이 하나도 없었다", which is exactly this sentence about one cell. A
#: fourth vocabulary for "there is nothing here to measure" is the drift
#: `verdict.py` exists to prevent.
NOT_APPLICABLE_READING = "해당 없음"


@dataclass(frozen=True)
class CohortWindow:
    """One D+N mark for one cohort.

    `base` is the denominator and it is **not** the cohort's size: only the
    members whose N days have fully elapsed as of the analysis date are in it.
    `size - base` is the members still inside the window, whose answer does not
    exist yet.
    """

    days: int
    #: Members whose window has fully elapsed **and that were still running**
    #: for it — the denominator.
    base: int = 0
    #: Of those, the ones that produced a later Event inside the window.
    retained: int = 0
    #: Members whose window elapsed and whose lifecycle **ended inside it** —
    #: completed or cancelled. Not in `base`, and not lost.
    #:
    #: This is the correction C-audit-2 made, and it is worth stating as a
    #: measurement rather than a preference. Before it, over one cohort of
    #: three — one Project completed the day it started, one cancelled on day
    #: three, one simply abandoned — the panel reported:
    #:
    #:     D+7  33.3%   (1 of 3 "still moving")
    #:
    #: The one counted as *moving* was the **cancelled** one, and the one
    #: counted as *stalled* was the one that **finished**. Both readings are
    #: backwards, and a COO acting on that number would go looking for the
    #: wrong project. A finished Project has not stalled and a killed Project
    #: is not progress; neither belongs in a retention rate at all.
    settled: int = 0

    @property
    def elapsed(self) -> int:
        """Members this window can say anything about at all."""
        return self.base + self.settled

    @property
    def status(self) -> str:
        """`MEASURED` / `DATA_REQUIRED`, in `kpi.py`'s own vocabulary.

        Derived from `base` rather than stored beside it. The two would be one
        fact in two fields — a window has an answer exactly when somebody's N
        days have elapsed and is still running for it — and a stored copy is a
        copy that can be set wrong by a caller building one of these by hand,
        which is how a refusal turns back into a zero.
        """
        return MEASURED if self.base > 0 else DATA_REQUIRED

    # No `is_measured` accessor beside `status`. `Kpi` has one because a
    # caller filters on it (`KpiSet.measured`); nothing filters cohort windows,
    # and `status` already answers the question in the vocabulary a reader
    # knows. A wrapper with no caller reads as though somebody needed it — the
    # reason `DashboardPanel` states for dropping its own.

    @property
    def rate(self) -> float | None:
        """Retention as a percentage, or None when nothing can be divided.

        None and not 0.0. `0.0` is the claim "아무도 다시 움직이지 않았다",
        which is a real and different finding from "이 창은 아직 지나지 않았다"
        — and the two would be the same pixel on a chart.

        The condition is `base` and not `status`: they are the same test (see
        `status`), and asking the derived one would be a second reading of a
        fact this class stores exactly once.
        """
        if self.base <= 0:
            return None
        return round(100.0 * self.retained / self.base, 1)

    def rendered(self) -> str:
        """The value as a person should see it — three answers, not two.

        One place, for `Kpi.rendered()`'s reason: a screen, a payload and a page
        must not disagree about how a refusal is spelled, and none of them may
        spell it `0%`.

            x.y%             a rate over `base` Projects that were running
            DATA REQUIRED    nothing has elapsed yet — ask again later
            NOT_APPLICABLE   everything that elapsed had already **ended**
                             inside the window, so there was nobody left to
                             stall

        The third is not pedantry, and collapsing it into the second would be a
        false sentence in the one direction that matters. "아직 모른다" tells a
        COO to wait; "이 Cohort는 창 안에 전부 끝났다" is the best outcome this
        panel can report, and it is the row they would otherwise chase.
        """
        rate = self.rate
        if rate is not None:
            return f"{rate:.1f}%"
        if self.elapsed == 0:
            return DATA_REQUIRED_READING
        return NOT_APPLICABLE_READING


@dataclass(frozen=True)
class Cohort:
    """Every Project whose first Event fell in one month, followed forward."""

    key: str
    #: `project_id`s, in the rollup's own order. Authored strings — a view
    #: redacts them on the way out, exactly as it does everywhere else.
    members: tuple[str, ...] = ()
    windows: tuple[CohortWindow, ...] = ()
    #: The first Event of each member — the file that put it in this cohort.
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def size(self) -> int:
        return len(self.members)

    # No `window(days)` lookup. `windows` is a tuple of three in a fixed order
    # and every consumer wants all of them — `_cohort_panel()` iterates it to
    # build one column trio per window, and the terminal reads the row it
    # produced. An accessor for a single window had exactly one caller, that
    # panel, and removing it removed a defensive `if window is None` branch
    # that could only ever have printed a zero for a window nobody computed.
    # `DeadCapabilityInventoryTests` is what noticed it had stopped being
    # called at all.


@dataclass(frozen=True)
class CohortAnalysis:
    """Every cohort, oldest first, and what could not be placed in one."""

    cohorts: tuple[Cohort, ...] = ()
    unit: str = COHORT_UNIT
    windows: tuple[int, ...] = COHORT_WINDOWS
    #: The day maturity is measured against. Every window that ends after this
    #: date is DATA REQUIRED rather than low.
    as_of: date_type | None = None
    #: `(project_id, reason)` for a Project whose first Event carries a date
    #: this module could not place. Reported rather than dropped, for
    #: `CompanyRollup.unreadable`'s reason.
    skipped: tuple[tuple[str, str], ...] = ()

    # No `cohort(key)` accessor. `CompanyRollup.metric()` and `Cohort.window()`
    # both exist because something calls them; one here would have no caller,
    # and this project has removed such wrappers before for
    # `DashboardPanel`'s stated reason — an uncalled accessor reads as though
    # somebody needed it. `DeadCapabilityInventoryTests` is what noticed.


def _day(iso: str | None) -> date_type | None:
    """The Seoul calendar day of an Event timestamp, or None.

    `business_date()` and not `.date()`, so the same instant lands in the same
    cohort whatever offset the reporting Desktop wrote it with. It raises on a
    naive value on purpose — a value with no offset has no instant, so "which
    month was that" has no answer — and that refusal is converted to None here
    rather than being guessed at.
    """
    if not iso:
        return None
    try:
        return business_date(datetime.fromisoformat(iso))
    except (TypeError, ValueError):
        return None


def _event_days(project: ProjectRollup, as_of: date_type) -> set[date_type]:
    """The distinct days this Project produced an Event, up to `as_of`.

    Bounded by `as_of` for the same reason the windows are: an Event dated after
    the analysis date is evidence about a period this analysis does not cover,
    and letting it satisfy a D+N whose window has "elapsed" would make a
    cohort's retention depend on data from that cohort's future. A clock behind
    a reporting Desktop is enough to produce one.
    """
    days = set()
    for ref in project.evidence:
        day = _day(ref.at)
        if day is not None and day <= as_of:
            days.add(day)
    return days


def build_cohort_analysis(rollup: CompanyRollup, *, now: datetime) -> CohortAnalysis:
    """Group `rollup`'s Projects by the month of their first Event.

    `now` is the caller's instant, for `DashboardModel`'s reason: a derivation
    that reads the clock cannot be tested for the answer it gives at a given
    moment, and every maturity decision here is made against it.

    The analysis date is `min(today, rollup.until)` when the rollup was bounded.
    Not `today` alone: a rollup asked for "as of the 10th" holds no Event after
    the 10th, so treating a window that ends on the 20th as elapsed would score
    every member of it as lost — using the absence of evidence that was
    deliberately excluded.
    """
    # `business_date()` refuses a naive value; `clock_date()` reads one as KST.
    # This is a clock reading rather than data, which is the case `clock_date()`
    # exists for — but it is imported as `business_date` for the aware case and
    # the naive fallback is written here rather than adding a second import for
    # one branch.
    as_of = business_date(now) if now.tzinfo is not None else now.date()
    if rollup.until is not None and rollup.until < as_of:
        as_of = rollup.until

    grouped: dict[str, list[tuple[ProjectRollup, date_type]]] = {}
    skipped: list[tuple[str, str]] = []
    # `state_projects` is the fold that ignores `since` — see the module
    # docstring for why the windowed one puts Projects in the wrong cohort.
    # Read directly, with no `or rollup.projects` fallback:
    # `build_company_rollup()` always sets it (it *is* `projects` when there is
    # no `since`), so the fallback would be a branch nothing can take, and a
    # second corpus this function could silently be reading instead.
    for project in rollup.state_projects:
        first = _day(project.first_seen)
        if first is None:  # pragma: no cover - unreachable from the pipeline
            # Unreachable from `build_company_rollup()`, and the reason is an
            # invariant rather than an accident — the same one
            # `dashboard._evidence_day()` writes down. `_roll_projects()` sets
            # `first_seen` from `event.timestamp` for every Project it makes,
            # and every Event that reaches it has already been through
            # `_event_date()`, which calls the same `fromisoformat` and sends
            # the failures to `CompanyRollup.unreadable` — which the screen
            # already renders as "읽지 못한 파일 N건 — 아래 숫자는 그만큼 적다".
            #
            # Kept, and kept as a *report* rather than a drop, for that
            # function's stated reason: this feeds a fold over a whole
            # company's evidence and one unparseable value must not take the
            # view down. Reachable through the `events=` seam, where a caller
            # hands over objects it built itself — which is how the test for
            # it gets here.
            skipped.append(
                (
                    project.project_id,
                    "first Event timestamp is not a dated instant: "
                    f"{project.first_seen!r}",
                )
            )
            continue
        # A Project whose first Event is dated **after** the analysis date is
        # deliberately not skipped: it gets its own cohort, in which every
        # window is unelapsed and therefore DATA REQUIRED. That is the honest
        # rendering and, more to the point, it is a *visible* one — a bar group
        # labelled with a month that has not happened is exactly the integrity
        # problem an operator should see (a reporting Desktop whose clock is
        # fast), and the PROJECTS panel is already showing the same Project with
        # the same future `first_seen`. Filing it into a list nothing renders
        # would hide it instead. The maturity rule needs no special case:
        # `first + N days > as_of` is already true for every N.
        grouped.setdefault(f"{first.year:04d}-{first.month:02d}", []).append(
            (project, first)
        )

    cohorts = []
    for key in sorted(grouped):
        members = grouped[key]
        # Once per member, not once per member per window: `_event_days()`
        # parses every one of that Project's timestamps, and the three windows
        # ask the same set three different questions.
        active = {
            project.project_id: _event_days(project, as_of)
            for project, _ in members
        }
        windows = []
        for days in COHORT_WINDOWS:
            base = 0
            retained = 0
            settled = 0
            for project, first in members:
                end = first + timedelta(days=days)
                if end > as_of:
                    continue  # the window has not elapsed for this member
                # A Project whose lifecycle **ended** inside the window is out
                # of the retention question entirely — see `CohortWindow.settled`
                # for the measurement that put it there. `settled_at` is the
                # rollup's own answer to "when did this end", so this is not a
                # second reading of COMPLETED / CANCELLED (C28).
                closed = _day(project.settled_at)
                if closed is not None and closed <= end:
                    settled += 1
                    continue
                base += 1
                # Strictly **after** the acquisition day: a second Event on the
                # first afternoon is the same day's work, not a return. The
                # upper bound is inclusive, so D+1 is "moved again the next
                # day" and D+7 is "moved again within the week".
                if any(first < day <= end for day in active[project.project_id]):
                    retained += 1
            windows.append(
                CohortWindow(
                    days=days, base=base, retained=retained, settled=settled
                )
            )
        cohorts.append(
            Cohort(
                key=key,
                members=tuple(project.project_id for project, _ in members),
                windows=tuple(windows),
                # The Event that put each member in this cohort. `evidence[0]`
                # is the first one because `_roll_projects()` appends in
                # instant order — the same ordering `first_seen` comes from, so
                # the citation and the cohort assignment cannot disagree.
                evidence=tuple(
                    project.evidence[0] for project, _ in members if project.evidence
                ),
            )
        )

    return CohortAnalysis(
        cohorts=tuple(cohorts),
        unit=COHORT_UNIT,
        windows=COHORT_WINDOWS,
        as_of=as_of,
        skipped=tuple(skipped),
    )
