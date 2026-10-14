"""A small orchestration harness.

Core loop:
  1. Find tasks whose dependencies are done.
  2. Run them in parallel.
  3. Check each result against its gate. Retry with the gate errors as feedback.
  4. Save the result. Add any tasks the result spawned.
  5. Repeat until nothing is ready.

Everything else in this repo is a Task handed to this loop.
"""
from __future__ import annotations

import traceback


class HarnessError(RuntimeError):
    """A crash in the harness or pipeline code itself. Not retried: the agent did nothing wrong."""


def _where(exc: BaseException) -> str:
    """The innermost frame of the traceback that is in this repository, as file:line in function."""
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "site-packages" not in (f.filename or "") and "dist-packages" not in (f.filename or "")]
    if not ours:
        return ""
    f = ours[-1]
    name = f.filename.replace("\\", "/").split("/")
    return f"{'/'.join(name[-2:])}:{f.lineno} in {f.name}"
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from . import console
from .store import Store
from .task import Context, Status, Task, TaskResult


class Orchestrator:
    def __init__(self, store: Store, max_workers: int = 6, resume: bool = False, verbose: bool = True,
                 step: bool = False):
        self.store = store
        self.max_workers = max_workers
        self.resume = resume
        self.verbose = verbose
        self.step = step                       # pause before each batch of ready tasks
        self.tasks: dict[str, Task] = {}
        self.outputs: dict[str, object] = {}

    # ---- plan -----------------------------------------------------------
    def add(self, *tasks: Task) -> None:
        for task in tasks:
            if task.id in self.tasks:
                if self.resume:                         # a resumed task re-added what an earlier resumed task already had
                    self.store.log("skipped_duplicate", task.id)
                    continue
                raise ValueError(f"duplicate task id: {task.id}")
            self.tasks[task.id] = task

    def _ready(self) -> list[Task]:
        ready = []
        for task in self.tasks.values():
            if task.status is not Status.PENDING:
                continue
            missing = [d for d in task.deps if d not in self.tasks]
            if missing:
                raise ValueError(f"{task.id} depends on unknown task(s): {missing}")
            states = [self.tasks[d].status for d in task.deps]
            if any(s in (Status.FAILED, Status.BLOCKED) for s in states):
                task.status = Status.BLOCKED
                self._say("blocked", task)
            elif all(s is Status.DONE for s in states):
                ready.append(task)
        return ready

    # ---- execution ------------------------------------------------------
    def _execute(self, task: Task) -> TaskResult:
        """Run one task, with gate and retries. Runs in a worker thread."""
        if self.resume and self.store.has_result(task.id):
            output = self.store.load_result(task.id)
            spawn = task.respawn(output) if task.respawn else []
            self.store.log("resumed", task.id, spawned=[t.id for t in spawn])
            return TaskResult(output=output, spawn=spawn, note="kept from the earlier attempt")

        if not self.verbose:
            return self._attempts(task, None)
        with console.status(task.id, report=False) as live:      # spinner while the task works
            return self._attempts(task, live)

    def _attempts(self, task: Task, live) -> TaskResult:
        def notify(event: str, data: dict) -> None:
            """Turn task events into the text beside the spinner."""
            if live is None:
                return
            if event == "model_call":
                live.update(f"{task.id}: waiting on {data.get('model', 'model')}")
            elif event == "tool_call":
                live.update(f"{task.id}: tool {data.get('tool', '?')}")
            elif event == "memory_search":
                live.update(f"{task.id}: recalled {data.get('hits', 0)} lesson(s)")

        feedback: list[str] = []
        while True:
            task.attempts += 1
            ctx = Context(
                task_id=task.id,
                inputs={d: self.outputs[d] for d in task.deps},
                store=self.store,
                attempt=task.attempts,
                feedback=feedback,
                notify=notify,
                overrides={"tier": tier} if (tier := self.store.control().get("tier", {}).get(task.id)) else {},
            )
            try:
                result = task.run(ctx)
            except Exception as exc:          # for example, a model that ran out of output tokens
                where = _where(exc)
                self.store.log("attempt_failed", task.id, attempt=task.attempts, error=f"{type(exc).__name__}: {str(exc)[:300]}", where=where,
                               trace=traceback.format_exc()[-3000:])
                self._say("attempt failed", task, f"{type(exc).__name__}: {str(exc)[:100]}" + (f"  at {where}" if where else ""))
                if isinstance(exc, (RecursionError, TypeError, AttributeError, KeyError, IndexError, NameError, AssertionError)):
                    # a defect in the harness or the pipeline, not in the agent's answer: retrying spends model calls on the same crash
                    raise HarnessError(f"{type(exc).__name__}: {str(exc)[:200]} at {where or 'unknown'}. This is a defect in the code, not in the agent's answer. "
                                       f"The traceback is in events.jsonl.") from exc
                if task.attempts >= task.max_attempts:
                    raise
                feedback = [f"Your last attempt failed before finishing: {str(exc)[:300]} "
                            f"Answer again and be more concise."]
                continue
            self.store.log("returned", task.id, note=result.note)
            try:
                errors = task.gate(result.output, ctx) if task.gate else []
            except Exception as exc:          # a gate that cannot evaluate the answer is itself a failed check
                errors = [f"The check could not evaluate this answer: {type(exc).__name__}: {str(exc)[:300]}. "
                          f"Something in it is malformed. Correct it and answer again."]
                self.store.log("gate_error", task.id, attempt=task.attempts, error=f"{type(exc).__name__}: {str(exc)[:300]}")
            if not errors:
                if task.gate:
                    self.store.log("gate_passed", task.id, attempt=task.attempts)
                return result
            self.store.log("gate_failed", task.id, attempt=task.attempts, errors=errors)
            self._say("gate failed", task, "; ".join(errors)[:160])
            if task.attempts >= task.max_attempts:
                raise RuntimeError(f"gate failed after {task.attempts} attempt(s): {errors}")
            feedback = errors
            self._await_control(task)

    def _await_control(self, task: Task) -> None:
        """Between attempts, obey the person: wait while paused, stop if told to, and pick up a tier change.

        control.json in the run folder holds {"paused": bool, "stop": [task ids], "tier": {task id: tier}}.
        The interface writes it. A tier change reaches the task through Context.overrides.
        """
        import time
        waited = False
        while True:
            control = self.store.control()
            if task.id in control.get("stop", []):
                self.store.log("stopped_by_person", task.id, attempt=task.attempts)
                raise RuntimeError("stopped by the person before the next attempt")
            if control.get("paused"):
                if not waited:
                    self.store.log("paused", task.id, attempt=task.attempts)
                    self._say("paused", task, "waiting for you")
                    waited = True
                time.sleep(1.0)
                continue
            if waited:
                self.store.log("resumed_by_person", task.id)
            return

    def run(self) -> dict[str, Task]:
        running: dict[Future, Task] = {}
        self.store.log("plan", tasks=list(self.tasks))
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while True:
                ready = self._ready()
                if ready and self.step:
                    console.ask(f"\n  next: {', '.join(t.id for t in ready)}   [Enter] ")
                for task in ready:
                    task.status = Status.RUNNING
                    self.store.log("started", task.id, kind=task.kind, model=task.model, deps=task.deps)
                    self._say("start", task)
                    running[pool.submit(self._execute, task)] = task
                if not running:
                    break
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    task = running.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:  # the task or its gate gave up
                        task.status = Status.FAILED
                        task.errors.append(str(exc))
                        self.store.log("failed", task.id, error=str(exc), trace=traceback.format_exc())
                        self._say("FAILED", task, str(exc)[:110])
                        if self.verbose and console.hint(str(exc)):
                            console.say(f"  {'':<14} {console.hint(str(exc))}")
                        continue
                    self.outputs[task.id] = result.output
                    self.store.save_result(task.id, result.output)
                    task.status = Status.DONE
                    self.store.log("done", task.id, attempts=task.attempts, note=result.note,
                                   spawned=[t.id for t in result.spawn])
                    self._say("done", task, result.note)
                    self.add(*result.spawn)
        return self.tasks

    # ---- reporting ------------------------------------------------------
    def _say(self, what: str, task: Task, detail: str = "") -> None:
        if not self.verbose:
            return
        label = f"{task.kind}:{task.model}" if task.model else task.kind
        console.say(f"  {what:<14} {task.id:<24} [{label}] {detail}")

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for task in self.tasks.values():
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
