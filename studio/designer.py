"""Design the harness itself. An operational scenario becomes a team of Strands agent specs.

A person describes how the work is done today: who does what, in what order, and what they hand
to each other. An agent proposes one specialist per element. The person edits the specs, adds or
removes agents, and the studio writes them to the files a build uses:

  harness.yaml            what is being built, the agents, the rules every function follows
  agents/<name>.md        one instruction file per agent, sent to that agent as its system prompt
  tools/<name>.py         one file per tool. The person defines them and maps them to agents

The systems engineer is not optional and is not edited here. It always writes the interfaces,
always reviews, and always has long-term memory.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from agents.config import MODELS

from . import tooling
from .project import KITS, PROJECTS, Project

SYSTEMS_ENGINEER = """# Lead systems engineer

You act as the systems engineer and nothing else. You hold the whole system in view. The
specialists each see one element. You always write the interface specification, you always
review what the specialists build, and you keep long-term memory of lessons across programs.

## Frame of reference

You are generally familiar with ISO/IEC/IEEE 15288, system life cycle processes. Use its
technical processes as your frame, without claiming conformance to the standard:

- stakeholder needs and requirements definition, then system requirements definition
- architecture definition and design definition
- system analysis
- integration, verification, and validation

In this studio the request and the parameterized specification are requirements definition. Your
interface specification is architecture definition. The specialists do design definition and
implementation. The gates and the integration pass are verification. Your review, the emergent
effects analysis, and the person's judgment are validation.

## How you work

- Decompose by what must be assessed, and give each assessment to one element.
- Name things once. A name means one thing across every interface.
- Write every exchange between elements as arithmetic on named values. Exchanges are where
  cross-element effects come from, so be explicit about them.
- Keep requirements traceable: every requirement names the element that verifies it.
- When you review, read each schema against the non-functional requirements yourself. Tests do not check everything.
- A design instance that fails a requirement is not a defect in a function. Say which it is.
- Record lessons that would help the next program.
"""

GENERIC = """# Domain specialist

You design one assessment function for a capability that has no dedicated specialist yet.

## How you work

- Follow the method in the interface spec.
- With no measures, every constant you need becomes a parameter with a stated default.
- Say plainly in the design notes that no human evidence backs this function yet.

## Pre-written tools and objects

