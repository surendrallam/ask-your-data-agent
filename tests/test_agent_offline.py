"""Agent loop behaviour with a scripted client: no API calls, no key needed."""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from askdata.agent import Agent, cost_of
from askdata.config import MODELS
from tests.conftest import FakeClient, response, text, tool_use

SQL = "SELECT COUNT(*) AS orders FROM orders WHERE status = 'completed'"
CHART_NONE = {"type": "none", "x": "", "y": "", "title": ""}


def submit(qid="q1", confidence="high", chart=None, answer="There were N completed orders."):
    return tool_use("submit_answer", answer=answer, query_id=qid, assumptions=["completed orders only"],
                    chart=chart or CHART_NONE, confidence=confidence)


def test_answer_on_one_model():
    client = FakeClient([
        response(tool_use("run_sql", sql=SQL, purpose="count orders")),
        response(submit()),
    ])
    r = Agent(client=client).ask("How many completed orders?", config="haiku")
    assert r.status == "answered"
    assert r.sql == SQL and r.columns == ["orders"] and r.rows[0][0] > 0
    assert r.model_used == "haiku" and not r.escalated
    assert r.api_calls == 2 and r.cost_usd > 0
    first = client.calls[0]
    assert first["endpoint"] == "messages"  # Haiku has no server-side fallback
    assert first["model"] == "claude-haiku-5-5"
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert all(t["strict"] for t in first["tools"])
    assert "temperature" not in first and "thinking" not in first


def test_sonnet_and_opus_use_server_side_fallback():
    for key in ("sonnet", "opus"):
        client = FakeClient([response(tool_use("run_sql", sql=SQL, purpose="count")), response(submit())])
        r = Agent(client=client).ask("How many completed orders?", config=key)
        assert r.status == "answered"
        call = client.calls[0]
        assert call["endpoint"] == "beta" and call["fallbacks"] == "default"
        assert call["betas"] == ["server-side-fallback-2026-07-01"]
        assert call["model"] == MODELS[key].model_id


def test_fallback_parameter_rejected_retries_without_it():
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.BadRequestError("fallbacks: not supported", response=httpx2.Response(400, request=req), body=None)
    client = FakeClient([err, response(tool_use("run_sql", sql=SQL, purpose="count")), response(submit())])
    agent = Agent(client=client)
    r = agent.ask("How many completed orders?", config="sonnet")
    assert r.status == "answered"
    assert [c["endpoint"] for c in client.calls] == ["beta", "messages", "messages"]
    assert agent.server_fallback is False


def test_low_confidence_escalates_and_history_is_append_only():
    client = FakeClient([
        response(tool_use("run_sql", sql=SQL, purpose="count")),
        response(text("Not sure about the definition."), submit(confidence="low")),
        response(tool_use("run_sql", sql=SQL + " AND order_date >= '2025-04-01'", purpose="recheck")),
        response(submit(qid="q2", confidence="high")),
    ])
    r = Agent(client=client).ask("How many completed orders since FY2026 began?", config="auto")
    assert r.status == "answered" and r.escalated and r.escalation_reason == "low confidence"
    assert r.model_used == "sonnet" and r.query_id == "q2"
    assert set(r.usage) == {"haiku", "sonnet"}
    haiku_last, sonnet_first = client.calls[1], client.calls[2]
    assert sonnet_first["model"] == "claude-sonnet-5-5"
    # Sonnet sees exactly Haiku's history, then the closing tool results, then an operator note.
    prior = haiku_last["messages"]
    assert sonnet_first["messages"][: len(prior)] == prior
    tail = sonnet_first["messages"][len(prior):]
    assert tail[0]["role"] == "assistant"
    assert tail[1]["role"] == "user" and {b["type"] for b in tail[1]["content"]} == {"tool_result"}
    open_ids = {b.id for b in tail[0]["content"] if getattr(b, "type", "") == "tool_use"}
    assert {b["tool_use_id"] for b in tail[1]["content"]} == open_ids
    assert tail[2]["role"] == "system" and "low confidence" in tail[2]["content"]


