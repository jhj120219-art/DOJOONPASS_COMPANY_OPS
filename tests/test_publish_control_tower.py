"""`publish_control_tower.py`'s own `main()` (C116).

The blind spot this file was written for
----------------------------------------
Measured before writing a line of it: **no test in this repository imported
`publish_control_tower`.** The only execution it ever got was
`AnEntrypointRefusesArgumentsItCannotHonourTests`, which runs it in a
subprocess with `--dry-run` and asserts exit 1 — a refusal that happens on
the *first statement* of `main()`. Everything after that line — the health
check, the parent discovery, four Notion write surfaces, the partial-failure
reporting and the exit code — had never run.

C115's coverage sweep could not have seen this: it measured `--source=src`,
and this file is at the repository root along with every other entrypoint.
A blind spot that a coverage run reports as 100% of what it was pointed at
is the kind that survives sprints.

Two defects were inside it
--------------------------
**Exit 0 for a partial failure.** The three writes after `publish()` are
non-fatal on purpose — losing the `Notes` column must not cost the Control
Tower page — and each prints `실패 — <reason>` to stderr. `main()` returned
**0** for all three. AGENT.md §6c tells the operator to register this tool
"in Task Scheduler beside `run_company_ops.py`", and Task Scheduler reads the
exit code, not stdout. That is verbatim the defect
`run_company_ops._report_run_summary()` exists for ("the failures that were
handled *gracefully* were exactly the ones that became invisible"), left
standing in the other tool that writes to Notion.

**A comment that its own next line falsified.** The block resolving the
Dashboard address said it read the port "from the server module rather than
restated here … a second copy of either is how the page starts advertising
an address nothing listens on" — and then restated the *parsing*:

    dashboard_server.main()        int(raw), refused unless 1 <= port <= 65535
    publish_control_tower.main()   raw if raw.isdigit() else str(DEFAULT_PORT)

So `COMPANY_OPS_DASHBOARD_PORT=99999` published `http://127.0.0.1:99999/` to
the whole Notion workspace while the server refused to start on that value.
The comment's own failure, produced by the line under it. Both tools now ask
`dashboard_server.resolve_port()`.

How these tests drive it
------------------------
`main()` is called in-process with the four `publish_*` functions, the
transport and the client replaced in `publish_control_tower`'s own namespace,
and `dashboard_server.gather` replaced with a fixed payload. Nothing here
reaches Notion, opens a socket, reads `runtime/`, or takes the Runner lock —
the module promises all four, and a test that needed a real workspace would
be a test nobody runs.

    TheExitCodeSaysWhatWasWrittenTests     the exit code, per surface
    TheAdvertisedAddressIsTheServersTests  the port the page publishes
    TheEarlyRefusalsTests                  everything before the first write
    TheReportNamesEverySurfaceTests        what the operator reads on stdout
"""

import ast
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dashboard_server  # noqa: E402
import publish_control_tower  # noqa: E402

from controltower.notion_page import (  # noqa: E402
    ControlTowerPageError,
    PublishResult,
    RowPageResult,
)
from notion.config import NotionConfigError  # noqa: E402
from notion.transport import NotionAPIError  # noqa: E402

#: Everything `main()` reads out of the payload itself. The four write
#: surfaces are replaced, so nothing else in it is consulted — a fuller
#: fixture would be describing `gather()`, which has its own tests.
PAYLOAD = {"attention": ["첫 번째", "두 번째"]}

#: The real resolver, captured before any test can patch the name away.
_REAL_RESOLVE_PORT = dashboard_server.resolve_port

PAGE = PublishResult(
    page_id="page-1",
    url="https://notion.so/page-1",
    created=False,
    blocks_written=51,
    blocks_archived=48,
    title=publish_control_tower.PAGE_TITLE,
)


class _Client:
    """`NotionClient`'s two methods this entrypoint calls."""

    def __init__(self, *, healthy=True, parent={"id": "parent-1"}, find_raises=None):
        self._healthy = healthy
        self._parent = parent
        self._find_raises = find_raises
        self.database_id = None

    def health_check(self):
        class _Health:
            ok = self._healthy
            error = None if self._healthy else "unauthorized"

        return _Health()

    def find_project(self, project_id):
        if self._find_raises is not None:
            raise self._find_raises
        return self._parent


