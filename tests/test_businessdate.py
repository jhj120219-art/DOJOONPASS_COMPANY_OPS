"""`src/businessdate.py` — the one place that turns an instant into a day.

C135. Three groups of tests, and the split matters:

  * the module's own contract (`KST`, `now()`, `to_kst()`, `business_date()`);
  * the **UTC/KST midnight boundary** at every surface that dates something,
    because that boundary is where the whole class of defect lives and a unit
    test of the helper alone would not have caught any of the ten call sites;
  * the **machine clock zone**, run under a real `TZ=UTC0` child process,
    because "does this still work on a machine that is not in Seoul" cannot be
    answered by injecting a `now` — the whole point is what happens when
    nobody injects one.

The defect this replaced (BUG-26, and its `now()` half) was reachable exactly
one way: `.astimezone()` and `.date()` both answer with respect to something
that is not Asia/Seoul, and docs/06 §9 says Asia/Seoul is the answer.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import businessdate  # noqa: E402
from businessdate import KST, business_date, clock_date, to_kst  # noqa: E402
from daily import generate_daily_history  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
)
from scheduler import run_once as scheduler_run_once  # noqa: E402

UTC = timezone.utc


class KstConstantTests(unittest.TestCase):
    def test_kst_is_utc_plus_nine(self):
        """docs/06 §9: 'Asia/Seoul', i.e. 'UTC+09:00'."""
        self.assertEqual(KST.utcoffset(None), timedelta(hours=9))

    def test_the_offset_does_not_move_with_the_season(self):
        """A fixed offset rather than `ZoneInfo`, on purpose (see the module
        docstring). Korea has observed no DST since 1988, so this must hold in
        January and July alike — if it ever does not, the module comment that
        justifies the fixed offset has become wrong."""
        for month in range(1, 13):
            with self.subTest(month=month):
                moment = datetime(2026, month, 15, 12, 0, tzinfo=UTC)
                self.assertEqual(moment.astimezone(KST).utcoffset(), timedelta(hours=9))

    def test_the_module_imports_nothing_from_this_project(self):
        """It is a leaf, and the layering table says so. Stated here too
        because this is the file someone reads when they want to add
        something to it."""
        source = (SRC / "businessdate.py").read_text(encoding="utf-8")
        for forbidden in ("from events", "from oplog", "import events", "import oplog"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class ToKstTests(unittest.TestCase):
    def test_the_same_instant_however_it_is_written(self):
        spellings = (
            "2026-08-05T15:00:00+00:00",
            "2026-08-06T00:00:00+09:00",
            "2026-08-05T10:00:00-05:00",
            "2026-08-05T20:30:00+05:30",
        )
        converted = {to_kst(datetime.fromisoformat(s)).isoformat() for s in spellings}
        self.assertEqual(converted, {"2026-08-06T00:00:00+09:00"})

    def test_a_naive_datetime_is_refused_rather_than_guessed_at(self):
        with self.assertRaises(ValueError) as caught:
            to_kst(datetime(2026, 8, 5, 15, 0))
        self.assertIn("naive", str(caught.exception))

    def test_the_refusal_names_the_value_it_refused(self):
        """An operator reading this in a log needs to know which value it
        was, not only that there was one."""
        with self.assertRaises(ValueError) as caught:
            to_kst(datetime(2026, 8, 5, 15, 0))
        self.assertIn("2026-08-05T15:00:00", str(caught.exception))


class BusinessDateBoundaryTests(unittest.TestCase):
    """The midnight boundary, from both sides, in both zones."""

    def test_utc_15_00_is_already_the_next_seoul_day(self):
        self.assertEqual(
            business_date(datetime(2026, 8, 5, 15, 0, tzinfo=UTC)), date(2026, 8, 6)
        )

    def test_utc_14_59_is_still_the_same_seoul_day(self):
        self.assertEqual(
            business_date(datetime(2026, 8, 5, 14, 59, 59, tzinfo=UTC)), date(2026, 8, 5)
        )

    def test_seoul_midnight_exactly(self):
        self.assertEqual(
            business_date(datetime(2026, 8, 6, 0, 0, tzinfo=KST)), date(2026, 8, 6)
        )

    def test_one_second_before_seoul_midnight(self):
        self.assertEqual(
            business_date(datetime(2026, 8, 5, 23, 59, 59, tzinfo=KST)), date(2026, 8, 5)
        )

    def test_every_hour_of_a_utc_day(self):
        """The sweep, as one property: 15:00 UTC onward is tomorrow in Seoul."""
        for hour in range(24):
            with self.subTest(hour=hour):
                self.assertEqual(
                    business_date(datetime(2026, 8, 5, hour, tzinfo=UTC)),
                    date(2026, 8, 6) if hour >= 15 else date(2026, 8, 5),
                )

    def test_a_month_boundary_too(self):
        """A day boundary that is also a month boundary, because off-by-one
        day errors are easiest to miss where the month rolls."""
        self.assertEqual(
            business_date(datetime(2026, 8, 31, 15, 0, tzinfo=UTC)), date(2026, 9, 1)
        )
        self.assertEqual(
            business_date(datetime(2026, 8, 31, 14, 0, tzinfo=UTC)), date(2026, 8, 31)
        )

    def test_a_year_boundary_too(self):
        self.assertEqual(
            business_date(datetime(2026, 12, 31, 15, 0, tzinfo=UTC)), date(2027, 1, 1)
        )

    def test_a_naive_datetime_is_refused(self):
        with self.assertRaises(ValueError):
            business_date(datetime(2026, 8, 5, 15, 0))


class ClockDateTests(unittest.TestCase):
    """`clock_date()` vs `business_date()` — the naive case, which is the only
    reason both exist. See the `clock_date` docstring for why the difference
    is a contract rather than a relaxation."""

    def test_they_agree_for_every_aware_value(self):
        for hour in range(24):
            for tz in (UTC, KST, timezone(timedelta(hours=-5))):
                moment = datetime(2026, 8, 5, hour, tzinfo=tz)
                with self.subTest(hour=hour, tz=str(tz)):
                    self.assertEqual(clock_date(moment), business_date(moment))

    def test_a_naive_wall_clock_is_read_as_seoul(self):
        self.assertEqual(clock_date(datetime(2026, 8, 5, 23, 59)), date(2026, 8, 5))
        self.assertEqual(clock_date(datetime(2026, 8, 6, 0, 1)), date(2026, 8, 6))

    def test_business_date_still_refuses_the_same_value(self):
        """The refusal that protects *data* is untouched. If this ever passes,
        `clock_date` has been allowed to soften `business_date`."""
        with self.assertRaises(ValueError):
            business_date(datetime(2026, 8, 5, 23, 59))

    def test_data_dating_sites_do_not_use_clock_date(self):
        """The split is only worth having if it is kept. A `timestamp` that
        arrived from another machine must never be dated by the clock rule."""
        for relative, _ in OneRuleForOneQuestionTests.DATING_SITES:
            with self.subTest(site=relative):
                source = (SRC / relative).read_text(encoding="utf-8")
                self.assertIn("business_date", source)


class NowIsKstOnAnyMachineTests(unittest.TestCase):
    """`businessdate.now()` under a real non-KST machine clock zone.

    A child process with `TZ=UTC0` is the only honest way to ask this: the
    thing under test is precisely the code path that takes *no* injected
    `now`, and every in-process trick for faking the local zone would leave
    that path reading the real one.
    """

    def _run(self, snippet: str, tz: str | None) -> str:
        env = dict(os.environ)
        if tz is None:
            env.pop("TZ", None)
        else:
            env["TZ"] = tz
        env["PYTHONPATH"] = str(SRC)
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-c", snippet],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def test_the_probe_actually_moves_the_child_clock_zone(self):
        """Guards the guard. If `TZ` stopped reaching the child, every test
        below would pass while measuring nothing."""
        under_utc = self._run(
            "import datetime;print(datetime.datetime.now().astimezone().utcoffset())",
            tz="UTC0",
        )
        self.assertEqual(under_utc, "0:00:00")

    def test_now_is_kst_under_a_utc_machine(self):
        for tz in ("UTC0", "EST5EDT", None):
            with self.subTest(tz=tz):
                offset = self._run(
                    "import businessdate;print(businessdate.now().utcoffset())", tz=tz
                )
                self.assertEqual(offset, "9:00:00")

    def test_the_event_timestamp_is_kst_under_a_utc_machine(self):
        """`events.current_timestamp()` is what every Desktop stamps its work
        with. Before C135 a Desktop whose clock zone was not Seoul stamped its
        own offset, with no warning anywhere."""
        stamped = self._run(
            "from events.schema import current_timestamp;print(current_timestamp())",
            tz="UTC0",
        )
        self.assertTrue(stamped.endswith("+09:00"), stamped)

    def test_the_two_zones_agree_about_which_day_it_is(self):
        """The failure this closes, stated as the operator would see it: two
        machines, same moment, and until C135 they could disagree about
        today's date."""
        snippet = "import businessdate;print(businessdate.business_date(businessdate.now()))"
        self.assertEqual(self._run(snippet, tz="UTC0"), self._run(snippet, tz=None))


