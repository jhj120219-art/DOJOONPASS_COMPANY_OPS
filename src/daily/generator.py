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

import os
import tempfile
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from history import HistoryCandidate, HistoryDecision, HistoryRepository

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
    matching_candidates.sort(key=lambda candidate: candidate.timestamp)

    markdown = render_daily_markdown(target_date, matching_candidates, generated_at)

    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{target_date.isoformat()}.md"
    if final_path.exists() and not overwrite:
        raise FileExistsError(f"daily history already exists: {final_path}")

    fd, tmp_path = tempfile.mkstemp(dir=output_dir, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        os.replace(tmp_path, final_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return final_path
