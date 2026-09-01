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
from .result import HistoryCandidate, HistoryDecision, candidate_errors

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEEP_DIR = PROJECT_ROOT / "runtime" / "history_candidates" / "keep"
DEFAULT_REVIEW_DIR = PROJECT_ROOT / "runtime" / "history_candidates" / "review"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")

# Windows resolves these to devices, not files, regardless of extension or
# case — `NUL.json` and `NUL.anything.json` both name the NUL device, not a
# file on disk. `_UNSAFE_FILENAME_CHARS` never touches them because every
# character in "NUL" is on the whitelist. Every real caller already passes
# a `history_id` beginning "HIST-" (never itself a reserved name), so this
# is defense in depth for the sanitiser's general contract rather than a
# reachable production path — kept in step with the other two storage
# boundaries, `reporter.local_output._has_reserved_windows_head()` and
# `transport.onedrive._has_reserved_windows_head()`.
_RESERVED_WINDOWS_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{d}" for d in range(1, 10)}
    | {f"LPT{d}" for d in range(1, 10)}
)


def _has_reserved_windows_head(name: str) -> bool:
    """Whether the text before `name`'s first '.' is a Windows device name."""
    return name.split(".", 1)[0].upper() in _RESERVED_WINDOWS_STEMS

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


class HistoryCandidateError(ValueError):
    """A stored Candidate file that cannot be turned into a HistoryCandidate.

    Carries the path, because that is the whole point of it existing. Before
    it, the same condition surfaced as a bare `KeyError('summary')` or, worse,
    as a `TypeError` thrown by the Markdown renderer three steps later —
    measured, `sequence item 2: expected str instance, int found`, which
    reached the Run Manifest's `reason` and `daily_late_update.log` naming
    neither the file nor the field.

    `ValueError` subclass on purpose, so every caller that already writes
    `except ValueError` around a repository read keeps catching it —
    `scheduler._generate_pending_dates()` wraps `repository.list()` in
    `except Exception` and turns it into `SchedulerRunResult(FAILED, error=...)`,
    which is how this message reaches the operator. Same relationship
    `monthly.parser.DailyParseError` has to its own readers.
    """

    def __init__(self, path, errors):
        self.path = path
        self.errors = list(errors)
        super().__init__(f"unusable history candidate: {path} ({'; '.join(self.errors)})")


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
    if (
        sanitized == history_id
        and len(history_id) <= _MAX_FILENAME_STEM
        and not _has_reserved_windows_head(history_id)
    ):
        return f"{history_id}.json"

    digest = hashlib.sha256(history_id.encode("utf-8")).hexdigest()[:12]
    stem = sanitized[:_MAX_FILENAME_STEM] or "candidate"
    name = f"{stem}-{digest}.json"
    if _has_reserved_windows_head(name):
        name = f"_{name}"
    return name


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
                # Durability, not only atomicity — see reporter/local_output.py.
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, final_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        return True

    def _candidate_from(self, path: Path) -> HistoryCandidate:
        """Parse one stored Candidate, or raise naming the file.

        Every failure mode of this read used to reach the caller as something
        that did not say where it came from: a `JSONDecodeError` (BUG-38), a
        `KeyError` for a missing field, or — for a wrong-typed field —
        nothing at all here, because `from_dict()` type-checks nothing and the
        Markdown renderer is what eventually died.

        The validation is `result.candidate_errors()`, shared with
        `ops_status._read_keep_candidates()` so the status view and the
        pipeline cannot disagree about which files are usable. It is
        deliberately the blocking set only — see that function.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise HistoryCandidateError(path, [f"could not read file ({exc})"]) from exc
        try:
            data = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise HistoryCandidateError(path, [f"not valid JSON ({exc})"]) from exc
        if not isinstance(data, dict):
            raise HistoryCandidateError(path, ["candidate must be a JSON object"])
        errors = candidate_errors(data)
        if errors:
            raise HistoryCandidateError(path, errors)
        return HistoryCandidate.from_dict(data)

    def get(self, history_id: str) -> HistoryCandidate | None:
        for directory in (self.keep_dir, self.review_dir):
            # Must derive the name the same way save() does, or a sanitised
            # candidate would be unreachable by its own history_id.
            path = directory / safe_candidate_filename(history_id)
            if path.exists():
                return self._candidate_from(path)
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
            # Listed explicitly so a directory that exists but cannot be
            # read raises instead of coming back empty. `Path.glob()`
            # swallows the `OSError` it meets while scanning, and an empty
            # result here does not mean "no Company History for this day" —
            # it is what `scheduler.py` turns into a rendered *empty day*
            # before advancing its watermark past it. Measured, one KEEP
            # Candidate in a `keep/` denied to this user:
            #
            #     status COMPLETED   generated ['2026-08-29', '2026-08-30']
            #     2026-08-29.md      "No material company history recorded."
            #
            # `list()` takes no date, so a single unreadable directory does
            # that to **every** pending date in the batch.
            #
            # Raising is the whole fix: `scheduler.run_once()` already wraps
            # this call in a `try/except` that returns FAILED without
            # generating anything, and its comment says why ("repository.
            # list()도 다른 단계와 동일하게 실패를 감춰서는 안 된다"). The
            # contract was already right; the failure never reached it.
            os.listdir(directory)
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
                results.append(self._candidate_from(path))
        return results
