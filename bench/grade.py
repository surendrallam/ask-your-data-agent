"""Execution-match grading.

The agent's cited SQL is re-run and its rows are compared with the reference
rows. A reference row matches an agent row when every reference value can be
paired with a different value in the agent row (extra columns are fine):
  - integers (counts, units) must be equal;
  - other numbers within 0.01 or 0.01% (rounding to cents is fine);
  - rates within 0.06 percentage points, as a fraction or a percentage;
  - text compared case-insensitively.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from askdata.db import QueryError, run_query

ABS_TOL = 0.01
REL_TOL = 1e-4
RATIO_TOL = 0.0006  # on the fraction scale


@dataclass
class Grade:
    passed: bool
    reason: str


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "").rstrip("%").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def values_match(ref, got, ratio: bool = False) -> bool:
    if ref is None:
        return got is None
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        g = _num(got)
        if g is None:
            return False
        if ratio:
            return abs(g - ref) <= RATIO_TOL or abs(g - ref * 100) <= RATIO_TOL * 100
        if isinstance(ref, int):
            return float(g) == float(ref)
        return abs(g - ref) <= ABS_TOL or abs(g - ref) <= REL_TOL * abs(ref)
    return got is not None and _norm(str(ref)) == _norm(str(got))


def row_contains(ref_row, got_row, ratio: bool = False) -> bool:
    used = [False] * len(got_row)

    def assign(i: int) -> bool:
        if i == len(ref_row):
            return True
        for j, g in enumerate(got_row):
            if not used[j] and values_match(ref_row[i], g, ratio):
                used[j] = True
                if assign(i + 1):
                    return True
                used[j] = False
        return False

    return assign(0)


def _perfect_matching(ref_rows, got_rows, ratio: bool) -> bool:
    """Kuhn's algorithm: every reference row gets its own agent row."""
    match_of_got: dict[int, int] = {}
    edges = [[j for j, g in enumerate(got_rows) if row_contains(r, g, ratio)] for r in ref_rows]

    def try_assign(i: int, seen: set[int]) -> bool:
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in match_of_got or try_assign(match_of_got[j], seen):
                match_of_got[j] = i
                return True
        return False

    return all(try_assign(i, set()) for i in range(len(ref_rows)))


def tables_match(ref_rows, got_rows, ordered=False, prefix=False, ratio=False) -> tuple[bool, str]:
    if prefix:
        if len(got_rows) < len(ref_rows):
            return False, f"{len(got_rows)} rows, expected at least {len(ref_rows)}"
        for i, ref in enumerate(ref_rows):
            if not row_contains(ref, got_rows[i], ratio):
                return False, f"row {i + 1} does not match {list(ref)}: got {list(got_rows[i])}"
        return True, "match"
    if len(got_rows) != len(ref_rows):
        return False, f"{len(got_rows)} rows, expected {len(ref_rows)}"
    if ordered:
        for i, (ref, got) in enumerate(zip(ref_rows, got_rows)):
            if not row_contains(ref, got, ratio):
                return False, f"row {i + 1} does not match {list(ref)}: got {list(got)}"
        return True, "match"
    if _perfect_matching(ref_rows, got_rows, ratio):
        return True, "match"
    return False, "rows do not match the reference"


@lru_cache(maxsize=256)
def reference_rows(sql: str) -> tuple:
    return tuple(tuple(r) for r in run_query(sql, max_rows=5000).rows)


def grade(question: dict, result: dict) -> Grade:
    expect = question.get("expect", "answer")
    status = result.get("status")
    if expect == "clarify":
        if status == "clarify":
            return Grade(True, "asked for clarification")
        return Grade(False, f"expected a clarifying question, got {status}")
    if status == "clarify":
        return Grade(False, "asked for clarification on an answerable question")
    if status != "answered" or not result.get("sql"):
        return Grade(False, f"no answer: {result.get('error') or status}")
    ref = reference_rows(question["sql"])
    try:
        got = run_query(result["sql"], max_rows=5000).rows
    except QueryError as e:
        return Grade(False, f"cited SQL fails on re-run: {e}")
    ok, why = tables_match(ref, got, question.get("ordered", False), question.get("prefix", False),
                           question.get("ratio", False))
    return Grade(ok, why)
