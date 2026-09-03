"""Development activity read out of git, for the D+1 Company Update.

Why this exists
---------------
Everything this system knows about development arrives as an Execution
Event: somebody on some Desktop decides a thing happened and reports it.
That is the right source for *judgement* — "this milestone is done", "this
is blocked" — and it is the wrong source for *fact*: nobody reports every
commit, and the day nobody reports anything is indistinguishable from the
day nothing happened.

Git already holds that fact, on the machine, with no account to log into
and no folder to sync. So the D+1 question "what actually changed
yesterday" is answered here, from `git log`, and the D+1 questions "what
completed / what is blocked / what needs a decision" stay answered by
Events. Two sources, two kinds of question, neither pretending to be the
other.

**Git is not made the company's source of truth.** It has no idea what a
Project is, which team owns one, whether anything is blocked, or that a
Decision is pending — `_OPEN_ITEM_LIFECYCLES` and `ProjectRollup` hold all
of that, and none of it is derivable from a commit. What git contributes is
one narrow, checkable fact: which files changed, when, and by whom.

Relation to OneDrive
--------------------
`transport/onedrive.py` is how an Event *travels between Desktops*. It is
untouched, and it is still the Transport `run_agent.py` builds.

What changed is that the D+1 report no longer has nothing to say when that
path is down. Before this module, a Desktop whose OneDrive was full, signed
out, or simply switched off produced no Events, and every downstream view
read that as a quiet day — the failure mode with no signal. Git activity is
read from the local repository with no network and no cloud account, so
"the machine was working and delivery was not" and "nobody worked" stop
looking identical.

Never raises
------------
Same posture as `controltower/rollup.py` and `history/reconciliation.py`:
this is read while things are going wrong. No git, no repository, a git
that times out or answers in a shape this does not expect — all of them
come back as `available=False` with the reason attached, because a D+1
report that dies because git is missing is worse than one that says git is
missing.

What it costs, measured
-----------------------
On this repository, warm (`GitActivity` built end to end, including the
`rev-parse` probe):

    1 day      35.5 ms    1 commit,  21 files
    1 month    37.9 ms   31 commits, 182 files
    everything 37.8 ms   31 commits, 182 files

Flat, because it is two process starts and git does the rest. Against a
Dashboard page that already spends ~100 ms building the model and several
hundred more reading `processed/`, this is not a cost worth caching — and a
cached answer to "what changed" is the wrong kind of wrong, for
`build_company_rollup()`'s stated reason.

**Nothing bounds the number of commits parsed, deliberately.** A cap would
make `commit_count` and `files_changed` under-report a busy window without
saying so, which is the one failure this whole module exists to prevent.
The *display* is bounded instead — `dashboard._code_changes_panel()` cuts
the row list at `RECENT_LIMIT` and puts the true total in its note.

Read-only, and pinned as such
-----------------------------
`log` and `rev-parse` only, both reads.
`tests/test_spec_conformance.py::…APPROVED_COMMANDS_ELSEWHERE` is the
review gate for every git command run outside `backup/git_ops.py`; it
requires a stated reason per command and checks that no writing verb
appears. This module is on that roster.

Deliberately not `backup.git_ops._run_git()`, which looks similar and is
not: that function exists to drive the Backup Working Copy against a
**remote**, and everything specific about it — the auth-failure marker
matching, `LC_ALL=C` so those English markers keep matching, the
BACKUP_PENDING classification its callers apply to a timeout — is about a
push that can fail on credentials. Nothing here touches a remote or can
fail that way. What the two genuinely share is the decode posture
(`encoding="utf-8"`, `errors="replace"`), and it is shared because getting
it wrong has the same consequence in both places: on Windows, `text=True`
decodes with the locale codepage inside subprocess's reader thread, so a
`UnicodeDecodeError` never reaches the caller and stdout silently becomes
`None`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from businessdate import KST

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: A local `git log` over a repository of this size is fast; this bound is
#: for the case where it is not — a repository on a disconnected network
#: drive, or a `.git` being rewritten by another process. An order of
#: magnitude under `backup/git_ops._GIT_TIMEOUT_SECONDS` because that one
#: covers a network push and this covers a local read.
GIT_TIMEOUT_SECONDS = 20.0

#: Field separator inside one `git log` record, and the record separator
#: between them. Both are ASCII control characters that cannot occur in a
#: commit subject, an author name, or an ISO date — unlike a tab or a pipe,
#: which can and do. A subject containing the separator would otherwise
#: split into extra fields and shift every later field by one.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"

#: The record separator **leads** the format, and that is load-bearing.
#:
#: `--name-only` prints a commit's file list *after* the line `--format`
#: produced for it, separated by a blank line. With the separator trailing
#: (`…%s\x1e`) the split lands between a commit's header and its own files,
#: so every file list is attributed to the **next** commit and the newest
#: commit reports zero files. Measured on this repository before the fix:
#: one commit in the window, `commit_count=1`, `files_changed=()` — against
#: a commit that touched 26 files. The count was right and the contents were
#: silently empty, which is the shape of loss that reads as "a quiet day".
#:
#: Leading it makes each chunk `header\n\n<files>`, which is what
#: `_parse_records()` splits on `\n` once. The empty first chunk (the text
#: before the first separator) is skipped there.
_LOG_FORMAT = _RECORD_SEP + _FIELD_SEP.join(["%H", "%aI", "%an", "%s"])


@dataclass(frozen=True)
class Commit:
    """One commit, with the files it touched.

    `files` is the paths `--name-only` reported. A commit that changed
    nothing legitimately has none; that is a fact about the commit, not a
    read failure, and the two are not conflated.
    """

    sha: str
    at: str
    author: str
    subject: str
    files: tuple[str, ...] = ()

    @property
    def short_sha(self) -> str:
        return self.sha[:8]


@dataclass(frozen=True)
class GitActivity:
    """What changed in one window, or why that could not be read.

    `available=False` is never an empty result dressed up as one: a window
    with no commits is `available=True` with `commits == ()`, and a git that
    could not be asked is `available=False` with `reason` set. Views must
    tell those apart — "nothing was committed yesterday" is a fact about the
    company, and "git could not be read" is a fact about this program. It is
    the same distinction `daily/role_summary.py` draws between "CMO 활동
    없음" and "CMO를 집계하는 것을 잊었다".
    """

    available: bool
    since: date_type | None = None
    until: date_type | None = None
    commits: tuple[Commit, ...] = ()
    reason: str | None = None
    repo_dir: str | None = None

    @property
    def commit_count(self) -> int:
        return len(self.commits)

    @property
    def files_changed(self) -> tuple[str, ...]:
        """Distinct paths touched in the window, sorted.

        Distinct, not a sum: one file edited in five commits is one file
        that changed, and reporting five would inflate the only number here
        a person is likely to read as a size.
        """
        seen: set[str] = set()
        for commit in self.commits:
            seen.update(commit.files)
        return tuple(sorted(seen))

    @property
    def authors(self) -> tuple[str, ...]:
        return tuple(sorted({commit.author for commit in self.commits if commit.author}))


def _run(args: list[str], repo_dir: Path) -> tuple[bool, str]:
    """`(ok, output_or_reason)` — this function does not raise.

    Every failure git can hand back arrives in the same shape, because the
    caller's job is to put a sentence in a report, not to tell a missing
    executable apart from a missing repository.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return False, "git is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} timed out after {GIT_TIMEOUT_SECONDS:.0f}s"
    except OSError as exc:  # unreadable cwd, permissions, a path that vanished
        return False, f"git {args[0]} could not be started: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return False, (
            f"git {args[0]} failed (exit {result.returncode})"
            + (f": {detail[0]}" if detail else "")
        )
    return True, result.stdout or ""


