"""
Bahrain National Bureau for Revenue (NBR) spider.
Scrapes https://www.nbr.gov.bh/releases (English version).

Language switch:
  The site defaults to Arabic.  A GET to /language/en sets the en locale cookie,
  which is carried automatically by Scrapy's cookie jar for all subsequent requests.

Listing structure (10 items per page, paginated via ?page=N):
  Items  : a[href*="/releases/"]
  Title  : h3.news-title
  Date   : h4.news-date  (format "09 Feb 2026" = %d %b %Y)
  Thumb  : div.introprojbg style="background-image:url(...)"
  NextPg : <a> whose text contains "Next"

Detail page (/releases/{id}):
  Description: div#news_letter p  (plain <p> tags, no class)
"""
import re
import scrapy
from urllib.parse import urljoin

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://www.nbr.gov.bh"
LISTING_URL = "https://www.nbr.gov.bh/releases"
LANG_URL = "https://www.nbr.gov.bh/language/en"

_BG_RE = re.compile(r"background-image:\s*url\(([^)]+)\)", re.IGNORECASE)


class BahrainNbrSpider(BaseNewsSpider):
    name = "bahrain_nbr"

    custom_settings = {
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    _DEFAULT_SOURCE_CONFIG = {
        "id": "bahrain_nbr",
        "name": "National Bureau for Revenue - Bahrain",
        "sourceType": "official",
        "sourceCountry": "BH",
        "config": {
            "baseUrl": BASE_URL,
            "source": LISTING_URL,
            "langUrl": LANG_URL,
            "country": "Bahrain",
            "countries": ["Bahrain"],
            "primaryCountry": "Bahrain",
            "jurisdictions": ["BH"],
        },
    }
    _DEFAULT_GLOBAL_CONFIG = {"maxAgeHours": 168}

    def __init__(self, source_config=None, global_config=None, *args, **kwargs):
        sc = source_config if source_config is not None else self._DEFAULT_SOURCE_CONFIG
        gc = global_config if global_config is not None else self._DEFAULT_GLOBAL_CONFIG
        super().__init__(source_config=sc, global_config=gc, *args, **kwargs)

        meta = self.source_config.get("config", {})
        self._base_url = meta.get("baseUrl", BASE_URL)
        self._listing_url = meta.get("source", LISTING_URL)
        self._lang_url = meta.get("langUrl", LANG_URL)

    # ------------------------------------------------------------------ #
    # Step 1: switch language to English                                   #
    # ------------------------------------------------------------------ #

    async def start(self):
        yield scrapy.Request(
            url=self._lang_url,
            callback=self._start_listing,
            dont_filter=True,
        )

    def _start_listing(self, response):
        """Language cookie is now set; start crawling the listing."""
        yield scrapy.Request(
            url=self._listing_url,
            callback=self.parse_listing,
        )

    # ------------------------------------------------------------------ #
    # Step 2: parse listing page                                           #
    # ------------------------------------------------------------------ #

    def parse_listing(self, response):
        stop_early = False

        for card in response.css("a[href*='/releases/']"):
            link = card.attrib.get("href", "").strip()
            if not link or "/releases/" not in link:
                continue
            link = urljoin(self._base_url, link)

            title = card.css("h3.news-title::text").get("").strip()
            date_raw = card.css("h4.news-date::text").get("").strip()
            pub_date = parse_date(date_raw)

            # Thumbnail from background-image style
            bg_div = card.css("div.introprojbg")
            thumb = ""
            if bg_div:
                style = bg_div.attrib.get("style", "")
                m = _BG_RE.search(style)
                if m:
                    thumb = m.group(1).strip()

            if not title:
                continue

            # Stop once we hit articles outside the timeframe
            if pub_date and not self.is_within_timeframe(pub_date):
                self.logger.info(
                    f"{self.name}: article '{title[:50]}' ({pub_date}) outside "
                    f"timeframe — stopping pagination."
                )
                stop_early = True
                break

            # Follow detail page for description
            yield scrapy.Request(
                url=link,
                callback=self.parse_article,
                cb_kwargs={
                    "title": title,
                    "link": link,
                    "thumbnail": thumb,
                    "pub_date": pub_date or date_raw,
                },
            )

        if not stop_early:
            next_href = response.css("a[rel='next']::attr(href)").get()
            if not next_href:
                # Fallback: find <a> whose text contains "Next"
                for a in response.css("a"):
                    if "next" in a.css("::text").get("").lower():
                        next_href = a.attrib.get("href", "")
                        break

            if next_href:
                yield scrapy.Request(
                    url=urljoin(self._base_url, next_href),
                    callback=self.parse_listing,
                )

    # ------------------------------------------------------------------ #
    # Step 3: parse detail page for description                            #
    # ------------------------------------------------------------------ #

    def parse_article(self, response, title, link, thumbnail, pub_date):
        # Paragraphs inside div#news_letter
        paras = response.css("div#news_letter p::text").getall()
        description = " ".join(p.strip() for p in paras if p.strip())

        yield self.build_item(
            title=title,
            link=link,
            description=description,
            thumbnail=thumbnail,
            pub_date=pub_date,
            category="",
        )
