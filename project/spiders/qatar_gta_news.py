"""
Qatar General Tax Authority (gta.gov.qa) news spider via rss.app aggregator.

Source: https://rss.app/feeds/LiZkauU6ol6CWUp4.xml
  (Aggregates GTA notifications from https://gta.gov.qa/en/media-center/notifications)

Mechanism:
  rss.app is a third-party RSS aggregator that mirrors Qatar's General Tax
  Authority notification page as a standard RSS 2.0 feed.
  Single flat endpoint — no pagination required.
  Keyword relevance is handled by the orchestrator post-processing step.

Feed structure:
  - RSS 2.0 with dc, content, atom, media namespaces
  - <title>           : CDATA, plain text article title
  - <description>     : CDATA wrapping HTML:
                          <div>
                            <img src="...thumbnail..." style="width:100%;"/>
                            <div>article excerpt text...</div>
                          </div>
                        Only the inner text div is used; the img is discarded.
  - <link>            : full URL to gta.gov.qa notification
  - <pubDate>         : RFC 2822 with GMT, e.g. "Wed, 31 Dec 2025 12:00:00 GMT"
  - <media:content medium="image" url="..."/> : thumbnail (self-closing)
  - <guid isPermaLink="false">: MD5-like hash
  - <dc:creator>      : CDATA "@tax_qatar" (ignored)
  - No <category>, no <enclosure>

Note: All items in the current feed share the same pubDate (articles
  were back-dated when the aggregator was first set up).  New articles
  will have accurate dates.
"""
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://gta.gov.qa"
_RSS_URL = "https://rss.app/feeds/LiZkauU6ol6CWUp4.xml"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# media:content or media:content — match namespace-prefixed self-closing tag
_MEDIA_CONTENT_RE = re.compile(
    r"<media:content\b[^>]+\burl=['\"]([^'\"]+)['\"][^>]*/?>", re.DOTALL
)


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


def _extract_description(cdata_html: str) -> str:
    """Extract plain-text excerpt from the CDATA description block.

    The block looks like:
        <div><img src="..." style="width: 100%;"/><div>article text…</div></div>

    We want the inner <div>text</div> only.
    """
    # Find the last (or deepest) nested <div>…</div>
    inner_m = re.search(r"<div>(.*?)</div>\s*</div>", cdata_html, re.DOTALL)
    if inner_m:
        return _strip_html(inner_m.group(1))
    # Fallback: strip all HTML
    return _strip_html(cdata_html)


class QatarGtaNewsSpider(BaseNewsSpider):
    """Flat RSS spider for Qatar General Tax Authority notifications.

    Fetches the rss.app-aggregated feed once per run and yields all articles
    within the configured age cutoff.  Thumbnails are extracted from the
    <media:content> tag.  Description text is extracted from the inner div
    inside the CDATA description block.
    """

    name = "qatar_gta_news"

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
        "id": "qatar_gta",
        "name": "Qatar General Tax Authority",
        "sourceType": "government",
        "sourceCountry": "QA",
        "enabled": True,
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/en/media-center/notifications",
            "country": "Qatar",
            "countries": ["Qatar"],
            "primaryCountry": "Qatar",
            "jurisdictions": ["QA"],
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
            self.logger.warning("[qatar_gta] No <item> elements found in RSS feed")
            return

        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        self.logger.debug(f"[qatar_gta] RSS feed returned {len(items_xml)} items")

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

            # Extract plain-text description from the CDATA HTML block
            raw_desc = _tag("description")
            cdata_html = _cdata(raw_desc)
            description = _extract_description(cdata_html)

            # Thumbnail from <media:content url="..."/>
            media_m = _MEDIA_CONTENT_RE.search(item_xml)
            thumbnail = media_m.group(1).strip() if media_m else ""

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category="",
            )
