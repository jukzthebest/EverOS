"""Literal-term guard for identifier-like search queries.

Vector recall is useful for natural-language intent, but poor for exact
environment names, versions, code symbols, paths, and command fragments. This
module extracts only those literal anchors and lets callers drop hits that do
not contain them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{1,}")
_STOPWORDS = frozenset(
    {
        "and",
        "for",
        "from",
        "how",
        "into",
        "please",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "why",
        "with",
    }
)
_LITERAL_CHARS = frozenset("_./:-")


def required_literal_terms(query: str) -> list[str]:
    """Return exact anchors that should be present in every acceptable hit."""
    terms: list[str] = []
    for match in _TOKEN_RE.finditer(query):
        term = match.group(0).strip(".,;:!?()[]{}<>\"'").lower()
        if not term or term in _STOPWORDS or not _is_literal_anchor(term):
            continue
        if term not in terms:
            terms.append(term)
    return terms


def contains_required_literal_terms(text: str, terms: Iterable[str]) -> bool:
    haystack = text.lower()
    return all(term in haystack for term in terms)


def _is_literal_anchor(term: str) -> bool:
    return any(char.isdigit() for char in term) or any(
        char in term for char in _LITERAL_CHARS
    )
