"""Demo 2. The harness with no model at all.

  python demos/02_minimal_harness.py

Shows the four ideas the whole talk rests on:
dependencies, parallel execution, gates with retry, and tasks that spawn tasks.
"""
import _path  # noqa: F401
import random
import time

from agents.config import ROOT
from harness import Context, Orchestrator, Store, Task, TaskResult

random.seed(7)


def measure(name: str, value: float):
    def run(ctx: Context) -> TaskResult:
        time.sleep(0.4)                                  # two of these run at the same time
        return TaskResult({"name": name, "value": value}, note=f"{name} = {value}")
    return run


def flaky_estimate(ctx: Context) -> TaskResult:
    """Stands in for a model call. Wrong on the first try, right once it sees feedback."""
    total = sum(v["value"] for v in ctx.inputs.values())
    guess = total if ctx.feedback else total * random.uniform(1.2, 1.5)
    return TaskResult({"total": round(guess, 1)}, note=f"attempt {ctx.attempt}, total {guess:.1f}")


def total_is_right(out: dict, ctx: Context) -> list[str]:
    """The gate recomputes the sum. Code checks the work, and the errors become feedback."""
    truth = sum(v["value"] for v in ctx.inputs.values())
    return [] if abs(out["total"] - truth) < 0.01 else [f"total {out['total']} does not equal the sum {truth}"]


def decide(ctx: Context) -> TaskResult:
    """A task can grow the plan. Here it adds a follow-up only when the total is over the limit."""
    total = ctx.inputs["estimate"]["total"]
    if total > 4000:
        follow_up = Task("trim_mass", lambda c: TaskResult({"cut_g": total - 4000}, note=f"cut {total - 4000:.0f} g"),
                         deps=["decide"])
        return TaskResult({"over": True}, spawn=[follow_up], note="over limit, spawning trim_mass")
    return TaskResult({"over": False}, note="within limit")


orch = Orchestrator(Store(ROOT / "runs" / "demo02"))
orch.add(
    Task("mass.structure", measure("structure", 1450.0)),
    Task("mass.avionics", measure("avionics", 2700.0)),
    Task("estimate", flaky_estimate, deps=["mass.structure", "mass.avionics"], gate=total_is_right, max_attempts=3),
    Task("decide", decide, deps=["estimate"]),
)
orch.run()
print("\n" + orch.summary())
