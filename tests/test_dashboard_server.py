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

import json
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
        for row in metrics["rows"]:
            with self.subTest(metric=row["values"]["key"]):
                value = row["values"]["value"]
                label = row["values"]["label"]
                self.assertIn(
                    f"<div class='kpi-value'>{value}</div>"
                    f"<div class='kpi-label'>{label}</div>",
                    page,
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

        self.assertIn("class='state bad'>BLOCKED", page)
        self.assertIn("class='state bad'>OPEN_BLOCKER", page)

    def test_a_missing_value_is_a_dash_and_never_a_blank(self):
        """Blank reads as "nothing to say". This project spends a great deal
        of effort keeping "no value" apart from "zero"."""
        self.put("E1")

        page = self.page()

        self.assertIn("<span class='nil'>—</span>", page)
        self.assertNotIn("<td>None</td>", page)


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
        return re.findall(r"<td class='state warn'>(.*?)</td>", page)

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
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                request = urllib.request.Request(self.url + "/", method=method)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=30)

                self.assertEqual(caught.exception.code, 405)

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

    def test_the_verdict_follows_the_attention_list(self):
        self.put("E1")

        self.assertIn("이상 없음", self.page(attention=[]))
        self.assertIn("ATTENTION 2건", self.page(attention=["a", "b"]))

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
        """`_BLOCKS` and `_RENDERERS` are two lists of the same six sections,
        and a name in one and not the other is a `KeyError` at request
        time — on the page an operator opened because something looked
        wrong."""
        self.assertEqual(
            [key for key, _title, _parity in dashboard_server._BLOCKS],
            list(dashboard_server._RENDERERS),
        )

    def test_the_control_tower_block_is_the_one_kept_for_comparison(self):
        """The page renders the Control Tower twice on purpose — as panels,
        and as the terminal's own text — so a person can check them against
        each other. Any *other* block marked that way would be hiding
        operational state inside a collapsed element."""
        parity = [key for key, _title, flag in dashboard_server._BLOCKS if flag]

        self.assertEqual(parity, ["CONTROL TOWER"])


if __name__ == "__main__":
    unittest.main()
