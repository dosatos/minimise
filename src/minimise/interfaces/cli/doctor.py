"""`mini doctor`: report harness/provider health so setup problems surface fast."""

import os
import shutil
import subprocess
from pathlib import Path

import click
from rich.table import Table

import minimise.interfaces.cli as _cli  # patchable CONFIG_DIR; read at call time
from minimise.interfaces.cli._shared import console
from minimise.personas import load_personas
from minimise.settings import load_settings

_HARNESS_BINS = {"claude": "claude", "pi": "pi"}

# All provider env vars recognised by at least one harness. Kept in sync with
# ClaudeCodeHarness._build_env and PiHarness._build_env in harness.py. Every
# key maps to a ", "-joined list of harness/provider labels.
_PROVIDER_KEYS = {
    # Anthropic (Claude Code, pi)
    "ANTHROPIC_API_KEY": "claude, pi/anthropic",
    # OpenAI (pi)
    "OPENAI_API_KEY": "pi/openai",
    # Azure (pi)
    "AZURE_OPENAI_API_KEY": "pi/azure",
    # DeepSeek (pi)
    "DEEPSEEK_API_KEY": "pi/deepseek",
    # Google Gemini / Vertex AI (pi)
    "GOOGLE_API_KEY": "pi/google",
    "GOOGLE_GENAI_USE_VERTEXAI": "pi/google-vertex",
    "GOOGLE_GENAI_USE_GENERATIVEAI": "pi/google-genai",
    "GOOGLE_CLOUD_PROJECT": "pi/google-cloud",
    "GOOGLE_CLOUD_LOCATION": "pi/google-cloud",
    # Mistral (pi)
    "MISTRAL_API_KEY": "pi/mistral",
    # Groq (pi)
    "GROQ_API_KEY": "pi/groq",
    # Cerebras (pi)
    "CEREBRAS_API_KEY": "pi/cerebras",
    # Cloudflare (pi)
    "CLOUDFLARE_API_KEY": "pi/cloudflare",
    "CLOUDFLARE_ACCOUNT_ID": "pi/cloudflare",
    "CLOUDFLARE_GATEWAY_ID": "pi/cloudflare",
    # xAI (pi)
    "XAI_API_KEY": "pi/xai",
    # OpenRouter (pi)
    "OPENROUTER_API_KEY": "pi/openrouter",
    # Vercel AI Gateway (pi)
    "AI_GATEWAY_API_KEY": "pi/ai-gateway",
    # ZAI (pi)
    "ZAI_API_KEY": "pi/zai",
    # OpenCode (pi)
    "OPENCODE_API_KEY": "pi/opencode",
    # Hugging Face (pi)
    "HF_TOKEN": "pi/huggingface",
    # Fireworks (pi)
    "FIREWORKS_API_KEY": "pi/fireworks",
    # Kimi (pi)
    "KIMI_API_KEY": "pi/kimi",
    # MiniMax (pi)
    "MINIMAX_API_KEY": "pi/minimax",
    "MINIMAX_CN_API_KEY": "pi/minimax",
    # Xiaomi MiMo (pi)
    "XIAOMI_API_KEY": "pi/xiaomi",
    "XIAOMI_TOKEN_PLAN_CN_API_KEY": "pi/xiaomi",
    "XIAOMI_TOKEN_PLAN_AMS_API_KEY": "pi/xiaomi",
    "XIAOMI_TOKEN_PLAN_SGP_API_KEY": "pi/xiaomi",
    # AWS Bedrock (Claude Code when CLAUDE_CODE_USE_BEDROCK=1, pi)
    "AWS_ACCESS_KEY_ID": "claude-bedrock, pi/bedrock",
    "AWS_SECRET_ACCESS_KEY": "claude-bedrock, pi/bedrock",
    "AWS_SESSION_TOKEN": "claude-bedrock, pi/bedrock",
    "AWS_REGION": "claude-bedrock, pi/bedrock",
    "CLAUDE_CODE_USE_BEDROCK": "claude-bedrock",
}


def _harness_version(binary: str) -> tuple[bool, str]:
    """Return (ok, status) for a harness binary: version string or 'not installed'."""
    path = shutil.which(binary)
    if path is None:
        return False, "not installed"
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
        version = (result.stdout or result.stderr).strip()
        return True, version or "installed (version unknown)"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"error: {e}"


@click.command(name="doctor")
def doctor():
    """Check harness availability, provider auth, and active settings."""
    settings = load_settings(_cli.CONFIG_DIR)
    resolved_harness = os.environ.get("MINIMISE_HARNESS") or settings.harness

    healthy = True

    harness_table = Table(title="Harnesses")
    harness_table.add_column("")
    harness_table.add_column("Name")
    harness_table.add_column("Status")
    for name, binary in _HARNESS_BINS.items():
        ok, status = _harness_version(binary)
        # Only the resolved harness gates health; the rest is informative.
        if name == resolved_harness:
            healthy = healthy and ok
        harness_table.add_row("✅" if ok else "❌", name, status)
    console.print(harness_table)

    provider_table = Table(title="Provider API Keys")
    provider_table.add_column("")
    provider_table.add_column("Env Var")
    provider_table.add_column("Used By")
    provider_table.add_column("Status")
    any_provider_set = False
    for key, used_by in _PROVIDER_KEYS.items():
        is_set = bool(os.environ.get(key))
        any_provider_set = any_provider_set or is_set
        provider_table.add_row("✅" if is_set else "⚠️", key, used_by, "set" if is_set else "not set")
    # pi can also authenticate purely via ~/.pi/agent/auth.json with no env var.
    # Check it so a pi user who set up auth.json instead of env vars isn't told
    # they're unhealthy. Claude Code stores credentials in ~/.claude, but it's
    # opaque and not checked here (ANTHROPIC_API_KEY is the canonical signal).
    pi_auth_json = Path.home() / ".pi" / "agent" / "auth.json"
    pi_has_auth_file = pi_auth_json.is_file()
    if not any_provider_set and not pi_has_auth_file:
        healthy = False
    console.print(provider_table)

    settings_table = Table(title="Active Settings")
    settings_table.add_column("Setting")
    settings_table.add_column("Value")
    settings_table.add_row("harness", settings.harness)
    settings_table.add_row("model", settings.model or "(default)")
    console.print(settings_table)

    personas = load_personas(_cli.CONFIG_DIR)
    overrides = {
        name: p
        for name, p in personas.items()
        if "@" not in name and (p.harness or p.model)
    }
    if overrides:
        persona_table = Table(title="Persona Overrides")
        persona_table.add_column("Persona")
        persona_table.add_column("Harness")
        persona_table.add_column("Model")
        for name, p in sorted(overrides.items()):
            persona_table.add_row(name, p.harness or "(default)", p.model or "(default)")
        console.print(persona_table)

    if not healthy:
        raise SystemExit(1)
