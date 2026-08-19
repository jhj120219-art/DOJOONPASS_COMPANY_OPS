"""Daily History Generator (docs/06_DAILY_HISTORY_SPEC.md).

Reads KEEP candidates from a History Repository for one target date and
writes YYYY-MM-DD.md to `output_dir`. Read-only against the Repository —
never adds, deletes, or modifies a stored HistoryCandidate; only calls
`repository.list()` (unless a caller already has the KEEP list — see
`keep_candidates` below).

Architecture 개선(P1, CEO 승인 Sprint): a caller processing many dates in
one batch (Scheduler catch-up) can pass its own already-fetched KEEP list
via `keep_candidates` so this function skips calling `repository.list()`
itself — turning an O(days x History) full-repository re-read per date
into a single O(History) read shared across the whole batch, with an
O(days) in-memory filter per date. The `repository` parameter and its
`.list()` contract are unchanged; a caller that does not pass
`keep_candidates` still gets the exact prior behavior (its own fresh
`repository.list()` call, same as before this Sprint).

Per docs/06 section 5, the official Company History Local Master lives
outside this project's own directory tree ("프로그램과 History 분리").
This module does not resolve or hardcode that location itself — docs/05
section 69 and docs/08 section 91 both defer the concrete path to the
implementation/deployment step, and the existing `output_dir` parameter
is that seam: pass the real Local Master path there. When `output_dir`
is omitted, this function falls back to `DEFAULT_DAILY_DIR` (this
project's own runtime/daily/), which is a local/dev fallback only, not
the official Local Master. GitHub Backup, Scheduler, Transport, Notion,
and Monthly History are all out of scope here too.
"""

from __future__ import annotations

import enum
import os
import tempfile
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from history import HistoryCandidate, HistoryDecision, HistoryRepository

from .late_events import append_late_events, select_late_candidates
from .markdown import render_daily_markdown

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DAILY_DIR = PROJECT_ROOT / "runtime" / "daily"


def _candidate_date(candidate: HistoryCandidate) -> date_type:
    return datetime.fromisoformat(candidate.timestamp).date()


def build_keep_index(
    candidates: Sequence[HistoryCandidate],
) -> dict[date_type, list[HistoryCandidate]]:
    """Bucket KEEP candidates by their Event date, once, for reuse across a
    whole Scheduler catch-up batch (CEO Decision ②: "History Index를 1회만
    만들고 재사용한다").

    Without this, every date in the batch re-scanned the entire candidate
    list and re-parsed every `timestamp` (`_candidate_date` calls
    `datetime.fromisoformat` per candidate per date). The index parses each
    timestamp exactly once and turns each date's lookup into a dict hit.

    Selection and ordering are deliberately identical to the non-indexed
    path: same "Event timestamp falls on this date" rule (docs/06 §12),
    and each bucket is sorted by `timestamp` at render time exactly as
    before — so the rendered Markdown is byte-for-byte unchanged.
    """
    index: dict[date_type, list[HistoryCandidate]] = {}
    for candidate in candidates:
        index.setdefault(_candidate_date(candidate), []).append(candidate)
    return index


