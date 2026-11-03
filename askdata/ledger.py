"""Per-call cost ledger with a hard budget check.

Every Claude API call the agent makes is appended to a CSV file (default
`runs/costs.csv`) as soon as it returns: model, token counts, cost, latency,
stop reason and the running total. Before each call, the ledger reserves the
call's worst case (the whole prompt at the higher of the input and cache-write
rates, plus `max_tokens` of output) and refuses the call if that could take
spend past the budget. Reservations are held until the call returns, so
concurrent calls can't overshoot together.

Settings (environment):
  ASKDATA_COSTS_CSV   ledger file (default: runs/costs.csv in the repo)
  ASKDATA_BUDGET_USD  total budget across everything in that file (default: 85)

The total is read back from the file on start, so the budget covers every
call logged there, across runs and processes started later.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from askdata.config import ROOT, ModelSpec

DEFAULT_BUDGET_USD = 85.0
FIELDS = [
    "ts_utc",
    "label",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "cost_usd",
    "latency_ms",
    "stop_reason",
    "running_total_usd",
]


class BudgetExceeded(RuntimeError):
    """Raised before an API call whose worst case could take spend past the budget."""


def estimate_prompt_tokens(*parts: Any) -> int:
    """Generous estimate of a request's prompt size: about 3 characters per token."""
    chars = 0
    for p in parts:
        try:
            chars += len(json.dumps(p, default=str))
        except (TypeError, ValueError):
            chars += len(str(p))
    return chars // 3 + 300


def worst_case_usd(spec: ModelSpec, prompt_tokens: int, max_tokens: int) -> float:
    """Upper bound for one call: the whole prompt at the dearer of input and cache-write rates, full output."""
    inp, out, write = spec.input, spec.output, spec.cache_write
    if spec.long_context_from and spec.long_rates and prompt_tokens > spec.long_context_from:
        inp, out, write = spec.long_rates[0], spec.long_rates[1], spec.long_rates[2]
    return (prompt_tokens * max(inp, write) + max_tokens * out) / 1_000_000


class CostLedger:
    def __init__(
        self,
        path: Path | str | None = None,
        budget_usd: float | None = None,
        run_budget_usd: float | None = None,
    ) -> None:
        self.path = Path(path or os.environ.get("ASKDATA_COSTS_CSV") or ROOT / "runs" / "costs.csv")
        env_budget = os.environ.get("ASKDATA_BUDGET_USD")
        self.budget_usd = float(budget_usd if budget_usd is not None else env_budget or DEFAULT_BUDGET_USD)
        self.run_budget_usd = run_budget_usd
        self._lock = threading.Lock()
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with self.path.open("w", newline="") as f:
                csv.writer(f).writerow(FIELDS)
        self.total_usd = self._read_total()
        self.run_usd = 0.0
        self.reserved_usd = 0.0

    def _read_total(self) -> float:
        total = 0.0
        with self.path.open(newline="") as f:
            for row in csv.DictReader(f):
                try:
                    total += float(row.get("cost_usd") or 0)
                except ValueError:
                    continue
        return total

    @contextmanager
    def labelled(self, label: str) -> Iterator[None]:
        """Tag the calls made in this thread (for example a benchmark question id)."""
        previous = getattr(self._local, "label", "")
        self._local.label = label
        try:
            yield
        finally:
            self._local.label = previous

    def reserve(self, worst_usd: float) -> float:
        """Hold a call's worst case against the budget, or refuse the call."""
        with self._lock:
            committed = self.total_usd + self.reserved_usd
            if committed + worst_usd > self.budget_usd:
                raise BudgetExceeded(
                    f"Refusing API call: ${committed:.4f} spent or reserved + worst case ${worst_usd:.4f} "
                    f"would pass the ${self.budget_usd:.2f} budget ({self.path})"
                )
            if self.run_budget_usd is not None and self.run_usd + self.reserved_usd + worst_usd > self.run_budget_usd:
                raise BudgetExceeded(
                    f"Refusing API call: this run's ${self.run_usd + self.reserved_usd:.4f} spent or reserved + "
                    f"worst case ${worst_usd:.4f} would pass its ${self.run_budget_usd:.2f} budget"
                )
            self.reserved_usd += worst_usd
            return worst_usd

    def release(self, reserved_usd: float) -> None:
        """Give back a reservation for a call that failed before it was billed."""
        with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - reserved_usd)

    def record(
        self,
        model: str,
        usage: Any,
        cost_usd: float,
        latency_ms: float,
        stop_reason: str | None,
        reserved_usd: float = 0.0,
    ) -> None:
        def n(name: str) -> int:
            return int(getattr(usage, name, 0) or 0)

        with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - reserved_usd)
            self.total_usd += cost_usd
            self.run_usd += cost_usd
            row = [
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                getattr(self._local, "label", ""),
                model,
                n("input_tokens"),
                n("output_tokens"),
                n("cache_creation_input_tokens"),
                n("cache_read_input_tokens"),
                f"{cost_usd:.6f}",
                f"{latency_ms:.0f}",
                stop_reason or "",
                f"{self.total_usd:.6f}",
            ]
            with self.path.open("a", newline="") as f:
                csv.writer(f).writerow(row)
