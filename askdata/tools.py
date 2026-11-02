"""Tool definitions and their execution, shared by the agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field

from askdata.db import QueryError, QueryResult, run_query, table_text
from askdata.glossary import KnowledgeBase, knowledge_base

TERMINAL_TOOLS = ("submit_answer", "ask_clarifying_question")


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }


TOOLS: list[dict] = [
    {
        "name": "search_glossary",
        "description": (
            "Search the business glossary and verified example queries. Use it for any business term "
            "you have not been given a definition for (for example churn, CAC, margin, attainment, "
            "repeat rate). Returns definitions with SQL patterns. Glossary definitions are binding."
        ),
        "strict": True,
        "input_schema": _obj({"query": {"type": "string", "description": "Term or question to look up."}}),
    },
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SQLite SELECT query on the sales database. You see up to 50 rows; the "
            "result gets an id (q1, q2, ...) that submit_answer cites. Errors come back as messages: "
            "fix the query and try again."
        ),
        "strict": True,
        "input_schema": _obj({
            "sql": {"type": "string", "description": "A single SQLite SELECT (or WITH ... SELECT) statement."},
            "purpose": {"type": "string", "description": "Five to ten words on what this query checks."},
        }),
    },
    {
        "name": "ask_clarifying_question",
        "description": (
            "Ask the user one short question when the request is ambiguous in a way the glossary does not "
            "settle (see the rules in the system prompt). This ends your turn."
        ),
        "strict": True,
        "input_schema": _obj({
            "question": {"type": "string", "description": "One short question for the user."},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Two to four concrete choices."},
        }),
    },
    {
        "name": "submit_answer",
        "description": (
            "Give the final answer. This ends your turn. query_id must name the run_sql result that holds "
            "the numbers in your answer; the user sees that table, its SQL and the chart you choose."
        ),
        "strict": True,
        "input_schema": _obj({
            "answer": {"type": "string", "description": "One to three plain sentences with the key numbers."},
            "query_id": {"type": "string", "description": "Id of the run_sql result behind the answer, e.g. q3."},
            "assumptions": {
                "type": "array", "items": {"type": "string"},
                "description": "Definitions or defaults you applied; empty if none.",
            },
            "chart": _obj({
                "type": {"type": "string", "enum": ["none", "bar", "line"]},
                "x": {"type": "string", "description": "Column for the x axis or categories; empty if none."},
                "y": {"type": "string", "description": "Numeric column to plot; empty if none."},
                "title": {"type": "string", "description": "Short chart title; empty if none."},
            }),
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        }),
    },
]


@dataclass
class ToolSession:
    """Per-question tool state: query results by id, a trace, error counts."""

    kb: KnowledgeBase = field(default_factory=knowledge_base)
    rows_for_model: int = 50
    queries: dict[str, QueryResult] = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    sql_errors: int = 0

    def execute(self, name: str, args: dict) -> tuple[str, bool]:
        """Run a non-terminal tool. Returns (content for the model, is_error)."""
        if name == "search_glossary":
            query = str(args.get("query", ""))
            text = self.kb.lookup(query, k_terms=3, k_examples=2)
            self.trace.append({"tool": name, "query": query})
            return text, False
        if name == "run_sql":
            sql = str(args.get("sql", ""))
            purpose = str(args.get("purpose", ""))
            try:
                result = run_query(sql)
            except QueryError as e:
                self.sql_errors += 1
                self.trace.append({"tool": name, "purpose": purpose, "sql": sql, "error": str(e)})
                return f"SQL error: {e}", True
            qid = f"q{len(self.queries) + 1}"
            self.queries[qid] = result
            self.trace.append({
                "tool": name, "id": qid, "purpose": purpose, "sql": result.sql,
                "rows": len(result.rows), "truncated": result.truncated, "ms": round(result.elapsed_ms, 1),
            })
            return f"Result {qid}:\n{table_text(result, self.rows_for_model)}", False
        return f"Unknown tool '{name}'. Available: search_glossary, run_sql, submit_answer, ask_clarifying_question.", True

    def check_terminal(self, name: str, args: dict) -> str | None:
        """Validate a terminal call. Returns an error message, or None when it is usable."""
        if name == "ask_clarifying_question":
            if not str(args.get("question", "")).strip():
                return "The question is empty."
            return None
        if name == "submit_answer":
            qid = str(args.get("query_id", "")).strip()
            if qid not in self.queries:
                known = ", ".join(self.queries) or "none yet"
                return f"query_id '{qid}' is not a run_sql result (known: {known}). Run the final query, then submit."
            chart = args.get("chart") or {}
            if chart.get("type") in ("bar", "line"):
                cols = self.queries[qid].columns
                missing = [c for c in (chart.get("x"), chart.get("y")) if c not in cols]
                if missing:
                    return f"Chart columns {missing} are not in {qid} (columns: {cols}). Fix the chart or use type none."
            if not str(args.get("answer", "")).strip():
                return "The answer is empty."
            return None
        return f"{name} is not a terminal tool."
