"""Compare an agent's SQL result with the gold result.

Execution-based grading: the agent's final SQL is run through the same
guardrails and its result set is compared with the gold query's result.
Column names and order do not matter, extra columns are allowed, and a
person's name split over two adjacent text columns also matches.

Match modes (per question):
  scalar   the gold value appears in the first result row
  rows     same rows, any order (on the gold columns being compared)
  ordered  same rows in the same order
  values   every gold value appears somewhere in the result (shape-free)

strict=True applies only the rules above. strict=False (used for the
published "corrected" score) adds two rules, applied to every model alike:
  scalar   also correct when the gold value appears anywhere in the result
           AND the answer text states it (the SQL listed a breakdown and the
           answer picked the right line).
  rows     extra result rows whose numbers are all 0 or empty are ignored
           (e.g. listing every employee with 0 customers next to the reps).
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from collections import Counter
from typing import Any

DEFAULT_TOL = 0.01
MAX_VALUES_CELLS = 60


def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = unicodedata.normalize("NFKC", str(v)).strip()
    try:
        return float(s.replace(",", "").lstrip("$").rstrip("%"))
    except ValueError:
        return s.casefold()


def eq(a: Any, b: Any, tol: float = DEFAULT_TOL, percent: bool = False) -> bool:
    """a is the gold value, b the predicted value."""
    a, b = _norm(a), _norm(b)
    if isinstance(a, float) and isinstance(b, float):
        if abs(a - b) <= tol + 1e-9:
            return True
        return percent and abs(a - b * 100) <= tol + 1e-9
    return a == b


def _columns(rows: list[list[Any]]) -> list[list[Any]]:
    """Result columns plus 'first last' joins of adjacent text columns."""
    if not rows:
        return []
    n = len(rows[0])
    cols = [[r[i] for r in rows] for i in range(n)]
    for i in range(n - 1):
        if all(isinstance(r[i], str) and isinstance(r[i + 1], str) for r in rows):
            cols.append([f"{r[i]} {r[i + 1]}" for r in rows])
    return cols


def _col_match(gold: list[Any], pred: list[Any], ordered: bool, tol: float, percent: bool) -> bool:
    if len(gold) != len(pred):
        return False
    if ordered:
        return all(eq(g, p, tol, percent) for g, p in zip(gold, pred))
    used = [False] * len(pred)
    for g in gold:
        for j, p in enumerate(pred):
            if not used[j] and eq(g, p, tol, percent):
                used[j] = True
                break
        else:
            return False
    return True


def _rows_match(gold_rows: list[tuple], pred_rows: list[tuple], ordered: bool, tol: float, percent: bool) -> bool:
    if ordered:
        return all(all(eq(g, p, tol, percent) for g, p in zip(gr, pr)) for gr, pr in zip(gold_rows, pred_rows))
    remaining = list(pred_rows)
    for gr in gold_rows:
        for j, pr in enumerate(remaining):
            if all(eq(g, p, tol, percent) for g, p in zip(gr, pr)):
                remaining.pop(j)
                break
        else:
            return False
    return True


_NUM = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?%?")


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text or ""):
        try:
            out.append(float(m.replace("$", "").replace(",", "").rstrip("%")))
        except ValueError:
            pass
    return out


def stated_in_answer(value: Any, answer_text: str | None, tol: float = DEFAULT_TOL, percent: bool = False) -> bool:
    """Does the prose answer state this value (a number within tolerance, or the exact text as a whole word)?"""
    if not answer_text:
        return False
    v = _norm(value)
    if v is None:
        return False
    if isinstance(v, float):
        return any(eq(value, n, tol, percent) for n in _numbers_in(answer_text))
    text = unicodedata.normalize("NFKC", answer_text).casefold()
    return re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", text) is not None


def _zero_row(row: list[Any]) -> bool:
    """True when every numeric (or empty) cell is 0 or empty and there is at least one such cell."""
    vals = [_norm(v) for v in row]
    nums = [v for v in vals if v is None or isinstance(v, float)]
    return bool(nums) and all(v is None or v == 0 for v in nums)


def grade(
    question: dict[str, Any],
    gold_rows: list[list[Any]],
    pred_rows: list[list[Any]] | None,
    answer_text: str | None = None,
    strict: bool = True,
) -> tuple[bool, str]:
    """Return (correct, reason). strict=False adds the two corrected rules described above."""
    if pred_rows is None:
        return False, "no result"
    mode = question.get("match", "rows")
    tol = float(question.get("tol", DEFAULT_TOL))
    percent = bool(question.get("percent", False))
    ncols = len(gold_rows[0]) if gold_rows else 0
    cmp_cols = question.get("compare_columns") or list(range(ncols))

    if mode == "scalar":
        if not pred_rows:
            return False, "empty result"
        target = gold_rows[0][cmp_cols[0]]
        first = [c[0] for c in _columns(pred_rows[:1])]
        if any(eq(target, v, tol, percent) for v in first):
            return True, "ok"
        if not strict:
            anywhere = [v for col in _columns(pred_rows) for v in col]
            if any(eq(target, v, tol, percent) for v in anywhere) and stated_in_answer(target, answer_text, tol, percent):
                return True, "ok (value in the result and stated in the answer)"
        return False, f"expected {target!r} in first row"

    if mode == "values":
        cells = [v for r in pred_rows for v in r]
        cells += [c[0] for r in pred_rows for c in _columns([r])[len(r):]]
        if len(cells) > MAX_VALUES_CELLS:
            return False, "result too large for a values match"
        used = [False] * len(cells)
        for r in gold_rows:
            for i in cmp_cols:
                for j, v in enumerate(cells):
                    if not used[j] and eq(r[i], v, tol, percent):
                        used[j] = True
                        break
                else:
                    return False, f"value {r[i]!r} not found"
        return True, "ok"

    ordered = mode == "ordered"
    if not strict and mode == "rows" and len(pred_rows) > len(gold_rows) and not any(_zero_row(r) for r in gold_rows):
        pred_rows = [r for r in pred_rows if not _zero_row(r)]
    if len(pred_rows) != len(gold_rows):
        return False, f"expected {len(gold_rows)} rows, got {len(pred_rows)}"
    if not gold_rows:
        return True, "ok"
    pcols = _columns(pred_rows)
    gcols = {i: [r[i] for r in gold_rows] for i in cmp_cols}
    cands = {i: [k for k, pc in enumerate(pcols) if _col_match(gcols[i], pc, ordered, tol, percent)] for i in cmp_cols}
    for i, ks in cands.items():
        if not ks:
            return False, f"no result column matches gold column {i}"
    for combo in itertools.product(*(cands[i] for i in cmp_cols)):
        if len(set(combo)) != len(combo):
            continue
        g = [tuple(r[i] for i in cmp_cols) for r in gold_rows]
        p = [tuple(pcols[k][j] for k in combo) for j in range(len(pred_rows))]
        if _rows_match(g, p, ordered, tol, percent):
            return True, "ok"
    return False, "columns match individually but not row by row"


def counts(xs: list[bool]) -> Counter:
    return Counter(xs)
