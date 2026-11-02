"""A scripted stand-in for the Anthropic client. Costs nothing, calls nothing.

It answers each benchmark question with the gold SQL, asking for
clarification first on ambiguous questions. Used by the free dry run
(python -m bench.run_bench --dry-run) and by the tests, to exercise the
agent loop, runner, grader and report end to end without the API.
"""

from __future__ import annotations

import itertools
import json
from types import SimpleNamespace
from typing import Any

from .build_expected import load_questions

_ids = itertools.count(1)


def _usage() -> SimpleNamespace:
    return SimpleNamespace(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0,
                           cache_read_input_tokens=0, cache_creation=None)


def tool_use(name: str, args: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_ids):04d}", name=name, input=args)


def response(*blocks: Any, stop_reason: str = "tool_use", usage: Any = None) -> SimpleNamespace:
    thinking = SimpleNamespace(type="thinking", thinking="", signature="sig")
    return SimpleNamespace(content=[thinking, *blocks], stop_reason=stop_reason, usage=usage or _usage())


class OracleMessages:
    def __init__(self) -> None:
        self.by_question = {q["question"]: q for q in load_questions()}
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        msgs = kwargs["messages"]
        first = msgs[0]["content"]
        q = self.by_question.get(first.split("\n\n")[0].strip())
        if q is None:
            return response(SimpleNamespace(type="text", text="I can't answer that."), stop_reason="end_turn")
        last = msgs[-1]["content"]
        results = last if isinstance(last, list) else []
        names = {}
        for m in msgs:
            if m["role"] == "assistant":
                for b in m["content"]:
                    if getattr(b, "type", None) == "tool_use":
                        names[b.id] = b.name
        last_tools = [names.get(r.get("tool_use_id")) for r in results]
        if len(msgs) == 1:
            if q["difficulty"] == "ambiguous":
                return response(tool_use("ask_clarification", {"question": "Which metric do you mean?",
                                                               "options": ["Revenue", "Units"]}))
            return response(tool_use("run_sql", {"sql": q["gold_sql"]}))
        if "ask_clarification" in last_tools:
            return response(tool_use("run_sql", {"sql": q["gold_sql"]}))
        if "run_sql" in last_tools:
            payload = json.loads(results[-1]["content"])
            return response(tool_use("final_answer", {
                "answer": f"The answer is {payload['rows'][0]}.", "sql": q["gold_sql"],
                "chart_type": "none", "chart_x": "", "chart_y": ""}))
        return response(SimpleNamespace(type="text", text="Done."), stop_reason="end_turn")


class OracleClient:
    def __init__(self) -> None:
        self.messages = OracleMessages()
