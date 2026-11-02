"""Compute expected answers by running every gold SQL query against the database.

    python -m bench.build_expected          # writes bench/expected.json
    python -m bench.build_expected --check  # fails if expected.json is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from askdata.db import run_query

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.json"
EXPECTED = HERE / "expected.json"


def load_questions() -> list[dict]:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]


def compute() -> dict[str, dict]:
    out = {}
    for q in load_questions():
        res = run_query(q["gold_sql"])
        if res.truncated or not res.rows:
            raise SystemExit(f"{q['id']}: gold query returned {'too many' if res.truncated else 'no'} rows")
        out[q["id"]] = {"columns": res.columns, "rows": res.rows}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    data = compute()
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    if args.check:
        if not EXPECTED.exists() or EXPECTED.read_text(encoding="utf-8") != text:
            sys.exit("bench/expected.json is out of date; run python -m bench.build_expected")
        print("expected.json is up to date")
        return
    EXPECTED.write_text(text, encoding="utf-8")
    print(f"wrote {EXPECTED} ({len(data)} questions)")


if __name__ == "__main__":
    main()