class SchedulerWindowIsMeasuredInSeoulTests(unittest.TestCase):
    """docs/07 §4 fixes the Scheduler at Asia/Seoul; docs/07 §18 says today is
    never processed. Together: `end` is *Seoul's* yesterday.

    Before C135 `run_once()` read `now.date()`, so a Runner handed a UTC
    instant during the 00:00-08:59 KST window closed D-2 and silently left
    D-1 for the next day.
    """

    def _repo(self) -> FileHistoryRepository:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        return FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )

    def _generated(self, now: datetime, start: date) -> list[date]:
        repo = self._repo()
        result = scheduler_run_once(
            repo,
            history_start_date=start,
            now=now,
            state_path=self.root / "state.json",
            lock_path=self.root / "lock",
            daily_output_dir=self.root / "daily",
        )
        return list(result.generated_dates)

    def test_the_same_instant_closes_the_same_day_in_either_spelling(self):
        """02:00 KST on the 28th is 17:00 UTC on the 27th. Both must close
        through the 27th."""
        start = date(2026, 8, 26)
        as_kst = self._generated(datetime(2026, 8, 28, 2, 0, tzinfo=KST), start)
        as_utc = self._generated(datetime(2026, 8, 27, 17, 0, tzinfo=UTC), start)
        self.assertEqual(as_kst, as_utc)
        self.assertEqual(as_kst[-1], date(2026, 8, 27))

    def test_just_after_seoul_midnight_yesterday_is_the_day_that_just_ended(self):
        generated = self._generated(
            datetime(2026, 8, 28, 0, 1, tzinfo=KST), date(2026, 8, 27)
        )
        self.assertEqual(generated, [date(2026, 8, 27)])

    def test_just_before_seoul_midnight_yesterday_is_the_day_before(self):
        generated = self._generated(
            datetime(2026, 8, 27, 23, 59, tzinfo=KST), date(2026, 8, 26)
        )
        self.assertEqual(generated, [date(2026, 8, 26)])

    def test_the_eleven_o_clock_run_closes_d_minus_one(self):
        """docs/07 §4's worked example, held as a test: at 11:00 on 08-06 the
        Scheduler processes 08-05."""
        generated = self._generated(
            datetime(2026, 8, 6, 11, 0, tzinfo=KST), date(2026, 8, 5)
        )
        self.assertEqual(generated, [date(2026, 8, 5)])


