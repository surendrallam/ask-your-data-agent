"""Benchmark runner: every question x every model config, graded by execution match.

  python -m bench.run --dry-run                       # check reference SQL, no API calls
  python -m bench.run --configs haiku --only q01,c01  # smoke test
  python -m bench.run --configs haiku,sonnet,opus,auto --concurrency 4 --budget 40

Results go to bench/results/<tag>/: results.jsonl (one line per run, resumable),
summary.json and summary.md. Re-running the same command skips finished runs.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml

from askdata.config import CONFIGS, DEFAULT_EFFORT, MODELS, ROOT
from bench.grade import grade, reference_rows

QUESTIONS = ROOT / "bench" / "questions.yaml"
RESULTS = ROOT / "bench" / "results"


def load_questions(path: Path = QUESTIONS, only: list[str] | None = None) -> list[dict]:
    qs = yaml.safe_load(path.read_text())
    if only:
        wanted = set(only)
        qs = [q for q in qs if q["id"] in wanted]
    return qs


def dry_run(questions: list[dict]) -> int:
    bad = 0
    for q in questions:
        if q.get("expect") == "clarify":
            print(f"{q['id']}  (clarify) {q['question']}  -- {q['why']}")
            continue
        try:
            rows = reference_rows(q["sql"])
            print(f"{q['id']}  {q['question']}\n      -> {list(rows[:6])}{' ...' if len(rows) > 6 else ''}")
            if not rows:
                bad += 1
                print("      !! no rows")
        except Exception as e:  # noqa: BLE001 - report every broken reference
            bad += 1
            print(f"{q['id']}  !! {e}")
    print(f"\n{len(questions)} questions, {bad} problems")
    return 1 if bad else 0


def compact_trace(trace: list[dict]) -> list[dict]:
    out = []
    for t in trace:
        item = {k: t[k] for k in ("tool", "id", "purpose", "rows", "error", "query") if k in t}
        if "sql" in t:
            item["sql"] = t["sql"]
        out.append(item)
    return out


def record(q: dict, config: str, effort: str, rep: int, r) -> dict:
    d = r.to_dict()
    g = grade(q, d)
    return {
        "qid": q["id"], "config": config, "effort": effort, "rep": rep, "tags": q.get("tags", []),
        "expect": q.get("expect", "answer"), "question": q["question"],
        "passed": g.passed, "reason": g.reason,
        "status": d["status"], "answer": d["answer"], "sql": d["sql"], "query_id": d["query_id"],
        "assumptions": d["assumptions"], "confidence": d["confidence"],
        "clarify_question": d["clarify_question"], "clarify_options": d["clarify_options"],
        "model_used": d["model_used"], "escalated": d["escalated"], "escalation_reason": d["escalation_reason"],
        "fallback_used": d["fallback_used"], "error": d["error"],
        "cost_usd": d["cost_usd"], "latency_s": d["latency_s"], "api_calls": d["api_calls"],
        "cache_hit_ratio": d["cache_hit_ratio"], "usage": d["usage"], "trace": compact_trace(d["trace"]),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def summarize(rows: list[dict]) -> dict:
    by_cfg: dict[tuple, list[dict]] = {}
    for r in rows:
        by_cfg.setdefault((r["config"], r["effort"]), []).append(r)
    out = {}
    for (cfg, effort), rs in sorted(by_cfg.items(), key=lambda kv: (CONFIGS.index(kv[0][0]) if kv[0][0] in CONFIGS else 9, kv[0][1])):
        ans = [r for r in rs if r["expect"] == "answer"]
        clar = [r for r in rs if r["expect"] == "clarify"]
        lat = sorted(r["latency_s"] for r in rs)
        out[f"{cfg}/{effort}"] = {
            "config": cfg, "effort": effort, "runs": len(rs),
            "accuracy": round(sum(r["passed"] for r in rs) / len(rs), 4) if rs else None,
            "answer_accuracy": round(sum(r["passed"] for r in ans) / len(ans), 4) if ans else None,
            "clarify_accuracy": round(sum(r["passed"] for r in clar) / len(clar), 4) if clar else None,
            "unneeded_clarifications": sum(1 for r in ans if r["status"] == "clarify"),
            "failed_runs": sum(1 for r in rs if r["status"] == "failed"),
            "escalation_rate": round(sum(r["escalated"] for r in rs) / len(rs), 4) if rs else None,
            "cost_total_usd": round(sum(r["cost_usd"] for r in rs), 4),
            "cost_per_question_usd": round(statistics.mean(r["cost_usd"] for r in rs), 5) if rs else None,
            "latency_p50_s": round(statistics.median(lat), 2) if lat else None,
            "latency_p90_s": round(lat[min(len(lat) - 1, int(0.9 * len(lat)))], 2) if lat else None,
            "api_calls_mean": round(statistics.mean(r["api_calls"] for r in rs), 2) if rs else None,
            "cache_hit_ratio_mean": round(statistics.mean(r["cache_hit_ratio"] for r in rs), 4) if rs else None,
        }
    return out


LABEL = {"haiku": "Haiku 5.5", "sonnet": "Sonnet 5.5", "opus": "Opus 5.5", "auto": "Auto (Haiku → Sonnet)"}


def summary_markdown(summary: dict, rows: list[dict]) -> str:
    lines = [
        "| Config | Effort | Accuracy (all) | Answer accuracy | Clarify accuracy | $ / question | Total $ | p50 latency | p90 latency | API calls / q | Escalated |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summary.values():
        pct = lambda v: "–" if v is None else f"{v * 100:.1f}%"  # noqa: E731
        lines.append(
            f"| {LABEL.get(s['config'], s['config'])} | {s['effort']} | {pct(s['accuracy'])} | {pct(s['answer_accuracy'])} | "
            f"{pct(s['clarify_accuracy'])} | ${s['cost_per_question_usd']:.4f} | ${s['cost_total_usd']:.2f} | "
            f"{s['latency_p50_s']:.1f}s | {s['latency_p90_s']:.1f}s | {s['api_calls_mean']:.1f} | "
            f"{pct(s['escalation_rate']) if s['config'] == 'auto' else '–'} |"
        )
    # Per-question matrix
    keys = list(summary)
    qids = sorted({r["qid"] for r in rows})
    lines += ["", "| Question | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for qid in qids:
        cells = []
        for k in keys:
            cfg, eff = k.split("/")
            rs = [r for r in rows if r["qid"] == qid and r["config"] == cfg and r["effort"] == eff]
            cells.append("–" if not rs else " ".join("✓" if r["passed"] else "✗" for r in rs))
        lines.append(f"| {qid} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", default="haiku", help="comma list of haiku,sonnet,opus,auto")
    ap.add_argument("--effort", default=DEFAULT_EFFORT, help="comma list of effort levels")
    ap.add_argument("--questions", type=Path, default=QUESTIONS)
    ap.add_argument("--only", default="", help="comma list of question ids")
    ap.add_argument("--repeat", type=int, default=1, help="runs per question and config")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--budget", type=float, default=10.0, help="stop starting new runs past this many USD")
    ap.add_argument("--tag", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true", help="run the reference SQL only; no API calls")
    args = ap.parse_args(argv)

    questions = load_questions(args.questions, [s for s in args.only.split(",") if s])
    if args.dry_run:
        return dry_run(questions)

    from askdata.agent import Agent
    from askdata.config import MissingKeyError, make_client

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    efforts = [e.strip() for e in args.effort.split(",") if e.strip()]
    for c in configs:
        if c not in CONFIGS:
            ap.error(f"unknown config {c!r}; use {CONFIGS}")
    try:
        client = make_client(max_retries=6)
    except MissingKeyError as e:
        print(e, file=sys.stderr)
        return 2

    out_dir = RESULTS / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    done: list[dict] = []
    if results_path.exists():
        done = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]
    finished = {(r["qid"], r["config"], r["effort"], r["rep"]) for r in done}
    spent = sum(r["cost_usd"] for r in done)
    lock = threading.Lock()
    stop = threading.Event()
    print(f"Writing to {results_path} ({len(done)} runs already there, ${spent:.2f} spent so far)")

    def run_one(agent: Agent, q: dict, config: str, effort: str, rep: int) -> None:
        nonlocal spent
        if stop.is_set():
            return
        r = agent.ask(q["question"], config=config)
        rec = record(q, config, effort, rep, r)
        with lock:
            with results_path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            done.append(rec)
            spent += rec["cost_usd"]
            mark = "PASS" if rec["passed"] else "FAIL"
            used = rec["model_used"] or "-"
            print(f"[{config}/{effort}] {q['id']} {mark} {rec['status']:9s} {used:6s} "
                  f"${rec['cost_usd']:.4f} {rec['latency_s']:5.1f}s  {rec['reason'][:70]}  (total ${spent:.2f})",
                  flush=True)
            if spent >= args.budget:
                stop.set()

    started = time.time()
    for effort in efforts:
        for config in configs:
            agent = Agent(client=client, effort=effort)
            todo = [(q, rep) for rep in range(1, args.repeat + 1) for q in questions
                    if (q["id"], config, effort, rep) not in finished]
            if not todo or stop.is_set():
                continue
            # First question alone, so the cached prompt prefix exists before the parallel runs.
            run_one(agent, todo[0][0], config, effort, todo[0][1])
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(run_one, agent, q, config, effort, rep) for q, rep in todo[1:]]
                for f in as_completed(futures):
                    f.result()
    if stop.is_set():
        print(f"\nStopped: budget ${args.budget:.2f} reached.")

    summary = summarize(done)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "summary.md").write_text(summary_markdown(summary, done))
    print(f"\nDone in {time.time() - started:.0f}s. Spent ${spent:.2f} in total.\n")
    print(summary_markdown(summary, done).split("\n\n")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
