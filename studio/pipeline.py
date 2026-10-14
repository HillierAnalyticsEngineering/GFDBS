"""The studio as harness tasks. A request goes in. A versioned build system and example designs come out.

Nothing here knows what is being built. That comes from the project's harness definition:
what a design is called, which agents exist, their instruction files, tiers, tools, and rules.

  1  request        a person describes what they want
  2  parameterize   an agent turns the request into parameters and requirements, and tags what it filled in
  3  interfaces     the systems engineer agent proposes the architecture: a structure of parts, a calc on
                    every part that has something to assess, and the exchanges between parts
  4  build.*        one specialist agent per calc writes its function, in parallel. A specialist owns a
                    capability and writes as many calcs as its subsystem has things to assess
  5  validate       code: gates, integration through the router, emergent effects
  6  review         the systems engineer agent, with long-term memory, accepts or asks for redesign
  7  build.*.vN     redesigned functions become new versions. Review rounds are capped
  8  verify         code: every active function still honors the interface spec
  9  inputs         an agent writes the baseline design instance and alternatives
 10  evaluate, render   code: results, a report, and a SysML v2 model per design

A follow-up request runs triage first, then only what the change touches.
A drill-down request decomposes one part further: a specialist proposes the finer parts, the systems
engineer reviews with memory, and the owning function is rebuilt and re-verified if it gained inputs.
"""
from __future__ import annotations

import json
import re
import time

import yaml

from agents.config import MODELS, Settings
from agents.memory import memory_tools, open_memory
from agents.runner import run_agent
from agents.schemas import BuilderSubmission, ChangePlan, Decomposition, DecompositionReview, DesignInputs, ExampleValues, InterfaceSpec, ParameterizedSpec, SystemReview
from harness import Context, Task, TaskResult
from harness.shared import SharedDictionary, _norm as _unit

from . import engine, gates, render, runtime, tooling
from .project import Project

j = lambda value: json.dumps(value, indent=1, default=str)
PARAMETERIZE_SYSTEM = """You turn a person's request for a {building} into a parameterized specification.
The request may be a list of instructions, a description, or a mix.
- Every number or choice that another mission could change becomes a named parameter with a unit.
- Follow the person's instructions on what to parameterize and what to keep fixed. Their word wins.
- Where the request leaves a gap that an assessment will need, fill it with a reasonable default and
  tag it source 'ai' with a rationale. Tag everything the person stated as source 'user'.
- Write requirements as 'shall' statements. Tag each one user or ai the same way.
- List the capabilities the design needs to be assessed, as short lowercase names.
  Specialist agents exist for: {specialists}. Prefer those names where they fit. Others get a generic specialist.
- The prompt lists the names the project's library already uses. When a value in the request means the
  same thing as a library name, use the library name exactly. Invent a name only for something the library lacks.
Never invent a value and tag it 'user'."""

INTERFACE_SYSTEM = """You are the lead systems engineer. From a parameterized specification, propose the architecture
as a structural decomposition and interface specifications.
- First the structure: the parts of the {building} as a tree, the way a domain engineer would name them.
  Subsystems directly under the whole, then the concrete things inside each: not 'StorageElement' but
  Volume with PersistentVolume and EphemeralVolume as subtypes, StorageClass, BackupSet; not 'SecurityElement'
  but Certificate, KeyPair, NetworkPolicy, ServiceMesh. Give each part a parent, a multiplicity, and the
  capability whose function assesses it. Use specializes for a subtype. At most {depth} levels below the
  whole: a subsystem is level 1. Every input of every contract names the part it describes.
- Then the calcs. A calc belongs to one part definition and runs once per usage of it: a calc on NodePool
  runs for every node pool in a design. Give every part that has something to assess its own calc, under
  the capability whose specialist knows that assessment. A subsystem's calc rolls its parts up through
  aggregate inputs: `aggregate: sum`, `of: vcpu_per_node`, `part: Node` means the sum of vcpu_per_node over
  the Node usages inside that subsystem, delivered to the function as one number. Several calcs per
  capability is normal. One calc per capability is a sign the decomposition is not being assessed.
  Each calc gets a static identifier of the form {prefix}.<capability>.<noun>, unique across the system.
  The identifier never changes when the function is rewritten.
- Write each calc's contract: the question it answers for any {building}, its inputs and outputs with units,
  each input on the part that carries it (the calc's own part, a part inside it, or an enclosing part for
  context such as mission life). Use the names in the specification wherever they exist. A name means
  one thing across the whole system. Give an example value for any input the specification does not provide.
- Write the exchanges between parts as arithmetic expressions. An exchange supplies one attribute of one
  part (target_element) from values that belong to others (source_element), and runs once per usage of
  the target part. These are where cross-domain effects come from.
  Every name in an expression must get a value from one of four places: a parameter in the specification,
  an output of another element's function, the target of another exchange, or the exchange's own
  parameters list with a numeric example. A derived quantity such as an orbit period is not in the
  specification, so it needs one of the other three. Do not leave an example empty.
- Where the library describes how an assessment is done, follow its vocabulary. The prompt lists the
  library's names for each capability. People wrote the worked cases in those names, and the acceptance tests
  come from the worked cases. So one calc of that capability must take every listed input and return every
  listed output, spelled exactly, and use the listed rule identifier; its other calcs are free. You may add
  inputs and outputs the library lacks.
  If the specification names the same quantity differently, write an exchange that maps one to the other.
- One call assesses one case. Do not fold several cases into one call with an output per case.
- A rule identifier is a single identifier.

{instructions}"""

BUILDER_SYSTEM = """You design one function of a build system for {building} designs. You do not assess one {building}.
Imports allowed in the module: math, typing, bisect, and toolbox. The toolbox, exactly as it is:
{toolbox}
You write the function, the parameter schema, and the verification rule that assess any {building}.
The function is a calc on one part definition and runs once per usage of that part, with scalar inputs
resolved for that usage. Aggregate inputs arrive already combined, as one number.
Follow the interface spec exactly: its input and output names are fixed.
Follow the architecture and non-functional requirements exactly.
Parameterize every constant a program might change, unless the person said to keep it fixed.
Treat measures as data that is passed in. Other agents are building other functions at the same time,
and you share one dictionary of terms with them. Never reuse a name with a different unit.
check() returns findings only when the design fails. A design that passes returns an empty list: no notes, no info findings.
The output named passes has unit bool. In your own tests, compute expected numbers from the method. Do not guess them.
Return design notes, the terms you use, a complete module, and a complete pytest module that imports from 'solution'.

{specialist}"""

REVIEW_SYSTEM = """You are the lead systems engineer reviewing a generated build system before people use it.
Code already ran every test and an integration pass. Your job is judgment:
- Use recall_lessons before you judge.
- Read each function's schema and notes against the non-functional requirements. Tests do not check everything.
- Read the sensitivity and the interactions. A first-order coupling is an input of one element moving another
  element's margin through an exchange. A second-order interaction is two inputs whose joint effect is not the sum
  of their single effects. Say which matter for this kind of system and why. Do not restate the table.
- Ask for redesign of a function only when the function is wrong or does not integrate. A design instance
  that fails a requirement is not a defect in the function. Alternatives handle that later.
- Record reusable lessons. A lesson about this system stays with this project. Only a lesson about method,
  true for any system, belongs to every project: say so with holds_for_every_project when you store one.

{instructions}"""

INPUTS_SYSTEM = """You prepare inputs for a build system from the person's original request.
- First the usages: what exists in the described design, as a tree that follows the structure. Two panels
  are two usages of the panel part; three node pools are three usages, each of the subtype that fits. Name
  usages in camelCase. Respect the multiplicities.
- Then the values, keyed by usage path and attribute (eps.panel1.panel_area_m2). A value that holds for the
  whole design, such as mission life, may stay global (no path). An attribute of a part goes on each usage
  of that part. Never set an aggregate input: it is computed from the usages.
- The baseline must match what the person described. Keep every value they stated, tagged 'user'.
- Fill any input the functions need and the person did not give, tagged 'ai'.
- Then propose two or three alternative designs worth comparing, especially ones that fix a failing
  requirement in the baseline. An alternative lists only the values that differ from the baseline."""

TRIAGE_SYSTEM = """A person tested a generated build system and sent a follow-up. Decide what it changes.
- A new or changed value is a parameter change. A new or changed obligation is a requirement change.
- Data they supply is a new measure. A result they expect is a new worked case.
- If new evidence contradicts an existing worked case, retire that case.
- A part added, removed, or swapped for another part (two deployable panels instead of two body-mounted ones)
  is a usage change, with the values of the new usage. A changed number on a part that stays is a parameter change.
- List a function for revision only when its method must change. Evidence alone does not need a new function.
Touch nothing the follow-up does not call for."""


DECOMPOSE_SYSTEM = """You decompose one part of a {building} one level finer, as the specialist who assesses it.
- Name the concrete things inside the part, the way a domain engineer would: types, not instances. Give each a
  parent (the part itself or another new part), a multiplicity, and a subtype relation where one exists.
- Stay within the depth you are given. Do not repeat parts that already exist.
- Add an attribute only when the finer part carries a quantity the assessment should use and the function does
  not already take. Every attribute names the part it describes, has a unit, and an example value for the
  described instance. A name means one thing across the whole system: the shared dictionary lists the names in
  use with their units. Reuse a listed name only with its unit.
- If you add attributes, say in method_change how the function should use them, in the method's own terms.
  Keep the existing inputs and outputs. The function's identifier and its rule do not change.

{specialist}"""

