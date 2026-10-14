"""Demo 1. Ask for an answer, or build the thing that answers.

  python demos/01_ask_vs_build.py            live
  python demos/01_ask_vs_build.py --offline  replay

Part A asks a model to count letters. You get one answer and no proof.
Part B asks an agent to write a general function and its tests. The harness runs
the tests as a gate. The agent's work is accepted only when the tests pass.
After that, no model is needed to count anything.
"""
import _path  # noqa: F401
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agents.config import MODELS, ROOT, Settings, load_env, require_key
from agents.runner import build_agent, run_agent
from agents.schemas import ToolSubmission
from harness import Context, Orchestrator, Store, Task, TaskResult, console

load_env()
Settings.offline = Settings.offline or "--offline" in sys.argv
require_key()
WORK = Path(tempfile.mkdtemp(prefix="toolsmith_"))

SYSTEM = """You are a toolsmith. You do not answer questions. You write small, general, tested Python functions
that answer a whole class of questions. Return a complete module and a complete pytest module.
The test module imports from a module named 'solution'. Use the standard library only."""

SPEC = """Write char_stats(text: str, chars: Sequence[str], case_sensitive: bool = False) -> dict.
It counts each given single character in the text. It returns
{"length", "total_matches", "match_share", "chars": {char: {"count", "positions", "share"}}}.
Raise ValueError when an entry is not a single character. Handle empty text.
Include a test that char_stats("strawberry", ["r"]) counts 3."""


def write_tool(ctx: Context) -> TaskResult:
    prompt = SPEC
    if ctx.feedback:
        prompt += "\n\nYour last submission failed its gate. Fix it:\n" + "\n".join(ctx.feedback)
    out = run_agent("toolsmith", system=SYSTEM, prompt=prompt, output=ToolSubmission, tier="balanced", log=ctx.log)
    return TaskResult(out, note=f"attempt {ctx.attempt}")


def tests_pass(out: dict, ctx: Context) -> list[str]:
    """The gate. Run the agent's tests against the agent's code in a subprocess."""
    (WORK / "solution.py").write_text(out["module_code"], encoding="utf-8")
    (WORK / "test_solution.py").write_text(out["test_code"], encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", str(WORK)],
                          capture_output=True, text=True, timeout=120, cwd=WORK)
    ctx.log("gate_output", check="pytest", text=proc.stdout[-6000:])
    console.say("    pytest: " + (proc.stdout.strip().splitlines() or ["no output"])[-1])
    return [] if proc.returncode == 0 else [proc.stdout[-1500:]]


print("Part A. Ask the model.")
if Settings.offline:
    print("  (skipped in offline mode)")
else:
    agent = build_agent("Answer in one short sentence.", tier="fast")
    with console.status(f"Asking {MODELS['fast']}", report=False):
        answer = str(agent("How many times does the letter r appear in 'strawberry'?")).strip()
    print("  ", answer)

print("\nPart B. Have an agent build the function, gated by its own tests.")
orch = Orchestrator(Store(ROOT / "runs" / "demo01"))
orch.add(Task("toolsmith", write_tool, kind="agent", model="balanced", gate=tests_pass, max_attempts=3))
orch.run()

spec = importlib.util.spec_from_file_location("solution", WORK / "solution.py")
solution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solution)
print("\nNow the function answers, with no model in the loop:")
for text, chars in [("strawberry", ["r"]), ("Mississippi", ["s", "p", "i"]),
                    ("International Council on Systems Engineering", ["e", "n", "s"])]:
    stats = solution.char_stats(text, chars)
    print(f"  {text!r}: " + json.dumps({c: v["count"] for c, v in stats["chars"].items()}))
print(f"\nGenerated code: {WORK}")
