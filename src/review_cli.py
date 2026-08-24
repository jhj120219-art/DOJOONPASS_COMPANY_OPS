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

import enum
import sys
from typing import Any, Callable, Sequence

# Same defensive fix as run_company_ops.py/init_notion.py (this Sprint's
# audit): every Korean string this file prints IS safe on a Korean
# Windows console's default cp949 codepage, so no crash is reproduced
# today -- but stdout's default strict error handling means any future
# non-cp949 character added to a print_fn() call (an em-dash, for
# instance, already present in this file's own docstrings/comments,
# just never printed) would crash this entry point the same way it did
# run_company_ops.py. Applied for consistency across all three CLI
# entry points, not in response to a reproduced failure here.
# `line_buffering=True` (C80): the same call the other four entrypoints
# make, for the same reason — block-buffered stdout under `> log 2>&1`
# lets stderr overtake it. The mixed-stream sequence that makes it
# visible elsewhere is `init_notion.py`'s; here the stream that would be
# swallowed is the **prompt**, which is worse than reordered: a redirected
# session would ask for Decision Context without showing the question.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

from cli import CONFIG_ERROR_EXIT, unexpected_arguments
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


class ReviewOutcome(enum.Enum):
    """What happened to one candidate.

    A plain bool used to be enough, when the only two answers were "saved"
    and "not saved". A save can now fail without ending the session, and
    that third answer must not be reported as a quiet skip — a failure and
    a deliberate skip look identical in a count.
    """

    SAVED = "SAVED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


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
) -> ReviewOutcome:
    print_fn(f"\n=== {candidate.history_id} ({candidate.filter_result.value}) ===")
    print_fn(f"Category: {candidate.category}")
    print_fn(f"Project: {candidate.project_id}")
    print_fn(f"Event ID: {candidate.event_id}")
    print_fn(f"Summary: {candidate.summary}")

    proceed = input_fn("이 항목을 검토하시겠습니까? (Enter=예, n=건너뛰기): ").strip().lower()
    if proceed == "n":
        print_fn("건너뜁니다.")
        return ReviewOutcome.SKIPPED

    updates: dict[str, Any] = {}
    for field_name, label in _REVIEW_FIELDS:
        value = _prompt_field(input_fn, print_fn, label, getattr(candidate, field_name))
        if value is not _SKIP:
            updates[field_name] = value

    if not updates:
        print_fn("변경 사항이 없습니다.")
        return ReviewOutcome.SKIPPED

    try:
        reviewer.submit_review(candidate.history_id, **updates)
    except Exception as exc:  # noqa: BLE001
        # One candidate's save failure must not end the session. This is the
        # same per-item isolation collector/runtime.py, outbox.drain(), and
        # monthly/generator.py all apply, and it matters more here than in
        # any of them: the text was typed by a person. An unhandled error
        # discarded what they had just written AND abandoned every remaining
        # candidate without ever offering it to them.
        #
        # The typed values are echoed back so the work is recoverable from
        # the terminal scrollback rather than simply gone. Decision Context
        # is what README RULE 11/12 call the company's most valuable asset;
        # losing a paragraph of it to a transient disk error is not an
        # acceptable failure mode.
        print_fn(f"[실패] {candidate.history_id} 저장하지 못했습니다: {exc}")
        print_fn("  입력한 내용은 아래와 같습니다. 다시 시도하거나 따로 보관하세요.")
        for field_name, label in _REVIEW_FIELDS:
            if field_name in updates:
                value = updates[field_name]
                print_fn(f"    {label}: {'(지움)' if value is None else value}")
        return ReviewOutcome.FAILED

    print_fn(f"저장되었습니다: {candidate.history_id}")
    return ReviewOutcome.SAVED


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
    failed: list[str] = []
    for candidate in candidates:
        outcome = _review_one(
            reviewer, candidate, input_fn=input_fn, print_fn=print_fn
        )
        if outcome is ReviewOutcome.SAVED:
            updated_count += 1
        elif outcome is ReviewOutcome.FAILED:
            failed.append(candidate.history_id)

    print_fn(f"\n리뷰 완료: {updated_count}건 저장됨 (총 {len(candidates)}건 중).")
    if failed:
        # Named again at the end: a failure printed thirty candidates ago has
        # scrolled off, and "저장됨 2건" alone reads like success.
        print_fn(f"저장 실패: {len(failed)}건 — {', '.join(failed)}")
    return updated_count


def main(argv: Sequence[str] = ()) -> int:
    """The entry point, and the argument refusal every sibling already had.

    **C79.** `cli.unexpected_arguments()` was written for the four tools at
    the repository root and this one was not among them — its roster is a
    hand-written tuple in `AnEntrypointRefusesArgumentsItCannotHonourTests`,
    and this file lives one directory down. Measured before the fix:

        python src/review_cli.py --help

    printed a real KEEP Candidate out of the operator's live
    `history_candidates/` and stopped at
    `이 항목을 검토하시겠습니까? (Enter=예, n=건너뛰기)`, waiting for
    the operator to start editing it. The argument was not rejected, not
    warned about, not read.

    That is `cli.py`'s own docstring, one tool over: *"An operator reaching
    for `--dry-run` ... is reaching for exactly the safety this had none
    of."* Here the thing on the other side of the prompt is Decision
    Context, which README RULE 11/12 call the company's most valuable
    asset, and which this file's own error path already goes out of its way
    to protect (`_review_one()` echoes typed values back rather than losing
    them).

    `configured_by=()` because this tool genuinely reads no environment
    variable — `FileHistoryRepository()` uses its own defaults.
    `unexpected_arguments()` already spells that case `(없음)`; it is the
    honest answer rather than a borrowed list from another tool.

    Returns a code, and the `__main__` guard raises it, for the reason the
    four siblings do: without `SystemExit` the refusal would print and then
    exit 0, which is the shape of the defect rather than the fix.
    """
    refusal = unexpected_arguments(argv, tool="review_cli.py", configured_by=())
    if refusal is not None:
        print(f"[FAILED] {refusal}", file=sys.stderr)
        return CONFIG_ERROR_EXIT

    repository = FileHistoryRepository()
    reviewer = RepositoryHistoryReviewer(repository)
    run_interactive_review(reviewer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
