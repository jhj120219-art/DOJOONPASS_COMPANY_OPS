"""Daily History Markdown renderer (docs/06_DAILY_HISTORY_SPEC.md sections 14-26).

Pure function: a list of KEEP HistoryCandidate -> Markdown text. No file
I/O, no Repository access, here — see generator.py for that.

Known gaps versus the spec's own worked examples, kept here rather than
invented: HistoryCandidate (fixed in Phase 4.3/4.35) carries no separate
"title" for a Decision/Milestone and no Issue "Status", so every category
section uses one uniform item template (project name / summary / owner /
event id) instead of the slightly different per-category field labels
shown in docs/06 sections 18-21. See this Phase's [ISSUES] report.

Phase 4.7 (Decision Context Integration) added optional Decision Context /
Expected Outcome / Actual Outcome / Lessons Learned bullets to that item
template, per docs/05_HISTORY_PIPELINE_SPEC.md section 27/38 and README
RULE 11/12. Each line is only rendered when the candidate actually has
that value — nothing is fabricated for candidates that don't carry it
(which, as of this Phase, is every candidate HistoryFilter produces).
"""

from __future__ import annotations

import re
from datetime import date as date_type
from typing import Sequence

from history import HistoryCandidate

_CATEGORY_ORDER = ("DECISION", "MILESTONE", "ISSUE", "LEARNING")

_SECTION_TITLE_BY_CATEGORY = {
    "DECISION": "Decisions",
    "MILESTONE": "Milestones",
    "ISSUE": "Issues",
    "LEARNING": "Learnings",
}

#: docs/06 §25's Empty Day sentence — the whole body of a closed day that had
#: no candidates. Named rather than inlined (C135) because two other places
#: act on the exact string: `late_events` removes it when a Late Event makes
#: it untrue, and `monthly/parser.EMPTY_DAY_MARKER` reads it. The layering
#: forbids Monthly importing it (docs/09 §13), so that copy stays a declared
#: duplicate; this one does not have to be.
EMPTY_DAY_SENTENCE = "No material company history recorded."

_ROLE_DISPLAY_NAMES = {
    "CTO_BACKEND": "CTO Backend",
    "CTO_FRONTEND": "CTO Frontend",
    "CMO": "CMO",
    "COO": "COO",
}


def _display_project_name(project_id: str) -> str:
    return project_id.replace("_", " ").title()


def _display_role(role: str) -> str:
    return _ROLE_DISPLAY_NAMES.get(role, role)


