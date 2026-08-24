"""The Control Tower's Notion projection (`controltower/notion_projection.py`).

The chain the request states is

    Desktop 1/2/4 -> Execution Evidence -> Control Tower
        -> Company / Team / Project / Sprint -> Dashboard -> Notion

and everything up to the last arrow already existed and was already tested.
These tests are about the last arrow: the databases, the property mapping,
the payload, what Notion would refuse, and the write.

Nothing here needs a credential. The Notion transport is the in-memory
double, which refuses over-long text with the same HTTP 400 the live API
answers with (C50 §10), and every schema assertion is made against the
property payloads the projection itself declares.

What is deliberately NOT asserted: that anything creates these databases.
They are out of docs/14 §1's Operational Projection until that table is
widened, and `ControlTowerDatabasesAreNotContractedYetTests` pins the gap
rather than closing it.
"""

import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from controltower import build_company_rollup, build_dashboard  # noqa: E402
from controltower import notion_projection as projection  # noqa: E402
from controltower.dashboard import (  # noqa: E402
    EVIDENCE_IN_PAYLOAD,
    UNSOURCED_LAYERS,
    DashboardModel,
    DashboardPanel,
    DashboardRow,
    PanelStatus,
    unsourced_layer_coverage,
)
from events import Event  # noqa: E402
from notion import InMemoryNotionTransport, NotionAPIError, NotionClient  # noqa: E402
from notion.dashboard import CONTRACTED_DATABASES, DASHBOARD_DATABASES  # noqa: E402
from notion.properties import RICH_TEXT_LIMIT  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 14, 11, 0, tzinfo=KST)


def _event(**overrides) -> Event:
    data = {
        "schema_version": "1.0",
        "event_id": "EVT-1",
        "timestamp": "2026-08-12T10:00:00+09:00",
        "source": "DESKTOP_1",
        "role": "CTO_BACKEND",
        "project_id": "SEARCH_BACKEND",
        "event_type": "MILESTONE_COMPLETED",
        "status": "IN_PROGRESS",
        "summary": "색인 재구축 완료",
        "history_candidate": True,
    }
    data.update(overrides)
    return Event.from_dict(data)


#: A rollup with something in every sourced panel: two teams, three projects,
#: an open blocker, a completion, and a Desktop whose Event claims a role
#: docs/02 §8 does not give it.
FULL_EVENTS = (
    _event(event_id="E-1", project_id="SEARCH_BACKEND", milestone="Index rebuild"),
    _event(
        event_id="E-2",
        project_id="SEARCH_BACKEND",
        event_type="BLOCKED",
        status="BLOCKED",
        blocker="벤더 API 키 발급 대기",
        timestamp="2026-08-12T14:00:00+09:00",
    ),
    _event(
        event_id="E-3",
        source="DESKTOP_2",
        role="CMO",
        project_id="BRAND_CAMPAIGN",
        event_type="DECISION_APPROVED",
        timestamp="2026-08-13T09:00:00+09:00",
    ),
    _event(
        event_id="E-4",
        source="DESKTOP_4",
        role="COO",
        project_id="COMPANY_OPS",
        event_type="COMPLETED",
        status="COMPLETED",
        timestamp="2026-08-13T10:00:00+09:00",
    ),
    # docs/02 §8 gives DESKTOP_3 to CTO_FRONTEND. This one claims the CMO's
    # work, which is a `PairMismatch` and therefore a RISKS row.
    _event(
        event_id="E-5",
        source="DESKTOP_3",
        role="CMO",
        project_id="BRAND_CAMPAIGN",
        timestamp="2026-08-13T11:00:00+09:00",
    ),
)


def _rollup(events=FULL_EVENTS, *, now: datetime = NOW):
    """`build_company_rollup()` takes `(Event, path)` pairs — the shape
    `read_events()` hands back, so a caller that already read the directory
    and one that did not produce the same rollup. The path is the file the
    `EvidenceRef` will name, so it is the Event's own filename here."""
    return build_company_rollup(
        events=tuple((event, f"{event.event_id}.json") for event in events), now=now
    )


def _model(events=FULL_EVENTS, *, now: datetime = NOW) -> DashboardModel:
    return build_dashboard(_rollup(events, now=now), now=now)


def _client(transport: InMemoryNotionTransport, database_id: str) -> NotionClient:
    return NotionClient(transport=transport, database_id=database_id)


def _all_clients(transport: InMemoryNotionTransport) -> dict:
    return {
        name: _client(transport, f"db-{name}")
        for name in projection.control_tower_databases()
    }


def _prop_text(prop, kind):
    return "".join(
        (item.get("text") or {}).get("content") or "" for item in (prop.get(kind) or [])
    )


class EveryColumnHasAPropertyTests(unittest.TestCase):
    """The contract that stops a column from vanishing on the way to Notion.

    A panel declares its `columns` and the model checks every row against
    them (`EveryRowFillsTheColumnsItsPanelDeclaresTests`). One layer further
    out, a column with no declared Notion property would simply not be
    written — no error, no empty cell, nothing — and the Notion table would
    quietly be a subset of the Control Tower. Gated in both directions,
    because only one of them is visible from the panel's side.
    """

    def setUp(self):
        self.model = _model()

    def test_every_sourced_panel_is_projected_or_explained(self):
        """Three states, not two.

        "sourced and projected", "unsourced", and "sourced but deliberately
        not projected" are different facts. Without a name for the third, a
        panel with no database is indistinguishable from one somebody forgot
        — which is the same confusion `PanelStatus.UNSOURCED` exists to
        remove one level down.
        """
        for panel in self.model.panels:
            if panel.status is not PanelStatus.SOURCED:
                continue
            with self.subTest(panel=panel.key):
                projected = panel.key in projection.PANEL_PROJECTIONS
                explained = panel.key in projection.UNPROJECTED_PANELS
                self.assertTrue(
                    projected != explained,
                    "a sourced panel must be projected, or named in "
                    "UNPROJECTED_PANELS with a reason — never neither and "
                    "never both",
                )

    def test_every_unprojected_panel_names_a_reason(self):
        for key, reason in projection.UNPROJECTED_PANELS.items():
            with self.subTest(panel=key):
                self.assertTrue(reason.strip())
                self.assertGreater(len(reason), 40, "a reason, not a label")

    def test_no_unprojected_panel_also_has_a_database(self):
        databases = projection.control_tower_databases()
        for key in projection.UNPROJECTED_PANELS:
            with self.subTest(panel=key):
                self.assertNotIn(f"CT_{key}", databases)

    def test_no_unprojected_panel_is_unsourced(self):
        """The list is for panels that **have** a source. An unsourced one
        already has `UNSOURCED_LAYER_NOTES` and putting it here too would be
        two records of one fact."""
        unsourced = {p.key for p in self.model.unsourced_panels}
        self.assertEqual(set(projection.UNPROJECTED_PANELS) & unsourced, set())

    def test_an_unprojected_panel_still_produces_no_rows(self):
        panels = {row.panel for row in projection.project_panels(self.model)}
        self.assertEqual(panels & set(projection.UNPROJECTED_PANELS), set())

    def test_no_projection_names_a_panel_that_does_not_exist(self):
        keys = {panel.key for panel in self.model.panels}
        self.assertEqual(set(projection.PANEL_PROJECTIONS) - keys, set())

    def test_every_column_of_every_panel_becomes_a_property(self):
        for panel in self.model.panels:
            mapping = projection.PANEL_PROJECTIONS.get(panel.key)
            if mapping is None:
                continue
            with self.subTest(panel=panel.key):
                self.assertEqual(
                    set(panel.columns),
                    set(mapping.columns),
                    "a panel column with no Notion property is a column that "
                    "silently does not reach Notion",
                )

    def test_the_projected_title_column_is_really_the_row_key(self):
        """`key_column` earns its exemption by being the row key already. If
        it ever stopped being, `Row Key` and the column would be two names
        for two different values and a reader would have no way to tell."""
        for panel in self.model.panels:
            mapping = projection.PANEL_PROJECTIONS.get(panel.key)
            if mapping is None or mapping.key_column is None:
                continue
            for row in panel.rows:
                with self.subTest(panel=panel.key, row=row.key):
                    self.assertEqual(row.values[mapping.key_column], row.key)

    def test_a_panel_with_no_key_column_still_has_a_unique_row_key(self):
        risks = self.model.panel("RISKS")
        keys = [row.key for row in risks.rows]
        self.assertTrue(keys, "the fixture is supposed to produce risks")
        self.assertEqual(len(keys), len(set(keys)))


class TheTitleColumnMayNeverBeNullTests(unittest.TestCase):
    """The nullability contract, stated as a mapping rule.

    `TheNullabilityContractTests` records which payload fields can be null.
    This is the one place where "can be null" is not merely information for a
    consumer but a **loss of identity**: `Row Key` is the `title`, and
    `_property()` renders a null title as `{"content": ""}`. Two rows with an
    empty title are two rows `find_by_title()` cannot tell apart — so the
    next sync updates one of them twice and the other keeps a state nobody
    wrote.

    Two statements, because there are two ways in:

        the row key itself      `ProjectedRow.row_key` comes from
                                `DashboardRow.key`, not from a column
        the `key_column`        the column a panel declares as already being
                                its row key, which is why it gets no property
                                of its own

    Both must be non-nullable, and the second is checkable against the
    recorded contract without running anything.
    """

    #: Kept in step with `test_controltower_dashboard.TheNullabilityContractTests`
    #: by `test_the_two_contracts_name_the_same_fields`, so this is not a
    #: second opinion about which fields are nullable.
    def _nullable(self):
        import importlib

        module = importlib.import_module("test_controltower_dashboard")
        return module.TheNullabilityContractTests.NULLABLE

    def test_no_panels_key_column_is_nullable(self):
        nullable = self._nullable()
        for panel_key, mapping in projection.PANEL_PROJECTIONS.items():
            if mapping.key_column is None:
                continue
            with self.subTest(panel=panel_key, column=mapping.key_column):
                self.assertNotIn(
                    (panel_key, mapping.key_column),
                    nullable,
                    f"{panel_key}.{mapping.key_column} is this panel's row key "
                    "and the contract says it can be null — an empty title is "
                    "a row the lookup cannot identify",
                )

    def test_the_two_contracts_name_the_same_fields(self):
        """Guard against the import above silently drifting into a stale or
        empty set, which would make the assertion vacuous."""
        nullable = self._nullable()

        self.assertGreater(len(nullable), 20)
        self.assertIn(("PROJECTS", "blocker"), nullable)

    def test_every_projected_row_has_a_non_empty_key(self):
        """The runtime half. A `project_id` of `""` is accepted by
        `validate_event()` (BACKLOG A-15) and reaches this projection as an
        empty row key — recorded rather than refused, because refusing it is
        that open decision and not this module's to take. What *is* checked
        is that the ordinary fold never produces one.
        """
        for row in projection.project_panels(_model()):
            with self.subTest(database=row.database):
                self.assertTrue(row.row_key)

    def test_an_empty_project_id_still_round_trips_to_one_row(self):
        """CHARACTERIZATION. The empty key is produced, accepted by
        `validate_rows()`, and — because the write side and the lookup side
        use the same string — stays **one** row across syncs rather than
        multiplying. That is the property that matters here; whether an empty
        `project_id` should exist at all is BACKLOG A-15.
        """
        transport = InMemoryNotionTransport()
        clients = _all_clients(transport)
        events = (_event(event_id="E-EMPTY", project_id=""),)
        model = _model(events=events)

        self.assertEqual(projection.validate_rows(projection.project_panels(model)), [])
        first = projection.sync_control_tower(clients, model)
        second = projection.sync_control_tower(clients, model)

        self.assertGreater(first.created, 0)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.retired, 0)
        rows = [
            page
            for page_id, page in transport._pages.items()
            if transport._page_database[page_id] == "db-CT_PROJECTS"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            _prop_text(rows[0]["properties"][projection.ROW_KEY_PROPERTY], "title"), ""
        )


