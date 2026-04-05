"""
Gulf News (gulfnews.com) news spider.

Source: https://gulfnews.com/search?q=<keyword>&sort=score

Mechanism:
  Gulf News runs on the Quintype CMS.  The Quintype JSON search API is
  accessible without authentication and without Cloudflare blocking:
    GET https://gulfnews.com/api/v1/search?q=<keyword>&limit=20&offset=<N>

  Response structure:
    results.total   - total matching articles
    results.stories - array of articles, sorted by relevance score
                      (Quintype's scoring has a strong recency bias, so
                      offset=0 pages tend to contain the newest articles)

  For each TAX_KEYWORD, up to _MAX_PAGES (5 × 20 = 100 results) are fetched.
  Pagination continues while:
    - the current page returned any articles within the cutoff window, AND
    - fewer than _MAX_PAGES pages have been requested for this keyword.
  Results are deduplicated by URL across all keyword queries.

Thumbnail URL:
  - If story["hero-image-metadata"]["original-url"] is set (agency images such
    as AP/AFP), that URL is used directly.
  - Otherwise: https://media.assettype.com/<hero-image-s3-key>

Category:
  First entry of story["sections"][0]["display-name"], if present.

Fields extracted:
  - headline              → title
  - https://gulfnews.com/<slug> → URL
  - published-at (ms UTC) → pubDate (ISO 8601)
  - subheadline           → description
  - hero-image-s3-key / hero-image-metadata.original-url → thumbnail
  - sections[0].display-name → category
"""
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.keywords import TAX_KEYWORDS

BASE_URL = "https://gulfnews.com"
_API_URL = f"{BASE_URL}/api/v1/search"
_CDN_BASE = "https://media.assettype.com"

_PAGE_SIZE = 20
_MAX_PAGES = 5   # up to 100 results per keyword


def _epoch_ms_to_iso(ms: int) -> str:
    """Convert a Unix timestamp in milliseconds to ISO 8601 UTC string."""
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        return dt.isoformat()
    except Exception:
        return ""


def _thumbnail(story: dict) -> str:
    """Return the best available thumbnail URL for a story."""
    # Agency images (AP, AFP, Reuters) have a direct original-url
    meta = story.get("hero-image-metadata") or {}
    original = meta.get("original-url", "")
    if original:
        return original
    s3_key = story.get("hero-image-s3-key", "")
    return f"{_CDN_BASE}/{s3_key}" if s3_key else ""


class GulfNewsSpider(BaseNewsSpider):
    """Quintype REST API spider for Gulf News.

    Issues paginated GET requests to the Quintype /api/v1/search endpoint for
    each TAX_KEYWORD.  Pagination stops when all results in a page fall outside
    the configured cutoff window, or when _MAX_PAGES pages have been fetched.
    Thumbnails are constructed from the hero-image-s3-key or original-url
    embedded in each story's metadata — no extra HTTP requests are needed.
    """

    name = "gulfnews_news"

    custom_settings = {
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "DOWNLOAD_DELAY": 0.3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 3,
    }

    _DEFAULT_SOURCE_CONFIG = {
        "id": "gulfnews",
        "name": "Gulf News",
        "sourceType": "media",
        "sourceCountry": "AE",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/",
            "apiBase": _API_URL,
            "country": "United Arab Emirates",
            "countries": ["United Arab Emirates", "Middle East"],
            "primaryCountry": "United Arab Emirates",
            "jurisdictions": ["AE"],
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen_slugs: set[str] = set()

    # ------------------------------------------------------------------ #
    # Request building                                                     #
    # ------------------------------------------------------------------ #

    def _make_request(self, keyword: str, offset: int) -> scrapy.Request:
        params = {"q": keyword, "limit": _PAGE_SIZE, "offset": offset}
        url = f"{_API_URL}?{urlencode(params)}"
        return scrapy.Request(
            url,
            callback=self.parse_search,
            headers={"Accept": "application/json"},
            meta={"keyword": keyword, "offset": offset},
        )

    async def start(self):
        for kw in TAX_KEYWORDS:
            yield self._make_request(kw, offset=0)

    # ------------------------------------------------------------------ #
    # Parsing                                                              #
    # ------------------------------------------------------------------ #

    def parse_search(self, response):
        keyword = response.meta["keyword"]
        offset = response.meta["offset"]
        page_num = offset // _PAGE_SIZE + 1

        try:
            data = response.json()
        except Exception:
            self.logger.warning(
                f"[gulfnews] Non-JSON response for keyword={keyword!r} offset={offset}"
            )
            return

        stories = (data.get("results") or {}).get("stories") or []
        if not stories:
            return

        self.logger.debug(
            f"[gulfnews] keyword={keyword!r} page={page_num}: {len(stories)} stories"
        )

        in_window_count = 0
        for story in stories:
            pub_ms = story.get("published-at") or story.get("first-published-at") or 0
            pub_date = _epoch_ms_to_iso(pub_ms)

            if not self.is_within_timeframe(pub_date):
                continue
            in_window_count += 1

            slug = story.get("slug", "")
            if not slug or slug in self._seen_slugs:
                continue
            self._seen_slugs.add(slug)

            link = f"{BASE_URL}/{slug}"
            headline = story.get("headline", "").strip()
            if not headline:
                continue

            subheadline = (story.get("subheadline") or "").strip()
            thumbnail = _thumbnail(story)

            sections = story.get("sections") or []
            category = sections[0].get("display-name", "") if sections else ""

            yield self.build_item(
                title=headline,
                link=link,
                description=subheadline,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )

        # Paginate: continue if this page had in-window articles and max pages not reached
        next_offset = offset + _PAGE_SIZE
        if in_window_count > 0 and page_num < _MAX_PAGES and len(stories) == _PAGE_SIZE:
            yield self._make_request(keyword, next_offset)
