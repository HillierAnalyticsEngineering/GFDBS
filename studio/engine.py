"""The evaluation engine of a generated build system. Standard library only, no model call, no studio
import: the exported build system carries this file unchanged.

The model has two halves.

  The metamodel (interfaces.json): a structure of part definitions as a tree, function contracts, and
  exchanges. A contract is a calc that belongs to one part definition and runs once per usage of it.
  A subsystem's calc rolls its children up through aggregate inputs (sum, max, min, mean, count, all,
  any) over the usages of a part inside it. An exchange supplies one attribute of a part from values
  elsewhere, by an arithmetic expression, once per usage of that part.

  A design instance (inputs/<name>.json): a tree of part usages, each naming its part definition, and
  one flat dictionary of values keyed by usage path: "eps.panel1.panel_area_m2". A value with no path
  is global to the design. Keeping values flat keeps every editor, sweep, and diff simple; the tree
  says what the paths mean.

Resolving a name for a usage is one rule, used everywhere: the usage's own value, then its ancestors',
then the design's global value, then the one usage anywhere that carries it (a value or a computed
output). Two carriers is an error that names them, because that is what an aggregate is for.
"""
from __future__ import annotations

import ast
import operator

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg}
AGGREGATES = ("sum", "max", "min", "mean", "count", "all", "any")


# ---- expressions -------------------------------------------------------------------------
def names_in(expression: str) -> list[str]:
    return sorted({n.id for n in ast.walk(ast.parse(expression, mode="eval")) if isinstance(n, ast.Name)})


def compute(expression: str, values: dict) -> float:
    """Evaluate an exchange expression. Arithmetic on named values only. Nothing else is allowed."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(f"exchange needs '{node.id}', which the design does not provide")
            return values[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.operand))
        raise ValueError(f"exchange expression may use arithmetic only: {expression}")
    return walk(ast.parse(expression, mode="eval"))


# ---- the structure ---------------------------------------------------------------------------
def by_name(structure: list[dict]) -> dict:
    return {p["name"]: p for p in structure}


def is_a(part: str, ancestor_type: str, parts: dict) -> bool:
    """True when `part` is `ancestor_type` or specializes it, directly or through other subtypes."""
    seen = set()
    while part and part in parts and part not in seen:
        if part == ancestor_type:
            return True
        seen.add(part)
        part = parts[part].get("specializes", "")
    return part == ancestor_type


def inside(part: str, container: str, parts: dict) -> bool:
    """True when `part` is composed into `container`, at any depth, following parents (and a subtype's supertype's parent)."""
    seen = set()
    while part and part in parts and part not in seen:
        seen.add(part)
        parent = parts[part].get("parent") or (parts[parts[part]["specializes"]].get("parent") if parts[part].get("specializes") in parts else "")
        if parent == container:
            return True
        part = parent
    return False


def root_of(capability: str, structure: list[dict]) -> str:
    """The subsystem root a capability owns: its first part with no parent. Older specs name capabilities
    where parts are meant, and this maps them."""
    for p in structure:
        if p["capability"] == capability and not p["parent"]:
            return p["name"]
    return ""


def contract_part(contract: dict, structure: list[dict]) -> str:
    """The part definition a calc belongs to. A contract without one belongs to its capability's root."""
    return contract.get("part") or root_of(contract["capability"], structure) or (structure[0]["name"] if structure else contract["capability"])


def element_part(name: str, structure: list[dict]) -> str:
    """An exchange end: a part definition by name, or a capability that stands for its root."""
    return name if name in by_name(structure) else (root_of(name, structure) or name)


def identity_source(x: dict, contracts: list) -> str | None:
    """A pass-through exchange: the expression is just the target's own name, and that name is an output of
    the source element. It is allowed and runs after the source function. Returns that function's id."""
    try:
        reads = names_in(x["expression"])
    except SyntaxError:
        return None
    if reads != [x["target_input"]]:
        return None
    for c in contracts:
        if c["capability"] == x["source_element"] or c.get("part") == x["source_element"]:
            if any(t["name"] == x["target_input"] for t in c.get("outputs", [])):
                return c["function_id"]
    return None


# ---- the usage tree --------------------------------------------------------------------------
def _ident(name: str) -> str:
    return name[:1].lower() + name[1:] if name else name


