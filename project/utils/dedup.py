"""
Deduplication and deterministic ID generation.

Dedup strategy (first match wins — article is dropped if already seen):
  1. Canonical URL  — same URL regardless of source
  2. Normalized title — catches syndicated/mirrored releases across different sources
"""
import hashlib
import re
from project.utils.url_utils import canonical_url


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = (title or '').lower()
    t = re.sub(r'[^\w\s]', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def make_id(article: dict) -> str:
    """
    Deterministic 16-char hex ID based on canonical URL.
    Falls back to normalised title if URL is empty.
    """
    key = canonical_url(article.get('link', '')) or _normalize_title(article.get('title', ''))
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]


def deduplicate(articles: list[dict]) -> list[dict]:
    """
    Remove duplicates from a mixed list (existing + new).
    Preserves the FIRST occurrence (existing articles take priority over new
    when merged as existing + new).
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    result: list[dict] = []

    for article in articles:
        url = canonical_url(article.get('link', ''))
        title = _normalize_title(article.get('title', ''))

        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue

        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)

        result.append(article)

    return result
