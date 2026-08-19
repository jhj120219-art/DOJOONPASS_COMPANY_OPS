import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import (  # noqa: E402
    Event,
    EventValidationError,
    create_event,
    generate_event_id,
    validate_event,
)


def sample_data(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-MILESTONE-001",
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
    return data


class EventCreationTests(unittest.TestCase):
    def test_valid_started_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-START-001",
                event_type="STARTED",
                status="IN_PROGRESS",
                blocker=None,
                evidence=[],
                history_candidate=False,
            )
        )
        self.assertEqual(event.event_type, "STARTED")

    def test_valid_blocked_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-BLOCK-001",
                event_type="BLOCKED",
                status="BLOCKED",
                blocker="auction_item synchronization mismatch",
                history_candidate=True,
            )
        )
        self.assertEqual(event.blocker, "auction_item synchronization mismatch")

    def test_valid_resumed_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-RESUME-001",
                event_type="RESUMED",
                status="IN_PROGRESS",
                blocker=None,
                history_candidate=False,
            )
        )
        self.assertIsNone(event.blocker)

    def test_valid_milestone_completed_event(self):
        event = Event.from_dict(sample_data())
        self.assertEqual(event.milestone, "Search UI")

    def test_valid_completed_event(self):
        event = Event.from_dict(
            sample_data(
                event_id="TEST-COMPLETE-001",
                event_type="COMPLETED",
                status="COMPLETED",
                milestone="Search Frontend Integration",
                evidence=["TypeScript PASS", "Integration Test PASS"],
            )
        )
        self.assertEqual(event.status, "COMPLETED")


class EventValidationTests(unittest.TestCase):
    def test_missing_required_field_rejected(self):
        data = sample_data()
        del data["summary"]
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_invalid_event_type_rejected(self):
        data = sample_data(event_type="RANDOM_TYPE")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_invalid_status_rejected(self):
        data = sample_data(status="ALMOST_DONE")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_invalid_timestamp_rejected(self):
        for bad_timestamp in ("2026-08-01", "8월 1일", "오후 8시", "not-a-date"):
            with self.subTest(bad_timestamp=bad_timestamp):
                data = sample_data(timestamp=bad_timestamp)
                with self.assertRaises(EventValidationError):
                    Event.from_dict(data)

    def test_a_z_suffixed_utc_timestamp_agrees_with_every_downstream_parser(self):
        """docs/02_EVENT_SCHEMA.md section 7 requires "ISO-8601" and never
        disallows the trailing 'Z' (Zulu/UTC) designator -- it IS valid
        ISO-8601, equivalent to '+00:00', and extremely common (JavaScript's
        `Date.prototype.toISOString()`, many JSON APIs emit it by default).

        This used to be a CHARACTERIZATION test asserting flat rejection,
        because `_timestamp_error()` validates with
        `datetime.fromisoformat()` and Python < 3.11 could not parse 'Z'.
        The reason given for not fixing it was that
        `daily/generator.py::_candidate_date()` and the other direct
        `fromisoformat()` call sites would then crash on a stored value
        validation had already accepted -- i.e. the real requirement was
        never "reject Z", it was "validation and every downstream parser
        must agree".

        On Python 3.11+ `fromisoformat()` accepts 'Z' at every one of those
        call sites at once, so that agreement now holds by construction.
        What this test pins is the agreement itself, on any Python: whatever
        validation decides about 'Z', the downstream parser decides the
        same. A future Python or a hand-rolled parser that breaks the tie
        fails here rather than in a Daily History run.
        """
        z_timestamp = "2026-08-01T20:31:00Z"
        data = sample_data(timestamp=z_timestamp)

        try:
            datetime.fromisoformat(z_timestamp)
        except ValueError:
            downstream_can_parse = False
        else:
            downstream_can_parse = True

        if downstream_can_parse:
            event = Event.from_dict(data)
            self.assertEqual(event.timestamp, z_timestamp)
        else:
            with self.assertRaises(EventValidationError):
                Event.from_dict(data)

    def test_blocked_without_blocker_rejected(self):
        data = sample_data(event_type="BLOCKED", status="BLOCKED", blocker=None)
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_completed_status_mismatch_rejected(self):
        data = sample_data(event_type="COMPLETED", status="NOT_STARTED")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_cancelled_status_mismatch_rejected(self):
        data = sample_data(event_type="CANCELLED", status="IN_PROGRESS")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_unsupported_schema_version_rejected(self):
        data = sample_data(schema_version="2.0")
        with self.assertRaises(EventValidationError):
            Event.from_dict(data)

    def test_validate_event_reports_multiple_errors(self):
        data = sample_data(event_type="BAD_TYPE", status="BAD_STATUS")
        del data["project_id"]
        errors = validate_event(data)
        self.assertGreaterEqual(len(errors), 3)


