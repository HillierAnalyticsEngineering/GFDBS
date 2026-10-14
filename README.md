# GFDSB: the Ground & Form Design System Builder

Companion code for the INCOSE Cleveland-Northern Ohio chapter talk, *Agentic Harness Engineering:
A Systems Thinking Approach*. MIT License.

Describe a thing to build, in plain language. Strands agents parameterize the request, write the
metamodel (a structure of parts, with a calc on every part worth assessing), and write each calc as a
function, each under gates that code enforces. A systems
engineer agent with long-term memory reviews the result. What comes out is a build system: versioned
functions behind static identifiers, the evidence and tests behind each, and the SysML v2 spec they
generate for any instance. The build system runs with no model. A live interface shows the
orchestration, the build system, the sensitivity sweep, versions, and the generated model.

The working rule throughout: **agents propose structure, code does arithmetic and checking.**

## Setup (Windows PowerShell)

The short way:

```powershell
Expand-Archive agentic-authoring-demo.zip -DestinationPath .
cd agentic-authoring-demo
.\setup.ps1
.\.venv\Scripts\Activate.ps1
```

`setup.ps1` finds a Python 3.10 to 3.13 whose `ssl` module works, builds `.venv` from it,
installs the packages, creates `.env`, and runs the 26 tests. It rebuilds a `.venv` that was
made from a Python without working `ssl`. Then open `.env` in Notepad and add your
`ANTHROPIC_API_KEY`.

The same steps by hand:

```powershell
py -3.12 -m venv .venv             # use the py launcher, not a bare 'python'
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env        # then: notepad .env, and add your ANTHROPIC_API_KEY
python -m pytest -q                # 26 tests, no API calls
```

If PowerShell blocks a script, run this once in the same window and try again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

### Troubleshooting: "the ssl module in Python is not available"

pip prints this when the Python behind `.venv` cannot load OpenSSL. The usual cause is a
conda Python used outside an activated conda environment. Check with:

```powershell
python -c "import sys; print(sys.base_prefix)"
py -0p
```

Fix it by rebuilding the environment from a python.org interpreter. `setup.ps1` does this
for you. By hand:

