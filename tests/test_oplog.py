"""`src/oplog.py` — the single writer for every operational log line.

Three modules append to a log: `collector/runtime.py`, `agent/agent.py` and
`app/runner.py`. Each had its own copy of the same six lines, and the
escaping that stops an untrusted value forging a second line existed in only
one of them (see `test_untrusted_event_input.LogInjectionTests` and
`test_collector_runtime.LogInjectionTests` for the two reproductions).

These tests cover the shared module's own contract, so the guarantees are
asserted once rather than three times.
"""

import re
import subprocess
import sys
import tempfile
import time
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


class ALogLineCannotBeReorderedTests(unittest.TestCase):
    """C64. `one_line()` closed forging a **second** line and not forging the
    content of the line it is on.

    Unicode's directional overrides and isolates reorder how a run of text
    renders without ending the line, so bytes that say one thing display as
    another. CVE-2021-42574 named the attack against compilers; here the
    reader is an operator deciding whether the pipeline is healthy, and this
    module's own docstring already states why that matters — "a forged line
    is not cosmetic — it is a false statement about what the system did".

    Measured on HEAD, all nine passed through untouched:

        bytes    REJECTED EVT-1<RLO>DETPECCA<LRO>
        renders  REJECTED EVT-1ACCEPTED

    `event_id` is the field they arrive in, and docs/02 constrains it only to
    "present and non-null".

    The fix was already written: it has been in `stash@{0}` since 2026-08-20
    with the rest of an unapplied Sprint (C50 §7, C64 §1). This is the same
    rule; only the constant is spelled with escapes instead of literals,
    because nine invisible codepoints in a source file is the very thing the
    constant exists to expose.
    """

    #: The nine, by codepoint — never as literals in this file either.
    OVERRIDES = (8234, 8235, 8236, 8237, 8238, 8294, 8295, 8296, 8297)

    #: Marks, not overrides. Deliberately untouched: they nudge neighbouring
    #: runs and cannot reverse one, and escaping every directional hint would
    #: mangle values merely written in a right-to-left script.
    MARKS = (0x200E, 0x200F)

    def test_every_override_is_escaped(self):
        for codepoint in self.OVERRIDES:
            with self.subTest(codepoint=hex(codepoint)):
                rendered = oplog.one_line("EVT" + chr(codepoint) + "1")
                self.assertNotIn(chr(codepoint), rendered)
                self.assertIn(r"\u%04x" % codepoint, rendered)

    def test_the_forged_verdict_becomes_visible(self):
        """The whole point: escaped, not stripped. The real value stays
        recoverable, and what a reader sees is what was recorded."""
        forged = "EVT-1" + chr(0x202E) + "DETPECCA " + chr(0x202D)
        line = "2026-08-22T00:00:00+09:00 REJECTED " + oplog.one_line(forged)

        self.assertNotIn(chr(0x202E), line)
        self.assertNotIn(chr(0x202D), line)
        self.assertTrue(line.endswith(r"EVT-1\u202eDETPECCA \u202d"))
        # One line, still.
        self.assertEqual(len(line.splitlines()), 1)

    def test_a_directional_mark_is_left_alone(self):
        """Precision, in the direction that costs a real user. A guard that
        escaped every directional codepoint would start rewriting Arabic and
        Hebrew text an operator wrote on purpose."""
        for codepoint in self.MARKS:
            with self.subTest(codepoint=hex(codepoint)):
                value = "EVT" + chr(codepoint) + "1"
                self.assertEqual(oplog.one_line(value), value)

    def test_ordinary_text_is_untouched(self):
        for value in ("EVT-001", "한글 요약", r"C:\Users\ops", "a b  c"):
            with self.subTest(value=value):
                self.assertEqual(oplog.one_line(value), value)

    def test_line_breaking_characters_are_still_escaped(self):
        """The property this class extends rather than replaces."""
        self.assertEqual(oplog.one_line("a" + chr(10) + "b"), r"a\nb")
        self.assertNotIn(chr(0x2028), oplog.one_line(chr(0x2028)))

    def test_this_file_carries_no_invisible_override(self):
        """A test about invisible characters that contained one would be
        unreviewable, and so would the module it guards."""
        for path in (Path(__file__), Path(__file__).resolve().parents[1] / "src" / "oplog.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertEqual(
                    [hex(ord(c)) for c in source if ord(c) in self.OVERRIDES], []
                )


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


