"""URL cleaning utilities: strip tracking parameters, build canonical form."""
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Common tracking / irrelevant query parameters to strip
_STRIP_PARAMS = frozenset({
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'utm_id', 'utm_reader', 'ref', 'output', 'fbclid', 'gclid', 'msclkid',
    'twclid', '_hsmi', '_hsenc', 'mc_cid', 'mc_eid', 'si', 'igshid',
})


def clean_url(url: str) -> str:
    """Remove tracking query parameters from a URL."""
    if not url:
        return url
    url = url.strip()
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in params.items() if k.lower() not in _STRIP_PARAMS}
    return urlunparse(parsed._replace(query=urlencode(cleaned, doseq=True)))


def canonical_url(url: str) -> str:
    """Return scheme-free, param-stripped, path-normalised URL for dedup keys."""
    if not url:
        return ''
    parsed = urlparse(clean_url(url))
    # netloc + path, no trailing slash, lowercase scheme/host
    return f"{parsed.netloc.lower()}{parsed.path}".rstrip('/')
