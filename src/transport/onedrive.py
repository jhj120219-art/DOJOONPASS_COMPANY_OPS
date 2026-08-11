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

import hashlib
import os
import re
import tempfile
from pathlib import Path

from events import Event

from .interface import Transport, TransportError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTGOING_DIR = PROJECT_ROOT / "runtime" / "events" / "outgoing"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")

# See history/file_repository.py for the same bound and the reason for it.
_MAX_FILENAME_STEM = 120


def safe_event_filename(event_id: str) -> str:
    """Derive a filesystem-safe filename from an `event_id`.

    Event ID 저장 보호 (CEO 승인 B안), sending side. `event_id` is untrusted —
    docs/02's schema constrains it only to "present and non-null" — and it used
    to be interpolated straight into a path, so an id like `../target/X` wrote
    *outside* the folder OneDrive watches (Audit BUG-15).

    Length is bounded as well as content. An unbounded id produced a path
    Windows rejects, and here that made `send()` raise TransportError — which
    means the Event could never be delivered to Desktop 4 at all, a silent
    loss at the sending end rather than a recoverable failure.

    Deliberately a local copy of the identical rule in
    `reporter.local_output.safe_event_filename()` rather than an import: this
    module must not depend on `reporter` (test_transport_onedrive.py asserts
    that boundary, and `reporter` already imports `transport` for the Transport
    seam, so importing back would be circular). This project already accepts
    exactly this trade — `ROLE_DISPLAY_NAMES` is duplicated between
    `daily/markdown.py` and `notion/properties.py` for the same reason.
    Both copies must stay in step; a test asserts they agree.
    """
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", event_id).strip("._")
    if sanitized == event_id and len(event_id) <= _MAX_FILENAME_STEM:
        return f"{event_id}.json"

    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:12]
    stem = sanitized[:_MAX_FILENAME_STEM] or "event"
    return f"{stem}-{digest}.json"


def _write_atomic(
    directory: Path, filename: str, content: str, *, overwrite: bool = False
) -> Path:
    """Write `content` to `directory/filename` via a same-directory temp file
    and one `os.replace()`.

    `overwrite` distinguishes the two directories this transport writes to,
    which differ in exactly one respect that matters — who else touches them.

    `overwrite=False` (the OneDrive sync folder): an existing entry is left
    alone. The sync folder is managed by the OneDrive client, so rewriting a
    file it may be mid-upload is the race the staging buffer exists to avoid
    (Phase 5.15). Skipping is the conservative choice there, and its known
    costs are characterized in
    `test_untrusted_event_input.OneDriveExistenceShortCircuitTests`
    (BUG-47's sync-folder half — still open, because narrowing the skip is a
    decision about that race, not a cleanup).

    `overwrite=True` (this transport's own `outgoing/` staging directory):
    an existing entry is residue from an earlier failed send, and the caller
    is handing us the authoritative content right now. Nothing else writes
    this directory, nothing reads it concurrently, and — by
    `test_nothing_ever_scans_the_outgoing_directory` — nothing scans it at
    all, so a stale file here was previously cleared by nothing, ever. The
    race reasoning above simply does not apply to a directory no other
    process knows about.
    """
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / filename
    if final_path.exists() and not overwrite:
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
        """Stage the Event, then publish it to the sync folder.

        BUG-47's worst facet, fixed: what reaches Desktop 4 is now the Event
        that was passed in. It used to be whatever `outgoing/<id>.json`
        happened to contain — `send()` staged the Event, then re-read the
        staged file and copied *that* to the sync folder, so a stale entry
        left by an earlier failed send short-circuited the staging write and
        was delivered in the Event's place. Measured: an outgoing file
        holding `{"stale": ...}` sent `{"stale": ...}` to Desktop 4, and
        `send()` returned None with no exception, so the Agent recorded a
        successful delivery and advanced its date.

        Two independent changes make that impossible, either of which would
        have been sufficient — kept together because they fix different
        halves of the same wrong assumption ("a file at this path must be
        ours"):

          * the staging write now overwrites, so residue cannot survive a
            re-send in a directory this code exclusively owns;
          * the sync write takes `event.to_json()` directly, so delivery no
            longer depends on the staging file's contents at all.

        Unchanged: the sync folder is still never overwritten, so the
        OneDrive race this staging buffer exists to avoid is untouched, and
        BUG-47's remaining facets (a directory, a Files On-Demand
        placeholder, or unrelated content already at the destination) still
        behave as characterized. Narrowing that skip needs a decision about
        the race, which is not this change.
        """
        filename = safe_event_filename(event.event_id)
        payload = event.to_json()
        try:
            _write_atomic(self.outgoing_dir, filename, payload, overwrite=True)
            _write_atomic(self.sync_folder, filename, payload)
        except OSError as exc:
            raise TransportError(
                f"failed to send event {event.event_id!r} via OneDrive: {exc}"
            ) from exc
