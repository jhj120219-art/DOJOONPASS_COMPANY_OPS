"""Work Signals the Multi-Desktop Agent turns into Execution Events.

Why a Signal file and not automatic detection
---------------------------------------------
README RULE 4 is explicit: "Reporter는 모든 PC 행동을 기록하지 않는다.
회사 상태를 변경할 의미 있는 Execution Event만 생성한다." — and
`reporter/reporter.py` states the same contract from the code side: it
"does not watch the filesystem, processes, keyboard, screen, or AI
conversations, and it does not infer that work happened — the caller
supplies the Signal explicitly."

Deciding *what a machine should treat as meaningful work* is therefore a
policy question this module does not get to answer. So the Agent does not
guess: each role's own workflow drops an explicit Signal file into

    runtime/agent/signals/<YYYY-MM-DD>/<anything>.json

and the Agent's job is to deliver those, reliably, for every date the PC
was off. A date with no Signal directory (or an empty one) is a normal
NO_ACTIVITY day, not an error and not a gap — see `agent.py`.

What a Signal may contain
-------------------------
Exactly the arguments `Reporter.report()` accepts, plus an optional
`timestamp`. Identity (`source`, `role`) is NEVER read from the Signal:
it comes from the Desktop Profile (`reporter/profiles.py`), so a Signal
file cannot claim to be from another Desktop or another role. `event_id`
is likewise not accepted — the Agent derives it deterministically
(see `agent.py`) so that a re-run after a crash produces the same id and
therefore cannot duplicate an Event.

Secret safety
-------------
docs/04 §56 and the project's standing rule: no token, key, or `.env`
value is ever collected, transported, or logged. A Signal is
operator-authored text that travels off this machine through OneDrive
into Company History, so it is scanned for secret-shaped content and
rejected outright if any is found. The Signal is left on disk for a human
to fix — never partially sent, never silently scrubbed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from oplog import SECRET_PATTERNS, SECRET_RE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIGNALS_DIR = PROJECT_ROOT / "runtime" / "agent" / "signals"

# Exactly `Reporter.report()`'s keyword arguments, minus the ones the Agent
# owns. `history_candidate` is required by `Reporter.report()` itself.
ALLOWED_SIGNAL_FIELDS = frozenset(
    {
        "project_id",
        "event_type",
        "status",
        "summary",
        "milestone",
        "blocker",
        "evidence",
        "history_candidate",
        "timestamp",
    }
)

REQUIRED_SIGNAL_FIELDS = ("project_id", "event_type", "status", "summary")

# Identity and identifiers a Signal is never allowed to set. Rejected loudly
# rather than ignored silently: a Signal that tries to set `role` is either a
# mistake worth surfacing or an attempt to attribute work to another role, and
# neither should look like a successful collection.
FORBIDDEN_SIGNAL_FIELDS = frozenset({"source", "role", "event_id", "schema_version"})

# Secret-shaped content now lives in `oplog.py`, which needs the same
# patterns to redact log output. Re-exported under the original private
# names so this module's callers and tests are unchanged: nothing about
# what a Signal may contain has moved or been relaxed.
_SECRET_PATTERNS = SECRET_PATTERNS
_SECRET_RE = SECRET_RE


class SignalError(ValueError):
    """Raised when a Signal file cannot be read as a valid Signal.

    `filename` and `reason` exist to build the message — `"<filename>:
    <reason>"` — which is what `agent.py` records in `DateResult.errors`
    and what an operator reads. Neither attribute is read back by anything.

    This docstring used to say `filename` was carried "so `agent.py` can
    route it to the rejected/ directory". It is not: `load_signals()`
    returns `(Path, SignalError)` pairs and `_collect_one_date()` routes
    with the `Path`, which is the right source — a `Path` can be moved and
    a name cannot. Corrected rather than deleted, because the claim was
    the kind that makes someone treat an unused attribute as load-bearing
    (found by sweeping for attributes nothing reads, C33 §6).

    The routing itself is unchanged and is the one this describes: a Signal
    that cannot be read moves to rejected/ the same way Collector Runtime
    routes a rejected Event file (docs/03 §7), instead of stalling the date
    forever.
    """

    def __init__(self, filename: str, reason: str):
        self.filename = filename
        self.reason = reason
        super().__init__(f"{filename}: {reason}")


@dataclass(frozen=True)
class Signal:
    """One validated Signal, ready to be handed to `Reporter.report()`.

    `signal_id` is the file's stem. It is part of the deterministic
    `event_id` derivation in `agent.py`, which is why it must be stable:
    renaming a Signal file after it has been sent produces a *different*
    Event, not a duplicate-suppressed one.

    `path` is carried rather than rebuilt from `signal_id` when the Signal
    later has to be moved. Reconstructing `<dir>/<stem>.json` is only
    correct while every Signal is named exactly that way, and a Signal
    that has to be rejected is precisely the one most likely not to be.
    """

    signal_id: str
    date: date_type
    payload: Mapping[str, Any]
    path: Path | None = None


def _iter_strings(value: Any):
    """Every string anywhere in `value`, walked with an explicit stack.

    Iterative rather than recursive on purpose. A Signal file is operator-
    authored but can also be corrupted, and deeply nested JSON is exactly
    the input that turns a recursive walk into a RecursionError — which
    would escape as an unhandled crash of the whole Agent run rather than
    a single rejected Signal. An explicit stack has no such ceiling.
    """
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
        elif isinstance(current, dict):
            for key, item in current.items():
                stack.append(key)
                stack.append(item)


def find_secret_material(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the secret-shaped patterns found anywhere in `payload`.

    Only the *pattern* is returned, never the matched text — reporting a
    detected secret must not itself become the thing that writes it into a
    log file.
    """
    found: list[str] = []
    for text in _iter_strings(dict(payload)):
        for pattern in _SECRET_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE) and pattern not in found:
                found.append(pattern)
    return tuple(found)


