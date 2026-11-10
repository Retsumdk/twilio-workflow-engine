"""Append-only, hash-chained journal: the single source of truth for run state."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .errors import StateError

__all__ = ["FORMAT_TAG", "FORMAT_VERSION", "Journal", "Run", "verify_journal"]

FORMAT_TAG = "twilio-workflow-engine/journal"
FORMAT_VERSION = 1


def _hash(prev: str, record: dict[str, Any]) -> str:
    payload = json.dumps({"prev": prev, "record": record}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Run:
    run_id: str
    workflow_name: str
    to_number: str
    steps: list[dict[str, Any]]
    context: dict[str, Any]
    created_at: float
    current: int = 0
    status: str = "running"
    step_results: dict[int, dict[str, Any]] = field(default_factory=dict)
    skipped: set[int] = field(default_factory=set)
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.status in ("completed", "failed")


class Journal:
    """JSONL journal with a SHA-256 hash chain over seq|prevHash|record."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._records: list[dict[str, Any]] = []
        self._runs: dict[str, Run] = {}
        self._hashes: list[str] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StateError(f"{self.path}:{lineno}: invalid JSON: {exc}") from exc
                if not isinstance(entry, dict) or "record" not in entry \
                        or "hash" not in entry:
                    raise StateError(f"{self.path}:{lineno}: missing record/hash fields")
                self._records.append(entry)
        self._replay()

    def _replay(self) -> None:
        prev = ""
        for lineno, entry in enumerate(self._records, start=1):
            if entry.get("format") != FORMAT_TAG or entry.get("version") != FORMAT_VERSION:
                raise StateError(f"{self.path}:{lineno}: unknown journal format/version")
            expected = _hash(prev, entry["record"])
            if expected != entry["hash"]:
                raise StateError(
                    f"{self.path}:{lineno}: hash mismatch (journal tampered or corrupt)")
            self._hashes.append(expected)
            self._apply(entry["record"])
            prev = expected

    def _apply(self, record: dict[str, Any]) -> None:
        event = record.get("event")
        run_id = record.get("run_id")
        if not isinstance(run_id, str):
            raise StateError(f"journal record has invalid run_id: {run_id!r}")
        if event == "run_started":
            self._runs[run_id] = Run(run_id=run_id, workflow_name=record["workflow_name"],
                                     to_number=record["to_number"], steps=record["steps"],
                                     context=record["context"], created_at=record["ts"])
        else:
            run = self._runs.get(run_id)
            if run is None:
                raise StateError(f"journal references unknown run {run_id!r}")
            if event == "step_completed":
                run.step_results[record["index"]] = record["result"]
                run.current = max(run.current, record["index"] + 1)
            elif event == "step_skipped":
                run.skipped.add(record["index"])
                run.current = max(run.current, record["index"] + 1)
            elif event == "run_completed":
                run.status = "completed"
            elif event == "run_failed":
                run.status = "failed"
                run.error = record.get("error")

    def append(self, record: dict[str, Any]) -> None:
        prev = self._hashes[-1] if self._hashes else ""
        digest = _hash(prev, record)
        entry = {"format": FORMAT_TAG, "version": FORMAT_VERSION, "hash": digest, "record": record}
        line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self._records.append(entry)
        self._hashes.append(digest)
        self._apply(record)

    def runs(self) -> dict[str, Run]:
        return self._runs

    def verify(self) -> list[str]:
        """Return a list of integrity problems (empty list = intact)."""
        problems: list[str] = []
        prev = ""
        for lineno, entry in enumerate(self._records, start=1):
            try:
                expected = _hash(prev, entry["record"])
            except (KeyError, TypeError):
                problems.append(f"{self.path}:{lineno}: malformed record")
                prev = ""
                continue
            if expected != entry.get("hash"):
                problems.append(f"{self.path}:{lineno}: hash mismatch")
            prev = entry.get("hash", "")
        return problems


def verify_journal(path: str) -> list[str]:
    """Lenient, file-level integrity check: reads every line, reports problems
    with line numbers instead of raising on the first bad record."""
    problems: list[str] = []
    if not os.path.exists(path):
        return [f"{path}: journal does not exist"]
    prev = ""
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"{path}:{lineno}: invalid JSON: {exc}")
                continue
            if not isinstance(entry, dict) or "record" not in entry or "hash" not in entry:
                problems.append(f"{path}:{lineno}: missing record/hash fields")
                continue
            if entry.get("format") != FORMAT_TAG or entry.get("version") != FORMAT_VERSION:
                problems.append(f"{path}:{lineno}: unknown journal format/version")
            try:
                expected = _hash(prev, entry["record"])
            except (KeyError, TypeError):
                problems.append(f"{path}:{lineno}: malformed record")
                prev = ""
                continue
            if expected != entry["hash"]:
                problems.append(f"{path}:{lineno}: hash mismatch (chain broken here)")
            prev = entry["hash"]
    return problems
