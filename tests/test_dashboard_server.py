"""Control Tower Dashboard page tests (C77).

`dashboard_server.py` is the third consumer of the Dashboard Model and the
only one a person looks at with their eyes. It derives nothing, so almost
nothing here is about arithmetic; what it can get wrong is **saying a true
field in a way that answers a question nobody asked**, and that is what these
classes are about.

The defect this file was written around
---------------------------------------
Found on the screen, not in a test, during C76's fault injection. With an
empty `processed/`, `coverage.complete` is legitimately `True` — no file
failed to be read, Company History was checked, there is no gap — and the
page rendered that as the green

    이 화면의 숫자는 증거 전체를 덮는다

over nine zero KPIs and an evidence range of `— ~ —`. Every word of the
banner was true about a field, and the sentence it made was false about the
company: it reads as 아무 일도 없었다 when the fact is 읽을 Event가 없다.

That is the same conversion this project has removed twice one layer down —
`history_checked` (C68: "nobody asked" reported as "asked and fine") and
`PanelStatus.UNSOURCED` (C48: "no source" rendered as "nothing happened") —
arriving at the surface where it is finally visible to a person. The model
was right both times. The renderer is a third place it can happen, and until
now the only thing standing between it and an operator was somebody
remembering to look at an empty tree.

The classes
-----------
    NoEvidenceDoesNotRenderAsHealthyTests   the defect above, pinned
    AnUnsourcedPanelIsNotAnEmptyPanelTests  C48's distinction, at the surface
    EveryIncompletenessNamesItsReasonTests  `complete=False` has three causes
                                            and they need different actions
    TheScreenShowsTheModelsOwnNumbersTests  no re-derivation on the way out
    AuthoredTextCannotBecomeMarkupTests     escaping and redaction
    ThePageIsReadOnlyTests                  GET only, and nothing written
    ThePageSurvivesWhatItCannotBuildTests   a broken model must not blank the
                                            operational blocks
    EveryPanelReachesTheScreenTests         guards the guard: a panel or a
                                            column the page cannot name
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dashboard_server  # noqa: E402
import ops_status  # noqa: E402

from controltower import build_company_rollup, build_dashboard  # noqa: E402
from controltower.dashboard import PanelStatus  # noqa: E402
from events import create_event  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=KST)

SOURCE_FOR_ROLE = {
    "CTO_BACKEND": "DESKTOP_1",
    "CMO": "DESKTOP_2",
    "CTO_FRONTEND": "DESKTOP_3",
    "COO": "DESKTOP_4",
}

# The green all-clear. Named once so that a test asserting it is *absent*
# cannot pass by quoting a string the page never contained — the empty-string
# failure that makes a negative assertion decorative.
ALL_CLEAR = "이 화면의 숫자는 증거 전체를 덮는다"

# A credential-shaped string, built by concatenation rather than written out
# — the same idiom, and the same reason, as `test_controltower_dashboard.py`:
# `SecretExposureGuardTests.test_no_secret_material_in_any_tracked_file`
# scans every tracked file for exactly this shape, and it should. A fixture
# and a leaked token are indistinguishable to a scanner, and the scanner is
# the thing that has to stay strict. (Measured: the first draft of this file
# spelled it out and the guard failed the whole suite, which is the guard
# working.)
SECRET = "ntn_" + "B" * 24


class PageTestCase(unittest.TestCase):
    """A real runtime tree, a real model, and the page built from it.

    The page is rendered from `to_payload()` rather than from a hand-written
    dictionary, deliberately: a fixture payload would let this file keep
    passing after the model changed shape, which is the one thing a renderer
    test must not do.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name)
        self.processed = self.runtime / "events" / "processed"
        self.processed.mkdir(parents=True)
        (self.runtime / "local_master" / "daily").mkdir(parents=True)

    def put(self, event_id, *, project="P", role="CTO_BACKEND", day=12, **extra):
        event = create_event(
            source=extra.pop("source", None) or SOURCE_FOR_ROLE[role],
            role=role,
            project_id=project,
            event_type=extra.pop("event_type", "MILESTONE_COMPLETED"),
            status=extra.pop("status", "IN_PROGRESS"),
            summary=extra.pop("summary", None) or f"summary for {event_id}",
            history_candidate=True,
            event_id=event_id,
            timestamp=extra.pop("timestamp", None)
            or f"2026-08-{day:02d}T09:00:00+09:00",
            **extra,
        )
        (self.processed / f"{event_id}.json").write_text(
            event.to_json(), encoding="utf-8"
        )
        return event

    def model(self):
        rollup = build_company_rollup(processed_dir=self.processed, now=NOW)
        return build_dashboard(rollup, now=NOW)

    def payload(self, *, history_gap=None, history_checked=True):
        return self.model().with_history_coverage(
            history_gap, checked=history_checked
        ).to_payload()

    def page(self, *, attention=(), blocks=(), **kwargs):
        """The HTML for this tree.

        `attention` and `blocks` default to empty because most classes here
        are about the model half; the two that are about the ATTENTION banner
        pass their own.
        """
        return dashboard_server.render_html(
            {
                "generated_at": NOW.isoformat(),
                "attention": list(attention),
                "blocks": list(blocks),
                "model": self.payload(**kwargs),
                "model_error": None,
                "build_ms": 12,
                "window": {"since": None, "until": None},
            }
        )


class NoEvidenceDoesNotRenderAsHealthyTests(PageTestCase):
    """An empty `processed/` must not produce a page that reads as 정상.

    `coverage.complete` answers "are there known gaps in what was read", and
    over no files at all the honest answer is genuinely yes — nothing failed.
    The renderer's job is to not turn that into an answer to "does this
    screen show the company", which is the question a person actually brings
    to it.
    """

    def test_the_control_a_tree_with_evidence_does_say_it_is_complete(self):
        """First, so that every assertion below is a difference rather than a
        string that never appears. Without this the whole class would pass
        against a page that lost the banner entirely."""
        self.put("E1")

        page = self.page()

        self.assertIn(ALL_CLEAR, page)
        self.assertEqual(self.payload()["coverage"]["complete"], True)

    def test_an_empty_tree_does_not_get_the_all_clear(self):
        payload = self.payload()

        # The premise: the model really does say complete, and the page still
        # must not say it. Asserted here rather than assumed, because if the
        # model ever stopped saying it this test would be checking nothing.
        self.assertEqual(payload["events_read"], 0)
        self.assertEqual(payload["coverage"]["complete"], True)

        self.assertNotIn(ALL_CLEAR, self.page())

    def test_an_empty_tree_says_the_zeros_are_not_an_answer(self):
        page = self.page()

        self.assertIn("증거가 <b>하나도 없다</b>", page)
        self.assertIn("셀 Event가 없다", page)
        self.assertIn("runtime/events/processed/", page)

    def test_the_completeness_tile_does_not_say_complete_over_nothing(self):
        page = self.page()

        tile = self._tile(page, "완전성")
        self.assertEqual(tile, "증거 없음")
        self.assertIn("cov-item warn", page)

    def test_a_tree_with_evidence_still_reads_complete_on_the_tile(self):
        """The other half of the pair. A fix that made every tile say
        `증거 없음` would satisfy the test above and destroy the field."""
        self.put("E1")

        self.assertEqual(self._tile(self.page(), "완전성"), "완전")

    def test_the_kpi_zeros_are_marked_as_zeros_not_as_values(self):
        """`kpi zero` is what greys the number. A zero in the same weight as
        a live count is a screen that reads as data.

        Not asserted as "no live tile anywhere": an empty tree has one real
        non-zero metric — `teams_silent`, which counts every role as silent
        precisely because nothing was heard. Marking that one grey would be
        the same lie from the other side.
        """
        page = self.page()

        self.assertEqual(page.count("kpi zero"), 8)
        self.assertEqual(page.count("kpi live"), 1)

    @staticmethod
    def _tile(page, label):
        """The value rendered beside one coverage label."""
        match = re.search(
            r"<span class='cov-l'>" + re.escape(label) + r"</span>"
            r"<span class='cov-v'>(.*?)</span>",
            page,
        )
        assert match is not None, f"no coverage tile labelled {label}"
        return match.group(1)


class AnUnsourcedPanelIsNotAnEmptyPanelTests(PageTestCase):
    """C48's distinction, carried to the surface it was made for.

    The model has kept `PanelStatus.UNSOURCED` apart from an empty
    `rows` tuple since C48. That is worth nothing if the page draws them the
    same, and the page is the only place a person ever meets either.
    """

    def _panel(self, page, key):
        """One panel's HTML, from its key marker to the next panel."""
        start = page.index(f"<span class='pkey'>{key}</span>")
        # `<div class='panel ` with the trailing space: `panel-head` is the
        # nearer match and slicing from it drops the very class this helper
        # exists to expose.
        head = page.rindex("<div class='panel ", 0, start)
        end = page.find("<div class='panel ", start)
        return page[head : end if end != -1 else len(page)]

    def test_an_unsourced_panel_says_there_is_no_source(self):
        page = self.page()

        for key in ("COMPANY_GOALS", "SPRINTS", "JUDGEMENTS"):
            with self.subTest(panel=key):
                panel = self._panel(page, key)

                self.assertIn("원천이 없다", panel)
                self.assertIn("UNSOURCED", panel)

    def test_an_unsourced_panel_carries_no_count_at_all(self):
        """The failure mode is not a wrong number; it is any number. `0건`
        on a panel with no source is a measurement of something nobody
        measured."""
        for key in ("COMPANY_GOALS", "SPRINTS", "JUDGEMENTS"):
            with self.subTest(panel=key):
                panel = self._panel(self.page(), key)

                self.assertNotIn("0건", panel)
                self.assertNotIn("<table>", panel)

    def test_an_unsourced_panel_names_the_layers_it_accounts_for(self):
        panel = self._panel(self.page(), "SPRINTS")

        self.assertIn("SPRINT", panel)
        self.assertIn("TASK", panel)

    def test_an_empty_sourced_panel_says_something_different(self):
        """The pair. `RISKS` over a clean tree is empty, not unsourced, and
        the two sentences must not be interchangeable."""
        self.put("E1")
        page = self.page()

        risks = self._panel(page, "RISKS")
        goals = self._panel(page, "COMPANY_GOALS")

        self.assertIn("하나도 없었다", risks)
        self.assertIn("원천은 있다", risks)
        self.assertNotIn("원천이 없다", risks)
        self.assertNotIn("하나도 없었다", goals)

    def test_the_two_are_drawn_differently_and_not_only_worded_differently(self):
        """A reader scanning the page sees colour before they see a
        sentence."""
        self.put("E1")
        page = self.page()

        self.assertIn("panel unsourced", self._panel(page, "COMPANY_GOALS"))
        self.assertNotIn("panel unsourced", self._panel(page, "RISKS"))

    def test_every_unsourced_panel_in_the_model_reaches_the_page(self):
        """Guards the guard: the three keys above are written by hand here,
        and a fourth unsourced panel would be checked by nothing."""
        unsourced = {p.key for p in self.model().unsourced_panels}

        self.assertEqual(unsourced, {"COMPANY_GOALS", "SPRINTS", "JUDGEMENTS"})


class EveryIncompletenessNamesItsReasonTests(PageTestCase):
    """`complete=False` has three causes and they ask for different actions.

    A single "불완전" tells an operator to go and look, without saying where.
    Unreadable files are a file to open; an unchecked Company History is a
    directory to fix; a gap older than the evidence is nothing to fix at all
    (C49: a qualifier, not an alert).
    """

    def test_unreadable_evidence_is_named_and_counted(self):
        self.put("E1")
        (self.processed / "torn.json").write_text('{"schema', encoding="utf-8")

        page = self.page()

        self.assertNotIn(ALL_CLEAR, page)
        self.assertIn("읽지 못한 증거 파일 1건", page)
        self.assertIn("torn.json", page)

    def test_an_unchecked_company_history_says_it_was_not_judged(self):
        self.put("E1")

        page = self.page(history_checked=False)

        self.assertNotIn(ALL_CLEAR, page)
        self.assertIn("확인하지 못했다", page)
        self.assertIn("판정하지 못했다", page)

    def test_history_older_than_the_evidence_names_the_day(self):
        from datetime import date

        self.put("E1")

        page = self.page(history_gap=date(2026, 8, 1))

        self.assertNotIn(ALL_CLEAR, page)
        self.assertIn("2026-08-01", page)
        self.assertIn("이 화면에 없다", page)

    def test_a_duplicate_file_is_reported_without_making_it_incomplete(self):
        """C50's count. Two files, one `event_id`, counted once — the
        pipeline did the right thing and there is nothing to do, so it is
        said out loud and does not turn the banner amber."""
        event = self.put("E1")
        (self.processed / "copy.json").write_text(event.to_json(), encoding="utf-8")

        page = self.page()

        self.assertEqual(self.payload()["coverage"]["duplicates"], 1)
        self.assertIn(ALL_CLEAR, page)
        self.assertIn("중복 파일", page)


class TheScreenAfterADisasterRestoreTests(PageTestCase):
    """C82, Recovery Audit. The one state `Coverage` was written for.

    Backup scope is `daily/` and `monthly/` only (docs/08 section 26), and
    `runtime/events/processed/` is Execution Evidence (docs/14 section 2). So
    a machine restored from the remote gets its whole Company History back
    and **none of its Events**. Every panel then says zero — truthfully —
    about a company that did a great deal, and nothing in the panels can tell
    that apart from a quiet week.

    Two of this page's rules meet here and could have contradicted each
    other: the empty-evidence banner (C76) and the history-gap qualifier
    (C49). Measured on a copy of the deployment tree with `processed/`
    emptied and six Daily files left standing: `events_read` 0,
    `history_uncovered_from` 2026-08-05, `complete` False — and the page
    naming both reasons in one banner.

    No defect was found here. It is gated because nothing tested the pair at
    the surface, and the state is by definition one nobody rehearses.
    """

    def _restored(self):
        """History on disk, evidence gone."""
        daily = self.runtime / "local_master" / "daily"
        (daily / "2026-08-05.md").write_text(
            chr(10).join(["# 2026-08-05", "", "## COMPANY_OPS", "",
                          "- Event ID: RESTORED-1", ""]),
            encoding="utf-8",
        )
        from datetime import date

        return self.page(history_gap=date(2026, 8, 5))

    def test_zero_events_over_surviving_history_is_not_an_all_clear(self):
        page = self._restored()

        self.assertNotIn(ALL_CLEAR, page)
        self.assertEqual(self.payload()["events_read"], 0)

    def test_both_reasons_are_named_not_just_the_first(self):
        """The emptiness explains why every number is 0; the gap explains
        that the zeros are not even the whole of the silence. An operator
        told only one of them draws the wrong conclusion either way."""
        page = self._restored()

        self.assertIn("증거가 <b>하나도 없다</b>", page)
        self.assertIn("증거보다 오래된 Company History", page)
        self.assertIn("2026-08-05", page)

    def test_no_evidence_age_is_invented_when_there_is_no_evidence(self):
        """`마지막 증거 0일 전` over an empty corpus would read as "reported
        today"."""
        page = self._restored()

        self.assertNotIn("마지막 증거", page)

    def test_the_completeness_tile_does_not_say_complete(self):
        page = self._restored()

        self.assertIn("증거 없음", page)


