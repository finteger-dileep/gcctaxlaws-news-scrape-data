"""
The Peninsula Qatar news spider.
Scrapes https://thepeninsulaqatar.com using the site's server-side search endpoint.

Strategy: For each keyword in TAX_KEYWORDS, send one GET request to
  /news/search?q=<keyword>&filter=all&search-by=title-body&duration=year

The search is server-rendered (jQuery/PHP stack) — no JS execution needed.
Results are de-duplicated within the spider before being yielded.
"""
from urllib.parse import urlencode

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date
from project.utils.keywords import TAX_KEYWORDS

BASE_URL = "https://thepeninsulaqatar.com"
SEARCH_URL = f"{BASE_URL}/news/search"
LISTING_URL = f"{BASE_URL}/news"


class PeninsulaQatarSpider(BaseNewsSpider):
    """Search-based spider for The Peninsula Qatar.

    Fires one search request per TAX_KEYWORD using `duration=year` so only
    articles from the last 12 months are returned.  Results across all keyword
    queries are deduplicated by URL before being yielded.
    """

    name = "peninsula_qatar"

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
        "id": "peninsula_qatar",
        "name": "The Peninsula Qatar",
        "sourceType": "media",
        "sourceCountry": "QA",
        "config": {
            "baseUrl": BASE_URL,
            "source": LISTING_URL,
            "country": "Qatar",
            "countries": ["Qatar"],
            "primaryCountry": "Qatar",
            "jurisdictions": ["QA"],
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen_urls: set = set()

    async def start(self):
        for kw in TAX_KEYWORDS:
            params = {
                "q": kw,
                "filter": "all",
                "search-by": "title-body",
                "duration": "year",
            }
            url = f"{SEARCH_URL}?{urlencode(params)}"
            yield scrapy.Request(url, callback=self.parse_search, meta={"keyword": kw})

    def parse_search(self, response):
        keyword = response.meta["keyword"]
        self.logger.debug(f"Parsing search results for keyword: {keyword!r}")

        for card in response.css("div.col-sm-6.item"):
            title_el = card.css("a.title")
            title = title_el.css("::text").get("").strip()
            rel_link = title_el.attrib.get("href", "")

            if not title or not rel_link:
                continue

            full_url = f"{BASE_URL}{rel_link}"

            # Skip if already yielded from a previous keyword query
            if full_url in self._seen_urls:
                continue
            self._seen_urls.add(full_url)

            # Date: "03 Apr 2026 - 03:12 pm"  ->  "03 Apr 2026"
            date_raw = card.css("span::text").get("").strip()
            date_part = date_raw.split(" - ")[0].strip()
            pub_date = parse_date(date_part)

            if not self.is_within_timeframe(pub_date):
                continue

            description = card.css("p.search::text").get("").strip()

            img_src = card.css("a.photo img::attr(src)").get("") or ""
            if img_src.startswith("//"):
                thumbnail = f"https:{img_src}"
            elif img_src.startswith("/"):
                thumbnail = f"{BASE_URL}{img_src}"
            else:
                thumbnail = img_src

            yield self.build_item(
                title=title,
                link=full_url,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category="",
            )
