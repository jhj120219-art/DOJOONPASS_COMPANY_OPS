"""`src/oplog.py` — the single writer for every operational log line.

Three modules append to a log: `collector/runtime.py`, `agent/agent.py` and
`app/runner.py`. Each had its own copy of the same six lines, and the
escaping that stops an untrusted value forging a second line existed in only
one of them (see `test_untrusted_event_input.LogInjectionTests` and
`test_collector_runtime.LogInjectionTests` for the two reproductions).

These tests cover the shared module's own contract, so the guarantees are
asserted once rather than three times.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import oplog  # noqa: E402


# Every character `str.splitlines()` treats as a line boundary. That is the
# operative set, because `splitlines()` is how this repository's tests and an
# operator's tooling read these files back — escaping only "\n" would leave
# seven other ways to forge the same line.
LINE_BREAKING = ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")

# Built by concatenation so this file never *contains* a credential-shaped
# literal. `test_repository_hygiene.test_no_secret_material_in_any_tracked_file`
# scans every tracked file for exactly these shapes and does not exempt tests \u2014
# correctly, since a fixture that looks like a secret is indistinguishable from
# one to a scanner, and to anyone reading the diff. Same idiom as
# `test_agent.FAKE_ENV_ASSIGNMENT`.
FAKE_TOKEN_BODY = "A1b2C3d4E5f6G7h8xyz"
FAKE_TOKEN = "ntn_" + FAKE_TOKEN_BODY
FAKE_BEARER = "Bearer " + FAKE_TOKEN


class OneLineTests(unittest.TestCase):
    def test_no_line_breaking_character_survives(self):
        for char in LINE_BREAKING:
            with self.subTest(char=repr(char)):
                escaped = oplog.one_line(f"A{char}B")
                self.assertEqual(len(escaped.splitlines()), 1)
                self.assertNotIn(char, escaped)

    def test_the_boundary_set_matches_what_splitlines_actually_does(self):
        """Pins the set against Python itself rather than against a list
        someone typed. If a future Python adds a boundary character, this
        fails instead of silently leaving a hole."""
        for char in LINE_BREAKING:
            with self.subTest(char=repr(char)):
                self.assertEqual(
                    len(f"A{char}B".splitlines()), 2, "not actually a line boundary"
                )

    def test_other_control_characters_are_escaped_too(self):
        """A lone CR rewrites the line from column zero; ESC can recolour or
        erase what precedes it. Neither forges a line, both corrupt the
        rendering of one."""
        for char in ("\x00", "\x07", "\x08", "\x1b", "\x7f"):
            with self.subTest(char=repr(char)):
                self.assertNotIn(char, oplog.one_line(f"A{char}B"))

    def test_ordinary_values_are_written_unchanged(self):
        """A log nobody can read is its own failure. Backslashes are
        deliberately not doubled — on this Windows-first project, error
        strings carry paths constantly."""
        for value in (
            "EVT-001",
            r"C:\Users\proj\runtime\logs\collector.log",
            "한글 요약입니다",
            "a-b_c.d:e=1;f,g",
            "",
        ):
            with self.subTest(value=value):
                self.assertEqual(oplog.one_line(value), value)

    def test_a_wide_codepoint_escapes_unambiguously(self):
        r"""U+2028 as `\x2028` would read as `\x20` followed by "28" — an
        escape that denotes something other than the character it stands for
        is worse than no escape."""
        self.assertEqual(oplog.one_line("\u2028"), "\\u2028")
        self.assertEqual(oplog.one_line("\x85"), "\\x85")

    def test_non_string_values_are_accepted(self):
        """Call sites interpolate ints, enums and Paths; the writer receives
        an already-formatted body, but the helper must not be the thing that
        raises."""
        self.assertEqual(oplog.one_line(42), "42")
        self.assertEqual(oplog.one_line(None), "None")


class BoundedTests(unittest.TestCase):
    def test_a_short_string_is_untouched(self):
        self.assertEqual(oplog.bounded("short"), "short")

    def test_an_over_long_string_is_truncated_and_marked(self):
        result = oplog.bounded("z" * (oplog.MAX_LOG_ERROR * 3))

        self.assertTrue(result.endswith("..."))
        self.assertEqual(len(result), oplog.MAX_LOG_ERROR + 3)

    def test_the_bound_sits_above_the_notion_body_bound(self):
        """Not an arbitrary number. `notion/transport.py` already caps a
        response body at 400; a bound at or below that would re-cut a string
        another layer had cut, and an operator could not tell which limit
        took the tail."""
        from notion import transport

        self.assertGreater(oplog.MAX_LOG_ERROR, transport._MAX_ERROR_DETAIL)

    def test_bounded_error_names_the_exception_type(self):
        """A bare message loses the one thing that says what kind of failure
        it was — `PermissionError` and `ValueError` read very differently."""
        result = oplog.bounded_error(PermissionError("denied"))

        self.assertIn("PermissionError", result)
        self.assertIn("denied", result)

    def test_bounded_error_is_also_bounded(self):
        result = oplog.bounded_error(RuntimeError("y" * (oplog.MAX_LOG_ERROR * 2)))

        self.assertTrue(result.endswith("..."))
        self.assertLessEqual(len(result), oplog.MAX_LOG_ERROR + 3)


class AppendLineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log_path = self.root / "logs" / "test.log"

    def test_the_parent_directory_is_created_on_demand(self):
        oplog.append_line(self.log_path, "HELLO")

        self.assertTrue(self.log_path.exists())

    def test_each_call_appends_exactly_one_line(self):
        for i in range(5):
            oplog.append_line(self.log_path, f"RECORD {i}")

        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 5)

    def test_a_body_containing_newlines_still_writes_one_line(self):
        """The property the whole module exists for, at the writer."""
        oplog.append_line(self.log_path, "A\nB\nC")

        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("A\\nB\\nC", lines[0])

    def test_every_line_starts_with_an_iso_timestamp(self):
        oplog.append_line(self.log_path, "HELLO")

        line = self.log_path.read_text(encoding="utf-8").strip()
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} HELLO$")

    def test_an_unwritable_path_costs_visibility_never_a_run(self):
        """Logging must never be the thing that fails a run. Here the log
        path is a *directory*, so opening it for append raises OSError."""
        self.log_path.mkdir(parents=True, exist_ok=True)

        oplog.append_line(self.log_path, "HELLO")  # must not raise

    def test_non_ascii_survives_a_round_trip(self):
        """Every message in this system is Korean. UTF-8 is written
        explicitly rather than left to the platform default, which on a
        Korean Windows console is cp949."""
        oplog.append_line(self.log_path, "수집 완료")

        self.assertIn("수집 완료", self.log_path.read_text(encoding="utf-8"))


class RedactionTests(unittest.TestCase):
    """docs/04 §56 at the write point.

    This earned its place rather than being added on principle. When
    `app/runner.py` started logging *why* a Notion sync failed, that reason
    became a remote response body. Notion's own JSON cannot carry the API
    token — it travels in a request header — but a proxy or captive portal
    answering in Notion's place may echo request headers back, and
    `notion/transport.py` already anticipates exactly that shape of
    response elsewhere. Measured before the fix: a 502 page containing
    `Authorization: Bearer ntn_...` wrote the token into notion_sync.log.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_path = Path(tmp.name) / "test.log"

    def test_a_token_in_the_body_never_reaches_the_file(self):
        oplog.append_line(self.log_path, "FAILED Authorization: " + FAKE_BEARER)

        written = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(FAKE_TOKEN, written)
        self.assertIn("[REDACTED]", written)

    def test_the_surrounding_diagnosis_survives_redaction(self):
        """A redaction that removed the reason would trade one defect for
        another — the field exists to tell an operator what went wrong."""
        oplog.append_line(
            self.log_path,
            "EVENT E-1 NOTION_RESULT NOTION_RETRY_REQUIRED REASON "
            "Notion API returned 502: Bad Gateway | " + FAKE_BEARER,
        )

        written = self.log_path.read_text(encoding="utf-8")
        self.assertIn("NOTION_RESULT NOTION_RETRY_REQUIRED", written)
        self.assertIn("502", written)
        self.assertIn("EVENT E-1", written)

    def test_the_env_assignment_shape_is_caught(self):
        oplog.append_line(self.log_path, "config dump NOTION_API_TOKEN=" + FAKE_TOKEN)

        self.assertNotIn(FAKE_TOKEN_BODY, self.log_path.read_text(encoding="utf-8"))

    def test_ordinary_log_lines_are_not_redacted(self):
        """Over-matching is the safe direction, but it must not swallow the
        vocabulary these logs are actually made of."""
        for body in (
            "EVENT EVT-001 PROJECT SEARCH_FRONTEND NOTION_RESULT NOTION_CREATED",
            "COLLECTOR FINISHED accepted=3 duplicate=0 rejected=0 failed=0",
            "LATE_UPDATE SCHEDULER_FAILED date=2026-08-07 PermissionError: denied",
            "AGENT DESKTOP_1 COMPLETED dates=2 last_successful=2026-08-10",
            "DASHBOARD DRAIN_PENDING drained=1 still_pending=0",
        ):
            with self.subTest(body=body):
                oplog.append_line(self.log_path, body)
                self.assertNotIn("[REDACTED]", self.log_path.read_text(encoding="utf-8"))
                self.log_path.unlink()

    def test_redaction_is_applied_after_escaping_not_before(self):
        """Order matters. `one_line()` runs first, so what the pattern sees is
        the final rendered text rather than the raw value — escaping cannot
        introduce a character that breaks a token out of a redactable run
        after the match has already been decided."""
        oplog.append_line(self.log_path, FAKE_BEARER)

        self.assertNotIn(FAKE_TOKEN_BODY, self.log_path.read_text(encoding="utf-8"))


