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
        # "OneDrive" alone is not checked here: it's a legitimate Transport
        # candidate name to mention in comments (see Event Transport analysis).
        # Only literal path-shaped usage would indicate a hardcoded path.
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


if __name__ == "__main__":
    unittest.main()
