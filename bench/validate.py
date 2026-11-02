"""Free pre-flight: send every scripted request through the live token counter.

The oracle client supplies the answers (gold SQL), so nothing is generated
and nothing is billed. Each request the agent builds is also passed to
POST /v1/messages/count_tokens, which is free (rate-limited) and validates
the request shape: tools, strict schemas, system blocks, cache_control,
thinking and output_config, and the tool_use / tool_result history.
It also returns the exact input-token count of each request.

Used by: python -m bench.run_bench --dry-run --validate-with-api
"""

from __future__ import annotations

import time
from typing import Any

from askdata.costs import CallUsage, CostLedger

from .oracle import OracleMessages

COUNT_KEYS = ("model", "system", "tools", "thinking", "output_config", "cache_control", "tool_choice")


def _plain_block(b: Any) -> dict[str, Any]:
    if isinstance(b, dict):
        return b
    t = getattr(b, "type", None)
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if t == "text":
        return {"type": "text", "text": b.text}
    if t == "thinking":
        return {"type": "thinking", "thinking": b.thinking, "signature": b.signature}
    raise TypeError(f"unexpected block {t!r}")


def _plain_messages(messages: list[dict[str, Any]], keep_thinking: bool) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            blocks = [_plain_block(b) for b in content]
            if not keep_thinking:
                blocks = [b for b in blocks if b.get("type") != "thinking"]
            content = blocks
        out.append({"role": m["role"], "content": content})
    return out


class ApiValidatingClient:
    """Oracle answers; real count_tokens for validation and exact prompt sizes."""

    def __init__(self, real_client: Any, ledger: CostLedger, run_id: str) -> None:
        self.real = real_client
        self.oracle = OracleMessages()
        self.ledger = ledger
        self.run_id = run_id
        self.records: list[dict[str, Any]] = []
        self.messages = self
        self.question_id = ""

    def _count(self, kw: dict[str, Any], keep_thinking: bool) -> int:
        params = {k: kw[k] for k in COUNT_KEYS if k in kw}
        params["messages"] = _plain_messages(kw["messages"], keep_thinking)
        return self.real.messages.count_tokens(**params).input_tokens

    def create(self, **kw: Any) -> Any:
        t0 = time.perf_counter()
        mode, error = "as-sent", ""
        try:
            n = self._count(kw, keep_thinking=True)
        except Exception as e:  # the scripted thinking blocks carry fake signatures
            error = f"{type(e).__name__}: {str(e)[:200]}"
            mode = "thinking-stripped"
            n = self._count(kw, keep_thinking=False)
        latency = (time.perf_counter() - t0) * 1000
        self.ledger.record(model=kw["model"], usage=CallUsage(input_tokens=n), latency_ms=latency,
                           stop_reason="count_tokens", run_id=self.run_id, purpose="free-check",
                           question_id=self.question_id, billable=False)
        self.records.append({"model": kw["model"], "question_id": self.question_id, "turn": len(kw["messages"]),
                             "input_tokens": n, "mode": mode, "first_error": error})
        return self.oracle.create(**kw)
