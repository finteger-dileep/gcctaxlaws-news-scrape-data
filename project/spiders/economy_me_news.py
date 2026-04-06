"""
Economy Middle East (economymiddleeast.com) news spider.

Source: https://economymiddleeast.com/latest-news/

Mechanism:
  1. Paginate through /latest-news/feed/?paged=N (RSS, 10 items per page).
  2. For each article published within the configured cutoff, follow the
     article URL to extract the og:image thumbnail from the <head>.
  3. Stop pagination as soon as a page's oldest item is older than the cutoff.

Post type on the site is a custom "news" type not exposed via WP REST API,
so the RSS feed is the cleanest ingestion path.
"""
import re
from html import unescape
from urllib.parse import urlparse, urlunparse

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://economymiddleeast.com"
FEED_URL = f"{BASE_URL}/latest-news/feed/"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html)).strip()


def _clean_link(url: str) -> str:
    """Remove UTM and tracking params from URL."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


class EconomyMENewsSpider(BaseNewsSpider):
    """RSS-based spider for Economy Middle East /latest-news/.

    Paginates the RSS feed until articles are older than the cutoff,
    then fetches each in-window article page to harvest og:image thumbnails.
    """

    name = "economy_me_news"

    custom_settings = {
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "DOWNLOAD_DELAY": 0.4,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 3,
    }

    _DEFAULT_SOURCE_CONFIG = {
        "id": "economy_me",
        "name": "Economy Middle East",
        "sourceType": "media",
        "sourceCountry": "AE",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/latest-news/",
            "country": "United Arab Emirates",
            "countries": ["United Arab Emirates", "Middle East"],
            "primaryCountry": "United Arab Emirates",
            "jurisdictions": ["AE"],
        },
    }

    async def start(self):
        yield scrapy.Request(FEED_URL, callback=self.parse_feed, meta={"page": 1})

    def parse_feed(self, response):
        page = response.meta["page"]
        self.logger.info(f"Parsing RSS page {page}: {response.url}")

        # Parse XML items with regex (RSS is simple and consistent)
        items_raw = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        if not items_raw:
            return

        oldest_on_page = None
        has_in_window = False

        for item_xml in items_raw:
            def _tag(tag):
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", item_xml, re.DOTALL)
                return m.group(1).strip() if m else ""

            title = _strip_html(_cdata(_tag("title")))
            link_raw = _cdata(_tag("link")).strip()
            link = _clean_link(link_raw)
            pub_date_str = _tag("pubDate").strip()
            pub_date = parse_date(pub_date_str)

            if not title or not link:
                continue

            if oldest_on_page is None or (pub_date and pub_date < oldest_on_page):
                oldest_on_page = pub_date

            if not self.is_within_timeframe(pub_date):
                continue

            has_in_window = True

            # Gather description from RSS <description> CDATA
            desc_raw = _cdata(_tag("description"))
            # Strip the "The post ... appeared first on..." boilerplate
            desc_clean = _strip_html(re.sub(r"<p>The post.*?</p>", "", desc_raw, flags=re.DOTALL))

            # Collect categories
            cats = re.findall(r"<category><!\[CDATA\[(.*?)\]\]></category>", item_xml)
            category = ", ".join(cats[:3]) if cats else ""

            # Follow article page only for thumbnail (og:image in <head>)
            yield scrapy.Request(
                link,
                callback=self.parse_article,
                meta={
                    "title": title,
                    "link": link,
                    "pub_date": pub_date,
                    "description": desc_clean,
                    "category": category,
                },
            )

        # Paginate unless all articles on this page are already too old
        if has_in_window or oldest_on_page is None or self.is_within_timeframe(oldest_on_page):
            next_page = page + 1
            next_url = f"{FEED_URL}?paged={next_page}"
            yield scrapy.Request(
                next_url,
                callback=self.parse_feed,
                meta={"page": next_page},
            )

    def parse_article(self, response):
        og_image = response.css('meta[property="og:image"]::attr(content)').get("") or ""

        yield self.build_item(
            title=response.meta["title"],
            link=response.meta["link"],
            description=response.meta["description"],
            thumbnail=og_image,
            pub_date=response.meta["pub_date"],
            category=response.meta["category"],
        )
