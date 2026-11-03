"""Correct stored benchmark costs after a price fix. No API calls.

On 3 Nov 2026 the Sonnet 5.5 cache-read price in askdata/config.py was corrected
from $0.20 to $0.10 per million tokens. Anthropic's pricing page lists Sonnet 5.5
cache hits at $0.10 ("cache hits and refreshes on Claude Opus 5.5 and Claude
Sonnet 5.5 are priced at 0.05x the base input price"). The runs of 2 Nov had
priced them at $0.20.

Every run stores its token counts per model, so the correction is exact: subtract
the over-charge on Sonnet's cache-read tokens from that run's cost, then rebuild
summary.json and summary.md the same way bench/run.py does. Haiku and Opus costs
are unchanged. Running it twice changes nothing (corrected runs are marked).

    python -m bench.reprice bench/results/<tag> [...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from askdata.config import MODELS
from bench.run import summarize, summary_markdown

OLD_SONNET_CACHE_READ = 0.20  # USD per million tokens, as used on 2 Nov 2026
MARK = "sonnet_cache_read_repriced_2026_11_03"


def reprice_record(rec: dict) -> float:
    """Correct one run in place; return the dollars removed."""
    sonnet = (rec.get("usage") or {}).get("sonnet")
    if rec.get(MARK) or not sonnet or not sonnet.get("cache_read_tokens"):
        return 0.0  # already corrected, or no Sonnet cache reads to correct
    delta = sonnet["cache_read_tokens"] * (OLD_SONNET_CACHE_READ - MODELS["sonnet"].cache_read) / 1_000_000
    sonnet["cost_usd"] = sonnet["cost_usd"] - delta
    rec["cost_usd"] = round(sum(u.get("cost_usd", 0.0) for u in rec["usage"].values()), 6)
    rec[MARK] = True
    return delta


def reprice_dir(d: Path) -> tuple[int, float]:
    path = d / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    removed = sum(reprice_record(r) for r in rows)
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    summary = summarize(rows)
    (d / "summary.json").write_text(json.dumps(summary, indent=2))
    (d / "summary.md").write_text(summary_markdown(summary, rows))
    return len(rows), removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path)
    args = ap.parse_args(argv)
    for d in args.dirs:
        n, removed = reprice_dir(d)
        print(f"{d}: {n} runs, ${removed:.4f} removed (Sonnet 5.5 cache reads at the listed price)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
