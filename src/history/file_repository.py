"""Repository Runtime: atomic JSON-file storage for HistoryCandidate.

Only KEEP and REVIEW candidates are persisted — DROP data doesn't need to
be kept (docs/05_HISTORY_PIPELINE_SPEC.md section 49: the original
Execution Event is already the record of it).

This is not Company History. Nothing here writes Markdown, a Daily/Monthly
entry, or anything under the Desktop 4 Local Master
(D:\\DOJOONPASS_COO\\history\\) — that path is intentionally not used yet.
This Phase only uses runtime/history_candidates/{keep,review}/.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .repository import HistoryRepository
from .result import HistoryCandidate, HistoryDecision

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEEP_DIR = PROJECT_ROOT / "runtime" / "history_candidates" / "keep"
DEFAULT_REVIEW_DIR = PROJECT_ROOT / "runtime" / "history_candidates" / "review"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")

# Longest stem kept before the digest suffix. Windows rejects a path over
# ~260 characters (WinError 123), and the candidate directory itself already
# consumes part of that budget, so the stem is bounded well below the limit.
# Every real history_id ("HIST-" + a UUID = 41 characters) is far shorter.
_MAX_FILENAME_STEM = 120

# Byte-identical copy of `reporter.local_output.INCOMPLETE_WRITE_PREFIX` /
# `is_incomplete_write()`, which carries the full reasoning. Copied rather
# than imported because `history` may import only `events`
# (LayeringInvariantTests); `IncompleteWriteInvariantTests` asserts the
# copies agree.
INCOMPLETE_WRITE_PREFIX = ".tmp-"


def is_incomplete_write(name: str) -> bool:
    """Whether `name` is an atomic writer's staging file, not a finished artifact."""
    return name.startswith(INCOMPLETE_WRITE_PREFIX)


def safe_candidate_filename(history_id: str) -> str:
    """Derive a filesystem-safe filename from a `history_id`.

    `history_id` is `f"HIST-{event.event_id}"`, and `event_id` arrives from
    another Desktop through a shared folder — it is untrusted input that
    docs/02's schema constrains only to "present and non-null". Before this
    guard, a crafted id escaped the candidate directory entirely
    (`../../../PWNED` wrote into `runtime/history_candidates/`) and an id
    containing a character Windows forbids in a filename aborted the whole
    Runner with an OSError (Audit BUG-2 / BUG-5).

    CEO-approved B안: sanitise at the storage boundary rather than tightening
    the Event Schema, so docs/02 is untouched and no previously-valid Event
    becomes invalid. Same rule as
    `reporter.local_output.safe_event_filename()`, which already protects the
    Reporter's own write path.

    Two properties this must hold, and the reason for the digest suffix:

      1. An id that is already safe is returned UNCHANGED. Every real
         `history_id` in this project (`HIST-` + a UUID or a structured id)
         is already safe, so no existing candidate is ever renamed.

      2. Sanitising is many-to-one — `"HIST-   "` and `"HIST-  "` both reduce
         to `"HIST-"`, and two over-long ids share a truncated stem — and two
         candidates sharing a filename would collide on `save()`
         (FileExistsError, aborting the run). So whenever the name had to be
         changed at all, a short digest of the *original* id is appended,
         keeping distinct ids distinct.

    Length is bounded as well as content. `event_id` is unbounded in the
    schema, and a ~250-character id produced a path Windows rejects
    (WinError 123), which aborted the whole Runner at the History Filter step
    — the same class of failure this guard exists to prevent, just reached
    through length instead of illegal characters.
    """
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", history_id).strip("._")
    if sanitized == history_id and len(history_id) <= _MAX_FILENAME_STEM:
        return f"{history_id}.json"

    digest = hashlib.sha256(history_id.encode("utf-8")).hexdigest()[:12]
    stem = sanitized[:_MAX_FILENAME_STEM] or "candidate"
    return f"{stem}-{digest}.json"


class FileHistoryRepository(HistoryRepository):
    def __init__(self, keep_dir: Path | None = None, review_dir: Path | None = None):
        self.keep_dir = Path(keep_dir) if keep_dir is not None else DEFAULT_KEEP_DIR
        self.review_dir = Path(review_dir) if review_dir is not None else DEFAULT_REVIEW_DIR

    def _dir_for(self, decision: HistoryDecision) -> Path | None:
        if decision is HistoryDecision.KEEP:
            return self.keep_dir
        if decision is HistoryDecision.REVIEW:
            return self.review_dir
        return None

    def save(self, candidate: HistoryCandidate, *, overwrite: bool = False) -> bool:
        target_dir = self._dir_for(candidate.filter_result)
        if target_dir is None:
            return False

        target_dir.mkdir(parents=True, exist_ok=True)
        final_path = target_dir / safe_candidate_filename(candidate.history_id)
        if final_path.exists() and not overwrite:
            raise FileExistsError(f"history candidate already stored: {final_path}")

        fd, tmp_path = tempfile.mkstemp(
            dir=target_dir, prefix=INCOMPLETE_WRITE_PREFIX, suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(candidate.to_dict(), handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, final_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        return True

    def get(self, history_id: str) -> HistoryCandidate | None:
        for directory in (self.keep_dir, self.review_dir):
            # Must derive the name the same way save() does, or a sanitised
            # candidate would be unreachable by its own history_id.
            path = directory / safe_candidate_filename(history_id)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return HistoryCandidate.from_dict(data)
        return None

    def list(self, decision: HistoryDecision | None = None) -> list[HistoryCandidate]:
        if decision is HistoryDecision.KEEP:
            directories = [self.keep_dir]
        elif decision is HistoryDecision.REVIEW:
            directories = [self.review_dir]
        elif decision is None:
            directories = [self.keep_dir, self.review_dir]
        else:
            directories = []  # DROP -> nothing is ever stored there

        results: list[HistoryCandidate] = []
        for directory in directories:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.json")):
                if is_incomplete_write(path.name):
                    # `save()` stages into this same directory, so a run
                    # killed mid-write leaves a `.tmp-…json` here that
                    # `glob("*.json")` cannot tell from a stored Candidate.
                    # Reading it has two outcomes and both are wrong: a
                    # truncated file raises JSONDecodeError, which this
                    # method does not catch (BUG-38) and which therefore
                    # blocks every Candidate for that date; a file that was
                    # fully written but never `os.replace`d parses fine and
                    # returns the *same* Candidate twice, once under its real
                    # name and once under the staging name.
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append(HistoryCandidate.from_dict(data))
        return results