def default_usages(structure: list[dict]) -> list[dict]:
    """One usage per part definition that is not optional and not a subtype, nested as the structure is.
    This is the instance a flat design implies, and the one the inputs agent starts from."""
    parts = by_name(structure)
    def kids(name):
        out = []
        for p in structure:
            if p["parent"] == name and not p.get("specializes") and not str(p.get("multiplicity", "1")).startswith("0"):
                out.append({"name": _ident(p["name"]), "part": p["name"], "parts": kids(p["name"])})
        return out
    return [{"name": _ident(p["name"]), "part": p["name"], "parts": kids(p["name"])} for p in structure if not p["parent"] and not p.get("specializes")]


def default_children(part: str, structure: list[dict]) -> list[dict]:
    """The default usages inside one part: one per required, non-subtype child, recursively."""
    return [u for root in default_usages([p for p in structure if p["name"] == part] + [p for p in structure if p["name"] != part])
            if root["part"] == part for u in root["parts"]] if any(p["name"] == part and not p["parent"] for p in structure) else _children_of(part, structure)


def _children_of(part: str, structure: list[dict]) -> list[dict]:
    out = []
    for p in structure:
        if p["parent"] == part and not p.get("specializes") and not str(p.get("multiplicity", "1")).startswith("0"):
            out.append({"name": _ident(p["name"]), "part": p["name"], "parts": _children_of(p["name"], structure)})
    return out


def tree_from_rows(rows: list[dict]) -> list[dict]:
    """Fold flat (path, part) rows into the nested usage tree. A row whose parent is missing is placed at the top."""
    nodes, roots = {}, []
    for row in sorted(rows, key=lambda r: r["path"].count(".")):
        path = row["path"].strip(".")
        if not path or path in nodes:
            continue
        node = nodes[path] = {"name": path.rsplit(".", 1)[-1], "part": row["part"], "parts": []}
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        (nodes[parent]["parts"] if parent in nodes else roots).append(node)
    return roots


def rows_from_tree(usages: list[dict]) -> list[dict]:
    return [{"path": path, "part": u["part"]} for path, u, _ in walk_usages(usages)]


def walk_usages(usages: list[dict], prefix: str = "", parent: str | None = None):
    """Yield (path, usage, parent_path) depth first."""
    for u in usages or []:
        path = f"{prefix}.{u['name']}" if prefix else u["name"]
        yield path, u, parent
        yield from walk_usages(u.get("parts", []), path, path)


def usage_index(design: dict, structure: list[dict]) -> dict:
    """path -> {part, parent, children: [paths], usage}. A design with no usages gets the default tree."""
    usages = design.get("usages") or default_usages(structure)
    index = {}
    for path, u, parent in walk_usages(usages):
        index[path] = {"part": u["part"], "parent": parent, "children": [], "usage": u}
    for path, entry in index.items():
        if entry["parent"]:
            index[entry["parent"]]["children"].append(path)
    return index


def usages_of(part: str, index: dict, parts: dict) -> list[str]:
    """Paths of every usage whose part is `part` or a subtype of it, in tree order."""
    return [path for path, e in index.items() if is_a(e["part"], part, parts)]


def descendants(path: str, index: dict) -> list[str]:
    out = []
    for child in index[path]["children"]:
        out.append(child)
        out += descendants(child, index)
    return out


def set_value(design: dict, structure: list[dict], name: str, value, part: str = "", tag: str | None = None) -> list[str]:
    """Put a value where it belongs. A path name goes to that usage. A bare name goes to every usage of
    the part that carries the attribute when one is known, else to the design's globals. Returns the keys set."""
    design.setdefault("values", {}); design.setdefault("tags", {})
    keys = []
    if "." in name:
        keys = [name]
    elif part:
        index = usage_index(design, structure)
        keys = [f"{p}.{name}" for p in usages_of(part, index, by_name(structure))] or [name]
    else:
        keys = [k for k in design["values"] if k == name or k.endswith("." + name)] or [name]
    for k in keys:
        design["values"][k] = value
        if tag:
            design["tags"][k] = tag
    return keys


