"""Read-only SQLite access with an authorizer, a row limit and a timeout."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .guardrails import GuardrailError, validate_sql

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class QueryTimeout(GuardrailError):
    pass


def _authorizer(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and str(arg1).lower().startswith("sqlite_") and str(arg1).lower() != "sqlite_master":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def connect_readonly(path: Path | str = config.DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    con.set_authorizer(_authorizer)
    return con


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: float
    row_limit: int = config.ROW_LIMIT
    notes: list[str] = field(default_factory=list)

    def to_model_payload(self, max_rows: int = config.MODEL_ROW_LIMIT) -> dict[str, Any]:
        shown = self.rows[:max_rows]
        payload: dict[str, Any] = {
            "columns": self.columns,
            "rows": [[_short(v) for v in r] for r in shown],
            "row_count": len(self.rows),
        }
        if self.truncated:
            payload["note"] = f"Result truncated at the {self.row_limit}-row limit; aggregate in SQL instead."
        elif len(self.rows) > max_rows:
            payload["note"] = f"Showing the first {max_rows} of {len(self.rows)} rows."
        return payload


def _short(v: Any) -> Any:
    if isinstance(v, bytes):
        return f"<blob {len(v)} bytes>"
    if isinstance(v, str) and len(v) > config.MAX_CELL_CHARS:
        return v[: config.MAX_CELL_CHARS] + "…"
    return v


def run_query(
    sql: str,
    db_path: Path | str = config.DB_PATH,
    row_limit: int = config.ROW_LIMIT,
    timeout_s: float = config.QUERY_TIMEOUT_S,
) -> QueryResult:
    """Validate and run one SELECT. Raises GuardrailError / QueryTimeout / sqlite3.Error."""
    body = validate_sql(sql)
    con = connect_readonly(db_path)
    deadline = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
    t0 = time.perf_counter()
    try:
        cur = con.execute(body)
        if cur.description is None:
            raise GuardrailError("Statement returned no result set")
        columns = [d[0] for d in cur.description]
        fetched = cur.fetchmany(row_limit + 1)
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "interrupted" in msg:
            raise QueryTimeout(f"Query exceeded the {timeout_s:g}s timeout") from None
        if "not authorized" in msg:
            raise GuardrailError("Statement not allowed: read-only SELECT queries only") from None
        raise
    except sqlite3.DatabaseError as e:
        if "not authorized" in str(e):
            raise GuardrailError("Statement not allowed: read-only SELECT queries only") from None
        raise
    finally:
        con.close()
    truncated = len(fetched) > row_limit
    rows = [list(r) for r in fetched[:row_limit]]
    return QueryResult(
        sql=body,
        columns=columns,
        rows=rows,
        truncated=truncated,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
        row_limit=row_limit,
    )


SCHEMA_NOTES = """\
Notes:
- Dates are ISO text 'YYYY-MM-DD HH:MM:SS'. Use strftime('%Y', InvoiceDate) etc.
- Invoices run from 2021-01-01 to 2025-12-22. Treat 2025-12-22 as "today" for relative dates.
- Revenue: Invoice.Total per invoice, or InvoiceLine.UnitPrice * InvoiceLine.Quantity per line; both reconcile.
- InvoiceLine.Quantity is always 1, so units sold = number of invoice lines.
- Customer.SupportRepId -> Employee.EmployeeId is the customer's sales support rep.
- Employee.ReportsTo -> Employee.EmployeeId is the manager.
- Playlist names are not unique (e.g. two playlists are called 'Music').
- Media types containing 'video' are videos; the rest are audio."""


def describe_schema(db_path: Path | str = config.DB_PATH) -> str:
    """Deterministic schema description (stable text, so it caches well)."""
    con = connect_readonly(db_path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        lines: list[str] = []
        for t in tables:
            cols = con.execute(f'SELECT * FROM "{t}" LIMIT 0').description
            info = _table_info(db_path, t)
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            col_txt = ", ".join(f"{c[0]} {info.get(c[0], '')}".strip() for c in cols)
            lines.append(f"{t} ({n} rows): {col_txt}")
            for fk in _foreign_keys(db_path, t):
                lines.append(f"  FK {t}.{fk[0]} -> {fk[1]}.{fk[2]}")
        return "\n".join(lines) + "\n" + SCHEMA_NOTES
    finally:
        con.close()


def _plain(db_path: Path | str) -> sqlite3.Connection:
    # PRAGMA reads for schema description use a separate read-only connection
    # without the authorizer (PRAGMA is denied there by design).
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def _table_info(db_path: Path | str, table: str) -> dict[str, str]:
    con = _plain(db_path)
    try:
        out = {}
        for _, name, typ, notnull, _, pk in con.execute(f'PRAGMA table_info("{table}")'):
            tag = typ.split("(")[0]
            if pk:
                tag += " PK"
            out[name] = tag
        return out
    finally:
        con.close()


def _foreign_keys(db_path: Path | str, table: str) -> list[tuple[str, str, str]]:
    con = _plain(db_path)
    try:
        return [(r[3], r[2], r[4]) for r in con.execute(f'PRAGMA foreign_key_list("{table}")')]
    finally:
        con.close()
