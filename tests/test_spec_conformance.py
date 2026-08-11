"""Spec Conformance Guard Tests (Audit Sprint).

The audit verified these properties once, by hand. That is worth exactly one
run. This file turns the checks that CAN be automated into permanent guards,
so a future change that violates a spec prohibition or silently alters the
Event -> Notion mapping fails the suite instead of reaching production.

Three kinds of guard live here:

    1. PROHIBITION guards  — docs/08 section 5's forbidden git commands, and
       the closed inventory of git commands backup/git_ops.py may run. These
       are the highest-stakes rules in the whole specification: violating one
       is a data-loss incident, not a feature regression.

    2. MAPPING guards      — docs/04 sections 20-28's Event Type -> Notion
       Property table, asserted for all 8 event types at once.

    3. FORMAT guards       — docs/06 sections 14-17's Daily History Markdown
       structure: heading set, section order, item template, Metadata block.

Plus one CHARACTERIZATION test for docs/10 section 17 (CEO Decision
authority), which is currently unenforced — audit finding GAP-7.

Nothing here changes production code, Runtime behaviour, or any spec.
"""

import ast
import inspect
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from collector.result import CollectorStatus  # noqa: E402
from daily import generate_daily_history  # noqa: E402
from events import EVENT_TYPES, ROLES, create_event, validate_event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
    HistoryFilter,
)
from notion.properties import build_create_properties, build_update_properties  # noqa: E402


def _source_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in str(p)]


def _all_source_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _source_files())


def _executable_lines(text: str) -> str:
    """Strip comments and docstrings so a prohibition written *about* a
    command in prose is not mistaken for the command itself."""
    tree = ast.parse(text)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    body = "\n".join(lines)
    for doc in docstrings:
        body = body.replace(doc, "")
    return body


class GitProhibitionGuardTests(unittest.TestCase):
    """docs/08 section 5 ("가장 중요한 금지사항") and sections 33/35/36.

    src/backup/git_ops.py's own module docstring lists what it must never do.
    These tests make that list enforceable.
    """

    def setUp(self):
        self.code = "\n".join(
            _executable_lines(p.read_text(encoding="utf-8")) for p in _source_files()
        )

    def test_no_force_push_anywhere(self):
        self.assertNotIn("--force", self.code)
        self.assertNotIn("force-with-lease", self.code)

    def test_no_destructive_worktree_commands(self):
        for forbidden in ("reset --hard", "clean -fd", "checkout -- ."):
            self.assertNotIn(forbidden, self.code)

    def test_no_pull_merge_or_rebase_verb_is_used(self):
        """docs/08 section 33: 자동 Pull 금지, 자동 Merge 금지."""
        found = set(
            re.findall(
                r"[\"'](pull|merge|rebase|reset|clean|checkout|restore|revert|"
                r"cherry-pick|filter-branch)[\"']",
                self.code,
            )
        )
        self.assertEqual(found, set(), f"forbidden git verb literal(s) present: {found}")

    def test_no_remote_deletion(self):
        """docs/08 section 36: Remote 삭제 금지."""
        self.assertNotIn("--delete", self.code)
        self.assertNotRegex(self.code, r"remote[\"'\s,\]]+remove")

    def test_git_ops_runs_only_the_approved_command_set(self):
        """A closed inventory. Adding any new git command to git_ops.py must
        be a deliberate, reviewed act — this test is the review gate."""
        text = (SRC / "backup" / "git_ops.py").read_text(encoding="utf-8")
        invocations = re.findall(r"_run_git\(\s*\[([^\]]+)\]", text)

        commands = set()
        for raw in invocations:
            args = [a.strip().strip("\"'") for a in raw.split(",") if a.strip()]
            args = [a for a in args if not a.startswith("repo") and not a.startswith("message")]
            commands.add(" ".join(args))

        self.assertEqual(
            commands,
            {
                "status --porcelain",
                "add -A",
                "commit -m",
                "rev-parse HEAD",
                "push",
            },
        )

    def test_working_copy_is_never_copied_back_onto_local_master(self):
        """docs/08 section 13: Copy 방향은 한쪽뿐이다."""
        self.assertNotRegex(self.code, r"copy2?\(\s*working_copy")
        self.assertNotRegex(self.code, r"copytree\(\s*working_copy")


class NoScoringOrAiGuardTests(unittest.TestCase):
    """docs/05 section 20 forbids a scoring system; the History Filter must
    stay rule-based (README RULE 6: AI는 Single Point of Failure가 아니다)."""

    def setUp(self):
        self.code = "\n".join(
            _executable_lines(p.read_text(encoding="utf-8")) for p in _source_files()
        )

    def test_no_ai_or_llm_client_is_imported(self):
        for marker in ("openai", "anthropic", "langchain", "completions.create"):
            self.assertNotIn(marker, self.code.lower())

    def test_history_filter_has_no_scoring(self):
        filter_code = _executable_lines(
            (SRC / "history" / "filter.py").read_text(encoding="utf-8")
        )
        for marker in ("score", "weight", "threshold"):
            self.assertNotIn(marker, filter_code.lower())


class NotionPropertyMappingGuardTests(unittest.TestCase):
    """docs/04 sections 20-28: Event Type -> additional Property.

    The common fields (Status / Last Updated / Last Event ID / Last Event
    Type) are asserted separately; each event type's *extra* properties are
    the mapping the spec actually enumerates.
    """

    COMMON = {"Status", "Last Updated", "Last Event ID", "Last Event Type"}

    # docs/04: section 21 STARTED, 22 BLOCKED, 23 RESUMED, 24 MILESTONE_COMPLETED,
    # 25 COMPLETED, 26 CANCELLED, 27 ISSUE_RESOLVED, 28 DECISION_APPROVED
    EXPECTED_EXTRA = {
        "STARTED": set(),
        "BLOCKED": {"Blocker"},
        "RESUMED": {"Blocker"},
        "MILESTONE_COMPLETED": {"Current Milestone"},
        "COMPLETED": {"Blocker", "Completed Date"},
        "CANCELLED": set(),
        "ISSUE_RESOLVED": {"Blocker"},
        "DECISION_APPROVED": set(),
    }

    def _event(self, event_type):
        status = {
            "COMPLETED": "COMPLETED",
            "CANCELLED": "CANCELLED",
            "BLOCKED": "BLOCKED",
        }.get(event_type, "IN_PROGRESS")
        return create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type=event_type,
            status=status,
            summary="mapping guard",
            milestone="M1",
            blocker="B" if event_type == "BLOCKED" else None,
            history_candidate=True,
            event_id=f"MAP-{event_type}",
            timestamp="2026-08-01T10:00:00+09:00",
        )

    def test_every_event_type_in_the_schema_is_covered_by_this_guard(self):
        self.assertEqual(set(self.EXPECTED_EXTRA), set(EVENT_TYPES))

    def test_update_properties_match_the_spec_table(self):
        for event_type, expected_extra in self.EXPECTED_EXTRA.items():
            with self.subTest(event_type=event_type):
                props = build_update_properties(self._event(event_type))
                self.assertTrue(self.COMMON.issubset(set(props)))
                self.assertEqual(set(props) - self.COMMON, expected_extra)

    def test_create_properties_add_the_identity_fields(self):
        """docs/04 section 8: CREATE also sets Project / Project ID / Owner /
        Source, which UPDATE deliberately does not touch (sections 9-12)."""
        props = build_create_properties(
            self._event("STARTED"), project_name="Search Frontend"
        )
        for name in ("Project", "Project ID", "Owner", "Source"):
            self.assertIn(name, props)

    def test_resumed_and_completed_clear_the_blocker(self):
        """sections 23 and 25: Blocker is emptied, not left stale."""
        for event_type in ("RESUMED", "COMPLETED"):
            with self.subTest(event_type=event_type):
                props = build_update_properties(self._event(event_type))
                self.assertEqual(props["Blocker"], {"rich_text": []})

    def test_cancelled_does_not_clear_the_blocker(self):
        """Characterization: docs/04 section 26 does not say what happens to
        an existing Blocker when a project is CANCELLED, and the code leaves
        it in place. A cancelled project's Notion row keeps its last Blocker
        string. Recorded so the behaviour is a decision, not an accident.
        """
        props = build_update_properties(self._event("CANCELLED"))
        self.assertNotIn("Blocker", props)

    def test_selects_only_ever_carry_schema_constrained_values(self):
        """Notion rejects a select name over 100 chars or containing a comma.
        Every select-typed property here is drawn from a frozenset in
        events.schema, so neither is reachable from untrusted input."""
        for event_type in EVENT_TYPES:
            with self.subTest(event_type=event_type):
                props = build_create_properties(
                    self._event(event_type), project_name="Search Frontend"
                )
                for name, prop in props.items():
                    if "select" in prop:
                        value = prop["select"]["name"]
                        self.assertLessEqual(len(value), 100)
                        self.assertNotIn(",", value)


