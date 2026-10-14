"""Structured outputs. Agents fill these. Code reads them."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

class ToolSubmission(BaseModel):
    """What the toolsmith agent hands back: a function and the tests that prove it."""
    module_code: str = Field(description="Complete Python module source")
    test_code: str = Field(description="Complete pytest module source that imports from 'solution'")


class Term(BaseModel):
    name: str = Field(description="Exact name of an input or output, as used in the code")
    unit: str = Field(description="Unit symbol such as W, g, dB, m2. Use 'ratio' for a pure number, 'name' for a label, 'boolean' for true or false")
    description: str


class BuilderSubmission(BaseModel):
    """What the builder agent hands back: a piece of the CubeSat builder, designed from a human brief."""
    design_notes: str = Field(description="How this piece assesses any CubeSat: the method, the valid range, the assumptions")
    terms: list["Term"] = Field(description="Every input and output name the module uses, with its unit. These go into the shared dictionary")
    module_code: str = Field(description="Complete Python module source that meets the stated architecture")
    test_code: str = Field(description="Complete pytest module source that imports from 'solution'")


# ---- the design studio: request -> parameterized spec -> interfaces -> functions -> designs ----
Scalar = float | str | bool


class SpecParameter(BaseModel):
    name: str = Field(description="snake_case name with the unit as a suffix where it has one, for example array_area_m2")
    value: Scalar
    unit: str = Field(description="Unit symbol, or 'ratio', 'name', 'boolean'")
    source: Literal["user", "ai"] = Field(description="'user' when the person stated it. 'ai' when you filled a gap")
    parameterized: bool = Field(description="False only when the person said to keep it fixed")
    rationale: str = Field(description="Where the value came from. For an ai value, why it is a reasonable default")


class SpecRequirement(BaseModel):
    id: str = Field(description="R-001, R-002, ...")
    text: str = Field(description="A 'shall' statement")
    source: Literal["user", "ai"]
    capability: str = Field(description="The capability that verifies it")


class ParameterizedSpec(BaseModel):
    """A request turned into parameters. Nothing about the mission stays hard-coded unless the person said so."""
    mission_name: str
    summary: str
    parameters: list[SpecParameter]
    requirements: list[SpecRequirement]
    capabilities: list[str] = Field(description="Assessments this design needs, such as power, mass, link")
    parameterization_notes: list[str] = Field(description="How you applied the person's instructions on what to parameterize")
    open_questions: list[str] = Field(default=[], description="What the person should confirm")


class ContractTerm(BaseModel):
    name: str
    unit: str
    description: str
    part: str = Field(default="", description="The part this attribute describes, by name from the structure. For an aggregate input, the part whose usages are combined")
    optional: bool = False
    example: Scalar | None = Field(default=None, description="A value for this mission when the spec does not give one. It will be tagged ai")
    aggregate: Literal["", "sum", "max", "min", "mean", "count", "all", "any"] = Field(default="", description="For a roll-up input: how to combine `of` over the usages of `part` inside the calc's own part. Empty for a plain attribute")
    of: str = Field(default="", description="For an aggregate input: the attribute or output taken from each usage. Defaults to this input's own name")


class Contract(BaseModel):
    function_id: str = Field(description="Static identifier, for example cubesat.power.margin. It never changes across versions")
    capability: str
    part: str = Field(description="The part definition this calc belongs to, from the structure. It runs once per usage of that part")
    question: str = Field(description="The one question this function answers for any CubeSat")
    rule_id: str
    method: str = Field(description="The method in two or three sentences, for a capability the library does not describe")
    inputs: list[ContractTerm]
    outputs: list[ContractTerm]


class Exchange(BaseModel):
    id: str = Field(description="X-1, X-2, ...")
    source_element: str
    target_element: str
    target_input: str = Field(description="The input of the target element's function that this exchange supplies")
    expression: str = Field(description="Arithmetic on named values using + - * / and parentheses only. Each name is a specification parameter, an output of another element's function, another exchange's target, or listed in parameters")
    unit: str
    rationale: str
    parameters: list[ContractTerm] = Field(default=[], description="Every name in the expression that is not a specification parameter, a function output, or another exchange's target. Each needs a numeric example")


class PartDef(BaseModel):
    """One kind of part in the structural decomposition. Concrete nouns a domain engineer would name."""
    name: str = Field(description="PascalCase type name, for example SolarArray, WorkerNode, PersistentVolume")
    parent: str = Field(default="", description="The part this one is composed into. Empty for a subsystem directly under the whole")
    specializes: str = Field(default="", description="A more general part this one is a subtype of, if any. For example PersistentVolume specializes Volume")
    multiplicity: str = Field(default="1", description="How many per parent: 1, 0..1, 1..*, or a number")
    capability: str = Field(description="The element whose function assesses this part")
    description: str = ""


class InterfaceSpec(BaseModel):
    """The architecture as interfaces: a structural decomposition, a calc on every part that has something to assess, and what the parts exchange."""
    architecture: str = Field(description="The proposed architecture in a short paragraph")
    structure: list[PartDef] = Field(default=[], description="The parts of the system as a tree: subsystems, then the concrete things inside them, with subtypes where they exist")
    contracts: list[Contract] = Field(description="One calc per part definition that has something to assess. Several per capability is normal")
    exchanges: list[Exchange] = Field(description="source_element and target_element name part definitions")


class NewAttribute(BaseModel):
    """An attribute a finer decomposition adds. It becomes an input of the owning element's function."""
    name: str = Field(description="snake_case, one meaning across the whole system. Reuse a shared-dictionary name only with its unit")
    unit: str
    description: str
    part: str = Field(description="The part this attribute describes: the part being decomposed or one of the new parts")
    example: Scalar = Field(description="A value for the described instance. It will be tagged ai")
    rationale: str