def place_values(design: dict, interfaces: dict) -> list[str]:
    """Move global values onto the usages of the part that carries the attribute, when every contract
    agrees on that part and the design has usages of it. A name used on several parts, or on a part
    with no usage, stays global and resolves for everyone. Returns the keys moved."""
    structure = interfaces.get("structure") or []
    if not structure:
        return []
    carriers: dict[str, set] = {}
    for c in interfaces.get("contracts", []):
        for t in c.get("inputs", []):
            name = (t.get("of") or t["name"]) if t.get("aggregate") else t["name"]
            carriers.setdefault(name, set()).add(t.get("part") or contract_part(c, structure))
    for x in interfaces.get("exchanges", []):
        for t in x.get("parameters", []):
            carriers.setdefault(t["name"], set()).add("")             # an exchange parameter is global by nature
    index, parts, moved = usage_index(design, structure), by_name(structure), []
    for key in [k for k in list(design.get("values", {})) if "." not in k]:
        owners = carriers.get(key, set())
        if len(owners) != 1 or "" in owners:
            continue
        paths = usages_of(next(iter(owners)), index, parts)
        if not paths:
            continue
        value, tag = design["values"].pop(key), design.get("tags", {}).pop(key, None)
        for path in paths:
            if f"{path}.{key}" in design["values"]:          # a value stated for that usage stands
                continue
            design["values"][f"{path}.{key}"] = value
            if tag:
                design.setdefault("tags", {})[f"{path}.{key}"] = tag
            moved.append(f"{path}.{key}")
    return moved


# ---- resolution -------------------------------------------------------------------------------
class Scope:
    """Name resolution over one design instance while it is being evaluated."""

    def __init__(self, design: dict, structure: list[dict]):
        self.parts = by_name(structure)
        self.index = usage_index(design, structure)
        self.values = dict(design.get("values", {}))     # path.name or name -> value; computed outputs join as they are made
        self.by_attr: dict[str, list[str]] = {}            # attribute name -> paths that carry it
        for key in self.values:
            if "." in key:
                path, attr = key.rsplit(".", 1)
                self.by_attr.setdefault(attr, []).append(path)

    def put(self, path: str, name: str, value) -> None:
        self.values[f"{path}.{name}" if path else name] = value
        if path and path not in self.by_attr.setdefault(name, []):
            self.by_attr[name].append(path)

    def chain(self, path: str) -> list[str]:
        out, cur = [], path
        while cur:
            out.append(cur)
            cur = self.index[cur]["parent"] if cur in self.index else None
        return out

    def resolve(self, path: str, name: str):
        """The rule: own value, ancestors', global, then the one usage anywhere that carries it."""
        for p in self.chain(path):
            if f"{p}.{name}" in self.values:
                return self.values[f"{p}.{name}"]
        if name in self.values:
            return self.values[name]
        carriers = self.by_attr.get(name, [])
        if len(carriers) == 1:
            return self.values[f"{carriers[0]}.{name}"]
        if len(carriers) > 1:
            raise ValueError(f"'{name}' is carried by {len(carriers)} usages ({', '.join(carriers[:4])}{', ...' if len(carriers) > 4 else ''}) and "
                             f"none is in scope of {path or 'the design'}. Declare an aggregate input to combine them.")
        raise KeyError(name)

    def aggregate(self, path: str, term: dict):
        """An aggregate input: `of` taken over the usages of `part` inside this usage (or anywhere, for a global calc)."""
        how, of, part = term["aggregate"], term.get("of") or term["name"], term.get("part") or ""
        within = descendants(path, self.index) if path else list(self.index)
        members = [p for p in within if not part or is_a(self.index[p]["part"], part, self.parts)]
        if how == "count":
            return len(members)
        # a usage carries the attribute itself, or inherits the design's global value for it: a global applies to every usage
        items = [self.values[f"{p}.{of}"] if f"{p}.{of}" in self.values else self.values[of] for p in members if f"{p}.{of}" in self.values or of in self.values]
        if not items:
            raise KeyError(f"{term['name']} ({how} of {of} over {part or 'all parts'} inside {path or 'the design'}: "
                           f"{'no usage of ' + part if part and not members else 'nothing carries ' + of})")
        if how == "sum":
            return sum(items)
        if how == "max":
            return max(items)
        if how == "min":
            return min(items)
        if how == "mean":
            return sum(items) / len(items)
        if how == "all":
            return all(bool(v) for v in items)
        if how == "any":
            return any(bool(v) for v in items)
        raise ValueError(f"unknown aggregate '{how}' on {term['name']}")

    def inputs_for(self, path: str, contract: dict) -> dict:
        """The scalar inputs one call takes, resolved for one usage. Missing optional inputs are left out."""
        out = {}
        for term in contract["inputs"]:
            try:
                out[term["name"]] = self.aggregate(path, term) if term.get("aggregate") else self.resolve(path, term["name"])
                if term.get("aggregate"):
                    self.put(path, term["name"], out[term["name"]])     # the rolled-up value is a value of this usage from here on
            except KeyError:
                if not term.get("optional"):
                    raise ValueError(f"{contract['function_id']} on {path or 'the design'} needs '{term['name']}' and nothing in scope provides it")
        return out


