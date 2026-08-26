"""The Control Tower as a Notion page (C105).

Driven entirely through `InMemoryNotionTransport`, which grew block support
in the same Sprint, so none of this needs a live workspace — the rule docs
§57-64 sets for every other Notion test.

What these hold, in one sentence each:

    a zero is never a verdict          three different absences, three
                                       different sentences
    running it twice leaves one page   idempotency, the whole point of
                                       find-by-title
    a re-render replaces, not appends  the failure that doubles a page
    a truncated listing refuses        the failure that doubles a page
                                       *silently*
    nothing is invented                no fabricated business fact reaches
                                       Notion
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from controltower.notion_page import (  # noqa: E402
    MAX_CHILDREN_PER_APPEND,
    MAX_TABLE_ROWS,
    NOTE_MARKER,
    ROW_PAGE_MARKER,
    SILENT_AFTER_DAYS,
    ControlTowerPageError,
    build_control_tower_blocks,
    build_database_summary,
    build_project_note,
    build_project_row_blocks,
    find_child_page,
    publish,
    publish_database_summary,
    publish_project_notes,
    publish_project_rows,
)
from notion.transport import (  # noqa: E402
    InMemoryNotionTransport,
    NotionAPIError,
)

PARENT = "parent-page-1"


def _panel(key, title, status="SOURCED", columns=(), rows=(), note=""):
    return {
        "key": key,
        "title": title,
        "status": status,
        "note": note,
        "columns": list(columns),
        "rows": [{"values": dict(r), "evidence": [], "evidence_count": 0} for r in rows],
    }


def _payload(**overrides):
    model = {
        "schema_version": "1.2",
        "generated_at": "2026-08-26T12:00:00+09:00",
        "since": None,
        "until": None,
        "events_read": 2,
        "coverage": {
            "evidence_from": "2026-08-05",
            "evidence_to": "2026-08-10",
            "unreadable": 0,
            "duplicates": 0,
            "history_uncovered_from": None,
            "history_checked": True,
            "complete": True,
        },
        "unreadable": [],
        "panels": [
            _panel("METRICS", "KPI", columns=("label", "value", "evidence_count"),
                   rows=({"label": "기록된 Event", "value": 2, "evidence_count": 2},)),
            _panel("TEAMS", "팀별 진행현황", columns=("display_name", "events"),
                   rows=({"display_name": "COO", "events": 2},)),
            # More columns than `PANEL_LAYOUT` renders, on purpose: the
            # dropped-column notice can only be tested by a panel that has
            # some to drop, and the real model always does.
            _panel("PROJECTS", "Project",
                   columns=("project_id", "status", "teams", "events", "last_seen",
                            "days_idle", "blocker", "milestones", "sprint"),
                   rows=({"project_id": "ALPHA", "status": "IN_PROGRESS", "teams": ["COO"],
                          "events": 2, "last_seen": "2026-08-10T15:00:00+09:00", "days_idle": 3},)),
            _panel("DESKTOPS", "Desktop", columns=("source", "events"),
                   rows=({"source": "DESKTOP_4", "events": 2},)),
            _panel("RISKS", "Risk / Blocker", columns=("kind", "project_id")),
            _panel("ACTIVITY", "최근 활동", columns=("at", "source", "summary"),
                   rows=({"at": "2026-08-10T15:00:00+09:00", "source": "DESKTOP_4",
                          "summary": "s"},)),
            _panel("COMPLETIONS", "최근 완료", columns=("at", "source", "summary")),
            _panel("COMPANY_GOALS", "전사 목표", status="UNSOURCED", note="원천이 없다"),
            _panel("SPRINTS", "Sprint / Task", status="UNSOURCED", note="원천이 없다"),
            _panel("JUDGEMENTS", "COO 판단", status="UNSOURCED", note="자동화하지 않는다"),
        ],
    }
    payload = {
        "generated_at": "2026-08-26T12:00:00+09:00",
        "window": {"since": None, "until": None},
        "build_ms": 12,
        "attention": [],
        "blocks": [],
        "model": model,
        "model_error": None,
    }
    payload.update(overrides)
    return payload


def _all_text(blocks):
    """Every rich_text string in a block list, flattened."""
    out = []
    for block in blocks:
        body = block.get(block.get("type")) or {}
        for item in body.get("rich_text") or ():
            out.append((item.get("text") or {}).get("content", ""))
        for row in (body.get("children") or ()):
            for cell in (row.get("table_row") or {}).get("cells") or ():
                for item in cell:
                    out.append((item.get("text") or {}).get("content", ""))
    return out


class AZeroIsNeverAVerdictTests(unittest.TestCase):
    """`controltower/dashboard.py`'s rule, carried onto the page.

    Three different facts render as "no rows" and mean opposite things. A
    page that shows `0` for all three tells an operator that nothing
    happened, when the truth may be that nothing was ever asked.
    """

    def _risks_text(self, payload):
        blocks, _ = build_control_tower_blocks(payload)
        joined = "\n".join(_all_text(blocks))
        start = joined.index("Risk / Blocker")
        return joined[start : start + 400]

    def test_an_unsourced_layer_says_it_has_no_source(self):
        payload = _payload()
        for panel in payload["model"]["panels"]:
            if panel["key"] == "RISKS":
                panel["status"] = "UNSOURCED"
                panel["note"] = "이 계층에는 원천이 없다"
        self.assertIn("원천 없음", self._risks_text(payload))

    def test_a_period_filter_that_excludes_everything_says_so(self):
        payload = _payload(window={"since": "2026-01-01", "until": "2026-01-07"})
        text = self._risks_text(payload)
        self.assertIn("기간 내 Event 없음", text)
        self.assertIn("2026-01-01", text)

    def test_no_events_at_all_is_its_own_sentence(self):
        payload = _payload()
        payload["model"]["events_read"] = 0
        self.assertIn("아직 입력되지 않음", self._risks_text(payload))

    def test_events_exist_but_this_table_is_genuinely_empty(self):
        """The fourth case, and the only one that really is good news: there
        are Events, and no open blocker among them."""
        text = self._risks_text(_payload())
        self.assertIn("해당 없음", text)

    def test_the_four_sentences_are_all_different(self):
        """Guards the guard. Four branches that produced the same string
        would pass every test above and tell an operator nothing."""
        seen = set()
        empty = _payload()
        empty["model"]["events_read"] = 0
        filtered = _payload(window={"since": "2026-01-01", "until": "2026-01-07"})
        unsourced = _payload()
        for panel in unsourced["model"]["panels"]:
            if panel["key"] == "RISKS":
                panel["status"] = "UNSOURCED"
        for payload in (_payload(), empty, filtered, unsourced):
            seen.add(self._risks_text(payload)[:120])
        self.assertEqual(len(seen), 4, "each absence needs its own sentence")


class NothingIsInventedTests(unittest.TestCase):
    """The page may not answer a question the system cannot answer.

    Two of the mission's required sections have no source: whether an Event
    is real business work or an Engineering Probe, and which approval is
    blocking what. Both are easy to fake convincingly, which is exactly why
    they are pinned.
    """

    def test_probe_versus_real_work_is_reported_as_undecidable(self):
        blocks, _ = build_control_tower_blocks(_payload())
        joined = "\n".join(_all_text(blocks))
        self.assertIn("이 데이터는 실제 업무인가", joined)
        self.assertIn("구별하지 못한다", joined)
        self.assertIn("사람이 판단해야 한다", joined)

    def test_the_project_ids_are_listed_so_a_person_can_decide(self):
        """Refusing to classify is only useful with the raw material for
        someone who can."""
        blocks, _ = build_control_tower_blocks(_payload())
        self.assertIn("ALPHA", "\n".join(_all_text(blocks)))

    def test_approvals_and_next_work_are_not_derived(self):
        blocks, _ = build_control_tower_blocks(_payload())
        joined = "\n".join(_all_text(blocks))
        self.assertIn("승인 병목", joined)
        self.assertIn("자동화하지 않는다", joined)

    def test_an_empty_attention_list_does_not_claim_the_company_is_well(self):
        blocks, _ = build_control_tower_blocks(_payload(attention=[]))
        joined = "\n".join(_all_text(blocks))
        self.assertIn("ATTENTION 없음", joined)
        self.assertIn("회사가 잘 돌아간다는 뜻은 아니다", joined)

    def test_a_model_that_could_not_be_built_is_said_out_loud(self):
        """The page must never render a broken model as a calm one."""
        blocks, _ = build_control_tower_blocks(
            _payload(model={}, model_error="rollup raised")
        )
        self.assertIn("모델을 만들지 못했다", "\n".join(_all_text(blocks)))


class RunningItTwiceLeavesOnePageTests(unittest.TestCase):
    """Idempotency — the property the whole find-by-title design exists for.

    Measured against live Notion in the same Sprint: three publishes, one
    child page, 51 blocks each time, PROJECTS row count unchanged.
    """

    def setUp(self):
        self.transport = InMemoryNotionTransport()

    def test_the_first_publish_creates_the_page(self):
        result = publish(
            transport=self.transport, parent_page_id=PARENT, payload=_payload()
        )
        self.assertTrue(result.created)
        self.assertEqual(len(self.transport.child_pages), 1)

    def test_the_second_publish_updates_the_same_page(self):
        first = publish(
            transport=self.transport, parent_page_id=PARENT, payload=_payload()
        )
        second = publish(
            transport=self.transport, parent_page_id=PARENT, payload=_payload()
        )

        self.assertFalse(second.created)
        self.assertEqual(second.page_id, first.page_id)
        self.assertEqual(len(self.transport.child_pages), 1)

    def test_ten_publishes_still_leave_one_page_of_one_size(self):
        sizes = set()
        for _ in range(10):
            publish(
                transport=self.transport, parent_page_id=PARENT, payload=_payload()
            )
            page_id = find_child_page(self.transport, PARENT, "Control Tower")
            sizes.add(len(self.transport.list_block_children(page_id)))

        self.assertEqual(len(self.transport.child_pages), 1)
        self.assertEqual(
            len(sizes), 1, f"the body must not grow with each publish, saw {sizes}"
        )

    def test_a_re_render_archives_the_old_body_rather_than_appending(self):
        """The defect this ordering prevents: append-without-archive leaves
        the page saying two different things about the company."""
        publish(transport=self.transport, parent_page_id=PARENT, payload=_payload())
        second = publish(
            transport=self.transport, parent_page_id=PARENT, payload=_payload()
        )

        self.assertGreater(second.blocks_archived, 0)
        self.assertEqual(second.blocks_archived, second.blocks_written)
        self.assertTrue(
            all(b.get("archived") for b in self.transport.archived_blocks),
            "Notion archives rather than destroys, and so must the double",
        )

    def test_a_page_someone_else_made_is_not_adopted(self):
        """Exact title match. A fuzzy one would eventually rewrite the body
        of a page a person wrote."""
        self.transport.create_child_page(PARENT, "Control Tower Notes", [])
        result = publish(
            transport=self.transport, parent_page_id=PARENT, payload=_payload()
        )

        self.assertTrue(result.created)
        self.assertEqual(len(self.transport.child_pages), 2)


class ATruncatedListingRefusesToRenderTests(unittest.TestCase):
    """The failure that doubles a page *silently*.

    If `list_block_children()` stops early, every block it did not see
    survives the re-render and the page grows a second copy of itself. There
    is no safe way to continue, and continuing is the only option that
    cannot be undone.
    """

    def test_a_truncated_listing_raises_rather_than_doubling(self):
        transport = InMemoryNotionTransport()
        publish(transport=transport, parent_page_id=PARENT, payload=_payload())
        before = len(
            transport.list_block_children(find_child_page(transport, PARENT, "Control Tower"))
        )
        transport.block_children_truncated = True

        with self.assertRaises(ControlTowerPageError):
            publish(transport=transport, parent_page_id=PARENT, payload=_payload())

        after = len(
            transport.list_block_children(find_child_page(transport, PARENT, "Control Tower"))
        )
        self.assertEqual(after, before, "the refusal must leave the page untouched")


class TheApiLimitsAreRespectedTests(unittest.TestCase):
    """Notion refuses more than 100 children per append, and a table row is
    a block. A page that exceeds it fails at publish time, which is the
    worst moment to discover a layout choice."""

    def test_no_single_append_exceeds_the_limit(self):
        class CountingTransport(InMemoryNotionTransport):
            def __init__(self):
                super().__init__()
                self.append_sizes = []
                self.create_sizes = []

            def append_block_children(self, block_id, children):
                self.append_sizes.append(len(children))
                return super().append_block_children(block_id, children)

            def create_child_page(self, parent_page_id, title, children=()):
                self.create_sizes.append(len(children))
                return super().create_child_page(parent_page_id, title, children)

        transport = CountingTransport()
        payload = _payload()
        # 200 activity rows: far past both the append limit and MAX_TABLE_ROWS.
        for panel in payload["model"]["panels"]:
            if panel["key"] == "ACTIVITY":
                panel["rows"] = [
                    {"values": {"at": f"2026-08-{(i % 28) + 1:02d}", "source": "DESKTOP_1",
                                "summary": f"row {i}"}, "evidence": [], "evidence_count": 0}
                    for i in range(200)
                ]
        publish(transport=transport, parent_page_id=PARENT, payload=payload)

        for size in transport.append_sizes + transport.create_sizes:
            self.assertLessEqual(size, MAX_CHILDREN_PER_APPEND)

    def test_a_truncated_table_says_how_many_it_dropped(self):
        """Silent truncation reads as "this is everything" when it is not."""
        payload = _payload()
        for panel in payload["model"]["panels"]:
            if panel["key"] == "ACTIVITY":
                panel["rows"] = [
                    {"values": {"at": str(i), "source": "D", "summary": "s"},
                     "evidence": [], "evidence_count": 0}
                    for i in range(MAX_TABLE_ROWS + 5)
                ]
        blocks, _ = build_control_tower_blocks(payload)
        joined = "\n".join(_all_text(blocks))

        self.assertIn(f"{MAX_TABLE_ROWS + 5}건 중 {MAX_TABLE_ROWS}건만", joined)

    def test_columns_left_out_of_a_table_are_named(self):
        blocks, _ = build_control_tower_blocks(_payload())
        joined = "\n".join(_all_text(blocks))
        self.assertIn("이 표에 싣지 않은 열", joined)

    def test_a_none_value_renders_as_a_dash_not_the_word_none(self):
        payload = _payload()
        for panel in payload["model"]["panels"]:
            if panel["key"] == "PROJECTS":
                panel["rows"][0]["values"]["days_idle"] = None
        blocks, _ = build_control_tower_blocks(payload)
        self.assertNotIn("None", _all_text(blocks))


class TheCoverageWarningsSurviveTests(unittest.TestCase):
    """`coverage.complete` and `history_checked` are the two facts that turn
    every number above them into an estimate. They may not be dropped."""

    def test_an_unchecked_history_is_a_warning_not_a_silence(self):
        payload = _payload()
        payload["model"]["coverage"]["history_checked"] = False
        blocks, _ = build_control_tower_blocks(payload)
        self.assertIn("아무도 확인하지 않았다", "\n".join(_all_text(blocks)))

    def test_an_incomplete_coverage_names_where_it_stops(self):
        payload = _payload()
        payload["model"]["coverage"]["complete"] = False
        payload["model"]["coverage"]["history_uncovered_from"] = "2026-08-07"
        blocks, _ = build_control_tower_blocks(payload)
        joined = "\n".join(_all_text(blocks))
        self.assertIn("빈틈", joined)
        self.assertIn("2026-08-07", joined)


class ADatabaseCanCarryItsOwnSummaryTests(unittest.TestCase):
    """C106: the highest-visibility surface this integration has.

    Measured on the live workspace: the integration can see exactly **one**
    top-level object, the PROJECTS database. So the description -- the
    paragraph Notion renders under the database title -- is the first and,
    without navigating, the only thing a person reads when they open Notion
    at all. It was empty, and nothing in this repository could write it.

    Also measured: the endpoint is permitted for this token (a deliberately
    malformed body answered 400, not 403), and one item caps at 2,000
    characters.
    """

    def _text(self, payload=None):
        return "".join(
            i["text"]["content"]
            for i in build_database_summary(payload or _payload())
        )

    def test_the_summary_names_the_attention_count(self):
        text = self._text(_payload(attention=["a", "b", "c"]))
        self.assertIn("주의 3건", text)

    def test_an_empty_attention_list_does_not_claim_the_company_is_well(self):
        """The same refusal the page makes. A summary that drops the caveat
        its full view carries is the confident half of an honest report."""
        text = self._text()
        self.assertIn("ATTENTION 없음", text)
        self.assertIn("회사가 잘 돌아간다는 뜻은 아니다", text)

    def test_the_probe_caveat_survives_summarising(self):
        """The one sentence that must never be dropped for brevity."""
        self.assertIn("구별하지 못한다", self._text())

    def test_it_points_at_where_the_full_view_is(self):
        text = self._text()
        self.assertIn("COMPANY_OPS", text)
        self.assertIn("Control Tower", text)

    def test_it_says_it_does_not_refresh_itself(self):
        self.assertIn("스스로 갱신되지 않는다", self._text())

    def test_no_events_at_all_is_not_reported_as_zero(self):
        payload = _payload()
        payload["model"]["events_read"] = 0
        text = self._text(payload)
        self.assertIn("아직 입력되지 않음", text)

    def test_a_period_filter_that_excludes_everything_says_so(self):
        payload = _payload(window={"since": "2026-01-01", "until": "2026-01-07"})
        payload["model"]["events_read"] = 0
        text = self._text(payload)
        self.assertIn("기간 내 Event 없음", text)
        self.assertIn("2026-01-01", text)

    def test_those_two_absences_are_different_sentences(self):
        empty = _payload()
        empty["model"]["events_read"] = 0
        filtered = _payload(window={"since": "2026-01-01", "until": "2026-01-07"})
        filtered["model"]["events_read"] = 0
        self.assertNotEqual(self._text(empty), self._text(filtered))

    def test_every_item_stays_under_notions_cap(self):
        """Measured against the live API: `description[0].text.content.length
        should be <= 2000`. A long ATTENTION list must split, not fail."""
        payload = _payload(attention=["엄청나게 긴 경보 " * 200])
        for item in build_database_summary(payload):
            self.assertLessEqual(len(item["text"]["content"]), 2000)

    def test_publishing_it_replaces_rather_than_appends(self):
        transport = InMemoryNotionTransport()
        publish_database_summary(
            transport=transport, database_id="DB-1", payload=_payload()
        )
        first = list(transport.database_description)
        publish_database_summary(
            transport=transport, database_id="DB-1", payload=_payload()
        )

        self.assertEqual(transport.database_description, first)

    def test_it_never_touches_the_schema(self):
        """The description and `properties` share one PATCH endpoint. A
        caller meaning to write one sentence must not redefine a column."""
        transport = InMemoryNotionTransport(initial_properties={"Name": {"title": {}}})
        publish_database_summary(
            transport=transport, database_id="DB-1", payload=_payload()
        )
        self.assertEqual(
            transport.retrieve_database("DB-1")["properties"], {"Name": {"title": {}}}
        )


class AProjectRowCarriesItsOwnEvidenceTests(unittest.TestCase):
    """C106: clicking a project in Notion used to show an empty page.

    The row's *properties* already carry Status, Owner and Last Event -- the
    Runner writes those every sync. What they cannot carry is **why**: which
    Events produced that state and which files they came from. That is what
    a person asks for the moment they doubt a status.
    """

    def setUp(self):
        self.transport = InMemoryNotionTransport()
        self.client = _StubClient(self.transport)

    def test_the_body_carries_status_events_and_evidence(self):
        blocks = build_project_row_blocks(_payload(), "ALPHA")
        text = "\n".join(_all_text(blocks))

        self.assertIn("IN_PROGRESS", text)
        self.assertIn("이 Project의 Event", text)
        self.assertIn("Evidence", text)

    def test_a_project_with_no_source_gets_no_blocks(self):
        self.assertEqual(build_project_row_blocks(_payload(), "NOT_A_PROJECT"), [])

    # --- the branches a project with actual work takes -------------------
    #
    # C115, from a full-suite coverage sweep: `notion_page.py` lines 871-885
    # and 895-904 — the `else:` halves of both sections below — were never
    # executed by any test. Every existing case reaches the empty branch,
    # because `_payload()`'s ACTIVITY rows carry no `project_id` (so the
    # filter never matches) and its PROJECTS row carries no `evidence`.
    #
    # That is the inverse of the usual coverage gap: what went untested is
    # not an error path but the **normal** one. These blocks are what
    # `publish_project_rows()` writes into a real PROJECTS row page in the
    # live Workspace for every project that has done anything, so a
    # regression here is invisible locally and lands in Notion.
    #
    # Measured before pinning: both render correctly today. Nothing is fixed
    # here — the point is that nothing was watching.

    @staticmethod
    def _busy_payload(activity_rows, evidence, evidence_count):
        payload = _payload()
        panels = {p["key"]: p for p in payload["model"]["panels"]}
        panels["ACTIVITY"]["rows"] = [
            {"values": dict(values), "evidence": [], "evidence_count": 0}
            for values in activity_rows
        ]
        project = panels["PROJECTS"]["rows"][0]
        project["evidence"] = list(evidence)
        project["evidence_count"] = evidence_count
        return payload

    @staticmethod
    def _activity(count, project_id="ALPHA"):
        return [
            {
                "at": f"2026-08-10T{index % 24:02d}:00:00+09:00",
                "source": "DESKTOP_4",
                "event_type": "PROGRESS",
                "summary": f"summary-{index}",
                "event_id": f"E{index}",
                "project_id": project_id,
            }
            for index in range(count)
        ]

    @staticmethod
    def _evidence(count):
        return [
            {
                "path": f"processed/e{index}.json",
                "event_id": f"E{index}",
                "at": "2026-08-10T15:00:00+09:00",
            }
            for index in range(count)
        ]

    def _tables(self, blocks):
        return [b for b in blocks if b.get("type") == "table"]

    def test_a_projects_own_events_are_rendered_as_a_table(self):
        payload = self._busy_payload(self._activity(3), self._evidence(2), 2)

        blocks = build_project_row_blocks(payload, "ALPHA")
        tables = self._tables(blocks)
        text = "\n".join(_all_text(blocks))

        self.assertEqual(len(tables), 1)
        table = tables[0]["table"]
        # Five columns, and one row per event plus the header.
        self.assertEqual(table["table_width"], 5)
        self.assertEqual(len(table["children"]), 3 + 1)
        self.assertIn("summary-0", text)
        self.assertIn("E2", text)
        # Nothing was dropped, so nothing claims anything was.
        self.assertNotIn("만 표시했다", text)

    def test_only_this_projects_events_reach_its_own_page(self):
        """The filter is the whole reason this page is per-project. A row
        page carrying another project's events would be worse than an empty
        one — it reads as evidence."""
        payload = self._busy_payload(
            self._activity(2) + self._activity(2, project_id="BETA"),
            self._evidence(1),
            1,
        )

        blocks = build_project_row_blocks(payload, "ALPHA")
        table = self._tables(blocks)[0]["table"]

        self.assertEqual(len(table["children"]), 2 + 1)

    def test_more_events_than_the_table_holds_says_how_many_it_dropped(self):
        """`MAX_TABLE_ROWS` is a cap, and a cap that does not announce itself
        turns "20 events" into what a reader believes is all of them."""
        payload = self._busy_payload(
            self._activity(MAX_TABLE_ROWS + 5), self._evidence(1), 1
        )

        blocks = build_project_row_blocks(payload, "ALPHA")
        table = self._tables(blocks)[0]["table"]
        text = "\n".join(_all_text(blocks))

        self.assertEqual(len(table["children"]), MAX_TABLE_ROWS + 1)
        self.assertIn(f"{MAX_TABLE_ROWS + 5}건 중 {MAX_TABLE_ROWS}건만 표시했다", text)

    def test_evidence_files_are_listed_one_per_bullet(self):
        payload = self._busy_payload(self._activity(1), self._evidence(3), 3)

        blocks = build_project_row_blocks(payload, "ALPHA")
        bullets = [b for b in blocks if b.get("type") == "bulleted_list_item"]
        text = "\n".join(_all_text(blocks))

        self.assertEqual(len(bullets), 3)
        self.assertIn("processed/e0.json", text)
        # path · event_id · at — all three, so a reader can go find the file.
        self.assertIn("E0", text)
        self.assertIn("2026-08-10T15:00:00+09:00", text)
        # The payload carried every ref it counted; no shortfall to report.
        self.assertNotIn("만 실렸다", text)

    def test_the_evidence_note_reports_the_true_total_not_the_listed_count(self):
        """`evidence_count` is the true number; the list is capped upstream
        at `dashboard.EVIDENCE_IN_PAYLOAD`. The note has to name both, or the
        page reads as "this project produced 5 files"."""
        payload = self._busy_payload(self._activity(1), self._evidence(5), 31)

        blocks = build_project_row_blocks(payload, "ALPHA")
        bullets = [b for b in blocks if b.get("type") == "bulleted_list_item"]
        text = "\n".join(_all_text(blocks))

        self.assertEqual(len(bullets), 5)
        self.assertIn("증거 31건 중 5건만 실렸다", text)

    def test_the_first_block_says_it_is_machine_written(self):
        """The permission slip. Without it the tool cannot tell its own work
        from a person's, and would eventually delete somebody's notes."""
        blocks = build_project_row_blocks(_payload(), "ALPHA")
        self.assertIn(ROW_PAGE_MARKER, _all_text(blocks)[0])

    def test_a_hand_written_row_is_skipped_and_named(self):
        """Measured live before it was tested here: a paragraph typed into a
        row survived a publish, and the run named the row it left alone."""
        self.client.add_row("ALPHA", "row-alpha")
        self.transport.append_block_children(
            "row-alpha",
            [{"object": "block", "type": "paragraph",
              "paragraph": {"rich_text": [{"type": "text",
                                           "text": {"content": "사람이 쓴 메모"}}]}}],
        )

        result = publish_project_rows(
            transport=self.transport, client=self.client, payload=_payload()
        )

        self.assertEqual(result.skipped_hand_written, ("ALPHA",))
        self.assertEqual(result.written, ())
        kept = _all_text(self.transport.list_block_children("row-alpha"))
        self.assertIn("사람이 쓴 메모", "\n".join(kept))

    def test_a_row_this_tool_wrote_is_re_rendered(self):
        self.client.add_row("ALPHA", "row-alpha")
        first = publish_project_rows(
            transport=self.transport, client=self.client, payload=_payload()
        )
        second = publish_project_rows(
            transport=self.transport, client=self.client, payload=_payload()
        )

        self.assertEqual(first.written, ("ALPHA",))
        self.assertEqual(second.written, ("ALPHA",))
        self.assertGreater(second.blocks_archived, 0)
        self.assertEqual(
            len(self.transport.list_block_children("row-alpha")),
            len(build_project_row_blocks(_payload(), "ALPHA")),
            "the body must not grow with each publish",
        )

    def test_a_row_with_no_source_is_left_completely_alone(self):
        """Writing "no evidence" into a row this system never produced would
        be claiming authority over somebody else's data."""
        self.client.add_row("SOMEONE_ELSES", "row-x")
        result = publish_project_rows(
            transport=self.transport, client=self.client, payload=_payload()
        )

        self.assertIn("SOMEONE_ELSES", result.skipped_unsourced)
        self.assertEqual(self.transport.list_block_children("row-x"), [])

    def test_a_child_page_is_never_archived(self):
        """The Control Tower page lives inside the COMPANY_OPS row. Treating
        it as body text would take the whole Control Tower with it."""
        self.client.add_row("ALPHA", "row-alpha")
        self.transport.create_child_page("row-alpha", "Control Tower", [])
        publish_project_rows(
            transport=self.transport, client=self.client, payload=_payload()
        )

        kinds = [b["type"] for b in self.transport.list_block_children("row-alpha")]
        self.assertIn("child_page", kinds)

    def test_a_truncated_listing_refuses_rather_than_doubling(self):
        self.client.add_row("ALPHA", "row-alpha")
        publish_project_rows(
            transport=self.transport, client=self.client, payload=_payload()
        )
        before = len(self.transport.list_block_children("row-alpha"))
        self.transport.block_children_truncated = True

        with self.assertRaises(ControlTowerPageError):
            publish_project_rows(
                transport=self.transport, client=self.client, payload=_payload()
            )
        self.assertEqual(
            len(self.transport.list_block_children("row-alpha")), before
        )


