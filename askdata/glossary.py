"""Business glossary and verified example queries, with BM25 retrieval.

This is the RAG part of the agent: the closest definitions and example queries
are pulled into the prompt for each question, and the agent can search for more.
BM25 keeps it dependency-free and explainable; swap in embeddings for a large
glossary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from askdata.config import EXAMPLES_PATH, GLOSSARY_PATH

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "had", "has",
    "have", "how", "i", "in", "is", "it", "its", "me", "much", "of", "on", "or", "our", "show", "that",
    "the", "their", "there", "this", "to", "us", "was", "we", "were", "what", "when", "which", "who",
    "with", "give", "list", "tell", "many", "each", "per", "all", "any",
}


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for t in tokens:
        if t in STOPWORDS:
            continue
        if len(t) > 4 and t.endswith("ies"):
            t = t[:-3] + "y"
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


@dataclass(frozen=True)
class Doc:
    kind: str  # "term" or "example"
    title: str
    body: str
    sql: str
    tokens: tuple[str, ...]


class BM25:
    def __init__(self, docs: list[Doc], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self.avgdl = sum(len(d.tokens) for d in docs) / max(len(docs), 1)
        df: dict[str, int] = {}
        for d in docs:
            for t in set(d.tokens):
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for d in self.docs:
            tf: dict[str, int] = {}
            for t in d.tokens:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d.tokens) / self.avgdl))
            out.append(s)
        return out


class KnowledgeBase:
    def __init__(self, glossary_path: Path = GLOSSARY_PATH, examples_path: Path = EXAMPLES_PATH) -> None:
        terms = yaml.safe_load(glossary_path.read_text())
        examples = yaml.safe_load(examples_path.read_text())
        self.terms = [
            Doc(
                "term",
                t["term"],
                t["definition"].strip(),
                (t.get("sql") or "").strip(),
                # Term and aliases are repeated so a name match outweighs a passing mention.
                tuple(tokenize(" ".join([t["term"]] * 3 + list(t.get("aliases", [])) * 2 + [t["definition"]]))),
            )
            for t in terms
        ]
        self.examples = [
            Doc("example", e["question"], "", e["sql"].strip(), tuple(tokenize(e["question"])))
            for e in examples
        ]
        self._terms_index = BM25(self.terms)
        self._examples_index = BM25(self.examples)

    def search(self, query: str, k_terms: int = 4, k_examples: int = 2) -> tuple[list[Doc], list[Doc]]:
        def top(index: BM25, docs: list[Doc], k: int) -> list[Doc]:
            ranked = sorted(zip(index.scores(query), range(len(docs))), key=lambda x: (-x[0], x[1]))
            return [docs[i] for s, i in ranked[:k] if s > 0]

        return top(self._terms_index, self.terms, k_terms), top(self._examples_index, self.examples, k_examples)

    @staticmethod
    def render(terms: list[Doc], examples: list[Doc]) -> str:
        parts = []
        for t in terms:
            block = f"## {t.title}\n{t.body}"
            if t.sql:
                block += f"\nSQL pattern:\n{t.sql}"
            parts.append(block)
        for e in examples:
            parts.append(f"## Verified example: {e.title}\n{e.sql}")
        return "\n\n".join(parts) if parts else "No matching glossary entries."

    def lookup(self, query: str, k_terms: int = 4, k_examples: int = 2) -> str:
        return self.render(*self.search(query, k_terms, k_examples))

    def all_terms(self) -> list[str]:
        return [t.title for t in self.terms]


@lru_cache(maxsize=1)
def knowledge_base() -> KnowledgeBase:
    return KnowledgeBase()