class TheScreenShowsTheModelsOwnNumbersTests(PageTestCase):
    """The reason this file renders `to_payload()` instead of a rollup.

    Two derivations of one view is how a screen and a projection start
    disagreeing about the same day — `controltower/dashboard.py` says so in
    its own docstring, and this page is the third consumer that could have
    started a third derivation.
    """

    def test_every_kpi_value_on_the_page_is_the_models_value(self):
        for index in range(3):
            self.put(f"E{index}", project=f"P{index}", day=10 + index)
        self.put("BLOCKED-1", project="P0", day=14, event_type="BLOCKED",
                 status="BLOCKED", blocker="waiting on approval")

        page = self.page()
        metrics = next(
            p for p in self.payload()["panels"] if p["key"] == "METRICS"
        )

        self.assertTrue(metrics["rows"])
        # The tile strip first, then split on the tile boundary. Matching a
        # closing-tag pair was what broke: C133 moved `derived_from` off the
        # tile, which changed which `</div></div>` came last and silently
        # re-pointed the pattern at a different span. Bounding the strip also
        # stops the final tile's segment running to the end of the document,
        # where an assertion about it could be satisfied by another section.
        strip = re.search(r"<section class='kpis'>(.*?)</section>", page, re.S)
        self.assertIsNotNone(strip)
        tiles = re.split(r"<div class='kpi ", strip.group(1))[1:]
        self.assertEqual(len(tiles), len(metrics["rows"]), tiles)
        for row, tile in zip(metrics["rows"], tiles):
            with self.subTest(metric=row["values"]["key"]):
                value = row["values"]["value"]
                label = row["values"]["label"]

                self.assertIn(f"<span class='kpi-value'>{value}</span>", tile)
                self.assertIn(f"<div class='kpi-label'>{label}</div>", tile)

    def test_a_kpi_with_a_direction_says_which_way_is_good(self):
        """C133. Nine bare counts made the reader supply nine judgements.

        Three of the nine have a direction and get 정상 / 주의. The other six
        are volume -- a quiet week really is a quiet week -- and they say
        참고 in a word rather than being left ambiguous between "healthy"
        and "nobody measured". Painting those amber would have taught an
        operator to ignore amber.
        """
        self.put("E1")

        page = self.page()
        # Scoped to the KPI strip. The NOW tiles carry the same class -- the
        # whole point of that class is that one vocabulary covers the page --
        # so a page-wide count measures both strips and belongs to neither.
        strip = re.search(r"<section class='kpis'>(.*?)</section>", page, re.S)
        self.assertIsNotNone(strip)
        words = re.findall(
            r"<span class='verdict-word (\w+)'>([^<]*)</span>", strip.group(1)
        )
        metrics = next(
            p for p in self.payload()["panels"] if p["key"] == "METRICS"
        )

        self.assertEqual(len(words), len(metrics["rows"]))
        for row, (tone, word) in zip(metrics["rows"], words):
            key = row["values"]["key"]
            with self.subTest(metric=key):
                if key in dashboard_server._KPI_LOWER_IS_BETTER:
                    self.assertIn(word, ("정상", "주의", "판정 불가"))
                    self.assertNotEqual(tone, "info")
                else:
                    self.assertEqual(word, "참고")
                    self.assertEqual(tone, "info")

    def test_both_surfaces_say_the_same_word_for_the_same_state(self):
        """C134. The words existed twice — here and in the Notion renderer —
        and the project's own dead-capability inventory is what caught it:
        `verdict.shape()` was defined and never called, which is what "the
        rule moved and the caller did not" looks like from the outside.

        A reader moving between the browser page and the Notion page must
        not have to learn two vocabularies for one company.
        """
        from controltower import verdict

        self.assertEqual(
            dashboard_server._VERDICTS,
            {tone: (verdict.shape(tone), verdict.word(tone))
             for tone in verdict.STATES},
        )
        # ...and the three states are the three this project uses, so a
        # fourth added upstream has to be decided about rather than
        # appearing silently.
        self.assertEqual(
            sorted(verdict.STATES), ["bad", "info", "ok", "warn"]
        )

    def test_every_state_carries_a_word_a_shape_and_an_emoji(self):
        """Colour is the third carrier, never the only one — and the Notion
        surface has no shapes, so it needs its own mark."""
        from controltower import verdict

        for tone in verdict.STATES:
            with self.subTest(tone=tone):
                self.assertTrue(verdict.word(tone).strip())
                self.assertTrue(verdict.shape(tone).strip())
                self.assertTrue(verdict.emoji(tone).strip())
                self.assertTrue(verdict.colour(tone).endswith("_background"))

    def test_the_directional_metrics_are_the_ones_that_can_be_bad(self):
        """Guards the guard. The set above is written by hand, and a metric
        added upstream would otherwise be silently filed as 참고 forever."""
        self.assertEqual(
            dashboard_server._KPI_LOWER_IS_BETTER,
            frozenset({"open_blockers", "teams_silent", "desktop_role_mismatches"}),
        )

    def test_a_kpi_carries_the_number_of_files_it_was_counted_from(self):
        """`Metric` declares that an untraceable number is a rumour. A tile
        that looked the same citing fourteen files and none would undo the
        declaration on the one surface a person reads."""
        self.put("E1")

        page = self.page()

        self.assertIn("증거 1건", page)
        self.assertIn("증거 파일 없음", page)

    def test_a_row_links_its_evidence_by_file(self):
        self.put("E1")

        page = self.page()

        self.assertIn("E1.json", page)
        self.assertIn("<summary>", page)

    def test_a_truncated_evidence_list_still_reports_the_true_total(self):
        """`EVIDENCE_IN_PAYLOAD` caps the list, never the count. Five of
        forty shown without a word would make `증거 5건` a wrong number
        rather than a short one."""
        from controltower.dashboard import EVIDENCE_IN_PAYLOAD

        total = EVIDENCE_IN_PAYLOAD + 3
        for index in range(total):
            self.put(f"E{index}", day=10, timestamp=f"2026-08-10T09:{index:02d}:00+09:00")

        page = self.page()

        self.assertIn(f"<summary>{total}건</summary>", page)
        self.assertIn(f"총 {total}건 중 {EVIDENCE_IN_PAYLOAD}건만 표시", page)

    def test_a_state_word_is_marked_so_a_reader_can_find_it(self):
        self.put("B1", project="P0", day=14, event_type="BLOCKED",
                 status="BLOCKED", blocker="waiting on approval")

        page = self.page()

        # `assertRegex`, not a literal: C133 added a second class to these
        # cells (`tok`, which stops `overflow-wrap:anywhere` splitting a
        # state word down the middle), and a test that pins the exact
        # attribute string is asserting the class list rather than the
        # property — the mistake `days_silent` already recorded one class up.
        self.assertRegex(page, r"class='state bad[^']*'>BLOCKED")
        self.assertRegex(page, r"class='state bad[^']*'>OPEN_BLOCKER")

    def test_a_short_state_word_is_never_split_down_the_middle(self):
        """C133. `overflow-wrap:anywhere` lets a browser compute a cell's
        min-content as one character, so a fifteen-column table compresses
        until short words break too. Measured at 1440px on a probe tree:
        `IN_PROGRESS` rendered `IN_PROG` / `RESS`, `COMPLETE` as `COMPL` /
        `ETE` — the words a reader scans the table for."""
        self.put("B1", project="P0", day=14, event_type="BLOCKED",
                 status="BLOCKED", blocker="waiting on approval")

        page = self.page()

        self.assertIn("td.state,td.tok{white-space:nowrap}", page)
        # The fixture's own state word carries the class on the real page.
        self.assertRegex(page, r"class='[^']*\btok\b[^']*'>BLOCKED")

    def test_a_long_identifier_still_wraps(self):
        """The pair, and the reason `_TOKEN` is bounded at 24 characters.
        Without the bound this would undo C129: one long id sets the table's
        width and pushes every later column off-screen."""
        long_id = "P" + "X" * 60

        self.assertNotIn("tok", dashboard_server._cell("project_id", long_id))
        self.assertIn("tok", dashboard_server._cell("status", "IN_PROGRESS"))

    def test_a_missing_value_is_a_dash_and_never_a_blank(self):
        """Blank reads as "nothing to say". This project spends a great deal
        of effort keeping "no value" apart from "zero"."""
        self.put("E1")

        page = self.page()

        self.assertIn("<span class='nil'>—</span>", page)
        self.assertNotIn("<td>None</td>", page)


class ADerivedNumberIsNotAnUnsourcedOneTests(unittest.TestCase):
    """C135, found by rendering the page against the live tree and reading it.

    The KPI strip said this, every run:

        기록된 Event      16   증거 16건
        움직인 Project     4   증거 파일 없음

    Both numbers come from the same sixteen files. `움직인 Project` is how
    many distinct `project_id`s are among them, and `rollup._roll_metrics()`
    gives it no `evidence` refs **on purpose** — it counts projects, not
    Events, so "one file per counted thing" does not exist, and
    `EveryMetricIsClassifiedByHowItCitesItsFilesTests` records that decision
    with its reasoning. That decision is right and is not what this class
    changes.

    What was wrong is the sentence built on top of it. "Carries no per-item
    refs" and "has no evidence" are different claims, and the renderer
    collapsed them into the second. `Metric`'s docstring says a Control Tower
    number nobody can trace is a rumour; the page was calling a perfectly
    traceable number a rumour, on the one surface a person reads — the exact
    inversion of the defect C134 fixed, which was a number with *no* evidence
    being reported as 정상.

    Three states now, and the third is the fix: counted, derived, or nothing
    to count. A zero keeps `증거 파일 없음`, where it is true.
    """

    def _cite(self, count, value):
        return dashboard_server._kpi_cite(count, value)

    def test_a_counted_number_cites_its_files(self):
        self.assertIn("증거 16건", self._cite(16, 16))

    def test_a_zero_still_says_there_is_nothing_to_cite(self):
        """The state that was always true, kept. Nothing happened, so there
        is no file to point at, and saying so is honest."""
        self.assertIn("증거 파일 없음", self._cite(0, 0))

    def test_a_derived_number_is_not_called_unsourced(self):
        """The defect. A non-zero value with no per-item refs came from
        somewhere, and the tile's own `derived_from` line says where."""
        rendered = self._cite(0, 4)

        self.assertNotIn("증거 파일 없음", rendered)
        self.assertIn("파생", rendered)

    def test_the_live_shape_no_longer_calls_projects_active_unsourced(self):
        """End to end, on a rollup with more Events than projects — the shape
        that produced the measurement above."""
        from controltower import build_company_rollup
        from controltower.dashboard import build_dashboard
        from events import Event

        events = []
        for index, project in enumerate(("OPS", "SEARCH", "PAY", "WEB")):
            events.append(
                (
                    Event(
                        schema_version="1.0", event_id=f"D{index}",
                        source="DESKTOP_4", role="COO", project_id=project,
                        event_type="MILESTONE_COMPLETED", status="COMPLETED",
                        summary="s", history_candidate=True, milestone=f"M{index}",
                        timestamp=f"2026-08-0{index + 1}T09:00:00+09:00",
                    ),
                    f"D{index}.json",
                )
            )
        model = build_dashboard(build_company_rollup(now=NOW, events=events), now=NOW)
        metrics = next(p for p in model.panels if p.key == "METRICS")

        active = next(r for r in metrics.rows if r.values["key"] == "projects_active")
        self.assertEqual(active.values["value"], 4)
        self.assertEqual(active.values["evidence_count"], 0)

        rendered = self._cite(
            active.values["evidence_count"], active.values["value"]
        )
        self.assertNotIn("증거 파일 없음", rendered)

    def test_both_surfaces_say_the_same_thing_about_the_same_number(self):
        """A COO reads Notion and an operator reads the browser. C110's whole
        lesson is that one word meaning two things on two surfaces is how
        they start disagreeing, and C133/C134 rebuilt both screens to keep
        them in step. So the two clauses are compared rather than promised.
        """
        from controltower.notion_page import _metric_cite

        for count, value in ((16, 16), (0, 0), (0, 4), (1, 1)):
            with self.subTest(count=count, value=value):
                browser = self._cite(count, value)
                notion = _metric_cite(count, value)

                for phrase in ("증거 파일 없음", "파생"):
                    self.assertEqual(
                        phrase in browser,
                        phrase in notion,
                        f"the two surfaces disagree about {phrase!r} for "
                        f"count={count} value={value}: {browser!r} / {notion!r}",
                    )


class AuthoredTextCannotBecomeMarkupTests(PageTestCase):
    """`project_id`, `blocker` and `summary` are strings a person typed on
    another Desktop, and `validate_event()` only type-checks them.

    Two separate properties, both of them about the same fact:

        escaped     the page is HTML now; a `<script>` in a `blocker` is
                    markup unless something stops it
        redacted    `to_payload()` redacts on the way out, and the page must
                    not undo it by reading the model instead
    """

    def test_a_script_in_authored_text_does_not_reach_the_page_as_markup(self):
        self.put(
            "X1",
            project="<script>alert(1)</script>",
            day=14,
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="<img src=x onerror=alert(2)>",
        )

        page = self.page()

        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("<img src=x onerror=alert(2)>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;img src=x", page)

    def test_an_attention_line_is_escaped_too(self):
        page = self.page(attention=["<b>not bold</b>"])

        self.assertNotIn("<b>not bold</b>", page)
        self.assertIn("&lt;b&gt;not bold&lt;/b&gt;", page)

    def test_a_block_of_captured_terminal_output_is_escaped(self):
        page = self.page(
            blocks=[
                {
                    "key": "AGENT",
                    "title": "AGENT",
                    "parity": False,
                    "text": "<script>alert(3)</script>",
                    "attention": 0,
                }
            ]
        )

        self.assertNotIn("<script>alert(3)</script>", page)
        self.assertIn("&lt;script&gt;alert(3)", page)

    def test_a_secret_shaped_blocker_reaches_the_page_redacted(self):
        self.put(
            "X2",
            project="P",
            day=14,
            event_type="BLOCKED",
            status="BLOCKED",
            blocker=f"waiting for NOTION_API_TOKEN={SECRET} rotation",
        )

        page = self.page()

        self.assertNotIn(SECRET, page)
        self.assertIn("REDACTED", page)

    def test_a_newline_in_authored_text_cannot_forge_a_second_row(self):
        """BUG-6's shape. `one_line()` is applied by `to_payload()`; the page
        must not reach around it."""
        self.put("X3", project="P\nFORGED", day=13)

        page = self.page()

        self.assertNotIn("P\nFORGED", page)
        self.assertIn("FORGED", page)