class NoSelectColumnCarriesAuthoredTextTests(unittest.TestCase):
    """A Notion `select` is a **vocabulary**, and a person's sentence is not.

    Two things go wrong when authored text lands in one, and they go wrong in
    opposite directions:

        the option list        Notion creates an option per distinct value.
                               A `blocker` as a select would add one option
                               per blocker, forever, to a database nobody can
                               tidy from here.
        the request            select option names may not contain a comma,
                               and `project_id` / `blocker` / `milestone` are
                               fields `validate_event()` only type-checks. One
                               comma is an HTTP 400 for the whole row, which
                               `sync.PERMANENTLY_REFUSING_STATUS_CODES` then
                               classifies PERMANENT.

    The property that keeps this safe already exists one layer down.
    `dashboard._UNAUTHORED_KEYS` is the list of columns whose value provably
    **cannot** be text a person typed — each is a member of `events.SOURCES`
    / `ROLES` / `STATUSES`, a word the model itself chose, or a
    `ROLE_DISPLAY_NAMES` entry — and it is deliberately the short list, for
    the reason its own comment gives: an allow-list of what to protect has to
    be complete to work, and this one fails closed.

    So the rule is one line: SELECT ⊆ `_UNAUTHORED_KEYS`. Today all nine
    satisfy it. Without this gate the tenth would not have to.
    """

    def test_every_select_column_is_provably_unauthored(self):
        from controltower.dashboard import _UNAUTHORED_KEYS

        for panel_key, mapping in projection.PANEL_PROJECTIONS.items():
            for column, (name, kind) in mapping.columns.items():
                if kind is not projection.PropertyType.SELECT:
                    continue
                with self.subTest(panel=panel_key, column=column):
                    self.assertIn(
                        column,
                        _UNAUTHORED_KEYS,
                        f"{name} is a Notion select fed by `{column}`, which is "
                        "not on the provably-unauthored list — a select option "
                        "per authored value, and one comma is a 400",
                    )

    def test_the_gate_would_catch_an_authored_select(self):
        """The detector detects. `blocker` is the obvious wrong answer and
        the one a future column is most likely to make."""
        from controltower.dashboard import _UNAUTHORED_KEYS

        self.assertNotIn("blocker", _UNAUTHORED_KEYS)
        self.assertNotIn("project_id", _UNAUTHORED_KEYS)
        self.assertNotIn("milestones", _UNAUTHORED_KEYS)

    def test_no_select_value_the_real_fold_produces_has_a_comma(self):
        for row in projection.project_panels(_model()):
            for name, payload in row.properties.items():
                option = payload.get("select")
                if not option:
                    continue
                with self.subTest(database=row.database, property=name):
                    self.assertNotIn(",", option["name"])

    def test_a_select_column_never_carries_a_secret(self):
        """Belt and braces on the same set: `_out()` skips redaction for
        exactly `_UNAUTHORED_KEYS`, so a SELECT column outside it would be
        both a vocabulary of sentences *and* an unredacted one."""
        events = (
            _event(
                event_id="E-S",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="ntn" + "_A1b2C3d4E5f6G7h8xyz",
            ),
        )
        for row in projection.project_panels(_model(events=events)):
            for name, payload in row.properties.items():
                option = payload.get("select")
                if not option:
                    continue
                with self.subTest(database=row.database, property=name):
                    self.assertNotIn("ntn_A1b2", option["name"])


class EveryPanelIsAccountedForTests(unittest.TestCase):
    """Sourced or unsourced — no third state, and nothing falls between.

    `project_panels()` skips a panel it has no mapping for. That branch must
    be unreachable from a real model, or the projection would be silently
    dropping a panel somebody added.
    """

    def test_each_panel_is_in_exactly_one_of_the_three_states(self):
        """projected / unsourced / sourced-but-not-projected.

        Written as two states when there were two. The third arrived with
        ACTIVITY and COMPLETIONS — panels with a real source that
        deliberately get no Notion database — and a two-way test would have
        called that "neither", which is the same verdict it gives a panel
        somebody forgot.
        """
        for panel in _model().panels:
            with self.subTest(panel=panel.key):
                states = [
                    panel.key in projection.PANEL_PROJECTIONS,
                    panel.status is PanelStatus.UNSOURCED,
                    panel.key in projection.UNPROJECTED_PANELS,
                ]
                self.assertEqual(sum(states), 1, f"states={states}")

    def test_every_unsourced_layer_has_a_note_and_no_database(self):
        model = _model()
        coverage = unsourced_layer_coverage(model)
        self.assertEqual(set(coverage), set(UNSOURCED_LAYERS))
        for layer, panel_key in coverage.items():
            with self.subTest(layer=layer):
                self.assertIn(layer, projection.UNSOURCED_LAYER_NOTES)
                self.assertNotIn(panel_key, projection.PANEL_PROJECTIONS)

    def test_the_notes_cover_exactly_the_unsourced_layers(self):
        self.assertEqual(
            set(projection.UNSOURCED_LAYER_NOTES), set(UNSOURCED_LAYERS)
        )


class TheActivityTableWouldBreakAtAThousandRowsTests(unittest.TestCase):
    """Why ACTIVITY and COMPLETIONS get no Notion database.

    They were projected first. The reason for taking that back is not taste
    — it is a number this code can state, and this class states it.

    A row keyed by `event_id` is a row per Event. This repository does not
    delete (docs/10 §46 keeps every collected Event; `remove_pending()` is a
    deletion decision still open as B-7), so a row written once stays even
    after its Event falls out of the `RECENT_LIMIT` window. The panel is
    bounded; the table is not.

    Then it breaks rather than merely growing. `_retire_absent_rows()` lists
    the whole database every sync, `RealNotionTransport.list_pages()` stops
    at `_SEARCH_PAGE_LIMIT` × 100 rows, and a truncated listing makes
    reconciliation decline to run — correctly, because retiring every row it
    did not see is worse. Past that point nothing is ever retired again and
    the `Present` view an operator filters on quietly fills with stale rows.
    """

    def test_the_listing_limit_is_a_thousand_rows(self):
        from notion.transport import RealNotionTransport

        self.assertEqual(RealNotionTransport._SEARCH_PAGE_LIMIT * 100, 1000)

    def test_a_truncated_listing_stops_retirement_for_that_database(self):
        """The consequence, driven rather than argued."""

        class _AsIfOverTheLimit(InMemoryNotionTransport):
            list_truncated = True

        transport = _AsIfOverTheLimit()
        clients = _all_clients(transport)
        projection.sync_control_tower(clients, _model())

        result = projection.sync_control_tower(clients, _model(events=()))

        self.assertEqual(result.retired, 0)
        self.assertEqual(
            set(result.unreconciled), set(projection.control_tower_databases())
        )

    def test_the_projected_databases_are_all_bounded_by_identity(self):
        """The property that makes the five survivors safe, stated so a
        sixth cannot be added without meeting it.

        Every projected row key is an identity with a **fixed or slowly
        growing** population — a metric name, a role, a Desktop, a
        `project_id`, or a risk about one of those. None of them is per
        Event, so none of those tables grows with the work.
        """
        events = tuple(
            _event(
                event_id=f"E-{index}",
                project_id=f"PROJ_{index % 3}",
                timestamp=f"2026-08-12T{10 + (index % 8):02d}:00:00+09:00",
            )
            for index in range(200)
        )
        few = projection.project_panels(_model(events=events[:20]))
        many = projection.project_panels(_model(events=events))

        by_database = {}
        for rows, label in ((few, "20 Events"), (many, "200 Events")):
            counts = {}
            for row in rows:
                counts[row.database] = counts.get(row.database, 0) + 1
            by_database[label] = counts

        self.assertEqual(by_database["20 Events"], by_database["200 Events"])

    def test_the_panels_themselves_still_carry_the_activity(self):
        """Not projected is not dropped. The Control Tower screen and
        `to_payload()` both still answer "최근 활동" and "최근 완료" — the
        request's own two items — and both are bounded."""
        model = _model()

        for key in ("ACTIVITY", "COMPLETIONS"):
            with self.subTest(panel=key):
                panel = model.panel(key)
                self.assertIsNotNone(panel)
                self.assertIs(panel.status, PanelStatus.SOURCED)
                self.assertTrue(panel.rows)

    def test_the_payload_still_carries_them(self):
        keys = {panel["key"] for panel in _model().to_payload()["panels"]}

        self.assertIn("ACTIVITY", keys)
        self.assertIn("COMPLETIONS", keys)


class TheSchemaAndThePayloadAgreeTests(unittest.TestCase):
    """One declaration, two artefacts. The 400 that never happens.

    `control_tower_databases()` and `project_panels()` are both built from
    `PANEL_PROJECTIONS`, so a type stated once cannot be two things — but
    "built from the same table" is a claim about the code and this is the
    check on the values.
    """

    def setUp(self):
        self.rows = projection.project_panels(_model())
        self.schemas = projection.control_tower_databases()

    def test_the_fixture_actually_fills_every_database(self):
        databases = {row.database for row in self.rows}
        self.assertEqual(databases, set(self.schemas))

    def test_every_property_written_is_in_its_databases_schema(self):
        for row in self.rows:
            for name in row.properties:
                with self.subTest(database=row.database, property=name):
                    self.assertIn(name, self.schemas[row.database])

    def test_every_property_in_the_schema_is_written(self):
        """The other direction: a declared property nothing ever fills is a
        column an operator sees permanently empty, which is the
        `CONTRACTED_DATABASES` mistake at property granularity."""
        for row in self.rows:
            with self.subTest(database=row.database, row=row.row_key):
                self.assertEqual(
                    set(self.schemas[row.database]) - set(row.properties), set()
                )

    def test_every_payload_uses_the_type_its_schema_declares(self):
        for row in self.rows:
            for name, payload in row.properties.items():
                declared = next(iter(self.schemas[row.database][name]))
                with self.subTest(database=row.database, property=name):
                    self.assertIn(declared, payload)

    def test_the_common_properties_are_on_every_row_of_every_database(self):
        for row in self.rows:
            with self.subTest(database=row.database, row=row.row_key):
                self.assertEqual(
                    set(projection.COMMON_PROPERTIES) - set(row.properties), set()
                )

    def test_exactly_one_title_property_per_database(self):
        for name, schema in self.schemas.items():
            titles = [key for key, value in schema.items() if "title" in value]
            with self.subTest(database=name):
                self.assertEqual(titles, [projection.ROW_KEY_PROPERTY])


class TheMetricsEvidenceCountIsTheSharedOneTests(unittest.TestCase):
    """METRICS maps its own `evidence_count` column onto the shared
    `Evidence Count` property instead of getting a second one.

    That is only safe while the two are the same number. The panel sets the
    column to `len(metric.evidence)` and puts those same refs on the row, so
    they are — but "so they are" is exactly the kind of claim that stops
    being true without anybody noticing.
    """

    def test_the_column_and_the_row_agree_for_every_metric(self):
        panel = _model().panel("METRICS")
        self.assertTrue(panel.rows)
        for row in panel.rows:
            with self.subTest(metric=row.key):
                self.assertEqual(row.values["evidence_count"], len(row.evidence))

    def test_the_written_number_is_that_number(self):
        model = _model()
        panel = model.panel("METRICS")
        expected = {row.key: row.values["evidence_count"] for row in panel.rows}
        written = {
            row.row_key: row.properties["Evidence Count"]["number"]
            for row in projection.project_panels(model)
            if row.database == "CT_METRICS"
        }
        self.assertEqual(written, expected)