# ---- scheduling ---------------------------------------------------------------------------------
def _reads(contract: dict) -> list[str]:
    return [t.get("of") or t["name"] if t.get("aggregate") else t["name"] for t in contract.get("inputs", [])]


def schedule(interfaces: dict, given: set) -> list[tuple[str, dict]]:
    """Order exchanges and calcs so every value exists before something reads it. Names are the unit, since
    one name means one thing across the system. Raises ValueError when the dependencies form a loop."""
    exchanges, contracts = interfaces.get("exchanges", []), interfaces.get("contracts", [])
    targets = {x["target_input"] for x in exchanges}
    made_by = {t["name"]: c["function_id"] for c in contracts for t in c.get("outputs", [])
               if t["name"] not in given and t["name"] not in targets}
    for c in contracts:                                  # an aggregate input becomes a value of the calc's usage, so it is made there too
        for t in c.get("inputs", []):
            if t.get("aggregate") and t["name"] not in targets:
                made_by.setdefault(t["name"], c["function_id"])
    identity = {x["id"]: identity_source(x, contracts) for x in exchanges}
    steps, called = [], set()
    pending = [("exchange", x) for x in exchanges] + [("function", c) for c in contracts]
    while pending:
        waiting = {x["target_input"] for kind, x in pending if kind == "exchange"}
        ready = []
        for kind, item in pending:
            if kind == "exchange" and identity.get(item["id"]):
                ok = identity[item["id"]] in called
            elif kind == "exchange":
                ok = not any(n in waiting or (n in made_by and made_by[n] not in called) for n in names_in(item["expression"]))
            else:
                ok = not any(n in waiting or (n in made_by and made_by[n] not in called and made_by[n] != item["function_id"]) for n in _reads(item))
            if ok:
                ready.append((kind, item))
        if not ready:
            raise ValueError(describe_cycle(interfaces, given))
        steps += ready
        called |= {c["function_id"] for kind, c in ready if kind == "function"}
        pending = [p for p in pending if p not in ready]
    return steps


def describe_cycle(interfaces: dict, given: set) -> str:
    """Find one dependency cycle and say it in words, edge by edge, so the author can break it."""
    exchanges, contracts = interfaces.get("exchanges", []), interfaces.get("contracts", [])
    targets = {x["target_input"]: x for x in exchanges}
    made_by = {t["name"]: c for c in contracts for t in c.get("outputs", []) if t["name"] not in given and t["name"] not in targets}
    for c in contracts:
        for t in c.get("inputs", []):
            if t.get("aggregate") and t["name"] not in targets:
                made_by.setdefault(t["name"], c)
    edges = {}
    for x in exchanges:
        source = identity_source(x, contracts)
        if source:
            edges.setdefault(x["id"], []).append((source, f"{x['id']} passes {x['target_input']} through from {source}"))
            continue
        for n in names_in(x["expression"]):
            if n in targets:
                edges.setdefault(x["id"], []).append((targets[n]["id"], f"{x['id']} reads {n}, which {targets[n]['id']} supplies"))
            elif n in made_by:
                edges.setdefault(x["id"], []).append((made_by[n]["function_id"], f"{x['id']} reads {n}, an output of {made_by[n]['function_id']}"))
    for c in contracts:
        for n in _reads(c):
            if n in targets:
                edges.setdefault(c["function_id"], []).append((targets[n]["id"], f"{c['function_id']} takes {n} from {targets[n]['id']}"))
            elif n in made_by and made_by[n]["function_id"] != c["function_id"]:
                edges.setdefault(c["function_id"], []).append((made_by[n]["function_id"], f"{c['function_id']} takes {n}, an output of {made_by[n]['function_id']}"))

    def walk(node, path, seen):
        nodes = [n for n, _ in path]
        for nxt, why in edges.get(node, []):
            if nxt in nodes:
                return [r for _, r in path[nodes.index(nxt):]] + [why]
            if nxt not in seen:
                seen.add(nxt)
                found = walk(nxt, path + [(nxt, why)], seen)
                if found:
                    return found
        return None
    for start in list(edges):
        cycle = walk(start, [(start, "")], {start})
        if cycle:
            chain = ". ".join(r for r in cycle if r)
            return (f"The exchanges and functions form a loop, so nothing can run first: {chain}. "
                    f"Break it: one of these inputs must come from the specification (add it as a parameter with an example) "
                    f"instead of from another element's output, or the two calculations belong in one function.")
    return "exchanges and functions depend on each other in a loop"


