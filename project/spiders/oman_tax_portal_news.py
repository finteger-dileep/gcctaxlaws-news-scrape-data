"""
Oman Tax Authority (taxoman.gov.om) news spider via fetchrss.com aggregator.

Source: https://fetchrss.com/feed/1w7rzH8iGFNK1w7wnYAmd5Z5.rss
  (Aggregates news from https://taxoman.gov.om/portal/web/taxportal/news)

Mechanism:
  fetchrss.com is a third-party RSS aggregator that mirrors Oman's Tax
  Authority portal news page as a standard RSS 2.0 feed.
  Single flat endpoint — no pagination required.
  Keyword relevance is handled by the orchestrator post-processing step.

Feed structure:
  - RSS 2.0 with media, atom, dc namespaces
  - <title>         : plain text (no CDATA); article headline
  - <description>   : CDATA wrapping HTML:
                          <div class="latest-news-item__published-at">
                              DD-MM-YYYY
                          </div><br/><br/>
                          <span style="...">FetchRSS watermark</span>
                      Contains only a date div and a fetchrss.com watermark
                      — no article text.  Title is used as the description
                      fallback so that at least some text is stored.
  - <link>          : full URL to taxoman.gov.om article
  - <pubDate>       : RFC 2822 with +0000, e.g. "Tue, 31 Mar 2026 00:00:00 +0000"
  - <guid isPermaLink="false">: same as link
  - No thumbnail, no <media:content>, no <enclosure>, no <category>
"""
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://taxoman.gov.om"
_RSS_URL = "https://fetchrss.com/feed/1w7rzH8iGFNK1w7wnYAmd5Z5.rss"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


class OmanTaxNewsSpider(BaseNewsSpider):
    """Flat RSS spider for Oman Tax Authority news (via fetchrss.com).

    Fetches the fetchrss-aggregated feed once per run and yields all articles
    within the configured age cutoff.  Because the feed description contains
    only a date and a watermark, the article title is also used as the
    description text.  No thumbnails are available.
    """

    name = "oman_tax_portal_news"

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
        "id": "oman_tax_portal",
        "name": "Oman Tax Authority - News Portal",
        "sourceType": "government",
        "sourceCountry": "OM",
        "enabled": True,
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/portal/web/taxportal/news",
            "country": "Oman",
            "countries": ["Oman"],
            "primaryCountry": "Oman",
            "jurisdictions": ["OM"],
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
            self.logger.warning("[oman_tax_portal] No <item> elements found in RSS feed")
            return

        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        self.logger.debug(f"[oman_tax_portal] RSS feed returned {len(items_xml)} items")

        for item_xml in items_xml:
            def _tag(tag: str, _xml: str = item_xml) -> str:
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", _xml, re.DOTALL)
                return m.group(1).strip() if m else ""

            title = _strip_html(_cdata(_tag("title")))
            link = _tag("link").strip()
            # guid is same as link; fallback to guid if link tag is empty
            if not link:
                link = _cdata(_tag("guid")).strip()
            pub_date_str = _tag("pubDate").strip()
            pub_date = parse_date(pub_date_str)

            if not title or not link:
                continue
            if not self.is_within_timeframe(pub_date):
                continue
            if link in self._seen_urls:
                continue
            self._seen_urls.add(link)

            # Feed description only has a date div + watermark — use title as description
            description = title

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail="",
                pub_date=pub_date,
                category="",
            )
