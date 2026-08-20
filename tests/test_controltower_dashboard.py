"""Control Tower Dashboard Model tests (C48).

`controltower/dashboard.py` is the layer between the rollup and anything that
wants to *show* it. Five properties matter more than the shapes it returns,
and each has its own class below:

    an unsourced panel is not an empty panel
        "아무 일도 없었다" and "물어볼 곳이 없다" render identically unless the
        model distinguishes them, and they mean opposite things

    every row fills the columns its panel declared
        the columns are what a projection creates; a row carrying a field the
        panel never announced reaches Notion as a column nobody made

    the screen and the payload are the same facts
        the whole reason this layer exists — `ops_status.py` renders from the
        model, so a projection of the model cannot disagree with the screen

    authored text is redacted on the way OUT, never on the way in
        the model keeps `blocker` / `project_id` verbatim so `EvidenceRef`
        still finds the file; `to_payload()` is the boundary

    nothing is invented
        every count in the model is a count the rollup already made
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controltower import (  # noqa: E402
    UNSOURCED_LAYERS,
    DashboardModel,
    PanelStatus,
    build_company_rollup,
    build_dashboard,
    unsourced_layer_coverage,
)
from controltower.dashboard import (  # noqa: E402
    EVIDENCE_IN_PAYLOAD,
    PROJECT_STATES,
    _UNAUTHORED_KEYS,
)
from events import ROLES, create_event  # noqa: E402
from oplog import one_line, redact  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=KST)

SOURCE_FOR_ROLE = {
    "CTO_BACKEND": "DESKTOP_1",
    "CMO": "DESKTOP_2",
    "CTO_FRONTEND": "DESKTOP_3",
    "COO": "DESKTOP_4",
}

# A credential-shaped string, for every test that needs authored text which
# must not leave the machine. Built by concatenation rather than written out,
# because `SecretExposureGuardTests.test_no_secret_material_in_any_tracked_
# file` scans every tracked file for exactly this shape — and it should: a
# test fixture and a leaked token are indistinguishable to a scanner, and the
# scanner is the thing that has to stay strict.
SECRET = "ntn_" + "A" * 24


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Laid out as a real runtime tree, not just a bare directory, so
        # `TheScreenAndThePayloadCarryTheSameFactsTests` can point the actual
        # `ops_status.py` block at it and compare what it prints with the
        # model built from the same files.
        self.runtime = Path(tmp.name)
        self.processed = self.runtime / "events" / "processed"
        self.processed.mkdir(parents=True)

    def put(self, event_id, project, role, event_type, status, day, **extra):
        source = extra.pop("source", None) or SOURCE_FOR_ROLE[role]
        event = create_event(
            source=source,
            role=role,
            project_id=project,
            event_type=event_type,
            status=status,
            summary=f"summary for {event_id}",
            history_candidate=True,
            event_id=event_id,
            timestamp=f"2026-08-{day:02d}T09:00:00+09:00",
            **extra,
        )
        (self.processed / f"{event_id}.json").write_text(
            event.to_json(), encoding="utf-8"
        )
        return event

    def rollup(self, **kwargs):
        return build_company_rollup(processed_dir=self.processed, now=NOW, **kwargs)

    def model(self, **kwargs) -> DashboardModel:
        return build_dashboard(self.rollup(**kwargs), now=NOW)


class EveryUnsourcedLayerIsClaimedByExactlyOnePanelTests(DashboardTestCase):
    """The property that stops the model quietly filling a layer in.

    `UNSOURCED_LAYERS` names four layers this system has no source for. If a
    panel is added for one of them without a source, or a layer gains a
    source and its panel keeps declaring it missing, the two lists disagree —
    and this is where that shows up rather than on an operator's screen.
    """

    def test_every_unsourced_layer_is_accounted_for(self):
        coverage = unsourced_layer_coverage(self.model())

        self.assertEqual(set(coverage), set(UNSOURCED_LAYERS))

    def test_no_layer_is_claimed_by_two_panels(self):
        model = self.model()
        claims = [
            (layer, panel.key)
            for panel in model.panels
            for layer in panel.unsourced_layers
        ]

        self.assertEqual(len(claims), len({layer for layer, _ in claims}))

    def test_an_unsourced_panel_carries_no_rows_at_all(self):
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        for panel in self.model().unsourced_panels:
            with self.subTest(panel=panel.key):
                self.assertEqual(panel.rows, ())
                self.assertEqual(panel.columns, ())
                self.assertEqual(panel.source, "")

    def test_an_unsourced_panel_says_what_decision_would_supply_it(self):
        for panel in self.model().unsourced_panels:
            with self.subTest(panel=panel.key):
                self.assertTrue(panel.note)
                self.assertIn("BACKLOG", panel.note)

    def test_a_sourced_panel_with_no_rows_is_still_sourced(self):
        """The distinction the whole enum exists for.

        An empty `processed/` means no project moved — a true statement about
        a real source. It must not read as "there is no such thing as a
        project here", which is what the Goal panel says.
        """
        model = self.model()

        projects = model.panel("PROJECTS")
        self.assertEqual(projects.rows, ())
        self.assertIs(projects.status, PanelStatus.SOURCED)
        self.assertTrue(projects.source)
        self.assertEqual(projects.unsourced_layers, ())

    def test_the_sourced_panels_name_where_their_rows_came_from(self):
        for panel in self.model().panels:
            if panel.status is not PanelStatus.SOURCED:
                continue
            with self.subTest(panel=panel.key):
                self.assertTrue(panel.source.strip(), panel.key)

    def test_the_team_and_project_panels_carry_a_null_sprint_rather_than_no_sprint(self):
        """Present-and-null, not absent.

        A consumer that never sees the column learns nothing and may invent
        one; a consumer that gets it with a null learns that this system has
        no Sprint, and the SPRINTS panel says why.
        """
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        model = self.model()

        self.assertIn("current_sprint", model.panel("TEAMS").columns)
        self.assertIn("sprint", model.panel("PROJECTS").columns)
        for row in model.panel("TEAMS").rows:
            self.assertIsNone(row.values["current_sprint"])
        for row in model.panel("PROJECTS").rows:
            self.assertIsNone(row.values["sprint"])


class EveryRowFillsTheColumnsItsPanelDeclaresTests(DashboardTestCase):
    """`columns` is what a projection creates; `values` is what it writes."""

    def _populate(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "BRAND", "CMO", "MILESTONE_COMPLETED", "IN_PROGRESS", 8, milestone="M1")
        self.put("E3", "SEARCH", "COO", "COMPLETED", "COMPLETED", 9)
        self.put("E4", "PAY", "CMO", "DECISION_APPROVED", "IN_PROGRESS", 10, source="DESKTOP_1")

    def test_every_row_has_exactly_its_panels_columns(self):
        self._populate()

        for panel in self.model().panels:
            for row in panel.rows:
                with self.subTest(panel=panel.key, row=row.key):
                    self.assertEqual(set(row.values), set(panel.columns))

    def test_the_payload_row_has_exactly_its_panels_columns(self):
        self._populate()

        for panel in self.model().to_payload()["panels"]:
            for row in panel["rows"]:
                with self.subTest(panel=panel["key"], row=row["key"]):
                    self.assertEqual(set(row["values"]), set(panel["columns"]))

    def test_every_row_key_is_unique_within_its_panel(self):
        """A projection diffs two payloads by row key; duplicates make that
        ambiguous, and the RISKS panel is the one that could produce them —
        it holds two kinds of row in one table."""
        self._populate()

        for panel in self.model().panels:
            with self.subTest(panel=panel.key):
                keys = [row.key for row in panel.rows]
                self.assertEqual(len(keys), len(set(keys)))

    def test_the_two_risk_kinds_share_one_row_shape(self):
        self._populate()
        rows = self.model().panel("RISKS").rows

        self.assertEqual(
            sorted({row.values["kind"] for row in rows}),
            ["OPEN_BLOCKER", "ROLE_MISMATCH"],
        )
        for row in rows:
            with self.subTest(row=row.key):
                self.assertEqual(set(row.values), set(self.model().panel("RISKS").columns))

    def test_every_row_carries_the_evidence_it_was_built_from(self):
        self._populate()
        model = self.model()

        for panel in model.panels:
            if panel.key in ("METRICS",):
                # `projects_active` and `teams_silent` are counts over things
                # rather than over Events — `rollup._roll_metrics` gives them
                # no evidence on purpose, and inventing some here would be
                # the invention this module refuses.
                continue
            for row in panel.rows:
                if panel.key in ("TEAMS", "DESKTOPS") and row.values["events"] == 0:
                    continue  # present-and-empty; there is nothing to cite
                with self.subTest(panel=panel.key, row=row.key):
                    self.assertTrue(row.evidence, f"{panel.key}/{row.key}")
                    for ref in row.evidence:
                        self.assertTrue((self.processed / ref.path).is_file())


class TheScreenAndThePayloadCarryTheSameFactsTests(DashboardTestCase):
    """Why this layer exists at all.

    `ops_status.py::_print_control_tower()` renders from the model, so the
    screen and any projection of the model are the same arrangement of the
    same facts. These are the equalities that would break first if either
    side started deriving its own.
    """

    def _populate(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "BRAND", "CMO", "MILESTONE_COMPLETED", "IN_PROGRESS", 8, milestone="M1")
        self.put("E3", "SEARCH", "COO", "COMPLETED", "COMPLETED", 9)

    def test_every_kpi_on_the_panel_equals_the_rollups_own_metric(self):
        self._populate()
        rollup = self.rollup()
        model = build_dashboard(rollup, now=NOW)

        for row in model.panel("METRICS").rows:
            with self.subTest(metric=row.key):
                self.assertEqual(row.values["value"], rollup.metric(row.key).value)
                self.assertEqual(
                    row.values["derived_from"], rollup.metric(row.key).source
                )

    def test_the_panel_carries_every_metric_the_rollup_produced(self):
        self._populate()
        rollup = self.rollup()

        self.assertEqual(
            [row.key for row in build_dashboard(rollup, now=NOW).panel("METRICS").rows],
            [metric.key for metric in rollup.metrics],
        )

    def test_the_printed_project_order_is_the_models_row_order(self):
        self._populate()
        self.put("E4", "QUIET", "CTO_FRONTEND", "STARTED", "IN_PROGRESS", 1)

        # The blocked marker is `!` and the unblocked one is a space, so the
        # project id is the first field of the line either way once the
        # marker is accounted for.
        order = [
            parts[1] if parts[0] == "!" else parts[0]
            for parts in (line.split() for line in self._print())
        ]
        model_order = [row.key for row in self.model().panel("PROJECTS").rows]

        self.assertEqual(order, model_order)

    def test_the_printed_blocked_project_is_the_models_blocked_row(self):
        self._populate()

        model = self.model()
        blocked = [
            row.key for row in model.panel("PROJECTS").rows
            if row.values["state"] == "BLOCKED"
        ]

        self.assertEqual(blocked, ["PAY"])
        self.assertEqual(
            [line.split()[1] for line in self._print() if line.lstrip().startswith("!")],
            ["PAY"],
        )

    def _print(self):
        """The CONTROL TOWER block's Project lines, as the real block prints
        them — not a re-implementation of the renderer."""
        import ops_status

        buffer = io.StringIO()
        with mock.patch.object(ops_status, "RUNTIME_DIR", self.runtime):
            with redirect_stdout(buffer):
                ops_status._print_control_tower(NOW)
        lines = buffer.getvalue().splitlines()
        try:
            start = lines.index("  Project") + 1
        except ValueError:
            return []
        out = []
        for line in lines[start:]:
            if not line.startswith("    "):
                break
            out.append(line)
        return out


class AuthoredValuesAreRedactedOnTheWayOutTests(DashboardTestCase):
    """The model keeps them verbatim; `to_payload()` is the boundary.

    `blocker`, `project_id`, `milestone` and `event_id` are strings a person
    typed on another Desktop and `validate_event()` only type-checks them.
    Rewriting them inside the model would make `EvidenceRef` unusable for
    finding the file the number came from, so the redaction happens exactly
    where the value leaves the machine.
    """

    def test_the_model_keeps_a_secret_shaped_blocker_verbatim(self):
        self.put(
            "E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6,
            blocker=f"waiting for {SECRET} rotation",
        )

        row = self.model().panel("RISKS").rows[0]

        self.assertIn(SECRET, row.values["blocker"])

    def test_the_payload_does_not_carry_it(self):
        self.put(
            "E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6,
            blocker=f"waiting for {SECRET} rotation",
        )

        payload = json.dumps(self.model().to_payload(), ensure_ascii=False)

        self.assertNotIn(SECRET, payload)
        self.assertIn("[REDACTED]", payload)

    def test_a_secret_shaped_project_id_does_not_reach_the_payload(self):
        self.put("E1", f"PROJ-{SECRET}", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 6)

        payload = json.dumps(self.model().to_payload(), ensure_ascii=False)

        self.assertNotIn(SECRET, payload)

    def test_a_secret_shaped_event_id_does_not_reach_the_payload(self):
        """`event_id` reaches the payload twice — as a RISKS value and inside
        every `evidence` entry — and C47 measured that ids are content."""
        self.put(
            f"EVT-{SECRET}", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6,
            blocker="vendor key",
        )

        payload = json.dumps(self.model().to_payload(), ensure_ascii=False)

        self.assertNotIn(SECRET, payload)

    def test_a_secret_shaped_milestone_does_not_reach_the_payload(self):
        self.put(
            "E1", "BRAND", "CMO", "MILESTONE_COMPLETED", "IN_PROGRESS", 8,
            milestone=f"ship {SECRET}",
        )

        payload = json.dumps(self.model().to_payload(), ensure_ascii=False)

        self.assertNotIn(SECRET, payload)

    def test_a_newline_in_an_id_cannot_forge_a_line_in_the_payload(self):
        """docs/02 constrains `project_id` only to "present and non-null"
        (BACKLOG A-15), so a newline inside one is accepted and stored."""
        forged = "PAY\n  ! everything is fine"
        self.put("E1", forged, "CTO_BACKEND", "STARTED", "IN_PROGRESS", 6)

        payload = self.model().to_payload()
        rendered = json.dumps(payload, ensure_ascii=False)

        self.assertNotIn("\n", [p for p in payload["panels"] if p["key"] == "PROJECTS"][0]["rows"][0]["key"])
        self.assertIn("\\n", rendered)

    def _unreadable_file(self, name, **overrides):
        """A file in `processed/` that is JSON but not an Event.

        Reachable without corruption: docs/11 permits writing into
        `incoming/` by hand, a partial restore leaves whatever it left, and
        `read_events()`'s own docstring notes staging residue the Collector
        accepted. The Collector validates, so this is not the *ordinary*
        path — but "not ordinary" is exactly the state an operator reads a
        status view in.
        """
        data = {
            "schema_version": "1.0",
            "event_id": "E1",
            "source": "DESKTOP_1",
            "role": "CTO_BACKEND",
            "project_id": "P",
            "event_type": "STARTED",
            "status": "IN_PROGRESS",
            "summary": "s",
            "timestamp": "2026-08-05T09:00:00+09:00",
            "history_candidate": True,
            "evidence": [],
            "milestone": None,
            "blocker": None,
        }
        data.update(overrides)
        (self.processed / name).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def test_a_rejected_value_echoed_by_the_validator_is_redacted(self):
        """C48: `validate_event()` puts the value it rejected **into the
        error message** — `invalid source: '…'`. The first draft of
        `to_payload()` reasoned that "a filename and an exception message
        are not authored Event text" and applied `one_line()` alone.
        Measured: one such file put the credential into the payload twice.
        """
        self._unreadable_file("bad.json", source=SECRET)

        payload = self.model().to_payload()

        self.assertEqual(len(payload["unreadable"]), 1)
        self.assertNotIn(SECRET, json.dumps(payload, ensure_ascii=False))
        self.assertIn("[REDACTED]", payload["unreadable"][0]["reason"])

    def test_a_secret_shaped_filename_is_redacted_too(self):
        """The Event file is named after the Event, so a secret-shaped
        `event_id` is a secret-shaped filename."""
        self._unreadable_file(f"{SECRET}.json", event_type="NOT_A_TYPE")

        payload = self.model().to_payload()

        self.assertNotIn(SECRET, json.dumps(payload, ensure_ascii=False))
        self.assertIn("[REDACTED]", payload["unreadable"][0]["file"])

    def test_the_reason_is_bounded(self):
        """`read_events()` catches `Exception`, so the text is whatever the
        failure produced. A report that can grow without limit is one that
        fills a disk — the argument `oplog.bounded()` already makes."""
        from oplog import MAX_LOG_ERROR

        self._unreadable_file("long.json", summary="x" * 5000, event_type="NOPE" * 400)

        payload = self.model().to_payload()

        self.assertLessEqual(
            len(payload["unreadable"][0]["reason"]), MAX_LOG_ERROR + 3
        )

    def test_the_model_still_carries_the_reason_verbatim(self):
        """Same split as everywhere else: the machine-local model keeps what
        it read so a person can act on it; the boundary redacts."""
        self._unreadable_file("bad.json", source=SECRET)

        self.assertIn(SECRET, self.model().unreadable[0][1])

    def test_no_event_text_can_reach_a_panels_own_strings(self):
        """The other half of the sweep.

        `title` / `source` / `note` / `columns` / `unsourced_layers` are the
        only payload strings `_out()` never touches, on the grounds that this
        module wrote them. That is a claim about the code, and this drives it:
        a model built over poisoned Events must produce **byte-identical**
        panel metadata to one built over none.
        """
        clean = self.model().to_payload()
        self.put(
            f"EVT-{SECRET}", f"PROJ-{SECRET}", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6,
            blocker=f"waiting on {SECRET}",
        )
        self._unreadable_file(f"{SECRET}.json", source=SECRET)
        poisoned = self.model().to_payload()

        def metadata(payload):
            return [
                {
                    key: panel[key]
                    for key in ("key", "title", "status", "source", "note",
                                "columns", "unsourced_layers")
                }
                for panel in payload["panels"]
            ]

        self.assertEqual(metadata(clean), metadata(poisoned))
        self.assertEqual(clean["schema_version"], poisoned["schema_version"])

    def test_the_exemption_list_names_only_columns_that_exist(self):
        """`_UNAUTHORED_KEYS` is the list `_out()` does NOT redact. A stale
        entry protects nothing and hides that it protects nothing."""
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="x")

        emitted = {name for panel in self.model().panels for name in panel.columns}

        self.assertEqual(_UNAUTHORED_KEYS - emitted, set())

    def test_every_exempted_column_only_ever_holds_a_schema_value(self):
        """The exemption is safe only because `validate_event()` constrains
        these fields to fixed sets — or this module picked the word itself.
        A field that stopped being constrained would have to leave the list,
        and this is the assertion that would notice."""
        from controltower.dashboard import PROJECT_STATES
        from events import ROLES, SOURCES, STATUSES
        from notion.properties import ROLE_DISPLAY_NAMES

        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="x")
        self.put("E2", "PAY", "CMO", "STARTED", "IN_PROGRESS", 7, source="DESKTOP_1")
        allowed = (
            set(ROLES)
            | set(SOURCES)
            | set(STATUSES)
            | set(ROLE_DISPLAY_NAMES.values())
            | set(PROJECT_STATES)
            | {"OPEN_BLOCKER", "ROLE_MISMATCH"}
        )

        for panel in self.model().panels:
            for row in panel.rows:
                for name in _UNAUTHORED_KEYS & set(panel.columns):
                    value = row.values[name]
                    for item in value if isinstance(value, list) else [value]:
                        if item is None:
                            continue
                        with self.subTest(panel=panel.key, row=row.key, column=name):
                            self.assertIn(item, allowed)

    def test_a_secret_shaped_value_in_an_exempted_column_is_impossible(self):
        """The other direction, driven rather than reasoned: an Event cannot
        even be built with one, so no exempted column can carry it."""
        from events.schema import EventValidationError

        with self.assertRaises(EventValidationError):
            self.put("E1", "PAY", SECRET, "STARTED", "IN_PROGRESS", 6, source="DESKTOP_1")

    def test_the_risk_row_key_is_redacted_too(self):
        """`RISKS` keys are built out of `project_id` and `event_id`."""
        self.put(f"EVT-{SECRET}", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="x")

        keys = [
            row["key"]
            for panel in self.model().to_payload()["panels"]
            if panel["key"] == "RISKS"
            for row in panel["rows"]
        ]

        self.assertTrue(keys)
        for key in keys:
            self.assertNotIn(SECRET, key)


class ThePayloadIsJsonAndDeterministicTests(DashboardTestCase):
    """A diff between two payloads has to mean a difference in the work."""

    def _populate(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "BRAND", "CMO", "MILESTONE_COMPLETED", "IN_PROGRESS", 8, milestone="M1")
        self.put("E3", "SEARCH", "COO", "COMPLETED", "COMPLETED", 9)

    def test_the_payload_serialises(self):
        self._populate()

        text = json.dumps(self.model().to_payload(), ensure_ascii=False)

        self.assertEqual(json.loads(text)["schema_version"], "1.0")

    def test_two_builds_over_the_same_evidence_are_byte_identical(self):
        self._populate()

        first = json.dumps(self.model().to_payload(), ensure_ascii=False, sort_keys=False)
        second = json.dumps(self.model().to_payload(), ensure_ascii=False, sort_keys=False)

        self.assertEqual(first, second)

    def test_the_payload_carries_the_period_the_rollup_was_bounded_by(self):
        self._populate()
        model = build_dashboard(
            self.rollup(since=datetime(2026, 8, 7, tzinfo=KST).date()), now=NOW
        )

        payload = model.to_payload()

        self.assertEqual(payload["since"], "2026-08-07")
        self.assertIsNone(payload["until"])
        self.assertEqual(payload["events_read"], 2)

    def test_the_payload_names_the_files_it_could_not_read(self):
        self._populate()
        (self.processed / "broken.json").write_text("{not json", encoding="utf-8")

        payload = self.model().to_payload()

        self.assertEqual([item["file"] for item in payload["unreadable"]], ["broken.json"])
        self.assertTrue(payload["unreadable"][0]["reason"])

    def test_generated_at_is_the_callers_instant_not_a_clock_read(self):
        self._populate()

        self.assertEqual(self.model().to_payload()["generated_at"], NOW.isoformat())

    def test_the_panels_come_out_in_a_fixed_order(self):
        self._populate()

        self.assertEqual(
            [panel["key"] for panel in self.model().to_payload()["panels"]],
            ["COMPANY_GOALS", "METRICS", "TEAMS", "PROJECTS", "SPRINTS", "DESKTOPS", "RISKS"],
        )

    def test_numbers_stay_numbers(self):
        """A projection writes these into Notion `number` properties; a
        stringified count would fail at the API rather than here."""
        self._populate()

        for panel in self.model().to_payload()["panels"]:
            for row in panel["rows"]:
                for name in ("events", "value", "days_open", "days_silent"):
                    if name in row["values"] and row["values"][name] is not None:
                        with self.subTest(panel=panel["key"], row=row["key"], value=name):
                            self.assertIsInstance(row["values"][name], int)


class ThePayloadDoesNotGrowWithTheWorkTests(DashboardTestCase):
    """docs/14 §3's rule, applied to the payload.

    "Manifest는 Event 1건당 줄을 쓰지 않는다 … 작업량에 비례해 커지는 것은
    로그이며, 그러면 Manifest일 수 없다." The first version of `to_payload()`
    broke exactly that: every Event appears in four rows (its metric, its
    project, its team, its Desktop), so the payload carried four refs per
    Event and grew without bound — measured at 6,000 Events, 2.0 MB and
    382 ms.

    The cap is only honest if the count is not capped with it, which is what
    these assert.
    """

    def _many(self, count):
        for index in range(count):
            self.put(
                f"E{index:04d}", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS",
                (index % 28) + 1,
            )

    def test_no_row_carries_more_than_the_cap(self):
        self._many(EVIDENCE_IN_PAYLOAD * 3)

        for panel in self.model().to_payload()["panels"]:
            for row in panel["rows"]:
                with self.subTest(panel=panel["key"], row=row["key"]):
                    self.assertLessEqual(len(row["evidence"]), EVIDENCE_IN_PAYLOAD)

    def test_the_count_is_the_true_total_not_the_capped_one(self):
        total = EVIDENCE_IN_PAYLOAD * 3
        self._many(total)

        panels = {panel["key"]: panel for panel in self.model().to_payload()["panels"]}
        events_row = next(
            row for row in panels["METRICS"]["rows"] if row["key"] == "events"
        )

        self.assertEqual(events_row["evidence_count"], total)
        self.assertEqual(len(events_row["evidence"]), EVIDENCE_IN_PAYLOAD)
        self.assertTrue(events_row["evidence_truncated"])

    def test_a_short_list_is_not_marked_truncated(self):
        """The flag has to be false sometimes or it says nothing."""
        self._many(2)

        panels = {panel["key"]: panel for panel in self.model().to_payload()["panels"]}
        events_row = next(
            row for row in panels["METRICS"]["rows"] if row["key"] == "events"
        )

        self.assertEqual(events_row["evidence_count"], 2)
        self.assertFalse(events_row["evidence_truncated"])

    def test_the_model_itself_keeps_every_reference(self):
        """The cap is a property of the payload, not of the derivation — a
        reader on this machine still gets all of them."""
        total = EVIDENCE_IN_PAYLOAD * 3
        self._many(total)

        model = self.model()
        events_metric = next(
            row for row in model.panel("METRICS").rows if row.key == "events"
        )

        self.assertEqual(len(events_metric.evidence), total)

    def test_a_row_that_names_one_cause_still_names_it(self):
        """The rows whose evidence *is* the reason — a blocker, a mismatch —
        carry one ref each and are untouched by the cap."""
        self._many(EVIDENCE_IN_PAYLOAD * 3)
        self.put("B1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 28, blocker="vendor")

        risks = [
            row
            for panel in self.model().to_payload()["panels"]
            if panel["key"] == "RISKS"
            for row in panel["rows"]
        ]

        self.assertEqual(len(risks), 1)
        self.assertEqual(risks[0]["evidence_count"], 1)
        self.assertFalse(risks[0]["evidence_truncated"])
        self.assertEqual(risks[0]["evidence"][0]["event_id"], "B1")

    def test_the_payload_stays_small_as_the_evidence_grows(self):
        """A property rather than a benchmark: doubling the Events must not
        double the payload."""
        import json

        self._many(20)
        small = len(json.dumps(self.model().to_payload(), ensure_ascii=False))
        for index in range(20, 400):
            self.put(
                f"E{index:04d}", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS",
                (index % 28) + 1,
            )
        large = len(json.dumps(self.model().to_payload(), ensure_ascii=False))

        # Twenty times the Events. What may differ is the *counts* — a few
        # digits per row — and nothing else, so the bound is bytes rather
        # than a ratio: a ratio would still pass if the payload had grown by
        # one ref per Event on a small fixture.
        self.assertLess(abs(large - small), 200, (small, large))


class NothingIsInventedTests(DashboardTestCase):
    """Every number in the model is a number the rollup already made."""

    def _populate(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "BRAND", "CMO", "MILESTONE_COMPLETED", "IN_PROGRESS", 8, milestone="M1")
        self.put("E3", "SEARCH", "COO", "COMPLETED", "COMPLETED", 9)
        self.put("E4", "PAY", "CTO_FRONTEND", "STARTED", "IN_PROGRESS", 10)

    def test_the_desktop_rows_sum_to_the_events_read(self):
        """No Event counted twice, and none lost."""
        self._populate()
        model = self.model()

        total = sum(row.values["events"] for row in model.panel("DESKTOPS").rows)

        self.assertEqual(total, model.events_read)

    def test_the_team_rows_sum_to_the_events_read(self):
        self._populate()
        model = self.model()

        total = sum(row.values["events"] for row in model.panel("TEAMS").rows)

        self.assertEqual(total, model.events_read)

    def test_the_project_rows_sum_to_the_events_read(self):
        self._populate()
        model = self.model()

        total = sum(row.values["events"] for row in model.panel("PROJECTS").rows)

        self.assertEqual(total, model.events_read)

    def test_the_panels_are_exactly_the_rollups_own_collections(self):
        self._populate()
        rollup = self.rollup()
        model = build_dashboard(rollup, now=NOW)

        self.assertEqual(
            sorted(row.key for row in model.panel("PROJECTS").rows),
            sorted(project.project_id for project in rollup.projects),
        )
        self.assertEqual(
            [row.key for row in model.panel("TEAMS").rows],
            [team.team for team in rollup.teams],
        )
        self.assertEqual(
            [row.key for row in model.panel("DESKTOPS").rows],
            [desktop.source for desktop in rollup.desktops],
        )
        self.assertEqual(
            len(model.panel("RISKS").rows),
            len(rollup.risks) + len(rollup.mismatches),
        )

    def test_every_role_gets_a_row_even_with_no_events_at_all(self):
        """Present-and-empty, the same argument `daily/role_summary.py`
        makes: an omitted row and a silent team look identical, and only one
        of them is fine."""
        model = self.model()

        self.assertEqual(
            {row.key for row in model.panel("TEAMS").rows}, set(ROLES)
        )
        for row in model.panel("TEAMS").rows:
            with self.subTest(team=row.key):
                self.assertFalse(row.values["has_activity"])
                self.assertEqual(row.values["events"], 0)
                self.assertIsNone(row.values["last_seen"])

    def test_every_desktop_gets_a_row_even_with_no_events_at_all(self):
        model = self.model()

        self.assertEqual(
            {row.key for row in model.panel("DESKTOPS").rows},
            set(SOURCE_FOR_ROLE.values()),
        )
        for row in model.panel("DESKTOPS").rows:
            with self.subTest(desktop=row.key):
                self.assertIsNone(row.values["days_silent"])
                self.assertEqual(row.values["role_mismatches"], 0)


class DesktopsDoNotMixTests(DashboardTestCase):
    """Desktop 1 / 2 / 4 are independent reporters and the panel keeps them so."""

    def test_each_desktop_row_carries_only_its_own_events(self):
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put("A2", "PAY", "CTO_BACKEND", "DECISION_APPROVED", "IN_PROGRESS", 6)
        self.put("B1", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 5)
        self.put("D1", "OPSX", "COO", "STARTED", "IN_PROGRESS", 5)

        rows = {row.key: row for row in self.model().panel("DESKTOPS").rows}

        self.assertEqual(rows["DESKTOP_1"].values["events"], 2)
        self.assertEqual(rows["DESKTOP_2"].values["events"], 1)
        self.assertEqual(rows["DESKTOP_3"].values["events"], 0)
        self.assertEqual(rows["DESKTOP_4"].values["events"], 1)
        self.assertEqual(rows["DESKTOP_1"].values["projects"], ["PAY"])
        self.assertEqual(rows["DESKTOP_2"].values["projects"], ["BRAND"])
        self.assertEqual(rows["DESKTOP_4"].values["projects"], ["OPSX"])

    def test_an_event_claiming_another_desktops_role_is_a_risk_row(self):
        """docs/02 §8 fixes the Desktop->role table; `validate_event()`
        checks the two fields independently and never the pair."""
        self.put("X1", "PAY", "CMO", "STARTED", "IN_PROGRESS", 5, source="DESKTOP_1")

        model = self.model()
        rows = {row.key: row for row in model.panel("DESKTOPS").rows}
        risk = [
            row for row in model.panel("RISKS").rows
            if row.values["kind"] == "ROLE_MISMATCH"
        ]

        # Counted under the Desktop that sent it, never under the role it claims.
        self.assertEqual(rows["DESKTOP_1"].values["events"], 1)
        self.assertEqual(rows["DESKTOP_2"].values["events"], 0)
        self.assertEqual(rows["DESKTOP_1"].values["role_mismatches"], 1)
        self.assertEqual(rows["DESKTOP_1"].values["mismatched_event_ids"], ["X1"])
        self.assertEqual(len(risk), 1)
        self.assertEqual(risk[0].values["claimed_role"], "CMO")
        self.assertEqual(risk[0].values["expected_role"], "CTO_BACKEND")
        self.assertEqual(risk[0].values["source"], "DESKTOP_1")

    def test_the_mismatch_reaches_the_payload_with_both_roles(self):
        self.put("X1", "PAY", "CMO", "STARTED", "IN_PROGRESS", 5, source="DESKTOP_1")

        rows = [
            row
            for panel in self.model().to_payload()["panels"]
            if panel["key"] == "RISKS"
            for row in panel["rows"]
        ]

        self.assertEqual(rows[0]["values"]["claimed_role"], "CMO")
        self.assertEqual(rows[0]["values"]["expected_role"], "CTO_BACKEND")

    def test_a_desktop_silent_for_days_says_how_many(self):
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        rows = {row.key: row for row in self.model().panel("DESKTOPS").rows}

        self.assertEqual(rows["DESKTOP_1"].values["days_silent"], 14)
        self.assertIsNone(rows["DESKTOP_2"].values["days_silent"])


class SilenceIsTwoDifferentFactsTests(DashboardTestCase):
    """C49: `days_silent` alone cannot express "has never reported".

    The DESKTOPS panel carries the number of whole days since a Desktop's most
    recent Event — and `None` for one that has sent nothing at all. That is
    deliberate (`_whole_days_between()` answers `None` rather than guessing),
    but it makes the obvious Notion filter wrong in the worst direction:

        days_silent >= 3        misses the Desktop that never reported

    `ops_status.py`'s COMPANY block gets this right — `silent_for()` includes
    an activity whose `days_silent` is `None` — and a view built on the panel
    has to do the same. `docs/13` §3-⑨-1 now says so; this pins the shape the
    guidance depends on.
    """

    def test_a_desktop_that_never_reported_has_no_number(self):
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        rows = {row.key: row for row in self.model().panel("DESKTOPS").rows}

        self.assertEqual(rows["DESKTOP_1"].values["days_silent"], 14)
        self.assertIsNone(rows["DESKTOP_2"].values["days_silent"])
        self.assertFalse(rows["DESKTOP_2"].values["has_activity"])
        self.assertTrue(rows["DESKTOP_1"].values["has_activity"])

    def test_has_activity_is_what_separates_the_two(self):
        """Present-and-empty, the same argument the panel makes everywhere:
        a Desktop that sent nothing must not be absent from the table."""
        rows = self.model().panel("DESKTOPS").rows

        self.assertEqual(len(rows), 4)
        for row in rows:
            with self.subTest(desktop=row.key):
                self.assertFalse(row.values["has_activity"])
                self.assertIsNone(row.values["days_silent"])

    def test_the_numeric_filter_alone_is_incomplete(self):
        """Stated as the failure it prevents rather than as a rule."""
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        rows = self.model().panel("DESKTOPS").rows

        numeric_only = {
            row.key for row in rows
            if (row.values["days_silent"] or 0) >= 3
        }
        correct = {
            row.key for row in rows
            if not row.values["has_activity"] or row.values["days_silent"] >= 3
        }

        self.assertEqual(numeric_only, {"DESKTOP_1"})
        self.assertEqual(
            correct, {"DESKTOP_1", "DESKTOP_2", "DESKTOP_3", "DESKTOP_4"}
        )

    def test_the_company_block_uses_the_wider_rule(self):
        """The panel's guidance is not invented here — it is the rule
        `app/desktop_activity.silent_for()` already applies, read back."""
        import inspect

        from app.desktop_activity import CompanyActivitySnapshot

        source = inspect.getsource(CompanyActivitySnapshot.silent_for)

        self.assertIn("silent is None or silent >= days", source)

    def test_the_payload_keeps_the_null_rather_than_zeroing_it(self):
        """A `0` would read as "reported today", which is the opposite of the
        truth. `_out()` passes `None` through untouched for exactly this."""
        payload = self.model().to_payload()
        rows = [
            row for panel in payload["panels"]
            if panel["key"] == "DESKTOPS" for row in panel["rows"]
        ]

        for row in rows:
            with self.subTest(desktop=row["key"]):
                self.assertIsNone(row["values"]["days_silent"])
                self.assertFalse(row["values"]["has_activity"])


class ProjectRowsAreOrderedForTheQuestionsAskedTests(DashboardTestCase):
    """Blocked first, then longest-quiet — never filename order."""

    def test_blocked_projects_come_first(self):
        self.put("A1", "AAA", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 18)
        self.put("Z1", "ZZZ", "CMO", "BLOCKED", "BLOCKED", 17, blocker="vendor")

        self.assertEqual(
            [row.key for row in self.model().panel("PROJECTS").rows], ["ZZZ", "AAA"]
        )

    def test_the_quietest_unblocked_project_comes_next(self):
        self.put("A1", "RECENT", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 18)
        self.put("B1", "OLD", "CMO", "STARTED", "IN_PROGRESS", 2)

        self.assertEqual(
            [row.key for row in self.model().panel("PROJECTS").rows], ["OLD", "RECENT"]
        )

    def test_the_order_does_not_follow_the_filename(self):
        """The fold is by Event instant; so is the order here."""
        self.put("ZZZ_FIRST", "OLD", "CMO", "STARTED", "IN_PROGRESS", 2)
        self.put("AAA_LATER", "RECENT", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 18)

        self.assertEqual(
            [row.key for row in self.model().panel("PROJECTS").rows], ["OLD", "RECENT"]
        )

    def test_a_project_blocked_after_completing_reads_as_blocked(self):
        """`is_complete` never goes back to False once §25 writes a
        Completed Date, so state has to decide between the two — and the one
        a person has to look at wins."""
        self.put("C1", "OPSX", "COO", "COMPLETED", "COMPLETED", 5)
        self.put("C2", "OPSX", "COO", "BLOCKED", "BLOCKED", 9, blocker="reopened")

        row = self.model().panel("PROJECTS").rows[0]

        self.assertEqual(row.values["state"], "BLOCKED")
        self.assertIsNotNone(row.values["completed_at"])

    def test_a_completed_project_reads_as_complete(self):
        self.put("C1", "OPSX", "COO", "COMPLETED", "COMPLETED", 5)

        self.assertEqual(self.model().panel("PROJECTS").rows[0].values["state"], "COMPLETE")

    def test_an_ordinary_project_reads_as_active(self):
        self.put("C1", "OPSX", "COO", "STARTED", "IN_PROGRESS", 5)

        self.assertEqual(self.model().panel("PROJECTS").rows[0].values["state"], "ACTIVE")

    def test_a_cancelled_project_does_not_read_as_active(self):
        """C48: docs/04 §26 gives `CANCELLED` no property of its own, so the
        fold has nothing to read and the only record of the cancellation is
        the reported `status`. Deriving `state` from the folded facts alone
        called a cancelled project ACTIVE — invisible on the screen, which
        prints `status` directly, and wrong in any projection of `state`."""
        self.put("C1", "OPSX", "COO", "STARTED", "IN_PROGRESS", 5)
        self.put("C2", "OPSX", "COO", "CANCELLED", "CANCELLED", 7)

        row = self.model().panel("PROJECTS").rows[0]

        self.assertEqual(row.values["state"], "CANCELLED")
        self.assertEqual(row.values["status"], "CANCELLED")

    def test_a_cancelled_project_that_is_blocked_still_reads_as_blocked(self):
        """A person has to look at it either way, and only one of the two
        words says so."""
        self.put("C1", "OPSX", "COO", "BLOCKED", "BLOCKED", 5, blocker="legal")
        self.put("C2", "OPSX", "COO", "CANCELLED", "CANCELLED", 7)

        self.assertEqual(
            self.model().panel("PROJECTS").rows[0].values["state"], "BLOCKED"
        )

    def test_the_state_is_always_one_of_the_declared_words(self):
        from controltower.dashboard import PROJECT_STATES

        self.put("A", "P1", "COO", "STARTED", "NOT_STARTED", 3)
        self.put("B", "P2", "CMO", "BLOCKED", "BLOCKED", 4, blocker="x")
        self.put("C", "P3", "CTO_BACKEND", "COMPLETED", "COMPLETED", 5)
        self.put("D", "P4", "CTO_FRONTEND", "CANCELLED", "CANCELLED", 6)

        for row in self.model().panel("PROJECTS").rows:
            with self.subTest(project=row.key):
                self.assertIn(row.values["state"], PROJECT_STATES)


class TheBlockerOwnerReachesThePanelTests(DashboardTestCase):
    """C48 fixed `Risk.team`; this is the dashboard-level statement of it."""

    def test_the_risk_row_names_the_team_that_declared_the_blocker(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "PAY", "CMO", "DECISION_APPROVED", "IN_PROGRESS", 8)

        model = self.model()
        risk = model.panel("RISKS").rows[0]

        self.assertEqual(risk.values["team"], "CTO_BACKEND")
        self.assertEqual(
            model.panel("PROJECTS").rows[0].values["blocker_team"], "CTO_BACKEND"
        )

    def test_both_teams_still_see_the_blocked_project_in_their_row(self):
        """A team working on a blocked project has a blocked project, even
        when another team owns the blocker. The two facts are different and
        both are true."""
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor key")
        self.put("E2", "PAY", "CMO", "DECISION_APPROVED", "IN_PROGRESS", 8)

        rows = {row.key: row for row in self.model().panel("TEAMS").rows}

        self.assertEqual(rows["CTO_BACKEND"].values["blocked_projects"], ["PAY"])
        self.assertEqual(rows["CMO"].values["blocked_projects"], ["PAY"])


class TheModelSurvivesTheStatesAnOperatorMeetsTests(DashboardTestCase):
    """Empty, failed, stale, restored. None of them may raise."""

    def test_an_empty_processed_directory_builds_a_full_model(self):
        model = self.model()

        self.assertEqual(len(model.panels), 7)
        self.assertEqual(model.events_read, 0)
        self.assertEqual(model.unreadable, ())
        json.dumps(model.to_payload(), ensure_ascii=False)

    def test_a_missing_processed_directory_builds_a_full_model(self):
        rollup = build_company_rollup(
            processed_dir=self.processed / "gone", now=NOW
        )

        model = build_dashboard(rollup, now=NOW)

        self.assertEqual(len(model.panels), 7)
        json.dumps(model.to_payload(), ensure_ascii=False)

    def test_an_unreadable_file_shortens_the_numbers_and_says_so(self):
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        (self.processed / "torn.json").write_text('{"schema', encoding="utf-8")

        model = self.model()

        self.assertEqual(model.events_read, 1)
        self.assertEqual([name for name, _ in model.unreadable], ["torn.json"])

    def test_a_restored_machine_with_history_but_no_evidence_says_nothing_moved(self):
        """After a disaster restore `processed/` is empty — it is not in the
        Backup scope (docs/08 §26) — and every panel has to say that rather
        than raise or omit itself."""
        model = self.model()

        self.assertEqual(model.panel("PROJECTS").rows, ())
        self.assertIs(model.panel("PROJECTS").status, PanelStatus.SOURCED)
        self.assertEqual(model.panel("RISKS").rows, ())
        self.assertEqual(
            [row.values["value"] for row in model.panel("METRICS").rows if row.key == "events"],
            [0],
        )

    def test_a_naive_now_does_not_produce_a_wrong_age(self):
        """Mixing naive and aware is answered `None` rather than guessed at,
        and the model must carry the None instead of a zero."""
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="vendor")
        naive = datetime(2026, 8, 19, 9, 0)

        model = build_dashboard(
            build_company_rollup(processed_dir=self.processed, now=naive), now=naive
        )

        self.assertIsNone(model.panel("PROJECTS").rows[0].values["days_blocked"])
        self.assertIsNone(model.panel("RISKS").rows[0].values["days_open"])
        self.assertIsNone(model.panel("DESKTOPS").rows[0].values["days_silent"])

    def test_an_unknown_panel_is_none_rather_than_an_error(self):
        self.assertIsNone(self.model().panel("NO_SUCH_PANEL"))


class CoverageSaysWhatTheNumbersDoNotCoverTests(DashboardTestCase):
    """C49: the panels answer "what happened"; `coverage` answers "over what".

    They are different questions and only one of them survives a restore.
    `processed/` is Execution Evidence and Backup scope is `daily/` +
    `monthly/` only (docs/08 §26), so a restored machine has its whole
    Company History and none of its Events — every panel then says zero,
    truthfully, about a company that did a great deal.

    Before this, the qualifier existed only as a line `ops_status.py`
    computed and printed. A projection of the same Control Tower would have
    had to derive it again, which is the fork C48 and C49 keep closing.
    """

    def test_the_evidence_range_is_the_events_own_dates(self):
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put("E2", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 11)
        self.put("E3", "PAY", "CTO_BACKEND", "DECISION_APPROVED", "IN_PROGRESS", 8)

        coverage = self.model().coverage

        self.assertEqual(coverage.evidence_from, "2026-08-05")
        self.assertEqual(coverage.evidence_to, "2026-08-11")

    def test_no_evidence_is_a_range_of_none_rather_than_a_guess(self):
        coverage = self.model().coverage

        self.assertIsNone(coverage.evidence_from)
        self.assertIsNone(coverage.evidence_to)

    def test_an_unreadable_file_makes_the_model_incomplete(self):
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        (self.processed / "torn.json").write_text('{"schema', encoding="utf-8")

        coverage = self.model().coverage

        self.assertEqual(coverage.unreadable, 1)
        self.assertFalse(coverage.complete)

    def test_a_clean_tree_is_complete(self):
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        self.assertTrue(self.model().coverage.complete)

    def test_history_older_than_the_evidence_makes_it_incomplete(self):
        from datetime import date

        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        model = self.model().with_history_coverage(date(2026, 8, 1))

        self.assertEqual(model.coverage.history_uncovered_from, "2026-08-01")
        self.assertFalse(model.coverage.complete)
        self.assertEqual(model.coverage.evidence_from, "2026-08-12")

    def test_asking_and_finding_no_gap_is_not_the_same_as_never_asking(self):
        """Both leave `history_uncovered_from` None — and that is fine,
        because the value's meaning is "there is a gap, from here". What must
        not happen is the call changing anything else."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        before = self.model()

        after = before.with_history_coverage(None)

        self.assertEqual(before.to_payload(), after.to_payload())

    def test_attaching_coverage_changes_nothing_else(self):
        from datetime import date

        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 5, blocker="x")
        before = self.model()

        after = before.with_history_coverage(date(2026, 8, 1))

        self.assertEqual(before.panels, after.panels)
        self.assertEqual(before.events_read, after.events_read)
        self.assertEqual(before.coverage.evidence_from, after.coverage.evidence_from)

    def test_the_model_is_not_mutated_by_attaching_coverage(self):
        """Frozen for the reason every other view here is: a model two
        readers can edit is a model they can disagree about."""
        from datetime import date

        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        model = self.model()

        model.with_history_coverage(date(2026, 8, 1))

        self.assertIsNone(model.coverage.history_uncovered_from)

    def test_the_payload_carries_the_coverage(self):
        from datetime import date

        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        (self.processed / "torn.json").write_text("{", encoding="utf-8")

        payload = self.model().with_history_coverage(date(2026, 8, 1)).to_payload()

        self.assertEqual(
            payload["coverage"],
            {
                "evidence_from": "2026-08-12",
                "evidence_to": "2026-08-12",
                "unreadable": 1,
                "history_uncovered_from": "2026-08-01",
                "complete": False,
            },
        )

    def test_the_restored_machine_shape(self):
        """The case this field exists for: History present, evidence gone.
        Every panel is empty and truthful, and only `coverage` says why."""
        from datetime import date

        model = self.model().with_history_coverage(date(2026, 8, 1))

        self.assertEqual(model.events_read, 0)
        self.assertEqual(model.panel("PROJECTS").rows, ())
        self.assertIs(model.panel("PROJECTS").status, PanelStatus.SOURCED)
        self.assertIsNone(model.coverage.evidence_from)
        self.assertEqual(model.coverage.history_uncovered_from, "2026-08-01")
        self.assertFalse(model.coverage.complete)

    def test_the_screen_and_the_model_agree_about_the_gap(self):
        """`ops_status.py` prints the qualifier out of the model it hands the
        answer back to, so there is one derivation rather than two."""
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        import ops_status

        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)
        daily = self.runtime / "local_master" / "daily"
        daily.mkdir(parents=True)
        # The shape `_rendered_event_ids()` reads — a real `- Event ID:`
        # label line, not a summary that merely looks like one. Built from
        # `chr(10).join` the way the sibling fixtures in
        # `test_controltower.py` are, so the two agree about what a Daily
        # that carries work looks like.
        (daily / "2026-08-01.md").write_text(
            chr(10).join([
                "# H", "", "## Milestones", "", "### OPS", "",
                "- work", "- Owner: COO", "- Event ID: OLD-1", "",
                "## Metadata", "", "- Event Count: 1", "",
            ]),
            encoding="utf-8",
        )

        buffer = io.StringIO()
        with mock.patch.object(ops_status, "RUNTIME_DIR", self.runtime):
            with redirect_stdout(buffer):
                ops_status._print_control_tower(NOW)
        printed = buffer.getvalue()

        self.assertIn("증거 범위 밖", printed)
        self.assertIn("2026-08-01", printed)
        self.assertIn("2026-08-12", printed)


