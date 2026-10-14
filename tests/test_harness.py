import threading
import time

from harness import Orchestrator, Status, Store, Task, TaskResult


def ok(value):
    return lambda ctx: TaskResult(value)


def test_dependencies_feed_inputs(tmp_path):
    orch = Orchestrator(Store(tmp_path), verbose=False)
    orch.add(Task("a", ok(2)), Task("b", ok(3)),
             Task("c", lambda ctx: TaskResult(ctx.inputs["a"] * ctx.inputs["b"]), deps=["a", "b"]))
    orch.run()
    assert orch.outputs["c"] == 6


def test_independent_tasks_run_in_parallel(tmp_path):
    barrier = threading.Barrier(2, timeout=5)

    def wait(ctx):
        barrier.wait()          # passes only if both tasks are running at once
        return TaskResult(1)
    orch = Orchestrator(Store(tmp_path), verbose=False)
    orch.add(Task("a", wait), Task("b", wait))
    assert all(t.status is Status.DONE for t in orch.run().values())


def test_gate_retries_with_feedback(tmp_path):
    seen = []

    def run(ctx):
        seen.append(list(ctx.feedback))
        return TaskResult("good" if ctx.feedback else "bad")
    orch = Orchestrator(Store(tmp_path), verbose=False)
    orch.add(Task("t", run, gate=lambda out, ctx: [] if out == "good" else ["not good"], max_attempts=3))
    tasks = orch.run()
    assert tasks["t"].status is Status.DONE and tasks["t"].attempts == 2
    assert seen == [[], ["not good"]]


def test_failure_blocks_dependents(tmp_path):
    orch = Orchestrator(Store(tmp_path), verbose=False)
    orch.add(Task("a", ok(1), gate=lambda out, ctx: ["never"], max_attempts=2),
             Task("b", ok(1), deps=["a"]), Task("c", ok(1), deps=["b"]), Task("d", ok(1)))
    tasks = orch.run()
    assert tasks["a"].status is Status.FAILED
    assert tasks["b"].status is Status.BLOCKED and tasks["c"].status is Status.BLOCKED
    assert tasks["d"].status is Status.DONE


def test_exception_fails_task(tmp_path):
    def boom(ctx):
        raise ValueError("boom")
    orch = Orchestrator(Store(tmp_path), verbose=False)
    orch.add(Task("a", boom))
    assert orch.run()["a"].status is Status.FAILED


def test_spawned_tasks_run(tmp_path):
    child = Task("child", lambda ctx: TaskResult(ctx.inputs["parent"] + 1), deps=["parent"])
    orch = Orchestrator(Store(tmp_path), verbose=False)
    orch.add(Task("parent", lambda ctx: TaskResult(1, spawn=[child])))
    orch.run()
    assert orch.outputs["child"] == 2


def test_resume_skips_finished_tasks(tmp_path):
    calls = []

    def run(ctx):
        calls.append(ctx.task_id)
        return TaskResult(len(calls))
    for resume in (False, True):
        orch = Orchestrator(Store(tmp_path), resume=resume, verbose=False)
        orch.add(Task("a", run), Task("b", run, deps=["a"]))
        orch.run()
    assert calls == ["a", "b"]


def test_event_log_feeds_the_sequence_diagram(tmp_path):
    import json
    store = Store(tmp_path)
    orch = Orchestrator(store, verbose=False)
    orch.add(Task("a", ok(1), gate=lambda out, ctx: [] if ctx.attempt > 1 else ["no"], max_attempts=2))
    orch.run()
    kinds = [json.loads(line)["event"] for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert kinds == ["plan", "started", "returned", "gate_failed", "returned", "gate_passed", "done"]


def test_crashed_attempt_is_retried_with_feedback(tmp_path):
    seen = []

    def run(ctx):
        seen.append(list(ctx.feedback))
        if not ctx.feedback:
            raise RuntimeError("Model stopped generating due to maximum token limit.")
        return TaskResult("ok")
    orch = Orchestrator(Store(tmp_path), verbose=False)
    orch.add(Task("t", run, max_attempts=3))
    tasks = orch.run()
    assert tasks["t"].status is Status.DONE and tasks["t"].attempts == 2
    assert "maximum token limit" in seen[1][0]


def test_every_structured_output_schema_converts_to_a_tool_schema():
    """Strands inlines $ref when it turns a Pydantic model into a tool schema, so a self-referential model
    recurses forever at the first call. Every schema an agent is asked to return must survive that conversion."""
    import inspect
    from pydantic import BaseModel
    import agents.schemas as schemas
    try:
        from strands.tools.structured_output import convert_pydantic_to_tool_spec
    except ImportError:
        from strands.types.tools import convert_pydantic_to_tool_spec
    models = [m for _, m in inspect.getmembers(schemas, inspect.isclass) if issubclass(m, BaseModel) and m is not BaseModel and m.__module__ == schemas.__name__]
    assert len(models) > 10
    for m in models:
        convert_pydantic_to_tool_spec(m)                     # raises RecursionError on a self-referential model


def test_a_crash_in_the_harness_is_not_retried_as_the_agents_fault(tmp_path):
    from harness import Orchestrator, Status, Store, Task, TaskResult
    from harness.orchestrator import HarnessError
    calls = []
    def broken(ctx):
        calls.append(ctx.attempt)
        def deeper(n): return deeper(n + 1)
        return TaskResult(deeper(0))
    orch = Orchestrator(Store(tmp_path / "run"), verbose=False)
    orch.add(Task("t", broken, kind="agent", max_attempts=3))
    tasks = orch.run()
    assert tasks["t"].status is Status.FAILED and calls == [1]                  # one attempt, not three
    assert "RecursionError" in tasks["t"].errors[-1] and "defect in the code" in tasks["t"].errors[-1] and "test_harness.py" in tasks["t"].errors[-1]
    events = [e for e in (tmp_path / "run" / "events.jsonl").read_text().splitlines() if "attempt_failed" in e]
    assert events and "where" in events[0]
