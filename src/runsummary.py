"""Run Contract — the canonical Run Summary (Run Manifest).

A Run Summary is **not a log**. It is the manifest of one execution: what
each component did, whether the run as a whole succeeded, and — for the
detail — where the evidence lives. Detail is referenced, never inlined:

    Run Summary   what happened, in one screen        (this module)
    artifact_ref  where to read the detail            -> Execution Evidence
    Execution Evidence   the logs and Event files a run produced
    Company Repository   Daily/Monthly History — Policy / Knowledge truth
    Operational Projection   Notion — a view, never a source

Why it exists: measured across `app/runner.py` and `run_company_ops.py`,
12 of 23 fields on the pipeline's own result objects were computed and then
discarded at process exit (BUG-39). They are the diagnostics — which Events
did not make it in and why, the entire Backup Log docs/08 §68 specifies.
That was never several missing features; it was one missing sink. This is
the sink.

Layering: this module imports nothing from the project, exactly like
`oplog`. It defines vocabulary and arithmetic, not policy about any
particular pipeline step — `app/runner.py` maps its own results onto these
types. That keeps it below every package and unable to close a cycle.
"""

from __future__ import annotations

import enum
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "1.0"


class ComponentStatus(enum.Enum):
    """What one pipeline step did.

    Three values, and the third is load-bearing: `SKIPPED` is not a soft
    failure. Notion being unconfigured is a supported deployment (docs/04:
    `notion_sync=None`이면 그 단계를 건너뛴다), and reporting it as a failure
    would make an ordinary pre-Notion install look broken forever.
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class OverallStatus(enum.Enum):
    """What the run as a whole did.

    `DEGRADED` is the value the system was missing. Without it every run is
    either fine or broken, and this pipeline's whole design is that most
    failures are neither: README RULE 5 puts Notion off the History critical
    path, and RULE 9 keeps Company History recording even when everything
    downstream of it is down. Collapsing that into SUCCESS hides real
    breakage; collapsing it into FAILED cries wolf until nobody looks.
    """

    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class Severity(enum.Enum):
    """Whether a component's failure threatens the run's purpose.

    The purpose is Company History. A step that records it or protects it is
    CRITICAL; a step that projects it elsewhere is not. This is the
    codification of README RULE 5, not a new judgement.
    """

    CRITICAL = "CRITICAL"
    DEGRADED = "DEGRADED"


class Retryability(enum.Enum):
    """Whether doing the same thing again could work.

    Kept separate from `Severity` because they answer different questions
    and a failure needs both: severity decides the exit code, retryability
    decides whether an operator should act now or wait. BUG-13 is exactly
    what conflating them costs — a permanently-rejected Notion payload and a
    transient 503 both reported as `NOTION_RETRY_REQUIRED`, so the queue
    re-sent the permanent one every run forever and nothing said to stop
    waiting.
    """

    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Failure:
    """Why a component failed, classified.

    `reason` is free-form text of external origin (an exception message, a
    remote response body). It is bounded and escaped by `oplog` on the way
    to a log; here it is bounded by the caller before construction, because
    a manifest that can grow without limit is a manifest that fills a disk.
    """

    classification: str
    severity: Severity
    retryability: Retryability
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "severity": self.severity.value,
            "retryability": self.retryability.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ComponentResult:
    """One pipeline step's line in the manifest.

    `artifact_refs` is the whole point of the "summary, not log" rule: the
    step says what it did in one line and names where the detail is, rather
    than copying the detail here.
    """

    name: str
    status: ComponentStatus
    failure: Failure | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: Sequence[str] = ()

    def __post_init__(self) -> None:
        # A FAILED component without a classified failure would defeat the
        # entire exit-code derivation below: `overall_status()` could not
        # tell a Notion outage from a lost Daily Close.
        if self.status is ComponentStatus.FAILED and self.failure is None:
            raise ValueError(f"component {self.name!r} is FAILED but carries no Failure")
        if self.status is not ComponentStatus.FAILED and self.failure is not None:
            raise ValueError(f"component {self.name!r} is {self.status.value} but carries a Failure")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "status": self.status.value,
            "metrics": dict(self.metrics),
            "artifact_refs": list(self.artifact_refs),
        }
        if self.failure is not None:
            data["failure"] = self.failure.to_dict()
        return data


# Exit codes. Task Scheduler's only automatic health signal is this number
# ("Last Run Result"), and stdout is not captured by default — so this is the
# single channel that reports without anyone reading anything.
#
# 1 is absent on purpose: `run_company_ops.py` already uses it for a
# configuration error, which happens before a run exists to summarise. 3
# matches `ops_status.py`'s existing "something needs a person", so the two
# entrypoints agree on what a 3 means.
EXIT_SUCCESS = 0
EXIT_FAILED = 2
EXIT_DEGRADED = 3

_EXIT_CODES = {
    OverallStatus.SUCCESS: EXIT_SUCCESS,
    OverallStatus.DEGRADED: EXIT_DEGRADED,
    OverallStatus.FAILED: EXIT_FAILED,
}


def exit_code_for(status: OverallStatus) -> int:
    """The last link of Failure -> Classification -> Severity -> Overall -> Exit."""
    return _EXIT_CODES[status]


def overall_status(components: Sequence[ComponentResult]) -> OverallStatus:
    """Fold component results into one verdict.

    The rule, in full: any CRITICAL failure makes the run FAILED; any other
    failure makes it DEGRADED; otherwise SUCCESS. `SKIPPED` never degrades
    anything — an unconfigured Notion is a supported deployment, not a fault.

    A run with no components at all is SUCCESS rather than an error: that is
    what a lock-skipped run looks like, and it genuinely did nothing wrong.
    """
    verdict = OverallStatus.SUCCESS
    for component in components:
        if component.status is not ComponentStatus.FAILED:
            continue
        assert component.failure is not None  # guaranteed by __post_init__
        if component.failure.severity is Severity.CRITICAL:
            return OverallStatus.FAILED
        verdict = OverallStatus.DEGRADED
    return verdict


@dataclass(frozen=True)
class RunSummary:
    """One execution, summarised. The Run Manifest.

    Deliberately NOT a log: no per-Event lines, no timestamps per operation,
    no message text beyond a bounded failure reason. Everything else is an
    `artifact_ref` pointing at Execution Evidence that already exists — the
    logs and Event directories the pipeline was already writing.
    """

    run_id: str
    started_at: str
    finished_at: str
    components: tuple[ComponentResult, ...] = ()
    schema_version: str = SCHEMA_VERSION

    @property
    def overall_status(self) -> OverallStatus:
        return overall_status(self.components)

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.overall_status)

    def component(self, name: str) -> ComponentResult | None:
        return next((c for c in self.components if c.name == name), None)

    def failures(self) -> tuple[ComponentResult, ...]:
        return tuple(c for c in self.components if c.status is ComponentStatus.FAILED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_status": self.overall_status.value,
            "exit_code": self.exit_code,
            "components": [c.to_dict() for c in self.components],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def write_summary(path: Path, summary: RunSummary) -> None:
    """Persist one Run Summary atomically.

    Same tempfile + `os.replace` idiom every state writer in this repository
    uses, for the same reason: a half-written manifest read by
    `ops_status.py` would be worse than none.

    Unlike a state file, this never raises. The manifest is a report about a
    run that has already finished and whose real output is already durable —
    failing the run because the report could not be filed would invert the
    priority this system is built on (README RULE 9: Data Safety over
    Convenience). A missing manifest costs visibility; that is the same
    trade `oplog.append_line()` makes.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(summary.to_json())
                # Durability, not only atomicity — see reporter/local_output.py.
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
    except OSError:
        pass