- `toolbox.interpolate`, `toolbox.lookup`, `toolbox.require`, `toolbox.finding`
"""

DESIGNER_SYSTEM = """You design a team of specialist agents from an operational scenario.
The scenario describes how people do a kind of engineering work today. Your job is to propose the
specialists whose functions, together, would let a build system assess any design of that kind.
- One specialist per element of the work that has its own method and its own numbers.
- Do not propose a systems engineer, reviewer, or coordinator. One is always present.
- Do not propose more specialists than the scenario supports. Three to seven is typical.
- Write each specialist's working rules the way a senior person in that field would brief a new hire.
- Assign a tool to a specialist only when one in the list plainly fits its work. Never invent a tool name.
Tools this harness defines: {tools}
Model tiers available: {tiers}. Use a stronger tier only where the method is subtle."""

RULES = {
    "architecture": [
        "One Python module. Standard library only, plus the studio's toolbox.",
        "One entry point: evaluate(inputs: dict, measures: list[dict]) -> dict. The result includes a boolean named passes.",
        "One rule: check(inputs: dict, measures: list[dict]) -> list of findings. A finding is a dict with rule, severity, domains, message, evidence.",
        "One schema: PARAMETERS, a dict of input name to a one-line description that states the unit.",
        "Measures are data. They are passed in. Never copy them into the code.",
        "Shared vocabulary: every input and output name goes into one shared dictionary with its unit. A name means one thing across the whole build system.",
    ],
    "non_functional": [
        "Deterministic. The same inputs give the same outputs.",
        "No extrapolation. Raise ValueError outside the measured range.",
        "Units appear in every parameter description.",
        "Every finding carries the numbers that justify it as evidence.",
        "A reader can follow the function in one sitting.",
    ],
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:24] or "agent"


def agent_markdown(spec: dict) -> str:
    """One agent spec as the instruction file the agent receives."""
    tools = spec.get("tools") or []
    lines = [f"# {spec['capability'].replace('_', ' ').capitalize()} specialist", "", spec.get("role", "").strip(), "", "## How you work", ""]
    lines += [f"- {rule}" for rule in spec.get("how_you_work", [])]
    lines += ["", "## Pre-written tools and objects", "", "- `toolbox.interpolate`, `toolbox.lookup`, `toolbox.require`, `toolbox.finding`"]
    lines += [f"- the tool `{t}` to sanity-check a number during design" for t in tools]
    lines += ["", "Edit this file to change how this specialist thinks. It is sent to the agent as part of its system prompt."]
    return "\n".join(lines) + "\n"


def generate(scenario: str, tools: list[dict] | None = None, log=None) -> dict:
    """Ask an agent for a team. Returns an editable draft. Nothing is written to disk.

    `tools` are the tools already defined in the draft being edited. The agent may map those to
    specialists. It cannot invent tools.
    """
    from agents.runner import run_agent
    from agents.schemas import HarnessDraft
    tools = [tooling.inspect_code(t.get("name", ""), t.get("code", "")) for t in tools or []]
    offered = ", ".join(f"{t['name']} ({t['doc']})" for t in tools if not t["error"]) or "none. Assign no tools"
    out = run_agent("studio.harness", system=DESIGNER_SYSTEM.format(tools=offered, tiers=", ".join(MODELS)),
                    prompt=f"Operational scenario:\n\n{scenario}", output=HarnessDraft, tier="deep", log=log)
    return to_draft(out, scenario, tools)


def to_draft(out: dict, scenario: str, tools: list[dict]) -> dict:
    known = {t["name"] for t in tools}
    specialists = [{**s, "tools": [t for t in s.get("tools", []) if t in known]} for s in out["specialists"]]
    return {"name": out["name"], "building": out["building"], "id_prefix": slug(out["id_prefix"]), "description": out["description"],
            "scenario": scenario, "notes": out.get("notes", []), "tools": tools,
            "agents": [{"capability": slug(s["capability"]), "tier": s.get("tier", "balanced"), "tools": s["tools"],
                        "instructions": agent_markdown(s)} for s in specialists]}


def folder_of(kind: str, name: str) -> Path:
    return (PROJECTS if kind == "project" else KITS) / name


def load(kind: str, name: str) -> dict:
    """An existing harness definition as an editable draft."""
    root = folder_of(kind, name)
    h = yaml.safe_load((root / "harness.yaml").read_text(encoding="utf-8"))
    text = lambda rel: (root / rel).read_text(encoding="utf-8") if rel and (root / rel).exists() else ""
    return {"name": h["name"], "building": h["building"], "id_prefix": h["id_prefix"], "description": h.get("description", ""),
            "structure_depth": int(h.get("structure_depth", 3)), "scenario": text("scenario.md"), "notes": [], "tools": tooling.list_tools(root),
            "agents": [{"capability": s["capability"], "tier": s.get("tier", "balanced"), "tools": s.get("tools", []),
                        "instructions": text(s.get("instructions", ""))} for s in h["agents"].get("specialists", [])]}


def save(kind: str, name: str, draft: dict) -> dict:
    """Write a draft to the files a build reads. Returns every path written.

    kind 'kit' writes a reusable harness definition under kits/. kind 'project' updates one project.
    Agents removed in the editor have their instruction files removed. The systems engineer file
    is always written from the studio's own text.
    """
    name = re.sub(r"[^a-z0-9\-]+", "-", name.lower()).strip("-") if kind == "kit" else name
    if not name:
        raise ValueError("Give the harness a name.")
    root = folder_of(kind, name)
    if kind == "project" and not root.exists():
        raise ValueError(f"No project named '{name}'.")
    (root / "agents").mkdir(parents=True, exist_ok=True)
    tool_files = tooling.save_tools(root, draft.get("tools", []))
    defined = {f.stem for f in tool_files}
    seen, specialists, written = set(), [], []
    for agent in draft.get("agents", []):
        cap = slug(agent.get("capability", ""))
        if cap in seen or cap in ("systems_engineer", "generic"):
            raise ValueError(f"Two agents cannot own the same capability: '{cap}'.")
        seen.add(cap)
        file = root / "agents" / f"{cap}.md"
        file.write_text(agent.get("instructions", "").rstrip() + "\n", encoding="utf-8")
        written.append(file)
        specialists.append({"capability": cap, "instructions": f"agents/{cap}.md", "tier": agent.get("tier", "balanced"),
                            "tools": [t for t in agent.get("tools", []) if t in defined]})
    for file in (root / "agents").glob("*.md"):                 # agents the person removed
        if file.stem not in seen | {"systems_engineer", "generic"}:
            file.unlink()
    for stem, text in (("systems_engineer", SYSTEMS_ENGINEER), ("generic", GENERIC)):
        (root / "agents" / f"{stem}.md").write_text(text, encoding="utf-8")
        written.append(root / "agents" / f"{stem}.md")
    existing = yaml.safe_load((root / "harness.yaml").read_text(encoding="utf-8")) if (root / "harness.yaml").exists() else {}
    harness = {"name": draft["name"], "building": draft["building"], "id_prefix": slug(draft["id_prefix"]), "description": draft.get("description", ""),
               "agents": {"parameterizer": {"tier": "balanced"},
                          "systems_engineer": {"tier": "deep", "instructions": "agents/systems_engineer.md", "memory": True},
                          "specialists": specialists, "fallback": {"instructions": "agents/generic.md", "tier": "balanced"},
                          "triage": {"tier": "balanced"}, "inputs": {"tier": "balanced"}},
               "review_rounds": existing.get("review_rounds", 2),
               "structure_depth": int(draft.get("structure_depth") or existing.get("structure_depth", 3)),
               "architecture": existing.get("architecture", RULES["architecture"]),
               "non_functional": existing.get("non_functional", RULES["non_functional"])}
    head = ("# A harness definition, written by GFDSB, the Ground & Form Design System Builder. Edit it here or in the studio's Harness view.\n"
            "# It says what is being built, which agents build it, and the rules every generated function must follow.\n\n")
    (root / "harness.yaml").write_text(head + yaml.safe_dump(harness, sort_keys=False, width=110), encoding="utf-8")
    written.insert(0, root / "harness.yaml")
    written += tool_files
    if draft.get("scenario", "").strip():
        (root / "scenario.md").write_text(draft["scenario"].strip() + "\n", encoding="utf-8")
        written.append(root / "scenario.md")
    if kind == "kit":
        for sub in ("library", "evidence"):
            (root / sub).mkdir(exist_ok=True)
            if not any((root / sub).iterdir()):
                (root / sub / "README.md").write_text("One YAML file per capability goes here. See kits/cubesat for the shape.\n", encoding="utf-8")
        if not (root / "example_request.md").exists():
            (root / "example_request.md").write_text(f"Describe the {draft['building']} you want here: what it must do, the numbers you know, "
                                                     "and what another program might change.\n", encoding="utf-8")
            written.append(root / "example_request.md")
    else:
        Project(name).log_change({"kind": "harness", "version": Project(name).harness_version(),
                                  "agents": [s["capability"] for s in specialists]})
    return {"name": name, "kind": kind, "root": str(root), "written": [str(p) for p in written]}
