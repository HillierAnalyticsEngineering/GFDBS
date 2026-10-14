"""Model tiers. One place to swap foundation models."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def load_env() -> None:
    """Read KEY=VALUE lines from .env if present. No extra dependency."""
    for stream in (sys.stdout, sys.stderr):           # Windows consoles: never fail on a character
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():   # tolerate a Notepad BOM
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()          # before the settings below, so .env can override models and limits

# Match the model to the job. Cheap and fast for drafting, strongest for review.
# A tier is a name an agent spec refers to. Add your own in .env as MODEL_<NAME>=<model id>,
# or in the studio's Settings panel.
DEFAULT_MODELS = {"fast": "claude-haiku-4-5-20251001", "balanced": "claude-sonnet-5-5", "deep": "claude-opus-5-5"}
MODELS: dict[str, str] = {}


def refresh_models() -> dict[str, str]:
    """Rebuild the tier table from the defaults and every MODEL_<NAME> in the environment."""
    MODELS.clear()
    MODELS.update(DEFAULT_MODELS)
    for key, value in os.environ.items():
        if key.startswith("MODEL_") and value.strip():
            MODELS[key[6:].lower()] = value.strip()
    return MODELS


def save_env(updates: dict[str, str | None], env_file: Path | None = None) -> Path:
    """Write settings to .env and to this process. A value of None removes the line."""
    env_file = env_file or ROOT / ".env"
    lines = env_file.read_text(encoding="utf-8-sig").splitlines() if env_file.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in updates]
    kept += [f"{key}={value}" for key, value in updates.items() if value is not None]
    env_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
    for key, value in updates.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    refresh_models()
    return env_file


refresh_models()
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))      # output budget per model turn, fast and balanced tiers
MAX_TOKENS_DEEP = int(os.getenv("MAX_TOKENS_DEEP", "32000"))   # the systems engineer writes the whole interface spec in one turn
MAX_TOKENS_CEILING = int(os.getenv("MAX_TOKENS_CEILING", "64000"))   # a turn that still runs out is retried up to here


def max_tokens_for(tier: str) -> int:
    return MAX_TOKENS_DEEP if tier == "deep" else MAX_TOKENS


def require_key() -> None:
    """Stop early, with a plain message, when a live run has no API key."""
    if Settings.offline or os.getenv("ANTHROPIC_API_KEY", "").strip():
        return
    raise SystemExit(
        "No ANTHROPIC_API_KEY found.\n"
        "  1. Copy .env.example to .env\n"
        "  2. Paste your key after ANTHROPIC_API_KEY=\n"
        "Or add --offline to replay saved outputs with no key.")


class Settings:
    offline = os.getenv("DEMO_OFFLINE", "") == "1"     # replay fixtures, no API calls
    pace = float(os.getenv("DEMO_PACE", "0"))          # seconds each replayed agent call takes
    fixtures = ROOT / "fixtures"
    memory_dir = ROOT / ".memory"
