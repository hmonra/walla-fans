"""Persistence: state snapshot + append-only event log.

Both live under state/ and are committed back to git by the GitHub Actions
workflow, giving a full timeline in the repository history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_FILE = STATE_DIR / "state.json"
EVENTS_FILE = STATE_DIR / "events.jsonl"


class StateStore:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


class EventLog:
    def __init__(self, path: Path = EVENTS_FILE):
        self.path = path

    def append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def load_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        events: list[dict] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events

    def since(self, iso_ts: str) -> list[dict]:
        return [e for e in self.load_all() if e.get("ts", "") >= iso_ts]

    def trim(self, max_lines: int = 5000) -> None:
        """Keep only the last max_lines entries (controls repo growth)."""
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= max_lines:
            return
        self.path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
