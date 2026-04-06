"""
OECD (oecd.org) news spider.

Source:
  https://www.oecd.org/en/search.html?q=<keyword>&orderBy=mostRelevant
  &page=0&facetTags=oecd-languages:en,oecd-policy-areas:pa15

Mechanism:
  The OECD search page (/en/search.html, /content/oecd/en/search.html) is 100%
  client-side rendered via an AEM web component (<oecd-search-app>).  All
  search logic is handled by the component loaded from the AEM cloud CDN.

  The component makes unauthenticated GET requests to the OECD's public search
  API (discovered from the component source bundle dist.76bd5ec3.js):
    https://api.oecd.org/webcms/search/faceted-search

  Query parameters (all query-string, NOT a POST body):
    siteName          = "oecd"
    searchTerm        = <keyword>
    interfaceLanguage = "en"
    orderBy           = "mostRecent"
    page              = 0-based page index
    pageSize          = number of results per page (up to ~25, default 10)
    hiddenFacets      = "oecd-policy-areas:pa15"   (Taxation topic area)
    hiddenFacets      = "oecd-languages:en"        (English only)

  The hiddenFacets parameter is repeated (one per facet), matching how the web
  component appends them via URLSearchParams.append().

  Response JSON structure:
    {
      "results": [ { title, description, url, publicationDateTime,
                     featuredImageUrl, tags, snippet, ... }, ... ],
      "facets":  [ ... ],
      "minPublicationYear": ...,
      "maxPublicationYear": ...,
      ...
    }

  Pagination continues while:
    - the page contained at least one article within the cutoff window, AND
    - fewer than _MAX_PAGES pages have been fetched for this keyword.

  No bot-protection is present on api.oecd.org — standard Scrapy HTTP works.

Fields extracted:
  - results[].title               → title
  - results[].url                 → URL
  - results[].publicationDateTime → pubDate (ISO 8601)
  - results[].description         → description
  - results[].featuredImageUrl    → thumbnail
  - results[].tags[].title        → category (first content-type tag found)
"""
from urllib.parse import urlencode

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.keywords import TAX_KEYWORDS

BASE_URL = "https://www.oecd.org"
_API_URL = "https://api.oecd.org/webcms/search/faceted-search"

_PAGE_SIZE = 20
_MAX_PAGES = 3   # 3 × 20 = up to 60 results per keyword


def _category(tags: list) -> str:
    """Return the first content-type tag title, or empty string."""
    for tag in (tags or []):
        tag_id = tag.get("id", "")
        if tag_id.startswith("oecd-content-types:"):
            return tag.get("title", "")
    return ""


class OecdNewsSpider(BaseNewsSpider):
    """OECD search API spider filtering to Taxation (pa15) content.

    Issues paginated GET requests to the OECD's public faceted-search API for
    each TAX_KEYWORD, filtering results to the Taxation policy area.  Results
    are deduplicated by URL across all keyword queries.  Pagination stops when
    all results in a page fall outside the configured cutoff window or when
    _MAX_PAGES pages have been fetched.
    """

    name = "oecd_news"

    custom_settings = {
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
    }

    _DEFAULT_SOURCE_CONFIG = {
        "id": "oecd",
        "name": "OECD",
        "sourceType": "media",
        "sourceCountry": "FR",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/en/topics/taxation.html",
            "apiBase": _API_URL,
            "country": "International",
            "countries": ["Middle East", "International"],
            "primaryCountry": "International",
            "jurisdictions": [],
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen_urls: set[str] = set()

    # ------------------------------------------------------------------ #
    # Request building                                                     #
    # ------------------------------------------------------------------ #

    def _make_request(self, keyword: str, page: int) -> scrapy.Request:
        # hiddenFacets must be repeated for each facet value; urlencode with
        # doseq=True handles the list correctly.
        params = [
            ("siteName", "oecd"),
            ("searchTerm", keyword),
            ("interfaceLanguage", "en"),
            ("orderBy", "mostRecent"),
            ("page", str(page)),
            ("pageSize", str(_PAGE_SIZE)),
            ("hiddenFacets", "oecd-policy-areas:pa15"),
            ("hiddenFacets", "oecd-languages:en"),
        ]
        url = f"{_API_URL}?{urlencode(params)}"
        return scrapy.Request(
            url,
            callback=self.parse_search,
            headers={
                "Accept": "application/json",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/en/search.html",
            },
            meta={"keyword": keyword, "page": page},
        )

    async def start(self):
        for kw in TAX_KEYWORDS:
            yield self._make_request(kw, page=0)

    # ------------------------------------------------------------------ #
    # Parsing                                                              #
    # ------------------------------------------------------------------ #

    def parse_search(self, response):
        keyword = response.meta["keyword"]
        page = response.meta["page"]

        try:
            data = response.json()
        except Exception:
            self.logger.warning(
                f"[oecd] Non-JSON response for keyword={keyword!r} page={page}"
            )
            return

        results = data.get("results") or []
        if not isinstance(results, list) or not results:
            return

        self.logger.debug(
            f"[oecd] keyword={keyword!r} page={page}: {len(results)} results"
        )

        in_window_count = 0
        for item in results:
            pub_date = (item.get("publicationDateTime") or "").strip()
            if not self.is_within_timeframe(pub_date):
                continue
            in_window_count += 1

            url = (item.get("url") or "").strip()
            if not url or url in self._seen_urls:
                continue
            self._seen_urls.add(url)

            title = (item.get("title") or "").strip()
            if not title:
                continue

            description = (item.get("description") or "").strip()
            thumbnail = (item.get("featuredImageUrl") or "").strip()
            # featuredImageUrl sometimes ends with a literal %3Fpreferwebp=true
            # (URL-encoded ?), which is fine — just keep it as-is.

            category = _category(item.get("tags") or [])

            yield self.build_item(
                title=title,
                link=url,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )

        # Paginate: continue if this page had in-window articles and max pages not reached
        if in_window_count > 0 and page + 1 < _MAX_PAGES and len(results) == _PAGE_SIZE:
            yield self._make_request(keyword, page + 1)
