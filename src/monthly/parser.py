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
_EVENT_ID_LINE = re.compile(r"^-[ \t]+Event ID:[ \t]*(\S.*?)[ \t]*$")
_OWNER_LINE = re.compile(r"^-[ \t]+Owner:[ \t]*(\S.*?)[ \t]*$")
_CATEGORY_LINE = re.compile(r"^-[ \t]+Category:[ \t]*(\S.*?)[ \t]*$")


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

    @property
    def has_material_history(self) -> bool:
        return bool(self.items)


def _first_bullet(lines: list[str], start: int, end: int) -> str | None:
    """The item block's summary: its first plain `- ` bullet.

    Deliberately positional rather than keyword-matched, because that is how
    `daily/markdown._render_item_block()` writes it — the summary is the only
    bullet with no `Label:` prefix, and it always comes first.
    """
    for index in range(start, end):
        line = lines[index].strip()
        if not line.startswith("- "):
            continue
        text = line[2:].strip()
        if re.match(r"^[A-Z][A-Za-z ]+:[ \t]", text):
            continue
        return text
    return None


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

            event_id = None
            owner = None
            item_category = section_category
            for index in range(block_start, block_end):
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

            summary = _first_bullet(lines, block_start, block_end)
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

    return DailyDocument(
        date=target_date,
        path=path if path is not None else Path(f"{target_date.isoformat()}.md"),
        items=tuple(items),
        is_empty_day=is_empty_day and not items,
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