class HistoryFilterDecisionMatrixGuardTests(unittest.TestCase):
    """docs/05 sections 22-26: the KEEP / DROP / REVIEW rules.

    tests/test_history_filter.py already covers each event type with its own
    test, but nothing iterates EVENT_TYPES. That leaves a specific hole: add a
    ninth event type to the schema tomorrow and HistoryFilter silently routes
    it to REVIEW via the else-branch, with no test noticing that a rule was
    never written for it.

    This is the same shape of guard as the docs/04 property mapping table —
    it asserts the whole matrix at once, and asserts that the matrix itself
    stays in step with the schema.

    docs/05 section 25 (automatic KEEP), section 26 (automatic DROP),
    section 24 (REVIEW examples), docs/02 section 36 (history_candidate).
    """

    # docs/05 sections 24-26 + docs/02 section 36.
    EXPECTED = {
        "DECISION_APPROVED": HistoryDecision.KEEP,
        "MILESTONE_COMPLETED": HistoryDecision.KEEP,
        "ISSUE_RESOLVED": HistoryDecision.KEEP,
        "STARTED": HistoryDecision.DROP,
        "RESUMED": HistoryDecision.DROP,
        "BLOCKED": HistoryDecision.REVIEW,
        "COMPLETED": HistoryDecision.REVIEW,
        "CANCELLED": HistoryDecision.REVIEW,
    }

    EXPECTED_CATEGORY = {
        "DECISION_APPROVED": "DECISION",
        "MILESTONE_COMPLETED": "MILESTONE",
        "COMPLETED": "MILESTONE",
        "ISSUE_RESOLVED": "ISSUE",
        "BLOCKED": "ISSUE",
        "STARTED": None,
        "RESUMED": None,
        "CANCELLED": None,
    }

    def _event(self, event_type, *, history_candidate=True):
        status = {
            "COMPLETED": "COMPLETED",
            "CANCELLED": "CANCELLED",
            "BLOCKED": "BLOCKED",
        }.get(event_type, "IN_PROGRESS")
        return create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="PRJ-MATRIX",
            event_type=event_type,
            status=status,
            summary="decision matrix probe",
            milestone="M1",
            blocker="B" if event_type == "BLOCKED" else None,
            history_candidate=history_candidate,
            event_id=f"MATRIX-{event_type}-{history_candidate}",
            timestamp="2026-08-01T10:00:00+09:00",
        )

    def test_the_matrix_covers_every_event_type_in_the_schema(self):
        """If this fails, a new event type was added without a filter rule."""
        self.assertEqual(set(self.EXPECTED), set(EVENT_TYPES))
        self.assertEqual(set(self.EXPECTED_CATEGORY), set(EVENT_TYPES))

    def test_every_event_type_gets_its_specified_decision(self):
        history_filter = HistoryFilter()
        for event_type, expected in self.EXPECTED.items():
            with self.subTest(event_type=event_type):
                result = history_filter.evaluate(self._event(event_type))
                self.assertIs(result.decision, expected)

    def test_history_candidate_false_forces_drop_for_every_event_type(self):
        """docs/02 section 36 overrides every automatic rule, including KEEP."""
        history_filter = HistoryFilter()
        for event_type in EVENT_TYPES:
            with self.subTest(event_type=event_type):
                result = history_filter.evaluate(
                    self._event(event_type, history_candidate=False)
                )
                self.assertIs(result.decision, HistoryDecision.DROP)
                self.assertEqual(result.reason, "history_candidate is false")

    def test_category_mapping_is_stable_for_every_event_type(self):
        """docs/06 section 17 routes a candidate to its Daily section by
        category, so an unmapped category means a KEEP candidate that is
        counted but has no detail block."""
        history_filter = HistoryFilter()
        for event_type, expected in self.EXPECTED_CATEGORY.items():
            with self.subTest(event_type=event_type):
                result = history_filter.evaluate(self._event(event_type))
                self.assertEqual(result.candidate.category, expected)

    def test_every_kept_event_type_has_a_renderable_category(self):
        """The property that actually matters: anything that reaches Company
        History must land in one of docs/06 section 17's sections."""
        renderable = {"DECISION", "MILESTONE", "ISSUE", "LEARNING"}
        history_filter = HistoryFilter()
        for event_type, decision in self.EXPECTED.items():
            if decision is not HistoryDecision.KEEP:
                continue
            with self.subTest(event_type=event_type):
                result = history_filter.evaluate(self._event(event_type))
                self.assertIn(result.candidate.category, renderable)

    def test_drop_candidates_are_never_persisted(self):
        """docs/05 section 49: DROP data does not need to be stored."""
        import tempfile as _tempfile

        tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        history_filter = HistoryFilter()

        for event_type in ("STARTED", "RESUMED"):
            with self.subTest(event_type=event_type):
                result = history_filter.evaluate(self._event(event_type))
                self.assertFalse(repo.save(result.candidate))

        self.assertEqual(list((root / "keep").glob("*.json")), [])
        self.assertFalse((root / "review").exists())


class DailyHistoryFormatGuardTests(unittest.TestCase):
    """docs/06 sections 14-17: the Daily History file's structure."""

    SPEC_SECTION_ORDER = [
        "Summary",
        "Decisions",
        "Milestones",
        "Issues",
        "Learnings",
        "Evidence",
        "Metadata",
    ]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )
        self.daily_dir = self.root / "daily"

    def _candidate(self, history_id, category, project_id, summary, evidence=()):
        return HistoryCandidate(
            history_id=history_id,
            event_id=history_id.replace("HIST-", ""),
            timestamp="2026-08-05T10:00:00+09:00",
            category=category,
            project_id=project_id,
            role="CTO_FRONTEND",
            summary=summary,
            evidence=tuple(evidence),
            filter_result=HistoryDecision.KEEP,
        )

    def _render_full_day(self) -> str:
        self.repo.save(
            self._candidate("HIST-D1", "DECISION", "CLOSED_BETA", "Beta scope confirmed.")
        )
        self.repo.save(
            self._candidate(
                "HIST-M1", "MILESTONE", "SEARCH_FRONTEND",
                "Search UI implementation completed.", ["TypeScript PASS"],
            )
        )
        self.repo.save(
            self._candidate("HIST-I1", "ISSUE", "AUCTION_DATA_SYNC", "Sync issue found.")
        )
        path = generate_daily_history(self.repo, date(2026, 8, 5), output_dir=self.daily_dir)
        return path.read_text(encoding="utf-8")

    def test_filename_is_the_iso_date(self):
        self._render_full_day()
        self.assertTrue((self.daily_dir / "2026-08-05.md").exists())

    def test_h1_matches_the_spec_template(self):
        text = self._render_full_day()
        self.assertTrue(text.startswith("# DOJOONPASS Company History — 2026-08-05"))

    def test_sections_appear_in_the_spec_order(self):
        text = self._render_full_day()
        headings = [h for h in re.findall(r"^## (.+)$", text, re.M)]
        expected = [s for s in self.SPEC_SECTION_ORDER if s in headings]
        self.assertEqual(headings, expected)

    def test_empty_sections_are_not_padded(self):
        """docs/06 section 14: 내용이 없는 Section을 억지로 채우지 않는다."""
        text = self._render_full_day()
        self.assertNotIn("## Learnings", text)

    def test_category_routes_to_its_section(self):
        """docs/06 section 17's category -> section table."""
        text = self._render_full_day()
        for heading in ("## Decisions", "## Milestones", "## Issues"):
            self.assertIn(heading, text)

    def test_item_block_uses_the_spec_template(self):
        text = self._render_full_day()
        self.assertIn("### Search Frontend", text)
        self.assertIn("- Search UI implementation completed.", text)
        self.assertIn("- Owner: CTO Frontend", text)
        self.assertIn("- Event ID: M1", text)

    def test_evidence_lines_are_prefixed_with_the_event_id(self):
        text = self._render_full_day()
        self.assertIn("- M1: TypeScript PASS", text)

    def test_metadata_block_carries_the_spec_fields(self):
        text = self._render_full_day()
        self.assertIn("- History Date: 2026-08-05", text)
        self.assertIn("- Source: DOJOONPASS Company Ops", text)
        self.assertRegex(text, r"- Generated At: \S+")

    def test_empty_day_still_produces_a_file(self):
        """docs/06 section 25 / docs/10 section 38: Empty Day도 정상이다."""
        path = generate_daily_history(self.repo, date(2026, 8, 6), output_dir=self.daily_dir)
        text = path.read_text(encoding="utf-8")
        self.assertIn("No material company history recorded.", text)
        self.assertIn("- Event Count: 0", text)


