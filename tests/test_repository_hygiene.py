"""Repository Hygiene Guard Tests (Audit Sprint).

Two of these guards protect secrets, and they had no automated coverage at
all — the whole defence rested on `.gitignore` staying correct forever and on
nobody ever pasting a token into a tracked file. Both are now enforced.

The rest pin properties the audit verified once by hand: the stdlib-only
dependency rule, and the documentation gaps (audit finding BUG-12) that could
not be fixed here because README and docs/ are specification documents and
this Sprint may not change them.

Guards (must keep passing forever):
    .env / .env.* / runtime/ / *.log are git-ignored
    no secret material in any tracked file
    no third-party import anywhere in src/
    every tracked source file is valid UTF-8

Characterization (records a known gap, audit finding BUG-12 / BUG-16):
    README section 12's document list vs the real docs/ directory
    stale `D:\\DOJOONPASS_COMPANY_OPS` path headers
    notion.dashboard_pending.remove_pending() has no caller

Nothing here changes production code, Runtime behaviour, or any spec.
"""

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
DOCS = REPO_ROOT / "docs"
sys.path.insert(0, str(SRC))


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )


def _tracked_files() -> list[Path]:
    result = _git("ls-files")
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line.strip()]


class SecretExposureGuardTests(unittest.TestCase):
    """docs/04 sections 40-41 and docs/13: secret values never live in this
    repository. `.env` holds a real Notion Integration token on the operator's
    machine, so a `.gitignore` regression is a credential leak."""

    IGNORED_PATHS = (
        ".env",
        ".env.local",
        ".env.production",
        "runtime/state/collector_state.json",
        "runtime/logs/collector.log",
        "runtime/backup_working_copy/daily/2026-08-01.md",
        "anything.log",
    )

    def test_secret_and_runtime_paths_are_git_ignored(self):
        for path in self.IGNORED_PATHS:
            with self.subTest(path=path):
                result = _git("check-ignore", "-q", path)
                self.assertEqual(
                    result.returncode, 0, f"{path} is NOT git-ignored — secrets/runtime may leak"
                )

    def test_gitignore_still_declares_the_protective_patterns(self):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".env", ".env.*", "runtime/", "*.log"):
            self.assertIn(pattern, text)

    def test_no_env_file_is_tracked(self):
        tracked = {p.name for p in _tracked_files()}
        self.assertNotIn(".env", tracked)
        for name in tracked:
            self.assertFalse(
                name.startswith(".env.") and name != ".env.example",
                f"tracked env file: {name}",
            )

    def test_no_secret_material_in_any_tracked_file(self):
        """A real Notion Integration Secret starts with `ntn_` (previously
        `secret_`). Neither may ever appear in a tracked file, nor may an
        Authorization header value."""
        offenders = []
        for path in _tracked_files():
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for pattern in (r"\bntn_[A-Za-z0-9]{10,}", r"\bsecret_[A-Za-z0-9]{10,}",
                            r"Bearer\s+[A-Za-z0-9._-]{20,}"):
                if re.search(pattern, text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern}")
        self.assertEqual(offenders, [])

    def test_env_example_declares_the_variables_without_values(self):
        """docs/13 section 3's setup contract: the template is tracked, the
        values are not."""
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for line in text.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                self.assertEqual(
                    value.strip(), "", f"{key.strip()} has a value in .env.example"
                )


class DependencyGuardTests(unittest.TestCase):
    """README's implementation principle and docs/07's "별도 서버 인프라 금지":
    this project has no third-party dependency, and that is a property worth
    keeping — it is why `python -m pytest` works with no install step at all
    (docs/11 section 101's Release Environment Check)."""

    LOCAL_PACKAGES = {
        "app", "backup", "collector", "daily", "events", "history",
        "notion", "reporter", "scheduler", "transport", "review_cli",
    }

    def test_src_imports_only_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names)
        third_party = {}
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module.split(".")[0]]
                for name in names:
                    if name not in stdlib and name not in self.LOCAL_PACKAGES:
                        third_party.setdefault(name, str(path.relative_to(REPO_ROOT)))
        self.assertEqual(third_party, {})

    def test_no_dependency_manifest_is_needed(self):
        for manifest in ("requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock"):
            self.assertFalse(
                (REPO_ROOT / manifest).exists(),
                f"{manifest} appeared — the stdlib-only property may have been dropped",
            )


