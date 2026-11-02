"""Settings: paths, models, prices and API-key loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("ASKDATA_DB", DATA_DIR / "sales.db"))
GLOSSARY_PATH = DATA_DIR / "glossary.yaml"
EXAMPLES_PATH = DATA_DIR / "examples.yaml"

# The sample data is frozen in time, so "today" is fixed too. This keeps answers
# to "last quarter" or "active customers" reproducible.
TODAY = "2026-10-01"
DATA_THROUGH = "2026-09-30"

API_BASE_URL = "https://api.anthropic.com"


@dataclass(frozen=True)
class ModelSpec:
    key: str  # short name used on the CLI and in results: haiku | sonnet | opus
    model_id: str
    label: str
    input: float  # USD per million input tokens
    output: float  # USD per million output tokens
    cache_write: float  # USD per million tokens written to the 5-minute cache
    cache_read: float  # USD per million tokens read from the cache
    server_fallback: bool  # supports the server-side refusal fallback parameter
    long_context_from: int | None = None  # prompt size (tokens) where a higher rate card starts
    long_rates: tuple[float, float, float, float] | None = None  # input, output, write, read


# Anthropic list prices for the Claude API (USD per million tokens), checked 2 Nov 2026.
# Cache writes are 1.25x input (5-minute TTL); cache reads are 0.1x input (0.05x on Opus 5.5).
MODELS: dict[str, ModelSpec] = {
    "haiku": ModelSpec(
        "haiku", "claude-haiku-5-5", "Claude Haiku 5.5", 0.10, 0.50, 0.125, 0.01, False,
        long_context_from=100_000, long_rates=(0.50, 2.50, 0.625, 0.05),
    ),
    "sonnet": ModelSpec("sonnet", "claude-sonnet-5-5", "Claude Sonnet 5.5", 2.00, 10.00, 2.50, 0.20, True),
    "opus": ModelSpec("opus", "claude-opus-5-5", "Claude Opus 5.5", 4.00, 20.00, 5.00, 0.20, True),
}

# "auto" starts on Haiku and escalates to Sonnet when the question proves hard.
CONFIGS = ["haiku", "sonnet", "opus", "auto"]
DEFAULT_EFFORT = "medium"


class MissingKeyError(RuntimeError):
    pass


def api_key() -> str | None:
    """Find the Anthropic API key without exporting anything to the shell.

    Order: the ASKDATA_ANTHROPIC_API_KEY environment variable (used in cloud
    sessions, so Claude Code's own ANTHROPIC_API_KEY handling is never
    triggered), then ANTHROPIC_API_KEY in the project's .env file (read
    directly, not loaded into the environment), then ANTHROPIC_API_KEY in the
    environment.
    """
    key = os.environ.get("ASKDATA_ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        from dotenv import dotenv_values

        values = dotenv_values(env_file)
        key = values.get("ANTHROPIC_API_KEY") or values.get("ASKDATA_ANTHROPIC_API_KEY")
        if key:
            return key.strip()
    key = os.environ.get("ANTHROPIC_API_KEY")
    return key.strip() if key else None


def make_client(max_retries: int = 5, timeout: float = 300.0):
    """Anthropic client pinned to the public API endpoint and the project's key."""
    import anthropic

    key = api_key()
    if not key:
        raise MissingKeyError(
            "No API key found. Put ANTHROPIC_API_KEY=... in the project's .env file "
            "(or set ASKDATA_ANTHROPIC_API_KEY)."
        )
    base_url = os.environ.get("ASKDATA_ANTHROPIC_BASE_URL", API_BASE_URL)
    return anthropic.Anthropic(api_key=key, base_url=base_url, max_retries=max_retries, timeout=timeout)