DECOMPOSE_REVIEW_SYSTEM = """You are the lead systems engineer reviewing a proposed finer decomposition of one part before it enters the model.
- Use recall_lessons before you judge.
- Judge whether the parts are the ones a domain engineer would name, whether the levels and multiplicities are
  right, whether each new attribute belongs to the part that carries it, and whether the attributes are worth
  the inputs they add to the function. Reject padding: a part with no attribute and no subtype adds nothing.
- Accept when the breakdown would survive a design review. Ask for rework with one specific instruction otherwise.
- Record a lesson only when it would change how this kind of system is decomposed next time.

{instructions}"""


def part_depth(name: str, by_name: dict) -> int:
    """Levels below the whole: a subsystem is 1, a part inside it 2, and so on. A subtype sits at its supertype's level."""
    depth, cur, seen = 0, name, set()
    while cur and cur in by_name and cur not in seen:
        seen.add(cur)
        p = by_name[cur]
        if p["parent"]:
            depth, cur = depth + 1, p["parent"]
        elif p["specializes"]:
            cur = p["specializes"]
        else:
            return depth + 1
    return depth + 1


def structure_check(parts: list[dict], contracts: list[dict], building: str, max_depth: int) -> list[str]:
    """The decomposition must be a tree no deeper than the harness allows, name real parents and supertypes,
    assign every part to a capability that has a contract, and own every input. Shared by the interface gate
    and the drill-down gate, so a finer decomposition is held to the same rules as the first one."""
    names = [p["name"] for p in parts]
    errors = [f"part '{n}' is defined twice" for n in sorted({n for n in names if names.count(n) > 1})]
    by_name = {p["name"]: p for p in parts}
    caps = {c["capability"] for c in contracts}
    if not parts:
        return [f"structure is empty. Decompose the {building} into parts: subsystems, then the concrete things inside them, each assigned to a capability."]
    for p in parts:
        if p["parent"] and p["parent"] not in by_name:
            errors.append(f"part '{p['name']}' has parent '{p['parent']}', which is not a part")
        if p["specializes"] and p["specializes"] not in by_name:
            errors.append(f"part '{p['name']}' specializes '{p['specializes']}', which is not a part")
        if p["capability"] not in caps:
            errors.append(f"part '{p['name']}' is assigned to capability '{p['capability']}', which has no contract. Known: {sorted(caps)}")
        seen, cur = set(), p["name"]                              # the parent chain must end at the whole
        while cur and cur in by_name:
            if cur in seen:
                errors.append(f"part '{p['name']}' is inside itself through its parents"); break
            seen.add(cur); cur = by_name[cur]["parent"]
        if not errors and part_depth(p["name"], by_name) > max_depth:
            errors.append(f"part '{p['name']}' is {part_depth(p['name'], by_name)} levels below the whole. This harness allows {max_depth} (structure_depth in harness.yaml). "
                          f"Fold it into its parent or stop one level higher.")
    for cap in caps:
        if not any(p["capability"] == cap for p in parts):
            errors.append(f"capability '{cap}' owns no part. Name the things its function assesses.")
    for c in contracts:
        own = [p["name"] for p in parts if p["capability"] == c["capability"]]
        for t in c["inputs"]:
            if not t.get("part"):
                t["part"] = own[0] if own else ""                   # default: the capability's first part
            elif t["part"] not in by_name:
                errors.append(f"{c['capability']} input '{t['name']}' names part '{t['part']}', which is not in the structure")
    return errors


def apply_usage_change(design: dict, structure: list[dict], change: dict, iteration: int) -> None:
    """Add, remove, or replace one usage in a design, and keep the values dictionary in step."""
    usages = design.setdefault("usages", None) or engine.default_usages(structure)
    design["usages"] = usages

    def find(path: str):
        parent, node = None, {"parts": usages}
        for name in [p for p in path.split(".") if p]:
            parent, node = node, next((u for u in node["parts"] if u["name"] == name), None)
            if node is None:
                raise ValueError(f"no usage at '{path}'")
        return parent, node

    def drop_values(path: str) -> None:
        for key in [k for k in design["values"] if k == path or k.startswith(path + ".")]:
            design["values"].pop(key); design.get("tags", {}).pop(key, None)

    if change["action"] == "remove":
        parent, node = find(change["path"])
        parent["parts"].remove(node); drop_values(change["path"])
        return
    new = {"name": change["name"] or engine._ident(change["part"]), "part": change["part"], "parts": []}
    if change["action"] == "replace":                     # the new part keeps the old usage's values unless the change states others
        parent, node = find(change["path"])
        new["parts"] = node.get("parts", [])
        parent["parts"][parent["parts"].index(node)] = new
        path = ".".join(change["path"].split(".")[:-1] + [new["name"]])
        if path != change["path"]:
            for key in [k for k in design["values"] if k == change["path"] or k.startswith(change["path"] + ".")]:
                design["values"][path + key[len(change["path"]):]] = design["values"].pop(key)
                if key in design.get("tags", {}):
                    design["tags"][path + key[len(change["path"]):]] = design["tags"].pop(key)
    else:
        parent, node = find(change["path"]) if change["path"] else (None, {"parts": usages})
        node["parts"].append(new)
        path = f"{change['path']}.{new['name']}" if change["path"] else new["name"]
    for item in change.get("values", []):
        design["values"][f"{path}.{item['name']}"] = item["value"]
        design.setdefault("tags", {})[f"{path}.{item['name']}"] = item.get("source", "user")


def rerender(project: Project) -> list[str]:
    """Evaluate every design through the active functions and write results, report, and SysML v2. No model call."""
    from . import render, runtime
    written = []
    for name, design in project.designs().items():
        result = runtime.evaluate_design(project, design)
        project.write(f"outputs/{name}/results.json", result)
        project.path("outputs", name, "design.sysml").write_text(render.sysml(project, design, result), encoding="utf-8")
        project.path("outputs", name, "report.html").write_text(render.report(project, design, result), encoding="utf-8")
        written.append(name)
    return written


def toolbox_signatures() -> str:
    """The toolbox helpers with their exact signatures and first doc line, so a builder never guesses a keyword."""
    import inspect
    from . import toolbox
    lines = []
    for name, fn in inspect.getmembers(toolbox, inspect.isfunction):
        if fn.__module__ == toolbox.__name__ and not name.startswith("_"):
            doc = (inspect.getdoc(fn) or "").splitlines()
            lines.append(f"  toolbox.{name}{inspect.signature(fn)}" + (f"  # {doc[0]}" if doc else ""))
    return "\n".join(lines)


def dictionary(project: Project) -> SharedDictionary:
    return SharedDictionary(project.path("dictionary.json"))


def provisional_values(project: Project) -> tuple[dict, dict]:
    """Values to exercise the functions before a person or agent writes a full design instance: the
    specification's parameters and every example, global, over the structure's default usage tree."""
    spec, interfaces = project.spec(), project.interfaces()
    values = {p["name"]: p["value"] for p in spec.get("parameters", [])}
    tags = {p["name"]: p["source"] for p in spec.get("parameters", [])}
    terms = [t for c in interfaces.get("contracts", []) for t in c["inputs"]]
    terms += [t for x in interfaces.get("exchanges", []) for t in x.get("parameters", [])]
    for term in terms:
        name = (term.get("of") or term["name"]) if term.get("aggregate") else term["name"]   # an aggregate's example is the per-usage value
        if name not in values and term.get("example") is not None:
            values[name], tags[name] = term["example"], "ai"
    return values, tags


def provisional_design(project: Project) -> dict:
    values, tags = provisional_values(project)
    return {"name": "provisional", "usages": engine.default_usages(project.interfaces().get("structure") or []), "values": values, "tags": tags}


def library_contract(contracts: list[dict], capability: str, want: dict | None) -> dict | None:
    """The one calc of a capability that the library's worked cases exercise: the one with the library's
    rule identifier, else the one taking most of the library's input names. Its sibling calcs are free."""
    own = [c for c in contracts if c["capability"] == capability]
    if not own:
        return None
    if want and want.get("rule_id"):
        hit = next((c for c in own if c["rule_id"] == want["rule_id"]), None)
        if hit:
            return hit
    if want:
        names = set(want.get("inputs_with_a_sample_value", {}))
        return max(own, key=lambda c: len(names & {t["name"] for t in c["inputs"]}))
    return own[0]


def vocabulary(project: Project, capabilities: list[str]) -> dict:
    """The names the project's library and evidence already use, per capability.

    Worked cases are written by people in these names, and the acceptance tests are generated from
    the worked cases. So the specification and the interface spec must use the same names, or no
    function could pass. A capability with no library entry and no evidence has no entry here.
    """
    out = {}
    for cap in capabilities:
        measures, cases = project.evidence(cap)
        rule = project.process(cap).get("rule_id", "")
        inputs, outputs = {}, []
        for case in cases:
            for name, value in case.get("inputs", {}).items():
                inputs.setdefault(name, value)
            outputs += [k for k in case.get("expect", {}) if k not in outputs]
        if inputs or outputs or rule:
            out[cap] = {"rule_id": rule, "inputs_with_a_sample_value": inputs, "outputs": outputs,
                        "measure_columns": sorted({k for m in measures for k in m})}
    return out


