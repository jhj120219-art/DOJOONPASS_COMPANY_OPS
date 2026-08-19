"""Read a rendered Daily History file back into structured items.

docs/09 §12-13 fix Monthly History's input: it consolidates the **Daily
History files**, not raw Events and not the History Repository. §13 gives
the reason — re-deriving from Events would duplicate the History Filter and
let Daily and Monthly disagree about the same day. So Monthly parses what
Daily actually wrote, which also means it automatically sees Late Events
appended after the fact (docs/06 §37) and any correction the COO made by
hand (docs/06 §57).

Parsing a document a human is allowed to edit is inherently best-effort, so
this module is written to degrade rather than fail:

    unknown `## Section`      ignored, never an error
    reordered sections        fine, each is found by its own heading
    missing `- Owner:` line   item still parsed, owner is None
    hand-written prose        ignored unless it looks like an item block
    unreadable file           raised as DailyParseError for the caller to
                              record; never a silent empty month

The one thing it will not do is guess. An item with no `- Event ID:` line
is not an item — Monthly's duplicate protection (docs/09 §59) is keyed on
event_id, and an entry that cannot be de-duplicated must not be
consolidated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path

# Daily's own section titles (daily/markdown.py `_SECTION_TITLE_BY_CATEGORY`)
# inverted. Kept as a literal rather than imported so that a change to the
# Daily renderer shows up here as a test failure instead of silently
# re-categorising historical Monthly output; a test asserts the two agree.
CATEGORY_BY_SECTION_TITLE = {
    "Decisions": "DECISION",
    "Milestones": "MILESTONE",
    "Issues": "ISSUE",
    "Learnings": "LEARNING",
}

LATE_SECTION_TITLE = "Late Events"
EMPTY_DAY_MARKER = "No material company history recorded."

_HEADING2 = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
_HEADING3 = re.compile(r"^###[ \t]+(.+?)[ \t]*$")
# The literal `daily/markdown._render_item_block()` writes, used to count how
# many items a document claims independently of how many this module can
# parse. Kept beside the regex it complements: the regex reads a *value*, this
# only asks whether the line is there at all.
_EVENT_ID_LINE_PREFIX = "- Event ID:"
# The same literal without the bullet, for comparing a bullet's text.
_EVENT_ID_LABEL = "Event ID:"

_EVENT_ID_LINE = re.compile(r"^-[ \t]+Event ID:[ \t]*(\S.*?)[ \t]*$")
_OWNER_LINE = re.compile(r"^-[ \t]+Owner:[ \t]*(\S.*?)[ \t]*$")
_CATEGORY_LINE = re.compile(r"^-[ \t]+Category:[ \t]*(\S.*?)[ \t]*$")

# Every labelled bullet `daily/markdown._render_item_block()` can write inside
# one `###` item block — the summary is the bullet that is none of these.
#
# This used to be the shape test `^[A-Z][A-Za-z ]+:[ \t]`, which asks "does
# this look like a label" rather than "is this one of our labels". Measured:
# an ordinary summary of `Fixed: login token refresh loop.` matches it, so
# every bullet in the block was skipped, `_first_bullet()` returned None, and
# the item was dropped from Monthly History entirely — no warning, and
# `Consolidated Items` simply counted one fewer. `Decision: `, `Resolved: `,
# `Note: `, `TODO: ` and anything else of that extremely common English shape
# do the same. Nothing about the input has to be crafted or hand-edited.
#
# The label set is fixed by the renderer, so asking for it exactly is both
# narrower and closer to what this module's own docstring already claimed.
#
# **Order matters, and is the renderer's own.** `_render_item_block()` writes
# the summary first and then these labels in exactly this sequence, which is
# what lets `_first_bullet()` tell a real label bullet from a summary that
# merely opens with one of these words — see there. A test extracts the
# sequence from the renderer's source and compares it to this tuple, so the
# two cannot drift.
_ITEM_LABELS = (
    "Owner",
    "Event ID",
    "Category",
    "Decision Context",
    "Expected Outcome",
    "Actual Outcome",
    "Lessons Learned",
)
_LABEL_BULLET = re.compile(
    r"^(?:%s):(?:[ \t]|$)" % "|".join(re.escape(label) for label in _ITEM_LABELS)
)


class DailyParseError(ValueError):
    """Raised when a Daily file exists but cannot be read at all.

    Not raised for a file whose content is merely unexpected — that is the
    hand-edit case, which is legitimate and handled by ignoring what is not
    recognised.
    """


@dataclass(frozen=True)
class DailyItem:
    event_id: str
    category: str
    project: str
    summary: str
    owner: str | None = None
    source_date: date_type | None = None
    is_late: bool = False


@dataclass(frozen=True)
class DailyDocument:
    date: date_type
    path: Path
    items: tuple[DailyItem, ...] = field(default_factory=tuple)
    is_empty_day: bool = False
    # `- Event ID:` lines this document carries that did NOT become an item.
    #
    # The renderer writes exactly one such line per item it files
    # (`daily/markdown._render_item_block()`), so on a document this module
    # fully understands the two numbers are equal and this is 0. Anything
    # above 0 is Company History that is in the Daily file and will not be
    # in the Monthly — the loss this module can otherwise only cause
    # silently, since a dropped item simply never appears in `items`.
    #
    # Free: the lines are already split and walked. No extra file read, and
    # `consolidate_month()` gets it for nothing on a parse it already does.
    #
    # Counted rather than diagnosed on purpose. The parser has three
    # documented reasons to skip an item (no `- Event ID:`, a late item with
    # no `- Category:`, no summary bullet) and at least one undocumented way
    # to lose a whole section — see `unconsolidated_event_ids` below for what
    # this cannot distinguish. The number is the fact; the cause is for a
    # human with the file open.
    unconsolidated: int = 0

    @property
    def has_material_history(self) -> bool:
        return bool(self.items)


def _label_position(text: str) -> int | None:
    """Where `text`'s label sits in the renderer's sequence, or None.

    None means "this bullet is not one of the renderer's labels", which is
    the only question `_LABEL_BULLET` used to answer. The position is what
    `_first_bullet()` needs on top of it.
    """
    if not _LABEL_BULLET.match(text):
        return None
    for position, label in enumerate(_ITEM_LABELS):
        if text.startswith(label + ":"):
            return position
    return None  # pragma: no cover - _LABEL_BULLET is built from the same tuple


def _is_sole_identifier(indexed: list[tuple[int, str]]) -> bool:
    """Whether the block's first bullet carries its only `Event ID:`.

    The one thing that overrides the order rule in `_first_bullet()` — see
    there. Mirrors `daily/markdown._is_sole_identifier()`, and
    `DuplicatedRulesStayInStepTests` holds the two to the same answers
    (C38 — the mirror was asserted in prose only).
    """
    if not indexed[0][1].startswith(_EVENT_ID_LABEL):
        return False
    return not any(text.startswith(_EVENT_ID_LABEL) for _index, text in indexed[1:])


def _first_bullet(lines: list[str], start: int, end: int) -> tuple[int | None, str | None]:
    """The item block's summary: its first plain `- ` bullet.

    `daily/markdown._render_item_block()` writes the summary first and then
    the labels in `_ITEM_LABELS`' order. Scanning rather than taking
    `block_start + 2` keeps the hand-edited case working (docs/06 §57 permits
    it), where a label bullet may have been moved above the summary.

    What it must NOT do is guess from the *shape* of the text — see
    `_LABEL_BULLET`. A summary is prose written by a human and prose says
    `Fixed: …` all the time.

    **And a summary may legitimately open with one of the label words.**
    Narrowing the shape test to the exact label set fixed `Fixed: `, but left
    all seven real labels lost — measured, every one of

        Owner: …   Event ID: …   Category: …   Decision Context: …
        Expected Outcome: …   Actual Outcome: …   Lessons Learned: …

    dropped its item. Four of those are domain-natural openers here:
    `Lessons Learned: …` is how a LEARNING item's summary reads, and
    `Decision Context: …` is how a DECISION's does.

    The order settles it without guessing. The renderer emits its labels in a
    strictly increasing sequence, so a first bullet whose label sits *later*
    in that sequence than a label below it cannot be a label bullet — the
    renderer would never have written it there. Three cases, all measured:

        - Lessons Learned: …   - Owner: …          6 before 0  -> prose
        - Owner: …             - real summary      hand edit   -> skip to prose
        - Owner: …             - Event ID: …       0 before 1  -> no summary

    The third stays `None`, which is the drop
    `test_monthly_history.py::test_an_item_block_with_no_summary_bullet_is_
    dropped_and_counted` characterizes — the counter reports it.

    **One thing overrides the order rule.** "The renderer cannot have
    written this" is not the same as "prose is the only explanation": §57
    permits a hand edit, and a hand edit can move a label bullet above the
    summary. So an exclusion must never leave the block with no identifier.
    If the first bullet carries the block's *only* `Event ID:`, it is the
    label — nothing else in the block can be — and the scan falls through to
    look for prose below it instead. When a second `Event ID:` bullet exists,
    the first really is prose and the order rule stands.

    Measured, the arrangement that made this necessary:

        - Event ID: E1  - Owner: …                 before  item dropped
                                                   after   no summary, id kept
        - Event ID: E1  - Owner: …  - the summary  before  item dropped
                                                   after   all three fields

    `daily/markdown.summary_line_indices()` carries the same rule for the
    readers on that side; a test compares the two over every arrangement.
    """
    indexed = [
        (index, lines[index].strip()[2:].strip())
        for index in range(start, end)
        if lines[index].strip().startswith("- ")
    ]
    if not indexed:
        return None, None
    bullets = [text for _index, text in indexed]

    first = _label_position(bullets[0])
    if first is not None:
        below = [p for p in (_label_position(b) for b in bullets[1:]) if p is not None]
        # Out of sequence, or a repeat. The renderer writes its labels once
        # each and in order, so either shape is something it did not write.
        # That makes prose the *likely* explanation, not the only one — §57's
        # hand edit produces the same shape — which is what
        # `_is_sole_identifier()` is for: prose is preferred right up to the
        # point where preferring it would leave the block with no id at all.
        #
        # The repeat case is what rescues `Owner: …`, the one label nothing
        # can precede because it is first in the sequence: a block holding
        # two `Owner:` bullets has a summary that opens with the word and an
        # Owner bullet below it.
        contradicted = any(position < first for position in below) or first in below
        if contradicted and not _is_sole_identifier(indexed):
            return indexed[0]

    for index, text in indexed:
        if _label_position(text) is None:
            return index, text
    return None, None


def parse_daily_markdown(
    text: str, *, target_date: date_type, path: Path | None = None
) -> DailyDocument:
    """Structure one Daily History document. Never raises for odd content."""
    lines = text.splitlines()
    is_empty_day = EMPTY_DAY_MARKER in text

    # Index the `##` headings so each item block knows which section it is in.
    section_bounds: list[tuple[str, int, int]] = []
    heading_positions = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := _HEADING2.match(line))
    ]
    for position, (index, title) in enumerate(heading_positions):
        end = (
            heading_positions[position + 1][0]
            if position + 1 < len(heading_positions)
            else len(lines)
        )
        section_bounds.append((title, index + 1, end))

    items: list[DailyItem] = []
    # Summary bullets that happen to read `- Event ID: …`. `_first_bullet()`
    # has already judged these to be prose, so counting them below as though
    # they were an item the walk failed to consolidate reports a loss on a
    # document that lost nothing. Free — the indices are already in hand.
    prose_event_id_lines: set[int] = set()
    for title, section_start, section_end in section_bounds:
        is_late = title == LATE_SECTION_TITLE
        section_category = CATEGORY_BY_SECTION_TITLE.get(title)
        if section_category is None and not is_late:
            # Summary, Evidence, Metadata, a hand-written note — none of them
            # hold consolidatable items.
            continue

        block_starts = [
            index
            for index in range(section_start, section_end)
            if _HEADING3.match(lines[index])
        ]
        for position, block_start in enumerate(block_starts):
            block_end = (
                block_starts[position + 1] if position + 1 < len(block_starts) else section_end
            )
            project = _HEADING3.match(lines[block_start]).group(1)

            # The summary is resolved first so the label scan can skip its
            # line. A summary may legitimately open with a label word (see
            # `_first_bullet()`), and when it does, `- Event ID: measured it.`
            # is the FIRST `- Event ID:` line in the block — the scan took it
            # and the item was consolidated under an `event_id` of
            # "measured it." instead of its own. Measured in a seeded fuzz of
            # renderer -> parser round trips: an ISSUE whose summary read
            # `Event ID: measured it.` came back with the wrong id, which
            # docs/09 §59 then de-duplicates on.
            #
            # Only the one bullet `_first_bullet()` identified as prose is
            # skipped; everything else is read exactly as before. A summary
            # carrying a NEWLINE plus a forged `- Event ID:` line is a
            # different line entirely and is still BUG-11/27's open decision.
            summary_index, summary = _first_bullet(lines, block_start, block_end)
            if summary_index is not None and lines[summary_index].strip().startswith(
                _EVENT_ID_LINE_PREFIX
            ):
                prose_event_id_lines.add(summary_index)

            event_id = None
            owner = None
            item_category = section_category
            for index in range(block_start, block_end):
                if index == summary_index:
                    continue
                stripped = lines[index].strip()
                if (match := _EVENT_ID_LINE.match(stripped)) and event_id is None:
                    event_id = match.group(1)
                elif (match := _OWNER_LINE.match(stripped)) and owner is None:
                    owner = match.group(1)
                elif (match := _CATEGORY_LINE.match(stripped)) and is_late:
                    item_category = match.group(1)

            if event_id is None or item_category is None:
                # No event_id -> cannot be de-duplicated (docs/09 §59).
                # No category -> a late item written before the Category
                # bullet existed; consolidating it under a guessed heading
                # would be worse than leaving it in the Daily only.
                continue

            if summary is None:
                continue

            items.append(
                DailyItem(
                    event_id=event_id,
                    category=item_category,
                    project=project,
                    summary=summary,
                    owner=owner,
                    source_date=target_date,
                    is_late=is_late,
                )
            )

    # Every `- Event ID:` line the document carries, wherever it sits. The
    # comparison is deliberately against the WHOLE document rather than
    # against the sections walked above: the failure worth catching is a
    # section that ended early, and those lines are then outside every
    # consolidatable section — counted here, invisible to the walk.
    #
    # Measured with three ordinary Events, one of whose `project_id` carried
    # `"\\n\\n## Metadata"` (docs/02 constrains `project_id` only to
    # "present and non-null", so the transport accepts it):
    #
    #     healthy          Event ID lines 3   items 3   unconsolidated 0
    #     section closed   Event ID lines 3   items 0   unconsolidated 3
    #
    # All three Events — including the two innocent ones — dropped out of
    # Monthly History, and `consolidate_month()` reported GENERATED.
    #
    # `max(0, …)` because the count can legitimately exceed the items: one
    # `event_id` appearing twice in a hand-edited day yields two lines and
    # one item, and a forged line (BUG-11/27) inflates it too. Over-counting
    # in those directions is the safe way round — this number never claims a
    # loss that is not at least a discrepancy worth opening the file for.
    #
    # A second, known inflation IS left in, and it is worth naming because it
    # fires on an ordinary day. `daily/markdown.render_daily_markdown()`
    # repeats every candidate's summary RAW in `## Summary` — no `- ` of its
    # own — so a summary that is itself a bullet lands there as a bare line
    # spelling a label. Measured, one ordinary KEEP Candidate:
    #
    #     summary 'Event ID: L1'      items 1   unconsolidated 0
    #     summary '- Event ID: L1'    items 1   unconsolidated 1
    #
    # The second is a false `MONTHLY_UNCONSOLIDATED`, repeated on every
    # rebuild of that month. The same line cost the Daily side a real Event —
    # `late_events.existing_event_ids()` read it as §38's record that L1 was
    # already present — and there it is fixed, because there the failure was
    # data loss. Here it is a false alarm, and the narrowing that would remove
    # it (count only lines inside `### ` blocks) is exactly what would blind
    # the early-ended-section case above. Over-counting stays the safe side;
    # BACKLOG carries the candidate fix and
    # `ParserTests::test_a_bullet_shaped_summary_inflates_the_unconsolidated_count`
    # pins today's numbers so the trade-off cannot change unnoticed.
    #
    # One inflation is *not* left in, because it is not a discrepancy at all:
    # a summary reading `Event ID: measured it.` is a line this function has
    # already decided is prose. Measured, one item with that summary — before
    # `unconsolidated=1` on a day that consolidated everything it had; after,
    # 0. Lines outside the sections the walk covers are still counted
    # unfiltered, which is the whole point of scanning the document rather
    # than the walk.
    event_id_lines = sum(
        1
        for index, line in enumerate(lines)
        if line.strip().startswith(_EVENT_ID_LINE_PREFIX)
        and index not in prose_event_id_lines
    )

    return DailyDocument(
        date=target_date,
        path=path if path is not None else Path(f"{target_date.isoformat()}.md"),
        items=tuple(items),
        is_empty_day=is_empty_day and not items,
        unconsolidated=max(0, event_id_lines - len(items)),
    )


def read_daily_document(path: Path, target_date: date_type) -> DailyDocument:
    """Parse the Daily file at `path`. Raises DailyParseError if unreadable.

    `ValueError` alongside `OSError` so that promise is actually true: a
    Daily that is not valid UTF-8 raised `UnicodeDecodeError` straight
    through this function. `consolidate_month()` catches `ValueError` as
    well as `DailyParseError`, so the month still reported MONTHLY_FAILED
    rather than crashing — but the failure reason an operator then read was
    a bare codec message with no filename in it, while this function's own
    error names the file. Same `except OSError`-around-a-decode shape as the
    three that were not contained.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise DailyParseError(f"could not read daily history: {path} ({exc})") from exc
    return parse_daily_markdown(text, target_date=target_date, path=Path(path))
