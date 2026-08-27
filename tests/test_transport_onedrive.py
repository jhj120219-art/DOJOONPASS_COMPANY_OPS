import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event  # noqa: E402
from transport import OneDriveTransport, TransportError  # noqa: E402


def sample_event(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-ONEDRIVE-001",
        "timestamp": "2026-08-01T20:00:00+09:00",
        "source": "DESKTOP_3",
        "role": "CTO_FRONTEND",
        "project_id": "SEARCH_FRONTEND",
        "event_type": "MILESTONE_COMPLETED",
        "status": "IN_PROGRESS",
        "milestone": "Search UI",
        "summary": "Search UI implementation completed",
        "blocker": None,
        "evidence": ["TypeScript PASS"],
        "history_candidate": True,
    }
    data.update(overrides)
    return Event.from_dict(data)


class OneDriveTransportTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.outgoing_dir = root / "outgoing"
        self.sync_folder = root / "sync"
        self.transport = OneDriveTransport(sync_folder=self.sync_folder, outgoing_dir=self.outgoing_dir)


class SendTests(OneDriveTransportTestCase):
    def test_writes_to_outgoing_dir(self):
        event = sample_event()
        self.transport.send(event)
        self.assertTrue((self.outgoing_dir / "TEST-ONEDRIVE-001.json").exists())

    def test_writes_to_sync_folder(self):
        event = sample_event()
        self.transport.send(event)
        self.assertTrue((self.sync_folder / "TEST-ONEDRIVE-001.json").exists())

    def test_sync_folder_content_round_trips_to_the_same_event(self):
        event = sample_event()
        self.transport.send(event)
        raw = (self.sync_folder / "TEST-ONEDRIVE-001.json").read_text(encoding="utf-8")
        self.assertEqual(Event.from_json(raw), event)

    def test_korean_summary_survives_the_copy(self):
        event = sample_event(event_id="TEST-ONEDRIVE-002", summary="검색 UI 구현 완료")
        self.transport.send(event)
        raw = (self.sync_folder / "TEST-ONEDRIVE-002.json").read_text(encoding="utf-8")
        self.assertIn("검색 UI 구현 완료", raw)

    def test_resending_same_event_id_is_idempotent(self):
        event = sample_event()
        self.transport.send(event)
        self.transport.send(event)  # simulate retry
        raw = (self.sync_folder / "TEST-ONEDRIVE-001.json").read_text(encoding="utf-8")
        self.assertEqual(Event.from_json(raw), event)

    def test_no_leftover_temp_files(self):
        self.transport.send(sample_event())
        self.assertEqual(list(self.outgoing_dir.glob(".tmp-*")), [])
        self.assertEqual(list(self.sync_folder.glob(".tmp-*")), [])

    def test_write_failure_raises_transport_error(self):
        # point outgoing_dir at a path that can never become a directory
        # (a file already occupies that name)
        root = self.outgoing_dir.parent
        blocked = root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        transport = OneDriveTransport(sync_folder=self.sync_folder, outgoing_dir=blocked / "nested")

        with self.assertRaises(TransportError):
            transport.send(sample_event())


class OneDriveTransportPathSafetyTests(unittest.TestCase):
    def test_no_hardcoded_absolute_windows_paths(self):
        onedrive_file = Path(__file__).resolve().parents[1] / "src" / "transport" / "onedrive.py"
        content = onedrive_file.read_text(encoding="utf-8")
        code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
        for token in ("C:\\Users", "D:\\", "OneDrive\\"):
            self.assertNotIn(token, code_without_docstrings)

    def test_does_not_import_collector_reporter_or_daily(self):
        onedrive_file = Path(__file__).resolve().parents[1] / "src" / "transport" / "onedrive.py"
        content = onedrive_file.read_text(encoding="utf-8")
        forbidden = re.compile(r"^\s*(import|from)\s+(collector|reporter|daily|scheduler)\b", re.MULTILINE)
        self.assertIsNone(forbidden.search(content))


