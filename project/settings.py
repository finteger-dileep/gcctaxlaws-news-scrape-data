BOT_NAME = "project"

SPIDER_MODULES = ["project.spiders"]
NEWSPIDER_MODULE = "project.spiders"

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