class NotionWouldRefuseNothingTests(unittest.TestCase):
    """`validate_rows()` against the payload the real fold produces."""

    def test_the_ordinary_projection_is_clean(self):
        self.assertEqual(projection.validate_rows(projection.project_panels(_model())), [])

    def test_the_projection_of_an_empty_company_is_clean(self):
        """Every panel present, every row absent. The panels still emit the
        Desktop and Team rows for machines that reported nothing, so this is
        not the same as "no rows"."""
        rows = projection.project_panels(_model(events=()))
        self.assertEqual(projection.validate_rows(rows), [])
        self.assertTrue(rows, "silent Desktops and Teams are still rows")

    def test_an_unknown_property_is_reported(self):
        rows = projection.project_panels(_model())
        broken = replace(
            rows[0],
            properties=dict(rows[0].properties, Invented={"number": 1}),
        )
        errors = projection.validate_rows([broken])
        self.assertTrue(any("not in the database schema" in e for e in errors), errors)

    def test_a_wrong_type_is_reported(self):
        rows = projection.project_panels(_model())
        broken = replace(
            rows[0],
            properties=dict(rows[0].properties, **{"Evidence Count": {"rich_text": []}}),
        )
        errors = projection.validate_rows([broken])
        self.assertTrue(any("schema says number" in e for e in errors), errors)

    def test_an_empty_select_name_is_reported(self):
        """Notion refuses `{"name": ""}`; a null select is how "no value" is
        spelled, and the projection already spells it that way."""
        row = next(r for r in projection.project_panels(_model()) if r.database == "CT_PROJECTS")
        broken = replace(
            row, properties=dict(row.properties, State={"select": {"name": ""}})
        )
        errors = projection.validate_rows([broken])
        self.assertTrue(any("empty select name" in e for e in errors), errors)

    def test_a_comma_in_a_select_name_is_reported(self):
        row = next(r for r in projection.project_panels(_model()) if r.database == "CT_PROJECTS")
        broken = replace(
            row, properties=dict(row.properties, State={"select": {"name": "A, B"}})
        )
        errors = projection.validate_rows([broken])
        self.assertTrue(any("contains a comma" in e for e in errors), errors)

    def test_two_rows_with_one_key_are_reported(self):
        """The merged-row failure `fit_key()` exists to prevent, one level
        up: two rows sharing a title means the second overwrites the first
        and one of the two projects' state is simply gone."""
        rows = projection.project_panels(_model())
        first = next(r for r in rows if r.database == "CT_PROJECTS")
        errors = projection.validate_rows([first, first])
        self.assertTrue(any("share the row key" in e for e in errors), errors)

    def test_over_long_text_is_reported_when_it_gets_through(self):
        row = next(r for r in projection.project_panels(_model()) if r.database == "CT_PROJECTS")
        broken = replace(
            row,
            properties=dict(
                row.properties,
                Blocker={"rich_text": [{"text": {"content": "x" * (RICH_TEXT_LIMIT + 1)}}]},
            ),
        )
        errors = projection.validate_rows([broken])
        self.assertTrue(any("over the" in e for e in errors), errors)

    def test_a_row_for_a_database_that_does_not_exist_is_reported(self):
        row = projection.project_panels(_model())[0]
        errors = projection.validate_rows([replace(row, database="CT_NOPE")])
        self.assertTrue(any("no database named" in e for e in errors), errors)

    def test_a_missing_property_is_reported(self):
        row = projection.project_panels(_model())[0]
        thinner = dict(row.properties)
        thinner.pop("Evidence")
        errors = projection.validate_rows([replace(row, properties=thinner)])
        self.assertTrue(any("no value for" in e for e in errors), errors)


class EveryDateThisProjectionEmitsIsISOTests(unittest.TestCase):
    """C65. `validate_rows()` claimed to list "every reason Notion would
    refuse these rows" and did not check a date.

    Measured before the fix: a row carrying `"not-a-date"` and
    `"2026-13-45T99:00:00"` in two DATE properties came back **clean**, and
    the in-memory double accepted it too. The live API answers both with
    HTTP 400, which `sync.PERMANENTLY_REFUSING_STATUS_CODES` classifies as an
    answer that will not change by retrying.

    Nothing produces such a value today, and the second test here is what
    establishes that rather than assuming it — it runs the real fold and
    reads every date back out. So this is a **stale claim** closed, not a
    live defect fixed, and the distinction is worth keeping: what the check
    is actually for is the DATE column that does not exist yet.
    `_property()` turns any string into a `date.start`, so the first one fed
    by something other than an Event timestamp would reach Notion with
    nothing on this side having said why.
    """

    def _one_row(self):
        row = projection.ProjectedRow(
            database="CT_PROJECTS", panel="PROJECTS", row_key="P", properties={}
        )
        for name, kind in projection.COMMON_PROPERTIES.items():
            row.properties[name] = projection._property(kind, None)
        mapping = projection.PANEL_PROJECTIONS["PROJECTS"]
        for column, (name, kind) in mapping.columns.items():
            if column != mapping.key_column:
                row.properties[name] = projection._property(kind, None)
        row.properties[projection.ROW_KEY_PROPERTY] = projection._property(
            projection.PropertyType.TITLE, "P"
        )
        return row

    def test_a_value_that_is_not_a_date_is_reported(self):
        for bad in ("not-a-date", "2026-13-45T99:00:00", "21/08/2026", ""):
            with self.subTest(value=bad):
                row = self._one_row()
                row.properties["First Seen"] = {"date": {"start": bad}}
                errors = projection.validate_rows([row])
                self.assertTrue(
                    any("ISO 8601" in message for message in errors), errors
                )

    def test_a_null_date_is_not_an_error(self):
        """Present-and-null is how this projection spells "no value", on
        purpose and on most rows — a check that flagged it would fail every
        ordinary run."""
        row = self._one_row()
        row.properties["Completed At"] = {"date": None}
        self.assertEqual(projection.validate_rows([row]), [])

    def test_every_date_the_real_fold_emits_is_accepted(self):
        """The premise the fix rests on, measured rather than assumed. If a
        panel ever carries a date this parser refuses, this fails here rather
        than as a 400 against a live Workspace."""
        rows = projection.project_panels(_model())
        seen = []
        for row in rows:
            for name, prop in row.properties.items():
                value = prop.get("date")
                if value:
                    seen.append((name, value["start"]))

        self.assertTrue(seen, "the fixture produced no dates to check")
        for name, start in seen:
            with self.subTest(prop=name, value=start):
                self.assertTrue(projection._is_iso_8601(start))
        self.assertEqual(projection.validate_rows(rows), [])

    def test_the_parser_is_the_one_that_validates_an_event(self):
        """One parser, not two patterns that have to agree.

        Stated as an **agreement** rather than as "both accept these": the
        property that matters is that a value `validate_event()` lets into an
        Event is a value this check lets out to Notion, for every value —
        including the ones both refuse. Writing it the other way is how the
        first draft of this test failed: it asserted that
        `2026-08-21T10:00:00.5+09:00` is a valid timestamp, and the Python
        3.9 `fromisoformat()` the project ran on then refuses a one-digit
        fraction. Both refused it, in step, which is the answer this test
        wanted and not the one it was asking for.

        C76 is why the labels below say "3.9" rather than "refused": the
        deployment runtime is 3.13.14 now and it takes both the one-digit
        fraction and the `Z`. The implication is unchanged and still passes,
        which is the whole argument for writing it this way.
        """
        from events.schema import _timestamp_error

        corpus = (
            "2026-08-21T10:00:00+09:00",       # the ordinary Event timestamp
            "2026-08-21T10:00:00.500000+09:00",  # six-digit fraction
            "2026-08-21T10:00:00.5+09:00",    # one digit: 3.9 refused, 3.13 takes it
            "2026-08-21T10:00:00Z",           # 3.9 refused the Z form, 3.13 takes it
            "2026-08-21",                     # a date with no offset
            "not-a-date",
            "",
        )
        for value in corpus:
            with self.subTest(value=value):
                event_accepts = _timestamp_error(value) is None
                notion_accepts = projection._is_iso_8601(value)
                if event_accepts:
                    self.assertTrue(
                        notion_accepts,
                        f"{value!r} validates as an Event timestamp and would"
                        " be refused as a Notion date",
                    )


class AuthoredTextIsBoundedOnTheWayOutTests(unittest.TestCase):
    """The C50 defect, in the new payload.

    `validate_event()` bounds neither `blocker` nor `milestone` nor
    `project_id`, and one character over 2,000 is an HTTP 400 for the whole
    row — classified PERMANENT, so the row never updates again. The bound
    belongs at the end of the builder, never inside the value helpers, for
    the reason `fit_properties()` states: the rollup asks the same rule
    module what an Event does to blocker state, and a truncation further in
    reached the Control Tower's own screen.
    """

    LONG = "막" * 3600

    def _projected(self):
        events = FULL_EVENTS + (
            _event(
                event_id="E-LONG",
                project_id="LONG_BLOCKER",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker=self.LONG,
                timestamp="2026-08-13T12:00:00+09:00",
            ),
        )
        return projection.project_panels(_model(events=events))

    def test_no_text_item_exceeds_what_notion_accepts(self):
        for row in self._projected():
            for name, payload in row.properties.items():
                for kind in ("title", "rich_text"):
                    if kind not in payload:
                        continue
                    with self.subTest(database=row.database, property=name):
                        self.assertLessEqual(
                            len(_prop_text(payload, kind)), RICH_TEXT_LIMIT
                        )

    def test_the_cut_is_visible(self):
        row = next(r for r in self._projected() if r.row_key == "LONG_BLOCKER")
        self.assertTrue(_prop_text(row.properties["Blocker"], "rich_text").endswith("…"))

    def test_the_rollup_keeps_the_whole_blocker(self):
        """The half of the C50 finding that is about *where* the bound goes.
        A truncation inside the value helpers would have shortened what the
        Control Tower itself reports, on a screen that never talks to
        Notion."""
        events = FULL_EVENTS + (
            _event(
                event_id="E-LONG",
                project_id="LONG_BLOCKER",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker=self.LONG,
                timestamp="2026-08-13T12:00:00+09:00",
            ),
        )
        rollup = _rollup(events)
        self.assertEqual(len(rollup.project("LONG_BLOCKER").open_blocker), len(self.LONG))

    def test_the_double_accepts_the_bounded_payload(self):
        """The bound is only worth something if the thing that enforces it
        agrees. The double answers over-long text with the live API's own
        400, so a write that lands is a write the API would have taken."""
        transport = InMemoryNotionTransport()
        events = FULL_EVENTS + (
            _event(
                event_id="E-LONG",
                project_id="LONG_BLOCKER",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker=self.LONG,
                timestamp="2026-08-13T12:00:00+09:00",
            ),
        )
        result = projection.sync_control_tower(
            _all_clients(transport), _model(events=events)
        )
        self.assertEqual(result.errors, ())
        self.assertIs(result.outcome, projection.ProjectionOutcome.RECORDED)


class TwoLongProjectIdsDoNotShareOneRowTests(unittest.TestCase):
    """The merged-row failure, at the Control Tower's own row key.

    `validate_event()` puts no length limit on `project_id`, so two projects
    can agree on their first 1,999 characters. Plain truncation would give
    them one `Row Key`, `find_by_title()` would find the same page for both,
    and the second write would put one project's state on top of the other's.

    That is strictly worse than the HTTP 400 it replaces: the 400 writes
    nothing and stays visible in ATTENTION, while a merged row is a wrong
    answer nothing flags. `fit_key()` appends a digest of the **whole** value
    for exactly this, and `notion/sync.py` reaches the same conclusion for
    `Project ID` / `Last Event ID`.
    """

    def _rows_for(self, *project_ids):
        events = tuple(
            _event(
                event_id=f"E-{index}",
                project_id=project_id,
                timestamp=f"2026-08-12T{10 + index:02d}:00:00+09:00",
            )
            for index, project_id in enumerate(project_ids)
        )
        return [
            row
            for row in projection.project_panels(_model(events=events))
            if row.database == "CT_PROJECTS"
        ]

    def test_a_shared_prefix_does_not_become_a_shared_row(self):
        shared = "P" * (RICH_TEXT_LIMIT + 100)
        rows = self._rows_for(shared + "-ALPHA", shared + "-BETA")

        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0].row_key, rows[1].row_key)
        self.assertEqual(projection.validate_rows(rows), [])

    def test_the_key_still_fits_what_notion_accepts(self):
        rows = self._rows_for("Q" * (RICH_TEXT_LIMIT + 100))

        self.assertLessEqual(len(rows[0].row_key), RICH_TEXT_LIMIT)

    def test_the_written_title_is_the_value_the_lookup_uses(self):
        """The write side and the lookup side agree without either storing
        anything — they are the same string, not two functions that have to
        produce it identically."""
        transport = InMemoryNotionTransport()
        clients = _all_clients(transport)
        long_id = "R" * (RICH_TEXT_LIMIT + 100)
        events = (_event(event_id="E-L", project_id=long_id),)

        first = projection.sync_control_tower(clients, _model(events=events))
        second = projection.sync_control_tower(clients, _model(events=events))

        self.assertGreater(first.created, 0)
        self.assertEqual(second.created, 0, "the lookup did not find its own write")
        self.assertEqual(second.retired, 0)

    def test_two_long_ids_produce_two_rows_in_notion(self):
        transport = InMemoryNotionTransport()
        shared = "S" * (RICH_TEXT_LIMIT + 100)
        events = (
            _event(event_id="E-1", project_id=shared + "-ALPHA"),
            _event(
                event_id="E-2",
                project_id=shared + "-BETA",
                timestamp="2026-08-12T11:00:00+09:00",
            ),
        )

        projection.sync_control_tower(_all_clients(transport), _model(events=events))

        rows = [
            page
            for page_id, page in transport._pages.items()
            if transport._page_database[page_id] == "db-CT_PROJECTS"
        ]
        self.assertEqual(len(rows), 2)


