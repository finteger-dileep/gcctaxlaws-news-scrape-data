"""
Output file management: load, cleanup with smart fallback, and atomic save.
"""
import json
import os
import logging
from datetime import datetime, timezone, timedelta

from project.utils.date_utils import is_within_cutoff

logger = logging.getLogger(__name__)


class OutputManager:

    def load_existing(self, path: str) -> list[dict]:
        """
        Load the existing output JSON.
        Returns an empty list (and logs a warning) on any failure so that
        a corrupted file does NOT cause the run to crash.
        """
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                logger.info(f'Loaded {len(data)} existing articles from {path}')
                return data
            logger.warning(f'Unexpected JSON structure in {path}; treating as empty')
        except Exception as exc:
            logger.warning(f'Could not read {path}: {exc}; treating as empty')
        return []

    def cleanup(self, articles: list[dict], max_age_hours: int, min_to_keep: int) -> list[dict]:
        """
        Remove articles older than max_age_hours.

        Smart Fallback Strategy:
          If the result would have fewer than min_to_keep articles, extend
          the window by returning the N most-recently-published articles
          (so the output always has at least min_to_keep items).
        """
        if not articles:
            return articles

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        fresh = [a for a in articles if is_within_cutoff(a.get('pubDate', ''), cutoff)]

        if len(fresh) >= min_to_keep:
            return fresh

        # Smart fallback: sort by pubDate desc, keep at least min_to_keep
        sorted_all = sorted(
            articles,
            key=lambda a: a.get('pubDate', '') or '',
            reverse=True,
        )
        keep_count = max(min_to_keep, len(fresh))
        fallback = sorted_all[:keep_count]
        logger.info(
            f'Cleanup smart fallback: only {len(fresh)} fresh articles '
            f'(need {min_to_keep}); keeping {len(fallback)} most-recent instead'
        )
        return fallback

    def save(self, path: str, articles: list[dict]) -> None:
        """
        Atomically write articles to path as pretty-printed JSON.
        Writes to a .tmp file first, then renames to avoid partial writes.
        """
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        logger.info(f'Saved {len(articles)} articles → {path}')
