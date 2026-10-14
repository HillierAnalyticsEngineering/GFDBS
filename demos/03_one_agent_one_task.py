"""Demo 3. One Strands agent, one deterministic tool, structured output, one harness task with a gate.

  python demos/03_one_agent_one_task.py            live
  python demos/03_one_agent_one_task.py --offline  replay

The link specialist sizes a CubeSat downlink. It calls the CubeSat kit's link_budget tool for the
arithmetic (kits/cubesat/tools/link_budget.py, the same file a build hands that agent) and returns a
LinkSizing the gate can check. The gate recomputes the link with the same function. It never trusts
the agent's claim.
"""
import _path  # noqa: F401
import json
import sys

from pydantic import BaseModel, Field

from agents.config import ROOT, Settings, load_env, require_key
from agents.runner import run_agent
from harness import Context, Orchestrator, Store, Task, TaskResult, console
from studio.tooling import load_tools

load_env()
Settings.offline = Settings.offline or "--offline" in sys.argv
require_key()

KIT = ROOT / "kits" / "cubesat"
GROUND = dict(rx_gain_dbi=35.0, noise_temp_k=200.0, slant_range_km=1695.0)   # a 500 km orbit seen at 10 degrees elevation
NEED = dict(data_rate_kbps=1000.0, required_ebn0_db=9.6, required_margin_db=3.0)


class LinkSizing(BaseModel):
    """What the agent hands back. Numbers it chose; the gate does the arithmetic again."""
    tx_power_w: float = Field(description="Transmitter RF power")
    tx_gain_dbi: float = Field(description="Spacecraft antenna gain")
    frequency_mhz: float
    ebn0_db: float = Field(description="The Eb/N0 the tool reported for these choices")
    rationale: str


SYSTEM = """You are the communications specialist on a CubeSat team. Size the S-band downlink.
Call link_budget for every number you report. Choose the smallest transmitter power that closes the link
with the required margin at a realistic antenna gain. Report the Eb/N0 the tool returned."""


def size_link(ctx: Context) -> TaskResult:
    prompt = (f"Ground station and geometry: {json.dumps(GROUND)}\nRequirement: {json.dumps(NEED)}\n"
              f"Close the link with Eb/N0 at least required_ebn0_db + required_margin_db.")
    if ctx.feedback:
        prompt += "\nFix these problems:\n" + "\n".join(ctx.feedback)
    tools = [] if Settings.offline else load_tools(KIT, ["link_budget"])
    out = run_agent("demo.link", system=SYSTEM, prompt=prompt, output=LinkSizing, tier="fast", tools=tools, log=ctx.log)
    return TaskResult(out, note=f"attempt {ctx.attempt}")


def link_closes(out: dict, ctx: Context) -> list[str]:
    """The gate recomputes the link from the reported parameters with the kit's own function."""
    namespace: dict = {}
    exec(compile((KIT / "tools" / "link_budget.py").read_text(encoding="utf-8"), "link_budget.py", "exec"), namespace)
    link = namespace["link_budget"](tx_power_w=out["tx_power_w"], tx_gain_dbi=out["tx_gain_dbi"], frequency_mhz=out["frequency_mhz"],
                                    data_rate_kbps=NEED["data_rate_kbps"], **GROUND)
    needed = NEED["required_ebn0_db"] + NEED["required_margin_db"]
    ctx.log("gate_output", check="recompute the link budget", text=json.dumps(link, indent=2))
    console.say(f"    gate recomputed: Eb/N0 {link['ebn0_db']} dB against {needed} dB needed")
    errors = []
    if link["ebn0_db"] < needed:
        errors.append(f"Eb/N0 is {link['ebn0_db']} dB with these choices; the requirement is {needed} dB")
    if abs(link["ebn0_db"] - out["ebn0_db"]) > 0.5:
        errors.append(f"you reported Eb/N0 {out['ebn0_db']} dB but the tool gives {link['ebn0_db']} dB for the same inputs")
    if out["tx_power_w"] > 10:
        errors.append(f"{out['tx_power_w']} W is more than a CubeSat bus can supply; stay at or below 10 W")
    return errors


orch = Orchestrator(Store(ROOT / "runs" / "demo03"))
orch.add(Task("demo.link", size_link, kind="agent", model="fast", gate=link_closes, max_attempts=3))
orch.run()
out = orch.outputs.get("demo.link")
if out:
    print(f"\n{out['tx_power_w']} W at {out['tx_gain_dbi']} dBi, {out['frequency_mhz']} MHz: Eb/N0 {out['ebn0_db']} dB")
    print(out["rationale"])
