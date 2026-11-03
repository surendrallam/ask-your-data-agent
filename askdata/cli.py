"""Command line: ask a question, run guarded SQL, or rebuild the sample database.

  askdata ask "What was net revenue last quarter?" --model auto
  askdata sql "SELECT COUNT(*) FROM orders"
  askdata seed
"""

from __future__ import annotations

import argparse
import json
import sys

from askdata.config import DEFAULT_EFFORT, MissingKeyError


def _print_table(columns: list[str], rows: list[list], limit: int = 20) -> None:
    from askdata.db import fmt_value

    cells = [[fmt_value(v) for v in r] for r in rows[:limit]]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    for r in cells:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(r, widths)))
    if len(rows) > limit:
        print(f"  ... {len(rows)} rows")


def cmd_ask(args: argparse.Namespace) -> int:
    from askdata.agent import Agent

    try:
        agent = Agent(effort=args.effort)
    except MissingKeyError as e:
        print(e, file=sys.stderr)
        return 2
    from askdata.ledger import BudgetExceeded

    try:
        r = agent.ask(args.question, config=args.model)
    except BudgetExceeded as e:
        print(e, file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(r.to_dict(), indent=2, default=str))
        return 0 if r.status != "failed" else 1
    if r.status == "answered":
        print(f"\n{r.answer}\n")
        if r.assumptions:
            print("Assumptions: " + "; ".join(r.assumptions) + "\n")
        _print_table(r.columns, r.rows)
        print(f"\nSQL:\n{r.sql}\n")
    elif r.status == "clarify":
        print(f"\nNeeds clarification: {r.clarify_question}")
        for o in r.clarify_options:
            print(f"  - {o}")
        print()
    else:
        print(f"\nFailed: {r.error}\n")
    used = r.model_used + (f" (escalated from haiku: {r.escalation_reason})" if r.escalated else "")
    print(f"Model: {used} | {r.api_calls} API calls | {r.latency_s:.1f}s | ${r.cost_usd:.4f} | "
          f"cache hits {r.cache_hit_ratio():.0%}")
    return 0 if r.status != "failed" else 1


def cmd_sql(args: argparse.Namespace) -> int:
    from askdata.db import QueryError, run_query

    try:
        res = run_query(args.sql)
    except QueryError as e:
        print(f"Blocked or failed: {e}", file=sys.stderr)
        return 1
    _print_table(res.columns, [list(r) for r in res.rows], limit=args.limit)
    return 0


def cmd_seed(_args: argparse.Namespace) -> int:
    from askdata.seed import main as seed_main

    sys.argv = sys.argv[:1]
    seed_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="askdata", description="Ask your data: a Claude analyst over a sample sales database.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="ask a question in plain English")
    a.add_argument("question")
    a.add_argument("--model", default="auto", choices=["auto", "haiku", "sonnet", "opus"])
    a.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    a.add_argument("--json", action="store_true", help="print the full result as JSON")
    a.set_defaults(func=cmd_ask)
    s = sub.add_parser("sql", help="run a read-only SQL query through the guardrail")
    s.add_argument("sql")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_sql)
    sd = sub.add_parser("seed", help="rebuild data/sales.db")
    sd.set_defaults(func=cmd_seed)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
