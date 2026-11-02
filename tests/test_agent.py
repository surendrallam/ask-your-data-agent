import csv

import pytest

from askdata import config
from askdata.agent import Agent, MissingAPIKey
from askdata.costs import BudgetExceeded, CostLedger
from fakes import ScriptedClient, final, resp, text, tool, usage

REV_BY_COUNTRY = "SELECT BillingCountry AS country, ROUND(SUM(Total), 2) AS revenue FROM Invoice GROUP BY 1 ORDER BY 2 DESC LIMIT 3"


def agent(client, tmp_path, **kw):
    return Agent(client, ledger=CostLedger(tmp_path / "costs.csv"), **kw)


def test_happy_path_and_request_shape(tmp_path):
    client = ScriptedClient([
        resp(tool("search_glossary", query="revenue")),
        resp(tool("run_sql", sql=REV_BY_COUNTRY)),
        final(REV_BY_COUNTRY, "USA leads with $523.06.", "bar", "country", "revenue"),
    ])
    res = agent(client, tmp_path).ask("Top 3 countries by revenue?")
    assert res.status == "answer"
    assert res.rows[0] == ["USA", 523.06] and res.columns == ["country", "revenue"]
    assert res.chart_svg and "<svg" in res.chart_svg
    assert res.models_tried == [config.HAIKU] and not res.escalated
    assert res.api_calls == 3

    req = client.requests[0]
    assert req["model"] == config.HAIKU
    assert req["cache_control"] == {"type": "ephemeral"}  # automatic caching for the conversation
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}  # fixed breakpoint on the system prompt
    assert [t["name"] for t in req["tools"]] == ["search_glossary", "run_sql", "ask_clarification", "final_answer"]
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in req["tools"])
    assert req["thinking"] == {"type": "adaptive"} and req["output_config"] == {"effort": config.EFFORT}
    for banned in ("temperature", "top_p", "top_k", "tool_choice"):
        assert banned not in req

    rows = list(csv.DictReader((tmp_path / "costs.csv").open()))
    assert len(rows) == 3 and rows[0]["model"] == config.HAIKU and rows[0]["cache_read_input_tokens"] == "2000"


def test_history_is_append_only_and_thinking_blocks_are_passed_back(tmp_path):
    client = ScriptedClient([resp(tool("run_sql", sql="SELECT 1 AS x")), final("SELECT 1 AS x")])
    agent(client, tmp_path).ask("one?")
    first, second = client.requests[0]["messages"], client.requests[1]["messages"]
    assert second[: len(first)] == first
    assert second[1]["role"] == "assistant" and second[1]["content"][0].type == "thinking"


def test_hard_question_routes_to_sonnet(tmp_path):
    client = ScriptedClient([resp(tool("run_sql", sql="SELECT 1 AS x")), final("SELECT 1 AS x")])
    res = agent(client, tmp_path).ask("By what percentage did revenue grow from 2024 to 2025?")
    assert client.requests[0]["model"] == config.SONNET and res.route_reasons


def test_failed_first_sql_escalates_to_sonnet(tmp_path):
    client = ScriptedClient([
        resp(tool("run_sql", sql="SELECT nope FROM Invoice")),
        resp(tool("run_sql", sql="SELECT COUNT(*) AS n FROM Invoice")),
        final("SELECT COUNT(*) AS n FROM Invoice", "412 invoices."),
    ])
    res = agent(client, tmp_path).ask("How many invoices?")
    assert res.status == "answer" and res.rows == [[412]]
    assert res.escalated and res.escalation_reason == "first SQL failed"
    assert [r["model"] for r in client.requests] == [config.HAIKU, config.SONNET, config.SONNET]
    assert "earlier attempt failed" in client.requests[1]["messages"][0]["content"]
    assert res.models_tried == [config.HAIKU, config.SONNET]


