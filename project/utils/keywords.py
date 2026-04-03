"""
Keyword matching for tax-related news articles.

Python translation of the JS keyword-matching logic:
  - Normalize text (lowercase + collapse dashes to spaces)
  - Build word-boundary-aware regexes with singular/plural handling
  - Return which keywords matched
"""
import re

TAX_KEYWORDS = [
    'corporate tax',
    'vat',
    'value added tax',
    'excise tax',
    'excise',
    'customs',
    'customs duty',
    'customs tax',
    'transfer pricing',
    'top-up tax',
    'top up tax',
    'withholding tax',
    'zakat',
    'cabinet decision',
    'ministerial decision',
    'implementing regulation',
    'guide',
    'public clarification',
    'faq',
    'tax treaty',
    'double tax treaty',
    'tax agreement',
    'double tax avoidance agreement',
    'dtaa',
    'e-invoicing',
    'e invoicing',
    'einvoicing',
    'pillar two',
    'pillar 2',
    'dmtt',
    'advance pricing agreement',
    'apa',
]


def _normalize(s: str) -> str:
    """Lowercase + collapse hyphens/dashes to spaces + collapse whitespace."""
    s = (s or '').lower()
    s = re.sub(r'[-\u2013\u2014]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _plural_pattern(word: str) -> str:
    """Build a singular/plural pattern for the last word of a keyword."""
    esc = re.escape(word)
    if re.search(r'[^aeiou]y$', word, re.IGNORECASE):
        stem = re.escape(word[:-1])
        return f'(?:{esc}|{stem}ies)'
    if re.search(r'(s|x|z|ch|sh)$', word, re.IGNORECASE):
        return f'(?:{esc}|{esc}es)'
    return f'(?:{esc}|{esc}s)'


def _compile_keyword(kw: str) -> re.Pattern:
    parts = _normalize(kw).split(' ')
    prefix = ' '.join(re.escape(p) for p in parts[:-1])
    last_pat = _plural_pattern(parts[-1])
    body = f'{prefix} {last_pat}' if prefix else last_pat
    return re.compile(rf'\b{body}\b')


# Pre-compile once at import time for performance
_COMPILED: list[tuple[str, re.Pattern]] = [
    (kw, _compile_keyword(kw)) for kw in TAX_KEYWORDS
]


def get_matched_keywords(article: dict) -> list[str]:
    """Return list of TAX_KEYWORDS found in the article's title/description/category."""
    haystack = _normalize(
        f"{article.get('title', '')} {article.get('description', '')} {article.get('category', '')}"
    )
    return [kw for kw, pat in _COMPILED if pat.search(haystack)]


def is_relevant_article(article: dict) -> bool:
    return bool(get_matched_keywords(article))