def test_refusal_on_haiku_hands_over_to_sonnet():
    client = FakeClient([
        response(stop="refusal", category="general_harms"),
        response(tool_use("run_sql", sql=SQL, purpose="count")),
        response(submit()),
    ])
    r = Agent(client=client).ask("How many completed orders?", config="auto")
    assert r.status == "answered" and r.escalation_reason == "refusal"
    msgs = client.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "system"]  # the refused turn is discarded


def test_repeated_sql_errors_escalate():
    bad = "SELECT nope FROM orders"
    client = FakeClient([
        response(tool_use("run_sql", sql=bad, purpose="try")),
        response(tool_use("run_sql", sql=bad, purpose="try again")),
        response(tool_use("run_sql", sql=bad, purpose="third try")),
        response(tool_use("run_sql", sql=SQL, purpose="fixed")),
        response(submit(qid="q1")),
        response(submit(qid="q1")),
    ])
    r = Agent(client=client).ask("How many completed orders?", config="auto")
    assert r.escalated and r.escalation_reason == "3 SQL errors"
    assert r.status == "answered" and r.model_used == "sonnet"


def test_unknown_query_id_is_returned_as_an_error_then_fixed():
    client = FakeClient([
        response(submit(qid="q9")),
        response(tool_use("run_sql", sql=SQL, purpose="count")),
        response(submit(qid="q1")),
    ])
    r = Agent(client=client).ask("How many completed orders?", config="haiku")
    assert r.status == "answered"
    err_block = client.calls[1]["messages"][-1]["content"][0]
    assert err_block["is_error"] and "q9" in err_block["content"]


def test_chart_columns_are_checked():
    chart = {"type": "bar", "x": "region", "y": "net_revenue", "title": "t"}
    client = FakeClient([
        response(tool_use("run_sql", sql=SQL, purpose="count")),
        response(submit(chart=chart)),
        response(submit(chart={"type": "bar", "x": "orders", "y": "orders", "title": "Orders"})),
    ])
    r = Agent(client=client).ask("How many completed orders?", config="haiku")
    assert r.status == "answered" and r.chart["type"] == "bar"


def test_clarifying_question():
    client = FakeClient([
        response(tool_use("run_sql", sql="SELECT rep_name FROM sales_reps WHERE rep_name LIKE '%Priya%'", purpose="lookup")),
        response(tool_use("ask_clarifying_question", question="Which Priya?", options=["Priya Sharma", "Priya Nair"])),
    ])
    r = Agent(client=client).ask("What were Priya's sales last quarter?", config="auto")
    assert r.status == "clarify" and r.clarify_options == ["Priya Sharma", "Priya Nair"]
    assert not r.escalated


def test_text_only_reply_is_nudged_then_fails_over():
    client = FakeClient([
        response(text("The answer is 42."), stop="end_turn"),
        response(text("Still 42."), stop="end_turn"),
        response(tool_use("run_sql", sql=SQL, purpose="count")),
        response(submit()),
    ])
    r = Agent(client=client).ask("How many completed orders?", config="auto")
    assert r.escalation_reason == "no answer" and r.status == "answered"


def test_api_error_is_reported_not_raised():
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=req), body=None)
    r = Agent(client=FakeClient([err])).ask("How many completed orders?", config="haiku")
    assert r.status == "failed" and "AuthenticationError" in r.error


@pytest.mark.parametrize("key,expected", [
    ("haiku", (1000 * 0.10 + 200 * 0.50 + 4000 * 0.125 + 6000 * 0.01) / 1e6),
    ("sonnet", (1000 * 2 + 200 * 10 + 4000 * 2.5 + 6000 * 0.2) / 1e6),
    ("opus", (1000 * 4 + 200 * 20 + 4000 * 5 + 6000 * 0.2) / 1e6),
])
def test_cost_math(key, expected):
    usage = response(inp=1000, out=200, cw=4000, cr=6000).usage
    assert cost_of(MODELS[key], usage) == pytest.approx(expected)


def test_haiku_long_context_rate_card():
    usage = response(inp=150_000, out=1000).usage
    assert cost_of(MODELS["haiku"], usage) == pytest.approx((150_000 * 0.50 + 1000 * 2.50) / 1e6)
