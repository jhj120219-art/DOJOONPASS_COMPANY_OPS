"""One operational log line, written safely (BUG-6 and its siblings).

Every log this system writes is append-only, one record per line, prefixed
with an ISO timestamp — `collector/runtime.py`, `agent/agent.py` and
`app/runner.py` each grew their own copy of that idiom. This module is the
one copy, and it exists mainly so that the *escaping* below cannot be
forgotten by one writer while another remembers it.

Why a top-level module rather than a home inside some package: the three
callers live in `collector/`, `agent/` and `app/`, and the dependency graph
is strictly layered (`app` is the composition root and depends on everything;
nothing depends on `app`). Putting the helper in any of them would either
invert that or force `collector` to import `app`. This has no imports of its
own beyond the standard library, so it sits below every package and adds no
edge that could form a cycle. `review_cli.py` is the existing precedent for
a top-level module under `src/`.

What is deliberately NOT here: the decision of *what* to log. Each caller
keeps its own vocabulary (`ACCEPTED`, `LATE_UPDATE`, `AGENT ... START`) and
its own log path, because those answer to different specs.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Secret-shaped content. The first three patterns are byte-for-byte the ones
# tests/test_repository_hygiene.py::test_no_secret_material_in_any_tracked_file
# already enforces over tracked files; a test asserts the two lists stay in
# step. The rest cover the shapes most likely to be pasted into a work note:
# a private key block, a GitHub token, and an `.env`-style assignment.
#
# These live here rather than in `agent/signals.py`, where they started,
# because two different consumers need them and only one of them is the
# Agent: `signals.py` refuses secret-shaped *Signal content*, and this module
# redacts secret-shaped *log output*. `agent` already imports `oplog`, so
# moving them down adds no dependency edge; the reverse would have made this
# module import `agent` and close a cycle. `agent.signals` re-exports both
# names, so its own callers and tests are unaffected.
# Longest namespace prefix a variable name may carry before the keyword.
# It exists only to bound the quantifier below; see `_NAMESPACE` for why an
# unbounded one was a denial of service.
MAX_SECRET_NAME_PREFIX = 40

# The optional namespace in front of `API_KEY` / `PASSWORD` / ….
#
# No leading `\b`: the realistic shape is `NOTION_API_TOKEN=...`, and `_` is a
# word character, so `\bAPI` never matches inside it. The prefix is what lets
# a namespaced variable name match.
#
# **Bounded, and the bound is the whole point.** As `[A-Za-z0-9_]*` this was
# a quadratic-backtracking denial of service: the quantifier is unanchored, so
# for a run of N word characters that never reaches the keyword the engine
# tries every start position against every prefix length. Measured on this
# machine before the bound, against a plain run of `A`s — the shape a summary
# or a blocker takes, and `validate_event()` bounds neither:
#
#     n= 1,000     32 ms
#     n= 2,000    137 ms
#     n= 4,000    552 ms
#     n= 8,000  2,211 ms       (double the input, quadruple the time)
#
# `redact()` is on the path of every operational log line
# (`append_line()`), every Dashboard payload string
# (`controltower/dashboard.to_payload()`), and every rejected-Event error
# message — and `validate_event()` echoes the value it rejected back into
# that message as `{value!r}`. One Event field from another Desktop is
# therefore enough to spend minutes of CPU per run, once per line, forever.
#
# 40 characters is longer than every namespaced name this project has
# (`NOTION_API_TOKEN` is 16), and the bound makes the scan linear: the engine
# tries at most 41 prefix lengths per start position instead of N.
_NAMESPACE = r"[A-Za-z0-9_]{0,%d}" % MAX_SECRET_NAME_PREFIX

# A PEM block, **body included**.
#
# This pattern used to stop at the banner — `-----BEGIN [A-Z ]*PRIVATE
# KEY-----` and nothing more. Every other pattern here ends in a character
# class that swallows the value itself, so only this one left its secret on
# the line: `redact()` returned `[REDACTED]` followed by the entire key.
# Measured on HEAD:
#
#     redact("-----BEGIN RSA PRIVATE KEY-----\\nMIIEowIBAAKC...\\n-----END ...")
#     -> "[REDACTED]\\nMIIEowIBAAKC...\\n-----END RSA PRIVATE KEY-----"
#
# A private key reaching a log is the worst of the seven shapes to miss, and
# it was the only one being missed.
#
# Two branches, tried in order:
#
#   1. through the matching END marker, which is the whole block and the
#      ordinary case. Lazy and bounded — an unbounded `[\s\S]*?` searching
#      for an END that is not there would rescan the tail of every log line
#      that mentions a private key.
#   2. banner plus whatever base64-shaped material follows, for a block that
#      was truncated before its END marker (`bounded()` cuts at 600
#      characters, so this is not hypothetical). Over-matching here is the
#      documented posture: the banner alone is already conclusive.
#
# `\\` is in the second class because `one_line()` runs **before** `redact()`
# and has already turned the block's newlines into literal `\n` two-character
# sequences.
_PEM_BODY = 4096
_PEM_BLOCK = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"(?:"
    r"[\s\S]{0,%d}?-----END [A-Z ]*PRIVATE KEY-----"
    r"|"
    r"[A-Za-z0-9+/=\s\\]{0,%d}"
    r")"
) % (_PEM_BODY, _PEM_BODY)

SECRET_PATTERNS = (
    r"\bntn_[A-Za-z0-9]{10,}",
    r"\bsecret_[A-Za-z0-9]{10,}",
    r"Bearer\s+[A-Za-z0-9._-]{20,}",
    _PEM_BLOCK,
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    _NAMESPACE + r"(?:API|ACCESS|AUTH|SECRET)[_-]?(?:KEY|TOKEN)\s*[=:]\s*\S+",
    _NAMESPACE + r"(?:PASSWORD|PASSWD|CLIENT[_-]?SECRET)\s*[=:]\s*\S+",
)
# These two deliberately over-match: a work note reading "auth token: rotated"
# is refused even though it carries no secret. A refusal is visible, names its
# reason, leaves the Signal on disk, and costs one rephrase; the opposite
# error writes a credential into Company History and a GitHub backup, where it
# is permanent. The asymmetry is the point.
SECRET_RE = re.compile("|".join(SECRET_PATTERNS), re.IGNORECASE)


def redact(text: str) -> str:
    """Replace any secret-shaped run in `text` with `[REDACTED]`."""
    return SECRET_RE.sub("[REDACTED]", text)

# Upper bound on any error string written to an operational log.
#
# Deliberately larger than `notion/transport.py`'s `_MAX_ERROR_DETAIL` (400),
# which caps a Notion *response body* alone. A NotionAPIError message is that
# body plus a prefix, so a 400-byte bound here would re-cut a string another
# layer already cut, and an operator would lose the tail of the explanation
# without being able to tell which of the two limits took it.
MAX_LOG_ERROR = 600


def _is_line_breaking(char: str) -> bool:
    """True for anything that would end a line for some reader of this log.

    C0 controls plus the four non-C0 boundaries `str.splitlines()` honours
    (`\\x85`, `\\u2028`, `\\u2029` — `\\x1c`-`\\x1e` are already C0).
    """
    return char < " " or char in ("\x7f", "\x85", "\u2028", "\u2029")


#: The nine Unicode controls that reorder a line without ending one.
#:
#: `_is_line_breaking()` above closes forging a **second** line. These forge
#: the content of the line they are on: an override or an isolate makes a run
#: of text render in an order the bytes do not have, so the same
#: `collector.log` line an operator reads as one verdict was recorded as
#: another. That is CVE-2021-42574's shape aimed at an operator's log rather
#: than at a compiler, and `event_id` — which docs/02 constrains only to
#: "present and non-null" — is the field it arrives in.
#:
#: Measured on HEAD: all nine passed through `one_line()` untouched, so the
#: bytes `REJECTED EVT-1<RLO>DETPECCA<LRO>` render as
#: `REJECTED EVT-1ACCEPTED`. Escaping makes them visible instead, which is
#: this module's stated posture — "escaped, not stripped ... keeps the real
#: value recoverable".
#:
#: Written as escapes rather than as the characters themselves. A literal
#: here would be nine invisible codepoints in a source file no reviewer can
#: see — the defect this constant exists to expose, spelled in its own file.
#:
#: U+200E / U+200F (LRM / RLM) are deliberately absent. They are marks rather
#: than overrides: they nudge the ordering of neighbouring runs and cannot by
#: themselves reverse one, and escaping every directional hint would start
#: mangling values merely written in a right-to-left script.
_BIDI_CONTROL = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def _escape(char: str) -> str:
    """`char` unchanged unless it can end **or reorder** a line, else a
    Python-style escape.

    `\\xNN` only for a byte-sized codepoint: `f"\\\\x{0x2028:02x}"` would
    render U+2028 as `\\x2028`, which reads as `\\x20` followed by the
    literal "28" — an escape that means something other than the character
    it stands for is worse than none.
    """
    if not (_is_line_breaking(char) or char in _BIDI_CONTROL):
        return char
    code = ord(char)
    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


def one_line(value: object) -> str:
    """Render a value so it cannot become a second log line.

    Nearly every field these logs interpolate reaches this system from
    outside it: an Event file arrives over the OneDrive transport, written
    by another Desktop, and `Event.from_json()` does not constrain
    `event_id` to a single line. A newline inside one produced a second,
    fully attacker-authored line indistinguishable from a genuine one.
    Reproduced in `collector.log`, where it is at its worst:

        2026-08-11T13:40:02+09:00 ACCEPTED X
        2026-01-01T00:00:00+09:00 ACCEPTED EVT-TOTALLY-FINE     <- forged

    An operator reads these logs to decide whether the system is healthy, so
    a forged line is not cosmetic — it is a false statement about what the
    system did, and the example above claims an Event was accepted that
    never existed.

    Escaped, not stripped: `\\n` keeps the real value recoverable, so
    docs/04 §55's "event_id를 기록한다" stays honest.

    Scope is every character that can end a line, not just `\\n`. Python's
    own `str.splitlines()` — which is how this repository's tests and an
    operator's tooling read these files back — also breaks on `\\v`, `\\f`,
    `\\x1c`-`\\x1e`, `\\x85`, `\\u2028` and `\\u2029`, so escaping newlines
    alone would leave seven other ways to forge the same line. Remaining C0
    controls go too: a lone `\\r` rewrites the line from column zero on most
    terminals, and `\\x08`/`\\x1b` can erase or recolour what precedes them.

    Backslash itself is deliberately NOT doubled — on this Windows-first
    project error strings routinely carry paths, and `C:\\\\Users\\\\...` in
    every log line costs more readability than the residual ambiguity
    (a literal "\\n" in a value vs. an escaped newline) is worth. Neither
    can produce a second line, which is the property that matters.

    Rejecting such an Event outright would be the deeper fix, but that
    changes what Collector accepts — an Event Schema contract decision
    (BACKLOG A-15). Escaping needs no approval and removes the forgery
    either way.
    """
    named = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    return "".join(named.get(char) or _escape(char) for char in str(value))


def bounded(text: str) -> str:
    """`text`, capped at a length this system chose rather than one an
    exception happened to have.

    Applied to every error string written to a log, so the rule is "nothing
    logged is unbounded" rather than "some fields are bounded by whichever
    layer produced them". The `except Exception` paths are why it must be
    unconditional: they catch anything at all, and an exception carrying,
    say, a decoded multi-megabyte payload would put that payload on disk —
    once per Event, on every run, until the failure clears.
    """
    if len(text) > MAX_LOG_ERROR:
        return text[:MAX_LOG_ERROR] + "..."
    return text


def bounded_error(exc: BaseException) -> str:
    """An exception rendered for a log: type name, message, bounded."""
    return bounded(f"{type(exc).__name__}: {exc}")


def append_line(log_path: Path, body: str) -> None:
    """One timestamp-prefixed line, appended. The single place this system
    writes an operational log line.

    Three rules live here so no caller has to remember them:

      * `one_line()` is applied to the whole body, so "one call, one line"
        holds no matter what a caller interpolated — including a field added
        years from now by someone who never read this docstring.
      * `redact()` is applied for the same reason, and it earned its place:
        `app/runner.py` began logging the *reason* a Notion sync failed, and
        that reason is a remote response body. Notion's own JSON cannot carry
        the API token — it travels in a request header — but a proxy or
        captive portal answering in Notion's place is free to echo request
        headers back, which is a case `notion/transport.py` already
        anticipates elsewhere. Measured: a 502 page containing
        `Authorization: Bearer ntn_...` put the token straight into
        notion_sync.log, violating docs/04 §56.
      * logging must never be the thing that fails a run. An unwritable log
        path costs visibility, never data, so `OSError` is swallowed.

    Redaction over-matches by design (see `SECRET_PATTERNS`). In a log that
    is the correct direction: an over-redacted line costs one investigation
    step, an under-redacted one writes a credential to disk.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {redact(one_line(body))}\n")
    except OSError:
        pass
