"""SysML v2 textual notation for one design instance. Standard library only, so the exported
build system carries this file and renders the model with no studio and no model call.

The metamodel is one package per subsystem: its part definitions nested as the structure composes
them, each calc as a calc def declared on the part definition it belongs to, and one calc def per
exchange carrying the expression. The design instance is the usage tree as nested part usages, each
with the literals it carries and the values its calcs computed.
"""
from __future__ import annotations

import re

try:
    from . import engine
except ImportError:                                  # exported: engine.py sits beside this file
    import engine


def _ident(name: str) -> str:
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", str(name)) and any(ch.isupper() for ch in str(name)[1:]):
        return name[0].lower() + name[1:]          # already camel or Pascal case: keep the humps
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", str(name)) if p]
    text = (parts[0].lower() + "".join(p[:1].upper() + p[1:] for p in parts[1:])) if parts else "x"
    return text if text[0].isalpha() else "x" + text


def _type(value) -> str:
    return "Boolean" if isinstance(value, bool) else "Real" if isinstance(value, (int, float)) else "String"


def _literal(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value) if isinstance(value, (int, float)) else f'"{value}"'


def _calc_name(function_id: str) -> str:
    noun = function_id.split(".")[-1]
    return _ident(noun)[:1].upper() + _ident(noun)[1:]


