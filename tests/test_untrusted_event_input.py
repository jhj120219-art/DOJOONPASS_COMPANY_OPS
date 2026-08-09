"""Untrusted Event Input Characterization Tests (Audit Sprint).

An Event file arrives from another Desktop through a shared OneDrive folder.
Every field in it is therefore untrusted input, but `events.validate_event()`
constrains only `source`, `role`, `event_type`, `status`, `timestamp`, and the
types of the remaining fields. `event_id`, `project_id`, `summary`, `milestone`
and `blocker` are accepted as arbitrary strings — and several of them are then
used to build filesystem paths, log lines, and the official Company History
Markdown.

These are CHARACTERIZATION tests: they assert what the code does TODAY and
name the audit finding wherever that differs from what the spec wants. No
production code, Runtime behaviour, or spec is changed by this file.

Audit findings referenced below:
    BUG-2   event_id escapes keep_dir (receiving side)
    BUG-5   an event_id that is not a legal filename aborts the run
    BUG-6   a newline in event_id forges a notion_sync.log line
    BUG-7   the secret scan is filename-based and narrow
    BUG-11  an untrusted summary forges Company History structure
    BUG-15  OneDriveTransport does not sanitise event_id (sending side)
"""

import inspect
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup.working_copy import scan_for_secrets  # noqa: E402
from events import create_event, validate_event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
)
from notion.properties import build_create_properties  # noqa: E402
from reporter.local_output import safe_event_filename, write_event_json  # noqa: E402
from transport.onedrive import OneDriveTransport  # noqa: E402


def _event(**overrides):
    data = dict(
        source="DESKTOP_1",
        role="CTO_BACKEND",
        project_id="PRJ-UNTRUSTED",
        event_type="MILESTONE_COMPLETED",
        status="IN_PROGRESS",
        summary="untrusted input probe",
        milestone="M1",
        history_candidate=True,
        timestamp="2026-08-01T10:00:00+09:00",
    )
    data.update(overrides)
    return create_event(**data)


def _event_with(**overrides):
    """_event() with an overridable summary — used by the OneDrive tests."""
    return _event(**overrides)


class EventIdValidationTests(unittest.TestCase):
    """What does the Event Schema actually accept as an event_id?"""

    def _accepts(self, event_id: str) -> bool:
        data = _event(event_id="PLACEHOLDER").to_dict()
        data["event_id"] = event_id
        return validate_event(data) == []

    def test_schema_accepts_posix_path_traversal(self):
        self.assertTrue(self._accepts("../../../../escaped"))

    def test_schema_accepts_windows_path_traversal(self):
        self.assertTrue(self._accepts(r"..\..\..\..\escaped"))

    def test_schema_accepts_an_absolute_path(self):
        self.assertTrue(self._accepts("C:/Windows/Temp/escaped"))

    def test_schema_accepts_embedded_newlines(self):
        self.assertTrue(self._accepts("ok\nforged log line"))

    def test_schema_accepts_whitespace_only(self):
        self.assertTrue(self._accepts("   "))

    def test_schema_accepts_an_unbounded_length(self):
        self.assertTrue(self._accepts("L" * 5000))

    def test_schema_still_rejects_a_missing_event_id(self):
        """The one constraint that does exist: presence."""
        data = _event(event_id="PLACEHOLDER").to_dict()
        data["event_id"] = None
        self.assertIn("missing required field: event_id", validate_event(data))


