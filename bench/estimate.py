"""Pre-run cost estimate. Makes no API calls; prints low / typical / high per model.

The fixed prefix (system prompt + tool definitions + Anthropic's 286-token
tool-use prompt) is estimated at 4 characters per token. Calls per question
and output tokens per call (thinking included) are assumptions, shown in
the output, until a smoke test measures them.

    python -m bench.estimate --questions 50
"""

from __future__ import annotations

import argparse
import json

from askdata import config
from askdata.costs import CallUsage, call_cost
from askdata.prompts import system_prompt
from askdata.tools import TOOLS

# (calls per question, output tokens per call, new tokens appended per call)
SCENARIOS = {"low": (2.5, 250, 300), "typical": (3.5, 600, 500), "high": (5.0, 1500, 900)}


def prefix_tokens() -> int:
    return (len(system_prompt()) + len(json.dumps(TOOLS))) // 4 + 286


def per_question(model: str, calls: float, out: int, new: int, prefix: int) -> float:
    """Each call reads the cached prefix + earlier turns, writes its new tail to cache, and generates `out`."""
    total = 0.0
    n = int(calls)
    frac = calls - n
    for i in range(n + (1 if frac else 0)):
        weight = frac if i == n else 1.0
        u = CallUsage(input_tokens=20, output_tokens=out, cache_creation_input_tokens=new + (out if i else 0),
                      cache_read_input_tokens=prefix + i * (new + out))
        total += weight * call_cost(model, u)
    return total


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=int, default=50)
    args = ap.parse_args(argv)
    p = prefix_tokens()
    print(f"Fixed prefix ~{p} tokens (system + tools + tool-use prompt), cached after the first call.")
    print("Scenario assumptions (calls/question, output tokens/call, new tokens/call):",
          {k: v for k, v in SCENARIOS.items()})
    grand = {k: 0.0 for k in SCENARIOS}
    for model in (config.HAIKU, config.SONNET, config.OPUS):
        first_write = p * config.PRICES[model].cache_write_5m / 1e6  # one cold write per run
        row = []
        for name, (calls, out, new) in SCENARIOS.items():
            cost = per_question(model, calls, out, new, p) * args.questions + first_write
            grand[name] += cost
            row.append(f"{name} ${cost:.3f} (${cost / args.questions:.4f}/q)")
        print(f"{model:18s} x{args.questions}: " + " | ".join(row))
    print("All three models: " + " | ".join(f"{k} ${v:.2f}" for k, v in grand.items()))


if __name__ == "__main__":
    main()
