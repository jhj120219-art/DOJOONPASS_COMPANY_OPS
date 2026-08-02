"""OneDriveTransport (COO Architecture Decision, Phase 5.1 / 5.15 / 5.2).

Sends one Event by writing it, atomically, into a local `outgoing/`
staging buffer, then copying the completed file into the OneDrive Sync
Folder — a folder managed entirely by the OS-level OneDrive client, which
this class never talks to directly (no OneDrive API, no network code).
Actual cross-desktop delivery is OneDrive's job, not this class's — this
class's contract ends at "wrote a complete file into the folder OneDrive
is watching."

Per Phase 5.15: outgoing/ exists so the OneDrive client can never observe
a partially-written temp file — only fully-written Events are ever placed
where OneDrive can see them.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from events import Event

from .interface import Transport, TransportError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTGOING_DIR = PROJECT_ROOT / "runtime" / "events" / "outgoing"


def _write_atomic(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / filename
    if final_path.exists():
        # Already staged (e.g. a retried send) — the same event_id always
        # means the same content, so re-writing is unnecessary, not unsafe.
        return final_path

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, final_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return final_path


class OneDriveTransport(Transport):
    def __init__(self, sync_folder: Path, outgoing_dir: Path | None = None):
        self.sync_folder = Path(sync_folder)
        self.outgoing_dir = Path(outgoing_dir) if outgoing_dir is not None else DEFAULT_OUTGOING_DIR

    def send(self, event: Event) -> None:
        filename = f"{event.event_id}.json"
        try:
            outgoing_path = _write_atomic(self.outgoing_dir, filename, event.to_json())
            _write_atomic(self.sync_folder, filename, outgoing_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TransportError(
                f"failed to send event {event.event_id!r} via OneDrive: {exc}"
            ) from exc