# ---- evaluation ---------------------------------------------------------------------------------
def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def summarize(per_usage: dict) -> dict:
    """One result for a calc that ran on several usages: passes when all pass, each number at its worst
    (the minimum, since the numbers that matter are margins and headrooms), and how many usages there were."""
    results = [r["result"] for r in per_usage.values() if "result" in r]
    errored = [p for p, r in per_usage.items() if "result" not in r]
    if not results:
        return {}
    if len(results) == 1 and not errored:
        return results[0]
    out = {"passes": all(r.get("passes", True) for r in results) and not errored, "usages": len(per_usage)}
    if errored:
        out["errors"] = len(errored)                     # a usage the function refused counts against the calc
    for key in results[0]:
        if key == "passes":
            continue
        nums = [r[key] for r in results if _number(r.get(key))]
        if len(nums) == len(results):
            out[key] = min(nums)
    ok = lambda p: "result" in per_usage[p] and per_usage[p]["result"].get("passes", True)
    out["worst"] = min(per_usage, key=lambda p: (ok(p), p))
    return out


def evaluate(interfaces: dict, design: dict, call, versions: dict | None = None) -> dict:
    """Evaluate one design instance through the whole build system.

    `call(function_id, inputs, version)` runs one function and returns
    {"version", "inputs", "result", "findings"}; it raises on a missing function or a refused input.
    Returns values (with every computed output keyed by usage path), exchanges, functions, findings,
    errors, and a trace of every step for the interface to draw.
    """
    structure = interfaces.get("structure") or []
    contracts = interfaces.get("contracts", [])
    scope = Scope(design, structure)
    try:
        steps = schedule(interfaces, set(k.rsplit(".", 1)[-1] for k in scope.values))
    except ValueError:                                   # a loop: run what can run and report the rest
        steps = [("exchange", x) for x in interfaces.get("exchanges", [])] + [("function", c) for c in contracts]
    trace, exchanged, functions = [], [], {}
    for kind, item in steps:
        if kind == "exchange":
            x = item
            target = element_part(x["target_element"], structure)
            paths = usages_of(target, scope.index, scope.parts) if structure else [""]
            if not paths:
                trace.append({"kind": "exchange", "id": x["id"], "note": f"no usage of {target} in this design"})
            for path in paths:
                try:
                    names = names_in(x["expression"])
                    value = compute(x["expression"], {n: scope.resolve(path, n) for n in names})
                    if _number(value):
                        value = round(value, 4)
                    elif not (len(names) == 1 and x["expression"].strip() == names[0]):     # a pass-through may carry a label; arithmetic may not
                        raise ValueError(f"the expression gave {type(value).__name__} {value!r}, not a number")
                    scope.put(path, x["target_input"], value)
                    exchanged.append({"id": x["id"], "usage": path, "target_input": x["target_input"], "value": value,
                                      "expression": x["expression"], "from": x["source_element"], "to": x["target_element"]})
                except (ValueError, ZeroDivisionError, KeyError, TypeError) as exc:
                    exchanged.append({"id": x["id"], "usage": path, "target_input": x["target_input"], "error": str(exc).strip("'") or "division by zero",
                                      "from": x["source_element"], "to": x["target_element"]})
                trace.append({"kind": "exchange", **exchanged[-1]})
            continue
        contract, fid = item, item["function_id"]
        part = contract_part(contract, structure)
        paths = usages_of(part, scope.index, scope.parts) if structure else [""]
        entry = {"function_id": fid, "part": part, "usages": {}}
        for path in paths:
            try:
                inputs = scope.inputs_for(path, contract)
                run = call(fid, inputs, (versions or {}).get(fid))
                entry["version"] = run["version"]
                for f in run.get("findings", []):
                    f["usage"] = path
                entry["usages"][path] = {"inputs": run["inputs"], "result": run["result"], "findings": run.get("findings", [])}
                for term in contract.get("outputs", []):     # outputs become values that later calcs and exchanges may read
                    out = run["result"].get(term["name"])
                    if out is not None and (_number(out) or isinstance(out, bool)):
                        scope.put(path, term["name"], out)
            except Exception as exc:                         # not built, outside the evidence, or a missing input
                entry["usages"][path] = {"error": str(exc)}
                entry.setdefault("error", str(exc))
        if not paths:
            entry["note"] = f"no usage of {part} in this design"
        entry["result"] = summarize(entry["usages"])
        entry["findings"] = [f for u in entry["usages"].values() for f in u.get("findings", [])]
        if "version" not in entry and paths:
            entry["version"] = (versions or {}).get(fid, "")
        functions[fid] = entry
        trace.append({"kind": "call", "capability": contract["capability"], **{k: v for k, v in entry.items() if k != "usages"},
                      "inputs": next((u.get("inputs") for u in entry["usages"].values() if "inputs" in u), {})})
    findings = [f for r in functions.values() for f in r.get("findings", [])]
    errors = {fid: r["error"] for fid, r in functions.items() if "error" in r}
    return {"values": scope.values, "exchanges": exchanged, "functions": functions, "findings": findings, "errors": errors,
            "passes": not findings and not errors, "trace": trace, "usages": {p: e["part"] for p, e in scope.index.items()}}