class ASecretShapedValueNeverReachesNotionTests(unittest.TestCase):
    """The projection builds from `to_payload()`, so redaction is inherited.

    Reading `DashboardRow.values` directly would have made this a second
    boundary that has to remember `_UNAUTHORED_KEYS`, and the first draft of
    that list — written as an allow-list of fields to redact — leaked a
    secret-shaped `project_id` through a row key. One boundary, and this side
    of it never sees an un-redacted string.
    """

    #: Split so the literal never appears in a tracked file — the same
    #: construction `tests/test_oplog.py` uses, and for the same reason:
    #: `test_no_secret_material_in_any_tracked_file` scans this file too,
    #: and a fixture that trips the repository's own secret gate is a
    #: fixture nobody can commit.
    SECRET = "ntn" + "_A1b2C3d4E5f6G7h8xyz"

    def _rows(self, **event_overrides):
        events = (_event(event_id="E-S", **event_overrides),)
        return projection.project_panels(_model(events=events))

    def _all_text(self, rows):
        return json.dumps([r.properties for r in rows], ensure_ascii=False) + "".join(
            r.row_key for r in rows
        )

    def test_a_secret_in_the_project_id_is_redacted_including_the_row_key(self):
        rows = self._rows(project_id=self.SECRET)
        blob = self._all_text(rows)
        self.assertNotIn(self.SECRET, blob)
        self.assertIn("[REDACTED]", blob)

    def test_a_secret_in_a_blocker_is_redacted(self):
        rows = self._rows(
            event_type="BLOCKED",
            status="BLOCKED",
            blocker="키: " + self.SECRET,
        )
        self.assertNotIn(self.SECRET, self._all_text(rows))

    def test_a_secret_in_a_milestone_is_redacted(self):
        rows = self._rows(milestone=self.SECRET)
        self.assertNotIn(self.SECRET, self._all_text(rows))

    def test_a_secret_in_an_event_id_is_redacted_in_the_evidence_cell(self):
        events = (_event(event_id=self.SECRET),)
        rows = projection.project_panels(_model(events=events))
        self.assertNotIn(self.SECRET, self._all_text(rows))

    def test_a_newline_in_a_project_id_cannot_forge_a_second_line(self):
        """`validate_event()` accepts a newline in `project_id` (BACKLOG
        A-15) and one reached an ATTENTION line as a forged report before
        C47. `one_line()` runs on every value inside `to_payload()`."""
        rows = self._rows(project_id="REAL\nFORGED")
        blob = self._all_text(rows)
        self.assertNotIn("REAL\nFORGED", blob)
        self.assertIn("REAL\\nFORGED", blob)


class AFailureReasonIsNotAPlaceToLeakATokenTests(unittest.TestCase):
    """A `NotionAPIError` carries the remote response body.

    `oplog.append_line()`'s docstring records the measurement: Notion's own
    JSON cannot carry the API token — it travels in a header — but a proxy or
    captive portal answering in Notion's place is free to echo request
    headers back, and one 502 page containing `Authorization: Bearer ntn_...`
    put the token straight into notion_sync.log.

    `record_run()` hands its `error` to `_log_dashboard()`, which redacts.
    These strings have no caller yet, so leaving redaction to whoever wires
    them up would make the field safe only by memory — and a reader who knows
    the sibling is redacted would reasonably assume this one is.
    """

    SECRET = "ntn" + "_A1b2C3d4E5f6G7h8xyz"

    def _blowing_up_with(self, message):
        secret = self.SECRET

        class _Leaky(InMemoryNotionTransport):
            def query_database(self, database_id, filter_):
                raise NotionAPIError(message.format(secret=secret), status_code=502)

        return _Leaky()

    def test_a_token_in_a_response_body_is_redacted(self):
        result = projection.sync_control_tower(
            _all_clients(
                self._blowing_up_with(
                    "502 Bad Gateway\nAuthorization: Bearer {secret}"
                )
            ),
            _model(),
        )

        self.assertIs(result.outcome, projection.ProjectionOutcome.FAILED)
        blob = " ".join(result.errors)
        self.assertNotIn(self.SECRET, blob)
        self.assertIn("[REDACTED]", blob)

    def test_the_diagnosis_around_it_survives(self):
        """A redaction that removed the reason would trade one defect for
        another."""
        result = projection.sync_control_tower(
            _all_clients(self._blowing_up_with("502 Bad Gateway from {secret}")),
            _model(),
        )

        blob = " ".join(result.errors)
        self.assertIn("NotionAPIError", blob)
        self.assertIn("502", blob)

    def test_a_reason_cannot_grow_without_limit(self):
        from oplog import MAX_LOG_ERROR

        result = projection.sync_control_tower(
            _all_clients(self._blowing_up_with("x" * 50_000)), _model()
        )

        for message in result.errors:
            with self.subTest(message=message[:40]):
                self.assertLess(len(message), MAX_LOG_ERROR + 200)

    def test_a_reason_cannot_forge_a_second_line(self):
        result = projection.sync_control_tower(
            _all_clients(self._blowing_up_with("first\nDASHBOARD OK second")), _model()
        )

        for message in result.errors:
            with self.subTest(message=message[:40]):
                self.assertNotIn("\n", message)

    def test_a_secret_shaped_row_key_is_redacted_in_a_retire_error(self):
        """The retire path builds its message from the row key, which is
        already redacted on the way out of `to_payload()` — asserted rather
        than assumed, because this is the one message that interpolates a
        value instead of only an exception."""

        class _FailsOnRetire(InMemoryNotionTransport):
            fail_retires = False

            def update_page(self, page_id, properties):
                if self.fail_retires and set(properties) == {"Present", "Retired At"}:
                    raise NotionAPIError("nope", status_code=500)
                return super().update_page(page_id, properties)

        transport = _FailsOnRetire()
        clients = _all_clients(transport)
        blocked = (
            _event(
                event_id="E-B",
                project_id=self.SECRET,
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="막힘",
            ),
        )
        projection.sync_control_tower(clients, _model(events=blocked))
        transport.fail_retires = True

        result = projection.sync_control_tower(clients, _model(events=()))

        self.assertTrue(result.errors)
        self.assertNotIn(self.SECRET, " ".join(result.errors))


class UnsourcedPanelsProduceNothingTests(unittest.TestCase):
    """No Goal database, no Sprint database, no empty rows standing in for
    them. An empty table is indistinguishable from a broken one — the
    `CONTRACTED_DATABASES` finding, not repeated here."""

    def test_no_row_comes_from_an_unsourced_panel(self):
        model = _model()
        unsourced = {p.key for p in model.unsourced_panels}
        self.assertTrue(unsourced)
        panels = {row.panel for row in projection.project_panels(model)}
        self.assertEqual(panels & unsourced, set())

    def test_no_database_is_named_after_an_unsourced_panel(self):
        databases = set(projection.control_tower_databases())
        for panel in _model().unsourced_panels:
            with self.subTest(panel=panel.key):
                self.assertNotIn(f"CT_{panel.key}", databases)

    def test_the_note_says_why_rather_than_leaving_a_gap(self):
        for layer, note in projection.UNSOURCED_LAYER_NOTES.items():
            with self.subTest(layer=layer):
                self.assertIn("원천이 없다", note)
                self.assertIn("BACKLOG", note)


class ARenamedPropertyIsNotAFreeChangeTests(unittest.TestCase):
    """Drift detection for the Notion side, and it is not symmetrical with
    the payload's.

    `to_payload()` is rebuilt from scratch every run, so a renamed key simply
    stops appearing. A Notion **database** is not: `create_database()` runs
    once, and after that a renamed property is a *new* property. The old one
    stays in the operator's workspace forever, keeping whatever it last held,
    with nothing writing to it and nothing able to remove it from here —
    `NotionClient` has `rename_property()`, which `notion/bootstrap.py` uses
    deliberately, and no code path calls it for these databases.

    So the cost of a rename is asymmetric: cheap in the code, permanent in
    the workspace. This makes it a deliberate act. The schema is derived from
    `PANEL_PROJECTIONS`, so it cannot drift from the mapping; what is
    recorded here is the property **names and types** an operator's real
    database would have been built with.

    A new property is fine and this notices it too — adding one to a live
    database is what `bootstrap_dashboard_properties()` exists for, and the
    point is that somebody decided to.
    """

    #: `database -> {property name: Notion type}`. Update deliberately.
    RECORDED = {
        "CT_METRICS": {
            "Row Key": "title", "Generated At": "date",
            "Coverage Complete": "checkbox", "History Checked": "checkbox",
            "Present": "checkbox",
            "Retired At": "date", "Evidence": "rich_text",
            "Evidence Count": "number", "Evidence Truncated": "checkbox",
            "Label": "rich_text", "Value": "number", "Derived From": "rich_text",
        },
        "CT_TEAMS": {
            "Row Key": "title", "Generated At": "date",
            "Coverage Complete": "checkbox", "History Checked": "checkbox",
            "Present": "checkbox",
            "Retired At": "date", "Evidence": "rich_text",
            "Evidence Count": "number", "Evidence Truncated": "checkbox",
            "Display Name": "rich_text", "Events": "number",
            "Projects": "rich_text", "Blocked Projects": "rich_text",
            "Blocked Project Count": "number", "Last Seen": "date",
            "Has Activity": "checkbox", "Current Sprint": "rich_text",
        },
        "CT_PROJECTS": {
            "Row Key": "title", "Generated At": "date",
            "Coverage Complete": "checkbox", "History Checked": "checkbox",
            "Present": "checkbox",
            "Retired At": "date", "Evidence": "rich_text",
            "Evidence Count": "number", "Evidence Truncated": "checkbox",
            "Teams": "rich_text", "Events": "number", "Status": "select",
            "State": "select", "Blocker": "rich_text", "Blocker Team": "select",
            "Blocked Since": "date", "Days Blocked": "number",
            "First Seen": "date", "Last Seen": "date", "Days Idle": "number",
            "Completed At": "date", "Milestones": "rich_text",
            "Sprint": "rich_text",
        },
        "CT_DESKTOPS": {
            "Row Key": "title", "Generated At": "date",
            "Coverage Complete": "checkbox", "History Checked": "checkbox",
            "Present": "checkbox",
            "Retired At": "date", "Evidence": "rich_text",
            "Evidence Count": "number", "Evidence Truncated": "checkbox",
            "Expected Team": "select", "Display Name": "rich_text",
            "Events": "number", "Projects": "rich_text", "Last Seen": "date",
            "Days Silent": "number", "Has Activity": "checkbox",
            "Role Mismatches": "number", "Mismatched Event IDs": "rich_text",
        },
        "CT_RISKS": {
            "Row Key": "title", "Generated At": "date",
            "Coverage Complete": "checkbox", "History Checked": "checkbox",
            "Present": "checkbox",
            "Retired At": "date", "Evidence": "rich_text",
            "Evidence Count": "number", "Evidence Truncated": "checkbox",
            "Kind": "select", "Project ID": "rich_text", "Team": "select",
            "Blocker": "rich_text", "Since": "date", "Days Open": "number",
            "Event ID": "rich_text", "Source": "select",
            "Claimed Role": "select", "Expected Role": "select",
            "Kept File": "rich_text", "Ignored File": "rich_text",
        },
    }

    @staticmethod
    def _current():
        return {
            database: {name: next(iter(spec)) for name, spec in properties.items()}
            for database, properties in projection.control_tower_databases().items()
        }

    def test_the_databases_are_the_recorded_ones(self):
        self.assertEqual(set(self._current()), set(self.RECORDED))

    def test_every_property_name_and_type_is_the_recorded_one(self):
        current = self._current()
        for database, recorded in self.RECORDED.items():
            with self.subTest(database=database):
                self.assertEqual(current[database], recorded)

    def test_no_recorded_property_has_lost_its_name(self):
        """The asymmetric half, stated on its own so the failure message
        says *rename* rather than *mismatch*.

        A property that disappears from the schema does not disappear from
        the workspace. It sits there with its last value, and a reader
        filtering on it gets an answer that stopped being updated on the day
        of the rename.
        """
        current = self._current()
        for database, recorded in self.RECORDED.items():
            missing = sorted(set(recorded) - set(current.get(database, {})))
            with self.subTest(database=database):
                self.assertEqual(
                    missing,
                    [],
                    f"{database} no longer declares {missing} — a live database "
                    "keeps the old property forever, holding whatever it last "
                    "had, with nothing writing to it",
                )

    def test_no_type_changed_under_an_existing_name(self):
        """Worse than a rename: Notion cannot change a property's type in
        place through this API, so the write simply fails against the real
        database while every test here passes."""
        current = self._current()
        for database, recorded in self.RECORDED.items():
            for name, kind in recorded.items():
                if name not in current.get(database, {}):
                    continue
                with self.subTest(database=database, property=name):
                    self.assertEqual(current[database][name], kind)

    def test_the_common_properties_are_identical_across_every_database(self):
        """They are one dict in the source; this is the value-level check
        that the schema builder really applies it uniformly, so an operator
        building five databases by hand gets five that agree."""
        current = self._current()
        shared = {
            name: kind.value for name, kind in projection.COMMON_PROPERTIES.items()
        }
        for database, properties in current.items():
            with self.subTest(database=database):
                self.assertEqual(
                    {name: properties[name] for name in shared}, shared
                )

    def test_the_gate_would_notice_a_rename(self):
        """The detector detects."""
        current = self._current()
        current["CT_PROJECTS"]["Idle Days"] = current["CT_PROJECTS"].pop("Days Idle")

        self.assertNotEqual(current["CT_PROJECTS"], self.RECORDED["CT_PROJECTS"])


