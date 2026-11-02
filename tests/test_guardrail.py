from __future__ import annotations

import pytest

from askdata.db import QueryError, run_query

BLOCKED = [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE products SET list_price = 0",
    "INSERT INTO regions VALUES (9, 'Mars')",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "PRAGMA writable_schema = ON",
    "SELECT 1; DROP TABLE orders",
    "SELECT load_extension('/tmp/evil')",
    "WITH x AS (SELECT 1) DELETE FROM orders",
    "CREATE TABLE t (a)",
    "SELECT * FROM pragma_table_info('orders')",
    "  -- sneaky\nVACUUM",
    "REPLACE INTO regions VALUES (1, 'X')",
]


@pytest.mark.parametrize("sql", BLOCKED)
def test_writes_and_escapes_are_blocked(sql):
    with pytest.raises(QueryError):
        run_query(sql)


def test_runaway_query_is_stopped():
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(QueryError, match="stopped"):
        run_query(sql, timeout_s=0.5)


def test_select_with_cte_and_comments_runs():
    r = run_query("/* top regions */ WITH t AS (SELECT region_id FROM customers) SELECT COUNT(*) FROM t;")
    assert r.rows[0][0] > 0


def test_row_limit_sets_truncated():
    r = run_query("SELECT order_id FROM orders", max_rows=10)
    assert len(r.rows) == 10 and r.truncated


def test_database_is_unchanged():
    assert run_query("SELECT COUNT(*) FROM regions").rows[0][0] == 5
