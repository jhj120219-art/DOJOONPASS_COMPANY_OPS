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
import os
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


def _entrypoint_files() -> tuple[str, ...]:
    """Every operator-facing tool in this repository, read off disk.

    Two classes here need this list and each used to carry its own
    hand-written tuple. They disagreed, and both were wrong in a different
    place:

        AnEntrypointRefusesArgumentsItCannotHonourTests   4 of 5, missing
            `src/review_cli.py` — which then accepted `--help` and opened an
            edit session over live Decision Context (C79)
        EntrypointOutputOrderingTests                     3 of 5, missing
            `init_notion.py` and `src/review_cli.py` — and `init_notion.py`
            really did block-buffer stdout (C80)

    The `__main__` guard is the derivation because it is what makes a file a
    tool rather than a module: `cli.py`, `oplog.py` and `runsummary.py` sit
    in the same directory and have none. Paths are repo-relative with forward
    slashes so a name reads the same on every machine this is worked on.
    """
    files = sorted(REPO_ROOT.glob("*.py")) + sorted(SRC.glob("*.py"))
    return tuple(
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for path in files
        if '__name__ == "__main__"' in path.read_text(encoding="utf-8")
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
    # A failed scan is an error, not an empty repository.
    #
    # Every guard in this file iterates this list, so a `git` that is absent,
    # a directory that is not a checkout (a copied or exported tree — this
    # project is moved between Desktops), or any git error at all made
    # `stdout` empty and turned the secret scan, the UTF-8 scan, the LF scan
    # and the BOM scan into loops over nothing. All four passed. Measured:
    # with `_git` stubbed to return an empty stdout,
    # `test_no_secret_material_in_any_tracked_file` reported success over
    # zero files.
    #
    # Raising rather than asserting: this is a helper, and the tests that
    # call it should fail with the reason rather than with an empty-list
    # symptom thirty lines later.
    if result.returncode != 0:
        raise RuntimeError(
            "git ls-files failed, so every guard built on it would scan "
            f"nothing: {result.stderr.strip() or result.returncode}"
        )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line.strip()]


# The suffixes the encoding guards treat as text. One tuple rather than a
# literal repeated at each guard: the set drifting between two of them is how
# a file ends up covered for UTF-8 and not for line endings.
_TEXT_SUFFIXES = frozenset({".py", ".md", ".json", ".example", ".gitignore"})


def _tracked_eols() -> "list[tuple[str, str, Path]]":
    """`(index_eol, worktree_eol, path)` for every file `_tracked_files()` sees.

    `git ls-files --eol` is the only thing that can answer "what does the
    repository store" without re-implementing git's own normalisation rules
    (`core.autocrlf`, `core.eol`, `.gitattributes` `text`/`eol`, and the
    binary heuristic all feed into it). Its output is

        i/lf    w/crlf  attr/                 <TAB>path/to/file

    with the columns tab-separated from the path, so the path is taken after
    the first tab and never split on whitespace — `.env.example` survives
    that, a path with a space in it would not.

    An untracked file (`--others`) has no index entry and git reports `i/`
    with nothing after the slash; it comes back as `""` so a caller can tell
    "not in the index" from "in the index as LF".
    """
    result = _git("ls-files", "--eol", "--cached", "--others", "--exclude-standard")
    rows = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        columns, _, name = line.partition("\t")
        fields = columns.split()
        index_eol = ""
        worktree_eol = ""
        for field in fields:
            if field.startswith("i/") and not index_eol:
                index_eol = field[2:]
            elif field.startswith("w/") and not worktree_eol:
                worktree_eol = field[2:]
        rows.append((index_eol, worktree_eol, REPO_ROOT / name))
    return rows


def _git_normalises_line_endings() -> bool:
    """True when git rewrites CRLF to LF as a file is staged.

    Three independent ways to turn it on and any one of them is enough:
    `core.autocrlf=true` (CRLF in the checkout, LF in the commit — the
    Git-for-Windows installer default), `core.autocrlf=input` (LF in the
    commit, checkout left alone), or a `.gitattributes` marking paths `text`,
    which overrides both.
    """
    autocrlf = _git("config", "--get", "core.autocrlf").stdout.strip().lower()
    if autocrlf in ("true", "input"):
        return True
    attributes = REPO_ROOT / ".gitattributes"
    if attributes.is_file():
        for raw in attributes.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            for token in line.split()[1:]:
                if token in ("text", "text=auto", "eol=lf"):
                    return True
    return False


class TheScansThisFileTrustsAreNotEmptyTests(unittest.TestCase):
    """Every guard below loops over a scan. A scan that returns nothing makes
    its guard pass without checking anything.

    This is the same shape C57 found in the schema fingerprint — a
    comparison over an empty collection reports green — but the consequence
    here is worse, because the guards built on `_tracked_files()` are the
    **security** ones. Measured, with `_git` stubbed to an empty stdout:
    `test_no_secret_material_in_any_tracked_file` passed over zero files.

    The trigger is ordinary rather than exotic. `git ls-files` returns
    nothing whenever the working directory is not a checkout — a copied
    tree, an export, a Desktop where the repository was moved — and this
    project is deliberately worked on from several machines (AGENT.md §1).
    `_tracked_files()` now raises on a failed `git`, and this pins the
    remaining case: git succeeds and the tree really is empty.

    Ordered first in the file on purpose: if these fail, no verdict below
    them means anything.
    """

    def test_the_tracked_file_scan_finds_the_repository(self):
        files = _tracked_files()

        self.assertGreater(
            len(files),
            50,
            "the tracked-file scan came back nearly empty — every guard in "
            "this file loops over it and would pass without checking",
        )

    def test_the_eol_scan_finds_the_same_repository(self):
        rows = _tracked_eols()

        self.assertGreater(len(rows), 50)

    def test_the_two_scans_agree_about_how_many_files_there_are(self):
        """They ask git two different questions (`ls-files` and
        `ls-files --eol`); a divergence means one of them is filtering
        something the other is not, and the guards would disagree about
        what they cover."""
        self.assertEqual(len(_tracked_files()), len(_tracked_eols()))

    def test_the_source_tree_scan_finds_modules(self):
        """`DependencyGuardTests` and `NaiveAwareComparisonGuardTests` both
        walk `src/` this way."""
        self.assertGreater(len(list(SRC.rglob("*.py"))), 40)

    def test_a_failed_git_is_an_error_rather_than_an_empty_result(self):
        """The fix, driven. Before it, a broken `git` was indistinguishable
        from a repository with no files."""
        import subprocess as subprocess_module

        real = globals()["_git"]
        globals()["_git"] = lambda *args: subprocess_module.CompletedProcess(
            args, 128, stdout="", stderr="fatal: not a git repository"
        )
        try:
            with self.assertRaises(RuntimeError) as caught:
                _tracked_files()
        finally:
            globals()["_git"] = real

        self.assertIn("scan nothing", str(caught.exception))


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

    def test_a_measurement_this_backlog_recommends_leaves_nothing_behind(self):
        """C135. `.coverage` was ignored and `.coverage.*` was not.

        Those are the per-process files coverage writes in **parallel mode**,
        which is the only way to see inside a subprocess — and this suite
        drives every entrypoint as a subprocess on purpose, so it is the
        technique BACKLOG E-1b/E-0b names for measuring them. Measured: one
        such run left **50** untracked `.coverage.<host>.<pid>.<random>`
        files in the repository root, each of which `git status` offers to
        commit. A repository that makes its own recommended measurement
        messy is a repository where that measurement stops being taken.

        Asked of `git check-ignore` rather than of the file's text: the
        question is whether git ignores the name, and a pattern can be
        present and still not match (`.coverage` does not match
        `.coverage.x`, which is exactly how this was wrong).
        """
        for name in (
            ".coverage",
            ".coverage.DESKTOP-EXAMPLE.pid1234.XyZ0aBcD.HqRs1TuV2wXy",
        ):
            with self.subTest(name=name):
                result = _git("check-ignore", name)
                self.assertEqual(
                    result.returncode,
                    0,
                    f"git does not ignore {name!r}; a coverage run in "
                    f"parallel mode would leave it in `git status`",
                )

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
        return (
            {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}
            | {p.stem for p in SRC.glob("*.py") if not p.stem.startswith("__")}
            # The root tools too, since C135 widened the sweep to reach them:
            # `publish_control_tower.py` imports `dashboard_server`, and a
            # sibling entrypoint is as local as anything under `src/`.
            | {p.stem for p in REPO_ROOT.glob("*.py") if not p.stem.startswith("__")}
        )

    def test_the_local_package_set_is_derived_not_hardcoded(self):
        """Guards the guard: if this ever shrinks to a literal again, a new
        module silently becomes indistinguishable from a third-party one."""
        self.assertIn("oplog", self.LOCAL_PACKAGES)
        self.assertIn("review_cli", self.LOCAL_PACKAGES)
        self.assertIn("collector", self.LOCAL_PACKAGES)
        self.assertNotIn("__pycache__", self.LOCAL_PACKAGES)

    def _shipped_files(self):
        """Every file that has to run on a deployment machine.

        **`src/` *and* the repository root (C135).** This swept `src/` alone,
        which made it an assertion about a package rather than about the
        property its own docstring names — that `python -m pytest` and every
        tool work "with no install step at all" (docs/11 §101's Release
        Environment Check). An operator does not run `src/`; they run
        `run_company_ops.py` from Task Scheduler and `ops_status.py` by hand.
        A third-party import in either would have failed on a fresh machine
        with this gate green the whole time.

        Measured when the sweep was widened: **no third-party import in any
        of the six entrypoints**, so this is a fence rather than a repair —
        the fourth such widening in C135, all found by asking each gate what
        it cannot see rather than whether its rule is right.
        """
        files = [p for p in SRC.rglob("*.py") if "__pycache__" not in str(p)]
        files += [p for p in REPO_ROOT.glob("*.py") if p.is_file()]
        return sorted(files)

    def test_the_dependency_sweep_covers_the_tools_an_operator_runs(self):
        """Guards the guard. `test_src_imports_only_the_standard_library`
        asserts an empty dict, and a sweep that lost the root would keep
        asserting it — over less."""
        names = {path.name for path in self._shipped_files()}

        self.assertIn("run_company_ops.py", names)
        self.assertIn("ops_status.py", names)
        self.assertIn("businessdate.py", names)
        self.assertGreater(len(names), 50, sorted(names))

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
        for path in self._shipped_files():
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

    def test_the_repository_stores_every_text_file_with_lf_line_endings(self):
        """The property is about what git **stores**, not about the bytes on
        this machine's disk — and the first version of this gate confused the
        two, which cost a whole-suite failure on a clean tree.

        The harm being prevented is a review-destroying diff: a scripted edit
        that changed six lines of one file rewrote all 1,163 of them, and
        `git diff --stat` reported 42,985 deletions across C50. That harm is
        a property of the *blob*. A working tree full of CRLF is harmless as
        long as git normalises on the way in, and `core.autocrlf=true` — the
        Git-for-Windows installer's default, and the setting on the machine
        this Sprint ran on — does exactly that: `git ls-files --eol` reported
        `i/lf w/crlf` for 158 files whose blobs were already pure LF.

        C50 wrote the earlier gate against `path.read_bytes()` and recorded
        "`core.autocrlf` is `false` here" as the justification. That was true
        of one Desktop. This project is edited on several (AGENT.md §1), and
        a gate whose verdict depends on an unrecorded local git setting
        reports the setting rather than the repository. Measured: same
        commit, same clean tree, `git status` empty — 41 files reported as
        offenders and the suite red.

        `git ls-files --eol` answers the real question directly and gives the
        same answer on every machine, because the `i/` column is read out of
        the index.
        """
        offenders = []
        for index_eol, _worktree_eol, path in _tracked_eols():
            if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file():
                continue
            # `none` is git's answer for a file with no line ending at all —
            # an empty file, or a single line with no terminator. Nothing to
            # be wrong about. `""` is an untracked file, which has no index
            # entry yet; the working-tree guard below is what covers those.
            if index_eol not in ("lf", "none", ""):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: index eol is {index_eol}"
                )
        self.assertEqual(
            offenders,
            [],
            "git is storing CRLF for a file in a repository that is otherwise "
            "entirely LF — every later edit to it produces a whole-file diff "
            "that hides the real change:\n  " + "\n  ".join(offenders),
        )

    def test_a_crlf_working_copy_is_flagged_only_where_git_will_not_fix_it(self):
        """The working-tree half, and it is conditional on purpose.

        With `core.autocrlf` set to `true` or `input`, or with a
        `.gitattributes` marking these paths `text`, git normalises CRLF to
        LF when the file is staged — so CRLF on disk never becomes CRLF in a
        blob and there is nothing to report. Without any of those, the bytes
        on disk are the bytes that get committed, and *then* a CRLF file is
        the C50 finding: it enters the index as CRLF and the guard above can
        only catch it after the commit that already did the harm.

        So this fires exactly where it can still prevent something. On a
        machine that normalises it skips and says which setting made it skip,
        rather than passing for a reason a reader would have to reconstruct.
        """
        if _git_normalises_line_endings():
            autocrlf = _git("config", "--get", "core.autocrlf").stdout.strip()
            self.skipTest(
                "git normalises on staging here (core.autocrlf="
                f"{autocrlf or 'unset'}), so working-tree CRLF cannot reach a blob"
            )
        offenders = []
        for _index_eol, worktree_eol, path in _tracked_eols():
            if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file():
                continue
            if worktree_eol == "crlf":
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            "CRLF on disk on a machine where git stages bytes verbatim — "
            "these would enter the index as CRLF:\n  " + "\n  ".join(offenders),
        )

    def test_no_tracked_file_starts_with_a_utf8_bom(self):
        offenders = []
        for path in _tracked_files():
            if path.suffix.lower() not in {".py", ".md"} or not path.is_file():
                continue
            if path.read_bytes().startswith(b"\xef\xbb\xbf"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])


