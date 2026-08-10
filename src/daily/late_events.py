"""Late Event update for an already-closed Daily History (docs/06 §36-40).

The problem docs/06 §36 states
--------------------------------
An Event dated 08-05 can arrive on 08-07, after `2026-08-05.md` was already
written. Ignoring it loses real Company History; silently rewriting the file
destroys History integrity. docs/06 §37 settles which way V1 goes, and this
module implements exactly that sequence:

    기존 파일 존재 확인
        ↓  기존 내용 보호
        ↓  동일 Event 중복 확인   (by event_id, §38)
        ↓  Late Event 추가
        ↓  Updated Metadata 기록  (§39)

with §40's absolute rule on top: a History file is never replaced without
leaving a record that it changed.

Why the existing text is appended to, never re-rendered
-------------------------------------------------------
Re-rendering the whole day from the Repository would produce a correct file
and would be far less code. It is not what §57 permits: the COO may edit
official History by hand (docs/11 §71 grants that explicitly), so
"프로그램은 Local 파일이 이미 존재한다는 이유만으로 무조건 새로 덮어써서는
안 된다 ... 기존 내용을 읽고 보존하는 방식이 필요하다". A re-render silently
discards every such edit. So the original text is carried through
byte-for-byte and the late items are appended under their own heading; the
only pre-existing lines this module ever rewrites are inside the `## Metadata`
block, which is machine bookkeeping rather than authored History.

Appending under `## Late Events` rather than merging each item back into its
category section is also deliberate: merging means editing the middle of a
document a human may have restructured, and the arrival-order distinction it
would erase is itself worth keeping — a decision recorded two days late is
not quite the same fact as one recorded on the day.

Everything here is pure text in, text out. File I/O, Repository access, and
the decision about *which* dates to check live in generator.py and
app/runner.py respectively.
"""

from __future__ import annotations

import re
from typing import Sequence

from history import HistoryCandidate

from .markdown import _render_item_block

LATE_SECTION_TITLE = "## Late Events"
METADATA_TITLE = "## Metadata"

# `[ \t]*` rather than `\s*` throughout: these patterns are matched against
# one already-split line at a time, so horizontal whitespace is the only kind
# that can occur and saying so is more precise.
#
# It also avoids a false positive worth knowing about before someone "tidies"
# it back. The path-safety guard in
# tests/test_daily_history.py::test_no_hardcoded_absolute_windows_paths_in_source
# is a blunt substring scan for absolute Windows drive prefixes, and the tail
# of "Event ID:" followed by an escape happens to spell one. The guard is
# right to stay blunt; the pattern is easy to write another way.
_EVENT_ID_LINE = re.compile(r"^- Event ID:[ \t]*(\S.*)$")
_GENERATED_AT_LINE = re.compile(r"^- Generated At:[ \t]*")
_LAST_UPDATED_LINE = re.compile(r"^- Last Updated At:[ \t]*")
_LATE_ADDED_LINE = re.compile(r"^- Late Events Added:[ \t]*(\d+)[ \t]*$")
_EVENT_COUNT_LINE = re.compile(r"^- Event Count:[ \t]*")


def existing_event_ids(markdown: str) -> set[str]:
    """Every event_id already recorded in a rendered Daily History.

    docs/06 §38's duplicate guard reads the file rather than the Repository
    on purpose: the question is "is this Event already *in this document*",
    and after a manual edit the Repository is no longer authoritative about
    that.

    Only `- Event ID:` lines count. The Evidence section's `- <id>: <text>`
    lines deliberately do not match — an Event can contribute evidence lines
    without its own item block only if the file was hand-edited, and in that
    case the item block's absence is what matters.
    """
    found: set[str] = set()
    for line in markdown.splitlines():
        match = _EVENT_ID_LINE.match(line.strip())
        if match:
            found.add(match.group(1).strip())
    return found