class ControlTowerDatabasesAreNotContractedYetTests(unittest.TestCase):
    """docs/14 §1 fixes the Operational Projection as `Notion (PROJECTS /
    OPS_RUNS)`. Five more databases is a change to that table.

    This test pins the gap deliberately. It fails the day docs/14 is widened
    and `CONTRACTED_DATABASES` grows — which is the point: the decision gets
    made in the spec and this gate is what notices, instead of five databases
    appearing in an operator's workspace because a module was written.
    """

    def test_no_control_tower_database_is_contracted(self):
        for name in projection.control_tower_databases():
            with self.subTest(database=name):
                self.assertNotIn(name, CONTRACTED_DATABASES)

    def test_no_control_tower_database_is_in_the_bootstrap_schema(self):
        """`bootstrap_dashboard_databases()` creates what is in
        `DASHBOARD_DATABASES`. Nothing here may be reachable from it."""
        for name in projection.control_tower_databases():
            with self.subTest(database=name):
                self.assertNotIn(name, DASHBOARD_DATABASES)

    def test_the_names_cannot_collide_with_the_spec_databases(self):
        """`CT_PROJECTS` and the spec's `PROJECTS` are different tables with
        different owners — one is a per-project Control Tower rollup, the
        other is Current State written one Event at a time by
        `notion/sync.py`. The prefix is what keeps a reader from wiring a
        client to the wrong one."""
        for name in projection.control_tower_databases():
            with self.subTest(database=name):
                self.assertTrue(name.startswith("CT_"))
        self.assertNotIn("PROJECTS", projection.control_tower_databases())


class TheProjectionIsDeterministicTests(unittest.TestCase):
    """Two runs over one model produce the same bytes, so a diff between two
    Notion snapshots means a difference in the work."""

    def test_the_same_model_projects_the_same_payload_twice(self):
        model = _model()
        first = projection.project_panels(model)
        second = projection.project_panels(model)
        self.assertEqual(
            json.dumps([r.properties for r in first], ensure_ascii=False, sort_keys=False),
            json.dumps([r.properties for r in second], ensure_ascii=False, sort_keys=False),
        )

    def test_row_order_follows_the_panels_own_order(self):
        model = _model()
        expected = [row.key for row in model.panel("PROJECTS").rows]
        actual = [
            r.row_key for r in projection.project_panels(model) if r.database == "CT_PROJECTS"
        ]
        self.assertEqual(actual, expected)

    def test_the_payload_is_json_serialisable(self):
        json.dumps([r.properties for r in projection.project_panels(_model())])


class PresentAndNullColumnsSurviveTests(unittest.TestCase):
    """The panels are full of present-and-null columns on purpose, because
    "every row of this panel has the same shape" is a property the model
    states. A projection that dropped them, or that turned a null into a
    string, would break it at the boundary."""

    def test_a_project_with_no_blocker_still_has_every_date_property(self):
        row = next(
            r
            for r in projection.project_panels(_model())
            if r.database == "CT_PROJECTS" and r.row_key == "BRAND_CAMPAIGN"
        )
        self.assertIsNone(row.properties["Blocked Since"]["date"])
        self.assertIsNone(row.properties["Completed At"]["date"])
        self.assertIsNone(row.properties["Days Blocked"]["number"])

    def test_the_sprint_column_is_present_and_empty_rather_than_absent(self):
        row = next(
            r for r in projection.project_panels(_model()) if r.database == "CT_PROJECTS"
        )
        self.assertIn("Sprint", row.properties)
        self.assertEqual(_prop_text(row.properties["Sprint"], "rich_text"), "")

    def test_a_risk_row_of_the_other_kind_has_null_not_missing(self):
        rows = [
            r for r in projection.project_panels(_model()) if r.database == "CT_RISKS"
        ]
        blocker_rows = [r for r in rows if r.row_key.startswith("BLOCKER:")]
        mismatch_rows = [r for r in rows if r.row_key.startswith("MISMATCH:")]
        self.assertTrue(blocker_rows and mismatch_rows)
        self.assertIsNone(blocker_rows[0].properties["Source"]["select"])
        self.assertIsNone(mismatch_rows[0].properties["Days Open"]["number"])

    def test_a_boolean_never_lands_in_a_number_property(self):
        """`isinstance(True, int)` is True in Python, so a checkbox value
        reaching a number property would be written as 1 rather than
        refused."""
        for row in projection.project_panels(_model()):
            for name, payload in row.properties.items():
                if "number" not in payload:
                    continue
                with self.subTest(database=row.database, property=name):
                    self.assertNotIsInstance(payload["number"], bool)


class EvidenceReachesTheRowTests(unittest.TestCase):
    """"Execution Evidence" is one of the request's own Project/Sprint
    Dashboard fields, and it is the one that makes a number checkable."""

    def test_the_count_is_the_true_total_not_the_listed_length(self):
        events = tuple(
            _event(event_id=f"E-{i}", timestamp=f"2026-08-12T{i:02d}:00:00+09:00")
            for i in range(1, EVIDENCE_IN_PAYLOAD + 4)
        )
        row = next(
            r
            for r in projection.project_panels(_model(events=events))
            if r.database == "CT_PROJECTS"
        )
        self.assertEqual(row.properties["Evidence Count"]["number"], len(events))
        self.assertTrue(row.properties["Evidence Truncated"]["checkbox"])
        listed = _prop_text(row.properties["Evidence"], "rich_text").split(" | ")
        self.assertEqual(len(listed), EVIDENCE_IN_PAYLOAD)

    def test_an_untruncated_row_says_so(self):
        row = next(
            r
            for r in projection.project_panels(_model())
            if r.database == "CT_PROJECTS" and r.row_key == "BRAND_CAMPAIGN"
        )
        self.assertFalse(row.properties["Evidence Truncated"]["checkbox"])

    def test_each_ref_names_the_file_a_person_would_open(self):
        row = next(
            r
            for r in projection.project_panels(_model())
            if r.database == "CT_PROJECTS" and r.row_key == "BRAND_CAMPAIGN"
        )
        self.assertIn("E-3", _prop_text(row.properties["Evidence"], "rich_text"))


class CoverageQualifiesEveryRowTests(unittest.TestCase):
    """A row reading `Events 0` means something different when the evidence
    it was counted from is gone. `Coverage.complete` is that difference and
    it belongs on the row, because that is where the number is read."""

    def test_a_complete_run_marks_every_row_complete(self):
        """`with_history_coverage(None)` is what `ops_status.py` does on
        every run, and since C56 it is part of being complete: a model nobody
        asked cannot claim the whole picture."""
        model = _model().with_history_coverage(None)

        for row in projection.project_panels(model):
            with self.subTest(row=row.row_key):
                self.assertTrue(row.properties["Coverage Complete"]["checkbox"])
                self.assertTrue(row.properties["History Checked"]["checkbox"])

    def test_an_unchecked_model_says_which_of_the_two_is_missing(self):
        """The reason `History Checked` is its own column.

        `Coverage Complete = false` has two causes that call for opposite
        reactions — a damaged file to go and find, or an enrichment step that
        did not run. One checkbox cannot say which, and sending an operator
        after a corrupt file that does not exist is the worse of the two.
        """
        for row in projection.project_panels(_model()):
            with self.subTest(row=row.row_key):
                self.assertFalse(row.properties["Coverage Complete"]["checkbox"])
                self.assertFalse(row.properties["History Checked"]["checkbox"])

    def test_an_unreadable_file_marks_every_row_incomplete(self):
        model = _model()
        model = replace(
            model,
            unreadable=(("broken.json", "not JSON"),),
            coverage=replace(model.coverage, unreadable=1),
        )
        rows = projection.project_panels(model)
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row=row.row_key):
                self.assertFalse(row.properties["Coverage Complete"]["checkbox"])

    def test_history_that_outlives_its_evidence_marks_every_row_incomplete(self):
        """The restore case: Backup scope is `daily/` and `monthly/` only, so
        a restored machine gets all of its Company History and none of its
        Events. Every panel then truthfully says zero."""
        model = _model().with_history_coverage(NOW.date() - timedelta(days=30))
        for row in projection.project_panels(model):
            with self.subTest(row=row.row_key):
                self.assertFalse(row.properties["Coverage Complete"]["checkbox"])

    def test_generated_at_is_the_models_own_instant(self):
        model = _model()
        for row in projection.project_panels(model):
            with self.subTest(row=row.row_key):
                self.assertEqual(
                    row.properties["Generated At"]["date"]["start"], model.generated_at
                )


class TheWriteNeverBreaksTheRunTests(unittest.TestCase):
    """CEO Decision ④ — "Dashboard 기록 실패는 Runtime을 절대 중단시키면 안
    된다" — inherited from `record_run()`, which writes to the same kind of
    sink for the same kind of reason."""

    def test_no_clients_is_skipped_rather_than_an_error(self):
        for clients in (None, {}):
            with self.subTest(clients=clients):
                result = projection.sync_control_tower(clients, _model())
                self.assertIs(
                    result.outcome, projection.ProjectionOutcome.SKIPPED_NOT_CONFIGURED
                )

    def test_a_half_wired_deployment_writes_what_it_can(self):
        transport = InMemoryNotionTransport()
        clients = {"CT_PROJECTS": _client(transport, "db-CT_PROJECTS")}
        result = projection.sync_control_tower(clients, _model())
        self.assertIs(result.outcome, projection.ProjectionOutcome.RECORDED)
        self.assertGreater(result.created + result.updated, 0)
        self.assertGreater(result.skipped, 0)
        self.assertEqual(result.errors, ())

    def test_a_raising_transport_comes_back_as_a_result(self):
        class Exploding(InMemoryNotionTransport):
            def query_database(self, database_id, filter_):
                raise NotionAPIError("boom", status_code=503)

        transport = Exploding()
        result = projection.sync_control_tower(_all_clients(transport), _model())
        self.assertIs(result.outcome, projection.ProjectionOutcome.FAILED)
        self.assertTrue(result.errors)
        self.assertEqual(result.created + result.updated, 0)

    def test_one_failing_row_does_not_stop_the_others(self):
        class FailsOnce(InMemoryNotionTransport):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def create_page(self, database_id, properties):
                self.calls += 1
                if self.calls == 1:
                    raise NotionAPIError("first one fails", status_code=500)
                return super().create_page(database_id, properties)

        transport = FailsOnce()
        result = projection.sync_control_tower(_all_clients(transport), _model())
        self.assertIs(result.outcome, projection.ProjectionOutcome.FAILED)
        self.assertEqual(len(result.errors), 1)
        self.assertGreater(result.created + result.updated, 0)

    def test_a_payload_notion_would_refuse_is_refused_before_the_first_write(self):
        """All-or-nothing, because a half-updated projection is a table where
        some rows are this morning's and some are last week's with nothing
        saying which."""
        transport = InMemoryNotionTransport()
        model = _model()
        broken = replace(
            model,
            panels=tuple(
                replace(
                    panel,
                    rows=tuple(
                        replace(row, key=row.key) for row in panel.rows
                    ) + ((panel.rows[0],) if panel.rows else ()),
                )
                if panel.key == "PROJECTS"
                else panel
                for panel in model.panels
            ),
        )
        result = projection.sync_control_tower(_all_clients(transport), broken)
        self.assertIs(result.outcome, projection.ProjectionOutcome.REFUSED_INVALID)
        self.assertEqual(result.created + result.updated, 0)
        self.assertEqual(transport._pages, {})

    def test_a_model_that_cannot_be_projected_comes_back_as_failed(self):
        class Hostile(DashboardModel):
            def to_payload(self):
                raise RuntimeError("no payload")

        result = projection.sync_control_tower(
            _all_clients(InMemoryNotionTransport()), Hostile(generated_at="x")
        )
        self.assertIs(result.outcome, projection.ProjectionOutcome.FAILED)
        self.assertTrue(result.errors[0].startswith("RuntimeError"))