def fixture_key(base: str, attempt: int, *alternates: str) -> str:
    """The saved answer an offline replay uses: this attempt's, else the task's, else the latest earlier attempt,
    else the same for each alternate key (a calc's capability-level fixture, say). A version past the saved
    ones (after a rollback, say) replays the highest saved version of that function."""
    for key in (f"{base}.a{attempt}", base):
        if (Settings.fixtures / f"{key}.json").exists():
            return key
    if alternates:
        found = fixture_key(alternates[0], attempt, *alternates[1:])
        if (Settings.fixtures / f"{found}.json").exists():
            return found
    earlier = sorted(Settings.fixtures.glob(f"{base}.a*.json"))
    if earlier:
        return earlier[-1].stem
    m = re.fullmatch(r"(.*)\.v(\d+)", base)
    if m:
        saved = sorted((int(re.search(r"\.v(\d+)$", f.stem).group(1)), f.stem) for f in Settings.fixtures.glob(f"{m.group(1)}.v*.json") if re.search(r"\.v(\d+)$", f.stem))
        below = [stem for v, stem in saved if v < int(m.group(2))]
        if below:
            return below[-1]
    return base


def plan(project: Project, iteration: int, say=print, max_reviews: int = 2) -> list[Task]:
    P = "" if iteration == 1 else f"i{iteration}."            # task and fixture prefix for follow-ups
    H = project.harness()                                    # the harness definition this project owns
    A, building, prefix = H["agents"], H["building"], H["id_prefix"]
    se_text = project.agent_text(A["systems_engineer"].get("instructions", ""))
    se_tier = A["systems_engineer"].get("tier", "deep")
    known = [s["capability"] for s in A.get("specialists", [])]
    max_reviews = min(max_reviews, H.get("review_rounds", max_reviews))
    max_depth = int(H.get("structure_depth", 3))             # harness.yaml: how many levels below the whole a decomposition may go
    shared, reports = dictionary(project), {}
    model_name = lambda tier: "saved answer" if Settings.offline else MODELS[tier]

    # ---- 2. parameterize ------------------------------------------------------------
    def parameterize(ctx: Context) -> TaskResult:
        prompt = f"The person's request:\n\n{project.requests()[0]}"
        names = vocabulary(project, known)
        if names:
            prompt += f"\n\nNames the library already uses, by capability. Reuse them exactly where the meaning matches:\n{j(names)}"
        if ctx.feedback:
            prompt += "\n\nYour last answer failed these checks. Fix them:\n" + "\n".join(ctx.feedback)
        out = run_agent("studio.parameterize", system=PARAMETERIZE_SYSTEM.format(building=building, specialists=", ".join(known) or "none yet"),
                        prompt=prompt, output=ParameterizedSpec, tier=A["parameterizer"].get("tier", "balanced"), log=ctx.log)
        for r in out["requirements"]:
            r.update(version=1, since=1)
        out["iteration"] = 1
        project.write("spec.json", out)
        ai = sum(p["source"] == "ai" for p in out["parameters"])
        return TaskResult(out, note=f"{len(out['parameters'])} parameters, {ai} filled in by the agent")

    def parameterize_gate(out: dict, ctx: Context) -> list[str]:
        names = [p["name"] for p in out["parameters"]]
        errors = [f"parameter '{n}' appears twice" for n in sorted({n for n in names if names.count(n) > 1})]
        errors += [f"{p['name']} is tagged ai and needs a rationale" for p in out["parameters"] if p["source"] == "ai" and not p["rationale"].strip()]
        errors += [f"{r['id']} is not a 'shall' statement" for r in out["requirements"] if "shall" not in r["text"].lower()]
        if not out["capabilities"]:
            errors.append("list at least one capability")
        return errors

    # ---- 3. interfaces ----------------------------------------------------------------
    def interfaces(ctx: Context) -> TaskResult:
        spec = ctx.inputs["parameterize"]
        library = {c: project.process(c).get("process_example", "") for c in spec["capabilities"]}
        prompt = (f"Parameterized specification:\n{j(spec)}\n\nHow the library says each assessment is done by hand:\n"
                  f"{yaml.safe_dump(library, sort_keys=False)}\n"
                  f"Names each contract must use, from the library's worked cases:\n{j(vocabulary(project, spec['capabilities']))}")
        if ctx.feedback:
            prompt += "\nYour last answer failed these checks. Fix them:\n" + "\n".join(ctx.feedback)
        out = run_agent("studio.interfaces", system=INTERFACE_SYSTEM.format(prefix=prefix, building=building, depth=max_depth, instructions=se_text), prompt=prompt,
                        output=InterfaceSpec, tier=se_tier, log=ctx.log)
        fill_examples(out, spec, ctx)
        out["version"] = 1
        project.write("interfaces.json", out)
        return TaskResult(out, note=f"{len(out['contracts'])} function contracts, {len(out['exchanges'])} exchanges")

    def fill_examples(out: dict, spec: dict, ctx: Context) -> None:
        """Inputs the systems engineer agent left with no value get an example from a short, focused second pass.

        The architecture is the deep model's job. Picking a plausible default for twenty inputs is not,
        and a long spec tends to leave some blank. A small agent fills exactly those, tagged ai.
        """
        given = {p["name"] for p in spec["parameters"]}
        targets = {x["target_input"] for x in out["exchanges"]}
        missing = [(c["capability"], t) for c in out["contracts"] for t in c["inputs"]
                   if not (t["name"] in given or t["name"] in targets or t.get("optional") or t.get("example") is not None
                           or (t.get("aggregate") and not t.get("of")))]
        missing += [("exchange " + x["id"], t) for x in out["exchanges"] for t in x.get("parameters", []) if t.get("example") is None]
        if not missing:
            return
        asked = {(t.get("of") or t["name"]) if t.get("aggregate") else t["name"]: {"unit": t["unit"], "description": t["description"] + (f" (the value one {t.get('part')} carries)" if t.get("aggregate") else ""), "used_by": cap} for cap, t in missing}
        prompt = (f"The described {building}:\n{j({p['name']: p['value'] for p in spec['parameters']})}\n\n"
                  f"Give one example value for each of these inputs, as a number or a short string, sized for that instance:\n{j(asked)}")
        got = run_agent("studio.examples", system=f"You supply reasonable default values for inputs of a {building} assessment. "
                        "Answer every name asked, once, with a value in the stated unit and a one-line rationale. Never leave one out.",
                        prompt=prompt, output=ExampleValues, tier="balanced", log=ctx.log)
        values = {v["name"]: v for v in got["values"]}
        for cap, t in missing:
            key = (t.get("of") or t["name"]) if t.get("aggregate") else t["name"]
            if key in values:
                t["example"] = values[key]["example"]
                t["example_rationale"] = values[key]["rationale"]
        ctx.log("examples_filled", asked=sorted(asked), filled=sorted(n for n in asked if n in values))

    def structure_errors(out: dict) -> list[str]:
        return structure_check(out.get("structure") or [], out["contracts"], building, max_depth)

    def interfaces_gate(out: dict, ctx: Context) -> list[str]:
        spec, errors, units = ctx.inputs["parameterize"], [], {}
        errors += structure_errors(out)
        given = {p["name"] for p in spec["parameters"]}
        targets = {x["target_input"] for x in out["exchanges"]}
        by_element = {c["capability"]: c for c in out["contracts"]}
        ids = [c["function_id"] for c in out["contracts"]]
        errors += [f"function id '{i}' is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
        names = vocabulary(project, [c["capability"] for c in out["contracts"]])
        parts = {p["name"]: p for p in out.get("structure") or []}
        caps = {c["capability"] for c in out["contracts"]}
        for cap_name in caps:                               # the worked cases fix the names of one calc per capability
            want = names.get(cap_name)
            keeper = library_contract(out["contracts"], cap_name, want)
            if want and keeper:
                have_in, have_out = {t["name"] for t in keeper["inputs"]}, {t["name"] for t in keeper["outputs"]}
                miss_in = [n for n in want["inputs_with_a_sample_value"] if n not in have_in]
                miss_out = [n for n in want["outputs"] if n not in have_out]
                if miss_in:
                    errors.append(f"One {cap_name} calc must take the inputs the worked cases use, spelled exactly: {miss_in}. "
                                  f"{keeper['function_id']} has {sorted(have_in)}. Rename yours to match. Do not keep both.")
                if miss_out:
                    errors.append(f"One {cap_name} calc must return the outputs the worked cases expect, spelled exactly: {miss_out}. {keeper['function_id']} has {sorted(have_out)}.")
                if want["rule_id"] and keeper["rule_id"] != want["rule_id"]:
                    errors.append(f"The {cap_name} calc the worked cases exercise must use rule identifier '{want['rule_id']}', as the library has it. {keeper['function_id']} wrote '{keeper['rule_id']}'.")
        for c in out["contracts"]:
            own_part = c.get("part") or ""
            if parts and own_part not in parts:
                errors.append(f"{c['function_id']} belongs to part '{own_part}', which is not in the structure. Name the part definition this calc assesses.")
            elif parts and parts[own_part]["capability"] != c["capability"]:
                errors.append(f"{c['function_id']} is a {c['capability']} calc on {own_part}, which the structure assigns to {parts[own_part]['capability']}. "
                              f"Put the calc under the capability that owns the part, or move the part.")
            for term in c["inputs"]:
                if term.get("aggregate"):
                    over = term.get("part") or ""
                    if parts and over not in parts:
                        errors.append(f"{c['function_id']} input '{term['name']}' aggregates over part '{over}', which is not in the structure")
                    elif parts and own_part in parts and not engine.inside(over, own_part, parts):
                        errors.append(f"{c['function_id']} input '{term['name']}' aggregates over {over}, which is not inside {own_part}. An aggregate combines the usages inside the calc's own part.")
                    of = term.get("of") or term["name"]
                    carried = any(t["name"] == of and t.get("part") == over for c2 in out["contracts"] for t in c2["inputs"] if not t.get("aggregate"))
                    produced = any(t["name"] == of and engine.contract_part(c2, out["structure"]) == over for c2 in out["contracts"] for t in c2["outputs"])
                    declared = term.get("example") is not None or of in given                 # the term declares the per-usage attribute: its example, or the specification's value
                    if parts and not (carried or produced or declared):
                        errors.append(f"{c['function_id']} input '{term['name']}' aggregates '{of}' over {over}, but nothing carries '{of}' on {over}. "
                                      f"Give this input an example (the value one {over} carries), or list '{of}' as an input of a calc on {over}, "
                                      f"or as an output of a calc on {over}.")
            if not re.fullmatch(r"[A-Za-z0-9_.\-]+", c["rule_id"] or ""):
                errors.append(f"{c['capability']} rule identifier '{c['rule_id']}' must be one identifier with no spaces or commas")
            if not re.fullmatch(re.escape(prefix) + r"\.[a-z0-9_]+\.[a-z0-9_]+", c["function_id"]):
                errors.append(f"'{c['function_id']}' is not of the form {prefix}.<capability>.<noun>")
            for term in c["inputs"] + c["outputs"]:
                if _unit(units.setdefault(term["name"], term["unit"])) != _unit(term["unit"]):
                    errors.append(f"'{term['name']}' has unit {term['unit']} in {c['capability']} and {units[term['name']]} elsewhere")
            for term in c["inputs"]:
                if not (term["name"] in given or term["name"] in targets or term.get("optional") or term.get("aggregate") or term.get("example") is not None):
                    errors.append(f"{c['capability']} input '{term['name']}' has no value: it is not in the specification, no exchange supplies it, "
                                  f"and it has no example. Set example to a number for the described {building}, or mark it optional.")
        examples = {t["name"] for c in out["contracts"] for t in c["inputs"] if t.get("example") is not None or t.get("aggregate")}
        examples |= {t["name"] for x in out["exchanges"] for t in x.get("parameters", []) if t.get("example") is not None}
        outputs = {t["name"] for c in out["contracts"] for t in c["outputs"]}
        listed = {t["name"] for c in out["contracts"] for t in c["inputs"]} | {t["name"] for x in out["exchanges"] for t in x.get("parameters", [])}
        for x in out["exchanges"]:
            tpart = engine.element_part(x["target_element"], out.get("structure") or [])
            on_target = [t for c in out["contracts"] for t in c["inputs"] if t["name"] == x["target_input"] and not t.get("aggregate")
                         and (not parts or (t.get("part") or engine.contract_part(c, out["structure"])) == tpart or engine.contract_part(c, out["structure"]) == tpart)]
            if parts and tpart not in parts:
                errors.append(f"{x['id']} targets '{x['target_element']}', which is not a part of the structure")
            elif not on_target:
                errors.append(f"{x['id']} supplies '{x['target_input']}', which no calc takes on {tpart}. Target the part that carries that attribute.")
            if parts and engine.element_part(x["source_element"], out["structure"]) not in parts:
                errors.append(f"{x['id']} comes from '{x['source_element']}', which is not a part of the structure")
            names = []
            try:
                names = runtime.names_in(x["expression"])
            except SyntaxError as exc:
                errors.append(f"{x['id']} expression does not parse: {exc}")
            if x["target_input"] in names and not runtime.identity_source(x, out["contracts"]):
                source_outputs = sorted(t["name"] for t in by_element.get(x["source_element"], {}).get("outputs", []))
                errors.append(f"{x['id']} computes '{x['target_input']}' from an expression that reads '{x['target_input']}' itself. "
                              f"An exchange derives its target from other names. If {x['source_element']} produces this value, list "
                              f"'{x['target_input']}' among {x['source_element']}'s outputs (its outputs are {source_outputs}); a pass-through "
                              f"of an output under the same name is allowed. If the value is simply a parameter, drop the exchange and give "
                              f"the input an example instead.")
            try:
                for n in names:
                    if n in given | targets | examples | outputs:
                        continue
                    if n in listed:
                        errors.append(f"{x['id']}: '{n}' is listed as a parameter but its example is empty. Give it a number.")
                    else:
                        errors.append(f"{x['id']}: '{n}' has no value. It is not in the specification, not an output of any function, "
                                      f"and not the target of another exchange. Either add it to this exchange's parameters with a numeric "
                                      f"example, or make it an output of the function that computes it.")
                # distinct trial values, so a - b is never zero by accident; a literal zero divisor still shows
                runtime.compute(x["expression"], {n: 2.0 + i for i, n in enumerate(names)})
            except (SyntaxError, ValueError) as exc:
                errors.append(f"{x['id']} expression is not plain arithmetic: {exc}")
            except ZeroDivisionError:
                errors.append(f"{x['id']} expression divides by zero for ordinary values: {x['expression']}. Guard the divisor or restate the exchange.")
        try:
            runtime.schedule(out, given)
        except (ValueError, SyntaxError) as exc:
            errors.append(str(exc))
        if not errors:                                       # the contracts become the starting vocabulary
            project.path("dictionary.json").unlink(missing_ok=True)
            for c in out["contracts"]:
                shared.propose(c["capability"], [{"name": t["name"], "unit": t["unit"], "description": t["description"]}
                                                 for t in c["inputs"] + c["outputs"]])
        return errors

    # ---- 4 and 7. one specialist agent writes one function version ----------------------------
    def build(function_id: str, version: str, instruction: str = "", reason: str = "first build"):
        def brief() -> dict:
            contract = project.contract(function_id)
            cap, spec = contract["capability"], project.spec()
            measures, cases = project.evidence(cap)
            process = project.process(cap)
            wanted = {t["name"] for t in contract["inputs"]}
            structure = project.interfaces().get("structure") or []
            part = engine.contract_part(contract, structure)
            inside = [p["name"] for p in structure if engine.inside(p["name"], part, {q["name"]: q for q in structure})]
            return {"function_id": function_id, "version": version, "capability": cap, "question": contract["question"],
                    "calc_on_part": part, "parts_inside_it": inside,
                    "runs": f"once per usage of {part}; aggregate inputs arrive combined over the usages inside that one",
                    "rule_id": contract["rule_id"], "interface_spec": {"inputs": contract["inputs"], "outputs": contract["outputs"]},
                    "method": process.get("process_example") or contract["method"],
                    "architecture": H["architecture"], "non_functional": H["non_functional"],
                    "parameterization_notes": spec.get("parameterization_notes", []),
                    "fixed_by_the_person": [p["name"] for p in spec["parameters"] if not p["parameterized"]],
                    "this_mission": {p["name"]: p["value"] for p in spec["parameters"] if p["name"] in wanted},
                    "requirements": [r for r in spec["requirements"] if r["capability"] == cap],
                    "measures": measures, "worked_cases": cases}

        def run(ctx: Context) -> TaskResult:
            b = brief()
            cap = b["capability"]
            if ctx.attempt > 1:
                shared.withdraw(cap)
            snapshot = shared.read()                         # no lock is held while the model thinks
            ctx.log("shared_read", version=snapshot["version"], terms=sorted(snapshot["terms"]))
            terms = {n: f"{t['unit']}  ({t['description']}; defined by {t['owner']})" for n, t in snapshot["terms"].items()}
            prompt = (f"Brief:\n{yaml.safe_dump(b, sort_keys=False)}\n"
                      f"Shared dictionary, version {snapshot['version']}:\n{yaml.safe_dump(terms) if terms else 'empty'}\n")
            if version != "v1":
                previous = project.registry()[function_id]["active"]
                old = project.function_dir(function_id, previous)
                prompt += (f"\nThis is a revision. The current version is {previous}:\n{(old / 'solution.py').read_text(encoding='utf-8')}\n"
                           f"\nIts tests:\n{(old / 'test_solution.py').read_text(encoding='utf-8')}\n")
            if instruction:
                prompt += f"\nChange required:\n{instruction}\n"
            if ctx.feedback:
                prompt += "\nYour last submission failed its checks. Fix it:\n" + "\n".join(ctx.feedback)
                folder = project.function_dir(function_id, version)       # the last attempt, as the gate saw it
                if (folder / "solution.py").exists() and (folder / "test_solution.py").exists():
                    prompt += (f"\nYour last submission, so you can correct it rather than start again. Keep what passed.\n"
                               f"solution.py:\n{(folder / 'solution.py').read_text(encoding='utf-8')}\n"
                               f"test_solution.py:\n{(folder / 'test_solution.py').read_text(encoding='utf-8')}\n")
            who, tools = project.specialist(cap), []
            if not Settings.offline and who.get("tools"):
                tools = tooling.load_tools(project.root, who["tools"])     # the tools this harness defines and maps to this agent
            noun = function_id.split(".")[-1]
            suffix = "" if version == "v1" else f".{version}"
            base, alternate = f"builder.{cap}.{noun}{suffix}", f"builder.{cap}{suffix}"     # a saved answer per calc, else per capability
            tier = ctx.overrides.get("tier") or who.get("tier", "balanced")
            if ctx.overrides.get("tier"):
                ctx.log("tier_override", tier=tier, attempt=ctx.attempt)
            out = run_agent(fixture_key(base, ctx.attempt, alternate) if Settings.offline else base,
                            system=BUILDER_SYSTEM.format(building=building, specialist=who["text"], toolbox=toolbox_signatures()), prompt=prompt,
                            output=BuilderSubmission, tier=tier, tools=tools, log=ctx.log)
            result = shared.propose(cap, out["terms"])       # merge and write in milliseconds
            ctx.log("shared_commit" if result["ok"] else "shared_conflict", **result)
            out.update(dictionary=result, function_id=function_id, version=version)
            return TaskResult(out, note=f"{function_id} {version}, attempt {ctx.attempt}")

        def gate(out: dict, ctx: Context) -> list[str]:
            if not out["dictionary"]["ok"]:
                return [f"The shared dictionary refused your terms. '{c['name']}' is already defined by {c['owner']} "
                        f"with unit {c['existing']} ({c['description']}). You used unit {c['yours']}. "
                        f"Use the existing meaning, or choose a different name." for c in out["dictionary"]["conflicts"]]
            contract = project.contract(function_id)
            measures, cases = project.evidence(contract["capability"])
            folder = project.function_dir(function_id, version)
            report = gates.run_gates(folder, function_id, contract["rule_id"], out["module_code"], out["test_code"],
                                     measures, cases, contract, out["terms"])
            ctx.log("gate_output", check="structure", text="ok" if report["structure"]["ok"] else "\n".join(report["structure"]["errors"]))
            for key in ("own_tests", "acceptance"):
                if report[key]:
                    ctx.log("gate_output", check=report[key]["label"], text=report[key]["output"])
                    say(f"    {function_id} {version}: {report[key]['label']}: " + (report[key]["output"].strip().splitlines() or ["no output"])[-1])
            ctx.log("gate_output", check="interface conformance", text="ok" if report["conformance"]["ok"] else "\n".join(report["conformance"]["errors"]))
            if report["errors"]:
                return report["errors"]
            module = gates.load_module(folder, function_id)
            manifest = {"function_id": function_id, "version": version, "capability": contract["capability"],
                        "built_with": model_name(ctx.overrides.get("tier") or project.specialist(contract["capability"]).get("tier", "balanced")),
                        "built_by": "generic specialist" if project.specialist(contract["capability"])["generic"] else f"{contract['capability']} specialist",
                        "iteration": iteration, "reason": reason,
                        "gates": gates.summary(report), "parameters": dict(module.PARAMETERS),
                        "design_notes": out["design_notes"], "terms": out["terms"],
                        "evidence": {"measures": len(measures), "cases": len(cases),
                                     "user_cases": sum(c.get("source") == "user" for c in cases)}}
            (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            project.register(function_id, version, {k: manifest[k] for k in ("capability", "built_with", "iteration", "reason", "gates", "evidence")})
            with project.lock:                               # several specialists may finish at once
                spec_now, declared = project.interfaces(), {t["name"]: t for t in out["terms"]}
                target = next(c for c in spec_now["contracts"] if c["function_id"] == function_id)
                extra = [n for n in module.PARAMETERS if n not in {t["name"] for t in target["inputs"]}]
                if extra:                                    # the interface spec learns the inputs a new version added
                    target["inputs"] += [{"name": n, "unit": declared[n]["unit"], "description": declared[n]["description"],
                                          "optional": True, "example": None, "added_by": f"{function_id} {version}"} for n in extra]
                    spec_now["version"] = spec_now.get("version", 1) + 1
                    project.write("interfaces.json", spec_now)
            project.log_change({"kind": "function", "function_id": function_id, "version": version, "reason": reason})
            return []
        return run, gate

    def build_task(function_id: str, version: str, deps: list[str], instruction: str = "", reason: str = "first build") -> Task:
        run, gate = build(function_id, version, instruction, reason)
        cap = project.contract(function_id)["capability"]
        task_id = f"{P}build.{function_id.split('.', 1)[1]}" + ("" if version == "v1" else f".{version}")   # one task per calc: build.power.margin
        return Task(task_id, run, deps=deps, kind="agent", model=project.specialist(cap).get("tier", "balanced"), gate=gate, max_attempts=4)

    # ---- 8. interface verification, and re-verification when evidence changes ------------------
    def verify(n: int, rebuild_on_failure: bool):
        def run(ctx: Context) -> TaskResult:
            registry, rows, failing = project.registry(), {}, []
            for contract in project.interfaces()["contracts"]:
                fid = contract["function_id"]
                if fid not in registry:
                    rows[fid] = {"ok": False, "errors": ["not built"]}
                    continue
                version = registry[fid]["active"]
                folder = project.function_dir(fid, version)
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                measures, cases = project.evidence(contract["capability"])
                report = gates.run_gates(folder, fid, contract["rule_id"], (folder / "solution.py").read_text(encoding="utf-8"),
                                         (folder / "test_solution.py").read_text(encoding="utf-8"), measures, cases,
                                         contract, manifest["terms"])
                rows[fid] = {"version": version, **gates.summary(report), "errors": report["errors"]}
                manifest["gates"], manifest["evidence"] = gates.summary(report), {"measures": len(measures), "cases": len(cases),
                                                                                    "user_cases": sum(c.get("source") == "user" for c in cases)}
                (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                with project.lock:
                    reg = project.registry()
                    reg[fid]["versions"][version].update(gates=manifest["gates"], evidence=manifest["evidence"])
                    project.write("registry.json", reg)
                ctx.log("gate_output", check=f"{fid} {version}", text="ok" if not report["errors"] else report["errors"][0][-400:])
                if report["errors"]:
                    failing.append((fid, report["errors"][0]))
            out = {"functions": rows, "failing": [f for f, _ in failing],
                   "rebuild": {fid: {"version": project.next_version(fid), "error": err[-1200:]} for fid, err in failing} if failing and rebuild_on_failure and n < 3 else {}}
            note = "every active function honors its interface spec and evidence" if not failing else f"{len(failing)} function(s) fail: " + ", ".join(f for f, _ in failing)
            return TaskResult(out, spawn=verify_spawn(n, rebuild_on_failure)(out), note=note)
        return run

    def verify_spawn(n: int, rebuild_on_failure: bool):
        """The tasks a verify step adds, from its output alone, so a resumed run adds the same ones."""
        def spawn(out: dict) -> list[Task]:
            me = f"{P}verify.{n}" if rebuild_on_failure else f"{P}verify.r{n}"
            if out.get("rebuild"):
                builds = [build_task(fid, r["version"], [me], f"The current version no longer passes the evidence:\n{r['error']}", "evidence changed")
                          for fid, r in out["rebuild"].items()]
                nxt = Task(f"{P}verify.{n + 1}", verify(n + 1, True), deps=[b.id for b in builds], respawn=verify_spawn(n + 1, True))
                return builds + [nxt] + tail(n + 1, [nxt.id])
            return tail(n, [me]) if rebuild_on_failure else []
        return spawn

    # ---- 5. validate ---------------------------------------------------------------------
    def validate(n: int):
        def run(ctx: Context) -> TaskResult:
            design = project.designs().get("baseline") or provisional_design(project)
            values, tags = design["values"], design.get("tags", {})
            result = runtime.evaluate_design(project, design)
            effects = runtime.sensitivity(project, design)
            registry = project.registry()
            functions = {fid: {"version": e["active"], **e["versions"][e["active"]]["gates"]} for fid, e in registry.items()}
            out = {"functions": functions, "integration": {k: result[k] for k in ("exchanges", "findings", "errors", "passes")},
                   "results": {fid: r.get("result") for fid, r in result["functions"].items()},
                   "per_usage": {fid: {path: u.get("result") or u.get("error") for path, u in r.get("usages", {}).items()} for fid, r in result["functions"].items()},
                   "emergent": {k: effects[k] for k in ("baseline_passes", "flips", "couplings", "interactions")},
                   "sensitivity": {fid: {"base": t["base"], "top": t["rows"][:5]} for fid, t in effects["tornado"].items()},
                   "values": values, "tags": tags, "usages": design.get("usages") or engine.default_usages(project.interfaces().get("structure") or [])}
            cross = sorted({(c["input"], c["affects"]) for c in effects["couplings"]})
            return TaskResult(out, note=f"{len(functions)} functions routed, {len(result['findings'])} finding(s) on the described design, "
                                        f"{len(cross)} cross-element couplings, {len(effects['interactions'])} interaction(s)")
        return run

    # ---- 6. review -------------------------------------------------------------------------
    def review(n: int, validate_id: str):
        def run(ctx: Context) -> TaskResult:
            v, memory = ctx.inputs[validate_id], open_memory()
            shared_pool = H.get("memory", "project+shared") != "project"   # harness.yaml: memory: project | project+shared
            query = f"{building} build system review: interfaces, units, emergent effects, " + " ".join(sorted(v["functions"]))
            lessons = memory.search(query, scope=project.name, shared=shared_pool)
            ctx.log("memory_search", hits=len(lessons), query=query, lessons=lessons)
            registry, manifests = project.registry(), {}
            for fid, e in registry.items():
                m = json.loads((project.function_dir(fid, e["active"]) / "manifest.json").read_text(encoding="utf-8"))
                manifests[fid] = {"version": e["active"], "parameters": m["parameters"], "design_notes": m["design_notes"], "gates": m["gates"]}
            prompt = (f"Review round {n} of {max_reviews}.\nLessons from earlier programs:\n{j(lessons)}\n"
                      f"Non-functional requirements:\n{j(H['non_functional'])}\n"
                      f"Interface spec:\n{j(project.interfaces())}\nFunctions as built:\n{j(manifests)}\n"
                      f"Integration on the described design:\n{j(v['integration'])}\nResults:\n{j(v['results'])}\n"
                      f"Sensitivity: the five inputs each function's margin responds to most, from moving each input ten percent:\n{j(v['sensitivity'])}\n"
                      f"Pass flips, cross-element couplings, and second-order interactions. An interaction is the part of a joint change "
                      f"two single changes do not explain. Cross-domain ones are the emergent candidates:\n{j(v['emergent'])}")
            if ctx.feedback:
                prompt += "\nYour last answer failed these checks. Fix them:\n" + "\n".join(ctx.feedback)
            out = run_agent(f"studio.{P}review.{n}", system=REVIEW_SYSTEM.format(instructions=se_text), prompt=prompt, output=SystemReview,
                            tier=se_tier, tools=memory_tools(memory, scope=project.name, shared=shared_pool), log=ctx.log)
            if any(r["function_id"] not in registry for r in out["redesign"]):
                return TaskResult(out, note="redesign names an unknown function")
            for lesson in out["lessons"]:
                memory.add(lesson, scope=project.name, run=project.name, round=n)
            if out["lessons"]:
                ctx.log("memory_add", count=len(out["lessons"]), lessons=out["lessons"])
            if out["verdict"] == "rework" and out["redesign"] and n < max_reviews:
                merged: dict[str, str] = {}
                for r in out["redesign"]:
                    merged[r["function_id"]] = (merged.get(r["function_id"], "") + "\n" + r["instruction"]).strip()
                out["rework"] = {fid: {"version": project.next_version(fid), "instruction": text} for fid, text in merged.items()}
            project.write(f"reviews/i{iteration}.{n}.json", out)
            note = f"rework: {', '.join(out['rework'])}" if out.get("rework") else f"{out['verdict']}, {len(out['emergent_effects'])} emergent effect(s) noted"
            return TaskResult(out, spawn=review_spawn(n, validate_id)(out), note=note)
        return run

    def review_spawn(n: int, validate_id: str):
        """What a review adds to the plan, from its saved output, so a resumed run adds the same tasks."""
        def spawn(out: dict) -> list[Task]:
            me = f"{P}review.{n}"
            if out.get("rework"):
                builds = [build_task(fid, r["version"], [me], r["instruction"], f"review round {n}") for fid, r in out["rework"].items()]
                check = Task(f"{P}verify.r{n + 1}", verify(n + 1, False), deps=[b.id for b in builds], respawn=verify_spawn(n + 1, False))
                again = Task(f"{P}validate.{n + 1}", validate(n + 1), deps=[check.id])
                nxt = Task(f"{P}review.{n + 1}", review(n + 1, again.id), deps=[again.id], kind="agent", model=se_tier,
                           gate=review_gate, max_attempts=3, respawn=review_spawn(n + 1, again.id))
                return builds + [check, again, nxt]
            return finish(me, validate_id)
        return spawn

    def review_gate(out: dict, ctx: Context) -> list[str]:
        known = sorted(project.registry())
        return [f"redesign names unknown function '{r['function_id']}'. Use one of: {known}"
                for r in out["redesign"] if r["function_id"] not in known]

    def tail(n: int, deps: list[str]) -> list[Task]:
        v = Task(f"{P}validate.{n}", validate(n), deps=deps)
        return [v, Task(f"{P}review.{n}", review(n, v.id), deps=[v.id], kind="agent", model=se_tier, gate=review_gate, max_attempts=3, respawn=review_spawn(n, v.id))]

    # ---- 9. inputs ---------------------------------------------------------------------------
    def inputs(validate_id: str):
        def run(ctx: Context) -> TaskResult:
            v = ctx.inputs[validate_id]
            spec_now = project.interfaces()
            structure = spec_now.get("structure") or []
            needs = {c["function_id"]: {"on_part": engine.contract_part(c, structure),
                                        "inputs": [f"{t['name']} on {t.get('part') or engine.contract_part(c, structure)}" + (f" = {t['aggregate']} of {t.get('of') or t['name']} over {t.get('part')} (computed, do not set)" if t.get("aggregate") else "")
                                                   for t in c["inputs"]]} for c in spec_now["contracts"]}
            prompt = (f"The person's request:\n{project.requests()[0]}\n\nSpecification:\n{j(project.spec())}\n"
                      f"The structure (part definitions, parents, subtypes, multiplicities):\n{j(structure)}\n"
                      f"The default usages, one per required part, as path and part rows. Replace them with what the request describes:\n{j(engine.rows_from_tree(engine.default_usages(structure)))}\n"
                      f"Calcs, the part each runs on, and the inputs each takes:\n{j(needs)}\nExchange expressions:\n{j(spec_now['exchanges'])}\n"
                      f"Provisional values and who supplied them:\n{j({k: [v['values'][k], v['tags'].get(k, 'ai')] for k in v['values']})}\n"
                      f"What the described design does today:\n{j(v['integration']['findings'])}\n{j(v['results'])}")
            if ctx.feedback:
                prompt += "\nYour last answer failed these checks. Fix them:\n" + "\n".join(ctx.feedback)
            out = run_agent("studio.inputs", system=INPUTS_SYSTEM, prompt=prompt, output=DesignInputs, tier="balanced", log=ctx.log)
            usages = engine.tree_from_rows(out["baseline"]["usages"]) or engine.default_usages(structure)
            targets = {x["target_input"] for x in spec_now["exchanges"]}
            aggregated = {t["name"] for c in spec_now["contracts"] for t in c["inputs"] if t.get("aggregate") and (t.get("of") or t["name"]) != t["name"]}   # a sum has its own name; a mean shares the attribute's
            computed = targets | aggregated                     # exchanged and aggregate values are computed, never entered
            base = {"name": "baseline", "description": out["baseline"]["description"], "source": "matches the request", "usages": usages,
                    "values": {k: val for k, val in v["values"].items() if k not in computed}, "tags": {k: v["tags"].get(k, "ai") for k in v["values"] if k not in computed}, "differs": []}
            for item in out["baseline"]["values"]:
                if item["name"].rsplit(".", 1)[-1] not in computed:
                    engine.set_value(base, structure, item["name"], item["value"], tag=item["source"])
            engine.place_values(base, spec_now)                 # a value lands on the usage that carries it
            project.write("inputs/baseline.json", base)
            for alt in out["alternatives"]:
                design = {"name": alt["name"], "description": alt["description"], "source": "ai alternative", "usages": engine.tree_from_rows(alt["usages"]) or usages,
                          "values": dict(base["values"]), "tags": dict(base["tags"]), "differs": []}
                for item in alt["values"]:
                    if item["name"].rsplit(".", 1)[-1] not in computed:
                        design["differs"] += engine.set_value(design, structure, item["name"], item["value"], tag="ai")
                design["differs"] = sorted(set(design["differs"]))
                engine.place_values(design, spec_now)
                project.write(f"inputs/{alt['name']}.json", design)
            return TaskResult(out, note=f"baseline plus {len(out['alternatives'])} alternative(s)")

        def gate(out: dict, ctx: Context) -> list[str]:
            interfaces_ = project.interfaces()
            known = {t["name"] for c in interfaces_["contracts"] for t in c["inputs"]} | {t["of"] for c in interfaces_["contracts"] for t in c["inputs"] if t.get("of")}
            known |= {n for x in interfaces_["exchanges"] for n in runtime.names_in(x["expression"])}
            errors = []
            for name, design in project.designs().items():             # every design must fit the metamodel: parts, counts, and inputs in scope
                errors += [f"{name}: {e}" for e in engine.instance_errors(interfaces_, design)]
            errors += [f"alternative {a['name']} sets '{i['name']}', which no function or exchange uses"
                       for a in out["alternatives"] for i in a["values"] if i["name"].rsplit(".", 1)[-1] not in known]
            return errors
        return run, gate

    # ---- 10. evaluate and render ---------------------------------------------------------------
    def evaluate(ctx: Context) -> TaskResult:
        summary = {}
        for name, design in project.designs().items():
            result = runtime.evaluate_design(project, design)
            project.write(f"outputs/{name}/results.json", result)
            summary[name] = {"passes": result["passes"], "findings": [f["rule"] for f in result["findings"]], "errors": result["errors"]}
        return TaskResult(summary, note="; ".join(f"{n}: {'passes' if s['passes'] else ', '.join(s['findings'] or list(s['errors']))}" for n, s in summary.items()))

    def render_outputs(ctx: Context) -> TaskResult:
        written = []
        for name, design in project.designs().items():
            result = project.read(f"outputs/{name}/results.json")
            project.path("outputs", name, "design.sysml").write_text(render.sysml(project, design, result), encoding="utf-8")
            project.path("outputs", name, "report.html").write_text(render.report(project, design, result), encoding="utf-8")
            written.append(f"outputs/{name}/")
        project.snapshot(iteration)
        project.log_change({"kind": "iteration", "designs": sorted(project.designs()), "harness": project.harness_version(),
                            "active": {fid: e["active"] for fid, e in project.registry().items()}})
        return TaskResult(written, note=f"report and SysML v2 for {len(written)} design(s)")

    def finish(review_id: str, validate_id: str) -> list[Task]:
        tasks, deps = [], [review_id]
        if iteration == 1:
            run, gate = inputs(validate_id)
            tasks.append(Task("inputs", run, deps=[review_id, validate_id], kind="agent", model="balanced", gate=gate, max_attempts=3))
            deps = ["inputs"]
        tasks.append(Task(f"{P}evaluate", evaluate, deps=deps))
        tasks.append(Task(f"{P}render", render_outputs, deps=[f"{P}evaluate"]))
        return tasks

    # ---- follow-up: triage decides what changes ---------------------------------------------------
    def triage(ctx: Context) -> TaskResult:
        project.checkpoint(iteration)                        # a follow-up changes the spec, evidence, and functions: undoable
        registry, spec = project.registry(), project.spec()
        functions = {fid: json.loads((project.function_dir(fid, e["active"]) / "manifest.json").read_text(encoding="utf-8"))["parameters"]
                     for fid, e in registry.items()}
        evidence = {e["capability"]: dict(zip(("measures", "cases"), project.evidence(e["capability"]))) for e in registry.values()}
        last = {n: project.read(f"outputs/{n}/results.json", {}).get("findings", []) for n in project.designs()}
        prompt = (f"The follow-up:\n{project.requests()[-1]}\n\nCurrent specification:\n{j(spec)}\n"
                  f"Functions and their inputs:\n{j(functions)}\nEvidence:\n{j(evidence)}\nFindings on each design today:\n{j(last)}")
        if ctx.feedback:
            prompt += "\nYour last answer failed these checks. Fix them:\n" + "\n".join(ctx.feedback)
        out = run_agent(f"studio.{P}triage", system=TRIAGE_SYSTEM, prompt=prompt, output=ChangePlan, tier="balanced", log=ctx.log)
        capability_ids = {e["capability"]: fid for fid, e in registry.items()}
        for r in out["functions_to_revise"]:                 # an agent may name the element instead of the identifier
            r["function_id"] = capability_ids.get(r["function_id"], r["function_id"])
        bad = [r["function_id"] for r in out["functions_to_revise"] if r["function_id"] not in registry]
        bad += [c["capability"] for c in out["new_measures"] + out["new_cases"] + out["retire_cases"] if c["capability"] not in capability_ids]
        if bad:
            out["unknown"] = bad
            return TaskResult(out, note="plan names something that does not exist")
        # apply the plan: parameters, requirements, evidence. Every change is tagged and versioned.
        by_name = {p["name"]: p for p in spec["parameters"]}
        for p in out["parameter_changes"]:
            by_name[p["name"]] = {**p, "changed_in": iteration}
        spec["parameters"] = list(by_name.values())
        by_id = {r["id"]: r for r in spec["requirements"]}
        for r in out["requirement_changes"]:
            old = by_id.get(r["id"])
            by_id[r["id"]] = {"id": r["id"], "text": r["text"], "source": "user", "capability": r["capability"],
                              "version": (old["version"] + 1) if old else 1, "since": old["since"] if old else iteration,
                              **({"previous": old["text"]} if old else {})}
        spec["requirements"], spec["iteration"] = list(by_id.values()), iteration
        project.write("spec.json", spec)
        touched = sorted({c["capability"] for c in out["new_measures"] + out["new_cases"] + out["retire_cases"]})
        for cap in touched:
            ctxt = project.context(cap)
            ctxt["measures"] += [{**m["row"], "_source": "user", "_since": iteration} for m in out["new_measures"] if m["capability"] == cap]
            retired = [c["inputs"] for c in out["retire_cases"] if c["capability"] == cap]
            ctxt["cases"] = [c for c in ctxt["cases"] if c["inputs"] not in retired]
            ctxt["cases"] += [{"inputs": c["inputs"], **({"expect": c["expect"]} if not c["raises"] else {}), "raises": c["raises"],
                               "source": "user", "since": iteration} for c in out["new_cases"] if c["capability"] == cap]
            project.save_context(cap, ctxt)
        structure = project.interfaces().get("structure") or []
        for design_name, design in project.designs().items():           # the person's new values and parts reach every design
            for p in out["parameter_changes"]:
                engine.set_value(design, structure, p["name"], p["value"], tag=p["source"])
            for change in out.get("usage_changes", []):
                apply_usage_change(design, structure, change, iteration)
            if (out["parameter_changes"] or out.get("usage_changes")) and f"request {iteration}" not in design["description"]:
                design["description"] += f" Updated by request {iteration}: " + ", ".join([f"{p['name']} = {p['value']}" for p in out["parameter_changes"]] +
                                                                                         [f"{c['action']} {c.get('part') or c['path']}" for c in out.get("usage_changes", [])]) + "."
            project.write(f"inputs/{design_name}.json", design)
        project.log_change({"kind": "follow-up", "summary": out["summary"], "parameters": [p["name"] for p in out["parameter_changes"]],
                            "usages": [f"{c['action']} {c.get('name') or c['path']}" for c in out.get("usage_changes", [])],
                            "requirements": [f"{r['id']} {r['action']}" for r in out["requirement_changes"]],
                            "evidence": {cap: project.context(cap)["version"] for cap in touched},
                            "revise": [r["function_id"] for r in out["functions_to_revise"]]})
        out["revise"] = {r["function_id"]: {"version": project.next_version(r["function_id"]), "instruction": r["instruction"]} for r in out["functions_to_revise"]}
        builds = triage_spawn(out)
        return TaskResult(out, spawn=builds,
                          note=f"{len(out['parameter_changes'])} parameter(s), {len(out['requirement_changes'])} requirement(s), "
                               f"{len(out['new_measures']) + len(out['new_cases'])} evidence item(s), {len(out['revise'])} function(s) to revise")

    def triage_spawn(out: dict) -> list[Task]:
        me = f"{P}triage"
        builds = [build_task(fid, r["version"], [me], r["instruction"], f"follow-up {iteration}") for fid, r in out.get("revise", {}).items()]
        return builds + [Task(f"{P}verify.1", verify(1, True), deps=[me] + [b.id for b in builds], respawn=verify_spawn(1, True))]

    def triage_gate(out: dict, ctx: Context) -> list[str]:
        return [f"The plan names things that do not exist: {out['unknown']}. Use these function identifiers: {sorted(project.registry())}. "
                f"Use these capability names: {sorted(e['capability'] for e in project.registry().values())}"] if out.get("unknown") else []


    # ---- drill-down: one part decomposed further, reviewed, then re-verified ---------------------------------
    def plan_drilldown(part_name: str, guidance: str) -> list[Task]:
        spec0 = project.interfaces()
        by_name0 = {p["name"]: p for p in spec0.get("structure", [])}
        if part_name not in by_name0:
            raise ValueError(f"'{part_name}' is not a part of this {building}. Known parts: {sorted(by_name0)}")
        target = by_name0[part_name]
        cap = target["capability"]
        contract = next(c for c in spec0["contracts"] if c["capability"] == cap)
        fid = contract["function_id"]
        who = project.specialist(cap)
        depth_here = part_depth(part_name, by_name0)
        room = max_depth - depth_here

        def subtree(parts: list[dict]) -> list[str]:
            names, frontier = [part_name], [part_name]
            while frontier:
                nxt = [p["name"] for p in parts if p["parent"] in frontier or p["specializes"] in frontier]
                nxt = [n for n in nxt if n not in names]
                names += nxt; frontier = nxt
            return names

        def merged(d: dict) -> dict:
            """The interface spec as it would be with this decomposition applied. A copy; nothing is written."""
            out = json.loads(json.dumps(spec0))
            out["structure"] += [{**p, "added_in": iteration} for p in d["parts"]]
            c = next(c for c in out["contracts"] if c["function_id"] == fid)
            c["inputs"] += [{"name": a["name"], "unit": a["unit"], "description": a["description"], "part": a["part"], "optional": False,
                             "example": a["example"], "example_rationale": a["rationale"], "added_by": f"drill-down {iteration}"} for a in d["attributes"]]
            return out

        def decompose(k: int):
            def run(ctx: Context) -> TaskResult:
                project.checkpoint(iteration)                 # so the whole request can be rolled back
                snapshot = shared.read()
                terms = {n: f"{t['unit']}  ({t['description']})" for n, t in snapshot["terms"].items()}
                inside = subtree(spec0["structure"])
                prompt = (f"The part to decompose: {part_name}\n{j(target)}\n\nWhat is already inside it:\n{j([by_name0[n] for n in inside if n != part_name]) or 'nothing yet'}\n"
                          f"It sits {depth_here} level(s) below the whole. You may add {room} more level(s).\n\n"
                          f"The function that assesses it, {fid}, and its contract:\n{j(contract)}\n"
                          f"How the library says this assessment is done:\n{project.process(cap).get('process_example', '') or contract.get('method', '')}\n\n"
                          f"Shared dictionary, version {snapshot['version']}:\n{yaml.safe_dump(terms) if terms else 'empty'}")
                if guidance.strip():
                    prompt += f"\n\nWhat the person asked for:\n{guidance.strip()}"
                if k > 1:
                    prev = ctx.inputs[f"{P}decompose.review.{k - 1}"]
                    prompt += f"\n\nThe systems engineer reviewed your last proposal and asked for rework:\n{prev['instruction']}\nYour last proposal:\n{j(ctx.inputs[f'{P}decompose' + ('' if k == 2 else f'.{k - 1}')])}"
                if ctx.feedback:
                    prompt += "\n\nYour last answer failed these checks. Fix them:\n" + "\n".join(ctx.feedback)
                tier = ctx.overrides.get("tier") or who.get("tier", "balanced")
                key = "studio.decompose" + ("" if k == 1 else f".{k}")       # offline fixtures do not depend on the iteration
                out = run_agent(key, system=DECOMPOSE_SYSTEM.format(building=building, specialist=who["text"]), prompt=prompt,
                                output=Decomposition, tier=tier, log=ctx.log)
                out.update(part=part_name, capability=cap, function_id=fid)
                return TaskResult(out, note=f"{len(out['parts'])} new part(s), {len(out['attributes'])} new attribute(s) under {part_name}")

            def gate(out: dict, ctx: Context) -> list[str]:
                if not out["parts"]:
                    return [f"Name at least one part inside {part_name}. If it has no meaningful parts, say so in summary and name one concrete constituent."]
                new = {p["name"] for p in out["parts"]}
                errors = [f"part '{p['name']}' already exists" for p in out["parts"] if p["name"] in by_name0]
                for p in out["parts"]:
                    if p["parent"] not in new | {part_name} and not (p["specializes"] and not p["parent"]):
                        errors.append(f"part '{p['name']}' has parent '{p['parent']}'. New parts go inside {part_name} or inside another new part.")
                    if p["capability"] != cap:
                        errors.append(f"part '{p['name']}' is assigned to '{p['capability']}'. Parts inside {part_name} are assessed by {cap}.")
                spec_after = merged(out)
                errors += structure_check(spec_after["structure"], spec_after["contracts"], building, max_depth)
                inside = set(subtree(spec_after["structure"]))
                have = {t["name"] for t in contract["inputs"] + contract["outputs"]}
                snapshot = shared.read()["terms"]
                for a in out["attributes"]:
                    if a["part"] not in inside:
                        errors.append(f"attribute '{a['name']}' names part '{a['part']}', which is not {part_name} or inside it")
                    if a["name"] in have:
                        errors.append(f"attribute '{a['name']}' is already an input or output of {fid}")
                    if a["name"] in snapshot and _unit(snapshot[a["name"]]["unit"]) != _unit(a["unit"]):
                        errors.append(f"'{a['name']}' is already defined with unit {snapshot[a['name']]['unit']} ({snapshot[a['name']]['description']}). You used {a['unit']}. Use the existing meaning or another name.")
                    if not re.fullmatch(r"[a-z][a-z0-9_]*", a["name"]):
                        errors.append(f"attribute name '{a['name']}' must be snake_case")
                names = [a["name"] for a in out["attributes"]]
                errors += [f"attribute '{n}' is listed twice" for n in sorted({n for n in names if names.count(n) > 1})]
                if out["attributes"] and not out["method_change"].strip():
                    errors.append("You added attributes but method_change is empty. Say how the function should use them.")
                return errors
            return run, gate

        def decompose_review(k: int):
            def run(ctx: Context) -> TaskResult:
                proposal = ctx.inputs[f"{P}decompose" + ("" if k == 1 else f".{k}")]
                memory = open_memory()
                shared_pool = H.get("memory", "project+shared") != "project"
                query = f"decomposing {part_name} of a {building}: {cap} parts, attributes, multiplicity"
                lessons = memory.search(query, scope=project.name, shared=shared_pool)
                ctx.log("memory_search", hits=len(lessons), query=query, lessons=lessons)
                prompt = (f"Review {k} of {max_reviews}. Part: {part_name}, assessed by {fid}.\nLessons:\n{j(lessons)}\n"
                          f"The structure today:\n{j(spec0['structure'])}\nThe contract today:\n{j(contract)}\n"
                          f"The proposal:\n{j({k2: proposal[k2] for k2 in ('summary', 'parts', 'attributes', 'method_change', 'notes')})}\n"
                          f"Depth allowed below the whole: {max_depth}. The part is at level {depth_here}.")
                if guidance.strip():
                    prompt += f"\nWhat the person asked for:\n{guidance.strip()}"
                if ctx.feedback:
                    prompt += "\nYour last answer failed these checks. Fix them:\n" + "\n".join(ctx.feedback)
                out = run_agent(f"studio.decompose.review.{k}", system=DECOMPOSE_REVIEW_SYSTEM.format(instructions=se_text), prompt=prompt,
                                output=DecompositionReview, tier=se_tier, tools=memory_tools(memory, scope=project.name, shared=shared_pool), log=ctx.log)
                for lesson in out["lessons"]:
                    memory.add(lesson, scope=project.name, run=project.name, part=part_name)
                if out["lessons"]:
                    ctx.log("memory_add", count=len(out["lessons"]), lessons=out["lessons"])
                if out["verdict"] == "rework" and k < max_reviews:
                    out["again"] = k + 1
                elif out["verdict"] == "accept":
                    out["applied"] = apply(proposal)
                project.write(f"reviews/i{iteration}.decompose.{k}.json", out)
                note = (f"rework: {out['instruction'][:80]}" if out.get("again") else
                        f"accepted: {len(proposal['parts'])} part(s) under {part_name}" + (f", {fid} to {out['applied']['version']}" if out.get("applied", {}).get("version") else "")
                        if out.get("applied") else f"rejected after {k} review(s): {part_name} is unchanged")
                return TaskResult(out, spawn=drill_spawn(k)(out), note=note)

            def gate(out: dict, ctx: Context) -> list[str]:
                return ["verdict is rework but instruction is empty. Say what to change."] if out["verdict"] == "rework" and not out["instruction"].strip() else []
            return run, gate

        def apply(proposal: dict) -> dict:
            """The accepted decomposition enters the interface spec, the designs, and the shared dictionary. Versioned."""
            with project.lock:
                spec_now = merged(proposal)
                spec_now["structure"] = project.interfaces().get("structure", []) + [p for p in spec_now["structure"] if p.get("added_in") == iteration]
                live = project.interfaces()                   # keep inputs other versions added meanwhile
                for c_live, c_new in zip(live["contracts"], spec_now["contracts"]):
                    known = {t["name"] for t in c_live["inputs"]}
                    c_new["inputs"] = c_live["inputs"] + [t for t in c_new["inputs"] if t["name"] not in known]
                spec_now["version"] = live.get("version", 1) + 1
                project.write("interfaces.json", spec_now)
            structure_now = project.interfaces().get("structure") or []
            for name, design in project.designs().items():    # every design gets the new parts under each usage of the decomposed part, and the examples on them
                design["usages"] = design.get("usages") or engine.default_usages(spec0.get("structure") or [])
                index = engine.usage_index(design, structure_now)
                for path in engine.usages_of(part_name, index, engine.by_name(structure_now)):
                    have = {u["part"] for u in index[path]["usage"].get("parts", [])}
                    index[path]["usage"].setdefault("parts", []).extend(u for u in engine.default_children(part_name, structure_now) if u["part"] not in have)
                for a in proposal["attributes"]:
                    for key in engine.set_value(design, structure_now, a["name"], a["example"], part=a["part"]):
                        design["tags"].setdefault(key, "ai")
                project.write(f"inputs/{name}.json", design)
            shared.propose(cap, [{"name": a["name"], "unit": a["unit"], "description": a["description"]} for a in proposal["attributes"]])
            project.log_change({"kind": "drill-down", "part": part_name, "parts": [p["name"] for p in proposal["parts"]],
                                "attributes": [a["name"] for a in proposal["attributes"]], "function_id": fid})
            version = project.next_version(fid) if proposal["attributes"] else ""
            return {"version": version, "attributes": [a["name"] for a in proposal["attributes"]], "method_change": proposal["method_change"],
                    "parts": [p["name"] for p in proposal["parts"]]}

        def drill_task(k: int) -> list[Task]:
            run, gate = decompose(k)
            run_r, gate_r = decompose_review(k)
            did = f"{P}decompose" + ("" if k == 1 else f".{k}")
            deps = [] if k == 1 else [f"{P}decompose.review.{k - 1}"]
            return [Task(did, run, deps=deps, kind="agent", model=who.get("tier", "balanced"), gate=gate, max_attempts=3),
                    Task(f"{P}decompose.review.{k}", run_r, deps=[did], kind="agent", model=se_tier, gate=gate_r, max_attempts=3, respawn=drill_spawn(k))]

        def drill_spawn(k: int):
            """What follows a review, from its saved output alone, so a resumed run adds the same tasks."""
            def spawn(out: dict) -> list[Task]:
                me = f"{P}decompose.review.{k}"
                if out.get("again"):
                    return drill_task(out["again"])
                applied = out.get("applied")
                if not applied:
                    return []
                tasks, deps = [], [me]
                if applied.get("version"):
                    instruction = (f"The decomposition of {part_name} added inputs {applied['attributes']} to this function's interface spec. "
                                   f"Take them as parameters with the examples as defaults. {applied['method_change']}")
                    tasks.append(build_task(fid, applied["version"], [me], instruction, f"drill-down {iteration}"))
                    deps = [tasks[-1].id]
                check = Task(f"{P}verify.r1", verify(1, False), deps=deps, respawn=verify_spawn(1, False))
                return tasks + [check, Task(f"{P}evaluate", evaluate, deps=[check.id]), Task(f"{P}render", render_outputs, deps=[f"{P}evaluate"])]
            return spawn

        return drill_task(1)
    # ---- the plan for this iteration ------------------------------------------------------------
    if iteration > 1:
        if not project.built:
            raise ValueError(f"The first build of '{project.name}' did not finish, so a follow-up has nothing to change. Run the first build again.")
        meta = project.request_meta(iteration)
        if meta.get("kind") == "drilldown":
            return plan_drilldown(str(meta.get("part", "")), project.requests()[iteration - 1])
        return [Task(f"{P}triage", triage, kind="agent", model="balanced", gate=triage_gate, max_attempts=3, respawn=triage_spawn)]

    def fan_out_spawn(function_ids: list[str]) -> list[Task]:
        builds = [build_task(fid, "v1", ["interfaces"]) for fid in function_ids]
        return builds + tail(1, [b.id for b in builds])

    def fan_out(ctx: Context) -> TaskResult:
        """The interface spec decides which specialists are needed. The harness adds their tasks to the plan."""
        ids = [c["function_id"] for c in ctx.inputs["interfaces"]["contracts"]]
        return TaskResult(ids, spawn=fan_out_spawn(ids), note=f"{len(ids)} specialist agents, in parallel")

    return [Task("parameterize", parameterize, kind="agent", model="balanced", gate=parameterize_gate, max_attempts=3),
            Task("interfaces", interfaces, deps=["parameterize"], kind="agent", model=se_tier, gate=interfaces_gate, max_attempts=4),
            Task("assign", fan_out, deps=["interfaces"], respawn=fan_out_spawn)]
