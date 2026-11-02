from __future__ import annotations

import hashlib

from askdata.db import run_query
from askdata.glossary import knowledge_base
from askdata.prompts import build_system_prompt, build_user_message
from askdata.seed import build


def _fingerprint(path) -> str:
    import sqlite3

    con = sqlite3.connect(path)
    parts = []
    for t in ["customers", "orders", "order_items", "returns", "targets", "marketing_spend"]:
        parts.append(str(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]))
    parts.append(str(con.execute("SELECT ROUND(SUM(quantity * unit_price), 2) FROM order_items").fetchone()[0]))
    con.close()
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def test_seed_is_deterministic(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    build(a)
    build(b)
    assert _fingerprint(a) == _fingerprint(b)


def test_ambiguity_cases_exist_in_data():
    priyas = run_query("SELECT rep_name FROM sales_reps WHERE rep_name LIKE 'Priya%'").rows
    assert len(priyas) == 2
    summit = run_query("SELECT customer_name FROM customers WHERE customer_name LIKE '%Summit Supply%'").rows
    assert len(summit) == 2
    apex_brand = run_query("SELECT COUNT(*) FROM products WHERE brand = 'Apex'").rows[0][0]
    apex_customer = run_query("SELECT COUNT(*) FROM customers WHERE customer_name = 'Apex Outfitters'").rows[0][0]
    assert apex_brand > 0 and apex_customer == 1
    marches = run_query("SELECT COUNT(DISTINCT strftime('%Y', order_date)) FROM orders "
                        "WHERE strftime('%m', order_date) = '03'").rows[0][0]
    assert marches == 2


def test_system_prompt_is_frozen_and_cacheable():
    a, b = build_system_prompt(), build_system_prompt()
    assert a == b
    assert len(a) / 4 > 1024  # comfortably above the 512-token minimum cacheable prefix
    assert "2026-10-01" in a and "Net revenue" in a


def test_retrieval_finds_governed_definitions():
    kb = knowledge_base()
    cases = {
        "What was blended CAC in FY2026?": "Customer acquisition cost (CAC)",
        "How many active customers do we have?": "Active customer",
        "What is our churn rate?": "Churned customer",
        "on-time shipping rate in the West": "Late shipment",
        "gross margin percentage": "Gross margin",
        "average order value for consumers": "Average order value (AOV)",
    }
    for q, term in cases.items():
        titles = [t.title for t in kb.search(q)[0]]
        assert term in titles, (q, titles)
    assert "Question: How many" in build_user_message("How many active customers do we have?")
