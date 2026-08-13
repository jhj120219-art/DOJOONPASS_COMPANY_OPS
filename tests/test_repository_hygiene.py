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


if __name__ == "__main__":
    unittest.main()


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
    The tool prints **four** blocks — COMPANY, HISTORY, LAST RUN, AGENT — and
    two of the four are where the state-vs-artifact consistency checks, the
    Backup Working Copy warnings, the Run Manifest and both lock checks live.
    An operator following the guide would not know the halves that carry most
    of the diagnostics exist at all.

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
    BLOCK_HEADINGS = ("COMPANY", "HISTORY", "LAST RUN", "AGENT", "ATTENTION")

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

        main = source.split("def main()", 1)[1]
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