class PrivateKeyBodiesAreRedactedTests(unittest.TestCase):
    r"""The PEM pattern used to stop at the banner, and it was the only one
    that did.

    Every other pattern in `SECRET_PATTERNS` ends in a character class that
    swallows the secret itself — `\bntn_[A-Za-z0-9]{10,}` takes the token,
    `...KEY\s*[=:]\s*\S+` takes the value. This one matched
    `-----BEGIN [A-Z ]*PRIVATE KEY-----` and stopped, so `redact()` replaced
    the *announcement* of a private key and left the key.

    Measured on the code this replaces:

        redact("-----BEGIN RSA PRIVATE KEY-----\\nMIIEowIB...")
        -> "[REDACTED]\\nMIIEowIB..."

    Of the seven shapes, a private key is the worst one to miss: a token can
    be rotated in a minute from a page the operator already has open, and a
    deploy key that reached a log and a GitHub backup cannot be un-published.
    """

    #: Not a real key. Base64-shaped so it exercises the same characters a
    #: real body has, and short enough to assert on whole.
    BODY = "MIIEowIBAAKCAQEA1234567890abcdefGHIJKLMNOP+/=="

    def _block(self, kind: str = "RSA", *, end: bool = True) -> str:
        block = f"-----BEGIN {kind} PRIVATE KEY-----\n{self.BODY}\n"
        if end:
            block += f"-----END {kind} PRIVATE KEY-----"
        return block

    def test_the_body_between_the_markers_is_gone(self):
        redacted = oplog.redact(self._block())

        self.assertNotIn(self.BODY, redacted)
        self.assertNotIn("PRIVATE KEY", redacted)
        self.assertEqual(redacted, "[REDACTED]")

    def test_every_key_type_this_project_could_meet(self):
        """`[A-Z ]*` in the banner is what makes the type free-form; the
        replacement has to keep matching all of them, END marker included."""
        for kind in ("RSA", "OPENSSH", "EC", "DSA", "ENCRYPTED", ""):
            with self.subTest(kind=kind):
                redacted = oplog.redact(self._block(kind))
                self.assertNotIn(self.BODY, redacted)

    def test_a_block_cut_short_before_its_end_marker_still_loses_its_body(self):
        """`bounded()` caps a logged error at 600 characters, so a key
        arriving inside an exception message is routinely truncated
        mid-body. Matching only the complete block would have turned that
        into a leak with no banner left to notice it by."""
        redacted = oplog.redact(self._block(end=False))

        self.assertNotIn(self.BODY, redacted)

    def test_it_survives_the_escaping_that_runs_before_it(self):
        """`append_line()` is `redact(one_line(body))`, so by the time the
        pattern sees a PEM block its real newlines are the two-character
        sequence `\\n`. A pattern that only spanned real newlines would
        match nothing on the one path that matters."""
        oplog.append_line(self.log_path, "SECRET DUMP " + self._block())

        written = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(self.BODY, written)
        self.assertIn("SECRET DUMP", written)

    def test_the_text_around_the_block_is_untouched(self):
        redacted = oplog.redact("before " + self._block() + " after")

        self.assertTrue(redacted.startswith("before "))
        self.assertTrue(redacted.endswith(" after"))

    def test_prose_about_keys_is_not_swallowed(self):
        """The second branch — banner plus trailing base64-shaped material —
        only ever engages *after* a banner. Text that merely talks about
        private keys must come through unchanged, or every incident note
        about a rotation becomes unreadable."""
        for benign in (
            "rotated the deploy private key on DESKTOP_4",
            "BACKUP_FAILED reason=missing private key file",
            "docs/08 §21 covers the private key credential failure",
        ):
            with self.subTest(text=benign):
                self.assertEqual(oplog.redact(benign), benign)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_path = Path(tmp.name) / "test.log"


