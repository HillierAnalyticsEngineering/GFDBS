"""Long-term memory for the systems engineer agent.

Mem0Lessons is the real thing: mem0 with a local vector store and a local embedder.
JsonLessons is a keyword-match stand-in for offline replays.
Both expose the same three methods, so the agent code does not change.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from harness import console

from .config import MODELS, Settings

os.environ.setdefault("MEM0_TELEMETRY", "false")     # no analytics calls from the stage
# Hugging Face prints progress bars and warnings while it downloads the embedding model.
# The demo shows its own one-line status for that step.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
AGENT_ID = "systems_engineer"
SHARED = "shared"                 # the scope for lessons that hold for every project
# mem0 logs an error when the optional spaCy extra is absent. This demo does not use it.
logging.getLogger("mem0.utils.spacy_models").setLevel(logging.CRITICAL)


def mem0_config(directory: Path) -> dict:
    """Anthropic for fact extraction, FastEmbed for vectors, Qdrant on local disk."""
    return {
        "llm": {"provider": "anthropic", "config": {"model": MODELS["fast"], "max_tokens": 2000}},
        "embedder": {"provider": "fastembed",
                     "config": {"model": os.getenv("MEM0_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
                                "embedding_dims": int(os.getenv("MEM0_EMBED_DIMS", "384"))}},
        "vector_store": {"provider": "qdrant",
                         "config": {"path": str(directory / "qdrant"), "collection_name": "se_lessons",
                                    "embedding_model_dims": int(os.getenv("MEM0_EMBED_DIMS", "384")),
                                    "on_disk": True}},
        "history_db_path": str(directory / "history.db"),
    }


def model_cache() -> Path:
    """Where FastEmbed keeps downloaded embedding models. Set FASTEMBED_CACHE_PATH to move it."""
    return Path(os.getenv("FASTEMBED_CACHE_PATH", os.path.join(tempfile.gettempdir(), "fastembed_cache")))


def _megabytes(folder: Path) -> float:
    try:
        return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6
    except OSError:
        return 0.0


class Mem0Lessons:
    def __init__(self, directory: Path | None = None, agent_id: str = AGENT_ID):
        directory = Path(directory or Settings.memory_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.agent_id = agent_id

        cache, before = model_cache(), _megabytes(model_cache())
        name = mem0_config(directory)["embedder"]["config"]["model"]
        cached = any(name.split("/")[-1].lower() in p.name.lower() for p in cache.glob("*")) if cache.exists() else False
        if not cached:
            console.say(f"  first use      downloading the embedding model {name} from Hugging Face (one time)")
            console.say(f"  {'':<14} saved to {cache}")

        def progress() -> str:
            grown = _megabytes(cache) - before
            return f"{grown:.0f} MB downloaded" if grown >= 1 else ""

        message = "Starting local memory: mem0, Qdrant on disk, embedding model"
        with console.status(message, detail=progress, hide_noise=True):
            from mem0 import Memory
            self.mem = Memory.from_config(mem0_config(directory))
            warm = getattr(self.mem.vector_store, "_get_bm25_encoder", None)
            if warm:                 # mem0's keyword index uses a second small model. Fetch it now,
                warm()               # under the status line, so the first add is quiet and quick.
        atexit.register(self.close)      # close Qdrant before interpreter teardown

    def close(self) -> None:
        """Release every local Qdrant handle before the interpreter starts to shut down.

        mem0 opens more than one. A handle left open prints a harmless traceback at exit.
        """
        import gc
        try:
            from qdrant_client import QdrantClient
        except Exception:
            return
        for client in [o for o in gc.get_objects() if isinstance(o, QdrantClient)]:
            try:
                client.close()
            except Exception:
                pass

    # A lesson lives in a scope: a project's name, or "shared" for lessons that hold for every project.
    # mem0 keeps the scope as run_id, so a search can stay inside one project or reach the shared pool too.
    def add(self, text: str, infer: bool = False, scope: str = SHARED, **metadata) -> None:
        """Store a lesson in a scope. infer=True lets mem0 extract and merge facts with its LLM."""
        self.mem.add(text, agent_id=self.agent_id, run_id=scope or SHARED, infer=infer, metadata={**metadata, "scope": scope or SHARED})

    def search(self, query: str, k: int = 5, scope: str | None = None, shared: bool = True) -> list[str]:
        """Lessons that match, from the project scope and, unless shared=False, the shared pool. Project lessons first."""
        scopes = [s for s in ([scope] if scope else []) + ([SHARED] if shared or not scope else []) if s]
        out: list[str] = []
        for s in dict.fromkeys(scopes):
            hits = self.mem.search(query, filters={"agent_id": self.agent_id, "run_id": s}, top_k=k)
            out += [h["memory"] for h in hits.get("results", []) if h["memory"] not in out]
        return out[: k * 2]

    def all(self) -> list[str]:
        return [e["text"] for e in self.entries()]

    def entries(self, scope: str | None = None) -> list[dict]:
        """Every lesson with its id, scope, and origin, oldest first. scope=None lists every scope."""
        filters = {"agent_id": self.agent_id, **({"run_id": scope} if scope else {})}
        hits = self.mem.get_all(filters=filters, top_k=500)
        out = [{"id": h["id"], "text": h["memory"], "source": (h.get("metadata") or {}).get("source", "review"),
                "scope": h.get("run_id") or (h.get("metadata") or {}).get("scope") or SHARED,
                "run": (h.get("metadata") or {}).get("run", ""), "created": h.get("created_at", "")} for h in hits.get("results", [])]
        return sorted(out, key=lambda e: e["created"] or "")

    def forget(self, memory_id: str) -> None:
        self.mem.delete(memory_id)


class JsonLessons:
    def __init__(self, directory: Path | None = None, agent_id: str = AGENT_ID):
        directory = Path(directory or Settings.memory_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{agent_id}.offline.json"

    def all(self) -> list[str]:
        return [e["text"] for e in self.entries()]

    def entries(self, scope: str | None = None) -> list[dict]:
        raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        items = [{"text": r, "source": "review", "run": "", "scope": SHARED} if isinstance(r, str) else {"scope": SHARED, **r} for r in raw]
        out = [{"id": str(i), "created": "", **e} for i, e in enumerate(items)]
        return [e for e in out if not scope or e["scope"] == scope]

    def _save(self, entries: list[dict]) -> None:
        self.path.write_text(json.dumps([{k: e.get(k, "") for k in ("text", "source", "run", "scope")} for e in entries], indent=2), encoding="utf-8")

    def add(self, text: str, infer: bool = False, scope: str = SHARED, **metadata) -> None:
        items = self.entries()
        if text not in [e["text"] for e in items]:
            items.append({"text": text, "source": metadata.get("source", "review"), "run": metadata.get("run", ""), "scope": scope or SHARED})
            self._save(items)

    def forget(self, memory_id: str) -> None:
        self._save([e for e in self.entries() if e["id"] != memory_id])

    def search(self, query: str, k: int = 5, scope: str | None = None, shared: bool = True) -> list[str]:
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        pool = [e for e in self.entries() if e["scope"] == scope or ((shared or not scope) and e["scope"] == SHARED)]
        scored = [(len(words & set(re.findall(r"[a-z0-9]+", e["text"].lower()))), e["scope"] != SHARED, e["text"]) for e in pool]
        return [t for score, own, t in sorted(scored, key=lambda s: (-s[0], not s[1])) if score][:k]


_memory = None


def open_memory():
    """One memory per process. Local Qdrant allows a single open handle."""
    global _memory
    if _memory is None:
        _memory = JsonLessons() if Settings.offline else Mem0Lessons()
    return _memory


def memory_tools(memory, scope: str = SHARED, shared: bool = True):
    """Give an agent explicit recall and remember tools over the same store, scoped to one project.

    recall looks in this project's lessons and, when shared is True, the pool every project shares.
    remember stores in this project unless the agent says the lesson holds everywhere.
    """
    from strands import tool

    @tool
    def recall_lessons(query: str) -> list[str]:
        """Search lessons from earlier reviews of this project, and the shared pool. Use before judging a design."""
        return memory.search(query, scope=scope, shared=shared) or ["No stored lessons match."]

    @tool
    def remember_lesson(lesson: str, holds_for_every_project: bool = False) -> str:
        """Store one reusable lesson. One sentence, specific. Set holds_for_every_project only for a lesson about method, not about this system."""
        memory.add(lesson, scope=SHARED if holds_for_every_project else scope, run=scope)
        return "stored for every project" if holds_for_every_project else f"stored for {scope}"

    return [recall_lessons, remember_lesson]
