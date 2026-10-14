"""Demo 5. A Strands agent with long-term memory through mem0.

  python demos/04_memory_agent.py "We just learned the star tracker blinds near the Moon. Remember that."
  python demos/04_memory_agent.py "What should I check first on a new 3U imaging design?"
  python demos/04_memory_agent.py --list
  python demos/04_memory_agent.py --offline --list     show the offline stand-in store

Each command is a new process. Nothing carries over in the conversation.
What the agent knows on the second call comes from the memory store on disk.
The full workflow (run.py) writes the systems engineer's lessons to the same store.
"""
import _path  # noqa: F401
import sys

from agents.config import Settings, load_env, require_key
from agents.memory import memory_tools, open_memory
from agents.runner import build_agent

load_env()
args = [a for a in sys.argv[1:] if not a.startswith("--")]
Settings.offline = Settings.offline or "--offline" in sys.argv
require_key()

SYSTEM = """You are a senior systems engineer who reviews small-satellite designs.
You have long-term memory across sessions through two tools.
- Call recall_lessons before you answer any design question.
- Call remember_lesson when the user shares a reusable lesson or asks you to remember something.
Say which answers came from stored lessons. Keep answers short."""

memory = open_memory()

if "--list" in sys.argv or not args:
    lessons = memory.all()
    print(f"{len(lessons)} stored lesson(s):")
    for lesson in lessons:
        print(f"  - {lesson}")
elif Settings.offline:
    print("Offline mode has no model to chat with. Matching lessons:")
    for lesson in memory.search(args[0]):
        print(f"  - {lesson}")
else:
    agent = build_agent(SYSTEM, tier="balanced", tools=memory_tools(memory), quiet=False)
    agent(args[0])
    print()