class RunSummaryError(ValueError):
    """Raised when a run summary file exists but cannot be read as one.

    Same contract as `collector.state.CollectorStateError` and its four
    siblings (docs/10 §46): a damaged file is a normal operational situation
    to be *reported*, and is never deleted by the program.
    """


def read_summary(path: Path) -> RunSummary | None:
    """Load a Run Summary. `None` if the file does not exist."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunSummaryError(f"run summary file is corrupted: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise RunSummaryError(f"run summary must contain a JSON object: {path}")

    try:
        components = tuple(
            ComponentResult(
                name=c["name"],
                status=ComponentStatus(c["status"]),
                failure=(
                    Failure(
                        classification=c["failure"]["classification"],
                        severity=Severity(c["failure"]["severity"]),
                        retryability=Retryability(c["failure"]["retryability"]),
                        reason=c["failure"].get("reason", ""),
                    )
                    if c.get("failure")
                    else None
                ),
                metrics=c.get("metrics", {}),
                artifact_refs=tuple(c.get("artifact_refs", ())),
            )
            for c in data.get("components", [])
        )
        return RunSummary(
            run_id=data["run_id"],
            started_at=data["started_at"],
            finished_at=data["finished_at"],
            components=components,
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RunSummaryError(f"run summary is malformed: {path} ({exc})") from exc


def now_iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")