class HowOldTheEvidenceIsSaysItselfTests(PageTestCase):
    """C81, found by reading the running dashboard against real data.

    The deployment tree showed, on 2026-08-25:

        증거 범위   2026-08-05 ~ 2026-08-10
        완전성      완전
        완료된 Milestone   14

    Every word true, and the newest evidence fifteen days old with nothing
    on the numbers saying so. `완료된 Milestone 14` reads as recent progress
    unless the reader subtracts two dates in their head.

    The page already applies this reasoning to itself — the snapshot age
    badge exists because nobody subtracts two timestamps at a glance. That
    badge says when the **page** was built; this says when the **company**
    last reported, and it is the one the KPIs are counts over.

    `SILENT_AFTER_DAYS` is the threshold rather than a new one, because it
    is already this project's answer to "how long is too long without a
    report".
    """

    def test_the_age_of_the_newest_evidence_is_stated_in_days(self):
        self.put("E1", day=12)   # NOW is 2026-08-19

        page = self.page()

        self.assertIn("마지막 증거 7일 전", page)

    def test_fresh_evidence_is_not_flagged(self):
        """The control. A tile that always went amber would say nothing."""
        self.put("E1", day=19)

        page = self.page()

        self.assertIn("마지막 증거 0일 전", page)
        self.assertNotIn("cov-item warn", page)

    def test_stale_evidence_is_flagged_at_the_projects_own_threshold(self):
        self.put("E1", day=12)

        self.assertIn("cov-item warn", self.page())

    def test_the_threshold_is_the_one_the_project_already_uses(self):
        self.assertEqual(ops_status.SILENT_AFTER_DAYS, 3)

        boundary = 19 - ops_status.SILENT_AFTER_DAYS      # exactly at the cap
        self.put("AT-CAP", day=boundary)
        self.assertNotIn("cov-item warn", self.page())

    def test_an_empty_tree_claims_no_age_at_all(self):
        """A number that could not be computed must not be shown as one —
        the same rule `history_checked` follows a layer down."""
        page = self.page()

        self.assertNotIn("마지막 증거", page)

    def test_the_age_is_computed_against_the_snapshot_not_the_wall_clock(self):
        """`generated_at` is what every other age in this model is measured
        against. Reading the clock here would make the page untestable and
        let two numbers on it disagree about what 'now' is."""
        self.put("E1", day=12)

        self.assertIsNone(
            dashboard_server._evidence_age_days(
                {"coverage": {"evidence_to": "2026-08-12"}, "generated_at": "nonsense"}
            )
        )
        self.assertEqual(
            dashboard_server._evidence_age_days(
                {
                    "coverage": {"evidence_to": "2026-08-12"},
                    "generated_at": "2026-08-19T09:00:00+09:00",
                }
            ),
            7,
        )


class TwoDifferentValuesCannotLookLikeOneTests(PageTestCase):
    """C89. An invisible character makes two rows read as the same row.

    `oplog.one_line()` escapes what can end **or reorder** a line, and by
    that contract a zero-width space is correctly out of scope: it ends
    nothing and reorders nothing. On a log line that is right. On a table it
    is not.

    Measured: two Events whose `project_id` differs only by U+200B produced
    two PROJECTS rows — `SEARCH_BACKEND` with 3 Events and `SEARCH_BACKEND`
    with 1. The same name twice, different numbers, nothing saying why. The
    model was correct throughout; only the rendering collapsed them.

    Revealed rather than stripped — `one_line()`'s own rule — so the row
    still names something a person can find in the evidence file.
    """

    ZWSP = "​"

    def test_two_ids_differing_only_by_an_invisible_char_render_differently(self):
        self.put("A1", project="SEARCH_BACKEND", day=12)
        self.put("B1", project="SEARCH" + self.ZWSP + "_BACKEND", day=13)

        page = self.page()
        cells = re.findall(r"<td[^>]*>([^<]*)</td>", page)
        names = {c for c in cells if "SEARCH" in c and "," not in c}

        self.assertEqual(len(names), 2, f"rows collapsed to one name: {names}")
        self.assertIn("SEARCH&lt;U+200B&gt;_BACKEND", page)

    def test_the_model_kept_them_apart_all_along(self):
        """The premise. If the rollup had folded them this would be a
        different defect in a different layer."""
        self.put("A1", project="SEARCH_BACKEND", day=12)
        self.put("B1", project="SEARCH" + self.ZWSP + "_BACKEND", day=13)

        projects = next(
            p for p in self.payload()["panels"] if p["key"] == "PROJECTS"
        )

        self.assertEqual(len(projects["rows"]), 2)

    def test_ordinary_text_is_untouched(self):
        """The control. A transform that rewrote normal values would be
        visible on every row and this class would still pass."""
        self.assertEqual(dashboard_server._reveal_invisible("SEARCH_BACKEND"),
                         "SEARCH_BACKEND")
        self.assertEqual(dashboard_server._reveal_invisible("한글 요약"), "한글 요약")

    def test_the_rule_is_the_unicode_category_not_a_hand_list(self):
        """A hand-written list goes stale the day another format character
        is added. Checked across several, including ones nobody would have
        thought to list."""
        for name, char in (
            ("ZWSP", "​"), ("ZWNJ", "‌"), ("ZWJ", "‍"),
            ("BOM", "﻿"), ("SOFT HYPHEN", "­"),
        ):
            with self.subTest(char=name):
                revealed = dashboard_server._reveal_invisible("A" + char + "B")

                self.assertNotIn(char, revealed)
                self.assertIn(f"<U+{ord(char):04X}>", revealed)

    def test_the_real_value_stays_recoverable(self):
        """Stripped, the row would name something that is not in any file."""
        revealed = dashboard_server._reveal_invisible("SEARCH" + self.ZWSP + "_BACKEND")

        self.assertIn("SEARCH", revealed)
        self.assertIn("_BACKEND", revealed)
        self.assertIn("200B", revealed)


class NoOneFieldCanInflateThePageTests(PageTestCase):
    """C80. `to_payload()` keeps authored values at full length on purpose --
    it is the faithful record, and a Notion projection needs the whole
    string. A rendered page is the other case: it is a thing a person
    downloads.

    Nothing bounds `blocker`, `summary` or `project_id`. Measured before the
    cap, three blocked Projects each carrying a 100,000-character `blocker`:
    the blob reached the page **nine** times -- a RISKS row, a PROJECTS row
    and an ATTENTION line each -- for a 0.89 MB page. One Event with a
    1,000,000-character blocker made a 1.98 MB one. After: 37 KB and 29 KB.
    """

    BLOB = "A" * 100_000

    def _blocked(self, count):
        for index in range(count):
            self.put(
                f"BIG-{index}",
                project=f"P{index}",
                day=14,
                timestamp=f"2026-08-14T09:{index:02d}:00+09:00",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker=self.BLOB,
            )

    def test_the_payload_still_carries_the_whole_value(self):
        """The premise. If the model started truncating, every assertion
        below would pass for the wrong reason -- and the Notion projection
        would silently lose text."""
        self._blocked(1)

        risks = next(p for p in self.payload()["panels"] if p["key"] == "RISKS")

        self.assertEqual(len(risks["rows"][0]["values"]["blocker"]), len(self.BLOB))

    def test_the_page_does_not_carry_it(self):
        self._blocked(3)

        page = self.page()

        self.assertEqual(page.count(self.BLOB), 0)
        self.assertLess(len(page.encode()), 200 * 1024)

    def test_the_cut_is_announced_and_names_the_true_length(self):
        """Silent truncation would make a short value look like the whole
        one -- the same rule `evidence_truncated` follows for a list."""
        self._blocked(1)

        page = self.page()

        self.assertIn(f"앞 {dashboard_server._CELL_CHARS:,}자만 표시", page)
        self.assertIn(f"(총 {len(self.BLOB):,}자)", page)
        self.assertIn("증거 파일에", page)

    def test_an_ordinary_value_is_not_touched(self):
        """The control. A cap that shortened normal cells would be visible
        everywhere and this class would still pass."""
        self.put("B1", project="SEARCH_BACKEND", day=14, event_type="BLOCKED",
                 status="BLOCKED", blocker="waiting on infra capacity approval")

        page = self.page()

        self.assertIn("waiting on infra capacity approval", page)
        self.assertNotIn("자만 표시", page)

    def test_the_cap_is_the_one_the_project_already_chose(self):
        """Not a number invented for this file: `oplog.MAX_LOG_ERROR` is the
        bound `append_line()` applies to the same class of text."""
        from oplog import MAX_LOG_ERROR

        self.assertEqual(dashboard_server._CELL_CHARS, MAX_LOG_ERROR)

    def test_the_attention_lines_are_bounded_at_their_source(self):
        """The third of the nine copies came through ATTENTION, which the
        page does not build -- `ops_status._authored()` does. Bounded there,
        so the terminal and this page are fixed by one change rather than
        two."""
        self.addCleanup(setattr, ops_status, "RUNTIME_DIR", ops_status.RUNTIME_DIR)
        ops_status.RUNTIME_DIR = self.runtime
        self._blocked(3)

        data = dashboard_server.gather(NOW)

        self.assertTrue(data["attention"])
        for line in data["attention"]:
            with self.subTest(line=line[:40]):
                self.assertLess(len(line), 2000)


class TheFleetTableReadsAsAVerdictTests(PageTestCase):
    """C84, Multi-Desktop / Sync Audit.

    The DESKTOPS panel is what a COO reads to answer "is every machine
    reporting". The model was already honest about the three states — a
    Desktop reporting today, one at twenty days, one that has **never** sent
    anything (`events` 0, `last_seen` None, `has_activity` False) — and
    ATTENTION separates "truly quiet" from "work-date old but files arrived
    late".

    What the page did not do was let the table be *scanned*. Measured on a
    mixed fleet: every cell in the same weight, `무응답 일수 20` looking
    exactly like `무응답 일수 1`, so the reader had to hold
    `SILENT_AFTER_DAYS` in their head and compare each row by eye.

    Two columns are marked and no others, for the reason the state colours
    already follow: colouring every number colours none.
    """

    def _fleet(self):
        # reporting today / one day / twenty days; DESKTOP_4 never at all
        for source, role, day in (
            ("DESKTOP_1", "CTO_BACKEND", 19),
            ("DESKTOP_2", "CMO", 18),
            ("DESKTOP_3", "CTO_FRONTEND", 1),
        ):
            self.put(f"E-{source}", role=role, day=day)
        return self.page()

    def _flagged(self, page):
        """Cell values carrying the `warn` verdict.

        Matches the class **list**, not one exact spelling (C129). It used to
        be `<td class='state warn'>`, which is the value a cell had when the
        only thing a cell could carry was a verdict. Numeric cells now also
        get `num` for tabular alignment, and `days_silent` — the column this
        class exists to check — is numeric, so the literal stopped matching
        the very cells it was written for while the marking was still there.

        A test that pins a class string rather than the property it stands
        for fails on presentation and passes on regression; this asks the
        question it means.
        """
        return [
            value
            for classes, value in re.findall(
                r"<td class='([^']*)'>(.*?)</td>", page
            )
            if "warn" in classes.split()
        ]

    def test_a_desktop_past_the_threshold_is_marked(self):
        flagged = self._flagged(self._fleet())

        self.assertIn("18", flagged)      # DESKTOP_3, silent since 2026-08-01

    def test_a_desktop_within_the_threshold_is_not_marked(self):
        flagged = self._flagged(self._fleet())

        self.assertNotIn("0", flagged)    # DESKTOP_1, reported today
        self.assertNotIn("1", flagged)    # DESKTOP_2, yesterday

    def test_a_desktop_that_never_reported_is_marked(self):
        """`활동 아니오` is not the same fact as a large silence count — it
        is the Desktop that has no `last_seen` at all — and it must not
        render as an ordinary cell."""
        page = self._fleet()

        self.assertIn("아니오", self._flagged(page))

    def test_the_threshold_is_the_projects_own(self):
        self.assertIsNone(
            dashboard_server._verdict_class(
                "days_silent", ops_status.SILENT_AFTER_DAYS
            )
        )
        self.assertEqual(
            dashboard_server._verdict_class(
                "days_silent", ops_status.SILENT_AFTER_DAYS + 1
            ),
            "warn",
        )

    def test_no_other_column_is_repainted(self):
        """A number in another column must stay a number. `events` and
        `projects` are counts, not verdicts."""
        for column in ("events", "projects", "days_idle", "days_blocked"):
            with self.subTest(column=column):
                self.assertIsNone(dashboard_server._verdict_class(column, 99))

    def test_a_missing_silence_count_is_not_treated_as_zero(self):
        """`days_silent` is None for a Desktop with no `last_seen`. `None`
        is not a small number."""
        self.assertIsNone(dashboard_server._verdict_class("days_silent", None))

    def test_a_bool_cannot_reach_the_threshold_anyway(self):
        """Recorded rather than guarded. `True` is `1` in Python and the
        mistake is a classic, so the first draft had an `isinstance(value,
        bool)` guard -- and a mutation removing it changed no outcome and
        failed nothing. A bool is 0 or 1, `SILENT_AFTER_DAYS` is 3, and the
        column is `int | None` by contract. This asserts the answer instead
        of keeping a branch nothing can reach."""
        self.assertGreater(ops_status.SILENT_AFTER_DAYS, 1)
        self.assertIsNone(dashboard_server._verdict_class("days_silent", True))
        self.assertIsNone(dashboard_server._verdict_class("days_silent", False))


class ASecondInstanceCannotTakeThePortTests(unittest.TestCase):
    """C83, Production Readiness. The error path that could not fire.

    `main()` catches `OSError` on bind and prints "port already in use, set
    COMPANY_OPS_DASHBOARD_PORT". On Windows -- the platform this project runs
    on -- it never fired, because the stdlib sets `allow_reuse_address = 1`
    on `HTTPServer` and Windows `SO_REUSEADDR` permits binding a port that is
    **actively in use** rather than merely relaxing TIME_WAIT.

    Measured before the fix: a second `python dashboard_server.py` on the
    same port started cleanly and ran. Two servers, one port, connections
    going to whichever the OS chose.

    Why that is worse than untidy: the second instance is the one started
    after an edit, or from another checkout, and `RUNTIME_DIR` is per
    process. A Control Tower that intermittently answers from a stale
    process, with nothing on the page saying which replied, is a screen that
    is not about the tree the reader thinks it is.

    Measured after: `[Errno 10048]`, and `main()` prints the message it was
    always supposed to. Three back-to-back restarts each serving requests
    still start immediately on this platform, so the stated POSIX cost
    (TIME_WAIT) does not show up here.
    """

    def test_the_server_refuses_to_reuse_an_address_in_use(self):
        self.assertFalse(dashboard_server._Server.allow_reuse_address)

    def test_the_stdlib_default_is_the_thing_being_overridden(self):
        """Guards the guard. If the stdlib ever changed its default, the
        override above would be a no-op and this class would still pass --
        so the premise is asserted, not assumed."""
        self.assertTrue(ThreadingHTTPServer.allow_reuse_address)

    def test_a_second_bind_on_the_same_port_is_refused(self):
        first = dashboard_server._Server(
            ("127.0.0.1", 0), dashboard_server._Handler
        )
        self.addCleanup(first.server_close)
        port = first.server_address[1]

        with self.assertRaises(OSError):
            dashboard_server._Server(("127.0.0.1", port), dashboard_server._Handler)

    def test_the_plain_stdlib_server_would_have_allowed_it(self):
        """The measurement this class exists for, run rather than quoted.

        Skipped off Windows: `SO_REUSEADDR` genuinely does mean the POSIX
        thing there, and asserting the defect on a platform that does not
        have it would be asserting a fiction.
        """
        if sys.platform != "win32":
            self.skipTest("SO_REUSEADDR only over-permits on Windows")

        first = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server._Handler)
        self.addCleanup(first.server_close)
        port = first.server_address[1]

        second = ThreadingHTTPServer(("127.0.0.1", port), dashboard_server._Handler)
        self.addCleanup(second.server_close)

        self.assertEqual(second.server_address[1], port)

    def test_main_uses_the_strict_server(self):
        """The override is worthless if `main()` builds a plain one."""
        source = Path(dashboard_server.__file__).read_text(encoding="utf-8")

        self.assertIn('server = _Server(("127.0.0.1", port), _Handler)', source)


