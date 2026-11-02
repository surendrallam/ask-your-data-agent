"""MCP server: the same guarded database tools for Claude Desktop or any MCP client.

Run:  python -m askdata.mcp_server   (stdio transport)

Claude does the reasoning in the client; this server only exposes read-only
tools, the glossary and the analyst instructions.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from askdata.db import QueryError, run_query, table_text
from askdata.glossary import knowledge_base
from askdata.prompts import build_system_prompt
from askdata.schema import FACTS, TABLES, describe

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

server = MCPServer(
    name="ask-your-data",
    instructions=(
        "Read-only access to the Trailhead Goods sample sales database (SQLite). Use the glossary "
        "definitions for business terms; they are binding. Load the 'analyst' prompt for the full "
        "instructions.\n" + FACTS
    ),
)


@server.tool(annotations=READ_ONLY)
def list_tables() -> str:
    """List the tables in the sales database with a one-line description of each."""
    return "\n".join(f"{name}: {spec['about']}" for name, spec in TABLES.items())


@server.tool(annotations=READ_ONLY)
def describe_table(table: str) -> str:
    """Describe one table: its columns, types, allowed values and joins."""
    text = describe(table)
    if text is None:
        return f"No table named {table!r}. Tables: {', '.join(TABLES)}"
    return text


@server.tool(annotations=READ_ONLY)
def search_glossary(query: str) -> str:
    """Look up business definitions (net revenue, churn, CAC, margin...) and verified example queries."""
    return knowledge_base().lookup(query, k_terms=3, k_examples=2)


@server.tool(annotations=READ_ONLY)
def run_sql(sql: str) -> str:
    """Run one read-only SQLite SELECT query. Returns up to 200 rows. Writes are blocked."""
    try:
        return table_text(run_query(sql), limit=200)
    except QueryError as e:
        return f"SQL error: {e}"


@server.prompt()
def analyst() -> str:
    """Instructions for answering business questions with these tools."""
    return build_system_prompt().replace("submit_answer", "your final answer").replace(
        "ask_clarifying_question", "a clarifying question"
    )


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
