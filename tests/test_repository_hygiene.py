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
    notion.dashboard_pending.remove_pending() has no production caller

Nothing here changes production code, Runtime behaviour, or any spec.
"""

import ast
import inspect
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
DOCS = REPO_ROOT / "docs"
sys.path.insert(0, str(SRC))


def _load_entrypoint_module():
    """`run_company_ops.py` as a module, loaded under its own name.

    A file rather than a package import, and by path rather than by putting
    the repo root on `sys.path`, so nothing this test does changes what any
    other test imports.
    """
    import importlib.util

    path = REPO_ROOT / "run_company_ops.py"
    spec = importlib.util.spec_from_file_location("run_company_ops_hygiene", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )


def _tracked_files() -> list[Path]:
    """Every file that would be in the repository after `git add -A`.

    Widened from plain `git ls-files` (committed files only). The guards
    below exist to stop a secret from entering the repository, and a
    secret-bearing file that is merely *not committed yet* is exactly the
    state in which stopping it is still cheap — once `git ls-files` can
    see it, the leak has already happened and the guard is reporting
    history rather than preventing it.

    This was not hypothetical: the Multi-Desktop Agent Sprint added test
    files carrying deliberately secret-shaped fixtures, and the narrow
    version of this function reported a clean repository for every one of
    them right up until the commit that would have published them.

    `--exclude-standard` keeps .gitignore authoritative, so `.env`,
    `runtime/`, and `*.log` are still out of scope here — they are covered
    by `test_secret_and_runtime_paths_are_git_ignored` instead.
    """
    result = _git("ls-files", "--cached", "--others", "--exclude-standard")
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

    @property
    def LOCAL_PACKAGES(self):
        """Everything importable from `src/`, read from disk.

        This was a hand-maintained literal, which made it a table that had to
        be edited every time a module was added — and it failed exactly that
        way when `oplog.py` arrived. A stale entry here cannot make the guard
        stricter, only wrong: the guard's job is to catch a *third-party*
        import, and anything living under `src/` is local by definition.
        Deriving it keeps the guard honest and removes the edit.
        """
        return {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"} | {
            p.stem for p in SRC.glob("*.py") if not p.stem.startswith("__")
        }

    def test_the_local_package_set_is_derived_not_hardcoded(self):
        """Guards the guard: if this ever shrinks to a literal again, a new
        module silently becomes indistinguishable from a third-party one."""
        self.assertIn("oplog", self.LOCAL_PACKAGES)
        self.assertIn("review_cli", self.LOCAL_PACKAGES)
        self.assertIn("collector", self.LOCAL_PACKAGES)
        self.assertNotIn("__pycache__", self.LOCAL_PACKAGES)

    def test_src_imports_only_the_standard_library(self):
        # sys.stdlib_module_names was added in Python 3.10; fall back to
        # sys.builtin_module_names + stdlib_list on older interpreters so this
        # guard runs the same way on any supported Python version.
        stdlib = set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names)
        if not hasattr(sys, "stdlib_module_names"):
            import sysconfig

            stdlib_dir = Path(sysconfig.get_path("stdlib"))
            for entry in stdlib_dir.iterdir():
                name = entry.stem if entry.suffix == ".py" else entry.name
                if name:
                    stdlib.add(name)
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

    def test_docs_directory_contains_the_expected_specs(self):
        """The count is asserted so a spec cannot be *deleted* unnoticed —
        the README cross-check above already catches an added one."""
        names = sorted(p.name for p in DOCS.glob("*.md"))
        self.assertEqual(len(names), 15)
        self.assertIn("12_APPLICATION_FLOW_SPEC.md", names)
        self.assertIn("13_NOTION_ENVIRONMENT_SETUP.md", names)
        self.assertIn("14_RUN_CONTRACT.md", names)

    def test_readme_document_list_names_every_spec_that_exists(self):
        """FIXED — and now a guard rather than a record of the gap.

        README section 12 is a directory listing, not a rule: it stopped at
        `11_DEPLOYMENT_RUNBOOK.md` while `docs/` held two more. Completing a
        list of what is on disk states no new policy and reorders no
        priority (section 13 is what does that, and it is untouched), so it
        is the one part of BUG-12 that needed no spec decision.

        Written against `docs/*.md` rather than a hardcoded pair, so the next
        spec added has to be listed too.
        """
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        missing = [p.name for p in sorted(DOCS.glob("*.md")) if p.name not in readme]
        self.assertEqual(missing, [], "README section 12 does not list every spec")

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

    def test_remove_pending_has_no_production_caller(self):
        """`drain_pending()` rebuilds the remaining list and saves once,
        which is strictly better than calling `remove_pending()` per record
        (one write instead of N) — so this is not an oversight to correct by
        wiring it in.

        "No caller" means no caller in `src/` or the root entrypoints; this
        counter never looked at `tests/`. It does have tests now
        (`test_notion_dashboard.py::RemovePendingCharacterizationTests`),
        added because an exported function that is both unused and untested
        is a trap: it looks available, and the next person to reach for it
        would inherit whatever it happens to do. Its behaviour is pinned;
        whether it should exist at all is a deletion decision, recorded in
        BACKLOG.md.
        """
        self.assertEqual(self._reference_count("remove_pending"), 0)

    def test_previously_suspected_functions_are_actually_used(self):
        """Corrects the audit's own false positives, so they are not
        re-reported as dead code by a future sweep."""
        for name in ("safe_event_filename", "save_all", "current_timestamp"):
            with self.subTest(name=name):
                self.assertGreater(self._reference_count(name), 0)


class TestIsolationGuardTests(unittest.TestCase):
    """A test must never write into the developer's real `runtime/`.

    `app.runner.run_once()` defaults every path it is not given to a
    PROJECT_ROOT-relative location. Miss one in a test and the suite quietly
    appends to the operator's live logs — which is exactly what happened
    when `late_update_log_path` was added: `runtime/logs/daily_late_update.log`
    filled up with fabricated LATE_UPDATE records for Events that never
    existed, in the one file an operator would read to find out which real
    Events arrived late.

    Nothing failed, and nothing would have. Hence a static guard: any test
    module that drives the Runner must pass every log path explicitly.
    """

    # Written on every run that has work, regardless of configuration, so
    # every runner-driving test must redirect them. `monthly_state_path` is
    # here for the same reason even though it is state rather than a log:
    # it defaults into the real `runtime/state/`, and Monthly Consolidation
    # saves it as soon as any month becomes complete.
    UNCONDITIONAL_LOG_PARAMS = (
        "collector_log_path",
        "late_update_log_path",
        "monthly_state_path",
        # The Run Manifest is written on EVERY exit path, including the
        # aborting ones — so it is the most unconditional of the four, and a
        # test module that omits it overwrites the repository's real
        # runtime/runs/last_run.json on every single test.
        "run_summary_path",
    )
    # Only written when a Notion Sync is actually configured.
    NOTION_LOG_PARAM = "notion_sync_log_path"

    def _test_modules_driving_the_runner(self) -> list[Path]:
        """Detected by `local_master_dir=`, which only `app.runner.run_once()`
        takes. Matching on the import instead would both miss the modules
        that build a runner call inside a subprocess script string and match
        this file, which merely names the function in prose.
        """
        modules = []
        for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
            if path.name == Path(__file__).name:
                # This file names the parameter in order to search for it.
                continue
            if "local_master_dir=" in path.read_text(encoding="utf-8"):
                modules.append(path)
        return modules

    def test_at_least_one_module_drives_the_runner(self):
        """Guard against the guard silently matching nothing."""
        self.assertGreater(len(self._test_modules_driving_the_runner()), 3)

    def test_every_runner_driving_test_redirects_the_unconditional_logs(self):
        for path in self._test_modules_driving_the_runner():
            text = path.read_text(encoding="utf-8")
            for param in self.UNCONDITIONAL_LOG_PARAMS:
                with self.subTest(module=path.name, param=param):
                    self.assertIn(
                        f"{param}=",
                        text,
                        f"tests/{path.name} drives app.runner.run_once() without "
                        f"passing {param} — it will write into the real runtime/",
                    )

    def test_notion_driving_tests_also_redirect_the_notion_log(self):
        """`notion_sync.log` is only written when a Notion Sync is passed, so
        the requirement applies only to the modules that pass one."""
        for path in self._test_modules_driving_the_runner():
            text = path.read_text(encoding="utf-8")
            if "notion_sync=" not in text:
                continue
            with self.subTest(module=path.name):
                self.assertIn(f"{self.NOTION_LOG_PARAM}=", text)

    def test_the_runner_still_has_the_log_parameters_this_guard_names(self):
        """If a parameter is renamed, this guard must fail loudly rather
        than keep passing while checking for a name nothing uses."""
        import inspect

        sys.path.insert(0, str(SRC))
        from app.runner import run_once as runner_run_once

        signature = inspect.signature(runner_run_once)
        for param in self.UNCONDITIONAL_LOG_PARAMS + (self.NOTION_LOG_PARAM,):
            with self.subTest(param=param):
                self.assertIn(param, signature.parameters)


class EnvironmentContractTests(unittest.TestCase):
    """`.env.example` is the only place an operator learns what to set.

    Nothing loads it — every entrypoint reads `os.environ` directly, which
    `.env.example` states and this project has deliberately kept. That makes
    the template documentation rather than configuration, and documentation
    that drifts is worse than none: a variable the code requires but the
    template omits produces a deployment that fails at logon, with the exit
    code going to a scheduled task nobody watches.

    So the two are checked against each other in both directions.
    """

    ENTRYPOINTS = ("run_company_ops.py", "run_agent.py", "init_notion.py", "ops_status.py")

    def _declared_variables(self) -> set[str]:
        declared = set()
        for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            declared.add(stripped.split("=", 1)[0].strip())
        return declared

    def _read_variables(self) -> dict[str, set[str]]:
        """Every environment variable the code actually looks up.

        Matches both the direct `os.environ.get("NAME")` form and the
        `NAME_ENV_VAR = "COMPANY_OPS_PROFILE"` indirection
        `reporter/profiles.py` uses, since only the latter names the
        variable at all.
        """
        found: dict[str, set[str]] = {}
        sources = list(SRC.rglob("*.py")) + [REPO_ROOT / name for name in self.ENTRYPOINTS]
        for path in sources:
            if "__pycache__" in str(path) or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            patterns = (
                r'os\.environ(?:\.get)?\(?\[?\s*["\']([A-Z_][A-Z0-9_]*)["\']',
                r'source\.get\(\s*["\']([A-Z_][A-Z0-9_]*)["\']',
                r'^[A-Z_]*ENV_VAR\s*=\s*["\']([A-Z_][A-Z0-9_]*)["\']',
            )
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.M):
                    found.setdefault(match.group(1), set()).add(path.name)
        return found

    def test_every_variable_the_code_reads_is_documented(self):
        declared = self._declared_variables()
        for name, where in sorted(self._read_variables().items()):
            with self.subTest(variable=name):
                self.assertIn(
                    name,
                    declared,
                    f"{name} is read by {sorted(where)} but is missing from .env.example",
                )

    def test_every_documented_variable_is_actually_read(self):
        """The other direction: a template entry nothing reads is an
        instruction to the operator to configure something that does
        nothing."""
        read = set(self._read_variables())
        for name in sorted(self._declared_variables()):
            with self.subTest(variable=name):
                self.assertIn(name, read, f"{name} is documented but nothing reads it")

    def test_the_scan_finds_the_variables_we_know_exist(self):
        """Guard against the patterns above silently matching nothing and
        turning both checks into no-ops."""
        read = set(self._read_variables())
        for name in (
            "COMPANY_OPS_PROFILE",
            "COMPANY_OPS_HISTORY_START_DATE",
            "COMPANY_OPS_AGENT_SYNC_FOLDER",
            "NOTION_API_TOKEN",
        ):
            with self.subTest(variable=name):
                self.assertIn(name, read)

    # ------------------------------------------------------------------
    # The third direction, added in C48: what the tools *tell an operator*
    # to set.

    def _advertised_variables(self) -> dict[str, set[str]]:
        """`name -> entrypoints` for every `configured_by` name.

        `cli.unexpected_arguments()` prints these when a tool is given an
        argument it does not take, and its own docstring says why: "this
        takes no arguments" leaves an operator with nowhere to go, and the
        next thing they need is **the name of the knob that does exist".
        A name in that list is therefore an instruction, and an instruction
        that names a variable nothing reads is worse than silence — the
        operator sets it, nothing changes, and the tool never says so.

        Measured before this test existed (C48). Three of the seven names
        across the four entrypoints did not exist:

            COMPANY_OPS_NOTION_API_TOKEN     init_notion, run_company_ops
            COMPANY_OPS_NOTION_PROJECTS_DB   init_notion, run_company_ops
            COMPANY_OPS_RUNTIME_DIR          ops_status, run_company_ops

        The first two are misspellings of `NOTION_API_TOKEN` /
        `NOTION_PROJECTS_DATABASE_ID`, which the same two files name
        correctly in their own module docstrings. The third is not a knob at
        all: `RUNTIME_DIR` is a constant in both files, deliberately.

        `init_notion.py`, whose entire job is Notion configuration, listed
        only the two wrong ones — so an operator who followed its message
        exactly got the same `NotionConfigError` they were trying to fix.
        """
        import ast

        advertised: dict[str, set[str]] = {}
        for name in self.ENTRYPOINTS:
            path = REPO_ROOT / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if called != "unexpected_arguments":
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "configured_by":
                        continue
                    for element in ast.walk(keyword.value):
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            advertised.setdefault(element.value, set()).add(name)
        return advertised

    def test_the_advertised_scan_finds_every_entrypoints_list(self):
        """Guard against the AST walk above matching nothing and turning the
        two checks below into no-ops."""
        advertised = self._advertised_variables()

        self.assertTrue(advertised)
        covered = {tool for tools in advertised.values() for tool in tools}
        self.assertEqual(covered, set(self.ENTRYPOINTS))

    def test_every_variable_a_tool_advertises_is_one_something_reads(self):
        read = self._read_variables()
        for name, tools in sorted(self._advertised_variables().items()):
            with self.subTest(variable=name):
                self.assertIn(
                    name,
                    read,
                    f"{sorted(tools)} tell an operator to set {name}, "
                    "and nothing in this repository reads it",
                )

    def test_every_variable_a_tool_advertises_is_in_the_template(self):
        """The message ends with 'AGENT.md를 보세요', and the variables
        themselves are documented in `.env.example` (which AGENT.md §2.3
        points at). A name in neither leaves the operator with a string and
        no way to look it up."""
        declared = self._declared_variables()
        for name, tools in sorted(self._advertised_variables().items()):
            with self.subTest(variable=name):
                self.assertIn(
                    name,
                    declared,
                    f"{sorted(tools)} advertise {name}, which is not in .env.example",
                )

    def test_each_entrypoint_advertises_the_variables_it_needs(self):
        """Specific enough to fail on the exact regression that was found:
        the two Notion tools have to name the two Notion variables."""
        advertised = self._advertised_variables()

        for name in ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID"):
            with self.subTest(variable=name):
                self.assertEqual(
                    advertised.get(name),
                    {"init_notion.py", "run_company_ops.py"},
                )
        self.assertIn("run_company_ops.py", advertised["COMPANY_OPS_HISTORY_START_DATE"])
        self.assertIn("run_agent.py", advertised["COMPANY_OPS_PROFILE"])

    def test_no_entrypoint_silently_loads_a_dotenv_file(self):
        """`.env.example` states that nothing auto-loads it. If that ever
        changes, the template's own instructions become wrong."""
        for name in self.ENTRYPOINTS:
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(entrypoint=name):
                self.assertNotIn("dotenv", text.lower())
                self.assertNotIn('".env"', text)

    def test_the_template_names_every_entrypoint(self):
        """An operator reading only this file must be able to tell which
        command needs which variable."""
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                self.assertIn(name, text)