class DashboardProductionWiringTests(unittest.TestCase):
    """Notion Dashboard Production 연결 (CEO 승인 A안).

    Audit finding GAP-1: `record_run()`, its OPS_RUNS property builder and its
    pending-retry queue were fully implemented and tested, but no entrypoint
    ever constructed a `dashboard_client`, so `app.runner.run_once()` always
    received None and CEO Decision ④'s Operations Dashboard had never recorded
    a single run in production.

    The client is now built from configuration, alongside the Sync client.
    Real credentials are out of scope for this Sprint (환경 연결은 별도 일정)
    — these tests use placeholder ids only and never touch the network.
    """

    def test_config_exposes_an_optional_ops_runs_database_id(self):
        from notion.config import NotionConfig

        config = NotionConfig.from_env(
            {
                "NOTION_API_TOKEN": "placeholder-token",
                "NOTION_PROJECTS_DATABASE_ID": "placeholder-projects-db",
                "NOTION_OPS_RUNS_DATABASE_ID": "placeholder-ops-runs-db",
            }
        )
        self.assertEqual(config.ops_runs_database_id, "placeholder-ops-runs-db")

    def test_ops_runs_id_is_optional_and_absent_means_none(self):
        from notion.config import NotionConfig

        config = NotionConfig.from_env(
            {
                "NOTION_API_TOKEN": "placeholder-token",
                "NOTION_PROJECTS_DATABASE_ID": "placeholder-projects-db",
            }
        )
        self.assertIsNone(config.ops_runs_database_id)

    def test_an_empty_ops_runs_id_is_treated_as_unset(self):
        from notion.config import NotionConfig

        config = NotionConfig.from_env(
            {
                "NOTION_API_TOKEN": "placeholder-token",
                "NOTION_PROJECTS_DATABASE_ID": "placeholder-projects-db",
                "NOTION_OPS_RUNS_DATABASE_ID": "",
            }
        )
        self.assertIsNone(config.ops_runs_database_id)

    def test_the_dashboard_id_is_not_required_for_notion_sync(self):
        """An existing install that never sets it must keep working."""
        from notion.config import NotionConfig, NotionConfigError

        with self.assertRaises(NotionConfigError):
            NotionConfig.from_env({"NOTION_OPS_RUNS_DATABASE_ID": "only-this"})

    def test_entrypoint_builds_and_passes_a_dashboard_client(self):
        """The wiring GAP-1 was about: run_company_ops.py must construct the
        client and hand it to run_once()."""
        entrypoint = (REPO_ROOT / "run_company_ops.py").read_text(encoding="utf-8")

        self.assertIn("_build_notion_clients", entrypoint)
        self.assertIn("dashboard_client=dashboard_client", entrypoint)
        self.assertIn("ops_runs_database_id", entrypoint)

    def test_env_example_documents_the_new_variable_without_a_value(self):
        """docs/04 sections 40-41: the template is tracked, values never are."""
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("NOTION_OPS_RUNS_DATABASE_ID=", text)
        for line in text.splitlines():
            if line.startswith("NOTION_OPS_RUNS_DATABASE_ID="):
                self.assertEqual(line.split("=", 1)[1].strip(), "")

    def test_dashboard_and_sync_share_one_transport_but_target_different_dbs(self):
        """A Dashboard client bound to the PROJECTS database would write run
        records into Current State — the one mistake record_run()'s docstring
        explicitly warns about."""
        entrypoint = (REPO_ROOT / "run_company_ops.py").read_text(encoding="utf-8")
        self.assertIn("database_id=config.projects_database_id", entrypoint)
        self.assertIn("database_id=config.ops_runs_database_id", entrypoint)


class DecisionAuthorityCharacterizationTests(unittest.TestCase):
    """docs/10 section 17: "COO recommends Beta Scope A" 를 "CEO approved Beta
    Scope A" 로 변환하면 FAIL이다. Recommendation과 Decision을 구분해야 한다.

    Audit finding GAP-7: there is no role x event_type authority rule anywhere
    in validate_event(). ROLES and EVENT_TYPES are independent frozensets, so
    any role may emit DECISION_APPROVED, and HistoryFilter then auto-KEEPs it
    into the Company History "Decisions" section.

    The separation therefore rests entirely on the human choosing the right
    event_type. These tests record that, so the absence of enforcement is
    visible rather than assumed.
    """

    def _decision_event(self, role):
        return create_event(
            source="DESKTOP_1",
            role=role,
            project_id="CLOSED_BETA",
            event_type="DECISION_APPROVED",
            status="IN_PROGRESS",
            summary=f"{role} recommends Beta Scope A",
            history_candidate=True,
            event_id=f"AUTH-{role}",
            timestamp="2026-08-01T10:00:00+09:00",
        )

    def test_every_role_may_emit_a_decision_approved_event(self):
        for role in ROLES:
            with self.subTest(role=role):
                self.assertEqual(validate_event(self._decision_event(role).to_dict()), [])

    def test_every_role_s_decision_is_auto_kept_as_a_decision(self):
        history_filter = HistoryFilter()
        for role in ROLES:
            with self.subTest(role=role):
                result = history_filter.evaluate(self._decision_event(role))
                self.assertIs(result.decision, HistoryDecision.KEEP)
                self.assertEqual(result.candidate.category, "DECISION")

    def test_schema_defines_roles_and_event_types_independently(self):
        """The structural reason there is no authority rule to enforce."""
        self.assertNotIn("DECISION_APPROVED", ROLES)
        self.assertNotIn("COO", EVENT_TYPES)


