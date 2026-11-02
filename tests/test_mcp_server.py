from __future__ import annotations

import asyncio

from askdata.mcp_server import server


def _text(result) -> str:
    content = getattr(result, "content", None) or []
    return "\n".join(getattr(c, "text", "") for c in content)


def test_tools_are_listed_and_read_only():
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {"list_tables", "describe_table", "search_glossary", "run_sql"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


def test_run_sql_and_guardrail_over_mcp():
    ok = asyncio.run(server.call_tool("run_sql", {"sql": "SELECT region_name FROM regions ORDER BY region_id"}))
    assert "North" in _text(ok) and "(5 rows)" in _text(ok)
    blocked = asyncio.run(server.call_tool("run_sql", {"sql": "DELETE FROM orders"}))
    assert "SQL error" in _text(blocked)


def test_describe_and_glossary():
    assert "fiscal_quarter" in _text(asyncio.run(server.call_tool("describe_table", {"table": "calendar"})))
    assert "Churned customer" in _text(asyncio.run(server.call_tool("search_glossary", {"query": "churn rate"})))
