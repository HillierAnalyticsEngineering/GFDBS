"""Write the build system out of the studio as a standalone folder.

The export holds the generated functions, the registry, the interface spec, the evidence, the
example inputs, and one small runner. It imports nothing from the studio, calls no model, and
needs only the Python standard library.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .project import Project, ROOT

RUNNER = '''"""A build system exported from GFDSB, the Ground & Form Design System Builder. Standard library only. No AI.

  python run.py                                 list the example designs
  python run.py baseline                        evaluate one
  python run.py baseline name=value             evaluate it with a value changed
  python run.py baseline eps.panel1.x=0.04      change a value on one usage

A design is a tree of part usages (inputs/<design>.json) with one flat dictionary of values keyed by
usage path. Each calc runs once per usage of its part; engine.py beside this file is the same engine the
studio uses. Each evaluation writes outputs/<design>/design.sysml, the SysML v2 model of that instance,
and outputs/<design>/result.json.
"""
import importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine                                     # the evaluation engine that travels with this build system
from sysml import sysml_text                      # and the renderer

load = lambda name: json.loads((HERE / name).read_text(encoding="utf-8"))
_modules = {}


def function(function_id, version):
    key = (function_id, version)
    if key not in _modules:
        folder = HERE / "functions" / function_id / version
        spec = importlib.util.spec_from_file_location(f"fn_{len(_modules)}", folder / "solution.py")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(folder)); spec.loader.exec_module(module); sys.path.remove(str(folder))
        _modules[key] = module
    return _modules[key]


def evaluate(design):
    interfaces, registry, evidence = load("interfaces.json"), load("registry.json"), load("evidence.json")
    capability = {c["function_id"]: c["capability"] for c in interfaces["contracts"]}

    def call(function_id, inputs, version):
        if function_id not in registry:
            raise LookupError("not built")
        version = version or registry[function_id]["active"]
        module = function(function_id, version)
        used = {k: inputs[k] for k in module.PARAMETERS if k in inputs}
        measures = evidence[capability[function_id]]
        return {"version": version, "inputs": used, "result": module.evaluate(used, measures), "findings": module.check(used, measures)}

    summary = engine.evaluate(interfaces, design, call)
    for x in summary["exchanges"]:
        print(f"  exchange {x['id']:<5} {(x.get('usage', '') + '.' if x.get('usage') else '') + x['target_input']:<34} = {x.get('value', x.get('error'))}")
    for fid, r in summary["functions"].items():
        for path, u in r.get("usages", {}).items():
            if "result" not in u:
                print(f"  {fid:<28} {r.get('version', ''):<4} on {path}: {u.get('error')}"); continue
            shown = ", ".join(f"{k} {v}" for k, v in u["result"].items() if k != "passes" and not k.startswith("required"))
            print(f"  {fid:<28} {r['version']:<4} {'passes' if u['result']['passes'] else 'FAILS '}  {(path + ': ') if len(r['usages']) > 1 else ''}{shown}")
    for f in summary["findings"]:
        print(f"    {f['rule']}{(' on ' + f['usage']) if f.get('usage') else ''}: {f['message']}")
    folder = HERE / "outputs" / design["name"]; folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (folder / "design.sysml").write_text(sysml_text(load("spec.json"), interfaces, registry, load("harness.json")["building"], design, summary), encoding="utf-8")
    print(f"  SysML v2 model: {folder / 'design.sysml'}")
    print("  Meets every requirement." if summary["passes"] else "  Does not meet every requirement.")


if __name__ == "__main__":
    designs = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted((HERE / "inputs").glob("*.json"))}
    if len(sys.argv) < 2:
        for name, d in designs.items(): print(f"  {name:<28} {d.get('description', '')[:90]}")
        raise SystemExit("\\nEvaluate one:  python run.py baseline")
    design = dict(designs[sys.argv[1]]); design["values"] = dict(design["values"])
    for item in sys.argv[2:]:
        key, _, raw = item.partition("=")
        try: design["values"][key] = json.loads(raw)
        except json.JSONDecodeError: design["values"][key] = raw
        design["tags"] = {**design.get("tags", {}), key: "user"}
    evaluate(design)
'''

def export(project: Project) -> Path:
    out = ROOT / "exports" / f"{project.name}-build-system"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    registry = project.registry()
    for fid, entry in registry.items():                 # every version travels, so routing still works
        shutil.copytree(project.path("functions", fid), out / "functions" / fid)
    for name in ("registry.json", "interfaces.json", "spec.json", "changelog.json"):
        shutil.copy(project.path(name), out / name)
    shutil.copytree(project.path("inputs"), out / "inputs")
    capabilities = sorted({e["capability"] for e in registry.values()})
    (out / "evidence.json").write_text(json.dumps({c: project.evidence(c)[0] for c in capabilities}, indent=2), encoding="utf-8")
    (out / "run.py").write_text(RUNNER, encoding="utf-8")
    for name in ("sysml.py", "engine.py"):              # the engine and the renderer travel with the build system
        shutil.copy(Path(__file__).with_name(name), out / name)
    (out / "harness.json").write_text(json.dumps({"name": project.harness()["name"], "building": project.harness()["building"]}, indent=2), encoding="utf-8")
    h = project.harness()
    (out / "README.md").write_text(
        f"# {h['name']}: build system\n\nExported from GFDSB (Ground & Form Design System Builder), project `{project.name}`, after request {project.iteration}.\n\n"
        f"Agents wrote the functions in `functions/`. Nothing here calls a model.\n\n```\npython run.py\npython run.py baseline\n```\n\nEach evaluation writes `outputs/<design>/design.sysml` (SysML v2) and `outputs/<design>/result.json`.\n\n"
        + "| Static identifier | Active | Versions |\n|---|---|---|\n"
        + "".join(f"| `{fid}` | {e['active']} | {', '.join(e['versions'])} |\n" for fid, e in registry.items()), encoding="utf-8")
    return out