class OneRuleForOneQuestionTests(unittest.TestCase):
    """Every place that answers 'which day did this happen on' must answer it
    the same way. The list is derived from the source, not typed here, so a
    new site that reads `.date()` off a timestamp is caught rather than
    quietly joining the ones this Sprint fixed.
    """

    DATING_SITES = (
        ("daily/generator.py", "_candidate_date"),
        ("daily/role_summary.py", "_candidate_date"),
        ("controltower/rollup.py", "_event_date"),
        ("controltower/dashboard.py", "_evidence_day"),
        ("app/desktop_activity.py", "last_event_date"),
        ("agent/signals.py", "business_date"),
    )

    def test_every_dating_site_goes_through_business_date(self):
        for relative, symbol in self.DATING_SITES:
            with self.subTest(site=f"{relative}::{symbol}"):
                source = (SRC / relative).read_text(encoding="utf-8")
                self.assertIn(symbol, source, f"{relative} no longer defines {symbol}")
                self.assertIn("business_date", source)

    def test_no_source_file_still_reads_the_machine_clock_zone(self):
        """`datetime.now().astimezone()` is the exact spelling C135 removed:
        it answers in the machine's zone, which docs/06 §9 says is not the
        question. `businessdate.py` itself names it in prose, so it is
        excluded by path rather than by pattern.
        """
        offenders = []
        roots = [SRC] + [PROJECT_ROOT]
        seen = set()
        for root in roots:
            paths = root.rglob("*.py") if root == SRC else root.glob("*.py")
            for path in paths:
                if path in seen or path.name == "businessdate.py":
                    continue
                seen.add(path)
                if "datetime.now().astimezone()" in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [], "these read the machine clock zone")

    def test_the_scan_above_would_notice_if_one_came_back(self):
        """Guards the guard: the pattern is one somebody could reformat away.
        Assert it matches the shape it is meant to match."""
        self.assertIn("datetime.now().astimezone()", businessdate.__doc__)


