BOT_NAME = "project"

SPIDER_MODULES = ["project.spiders"]
NEWSPIDER_MODULE = "project.spiders"

# Respect robots.txt by default; set False if a site allows scraping
ROBOTSTXT_OBEY = False

# Be polite: 0.5-second delay between requests (orchestrator overrides if needed)
DOWNLOAD_DELAY = 0.5

# No item pipelines by default — the orchestrator enables CollectorPipeline at runtime.
# To use when running spiders standalone, pass -o output.json on the CLI.
ITEM_PIPELINES = {}

import os as _os

ADDONS = {}

try:
    import scrapy_poet
    ADDONS[scrapy_poet.Addon] = 300
    SCRAPY_POET_DISCOVER = ["project.pages"]

    # Only enable the Zyte API download-handler addon when a key is supplied.
    # Without a key, ScrapyZyteAPIHTTPSDownloadHandler replaces the default
    # HTTPS handler and makes all HTTPS requests fail.
    _zyte_key = _os.environ.get("ZYTE_API_KEY", "")
    if _zyte_key:
        import scrapy_zyte_api
        ADDONS[scrapy_zyte_api.Addon] = 500
        ZYTE_API_TRANSPARENT_MODE = False
        ZYTE_API_KEY = _zyte_key
except ImportError:
    pass

