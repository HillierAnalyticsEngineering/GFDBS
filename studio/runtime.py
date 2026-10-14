"""The runtime of a generated build system, bound to a project. No model is called from this file.

The semantics live in engine.py, which the exported build system carries unchanged: a design is a tree
of part usages with one flat dictionary of values keyed by usage path; a calc belongs to a part
definition and runs once per usage; aggregates roll children up; exchanges run per target usage.
This module adds what needs the project: routing a call through the registry, loading function
modules, and the sensitivity sweep.
"""
from __future__ import annotations

from . import engine, gates
from .engine import AGGREGATES, compute, describe_cycle, identity_source, names_in, schedule  # noqa: F401  re-exported
from .project import Project

_modules: dict = {}


def _module(folder, name: str):
    """Load a function module once per file version. A sensitivity sweep calls each function hundreds of times."""
    source = folder / "solution.py"
    stamp = source.stat().st_mtime_ns
    hit = _modules.get(source)
    if hit is None or hit[0] != stamp:
        hit = _modules[source] = (stamp, gates.load_module(folder, name))
    return hit[1]


def call(project: Project, function_id: str, inputs: dict, version: str | None = None) -> dict:
    """Route one call through the registry. `version` overrides the active version for a single call."""
    registry = project.registry()
    if function_id not in registry:
        raise LookupError("not built")
    entry = registry[function_id]
    version = version or entry["active"]
    module = _module(project.function_dir(function_id, version), f"{function_id}_{version}")
    measures, _ = project.evidence(entry["capability"])
    used = {k: inputs[k] for k in module.PARAMETERS if k in inputs}
    return {"function_id": function_id, "version": version, "inputs": used,
            "result": module.evaluate(used, measures), "findings": module.check(used, measures)}


def as_design(project: Project, design_or_values: dict) -> dict:
    """Callers may pass a design (usages + values) or just a flat values dictionary. A flat dictionary is
    laid over the baseline's usage tree when there is one, else over the structure's default tree."""
    if "values" in design_or_values and isinstance(design_or_values["values"], dict):
        return design_or_values
    base = project.designs().get("baseline", {})
    return {"usages": base.get("usages") or engine.default_usages(project.interfaces().get("structure") or []), "values": dict(design_or_values)}


def evaluate_design(project: Project, design_or_values: dict, versions: dict | None = None) -> dict:
    """Evaluate one design instance through the whole build system."""
    design = as_design(project, design_or_values)
    return engine.evaluate(project.interfaces(), design, lambda fid, inputs, v: call(project, fid, inputs, v), versions)


def metric(result: dict) -> float | None:
    """One number per function that says how much room it has. Used to see what moves when an input moves.

    Prefers an output that reads like a margin. Otherwise the last numeric output that is not a requirement.
    """
    numbers = {k: v for k, v in result.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and not k.startswith("required") and k != "usages"}
    for key, value in numbers.items():
        if any(word in key for word in ("margin", "headroom", "rdm", "reserve", "slack")):
            return value
    return list(numbers.values())[-1] if numbers else None


def _owners(interfaces: dict) -> dict:
    """attribute name -> the capability whose part carries it (or whose exchange reads it)."""
    structure = interfaces.get("structure") or []
    cap_of_part = {p["name"]: p["capability"] for p in structure}
    owner = {}
    for c in interfaces["contracts"]:
        for term in c["inputs"]:
            owner.setdefault(term["name"], cap_of_part.get(term.get("part", ""), c["capability"]))
    for c in interfaces["contracts"]:
        for term in c.get("outputs", []):
            owner.setdefault(term["name"], c["capability"])
    for x in interfaces.get("exchanges", []):
        for name in names_in(x["expression"]):
            owner.setdefault(name, cap_of_part.get(x["source_element"], x["source_element"]))
    return owner


def _resolution(*numbers: float) -> float:
    """The rounding step the function itself uses, read off its outputs. Differences below it are noise."""
    decimals = 0
    for n in numbers:
        text = repr(float(n))
        if "e" not in text and "." in text:
            decimals = max(decimals, len(text.split(".")[1].rstrip("0")))
    return 10.0 ** -decimals


