"""
Zawya (zawya.com) news spider.

Source: https://www.zawya.com/en/search?q=<keyword>

Mechanism:
  Zawya is a Next.js site (Atex/Pace CMS).  Search results are loaded
  client-side via the public GraphQL endpoint:
    https://api.zawya.atexcloud.io/ace-pace-gateway/graphql

  For each keyword in TAX_KEYWORDS, this spider POSTs a paginated GraphQL
  ``search`` query with ``sort: DATE`` (newest-first) and paginates until
  the oldest article on a page is older than the cutoff, or until
  MAX_PAGES_PER_KW pages have been fetched for that keyword.  Results are
  deduplicated by URL across all keyword searches before being yielded.

Key fields extracted from the GraphQL ``Article`` type:
  - title, lead (description, may contain HTML), publishedDate,
    path (absolute URL), topMedia.baseUrl (thumbnail), parent.title (section)
"""
import json
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.keywords import TAX_KEYWORDS

BASE_URL = "https://www.zawya.com"
GQL_URL = "https://api.zawya.atexcloud.io/ace-pace-gateway/graphql"
SITE_ID = "contentid/section.zawya.en.site"
PAGE_SIZE = 25
MAX_PAGES_PER_KW = 15  # cap at 375 articles per keyword to prevent runaway

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities from a string."""
    return _HTML_TAG_RE.sub("", unescape(text or "")).strip()


_GQL_QUERY = """
query searchPage(
  $query: String!
  $limit: Int
  $offset: Int
  $siteId: ID!
  $sort: SearchSort
  $absolute: Boolean
) {
  search(
    query: $query
    limit: $limit
    offset: $offset
    siteId: $siteId
    sort: $sort
  ) {
    numberOfHits
    articles {
      id
      title
      lead
      byline
      publishedDate
      path(absolute: $absolute)
      teaserImage { baseUrl }
      topMedia {
        ... on Image { baseUrl }
      }
      parent { title }
    }
  }
}
"""


class ZawyaNewsSpider(BaseNewsSpider):
    """GraphQL-based spider for Zawya.com.

    Queries the Atex/Pace GraphQL API for each TAX_KEYWORD, sorted newest-first.
    Paginates per keyword until articles go beyond the configured cutoff or
    MAX_PAGES_PER_KW pages have been fetched.  All URLs are deduplicated within
    the spider run.
    """

    name = "zawya_news"

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
        "id": "zawya",
        "name": "Zawya",
        "sourceType": "media",
        "sourceCountry": "AE",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/en/search",
            "country": "United Arab Emirates",
            "countries": ["United Arab Emirates", "Middle East"],
            "primaryCountry": "United Arab Emirates",
            "jurisdictions": ["AE"],
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen_urls: set = set()

    # ------------------------------------------------------------------ #
    # Request helpers                                                      #
    # ------------------------------------------------------------------ #

    def _make_gql_request(self, keyword: str, offset: int) -> scrapy.Request:
        payload = {
            "operationName": "searchPage",
            "query": _GQL_QUERY,
            "variables": {
                "query": keyword,
                "limit": PAGE_SIZE,
                "offset": offset,
                "siteId": SITE_ID,
                "sort": "DATE",
                "absolute": True,
            },
        }
        return scrapy.Request(
            url=GQL_URL,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/en/search?q={keyword}",
            },
            body=json.dumps(payload),
            callback=self.parse_search,
            meta={"keyword": keyword, "offset": offset},
            dont_filter=True,
        )

    # ------------------------------------------------------------------ #
    # Entry points                                                         #
    # ------------------------------------------------------------------ #

    def start_requests(self):
        for kw in TAX_KEYWORDS:
            yield self._make_gql_request(kw, offset=0)

    # ------------------------------------------------------------------ #
    # Parsing                                                              #
    # ------------------------------------------------------------------ #

    def parse_search(self, response):
        keyword = response.meta["keyword"]
        offset = response.meta["offset"]
        page_num = offset // PAGE_SIZE + 1
        self.logger.debug(
            f"[zawya] keyword={keyword!r} page={page_num} url={response.url}"
        )

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning(f"[zawya] Non-JSON response for keyword={keyword!r}")
            return

        errors = data.get("errors")
        if errors:
            self.logger.warning(f"[zawya] GraphQL errors for {keyword!r}: {errors}")
            return

        articles = (
            data.get("data", {}).get("search", {}).get("articles") or []
        )
        if not articles:
            return

        reached_cutoff = False

        for article in articles:
            pub_date = article.get("publishedDate") or ""

            # Stop iterating if this article is past the cutoff
            if pub_date and not self.is_within_timeframe(pub_date):
                reached_cutoff = True
                break

            url = article.get("path") or ""
            if not url or url in self._seen_urls:
                continue
            self._seen_urls.add(url)

            title = (article.get("title") or "").strip()
            if not title:
                continue

            # Description: prefer lead (may have HTML), clean it
            description = _strip_html(article.get("lead") or "")

            # Thumbnail: topMedia.baseUrl preferred, fall back to teaserImage
            top_media = article.get("topMedia") or {}
            teaser = article.get("teaserImage") or {}
            thumbnail = top_media.get("baseUrl") or teaser.get("baseUrl") or ""

            # Category: section title from parent, e.g. "GCC", "Companies News"
            parent = article.get("parent") or {}
            category = parent.get("title") or ""

            yield self.build_item(
                title=title,
                link=url,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )

        # Paginate if we haven't hit the cutoff and aren't at the page cap
        if not reached_cutoff and len(articles) == PAGE_SIZE and page_num < MAX_PAGES_PER_KW:
            yield self._make_gql_request(keyword, offset=offset + PAGE_SIZE)
