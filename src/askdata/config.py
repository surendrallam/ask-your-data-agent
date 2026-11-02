"""Central settings: models, prices, limits, paths.

Prices were checked on Anthropic's pricing page on 2026-11-02
(https://platform.claude.com/docs/en/about-claude/pricing). See
docs/pricing-2026-11-02.md for the rows copied from that page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("ASKDATA_DB", ROOT / "data" / "chinook.sqlite"))
GLOSSARY_PATH = ROOT / "data" / "glossary.json"
COSTS_CSV = Path(os.environ.get("ASKDATA_COSTS_CSV", ROOT / "runs" / "costs.csv"))

# Name of the only environment variable the app reads the API key from.
API_KEY_ENV = "ASKDATA_ANTHROPIC_KEY"

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
MODEL_ALIASES = {"haiku": HAIKU, "sonnet": SONNET, "opus": OPUS}
DEFAULT_MODEL = HAIKU
ESCALATION_MODEL = SONNET

# Same effort on every model so the benchmark compares models, not settings.
EFFORT = "medium"
MAX_TOKENS = 8000  # per API call; thinking counts toward it
MAX_TURNS = 8  # API calls per conversation before giving up

# Guardrails
ROW_LIMIT = 200  # rows fetched and returned to the UI / caller
MODEL_ROW_LIMIT = 50  # rows shown to the model in a tool result
QUERY_TIMEOUT_S = 5.0
MAX_SQL_CHARS = 10_000
MAX_CELL_CHARS = 200

# Spend controls
BUDGET_USD = float(os.environ.get("ASKDATA_BUDGET_USD", "85"))


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float


PRICES: dict[str, Price] = {
    HAIKU: Price(0.10, 0.125, 0.20, 0.01, 0.50),  # prompts up to 100K tokens
    SONNET: Price(2.00, 2.50, 4.00, 0.10, 10.00),
    OPUS: Price(4.00, 5.00, 8.00, 0.20, 20.00),
}
# Haiku 5.5 is priced by prompt length: prompts over 100K tokens pay more.
HAIKU_LONG = Price(0.50, 0.625, 1.00, 0.05, 2.50)
HAIKU_LONG_THRESHOLD = 100_000
PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_CHECKED = "2026-11-02"


def resolve_model(name: str | None) -> str | None:
    if name is None or name == "auto":
        return None
    return MODEL_ALIASES.get(name, name)
