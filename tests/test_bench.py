from __future__ import annotations

import pytest

from bench.grade import grade, tables_match, values_match
from bench.run import load_questions, summarize

QUESTIONS = load_questions()
BY_ID = {q["id"]: q for q in QUESTIONS}


def test_question_set_shape():
    assert len(QUESTIONS) == 50
    assert sum(q.get("expect") == "clarify" for q in QUESTIONS) == 5
    assert len(BY_ID) == 50


@pytest.mark.parametrize("q", [q for q in QUESTIONS if q.get("expect") != "clarify"], ids=lambda q: q["id"])
def test_reference_answer_grades_as_correct(q):
    """Citing the reference SQL itself must pass; an empty answer must fail."""
    assert grade(q, {"status": "answered", "sql": q["sql"]}).passed
    assert not grade(q, {"status": "failed", "sql": None, "error": "x"}).passed
    assert not grade(q, {"status": "clarify"}).passed


def test_common_mistakes_fail():
    q01 = BY_ID["q01"]
    with_cancelled = q01["sql"].replace("o.status = 'completed'", "o.status IN ('completed', 'cancelled')")
    assert not grade(q01, {"status": "answered", "sql": with_cancelled}).passed
    no_refunds = """SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct))
                    FROM orders o JOIN order_items oi ON oi.order_id = o.order_id
                    WHERE o.status = 'completed' AND o.order_date BETWEEN '2025-04-01' AND '2026-03-31'"""
    assert not grade(q01, {"status": "answered", "sql": no_refunds}).passed
    calendar_year = q01["sql"].replace("'2025-04-01' AND '2026-03-31'", "'2025-01-01' AND '2025-12-31'")
    assert not grade(q01, {"status": "answered", "sql": calendar_year}).passed


def test_extra_columns_and_rounding_are_fine():
    q17 = BY_ID["q17"]
    richer = q17["sql"].replace("SELECT p.product_name,", "SELECT p.product_id, p.product_name, p.brand,")
    assert grade(q17, {"status": "answered", "sql": richer}).passed
    q13 = BY_ID["q13"]
    as_percent = q13["sql"].replace("ROUND((cur.net - prev.net) / prev.net, 4)",
                                    "ROUND(100.0 * (cur.net - prev.net) / prev.net, 1)")
    assert grade(q13, {"status": "answered", "sql": as_percent}).passed


def test_clarify_questions():
    c01 = BY_ID["c01"]
    assert grade(c01, {"status": "clarify"}).passed
    assert not grade(c01, {"status": "answered", "sql": "SELECT 1"}).passed


def test_value_matching_rules():
    assert values_match(2357, 2357.0)
    assert not values_match(2357, 2358)
    assert values_match(8358885.95, 8358886)  # rounding to whole dollars is within 0.01%
    assert not values_match(8358885.95, 8358885.95 * 1.003)
    assert values_match(0.1996, 19.96, ratio=True) and values_match(0.1996, "20.0%", ratio=True)
    assert not values_match(0.1996, 0.21, ratio=True)
    assert values_match("West", "west ")


def test_table_matching_modes():
    ref = [("A", 1), ("B", 2)]
    assert tables_match(ref, [("B", 2, "x"), ("A", 1, "y")])[0]
    assert not tables_match(ref, [("B", 2), ("A", 1)], ordered=True)[0]
    assert tables_match(ref, [("A", 1), ("B", 2), ("C", 3)], ordered=True, prefix=True)[0]
    assert not tables_match(ref, [("A", 1)], prefix=True)[0]
    assert not tables_match(ref, [("A", 1), ("B", 2), ("C", 3)])[0]


def test_summary_counts():
    rows = [
        {"config": "haiku", "effort": "medium", "expect": "answer", "passed": True, "status": "answered",
         "escalated": False, "cost_usd": 0.01, "latency_s": 3.0, "api_calls": 3, "cache_hit_ratio": 0.7, "qid": "q01"},
        {"config": "haiku", "effort": "medium", "expect": "clarify", "passed": False, "status": "answered",
         "escalated": False, "cost_usd": 0.02, "latency_s": 5.0, "api_calls": 4, "cache_hit_ratio": 0.6, "qid": "c01"},
    ]
    s = summarize(rows)["haiku/medium"]
    assert s["accuracy"] == 0.5 and s["answer_accuracy"] == 1.0 and s["clarify_accuracy"] == 0.0
    assert s["cost_total_usd"] == 0.03