class OnlyALoopbackNameGetsAnAnswerTests(PageTestCase):
    """C82, Security Audit. Binding to 127.0.0.1 is half of the protection.

    Binding stops a packet from another machine. It does not stop a browser
    on this one. DNS rebinding is the gap: a page the operator visits makes
    `attacker.example` resolve to 127.0.0.1, fetches
    `http://attacker.example:8765/api/dashboard.json`, and the browser calls
    that same-origin — so the attacker's script reads the body. The body is
    this company's internal state: `project_id`s, the `blocker` sentences
    people typed on other Desktops, Desktop names, evidence filenames.

    Measured before the check: `Host: evil.example.com` returned 200 and the
    whole 51 KB page.

    Two halves are asserted here, and the second is the one a naive fix
    fails: `127.0.0.1.evil.com` is an attacker-controlled domain that merely
    *starts with* a loopback address.
    """

    def setUp(self):
        super().setUp()
        self.put("E1")
        self.addCleanup(setattr, ops_status, "RUNTIME_DIR", ops_status.RUNTIME_DIR)
        ops_status.RUNTIME_DIR = self.runtime
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), dashboard_server._Handler
        )
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _get(self, host, path="/"):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if host is not None:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, (error.read() or b"").decode("utf-8")

    def test_a_loopback_name_is_served(self):
        """The control. A check that refused everything would pass every
        assertion below and break the tool."""
        for host in (
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
            f"[::1]:{self.port}",
            f"LOCALHOST:{self.port}",
            "127.0.0.1",
        ):
            with self.subTest(host=host):
                status, body = self._get(host)

                self.assertEqual(status, 200)
                self.assertIn("DOJOONPASS Control Tower", body)

    def test_a_foreign_name_gets_nothing(self):
        for host in ("evil.example.com", "attacker.test", "internal.corp"):
            with self.subTest(host=host):
                status, body = self._get(host)

                self.assertEqual(status, 403)
                self.assertNotIn("DOJOONPASS", body)

    def test_a_name_that_merely_starts_with_a_loopback_address_is_refused(self):
        """`127.0.0.1.evil.com` is a domain the attacker owns. A `startswith`
        check — the obvious wrong fix — hands them the page."""
        status, body = self._get("127.0.0.1.evil.com")

        self.assertEqual(status, 403)
        self.assertNotIn("DOJOONPASS", body)

    def test_the_json_endpoint_is_refused_too(self):
        """It is the endpoint an attacker would actually want: the same
        facts, already parsed."""
        status, body = self._get("evil.example.com", "/api/dashboard.json")

        self.assertEqual(status, 403)
        self.assertNotIn("events_read", body)

    def test_the_refusal_names_no_internal_state(self):
        _status, body = self._get("evil.example.com")

        self.assertNotIn("DESKTOP", body)
        self.assertLess(len(body.encode()), 200)

    def test_a_request_without_a_host_header_still_works(self):
        """HTTP/1.0 clients omit it, and an absent header cannot carry an
        attacker's name — the browser sets `Host` from the URL and script
        cannot override it."""
        self.assertTrue(dashboard_server._Handler._host_allowed.__doc__)

        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        connection.putrequest("GET", "/healthz", skip_host=True)
        connection.endheaders()
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()

        self.assertEqual(status, 200)


class ThePageIsReadOnlyTests(PageTestCase):
    """The tool an operator opens *because* something already looks wrong.

    Served over HTTP, so "it only reads" has to be true of the handler and
    not only of the functions under it.
    """

    def setUp(self):
        super().setUp()
        self.put("E1")
        # The handler builds from `ops_status.RUNTIME_DIR`, which that module
        # documents as rebindable for exactly this.
        self.addCleanup(setattr, ops_status, "RUNTIME_DIR", ops_status.RUNTIME_DIR)
        ops_status.RUNTIME_DIR = self.runtime

        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), dashboard_server._Handler
        )
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=30) as response:
            return response.status, response.read().decode("utf-8")

    def _raw_request(self, method, path="/"):
        """One request, one socket, and the bytes exactly as they arrived.

        `urllib` is the right tool everywhere else in this class, and the
        wrong one for any assertion about whether a body was *sent*: it
        applies HTTP's own rules on the client side and hides a HEAD body
        that a broken server did send.
        """
        import socket

        host, _, port = self.url.rpartition(":")
        connection = socket.create_connection(("127.0.0.1", int(port)), timeout=30)
        try:
            connection.sendall(
                f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode("ascii")
            )
            received = b""
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                received += chunk
        finally:
            connection.close()
        head, _, body = received.partition(b"\r\n\r\n")
        return head, body

    def test_the_page_is_served(self):
        status, body = self._get("/")

        self.assertEqual(status, 200)
        self.assertIn("DOJOONPASS Control Tower", body)
        self.assertIn("데이터 Coverage", body)

    def test_the_payload_is_served_as_json(self):
        status, body = self._get("/api/dashboard.json")

        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["model"]["events_read"], 1)

    def test_every_writing_method_is_refused(self):
        """The module docstring's claim, not the handler's roster.

        This list used to be exactly `("POST", "PUT", "DELETE", "PATCH")` —
        the same four names the handler aliased — so the test asserted the
        roster it was handed rather than the invariant above it, and passed
        while three methods did something else. Measured against the running
        server: HEAD, OPTIONS and TRACE all answered `501 Unsupported
        method`, because `BaseHTTPRequestHandler` handles a `do_*` it cannot
        find before this class ever sees the request.

        `FROBNICATE` is in the list on purpose. It is not a method anyone
        will send; it is the one entry a future roster cannot be quietly
        extended to cover, so it fails if the refusal ever stops being
        structural.
        """
        for method in (
            "HEAD", "POST", "PUT", "DELETE", "PATCH",
            "OPTIONS", "TRACE", "FROBNICATE",
        ):
            with self.subTest(method=method):
                request = urllib.request.Request(self.url + "/", method=method)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=30)

                self.assertEqual(
                    caught.exception.code,
                    405,
                    f"{method} was not refused the way the module docstring "
                    f"says every non-GET method is",
                )

    def test_a_head_request_answers_without_a_body(self):
        """`curl -I <url>` is how anyone checks a local server is up.

        It used to answer `501 Unsupported method ('HEAD')`, which reads as
        "this program is broken" rather than "this program is read-only".
        It now answers 405 — and a 405 that carries a body would be a
        response contradicting its own status line (RFC 9110 §9.3.2: a HEAD
        response has no content), so both halves are pinned here.

        Read off a raw socket rather than through `urllib`, and that is not
        fussiness: `urllib` discards the body of a HEAD response on the
        client side, so an assertion made through it passes whether or not
        the server sent one. Measured — deleting the server's suppression
        left this test green until it was rewritten this way. The bytes on
        the wire are the only witness to what the server actually wrote.
        """
        head, body = self._raw_request("HEAD")

        self.assertIn(b"405", head.splitlines()[0])
        self.assertEqual(body, b"")
        self.assertIn(b"Cache-Control: no-store", head)

        # `Content-Length` describes the **405 representation**, not a GET
        # (C121). The comment here used to say the opposite, one line above
        # this very assertion — `Content-Length: 9` is `len(b"read-only")`,
        # while a GET of the same path returns tens of kilobytes. Both facts
        # are asserted now, so the two can never be confused again by
        # reading only one of them.
        self.assertIn(b"Content-Length: 9", head)
        self.assertEqual(len(b"read-only"), 9)

        get_head, get_body = self._raw_request("GET")
        self.assertIn(b"200", get_head.splitlines()[0])
        self.assertGreater(
            len(get_body),
            1000,
            "the GET this compares against returned almost nothing, so the "
            "contrast below proves nothing",
        )
        self.assertNotIn(
            f"Content-Length: {len(get_body)}".encode("ascii"),
            head,
            "the HEAD refusal advertised the GET's size — that is the claim "
            "the old comment made and the server does not make",
        )

    def test_a_refusal_that_is_not_head_still_carries_its_body(self):
        """The control for the test above: the suppression must be keyed on
        the method, not applied to every 405."""
        head, body = self._raw_request("POST")

        self.assertIn(b"405", head.splitlines()[0])
        self.assertEqual(body, b"read-only")

    def test_a_get_still_carries_its_body(self):
        """The HEAD suppression is keyed on the method, and a bug there
        would silently empty the page rather than fail anything above."""
        status, body = self._get("/")

        self.assertEqual(status, 200)
        self.assertIn("DOJOONPASS Control Tower", body)
        self.assertGreater(len(body), 1000)

    def test_the_handler_does_not_swallow_real_attribute_errors(self):
        """`__getattr__` answers `do_*` and nothing else. A catch-all would
        turn a typo anywhere in this class into a silent 405."""
        handler = dashboard_server._Handler.__new__(dashboard_server._Handler)

        self.assertTrue(callable(handler.do_ANYTHING))
        with self.assertRaises(AttributeError):
            handler.definitely_not_a_handler_method

    def test_an_unknown_path_is_not_the_dashboard(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._get("/anything-else")

        self.assertEqual(caught.exception.code, 404)

    def test_the_page_is_never_cached(self):
        """A cached Control Tower is one showing yesterday, which is worse
        than showing nothing."""
        with urllib.request.urlopen(self.url + "/", timeout=30) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_serving_the_page_changes_nothing_on_disk(self):
        before = self._snapshot()

        self._get("/")
        self._get("/api/dashboard.json")

        self.assertEqual(self._snapshot(), before)

    def _snapshot(self):
        return {
            str(path.relative_to(self.runtime)): path.stat().st_mtime_ns
            for path in sorted(self.runtime.rglob("*"))
            if path.is_file()
        }


class ThePageSurvivesWhatItCannotBuildTests(PageTestCase):
    """The day the Control Tower model raises is a day an operator still
    needs LAST RUN and AGENT.

    A page that shows nothing because one part could not be built is a page
    that hides the parts that worked.
    """

    def test_a_failed_model_does_not_blank_the_operational_blocks(self):
        page = dashboard_server.render_html(
            {
                "generated_at": NOW.isoformat(),
                "attention": ["agent has not run"],
                "blocks": [
                    {
                        "key": "AGENT",
                        "title": "AGENT — 이 머신의 Agent",
                        "parity": False,
                        "text": "last_run : 2026-08-11",
                        "attention": 1,
                    }
                ],
                "model": None,
                "model_error": "ZeroDivisionError: division by zero",
            }
        )

        self.assertIn("Control Tower Model을 만들지 못했다", page)
        self.assertIn("ZeroDivisionError", page)
        self.assertIn("last_run : 2026-08-11", page)
        self.assertIn("agent has not run", page)

    def test_a_block_that_could_not_be_read_is_reported_by_gather(self):
        """`gather()` runs every block through `ops_status._block()`, which
        is what turns an unreadable tree into a reported section instead of
        a traceback. Inherited, not re-implemented — so this checks that it
        really is inherited."""
        self.addCleanup(setattr, ops_status, "RUNTIME_DIR", ops_status.RUNTIME_DIR)
        ops_status.RUNTIME_DIR = self.runtime / "events" / "processed" / "not-a-dir"

        data = dashboard_server.gather(NOW)

        self.assertIsNotNone(data["model"])
        self.assertEqual(len(data["blocks"]), len(dashboard_server._BLOCKS))

    def test_a_failed_model_is_not_reported_as_a_company_with_no_events(self):
        """C133. `render_html` keeps the operational half alive when
        `build_dashboard()` raises — deliberately, because the day the model
        raises is a day an operator still needs LAST RUN. The NOW section
        then rendered every model-derived tile as if the model had answered
        **zero**.

        Measured on the failure path before this: the headline read
        `셀 Event가 없다 — '문제 없음'이 아니라 '판단할 증거가 없다'`, the Blocker
        tile read `0건 정상`, and the Project tile read `이 기간에 움직인
        Project가 없다`. Three claims about the company, from a computation
        that failed.
        """
        page = dashboard_server.render_html(
            {
                "generated_at": NOW.isoformat(),
                "attention": [], "blocks": [],
                "model": None, "model_error": "boom",
                "build_ms": 1, "window": {"since": None, "until": None},
            }
        )

        self.assertIn("Model을 만들지 못했다", page)
        self.assertNotIn("셀 Event가 없다", page)
        self.assertNotIn("0건<span class='verdict-word ok'>정상</span>", page)
        self.assertNotIn("이 기간에 움직인 Project가 없다", page)
        self.assertIn("Model을 만들지 못해 셀 수 없었다", page)

    def test_a_failed_model_still_lets_the_attention_list_decide(self):
        """The pair. The ATTENTION list comes from `ops_status.py`'s own
        renderers, which ran — a model that could not be built does not make
        those findings go away, and burying a P1 behind "모델을 못 만들었다"
        would be the worse failure."""
        page = dashboard_server.render_html(
            {
                "generated_at": NOW.isoformat(),
                "attention": ["Runner가 9일째 실행되지 않았다"],
                "blocks": [], "model": None, "model_error": "boom",
                "build_ms": 1, "window": {"since": None, "until": None},
            }
        )

        self.assertIn("<span class='v-word'>조치 필요</span>", page)
        self.assertIn("ATTENTION 1건은 그대로 유효하다", page)
        self.assertIn("python run_company_ops.py", page)

    def test_the_verdict_follows_the_attention_list(self):
        self.put("E1")

        clear = self.page(attention=[])
        self.assertIn("<span class='v-word'>정상</span>", clear)
        self.assertIn("지금 할 일 없음", clear)

        busy = self.page(attention=["a", "b"])
        self.assertIn("ATTENTION 2건", busy)
        self.assertNotIn("<span class='v-word'>정상</span>", busy)

    def test_an_empty_attention_list_says_so_rather_than_saying_nothing(self):
        self.put("E1")

        page = self.page(attention=[])

        self.assertIn("사람이 지금 할 일은 없다", page)
        self.assertIn("exit 0", page)


class AFilteredPageSaysWhatItIsShowingTests(PageTestCase):
    """C86, the honesty half. Filtering makes two different facts look alike.

    An empty **window** and an empty `processed/` both give `events_read` 0.
    Measured on the deployment tree: `2026-08-20..25` returns 0 beside
    sixteen files on disk. The C76 banner said "`runtime/events/processed/`
    is empty", which would have been a **false sentence produced by a true
    field** — exactly what that banner was written to prevent, reintroduced
    by the feature.

    The second half is scope. The window bounds the panels; the operational
    blocks are `ops_status.py`'s own output and that module has no period
    concept. `기간 2026-08-01 ~ 2026-08-07` above a LAST RUN block describing
    this morning is one screen making two claims about "when".
    """

    def _windowed(self, since, until):
        from datetime import date

        data = {
            "generated_at": NOW.isoformat(),
            "attention": [],
            "blocks": [],
            "model": self.model().with_history_coverage(None).to_payload(),
            "model_error": None,
            "build_ms": 3,
            "window": {"since": since, "until": until},
        }
        payload = data["model"]
        payload["since"], payload["until"] = since, until
        payload["events_read"] = 0 if since == "2026-08-20" else payload["events_read"]
        return dashboard_server.render_html(data)

    def test_the_whole_period_says_so(self):
        self.put("E1")

        page = self.page()

        self.assertIn("기간 — 전체", page)
        self.assertIn("기간을 비워 두면 전체 기간이다", page)

    def test_an_active_window_is_named_in_a_heading(self):
        page = self._windowed("2026-08-05", "2026-08-07")

        self.assertIn("기간 — 2026-08-05 ~ 2026-08-07", page)

    def test_an_active_window_says_which_half_of_the_page_it_covers(self):
        """The operational blocks are never filtered. A page that did not say
        so would be claiming the whole screen is about that week."""
        page = self._windowed("2026-08-05", "2026-08-07")

        self.assertIn("위쪽 KPI·패널에만", page)
        self.assertIn("기간 개념이 없다", page)

    def test_an_empty_window_does_not_blame_an_empty_directory(self):
        page = self._windowed("2026-08-20", "2026-08-25")

        self.assertIn("이 <b>기간</b>에 Event가 없다", page)
        self.assertNotIn("<code>runtime/events/processed/</code> 가 비어 있다", page)

    def test_an_empty_corpus_still_blames_the_directory(self):
        """The pair. A fix that only ever said "this window" would stop
        telling an operator that the evidence itself is gone."""
        page = self.page()

        self.assertIn("<code>runtime/events/processed/</code> 가 비어 있다", page)
        self.assertNotIn("이 <b>기간</b>에 Event가 없다", page)

    def test_the_all_clear_is_scoped_to_the_window(self):
        """`증거 전체` means "everything that was read", which with a window
        on is the window. Unqualified, a filtered reader takes the green
        banner as "this covers everything the company did"."""
        self.put("E1", day=12)
        page = self._windowed("2026-08-05", "2026-08-19")

        self.assertIn("이 <b>기간</b>의 증거 전체를 덮는다", page)

    def test_the_all_clear_is_unqualified_without_a_window(self):
        """The pair. Qualifying it always would say "this period" about a
        page that has none."""
        self.put("E1", day=12)

        page = self.page()

        self.assertIn(ALL_CLEAR, page)
        self.assertNotIn("이 <b>기간</b>의 증거 전체", page)

    def test_the_evidence_age_is_scoped_to_the_window_too(self):
        """`마지막 증거 18일 전` under a filter is the newest Event **in the
        window**, not the last time the company reported."""
        self.put("E1", day=12)
        page = self._windowed("2026-08-05", "2026-08-19")

        self.assertIn("이 기간의 마지막 증거", page)

    def test_a_window_offers_the_way_back(self):
        page = self._windowed("2026-08-05", "2026-08-07")

        self.assertIn("전체 기간으로", page)

    def test_the_whole_period_offers_no_reset(self):
        self.put("E1")

        self.assertNotIn("전체 기간으로", self.page())


class TheFilterReachesTheRealServerTests(PageTestCase):
    """The parser and the page are gated above; this drives real sockets,
    because what an operator types is a URL."""

    def setUp(self):
        super().setUp()
        self.put("EARLY", day=5)
        self.put("LATE", day=17)
        self.addCleanup(setattr, ops_status, "RUNTIME_DIR", ops_status.RUNTIME_DIR)
        ops_status.RUNTIME_DIR = self.runtime
        self.server = dashboard_server._Server(
            ("127.0.0.1", 0), dashboard_server._Handler
        )
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _get(self, query=""):
        try:
            with urllib.request.urlopen(self.base + query, timeout=60) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, (error.read() or b"").decode("utf-8")

    def test_the_window_actually_narrows_the_numbers(self):
        status, body = self._get("/api/dashboard.json?since=2026-08-01&until=2026-08-10")

        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["window"], {"since": "2026-08-01", "until": "2026-08-10"})
        self.assertEqual(data["model"]["events_read"], 1)

    def test_the_whole_period_sees_both(self):
        """The control: without a window both Events are counted, so the
        assertion above is a difference rather than a coincidence."""
        _status, body = self._get("/api/dashboard.json")

        self.assertEqual(json.loads(body)["model"]["events_read"], 2)

    def test_a_bad_window_is_refused_over_http(self):
        for query, expected in (
            ("/?since=bogus", "날짜가 아닙니다"),
            ("/?since=2026-08-10&until=2026-08-05", "거꾸로"),
            ("/?form=1", "모르는 조건"),
        ):
            with self.subTest(query=query):
                status, body = self._get(query)

                self.assertEqual(status, 400)
                self.assertIn(expected, body)
                self.assertNotIn("DOJOONPASS Control Tower", body)

    def test_healthz_ignores_the_window_entirely(self):
        """A liveness probe must not be able to fail on a query string."""
        status, body = self._get("/healthz?since=bogus")

        self.assertEqual(status, 200)
        self.assertEqual(body, "ok")


