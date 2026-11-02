"""The agent loop: Claude API tool use with prompt caching, model escalation and cost tracking.

One question runs as a manual tool loop:
  user question (+ retrieved glossary entries)
  -> Claude calls search_glossary / run_sql as often as it needs
  -> Claude ends with submit_answer (cites a query id) or ask_clarifying_question.

Config "auto" starts on Haiku 5.5 and hands the same conversation to Sonnet 5.5
when the question proves hard: Haiku refused, ran out of steps, hit repeated SQL
errors or submitted a low-confidence answer. The history is append-only, so
Sonnet keeps Haiku's queries and results.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import anthropic

from askdata.config import DEFAULT_EFFORT, MODELS, ModelSpec, make_client
from askdata.prompts import build_system_prompt, build_user_message
from askdata.tools import TERMINAL_TOOLS, TOOLS, ToolSession

MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
ESCALATE_AFTER_SQL_ERRORS = 3
FIRST_MODEL_TURNS = 8
ROWS_RETURNED = 200

HANDOVER_NOTE = (
    "Operator note: a faster model worked on this question first and stopped ({reason}). You are now "
    "answering it. Review the work above: check each definition against the glossary and each query "
    "against the question, fix or rerun queries as needed, then call submit_answer, or "
    "ask_clarifying_question if the rules call for it. Do not reuse the earlier answer without checking it."
)


def cost_of(spec: ModelSpec, usage: Any) -> float:
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    rates = (spec.input, spec.output, spec.cache_write, spec.cache_read)
    if spec.long_context_from and spec.long_rates and inp + cw + cr > spec.long_context_from:
        rates = spec.long_rates
    return (inp * rates[0] + out * rates[1] + cw * rates[2] + cr * rates[3]) / 1_000_000


@dataclass
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class AgentResult:
    question: str
    config: str
    status: str = "failed"  # answered | clarify | failed
    answer: str | None = None
    assumptions: list[str] = field(default_factory=list)
    confidence: str | None = None
    query_id: str | None = None
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    chart: dict | None = None
    clarify_question: str | None = None
    clarify_options: list[str] = field(default_factory=list)
    model_used: str | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    fallback_used: bool = False
    error: str | None = None
    usage: dict[str, ModelUsage] = field(default_factory=dict)
    latency_s: float = 0.0
    trace: list[dict] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.usage.values())

    @property
    def api_calls(self) -> int:
        return sum(u.calls for u in self.usage.values())

    def cache_hit_ratio(self) -> float:
        read = sum(u.cache_read_tokens for u in self.usage.values())
        total = sum(u.input_tokens + u.cache_write_tokens + u.cache_read_tokens for u in self.usage.values())
        return read / total if total else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cost_usd"] = round(self.cost_usd, 6)
        d["api_calls"] = self.api_calls
        d["cache_hit_ratio"] = round(self.cache_hit_ratio(), 4)
        return d


@dataclass
class Outcome:
    kind: str  # answer | clarify | refusal | max_turns | no_answer | max_tokens | error
    tool_use_id: str | None = None
    args: dict = field(default_factory=dict)
    pending_results: list[dict] = field(default_factory=list)
    error: str | None = None


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


class Agent:
    def __init__(self, client: Any = None, effort: str = DEFAULT_EFFORT, max_turns: int = 12,
                 server_fallback: bool = True) -> None:
        self.client = client if client is not None else make_client()
        self.effort = effort
        self.max_turns = max_turns
        self.server_fallback = server_fallback
        # Tools + system prompt are identical on every request, so they form one cached prefix.
        self.system = [{"type": "text", "text": build_system_prompt(), "cache_control": {"type": "ephemeral"}}]

    # ------------------------------------------------------------------ public
    def ask(self, question: str, config: str = "auto") -> AgentResult:
        if config not in ("auto", *MODELS):
            raise ValueError(f"config must be auto, haiku, sonnet or opus, not {config!r}")
        started = time.perf_counter()
        result = AgentResult(question=question, config=config)
        session = ToolSession()
        messages: list[dict] = [{"role": "user", "content": build_user_message(question)}]
        plan = ["haiku", "sonnet"] if config == "auto" else [config]
        for i, key in enumerate(plan):
            spec = MODELS[key]
            first_of_two = len(plan) > 1 and i == 0
            outcome = self._run(spec, messages, session, result,
                                FIRST_MODEL_TURNS if first_of_two else self.max_turns)
            result.model_used = key
            reason = self._escalation_reason(outcome, session) if first_of_two else None
            if reason:
                result.escalated, result.escalation_reason = True, reason
                self._hand_over(messages, outcome, reason)
                continue
            self._finish(result, outcome, session)
            break
        result.latency_s = round(time.perf_counter() - started, 2)
        result.trace = session.trace
        return result

    # ---------------------------------------------------------------- internals
    def _create(self, spec: ModelSpec, messages: list[dict]):
        kwargs = dict(
            model=spec.model_id,
            max_tokens=MAX_TOKENS,
            system=self.system,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},  # automatic breakpoint on the growing conversation
        )
        if spec.server_fallback and self.server_fallback:
            try:
                # Server-side refusal fallback: if the model declines on a policy classifier,
                # the API retries the same request on a fallback model within the same call.
                return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            except anthropic.BadRequestError as e:
                if "fallback" not in str(e).lower():
                    raise
                self.server_fallback = False  # not available for this key or model; carry on without it
        return self.client.messages.create(**kwargs)

    def _record(self, result: AgentResult, spec: ModelSpec, response: Any) -> None:
        u = response.usage
        mu = result.usage.setdefault(spec.key, ModelUsage())
        mu.calls += 1
        mu.input_tokens += getattr(u, "input_tokens", 0) or 0
        mu.output_tokens += getattr(u, "output_tokens", 0) or 0
        mu.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
        mu.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        mu.cost_usd += cost_of(spec, u)
        if any(getattr(b, "type", None) == "fallback" for b in response.content or []):
            result.fallback_used = True

    def _run(self, spec: ModelSpec, messages: list[dict], session: ToolSession, result: AgentResult,
             max_turns: int) -> Outcome:
        nudged = False
        for _ in range(max_turns):
            try:
                response = self._create(spec, messages)
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                return Outcome("error", error=f"{type(e).__name__}: {getattr(e, 'message', e)}")
            self._record(result, spec, response)
            if response.stop_reason == "refusal":
                category = getattr(getattr(response, "stop_details", None), "category", None)
                return Outcome("refusal", error=f"refusal ({category})")
            if response.stop_reason == "max_tokens":
                return Outcome("max_tokens", error="response hit max_tokens")
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                if nudged:
                    return Outcome("no_answer", error="model stopped without submit_answer")
                messages.append({"role": "user", "content":
                                 "Please finish by calling submit_answer, or ask_clarifying_question if the rules call for it."})
                nudged = True
                continue
            results: list[dict] = []
            terminal = None
            for tu in tool_uses:
                args = tu.input if isinstance(tu.input, dict) else {}
                if tu.name in TERMINAL_TOOLS:
                    if terminal is not None:
                        results.append(_tool_result(tu.id, "Only one final call per turn; this one was ignored.", True))
                        continue
                    problem = session.check_terminal(tu.name, args)
                    if problem:
                        results.append(_tool_result(tu.id, problem, True))
                    else:
                        terminal = (tu, args)
                    continue
                content, is_error = session.execute(tu.name, args)
                results.append(_tool_result(tu.id, content, is_error))
            if terminal is not None:
                tu, args = terminal
                kind = "answer" if tu.name == "submit_answer" else "clarify"
                return Outcome(kind, tool_use_id=tu.id, args=args, pending_results=results)
            messages.append({"role": "user", "content": results})
        return Outcome("max_turns", error=f"no answer after {max_turns} model calls")

    @staticmethod
    def _escalation_reason(outcome: Outcome, session: ToolSession) -> str | None:
        if outcome.kind not in ("answer", "clarify"):
            return outcome.kind.replace("_", " ")
        if outcome.kind == "answer" and outcome.args.get("confidence") == "low":
            return "low confidence"
        if session.sql_errors >= ESCALATE_AFTER_SQL_ERRORS:
            return f"{session.sql_errors} SQL errors"
        return None

    @staticmethod
    def _hand_over(messages: list[dict], outcome: Outcome, reason: str) -> None:
        """Close any open tool calls, then add an operator note for the next model. Append-only."""
        last = messages[-1]
        if last["role"] == "assistant":
            open_calls = [b for b in last["content"] if getattr(b, "type", None) == "tool_use"]
            if open_calls:
                answered = {r["tool_use_id"] for r in outcome.pending_results}
                results = list(outcome.pending_results)
                for tu in open_calls:
                    if tu.id not in answered:
                        results.append(_tool_result(tu.id, "Received; a second model will review it before it goes to the user."))
                messages.append({"role": "user", "content": results})
            else:
                messages.append({"role": "user", "content": "Please continue."})
        messages.append({"role": "system", "content": HANDOVER_NOTE.format(reason=reason)})

    @staticmethod
    def _finish(result: AgentResult, outcome: Outcome, session: ToolSession) -> None:
        if outcome.kind == "answer":
            a = outcome.args
            qr = session.queries[a["query_id"]]
            chart = a.get("chart") or {}
            result.status = "answered"
            result.answer = a.get("answer", "").strip()
            result.assumptions = [s for s in a.get("assumptions", []) if str(s).strip()]
            result.confidence = a.get("confidence")
            result.query_id = a["query_id"]
            result.sql = qr.sql
            result.columns = qr.columns
            result.rows = [list(r) for r in qr.rows[:ROWS_RETURNED]]
            result.row_count = len(qr.rows)
            result.truncated = qr.truncated or len(qr.rows) > ROWS_RETURNED
            result.chart = chart if chart.get("type") in ("bar", "line") else None
        elif outcome.kind == "clarify":
            result.status = "clarify"
            result.clarify_question = outcome.args.get("question", "").strip()
            result.clarify_options = [o for o in outcome.args.get("options", []) if str(o).strip()]
        else:
            result.status = "failed"
            result.error = outcome.error or outcome.kind