class TheDefaultEvidenceDirectoryIsTheOneTheViewReadsTests(unittest.TestCase):
    """`build_company_rollup()` has a default `processed_dir`, and nothing in
    production uses it — `ops_status.py` passes its own, the Runner passes
    `events=`. That makes the default a trap rather than a convenience: the
    place it is actually reached from is an operator's one-liner, including
    the one `docs/13` §3-⑨ prints for listing the Control Tower's panels.

    If the two ever point at different trees, that command would describe a
    directory nobody's pipeline writes to and nothing would say so.
    """

    def test_the_rollup_default_is_the_directory_ops_status_reads(self):
        import ops_status
        from controltower.rollup import DEFAULT_PROCESSED_DIR

        self.assertEqual(
            DEFAULT_PROCESSED_DIR,
            ops_status.RUNTIME_DIR / "events" / "processed",
        )

    def test_the_documented_one_liner_builds_a_model(self):
        """The command in docs/13 §3-⑨, run. It must work on a machine with
        no evidence at all — which is what an operator setting Notion up for
        the first time has."""
        from datetime import datetime

        now = datetime.now().astimezone()
        model = build_dashboard(build_company_rollup(now=now), now=now)

        self.assertTrue(model.panels)
        for panel in model.panels:
            with self.subTest(panel=panel.key):
                self.assertTrue(panel.source or panel.note)