class ThePeriodFilterRefusesWhatItCannotHonourTests(unittest.TestCase):
    """C86. `build_company_rollup()` has taken `since`/`until` since C48 and
    nothing on the page ever passed them.

    Bounded by the Event's own **work date**, not arrival — docs/06 section
    12, and `build_company_rollup()` says why: arrival would put a Desktop
    that was switched off for a week into the wrong period.

    Every rejection below is a refusal rather than a fallback. A page that
    quietly ignored a mistyped date would show every Event the company ever
    recorded while the operator believed they were reading one week. The
    numbers would all be real and the question they answer would be the wrong
    one, with nothing on screen saying so — which is the same failure
    `cli.unexpected_arguments()` exists to prevent, over HTTP instead of argv.
    """

    def test_no_query_is_the_whole_period(self):
        self.assertEqual(dashboard_server.parse_window(""), (None, None))

    def test_a_window_is_parsed(self):
        from datetime import date

        self.assertEqual(
            dashboard_server.parse_window("since=2026-08-05&until=2026-08-07"),
            (date(2026, 8, 5), date(2026, 8, 7)),
        )

    def test_one_open_end_is_allowed(self):
        from datetime import date

        self.assertEqual(
            dashboard_server.parse_window("since=2026-08-05"),
            (date(2026, 8, 5), None),
        )
        self.assertEqual(
            dashboard_server.parse_window("until=2026-08-07"),
            (None, date(2026, 8, 7)),
        )

    def test_a_blank_value_is_the_open_end_not_an_error(self):
        """The form submits empty inputs. Refusing those would make the
        control unusable for the case it exists to return to."""
        self.assertEqual(dashboard_server.parse_window("since=&until="), (None, None))

    def test_a_date_that_is_not_a_date_is_refused(self):
        for query in ("since=bogus", "until=2026-13-01", "since=05-08-2026"):
            with self.subTest(query=query):
                with self.assertRaises(dashboard_server.WindowError):
                    dashboard_server.parse_window(query)

    def test_a_backwards_window_is_refused(self):
        with self.assertRaises(dashboard_server.WindowError):
            dashboard_server.parse_window("since=2026-08-10&until=2026-08-05")

    def test_an_unknown_condition_is_refused(self):
        """There are two knobs. A third is a typo, and dropping it silently
        is the same silence as ignoring a bad date."""
        with self.assertRaises(dashboard_server.WindowError):
            dashboard_server.parse_window("form=1")

    def test_a_repeated_parameter_is_refused(self):
        """`?since=A&since=B` — picking one would be guessing which."""
        with self.assertRaises(dashboard_server.WindowError):
            dashboard_server.parse_window("since=2026-08-05&since=2026-08-06")

    def test_the_message_names_what_was_wrong(self):
        """A refusal whose whole value is telling the operator what to fix."""
        with self.assertRaises(dashboard_server.WindowError) as caught:
            dashboard_server.parse_window("since=bogus")

        self.assertIn("bogus", str(caught.exception))
        self.assertIn("YYYY-MM-DD", str(caught.exception))