class HistoryRepositoryPathEscapeTests(unittest.TestCase):
    """BUG-2 / BUG-5 FIXED (CEO-approved B안).

    FileHistoryRepository.save() used to build its destination as
    `keep_dir / f"{history_id}.json"` with history_id = `f"HIST-{event_id}"`,
    i.e. untrusted input straight into a filesystem path. Measured before the
    fix: `../../../PWNED3` wrote into `runtime/history_candidates/` and
    `../../../../PWNED4` into `runtime/` — arbitrary file creation outside the
    candidate directory. A Windows-illegal character aborted the whole Runner.

    `safe_candidate_filename()` now maps every character outside
    [A-Za-z0-9_.-] to '_' and strips leading/trailing '.'/'_', so no separator
    and no '..' can survive. docs/02's Event Schema is deliberately untouched
    (B안) — the guard sits at the storage boundary instead.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.keep_dir = self.root / "runtime" / "history_candidates" / "keep"
        self.review_dir = self.root / "runtime" / "history_candidates" / "review"
        self.repo = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)

    def _save(self, event_id: str) -> None:
        self.repo.save(
            HistoryCandidate(
                history_id=f"HIST-{event_id}",
                event_id=event_id,
                timestamp="2026-08-01T10:00:00+09:00",
                category="MILESTONE",
                project_id="PRJ-UNTRUSTED",
                role="COO",
                summary="probe",
                evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
        )

    def _files_outside_keep(self):
        return [
            p
            for p in self.root.rglob("*.json")
            if p.is_file() and p.parent != self.keep_dir
        ]

    def test_traversal_never_escapes_the_keep_directory(self):
        for event_id in ("../../PWNED2", "../../../PWNED3", "../../../../PWNED4"):
            with self.subTest(event_id=event_id):
                self._save(event_id)
        self.assertEqual(self._files_outside_keep(), [])
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 3)

    def test_windows_style_traversal_never_escapes_either(self):
        self._save(r"..\..\..\PWNED_WIN")
        self.assertEqual(self._files_outside_keep(), [])

    def test_an_absolute_path_event_id_stays_inside_keep(self):
        self._save("C:/Windows/Temp/PWNED_ABS")
        self.assertEqual(self._files_outside_keep(), [])

    def test_a_windows_illegal_character_no_longer_aborts_the_save(self):
        """BUG-5: previously raised OSError and killed the run."""
        self._save("bad\nname:with*illegal?chars")
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 1)

    def test_an_existing_file_outside_keep_is_never_touched(self):
        victim = self.root / "runtime" / "state" / "collector_state.json"
        victim.parent.mkdir(parents=True, exist_ok=True)
        original = '{"last_run": null, "processed_event_ids": ["REAL"]}'
        victim.write_text(original, encoding="utf-8")

        self._save("../../state/collector_state")

        self.assertEqual(victim.read_text(encoding="utf-8"), original)
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 1)

    def test_a_saved_candidate_is_retrievable_by_its_history_id(self):
        """save() and get() must derive the same name after sanitisation."""
        self._save("../../../ROUNDTRIP")
        candidate = self.repo.get("HIST-../../../ROUNDTRIP")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.event_id, "../../../ROUNDTRIP")

    def test_ordinary_history_ids_are_unchanged_by_the_guard(self):
        """The fix must not rename existing, well-formed candidates."""
        from history.file_repository import safe_candidate_filename

        self.assertEqual(
            safe_candidate_filename("HIST-RUNNER-PROD-E2E-001"),
            "HIST-RUNNER-PROD-E2E-001.json",
        )
        self.assertEqual(
            safe_candidate_filename("HIST-3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
            "HIST-3f2504e0-4f89-11d3-9a0c-0305e82c3301.json",
        )

    def test_distinct_ids_never_collapse_to_the_same_filename(self):
        """Sanitising is many-to-one, so a naive implementation maps
        `"HIST-   "` and `"HIST-  "` onto the same file — the second save()
        would then abort the run with FileExistsError. A digest of the
        original id keeps them apart."""
        from history.file_repository import safe_candidate_filename

        ids = [
            "HIST-   ",
            "HIST-  ",
            "HIST-../../../PWNED",
            r"HIST-..\..\PWNED",
            "HIST-bad:name*x",
            "HIST-bad?name|x",
        ]
        names = [safe_candidate_filename(i) for i in ids]
        self.assertEqual(len(set(names)), len(ids), names)

    def test_two_distinct_events_sharing_an_empty_event_id_collide(self):
        """New finding this Sprint, and NOT the same shape as the test above:
        that test's ids are all distinct STRINGS, so the digest (hash of the
        original id) tells them apart even after garbling to the same
        sanitized form. Here the input itself -- not just its sanitized
        form -- is IDENTICAL for two genuinely different events, because
        docs/02's schema accepts event_id="" (rejects only a missing/None
        id, see EventIdValidationTests.test_schema_still_rejects_a_missing_event_id).
        No digest can disambiguate two calls with the same input: sha256("")
        is the same hash both times. `history_id = f"HIST-{event_id}"` makes
        this "HIST-" for every such event, and `sanitized == "HIST-" ==
        original` takes the unchanged-is-safe branch, same collision either
        way. This is a schema question (should event_id="" be rejected like
        None already is?), not a sanitizer bug -- the sanitizer is doing
        exactly what it is documented to do (never assumes DIFFERENT inputs
        collide; two genuinely IDENTICAL inputs are indistinguishable to it
        by design, matching how the rest of this codebase treats a repeated
        event_id as the same event).
        """
        from history.file_repository import safe_candidate_filename

        history_id = f"HIST-{''}"
        self.assertEqual(safe_candidate_filename(history_id), "HIST-.json")

        second_history_id = f"HIST-{''}"
        self.assertEqual(
            safe_candidate_filename(history_id), safe_candidate_filename(second_history_id)
        )

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        keep_dir = Path(tmp.name) / "keep"
        repo = FileHistoryRepository(keep_dir=keep_dir, review_dir=Path(tmp.name) / "review")

        first = HistoryCandidate(
            history_id=history_id, event_id="", timestamp="2026-08-01T10:00:00+09:00",
            category="MILESTONE", project_id="PRJ-A", role="COO",
            summary="first genuinely different event with an empty event_id",
            evidence=(), filter_result=HistoryDecision.KEEP,
        )
        second = HistoryCandidate(
            history_id=second_history_id, event_id="", timestamp="2026-08-01T11:00:00+09:00",
            category="MILESTONE", project_id="PRJ-B", role="COO",
            summary="second, unrelated event that also happens to have an empty event_id",
            evidence=(), filter_result=HistoryDecision.KEEP,
        )
        self.assertTrue(repo.save(first))
        with self.assertRaises(FileExistsError):
            repo.save(second)  # the second, genuinely different event is lost here

    def test_an_over_long_event_id_does_not_abort_the_save(self):
        """The schema accepts an unbounded event_id, and a ~250-character one
        produced a path Windows rejects (WinError 123), aborting the whole
        Runner at the History Filter step. Length is now bounded like content.
        """
        for length in (200, 300, 1000, 5000):
            with self.subTest(length=length):
                self._save("A" * length)

        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 4)
        self.assertEqual(self._files_outside_keep(), [])

    def test_over_long_ids_stay_distinct_after_truncation(self):
        """Truncation is many-to-one, so the digest must keep them apart."""
        from history.file_repository import safe_candidate_filename

        a = safe_candidate_filename("HIST-" + "A" * 400)
        b = safe_candidate_filename("HIST-" + "A" * 401)
        self.assertNotEqual(a, b)
        self.assertLess(len(a), 160)
        self.assertLess(len(b), 160)

    def test_two_pathological_ids_can_both_be_stored(self):
        """The collision, end to end: both candidates must persist."""
        self._save("   ")
        self._save("  ")
        self.assertEqual(len(list(self.keep_dir.glob("*.json"))), 2)
        self.assertEqual(self._files_outside_keep(), [])


class TransportSanitisationAsymmetryTests(unittest.TestCase):
    """BUG-15 FIXED (CEO-approved B안, sending side).

    This repository already contained a sanitiser —
    `reporter.local_output.safe_event_filename()` — and `write_event_json()`
    used it, but `transport.onedrive.OneDriveTransport.send()` interpolated
    `event_id` into a path directly, so a crafted id wrote outside the folder
    OneDrive watches. All three storage boundaries now sanitise identically:

        reporter/local_output.py     safe_event_filename()   (already did)
        transport/onedrive.py        safe_event_filename()   (this fix)
        history/file_repository.py   safe_candidate_filename()
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_safe_event_filename_neutralises_traversal(self):
        """Every character outside [A-Za-z0-9_.-] becomes '_', then leading and
        trailing '.'/'_' are stripped — so no separator and no '..' survives.

        A name that had to be changed also carries a digest of the original id:
        sanitising is many-to-one, and two Events must never collide on one
        filename."""
        for event_id in ("../../target/ESCAPED", r"..\..\target\ESCAPED"):
            with self.subTest(event_id=event_id):
                name = safe_event_filename(event_id)
                self.assertTrue(name.startswith("target_ESCAPED-"), name)
                self.assertTrue(name.endswith(".json"), name)
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)
                self.assertNotIn("..", name)
        self.assertTrue(safe_event_filename("   ").startswith("event-"))
        # An id that is already safe is returned untouched.
        self.assertEqual(
            safe_event_filename("RUNNER-PROD-E2E-001"), "RUNNER-PROD-E2E-001.json"
        )

    def test_an_over_long_event_id_does_not_abort_the_send(self):
        """BUG-23: the two sending-side sanitisers bounded content but not
        length, so a ~250-character event_id produced a path Windows rejects.
        On the transport side that raised TransportError — the Event could then
        never reach Desktop 4 at all, a silent loss at the point of origin."""
        sync_folder = self.root / "onedrive_long"
        outgoing_dir = self.root / "outgoing_long"
        long_id = "L" * 300

        transport = OneDriveTransport(sync_folder=sync_folder, outgoing_dir=outgoing_dir)
        transport.send(_event(event_id=long_id))

        written = list(sync_folder.glob("*.json"))
        self.assertEqual(len(written), 1)
        self.assertLessEqual(len(written[0].name), 140)

        # Distinct over-long ids sharing a truncated stem stay distinct.
        transport.send(_event(event_id=long_id + "-SECOND"))
        self.assertEqual(len(list(sync_folder.glob("*.json"))), 2)

    def test_an_over_long_event_id_does_not_abort_the_local_write(self):
        out_dir = self.root / "local_long"

        first = write_event_json(_event(event_id="R" * 300), directory=out_dir)
        second = write_event_json(_event(event_id="R" * 300 + "-SECOND"), directory=out_dir)

        self.assertNotEqual(first.name, second.name)
        self.assertEqual(len(list(out_dir.glob("*.json"))), 2)
        self.assertLessEqual(len(first.name), 140)

    def test_local_output_writes_inside_its_directory(self):
        out_dir = self.root / "local_out"
        target_dir = self.root / "target"
        target_dir.mkdir(parents=True, exist_ok=True)

        path = write_event_json(_event(event_id="../target/ESCAPED"), directory=out_dir)

        self.assertEqual(path.parent, out_dir)
        self.assertEqual(list(target_dir.iterdir()), [])

    def test_onedrive_transport_never_writes_outside_the_sync_folder(self):
        sync_folder = self.root / "onedrive_sync"
        outgoing_dir = self.root / "outgoing"
        target_dir = self.root / "target"
        target_dir.mkdir(parents=True, exist_ok=True)

        transport = OneDriveTransport(sync_folder=sync_folder, outgoing_dir=outgoing_dir)
        transport.send(_event(event_id="../target/ESCAPED"))

        self.assertEqual(list(target_dir.iterdir()), [])
        written = list(sync_folder.glob("*.json"))
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].name.startswith("target_ESCAPED-"), written[0].name)
        # Staged in outgoing/ first (Phase 5.15), also inside its own folder.
        staged = [p.name for p in outgoing_dir.glob("*.json")]
        self.assertEqual(staged, [written[0].name])

    def test_onedrive_transport_leaves_ordinary_event_ids_unchanged(self):
        """Existing deliveries must keep their filenames."""
        sync_folder = self.root / "onedrive_sync2"
        transport = OneDriveTransport(
            sync_folder=sync_folder, outgoing_dir=self.root / "outgoing2"
        )
        transport.send(_event(event_id="RUNNER-PROD-E2E-001"))

        self.assertTrue((sync_folder / "RUNNER-PROD-E2E-001.json").exists())

    def test_all_three_storage_boundaries_sanitise(self):
        """The asymmetry this closed: the same rule, applied at every boundary."""
        import inspect

        from history import file_repository
        from reporter import local_output
        from transport import onedrive

        self.assertIn("safe_event_filename", inspect.getsource(local_output.write_event_json))
        self.assertIn("safe_event_filename", inspect.getsource(onedrive.OneDriveTransport.send))
        self.assertIn(
            "safe_candidate_filename",
            inspect.getsource(file_repository.FileHistoryRepository.save),
        )

    def test_the_two_sanitiser_copies_stay_in_step(self):
        """`transport.onedrive` may not import `reporter` (an architecture
        boundary test_transport_onedrive.py enforces, and `reporter` already
        imports `transport`), so the rule is duplicated — the same trade this
        project already makes for ROLE_DISPLAY_NAMES. Both copies must agree.
        """
        from reporter.local_output import safe_event_filename as reporter_rule
        from transport.onedrive import safe_event_filename as transport_rule

        for event_id in (
            "RUNNER-PROD-E2E-001",
            "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
            "../target/ESCAPED",
            r"..\..\ESCAPED",
            "bad:name*x",
            "   ",
            "",
        ):
            with self.subTest(event_id=event_id):
                self.assertEqual(reporter_rule(event_id), transport_rule(event_id))

    def test_transport_still_does_not_depend_on_reporter(self):
        """The boundary the duplication exists to protect."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "transport" / "onedrive.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import reporter", source)
        self.assertNotIn("from reporter", source)


class SecretScanCoverageTests(unittest.TestCase):
    """BUG-7: docs/08 section 29's Secret Scan matches known secret-like
    FILENAMES only. It never inspects file contents, so a token pasted into a
    Daily History Markdown body is copied into the Working Copy, committed,
    and pushed to the backup remote.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.master = Path(tmp.name) / "local_master"
        (self.master / "daily").mkdir(parents=True, exist_ok=True)

    def test_known_secret_filenames_are_detected(self):
        for name in (".env", ".env.local", "id_rsa", "server.pem", "cert.key"):
            (self.master / name).write_text("secret", encoding="utf-8")

        detected = scan_for_secrets(self.master)

        for name in (".env", ".env.local", "id_rsa", "server.pem", "cert.key"):
            self.assertIn(name, detected)

    def test_other_common_secret_filenames_are_not_detected(self):
        for name in ("secrets.json", "credentials.json", "token.txt", "config.yaml"):
            (self.master / name).write_text("NOTION_API_TOKEN=ntn_real", encoding="utf-8")

        detected = scan_for_secrets(self.master)

        for name in ("secrets.json", "credentials.json", "token.txt", "config.yaml"):
            self.assertNotIn(name, detected)

    def test_a_token_inside_a_daily_history_body_is_not_detected(self):
        """The case that actually reaches the remote: the file name is
        legitimate, only the contents are dangerous.

        Broadened after measuring the gate's real scope. Twelve realistic
        secrets planted under a master directory produced three detections,
        all by filename; every one of the six pasted into a Daily History body
        went through. That is section 29's stated design — content is never
        read — but it matters more than it used to: backup/runner.py now FAILS
        the backup on a match, so the gate looks like protection it does not
        provide. Widening it is a policy decision, so this test pins the gap
        rather than closing it, and will fail the day content scanning lands.
        """
        secrets = {
            "2026-08-01.md": "NOTION_API_TOKEN=ntn_" + "A" * 46,
            "2026-08-02.md": "github_pat_" + "B" * 70,
            "2026-08-03.md": "ghp_" + "C" * 36,
            "2026-08-04.md": "-----BEGIN RSA PRIVATE KEY-----",
            "2026-08-05.md": "AKIA" + "D" * 16,
            "2026-08-06.md": "password: hunter2trustno1",
        }
        for name, payload in secrets.items():
            (self.master / "daily" / name).write_text(
                f"# DOJOONPASS Company History\n\n- 메모: {payload}\n", encoding="utf-8"
            )

        self.assertEqual(scan_for_secrets(self.master), ())