```powershell
deactivate
Remove-Item -Recurse -Force .venv
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `py -0p` lists no python.org install, run `winget install Python.Python.3.12`, open a new
PowerShell window, and repeat. To stay on conda instead, use
`conda create -n harness python=3.12`, `conda activate harness`, and skip the `.venv` steps.

### Troubleshooting a live run

- **"Model stopped generating due to maximum token limit."** One agent wrote more than its
  output budget. The runner retries the same turn with twice the room (up to `MAX_TOKENS_CEILING`,
  64000) before it counts as a failed attempt; the console says `budget` when it does. The deep tier
  starts at `MAX_TOKENS_DEEP=32000`, since the interface spec with its structure is long; the others at
  `MAX_TOKENS=16000`. Set either in `.env`.
- **"X-1 computes 'name' from an expression that reads 'name' itself."** An exchange whose expression is
  just the target's own name is a pass-through, and it is allowed when that name is an output of the
  source element. The gate refuses it only when nothing produces the value, and says which outputs the
  source does have.
- **"RuntimeError: Event loop is closed" tracebacks.** These are cleanup noise from the HTTP
  client on Windows, not a failed task. The runner now closes each connection as soon as its
  response ends, which should stop them. Look for a `FAILED` line to find a real failure.
- **A run stopped partway.** Continue it without paying for finished tasks: **Resume the build** on
  the page, or `python studio.py resume --project NAME`. Finished tasks are kept.
- **"Downloading bytes" on the first memory command.** FastEmbed is fetching the embedding
  model from Hugging Face, about 70 MB, once. The `HF_TOKEN` warning can be ignored.

All commands in this README are written for PowerShell. On macOS or Linux, activate with
`source .venv/bin/activate`, copy with `cp`, and use forward slashes in paths.

## The demos, in talk order

| # | Command | Shows |
|---|---|---|
| 1 | `python demos\01_ask_vs_build.py` | Ask a model to count letters, then have an agent build a tested function that counts any letters in any string |
| 2 | `python demos\02_minimal_harness.py` | The harness alone: dependencies, parallel tasks, a gate with retry, a task that spawns a task. No model |
| 3 | `python demos\03_one_agent_one_task.py` | One Strands agent with a deterministic tool (the CubeSat kit's `link_budget`) and structured output, tied to one harness task whose gate recomputes the answer |
| 4 | `python demos\04_memory_agent.py "..."` | A Strands agent with mem0 long-term memory across separate processes |
| 5 | `python studio.py ui`, or `python studio.py new --offline` | GFDSB, the Ground & Form Design System Builder. A project defines a harness. A request becomes a versioned build system, and you use it through a live interface |

## Three levels, and where each part sits

| Level | The agent | The person | In this repo |
|---|---|---|---|
| 1 | Answers a question | Asks, then checks by hand | Demo 1, part A |
| 2 | Designs inside tools and schemas a person wrote | Writes the tools, schemas, and rules | Demo 3 |
| 3 | Designs the tools, schemas, and rules | Describes what they want, judges the general design, and supplies evidence | Demo 1 part B, `studio.py` |

## GFDSB: the Ground & Form Design System Builder

```powershell
python studio.py ui                      # the interface at http://localhost:8770. Everything below can be done there
python studio.py new --offline           # the CubeSat example request. Agents build a build system for it
python studio.py evaluate baseline       # run the design through the build system. No model call
python studio.py followup --offline      # the example follow-up: test it, then say what to change
python studio.py drilldown PowerBus      # decompose one part further: its specialist proposes, the SE reviews, the function is rebuilt
python studio.py rollback                # undo the latest request: model, routing, and designs as they were
python studio.py delete --project NAME   # move a project to projects/.trash (add --purge to remove it for good)
python studio.py route cubesat.radiation.dose v1     # point a static identifier at another version
python studio.py export                  # the build system as a standalone folder. No studio, no AI
```

The studio is not tied to CubeSats. A **project** defines a harness: what is being built, which
Strands agents build it, and the rules every generated function follows. You describe one
thing you want. The agents do not answer the request. They generalize it into a metamodel of
named parameters and a set of functions that assess any such thing. What you described becomes
one instance of that metamodel. The build system then runs with no AI in it. Agents are called
again only when you ask for something it cannot do yet.

### No code required

Three things used to need a text editor. The interface now does them.

| In the interface | What it does | What it writes |
|---|---|---|
| **Settings** | Save your Anthropic API key. Load the list of models your key can use from Anthropic (`GET /v1/models`). Map each model tier to a model, and add tiers of your own | `.env` in the repo folder. The key is never sent back to the page |
| **Projects** | The landing page. Every design system on this computer is a card: what it builds, its functions and versions, whether its first build finished, and where it lives. Open one and the page restores it, tabs and all, and remembers it across reloads. Start a new one from the same page: say what to build, pick the harness, and build. Projects is the only way between design systems, so each stays its own restore point |
| **Define** | Describe an operational scenario: how the work is done today. An agent proposes one specialist per element. Edit each spec, change its tier, add or remove agents, define tools and map them to agents, then write the files | `kits/<name>/harness.yaml`, `kits/<name>/agents/*.md`, and `kits/<name>/tools/*.py`, or the same files inside one project |
| **Describe what to build** | Type a request, pick a harness definition, and start a build or a follow-up | `projects/<name>/` |

Every element in the interface that is backed by a file shows its path on your computer. Click
the path to open your file browser with the file selected. The path is copied as well.

### A project defines the harness

| File | What it holds |
|---|---|
| `harness.yaml` | What one design is called, the function identifier prefix, the agents with their tiers and tools, the review cap, and the architecture and non-functional rules every function must follow |
| `tools/<name>.py` | One Python function per file that an agent may call during a live build. The function name matches the file name. It needs a docstring and type hints, because the model reads both. Write them in the Define view, press Check, then tick the tool on each agent that may call it |
| `agents/systems_engineer.md` | The systems engineer. It always writes the interfaces, always reviews, and always has mem0 long-term memory. It acts as a systems engineer and nothing else, with ISO/IEC/IEEE 15288 as its general frame. The studio writes this file. It is not optional |
| `agents/<capability>.md` | One instruction file per specialist. It is sent to that agent as part of its system prompt |
| `agents/generic.md` | The specialist used for a capability that has no agent of its own yet |
| `library/`, `evidence/` | How each assessment is done by hand, and the measures and worked cases the project starts with |

A harness definition under `kits/` is the reusable form. `kits/cubesat` ships with five
specialists, a library, evidence, and saved answers for offline runs. `kits/blank` has a generic
team for anything else. Editing a project's agents changes the harness version, and the
changelog records it.

```powershell
python studio.py kits
python studio.py harness --name my-team --file scenario.md      # the same as the Define view
python studio.py new --kit my-team --file my_request.md
```

### Model tiers

An agent spec names a tier. A tier names a model. The three built-in tiers are `fast`,
`balanced`, and `deep`. Add your own in Settings, or as `MODEL_<NAME>=<model id>` in `.env`.

### The ten steps

| Step | Task | Who | What it produces |
|---|---|---|---|
| 1 | request | You | The CubeSat you want, in your words |
| 2 | `parameterize` | Agent, balanced tier | A parameterized spec. Every value is tagged `user` or `ai`. Your instructions on what to parameterize win |
| 3 | `interfaces` | Systems engineer agent, deep tier | The architecture: a structural decomposition (subsystems, then the concrete parts inside them, with subtypes and multiplicities), a calc on every part definition that has something to assess (several per capability is normal; each runs once per usage), aggregate inputs that roll parts up, and the exchanges between parts. The SysML model nests part defs and part usages from this tree, with each calc def declared on its part |
| 3a | `interfaces` (second pass) | A balanced-tier agent fills an example value for any input the systems engineer left without one, tagged ai. The deep model designs the architecture; picking twenty defaults is not its job |
| 4 | `build.*` | One specialist agent per element, in parallel | A function, its schema, its rule, and tests. Each specialist has an instruction file in the project's `agents/` folder |
| 5 | `validate` | Code | Gates, integration through the router, and an emergent-effects pass |
| 6 | `review` | Systems engineer agent with long-term memory | Accept, or ask for redesign. Rounds are capped by `--max-reviews` |
| 7 | `build.*.vN` | Specialist agent | A new version of a redesigned function |
| 8 | `verify` | Code | Every active function still honors its interface spec and its evidence |
| 9 | `inputs` | Agent | A baseline design instance that matches your request, and alternatives |
| 10 | `evaluate`, `render` | Code | Results, a report, and a SysML v2 model per design |

### What a project holds

Everything lands in `projects/<name>/`, beside the harness files described above.

| Path | What it is |
|---|---|
| `requests/` | What you asked for, one file per request |
| `spec.json` | The parameterized spec and the requirements, each with a source and a version |
| `interfaces.json` | The structure, the calcs on their parts, and the exchange expressions. This is the metamodel |
| `context/` | Evidence per capability: measures and worked cases. It starts from the library in `context/` and grows with what you supply |
| `functions/<static id>/<version>/` | One generated function with its tests, acceptance tests, and manifest |
| `registry.json` | Static identifier to active version. Every call routes through it |
| `inputs/` | Design instances: `baseline` and the alternatives, each a usage tree with values keyed by usage path |
| `outputs/<design>/` | `results.json`, `report.html`, `design.sysml` |
| `changelog.json`, `history/` | What each request changed, snapshots of the spec, interfaces, and registry after each request, and `i<n>.before/` checkpoints that `rollback` restores |

### Elements are parts, calcs run per usage

An agent owns a capability; it does not stand for an element. The elements of the system are the part
definitions in the structure the systems engineer agent proposes, and a calc (a function contract) belongs
to one part definition and runs once per usage of it. A specialist writes as many calcs as its subsystem
has things worth assessing. A subsystem's calc rolls its parts up through aggregate inputs:
`aggregate: sum, of: panel_area_m2, part: SolarArray` arrives at the function as one number, the sum over
the panels inside that subsystem. `mean`, `max`, `min`, `count`, `all`, and `any` work the same way.

A design instance (`inputs/<name>.json`) is a tree of part usages, each naming its part definition (a
subtype where one fits), plus one flat dictionary of values keyed by usage path:
`eps.panel1.panel_area_m2`. Two panels are two usages; three node pools are three usages. A value with no
path is global to the design and applies to every usage that needs it. Resolving a name for a usage is one
rule, used everywhere: the usage's own value, then its ancestors', then the global, then the one usage
anywhere that carries it. Two carriers is an error that names them, which is what an aggregate is for.

`studio/engine.py` implements all of this with the standard library and travels with an export, so the
exported `run.py` evaluates a usage tree exactly as the studio does (`python run.py baseline
eps.panel1.panel_area_m2=0.05`). Every usage is checked against the metamodel before anything runs: an
unknown part, a part where the structure does not compose it, a count outside a multiplicity, or an
input no scope provides is a gate failure for the inputs agent, with the reason. A follow-up that adds,
removes, or swaps a part is a usage change; a replaced usage keeps its values unless the change states others.

### Static identifiers, versions, and routing

A function is addressed by an identifier such as `cubesat.power.margin`. The identifier never
changes. The registry maps it to one active version. A redesign or a follow-up adds `v2` beside
`v1` and moves the pointer. `python studio.py route <id> <version>` moves it back. Nothing else
changes, and the changelog records the swap. The interface can also run one evaluation against
another version without changing the registry.

### Exchanges, sensitivity, and interactions

The systems engineer agent writes exchanges as arithmetic expressions, for example
`load_avg_w = bus_load_w + tx_power_w * tx_duty_cycle / tx_efficiency`. Each name in an
expression gets its value from the specification, from another exchange, from the output of
another element's function, or from the exchange's own parameters with an example. The runtime
orders exchanges and function calls by those dependencies, so a transmitter choice reaches the
power margin and the bulk temperature. The sensitivity pass moves each input ten percent and records every function's margin: a
tornado chart per function. It then moves every pair of inputs together and compares the joint
change with the sum of the two single changes. The difference is a second-order interaction.
One between inputs of different elements is behavior no single specialist could see. The
reviewing agent reads the top sensitivities and the interactions before it accepts the build.

### Follow-ups

A follow-up changes a build system that exists. If a build stopped early, the studio refuses the
follow-up and says so. **Resume** (`python studio.py resume`) continues it: every task with a saved result
is kept, the task that failed runs again with fresh attempts, and the rest follows. **Start over**
(`python studio.py rebuild`) clears the project and runs request 1 from nothing. Any follow-up sent in
between moves to `requests/not_applied/`. A specialist that fails its own tests now gets its last
submission back with the failure, so it corrects the test or the function instead of starting again.

A follow-up runs triage first. An agent sorts what you said into parameter changes, requirement
changes, new measures, new worked cases, cases to retire, and functions whose method must
change. Then only the affected parts run.

- New evidence with an unchanged method: the function is re-verified by code. It is not rebuilt.
- A changed method: the specialist writes a new version. The old one stays in the registry.
- A changed requirement: its version goes up and the earlier text is kept.

### Drill-downs

The first decomposition goes as deep as `structure_depth` in harness.yaml allows (3 by default: a subsystem
is level 1). The Define view sets it; the interface gate holds the systems engineer to it. To go finer in one
place, hover a part in the drawn SysML definitions and choose **Drill down**, or run
`python studio.py drilldown <Part> "optional guidance"`. A drill-down is a request of its own and runs through
the orchestrator like any other:

1. `decompose`: the specialist that owns the part's capability names the concrete parts inside it, with
   multiplicities and subtypes, and only the attributes the assessment should start using. A gate holds it
   to the same rules as the first decomposition (a tree, real parents, within the depth limit, one meaning
   per name against the shared dictionary) and refuses parts that already exist.
2. `decompose.review`: the systems engineer agent judges it with its memory, accepts or sends it back with one
   instruction (review rounds are capped), and records a lesson if there is one.
3. On acceptance the parts and attributes enter `interfaces.json` as a new version, every design gets the
   attribute examples tagged `ai`, and the shared dictionary learns the terms.
4. If the function gained inputs it is rebuilt as a new version with the method change, then `verify`,
   `evaluate`, and `render` run as always. If not, it is re-verified and the spec is re-rendered.

Every request that changes the model (a follow-up or a drill-down) first writes a checkpoint to
`history/i<n>.before/`. **Roll back** in the Versions view, or `python studio.py rollback`, restores it:
interfaces, registry routing, spec, dictionary, evidence, and designs return to what they were, the outputs
are re-rendered, and the request moves to `requests/rolled_back/`. Versions the request built stay in the
registry, inactive, so an identifier can still be routed to them. Rollback goes one request at a time.

### The interface

`python studio.py ui` serves a React Flow interface. It has five views.

| View | What it shows |
|---|---|
| Define | An operational scenario, the agent specs generated from it, and an editor for the team |
| Harness | The team this project uses as a flow, each agent's instruction file, and the rules |
| Orchestration | Every task as a node in its step, live. Edges draw themselves in and carry a moving dot while a task runs. Click a node for every call in both directions: prompts, API requests and responses, tool calls, dictionary commits, gate output, and the result |
| Orchestration · while it runs | **Pause before next attempt** holds every task after its current attempt. Select an agent task for its controls: **Stop this task** ends it after the current attempt, and when the same check has failed twice the panel suggests **Retry on the next tier**, which runs its remaining attempts on a stronger model and, if you keep the box ticked, writes that tier into this project's harness.yaml. A task that keeps failing on something that is not a logic error in the spec is usually a model that is too small for it |
| Build system | The registry, the element functions, the exchanges, and the spec they write. Edit any input and generate. The trace animates through the router, the functions, and into design.sysml. Click an element for its schema, code, tests, and versions |
| Build system · Sweep | Sensitivity and interactions, no model call. First order: every numeric input moved ten percent down and up, drawn as a tornado chart per function, with pass flips. Click a bar to apply that move to the inputs. Second order: every pair moved together, against the sum of the single moves. What the sum misses is the interaction; a cross-element one is behavior no single specialist could see |
| Build system · SysML v2 | The build system is a set of functions whose output is the SysML v2 design spec. Generate the spec and the functions run through the registry, each one writing its part: literals for the inputs, computed values for the outputs, and whether the part satisfies its requirement. The spec lands at `outputs/<design>/design.sysml` in the project, and the exported `run.py` writes the same file. The SysML v2 tab shows it as textual notation or drawn in graphical notation, with a pop-out for a full-screen view. The functions themselves appear in the model as calc defs |
| SysML v2 · four drawn views | **Interconnection**: the design instance as part usages with their values, the exchanges between them, and which requirements each satisfies. **Definitions**: the metamodel as a tree of part defs with composition diamonds and specialization triangles; packages fold; hover a part to raise it and to drill down. **Interfaces**: each element as a block with input ports on the left and output ports on the right, and every exchange as a flow from the value it reads to the input it supplies, labelled with its expression. **Parametric**: every calc def as a constraint block, exchanges on the left and the functions on the right, bound to the given, exchanged, and computed values. Hover a block to isolate its bindings |
| SysML v2 · the model | One package per subsystem, holding its part defs (with specialization and multiplicities), its calc def (signature, rule, method, and where the body lives), and one calc def per exchange with the expression itself. The whole composes the subsystem roots. The instance nests part usages the same way, with literals on the part they describe; optional and subtype parts with nothing stated are left out of the instance. In the drawn definitions view, subsystems fold into packages when the tree is large; click a root to open or fold it |
| Memory | What the systems engineer agent remembers. Lessons are scoped: each project has its own, and a shared pool holds lessons about method that apply to every project. Before a review the agent recalls from this project and the pool; afterwards it records lessons in the project, or in the pool when it says a lesson holds everywhere. Set `memory: project` in harness.yaml to keep a project from reading the pool. Read, recall, add (to this project or to every project), or forget. From PowerShell: `python studio.py memory add "..." --project NAME`, or without `--project` for the pool |
| Versions | The changelog, the registry, and a line diff between two versions of a function |

The page can start a build or a follow-up itself. The built interface ships in `ui/dist`, so
nobody needs Node to run it. To change it: `cd ui; npm install; npm run build`.

### Checks that make it dependable

| Check | What it guards against | Who runs it |
|---|---|---|
| Spec and interface gates | Duplicate names, unit clashes, exchanges that reference nothing, and contracts that rename an input or output the library's worked cases use | Code |
| Shared dictionary | One name used for two things. No lock is held across a model call, so builders never block each other | Code |
| Structure | A wrong module shape, or imports outside the standard library and `toolbox` | Code |
| The agent's own tests | A function that does not do what its author claims | pytest |
| Acceptance tests | A function that does not match the worked cases. The agent never sees this file | pytest |
| Interface conformance | A function that drifted from its contract | Code |
| Review with memory | What tests miss, and effects across elements | Systems engineer agent |
| You | A build system nobody has judged | The interface |

### Export

`python studio.py export` writes `exports/<project>-build-system/`. It holds every function
version, the registry, the interface spec, the evidence, the example inputs, and one runner.
It needs Python and nothing else: `python run.py baseline`.

### What offline mode does

Offline mode replays saved answers for the CubeSat example request and follow-up, whatever
text you give it. It works for the `cubesat` harness definition only. Two saved answers are flawed on purpose so the checks fire: the link
specialist first reuses a name with another unit, and the thermal specialist leaves a unit out
of a description. The saved drill-down decomposes `PowerBus`; offline, any other part is refused by the
gate because the saved answer names that part. A function version past the saved ones (after a rollback,
say) replays the highest saved version. A live run needs a key and will differ.

## Reading the console

Every script prints one line per event and keeps one live line at the bottom.

```
  start          build.power.margin       [agent:balanced]
  gate failed    build.power.margin       [agent:balanced] the agent's own tests failed: ...
  done           build.power.margin       [agent:balanced] cubesat.power.margin v1, attempt 2
  | 2 running: build.link.downlink, build.thermal.bulk  41s
```

The last line is the spinner. It names what is running now and for how long. With one task
running it also shows the current step, such as `waiting on claude-sonnet-5-5` or
`tool link_budget`. A live model call can take a minute or more, so a moving spinner means
the run is healthy.

Other messages you may see:

| Message | Meaning |
|---|---|
| `first use  downloading the embedding model ...` | The memory layer fetches a small model from Hugging Face once. The spinner shows megabytes received |
| `Starting local memory ...` | mem0, the Qdrant store, and the embedding model are loading. A few seconds after the first use |
| `attempt failed` | A model call ended early. The harness retries with the error as feedback |
| `gate failed` | Code rejected an agent's output. The agent gets the reasons and tries again. This is the design working |
| `FAILED` | A task used all its attempts. A hint follows when the cause is a known one, such as a bad key or no credit |
| `blocked` | A task was skipped because one it depends on failed. Fix the cause, then Resume |
| `budget` | An answer did not fit the output budget. The same turn is retried with twice the room |
| `harness crash` (in the task panel) | A defect in this code, not in the agent's answer. Not retried; the location and traceback are in the panel |

A live script with no key stops at once and says how to add one. When output is piped to a
file there is no spinner, and each event is still one line.

## Map of the code

| Path | Role | Lines to show on stage |
|---|---|---|
| `harness/orchestrator.py` | The loop: ready set, thread pool, gate, retry, spawn, resume, step | `_execute`, `run` |
| `harness/task.py` | `Task`, `Context`, `TaskResult` | all of it |
| `harness/console.py` | The live status line, the spinner, and plain-language error hints | `status` |
| `harness/shared.py` | A file many agents edit at once, with no deadlock | `propose` |
| `studio/pipeline.py` | The ten steps and the follow-up path as harness tasks | `plan` |
| `studio/engine.py` | The evaluation engine: usage trees, name resolution, aggregates, per-usage calcs, scheduling. Standard library only, travels with an export | `evaluate`, `Scope.resolve` |
| `studio/runtime.py` | The engine bound to a project: registry routing, module cache, the sensitivity sweep | `sensitivity` |
| `studio/sysml.py` | The SysML v2 textual notation. Travels with an export | `sysml_text` |
| `studio/gates.py` | Structure, tests, acceptance, interface conformance | `run_gates` |
| `studio/project.py` | A project on disk: spec, interfaces, registry, evidence, changelog | `Project` |
| `studio/designer.py` | An operational scenario becomes agent specs, and the specs become files | `generate`, `save` |
| `studio/export.py` | The build system as a standalone folder | `export` |
| `studio/server.py` | The local API: state, events, evaluate, route, settings, reveal a file | |
| `kits/<name>/` | A harness definition: `harness.yaml`, `agents/*.md`, `library/`, `evidence/` | |
| `ui/` | The React Flow interface. Source in `ui/src`, built bundle in `ui/dist` | |
| `agents/config.py` | Model tiers: fast, balanced, deep | `MODELS` |
| `agents/schemas.py` | Pydantic structured outputs, one per thing an agent returns | `InterfaceSpec`, `BuilderSubmission` |
| `studio/tooling.py` | Tools a harness defines in `tools/*.py`, loaded as Strands tools for the agents that list them | `load_tools` |
| `agents/runner.py` | `run_agent`: live through Strands, or fixture replay | all of it |
| `agents/memory.py` | mem0 store and the recall/remember tools | `mem0_config`, `memory_tools` |
## Models

`agents/config.py` maps three tiers to model ids. Override any of them in `.env`.

| Tier | Default | Used for |
|---|---|---|
| fast | `claude-haiku-4-5-20251001` | Demo 3, mem0 fact extraction |
| balanced | `claude-sonnet-5-5` | The parameterizer, the specialists, the inputs agent, triage |
| deep | `claude-opus-5-5` | The systems engineer: interfaces, reviews, decomposition reviews |

To use another provider, change `build_agent` in `agents/runner.py`. Strands ships
model classes for Bedrock, OpenAI, Ollama, and others, and nothing else in the repo changes.

## Memory

The systems engineer uses mem0 with three local pieces: Anthropic for fact extraction,
FastEmbed for vectors, and Qdrant on disk under `.memory/`. No mem0 account is needed.

Memory reaches the agent two ways. The review task searches the store and injects matching
lessons into the prompt, then writes the review's lessons back. The agent also holds
`recall_lessons` and `remember_lesson` tools for its own queries.

The first live use downloads the embedding model from Hugging Face. The console says so and
shows progress. Run demo 4 once before the talk so the model is cached. FastEmbed keeps it in
the system temp folder. Set `FASTEMBED_CACHE_PATH` in `.env` to keep it somewhere permanent. To reset the agent's memory, run
`Remove-Item -Recurse -Force .memory`.

## What has and has not been verified

- The tests pass (`python -m pytest -q`). They cover the harness and its event log, the engine, an
  end-to-end offline replay of request 1, a follow-up, a drill-down, a rollback, the export, and a mem0
  and Qdrant round trip with a substitute embedder.
- `tests/test_live_path.py` runs the real Strands loop against a scripted stand-in model.
  It proves tool calling, the tool hook, and structured output work. Only the Anthropic
  wire format is left unexercised.
- The live path was built against strands-agents 1.59 and mem0ai 2.2.1. The studio has completed
  live runs against the Anthropic API (the CubeSat kit, and a Kubernetes platform on a generated
  harness); the fixes those runs forced are in the troubleshooting notes above. Run every demo live
  once before the talk.
- The generated `design.sysml` files have **not** been parsed by the SysML v2 pilot implementation.
  Load one there before you claim conformance.
- The CubeSat kit's library methods are first-order sizing relations. The mission numbers are illustrative.
- Demo 1 runs model-written code in a subprocess on your machine. That is fine for a demo.
  Use a sandbox for anything else.

## License

GFDSB is open source under the MIT License. See `LICENSE`. Copyright 2026 Ground & Form Advisory, LLC.

The software is provided as is, without warranty of any kind, express or implied. Three things to
know before you build: agents write Python during a build and the gates run it on your computer, so
review generated functions before you rely on them; live builds call the model provider on your API
key and cost money, while offline replays call nothing; and generated functions are engineering
estimates from the evidence you supplied, not a substitute for qualified review. The interface shows
this notice once on first launch, and the logo reopens it.
