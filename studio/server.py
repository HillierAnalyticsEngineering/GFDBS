"""The studio's local web server. Standard library only. It serves the interface and a small JSON API.

Starting a build from the page launches `studio.py` as a separate process. The only model call
made in this process is the one that drafts agent specs from an operational scenario.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agents.config import Settings
from . import engine, designer, render, runtime, tooling
from .project import ROOT, Project, all_projects, kits

DIST = ROOT / "ui" / "dist"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
         ".json": "application/json", ".sysml": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
         ".py": "text/plain; charset=utf-8"}
STATE = {"process": None, "offline": False}


API = 4            # bumped whenever the page and the server must change together; the page checks it


def run_dir(project: Project, iteration: int | None) -> Path | None:
    runs = project.read("runs.json", []) or []
    if iteration:
        runs = [r for r in runs if r["iteration"] == iteration]
    return Path(runs[-1]["run"]) if runs else None


def events(project: Project, iteration: int | None, since: int) -> dict:
    folder = run_dir(project, iteration)
    out = []
    if folder and (folder / "events.jsonl").exists():
        for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return {"total": len(out), "events": out[since:], "run": folder.name if folder else ""}


def state(project: Project) -> dict:
    registry = project.registry()
    for fid, entry in registry.items():
        for version, v in entry["versions"].items():
            file = project.function_dir(fid, version) / "manifest.json"
            if file.exists():
                m = json.loads(file.read_text(encoding="utf-8"))
                v.update(parameters=m["parameters"], design_notes=m["design_notes"])
    designs = project.designs()
    for name, d in designs.items():
        d["result"] = project.read(f"outputs/{name}/results.json")
    capabilities = sorted({e["capability"] for e in registry.values()})
    harness = project.harness()
    for agent in [harness["agents"]["systems_engineer"], harness["agents"]["fallback"]] + harness["agents"].get("specialists", []):
        agent["text"] = project.agent_text(agent.get("instructions", ""))
    harness["tools"] = [{k: t[k] for k in ("name", "doc", "signature", "error")} for t in tooling.list_tools(project.root)]
    return {"name": project.name, "iteration": project.iteration, "built": project.built, "missing": project.missing_for_build(), "requests": project.requests(), "spec": project.spec(),
            "root": str(project.root.resolve()), "repo": str(ROOT.resolve()), "sep": os.sep,
            "harness": harness, "harness_version": project.harness_version(), "kit": (project.read("kit.json", {}) or {}).get("kit", ""),
            "interfaces": project.interfaces(), "registry": registry, "designs": designs,
            "changelog": project.read("changelog.json", []), "dictionary": project.read("dictionary.json", {"version": 0, "terms": {}}),
            "context": {c: project.context(c) for c in capabilities},
            "reviews": {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(project.path("reviews").glob("*.json"))},
            "runs": project.read("runs.json", []), "rollbacks": project.rollbacks(),
            "request_meta": [project.request_meta(i) for i in range(1, project.iteration + 1)]}


def function_files(project: Project, function_id: str, version: str) -> dict:
    folder = project.function_dir(function_id, version)
    text = lambda name: (folder / name).read_text(encoding="utf-8") if (folder / name).exists() else ""
    return {"code": text("solution.py"), "tests": text("test_solution.py"), "acceptance": text("test_acceptance.py"),
            "manifest": json.loads(text("manifest.json") or "{}")}


def settings() -> dict:
    """What the Settings panel shows. The key itself never leaves this machine or this process."""
    from agents.config import DEFAULT_MODELS, MODELS, ROOT as repo
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return {"has_key": bool(key), "key_hint": f"ends in {key[-4:]}" if len(key) > 8 else "", "tiers": dict(MODELS),
            "defaults": list(DEFAULT_MODELS), "env_file": str((repo / ".env").resolve())}


def save_settings(body: dict) -> dict:
    from agents.config import DEFAULT_MODELS, MODELS, save_env
    import re
    updates: dict[str, str | None] = {}
    key = str(body.get("api_key") or "").strip()
    if key:
        updates["ANTHROPIC_API_KEY"] = key
    tiers = body.get("tiers")
    if isinstance(tiers, dict):
        for name, model in tiers.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", name):
                raise ValueError(f"Tier name '{name}' must be lowercase letters, digits, or underscores.")
            if not str(model).strip():
                raise ValueError(f"Tier '{name}' needs a model.")
            updates[f"MODEL_{name.upper()}"] = str(model).strip()
        for name in list(MODELS):
            if name not in tiers and name not in DEFAULT_MODELS:
                updates[f"MODEL_{name.upper()}"] = None
    save_env(updates)
    return settings()


def list_models() -> dict:
    """Ask Anthropic which models this key can use. GET /v1/models. Falls back to the configured tiers."""
    import urllib.error
    import urllib.request
    from agents.config import MODELS
    fallback = [{"id": m, "name": m} for m in dict.fromkeys(MODELS.values())]
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return {"models": fallback, "source": "configured tiers", "note": "Save an API key to load the list from Anthropic."}
    try:
        request = urllib.request.Request("https://api.anthropic.com/v1/models?limit=100",
                                         headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read())
        models = [{"id": m["id"], "name": m.get("display_name", m["id"])} for m in data.get("data", [])]
        return {"models": models or fallback, "source": "Anthropic API"}
    except urllib.error.HTTPError as exc:
        note = "The key was rejected." if exc.code in (401, 403) else f"Anthropic answered {exc.code}."
        return {"models": fallback, "source": "configured tiers", "note": note}
    except Exception as exc:
        return {"models": fallback, "source": "configured tiers", "note": f"Could not reach Anthropic: {exc}"}


def reveal(path: str) -> dict:
    """Show a file in the operating system's file browser. Only paths inside this repository."""
    file = Path(path).resolve()
    if ROOT.resolve() != file and ROOT.resolve() not in file.parents:
        return {"error": "That path is outside this repository."}
    if not file.exists():
        return {"error": f"Not created yet: {file}", "path": str(file)}
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", f"/select,{file}"] if file.is_file() else ["explorer", str(file)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(file)])
        else:
            subprocess.Popen(["xdg-open", str(file.parent if file.is_file() else file)])
    except OSError as exc:
        return {"path": str(file), "note": f"Could not open the file browser: {exc}"}
    return {"path": str(file), "opened": True}