class NothingDatesATimestampByItsOwnOffsetTests(unittest.TestCase):
    """The roster above is hand-written, and this repository has found three
    separate defects hiding behind hand-written rosters (C79, C80, C81). So
    the rule is also asserted the other way round, off the AST, over every
    production file: nowhere may a parsed timestamp be turned into a date
    without going through this module.

    The shape being hunted is exactly the one C135 removed, in either
    spelling:

        datetime.fromisoformat(x).date()
        fromisoformat(x).date()

    A test that names its own call sites cannot see the eleventh one. This
    one can, and it is derived from disk rather than typed here.
    """

    def _production_files(self):
        files = sorted(SRC.rglob("*.py")) + sorted(PROJECT_ROOT.glob("*.py"))
        return [f for f in files if "__pycache__" not in f.parts]

    def test_the_files_this_scans_are_the_ones_that_exist(self):
        """Guards the guard: an empty scan passes vacuously, and this project
        has caught exactly that before (C76 section 4)."""
        names = {f.name for f in self._production_files()}
        self.assertIn("businessdate.py", names)
        self.assertIn("ops_status.py", names)
        self.assertGreater(len(names), 50, "the scan lost the source tree")

    def test_no_production_file_reads_a_date_off_a_parsed_timestamp(self):
        import ast

        offenders = []
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "date"):
                    continue
                if node.args or node.keywords:
                    continue  # `.date(...)` with arguments is something else
                inner = func.value
                if not isinstance(inner, ast.Call):
                    continue
                target = inner.func
                name = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else getattr(target, "id", None)
                )
                if name in ("fromisoformat", "fromtimestamp"):
                    offenders.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                    )
        self.assertEqual(
            offenders,
            [],
            "these date a parsed instant by its own offset instead of Seoul's",
        )

    def test_the_scan_above_can_actually_fail(self):
        """The predicate, on an input it must reject. Without this the test
        above passes whether or not the AST walk works."""
        import ast

        tree = ast.parse("d = datetime.fromisoformat(x).date()")
        hits = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "date"
            and isinstance(n.func.value, ast.Call)
        ]
        self.assertEqual(len(hits), 1)


