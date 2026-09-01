"""File-based mutual exclusion (docs/07_SCHEDULER_CATCHUP_SPEC.md sections 24-28).

Prevents two run_once() calls (e.g. a manual/startup trigger overlapping
with a scheduled one, section 23) from processing the same dates at once.
This is a lock *file* taken and released around one synchronous call — not
a resident process, not a Thread, not polling.

Acquisition is a single atomic `os.open(..., O_CREAT | O_EXCL)`. That is
the whole basis of section 25's invariant ("Lock으로 하나의 Runner만
실행되게 한다"): the OS lets exactly one caller create the file, so exactly
one caller can be told it holds the lock. An earlier version checked
`lock_path.exists()` and later wrote through `os.replace()`; nothing joined
those two steps, so runs starting together all passed the check and all
believed they had acquired it, and Windows additionally raised
PermissionError on the contended replace. Both failure modes are gone with
O_EXCL — a loser simply gets FileExistsError, which is exactly the "someone
else holds it" signal.

Section 27 requires checking whether the recorded process is actually
still running ("단순 시간 경과만으로 실행 중인 정상 Process를 강제
종료하지 않는다") rather than deciding staleness from elapsed time alone.
`_is_process_running()` implements that check with stdlib-only calls (no
psutil): `tasklist` on Windows (this project's target OS, per
DOJOONPASS_COMPANY_OPS environment), `os.kill(pid, 0)` elsewhere.
Taking over a stale lock is itself made safe by `_take_over_stale()`, which
removes it only if it has not changed since it was read — otherwise two
runs that both judged the same lock stale would delete each other's fresh
lock and both proceed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = PROJECT_ROOT / "runtime" / "locks" / "company_ops.lock"


def _long_path(path: Path) -> Path:
    """A Path usable even past Windows' 260-character MAX_PATH.

    Lock filenames are not chosen by this module, so a legal-but-long name
    (or one with characters like `:` that create an NTFS alternate data
    stream) must not be mistaken for an acquisition failure. The `\\\\?\\`
    prefix lifts the limit, but only for an absolute, backslash-separated
    path, so it is built from the resolved path rather than the raw input.

    An input that *already* carries the prefix is returned unchanged. This
    used to be a documented non-case ("`Path.resolve()` strips an existing
    `\\\\?\\` prefix, so there is nothing to special-case") — that claim is
    false on the Python this project runs (verified on 3.13: `resolve()`
    preserves the prefix). The prefixed path then still began with `\\\\`,
    so it took the UNC branch below and came out as
    `\\\\?\\UNC\\?\\C:\\...` — not a path any Windows API accepts. Every
    `os.open`/`unlink`/`read_text` on it fails with OSError, which
    `try_acquire_lock()` reports as "someone else holds the lock": a Runner
    given an already-prefixed `lock_path` could never acquire it and would
    skip every single run, silently, forever.
    """
    if sys.platform != "win32":
        return path
    resolved = path.resolve()
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        return resolved
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def _read_lock(lock_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(_long_path(lock_path).read_text(encoding="utf-8"))
    # `RecursionError` is what `json.loads()` answers a deeply nested file
    # with, and it is a `RuntimeError` rather than a `ValueError` — so the
    # conversion below, which is the whole of what this promises about a
    # file it cannot read, used to be skipped for that one shape. One home
    # for the reasoning: `ADeeplyNestedStateFileReadsLikeAnyOtherCorruptOneTests`
    # (C65), which also holds the roster this is one of nine entries in.
    except (OSError, ValueError, RecursionError):
        return None


#: What a recorded `image_name` may look like before it is passed to
#: `tasklist`. A bare filename, because that is what `os.path.basename()`
#: produces and what the `IMAGENAME` filter matches.
#:
#: The lock file is written by this module, but it is a file on disk: a
#: hand-edited, restored, or truncated one can hold anything. A value this
#: rejects is dropped and the probe falls back to asking about the pid
#: alone -- the behaviour every lock written before this field existed
#: already gets. It is passed as one element of an argv list, never through
#: a shell, so this is about keeping the filter *meaningful* rather than
#: about injection.
_IMAGE_NAME_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,120}\Z")


def _is_process_running(pid: object, image_name: object = None) -> bool:
    """docs/07_SCHEDULER_CATCHUP_SPEC.md §27: Lock에 기록된 Process가
    실제로 실행 중인지 확인한다.

    `image_name` is the executable the lock's holder was running, recorded
    beside its pid at acquisition. It exists to answer **pid reuse**, which
    this function could not see and which wedges the Runner permanently.

    Measured on this machine before the field existed. A lock left by a
    killed Runner, holding a pid Windows had since handed to an unrelated
    process:

        lock:  {"process_id": 1336, "created_at": "2020-01-01T00:00:00+09:00"}
        1336 now belongs to svchost.exe
        _is_process_running(1336)  ->  True
        try_acquire_lock(...)      ->  False

    False, from then on, every run. The lock is never judged stale, the
    Runner skips every trigger, and only a person deleting the file undoes
    it. The `created_at` is five years old and nothing looks at it, by
    design -- §27 forbids deciding staleness from elapsed time.

    This is reachable without anything exotic: `ExecutionTimeLimit` expiring
    (docs/07 §55 registers one), a power cut, a machine reset. Each leaves
    the lock behind; a reboot then reassigns low pids immediately.

    **The direction of the change is one-way, which is why it is safe.**
    Adding a filter can only turn a "running" answer into "not running", and
    only when the pid belongs to a process running a *different executable*
    -- which by construction cannot be the holder that wrote this lock. A
    genuinely live holder still matches its own image name and is still
    reported running, so the BUG-18/BUG-20 condition (two Runners at once)
    is not reachable through this. What remains unfixed is a pid reused by
    another process with the same image name; that is narrower than "any
    process at all", and `ops_status.py`'s LOCK_STUCK_AFTER_HOURS line still
    reports it.

    A lock without the field -- written before it existed, or by a Python
    with no `sys.executable` -- falls back to asking about the pid alone,
    exactly as before.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        filters = ["/FI", f"PID eq {pid}"]
        if isinstance(image_name, str) and _IMAGE_NAME_RE.match(image_name):
            filters += ["/FI", f"IMAGENAME eq {image_name}"]
        try:
            result = subprocess.run(
                ["tasklist", *filters, "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            # BUG-54. The probe learned nothing -- it did not learn that the
            # process is gone. Answering False here means "stale", and stale
            # means the caller unlinks a live holder's lock and takes the
            # critical section: two Runners at once, which is the BUG-18/BUG-20
            # condition the O_EXCL rewrite was written to remove. That rewrite
            # does not help, because the second run legitimately unlinks the
            # first one's lock and creates its own.
            #
            # Reachable without anything unusual. `tasklist` enumerates every
            # process under a 5 s timeout, and the moment it is most likely to
            # be slow -- heavy load -- is the moment two scheduled runs are
            # most likely to overlap. A stripped PATH or a hardened image with
            # no `tasklist` fails this way permanently.
            #
            # The direction is the whole thing, and the costs are not
            # symmetric: skipping a run costs one cycle, and the next run
            # catches up (docs/07 §20). Taking a live holder's lock costs
            # Company History -- measured at up to 36% of History Candidates
            # lost under that condition.
            #
            # This is not a new policy. It is the answer this same function
            # already gives on POSIX, four lines below: `PermissionError` from
            # `os.kill(pid, 0)` means "a process is there and it is not ours",
            # and that arm returns True. Windows was the only branch that
            # turned "I cannot tell" into "it is gone".
            #
            # Why the objection recorded against this no longer holds. It was
            # that a probe which keeps failing would leave a lock nobody can
            # ever reclaim, silently. Two checks written since then make that
            # condition loud instead: `ops_status.py`'s
            # LOCK_STUCK_AFTER_HOURS line reads `lock_held_since()` (which
            # answers, on this path, precisely because the holder now counts
            # as alive) and names this shape in as many words, and the
            # Runner/Agent silence checks raise a run that stops happening
            # after SILENT_AFTER_DAYS. A permanently unreclaimable lock is now
            # reported; a broken mutual exclusion still would not be.
            return True
        return result.returncode == 0 and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _take_over_stale(lock_path: Path, observed: dict[str, Any] | None) -> bool:
    """Remove a lock judged stale, but only if it has not changed since.

    Two runs can both read the same stale lock and both decide to take it
    over. If each then unlinked unconditionally, the second unlink would
    delete the *fresh* lock the first had just created, and both would
    believe they hold it. Re-reading and comparing before the unlink means a
    lock that someone else already replaced is left alone.
    """
    if _read_lock(lock_path) != observed:
        return False
    try:
        os.unlink(_long_path(lock_path))
    except OSError:
        return False
    return True


def try_acquire_lock(lock_path: Path, *, now: datetime) -> bool:
    """Return True if the lock was acquired, False if another run holds it.

    Acquisition is a single atomic `os.open(..., O_CREAT | O_EXCL)`: the OS
    guarantees that exactly one caller can create the file, so exactly one
    caller can ever be told True for a given lock.

    This replaces a check-then-write sequence (`if lock_path.exists()` …
    later … `os.replace(tmp, lock_path)`) that was not atomic. Nothing joined
    the two steps, so several runs starting together all passed the existence
    check and all "acquired"; `os.replace()` overwrites unconditionally, so
    the last writer merely won the file while every caller had already been
    told True. Measured before this change (8 processes x 12 trials): 2-3
    simultaneous holders in every trial, and zero clean denials. On Windows a
    contended `os.replace()` additionally raised PermissionError, so a
    contended acquisition could crash the Runner instead of returning False —
    O_CREAT|O_EXCL has no such failure mode, it simply reports FileExistsError
    which is exactly the "someone else holds it" signal.

    docs/07_SCHEDULER_CATCHUP_SPEC.md §25 states the invariant this restores:
    "Lock으로 하나의 Runner만 실행되게 한다" — one system-wide holder.
    §27's rule is unchanged: a lock whose recorded process is no longer
    running is stale and may be taken over, never a lock judged only by
    elapsed time.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"process_id": os.getpid(), "created_at": now.isoformat(timespec="seconds")}

    # The executable this process is running, so a later run can tell "my
    # pid is still alive" from "something else now has my old pid". See
    # `_is_process_running()` for the measurement and for why adding this
    # can only make the probe more accurate, never less safe.
    #
    # Omitted rather than guessed at when `sys.executable` is empty (an
    # embedded interpreter): the probe then behaves exactly as it did before
    # this field existed.
    image = os.path.basename(sys.executable or "")
    if image:
        payload["image_name"] = image

    # At most two passes: the second exists only to retry once after a stale
    # lock was cleared. A further collision means another run got there first.
    for _ in range(2):
        try:
            fd = os.open(_long_path(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            observed = _read_lock(lock_path)
            pid = observed.get("process_id") if observed is not None else None
            image = observed.get("image_name") if observed is not None else None
            if _is_process_running(pid, image):
                return False
            # Unparseable lock, or recorded process not running -> stale (§27).
            if not _take_over_stale(lock_path, observed):
                return False
            continue
        except OSError:
            return False

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except BaseException:
            try:
                os.unlink(_long_path(lock_path))
            except OSError:
                pass
            raise

        # Confirm the lock on disk is still ours. A run that was concurrently
        # taking over a stale lock could have unlinked and recreated it in the
        # instant between our create and our write; if so, it holds the lock
        # and we do not.
        confirmed = _read_lock(lock_path)
        if confirmed is None or confirmed.get("process_id") != os.getpid():
            return False
        return True

    return False


def is_locked(lock_path: Path) -> bool:
    """Is a live process holding this lock right now? Read-only.

    Deliberately not `try_acquire_lock()`. That call *competes* for the
    lock: it creates the file when free and can take over one it judges
    stale. A status view must never do either — an operator running
    `ops_status.py` while a Runner works must not be able to disturb it, and
    the docstring of that script promises exactly that.

    So this only reads: the lock file exists, it parses, and the process it
    records is still running. Anything else is False, including a lock left
    behind by a dead process — which is the same judgement
    `try_acquire_lock()` makes (§27), reached without acting on it.

    **`image_name` is part of that judgement, and this function was left out
    of the change that made it so.** BUG-54's fix passed the recorded
    executable name to `_is_process_running()` from `try_acquire_lock()`,
    `lock_held_since()` and `stale_lock_cannot_be_cleared()` — the last of
    those carries the reason in a comment ("a narrower probe there than here
    would make it silently miss exactly the locks that had become
    reclaimable"). This one kept asking about the pid alone, so the sentence
    above claiming it reaches the same judgement was false for precisely the
    case that fix exists for. Measured on this machine, one lock file:

        {"process_id": 1336, "image_name": "python.exe"}   1336 is svchost.exe now

        lock_held_since    -> None      nobody holds it
        try_acquire_lock   -> True      took it over: the holder is dead
        is_locked          -> True      **a live process is holding it**

    What that costs is not cosmetic. `ops_status.py` calls this in two
    places, both to soften an ATTENTION line about work that may have been
    lost — unrendered KEEP candidates, orphaned Events — with "(Runner 실행
    중 — 완료 후 재확인 권장)". The comment at the second one states the
    stake outright: "a real loss hidden behind 'probably just running' is
    far worse than a false alarm". A reused pid attaches that reassurance
    permanently, because the stale lock never goes away on its own.

    Adding the filter can only move an answer from True to False, and only
    when that pid is running a *different* executable — which cannot be the
    holder that wrote the lock. A live Runner keeps matching its own name,
    so this cannot begin reporting False about a run that is really going.
    A lock with no `image_name` (written before the field existed, and
    present on deployed machines) is judged on the pid alone, as before.

    Nothing here decides staleness or timing. `lock_held_since()` answers
    the "for how long" question separately.
    """
    observed = _read_lock(lock_path)
    if observed is None:
        return False
    return _is_process_running(
        observed.get("process_id"), observed.get("image_name")
    )


def lock_held_since(lock_path: Path) -> datetime | None:
    """When the live holder acquired this lock, or None if nobody holds it.

    Reads `created_at`, and `process_id`/`image_name` only to ask whether
    anybody still holds the lock. None also covers a lock whose `created_at`
    is missing or unparseable: a time this cannot read is not a time to
    report.

    **What this used to be for, and what changed under it (C138).** This
    docstring described the pid-reuse hole — "once Windows reassigns that
    number to something unrelated the lock looks held forever" — and
    deferred closing it: *"Making the identity check exact means widening
    the lock file's contract, which is a decision."*

    That premise was wrong, in the way BACKLOG E-11 keeps describing.
    docs/07 §26 says "Lock에는 **최소한** 다음 정보를 기록할 수 있다" and then
    lists two fields. It states a minimum, not a schema, so recording the
    holder's executable beside its pid is implementing §27's own question —
    "해당 Process가 실제 실행 중인가?" — rather than changing the contract.
    `_is_process_running()` carries the measurement.

    So this reports "held" for a narrower and truer set of locks than it
    used to, and the pid-reuse case it was written to *mitigate* is now
    mostly gone. It keeps its job for what remains: a lock held implausibly
    long by a process that really is alive.
    """
    observed = _read_lock(lock_path)
    if observed is None or not _is_process_running(
        observed.get("process_id"), observed.get("image_name")
    ):
        return None
    created_at = observed.get("created_at")
    if not isinstance(created_at, str):
        return None
    try:
        return datetime.fromisoformat(created_at)
    except ValueError:
        return None


def stale_lock_cannot_be_cleared(lock_path: Path) -> bool:
    """Is there a lock file that no run will ever be able to take over?

    Read-only, like `is_locked()` and `lock_held_since()`: it removes
    nothing, takes nothing, and changes no attribute.

    The condition is narrow on purpose, and every part of it is required:

        the lock file exists
        the process it records is NOT running  -> §27 says it is stale
        the file is not writable               -> `os.unlink()` will fail

    A stale lock on its own is ordinary — the next run takes it over, which
    is exactly what §27 provides for, so reporting that would be noise. What
    this reports is a stale lock that the takeover *cannot* remove, and that
    is permanent by construction: the recorded process is dead, so nobody
    will ever rewrite the file, and the attribute stops every unlink.

    Why it needed its own detector (BUG-42). `try_acquire_lock()` answers
    False, and False downstream means "another run holds it" — so the Runner
    reads a permanent, unrecoverable condition as routine contention and
    skips, on schedule, forever. Measured: `try_acquire_lock()` False on
    every call, and **both existing detectors blind to it** —
    `is_locked()` False and `lock_held_since()` None, because both key on a
    *live* process and this one is dead. A lock-skipped run also writes no
    manifest (docs/14 §7, deliberately), so nothing else was left to notice.

    Reachable without anyone doing anything unusual: files restored from a
    Windows backup commonly come back read-only, and sync clients and
    antivirus tools both set the attribute.

    Detection only. Whether the Runner may strip an attribute from a file it
    did not create, and whether `try_acquire_lock()` should distinguish
    "contended" from "cannot proceed", are the two decisions BUG-42 records —
    neither is taken here.
    """
    path = _long_path(lock_path)
    observed = _read_lock(lock_path)
    if observed is None and not path.exists():
        return False
    # The same view of "running" the acquirer uses, `image_name` included.
    # These two must agree: this function's whole subject is "a lock
    # `try_acquire_lock()` would judge stale and then fail to remove", and
    # a narrower probe there than here would make it silently miss exactly
    # the locks that had become reclaimable.
    if observed is not None and _is_process_running(
        observed.get("process_id"), observed.get("image_name")
    ):
        return False
    return not os.access(path, os.W_OK)


def release_lock(lock_path: Path) -> None:
    try:
        _long_path(lock_path).unlink()
    except OSError:
        pass
