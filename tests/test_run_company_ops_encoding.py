"""run_company_ops.py's own stdout/stderr encoding (Audit finding, this Sprint).

Found via direct execution (not source inspection, which is all any prior
test of this file did): on a console whose default codepage cannot encode
this file's own Korean status messages (they use an em dash, "—", outside
e.g. cp949's repertoire), `_build_notion_clients()`'s "Notion 미설정" info
print crashed the whole entrypoint with UnicodeEncodeError -- on exactly the
"Notion isn't configured yet" path docs/11 §18 promises is safe and
non-fatal. Fixed by reconfiguring stdout/stderr to UTF-8 at import time.

Runs `_build_notion_clients()` in a subprocess with PYTHONIOENCODING=ascii
(a portable, OS-independent way to force the same failure mode the real bug
needed a non-UTF-8 Windows console for) — never calls `main()`, so this
creates no runtime/ files.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class EncodingSafetyTests(unittest.TestCase):
    def _run(self, code: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "ascii"
        for key in ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID", "NOTION_OPS_RUNS_DATABASE_ID"):
            env.pop(key, None)
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def test_notion_unconfigured_message_does_not_crash_on_ascii_stdout(self):
        result = self._run(
            "import run_company_ops as m\n"
            "m._build_notion_clients()\n"
        )
        self.assertEqual(
            result.returncode, 0,
            f"crashed with PYTHONIOENCODING=ascii:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("Notion", result.stdout)

    def test_the_module_reconfigures_stdout_and_stderr_to_utf8(self):
        """The structural fix, so a refactor cannot silently drop it."""
        source = (REPO_ROOT / "run_company_ops.py").read_text(encoding="utf-8")
        self.assertIn('sys.stdout.reconfigure(encoding="utf-8")', source)
        self.assertIn('sys.stderr.reconfigure(encoding="utf-8")', source)

    def test_init_notion_also_reconfigures_stdout_and_stderr_to_utf8(self):
        """init_notion.py prints real Notion API error text (health.error /
        exc) to stdout — the same risk class, same fix applied for
        consistency, even though no concrete crash was reproduced for it
        (its own output can't be driven without a real Notion connection)."""
        source = (REPO_ROOT / "init_notion.py").read_text(encoding="utf-8")
        self.assertIn('sys.stdout.reconfigure(encoding="utf-8")', source)
        self.assertIn('sys.stderr.reconfigure(encoding="utf-8")', source)

    def test_review_cli_also_reconfigures_stdout_and_stderr_to_utf8(self):
        """src/review_cli.py: checked directly this Sprint -- every Korean
        string it actually prints IS cp949-safe, and its only em-dash
        characters sit in docstrings/comments that are never printed, so no
        crash is reproducible today. Same fix applied anyway for consistency
        across all three CLI entry points, closing the latent risk that a
        future print_fn() call adds a non-cp949 character."""
        source = (REPO_ROOT / "src" / "review_cli.py").read_text(encoding="utf-8")
        self.assertIn('sys.stdout.reconfigure(encoding="utf-8")', source)
        self.assertIn('sys.stderr.reconfigure(encoding="utf-8")', source)


if __name__ == "__main__":
    unittest.main()