class OneRequestCannotStealAnothersOutputTests(unittest.TestCase):
    """C78, measured. `redirect_stdout` rebinds process-global `sys.stdout`
    and `ThreadingHTTPServer` runs each request in its own thread.

    Two symptoms from the one cause, both measured on this machine before
    `_CAPTURE_LOCK` existed:

        2 concurrent GETs   12 blocks: 2 carried another request's text,
                            2 blank
        4 concurrent GETs   24 blocks: 8 foreign, 4 blank — and `sys.stdout`
                            left as a discarded StringIO, so every later
                            print() in the process went nowhere, silently
                            and permanently

    The second is why this is a leak and not a cosmetic bug: the nested
    context managers each restore what *they* saved, so an interleaving
    leaves the outer one installing a buffer the inner one owned. The probe
    that found it went quiet mid-run and never printed its own remaining
    results.

    Driven through real sockets, because thread scheduling is the subject.
    """

    WORKERS = 4

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runtime = Path(tmp.name)
        (runtime / "events" / "processed").mkdir(parents=True)
        (runtime / "local_master" / "daily").mkdir(parents=True)
        self.addCleanup(setattr, ops_status, "RUNTIME_DIR", ops_status.RUNTIME_DIR)
        ops_status.RUNTIME_DIR = runtime

        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), dashboard_server._Handler
        )
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _fetch(self, _index):
        with urllib.request.urlopen(
            self.url + "/api/dashboard.json", timeout=120
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_no_block_carries_another_requests_text(self):
        """Every block's captured text begins with its own banner, because
        that is what `ops_status.py` prints first in each one."""
        with ThreadPoolExecutor(self.WORKERS) as pool:
            pages = list(pool.map(self._fetch, range(self.WORKERS)))

        self.assertEqual(len(pages), self.WORKERS)
        for index, page in enumerate(pages):
            for block in page["blocks"]:
                with self.subTest(request=index, block=block["key"]):
                    text = block["text"].lstrip()

                    self.assertTrue(text, "a block came back blank")
                    self.assertTrue(
                        text.startswith(block["key"]),
                        f"{block['key']} carries another section's output: "
                        f"{text[:60]!r}",
                    )

    def test_the_process_still_owns_its_own_stdout_afterwards(self):
        """The half that outlives the request. A page that renders correctly
        and leaves the interpreter unable to print is the worse failure of
        the two — nothing raises, and the server's console goes quiet."""
        before = sys.stdout

        with ThreadPoolExecutor(self.WORKERS) as pool:
            list(pool.map(self._fetch, range(self.WORKERS)))

        self.assertIs(sys.stdout, before)

    def test_the_lock_protects_the_capture_and_not_the_whole_build(self):
        """The scope is deliberate: the lock exists for one process-global.
        Holding it over the model build would queue requests for arithmetic
        that shares nothing."""
        source = (
            Path(dashboard_server.__file__).read_text(encoding="utf-8")
        )
        held = source.index("with _CAPTURE_LOCK:")
        # Anchored on the call, not on its argument list: C86 added
        # `since=`/`until=` and an exact-line anchor turned this gate
        # into a ValueError instead of an assertion.
        model = source.index("payload = build_model_payload(now")

        self.assertLess(held, model, "the capture block comes first")
        self.assertNotIn(
            "_CAPTURE_LOCK",
            source[model - 400 : model + 200],
            "the model build must not be inside the capture lock",
        )


class TheCostOfThePageIsReportedTests(PageTestCase):
    """The page is linear in the number of Event files.

    Measured: about 1 ms per Event, five full passes over `processed/` to
    build one page — four of them `ops_status.py`'s own (a terminal run pays
    the same) and one the Control Tower panels'. At ten thousand Events that
    is roughly ten seconds, three quarters of it inside the COMPANY block.

    Nothing here makes it faster. It makes it **visible**, which is the
    difference between "there is a lot of evidence" and "this has hung".
    """

    def test_the_build_time_is_measured_and_shown(self):
        self.put("E1")

        self.assertIn("12ms에 생성", self.page())

    def test_gather_reports_its_own_cost(self):
        self.addCleanup(setattr, ops_status, "RUNTIME_DIR", ops_status.RUNTIME_DIR)
        ops_status.RUNTIME_DIR = self.runtime

        data = dashboard_server.gather(NOW)

        self.assertIsInstance(data["build_ms"], int)
        self.assertGreaterEqual(data["build_ms"], 0)

    def test_a_page_without_a_measurement_still_renders(self):
        """`build_ms` is a report, not a requirement. A caller that does not
        supply one gets a page, not a `KeyError`."""
        page = dashboard_server.render_html(
            {
                "generated_at": NOW.isoformat(),
                "attention": [],
                "blocks": [],
                "model": self.payload(),
                "model_error": None,
            }
        )

        self.assertIn("DOJOONPASS Control Tower", page)
        self.assertNotIn("에 생성", page)


class TheAgeOfTheSnapshotIsVisibleTests(PageTestCase):
    """C77's decision: the page does not refresh itself, so it must say how
    old it is.

    The whole hazard of a Control Tower on a second monitor is reading
    이상 없음 off a screen rendered three hours ago. Nothing on the page can
    prevent that except telling the reader, and the ISO timestamp alone does
    not — nobody subtracts two timestamps at a glance.
    """

    def test_the_page_does_not_reload_itself(self):
        """The alternative that was rejected, pinned so it cannot arrive by
        accident: an auto-reload replaces the last known state with a browser
        error page the moment the server is gone."""
        page = self.page()

        self.assertNotIn("http-equiv='refresh'", page)
        self.assertNotIn('http-equiv="refresh"', page)
        self.assertNotIn("location.reload", page)

    def test_the_generated_instant_is_machine_readable(self):
        page = self.page()

        self.assertIn(f"<time id='gen' datetime='{NOW.isoformat()}'>", page)

    def test_the_timestamp_survives_with_scripting_off(self):
        """The badge is `hidden` until the script fills it. A page whose only
        statement of when it was built lives inside a `<script>` says nothing
        at all to a reader who has none."""
        page = self.page()

        self.assertIn(NOW.isoformat(), page)
        self.assertIn("<span id='age' hidden></span>", page)

    def test_the_age_goes_stale_at_the_declared_threshold(self):
        """The threshold is a constant, not a number written twice."""
        page = self.page()

        self.assertIn(f"s>{dashboard_server._STALE_AFTER_S}", page)
        self.assertIn("stale", page)

    def test_the_page_says_out_loud_that_it_does_not_refresh(self):
        self.assertIn("스스로 갱신하지 않는다", self.page())


class EveryPanelReachesTheScreenTests(PageTestCase):
    """Guards the guard.

    Two hand-written rosters sit between the model and the page —
    `_PANEL_ORDER` and `_COLUMN_LABELS`. Neither can lose data (an unlisted
    panel is appended, an unlabelled column falls back to its key), so
    nothing would fail; the page would just quietly get worse. This is what
    notices instead.
    """

    def setUp(self):
        super().setUp()
        self.put("E1", project="P0", day=10)
        self.put("B1", project="P1", day=14, event_type="BLOCKED",
                 status="BLOCKED", blocker="waiting")

    def test_every_panel_the_model_builds_is_on_the_page(self):
        """METRICS is the one panel that is not drawn as a panel — it is the
        KPI row at the top, which is where a Control Tower's numbers belong.
        Checked by its rows rather than exempted, so it cannot vanish."""
        page = self.page()

        for panel in self.payload()["panels"]:
            with self.subTest(panel=panel["key"]):
                if panel["key"] == "METRICS":
                    self.assertIn("<section class='kpis'>", page)
                    for row in panel["rows"]:
                        self.assertIn(str(row["values"]["label"]), page)
                    continue
                self.assertIn(f"<span class='pkey'>{panel['key']}</span>", page)

    def test_no_panel_is_drawn_twice(self):
        """C133 added a third roster — `_PANEL_PLACEMENT`, which routes each
        panel to one of five regions. A key listed in two regions, or a
        region rendered twice, duplicates a panel; a reader then meets the
        same table in two places and cannot tell whether they are the same
        instant. The page said `<h2>패널</h2>` once before this and could
        not have the problem."""
        page = self.page()
        keys = re.findall(r"<span class='pkey'>([A-Z_]+)</span>", page)

        self.assertEqual(sorted(keys), sorted(set(keys)), keys)

    def test_every_panel_lands_in_exactly_one_region(self):
        """The routing itself, checked against the model rather than against
        the map: a panel added upstream must be placed, not dropped."""
        panels = self.payload()["panels"]
        regions = {}
        for panel in panels:
            regions.setdefault(dashboard_server.panel_placement(panel), []).append(
                panel["key"]
            )

        placed = sorted(k for keys in regions.values() for k in keys)
        self.assertEqual(placed, sorted(p["key"] for p in panels))
        self.assertEqual(regions["KPI"], ["METRICS"])
        self.assertIn("PROJECTS", regions["PROJECTS"])

    def test_a_panel_this_page_has_never_heard_of_is_still_shown(self):
        """`EVIDENCE`, not nowhere. An unknown panel is one nobody has
        decided about, and dropping it would make adding a panel upstream a
        silent no-op on the only surface a person reads."""
        placement = dashboard_server.panel_placement(
            {"key": "SOMETHING_NEW", "status": "SOURCED", "rows": [{"values": {}}]}
        )

        self.assertEqual(placement, "EVIDENCE")

    def test_an_empty_risk_table_is_not_left_at_the_top_of_the_screen(self):
        """`RISKS` moves between the first region and the last depending on
        whether it has rows. An open Blocker is priority ①; an empty Risk
        table is the sentence "이 기간의 증거에 이 항목이 하나도 없었다", and a
        clean company that always carried a red empty box would learn to
        ignore the box."""
        empty = {"key": "RISKS", "status": "SOURCED", "rows": []}
        full = {"key": "RISKS", "status": "SOURCED", "rows": [{"values": {}}]}

        self.assertEqual(dashboard_server.panel_placement(empty), "EVIDENCE")
        self.assertEqual(dashboard_server.panel_placement(full), "ACTION")

    def test_the_blocker_section_is_never_behind_a_disclosure(self):
        """The rule this whole redesign is measured against: P1 / Blocker /
        승인 필요 must not be collapsed."""
        self.put("B2", project="P2", day=15, event_type="BLOCKED",
                 status="BLOCKED", blocker="waiting on a vendor")
        page = self.page()

        self.assertIn("<section class='blockers'", page)
        # No *unclosed* `<details>` above it — i.e. it is not nested inside
        # one. Counted from `</style>` onward: `_CSS`'s own comments mention
        # `<details>` twice, and counting those made this measure the
        # stylesheet rather than the document.
        body = page[page.index("</style>") :]
        head = body[: body.index("<section class='blockers'")]

        self.assertEqual(head.count("<details"), head.count("</details>"), head[-400:])

    def test_the_panel_order_names_only_panels_that_exist(self):
        keys = {p["key"] for p in self.payload()["panels"]}

        self.assertEqual(set(dashboard_server._PANEL_ORDER) - keys, set())

    def test_the_panel_order_covers_every_panel(self):
        """An unlisted panel still renders — at the end, after the unsourced
        ones, which is where nothing important belongs."""
        keys = {p["key"] for p in self.payload()["panels"]}

        self.assertEqual(keys - set(dashboard_server._PANEL_ORDER), set())

    def test_every_column_of_every_sourced_panel_has_a_label(self):
        """A column rendered as its raw key (`blocked_project_count`) is a
        header written for the model rather than for the person reading."""
        for panel in self.payload()["panels"]:
            if panel["status"] != PanelStatus.SOURCED.value:
                continue
            for column in panel["columns"]:
                with self.subTest(panel=panel["key"], column=column):
                    self.assertIn(
                        column,
                        dashboard_server._COLUMN_LABELS,
                        f"{panel['key']}.{column} would render as its own key — "
                        "add it to _COLUMN_LABELS",
                    )

    def test_the_roster_of_blocks_is_the_roster_of_renderers(self):
        """`_BLOCKS` and `_RENDERERS` are two lists of the same sections,
        and a name in one and not the other is a `KeyError` at request
        time — on the page an operator opened because something looked
        wrong."""
        self.assertEqual(
            [key for key, _title, _parity in dashboard_server._BLOCKS],
            list(dashboard_server._RENDERERS),
        )

    def test_this_page_shows_every_block_the_terminal_tool_prints(self):
        """The property those two hand-written lists could never give.

        They are held in step with **each other** and with nothing else, and
        this page exists to mirror `ops_status.py`. So a block added to the
        tool reaches the terminal and silently never reaches the page — or
        the Notion Control Tower, which `publish_control_tower.py` renders
        from this same `gather()` payload. That is the seat for everyone who
        does not open a terminal, and it would be missing a section with
        nothing anywhere reporting the omission.

        Measured: it had already happened. `ops_status.main()` grew a
        SCHEDULE block and both lists here stayed at six, so the page and
        the Notion page showed no scheduling state at all while the terminal
        did (C138).

        Derived from `main()` rather than restated, the same way
        `test_observability.py`'s own roster guard is — a second hand-written
        list would be a third thing to keep in step.
        """
        import inspect

        source = inspect.getsource(ops_status.main)
        printed = [
            key
            for key in re.findall(r'\(\s*"([A-Z][A-Z ]+)",\s*_print_\w+\)', source)
        ]

        self.assertTrue(printed, "the scan found no blocks — it has gone blind")
        self.assertEqual(printed, list(dashboard_server._RENDERERS))

    def test_the_renderers_are_the_tools_own_functions(self):
        """Not a re-implementation. The page inherits `_block()`'s
        damaged-tree guarantee and the tool's wording by using the same
        callables, and a local copy of any of them would drift silently."""
        for key, renderer in dashboard_server._RENDERERS.items():
            with self.subTest(block=key):
                self.assertIs(
                    renderer,
                    getattr(ops_status, renderer.__name__),
                    f"{key} does not render through ops_status.{renderer.__name__}",
                )

    def test_the_control_tower_block_is_the_one_kept_for_comparison(self):
        """The page renders the Control Tower twice on purpose — as panels,
        and as the terminal's own text — so a person can check them against
        each other. Any *other* block marked that way would be hiding
        operational state inside a collapsed element."""
        parity = [key for key, _title, flag in dashboard_server._BLOCKS if flag]

        self.assertEqual(parity, ["CONTROL TOWER"])



def _bare_payload(**over):
    """The smallest payload `render_html()` accepts, for UI-shape tests."""
    payload = {
        "generated_at": "2026-08-27T10:00:00+09:00",
        "window": {"since": None, "until": None},
        "build_ms": 5,
        "attention": [],
        "blocks": [],
        "ops": {},
        "model": {
            "schema_version": "1.2",
            "generated_at": "2026-08-27T10:00:00+09:00",
            "since": None,
            "until": None,
            "events_read": 3,
            "coverage": {
                "evidence_from": "2026-08-05", "evidence_to": "2026-08-10",
                "unreadable": 0, "duplicates": 0,
                "history_uncovered_from": None, "history_checked": True,
                "complete": True,
            },
            "unreadable": [],
            "panels": [],
        },
        "model_error": None,
    }
    payload.update(over)
    return payload


def _panel(key, columns, rows, **over):
    panel = {
        "key": key, "title": key, "status": "SOURCED",
        "columns": list(columns),
        "rows": [
            {"key": str(i), "values": values, "evidence": [], "evidence_count": 0}
            for i, values in enumerate(rows)
        ],
        "note": None, "source": None, "unsourced_layers": [],
    }
    panel.update(over)
    return panel


class AttentionSaysHowBadAndWhereFromTests(unittest.TestCase):
    """ATTENTION, as the thing a person reads first (C129).

    What it was: a flat `<ol>` of nine escaped strings, in the order
    `ops_status.py` happened to build them, with a 396-character paragraph
    sitting between two one-line alerts and `**` / backticks showing as
    literal characters. A reader could not tell which line meant "Company
    History is not being written" and which meant "a Desktop is quiet".

    Three things changed, and each is checked below:

        severity   P1 / P2 / **?**, with the phrase it matched shown beside
                   it — the classification is this screen's reading, not a
                   field the pipeline computes, and it says so
        source     which ops_status block raised the line, reconstructed
                   exactly from `blocks[i]["attention"]`
        length     over 150 characters folds into a disclosure, so one long
                   line cannot push the rest below the fold
    """

    def test_a_stopped_pipeline_outranks_a_quiet_desktop(self):
        page = dashboard_server.render_html(
            _bare_payload(attention=[
                "3일 이상 아무것도 오지 않은 Desktop: DESKTOP_2",
                "Runner가 9일째 실행되지 않았다",
            ])
        )

        self.assertEqual(re.findall(r"<li class='att (\w+)'>", page), ["p1", "p2"])

    def test_a_line_nothing_classifies_is_labelled_not_buried(self):
        """The honest default. Filing an unrecognised alert as minor is how
        a new critical condition arrives silently."""
        page = dashboard_server.render_html(
            _bare_payload(attention=["완전히 새로운 종류의 경보"])
        )

        self.assertIn("<span class='sev unknown'>?</span>", page)
        self.assertIn("분류하지 못한", page)
        self.assertEqual(re.findall(r"<li class='att (\w+)'>", page), ["unknown"])

    def test_the_badge_says_what_it_matched_on(self):
        """A severity with no stated reason is an opinion. This screen has
        no severity field to quote, so it quotes its own rule instead."""
        page = dashboard_server.render_html(
            _bare_payload(attention=["Runner가 9일째 실행되지 않았다"])
        )

        self.assertIn("파이프라인이 돌지 않음", page)
        self.assertIn("이 화면의 분류", page)

    def test_each_line_carries_the_block_that_raised_it(self):
        """Exact, not guessed: `gather()` extends the flat list block by
        block and records each count in the same pass."""
        page = dashboard_server.render_html(
            _bare_payload(
                attention=["Runner가 9일째 실행되지 않았다", "무언가"],
                blocks=[
                    {"key": "LAST RUN", "title": "LAST RUN", "parity": False,
                     "text": "", "attention": 1},
                    {"key": "AGENT", "title": "AGENT", "parity": False,
                     "text": "", "attention": 1},
                ],
            )
        )

        self.assertIn("<span class='sev-src'>LAST RUN</span>", page)
        self.assertIn("<span class='sev-src'>AGENT</span>", page)

    def test_a_mismatched_partition_attributes_nothing(self):
        """If the counts do not add up, the list was built some other way.
        Attributing a line to the wrong block is worse than not attributing
        it — the operator would open the wrong file."""
        sources = dashboard_server.attention_sources(
            ["a", "b", "c"], [{"key": "COMPANY", "attention": 1}]
        )

        self.assertEqual(sources, [None, None, None])

    def test_a_long_p2_folds_instead_of_pushing_the_rest_down(self):
        """Folding exists so one long paragraph cannot push the other items
        below the fold. It applies to the ones that can afford to wait."""
        long_line = "3일 이상 아무것도 오지 않은 Desktop: " + "가" * 300
        page = dashboard_server.render_html(_bare_payload(attention=[long_line]))

        self.assertIn("전체 보기", page)
        self.assertIn(f"({len(long_line):,}자)", page)

    def test_a_p1_is_never_folded_however_long_it_is(self):
        """**The screen exists to show these** (C130). Measured on the
        rendered page: two items sat behind a disclosure and one was a P1 —
        `KEEP Candidate … Daily History에 없다`, whose tail carries the only
        sentence saying what recovers it. An operator scanning for what is
        wrong read 150 characters of the most serious item and had to click
        for the rest."""
        tail = "여기에만 적힌 복구 방법"
        long_p1 = ("수집됐지만 History에 들어가지 못한 Event 1건: "
                   + "가" * 400 + " " + tail)
        page = dashboard_server.render_html(_bare_payload(attention=[long_p1]))

        self.assertNotIn("전체 보기", page)
        self.assertIn(tail, page)

    def test_an_unclassified_line_is_never_folded_either(self):
        """It sorts to the top because nobody knows what it is; folding it
        would put the unknown behind a click."""
        long_unknown = "아무 규칙에도 걸리지 않는 새 경보 " + "나" * 400
        page = dashboard_server.render_html(_bare_payload(attention=[long_unknown]))

        self.assertNotIn("전체 보기", page)

    def test_a_marker_cut_in_half_by_the_fold_is_not_left_showing(self):
        """The fold cuts at 150 characters and lands mid-`**` about as often
        as not. `_inline_markup()` only matches pairs, so the head rendered
        as `대상은 **그 실행이 수집한…` — literal asterisks, which is the
        defect the markup rendering was added to remove."""
        line = ("3일 이상 아무것도 오지 않은 Desktop: " + "가" * 180
                + " **강조된 부분이 잘린다** 그리고 더 있다" + "나" * 200)
        page = dashboard_server.render_html(_bare_payload(attention=[line]))

        head = page[page.index("att-body"):page.index("전체 보기")]
        self.assertNotIn("**", head)
        # and the full text in the disclosure still renders the pair
        self.assertIn("<strong>강조된 부분이 잘린다</strong>", page)

    def test_pairs_the_author_balanced_are_untouched(self):
        """The control: `_drop_unpaired()` must only take a marker the cut
        orphaned, never one the author closed."""
        self.assertEqual(dashboard_server._drop_unpaired("가 **나** 다"), "가 **나** 다")
        self.assertEqual(dashboard_server._drop_unpaired("평범한 문장"), "평범한 문장")

    def test_the_authors_emphasis_renders_and_their_markup_does_not(self):
        """`ops_status.py` writes `**` and backticks for both surfaces. Raw,
        they showed as asterisks; unescaped, an Event id could inject."""
        page = dashboard_server.render_html(
            _bare_payload(attention=["<script>x</script> **굵게** `코드`"])
        )

        self.assertIn("<strong>굵게</strong>", page)
        self.assertIn("<code>코드</code>", page)
        self.assertNotIn("<script>x", page)

    def test_every_line_carries_what_to_do_about_it(self):
        """C133. The list described conditions and prescribed nothing.

        The portfolio-reporting rule this was measured against is explicit:
        every red or amber entry needs one line saying what happens next.
        Before this, none of the eleven had one.
        """
        page = dashboard_server.render_html(
            _bare_payload(attention=[
                "Runner가 9일째 실행되지 않았다",
                "3일 이상 아무것도 오지 않은 Desktop: X",
            ])
        )

        self.assertEqual(page.count("<b>다음 행동</b>"), 2)
        self.assertIn("python run_company_ops.py", page)
        self.assertIn("python run_agent.py", page)

    def test_an_unclassified_line_admits_it_has_no_remedy(self):
        """Inventing a remedy for a line nothing classified is the failure
        `?` exists to prevent one field over."""
        page = dashboard_server.render_html(
            _bare_payload(attention=["무엇인지 알 수 없는 새 경보"])
        )

        self.assertIn("att-do none", page)
        self.assertIn("정해 두지 않았다", page)

    def test_the_remedy_is_never_folded_behind_the_p2_disclosure(self):
        """An item worth showing is an item worth showing the remedy for."""
        long_p2 = "3일 이상 아무것도 오지 않은 Desktop: " + "X" * 400
        page = dashboard_server.render_html(_bare_payload(attention=[long_p2]))

        head = page[: page.index("<details class='more'>")]
        self.assertIn("전체 보기", page)
        self.assertNotIn("<b>다음 행동</b>", head)
        # ...and it is outside the disclosure, after it, not inside.
        disclosure = page[
            page.index("<details class='more'>") : page.index("</details>")
        ]
        self.assertNotIn("<b>다음 행동</b>", disclosure)
        self.assertIn("<b>다음 행동</b>", page)

    def test_every_rule_the_module_classifies_has_a_remedy(self):
        """Guards the guard. A phrase added to `RULES` with no entry in
        `ACTIONS` classifies a line and then tells its reader nothing, which
        is worse than leaving it `?` — the badge would claim the screen
        understood the line."""
        from controltower import attention as attention_module

        for phrase, level, _why in attention_module.RULES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, attention_module.ACTIONS)
                self.assertTrue(attention_module.ACTIONS[phrase].strip())

    def test_the_conditions_the_probe_tree_exposed_are_classified(self):
        """C133. Four real conditions fell through as `?`.

        Measured on a probe tree with one blocked Project: 3 ATTENTION items,
        2 of them `?`, and one of those was the open Blocker — the single
        most actionable line this Dashboard shows, rendered as "이 화면이
        분류하지 못한 줄".
        """
        cases = {
            "5일째 막혀 있는 Project: P [CTO Backend] — waiting on a vendor": "P2",
            "같은 event_id를 두고 내용이 다른 파일이 둘 있다: E1": "P1",
            "Desktop과 role이 어긋난 Event: E1 — DESKTOP_1에서 왔는데": "P2",
            "작업일은 3일 이상 지났지만 최근 파일이 도착한 Desktop: D "
            "(꺼져 있다가 밀린 분을 보낸 것으로 보인다 — Agent는 살아 있다)": "P2",
            "사람 검토를 기다리는 History Candidate 2건": "P2",
        }
        for line, expected in cases.items():
            with self.subTest(line=line[:26]):
                level, why = dashboard_server.attention_severity(line)

                self.assertEqual(level, expected)
                self.assertTrue(why)
                self.assertTrue(dashboard_server._attention_action(line))

    def test_every_shape_ops_status_can_raise_is_classified_and_actionable(self):
        """The assembled shapes, hand-written — and **not** the whole roster.

        `ops_status.py` builds these lines by f-string concatenation across
        several source lines, so grepping the file for a phrase does not
        prove the phrase reaches the classifier — the assembled string does.
        These are the assembled shapes. Before C133 four of the eleven came
        out `?`, including the open Blocker, which is the most actionable
        line this Dashboard shows.

        **This docstring used to open "The whole roster, not a sample", and
        C138 made that false without failing anything.** The SCHEDULE block
        added ten alarms and joined none of them, which is what a
        hand-written list does — measured by loading the rendered page, both
        of its lines carried a `?` badge and no next action.

        The reason above is still the reason this list is written by hand,
        so it stays. What it cannot be is complete. `test_observability.py
        ::TheScheduleBlockAsksWindowsWhatNoFileCanAnswerTests
        ::test_every_alarm_this_block_raises_is_classified_and_actionable`
        takes the other half for SCHEDULE: it **runs** the block through
        each alarming state and classifies whatever comes out, so the
        strings are still the assembled ones and a new alarm joins the sweep
        the day it exists. A block that grows one is covered; a block whose
        author writes a fresh hand-list is not.

        Ordering is checked implicitly and it matters: `RULES` is
        first-match-wins with P1 listed first, so a P2 shape that happens to
        contain a P1 phrase would be escalated. That direction is safe; the
        reverse would not be, and this is what would notice.
        """
        cases = {
            "OPEN_BLOCKER": ("P2",
                "5일째 막혀 있는 Project: P [CTO Backend] — waiting on a vendor "
                "(증거 E1.json) — Blocker는 파이프라인이 스스로 지우지 않는다. 그 팀이 "
                "RESUMED / ISSUE_RESOLVED / COMPLETED를 보고할 때까지 열려 있다"),
            "OPEN_BLOCKER_TOTAL": ("P2",
                "막혀 있는 Project 총 5건 — 위 3건 외 2건이 더 있다. Blocker는 "
                "파이프라인이 스스로 지우지 않으므로 이 수는 줄지 않는다"),
            "EVENT_ID_CONFLICT": ("P1",
                "같은 event_id를 두고 내용이 다른 파일이 둘 있다: E1 — Control Tower는 "
                "a를 세었고 b는 세지 않았다"),
            "ROLE_MISMATCH": ("P2",
                "Desktop과 role이 어긋난 Event: E1 — DESKTOP_1에서 왔는데 role은 "
                "CMO라고 말한다. 거부하지 않는 이유와 필요한 결정은 BACKLOG"),
            "ROLE_MISMATCH_TOTAL": ("P2",
                "Desktop과 role이 어긋난 Event 총 5건 — 위 3건 외 2건은 같은 종류다"),
            "SILENT": ("P2",
                "3일 이상 아무것도 오지 않은 Desktop: DESKTOP_1 (꺼져 있거나, 보고할 "
                "일이 없었거나, Agent가 멈췄다)"),
            "CAUGHT_UP": ("P2",
                "작업일은 3일 이상 지났지만 최근 파일이 도착한 Desktop: DESKTOP_1 "
                "(꺼져 있다가 밀린 분을 보낸 것으로 보인다 — Agent는 살아 있다)"),
            "REVIEW_WAITING": ("P2",
                "사람 검토를 기다리는 History Candidate 3건 "
                "(runtime/history_candidates/review/) — docs/05 §24"),
            "NOTION_UNEXERCISED": ("P2",
                "Notion 자격증명이 전달돼 있지만 그것으로 Notion 단계를 시도한 실행이 "
                "아직 없다 — run_company_ops.py를 한 번 실행해 확인해야 한다"),
            "RUNNER_STALE": ("P1",
                "Runner가 9일째 실행되지 않았다 (마지막 실행 2026-08-18)"),
            "REJECTED": ("P1",
                "Collector가 거부한 Event 2건 — 사람이 확인해야 한다"),
            "E17": ("P1",
                "KEEP Candidate 1건이 저장돼 있는데 그 날짜의 Daily History에 없다: E1"),
        }
        for name, (expected, line) in cases.items():
            with self.subTest(shape=name):
                level, why = dashboard_server.attention_severity(line)

                self.assertEqual(level, expected, line[:60])
                self.assertTrue(why, "classified with no stated reason")
                self.assertTrue(
                    dashboard_server._attention_action(line),
                    "classified with no remedy",
                )

    def test_only_the_review_queue_asks_for_a_decision(self):
        """The FIX / DECIDE split is narrow on purpose. `사람이 확인해야 한다`
        is appended to fault lines too, so it does not separate the two —
        the review queue does, because `docs/05 §24` forbids deciding those
        automatically."""
        review = "사람 검토를 기다리는 History Candidate 3건"
        fault = "Collector가 거부한 Event 2건 — 사람이 확인해야 한다"

        self.assertEqual(dashboard_server._attention_kind(review), "DECIDE")
        self.assertEqual(dashboard_server._attention_kind(fault), "FIX")

    def test_severity_is_derived_not_stored(self):
        """Guards the guard: the rule table must actually discriminate."""
        self.assertEqual(
            dashboard_server.attention_severity("Runner가 3일째 실행되지 않았다")[0],
            "P1",
        )
        self.assertEqual(
            dashboard_server.attention_severity(
                "3일 이상 아무것도 오지 않은 Desktop: X"
            )[0],
            "P2",
        )
        self.assertEqual(dashboard_server.attention_severity("xyz")[0], "?")