def select_late_candidates(
    markdown: str, candidates: Sequence[HistoryCandidate]
) -> tuple[HistoryCandidate, ...]:
    """The candidates for this date that the file does not already contain.

    Also de-duplicates within `candidates` itself: two Repository records
    carrying one event_id (a re-collected Event, a restored backup merged
    back in) must add one item, not two.
    """
    already = existing_event_ids(markdown)
    selected: list[HistoryCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.event_id in already or candidate.event_id in seen:
            continue
        seen.add(candidate.event_id)
        selected.append(candidate)
    selected.sort(key=lambda candidate: candidate.timestamp)
    return tuple(selected)


def _metadata_bounds(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line indices of the `## Metadata` block, or None.

    `end` is exclusive and stops at the next `## ` heading so a file whose
    Metadata is not last is still handled correctly.
    """
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == METADATA_TITLE)
    except StopIteration:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return start, end


def _update_metadata(lines: list[str], *, now_iso: str, added: int, total_events: int) -> None:
    """Rewrite the Metadata block in place, per docs/06 §39.

    Field order follows §39's worked example: History Date, Generated At,
    Last Updated At, Late Events Added, then whatever else the block already
    carried. `Generated At` is never touched — it records when the day was
    first closed, and overwriting it would erase exactly the fact §40 wants
    kept.

    A field that is missing (a hand-trimmed block) is inserted; a field that
    is present is updated. `Late Events Added` accumulates across repeated
    late updates rather than reporting only the most recent batch.
    """
    bounds = _metadata_bounds(lines)
    if bounds is None:
        # No Metadata block to update — §40 still requires a record that the
        # file changed, so one is created rather than skipped.
        lines.extend(
            [
                "",
                METADATA_TITLE,
                "",
                f"- Last Updated At: {now_iso}",
                f"- Late Events Added: {added}",
                f"- Event Count: {total_events}",
            ]
        )
        return

    start, end = bounds

    for index in range(start, end):
        if _EVENT_COUNT_LINE.match(lines[index].strip()):
            lines[index] = f"- Event Count: {total_events}"
            break

    previously_added = 0
    late_index = None
    for index in range(start, end):
        match = _LATE_ADDED_LINE.match(lines[index].strip())
        if match:
            previously_added = int(match.group(1))
            late_index = index
            break

    updated_index = None
    for index in range(start, end):
        if _LAST_UPDATED_LINE.match(lines[index].strip()):
            updated_index = index
            break

    total_added = previously_added + added

    if late_index is not None:
        lines[late_index] = f"- Late Events Added: {total_added}"
    if updated_index is not None:
        lines[updated_index] = f"- Last Updated At: {now_iso}"

    if updated_index is not None and late_index is not None:
        return

    # Insert whichever line is missing, directly after `Generated At` so the
    # block reads in §39's order.
    insert_at = None
    for index in range(start, end):
        if _GENERATED_AT_LINE.match(lines[index].strip()):
            insert_at = index + 1
            break
    if insert_at is None:
        insert_at = end

    new_lines = []
    if updated_index is None:
        new_lines.append(f"- Last Updated At: {now_iso}")
    if late_index is None:
        new_lines.append(f"- Late Events Added: {total_added}")
    lines[insert_at:insert_at] = new_lines


def append_late_events(
    markdown: str, candidates: Sequence[HistoryCandidate], *, now_iso: str
) -> str:
    """Return `markdown` with `candidates` appended as Late Events.

    `candidates` must already have been filtered through
    `select_late_candidates()`. Returns the input unchanged when it is empty,
    so a caller can call this unconditionally without risking a pointless
    rewrite of a History file.
    """
    if not candidates:
        return markdown

    lines = markdown.splitlines()
    # `include_category=True`: see `_render_item_block()` — a late item is
    # the only kind whose category is not implied by the heading above it,
    # and Monthly History has to be able to file it.
    item_blocks = [
        _render_item_block(candidate, include_category=True) for candidate in candidates
    ]

    try:
        section_index = next(
            i for i, line in enumerate(lines) if line.strip() == LATE_SECTION_TITLE
        )
    except StopIteration:
        section_index = None

    if section_index is None:
        insertion = ["", LATE_SECTION_TITLE, ""]
        for position, block in enumerate(item_blocks):
            if position:
                insertion.append("")
            insertion.extend(block.splitlines())
        bounds = _metadata_bounds(lines)
        insert_at = bounds[0] if bounds is not None else len(lines)
        # Keep the blank line that separated Metadata from what came before.
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = insertion
    else:
        # A second (or third) late update on the same day extends the
        # existing section instead of opening a new one.
        end = len(lines)
        for index in range(section_index + 1, len(lines)):
            if lines[index].startswith("## "):
                end = index
                break
        while end > section_index + 1 and not lines[end - 1].strip():
            end -= 1
        insertion = []
        for block in item_blocks:
            insertion.append("")
            insertion.extend(block.splitlines())
        lines[end:end] = insertion

    total_events = len(existing_event_ids("\n".join(lines)))
    _update_metadata(
        lines, now_iso=now_iso, added=len(candidates), total_events=total_events
    )

    return "\n".join(lines) + "\n"