class SeededChainPropertyTests(unittest.TestCase):
    """Random Event sequences through the whole chain, checking the
    invariants the request states rather than any particular output.

    The fixed classes above pin *cases*. These pin *properties*, which is
    where an ordering or fold mistake actually lives — C47 found the
    filename-ordering defect that way, and this extends the same harness one
    layer up, to the Dashboard Model and its payload.

    Seeds are fixed, so a failure is reproducible by number.

        Desktop 간 데이터 혼입 없음         each Desktop's count is its own
        중복 집계 없음                       every partition sums to events_read
        filename order 의존 없음            shuffling the files changes nothing
        event time 기준 정렬                the fold follows the instant
        실패를 성공으로 표시하지 않음        state agrees with the fold
        Evidence 없는 완료 표시 방지         every completion names its Event
        Notion payload와 rollup 결과 일치    the payload is the model
        secret redaction                     no authored field escapes
    """

    SEEDS = 40
    TYPES = (
        "STARTED", "BLOCKED", "RESUMED", "DECISION_APPROVED",
        "MILESTONE_COMPLETED", "ISSUE_RESOLVED", "COMPLETED", "CANCELLED",
    )
    PROJECTS = ("PAY", "BRAND", "SEARCH", "OPSX")

    def _status_for(self, event_type, rnd):
        if event_type in ("COMPLETED", "CANCELLED", "BLOCKED"):
            return event_type
        return rnd.choice(["NOT_STARTED", "IN_PROGRESS", "BLOCKED"])

    def _sequence(self, rnd, count):
        """`[(kwargs, filename)]` — the Events, and the name each is stored
        under. The two are decided separately on purpose: a filename that
        has nothing to do with the Event's instant is exactly the case
        `event_instant_key()` exists for."""
        items = []
        for index in range(count):
            role = rnd.choice(list(SOURCE_FOR_ROLE))
            event_type = rnd.choice(self.TYPES)
            extra = {}
            if event_type == "BLOCKED":
                extra["blocker"] = rnd.choice(
                    ["vendor key", "legal review", "budget", "waiting on data"]
                )
            if event_type == "MILESTONE_COMPLETED":
                extra["milestone"] = rnd.choice(["M1", "M2", "M3"])
            items.append(
                (
                    dict(
                        source=SOURCE_FOR_ROLE[role],
                        role=role,
                        project_id=rnd.choice(self.PROJECTS),
                        event_type=event_type,
                        status=self._status_for(event_type, rnd),
                        summary=f"summary {index}",
                        history_candidate=True,
                        event_id=f"EV-{index:03d}",
                        timestamp=(
                            f"2026-08-{rnd.randint(1, 28):02d}"
                            f"T{rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:00+09:00"
                        ),
                        **extra,
                    ),
                    f"{rnd.randint(0, 999999):06d}-{index}.json",
                )
            )
        return items

    def _write(self, directory, items):
        for kwargs, filename in items:
            (directory / filename).write_text(
                create_event(**kwargs).to_json(), encoding="utf-8"
            )

    def _build(self, directory):
        rollup = build_company_rollup(processed_dir=directory, now=NOW)
        return rollup, build_dashboard(rollup, now=NOW)

    def test_every_partition_sums_to_the_events_read(self):
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed)
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, self._sequence(rnd, rnd.randint(0, 30)))
                    _rollup, model = self._build(directory)

                    for key in ("DESKTOPS", "TEAMS", "PROJECTS"):
                        self.assertEqual(
                            sum(row.values["events"] for row in model.panel(key).rows),
                            model.events_read,
                            f"{key} does not sum to events_read",
                        )

    def test_no_desktop_carries_another_desktops_event(self):
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 1000)
                items = self._sequence(rnd, rnd.randint(1, 30))
                expected: dict[str, int] = {}
                for kwargs, _ in items:
                    expected[kwargs["source"]] = expected.get(kwargs["source"], 0) + 1
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, items)
                    _rollup, model = self._build(directory)

                    for row in model.panel("DESKTOPS").rows:
                        self.assertEqual(
                            row.values["events"], expected.get(row.key, 0), row.key
                        )

    def test_the_model_does_not_depend_on_the_filenames(self):
        """Same Events, different names, and — because the names decide the
        directory listing order — a different read order."""
        import json
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 2000)
                items = self._sequence(rnd, rnd.randint(1, 25))
                renamed = [
                    (kwargs, f"z{len(items) - index:04d}.json")
                    for index, (kwargs, _) in enumerate(items)
                ]
                payloads = []
                for variant in (items, renamed):
                    with tempfile.TemporaryDirectory() as tmp:
                        directory = Path(tmp)
                        self._write(directory, variant)
                        _rollup, model = self._build(directory)
                        payload = model.to_payload()
                        # The filename is legitimately part of the evidence,
                        # so compare everything except that field.
                        for panel in payload["panels"]:
                            for row in panel["rows"]:
                                for ref in row["evidence"]:
                                    ref.pop("path")
                        payloads.append(json.dumps(payload, ensure_ascii=False))

                self.assertEqual(payloads[0], payloads[1])

    def test_the_state_word_always_agrees_with_the_fold(self):
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 3000)
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, self._sequence(rnd, rnd.randint(1, 30)))
                    rollup, model = self._build(directory)

                    for row in model.panel("PROJECTS").rows:
                        project = rollup.project(row.key)
                        self.assertIn(row.values["state"], PROJECT_STATES)
                        self.assertEqual(
                            row.values["state"] == "BLOCKED", project.is_blocked
                        )
                        if row.values["state"] == "COMPLETE":
                            self.assertTrue(project.is_complete)
                            self.assertFalse(project.is_blocked)

    def test_every_completion_names_the_event_that_completed_it(self):
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 4000)
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, self._sequence(rnd, rnd.randint(1, 30)))
                    rollup, model = self._build(directory)

                    metric = rollup.metric("projects_completed")
                    self.assertEqual(len(metric.evidence), metric.value)
                    for ref in metric.evidence:
                        self.assertTrue((directory / ref.path).is_file())
                    del model

    def test_the_risk_panel_is_exactly_the_blockers_plus_the_mismatches(self):
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 5000)
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, self._sequence(rnd, rnd.randint(1, 30)))
                    rollup, model = self._build(directory)

                    rows = model.panel("RISKS").rows
                    self.assertEqual(
                        len(rows), len(rollup.risks) + len(rollup.mismatches)
                    )
                    self.assertEqual(len({row.key for row in rows}), len(rows))
                    for row in rows:
                        # Every risk names one Event, and that file is there.
                        self.assertEqual(len(row.evidence), 1)
                        self.assertTrue((directory / row.evidence[0].path).is_file())

    def test_no_authored_field_escapes_into_the_payload(self):
        """One Event in each sequence carries a credential in every text
        field it is allowed to have one in."""
        import json
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 6000)
                items = self._sequence(rnd, rnd.randint(1, 20))
                poisoned = dict(items[0][0])
                poisoned.update(
                    event_id=f"EV-{SECRET}",
                    project_id=f"PROJ-{SECRET}",
                    summary=f"note {SECRET}",
                    event_type="BLOCKED",
                    status="BLOCKED",
                    blocker=f"waiting on {SECRET}",
                )
                poisoned.pop("milestone", None)
                items[0] = (poisoned, items[0][1])
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, items)
                    # ...and one file that is JSON but not an Event, whose
                    # rejected value the validator echoes back into the
                    # `unreadable` reason. Both halves of the payload have to
                    # hold, and they are redacted in different places.
                    (directory / f"{SECRET}.json").write_text(
                        json.dumps(
                            {"schema_version": "1.0", "source": SECRET},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    _rollup, model = self._build(directory)

                    self.assertNotIn(
                        SECRET, json.dumps(model.to_payload(), ensure_ascii=False)
                    )

    def test_the_coverage_range_contains_every_event(self):
        """`coverage` is what a consumer trusts when every panel says zero,
        so it has to be right on sequences, not just on fixtures."""
        import random
        import tempfile
        from datetime import datetime as datetime_type

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 8000)
                items = self._sequence(rnd, rnd.randint(1, 30))
                days = sorted(
                    datetime_type.fromisoformat(kwargs["timestamp"]).date()
                    for kwargs, _ in items
                )
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, items)
                    _rollup, model = self._build(directory)

                    self.assertEqual(model.coverage.evidence_from, days[0].isoformat())
                    self.assertEqual(model.coverage.evidence_to, days[-1].isoformat())
                    self.assertLessEqual(
                        model.coverage.evidence_from, model.coverage.evidence_to
                    )

    def test_complete_means_exactly_what_it_says(self):
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 9000)
                items = self._sequence(rnd, rnd.randint(0, 20))
                torn = rnd.random() < 0.5
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, items)
                    if torn:
                        (directory / "torn.json").write_text("{", encoding="utf-8")
                    _rollup, model = self._build(directory)

                    self.assertEqual(model.coverage.unreadable, len(model.unreadable))
                    self.assertEqual(model.coverage.unreadable, 1 if torn else 0)
                    self.assertEqual(model.coverage.complete, not torn)
                    self.assertEqual(
                        model.to_payload()["coverage"]["complete"], not torn
                    )

    def test_the_attention_set_is_exactly_the_risks_panel(self):
        """What `ops_status.py` prints under ATTENTION from this block is one
        line per RISKS row — no more, and none of the other panels. Stated
        over sequences because the alternative (a threshold, a silent team, an
        unreadable file quietly becoming an alarm) is the kind of drift that
        arrives one line at a time."""
        import io
        import random
        import tempfile
        from contextlib import redirect_stdout
        from unittest import mock

        import ops_status

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 10000)
                items = self._sequence(rnd, rnd.randint(0, 20))
                with tempfile.TemporaryDirectory() as tmp:
                    runtime = Path(tmp)
                    directory = runtime / "events" / "processed"
                    directory.mkdir(parents=True)
                    self._write(directory, items)
                    _rollup, model = self._build(directory)

                    buffer = io.StringIO()
                    with mock.patch.object(ops_status, "RUNTIME_DIR", runtime):
                        with redirect_stdout(buffer):
                            attention = ops_status._print_control_tower(NOW)

                    self.assertEqual(
                        len(attention), len(model.panel("RISKS").rows)
                    )

    def test_no_age_is_ever_negative(self):
        import random
        import tempfile

        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed + 7000)
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self._write(directory, self._sequence(rnd, rnd.randint(1, 30)))
                    _rollup, model = self._build(directory)

                    for panel in model.panels:
                        for row in panel.rows:
                            for name in ("days_blocked", "days_idle", "days_silent", "days_open"):
                                value = row.values.get(name)
                                if value is not None:
                                    self.assertGreaterEqual(value, 0, (panel.key, name))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