class _ListingClient:
    """A `NotionClient` stand-in whose `list_pages()` answer is the injection.

    Not an `InMemoryNotionTransport` subclass on purpose: the double is a
    *well-behaved* Notion, and these tests are about what arrives when the
    thing answering is not Notion — a proxy, a captive portal, an error body.
    Standing in at the client seam is the narrowest place to say that.
    """

    def __init__(self, pages, *, truncated: bool = False):
        self._pages = pages
        self.list_truncated = truncated
        self.created: list = []
        self.updates: list = []

    def find_by_title(self, *, property_name, value):
        return None

    def create_project(self, properties):
        self.created.append(properties)
        return {"id": f"page-{len(self.created)}"}

    def update_project(self, page_id, properties):
        self.updates.append((page_id, properties))
        return {"id": page_id}

    def list_pages(self):
        return self._pages

    @property
    def retirements(self):
        """The updates that actually retired a row.

        `"Retired At" in props` is not that test and getting it wrong made
        this class fail on its own control: every ordinary refresh carries
        `Retired At` too, as an explicit null — `project_panels()` writes it
        on every row so that a row which comes back has the stamp cleared.
        A retirement is `Present = false`.
        """
        return [
            props
            for _, props in self.updates
            if props.get("Present", {}).get("checkbox") is False
        ]


class ARemoteThatIsNotNotionCannotBreakTheRunTests(unittest.TestCase):
    """C64. `sync_control_tower()` says **Never raises** and did.

    `_reason()` already records the threat model this module was written
    against — "a proxy or captive portal answering in Notion's place is free
    to echo request headers back". The write path was hardened for it. The
    **reconciliation** path was not: `list_pages()` hands back whatever the
    response body's `results` held, and `_retire_absent_rows()` walked it
    assuming every element was a page-shaped mapping, outside any `try` in
    `sync_control_tower()`.

    Measured on HEAD over twelve injected response shapes: nine escaped as
    `AttributeError` — one line, `page.get("properties")`, and everything
    under it. CEO Decision ④ ("Dashboard 기록 실패는 Runtime을 절대
    중단시키면 안 된다") is the contract that broke, and it is the only
    promise this module makes to the Runner it is meant to be wired into.

    Fixed by reading the listing through **before** the first retirement and
    treating an unreadable answer the way a truncated one is already
    treated — reconcile nothing, say so in `unreconciled`.
    """

    #: The nine shapes measured as escaping on HEAD, plus `results is not
    #: iterable`. Listed in full rather than trimmed to one representative:
    #: each names a different line of the walk, and a fix that guarded one
    #: line would pass a one-shape test.
    HOSTILE = {
        "results is a list of strings": ["oops"],
        "results is a list of nulls": [None],
        "results is an error object": {"object": "error", "message": "x"},
        "results is a bare string": "unauthorized",
        "results is not iterable": 7,
        "properties is a string": [{"id": "1", "properties": "nope"}],
        "the row key property is a string": [
            {"id": "1", "properties": {"Row Key": "nope"}}
        ],
        "the title is a string": [
            {"id": "1", "properties": {"Row Key": {"title": "nope"}}}
        ],
        "a title item is a string": [
            {"id": "1", "properties": {"Row Key": {"title": ["nope"]}}}
        ],
        "Present is a string": [
            {"id": "1", "properties": {"Row Key": {"title": []}, "Present": "yes"}}
        ],
    }

    #: Readable listing, one row this projection did not write. Kept separate
    #: because the answer is different and should be: the listing **was**
    #: understood, so reconciliation runs, and only this row fails.
    ODD_BUT_READABLE = {
        "a page has no id": [{"properties": {"Row Key": {"title": []}}}],
    }

    def _clients(self, injected):
        """One database gets the hostile listing; the rest answer emptily."""
        names = sorted(projection.control_tower_databases())
        return {
            name: _ListingClient(injected if name == names[0] else [])
            for name in names
        }

    def test_no_response_shape_escapes_as_an_exception(self):
        for label, pages in {**self.HOSTILE, **self.ODD_BUT_READABLE}.items():
            with self.subTest(shape=label):
                try:
                    projection.sync_control_tower(self._clients(pages), _model())
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{label} raised {type(exc).__name__}: {exc}")

    def test_an_unreadable_listing_retires_nothing(self):
        """The direction that matters. Retiring on a listing this code does
        not understand would mark live rows absent — the failure the
        truncation branch already refuses for the same reason."""
        for label, pages in self.HOSTILE.items():
            with self.subTest(shape=label):
                clients = self._clients(pages)
                result = projection.sync_control_tower(clients, _model())
                first = clients[sorted(clients)[0]]
                self.assertEqual(first.retirements, [])
                self.assertEqual(result.retired, 0)

    def test_an_unreadable_listing_is_named_rather_than_counted(self):
        """`retired = 0` is indistinguishable from "nothing needed retiring",
        which is why `unreconciled` names the database instead."""
        names = sorted(projection.control_tower_databases())
        for label, pages in self.HOSTILE.items():
            with self.subTest(shape=label):
                result = projection.sync_control_tower(self._clients(pages), _model())
                self.assertIn(names[0], result.unreconciled)
                self.assertTrue(result.errors)

    def test_a_readable_listing_with_one_bad_row_still_reconciles(self):
        """The distinction the two corpora exist for. A page missing its `id`
        is a row this pass cannot act on; it is not evidence that the listing
        came from something other than Notion, so the other rows are still
        reconciled and the database is not declared unreconciled."""
        names = sorted(projection.control_tower_databases())
        for label, pages in self.ODD_BUT_READABLE.items():
            with self.subTest(shape=label):
                result = projection.sync_control_tower(self._clients(pages), _model())
                self.assertNotIn(names[0], result.unreconciled)
                self.assertEqual(result.retired, 0)
                self.assertTrue(result.errors)

    def test_the_rows_are_still_written(self):
        """A broken listing stops reconciliation and nothing else. The write
        happens first and is unaffected — half a Control Tower is what this
        function exists to still deliver."""
        clients = self._clients(["oops"])
        result = projection.sync_control_tower(clients, _model())
        self.assertGreater(sum(len(c.created) for c in clients.values()), 0)
        self.assertIs(result.outcome, projection.ProjectionOutcome.FAILED)

    def test_a_well_formed_listing_still_reconciles(self):
        """Guards the guard: a fix that refused every listing would pass every
        test above."""
        stale = {
            "id": "page-stale",
            "properties": {
                "Row Key": {
                    "title": [{"type": "text", "text": {"content": "GONE"},
                               "plain_text": "GONE"}]
                },
                "Present": {"checkbox": True},
            },
        }
        names = sorted(projection.control_tower_databases())
        clients = {
            name: _ListingClient([stale] if name == names[0] else [])
            for name in names
        }
        result = projection.sync_control_tower(clients, _model())
        self.assertEqual(result.unreconciled, ())
        self.assertEqual(result.retired, 1)
        self.assertEqual(clients[names[0]].updates[-1][0], "page-stale")


class TheRowKeyReaderRefusesRatherThanAnswersEmptyTests(unittest.TestCase):
    """`_row_key_of()`'s own contract, tested where it lives.

    Written because a mutation survived the tests above: making the
    `not isinstance(page, Mapping)` branch `return ""` instead of raising
    changed nothing observable, since `_is_present()` happens to raise on the
    same page a step later. That is a real property — but it is **coupling**,
    not the rule, and a later edit to `_is_present()` would silently hand this
    branch its old behaviour back.

    The rule itself: a page this code cannot read must not answer `""`,
    because `""` is not a live row key (`TheTitleColumnMayNeverBeNullTests`
    holds every projected row to a non-empty one) and a row whose key reads
    `""` is a row that gets **retired for not being understood**.
    """

    UNREADABLE = {
        "not an object": "nope",
        "null": None,
        "properties is a string": {"id": "1", "properties": "nope"},
        "the row key property is a string": {
            "id": "1", "properties": {"Row Key": "nope"}
        },
        "the title is a string": {
            "id": "1", "properties": {"Row Key": {"title": "nope"}}
        },
        "a title item is a string": {
            "id": "1", "properties": {"Row Key": {"title": ["nope"]}}
        },
    }

    def test_every_unreadable_shape_raises(self):
        for label, page in self.UNREADABLE.items():
            with self.subTest(shape=label):
                with self.assertRaises(TypeError):
                    projection._row_key_of(page)

    def test_a_genuinely_empty_title_is_not_an_error(self):
        """The distinction the raise exists to make. An empty title is a
        readable answer — a row a person added by hand — and it is the
        caller's business what to do with it, not this function's."""
        self.assertEqual(
            projection._row_key_of({"id": "1", "properties": {"Row Key": {"title": []}}}),
            "",
        )
        self.assertEqual(projection._row_key_of({"id": "1", "properties": {}}), "")
        self.assertEqual(projection._row_key_of({"id": "1"}), "")

    def test_the_reason_names_what_was_wrong(self):
        """A `TypeError` reaching an operator through `unreadable listing:`
        has to say more than that something was not a mapping."""
        with self.assertRaises(TypeError) as caught:
            projection._row_key_of({"id": "1", "properties": {"Row Key": "nope"}})
        self.assertIn("Row Key", str(caught.exception))
        self.assertIn("str", str(caught.exception))


class ThePresentReaderRefusesTheSameShapesTests(unittest.TestCase):
    """`_is_present()`'s own contract, for `_row_key_of()`'s reason.

    Written because a branch-coverage pass found the `properties is not a
    Mapping` raise here **unreachable**: `_retire_absent_rows()` evaluates
    `_row_key_of(page)` first in the same tuple, and that function rejects
    the identical shape a step earlier. Unreachable-in-production is fine;
    *untested* is not, because then the line is held up only by the order of
    two calls in one expression, and reordering that expression is an edit
    nobody would think twice about.

    Same lesson as `TheRowKeyReaderRefusesRatherThanAnswersEmptyTests`, from
    the other end: there a mutation survived, here coverage found the line
    before a mutation could.
    """

    def test_an_unreadable_properties_object_raises(self):
        with self.assertRaises(TypeError) as caught:
            projection._is_present({"id": "1", "properties": "nope"})
        self.assertIn("properties", str(caught.exception))

    def test_an_unreadable_present_value_raises(self):
        with self.assertRaises(TypeError) as caught:
            projection._is_present(
                {"id": "1", "properties": {"Present": "yes"}}
            )
        self.assertIn("Present", str(caught.exception))

    def test_an_absent_property_reads_as_present(self):
        """A database created before the column existed, or a row a person
        added by hand. Present until something says otherwise — the only
        thing False does is skip the row, and skipping a row that was never
        retired leaves a solved blocker on the operator's view forever."""
        self.assertTrue(projection._is_present({"id": "1", "properties": {}}))
        self.assertTrue(projection._is_present({"id": "1"}))
        self.assertTrue(
            projection._is_present({"id": "1", "properties": {"Present": {}}})
        )

    def test_the_checkbox_is_read_when_it_is_there(self):
        for value, expected in ((True, True), (False, False), (None, False)):
            with self.subTest(checkbox=value):
                self.assertEqual(
                    projection._is_present(
                        {"id": "1", "properties": {"Present": {"checkbox": value}}}
                    ),
                    expected,
                )


