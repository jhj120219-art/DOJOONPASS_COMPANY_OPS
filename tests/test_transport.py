import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event, EventValidationError  # noqa: E402
from reporter import Reporter, ReporterConfigError  # noqa: E402
from transport import InMemoryTransport, Transport, TransportError  # noqa: E402


class AlwaysFailTransport(Transport):
    """Test-only Transport double that always fails delivery."""

    def send(self, event: Event) -> None:
        raise TransportError("simulated delivery failure")


class TransportInterfaceTests(unittest.TestCase):
    def test_transport_cannot_be_instantiated_directly(self):
        with self.assertRaises(TypeError):
            Transport()

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class Incomplete(Transport):
            pass

        with self.assertRaises(TypeError):
            Incomplete()

    def test_in_memory_transport_satisfies_interface(self):
        transport = InMemoryTransport()
        self.assertIsInstance(transport, Transport)


class ReporterTransportWiringTests(unittest.TestCase):
    def setUp(self):
        self.transport = InMemoryTransport()
        self.reporter = Reporter(profile="DESKTOP_3", transport=self.transport)

    def _report_kwargs(self, **overrides):
        kwargs = dict(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            milestone="Search UI",
            summary="Search UI implementation completed",
            evidence=["TypeScript PASS"],
            history_candidate=True,
        )
        kwargs.update(overrides)
        return kwargs

    def test_send_delivers_event_to_configured_transport(self):
        event = self.reporter.report(**self._report_kwargs())
        self.reporter.send(event)
        self.assertEqual(self.transport.sent, [event])

    def test_report_and_send_creates_and_delivers_in_one_call(self):
        event = self.reporter.report_and_send(**self._report_kwargs())
        self.assertEqual(self.transport.sent, [event])

    def test_send_without_configured_transport_raises(self):
        bare_reporter = Reporter(profile="DESKTOP_3")
        event = bare_reporter.report(**self._report_kwargs())
        with self.assertRaises(ReporterConfigError):
            bare_reporter.send(event)

    def test_invalid_signal_never_reaches_transport(self):
        with self.assertRaises(EventValidationError):
            self.reporter.report_and_send(
                **self._report_kwargs(event_type="BLOCKED", status="BLOCKED", blocker=None)
            )
        self.assertEqual(self.transport.sent, [])

    def test_transport_failure_propagates_to_caller(self):
        failing_reporter = Reporter(profile="DESKTOP_3", transport=AlwaysFailTransport())
        event = failing_reporter.report(**self._report_kwargs())
        with self.assertRaises(TransportError):
            failing_reporter.send(event)

    def test_two_reporters_can_share_one_transport(self):
        other = Reporter(profile="DESKTOP_1", transport=self.transport)
        event = other.report(**self._report_kwargs(project_id="AUCTION_CRAWLER"))
        other.send(event)
        self.assertIn(event, self.transport.sent)


class TransportPathSafetyTests(unittest.TestCase):
    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        transport_src = Path(__file__).resolve().parents[1] / "src" / "transport"
        # "OneDrive" alone is not checked here: it is the name of the
        # production Transport (`OneDriveTransport`) and appears legitimately
        # all over this package. Only literal path-shaped usage would
        # indicate a hardcoded path. The citation that used to sit here —
        # "see Event Transport analysis" — pointed at no document (C122).
        forbidden = ("C:\\Users", "D:\\", "OneDrive\\")
        # Materialised and asserted non-empty first: an empty glob (a renamed
        # or moved package) would make every assertion below run zero times and
        # the test pass while enforcing nothing.
        sources = sorted(transport_src.glob("*.py"))
        self.assertTrue(sources, f"no sources under {transport_src}")
        for py_file in sources:
            content = py_file.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, content, f"{token} found in {py_file}")


