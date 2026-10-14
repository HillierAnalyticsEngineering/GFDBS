"""The live agent path, run against a scripted stand-in for the foundation model.

This exercises the real Strands loop: tool selection, tool execution, the tool hook
that feeds the sequence diagram, and structured output. Only the model is replaced.
"""
import json
from pathlib import Path

from strands import Agent
from strands.models.model import Model

import agents.runner as runner
from agents.schemas import BuilderSubmission
from studio.tooling import load_tools

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


class ScriptedModel(Model):
    """Answers each turn with the next scripted tool call."""

    def __init__(self, script):
        self.script, self.turns = list(script), 0

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {"model_id": "scripted"}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        yield {"output": None}

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        name, tool_input = self.script.pop(0)
        self.turns += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{self.turns}", "name": name}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(tool_input)}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 1}}}


def test_agent_calls_tool_then_returns_structured_output(monkeypatch):
    submission = json.loads((FIXTURES / "builder.link.a2.json").read_text())
    link_budget = load_tools(ROOT / "kits" / "cubesat", ["link_budget"])[0]       # the kit's own tool, as a build hands it to the agent
    link_inputs = dict(tx_power_w=2, tx_gain_dbi=6, rx_gain_dbi=35, frequency_mhz=2250, slant_range_km=1695, data_rate_kbps=1000, noise_temp_k=200)
    model = ScriptedModel([("link_budget", link_inputs), ("BuilderSubmission", submission)])
    monkeypatch.setattr(runner.Settings, "offline", False)
    monkeypatch.setattr(runner, "build_agent", lambda system, tier="fast", tools=(), quiet=True: Agent(
        model=model, system_prompt=system, tools=list(tools), callback_handler=None))
    events = []
    out = runner.run_agent("build.link", system="s", prompt="p", output=BuilderSubmission, tier="fast",
                           tools=[link_budget], log=lambda event, **data: events.append((event, data)))
    assert "def evaluate" in out["module_code"]
    assert model.turns == 2
    kinds = [e for e, _ in events]
    # every round trip to the model is logged, both directions. The schema tool is not logged as a tool.
    assert kinds == ["model_call", "api_request", "api_response", "tool_call", "tool_result", "api_request", "api_response", "usage"]
    calls = dict(events)
    assert [d["tool"] for e, d in events if e == "tool_call"] == ["link_budget"]
    first, second = [d for e, d in events if e == "api_request"]
    assert first["turn"] == 1 and first["system"] == "s" and "link_budget" in first["tools"]
    assert "toolResult" in json.dumps(second["new_messages"])          # the tool result goes back to the model
    replies = [d for e, d in events if e == "api_response"]
    assert replies[0]["stop_reason"] == "tool_use" and "link_budget" in json.dumps(replies[0]["content"])
