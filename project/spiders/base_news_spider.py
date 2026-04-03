"""
BaseNewsSpider — shared foundation for all site-specific spiders.

Subclasses receive source_config (the source object from news-sources-sites.json)
and global_config (the settings block) and are expected to implement
start_requests() and parse().  They build output items via self.build_item().
"""
import scrapy
from datetime import datetime, timezone

from project.utils.url_utils import clean_url
from project.utils.date_utils import calculate_cutoff, is_within_cutoff
from project.utils.dedup import make_id


class BaseNewsSpider(scrapy.Spider):
    """Abstract base providing config handling, timeframe filtering, and item building."""

    # Subclasses must set this
    name = None

    def __init__(self, source_config: dict = None, global_config: dict = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_config: dict = source_config or {}
        self.global_config: dict = global_config or {}
        self.scraped_at: str = datetime.now(timezone.utc).isoformat()

        max_age_hours: int = int(self.global_config.get('maxAgeHours', 168))
        self.cutoff = calculate_cutoff(max_age_hours)
        self.logger.info(
            f'{self.name}: maxAgeHours={max_age_hours}, cutoff={self.cutoff.isoformat()}'
        )

    # ------------------------------------------------------------------ #
    # Helpers for subclasses                                               #
    # ------------------------------------------------------------------ #

    def is_within_timeframe(self, pub_date_iso: str) -> bool:
        """Return True if pub_date_iso is within the configured timeframe."""
        return is_within_cutoff(pub_date_iso, self.cutoff)

    def build_item(
        self,
        *,
        title: str,
        link: str,
        description: str = '',
        thumbnail: str = '',
        pub_date: str = '',
        category: str = '',
    ) -> dict:
        """
        Assemble a fully-structured output dict for one article.
        Fields that are source-level metadata are read from source_config.
        """
        src = self.source_config
        meta: dict = src.get('config', {})

        cleaned_link = clean_url(link) if link else ''
        countries: list = meta.get('countries', [])

        item = {
            'id': '',
            'title': (title or '').strip(),
            'description': (description or '').strip(),
            'thumbnail': (thumbnail or '').strip(),
            'link': cleaned_link,
            'pubDate': pub_date or '',
            'scrapedAt': self.scraped_at,
            'source': meta.get('source', ''),
            'sourceType': src.get('sourceType', 'news'),
            'sourceCountry': src.get('sourceCountry', ''),
            'country': meta.get('primaryCountry', meta.get('country', '')),
            'countries': countries,
            'primaryCountry': meta.get('primaryCountry', meta.get('country', '')),
            'isMultiCountry': len(countries) > 1,
            'category': category or '',
            'jurisdictions': meta.get('jurisdictions', []),
            'matchedKeywords': [],  # populated by orchestrator post-processing
        }
        item['id'] = make_id(item)
        return item
