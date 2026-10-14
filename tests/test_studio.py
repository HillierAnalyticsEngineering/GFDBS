"""The design studio end to end, offline: request -> build system -> designs -> follow-up -> new versions."""
import json
import threading

import pytest

from agents.config import ROOT, Settings
from harness import Orchestrator, Status, Store
from harness.shared import SharedDictionary
from studio import gates, runtime


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    """Run request 1 and the follow-up once, into a temporary projects folder."""
    import agents.memory as memory
    import studio.project as sp
    from studio.pipeline import plan
    tmp = tmp_path_factory.mktemp("studio")
    saved = (sp.PROJECTS, Settings.offline, Settings.memory_dir, memory._memory)
    sp.PROJECTS, Settings.offline, Settings.memory_dir, memory._memory = tmp / "projects", True, tmp / "mem", None
    project, runs = sp.Project("test-sat"), {}
    project.create_from_kit("cubesat")
    for n, file in ((1, "example_request.md"), (2, "example_followup.md")):
        project.add_request((ROOT / "kits" / "cubesat" / file).read_text(encoding="utf-8"))
        store = Store(tmp / f"run{n}")
        orch = Orchestrator(store, verbose=False)
        orch.add(*plan(project, n, say=lambda *_: None))
        runs[n] = (orch.run(), store)
        if n == 1:
            runs["after1"] = {name: runtime.evaluate_design(project, d["values"]) for name, d in project.designs().items()}
            runs["registry1"] = project.registry()
    yield project, runs
    sp.PROJECTS, Settings.offline, Settings.memory_dir, memory._memory = saved


def test_request_becomes_a_tagged_parameterized_spec(studio):
    project, runs = studio
    tasks, store = runs[1]
    assert all(t.status is Status.DONE for t in tasks.values()), {t.id: t.errors for t in tasks.values() if t.errors}
    spec = store.load_result("parameterize")
    sources = {p["name"]: p["source"] for p in spec["parameters"]}
    assert sources["array_area_m2"] == "user" and sources["pointing_factor"] == "ai"
    assert [p["name"] for p in spec["parameters"] if not p["parameterized"]] == ["solar_constant_w_m2"]   # the person's instruction


def test_specialists_build_in_parallel_and_the_dictionary_refuses_a_unit_clash(studio):
    project, runs = studio
    tasks, _ = runs[1]
    assert {"build.power.margin", "build.radiation.dose", "build.mass.launch", "build.link.downlink", "build.thermal.bulk"} <= set(tasks)
    assert tasks["build.link.downlink"].attempts == 2                                  # refused once, then conformed
    terms = project.read("dictionary.json")["terms"]
    assert terms["required_margin"]["unit"] == "ratio" and terms["required_margin_db"]["unit"] == "dB"


def test_review_with_memory_sends_one_function_back_and_it_becomes_v2(studio):
    project, runs = studio
    tasks, store = runs[1]
    assert store.load_result("review.1")["verdict"] == "rework" and store.load_result("review.2")["verdict"] == "accept"
    assert {"build.thermal.bulk.v2", "verify.r2", "validate.2"} <= set(tasks)
    entry = runs["registry1"]["cubesat.thermal.bulk"]
    assert entry["active"] == "v2" and set(entry["versions"]) == {"v1", "v2"}
    assert "in W" in gates.load_module(project.function_dir("cubesat.thermal.bulk", "v2")).PARAMETERS["internal_power_w"]
    events = [json.loads(line) for line in (store.root / "events.jsonl").read_text().splitlines()]
    assert any(e["event"] == "memory_add" for e in events)
    assert next(e for e in events if e["event"] == "memory_search" and e["task"] == "review.2")["hits"] > 0   # round two recalls round one


def test_baseline_matches_the_request_and_alternatives_are_prepared(studio):
    project, runs = studio
    first = runs["after1"]
    assert [f["rule"] for f in first["baseline"]["findings"]] == ["V-EOLPWR"]       # 0.06 m2 is not enough
    assert first["alt-deployable-panels"]["passes"]
    design = project.read("history/i1.spec.json")
    assert design["mission_name"] == "CNO-Sat 1"
    for name in ("report.html", "design.sysml", "results.json"):
        assert project.path("outputs", "baseline", name).exists()
    model = project.path("outputs", "baseline", "design.sysml").read_text()
    assert "part def SolarArray" in model and "part def BodyMountedPanel :> SolarArray" in model and "calc margin : Margin;" in model
    assert "part panel1 : " in model and "part panel2 : " in model and ":>> arrayAreaM2 = " in model and "sum of panelAreaM2 over SolarArray" in model
    power1 = first["baseline"]["functions"]["cubesat.power.margin"]
    assert power1["part"] == "ElectricalPowerSubsystem" and list(power1["usages"]) == ["eps"]
    assert power1["usages"]["eps"]["inputs"]["array_area_m2"] == 0.06                 # two body-mounted panels of 0.03, rolled up


def test_exchanges_carry_effects_across_elements(studio):
    project, _ = studio
    design = project.designs()["baseline"]
    values = design["values"]
    tx = "communicationsSubsystem.transmitter.tx_power_w"            # the value sits on the transmitter usage
    assert tx in values
    base = runtime.evaluate_design(project, design)
    more = runtime.evaluate_design(project, {**design, "values": {**values, tx: values[tx] * 3}})
    f = "cubesat.power.margin"
    assert more["functions"][f]["result"]["margin"] < base["functions"][f]["result"]["margin"]      # a link choice costs power
    effects = runtime.emergent_effects(project, design)
    assert any(c["input"] == tx and c["affects"] == f for c in effects["couplings"])
    assert any(c["input"].endswith("shield_mm") and c["affects"] == "cubesat.mass.launch" for c in effects["couplings"])


