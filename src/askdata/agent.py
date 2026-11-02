"""The ask-your-data agent: a Claude tool-use loop with prompt caching and escalation.

Flow for one question:
  question -> (router picks Haiku or Sonnet) -> search_glossary / run_sql loop
  -> final_answer (SQL re-run for the table and chart) or ask_clarification.

Auto mode (model=None) starts on Haiku 5.5 unless the router marks the
question hard, and restarts on Sonnet 5.5 if Haiku's first SQL fails or it
cannot finish. A pinned model (the benchmark) never escalates.

Every API call is checked against and written to the cost ledger.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .chart import render_svg
from .costs import CallUsage, CostLedger, worst_case_cost
from .db import QueryResult
from .glossary import Glossary
from .prompts import system_prompt
from .router import classify
from .tools import TOOLS, ToolExecutor


class MissingAPIKey(RuntimeError):
    pass


def make_client() -> Any:
    """Create the Anthropic client from ASKDATA_ANTHROPIC_KEY. The key is never logged."""
    if not os.environ.get(config.API_KEY_ENV):
        raise MissingAPIKey(f"Set {config.API_KEY_ENV} to call the Claude API.")
    import anthropic

    return anthropic.Anthropic(api_key=os.environ[config.API_KEY_ENV])


@dataclass
class AgentResult:
    status: str  # "answer" | "clarify" | "error"
    question: str
    conversation_id: str
    answer: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    chart: dict[str, Any] | None = None
    chart_svg: str | None = None
    clarification: dict[str, Any] | None = None
    model: str = ""
    models_tried: list[str] = field(default_factory=list)
    route_reasons: list[str] = field(default_factory=list)
    escalated: bool = False
    escalation_reason: str = ""
    clarifications_asked: int = 0
    api_calls: int = 0
    sql_errors: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    error: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Conversation:
    id: str
    question: str
    model: str
    can_escalate: bool
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending_results: list[dict[str, Any]] = field(default_factory=list)
    pending_clarify_id: str | None = None
    pending_text_clarify: bool = False
    clarification_qa: list[tuple[str, str]] = field(default_factory=list)
    sql_attempts: int = 0
    last_ok: QueryResult | None = None
    result: AgentResult | None = None


def _block_get(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


class Agent:
    def __init__(
        self,
        client: Any | None = None,
        *,
        db_path: Path | str = config.DB_PATH,
        glossary: Glossary | None = None,
        ledger: CostLedger | None = None,
        effort: str = config.EFFORT,
        max_tokens: int = config.MAX_TOKENS,
        max_turns: int = config.MAX_TURNS,
        purpose: str = "app",
        run_id: str = "",
        auto_escalate: bool = True,
    ) -> None:
        self._client = client
        self.db_path = db_path
        self.executor = ToolExecutor(db_path, glossary)
        self.ledger = ledger or CostLedger()
        self.effort = effort
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.purpose = purpose
        self.run_id = run_id
        self.auto_escalate = auto_escalate
        self.system = [
            {"type": "text", "text": system_prompt(str(db_path)), "cache_control": {"type": "ephemeral"}}
        ]
        self._prefix_chars = len(self.system[0]["text"]) + len(str(TOOLS))
        self._conversations: dict[str, _Conversation] = {}
        self.question_id = ""

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = make_client()
        return self._client

    # ------------------------------------------------------------------ public API

    def ask(self, question: str, model: str | None = None, question_id: str = "") -> AgentResult:
        """Answer a question. model=None means auto (route, then escalate on failure)."""
        self.question_id = question_id
        model = config.resolve_model(model)
        reasons: list[str] = []
        if model is None:
            hard, reasons = classify(question)
            model = config.ESCALATION_MODEL if hard else config.DEFAULT_MODEL
            can_escalate = self.auto_escalate and model == config.DEFAULT_MODEL
        else:
            can_escalate = False
        conv = _Conversation(id=uuid.uuid4().hex[:12], question=question, model=model, can_escalate=can_escalate)
        conv.messages.append({"role": "user", "content": question})
        conv.result = AgentResult(
            status="error", question=question, conversation_id=conv.id, route_reasons=reasons
        )
        self._conversations[conv.id] = conv
        return self._run(conv)

    def reply(self, conversation_id: str, text: str) -> AgentResult:
        """Continue a conversation that is waiting on a clarifying question."""
        conv = self._conversations.get(conversation_id)
        if conv is None or (conv.pending_clarify_id is None and not conv.pending_text_clarify):
            raise KeyError(f"No conversation waiting for a reply: {conversation_id}")
        asked = conv.result.clarification or {}
        conv.clarification_qa.append((asked.get("question", ""), text))
        if conv.pending_text_clarify:
            conv.messages.append({"role": "user", "content": text})
        else:
            conv.messages.append(
                {
                    "role": "user",
                    "content": conv.pending_results
                    + [{"type": "tool_result", "tool_use_id": conv.pending_clarify_id, "content": text}],
                }
            )
        conv.pending_results, conv.pending_clarify_id, conv.pending_text_clarify = [], None, False
        conv.result.status, conv.result.clarification = "error", None
        return self._run(conv)

    # ------------------------------------------------------------------ internals

    def _call(self, conv: _Conversation) -> Any:
        res = conv.result
        prompt_est = (self._prefix_chars + len(str(conv.messages))) // 3 + 300
        self.ledger.check(worst_case_cost(conv.model, prompt_est, self.max_tokens), self.run_id or None)
        t0 = time.perf_counter()
        resp = self.client.messages.create(
            model=conv.model,
            max_tokens=self.max_tokens,
            system=self.system,
            tools=TOOLS,
            messages=conv.messages,
            cache_control={"type": "ephemeral"},
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        latency = (time.perf_counter() - t0) * 1000
        usage = CallUsage.from_usage(_block_get(resp, "usage"))
        res.api_calls += 1
        cost = self.ledger.record(
            model=conv.model,
            usage=usage,
            latency_ms=latency,
            stop_reason=_block_get(resp, "stop_reason"),
            run_id=self.run_id,
            purpose=self.purpose,
            question_id=self.question_id,
            call_no=res.api_calls,
        )
        res.cost_usd += cost
        res.input_tokens += usage.input_tokens
        res.output_tokens += usage.output_tokens
        res.cache_creation_input_tokens += usage.cache_creation_input_tokens
        res.cache_read_input_tokens += usage.cache_read_input_tokens
        res.trace.append({"event": "api_call", "model": conv.model, "latency_ms": round(latency), "cost_usd": round(cost, 6),
                          "stop_reason": _block_get(resp, "stop_reason"), **asdict(usage)})
        if conv.model not in res.models_tried:
            res.models_tried.append(conv.model)
        return resp

    def _run(self, conv: _Conversation) -> AgentResult:
        res = conv.result
        t_start = time.perf_counter()
        try:
            return self._loop(conv)
        finally:
            res.latency_ms += (time.perf_counter() - t_start) * 1000

    def _loop(self, conv: _Conversation) -> AgentResult:
        res = conv.result
        res.model = conv.model
        turns_used = sum(1 for m in conv.messages if m["role"] == "assistant")
        while turns_used < self.max_turns:
            turns_used += 1
            resp = self._call(conv)
            content = _block_get(resp, "content") or []
            conv.messages.append({"role": "assistant", "content": content})
            stop = _block_get(resp, "stop_reason")
            if stop == "refusal":
                return self._fail(conv, "The model declined this request (refusal).")
            if stop == "max_tokens":
                return self._fail(conv, "The model ran out of output tokens.", escalate_reason="max_tokens")

            tool_uses = [b for b in content if _block_get(b, "type") == "tool_use"]
            if not tool_uses:
                text = "".join(_block_get(b, "text", "") for b in content if _block_get(b, "type") == "text").strip()
                if conv.last_ok is not None:
                    return self._answer(conv, text, conv.last_ok, {})
                if text.endswith("?") and not conv.clarification_qa:
                    conv.pending_text_clarify = True
                    res.status, res.clarifications_asked = "clarify", res.clarifications_asked + 1
                    res.clarification = {"question": text, "options": []}
                    return res
                return self._fail(conv, "The model stopped without running SQL.", escalate_reason="no SQL")

            results: list[dict[str, Any]] = []
            final: tuple[str, QueryResult, dict[str, Any]] | None = None
            clarify: tuple[str, dict[str, Any]] | None = None
            first_sql_error: str | None = None
            for tu in tool_uses:
                name, tid = _block_get(tu, "name"), _block_get(tu, "id")
                args = dict(_block_get(tu, "input") or {})
                if name == "ask_clarification":
                    if conv.clarification_qa or clarify is not None:
                        results.append(self._tool_result(tid, "You already asked a clarifying question. Answer with the information you have.", True))
                    else:
                        clarify = (tid, args)
                    continue
                if name == "final_answer":
                    out = self.executor.run_sql(str(args.get("sql", "")))
                    res.trace.append({"event": "final_answer", "sql": args.get("sql", ""), "ok": not out.is_error})
                    if out.is_error:
                        results.append(self._tool_result(tid, f"final_answer SQL failed: {out.content}", True))
                    else:
                        final = (str(args.get("answer", "")), out.query, args)
                        results.append(self._tool_result(tid, "Delivered to the user."))
                    continue
                out = self.executor.execute(name, args)
                trace = {"event": name, "ok": not out.is_error, **({"sql": args.get("sql", "")} if name == "run_sql" else {"query": args.get("query", "")})}
                if out.is_error:
                    trace["error"] = out.content
                res.trace.append(trace)
                if name == "run_sql":
                    conv.sql_attempts += 1
                    if out.is_error:
                        res.sql_errors += 1
                        if conv.sql_attempts == 1:
                            first_sql_error = f"{args.get('sql', '')!r} -> {out.content}"
                    else:
                        conv.last_ok = out.query
                results.append(self._tool_result(tid, out.content, out.is_error))

            if first_sql_error and conv.can_escalate:
                return self._escalate(conv, "first SQL failed", first_sql_error)
            if final is not None:
                conv.messages.append({"role": "user", "content": results})
                return self._answer(conv, final[0], final[1], final[2])
            if clarify is not None:
                tid, args = clarify
                conv.pending_results, conv.pending_clarify_id = results, tid
                res.status, res.clarifications_asked = "clarify", res.clarifications_asked + 1
                res.clarification = {"question": str(args.get("question", "")), "options": list(args.get("options") or [])}
                return res
            conv.messages.append({"role": "user", "content": results})
        return self._fail(conv, f"No answer after {self.max_turns} model calls.", escalate_reason="turn limit")

    @staticmethod
    def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if is_error:
            block["is_error"] = True
        return block

    def _answer(self, conv: _Conversation, text: str, q: QueryResult, args: dict[str, Any]) -> AgentResult:
        res = conv.result
        res.status, res.answer, res.sql = "answer", text, q.sql
        res.columns, res.rows, res.truncated = q.columns, q.rows, q.truncated
        hint = {"type": args.get("chart_type") or None, "x": args.get("chart_x") or None, "y": args.get("chart_y") or None}
        res.chart = {k: v for k, v in hint.items() if v}
        try:
            res.chart_svg = render_svg(q.columns, q.rows, hint)
        except Exception:  # a chart is a nice-to-have; never fail the answer over it
            res.chart_svg = None
        res.model = conv.model
        return res

    def _fail(self, conv: _Conversation, message: str, escalate_reason: str = "") -> AgentResult:
        if escalate_reason and conv.can_escalate:
            return self._escalate(conv, escalate_reason, "")
        res = conv.result
        res.status, res.error, res.model = "error", message, conv.model
        return res

    def _escalate(self, conv: _Conversation, reason: str, detail: str) -> AgentResult:
        res = conv.result
        res.escalated, res.escalation_reason = True, reason
        res.trace.append({"event": "escalate", "from": conv.model, "to": config.ESCALATION_MODEL, "reason": reason})
        prompt = conv.question
        for q, a in conv.clarification_qa:
            prompt += f"\n\nClarification - you asked: {q}\nUser replied: {a}"
        if detail:
            prompt += f"\n\nNote: an earlier attempt failed. {detail}"
        new = _Conversation(id=conv.id, question=conv.question, model=config.ESCALATION_MODEL, can_escalate=False,
                            clarification_qa=list(conv.clarification_qa))
        new.messages.append({"role": "user", "content": prompt})
        new.result = res
        self._conversations[conv.id] = new
        return self._loop(new)