def redact(text: str) -> str:
    """Replace any secret-shaped run in `text` with `[REDACTED]`.

    Applied to operator-chosen values the Agent writes to its log —
    currently Signal filenames. A Signal's *content* is never logged at
    all, but a filename is, and nothing stops an operator from naming a
    file after the very token they were working with. Redacting costs one
    regex substitution and removes the one path by which the Agent's own
    log could become the leak.
    """
    return _SECRET_RE.sub("[REDACTED]", text)


def parse_signal(
    raw: str, *, signal_id: str, target_date: date_type, path: Path | None = None
) -> Signal:
    """Validate one Signal document. Raises SignalError, never anything else.

    Event-level rules (allowed `event_type`, BLOCKED needs a blocker, ...)
    are NOT re-implemented here — `events/schema.py` owns them and
    `Reporter.report()` applies them when the Event is built. This function
    checks only what is specific to being a *Signal*: the field set, the
    identity fields it may not claim, the date it belongs to, and secret
    safety.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SignalError(signal_id, f"not valid JSON ({exc})") from exc
    except RecursionError as exc:
        # `json.loads` raises RecursionError, not ValueError, on deeply
        # nested input. Uncaught, one corrupt Signal file would take down
        # the entire Agent run instead of being rejected on its own.
        raise SignalError(signal_id, "JSON is nested too deeply to parse") from exc

    if not isinstance(data, dict):
        raise SignalError(signal_id, "signal must be a JSON object")

    forbidden = sorted(FORBIDDEN_SIGNAL_FIELDS & data.keys())
    if forbidden:
        raise SignalError(
            signal_id,
            f"signal may not set identity fields {forbidden} — source/role come "
            f"from the Desktop Profile and event_id is derived by the Agent",
        )

    unknown = sorted(data.keys() - ALLOWED_SIGNAL_FIELDS)
    if unknown:
        raise SignalError(signal_id, f"unknown field(s): {unknown}")

    missing = [name for name in REQUIRED_SIGNAL_FIELDS if not data.get(name)]
    if missing:
        raise SignalError(signal_id, f"missing required field(s): {missing}")

    if "history_candidate" in data and not isinstance(data["history_candidate"], bool):
        raise SignalError(signal_id, "history_candidate must be a boolean")

    timestamp = data.get("timestamp")
    if timestamp is not None:
        if not isinstance(timestamp, str):
            raise SignalError(signal_id, "timestamp must be an ISO-8601 string")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise SignalError(signal_id, f"timestamp is not valid ISO-8601 ({exc})") from exc
        if parsed.tzinfo is None:
            raise SignalError(signal_id, "timestamp must include a timezone offset")
        if parsed.date() != target_date:
            # Daily History buckets by the Event's own timestamp (docs/06
            # §12). A Signal filed under 08-09 but stamped 08-10 would be
            # marked collected on one date and rendered on another — a
            # silent misfile, so it is refused instead.
            raise SignalError(
                signal_id,
                f"timestamp {timestamp!r} is not on the signal's date "
                f"{target_date.isoformat()}",
            )

    secrets = find_secret_material(data)
    if secrets:
        raise SignalError(
            signal_id,
            f"signal contains secret-shaped content matching {list(secrets)} — "
            f"refusing to collect it (docs/04 §56)",
        )

    return Signal(signal_id=signal_id, date=target_date, payload=data, path=path)


def load_signals(
    signals_dir: Path, target_date: date_type
) -> tuple[tuple[Signal, ...], tuple[tuple[Path, SignalError], ...]]:
    """Read every `*.json` Signal filed under `signals_dir/<target_date>/`.

    Returns `(valid, invalid)`. Loading is deliberately side-effect free:
    it never moves, rewrites, or deletes a Signal file. `agent.py` decides
    what to do with an invalid one, exactly as `collector/runtime.py`
    (routing) is separate from `collector/collector.py` (judging).

    A missing date directory is not an error — it is a NO_ACTIVITY day.
    Files are returned in sorted filename order so a re-run produces the
    same Events in the same order.
    """
    date_dir = Path(signals_dir) / target_date.isoformat()
    if not date_dir.is_dir():
        return (), ()

    valid: list[Signal] = []
    invalid: list[tuple[Path, SignalError]] = []

    for path in sorted(date_dir.glob("*.json")):
        if path.is_symlink():
            # `backup/working_copy.scan_for_secrets()` already treats any
            # link under a Company History tree as a hard failure, for the
            # reason that applies verbatim here: a link renamed to something
            # innocuous is invisible to every name-based check while its
            # *target's* content is what actually gets read and shipped.
            # Nothing under a Signals directory is expected to be a link, so
            # one is refused rather than followed.
            invalid.append(
                (path, SignalError(path.name, "signal is a symlink; refusing to follow it"))
            )
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            # `ValueError` covers `UnicodeDecodeError`. A Signal file written
            # by the operator's own tool in a legacy codepage, or truncated
            # mid-write, is not valid UTF-8 — and without this it escaped
            # `load_signals()` and aborted the Agent's whole run for that
            # date instead of rejecting the one Signal.
            #
            # That is the guarantee this loop exists to provide: one
            # unusable Signal is quarantined for a human and the rest of the
            # date proceeds.
            invalid.append((path, SignalError(path.name, f"could not read file ({exc})")))
            continue
        try:
            valid.append(
                parse_signal(
                    raw, signal_id=path.stem, target_date=target_date, path=path
                )
            )
        except SignalError as exc:
            invalid.append((path, exc))

    return tuple(valid), tuple(invalid)