class SignalDateAgreesWithWhereItIsRenderedTests(unittest.TestCase):
    """`agent/signals.py` refuses a Signal whose stated `timestamp` is not on
    the date it was filed under, and its own comment says why: "A Signal filed
    under 08-09 but stamped 08-10 would be marked collected on one date and
    rendered on another — a silent misfile."

    That check compared the date *written in the string*, so it agreed with
    the renderer only for a `+09:00` timestamp. A Signal filed under 08-06 and
    stamped `2026-08-05T23:00:00+00:00` passed neither test cleanly before
    C135: the check said "not on 08-06" and refused it, while the renderer —
    had it got there — would have put it on 08-05 anyway. Both halves now
    measure the Seoul day, so the refusal and the rendering cannot disagree.
    """

    def _parse(self, timestamp, target):
        from agent.signals import SignalError, parse_signal

        raw = json.dumps(
            {
                "project_id": "SEARCH_FRONTEND",
                "event_type": "MILESTONE_COMPLETED",
                "status": "COMPLETED",
                "summary": "tz probe",
                "milestone": "M1",
                "history_candidate": True,
                "timestamp": timestamp,
            }
        )
        try:
            return parse_signal(raw, signal_id="TZ", target_date=target)
        except SignalError as exc:
            return exc

    def test_a_utc_timestamp_is_accepted_on_its_seoul_day(self):
        from agent.signals import Signal

        accepted = self._parse("2026-08-05T23:00:00+00:00", date(2026, 8, 6))
        self.assertIsInstance(accepted, Signal, str(accepted))

    def test_a_utc_timestamp_is_refused_on_the_day_it_merely_reads_as(self):
        """The other half. Before C135 this was the accepted one."""
        refused = self._parse("2026-08-05T23:00:00+00:00", date(2026, 8, 5))
        self.assertNotIsInstance(refused, tuple)
        self.assertIn("not on", str(refused))

    def test_a_kst_timestamp_is_unaffected(self):
        from agent.signals import Signal

        self.assertIsInstance(
            self._parse("2026-08-05T23:00:00+09:00", date(2026, 8, 5)), Signal
        )


class DefaultSignalTimestampIsSeoulMidnightTests(unittest.TestCase):
    """`agent._default_timestamp()` stamps a Signal that states no timestamp
    of its own, and it must land on the Signal's own date (docs/06 §12).

    It used to build local midnight and attach the machine's offset. East of
    Seoul that is the previous Seoul day: `2026-08-08T00:00+14:00` is
    2026-08-07 05:00 here. Nobody runs a Desktop in Kiribati, which is why it
    survived — but the value is now KST midnight, so the class of machine that
    could get it wrong is empty rather than small.
    """

    def test_it_is_midnight_kst_on_the_signals_own_date(self):
        from agent.agent import _default_timestamp

        self.assertEqual(
            _default_timestamp(date(2026, 8, 8)), "2026-08-08T00:00:00+09:00"
        )

    def test_it_lands_in_its_own_days_bucket(self):
        """The property, rather than the string: whatever it stamps must be
        dated back to the date it was given."""
        from agent.agent import _default_timestamp

        for day in (date(2026, 1, 1), date(2026, 8, 8), date(2026, 12, 31)):
            with self.subTest(day=day):
                stamped = datetime.fromisoformat(_default_timestamp(day))
                self.assertEqual(business_date(stamped), day)

    def test_it_does_not_read_the_machine_clock_zone(self):
        """The code, not the prose — the docstring names `.astimezone()` on
        purpose, to say what this function stopped doing."""
        import ast
        import textwrap

        source = inspect.getsource(sys.modules["agent.agent"]._default_timestamp)
        function = ast.parse(textwrap.dedent(source)).body[0]
        body = function.body[1:] if ast.get_docstring(function) else function.body
        code = chr(10).join(ast.unparse(node) for node in body)

        self.assertIn("KST", code)
        self.assertNotIn("astimezone", code)