class ALiveRowIsNotRetiredBecauseOfHowNotionSpellsItsTitleTests(unittest.TestCase):
    """C64. The same Notion shape this repository has already been bitten by,
    at the one reader that had not learned it.

    Notion returns a title as one item **per run of identical formatting**,
    and an item that is not literal text — a mention, an equation — carries no
    `"text"` key at all. `notion/properties._extract_rich_text()` records the
    measurement that made it read `plain_text`; `notion/dashboard._page_title()`
    reads `plain_text` too. `_retire_absent_rows()` read `text.content`.

    Why that is worse here than a missed comparison: Notion's `title equals`
    filter compares **plain text**, so the row is found and refreshed — and
    then retired seconds later by the pass that could not read the key it had
    just written. Measured: a live `CT_PROJECTS` row went `Present = false`
    with `Retired At` stamped in the same sync, and every later run did it
    again. The operator's view filters on `Present`.
    """

    KEY = "SEARCH_BACKEND"

    SHAPES = {
        "plain text, as the API writes it": [
            {"type": "text", "text": {"content": KEY}, "plain_text": KEY},
        ],
        "two formatting runs": [
            {"type": "text", "text": {"content": "SEARCH_"}, "plain_text": "SEARCH_"},
            {"type": "text", "text": {"content": "BACKEND"}, "plain_text": "BACKEND"},
        ],
        "a mention run": [
            {"type": "mention", "mention": {"type": "page", "page": {"id": "x"}},
             "plain_text": KEY},
        ],
        "text and an equation": [
            {"type": "text", "text": {"content": "SEARCH_"}, "plain_text": "SEARCH_"},
            {"type": "equation", "equation": {"expression": "BACKEND"},
             "plain_text": "BACKEND"},
        ],
    }

    def _live_client(self, title_items):
        page = {
            "id": "page-live",
            "properties": {
                "Row Key": {"type": "title", "title": title_items},
                "Present": {"checkbox": True},
            },
        }

        class Live(_ListingClient):
            def find_by_title(self, *, property_name, value):
                # What Notion's filter does: compare the plain text.
                plain = "".join(
                    item.get("plain_text", "") for item in title_items
                )
                return page if plain == value else None

        return Live([page])

    def _model_for_one_project(self):
        return _model((_event(event_id="E-1", project_id=self.KEY),))

    def test_no_title_shape_retires_the_live_row(self):
        for label, items in self.SHAPES.items():
            with self.subTest(shape=label):
                client = self._live_client(items)
                names = sorted(projection.control_tower_databases())
                clients = {
                    name: (client if name == "CT_PROJECTS" else _ListingClient([]))
                    for name in names
                }
                projection.sync_control_tower(clients, self._model_for_one_project())
                self.assertEqual(
                    client.retirements, [], f"{label}: a live row was retired"
                )

    def test_the_row_is_still_refreshed(self):
        """Not vacuous: the row must be found and updated, which is what makes
        the retirement above a contradiction rather than a no-op."""
        for label, items in self.SHAPES.items():
            with self.subTest(shape=label):
                client = self._live_client(items)
                names = sorted(projection.control_tower_databases())
                clients = {
                    name: (client if name == "CT_PROJECTS" else _ListingClient([]))
                    for name in names
                }
                projection.sync_control_tower(clients, self._model_for_one_project())
                self.assertEqual(client.created, [])
                self.assertTrue(client.updates)

    def test_a_row_that_really_is_gone_is_still_retired(self):
        """The other direction, in the same shapes — a reader that answered
        every title `""` would pass the two tests above and retire nothing
        ever."""
        gone = [{"type": "mention", "mention": {}, "plain_text": "RETIRED_PROJECT"}]
        client = self._live_client(gone)
        names = sorted(projection.control_tower_databases())
        clients = {
            name: (client if name == "CT_PROJECTS" else _ListingClient([]))
            for name in names
        }
        result = projection.sync_control_tower(clients, self._model_for_one_project())
        self.assertEqual(result.retired, 1)

    def test_the_reader_is_the_one_the_rest_of_the_project_uses(self):
        """The fix is a rule this repository already stated twice. Pinned
        against both, so a change to the rule cannot leave one behind."""
        from notion.properties import _extract_rich_text

        item = {"type": "mention", "mention": {}, "plain_text": "X"}
        self.assertEqual(
            projection._row_key_of(
                {"id": "1", "properties": {"Row Key": {"title": [item]}}}
            ),
            _extract_rich_text({"rich_text": [item]}),
        )


class TheRowIsRefreshedNotFrozenTests(unittest.TestCase):
    """The defect this projection would have shipped with.

    `find_or_create_by_title()` is the right primitive for `OPS_RUNS`, whose
    row key is a `Run ID` — unique to one execution, so an existing row is a
    finished one. Every key here is an identity that **outlives every run**:
    a `project_id`, a team, a Desktop. Find-or-create would have written each
    row once and never touched it again, freezing `Events`, `State`,
    `Blocker` and `Generated At` at whatever they were the first time the
    projection ran, forever, while the terminal beside it showed the truth.
    """

    def test_a_second_run_adds_no_row(self):
        transport = InMemoryNotionTransport()
        clients = _all_clients(transport)
        first = projection.sync_control_tower(clients, _model())
        pages_after_first = len(transport._pages)
        second = projection.sync_control_tower(clients, _model())

        self.assertEqual(len(transport._pages), pages_after_first)
        self.assertEqual(first.created, pages_after_first)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, pages_after_first)

    def test_a_changed_number_reaches_the_existing_row(self):
        transport = InMemoryNotionTransport()
        clients = _all_clients(transport)
        projection.sync_control_tower(clients, _model())

        more = FULL_EVENTS + (
            _event(event_id="E-6", timestamp="2026-08-13T15:00:00+09:00"),
        )
        projection.sync_control_tower(clients, _model(events=more))

        row = next(
            page
            for page_id, page in transport._pages.items()
            if transport._page_database[page_id] == "db-CT_PROJECTS"
            and _prop_text(page["properties"][projection.ROW_KEY_PROPERTY], "title")
            == "SEARCH_BACKEND"
        )
        self.assertEqual(row["properties"]["Events"]["number"], 3)

    def test_a_resolved_blocker_clears_on_the_existing_row(self):
        """The worst version of a frozen row: BLOCKED forever on a project
        that resumed. `state` is folded, not read off the last Event, so the
        Control Tower knows — the row is the only thing that would not."""
        transport = InMemoryNotionTransport()
        clients = _all_clients(transport)
        projection.sync_control_tower(clients, _model())

        resumed = FULL_EVENTS + (
            _event(
                event_id="E-R",
                project_id="SEARCH_BACKEND",
                event_type="RESUMED",
                timestamp="2026-08-13T16:00:00+09:00",
            ),
        )
        projection.sync_control_tower(clients, _model(events=resumed))

        row = next(
            page
            for page_id, page in transport._pages.items()
            if transport._page_database[page_id] == "db-CT_PROJECTS"
            and _prop_text(page["properties"][projection.ROW_KEY_PROPERTY], "title")
            == "SEARCH_BACKEND"
        )
        self.assertEqual(row["properties"]["State"]["select"]["name"], "ACTIVE")
        self.assertEqual(_prop_text(row["properties"]["Blocker"], "rich_text"), "")

    def test_generated_at_advances(self):
        transport = InMemoryNotionTransport()
        clients = _all_clients(transport)
        projection.sync_control_tower(clients, _model())
        later = NOW + timedelta(days=1)
        projection.sync_control_tower(clients, _model(now=later))

        for page_id, page in transport._pages.items():
            with self.subTest(page=page_id):
                self.assertEqual(
                    page["properties"]["Generated At"]["date"]["start"],
                    later.isoformat(),
                )

    def test_each_database_keeps_its_own_rows(self):
        """One transport serves five databases, which is how production wires
        it. A projection that ignored the database id would let a Team row
        answer a lookup for a Project row."""
        transport = InMemoryNotionTransport()
        projection.sync_control_tower(_all_clients(transport), _model())
        databases = {transport._page_database[p] for p in transport._pages}
        self.assertEqual(
            databases,
            {f"db-{name}" for name in projection.control_tower_databases()},
        )


class ARowWhoseSubjectIsGoneIsRetiredTests(unittest.TestCase):
    """The failure find-then-update cannot see on its own.

    Create refreshes nothing and update visits only rows this run produced.
    A RISK row is written when a project reports BLOCKED, and when the
    project reports RESUMED the risk simply stops being produced — so nothing
    ever visits that row again and the operator's one at-a-glance view keeps
    reporting a solved problem. Measured end to end before the reconciliation
    pass existed: the CT_PROJECTS row went `BLOCKED -> ACTIVE` with an empty
    `Blocker`, and the CT_RISKS row beside it still said `OPEN_BLOCKER`.

    Marked, never deleted: this repository does not delete, and `Retired At`
    is the one fact an archived page would take with it.
    """

    RESOLVED = FULL_EVENTS + (
        _event(
            event_id="E-R",
            project_id="SEARCH_BACKEND",
            event_type="RESUMED",
            timestamp="2026-08-13T16:00:00+09:00",
        ),
    )

    def setUp(self):
        self.transport = InMemoryNotionTransport()
        self.clients = _all_clients(self.transport)

    def _risk_rows(self):
        return [
            page
            for page_id, page in self.transport._pages.items()
            if self.transport._page_database[page_id] == "db-CT_RISKS"
        ]

    def test_a_resolved_blocker_is_marked_not_left_open(self):
        projection.sync_control_tower(self.clients, _model())
        self.assertTrue(self._risk_rows())

        result = projection.sync_control_tower(self.clients, _model(events=self.RESOLVED))

        self.assertEqual(result.retired, 1)
        row = next(
            r
            for r in self._risk_rows()
            if _prop_text(r["properties"][projection.ROW_KEY_PROPERTY], "title")
            == "BLOCKER:SEARCH_BACKEND"
        )
        self.assertFalse(row["properties"]["Present"]["checkbox"])

    def test_the_row_is_kept_rather_than_deleted(self):
        projection.sync_control_tower(self.clients, _model())
        before = len(self._risk_rows())

        projection.sync_control_tower(self.clients, _model(events=self.RESOLVED))

        self.assertEqual(len(self._risk_rows()), before)

    def test_retired_at_is_the_models_instant_not_a_clock_read(self):
        projection.sync_control_tower(self.clients, _model())
        later = NOW + timedelta(days=3)

        projection.sync_control_tower(self.clients, _model(events=self.RESOLVED, now=later))

        row = next(
            r
            for r in self._risk_rows()
            if _prop_text(r["properties"][projection.ROW_KEY_PROPERTY], "title")
            == "BLOCKER:SEARCH_BACKEND"
        )
        self.assertEqual(row["properties"]["Retired At"]["date"]["start"], later.isoformat())

    def test_a_risk_that_comes_back_is_present_again_with_no_retired_at(self):
        """Blocked, resolved, blocked again. A retirement that could not be
        undone would leave the second blocker invisible in the one view an
        operator filters on `Present`."""
        projection.sync_control_tower(self.clients, _model())
        projection.sync_control_tower(self.clients, _model(events=self.RESOLVED))
        again = self.RESOLVED + (
            _event(
                event_id="E-B2",
                project_id="SEARCH_BACKEND",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="다시 막혔다",
                timestamp="2026-08-13T18:00:00+09:00",
            ),
        )

        projection.sync_control_tower(self.clients, _model(events=again))

        row = next(
            r
            for r in self._risk_rows()
            if _prop_text(r["properties"][projection.ROW_KEY_PROPERTY], "title")
            == "BLOCKER:SEARCH_BACKEND"
        )
        self.assertTrue(row["properties"]["Present"]["checkbox"])
        self.assertIsNone(row["properties"]["Retired At"]["date"])

    def test_an_already_retired_row_is_not_rewritten_every_run(self):
        """`Retired At` would otherwise walk forward forever, and a quiet
        week would cost one API call per historical row per run."""
        projection.sync_control_tower(self.clients, _model())
        projection.sync_control_tower(self.clients, _model(events=self.RESOLVED))

        third = projection.sync_control_tower(self.clients, _model(events=self.RESOLVED))

        self.assertEqual(third.retired, 0)

    def test_a_live_row_is_never_retired(self):
        projection.sync_control_tower(self.clients, _model())
        projection.sync_control_tower(self.clients, _model())

        for page in self.transport._pages.values():
            with self.subTest(row=page["id"]):
                self.assertTrue(page["properties"]["Present"]["checkbox"])

    def test_reconciliation_does_not_cross_databases(self):
        """One transport serves five databases. A retire pass that ignored
        the database id would mark every Team row absent while reconciling
        CT_RISKS."""
        projection.sync_control_tower(self.clients, _model())
        projection.sync_control_tower(self.clients, _model(events=self.RESOLVED))

        for page_id, page in self.transport._pages.items():
            if self.transport._page_database[page_id] == "db-CT_RISKS":
                continue
            with self.subTest(database=self.transport._page_database[page_id]):
                self.assertTrue(page["properties"]["Present"]["checkbox"])

    def test_a_database_with_no_rows_this_run_is_still_reconciled(self):
        """The case a per-row loop cannot reach: `CT_RISKS` produces nothing
        at all on the first quiet day, so a reconciliation driven by the rows
        this run wrote would skip exactly the database that needs it."""
        projection.sync_control_tower(self.clients, _model())
        quiet = tuple(e for e in FULL_EVENTS if e.event_type != "BLOCKED")

        result = projection.sync_control_tower(self.clients, _model(events=quiet))

        self.assertGreaterEqual(result.retired, 1)
        self.assertEqual(result.unreconciled, ())


