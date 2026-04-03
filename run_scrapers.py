#!/usr/bin/env python3
"""
Orchestrator — run_scrapers.py

Reads news-sources-sites.json, auto-discovers all registered spiders,
runs every enabled source, then post-processes collected items:
  1. Keyword filter  (only tax-relevant articles)
  2. Deduplication   (by canonical URL and normalised title)
  3. ID assignment   (deterministic SHA-256 hash)
  4. Cleanup         (max-age cutoff with smart fallback)
  5. Atomic save     (preserves last valid JSON on failure)
  6. Config update   (lastScrapedAt timestamp)

Usage:
  python run_scrapers.py

Logs are written to logs/scraper_YYYYMMDD_HHMMSS.log
"""
import importlib
import json
import logging
import os
import pkgutil
import sys
import traceback
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Bootstrap: ensure the Project root (where scrapy.cfg lives) is on sys.path  #
# --------------------------------------------------------------------------- #
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ.setdefault('SCRAPY_SETTINGS_MODULE', 'project.settings')

CONFIG_FILE = os.path.join(ROOT, 'news-sources-sites.json')


# --------------------------------------------------------------------------- #
# Spider auto-discovery                                                        #
# --------------------------------------------------------------------------- #

def _build_spider_registry(logger: logging.Logger) -> dict:
    """
    Walk project/spiders/ and register every Scrapy spider class by its .name.
    Adding a new spider is as simple as dropping a new .py file into that folder.
    """
    import scrapy
    from project.spiders.base_news_spider import BaseNewsSpider
    import project.spiders as spiders_pkg

    registry: dict = {}
    for _, module_name, _ in pkgutil.iter_modules(spiders_pkg.__path__):
        if module_name.startswith('_'):
            continue
        try:
            mod = importlib.import_module(f'project.spiders.{module_name}')
            for attr_name in dir(mod):
                cls = getattr(mod, attr_name)
                if (
                    isinstance(cls, type)
                    and issubclass(cls, scrapy.Spider)
                    and cls not in (scrapy.Spider, BaseNewsSpider)
                    and getattr(cls, 'name', None) is not None
                ):
                    registry[cls.name] = cls
                    logger.debug(f'Registered spider: {cls.name}')
        except Exception:
            logger.warning(f'Could not load spider module "{module_name}":\n{traceback.format_exc()}')

    logger.info(f'Spider registry built: {list(registry.keys())}')
    return registry


# --------------------------------------------------------------------------- #
# Logging setup                                                                #
# --------------------------------------------------------------------------- #

def _setup_logging(logs_dir: str, timestamp: str) -> tuple[logging.Logger, str]:
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, f'scraper_{timestamp}.log')

    logger = logging.getLogger('orchestrator')
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s')

    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger, log_file


# --------------------------------------------------------------------------- #
# Config helpers                                                               #
# --------------------------------------------------------------------------- #

def _load_config() -> dict:
    with open(CONFIG_FILE, encoding='utf-8') as f:
        return json.load(f)