def _render_item_block(candidate: HistoryCandidate, *, include_category: bool = False) -> str:
    """One item block, per docs/06 sections 18-21.

    `include_category` adds a `- Category:` bullet. It is used **only** by
    `late_events.py` for the `## Late Events` section, and never in the four
    sections docs/06 fixes, whose template stays exactly as specified.

    The reason it exists at all: every canonical section states its items'
    category through its own heading (`## Decisions` -> DECISION), so an item
    inside one is self-describing. A late item is not — `## Late Events`
    holds items of every category at once — and Monthly History consolidates
    strictly from the Daily files (docs/09 sections 12-13, which forbid
    re-reading raw Events or the Repository to avoid "판단 기준 불일치").
    Without this bullet a late DECISION could not be filed under Major
    Decisions in the Monthly, and a Desktop that was offline across a Daily
    Close is exactly the common case that produces late items.
    """
    lines = [
        f"### {_display_project_name(candidate.project_id)}",
        "",
        f"- {candidate.summary}",
        f"- Owner: {_display_role(candidate.role)}",
        f"- Event ID: {candidate.event_id}",
    ]
    if include_category and candidate.category:
        lines.append(f"- Category: {candidate.category}")
    if candidate.decision_context:
        lines.append(f"- Decision Context: {candidate.decision_context}")
    if candidate.expected_outcome:
        lines.append(f"- Expected Outcome: {candidate.expected_outcome}")
    if candidate.actual_outcome:
        lines.append(f"- Actual Outcome: {candidate.actual_outcome}")
    if candidate.lessons_learned:
        lines.append(f"- Lessons Learned: {candidate.lessons_learned}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Reading back what `_render_item_block()` wrote
# --------------------------------------------------------------------------
#
# The summary is written raw as the block's first bullet (`- {summary}`), so
# a summary of `Event ID: EVT-999` is byte-identical to the label bullet
# below it. Any reader that matches `- Event ID:` line by line will believe
# EVT-999 is in this document. Measured, one ordinary KEEP Candidate:
#
#     summary "Event ID: EVT-999"
#     -> existing_event_ids() == {'EVT-1', 'EVT-999'}
#     -> a genuinely late EVT-999 is dropped by docs/06 section 38's
#        duplicate guard, on that run and on every run after it
#
# So the format needs a rule for "which `- Event ID:` line is the label",
# and it belongs here, with the writer that creates the ambiguity.
#
# Not shared with `monthly/parser.py`, which asks the same question: that
# package is a declared leaf (tests/test_architecture_invariants.py
# `ALLOWED["monthly"] == set()`) precisely so Monthly consolidation cannot
# reach past the Daily *text* into `history`/`events` — docs/09 sections
# 12-13. An import here would be the shortest path to breaking that. The two
# implementations are held together by test instead.

ITEM_LABELS = (
    "Owner",
    "Event ID",
    "Category",
    "Decision Context",
    "Expected Outcome",
    "Actual Outcome",
    "Lessons Learned",
)

_LABEL_BULLET = re.compile(
    r"^(?:%s):(?:[ \t]|$)" % "|".join(re.escape(label) for label in ITEM_LABELS)
)


def label_position(text: str) -> int | None:
    """Where a bullet's label sits in the sequence above, or None for prose."""
    if not _LABEL_BULLET.match(text):
        return None
    for position, label in enumerate(ITEM_LABELS):
        if text.startswith(label + ":"):
            return position
    return None  # pragma: no cover - _LABEL_BULLET is built from the same tuple


_EVENT_ID_LABEL = "Event ID:"


def _is_sole_identifier(indexed: Sequence[tuple[int, str]]) -> bool:
    """Whether the block's first bullet carries its only `Event ID:`.

    `indexed` is the block's bullets as (line index, text without `- `).
    """
    if not indexed[0][1].startswith(_EVENT_ID_LABEL):
        return False
    return not any(text.startswith(_EVENT_ID_LABEL) for _index, text in indexed[1:])


def item_block_bounds(lines: Sequence[str]) -> list[tuple[int, int]]:
    """(start, end) line ranges of each `### ` item block's body.

    `end` is exclusive; a block ends at the next `### ` or at the next `## `
    section heading, whichever comes first.

    Split out of `summary_line_indices()` because a second reader needs the
    same ranges. `late_events.existing_event_ids()` asks "which `- Event ID:`
    lines are the renderer's *label*", and a label is only ever written
    inside one of these blocks — `_render_item_block()` is the only place
    that emits one.

    Everything else in a Daily file that looks like a label is not one:

        ## Summary      `render_daily_markdown()` writes each candidate's
                        summary there RAW, one per line. A summary reading
                        `- Event ID: EVT-999` is byte-identical to a label,
                        and unlike the copy inside the item block (which is
                        written as `- {summary}`, i.e. `- - Event ID: …`)
                        nothing distinguished it.
        ## Evidence     `- <event_id>: <text>`, which spells a label exactly
                        when an `event_id` is the literal `Event ID`.
        hand-written    docs/06 §57 permits prose anywhere; a note reading
        prose           `- Event ID: EVT-9 was superseded` is not the Event.

    All three used to be read as labels, and the first is reachable without
    a hand edit or a crafted Event — see `existing_event_ids()` for what it
    cost.
    """
    bounds: list[tuple[int, int]] = []
    block_start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("### "):
            if block_start is not None:
                bounds.append((block_start, index))
            block_start = index + 1
        elif stripped.startswith("## ") and block_start is not None:
            bounds.append((block_start, index))
            block_start = None
    if block_start is not None:
        bounds.append((block_start, len(lines)))
    return bounds


def summary_line_indices(lines: Sequence[str]) -> set[int]:
    """Indices of the lines that are item *summaries* rather than labels.

    One per `###` item block that has a summary. A reader looking for a
    labelled value skips these; a reader looking for the summary keeps only
    these.

    Order is what settles the ambiguous case without guessing.
    `_render_item_block()` writes its labels **once each, in `ITEM_LABELS`
    order, after the summary**. So a first bullet carrying a label is the
    real thing only if nothing below it contradicts that: a label that sits
    *earlier* in the sequence below it, or a repeat of the same label below
    it, is an arrangement the renderer cannot produce, which leaves prose as
    the only explanation. When the first bullet is a label the renderer
    *could* have written there, the block was hand-edited (docs/06 section
    57 allows it) and the first non-label bullet is the summary.

    **Except that "the renderer cannot produce it" is not the same as "prose
    is the only explanation".** docs/06 section 57 permits a hand edit, and
    a hand edit can move a label bullet above the summary — which is the
    same arrangement. The first version of this rule treated that as prose
    unconditionally, and measured, on `- Event ID: EVT-H` moved above
    `- Owner:`:

        existing_event_ids()        set()      <- the block's id, gone
        select_late_candidates()    ['EVT-H']  <- re-added, every run
        Monthly                     dropped

    An unbounded duplicate, which is the defect the empty-`event_id` fix
    closed arriving by another door.

    So one thing overrides the order rule: an exclusion must never leave a
    block with **no identifier at all**. If the bullet about to be called
    prose carries the block's only `Event ID:`, it is the label — nothing
    else in the block can be. When the block has a second `Event ID:`
    bullet, the first one really is prose (that is the case the order rule
    was written for) and the exclusion stands.

    Strictly better than dropping through, too: with
    `- Event ID: E1 / - Owner: COO / - the summary` the parser used to lose
    the whole item, and now recovers all three fields.
    """
    found: set[int] = set()

    def close(start: int, end: int) -> None:
        indexed = [
            (index, lines[index].strip()[2:].strip())
            for index in range(start, end)
            if lines[index].strip().startswith("- ")
        ]
        if not indexed:
            return
        first = label_position(indexed[0][1])
        if first is not None:
            below = [
                position
                for position in (label_position(text) for _i, text in indexed[1:])
                if position is not None
            ]
            contradicted = any(position < first for position in below) or first in below
            if contradicted and not _is_sole_identifier(indexed):
                found.add(indexed[0][0])
                return
        for index, text in indexed:
            if label_position(text) is None:
                found.add(index)
                return

    for start, end in item_block_bounds(lines):
        close(start, end)
    return found


def _metadata_block(target_date: date_type, generated_at: str, event_count: int) -> str:
    return "\n".join(
        [
            "## Metadata",
            "",
            f"- History Date: {target_date.isoformat()}",
            f"- Generated At: {generated_at}",
            "- Source: DOJOONPASS Company Ops",
            f"- Event Count: {event_count}",
        ]
    )


def render_daily_markdown(
    target_date: date_type,
    candidates: Sequence[HistoryCandidate],
    generated_at: str,
) -> str:
    blocks = [f"# DOJOONPASS Company History — {target_date.isoformat()}"]

    if not candidates:
        blocks.append(EMPTY_DAY_SENTENCE)
        blocks.append(_metadata_block(target_date, generated_at, 0))
        return "\n\n".join(blocks) + "\n"

    summary_lines = ["## Summary", ""]
    summary_lines.extend(candidate.summary for candidate in candidates)
    blocks.append("\n".join(summary_lines))

    by_category: dict[str, list[HistoryCandidate]] = {key: [] for key in _CATEGORY_ORDER}
    for candidate in candidates:
        if candidate.category in by_category:
            by_category[candidate.category].append(candidate)

    for category in _CATEGORY_ORDER:
        items = by_category[category]
        if not items:
            continue
        item_blocks = "\n\n".join(_render_item_block(item) for item in items)
        blocks.append(f"## {_SECTION_TITLE_BY_CATEGORY[category]}\n\n{item_blocks}")

    evidence_lines = [
        f"- {candidate.event_id}: {item}"
        for candidate in candidates
        for item in candidate.evidence
    ]
    if evidence_lines:
        blocks.append("## Evidence\n\n" + "\n".join(evidence_lines))

    blocks.append(_metadata_block(target_date, generated_at, len(candidates)))

    return "\n\n".join(blocks) + "\n"
