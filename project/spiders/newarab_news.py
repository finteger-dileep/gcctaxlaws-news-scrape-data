"""
The New Arab (newarab.com) news spider.

Source: https://www.newarab.com/rss

Mechanism:
  The New Arab's search page (/search?search_api_fulltext=<kw>) returns HTTP
  403 (Akamai WAF).  However, the site publishes a flat RSS feed at /rss that
  requires no authentication and is not rate-limited:
    https://www.newarab.com/rss

  The feed contains ~94 items covering approximately the last 3-4 days of
  content across all categories (MENA, Economy, Energy, World, etc.).  There
  is no per-keyword or per-category pagination — the feed is a single flat
  endpoint.  Keyword relevance filtering is handled downstream by the
  orchestrator's post-processing step.

  The feed is fetched once per run.  Items outside the configured age cutoff
  are silently skipped.

Thumbnail URL:
  The RSS does not include a dedicated thumbnail tag.  Each item's
  <content:encoded> block contains an HTML fragment whose last <img src="...">
  element is the article thumbnail.  The pattern is:
    <div><img src="https://www.newarab.com/sites/default/files/...jpg?..." /></div>

Fields extracted:
  - RSS <title>                → title        (CDATA-unwrapped)
  - RSS <link>                 → URL
  - RSS <pubDate>              → pubDate      (RFC 2822 → ISO 8601 via parse_date)
  - RSS <description>          → description  (CDATA-unwrapped, HTML-stripped)
  - RSS <category>             → category     (first CDATA value)
  - content:encoded last <img> → thumbnail
"""
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://www.newarab.com"
_RSS_URL = f"{BASE_URL}/rss"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_IMG_SRC_RE = re.compile(r'<img\b[^>]+\bsrc=["\']([^"\']+)["\']', re.DOTALL)


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


def _thumbnail_from_encoded(encoded_html: str) -> str:
    """Return the src of the last <img> in content:encoded, or empty string."""
    imgs = _IMG_SRC_RE.findall(encoded_html)
    return imgs[-1].strip() if imgs else ""


class NewArabNewsSpider(BaseNewsSpider):
    """Flat RSS spider for The New Arab.

    Fetches the single universal RSS feed once per run and yields all articles
    within the configured age cutoff.  Thumbnail URLs are extracted from the
    last <img> element inside content:encoded.  Keyword relevance is handled
    by the orchestrator in post-processing.
    """

    name = "newarab_news"

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
        "id": "newarab",
        "name": "The New Arab",
        "sourceType": "media",
        "sourceCountry": "GB",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/",
            "country": "Middle East",
            "countries": ["Middle East", "Arab World"],
            "primaryCountry": "Middle East",
            "jurisdictions": [],
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
        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        if not items_xml:
            self.logger.warning("[newarab] No <item> elements found in RSS feed")
            return

        self.logger.debug(f"[newarab] RSS feed returned {len(items_xml)} items")

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

            # First category value
            cat_m = re.search(r"<category[^>]*>(.*?)</category>", item_xml, re.DOTALL)
            category = _cdata(cat_m.group(1)) if cat_m else ""

            # Thumbnail: last <img src="..."> in content:encoded
            encoded_tag = _tag("encoded")       # <content:encoded>
            if not encoded_tag:
                # try explicit namespace match
                enc_m = re.search(
                    r"<content:encoded[^>]*>(.*?)</content:encoded>",
                    item_xml, re.DOTALL
                )
                encoded_tag = enc_m.group(1).strip() if enc_m else ""
            thumbnail = _thumbnail_from_encoded(_cdata(encoded_tag))

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )
