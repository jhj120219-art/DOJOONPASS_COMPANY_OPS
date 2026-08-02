"""Local, Transport-free JSON output for Reporter-generated Events.

This is NOT Event Transport: no network, no GitHub, no Notion, no other
Desktop involved. It only lets a locally generated Event be written to and
read back from a file inside this project's own `runtime/` area, so it can
be inspected or tested. `runtime/events/incoming/` is the same location
docs/03_COLLECTOR_SPEC.md defines as where not-yet-collected Events live;
an actual Collector is out of scope for this phase.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from events import Event

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVENT_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "events" / "incoming"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def safe_event_filename(event_id: str) -> str:
    """Derive a Windows-safe filename from an event_id (never from summary/user text)."""
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", event_id).strip("._") or "event"
    return f"{sanitized}.json"


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

    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".json")
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
