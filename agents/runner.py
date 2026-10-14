"""One function that runs a Strands agent and returns structured output.

Live mode calls the Anthropic API through Strands.
Offline mode replays a saved fixture, so the demo survives bad conference wifi.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Callable, Sequence

from pydantic import BaseModel

from harness import console

from .config import MAX_TOKENS_CEILING, MODELS, Settings, max_tokens_for


CLIP = 20000        # longest text kept per event field


def build_agent(system: str, tier: str = "fast", tools: Sequence = (), quiet: bool = True, max_tokens: int | None = None):
    """Create a Strands agent on the chosen model tier."""
    if "strands" not in sys.modules:                   # the first import takes a few seconds
        with console.status("Loading the Strands Agents SDK", report=False):
            import strands  # noqa: F401
    from strands import Agent
    from strands.models.anthropic import AnthropicModel

    model = AnthropicModel(model_id=MODELS[tier], max_tokens=max_tokens or max_tokens_for(tier), client_args=_client_args())
    kwargs = {"callback_handler": None} if quiet else {}
    return Agent(model=model, system_prompt=system, tools=list(tools), **kwargs)


def run_agent(key: str, *, system: str, prompt: str, output: type[BaseModel], tier: str = "fast",
              tools: Sequence = (), log: Callable | None = None) -> dict:
    """Run one agent turn and return its validated structured output as a dict.

    `log(event, **data)` receives model and tool events for the run's event log.
    """
    log = log or (lambda event, **data: None)
    sent = {"system": system[:CLIP], "prompt": prompt[:CLIP]}       # shown in the Orchestration task panel
    if Settings.offline:
        log("model_call", tier=tier, model=f"fixture {key}.json", offline=True, **sent)
        path = Settings.fixtures / f"{key}.json"
        if not path.exists():
            raise FileNotFoundError(f"offline mode has no fixture for task '{key}' ({path.name})")
        time.sleep(Settings.pace)
        answer = output.model_validate(json.loads(path.read_text(encoding="utf-8"))).model_dump()
        log("model_return", offline=True, model=f"fixture {key}.json", answer=_clip(answer))
        return answer

    budget = max_tokens_for(tier)
    log("model_call", tier=tier, model=MODELS[tier], offline=False, **sent)
    while True:
        agent = build_agent(system, tier, tools) if budget == max_tokens_for(tier) else build_agent(system, tier, tools, max_tokens=budget)
        _watch_tools(agent, log, skip=output.__name__)
        _watch_model(agent, log, MODELS[tier])
        try:
            result = _call_with_backoff(agent, prompt, output, log)
            break
        except Exception as exc:                  # noqa: BLE001
            # The answer did not fit the output budget. That is a budget problem, not a wrong answer, so it is
            # retried here with twice the room, not sent back through the gate as a failed attempt.
            if not _hit_output_limit(exc) or budget >= MAX_TOKENS_CEILING:
                raise
            budget = min(budget * 2, MAX_TOKENS_CEILING)
            log("output_budget_raised", max_tokens=budget, error=str(exc)[:200])
            console.say(f"  budget         the answer did not fit. Retrying with {budget} output tokens")
    try:                                          # Strands totals the tokens after the last round trip, so report them here
        used = dict(getattr(result.metrics, "accumulated_usage", None) or getattr(agent.event_loop_metrics, "accumulated_usage", {}) or {})
        if used.get("inputTokens") or used.get("outputTokens"):
            log("usage", model=MODELS[tier], input_tokens=used.get("inputTokens", 0), output_tokens=used.get("outputTokens", 0))
    except Exception:
        pass
    return result.structured_output.model_dump()


def _hit_output_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "maxtokensreached" in text or "maximum token limit" in text


def _call_with_backoff(agent, prompt: str, output: type[BaseModel], log: Callable):
    """One agent turn, retried on network trouble. The SDK retries single requests; this covers the turn."""
    import time
    delays = (5, 20, 60)
    for i, delay in enumerate(delays + (None,)):
        try:
            return agent(prompt, structured_output_model=output)
        except Exception as exc:                  # noqa: BLE001 - we inspect the message
            text = f"{type(exc).__name__}: {exc}".lower()
            transient = any(w in text for w in ("connection", "timed out", "timeout", "overloaded", "529", "rate_limit", "429", "remoteprotocol", "server disconnected"))
            if not transient or delay is None:
                raise
            log("network_retry", attempt=i + 1, wait_s=delay, error=str(exc)[:300])
            console.say(f"  network        {str(exc)[:80] or type(exc).__name__}. Retrying in {delay} s")
            time.sleep(delay)


def _watch_tools(agent, log: Callable, skip: str) -> None:
    """Log each tool the agent calls. A hook problem must never break the run."""
    try:
        from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

        def on_tool(event: BeforeToolCallEvent) -> None:
            name = event.tool_use.get("name", "?")
            if name != skip:                      # the structured-output tool is not a real tool call
                log("tool_call", tool=name, input=event.tool_use.get("input", {}))

        def on_result(event: AfterToolCallEvent) -> None:
            name = event.tool_use.get("name", "?")
            if name != skip:
                log("tool_result", tool=name, result=json.dumps(event.result, default=str)[:CLIP])

        agent.add_hook(on_tool, BeforeToolCallEvent)
        agent.add_hook(on_result, AfterToolCallEvent)
    except Exception:                             # older or newer Strands without this hook
        pass


def _clip(value, limit: int = 6000):
    """Keep event payloads readable: shorten very long strings, keep the structure."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f" ... [{len(value) - limit} more characters]"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v, limit) for v in value]
    return value if isinstance(value, (int, float, bool)) or value is None else str(value)[:limit]


