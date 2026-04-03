## Architecture Overview

### Run command
```bash
python run_scrapers.py
```

### File structure
```
Project/
├── news-sources-sites.json     ← Config: sources, timeframe, cleanup settings
├── run_scrapers.py             ← Orchestrator entry point
├── news_output.json            ← Merged output (auto-generated)
├── logs/
│   └── scraper_YYYYMMDD_HHMMSS.log
└── project/
    ├── pipelines.py            ← CollectorPipeline (shared in-memory list)
    ├── settings.py
    ├── spiders/
    │   ├── base_news_spider.py ← Abstract base (config, cutoff, build_item)
    │   └── gta_news.py         ← GTA Qatar spider (refactored)
    └── utils/
        ├── keywords.py         ← Tax keyword matching (JS logic translated to Python)
        ├── url_utils.py        ← Strip tracking params, canonicalize URL
        ├── date_utils.py       ← Multi-format → ISO 8601 date parsing
        ├── dedup.py            ← Deduplicate by canonical URL + normalised title
        └── output_manager.py   ← Load / cleanup with smart fallback / atomic save
```

### How to add a new source
1. Add an entry in news-sources-sites.json under `sources` (copy the GTA block; set `sourceType: "official"` or `"news"`)
2. Create `project/spiders/<spider_name>.py` extending `BaseNewsSpider` — it's auto-discovered
3. Enable it: `"enabled": true`

### Key behaviour highlights

| Feature | Detail |
|---|---|
| **Timeframe** | `maxAgeHours` in config (168 = 1 week). Spider stops paginating once it hits older articles. |
| **Keyword filter** | 34 tax keywords with plural/boundary-aware regex; attaches `matchedKeywords[]` per article. |
| **Dedup** | By canonical URL, then by normalised title across all sources. |
| **Smart fallback** | If fewer than `minArticlesToKeep` fresh articles exist, keeps the N most-recent regardless of age. |
| **URL cleaning** | Strips `utm_*`, `ref`, `output`, `fbclid` etc. before storing. |
| **Atomic save** | Writes `.tmp` then renames — last valid JSON always preserved on failure. |
| **Per-source isolation** | Each spider wrapped in `try/except`; one failing source doesn't stop others. |
| **Deterministic ID** | SHA-256 of canonical URL → first 16 hex chars; stable across re-runs. |
| **Timestamped logs** | `logs/scraper_YYYYMMDD_HHMMSS.log` — DEBUG to file, INFO to console. |

Made changes.