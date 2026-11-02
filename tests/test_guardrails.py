import sqlite3

import pytest

from askdata import config
from askdata.db import QueryTimeout, connect_readonly, run_query
from askdata.guardrails import GuardrailError, validate_sql


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select count(*) from Customer;",
    "SELECT 1;;  ",
    "  -- a comment\nSELECT Name FROM Genre",
    "/* lead */ WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT 'a;b' AS s, \"Name\" FROM Genre WHERE Name = 'it''s; DROP TABLE x' -- ;\n",
    "SELECT [Name] FROM [Genre]",
])
def test_accepts_single_select(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql,msg", [
    ("", "Empty"),
    ("   ", "Empty"),
    ("INSERT INTO Genre VALUES (99, 'x')", "Only SELECT"),
    ("UPDATE Invoice SET Total = 0", "Only SELECT"),
    ("DELETE FROM Invoice", "Only SELECT"),
    ("DROP TABLE Invoice", "Only SELECT"),
    ("ATTACH DATABASE 'x.db' AS x", "Only SELECT"),
    ("PRAGMA table_info(Invoice)", "Only SELECT"),
    ("VACUUM", "Only SELECT"),
    ("SELECT 1; DELETE FROM Invoice", "one SQL statement"),
    ("SELECT 1; SELECT 2", "one SQL statement"),
    ("SELECT 'unterminated", "Unterminated"),
    ("SELECT 1 /* open", "Unterminated"),
    ("SELECT 1" + " " * config.MAX_SQL_CHARS, "longer than"),
])
def test_rejects_everything_else(sql, msg):
    with pytest.raises(GuardrailError, match=msg):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "WITH x AS (SELECT 1) DELETE FROM Invoice",
    "WITH x AS (SELECT 1) INSERT INTO Genre SELECT 99, 'x'",
    "SELECT load_extension('evil')",
])
def test_authorizer_blocks_what_validation_lets_through(sql):
    with pytest.raises(GuardrailError, match="not allowed"):
        run_query(sql)


def test_connection_itself_is_read_only():
    con = connect_readonly()
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM Invoice")
    con.close()
    assert run_query("SELECT COUNT(*) FROM Invoice").rows == [[412]]


def test_row_limit_truncates():
    res = run_query("SELECT TrackId FROM Track", row_limit=10)
    assert len(res.rows) == 10 and res.truncated
    assert "truncated" in res.to_model_payload()["note"]


def test_timeout_stops_runaway_query():
    with pytest.raises(QueryTimeout):
        run_query("SELECT COUNT(*) FROM Track a, Track b, Track c", timeout_s=0.3)


def test_model_payload_is_capped():
    res = run_query("SELECT TrackId FROM Track", row_limit=120)
    payload = res.to_model_payload(max_rows=50)
    assert len(payload["rows"]) == 50 and payload["row_count"] == 120
