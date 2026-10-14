"""One design project on disk. Everything a person or an agent produced, versioned.

  harness.yaml     the harness definition: what is being built, which agents build it, the rules they follow
  agents/          one instruction file per agent. Edit them to change how an agent thinks
  tools/           one Python file per tool the agents may call
  library/         how each assessment is done by hand, where someone has written that down
  evidence/        measures and worked cases the project starts with
  requests/        what the person asked for, one file per iteration
  spec.json        the parameterized specification. Every value is tagged user or ai
  interfaces.json  elements, function contracts, and the exchanges between elements
  context/         evidence per capability: measures and worked cases, each tagged with its source
  functions/       <static id>/<version>/ holds one generated function and its tests
  registry.json    static id -> active version. The runtime routes every call through this file
  inputs/          design instances: the baseline that matches the request, and alternatives
  outputs/         per design: results, a report, and a SysML v2 model
  changelog.json   what each iteration added or replaced
"""
from __future__ import annotations

import hashlib
import copy
import json
import re
import shutil
import threading
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"
KITS = ROOT / "kits"


def kits() -> dict[str, dict]:
    """Harness definitions a new project can start from."""
    found = {}
    for folder in sorted(KITS.iterdir()) if KITS.exists() else []:
        if (folder / "harness.yaml").exists():
            h = yaml.safe_load((folder / "harness.yaml").read_text(encoding="utf-8"))
            text = lambda name: (folder / name).read_text(encoding="utf-8") if (folder / name).exists() else ""
            found[folder.name] = {"kit": folder.name, "name": h["name"], "building": h["building"], "description": h.get("description", ""),
                                  "specialists": [s["capability"] for s in h["agents"].get("specialists", [])],
                                  "example_request": text("example_request.md"), "example_followup": text("example_followup.md")}
    return found


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "project"


def digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:10]


_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()


