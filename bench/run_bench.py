"""Run the benchmark: every question on each model, graded against the database.

Paid run (asks for nothing interactively; refuses without --confirm-spend):
    python -m bench.run_bench --models haiku,sonnet,opus --budget 20 --confirm-spend
Smoke test:
    python -m bench.run_bench --models haiku --ids E04,M03,A01 --purpose smoke --budget 0.5 --confirm-spend
Free dry run with the scripted oracle client (no API calls):
    python -m bench.run_bench --dry-run

Results go to runs/bench/<run_id>/results.jsonl; every API call is in runs/costs.csv.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from askdata import config
from askdata.agent import Agent
from askdata.costs import BudgetExceeded, CostLedger

from .build_expected import EXPECTED, load_questions
from .grade import grade

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs" / "bench"
GENERIC_REPLY = "Use your best judgment with the glossary definitions."
DEFAULT_DEADLINE = "2026-11-03T17:30:00Z"  # 3 Nov 2026, 11 pm IST: last moment for paid runs


def run_question(agent: Agent, q: dict, model: str, gold_rows: list) -> dict:
    t0 = time.perf_counter()
    res = agent.ask(q["question"], model=model, question_id=q["id"])
    asked = res.status == "clarify"
    clarification = res.clarification
    if asked:
        res = agent.reply(res.conversation_id, q.get("clarification_reply") or GENERIC_REPLY)
    wall_ms = (time.perf_counter() - t0) * 1000
    pred = res.rows if res.status == "answer" else None
    matched, reason = grade(q, gold_rows, pred)
    ambiguous = q["difficulty"] == "ambiguous"
    correct = matched and (asked or not ambiguous)
    if ambiguous and not asked:
        reason = "did not ask a clarifying question" + (" (guess matched)" if matched else "")
    return {
        "id": q["id"], "difficulty": q["difficulty"], "question": q["question"], "model": model,
        "models_tried": res.models_tried, "escalated": res.escalated, "escalation_reason": res.escalation_reason,
        "status": res.status, "correct": correct, "result_matched": matched, "reason": reason,
        "asked_clarification": asked, "clarification": clarification,
        "unneeded_clarification": asked and not ambiguous,
        "answer": res.answer, "sql": res.sql, "columns": res.columns, "rows": res.rows[:20],
        "error": res.error, "api_calls": res.api_calls, "sql_errors": res.sql_errors,
        "cost_usd": round(res.cost_usd, 6), "latency_ms": round(res.latency_ms), "wall_ms": round(wall_ms),
        "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
        "cache_creation_input_tokens": res.cache_creation_input_tokens,
        "cache_read_input_tokens": res.cache_read_input_tokens,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="haiku,sonnet,opus", help="comma list: haiku, sonnet, opus, auto")
    ap.add_argument("--ids", help="comma list of question ids (default: all)")
    ap.add_argument("--purpose", default="bench")
    ap.add_argument("--budget", type=float, help="cap for this run in USD (on top of the global cap)")
    ap.add_argument("--deadline", default=DEFAULT_DEADLINE, help="refuse paid calls after this UTC time")
    ap.add_argument("--resume", help="run id to continue (skips questions already done)")
    ap.add_argument("--dry-run", action="store_true", help="use the scripted oracle client; no API calls")
    ap.add_argument("--confirm-spend", action="store_true", help="required for paid runs")
    args = ap.parse_args(argv)

    questions = load_questions()
    if args.ids:
        wanted = args.ids.split(",")
        questions = [q for q in questions if q["id"] in wanted]
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    if args.dry_run:
        from .oracle import OracleClient

        client, purpose = OracleClient(), "dry-run"
        ledger_path = ROOT / "runs" / "scratch" / "dry-run-costs.csv"
    else:
        if not args.confirm_spend:
            sys.exit("Paid run refused: pass --confirm-spend once the spend is approved.")
        deadline = datetime.fromisoformat(args.deadline.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= deadline:
            sys.exit(f"Paid run refused: past the deadline {args.deadline}.")
        client, purpose = None, args.purpose
        ledger_path = config.COSTS_CSV

    run_id = args.resume or f"{purpose}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = RUNS / run_id if not args.dry_run else ROOT / "runs" / "scratch" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["model"], r["id"]))

    ledger = CostLedger(ledger_path, run_budget_usd=args.budget)
    start_total = ledger.total_usd
    meta = {"run_id": run_id, "purpose": purpose, "models": models, "question_ids": [q["id"] for q in questions],
            "effort": config.EFFORT, "max_tokens": config.MAX_TOKENS, "started_utc": datetime.now(timezone.utc).isoformat(),
            "pricing_source": config.PRICING_SOURCE, "pricing_checked": config.PRICING_CHECKED}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"run {run_id}: {len(questions)} questions x {models}; spend so far ${start_total:.4f}")

    stopped = ""
    with results_path.open("a", encoding="utf-8") as out:
        for model in models:
            agent = Agent(client, ledger=ledger, purpose=purpose, run_id=run_id)
            for q in questions:
                if (model, q["id"]) in done:
                    continue
                if not args.dry_run and datetime.now(timezone.utc) >= deadline:
                    stopped = "deadline reached"
                    break
                try:
                    rec = run_question(agent, q, model, expected[q["id"]]["rows"])
                except BudgetExceeded as e:
                    stopped = str(e)
                    break
                out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                out.flush()
                mark = "ok " if rec["correct"] else "BAD"
                print(f"{mark} {model:7s} {q['id']} ${rec['cost_usd']:.4f} {rec['latency_ms'] / 1000:5.1f}s "
                      f"run ${ledger.run_totals.get(run_id, 0):.4f} total ${ledger.total_usd:.4f}  {rec['reason']}")
            if stopped:
                break
    print(f"run spend ${ledger.total_usd - start_total:.4f}; total API spend ${ledger.total_usd:.4f}")
    if stopped:
        print(f"STOPPED: {stopped}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
