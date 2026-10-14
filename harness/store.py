"""Run directory: task results, artifacts, and an append-only event log."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "results").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._t0 = time.time()

    # task results (used for resume)
    def result_path(self, task_id: str) -> Path:
        return self.root / "results" / f"{task_id}.json"

    def save_result(self, task_id: str, output: Any) -> None:
        self.result_path(task_id).write_text(json.dumps(output, indent=2), encoding="utf-8")

    def load_result(self, task_id: str) -> Any:
        return json.loads(self.result_path(task_id).read_text(encoding="utf-8"))

    def has_result(self, task_id: str) -> bool:
        return self.result_path(task_id).exists()

    # control: a person can pause, stop a task, or change a task's model tier between attempts
    def control(self) -> dict:
        file = self.root / "control.json"
        try:
            return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def set_control(self, **changes: Any) -> dict:
        with self._lock:
            data = {**self.control(), **changes}
            (self.root / "control.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    # artifacts (the deliverables)
    def write_artifact(self, name: str, text: str) -> Path:
        path = self.root / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # event log
    def log(self, event: str, task_id: str = "", **data: Any) -> None:
        record = {"t": round(time.time() - self._t0, 2), "event": event, "task": task_id, **data}
        with self._lock:
            with (self.root / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
