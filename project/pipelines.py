"""
Scrapy item pipelines.

CollectorPipeline appends every scraped item (as a plain dict) to the
module-level COLLECTED_ITEMS list so the orchestrator can read them after
all spiders finish.  The orchestrator resets this list before each run.
"""

# Shared, mutable list — reset by run_scrapers.py before each crawl.
COLLECTED_ITEMS: list[dict] = []


class CollectorPipeline:
    """Collect every yielded item into the module-level COLLECTED_ITEMS list."""

    def process_item(self, item, *args, **kwargs):
        record = dict(item) if not isinstance(item, dict) else item
        COLLECTED_ITEMS.append(record)
        return item