class _StubClient:
    """The two things `publish_project_rows()` asks a NotionClient for."""

    def __init__(self, transport):
        self._transport = transport
        self._rows = []

    def add_row(self, project_id, page_id, notes=None):
        properties = {
            "Project ID": {
                "type": "rich_text",
                "rich_text": [{"plain_text": project_id}],
            }
        }
        if notes is not None:
            properties["Notes"] = {
                "type": "rich_text",
                "rich_text": [{"plain_text": notes}] if notes else [],
            }
        self._rows.append({"id": page_id, "properties": properties})

    def list_pages(self):
        return list(self._rows)

    def update_project(self, page_id, properties):
        row = next(r for r in self._rows if r["id"] == page_id)
        for name, value in properties.items():
            # Stored the way Notion hands it back: `plain_text`, not the
            # `text.content` the request carried. A stub that echoed the
            # request shape would let a reader that only understands one of
            # them pass here and fail live.
            row["properties"][name] = {
                "type": "rich_text",
                "rich_text": [
                    {"plain_text": (i.get("text") or {}).get("content", "")}
                    for i in value.get("rich_text") or ()
                ],
            }
        return row

    def notes_of(self, page_id):
        row = next(r for r in self._rows if r["id"] == page_id)
        return "".join(
            i.get("plain_text", "")
            for i in (row["properties"].get("Notes") or {}).get("rich_text") or ()
        )


