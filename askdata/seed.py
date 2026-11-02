"""Generate the Trailhead Goods sample sales database (SQLite).

Trailhead Goods is a fictional outdoor-gear retailer. Every row is synthetic.
The generator is deterministic: all randomness comes from
``random.Random(SEED).random()``, so the same seed gives the same database on
any platform and Python version.

Run:  python -m askdata.seed            (writes data/sales.db)
"""

from __future__ import annotations

import argparse
import bisect
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from askdata.config import DB_PATH

SEED = 20261102
START = date(2024, 4, 1)  # first day of FY2025
END = date(2026, 9, 30)  # last day with data (end of Q2 FY2027)
SIGNUP_START = date(2024, 1, 1)
CAL_START, CAL_END = date(2024, 1, 1), date(2027, 3, 31)

DDL = """
CREATE TABLE regions (
  region_id     INTEGER PRIMARY KEY,
  region_name   TEXT NOT NULL UNIQUE
);
CREATE TABLE sales_reps (
  rep_id        INTEGER PRIMARY KEY,
  rep_name      TEXT NOT NULL,
  region_id     INTEGER NOT NULL REFERENCES regions(region_id),
  hire_date     TEXT NOT NULL
);
CREATE TABLE customers (
  customer_id          INTEGER PRIMARY KEY,
  customer_name        TEXT NOT NULL,
  segment              TEXT NOT NULL CHECK (segment IN ('Consumer','Small Business','Enterprise')),
  region_id            INTEGER NOT NULL REFERENCES regions(region_id),
  signup_date          TEXT NOT NULL,
  acquisition_channel  TEXT NOT NULL,
  rep_id               INTEGER REFERENCES sales_reps(rep_id)
);
CREATE TABLE categories (
  category_id    INTEGER PRIMARY KEY,
  category_name  TEXT NOT NULL UNIQUE,
  department     TEXT NOT NULL
);
CREATE TABLE products (
  product_id    INTEGER PRIMARY KEY,
  product_name  TEXT NOT NULL UNIQUE,
  brand         TEXT NOT NULL,
  category_id   INTEGER NOT NULL REFERENCES categories(category_id),
  list_price    REAL NOT NULL,
  unit_cost     REAL NOT NULL,
  launch_date   TEXT NOT NULL,
  is_active     INTEGER NOT NULL
);
CREATE TABLE orders (
  order_id      INTEGER PRIMARY KEY,
  customer_id   INTEGER NOT NULL REFERENCES customers(customer_id),
  order_date    TEXT NOT NULL,
  ship_date     TEXT,
  status        TEXT NOT NULL CHECK (status IN ('completed','cancelled','pending')),
  channel       TEXT NOT NULL,
  promo_code    TEXT,
  shipping_fee  REAL NOT NULL
);
CREATE TABLE order_items (
  order_item_id  INTEGER PRIMARY KEY,
  order_id       INTEGER NOT NULL REFERENCES orders(order_id),
  product_id     INTEGER NOT NULL REFERENCES products(product_id),
  quantity       INTEGER NOT NULL,
  unit_price     REAL NOT NULL,
  discount_pct   REAL NOT NULL
);
CREATE TABLE returns (
  return_id      INTEGER PRIMARY KEY,
  order_item_id  INTEGER NOT NULL REFERENCES order_items(order_item_id),
  return_date    TEXT NOT NULL,
  quantity       INTEGER NOT NULL,
  reason         TEXT NOT NULL,
  refund_amount  REAL NOT NULL
);
CREATE TABLE targets (
  region_id           INTEGER NOT NULL REFERENCES regions(region_id),
  fiscal_year         INTEGER NOT NULL,
  fiscal_quarter      INTEGER NOT NULL,
  net_revenue_target  REAL NOT NULL,
  PRIMARY KEY (region_id, fiscal_year, fiscal_quarter)
);
CREATE TABLE marketing_spend (
  month    TEXT NOT NULL,
  channel  TEXT NOT NULL,
  spend    REAL NOT NULL,
  PRIMARY KEY (month, channel)
);
CREATE TABLE calendar (
  date            TEXT PRIMARY KEY,
  year            INTEGER NOT NULL,
  month           INTEGER NOT NULL,
  month_name      TEXT NOT NULL,
  fiscal_year     INTEGER NOT NULL,
  fiscal_quarter  INTEGER NOT NULL,
  fiscal_month    INTEGER NOT NULL,
  is_weekend      INTEGER NOT NULL
);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
CREATE INDEX idx_returns_item ON returns(order_item_id);
CREATE INDEX idx_customers_rep ON customers(rep_id);
"""