def test_pinned_model_retries_instead_of_escalating(tmp_path):
    client = ScriptedClient([
        resp(tool("run_sql", sql="SELECT nope FROM Invoice")),
        resp(tool("run_sql", sql="SELECT COUNT(*) AS n FROM Invoice")),
        final("SELECT COUNT(*) AS n FROM Invoice"),
    ])
    res = agent(client, tmp_path).ask("How many invoices?", model="opus")
    assert res.status == "answer" and not res.escalated and res.sql_errors == 1
    assert {r["model"] for r in client.requests} == {config.OPUS}
    err = client.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] is True and "no such column" in err["content"]


def test_guardrail_rejection_reaches_the_model(tmp_path):
    client = ScriptedClient([resp(tool("run_sql", sql="DELETE FROM Invoice")), resp(tool("run_sql", sql="SELECT 1 AS x")), final("SELECT 1 AS x")])
    res = agent(client, tmp_path).ask("delete everything", model="haiku")
    assert res.status == "answer"
    assert "Rejected" in client.requests[1]["messages"][-1]["content"][0]["content"]


def test_clarification_round_trip(tmp_path):
    client = ScriptedClient([
        resp(tool("ask_clarification", question="By revenue or units?", options=["Revenue", "Units"])),
        resp(tool("run_sql", sql=REV_BY_COUNTRY)),
        final(REV_BY_COUNTRY),
    ])
    a = agent(client, tmp_path)
    res = a.ask("Which country is best?")
    assert res.status == "clarify" and res.clarification["options"] == ["Revenue", "Units"]
    res = a.reply(res.conversation_id, "Revenue")
    assert res.status == "answer" and res.clarifications_asked == 1
    last_user = client.requests[1]["messages"][-1]
    assert last_user["content"][0]["type"] == "tool_result" and last_user["content"][0]["content"] == "Revenue"


def test_second_clarification_is_refused(tmp_path):
    client = ScriptedClient([
        resp(tool("ask_clarification", question="Which metric?", options=["A", "B"])),
        resp(tool("ask_clarification", question="And which year?", options=["2024", "2025"])),
        resp(tool("run_sql", sql="SELECT 1 AS x")),
        final("SELECT 1 AS x"),
    ])
    a = agent(client, tmp_path)
    res = a.reply(a.ask("best?").conversation_id, "A")
    assert res.status == "answer"
    refused = client.requests[2]["messages"][-1]["content"][0]
    assert refused["is_error"] and "already asked" in refused["content"]


def test_final_answer_with_bad_sql_is_bounced(tmp_path):
    client = ScriptedClient([final("SELECT nope"), final("SELECT 1 AS x")])
    res = agent(client, tmp_path).ask("x?", model="sonnet")
    assert res.status == "answer" and res.rows == [[1]]
    assert "final_answer SQL failed" in client.requests[1]["messages"][-1]["content"][0]["content"]


def test_refusal_is_an_error(tmp_path):
    client = ScriptedClient([resp(text(""), stop_reason="refusal")])
    res = agent(client, tmp_path).ask("x?", model="opus")
    assert res.status == "error" and "declined" in res.error


def test_turn_limit_escalates_then_fails(tmp_path):
    client = ScriptedClient(lambda kw: resp(tool("search_glossary", query="revenue")))
    res = agent(client, tmp_path, max_turns=2).ask("revenue?")
    assert res.status == "error" and res.escalated and res.api_calls == 4


def test_budget_stops_before_the_call(tmp_path):
    client = ScriptedClient([final("SELECT 1 AS x")])
    a = Agent(client, ledger=CostLedger(tmp_path / "c.csv", budget_usd=0.0001))
    with pytest.raises(BudgetExceeded):
        a.ask("x?", model="opus")
    assert client.requests == []


def test_costs_are_summed(tmp_path):
    client = ScriptedClient([final("SELECT 1 AS x", "1")])
    client.script[0].usage = usage(inp=100, out=1000, write=500, read=3000)
    res = agent(client, tmp_path).ask("x?", model="sonnet")
    expected = (100 * 2 + 1000 * 10 + 500 * 2.5 + 3000 * 0.1) / 1e6
    assert res.cost_usd == pytest.approx(expected)


def test_missing_key_never_builds_a_client(tmp_path):
    a = Agent(ledger=CostLedger(tmp_path / "c.csv"))
    with pytest.raises(MissingAPIKey):
        a.ask("How many customers?")