class TheTableViewSaysWhichProjectNeedsSomeoneTests(unittest.TestCase):
    """C107: the PROJECTS **table** was silent about attention.

    The row properties the Runner syncs (docs/04 §43) answer "what state is
    this project in". They do not answer "does it need me" — `days_idle`,
    the blocker age and the Event count are Control Tower derivations that
    lived nowhere in Notion, so a person had to open each project to find
    out. The table view is what Notion shows when you open a database, and
    it said nothing.

    `Notes` is the column for it, and it is genuinely unassigned: docs/04
    §43's automated set does not list it, and neither do §44 (COO judgement)
    or §45 (CEO authority). Measured on this workspace it is empty on every
    row and no code in this repository writes it.

    Unassigned is not "ours", which is what `NOTE_MARKER` is for.
    """

    def setUp(self):
        self.client = _StubClient(InMemoryNotionTransport())

    def test_a_quiet_project_is_flagged(self):
        note = build_project_note(_payload(), "ALPHA")
        self.assertTrue(note.startswith(NOTE_MARKER))
        self.assertIn("3일째 조용함", note)
        self.assertIn("Event 2건", note)
        self.assertIn("IN_PROGRESS", note)

    def test_the_quiet_threshold_matches_ops_status(self):
        """Restated rather than imported (a library may not import an
        entrypoint), so it is pinned against its source instead. Two views
        disagreeing about what counts as quiet is worse than either
        threshold."""
        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"SILENT_AFTER_DAYS = {SILENT_AFTER_DAYS}", source)

    def test_a_project_idle_below_the_threshold_is_not_warned_about(self):
        payload = _payload()
        for panel in payload["model"]["panels"]:
            if panel["key"] == "PROJECTS":
                panel["rows"][0]["values"]["days_idle"] = 1
        note = build_project_note(payload, "ALPHA")
        self.assertIn("1일째 조용함", note)
        self.assertNotIn("\u26a0", note)

    def test_a_blocker_outranks_the_idle_count(self):
        """A blocked project is not merely quiet, and the column has room
        for one fact."""
        payload = _payload()
        for panel in payload["model"]["panels"]:
            if panel["key"] == "PROJECTS":
                panel["rows"][0]["values"]["blocker"] = "API 키 발급 대기"
                panel["rows"][0]["values"]["days_blocked"] = 5
        note = build_project_note(payload, "ALPHA")
        self.assertIn("Blocker", note)
        self.assertIn("API 키 발급 대기", note)
        self.assertNotIn("조용함", note)

    def test_a_project_with_no_source_gets_no_note(self):
        self.assertIsNone(build_project_note(_payload(), "NOT_A_PROJECT"))

    def test_a_hand_written_note_is_skipped_and_named(self):
        """Measured live before it was tested here: a COO memo typed into
        the column survived a publish, and the run named the row."""
        self.client.add_row("ALPHA", "row-alpha", notes="COO 메모: 보류 결정됨")

        written, skipped = publish_project_notes(
            client=self.client, payload=_payload()
        )

        self.assertEqual(skipped, ("ALPHA",))
        self.assertEqual(written, ())
        self.assertIn("COO 메모", self.client.notes_of("row-alpha"))

    def test_an_empty_note_is_adopted(self):
        self.client.add_row("ALPHA", "row-alpha", notes="")
        written, skipped = publish_project_notes(client=self.client, payload=_payload())

        self.assertEqual(written, ("ALPHA",))
        self.assertEqual(skipped, ())
        self.assertTrue(self.client.notes_of("row-alpha").startswith(NOTE_MARKER))

    def test_a_note_this_tool_wrote_is_refreshed(self):
        self.client.add_row("ALPHA", "row-alpha", notes="")
        publish_project_notes(client=self.client, payload=_payload())
        first = self.client.notes_of("row-alpha")

        payload = _payload(generated_at="2026-09-01T09:00:00+09:00")
        publish_project_notes(client=self.client, payload=payload)
        second = self.client.notes_of("row-alpha")

        self.assertTrue(second.startswith(NOTE_MARKER))
        self.assertNotEqual(first, second, "the column must follow the data")

    def test_an_unsourced_row_is_never_touched(self):
        self.client.add_row("SOMEONE_ELSES", "row-x", notes="")
        written, skipped = publish_project_notes(client=self.client, payload=_payload())

        self.assertEqual(written, ())
        self.assertEqual(skipped, ())
        self.assertEqual(self.client.notes_of("row-x"), "")

    def test_the_note_stays_short_enough_to_read_sideways(self):
        """It is a table cell. A blocker sentence of any length must not turn
        the column into a wall."""
        payload = _payload()
        for panel in payload["model"]["panels"]:
            if panel["key"] == "PROJECTS":
                panel["rows"][0]["values"]["blocker"] = "매우 긴 블로커 설명 " * 60
                panel["rows"][0]["values"]["days_blocked"] = 2
        self.assertLessEqual(len(build_project_note(payload, "ALPHA")), 300)


