"""MCP server exposing the same database tools to Claude Desktop (stdio).

No API key needed: the MCP client (e.g. Claude Desktop) is the model.
Tools: describe_schema, search_glossary, run_sql. All SQL passes the same
guardrails as the web app: one read-only SELECT, row limit, timeout.

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"ask-your-data": {"command": "/abs/path/.venv/bin/askdata-mcp"}}}
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import config
from .db import describe_schema as _describe_schema
from .tools import RUN_SQL_DESC, SEARCH_GLOSSARY_DESC, ToolExecutor

INSTRUCTIONS = (
    "Answer questions about a digital music store's sales database. Call describe_schema once, "
    "use search_glossary for business terms (revenue, active customer, region, ...), then run_sql "
    "with a single read-only SELECT. The data ends on 2025-12-22; treat that as today. Ask the "
    "user one clarifying question when a request is ambiguous (e.g. 'best' with no metric)."
)


def build_server(db_path: Path | str = config.DB_PATH) -> MCPServer:
    server = MCPServer(name="ask-your-data", instructions=INSTRUCTIONS, version="0.1.0")
    executor = ToolExecutor(db_path)
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(description="Tables, columns, row counts, foreign keys and data notes.", annotations=ro)
    def describe_schema() -> str:
        return _describe_schema(db_path)

    @server.tool(description=SEARCH_GLOSSARY_DESC, annotations=ro)
    def search_glossary(query: str) -> str:
        return executor.search_glossary(query).content

    @server.tool(description=RUN_SQL_DESC, annotations=ro)
    def run_sql(sql: str) -> str:
        out = executor.run_sql(sql)
        if out.is_error:
            raise ValueError(out.content)
        payload = json.loads(out.content)
        payload["sql"] = out.query.sql
        return json.dumps(payload, ensure_ascii=False)

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
