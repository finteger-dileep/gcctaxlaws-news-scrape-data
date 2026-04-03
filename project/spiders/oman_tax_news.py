"""
Oman Tax Authority news spider — scrapes https://tms.taxoman.gov.om/portal/news

The listing page contains:
  - 1 featured article (.thumbnail--news-preview / .thumbnial.h-100)
  - 60 list cards (a.latest-news-item) — title + date + thumbnail only
    (No inline description; description is fetched from each article detail page)

All 61 items are on a single page (no server-side pagination observed).
Articles are ordered newest-first; pagination stops once the cutoff date is hit.
"""
import re
import scrapy
from urllib.parse import urljoin

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date

BASE_URL = "https://tms.taxoman.gov.om"
LISTING_URL = "https://tms.taxoman.gov.om/portal/news"


class OmanTaxNewsSpider(BaseNewsSpider):
    name = "oman_tax_news"

    _DEFAULT_SOURCE_CONFIG = {
        "id": "oman_tax_authority",
        "name": "Oman Tax Authority",
        "sourceType": "official",
        "sourceCountry": "OM",
        "config": {
            "baseUrl": BASE_URL,
            "source": LISTING_URL,
            "country": "Oman",
            "countries": ["Oman"],
            "primaryCountry": "Oman",
            "jurisdictions": ["OM"],
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

    # ------------------------------------------------------------------ #
    # Step 1: scrape the listing page                                      #
    # ------------------------------------------------------------------ #

    def start_requests(self):
        yield scrapy.Request(
            url=self._listing_url,
            callback=self.parse_listing,
            headers={"Referer": self._base_url},
        )

    def parse_listing(self, response):
        # --- Featured article (top hero card) ---
        feat_link = response.css(".thumbnial.h-100::attr(href)").get("")
        feat_title = response.css(
            ".thumbnail-content__title--news-preview::attr(title)"
        ).get("") or response.css(
            ".thumbnail-content__title--news-preview::text"
        ).get("")
        feat_date_raw = response.css(
            ".thumbnail-content__published-at--news-preview::text"
        ).get("") or ""
        feat_thumb = response.css(
            ".thumbnail__img--news-preview::attr(src)"
        ).get("") or ""
        feat_desc_snippet = response.css(".news-preview-text-column p ::text").getall()

        if feat_link and feat_title:
            feat_link = urljoin(self._base_url, feat_link.strip())
            feat_pub_date = parse_date(feat_date_raw.strip())
            feat_desc_text = re.sub(r"\s+", " ", " ".join(feat_desc_snippet)).strip()
            # Strip "read more" suffix
            feat_desc_text = re.sub(r"\s*read more\s*$", "", feat_desc_text, flags=re.IGNORECASE).strip()

            if feat_pub_date and not self.is_within_timeframe(feat_pub_date):
                self.logger.info(f"{self.name}: featured article outside timeframe, skipping")
            elif feat_title.strip():
                # Fetch detail page for full description
                yield scrapy.Request(
                    url=feat_link,
                    callback=self.parse_article,
                    cb_kwargs={
                        "title": feat_title.strip(),
                        "link": feat_link,
                        "thumbnail": feat_thumb.strip(),
                        "pub_date": feat_pub_date,
                        "description_hint": feat_desc_text,
                    },
                )

        # --- List items (a.latest-news-item) ---
        for item in response.css("a.latest-news-item"):
            link_rel = item.attrib.get("href", "")
            if not link_rel:
                continue

            title = (
                item.css(".latest-news-item__title::attr(title)").get("")
                or item.css(".latest-news-item__title::text").get("")
            ).strip()
            date_raw = item.css(".latest-news-item__published-at::text").get("").strip()
            thumb = item.css(".latest-news-item__thumbnail-img::attr(src)").get("").strip()

            pub_date = parse_date(date_raw)

            # Stop fetching older articles (list is newest-first)
            if pub_date and not self.is_within_timeframe(pub_date):
                self.logger.info(
                    f"{self.name}: reached articles older than cutoff ({pub_date}); stopping."
                )
                break

            if not title:
                continue

            abs_link = urljoin(self._base_url, link_rel.strip())
            yield scrapy.Request(
                url=abs_link,
                callback=self.parse_article,
                cb_kwargs={
                    "title": title,
                    "link": abs_link,
                    "thumbnail": thumb,
                    "pub_date": pub_date,
                    "description_hint": "",
                },
            )

    # ------------------------------------------------------------------ #
    # Step 2: parse each article detail page for the description           #
    # ------------------------------------------------------------------ #

    def parse_article(self, response, title, link, thumbnail, pub_date, description_hint):
        # Article body lives inside .c-content or .rich-content on the detail page
        desc = ""

        for sel in [
            ".c-content p",
            ".rich-content p",
            ".journal-content-article p",
            "article p",
        ]:
            paras = response.css(sel)
            if paras:
                candidate = re.sub(
                    r"\s+",
                    " ",
                    " ".join(p.css("::text").get("") for p in paras),
                ).strip()
                # Skip navigation noise or very short snippets
                if len(candidate) > 40 and "back to news" not in candidate.lower():
                    desc = candidate
                    break

        # Fall back to the snippet captured on the listing page
        if not desc and description_hint:
            desc = description_hint

        thumb_abs = urljoin(self._base_url, thumbnail) if thumbnail else ""

        yield self.build_item(
            title=title,
            link=link,
            description=desc,
            thumbnail=thumb_abs,
            pub_date=pub_date,
            category="News",
        )
