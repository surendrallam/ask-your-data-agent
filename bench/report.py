"""Summarise benchmark runs into tables (Markdown + JSON).

    python -m bench.report runs/bench/<run_id> [more run dirs] --out docs/results

Uses graded.jsonl (written by bench.regrade) when present, so the report
shows both the strict score and the corrected score; otherwise results.jsonl.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

LABELS = {"claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5",
          "haiku": "Haiku 5.5", "sonnet": "Sonnet 5.5", "opus": "Opus 5.5", "auto": "Auto (Haiku → Sonnet)"}
DIFFS = ["easy", "medium", "hard", "ambiguous"]


def load(dirs: list[Path]) -> list[dict]:
    recs: dict[tuple[str, str], dict] = {}
    for d in dirs:
        src = d / "graded.jsonl" if (d / "graded.jsonl").exists() else d / "results.jsonl"
        for line in src.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            r.setdefault("correct_strict", r["correct"])
            recs[(r["model"], r["id"])] = r  # a later run of the same (model, id) wins
    return list(recs.values())


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "–"


def summarise(recs: list[dict]) -> dict[str, dict]:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_model[r["model"]].append(r)
    out = {}
    for model, rs in by_model.items():
        n = len(rs)
        lat = [r["latency_ms"] / 1000 for r in rs]
        amb = [r for r in rs if r["difficulty"] == "ambiguous"]
        non = [r for r in rs if r["difficulty"] != "ambiguous"]
        cost = sum(r["cost_usd"] for r in rs)
        prompt = sum(r["input_tokens"] + r["cache_creation_input_tokens"] + r["cache_read_input_tokens"] for r in rs)
        out[model] = {
            "label": LABELS.get(model, model), "n": n, "correct": sum(r["correct"] for r in rs),
            "correct_strict": sum(r["correct_strict"] for r in rs),
            "by_difficulty": {d: [sum(r["correct"] for r in rs if r["difficulty"] == d),
                                  sum(1 for r in rs if r["difficulty"] == d)] for d in DIFFS},
            "clarify_recall": [sum(r["asked_clarification"] for r in amb), len(amb)],
            "unneeded_clarifications": [sum(r["asked_clarification"] for r in non), len(non)],
            "cost_total": cost, "cost_per_q": cost / n if n else 0,
            "latency_median_s": statistics.median(lat) if lat else 0,
            "latency_p90_s": sorted(lat)[max(0, int(round(0.9 * len(lat))) - 1)] if lat else 0,
            "api_calls_mean": statistics.mean(r["api_calls"] for r in rs) if rs else 0,
            "output_tokens_mean": statistics.mean(r["output_tokens"] for r in rs) if rs else 0,
            "cache_read_share": (sum(r["cache_read_input_tokens"] for r in rs) / prompt) if prompt else 0,
            "escalated": sum(1 for r in rs if r.get("escalated")),
            "errors": sum(1 for r in rs if r["status"] == "error"),
        }
    return out


def markdown(s: dict[str, dict]) -> str:
    order = [m for m in ["claude-haiku-5-5", "haiku", "claude-sonnet-5-5", "sonnet", "claude-opus-5-5", "opus", "auto"] if m in s]
    order += [m for m in s if m not in order]
    lines = ["| Model | Accuracy | Strict grader | Easy | Medium | Hard | Ambiguous | Cost / question | Median latency | Total cost |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for m in order:
        x = s[m]
        bd = x["by_difficulty"]
        lines.append(
            f"| {x['label']} | **{x['correct']}/{x['n']} ({pct(x['correct'], x['n'])})** | "
            f"{x['correct_strict']}/{x['n']} | "
            + " | ".join(f"{bd[d][0]}/{bd[d][1]}" for d in DIFFS)
            + f" | ${x['cost_per_q']:.4f} | {x['latency_median_s']:.1f} s | ${x['cost_total']:.2f} |"
        )
    lines += ["", "| Model | Asked when ambiguous | Asked when not needed | Mean API calls | Mean output tokens | Prompt tokens read from cache | p90 latency | Escalated | Errors |",
              "|---|---|---|---|---|---|---|---|---|"]
    for m in order:
        x = s[m]
        lines.append(
            f"| {x['label']} | {x['clarify_recall'][0]}/{x['clarify_recall'][1]} | "
            f"{x['unneeded_clarifications'][0]}/{x['unneeded_clarifications'][1]} | {x['api_calls_mean']:.1f} | "
            f"{x['output_tokens_mean']:.0f} | {100 * x['cache_read_share']:.0f}% | {x['latency_p90_s']:.1f} s | {x['escalated']} | {x['errors']} |"
        )
    return "\n".join(lines) + "\n"


def failures(recs: list[dict]) -> str:
    rows = ["| Model | Question | Corrected grader | Strict grader |", "|---|---|---|---|"]
    for r in sorted(recs, key=lambda r: (r["model"], r["id"])):
        if not r["correct"] or not r["correct_strict"]:
            why = "correct" if r["correct"] else (r["reason"] or r["error"] or "").replace("|", "/")
            strict = "correct" if r["correct_strict"] else (r.get("reason_strict") or r["reason"] or "").replace("|", "/")
            rows.append(f"| {LABELS.get(r['model'], r['model'])} | {r['id']}: {r['question']} | {why} | {strict} |")
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("docs/results"))
    args = ap.parse_args(argv)
    recs = load(args.dirs)
    s = summarise(recs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    md = "# Benchmark results\n\nRuns: " + ", ".join(d.name for d in args.dirs) + "\n\n" + markdown(s)
    md += "\n## Misses\n\n" + failures(recs)
    args.out.with_suffix(".md").write_text(md, encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