class Decomposition(BaseModel):
    """A finer decomposition of one part: the concrete things inside it, and what the function now needs to know about them."""
    summary: str = Field(description="What is inside this part and why this breakdown, in two or three sentences")
    parts: list[PartDef] = Field(description="New parts only. Each parent is the part being decomposed or another new part. specializes may name an existing part")
    attributes: list[NewAttribute] = Field(default=[], description="New inputs the finer parts bring. Only what changes the assessment. Empty if the function already has what it needs")
    method_change: str = Field(default="", description="How the function should use the new attributes. Empty when there are none")
    notes: list[str] = Field(default=[])


class DecompositionReview(BaseModel):
    """The systems engineer's judgment of a proposed decomposition."""
    verdict: Literal["accept", "rework"]
    assessment: str
    instruction: str = Field(default="", description="What to change, when the verdict is rework")
    lessons: list[str] = Field(default=[], description="Reusable lessons about decomposing this kind of system")


class ExampleValue(BaseModel):
    name: str
    example: Scalar
    rationale: str = Field(description="Why this value is a reasonable default for the described instance")


class ExampleValues(BaseModel):
    """Example values for inputs the interface spec left without a source."""
    values: list[ExampleValue]


class Redesign(BaseModel):
    function_id: str
    instruction: str = Field(description="What to change and why")


class SystemReview(BaseModel):
    verdict: Literal["accept", "rework"]
    assessment: str
    emergent_effects: list[str] = Field(default=[], description="Cross-element behavior that no single specialist could see")
    redesign: list[Redesign] = Field(default=[])
    lessons: list[str] = Field(default=[], description="Reusable lessons for future programs")


class DesignValue(BaseModel):
    name: str
    value: Scalar
    source: Literal["user", "ai"]