class SymlinkSecretExfiltrationTests(unittest.TestCase):
    """This Sprint: a symlink under `daily/`, renamed to something
    innocuous, is invisible to `scan_for_secrets()` (filename-only, see
    that function's docstring) while `sync_to_working_copy()` used to
    follow it and copy the TARGET's content into the Working Copy —
    reproduced end to end with a link named `notes.md` pointing at an
    external `.env`, whose content landed in the git-tracked Working Copy
    verbatim. Fixed: `_relative_files()` now excludes symlinks from
    copying, and `scan_for_secrets()` flags any symlink regardless of its
    own name, so the backup fails loudly instead of silently leaking.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.master = self.root / "local_master"
        (self.master / "daily").mkdir(parents=True)

    def _make_symlink(self, link_path: Path, target: Path) -> bool:
        try:
            link_path.symlink_to(target)
            return True
        except OSError:
            return False

    def test_a_renamed_symlink_to_an_external_secret_is_flagged(self):
        outside = self.root / "outside"
        outside.mkdir()
        secret = outside / ".env"
        secret.write_text("NOTION_API_TOKEN=super-secret\n", encoding="utf-8")

        link = self.master / "daily" / "innocuous_notes.md"
        if not self._make_symlink(link, secret):
            self.skipTest("symlink creation not permitted in this environment")

        detected = scan_for_secrets(self.master)

        self.assertIn(str(Path("daily", "innocuous_notes.md")), detected)

    def test_a_symlink_is_never_copied_into_the_working_copy(self):
        from backup.working_copy import sync_to_working_copy

        outside = self.root / "outside"
        outside.mkdir()
        secret = outside / ".env"
        secret.write_text("NOTION_API_TOKEN=super-secret\n", encoding="utf-8")

        link = self.master / "daily" / "innocuous_notes.md"
        if not self._make_symlink(link, secret):
            self.skipTest("symlink creation not permitted in this environment")

        working_copy = self.root / "backup_working_copy"
        result = sync_to_working_copy(self.master, working_copy)

        self.assertEqual(result.added, ())
        self.assertFalse((working_copy / "daily" / "innocuous_notes.md").exists())


class EvidenceMarkdownInjectionTests(unittest.TestCase):
    """BUG-27 (NEW, NOT FIXED): `evidence` is a SECOND injection vector, and
    the audit had only ever characterized `summary` (BUG-11).

    CHARACTERIZATION: asserts today's behaviour, including the forgery.

    `daily/markdown.py` renders each evidence item as `f"- {event_id}: {item}"`
    with no escaping, exactly like `summary`. Two things make this vector
    distinct rather than a duplicate of BUG-11:

      1. Placement. The Evidence section is emitted immediately BEFORE the
         real Metadata block, so a forged `## Metadata` inside an evidence
         item appears FIRST in the file. Anything reading the earliest match —
         a person skimming, or a parser — sees the forged one.

      2. Cardinality. `evidence` is a list with no cap on item count or item
         length, so one Event can inject arbitrarily many forged lines.
         Measured: 5000 items rendered 5000 lines into an 89KB Daily History.

    docs/02's schema constrains `evidence` only to "a list of strings"
    (`events/schema.py` line 103), so every one of these validates.

    The real Metadata block uses `- Event Count:`, while a forger is free to
    write `- Total Items:` or any other label — so the forged block need not
    even collide with a real field to be misleading.

    Not fixed: escaping Company History content changes the rendered format
    that docs/06 sections 14-17 specify, which is a spec decision.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )

    def _render(self, evidence):
        from daily import generate_daily_history

        self.repo.save(
            HistoryCandidate(
                history_id="HIST-EVINJ",
                event_id="EVINJ",
                timestamp="2026-08-01T10:00:00+09:00",
                category="MILESTONE",
                project_id="PRJ-EVINJ",
                role="COO",
                summary="ordinary summary",
                evidence=tuple(evidence),
                filter_result=HistoryDecision.KEEP,
            )
        )
        return generate_daily_history(
            self.repo, date(2026, 8, 1), output_dir=self.root / "daily"
        ).read_text(encoding="utf-8")

    def test_the_schema_accepts_these_evidence_items(self):
        """Precondition: reachable through validation, not hand-built."""
        data = _event(event_id="EV-SCHEMA").to_dict()
        data["evidence"] = ["\n\n## Metadata\n\n- Event Count: 999\n", "- ok"]
        self.assertEqual(validate_event(data), [])

    def test_ordinary_evidence_produces_exactly_one_metadata_block(self):
        text = self._render(["docs/06 참조", "PR #42"])
        self.assertEqual(text.count("## Metadata"), 1)
        self.assertEqual(text.count("# DOJOONPASS Company History"), 1)

    def test_a_crafted_evidence_item_forges_a_second_metadata_block(self):
        text = self._render(
            ["\n\n## Metadata\n\n- History Date: 1999-01-01\n- Event Count: 999\n"]
        )
        self.assertEqual(text.count("## Metadata"), 2)

    def test_the_forged_metadata_block_precedes_the_real_one(self):
        """Why placement matters: first match wins for any naive reader."""
        text = self._render(["\n\n## Metadata\n\n- Event Count: 999\n"])
        first = text.index("## Metadata")
        real = text.index("- Source: DOJOONPASS Company Ops")
        forged_count = text.index("- Event Count: 999")
        self.assertLess(first, real)
        self.assertLess(forged_count, real)

    def test_a_crafted_evidence_item_forges_a_second_h1_title(self):
        text = self._render(["\n---\n\n# DOJOONPASS Company History — FORGED\n"])
        self.assertEqual(text.count("# DOJOONPASS Company History"), 2)

    def test_evidence_has_no_item_count_or_length_cap(self):
        text = self._render([f"근거 {i}" for i in range(5000)])
        self.assertEqual(text.count("EVINJ:"), 5000)


