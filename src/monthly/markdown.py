"""Monthly History Markdown renderer (docs/09 §14-37, §58, §71-73).

Pure function: consolidated items in, Markdown out. No file I/O, no state,
no Repository — see generator.py for those.

What this renderer deliberately does NOT produce
------------------------------------------------
docs/09 §14 lists eleven sections. Five of them cannot be derived from the
Daily files by rule, and V1 has no AI (§63 requires the rule-based path to
work on its own; tests/test_spec_conformance.py forbids importing an LLM
client at all). Those five are omitted rather than filled:

    Executive Summary     §15-16 ask "이번 달 회사가 어디에서 어디로
                          이동했는가" — a judgement, and §108 explicitly
                          rules out a "복잡한 Narrative Generator".
                          The ONE case that is mechanical is §71's
                          no-material-history month, whose exact sentence
                          the spec supplies; that one is rendered.
    Product Evolution     §27 needs a product-level narrative.
    KPI / Customer        §30 forbids inventing numbers and §31 says this
      Signals             is not a KPI store; with no KPI source in V1
                          there is nothing truthful to write.
    Open Risks            §32 needs "still unresolved at month end", which
      / Next-Month        means matching an issue to its resolution (§22).
      Carryover           `HistoryCandidate` carries `category`, not
                          `event_type`, so BLOCKED and ISSUE_RESOLVED are
                          both just ISSUE and cannot be paired. Adding
                          `event_type` is a docs/05 schema change.

§14 settles what to do about that: "내용이 없는 Section은 억지로 채우지
않는다." Omitting is also the only safe direction — §30, §64 and §65 all
forbid the alternative, which is a machine writing something plausible that
nobody actually observed.

Each omission is recorded in BACKLOG.md with what it would take to fill it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from typing import Sequence

# docs/09 §14's order, restricted to what V1 can derive. The full list stays
# in the module docstring so the gap is visible rather than forgotten.
SECTION_ORDER = ("DECISION", "MILESTONE", "ISSUE", "LEARNING")

SECTION_TITLE_BY_CATEGORY = {
    "DECISION": "Major Decisions",
    "MILESTONE": "Major Milestones",
    "ISSUE": "Major Issues & Resolutions",
    "LEARNING": "Key Learnings",
}

# Shared with `generator._existing_generated_at()`, which has to read a field
# back out of a rendered file and must find the block rather than the first
# line that looks like it — see there.
METADATA_TITLE = "## Metadata"

# docs/09 §71, verbatim.
NO_MATERIAL_HISTORY_SENTENCE = (
    "No material company-level changes were recorded during this month."
)


@dataclass(frozen=True)
class MonthlyItem:
    """One consolidated entry. Mirrors `parser.DailyItem` minus the fields
    Monthly does not reproduce, plus the source date it came from (§36)."""

    event_id: str
    category: str
    project: str
    summary: str
    owner: str | None
    source_date: date_type


def month_title(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _display_project(project: str) -> str:
    return project


def _render_item(item: MonthlyItem) -> str:
    lines = [f"### {_display_project(item.project)}", "", f"- {item.summary}"]
    if item.owner:
        lines.append(f"- Owner: {item.owner}")
    lines.append(f"- Event ID: {item.event_id}")
    lines.append(f"- Source: {item.source_date.isoformat()}.md")
    return "\n".join(lines)


def _metadata_block(
    *,
    year: int,
    month: int,
    generated_at: str,
    coverage: str,
    item_count: int,
    last_updated_at: str | None,
) -> str:
    lines = [
        METADATA_TITLE,
        "",
        f"- History Month: {month_title(year, month)}",
        f"- Generated At: {generated_at}",
    ]
    if last_updated_at:
        # docs/09 §58: an update never silently replaces the previous file.
        lines.append(f"- Last Updated At: {last_updated_at}")
    lines.extend(
        [
            "- Source: DOJOONPASS Company Ops",
            f"- Daily Coverage: {coverage}",
            f"- Consolidated Items: {item_count}",
        ]
    )
    return "\n".join(lines)


def render_monthly_markdown(
    *,
    year: int,
    month: int,
    items: Sequence[MonthlyItem],
    source_dates: Sequence[date_type],
    generated_at: str,
    coverage: str,
    last_updated_at: str | None = None,
) -> str:
    """Render one Monthly History document.

    `items` are consolidated and de-duplicated by the caller (docs/09 §59).
    `source_dates` are only the days that actually contributed content —
    §36: "모든 Empty Daily를 나열할 필요는 없다."
    """
    blocks = [f"# DOJOONPASS Company History — {month_title(year, month)}"]

    if not items:
        # §71-72: a month with nothing material still gets a file, so that
        # "nothing happened" and "this month was never consolidated" stay
        # distinguishable.
        blocks.append(f"## Executive Summary\n\n{NO_MATERIAL_HISTORY_SENTENCE}")
        blocks.append(
            _metadata_block(
                year=year,
                month=month,
                generated_at=generated_at,
                coverage=coverage,
                item_count=0,
                last_updated_at=last_updated_at,
            )
        )
        return "\n\n".join(blocks) + "\n"

    by_category: dict[str, list[MonthlyItem]] = {key: [] for key in SECTION_ORDER}
    for item in items:
        if item.category in by_category:
            by_category[item.category].append(item)

    for category in SECTION_ORDER:
        bucket = by_category[category]
        if not bucket:
            continue
        rendered = "\n\n".join(_render_item(item) for item in bucket)
        blocks.append(f"## {SECTION_TITLE_BY_CATEGORY[category]}\n\n{rendered}")

    if source_dates:
        listed = "\n".join(f"- {day.isoformat()}.md" for day in source_dates)
        blocks.append(f"## Source Records\n\n{listed}")

    blocks.append(
        _metadata_block(
            year=year,
            month=month,
            generated_at=generated_at,
            coverage=coverage,
            item_count=len(items),
            last_updated_at=last_updated_at,
        )
    )

    return "\n\n".join(blocks) + "\n"
