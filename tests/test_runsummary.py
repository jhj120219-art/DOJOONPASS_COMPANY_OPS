"""Run Contract — `src/runsummary.py`.

The Run Summary is a **manifest**, not a log: it says what each component
did and names where the detail lives (`artifact_refs`), rather than
reproducing the detail. These tests pin that distinction and the arithmetic
that turns component outcomes into a process exit code:

    Failure -> Classification -> Severity / Retryability
            -> Overall Status -> Exit Code

`tests/test_run_contract.py` covers the same contract end to end through
`app.runner.run_once()`; this file covers the vocabulary in isolation.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from runsummary import (  # noqa: E402
    EXIT_DEGRADED,
    EXIT_FAILED,
    EXIT_SUCCESS,
    ComponentResult,
    ComponentStatus,
    Failure,
    OverallStatus,
    Retryability,
    RunSummary,
    RunSummaryError,
    Severity,
    exit_code_for,
    overall_status,
    read_summary,
    write_summary,
)


def _failure(severity=Severity.DEGRADED, retry=Retryability.RETRYABLE, reason="boom"):
    return Failure(
        classification="TEST_FAILURE",
        severity=severity,
        retryability=retry,
        reason=reason,
    )


def _component(name, status, failure=None, **metrics):
    return ComponentResult(name=name, status=status, failure=failure, metrics=metrics)


class ComponentResultInvariantTests(unittest.TestCase):
    """A FAILED component without a classified failure would silently break
    the whole derivation — `overall_status()` could not tell a Notion outage
    from a lost Daily Close, and would have to guess. So the type refuses to
    exist in that shape."""

    def test_failed_without_a_failure_is_rejected(self):
        with self.assertRaises(ValueError):
            ComponentResult(name="daily", status=ComponentStatus.FAILED)

    def test_success_carrying_a_failure_is_rejected(self):
        """The other direction, which would be just as misleading to read."""
        with self.assertRaises(ValueError):
            ComponentResult(
                name="daily", status=ComponentStatus.SUCCESS, failure=_failure()
            )

    def test_skipped_carrying_a_failure_is_rejected(self):
        with self.assertRaises(ValueError):
            ComponentResult(
                name="notion_sync", status=ComponentStatus.SKIPPED, failure=_failure()
            )


class OverallStatusTests(unittest.TestCase):
    def test_all_success_is_success(self):
        components = [
            _component("transport", ComponentStatus.SUCCESS),
            _component("collector", ComponentStatus.SUCCESS),
        ]
        self.assertEqual(overall_status(components), OverallStatus.SUCCESS)

    def test_a_skipped_component_never_degrades_the_run(self):
        """The load-bearing case. An unconfigured Notion is a supported
        deployment (docs/04: `notion_sync=None`이면 그 단계를 건너뛴다), and
        README RULE 9 keeps Company History recording before Notion exists.
        Reporting SKIPPED as a fault would make every pre-Notion install
        look broken forever."""
        components = [
            _component("collector", ComponentStatus.SUCCESS),
            _component("notion_sync", ComponentStatus.SKIPPED),
            _component("dashboard", ComponentStatus.SKIPPED),
        ]
        self.assertEqual(overall_status(components), OverallStatus.SUCCESS)

    def test_a_non_critical_failure_degrades_rather_than_fails(self):
        components = [
            _component("collector", ComponentStatus.SUCCESS),
            _component(
                "notion_sync",
                ComponentStatus.FAILED,
                _failure(severity=Severity.DEGRADED),
            ),
        ]
        self.assertEqual(overall_status(components), OverallStatus.DEGRADED)

    def test_a_critical_failure_fails_the_run(self):
        components = [
            _component("collector", ComponentStatus.SUCCESS),
            _component(
                "daily", ComponentStatus.FAILED, _failure(severity=Severity.CRITICAL)
            ),
        ]
        self.assertEqual(overall_status(components), OverallStatus.FAILED)

    def test_critical_wins_over_degraded_regardless_of_order(self):
        """Order-independence matters: components are appended in pipeline
        order, and a Notion failure in step 4 must not mask a Daily failure
        in step 6 — nor be masked by it."""
        critical = _component(
            "daily", ComponentStatus.FAILED, _failure(severity=Severity.CRITICAL)
        )
        degraded = _component(
            "notion_sync", ComponentStatus.FAILED, _failure(severity=Severity.DEGRADED)
        )

        self.assertEqual(overall_status([critical, degraded]), OverallStatus.FAILED)
        self.assertEqual(overall_status([degraded, critical]), OverallStatus.FAILED)

    def test_no_components_is_success(self):
        """What a lock-skipped run looks like. It genuinely did nothing
        wrong, and reporting a second Runner's normal back-off as a failure
        would make every overlapping schedule look broken."""
        self.assertEqual(overall_status([]), OverallStatus.SUCCESS)


class ExitCodeTests(unittest.TestCase):
    def test_the_three_codes(self):
        self.assertEqual(exit_code_for(OverallStatus.SUCCESS), EXIT_SUCCESS)
        self.assertEqual(exit_code_for(OverallStatus.DEGRADED), EXIT_DEGRADED)
        self.assertEqual(exit_code_for(OverallStatus.FAILED), EXIT_FAILED)

    def test_success_is_zero_and_the_others_are_not(self):
        self.assertEqual(EXIT_SUCCESS, 0)
        self.assertNotEqual(EXIT_DEGRADED, 0)
        self.assertNotEqual(EXIT_FAILED, 0)

    def test_one_is_reserved_for_configuration_errors(self):
        """`run_company_ops.py` uses 1 for "the run never started". A run
        that finished must never report it, or a scheduled task's history
        becomes unreadable."""
        self.assertNotIn(1, {exit_code_for(s) for s in OverallStatus})

    def test_the_summary_exposes_its_own_exit_code(self):
        summary = RunSummary(
            run_id="R-1",
            started_at="2026-08-11T10:00:00+09:00",
            finished_at="2026-08-11T10:00:05+09:00",
            components=(
                _component(
                    "backup",
                    ComponentStatus.FAILED,
                    _failure(severity=Severity.CRITICAL, retry=Retryability.PERMANENT),
                ),
            ),
        )

        self.assertEqual(summary.overall_status, OverallStatus.FAILED)
        self.assertEqual(summary.exit_code, EXIT_FAILED)


class SerialisationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "runs" / "last_run.json"
        self.summary = RunSummary(
            run_id="R-1",
            started_at="2026-08-11T10:00:00+09:00",
            finished_at="2026-08-11T10:00:05+09:00",
            components=(
                ComponentResult(
                    name="collector",
                    status=ComponentStatus.SUCCESS,
                    metrics={"accepted": 2},
                    artifact_refs=("logs/collector.log",),
                ),
                ComponentResult(
                    name="notion_sync",
                    status=ComponentStatus.FAILED,
                    failure=_failure(reason="503"),
                    artifact_refs=("logs/notion_sync.log",),
                ),
                ComponentResult(name="dashboard", status=ComponentStatus.SKIPPED),
            ),
        )

    def test_a_summary_round_trips(self):
        write_summary(self.path, self.summary)

        loaded = read_summary(self.path)

        self.assertEqual(loaded.run_id, "R-1")
        self.assertEqual(loaded.overall_status, OverallStatus.DEGRADED)
        self.assertEqual(loaded.component("collector").metrics["accepted"], 2)
        self.assertEqual(loaded.component("notion_sync").failure.reason, "503")
        self.assertEqual(loaded.component("dashboard").status, ComponentStatus.SKIPPED)

    def test_the_overall_status_and_exit_code_are_written_not_only_derived(self):
        """An operator reads this file with `cat`, and so does anything that
        is not Python. The verdict has to be in it, not recomputable from
        it."""
        write_summary(self.path, self.summary)

        data = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(data["overall_status"], "DEGRADED")
        self.assertEqual(data["exit_code"], EXIT_DEGRADED)
        self.assertEqual(data["schema_version"], "1.0")

    def test_artifact_refs_are_relative_not_absolute(self):
        """A manifest may be read on another machine, and an absolute path
        from this one is worse than useless there."""
        write_summary(self.path, self.summary)

        data = json.loads(self.path.read_text(encoding="utf-8"))
        for component in data["components"]:
            for ref in component["artifact_refs"]:
                with self.subTest(ref=ref):
                    self.assertFalse(Path(ref).is_absolute())
                    self.assertNotIn(":", ref)

    def test_a_missing_file_reads_as_none(self):
        self.assertIsNone(read_summary(self.path))

    def test_a_corrupted_file_is_named_not_guessed(self):
        """Same contract as the five state loaders (docs/10 §46)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(RunSummaryError):
            read_summary(self.path)

    def test_a_corrupted_file_is_never_deleted(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(RunSummaryError):
            read_summary(self.path)

        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")

    def test_writing_never_raises_even_when_it_cannot_write(self):
        """The manifest reports on a run whose real output is already
        durable. Failing that run because the report could not be filed
        would invert README RULE 9 — the same trade `oplog.append_line()`
        makes. Here the target path is a directory."""
        self.path.mkdir(parents=True, exist_ok=True)

        write_summary(self.path, self.summary)  # must not raise

    def test_a_partially_written_file_is_never_observed(self):
        """Atomic replace, like every state writer here: `ops_status.py` may
        read this while a Runner is mid-write, and half a manifest would be
        worse than none."""
        write_summary(self.path, self.summary)
        leftovers = list(self.path.parent.glob(".tmp-*"))

        self.assertEqual(leftovers, [])
        self.assertEqual(read_summary(self.path).run_id, "R-1")


class ManifestNotALogTests(unittest.TestCase):
    """The rule the whole module exists to hold: summarise, reference the
    detail, never reproduce it."""

    def test_a_summary_has_no_per_event_detail(self):
        summary = RunSummary(
            run_id="R-1",
            started_at="2026-08-11T10:00:00+09:00",
            finished_at="2026-08-11T10:00:05+09:00",
            components=(
                ComponentResult(
                    name="collector",
                    status=ComponentStatus.SUCCESS,
                    metrics={"accepted": 500, "duplicate": 0, "rejected": 0, "failed": 0},
                    artifact_refs=("logs/collector.log", "events/processed/"),
                ),
            ),
        )

        rendered = summary.to_json()

        # 500 Events summarised in one component, and the file stays small:
        # a manifest that grew with the workload would be a log.
        self.assertLess(len(rendered), 1000)
        self.assertIn("events/processed/", rendered)

    def test_failures_are_listed_for_a_reporter(self):
        summary = RunSummary(
            run_id="R-1",
            started_at="2026-08-11T10:00:00+09:00",
            finished_at="2026-08-11T10:00:05+09:00",
            components=(
                _component("collector", ComponentStatus.SUCCESS),
                _component("notion_sync", ComponentStatus.FAILED, _failure()),
                _component("dashboard", ComponentStatus.SKIPPED),
            ),
        )

        self.assertEqual([c.name for c in summary.failures()], ["notion_sync"])


if __name__ == "__main__":
    unittest.main()
