"""
Zakat, Tax and Customs Authority (ZATCA) Saudi Arabia news spider.
Scrapes https://zatca.gov.sa/en/MediaCenter/News/Pages/default.aspx

The news listing is populated via a single AJAX call to a PortalHandler endpoint
that returns all items as a flat JSON array (688+ items, both Arabic and English).
No pagination is required — all data is returned in one response.

English-only articles are kept (Arabic items are skipped via ASCII title check).
Date is filtered against the configured maxAgeHours cutoff.
"""
import re
import scrapy

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://zatca.gov.sa"
LISTING_URL = "https://zatca.gov.sa/en/MediaCenter/News/Pages/default.aspx"
API_URL = (
    "https://zatca.gov.sa/en/_LAYOUTS/15/GAZTInternet/PortalHandler.ashx"
    "?op=LoadItems&listUrl=/en/MediaCenter/News/pages&viewName=Home"
)

_IMG_SRC_RE = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)


class ZatcaNewsSpider(BaseNewsSpider):
    name = "zatca_news"

    custom_settings = {
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    _DEFAULT_SOURCE_CONFIG = {
        "id": "zatca_saudi",
        "name": "Zakat, Tax and Customs Authority - Saudi Arabia",
        "sourceType": "official",
        "sourceCountry": "SA",
        "config": {
            "baseUrl": BASE_URL,
            "source": LISTING_URL,
            "apiUrl": API_URL,
            "country": "Saudi Arabia",
            "countries": ["Saudi Arabia"],
            "primaryCountry": "Saudi Arabia",
            "jurisdictions": ["SA"],
        },
    }
    _DEFAULT_GLOBAL_CONFIG = {"maxAgeHours": 168}

    def __init__(self, source_config=None, global_config=None, *args, **kwargs):
        sc = source_config if source_config is not None else self._DEFAULT_SOURCE_CONFIG
        gc = global_config if global_config is not None else self._DEFAULT_GLOBAL_CONFIG
        super().__init__(source_config=sc, global_config=gc, *args, **kwargs)

        meta = self.source_config.get("config", {})
        self._base_url = meta.get("baseUrl", BASE_URL)
        self._api_url = meta.get("apiUrl", API_URL)

    # ------------------------------------------------------------------ #
    # Single AJAX request — returns all items in one response              #
    # ------------------------------------------------------------------ #

    def start_requests(self):
        yield scrapy.Request(
            url=self._api_url,
            callback=self.parse,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": LISTING_URL,
            },
        )

    def parse(self, response):
        items = response.json()
        self.logger.info(f"{self.name}: API returned {len(items)} total items")

        yielded = 0
        for item in items:
            title = (item.get("Title") or "").strip()
            if not title:
                continue

            # Skip Arabic articles — English titles are pure ASCII
            if not title.isascii():
                continue

            date_raw = (item.get("ArticleStartDate") or "").strip()
            pub_date = parse_date(date_raw)  # DD/MM/YYYY → ISO 8601

            # Skip articles outside the configured timeframe
            if pub_date and not self.is_within_timeframe(pub_date):
                continue

            # Article URL constructed from the FileLeafRef (.aspx filename)
            file_ref = (item.get("FileLeafRef") or "").strip()
            link = (
                f"{self._base_url}/en/MediaCenter/News/Pages/{file_ref}"
                if file_ref
                else ""
            )

            # Extract thumbnail from the PublishingRollupImage HTML snippet
            img_html = item.get("PublishingRollupImage") or ""
            thumb = ""
            m = _IMG_SRC_RE.search(img_html)
            if m:
                src = m.group(1).strip()
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = self._base_url + src
                thumb = src

            description = (item.get("Comments") or "").strip()
            category = (item.get("Category") or "").strip()

            yielded += 1
            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumb,
                pub_date=pub_date or date_raw,
                category=category,
            )

        self.logger.info(f"{self.name}: yielded {yielded} English articles within timeframe")