# ---- checks on an instance against its metamodel ---------------------------------------------------
def _bounds(multiplicity: str) -> tuple[int, int | None]:
    m = str(multiplicity or "1").strip()
    if ".." in m:
        lo, hi = m.split("..", 1)
        return int(lo or 0), (None if hi.strip() == "*" else int(hi))
    if m == "*":
        return 0, None
    try:
        return int(m), int(m)
    except ValueError:
        return 0, None


def instance_errors(interfaces: dict, design: dict) -> list[str]:
    """Why a design instance does not fit its metamodel: unknown parts, counts outside a multiplicity,
    and inputs no scope provides. Empty when it fits."""
    structure = interfaces.get("structure") or []
    parts = by_name(structure)
    if not structure:
        return []
    errors = []
    index = usage_index(design, structure)
    for path, e in index.items():
        if e["part"] not in parts:
            errors.append(f"usage '{path}' is a '{e['part']}', which is not a part of this system")
            continue
        parent_part = index[e["parent"]]["part"] if e["parent"] else ""
        declared = parts[e["part"]].get("parent") or (parts[parts[e["part"]]["specializes"]].get("parent") if parts[e["part"]].get("specializes") in parts else "")
        if (declared or "") != (parent_part or "") and not (declared and parent_part and is_a(parent_part, declared, parts)):
            errors.append(f"usage '{path}' puts a {e['part']} inside {parent_part or 'the whole'}; the structure composes it into {declared or 'the whole'}")
    for path, e in index.items():
        for child_def in [p for p in structure if p["parent"] == e["part"] and not p.get("specializes")]:
            lo, hi = _bounds(child_def.get("multiplicity", "1"))
            n = sum(1 for c in e["children"] if is_a(index[c]["part"], child_def["name"], parts))
            if n < lo or (hi is not None and n > hi):
                errors.append(f"usage '{path}' holds {n} {child_def['name']} usage(s); the structure says {child_def.get('multiplicity', '1')}")
    for root in [p for p in structure if not p["parent"] and not p.get("specializes")]:
        lo, hi = _bounds(root.get("multiplicity", "1"))
        n = sum(1 for p, e in index.items() if not e["parent"] and is_a(e["part"], root["name"], parts))
        if n < lo or (hi is not None and n > hi):
            errors.append(f"the design holds {n} {root['name']} usage(s) at the top; the structure says {root.get('multiplicity', '1')}")
    scope = Scope(design, structure)
    targets = {x["target_input"] for x in interfaces.get("exchanges", [])}
    outputs = {t["name"] for c in interfaces.get("contracts", []) for t in c.get("outputs", [])}
    for c in interfaces.get("contracts", []):
        for path in usages_of(contract_part(c, structure), index, parts):
            for term in c["inputs"]:
                if term.get("aggregate"):
                    of = term.get("of") or term["name"]
                    if of in outputs or of in targets or term["aggregate"] == "count":   # outputs and exchanged values exist once their step has run; a count needs no value
                        continue
                    try:
                        scope.aggregate(path, term)
                    except KeyError as exc:
                        errors.append(f"{c['function_id']} on '{path}' cannot roll up {exc}")
                    continue
                if term.get("optional") or term["name"] in targets:
                    continue
                try:
                    scope.resolve(path, term["name"])
                except KeyError:
                    if term["name"] not in outputs:
                        errors.append(f"{c['function_id']} on '{path}' needs '{term['name']}' and no value in scope provides it")
                except ValueError as exc:
                    errors.append(str(exc))
    return errors
