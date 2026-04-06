"""
Bahrain News Agency (bna.bh) economy news spider.

Source: https://www.bna.bh/en/GenerateRssFeed.aspx?categoryId=176

Mechanism:
  Standard RSS 2.0 feed published by the Bahrain News Agency (BNA) for the
  Economy category (categoryId=176).  Single flat endpoint — no pagination
  required.  Keyword relevance is handled by the orchestrator post-processing
  step.

  WARNING: The BNA RSS endpoint is protected by AWS WAF with a JavaScript
  challenge (CAPTCHA).  A plain HTTP GET will return a WAF challenge HTML page
  rather than XML.  The spider detects this condition, logs a warning, and
  yields no items.  To bypass the WAF in a production deployment, use one of:
    - scrapy-playwright (with headless Chromium executing the JS challenge)
    - a third-party WAF bypass service / rotating residential proxies
    - a pre-authenticated AWS WAF cookie injected via COOKIES_ENABLED + cookies

Feed structure (when accessible):
  - Standard RSS 2.0
  - <title>         : plain text or CDATA
  - <description>   : plain text or HTML-encoded summary
  - <link>          : full URL (https://www.bna.bh/en/article/...)
  - <pubDate>       : RFC 2822
  - May include <enclosure> or <media:thumbnail> for thumbnails
  - No pagination — flat feed
"""
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://www.bna.bh"
_RSS_URL = f"{BASE_URL}/en/GenerateRssFeed.aspx?categoryId=176"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ENCLOSURE_URL_RE = re.compile(r"<enclosure\b[^>]+\burl=['\"]([^'\"]+)['\"]", re.DOTALL)
_MEDIA_THUMB_RE = re.compile(
    r"<media:thumbnail\b[^>]+\burl=['\"]([^'\"]+)['\"]", re.DOTALL
)


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


class BahrainBnaNewsSpider(BaseNewsSpider):
    """Flat RSS spider for Bahrain News Agency (economy category).

    Fetches the BNA RSS feed once per run.  If the response is a WAF
    challenge (HTML instead of XML), a warning is logged and no items are
    yielded.  Once the WAF is bypassed (e.g. via scrapy-playwright or a
    valid session cookie), the spider will extract articles in the standard
    RSS pattern with thumbnail fallback between <enclosure> and
    <media:thumbnail>.
    """

    name = "bahrain_bna_news"

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
        "id": "bahrain_bna",
        "name": "Bahrain News Agency",
        "sourceType": "media",
        "sourceCountry": "BH",
        "enabled": True,
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/en/economy",
            "country": "Bahrain",
            "countries": ["Bahrain"],
            "primaryCountry": "Bahrain",
            "jurisdictions": ["BH"],
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
            headers={
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{BASE_URL}/en/",
            },
        )

    # ------------------------------------------------------------------ #
    # Parsing                                                              #
    # ------------------------------------------------------------------ #

    def parse_rss(self, response):
        # Detect AWS WAF challenge — response will be HTML, not XML
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore")
        is_html_response = (
            response.text.strip().startswith("<!DOCTYPE")
            or response.text.strip().startswith("<html")
            or "text/html" in content_type
        )
        if is_html_response:
            self.logger.warning(
                "[bahrain_bna] AWS WAF challenge detected — RSS feed not accessible "
                "via plain HTTP. Consider using scrapy-playwright or injecting a "
                "valid WAF session cookie. No items yielded for this run."
            )
            return

        if "<item>" not in response.text:
            self.logger.warning("[bahrain_bna] No <item> elements found in RSS feed")
            return

        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        self.logger.debug(f"[bahrain_bna] RSS feed returned {len(items_xml)} items")

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

            description = _strip_html(_cdata(_tag("description")))

            # Category (first occurrence)
            cat_m = re.search(r"<category[^>]*>(.*?)</category>", item_xml, re.DOTALL)
            category = _cdata(cat_m.group(1)) if cat_m else ""

            # Thumbnail: try <enclosure> first, then <media:thumbnail>
            enc_m = _ENCLOSURE_URL_RE.search(item_xml)
            if enc_m:
                thumbnail = enc_m.group(1).strip()
            else:
                mt_m = _MEDIA_THUMB_RE.search(item_xml)
                thumbnail = mt_m.group(1).strip() if mt_m else ""

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )
