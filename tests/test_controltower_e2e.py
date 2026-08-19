"""Desktop 1 / 2 / 4 -> Control Tower -> Notion, end to end (C47).

The completion criterion for the Control Tower is not "the code exists" but
"real data reaches Notion and is represented correctly". Nothing between the
Agents and the Notion rows is stubbed here:

    Signal files on three Desktops
        -> agent.run_once()          real Agent, real outbox
        -> OneDriveTransport         real atomic write into a shared folder
        -> transport.run_intake()    real stability window and dedup
        -> collector.run_once()      real duplicate check
        -> app.runner.run_once()     the real pipeline, real git backup
        -> controltower rollup       read back off disk
        -> record_run()/ExecutionPlanSync  the real Notion payload builders

Only the Notion *transport* is the in-memory double, because the alternative
is a live Workspace and a credential (BACKLOG A-8).

What this pins that a unit test cannot:

    every Desktop's work arrives, and arrives **once**
    each Desktop's Events stay attributed to that Desktop
    the OPS_RUNS row's numbers equal the rollup's
    the PROJECTS row's blocker equals the rollup's blocker
    a re-run adds no row and no duplicate
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import run_once as agent_run_once  # noqa: E402
from app.runner import run_once as runner_run_once  # noqa: E402
from controltower import build_company_rollup  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    InMemoryNotionTransport,
    NotionClient,
)
from reporter.profiles import PROFILES  # noqa: E402
from runsummary import read_summary  # noqa: E402
from transport.onedrive import OneDriveTransport  # noqa: E402

KST = timezone(timedelta(hours=9))
DAY = date(2026, 8, 12)
NOW_AGENT = datetime(2026, 8, 13, 9, 0, tzinfo=KST)
NOW_RUNNER = datetime(2026, 8, 14, 11, 0, tzinfo=KST)

SIGNALS = {
    "DESKTOP_1": [
        ("search-index.json", {
            "project_id": "SEARCH_BACKEND", "event_type": "MILESTONE_COMPLETED",
            "status": "IN_PROGRESS", "summary": "색인 재구축 완료",
            "milestone": "Index rebuild", "history_candidate": True,
        }),
        ("vendor-block.json", {
            "project_id": "SEARCH_BACKEND", "event_type": "BLOCKED",
            "status": "BLOCKED", "blocker": "벤더 API 키 발급 대기",
            "summary": "키 대기", "history_candidate": True,
            # An explicit timestamp, because two Signals for one project on
            # one date otherwise share that date's midnight and the second
            # never reaches the Notion row (docs/06 §12 + docs/04 §29-30,
            # BACKLOG E-23). AGENT.md §3 documents exactly this.
            "timestamp": f"{DAY.isoformat()}T14:00:00+09:00",
        }),
    ],
    "DESKTOP_2": [
        ("campaign.json", {
            "project_id": "BRAND_CAMPAIGN", "event_type": "DECISION_APPROVED",
            "status": "IN_PROGRESS", "summary": "8월 캠페인 승인",
            "history_candidate": True,
        }),
    ],
    "DESKTOP_4": [
        ("ops-review.json", {
            "project_id": "COMPANY_OPS", "event_type": "ISSUE_RESOLVED",
            "status": "IN_PROGRESS", "summary": "주간 운영 점검 이슈 해소",
            "history_candidate": True,
        }),
    ],
}


def _prop(properties, name):
    value = properties.get(name) or {}
    kind = next(iter(value), None)
    if kind == "number":
        return value["number"]
    if kind == "select":
        return (value["select"] or {}).get("name")
    if kind in ("title", "rich_text"):
        return "".join(
            item.get("text", {}).get("content", "") for item in (value[kind] or [])
        )
    if kind == "date":
        return (value["date"] or {}).get("start")
    return None


class ThreeDesktopsReachNotionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sync_folder = self.root / "onedrive"
        self.sync_folder.mkdir(parents=True)
        self.runtime = self.root / "DESKTOP_4" / "runtime"
        self.local_master = self.runtime / "local_master"
        self.local_master.mkdir(parents=True)
        self.working_copy = self.runtime / "backup_working_copy"
        self.working_copy.mkdir(parents=True)
        self._init_git()
        self.transport = InMemoryNotionTransport()

    def _git(self, args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _init_git(self):
        bare = self.root / "remote.git"
        self._git(["init", "--bare", "-b", "main", str(bare)], self.root)
        self._git(["init", "-b", "main"], self.working_copy)
        self._git(["config", "user.email", "t@example.invalid"], self.working_copy)
        self._git(["config", "user.name", "Control Tower E2E"], self.working_copy)
        self._git(["remote", "add", "origin", str(bare)], self.working_copy)
        (self.working_copy / ".gitkeep").write_text("", encoding="utf-8")
        self._git(["add", "-A"], self.working_copy)
        self._git(["commit", "-m", "init"], self.working_copy)
        self._git(["push", "-u", "origin", "main"], self.working_copy)

    # ---------------------------------------------------------------- steps
    def _run_the_agents(self, signals=None):
        """One real Agent run per Desktop, into one shared sync folder."""
        results = {}
        for desktop, entries in (signals or SIGNALS).items():
            agent_dir = self.root / desktop / "agent"
            day_dir = agent_dir / "signals" / DAY.isoformat()
            day_dir.mkdir(parents=True, exist_ok=True)
            for name, payload in entries:
                (day_dir / name).write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
            results[desktop] = agent_run_once(
                transport=OneDriveTransport(
                    sync_folder=self.sync_folder, outgoing_dir=agent_dir / "outgoing"
                ),
                agent_start_date=DAY,
                profile=PROFILES[desktop],
                signals_dir=agent_dir / "signals",
                rejected_signals_dir=agent_dir / "signals_rejected",
                outbox_dir=agent_dir / "outbox",
                sent_dir=agent_dir / "sent",
                state_path=agent_dir / "state" / "agent_state.json",
                lock_path=agent_dir / "locks" / "agent.lock",
                log_path=agent_dir / "logs" / "agent.log",
                now=NOW_AGENT,
            )
        self._age_the_sync_folder()
        return results

    def _age_the_sync_folder(self):
        """docs/03's stability window: a file must have stopped changing before
        intake promotes it. A real OneDrive delivery is minutes old by the time
        the scheduled Runner sees it; these are seconds old, so they are aged
        deliberately rather than by sleeping in a test."""
        old = time.time() - 600
        for path in self.sync_folder.glob("*.json"):
            os.utime(path, (old, old))

    def _run_the_runner(self, now=NOW_RUNNER):
        return runner_run_once(
            local_master_dir=self.local_master,
            backup_working_copy_dir=self.working_copy,
            history_start_date=DAY,
            runner_lock_path=self.runtime / "locks" / "company_ops.lock",
            now=now,
            # Desktop 4's transport directory IS the shared folder (AGENT.md §1).
            transport_dir=self.sync_folder,
            incoming_dir=self.runtime / "events" / "incoming",
            processed_dir=self.runtime / "events" / "processed",
            rejected_dir=self.runtime / "events" / "rejected",
            collector_log_path=self.runtime / "logs" / "collector.log",
            collector_state_path=self.runtime / "state" / "collector_state.json",
            notion_sync=ExecutionPlanSync(
                client=NotionClient(transport=self.transport, database_id="PROJECTS")
            ),
            notion_sync_log_path=self.runtime / "logs" / "notion_sync.log",
            late_update_log_path=self.runtime / "logs" / "daily_late_update.log",
            monthly_state_path=self.runtime / "state" / "monthly_history_state.json",
            run_summary_path=self.runtime / "runs" / "last_run.json",
            notion_retry_queue_path=self.runtime / "state" / "notion_retry_queue.json",
            dashboard_client=NotionClient(
                transport=self.transport, database_id="OPSRUNS"
            ),
            dashboard_pending_path=self.runtime / "state" / "dashboard_pending.json",
            keep_dir=self.runtime / "history_candidates" / "keep",
            review_dir=self.runtime / "history_candidates" / "review",
            scheduler_state_path=self.runtime / "state" / "daily_history_state.json",
            backup_state_path=self.runtime / "state" / "backup_state.json",
        )

    def _rollup(self, now=NOW_RUNNER):
        return build_company_rollup(
            processed_dir=self.runtime / "events" / "processed", now=now
        )

    def _rows(self, key):
        return [
            page for page in self.transport._pages.values()
            if key in page.get("properties", {})
        ]

    # ---------------------------------------------------------------- tests
    def test_every_desktops_work_arrives_attributed_to_that_desktop(self):
        self._run_the_agents()
        self._run_the_runner()

        rollup = self._rollup()

        self.assertEqual(rollup.events_read, 4)
        self.assertEqual(rollup.mismatches, ())
        by_source = {d.source: d for d in rollup.desktops}
        self.assertEqual(by_source["DESKTOP_1"].event_count, 2)
        self.assertEqual(by_source["DESKTOP_1"].projects, ("SEARCH_BACKEND",))
        self.assertEqual(by_source["DESKTOP_2"].projects, ("BRAND_CAMPAIGN",))
        self.assertEqual(by_source["DESKTOP_4"].projects, ("COMPANY_OPS",))
        # Desktop 3 sent nothing and is present-and-empty rather than absent.
        self.assertEqual(by_source["DESKTOP_3"].event_count, 0)
        self.assertFalse(by_source["DESKTOP_3"].has_activity)

    def test_no_desktops_events_leak_into_another(self):
        """The mixing this layer exists to make impossible to miss: no
        Desktop's project list may contain another Desktop's project."""
        self._run_the_agents()
        self._run_the_runner()

        rollup = self._rollup()
        owner = {
            "SEARCH_BACKEND": "DESKTOP_1",
            "BRAND_CAMPAIGN": "DESKTOP_2",
            "COMPANY_OPS": "DESKTOP_4",
        }
        for desktop in rollup.desktops:
            for project in desktop.projects:
                with self.subTest(desktop=desktop.source, project=project):
                    self.assertEqual(owner[project], desktop.source)

    def test_the_blocker_one_desktop_reported_is_the_companys_risk(self):
        self._run_the_agents()
        self._run_the_runner()

        rollup = self._rollup()

        self.assertEqual(len(rollup.risks), 1)
        risk = rollup.risks[0]
        self.assertEqual(risk.project_id, "SEARCH_BACKEND")
        self.assertEqual(risk.blocker, "벤더 API 키 발급 대기")
        self.assertEqual(risk.team, "CTO_BACKEND")
        # ...and it is traceable to a file that exists.
        self.assertTrue(
            (self.runtime / "events" / "processed" / risk.evidence.path).is_file()
        )

    def test_the_ops_runs_row_agrees_with_the_rollup(self):
        self._run_the_agents()
        self._run_the_runner()

        rollup = self._rollup()
        rows = self._rows("Run ID")
        self.assertEqual(len(rows), 1)
        properties = rows[0]["properties"]

        expected = " ".join(
            f"{d.source}:{d.event_count}" for d in sorted(
                (d for d in rollup.desktops if d.event_count), key=lambda d: d.source
            )
        )
        self.assertEqual(_prop(properties, "Desktops Reporting"), expected)
        self.assertEqual(
            _prop(properties, "Role Mismatches"), len(rollup.mismatches)
        )
        self.assertEqual(_prop(properties, "Accepted"), rollup.events_read)
        self.assertEqual(_prop(properties, "Overall"), "OK")

    def test_the_projects_rows_agree_with_the_rollup(self):
        self._run_the_agents()
        self._run_the_runner()

        rollup = self._rollup()
        rows = self._rows("Project ID")
        self.assertEqual(len(rows), 3)

        for page in rows:
            properties = page["properties"]
            project_id = _prop(properties, "Project ID")
            with self.subTest(project=project_id):
                folded = rollup.project(project_id)
                self.assertIsNotNone(folded)
                self.assertEqual(
                    _prop(properties, "Blocker") or None, folded.open_blocker
                )
                self.assertEqual(_prop(properties, "Status"), folded.status)
                # `Source` and `Owner` must point at the same Desktop —
                # docs/02 §8's pairing, seen from the Notion side.
                self.assertEqual(
                    _prop(properties, "Source"),
                    next(
                        d.source for d in rollup.desktops
                        if project_id in d.projects
                    ),
                )

    def test_a_second_run_adds_no_row_and_no_duplicate(self):
        """Re-running the scheduled pipeline is the ordinary case, not an
        exception: the trigger can fire twice, and catch-up re-runs happen."""
        self._run_the_agents()
        self._run_the_runner()
        before_ops = len(self._rows("Run ID"))
        before_projects = len(self._rows("Project ID"))
        rollup_before = self._rollup()

        # Same run_id (the Runner derives it from `now`), same Events.
        self._run_the_runner()

        self.assertEqual(len(self._rows("Run ID")), before_ops)
        self.assertEqual(len(self._rows("Project ID")), before_projects)
        rollup_after = self._rollup()
        self.assertEqual(rollup_after.events_read, rollup_before.events_read)
        self.assertEqual(
            [p.event_count for p in rollup_after.projects],
            [p.event_count for p in rollup_before.projects],
        )

    def test_re_running_the_agents_delivers_nothing_new(self):
        """`event_id` is derived deterministically from Desktop+date+filename,
        so the same Signal is the same Event however many times it is sent —
        which is what makes the Control Tower's counts stable."""
        self._run_the_agents()
        self._run_the_runner()
        before = self._rollup().events_read

        self._run_the_agents()
        self._run_the_runner()

        self.assertEqual(self._rollup().events_read, before)

    def test_company_history_and_the_control_tower_agree_about_what_is_kept(self):
        """The two are not the same set, and the difference is the History
        Filter's, not a loss: a `BLOCKED` Event is REVIEW (docs/05 §24) and so
        never reaches a Daily file, while the Control Tower reads every
        accepted Event. That is why the company's one open blocker is visible
        in the Control Tower and nowhere in Company History."""
        self._run_the_agents()
        self._run_the_runner()

        daily = (self.local_master / "daily" / f"{DAY.isoformat()}.md").read_text(
            encoding="utf-8"
        )
        rollup = self._rollup()
        blocker_event = rollup.risks[0].evidence.event_id

        self.assertNotIn(f"- Event ID: {blocker_event}", daily)
        for project in ("SEARCH_BACKEND", "BRAND_CAMPAIGN", "COMPANY_OPS"):
            with self.subTest(project=project):
                self.assertIsNotNone(rollup.project(project))
        # The three KEEP Events did reach Company History.
        kept = [
            path.stem
            for path in (self.runtime / "events" / "processed").glob("*.json")
            if path.stem != blocker_event
        ]
        self.assertEqual(len(kept), 3)
        for event_id in kept:
            with self.subTest(event=event_id):
                self.assertIn(f"- Event ID: {event_id}", daily)

    def test_a_desktop_claiming_another_teams_role_is_reported_all_the_way_up(self):
        """The mixing case, driven through the same chain. `PROFILES` prevents
        an *Agent* from producing it, so this writes the Event the way the one
        path that can does: straight into `incoming/`, which docs/11 permits an
        operator to do on Desktop 4."""
        from events import create_event

        self._run_the_agents()
        incoming = self.runtime / "events" / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        event = create_event(
            source="DESKTOP_1", role="CMO", project_id="BRAND_CAMPAIGN",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS",
            summary="hand-written on Desktop 4", milestone="M",
            history_candidate=True, event_id="HANDWRITTEN-1",
            timestamp=f"{DAY.isoformat()}T16:00:00+09:00",
        )
        (incoming / "HANDWRITTEN-1.json").write_text(
            event.to_json(), encoding="utf-8"
        )

        self._run_the_runner()
        rollup = self._rollup()

        self.assertEqual(len(rollup.mismatches), 1)
        mismatch = rollup.mismatches[0]
        self.assertEqual(mismatch.event_id, "HANDWRITTEN-1")
        self.assertEqual(mismatch.source, "DESKTOP_1")
        self.assertEqual(mismatch.claimed_role, "CMO")
        self.assertEqual(mismatch.expected_role, "CTO_BACKEND")

        # It is counted under the Desktop that sent it, not the role it claims.
        by_source = {d.source: d for d in rollup.desktops}
        self.assertIn("BRAND_CAMPAIGN", by_source["DESKTOP_1"].projects)
        self.assertEqual(len(by_source["DESKTOP_1"].mismatched), 1)

        # ...and the Dashboard row carries the count.
        row = self._rows("Run ID")[0]["properties"]
        self.assertEqual(_prop(row, "Role Mismatches"), 1)

    def test_a_run_that_dies_at_backup_still_leaves_the_control_tower_complete(self):
        """The partial-execution case, and the one that shows which view is
        the resilient one.

        `backup.run_once()` re-raises `GitOperationError` and `app.runner`
        does not absorb it (BUG-4 / A-18), so a push failure ends the run at
        step 7 — before step 9b writes the OPS_RUNS row. Notion therefore has
        **no row at all** for that run and nothing queued for it, which is
        A-18's recorded consequence.

        What must still hold is the half this Sprint is about: the Events
        reached `processed/` in step 4, so the Control Tower — which reads
        that directory and nothing else — is complete and correct. README
        RULE 5/9 says Company History keeps recording while Notion is down;
        this is the same property for the business view.
        """
        from backup.git_ops import GitOperationError

        self._run_the_agents()
        # Break the push the way a wrong remote does.
        self._git(
            ["remote", "set-url", "origin", str(self.root / "does-not-exist.git")],
            self.working_copy,
        )

        with self.assertRaises(GitOperationError):
            self._run_the_runner()

        # No Dashboard row, and nothing queued — A-18, measured rather than
        # assumed, so a change to it fails here rather than silently.
        self.assertEqual(self._rows("Run ID"), [])
        self.assertFalse((self.runtime / "state" / "dashboard_pending.json").is_file())

        # ...and the Control Tower is whole.
        rollup = self._rollup()
        self.assertEqual(rollup.events_read, 4)
        self.assertEqual(len(rollup.risks), 1)
        self.assertEqual(
            {d.source for d in rollup.desktops if d.event_count},
            {"DESKTOP_1", "DESKTOP_2", "DESKTOP_4"},
        )
        # The Manifest, which IS written on every exit path, agrees.
        summary = read_summary(self.runtime / "runs" / "last_run.json")
        self.assertEqual(summary.component("backup").status.value, "FAILED")
        self.assertIsNone(summary.component("dashboard"))

    def test_an_empty_run_produces_an_empty_but_honest_control_tower(self):
        """No Signals anywhere. Every Desktop must still appear, and nothing
        may be reported as a risk or a mismatch."""
        self._run_the_runner()

        rollup = self._rollup()

        self.assertEqual(rollup.events_read, 0)
        self.assertEqual(rollup.projects, ())
        self.assertEqual(rollup.risks, ())
        self.assertEqual(rollup.mismatches, ())
        self.assertEqual(len(rollup.desktops), 4)
        self.assertTrue(all(not d.has_activity for d in rollup.desktops))
        row = self._rows("Run ID")[0]["properties"]
        self.assertEqual(_prop(row, "Desktops Reporting"), "")
        self.assertEqual(_prop(row, "Role Mismatches"), 0)
        self.assertEqual(_prop(row, "Accepted"), 0)

    def test_the_run_manifest_and_the_dashboard_agree(self):
        self._run_the_agents()
        self._run_the_runner()

        summary = read_summary(self.runtime / "runs" / "last_run.json")
        row = self._rows("Run ID")[0]["properties"]

        self.assertEqual(_prop(row, "Run ID"), summary.run_id)
        failed = [
            component.name for component in summary.components
            if component.status.value == "FAILED"
        ]
        self.assertEqual(_prop(row, "Failed Steps"), ", ".join(failed))
        self.assertEqual(summary.exit_code, 0)