class TimezoneGroupingCharacterizationTests(unittest.TestCase):
    """BUG-26 (NOT FIXED — the fix needs a decision that has not been made).

    CHARACTERIZATION: asserts today's behaviour, including the misfiling. It
    will fail the day the grouping is normalised, and should then be rewritten
    as the guarantee.

    docs/06 section 12 groups a candidate into the day its "Event timestamp
    falls on". `daily/generator._candidate_date()` implements that as

        datetime.fromisoformat(candidate.timestamp).date()

    which takes the date *as written in the string* and ignores the UTC
    offset entirely. Three things have to line up for that to be safe, and
    only two of them do:

      1. docs/02's schema requires an offset to be present but does NOT
         require it to be +09:00 — '+00:00', 'Z' and '-05:00' all validate.
      2. `events.current_timestamp()` stamps the machine's local offset, so
         a Desktop running in UTC (a VM, a mis-set clock, a machine that
         travelled) emits '+00:00' with no warning anywhere.
      3. Nothing normalises to KST before grouping.

    Measured over the 24 hours of one UTC day, 9 land on the wrong calendar
    day — every Event from 15:00 UTC onward, which is 00:00-08:59 the next
    day in KST:

        2026-08-05T14:00+00:00  = 2026-08-05 23:00 KST -> filed 2026-08-05  OK
        2026-08-05T15:00+00:00  = 2026-08-06 00:00 KST -> filed 2026-08-05  wrong
        2026-08-05T23:00+00:00  = 2026-08-06 08:00 KST -> filed 2026-08-05  wrong

    Company History is README RULE 2's primary record, so this attributes work
    to the wrong day in the record of last resort. It also compounds BUG-17:
    a candidate misfiled into an already-closed day is one the Daily Close
    will never pick up.

    Not fixed here because the correct rule is a decision, not a cleanup —
    normalise to KST at grouping, or constrain the schema to +09:00, or
    normalise at Event creation. Those differ in what happens to Events
    already on disk.
    """

    def _candidate(self, event_id, timestamp):
        return HistoryCandidate(
            history_id=f"HIST-{event_id}",
            event_id=event_id,
            timestamp=timestamp,
            category="MILESTONE",
            project_id="SEARCH_FRONTEND",
            role="COO",
            summary="timezone grouping probe",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def _filed_on(self, timestamp):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        repo.save(self._candidate("TZPROBE", timestamp))
        for day in (date(2026, 8, 5), date(2026, 8, 6)):
            body = generate_daily_history(
                repo, day, output_dir=root / "daily"
            ).read_text(encoding="utf-8")
            if "TZPROBE" in body:
                return day
        return None

    def test_the_schema_accepts_a_non_kst_offset(self):
        """Precondition 1: this is reachable, not hypothetical."""
        for offset in ("+00:00", "-05:00", "+05:30"):
            with self.subTest(offset=offset):
                data = create_event(
                    source="DESKTOP_1",
                    role="COO",
                    project_id="SEARCH_FRONTEND",
                    event_type="MILESTONE_COMPLETED",
                    status="IN_PROGRESS",
                    summary="s",
                    milestone="M1",
                    history_candidate=True,
                    event_id="TZ-SCHEMA",
                    timestamp="2026-08-05T10:00:00+09:00",
                ).to_dict()
                data["timestamp"] = f"2026-08-05T10:00:00{offset}"
                self.assertEqual(validate_event(data), [])

    def test_a_kst_timestamp_is_filed_on_its_own_day(self):
        """The path that works, so the test below cannot pass vacuously."""
        self.assertEqual(self._filed_on("2026-08-05T23:00:00+09:00"), date(2026, 8, 5))
        self.assertEqual(self._filed_on("2026-08-06T00:30:00+09:00"), date(2026, 8, 6))

    def test_a_utc_timestamp_after_15_00_is_filed_a_day_early(self):
        """15:00 UTC is 00:00 the next day in KST, but it is filed as the 5th.

        If this fails, BUG-26 was fixed — rewrite this class as the guarantee.
        """
        self.assertEqual(self._filed_on("2026-08-05T15:00:00+00:00"), date(2026, 8, 5))
        self.assertEqual(self._filed_on("2026-08-05T23:00:00+00:00"), date(2026, 8, 5))

    def test_the_grouping_never_converts_to_a_common_timezone(self):
        """The structural cause, so a refactor cannot lose the finding."""
        source = inspect.getsource(sys.modules["daily.generator"]._candidate_date)
        self.assertIn("fromisoformat", source)
        self.assertNotIn("astimezone", source)


class EventTypeStatusCoherenceTests(unittest.TestCase):
    """BUG-28 (NOT FIXED — closing it changes what the schema accepts).

    CHARACTERIZATION: asserts today's behaviour, including the gap.

    docs/02 section 26 gives an Event Type -> Status table and then states a
    rule, not a suggestion:

        명백하게 모순되는 조합은 Validation에서 거부한다.
        예: event_type = COMPLETED, status = NOT_STARTED -> REJECT

    `events/schema.py` implements exactly two of the table's eight rows:

        COMPLETED -> status must be COMPLETED
        CANCELLED -> status must be CANCELLED

    The spec's own example is therefore covered, and nothing else is. Six
    combinations of the same kind — an Event asserting progress while the
    status says the work has not started — validate cleanly:

        STARTED             + NOT_STARTED  -> DROP
        RESUMED             + NOT_STARTED  -> DROP
        RESUMED             + BLOCKED      -> DROP
        BLOCKED             + NOT_STARTED  -> REVIEW
        ISSUE_RESOLVED      + NOT_STARTED  -> KEEP
        MILESTONE_COMPLETED + NOT_STARTED  -> KEEP

    The last two are the ones that matter: KEEP means the contradiction is
    written into Company History, so "a milestone was completed" is recorded
    for a project the same Event says has not started. Company History is
    README RULE 2's primary record, and nothing downstream re-checks
    coherence.

    Not fixed here because rejecting more combinations makes previously-valid
    Events invalid — a schema decision with a migration question attached
    (what happens to Events already on disk, and to Desktop 1/2 Reporters
    already emitting them).
    """

    def _event(self, event_type, status):
        return create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type=event_type,
            status=status,
            summary="coherence probe",
            milestone="M1",
            blocker="B",
            history_candidate=True,
            event_id=f"COH-{event_type}-{status}",
            timestamp="2026-08-05T10:00:00+09:00",
        )

    def test_the_two_implemented_rules_still_reject(self):
        """The spec's own example, and its sibling. These must not regress."""
        for event_type, status in (("COMPLETED", "NOT_STARTED"), ("CANCELLED", "IN_PROGRESS")):
            with self.subTest(event_type=event_type, status=status):
                with self.assertRaises(Exception):
                    self._event(event_type, status)

    def test_a_coherent_combination_is_accepted(self):
        """So the tests below cannot pass by rejecting everything."""
        self.assertEqual(validate_event(self._event("STARTED", "IN_PROGRESS").to_dict()), [])
        self.assertEqual(
            validate_event(self._event("MILESTONE_COMPLETED", "COMPLETED").to_dict()), []
        )

    def test_six_clearly_contradictory_combinations_are_still_accepted(self):
        """If this fails, BUG-28 was fixed — rewrite the class as the rule."""
        for event_type, status in (
            ("STARTED", "NOT_STARTED"),
            ("RESUMED", "NOT_STARTED"),
            ("RESUMED", "BLOCKED"),
            ("BLOCKED", "NOT_STARTED"),
            ("ISSUE_RESOLVED", "NOT_STARTED"),
            ("MILESTONE_COMPLETED", "NOT_STARTED"),
        ):
            with self.subTest(event_type=event_type, status=status):
                self.assertEqual(validate_event(self._event(event_type, status).to_dict()), [])

    def test_a_contradictory_milestone_reaches_company_history(self):
        """The consequence, not just the acceptance: KEEP means it is written."""
        for event_type in ("MILESTONE_COMPLETED", "ISSUE_RESOLVED"):
            with self.subTest(event_type=event_type):
                event = self._event(event_type, "NOT_STARTED")
                self.assertIs(
                    HistoryFilter().evaluate(event).decision, HistoryDecision.KEEP
                )

    def test_only_two_coherence_rules_exist_in_the_schema(self):
        """The structural cause, so a refactor cannot lose the finding."""
        source = (SRC / "events" / "schema.py").read_text(encoding="utf-8")
        rules = re.findall(r'if event_type == "(\w+)" and status != "(\w+)"', source)
        self.assertEqual(sorted(r[0] for r in rules), ["CANCELLED", "COMPLETED"])


