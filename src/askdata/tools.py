"""Tool definitions shared by the Claude agent and the MCP server, plus their executor."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .db import QueryResult, run_query
from .glossary import Glossary
from .guardrails import GuardrailError


def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SEARCH_GLOSSARY_DESC = (
    "Look up business terms in the company glossary (e.g. revenue, active customer, region, "
    "VIP customer, last quarter, best-selling). Returns definitions with SQL snippets. "
    "Use the definition exactly when the question uses one of these terms."
)
RUN_SQL_DESC = (
    "Run one read-only SQLite SELECT (or WITH ... SELECT) against the store database and get "
    f"columns and up to {config.MODEL_ROW_LIMIT} rows. Anything else is rejected. Rows are capped at "
    f"{config.ROW_LIMIT} and queries time out after {config.QUERY_TIMEOUT_S:g}s."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_glossary",
        "description": SEARCH_GLOSSARY_DESC,
        "input_schema": _schema({"query": {"type": "string", "description": "Term or phrase to look up"}}),
        "strict": True,
    },
    {
        "name": "run_sql",
        "description": RUN_SQL_DESC,
        "input_schema": _schema({"sql": {"type": "string", "description": "A single SQLite SELECT statement"}}),
        "strict": True,
    },
    {
        "name": "ask_clarification",
        "description": (
            "Ask the user one short clarifying question when the request can reasonably mean "
            "materially different things and the glossary does not settle it. The user's reply "
            "comes back as this tool's result."
        ),
        "input_schema": _schema(
            {
                "question": {"type": "string", "description": "The clarifying question"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Two to four concrete interpretations the user can pick from",
                },
            }
        ),
        "strict": True,
    },
    {
        "name": "final_answer",
        "description": (
            "Deliver the answer. Call it once the SQL result answers the question. The SQL is "
            "re-run to produce the table and chart the user sees."
        ),
        "input_schema": _schema(
            {
                "answer": {
                    "type": "string",
                    "description": "One to three plain-English sentences with the key numbers. No SQL.",
                },
                "sql": {"type": "string", "description": "The exact SELECT that produced the numbers"},
                "chart_type": {"type": "string", "enum": ["bar", "line", "none"]},
                "chart_x": {"type": "string", "description": "Result column for the x axis, or empty"},
                "chart_y": {"type": "string", "description": "Numeric result column for the y axis, or empty"},
            }
        ),
        "strict": True,
    },
]

TOOL_NAMES = [t["name"] for t in TOOLS]


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    query: QueryResult | None = None
    data: dict[str, Any] = field(default_factory=dict)


class ToolExecutor:
    """Runs search_glossary and run_sql; validates final_answer's SQL."""

    def __init__(self, db_path: Path | str = config.DB_PATH, glossary: Glossary | None = None) -> None:
        self.db_path = db_path
        self.glossary = glossary or Glossary()

    def search_glossary(self, query: str) -> ToolOutcome:
        hits = self.glossary.search(query)
        if not hits:
            return ToolOutcome(json.dumps({"matches": [], "note": "No glossary entry matched."}))
        for h in hits:
            h.pop("score", None)
            h.pop("synonyms", None)
        return ToolOutcome(json.dumps({"as_of_date": self.glossary.as_of_date, "matches": hits}, ensure_ascii=False))

    def run_sql(self, sql: str) -> ToolOutcome:
        try:
            res = run_query(sql, self.db_path)
        except GuardrailError as e:
            return ToolOutcome(f"Rejected: {e}", is_error=True, data={"error": str(e)})
        except sqlite3.Error as e:
            return ToolOutcome(f"SQLite error: {e}", is_error=True, data={"error": str(e)})
        return ToolOutcome(json.dumps(res.to_model_payload(), ensure_ascii=False, default=str), query=res)

    def execute(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        if name == "search_glossary":
            return self.search_glossary(str(args.get("query", "")))
        if name == "run_sql":
            return self.run_sql(str(args.get("sql", "")))
        return ToolOutcome(f"Unknown tool {name!r}", is_error=True)
