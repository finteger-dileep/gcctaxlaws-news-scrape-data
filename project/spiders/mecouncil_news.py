"""
Middle East Council on Global Affairs (mecouncil.org) news spider.

Sources:
  - https://mecouncil.org/in-the-news/    (external coverage of ME Council)
  - https://mecouncil.org/press-releases/ (ME Council press releases)

Mechanism: WordPress REST API using custom post types `in_the_news` and
`press_release`.  The `after` query parameter limits results to items
published after the configured cutoff date, eliminating the need for
deep pagination in normal runs.  Thumbnails are fetched via `_embed`.
"""
import re
from html import unescape
from urllib.parse import urlencode

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://mecouncil.org"
API_BASE = f"{BASE_URL}/wp-json/wp/v2"

# WordPress custom post types to scrape
_POST_TYPES = ["in_the_news", "press_release"]

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return unescape(_HTML_TAG_RE.sub("", html)).strip()


class MecouncilNewsSpider(BaseNewsSpider):
    """WordPress REST API spider for ME Council's in-the-news and press-release posts.

    Uses `after` date param to restrict results to articles published after the
    configured cutoff, so only recent content is fetched without full pagination.
    """

    name = "mecouncil_news"

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
        "id": "mecouncil",
        "name": "Middle East Council on Global Affairs",
        "sourceType": "media",
        "sourceCountry": "QA",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/in-the-news/",
            "country": "Qatar",
            "countries": ["Qatar", "Middle East"],
            "primaryCountry": "Qatar",
            "jurisdictions": ["QA"],
        },
    }

    def start_requests(self):
        # ISO 8601 date that the WP API `after` param accepts: YYYY-MM-DDTHH:MM:SS
        after = self.cutoff.strftime("%Y-%m-%dT%H:%M:%S")

        for post_type in _POST_TYPES:
            params = {
                "per_page": 100,
                "orderby": "date",
                "order": "desc",
                "after": after,
                "_embed": "wp:featuredmedia",
            }
            url = f"{API_BASE}/{post_type}?{urlencode(params)}"
            yield scrapy.Request(
                url,
                callback=self.parse_api,
                headers={"Accept": "application/json"},
                meta={"post_type": post_type, "page": 1, "after": after},
            )

    def parse_api(self, response):
        post_type = response.meta["post_type"]
        page = response.meta["page"]
        after = response.meta["after"]

        try:
            items = response.json()
        except Exception:
            self.logger.error(f"Failed to parse JSON for {post_type} page {page}")
            return

        if not isinstance(items, list) or not items:
            return

        self.logger.info(f"{post_type} page {page}: {len(items)} items")

        for item in items:
            title = _strip_html(item.get("title", {}).get("rendered", ""))
            if not title:
                continue

            link = item.get("link", "")
            date_raw = item.get("date", "")
            pub_date = parse_date(date_raw)

            if not self.is_within_timeframe(pub_date):
                continue

            excerpt_html = item.get("excerpt", {}).get("rendered", "")
            description = _strip_html(excerpt_html)

            # Thumbnail from embedded featured media
            thumbnail = ""
            embedded = item.get("_embedded", {})
            media_list = embedded.get("wp:featuredmedia", [])
            if media_list and isinstance(media_list, list):
                thumbnail = media_list[0].get("source_url", "") or ""

            category = post_type.replace("_", " ").title()

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )

        # Paginate if a full page was returned (may be more)
        total_pages = int(response.headers.get("X-WP-TotalPages", 1))
        if page < total_pages:
            next_page = page + 1
            params = {
                "per_page": 100,
                "orderby": "date",
                "order": "desc",
                "after": after,
                "_embed": "wp:featuredmedia",
                "page": next_page,
            }
            url = f"{API_BASE}/{post_type}?{urlencode(params)}"
            yield scrapy.Request(
                url,
                callback=self.parse_api,
                headers={"Accept": "application/json"},
                meta={"post_type": post_type, "page": next_page, "after": after},
            )
