import scrapy
import re
from urllib.parse import urljoin
from datetime import datetime

from project.items import NewsItem

BASE_URL = "https://gta.gov.qa"
AJAX_URL = "https://gta.gov.qa/en/ajax/media-center.page"
NEWS_SOURCE = "https://gta.gov.qa/en/media-center/news"


class GtaNewsSpider(scrapy.Spider):
    name = "gta_news"
    custom_settings = {
        "FEEDS": {
            "gta_news.json": {
                "format": "json",
                "overwrite": True,
                "indent": 2,
            }
        },
        # Disable scrapy-poet / zyte-api addons so plain HTTP requests are used
        "ADDONS": {},
    }

    def start_requests(self):
        yield scrapy.Request(
            url=f"{AJAX_URL}?dct=Content%2FNews&start=0&rows=10",
            callback=self.parse,
            headers={"Referer": NEWS_SOURCE},
            cb_kwargs={"start": 0},
            dont_filter=True,
        )

    def parse(self, response, start=0):
        items = response.css("li.bottom-details-item")

        for item in items:
            title_el = item.css(".desc-title a")
            title = title_el.css("::text").get("").strip()
            relative_link = title_el.attrib.get("href", "")
            link = urljoin(BASE_URL, relative_link) if relative_link else ""

            description = re.sub(
                r"\s+",
                " ",
                " ".join(item.css(".desc-title-and-pg p ::text").getall()),
            ).strip()

            thumb_src = (item.css(".img-wrapper img::attr(src)").get("") or "").strip()
            thumbnail = urljoin(BASE_URL, thumb_src) if thumb_src else ""

            date_text = "".join(
                t for t in item.css(".date ::text").getall() if t.strip()
            ).strip()
            pub_date = self._parse_date(date_text)

            yield NewsItem(
                title=title,
                link=link,
                description=description,
                thumbnail=thumbnail,
                category="News",
                pubDate=pub_date,
                source=NEWS_SOURCE,
            )

        # Follow pagination using data attributes on the #pagination-here div
        total_str = response.css("#pagination-here::attr(data-total)").get()
        rows_str = response.css("#pagination-here::attr(data-rows)").get()

        if total_str and rows_str:
            total = int(total_str)
            rows = int(rows_str)
            next_start = start + rows
            if next_start < total:
                yield scrapy.Request(
                    url=f"{AJAX_URL}?dct=Content%2FNews&start={next_start}&rows={rows}",
                    callback=self.parse,
                    headers={"Referer": NEWS_SOURCE},
                    cb_kwargs={"start": next_start},
                    dont_filter=True,
                )

    def _parse_date(self, date_text):
        date_text = date_text.strip()
        for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_text, fmt)
                return dt.strftime("%Y-%m-%dT00:00:00")
            except ValueError:
                continue
        return date_text