class ThePageSaysHowToReachTheLiveDashboardTests(unittest.TestCase):
    """C107: the page described the Dashboard and never said where it is.

    Deliberately not a hyperlink. `dashboard_server.py` binds 127.0.0.1 and
    is not configurable — its own docstring says why — so the address opens
    the Control Tower **only on the machine running the server**. A
    clickable link would be a dead link for every other reader, and a page
    that hands someone a dead link has told them something false.
    """

    URL = "http://127.0.0.1:8765/"

    def test_the_address_reaches_the_page(self):
        blocks, _ = build_control_tower_blocks(_payload(), dashboard_url=self.URL)
        self.assertIn(self.URL, "\n".join(_all_text(blocks)))

    def test_the_loopback_limit_is_stated_beside_it(self):
        blocks, _ = build_control_tower_blocks(_payload(), dashboard_url=self.URL)
        text = "\n".join(_all_text(blocks))
        self.assertIn("서버를 켠 그 컴퓨터에서만", text)
        self.assertIn("다른 기기에서는 열리지 않는다", text)

    def test_the_commands_are_named(self):
        blocks, _ = build_control_tower_blocks(_payload(), dashboard_url=self.URL)
        text = "\n".join(_all_text(blocks))
        for command in (
            "python dashboard_server.py",
            "python ops_status.py",
            "python publish_control_tower.py",
        ):
            self.assertIn(command, text)

    def test_the_description_carries_the_address_too(self):
        text = "".join(
            i["text"]["content"]
            for i in build_database_summary(_payload(), dashboard_url=self.URL)
        )
        self.assertIn(self.URL, text)
        self.assertIn("서버를 켠 컴퓨터에서만", text)

    def test_without_an_address_the_page_still_renders(self):
        """A caller that cannot say where the server is must not lose the
        rest of the page over it."""
        blocks, _ = build_control_tower_blocks(_payload())
        text = "\n".join(_all_text(blocks))
        self.assertIn("실시간 화면(Dashboard)에 가려면", text)
        self.assertNotIn("http://", text)