class MarkdownInjectionTests(unittest.TestCase):
    """BUG-11: `summary` is untrusted and is interpolated into the Daily
    History Markdown verbatim, so a crafted summary can forge extra headings
    and a second Metadata block inside README RULE 2's primary source record.

    See EvidenceMarkdownInjectionTests above for BUG-27, the same weakness in
    the `evidence` field — found later, and distinct because evidence renders
    before the real Metadata block and has no cap on item count.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.keep_dir = self.root / "keep"
        self.review_dir = self.root / "review"
        self.daily_dir = self.root / "daily"
        self.repo = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)

    def _render(self, summary: str) -> str:
        from daily import generate_daily_history

        self.repo.save(
            HistoryCandidate(
                history_id="HIST-MDINJ",
                event_id="MDINJ",
                timestamp="2026-08-01T10:00:00+09:00",
                category="MILESTONE",
                project_id="PRJ-MDINJ",
                role="COO",
                summary=summary,
                evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
        )
        path = generate_daily_history(self.repo, date(2026, 8, 1), output_dir=self.daily_dir)
        return path.read_text(encoding="utf-8")

    def test_a_normal_summary_produces_exactly_one_metadata_block(self):
        text = self._render("Search UI implementation completed.")
        self.assertEqual(text.count("## Metadata"), 1)
        self.assertEqual(text.count("# DOJOONPASS Company History"), 1)

    def test_a_crafted_summary_forges_extra_structure(self):
        evil = (
            "real summary\n\n"
            "## Metadata\n\n"
            "- History Date: 1999-01-01\n"
            "- Event Count: 999\n\n"
            "# DOJOONPASS Company History — FORGED"
        )
        text = self._render(evil)

        # Current behaviour: the forged blocks are written verbatim.
        self.assertGreater(text.count("## Metadata"), 1)
        self.assertGreater(text.count("# DOJOONPASS Company History"), 1)
        self.assertIn("- History Date: 1999-01-01", text)
        self.assertIn("- Event Count: 999", text)


class LogInjectionTests(unittest.TestCase):
    """BUG-6: app/runner.py's `_log_notion_sync()` writes one line per sync as
    `<timestamp> EVENT <event_id> PROJECT <project_id> NOTION_RESULT <status>`.
    A newline inside event_id therefore produces a second, attacker-authored
    line that is indistinguishable from a genuine one.

    BUG-5 rides along: on Windows the same event_id is not a legal filename,
    so History Filter (step 5, after the log write at step 4) raises OSError
    and the whole run aborts — after the forged line is already on disk.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_newline_in_event_id_forges_a_second_log_line(self):
        import subprocess

        from app.runner import run_once
        from notion import ExecutionPlanSync, InMemoryNotionTransport, NotionClient

        working_copy = self.root / "backup_working_copy"
        bare_remote = self.root / "backup_remote.git"
        working_copy.mkdir(parents=True, exist_ok=True)

        def git(args, cwd):
            subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

        git(["init", "--bare", "-b", "main", str(bare_remote)], self.root)
        git(["init", "-b", "main"], working_copy)
        git(["config", "user.email", "t@example.invalid"], working_copy)
        git(["config", "user.name", "T"], working_copy)
        git(["remote", "add", "origin", str(bare_remote)], working_copy)
        (working_copy / ".gitkeep").write_text("", encoding="utf-8")
        git(["add", "-A"], working_copy)
        git(["commit", "-m", "init"], working_copy)
        git(["push", "-u", "origin", "main"], working_copy)

        incoming = self.root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        forged_id = (
            "X\n2026-01-01T00:00:00+09:00 EVENT FORGED PROJECT PRJ "
            "NOTION_RESULT NOTION_CREATED"
        )
        (incoming / "loginj.json").write_text(
            _event(event_id=forged_id).to_json(), encoding="utf-8"
        )

        log_path = self.root / "logs" / "notion_sync.log"
        sync = ExecutionPlanSync(
            client=NotionClient(transport=InMemoryNotionTransport(), database_id="DB-1")
        )

        try:
            run_once(
                local_master_dir=self.root / "local_master",
                backup_working_copy_dir=working_copy,
                history_start_date=date(2026, 8, 1),
                runner_lock_path=self.root / "locks" / "company_ops.lock",
                now=datetime(2026, 8, 2, 12, 0).astimezone(),
                transport_dir=self.root / "transport",
                incoming_dir=incoming,
                processed_dir=self.root / "processed",
                rejected_dir=self.root / "rejected",
                collector_log_path=self.root / "logs" / "collector.log",
                collector_state_path=self.root / "state" / "collector_state.json",
                notion_sync=sync,
                notion_sync_log_path=log_path,
                notion_retry_queue_path=self.root / "state" / "notion_retry_queue.json",
                keep_dir=self.root / "keep",
                review_dir=self.root / "review",
                scheduler_state_path=self.root / "state" / "daily_history_state.json",
                backup_state_path=self.root / "state" / "backup_state.json",
            )
        except OSError:
            # BUG-5: the same event_id is not a legal Windows filename, so
            # History Filter aborts the run. The log write already happened.
            pass

        self.assertTrue(log_path.exists())
        lines = log_path.read_text(encoding="utf-8").splitlines()
        forged = [ln for ln in lines if ln.startswith("2026-01-01T00:00:00+09:00 EVENT FORGED")]
        self.assertEqual(len(forged), 1, f"expected one forged line, got: {lines}")