class TheCompanyLineSeparatesQuietFromEmptyTests(unittest.TestCase):
    """COMPANY — the required first section, which did not exist (C129).

    What stood in for it was the `COMPANY` prose block at the bottom of the
    page, inside a `<pre>`.

    The case this class exists for is the last one: **zero Events with zero
    ATTENTION**. The first draft printed a green "지금 사람이 할 일은 없다"
    over six zeroes — every word true about a field, and false about the
    company. That is C77's defect arriving in the section written to
    summarise it, so the evidence count decides before the severity does.
    """

    def _verdict(self, page):
        return re.search(r"company-state (\w+)'>([^<]*)", page).groups()

    def test_no_events_is_not_reported_as_nothing_to_do(self):
        model = _bare_payload()["model"] | {"events_read": 0}
        page = dashboard_server.render_html(_bare_payload(model=model))

        tone, text = self._verdict(page)
        self.assertEqual(tone, "warn")
        self.assertIn("셀 Event가 없다", text)

    def test_events_and_no_attention_is_reported_as_clear(self):
        """The control — without it the check above passes on a screen that
        never says anything is fine."""
        page = dashboard_server.render_html(_bare_payload())

        tone, text = self._verdict(page)
        self.assertEqual(tone, "ok")
        self.assertIn("할 일은 없다", text)

    def test_a_p1_makes_the_line_red(self):
        page = dashboard_server.render_html(
            _bare_payload(attention=["Runner가 9일째 실행되지 않았다"])
        )

        tone, text = self._verdict(page)
        self.assertEqual(tone, "bad")
        self.assertIn("P1 1건", text)

    def test_only_p2s_make_it_amber(self):
        page = dashboard_server.render_html(
            _bare_payload(attention=["3일 이상 아무것도 오지 않은 Desktop: X"])
        )

        self.assertEqual(self._verdict(page)[0], "warn")

    def test_no_field_renders_as_the_word_none(self):
        """C133. `overall_status` was spelled out with `str()` instead of
        `_e()`, so a Run Manifest that carried no status put the literal
        word `None` on the tile a five-second reader looks at first.
        Measured with `ops={"run": {}}`. Every other value on the page goes
        through `_e()`, which is where the em-dash-for-nothing rule lives."""
        for ops in ({"run": {}}, {"run": {"days_ago": 0.2}}, {"agent": {}}):
            with self.subTest(ops=ops):
                page = dashboard_server.render_html(_bare_payload(ops=ops))

                self.assertNotIn(">None<", page)
                self.assertNotIn(">None ", page)

    def test_a_missing_record_is_not_a_zero(self):
        """`operational_facts()` answers `None` for a source it could not
        read, and the difference has to survive to the screen."""
        page = dashboard_server.render_html(_bare_payload(ops={}))

        self.assertIn("기록 없음", page)
        self.assertIn("Run Manifest가 아직 없다", page)


class TheTwoNotionSyncsAreNeverOneStatusTests(unittest.TestCase):
    """NOTION SYNC — the required ninth section, which did not exist (C129).

    AGENT.md §6c spends a paragraph on why these must not be merged: the
    Runner's sync writes PROJECTS **rows** on the Runner's schedule, the
    publish rewrites a **page** when a person runs a command, and "앞의 것이
    며칠 멈춰 있어도 뒤의 것은 계속 성공한다". One status for both is false
    about whichever one the reader meant.
    """

    def _page(self, **ops):
        return dashboard_server.render_html(_bare_payload(ops=ops))

    def test_both_syncs_appear_as_separate_cards(self):
        page = self._page(run={"notion_sync": "SUCCESS", "started_at": "2026-08-27T09:00:00+09:00", "days_ago": 0.1})

        self.assertIn("Runner의 Notion Sync", page)
        self.assertIn("Dashboard publish", page)
        self.assertEqual(page.count("<div class='sync'>"), 2)

    def test_skipped_is_shown_as_not_a_failure(self):
        """docs/14 §4: `SKIPPED`가 실패가 아닌 것이 핵심이다. Rendering it red
        would report every pre-Notion deployment as broken."""
        page = self._page(run={"notion_sync": "SKIPPED", "started_at": None})

        self.assertIn("실패가 아니다", page)
        self.assertNotIn("<span class='state bad'>SKIPPED</span>", page)

    def test_the_publish_side_admits_it_has_no_local_record(self):
        """`publish_control_tower.py` writes its timestamp onto the Notion
        page and nothing here. Borrowing the Runner's would be the merge
        this section exists to prevent."""
        page = self._page(run={"notion_sync": "SUCCESS",
                               "started_at": "2026-08-27T09:00:00+09:00"})

        publish = page[page.index("Dashboard publish"):]
        self.assertIn("이 머신에 기록이 없다", publish)
        self.assertNotIn("2026-08-27T09:00:00+09:00", publish[:600])

    def test_the_page_says_whether_this_process_can_see_the_credentials(self):
        """C133, found by publishing to the live workspace.

        Same instant, same company: the browser page said `ATTENTION 1건`
        and the Notion page said `2건`. Neither was wrong —
        `publish_control_tower.py` had been started from a shell with the
        token exported and the server had not, so the NOTION block raised a
        line in one process and not the other. Neither surface said which of
        the two states it had rendered under, and two screens describing one
        company must not be able to disagree in silence.
        """
        seen = self._page(notion_credentials=True)
        blind = self._page(notion_credentials=False)

        self.assertIn("이 프로세스에 전달됨", seen)
        self.assertIn("이 프로세스에 없음", blind)
        self.assertIn("ATTENTION은 그만큼 적을 수 있다", blind)

    def test_a_credential_state_this_screen_could_not_read_is_not_a_no(self):
        """`None` is not `False`. "이 프로세스에 없음" is a claim about the
        environment; a screen that could not look has not made it."""
        page = self._page()

        self.assertIn("확인하지 못했다", page)
        self.assertNotIn("이 프로세스에 없음", page)

    def test_no_credential_value_can_reach_the_page(self):
        """Whether, never what."""
        import os
        from unittest import mock

        with mock.patch.dict(
            os.environ,
            {"NOTION_API_TOKEN": "secret-token-value",
             "NOTION_PROJECTS_DATABASE_ID": "secret-db-id"},
        ):
            facts = dashboard_server.operational_facts(NOW)

        self.assertIs(facts["notion_credentials"], True)
        page = dashboard_server.render_html(_bare_payload(ops=facts))
        self.assertNotIn("secret-token-value", page)
        self.assertNotIn("secret-db-id", page)

    def test_an_unreadable_queue_is_not_reported_as_empty(self):
        page = self._page(run={}, notion_queue=None, notion_pending=None)

        self.assertIn("읽지 못했다", page)


