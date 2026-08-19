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
import io
import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup.result import BackupStatus  # noqa: E402
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


class ReservedDeviceNameTests(unittest.TestCase):
    """Windows reserved device names, measured rather than assumed.

    `_UNSAFE_FILENAME_CHARS` is a character whitelist, so an `event_id` of
    `NUL` / `CON` / `COM1` passes it untouched — those are legal characters.
    On Win32 those base names are *devices*: writing to one succeeds and
    discards the bytes, which is this repository's worst failure shape
    (docs/11 §50 "History 손실", reported as success).

    Measured on this machine, directly, both shapes:

        NUL         written -> exists=True, size=0, ABSENT from the listing
        NUL.json    written -> exists=True, size=10, present

    **The extension is what saves the pipeline, not the sanitiser.** Every
    filename this project derives from an untrusted id ends in `.json`
    (`safe_event_filename()`, `safe_candidate_filename()`), and `NUL.json` is
    an ordinary file. Verified end to end through `write_event_json()` /
    `read_event_json()` for five reserved names: real files, real content,
    exact round-trip.

    So there is nothing to fix — and one thing to pin. If a derivation ever
    drops the extension, the device path opens with no other guard in the
    way. This test is that guard.
    """

    RESERVED = ("NUL", "CON", "AUX", "PRN", "COM1", "LPT1", "nul", "Con")

    def test_every_derived_event_filename_keeps_an_extension(self):
        for event_id in self.RESERVED:
            with self.subTest(event_id=event_id):
                self.assertTrue(safe_event_filename(event_id).endswith(".json"))

    def test_every_derived_candidate_filename_keeps_an_extension(self):
        from history.file_repository import safe_candidate_filename

        for event_id in self.RESERVED:
            with self.subTest(event_id=event_id):
                self.assertTrue(
                    safe_candidate_filename(f"HIST-{event_id}").endswith(".json")
                )

    def test_a_reserved_name_round_trips_through_the_real_writer(self):
        """The property the extension buys: a real file with real content."""
        from events import create_event
        from reporter.local_output import read_event_json

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for event_id in ("NUL", "CON", "COM1", "LPT1", "AUX"):
                with self.subTest(event_id=event_id):
                    event = create_event(
                        source="DESKTOP_1",
                        role="CTO_BACKEND",
                        project_id="PRJ",
                        event_type="MILESTONE_COMPLETED",
                        status="IN_PROGRESS",
                        summary="s",
                        history_candidate=True,
                        event_id=event_id,
                    )
                    path = write_event_json(event, directory=directory)

                    self.assertTrue(path.exists())
                    self.assertGreater(path.stat().st_size, 0)
                    self.assertEqual(read_event_json(path).event_id, event_id)
                    self.assertIn(path.name, [p.name for p in directory.iterdir()])


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

    def test_every_name_section_29_writes_down_is_detected(self):
        """§29 gives three examples of what the scan must catch: `.env`,
        `credentials.json`, `token.json`. Two of them were not in the list.

        This used to be part of `test_other_common_secret_filenames_are_not_detected`
        below, which lumped the spec's own examples together with names
        nobody had asked for — so a spec requirement sat inside a test that
        asserted it was fine to miss. Measured before the fix: a
        `credentials.json` placed in `daily/`, which docs/08 §26 puts *in*
        backup scope, was synced and would have been pushed.
        """
        for name in (".env", "credentials.json", "token.json"):
            with self.subTest(name=name):
                target = self.master / "daily" / name
                target.write_text("NOTION_API_TOKEN=ntn_real", encoding="utf-8")
                try:
                    detected = scan_for_secrets(self.master)
                    self.assertIn(str(Path("daily") / name), detected)
                finally:
                    target.unlink()

    def test_other_common_secret_filenames_are_not_detected(self):
        """Names the spec does NOT list. Still a real gap — pinned, not
        closed, because picking which extra names to add is policy rather
        than implementation (see `_SECRET_EXACT_NAMES`' comment)."""
        for name in ("secrets.json", "credentials.yml", "token.txt", "config.yaml"):
            (self.master / name).write_text("NOTION_API_TOKEN=ntn_real", encoding="utf-8")

        detected = scan_for_secrets(self.master)

        for name in ("secrets.json", "credentials.yml", "token.txt", "config.yaml"):
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


