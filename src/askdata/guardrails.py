"""SQL guardrails: one read-only SELECT, nothing else.

Two layers:
1. validate_sql() - a quote- and comment-aware scan that rejects anything
   but a single SELECT / WITH ... SELECT statement.
2. db.connect_readonly() - the connection itself is read-only (mode=ro,
   PRAGMA query_only) and a SQLite authorizer denies every action except
   reading tables and calling functions. Layer 2 holds even if layer 1
   misses something.
"""

from __future__ import annotations

import re

from . import config


class GuardrailError(ValueError):
    """The SQL was rejected before or during execution."""


_WORD = re.compile(r"[A-Za-z_]+")


def _scan(sql: str) -> tuple[str, list[int]]:
    """Return (sql with comments replaced by spaces, positions of ';' outside quotes)."""
    out: list[str] = []
    semis: list[int] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j == -1:
                raise GuardrailError("Unterminated /* comment")
            out.append(" " * (j + 2 - i))
            i = j + 2
        elif c in ("'", '"', "`", "["):
            close = "]" if c == "[" else c
            j = i + 1
            while True:
                k = sql.find(close, j)
                if k == -1:
                    raise GuardrailError("Unterminated quoted string or identifier")
                # SQL escapes a quote by doubling it ('it''s'); brackets have no escape.
                if close != "]" and k + 1 < n and sql[k + 1] == close:
                    j = k + 2
                    continue
                break
            out.append(sql[i : k + 1])
            i = k + 1
        else:
            if c == ";":
                semis.append(len("".join(out)))
            out.append(c)
            i += 1
    return "".join(out), semis


def validate_sql(sql: str) -> str:
    """Return the cleaned statement if it is a single read-only query; raise otherwise."""
    if not isinstance(sql, str) or not sql.strip():
        raise GuardrailError("Empty SQL")
    if len(sql) > config.MAX_SQL_CHARS:
        raise GuardrailError(f"SQL longer than {config.MAX_SQL_CHARS} characters")
    if "\x00" in sql:
        raise GuardrailError("NUL byte in SQL")
    cleaned, _ = _scan(sql)
    body = cleaned.strip()
    # Allow trailing semicolons only.
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if _scan(body)[1]:
        raise GuardrailError("Only one SQL statement is allowed")
    m = _WORD.search(body)
    first = m.group(0).upper() if m else ""
    if first not in ("SELECT", "WITH"):
        raise GuardrailError(f"Only SELECT queries are allowed (got {first or 'nothing'})")
    return body
