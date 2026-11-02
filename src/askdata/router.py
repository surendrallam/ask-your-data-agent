"""Pick a starting model: Haiku for routine questions, Sonnet for hard ones.

A zero-cost keyword heuristic. Hard means the question needs multi-step SQL:
growth or comparisons between periods, shares of a total, rankings inside
groups, negations (customers who never ...), or several conditions at once.
The agent also escalates to Sonnet at run time when Haiku's first SQL fails.
"""

from __future__ import annotations

import re

_HARD_PATTERNS: list[tuple[str, str]] = [
    (r"\b(growth|grew|grow|change[sd]?|increase[sd]?|decrease[sd]?|decline[sd]?)\b", "change over time"),
    (r"\b(year[- ]over[- ]year|yoy|month[- ]over[- ]month|mom|vs\.?|versus|compared?|comparison)\b", "comparison"),
    (r"\b(share|percent(age)?|%|proportion|ratio)\b", "share of total"),
    (r"\b(each|every|per)\b.*\b(top|best|highest|most|lowest)\b|\b(top|best|highest|most)\b.*\b(each|every|per)\b", "ranking within groups"),
    (r"\b(never|without|no\s+\w+\s+(in|since)|not\s+(bought|purchased|sold))\b", "negation / anti-join"),
    (r"\b(above|below|more than|less than)\s+(the\s+)?(average|mean|median)\b", "compare to an aggregate"),
    (r"\b(median|cumulative|running total|rolling|moving average|cohort|retention)\b", "advanced aggregate"),
    (r"\b(second|third|nth|rank(ed|ing)?)\b", "ranking"),
    (r"\b(both|and also|as well as|but not)\b", "multiple conditions"),
]


def classify(question: str) -> tuple[bool, list[str]]:
    q = question.lower()
    reasons = [label for pat, label in _HARD_PATTERNS if re.search(pat, q)]
    if len(q.split()) > 30:
        reasons.append("long question")
    return len(reasons) >= 2 or "ranking within groups" in reasons, reasons