class APowerShellScriptStaysPureAsciiTests(unittest.TestCase):
    r"""C136. A `.ps1` in this repository may contain no character above
    ASCII, because on the machines it is written for it cannot survive one.

    **Windows PowerShell 5.1 reads a `.ps1` as ANSI unless the file carries a
    UTF-8 BOM.** AGENT.md §2.2 tells an operator to run the installer with
    `powershell -ExecutionPolicy Bypass -File`, and the Desktops are Korean
    Windows, so "ANSI" is cp949. A UTF-8 file without a BOM is therefore
    decoded with the wrong table and every non-ASCII character is mojibake.

    Measured on this machine before the fix (PowerShell 5.1.26100.9168,
    `[Text.Encoding]::Default` = `ks_c_5601-1987`), reading the installer's
    own comment-based help back through `Get-Help`:

        source   Company Ops ("OFF 허용 + Catch-up 구조가 더 적합하다")
        Get-Help Company Ops ("OFF ?덉슜 + Catch-up 援ъ“媛 ???곹빀?섎떎")

    That block is `.SYNOPSIS`/`.DESCRIPTION`, so `Get-Help
    .\install_agent_task.ps1` is a surface an operator actually reads — the
    C135 shape exactly: the code is correct and the rendered output is not.

    **Why ASCII rather than a BOM.** Adding a BOM would fix the decode and
    would contradict `test_no_tracked_file_starts_with_a_utf8_bom`, which
    this repository applies to every text file it gates. Between "add the one
    thing the repo forbids" and "write the comment in ASCII", the second
    costs nothing: the two offending spots were a Korean phrase quoted inside
    an English sentence and an em dash, and both say the same thing in ASCII.

    This also closes a scope gap rather than only a defect. `_TEXT_SUFFIXES`
    — which drives the UTF-8 and BOM gates — is `{.py, .md, .json, .example,
    .gitignore}` and has never included `.ps1`, so the only executable file
    in `scripts/` was outside both. (The **secret** scan is unaffected: it
    walks every tracked file and does not consult that set.)
    """

    def _scripts(self):
        return sorted(
            path
            for path in (REPO_ROOT / "scripts").rglob("*.ps1")
            if path.is_file()
        )

    def test_the_scan_finds_the_installer(self):
        """Guards the guard: the assertion below is a negative over this
        list, and a negative over nothing is true (C66 §4)."""
        names = {p.name for p in self._scripts()}

        self.assertIn("install_agent_task.ps1", names)

    def test_no_powershell_script_contains_a_character_above_ascii(self):
        offenders = []
        for path in self._scripts():
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                bad = sorted({c for c in line if ord(c) > 127})
                if bad:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: "
                        f"{''.join(bad)!r}"
                    )
        self.assertEqual(
            offenders,
            [],
            "Windows PowerShell 5.1 decodes a BOM-less .ps1 with the system "
            "codepage, so these characters reach the operator as mojibake",
        )

    def test_no_powershell_script_carries_a_bom_either(self):
        """The other half of the same rule. A BOM would make non-ASCII safe
        and is the thing this repository forbids everywhere else; the file
        must be ASCII *and* BOM-less, so neither escape is taken quietly."""
        for path in self._scripts():
            with self.subTest(script=path.name):
                self.assertFalse(
                    path.read_bytes().startswith(b"\xef\xbb\xbf"),
                    f"{path.name} starts with a UTF-8 BOM",
                )

    def test_the_suffix_set_the_other_encoding_gates_use_still_omits_ps1(self):
        """Why this class exists as its own rule rather than one more suffix.

        Adding `.ps1` to `_TEXT_SUFFIXES` would subject it to the UTF-8 and
        no-BOM gates, and both already pass — but neither of them says
        anything about ASCII, which is the property that actually matters
        here. If somebody does add the suffix later, this test fails and the
        reader is sent to this docstring instead of assuming the gap closed.
        """
        self.assertNotIn(".ps1", _TEXT_SUFFIXES)




