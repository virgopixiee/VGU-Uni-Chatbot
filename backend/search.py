import re
from collections import Counter
from dataclasses import dataclass
from textwrap import wrap

from backend.pages import Page
from functools import lru_cache

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
STOP_WORDS = {
    "a", "an", "and", "are", "can", "do", "does", "for", "from", "give",
    "how", "i", "in", "is", "it", "list", "me", "of", "on", "please",
    "provide", "show", "specific", "tell", "the", "to", "what", "which",
    "with",
}
MAX_EXCERPT_CHARS = 3500
MAX_TITLE_MATCH_EXCERPT_CHARS = 20_000
EXCERPT_CHUNK_CHARS = 700
QUERY_SYNONYMS = {
    "facility": {
        "facility", "facilities", "infrastructure", "laboratory", "laboratories",
        "library", "campus", "building", "buildings",
    },
    "facilities": {
        "facility", "facilities", "infrastructure", "laboratory", "laboratories",
        "library", "campus", "building", "buildings",
    },
    "requirement": {
        "requirement", "requirements", "admission", "eligibility", "eligible",
        "criteria", "qualification", "qualifications", "documents",
    },
    "requirements": {
        "requirement", "requirements", "admission", "eligibility", "eligible",
        "criteria", "qualification", "qualifications", "documents",
    },
    "program": {
        "program", "programs", "study", "degree", "degrees", "bachelor", "master",
    },
    "programs": {
        "program", "programs", "study", "degree", "degrees", "bachelor", "master",
    },
    "tuition": {"tuition", "fee", "fees", "financing", "scholarship", "scholarships"},
    "fees": {"tuition", "fee", "fees", "financing", "scholarship", "scholarships"},
}


@dataclass(frozen=True)
class SearchResult:
    page: Page
    score: float
    excerpt: str


def _tokens(text: str) -> list[str]:
    return [
        token.casefold()
        for token in TOKEN_RE.findall(text)
        if token.casefold() not in STOP_WORDS
    ]


def _excerpt(
    page: Page, query_tokens: set[str], has_title_match: bool
) -> str:
    if has_title_match:
        return page.text[:MAX_TITLE_MATCH_EXCERPT_CHARS].strip()
    if len(page.text) <= MAX_EXCERPT_CHARS:
        return page.text.strip()

    chunks = [
        chunk
        for line in page.text.splitlines()
        for chunk in wrap(line.strip(), width=EXCERPT_CHUNK_CHARS)
        if line.strip()
    ]
    ranked_chunks = sorted(
        enumerate(chunks),
        key=lambda item: (
            -sum(token in _tokens(item[1]) for token in query_tokens),
            item[0],
        ),
    )
    selected: list[tuple[int, str]] = []
    used_chars = 0
    for index, chunk in ranked_chunks:
        if selected and used_chars + len(chunk) > MAX_EXCERPT_CHARS:
            continue
        selected.append((index, chunk))
        used_chars += len(chunk) + 1
        if used_chars >= MAX_EXCERPT_CHARS:
            break
    selected.sort()
    return "\n".join(chunk for _index, chunk in selected)


@lru_cache(maxsize=None)
def _page_index(page: Page):
    page_tokens = Counter(_tokens(f"{page.title} {page.url} {page.text}"))
    url_tokens = frozenset(_tokens(page.url))
    title_tokens = frozenset(_tokens(page.title))
    return page_tokens, url_tokens, title_tokens


def search_pages(question: str, pages: list[Page], limit: int = 8) -> list[SearchResult]:
    query_tokens = _tokens(question)
    if not query_tokens:
        return []

    query_counts = Counter(query_tokens)
    related_terms = {
        related
        for token in query_counts
        for related in QUERY_SYNONYMS.get(token, set())
        if related not in query_counts
    }
    scored = []
    for page in pages:
        page_tokens, url_tokens, title_tokens = _page_index(page)
        overlap = sum(min(count, page_tokens[token]) for token, count in query_counts.items())
        related_matches = len(related_terms.intersection(page_tokens))
        url_matches = len(set(query_counts).intersection(url_tokens))
        if overlap == 0 and related_matches == 0:
            continue
        title_matches = sum(1 for token in query_counts if token in title_tokens)
        score = (
            overlap / (sum(query_counts.values()) ** 0.5)
            + related_matches * 0.25
            + title_matches * 1.5
            + url_matches * 2
        )
        scored.append((score, title_matches, page))

    scored.sort(key=lambda item: item[0], reverse=True)
    results = []
    for score, title_matches, page in scored[:limit]:
        excerpt = _excerpt(
            page,
            set(query_counts).union(related_terms),
            has_title_match=bool(title_matches),
        )
        results.append(SearchResult(page=page, score=score, excerpt=excerpt))
    return results