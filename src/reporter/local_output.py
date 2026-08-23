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

# Windows resolves these to devices, not files, regardless of extension or
# case — `NUL.json` and `NUL.anything.json` both name the NUL device, not a
# file on disk. `_UNSAFE_FILENAME_CHARS` never touches them because every
# character in "NUL" is on the whitelist. See `_has_reserved_windows_head()`.
_RESERVED_WINDOWS_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{d}" for d in range(1, 10)}
    | {f"LPT{d}" for d in range(1, 10)}
)


def _has_reserved_windows_head(name: str) -> bool:
    """Whether the text before `name`'s first '.' is a Windows device name.

    Measured directly on this project's own deployment target: a fresh,
    empty directory already reports `Path("NUL.json").exists() == True`,
    and `os.replace(tmp, "NUL.json")` raises `FileExistsError` (WinError
    183) even though nothing was ever written there and `overwrite=True`
    changes nothing — Windows refuses to replace a device path outright.
    Every character in "NUL" is on `_UNSAFE_FILENAME_CHARS`'s whitelist, so
    the sanitiser below passes such an `event_id` through unchanged, and
    the untouched name is exactly the one Windows treats as the device.
    """
    return name.split(".", 1)[0].upper() in _RESERVED_WINDOWS_STEMS

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
    and in step with `history.file_repository.safe_candidate_filename()`.
    `DuplicatedRulesStayInStepTests` compares the two copies over a shared
    corpus of adversarial ids — it did not exist until C38, so for several
    Sprints this sentence promised a check that nothing performed.
    """
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", event_id).strip("._")
    if (
        sanitized == event_id
        and len(event_id) <= _MAX_FILENAME_STEM
        and not _has_reserved_windows_head(event_id)
    ):
        return f"{event_id}.json"

    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:12]
    stem = sanitized[:_MAX_FILENAME_STEM] or "event"
    name = f"{stem}-{digest}.json"
    if _has_reserved_windows_head(name):
        # The digest is joined with '-', not '.', so this only triggers when
        # `stem` itself carries an embedded reserved head before a literal
        # '.' the whitelist let through untouched (e.g. "NUL.txt").
        name = f"_{name}"
    return name


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
            # Durability, not only atomicity — the canonical statement of it
            # for this project's fourteen atomic writers, which the others
            # point back at.
            #
            # `mkstemp` + `os.replace` buys *atomicity*: a reader never sees a
            # half-written file, because the name only ever points at a
            # complete one. It does not buy *durability*. Without this flush
            # the bytes live in the OS page cache while `os.replace()` is a
            # metadata operation NTFS journals, so the two can reach the disk
            # in either order. The order that hurts is rename-first: after a
            # power cut the file is there, under its real name, the right
            # size, and its contents are whatever was on those blocks —
            # usually zeros.
            #
            # That is not a hypothetical class of event for this repository.
            # `INCOMPLETE_WRITE_PREFIX` above already reasons about "a write
            # the process never returned from — power loss, SIGKILL, a
            # container stop", and follows the *staging* file through every
            # reader. This is the other half of the same accident, and it is
            # the worse half: a leftover `.tmp-…json` is at least visibly not
            # an artifact, while a zero-filled `2026-08-05.md` is a day of
            # Company History that every reader, detector and backup accepts
            # as the record. `_holes_in_the_daily_sequence()` looks for a
            # missing file, not an empty one, and Backup would commit and
            # push it.
            #
            # Cost, measured on this machine (200 writes of a small JSON
            # payload, local disk): 0.43 ms -> 1.12 ms per write. A run writes
            # on the order of tens of files, so this is tens of milliseconds
            # against a step already dominated by git.
            #
            # File only, not the directory. `os.fsync()` on a directory
            # handle is not supported on Windows (this project's target OS),
            # and the half that is portable is the half that matters here:
            # the data is on disk before any name points at it.
            handle.flush()
            os.fsync(handle.fileno())
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