class EveryInstallerPersistsWhatItsEntrypointNeedsTests(unittest.TestCase):
    """The last hand-written list on the Python/PowerShell boundary.

    Each installer test file asserted "every variable the entrypoint
    requires is persisted" against a **transcribed list of three**. The
    other direction was already derived -- the set is read out of the script
    with a regex over `SetEnvironmentVariable(` -- so a variable the
    *installer* gained was covered. A variable the *entrypoint* started
    requiring was not: nothing linked the two, and the failure is a task
    that registers cleanly and then exits 1 on every Desktop, every morning,
    for want of a value the installer never wrote.

    It is derivable, and from something this repository already keeps
    honest. `cli.unexpected_arguments(configured_by=...)` is each tool's own
    statement of what configures it, and three tests here already hold that
    tuple to what the process actually reads
    (`_advertised_variables()`, `test_every_variable_a_refusal_names_is_one
    something_reads`, and `EnvironmentContractTests`). So it is a checked
    declaration, not a comment.

    **One rule separates what an installer may persist from what it must
    not**, and it is the rule the project already applies rather than a new
    one: `NOTION_*` are secrets, `COMPANY_OPS_*` are not. Every installer
    says so in its own `.NOTES` -- a parameter would put a token in the
    command line, the process list and PowerShell history -- and
    `NoSecretIsHandledTests` enforces the refusal. So the derived
    requirement is `configured_by` narrowed to `COMPANY_OPS_*`.
    """

    #: installer -> the entrypoint it registers. Two names, both of which
    #: already have to be right for the installer to work at all: the script
    #: refuses to run if the entrypoint is missing, and
    #: `test_schedtask.py` checks the roster of installers itself.
    PAIRS = {
        "install_runner_task.ps1": "run_company_ops.py",
        "install_agent_task.ps1": "run_agent.py",
        "install_publish_task.ps1": "publish_control_tower.py",
    }

    def test_the_pairs_are_the_installers_that_exist(self):
        """Guards the guard, and against the pairing going stale: each
        installer really does name this entrypoint."""
        found = {p.name for p in (REPO_ROOT / "scripts").glob("install_*_task.ps1")}
        self.assertEqual(found, set(self.PAIRS))

        for installer, entrypoint in self.PAIRS.items():
            with self.subTest(installer=installer):
                script = (REPO_ROOT / "scripts" / installer).read_text(encoding="utf-8")
                self.assertIn(f"'{entrypoint}'", script)
                self.assertTrue((REPO_ROOT / entrypoint).is_file())

    @staticmethod
    def _configured_by(entrypoint):
        """The `configured_by` tuple that entrypoint passes, by AST.

        Read rather than imported: importing an entrypoint runs its
        module-level prologue, and two of these three reconfigure `stdout`.
        """
        tree = ast.parse((REPO_ROOT / entrypoint).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "configured_by":
                    continue
                return {
                    element.value
                    for element in keyword.value.elts
                    if isinstance(element, ast.Constant)
                }
        return set()

    def test_the_scan_reads_a_real_tuple_from_every_entrypoint(self):
        """Guards the guard. An empty read would make the sweep below a
        negative over nothing -- the vacuous pass this file keeps finding."""
        for installer, entrypoint in self.PAIRS.items():
            with self.subTest(entrypoint=entrypoint):
                self.assertTrue(self._configured_by(entrypoint), entrypoint)

    def test_every_non_secret_variable_the_entrypoint_needs_is_persisted(self):
        """The property the transcribed lists could not give.

        Narrowed to `COMPANY_OPS_*` because `NOTION_*` are secrets the
        installers deliberately refuse to touch -- `run_company_ops.py`
        names two of them in `configured_by`, and its installer must not
        write either.
        """
        for installer, entrypoint in self.PAIRS.items():
            script = (REPO_ROOT / "scripts" / installer).read_text(encoding="utf-8")
            persisted = set(
                re.findall(r"SetEnvironmentVariable\(\s*'([A-Z_]+)'", script)
            )
            required = {
                name
                for name in self._configured_by(entrypoint)
                if name.startswith("COMPANY_OPS_")
            }
            with self.subTest(installer=installer):
                self.assertEqual(
                    required - persisted,
                    set(),
                    f"{entrypoint} is configured by these and {installer} "
                    f"never writes them; the task would register and then "
                    f"exit 1 every morning",
                )

    def test_no_installer_persists_a_secret(self):
        """The other half of the same narrowing, so it cannot be read as an
        excuse. What is excluded above must be excluded *because it is a
        secret*, not because an installer quietly stopped writing it."""
        for installer in self.PAIRS:
            script = (REPO_ROOT / "scripts" / installer).read_text(encoding="utf-8")
            persisted = set(
                re.findall(r"SetEnvironmentVariable\(\s*'([A-Z_]+)'", script)
            )
            with self.subTest(installer=installer):
                self.assertEqual(
                    {name for name in persisted if name.startswith("NOTION_")}, set()
                )

    def test_the_publish_installer_persists_nothing_because_it_needs_nothing(self):
        """The case that proves the narrowing is real rather than a way of
        excusing an empty set: `publish_control_tower.py` is configured by
        two variables and **both are secrets**, so its installer correctly
        writes none at all -- and says so on the screen instead."""
        required = self._configured_by("publish_control_tower.py")

        self.assertTrue(required)
        self.assertEqual(
            {name for name in required if name.startswith("COMPANY_OPS_")}, set()
        )
        script = (REPO_ROOT / "scripts" / "install_publish_task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("SetEnvironmentVariable", script)
        for name in required:
            with self.subTest(variable=name):
                self.assertIn(name, script, "the operator is not told to set it")


class NothingNewIsSpelledOutOnBothSidesOfTheBoundaryTests(unittest.TestCase):
    """Every value written in both PowerShell and Python, and what holds it.

    This project's deployment tooling is PowerShell and everything it
    deploys is Python, so any value both sides need is a value that can be
    written down twice. C138 did exactly that -- task names and log
    filenames as literals in `src/schedtask.py` **and** in each installer,
    with a test comparing the two -- and C139 removed the copies rather than
    the drift: the installers now ask `schedtask` at install time.

    What is left is swept here, because "we fixed the ones we knew about" is
    not a property. The scan reads every quoted literal out of the
    installers' **executable** text and asks whether any Python file spells
    the same thing; each survivor has to be on the roster below with the
    thing that keeps it honest.

    The roster is reasons, and the scan is derived from disk -- the shape
    this repository uses for `ALLOWED_SILENT`, `LayeringInvariantTests
    .ALLOWED` and `KNOWN_DIFFERENT`. A new shared literal that nobody has
    thought about fails here.
    """

    #: What each surviving cross-boundary literal is, and what stops it
    #: drifting. Every entry names a test, and `test_every_reason_names_a
    #: _test_that_exists` checks that the test is really there.
    ACCOUNTED_FOR = {
        "COMPANY_OPS_PROFILE": "EveryInstallerPersistsWhatItsEntrypointNeedsTests",
        "COMPANY_OPS_AGENT_SYNC_FOLDER": "EveryInstallerPersistsWhatItsEntrypointNeedsTests",
        "COMPANY_OPS_AGENT_START_DATE": "EveryInstallerPersistsWhatItsEntrypointNeedsTests",
        "COMPANY_OPS_HISTORY_START_DATE": "EveryInstallerPersistsWhatItsEntrypointNeedsTests",
        # The Desktop roster. `reporter/profiles.PROFILES` is docs/02
        # section 8's own source->role table; the installer's ValidateSet is
        # read out of the script and compared against it.
        "DESKTOP_1": "test_the_desktop_id_is_restricted_to_the_real_profiles",
        "DESKTOP_2": "test_the_desktop_id_is_restricted_to_the_real_profiles",
        "DESKTOP_3": "test_the_desktop_id_is_restricted_to_the_real_profiles",
        "DESKTOP_4": "test_the_desktop_id_is_restricted_to_the_real_profiles",
        # Irreducible: an installer has to name the entrypoint it registers,
        # and that file has to exist. Both are checked.
        "run_company_ops.py": "test_the_pairs_are_the_installers_that_exist",
        "run_agent.py": "test_the_pairs_are_the_installers_that_exist",
        "publish_control_tower.py": "test_the_pairs_are_the_installers_that_exist",
    }

    #: **A test that used to be here was removed, and saying why matters
    #: more than the test did.** `test_the_task_names_are_no_longer_written
    #: _on_both_sides` asserted that no `DOJOONPASS_*` or `scheduled_*.log`
    #: literal is shared across the boundary -- the pair C139 had just
    #: stopped duplicating. It was written in the same Sprint as
    #: `test_schedtask.py::TaskNamesMatchTheInstallersTests
    #: ::test_no_installer_hard_codes_a_task_name`, which asserts the same
    #: thing and more: that one fires whether or not Python also spells the
    #: name, this one only fired when both sides did.
    #:
    #: Measured before removing it, by putting the literal back into
    #: `install_publish_task.ps1`: three tests fired, and two of them were
    #: the general properties (`test_no_installer_hard_codes_a_task_name`
    #: and `test_every_value_written_on_both_sides_is_accounted_for`). The
    #: third proved nothing the other two had not.
    #:
    #: Removed rather than kept because a redundant gate is not free: it is
    #: a second place to edit, a second thing to read, and it makes a suite
    #: look like it covers two properties when it covers one. The same
    #: Sprint that wrote it spent its time deleting duplication elsewhere.

    #: PowerShell vocabulary, not project values. Excluded by what they are
    #: rather than by having been seen before.
    _POWERSHELL_WORDS = frozenset(
        {"SilentlyContinue", "Stop", "IgnoreNew", "User", "Continue"}
    )

    @staticmethod
    def _installer_literals():
        """Quoted literals in the installers' executable text.

        Comment-based help and `#` lines are stripped: `.NOTES` quotes task
        names for an operator to paste, and counting documentation as a
        duplicated constant would report every correct script.
        """
        found = {}
        for path in sorted((REPO_ROOT / "scripts").glob("*.ps1")):
            text = re.sub(
                r"<#.*?#>", "", path.read_text(encoding="utf-8"), flags=re.DOTALL
            )
            code = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#")
            )
            for token in re.findall(r"'([A-Za-z0-9_./\\-]{6,})'", code):
                found.setdefault(token, set()).add(path.name)
        return found

    @staticmethod
    def _python_files():
        return [
            path
            for path in list(REPO_ROOT.glob("*.py")) + list(SRC.rglob("*.py"))
            if "__pycache__" not in path.as_posix()
        ]

    def _shared(self):
        literals = self._installer_literals()
        self.assertGreater(len(literals), 10, "the literal scan found almost nothing")

        sources = {p: p.read_text(encoding="utf-8") for p in self._python_files()}
        shared = {}
        for token, scripts in literals.items():
            if token in self._POWERSHELL_WORDS:
                continue
            pattern = re.compile(r"""['"]""" + re.escape(token) + r"""['"]""")
            if any(pattern.search(text) for text in sources.values()):
                shared[token] = sorted(scripts)
        return shared

    def test_the_scan_still_finds_the_known_duplicates(self):
        """Guards the guard. The assertion below is a negative over this
        set, and a scan that stopped matching would pass for everything --
        including a task name spelled out again in both places."""
        shared = self._shared()

        self.assertIn("COMPANY_OPS_PROFILE", shared)
        self.assertIn("DESKTOP_1", shared)
        self.assertGreaterEqual(len(shared), 8, sorted(shared))

    def test_every_value_written_on_both_sides_is_accounted_for(self):
        """The property. A value the installers and the Python both spell
        out, with nothing holding the two together, is the defect C138
        introduced and C139 removed -- this is what stops the next one."""
        unaccounted = sorted(set(self._shared()) - set(self.ACCOUNTED_FOR))

        self.assertEqual(
            unaccounted,
            [],
            "these are written in both scripts/*.ps1 and Python with nothing "
            "keeping them in step -- derive one side from the other, or add "
            "the test that does keep them in step:\n  " + "\n  ".join(unaccounted),
        )

    def test_the_roster_does_not_name_a_value_that_is_gone(self):
        """The other direction: a stale entry excuses a duplication that no
        longer exists and hides one that might take its name."""
        self.assertEqual(sorted(set(self.ACCOUNTED_FOR) - set(self._shared())), [])

    def test_every_reason_names_a_test_that_exists(self):
        """An entry pointing at a test nobody wrote is an excuse, not a
        reason.

        **Written wrong the first time, and the mistake is the interesting
        part.** It searched the suite for the reason as a *substring*, and
        the roster above lives in this file -- which is in the suite. So
        every entry matched itself. The first name tried,
        `test_the_desktop_ids_are_the_ones_the_project_defines`, is defined
        nowhere at all, and the guard passed.

        A reason must therefore name a **definition**: `class X` or `def X(`.
        Measured after the fix -- the invented name fails and the real one
        (`test_the_desktop_id_is_restricted_to_the_real_profiles`) passes.
        """
        suite = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO_ROOT / "tests").glob("test_*.py"))
        )
        for value, reason in sorted(self.ACCOUNTED_FOR.items()):
            with self.subTest(value=value):
                self.assertTrue(
                    f"class {reason}" in suite or f"def {reason}(" in suite,
                    f"{value} points at {reason}, which is defined nowhere",
                )

    def test_that_guard_would_notice_an_invented_name(self):
        """The predicate, on the exact name that slipped through. It is
        still quoted in the docstring above -- which is precisely why a
        substring search was not enough."""
        suite = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO_ROOT / "tests").glob("test_*.py"))
        )
        invented = "test_the_desktop_ids_are_the_ones_the_project_defines"

        self.assertIn(invented, suite)
        self.assertNotIn(f"def {invented}(", suite)
        self.assertNotIn(f"class {invented}", suite)
    def _scripts(self):
        return sorted((REPO_ROOT / "scripts").glob("install_*_task.ps1"))

    @staticmethod
    def _code(path):
        text = re.sub(r"<#.*?#>", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    def test_the_scan_finds_every_installer(self):
        """Guards the guard. Named individually so a renamed or deleted
        installer fails here rather than shrinking the sweep in silence."""
        self.assertEqual(
            {path.name for path in self._scripts()},
            {
                "install_agent_task.ps1",
                "install_runner_task.ps1",
                "install_publish_task.ps1",
            },
        )

    def test_the_comment_stripper_would_notice_a_rule_that_is_only_discussed(self):
        """The predicate this class rests on. Every rule below appears in
        prose in these files too, so a scan reading whole text would pass a
        script that had lost the rule and kept the paragraph about it."""
        sample = self._code(REPO_ROOT / "scripts" / "install_agent_task.ps1")

        self.assertNotIn("#", sample.replace("$#", ""))
        self.assertIn("Register-ScheduledTask", sample)

    def test_every_logon_trigger_is_scoped_to_this_user(self):
        for path in self._scripts():
            with self.subTest(installer=path.name):
                code = self._code(path)
                if "-AtLogOn" not in code:
                    continue
                self.assertIn("New-ScheduledTaskTrigger -AtLogOn -User", code)

    def test_every_installer_verifies_the_task_exists_afterwards(self):
        for path in self._scripts():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertIn("Get-ScheduledTask -TaskName $taskName", code)
                self.assertLess(
                    code.index("Register-ScheduledTask"),
                    code.index("$registered = Get-ScheduledTask"),
                    "the verification must follow the registration",
                )
                self.assertIn("Nothing is scheduled.", code)

    def test_every_installer_drops_an_overlapping_run(self):
        for path in self._scripts():
            with self.subTest(installer=path.name):
                self.assertIn("-MultipleInstances IgnoreNew", self._code(path))

    def test_every_installer_survives_a_machine_that_was_switched_off(self):
        for path in self._scripts():
            with self.subTest(installer=path.name):
                self.assertIn("-StartWhenAvailable", self._code(path))

    def test_every_installer_is_safe_to_re_run(self):
        """Every refusal message in these scripts tells the operator to fix
        something and run again. That advice is only true with `-Force`."""
        for path in self._scripts():
            with self.subTest(installer=path.name):
                self.assertIn("-Force", self._code(path))

    def test_every_installer_gives_the_scheduled_run_somewhere_to_print(self):
        for path in self._scripts():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertIn("2>&1", code)
                self.assertIn('>> "{2}"', code)
                self.assertIn("$comspec", code)

    def test_every_installer_creates_the_log_directory_first(self):
        for path in self._scripts():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertIn("New-Item -ItemType Directory -Path $logDir", code)
                self.assertLess(
                    code.index("New-Item -ItemType Directory -Path $logDir"),
                    code.index("Register-ScheduledTask"),
                )

    def test_every_installer_can_be_previewed(self):
        """`-WhatIf` is the only way to see what an installer would do
        without doing it, and it works only if the script declares support
        for it. The Agent installer's environment writes once ran during a
        preview because they sat outside the guard."""
        for path in self._scripts():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertIn("SupportsShouldProcess = $true", code)
                self.assertIn("$PSCmdlet.ShouldProcess", code)

    def test_no_installer_stores_a_secret(self):
        """The Notion credentials are secrets; a parameter would put them in
        the command line, the process list and PowerShell history."""
        for path in self._scripts():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertNotIn("SetEnvironmentVariable('NOTION", code)
                self.assertNotIn('SetEnvironmentVariable("NOTION', code)
                for forbidden in ("$Token", "$ApiToken", "$NotionToken", "$Secret"):
                    self.assertNotIn(forbidden, code)

    def test_an_installer_that_needs_a_secret_says_where_it_has_to_live(self):
        """The gap between "the tool works" and "the deployment works" (C147).

        These two installers deliberately refuse the Notion credentials —
        `test_no_installer_stores_a_secret` above is that decision — so a
        person sets them, and *where* they set them decides whether the
        scheduled run ever sees them. A scheduled task inherits no
        interactive shell.

        The scripts knew this and used it: each persists its own non-secret
        variable with `SetEnvironmentVariable(..., 'User')` and says why.
        Then they printed the three variables they do not set with no scope
        at all, and `.env.example` said "export these in the shell".

        Measured on this repository, which followed that advice: `.env` holds
        the credentials, `ops_status.py` reports *"자격증명: .env에 있으나 이
        프로세스에 전달되지 않았다"*, and `runtime/logs/notion_sync.log`
        **does not exist** — Notion has never been reached once. A shell
        export would make an interactive run work and change nothing about
        the scheduled one, and because Notion sits off the History critical
        path (README RULE 5) that run still exits 0. Success-shaped failure.

        Asserted as a property, not as wording: the installer must name the
        USER scope, say a scheduled task inherits nothing, and point at the
        one document that carries the command.
        """
        needs_a_secret = ("install_runner_task.ps1", "install_publish_task.ps1")
        for path in self._scripts():
            if path.name not in needs_a_secret:
                continue
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertIn("USER environment", code)
                self.assertIn("inherits no interactive shell", code)
                self.assertIn("13_NOTION_ENVIRONMENT_SETUP.md", code)

    def test_the_document_those_installers_point_at_carries_the_command(self):
        """The pointer is only worth having if the target answers.

        The command lives in exactly one place on purpose: an installer is
        scanned by `test_no_installer_stores_a_secret`, which forbids the
        literal `SetEnvironmentVariable("NOTION` anywhere in it — a guard
        about *storing* a secret that cannot tell a call from printed help.
        Keeping that guard at full strength and putting the command in the
        document is the trade this makes.
        """
        doc = (REPO_ROOT / "docs" / "13_NOTION_ENVIRONMENT_SETUP.md").read_text(
            encoding="utf-8"
        )
        section = doc[doc.index("### 2.1"):doc.index("## 3.")]

        for name in ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID"):
            with self.subTest(variable=name):
                self.assertIn(f"SetEnvironmentVariable('{name}'", section)
        self.assertIn("'User'", section)
        # And the reason, so the command is not a rite performed blindly.
        self.assertIn("exit 0", section)

    def test_the_env_template_sends_the_reader_to_the_same_place(self):
        """`.env.example` is the file an operator has open while doing this,
        and its own advice ("export these in the shell") is the one that
        produces the silent failure for a scheduled run."""
        template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("USER environment", template)
        self.assertIn("13_NOTION_ENVIRONMENT_SETUP.md", template)

    def test_the_publish_runs_after_the_runner_it_reports_on(self):
        """The one relationship between two installers' defaults.

        The Control Tower page shows what the Runner produced, so publishing
        before the Runner has finished shows yesterday's numbers until the
        next trigger. `install_publish_task.ps1` documents its 11:30 as
        "after the Runner's 11:00 default" and docs/11 section 19b says the
        same -- and nothing checked that the two defaults still stand in that
        order.

        A relationship, not a pair of literals: whatever the two are, the
        publish must not be earlier. Moving the Runner and forgetting the
        publish is exactly what this catches, and it is a plausible edit --
        `-DailyAt` exists on both scripts precisely so the times can move.
        """
        times = {}
        for name in ("install_runner_task.ps1", "install_publish_task.ps1"):
            code = self._code(REPO_ROOT / "scripts" / name)
            match = re.search(r"\$DailyAt\s*=\s*'([0-9]{2}):([0-9]{2})'", code)
            self.assertIsNotNone(match, f"{name} has no $DailyAt default")
            times[name] = int(match.group(1)) * 60 + int(match.group(2))

        self.assertGreater(
            times["install_publish_task.ps1"],
            times["install_runner_task.ps1"],
            "the publish would run before the Runner whose output it shows",
        )

    def test_the_logon_delays_stand_in_the_same_order(self):
        """The same relationship on the other trigger. Both fire at logon,
        and the publish's longer delay is what gives a startup catch-up run
        time to finish first -- best-effort by design (nothing depends on
        the order holding), but a publish delay shorter than the Runner's
        would be the intent inverted."""
        delays = {}
        for name in ("install_runner_task.ps1", "install_publish_task.ps1"):
            code = self._code(REPO_ROOT / "scripts" / name)
            match = re.search(r"\$DelayMinutes\s*=\s*([0-9]+)", code)
            self.assertIsNotNone(match, f"{name} has no $DelayMinutes default")
            delays[name] = int(match.group(1))

        self.assertGreater(
            delays["install_publish_task.ps1"], delays["install_runner_task.ps1"]
        )

    def test_every_installer_explains_a_refusal_before_suggesting_elevation(self):
        """The Agent installer's lesson: "this environment cannot register
        tasks at all" was recorded as the diagnosis and was false — every
        other trigger shape registered fine in the same session. Elevation
        is the last candidate."""
        for path in self._scripts():
            with self.subTest(installer=path.name):
                code = self._code(path)
                self.assertLess(
                    code.index("Get-Service Schedule"), code.index("elevated")
                )

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


class UnusedImportTests(unittest.TestCase):
    """No production file may import a name it does not use.

    This project has no linter — `test_repository_hygiene.py
    ::DependencyGuardTests` exists precisely because it ships with no
    third-party dependency, and that includes no flake8 — so nothing was
    checking. C135 ran the scan by hand and found five, three of which its own
    edits had just created by replacing the last caller of a `datetime`
    import. A change that removes the last use of an import leaves no trace
    anywhere else, which is exactly the kind of thing a gate is for.

    An unused import is not merely untidy here. `DependencyGuardTests` derives
    "what this project depends on" from the import statements, and
    `LayeringInvariantTests` derives the whole dependency graph the same way —
    so an import nothing uses is a false edge in both, and can make a layering
    violation that was really deleted look like one that is still there.

    Two things are deliberately not flagged, and each has a reason rather than
    an exception:

      * `from __future__ import annotations` binds no name and is used by the
        compiler, not by any expression;
      * a name re-exported for a caller outside this file. The allowlist below
        carries those, one line each with why, and it is asserted to be
        accurate rather than assumed — an entry that stops being needed fails
        this test as loudly as a new unused import does.
    """

    #: (path relative to the repository root, imported name) -> why it stays.
    ALLOWED = {
        ("src/app/runner.py", "_MAX_LOG_ERROR"): (
            "Re-exported for the tests that assert this Runner's log lines are "
            "bounded (test_runner_failure_paths.py reads it as "
            "`runner_module._MAX_LOG_ERROR`). The alias exists so the bound and "
            "the assertion cannot drift apart; the Runner itself passes the "
            "value through `oplog` rather than reading it."
        ),
    }

    def _production_files(self):
        files = sorted(SRC.rglob("*.py")) + sorted(REPO_ROOT.glob("*.py"))
        return [f for f in files if "__pycache__" not in f.parts]

    def _unused_in(self, path):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)

        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound[alias.asname or alias.name.split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    bound[alias.asname or alias.name] = node.lineno

        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        # A name can also be reached as a string: `__all__`, `getattr`, and
        # this project's own source-reading gates all do it. Quoted use counts.
        return {
            name: lineno
            for name, lineno in bound.items()
            if name not in used
            and f'"{name}"' not in text
            and f"'{name}'" not in text
        }

    def test_the_scan_finds_the_files_that_exist(self):
        """Guards the guard: an empty scan passes vacuously (C76 §4)."""
        names = {f.name for f in self._production_files()}
        self.assertIn("ops_status.py", names)
        self.assertIn("businessdate.py", names)
        self.assertGreater(len(names), 50)

    def test_the_scan_can_actually_detect_an_unused_import(self):
        """The predicate, on an input it must flag and one it must not."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.py"
            good.write_text("import os\nprint(os.sep)\n", encoding="utf-8")
            bad = Path(tmp) / "bad.py"
            bad.write_text("import os\nprint(1)\n", encoding="utf-8")

            self.assertEqual(self._unused_in(good), {})
            self.assertEqual(list(self._unused_in(bad)), ["os"])

    def test_no_production_file_imports_a_name_it_does_not_use(self):
        offenders = []
        for path in self._production_files():
            relative = path.relative_to(REPO_ROOT).as_posix()
            for name, lineno in sorted(self._unused_in(path).items()):
                if (relative, name) in self.ALLOWED:
                    continue
                offenders.append(f"{relative}:{lineno}: {name}")
        self.assertEqual(offenders, [])

    def test_every_allowlisted_import_is_still_unused(self):
        """An allowlist that outlives its reason is the failure mode this
        repository keeps finding (C76, C111, C114): a note about the code
        surviving the code. If one of these is used now, the entry is the
        thing to delete."""
        stale = []
        for (relative, name), _why in self.ALLOWED.items():
            path = REPO_ROOT / relative
            self.assertTrue(path.is_file(), f"allowlist names a missing file: {relative}")
            if name not in self._unused_in(path):
                stale.append(f"{relative}:{name}")
        self.assertEqual(stale, [], "these allowlist entries are no longer needed")

    def test_every_allowlisted_entry_says_why(self):
        for key, why in self.ALLOWED.items():
            with self.subTest(entry=key):
                self.assertGreater(len(why), 40, "an allowlist entry needs a reason")


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

    #: The root-level half of the sweep: `src/` is reached by `rglob` in
    #: `_read_variables()`, and these are the files it cannot see.
    #:
    #: **Derived since C135, and it was complete when it was derived** — this
    #: is a fence, not a repair. It had been wrong twice before and each
    #: correction was a line added by hand: `dashboard_server.py` (C80, "the
    #: one tool whose variable nothing checked against `.env.example`") and
    #: `publish_control_tower.py` (C105). Two corrections to one tuple is the
    #: signal; the third was never going to be caught by the same method.
    #:
    #: `src/review_cli.py` is deliberately not here and does not need to be:
    #: it is under `SRC`, so `_read_variables()` already reads it through the
    #: `rglob` half. Measured — it looks up no environment variable at all,
    #: and if it ever does, `test_every_variable_the_code_reads_is_documented`
    #: sees it through that half.
    @property
    def ENTRYPOINTS(self):
        return tuple(
            sorted(path.name for path in REPO_ROOT.glob("*.py") if path.is_file())
        )

    def test_the_entrypoint_roster_is_derived_and_complete(self):
        """Guards the guard. Several assertions in this class are negatives
        over this list, and the two tools it has historically been missing
        are named so that a derivation which silently narrows fails here."""
        found = self.ENTRYPOINTS

        self.assertGreaterEqual(len(found), 6, found)
        for name in (
            "run_company_ops.py",
            "run_agent.py",
            "init_notion.py",
            "ops_status.py",
            "dashboard_server.py",
            "publish_control_tower.py",
        ):
            with self.subTest(entrypoint=name):
                self.assertIn(name, found)

    def test_the_roster_does_not_restate_what_the_src_half_reads(self):
        """`_read_variables()` is `SRC.rglob()` plus this. An entry that is
        also under `src/` would be read twice and would move a variable's
        attribution; `src/review_cli.py` is the file that would do it."""
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                self.assertFalse(
                    (REPO_ROOT / name).resolve().is_relative_to(SRC.resolve()),
                    f"{name} is inside src/ and is already read by the "
                    "SRC.rglob() half",
                )

    def _declared_variables(self) -> set[str]:
        declared = set()
        for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            declared.add(stripped.split("=", 1)[0].strip())
        return declared

    #: How this project can read an environment variable.
    #:
    #: `os.getenv(...)` was missing until C58 and nothing noticed, because
    #: nothing in the tree happened to use it — a gate promising "every
    #: variable the code actually looks up" that recognised only one of the
    #: two ordinary spellings. Measured: an injected
    #: `os.getenv("COMPANY_OPS_UNDOCUMENTED")` passed this class cleanly
    #: while the same read written `os.environ.get(...)` failed it.
    #:
    #: `test_the_scanner_recognises_every_spelling` is the detector for the
    #: detector, and it is the point of naming the patterns here rather than
    #: burying them in the method: a pattern list nobody exercises is a gate
    #: whose coverage is assumed.
    READ_PATTERNS = (
        # os.environ.get("X") / os.environ["X"] / os.environ("X")
        r"""os\.environ(?:\.get)?\(?\[?\s*[\x22']([A-Z_][A-Z0-9_]*)[\x22']""",
        # os.getenv("X")
        r"""os\.getenv\(\s*[\x22']([A-Z_][A-Z0-9_]*)[\x22']""",
        # `from os import environ` then environ.get("X") / environ["X"]
        r"""(?<!\.)\benviron(?:\.get)?\(?\[?\s*[\x22']([A-Z_][A-Z0-9_]*)[\x22']""",
        # a Mapping passed in place of os.environ (agent/reporter do this)
        r"""source\.get\(\s*[\x22']([A-Z_][A-Z0-9_]*)[\x22']""",
        # NAME_ENV_VAR = "COMPANY_OPS_PROFILE" — the indirection
        # `reporter/profiles.py` uses, which is the only place the variable
        # is named at all.
        r"""^[A-Z_]*ENV_VAR\s*=\s*[\x22']([A-Z_][A-Z0-9_]*)[\x22']""",
    )

    @classmethod
    def _variables_in(cls, text: str) -> set:
        """The variables one source text reads. Split out of the file walk so
        the pattern list can be exercised directly — see
        `test_the_scanner_recognises_every_spelling`."""
        names: set = set()
        for pattern in cls.READ_PATTERNS:
            names |= {m.group(1) for m in re.finditer(pattern, text, re.M)}
        return names

    def _read_variables(self) -> dict[str, set[str]]:
        """Every environment variable the code actually looks up.

        Spellings covered are `READ_PATTERNS`; the indirection form is there
        because `reporter/profiles.py` names the variable only in a constant.
        """
        found: dict[str, set[str]] = {}
        sources = list(SRC.rglob("*.py")) + [REPO_ROOT / name for name in self.ENTRYPOINTS]
        for path in sources:
            if "__pycache__" in str(path) or not path.is_file():
                continue
            for name in self._variables_in(path.read_text(encoding="utf-8")):
                found.setdefault(name, set()).add(path.name)
        return found

    def test_the_scanner_recognises_every_spelling(self):
        """The detector for the detector.

        This class's verdict is only as wide as its pattern list, and a
        missing spelling is invisible: the gate reports green because it
        found nothing to complain about, not because there was nothing.
        Every form below is one a Python file in this project could
        plausibly use, and each is asserted rather than assumed.
        """
        for label, snippet in (
            ("os.environ.get", 'os.environ.get("COMPANY_OPS_PROBE")'),
            ("os.environ[]", 'os.environ["COMPANY_OPS_PROBE"]'),
            ("os.getenv", 'os.getenv("COMPANY_OPS_PROBE")'),
            ("os.getenv with default", 'os.getenv("COMPANY_OPS_PROBE", "x")'),
            ("bare environ.get", 'environ.get("COMPANY_OPS_PROBE")'),
            ("bare environ[]", 'environ["COMPANY_OPS_PROBE"]'),
            ("mapping source", 'source.get("COMPANY_OPS_PROBE")'),
            ("ENV_VAR constant", 'PROFILE_ENV_VAR = "COMPANY_OPS_PROBE"'),
        ):
            with self.subTest(spelling=label):
                self.assertIn(
                    "COMPANY_OPS_PROBE",
                    self._variables_in(snippet),
                    f"the scanner does not recognise {label} — a variable read "
                    "that way is undocumented and this class says nothing",
                )

    def test_the_scanner_does_not_invent_variables(self):
        """The other direction. A pattern loose enough to match ordinary code
        would fill this class with names nobody reads, and the first response
        to that noise is to weaken the gate."""
        for snippet in (
            'print("COMPANY_OPS_PROBE")',
            'path = "COMPANY_OPS_PROBE"',
            '# os.environ.get("COMPANY_OPS_PROBE") in a comment',
        ):
            with self.subTest(snippet=snippet):
                if snippet.lstrip().startswith("#"):
                    continue  # a commented read is still a read to a reviewer
                self.assertEqual(self._variables_in(snippet), set())

    def test_the_scan_still_finds_the_variables_this_project_has(self):
        """Non-emptiness, for C57's reason: a scan that returns nothing makes
        every assertion below it pass over an empty loop."""
        self.assertGreaterEqual(len(self._read_variables()), 3)

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
        every Notion tool has to name the two Notion variables.

        The set is spelled out rather than derived, which is the point: it
        fails when a tool starts or stops reading them, and a person then
        decides whether that was intended. C105 added the third member —
        `publish_control_tower.py`, which reads the same two and declares
        none of its own, because where its page goes is discovered from
        PROJECTS rather than pasted into a file.
        """
        advertised = self._advertised_variables()

        for name in ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID"):
            with self.subTest(variable=name):
                self.assertEqual(
                    advertised.get(name),
                    {
                        "init_notion.py",
                        "run_company_ops.py",
                        "publish_control_tower.py",
                    },
                )
        self.assertIn("run_company_ops.py", advertised["COMPANY_OPS_HISTORY_START_DATE"])
        self.assertIn("run_agent.py", advertised["COMPANY_OPS_PROFILE"])

    def test_no_entrypoint_silently_loads_a_dotenv_file(self):
        """`.env.example` states that nothing auto-loads it. If that ever
        changes, the template's own instructions become wrong.

        **What "loads" means, made precise (C90).** The original form of this
        test banned the *string* `".env"` from every entrypoint. That is a
        proxy for the property, and it turned out to forbid something the
        property permits: `ops_status.py` now reports **that** `.env` holds
        Notion credentials this process was never given, which is the
        opposite of loading them — it names the two variables, uses neither
        value, and tells the operator the template's own sentence ("`.env`는
        자동으로 읽히지 않는다 … 셸에서 export"). It makes the template's
        instruction *more* likely to be followed, not wrong.

        Why that report exists is measured, not hypothetical: on this
        deployment `.env` held a token and database id that worked — they
        wrote four rows into the live PROJECTS database — while every run
        printed `notion_sync: SKIPPED (미설정)` and the Notion projection sat
        **15 days** behind the Control Tower. "Not configured" and
        "configured but not exported" need opposite reactions.

        So the check is now the property itself, and it is stricter than the
        string ban in the direction that matters:

            no dotenv library, anywhere        as before
            nothing writes to `os.environ`     new — this is what "load"
                                               actually is, and the old test
                                               never looked for it
            a file that mentions `.env` must
            also state that it is not loaded   so the template cannot be
                                               contradicted in silence
        """
        import ast

        for name in self.ENTRYPOINTS:
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(entrypoint=name):
                self.assertNotIn("dotenv", text.lower())

                # Loading is assignment into the environment. Nothing here
                # may do it, whether or not `.env` is involved.
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if (
                                isinstance(target, ast.Subscript)
                                and isinstance(target.value, ast.Attribute)
                                and target.value.attr == "environ"
                            ):
                                self.fail(f"{name} assigns into os.environ")
                    if isinstance(node, ast.Call):
                        func = node.func
                        if (
                            isinstance(func, ast.Attribute)
                            and func.attr in {"update", "setdefault"}
                            and isinstance(func.value, ast.Attribute)
                            and func.value.attr == "environ"
                        ):
                            self.fail(f"{name} mutates os.environ")

                if '".env"' in text:
                    self.assertIn(
                        "자동으로 읽히지 않는다",
                        text,
                        f"{name} mentions .env without saying it is not "
                        "auto-loaded — the template's instruction must not be "
                        "contradicted in silence",
                    )

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

    Every entrypoint already reconfigures stdout for UTF-8 (a separate
    defect, also operator-facing); line buffering belongs on the same call.

    **C80: this class covered three of the five.** Its roster was a
    hand-written tuple of the tools at the repository root that the Sprint
    happened to be looking at, and `init_notion.py` and `src/review_cli.py`
    were outside it — with the fix genuinely absent from both, not merely
    unchecked. `init_notion.py` reaches the mixed-stream case for real:

        print(f"Health Check: PASS (database_id=...)")            -> stdout
        ...bootstrap_database() raises NotionAPIError...
        print(f"[FAILED] Notion API error: ...", file=sys.stderr)  -> stderr

    Measured with that file's own prologue, captured as `> log 2>&1`:

        0: [FAILED] Notion API error: 429 rate limited
        1: Health Check: PASS (database_id=abc)

    the failure above the line it follows, which is this class's whole
    subject. The roster is now `_entrypoint_files()`.
    """

    @property
    def ENTRYPOINTS(self):
        return _entrypoint_files()

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

    def test_every_entrypoint_reconfigures_stderr_too(self):
        """The other half of the same call, and the reason it is asserted
        here rather than only in `EncodingSafetyTests`: that class names
        three files by hand, which is the shape that produced this Sprint.
        Both halves, one derived roster, every tool.
        """
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertIn('sys.stderr.reconfigure(encoding="utf-8")', text)

    def test_the_roster_covers_every_tool_and_no_module(self):
        """Guards the guard (C80). Both tests above loop over it, so an empty
        or shrunken derivation would leave them passing over nothing."""
        found = set(self.ENTRYPOINTS)

        self.assertEqual(
            found,
            {
                "run_company_ops.py",
                "run_agent.py",
                "init_notion.py",
                "ops_status.py",
                "dashboard_server.py",
                "publish_control_tower.py",
                "src/review_cli.py",
            },
        )
        for library in ("src/cli.py", "src/oplog.py", "src/runsummary.py"):
            with self.subTest(module=library):
                self.assertNotIn(library, found)

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
        # C138. The only block not derived from this system's own
        # files, and therefore the only one that can see a scheduled
        # task which never started the process — the failure that
        # leaves no evidence under `runtime/` at all.
        "SCHEDULE",
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


class StrayShellArtifactsInTheRepositoryRootTests(unittest.TestCase):
    """No extension-less stray file is committed at the repository root.

    **This was a characterization until C140, and it named the day it would
    end.** Five empty, extension-less files were committed at the root, every
    one of them the name of a command:

        FETCH_HEAD  cd  claude  git  main

    All five were zero bytes and all five entered in one commit (43771a9) --
    the signature of a shell redirection that went to a file instead of to a
    program (`git ... > main`, `> FETCH_HEAD`) on a project driven from
    several Windows Desktops.

    Nothing broke: they carried no content, no guard read them (none has a
    text suffix), and `PATHEXT` means an extension-less `git` in the working
    directory is not executable on Windows. What they cost was legibility --
    `git`, `cd` and `main` sitting beside `src/` and `docs/` read as though
    they mean something, and they shipped to every clone.

    The old docstring recorded why they survived: *"Not deleted, and the
    reason is the session's Git policy rather than a judgement about the
    files ... the day someone does delete these the test fails too and this
    record has to go with them."* C140 deleted them, this test failed
    exactly as promised, and the record goes with them.

    What replaces it is the guarantee rather than nothing, because the
    failure mode is still live: a mistyped redirection in this repository's
    root is one keystroke away, and it is invisible in review precisely
    because an empty extension-less file looks like nothing.
    """

    def _stray_root_files(self):
        return {
            path.name
            for path in _tracked_files()
            if path.parent == REPO_ROOT
            and path.is_file()
            and not path.suffix
            and not path.name.startswith(".")
        }

    def test_no_stray_artifact_is_committed_at_the_root(self):
        self.assertEqual(
            self._stray_root_files(),
            set(),
            "an extension-less file is tracked at the repository root -- "
            "almost always a shell redirection that missed its program "
            "(`git ... > main`). If one is genuinely intended, it needs a "
            "name that says so and an entry here saying why.",
        )

    def test_the_scan_would_see_one(self):
        """Guards the guard: the assertion above is a negative over a scan,
        and a scan that stopped matching would satisfy it for free. The five
        this class was written for are the fixture -- they are gone from the
        tree, so the check is made against the predicate directly."""
        for name in ("FETCH_HEAD", "cd", "claude", "git", "main", "notes"):
            with self.subTest(name=name):
                candidate = REPO_ROOT / name
                self.assertFalse(candidate.suffix)
                self.assertFalse(candidate.name.startswith("."))
        # ...and the things that legitimately live there are not swept up.
        for name in ("README.md", "run_company_ops.py", "ops_status.py"):
            with self.subTest(name=name):
                self.assertTrue((REPO_ROOT / name).suffix)

    def test_the_deleted_artifacts_are_really_gone(self):
        """The other direction, so this class cannot pass while the files it
        describes are still on disk under a name the scan misses."""
        for name in ("FETCH_HEAD", "cd", "claude", "git", "main"):
            with self.subTest(name=name):
                self.assertFalse((REPO_ROOT / name).exists())


class NoTestTakesADefaultThatPointsAtTheLiveTreeTests(unittest.TestCase):
    """Every parameter whose default is under `runtime/`, checked at every
    call site in `tests/` (C126).

    C123 found this one instance at a time: two tests built an
    `OneDriveTransport` without `outgoing_dir`, so `send()` staged a
    fabricated Event into the operator's own `runtime/events/outgoing/` on
    every full run. The fix there was a gate for that one class — which is
    the roster shape, and there are **eleven** such callables.

    Discovered by introspection rather than listed, so a twelfth is covered
    the day it is written:

        agent.agent.run_once            lock_path log_path outbox_dir
                                        rejected_signals_dir sent_dir
                                        signals_dir state_path
        agent.status.read_status        (five)
        collector.runtime.run_once      incoming_dir log_path processed_dir
                                        rejected_dir
        collector.state.PersistentSeenEventStore   state_path
        controltower.rollup.build_company_rollup   processed_dir
        history.file_repository.FileHistoryRepository  keep_dir review_dir
        monthly.generator.mark_month_dirty         monthly_dir
        monthly.generator.run_once                 state_path
        scheduler.scheduler.run_once               lock_path state_path
        transport.intake.run_intake     incoming_dir processed_dir
                                        rejected_dir transport_dir
        transport.onedrive.OneDriveTransport       outgoing_dir

    **The reads are the reason this is not only about pollution.** C123's
    instances wrote; the ones this found *read*, which is worse in one
    specific way — the test's answer then depends on what the operator did
    yesterday. Measured, on the two `read_status()` helpers this caught:

        signals_dir omitted, watermark past the dates on disk
            undelivered_closed_signal_count -> **5**
            (the operator's real Signal files, in runtime/agent/signals/)
        signals_dir given
            undelivered_closed_signal_count -> 0

    Both call sites passed every other directory explicitly and missed the
    one with a default. They were green only because their fixtures' dates
    happen to sit before the live ones.

    Two shapes are deliberately **not** offenders, and both were false
    positives in the first draft:

        build_company_rollup(events=..., now=...)   `events` replaces the
                                                   directory read entirely
        Store(root / "state.json")                 positional arguments
                                                   cover the leading
                                                   parameters
    """

    #: `processed_dir` is not read when the caller supplies the events.
    SUBSTITUTES = {"processed_dir": "events"}

    #: The one call that takes a default **on purpose**, with the reason.
    #:
    #: `test_the_documented_one_liner_builds_a_model` runs the command
    #: docs/13 §3-⑨ gives an operator, exactly as that operator would type
    #: it — on the default directory. Passing `processed_dir` would test a
    #: different command than the one the document publishes.
    #:
    #: Safe because it is read-only and its assertions do not depend on what
    #: it reads: it checks that every panel has a `source` or a `note`, which
    #: holds for an empty tree and a full one alike. That is also the
    #: property the test exists for — the docstring says "it must work on a
    #: machine with no evidence at all".
    #:
    #: Keyed by `(file, callable)` rather than by line, because a line number
    #: goes stale on the next edit above it — the drift C92 recorded when
    #: two of them moved twice in one Sprint.
    ALLOWED = {
        ("test_controltower_dashboard.py", "controltower.rollup.build_company_rollup"),
    }

    def _runtime_defaulting(self):
        """`{qualified name: [parameter, ...]}`, from the real signatures.

        **Qualified, and that is not tidiness.** Five modules export a
        `run_once` — agent, collector, scheduler, monthly and app.runner —
        with different parameters, and the first draft of this class keyed on
        the bare name. It reported nineteen offenders, of which fifteen were
        `test_collector_runtime.py` calling the *collector's* `run_once`
        against the *scheduler's* parameter list. A check that names healthy
        code is a check that gets switched off.
        """
        import importlib
        import inspect
        import pkgutil

        runtime = REPO_ROOT / "runtime"

        def under_runtime(value):
            return isinstance(value, Path) and (
                value == runtime or runtime in value.parents
            )

        found = {}
        for module_info in pkgutil.walk_packages([str(REPO_ROOT / "src")]):
            try:
                module = importlib.import_module(module_info.name)
            except Exception:  # noqa: BLE001 — a module that will not import
                continue       # is another test's problem, not this one's
            constants = {
                name
                for name, value in vars(module).items()
                if name.startswith("DEFAULT_") and under_runtime(value)
            }
            for name, obj in vars(module).items():
                if not (inspect.isclass(obj) or inspect.isfunction(obj)):
                    continue
                if getattr(obj, "__module__", None) != module_info.name:
                    continue
                try:
                    signature = inspect.signature(obj)
                except (TypeError, ValueError):
                    continue
                risky = [
                    parameter.name
                    for parameter in signature.parameters.values()
                    if under_runtime(parameter.default)
                    # The other spelling: `x_dir=None` with
                    # `x_dir if x_dir is not None else DEFAULT_X_DIR` inside.
                    or (
                        parameter.default is None
                        and f"DEFAULT_{parameter.name.upper()}" in constants
                    )
                ]
                if risky:
                    found[f"{module_info.name}.{name}"] = sorted(risky)
        return found

    @staticmethod
    def _direct_imports(scope):
        """`ImportFrom` statements belonging to this scope.

        **Does not descend into a nested `def` (C126).** `ast.walk()` yields
        every descendant, so a module-level sweep written with it collects
        imports from inside every test method in the file — which is how the
        first draft decided that `test_observability.py` binds `run_once` to
        `agent.agent`, on the strength of one `from agent.agent import
        run_once` twelve hundred lines below the call it then misjudged.

        This and `_bindings()`'s unbind rule are **two independent fixes for
        that, either of which is sufficient** — measured: restoring the
        descent leaves the sweep correct, because the unbind then answers.
        Recorded rather than one of them deleted: they fail differently. The
        unbind needs an import to be *present and untracked*; not descending
        needs nothing at all, and is what keeps `_bindings()` describing a
        scope rather than a file.
        `test_the_resolver_reads_the_import_that_is_in_scope` drives both,
        and the redundancy is measured rather than asserted: breaking either
        one alone leaves every test here green, and breaking **both together**
        turns the sample's `app.runner.run_once` call back into a false
        offender. That is what "either is sufficient" means, stated as the
        mutation that shows it.
        """
        found = []
        stack = list(getattr(scope, "body", []))
        while stack:
            statement = stack.pop()
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(statement, ast.ImportFrom):
                found.append(statement)
            for field in ("body", "orelse", "finalbody"):
                stack.extend(getattr(statement, field, None) or [])
            for handler in getattr(statement, "handlers", None) or []:
                stack.extend(handler.body)
        return found

    def _bindings(self, scope, qualified):
        """`{name as spelled here: qualified name}` for one scope."""
        by_leaf = {}
        for full in qualified:
            by_leaf.setdefault(full.rsplit(".", 1)[1], []).append(full)

        names = {}
        for statement in self._direct_imports(scope):
            if not statement.module:
                continue
            for alias in statement.names:
                local = alias.asname or alias.name
                candidate = f"{statement.module}.{alias.name}"
                if candidate in qualified:
                    names[local] = candidate
                elif alias.name in by_leaf and len(by_leaf[alias.name]) == 1:
                    # A package re-export (`from scheduler import run_once`),
                    # unambiguous only when one module defines that leaf.
                    names[local] = by_leaf[alias.name][0]
                else:
                    # Imported from somewhere this sweep does not track, or
                    # ambiguous. **Unbind**, so an outer scope's binding for
                    # the same name cannot answer for this call:
                    # `app.runner.run_once` takes no runtime default at all,
                    # and the honest verdict for its calls is "not mine".
                    names[local] = None
        return names

    def _resolved_calls(self, tree, qualified):
        """`(call node, qualified name)` for every call this sweep can name.

        Scope-aware, innermost wins. A file that imports one `run_once` in
        one test method and a different one in another resolves each call
        against the import that is actually in scope for it.
        """
        parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[child] = node

        def chain(node):
            """This node's enclosing scopes, outermost first."""
            scopes, current = [], node
            while current is not None:
                if isinstance(current, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
                    scopes.append(current)
                current = parent.get(current)
            return list(reversed(scopes))

        cache = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            names = {}
            for scope in chain(node):
                if id(scope) not in cache:
                    cache[id(scope)] = self._bindings(scope, qualified)
                names.update(cache[id(scope)])
            resolved = names.get(node.func.id)
            if resolved is not None:
                yield node, resolved

    def test_the_introspection_finds_the_callables_we_know_about(self):
        """Guards the guard. An import failure or a rename would leave the
        sweep below scanning for nothing, which is the vacuous shape C76 §4
        swept a whole repository for."""
        found = self._runtime_defaulting()

        self.assertGreaterEqual(len(found), 8, f"only found {sorted(found)}")
        self.assertIn("outgoing_dir", found["transport.onedrive.OneDriveTransport"])
        self.assertIn("signals_dir", found["agent.status.read_status"])
        # The name that made qualification necessary: more than one module
        # defines it, with different parameters.
        run_onces = [name for name in found if name.endswith(".run_once")]
        self.assertGreater(len(run_onces), 1, run_onces)

    def test_no_test_leaves_such_a_parameter_to_its_default(self):
        risky = self._runtime_defaulting()
        offenders = []
        allowed_seen = set()
        for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            # A call inside a method is resolved with that method's own
            # imports; the deepest scope wins, so the last verdict for a
            # given line is the right one.
            for node, qualified in self._resolved_calls(tree, risky):
                given = {keyword.arg for keyword in node.keywords}
                positional = len(node.args)
                missing = [
                    parameter
                    for index, parameter in enumerate(risky[qualified])
                    if parameter not in given
                    and self.SUBSTITUTES.get(parameter) not in given
                    and index >= positional
                ]
                if missing and (path.name, qualified) not in self.ALLOWED:
                    offenders.append(
                        f"{path.name}:{node.lineno} {qualified}() left "
                        f"{missing} to a default under runtime/"
                    )
                if missing and (path.name, qualified) in self.ALLOWED:
                    allowed_seen.add((path.name, qualified))

        self.assertEqual(
            offenders,
            [],
            "a test let a parameter fall back to this repository's live "
            "runtime/ tree. If it writes, it leaves residue in the "
            "operator's directories; if it reads, the test's answer depends "
            "on what the operator did yesterday:\n  " + "\n  ".join(offenders),
        )

        # An allowance that stops matching anything is a permission nobody
        # needs, still granted. C88's shape: the roster outlived its subject
        # and kept advertising a to-do that was already done.
        self.assertEqual(
            allowed_seen,
            self.ALLOWED,
            "an entry in ALLOWED no longer matches any call — delete it "
            f"(unmatched: {sorted(self.ALLOWED - allowed_seen)})",
        )

    #: Two methods, two different `run_once` imports, and a third with none
    #: — the shape `test_observability.py` actually has. Written out here so
    #: the resolver is checked against a case whose right answer is obvious,
    #: rather than against whatever the real tree happens to contain.
    RESOLVER_SAMPLE = """
from agent.status import read_status

class T:
    def test_a(self):
        from collector.runtime import run_once
        run_once(incoming_dir=x)

    def test_b(self):
        from app.runner import run_once
        run_once(local_master_dir=x)

    def test_c(self):
        read_status(state_path=p)

    def test_d(self):
        from agent.agent import run_once
        run_once()
"""

    def test_the_resolver_reads_the_import_that_is_in_scope(self):
        """The contract both mechanisms in `_direct_imports()` exist for.

        `test_b` calls `app.runner.run_once`, which takes no runtime default
        at all — naming it would be reporting a healthy test, and the first
        draft did exactly that for three of them.
        """
        risky = self._runtime_defaulting()
        tree = ast.parse(self.RESOLVER_SAMPLE)

        resolved = {
            node.lineno: qualified
            for node, qualified in self._resolved_calls(tree, risky)
        }

        self.assertEqual(
            sorted(resolved.values()),
            [
                "agent.agent.run_once",
                "agent.status.read_status",
                "collector.runtime.run_once",
            ],
            f"resolved: {resolved}",
        )
        # and specifically: the untracked import is not answered for by the
        # tracked one three methods above it
        self.assertNotIn("app.runner.run_once", resolved.values())

    def test_the_sweep_would_notice_one(self):
        """Guards the guard, by feeding it the defect — the real tree is
        clean, so a sweep that had stopped working looks identical to one
        that works."""
        risky = {"transport.onedrive.OneDriveTransport": ["outgoing_dir"]}
        local = {"OneDriveTransport": "transport.onedrive.OneDriveTransport"}
        self.assertTrue(local)
        samples = {
            "OneDriveTransport(sync_folder=t)": 1,
            "OneDriveTransport(sync_folder=t, outgoing_dir=o)": 0,
            "OneDriveTransport(t, o)": 0,
        }
        for source, expected in samples.items():
            with self.subTest(call=source):
                node = ast.parse(source).body[0].value
                given = {keyword.arg for keyword in node.keywords}
                missing = [
                    parameter
                    for index, parameter in enumerate(risky[local[node.func.id]])
                    if parameter not in given
                    and self.SUBSTITUTES.get(parameter) not in given
                    and index >= len(node.args)
                ]
                self.assertEqual(len(missing), expected)


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
        #
        # `to_payload` left this list in C51. It is what
        # `controltower/notion_projection.project_panels()` builds every
        # Notion row out of, deliberately — reading `DashboardRow.values`
        # directly would have made the projection a second redaction
        # boundary, and `_UNAUTHORED_KEYS` exists because the first draft of
        # that list leaked a secret-shaped `project_id` through a row key.
        # The Control Tower's hand-off contract finally has a consumer.
        "sync_control_tower",             # C51: writes the five CT_* rows.
                                          # Unwired for the reason
                                          # `bootstrap_dashboard_databases`
                                          # above is, plus one more: docs/14
                                          # §1 fixes the Operational
                                          # Projection as `Notion (PROJECTS /
                                          # OPS_RUNS)`, so these databases are
                                          # out of contract until that table
                                          # is widened — a spec decision, not
                                          # a coding one.
                                          # `ControlTowerDatabasesAreNot
                                          # ContractedYetTests` pins the gap
                                          # and fails the day it closes.
                                          # Everything below the write —
                                          # schema, mapping, payload,
                                          # validation — is exercised end to
                                          # end against the in-memory
                                          # transport, so what waits is the
                                          # sink and the sanction, never the
                                          # shape.
        # --- convenience accessors nothing needed yet ---------------------
        # `component` left this list in C46: `ops_status._same_instant_skips_
        # from_the_last_run()` needs one named component's metrics out of the
        # manifest, which is exactly the lookup it was written for.
        "read_event_json",                # local_output read seam
        # --- dispatched by name, never called from this source ------------
        #
        # `http.server` resolves a request method to `do_<VERB>` with
        # `getattr()`, and calls `log_message()` from inside
        # `BaseHTTPRequestHandler`. There is no call site in this repository
        # to find and there cannot be one: naming them here is what the
        # detector can see. They are the opposite of dead — every request to
        # `dashboard_server.py` enters through them — so they are recorded
        # under their own heading rather than filed beside the entries that
        # are waiting on a decision.
        "do_GET",
        "log_message",
        # `rank()` joined for the same structural reason, not the same
        # framework one (C129). `controltower/notion_page.py` passes it as
        # `sorted(attention, key=_attention_rank)` — a bare name on the right
        # of a keyword argument, which is neither a Call nor an Attribute, so
        # this scanner cannot see it for exactly the reason the `do_POST`
        # note below records. It is called on every ATTENTION line the Notion
        # page renders.
        #
        # Wrapping the call site in a lambda purely to make it visible was
        # the alternative and was rejected: contorting production code to
        # suit a detector is how the detector stops describing the code.
        "rank",
        # `do_POST` left this list in C115, and the direction is worth
        # recording: it is not that the detector got weaker, it is that the
        # code got more visible. The refusal used to be a hand-written roster
        # (`do_PUT = do_DELETE = do_PATCH = do_POST`), and a bare name on the
        # right of an assignment is neither a Call nor an Attribute, so this
        # scanner could not see it. `__getattr__` now returns `self.do_POST`,
        # which *is* an attribute access — so the one method that every
        # non-GET request reaches is now reachable by grep too.
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

    def test_the_structural_scan_finds_the_writers(self):
        """Guard against the guard silently matching nothing.

        `test_the_idiom_is_present_in_every_atomic_writer` asserts a **negative** over this scan — "nothing in the tree
        does X" — and a negative over an empty set is true. Measured (C66):
        with tree discovery neutered, it passed while checking nothing.

        The trigger is ordinary rather than exotic, and this repository
        already names it: `TheScansThisFileTrustsAreNotEmptyTests` was
        written when `git ls-files` came back empty outside a checkout. A
        renamed or moved `src/` does the same thing to `rglob`, and this
        project is deliberately worked on from several machines
        (AGENT.md §1).
        """
        import ast

        writers = []
        for path in (REPO_ROOT / "src").glob("**/*.py"):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if any(
                    getattr(inner.func, "attr", None) == "mkstemp"
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Call)
                ):
                    writers.append(f"{path.name}:{node.name}")

        self.assertGreaterEqual(len(writers), 12, writers)

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


class TheRecordedRuntimeIsTheRunningOneTests(unittest.TestCase):
    """BACKLOG section D declares the interpreter its numbers were taken on.
    This checks that declaration against the interpreter actually running.

    **Why a gate for a header (C70).** The header said "이 머신, Python 3.13,
    Windows 11" while the machine was Python 3.9.7 on Windows 10. C17 recorded
    that same drift once and fixed only the instance, so it came back; the
    gate built afterwards — `EveryTrackedModuleParsesOnThisInterpreterTests`
    — covers the **syntax** consequence (a PEP 604 `|` evaluated at runtime)
    and nothing else.

    **It fired for real in C76**, in the other direction: the deployment
    machine moved to Python 3.13.14 on Windows 11 and this test went red on a
    tree with nothing wrong with it. That is the gate working. Updating the
    line is half the fix; the other half is the capability re-check the
    failure message asks for, and BACKLOG D now carries it — `isjunction`
    (blind then, live now), `sys.stdlib_module_names`, and
    `fromisoformat('...Z')`, which flipped A-24 on this machine.

    The face it does not cover is **capability**, and that is what C70 walked
    into. `os.path.isjunction()` is 3.12+. A reader asking "is the junction
    exposure detector live on this deployment?" consults section D, reads
    3.13, and concludes yes. It had never fired here. A stale environment
    record is what kept a security detector's blindness invisible — so the
    record is a contract, not a caption.

    Major.minor only. The patch level moves without changing what any
    interpreter can do, and pinning it would make this fail for a reason
    nobody needs to act on.
    """

    #: The one line section D publishes for this purpose. Historical figures
    #: elsewhere in that section legitimately name other interpreters (they
    #: were measured there), so the check reads this marker rather than
    #: searching the prose — a scan would trip over its own history.
    MARKER = re.compile(r"<!--\s*RUNTIME-DECLARATION:\s*Python\s+(\d+)\.(\d+)\s*-->")

    def _declared(self):
        backlog = (REPO_ROOT / "BACKLOG.md").read_text(encoding="utf-8")
        found = self.MARKER.findall(backlog)
        self.assertEqual(
            len(found), 1,
            "BACKLOG.md must carry exactly one RUNTIME-DECLARATION marker; "
            f"found {len(found)}",
        )
        return tuple(int(part) for part in found[0])

    def test_the_declared_runtime_is_the_one_running(self):
        declared = self._declared()
        actual = (sys.version_info.major, sys.version_info.minor)

        self.assertEqual(
            declared,
            actual,
            "BACKLOG section D declares Python "
            f"{declared[0]}.{declared[1]} but this interpreter is "
            f"{actual[0]}.{actual[1]}. Update the RUNTIME-DECLARATION line "
            "(and re-check anything that branches on interpreter capability "
            "— C70's junction detector is the worked example).",
        )

    def test_the_marker_would_notice_a_wrong_version(self):
        """Guards the guard: the pattern has to be able to disagree."""
        sample = "<!-- RUNTIME-DECLARATION: Python 2.7 -->"
        self.assertEqual(self.MARKER.findall(sample), [("2", "7")])
        self.assertNotEqual(
            (2, 7), (sys.version_info.major, sys.version_info.minor)
        )

    def test_the_capability_that_made_this_a_contract_is_stated(self):
        """The declaration is only useful while a reader can tell what it
        implies. C70's case is named in section D on purpose; without it the
        line reads as trivia and the next drift is silent again."""
        backlog = (REPO_ROOT / "BACKLOG.md").read_text(encoding="utf-8")
        head = backlog[backlog.index("## D. 측정값"):][:3000]

        self.assertIn("isjunction", head)
        self.assertIn("capability", head)


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

    @property
    def ENTRYPOINTS(self):
        """Every operator-facing tool, read off disk.

        This was a hand-written tuple of the four at the repository root,
        and it missed `src/review_cli.py` — which is an entrypoint by every
        other measure this repository applies: it has a `__main__` guard, it
        is one of the CLI entry points `EncodingSafetyTests` names, and
        AGENT.md tells an operator to run it. Measured before the fix (C79):

            python src/review_cli.py --help

        printed a real KEEP Candidate out of the live `history_candidates/`
        and stopped at the edit prompt, waiting. The argument was not
        rejected, not warned about, not read — the exact defect this class
        was written for, on the one tool its roster could not see.

        C80 moved the derivation to `_entrypoint_files()`, because a second
        class in this file had the same tuple and the same kind of gap.
        """
        return _entrypoint_files()

    def test_the_roster_is_derived_and_finds_every_tool(self):
        """Guards the guard, both ways (C79).

        Every test below loops over `ENTRYPOINTS`, so a derivation that came
        back empty — or that quietly stopped seeing one file — would leave
        them all passing over nothing. And a derivation that swept in a
        library module would fail confusingly somewhere else.
        """
        found = set(self.ENTRYPOINTS)

        self.assertEqual(
            found,
            {
                "run_company_ops.py",
                "run_agent.py",
                "init_notion.py",
                "ops_status.py",
                "dashboard_server.py",
                "publish_control_tower.py",
                "src/review_cli.py",
            },
        )
        for library in ("src/cli.py", "src/oplog.py", "src/runsummary.py"):
            with self.subTest(module=library):
                self.assertNotIn(library, found)

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

        naming = 0
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                source = (REPO_ROOT / name).read_text(encoding="utf-8")
                message = self._run(name, "--dry-run").stderr
                named = set(
                    re.findall(r"(?:COMPANY_OPS|NOTION)_[A-Z0-9_]+", message)
                )

                if "configured_by=()" in source:
                    # C79: `src/review_cli.py` reads no environment variable
                    # at all — `FileHistoryRepository()` uses its own
                    # defaults. Naming one anyway would be precisely the
                    # failure this test exists to stop, from the other
                    # direction: sending an operator to set something nothing
                    # looks at. `unexpected_arguments()` already spells this
                    # case, so the check is that the tool uses it rather than
                    # borrowing a neighbour's list.
                    self.assertEqual(
                        named, set(),
                        f"{name} declares no configuration but names one",
                    )
                    self.assertIn("(없음)", message)
                else:
                    naming += 1
                    self.assertTrue(named, f"{name} names no variable at all")

                self.assertEqual(
                    named - known,
                    set(),
                    f"{name} tells an operator to set a variable nothing reads",
                )

        self.assertGreaterEqual(
            naming, 3,
            "no entrypoint named a variable — the subset check above just "
            "compared two empty sets",
        )

    def test_the_file_the_refusal_sends_them_to_names_the_tool(self):
        """Every refusal ends with "AGENT.md를 보세요". That sentence is the
        whole exit an operator gets, and it is only an exit if AGENT.md
        actually says something about the tool they typed.

        Measured on `dashboard_server.py` (C79): it printed the refusal, and
        AGENT.md — the file the refusal names — did not contain the string
        `dashboard_server` anywhere. The operator is told where to look and
        the place they look does not answer, which is worse than being told
        nothing: `.env.example` had it, so the one document that did know was
        not the one the message pointed at.

        The check is deliberately weak — the tool's name appears somewhere —
        because a stronger one would be prescribing prose. A name that is
        present can be found with Ctrl-F; a name that is absent cannot.
        """
        text = (REPO_ROOT / "AGENT.md").read_text(encoding="utf-8")

        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                tool = Path(name).name
                message = self._run(name, "--dry-run").stderr

                # The premise: this tool really does send them to AGENT.md.
                self.assertIn("AGENT.md", message)
                self.assertIn(
                    tool,
                    text,
                    f"{tool}'s refusal sends an operator to AGENT.md, which "
                    f"never mentions {tool}",
                )

    def test_the_rule_lives_in_one_place(self):
        """One copy per entrypoint of "reject unknown arguments" is one
        chance each for one of them to keep running. Each imports the shared
        helper rather than restating the check.

        The count is not written down here on purpose (C66's rule about
        numbers in prose): `ENTRYPOINTS` is derived, so this covers whatever
        is on disk.
        """
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


class TheShippingCodeUsesNothingNewerThanTheTestsDoTests(unittest.TestCase):
    """C135, Release audit. A-25 records that nothing in this repository
    declares a supported Python version. Choosing one is a decision and stays
    in A-25; **measuring what the code actually requires** is not, and this is
    that measurement, kept as a test so it cannot quietly stop being true.

    Why it matters here specifically: this project is deliberately run from
    four machines (AGENT.md §1), each with its own interpreter, and
    `EveryTrackedModuleParsesOnThisInterpreterTests` only ever checks the one
    the suite happens to be running on. A construct that parses here and not
    on Desktop 2 is a Desktop that silently stops reporting — and C50 records
    that exact shape, where one 3.10-only annotation stopped the whole suite.

    That class states the gap and then leaves it open, in these words:

        "Measured in C76 … across all 139 tracked `.py` files, **zero**
         post-3.9 AST constructs. Nothing has drifted; **nothing would
         notice if it did.**"

    This is what notices. It does not close the part that genuinely needs a
    decision — *which* interpreter is supported, A-25 — because it declares
    no version at all. It compares the repository against itself: the two
    halves have different floors today, the shipping half is the lower one,
    and that ordering is the thing a mixed-version fleet depends on.

    Measured (C135, 87 files):

        src/ + the six entrypoints    0 constructs above the 3.8 baseline
        tests/                        3.9  (ast.unparse x18, removesuffix x2)

    So the shipping code asks for **less** than the tests do, and that is the
    property asserted: whatever floor the suite establishes, `src/` may not
    raise it. A test-only helper reaching for something newer is fine and
    stays fine; the same reach inside `src/` is a machine that cannot run.

    **This decides nothing.** It does not say 3.8 is supported, or that 3.13
    is. It says the two halves of the repository have not drifted apart, and
    it will fail loudly the day someone puts a `match` statement in
    `src/`, which is when the A-25 decision actually has to be made.

    Not covered here, because it is not syntax: `datetime.fromisoformat()`
    accepts a trailing `Z` from 3.11 and refuses it before (A-24). That is a
    *behavioural* split across versions and the reason A-25 is filed as a
    multi-machine contract rather than a packaging chore.
    """

    #: Construct -> the version that introduced it. Hand-written, and
    #: `test_the_detector_finds_a_construct_it_is_meant_to_find` exists
    #: because a roster nobody probes is how this repository has been wrong
    #: three times (C79, C80, C81).
    NEWER_THAN_BASELINE = {
        "unparse": "ast.unparse (3.9)",
        "removeprefix": "str.removeprefix (3.9)",
        "removesuffix": "str.removesuffix (3.9)",
        "zoneinfo": "zoneinfo (3.9)",
        "pairwise": "itertools.pairwise (3.10)",
        "aiter": "aiter() (3.10)",
        "anext": "anext() (3.10)",
        "tomllib": "tomllib (3.11)",
        "ExceptionGroup": "ExceptionGroup (3.11)",
        "Self": "typing.Self (3.11)",
        "batched": "itertools.batched (3.12)",
        "override": "typing.override (3.12)",
    }

    def _shipping_files(self):
        files = sorted(SRC.rglob("*.py")) + sorted(REPO_ROOT.glob("*.py"))
        return [f for f in files if "__pycache__" not in f.parts]

    def _newer_constructs_in(self, source, label):
        """`[(line, why)]` for constructs above the baseline.

        Annotations are not inspected: every module here carries
        `from __future__ import annotations`, so `int | None` in an
        annotation is a string at runtime and costs nothing. A `X | Y` that
        is *evaluated* — inside `isinstance()` — is a different matter and is
        caught below.
        """
        found = []
        tree = ast.parse(source, filename=label)
        for node in ast.walk(tree):
            if isinstance(node, ast.Match):
                found.append((node.lineno, "match statement (3.10)"))
            elif isinstance(node, ast.TryStar):
                found.append((node.lineno, "except* (3.11)"))
            elif isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", None)
                )
                if name in self.NEWER_THAN_BASELINE:
                    found.append((node.lineno, self.NEWER_THAN_BASELINE[name]))
                if name == "isinstance" and len(node.args) == 2:
                    second = node.args[1]
                    if isinstance(second, ast.BinOp) and isinstance(second.op, ast.BitOr):
                        found.append((node.lineno, "X | Y inside isinstance() (3.10)"))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    head = alias.name.split(".")[0]
                    if head in self.NEWER_THAN_BASELINE:
                        found.append((node.lineno, self.NEWER_THAN_BASELINE[head]))
            elif isinstance(node, ast.ImportFrom):
                head = (node.module or "").split(".")[0]
                if head in self.NEWER_THAN_BASELINE:
                    found.append((node.lineno, self.NEWER_THAN_BASELINE[head]))
                for alias in node.names:
                    if alias.name in self.NEWER_THAN_BASELINE:
                        found.append((node.lineno, self.NEWER_THAN_BASELINE[alias.name]))
        return found

    def test_the_scan_finds_the_shipping_tree(self):
        """Guards the guard: the assertion below is a negative over this
        list, and a negative over nothing is true (C76 §4)."""
        names = {f.name for f in self._shipping_files()}

        self.assertIn("businessdate.py", names)
        self.assertIn("run_company_ops.py", names)
        self.assertIn("ops_status.py", names)
        self.assertGreater(len(self._shipping_files()), 50)

    def test_the_detector_finds_a_construct_it_is_meant_to_find(self):
        """The predicate, on inputs it must flag and one it must not.

        Without this the assertion below passes whether the walk works or
        not — and the roster it walks is hand-written.
        """
        for source, expected in (
            ("match x:\n    case 1:\n        pass\n", "match"),
            ("import tomllib\n", "tomllib"),
            ("from itertools import pairwise\n", "pairwise"),
            ("y = 'ab'.removeprefix('a')\n", "removeprefix"),
            ("isinstance(v, int | str)\n", "isinstance"),
        ):
            with self.subTest(source=source.splitlines()[0]):
                found = self._newer_constructs_in(source, "probe.py")
                self.assertTrue(found, f"{expected} was not detected")

        plain = "from __future__ import annotations\n\n\ndef f(x: int | None) -> str | None:\n    return None\n"
        self.assertEqual(
            self._newer_constructs_in(plain, "probe.py"),
            [],
            "a deferred annotation is not a runtime requirement",
        )

    def test_no_shipping_file_uses_a_construct_newer_than_the_baseline(self):
        offenders = []
        for path in self._shipping_files():
            source = path.read_text(encoding="utf-8")
            for line, why in self._newer_constructs_in(source, str(path)):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{line}: {why}"
                )

        self.assertEqual(
            offenders,
            [],
            "src/ and the entrypoints raised the interpreter floor; A-25's "
            "decision (which version is supported) now has to be made",
        )


class AFirstRunOnAnUnconfiguredMachineStopsBeforeItTouchesAnythingTests(
    unittest.TestCase
):
    """C135, found by E-1b. What an operator sees the very first time they
    type the command on a machine where nothing is exported yet.

    `AnEntrypointRefusesArgumentsItCannotHonourTests` covers the *argument*
    half of that first contact and runs each tool as a real subprocess for
    the right reason -- "what the operating system sees when a person types
    the command". Its own `test_no_arguments_still_reaches_the_tool` covers
    the no-argument half for exactly one tool, `ops_status.py`, and its
    docstring says of the others only that they "stop at their own
    environment check, which is the pre-existing behaviour and a different
    message". Nothing asserted that.

    Per-entrypoint coverage (E-1b, C135) is what made the gap visible:

        run_company_ops.py   148 stmts   84%   216-325 = the whole of main()

    Those numbers understate the truth, because `coverage run` cannot see
    into a subprocess and this repository tests entrypoints as subprocesses
    on purpose. But the two halves agree on this much: `main()`'s body past
    the argument refusal is executed by no test, in-process or out.

    Three properties, and the third is the one worth having:

      1. it exits non-zero, so a scheduler or a shell `&&` chain stops;
      2. it says on **stderr** what to set, naming a variable this project
         actually reads;
      3. **it writes nothing.** A tool that gets halfway into a run before
         discovering it has no configuration leaves a partial state behind,
         and the next properly-configured run inherits it. Measured here by
         comparing the whole `runtime/` tree before and after.

    The roster is derived, minus three exclusions that each name a reason —
    and each exclusion is itself checked below, because an exclusion nobody
    re-examines is how `AnEntrypointRefusesArgumentsItCannotHonourTests`
    came to miss `src/review_cli.py` for a whole release (C79).
    """

    #: Tools this class does not run, and why. Not "not applicable" — each is
    #: a tool whose no-argument behaviour is deliberately something else.
    NOT_RUN = {
        "ops_status.py": (
            "The one tool that runs with no configuration at all: it is the "
            "read-only diagnostic an operator reaches for precisely when "
            "nothing is set up yet. Its no-argument behaviour is already "
            "asserted by AnEntrypointRefusesArgumentsItCannotHonourTests::"
            "test_no_arguments_still_reaches_the_tool."
        ),
        "dashboard_server.py": (
            "Binds a TCP port and serves until interrupted. Running it here "
            "would either hang the suite or fight whatever else holds the "
            "port; its configuration errors are executed in-process by "
            "test_dashboard_server.py instead."
        ),
        "src/review_cli.py": (
            "Interactive by design — with no arguments it prints a live KEEP "
            "Candidate and waits at an edit prompt. Measured: exit 3 only "
            "because stdin was closed. It is covered by test_review_cli.py, "
            "which drives it with an injected input function."
        ),
    }

    @property
    def ENTRYPOINTS(self):
        return tuple(n for n in _entrypoint_files() if n not in self.NOT_RUN)

    #: Variables the project reads. A refusal that names something else is
    #: sending the operator to a knob that does not exist (C117's shape).
    def _readable_variables(self):
        return EnvironmentContractTests()._declared_variables()

    @staticmethod
    def _runtime_fingerprint():
        """Every file under `runtime/`, by path and size.

        Sizes rather than mtimes: a file rewritten with identical content is
        still a write this class wants to see, and mtime granularity on
        Windows is coarse enough to miss a fast one.
        """
        root = REPO_ROOT / "runtime"
        if not root.is_dir():
            return ()
        return tuple(
            sorted(
                (str(f.relative_to(root)).replace("\\", "/"), f.stat().st_size)
                for f in root.rglob("*")
                if f.is_file()
            )
        )

    def _scrubbed_environment(self):
        """This process's environment minus every variable the project reads.

        **This is a safety boundary, not a convenience.** Two of these tools
        talk to Notion. On an operator's own machine `NOTION_API_TOKEN` is
        exported, and a scrub list that came back empty — or that lost a name
        — would have the suite make a **real API call to a real workspace**.
        So the list is checked here rather than trusted, on every call.
        """
        readable = self._readable_variables()
        self.assertGreaterEqual(
            len(readable), 6, f"the scrub list collapsed: {sorted(readable)}"
        )
        for credential in ("NOTION_API_TOKEN", "NOTION_PROJECTS_DATABASE_ID"):
            self.assertIn(
                credential,
                readable,
                "the scrub list does not name a credential these tools use; "
                "running them would reach a real workspace",
            )

        environment = dict(os.environ)
        for variable in readable:
            environment.pop(variable, None)
        environment["PYTHONIOENCODING"] = "utf-8"

        leaked = sorted(
            name
            for name in environment
            if name.startswith("NOTION_") or name.startswith("COMPANY_OPS_")
        )
        self.assertEqual(leaked, [], f"configuration survived the scrub: {leaked}")
        return environment

    def test_the_scrub_removes_a_credential_that_is_actually_set(self):
        """The predicate, on an environment that has one.

        On a developer machine none of these is exported, so every assertion
        above passes without the scrub doing anything. This sets one and
        checks it is gone.
        """
        os.environ["NOTION_API_TOKEN"] = "secret-probe-value"
        try:
            environment = self._scrubbed_environment()
        finally:
            os.environ.pop("NOTION_API_TOKEN", None)

        self.assertNotIn("NOTION_API_TOKEN", environment)
        self.assertNotIn(
            "secret-probe-value",
            "".join(environment.values()),
            "the token survived under another name",
        )

    def _run_unconfigured(self, name):
        """`name`, as a subprocess, with every variable this project reads
        removed from the environment."""
        environment = self._scrubbed_environment()

        return subprocess.run(
            [sys.executable, str(REPO_ROOT / name)],
            cwd=str(REPO_ROOT),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )

    def test_the_roster_is_not_empty_and_names_the_runner(self):
        """Guards the guard: every assertion below is a loop over this list,
        and a loop over nothing passes (C76 §4)."""
        self.assertIn("run_company_ops.py", self.ENTRYPOINTS)
        self.assertIn("run_agent.py", self.ENTRYPOINTS)
        self.assertGreaterEqual(len(self.ENTRYPOINTS), 3, self.ENTRYPOINTS)

    def test_every_exclusion_is_still_a_real_entrypoint(self):
        """An exclusion for a file that no longer exists, or that stopped
        being an entrypoint, is a note outliving its reason."""
        for name, why in self.NOT_RUN.items():
            with self.subTest(excluded=name):
                self.assertGreater(len(why), 60, "an exclusion needs a reason")
                self.assertIn(
                    name,
                    _entrypoint_files(),
                    f"{name} is excluded from a roster it is no longer in",
                )

    def test_an_unconfigured_run_exits_non_zero(self):
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                result = self._run_unconfigured(name)
                self.assertNotEqual(
                    result.returncode,
                    0,
                    f"{name} reported success with no configuration:\n"
                    f"{result.stdout}\n{result.stderr}",
                )

    def test_an_unconfigured_run_says_what_to_set_on_stderr(self):
        """On stderr, not stdout: a scheduler captures the two separately and
        this is a diagnostic, not output. And it must name a variable that
        exists — `EnvironmentContractTests` owns that roster."""
        readable = self._readable_variables()

        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                result = self._run_unconfigured(name)

                self.assertIn("[FAILED]", result.stderr)
                named = [v for v in readable if v in result.stderr]
                self.assertTrue(
                    named,
                    f"{name}'s refusal names no variable this project reads:\n"
                    f"{result.stderr}",
                )

    def test_an_unconfigured_run_writes_nothing(self):
        """The property this class exists for.

        Each tool is checked against the tree as it was immediately before
        that tool ran, so one failure cannot be masked or caused by another.
        """
        for name in self.ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                before = self._runtime_fingerprint()
                self._run_unconfigured(name)
                after = self._runtime_fingerprint()

                self.assertEqual(
                    before,
                    after,
                    f"{name} wrote to runtime/ before discovering it has no "
                    f"configuration",
                )

    def test_the_fingerprint_would_notice_a_write(self):
        """The predicate, on a tree that did change. Without this,
        `test_an_unconfigured_run_writes_nothing` passes whether or not the
        fingerprint sees anything at all."""
        root = REPO_ROOT / "runtime"
        if not root.is_dir():
            self.skipTest("no runtime/ tree on this machine")

        before = self._runtime_fingerprint()
        probe = root / ".hygiene-write-probe"
        probe.write_text("x", encoding="utf-8")
        try:
            self.assertNotEqual(before, self._runtime_fingerprint())
        finally:
            probe.unlink()
        self.assertEqual(before, self._runtime_fingerprint())



class AMisconfiguredValueIsNamedWithoutBeingArchivedTests(unittest.TestCase):
    """What a configuration error puts on **disk**, now that one can.

    The class above asks what an unconfigured machine prints. This asks the
    next question, which only became a question in C138: the installers now
    register their action as `cmd /c "... >> runtime\\logs\\scheduled_*.log
    2>&1"`, so a scheduled run's stderr persists instead of being discarded.

    `publish_control_tower.py` had already reasoned it through for its own
    output -- "`tool > log 2>&1` puts it on disk, which is the shape `oplog`
    exists to stop" -- and it simply was not true of anything yet.

    Three configuration errors interpolate an **environment value** back to
    the operator, which is arbitrary in length and content:

        run_company_ops.py   COMPANY_OPS_HISTORY_START_DATE
        run_agent.py         COMPANY_OPS_AGENT_START_DATE
        run_agent.py         COMPANY_OPS_PROFILE  (via resolve_profile)

    Measured before the guard, with a token-shaped value in each:

        [FAILED] COMPANY_OPS_HISTORY_START_DATE ... : 'ntn_AAAA...'

    Every other thing this system writes to a log goes through
    `oplog.append_line()`, which redacts, flattens and bounds. These did
    not, and the one mistake that puts a credential in one of these three
    variables is mixing up which variable takes the Notion token.

    **Both halves are asserted, and the second is the one that keeps this
    honest.** Masking everything would be trivially "safe" and would destroy
    the diagnosis these messages exist for -- `run_company_ops.py`'s own
    comment says printing the value is what separates "never set" from "set
    to something wrong". So an ordinary typo must still come back verbatim.
    """

    #: Built by concatenation so this file never *contains* a
    #: credential-shaped literal: `test_no_secret_material_in_any_tracked_file`
    #: scans every tracked file and does not exempt tests, correctly -- a
    #: fixture that looks like a secret is indistinguishable from one to a
    #: scanner and to anyone reading the diff.
    SECRET_SHAPED = "ntn_" + "A" * 44

    #: `(entrypoint, {variable: value})`, with the variable under test last.
    #: The companion values are the ones each tool needs to *reach* the
    #: check being exercised.
    CASES = (
        ("run_company_ops.py", "COMPANY_OPS_HISTORY_START_DATE", {}),
        ("run_agent.py", "COMPANY_OPS_PROFILE", {}),
        (
            "run_agent.py",
            "COMPANY_OPS_AGENT_START_DATE",
            {
                "COMPANY_OPS_PROFILE": "DESKTOP_1",
                "COMPANY_OPS_AGENT_SYNC_FOLDER": "C:/does-not-exist",
            },
        ),
    )

    def _run(self, name, variable, value, companions):
        harness = AFirstRunOnAnUnconfiguredMachineStopsBeforeItTouchesAnythingTests()
        harness.assertGreaterEqual = self.assertGreaterEqual
        harness.assertIn = self.assertIn
        harness.assertEqual = self.assertEqual
        environment = harness._scrubbed_environment()
        environment.update(companions)
        environment[variable] = value

        return subprocess.run(
            [sys.executable, str(REPO_ROOT / name)],
            cwd=str(REPO_ROOT),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

    def test_the_cases_name_variables_this_project_reads(self):
        """Guards the guard. A variable nothing reads would make every
        assertion below pass against a tool that never looked at it."""
        readable = AFirstRunOnAnUnconfiguredMachineStopsBeforeItTouchesAnythingTests(
        )._readable_variables()

        for _name, variable, _companions in self.CASES:
            with self.subTest(variable=variable):
                self.assertIn(variable, readable)

    def test_a_credential_shaped_value_does_not_reach_the_stream(self):
        for name, variable, companions in self.CASES:
            with self.subTest(entrypoint=name, variable=variable):
                result = self._run(
                    name, variable, self.SECRET_SHAPED, companions
                )
                output = (result.stdout or "") + (result.stderr or "")

                self.assertNotEqual(result.returncode, 0, output)
                self.assertNotIn(self.SECRET_SHAPED, output)
                self.assertIn("REDACTED", output)

    def test_an_ordinary_mistake_is_still_quoted_back(self):
        """The half that stops this from being fixed by muting the message.

        A mistyped date and an unknown Desktop id are not secret-shaped, so
        `redact()` leaves them alone -- and an operator staring at their own
        typo needs to see it.
        """
        for name, variable, companions, typo in (
            ("run_company_ops.py", "COMPANY_OPS_HISTORY_START_DATE", {}, "2026-8-1"),
            ("run_agent.py", "COMPANY_OPS_PROFILE", {}, "DESKTOP_9"),
            (
                "run_agent.py",
                "COMPANY_OPS_AGENT_START_DATE",
                {
                    "COMPANY_OPS_PROFILE": "DESKTOP_1",
                    "COMPANY_OPS_AGENT_SYNC_FOLDER": "C:/does-not-exist",
                },
                "10 August 2026",
            ),
        ):
            with self.subTest(entrypoint=name, variable=variable):
                result = self._run(name, variable, typo, companions)
                output = (result.stdout or "") + (result.stderr or "")

                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(typo, output)
                self.assertNotIn("REDACTED", output)

    def test_the_message_stays_one_line_and_bounded(self):
        """An environment value is chosen by whoever set it. Unbounded, it
        makes one log line as long as the value; unflattened, it forges as
        many report lines as it likes -- into a file whose last lines
        `ops_status.py` prints on the screen an operator reads when a
        scheduled run has failed.
        """
        from oplog import MAX_LOG_ERROR

        forged = "2026-01-01\n[FAILED] " + "z" * (MAX_LOG_ERROR * 2)
        result = self._run(
            "run_company_ops.py", "COMPANY_OPS_HISTORY_START_DATE", forged, {}
        )
        failures = [
            line
            for line in ((result.stderr or "") + (result.stdout or "")).splitlines()
            if "[FAILED]" in line
        ]

        self.assertEqual(len(failures), 1, failures)
        self.assertLess(len(failures[0]), MAX_LOG_ERROR * 2)


class EveryTrackedModuleParsesOnThisInterpreterTests(unittest.TestCase):
    """C50: one 3.10-only annotation stopped the entire suite from running.

    What happened, measured on this machine at HEAD 43771a9:

        python -m pytest tests/
        ERROR tests/test_daily_late_events.py - TypeError: unsupported operand
        !!!! Interrupted: 1 error during collection !!!!
        2999 tests collected, 1 error

    `tests/test_daily_late_events.py` gained `def _field(...) -> str | None`
    in C47 and did not carry `from __future__ import annotations`. A method
    signature is evaluated at `def` time, so PEP 604's `|` ran as a real
    `type.__or__` on an interpreter that does not have one -- during
    **collection**, which pytest treats as fatal for the whole session.

    The damage is not the one file. It is that **no test ran at all** for two
    commits, while C48 and C49 both recorded a "fresh 전체 실행". A suite that
    aborts and a suite that passes differ by one line near the top of a long
    log, and this project has a Sprint (C38) about exactly that shape.

    Why it survived: C17 already recorded this environment drift once --
    "이전 Sprint 기록('이 머신, Python 3.13')과 달리 현재 기본 `python`은
    Anaconda의 3.9.7" -- and fixed the instances then. Nothing was left
    behind that would fail the next time, so the next time it came back.

    This is that thing. It checks the interpreter that is actually running,
    not a version this repository believes it is on, so it cannot go stale
    the way a hardcoded version would.

    **What an upgrade does to it, stated properly (C76).** The two halves
    below age differently, and the earlier wording ("the check simply stops
    finding anything") described only the harmless one.

        test_no_module_evaluates_a_pep604_annotation_without_the_future_import
            an AST rule. Version-independent: it gives the same verdict on
            3.9 and on 3.13, and an upgrade costs it nothing.

        test_every_tracked_python_file_compiles
            `compile()` on **this** interpreter. On 3.9 it also enforced
            3.9. On 3.13 it enforces 3.13 and says nothing about 3.9, so a
            `match` statement or an `except*` would pass here and abort
            collection on a Desktop that has not moved -- the same
            whole-suite failure described above, one machine over.

    That is a live gap rather than a theoretical one, because the Agents run
    on DESKTOP_1/2/3 and this file's own `_tracked_files()` docstring notes
    the project is moved between them. Measured in C76, so the size of it is
    on the record rather than assumed: across all 139 tracked `.py` files,
    **zero** post-3.9 AST constructs (`Match`, `TryStar`, PEP 695
    `TypeAlias`/`TypeVar`). Nothing has drifted; nothing would notice if it
    did.

    Closing it needs a **declared minimum interpreter**, which this
    repository does not have anywhere -- no `python_requires`, no line in
    README or docs/11. Picking one is a decision, so it is recorded in
    BACKLOG A rather than chosen here.
    """

    def _python_files(self):
        return [
            path
            for path in _tracked_files()
            if path.suffix == ".py" and path.is_file()
        ]

    def test_the_scan_finds_the_files_we_know_exist(self):
        """A guard that silently scanned nothing would pass forever."""
        names = {path.name for path in self._python_files()}

        self.assertIn("test_daily_late_events.py", names)
        self.assertIn("ops_status.py", names)
        self.assertGreater(len(names), 50)

    def test_every_tracked_python_file_compiles(self):
        """Syntax, on this interpreter. Catches the constructs that are not
        annotations at all -- `match`, `except*`, a walrus in a comprehension
        target -- which fail earlier and just as fatally."""
        for path in self._python_files():
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                source = path.read_text(encoding="utf-8")
                try:
                    compile(source, str(path), "exec")
                except SyntaxError as exc:  # pragma: no cover - the guard
                    self.fail(
                        f"{path.relative_to(REPO_ROOT)}:{exc.lineno} does not "
                        f"compile on Python {sys.version_info.major}."
                        f"{sys.version_info.minor}: {exc.msg}"
                    )

    @staticmethod
    def _has_future_annotations(tree) -> bool:
        for node in tree.body:
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "__future__"
                and any(alias.name == "annotations" for alias in node.names)
            ):
                return True
        return False

    @staticmethod
    def _annotations(tree):
        """Every annotation expression that is **evaluated at runtime**.

        A `def`'s parameter and return annotations, and an `AnnAssign`'s.
        Deliberately not every `Subscript` in the file: a `|` inside a string
        annotation, a comment, or `typing.get_type_hints()` input is not
        evaluated at import time and is not what broke.
        """
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = node.args
                for group in (
                    arguments.posonlyargs,
                    arguments.args,
                    arguments.kwonlyargs,
                    [arguments.vararg] if arguments.vararg else [],
                    [arguments.kwarg] if arguments.kwarg else [],
                ):
                    for argument in group:
                        if argument.annotation is not None:
                            yield argument.annotation
                if node.returns is not None:
                    yield node.returns
            elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
                yield node.annotation

    def test_no_module_evaluates_a_pep604_annotation_without_the_future_import(self):
        """`X | None` in a runtime-evaluated position, in a file that does not
        carry `from __future__ import annotations`.

        Every module in this tree already carries the import -- it is the
        convention, not a workaround -- so the fix for anything this finds is
        one line, and finding it here costs one test rather than a whole
        session's results.
        """
        offenders = []
        for path in self._python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            if self._has_future_annotations(tree):
                continue
            for annotation in self._annotations(tree):
                for node in ast.walk(annotation):
                    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                        )
        self.assertEqual(
            offenders,
            [],
            "PEP 604 annotation evaluated at import time without "
            "`from __future__ import annotations` -- this aborts pytest "
            "collection for the WHOLE suite on Python < 3.10:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_detector_actually_detects(self):
        """The guard above passes on a clean tree, which is also what a
        broken guard does. This drives it over the exact source that was in
        `tests/test_daily_late_events.py`, and over the fixed version."""
        broken = "def _field(self, text: str, prefix: str) -> str | None:\n    return None\n"
        fixed = "from __future__ import annotations\n" + broken

        def _offends(source: str) -> bool:
            tree = ast.parse(source)
            if self._has_future_annotations(tree):
                return False
            return any(
                isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
                for annotation in self._annotations(tree)
                for node in ast.walk(annotation)
            )

        self.assertTrue(_offends(broken))
        self.assertFalse(_offends(fixed))
        self.assertFalse(_offends("def f(a: int) -> str:\n    return ''\n"))
        self.assertFalse(_offends("x = 1 | 2\n"))



class NothingHereNeedsAGrammarOlderDesktopsDoNotHaveTests(unittest.TestCase):
    """CHARACTERIZATION (C119): every tracked module still parses under the
    **3.9** grammar, which is what DESKTOP_1 runs.

    The class above states this gap in its own docstring and then says
    nothing would notice it:

        test_every_tracked_python_file_compiles
            `compile()` on **this** interpreter. On 3.9 it also enforced
            3.9. On 3.13 it enforces 3.13 and says nothing about 3.9, so a
            `match` statement or an `except*` would pass here and abort
            collection on a Desktop that has not moved.

        Measured in C76 ... across all 139 tracked `.py` files, **zero**
        post-3.9 AST constructs. Nothing has drifted; **nothing would notice
        if it did.**

    This is the thing that would notice. It is not the "declared minimum
    interpreter" that docstring says is needed and that BACKLOG A holds as a
    decision — it declares nothing. It pins the **measurement** C76 already
    took, the way `StrayShellArtifactsInTheRepositoryRootTests` pins a set of
    files nobody has decided about yet: the day this goes red, somebody
    chooses, and until then the tree cannot drift past 3.9 in silence.

    **Why 3.9 and not some other number.** Not chosen here either. BACKLOG D
    records it: DESKTOP_1 is `Python 3.9.7 / Windows 10 Pro 10.0.19042`, it
    runs an Agent (AGENT.md §1), and the whole tree is copied there. The 32
    failures that machine already shows are enumerated in that section and
    understood — 31 of them are `enterContext` (3.11+) in one test file, an
    `AttributeError` at run time. A **syntax** error is worse than any of
    them: it aborts collection of its whole file, so the 32 would become "32
    failures and some number of tests that no longer exist".

    `ast.parse(..., feature_version=(3, 9))` rather than a hand-written list
    of node types. CPython maintains it, and the messages name themselves:

        match      -> Pattern matching is only supported in Python 3.10 and greater
        except*    -> Exception groups are only supported in Python 3.11 and greater
        type X = Y -> Type statement is only supported in Python 3.12 and greater
        def f[T]() -> Type parameter lists are only supported in Python 3.12 and greater

    **What it does not see, stated rather than implied.** `feature_version`
    reaches the parser, not the tokenizer, so PEP 701 f-strings (`f"{d["k"]}"`,
    3.12+) pass here and fail on 3.9. Measured, not assumed — that string is
    in `test_the_check_can_actually_disagree` below. Runtime API drift
    (`enterContext` again) is outside this entirely: it is not syntax.
    """

    #: BACKLOG D's DESKTOP_1, as a tuple. Not a policy — see the docstring.
    OLDEST_DESKTOP = (3, 9)

    def _python_files(self):
        return [
            path
            for path in _tracked_files()
            if path.suffix == ".py" and path.is_file()
        ]

    def test_the_scan_sees_the_files_it_is_supposed_to(self):
        """Guards the guard. A derivation that came back empty would pass
        this class forever, which is the vacuous shape C76 §4 swept for."""
        names = {path.name for path in self._python_files()}

        self.assertIn("ops_status.py", names)
        self.assertIn("run_agent.py", names)
        self.assertGreater(len(names), 50)

    def _refusal(self, source, name="<probe>"):
        """The `SyntaxError` the older grammar gives for `source`, or `None`.

        One copy, and the guard below drives **this** rather than `ast.parse`
        directly. Measured why: a mutant that dropped `feature_version` from
        the sweep left a guard written against `ast.parse` completely green,
        because that guard was proving something about the standard library
        instead of about the check it guards.
        """
        try:
            ast.parse(source, name, feature_version=self.OLDEST_DESKTOP)
        except SyntaxError as exc:
            return exc
        return None

    def test_every_tracked_module_parses_under_the_older_grammar(self):
        offenders = []
        for path in self._python_files():
            source = path.read_text(encoding="utf-8")
            refusal = self._refusal(source, str(path))
            if refusal is not None:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{refusal.lineno}: {refusal.msg}"
                )

        self.assertEqual(
            offenders,
            [],
            "a tracked module uses syntax newer than Python "
            f"{self.OLDEST_DESKTOP[0]}.{self.OLDEST_DESKTOP[1]}, which is what "
            "BACKLOG D records DESKTOP_1 as running. On that machine this is "
            "not a failing test — it aborts collection of the whole file. "
            "Either rewrite it, or decide in BACKLOG A that the older "
            "Desktops are no longer supported and change OLDEST_DESKTOP with "
            "that decision written down:\n  " + "\n  ".join(offenders),
        )

    def test_the_check_can_actually_disagree(self):
        """A `feature_version` that was being ignored would make the test
        above pass over anything. Each of these is a real construct from the
        release that introduced it — and the last one is the limit this
        check has, asserted rather than left as a footnote."""
        newer = {
            "3.10 match": "match x:\n    case 1:\n        pass\n",
            "3.11 except*": "try:\n    pass\nexcept* ValueError:\n    pass\n",
            "3.12 type alias": "type Alias = int\n",
            "3.12 type params": "def f[T](x: T) -> T:\n    return x\n",
        }
        for label, source in newer.items():
            with self.subTest(construct=label):
                refusal = self._refusal(source)
                self.assertIsNotNone(
                    refusal,
                    f"{label} was accepted — the sweep above is passing over "
                    "anything a newer interpreter can parse",
                )
                ast.parse(source)  # and it is fine on this interpreter

        # The blind spot, pinned so it cannot be discovered twice.
        self.assertIsNone(self._refusal('d = {}\nprint(f"{d["k"]}")\n'))

    def test_the_grammar_this_repository_actually_uses_still_passes(self):
        """The antecedent. A check that rejected everything would also make
        the sweep above green-by-accident impossible to tell from real."""
        ordinary = (
            "from __future__ import annotations\n"
            "def f(x: int | None = None) -> str | None:\n"
            "    if (n := 1):\n"
            "        return f'{n}'\n"
            "    return None\n"
        )
        self.assertIsNone(self._refusal(ordinary))


class NoPatchPlaceholderSurvivesIntoTheSourceTests(unittest.TestCase):
    """C94. A substitution token that never got substituted.

    Source in this repository is edited by patch scripts, and one of them
    builds a line with a placeholder and calls `.replace()` to fill it in.
    When the `.replace()` is forgotten, the token ships -- a brace-wrapped
    ALL-CAPS word sitting where an em dash belongs, in the middle of a
    sentence.

    Five reached `src/agent/status.py`, in the docstrings of
    `_is_date_directory_name()` and `_count_unreachable_signals()` -- the
    latter being the paragraph that explains what a misfiled Signal means,
    which is what a person reads when deciding whether work they typed is
    gone. They survived three full regressions. Nothing reads a docstring
    for spelling, and no test ever will.

    **This guard exists because the obvious one does not work.** The same
    Sprint measured a "flag any Hangul syllable that appears nowhere else in
    the repository" check against the three garbled-text bugs it had
    actually produced, and it caught one of three -- both real typos used
    syllables that occur elsewhere. A test with that hit rate is the false
    comfort this Sprint exists to remove, so it was not written. This one is
    different in kind: it is not looking for a *typo*, it is looking for a
    token that **cannot be correct in the position it is in**, and that is
    decidable.

    The rule is narrow for a measured reason. A brace-wrapped ALL-CAPS name
    inside an **f-string** is an ordinary interpolation of a module
    constant, and there are 45 of them here. The same token inside a plain
    string or a comment is never substituted by anything. Measured across
    every `.py` file after the five were repaired: **0**.

    **This file obeys the rule it defines.** Every token below is assembled
    at runtime rather than written out, and the docstring above names them
    in prose. Exempting the file that owns a check is how a check stops
    being true of itself -- and there would be no way to notice, because the
    only thing that could notice is the check.
    """

    TOKEN = re.compile(r"\{[A-Z][A-Z0-9_]{2,}\}")

    @staticmethod
    def _token(name):
        """A placeholder, built rather than written. See the docstring."""
        return "{" + name + "}"

    @staticmethod
    def _unsubstitutable_text(source):
        """Every plain string and comment -- everything an f-string is not.

        An f-string's braces are filled in at runtime, so a token there is a
        value. A docstring's are not filled in by anything, so a token there
        is a leak. That distinction is the whole test, which is why it is
        drawn from the AST rather than by pattern.
        """
        import io
        import tokenize

        tree = ast.parse(source)
        interpolated = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for part in ast.walk(node):
                    interpolated.add(id(part))

        found = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in interpolated
            ):
                found.append((node.lineno, node.value))
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                found.append((token.start[0], token.string))
        return found

    def _leaks_in(self, source):
        return [
            (line, match.group(0))
            for line, text in self._unsubstitutable_text(source)
            for match in self.TOKEN.finditer(text)
        ]

    def _python_files(self):
        for path in sorted(REPO_ROOT.glob("**/*.py")):
            if "__pycache__" in path.parts or ".git" in path.parts:
                continue
            yield path

    def test_no_placeholder_reached_the_tree(self):
        offenders = []
        for path in self._python_files():
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover
                continue
            for line, token in self._leaks_in(source):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{line} {token}"
                )

        self.assertEqual(
            offenders,
            [],
            "a patch-script placeholder was never substituted and shipped "
            f"into the source: {offenders}",
        )

    def test_the_check_catches_the_shape_that_actually_happened(self):
        """Fault injection, in the exact form the five leaks took: a token
        inside a docstring, standing where an em dash belongs."""
        token = self._token("DASH")
        source = 'def f():\n    """A sentence ' + token + ' and its continuation."""\n'

        self.assertEqual(self._leaks_in(source), [(2, token)])

    def test_a_comment_is_read_too(self):
        """The idiom that produced the five writes comments as often as it
        writes docstrings."""
        token = self._token("DASH")

        self.assertEqual(
            self._leaks_in("x = 1  # a note " + token + " and more\n"), [(1, token)]
        )

    def test_an_f_string_interpolation_is_not_a_leak(self):
        """The 45 real ones. Without this the check is unusable and would be
        deleted rather than obeyed, which is worse than not having it.

        Note *why* it passes, because it is not the `JoinedStr` exclusion
        below: `ast` turns an interpolation into a `FormattedValue`, so a
        braced constant name never exists as a string constant to be
        searched at all.
        The exclusion earns its place on the next test instead -- which is
        how it was found to be doing nothing here, by a mutation that
        removed it and broke no test.
        """
        source = 'SECRET = "s"\nx = f"token=' + self._token("SECRET") + ' rest"\n'

        self.assertEqual(self._leaks_in(source), [])

    def test_an_escaped_brace_inside_an_f_string_is_not_a_leak(self):
        """What the `JoinedStr` exclusion is actually for.

        A **doubled** brace inside an f-string is a deliberate escape: it
        renders the literal single-braced text, and `ast` hands that back
        as one string constant whose value is exactly what a leak looks
        like. Nothing else distinguishes
        the two, so without the exclusion this legitimate line would be
        reported and the check would start costing more than it catches.

        A first draft of this class asserted the exclusion with the test
        above, which passes with or without it -- a mutation that deleted
        the exclusion outright was MISSED. This is the input that separates
        them.
        """
        token = self._token("DASH")
        escaped = 'x = f"see {{' + "DASH" + '}} here"\n'

        self.assertNotIn(token, [found for _line, found in self._leaks_in(escaped)])
        # ...and the same text outside an f-string still is a leak, so the
        # exclusion is narrow rather than a blanket amnesty for braces.
        self.assertEqual(
            self._leaks_in('x = "see ' + token + ' here"\n'), [(1, token)]
        )

    def test_the_scan_actually_reads_files(self):
        """`TheScansThisFileTrustsAreNotEmptyTests`' rule applied here: a
        scan that found no files would make the assertion above green
        forever."""
        self.assertGreater(len(list(self._python_files())), 50)

    def test_this_file_is_not_exempt_from_its_own_rule(self):
        """The claim the docstring makes, checked rather than asserted."""
        self.assertIn(
            Path(__file__).resolve(), set(self._python_files())
        )


if __name__ == "__main__":
    unittest.main()
