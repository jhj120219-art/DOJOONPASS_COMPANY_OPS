"""Daily History Generator (docs/06_DAILY_HISTORY_SPEC.md).

Reads KEEP candidates from a History Repository for one target date and
writes runtime/daily/YYYY-MM-DD.md. Read-only against the Repository —
never adds, deletes, or modifies a stored HistoryCandidate; only calls
`repository.list()`.

Desktop 4 Local Master (D:\\DOJOONPASS_COO\\history\\daily\\) is
intentionally NOT used yet — this Phase only writes inside the project's
own runtime/daily/. GitHub Backup, Scheduler, Transport, Notion, and
Monthly History are all out of scope here too.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from history import HistoryCandidate, HistoryDecision, HistoryRepository

from .markdown import render_daily_markdown

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DAILY_DIR = PROJECT_ROOT / "runtime" / "daily"


def _candidate_date(candidate: HistoryCandidate) -> date_type:
    return datetime.fromisoformat(candidate.timestamp).date()


def generate_daily_history(
    repository: HistoryRepository,
    target_date: date_type,
    *,
    output_dir: Path | None = None,
    generated_at: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Render and atomically write the Daily History file for `target_date`.

    Only KEEP candidates whose Event `timestamp` falls on `target_date`
    (not the date this function happens to run on) are included, per
    docs/06_DAILY_HISTORY_SPEC.md section 12.
    """
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_DAILY_DIR
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")

    keep_candidates = [
        candidate
        for candidate in repository.list(decision=HistoryDecision.KEEP)
        if _candidate_date(candidate) == target_date
    ]
    keep_candidates.sort(key=lambda candidate: candidate.timestamp)

    markdown = render_daily_markdown(target_date, keep_candidates, generated_at)

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
