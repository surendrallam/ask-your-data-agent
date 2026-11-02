import pytest

from askdata import chart
from askdata.db import run_query
from askdata.glossary import Glossary
from askdata.router import classify


@pytest.mark.parametrize("query,term", [
    ("How many active customers do we have?", "active customer"),
    ("What is our AOV?", "average order value"),
    ("churned customers", "lapsed customer"),
    ("sales in Europe", "region"),
    ("Who are our VIP customers?", "VIP customer"),
    ("revenue last quarter", "last quarter"),
    ("best-selling genres", "best-selling"),
    ("basket size", "basket size"),
    ("corporate customers", "corporate customer"),
    ("tracks that never sold", "unsold track"),
])
def test_glossary_top_hit(query, term):
    hits = Glossary().search(query)
    assert hits and hits[0]["term"] == term


def test_glossary_no_match():
    assert Glossary().search("zzzz qqqq") == []


@pytest.mark.parametrize("q,hard", [
    ("How many customers do we have?", False),
    ("What was total revenue in 2025?", False),
    ("By what percentage did revenue grow from 2024 to 2025?", True),
    ("For each region, which genre sold the most units in 2025?", True),
    ("Which customers spent more than the average, and never bought a video?", True),
])
def test_router(q, hard):
    assert classify(q)[0] is hard


def test_bar_chart_for_categories():
    r = run_query("SELECT BillingCountry AS country, ROUND(SUM(Total),2) AS revenue FROM Invoice GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
    svg = chart.render_svg(r.columns, r.rows)
    assert svg.startswith("<svg") and svg.count("<path") == 5 and "USA" in svg


def test_line_chart_for_time():
    r = run_query("SELECT strftime('%Y-%m', InvoiceDate) AS month, ROUND(SUM(Total),2) AS revenue FROM Invoice WHERE InvoiceDate LIKE '2025%' GROUP BY 1")
    assert chart.pick_spec(r.columns, r.rows)["type"] == "line"
    assert "<polyline" in chart.render_svg(r.columns, r.rows)


def test_no_chart_for_scalar_or_text_only():
    assert chart.render_svg(["n"], [[59]]) is None
    assert chart.render_svg(["name"], [["a"], ["b"]]) is None
    assert chart.render_svg(["a", "b"], [["x", 1], ["y", 2]], {"type": "none"}) is None


def test_chart_labels_are_escaped():
    svg = chart.render_svg(["label", "v"], [["<script>alert(1)</script>", 1], ["b&c", 2]])
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "b&amp;c" in svg


def test_bar_labels_are_not_cut_when_there_is_room():
    svg = chart.render_svg(["region", "revenue"], [["North America", 170.55], ["Asia", 10.89]], width=786)
    assert ">North America<" in svg and "…" not in svg
    narrow = chart.render_svg(["name", "v"], [["A very long customer name indeed", 1], ["B", 2]], width=320)
    assert "…" in narrow  # long labels are still shortened on a phone-width chart