class Rng:
    """Deterministic helpers built only on random.Random.random()."""

    def __init__(self, seed: int) -> None:
        self._r = random.Random(seed)

    def u(self) -> float:
        return self._r.random()

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.u()

    def randint(self, a: int, b: int) -> int:
        return a + min(int(self.u() * (b - a + 1)), b - a)

    def chance(self, p: float) -> bool:
        return self.u() < p

    def choice(self, seq):
        return seq[min(int(self.u() * len(seq)), len(seq) - 1)]

    def weighted(self, items, weights):
        x = self.u() * sum(weights)
        acc = 0.0
        for item, w in zip(items, weights):
            acc += w
            if x < acc:
                return item
        return items[-1]


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

REGIONS = ["North", "South", "East", "West", "Central"]
REGION_WEIGHTS = [0.22, 0.18, 0.24, 0.26, 0.10]

# (name, region, hire_date)
REPS = [
    ("Priya Sharma", "North", "2023-02-13"),
    ("Daniel Okafor", "North", "2024-06-03"),
    ("Arjun Mehta", "East", "2022-09-19"),
    ("Mei Lin", "East", "2023-11-06"),
    ("Sarah Kim", "East", "2025-01-13"),
    ("Carlos Ruiz", "South", "2022-04-04"),
    ("Aisha Khan", "South", "2024-08-26"),
    ("Priya Nair", "West", "2023-07-10"),
    ("Lucas Silva", "West", "2021-10-18"),
    ("Nina Petrova", "West", "2025-03-03"),
    ("Ravi Iyer", "Central", "2022-01-24"),
    ("Tom Becker", "Central", "2024-02-05"),
]

DEPARTMENTS = {
    "Camping": ["Tents", "Sleeping Bags", "Camp Kitchen"],
    "Hiking": ["Backpacks", "Footwear", "Trekking Poles"],
    "Climbing": ["Harnesses", "Ropes", "Climbing Hardware"],
    "Paddling": ["Kayaks & Boards", "Paddling Gear"],
    "Apparel": ["Jackets", "Base Layers"],
    "Lighting & Navigation": ["Headlamps & Lanterns", "GPS & Navigation"],
}

# Monthly seasonality (Jan..Dec) by department.
SEASON = {
    "Camping": [0.50, 0.55, 0.75, 1.00, 1.35, 1.60, 1.65, 1.40, 1.00, 0.70, 0.55, 0.75],
    "Hiking": [0.70, 0.70, 0.90, 1.10, 1.30, 1.35, 1.30, 1.25, 1.15, 1.00, 0.80, 0.90],
    "Climbing": [0.80, 0.80, 0.95, 1.10, 1.20, 1.15, 1.10, 1.10, 1.15, 1.05, 0.85, 0.80],
    "Paddling": [0.30, 0.30, 0.50, 0.90, 1.50, 1.90, 2.00, 1.70, 0.90, 0.50, 0.30, 0.35],
    "Apparel": [1.30, 1.10, 0.90, 0.80, 0.70, 0.60, 0.60, 0.70, 0.90, 1.20, 1.60, 1.90],
    "Lighting & Navigation": [1.00, 0.90, 0.90, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.10, 1.30, 1.60],
}
ORDER_SEASON = [0.85, 0.75, 0.85, 1.00, 1.15, 1.25, 1.25, 1.10, 0.95, 0.95, 1.15, 1.45]