class SecretNameCaseTests(unittest.TestCase):
    """NEW, **security**. The gate's name list is right; its comparison is
    case-sensitive and the filesystem it runs on is not.

    NOT FIXED — characterization plus a detector. `_looks_like_secret()`
    compares exactly. Windows treats `ID_RSA` and `id_rsa` as one name, so
    which of the two an operator happens to create decides whether docs/08
    §29's gate protects them, and the file is otherwise identical.

    Measured end to end through the real `backup.run_once()`, a real local
    bare remote, same content, same directory, only the case differing:

        daily/ID_RSA   BACKUP_SUCCESS  push=SUCCESS
                       remote tree: daily/2026-08-05.md, daily/ID_RSA
                       `git show main:daily/ID_RSA` returns the key
        daily/id_rsa   BACKUP_FAILED   "secret files detected: daily\\id_rsa"
                       remote tree: (empty)

    Same root as BUG-55 (a case-sensitive comparison against a
    case-insensitive filesystem), a second location: BUG-55 decides which
    files are *backed up*, this decides which are *blocked*.

    Why it is not fixed here: case-folding the comparison gives the gate a
    new way to return BACKUP_FAILED, which is exactly E-15's documented harm
    — a false positive there stops Company History reaching the remote at
    all. Every candidate fix for that pair is recorded as needing a decision,
    so this reports (`ops_status._secret_names_the_gate_will_not_recognise()`)
    and changes nothing.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.master = Path(tmp.name) / "local_master"
        (self.master / "daily").mkdir(parents=True, exist_ok=True)

    # ---- the defect -----------------------------------------------------

    def test_an_upper_case_variant_of_a_listed_name_is_not_detected(self):
        """If these start passing, the gate was made case-insensitive."""
        for name in ("ID_RSA", "CREDENTIALS.JSON", "Token.json", ".ENV"):
            with self.subTest(name=name):
                target = self.master / "daily" / name
                target.write_text("-----BEGIN OPENSSH PRIVATE KEY-----", encoding="utf-8")
                try:
                    self.assertEqual(scan_for_secrets(self.master), ())
                finally:
                    target.unlink()

    def test_an_upper_case_suffix_is_not_detected_either(self):
        """`_SECRET_SUFFIXES` is matched with `str.endswith`, same problem."""
        for name in ("server.PEM", "client.Key", "bundle.P12"):
            with self.subTest(name=name):
                target = self.master / "daily" / name
                target.write_text("secret", encoding="utf-8")
                try:
                    self.assertEqual(scan_for_secrets(self.master), ())
                finally:
                    target.unlink()

    def test_the_exact_case_is_still_blocked(self):
        """The control. Only the case differs between this and the above."""
        (self.master / "daily" / "id_rsa").write_text("secret", encoding="utf-8")

        self.assertIn(str(Path("daily") / "id_rsa"), scan_for_secrets(self.master))

    # ---- the detector ---------------------------------------------------

    def test_the_status_view_names_what_the_gate_cannot_see(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_secret_case", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for name in ("ID_RSA", "CREDENTIALS.JSON", "server.PEM"):
            (self.master / "daily" / name).write_text("secret", encoding="utf-8")
        (self.master / "daily" / "2026-08-05.md").write_text("history", encoding="utf-8")

        found = module._secret_names_the_gate_will_not_recognise(self.master)

        self.assertEqual(
            set(found),
            {
                str(Path("daily") / "ID_RSA"),
                str(Path("daily") / "CREDENTIALS.JSON"),
                str(Path("daily") / "server.PEM"),
            },
        )

    def test_the_detector_stays_silent_on_what_the_gate_already_catches(self):
        """No duplicate line: the E-21 report already names these, and two
        alerts for one file is how a section stops being read."""
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_secret_case2", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        (self.master / "daily" / "id_rsa").write_text("secret", encoding="utf-8")
        (self.master / "daily" / "2026-08-05.md").write_text("history", encoding="utf-8")

        self.assertEqual(module._secret_names_the_gate_will_not_recognise(self.master), ())

    def test_git_s_own_storage_is_not_walked(self):
        """`.git/` holds no file git will ever list, and on this machine's
        Working Copy it is 93% of the walk (90 of 97 files) — a share that
        only grows with backup history. Same exclusion, same reason, as the
        staging-residue scan directly above it in `ops_status.py`."""
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_secret_case4", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        (self.master / ".git" / "objects").mkdir(parents=True)
        (self.master / ".git" / "objects" / "ID_RSA").write_text("x", encoding="utf-8")

        self.assertEqual(module._secret_names_the_gate_will_not_recognise(self.master), ())

    def test_the_detector_uses_the_gate_s_own_name_list(self):
        """A second opinion about what a secret looks like is how the two
        drift apart — the same rule the import block already states."""
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_secret_case3", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from backup import working_copy

        self.assertIs(module._looks_like_secret, working_copy._looks_like_secret)


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
    """BUG-6 FIXED. `app/runner.py` writes one line per sync as
    `<timestamp> EVENT <event_id> PROJECT <project_id> NOTION_RESULT <status>`.
    A newline inside event_id used to produce a second, attacker-authored
    line indistinguishable from a genuine one — a false statement about what
    the system did, in the file an operator reads to decide whether it is
    healthy. The Event arrives over the OneDrive transport from another
    Desktop and `Event.from_json()` does not constrain `event_id` to one
    line, so the input is genuinely untrusted.

    Now every field is escaped at the single write point
    (`app.runner._one_line()`), so the injected text lands inside the real
    line as `\\n...` and no second line exists. The Event is still accepted
    and still logged — escaping preserves docs/04 §55's "event_id를
    기록한다" while removing the forgery. Rejecting such an event_id
    outright is an Event Schema contract decision and remains out of scope.

    BUG-5 still rides along: on Windows the same event_id is not a legal
    filename, so History Filter (step 5, after the log write at step 4)
    raises OSError and the run aborts. That is unchanged and separate.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_newline_in_event_id_no_longer_forges_a_second_log_line(self):
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
                late_update_log_path=log_path.parent / "daily_late_update.log",
                monthly_state_path=log_path.parent / "monthly_history_state.json",
                run_summary_path=log_path.parent / "last_run.json",
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
        raw = log_path.read_text(encoding="utf-8")
        lines = raw.splitlines()

        # No line the attacker authored. `splitlines()` on purpose: it is how
        # this repository and an operator's tooling read the file back, and it
        # breaks on more than "\n" — so this also pins that the escaping covers
        # \v, \f, \x1c-\x1e, \x85, \u2028, \u2029, not just newline.
        forged = [ln for ln in lines if ln.startswith("2026-01-01T00:00:00+09:00 EVENT FORGED")]
        self.assertEqual(forged, [], f"a forged line survived: {lines}")

        # Exactly one genuine line, and the injected text is inside it.
        self.assertEqual(len(lines), 1, f"expected a single log line, got: {lines}")
        self.assertIn("EVENT X\\n2026-01-01T00:00:00+09:00 EVENT FORGED", lines[0])
        # The line is still a real record of the sync — escaping did not
        # cost docs/04 section 55's required fields.
        self.assertIn("NOTION_RESULT NOTION_CREATED", lines[0])

    def test_the_runner_uses_the_shared_escaping_writer(self):
        """The unit-level properties of the escaping now live with the code,
        in `tests/test_oplog.py`. What belongs *here* is that this Runner
        path is wired to it — the end-to-end test above proves the behaviour,
        and this proves it is not a second, private implementation that could
        drift from the one `collector/runtime.py` and `agent/agent.py` use.
        """
        import app.runner as runner
        import oplog

        self.assertIs(runner._one_line, oplog.one_line)
        self.assertIs(runner._append_log_line, oplog.append_line)