def sysml_text(spec: dict, interfaces: dict, registry: dict, building: str, design: dict, result: dict, project_name: str = "design") -> str:
    """The metamodel as part definitions with their calc defs, the requirements, then this design as one
    usage tree: literals for the inputs, computed values for the outputs, connections, and which
    requirements the instance satisfies."""
    mission = _ident(spec.get("mission_name", project_name)).capitalize()
    whole = _ident(building)[:1].upper() + _ident(building)[1:]
    contracts = interfaces["contracts"]
    structure = interfaces.get("structure") or []
    caps = []
    for c in contracts:
        if c["capability"] not in caps:
            caps.append(c["capability"])
    if not structure:                                    # an older spec with no decomposition: one flat part per capability
        structure = [{"name": _ident(cap).capitalize() + "Element", "parent": "", "specializes": "", "multiplicity": "1", "capability": cap, "description": ""} for cap in caps]
    parts = engine.by_name(structure)
    part_of = {c["function_id"]: engine.contract_part(c, structure) for c in contracts}
    children = {}
    for p in structure:
        children.setdefault(p["parent"], []).append(p["name"])
    roots_by_cap = {}
    for p in structure:
        if not p["parent"] and not p.get("specializes"):
            roots_by_cap.setdefault(p["capability"], []).append(p["name"])
    attrs_of = {}                                        # part name -> [(contract, term)] plain attributes carried by that part
    for c in contracts:
        for term in c["inputs"]:
            if term.get("aggregate"):                    # the per-usage attribute an aggregate sums lives on the aggregated part
                of = term.get("of") or term["name"]
                if of != term["name"] and not any(t["name"] == of for _, t in attrs_of.get(term.get("part", ""), [])):
                    attrs_of.setdefault(term.get("part", ""), []).append((c, {**term, "name": of, "aggregate": ""}))
                continue
            attrs_of.setdefault(term.get("part") or part_of[c["function_id"]], []).append((c, term))
    calcs_on = {}                                        # part name -> [contract]
    for c in contracts:
        calcs_on.setdefault(part_of[c["function_id"]], []).append(c)
    question = {c["capability"]: c["question"] for c in contracts}
    pkg = {cap: _ident(cap)[:1].upper() + _ident(cap)[1:] for cap in caps}
    inherited = {}
    for p in structure:
        sup, seen = p.get("specializes", ""), set()
        while sup and sup in parts and sup not in seen:
            seen.add(sup); inherited.setdefault(p["name"], []).extend(term["name"] for _, term in attrs_of.get(sup, [])); sup = parts[sup].get("specializes", "")
    exch_by_part = {}
    for x in interfaces.get("exchanges", []):
        exch_by_part.setdefault(engine.element_part(x["target_element"], structure), []).append(x)
    values = result.get("values", {})
    out = [f"package {mission}Design {{", "    private import ScalarValues::*;", "",
           "    // ---- metamodel: one package per subsystem; each part definition declares the calcs that assess it ----",
           "    package Metamodel {"]
    for cap in caps:
        out.append(f"        private import {pkg[cap]}::*;")
    out.append(f"        part def {whole} {{")
    for cap in caps:
        for name in roots_by_cap.get(cap, []):
            out.append(f"            part {_ident(name)} : {name}[{parts[name].get('multiplicity', '1')}];")
    out.append("        }")

    def emit_def(name: str, pad: str) -> None:
        p = parts[name]
        out.append(f"{pad}part def {name}" + (f" :> {p['specializes']}" if p.get("specializes") else "") + " {")
        if p.get("description") or (not p["parent"] and p["capability"] in question and not p.get("specializes")):
            out.append(f"{pad}    doc /* {p.get('description') or question[p['capability']]} */")
        if inherited.get(name):
            out.append(f"{pad}    // inherits {', '.join(_ident(n) for n in inherited[name])} from {p['specializes']}")
        for c, term in attrs_of.get(name, []):
            sample = design.get("values", {}).get(term["name"], term.get("example"))
            out.append(f"{pad}    attribute {_ident(term['name'])} : {_type(sample if sample is not None else 0.0)};   // {term['unit']}")
        for c in calcs_on.get(name, []):
            for term in c["inputs"]:
                if term.get("aggregate"):
                    out.append(f"{pad}    attribute {_ident(term['name'])} : Real;   // {term['unit']}, {term['aggregate']} of {_ident(term.get('of') or term['name'])} over {term.get('part')}")
            for term in c.get("outputs", []):
                out.append(f"{pad}    attribute {_ident(term['name'])} : {'Boolean' if term['name'] == 'passes' else 'Real'};   // {term['unit']}, computed by {c['function_id'].split('.', 1)[1]}")
            out.append(f"{pad}    calc {_ident(c['function_id'].split('.')[-1])} : {_calc_name(c['function_id'])};")
        for child in children.get(name, []):
            out.append(f"{pad}    part {_ident(child)} : {child}[{parts[child].get('multiplicity', '1')}];")
        out.append(f"{pad}}}")

    for cap in caps:
        out += ["", f"        package {pkg[cap]} {{", f"            doc /* {question[cap]} */"]
        for p in structure:
            if p["capability"] == cap:
                emit_def(p["name"], "            ")
        for c in [c for c in contracts if c["capability"] == cap]:
            version = registry.get(c["function_id"], {}).get("active", "")
            out.append(f"            calc def {_calc_name(c['function_id'])} {{")
            out.append(f"                doc /* {c['function_id']} {version} on {part_of[c['function_id']]}, once per usage. Rule {c['rule_id']}. "
                       f"The body is functions/{c['function_id']}/{version}/solution.py. */")
            method = (c.get("method") or "").strip().replace("*/", "* /")
            if method:
                out.append(f"                comment /* Method: {method} */")
            for term in c["inputs"]:
                out.append(f"                in {_ident(term['name'])} : Real;" + (f"   // {term['aggregate']} over {term.get('part')}" if term.get("aggregate") else ""))
            for term in c.get("outputs", []):
                out.append(f"                return {_ident(term['name'])} : {'Boolean' if term['name'] == 'passes' else 'Real'};")
            out.append("            }")
        for x in interfaces.get("exchanges", []):          # an exchange is arithmetic, so it is a calc def with its expression
            if parts.get(engine.element_part(x["target_element"], structure), {}).get("capability") != cap:
                continue
            names = sorted({n for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", x["expression"])} - {x["target_input"]})
            out.append(f"            calc def {_ident(x['id']).capitalize()} {{")
            out.append(f"                doc /* {x['id']}, {x['source_element']} to {x['target_element']}: {x.get('rationale', '')} */")
            for n in names:
                out.append(f"                in {_ident(n)} : Real;")
            expr = x["expression"]
            for n in names:
                expr = re.sub(rf"\b{re.escape(n)}\b", _ident(n), expr)
            out.append(f"                return {_ident(x['target_input'])} : Real = {expr};   // {x.get('unit', '')}")
            out.append("            }")
        out.append("        }")
    out += ["    }", "", "    // ---- requirements, with the version each one is at ----", "    package Requirements {"]
    for r in spec["requirements"]:
        out.append(f"        requirement <'{r['id']}'> {_ident(r['id'])} {{")
        out.append(f"            doc /* {r['text']} Source: {r['source']}. Version {r.get('version', 1)}. */")
        out.append("        }")
    out += ["    }", "", "    private import Metamodel::*;"] + [f"    private import Metamodel::{pkg[cap]}::*;" for cap in caps] + ["",
            f"    // ---- the design instance '{design['name']}': its usages, with literals for what they carry ----",
            f"    part {_ident(design['name'])} : {whole} {{", f"        doc /* {design.get('description', '')} */"]

    index = engine.usage_index(design, structure)
    runs = result.get("functions", {})

    def emit_usage(path: str, depth: int) -> None:
        pad = "        " + "    " * depth
        e = index[path]
        name = path.rsplit(".", 1)[-1]
        out.append(f"{pad}part {name} : {e['part']} {{")
        for c in calcs_on.get(e["part"], []) + [c for sup in _supertypes(e["part"], parts) for c in calcs_on.get(sup, [])]:
            run = (runs.get(c["function_id"], {}).get("usages", {}) or {}).get(path, {})
            if run:
                out.append(f"{pad}    doc /* {c['function_id']} {runs[c['function_id']].get('version', '')}: "
                           f"{'passes' if run.get('result', {}).get('passes') else run.get('error') or 'does not pass'}. */")
        for key in [k for k in values if k.rsplit(".", 1)[0] == path and "." in k]:
            attr = key.rsplit(".", 1)[1]
            tag = design.get("tags", {}).get(key)
            computed = any(t["name"] == attr for c in contracts for t in c.get("outputs", []))
            here = [e["part"]] + _supertypes(e["part"], parts)
            agg = next((t for c in contracts for t in c["inputs"] if t["name"] == attr and t.get("aggregate") and part_of[c["function_id"]] in here), None)
            note = "computed" if computed else f"{agg['aggregate']} of {_ident(agg.get('of') or attr)} over {agg.get('part')}" if agg else (tag or "exchange")
            out.append(f"{pad}    :>> {_ident(attr)} = {_literal(values[key])};   // {note}")
        for x in exch_by_part.get(e["part"], []):
            got = next((g for g in result.get("exchanges", []) if g["id"] == x["id"] and g.get("usage") == path), {})
            if got.get("value") is not None:
                out.append(f"{pad}    // {x['id']}: {_ident(x['target_input'])} = {got['value']} by calc {_ident(x['id']).capitalize()}")
        for child in e["children"]:
            emit_usage(child, depth + 1)
        out.append(f"{pad}}}")

    for path, e in index.items():
        if not e["parent"]:
            emit_usage(path, 0)
    globals_ = {k: v for k, v in values.items() if "." not in k}
    for key, value in globals_.items():
        computed = any(t["name"] == key for c in contracts for t in c.get("outputs", []))
        out.append(f"        :>> {_ident(key)} = {_literal(value)};   // {'computed' if computed else design.get('tags', {}).get(key, 'exchange')}, whole design")
    first_usage = {}
    for path, e in index.items():
        for sup in [e["part"]] + _supertypes(e["part"], parts):
            first_usage.setdefault(sup, path)
    for x in interfaces.get("exchanges", []):
        src, tgt = engine.element_part(x["source_element"], structure), engine.element_part(x["target_element"], structure)
        out.append(f"        // {x['id']}: {x['source_element']} to {x['target_element']}. {x['target_input']} = {x['expression']}")
        if src in first_usage and tgt in first_usage:
            out.append(f"        connection {_ident(x['id'])} connect {first_usage[src]} to {first_usage[tgt]};")
    out.append("    }")
    out.append("")
    for r in spec["requirements"]:
        own = [c for c in contracts if c["capability"] == r["capability"]]
        ok = bool(own) and all(c["function_id"] in runs and "error" not in runs[c["function_id"]]
                               and all(u.get("result", {}).get("passes", True) for u in runs[c["function_id"]].get("usages", {}).values()) for c in own)
        target = next((first_usage.get(part_of[c["function_id"]], "") for c in own), "")
        if ok and target:
            out.append(f"    satisfy Requirements::{_ident(r['id'])} by {_ident(design['name'])}.{target};")
        else:
            out.append(f"    // NOT satisfied: {r['id']} by {design['name']}.{target or r['capability']}")
    out.append("}")
    return "\n".join(out) + "\n"


def _supertypes(part: str, parts: dict) -> list[str]:
    out, seen = [], set()
    while part in parts and parts[part].get("specializes") and parts[part]["specializes"] not in seen:
        part = parts[part]["specializes"]; seen.add(part); out.append(part)
    return out