# (name, brand, category, list_price, unit_cost, launch_date, is_active, popularity)
PRODUCTS = [
    ("Ridgeline 2P Tent", "Ridgeline", "Tents", 329.0, 150.0, "2023-03-01", 1, 1.6),
    ("Ridgeline UL 1P Tent", "Ridgeline", "Tents", 389.0, 170.0, "2025-09-01", 1, 1.2),
    ("Cascade 4P Family Tent", "Cascade", "Tents", 449.0, 210.0, "2022-04-01", 1, 1.0),
    ("Cascade Canvas Tent", "Cascade", "Tents", 699.0, 360.0, "2021-05-01", 0, 0.5),
    ("Trailhead Basecamp 6P Tent", "Trailhead", "Tents", 599.0, 290.0, "2023-04-01", 1, 0.6),
    ("Trailhead Down Bag -5C", "Trailhead", "Sleeping Bags", 279.0, 120.0, "2022-08-01", 1, 1.2),
    ("Trailhead Synthetic Bag 0C", "Trailhead", "Sleeping Bags", 149.0, 62.0, "2021-08-01", 1, 1.5),
    ("Ridgeline Quilt 2C", "Ridgeline", "Sleeping Bags", 239.0, 100.0, "2024-10-01", 1, 0.9),
    ("Kids Explorer Bag", "Trailhead", "Sleeping Bags", 69.0, 28.0, "2022-03-01", 1, 0.8),
    ("Trailhead Camp Stove", "Trailhead", "Camp Kitchen", 89.0, 38.0, "2021-03-01", 1, 1.8),
    ("Titanium Cook Set", "Trailhead", "Camp Kitchen", 119.0, 45.0, "2023-02-01", 1, 1.2),
    ("Collapsible Kettle", "Trailhead", "Camp Kitchen", 39.0, 14.0, "2022-05-01", 1, 1.4),
    ("Water Filter Pro", "Cascade", "Camp Kitchen", 59.0, 22.0, "2023-06-01", 1, 2.0),
    ("Explorer Backpack 40L", "Trailhead", "Backpacks", 189.0, 78.0, "2022-02-01", 1, 1.7),
    ("Explorer Backpack 65L", "Trailhead", "Backpacks", 259.0, 105.0, "2022-02-01", 1, 1.1),
    ("Daypack 22L", "Trailhead", "Backpacks", 89.0, 34.0, "2021-02-01", 1, 2.2),
    ("Apex Alpine Pack 50L", "Apex", "Backpacks", 299.0, 125.0, "2024-03-01", 1, 0.9),
    ("Granite Hiking Boot", "Granite", "Footwear", 219.0, 95.0, "2022-01-01", 1, 1.9),
    ("Granite Mid Boot", "Granite", "Footwear", 179.0, 76.0, "2023-01-01", 1, 1.4),
    ("Trail Runner Shoe", "Granite", "Footwear", 139.0, 55.0, "2023-09-01", 1, 1.8),
    ("Camp Sandal", "Trailhead", "Footwear", 59.0, 20.0, "2022-04-01", 1, 1.0),
    ("Apex Trekking Poles", "Apex", "Trekking Poles", 129.0, 48.0, "2023-03-01", 1, 1.3),
    ("Trailhead Carbon Poles", "Trailhead", "Trekking Poles", 169.0, 66.0, "2024-04-01", 1, 0.9),
    ("Trailhead Aluminum Poles", "Trailhead", "Trekking Poles", 69.0, 25.0, "2021-04-01", 1, 1.3),
    ("Apex Climbing Harness", "Apex", "Harnesses", 89.0, 34.0, "2022-06-01", 1, 1.1),
    ("Trailhead Sport Harness", "Trailhead", "Harnesses", 69.0, 26.0, "2021-06-01", 1, 0.9),
    ("Dynamic Rope 9.8mm 60m", "Cascade", "Ropes", 229.0, 105.0, "2022-06-01", 1, 0.7),
    ("Static Rope 10mm 50m", "Cascade", "Ropes", 179.0, 80.0, "2022-06-01", 1, 0.4),
    ("Locking Carabiner", "Apex", "Climbing Hardware", 19.0, 6.0, "2021-06-01", 1, 2.4),
    ("Quickdraw Set of 6", "Apex", "Climbing Hardware", 109.0, 44.0, "2022-06-01", 1, 0.8),
    ("Belay Device", "Trailhead", "Climbing Hardware", 29.0, 10.0, "2021-06-01", 1, 1.0),
    ("Climbing Helmet", "Apex", "Climbing Hardware", 79.0, 30.0, "2022-06-01", 1, 1.0),
    ("Touring Kayak 14ft", "Cascade", "Kayaks & Boards", 1299.0, 640.0, "2022-04-01", 1, 0.35),
    ("Inflatable Kayak 2P", "Cascade", "Kayaks & Boards", 599.0, 280.0, "2023-04-01", 1, 0.6),
    ("Paddle Board 10ft", "Cascade", "Kayaks & Boards", 749.0, 350.0, "2023-04-01", 1, 0.5),
    ("Carbon Paddle", "Cascade", "Paddling Gear", 199.0, 80.0, "2022-04-01", 1, 0.6),
    ("PFD Life Vest", "Trailhead", "Paddling Gear", 99.0, 38.0, "2021-04-01", 1, 1.1),
    ("Dry Bag 20L", "Trailhead", "Paddling Gear", 39.0, 12.0, "2021-04-01", 1, 1.6),
    ("Storm Shell Jacket", "Ridgeline", "Jackets", 249.0, 98.0, "2022-09-01", 1, 1.4),
    ("Down Puffer Jacket", "Ridgeline", "Jackets", 229.0, 92.0, "2022-09-01", 1, 1.5),
    ("Fleece Midlayer", "Trailhead", "Jackets", 99.0, 36.0, "2021-09-01", 1, 1.6),
    ("Merino Base Top", "Ridgeline", "Base Layers", 89.0, 33.0, "2022-09-01", 1, 1.5),
    ("Merino Base Bottom", "Ridgeline", "Base Layers", 79.0, 29.0, "2022-09-01", 1, 1.1),
    ("Hiking Socks 3-Pack", "Trailhead", "Base Layers", 29.0, 9.0, "2021-01-01", 1, 2.8),
    ("Apex Headlamp 400", "Apex", "Headlamps & Lanterns", 59.0, 21.0, "2023-10-01", 1, 1.9),
    ("Trailhead Headlamp 250", "Trailhead", "Headlamps & Lanterns", 35.0, 12.0, "2021-10-01", 1, 1.5),
    ("Rechargeable Lantern", "Trailhead", "Headlamps & Lanterns", 49.0, 18.0, "2022-10-01", 1, 1.1),
    ("Handheld GPS Navigator", "Cascade", "GPS & Navigation", 349.0, 180.0, "2022-10-01", 1, 0.5),
    ("Compass Pro", "Trailhead", "GPS & Navigation", 29.0, 9.0, "2021-01-01", 1, 1.2),
    ("Satellite Messenger", "Cascade", "GPS & Navigation", 299.0, 150.0, "2024-11-01", 1, 0.7),
]
DISCONTINUED_AFTER = {"Cascade Canvas Tent": date(2025, 12, 31)}

# Business buyers (outfitters, camps, clubs) lean towards rental and group gear.
BUSINESS_TILT = {
    "Tents": 1.6, "Sleeping Bags": 1.3, "Kayaks & Boards": 2.2, "Paddling Gear": 2.0,
    "Harnesses": 1.8, "Climbing Hardware": 1.6, "Ropes": 1.8, "Jackets": 0.6,
    "Base Layers": 0.5, "Footwear": 0.5,
}

CHANNELS = ["Organic Search", "Paid Search", "Social", "Referral", "Partner"]
CONSUMER_CHANNEL_W = [0.34, 0.30, 0.22, 0.12, 0.02]
BUSINESS_CHANNEL_W = [0.18, 0.15, 0.07, 0.25, 0.35]
SPEND_BASE = {"Paid Search": 18000, "Social": 12000, "Partner": 6000, "Referral": 3000, "Organic Search": 4000}

FIRST = [
    "Aarav", "Ananya", "Rohan", "Kavya", "Vikram", "Meera", "Ishaan", "Sneha", "Kabir", "Diya",
    "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason", "Isabella", "Lucas",
    "Mia", "James", "Amelia", "Benjamin", "Harper", "Elijah", "Evelyn", "Henry", "Abigail", "Jack",
    "Chloe", "Leo", "Zara", "Omar", "Fatima", "Yusuf", "Hana", "Kenji", "Yuki", "Mateo",
    "Lucia", "Diego", "Sofia", "Andre", "Grace", "Samuel", "Nora", "Felix", "Ingrid", "Tariq",
]
LAST = [
    "Patel", "Gupta", "Reddy", "Menon", "Joshi", "Rao", "Kapoor", "Bose", "Das", "Pillai",
    "Smith", "Johnson", "Brown", "Garcia", "Miller", "Davis", "Wilson", "Anderson", "Thomas", "Moore",
    "Martin", "Lee", "Walker", "Hall", "Young", "King", "Wright", "Lopez", "Hill", "Scott",
    "Green", "Adams", "Baker", "Nelson", "Carter", "Mitchell", "Roberts", "Turner", "Phillips", "Campbell",
    "Tanaka", "Suzuki", "Kowalski", "Novak", "Haddad", "Rahman", "Okoro", "Mensah", "Silva", "Costa",
]
BIZ_PREFIX = [
    "Blue Ridge", "Pine Valley", "Coastal", "Granite Peak", "Riverbend", "Northern Lights", "Cedar Creek",
    "Redwood", "Lakeside", "Highland", "Canyon", "Timberline", "Bluewater", "Frontier", "Evergreen",
    "Wildflower", "Stonebridge", "Silver Lake", "Maple Hill", "Desert Sky", "Ironwood", "Foxglove",
    "Glacier", "Harbor", "Juniper", "Kestrel", "Lone Pine", "Meadowlark", "North Fork", "Oak Hollow",
    "Prairie Wind", "Quarry", "Rimrock", "Sagebrush", "Tamarack", "Upper Basin", "Vista", "Willow Bend",
    "Yellowstone Gate", "Zephyr",
]
BIZ_NOUN = [
    "Outfitters", "Adventures", "Expeditions", "Rentals", "Outdoor Club", "Camps", "Guides",
    "Gear Co.", "Trail Crew", "Paddle Club", "Climbing Gym", "Scouts",
]
# Fixed accounts used by the benchmark's ambiguity cases.
SPECIAL_ACCOUNTS = [
    # name, segment, region, rep, signup, orders/yr
    ("Summit Supply Co.", "Enterprise", "East", "Arjun Mehta", date(2024, 5, 10), 22.0),
    ("Summit Supply Partners", "Small Business", "West", "Priya Nair", date(2025, 2, 3), 9.0),
    ("Apex Outfitters", "Enterprise", "North", "Priya Sharma", date(2024, 4, 15), 24.0),
]

CONSUMERS, SMB, ENTERPRISE = 9000, 380, 70
PRICE_FACTOR = {2025: 1.00, 2026: 1.04, 2027: 1.07}


def fiscal_year(d: date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def fiscal_quarter(d: date) -> int:
    return ((d.month - 4) % 12) // 3 + 1


@dataclass
class Customer:
    cid: int
    name: str
    segment: str
    region: int
    signup: date
    channel: str
    rep: int | None
    rate: float  # orders per year while active
    churn: date  # last possible order date
    never_orders: bool = False
    orders: list = field(default_factory=list)


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def build(path: Path = DB_PATH, seed: int = SEED) -> dict:
    rng = Rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(DDL)

    # Dimensions --------------------------------------------------------------
    con.executemany("INSERT INTO regions VALUES (?, ?)", list(enumerate(REGIONS, start=1)))
    region_id = {name: i for i, name in enumerate(REGIONS, start=1)}
    rep_rows = [(i, n, region_id[r], h) for i, (n, r, h) in enumerate(REPS, start=1)]
    con.executemany("INSERT INTO sales_reps VALUES (?, ?, ?, ?)", rep_rows)
    rep_id = {n: i for i, n, _, _ in rep_rows}
    reps_by_region: dict[int, list[int]] = {}
    for i, _, rg, _ in rep_rows:
        reps_by_region.setdefault(rg, []).append(i)

    cat_rows, cat_dept = [], {}
    for dept, cats in DEPARTMENTS.items():
        for c in cats:
            cat_rows.append((len(cat_rows) + 1, c, dept))
            cat_dept[c] = dept
    con.executemany("INSERT INTO categories VALUES (?, ?, ?)", cat_rows)
    cat_id = {c: i for i, c, _ in cat_rows}

    prod_rows = []
    for i, (n, b, c, lp, uc, ld, act, _pop) in enumerate(PRODUCTS, start=1):
        prod_rows.append((i, n, b, cat_id[c], lp, uc, ld, act))
    con.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", prod_rows)

    cal = []
    for d in daterange(CAL_START, CAL_END):
        cal.append((d.isoformat(), d.year, d.month, d.strftime("%B"), fiscal_year(d),
                    fiscal_quarter(d), (d.month - 4) % 12 + 1, 1 if d.weekday() >= 5 else 0))
    con.executemany("INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?)", cal)

    # Customers ---------------------------------------------------------------
    days = list(daterange(SIGNUP_START, END - timedelta(days=5)))
    span = (days[-1] - days[0]).days
    weights, acc = [], 0.0
    for d in days:
        growth = 1.0 + 0.8 * (d - days[0]).days / span
        acc += growth * ORDER_SEASON[d.month - 1]
        weights.append(acc)

    def signup_date() -> date:
        # About a fifth of accounts registered before the online store opened on 1 Apr 2024.
        if rng.chance(0.22):
            return SIGNUP_START + timedelta(days=rng.randint(0, 90))
        return days[bisect.bisect_left(weights, rng.u() * weights[-1])]

    def far_future() -> date:
        return date(2100, 1, 1)

    customers: list[Customer] = []
    used_names: set[str] = set()

    def add_customer(name, segment, region, signup, channel, rep, rate, churn, never=False):
        customers.append(Customer(len(customers) + 1, name, segment, region, signup, channel, rep,
                                  rate, churn, never))
        used_names.add(name)

    for name, seg, reg, rep, signup, rate in SPECIAL_ACCOUNTS:
        add_customer(name, seg, region_id[reg], signup, "Partner" if seg == "Enterprise" else "Referral",
                     rep_id[rep], rate, far_future())

    for _ in range(CONSUMERS):
        first_name, last_name = rng.choice(FIRST), rng.choice(LAST)
        name = f"{first_name} {last_name}"
        if name in used_names and not rng.chance(0.10):  # keep a few namesakes, as in real data
            name = f"{first_name} {chr(65 + rng.randint(0, 25))}. {last_name}"
        signup = signup_date()
        rate = rng.weighted([0.6, 1.2, 2.5, 4.0, 7.0], [0.30, 0.30, 0.22, 0.13, 0.05])
        life = rng.weighted([120, 300, 540, 900, 100000], [0.20, 0.25, 0.20, 0.15, 0.20])
        add_customer(name, "Consumer", region_id[rng.weighted(REGIONS, REGION_WEIGHTS)], signup,
                     rng.weighted(CHANNELS, CONSUMER_CHANNEL_W), None, rate,
                     signup + timedelta(days=life), never=rng.chance(0.08))

    biz_names = [f"{p} {n}" for p in BIZ_PREFIX for n in BIZ_NOUN]
    for seg, count, tiers, tier_w, lives, life_w in [
        ("Small Business", SMB, [3, 6, 10, 16], [0.30, 0.35, 0.25, 0.10], [365, 730, 100000], [0.20, 0.30, 0.50]),
        ("Enterprise", ENTERPRISE, [8, 15, 25, 40], [0.25, 0.35, 0.25, 0.15], [730, 100000], [0.15, 0.85]),
    ]:
        for _ in range(count):
            while True:
                name = biz_names[int(rng.u() * len(biz_names))]
                if name not in used_names:
                    break
            reg = region_id[rng.weighted(REGIONS, REGION_WEIGHTS)]
            signup = signup_date()
            add_customer(name, seg, reg, signup, rng.weighted(CHANNELS, BUSINESS_CHANNEL_W),
                         rng.choice(reps_by_region[reg]), float(rng.weighted(tiers, tier_w)),
                         signup + timedelta(days=rng.weighted(lives, life_w)))

    # Sort by signup so customer_id order follows signup order, like a real system.
    specials = customers[: len(SPECIAL_ACCOUNTS)]
    others = sorted(customers[len(SPECIAL_ACCOUNTS):], key=lambda c: (c.signup, c.name))
    customers = sorted(specials + others, key=lambda c: (c.signup, c.name))
    for i, c in enumerate(customers, start=1):
        c.cid = i
    con.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(c.cid, c.name, c.segment, c.region, c.signup.isoformat(), c.channel, c.rep) for c in customers],
    )

    # Orders ------------------------------------------------------------------
    product_list = [
        (i, n, c, cat_dept[c], lp, pop, date.fromisoformat(ld), DISCONTINUED_AFTER.get(n))
        for i, (n, _b, c, lp, _uc, ld, _a, pop) in enumerate(PRODUCTS, start=1)
    ]

    def pick_product(d: date, business: bool):
        items, ws = [], []
        for pid, _n, cat, dept, lp, pop, launch, disc in product_list:
            if launch > d or (disc and d > disc):
                continue
            w = pop * SEASON[dept][d.month - 1]
            if business:
                w *= BUSINESS_TILT.get(cat, 1.0)
            if pid == 2:  # Ridgeline UL 1P Tent: strong launch, keeps growing
                w *= 1.0 + min((d - launch).days / 200.0, 0.8)
            items.append((pid, cat, dept, lp))
            ws.append(w)
        return rng.weighted(items, ws)

    order_rows, item_rows, return_rows = [], [], []
    ship_lag_west_bad = (date(2026, 7, 1), date(2026, 9, 30))
    order_seq = item_seq = return_seq = 0
    raw_orders = []  # (date, customer)
    for c in customers:
        if c.never_orders:
            continue
        first = c.signup + timedelta(days=rng.randint(0, 14))
        if first < START:
            first = START + timedelta(days=rng.randint(0, 20)) if c.signup < START else first
        last = min(c.churn, END)
        if first > END:
            continue
        raw_orders.append((first, c))
        weekday_biz = c.segment != "Consumer"
        for d in daterange(first + timedelta(days=3), last):
            p = c.rate / 365.0 * ORDER_SEASON[d.month - 1]
            if d.weekday() >= 5:
                p *= 0.25 if weekday_biz else 1.15
            if rng.u() < p:
                raw_orders.append((d, c))
    raw_orders.sort(key=lambda x: (x[0], x[1].cid))

    first_order_seen: set[int] = set()
    for d, c in raw_orders:
        order_seq += 1
        business = c.segment != "Consumer"
        is_first = c.cid not in first_order_seen
        first_order_seen.add(c.cid)
        if business:
            channel = rng.weighted(["Web", "Phone", "Mobile App"], [0.55, 0.40, 0.05])
            n_lines = rng.randint(1, 5) if c.segment == "Small Business" else rng.randint(2, 6)
        else:
            channel = rng.weighted(["Web", "Mobile App", "Marketplace", "Phone"], [0.50, 0.30, 0.17, 0.03])
            n_lines = rng.weighted([1, 2, 3, 4], [0.60, 0.25, 0.10, 0.05])
        promo, disc = None, 0.0
        if c.segment == "Enterprise":
            disc = 0.08  # contract discount, no promo code
        elif not business and date(2025, 6, 15) <= d <= date(2025, 7, 15) and rng.chance(0.35):
            promo, disc = "SUMMER25", 0.25
        elif not business and is_first and rng.chance(0.30):
            promo, disc = "WELCOME10", 0.10

        lines = []
        for _ in range(n_lines):
            pid, cat, dept, lp = pick_product(d, business)
            if c.segment == "Enterprise":
                qty = rng.randint(2, 12)
            elif c.segment == "Small Business":
                qty = rng.randint(1, 5)
            else:
                qty = 1 if rng.chance(0.9) else 2
            price = round(lp / PRICE_FACTOR[2027] * PRICE_FACTOR[fiscal_year(d)], 2)
            lines.append([pid, cat, dept, qty, price])
        if c.segment == "Small Business" and sum(q for _, _, _, q, _ in lines) >= 15:
            promo, disc = "BULK15", 0.15

        status = "completed"
        if rng.chance(0.015 if business else 0.035):
            status = "cancelled"
        lag = rng.weighted([1, 2, 3, 4, 5, 6, 7], [0.55, 0.30, 0.08, 0.03, 0.02, 0.01, 0.01])
        if c.region == region_id["West"] and ship_lag_west_bad[0] <= d <= ship_lag_west_bad[1] and rng.chance(0.25):
            lag = rng.randint(4, 7)
        ship = d + timedelta(days=lag)
        if status == "completed" and (ship > END or (d >= date(2026, 9, 28) and rng.chance(0.6))):
            status = "pending"
        ship_date = ship.isoformat() if status == "completed" else None
        net_goods = sum(q * p for _, _, _, q, p in lines) * (1 - disc)
        fee = 0.0 if business or status == "cancelled" or net_goods >= 75 else 7.95
        order_rows.append((order_seq, c.cid, d.isoformat(), ship_date, status, channel, promo, fee))

        for pid, cat, dept, qty, price in lines:
            item_seq += 1
            item_rows.append((item_seq, order_seq, pid, qty, price, disc))
            if status != "completed":
                continue
            p_ret = {"Apparel": 0.10, "Paddling": 0.04}.get(dept, 0.03)
            if cat == "Footwear":
                p_ret = 0.08
            elif dept == "Climbing":
                p_ret = 0.02
            if business:
                p_ret *= 0.4
            if rng.u() < p_ret:
                rdate = ship + timedelta(days=rng.randint(3, 30))
                if rdate > END:
                    continue
                rqty = qty if not business else rng.randint(1, qty)
                if dept == "Apparel" or cat == "Footwear":
                    reason = rng.weighted(["Wrong size", "Changed mind", "Defective", "Not as described"],
                                          [0.60, 0.20, 0.10, 0.10])
                else:
                    reason = rng.weighted(["Defective", "Changed mind", "Not as described", "Arrived late"],
                                          [0.40, 0.30, 0.20, 0.10])
                return_seq += 1
                return_rows.append((return_seq, item_seq, rdate.isoformat(), rqty, reason,
                                    round(rqty * price * (1 - disc), 2)))

    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", order_rows)
    con.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", item_rows)
    con.executemany("INSERT INTO returns VALUES (?, ?, ?, ?, ?, ?)", return_rows)

    # Targets: set from actual net revenue so attainment lands between ~88% and ~115%.
    actual = {
        (r, fy, fq): v
        for r, fy, fq, v in con.execute(
            """
            SELECT c.region_id, cal.fiscal_year, cal.fiscal_quarter,
                   SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)) - COALESCE(SUM(rf.refund), 0)
            FROM orders o
            JOIN customers c ON c.customer_id = o.customer_id
            JOIN calendar cal ON cal.date = o.order_date
            JOIN order_items oi ON oi.order_id = o.order_id
            LEFT JOIN (SELECT order_item_id, SUM(refund_amount) AS refund FROM returns GROUP BY order_item_id) rf
                   ON rf.order_item_id = oi.order_item_id
            WHERE o.status = 'completed'
            GROUP BY 1, 2, 3
            """
        )
    }
    target_rows = []
    for r in range(1, len(REGIONS) + 1):
        for fy in (2025, 2026, 2027):
            for fq in (1, 2, 3, 4):
                if (r, fy, fq) in actual:
                    base = actual[(r, fy, fq)] * rng.uniform(0.87, 1.14)
                else:  # future quarters: last year's actual plus 15%
                    base = actual.get((r, fy - 1, fq), 0.0) * 1.15
                target_rows.append((r, fy, fq, round(base, -3)))
    con.executemany("INSERT INTO targets VALUES (?, ?, ?, ?)", target_rows)

    spend_rows = []
    m = date(START.year, START.month, 1)
    i = 0
    while m <= END:
        for ch in CHANNELS:
            growth = 1.0 + 0.025 * i
            season = ORDER_SEASON[m.month - 1]
            jitter = rng.uniform(0.9, 1.1)
            spend_rows.append((m.strftime("%Y-%m"), ch, round(SPEND_BASE[ch] * growth * season * jitter, 0)))
        m = date(m.year + (m.month // 12), m.month % 12 + 1, 1)
        i += 1
    con.executemany("INSERT INTO marketing_spend VALUES (?, ?, ?)", spend_rows)

    con.commit()
    stats = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
             ["regions", "sales_reps", "customers", "categories", "products", "orders", "order_items",
              "returns", "targets", "marketing_spend", "calendar"]}
    con.execute("VACUUM")
    con.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Trailhead Goods sample database.")
    ap.add_argument("--path", type=Path, default=DB_PATH)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    stats = build(args.path, args.seed)
    print(f"Wrote {args.path}")
    for table, n in stats.items():
        print(f"  {table:16s} {n:>8,}")


if __name__ == "__main__":
    main()
