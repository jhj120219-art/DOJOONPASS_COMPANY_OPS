"""RealNotionTransport failure conversion, against a real local HTTP server.

`RealNotionTransport` is the only module in this project that talks to a
network, and it was the only one with no tests at all — the whole Notion
suite runs on `InMemoryNotionTransport`, which by construction can never
exercise urllib's error paths.

That gap hid four defects, each found by pointing the transport at a socket
on 127.0.0.1 and watching what came out:

    read timeout          -> leaked TimeoutError    (urllib does not wrap it)
    non-JSON 200 body     -> leaked JSONDecodeError
    undecodable 200 body  -> leaked UnicodeDecodeError
    HTTP 4xx              -> discarded Notion's own explanation

The first three matter because every caller is written against
`NotionAPIError`: `ExecutionPlanSync.sync()` converts it to
NOTION_RETRY_REQUIRED, and `notion/bootstrap.py` and `notion/dashboard.py`
catch it too. An exception of a type they do not expect bypasses that
classification. A read timeout is the most likely real-world failure of the
lot, and it was the one that leaked.

The fourth matters because a permanent 4xx is re-sent by the retry queue on
every subsequent run (a known, separately recorded gap). "Bad Request" tells
an operator nothing; Notion's body names the offending property.

No network access: every case is served by `http.server` on an ephemeral
localhost port, or by a raw socket that accepts and never answers.
"""

import contextlib
import io
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notion.transport import NotionAPIError, RealNotionTransport  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    """Base for the canned responses below. Silences the access log so the
    test output stays readable."""

    status = 200
    body = b"{}"
    content_type = "application/json"

    def _respond(self):
        self.send_response(self.status)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    do_GET = _respond
    do_POST = _respond
    do_PATCH = _respond

    def log_message(self, *args):
        pass


class LocalServerTestCase(unittest.TestCase):
    def serve(self, handler_cls) -> RealNotionTransport:
        server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return RealNotionTransport(
            api_token="not-a-real-token",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            timeout=5.0,
        )


class SuccessPathTests(LocalServerTestCase):
    def test_a_json_response_is_returned_as_a_mapping(self):
        class Ok(_Handler):
            body = b'{"object":"database","id":"db-1"}'

        transport = self.serve(Ok)

        self.assertEqual(
            transport.retrieve_database("db-1"), {"object": "database", "id": "db-1"}
        )

    def test_the_token_is_sent_as_a_bearer_header_and_not_in_the_url(self):
        """The credential must never reach a URL, where it would land in
        proxy logs and server access logs."""
        seen = {}

        class Capture(_Handler):
            body = b"{}"

            def _respond(self):
                seen["path"] = self.path
                seen["auth"] = self.headers.get("Authorization")
                seen["version"] = self.headers.get("Notion-Version")
                _Handler._respond(self)

            do_GET = _respond

        transport = self.serve(Capture)
        transport.retrieve_database("db-1")

        self.assertEqual(seen["auth"], "Bearer not-a-real-token")
        self.assertNotIn("not-a-real-token", seen["path"])
        self.assertEqual(seen["version"], "2022-06-28")


