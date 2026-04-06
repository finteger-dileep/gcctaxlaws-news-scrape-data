"""
UAE Federal Tax Authority (tax.gov.ae) news spider.

Source: https://tax.gov.ae/en/rss/news.aspx

Mechanism:
  Static RSS 2.0 feed published by the UAE Federal Tax Authority.
  Single flat endpoint — no pagination or per-keyword iteration required.
  The feed is fetched once per run; keyword relevance is handled by the
  orchestrator post-processing step.

Feed structure:
  - Standard RSS 2.0, no namespaces
  - <title>         : plain text (no CDATA)
  - <description>   : plain text summary (no HTML encoding)
  - <link>          : full URL (https://tax.gov.ae/en/media.centre/news/...aspx)
  - <pubDate>       : RFC 2822 date-only, e.g. "Sun, 08 Mar 2026"
                      (no time component — handled by date_utils)
  - <thumbnailimage>: non-standard custom tag with full image URL
  - <guid>          : slug string (not a permalink)
  - No <category>, no <enclosure>
"""
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://tax.gov.ae"
_RSS_URL = f"{BASE_URL}/en/rss/news.aspx"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


class UaeFtaNewsSpider(BaseNewsSpider):
    """Flat RSS spider for UAE Federal Tax Authority news.

    Fetches the single RSS feed once per run and yields all articles within
    the configured age cutoff.  Thumbnail URLs are retrieved from the
    non-standard <thumbnailimage> element.
    """

    name = "uae_fta_news"

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
        "id": "uae_fta",
        "name": "UAE Federal Tax Authority",
        "sourceType": "government",
        "sourceCountry": "AE",
        "enabled": True,
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/en/media.centre/news.aspx",
            "country": "United Arab Emirates",
            "countries": ["United Arab Emirates"],
            "primaryCountry": "United Arab Emirates",
            "jurisdictions": ["AE"],
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen_urls: set[str] = set()

    # ------------------------------------------------------------------ #
    # Request building                                                     #
    # ------------------------------------------------------------------ #

    async def start(self):
        yield scrapy.Request(
            _RSS_URL,
            callback=self.parse_rss,
            headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"},
        )

    # ------------------------------------------------------------------ #
    # Parsing                                                              #
    # ------------------------------------------------------------------ #

    def parse_rss(self, response):
        if "<item>" not in response.text:
            self.logger.warning("[uae_fta] No <item> elements found in RSS feed")
            return

        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        self.logger.debug(f"[uae_fta] RSS feed returned {len(items_xml)} items")

        for item_xml in items_xml:
            def _tag(tag: str, _xml: str = item_xml) -> str:
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", _xml, re.DOTALL)
                return m.group(1).strip() if m else ""

            title = _strip_html(_cdata(_tag("title")))
            link = _tag("link").strip()
            pub_date_str = _tag("pubDate").strip()
            pub_date = parse_date(pub_date_str)

            if not title or not link:
                continue
            if not self.is_within_timeframe(pub_date):
                continue
            if link in self._seen_urls:
                continue
            self._seen_urls.add(link)

            description = _strip_html(_cdata(_tag("description")))

            # Thumbnail from the non-standard <thumbnailimage> tag
            thumb_m = re.search(r"<thumbnailimage[^>]*>(.*?)</thumbnailimage>", item_xml, re.DOTALL)
            thumbnail = thumb_m.group(1).strip() if thumb_m else ""

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category="",
            )