class KeepIndexContractTests(unittest.TestCase):
    """`build_keep_index()` buckets whatever it is given.

    Its name and docstring both say KEEP, but it does not filter by decision —
    handed a REVIEW candidate it indexes it, and `generate_daily_history()`
    renders whatever the index contains. The only thing keeping REVIEW
    candidates out of Company History is that the single production caller
    remembers to pass `decision=HistoryDecision.KEEP`.

    That caller is correct today (verified). This pins it, because the failure
    mode of losing that argument is silent: REVIEW candidates — the ones a
    human has NOT yet confirmed — would start appearing in the official
    record with nothing to indicate they were unreviewed.
    """

    def test_the_scheduler_asks_the_repository_for_keep_only(self):
        source = (SRC / "scheduler" / "scheduler.py").read_text(encoding="utf-8")
        self.assertIn("build_keep_index(repository.list(decision=HistoryDecision.KEEP))", source)

    def test_build_keep_index_itself_does_not_filter(self):
        """Characterization of why the caller has to be careful."""
        from daily import build_keep_index

        def candidate(event_id, decision):
            return HistoryCandidate(
                history_id=f"HIST-{event_id}",
                event_id=event_id,
                timestamp="2026-08-05T10:00:00+09:00",
                category="MILESTONE",
                project_id="SEARCH_FRONTEND",
                role="COO",
                summary="index probe",
                evidence=(),
                filter_result=decision,
            )

        index = build_keep_index(
            [candidate("K-1", HistoryDecision.KEEP), candidate("R-1", HistoryDecision.REVIEW)]
        )
        indexed = [c.event_id for items in index.values() for c in items]
        self.assertEqual(sorted(indexed), ["K-1", "R-1"])

    def test_review_candidates_stay_out_of_the_daily_history(self):
        """The property that must hold end to end, whatever the plumbing."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        for event_id, decision in (("K-9", HistoryDecision.KEEP), ("R-9", HistoryDecision.REVIEW)):
            repo.save(
                HistoryCandidate(
                    history_id=f"HIST-{event_id}",
                    event_id=event_id,
                    timestamp="2026-08-05T10:00:00+09:00",
                    category="MILESTONE",
                    project_id="SEARCH_FRONTEND",
                    role="COO",
                    summary="daily probe",
                    evidence=(),
                    filter_result=decision,
                )
            )

        body = generate_daily_history(
            repo, date(2026, 8, 5), output_dir=root / "daily"
        ).read_text(encoding="utf-8")

        self.assertIn("K-9", body)
        self.assertNotIn("R-9", body)


class CategoryRenderingCoverageTests(unittest.TestCase):
    """BUG-34: a candidate whose category the renderer does not know is
    dropped from the Daily History body — but still counted in its Metadata.

    `daily/markdown.py` renders one section per entry in `_CATEGORY_ORDER`
    ('DECISION', 'MILESTONE', 'ISSUE', 'LEARNING'). A candidate with any other
    category matches no section and is never emitted. The Metadata block,
    however, is built from `len(candidates)` — every candidate, rendered or
    not. Measured with 6 candidates of which 2 had unknown categories:

        rendered in the body : 4
        "- Event Count:"     : 6

    So the record does not merely omit the candidate, it states a count that
    contradicts its own contents. Nothing warns.

    LATENT, NOT LIVE — and the scope had to be corrected once. Every KEEP
    candidate the filter produces carries DECISION, ISSUE or MILESTONE, all of
    which the renderer knows, so no Daily History written today loses anything.

    The one category the renderer cannot handle is `None`, and the filter does
    produce it — for `CANCELLED` + `CANCELLED`, which is the only status the
    schema allows with a CANCELLED event. That candidate is judged REVIEW, and
    REVIEW candidates are excluded from the Daily History by design (the
    Scheduler asks the repository for KEEP only), so it never reaches the
    renderer and nothing is lost.

    So the invariant that actually protects Company History is narrower than
    "every category the filter emits": it is "every category the filter emits
    ON A KEEP CANDIDATE". That is what the guard below asserts. A broader
    version fails on the harmless REVIEW/None case and would have to be
    muted — which is worse than not having it.

    What keeps this worth guarding: `history/review.py` says promoting a
    REVIEW candidate to KEEP is "not part of this Phase". The day it is, the
    CANCELLED candidate becomes a KEEP with category=None and disappears from
    Company History while the Metadata still counts it.

    Note 'LEARNING' is the reverse case: the renderer has a section for it and
    the filter never produces it. Harmless (an empty section is skipped), but
    it shows the two lists are already out of step in one direction.
    """

    def _filter_categories(self, *, decisions):
        from events.schema import EVENT_TYPES, STATUSES

        emitted = set()
        for event_type in sorted(EVENT_TYPES):
            for status in sorted(STATUSES):
                try:
                    event = create_event(
                        source="DESKTOP_1",
                        role="COO",
                        project_id="SEARCH_FRONTEND",
                        event_type=event_type,
                        status=status,
                        summary="category coverage probe",
                        milestone="M1",
                        blocker="B",
                        history_candidate=True,
                        event_id=f"CAT-{event_type}-{status}",
                        timestamp="2026-08-05T10:00:00+09:00",
                    )
                except Exception:
                    continue  # schema rejects this combination
                result = HistoryFilter().evaluate(event)
                if result.decision in decisions:
                    emitted.add(result.candidate.category)
        return emitted

    def test_every_keep_category_can_be_rendered(self):
        """THE GUARD. If a new category is added to HistoryFilter's KEEP path
        without a matching section, this fails instead of Company History
        silently losing entries."""
        import daily.markdown as markdown

        keep_categories = self._filter_categories(decisions={HistoryDecision.KEEP})
        unrenderable = keep_categories - set(markdown._CATEGORY_ORDER)

        self.assertEqual(
            unrenderable,
            set(),
            f"HistoryFilter emits {unrenderable} on a KEEP candidate, which "
            f"daily/markdown.py cannot render — those candidates would vanish "
            f"from Company History",
        )

    def test_the_review_path_does_produce_an_unrenderable_category(self):
        """Characterization of the latent case, and of why the guard above is
        scoped to KEEP. CANCELLED+CANCELLED yields category=None."""
        import daily.markdown as markdown

        review_categories = self._filter_categories(decisions={HistoryDecision.REVIEW})

        self.assertIn(None, review_categories)
        self.assertNotIn(None, markdown._CATEGORY_ORDER)

    def test_the_unrenderable_review_candidate_never_reaches_the_daily(self):
        """Why it is harmless today: REVIEW is excluded before rendering."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")

        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="CANCELLED",
            status="CANCELLED",
            summary="cancelled project",
            milestone="M1",
            history_candidate=True,
            event_id="CANCEL-1",
            timestamp="2026-08-05T10:00:00+09:00",
        )
        result = HistoryFilter().evaluate(event)
        self.assertIs(result.decision, HistoryDecision.REVIEW)
        self.assertIsNone(result.candidate.category)
        repo.save(result.candidate)

        body = generate_daily_history(
            repo, date(2026, 8, 5), output_dir=root / "daily"
        ).read_text(encoding="utf-8")

        self.assertNotIn("CANCEL-1", body)
        self.assertIn("- Event Count: 0", body)

    def test_an_unknown_category_is_dropped_from_the_body(self):
        """Characterization of the mechanism the guard protects against."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")

        categories = ["MILESTONE", "DECISION", "ISSUE", "LEARNING", "UNKNOWN_CAT", ""]
        for i, category in enumerate(categories):
            repo.save(
                HistoryCandidate(
                    history_id=f"HIST-CAT-{i}",
                    event_id=f"CAT-{i}",
                    timestamp="2026-08-05T10:00:00+09:00",
                    category=category,
                    project_id="SEARCH_FRONTEND",
                    role="COO",
                    summary=f"category {category or 'empty'}",
                    evidence=(),
                    filter_result=HistoryDecision.KEEP,
                )
            )

        body = generate_daily_history(
            repo, date(2026, 8, 5), output_dir=root / "daily"
        ).read_text(encoding="utf-8")

        for i, category in enumerate(categories):
            with self.subTest(category=category or "(empty)"):
                known = category in ("MILESTONE", "DECISION", "ISSUE", "LEARNING")
                self.assertEqual(f"CAT-{i}" in body, known)

    def test_the_metadata_count_includes_candidates_it_did_not_render(self):
        """The part that turns an omission into a contradiction."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")

        for i, category in enumerate(["MILESTONE", "UNKNOWN_A", "UNKNOWN_B"]):
            repo.save(
                HistoryCandidate(
                    history_id=f"HIST-MC-{i}",
                    event_id=f"MC-{i}",
                    timestamp="2026-08-05T10:00:00+09:00",
                    category=category,
                    project_id="SEARCH_FRONTEND",
                    role="COO",
                    summary="metadata count probe",
                    evidence=(),
                    filter_result=HistoryDecision.KEEP,
                )
            )

        body = generate_daily_history(
            repo, date(2026, 8, 5), output_dir=root / "daily"
        ).read_text(encoding="utf-8")

        rendered = sum(1 for i in range(3) if f"MC-{i}" in body)
        self.assertEqual(rendered, 1)
        self.assertIn("- Event Count: 3", body)


class SourceAndRoleIndependenceTests(unittest.TestCase):
    """docs/02 section 8: `source`와 `role`은 동일한 개념이 아니다.

    VERIFIED CORRECT — this is a guard, not a finding.

    The spec draws the distinction explicitly: `source` answers "어느
    컴퓨터에서 발생했는가", `role` answers "어떤 조직 역할의 업무인가". The
    Desktop table in the same section is headed "현재 역할" — it describes who
    happens to sit where today, not a rule the schema may enforce.

    So all 4x4 combinations validating is correct behaviour, including ones
    that look odd (CTO_BACKEND work reported from DESKTOP_4). Someone
    reviewing this later could easily mistake that for a missing constraint
    and "fix" it, which would start rejecting valid Events the moment anyone
    works from a different machine — exactly what the spec separates the two
    fields to allow.
    """

    def test_every_source_and_role_combination_is_accepted(self):
        import itertools

        from events.schema import ROLES, SOURCES

        for role, source in itertools.product(sorted(ROLES), sorted(SOURCES)):
            with self.subTest(role=role, source=source):
                event = create_event(
                    source=source,
                    role=role,
                    project_id="SEARCH_FRONTEND",
                    event_type="MILESTONE_COMPLETED",
                    status="IN_PROGRESS",
                    summary="source/role independence",
                    milestone="M1",
                    history_candidate=True,
                    event_id=f"RS-{role}-{source}",
                    timestamp="2026-08-05T10:00:00+09:00",
                )
                self.assertEqual(validate_event(event.to_dict()), [])

    def test_the_schema_declares_them_as_separate_value_sets(self):
        from events.schema import ROLES, SOURCES

        self.assertEqual(ROLES & SOURCES, frozenset())


class DailyRenderOrderStabilityTests(unittest.TestCase):
    """Company History must render the same way every time it is generated.

    VERIFIED CORRECT — a guard for a property nothing else asserts.

    Candidates are sorted by `timestamp`, and several Events on one day can
    share a timestamp to the second. If the tie were broken by whatever order
    the filesystem happened to return, the same inputs would produce different
    Markdown on different machines or after a re-generation — and Company
    History is the record other things are reconciled against.

    Measured over five generations with the save order reversed on alternate
    runs: identical output every time.
    """

    def _render_order(self, event_ids, timestamp):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")

        for event_id in event_ids:
            repo.save(
                HistoryCandidate(
                    history_id=f"HIST-{event_id}",
                    event_id=event_id,
                    timestamp=timestamp,
                    category="MILESTONE",
                    project_id="SEARCH_FRONTEND",
                    role="COO",
                    summary=f"work {event_id}",
                    evidence=(),
                    filter_result=HistoryDecision.KEEP,
                )
            )

        body = generate_daily_history(
            repo, date(2026, 8, 5), output_dir=root / "daily"
        ).read_text(encoding="utf-8")

        seen = []
        for match in re.findall(r"ORD-[A-Z]", body):
            if match not in seen:
                seen.append(match)
        return tuple(seen)

    def test_identical_timestamps_render_in_a_stable_order(self):
        timestamp = "2026-08-05T10:00:00+09:00"
        forward = ["ORD-A", "ORD-B", "ORD-C", "ORD-D"]

        first = self._render_order(forward, timestamp)
        self.assertEqual(len(first), 4)

        for _ in range(2):
            self.assertEqual(self._render_order(forward, timestamp), first)
            self.assertEqual(self._render_order(list(reversed(forward)), timestamp), first)


