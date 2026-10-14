"""GFDSB: the Ground & Form Design System Builder.

A project defines a harness: what is being built, which agents build it, and the rules they follow.
The studio turns a request into a versioned build system, plus example inputs, and shows how it was made.

  python studio.py kits                                           harness definitions a project can start from
  python studio.py harness --name my-kit --file scenario.md       draft a team of agents from an operational scenario
  python studio.py new --offline                                  the CubeSat example request, replayed from saved answers
  python studio.py new "Build me a 6U ..." --kit cubesat          live, from your own text
  python studio.py new --kit blank --file my_request.md           anything else: a generic systems engineer and specialists
  python studio.py resume                                         continue a build that stopped: finished tasks are kept
  python studio.py rebuild                                        start request 1 over from nothing
  python studio.py followup --offline                             the example follow-up. Or give your own text
  python studio.py drilldown SolarArray "split by cell technology"  decompose one part further: a specialist proposes, the SE reviews
  python studio.py rollback                                       undo the latest request: model, routing, and designs as they were
  python studio.py delete --project NAME                          move a project to projects/.trash (add --purge to remove it for good)
  python studio.py status                                         functions, versions, evidence, designs
  python studio.py evaluate baseline tx_power_w=4                 run a design through the build system. No model call
  python studio.py route cubesat.radiation.dose v1                point a static identifier at another version
  python studio.py memory [add "..."|search "..."|forget ID|all]   the systems engineer's lessons. --project scopes them; without it, the shared pool
  python studio.py export                                         the build system as a standalone folder. No studio, no AI
  python studio.py ui                                             the live interface at http://localhost:8770

Add --project NAME to pick a project. The default is the most recent one.
Offline mode replays saved answers for the CubeSat example, whatever text you give it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agents.config import MODELS, ROOT, Settings, load_env, require_key
from harness import console


def pick(name: str | None):
    from studio.project import Project, latest
    project = Project(name) if name else latest()
    if not project or not project.exists:
        from studio.project import PROJECTS
        raise SystemExit(f"No project in {PROJECTS}. Start one: python studio.py new --offline")
    return project


def text_of(args, example: str = "") -> str:
    if args.file:
        return open(args.file, encoding="utf-8-sig").read()
    if args.text:
        return " ".join(args.text)
    if example:
        print("No request given. Using the example that ships with this harness definition.\n")
        return example
    raise SystemExit("Give the request as text, or with --file path.md")


def run(project, iteration: int, args, resume: bool = False) -> None:
    from harness import Orchestrator, Status, Store
    from studio.pipeline import plan
    runs = project.read("runs.json", []) or []
    earlier = [r for r in runs if r["iteration"] == iteration and Path(r["run"]).exists()]
    if resume and earlier:
        run_dir = Path(earlier[-1]["run"])                   # same folder: finished tasks are kept, the rest run again
        print(f"Resuming   {run_dir.name}: tasks with a saved result are kept")
    else:
        run_dir = ROOT / "runs" / f"studio-{project.name}-i{iteration}-{time.strftime('%H%M%S')}"
        project.write("runs.json", runs + [{"iteration": iteration, "run": str(run_dir)}])
    store = Store(run_dir)
    orch = Orchestrator(store, max_workers=args.workers, step=args.step, resume=resume and bool(earlier))
    orch.add(*plan(project, iteration, say=console.say, max_reviews=args.max_reviews))
    h = project.harness()
    print(f"Project    projects/{project.name}   request {iteration}")
    print(f"Harness    {h['name']}: building a {h['building']} with {len(h['agents'].get('specialists', []))} specialist agent(s). Version {project.harness_version()}")
    print("Mode       " + ("offline replay of saved answers, no API calls" if Settings.offline else
                           f"live. Systems engineer on {MODELS['deep']}, specialists on {MODELS['balanced']}"))
    print("Interface  run `python studio.py ui` in a second terminal to watch it live\n")
    t0 = time.time()
    tasks = orch.run()
    print(f"\nFinished in {time.time() - t0:.1f} s. {len(tasks)} tasks: {orch.summary()}")
    for t in tasks.values():
        if t.status is Status.FAILED:
            print(f"  FAILED {t.id}: {t.errors[-1][:160]}")
            if console.hint(t.errors[-1]):
                print(f"         {console.hint(t.errors[-1])}")
    print()
    status(project)


def status(project) -> None:
    spec, registry = project.spec(), project.registry()
    ai = sum(p["source"] == "ai" for p in spec.get("parameters", []))
    print(f"{spec.get('mission_name', project.name)}   (projects/{project.name}, iteration {project.iteration})")
    print(f"  spec        {len(spec.get('parameters', []))} parameters, {ai} filled in by an agent. {len(spec.get('requirements', []))} requirements")
    print(f"  interfaces  version {project.interfaces().get('version', 0)}: {len(project.interfaces().get('contracts', []))} contracts, "
          f"{len(project.interfaces().get('exchanges', []))} exchanges\n")
    print(f"  {'static identifier':<26} {'active':<7} {'versions':<9} {'agent tests':>11} {'worked cases':>13} {'from you':>9}")
    for fid, entry in registry.items():
        v = entry["versions"][entry["active"]]
        acc = v["gates"].get("acceptance") or {}
        print(f"  {fid:<26} {entry['active']:<7} {', '.join(entry['versions']):<9} {(v['gates'].get('own_tests') or {}).get('passed', 0):>11} "
              f"{str(acc.get('passed', 0)) + ' of ' + str(acc.get('passed', 0) + acc.get('failed', 0)):>13} {v['evidence'].get('user_cases', 0):>9}")
    designs = project.designs()
    if designs:
        print(f"\n  {'design':<28} result")
        for name in designs:
            r = project.read(f"outputs/{name}/results.json", {})
            line = "meets every requirement" if r.get("passes") else ", ".join([f["rule"] for f in r.get("findings", [])] + list(r.get("errors", {}))) or "not evaluated"
            print(f"  {name:<28} {line}")
        print(f"\n  Reports and SysML v2: projects/{project.name}/outputs/<design>/")
    print("  Next: python studio.py evaluate baseline     python studio.py followup \"...\"     python studio.py ui")


def evaluate(project, name: str, items: list[str]) -> None:
    from studio import runtime
    designs = project.designs()
    if name not in designs:
        raise SystemExit(f"No design named '{name}'. Known: {', '.join(designs)}")
    design = {**designs[name], "values": dict(designs[name]["values"])}
    for item in items:                                       # name=value, or usage.path.name=value for one usage
        key, _, raw = item.partition("=")
        try:
            design["values"][key] = json.loads(raw)
        except json.JSONDecodeError:
            design["values"][key] = raw
    result = runtime.evaluate_design(project, design)
    for x in result["exchanges"]:
        print(f"  exchange {x['id']:<5} {(x.get('usage') + '.' if x.get('usage') else '') + x['target_input']:<30} = {x.get('value', x.get('error'))}")
    for fid, r in result["functions"].items():
        if "error" in r:
            print(f"  {fid:<26} {r.get('version', ''):<4} could not evaluate: {r['error']}")
            continue
        for path, u in r.get("usages", {}).items():
            if "result" not in u:
                print(f"  {fid:<26} {r.get('version', ''):<4} on {path}: {u.get('error')}"); continue
            shown = ", ".join(f"{k} {v}" for k, v in u["result"].items() if k != "passes" and not k.startswith("required"))
            print(f"  {fid:<26} {r['version']:<4} {'passes' if u['result']['passes'] else 'FAILS ':<7} {(path + ': ') if len(r['usages']) > 1 else ''}{shown}")
    for f in result["findings"]:
        print(f"  finding {f['rule']}{(' on ' + f['usage']) if f.get('usage') else ''}: {f['message']}")
    print("  " + ("Meets every requirement." if result["passes"] else "Does not meet every requirement.") + "  No model was called.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["kits", "harness", "new", "resume", "rebuild", "followup", "drilldown", "rollback", "delete", "memory", "status", "evaluate", "route", "export", "ui"])
    p.add_argument("--kit", default="cubesat", help="harness definition a new project starts from")
    p.add_argument("--name", help="name for a new harness definition")
    p.add_argument("text", nargs="*")
    p.add_argument("--file", help="read the request from a file")
    p.add_argument("--part", help="drilldown: the part to decompose further")
    p.add_argument("--purge", action="store_true", help="delete: remove the project for good instead of moving it to projects/.trash")
    p.add_argument("--project", help="project name. Default: the most recent")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--step", action="store_true", help="pause before each batch of ready tasks")
    p.add_argument("--pace", type=float, default=0.0, help="seconds per replayed agent call")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--max-reviews", type=int, default=2, help="cap on systems engineer review rounds")
    p.add_argument("--port", type=int, default=8770)
    args = p.parse_args()
    load_env()
    Settings.offline = Settings.offline or args.offline
    Settings.pace = args.pace or Settings.pace

    if args.command == "ui":
        from studio.server import serve
        serve(args.port, offline=Settings.offline)
    elif args.command == "kits":
        from studio.project import kits
        for k in kits().values():
            print(f"  {k['kit']:<10} {k['name']}: builds a {k['building']}. Specialists: {', '.join(k['specialists']) or 'generic only'}")
        print("\n  A kit is a folder under kits/. Copy one to make your own: harness.yaml, agents/, library/, evidence/.")
    elif args.command == "harness":
        require_key()
        from studio import designer
        from studio.project import kits
        scenario = text_of(args, (ROOT / "kits" / "cubesat" / "scenario.md").read_text(encoding="utf-8") if Settings.offline else "")
        with console.status("An agent is proposing the team", report=False):
            draft = designer.generate(scenario, designer.load("kit", args.name)["tools"] if args.name in kits() else [])
        result = designer.save("kit", args.name or draft["id_prefix"], draft)
        print(f"{draft['name']}: builds a {draft['building']}. {len(draft['agents'])} specialist agents, plus the systems engineer.")
        for path in result["written"]:
            print(f"  wrote {path}")
        for note in draft["notes"]:
            print(f"  note: {note}")
        print(f"\nEdit the files, or use the Harness view in the interface. Then: python studio.py new --kit {result['name']} --file your_request.md")
    elif args.command == "new":
        require_key()
        from studio.project import Project, kits, slug
        if args.kit not in kits():
            raise SystemExit(f"No harness definition named '{args.kit}'. Known: {', '.join(kits())}")
        if Settings.offline and args.kit != "cubesat":
            raise SystemExit("Offline mode has saved answers for the cubesat kit only. Run this one live.")
        request = text_of(args, kits()[args.kit]["example_request"])
        from studio.project import unused_name
        project = Project(args.project or unused_name("cno-sat-1" if Settings.offline else slug(request.splitlines()[0])))
        project.create_from_kit(args.kit)
        (Settings.memory_dir / "systems_engineer.offline.json").unlink(missing_ok=True) if Settings.offline else None
        run(project, project.add_request(request), args)
    elif args.command == "followup":
        require_key()
        from studio.project import kits
        project = pick(args.project)
        if not project.built:
            raise SystemExit(f"The first build of '{project.name}' did not finish, so there is nothing to follow up on yet.\n"
                             f"Run it again:  python studio.py rebuild --project {project.name}")
        example = kits().get((project.read("kit.json", {}) or {}).get("kit", ""), {}).get("example_followup", "")
        run(project, project.add_request(text_of(args, example)), args)
    elif args.command == "drilldown":
        require_key()
        project = pick(args.project)
        if not project.built:
            raise SystemExit(f"The first build of '{project.name}' did not finish, so there is nothing to decompose yet.")
        part = args.part or (args.text[0] if args.text else "")
        parts = {p["name"] for p in project.interfaces().get("structure", [])}
        if part not in parts:
            raise SystemExit(f"Name the part to decompose. Known parts: {', '.join(sorted(parts))}")
        guidance = ""
        if args.file:
            guidance = open(args.file, encoding="utf-8-sig").read()
        elif args.text:
            guidance = " ".join(args.text[1:] if not args.part else args.text)
        run(project, project.add_request(guidance or f"Decompose {part} further.", kind="drilldown", part=part), args)
    elif args.command == "delete":
        from studio.project import delete_project
        if not args.project:
            raise SystemExit("Name the project: python studio.py delete --project NAME")
        try:
            done = delete_project(args.project, purge=args.purge)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"Removed {args.project}." if done.get("purged") else f"Moved {args.project} to {done['moved_to']}. Move the folder back to restore it, or delete it there.")
    elif args.command == "rollback":
        from studio.pipeline import rerender
        project = pick(args.project)
        try:
            undone = project.rollback()
        except ValueError as exc:
            raise SystemExit(str(exc))
        rerender(project)
        what = f"drill-down of {undone['part']}" if undone["kind"] == "drilldown" else "follow-up"
        print(f"Rolled back request {undone['iteration']} ({what}). Active: " + ", ".join(f"{k.split('.')[1]} {v}" for k, v in undone["active"].items()))
        print("  The request is in requests/rolled_back/. Versions it built stay in the registry, inactive.\n")
        status(project)
    elif args.command == "resume":
        require_key()
        project = pick(args.project)
        if project.built and project.iteration == 1:
            raise SystemExit(f"'{project.name}' is already built. Send a follow-up, or start a new project.")
        for f in sorted(project.path("requests").glob("*.md"))[1:] if not project.built else []:
            target = project.path("requests", "not_applied", f.name); target.parent.mkdir(parents=True, exist_ok=True); f.replace(target)
            print(f"  set aside a follow-up that had nothing to change: {target}")
        run(project, project.iteration, args, resume=True)
    elif args.command == "rebuild":
        require_key()
        project = pick(args.project)
        if project.built:
            raise SystemExit(f"'{project.name}' is already built. Send a follow-up, or start a new project.")
        for path in project.reset_build():
            print(f"  set aside a follow-up that had nothing to change: {path}")
        run(project, 1, args)
    elif args.command == "memory":
        from agents.memory import SHARED, open_memory
        memory, verb = open_memory(), (args.text[0] if args.text else "list")
        rest = " ".join(args.text[1:]).strip()
        scope = args.project or SHARED                       # --project scopes the lesson; without it, the shared pool
        if verb == "add" and rest:
            memory.add(rest, source="person", scope=scope, run=args.project or "")
        elif verb == "forget" and rest:
            memory.forget(rest)
        elif verb == "search" and rest:
            print(json.dumps({"hits": memory.search(rest, k=8, scope=args.project, shared=True)}, indent=2)); raise SystemExit
        print(json.dumps({"entries": memory.entries(scope=None if verb == "all" else (args.project or None))}, indent=2, default=str))
    elif args.command == "export":
        from studio.export import export
        project = pick(args.project)
        print(f"Build system written to {export(project)}\n  It needs Python and nothing else:  python run.py baseline")
    elif args.command == "status":
        status(pick(args.project))
    elif args.command == "evaluate":
        evaluate(pick(args.project), args.text[0] if args.text else "baseline", args.text[1:])
    elif args.command == "route":
        if len(args.text) != 2:
            raise SystemExit("Usage: python studio.py route <static identifier> <version>")
        project = pick(args.project)
        try:
            project.route(args.text[0], args.text[1])
        except (KeyError, ValueError) as exc:
            raise SystemExit(str(exc))
        print(f"{args.text[0]} now routes to {args.text[1]}. Nothing else changed.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    main()