class OneDriveExistenceShortCircuitTests(unittest.TestCase):
    """BUG-47 — the outgoing/ half is FIXED; the sync-folder half is not.

    The two halves share one wrong assumption, "a file already at this path
    must be ours and must be identical", but they differ in the only respect
    that decides whether it is safe to act on: who else touches the
    directory.

        outgoing/     this transport's own staging buffer.  FIXED.
        sync folder   managed by the OneDrive client.       still open.

    Nothing outside this class writes, reads, or even scans outgoing/, so the
    race that justified skipping there did not exist — while the cost was the
    worst facet of all: wrong data delivered to Desktop 4 and reported as
    success. The sync-folder facets below are unchanged and still
    characterized, because narrowing that skip is a decision about a real
    race (Phase 5.15), not a cleanup.

    CHARACTERIZATION for everything still marked open below: asserts today's
    behaviour.

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

    def test_a_stale_outgoing_entry_is_no_longer_delivered(self):
        """BUG-47's worst facet — FIXED.

        `send()` used to stage the Event into outgoing/, then re-read that
        file and copy *it* to the sync folder. A stale entry short-circuited
        the staging write, so its content went to Desktop 4 in the Event's
        place, and `send()` still returned None — the Agent recorded a
        successful delivery and advanced its date past an Event that was
        never actually sent.

        Fixed without touching the sync-folder skip: the race that justified
        skipping is about the directory OneDrive manages, and outgoing/ is
        neither managed nor even read by anything else.
        """
        (self.outgoing / "OD-PROBE.json").write_text(
            '{"stale": "left over from an earlier failure"}', encoding="utf-8"
        )

        self._send()

        delivered = (self.sync / "OD-PROBE.json").read_text(encoding="utf-8")
        self.assertIn("the real payload", delivered)
        self.assertNotIn("stale", delivered)

    def test_a_stale_outgoing_entry_is_also_replaced_not_just_bypassed(self):
        """The staging copy is corrected too, not merely stepped around.

        Nothing scans outgoing/ (see the test below), so residue there was
        previously cleared by nothing, ever — it simply waited for the next
        send of the same event_id to be delivered again.
        """
        (self.outgoing / "OD-PROBE.json").write_text(
            '{"stale": "left over"}', encoding="utf-8"
        )

        self._send()

        staged = (self.outgoing / "OD-PROBE.json").read_text(encoding="utf-8")
        self.assertIn("the real payload", staged)
        self.assertNotIn("stale", staged)

    def test_the_staged_and_delivered_bytes_are_identical(self):
        """Both writes take the same in-memory payload, so the staging copy
        is a true record of what was sent rather than a second rendering."""
        self._send()

        self.assertEqual(
            (self.outgoing / "OD-PROBE.json").read_text(encoding="utf-8"),
            (self.sync / "OD-PROBE.json").read_text(encoding="utf-8"),
        )

    def test_the_sync_folder_is_still_never_overwritten(self):
        """The half deliberately NOT changed. Rewriting a file the OneDrive
        client may be mid-upload is the race the staging buffer exists to
        avoid, so narrowing this skip stays a decision, not a cleanup."""
        (self.sync / "OD-PROBE.json").write_text("pre-existing", encoding="utf-8")

        self._send()

        self.assertEqual(
            (self.sync / "OD-PROBE.json").read_text(encoding="utf-8"), "pre-existing"
        )

    def test_a_resend_after_a_partial_failure_delivers_the_event(self):
        """The scenario the fix is actually for, end to end: an earlier send
        staged something and died before publishing; the retry must deliver
        the real Event."""
        (self.outgoing / "OD-PROBE.json").write_text("{}", encoding="utf-8")

        self._send()  # first retry
        self._send()  # and again — idempotent

        delivered = (self.sync / "OD-PROBE.json").read_text(encoding="utf-8")
        self.assertIn("the real payload", delivered)

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
        sources = sorted(src.rglob("*.py"))
        # Same vacuity trap as the other src-wide scans: every assertion here
        # is an assertNotIn, which an empty read would satisfy trivially.
        self.assertTrue(sources, f"no sources under {src}")
        text = "\n".join(p.read_text(encoding="utf-8") for p in sources)

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
            transport_blocked=0,
            accepted=0,
            duplicate=0,
            rejected=0,
            failed=0,
            scheduler_status="COMPLETED",
            generated_days=0,
            reused_days=0,
            backup_status="BACKUP_SUCCESS",
            deleted_files=0,
            notion_synced=0,
            notion_skipped=0,
            notion_retried=0,
            notion_unreadable=0,
            notion_queued=0,
        )

        self.assertEqual(longest(build_ops_run_properties(run_id="R" * 2000, **common)), 2000)
        self.assertEqual(longest(build_ops_run_properties(run_id="R" * 2001, **common)), 2001)

        # The default the Runner actually uses is nowhere near the limit.
        default_run_id = common["run_at"].isoformat(timespec="seconds")
        self.assertLess(len(default_run_id), 40)


class WorkingCopyStrayFileTests(unittest.TestCase):
    """CHARACTERIZATION (BACKLOG E-21): the Secret Scan looks at a different
    directory from the one git commits.

    Three facts, each defensible alone:

        scan_for_secrets(master_dir)   scans **Local Master**
        _relative_files()              is scope-filtered, so `sync_to_working_copy()`
                                       never sees anything outside daily/ and monthly/
        git_add_all()                  runs `git add -A` in the **Working Copy**,
                                       which stages everything present there

    Together they leave a file that reaches the Working Copy by any route
    other than sync — an operator working in the directory, an editor swap
    file, a tool writing a log — completely ungated. It is invisible to the
    scan (wrong directory) and invisible to sync (out of scope), and
    `git add -A` commits it.

    Measured end to end against a real local remote: a `.env` holding a
    token-shaped string, a `notes/id_rsa`, and a `scratch.log` were placed
    in the Working Copy only. `backup.run_once()` returned BACKUP_SUCCESS
    with `push_result="SUCCESS"`, and all three were in the pushed commit
    alongside `daily/2026-08-05.md`.

    docs/08 has two provisions that would each have caught it and neither is
    in force: §28 asks the Backup Repo to carry a `.gitignore`
    (`.env`, `.env.*`, `*.tmp`, `*.log`, ...) — the Working Copy has none,
    and creating it is operator setup (§30, BACKLOG A-8) — and §29 requires
    checking "알려진 Secret 파일이 **포함**되지 않았는지" before backup, where
    what is *included* is what `git add -A` stages.

    Not fixed, and the reason is the same one that keeps E-15 open: every
    candidate move repoints or reshapes a security gate.

        scan the Working Copy instead of Master   changes which directory the
                                                  gate guards, in both
                                                  directions (E-15's
                                                  false positives disappear,
                                                  new blocks appear)
        `git add daily monthly` instead of -A     narrows what is committed and
                                                  changes the approved command
                                                  set that
                                                  test_spec_conformance.py pins
        write a .gitignore into the Working Copy  creates a file in a repository
                                                  this code did not create

    So this pins the boundary instead. It fails the day any of the three is
    taken, and at that point it should be rewritten as the guarantee.
    """

    TOKEN = "ntn_" + "Z" * 40

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.master = self.root / "local_master"
        (self.master / "daily").mkdir(parents=True)
        (self.master / "daily" / "2026-08-05.md").write_text(
            "# history\n", encoding="utf-8"
        )
        self.wc = self.root / "wc"
        self.wc.mkdir()
        self.bare = self.root / "remote.git"
        self._git("init", "--bare", "-b", "main", str(self.bare), cwd=self.root)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Stray File Test")
        self._git("remote", "add", "origin", str(self.bare))
        (self.wc / ".gitkeep").write_text("", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")
        self._git("push", "-u", "origin", "main")

    def _git(self, *args, cwd=None):
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.wc,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _plant_strays(self):
        (self.wc / ".env").write_text(f"NOTION_API_TOKEN={self.TOKEN}\n", encoding="utf-8")
        (self.wc / "scratch.log").write_text("debug\n", encoding="utf-8")
        (self.wc / "notes").mkdir()
        (self.wc / "notes" / "id_rsa").write_text("KEY\n", encoding="utf-8")

    def _backup(self):
        from backup.runner import run_once as backup_run_once

        return backup_run_once(
            self.master,
            self.wc,
            state_path=self.root / "backup_state.json",
            now=datetime(2026, 8, 6, 11, 0).astimezone(),
        )

    def _committed_files(self):
        return sorted(self._git("ls-tree", "-r", "--name-only", "HEAD").split())

    def test_the_secret_scan_does_not_look_at_the_working_copy(self):
        """The root fact. The gate is pointed at Local Master."""
        self._plant_strays()

        self.assertEqual(scan_for_secrets(self.master), ())
        self.assertNotEqual(scan_for_secrets(self.wc), ())

    def test_the_sync_never_sees_an_out_of_scope_stray(self):
        """The second reason nothing catches it: `_relative_files()` is
        scope-filtered, so these files are not added, modified or deleted as
        far as sync is concerned."""
        from backup.working_copy import sync_to_working_copy

        self._plant_strays()

        result = sync_to_working_copy(self.master, self.wc)

        self.assertEqual(result.deleted, ())
        for name in (".env", "scratch.log", "notes/id_rsa"):
            with self.subTest(name=name):
                self.assertNotIn(name, result.added + result.modified)

    def test_a_stray_env_is_committed_and_pushed(self):
        self._plant_strays()

        entry = self._backup()

        self.assertEqual(entry.final_status, BackupStatus.SUCCESS)
        committed = self._committed_files()
        self.assertIn(".env", committed)
        self.assertIn("notes/id_rsa", committed)
        self.assertIn("scratch.log", committed)

    def test_the_backup_reports_success_while_doing_it(self):
        """The part that makes it dangerous rather than merely wrong: every
        signal says the backup was clean."""
        self._plant_strays()

        entry = self._backup()

        self.assertEqual(entry.final_status, BackupStatus.SUCCESS)
        self.assertEqual(entry.push_result, "SUCCESS")

    def test_the_same_file_in_master_is_caught(self):
        """The gate does work — on the directory it watches. This is the
        contrast that makes the finding a mis-pointed gate rather than an
        absent one."""
        (self.master / ".env").write_text(
            f"NOTION_API_TOKEN={self.TOKEN}\n", encoding="utf-8"
        )

        entry = self._backup()

        self.assertEqual(entry.final_status, BackupStatus.FAILED)
        self.assertIn("secret files detected", entry.push_result)

    def test_the_working_copy_carries_no_gitignore(self):
        """docs/08 §28's second line of defence, absent. Creating it is
        operator setup (§30 / BACKLOG A-8), which is why this is pinned
        rather than closed."""
        self.assertFalse((self.wc / ".gitignore").exists())

    def test_git_add_all_is_still_the_approved_command(self):
        """Narrowing to `git add daily monthly` is one of the candidate
        fixes; it would change the command set
        `test_spec_conformance.py::test_git_ops_runs_only_the_approved_command_set`
        pins. Recorded here so the two move together."""
        import inspect

        from backup import git_ops

        source = inspect.getsource(git_ops.git_add_all)
        self.assertIn('"add"', source)
        self.assertIn('"-A"', source)


class StrayFileSurvivesAFailedPushTests(unittest.TestCase):
    """CHARACTERIZATION (BACKLOG E-21): a failed backup has already committed
    the stray, and the retry delivers it.

    E-21 measured the leak on a clean run. This measures it across the
    failure path, which is worse in one way and better in another.

    Worse: `backup.run_once()` runs add -> commit -> push in one `try`, so a
    push failure raises **after** the commit. The stray is durably in the
    local history, the run is reported as failed, and the next run — which
    reports BACKUP_SUCCESS — pushes it. Failing does not hold the leak back;
    it only defers it.

    Better: the deferral is a window. Between the failed run and the
    successful one the file is still sitting in the Working Copy, so
    `ops_status.py`'s C24 check names it *before* it leaves the machine.
    That makes the C24 detection more than an after-the-fact notice for
    exactly the case an unattended deployment is most likely to hit — an
    offline or unreachable remote.

    Nothing here changes behaviour. `run_once()`'s ordering is docs/08 §12's
    Flow, and the commit-then-push sequence is what makes BACKUP_PENDING
    retryable at all.
    """

    TOKEN = "ntn_" + "Q" * 40

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / "runtime"
        self.master = self.runtime / "local_master"
        (self.master / "daily").mkdir(parents=True)
        (self.master / "daily" / "2026-08-05.md").write_text("# h\n", encoding="utf-8")
        (self.runtime / "state").mkdir(parents=True)
        self.wc = self.runtime / "backup_working_copy"
        self.wc.mkdir()
        self.bare = self.root / "remote.git"
        self._git("init", "--bare", "-b", "main", str(self.bare), cwd=self.root)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Retry Leak Test")
        self._git("remote", "add", "origin", str(self.bare))
        (self.wc / ".gitkeep").write_text("", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")
        self._git("push", "-u", "origin", "main")

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.wc,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _backup(self, day):
        from backup.runner import run_once as backup_run_once

        return backup_run_once(
            self.master,
            self.wc,
            state_path=self.root / "backup_state.json",
            now=datetime(2026, 8, day, 11, 0).astimezone(),
        )

    def _local(self):
        return sorted(self._git("ls-tree", "-r", "--name-only", "HEAD").stdout.split())

    def _remote(self):
        return sorted(
            self._git("ls-tree", "-r", "--name-only", "origin/main").stdout.split()
        )

    def _break_remote(self):
        self._git("remote", "set-url", "origin", str(self.root / "gone.git"))

    def _restore_remote(self):
        self._git("remote", "set-url", "origin", str(self.bare))

    def _plant(self):
        (self.wc / ".env").write_text(f"TOKEN={self.TOKEN}\n", encoding="utf-8")

    def test_a_push_failure_still_leaves_the_stray_committed_locally(self):
        from backup.git_ops import GitOperationError

        self._plant()
        self._break_remote()

        with self.assertRaises(GitOperationError):
            self._backup(6)

        self.assertIn(".env", self._local())
        self.assertNotIn(".env", self._remote())

    def test_the_next_successful_run_pushes_it(self):
        from backup.git_ops import GitOperationError

        self._plant()
        self._break_remote()
        with self.assertRaises(GitOperationError):
            self._backup(6)
        self._restore_remote()

        entry = self._backup(7)

        self.assertEqual(entry.final_status, BackupStatus.SUCCESS)
        self.assertIn(".env", self._remote())

    def test_the_status_view_names_it_before_the_push_happens(self):
        """The window. Between the failed run and the successful one the
        file is still in the Working Copy, so the C24 check can name it
        while it is still only local."""
        import contextlib
        import importlib.util

        from backup.git_ops import GitOperationError

        self._plant()
        self._break_remote()
        with self.assertRaises(GitOperationError):
            self._backup(6)

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_retryleak", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.runtime

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            attention = module._print_history(datetime(2026, 8, 6, 12, 0).astimezone())

        warned = [item for item in attention if "Working Copy" in item]
        self.assertTrue(warned, f"no warning before the push: {attention}")
        self.assertIn(".env", warned[0])
        # And it really has not left yet.
        self.assertNotIn(".env", self._remote())

    def test_removing_the_file_before_the_retry_still_leaves_the_local_commit(self):
        """What an operator acting on that warning gets, stated honestly:
        deleting the file stops the *content* going out only if the commit
        is also undone — the stray is already in local history, and
        `sync_to_working_copy()` never deletes anything out of scope."""
        from backup.git_ops import GitOperationError

        self._plant()
        self._break_remote()
        with self.assertRaises(GitOperationError):
            self._backup(6)

        (self.wc / ".env").unlink()
        self._restore_remote()
        self._backup(7)

        # Gone from the tip, but still reachable in history.
        self.assertNotIn(".env", self._remote())
        older = self._git("log", "--all", "--name-only", "--format=").stdout.split()
        self.assertIn(".env", older)


class WorkingCopyJunctionExposureTests(unittest.TestCase):
    """A junction inside the Working Copy is another route into the commit,
    and the C24 detection reaches through it.

    `git add -A` follows a Windows directory junction and stages what is on
    the other side, so a junction placed in the Working Copy publishes an
    external directory to the backup remote. Measured: a junction pointing
    at a folder holding a `.env` produced a remote commit containing
    `linked/.env`, with the backup reporting BACKUP_SUCCESS.

    That is E-21 again rather than a separate defect — anything present in
    the Working Copy is committed — but it is worth pinning separately,
    because the detection's coverage of it is not obvious.
    `scan_for_secrets()` walks with `rglob`, which descends into a junction
    (BACKLOG A-19 measured exactly that, as a hazard), so here that
    behaviour works in the operator's favour: the file is found and named
    as `linked\\.env`.

    Junction creation needs no elevation on Windows (A-19 measured that
    too), which is why this is reachable at all. The test skips where the
    filesystem will not make one.
    """

    TOKEN = "ntn_" + "W" * 40

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.wc = self.root / "wc"
        self.wc.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / ".env").write_text(f"TOKEN={self.TOKEN}\n", encoding="utf-8")

    def _make_junction(self, link: Path, target: Path) -> bool:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        self.addCleanup(
            lambda: subprocess.run(
                ["cmd", "/c", "rmdir", str(link)], capture_output=True
            )
        )
        return True

    def test_the_secret_scan_reaches_through_a_junction(self):
        if sys.platform != "win32":
            self.skipTest("directory junctions are a Windows construct")
        if not self._make_junction(self.wc / "linked", self.outside):
            self.skipTest("this filesystem refused to create a junction")

        found = scan_for_secrets(self.wc)

        self.assertTrue(found, "a junction hid the file from the scan")
        self.assertTrue(
            any(name.endswith(".env") for name in found), f"unexpected: {found}"
        )

    def test_the_same_walk_that_makes_a_19_a_hazard_helps_here(self):
        """`rglob` descending into a junction is the behaviour BACKLOG A-19
        records as the Master-side risk. Pinned from this side so a future
        change to A-19 is known to affect this detection too."""
        if sys.platform != "win32":
            self.skipTest("directory junctions are a Windows construct")
        if not self._make_junction(self.wc / "linked", self.outside):
            self.skipTest("this filesystem refused to create a junction")

        reached = [p for p in self.wc.rglob("*") if p.name == ".env"]

        self.assertEqual(len(reached), 1)


class EventIdForgesDailyMarkdownStructureTests(unittest.TestCase):
    """A newline in `event_id` forges a Daily History line. NOT FIXED.

    BUG-11/BUG-27 record that `summary` and `evidence` reach Daily Markdown
    unescaped, so Markdown structure can be forged. **`event_id` is not named
    in that record**, and it is the worse of the three: `daily/markdown.py`
    writes it into a *structural* field —

        f"- Event ID: {candidate.event_id}"

    — so a newline does not merely add prose, it adds a second `- Event ID:`
    line. Measured, with `event_id = "X\\n- Event ID: FORGED-EVENT"`, the
    rendered day contains a standalone `- Event ID: FORGED-EVENT` line for an
    Event that never existed.

    **A-15's supporting claim is stale, which is how this surfaced.** A-15
    argues for schema rejection partly on the grounds that such an id
    "받아들이지만 나중에 터진다" — that `History Filter`가 `OSError`로 실행
    전체를 중단시킨다 (BUG-5). Re-measured: `safe_candidate_filename()`
    sanitises the newline to `_` and appends a digest
    (`HIST-X_FORGED-LINE-82fa8b62e81e.json`), and `save()` succeeds. Nothing
    explodes later — the CEO-approved sanitisation at the storage boundary
    closed that. So A-15's remaining risk is not "it crashes", it is this:
    it renders.

    Still SKIP. Constraining `event_id` is docs/02's Event Schema contract
    (A-15) and escaping the Markdown renderer is docs/06's rendering contract
    (BUG-11/27). This test decides neither; it puts `event_id` on the record
    beside `summary` and `evidence`, and states the measured shape so the
    decision is made against what happens rather than what was assumed.

    `oplog.one_line()` already closed the identical forgery for *logs*
    (BUG-6, C10). The renderer has no equivalent.
    """

    FORGERY = "X\n- Event ID: FORGED-EVENT"

    def _candidate(self, event_id):
        from events import create_event
        from history.filter import HistoryFilter

        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="PRJ",
            event_type="MILESTONE_COMPLETED", status="COMPLETED",
            summary="real work", history_candidate=True,
            event_id=event_id, timestamp="2026-08-10T10:00:00+09:00",
        )
        return HistoryFilter().evaluate(event).candidate

    def test_a_newline_event_id_produces_a_second_event_id_line(self):
        from daily.markdown import render_daily_markdown

        markdown = render_daily_markdown(
            target_date=date(2026, 8, 10),
            candidates=[self._candidate(self.FORGERY)],
            generated_at="2026-08-10T18:00:00+09:00",
        )

        forged = [
            line for line in markdown.splitlines()
            if line.strip() == "- Event ID: FORGED-EVENT"
        ]
        self.assertEqual(len(forged), 1, "the forged line is rendered standalone")

    def test_one_candidate_yields_two_event_id_lines(self):
        """The count is the point: a reader (or a parser) sees two Events."""
        from daily.markdown import render_daily_markdown

        markdown = render_daily_markdown(
            target_date=date(2026, 8, 10),
            candidates=[self._candidate(self.FORGERY)],
            generated_at="2026-08-10T18:00:00+09:00",
        )

        lines = [
            line for line in markdown.splitlines()
            if line.strip().startswith("- Event ID:")
        ]
        self.assertEqual(len(lines), 2)

    def test_storage_no_longer_explodes_on_such_an_id(self):
        """A-15's stale premise, re-measured: BUG-5 is closed."""
        import tempfile

        from history.file_repository import FileHistoryRepository, safe_candidate_filename

        name = safe_candidate_filename("HIST-" + self.FORGERY)
        self.assertNotIn("\n", name)

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        keep, review = root / "keep", root / "review"
        keep.mkdir()
        review.mkdir()

        stored = FileHistoryRepository(keep_dir=keep, review_dir=review).save(
            self._candidate(self.FORGERY)
        )

        self.assertTrue(stored)
        self.assertEqual(len(list(keep.glob("*.json"))), 1)

    def test_the_log_writer_already_refuses_the_same_forgery(self):
        """BUG-6/C10 closed this for logs. The contrast is the argument that
        the renderer is the remaining half, not that the problem is new."""
        from oplog import one_line

        rendered = one_line(self.FORGERY)

        self.assertNotIn("\n", rendered)
        self.assertIn("\\n", rendered)

    def test_an_ordinary_event_id_renders_exactly_one_line(self):
        """The guard on the guard: this must not read as "the renderer is
        broken for normal input"."""
        from daily.markdown import render_daily_markdown

        markdown = render_daily_markdown(
            target_date=date(2026, 8, 10),
            candidates=[self._candidate("EVT-ORDINARY")],
            generated_at="2026-08-10T18:00:00+09:00",
        )

        lines = [
            line for line in markdown.splitlines()
            if line.strip().startswith("- Event ID:")
        ]
        self.assertEqual(lines, ["- Event ID: EVT-ORDINARY"])


if __name__ == "__main__":
    unittest.main()