def _links_in(blocks):
    """Every (text, href) pair in a block list."""
    out = []
    for block in blocks:
        body = block.get(block.get("type")) or {}
        for item in body.get("rich_text") or ():
            url = ((item.get("text") or {}).get("link") or {}).get("url")
            if url:
                out.append(((item.get("text") or {}).get("content", ""), url))
    return out


class AProjectRowHasAWayBackOutTests(unittest.TestCase):
    """C108: a detail page that never said what it was a detail of.

    Someone who clicked into a project to check one number was one click
    from the evidence and no clicks from anything else — the company-wide
    view and the live screen were both elsewhere, reachable only from
    memory.

    Verified against the live API before it was used here: a
    `text.link.url` round-trips and reads back as `href`. Worth measuring
    rather than assuming, because a link that silently degrades to plain
    text is a pointer a reader cannot follow and cannot tell is broken.
    """

    CT = "https://app.notion.com/p/Control-Tower-abc123"
    DASH = "http://127.0.0.1:8765/"

    def test_the_control_tower_link_is_a_real_link(self):
        blocks = build_project_row_blocks(
            _payload(), "ALPHA", control_tower_url=self.CT
        )
        links = _links_in(blocks)

        self.assertEqual([url for _text, url in links], [self.CT])
        self.assertIn("Control Tower", links[0][0])

    def test_the_dashboard_address_is_not_a_link(self):
        """Deliberate. 127.0.0.1 opens only on the machine running the
        server, so a clickable link would be dead for every other reader."""
        blocks = build_project_row_blocks(
            _payload(), "ALPHA", dashboard_url=self.DASH
        )
        text = "\n".join(_all_text(blocks))

        self.assertIn(self.DASH, text)
        self.assertEqual(_links_in(blocks), [])
        self.assertIn("그 컴퓨터에서만 열린다", text)

    def test_neither_address_still_renders_the_evidence(self):
        """A caller that cannot say where either screen is must not lose the
        project's own evidence over it."""
        blocks = build_project_row_blocks(_payload(), "ALPHA")
        text = "\n".join(_all_text(blocks))

        self.assertIn("이 Project의 Event", text)
        self.assertIn("Evidence", text)
        self.assertEqual(_links_in(blocks), [])

    def test_the_links_reach_the_row_through_publish(self):
        transport = InMemoryNotionTransport()
        client = _StubClient(transport)
        client.add_row("ALPHA", "row-alpha", notes="")

        publish_project_rows(
            transport=transport,
            client=client,
            payload=_payload(),
            control_tower_url=self.CT,
            dashboard_url=self.DASH,
        )

        written = transport.list_block_children("row-alpha")
        self.assertEqual([url for _t, url in _links_in(written)], [self.CT])

    def test_an_unsourced_row_gets_no_link_either(self):
        transport = InMemoryNotionTransport()
        client = _StubClient(transport)
        client.add_row("SOMEONE_ELSES", "row-x", notes="")

        publish_project_rows(
            transport=transport,
            client=client,
            payload=_payload(),
            control_tower_url=self.CT,
        )

        self.assertEqual(transport.list_block_children("row-x"), [])