def start_run(kind: str, text: str, project: str | None, offline: bool, kit: str = "cubesat", part: str = "") -> dict:
    if STATE["process"] and STATE["process"].poll() is None:
        return {"error": "A build is already running."}
    if kind in ("followup", "rebuild", "resume", "drilldown") and not (project and Project(project).exists):
        return {"error": "Pick a project first."}
    if kind in ("followup", "drilldown") and not Project(project).built:
        return {"error": "The first build did not finish, so there is nothing to follow up on. Run the first build again."}
    if kind == "drilldown" and part not in {p["name"] for p in Project(project).interfaces().get("structure", [])}:
        return {"error": f"'{part}' is not a part of this project."}
    if kind == "new" and not text.strip():
        return {"error": "Describe what to build first."}
    if kind == "new":                                      # name the project here, so the page can follow it
        from .project import slug, unused_name
        project = unused_name("cno-sat-1" if offline else slug(text.strip().splitlines()[0]))
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
    command = [sys.executable, str(ROOT / "studio.py"), kind, "--pace", "0.8" if offline else "0"]
    command += ["--file", f.name] if kind not in ("rebuild", "resume") else []
    command += ["--offline"] if offline else []
    command += ["--project", project] if project else []
    command += ["--kit", kit] if kind == "new" else []
    command += ["--part", part] if kind == "drilldown" else []
    (ROOT / "runs").mkdir(exist_ok=True)
    log = open(ROOT / "runs" / "studio-console.log", "w", encoding="utf-8")
    STATE["process"] = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    STATE["last"] = {"kind": kind, "project": project}
    return {"started": True, "project": project}


def memory_op(*words: str, project: str | None = None) -> dict:
    """Read or change the systems engineer's long-term memory in a short subprocess.

    Local Qdrant allows one open handle per store, and a running build holds it, so memory is
    read-only from the page while agents are building.
    """
    if STATE["process"] and STATE["process"].poll() is None and words and words[0] in ("add", "forget"):
        return {"error": "Agents are building and hold the memory store. Try again when the build finishes."}
    command = [sys.executable, str(ROOT / "studio.py"), "memory", *words] + (["--offline"] if STATE["offline"] else []) + (["--project", project] if project else [])
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    text = proc.stdout.strip()
    start = text.find("{")
    try:
        return json.loads(text[start:]) if start >= 0 else {"error": (proc.stderr or text)[-600:] or "no output"}
    except json.JSONDecodeError:
        return {"error": (proc.stderr or text)[-600:]}