def test_follow_up_versions_functions_tests_and_requirements(studio):
    project, runs = studio
    tasks, store = runs[2]
    assert all(t.status is Status.DONE for t in tasks.values()), {t.id: t.errors for t in tasks.values() if t.errors}
    registry = project.registry()
    assert registry["cubesat.radiation.dose"]["active"] == "v2"                # the method changed: a new version
    assert list(registry["cubesat.power.margin"]["versions"]) == ["v1"]        # only the evidence changed: re-verified, not rebuilt
    assert registry["cubesat.power.margin"]["versions"]["v1"]["evidence"]["user_cases"] == 1
    reqs = {r["id"]: r for r in project.spec()["requirements"]}
    assert reqs["R-003"]["version"] == 2 and "6 dB" in reqs["R-003"]["text"] and "3 dB" in reqs["R-003"]["previous"]
    assert project.context("power")["version"] == 2
    assert any(t["name"] == "dose_factor" and t["optional"] for t in project.contract("cubesat.radiation.dose")["inputs"])
    base = project.designs()["baseline"]
    assert runtime.evaluate_design(project, base)["passes"]
    assert [u["part"] for u in base["usages"][0]["parts"] if u["part"].endswith("Panel")] == ["DeployablePanel", "DeployablePanel"]   # the usage change swapped the panels
    assert base["values"]["eps.panel1.panel_area_m2"] == 0.06
    kinds = [c["kind"] for c in project.read("changelog.json")]
    assert kinds.count("iteration") == 2 and "follow-up" in kinds


def test_static_identifier_routes_to_any_version(studio):
    project, _ = studio
    design = project.designs()["baseline"]
    fid = "cubesat.radiation.dose"
    values = runtime.evaluate_design(project, design)["functions"][fid]["usages"]["radiationEnvironment"]["inputs"]   # the scalars the engine resolved for that usage
    assert runtime.call(project, fid, values)["result"]["mission_dose_krad"] == 9.6
    assert runtime.call(project, fid, values, version="v1")["result"]["mission_dose_krad"] == 8.0   # one call, another version
    project.route(fid, "v1")
    assert runtime.call(project, fid, values)["version"] == "v1"
    project.route(fid, "v2")
    with pytest.raises(ValueError):
        project.route(fid, "v9")


def test_server_state_and_evaluate(studio):
    from studio import server
    project, _ = studio
    state = json.loads(json.dumps(server.state(project), default=str))
    assert state["iteration"] == 2 and "cubesat.link.downlink" in state["registry"] and state["designs"]["baseline"]["result"]["passes"]
    files = server.function_files(project, "cubesat.radiation.dose", "v2")
    assert "dose_factor" in files["code"] and "test_worked_case" in files["acceptance"]


def test_project_owns_its_harness_definition(studio):
    project, _ = studio
    h = project.harness()
    assert h["building"] == "CubeSat" and h["id_prefix"] == "cubesat"
    assert project.specialist("power")["generic"] is False and "solar" in project.specialist("power")["text"].lower()
    assert project.specialist("propulsion")["generic"] is True                 # no specialist yet: the generic one steps in
    before = project.harness_version()
    file = project.path("agents", "power.md")
    file.write_text(file.read_text(encoding="utf-8") + "\n- Always report beginning-of-life generation too.\n", encoding="utf-8")
    assert project.harness_version() != before                                 # editing an agent is a versioned change


def test_any_kit_can_start_a_project(tmp_path, monkeypatch):
    import studio.project as sp
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    assert {"cubesat", "blank"} <= set(sp.kits())
    project = sp.Project("rain")
    project.create_from_kit("blank")
    assert project.harness()["id_prefix"] == "sys" and project.specialist("storage")["generic"]
    with pytest.raises(ValueError):
        sp.Project("x").create_from_kit("no-such-kit")


def test_export_runs_with_no_studio_and_no_model(studio, tmp_path, monkeypatch):
    import subprocess, sys
    import studio.export as ex
    project, _ = studio
    monkeypatch.setattr(ex, "ROOT", tmp_path)
    out = ex.export(project)
    proc = subprocess.run([sys.executable, "-I", "run.py", "baseline"], cwd=out, capture_output=True, text=True)
    assert "Meets every requirement." in proc.stdout and "cubesat.radiation.dose" in proc.stdout, proc.stdout + proc.stderr
    proc = subprocess.run([sys.executable, "-I", "run.py", "baseline", "eps.panel1.panel_area_m2=0.01", "eps.panel2.panel_area_m2=0.01"], cwd=out, capture_output=True, text=True)
    assert "V-EOLPWR" in proc.stdout
    model = (out / "outputs" / "baseline" / "design.sysml").read_text(encoding="utf-8")
    assert "calc def Margin" in model and ":>> arrayAreaM2 = 0.02" in model and ":>> panelAreaM2 = 0.01" in model and "NOT satisfied: R-001" in model


def test_exchange_expressions_are_arithmetic_only():
    assert runtime.compute("a + b * (c - 1) / 2", {"a": 1, "b": 4, "c": 3}) == 5
    for bad in ("__import__('os').system('x')", "a.b", "open('x')", "[1][0]"):
        with pytest.raises((ValueError, SyntaxError)):
            runtime.compute(bad, {"a": 1})
    with pytest.raises(ValueError):
        runtime.compute("a + missing", {"a": 1})


def test_gate_rejects_a_wrong_design(tmp_path):
    wrong = ("PARAMETERS = {'shield_mm': 'in mm', 'life_years': 'in years', 'tolerance_krad': 'in krad'}\n"
             "def evaluate(inputs, measures):\n    return {'mission_dose_krad': 1.0, 'rdm': 9.0, 'passes': True}\n"
             "def check(inputs, measures):\n    return []\n")
    cases = [{"inputs": {"shield_mm": 2.0, "life_years": 2, "tolerance_krad": 20}, "expect": {"rdm": 2.5, "passes": True}, "raises": False}]
    report = gates.run_gates(tmp_path / "gen", "cubesat.radiation.dose", "V-RAD", wrong, "def test_ok():\n    pass\n", [], cases)
    assert report["own_tests"]["ok"] and not report["acceptance"]["ok"] and "acceptance tests" in report["errors"][0]
    assert gates.structure_errors("import requests\nPARAMETERS = {}\ndef evaluate(i, m): pass\ndef check(i, m): pass\n")