def _save_config(config: dict) -> None:
    tmp = CONFIG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main() -> None:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    config = _load_config()
    settings_cfg: dict = config.get('settings', {})

    logs_dir = os.path.join(ROOT, settings_cfg.get('logsDir', 'logs'))
    logger, log_file = _setup_logging(logs_dir, timestamp)
    logger.info(f'{"=" * 60}')
    logger.info(f'Scraper run started: {timestamp}')
    logger.info(f'Log file: {log_file}')

    # ------------------------------------------------------------------ #
    # Collect enabled sources                                              #
    # ------------------------------------------------------------------ #
    sources: list[dict] = [s for s in config.get('sources', []) if s.get('enabled', True)]
    logger.info(f'Enabled sources: {len(sources)}')

    if not sources:
        logger.warning('No enabled sources found. Exiting.')
        return

    spider_registry = _build_spider_registry(logger)

    # ------------------------------------------------------------------ #
    # Reset collector and configure Scrapy                                 #
    # ------------------------------------------------------------------ #
    import project.pipelines as pipes
    pipes.COLLECTED_ITEMS = []  # fresh slate for this run

    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    scrapy_settings = get_project_settings()
    # Enable collector pipeline (higher priority so it isn't overridden by spider custom_settings)
    scrapy_settings.set(
        'ITEM_PIPELINES',
        {'project.pipelines.CollectorPipeline': 300},
        priority='spider',
    )
    scrapy_settings.set('FEEDS', {}, priority='spider')      # disable per-spider file output
    scrapy_settings.set('LOG_LEVEL', 'WARNING', priority='spider')  # suppress Scrapy noise

    process = CrawlerProcess(scrapy_settings)

    # ------------------------------------------------------------------ #
    # Queue each enabled source (safely)                                   #
    # ------------------------------------------------------------------ #
    queued = 0
    for source in sources:
        spider_name: str = source.get('spider', '')
        spider_cls = spider_registry.get(spider_name)

        if not spider_cls:
            logger.warning(
                f"No spider registered for '{spider_name}' "
                f"(source: {source.get('id')}). "
                "Create project/spiders/{spider_name}.py to add it."
            )
            continue

        try:
            process.crawl(
                spider_cls,
                source_config=source,
                global_config=settings_cfg,
            )
            logger.info(f"Queued: [{source.get('id')}] via spider '{spider_name}'")
            queued += 1
        except Exception:
            logger.error(
                f"Failed to queue spider '{spider_name}' for source '{source.get('id')}':\n"
                + traceback.format_exc()
            )

    if queued == 0:
        logger.warning('No spiders were queued. Nothing to run.')
        return

    # ------------------------------------------------------------------ #
    # Run all spiders (blocking; errors per-spider are logged internally)  #
    # ------------------------------------------------------------------ #
    logger.info(f'Starting Scrapy crawl ({queued} spider(s))…')
    try:
        process.start()
    except Exception:
        logger.error(f'CrawlerProcess error:\n{traceback.format_exc()}')

    collected: list[dict] = list(pipes.COLLECTED_ITEMS)
    logger.info(f'Spiders finished. Raw items collected: {len(collected)}')

    # ------------------------------------------------------------------ #
    # Post-processing                                                       #
    # ------------------------------------------------------------------ #
    from project.utils.keywords import get_matched_keywords
    from project.utils.dedup import deduplicate, make_id
    from project.utils.output_manager import OutputManager

    output_file = os.path.join(ROOT, settings_cfg.get('outputFile', 'news_output.json'))
    max_age_hours: int = int(settings_cfg.get('maxAgeHours', 168))
    min_to_keep: int = int(settings_cfg.get('minArticlesToKeep', 5))
    cleanup_enabled: bool = bool(settings_cfg.get('cleanupEnabled', True))

    om = OutputManager()

    # Load existing output (safe: returns [] on any failure)
    existing: list[dict] = om.load_existing(output_file)

    # 1. Keyword filter new items and attach matched keywords
    relevant: list[dict] = []
    skipped = 0
    for item in collected:
        matched = get_matched_keywords(item)
        if matched:
            item['matchedKeywords'] = matched
            relevant.append(item)
        else:
            skipped += 1
    logger.info(f'Keyword filter: {len(relevant)} relevant / {len(collected)} total ({skipped} skipped)')

    # 2. Ensure deterministic IDs on existing items (back-fill if missing)
    for item in existing:
        if not item.get('id'):
            item['id'] = make_id(item)

    # 3. Merge existing + new, then deduplicate
    #    Existing items come first so they take priority in dedup.
    merged = deduplicate(existing + relevant)
    logger.info(f'After merge + dedup: {len(merged)} articles')

    # 4. Cleanup old articles (with smart fallback)
    if cleanup_enabled:
        merged = om.cleanup(merged, max_age_hours, min_to_keep)
        logger.info(f'After cleanup: {len(merged)} articles')

    # 5. Atomic save — preserve existing output if we ended up with nothing
    if merged:
        try:
            om.save(output_file, merged)
        except Exception:
            logger.error(f'Failed to save output:\n{traceback.format_exc()}')
    elif existing:
        logger.warning(
            'Processing produced 0 articles; preserving existing output file unchanged.'
        )
    else:
        logger.warning('No articles and no existing output; nothing saved.')

    # 6. Update lastScrapedAt in config
    try:
        config['settings']['lastScrapedAt'] = datetime.now(timezone.utc).isoformat()
        _save_config(config)
        logger.info('Updated lastScrapedAt in news-sources-sites.json')
    except Exception:
        logger.error(f'Could not update config:\n{traceback.format_exc()}')

    logger.info(f'Scraper run complete: {timestamp}')
    logger.info(f'{"=" * 60}')


if __name__ == '__main__':
    main()
