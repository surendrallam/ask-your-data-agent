"""Summaries for the README: accuracy per repetition, consistency and the misses.

  python -m bench.report 2026-11-02-v2
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict

from bench.run import LABEL, RESULTS


def load(tag: str) -> list[dict]:
    return [json.loads(line) for line in (RESULTS / tag / "results.jsonl").read_text().splitlines() if line.strip()]


def report(tag: str) -> str:
    rows = load(tag)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["config"], r["effort"])].append(r)
    order = {"haiku": 0, "sonnet": 1, "opus": 2, "auto": 3}
    effort_order = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}
    out = [
        f"Results for {tag}: {len(rows)} runs, ${sum(r['cost_usd'] for r in rows):.2f}",
        "",
        "| Model | Effort | Accuracy | Range across runs | Always right | $ per question | p50 / p90 latency | Cache hits |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (cfg, eff), rs in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 9), effort_order.get(kv[0][1], 9))):
        by_rep: dict[int, list[dict]] = defaultdict(list)
        by_q: dict[str, list[bool]] = defaultdict(list)
        for r in rs:
            by_rep[r["rep"]].append(r)
            by_q[r["qid"]].append(r["passed"])
        rep_acc = [sum(x["passed"] for x in v) / len(v) for v in by_rep.values()]
        always = sum(all(v) for v in by_q.values())
        lat = sorted(r["latency_s"] for r in rs)
        p90 = lat[min(len(lat) - 1, int(0.9 * len(lat)))]
        acc = sum(r["passed"] for r in rs) / len(rs)
        rng = f"{min(rep_acc) * 100:.0f}–{max(rep_acc) * 100:.0f}%" if len(rep_acc) > 1 else "–"
        out.append(
            f"| {LABEL.get(cfg, cfg)} | {eff} | {acc * 100:.1f}% | {rng} | {always}/{len(by_q)} | "
            f"${statistics.mean(r['cost_usd'] for r in rs):.4f} | {statistics.median(lat):.1f}s / {p90:.1f}s | "
            f"{statistics.mean(r['cache_hit_ratio'] for r in rs) * 100:.0f}% |"
        )
    misses: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if not r["passed"]:
            misses[r["qid"]].append(f"{r['config']}/{r['effort']}#{r['rep']}: {r['reason'][:90]}")
    out += ["", "Misses by question:"]
    questions = {r["qid"]: r["question"] for r in rows}
    for qid in sorted(misses):
        out.append(f"- {qid} {questions[qid]}")
        out += [f"    {m}" for m in misses[qid]]
    return "\n".join(out)


if __name__ == "__main__":
    print(report(sys.argv[1] if len(sys.argv) > 1 else "2026-11-02-v2"))