class _Harness(unittest.TestCase):
    """Drives `main()` with every outbound call replaced."""

    def setUp(self):
        self._restore = {}
        self._install()

    @contextlib.contextmanager
    def scenario(self):
        """A second, independent set of patches inside one test method.

        Several tests below walk a table of scenarios and need the harness
        back at its starting point between rows. Calling `setUp()` again by
        hand looked like it did that and did not: it rebinds `_restore` to a
        fresh dict, so the *outer* patches lose their record and the real
        `tearDown` puts nothing back. That is how this file first left
        `dashboard_server.resolve_port` replaced for the rest of the process
        — 17 tests in `test_dashboard_server.py` failed in the same run and
        passed on their own, which is the leak `TestIsolationGuardTests`
        exists for.

        This nests instead: inner patches record the outer stubs and undo
        exactly themselves, and the outer `tearDown` still owns the originals.
        """
        outer, self._restore = self._restore, {}
        self._install()
        try:
            yield
        finally:
            for target, name, original in self._restore.values():
                setattr(target, name, original)
            self._restore = outer

    def _install(self):
        # Configuration comes from the environment; supply it directly rather
        # than mutating `os.environ`, so a machine whose `.env` has been
        # exported cannot make these tests reach the real workspace.
        self._patch(
            publish_control_tower.NotionConfig,
            "from_env",
            classmethod(
                lambda cls, env=None: cls(
                    api_token="test-token", projects_database_id="db-1"
                )
            ),
        )
        self._patch(
            publish_control_tower, "RealNotionTransport", lambda **kwargs: object()
        )
        self.client = _Client()
        self._patch(publish_control_tower, "NotionClient", lambda **kwargs: self.client)
        self._patch(dashboard_server, "gather", lambda now: dict(PAYLOAD))
        self._patch(dashboard_server, "resolve_port", lambda env=None: 8765)

        self.published_with = {}

        def _publish(**kwargs):
            self.published_with.update(kwargs)
            return PAGE

        self._patch(publish_control_tower, "publish", _publish)
        self._patch(
            publish_control_tower,
            "publish_project_rows",
            lambda **kwargs: RowPageResult(written=("COMPANY_OPS",), blocks_archived=3),
        )
        self._patch(
            publish_control_tower,
            "publish_project_notes",
            lambda **kwargs: (("COMPANY_OPS",), ()),
        )
        self._patch(
            publish_control_tower, "publish_database_summary", lambda **kwargs: 240
        )

    def _patch(self, target, name, value):
        """Replace `target.name`, remembering the value that was there first.

        `setdefault`, not assignment. Several tests below patch a name this
        class has already patched — `resolve_port` and the surface a test
        makes fail — and overwriting the saved original with the *stub* is
        how `tearDown` puts a stub back and leaves it there. Measured: the
        first draft did exactly that, `dashboard_server.resolve_port` stayed
        replaced after this file ran, and 17 tests in
        `test_dashboard_server.py` failed in the same process while passing
        on their own. A leak whose symptom is a different file is the one
        `TestIsolationGuardTests` exists for.
        """
        self._restore.setdefault(
            (id(target), name), (target, name, getattr(target, name))
        )
        setattr(target, name, value)

    def tearDown(self):
        for target, name, original in self._restore.values():
            setattr(target, name, original)

    def run_main(self):
        """`main()`'s exit code, stdout and stderr."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = publish_control_tower.main(("publish_control_tower.py",))
        return code, out.getvalue(), err.getvalue()

    def fail_surface(self, name, exc):
        self._patch(
            publish_control_tower,
            name,
            lambda **kwargs: (_ for _ in ()).throw(exc),
        )


class TheExitCodeSaysWhatWasWrittenTests(_Harness):
    """The defect: three surfaces could fail and the process still said 0."""

    def test_a_publish_that_wrote_everything_exits_zero(self):
        """The antecedent. Without this, every assertion below could be
        satisfied by a `main()` that returned 3 unconditionally."""
        code, _, err = self.run_main()

        self.assertEqual(code, 0, err)
        self.assertNotIn("DEGRADED", err)

    def test_each_failing_surface_alone_degrades_the_run(self):
        surfaces = (
            ("publish_database_summary", "DB 설명"),
            ("publish_project_notes", "Notes 열"),
            ("publish_project_rows", "Project Row"),
        )
        for name, label in surfaces:
            with self.subTest(surface=name), self.scenario():
                self.fail_surface(name, NotionAPIError("503 service unavailable"))
                code, _, err = self.run_main()

                self.assertEqual(
                    code,
                    publish_control_tower.DEGRADED_EXIT,
                    f"{name} failed and the process reported success:\n{err}",
                )
                self.assertIn("DEGRADED", err)
                self.assertIn(label, err)

    def test_the_degraded_line_names_every_surface_that_failed(self):
        """Three at once, because a summary that stops at the first is how an
        operator fixes one thing and believes they are done."""
        self.fail_surface("publish_database_summary", NotionAPIError("a"))
        self.fail_surface("publish_project_notes", NotionAPIError("b"))
        self.fail_surface("publish_project_rows", NotionAPIError("c"))

        code, _, err = self.run_main()

        self.assertEqual(code, publish_control_tower.DEGRADED_EXIT)
        summary = [line for line in err.splitlines() if "DEGRADED" in line]
        self.assertEqual(len(summary), 1, err)
        for label in ("DB 설명", "Notes 열", "Project Row"):
            self.assertIn(label, summary[0])

    def test_a_row_skipped_because_a_person_wrote_in_it_is_not_a_failure(self):
        """AGENT.md §6c: 사람이 쓴 내용은 이깁니다. Skipping such a row is the
        tool keeping that promise, and an exit code that called it degraded
        would train the operator to ignore the one that isn't."""
        self._patch(
            publish_control_tower,
            "publish_project_rows",
            lambda **kwargs: RowPageResult(
                written=("COMPANY_OPS",),
                skipped_hand_written=("SEARCH_BACKEND",),
                skipped_unsourced=("ENGINEERING_PROBE_1",),
            ),
        )
        self._patch(
            publish_control_tower,
            "publish_project_notes",
            lambda **kwargs: (("COMPANY_OPS",), ("SEARCH_BACKEND",)),
        )

        code, _, err = self.run_main()

        self.assertEqual(code, 0)
        self.assertIn("SEARCH_BACKEND", err)

    def test_a_page_warning_is_not_a_failure_either(self):
        """`PublishResult.warnings` says so in its own field docstring —
        "Never a reason to fail". Pinned here so the exit code cannot start
        disagreeing with the field it reads."""
        self._patch(
            publish_control_tower,
            "publish",
            lambda **kwargs: PublishResult(
                page_id="page-1",
                url=None,
                created=True,
                blocks_written=51,
                blocks_archived=0,
                title=publish_control_tower.PAGE_TITLE,
                warnings=("panel team missing from the model",),
            ),
        )

        code, _, err = self.run_main()

        self.assertEqual(code, 0)
        self.assertIn("panel team missing from the model", err)

    def test_the_three_exit_codes_are_distinct(self):
        """Guards the constants themselves: the whole point of 3 is that a
        degraded publish is neither a success nor a configuration error, and
        two of these collapsing would put it back where it started."""
        self.assertEqual(publish_control_tower.CONFIG_ERROR_EXIT, 1)
        self.assertEqual(publish_control_tower.FAILED_EXIT, 1)
        self.assertNotIn(publish_control_tower.DEGRADED_EXIT, (0, 1, 2))

    def test_three_matches_what_the_other_entrypoints_spend_it_on(self):
        """`ops_status.py` exit 3 and the Runner's DEGRADED already mean
        "something needs a person". A third meaning for the same number is
        how two tools start disagreeing about what a 3 is."""
        import ops_status
        import run_company_ops

        self.assertEqual(publish_control_tower.DEGRADED_EXIT, 3)
        self.assertIn(
            "3   at least one thing needs a person", ops_status.__doc__
        )
        self.assertIn("DEGRADED  3", run_company_ops._report_run_summary.__doc__)


