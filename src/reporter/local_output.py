"""Local, Transport-free JSON output for Reporter-generated Events.

This is NOT Event Transport: no network, no GitHub, no Notion, no other
Desktop involved. It only lets a locally generated Event be written to and
read back from a file inside this project's own `runtime/` area, so it can
be inspected or tested. `runtime/events/incoming/` is the same location
docs/03_COLLECTOR_SPEC.md defines as where not-yet-collected Events live;
an actual Collector is out of scope for this phase.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from events import Event

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVENT_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "events" / "incoming"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")

# See history/file_repository.py for the same bound and the reason for it.
_MAX_FILENAME_STEM = 120

# Every atomic writer in this project stages through
# `tempfile.mkstemp(dir=<the destination directory>, prefix=".tmp-")` and
# commits with one `os.replace()`. That prefix is therefore not decoration:
# it is the one mark that distinguishes "a write that has not finished" from
# "a finished artifact", and it is the only mark a *reader* has to go on.
#
# The failure path is already cleaned up (`except BaseException: os.remove`),
# so residue only survives a write the process never returned from — power
# loss, SIGKILL, a container stop. What made that worth naming here is what
# the readers do with the survivor: every scanner in this repository lists a
# directory by extension (`glob("*.json")`, `glob("*.md")`), and `.tmp-…json`
# matches `*.json`. A half-written file is then indistinguishable from a
# delivered Event, a stored Candidate, or a day of Company History.
#
# `is_incomplete_write()` is that distinction, published by the writer rather
# than re-derived by each reader. Modules that cannot import this one (the
# `transport`, `backup` and `history` leaves — see LayeringInvariantTests)
# carry a byte-identical copy, exactly as `safe_event_filename()` already
# does, and `IncompleteWriteInvariantTests` asserts every copy agrees with
# every writer's actual mkstemp prefix.
INCOMPLETE_WRITE_PREFIX = ".tmp-"


def is_incomplete_write(name: str) -> bool:
    """Whether `name` is an atomic writer's staging file, not a finished artifact."""
    return name.startswith(INCOMPLETE_WRITE_PREFIX)


def safe_event_filename(event_id: str) -> str:
    """Derive a Windows-safe filename from an event_id (never from summary/user text).

    Bounds both content and length. `event_id` is unbounded in docs/02's
    schema, and a ~250-character one produced a path Windows rejects
    (WinError 123) — here that aborted the Reporter's own write. When the name
    has to be changed at all, a short digest of the original id is appended,
    because both sanitising and truncating are many-to-one and two Events
    must never collide on one filename.

    Kept byte-for-byte identical to `transport.onedrive.safe_event_filename()`
    and in step with `history.file_repository.safe_candidate_filename()`;
    tests assert the two copies agree.
    """
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", event_id).strip("._")
    if sanitized == event_id and len(event_id) <= _MAX_FILENAME_STEM:
        return f"{event_id}.json"

    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:12]
    stem = sanitized[:_MAX_FILENAME_STEM] or "event"
    return f"{stem}-{digest}.json"


def write_event_json(
    event: Event,
    directory: Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write `event` as JSON under `directory` (default: runtime/events/incoming).

    Refuses to silently overwrite an existing Event file unless `overwrite=True`.
    """
    target_dir = Path(directory) if directory is not None else DEFAULT_EVENT_OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    final_path = target_dir / safe_event_filename(event.event_id)
    if final_path.exists() and not overwrite:
        raise FileExistsError(f"event file already exists: {final_path}")

    fd, tmp_path = tempfile.mkstemp(
        dir=target_dir, prefix=INCOMPLETE_WRITE_PREFIX, suffix=".json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(event.to_json())
        os.replace(tmp_path, final_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return final_path


def read_event_json(path: Path) -> Event:
    raw = Path(path).read_text(encoding="utf-8")
    return Event.from_json(raw)