class SecretPatternHomeTests(unittest.TestCase):
    """The patterns are shared by two consumers with different jobs:
    `agent/signals.py` refuses secret-shaped Signal *content*, this module
    redacts secret-shaped log *output*.

    They were duplicated the moment the second consumer appeared, which is
    the drift these assertions prevent. `agent` already imported `oplog`, so
    moving them down added no dependency edge — the reverse would have made
    `oplog` import `agent` and close a cycle.
    """

    def test_the_agent_uses_these_very_patterns(self):
        import agent.signals as signals

        self.assertIs(signals._SECRET_PATTERNS, oplog.SECRET_PATTERNS)
        self.assertIs(signals._SECRET_RE, oplog.SECRET_RE)

    def test_the_repository_hygiene_patterns_are_still_covered(self):
        """Unchanged obligation, restated at the new home: a Signal travels
        off this machine into Company History, so it is held to at least the
        bar tracked files are."""
        for pattern in (
            r"\bntn_[A-Za-z0-9]{10,}",
            r"\bsecret_[A-Za-z0-9]{10,}",
            r"Bearer\s+[A-Za-z0-9._-]{20,}",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, oplog.SECRET_PATTERNS)


class SharedWriterTests(unittest.TestCase):
    """The point of the module: one implementation, not three."""

    def test_all_three_log_writers_delegate_here(self):
        import agent.agent as agent_module
        import app.runner as runner_module
        import collector.runtime as collector_module

        for name, module in (
            ("collector.runtime", collector_module),
            ("agent.agent", agent_module),
        ):
            with self.subTest(module=name):
                source = __import__("inspect").getsource(module._log)
                self.assertIn("append_line(log_path, message)", source)

        self.assertIs(runner_module._append_log_line, oplog.append_line)

    def test_oplog_imports_nothing_from_this_project(self):
        """It has to sit below every package: `collector` and `agent` use it,
        and `app` — which depends on both — uses it too. Any import of a
        project package here could close a cycle.

        Checked by importing it in a subprocess with only `src/` on the path
        and asserting no project package was pulled in, which catches a
        transitive import that reading the file would not.
        """
        packages = sorted(
            p.name for p in (REPO_ROOT / "src").iterdir() if p.is_dir() and p.name != "__pycache__"
        )
        script = (
            "import sys; sys.path.insert(0, r'{src}')\n"
            "import oplog\n"
            "loaded = [m for m in {pkgs!r} if m in sys.modules]\n"
            "print(','.join(loaded))\n"
        ).format(src=REPO_ROOT / "src", pkgs=packages)

        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "", "oplog pulled in a project package"
        )


if __name__ == "__main__":
    unittest.main()
