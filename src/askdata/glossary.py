"""Business glossary lookup (the retrieval part of RAG).

A small BM25 index over term, synonyms and definition. Keyword retrieval is
deterministic, free to run and good enough for a glossary of this size; an
embedding model can replace it behind the same search() call.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config

_TOKEN = re.compile(r"[a-z0-9$.]+")
_STOP = {
    "a", "an", "the", "of", "in", "on", "for", "to", "by", "and", "or", "is", "are", "was",
    "were", "what", "which", "who", "how", "many", "much", "do", "does", "did", "we", "our",
    "us", "me", "show", "list", "give", "with", "per", "each", "from", "at", "as", "it", "be",
}


def _tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        t = t.strip(".")
        if not t or t in _STOP:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        if len(t) > 5 and t.endswith("ed"):
            t = t[:-2]
        elif len(t) > 6 and t.endswith("ing"):
            t = t[:-3]
        out.append(t)
    return out


@dataclass
class Entry:
    term: str
    synonyms: list[str]
    definition: str
    sql: str

    def as_dict(self) -> dict[str, Any]:
        return {"term": self.term, "synonyms": self.synonyms, "definition": self.definition, "sql": self.sql}


class Glossary:
    def __init__(self, path: Path | str = config.GLOSSARY_PATH, k1: float = 1.2, b: float = 0.75) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.as_of_date: str = data.get("as_of_date", "")
        self.entries = [Entry(**e) for e in data["entries"]]
        self.k1, self.b = k1, b
        # Term counted 3x and synonyms 2x so a direct name match outranks a passing mention.
        self.docs = [
            _tokens(" ".join([e.term] * 3 + e.synonyms * 2 + [e.definition])) for e in self.entries
        ]
        self.tf = [Counter(d) for d in self.docs]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        q = _tokens(query)
        scores = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scores.append((s, i))
        scores.sort(key=lambda x: (-x[0], x[1]))
        return [dict(self.entries[i].as_dict(), score=round(s, 3)) for s, i in scores[:k]]
