"""
Kuwait e-Government Portal (e.gov.kw) news spider.

Source:
  https://e.gov.kw/sites/kgoenglish/_layouts/15/listfeed.aspx?
    List=6ee7b38b-a51e-4f9a-b933-534beb554ed4&
    View=04c66f2b-29c3-460f-b3f3-be3b54bbdcb1

Mechanism:
  Microsoft SharePoint Foundation RSS 2.0 feed from Kuwait's e-government
  portal (English announcements list).  Single flat endpoint — no pagination
  required.  Keyword relevance is applied by the orchestrator post-processing
  step (many items are generic service/portal announcements).

Feed structure:
  - SharePoint RSS 2.0 (no extra namespaces)
  - <title>         : plain text; may have leading/trailing whitespace
  - <description>   : CDATA wrapping HTML — two divs:
                        <div><b>Body:</b> …text…</div>
                        <div><b>AnnoucementDate:</b> MM/DD/YYYY</div>
                      Only the Body div is used as the article description;
                      the date div is discarded.  HTML entities (&#58;) are
                      decoded via html.unescape.
  - <link>          : SharePoint DispForm URL
                        https://e.gov.kw/sites/.../DispForm.aspx?ID=NNN
  - <pubDate>       : RFC 2822 with GMT, e.g. "Wed, 24 Dec 2025 17:25:41 GMT"
  - No thumbnail, no <enclosure>, no <category>
  - <author>        : "System Account" (ignored)
  - <guid isPermaLink="true">: same as link
"""
import re
from html import unescape

import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://e.gov.kw"
_RSS_URL = (
    f"{BASE_URL}/sites/kgoenglish/_layouts/15/listfeed.aspx"
    "?List=6ee7b38b-a51e-4f9a-b933-534beb554ed4"
    "&View=04c66f2b-29c3-460f-b3f3-be3b54bbdcb1"
)

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Match the Body div: <div><b>Body:</b> …content…</div>
# Uses lookahead so the next div (AnnoucementDate) is not consumed.
_BODY_DIV_RE = re.compile(
    r"<div>\s*<b>Body&#58;</b>\s*(.*?)\s*</div>",
    re.DOTALL | re.IGNORECASE,
)


def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


def _extract_body(cdata_html: str) -> str:
    """Extract the Body text from SharePoint CDATA description."""
    # Body div may use literal colon or HTML entity &#58;
    body_re = re.compile(
        r"<div>\s*<b>Body(?:&#58;|:)</b>\s*(.*?)\s*</div>",
        re.DOTALL | re.IGNORECASE,
    )
    m = body_re.search(cdata_html)
    if m:
        return _strip_html(m.group(1))
    # Fallback: strip all HTML from the full CDATA block
    return _strip_html(cdata_html)


class KuwaitNewsSpider(BaseNewsSpider):
    """Flat RSS spider for Kuwait e-Government Portal announcements.

    Fetches the SharePoint RSS feed once per run and yields all articles
    within the configured age cutoff.  Description text is extracted from
    the Body div inside the CDATA-wrapped HTML description field.
    Keyword filtering by the orchestrator removes non-tax items.
    """

    name = "kuwait_news"

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
        "id": "kuwait_egov",
        "name": "Kuwait e-Government Portal",
        "sourceType": "government",
        "sourceCountry": "KW",
        "enabled": True,
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/sites/kgoenglish/Pages/KGOAnnouncements.aspx",
            "country": "Kuwait",
            "countries": ["Kuwait"],
            "primaryCountry": "Kuwait",
            "jurisdictions": ["KW"],
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
            self.logger.warning("[kuwait] No <item> elements found in RSS feed")
            return

        items_xml = re.findall(r"<item>(.*?)</item>", response.text, re.DOTALL)
        self.logger.debug(f"[kuwait] RSS feed returned {len(items_xml)} items")

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

            # Description is CDATA wrapping HTML with Body + AnnoucementDate divs
            raw_desc = _tag("description")
            cdata_html = _cdata(raw_desc)
            description = _extract_body(cdata_html)

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail="",
                pub_date=pub_date,
                category="",
            )
