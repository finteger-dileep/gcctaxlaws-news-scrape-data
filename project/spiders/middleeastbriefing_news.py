"""
Middle East Briefing (middleeastbriefing.com) news spider.

Source: https://www.middleeastbriefing.com/news/?s=<keyword>

Mechanism:
  Middle East Briefing runs WordPress.  The WP REST API requires authentication
  (returns HTML instead of JSON), but the standard WordPress search RSS feed
  works without restrictions:
    https://www.middleeastbriefing.com/news/search/<keyword>/feed/rss2/

  Each RSS response returns 10 items.  Pagination is done via the ``?paged=N``
  query parameter — this is WordPress's standard feed pagination mechanism.

  Pagination per keyword continues while:
    - the page contained at least one article within the cutoff window, AND
    - fewer than _MAX_PAGES pages have been fetched for this keyword.

  The thumbnail URL is extracted from the ``<enclosure>`` tag in each RSS item.

  A 0.3-second per-domain download delay keeps the site polite.
  No Cloudflare or other bot protection is present — standard Scrapy HTTP works.

Fields extracted:
  - RSS <title>      → title
  - RSS <link>       → URL
  - RSS <pubDate>    → pubDate (RFC 2822 → ISO 8601)
  - RSS <description>→ description (HTML + WP boilerplate stripped)
  - RSS <category>   → category (up to 3 non-generic tags joined)
  - RSS <enclosure>  → thumbnail (url attribute of self-closing <enclosure/>)
"""
import re
from html import unescape
from urllib.parse import quote_plus, urlparse, urlunparse

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date
from project.utils.keywords import TAX_KEYWORDS

BASE_URL = "https://www.middleeastbriefing.com"
_RSS_BASE = f"{BASE_URL}/news/search"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOILERPLATE_RE = re.compile(r"<p>The post .+?</p>", re.DOTALL)

_MAX_PAGES = 10   # 10 items × 10 pages = up to 100 per keyword
_SKIP_CATS = {"featured", ""}


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


def _clean_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


class MiddleEastBriefingNewsSpider(BaseNewsSpider):
    """WordPress search RSS spider for Middle East Briefing.

    Paginates the per-keyword search RSS feed (``?paged=N``) until articles
    fall outside the configured cutoff window or until _MAX_PAGES pages have
    been fetched.  Thumbnails come from the ``<enclosure url="...">`` tag
    present in every RSS item — no extra HTTP requests are needed.
    """

    name = "middleeastbriefing_news"

    custom_settings = {
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "DOWNLOAD_DELAY": 0.3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 3,
    }

    _DEFAULT_SOURCE_CONFIG = {
        "id": "middleeastbriefing",
        "name": "Middle East Briefing",
        "sourceType": "media",
        "sourceCountry": "AE",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/news/",
            "country": "United Arab Emirates",
            "countries": ["United Arab Emirates", "Middle East"],
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

    def _rss_url(self, keyword: str, page: int) -> str:
        slug = quote_plus(keyword)
        url = f"{_RSS_BASE}/{slug}/feed/rss2/"
        if page > 1:
            url += f"?paged={page}"
        return url

    def _make_request(self, keyword: str, page: int) -> scrapy.Request:
        return scrapy.Request(
            self._rss_url(keyword, page),
            callback=self.parse_rss,
            meta={"keyword": keyword, "page": page},
        )

    async def start(self):
        for kw in TAX_KEYWORDS:
            yield self._make_request(kw, page=1)

    # ------------------------------------------------------------------ #
    # Parsing                                                              #
    # ------------------------------------------------------------------ #

    def parse_rss(self, response):
        keyword = response.meta["keyword"]
        page = response.meta["page"]

        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        if not items_xml:
            return

        self.logger.debug(
            f"[middleeastbriefing] keyword={keyword!r} page={page}: {len(items_xml)} items"
        )

        in_window_count = 0
        for item_xml in items_xml:
            def _tag(tag: str, _xml: str = item_xml) -> str:
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", _xml, re.DOTALL)
                return m.group(1).strip() if m else ""

            title = _strip_html(_cdata(_tag("title")))
            link = _clean_url(_cdata(_tag("link")).strip())
            pub_date_str = _tag("pubDate").strip()
            pub_date = parse_date(pub_date_str)

            if not title or not link:
                continue
            if not self.is_within_timeframe(pub_date):
                continue
            in_window_count += 1

            if link in self._seen_urls:
                continue
            self._seen_urls.add(link)

            # Description: strip HTML tags and WP "appeared first on" boilerplate
            desc_raw = _cdata(_tag("description"))
            description = _strip_html(_BOILERPLATE_RE.sub("", desc_raw))

            # Categories: exclude generic labels
            cats_raw = re.findall(r"<category[^>]*>(.*?)</category>", item_xml, re.DOTALL)
            cat_names = [
                _cdata(c)
                for c in cats_raw
                if _cdata(c).lower() not in _SKIP_CATS
            ]
            category = ", ".join(cat_names[:3])

            # Thumbnail from <enclosure url="..." type="image/..."/>
            enc_m = re.search(r'<enclosure\s+url="([^"]+)"', item_xml)
            thumbnail = enc_m.group(1) if enc_m else ""

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category=category,
            )

        # Paginate if any in-window items were found and max pages not reached
        if in_window_count > 0 and page < _MAX_PAGES and len(items_xml) == 10:
            yield self._make_request(keyword, page + 1)
