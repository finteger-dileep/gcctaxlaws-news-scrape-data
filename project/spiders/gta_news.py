"""
GTA Qatar news spider - scrapes https://gta.gov.qa/en/media-center/news

Extends BaseNewsSpider.  Can be run:
  - Via the orchestrator (run_scrapers.py) - source_config is injected.
  - Standalone: scrapy crawl gta_news -o output.json
    (uses built-in defaults; global_config defaults to 1-week window)
"""
import re
import scrapy
from urllib.parse import urljoin

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date


class GtaNewsSpider(BaseNewsSpider):
    name = "gta_news"

    # --- Default config used when running the spider standalone ---
    _DEFAULT_SOURCE_CONFIG = {
        "id": "gta_qatar",
        "name": "General Tax Authority Qatar",
        "sourceType": "official",
        "sourceCountry": "QA",
        "config": {
            "baseUrl": "https://gta.gov.qa",
            "ajaxUrl": "https://gta.gov.qa/en/ajax/media-center.page",
            "source": "https://gta.gov.qa/en/media-center/news",
            "country": "Qatar",
            "countries": ["Qatar"],
            "primaryCountry": "Qatar",
            "jurisdictions": ["QA"],
        },
    }
    _DEFAULT_GLOBAL_CONFIG = {"maxAgeHours": 168}

    def __init__(self, source_config=None, global_config=None, *args, **kwargs):
        sc = source_config if source_config is not None else self._DEFAULT_SOURCE_CONFIG
        gc = global_config if global_config is not None else self._DEFAULT_GLOBAL_CONFIG
        super().__init__(source_config=sc, global_config=gc, *args, **kwargs)

        meta = self.source_config.get("config", {})
        self._base_url = meta.get("baseUrl", "https://gta.gov.qa")
        self._ajax_url = meta.get("ajaxUrl", "https://gta.gov.qa/en/ajax/media-center.page")
        self._referrer = meta.get("source", "https://gta.gov.qa/en/media-center/news")

    async def start(self):
        yield scrapy.Request(
            url=f"{self._ajax_url}?dct=Content%2FNews&start=0&rows=10",
            callback=self.parse,
            headers={"Referer": self._referrer},
            cb_kwargs={"start": 0},
            dont_filter=True,
        )

    def parse(self, response, start=0):
        items = response.css("li.bottom-details-item")
        stop_pagination = False

        for item in items:
            title_el = item.css(".desc-title a")
            title = title_el.css("::text").get("").strip()
            relative_link = title_el.attrib.get("href", "")
            link = urljoin(self._base_url, relative_link) if relative_link else ""

            description = re.sub(
                r"\s+",
                " ",
                " ".join(item.css(".desc-title-and-pg p ::text").getall()),
            ).strip()

            thumb_src = (item.css(".img-wrapper img::attr(src)").get("") or "").strip()
            thumbnail = urljoin(self._base_url, thumb_src) if thumb_src else ""

            raw_date = "".join(
                t for t in item.css(".date ::text").getall() if t.strip()
            ).strip()
            pub_date = parse_date(raw_date)

            if pub_date and not self.is_within_timeframe(pub_date):
                stop_pagination = True
                continue

            if not title:
                continue

            yield self.build_item(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                pub_date=pub_date,
                category="News",
            )

        if stop_pagination:
            self.logger.info(
                f"{self.name}: reached articles older than cutoff at start={start}; stopping."
            )
            return

        total_str = response.css("#pagination-here::attr(data-total)").get()
        rows_str = response.css("#pagination-here::attr(data-rows)").get()

        if total_str and rows_str:
            total = int(total_str)
            rows = int(rows_str)
            next_start = start + rows
            if next_start < total:
                yield scrapy.Request(
                    url=f"{self._ajax_url}?dct=Content%2FNews&start={next_start}&rows={rows}",
                    callback=self.parse,
                    headers={"Referer": self._referrer},
                    cb_kwargs={"start": next_start},
                    dont_filter=True,
                )