class TheAddressOfAnExistingPageIsRecoveredTests(unittest.TestCase):
    """`create_child_page()` hands the url back once and never again.

    Every run after the first takes the update branch, so a `publish()` that
    only knew the address on creation could never link to the page it had
    just written — which is every run in production.

    Retrieved rather than constructed: building the address from the id
    works today and is a guess about Notion's URL format. Asking is not.
    """

    def test_publish_returns_the_url_on_update_too(self):
        transport = InMemoryNotionTransport()
        first = publish(
            transport=transport, parent_page_id=PARENT, payload=_payload()
        )
        second = publish(
            transport=transport, parent_page_id=PARENT, payload=_payload()
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertIsNotNone(second.url)
        self.assertEqual(second.url, first.url)

    def test_a_transport_that_cannot_retrieve_still_publishes(self):
        """Best effort: a page that renders is worth more than a link."""

        class NoRetrieve(InMemoryNotionTransport):
            def retrieve_page(self, page_id):
                raise NotImplementedError("cannot retrieve")

        transport = NoRetrieve()
        publish(transport=transport, parent_page_id=PARENT, payload=_payload())
        second = publish(
            transport=transport, parent_page_id=PARENT, payload=_payload()
        )

        self.assertIsNone(second.url)
        self.assertGreater(second.blocks_written, 0)


class TheDefaultNotionColumnsAreNotFilledTests(unittest.TestCase):
    """`Date` and `Tags` stay empty, and that is the decision (C108).

    docs/13 §5 settles what they are: PROJECTS was created with Notion's
    default template, and `Date` / `Notes` / `Tags` are the three leftovers
    from it — recorded there as "무해", neither required by docs/04 §8's
    eleven-property contract nor reserved by §44/§45.

    Leftover is not the same as available, and the two differ:

        Date   `Last Updated` and `Completed Date` already carry the dates
               this system knows. A third date column with no defined
               meaning is not more information, it is an ambiguity a reader
               has to resolve every time.
        Tags   a `multi_select` with no options. Filling it means creating
               options, which is a schema change to a database this project
               does not own — and the classification would only restate what
               `Notes` says in words.

    `Notes` was taken (C107) because it is free text with nothing else
    claiming it and it answers a question no property answered. The other
    two are left alone, and this test is where that reasoning fails loudly
    if someone later fills them without revisiting it.
    """

    def test_nothing_in_the_publisher_writes_date_or_tags(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "src" / "controltower" / "notion_page.py"
        ).read_text(encoding="utf-8")

        for column in ('"Date"', '"Tags"'):
            with self.subTest(column=column):
                self.assertNotIn(column, source)

    def test_publishing_notes_touches_no_other_property(self):
        transport = InMemoryNotionTransport()
        client = _StubClient(transport)
        client.add_row("ALPHA", "row-alpha", notes="")

        publish_project_notes(client=client, payload=_payload())

        written = next(r for r in client.list_pages() if r["id"] == "row-alpha")
        self.assertEqual(
            sorted(written["properties"]), ["Notes", "Project ID"],
            "only the column this project claimed may be written",
        )

    def test_the_reason_is_recorded_where_someone_will_look(self):
        """A decision that lives only in a commit message is a decision the
        next person re-takes from scratch."""
        backlog = (Path(__file__).resolve().parents[1] / "BACKLOG.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("docs/13", backlog)
        self.assertIn("Tags", backlog)


class AnAttentionMessageIsRedactedBeforeItLeavesTheMachineTests(unittest.TestCase):
    """C109 — P1. The one input that does not come through `to_payload()`.

    Everything else this module renders is redacted upstream: `to_payload()`
    applies `redact(one_line(...))` to every authored field, and this
    module's docstring leans on that. **The ATTENTION list does not pass
    through it.** `ops_status.py` builds it and hands it to
    `dashboard_server.gather()` beside the model.

    `ops_status.py` does not redact it either, and that is deliberate —
    `_authored()`'s docstring records the trade: the sink applies
    `one_line()` to every message but not `redact()`, because "over-redacting
    a path an operator has to go and open costs more than it protects."

    Right for a terminal on the operator's own machine. Wrong the moment
    this module gave those same strings a second sink that **leaves** the
    machine, and nothing re-weighed it when that happened.

    Measured, not argued: of 67 `attention.append/extend` sites in
    `ops_status.py`, **zero** call `redact()`; 21 interpolate authored
    fields; and `ops_status.py:2539` interpolates raw `event_id`s, which the
    Event's author sets and `validate_event()` only type-checks.
    """

    TOKEN = "ntn_" + "A" * 40

    def test_a_secret_shaped_attention_line_does_not_reach_a_block(self):
        payload = _payload(attention=[f"경보에 토큰이 섞였다: {self.TOKEN}"])
        blocks, _ = build_control_tower_blocks(payload)
        text = "\n".join(_all_text(blocks))

        self.assertNotIn(self.TOKEN, text)
        self.assertIn("[REDACTED]", text)

    def test_it_does_not_reach_the_database_description_either(self):
        """The description quotes the first ATTENTION item, and it is the
        more exposed of the two — it renders under the database title, which
        is the first thing anyone opening this workspace reads."""
        payload = _payload(attention=[f"토큰: {self.TOKEN}"])
        text = "".join(i["text"]["content"] for i in build_database_summary(payload))

        self.assertNotIn(self.TOKEN, text)
        self.assertIn("[REDACTED]", text)

    def test_a_newline_in_an_attention_line_cannot_forge_structure(self):
        payload = _payload(attention=["첫 줄\n## 가짜 제목\n두 번째 줄"])
        blocks, _ = build_control_tower_blocks(payload)

        for run in _all_text(blocks):
            self.assertNotIn(
                "\n", run, "a run with a real newline can restructure the page"
            )

    def test_a_model_error_is_redacted_too(self):
        """An exception message is authored text as surely as a blocker is —
        it routinely quotes the value that broke."""
        payload = _payload(model={}, model_error=f"parse failed on {self.TOKEN}")
        blocks, _ = build_control_tower_blocks(payload)
        text = "\n".join(_all_text(blocks))

        self.assertNotIn(self.TOKEN, text)
        self.assertIn("[REDACTED]", text)

    def test_an_ordinary_attention_line_survives_intact(self):
        """The control. Over-redaction is the right direction at this sink,
        but a filter that mangles ordinary operational text would make the
        page useless — and this project has already measured that an alert
        nobody reads is worse than no alert."""
        message = "Runner가 9.1일째 실행되지 않았다 (마지막 실행 2026-08-17T12:07:42+09:00)"
        payload = _payload(attention=[message])
        blocks, _ = build_control_tower_blocks(payload)

        self.assertIn(message, "\n".join(_all_text(blocks)))

    def test_the_boundary_is_the_module_not_its_callers(self):
        """Structural. The fix must not be "every caller redacts first" —
        that is the shape `transport._error_detail()` already rejected for
        its own output, and sixty-seven call sites is exactly the number
        that guarantees one of them forgets.
        """
        source = (
            Path(__file__).resolve().parents[1]
            / "src" / "controltower" / "notion_page.py"
        ).read_text(encoding="utf-8")

        self.assertIn("from oplog import one_line, redact", source)
        self.assertIn("def _safe(", source)

    def test_ops_status_still_does_not_redact_its_own_sink(self):
        """The other half of the decision, pinned so the fix stays where it
        belongs. Redacting inside `ops_status.py` would break the terminal
        view it was deliberately designed for — the operator there needs the
        path they are about to open.

        If this ever fails, someone moved the redaction upstream and the
        trade recorded in `_authored()` needs re-reading, not patching.
        """
        source = (Path(__file__).resolve().parents[1] / "ops_status.py").read_text(
            encoding="utf-8"
        )
        sink = source[source.index('print("ATTENTION")') :][:1200]

        self.assertIn("one_line(", sink)
        self.assertNotIn("redact(", sink)


NOTION_BLOCK_TEXT = (
    "NOTION — Retry Queue\n"
    "------------------------------------------------------------\n"
    "  대기 중 Event       : 0\n"
    "  Dashboard 밀린 기록 : 0\n"
    "  마지막 Notion 반영  : Row 생성 0 / 갱신 2 / 넘어감 14 (run 2026-08-26T10:53:38+09:00)\n"
)


class TwoDifferentSyncsShareOneWordTests(unittest.TestCase):
    """C110: the page said when *it* was written and nothing about the rows.

    Two things are called "sync" here and conflating them is how someone
    reads "up to date" off a page that is not:

        Runner Notion Sync   writes Event state onto the PROJECTS **rows**,
                             on the Runner's schedule.
        this page's publish  rewrites this page, only when a person runs
                             `publish_control_tower.py`.

    The first can be broken for days while the second keeps succeeding — the
    page renders perfectly and the row data underneath it is stale. Measured
    on this deployment: the page is current to the minute and the Runner's
    Notion step has **never once been attempted** with these credentials.

    The facts come from `gather()`'s captured NOTION block, which is already
    in the payload — never a second derivation.
    """

    def _text(self, payload=None):
        blocks, _ = build_control_tower_blocks(payload or _payload(blocks=[
            {"key": "NOTION", "title": "NOTION", "parity": 0,
             "text": NOTION_BLOCK_TEXT, "attention": 0}
        ]))
        return "\n".join(_all_text(blocks))

    def test_the_section_exists(self):
        self.assertIn("동기화 상태", self._text())

    def test_it_says_when_this_page_was_written(self):
        text = self._text()
        self.assertIn("이 페이지가 쓰인 시각", text)
        self.assertIn("2026-08-26T12:00:00+09:00", text)

    def test_it_carries_the_runner_side_facts(self):
        text = self._text()
        self.assertIn("대기 중 Event", text)
        self.assertIn("마지막 Notion 반영", text)

    def test_it_names_the_two_as_different(self):
        """The whole point. A reader who takes one timestamp for the other
        is exactly the failure this section exists to prevent."""
        text = self._text()
        self.assertIn("두 시각은 다른 것이다", text)
        self.assertIn("한쪽이 며칠 멈춰 있어도", text)

    def test_the_terminal_heading_is_not_repeated(self):
        """The captured block starts with its own title and rule. Keeping
        them would put two headings on one section."""
        text = self._text()
        self.assertNotIn("NOTION — Retry Queue", text)
        self.assertNotIn("-" * 20, text)

    def test_a_missing_notion_block_is_said_out_loud(self):
        """Absent is not "fine". If the Runner-side state cannot be read,
        a current page is no evidence at all about the rows."""
        text = self._text(_payload(blocks=[]))
        self.assertIn("상태를 읽지 못했다", text)
        self.assertIn("Row 데이터는 오래됐을 수 있다", text)

    def test_the_runner_side_lines_are_redacted(self):
        """Same provenance as ATTENTION: `blocks` is captured
        `ops_status.py` stdout and does not pass through `to_payload()`."""
        token = "ntn_" + "B" * 40
        payload = _payload(blocks=[
            {"key": "NOTION", "title": "NOTION", "parity": 0,
             "text": f"NOTION\n---\n  토큰이 섞인 줄: {token}\n", "attention": 0}
        ])
        text = self._text(payload)

        self.assertNotIn(token, text)
        self.assertIn("[REDACTED]", text)

    def test_each_line_becomes_its_own_paragraph(self):
        """`one_line()` escapes newlines, so a single run would render the
        whole block as one unreadable string."""
        blocks, _ = build_control_tower_blocks(_payload(blocks=[
            {"key": "NOTION", "title": "NOTION", "parity": 0,
             "text": NOTION_BLOCK_TEXT, "attention": 0}
        ]))
        for run in _all_text(blocks):
            self.assertNotIn("\\n", run, "a block rendered as one escaped run")


class AFailedRewriteSaysThePageIsEmptyTests(unittest.TestCase):
    """C113 — the window archive-then-write deliberately opens.

    That ordering is right (see `publish()`): the reverse leaves a reader
    two copies of the Control Tower, and if the archiving half fails it
    leaves them permanently. "A page that says two different things about
    the company is worse than a page that is briefly empty."

    **But "briefly" assumes someone runs it again.** Measured against live
    Notion: an append that failed after the archive left the real page at
    **0 blocks**, and the caller printed only the API error. An operator
    opening Notion then found a blank Control Tower with no way to tell
    whether the company had no state or the tool had eaten it.

    The ordering is unchanged. What is added is the one fact the caller
    could not have known.
    """

    class _FailsOnRewrite(InMemoryNotionTransport):
        def __init__(self):
            super().__init__()
            self.appends = 0

        def append_block_children(self, block_id, children):
            self.appends += 1
            if self.appends > 1:
                raise NotionAPIError("simulated mid-publish failure", status_code=503)
            return super().append_block_children(block_id, children)

    def _fail_a_rewrite(self):
        transport = self._FailsOnRewrite()
        publish(transport=transport, parent_page_id=PARENT, payload=_payload())
        with self.assertRaises(ControlTowerPageError) as caught:
            publish(transport=transport, parent_page_id=PARENT, payload=_payload())
        return transport, str(caught.exception)

    def test_the_message_says_the_page_is_empty(self):
        _transport, message = self._fail_a_rewrite()
        self.assertIn("비어 있다", message)

    def test_it_says_how_many_blocks_were_archived(self):
        """Not decoration: it is how an operator tells this from a page that
        was empty to begin with."""
        _transport, message = self._fail_a_rewrite()
        self.assertRegex(message, r"기존 블록 \d+개")

    def test_it_names_the_command_that_restores_it(self):
        _transport, message = self._fail_a_rewrite()
        self.assertIn("publish_control_tower.py", message)

    def test_the_original_cause_survives(self):
        """Wrapping must not swallow what actually broke."""
        _transport, message = self._fail_a_rewrite()
        self.assertIn("simulated mid-publish failure", message)

    def test_the_page_really_is_empty_at_that_point(self):
        """The claim the message makes, checked rather than asserted in
        prose."""
        transport, _message = self._fail_a_rewrite()
        page_id = find_child_page(transport, PARENT, "Control Tower")
        self.assertEqual(transport.list_block_children(page_id), [])

    def test_a_successful_rewrite_raises_nothing(self):
        """The control."""
        transport = InMemoryNotionTransport()
        publish(transport=transport, parent_page_id=PARENT, payload=_payload())
        result = publish(transport=transport, parent_page_id=PARENT, payload=_payload())
        self.assertGreater(result.blocks_written, 0)


if __name__ == "__main__":
    unittest.main()