def last_failure() -> str:
    """If the last build process exited with an error, its final console lines. Otherwise empty."""
    proc = STATE.get("process")
    if not proc or proc.poll() in (None, 0):
        return ""
    try:
        lines = (ROOT / "runs" / "studio-console.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return "The build process stopped with an error."
    return " ".join(l.strip() for l in lines[-4:] if l.strip())[-400:] or "The build process stopped with an error."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, kind="application/json", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass                                        # the page reloaded or navigated away mid-response

    def project(self, q) -> Project | None:
        name = q.get("project", [""])[0]
        found = all_projects()
        return Project(name) if name and Project(name).exists else (found[0] if found else None)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            if url.path == "/api/projects":
                running = bool(STATE["process"] and STATE["process"].poll() is None)
                def card(p: Project) -> dict:
                    h = p.harness() if p.path("harness.yaml").exists() else {}
                    reg = p.registry()
                    return {"name": p.name, "mission": p.spec().get("mission_name", p.name), "iteration": p.iteration, "built": p.built,
                            "building": h.get("building", ""), "harness": h.get("name", ""), "kit": (p.read("kit.json", {}) or {}).get("kit", ""),
                            "functions": len(reg), "versions": sum(len(e["versions"]) for e in reg.values()), "designs": len(p.designs()),
                            "summary": p.spec().get("summary", ""), "updated": p.path("requests").stat().st_mtime if p.path("requests").exists() else 0}
                return self.send({"projects": [card(p) for p in all_projects()], "running": running, "offline": STATE["offline"], "failure": last_failure(), "last": STATE.get("last"),
                                  "kits": list(kits().values()), "repo": str(ROOT.resolve()), "sep": os.sep,
                                  "tool_template": tooling.TEMPLATE, "api": API})
            if url.path == "/api/settings":
                return self.send(settings())
            if url.path == "/api/models":
                return self.send(list_models())
            if url.path == "/api/harness":
                return self.send({"draft": designer.load(q["kind"][0], q["name"][0]), "root": str(designer.folder_of(q["kind"][0], q["name"][0]).resolve())})
            project = self.project(q)
            if url.path.startswith("/api/") and not project:
                return self.send({"error": "no project yet"})
            if url.path == "/api/memory":
                query, proj = q.get("q", [""])[0], q.get("project", [""])[0] or None
                out = memory_op("search", query, project=proj) if query else memory_op("all" if not proj else "list", project=proj)
                if proj and not query:                                           # the project's own lessons, then the shared pool
                    pooled = memory_op("list")
                    out["entries"] = (out.get("entries") or []) + [e for e in (pooled.get("entries") or []) if e.get("scope") == "shared"]
                out.update(store=str(Settings.memory_dir.resolve()), offline=STATE["offline"], busy=bool(STATE["process"] and STATE["process"].poll() is None))
                return self.send(out)
            if url.path == "/api/state":
                return self.send(state(project))
            if url.path == "/api/events":
                it = int(q.get("iteration", ["0"])[0]) or None
                out = events(project, it, int(q.get("since", ["0"])[0]))
                folder = run_dir(project, it)
                if folder:
                    from harness import Store
                    out["control"] = Store(folder).control()
                return self.send(out)
            if url.path == "/api/result":
                folder = run_dir(project, int(q.get("iteration", ["0"])[0]) or None)
                file = folder / "results" / f"{q.get('task', [''])[0]}.json" if folder else None
                return self.send(file.read_bytes() if file and file.exists() else b"null")
            if url.path == "/api/function":
                return self.send(function_files(project, q["id"][0], q["version"][0]))
            if url.path == "/api/file":
                file = (project.root / q["path"][0]).resolve()
                if project.root.resolve() in file.parents and file.exists():
                    return self.send(file.read_bytes(), TYPES.get(file.suffix, "text/plain; charset=utf-8"))
                return self.send({"error": "not found"}, code=404)
            file = DIST / ("index.html" if url.path in ("/", "") else url.path.lstrip("/"))
            if file.resolve() != DIST.resolve() and DIST.resolve() in file.resolve().parents and file.exists():
                return self.send(file.read_bytes(), TYPES.get(file.suffix, "application/octet-stream"))
            self.send({"error": "not found"}, code=404)
        except Exception as exc:
            self.send({"error": str(exc)}, code=500)

    def do_POST(self):
        url = urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        try:
            if url.path == "/api/run":
                return self.send(start_run(body.get("kind", "new"), body.get("text", ""), body.get("project"), bool(body.get("offline")), body.get("kit") or "cubesat", str(body.get("part") or "")))
            if url.path == "/api/settings":
                return self.send(save_settings(body))
            if url.path == "/api/reveal":
                return self.send(reveal(body.get("path", "")))
            if url.path == "/api/harness/generate":           # the one place the page asks a model directly
                from agents.config import Settings
                was, Settings.offline = Settings.offline, bool(body.get("offline"))
                try:
                    return self.send({"draft": designer.generate(body.get("scenario", ""), body.get("tools") or [])})
                finally:
                    Settings.offline = was
            if url.path == "/api/memory":
                if body.get("forget"):
                    return self.send(memory_op("forget", str(body["forget"])))
                scope = body.get("scope") or None                               # a project name, or None for the shared pool
                return self.send(memory_op("add", str(body.get("text", "")).strip(), project=scope) if str(body.get("text", "")).strip() else {"error": "Write the lesson first."})
            if url.path == "/api/control":                     # pause, stop a task, or raise a task's tier, between attempts
                project = Project(body["project"])
                folder = run_dir(project, int(body.get("iteration") or 0) or None)
                if not folder:
                    return self.send({"error": "No run to control."})
                from harness import Store
                store, action = Store(folder), body.get("action")
                if action == "pause":
                    return self.send(store.set_control(paused=True))
                if action == "resume":
                    return self.send(store.set_control(paused=False))
                if action == "stop":
                    return self.send(store.set_control(stop=sorted(set(store.control().get("stop", [])) | {body["task"]})))
                if action == "tier":
                    tiers = {**store.control().get("tier", {}), body["task"]: body["tier"]}
                    if body.get("keep") and body.get("capability"):
                        project.set_specialist_tier(body["capability"], body["tier"])
                    return self.send(store.set_control(**{"tier": tiers, "paused": False}))
                return self.send({"error": f"unknown action {action}"})
            if url.path == "/api/tools/check":                # read a tool's source without running it
                return self.send(tooling.inspect_code(body.get("name", ""), body.get("code", "")))
            if url.path == "/api/harness/save":
                return self.send(designer.save(body["kind"], body["name"], body["draft"]))
            project = Project(body["project"])
            if url.path == "/api/evaluate":
                design = dict(project.designs().get(body.get("design", ""), {"name": body.get("design") or "design", "tags": {}, "description": ""}))
                design["values"], design["tags"] = body["values"], {**design.get("tags", {}), **{k: "user" for k, v in body["values"].items() if design.get("values", {}).get(k) != v}}
                design["usages"] = body.get("usages") or design.get("usages") or engine.default_usages(project.interfaces().get("structure") or [])
                result = runtime.evaluate_design(project, design, body.get("versions"))
                result["sysml"] = render.sysml(project, design, result)
                if body.get("design") in project.designs():       # the run writes the outputs, as the exported build system does
                    folder = project.path("outputs", body["design"]); folder.mkdir(parents=True, exist_ok=True)
                    (folder / "design.sysml").write_text(result["sysml"], encoding="utf-8")
                    (folder / "report.html").write_text(render.report(project, design, result), encoding="utf-8")
                return self.send(result)
            if url.path == "/api/emergent":
                base = project.designs().get(body.get("design", ""), {})
                return self.send(runtime.sensitivity(project, {"usages": body.get("usages") or base.get("usages"), "values": body["values"]}, pairs=body.get("pairs", True)))
            if url.path == "/api/route":
                project.route(body["id"], body["version"])
                return self.send({"ok": True})
            if url.path == "/api/project/delete":              # moves the folder to projects/.trash unless purge is set
                if STATE["process"] and STATE["process"].poll() is None and (STATE.get("last") or {}).get("project") == body.get("project"):
                    return self.send({"error": "A build is running on this project. Stop it first."})
                from .project import delete_project
                return self.send({"ok": True, **delete_project(body["project"], bool(body.get("purge")))})
            if url.path == "/api/rollback":                    # undo the latest request. No model call
                if STATE["process"] and STATE["process"].poll() is None:
                    return self.send({"error": "A build is running. Stop it first."})
                from .pipeline import rerender
                undone = project.rollback()
                rerender(project)
                return self.send({"ok": True, **undone})
            self.send({"error": "not found"}, code=404)
        except Exception as exc:
            self.send({"error": str(exc)}, code=500)


def serve(port: int = 8770, offline: bool = False) -> None:
    STATE["offline"] = offline
    if not (DIST / "index.html").exists():
        raise SystemExit("The interface is not built. It ships in ui/dist. To rebuild it: cd ui; npm install; npm run build")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"GFDSB, the Ground & Form Design System Builder, at http://localhost:{port}   (Ctrl+C to stop)")
    print("Builds started from the page run " + ("offline, replaying saved answers." if offline else "live, unless you tick Offline."))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
