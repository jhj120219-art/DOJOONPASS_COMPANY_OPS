"""docs/04_NOTION_SYNC_SPEC.md §8 — Notion Database Auto Bootstrap.

Uses InMemoryNotionTransport exclusively — no real Notion API call.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notion import (  # noqa: E402
    TARGET_PROPERTIES,
    InMemoryNotionTransport,
    NotionAPIError,
    NotionClient,
    PropertyOutcome,
    bootstrap_database,
    format_report,
)


class BootstrapFreshDatabaseTests(unittest.TestCase):
    """A brand-new Notion database always starts with exactly one Title
    property, default-named "Name". V1.1 (CEO/COO policy change, this
    Sprint): the Title property is the single exception where Bootstrap
    auto-renames an existing property — every other Property is still
    only ever created, never modified."""

    def _make_client(self):
        transport = InMemoryNotionTransport(initial_properties={"Name": {"type": "title", "title": {}}})
        return NotionClient(transport=transport, database_id="DB-1"), transport

    def test_all_missing_properties_are_created_and_title_is_renamed(self):
        client, transport = self._make_client()

        result = bootstrap_database(client)

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(len(result.reports), len(TARGET_PROPERTIES))

        # Title Property ("Name") gets auto-renamed to "Project" (V1.1).
        self.assertEqual(by_name["Project"].outcome, PropertyOutcome.RENAMED)
        self.assertIn("Name", by_name["Project"].detail)
        self.assertIn("Project", by_name["Project"].detail)

        # every other target Property was missing -> CREATED
        for name in TARGET_PROPERTIES:
            if name == "Project":
                continue
            self.assertEqual(by_name[name].outcome, PropertyOutcome.CREATED, name)

        # actually landed in the (simulated) Database schema
        schema = client.get_database_schema()
        for name in TARGET_PROPERTIES:
            self.assertIn(name, schema)

        # "Name" no longer exists as a key -- it was renamed, not duplicated
        self.assertNotIn("Name", schema)
        self.assertEqual(schema["Project"]["type"], "title")

    def test_status_and_last_event_type_created_as_select(self):
        client, _ = self._make_client()
        bootstrap_database(client)

        schema = client.get_database_schema()
        self.assertEqual(schema["Status"], {"select": {}})
        self.assertEqual(schema["Last Event Type"], {"select": {}})


class BootstrapProjectAlreadyNamedTests(unittest.TestCase):
    """작업 6, 시나리오 2: 이미 Project인 경우 Skip (아무 작업도 하지 않는다)."""

    def test_title_already_named_project_is_skipped_not_renamed(self):
        transport = InMemoryNotionTransport(initial_properties={"Project": {"type": "title", "title": {}}})
        client = NotionClient(transport=transport, database_id="DB-1")

        result = bootstrap_database(client)

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(by_name["Project"].outcome, PropertyOutcome.SKIPPED)
        self.assertIsNone(by_name["Project"].detail)
        for name in TARGET_PROPERTIES:
            if name == "Project":
                continue
            self.assertEqual(by_name[name].outcome, PropertyOutcome.CREATED, name)

        # no rename call side-effect: schema unchanged for the Title entry
        schema = client.get_database_schema()
        self.assertEqual(schema["Project"], {"type": "title", "title": {}})


class BootstrapTitleRenameCollisionTests(unittest.TestCase):
    """The Title property can be named anything before Bootstrap renames it
    to "Project" (V1.1). If that original name collides with one of §8's
    other Target Properties (e.g. a Title literally named "Status"), the
    rename consumes that name — the real "Status" Select property still
    does not exist afterwards, and must still be created."""

    def test_a_target_property_name_freed_by_the_rename_is_still_created(self):
        transport = InMemoryNotionTransport(initial_properties={"Status": {"type": "title", "title": {}}})
        client = NotionClient(transport=transport, database_id="DB-1")

        result = bootstrap_database(client)

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(by_name["Project"].outcome, PropertyOutcome.RENAMED)
        self.assertEqual(by_name["Status"].outcome, PropertyOutcome.CREATED)

        schema = client.get_database_schema()
        self.assertEqual(schema["Project"]["type"], "title")
        self.assertEqual(schema["Status"], {"select": {}})


class SchemaWithNoTitleIsReportedNotCrashedIntoTests(unittest.TestCase):
    """C49: found by branch coverage — three guards in this module had never
    been executed.

    Notion enforces exactly one Title property on every database, which is
    why `_bootstrap_title_property()`'s own docstring calls the no-Title case
    "이론상 발생하지 않음". It is still the case that decides what happens when
    the schema this code reads back is *not* what Notion enforces — a partial
    response, a hand-built payload, a stubbed client — and "defensive" is
    only true if the defence works.

    Measured before these tests existed: `_has_title_property()`'s
    `return False, None` and the `if not title_exists:` branch above it were
    dead in the suite, so nothing said whether the FAILED report they promise
    is actually produced or whether the code raises on the way there.
    """

    def test_a_schema_with_no_title_property_is_recognised(self):
        from notion.bootstrap import _has_title_property

        found, name = _has_title_property(
            {"Status": {"type": "select"}, "Notes": {"type": "rich_text"}}
        )

        self.assertFalse(found)
        self.assertIsNone(name)

    def test_a_schema_with_a_title_names_it(self):
        """The other side, so the check above cannot pass by always failing."""
        from notion.bootstrap import _has_title_property

        found, name = _has_title_property(
            {"Name": {"type": "title"}, "Status": {"type": "select"}}
        )

        self.assertTrue(found)
        self.assertEqual(name, "Name")

    def test_no_title_produces_a_failed_report_rather_than_an_exception(self):
        """The promise the docstring makes: absorbed, classified, and the
        reason said out loud — never a raise that would take a bootstrap run
        down at the one step an operator cannot repeat safely."""
        from notion.bootstrap import PropertyOutcome, _bootstrap_title_property

        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="DB-1")

        report = _bootstrap_title_property(
            client, {"Status": {"type": "select"}}, title_property="Run ID"
        )

        self.assertEqual(report.name, "Run ID")
        self.assertIs(report.outcome, PropertyOutcome.FAILED)
        self.assertIn("no Title property", report.detail)

    def test_nothing_was_written_to_the_database(self):
        """A FAILED report must not have half-applied something first."""
        from notion.bootstrap import _bootstrap_title_property

        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="DB-1")
        before = dict(transport.retrieve_database("DB-1").get("properties", {}))

        _bootstrap_title_property(client, {"Status": {"type": "select"}})

        self.assertEqual(
            dict(transport.retrieve_database("DB-1").get("properties", {})), before
        )


class FormatReportHandlesAnEmptyResultTests(unittest.TestCase):
    """The third never-executed guard. `format_report()` computes a column
    width with `max()` over the reports, which raises `ValueError` on an
    empty sequence — so the early return is not decoration, it is the only
    thing standing between an empty result and a crash in the function whose
    whole job is printing one."""

    def test_an_empty_result_formats_to_nothing(self):
        from notion.bootstrap import BootstrapResult, format_report

        self.assertEqual(format_report(BootstrapResult(reports=())), "")

    def test_a_non_empty_result_still_formats(self):
        from notion.bootstrap import (
            BootstrapResult,
            PropertyBootstrapReport,
            PropertyOutcome,
            format_report,
        )

        text = format_report(
            BootstrapResult(
                reports=(
                    PropertyBootstrapReport("Run ID", PropertyOutcome.EXISTS),
                    PropertyBootstrapReport("Accepted", PropertyOutcome.CREATED),
                )
            )
        )

        self.assertIn("Run ID", text)
        self.assertIn("EXISTS", text)
        self.assertIn("Accepted", text)
        self.assertIn("CREATED", text)


class BootstrapTitleRenameFailureTests(unittest.TestCase):
    """작업 6, 시나리오 3: Rename 실패 시 Runtime 중단 없이 Error 반환."""

    def test_rename_api_failure_returns_failed_outcome_without_raising(self):
        transport = InMemoryNotionTransport(initial_properties={"Name": {"type": "title", "title": {}}})
        transport.fail_next_method = "update_database"  # only the rename call fails; retrieve_database succeeds
        client = NotionClient(transport=transport, database_id="DB-1")

        try:
            result = bootstrap_database(client)
        except NotionAPIError:
            self.fail("bootstrap_database() must not let a Title rename NotionAPIError propagate")

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(by_name["Project"].outcome, PropertyOutcome.FAILED)
        self.assertIsNotNone(by_name["Project"].detail)

        # the rest of Bootstrap still ran normally despite the Title failure
        for name in TARGET_PROPERTIES:
            if name == "Project":
                continue
            self.assertEqual(by_name[name].outcome, PropertyOutcome.CREATED, name)

        # Title Property itself was left exactly as it was (rename never took effect)
        schema = client.get_database_schema()
        self.assertEqual(schema["Name"], {"type": "title", "title": {}})
        self.assertNotIn("Project", schema)


class BootstrapExistingPropertiesNeverModifiedTests(unittest.TestCase):
    def test_existing_property_definition_is_left_byte_for_byte_unchanged(self):
        # a hand-made "Owner" Select with real options already configured —
        # bootstrap must never touch it, even though TARGET_PROPERTIES'
        # payload for "Owner" is a bare {"select": {}}.
        existing_owner = {
            "type": "select",
            "select": {
                "options": [
                    {"name": "CTO Backend", "color": "blue"},
                    {"name": "COO", "color": "green"},
                ]
            },
        }
        transport = InMemoryNotionTransport(
            initial_properties={
                "Project": {"type": "title", "title": {}},
                "Owner": existing_owner,
            }
        )
        client = NotionClient(transport=transport, database_id="DB-1")

        result = bootstrap_database(client)

        by_name = {r.name: r for r in result.reports}
        self.assertEqual(by_name["Owner"].outcome, PropertyOutcome.EXISTS)

        schema = client.get_database_schema()
        self.assertEqual(schema["Owner"], existing_owner)


class BootstrapIdempotencyTests(unittest.TestCase):
    """검증: 동일한 초기화를 2회 연속 실행 -> 두 번째 실행에서는 변경사항이 없어야 한다."""

    def test_second_run_creates_nothing(self):
        transport = InMemoryNotionTransport(initial_properties={"Name": {"type": "title", "title": {}}})
        client = NotionClient(transport=transport, database_id="DB-1")

        first = bootstrap_database(client)
        schema_after_first = dict(client.get_database_schema())

        second = bootstrap_database(client)
        schema_after_second = dict(client.get_database_schema())

        first_by_name = {r.name: r for r in first.reports}
        self.assertEqual(first_by_name["Project"].outcome, PropertyOutcome.RENAMED)
        self.assertGreater(len(first.created), 0)

        self.assertEqual(second.created, ())
        self.assertEqual(second.renamed, ())
        # every non-Title target Property is now EXISTS; Project (now
        # correctly named after run 1) is SKIPPED on run 2 -- not renamed again.
        second_by_name = {r.name: r for r in second.reports}
        for name in TARGET_PROPERTIES:
            if name == "Project":
                self.assertEqual(second_by_name[name].outcome, PropertyOutcome.SKIPPED)
            else:
                self.assertEqual(second_by_name[name].outcome, PropertyOutcome.EXISTS, name)

        # schema is byte-for-byte identical across both runs (no drift)
        self.assertEqual(schema_after_first, schema_after_second)


class FormatReportTests(unittest.TestCase):
    def test_format_report_lists_every_property_in_spec_order(self):
        transport = InMemoryNotionTransport(initial_properties={"Project": {"type": "title", "title": {}}})
        client = NotionClient(transport=transport, database_id="DB-1")
        result = bootstrap_database(client)

        text = format_report(result)
        lines = text.splitlines()

        self.assertEqual(len(lines), len(TARGET_PROPERTIES))
        self.assertTrue(lines[0].startswith("Project "))
        self.assertIn("SKIPPED", lines[0])
        self.assertTrue(lines[1].startswith("Project ID "))
        self.assertIn("CREATED", lines[1])


class TheSetupCommandSaysWhetherTheBootstrapTookTests(unittest.TestCase):
    """`init_notion.py`'s exit code (C117).

    The defect. `main()` printed its summary line —

        EXISTS=10 CREATED=0 RENAMED=0 SKIPPED=0 FAILED=1

    — and returned **0** for any `FAILED`. The one Property that can reach
    `FAILED` without raising is the Title:
    `BootstrapTitleRenameFailureTests` above pins the state where the rename
    `"Name" -> "Project"` is refused and the Database is left with no
    `Project` property at all.

    That is not cosmetic. `notion/properties.py` writes every PROJECTS row as
    `{"Project": _title(...)}`, so the Database this leaves behind fails
    **every** later Notion Sync. And this module's own docstring records why
    the rename is automated in the first place: the manual step "was
    attempted twice by a human operator and did not take effect either time".
    Exiting 0 told the operator the automated attempt had worked, and sent
    them to the next line of docs/13 on top of the same broken state.

    Driven through `main()` because that is where the code lived. Nothing
    reaches Notion — `RealNotionTransport` is replaced by the in-memory one
    every other test in this file uses.
    """

    def _run(self, transport):
        import contextlib
        import importlib
        import io
        import os

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        init_notion = importlib.import_module("init_notion")
        original_factory = init_notion.RealNotionTransport
        original_environ = dict(os.environ)
        out, err = io.StringIO(), io.StringIO()
        try:
            init_notion.RealNotionTransport = lambda **kwargs: transport
            os.environ["NOTION_API_TOKEN"] = "ntn_" + "testtokenvalue0000"
            os.environ["NOTION_PROJECTS_DATABASE_ID"] = "DB-1"
            os.environ.pop("NOTION_OPS_RUNS_DATABASE_ID", None)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = init_notion.main(("init_notion.py",))
        finally:
            init_notion.RealNotionTransport = original_factory
            os.environ.clear()
            os.environ.update(original_environ)
        return code, out.getvalue(), err.getvalue(), init_notion

    def test_a_refused_title_rename_does_not_report_success(self):
        transport = InMemoryNotionTransport(
            initial_properties={"Name": {"type": "title", "title": {}}}
        )
        transport.fail_next_method = "update_database"

        code, out, err, init_notion = self._run(transport)

        self.assertIn("FAILED=1", out)
        self.assertEqual(
            code,
            init_notion.DEGRADED_EXIT,
            "the Title rename was refused and the command reported success",
        )
        self.assertIn("DEGRADED", err)
        self.assertIn("Project", err)

    def test_a_clean_bootstrap_still_reports_success(self):
        """The antecedent. Without it a `main()` that returned 3 whatever
        happened would satisfy the assertion above."""
        transport = InMemoryNotionTransport(
            initial_properties={"Name": {"type": "title", "title": {}}}
        )

        code, out, err, _ = self._run(transport)

        self.assertIn("FAILED=0", out)
        self.assertEqual(code, 0, err)
        self.assertNotIn("DEGRADED", err)

    def test_a_title_already_named_project_is_not_a_failure(self):
        """`SKIPPED` means there was nothing to rename — the good outcome,
        and the one a second run of this idempotent command produces."""
        transport = InMemoryNotionTransport(
            initial_properties={"Project": {"type": "title", "title": {}}}
        )

        code, out, err, _ = self._run(transport)

        self.assertIn("SKIPPED=1", out)
        self.assertIn("FAILED=0", out)
        self.assertEqual(code, 0, err)

    def test_the_exit_code_is_the_one_the_other_entrypoints_spend(self):
        """docs/14 §4: "`3`은 `ops_status.py`의 기존 '사람이 확인해야 함'과
        같은 뜻이다 — 두 진입점이 같은 숫자로 같은 말을 한다." A fourth
        meaning for the same number would undo that."""
        import importlib

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        init_notion = importlib.import_module("init_notion")

        self.assertEqual(init_notion.DEGRADED_EXIT, 3)
        self.assertEqual(init_notion.CONFIG_ERROR_EXIT, 1)
        self.assertNotEqual(init_notion.DEGRADED_EXIT, init_notion.CONFIG_ERROR_EXIT)


if __name__ == "__main__":
    unittest.main()
