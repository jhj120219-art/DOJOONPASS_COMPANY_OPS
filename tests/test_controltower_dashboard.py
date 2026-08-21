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
    DASHBOARD_SCHEMA_VERSION,
    EVIDENCE_IN_PAYLOAD,
    PROJECT_STATES,
    _UNAUTHORED_KEYS,
)
from controltower.rollup import RECENT_LIMIT  # noqa: E402
from events import ROLES, Event, create_event  # noqa: E402
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


#: Every panel `build_dashboard()` produces, in the order it produces them.
#:
#: One tuple rather than a literal per test: the order test and the two
#: "a full model" count tests were three separate restatements of the same
#: list, and a panel added to two of them and not the third is a gap that
#: reads as a pass. The order itself is part of the contract — a payload that
#: reordered itself between runs would make a diff between two Notion
#: snapshots mean nothing.
EXPECTED_PANELS: tuple = (
    "COMPANY_GOALS",
    "METRICS",
    "TEAMS",
    "PROJECTS",
    "SPRINTS",
    "DESKTOPS",
    "RISKS",
    "ACTIVITY",
    "COMPLETIONS",
    "JUDGEMENTS",
)


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
        # `day` is the ordinary way to place an Event; an explicit
        # `timestamp` overrides it, the same override `source` already has.
        # Two Events on one day need distinct instants to have an order, and
        # the panels that put them in one are the reason this exists.
        timestamp = extra.pop("timestamp", None) or f"2026-08-{day:02d}T09:00:00+09:00"
        event = create_event(
            source=source,
            role=role,
            project_id=project,
            event_type=event_type,
            status=status,
            summary=extra.pop("summary", None) or f"summary for {event_id}",
            history_candidate=True,
            event_id=event_id,
            timestamp=timestamp,
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
        from events import EVENT_TYPES, ROLES, SOURCES, STATUSES
        from notion.properties import ROLE_DISPLAY_NAMES

        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 6, blocker="x")
        self.put("E2", "PAY", "CMO", "STARTED", "IN_PROGRESS", 7, source="DESKTOP_1")
        allowed = (
            set(ROLES)
            | set(SOURCES)
            | set(STATUSES)
            # The ACTIVITY / COMPLETIONS panels exempt `event_type`, which is
            # `events.EVENT_TYPES` — a frozenset `validate_event()` enforces,
            # the same footing as `status` beside it.
            | set(EVENT_TYPES)
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
        """The subject here is the round trip, not the number.

        This restated `"1.0"` as a literal, which is the shape docs/13 §3-⑧'s
        own paragraph warns about — "문서에 박아 둔 숫자는 그때마다 조용히
        틀렸다" — one layer over. Read off the constant instead:
        `ThePayloadShapeIsPinnedToItsVersionTests` is what holds the constant
        to meaning something, and a version bump should not have to hunt for
        copies of itself in assertions about JSON.
        """
        self._populate()

        text = json.dumps(self.model().to_payload(), ensure_ascii=False)

        self.assertEqual(
            json.loads(text)["schema_version"], DASHBOARD_SCHEMA_VERSION
        )

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
            list(EXPECTED_PANELS),
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


class WhatHappenedLatelyTests(DashboardTestCase):
    """ACTIVITY and COMPLETIONS — the two questions a fold cannot answer.

    Every other panel collapses Events into a state or a count.
    `ProjectRollup` says a project is BLOCKED; it cannot say what happened on
    Tuesday. The request asks for 최근 활동 in all three of its dashboards and
    최근 완료 as its own item, and neither had anywhere to come from: the
    METRICS panel counts `milestones_completed` and names none of them, and
    PROJECTS carries `completed_at` only for a finished **project**, which on
    this repository's own evidence is 0 of 16 Events while 14 completed a
    milestone.
    """

    def _many(self, count, *, event_type="STARTED", status="IN_PROGRESS", start=1):
        for index in range(count):
            self.put(
                f"E{start + index:03d}",
                "PAY",
                "CTO_BACKEND",
                event_type,
                status,
                1,
                timestamp=f"2026-08-01T{index % 24:02d}:{index % 60:02d}:00+09:00",
            )

    def _rows(self, key):
        return self.model().panel(key).rows

    def test_the_newest_event_is_the_first_row(self):
        self.put("OLD", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put("NEW", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 7)

        self.assertEqual([row.key for row in self._rows("ACTIVITY")], ["NEW", "OLD"])

    def test_the_list_is_bounded(self):
        self._many(RECENT_LIMIT + 15)

        self.assertEqual(len(self._rows("ACTIVITY")), RECENT_LIMIT)

    def test_a_bounded_row_says_what_it_is_a_slice_of(self):
        """Twenty rows with no "of 35" beside them is the false reading
        `evidence_truncated` prevents one level down."""
        self._many(RECENT_LIMIT + 15)

        row = self._rows("ACTIVITY")[0]
        self.assertEqual(row.values["of_total"], RECENT_LIMIT + 15)
        self.assertTrue(row.values["truncated"])

    def test_an_unbounded_list_says_that_too(self):
        """`truncated=False` is present, not omitted. A reader who only ever
        sees the field when it fires cannot tell a complete list from one
        that never learned to say."""
        self._many(3)

        row = self._rows("ACTIVITY")[0]
        self.assertEqual(row.values["of_total"], 3)
        self.assertFalse(row.values["truncated"])

    def test_a_completion_survives_a_busy_week(self):
        """The loss a single filtered list would have.

        One completion, then `RECENT_LIMIT` louder Events on top of it. If
        COMPLETIONS were a filter over ACTIVITY it would now be empty — on
        exactly the week an operator most wants to know something finished.
        """
        self.put(
            "DONE",
            "PAY",
            "CTO_BACKEND",
            "MILESTONE_COMPLETED",
            "IN_PROGRESS",
            1,
            milestone="M1",
            timestamp="2026-08-01T00:00:00+09:00",
        )
        self._many(RECENT_LIMIT + 5, start=100)

        self.assertNotIn("DONE", [row.key for row in self._rows("ACTIVITY")])
        self.assertEqual([row.key for row in self._rows("COMPLETIONS")], ["DONE"])

    def test_both_kinds_of_completion_count(self):
        """`MILESTONE_COMPLETED` finishes a step and `COMPLETED` finishes a
        project. A panel that showed only the second would have been empty
        while this repository completed fourteen things."""
        self.put(
            "M", "PAY", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 5,
            milestone="M1",
        )
        self.put("C", "PAY", "CTO_BACKEND", "COMPLETED", "COMPLETED", 6)

        self.assertEqual({row.key for row in self._rows("COMPLETIONS")}, {"M", "C"})

    def test_a_cancellation_is_not_a_completion(self):
        """It ends a project without finishing anything, and
        `PROJECT_STATES` already keeps the two apart for the same reason."""
        self.put("X", "PAY", "CTO_BACKEND", "CANCELLED", "CANCELLED", 5)

        self.assertEqual(self._rows("COMPLETIONS"), ())

    def test_the_completion_total_counts_the_whole_period(self):
        self.put(
            "D0", "PAY", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 1,
            milestone="M", timestamp="2026-08-01T00:00:00+09:00",
        )
        self._many(RECENT_LIMIT + 5, event_type="MILESTONE_COMPLETED", start=100)

        rows = self._rows("COMPLETIONS")
        self.assertEqual(len(rows), RECENT_LIMIT)
        self.assertEqual(rows[0].values["of_total"], RECENT_LIMIT + 6)

    def test_each_row_carries_the_file_it_came_from(self):
        """A Control Tower number nobody can trace is a rumour, and this is
        the panel where the row **is** the Event."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        row = self._rows("ACTIVITY")[0]
        self.assertEqual(len(row.evidence), 1)
        self.assertEqual(row.evidence[0].event_id, "E1")
        self.assertTrue((self.processed / row.evidence[0].path).is_file())

    def test_the_summary_reaches_the_row(self):
        """The first panel that carries a sentence a person wrote — which is
        why `RECENT_LIMIT` exists at all."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        self.assertEqual(self._rows("ACTIVITY")[0].values["summary"], "summary for E1")

    def test_an_authored_summary_is_redacted_on_the_way_out(self):
        """`summary` is authored text that `validate_event()` only
        type-checks, so it is on `to_payload()`'s redaction side — not on
        `_UNAUTHORED_KEYS`."""
        self.put(
            "E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5,
            summary=f"token is {SECRET}",
        )

        payload = json.dumps(self.model().to_payload(), ensure_ascii=False)
        self.assertNotIn(SECRET, payload)
        self.assertIn("[REDACTED]", payload)

    def test_the_two_panels_declare_the_same_columns(self):
        """Same rows selected two ways. Different shapes would make one
        consumer of "an activity row" impossible."""
        model = self.model()

        self.assertEqual(
            model.panel("ACTIVITY").columns, model.panel("COMPLETIONS").columns
        )

    def test_an_empty_company_still_has_both_panels_sourced(self):
        """Empty and unsourced mean opposite things, and this is the
        distinction the whole module is arranged around."""
        model = self.model()

        for key in ("ACTIVITY", "COMPLETIONS"):
            with self.subTest(panel=key):
                panel = model.panel(key)
                self.assertIs(panel.status, PanelStatus.SOURCED)
                self.assertEqual(panel.rows, ())

    def test_the_note_does_not_move_with_the_evidence(self):
        """`note` is one of the strings `_out()` never redacts, on the
        grounds that this module wrote it. The first draft interpolated the
        counts into it, and `AuthoredValuesAreRedactedOnTheWayOutTests`
        refused that — correctly: a note that varies with the Events destroys
        the byte-comparison that would catch a leak in one."""
        before = self.model().panel("ACTIVITY").note
        self._many(5)

        self.assertEqual(self.model().panel("ACTIVITY").note, before)

    def test_two_events_at_one_instant_keep_a_stable_order(self):
        """No second sort. `event_instant_key()` already settles ties and
        `_fold_duplicates()` depends on that order; asking again here would
        be a second opinion about which came first."""
        for event_id in ("B", "A"):
            self.put(
                event_id, "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5,
                timestamp="2026-08-05T09:00:00+09:00",
            )

        first = [row.key for row in self._rows("ACTIVITY")]
        second = [row.key for row in self._rows("ACTIVITY")]
        self.assertEqual(first, second)


class TheJudgementsPanelRefusesRatherThanWaitsTests(DashboardTestCase):
    """Critical Path and 완료 조건: asked for, unsourced, and now declared.

    Separate from COMPANY_GOALS because the two are unsourced for different
    reasons, and a reader has to be able to tell them apart. A Goal has no
    source *yet* — BACKLOG carries the open question of where one would
    live. These are refused on purpose: docs/03 §4, docs/04 §44 and docs/04
    §68 each say they are not derived from Events, so computing one here
    would contradict three specs rather than get ahead of them.
    """

    def test_the_panel_exists_and_is_unsourced(self):
        panel = self.model().panel("JUDGEMENTS")

        self.assertIsNotNone(panel)
        self.assertIs(panel.status, PanelStatus.UNSOURCED)
        self.assertEqual(panel.rows, ())

    def test_it_claims_both_layers(self):
        self.assertEqual(
            set(self.model().panel("JUDGEMENTS").unsourced_layers),
            {"CRITICAL_PATH", "COMPLETION_CRITERIA"},
        )

    def test_the_note_names_the_specs_that_refuse_them(self):
        note = self.model().panel("JUDGEMENTS").note

        for citation in ("docs/04 §44", "docs/04 §68", "docs/03 §4"):
            with self.subTest(citation=citation):
                self.assertIn(citation, note)

    def test_the_note_names_critical_path_itself(self):
        """A consumer looking for the words the request used has to find
        them; a layer key alone is not the vocabulary anyone searches."""
        self.assertIn("Critical Path", self.model().panel("JUDGEMENTS").note)

    def test_goals_and_judgements_are_not_the_same_panel(self):
        model = self.model()

        self.assertNotEqual(
            model.panel("COMPANY_GOALS").unsourced_layers,
            model.panel("JUDGEMENTS").unsourced_layers,
        )

    def test_nothing_derives_a_critical_path(self):
        """The property the specs demand, asserted against the payload: no
        panel, column or value anywhere claims to be one."""
        self.put("E1", "PAY", "CTO_BACKEND", "COMPLETED", "COMPLETED", 5)
        payload = self.model().to_payload()

        for panel in payload["panels"]:
            if panel["key"] == "JUDGEMENTS":
                continue
            with self.subTest(panel=panel["key"]):
                self.assertNotIn("critical", " ".join(panel["columns"]).lower())


class TheOperationalHalfReallyIsInOpsRunsTests(unittest.TestCase):
    """The model's own reason for **not** carrying seven of the request's
    fields, checked against the schema it points at.

    `dashboard.py`'s "Where ⑤'s operational half lives" block is not
    commentary — it is the justification for an absence. Agent state, Runner
    state, Last Run, Backup, Delivery, Recovery and Notion Sync have no
    panel here *because* the `OPS_RUNS` row already carries them, and the
    block names the columns.

    A list nothing reads drifts. Four of the five Notion Sync entries were
    written as `Skipped` / `Retried` / `Unreadable` / `Queued` — none of
    which is a column; the real ones are `Notion Skipped` and so on. Reading
    fine is not the same as being right, and a reader following that list
    into the Database would have found nothing under four of the names.

    The worse version is what this prevents: a column renamed in
    `DASHBOARD_DATABASES` leaves this justification standing and true-looking
    while the fact reaches nobody at all — not the panels, which deferred to
    the row, and not the row, which no longer has it.
    """

    #: The docstring block, parsed rather than restated. Restating it would
    #: make this a third copy of the same list.
    BLOCK_START = "Where ⑤'s operational half lives"
    BLOCK_END = "Restating them here would be"

    def _named_columns(self):
        """Only the indented table rows, not the prose around them.

        The first version scanned every backtick between the two markers and
        picked up this class's own name out of a sentence explaining the
        gate — a parse that reads its own documentation is a parse that
        fails for reasons unrelated to its subject.
        """
        import re

        from controltower import dashboard as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        start = source.index(self.BLOCK_START)
        end = source.index(self.BLOCK_END, start)
        columns: set = set()
        for line in source[start:end].splitlines():
            if not line.startswith("    ") or "`" not in line:
                continue
            columns |= set(re.findall(r"`([A-Z][A-Za-z ]+)`", line))
        return sorted(columns)

    def test_the_block_still_names_columns(self):
        """Guard against the parse silently matching nothing — the failure
        that would make every assertion below vacuous."""
        self.assertGreater(len(self._named_columns()), 12)

    def test_every_column_it_names_exists_in_the_ops_runs_schema(self):
        from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

        schema = DASHBOARD_DATABASES[OPS_RUNS]
        for column in self._named_columns():
            with self.subTest(column=column):
                self.assertIn(
                    column,
                    schema,
                    f"the Dashboard Model defers to `{column}` on the OPS_RUNS "
                    "row and no such column exists — the fact it declines to "
                    "carry reaches nobody",
                )

    def test_it_names_the_facts_the_request_asks_for(self):
        """The other direction, at the level a reader cares about: each of
        the request's operational items has at least one named column."""
        named = " ".join(self._named_columns())
        for fact, column in (
            ("최근 실행", "Run At"),
            ("성공 / 실패", "Overall"),
        ):
            with self.subTest(fact=fact):
                # These two are on the row and deliberately *not* in the
                # block, because the block lists what the panels defer — the
                # row's identity and verdict are the row's own.
                from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

                self.assertIn(column, DASHBOARD_DATABASES[OPS_RUNS])
        for fact, column in (
            ("Backup", "Backup Status"),
            ("Delivery", "Transport Moved"),
            ("Recovery", "Reused Days"),
            ("Notion Sync", "Notion Queued"),
            ("Desktop", "Desktops Reporting"),
        ):
            with self.subTest(fact=fact):
                self.assertIn(column, named)

    def test_no_panel_restates_what_the_row_carries(self):
        """The reason the deferral is worth keeping: two derivations of one
        run is how a screen and a row start disagreeing about it."""
        from controltower import build_company_rollup

        model = build_dashboard(
            build_company_rollup(events=(), now=NOW), now=NOW
        )
        columns = {
            column for panel in model.panels for column in panel.columns
        }

        for operational in ("backup_status", "run_id", "overall", "notion_queued"):
            with self.subTest(column=operational):
                self.assertNotIn(operational, columns)


class ThePayloadShapeIsPinnedToItsVersionTests(DashboardTestCase):
    """Drift detection for `to_payload()`.

    `DASHBOARD_SCHEMA_VERSION` was decorative. It read "1.0" through C49
    adding the entire `coverage` block, C50 adding `coverage.duplicates`, and
    C52 adding three panels and two columns — three shape changes, none
    announced, because nothing ever compared the number to the shape. A
    version string that cannot move is worse than none: it tells a consumer
    "nothing changed" while things change.

    This is the comparison. The fingerprint is **derived** from a real model
    rather than hand-listed, so it cannot drift from the code; the recorded
    copy below is the only hand-written part, and it is what a Sprint has to
    update deliberately.

    Both directions are enforced, and the second is the one that matters:

        shape changed, version did not      -> fail (silent drift)
        version changed, shape not recorded -> fail (a number nobody earned)
        anything removed or renamed, MAJOR unchanged -> fail

    Removal is singled out because it is the only change that breaks a
    reader. A new panel is invisible to a consumer that ignores what it does
    not know; a **deleted** column is a KeyError in somebody else's code.
    """

    #: Every panel populated, so no panel's columns are missing from the
    #: fingerprint just because the fixture never gave it a row. The
    #: fingerprint reads `panel["columns"]`, which a panel declares whether
    #: or not it has rows — but a fixture that exercises the rows too is what
    #: makes `_row_keys` and `_evidence_keys` real rather than assumed.
    def _populated(self):
        self.put("E1", "PAY", "CTO_BACKEND", "BLOCKED", "BLOCKED", 5, blocker="b")
        self.put(
            "E2", "PAY", "CTO_BACKEND", "MILESTONE_COMPLETED", "IN_PROGRESS", 6,
            milestone="M1",
        )
        self.put("E3", "ADS", "CMO", "COMPLETED", "COMPLETED", 7)
        # A Desktop whose Event claims another team's role — the RISKS
        # panel's second row kind.
        self.put(
            "E4", "ADS", "CMO", "STARTED", "IN_PROGRESS", 8, source="DESKTOP_3"
        )
        # One file the rollup cannot use, so `unreadable_entry` is measured
        # rather than assumed. A fingerprint that recorded `[]` for it would
        # pin nothing about the shape of an entry an operator only ever sees
        # when something is already wrong.
        (self.processed / "torn.json").write_text('{"schema', encoding="utf-8")
        return self.model().to_payload()

    @staticmethod
    def _shape(payload):
        """The structure, with every value thrown away.

        Keys and columns only: two runs over different evidence produce the
        same fingerprint, and the only thing that can change it is the
        payload's *shape*.
        """
        rows = [row for panel in payload["panels"] for row in panel["rows"]]
        evidence = [ref for row in rows for ref in row["evidence"]]
        return {
            "top_level": sorted(payload),
            "coverage": sorted(payload["coverage"]),
            "unreadable_entry": sorted(payload["unreadable"][0])
            if payload["unreadable"]
            else [],
            "panel": sorted(payload["panels"][0]),
            "row": sorted(rows[0]) if rows else [],
            "evidence": sorted(evidence[0]) if evidence else [],
            "panels": {
                panel["key"]: list(panel["columns"]) for panel in payload["panels"]
            },
            "panel_order": [panel["key"] for panel in payload["panels"]],
        }

    #: The recorded shape of every published `DASHBOARD_SCHEMA_VERSION`.
    #: Update deliberately, together with the version, or not at all.
    #:
    #: **Add an entry; never rename one.** C56 bumped 1.1 to 1.2 by renaming
    #: this key, which deleted the only record of what 1.1 looked like — and
    #: `test_nothing_recorded_earlier_has_been_removed` skips the current
    #: version, so with one entry left it iterated an empty set and passed by
    #: doing nothing. The gate built to catch a silent removal was itself
    #: silently disabled by the edit that was supposed to exercise it.
    #: `test_the_removal_rule_actually_compared_something` is the guard
    #: against that happening again.
    RECORDED = {
        # 1.1 is 1.2 minus `coverage.history_checked`. Kept so the MINOR
        # promise — a 1.1 reader finds every key it knew — is checkable
        # rather than asserted.
        "1.1": {
            "top_level": [
                "coverage", "events_read", "generated_at", "panels",
                "schema_version", "since", "unreadable", "until",
            ],
            "coverage": [
                "complete", "duplicates", "evidence_from", "evidence_to",
                "history_uncovered_from", "unreadable",
            ],
            "unreadable_entry": ["file", "reason"],
            "panel": [
                "columns", "key", "note", "rows", "source", "status", "title",
                "unsourced_layers",
            ],
            "row": ["evidence", "evidence_count", "evidence_truncated", "key", "values"],
            "evidence": ["at", "event_id", "path"],
            "panels": {
                "COMPANY_GOALS": [],
                "METRICS": ["key", "label", "value", "derived_from", "evidence_count"],
                "TEAMS": [
                    "team", "display_name", "events", "projects",
                    "blocked_projects", "blocked_project_count", "last_seen",
                    "has_activity", "current_sprint",
                ],
                "PROJECTS": [
                    "project_id", "teams", "events", "status", "state", "blocker",
                    "blocker_team", "blocked_since", "days_blocked", "first_seen",
                    "last_seen", "days_idle", "completed_at", "milestones", "sprint",
                ],
                "SPRINTS": [],
                "DESKTOPS": [
                    "source", "expected_team", "display_name", "events", "projects",
                    "last_seen", "days_silent", "has_activity", "role_mismatches",
                    "mismatched_event_ids",
                ],
                "RISKS": [
                    "kind", "project_id", "team", "blocker", "since", "days_open",
                    "event_id", "source", "claimed_role", "expected_role", "kept",
                    "ignored",
                ],
                "ACTIVITY": [
                    "event_id", "at", "source", "team", "project_id", "event_type",
                    "status", "summary", "milestone", "of_total", "truncated",
                ],
                "COMPLETIONS": [
                    "event_id", "at", "source", "team", "project_id", "event_type",
                    "status", "summary", "milestone", "of_total", "truncated",
                ],
                "JUDGEMENTS": [],
            },
            "panel_order": list(EXPECTED_PANELS),
        },
        "1.2": {
            "top_level": [
                "coverage", "events_read", "generated_at", "panels",
                "schema_version", "since", "unreadable", "until",
            ],
            "coverage": [
                "complete", "duplicates", "evidence_from", "evidence_to",
                "history_checked", "history_uncovered_from", "unreadable",
            ],
            "unreadable_entry": ["file", "reason"],
            "panel": [
                "columns", "key", "note", "rows", "source", "status", "title",
                "unsourced_layers",
            ],
            "row": ["evidence", "evidence_count", "evidence_truncated", "key", "values"],
            "evidence": ["at", "event_id", "path"],
            "panels": {
                "COMPANY_GOALS": [],
                "METRICS": ["key", "label", "value", "derived_from", "evidence_count"],
                "TEAMS": [
                    "team", "display_name", "events", "projects",
                    "blocked_projects", "blocked_project_count", "last_seen",
                    "has_activity", "current_sprint",
                ],
                "PROJECTS": [
                    "project_id", "teams", "events", "status", "state", "blocker",
                    "blocker_team", "blocked_since", "days_blocked", "first_seen",
                    "last_seen", "days_idle", "completed_at", "milestones", "sprint",
                ],
                "SPRINTS": [],
                "DESKTOPS": [
                    "source", "expected_team", "display_name", "events", "projects",
                    "last_seen", "days_silent", "has_activity", "role_mismatches",
                    "mismatched_event_ids",
                ],
                "RISKS": [
                    "kind", "project_id", "team", "blocker", "since", "days_open",
                    "event_id", "source", "claimed_role", "expected_role", "kept",
                    "ignored",
                ],
                "ACTIVITY": [
                    "event_id", "at", "source", "team", "project_id", "event_type",
                    "status", "summary", "milestone", "of_total", "truncated",
                ],
                "COMPLETIONS": [
                    "event_id", "at", "source", "team", "project_id", "event_type",
                    "status", "summary", "milestone", "of_total", "truncated",
                ],
                "JUDGEMENTS": [],
            },
            "panel_order": list(EXPECTED_PANELS),
        }
    }

    def test_the_version_has_a_recorded_shape(self):
        """A number nobody recorded a shape for is the old problem back."""
        self.assertIn(DASHBOARD_SCHEMA_VERSION, self.RECORDED)

    def test_the_payload_still_has_that_shape(self):
        self.assertEqual(
            self._shape(self._populated()), self.RECORDED[DASHBOARD_SCHEMA_VERSION]
        )

    def test_the_shape_does_not_depend_on_the_evidence(self):
        """A fingerprint that moved with the data would fire on every quiet
        week and be ignored by the second Sprint."""
        first = self._shape(self._populated())
        self.put("E9", "NEW_PROJECT", "COO", "STARTED", "IN_PROGRESS", 9)

        self.assertEqual(self._shape(self.model().to_payload()), first)

    def test_an_empty_company_declares_the_same_panels_and_columns(self):
        """The half a populated-only fixture would miss: a consumer reading a
        quiet run must still be told the shape."""
        empty = self._shape(self.model().to_payload())
        recorded = self.RECORDED[DASHBOARD_SCHEMA_VERSION]

        self.assertEqual(empty["panels"], recorded["panels"])
        self.assertEqual(empty["panel_order"], recorded["panel_order"])
        self.assertEqual(empty["top_level"], recorded["top_level"])

    def test_the_removal_rule_actually_compared_something(self):
        """A comparison over an empty collection passes by doing nothing.

        That is not hypothetical here: C56 renamed the `"1.1"` key to
        `"1.2"` instead of adding one, leaving a single recorded shape — and
        the test below, which skips the current version, then iterated
        nothing. It reported green while checking that no key had been
        removed between versions it could no longer see.
        """
        major = DASHBOARD_SCHEMA_VERSION.split(".")[0]
        earlier = [
            version
            for version in self.RECORDED
            if version.split(".")[0] == major
            and version != DASHBOARD_SCHEMA_VERSION
        ]

        self.assertTrue(
            earlier,
            "no earlier shape is recorded for this MAJOR — the removal rule "
            "below has nothing to compare against and passes vacuously. Add "
            "the previous version's shape rather than renaming its key.",
        )

    def test_a_reader_of_any_earlier_version_still_finds_its_keys(self):
        """The MINOR promise, from the reader's side.

        `notion_projection.project_panels()` reads this payload by key. A
        reader written against 1.1 must find every key it knew in a 1.2
        payload — that is what makes the bump MINOR rather than MAJOR, and
        it is checkable now that 1.1's shape exists again.
        """
        current = self.RECORDED[DASHBOARD_SCHEMA_VERSION]
        for version, shape in self.RECORDED.items():
            if version == DASHBOARD_SCHEMA_VERSION:
                continue
            for section in ("top_level", "coverage", "panel", "row", "evidence"):
                with self.subTest(version=version, section=section):
                    self.assertTrue(set(shape[section]) <= set(current[section]))

    def test_nothing_recorded_earlier_has_been_removed(self):
        """MAJOR's rule. Additions are invisible to a reader that ignores
        unknown keys; a **removal** is a KeyError in somebody else's code, so
        it may not happen inside a MINOR bump.

        Checked against every recorded version, not just the current one, so
        the guarantee survives the next bump: 1.1 may add to 1.0's shape and
        may not take anything out of it.
        """
        major = DASHBOARD_SCHEMA_VERSION.split(".")[0]
        current = self.RECORDED[DASHBOARD_SCHEMA_VERSION]

        for version, shape in self.RECORDED.items():
            if version.split(".")[0] != major or version == DASHBOARD_SCHEMA_VERSION:
                continue
            with self.subTest(version=version):
                for section in ("top_level", "coverage", "panel", "row", "evidence"):
                    self.assertEqual(
                        set(shape[section]) - set(current[section]),
                        set(),
                        f"{section} lost a key without a MAJOR bump",
                    )
                for panel, columns in shape["panels"].items():
                    self.assertIn(panel, current["panels"], "a panel disappeared")
                    self.assertEqual(
                        set(columns) - set(current["panels"][panel]),
                        set(),
                        f"{panel} lost a column without a MAJOR bump",
                    )

    def test_the_model_carries_the_version_it_was_built_with(self):
        """The number has to reach the payload, or a consumer cannot read
        it at all."""
        self.assertEqual(
            self._populated()["schema_version"], DASHBOARD_SCHEMA_VERSION
        )

    def test_the_version_is_a_major_minor_pair(self):
        """The rule above is meaningless if the string is not one."""
        major, _, minor = DASHBOARD_SCHEMA_VERSION.partition(".")

        self.assertTrue(major.isdigit(), DASHBOARD_SCHEMA_VERSION)
        self.assertTrue(minor.isdigit(), DASHBOARD_SCHEMA_VERSION)

    def test_the_gate_would_notice_a_new_column(self):
        """The detector detects. A column added to a panel and to nothing
        else must fail this comparison."""
        payload = self._populated()
        payload["panels"][1]["columns"] = list(payload["panels"][1]["columns"]) + [
            "invented"
        ]

        self.assertNotEqual(
            self._shape(payload), self.RECORDED[DASHBOARD_SCHEMA_VERSION]
        )

    def test_the_gate_would_notice_a_removed_top_level_key(self):
        payload = self._populated()
        payload.pop("since")

        self.assertNotEqual(
            self._shape(payload), self.RECORDED[DASHBOARD_SCHEMA_VERSION]
        )


class TheNullabilityContractTests(unittest.TestCase):
    """Which payload fields may be null — the half `ThePayloadShapeIsPinnedToItsVersionTests`
    does not cover.

    That gate records which **keys exist**. A consumer needs one more thing
    from a schema: which of them can be `null`. Without it "the key is there"
    is only half a contract, and both halves of getting it wrong are real:

        a NULLABLE field a consumer treats as required
            -> a crash in somebody else's code on the first quiet week
        an ALWAYS field that starts being null
            -> `notion_projection._property()` turns null into
               `{"number": None}` / `{"date": None}` / `{"select": None}`,
               and for text into `""`. For `Row Key` — a `title` built from
               `PROJECTS.project_id` — that is an **empty title**, which is a
               row `find_by_title()` can no longer tell from any other empty
               one. Row identity is the thing the whole projection is keyed
               on.

    Measured, not declared. The lists below were produced by building a model
    over each state and recording what came out null; writing them from
    reading the code would have got them wrong, and did — a first pass over
    seven states recorded `RISKS.since` as never-null, because
    `EVENT_ID_CONFLICT` is the only row kind that leaves it empty and no
    state produced one. That is why `test_the_fixture_reaches_every_row_kind`
    exists: a contract measured over an incomplete fixture is narrower than
    the truth and looks just as authoritative.
    """

    #: `(section, field)`; `""` is the payload's top level.
    NULLABLE = {
        ("", "since"),
        ("", "until"),
        ("ACTIVITY", "milestone"),
        ("COMPLETIONS", "milestone"),
        ("DESKTOPS", "days_silent"),
        ("DESKTOPS", "last_seen"),
        ("PROJECTS", "blocked_since"),
        ("PROJECTS", "blocker"),
        ("PROJECTS", "blocker_team"),
        ("PROJECTS", "completed_at"),
        ("PROJECTS", "days_blocked"),
        ("PROJECTS", "sprint"),
        ("RISKS", "blocker"),
        ("RISKS", "claimed_role"),
        ("RISKS", "days_open"),
        ("RISKS", "expected_role"),
        ("RISKS", "ignored"),
        ("RISKS", "kept"),
        ("RISKS", "project_id"),
        ("RISKS", "since"),
        ("RISKS", "source"),
        ("TEAMS", "current_sprint"),
        ("TEAMS", "last_seen"),
        ("coverage", "evidence_from"),
        ("coverage", "evidence_to"),
        ("coverage", "history_uncovered_from"),
    }

    ALWAYS = {
        ("", "events_read"),
        ("", "generated_at"),
        ("", "schema_version"),
        ("", "unreadable"),
        ("ACTIVITY", "at"),
        ("ACTIVITY", "event_id"),
        ("ACTIVITY", "event_type"),
        ("ACTIVITY", "of_total"),
        ("ACTIVITY", "project_id"),
        ("ACTIVITY", "source"),
        ("ACTIVITY", "status"),
        ("ACTIVITY", "summary"),
        ("ACTIVITY", "team"),
        ("ACTIVITY", "truncated"),
        ("COMPLETIONS", "at"),
        ("COMPLETIONS", "event_id"),
        ("COMPLETIONS", "event_type"),
        ("COMPLETIONS", "of_total"),
        ("COMPLETIONS", "project_id"),
        ("COMPLETIONS", "source"),
        ("COMPLETIONS", "status"),
        ("COMPLETIONS", "summary"),
        ("COMPLETIONS", "team"),
        ("COMPLETIONS", "truncated"),
        ("DESKTOPS", "display_name"),
        ("DESKTOPS", "events"),
        ("DESKTOPS", "expected_team"),
        ("DESKTOPS", "has_activity"),
        ("DESKTOPS", "mismatched_event_ids"),
        ("DESKTOPS", "projects"),
        ("DESKTOPS", "role_mismatches"),
        ("DESKTOPS", "source"),
        ("METRICS", "derived_from"),
        ("METRICS", "evidence_count"),
        ("METRICS", "key"),
        ("METRICS", "label"),
        ("METRICS", "value"),
        ("PROJECTS", "days_idle"),
        ("PROJECTS", "events"),
        ("PROJECTS", "first_seen"),
        ("PROJECTS", "last_seen"),
        ("PROJECTS", "milestones"),
        ("PROJECTS", "project_id"),
        ("PROJECTS", "state"),
        ("PROJECTS", "status"),
        ("PROJECTS", "teams"),
        ("RISKS", "event_id"),
        ("RISKS", "kind"),
        ("RISKS", "team"),
        ("TEAMS", "blocked_project_count"),
        ("TEAMS", "blocked_projects"),
        ("TEAMS", "display_name"),
        ("TEAMS", "events"),
        ("TEAMS", "has_activity"),
        ("TEAMS", "projects"),
        ("TEAMS", "team"),
        ("coverage", "complete"),
        ("coverage", "duplicates"),
        ("coverage", "history_checked"),
        ("coverage", "unreadable"),
    }

    def _event(self, **overrides):
        data = {
            "schema_version": "1.0",
            "event_id": "E",
            "timestamp": "2026-08-12T10:00:00+09:00",
            "source": "DESKTOP_1",
            "role": "CTO_BACKEND",
            "project_id": "P",
            "event_type": "STARTED",
            "status": "IN_PROGRESS",
            "summary": "s",
            "history_candidate": True,
        }
        data.update(overrides)
        return Event.from_dict(data)

    def _states(self):
        """One state per branch that can leave a field empty.

        `conflict` is two files claiming one `event_id` with different
        contents — the only way to reach an `EVENT_ID_CONFLICT` RISKS row,
        and the state whose absence made the first draft of `NULLABLE`
        wrong.
        """
        e = self._event
        return {
            "empty": (),
            "started": ((e(event_id="A"), "A.json"),),
            "blocked": (
                (
                    e(event_id="B", event_type="BLOCKED", status="BLOCKED", blocker="b"),
                    "B.json",
                ),
            ),
            "completed": (
                (e(event_id="C", event_type="COMPLETED", status="COMPLETED"), "C.json"),
            ),
            "milestone": (
                (
                    e(event_id="D", event_type="MILESTONE_COMPLETED", milestone="M"),
                    "D.json",
                ),
            ),
            "cancelled": (
                (e(event_id="X", event_type="CANCELLED", status="CANCELLED"), "X.json"),
            ),
            "mismatch": ((e(event_id="M", source="DESKTOP_3", role="CMO"), "M.json"),),
            "conflict": (
                (e(event_id="K"), "a.json"),
                (e(event_id="K", project_id="OTHER"), "b.json"),
            ),
        }

    def _observe(self):
        """`(seen, nullable)` over every state."""
        seen: set = set()
        nullable: set = set()
        for events in self._states().values():
            payload = build_dashboard(
                build_company_rollup(events=events, now=NOW), now=NOW
            ).to_payload()
            sections = [("", payload), ("coverage", payload["coverage"])]
            for panel in payload["panels"]:
                for row in panel["rows"]:
                    sections.append((panel["key"], row["values"]))
            for section, mapping in sections:
                for field, value in mapping.items():
                    if section == "" and field in ("panels", "coverage"):
                        continue
                    seen.add((section, field))
                    if value is None:
                        nullable.add((section, field))
        return seen, nullable

    def test_the_fixture_reaches_every_row_kind(self):
        """The contract is only as wide as the states that produced it.

        A missing row kind narrows `NULLABLE` silently and the result looks
        exactly as authoritative — which is how `RISKS.since` was first
        recorded as never-null.
        """
        kinds: set = set()
        states: set = set()
        for events in self._states().values():
            model = build_dashboard(
                build_company_rollup(events=events, now=NOW), now=NOW
            )
            for row in model.panel("RISKS").rows:
                kinds.add(row.values["kind"])
            for row in model.panel("PROJECTS").rows:
                states.add(row.values["state"])

        self.assertEqual(
            kinds, {"OPEN_BLOCKER", "ROLE_MISMATCH", "EVENT_ID_CONFLICT"}
        )
        self.assertEqual(states, set(PROJECT_STATES))

    def test_every_observed_field_is_classified(self):
        """Neither list may quietly stop covering a field — a new column
        with no entry is a field whose nullability nobody decided."""
        seen, _ = self._observe()

        self.assertEqual(seen - (self.ALWAYS | self.NULLABLE), set())

    def test_neither_list_names_a_field_that_is_gone(self):
        seen, _ = self._observe()

        self.assertEqual((self.ALWAYS | self.NULLABLE) - seen, set())

    def test_no_field_is_in_both_lists(self):
        self.assertEqual(self.ALWAYS & self.NULLABLE, set())

    def test_nothing_declared_always_is_ever_null(self):
        _, nullable = self._observe()
        offenders = sorted(self.ALWAYS & nullable)

        self.assertEqual(
            offenders,
            [],
            "a field the contract calls always-present came out null — for a "
            "text field the projection writes that as an empty string, and "
            "for `Row Key` an empty title is a row the lookup can no longer "
            "identify",
        )

    def test_everything_declared_nullable_really_can_be(self):
        """The other direction. A field marked nullable that never is
        teaches a consumer to write a null-check nobody needs, and hides
        the day it stops being reachable."""
        _, nullable = self._observe()
        never = sorted(self.NULLABLE - nullable)

        self.assertEqual(never, [])

    def test_a_nullable_field_is_still_always_present(self):
        """Null and absent are different. Every panel row fills every column
        its panel declares, so a consumer reads `values["blocker"]` and gets
        `None` — never a KeyError."""
        for name, events in self._states().items():
            model = build_dashboard(
                build_company_rollup(events=events, now=NOW), now=NOW
            )
            for panel in model.panels:
                for row in panel.rows:
                    with self.subTest(state=name, panel=panel.key):
                        self.assertEqual(set(row.values), set(panel.columns))

    def test_the_row_key_of_every_projected_panel_is_non_null(self):
        """The one that costs identity rather than a value.

        `notion_projection` builds `Row Key` from `DashboardRow.key`; a null
        there becomes an empty title, and `find_by_title("")` cannot tell one
        such row from another. Asserted for the panels that actually reach
        Notion — the two in `UNPROJECTED_PANELS` have no row key in a
        database to lose.
        """
        from controltower import notion_projection

        for name, events in self._states().items():
            model = build_dashboard(
                build_company_rollup(events=events, now=NOW), now=NOW
            )
            for panel_key in notion_projection.PANEL_PROJECTIONS:
                panel = model.panel(panel_key)
                for row in panel.rows:
                    with self.subTest(state=name, panel=panel_key):
                        self.assertIsNotNone(row.key)


class TheModelSurvivesTheStatesAnOperatorMeetsTests(DashboardTestCase):
    """Empty, failed, stale, restored. None of them may raise."""

    def test_an_empty_processed_directory_builds_a_full_model(self):
        model = self.model()

        self.assertEqual(len(model.panels), len(EXPECTED_PANELS))
        self.assertEqual(model.events_read, 0)
        self.assertEqual(model.unreadable, ())
        json.dumps(model.to_payload(), ensure_ascii=False)

    def test_a_missing_processed_directory_builds_a_full_model(self):
        rollup = build_company_rollup(
            processed_dir=self.processed / "gone", now=NOW
        )

        model = build_dashboard(rollup, now=NOW)

        self.assertEqual(len(model.panels), len(EXPECTED_PANELS))
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

    def test_a_clean_tree_is_complete_once_it_has_been_checked(self):
        """"Checked" is now part of "complete", so the fixture has to do what
        `ops_status.py` does. That is not ceremony: the whole point is that a
        model nobody asked cannot claim the whole picture."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        self.assertTrue(self.model().with_history_coverage(None).coverage.complete)

    def test_an_unchecked_tree_is_not_complete_however_clean(self):
        """The half that used to be missing. Nothing is unreadable and no
        history outruns the evidence — and nobody has looked at the second
        of those, so the honest answer is not yet."""
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        coverage = self.model().coverage

        self.assertEqual(coverage.unreadable, 0)
        self.assertIsNone(coverage.history_uncovered_from)
        self.assertFalse(coverage.history_checked)
        self.assertFalse(coverage.complete)

    def test_history_older_than_the_evidence_makes_it_incomplete(self):
        from datetime import date

        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 12)

        model = self.model().with_history_coverage(date(2026, 8, 1))

        self.assertEqual(model.coverage.history_uncovered_from, "2026-08-01")
        self.assertFalse(model.coverage.complete)
        self.assertEqual(model.coverage.evidence_from, "2026-08-12")

    def test_asking_and_finding_no_gap_is_not_the_same_as_never_asking(self):
        """The name was right and the body was wrong.

        It asserted the two payloads were **equal**, with a docstring
        rationalising it ("Both leave `history_uncovered_from` None — and
        that is fine"). They were equal, and that was the defect: the
        distinction this test is named after had nowhere to live, so
        `complete` answered `True` for a model on which nobody had asked the
        Company-History question at all.

        `coverage.history_checked` is where it lives now. The name is true.
        """
        self.put("E1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        before = self.model()

        after = before.with_history_coverage(None)

        self.assertNotEqual(before.to_payload(), after.to_payload())
        self.assertFalse(before.coverage.history_checked)
        self.assertTrue(after.coverage.history_checked)
        # Neither has a gap — the difference is only whether anybody looked.
        self.assertIsNone(before.coverage.history_uncovered_from)
        self.assertIsNone(after.coverage.history_uncovered_from)
        self.assertFalse(before.coverage.complete)
        self.assertTrue(after.coverage.complete)

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
                # C50: folded duplicates are reported, never dropped in
                # silence. Zero here because this fixture has none.
                "duplicates": 0,
                # C56: whether anybody asked the Company-History question.
                # True here because the fixture calls
                # `with_history_coverage()`, which is what `ops_status.py`
                # does on every run.
                "history_checked": True,
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
                    # Three inputs now. An unchecked model is never complete,
                    # however clean — so the torn/clean property is asserted
                    # on the checked one, and the unchecked one is asserted
                    # to be false either way.
                    self.assertFalse(model.coverage.complete)
                    model = model.with_history_coverage(None)
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
