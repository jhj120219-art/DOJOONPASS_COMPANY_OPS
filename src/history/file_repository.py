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

import json
import os
import tempfile
from pathlib import Path

from .repository import HistoryRepository
from .result import HistoryCandidate, HistoryDecision

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEEP_DIR = PROJECT_ROOT / "runtime" / "history_candidates" / "keep"
DEFAULT_REVIEW_DIR = PROJECT_ROOT / "runtime" / "history_candidates" / "review"


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
        final_path = target_dir / f"{candidate.history_id}.json"
        if final_path.exists() and not overwrite:
            raise FileExistsError(f"history candidate already stored: {final_path}")

        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".json")
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
            path = directory / f"{history_id}.json"
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
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append(HistoryCandidate.from_dict(data))
        return results
