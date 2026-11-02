"""Data dictionary for the Trailhead Goods database.

One source of truth for the agent's system prompt, the MCP server's
describe_table tool and the README.
"""

from __future__ import annotations

from askdata.config import DATA_THROUGH, TODAY

TABLES: dict[str, dict] = {
    "regions": {
        "about": "One row per sales region.",
        "columns": {
            "region_id": "INTEGER primary key",
            "region_name": "TEXT: 'North', 'South', 'East', 'West', 'Central'",
        },
    },
    "sales_reps": {
        "about": "Account managers. Only business customers (Small Business, Enterprise) have a rep.",
        "columns": {
            "rep_id": "INTEGER primary key",
            "rep_name": "TEXT full name",
            "region_id": "INTEGER -> regions.region_id",
            "hire_date": "TEXT 'YYYY-MM-DD'",
        },
    },
    "customers": {
        "about": "One row per customer account (people and businesses). Some accounts never ordered.",
        "columns": {
            "customer_id": "INTEGER primary key",
            "customer_name": "TEXT person name (Consumer) or company name (business). Not unique.",
            "segment": "TEXT: 'Consumer', 'Small Business', 'Enterprise'",
            "region_id": "INTEGER -> regions.region_id",
            "signup_date": "TEXT 'YYYY-MM-DD' account creation date",
            "acquisition_channel": "TEXT marketing channel that acquired the customer: 'Organic Search', "
                                   "'Paid Search', 'Social', 'Referral', 'Partner'",
            "rep_id": "INTEGER -> sales_reps.rep_id; NULL for Consumer accounts",
        },
    },
    "categories": {
        "about": "15 product categories grouped into 6 departments.",
        "columns": {
            "category_id": "INTEGER primary key",
            "category_name": "TEXT e.g. 'Tents', 'Footwear', 'Kayaks & Boards'",
            "department": "TEXT: 'Camping', 'Hiking', 'Climbing', 'Paddling', 'Apparel', 'Lighting & Navigation'",
        },
    },
    "products": {
        "about": "50 products.",
        "columns": {
            "product_id": "INTEGER primary key",
            "product_name": "TEXT unique",
            "brand": "TEXT: 'Trailhead' (house brand), 'Ridgeline', 'Cascade', 'Apex', 'Granite'",
            "category_id": "INTEGER -> categories.category_id",
            "list_price": "REAL current list price, USD",
            "unit_cost": "REAL cost per unit, USD (use for COGS)",
            "launch_date": "TEXT 'YYYY-MM-DD'",
            "is_active": "INTEGER 1 = sold today, 0 = discontinued",
        },
    },
    "orders": {
        "about": "One row per order.",
        "columns": {
            "order_id": "INTEGER primary key",
            "customer_id": "INTEGER -> customers.customer_id",
            "order_date": "TEXT 'YYYY-MM-DD'",
            "ship_date": "TEXT 'YYYY-MM-DD'; NULL unless status = 'completed'",
            "status": "TEXT: 'completed' (shipped), 'cancelled', 'pending' (not shipped yet)",
            "channel": "TEXT where the order was placed: 'Web', 'Mobile App', 'Marketplace', 'Phone'",
            "promo_code": "TEXT NULL or 'WELCOME10', 'SUMMER25', 'BULK15'",
            "shipping_fee": "REAL USD charged for shipping (not revenue)",
        },
    },
    "order_items": {
        "about": "One row per product line on an order.",
        "columns": {
            "order_item_id": "INTEGER primary key",
            "order_id": "INTEGER -> orders.order_id",
            "product_id": "INTEGER -> products.product_id",
            "quantity": "INTEGER units ordered",
            "unit_price": "REAL USD per unit before discount",
            "discount_pct": "REAL fraction 0-1 taken off the line (promo codes and Enterprise contract discounts)",
        },
    },
    "returns": {
        "about": "Returned units. At most one return row per order line.",
        "columns": {
            "return_id": "INTEGER primary key",
            "order_item_id": "INTEGER -> order_items.order_item_id",
            "return_date": "TEXT 'YYYY-MM-DD'",
            "quantity": "INTEGER units returned",
            "reason": "TEXT: 'Wrong size', 'Changed mind', 'Defective', 'Not as described', 'Arrived late'",
            "refund_amount": "REAL USD refunded",
        },
    },
    "targets": {
        "about": "Quarterly net revenue targets by region.",
        "columns": {
            "region_id": "INTEGER -> regions.region_id",
            "fiscal_year": "INTEGER, e.g. 2026 = 2025-04-01 to 2026-03-31",
            "fiscal_quarter": "INTEGER 1-4",
            "net_revenue_target": "REAL USD",
        },
    },
    "marketing_spend": {
        "about": "Monthly marketing spend by acquisition channel.",
        "columns": {
            "month": "TEXT 'YYYY-MM'",
            "channel": "TEXT, same values as customers.acquisition_channel",
            "spend": "REAL USD",
        },
    },
    "calendar": {
        "about": "Date dimension from 2024-01-01 to 2027-03-31. Join calendar.date to any date column.",
        "columns": {
            "date": "TEXT 'YYYY-MM-DD' primary key",
            "year": "INTEGER calendar year",
            "month": "INTEGER 1-12",
            "month_name": "TEXT 'January'...",
            "fiscal_year": "INTEGER (April-March year, named for the year it ends)",
            "fiscal_quarter": "INTEGER 1-4 (Q1 = Apr-Jun)",
            "fiscal_month": "INTEGER 1-12 (1 = April)",
            "is_weekend": "INTEGER 1 = Saturday or Sunday",
        },
    },
}

FACTS = f"""\
- Today is {TODAY}. Orders run from 2024-04-01 (the online store opened) to {DATA_THROUGH}.
- Fiscal years run April to March and are named for the year they end: FY2026 = 2025-04-01 to 2026-03-31.
  Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar. The current fiscal year is FY2027 (Q1 and Q2 have data).
- "Last quarter" is FY2027 Q2 (2026-07-01 to 2026-09-30). "Last month" is September 2026.
- Dates are TEXT 'YYYY-MM-DD'. Use BETWEEN on the strings, date() / julianday(), or join the calendar table.
- Money is in USD."""


def schema_markdown() -> str:
    parts = []
    for table, spec in TABLES.items():
        cols = "\n".join(f"  - {c}: {d}" for c, d in spec["columns"].items())
        parts.append(f"### {table}\n{spec['about']}\n{cols}")
    return "\n\n".join(parts)


def describe(table: str) -> str | None:
    spec = TABLES.get(table)
    if not spec:
        return None
    cols = "\n".join(f"- {c}: {d}" for c, d in spec["columns"].items())
    return f"{table}: {spec['about']}\n{cols}"