class TransportSeamNamesWhatActuallyDeliversTests(unittest.TestCase):
    """The seam's own docstring, checked against the tree (C122).

    What it said, in the module that *defines* what delivers Events:

        No concrete production Transport is chosen yet (GitHub / OneDrive /
        USB / SharedFolder are all still open — see the Event Transport
        analysis) ... so a real Transport can be dropped in later

    Every clause was false, and had been for a long time:

        the choice          made and shipped — `OneDriveTransport`
                            ("COO Architecture Decision, Phase 5.1"),
                            built by `run_agent.py`, drawn in AGENT.md §1
        GitHubTransport     does not exist, anywhere, and never has
        USBTransport        same
        SharedFolderTransport  same
        "the Event Transport analysis"  not a file in `docs/`

    A reader who opens `interface.py` to find out what carries an Event
    between Desktops was told the question was open and pointed at four
    things that do not exist. Nothing checked it, because it is prose about
    a decision rather than a `docs/NN §M` citation — the shape
    `DocumentPointersResolveTests` resolves — and the decision left no trace
    in the file that describes the seam it settled.

    Two halves, because either alone goes stale:

        every `*Transport` the seam names must be a real class here
        the class `run_agent.py` builds must be one the seam names
    """

    PACKAGE = Path(__file__).resolve().parents[1] / "src" / "transport"
    RUN_AGENT = Path(__file__).resolve().parents[1] / "run_agent.py"

    #: `InMemoryNotionTransport` and friends live in `src/notion/` and answer
    #: a different interface entirely; this class is about `src/transport/`.
    NAME = re.compile(r"\b([A-Z][A-Za-z0-9]*Transport)\b")

    def _seam_text(self):
        """The module docstring plus the `Transport` class docstring —
        the two places the old claim lived."""
        source = (self.PACKAGE / "interface.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        chunks = [ast.get_docstring(tree) or ""]
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                chunks.append(ast.get_docstring(node) or "")
        return "\n".join(chunks)

    def _real_classes(self):
        from transport.interface import Transport as Seam

        found = {}
        for path in sorted(self.PACKAGE.glob("*.py")):
            module = __import__(f"transport.{path.stem}", fromlist=["*"])
            for name in dir(module):
                value = getattr(module, name)
                if (
                    isinstance(value, type)
                    and issubclass(value, Seam)
                    and value is not Seam
                ):
                    found[value.__name__] = value
        return found

    def test_the_package_really_has_the_two_implementations(self):
        """The antecedent. A discovery that came back empty would make both
        checks below pass over nothing."""
        found = self._real_classes()

        self.assertEqual(set(found), {"OneDriveTransport", "InMemoryTransport"})

    def test_every_transport_the_seam_names_exists(self):
        """The half that was wrong. Three of the four names in the old text
        had no class behind them.

        Reads `_live_claims()`, so the quoted record of those three names
        does not trip it — and there is **no phrase-based exemption**. The
        first draft had one ("skip if the text says 'never built'"), and it
        was vacuous: the phrase is in the correction, so the condition was
        false for every name and the set was always empty. A mutation that
        added `A GitHubTransport also delivers Events.` passed it. The
        distinction belongs in where the name sits, not in a nearby word.
        """
        named = set(self.NAME.findall(self._live_claims()))
        real = set(self._real_classes()) | {"Transport"}
        phantom = named - real

        self.assertEqual(
            phantom,
            set(),
            f"the Transport seam names a class that is not in {self.PACKAGE}: "
            f"{sorted(phantom)}. If it is a name being *recorded as absent*, "
            f"put it in an indented block — this check reads claims at the "
            f"margin.",
        )

    def test_the_seam_names_the_one_the_agent_actually_builds(self):
        """The other half. The class `run_agent.py` constructs is the
        production answer, and the seam has to say so — this is what turns
        "no concrete Transport is chosen yet" from prose nobody checks into
        a statement that fails."""
        source = self.RUN_AGENT.read_text(encoding="utf-8")
        built = {
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.endswith("Transport")
        }

        self.assertEqual(
            built,
            {"OneDriveTransport"},
            "run_agent.py builds a different set of Transports than this "
            "test knows about",
        )
        for name in built:
            with self.subTest(transport=name):
                self.assertIn(
                    name,
                    self._seam_text(),
                    f"{name} is what the Agent actually delivers through, and "
                    f"the seam that defines the interface does not mention it",
                )

    def _live_claims(self):
        """The seam's docstrings with every **indented block** removed.

        The correction quotes the false sentences on purpose, so a scan of
        the raw text fails on the record of the defect it is guarding —
        exactly the shape that already caught C118 §6 and C119 §4 in this
        session. An indented block inside a docstring is a quotation or an
        example; a claim this module is making sits at the margin.
        """
        return "\n".join(
            line
            for line in self._seam_text().splitlines()
            if not line.startswith("    ")
        )

    def test_the_quotation_stripper_still_leaves_the_claims(self):
        """Guards the guard. A stripper that removed everything would make
        both checks below pass over an empty string."""
        live = self._live_claims()

        self.assertIn("OneDriveTransport", live)
        self.assertIn("InMemoryTransport", live)
        # and it really did remove the quoted block
        self.assertNotIn(
            "No concrete production Transport is chosen yet", live
        )
        self.assertIn(
            "No concrete production Transport is chosen yet", self._seam_text()
        )

    def test_the_seam_does_not_claim_the_decision_is_still_open(self):
        """The exact sentences, because their shape is what made them
        survive: a decision recorded as pending outlives the decision (C76's
        lesson, one module over)."""
        live = self._live_claims()

        for stale in (
            "No concrete production Transport is chosen yet",
            "are chosen and built in a later phase",
            "can be dropped in later",
        ):
            with self.subTest(claim=stale):
                self.assertNotIn(stale, live)

    def test_the_citation_it_used_to_carry_still_points_at_no_document(self):
        """"the Event Transport analysis" was cited twice and is not a file
        in `docs/`. Asserted as a fact rather than left as a claim: if that
        document is ever written, this test says so and the citations can
        come back."""
        docs = Path(__file__).resolve().parents[1] / "docs"
        names = " ".join(path.name.lower() for path in docs.glob("*.md"))

        self.assertTrue(names, "no docs were found at all")
        self.assertNotIn("transport", names)
        # The **citation** form, not the phrase. The correction above names
        # the document in order to say it does not exist, and a check that
        # could not tell those apart would fail on its own record — the trap
        # this session walked into three times (C118 §6, C119 §4, and the
        # first draft of this class).
        self.assertNotIn("see the event transport analysis", self._live_claims().lower())



if __name__ == "__main__":
    unittest.main()
