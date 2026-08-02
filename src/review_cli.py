"""History Review CLI (Phase 4.9).

A terminal entry point for a human to fill in the Decision Context fields
Phase 4.7 added to HistoryCandidate, through the Review Layer Phase 4.8
built. This file only lists KEEP/REVIEW candidates and calls
RepositoryHistoryReviewer.submit_review() — it never calls Daily
Generator, Scheduler, or Backup, and it never generates content itself
(no AI/auto-fill). Every value comes from what the human at the keyboard
types.

Run directly from inside src/, so the sibling `history` package resolves
without any sys.path tricks:

    python review_cli.py
"""

from __future__ import annotations

from typing import Any, Callable

from history import (
    FileHistoryRepository,
    HistoryCandidate,
    HistoryReviewer,
    RepositoryHistoryReviewer,
)

_SKIP: Any = object()
"""Returned by _prompt_field when the user left a field blank — meaning
"don't change this field", distinct from explicitly clearing it (`-`)."""

InputFn = Callable[[str], str]
PrintFn = Callable[..., None]

_REVIEW_FIELDS = (
    ("decision_context", "Decision Context"),
    ("expected_outcome", "Expected Outcome"),
    ("actual_outcome", "Actual Outcome"),
    ("lessons_learned", "Lessons Learned"),
)


def _prompt_field(
    input_fn: InputFn, print_fn: PrintFn, label: str, current_value: str | None
) -> Any:
    current_display = current_value if current_value is not None else "(없음)"
    print_fn(f"  {label} [현재: {current_display}]")
    raw = input_fn(f"  새 {label} (Enter=유지, -=지우기): ").strip()
    if raw == "":
        return _SKIP
    if raw == "-":
        return None
    return raw


def _review_one(
    reviewer: HistoryReviewer,
    candidate: HistoryCandidate,
    *,
    input_fn: InputFn,
    print_fn: PrintFn,
) -> bool:
    print_fn(f"\n=== {candidate.history_id} ({candidate.filter_result.value}) ===")
    print_fn(f"Category: {candidate.category}")
    print_fn(f"Project: {candidate.project_id}")
    print_fn(f"Event ID: {candidate.event_id}")
    print_fn(f"Summary: {candidate.summary}")

    proceed = input_fn("이 항목을 검토하시겠습니까? (Enter=예, n=건너뛰기): ").strip().lower()
    if proceed == "n":
        print_fn("건너뜁니다.")
        return False

    updates: dict[str, Any] = {}
    for field_name, label in _REVIEW_FIELDS:
        value = _prompt_field(input_fn, print_fn, label, getattr(candidate, field_name))
        if value is not _SKIP:
            updates[field_name] = value

    if not updates:
        print_fn("변경 사항이 없습니다.")
        return False

    reviewer.submit_review(candidate.history_id, **updates)
    print_fn(f"저장되었습니다: {candidate.history_id}")
    return True


def run_interactive_review(
    reviewer: HistoryReviewer,
    *,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
) -> int:
    """Review every KEEP/REVIEW candidate one at a time.

    Returns the count actually updated. Only ever calls
    reviewer.list_reviewable() and reviewer.submit_review() — no Daily
    Generator, Scheduler, or Backup call happens here.
    """
    candidates = reviewer.list_reviewable()
    if not candidates:
        print_fn("리뷰할 History Candidate가 없습니다.")
        return 0

    updated_count = 0
    for candidate in candidates:
        if _review_one(reviewer, candidate, input_fn=input_fn, print_fn=print_fn):
            updated_count += 1

    print_fn(f"\n리뷰 완료: {updated_count}건 저장됨 (총 {len(candidates)}건 중).")
    return updated_count


def main() -> None:
    repository = FileHistoryRepository()
    reviewer = RepositoryHistoryReviewer(repository)
    run_interactive_review(reviewer)


if __name__ == "__main__":
    main()