class TheAdvertisedAddressIsTheServersTests(_Harness):
    """What the page tells the workspace to open."""

    def _publish_with(self, raw):
        """`main()` under a given `COMPANY_OPS_DASHBOARD_PORT`, resolved by
        the real `resolve_port()` rather than the harness stub — the point of
        these tests is the answer that function actually gives."""
        env = {} if raw is None else {dashboard_server.PORT_ENV_VAR: raw}
        self._patch(
            dashboard_server, "resolve_port", lambda e=None: _REAL_RESOLVE_PORT(env)
        )
        return self.run_main()

    def test_an_unset_variable_advertises_the_default_port(self):
        code, _, err = self._publish_with(None)

        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.published_with["dashboard_url"],
            f"http://127.0.0.1:{dashboard_server.DEFAULT_PORT}/",
        )

    def test_a_real_port_is_advertised_as_set(self):
        code, _, err = self._publish_with("9001")

        self.assertEqual(code, 0, err)
        self.assertEqual(self.published_with["dashboard_url"], "http://127.0.0.1:9001/")

    def test_a_port_the_server_would_refuse_is_not_published_at_all(self):
        """The defect. `99999` and `0` are outside 1-65535, so
        `dashboard_server.py` exits 1 rather than binding — but both pass
        `isdigit()`, and the old line published them to the workspace."""
        for raw in ("99999", "0", "-1", "eight thousand"):
            with self.subTest(port=raw), self.scenario():
                code, _, err = self._publish_with(raw)

                self.assertIsNone(
                    self.published_with["dashboard_url"],
                    f"published an address for {raw!r} that the server "
                    f"refuses to bind",
                )
                self.assertEqual(code, publish_control_tower.DEGRADED_EXIT)
                self.assertIn(dashboard_server.PORT_ENV_VAR, err)

    def test_the_two_tools_cannot_disagree_about_what_a_port_is(self):
        """The structural half. Both now call the same function, so the table
        in `resolve_port()`'s docstring is a property rather than a snapshot
        — and this asserts the server's own acceptance directly."""
        real = _REAL_RESOLVE_PORT
        cases = {
            "": dashboard_server.DEFAULT_PORT,
            "  ": dashboard_server.DEFAULT_PORT,
            "8765": 8765,
            "1": 1,
            "65535": 65535,
            "0": None,
            "65536": None,
            "99999": None,
            "-1": None,
            "80.5": None,
            "http://127.0.0.1:8765": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    real({dashboard_server.PORT_ENV_VAR: raw}), expected
                )

    def test_the_publisher_holds_no_parser_of_its_own(self):
        """Guards the guard. The defect was a *second copy*, and a second copy
        can come back without any test above going red — the two would simply
        agree on the day it was written, and drift after.

        Read from the source because that is where a second copy appears —
        and with comments and strings tokenized away, because the *prose*
        above the fixed line quotes the old parser on purpose and a scan of
        raw text fails on the record of the defect it is guarding."""
        path = Path(__file__).resolve().parents[1] / "publish_control_tower.py"
        text = path.read_text(encoding="utf-8")

        self.assertIn("dashboard_server.resolve_port()", text)

        code = self._code_only(path)
        for parser in (".isdigit(", "int(raw", "65535", "DEFAULT_PORT"):
            with self.subTest(fragment=parser):
                self.assertNotIn(parser, code)

    @staticmethod
    def _code_only(path):
        """Source with every comment and string literal removed."""
        import io
        import tokenize

        kept = []
        with open(path, "rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                kept.append(token.string)
        return " ".join(kept)

    def test_the_comment_scanner_can_actually_disagree(self):
        """Guards the guard's guard: a tokenizer that returned nothing would
        make every assertion above vacuous."""
        code = self._code_only(
            Path(__file__).resolve().parents[1] / "publish_control_tower.py"
        )

        self.assertIn("resolve_port", code)
        self.assertIn("DEGRADED_EXIT", code)
        self.assertNotIn("C116", code)  # only ever appears in prose


class TheEarlyRefusalsTests(_Harness):
    """Everything that stops before the first write. None of it had ever
    executed in this suite."""

    def test_an_argument_is_refused_and_nothing_is_published(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = publish_control_tower.main(
                ("publish_control_tower.py", "--dry-run")
            )

        self.assertEqual(code, publish_control_tower.CONFIG_ERROR_EXIT)
        self.assertEqual(self.published_with, {})
        self.assertIn("NOTION_API_TOKEN", err.getvalue())

    def test_unconfigured_notion_names_the_file_that_is_not_read(self):
        """`.env` is deliberately not auto-loaded, and an operator staring at
        a filled `.env` needs to be told that rather than the variable names
        they can already see in it."""
        self._patch(
            publish_control_tower.NotionConfig,
            "from_env",
            classmethod(
                lambda cls, env=None: (_ for _ in ()).throw(
                    NotionConfigError("missing or blank required environment variable(s)")
                )
            ),
        )

        code, _, err = self.run_main()

        self.assertEqual(code, publish_control_tower.CONFIG_ERROR_EXIT)
        self.assertIn(".env", err)
        self.assertEqual(self.published_with, {})

    def test_an_unreachable_database_fails_before_it_writes(self):
        self.client = _Client(healthy=False)
        self._patch(publish_control_tower, "NotionClient", lambda **kw: self.client)

        code, _, err = self.run_main()

        self.assertEqual(code, publish_control_tower.FAILED_EXIT)
        self.assertEqual(self.published_with, {})
        self.assertIn("unauthorized", err)

    def test_a_missing_parent_row_says_which_row(self):
        """"Publish failed" sends nobody anywhere. The `Project ID` this tool
        looks for is the one actionable fact in that state."""
        self.client = _Client(parent=None)
        self._patch(publish_control_tower, "NotionClient", lambda **kw: self.client)

        code, _, err = self.run_main()

        self.assertEqual(code, publish_control_tower.FAILED_EXIT)
        self.assertIn(publish_control_tower.PARENT_PROJECT_ID, err)
        self.assertEqual(self.published_with, {})

    def test_a_lookup_that_raises_is_reported_rather_than_traced(self):
        self.client = _Client(find_raises=NotionAPIError("429 rate limited"))
        self._patch(publish_control_tower, "NotionClient", lambda **kw: self.client)

        code, _, err = self.run_main()

        self.assertEqual(code, publish_control_tower.FAILED_EXIT)
        self.assertIn("429 rate limited", err)

    def test_a_page_that_cannot_be_built_is_fatal(self):
        """Unlike the three surfaces after it: losing the page *is* losing
        the report, so there is nothing left to be degraded about."""
        for exc in (ControlTowerPageError("blocks too deep"), NotionAPIError("500")):
            with self.subTest(error=type(exc).__name__), self.scenario():
                self.fail_surface("publish", exc)
                code, _, err = self.run_main()

                self.assertEqual(code, publish_control_tower.FAILED_EXIT)
                self.assertIn(str(exc), err)


class TheReportNamesEverySurfaceTests(_Harness):
    """AGENT.md §6c promises the operator four lines. Nothing checked that
    the tool prints them."""

    def test_a_healthy_run_reports_all_four_surfaces(self):
        code, out, err = self.run_main()

        self.assertEqual(code, 0, err)
        for expected in ("page_id", "블록 기록", "DB 설명", "Notes 열", "Project Row"):
            with self.subTest(line=expected):
                self.assertIn(expected, out)

    def test_the_attention_count_comes_from_the_payload(self):
        code, out, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertIn(f"{len(PAYLOAD['attention'])}건", out)

    def test_created_and_updated_are_told_apart(self):
        """Run a hundred times and there is one page — so `생성` appearing on
        a second run would mean the find-by-title lookup had stopped working
        and the workspace was collecting duplicates."""
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("갱신 완료", out)

        with self.scenario():
            self._patch(
                publish_control_tower,
                "publish",
                lambda **kw: PublishResult(
                    page_id="page-1",
                    url=None,
                    created=True,
                    blocks_written=51,
                    blocks_archived=0,
                    title=publish_control_tower.PAGE_TITLE,
                ),
            )
            code, out, _ = self.run_main()
            self.assertEqual(code, 0)
            self.assertIn("생성 완료", out)

    def test_a_failed_surface_is_reported_on_stderr_not_stdout(self):
        """stdout is the report; stderr is what a person has to act on. A
        failure printed into the report reads as one of the four good lines."""
        self.fail_surface("publish_project_notes", NotionAPIError("503"))

        _, out, err = self.run_main()

        self.assertIn("503", err)
        self.assertNotIn("503", out)


#: A credential-shaped string, built by concatenation rather than written out
#: — `SecretExposureGuardTests.test_no_secret_material_in_any_tracked_file`
#: scans every tracked file for exactly this shape and should. A fixture and
#: a leaked token are indistinguishable to a scanner.
FAKE_TOKEN = "ntn_" + "A" * 44

#: What a broken or hostile proxy answers with in Notion's place. docs/04 §56
#: is about this, and `NotionAPIError` carries up to 400 bytes of the body
#: verbatim. Two payloads in one: the request's own `Authorization` header
#: echoed back, and a line in the exact shape of a report line.
PROXY_BODY = (
    "502 Bad Gateway\n"
    "Upstream request was:\n"
    f"  Authorization: Bearer {FAKE_TOKEN}\n"
    "  다음 할 일     : 없음 — 설정 완료\n"
)


class NothingRemoteReachesTheOperatorUnguardedTests(_Harness):
    """Every value this tool prints came back over the network (C124).

    **The defect: not one sink in this file was guarded.** `init_notion.py`
    grew `_safe()` for exactly these values and its docstring named the gap
    in the same sentence —

        the blind spot C31 §7/§8 closed at `run_company_ops.py`'s two sinks
        and **did not look for at this one**

    — and it did not look here either. This tool was written afterwards
    (C105/C106), reached the same two classes of string, and guarded none of
    them.

    Measured through the real `main()` before the fix, with a transport that
    raises what a proxy would:

        exit 1, **5 lines** on stderr from one `print()`
        `Authorization: Bearer ntn_AAAA…`  verbatim, no `[REDACTED]`
        `  다음 할 일     : 없음 — 설정 완료`  as its own line

    Both halves cost something different. The token is a credential this
    process had just sent, and `tool > log 2>&1` writes it to disk. The
    forged line is in `init_notion.py`'s own format for "what is left to do".

    Why redaction is right here while `ops_status.py` deliberately refuses it
    at its own sink: that decision is about strings built from local paths,
    ids and counts, where over-redacting costs the operator the path they
    are about to open. Nothing in this file is local.
    """

    def _error(self, text):
        return NotionAPIError(text)

    def test_a_proxy_echoing_the_request_cannot_print_the_token(self):
        """The measurement, as a test. The health-check sink is the one an
        operator hits first and the one that fires on a bad token."""
        class _Unhealthy:
            ok = False
            error = "Notion API returned 502: Bad Gateway | " + PROXY_BODY

        self.client.health_check = lambda: _Unhealthy()

        code, _, err = self.run_main()

        self.assertEqual(code, publish_control_tower.FAILED_EXIT)
        self.assertNotIn(FAKE_TOKEN, err)
        self.assertIn("[REDACTED]", err)

    def test_a_remote_body_cannot_forge_a_report_line(self):
        class _Unhealthy:
            ok = False
            error = PROXY_BODY

        self.client.health_check = lambda: _Unhealthy()

        _, _, err = self.run_main()

        self.assertEqual(
            len(err.strip().splitlines()),
            1,
            f"one print() became several lines:\n{err}",
        )
        self.assertIn("\\n", err)  # the newlines are shown, not obeyed

    def test_every_failing_surface_is_guarded_the_same_way(self):
        """The three non-fatal writes each print `str(NotionAPIError)` — the
        same remote body, three more sinks. Guarding one and not the others
        is how this defect got here in the first place."""
        surfaces = (
            "publish_database_summary",
            "publish_project_notes",
            "publish_project_rows",
        )
        for name in surfaces:
            with self.subTest(surface=name), self.scenario():
                self.fail_surface(name, self._error(PROXY_BODY))
                code, _, err = self.run_main()

                self.assertEqual(code, publish_control_tower.DEGRADED_EXIT)
                self.assertNotIn(FAKE_TOKEN, err)
                self.assertIn("[REDACTED]", err)

    def test_the_fatal_publish_paths_are_guarded_too(self):
        for exc in (
            ControlTowerPageError(PROXY_BODY),
            NotionAPIError(PROXY_BODY),
        ):
            with self.subTest(error=type(exc).__name__), self.scenario():
                self.fail_surface("publish", exc)
                _, _, err = self.run_main()

                self.assertNotIn(FAKE_TOKEN, err)
                self.assertIn("[REDACTED]", err)

    def test_a_project_lookup_failure_is_guarded(self):
        self.client = _Client(find_raises=NotionAPIError(PROXY_BODY))
        self._patch(publish_control_tower, "NotionClient", lambda **kw: self.client)

        _, _, err = self.run_main()

        self.assertNotIn(FAKE_TOKEN, err)
        self.assertIn("[REDACTED]", err)

    def test_a_row_name_someone_typed_in_notion_is_guarded(self):
        """The other provenance. These are Notion Row names — text a person
        typed in a browser, on the other side of the network, and this tool
        prints them back so the operator knows which rows it left alone."""
        forged = "COMPANY_OPS\n  Project Row  : 8건 갱신 (블록 보관 0)"
        self._patch(
            publish_control_tower,
            "publish_project_rows",
            lambda **kw: RowPageResult(
                written=(),
                skipped_hand_written=(forged,),
                skipped_unsourced=(f"Bearer {FAKE_TOKEN}",),
            ),
        )
        self._patch(
            publish_control_tower,
            "publish_project_notes",
            lambda **kw: ((), (forged,)),
        )

        _, out, err = self.run_main()

        combined = out + err
        self.assertNotIn(FAKE_TOKEN, combined)

        # The row name still appears — the operator asked which rows were
        # skipped and that is the answer. What must not happen is it
        # appearing as **its own line**, in the format of the report line
        # directly above it. `one_line()` leaves the text and takes the
        # newline, which is exactly the right trade at this sink.
        forged_line = "  Project Row  : 8건 갱신 (블록 보관 0)"
        self.assertNotIn(
            forged_line,
            combined.splitlines(),
            "a Notion row name became a line of this tool's own report",
        )
        self.assertIn("Project Row  : 8건 갱신", combined)  # still readable
        self.assertIn("\\n", combined)  # as an escaped newline, in one line

    def test_the_page_id_and_url_are_guarded(self):
        """Ids are authored text too — `AnIdIsAlsoAuthoredTextTests` settled
        that one layer down, and these two come straight out of an API
        response."""
        self._patch(
            publish_control_tower,
            "publish",
            lambda **kw: PublishResult(
                page_id=f"page\nBearer {FAKE_TOKEN}",
                url=f"https://notion.so/{FAKE_TOKEN}",
                created=False,
                blocks_written=1,
                blocks_archived=0,
                title=publish_control_tower.PAGE_TITLE,
            ),
        )

        _, out, _ = self.run_main()

        self.assertNotIn(FAKE_TOKEN, out)
        # Nine lines: the header and the eight facts. The forged newline
        # inside `page_id` would have made it ten.
        self.assertEqual(len(out.strip().splitlines()), 9, out)
        self.assertIn("page\\n[REDACTED]", out)

    def test_a_page_warning_is_guarded(self):
        self._patch(
            publish_control_tower,
            "publish",
            lambda **kw: PublishResult(
                page_id="p",
                url=None,
                created=False,
                blocks_written=1,
                blocks_archived=0,
                title=publish_control_tower.PAGE_TITLE,
                warnings=(PROXY_BODY,),
            ),
        )

        _, _, err = self.run_main()

        self.assertNotIn(FAKE_TOKEN, err)

    def test_a_healthy_run_is_not_over_redacted(self):
        """The control. A guard that mangled ordinary output would be
        traded for the defect it removes — the page id and url are what an
        operator clicks."""
        code, out, err = self.run_main()

        self.assertEqual(code, 0, err)
        self.assertIn("page-1", out)
        self.assertIn("https://notion.so/page-1", out)
        self.assertNotIn("[REDACTED]", out)

    #: Names bound to something the network produced. An `int` read off one
    #: of these is not text and cannot forge or leak — see
    #: `NUMERIC_FIELDS` and the test that checks that claim.
    REMOTE = {
        "exc", "health", "result", "summary_error", "notes_error",
        "rows_error", "warning", "notes_skipped", "rows_result", "name",
    }

    #: Attributes on those objects that are **integers**, so interpolating
    #: them raw is safe by type rather than by trust. Asserted against the
    #: real annotations in `test_the_numeric_exclusion_is_actually_numeric`,
    #: because an exclusion nobody checks is how a scanner starts skipping a
    #: string.
    NUMERIC_FIELDS = {"blocks_written", "blocks_archived"}

    #: The one `{exc}` in this file that is not remote: `NotionConfigError`
    #: is built from a fixed tuple of variable **names** in
    #: `NotionConfig.from_env()` and never carries a response body.
    LOCAL_EXCEPTION_LINE = "Notion 미설정"

    def _unguarded_prints(self, source):
        """Every `print()` here that interpolates remote text without
        `_safe()`."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if self.LOCAL_EXCEPTION_LINE in segment:
                continue
            for slot in (
                inner for inner in ast.walk(node)
                if isinstance(inner, ast.FormattedValue)
            ):
                expression = slot.value
                # `{len(...)}` is a count.
                if (
                    isinstance(expression, ast.Call)
                    and isinstance(expression.func, ast.Name)
                    and expression.func.id == "len"
                ):
                    continue
                # `{result.blocks_written}` is an int — see NUMERIC_FIELDS.
                if (
                    isinstance(expression, ast.Attribute)
                    and expression.attr in self.NUMERIC_FIELDS
                ):
                    continue
                guarded = any(
                    isinstance(inner, ast.Name) and inner.id == "_safe"
                    for inner in ast.walk(expression)
                )
                if guarded:
                    continue
                referenced = {
                    inner.id
                    for inner in ast.walk(expression)
                    if isinstance(inner, ast.Name)
                }
                if referenced & self.REMOTE:
                    yield node.lineno, " ".join(segment.split())[:80]

    def test_no_remote_value_reaches_a_print_unguarded(self):
        """Structural, because the ten fixes above are a roster and a roster
        drifts (C115). Checks each interpolation slot rather than the whole
        `print()`: a call that mixes a guarded string with a raw count is
        correct, and a check that could not tell them apart would report the
        healthy line as a defect and be ignored."""
        source = (
            Path(__file__).resolve().parents[1] / "publish_control_tower.py"
        ).read_text(encoding="utf-8")

        offenders = [f"line {line}: {text}" for line, text in self._unguarded_prints(source)]

        self.assertEqual(
            offenders,
            [],
            "a print() interpolates a remote-authored value without "
            "_safe():\n  " + "\n  ".join(offenders),
        )

    def test_the_numeric_exclusion_is_actually_numeric(self):
        """The exclusion above skips two attributes on the ground that they
        are `int`. That is checked here rather than believed — the day one
        becomes a string, the scanner must stop skipping it."""
        import typing

        from controltower.notion_page import PublishResult, RowPageResult

        for model in (PublishResult, RowPageResult):
            hints = typing.get_type_hints(model)
            for field in self.NUMERIC_FIELDS & set(hints):
                with self.subTest(model=model.__name__, field=field):
                    self.assertIs(hints[field], int)

        # and the set is not empty against those models, or it excludes
        # nothing and says nothing
        self.assertTrue(
            self.NUMERIC_FIELDS
            & (set(typing.get_type_hints(PublishResult))
               | set(typing.get_type_hints(RowPageResult)))
        )

    def test_the_scanner_finds_an_unguarded_print_when_there_is_one(self):
        """Guards the guard, by feeding it the defect. The real file is
        clean, so a scanner that had stopped working would look identical to
        one that works — this drives it over the source as it was."""
        broken = (
            "def main():\n"
            "    print(f'[FAILED] {health.error}', file=sys.stderr)\n"
            "    print(f'  ! {warning}', file=sys.stderr)\n"
        )
        fixed = broken.replace("{health.error}", "{_safe(health.error)}").replace(
            "{warning}", "{_safe(warning)}"
        )
        counted = (
            "def main():\n"
            "    print(f'  blocks : {result.blocks_written}')\n"
            "    print(f'  n : {len(rows_result.written)}건')\n"
        )

        self.assertEqual(len(list(self._unguarded_prints(broken))), 2)
        self.assertEqual(list(self._unguarded_prints(fixed)), [])
        self.assertEqual(
            list(self._unguarded_prints(counted)),
            [],
            "the scanner reported a count as a leak — a check that flags "
            "healthy lines is a check nobody reads",
        )



if __name__ == "__main__":
    unittest.main()
