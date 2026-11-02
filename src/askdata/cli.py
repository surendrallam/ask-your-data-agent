"""Command line: ask questions, run guarded SQL, search the glossary.

    askdata ask "Which country brought in the most revenue in 2025?"
    askdata ask "Who is our best customer?" --model sonnet
    askdata sql "SELECT COUNT(*) FROM Customer"
    askdata glossary "active customer"
    askdata check-key
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import config


def _print_result(res, as_json: bool) -> None:
    if as_json:
        d = res.to_dict()
        d.pop("chart_svg", None)
        print(json.dumps(d, indent=2, default=str))
        return
    if res.status == "answer":
        print(res.answer, "\n")
        print("SQL:\n" + res.sql, "\n")
        if res.columns:
            print(" | ".join(res.columns))
            for row in res.rows[:20]:
                print(" | ".join("" if v is None else str(v) for v in row))
            if len(res.rows) > 20:
                print(f"... {len(res.rows)} rows")
    elif res.status == "error":
        print(f"Error: {res.error}")
    tried = " -> ".join(res.models_tried) or res.model
    print(
        f"\n[{tried} | {res.api_calls} calls | ${res.cost_usd:.4f} | {res.latency_ms / 1000:.1f}s"
        f"{' | escalated: ' + res.escalation_reason if res.escalated else ''}]"
    )


def check_api() -> int:
    """Free calls only: model lookup and token counting. Logged to the ledger at $0. Never prints the key."""
    import time

    from .agent import MissingAPIKey, make_client
    from .costs import CallUsage, CostLedger
    from .prompts import system_prompt
    from .tools import TOOLS

    try:
        client = make_client()
    except MissingAPIKey as e:
        print(e, file=sys.stderr)
        return 2
    ledger = CostLedger()
    system = [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]
    counts = {}
    for model in (config.HAIKU, config.SONNET, config.OPUS):
        t0 = time.perf_counter()
        info = client.models.retrieve(model)
        ledger.record(model=model, usage=CallUsage(), latency_ms=(time.perf_counter() - t0) * 1000,
                      stop_reason="models.retrieve", purpose="free-check", billable=False)
        t0 = time.perf_counter()
        n = client.messages.count_tokens(
            model=model, system=system, tools=TOOLS, thinking={"type": "adaptive"},
            output_config={"effort": config.EFFORT}, cache_control={"type": "ephemeral"},
            messages=[{"role": "user", "content": "How many customers do we have?"}],
        ).input_tokens
        ledger.record(model=model, usage=CallUsage(input_tokens=n), latency_ms=(time.perf_counter() - t0) * 1000,
                      stop_reason="count_tokens", purpose="free-check", billable=False)
        counts[model] = n
        print(f"{model}: reachable ({getattr(info, 'display_name', model)}); first request = {n} input tokens")
    out = config.COSTS_CSV.parent / "token-counts.json"
    out.write_text(json.dumps({"first_request_input_tokens": counts}, indent=2), encoding="utf-8")
    print(f"API spend so far: ${ledger.total_usd:.4f} (these checks are free)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="askdata")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="Ask a question in plain English (calls the Claude API)")
    a.add_argument("question")
    a.add_argument("--model", default="auto", help="auto | haiku | sonnet | opus | full model id")
    a.add_argument("--json", action="store_true")
    a.add_argument("--chart", help="write the chart SVG to this path")
    s = sub.add_parser("sql", help="Run one read-only SELECT through the guardrails (no API call)")
    s.add_argument("sql")
    g = sub.add_parser("glossary", help="Search the business glossary (no API call)")
    g.add_argument("query")
    sub.add_parser("schema", help="Print the system prompt and schema the model sees")
    sub.add_parser("check-key", help=f"Say whether {config.API_KEY_ENV} is set (prints yes/no only)")
    sub.add_parser("check-api", help="Free check: key reaches each model; exact prompt token counts (count_tokens is free)")
    args = ap.parse_args(argv)

    if args.cmd == "check-key":
        print("yes" if os.environ.get(config.API_KEY_ENV) else "no")
        return 0
    if args.cmd == "check-api":
        return check_api()
    if args.cmd == "schema":
        from .prompts import system_prompt

        print(system_prompt())
        return 0
    if args.cmd == "glossary":
        from .glossary import Glossary

        print(json.dumps(Glossary().search(args.query), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "sql":
        from .tools import ToolExecutor

        out = ToolExecutor().run_sql(args.sql)
        print(out.content)
        return 1 if out.is_error else 0

    from .agent import Agent, MissingAPIKey

    agent = Agent(purpose="cli")
    try:
        res = agent.ask(args.question, model=args.model)
        while res.status == "clarify":
            c = res.clarification or {}
            print(c.get("question", ""))
            opts = c.get("options") or []
            for i, o in enumerate(opts, 1):
                print(f"  {i}. {o}")
            if not sys.stdin.isatty():
                break
            reply = input("> ").strip()
            if reply.isdigit() and 1 <= int(reply) <= len(opts):
                reply = opts[int(reply) - 1]
            res = agent.reply(res.conversation_id, reply)
    except MissingAPIKey as e:
        print(e, file=sys.stderr)
        return 2
    _print_result(res, args.json)
    if args.chart and res.chart_svg:
        with open(args.chart, "w", encoding="utf-8") as f:
            f.write(res.chart_svg)
    print(f"Total API spend so far: ${agent.ledger.total_usd:.4f}", file=sys.stderr)
    return 0 if res.status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
