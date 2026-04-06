"""
UAE WAM (Emirates News Agency / wam.ae) news spider.

Source: https://www.wam.ae/en/rss/feed/g4ylploo0n?slug=rss-economy&vsCode=avs-002-1jc72emk1y2i&type=rss

Mechanism:
  Static RSS 2.0 feed published by WAM, the UAE's official news agency.
  Single flat endpoint — no pagination or per-keyword iteration required.
  The feed is fetched once per run; keyword relevance is handled by the
  orchestrator post-processing step.

Feed structure:
  - Standard RSS 2.0 with xml:base attributes on channel and item elements
  - <title>         : plain text
  - <description>   : HTML-encoded content (contains <p> tags, entities)
                      — unescaped and HTML-stripped before storing
  - <link>          : full URL (https://www.wam.ae/en/article/...)
  - <pubDate>       : RFC 2822 with +0400 offset, e.g. "Mon, 06 Apr 2026 12:12:11 +0400"
  - <enclosure>     : self-closing tag with url/type/length attributes — thumbnail
  - <category>      : plain text, can have multiple per item (take first)
  - <guid isPermaLink="false">: slug path
"""
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://www.wam.ae"
_RSS_URL = (
    f"{BASE_URL}/en/rss/feed/g4ylploo0n"
    "?slug=rss-economy&vsCode=avs-002-1jc72emk1y2i&type=rss"
)

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ENCLOSURE_URL_RE = re.compile(r"<enclosure\b[^>]+\burl=['\"]([^'\"]+)['\"]", re.DOTALL)


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


class WamUaeNewsSpider(BaseNewsSpider):
    """Flat RSS spider for UAE WAM (Emirates News Agency).

    Fetches the economy RSS feed once per run and yields all articles within
    the configured age cutoff.  Thumbnails are extracted from the <enclosure>
    tag.  Multiple <category> values are collapsed to the first one.
    Description HTML is unescaped and stripped before storage.
    """

    name = "wam_uae_news"

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
        "id": "wam_uae",
        "name": "WAM - Emirates News Agency",
        "sourceType": "media",
        "sourceCountry": "AE",
        "enabled": True,
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/en/economy",
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
            self.logger.warning("[wam_uae] No <item> elements found in RSS feed")
            return

        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        self.logger.debug(f"[wam_uae] RSS feed returned {len(items_xml)} items")

        for item_xml in items_xml:
            def _tag(tag: str, _xml: str = item_xml) -> str:
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", _xml, re.DOTALL)
                return m.group(1).strip() if m else ""

            title = _strip_html(_cdata(_tag("title")))
            link = _cdata(_tag("link")).strip()
            pub_date_str = _tag("pubDate").strip()
            pub_date = parse_date(pub_date_str)

            if not title or not link:
                continue
            if not self.is_within_timeframe(pub_date):
                continue
            if link in self._seen_urls:
                continue
            self._seen_urls.add(link)

            # Description contains HTML-encoded markup — unescape and strip
            description = _strip_html(_cdata(_tag("description")))

            # First <category> value (multiple may exist per item)
            cat_m = re.search(r"<category[^>]*>(.*?)</category>", item_xml, re.DOTALL)
            category = _cdata(cat_m.group(1)) if cat_m else ""

            # Thumbnail from <enclosure url="..." type="image/jpeg" .../>
            enc_m = _ENCLOSURE_URL_RE.search(item_xml)
            thumbnail = enc_m.group(1).strip() if enc_m else ""

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )
