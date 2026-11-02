"""Build data/chinook.sqlite from the upstream Chinook sample database.

Source: https://github.com/lerocha/chinook-database (MIT licence, see
data/LICENSE-chinook.md). The upstream file is downloaded, its SHA-256 is
checked, and contact-style columns (street addresses, postal codes, phone,
fax, email, birth dates) are removed so the shipped database holds no
contact details, even fictitious ones. City, state and country stay, so
geographic questions still work.

Usage:
    python scripts/build_db.py            # download + build
    python scripts/build_db.py --src FILE # build from a local copy
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

UPSTREAM_URL = (
    "https://raw.githubusercontent.com/lerocha/chinook-database/master/"
    "ChinookDatabase/DataSources/Chinook_Sqlite.sqlite"
)
UPSTREAM_SHA256 = "7651ba378ac2fcd0dfc3c66fb101f7a7eed3ba39a612ec642b96e20702061f15"
OUT = Path(__file__).resolve().parent.parent / "data" / "chinook.sqlite"

DROP_COLUMNS = {
    "Customer": ["Address", "PostalCode", "Phone", "Fax", "Email"],
    "Employee": ["Address", "PostalCode", "Phone", "Fax", "Email", "BirthDate"],
    "Invoice": ["BillingAddress", "BillingPostalCode"],
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def build(src: Path, out: Path = OUT) -> None:
    digest = sha256(src)
    if digest != UPSTREAM_SHA256:
        sys.exit(f"SHA-256 mismatch for {src}: {digest} (expected {UPSTREAM_SHA256})")
    tmp = out.with_suffix(".tmp")
    shutil.copyfile(src, tmp)
    con = sqlite3.connect(tmp)
    try:
        for table, cols in DROP_COLUMNS.items():
            for col in cols:
                con.execute(f'ALTER TABLE "{table}" DROP COLUMN "{col}"')
        # Customer.Email was NOT NULL upstream; DROP COLUMN handles that.
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    tmp.replace(out)
    print(f"wrote {out} ({out.stat().st_size} bytes, sha256 {sha256(out)})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, help="local copy of Chinook_Sqlite.sqlite")
    args = ap.parse_args()
    if args.src:
        build(args.src)
        return
    with tempfile.TemporaryDirectory() as d:
        dl = Path(d) / "Chinook_Sqlite.sqlite"
        urllib.request.urlretrieve(UPSTREAM_URL, dl)
        build(dl)


if __name__ == "__main__":
    main()