class OneDriveExistenceShortCircuitTests(unittest.TestCase):
    """BUG-47 (NOT FIXED): `send()` reports success without delivering, and in
    one case delivers the WRONG content.

    CHARACTERIZATION: asserts today's behaviour.

    `transport/onedrive._write_atomic()` short-circuits on

        if final_path.exists(): return final_path

    with the stated reasoning: "Already staged (e.g. a retried send) — the
    same event_id always means the same content, so re-writing is unnecessary,
    not unsafe." That holds only when the existing entry was written by this
    same code. `Path.exists()` is True for anything at that path.

    Measured, all reporting success and delivering nothing:

        sync/<id>.json is a directory        -> not delivered
        sync/<id>.json is 0 bytes            -> not delivered
        sync/<id>.json has other content     -> not delivered, other content kept

    And the outgoing side is worse. `send()` stages into outgoing/, then
    copies `outgoing_path.read_text()` into the sync folder. A stale
    outgoing/<id>.json from an earlier failed attempt also short-circuits the
    staging write, so its content — not the Event's — is what reaches
    Desktop 4:

        outgoing holds {"stale": ...}  ->  Desktop 4 receives {"stale": ...}

    `send()` returns None and raises nothing, so the caller records a
    successful send in every one of these cases.

    Why this is not exotic on the intended deployment: the sync folder is
    managed by the OneDrive client, and OneDrive Files On-Demand represents
    not-yet-downloaded files as placeholders that exist on the filesystem
    without local content. Conflict copies and interrupted transfers leave
    similar residue. The one directory that this code does NOT control is
    exactly the one it trusts to be either absent or byte-identical.

    Nothing scans outgoing/ either — no code anywhere globs it — so a file
    stranded there is only ever cleared by sending the same event_id again.

    Not fixed: comparing content before skipping, or checking is_file() and
    size, changes when a re-send rewrites a file that OneDrive may be reading,
    which is the race the staging buffer exists to avoid (Phase 5.15).
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.outgoing = self.root / "outgoing"
        self.sync = self.root / "sync"
        self.outgoing.mkdir()
        self.sync.mkdir()

    def _event(self, event_id="OD-PROBE"):
        return _event_with(event_id=event_id, summary="the real payload")

    def _send(self, event_id="OD-PROBE"):
        OneDriveTransport(sync_folder=self.sync, outgoing_dir=self.outgoing).send(
            self._event(event_id)
        )

    def test_a_clean_send_delivers_the_event(self):
        """Baseline."""
        self._send()

        delivered = (self.sync / "OD-PROBE.json").read_text(encoding="utf-8")
        self.assertIn("the real payload", delivered)

    def test_a_directory_at_the_destination_is_treated_as_delivered(self):
        (self.sync / "OD-PROBE.json").mkdir()

        self._send()  # reports success

        self.assertTrue((self.sync / "OD-PROBE.json").is_dir())

    def test_an_empty_placeholder_at_the_destination_is_treated_as_delivered(self):
        """The OneDrive Files On-Demand shape."""
        (self.sync / "OD-PROBE.json").write_bytes(b"")

        self._send()

        self.assertEqual((self.sync / "OD-PROBE.json").read_bytes(), b"")

    def test_unrelated_content_at_the_destination_is_left_in_place(self):
        (self.sync / "OD-PROBE.json").write_text("not an event at all", encoding="utf-8")

        self._send()

        self.assertEqual(
            (self.sync / "OD-PROBE.json").read_text(encoding="utf-8"),
            "not an event at all",
        )

    def test_a_stale_outgoing_entry_is_delivered_instead_of_the_event(self):
        """The worst facet: wrong data reaches Desktop 4, reported as success."""
        (self.outgoing / "OD-PROBE.json").write_text(
            '{"stale": "left over from an earlier failure"}', encoding="utf-8"
        )

        self._send()

        delivered = (self.sync / "OD-PROBE.json").read_text(encoding="utf-8")
        self.assertIn("stale", delivered)
        self.assertNotIn("the real payload", delivered)

    def test_send_reports_nothing_in_any_of_these_cases(self):
        """Why none of it is detectable by the caller: no return value, no
        exception, even when nothing was delivered."""
        (self.sync / "OD-PROBE.json").write_text("not an event", encoding="utf-8")

        transport = OneDriveTransport(sync_folder=self.sync, outgoing_dir=self.outgoing)
        result = transport.send(self._event())

        self.assertIsNone(result)
        self.assertEqual(
            (self.sync / "OD-PROBE.json").read_text(encoding="utf-8"), "not an event"
        )

    def test_nothing_ever_scans_the_outgoing_directory(self):
        """So a stranded file is never re-sent by any recovery pass."""
        src = Path(__file__).resolve().parents[1] / "src"
        text = "\n".join(p.read_text(encoding="utf-8") for p in src.rglob("*.py"))

        for scan in ("outgoing_dir.glob", "outgoing_dir.iterdir", "outgoing_dir.rglob"):
            self.assertNotIn(scan, text)


class NotionPayloadBoundaryTests(unittest.TestCase):
    """The exact threshold behind BUG-13, measured rather than described.

    CHARACTERIZATION: asserts today's behaviour. It will fail if truncation or
    a length check is added, which is the point.

    Notion caps a `rich_text` or `title` content string at 2000 characters and
    rejects a longer one with HTTP 400. `notion/properties.py` contains no
    truncation, no length check, and not even the constant 2000 — every field
    is passed through verbatim. Three untrusted values reach a capped
    property:

        milestone     -> Current Milestone (rich_text)
        blocker       -> Blocker           (rich_text)
        project_name  -> Project           (title)

    Measured boundary: 2000 characters produces a valid payload, 2001 does
    not. `summary` is NOT affected — it is never sent to Notion, so the
    longest string in a summary-only payload stays the 25-character timestamp.

    Why it matters beyond one rejected call: ExecutionPlanSync maps every
    NotionAPIError to NOTION_RETRY_REQUIRED, so a payload Notion will NEVER
    accept is queued and retried on every subsequent run, forever, with
    attempt_count incrementing and nothing capping it (BUG-13/BUG-14). An
    over-long milestone is therefore not a transient failure — it is a
    permanent one wearing a retryable failure's clothes.

    Fixing it is a decision (truncate and lose data / reject at the Event
    boundary and change the schema / cap retries), so this only pins the
    threshold.
    """

    LIMIT = 2000

    def _longest_string(self, payload, path=""):
        if isinstance(payload, dict):
            best = (0, None)
            for key, value in payload.items():
                found = self._longest_string(value, f"{path}.{key}")
                if found[0] > best[0]:
                    best = found
            return best
        if isinstance(payload, list):
            best = (0, None)
            for i, value in enumerate(payload):
                found = self._longest_string(value, f"{path}[{i}]")
                if found[0] > best[0]:
                    best = found
            return best
        if isinstance(payload, str):
            return len(payload), path
        return 0, None

    def _properties(self, *, project_name="Search Frontend", **overrides):
        return build_create_properties(_event(**overrides), project_name=project_name)

    def test_two_thousand_characters_is_still_within_the_notion_limit(self):
        for field, extra in (
            ("milestone", {}),
            ("blocker", {"event_type": "BLOCKED", "status": "BLOCKED"}),
        ):
            with self.subTest(field=field):
                props = self._properties(**{field: "X" * self.LIMIT}, **extra)
                self.assertEqual(self._longest_string(props)[0], self.LIMIT)

    def test_one_character_over_the_limit_is_passed_through_unchanged(self):
        """Nothing truncates, so the payload Notion will reject is built."""
        for field, extra, prop in (
            ("milestone", {}, "Current Milestone"),
            ("blocker", {"event_type": "BLOCKED", "status": "BLOCKED"}, "Blocker"),
        ):
            with self.subTest(field=field):
                props = self._properties(**{field: "X" * (self.LIMIT + 1)}, **extra)
                longest, where = self._longest_string(props)
                self.assertEqual(longest, self.LIMIT + 1)
                self.assertIn(prop, where)

    def test_the_project_title_is_uncapped_too(self):
        props = self._properties(project_name="N" * 10_000)
        longest, where = self._longest_string(props)
        self.assertEqual(longest, 10_000)
        self.assertIn("Project", where)

    def test_summary_never_reaches_a_capped_notion_property(self):
        """The one untrusted field that is safe here, and the reason the
        others are worth pinning: the exposure is specific, not general."""
        props = self._properties(summary="S" * 10_000)
        self.assertLess(self._longest_string(props)[0], 100)

    def test_no_notion_payload_module_has_a_length_guard(self):
        """The structural cause, so a refactor cannot lose the finding.

        Extended after measuring: `notion/dashboard.py` builds payloads the
        same way and has no guard either. Its `Run ID` goes into a title
        property and crosses 2000 at exactly the same boundary — but the
        default run_id is a ~25-character timestamp and the value is
        caller-supplied, so unlike `milestone`/`blocker` it is not reachable
        from untrusted Event input. Same defect, wider than first recorded.
        """
        src = Path(__file__).resolve().parents[1] / "src" / "notion"
        for module in ("properties.py", "dashboard.py"):
            with self.subTest(module=module):
                self.assertNotIn("2000", (src / module).read_text(encoding="utf-8"))

    def test_the_dashboard_run_id_crosses_the_same_boundary(self):
        """Reachable only via an explicit run_id, not via Event input."""
        from datetime import datetime

        from notion.dashboard import build_ops_run_properties

        def longest(payload):
            if isinstance(payload, dict):
                return max((longest(v) for v in payload.values()), default=0)
            if isinstance(payload, list):
                return max((longest(v) for v in payload), default=0)
            return len(payload) if isinstance(payload, str) else 0

        common = dict(
            run_at=datetime(2026, 8, 5, 11, 0).astimezone(),
            transport_moved=0,
            accepted=0,
            duplicate=0,
            rejected=0,
            failed=0,
            scheduler_status="COMPLETED",
            generated_days=0,
            backup_status="BACKUP_SUCCESS",
            notion_synced=0,
            notion_retried=0,
        )

        self.assertEqual(longest(build_ops_run_properties(run_id="R" * 2000, **common)), 2000)
        self.assertEqual(longest(build_ops_run_properties(run_id="R" * 2001, **common)), 2001)

        # The default the Runner actually uses is nowhere near the limit.
        default_run_id = common["run_at"].isoformat(timespec="seconds")
        self.assertLess(len(default_run_id), 40)


if __name__ == "__main__":
    unittest.main()
