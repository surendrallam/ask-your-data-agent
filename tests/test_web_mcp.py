import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

from askdata.agent import Agent
from askdata.costs import CostLedger
from askdata.mcp_server import build_server
from askdata.web import create_app
from fakes import ScriptedClient, final, resp, tool

SQL = "SELECT COUNT(*) AS customers FROM Customer"


def web(script, tmp_path):
    return TestClient(create_app(lambda: Agent(ScriptedClient(script), ledger=CostLedger(tmp_path / "c.csv"))))


def test_page_and_health(tmp_path):
    c = web([], tmp_path)
    page = c.get("/")
    assert page.status_code == 200 and "Ask your data" in page.text
    assert "http" not in page.text.split("<script>")[1]  # no external calls from the page
    assert c.get("/api/health").json() == {"status": "ok"}


def test_ask_returns_answer_sql_table(tmp_path):
    c = web([resp(tool("run_sql", sql=SQL)), final(SQL, "59 customers.")], tmp_path)
    data = c.post("/api/ask", json={"question": "How many customers?"}).json()
    assert data["status"] == "answer" and data["rows"] == [[59]] and data["sql"] == SQL
    assert "trace" not in data


def test_clarify_then_reply(tmp_path):
    c = web([resp(tool("ask_clarification", question="Which metric?", options=["Revenue", "Units"])),
             resp(tool("run_sql", sql=SQL)), final(SQL)], tmp_path)
    first = c.post("/api/ask", json={"question": "Best?"}).json()
    assert first["status"] == "clarify" and first["clarification"]["options"] == ["Revenue", "Units"]
    second = c.post("/api/reply", json={"conversation_id": first["conversation_id"], "reply": "Revenue"}).json()
    assert second["status"] == "answer"


def test_errors(tmp_path):
    c = web([], tmp_path)
    assert c.post("/api/ask", json={"question": ""}).status_code == 422
    assert c.post("/api/reply", json={"conversation_id": "nope", "reply": "x"}).status_code == 404


def test_missing_key_is_503(tmp_path, monkeypatch):
    c = TestClient(create_app(lambda: Agent(ledger=CostLedger(tmp_path / "c.csv"))))
    r = c.post("/api/ask", json={"question": "How many customers?"})
    assert r.status_code == 503 and "ASKDATA_ANTHROPIC_KEY" in r.json()["detail"]


def _texts(result):
    return "".join(getattr(c, "text", "") for c in result.content)


def test_mcp_tools_in_process():
    from mcp import Client

    async def run():
        async with Client(build_server()) as client:
            names = sorted(t.name for t in (await client.list_tools()).tools)
            ok = await client.call_tool("run_sql", {"sql": SQL})
            bad = await client.call_tool("run_sql", {"sql": "DELETE FROM Customer"})
            gl = await client.call_tool("search_glossary", {"query": "active customer"})
            schema = await client.call_tool("describe_schema", {})
            return names, ok, bad, gl, schema

    names, ok, bad, gl, schema = asyncio.run(run())
    assert names == ["describe_schema", "run_sql", "search_glossary"]
    assert not ok.is_error and json.loads(_texts(ok))["rows"] == [[59]]
    assert bad.is_error and "Only SELECT" in _texts(bad)
    assert "active customer" in _texts(gl)
    assert "Invoice (412 rows)" in _texts(schema)


def test_mcp_over_stdio_with_legacy_handshake():
    """What Claude Desktop does: launch the command and talk over stdin/stdout."""
    from mcp import Client, StdioServerParameters

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "askdata.mcp_server"])
        async with Client(params, mode="legacy") as client:
            tools = (await client.list_tools()).tools
            res = await client.call_tool("run_sql", {"sql": SQL})
            return tools, res

    tools, res = asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert {t.name for t in tools} == {"describe_schema", "run_sql", "search_glossary"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    assert json.loads(_texts(res))["rows"] == [[59]]