class UsageRow(BaseModel):
    """One part usage in a design: a thing that exists, of a given part definition. The path names its place in
    the tree, so the list is flat (a tool schema cannot refer to itself) and the pipeline folds it into the tree."""
    path: str = Field(description="Dotted camelCase path from the top: eps, eps.panel1, cluster.appPool.node1. Each segment is unique among its siblings")
    part: str = Field(description="The part definition, or a subtype of the one the structure composes at that place")


class DesignInstance(BaseModel):
    name: str = Field(description="Short kebab-case name, for example baseline or alt-deployable-panels")
    description: str
    usages: list[UsageRow] = Field(default=[], description="Every part usage of this design, one row per thing that exists: three node pools are three rows, each node inside them a row. Parents come before their children. Empty for an alternative that keeps the baseline's")
    values: list[DesignValue] = Field(description="Values keyed by usage path and attribute, for example eps.panel1.panel_area_m2. A name with no path is global to the design")


class DesignInputs(BaseModel):
    """Inputs for the build system: the design the person described, and alternatives worth comparing."""
    baseline: DesignInstance
    alternatives: list[DesignInstance] = Field(description="Each lists only the values that differ from the baseline")
    notes: str = ""


class CaseChange(BaseModel):
    capability: str
    inputs: dict[str, Scalar]
    expect: dict[str, Scalar] = Field(default={}, description="Expected outputs. Empty when raises is true")
    raises: bool = False


class MeasureChange(BaseModel):
    capability: str
    row: dict[str, Scalar]


class RequirementChange(BaseModel):
    action: Literal["add", "revise"]
    id: str
    text: str
    capability: str


class UsageChange(BaseModel):
    """A change to the part usages of every design: something added, removed, or replaced by another part."""
    action: Literal["add", "remove", "replace"]
    path: str = Field(description="The usage path, for example eps.panel1. For add, the parent path, or empty for the top")
    part: str = Field(default="", description="For add or replace: the part definition of the new usage")
    name: str = Field(default="", description="For add or replace: the new usage's name")
    values: list[DesignValue] = Field(default=[], description="Values on the new usage, by attribute name")


class ChangePlan(BaseModel):
    """What a follow-up request changes. Only what is listed here is touched."""
    summary: str
    usage_changes: list[UsageChange] = Field(default=[], description="Changes to what exists in the design: a part added, removed, or replaced by another part")
    parameter_changes: list[SpecParameter] = Field(default=[], description="New or changed parameters. All are source 'user' unless you filled a gap")
    requirement_changes: list[RequirementChange] = Field(default=[])
    new_measures: list[MeasureChange] = Field(default=[])
    new_cases: list[CaseChange] = Field(default=[], description="Test results or worked cases the person supplied")
    retire_cases: list[CaseChange] = Field(default=[], description="Existing cases the new evidence contradicts. Match by inputs")
    functions_to_revise: list[Redesign] = Field(default=[], description="Functions whose method must change. Each gets a new version")
    notes: list[str] = Field(default=[])


# ---- designing the harness itself: an operational scenario becomes a team of agents ----
class AgentSpec(BaseModel):
    capability: str = Field(description="One lowercase word for the assessment this agent owns, for example power or storage")
    role: str = Field(description="One or two sentences: who this specialist is and what they are responsible for")
    how_you_work: list[str] = Field(description="Four to six working rules a senior specialist in this field would insist on")
    tier: str = Field(default="balanced", description="Model tier name, from the list offered")
    tools: list[str] = Field(default=[], description="Names of pre-written tools this agent should have, from the list offered")


class HarnessDraft(BaseModel):
    """A proposed harness definition: what is being built, and the specialist agents that will build its design system."""
    name: str = Field(description="Short name for the design system, for example 'CubeSat design system'")
    building: str = Field(description="What one design instance is called, for example CubeSat")
    id_prefix: str = Field(description="Short lowercase prefix for function identifiers, for example cubesat")
    description: str
    specialists: list[AgentSpec]
    notes: list[str] = Field(default=[], description="What you assumed about the scenario, and what the person should confirm")
