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
from dataclasses import dataclass
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
from oplog import SECRET_RE
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


def _echo_typed(updates: dict, print_fn: PrintFn) -> None:
    """Print back what the person typed for a candidate that was not saved.

    Two callers reach here — a save that raised, and input that ended mid
    candidate — and both mean the same thing: this text exists nowhere but
    the terminal. Decision Context is what README RULE 11/12 call the
    company's most valuable asset; losing a paragraph of it is not an
    acceptable failure mode, and one copy of the sentence is how the two
    paths stay saying it the same way.
    """
    if not updates:
        return
    print_fn("  입력한 내용은 아래와 같습니다. 다시 시도하거나 따로 보관하세요.")
    for field_name, label in _REVIEW_FIELDS:
        if field_name in updates:
            value = updates[field_name]
            print_fn(f"    {label}: {'(지움)' if value is None else value}")


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

    updates: dict[str, Any] = {}
    try:
        proceed = input_fn(
            "이 항목을 검토하시겠습니까? (Enter=예, n=건너뛰기): "
        ).strip().lower()
        if proceed == "n":
            print_fn("건너뜁니다.")
            return ReviewOutcome.SKIPPED

        for field_name, label in _REVIEW_FIELDS:
            value = _prompt_field(
                input_fn, print_fn, label, getattr(candidate, field_name)
            )
            if value is not _SKIP:
                updates[field_name] = value
    except (EOFError, KeyboardInterrupt):
        # The input ran out — or a person pressed Ctrl+C — part way through
        # this candidate. C117 found this by running the real command:
        #
        #     printf 'n\nn\nn\n' | python src/review_cli.py
        #
        # ended on the fourth candidate with a raw `EOFError` traceback and
        # exit 1, which this project reserves for a configuration error. Any
        # non-terminal invocation reaches it — a pipe, a redirect, a task
        # with no console.
        #
        # What is echoed here is the same thing the save-failure path below
        # echoes, for the same reason and it is the reason this branch
        # exists at all: whatever the person had already typed for *this*
        # candidate was never passed to `submit_review()`, so the terminal is
        # the only place it still is. Printing it before re-raising is the
        # difference between "the session ended" and "the paragraph is gone".
        if updates:
            print_fn("")
            print_fn(f"[중단] {candidate.history_id} 은(는) 저장되지 않았습니다.")
            _echo_typed(updates, print_fn)
        raise

    if not updates:
        print_fn("변경 사항이 없습니다.")
        return ReviewOutcome.SKIPPED

    # Secret-shaped prose, named before it is stored (C125).
    #
    # **This is the door nothing was watching.** `_secret_shaped_event_content()`
    # in `ops_status.py` writes down two ways text reaches Company History —
    # a Signal typed here, which `find_secret_material()` refuses outright,
    # and an Event from another Desktop, which nothing reads but that
    # detector reports. Decision Context is a third, and it had neither.
    # Measured end to end in a temp tree: a token typed into
    # `decision_context` is accepted unscanned, stored, invisible to the
    # Event detector (it reads `processed/`, this is a Candidate), and
    # **rendered into the Daily History markdown** that is committed and
    # pushed to the backup remote.
    #
    # A warning, not a refusal, and the line is deliberate. `oplog`'s own
    # note says the patterns over-match on purpose — "a work note reading
    # 'auth token: rotated' is refused even though it carries no secret" —
    # and that trade was accepted for a Signal, which is a short structured
    # record. This field is **prose**: refusing a lessons-learned paragraph
    # because it contains the words "auth token:" would be a different
    # bargain, and choosing it is a policy decision (BACKLOG), not something
    # a warning needs. What the person gets instead is the fact, at the one
    # moment they can still retype it.
    #
    # The matched text is never echoed, for `find_secret_material()`'s
    # stated reason: a report of a leaked credential must not become the
    # second copy of it. The field name is what the person needs.
    flagged = [
        label
        for field_name, label in _REVIEW_FIELDS
        if isinstance(updates.get(field_name), str)
        and SECRET_RE.search(updates[field_name])
    ]
    if flagged:
        print_fn(
            f"  [주의] {', '.join(flagged)}에 Secret 형태의 문자열이 있습니다. "
            "저장하면 Daily History에 렌더링되어 Company Repository와 backup "
            "원격까지 갑니다 — 거기서는 지워도 남습니다. 값이 진짜 자격증명이면 "
            "지금 다시 쓰고, 이미 저장했다면 그 자격증명을 교체하세요."
        )

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
        _echo_typed(updates, print_fn)
        return ReviewOutcome.FAILED

    print_fn(f"저장되었습니다: {candidate.history_id}")
    return ReviewOutcome.SAVED


