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

ADDONS = {}

try:
    import scrapy_poet
    import scrapy_zyte_api
    ADDONS = {
        scrapy_poet.Addon: 300,
        scrapy_zyte_api.Addon: 500,
    }
    SCRAPY_POET_DISCOVER = ["project.pages"]
    # ZYTE_API_KEY = "YOUR_API_KEY"
    ZYTE_API_TRANSPARENT_MODE = False
except ImportError:
    pass