def sensitivity(project: Project, design_or_values: dict, step: float = 0.10, pairs: bool = True, max_pairs: int = 1500) -> dict:
    """Move inputs and watch every function. No model is called.

    First order: each numeric value alone, down and up by `step`. A value on one usage moves on that usage
    only, so two panels are two rows. For every function this gives the swing of its margin metric and
    whether its pass flips. That is a tornado per function.
    Second order: each pair of values together, up by `step`. The interaction is the part of the
    joint change the two single changes do not explain:  f(i+, j+) - f(i+) - f(j+) + f(base).
    A pair whose values belong to different elements and whose interaction is not small is the
    kind of behavior no single specialist could have seen. Those are the emergent candidates.
    """
    design = as_design(project, design_or_values)
    values = design["values"]
    interfaces = project.interfaces()
    owner = _owners(interfaces)
    attr = lambda key: key.rsplit(".", 1)[-1]
    capability = {c["function_id"]: c["capability"] for c in interfaces["contracts"]}
    run = lambda vals: evaluate_design(project, {**design, "values": vals})
    base = run(values)
    fids = list(base["functions"])
    base_metric = {fid: metric(base["functions"][fid].get("result", {})) for fid in fids}
    base_pass = {fid: base["functions"][fid].get("result", {}).get("passes") for fid in fids}
    movable = [k for k, v in values.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0]

    def metrics(trial: dict) -> dict:
        return {fid: metric(trial["functions"].get(fid, {}).get("result", {})) for fid in fids}

    single, flips, up = {}, [], {}
    for name in movable:
        single[name] = {}
        for direction, label in ((1 - step, "low"), (1 + step, "high")):
            trial = run({**values, name: values[name] * direction})
            m = metrics(trial)
            if label == "high":
                up[name] = m
            for fid in fids:
                single[name].setdefault(fid, {})[label] = m[fid]
                now = trial["functions"].get(fid, {}).get("result", {}).get("passes")
                if base_pass[fid] is not None and now is not None and now != base_pass[fid]:
                    flips.append({"input": name, "owner": owner.get(attr(name)), "change": f"{direction - 1:+.0%}", "function_id": fid,
                                  "passes_before": base_pass[fid], "passes_after": now, "cross_domain": owner.get(attr(name)) not in (None, capability[fid])})
    tornado = {}
    for fid in fids:
        rows = []
        for name in movable:
            lo, hi, b = single[name][fid].get("low"), single[name][fid].get("high"), base_metric[fid]
            if lo is None or hi is None or b is None:
                continue
            rows.append({"input": name, "owner": owner.get(attr(name)), "cross": owner.get(attr(name)) not in (None, capability[fid]),
                         "low": round(lo, 4), "high": round(hi, 4), "swing": round(abs(hi - lo), 4), "value": values[name]})
        rows.sort(key=lambda r: -r["swing"])
        tornado[fid] = {"capability": capability[fid], "part": base["functions"][fid].get("part", ""), "base": base_metric[fid], "passes": base_pass[fid], "rows": rows}
    couplings = [{"input": r["input"], "owner": r["owner"], "affects": fid, "metric_before": t["base"], "metric_after": r["high"]}
                 for fid, t in tornado.items() for r in t["rows"] if r["cross"] and r["swing"] > 1e-6]
    interactions = []
    if pairs:
        weight = {n: sum(abs((single[n][f].get("high") or 0) - (single[n][f].get("low") or 0)) for f in fids if base_metric[f] is not None) for n in movable}
        candidates = sorted(((a, b) for i, a in enumerate(movable) for b in movable[i + 1:]), key=lambda p: -(weight[p[0]] * weight[p[1]]))[:max_pairs]
        for a, b in candidates:
            if weight[a] == 0 and weight[b] == 0:
                break
            joint = metrics(run({**values, a: values[a] * (1 + step), b: values[b] * (1 + step)}))
            for fid in fids:
                f0, fa, fb, fab = base_metric[fid], up[a][fid], up[b][fid], joint[fid]
                if None in (f0, fa, fb, fab):
                    continue
                effect = fab - fa - fb + f0
                scale = max(abs(fa - f0), abs(fb - f0), 1e-9)
                if abs(effect) > 1.5 * _resolution(f0, fa, fb, fab) and abs(effect) / scale >= 0.05:
                    interactions.append({"inputs": [a, b], "owners": [owner.get(attr(a)), owner.get(attr(b))], "function_id": fid,
                                         "main_a": round(fa - f0, 4), "main_b": round(fb - f0, 4), "interaction": round(effect, 4),
                                         "relative": round(abs(effect) / scale, 3), "cross_domain": len({owner.get(attr(a)), owner.get(attr(b)), capability[fid]} - {None}) > 1})
        interactions.sort(key=lambda r: -r["relative"])
    return {"baseline_passes": base["passes"], "step": step, "tornado": tornado, "flips": flips, "couplings": couplings,
            "interactions": interactions[:60], "inputs_moved": len(movable)}


def emergent_effects(project: Project, values: dict, step: float = 0.10) -> dict:
    """Kept for callers that used the old name. See sensitivity()."""
    return sensitivity(project, values, step)
