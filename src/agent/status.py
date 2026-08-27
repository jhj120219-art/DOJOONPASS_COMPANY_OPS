"""Read-only status snapshot of this machine's Agent.

Answers the questions an operator actually asks when something feels wrong,
using only files the Agent already writes:

    언제 마지막으로 실행됐나          state.last_run
    어디까지 수집했나                 state.last_successful_collection_date
    아직 안 한 날짜가 있나            catchup.pending_dates()
    보내지 못한 Event가 쌓여 있나     outbox/
    사람이 봐야 할 Signal이 있나      signals_rejected/
    어느 날짜도 읽지 못할 Signal이 있나   signals/ 의 날짜 디렉토리 밖
    한참 안 돌았나                    now - last_run

Nothing here writes, moves, deletes, locks, or sends. It can be called
while the Agent is running, from another process, without any risk to a
run in progress — which is the whole point: a diagnostic that can itself
break the thing it is diagnosing is not a diagnostic.

Deliberately no new stored state. Every field is derived from what the
Agent already persists, so this module cannot drift out of sync with
reality and adds nothing to recover after a crash. A `pending_signals`
count is NOT reported: knowing it would mean parsing every Signal file,
which is the Agent's job and would make a read-only status call able to
report Signals it has not validated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from .agent import DEFAULT_REJECTED_SIGNALS_DIR, derive_event_id
from .signals import DEFAULT_SIGNALS_DIR
from .catchup import pending_dates
from .outbox import DEFAULT_OUTBOX_DIR, DEFAULT_SENT_DIR, pending, safe_event_filename
from .state import DEFAULT_STATE_PATH, AgentStateError, load_state


@dataclass(frozen=True)
class AgentStatusSnapshot:
    desktop_id: str | None
    last_run: str | None
    last_successful_collection_date: date_type | None
    pending_dates: tuple[date_type, ...]
    outbox_count: int
    sent_count: int
    rejected_signal_count: int
    #: Signal files no target date can ever read — see
    #: `_count_unreachable_signals()`. Defaulted so an existing caller that
    #: does not pass `signals_dir` keeps working and simply reports 0.
    unreachable_signal_count: int = 0
    #: Signal files in a correctly named date directory that the
    #: watermark has already passed, and that were never delivered --
    #: see `_count_undelivered_signals_in_closed_dates()`. Defaulted for
    #: the same reason as the field above.
    undelivered_closed_signal_count: int = 0
    state_error: str | None = None

    @property
    def has_undelivered_events(self) -> bool:
        """Events created but not yet accepted by Transport.

        Not the same as "something is broken" — a run that has just staged
        its Events is momentarily in this state. It is only a problem in
        combination with a stale `last_run`, which is what
        `needs_attention()` weighs.
        """
        return self.outbox_count > 0

    @property
    def has_uncollected_dates(self) -> bool:
        return bool(self.pending_dates)

    def days_since_last_run(self, now: datetime) -> int | None:
        """Whole days since the Agent last completed a run, or None if never.

        Returns None rather than 0 for a never-run Agent, because "brand new"
        and "ran a moment ago" call for opposite reactions.
        """
        if self.last_run is None:
            return None
        try:
            parsed = datetime.fromisoformat(self.last_run)
        except ValueError:
            return None
        if parsed.tzinfo is None or now.tzinfo is None:
            parsed = parsed.replace(tzinfo=None)
            reference = now.replace(tzinfo=None)
        else:
            reference = now
        return max(0, (reference.date() - parsed.date()).days)

    def needs_attention(self, now: datetime, *, stale_after_days: int = 2) -> tuple[str, ...]:
        """Plain-language reasons a human should look at this Desktop.

        Empty means "nothing here needs a person". Ordered most-serious
        first. `stale_after_days` defaults to 2 rather than 1 because a
        machine that is simply off for a weekend is normal in this
        deployment (docs/07 §58) and a status view that cries wolf every
        Monday gets ignored.

        **Korean, and that is a contract rather than a preference (C120).**
        These strings are not a log line and not an API value. They are
        appended verbatim to `ops_status.main()`'s ATTENTION list, and that
        list is rendered on three surfaces:

            ops_status.py          the terminal block, every other line Korean
            dashboard_server.py    the ATTENTION panel, same list
            publish_control_tower  a bulleted list on the Notion page the
                                   whole workspace reads

        Measured on the live page before this changed: eight Korean bullets
        and, among them, `agent has not run for 15 day(s)`. Every sibling
        contributor to that list — `signal_attention`, `delivery_attention`,
        `lock_attention`, and every block in `ops_status.py` — was already
        Korean; this one function was the only English in the company's
        status page.

        The phrasing deliberately mirrors the lines it now sits beside:
        `Runner가 9.4일째 실행되지 않았다` is the Runner's, so the Agent's is
        `이 머신의 Agent가 15일째 실행되지 않았다`. "이 머신의" is not
        decoration — `ops_status.py` heads this whole section
        `AGENT — 이 머신의 Agent`, and the COMPANY section above it talks
        about *other* Desktops' agents.
        """
        reasons: list[str] = []
        if self.state_error is not None:
            reasons.append(f"이 머신의 Agent state를 읽을 수 없다: {self.state_error}")

        # A collection date in the future is a silent, permanent stop, and
        # every other signal here reports it as perfect health.
        #
        # `agent.run_once()` never writes one — it caps at `now` — but clock
        # skew on a machine that has since been corrected, or a state file
        # restored from a newer backup, can put one there. `pending_dates()`
        # then computes `start > end` and correctly returns nothing, so:
        # last_run is recent, outbox is empty, pending is zero. The Desktop
        # looks healthier than a working one, and it will not collect again
        # until the calendar reaches that date — possibly years.
        #
        # Detection only. `pending_dates()`'s refusal to walk backwards is
        # the safe behaviour and is left exactly as it is; nothing here
        # rewrites the state or reprocesses a date. Same restraint as
        # `scheduler/consistency.py`: report, never repair.
        collected_through = self.last_successful_collection_date
        if collected_through is not None and collected_through > now.date():
            reasons.append(
                f"이 머신의 Agent state가 {collected_through.isoformat()}까지 수집했다고 "
                f"말하는데 그 날짜는 미래다 (오늘은 {now.date().isoformat()}) — 그 날짜가 "
                f"올 때까지 아무것도 수집되지 않는다"
            )

        if self.outbox_count:
            reasons.append(
                f"만들어졌지만 전달되지 않은 Event {self.outbox_count}건"
            )
        if self.rejected_signal_count:
            reasons.append(
                f"거부된 Signal {self.rejected_signal_count}건이 사람을 기다리고 있다"
            )
        # Two facts, and they were reported as one. `days_since_last_run()`
        # answers None both for "no `last_run` at all" and for "`last_run` is
        # there but is not a timestamp", and this branch called both of them
        # "never completed a run".
        #
        # `agent/state.load_state()` validates that `last_run` is a *string*
        # and stops there — its sibling `last_successful_collection_date` is
        # additionally parsed and rejected if it will not, so one of the two
        # date fields is checked and the other is not. A restored, older, or
        # hand-edited state file (docs/11 §71 permits the edit) therefore
        # loads fine with `last_run` set to something like `2026-08-0` or
        # `yesterday`. Measured, four such values against a state that had
        # collected through the day before:
        #
        #     last_run '2026-08-18T09:00:00+09:00'   days 1      ATTENTION —
        #     last_run '2026-08-0'                   days None   'agent has
        #                                                         never
        #                                                         completed a
        #                                                         run'
        #
        # — a sentence that contradicts the `last_run` line `ops_status.py`
        # prints three lines above it, and that sends an operator to install
        # an Agent that is already installed. Worse, it is the branch that
        # *silences* staleness: an Agent that really has been down for weeks
        # reports the newcomer's line instead of the `N일째 실행되지 않았다`
        # one.
        #
        # Not fixed by validating the field on load. `last_run` is
        # informational — `last_successful_collection_date` is what decides
        # what gets collected — so rejecting it there would turn a cosmetic
        # corruption into a stopped Agent, which is the wrong direction. The
        # status view is where the distinction is worth having.
        #
        # The value itself is deliberately not quoted into the message: it is
        # a file's *contents*, and `ops_status.main()`'s ATTENTION block
        # states that its messages are built from filenames, ids and counts
        # rather than contents. The printed `last_run` line is where the
        # value already is.
        elapsed = self.days_since_last_run(now)
        if self.last_run is None:
            reasons.append("이 머신의 Agent가 한 번도 실행을 완료한 적이 없다")
        elif elapsed is None:
            reasons.append(
                "이 머신의 Agent state의 last_run이 timestamp가 아니다 — 마지막 실행 "
                "시각을 알 수 없어 지연 여부를 검사하지 못한다 (그 값은 위 last_run "
                "줄에 있다)"
            )
        elif elapsed >= stale_after_days:
            reasons.append(f"이 머신의 Agent가 {elapsed}일째 실행되지 않았다")
        if self.pending_dates:
            reasons.append(f"아직 수집되지 않은 날짜 {len(self.pending_dates)}건")
        return tuple(reasons)


def _count_json(directory: Path) -> int:
    path = Path(directory)
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.glob("*.json"))


def _count_rejected_signals(rejected_dir: Path) -> int:
    path = Path(rejected_dir)
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.rglob("*.json"))


def _is_date_directory_name(name: str) -> bool:
    """Whether `name` is what `date.isoformat()` produces.

    `date.fromisoformat()` alone is too generous here: on this interpreter it
    accepts `20260821` and `2026-W34-5`, and neither is a name
    `load_signals()` will ever look for — it builds the directory it reads
    with `target_date.isoformat()`, which is always `YYYY-MM-DD`. So the
    round trip is the test, not the parse.
    """
    try:
        return date_type.fromisoformat(name).isoformat() == name
    except ValueError:
        return False


def _count_unreachable_signals(signals_dir: Path) -> int:
    """Signal files that no target date can ever read.

    `load_signals()` reads exactly `signals_dir/<target_date>/*.json`, and
    `target_date` is stamped `YYYY-MM-DD`. A `*.json` anywhere else under
    `signals_dir` is therefore not "waiting" — it is unreachable. Measured
    with the real entrypoint, one Signal filed each way:

        signals/2026-08-21/s.json   COLLECTED and delivered
        signals/toplevel.json       never read
        signals/2026-8-21/s.json    never read   (unpadded month/day)
        signals/august-21/s.json    never read

    and for the three that were never read: not moved, not rejected, not
    logged, `rejected_signal_count=0`, `outbox_count=0`, `pending_dates=()`,
    run reported COMPLETED with exit 0 — and the watermark advanced **past**
    the date the work belonged to, so no later run reconsiders it. Work a
    person typed, gone, with every diagnostic reading all-clear.

    **This counts; it changes nothing.** Collecting such a file, or moving it
    to `signals_rejected/`, decides what a misfiled Signal means, and that is
    a decision (BACKLOG). Reporting it is not — it is the move C19's
    `is_locked`, C22's review counter, C23's stale lock and C24's
    `name_collision` all made.

    **Not the `pending_signals` count this module refuses.** That refusal is
    about *parsing* every Signal to see if it is valid, which is the Agent's
    job. This is a directory listing: nothing is opened, nothing is parsed,
    and the question is structural rather than a judgement about content.

    A correctly filed Signal is NOT counted even though it stays on disk
    after collection — `load_signals()` is deliberately side-effect free and
    never moves one, so "still in `signals/`" is normal and only the
    unreachable *location* is the signal.

    **Cost (C87).** The first implementation was `root.rglob("*.json")` and
    a filter, which builds a `Path` for every Signal ever written — and
    `signals/` never shrinks, because `load_signals()` deletes nothing and
    retention is an open policy question (BACKLOG B-6). Measured on this
    machine, before and after:

           files    rglob    scandir
             154    3.0 ms    0.9 ms
             904   35.5 ms    3.7 ms
           3,654   83.0 ms   29.4 ms      <- a year at 10 Signals/day
          10,954  567.3 ms  255.9 ms

    Identical answers at every size. The shape of the question is what makes
    it cheap: a file is reachable only if it sits **directly** in a
    `YYYY-MM-DD` directory one level down, so a valid date directory needs
    its entries listed (to find anything nested deeper) and its `.json`
    files never need to be looked at one by one. The recursive walk only
    runs for subtrees that are already abnormal.

    Errors count rather than vanish, in both directions: an entry this
    process cannot even `is_dir()` is counted as unreachable. Over-reporting
    is the safe direction for a data-loss signal, and it is the direction
    this repository already chose for secret reporting.
    """
    root = Path(signals_dir)
    if not root.is_dir():
        return 0

    def _all_json_below(path: str) -> int:
        total = 0
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir():
                            total += _all_json_below(entry.path)
                        elif entry.name.endswith(".json"):
                            total += 1
                    except OSError:
                        total += 1
        except OSError:
            # A read-only diagnostic must not become the thing that fails —
            # and it must not answer **zero** either, which is what returning
            # `total` unchanged did.
            #
            # This is the branch the date-directory case below argues
            # against, in this function, unfixed: a directory whose parent
            # listed it is a directory that exists, so its contents "cannot
            # be ruled out" for exactly the reason written there. Measured,
            # three misfiled Signals under a directory `os.scandir()`
            # refuses:
            #
            #     whole non-date dir unlistable        healthy 3  ->  0
            #     subtree under a non-date dir          healthy 3  ->  0
            #     a nested dir inside a date dir        healthy 3  ->  0
            #     a date dir itself (the branch below)  healthy 0  ->  1
            #
            # Three of the four ways to hit "cannot list this" made the
            # number *smaller* — down to the one value that reads as "there
            # is nothing to look at". `ops_status.py` then prints
            # `읽힐 수 없는 Signal : 0` and raises no ATTENTION for Signals
            # that no date will ever read. Same shape as C62, C68 and C101's
            # M5: a refused entry counted as a clean one.
            #
            # `+ 1`, not the healthy count, because the healthy count is
            # exactly what is unknowable here. One is the smallest statement
            # that is still a statement, and it matches the sibling branch
            # below to the character.
            return total + 1
        return total

    count = 0
    try:
        with os.scandir(root) as entries:
            top = list(entries)
    except OSError:
        return 0

    for entry in top:
        try:
            is_dir = entry.is_dir()
        except OSError:
            count += 1
            continue
        if not is_dir:
            if entry.name.endswith(".json"):
                count += 1
            continue
        if not _is_date_directory_name(entry.name):
            count += _all_json_below(entry.path)
            continue
        # A valid date directory: its own `*.json` files are what
        # `load_signals()` reads, so only what is nested below it counts.
        try:
            with os.scandir(entry.path) as inner:
                children = list(inner)
        except OSError:
            # A date directory this process cannot list is a date directory
            # whose nesting cannot be ruled out, so it counts. `continue`ing
            # in silence here would have made the number *smaller* for a
            # tree that got harder to read -- the direction that reads as
            # reassurance, and the one C62 and C68 both removed elsewhere.
            # Caught by `ASilentlyDroppedEntryIsARosterNotAParagraphTests`
            # the first time this function ran under it.
            count += 1
            continue
        for child in children:
            try:
                if child.is_dir():
                    count += _all_json_below(child.path)
            except OSError:
                count += 1
    return count


def _entry_is_file(entry) -> bool:
    """`DirEntry.is_file()`, with a refusal counted as "not a file".

    An entry that cannot be stat-ed is not evidence of delivery, and
    treating it as one would hide a lost Signal -- the direction this
    whole family of counters exists to avoid.
    """
    try:
        return entry.is_file()
    except OSError:
        return False


def _count_undelivered_signals_in_closed_dates(
    signals_dir: Path,
    sent_dir: Path,
    *,
    source: str | None,
    collected_through: date_type | None,
) -> int:
    """Signal files in a **closed** date that were never delivered.

    `_count_unreachable_signals()` above answers "filed where no date will
    read it". This answers the other half, and it is the half that needs no
    mistake at all:

        pending_dates() ends at **yesterday** and never walks backwards
        (`catchup.pending_dates()`, docs/07 section 50)

    So once the watermark reaches a date, a Signal added to that date's
    directory afterwards is never read again -- by any run, ever. The
    directory name is correct, the filename is correct, the content is
    valid, and nothing looks at it.

    **Measured with the real entrypoint, no misconfiguration anywhere:**

        08:00  the scheduled run collects 2026-08-23   watermark 2026-08-23
        09:00  the person writes up the afternoon into
               signals/2026-08-23/afternoon.json
        09:00  run 2                COMPLETED   delivered: still 1
        +1 day, +2 days: 2 more runs COMPLETED  delivered: still 1

        the file is still on disk        never delivered, never rejected
        the agent log never names it
        outbox_count 0   rejected_signal_count 0   unreachable_signal_count 0
        pending_dates ()   needs_attention ()

    Writing up yesterday after this morning's run is not an exotic
    operation; it is the ordinary shape of the working day, and Signal
    authoring is by hand (BACKLOG A-11).

    **The predicate is exact and reuses production code rather than
    restating it** (C28): `derive_event_id()` is what stamped the Event, and
    `outbox.is_sent()` is what the delivery path asks. Neither opens a
    Signal -- the id is built from the source, the directory name and the
    file stem -- so this stays a directory listing, which is the whole basis
    on which this module refuses a `pending_signals` count.

    Dates *after* the watermark are not counted: those are pending, and the
    next run reads them. That is the same asymmetry C68 drew between an
    absent subject and a failed read.

    **This counts; it changes nothing.** Re-reading a closed date, or moving
    the file forward, decides what a late Signal *means* -- and
    `pending_dates()` refusing to walk backwards is a deliberate rule with
    its own reasons (a re-read date re-derives Events the Collector has
    already seen). That is a decision (BACKLOG). Saying it is there is not.

    Rests on `sent/` being kept: it is never pruned today (the retention
    question is BACKLOG A-6's, still open). If it ever is, this count
    becomes an over-report rather than an under-report -- the safe
    direction, and the one this repository already chooses for secrets.
    """
    if source is None or collected_through is None:
        # No watermark and no identity means no date is closed yet.
        return 0
    signals = Path(signals_dir)
    sent = Path(sent_dir)

    # `sent/` read ONCE, not once per Signal (C101). `outbox.is_sent()`
    # is one `is_file()` per call, so asking it per Signal made this
    # counter cost one stat each: measured 28 us per Signal, 140 ms at
    # three years and 560 ms at eleven -- on a script whose whole premise
    # is that a person runs it first, casually. One directory listing is
    # 5 us per Signal instead, the same `glob+is_file -> scandir` swap
    # `_daily_dates()` (16x) and `_count_unreachable_signals()` (C87)
    # already carry.
    #
    # **`is_file()` is kept, not traded for `exists()`.** `is_sent()`'s
    # own docstring records the measurement: a *directory* carrying an
    # Event's name made it answer True, which is the Agent declining to
    # send an Event it never sent. `DirEntry.is_file()` answers from the
    # listing that was already fetched, so the rule survives the batching
    # at no cost. `TheDeliveredSetIsTheSameQuestionIsSentAsksTests` pins
    # that this stays the same question rather than a lookalike.
    if not sent.is_dir():
        # Nothing has ever been delivered, which is a fact rather than a
        # failure: every closed-date Signal below really is undelivered.
        delivered: set[str] = set()
    else:
        try:
            with os.scandir(sent) as entries:
                delivered = {e.name for e in entries if _entry_is_file(e)}
        except OSError:
            # The directory is there and cannot be listed, so whether a
            # Signal was delivered is unknown. Reporting every one of
            # them would be a false alarm the size of the whole tree;
            # reporting none makes no claim. `read_status()` surfaces an
            # unreadable `sent/` through `sent_count` either way.
            return 0
    count = 0
    try:
        days = list(os.scandir(signals))
    except OSError:
        # Unreadable, not absent. `_count_unreachable_signals()` reports the
        # same directory failing the same way, and one line about one
        # directory is enough -- 0 here, and that line carries it.
        return 0
    for day_entry in days:
        if not _is_date_directory_name(day_entry.name):
            continue  # `_count_unreachable_signals()` owns those
        try:
            if not day_entry.is_dir():
                continue
        except OSError:
            # C88: unreadable is not the same as absent, and collapsing them
            # is what this whole family of counters exists to stop.
            count += 1
            continue
        day = date_type.fromisoformat(day_entry.name)
        if day > collected_through:
            continue  # still pending; a later run will read it
        try:
            files = list(os.scandir(day_entry.path))
        except OSError:
            count += 1
            continue
        for signal in files:
            if not signal.name.endswith(".json"):
                continue
            try:
                if not signal.is_file():
                    continue
            except OSError:
                count += 1
                continue
            event_id = derive_event_id(
                source=source,
                target_date=day,
                signal_id=Path(signal.name).stem,
            )
            if safe_event_filename(event_id) not in delivered:
                count += 1
    return count


def read_status(
    *,
    agent_start_date: date_type | None = None,
    now: datetime | None = None,
    state_path: Path | None = None,
    outbox_dir: Path | None = None,
    sent_dir: Path | None = None,
    rejected_signals_dir: Path | None = None,
    signals_dir: Path | None = None,
) -> AgentStatusSnapshot:
    """Build the snapshot. Never raises for a damaged state file.

    A corrupted `agent_state.json` is exactly when someone needs this view
    most, so `AgentStateError` is captured into `state_error` rather than
    propagated — unlike `agent.run_once()`, which must refuse to proceed on
    a state it cannot trust. Reading is safe where acting is not.

    `pending_dates` is only computed when `agent_start_date` is supplied,
    since a first-ever run has no other way to know where counting starts
    (docs/07 §50: never guessed).
    """
    now = now or datetime.now().astimezone()
    state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    outbox_dir = Path(outbox_dir) if outbox_dir is not None else DEFAULT_OUTBOX_DIR
    sent_dir = Path(sent_dir) if sent_dir is not None else DEFAULT_SENT_DIR
    rejected_signals_dir = (
        Path(rejected_signals_dir)
        if rejected_signals_dir is not None
        else DEFAULT_REJECTED_SIGNALS_DIR
    )
    signals_dir = Path(signals_dir) if signals_dir is not None else DEFAULT_SIGNALS_DIR

    state_error: str | None = None
    try:
        state = load_state(state_path)
    except AgentStateError as exc:
        state_error = str(exc)
        state = None

    upcoming: tuple[date_type, ...] = ()
    if state is not None and agent_start_date is not None:
        upcoming = tuple(
            pending_dates(
                last_successful_collection_date=state.last_successful_collection_date,
                start_date=agent_start_date,
                now=now,
            )
        )

    return AgentStatusSnapshot(
        desktop_id=state.desktop_id if state is not None else None,
        last_run=state.last_run if state is not None else None,
        last_successful_collection_date=(
            state.last_successful_collection_date if state is not None else None
        ),
        pending_dates=upcoming,
        outbox_count=len(pending(outbox_dir)),
        sent_count=_count_json(sent_dir),
        rejected_signal_count=_count_rejected_signals(rejected_signals_dir),
        unreachable_signal_count=_count_unreachable_signals(signals_dir),
        undelivered_closed_signal_count=_count_undelivered_signals_in_closed_dates(
            signals_dir,
            sent_dir,
            source=state.desktop_id if state is not None else None,
            collected_through=(
                state.last_successful_collection_date if state is not None else None
            ),
        ),
        state_error=state_error,
    )
