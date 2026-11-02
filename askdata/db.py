"""Read-only access to the sample database, with a SQL guardrail.

Defence in depth, all enforced by SQLite itself rather than by string checks:
1. The file is opened read-only (URI mode=ro) and PRAGMA query_only is on.
2. An authorizer allows only reads: SELECT, reading columns, and a safe list of
   functions. PRAGMA, ATTACH, writes, schema changes and load_extension are denied.
3. Python's sqlite3 refuses to run more than one statement per call.
4. A progress handler stops queries that run longer than the time limit.
5. At most `max_rows` rows are fetched.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from askdata.config import DB_PATH

MAX_ROWS = 1000
TIMEOUT_S = 5.0
BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "sqlite_offset"}


class QueryError(Exception):
    """The query was blocked by the guardrail or failed in SQLite."""


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float


def _authorizer(action, arg1, arg2, _db, _trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() in BLOCKED_FUNCTIONS else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def ensure_db(path: Path = DB_PATH) -> Path:
    if not path.exists():
        from askdata.seed import build

        build(path)
    return path


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_db(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    con.set_authorizer(_authorizer)
    return con


_LEADING_COMMENTS = re.compile(r"^\s*(--[^\n]*\n\s*|/\*.*?\*/\s*)*", re.S)


def _clean(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    head = _LEADING_COMMENTS.sub("", sql).lstrip("(").lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise QueryError("Only read-only SELECT queries (optionally starting WITH) are allowed.")
    return sql


def run_query(sql: str, max_rows: int = MAX_ROWS, timeout_s: float = TIMEOUT_S, path: Path = DB_PATH) -> QueryResult:
    sql = _clean(sql)
    con = connect(path)
    deadline = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20_000)
    started = time.perf_counter()
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if "not authorized" in msg:
            msg += " (the guardrail allows read-only SELECT queries only)"
        elif "interrupted" in msg:
            msg = f"query stopped after {timeout_s:.0f}s; simplify it or add filters"
        raise QueryError(msg) from None
    except sqlite3.ProgrammingError as e:
        raise QueryError(str(e)) from None
    except sqlite3.Warning as e:  # e.g. "You can only execute one statement at a time."
        raise QueryError(str(e)) from None
    finally:
        con.close()
    truncated = len(rows) > max_rows
    return QueryResult(sql, columns, rows[:max_rows], truncated, (time.perf_counter() - started) * 1000)


def fmt_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return f"{v:.1f}"
        return f"{v:.2f}" if abs(v) >= 100 else f"{v:.6g}"
    return str(v)


def table_text(result: QueryResult, limit: int = 50) -> str:
    """Compact pipe table for the model."""
    lines = [" | ".join(result.columns)]
    for row in result.rows[:limit]:
        lines.append(" | ".join(fmt_value(v) for v in row))
    shown = min(limit, len(result.rows))
    total = f"{len(result.rows)}{'+' if result.truncated else ''}"
    footer = f"({total} rows" + (f"; first {shown} shown" if len(result.rows) > shown else "") + ")"
    return "\n".join(lines + [footer])