class OutOfRangeCandidateDateTests(unittest.TestCase):
    """BUG-46 (NOT FIXED): a KEEP candidate dated outside the Scheduler's
    window is stored and then never rendered.

    CHARACTERIZATION: asserts today's behaviour.

    The Scheduler generates Daily History for dates from
    `history_start_date` through yesterday. A candidate is bucketed by its own
    Event timestamp. Nothing checks that the bucket falls inside the window, so
    a candidate outside it is saved to keep/, counted as KEEP, and never
    appears in any Daily History file. It is not reported anywhere either —
    `generated_dates` lists the days that WERE written, never the candidates
    that matched none of them.

    Measured with history_start_date = 2026-08-05:

        Event dated 2026-08-01 (before the window)  stored, NEVER rendered
        Event dated 2026-08-05 (on the boundary)    rendered
        Event dated 2026-08-06                      rendered

    The reachable form is a backdated Event, not an absurd one: an Event that
    arrives late from a Desktop that was offline, a machine whose clock is
    behind, or any work reported for a day before Company History started.
    docs/02 places no lower bound on `timestamp` — year 0001 and year 9999
    both validate — so nothing rejects it earlier in the pipeline either.

    Same family as BUG-26 (misfiled a day early by timezone) and BUG-34
    (unrenderable category): the candidate survives every integrity check and
    is simply absent from the record, with no signal.

    Not fixed: rendering it means either extending the Scheduler window
    backwards (which would rewrite already-closed days) or reporting orphans
    somewhere. Both are decisions.
    """

    def _store(self, repo, event_id, timestamp):
        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary=f"candidate {event_id}",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp=timestamp,
        )
        repo.save(HistoryFilter().evaluate(event).candidate)

    def _run_scheduler(self, start, now):
        from scheduler.scheduler import run_once as scheduler_run_once

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")

        self._store(repo, "BEFORE", "2026-08-01T10:00:00+09:00")
        self._store(repo, "ONDATE", "2026-08-05T10:00:00+09:00")
        self._store(repo, "AFTER", "2026-08-06T10:00:00+09:00")

        scheduler_run_once(
            repo,
            history_start_date=start,
            now=now,
            state_path=root / "state.json",
            lock_path=root / "lock",
            daily_output_dir=root / "daily",
        )
        rendered = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted((root / "daily").glob("*.md"))
        )
        return repo, rendered

    def test_all_three_candidates_are_stored_as_keep(self):
        """They pass every check — the loss is purely in rendering."""
        from datetime import timezone

        kst = timezone(timedelta(hours=9))
        repo, _ = self._run_scheduler(date(2026, 8, 5), datetime(2026, 8, 7, 11, 0, tzinfo=kst))

        self.assertEqual(len(repo.list(decision=HistoryDecision.KEEP)), 3)

    def test_a_candidate_before_the_window_is_never_rendered(self):
        from datetime import timezone

        kst = timezone(timedelta(hours=9))
        _, rendered = self._run_scheduler(date(2026, 8, 5), datetime(2026, 8, 7, 11, 0, tzinfo=kst))

        self.assertIn("ONDATE", rendered)
        self.assertIn("AFTER", rendered)
        self.assertNotIn("BEFORE", rendered)

    def test_the_schema_places_no_bound_on_the_timestamp(self):
        """Why nothing rejects it earlier."""
        for timestamp in ("0001-01-01T00:00:00+09:00", "9999-12-31T23:59:59+09:00"):
            with self.subTest(timestamp=timestamp):
                event = create_event(
                    source="DESKTOP_1",
                    role="COO",
                    project_id="SEARCH_FRONTEND",
                    event_type="MILESTONE_COMPLETED",
                    status="IN_PROGRESS",
                    summary="bound probe",
                    milestone="M1",
                    history_candidate=True,
                    event_id=f"BOUND-{timestamp[:4]}",
                    timestamp=timestamp,
                )
                self.assertEqual(validate_event(event.to_dict()), [])

    def test_an_empty_timestamp_is_still_rejected_by_the_validator(self):
        """Guard against a misreading: create_event substitutes the current
        time for a falsy timestamp, which can look like acceptance. The
        validator itself rejects a literal empty string."""
        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="empty timestamp probe",
            milestone="M1",
            history_candidate=True,
            event_id="TS-EMPTY",
            timestamp="",
        )
        self.assertNotEqual(event.timestamp, "")

        data = event.to_dict()
        data["timestamp"] = ""
        self.assertIn("timestamp is not valid ISO-8601: ''", validate_event(data))


class BackupStepOrderGuardTests(unittest.TestCase):
    """The Backup deletion gate depends on `git status` running BEFORE
    `git add`, and nothing asserted that.

    VERIFIED CORRECT — this is a guard, and it exists because the audit
    initially mistook the parser for broken.

    `_parse_porcelain()` classifies a line as a deletion by looking for "D" in
    the two-character status code. It does NOT understand git's rename form:

        R  daily/old.md -> daily/new.md    ->  changed, NOT deleted

    That looks like a hole in the safety gate that blocks a backup when
    Company History files disappear (docs/08 sections 31, 44-47). It is not,
    because of ordering. Measured against a real repository, a rename reports
    differently depending on whether it has been staged:

        before `git add`   " D daily/old.md" + "?? daily/new.md"  -> gate FIRES
        after  `git add`   "R  daily/old.md -> daily/new.md"      -> gate would miss

    backup/runner.py calls `git_status()` first and only calls `git_add_all()`
    once it has decided to proceed, so the parser only ever sees the pre-add
    form and the gate is correct.

    The ordering is therefore load-bearing rather than incidental: moving
    `git_add_all` before `git_status` would silently stop renames — and
    therefore disappearing Company History files — from blocking a backup,
    with every existing test still passing.
    """

    def _run_once_source(self):
        return (SRC / "backup" / "runner.py").read_text(encoding="utf-8")

    def test_git_status_is_called_before_git_add(self):
        source = self._run_once_source()

        status_at = source.index("git_status(working_copy_dir)")
        add_at = source.index("git_add_all(working_copy_dir)")

        self.assertLess(
            status_at,
            add_at,
            "git_add_all() must not run before git_status(): a staged rename "
            "reports as 'R old -> new', which _parse_porcelain() does not "
            "classify as a deletion, so the deletion gate would stop firing",
        )

    def test_the_parser_reads_the_pre_add_deletion_form(self):
        from backup.git_ops import _parse_porcelain

        result = _parse_porcelain(" D daily/old.md\n?? daily/new.md")

        self.assertEqual(list(result.deleted_files), ["daily/old.md"])
        self.assertEqual(list(result.changed_files), ["daily/new.md"])

    def test_the_parser_does_not_understand_the_post_add_rename_form(self):
        """Characterization of exactly why the ordering matters."""
        from backup.git_ops import _parse_porcelain

        result = _parse_porcelain("R  daily/old.md -> daily/new.md")

        self.assertEqual(list(result.deleted_files), [])

    def test_a_path_containing_d_is_not_mistaken_for_a_deletion(self):
        """The status code is read from the first two columns only."""
        from backup.git_ops import _parse_porcelain

        result = _parse_porcelain(" M daily/DRAFT.md")

        self.assertEqual(list(result.deleted_files), [])
        self.assertEqual(list(result.changed_files), ["daily/DRAFT.md"])


class RecordRunDocstringTests(unittest.TestCase):
    """DOC-2 (FIXED): `record_run()`'s docstring claimed it had no caller.

    app/runner.py calls it once per Runner execution, right after the
    Collector step. The stale wording was the same hazard already corrected
    for `scan_for_secrets()` — it invites someone to delete a live call.
    """

    def test_the_docstring_no_longer_denies_having_a_caller(self):
        source = (SRC / "collector" / "state.py").read_text(encoding="utf-8")

        self.assertNotIn("Not called automatically by anything yet", source)

    def test_the_runner_really_does_call_it(self):
        runner_source = (SRC / "app" / "runner.py").read_text(encoding="utf-8")

        self.assertIn("seen_store.record_run(", runner_source)


