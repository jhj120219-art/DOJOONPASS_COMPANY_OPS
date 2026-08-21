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
    SCHEMA_VERSION,
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
        # Read off the constant, not restated. A version bump should not
        # have to hunt for copies of itself in assertions about JSON, and
        # `TheManifestShapeIsPinnedToItsVersionTests` is what holds the
        # constant to meaning something.
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)

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

    def test_it_still_does_not_raise_when_the_cleanup_also_fails(self):
        """C49: the inner `except OSError` of the atomic idiom, here.

        `write_summary()` is called from `run_once()`'s `finally` — it is the
        one writer whose failure must never surface, because by the time it
        runs the History is already written and the Backup already pushed.
        The test above breaks the *write*; this breaks the write **and** the
        removal of its staging file, which on Windows is the same cause
        arriving twice (something holding both files open).

        A raise escaping here would take down a run that had already
        succeeded, which is exactly the inversion of README RULE 9 the
        docstring above refuses.
        """
        import os

        real_replace = os.replace
        real_remove = os.remove

        def failing_replace(src, dst):
            raise OSError(5, "destination held open")

        def failing_remove(path):
            raise OSError(32, "temp file held open too")

        os.replace = failing_replace
        os.remove = failing_remove
        self.addCleanup(setattr, os, "remove", real_remove)
        self.addCleanup(setattr, os, "replace", real_replace)

        write_summary(self.path, self.summary)  # must not raise

        self.assertFalse(self.path.exists(), "a failed write left a manifest")

    def test_a_failed_write_leaves_the_previous_manifest_alone(self):
        """`ops_status.py` reads this file. A failed write that truncated the
        last good manifest would replace "the previous run's result" with
        "no result", which is a worse answer than a stale one."""
        import os

        write_summary(self.path, self.summary)
        before = self.path.read_text(encoding="utf-8")

        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError(5, "destination held open")

        os.replace = failing_replace
        self.addCleanup(setattr, os, "replace", real_replace)

        write_summary(self.path, self.summary)  # must not raise

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

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


