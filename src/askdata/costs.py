"""Per-call cost calculation and the append-only spend ledger (runs/costs.csv).

Every API call is written to the ledger as soon as it returns, with model,
token counts, cost, latency and the running total. Before each call the
ledger checks that the worst case for that call cannot push total spend
past the budget (default $85), and refuses otherwise.
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

FIELDS = [
    "ts_utc",
    "run_id",
    "purpose",
    "question_id",
    "model",
    "call_no",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cost_usd",
    "latency_ms",
    "stop_reason",
    "running_total_usd",
]


class BudgetExceeded(RuntimeError):
    pass


def _get(obj: Any, name: str, default: int = 0) -> int:
    val = getattr(obj, name, None)
    if val is None and isinstance(obj, dict):
        val = obj.get(name)
    return int(val or 0)


@dataclass
class CallUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_1h_tokens: int = 0  # subset of cache_creation_input_tokens

    @classmethod
    def from_usage(cls, usage: Any) -> "CallUsage":
        u = cls(
            input_tokens=_get(usage, "input_tokens"),
            output_tokens=_get(usage, "output_tokens"),
            cache_creation_input_tokens=_get(usage, "cache_creation_input_tokens"),
            cache_read_input_tokens=_get(usage, "cache_read_input_tokens"),
        )
        breakdown = getattr(usage, "cache_creation", None)
        if breakdown is not None:
            u.cache_write_1h_tokens = _get(breakdown, "ephemeral_1h_input_tokens")
        return u

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


def price_for(model: str, prompt_tokens: int = 0) -> config.Price:
    if model not in config.PRICES:
        raise KeyError(f"No price for model {model!r}; add it to config.PRICES")
    if model == config.HAIKU and prompt_tokens > config.HAIKU_LONG_THRESHOLD:
        return config.HAIKU_LONG
    return config.PRICES[model]


def call_cost(model: str, usage: CallUsage) -> float:
    p = price_for(model, usage.prompt_tokens)
    write_5m = usage.cache_creation_input_tokens - usage.cache_write_1h_tokens
    return (
        usage.input_tokens * p.input
        + write_5m * p.cache_write_5m
        + usage.cache_write_1h_tokens * p.cache_write_1h
        + usage.cache_read_input_tokens * p.cache_read
        + usage.output_tokens * p.output
    ) / 1_000_000


def worst_case_cost(model: str, prompt_tokens_estimate: int, max_tokens: int) -> float:
    """Upper bound for one call: whole prompt written to cache at the 5m rate, full max_tokens out."""
    p = price_for(model, prompt_tokens_estimate)
    return (prompt_tokens_estimate * max(p.input, p.cache_write_5m) + max_tokens * p.output) / 1_000_000


class CostLedger:
    """Append-only CSV ledger with a hard total cap and an optional per-run cap."""

    def __init__(
        self,
        path: Path | str | None = None,
        budget_usd: float | None = None,
        run_budget_usd: float | None = None,
    ) -> None:
        self.path = Path(path or config.COSTS_CSV)
        self.budget_usd = config.BUDGET_USD if budget_usd is None else budget_usd
        self.run_budget_usd = run_budget_usd
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with self.path.open("w", newline="") as f:
                csv.writer(f).writerow(FIELDS)
        self.total_usd = self._read_total()
        self.run_totals: dict[str, float] = {}

    def _read_total(self) -> float:
        total = 0.0
        with self.path.open(newline="") as f:
            for row in csv.DictReader(f):
                total += float(row.get("cost_usd") or 0)
        return total

    def check(self, projected_usd: float, run_id: str | None = None) -> None:
        with self._lock:
            if self.total_usd + projected_usd > self.budget_usd:
                raise BudgetExceeded(
                    f"Refusing API call: total spend ${self.total_usd:.4f} + worst case "
                    f"${projected_usd:.4f} would exceed the ${self.budget_usd:.2f} budget"
                )
            if run_id and self.run_budget_usd is not None:
                spent = self.run_totals.get(run_id, 0.0)
                if spent + projected_usd > self.run_budget_usd:
                    raise BudgetExceeded(
                        f"Refusing API call: run {run_id} spend ${spent:.4f} + worst case "
                        f"${projected_usd:.4f} would exceed its ${self.run_budget_usd:.2f} cap"
                    )

    def record(
        self,
        *,
        model: str,
        usage: CallUsage,
        latency_ms: float,
        stop_reason: str | None,
        run_id: str = "",
        purpose: str = "app",
        question_id: str = "",
        call_no: int = 0,
    ) -> float:
        cost = call_cost(model, usage)
        with self._lock:
            self.total_usd += cost
            if run_id:
                self.run_totals[run_id] = self.run_totals.get(run_id, 0.0) + cost
            row = [
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                run_id,
                purpose,
                question_id,
                model,
                call_no,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_creation_input_tokens,
                usage.cache_read_input_tokens,
                f"{cost:.6f}",
                f"{latency_ms:.0f}",
                stop_reason or "",
                f"{self.total_usd:.6f}",
            ]
            with self.path.open("a", newline="") as f:
                csv.writer(f).writerow(row)
                f.flush()
        return cost
