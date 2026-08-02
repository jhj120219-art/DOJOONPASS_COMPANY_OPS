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


def _render_item_block(candidate: HistoryCandidate) -> str:
    lines = [
        f"### {_display_project_name(candidate.project_id)}",
        "",
        f"- {candidate.summary}",
        f"- Owner: {_display_role(candidate.role)}",
        f"- Event ID: {candidate.event_id}",
    ]
    if candidate.decision_context:
        lines.append(f"- Decision Context: {candidate.decision_context}")
    if candidate.expected_outcome:
        lines.append(f"- Expected Outcome: {candidate.expected_outcome}")
    if candidate.actual_outcome:
        lines.append(f"- Actual Outcome: {candidate.actual_outcome}")
    if candidate.lessons_learned:
        lines.append(f"- Lessons Learned: {candidate.lessons_learned}")
    return "\n".join(lines)


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
        blocks.append("No material company history recorded.")
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