def test_shared_dictionary_many_writers_no_lost_update(tmp_path):
    """Twenty threads commit at once. Every commit lands, versions never repeat, and nothing deadlocks."""
    shared, results = SharedDictionary(tmp_path / "d.json"), []

    def writer(i):
        results.append(shared.propose(f"agent{i}", [{"name": f"term_{i}", "unit": "W", "description": "x"},
                                                     {"name": "life_years", "unit": "years", "description": "shared"}]))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    data = shared.read()
    assert data["version"] == 20 and sorted(r["version"] for r in results) == list(range(1, 21))
    assert len(data["terms"]) == 21 and len(data["terms"]["life_years"]["users"]) == 20


def test_shared_dictionary_refuses_a_unit_clash_whole(tmp_path):
    shared = SharedDictionary(tmp_path / "d.json")
    assert shared.propose("power", [{"name": "margin", "unit": "ratio", "description": "a"}])["ok"]
    refused = shared.propose("link", [{"name": "margin", "unit": "dB", "description": "b"},
                                      {"name": "ebn0_db", "unit": "dB", "description": "c"}])
    assert not refused["ok"] and refused["conflicts"][0]["owner"] == "power"
    assert "ebn0_db" not in shared.read()["terms"] and shared.read()["version"] == 1


def test_parallel_specialists_do_not_lose_registry_entries(tmp_path, monkeypatch):
    """Twenty functions register at the same moment. Every one must be in the registry afterward."""
    import studio.project as sp
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    project = sp.Project("race")
    threads = [threading.Thread(target=lambda i=i: (project.register(f"sys.f{i}.x", "v1", {"capability": f"f{i}"}),
                                                    project.log_change({"kind": "function", "n": i}))) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(project.registry()) == 20 and len(project.read("changelog.json")) == 20


def test_settings_write_env_and_define_tiers_without_leaking_the_key(tmp_path, monkeypatch):
    import agents.config as config
    from studio import server
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    out = server.save_settings({"api_key": "test-key-for-this-app-1234", "tiers": {**config.DEFAULT_MODELS, "local": "llama-test", "deep": "claude-test-deep"}})
    assert out["has_key"] and out["key_hint"] == "ends in 1234" and "test-key" not in str(out)      # never echoed back
    assert config.MODELS["local"] == "llama-test" and config.MODELS["deep"] == "claude-test-deep"
    text = (tmp_path / ".env").read_text()
    assert "MODEL_LOCAL=llama-test" in text and "ANTHROPIC_API_KEY=test-key-for-this-app-1234" in text
    server.save_settings({"tiers": dict(config.DEFAULT_MODELS)})                                    # removing a custom tier
    assert "local" not in config.MODELS and "MODEL_LOCAL" not in (tmp_path / ".env").read_text()
    with pytest.raises(ValueError):
        server.save_settings({"tiers": {"Bad Name": "x"}})
    monkeypatch.delenv("MODEL_DEEP", raising=False)
    monkeypatch.delenv("MODEL_FAST", raising=False)
    monkeypatch.delenv("MODEL_BALANCED", raising=False)
    config.refresh_models()


def test_model_list_falls_back_when_there_is_no_key(monkeypatch):
    from studio import server
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    out = server.list_models()
    assert out["source"] == "configured tiers" and out["models"] and "note" in out


def test_scenario_becomes_editable_agent_specs_written_to_files(tmp_path, monkeypatch):
    import studio.designer as designer
    import studio.project as sp
    monkeypatch.setattr(Settings, "offline", True)
    monkeypatch.setattr(designer, "KITS", tmp_path / "kits")
    monkeypatch.setattr(sp, "KITS", tmp_path / "kits")
    from studio import tooling
    kit_tools = tooling.list_tools(ROOT / "kits" / "cubesat")
    assert {t["name"] for t in kit_tools} == {"solar_generation", "link_budget", "thermal_balance"} and not any(t["error"] for t in kit_tools)
    assert designer.generate("x")["agents"][0]["tools"] == []                 # no tools defined: none can be mapped
    draft = designer.generate("How our team designs a CubeSat today.", kit_tools)
    assert [a["capability"] for a in draft["agents"]] == ["power", "radiation", "mass", "link", "thermal"]
    assert draft["agents"][0]["tools"] == ["solar_generation"]
    draft["tools"].append({"name": "delta_v", "code": "import math\n\ndef delta_v(isp_s: float, wet_kg: float, dry_kg: float) -> dict:\n    \"\"\"Rocket equation delta-v in m/s.\"\"\"\n    return {'delta_v_ms': round(9.80665 * isp_s * math.log(wet_kg / dry_kg), 1)}\n"})
    draft["agents"] = [a for a in draft["agents"] if a["capability"] != "thermal"]                 # remove one
    draft["agents"].append({"capability": "Propulsion", "tier": "deep", "tools": ["delta_v", "not_a_tool"],
                            "instructions": "# Propulsion specialist\n\nYou size delta-v.\n"})      # add one
    out = designer.save("kit", "My Team", draft)
    root = tmp_path / "kits" / "my-team"
    assert out["name"] == "my-team" and (root / "agents" / "propulsion.md").read_text().startswith("# Propulsion")
    assert not (root / "agents" / "thermal.md").exists()
    h = sp.kits()["my-team"]
    assert h["specialists"] == ["power", "radiation", "mass", "link", "propulsion"]
    harness = __import__("yaml").safe_load((root / "harness.yaml").read_text())
    assert harness["agents"]["specialists"][-1] == {"capability": "propulsion", "instructions": "agents/propulsion.md", "tier": "deep", "tools": ["delta_v"]}
    assert (root / "tools" / "delta_v.py").exists() and (root / "tools" / "solar_generation.py").exists()
    loaded = tooling.load_tools(root, ["delta_v", "missing"])                 # a real Strands tool, from the person's file
    assert len(loaded) == 1 and loaded[0](isp_s=45.0, wet_kg=4.0, dry_kg=3.88)["delta_v_ms"] > 0
    assert tooling.inspect_code("bad", "def bad(x):\n    return x\n")["error"]      # no docstring, no hints
    assert tooling.inspect_code("other", "def different(x: float) -> float:\n    'd'\n    return x\n")["error"]
    draft["tools"] = [t for t in draft["tools"] if t["name"] != "solar_generation"]              # delete a tool
    designer.save("kit", "My Team", draft)
    assert not (root / "tools" / "solar_generation.py").exists()
    assert __import__("yaml").safe_load((root / "harness.yaml").read_text())["agents"]["specialists"][0]["tools"] == []   # its mapping goes too
    harness = __import__("yaml").safe_load((root / "harness.yaml").read_text())
    se = (root / "agents" / "systems_engineer.md").read_text()
    assert "15288" in se and harness["agents"]["systems_engineer"]["memory"] is True              # always present, always with memory
    again = designer.load("kit", "my-team")
    assert again["agents"][0]["instructions"].startswith("# Power specialist")
    draft["agents"].append({"capability": "power", "tier": "fast", "tools": [], "instructions": "x"})
    with pytest.raises(ValueError):
        designer.save("kit", "my-team", draft)


def test_an_exchange_may_read_a_function_output_and_calls_follow_the_dependencies():
    """A derived value such as an orbit period comes from a function, then feeds an exchange into another function."""
    term = lambda n: {"name": n, "unit": "x", "description": ""}
    interfaces = {
        "contracts": [
            {"function_id": "s.power.margin", "capability": "power", "inputs": [term("eclipse_load_wh")], "outputs": [term("margin")]},
            {"function_id": "s.orbit.geometry", "capability": "orbit", "inputs": [term("altitude_km")], "outputs": [term("orbit_period_min")]},
        ],
        "exchanges": [{"id": "X-1", "source_element": "orbit", "target_element": "power", "target_input": "eclipse_load_wh",
                       "expression": "load_w * orbit_period_min / 60"}],
    }
    steps = runtime.schedule(interfaces, {"altitude_km", "load_w"})
    assert [x.get("id") or x["function_id"] for _, x in steps] == ["s.orbit.geometry", "X-1", "s.power.margin"]
    given = runtime.schedule(interfaces, {"altitude_km", "load_w", "orbit_period_min"})       # a person may still supply the value
    assert given[0][1].get("id") == "X-1"
    interfaces["exchanges"].append({"id": "X-2", "source_element": "power", "target_element": "orbit", "target_input": "altitude_km",
                                    "expression": "margin * 2"})
    with pytest.raises(ValueError, match="loop"):
        runtime.schedule(interfaces, {"load_w"})


def test_a_follow_up_is_refused_until_the_first_build_finishes(tmp_path, monkeypatch):
    """A first build that stopped early leaves no functions. A follow-up then has nothing to change."""
    from studio import pipeline, project as project_module, server
    monkeypatch.setattr(project_module, "PROJECTS", tmp_path)
    p = project_module.Project("half")
    p.create_from_kit("cubesat")
    p.add_request("Build a 3U CubeSat.")
    p.write("spec.json", {"parameters": [{"name": "a"}]})            # parameterize ran, interfaces did not pass
    p.add_request("Adopt deployable panels.")
    assert not p.built
    with pytest.raises(ValueError, match="did not finish"):
        pipeline.plan(p, 2)
    assert "did not finish" in server.start_run("followup", "x", "half", True)["error"]
    aside = p.reset_build()
    assert len(aside) == 1 and p.iteration == 1 and p.requests() == ["Build a 3U CubeSat.\n"]


def test_contracts_must_use_the_names_the_worked_cases_use(studio):
    """The acceptance tests come from worked cases. A contract that renames their inputs could never be built."""
    import copy
    from types import SimpleNamespace
    from studio import pipeline
    project, _ = studio
    names = pipeline.vocabulary(project, ["link", "thermal", "no_such_capability"])
    assert "tx_power_w" in names["link"]["inputs_with_a_sample_value"] and names["thermal"]["outputs"] == ["temperature_c", "passes"]
    assert "no_such_capability" not in names
    gate = pipeline.plan(project, 1)[1].gate
    renamed = copy.deepcopy(project.interfaces())
    link = next(c for c in renamed["contracts"] if c["capability"] == "link")
    next(t for t in link["inputs"] if t["name"] == "tx_power_w")["name"] = "transmitter_rf_power_w"
    link["rule_id"] = "R-001, R-011"
    errors = " ".join(gate(renamed, SimpleNamespace(inputs={"parameterize": project.spec()})))
    assert "tx_power_w" in errors and "V-LINKM" in errors and "one identifier" in errors


def test_two_spellings_of_one_unit_are_not_a_clash(tmp_path):
    shared = SharedDictionary(tmp_path / "d.json")
    assert shared.propose("radiation", [{"name": "passes", "unit": "bool", "description": ""}, {"name": "flux", "unit": "W/m2", "description": ""}])["ok"]
    assert shared.propose("link", [{"name": "passes", "unit": "boolean", "description": ""}, {"name": "flux", "unit": "W/m^2", "description": ""}])["ok"]
    assert not shared.propose("mass", [{"name": "flux", "unit": "kg", "description": ""}])["ok"]


def test_sensitivity_gives_a_tornado_per_function_and_finds_the_multiplicative_interaction(studio):
    """A panel's area and panel_density_g_m2 multiply inside exchange X-3, so moving both is more than the sum.
    Values move per usage: each panel is its own row."""
    project, _ = studio
    s = runtime.sensitivity(project, project.designs()["baseline"])
    assert set(s["tornado"]) == set(project.registry()) and s["inputs_moved"] > 20
    rows = s["tornado"]["cubesat.power.margin"]["rows"]
    assert rows[0]["swing"] >= rows[-1]["swing"] and {"input", "owner", "cross", "low", "high"} <= set(rows[0])
    pairs = {tuple(sorted(i["inputs"])): i for i in s["interactions"]}
    assert ("eps.panel1.panel_area_m2", "panel_density_g_m2") in pairs and pairs[("eps.panel1.panel_area_m2", "panel_density_g_m2")]["cross_domain"]
    assert any(r["input"] == "eps.panel2.panel_area_m2" for r in rows)
    assert all(abs(i["interaction"]) > 0 for i in s["interactions"])


def test_a_stopped_build_resumes_and_keeps_finished_work(tmp_path, monkeypatch):
    """One specialist fails. Resume keeps every finished task, runs the failed one again, and completes."""
    import shutil
    import agents.memory as memory
    import studio.project as sp
    from studio.pipeline import plan
    fixtures = tmp_path / "fixtures"
    shutil.copytree(Settings.fixtures, fixtures)
    hidden = [f for f in fixtures.glob("builder.link*.json")]
    for f in hidden:
        f.rename(f.with_suffix(".hidden"))
    monkeypatch.setattr(sp, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(Settings, "fixtures", fixtures)
    monkeypatch.setattr(Settings, "offline", True)
    monkeypatch.setattr(Settings, "memory_dir", tmp_path / "mem")
    monkeypatch.setattr(memory, "_memory", None)
    project = sp.Project("stopped")
    project.create_from_kit("cubesat")
    project.add_request((ROOT / "kits" / "cubesat" / "example_request.md").read_text(encoding="utf-8"))
    store = Store(tmp_path / "run1")
    orch = Orchestrator(store, verbose=False)
    orch.add(*plan(project, 1, say=lambda *_: None))
    first = orch.run()
    assert first["build.link.downlink"].status is Status.FAILED and not project.built
    done_before = {t for t, task in first.items() if task.status is Status.DONE}
    assert {"parameterize", "interfaces", "assign", "build.power.margin"} <= done_before
    for f in fixtures.glob("builder.link*.hidden"):               # the problem is fixed
        f.rename(f.with_suffix(".json"))
    orch2 = Orchestrator(Store(tmp_path / "run1"), verbose=False, resume=True)
    orch2.add(*plan(project, 1, say=lambda *_: None))
    second = orch2.run()
    events = [json.loads(l) for l in (tmp_path / "run1" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    resumed = {e["task"] for e in events if e["event"] == "resumed"}
    assert done_before <= resumed and "build.link.downlink" not in resumed
    assert project.built and second["render"].status is Status.DONE
    assert all(t.status is not Status.FAILED for t in second.values())


def test_a_dependency_loop_is_explained_edge_by_edge():
    term = lambda n: {"name": n, "unit": "x", "description": ""}
    interfaces = {"contracts": [
        {"function_id": "s.a.one", "capability": "a", "inputs": [term("p")], "outputs": [term("q")]},
        {"function_id": "s.b.two", "capability": "b", "inputs": [term("q")], "outputs": [term("r")]}],
        "exchanges": [{"id": "X-1", "source_element": "b", "target_element": "a", "target_input": "p", "expression": "r * 2"}]}
    with pytest.raises(ValueError) as err:
        runtime.schedule(interfaces, set())
    text = str(err.value)
    assert "X-1 reads r, an output of s.b.two" in text and "s.b.two takes q, an output of s.a.one" in text and "s.a.one takes p from X-1" in text
    # a function reading another function's output by name, with no exchange, still runs after it
    steps = runtime.schedule({"contracts": interfaces["contracts"], "exchanges": []}, {"p"})
    assert [c["function_id"] for _, c in steps] == ["s.a.one", "s.b.two"]


def test_interface_gate_reports_self_reference_and_zero_division_as_feedback(studio):
    """A bad expression is feedback for the agent, never a crash of the task."""
    import copy
    from types import SimpleNamespace
    from studio import pipeline
    project, _ = studio
    gate = pipeline.plan(project, 1)[1].gate
    bad = copy.deepcopy(project.interfaces())
    bad["exchanges"][0]["expression"] = bad["exchanges"][0]["target_input"] + " * 1.1"
    bad["exchanges"][1]["expression"] = "bus_load_w / 0"
    errors = " ".join(gate(bad, SimpleNamespace(inputs={"parameterize": project.spec()})))
    assert "reads 'load_avg_w' itself" in errors and "divides by zero" in errors


def test_a_person_can_stop_a_task_or_change_its_tier_between_attempts(tmp_path):
    """control.json in the run folder is read between attempts: stop ends the task, tier reaches the next attempt."""
    from harness import Context, Task, TaskResult
    seen = []
    def flaky(ctx: Context) -> TaskResult:
        seen.append(dict(ctx.overrides))
        return TaskResult({"n": ctx.attempt})
    store = Store(tmp_path / "r")
    store.set_control(tier={"t": "deep"})
    orch = Orchestrator(store, verbose=False)
    orch.add(Task("t", flaky, gate=lambda out, ctx: [] if out["n"] >= 2 else ["again"], max_attempts=3))
    assert orch.run()["t"].status is Status.DONE and seen == [{"tier": "deep"}, {"tier": "deep"}]
    store2 = Store(tmp_path / "s")
    def gate(out, ctx):
        store2.set_control(stop=["t"])              # the person presses Stop while the gate is failing it
        return ["again"]
    orch2 = Orchestrator(store2, verbose=False)
    orch2.add(Task("t", flaky, gate=gate, max_attempts=3))
    t = orch2.run()["t"]
    assert t.status is Status.FAILED and "stopped by the person" in t.errors[-1]


def test_a_drill_down_decomposes_one_part_reviews_it_and_rebuilds_the_owning_function(studio):
    """PowerBus gets a battery, a conditioning unit, and a harness. Two attributes join the power contract,
    so the power function becomes v2, is re-verified against the same worked cases, and the designs are re-rendered."""
    from harness import Orchestrator, Store
    from studio.pipeline import plan, structure_check
    project, runs = studio
    before = project.interfaces()
    n = project.add_request("Decompose the power bus.", kind="drilldown", part="PowerBus")
    assert project.request_meta(n) == {"kind": "drilldown", "part": "PowerBus"} and project.requests()[-1].startswith("Decompose")
    store = Store(project.path("..", "..", f"run{n}"))
    orch = Orchestrator(store, verbose=False)
    orch.add(*plan(project, n, say=lambda *_: None))
    tasks = orch.run()
    assert all(t.status is Status.DONE for t in tasks.values()), {t.id: t.errors for t in tasks.values() if t.errors}
    ids = set(tasks)
    assert {f"i{n}.decompose", f"i{n}.decompose.review.1", f"i{n}.build.power.margin.v2", f"i{n}.verify.r1", f"i{n}.evaluate", f"i{n}.render"} <= ids
    after = project.interfaces()
    names = {p["name"]: p for p in after["structure"]}
    assert {"BatteryPack", "PowerConditioningUnit", "Harness"} <= set(names) and names["Harness"]["parent"] == "PowerBus" and names["Harness"]["added_in"] == n
    assert after["version"] == before["version"] + 1 and len(after["structure"]) == len(before["structure"]) + 3
    power = project.contract("cubesat.power.margin")
    added = {t["name"]: t for t in power["inputs"] if t.get("added_by") == f"drill-down {n}"}
    assert set(added) == {"conversion_efficiency", "harness_loss_fraction"} and added["harness_loss_fraction"]["part"] == "Harness"
    assert project.registry()["cubesat.power.margin"]["active"] == "v2"
    design = project.designs()["baseline"]
    pcu = "eps.powerBus.powerConditioningUnit"                      # the new parts got usages under each power bus, and the examples on them
    assert [u["part"] for u in design["usages"][0]["parts"][-1]["parts"]] == ["BatteryPack", "PowerConditioningUnit", "Harness"]
    assert design["values"][f"{pcu}.conversion_efficiency"] == 0.95 and design["tags"][f"{pcu}.conversion_efficiency"] == "ai"
    result = runtime.evaluate_design(project, design)
    assert result["functions"]["cubesat.power.margin"]["result"]["delivered_avg_w"] < result["functions"]["cubesat.power.margin"]["result"]["generation_avg_w"]
    assert "Harness" in project.path("outputs", "baseline", "design.sysml").read_text(encoding="utf-8")
    assert [c for c in project.read("changelog.json") if c["kind"] == "drill-down"][-1]["part"] == "PowerBus"
    # the gate holds a finer decomposition to the harness's depth limit
    deep = [{"name": "A", "parent": "", "specializes": "", "multiplicity": "1", "capability": "power"},
            {"name": "B", "parent": "A", "specializes": "", "multiplicity": "1", "capability": "power"},
            {"name": "C", "parent": "B", "specializes": "", "multiplicity": "1", "capability": "power"}]
    assert not structure_check(deep, [{"capability": "power", "inputs": []}], "thing", 3)
    assert any("3 levels" in e and "allows 2" in e for e in structure_check(deep, [{"capability": "power", "inputs": []}], "thing", 2))


def test_a_drill_down_rolls_back_to_its_checkpoint_and_keeps_the_version_inactive(studio):
    """Runs after the drill-down test. The interface spec, the routing, and the designs return to what
    they were. The v2 the drill-down built stays in the registry so the identifier can route to it again."""
    from studio.pipeline import rerender
    project, runs = studio
    n = project.iteration
    assert project.rollbacks() == [n]
    undone = project.rollback()
    rerender(project)
    assert undone["kind"] == "drilldown" and undone["part"] == "PowerBus" and undone["active"]["cubesat.power.margin"] == "v1"
    names = {p["name"] for p in project.interfaces()["structure"]}
    assert not {"BatteryPack", "PowerConditioningUnit", "Harness"} & names
    assert all(not t.get("added_by", "").startswith("drill-down") for t in project.contract("cubesat.power.margin")["inputs"])
    reg = project.registry()["cubesat.power.margin"]
    assert reg["active"] == "v1" and reg["versions"]["v2"]["rolled_back"] == n
    assert not any(k.endswith("conversion_efficiency") for k in project.designs()["baseline"]["values"])
    assert project.iteration == n - 1 and project.path("requests", "rolled_back", f"{n:03d}.md").exists()
    assert project.read("changelog.json")[-1]["kind"] == "rollback"
    assert "Harness" not in project.path("outputs", "baseline", "design.sysml").read_text(encoding="utf-8")
    assert project.rollbacks() == [n - 1]                     # one step at a time: the follow-up before it is now the latest undoable request
    project.route("cubesat.power.margin", "v2")               # the version is still there to route to
    assert project.registry()["cubesat.power.margin"]["active"] == "v2"


def test_a_pass_through_exchange_carries_an_output_to_a_same_named_input():
    """B's input is A's output, under one name. The exchange is allowed, runs after A, and B runs after it."""
    from studio.runtime import identity_source, schedule, describe_cycle
    contracts = [{"function_id": "k.security.mesh", "capability": "security", "inputs": [{"name": "pods"}], "outputs": [{"name": "proxy_cpu_m"}]},
                 {"function_id": "k.scheduling.fit", "capability": "scheduling", "inputs": [{"name": "proxy_cpu_m"}, {"name": "pods"}], "outputs": [{"name": "fits"}]}]
    x = {"id": "X-1", "source_element": "security", "target_element": "scheduling", "target_input": "proxy_cpu_m", "expression": "proxy_cpu_m"}
    assert identity_source(x, contracts) == "k.security.mesh"
    order = [(k, i.get("id") or i.get("function_id")) for k, i in schedule({"contracts": contracts, "exchanges": [x]}, {"pods"})]
    assert order == [("function", "k.security.mesh"), ("exchange", "X-1"), ("function", "k.scheduling.fit")]
    # the same shape with a name nobody produces is still a loop, and the message says what to do
    bad = {**x, "target_input": "proxy_mem_mib", "expression": "proxy_mem_mib"}
    contracts[1]["inputs"].append({"name": "proxy_mem_mib"})
    assert identity_source(bad, contracts) is None
    with pytest.raises(ValueError):
        schedule({"contracts": contracts, "exchanges": [bad]}, {"pods"})
    assert "X-1 reads proxy_mem_mib, which X-1 supplies" in describe_cycle({"contracts": contracts, "exchanges": [bad]}, {"pods"})


def test_a_calc_runs_once_per_usage_and_its_parent_rolls_the_outputs_up():
    """Three node pools of two subtypes, each assessed by the same calc; the cluster calc sums their outputs.
    No functions on disk: the call is a stand-in, so this tests the engine alone."""
    from studio import engine
    structure = [{"name": "Cluster", "parent": "", "specializes": "", "multiplicity": "1", "capability": "scheduling"},
                 {"name": "NodePool", "parent": "Cluster", "specializes": "", "multiplicity": "1..*", "capability": "scheduling"},
                 {"name": "SpotPool", "parent": "Cluster", "specializes": "NodePool", "multiplicity": "0..*", "capability": "scheduling"},
                 {"name": "Node", "parent": "NodePool", "specializes": "", "multiplicity": "1..*", "capability": "scheduling"}]
    contracts = [{"function_id": "k.scheduling.pool", "capability": "scheduling", "part": "NodePool", "rule_id": "R-POOL", "question": "", "method": "",
                  "inputs": [{"name": "nodes", "unit": "count", "description": "", "part": "Node", "aggregate": "count", "of": "", "optional": False},
                             {"name": "vcpu_per_node", "unit": "vCPU", "description": "", "part": "NodePool"}],
                  "outputs": [{"name": "pool_vcpu", "unit": "vCPU", "description": ""}, {"name": "passes", "unit": "bool", "description": ""}]},
                 {"function_id": "k.scheduling.fit", "capability": "scheduling", "part": "Cluster", "rule_id": "R-FIT", "question": "", "method": "",
                  "inputs": [{"name": "cluster_vcpu", "unit": "vCPU", "description": "", "part": "NodePool", "aggregate": "sum", "of": "pool_vcpu"},
                             {"name": "requested_vcpu", "unit": "vCPU", "description": "", "part": "Cluster"}],
                  "outputs": [{"name": "headroom_vcpu", "unit": "vCPU", "description": ""}, {"name": "passes", "unit": "bool", "description": ""}]}]
    interfaces = {"structure": structure, "contracts": contracts, "exchanges": []}
    design = {"name": "d", "usages": [{"name": "cluster", "part": "Cluster", "parts": [
        {"name": "app", "part": "NodePool", "parts": [{"name": "n1", "part": "Node"}, {"name": "n2", "part": "Node"}, {"name": "n3", "part": "Node"}]},
        {"name": "data", "part": "NodePool", "parts": [{"name": "n1", "part": "Node"}]},
        {"name": "spot", "part": "SpotPool", "parts": [{"name": "n1", "part": "Node"}, {"name": "n2", "part": "Node"}]}]}],
        "values": {"cluster.app.vcpu_per_node": 8, "cluster.data.vcpu_per_node": 16, "cluster.spot.vcpu_per_node": 4, "cluster.requested_vcpu": 40}}
    calls = []
    def call(fid, inputs, version):
        calls.append((fid, dict(inputs)))
        if fid == "k.scheduling.pool":
            return {"version": "v1", "inputs": inputs, "result": {"pool_vcpu": inputs["nodes"] * inputs["vcpu_per_node"], "passes": True}, "findings": []}
        head = inputs["cluster_vcpu"] - inputs["requested_vcpu"]
        return {"version": "v1", "inputs": inputs, "result": {"headroom_vcpu": head, "passes": head >= 0}, "findings": [] if head >= 0 else [{"rule": "R-FIT", "message": "short"}]}
    assert engine.instance_errors(interfaces, design) == []
    out = engine.evaluate(interfaces, design, call)
    pool = out["functions"]["k.scheduling.pool"]
    assert list(pool["usages"]) == ["cluster.app", "cluster.data", "cluster.spot"]            # a SpotPool is a NodePool
    assert pool["usages"]["cluster.app"]["result"]["pool_vcpu"] == 24 and pool["usages"]["cluster.spot"]["inputs"]["nodes"] == 2
    assert pool["result"] == {"passes": True, "usages": 3, "pool_vcpu": 8, "worst": "cluster.app"}      # the summary keeps the worst number
    fit = out["functions"]["k.scheduling.fit"]
    assert fit["usages"]["cluster"]["inputs"]["cluster_vcpu"] == 24 + 16 + 8 and fit["result"]["headroom_vcpu"] == 8 and out["passes"]
    assert [fid for fid, _ in calls] == ["k.scheduling.pool"] * 3 + ["k.scheduling.fit"]       # pools first, then the roll-up
    assert out["values"]["cluster.cluster_vcpu"] == 48 and out["values"]["cluster.app.pool_vcpu"] == 24
    # the instance check: a pool with no node, and a part the structure does not compose there
    bad = {**design, "usages": [{"name": "cluster", "part": "Cluster", "parts": [{"name": "app", "part": "NodePool", "parts": []}, {"name": "x", "part": "Node", "parts": []}]}]}
    errors = engine.instance_errors(interfaces, bad)
    assert any("holds 0 Node usage(s); the structure says 1..*" in e for e in errors) and any("puts a Node inside Cluster" in e for e in errors)
    # a value stated once for every usage: the global applies to each pool
    shared = {**design, "values": {"vcpu_per_node": 8, "cluster.requested_vcpu": 40}}
    assert engine.evaluate(interfaces, shared, call)["functions"]["k.scheduling.fit"]["usages"]["cluster"]["inputs"]["cluster_vcpu"] == (3 + 1 + 2) * 8


def test_a_project_can_be_deleted_to_the_trash_and_purged(tmp_path, monkeypatch):
    import studio.project as sp
    monkeypatch.setattr(sp, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(sp, "TRASH", tmp_path / "projects" / ".trash")
    for name in ("one", "two"):
        p = sp.Project(name); p.create_from_kit("cubesat"); p.add_request("x")
    assert [p.name for p in sp.all_projects()] and len(sp.all_projects()) == 2
    done = sp.delete_project("one")
    assert "moved_to" in done and (tmp_path / "projects" / ".trash").exists() and [p.name for p in sp.all_projects()] == ["two"]   # the trash is not a project
    assert sp.delete_project("two", purge=True) == {"name": "two", "purged": True} and sp.all_projects() == []
    with pytest.raises(ValueError):
        sp.delete_project("one")


def test_an_aggregate_over_an_exchanged_value_is_not_demanded_before_the_exchange_runs():
    """volume_size_gib arrives on each PersistentVolume through an exchange; the roll-up over it is a
    runtime matter, not something the inputs gate may require to be stated."""
    from studio import engine
    structure = [{"name": "DataServices", "parent": "", "specializes": "", "multiplicity": "1", "capability": "storage"},
                 {"name": "RdbmsCluster", "parent": "DataServices", "specializes": "", "multiplicity": "1", "capability": "storage"},
                 {"name": "PersistentVolume", "parent": "DataServices", "specializes": "", "multiplicity": "1..*", "capability": "storage"}]
    contracts = [{"function_id": "k.storage.pv", "capability": "storage", "part": "PersistentVolume", "rule_id": "R", "question": "", "method": "",
                  "inputs": [{"name": "volume_size_gib", "unit": "GiB", "description": "", "part": "PersistentVolume"}], "outputs": [{"name": "passes", "unit": "bool", "description": ""}]},
                 {"function_id": "k.storage.data", "capability": "storage", "part": "DataServices", "rule_id": "R2", "question": "", "method": "",
                  "inputs": [{"name": "total_pv_size_gib", "unit": "GiB", "description": "", "part": "PersistentVolume", "aggregate": "sum", "of": "volume_size_gib"}], "outputs": [{"name": "passes", "unit": "bool", "description": ""}]}]
    exchanges = [{"id": "X-1", "source_element": "RdbmsCluster", "target_element": "PersistentVolume", "target_input": "volume_size_gib", "expression": "rdbms_pv_size_gib", "unit": "GiB", "rationale": ""}]
    interfaces = {"structure": structure, "contracts": contracts, "exchanges": exchanges}
    design = {"name": "d", "usages": [{"name": "dataServices", "part": "DataServices", "parts": [{"name": "rdbms", "part": "RdbmsCluster"}, {"name": "pv1", "part": "PersistentVolume"}, {"name": "pv2", "part": "PersistentVolume"}]}],
              "values": {"dataServices.rdbms.rdbms_pv_size_gib": 500}}
    assert engine.instance_errors(interfaces, design) == []
    call = lambda fid, inputs, v: {"version": "v1", "inputs": inputs, "result": {"passes": True, **({"total": inputs.get("total_pv_size_gib")} if "total_pv_size_gib" in inputs else {})}, "findings": []}
    out = engine.evaluate(interfaces, design, call)
    assert out["values"]["dataServices.pv1.volume_size_gib"] == 500 and out["functions"]["k.storage.data"]["result"]["total"] == 1000


def test_a_calc_whose_function_refuses_one_usage_is_summarized_not_crashed():
    from studio.engine import summarize
    s = summarize({"a": {"result": {"passes": True, "margin": 0.5}}, "b": {"error": "missing inputs: ['x']"}, "c": {"result": {"passes": True, "margin": 0.2}}})
    assert s["passes"] is False and s["usages"] == 3 and s["errors"] == 1 and s["margin"] == 0.2 and s["worst"] == "b"
    assert summarize({"a": {"result": {"passes": True}}, "b": {"error": "x"}})["passes"] is False
    assert summarize({"a": {"error": "x"}}) == {}


def test_an_exchange_may_pass_a_label_through_but_arithmetic_on_a_label_is_an_exchange_error():
    from studio import engine
    structure = [{"name": "A", "parent": "", "specializes": "", "multiplicity": "1", "capability": "x"},
                 {"name": "B", "parent": "", "specializes": "", "multiplicity": "1", "capability": "y"}]
    contracts = [{"function_id": "k.x.a", "capability": "x", "part": "A", "rule_id": "R", "question": "", "method": "",
                  "inputs": [{"name": "case", "unit": "name", "description": "", "part": "A"}], "outputs": [{"name": "passes", "unit": "bool", "description": ""}]},
                 {"function_id": "k.y.b", "capability": "y", "part": "B", "rule_id": "R", "question": "", "method": "",
                  "inputs": [{"name": "mode", "unit": "name", "description": "", "part": "B"}, {"name": "n", "unit": "count", "description": "", "part": "B", "optional": True}],
                  "outputs": [{"name": "passes", "unit": "bool", "description": ""}]}]
    exchanges = [{"id": "X-1", "source_element": "A", "target_element": "B", "target_input": "mode", "expression": "case", "unit": "name", "rationale": ""},
                 {"id": "X-2", "source_element": "A", "target_element": "B", "target_input": "n", "expression": "case * 2", "unit": "count", "rationale": ""}]
    design = {"name": "d", "usages": [{"name": "a", "part": "A"}, {"name": "b", "part": "B"}], "values": {"a.case": "hot"}}
    call = lambda fid, inputs, v: {"version": "v1", "inputs": inputs, "result": {"passes": True, **inputs}, "findings": []}
    out = engine.evaluate({"structure": structure, "contracts": contracts, "exchanges": exchanges}, design, call)
    got = {x["id"]: x for x in out["exchanges"]}
    assert got["X-1"]["value"] == "hot" and out["functions"]["k.y.b"]["usages"]["b"]["inputs"]["mode"] == "hot"
    assert "not a number" in got["X-2"]["error"] and out["functions"]["k.y.b"]["usages"]["b"]["inputs"].get("n") is None