class OneValueRepeatedIsNotAColumnTests(unittest.TestCase):
    """Table width, measured rather than eyeballed (C129).

    On the live screen PROJECTS had **16 columns**, six of them `—` in all
    four rows, and ACTIVITY had twelve, two of which (`of_total`,
    `truncated`) held literally the same value on all sixteen rows. The cost
    was not the pixels: the table needed sideways scrolling, so `Project`
    and `Blocker` could not be read at the same time as the row they
    belonged to.
    """

    def _page(self, columns, rows):
        model = _bare_payload()["model"] | {
            "panels": [_panel("ACTIVITY", columns, rows)]
        }
        return dashboard_server.render_html(_bare_payload(model=model))

    def test_a_constant_column_is_shown_once(self):
        page = self._page(
            ["event_id", "status", "of_total"],
            [{"event_id": "E1", "status": "IN_PROGRESS", "of_total": 2},
             {"event_id": "E2", "status": "IN_PROGRESS", "of_total": 2}],
        )

        self.assertIn("한 줄로 접었다", page)
        self.assertEqual(re.search(r"<thead><tr>(.*?)</tr>", page).group(1).count("<th"), 2)
        # the value is not lost — it is stated once
        self.assertIn("IN_PROGRESS", page)

    def test_a_varying_column_stays_a_column(self):
        page = self._page(
            ["event_id", "status"],
            [{"event_id": "E1", "status": "IN_PROGRESS"},
             {"event_id": "E2", "status": "BLOCKED"}],
        )

        self.assertNotIn("한 줄로 접었다", page)

    def test_the_first_column_is_never_folded(self):
        """It identifies the row. Folding it would leave a table of values
        with nothing to attach them to."""
        kept, folded = dashboard_server._fold_constant_columns(
            ["project_id", "status"],
            [{"values": {"project_id": "P", "status": "X"}},
             {"values": {"project_id": "P", "status": "X"}}],
        )

        self.assertEqual(kept, ["project_id"])
        self.assertEqual(folded, [("status", "X")])

    def test_a_single_row_folds_nothing(self):
        """With one row every column is trivially constant and the table
        would disappear."""
        kept, folded = dashboard_server._fold_constant_columns(
            ["a", "b", "c"], [{"values": {"a": 1, "b": 2, "c": 3}}]
        )

        self.assertEqual(kept, ["a", "b", "c"])
        self.assertEqual(folded, [])

    def test_a_single_rows_empty_columns_still_fold(self):
        """C133. `RISKS` is a union of three row shapes, so one open Blocker
        cannot fill `claimed_role` / `expected_role` / `kept` / `ignored`.

        Measured on a probe tree: one row, six columns of `—`, on the table
        this page puts at the top. Those are not missing values — they are
        columns that do not apply — and an always-empty column has nothing
        in it to lose by folding.
        """
        kept, folded = dashboard_server._fold_constant_columns(
            ["kind", "blocker", "claimed_role", "kept"],
            [{"values": {"kind": "OPEN_BLOCKER", "blocker": "x",
                         "claimed_role": None, "kept": ""}}],
        )

        self.assertEqual(kept, ["kind", "blocker"])
        self.assertEqual(folded, [("claimed_role", None), ("kept", "")])

    def test_a_single_rows_first_column_never_folds_even_when_empty(self):
        """The row would lose the thing that identifies it."""
        kept, _folded = dashboard_server._fold_constant_columns(
            ["id", "b"], [{"values": {"id": None, "b": None}}]
        )

        self.assertEqual(kept, ["id"])

    def test_the_note_covers_both_reasons_a_column_folded(self):
        """It said "같은 값인 열" only, which is a false description of a
        column that is empty in a one-row table."""
        note = dashboard_server._folded_html([("claimed_role", None)], 1)

        self.assertIn("비어 있는", note)


class TimeAndWidthAreReadableTests(unittest.TestCase):
    """The remaining width offenders, and the narrow screen (C129)."""

    def test_a_timestamp_is_shown_compactly_with_the_full_value_kept(self):
        cell = dashboard_server._timestamp_cell("2026-08-05T18:00:00+09:00")

        self.assertIn("08-05 18:00", cell)
        self.assertIn("title='2026-08-05T18:00:00+09:00'", cell)

    def test_something_that_is_not_a_timestamp_is_left_alone(self):
        self.assertIsNone(dashboard_server._timestamp_cell("해당 없음"))
        self.assertIsNone(dashboard_server._timestamp_cell(""))

    def test_a_numeric_verdict_keeps_its_colour(self):
        """The regression this class was written after: right-aligning
        numbers first dropped the verdict class from `days_silent`, which is
        the whole point of the DESKTOPS table."""
        cell = dashboard_server._cell("days_silent", 18)

        self.assertIn("num", cell)
        self.assertIn("warn", cell)

    def test_no_marker_of_the_projects_own_convention_is_left_showing(self):
        """One convention, one renderer for it.

        `ops_status.py` and the Dashboard Model both write `**bold**` and
        `` `code` ``. ATTENTION was taught to render them; panel notes were
        not, and measured on the live page two `**` pairs survived — in the
        SPRINTS and JUDGEMENTS notes, which are the two panels a reader most
        needs to understand because they explain why a section is empty.
        """
        model = _bare_payload()["model"] | {
            "panels": [
                _panel("X", ["a"], [{"a": 1}],
                       note="`코드`와 **강조**가 있는 설명",
                       source="`출처`도 마찬가지"),
            ]
        }
        page = dashboard_server.render_html(_bare_payload(model=model))

        self.assertIn("<strong>강조</strong>", page)
        self.assertIn("<code>코드</code>", page)
        self.assertIn("<code>출처</code>", page)
        self.assertNotIn("**강조**", page)

    def test_a_note_still_cannot_become_markup(self):
        """`_inline_markup()` escapes before it substitutes, so widening the
        rendering must not have widened what an author can inject."""
        model = _bare_payload()["model"] | {
            "panels": [_panel("X", ["a"], [{"a": 1}],
                              note="<script>alert(1)</script>")]
        }
        page = dashboard_server.render_html(_bare_payload(model=model))

        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_the_page_brings_its_own_icon(self):
        """A browser asks for `/favicon.ico` on every load and this server
        answered **404** — measured in its own log, on the line after the
        page request. A 404 in the network tab beside a status screen is one
        more thing an operator has to rule out.

        A `data:` URI rather than a route, so nothing new is served and the
        page still fetches nothing from anywhere (C130)."""
        page = dashboard_server.render_html(_bare_payload())

        self.assertIn("rel='icon'", page)
        self.assertIn("data:image/svg+xml,", page)

    def test_the_icon_is_inline_and_not_a_second_request(self):
        """The point of the fix. A `<link rel=icon href=/something>` would
        trade one 404 for one more round trip, and this page's own footer
        promises it contacts nothing."""
        page = dashboard_server.render_html(_bare_payload())

        icon = page[page.index("rel='icon'"):]
        icon = icon[:icon.index(">")]
        self.assertIn("data:", icon)
        self.assertNotIn("http://", icon.replace("http://www.w3.org", ""))

    def test_a_block_does_not_print_its_own_title_twice(self):
        """`ops_status.py` opens each section with its title and a rule,
        because a terminal has nothing else to separate them. The card's
        `<h3>` says the same thing two lines above, so every block opened
        with its title twice — twelve lines of the operational area saying
        nothing across six blocks (C130)."""
        payload = _bare_payload(blocks=[{
            "key": "COMPANY", "title": "COMPANY — Desktop 4가 수집한 Event",
            "parity": False, "attention": 0,
            "text": "COMPANY — Desktop 4가 수집한 Event 기준\n" + "-" * 60
                    + "\n  DESKTOP_1  events=9",
        }])
        page = dashboard_server.render_html(payload)

        pre = page[page.index("<pre>"):page.index("</pre>")]
        self.assertNotIn("-" * 60, pre)
        self.assertNotIn("기준", pre)
        self.assertIn("DESKTOP_1", pre)
        # the title is still on the card, once
        self.assertEqual(page.count("COMPANY — Desktop 4가 수집한 Event"), 1)

    def test_a_block_of_another_shape_keeps_every_line(self):
        """Losing a line of content to a guess is worse than a duplicated
        heading. Both conditions must hold before anything is dropped."""
        for text in (
            "COMPANY 제목만 있고 밑줄이 없다\n  내용",
            "다른 것으로 시작한다\n" + "-" * 60 + "\n  내용",
            "한 줄뿐",
        ):
            with self.subTest(text=text[:18]):
                self.assertEqual(
                    dashboard_server._strip_block_heading(text, "COMPANY"), text
                )

    def test_no_cell_is_cut_with_an_ellipsis(self):
        """`.kpi-src` was the one rule on the page that clipped text
        outright; hover revealed it, and hover does not exist on a phone."""
        page = dashboard_server.render_html(_bare_payload())

        self.assertNotIn("text-overflow:ellipsis", page)

    def test_the_page_has_a_narrow_screen_rule(self):
        """There was no `@media` block at all."""
        page = dashboard_server.render_html(_bare_payload())

        self.assertIn("@media (max-width:760px)", page)
        self.assertIn("@media (max-width:420px)", page)

    def test_a_long_unbroken_token_can_wrap(self):
        """A 400-character run with no spaces is what an `event_id` or a
        base64 blob looks like; without a break rule it sets the table's
        width and pushes every later column off-screen."""
        page = dashboard_server.render_html(_bare_payload())

        self.assertIn("td{overflow-wrap:anywhere}", page)
        self.assertIn(".att-body{color:#ffd7d5;overflow-wrap:anywhere}", page)


class TheServerRefusesAPortItCannotHonourTests(unittest.TestCase):
    """`main()`'s own configuration path (C116).

    Measured: nothing in this suite called `dashboard_server.main()`. Every
    test above drives `_Handler` through a server it builds itself, so the
    entrypoint's port handling — the block whose comment says refusing is the
    point, "quietly serving on the taken port instead is the 'did the unsafe
    thing and reported success' shape `src/cli.py` was written about" —
    had never executed.

    Found by mutation rather than by reading: replacing `resolve_port()` with
    `resolve_port() or DEFAULT_PORT`, which turns every refusal into a silent
    bind on 8765, left the whole suite green.

    Nothing here opens a socket. Every case returns before `_Server` is
    constructed, and `_Server` is replaced by a recorder that proves it.
    """

    def setUp(self):
        self._server_calls = []
        self._real_server = dashboard_server._Server
        self._real_environ = dict(os.environ)

        def _recording_server(address, handler):
            self._server_calls.append(address)
            raise _StopBeforeServing()

        dashboard_server._Server = _recording_server
        self.addCleanup(setattr, dashboard_server, "_Server", self._real_server)
        self.addCleanup(self._restore_environ)

    def _restore_environ(self):
        os.environ.clear()
        os.environ.update(self._real_environ)

    def _main(self, raw=None):
        os.environ.pop(dashboard_server.PORT_ENV_VAR, None)
        if raw is not None:
            os.environ[dashboard_server.PORT_ENV_VAR] = raw
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = dashboard_server.main(("dashboard_server.py",))
            except _StopBeforeServing:
                code = None
        return code, out.getvalue(), err.getvalue()

    def test_a_port_outside_the_range_is_refused_before_any_socket(self):
        """`99999` is the value that made this worth writing: `isdigit()`
        accepts it, `bind()` cannot, and `publish_control_tower.py` used to
        advertise it to the whole Notion workspace."""
        for raw in ("99999", "0", "65536", "-1", "abc", "80.5", ""):
            with self.subTest(port=raw):
                self._server_calls.clear()
                code, _, err = self._main(raw)

                if raw == "":
                    # Empty is "unset", not "wrong" — it must reach the bind.
                    self.assertIsNone(code)
                    self.assertEqual(
                        self._server_calls,
                        [("127.0.0.1", dashboard_server.DEFAULT_PORT)],
                    )
                    continue

                self.assertEqual(code, dashboard_server.CONFIG_ERROR_EXIT)
                self.assertEqual(self._server_calls, [], "opened a socket anyway")
                self.assertIn(dashboard_server.PORT_ENV_VAR, err)
                self.assertIn(repr(raw), err)

    def test_a_real_port_is_the_one_bound(self):
        """The antecedent. Without it a `main()` that refused everything
        would satisfy every assertion above."""
        code, _, err = self._main("9001")

        self.assertIsNone(code, err)
        self.assertEqual(self._server_calls, [("127.0.0.1", 9001)])

    def test_an_unset_variable_binds_the_default(self):
        code, _, err = self._main(None)

        self.assertIsNone(code, err)
        self.assertEqual(
            self._server_calls, [("127.0.0.1", dashboard_server.DEFAULT_PORT)]
        )

    def test_a_port_already_in_use_says_which_variable_moves_it(self):
        """The one refusal an operator hits without having mistyped anything.
        "Address already in use" alone leaves them nowhere to go."""
        def _busy(address, handler):
            raise OSError(98, "Address already in use")

        dashboard_server._Server = _busy

        code, _, err = self._main("9002")

        self.assertEqual(code, dashboard_server.CONFIG_ERROR_EXIT)
        self.assertIn("9002", err)
        self.assertIn(dashboard_server.PORT_ENV_VAR, err)

    def test_an_argument_is_refused_before_the_port_is_even_read(self):
        os.environ[dashboard_server.PORT_ENV_VAR] = "99999"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dashboard_server.main(("dashboard_server.py", "--port", "9000"))

        message = err.getvalue()

        self.assertEqual(code, dashboard_server.CONFIG_ERROR_EXIT)
        self.assertEqual(self._server_calls, [])
        # The argument, not the (also wrong) port: an operator who passed a
        # flag needs to be told the flag does not exist first.
        self.assertIn("--port", message)
        self.assertNotIn("포트 번호가 아닙니다", message)


class _StopBeforeServing(Exception):
    """Raised by the fake `_Server` so `main()` never reaches
    `serve_forever()`. A test that blocked there would hang the suite."""


if __name__ == "__main__":
    unittest.main()