class EventIdentityTests(unittest.TestCase):
    def test_generated_event_ids_are_unique(self):
        ids = {generate_event_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_event_id_is_preserved(self):
        event = create_event(
            source="DESKTOP_3",
            role="CTO_FRONTEND",
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Search UI implementation started",
            history_candidate=False,
            event_id="TEST-START-001",
        )
        self.assertEqual(event.event_id, "TEST-START-001")

    def test_create_event_generates_id_and_timestamp_when_omitted(self):
        event = create_event(
            source="DESKTOP_3",
            role="CTO_FRONTEND",
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="Search UI implementation started",
            history_candidate=False,
        )
        self.assertTrue(event.event_id)
        parsed = datetime.fromisoformat(event.timestamp)
        self.assertIsNotNone(parsed.tzinfo)


class EventSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_meaning(self):
        original = Event.from_dict(sample_data())
        restored = Event.from_json(original.to_json())
        self.assertEqual(original, restored)

    def test_round_trip_preserves_unicode(self):
        data = sample_data(
            summary="검색 UI 구현 완료",
            blocker=None,
            milestone="검색 UI",
        )
        original = Event.from_dict(data)
        raw = original.to_json()
        self.assertNotIn("\\u", raw)
        restored = Event.from_json(raw)
        self.assertEqual(restored.summary, "검색 UI 구현 완료")
        self.assertEqual(restored.milestone, "검색 UI")


class DeclaredStringFieldsAreEnforcedTests(unittest.TestCase):
    """REGRESSION. docs/02 §4's table says `string`; the validator did not.

    Every other typed field was enforced — `timestamp` through
    `_timestamp_error()`, `milestone`/`blocker` and `evidence` and
    `history_candidate` directly, and `source`/`role`/`event_type`/`status`
    implicitly, because a non-string cannot be a member of a frozenset of
    strings. `event_id`, `project_id` and `summary` were checked for presence
    only, so any JSON type passed.

    That is a trust boundary. An Event is JSON written on another Desktop
    that crosses OneDrive, and the Signal layer does not close the gap
    either: `agent.signals.parse_signal()` validates the field *set*, not the
    field types.

    What it cost, measured through the real Runner with one crafted Event
    beside one ordinary one (`test_runner_notion_integration.py::
    ANonStringFieldIsRejectedNotCrashedIntoTests` runs it):

        summary=12345    ACCEPTED -> KEEP Candidate stored -> daily FAILED
                         -> 0 Daily files, exit 2, and again on every run
        project_id=7     ACCEPTED -> notion_sync and daily both FAILED
        event_id=99      TypeError escapes run_once() from a sorted() over
                         mixed int and str ids

    Empty strings are deliberately untouched: `""` is still valid, which is
    BACKLOG A-15's separate and still-open question.
    """

    def _valid(self, **overrides):
        data = {
            "schema_version": "1.0",
            "event_id": "EVT-1",
            "timestamp": "2026-08-05T10:00:00+09:00",
            "source": "DESKTOP_1",
            "role": "CTO_BACKEND",
            "project_id": "SEARCH_FRONTEND",
            "event_type": "STARTED",
            "status": "IN_PROGRESS",
            "summary": "did work",
            "evidence": [],
            "history_candidate": True,
        }
        data.update(overrides)
        return data

    FIELDS = ("event_id", "project_id", "summary")
    NON_STRINGS = (12345, 1.5, {"t": "x"}, ["a"], True)

    def test_a_baseline_event_is_valid(self):
        self.assertEqual(validate_event(self._valid()), [])

    def test_every_declared_string_field_refuses_every_non_string(self):
        for field_name in self.FIELDS:
            for value in self.NON_STRINGS:
                with self.subTest(field=field_name, value=type(value).__name__):
                    errors = validate_event(self._valid(**{field_name: value}))

                    self.assertTrue(errors, f"{field_name}={value!r} was accepted")
                    self.assertTrue(
                        any(field_name in error for error in errors),
                        f"the error does not name the field: {errors}",
                    )

    def test_the_docs_table_is_what_this_enforces(self):
        """The three names are not a guess — they are the rows docs/02 §4
        marks `string` that nothing else in this function covers."""
        docs = Path(__file__).resolve().parents[1] / "docs"
        table = (docs / "02_EVENT_SCHEMA.md").read_text(encoding="utf-8")

        for field_name in self.FIELDS:
            with self.subTest(field=field_name):
                self.assertIn(f"| {field_name} | string |", table)

    def test_an_empty_string_is_still_accepted(self):
        """A-15 is about emptiness and is untouched. If this starts failing,
        that decision was taken and BACKLOG must record it."""
        for field_name in self.FIELDS:
            with self.subTest(field=field_name):
                self.assertEqual(validate_event(self._valid(**{field_name: ""})), [])

    def test_a_missing_field_still_reads_as_missing_not_as_a_type_error(self):
        """One error per problem: `None` is the absence the required-field
        loop already reports, so the type check must not double up on it."""
        for field_name in self.FIELDS:
            with self.subTest(field=field_name):
                errors = validate_event(self._valid(**{field_name: None}))

                self.assertEqual(errors, [f"missing required field: {field_name}"])

    def test_the_other_typed_fields_were_already_covered(self):
        """The half that says why only three names were added — a sweep, not
        a guess."""
        cases = {
            "milestone": 1,
            "blocker": 1,
            "evidence": [1],
            "history_candidate": "yes",
            "timestamp": 5,
            "source": 5,
            "role": 5,
            "event_type": 5,
            "status": 5,
            "schema_version": 5,
        }
        for field_name, value in cases.items():
            with self.subTest(field=field_name):
                data = self._valid(**{field_name: value})
                if field_name == "blocker":
                    data["event_type"] = "BLOCKED"

                self.assertTrue(validate_event(data), f"{field_name} was accepted")

if __name__ == "__main__":
    unittest.main()
