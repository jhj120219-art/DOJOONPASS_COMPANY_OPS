"""Monthly History (docs/09_MONTHLY_HISTORY_SPEC.md).

Consolidates confirmed Daily History into one file per calendar month.
Reads Daily files only (§12-13), never raw Events and never the History
Repository. See generator.py for the rules and markdown.py for which
sections V1 can and cannot derive.
"""

from .coverage import DailyCoverage, check_coverage, month_dates
from .generator import (
    DEFAULT_MONTHLY_DIR,
    MonthlyResult,
    MonthlyRunResult,
    MonthlyStatus,
    consolidate_month,
    mark_month_dirty,
    monthly_history_path,
    pending_months,
    run_once,
)
from .markdown import (
    NO_MATERIAL_HISTORY_SENTENCE,
    SECTION_TITLE_BY_CATEGORY,
    MonthlyItem,
    month_title,
    render_monthly_markdown,
)
from .parser import (
    CATEGORY_BY_SECTION_TITLE,
    DailyDocument,
    DailyItem,
    DailyParseError,
    parse_daily_markdown,
    read_daily_document,
)
from .state import (
    DEFAULT_STATE_PATH,
    MonthlyState,
    MonthlyStateError,
    load_state,
    month_key,
    parse_month_key,
    save_state,
)

__all__ = [
    "CATEGORY_BY_SECTION_TITLE",
    "DEFAULT_MONTHLY_DIR",
    "DEFAULT_STATE_PATH",
    "NO_MATERIAL_HISTORY_SENTENCE",
    "SECTION_TITLE_BY_CATEGORY",
    "DailyCoverage",
    "DailyDocument",
    "DailyItem",
    "DailyParseError",
    "MonthlyItem",
    "MonthlyResult",
    "MonthlyRunResult",
    "MonthlyState",
    "MonthlyStateError",
    "MonthlyStatus",
    "check_coverage",
    "consolidate_month",
    "load_state",
    "mark_month_dirty",
    "month_dates",
    "month_key",
    "month_title",
    "monthly_history_path",
    "parse_daily_markdown",
    "parse_month_key",
    "pending_months",
    "read_daily_document",
    "render_monthly_markdown",
    "run_once",
    "save_state",
]