class TheManifestShapeIsPinnedToItsVersionTests(unittest.TestCase):
    """Drift detection for the Run Manifest, and it carries further than the
    Dashboard payload's.

    `to_payload()` is rebuilt every run and read by nothing that persists, so
    a shape change there is only a contract problem. This manifest is
    **written to disk** and read back by a later process — `ops_status.py`'s
    LAST RUN block, `run_company_ops.py`'s reporting — and after a restore
    the file can be older than the code reading it.

    `SCHEMA_VERSION` said "1.0" through C31 adding `failure.reason`, for the
    same reason the Dashboard's did: nothing compared the number to the
    shape. `read_summary()` handles the older shape correctly, which is why
    nobody noticed — and "handled correctly" was a claim about source text
    (`ReadSummaryValidatesOnlyTheThreeEnums` greps for `.get("reason", "")`)
    rather than a behaviour anybody drove. Both halves are here now.
    """

    #: Add an entry; never rename one — see
    #: `ThePayloadShapeIsPinnedToItsVersionTests.RECORDED` for what renaming
    #: cost the payload's gate.
    RECORDED = {
        # 1.0 is 1.1 minus `failure.reason`, which C31 added. It is the shape
        # `test_a_manifest_written_before_reason_existed_still_reads` writes
        # to disk and loads, so the two halves — "an old file still parses"
        # and "no key was dropped getting here" — are checked against the
        # same recorded fact.
        "1.0": {
            "top_level": [
                "components", "exit_code", "finished_at", "overall_status",
                "run_id", "schema_version", "started_at",
            ],
            "component": ["artifact_refs", "failure", "metrics", "name", "status"],
            "failure": ["classification", "retryability", "severity"],
        },
        "1.1": {
            "top_level": [
                "components", "exit_code", "finished_at", "overall_status",
                "run_id", "schema_version", "started_at",
            ],
            "component": ["artifact_refs", "failure", "metrics", "name", "status"],
            "failure": ["classification", "reason", "retryability", "severity"],
        }
    }

    def _summary(self):
        return RunSummary(
            run_id="RUN-1",
            started_at="2026-08-20T09:00:00+09:00",
            finished_at="2026-08-20T09:00:05+09:00",
            components=(
                ComponentResult(
                    name="transport",
                    status=ComponentStatus.SUCCESS,
                    metrics={"moved": 1},
                    artifact_refs=("events/transport/",),
                ),
                ComponentResult(
                    name="backup",
                    status=ComponentStatus.FAILED,
                    failure=Failure(
                        classification="BACKUP_FAILED",
                        severity=Severity.CRITICAL,
                        retryability=Retryability.RETRYABLE,
                        reason="remote unreachable",
                    ),
                ),
            ),
        )

    def _shape(self, data):
        failed = next(c for c in data["components"] if "failure" in c)
        return {
            "top_level": sorted(data),
            "component": sorted(failed),
            "failure": sorted(failed["failure"]),
        }

    def test_the_version_has_a_recorded_shape(self):
        self.assertIn(SCHEMA_VERSION, self.RECORDED)

    def test_the_manifest_still_has_that_shape(self):
        self.assertEqual(
            self._shape(self._summary().to_dict()), self.RECORDED[SCHEMA_VERSION]
        )

    def test_a_component_without_a_failure_omits_the_key(self):
        """`failure` is present only on a component that has one — the
        recorded `component` list is the failing shape, and a successful one
        is a strict subset. Stated so the fingerprint above cannot be read as
        "every component carries a null failure"."""
        data = self._summary().to_dict()
        healthy = next(c for c in data["components"] if c["name"] == "transport")

        self.assertNotIn("failure", healthy)
        self.assertEqual(
            set(healthy) | {"failure"}, set(self.RECORDED[SCHEMA_VERSION]["component"])
        )

    def test_the_removal_rule_actually_compared_something(self):
        """Same guard, same reason: a loop over no earlier version passes
        without checking anything."""
        major = SCHEMA_VERSION.split(".")[0]
        earlier = [
            version
            for version in self.RECORDED
            if version.split(".")[0] == major and version != SCHEMA_VERSION
        ]

        self.assertTrue(earlier, "no earlier manifest shape is recorded")

    def test_nothing_recorded_earlier_has_been_removed(self):
        """MAJOR's rule. An added key is invisible to a reader that ignores
        what it does not know; a removed one is a KeyError in a process that
        may be running older code against a newer file."""
        major = SCHEMA_VERSION.split(".")[0]
        current = self.RECORDED[SCHEMA_VERSION]

        for version, shape in self.RECORDED.items():
            if version.split(".")[0] != major or version == SCHEMA_VERSION:
                continue
            for section, keys in shape.items():
                with self.subTest(version=version, section=section):
                    self.assertEqual(set(keys) - set(current[section]), set())

    def test_the_version_is_a_major_minor_pair(self):
        major, _, minor = SCHEMA_VERSION.partition(".")

        self.assertTrue(major.isdigit(), SCHEMA_VERSION)
        self.assertTrue(minor.isdigit(), SCHEMA_VERSION)

    def test_a_manifest_written_before_reason_existed_still_reads(self):
        """The MINOR promise, driven rather than grepped.

        A manifest on disk can predate the code reading it — most obviously
        after a restore, and ordinarily whenever a machine is upgraded
        between runs. C31 added `failure.reason`; a file written before it
        has a `failure` object with three keys, and it must still load.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old_manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": "OLD-1",
                        "started_at": "2026-01-01T09:00:00+09:00",
                        "finished_at": "2026-01-01T09:00:05+09:00",
                        "components": [
                            {
                                "name": "backup",
                                "status": "FAILED",
                                "failure": {
                                    "classification": "BACKUP_FAILED",
                                    "severity": "CRITICAL",
                                    "retryability": "RETRYABLE",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = read_summary(path)

        self.assertEqual(summary.run_id, "OLD-1")
        self.assertEqual(summary.components[0].failure.reason, "")
        self.assertEqual(summary.schema_version, "1.0")

    def test_a_manifest_with_no_metrics_or_refs_still_reads(self):
        """The other two tolerant defaults, for the same reason. A component
        written before either existed is a two-key object."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "older_manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "run_id": "OLDER-1",
                        "started_at": "2026-01-01T09:00:00+09:00",
                        "finished_at": "2026-01-01T09:00:05+09:00",
                        "components": [{"name": "transport", "status": "SUCCESS"}],
                    }
                ),
                encoding="utf-8",
            )

            summary = read_summary(path)

        self.assertEqual(summary.components[0].metrics, {})
        self.assertEqual(summary.components[0].artifact_refs, ())
        # No `schema_version` at all — the reader defaults it to the current
        # one, which is the documented behaviour and the reason a *removal*
        # has to be a MAJOR bump: an unversioned file is read as current.
        self.assertEqual(summary.schema_version, SCHEMA_VERSION)

    def test_the_gate_would_notice_a_removed_key(self):
        """The detector detects."""
        data = self._summary().to_dict()
        data.pop("exit_code")

        self.assertNotEqual(self._shape(data), self.RECORDED[SCHEMA_VERSION])


if __name__ == "__main__":
    unittest.main()