#: The exit code for a session in which the operator typed Decision Context
#: and this tool could not store it.
#:
#: **Why this exists (C117).** `main()` called `run_interactive_review()`,
#: **threw the return value away**, and returned `0`. A session where every
#: single save failed — where the person typed paragraphs into three
#: candidates and none of them reached disk — ended the same way as a clean
#: one. `test_every_candidate_failing_still_completes_the_session` pinned
#: that state and asserted only the printed line.
#:
#: This file already argues the point against itself. `main()`'s own
#: docstring ends: *"without `SystemExit` the refusal would print and then
#: exit 0, which is the shape of the defect rather than the fix."* The
#: summary line `저장 실패: N건` exists for the same reason — because "a
#: failure printed thirty candidates ago has scrolled off". The exit code is
#: the one signal that cannot scroll off.
#:
#: 3, matching `ops_status.py`, docs/14 §4's Overall Status table, and the
#: other two entrypoints that write outside this machine. The session did
#: run — nothing crashed — and something needs a person.
#:
#: It also covers the second state C117 found, and that one came from
#: running the real command rather than from reading it: input that ends
#: before the candidates do. The rest of the list was never offered, so a
#: `0` there would be saying "reviewed" about candidates nobody ever saw.
DEGRADED_EXIT = 3


@dataclass(frozen=True)
class ReviewSession:
    """What one pass over the candidates did.

    Was a bare `int` (the updated count) until C117. The count alone cannot
    answer the question `main()` has to ask — `0` is both "nothing needed
    reviewing" and "every save failed" — and the failures were already
    tracked here; they simply had nowhere to go but the screen.
    """

    updated: int = 0
    #: `history_id`s whose save raised. The operator's typed text for these
    #: is in the terminal scrollback and nowhere else (`_review_one()`
    #: echoes it back on purpose).
    failed: tuple[str, ...] = ()
    #: `history_id`s the session never reached, because the input ended or a
    #: person interrupted it. Distinct from a skip, which the operator chose
    #: — these were never put in front of anybody.
    unreached: tuple[str, ...] = ()


def run_interactive_review(
    reviewer: HistoryReviewer,
    *,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
) -> ReviewSession:
    """Review every KEEP/REVIEW candidate one at a time.

    Returns what the session did. Only ever calls
    reviewer.list_reviewable() and reviewer.submit_review() — no Daily
    Generator, Scheduler, or Backup call happens here.
    """
    candidates = reviewer.list_reviewable()
    if not candidates:
        print_fn("리뷰할 History Candidate가 없습니다.")
        return ReviewSession()

    updated_count = 0
    failed: list[str] = []
    unreached: list[str] = []
    for index, candidate in enumerate(candidates):
        try:
            outcome = _review_one(
                reviewer, candidate, input_fn=input_fn, print_fn=print_fn
            )
        except (EOFError, KeyboardInterrupt):
            # The session ends here, and every candidate after this one was
            # never offered. Caught rather than allowed out, because the
            # alternative is what C117 measured on the real command: a raw
            # `EOFError` traceback, exit 1 (this project's configuration-error
            # code), and no summary of what the session had already saved.
            #
            # `_review_one()` has already echoed anything typed for this
            # candidate. Both exceptions land here on purpose: Ctrl+C and a
            # closed pipe leave the operator in the same place — a list that
            # was not finished — and the lines below say how much of it.
            unreached = [c.history_id for c in candidates[index:]]
            break
        if outcome is ReviewOutcome.SAVED:
            updated_count += 1
        elif outcome is ReviewOutcome.FAILED:
            failed.append(candidate.history_id)

    print_fn(f"\n리뷰 완료: {updated_count}건 저장됨 (총 {len(candidates)}건 중).")
    if failed:
        # Named again at the end: a failure printed thirty candidates ago has
        # scrolled off, and "저장됨 2건" alone reads like success.
        print_fn(f"저장 실패: {len(failed)}건 — {', '.join(failed)}")
    if unreached:
        # A count rather than a list, because this one can be the whole
        # backlog. The ids are still on disk and `ops_status.py` reports
        # them; what a reader needs here is that the session stopped early.
        print_fn(
            f"검토하지 못한 Candidate: {len(unreached)}건 "
            "(입력이 끝났거나 중단됐습니다 — 다시 실행하면 그대로 남아 있습니다)."
        )
    return ReviewSession(
        updated=updated_count, failed=tuple(failed), unreached=tuple(unreached)
    )


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
    session = run_interactive_review(reviewer)

    # See `DEGRADED_EXIT`. A skip is not a failure — the operator chose it —
    # and an empty candidate list is not one either; both leave these two
    # tuples empty and this returns 0, which is what they mean.
    if session.failed:
        print(
            f"[DEGRADED] {len(session.failed)}건의 Decision Context가 저장되지 "
            "못했습니다. 입력한 내용은 위 출력에만 남아 있습니다 — 스크롤을 "
            "올려 확인한 뒤 다시 시도하거나 따로 보관하세요.",
            file=sys.stderr,
        )
        return DEGRADED_EXIT
    if session.unreached:
        print(
            f"[DEGRADED] {len(session.unreached)}건을 검토하지 못한 채 "
            "끝났습니다 — 입력이 끝났거나 중단됐습니다. 이 명령은 터미널에서 "
            "직접 실행해야 합니다(파이프로 답을 넣으면 목록 끝까지 가지 "
            "못합니다).",
            file=sys.stderr,
        )
        return DEGRADED_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