class StaleDocstringSweepTests(unittest.TestCase):
    """DOC-3 (FIXED) plus the guard that made finding it systematic.

    Two stale "this has no caller" docstrings were found separately
    (`scan_for_secrets`, `record_run`), so the whole source tree was swept for
    the same class of claim. Eight docstrings assert something is not called,
    not implemented, or not yet used. Six were verified TRUE:

        git_ops        "git pull (never called, anywhere)"        TRUE
        file_repository  D:\\DOJOONPASS_COO\\history\\ unused      TRUE
        dashboard      bootstrap_* "Never called by the Runtime"  TRUE
        scheduler      Collector/Notion/Backup/Transport not      TRUE
                       called from here
        (plus the two already corrected)

    One was false: `backup/working_copy.py`'s MODULE docstring still said
    "Secret Scan (section 29) is not implemented here — Issue #3 ... still
    unresolved", even though `scan_for_secrets()` is defined in that very file,
    is called by backup/runner.py, and fails the backup on a match. The
    function-level docstring had been corrected earlier; the module-level one
    was missed. Now corrected.

    This test keeps the two most load-bearing claims honest, because both
    describe security or data-safety properties that a reader would otherwise
    have to re-derive.
    """

    def test_the_module_docstring_no_longer_denies_the_secret_scan(self):
        source = (SRC / "backup" / "working_copy.py").read_text(encoding="utf-8")

        self.assertNotIn("Secret Scan (section 29) is not implemented here", source)
        self.assertIn("Secret Scan (section 29) IS implemented here", source)
        # The corrected text legitimately mentions Issue #3 while explaining
        # what it used to claim, so assert the RESOLUTION is stated, not that
        # the phrase is absent.
        self.assertIn("Issue #3 was decided", source)

    def test_the_git_pull_prohibition_claim_is_still_true(self):
        """git_ops.py's docstring says `git pull` is never called anywhere."""
        code = "\n".join(
            _executable_lines(p.read_text(encoding="utf-8")) for p in _source_files()
        )
        self.assertNotIn('"pull"', code)
        self.assertNotIn("'pull'", code)

    def test_the_scheduler_isolation_claim_is_still_true(self):
        """scheduler.py's docstring says it calls none of the other stages."""
        tree = ast.parse((SRC / "scheduler" / "scheduler.py").read_text(encoding="utf-8"))
        modules = [
            getattr(node, "module", "") or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        for forbidden in ("collector", "notion", "backup", "transport"):
            with self.subTest(module=forbidden):
                self.assertFalse([m for m in modules if forbidden in m])


class DailyGeneratorArgumentPrecedenceTests(unittest.TestCase):
    """`generate_daily_history()` has three sources for its candidates and
    silently prefers one. Documented here because nothing else states it.

    VERIFIED — not a defect, but a sharp edge.

        neither argument      -> repository.list(decision=KEEP)
        keep_candidates=...   -> that list, repository NOT consulted
        keep_index=...        -> that index, repository NOT consulted
        BOTH                  -> keep_index wins, keep_candidates ignored
                                 silently

    The production caller (scheduler.py) passes only `keep_index`, so the
    ambiguous case is unreachable today. It is worth pinning because passing
    both is the kind of thing a future caller does by accident, and the
    failure would be invisible: a Daily History built from the wrong set of
    candidates still looks completely well-formed.
    """

    def _candidate(self, event_id):
        return HistoryCandidate(
            history_id=f"HIST-{event_id}",
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
            category="MILESTONE",
            project_id="SEARCH_FRONTEND",
            role="COO",
            summary=f"{event_id} work",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )
        self.repo.save(self._candidate("FROM-REPO"))

    def _render(self, name, **kwargs):
        return generate_daily_history(
            self.repo, date(2026, 8, 5), output_dir=self.root / name, **kwargs
        ).read_text(encoding="utf-8")

    def test_without_arguments_it_reads_the_repository(self):
        self.assertIn("FROM-REPO", self._render("a"))

    def test_keep_candidates_replaces_the_repository_lookup(self):
        body = self._render("b", keep_candidates=[self._candidate("FROM-ARG")])

        self.assertIn("FROM-ARG", body)
        self.assertNotIn("FROM-REPO", body)

    def test_keep_index_replaces_the_repository_lookup(self):
        from daily import build_keep_index

        body = self._render("c", keep_index=build_keep_index([self._candidate("FROM-INDEX")]))

        self.assertIn("FROM-INDEX", body)
        self.assertNotIn("FROM-REPO", body)

    def test_when_both_are_given_the_index_wins_silently(self):
        from daily import build_keep_index

        body = self._render(
            "d",
            keep_candidates=[self._candidate("CAND")],
            keep_index=build_keep_index([self._candidate("INDEX")]),
        )

        self.assertIn("INDEX", body)
        self.assertNotIn("CAND", body)

    def test_the_production_caller_passes_only_the_index(self):
        """Why the ambiguous case is unreachable today."""
        scheduler_source = (SRC / "scheduler" / "scheduler.py").read_text(encoding="utf-8")

        self.assertIn("keep_index=keep_index", scheduler_source)
        self.assertNotIn("keep_candidates=", scheduler_source)


class CollectResultContractTests(unittest.TestCase):
    """`Collector.collect()`'s status/event/errors relationship, which
    collector/runtime.py dereferences without checking.

    VERIFIED CORRECT — a guard for an invariant nothing asserted.

    runtime.py writes `result.event.event_id` on both the ACCEPTED and the
    DUPLICATE branch, with no None check. That is safe only because collect()
    guarantees:

        ACCEPTED / DUPLICATE  ->  event is not None, errors empty
        REJECTED              ->  event is None,     errors non-empty

    and because collect() never raises: nine malformed inputs — not JSON, a
    bare array, a bare string, null, an empty string, and schema violations —
    all come back as REJECTED with errors rather than an exception. That is
    what lets runtime.py's per-file guard (docs/03 section 53) work at the
    granularity of one Event.

    If a future change made collect() return ACCEPTED with event=None, or
    raise on some input shape, runtime.py would crash on that file and — per
    the measured behaviour of that loop — the file would stay in incoming/ and
    fail identically on every subsequent run.
    """

    def _collect(self, raw, store=None):
        from collector import InMemorySeenEventStore
        from collector.collector import Collector

        return Collector(seen_store=store or InMemorySeenEventStore()).collect(raw)

    def _valid_raw(self, event_id="CONTRACT-1"):
        import json as json_module

        return json_module.dumps(
            {
                "schema_version": "1.0",
                "event_id": event_id,
                "timestamp": "2026-08-05T10:00:00+09:00",
                "source": "DESKTOP_1",
                "role": "COO",
                "project_id": "SEARCH_FRONTEND",
                "event_type": "MILESTONE_COMPLETED",
                "status": "IN_PROGRESS",
                "summary": "contract probe",
                "milestone": "M1",
                "history_candidate": True,
                "evidence": [],
            }
        )

    def test_accepted_always_carries_an_event_and_no_errors(self):
        result = self._collect(self._valid_raw())

        self.assertIs(result.status, CollectorStatus.ACCEPTED)
        self.assertIsNotNone(result.event)
        self.assertEqual(result.errors, ())

    def test_duplicate_also_carries_an_event(self):
        """runtime.py logs result.event.event_id on this branch too."""
        from collector import InMemorySeenEventStore

        store = InMemorySeenEventStore()
        self._collect(self._valid_raw(), store)

        result = self._collect(self._valid_raw(), store)

        self.assertIs(result.status, CollectorStatus.DUPLICATE)
        self.assertIsNotNone(result.event)
        self.assertEqual(result.event.event_id, "CONTRACT-1")

    def test_rejected_always_carries_errors_and_no_event(self):
        import json as json_module

        rejected_inputs = {
            "not json": "{ broken",
            "empty string": "",
            "bare array": "[]",
            "bare string": '"just a string"',
            "null": "null",
            "schema violation": json_module.dumps({"schema_version": "1.0"}),
        }
        for label, raw in rejected_inputs.items():
            with self.subTest(input=label):
                result = self._collect(raw)

                self.assertIs(result.status, CollectorStatus.REJECTED)
                self.assertIsNone(result.event)
                self.assertTrue(result.errors)

    def test_collect_never_raises_on_any_of_them(self):
        """The property docs/03 section 53's batch resilience rests on."""
        for raw in ("{ broken", "", "[]", '"s"', "null", "\x00\xff", "0"):
            with self.subTest(raw=raw[:12]):
                try:
                    self._collect(raw)
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"collect() raised {type(exc).__name__} on {raw[:20]!r}")

    def test_runtime_dereferences_the_event_without_a_none_check(self):
        """The structural reason this contract has to hold."""
        runtime_source = (SRC / "collector" / "runtime.py").read_text(encoding="utf-8")

        self.assertIn("result.event.event_id", runtime_source)


