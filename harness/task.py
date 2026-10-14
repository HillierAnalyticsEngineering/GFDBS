"""Task model for the harness.

A Task is a unit of work with dependencies and an optional gate.
The harness does not care whether `run` calls an LLM or a plain function.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class TaskResult:
    """What a task hands back to the harness."""

    output: Any = None                      # JSON-serializable payload
    spawn: list["Task"] = field(default_factory=list)   # new tasks to add to the plan
    note: str = ""                          # one line for the event log


@dataclass
class Context:
    """What the harness hands to a task."""

    task_id: str
    inputs: dict[str, Any]                  # outputs of the tasks this one depends on
    store: Any                              # harness.Store
    attempt: int = 1
    feedback: list[str] = field(default_factory=list)   # gate errors from the last attempt
    notify: Callable[[str, dict], None] | None = None   # the harness uses this for its live status line
    overrides: dict = field(default_factory=dict)       # what the person changed for this task between attempts, such as {"tier": "deep"}

    def log(self, event: str, **data: Any) -> None:
        """Write an event for this task to the run's event log. The Orchestration view draws these."""
        self.store.log(event, self.task_id, **data)
        if self.notify:
            self.notify(event, data)


@dataclass
class Task:
    id: str
    run: Callable[[Context], TaskResult]
    deps: list[str] = field(default_factory=list)
    kind: str = "function"                  # "function" or "agent" (label only)
    model: str = ""                         # model tier label, shown in the log
    gate: Callable[[Any, Context], list[str]] | None = None   # returns a list of errors
    respawn: Callable[[Any], list["Task"]] | None = None      # on resume: rebuild the tasks this one spawned, from its saved output
    max_attempts: int = 1
    status: Status = Status.PENDING
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
