import scrapy


class NewsItem(scrapy.Item):
    title = scrapy.Field()
    link = scrapy.Field()
    description = scrapy.Field()
    thumbnail = scrapy.Field()
    category = scrapy.Field()
    pubDate = scrapy.Field()
    source = scrapy.Field()