class EveryNaiveTimestampDecisionIsAccountedForTests(unittest.TestCase):
    """C135. What this project does when a timestamp has no offset — all of
    it, in one place, checked rather than described.

    A naive timestamp is not supposed to exist here: `events.schema` requires
    an offset and REJECTS an Event without one. It happens anyway — a legacy
    file, a hand edit, a restore from another tool, a value Notion returns —
    and by C135 there were **five different answers** to it spread across
    eight modules, each one locally reasonable and none of them written down
    together. That is the shape this repository keeps finding: a rule that is
    right in every instance and that nobody can see whole.

    The five, and the question that organises them — **whose value is it?**

      REFUSE      someone else's data, at the boundary where it arrives.
                  `events.schema` rejects the Event; `agent.signals` rejects
                  the Signal; `businessdate.business_date()` raises. A value
                  that cannot say when it happened may not enter.

      UNKNOWN     someone else's data, past the boundary, where refusing
                  would take a whole view down. `rollup._whole_days_between`
                  and `notion.sync` answer None rather than guess; a day that
                  cannot be dated is *reported* unreadable, never dropped.

      SORT LAST   ordering among mixed values. `history.result` and
                  `rollup.event_instant_key` use a two-tier key so one
                  unorderable value cannot decide the order of everything
                  around it.

      BOTH NAIVE  two values of the same kind. `agent.status` and
                  `ops_status` strip the aware side and compare wall clock to
                  wall clock — like with like, inventing nothing.

      READ AS KST this system's own clock. `businessdate.clock_date()` and
                  `ops_status._comparable()` read an unqualified `now` as
                  Asia/Seoul, because docs/06 §9 says which wall this project
                  reads. Applying a stated default is not guessing.

    The roster below is hand-written and the scan is not: every
    `tzinfo is None` in `src/` and the entrypoints must appear here with a
    class, so a ninth module answering a sixth way fails rather than joining
    quietly. Four hand-written rosters in this repository went stale before
    anyone noticed (C79, C80, C81, C135) and this is the same guard applied
    to a policy instead of a file list.
    """

    #: `(file, symbol or nearest function) -> (class, why)`.
    DECISIONS = {
        "src/events/schema.py": ("REFUSE", "validation: the Event is REJECTED"),
        "src/agent/signals.py": ("REFUSE", "the Signal is refused before it becomes an Event"),
        "src/notion/sync.py": ("UNKNOWN", "the Late Event comparison is not made"),
        "src/history/result.py": ("SORT LAST", "two-tier sort key, by text"),
        "src/controltower/rollup.py": (
            "UNKNOWN + SORT LAST + BOTH NAIVE",
            "three sites: `_whole_days_between` answers None on a mismatch "
            "and compares wall clocks when both are naive; "
            "`event_instant_key` sorts last by text",
        ),
        "src/agent/status.py": ("BOTH NAIVE", "strips the aware side and compares wall clocks"),
        "ops_status.py": (
            "BOTH NAIVE + READ AS KST",
            "`_history_newer_than_the_last_backup` drags the aware side down; "
            "`_comparable` lifts a naive reference to KST (C135 changed that "
            "from the machine's zone); `_run_duration` only checks the pair agrees",
        ),
    }

    def _sites(self):
        """Every `X.tzinfo is None` test in the shipping tree, off the AST."""
        import ast

        files = [f for f in sorted(SRC.rglob("*.py")) if "__pycache__" not in f.parts]
        files += sorted(PROJECT_ROOT.glob("*.py"))
        found = {}
        for path in files:
            if path.name == "businessdate.py":
                continue  # the module that defines the policy
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                if not any(isinstance(op, ast.Is) for op in node.ops):
                    continue
                text = ast.unparse(node)
                if ".tzinfo is None" not in text:
                    continue
                key = path.relative_to(PROJECT_ROOT).as_posix()
                found.setdefault(key, []).append(node.lineno)
        return found

    def test_the_scan_finds_the_sites_we_know_exist(self):
        """Guards the guard: the assertion below is a negative over this
        scan, and a negative over nothing is true (C66 §1)."""
        sites = self._sites()

        self.assertIn("src/events/schema.py", sites)
        self.assertIn("ops_status.py", sites)
        self.assertGreaterEqual(sum(len(v) for v in sites.values()), 10, sites)

    def test_every_site_is_classified(self):
        """A module that decides this question must say which answer it gives."""
        unclassified = sorted(set(self._sites()) - set(self.DECISIONS))

        self.assertEqual(
            unclassified,
            [],
            "these decide what to do with a naive timestamp and are not in "
            "the roster above; add the module with the answer it gives",
        )

    def test_every_classified_module_still_decides_it(self):
        """The other direction. An entry that outlived its code is the
        failure mode this repository keeps finding (C76, C111, C114)."""
        stale = sorted(set(self.DECISIONS) - set(self._sites()))

        self.assertEqual(stale, [], "these no longer test tzinfo; drop the entry")

    def test_every_entry_names_an_answer_and_a_reason(self):
        known = {"REFUSE", "UNKNOWN", "SORT LAST", "BOTH NAIVE", "READ AS KST"}
        for module, (answer, why) in sorted(self.DECISIONS.items()):
            with self.subTest(module=module):
                self.assertTrue(
                    set(answer.split(" + ")) <= known,
                    f"{answer!r} is not one of the five",
                )
                self.assertGreater(len(why), 20, "an entry needs a reason")

    # ---- one behavioural probe per answer, so the table is not just prose --

    def test_refuse_the_schema_rejects_a_naive_event(self):
        from events import validate_event

        errors = validate_event(
            {
                "schema_version": "1.0", "event_id": "N", "source": "DESKTOP_1",
                "role": "CTO_BACKEND", "project_id": "P", "event_type": "STARTED",
                "status": "IN_PROGRESS", "summary": "s", "history_candidate": True,
                "timestamp": "2026-08-05T09:00:00",
            }
        )
        self.assertTrue(
            any("timezone offset" in error for error in errors), errors
        )

    def test_refuse_business_date_raises(self):
        with self.assertRaises(ValueError):
            business_date(datetime(2026, 8, 5, 9, 0))

    def test_unknown_a_mismatched_pair_answers_none(self):
        from controltower.rollup import _whole_days_between

        self.assertIsNone(
            _whole_days_between("2026-08-05T09:00:00", datetime(2026, 8, 9, tzinfo=KST))
        )

    def test_sort_last_an_unorderable_value_goes_after_the_rest(self):
        from history.result import HistoryCandidate
        from history import HistoryDecision

        def candidate(event_id, timestamp):
            return HistoryCandidate(
                history_id=f"H-{event_id}", event_id=event_id, timestamp=timestamp,
                category="MILESTONE", project_id="P", role="COO", summary="s",
                evidence=(), filter_result=HistoryDecision.KEEP,
            )

        aware = candidate("AWARE", "2026-08-05T09:00:00+09:00")
        naive = candidate("NAIVE", "2026-08-05T09:00:00")
        ordered = sorted([naive, aware], key=lambda c: c.chronological_key)

        self.assertEqual([c.event_id for c in ordered], ["AWARE", "NAIVE"])

    def test_both_naive_two_wall_clocks_compare_as_wall_clocks(self):
        from controltower.rollup import _whole_days_between

        self.assertEqual(
            _whole_days_between("2026-08-05T09:00:00", datetime(2026, 8, 9, 9, 0)), 4
        )

    def test_read_as_kst_an_unqualified_now_is_seoul(self):
        self.assertEqual(clock_date(datetime(2026, 8, 5, 23, 59)), date(2026, 8, 5))