class EntrypointOutputOrderingTests(unittest.TestCase):
    """Every entrypoint's captured output must read in the order it was written.

    Python block-buffers stdout when it is not a terminal and leaves stderr
    unbuffered. Under `>log 2>&1` — which is how a scheduled task captures a
    run — that reorders the two streams against each other. Measured before
    the fix, with the entrypoints' own reconfigure() in place:

        1  stderr: the failure
        2  stdout: the context line explaining it
        3  stdout: trailing summary

    The failure printed above the thing it referred to. That is the only
    reading an operator gets from a captured log, and it makes a report
    describe the wrong event.

    All three entrypoints already reconfigure stdout for UTF-8 (a separate
    defect, also operator-facing); line buffering belongs on the same call.
    """

    ENTRYPOINTS = ("run_company_ops.py", "run_agent.py", "ops_status.py")

    def test_every_entrypoint_line_buffers_stdout(self):
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertIn(
                    'sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)',
                    text,
                    f"{name} block-buffers stdout; stderr will overtake it in a "
                    f"captured log",
                )

    def test_the_ordering_actually_holds_when_redirected(self):
        """Asserted by running it, because the property only appears when
        stdout is not a terminal — which is exactly the case no interactive
        check ever covers."""
        script = (
            "import sys\n"
            'sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)\n'
            'sys.stderr.reconfigure(encoding="utf-8")\n'
            'print("first-stdout")\n'
            'print("second-stderr", file=sys.stderr)\n'
            'print("third-stdout")\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "captured.log"
            with open(log, "w", encoding="utf-8") as handle:
                subprocess.run(
                    [sys.executable, "-c", script],
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            lines = log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(lines, ["first-stdout", "second-stderr", "third-stdout"])


class OperatorGuideMatchesTheToolTests(unittest.TestCase):
    """`AGENT.md` §6 describes `ops_status.py`. It had drifted.

    The guide said the tool "shows two things" and named COMPANY and AGENT.
    The tool prints COMPANY, HISTORY, LAST RUN, NOTION and AGENT — and most
    of those are where the state-vs-artifact consistency checks, the Backup
    Working Copy warnings, the Run Manifest, the Notion queue ages and both
    lock checks live. An operator following the guide would not know the
    halves that carry most of the diagnostics exist at all.

    It has since caught the drift it was written for: C32 added the NOTION
    block to the tool and this test failed on the same run, before the block
    could ship undocumented.

    Drift is the expected outcome, not an accident: this Sprint alone added
    lines to HISTORY and LAST RUN, and nothing anywhere connected the guide
    to the tool. BACKLOG E-11 records that "고쳤다" claims outlive the
    repository because tests and BACKLOG do not reference each other; this is
    the same failure between a document and the program it documents, and it
    is cheap to close for this one pair.

    Deliberately narrow. It asserts that every block heading the tool prints
    is named in the guide — not wording, not ordering, not completeness of
    the prose. A test that pinned the text would fail on every edit and be
    deleted; this one fails only when the tool grows a block the guide has
    never heard of, which is exactly the drift that happened.
    """

    # `ATTENTION` is one of them: it is printed with the same "NAME — "
    # shape when empty, and the guide already explains it. It is in this
    # list because the tool prints it, not because it looked like a
    # block — the second test below is what put it here.
    BLOCK_HEADINGS = (
        "COMPANY",
        "HISTORY",
        # The business layer, added in C46. Every other block is
        # operational; this one answers which projects moved, which are
        # blocked, and which team is silent.
        "CONTROL TOWER",
        "LAST RUN",
        "NOTION",
        "AGENT",
        "ATTENTION",
    )

    def test_the_guide_names_every_block_the_tool_prints(self):
        guide = (REPO_ROOT / "AGENT.md").read_text(encoding="utf-8")
        section = guide.split("## 6. 확인 방법", 1)
        self.assertEqual(len(section), 2, "AGENT.md §6 heading moved or renamed")
        body = section[1].split("\n## ", 1)[0]

        for heading in self.BLOCK_HEADINGS:
            with self.subTest(block=heading):
                self.assertIn(
                    heading, body, f"AGENT.md §6 does not mention the {heading} block"
                )

    def test_the_block_list_is_the_one_the_tool_actually_prints(self):
        """The other half: the list above must come from the program, not
        from whatever was true when this test was written."""
        source = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")

        printed = set(re.findall(r'print\("([A-Z][A-Z ]+) —', source))

        self.assertEqual(
            printed,
            set(self.BLOCK_HEADINGS),
            "ops_status.py prints a different set of blocks than this test "
            "and AGENT.md know about",
        )

    def test_the_documented_exit_codes_are_the_ones_returned(self):
        """§6 states `0` / `1` / `3`. A fourth would be undocumented."""
        guide = (REPO_ROOT / "AGENT.md").read_text(encoding="utf-8")
        source = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")

        main = source.split("def main(", 1)[1]
        returned = set(re.findall(r"return (\d+)", main))

        self.assertEqual(returned, {"0", "3"}, returned)
        for code in sorted(returned):
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", guide)


class DashboardDatabasesWithNoWriterTests(unittest.TestCase):
    """A-16, verified instead of remembered.

    `bootstrap_dashboard_databases()` creates five Operations databases and
    only one of them is ever written. That has been recorded since C10 as a
    sentence in BACKLOG, checked by nothing — so it was a claim about the
    code that could quietly stop being true (or quietly stay true while
    everyone assumed it had been fixed). Both directions are the failure mode
    E-11 names.

    **`DeadCodeCharacterizationTests` structurally cannot see this one.** It
    counts references by name, and `build_ops_backup_properties` *is*
    referenced — by `notion/__init__.py`'s import and `__all__`. A name that
    is exported but never called looks alive to a reference count. Verified
    with an AST walk instead, which counts actual `Call` nodes:

        build_ops_run_properties       1 call   (dashboard.record_run)
        build_ops_backup_properties    0 calls
        record_run                     1 call   (app/runner.py)

    Still SKIP: which database receives what is docs/04 §53's "Notion 데이터
    과잉 방지" decision, and wiring `OPS_BACKUP` in would add a Notion write
    to every run. This test decides nothing — it makes the day someone wires
    it in, or deletes it, a visible event that points back at A-16.

    The AST walk is deliberately narrow: `Call` nodes whose callee is a bare
    name or an attribute with the matching name. An indirect call through a
    variable would be missed, which is worth knowing rather than papering
    over — nothing in this repository dispatches these builders indirectly.
    """

    BUILDERS = ("build_ops_run_properties", "build_ops_backup_properties")

    def _call_counts(self, *names):
        counts = {name: 0 for name in names}
        for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py")):
            if "__pycache__" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a syntax error fails elsewhere
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if called in counts:
                    counts[called] += 1
        return counts

    def test_the_run_builder_is_called_and_the_backup_builder_is_not(self):
        counts = self._call_counts(*self.BUILDERS)

        self.assertGreaterEqual(counts["build_ops_run_properties"], 1)
        self.assertEqual(
            counts["build_ops_backup_properties"],
            0,
            "OPS_BACKUP now has a writer — A-16 needs updating either way",
        )

    def test_a_reference_count_cannot_see_it(self):
        """Why this test exists separately from `DeadCodeCharacterizationTests`:
        the exported name is referenced, so counting references finds it
        alive."""
        referenced = 0
        for path in list(SRC.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            referenced += text.count("build_ops_backup_properties")

        self.assertGreater(referenced, 1, "it is imported and exported")
        self.assertEqual(self._call_counts("build_ops_backup_properties")[
            "build_ops_backup_properties"
        ], 0)

    def test_every_bootstrapped_database_is_named_and_only_one_is_written(self):
        """The five databases are created; `record_run()` writes `OPS_RUNS`.
        Pinning both halves keeps the gap's size honest."""
        from notion.dashboard import (
            OPS_BACKUP,
            OPS_NOTION_SYNC,
            OPS_READINESS,
            OPS_RISK,
            OPS_RUNS,
        )
        import notion.dashboard as dashboard

        created = {OPS_RUNS, OPS_BACKUP, OPS_NOTION_SYNC, OPS_RISK, OPS_READINESS}
        self.assertEqual(len(created), 5)

        record_run_source = inspect.getsource(dashboard.record_run)
        self.assertIn("OPS_RUNS", record_run_source)
        for unwritten in (OPS_BACKUP, OPS_NOTION_SYNC, OPS_RISK, OPS_READINESS):
            with self.subTest(database=unwritten):
                self.assertNotIn(f'"{unwritten}"', record_run_source)

    def test_the_unwritten_databases_have_no_builder_at_all(self):
        """Three of the four are further from being wired than `OPS_BACKUP`:
        they have no property builder either. Recorded so that "four are
        empty" does not read as "four are one call away"."""
        import notion.dashboard as dashboard

        for database in ("notion_sync", "risk", "readiness"):
            with self.subTest(database=database):
                self.assertFalse(
                    hasattr(dashboard, f"build_ops_{database}_properties"),
                    f"build_ops_{database}_properties now exists — A-16 moved",
                )


class DeadCapabilityInventoryTests(unittest.TestCase):
    """The complete list of public functions nothing in production calls.

    Three separate BACKLOG entries had recorded one instance each of this
    shape (A-16's `build_ops_backup_properties`, E-20's REVIEW candidates,
    C31 §16's `build_role_summary`) without anyone ever making the list. This
    is the list, pinned, so a fourth cannot appear unnoticed and a recorded
    one cannot quietly get wired up while the record still says it is not.

    Counted by AST Call nodes per name. The two obvious alternatives were
    tried and are useless — "exported but unused outside its package" flags
    114 of 180 (normal intra-package calls), and name-based call-graph
    reachability from the entrypoints flags 64 of 231 including `run_intake`
    (15 real call sites) and `record_run` (12), because six modules define a
    `run_once` and the graph cannot tell them apart.

    Two of the four are superseded rather than missing: `Reporter`'s two
    convenience wrappers bundle report+write / report+send, and the Agent
    deliberately uses `report()` -> `outbox.stage()` -> `drain()` instead,
    because the outbox is what makes "an Event that was created is never
    lost" true. Calling them would bypass that.
    """

    # Every entry carries why it is here. An addition without a reason is
    # the thing this test exists to stop.
    EXPECTED = {
        # --- recorded as needing a decision -------------------------------
        "bootstrap_dashboard_databases",  # C31 §18: creates the OPS_* DBs;
                                          # no entrypoint calls it, and
                                          # wiring it writes to a real Notion
                                          # Workspace (A-8)
        "bootstrap_dashboard_properties",  # C36: adds OPS_RUNS columns that
                                           # a widening introduced. Same
                                           # reason as the line above — it
                                           # mutates a real Database, and
                                           # `init_notion.py` is pinned to
                                           # diagnose without creating
                                           # (test_the_setup_cli_does_not_
                                           # create_anything_from_the_
                                           # diagnosis). The operator runs
                                           # it deliberately; docs/13 §3-⑧
                                           # carries the command.
        "build_ops_backup_properties",    # A-16: OPS_BACKUP is never written
        "remove_pending",                 # B-7: deletion is the open decision
        # --- unwired capability, recorded ---------------------------------
        "build_role_summary",             # C31 §16 (A-3's record corrected)
        "for_role",                       # same module, same reason
        "of_category",                    # same module, same reason
        # --- superseded by a better mechanism -----------------------------
        "report_and_write",               # the outbox gives durability
        "report_and_send",                # the outbox gives durability
        "enqueue",                        # retry_queue's one-shot API; the
        "dequeue",                        # Runner uses the batch API (B안)
        # --- waiting on a credentialled sink, not on a decision -----------
        "to_payload",                     # C48: `DashboardModel.to_payload()`
                                          # is the Control Tower's hand-off
                                          # contract for a Notion projection,
                                          # and the Workspace it would write
                                          # to needs credentials this
                                          # repository does not have (A-8).
                                          # Distinct from the four above: no
                                          # decision is outstanding and the
                                          # shape is fixed by tests against
                                          # the very model `ops_status.py`
                                          # renders, so the screen and the
                                          # payload cannot drift while it
                                          # waits.
        # --- convenience accessors nothing needed yet ---------------------
        # `component` left this list in C46: `ops_status._same_instant_skips_
        # from_the_last_run()` needs one named component's metrics out of the
        # manifest, which is exactly the lookup it was written for.
        "read_event_json",                # local_output read seam
    }

    def _production_files(self):
        return [
            p
            for p in list((REPO_ROOT / "src").glob("**/*.py")) + list(REPO_ROOT.glob("*.py"))
            if "__pycache__" not in str(p)
        ]

    def test_the_inventory_is_exactly_what_is_recorded(self):
        import ast

        files = self._production_files()
        defined, called = {}, set()
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # Import aliases resolved. `app/runner.py` imports
            # `build_index as build_retry_queue_index` and calls the alias, so
            # a detector that matches on the called name alone reports a
            # heavily-used function as dead. That mistake was made — and
            # caught by this very test failing — before this line existed.
            aliases = {
                alias.asname: alias.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
                if alias.asname
            }
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        defined.setdefault(node.name, path.name)
                elif isinstance(node, ast.Call):
                    func = node.func
                    name = (
                        func.id
                        if isinstance(func, ast.Name)
                        else getattr(func, "attr", None)
                    )
                    if name:
                        called.add(aliases.get(name, name))
                elif isinstance(node, ast.Attribute):
                    called.add(aliases.get(node.attr, node.attr))

        uncalled = {name for name in defined if name not in called}
        # Properties are read, not called; they are not capabilities.
        uncalled -= {
            name
            for name in uncalled
            if self._is_property(files, name)
        }

        self.assertEqual(
            uncalled,
            self.EXPECTED,
            "the dead-capability inventory changed — update BACKLOG C31 §17 "
            f"(now: {sorted(uncalled)})",
        )

    def _is_property(self, files, name):
        import ast

        for path in files:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == name
                ):
                    for decorator in node.decorator_list:
                        target = (
                            decorator.id
                            if isinstance(decorator, ast.Name)
                            else getattr(decorator, "attr", None)
                        )
                        if target in ("property", "cached_property"):
                            return True
        return False


class AtomicWriteLeavesNoResidueTests(unittest.TestCase):
    """Every atomic writer must clean up its staging file when the write dies.

    Fourteen modules share the same four lines:

        except BaseException:
            try: os.remove(tmp_path)
            except OSError: pass
            raise

    Tracing which `src/` lines the suite executes showed that cleanup was
    **never reached** in the writers checked. It is the guard that prevents
    C27's entire finding — a `.tmp-*` file left behind by an interrupted run
    was read as a finished artifact by six consumers, promoted to an Event,
    and pushed to the backup remote as a truncated day of Company History.
    The fix for that was to teach the readers to skip `.tmp-`; this is the
    other half, and nothing was checking it.

    Driven by making `os.replace` fail, which is the last step of every one
    of these writers and the only one that can fail after the temp file
    exists. Two properties per writer: the exception still propagates (the
    caller must not think the write succeeded), and **no staging file
    survives**.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    # `write_summary()` is the one writer that must NOT propagate: a manifest
    # is a report about a run that already finished, and failing the run
    # because the report could not be filed would invert README RULE 9. It
    # still has to clean up after itself, which is what this class is about.
    SWALLOWS = {"runsummary.write_summary"}

    def _writers(self):
        """`(label, directory, call)` for each writer, ready to invoke."""
        from datetime import date, datetime

        from agent.state import AgentState, save_state as save_agent
        from backup.state import BackupState, save_state as save_backup
        from history import HistoryCandidate, HistoryDecision
        from history.file_repository import FileHistoryRepository
        from monthly.state import MonthlyState, save_state as save_monthly
        from notion.dashboard_pending import PendingDashboardRecord, save_all
        from notion.retry_queue import RetryQueueEntry, save_queue
        from reporter.local_output import write_event_json
        from runsummary import RunSummary, write_summary
        from scheduler.state import SchedulerState, save_state as save_scheduler
        from events import create_event

        def area(name):
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            return path

        event = create_event(
            source="DESKTOP_1", role="CTO_BACKEND", project_id="P",
            event_type="MILESTONE_COMPLETED", status="IN_PROGRESS",
            summary="s", history_candidate=True, event_id="EVT-1",
        )
        candidate = HistoryCandidate(
            history_id="HIST-1", event_id="EVT-1",
            timestamp="2026-08-05T10:00:00+09:00", category="MILESTONE",
            project_id="P", role="COO", summary="s", evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

        keep = area("keep")
        return [
            ("reporter.write_event_json", d := area("reporter"),
             lambda: write_event_json(event, directory=d)),
            ("history.FileHistoryRepository.save", keep,
             lambda: FileHistoryRepository(
                 keep_dir=keep, review_dir=area("review")
             ).save(candidate)),
            ("runsummary.write_summary", (r := area("runs")),
             lambda: write_summary(
                 r / "last_run.json",
                 RunSummary(run_id="R", started_at="a", finished_at="b"),
             )),
            ("scheduler.state.save_state", (s := area("sched")),
             lambda: save_scheduler(
                 s / "state.json",
                 SchedulerState(last_successful_daily_close=date(2026, 8, 1)),
             )),
            ("monthly.state.save_state", (m := area("mon")),
             lambda: save_monthly(m / "state.json", MonthlyState())),
            ("agent.state.save_state", (a := area("agent")),
             lambda: save_agent(a / "state.json", AgentState(desktop_id="DESKTOP_1"))),
            ("backup.state.save_state", (b := area("backup")),
             lambda: save_backup(
                 b / "state.json", BackupState(last_successful_backup=None)
             )),
            ("notion.retry_queue.save_queue", (q := area("queue")),
             lambda: save_queue(
                 q / "queue.json",
                 [RetryQueueEntry(
                     event_id="EVT-1", project_id="P", event_data=event.to_dict(),
                     added_at="2026-08-05T10:00:00+09:00", attempt_count=1,
                 )],
             )),
            ("notion.dashboard_pending.save_all", (p := area("pending")),
             lambda: save_all(
                 p / "pending.json",
                 [PendingDashboardRecord(
                     run_id="R", properties={}, queued_at="2026-08-05T10:00:00+09:00",
                     attempt_count=1,
                 )],
             )),
        ]

    def test_no_writer_leaves_a_staging_file_when_the_write_dies(self):
        import os
        import unittest.mock

        real_replace = os.replace

        for label, directory, call in self._writers():
            with self.subTest(writer=label):
                def exploding(src, dst, *, _real=real_replace):
                    raise OSError("simulated failure at the commit step")

                with unittest.mock.patch("os.replace", exploding):
                    if label in self.SWALLOWS:
                        call()  # must not raise — see SWALLOWS
                    else:
                        with self.assertRaises(
                            OSError, msg=f"{label} swallowed the failure"
                        ):
                            call()

                residue = [
                    p.name
                    for p in directory.iterdir()
                    if p.name.startswith(".tmp-")
                ]
                self.assertEqual(
                    residue,
                    [],
                    f"{label} left staging residue behind: {residue}",
                )

    def test_the_idiom_is_present_in_every_atomic_writer(self):
        """The structural half. A writer that grows a `mkstemp` without the
        cleanup would pass the behavioural test above only because it is not
        in its list."""
        import ast

        offenders = []
        for path in (REPO_ROOT / "src").glob("**/*.py"):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = {
                    getattr(inner.func, "attr", None)
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Call)
                }
                if "mkstemp" not in calls:
                    continue
                cleans = any(
                    isinstance(inner, ast.Try)
                    and any(
                        h.type is not None
                        and getattr(h.type, "id", None) == "BaseException"
                        for h in inner.handlers
                    )
                    for inner in ast.walk(node)
                )
                if not cleans:
                    offenders.append(f"{path.name}::{node.name}")

        self.assertEqual(
            offenders,
            [],
            "these mkstemp writers have no `except BaseException` cleanup, so an "
            f"interrupted write leaves a .tmp- file behind: {offenders}",
        )


class RecoveryTableMatchesTheRunnerTests(unittest.TestCase):
    """AGENT.md §6a-2 tells an operator which aborted steps recover on their
    own. That is a claim about `app/runner.py`, so it is checked.

    C35 measured every row of it (`RerunAfterAbortTests` in
    `test_runner_notion_integration.py` pins the behaviour). What this class
    pins is the *document* — that the table names every step the Runner has,
    and that it still singles out the one step whose abort no later run
    undoes.

    Drift here is the same failure `OperatorGuideMatchesTheToolTests` was
    written for, one section further down: a tenth pipeline step would be
    absent from the table and an operator following it would assume a
    recovery that never happens.
    """

    def _section(self):
        guide = (REPO_ROOT / "AGENT.md").read_text(encoding="utf-8")
        self.assertIn("### 6a-2.", guide, "AGENT.md §6a-2 heading moved or renamed")
        body = guide.split("### 6a-2.", 1)[1]
        return body.split("\n## ", 1)[0]

    def test_the_table_names_every_pipeline_step(self):
        from app.runner import PIPELINE_COMPONENTS

        section = self._section()

        for component in PIPELINE_COMPONENTS:
            with self.subTest(component=component):
                self.assertIn(
                    component,
                    section,
                    f"AGENT.md §6a-2 does not say what an abort in {component} leaves behind",
                )

    def test_history_filter_is_still_the_one_that_does_not_recover(self):
        """A-20's window. If this ever stops being true — a recovery pass, or
        a Collector that records the Candidate before marking seen — the
        table is wrong in the most dangerous direction: it would tell an
        operator to intervene where nothing is broken, or worse, the reverse.
        """
        section = self._section()

        self.assertIn("history_filter", section)
        self.assertIn("A-20", section)
        self.assertIn("이어받지 못한다", section)

    def test_it_points_at_the_line_ops_status_actually_prints(self):
        """The guide quotes two ATTENTION messages. Quoting text the tool does
        not produce sends an operator looking for a string that never
        appears."""
        section = self._section()
        ops_status = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")

        for quoted in (
            "수집됐지만 History에 들어가지 못한 Event",
            "History 반영 여부를 판단할 수 없다",
        ):
            with self.subTest(message=quoted):
                self.assertIn(quoted, section)
                self.assertIn(quoted, ops_status)

    def test_the_manifest_really_is_a_single_file(self):
        """The section's closing note explains why SUCCESS can sit next to a
        standing ATTENTION line. That rests on `last_run.json` holding only
        the last run."""
        from app.runner import DEFAULT_RUN_SUMMARY_PATH

        section = self._section()

        self.assertEqual(DEFAULT_RUN_SUMMARY_PATH.name, "last_run.json")
        self.assertIn("last_run.json", section)





class BacklogEvidenceLinksResolveTests(unittest.TestCase):
    """Every test class `BACKLOG.md` cites as evidence still exists.

    The BACKLOG's whole value is that each entry names what was measured and
    where the measurement lives. A citation to a class that no longer exists
    is worse than no citation: it reads as coverage, and the entry beside it
    reads as verified.

    E-11 is this repository's own name for the shape ("고쳤다는 기록을
    저장소가 검증하지 않는다"), and C38 fenced two other halves of it —
    `docs/NN §M` pointers and backticked file paths. This is the third.

    Measured before pinning: 119 test classes cited, **one missing** —
    `ReconciliationLockAwarenessTests`, listed as the four-test evidence for
    the ops_status half of `is_locked()` (A-20). The behaviour was live and
    had no test at all, including the part that decides whether a data-loss
    report can be silenced by a lock file. Written in C40; the citation now
    resolves.

    **Method names are deliberately not checked.** The same sweep over
    `test_[a-z_]+` flags 33 names, and all of them are false positives:
    module names (`test_observability`), and citations wrapped across a line
    break mid-identifier. A check whose failures are mostly noise is one
    people learn to silence.
    """

    def test_every_cited_test_class_exists(self):
        import re

        backlog = (REPO_ROOT / "BACKLOG.md").read_text(encoding="utf-8")
        suite = chr(10).join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO_ROOT / "tests").glob("test_*.py"))
        )

        cited = sorted(set(re.findall(r"\b([A-Z][A-Za-z0-9]*Tests)\b", backlog)))
        missing = [name for name in cited if f"class {name}(" not in suite]

        self.assertGreater(len(cited), 100, "the citation pattern stopped matching")
        self.assertEqual(missing, [])

    def test_the_check_would_notice_a_removed_class(self):
        """Guards the guard: the pattern has to be able to fail."""
        import re

        sample = "evidence: `tests/test_x.py::SomeVanishedTests` (4건)"
        cited = re.findall(r"\b([A-Z][A-Za-z0-9]*Tests)\b", sample)

        self.assertEqual(cited, ["SomeVanishedTests"])
        # And the second half of the check — "is it defined anywhere" — run
        # against the real suite, so a citation to a class nobody wrote is
        # demonstrably reported rather than assumed to be.
        suite = chr(10).join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO_ROOT / "tests").glob("test_*.py"))
        )
        missing = [name for name in cited if f"class {name}(" not in suite]

        self.assertEqual(missing, ["SomeVanishedTests"])


class ReleaseEnvironmentCheckStaysSafeTests(unittest.TestCase):
    """`docs/11` §101 item 4 is `python -m src.app.runner`, run as one of five
    Release Environment Checks. `src/app/runner.py` has no `__main__` block,
    so today that is an import and nothing else — the same thing item 5
    (`python -c "import src.app.runner"`) does.

    Which is exactly why the absence has to be pinned. Add a `__main__` block
    to that module and the release checklist silently becomes "run the full
    pipeline against whatever runtime this machine has", executed by whoever
    is verifying an environment — on a production Desktop 4, before anyone
    has decided a run should happen. `run_company_ops.py` is the entrypoint
    that may do that, and it guards itself (`_one_runtime_root_or_refuse()`);
    the library module has no such guard because it is not supposed to need
    one.

    Verified against the runbook rather than asserted in a vacuum: the test
    reads §101 and only demands the invariant while that command is in it.
    """

    def _release_check_section(self):
        runbook = (DOCS / "11_DEPLOYMENT_RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("Release Environment Check", runbook)
        return runbook.split("Release Environment Check", 1)[1]

    def test_the_runbook_still_runs_the_runner_as_a_module(self):
        self.assertIn("python -m src.app.runner", self._release_check_section())

    def test_the_runner_module_does_nothing_when_executed(self):
        import ast

        source = (REPO_ROOT / "src" / "app" / "runner.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        guards = [
            node
            for node in tree.body
            if isinstance(node, ast.If) and "__main__" in ast.dump(node.test)
        ]

        self.assertEqual(
            guards,
            [],
            "src/app/runner.py gained a __main__ block — docs/11 §101 item 4 "
            "would then run a real pipeline as part of an environment check",
        )

    def test_the_entrypoint_that_may_run_it_still_guards_itself(self):
        """The other half of the split: `run_company_ops.py` is allowed to
        run the pipeline, and refuses when the runtime root is ambiguous."""
        source = (REPO_ROOT / "run_company_ops.py").read_text(encoding="utf-8")

        self.assertIn("_one_runtime_root_or_refuse", source)
        self.assertIn('if __name__ == "__main__":', source)


class RestoreSectionMatchesTheCodeTests(unittest.TestCase):
    """AGENT.md §6a-3 tells an operator what the first run after a disaster
    restore does. Every claim in it is a claim about code.

    Written because the restore path had never been *run*: the disaster
    tests proved what the remote gives back and stopped there, so nobody had
    measured the state a restored Desktop 4 boots into — a complete Company
    History with no watermark. C39 measured it
    (`TheFirstRunAfterARestoreTests`), and this pins the document against
    the same code the measurement went through.

    The section's one instruction to a human — "if `generated` is large and
    `reused` is small after a restore, stop" — is only actionable while
    those two words are what the run actually prints.
    """

    def _section(self):
        guide = (REPO_ROOT / "AGENT.md").read_text(encoding="utf-8")
        self.assertIn("### 6a-3.", guide, "AGENT.md §6a-3 heading moved or renamed")
        return guide.split("### 6a-3.", 1)[1].split("\n## ", 1)[0]

    def test_the_two_words_it_tells_the_operator_to_compare_are_printed(self):
        """Asserted by RUNNING the reporter, not by finding an f-string.

        The source-text version of this test is what let the drift below
        happen: it pinned `f"generated={scheduler_result.generated_dates}"`,
        which is a claim about how the line is *written* and not about what
        it *says*. Interpolating a tuple of `date` objects prints their
        repr, so the program's real line was

            generated=(datetime.date(2026, 8, 5),) reused=(datetime.date(...

        while the section this class exists to hold it to shows

            generated=(2026-08-05,) reused=(08-01 … 08-04)

        Both halves passed. Running the function is the only version of this
        assertion the rendering cannot slip past.
        """
        section = self._section()

        self.assertIn("generated=", section)
        self.assertIn("reused=", section)

        line = self._scheduler_line(
            generated=(date(2026, 8, 5),),
            reused=(date(2026, 8, 1), date(2026, 8, 2)),
        )

        self.assertIn("generated=", line)
        self.assertIn("reused=", line)

    def test_the_printed_line_is_dates_a_human_reads_not_python_reprs(self):
        """REGRESSION, and the reason the test above now runs the code.

        §6a-3's instruction — compare `generated` against `reused` — is at
        its most important right after a disaster restore, which is exactly
        when both lists are longest. Measured, the production entrypoint run
        end to end in an isolated copy of this repository:

            before   generated=(datetime.date(2026, 8, 1), … )   606 chars
            after    generated=17 (2026-08-01, 2026-08-02, … 외 7일)

        and the restored-state reading it exists for, state removed and the
        Daily files left in place:

            generated=0 reused=17 (2026-08-01, …, 외 7일)
        """
        line = self._scheduler_line(
            generated=(date(2026, 8, 5),),
            reused=tuple(date(2026, 8, day) for day in range(1, 5)),
        )

        self.assertNotIn("datetime.date(", line)
        self.assertIn("generated=1 (2026-08-05)", line)
        self.assertIn("reused=4 (2026-08-01, 2026-08-02, 2026-08-03, 2026-08-04)", line)

    def test_a_long_list_says_how_many_it_did_not_print(self):
        """A truncation that does not say it truncated would make a long
        catch-up read as a short one — the same misreading in a new place."""
        line = self._scheduler_line(
            generated=tuple(date(2026, 8, day) for day in range(1, 18)), reused=()
        )

        self.assertIn("generated=17 (2026-08-01,", line)
        self.assertIn("외 7일", line)
        self.assertNotIn("reused=", line)

    def _scheduler_line(self, *, generated, reused):
        """The real `_print_result()` line, from real result objects."""
        import contextlib
        import io as _io

        from backup.log import BackupLogEntry
        from backup.result import BackupStatus
        from collector.runtime import RuntimeSummary
        from scheduler.result import SchedulerRunResult, SchedulerStatus
        from transport.intake import IntakeSummary

        module = _load_entrypoint_module()
        result = (
            IntakeSummary((), (), (), (), (), ()),
            RuntimeSummary(files=(), accepted=0, duplicate=0, rejected=0, failed=0),
            SchedulerRunResult(
                status=SchedulerStatus.COMPLETED,
                generated_dates=generated,
                reused_dates=reused,
            ),
            BackupLogEntry(
                run_id="R", backup_start=datetime(2026, 8, 18, 11, 0),
                source="s", changed_files=(), deleted_files=(),
                commit_hash=None, push_result=None,
                backup_end=datetime(2026, 8, 18, 11, 0),
                final_status=BackupStatus.NOT_REQUIRED,
            ),
            (),
        )

        buffer = _io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module._print_result(result)
        return next(
            item
            for item in buffer.getvalue().splitlines()
            if item.startswith("Daily History (Scheduler):")
        )

    def test_the_result_object_really_has_both_halves(self):
        from scheduler.result import SchedulerRunResult

        fields = SchedulerRunResult.__dataclass_fields__

        self.assertIn("generated_dates", fields)
        self.assertIn("reused_dates", fields)
        self.assertTrue(hasattr(SchedulerRunResult, "closed_dates"))

    def test_the_scheduler_still_checks_before_it_writes(self):
        """The whole reason restored History survives. If this guard were
        removed, §6a-3's measured table would become a lie in the most
        expensive direction — real History overwritten by empty days and
        pushed to the only copy."""
        source = (REPO_ROOT / "src" / "scheduler" / "scheduler.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("if not final_path.is_file():", source)

    def test_the_backup_scope_the_table_states_is_the_backup_scope(self):
        """§6a-3's table says `daily/` and `monthly/` come back and nothing
        else does. That is `docs/08` §26, and it is what makes the missing
        watermark unavoidable rather than an oversight."""
        source = (REPO_ROOT / "src" / "backup" / "working_copy.py").read_text(
            encoding="utf-8"
        )
        section = self._section()

        self.assertIn("daily", section)
        self.assertIn("monthly", section)
        self.assertIn("runtime/state/", section)
        # The writer's own list of what it copies.
        self.assertIn('"daily"', source)
        self.assertIn('"monthly"', source)
        self.assertNotIn('"state"', source)


class NoDocumentFreezesATestCountTests(unittest.TestCase):
    """C35: a test count written into a live document goes stale by the next
    Sprint, and nothing notices.

    Found by sweeping for it: `docs/13` carried "2244 passed" in two places,
    written three Sprints earlier, while the suite had grown past 2300. Both
    lines were release-readiness claims, so the stale number was doing real
    work — a reader checking the checklist would compare it against a run
    that no longer matches and have no way to tell which is right.

    The fix was to remove the number rather than update it, for the reason
    C33 §2 already applied to a property count in a docstring: a figure
    restated outside the thing that produces it is one more place that has
    to be remembered, and it will not be.

    **`BACKLOG.md` is deliberately exempt.** Its per-Sprint lines
    ("전체 Regression 2,331 passed") are dated historical records, not live
    claims — being fixed in time is what makes them useful. The distinction
    this test draws is between *what the suite is* (must not be frozen) and
    *what it was on a date* (must be).
    """

    #  "1234 passed" / "1,234 passed" — the shape a pasted pytest summary has.
    COUNT = re.compile(r"\b\d{1,3}(?:,\d{3})?\d*\s+passed\b")

    def _live_documents(self):
        docs = [REPO_ROOT / "README.md", REPO_ROOT / "AGENT.md"]
        docs += sorted(DOCS.glob("*.md"))
        return docs

    def test_no_live_document_states_a_pytest_pass_count(self):
        offenders = []
        for path in self._live_documents():
            text = path.read_text(encoding="utf-8")
            for match in self.COUNT.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {match.group()}")

        self.assertEqual(
            offenders,
            [],
            "a live document froze a test count — it will be wrong by the next "
            "Sprint. State the command instead, and let BACKLOG.md hold the "
            "dated figures.",
        )

    def test_the_pattern_would_actually_catch_one(self):
        """Guards the guard: a regex that matches nothing passes forever."""
        for sample in ("2244 passed", "2,331 passed", "전체 회귀 267 passed"):
            with self.subTest(sample=sample):
                self.assertTrue(self.COUNT.search(sample))

    def test_backlog_is_exempt_and_still_carries_its_dated_figures(self):
        """The exemption is load-bearing: if BACKLOG stopped recording them,
        there would be no measured history at all."""
        backlog = (REPO_ROOT / "BACKLOG.md").read_text(encoding="utf-8")

        self.assertNotIn(REPO_ROOT / "BACKLOG.md", self._live_documents())
        self.assertTrue(self.COUNT.search(backlog))


class DocumentPointersResolveTests(unittest.TestCase):
    """Every `docs/NN §M` the code cites points at a section that exists.

    The comments in this repository carry their reasoning by reference — 89
    such pointers across `src/` and the root scripts, and they are the main
    way a reader gets from a line of code to the decision behind it. A
    pointer to a section that was renumbered or removed is worse than no
    pointer: it sends the reader to the wrong paragraph, or to nothing, and
    it reads as authority the whole time.

    Same lens as E-11 ("a claim in a comment that outlived the code") and
    `NoDocumentFreezesATestCountTests` above, aimed at the one kind of claim
    those two do not cover — a cross-reference rather than a number.

    Measured before pinning: all 89 resolve today. This is a fence around a
    property the repository already has, not a cleanup.
    """

    # "docs/13_NOTION_ENVIRONMENT_SETUP.md §3-⑧-4", "docs/04 §66", and the
    # range form "docs/09 §50-51" — which is two pointers, both of which
    # must resolve. The trailing "-4"/"-⑧" of a sub-section is deliberately
    # not checked: documents number their sub-items in prose, inconsistently,
    # and the section is the part that gets renumbered.
    POINTER = re.compile(r"docs/(\d{2})[_A-Za-z0-9]*(?:\.md)?\s*§\s*(\d+)(?:\s*-\s*(\d+))?")

    def _sources(self):
        return [
            p
            for p in list((REPO_ROOT / "src").glob("**/*.py")) + list(REPO_ROOT.glob("*.py"))
            if "__pycache__" not in str(p)
        ]

    def _documents(self):
        return {path.name[:2]: path for path in DOCS.glob("*.md")}

    def _sections_defined_in(self, text):
        """A section is "defined" if the document writes `§N` anywhere, or has
        a heading starting with the number. Both forms are in use — docs/04
        writes "§66" inline, docs/13 uses "## 3. ..." headings — and this
        test is about dangling pointers, not about house style.
        """
        return set(re.findall(r"§\s*(\d+)", text)) | set(
            re.findall(r"^#{1,6}\s*(\d+)[\.\s]", text, re.M)
        )

    def _collect(self):
        pointers = {}
        for path in self._sources():
            text = path.read_text(encoding="utf-8")
            for match in self.POINTER.finditer(text):
                document, first, last = match.group(1), match.group(2), match.group(3)
                for section in (first, last) if last else (first,):
                    line = text[: match.start()].count("\n") + 1
                    pointers.setdefault((document, section), set()).add(
                        f"{path.relative_to(REPO_ROOT)}:{line}"
                    )
        return pointers

    def test_every_cited_section_exists(self):
        documents = self._documents()
        defined = {
            number: self._sections_defined_in(path.read_text(encoding="utf-8"))
            for number, path in documents.items()
        }

        dangling = []
        for (document, section), sites in sorted(self._collect().items()):
            if document not in documents:
                dangling.append(f"docs/{document} does not exist — {sorted(sites)[0]}")
            elif section not in defined[document]:
                dangling.append(
                    f"docs/{document} §{section} not found — {sorted(sites)[0]}"
                )

        self.assertEqual(dangling, [])

    def test_the_pattern_finds_the_pointers_that_are_actually_written(self):
        """Guards the guard, twice over: a regex that matches nothing passes
        forever, and one that misses the range form silently checks half."""
        found = self._collect()
        self.assertGreater(len(found), 50)

        samples = {
            "docs/04_NOTION_SYNC_SPEC.md §8": [("04", "8")],
            "docs/09 §50-51": [("09", "50"), ("09", "51")],
            "docs/13 §3-⑧-4": [("13", "3")],
        }
        for text, expected in samples.items():
            with self.subTest(text=text):
                matches = []
                for match in self.POINTER.finditer(text):
                    document, first, last = match.group(1), match.group(2), match.group(3)
                    matches += [
                        (document, section)
                        for section in ((first, last) if last else (first,))
                    ]
                self.assertEqual(matches, expected)

class DocumentPathsResolveTests(unittest.TestCase):
    """Every repository file a document names in backticks actually exists.

    The sibling of `DocumentPointersResolveTests` above, aimed at the other
    half of what a document points at. A `docs/NN §M` that no longer exists
    sends a reader to the wrong paragraph; a `src/notion/foo.py` that no
    longer exists sends them to nothing at all, and these documents are how
    an operator finds the file they are being told to run.

    Measured before pinning: 186 such references across README, AGENT.md and
    `docs/`, all resolving. A fence around a property the repository has,
    not a cleanup — same as the pointer test.

    Three kinds of reference are deliberately out of scope, each because it
    is not a claim about a file in this repository:

        runtime artifacts       `dashboard_pending.json`, `runtime/...` —
                                written by a run, absent on a fresh clone,
                                and their absence is normal rather than a
                                documentation error.
        filename patterns       `YYYY-MM-DD.md` is a shape, not a path.
        the other repository    `DOJOONPASS_OS/...` — README and docs/00
                                both name it in a sentence that says it is
                                a different repository (the Repository
                                Contract). A reference across that boundary
                                is correct and cannot be checked here.
    """

    PATH_LIKE = re.compile(
        r"`((?:[\w.\-]+/)*[\w.\-]+\.(?:py|md|json|ps1|txt|cfg|toml|yml|yaml))`"
    )

    # Written out rather than pattern-matched: an exemption list that grows
    # by accident is how this kind of test stops meaning anything.
    RUNTIME_ARTIFACTS = frozenset(
        {
            "dashboard_pending.json",
            "notion_retry_queue.json",
            "daily_history_state.json",
            "monthly_history_state.json",
            "collector_state.json",
            "backup_state.json",
            "agent_state.json",
            "last_run.json",
            "run_summary.json",
            "collector.log",
            "notion_sync.log",
            "daily_late_update.log",
            "agent.log",
            "YYYY-MM-DD.md",
            "YYYY-MM.md",
        }
    )

    def _documents(self):
        return [REPO_ROOT / "README.md", REPO_ROOT / "AGENT.md"] + sorted(
            DOCS.glob("*.md")
        )

    def _is_exempt(self, raw):
        if raw.startswith(("runtime/", "backup_working_copy/", "DOJOONPASS_OS/")):
            return True
        return Path(raw).name in self.RUNTIME_ARTIFACTS

    def _collect(self):
        found = {}
        for document in self._documents():
            text = document.read_text(encoding="utf-8")
            for match in self.PATH_LIKE.finditer(text):
                raw = match.group(1)
                line = text[: match.start()].count("\n") + 1
                found.setdefault(raw, []).append(
                    f"{document.relative_to(REPO_ROOT)}:{line}"
                )
        return found

    def test_every_documented_repository_path_exists(self):
        dangling = []
        for raw, sites in sorted(self._collect().items()):
            if self._is_exempt(raw):
                continue
            if (REPO_ROOT / raw).exists():
                continue
            # A bare filename is allowed to live anywhere in the tree —
            # documents say `runner.py` far more often than `src/app/runner.py`.
            if "/" not in raw and list(REPO_ROOT.glob(f"**/{raw}")):
                continue
            dangling.append(f"{raw} — {sorted(sites)[0]}")

        self.assertEqual(dangling, [])

    def test_the_pattern_finds_the_paths_that_are_actually_written(self):
        """Guards the guard: a regex matching nothing passes forever."""
        found = self._collect()
        self.assertGreater(len(found), 40)
        self.assertIn("ops_status.py", found)
        self.assertIn("src/app/runner.py", found)

    def test_the_exemptions_are_all_still_needed(self):
        """An exemption for a path that now resolves is an exemption that
        has stopped being a statement about anything — and the next reader
        would take it as evidence the file is a runtime artifact."""
        stale = [
            name
            for name in self.RUNTIME_ARTIFACTS
            if (REPO_ROOT / name).exists() and name not in ("README.md",)
        ]
        self.assertEqual(stale, [])


class NoTestFileHidesTestsBelowItsMainGuardTests(unittest.TestCase):
    """`if __name__ == "__main__": unittest.main()` must be the last thing in
    a test file, because `unittest.main()` runs at the point it is reached.

    Measured, before this test existed: twenty of the fifty-four test files
    carried that guard somewhere in the middle, with **760 test methods
    defined below it**. Running such a file directly executed only the part
    above the guard and printed `OK`:

        python tests/test_observability.py     Ran  44 tests ... OK
        pytest tests/test_observability.py     411 passed

    Nothing was broken — the suite runs under pytest, which imports the
    module and never reaches the guard — and that is exactly why it lasted.
    A green `OK` covering 11% of a file is the repository's own recurring
    failure mode (a silent pass that reads as coverage) sitting inside the
    tests that exist to catch it.

    Moving the twenty guards to EOF then surfaced a second thing they had
    been hiding: ten tests in `test_observability.py` and five in
    `test_monthly_history.py` import `ops_status`, which lives beside `src/`
    rather than in it, and only pytest had been putting the repository root
    on `sys.path`. Those fifteen raised `ModuleNotFoundError` the moment the
    direct run reached them. Fixed in the five affected headers.

    Both halves are pinned here: the guard is last, and every test file can
    still be run on its own.
    """

    def _test_files(self):
        return sorted((REPO_ROOT / "tests").glob("test_*.py"))

    def test_nothing_is_defined_below_a_main_guard(self):
        import ast

        offenders = []
        for path in self._test_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            guards = [
                node
                for node in tree.body
                if isinstance(node, ast.If) and "__main__" in ast.dump(node.test)
            ]
            if not guards:
                continue
            below = [
                node for node in tree.body if node.lineno > guards[-1].lineno
            ]
            if below:
                hidden = sum(
                    1
                    for node in below
                    if isinstance(node, ast.ClassDef)
                    for child in ast.walk(node)
                    if isinstance(child, ast.FunctionDef)
                    and child.name.startswith("test_")
                )
                offenders.append(f"{path.name}: {hidden} test methods below the guard")

        self.assertEqual(
            offenders,
            [],
            "`unittest.main()` runs where it is written — everything after it "
            "is skipped by `python tests/<file>.py` while the run still prints OK",
        )

    def test_a_file_importing_a_root_script_puts_the_root_on_sys_path(self):
        """The gap the guards were hiding.

        `src/` is inserted by every test file; the repository root is not,
        and `ops_status.py` / `run_company_ops.py` / `review_cli.py` live
        there. pytest adds the rootdir itself, so a missing insert is
        invisible under the runner the suite actually uses.
        """
        import re

        root_scripts = tuple(
            path.stem for path in REPO_ROOT.glob("*.py") if path.stem != "conftest"
        )
        self.assertIn("ops_status", root_scripts)

        offenders = []
        for path in self._test_files():
            source = path.read_text(encoding="utf-8")
            imports_root = any(
                re.search(rf"^\s*(?:from|import)\s+{script}\b", source, re.M)
                for script in root_scripts
            )
            if not imports_root:
                continue
            if "parents[1]))" not in source:
                offenders.append(path.name)

        self.assertEqual(offenders, [])

    def test_the_detector_would_catch_a_reintroduced_guard(self):
        """Guards the guard: run the same AST check against a file that has
        the defect, so an always-empty `offenders` list cannot pass forever.
        """
        import ast

        source = (
            "import unittest\n"
            'if __name__ == "__main__":\n'
            "    unittest.main()\n"
            "class LateTests(unittest.TestCase):\n"
            "    def test_one(self):\n"
            "        pass\n"
        )
        tree = ast.parse(source)
        guards = [
            node
            for node in tree.body
            if isinstance(node, ast.If) and "__main__" in ast.dump(node.test)
        ]
        below = [node for node in tree.body if node.lineno > guards[-1].lineno]

        self.assertTrue(below)


class AnEntrypointRefusesArgumentsItCannotHonourTests(unittest.TestCase):
    """C47, Release audit: every tool here silently ignored `sys.argv`.

    None of the four entrypoints reads `sys.argv`, `argparse` or
    `ArgumentParser` -- configuration is entirely environmental, and
    `scripts/install_agent_task.ps1` registers its action with no arguments
    at all. That is a coherent design, and it had one silent edge:

        python run_company_ops.py --dry-run

    ran a full production run. Real git push, real Notion writes, exit 0.
    The flag was not rejected, not warned about, not read. An operator
    reaching for `--dry-run` before a first production run is reaching for
    exactly the safety this had none of, and the tool answered by doing the
    unsafe thing and reporting success.

    `--help` was the same shape: it printed
    `COMPANY_OPS_HISTORY_START_DATE 환경변수가 없습니다`, a true sentence
    about a question nobody asked.

    Each entrypoint is run as a real subprocess, because that is the only way
    to check the thing that matters -- what the operating system sees when a
    person types the command.
    """

    ENTRYPOINTS = (
        "run_company_ops.py",
        "run_agent.py",
        "init_notion.py",
        "ops_status.py",
    )

    def _run(self, name, *arguments):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / name), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=REPO_ROOT,
            timeout=180,
        )

    def test_an_argument_is_refused_before_anything_happens(self):
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                result = self._run(name, "--dry-run")

                self.assertEqual(result.returncode, 1)
                self.assertIn("--dry-run", result.stderr)
                self.assertIn("환경변수", result.stderr)

    def test_asking_for_help_says_there_is_none(self):
        """The failure that reads as a bug report otherwise: `--help` used to
        answer with whichever environment variable happened to be checked
        first, which tells an operator nothing about why."""
        for name in self.ENTRYPOINTS:
            for flag in ("--help", "-h"):
                with self.subTest(entrypoint=name, flag=flag):
                    result = self._run(name, flag)

                    self.assertEqual(result.returncode, 1)
                    self.assertIn("--help가 없습니다", result.stderr)
                    self.assertIn("AGENT.md", result.stderr)

    def test_no_arguments_still_reaches_the_tool(self):
        """The half that must not change. `ops_status.py` is the one that can
        run with no configuration at all -- the others stop at their own
        environment check, which is the pre-existing behaviour and a
        different message.
        """
        result = self._run("ops_status.py")

        self.assertIn(result.returncode, (0, 3))
        self.assertIn("DOJOONPASS Company Ops", result.stdout)
        self.assertNotIn("명령줄 인자", result.stderr)

    def test_the_variables_named_are_variables_the_project_reads(self):
        """A refusal message whose whole value is telling an operator what to
        set instead. A stale name there sends them to a variable nothing
        looks at, which is worse than saying nothing.

        **This test was written in C47 and could not catch the thing it was
        for.** Two mistakes, both found in C48:

        1. It matched only `COMPANY_OPS_*`. The two Notion tools name
           `NOTION_API_TOKEN` / `NOTION_PROJECTS_DATABASE_ID`, so most of
           what the messages say was outside the scan entirely.
        2. `known` was "every `COMPANY_OPS_*` string that appears anywhere in
           the source" — and the `configured_by` tuple **is** source. A
           made-up name satisfied the check by being written down in the
           very list under test. `COMPANY_OPS_NOTION_API_TOKEN`,
           `COMPANY_OPS_NOTION_PROJECTS_DB` and `COMPANY_OPS_RUNTIME_DIR`
           passed here for a full Sprint.

        So `known` is now the set of names something actually **reads** —
        an `os.environ.get()` / `source.get()` call or a `*_ENV_VAR`
        constant — which is the property the message claims.
        `EnvironmentContractTests` checks the same relation statically; this
        one drives the real processes, so it also catches a message built
        somewhere other than `configured_by`.
        """
        patterns = (
            r'os\.environ(?:\.get)?\(?\[?\s*["\']([A-Z_][A-Z0-9_]*)["\']',
            r'source\.get\(\s*["\']([A-Z_][A-Z0-9_]*)["\']',
            r'^[A-Z_]*ENV_VAR\s*=\s*["\']([A-Z_][A-Z0-9_]*)["\']',
        )
        known = set()
        for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py")):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                known.update(re.findall(pattern, text, re.M))
        # The scan itself works — one variable read each way.
        self.assertIn("COMPANY_OPS_PROFILE", known)      # via a *_ENV_VAR constant
        self.assertIn("NOTION_API_TOKEN", known)         # via `source.get()`

        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                message = self._run(name, "--dry-run").stderr
                named = set(
                    re.findall(r"(?:COMPANY_OPS|NOTION)_[A-Z0-9_]+", message)
                )

                self.assertTrue(named, f"{name} names no variable at all")
                self.assertEqual(
                    named - known,
                    set(),
                    f"{name} tells an operator to set a variable nothing reads",
                )

    def test_the_rule_lives_in_one_place(self):
        """Four copies of "reject unknown arguments" is four chances for one
        of them to keep running. Each entrypoint imports the shared helper
        rather than restating the check."""
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                source = (REPO_ROOT / name).read_text(encoding="utf-8")

                self.assertIn("from cli import", source)
                self.assertIn("unexpected_arguments(", source)

    def test_the_helper_passes_an_empty_command_line_through(self):
        from cli import unexpected_arguments

        self.assertIsNone(
            unexpected_arguments(["run_agent.py"], tool="t", configured_by=("A",))
        )
        self.assertIsNotNone(
            unexpected_arguments(["run_agent.py", "x"], tool="t", configured_by=("A",))
        )


if __name__ == "__main__":
    unittest.main()
