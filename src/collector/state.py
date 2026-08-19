"""Persistent SeenEventStore backed by runtime/state/collector_state.json.

Lets Collector remember which event_ids it has already accepted across
process restarts (docs/03_COLLECTOR_SPEC.md sections 36-38), without
replacing InMemorySeenEventStore — that stays the lightweight test/dev
double. This is the durable option a caller can inject instead.

This module owns exactly one thing: duplicate-check state. It does not
know about History, Notion, Transport, Backup, or Scheduling, and it does
not touch any file but its own state file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .result import CollectorError
from .runtime import PROJECT_ROOT
from .seen_store import SeenEventStore

DEFAULT_STATE_PATH = PROJECT_ROOT / "runtime" / "state" / "collector_state.json"


class CollectorStateError(CollectorError):
    """Raised when collector_state.json exists but cannot be read as valid
    Collector state (bad JSON, wrong shape, wrong field types).

    Never raised for a simply-missing file — that case starts an empty
    state instead. Raising a specific, typed error here (rather than
    letting a raw JSONDecodeError/KeyError escape) is the "명확한 오류"
    this Phase requires: it lets the caller decide what to do about a
    damaged state file instead of the failure looking like an arbitrary
    crash somewhere inside Collector's normal per-file processing.
    """


class PersistentSeenEventStore(SeenEventStore):
    """SeenEventStore that survives process restarts via a JSON state file.

    Every mark_seen()/unmark_seen()/record_run() call saves the full state
    atomically (temp file + os.replace) before returning, so a crash right
    after any of them never leaves a torn file and never loses an update that
    already reported success.

    unmark_seen() persists for the same reason mark_seen() does: the rollback
    it performs must survive a crash, or the retry it exists to enable would
    be lost with it.
    """

    def __init__(self, state_path: Path | None = None):
        self.state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
        self._seen_ids: set[str] = set()
        self.last_run: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return

        try:
            raw = self.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise CollectorStateError(
                f"collector state file is corrupted: {self.state_path} ({exc})"
            ) from exc

        if not isinstance(data, dict):
            raise CollectorStateError(
                f"collector state file must contain a JSON object: {self.state_path}"
            )

        ids = data.get("processed_event_ids", [])
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise CollectorStateError(
                "collector state file has an invalid processed_event_ids field: "
                f"{self.state_path}"
            )

        last_run = data.get("last_run")
        if last_run is not None and not isinstance(last_run, str):
            raise CollectorStateError(
                f"collector state file has an invalid last_run field: {self.state_path}"
            )

        self._seen_ids = set(ids)
        self.last_run = last_run

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_run": self.last_run,
            "processed_event_ids": sorted(self._seen_ids),
        }
        fd, tmp_path = tempfile.mkstemp(
            dir=self.state_path.parent, prefix=".tmp-", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                # Durability, not only atomicity — see reporter/local_output.py.
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.state_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def is_seen(self, event_id: str) -> bool:
        return event_id in self._seen_ids

    def mark_seen(self, event_id: str) -> None:
        self._seen_ids.add(event_id)
        self._save()

    def unmark_seen(self, event_id: str) -> None:
        """Roll back a mark_seen() whose Event was never consumed.

        See SeenEventStore.unmark_seen() for why this exists. Persists
        immediately, like mark_seen(), so the rollback survives a crash right
        after it. A no-op (and no write) when the id is not present, so it is
        safe to call unconditionally on a failure path.
        """
        if event_id not in self._seen_ids:
            return
        self._seen_ids.discard(event_id)
        self._save()

    def record_run(self, timestamp: str | None = None) -> None:
        """Record that a Collector run happened, for the state file's `last_run`.

        Docstring corrected: it used to say no caller existed yet. One does —
        `app/runner.py` calls this immediately after the Collector step, once
        per Runner execution, with the run's `now`. The stale wording invited
        the same mistake as the one already corrected in
        `backup/working_copy.scan_for_secrets()`: reading a live call site as
        dead code.

        `last_run` is written but never read back by anything, so it is a
        record for a human inspecting collector_state.json, not a value the
        Runtime branches on.
        """
        self.last_run = timestamp or datetime.now().astimezone().isoformat(timespec="seconds")
        self._save()
