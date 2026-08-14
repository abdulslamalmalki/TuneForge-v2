"""Broader approved-library discovery for questions not covered by accepted claims."""

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.search.query import SearchResult, search_approved

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+()./-]*")
_STOP_WORDS = frozenset(
    {"a", "about", "an", "and", "are", "best", "can", "do", "does", "for", "how", "i",
     "in", "is", "it", "me", "my", "of", "on", "or", "please", "setting", "settings",
     "should", "tell", "the", "this", "to", "what", "which", "with"}
)


@dataclass(frozen=True)
class ApprovedLibraryMatches:
    query_terms: tuple[str, ...]
    results: tuple[SearchResult, ...]


def find_approved_matches(
    session: Session,
    question: str,
    *,
    limit: int = 5,
) -> ApprovedLibraryMatches:
    """Return approved source records only; never treat them as generated claims."""

    terms = tuple(
        dict.fromkeys(
            token
            for token in _TOKEN.findall(question)
            if len(token) >= 2 and token.casefold() not in _STOP_WORDS
        )
    )[:8]
    if not terms:
        return ApprovedLibraryMatches(query_terms=(), results=())
    attempts = [" ".join(terms)]
    attempts.extend(sorted(terms, key=lambda item: (-len(item), item.casefold())))
    results: list[SearchResult] = []
    seen: set[int] = set()
    for query in attempts:
        for result in search_approved(session, query):
            if result.setting_id in seen:
                continue
            seen.add(result.setting_id)
            results.append(result)
            if len(results) >= max(1, min(limit, 10)):
                return ApprovedLibraryMatches(query_terms=terms, results=tuple(results))
    return ApprovedLibraryMatches(query_terms=terms, results=tuple(results))