class RedactionCostIsLinearTests(unittest.TestCase):
    """`redact()` was quadratic in the length of its input.

    Two of the seven patterns began with an unanchored `[A-Za-z0-9_]*`. For a
    run of N word characters that never reaches `API`/`PASSWORD`/…, the
    engine tries every start position against every prefix length, so the
    work is N²/2 rather than N. Measured on the code this replaces:

        n= 1,000     32 ms
        n= 2,000    137 ms
        n= 4,000    552 ms
        n= 8,000  2,211 ms

    Reachable from outside the machine, three ways. `validate_event()` bounds
    neither `summary` nor `blocker` nor `project_id`, and it echoes the value
    it rejected back into its own error string as `{value!r}`; that string
    reaches `oplog.append_line()`, which redacts every line it writes.
    `controltower/dashboard.to_payload()` redacts every authored string in
    every row. One Event file written on another Desktop is therefore enough
    to spend CPU on every run, on every line, until someone deletes the file.

    Asserting on a ratio rather than on wall-clock milliseconds: an absolute
    bound would be a machine specification, and this test has to give the
    same verdict on a slow CI box. Quadratic growth shows up as a ratio near
    4 when the input doubles; linear shows up as a ratio near 2. The
    threshold sits at 3 so ordinary timing noise cannot decide it.
    """

    #: Word characters and nothing else — the worst case for the bounded
    #: prefix, because every one of them is a candidate start.
    FILLER = "A"

    def _measure(self, n: int) -> float:
        text = self.FILLER * n
        oplog.redact(text)  # warm the pattern cache; not timed
        start = time.perf_counter()
        for _ in range(3):
            oplog.redact(text)
        return (time.perf_counter() - start) / 3

    def test_doubling_the_input_does_not_quadruple_the_work(self):
        small = self._measure(4000)
        large = self._measure(8000)

        # A sub-millisecond `small` would make the ratio meaningless — it
        # would be measuring the clock. Linear at these sizes puts `small`
        # well above that on any machine that can run the suite.
        self.assertGreater(small, 0.0)
        self.assertLess(
            large / small,
            3.0,
            "redact() is growing faster than its input — the unbounded "
            f"prefix quantifier is back ({small * 1000:.1f}ms at 4,000 chars, "
            f"{large * 1000:.1f}ms at 8,000)",
        )

    def test_the_prefix_quantifier_is_bounded_in_every_pattern(self):
        """The property directly, so the reason survives even if a future
        machine is fast enough to hide the timing.

        An unbounded `*` or `+` at the very start of a pattern is the shape
        that costs N² here; a bounded `{0,n}` is not.
        """
        for pattern in oplog.SECRET_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertFalse(
                    re.match(r"^\[[^\]]+\][*+]", pattern),
                    "an unanchored, unbounded leading character-class "
                    "quantifier — this is the quadratic shape",
                )

    def test_a_long_value_is_still_redacted(self):
        """The bound is on the *namespace* in front of the keyword, not on
        the secret. A 40-character cap that stopped catching
        `NOTION_API_TOKEN=` would trade a slow log for a leaked one."""
        long_prefix = "A" * 39
        self.assertEqual(
            oplog.redact(f"{long_prefix}_API_KEY=abcdef"), "[REDACTED]"
        )

    def test_the_bound_is_wider_than_any_name_this_project_uses(self):
        for name in (
            "NOTION_API_TOKEN",
            "NOTION_PARENT_PAGE_ID_CLIENT_SECRET",
            "COMPANY_OPS_BACKUP_ACCESS_TOKEN",
        ):
            with self.subTest(name=name):
                self.assertLessEqual(
                    len(name.rsplit("_", 2)[0]), oplog.MAX_SECRET_NAME_PREFIX
                )
                self.assertEqual(oplog.redact(f"{name}=value"), "[REDACTED]")


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
