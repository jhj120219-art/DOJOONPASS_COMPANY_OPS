"""Dashboard Model -> Notion projection tests (C49).

`controltower/projection.py` is the last link of the chain the request
states:

    Desktop -> Agent -> Execution Evidence -> Company Ops
    -> Control Tower Rollup -> Dashboard Model -> Notion Payload

and its whole reason to exist is that the link used to be a fork. C48 made
one fold feed both the screen and the `OPS_RUNS` row, but the row's
*representation* was still assembled in `app/runner.py` out of the rollup,
while the screen was assembled from the Dashboard Model. Two arrangements of
one fold, kept in step by a test comparing them afterwards.

Three properties matter more than the strings it returns:

    the row is the model
        not "equal to the model when checked" — derived from it, so a panel
        that changes shape takes the row with it

    it invents no column
        every key maps to a property `notion/dashboard.DASHBOARD_DATABASES`
        actually declares, or the first real run is an HTTP 400

    it fits what Notion accepts
        `Desktops Reporting` is rich_text and grows with `events.SOURCES`
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controltower import (  # noqa: E402
    OPS_RUNS_CONTROL_TOWER_COLUMNS,
    build_company_rollup,
    build_dashboard,
    ops_runs_fields,
)
from controltower.projection import RICH_TEXT_LIMIT  # noqa: E402
from events import create_event  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=KST)

SOURCE_FOR_ROLE = {
    "CTO_BACKEND": "DESKTOP_1",
    "CMO": "DESKTOP_2",
    "CTO_FRONTEND": "DESKTOP_3",
    "COO": "DESKTOP_4",
}


class ProjectionTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.processed = Path(tmp.name) / "processed"
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

    def model(self):
        return build_dashboard(
            build_company_rollup(processed_dir=self.processed, now=NOW), now=NOW
        )

    def fields(self):
        return ops_runs_fields(self.model())


class ContractedColumnsExistTests(unittest.TestCase):
    """Every column this projection writes is one the schema declares.

    `DashboardSchemaMappingTests` already holds `record_run()`'s whole
    payload to `DASHBOARD_DATABASES[OPS_RUNS]`. This is the narrower
    statement for the two columns the Control Tower owns, and it is worth
    stating separately because they are the ones most likely to grow: they
    are the only columns on that row that come from a *derivation* rather
    than from a pipeline step's own counter.
    """

    def test_every_projected_column_is_in_the_ops_runs_schema(self):
        from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

        schema = DASHBOARD_DATABASES[OPS_RUNS]
        for keyword, column in sorted(OPS_RUNS_CONTROL_TOWER_COLUMNS.items()):
            with self.subTest(column=column):
                self.assertIn(column, schema, f"{keyword} -> {column}")

    def test_the_column_types_are_what_the_values_are(self):
        """A number into a rich_text column, or the reverse, is an HTTP 400
        on the first real run — and nothing before that would say so.

        The schema is read here rather than through an accessor on the
        projection: two modules each answering for their own half, asked once
        each, is what keeps the mapping checkable without a third place to
        keep in step."""
        from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

        schema = DASHBOARD_DATABASES[OPS_RUNS]
        expected = {"Desktops Reporting": "rich_text", "Role Mismatches": "number"}

        for column in OPS_RUNS_CONTROL_TOWER_COLUMNS.values():
            with self.subTest(column=column):
                self.assertEqual(next(iter(schema[column])), expected[column])

    def test_every_keyword_is_one_record_run_accepts(self):
        """The other half of the mapping: the keys are `record_run()`'s
        parameters, and a rename there would otherwise surface as a
        TypeError at the end of a production run."""
        import inspect

        from notion.dashboard import record_run

        parameters = inspect.signature(record_run).parameters
        for keyword in sorted(OPS_RUNS_CONTROL_TOWER_COLUMNS):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, parameters)

    def test_the_projection_fills_exactly_the_columns_it_declares(self):
        model = build_dashboard(build_company_rollup(events=[], now=NOW), now=NOW)

        self.assertEqual(
            set(ops_runs_fields(model)), set(OPS_RUNS_CONTROL_TOWER_COLUMNS)
        )


class TheRowIsTheModelTests(ProjectionTestCase):
    """Derived from the panels, not compared with them afterwards."""

    def test_the_string_names_every_desktop_that_reported(self):
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put("A2", "PAY", "CTO_BACKEND", "DECISION_APPROVED", "IN_PROGRESS", 6)
        self.put("B1", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 5)
        self.put("D1", "OPSX", "COO", "STARTED", "IN_PROGRESS", 5)

        self.assertEqual(
            self.fields()["desktops_reporting"],
            "DESKTOP_1:2 DESKTOP_2:1 DESKTOP_4:1",
        )

    def test_each_count_is_the_panels_own(self):
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        self.put("B1", "BRAND", "CMO", "STARTED", "IN_PROGRESS", 5)
        model = self.model()

        counted = dict(
            part.split(":") for part in ops_runs_fields(model)["desktops_reporting"].split()
        )

        for row in model.panel("DESKTOPS").rows:
            with self.subTest(desktop=row.key):
                self.assertEqual(
                    int(counted.get(row.key, 0)), row.values["events"]
                )

    def test_a_silent_desktop_is_absent_rather_than_zero(self):
        """The panel keeps it present-and-empty because a status view must
        not confuse "no activity" with "not counted"; a per-run row is a
        different sentence and a Desktop that sent nothing did not report."""
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        model = self.model()

        self.assertEqual(len(model.panel("DESKTOPS").rows), 4)
        self.assertEqual(ops_runs_fields(model)["desktops_reporting"], "DESKTOP_1:1")

    def test_an_empty_run_writes_an_empty_string_not_a_missing_column(self):
        self.assertEqual(self.fields()["desktops_reporting"], "")
        self.assertEqual(self.fields()["role_mismatches"], 0)

    def test_the_string_is_sorted_by_source_not_by_the_panels_order(self):
        """The panel orders by docs/02 §8's role table (DESKTOP_1, _3, _2,
        _4); the row orders by name, so a diff between two rows means a
        difference in the work rather than a difference in presentation."""
        for desktop, role in (
            ("DESKTOP_4", "COO"),
            ("DESKTOP_2", "CMO"),
            ("DESKTOP_1", "CTO_BACKEND"),
            ("DESKTOP_3", "CTO_FRONTEND"),
        ):
            self.put(f"E-{desktop}", "PAY", role, "STARTED", "IN_PROGRESS", 5)
        model = self.model()

        panel_order = [row.key for row in model.panel("DESKTOPS").rows]
        row_order = [
            part.split(":")[0]
            for part in ops_runs_fields(model)["desktops_reporting"].split()
        ]

        self.assertEqual(panel_order, ["DESKTOP_1", "DESKTOP_3", "DESKTOP_2", "DESKTOP_4"])
        self.assertEqual(row_order, sorted(row_order))

    def test_the_same_evidence_always_renders_the_same_string(self):
        for index in range(6):
            self.put(f"E{index}", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", index + 1)

        self.assertEqual(self.fields(), self.fields())


class TheTwoPanelsAgreeAboutMismatchesTests(ProjectionTestCase):
    """`role_mismatches` is counted off DESKTOPS, and RISKS carries the same
    Events as `ROLE_MISMATCH` rows.

    Choosing one of two sources for a number is fine; letting the choice
    become a difference is not. DESKTOPS is the right one here — it counts by
    the Desktop that **sent** the Event, which is the partition the column is
    about — and this holds RISKS to the same total so the alternative can
    never quietly disagree.
    """

    def _mismatch(self, event_id, source, role):
        self.put(event_id, "PAY", role, "STARTED", "IN_PROGRESS", 5, source=source)

    def test_one_mismatch_is_one_on_both_panels(self):
        self._mismatch("X1", "DESKTOP_1", "CMO")
        model = self.model()

        self.assertEqual(ops_runs_fields(model)["role_mismatches"], 1)
        self.assertEqual(
            len([
                row for row in model.panel("RISKS").rows
                if row.values["kind"] == "ROLE_MISMATCH"
            ]),
            1,
        )

    def test_several_mismatches_across_desktops_agree(self):
        self._mismatch("X1", "DESKTOP_1", "CMO")
        self._mismatch("X2", "DESKTOP_2", "COO")
        self._mismatch("X3", "DESKTOP_2", "CTO_BACKEND")
        self.put("OK1", "PAY", "CTO_FRONTEND", "STARTED", "IN_PROGRESS", 5)
        model = self.model()

        from_risks = len([
            row for row in model.panel("RISKS").rows
            if row.values["kind"] == "ROLE_MISMATCH"
        ])

        self.assertEqual(ops_runs_fields(model)["role_mismatches"], 3)
        self.assertEqual(from_risks, 3)

    def test_a_conforming_run_is_zero_on_both(self):
        self.put("OK1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)
        model = self.model()

        self.assertEqual(ops_runs_fields(model)["role_mismatches"], 0)
        self.assertEqual(
            [row for row in model.panel("RISKS").rows if row.values["kind"] == "ROLE_MISMATCH"],
            [],
        )

    def test_a_mismatched_event_still_counts_under_the_desktop_that_sent_it(self):
        """The column would be meaningless if the Event were attributed to
        the role it claims — that is the mixing this whole layer exists to
        make visible."""
        self._mismatch("X1", "DESKTOP_1", "CMO")

        self.assertEqual(self.fields()["desktops_reporting"], "DESKTOP_1:1")


class TheValueFitsWhatNotionAcceptsTests(ProjectionTestCase):
    """`Desktops Reporting` is `rich_text`, and it grows with `events.SOURCES`.

    Four Desktops is about fifty characters today. The column exists as one
    rich_text rather than one number per Desktop precisely because that set
    is a schema value that can grow (C47), so the bound is the thing that
    keeps a growing set from turning into a rejected row.
    """

    def test_todays_value_is_nowhere_near_the_limit(self):
        for desktop, role in SOURCE_FOR_ROLE.items():
            self.put(f"E-{desktop}", "PAY", desktop, "STARTED", "IN_PROGRESS", 5)

        self.assertLess(len(self.fields()["desktops_reporting"]), 100)

    def test_a_value_over_the_limit_is_cut_and_says_so(self):
        """Driven rather than reasoned: a hand-built model with enough rows
        to pass the bound."""
        from controltower.dashboard import DashboardPanel, DashboardRow, PanelStatus
        from controltower.dashboard import DashboardModel

        rows = tuple(
            DashboardRow(
                key=f"DESKTOP_{index:04d}",
                # Every declared column, because
                # `EveryRowFillsTheColumnsItsPanelDeclaresTests` holds a real
                # model to exactly that — a fixture that is looser than the
                # thing it stands in for tests the wrong object.
                values={"events": 7, "role_mismatches": 0},
            )
            for index in range(400)
        )
        model = DashboardModel(
            generated_at=NOW.isoformat(),
            panels=(
                DashboardPanel(
                    key="DESKTOPS",
                    title="Desktop",
                    status=PanelStatus.SOURCED,
                    columns=("events", "role_mismatches"),
                    rows=rows,
                ),
            ),
        )

        value = ops_runs_fields(model)["desktops_reporting"]

        self.assertEqual(len(value), RICH_TEXT_LIMIT)
        self.assertTrue(value.endswith("…"), value[-20:])

    def test_the_cut_is_visible_rather_than_silent(self):
        """A truncated value that looks complete would make "which Desktops
        reported" a false statement rather than a short one."""
        from controltower.dashboard import DashboardModel, DashboardPanel, DashboardRow
        from controltower.dashboard import PanelStatus

        model = DashboardModel(
            generated_at=NOW.isoformat(),
            panels=(
                DashboardPanel(
                    key="DESKTOPS",
                    title="Desktop",
                    status=PanelStatus.SOURCED,
                    columns=("events", "role_mismatches"),
                    rows=tuple(
                        DashboardRow(
                            key=f"D{index:05d}",
                            values={"events": 1, "role_mismatches": 0},
                        )
                        for index in range(500)
                    ),
                ),
            ),
        )

        self.assertNotIn("…", ops_runs_fields(model)["desktops_reporting"][:-1])

    def test_the_number_stays_a_number(self):
        """`Role Mismatches` is a Notion `number`; a string would be a 400."""
        self.put("X1", "PAY", "CMO", "STARTED", "IN_PROGRESS", 5, source="DESKTOP_1")

        self.assertIsInstance(self.fields()["role_mismatches"], int)
        self.assertNotIsInstance(self.fields()["role_mismatches"], bool)

    def test_the_projection_is_json_serialisable(self):
        """Whatever writes it eventually goes over HTTP."""
        self.put("A1", "PAY", "CTO_BACKEND", "STARTED", "IN_PROGRESS", 5)

        self.assertEqual(json.loads(json.dumps(self.fields())), self.fields())


class AModelWithoutADesktopsPanelIsAnsweredTests(unittest.TestCase):
    """Never raises, for `rollup.py`'s own reason: a derivation over evidence
    a pipeline wrote is read while things are going wrong."""

    def test_a_model_with_no_panels_projects_an_empty_row(self):
        from controltower.dashboard import DashboardModel

        fields = ops_runs_fields(DashboardModel(generated_at=NOW.isoformat()))

        self.assertEqual(fields["desktops_reporting"], "")
        self.assertEqual(fields["role_mismatches"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
