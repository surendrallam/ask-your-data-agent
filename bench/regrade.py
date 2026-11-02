"""Re-grade saved benchmark results offline. No API calls.

Each record stores the agent's final SQL. Because the database is static,
re-running that SQL through the same guardrails reproduces the exact result
the agent saw, so grading rules can be fixed after a run and applied to
every model equally, at no cost.

    python -m bench.regrade runs/bench/<run_id> [more run dirs]

Writes graded.jsonl next to each results.jsonl with two verdicts per record:
`correct_strict` (the original rules) and `correct` (the current rules).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from askdata.db import run_query
from askdata.guardrails import GuardrailError

from .build_expected import EXPECTED, load_questions
from .grade import grade


def regrade_record(rec: dict, q: dict, gold_rows: list) -> dict:
    out = dict(rec)
    pred = None
    if rec.get("status") == "answer" and rec.get("sql"):
        try:
            pred = run_query(rec["sql"]).rows
        except (GuardrailError, Exception) as e:  # the SQL ran during the benchmark; flag if it no longer does
            out["regrade_error"] = f"{type(e).__name__}: {e}"
    ambiguous = q["difficulty"] == "ambiguous"
    asked = bool(rec.get("asked_clarification"))
    strict_ok, strict_reason = grade(q, gold_rows, pred, strict=True)
    ok, reason = grade(q, gold_rows, pred, answer_text=rec.get("answer", ""), strict=False)
    out["result_matched_strict"], out["result_matched"] = strict_ok, ok
    out["correct_strict"] = strict_ok and (asked or not ambiguous)
    out["correct"] = ok and (asked or not ambiguous)
    if ambiguous and not asked:
        reason = "did not ask a clarifying question" + (" (guess matched)" if ok else "")
        strict_reason = "did not ask a clarifying question" + (" (guess matched)" if strict_ok else "")
    out["reason"], out["reason_strict"] = reason, strict_reason
    return out


def regrade_dir(d: Path) -> list[dict]:
    qs = {q["id"]: q for q in load_questions()}
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    recs = [json.loads(line) for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    graded = [regrade_record(r, qs[r["id"]], expected[r["id"]]["rows"]) for r in recs]
    (d / "graded.jsonl").write_text("".join(json.dumps(g, ensure_ascii=False, default=str) + "\n" for g in graded),
                                    encoding="utf-8")
    return graded


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    args = ap.parse_args(argv)
    for d in args.dirs:
        g = regrade_dir(d)
        by_model: dict[str, list[dict]] = {}
        for r in g:
            by_model.setdefault(r["model"], []).append(r)
        for m, rs in by_model.items():
            print(f"{d.name} {m}: strict {sum(r['correct_strict'] for r in rs)}/{len(rs)}, "
                  f"current {sum(r['correct'] for r in rs)}/{len(rs)}")


if __name__ == "__main__":
    main()