def _watch_model(agent, log: Callable, model_id: str) -> None:
    """Log every round trip to the foundation model: what was sent and what came back.

    One agent turn is several round trips. The model asks for a tool, the tool result goes
    back in the next request, and so on until the final answer.
    """
    try:
        from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent
        state = {"turn": 0, "seen": 0, "tokens": {"inputTokens": 0, "outputTokens": 0}}
        provider = type(agent.model).__name__

        def usage(a) -> dict:
            total = dict(getattr(a.event_loop_metrics, "accumulated_usage", {}) or {})
            delta = {k: total.get(k, 0) - state["tokens"].get(k, 0) for k in ("inputTokens", "outputTokens")}
            state["tokens"] = {k: total.get(k, 0) for k in ("inputTokens", "outputTokens")}
            return delta if any(delta.values()) else None    # not counted yet. Say nothing instead of zero

        def before(event: BeforeModelCallEvent) -> None:
            state["turn"] += 1
            messages = list(event.agent.messages)
            new = [m for m in messages[state["seen"]:] if m.get("role") != "assistant"]
            state["seen"] = len(messages)
            log("api_request", turn=state["turn"], model=model_id, provider=provider,
                system=_clip(event.agent.system_prompt or "") if state["turn"] == 1 else None,
                tools=sorted(event.agent.tool_names), messages_in_conversation=len(messages),
                new_messages=_clip(new))

        def after(event: AfterModelCallEvent) -> None:
            reply = event.stop_response
            if reply is None:
                log("api_response", turn=state["turn"], model=model_id, error=str(event.exception)[:2000])
                return
            log("api_response", turn=state["turn"], model=model_id, stop_reason=str(reply.stop_reason),
                content=_clip(reply.message.get("content", [])), usage=usage(event.agent))

        agent.add_hook(before, BeforeModelCallEvent)
        agent.add_hook(after, AfterModelCallEvent)
    except Exception:                             # logging must never break a run
        pass


def _client_args() -> dict:
    """Close each HTTP connection as soon as its response ends.

    Every task runs in its own thread with its own event loop. A kept-alive connection
    outlives that loop, and closing it later prints 'RuntimeError: Event loop is closed'
    on Windows. The message is harmless, but it buries the run log.
    """
    try:
        import anthropic
        import httpx
        return {"http_client": anthropic.DefaultAsyncHttpxClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=0),
            timeout=httpx.Timeout(600.0, connect=30.0)),   # a long answer may stream for minutes; a dead connection must not hang
            "max_retries": 4}                                 # the SDK retries 429, 5xx, and dropped connections with backoff
    except Exception:
        return {}