class FailureConversionTests(LocalServerTestCase):
    """Every network failure must arrive as NotionAPIError — that is the
    only exception type any caller is written against."""

    def test_an_http_error_becomes_a_notion_api_error_with_its_status(self):
        class Rejected(_Handler):
            status = 400
            body = b'{"object":"error","code":"validation_error","message":"X is not a valid property"}'

        transport = self.serve(Rejected)

        with self.assertRaises(NotionAPIError) as ctx:
            transport.retrieve_database("db-1")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_the_notion_explanation_survives_into_the_error(self):
        """"Bad Request" is not actionable; the body names the property."""

        class Rejected(_Handler):
            status = 400
            body = b'{"object":"error","code":"validation_error","message":"X is not a valid property"}'

        transport = self.serve(Rejected)

        with self.assertRaises(NotionAPIError) as ctx:
            transport.retrieve_database("db-1")
        self.assertIn("X is not a valid property", str(ctx.exception))

    def test_an_enormous_error_body_is_truncated(self):
        """A proxy's HTML error page must not be pasted whole into a console
        or a SyncResult."""

        class Huge(_Handler):
            status = 502
            content_type = "text/html"
            body = b"<html>" + b"x" * 50_000 + b"</html>"

        transport = self.serve(Huge)

        with self.assertRaises(NotionAPIError) as ctx:
            transport.retrieve_database("db-1")
        self.assertLess(len(str(ctx.exception)), 1_000)
        self.assertIn("...", str(ctx.exception))

    def test_an_empty_error_body_adds_no_separator(self):
        class Bare(_Handler):
            status = 500
            body = b""

        transport = self.serve(Bare)

        with self.assertRaises(NotionAPIError) as ctx:
            transport.retrieve_database("db-1")
        self.assertNotIn("|", str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 500)

    def test_a_non_json_two_hundred_becomes_a_notion_api_error(self):
        class Html(_Handler):
            content_type = "text/html"
            body = b"<html><body>captive portal</body></html>"

        transport = self.serve(Html)

        with self.assertRaises(NotionAPIError) as ctx:
            transport.retrieve_database("db-1")
        self.assertIn("not JSON", str(ctx.exception))

    def test_an_undecodable_body_becomes_a_notion_api_error(self):
        class Garbage(_Handler):
            body = b"\xff\xfe not utf-8 at all"

        transport = self.serve(Garbage)

        with self.assertRaises(NotionAPIError):
            transport.retrieve_database("db-1")

    def test_a_refused_connection_becomes_a_notion_api_error(self):
        # Bind and immediately close, so the port is almost certainly free.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        transport = RealNotionTransport(
            api_token="not-a-real-token",
            base_url=f"http://127.0.0.1:{port}",
            timeout=2.0,
        )

        with self.assertRaises(NotionAPIError):
            transport.retrieve_database("db-1")

    def test_a_read_timeout_becomes_a_notion_api_error(self):
        """urllib does NOT wrap a read timeout in URLError — it escapes as a
        bare TimeoutError. This is the most likely real failure of all."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]

        def accept_and_stall():
            try:
                conn, _ = listener.accept()
                time.sleep(5)
                conn.close()
            except OSError:
                pass

        threading.Thread(target=accept_and_stall, daemon=True).start()

        transport = RealNotionTransport(
            api_token="not-a-real-token",
            base_url=f"http://127.0.0.1:{port}",
            timeout=0.5,
        )

        with self.assertRaises(NotionAPIError) as ctx:
            transport.retrieve_database("db-1")
        self.assertIn("timed out", str(ctx.exception))

    def test_no_failure_message_ever_contains_the_token(self):
        """Every error string reaches a console and a SyncResult. None of
        them may carry the credential."""
        token = "ntn_" + "SUPERSECRETTOKENVALUE"

        class Rejected(_Handler):
            status = 401
            body = b'{"object":"error","code":"unauthorized","message":"API token is invalid."}'

        server = HTTPServer(("127.0.0.1", 0), Rejected)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)

        transport = RealNotionTransport(
            api_token=token,
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            timeout=5.0,
        )

        with self.assertRaises(NotionAPIError) as ctx:
            transport.retrieve_database("db-1")
        self.assertNotIn(token, str(ctx.exception))
        self.assertNotIn(token, repr(ctx.exception))


class CallerContractTests(LocalServerTestCase):
    """The point of the conversion: callers classify by NotionAPIError, so a
    leaked exception type silently bypasses their handling."""

    def test_a_timeout_is_classified_as_retry_required_by_the_sync_layer(self):
        from notion import ExecutionPlanSync, NotionClient, SyncStatus
        from reporter import Reporter

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]

        def accept_and_stall():
            try:
                conn, _ = listener.accept()
                time.sleep(5)
                conn.close()
            except OSError:
                pass

        threading.Thread(target=accept_and_stall, daemon=True).start()

        transport = RealNotionTransport(
            api_token="not-a-real-token",
            base_url=f"http://127.0.0.1:{port}",
            timeout=0.5,
        )
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )
        event = Reporter(profile="DESKTOP_1").report(
            project_id="P",
            event_type="COMPLETED",
            status="COMPLETED",
            summary="a timeout must be retried, not dropped",
            history_candidate=True,
            event_id="TIMEOUT-001",
            timestamp="2026-08-08T10:00:00+09:00",
        )

        result = sync.sync(event)

        self.assertEqual(result.status, SyncStatus.NOTION_RETRY_REQUIRED)


class BlankConfigurationTests(unittest.TestCase):
    """A configuration value that looks set but is blank.

    `""` was already rejected; `"   "` was not, so a trailing space after
    `=` in a hand-written `.env` produced a token Notion answers 401 to.
    That 401 became NOTION_RETRY_REQUIRED and queued every Event forever,
    because nothing caps that retry — the worst place for an invisible typo
    to land.
    """

    BLANK = ("   ", "\t", "\n", " \t\n ")

    def test_a_blank_required_value_is_refused(self):
        from notion.config import NotionConfig, NotionConfigError

        for blank in self.BLANK:
            with self.subTest(value=blank):
                with self.assertRaises(NotionConfigError):
                    NotionConfig.from_env(
                        {
                            "NOTION_API_TOKEN": blank,
                            "NOTION_PROJECTS_DATABASE_ID": "db-1",
                        }
                    )

    def test_a_real_value_is_still_passed_through_untouched(self):
        """The boundary: blank means absent, but a value that contains
        characters is never trimmed or unquoted — that would be
        second-guessing what the operator set."""
        from notion.config import NotionConfig

        for raw in ("  ntn_x  ", '"ntn_x"', "'ntn_x'", "ntn_x\n"):
            with self.subTest(raw=raw):
                config = NotionConfig.from_env(
                    {"NOTION_API_TOKEN": raw, "NOTION_PROJECTS_DATABASE_ID": "db-1"}
                )
                self.assertEqual(config.api_token, raw)

    def test_the_error_names_the_offending_variable(self):
        from notion.config import NotionConfig, NotionConfigError

        with self.assertRaises(NotionConfigError) as caught:
            NotionConfig.from_env(
                {"NOTION_API_TOKEN": "   ", "NOTION_PROJECTS_DATABASE_ID": "db-1"}
            )

        self.assertIn("NOTION_API_TOKEN", str(caught.exception))
        self.assertNotIn("NOTION_PROJECTS_DATABASE_ID", str(caught.exception))

    def test_the_entrypoint_reports_the_reason_rather_than_guessing(self):
        """`run_company_ops.py` used to print a fixed "the variables are
        absent" line. For a blank value that is wrong, and the operator is
        looking straight at the variable in their own `.env` while being
        told it is missing.

        Kept as a source check because it pins the *shape* of the message
        (interpolate the reason, do not restate a guess). What it cannot do
        is watch the function behave —
        `BuildNotionClientsDecidesWhatRunsTests::test_a_blank_value_is_reported_as_blank_rather_than_missing`
        does that, and until C41 nothing did.
        """
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        source = __import__("inspect").getsource(module._build_notion_clients)
        self.assertIn("{exc}", source)
        self.assertNotIn("NOTION_PROJECTS_DATABASE_ID 없음", source)


class BuildNotionClientsDecidesWhatRunsTests(unittest.TestCase):
    """`run_company_ops.py::_build_notion_clients()` — the one function that
    decides whether Notion Sync and the Operations Dashboard run at all.

    Three outcomes, from environment variables alone:

        no token / no PROJECTS id      (None, None)      neither runs
        no NOTION_OPS_RUNS_DATABASE_ID (sync, None)      Sync only
        all three set                  (sync, dashboard) both run

    **None of them had a behavioural test.** A line-coverage pass that
    included the root scripts for the first time (C41) put this file at 67%
    — the worst in the repository — with essentially the whole function
    unexecuted. What existed instead was `inspect.getsource()` string checks
    here and in `test_spec_conformance.py`, plus
    `test_run_company_ops_encoding.py`, which does run it but **in a
    subprocess** and only to prove the "미설정" line does not crash a cp949
    console. None of those can see which objects come back.

    Why that matters more than the percentage:

        The middle case is what this deployment takes on every run (docs/13:
        `NOTION_OPS_RUNS_DATABASE_ID` is unset). If it ever returned a
        dashboard client instead of None, `record_run()` would write OPS_RUNS
        rows into whatever database that client held.

        The third case is the one the operator reaches by finishing docs/13
        §3-⑧. Both clients are built from **one** transport, and the two
        database ids are the only thing keeping them apart — `record_run()`'s
        own docstring says "client must be bound to the OPS_RUNS database id
        ... nothing can check that". This is where it is decided, so this is
        where it can be checked.

    No network: `RealNotionTransport(api_token=...)` stores the token, and
    `NotionClient` stores the id — neither connects until a request is made,
    and none is made here.
    """

    TOKEN = "ntn_" + "x" * 28  # built at runtime; a literal would trip
    PROJECTS_DB = "projects-db-id"  # `SecretExposureGuardTests`
    OPS_RUNS_DB = "ops-runs-db-id"

    def _module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_wiring", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _with_env(self, **values):
        import os

        keys = (
            "NOTION_API_TOKEN",
            "NOTION_PROJECTS_DATABASE_ID",
            "NOTION_OPS_RUNS_DATABASE_ID",
        )
        original = {key: os.environ.get(key) for key in keys}

        def restore():
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        for key in keys:
            os.environ.pop(key, None)
        for key, value in values.items():
            os.environ[key] = value

    def _build(self, **env):
        self._with_env(**env)
        module = self._module()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sync, dashboard = module._build_notion_clients()
        return sync, dashboard, out.getvalue()

    def test_nothing_configured_builds_neither(self):
        sync, dashboard, printed = self._build()

        self.assertIsNone(sync)
        self.assertIsNone(dashboard)
        self.assertIn("Notion 미설정", printed)

    def test_sync_configured_alone_leaves_the_dashboard_off(self):
        """The case this deployment is in. `None`, not a client pointed at
        some other database."""
        sync, dashboard, printed = self._build(
            NOTION_API_TOKEN=self.TOKEN,
            NOTION_PROJECTS_DATABASE_ID=self.PROJECTS_DB,
        )

        self.assertIsNotNone(sync)
        self.assertIsNone(dashboard)
        self.assertIn("Operations Dashboard 미설정", printed)

    def test_both_configured_builds_two_clients_on_the_right_databases(self):
        """The wiring `record_run()` cannot verify for itself."""
        sync, dashboard, _printed = self._build(
            NOTION_API_TOKEN=self.TOKEN,
            NOTION_PROJECTS_DATABASE_ID=self.PROJECTS_DB,
            NOTION_OPS_RUNS_DATABASE_ID=self.OPS_RUNS_DB,
        )

        self.assertIsNotNone(sync)
        self.assertIsNotNone(dashboard)
        self.assertEqual(dashboard._database_id, self.OPS_RUNS_DB)
        self.assertEqual(sync._client._database_id, self.PROJECTS_DB)

    def test_the_two_clients_are_never_pointed_at_one_database(self):
        """The failure this function is the only place that can prevent:
        an OPS_RUNS row written into PROJECTS, or a project row into
        OPS_RUNS. They share a transport on purpose; the ids are what keep
        them apart."""
        sync, dashboard, _printed = self._build(
            NOTION_API_TOKEN=self.TOKEN,
            NOTION_PROJECTS_DATABASE_ID=self.PROJECTS_DB,
            NOTION_OPS_RUNS_DATABASE_ID=self.OPS_RUNS_DB,
        )

        self.assertNotEqual(dashboard._database_id, sync._client._database_id)

    def test_a_blank_value_is_reported_as_blank_rather_than_missing(self):
        """The reason the message interpolates `{exc}` — an operator staring
        at the variable in their own `.env` must not be told it is absent.
        The string check this replaces could only see that `{exc}` appears
        in the source."""
        sync, dashboard, printed = self._build(
            NOTION_API_TOKEN="   ",
            NOTION_PROJECTS_DATABASE_ID=self.PROJECTS_DB,
        )

        self.assertIsNone(sync)
        self.assertIsNone(dashboard)
        self.assertIn("NOTION_API_TOKEN", printed)

    def test_the_token_is_never_printed(self):
        """docs/04 §40-41. This function is one of the few that holds the
        token in a local, and it prints on two of its three paths."""
        _sync, _dashboard, printed = self._build(
            NOTION_API_TOKEN=self.TOKEN,
            NOTION_PROJECTS_DATABASE_ID=self.PROJECTS_DB,
        )

        self.assertNotIn(self.TOKEN, printed)


if __name__ == "__main__":
    unittest.main()