class ATransportThatCannotListSaysSoTests(unittest.TestCase):
    """`list_pages()` is an optional capability, like `search_pages()`.

    Every `NotionTransport` double in this repository predates it. A
    reconciliation that cannot run has to degrade to "not attempted" and say
    which databases — a `0` in `retired` is indistinguishable from "nothing
    needed retiring", and that is the difference between a clean run and a
    view full of solved problems.
    """

    class _CannotList(InMemoryNotionTransport):
        def list_pages(self, database_id):
            raise NotImplementedError("this transport cannot list a database")

    def test_the_write_still_happens(self):
        transport = self._CannotList()
        result = projection.sync_control_tower(_all_clients(transport), _model())

        self.assertIs(result.outcome, projection.ProjectionOutcome.RECORDED)
        self.assertGreater(result.created, 0)
        self.assertEqual(result.errors, ())

    def test_every_database_is_named_as_unreconciled(self):
        transport = self._CannotList()
        result = projection.sync_control_tower(_all_clients(transport), _model())

        self.assertEqual(
            set(result.unreconciled), set(projection.control_tower_databases())
        )
        self.assertEqual(result.retired, 0)

    def test_a_truncated_listing_reconciles_nothing_rather_than_guessing(self):
        """Retiring against a partial listing would mark every row it did not
        see. Not reconciling is the safe direction and the flag says so."""

        class _Truncating(InMemoryNotionTransport):
            list_truncated = True

            def list_pages(self, database_id):
                return []

        transport = _Truncating()
        result = projection.sync_control_tower(_all_clients(transport), _model())

        self.assertEqual(result.retired, 0)
        self.assertEqual(
            set(result.unreconciled), set(projection.control_tower_databases())
        )

    def test_a_listing_that_raises_becomes_an_error_not_an_exception(self):
        class _Explodes(InMemoryNotionTransport):
            def list_pages(self, database_id):
                raise NotionAPIError("gateway", status_code=502)

        result = projection.sync_control_tower(_all_clients(_Explodes()), _model())

        self.assertIs(result.outcome, projection.ProjectionOutcome.FAILED)
        self.assertTrue(result.errors)
        self.assertGreater(result.created, 0)

    def test_a_retire_write_that_fails_is_reported_and_the_rest_continue(self):
        """Only the retire write fails here.

        The obvious fixture — refuse any update carrying `Present` — refuses
        *every* update, because an ordinary row write carries `Present` too:
        it is a common property. The retire write is the one that carries
        **nothing else**, so that is what this keys on. Without the
        distinction the test would pass while proving something much weaker
        than its name.
        """

        class _FailsOnRetire(InMemoryNotionTransport):
            fail_retires = False

            def update_page(self, page_id, properties):
                if self.fail_retires and set(properties) == {"Present", "Retired At"}:
                    raise NotionAPIError("nope", status_code=500)
                return super().update_page(page_id, properties)

        transport = _FailsOnRetire()
        clients = _all_clients(transport)
        projection.sync_control_tower(clients, _model())
        transport.fail_retires = True

        resolved = FULL_EVENTS + (
            _event(
                event_id="E-R",
                project_id="SEARCH_BACKEND",
                event_type="RESUMED",
                timestamp="2026-08-13T16:00:00+09:00",
            ),
        )
        result = projection.sync_control_tower(clients, _model(events=resolved))

        self.assertIs(result.outcome, projection.ProjectionOutcome.FAILED)
        self.assertTrue(any("retire" in e for e in result.errors), result.errors)
        self.assertEqual(result.retired, 0)
        # The ordinary rows still went through — a failed reconciliation must
        # not cost the update it runs after.
        self.assertGreater(result.updated, 0)
        row = next(
            page
            for page_id, page in transport._pages.items()
            if transport._page_database[page_id] == "db-CT_PROJECTS"
            and _prop_text(page["properties"][projection.ROW_KEY_PROPERTY], "title")
            == "SEARCH_BACKEND"
        )
        self.assertEqual(row["properties"]["State"]["select"]["name"], "ACTIVE")

    def test_the_in_memory_double_scopes_its_listing_to_one_database(self):
        transport = InMemoryNotionTransport()
        projection.sync_control_tower(_all_clients(transport), _model())

        listed = transport.list_pages("db-CT_TEAMS")
        self.assertTrue(listed)
        self.assertEqual(len(listed), len(_model().panel("TEAMS").rows))


class ThePayloadDoesNotGrowWithTheWorkTests(unittest.TestCase):
    """docs/14 §3's rule, one layer out.

    "Manifest는 Event 1건당 줄을 쓰지 않는다 … 작업량에 비례해 커지는 것은
    로그이며, 그러면 Manifest일 수 없다." The first version of
    `to_payload()` broke it and `EVIDENCE_IN_PAYLOAD` is the fix; a Notion
    projection can break it again in two new ways — a row per Event, or a
    text cell that concatenates every milestone a project ever had.

    Measured on this machine, real fold, 50 projects:

        n=1,000 Events   67 rows   81.4 KB   project 10.3 ms   sync 17.7 ms
        n=6,000 Events   67 rows  116.2 KB   project 18.0 ms   sync 19.3 ms

    Six times the work, the same row count, and the payload grows by half —
    all of it in `Milestones`, which `fit_properties()` bounds at
    `RICH_TEXT_LIMIT` per row. Bounded by construction, not by luck.
    """

    #: 50 projects across the four Desktops, with a distinct milestone per
    #: Event so `Milestones` is the cell that grows.
    PROJECTS = 8

    def _events(self, count):
        sources = ("DESKTOP_1", "DESKTOP_2", "DESKTOP_3", "DESKTOP_4")
        roles = {
            "DESKTOP_1": "CTO_BACKEND",
            "DESKTOP_2": "CMO",
            "DESKTOP_3": "CTO_FRONTEND",
            "DESKTOP_4": "COO",
        }
        events = []
        for i in range(count):
            source = sources[i % 4]
            events.append(
                _event(
                    event_id=f"E-{i}",
                    source=source,
                    role=roles[source],
                    project_id=f"PROJ_{i % self.PROJECTS}",
                    milestone=f"Milestone {i}",
                    timestamp=f"2026-08-{(i % 12) + 1:02d}T10:00:00+09:00",
                )
            )
        return tuple(events)

    def test_the_row_count_does_not_follow_the_event_count(self):
        small = projection.project_panels(_model(events=self._events(60)))
        large = projection.project_panels(_model(events=self._events(600)))

        self.assertEqual(len(small), len(large))

    def test_no_row_is_written_per_event(self):
        events = self._events(600)
        rows = projection.project_panels(_model(events=events))

        self.assertLess(len(rows), len(events) // 4)

    def test_every_text_cell_stays_under_the_api_limit_at_scale(self):
        for row in projection.project_panels(_model(events=self._events(600))):
            for name, payload in row.properties.items():
                for kind in ("title", "rich_text"):
                    if kind not in payload:
                        continue
                    with self.subTest(database=row.database, property=name):
                        self.assertLessEqual(
                            len(_prop_text(payload, kind)), RICH_TEXT_LIMIT
                        )

    def test_the_whole_payload_stays_small_enough_to_read(self):
        """A hard ceiling rather than a ratio: the row count is fixed and
        every cell is capped, so the product is capped too. 1 MB is far above
        the 116 KB measured at 6,000 Events and far below anything that would
        make a projection a log."""
        payload = json.dumps(
            [r.properties for r in projection.project_panels(_model(events=self._events(600)))],
            ensure_ascii=False,
        )
        self.assertLess(len(payload.encode("utf-8")), 1_000_000)

    def test_the_evidence_cell_is_capped_no_matter_how_much_there_is(self):
        rows = projection.project_panels(_model(events=self._events(600)))
        for row in rows:
            cell = _prop_text(row.properties["Evidence"], "rich_text")
            with self.subTest(database=row.database, row=row.row_key):
                self.assertLessEqual(
                    len([part for part in cell.split(" | ") if part]),
                    EVIDENCE_IN_PAYLOAD,
                )


class AHandBuiltModelIsAnsweredTests(unittest.TestCase):
    """The defensive branches, reached the only way they can be."""

    def test_a_panel_with_no_projection_is_skipped_rather_than_guessed(self):
        model = DashboardModel(
            generated_at=NOW.isoformat(),
            panels=(
                DashboardPanel(
                    key="INVENTED",
                    title="Invented",
                    status=PanelStatus.SOURCED,
                    columns=("a",),
                    rows=(DashboardRow(key="k", values={"a": 1}),),
                ),
            ),
        )
        self.assertEqual(projection.project_panels(model), [])

    def test_a_client_for_a_database_this_module_does_not_own_is_ignored(self):
        """A deployment that hands over one extra client — a PROJECTS or an
        OPS_RUNS one, both of which a real wiring already has — must not have
        its rows reconciled against a projection that produces none of them.
        Every row in that database would be retired."""
        transport = InMemoryNotionTransport()
        clients = dict(_all_clients(transport))
        clients["PROJECTS"] = _client(transport, "db-PROJECTS")
        transport.create_page("db-PROJECTS", {"Project ID": {"rich_text": []}})

        result = projection.sync_control_tower(clients, _model())

        self.assertEqual(result.retired, 0)
        self.assertNotIn("PROJECTS", result.unreconciled)
        self.assertNotIn("Present", transport._pages["mock-page-1"]["properties"])

    def test_a_boolean_reaching_a_text_property_renders_as_a_word(self):
        """Unreachable through `build_dashboard()` — every boolean column in
        `PANEL_PROJECTIONS` is a CHECKBOX — and characterised rather than
        removed, like `PropertyHelperNullGuardTests` does for the null case.
        `str(True)` would put `True` in a cell beside `false` from another,
        and the two would be the same fact spelled two ways."""
        self.assertEqual(
            _prop_text(
                projection._property(projection.PropertyType.RICH_TEXT, True),
                "rich_text",
            ),
            "true",
        )
        self.assertEqual(
            _prop_text(
                projection._property(projection.PropertyType.RICH_TEXT, [True, False]),
                "rich_text",
            ),
            "true, false",
        )

    def test_a_model_with_no_panels_projects_nothing(self):
        self.assertEqual(
            projection.project_panels(DashboardModel(generated_at=NOW.isoformat())), []
        )
        self.assertEqual(projection.validate_rows([]), [])


if __name__ == "__main__":
    unittest.main()