def generate_daily_history(
    repository: HistoryRepository,
    target_date: date_type,
    *,
    output_dir: Path | None = None,
    generated_at: str | None = None,
    overwrite: bool = False,
    keep_candidates: Sequence[HistoryCandidate] | None = None,
    keep_index: Mapping[date_type, Sequence[HistoryCandidate]] | None = None,
) -> Path:
    """Render and atomically write the Daily History file for `target_date`.

    Only KEEP candidates whose Event `timestamp` falls on `target_date`
    (not the date this function happens to run on) are included, per
    docs/06_DAILY_HISTORY_SPEC.md section 12.

    Three input paths, all producing identical output for the same data:

        `keep_index`      — pre-built date -> candidates index (fastest;
                            what Scheduler passes, CEO Decision ②)
        `keep_candidates` — pre-fetched flat KEEP list, filtered here
        neither           — `repository.list(decision=KEEP)` called fresh
                            right here, exactly as before this Sprint

    `keep_index` wins when both are given. A date missing from the index
    simply has no KEEP candidates — the same Empty Day outcome the other
    two paths produce for that date (docs/06 §25).
    """
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_DAILY_DIR
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")

    if keep_index is not None:
        matching_candidates = list(keep_index.get(target_date, ()))
    else:
        source_candidates = (
            keep_candidates
            if keep_candidates is not None
            else repository.list(decision=HistoryDecision.KEEP)
        )
        matching_candidates = [
            candidate
            for candidate in source_candidates
            if _candidate_date(candidate) == target_date
        ]
    # By instant, not by the raw timestamp string — see
    # `HistoryCandidate.chronological_key`. Two Events on the same day with
    # different UTC offsets sorted backwards as text.
    matching_candidates.sort(key=lambda candidate: candidate.chronological_key)

    markdown = render_daily_markdown(target_date, matching_candidates, generated_at)

    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{target_date.isoformat()}.md"
    # The non-file case is tested FIRST, and deliberately so. Both branches
    # raise `FileExistsError`, but they are different facts and only one of
    # them needs a person: "that day is already written" is the normal
    # catch-up outcome, while "something that is not a file is sitting where
    # that day belongs" is an anomaly no run can clear. Checked second, the
    # generic message won and the operator was told the day already existed —
    # about a directory. Under `overwrite=True` the alternative was worse
    # still: `os.replace()` failed with a bare `Errno 13 Permission denied`
    # naming a temp path.
    if final_path.exists() and not final_path.is_file():
        raise FileExistsError(
            f"a non-file is in the way of daily history: {final_path} — a day of "
            "Company History cannot be written there until it is moved"
        )
    if final_path.exists() and not overwrite:
        raise FileExistsError(f"daily history already exists: {final_path}")

    fd, tmp_path = tempfile.mkstemp(dir=output_dir, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(markdown)
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
    return final_path


class LateUpdateOutcome(enum.Enum):
    """docs/06 §65's per-date result vocabulary, narrowed to this function's
    two possible answers plus the two ways it can decline to act."""

    UPDATED_LATE_EVENT = "UPDATED_LATE_EVENT"
    NO_LATE_EVENTS = "NO_LATE_EVENTS"
    NO_EXISTING_FILE = "NO_EXISTING_FILE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class LateUpdateResult:
    date: date_type
    outcome: LateUpdateOutcome
    added_event_ids: tuple[str, ...] = ()
    path: Path | None = None
    error: str | None = None


def update_daily_history(
    repository: HistoryRepository,
    target_date: date_type,
    *,
    output_dir: Path | None = None,
    now: datetime | None = None,
    keep_candidates: Sequence[HistoryCandidate] | None = None,
) -> LateUpdateResult:
    """Add any Late KEEP Events to an already-written Daily History (docs/06 §37).

    This closes the gap docs/06 §36 names: an Event dated 08-05 that arrives
    on 08-07 is accepted, filtered, stored in keep/, and synced to Notion,
    but `scheduler.run_once()` skips any date whose `.md` already exists and
    `generate_daily_history()` refuses to overwrite — so Company History
    never receives it while every other indicator reports success. With four
    Desktops the triggering condition (one machine offline across a Daily
    Close) is routine rather than exceptional.

    Deliberately narrow and side-effect-free unless there is real work:

        no existing file      -> NO_EXISTING_FILE, nothing written
                                 (that date is `generate_daily_history()`'s
                                  job, not this one's)
        nothing new           -> NO_LATE_EVENTS, nothing written
        genuinely late Events -> the file is rewritten atomically with the
                                 original content preserved verbatim and the
                                 new items appended (see late_events.py)

    Never raises for an I/O or rendering failure — docs/06 §41 requires that
    a History write failure leave the existing History intact, delete no
    candidate, and be retried on the next run. Returning FAILED gives the
    caller that behaviour for free: nothing was written, nothing advanced,
    and the same Events are still pending next time.
    """
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_DAILY_DIR
    now = now or datetime.now().astimezone()
    final_path = output_dir / f"{target_date.isoformat()}.md"

    # `is_file()`, not `exists()`: this function's whole precondition is
    # "there is an already-written day here to add to". A directory named
    # `2026-08-20.md` is not one, and treating it as one produced
    # `FAILED: [Errno 13] Permission denied` naming a temp path — a report
    # about the wrong thing. `NO_EXISTING_FILE` is the honest answer, and it
    # routes the problem to `generate_daily_history()`, which now says
    # exactly what is in the way.
    if not final_path.is_file():
        return LateUpdateResult(date=target_date, outcome=LateUpdateOutcome.NO_EXISTING_FILE)

    try:
        existing = final_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        # `ValueError` covers `UnicodeDecodeError`, which is what an existing
        # Daily that is not valid UTF-8 raises — and `OSError` alone did not
        # catch it, so it escaped this function entirely, contradicting the
        # "Never raises for an I/O or rendering failure" contract three
        # paragraphs up.
        #
        # Measured through the real Runner: the exception left `run_once()`
        # from step 6.5, so Monthly, **Backup** and Dashboard never started —
        # 6 of 9 components recorded, no commit, no Dashboard row. A
        # component docs/14 §5 classifies as DEGRADED was aborting one it
        # classifies as CRITICAL, which inverts the entire point of having
        # the two severities.
        #
        # Exactly the defect `monthly/generator._existing_generated_at()`
        # already fixed for the identical reason ("it used to catch only
        # `OSError`, so a previous Monthly that was not valid UTF-8 raised
        # `UnicodeDecodeError` (a ValueError) out of here and failed the
        # entire rebuild"). Same shape, other module.
        return LateUpdateResult(
            date=target_date, outcome=LateUpdateOutcome.FAILED, error=str(exc)
        )

    try:
        source = (
            keep_candidates
            if keep_candidates is not None
            else repository.list(decision=HistoryDecision.KEEP)
        )
        for_this_date = [
            candidate for candidate in source if _candidate_date(candidate) == target_date
        ]
    except Exception as exc:  # noqa: BLE001  (§41: report, never crash the run)
        return LateUpdateResult(
            date=target_date, outcome=LateUpdateOutcome.FAILED, error=str(exc)
        )

    # Inside the guard, not between two of them. This line sat in the gap
    # between the "read the Repository" try above and the "render and write"
    # try below, so the docstring's promise three paragraphs up — "Never
    # raises for an I/O or rendering failure" — was not actually true of the
    # whole function. `select_late_candidates()` parses the existing document
    # (`existing_event_ids()`) and sorts by `chronological_key`, both over
    # content this function did not produce and docs/06 §57 lets a human
    # edit.
    #
    # The caller has a belt for this now too (`app/runner.py`'s step 6.5),
    # but a function whose contract says it does not raise should not need
    # one — and until both existed, an escape here took Backup down with it.
    try:
        late = select_late_candidates(existing, for_this_date)
    except Exception as exc:  # noqa: BLE001  (§41: report, never crash the run)
        return LateUpdateResult(
            date=target_date, outcome=LateUpdateOutcome.FAILED, error=str(exc)
        )
    if not late:
        return LateUpdateResult(
            date=target_date, outcome=LateUpdateOutcome.NO_LATE_EVENTS, path=final_path
        )

    try:
        updated = append_late_events(
            existing, late, now_iso=now.isoformat(timespec="seconds")
        )
        fd, tmp_path = tempfile.mkstemp(dir=output_dir, prefix=".tmp-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(updated)
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
    except Exception as exc:  # noqa: BLE001
        return LateUpdateResult(
            date=target_date, outcome=LateUpdateOutcome.FAILED, error=str(exc)
        )

    return LateUpdateResult(
        date=target_date,
        outcome=LateUpdateOutcome.UPDATED_LATE_EVENT,
        added_event_ids=tuple(candidate.event_id for candidate in late),
        path=final_path,
    )