class Project:
    def __init__(self, name: str):
        self.name, self.root = name, PROJECTS / name
        with _guard:                                    # specialists finish at the same time. Shared files need one writer
            self.lock = _locks.setdefault(str(self.root), threading.RLock())

    # ---- plain files --------------------------------------------------------------
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def read(self, name: str, default=None):
        """A build process writes these files while the interface reads them. A half-written file reads as the default."""
        file = self.path(name)
        if not file.exists():
            return default
        for _ in range(3):
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                time.sleep(0.02)
        return default

    def write(self, name: str, data) -> None:
        """Write to a temporary file, then replace, so a reader never sees a partial file."""
        file = self.path(name)
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(file.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(file)

    # ---- the harness definition: what is being built, by which agents, under which rules ----------
    def create_from_kit(self, kit: str) -> None:
        """Start a project as a copy of a harness definition. From here on the project owns it."""
        source = KITS / kit
        if not (source / "harness.yaml").exists():
            raise ValueError(f"No harness definition named '{kit}'. Known: {', '.join(kits())}")
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        shutil.copy(source / "harness.yaml", self.path("harness.yaml"))
        for folder in ("agents", "tools", "library", "evidence"):
            if (source / folder).exists():
                shutil.copytree(source / folder, self.path(folder))
        self.write("kit.json", {"kit": kit})

    def set_specialist_tier(self, capability: str, tier: str) -> None:
        """Change one specialist's model tier in this project's harness.yaml. The kit it came from is untouched."""
        with self.lock:
            h = self.harness()
            for s in h["agents"].get("specialists", []):
                if s["capability"] == capability:
                    s["tier"] = tier
            if capability == "systems_engineer":
                h["agents"]["systems_engineer"]["tier"] = tier
            self.path("harness.yaml").write_text(yaml.safe_dump(h, sort_keys=False, width=110), encoding="utf-8")
            self.log_change({"kind": "harness", "summary": f"{capability} agent moved to the {tier} tier"})

    def harness(self) -> dict:
        return yaml.safe_load(self.path("harness.yaml").read_text(encoding="utf-8"))

    def harness_version(self) -> str:
        files = [self.path("harness.yaml")] + sorted(self.path("agents").glob("*.md")) + sorted(self.path("tools").glob("*.py"))
        return digest([f.read_text(encoding="utf-8") for f in files if f.exists()])

    def agent_text(self, relative: str) -> str:
        file = self.path(relative)
        return file.read_text(encoding="utf-8") if file.exists() else ""

    def specialist(self, capability: str) -> dict:
        """The agent that builds one capability: its instruction file, model tier, and tools."""
        agents = self.harness()["agents"]
        found = next((s for s in agents.get("specialists", []) if s["capability"] == capability), None)
        chosen = found or {**agents["fallback"], "capability": capability, "tools": []}
        return {**chosen, "generic": found is None, "text": self.agent_text(chosen["instructions"])}

    @property
    def exists(self) -> bool:
        return self.path("requests").exists()

    @property
    def iteration(self) -> int:
        return len(list(self.path("requests").glob("*.md")))

    @property
    def built(self) -> bool:
        """True once request 1 ran to its end: functions are registered, a baseline exists, and the render step
        took its snapshot. A task may write files and then fail its gate, so files alone do not mean finished."""
        return bool(self.registry()) and self.path("inputs", "baseline.json").exists() and self.path("history", "i1.registry.json").exists()

    def missing_for_build(self) -> list[str]:
        """What a finished first build would have written and this project lacks. Empty when built."""
        out = [] if self.registry() else ["registry.json (no function was registered)"]
        out += [] if self.path("inputs", "baseline.json").exists() else ["inputs/baseline.json (the inputs step did not finish)"]
        return out + ([] if self.path("history", "i1.registry.json").exists() else ["history/i1.* (evaluate and render did not run, so a task before them failed)"])

    def reset_build(self) -> list[str]:
        """Clear what an unfinished first build left behind, so request 1 can run again. Returns follow-ups it set aside."""
        import shutil
        aside = []
        for f in sorted(self.path("requests").glob("*.md"))[1:]:      # follow-ups that never had a build to change
            target = self.path("requests", "not_applied", f.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            f.replace(target)
            aside.append(str(target))
        for name in ("functions", "inputs", "outputs", "history"):
            shutil.rmtree(self.path(name), ignore_errors=True)
        for name in ("registry.json", "dictionary.json", "interfaces.json", "changelog.json"):
            self.path(name).unlink(missing_ok=True)
        return aside

    def add_request(self, text: str, **meta) -> int:
        """A request is a markdown file. Metadata such as kind: drilldown and the part go in YAML front matter."""
        n = self.iteration + 1
        self.path("requests").mkdir(parents=True, exist_ok=True)
        head = ("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n") if meta else ""
        self.path("requests", f"{n:03d}.md").write_text(head + text.strip() + "\n", encoding="utf-8")
        return n

    @staticmethod
    def _split_request(text: str) -> tuple[dict, str]:
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end > 0:
                return (yaml.safe_load(text[4:end]) or {}), text[end + 5:]
        return {}, text

    def requests(self) -> list[str]:
        """The text of each request, oldest first, without its metadata."""
        return [self._split_request(f.read_text(encoding="utf-8"))[1] for f in sorted(self.path("requests").glob("*.md"))]

    def request_meta(self, iteration: int) -> dict:
        """The metadata of one request: {} for an ordinary request, {kind: drilldown, part: ...} for a drill-down."""
        files = sorted(self.path("requests").glob("*.md"))
        return self._split_request(files[iteration - 1].read_text(encoding="utf-8"))[0] if 0 < iteration <= len(files) else {}

    # ---- spec, interfaces, inputs ---------------------------------------------------
    def spec(self) -> dict:
        return self.read("spec.json", {})

    def interfaces(self) -> dict:
        return self.read("interfaces.json", {})

    def contract(self, function_id: str) -> dict:
        return next(c for c in self.interfaces()["contracts"] if c["function_id"] == function_id)

    def snapshot(self, iteration: int) -> None:
        """Keep a copy of the spec and interfaces as they stood at the end of an iteration."""
        for name in ("spec.json", "interfaces.json", "registry.json", "harness.yaml"):
            if self.path(name).exists():
                self.path("history").mkdir(exist_ok=True)
                shutil.copy(self.path(name), self.path("history", f"i{iteration}.{name}"))

    # ---- checkpoints: a request that changes the model can be undone ------------------------
    CHECKPOINTED = ("interfaces.json", "registry.json", "spec.json", "dictionary.json")

    def checkpoint(self, iteration: int) -> None:
        """Copy what a request may change, before it changes anything, into history/i<n>.before/."""
        folder = self.path("history", f"i{iteration}.before")
        if folder.exists():
            return                                            # a resumed run keeps the original checkpoint
        folder.mkdir(parents=True)
        for name in self.CHECKPOINTED:
            if self.path(name).exists():
                shutil.copy(self.path(name), folder / name)
        for sub in ("inputs", "context"):                     # designs, and the evidence a follow-up may extend
            if self.path(sub).exists():
                shutil.copytree(self.path(sub), folder / sub)

    def rollbacks(self) -> list[int]:
        """Iterations that can be undone: the latest request, when it left a checkpoint and nothing came after it."""
        n = self.iteration
        return [n] if n and self.path("history", f"i{n}.before").exists() else []

    def rollback(self) -> dict:
        """Undo the latest request: restore the checkpoint, route every function back to the version it had,
        and set the request aside. Function versions it built stay on disk and in the registry, inactive,
        so the identifier can be routed to them again. Returns what was undone."""
        n = self.iteration
        if n not in self.rollbacks():
            raise ValueError(f"Request {n} left no checkpoint to roll back to." if n else "Nothing to roll back.")
        folder = self.path("history", f"i{n}.before")
        with self.lock:
            kept = self.registry()                            # versions built since stay, inactive
            for name in self.CHECKPOINTED:
                if (folder / name).exists():
                    shutil.copy(folder / name, self.path(name))
                elif self.path(name).exists():
                    self.path(name).unlink()
            restored = self.registry()
            for fid, entry in kept.items():
                if fid in restored:
                    for v, meta in entry["versions"].items():
                        restored[fid]["versions"].setdefault(v, {**meta, "rolled_back": n})
            self.write("registry.json", restored)
            for sub in ("inputs", "context"):
                shutil.rmtree(self.path(sub), ignore_errors=True)
                if (folder / sub).exists():
                    shutil.copytree(folder / sub, self.path(sub))
            self._yaml_cache.clear()
            request = sorted(self.path("requests").glob("*.md"))[-1]
            aside = self.path("requests", "rolled_back", request.name)
            aside.parent.mkdir(parents=True, exist_ok=True)
            request.replace(aside)
            self.write("runs.json", [r for r in (self.read("runs.json", []) or []) if r["iteration"] != n])
        shutil.rmtree(folder, ignore_errors=True)             # the checkpoint is spent: the next request at this number takes its own
        for snap in self.path("history").glob(f"i{n}.*"):
            if snap.is_file():
                snap.unlink()
        meta = self._split_request(aside.read_text(encoding="utf-8"))[0]
        undone = {"iteration": n, "kind": meta.get("kind", "followup"), "part": meta.get("part", ""),
                  "active": {fid: e["active"] for fid, e in restored.items()}}
        self.log_change({"kind": "rollback", "undone": undone["kind"], "part": undone["part"], "of_iteration": n, "active": undone["active"]})
        return undone

    def designs(self) -> dict[str, dict]:
        return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(self.path("inputs").glob("*.json"))}

    # ---- evidence -------------------------------------------------------------------
    _yaml_cache: dict = {}

    def context(self, capability: str) -> dict:
        """Measures and worked cases for one capability. Starts from the library, then the person adds to it.

        Parsed once per file change: a sensitivity sweep reads it thousands of times.
        """
        file = self.path("context", f"{capability}.yaml")
        if file.exists():
            stamp, hit = file.stat().st_mtime_ns, self._yaml_cache.get(file)
            if hit and hit[0] == stamp:
                return copy.deepcopy(hit[1])
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
            self._yaml_cache[file] = (stamp, copy.deepcopy(data))
            return data
        if not file.exists():
            library = self.path("evidence", f"{capability}.yaml")
            data = yaml.safe_load(library.read_text(encoding="utf-8")) if library.exists() else {}
            data = {"notes": data.get("notes", ""), "version": 1,
                    "measures": [{**m, "_source": "library"} for m in data.get("measures", [])],
                    "cases": [{**c, "raises": bool(c.get("raises")), "source": "library", "since": 1}
                              for c in data.get("examples", [])]}
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return yaml.safe_load(file.read_text(encoding="utf-8"))

    def save_context(self, capability: str, data: dict) -> None:
        data["version"] = data.get("version", 1) + 1
        self.path("context", f"{capability}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def evidence(self, capability: str) -> tuple[list, list]:
        """(measures, cases) in the plain form the functions and tests use."""
        ctx = self.context(capability)
        measures = [{k: v for k, v in m.items() if not k.startswith("_")} for m in ctx.get("measures", [])]
        return measures, ctx.get("cases", [])

    def process(self, capability: str) -> dict:
        """The library's description of how the operation is done by hand, if there is one."""
        file = self.path("library", f"{capability}.yaml")
        return yaml.safe_load(file.read_text(encoding="utf-8")) if file.exists() else {}

    # ---- the registry: static identifiers, versions, routing ---------------------------
    def registry(self) -> dict:
        return self.read("registry.json", {})

    def function_dir(self, function_id: str, version: str) -> Path:
        return self.path("functions", function_id, version)

    def next_version(self, function_id: str) -> str:
        return f"v{len(self.registry().get(function_id, {}).get('versions', {})) + 1}"

    def register(self, function_id: str, version: str, entry: dict, activate: bool = True) -> None:
        with self.lock:
            reg = self.registry()
            item = reg.setdefault(function_id, {"capability": entry.get("capability", ""), "active": version, "versions": {}})
            item["versions"][version] = {**entry, "registered_at": time.strftime("%Y-%m-%d %H:%M")}
            if activate:
                item["active"] = version
            self.write("registry.json", reg)

    def route(self, function_id: str, version: str) -> None:
        """Point a static identifier at another version. Nothing else changes."""
        reg = self.registry()
        if version not in reg[function_id]["versions"]:
            raise ValueError(f"{function_id} has no version {version}. Known: {sorted(reg[function_id]['versions'])}")
        reg[function_id]["active"] = version
        self.write("registry.json", reg)
        self.log_change({"kind": "route", "function_id": function_id, "to": version})

    # ---- changelog --------------------------------------------------------------------
    def log_change(self, entry: dict) -> None:
        with self.lock:
            log = self.read("changelog.json", [])
            log.append({"at": time.strftime("%Y-%m-%d %H:%M"), "iteration": self.iteration, **entry})
            self.write("changelog.json", log)


TRASH = PROJECTS / ".trash"


def delete_project(name: str, purge: bool = False) -> dict:
    """Delete a project. By default it moves to projects/.trash/<name>-<stamp>/, so a mistake is recoverable
    by moving the folder back; `purge` removes it for good. Its run logs under runs/ are left alone."""
    project = Project(name)
    if not project.exists:
        raise ValueError(f"No project named '{name}'.")
    if purge:
        shutil.rmtree(project.root)
        return {"name": name, "purged": True}
    TRASH.mkdir(parents=True, exist_ok=True)
    target = TRASH / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(project.root), str(target))
    return {"name": name, "moved_to": str(target)}


def unused_name(base: str) -> str:
    """A project name that does not exist yet, so a new build never deletes an earlier project."""
    name, n = base, 1
    while (PROJECTS / name / "requests").exists():
        n += 1
        name = f"{base}-{n}"
    return name


def all_projects() -> list[Project]:
    if not PROJECTS.exists():
        return []
    found = [Project(p.name) for p in PROJECTS.iterdir() if (p / "requests").exists() and not p.name.startswith(".")]
    return sorted(found, key=lambda p: p.path("requests").stat().st_mtime, reverse=True)


def latest() -> Project | None:
    found = all_projects()
    return found[0] if found else None