class SourceEncodingGuardTests(unittest.TestCase):
    """Every spec and source file is Korean-bearing UTF-8; a mis-encoded file
    breaks both the docs and the Markdown History renderer."""

    def test_every_tracked_text_file_is_valid_utf8(self):
        failures = []
        for path in _tracked_files():
            if path.suffix.lower() not in {".py", ".md", ".json", ".example", ".gitignore"}:
                continue
            if not path.is_file():
                continue
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                failures.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
        self.assertEqual(failures, [])

    def test_no_tracked_file_starts_with_a_utf8_bom(self):
        offenders = []
        for path in _tracked_files():
            if path.suffix.lower() not in {".py", ".md"} or not path.is_file():
                continue
            if path.read_bytes().startswith(b"\xef\xbb\xbf"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])


class DocumentationGapCharacterizationTests(unittest.TestCase):
    """Audit finding BUG-12. README and docs/ are specification documents;
    this Sprint may not edit them, so the gaps are recorded here instead.
    Fixing them should make these tests fail and be rewritten as guards.
    """

    def test_docs_directory_contains_fourteen_specs(self):
        names = sorted(p.name for p in DOCS.glob("*.md"))
        self.assertEqual(len(names), 14)
        self.assertIn("12_APPLICATION_FLOW_SPEC.md", names)
        self.assertIn("13_NOTION_ENVIRONMENT_SETUP.md", names)

    def test_readme_document_list_is_missing_the_last_two_specs(self):
        """README section 12 stops at 11_DEPLOYMENT_RUNBOOK.md."""
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("11_DEPLOYMENT_RUNBOOK.md", readme)
        self.assertNotIn("12_APPLICATION_FLOW_SPEC.md", readme)
        self.assertNotIn("13_NOTION_ENVIRONMENT_SETUP.md", readme)

    def test_spec_headers_still_carry_a_stale_absolute_path(self):
        """The repository lives at C:\\Users\\user\\Desktop\\DOJOONPASS_COMPANY_OPS,
        but 13 files still open with a `D:\\DOJOONPASS_COMPANY_OPS\\...` header."""
        stale = [
            p.name
            for p in [REPO_ROOT / "README.md", *sorted(DOCS.glob("*.md"))]
            if p.read_text(encoding="utf-8").startswith("# D:\\DOJOONPASS_COMPANY_OPS")
        ]
        self.assertEqual(len(stale), 13)

    def test_the_working_copy_docstring_no_longer_contradicts_the_runner(self):
        """FIXED. This asserted the drift; now it asserts the drift is gone.

        scan_for_secrets()'s docstring used to say it "is not called from ...
        runner.py" and that "nothing is excluded from a sync", describing
        Issue #3 as unresolved. backup/runner.py has called it and failed the
        backup on a match since that decision was made, so the docstring was
        inviting someone to delete a live security gate as dead code.
        """
        working_copy = (SRC / "backup" / "working_copy.py").read_text(encoding="utf-8")
        runner = (SRC / "backup" / "runner.py").read_text(encoding="utf-8")

        self.assertIn("scan_for_secrets(master_dir)", runner)
        self.assertNotIn("is not called from", working_copy)
        self.assertNotIn("Issue #3, still unresolved", working_copy)


class DeadCodeCharacterizationTests(unittest.TestCase):
    """Audit finding BUG-16. The audit's first dead-code sweep produced eight
    false positives by ignoring same-file references; re-verification left
    exactly one genuinely unreferenced public function."""

    def _reference_count(self, name: str) -> int:
        count = 0
        for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py")):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(rf"\b{re.escape(name)}\b", text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line = text[line_start : text.find("\n", match.start())]
                if line.strip().startswith(f"def {name}"):
                    continue
                count += 1
        return count

    def test_remove_pending_has_no_caller(self):
        """drain_pending() removes records inline instead of calling it."""
        self.assertEqual(self._reference_count("remove_pending"), 0)

    def test_previously_suspected_functions_are_actually_used(self):
        """Corrects the audit's own false positives, so they are not
        re-reported as dead code by a future sweep."""
        for name in ("safe_event_filename", "save_all", "current_timestamp"):
            with self.subTest(name=name):
                self.assertGreater(self._reference_count(name), 0)


if __name__ == "__main__":
    unittest.main()