class PropertyHelperNullGuardTests(unittest.TestCase):
    """`notion/properties.py`'s four builders disagree about falsy input.
    Currently harmless — pinned because it is one caller away from mattering.

    VERIFIED — not a defect today.

        _rich_text(x)  falsy -> {"rich_text": []}        guarded
        _title(x)      None  -> {"content": None}        NOT guarded
        _select(x)     None  -> {"name": None}           NOT guarded
        _date(x)       None  -> {"start": None}          NOT guarded

    Notion rejects a null in any of those three with HTTP 400, and
    ExecutionPlanSync maps every NotionAPIError to NOTION_RETRY_REQUIRED — so
    a null reaching one of them would be a permanently-retried Event
    (BUG-13's shape), not a one-off error.

    It cannot happen through the production path today, and this test records
    why rather than leaving it to be re-derived:

        _title   <- humanize_project_id(event.project_id), which is
                    `project_id.replace("_", " ").title()` — always a str,
                    and project_id is required non-null by docs/02
        _select  <- role / source / status / event_type, all closed enums
        _date    <- event.timestamp, validated ISO-8601 by docs/02

    So every argument is non-null by construction upstream. The guard is in
    the schema, not in these helpers — which is fine, as long as a future
    caller does not pass something optional (a nullable Notion field, a
    default of None) into them.
    """

    def test_rich_text_is_the_only_guarded_helper(self):
        import notion.properties as properties

        guarded = {}
        for name in ("_title", "_rich_text", "_select", "_date"):
            source = inspect.getsource(getattr(properties, name))
            guarded[name] = "if not" in source

        self.assertEqual(
            guarded, {"_title": False, "_rich_text": True, "_select": False, "_date": False}
        )

    def test_the_unguarded_helpers_would_emit_null(self):
        """Characterization: what a null would produce if one ever arrived."""
        import notion.properties as properties

        self.assertIsNone(properties._title(None)["title"][0]["text"]["content"])
        self.assertIsNone(properties._select(None)["select"]["name"])
        self.assertIsNone(properties._date(None)["date"]["start"])

    def test_the_title_argument_can_never_be_none_in_practice(self):
        """humanize_project_id() always returns a str, for every input the
        schema permits."""
        from notion.sync import humanize_project_id

        for project_id in ("SEARCH_FRONTEND", "", "   ", "a", "123", "한글"):
            with self.subTest(project_id=project_id):
                self.assertIsInstance(humanize_project_id(project_id), str)

    def test_an_empty_project_id_produces_an_untitled_page_not_a_null(self):
        """The one reachable edge — accepted by Notion, unlike a null."""
        from notion.sync import humanize_project_id

        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="empty project id probe",
            milestone="M1",
            history_candidate=True,
            event_id="EMPTY-PID",
            timestamp="2026-08-05T10:00:00+09:00",
        )

        properties = build_create_properties(
            event, project_name=humanize_project_id(event.project_id)
        )

        self.assertEqual(properties["Project"]["title"][0]["text"]["content"], "")
        self.assertEqual(properties["Project ID"], {"rich_text": []})


class CreateVersusUpdatePropertyTests(unittest.TestCase):
    """Why the two builders differ, asserted rather than left to inference.

    VERIFIED CORRECT — a guard for an invariant with real consequences.

    Measured difference:

        create only : Owner, Project, Project ID, Source
        update only : (nothing)
        both        : Status, Last Updated, Last Event ID, Last Event Type,
                      Current Milestone

    The split is identity/provenance versus mutable state. `Project ID` is the
    system identifier docs/04 sections 9-10 designates, `Project` is its
    display name, `Source` records which Desktop first reported it, and
    `Owner` the role. None of those describe what is happening now, so an
    update never rewrites them.

    That matters in two directions:

      * An update that DID send them would overwrite a display name a human
        had corrected in Notion — docs/04 sections 9-10 explicitly allow
        `Project` to be fixed by hand there, since project_id is the real
        identifier.
      * `Owner` therefore stays whatever the FIRST event set, even if later
        work on that project comes from a different role. That is a
        consequence of the split, not an accident, and it is recorded here so
        it is not mistaken for a bug later.

    A regression in either direction is silent: the payload stays valid and
    Notion accepts it.
    """

    IDENTITY_PROPERTIES = {"Owner", "Project", "Project ID", "Source"}
    MUTABLE_PROPERTIES = {
        "Status",
        "Last Updated",
        "Last Event ID",
        "Last Event Type",
    }

    def _event(self):
        return create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="create/update split probe",
            milestone="M1",
            history_candidate=True,
            event_id="SPLIT-1",
            timestamp="2026-08-05T10:00:00+09:00",
        )

    def test_only_create_carries_the_identity_properties(self):
        created = set(build_create_properties(self._event(), project_name="Search Frontend"))
        updated = set(build_update_properties(self._event()))

        self.assertEqual(created - updated, self.IDENTITY_PROPERTIES)

    def test_update_sends_nothing_create_does_not(self):
        created = set(build_create_properties(self._event(), project_name="Search Frontend"))
        updated = set(build_update_properties(self._event()))

        self.assertEqual(updated - created, set())

    def test_the_mutable_state_is_sent_by_both(self):
        created = set(build_create_properties(self._event(), project_name="Search Frontend"))
        updated = set(build_update_properties(self._event()))

        self.assertTrue(self.MUTABLE_PROPERTIES <= (created & updated))

    def test_an_update_never_rewrites_a_hand_corrected_project_name(self):
        """The concrete reason the split must hold (docs/04 sections 9-10)."""
        self.assertNotIn("Project", build_update_properties(self._event()))


class EmptySummaryRenderingTests(unittest.TestCase):
    """A reachable cosmetic edge, recorded so it is not re-derived.

    docs/02 requires `summary` to be present but places no constraint on its
    content, so `summary=""` validates. `_render_item_block()` then emits a
    bare "- " bullet into Company History.

    Not a defect worth its own number — nothing is lost, the Markdown is still
    well-formed, and the cause is the already-logged absence of format
    constraints on free-text fields. Pinned because an empty bullet in the
    official record looks like a rendering bug when someone eventually sees
    one, and this says where it came from.

    `role=None` produces a literal "- Owner: None" but is NOT reachable: the
    schema rejects a missing or unknown role, which is asserted below so the
    two cases are not confused.
    """

    def _event(self, **overrides):
        data = dict(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="ordinary summary",
            milestone="M1",
            history_candidate=True,
            event_id="RENDER-1",
            timestamp="2026-08-05T10:00:00+09:00",
        )
        data.update(overrides)
        return create_event(**data)

    def test_an_empty_summary_validates(self):
        self.assertEqual(validate_event(self._event(summary="").to_dict()), [])

    def test_an_empty_summary_renders_a_bare_bullet(self):
        import daily.markdown as markdown

        candidate = HistoryFilter().evaluate(self._event(summary="")).candidate
        block = markdown._render_item_block(candidate)

        self.assertIn("\n- \n", block)

    def test_a_missing_or_unknown_role_is_rejected_so_owner_is_never_none(self):
        for role in (None, "NOBODY"):
            with self.subTest(role=role):
                with self.assertRaises(Exception):
                    self._event(role=role)


class RoleDisplayTableCoverageTests(unittest.TestCase):
    """Two role->display-name tables exist, and that is correct:

        daily/markdown.py::_ROLE_DISPLAY_NAMES     docs/06 Daily History
        notion/properties.py::ROLE_DISPLAY_NAMES   docs/04 section 11

    They are identical today, but they answer to different specs, so merging
    them would invent a coupling neither doc asked for — docs/04 could rename
    a Notion Owner label without docs/06 changing one character of a
    Markdown heading. What they must NOT do is silently fall behind
    `events.ROLES`, which is the one table both derive from.

    That is a live risk rather than a hypothetical: BACKLOG A-1 (Desktop 3 =
    ROLE=OTHER) is an open request to add a fifth role, and it names these
    two tables plus `events.schema.ROLES` as the set to change together. The
    failure is quiet — `_display_role()` falls back to `.get(role, role)`, so
    a missed role renders as the raw `CTO_BACKEND` in Company History rather
    than raising — which is exactly the kind of defect a test has to catch
    because no run ever will.

    `ROLE_ORDER` in daily/role_summary.py already has this guard
    (test_daily_role_summary.py); these two did not.
    """

    def test_every_role_has_a_daily_history_display_name(self):
        import daily.markdown as markdown
        from events import ROLES

        self.assertEqual(set(markdown._ROLE_DISPLAY_NAMES), set(ROLES))

    def test_every_role_has_a_notion_display_name(self):
        import notion.properties as properties
        from events import ROLES

        self.assertEqual(set(properties.ROLE_DISPLAY_NAMES), set(ROLES))

    def test_no_display_name_is_empty(self):
        """An empty label is worse than the raw role: it renders as a blank
        Owner rather than something an operator can search for."""
        import daily.markdown as markdown
        import notion.properties as properties

        for table_name, table in (
            ("daily.markdown", markdown._ROLE_DISPLAY_NAMES),
            ("notion.properties", properties.ROLE_DISPLAY_NAMES),
        ):
            for role, display in table.items():
                with self.subTest(table=table_name, role=role):
                    self.assertTrue(display and display.strip())


if __name__ == "__main__":
    unittest.main()