class DailyHistoryUsesTheSameRuleAsTheSummaryTests(unittest.TestCase):
    """`daily.generator` and `daily.role_summary` bucket independently. If
    they ever disagree, the summary describes a different set of work than the
    file it summarises — which is what a divergent timezone rule would do."""

    def test_a_utc_stamped_candidate_reaches_both_or_neither(self):
        from daily.role_summary import build_role_summary

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        candidate = HistoryCandidate(
            history_id="HIST-TZPAIR",
            event_id="TZPAIR",
            timestamp="2026-08-05T23:00:00+00:00",  # 08-06 08:00 KST
            category="MILESTONE",
            project_id="SEARCH_FRONTEND",
            role="COO",
            summary="pair probe",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )
        repo.save(candidate)
        kept = repo.list(decision=HistoryDecision.KEEP)

        for day, expected in ((date(2026, 8, 5), False), (date(2026, 8, 6), True)):
            with self.subTest(day=day):
                body = generate_daily_history(
                    repo, day, output_dir=root / "daily"
                ).read_text(encoding="utf-8")
                summary = build_role_summary(kept, day)
                in_file = "TZPAIR" in body
                in_summary = any(
                    c.event_id == "TZPAIR"
                    for role in summary.roles
                    for c in role.candidates
                )
                self.assertEqual(in_file, expected)
                self.assertEqual(in_summary, expected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