def _parse_records(raw: str) -> tuple[Commit, ...]:
    """Parse `--format=_LOG_FORMAT --name-only` output into Commits.

    A record whose header has too few fields is skipped rather than guessed
    at: a half-parsed commit would put a truncated sha or somebody else's
    name in a report, and a report nobody can trust is worse than a short
    one. Unreachable while `_LOG_FORMAT` and this parse agree, which is
    exactly why it must not be an exception — the day they stop agreeing is
    a day the D+1 report should still render.
    """
    commits: list[Commit] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        header, _, tail = record.partition("\n")
        fields = header.split(_FIELD_SEP)
        if len(fields) < 4:
            continue
        sha, at, author, subject = fields[0], fields[1], fields[2], fields[3]
        if not sha.strip():
            continue
        files = tuple(line for line in tail.split("\n") if line.strip())
        commits.append(
            Commit(
                sha=sha.strip(),
                at=at.strip(),
                author=author.strip(),
                subject=subject.strip(),
                files=files,
            )
        )
    return tuple(commits)


def read_git_activity(
    *,
    since: date_type,
    until: date_type,
    repo_dir: Path | None = None,
) -> GitActivity:
    """Commits authored in `[since, until]` inclusive, in the business zone.

    The window is given in **business dates** (docs/06 §9's `Asia/Seoul`)
    and not in machine-local time, for the reason C135 made a rule of:
    "which day did this happen on" must not depend on the clock zone of
    whichever machine runs the report. `--since` / `--until` are therefore
    handed timestamps carrying an explicit offset, so git answers the
    question this program asked rather than the one the environment's `TZ`
    makes of a bare date.

    `until` is inclusive — a caller that says "yesterday" means the whole of
    yesterday — which is why the bound is the last instant of that day and
    not its midnight.
    """
    directory = repo_dir if repo_dir is not None else PROJECT_ROOT

    if since > until:
        return GitActivity(
            available=False,
            since=since,
            until=until,
            repo_dir=str(directory),
            reason=(
                f"window is inverted: since={since.isoformat()} "
                f"is after until={until.isoformat()}"
            ),
        )

    # Asked before `log`, and separately, so that "this is not a git
    # repository" is reported as itself. `git log` in a non-repository fails
    # too, but with a message about the *log* — which sends whoever reads the
    # D+1 report looking for a broken query instead of a missing checkout.
    ok, output = _run(["rev-parse", "--git-dir"], directory)
    if not ok:
        return GitActivity(
            available=False,
            since=since,
            until=until,
            repo_dir=str(directory),
            reason=output,
        )

    start = datetime.combine(since, datetime.min.time(), tzinfo=KST)
    end = datetime.combine(until, datetime.max.time(), tzinfo=KST)

    ok, output = _run(
        [
            "log",
            f"--since={start.isoformat()}",
            f"--until={end.isoformat()}",
            f"--format={_LOG_FORMAT}",
            "--name-only",
            "--no-merges",
            "--date-order",
        ],
        directory,
    )
    if not ok:
        return GitActivity(
            available=False,
            since=since,
            until=until,
            repo_dir=str(directory),
            reason=output,
        )

    return GitActivity(
        available=True,
        since=since,
        until=until,
        repo_dir=str(directory),
        commits=_parse_records(output),
    )