class NoTestStagesIntoTheLiveRepositoryTests(unittest.TestCase):
    """`outgoing_dir` defaults to this repository, and two tests took it (C123).

    `OneDriveTransport.__init__` ends with

        self.outgoing_dir = Path(outgoing_dir) if outgoing_dir is not None \
            else DEFAULT_OUTGOING_DIR

    and `DEFAULT_OUTGOING_DIR` is `PROJECT_ROOT / "runtime" / "events" /
    "outgoing"` — the **live tree**, not a temp directory. Two tests built a
    transport without it while carefully putting every other path under
    `tempfile.mkdtemp()`, and `send()` never removes its staging file, so
    each full run left a fabricated Event sitting in the operator's runtime:

        runtime/events/outgoing/E1.json
            {"event_id": "E1", "project_id": "P", "summary": "s", ...}
        runtime/events/outgoing/3862cac2-....json
            {"project_id": "PRJ", "milestone": "M", ...}

    **Nothing corrupts Company History from there** — the Collector reads
    `incoming/`, and `run_intake()` promotes `transport/`; no code path
    promotes `outgoing/`. It was measured before this class was written
    rather than assumed. What it does cost is real all the same: fabricated
    Events in an operational directory, and a test suite that writes into the
    tree it is supposed to only read.

    **And the directory itself is stranger than the bug.** Measured:

        who writes it in production   nobody — `run_agent.py` passes
                                      `outgoing_dir=runtime/agent/outgoing`
        who reads it                  nothing, anywhere in the repository
        docs/14 §2 Artifact Taxonomy  `events/` is listed as
                                      `transport|incoming|processed|rejected/`
                                      — `outgoing/` is **not in it**, and that
                                      section's own claim is "새 폴더를 만들지
                                      않았다"

    So the default's only effect in the whole system is to absorb the output
    of a caller who forgot the argument. Whether it should exist at all is a
    public-API decision (`DEFAULT_OUTGOING_DIR` is in `transport.__all__`) and
    is recorded in BACKLOG rather than taken here. This class removes the way
    it actually fires.
    """

    TESTS = Path(__file__).resolve().parents[0]

    def _call_sites(self):
        """Every `OneDriveTransport(...)` construction under `tests/`."""
        for path in sorted(self.TESTS.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "OneDriveTransport"
                ):
                    yield path, node

    def test_the_scan_finds_the_call_sites_that_exist(self):
        """Guards the guard. An AST walk that matched nothing — a rename, a
        different import spelling — would make the check below vacuous."""
        sites = list(self._call_sites())

        self.assertGreater(len(sites), 10, "the scan found almost no call sites")
        self.assertGreater(
            len({path for path, _ in sites}),
            5,
            "the scan is only seeing one file",
        )

    def test_every_test_names_its_own_staging_directory(self):
        offenders = []
        for path, node in self._call_sites():
            names = {keyword.arg for keyword in node.keywords}
            # Positional: `OneDriveTransport(sync, outgoing)` gives two args.
            if "outgoing_dir" in names or len(node.args) >= 2:
                continue
            offenders.append(f"{path.name}:{node.lineno}")

        self.assertEqual(
            offenders,
            [],
            "a test built a OneDriveTransport without `outgoing_dir`, so its "
            "staging write lands in this repository's own "
            "runtime/events/outgoing/ and stays there — `send()` never "
            "removes it:\n  " + "\n  ".join(offenders),
        )

    def test_the_default_really_is_the_repository(self):
        """The premise, asserted rather than believed. If this ever stops
        being true the class above is guarding nothing."""
        from transport.onedrive import DEFAULT_OUTGOING_DIR

        repo = Path(__file__).resolve().parents[1]

        self.assertEqual(DEFAULT_OUTGOING_DIR, repo / "runtime" / "events" / "outgoing")

    def test_the_production_agent_does_not_use_that_default(self):
        """Which is why nothing noticed. The one caller that matters passes
        `runtime/agent/outgoing` — a different directory entirely."""
        source = (Path(__file__).resolve().parents[1] / "run_agent.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("outgoing_dir=agent_dir / \"outgoing\"", source)

    def test_nothing_outside_the_transport_package_uses_that_default(self):
        """The other half of "stranger than the bug": a staging buffer whose
        default nobody reaches for. Stated as a measurement, so the day
        something does start using it, this says so and the record has to be
        revisited rather than quietly outgrown."""
        repo = Path(__file__).resolve().parents[1]
        users = []
        for path in list(repo.glob("*.py")) + sorted((repo / "src").rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            if relative.startswith("src/transport/"):
                continue
            if "DEFAULT_OUTGOING_DIR" in path.read_text(encoding="utf-8"):
                users.append(relative)

        self.assertEqual(users, [])

    def test_that_sweep_can_see_the_package_that_does_use_it(self):
        """Guards the guard: the exclusion above must not be excluding
        everything."""
        package = Path(__file__).resolve().parents[1] / "src" / "transport"
        using = [
            path.name
            for path in sorted(package.glob("*.py"))
            if "DEFAULT_OUTGOING_DIR" in path.read_text(encoding="utf-8")
        ]

        self.assertEqual(using, ["__init__.py", "onedrive.py"])


if __name__ == "__main__":
    unittest.main()
