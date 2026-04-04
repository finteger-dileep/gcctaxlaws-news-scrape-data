"""
Arabian Business (arabianbusiness.com) news spider.

Source: https://www.arabianbusiness.com/?s=<keyword>

Mechanism:
  Arabian Business uses Cloudflare Bot Management which blocks Python's TLS
  stack (OpenSSL/urllib/Twisted all produce a non-browser TLS fingerprint that
  Cloudflare detects and blocks with HTTP 403).

  However, the system ``curl`` binary (git-bundled on Windows, using Schannel)
  produces a real-Windows TLS fingerprint that Cloudflare accepts.

  This spider uses Scrapy 2.13+'s ``async def start()`` to yield items directly
  (no Scrapy HTTP requests are issued), while delegating all actual HTTP to
  ``curl`` via ``subprocess.run``, wrapped in ``asyncio.to_thread`` so it does
  not block the Scrapy event loop.

  Workflow:
    1. For each TAX_KEYWORD, fetch the search RSS feed via curl:
         https://www.arabianbusiness.com/search/<keyword>/feed/rss2/
       Each feed returns up to 200 items (~2 years of history).
    2. Parse RSS items, filter to the configured cutoff window, deduplicate by URL.
    3. For each unique article URL, fetch the article page via curl to extract
       the ``og:image`` thumbnail.  Thumbnail fetches are concurrent (asyncio.gather),
       capped by a semaphore at 6 simultaneous connections.
    4. Yield assembled item dicts — Scrapy pipelines receive them normally.

  A 0.5-second pause is inserted between keyword RSS fetches to be polite.

Fields extracted:
  - RSS <title>          → title
  - RSS <link>           → URL (canonical, no UTM)
  - RSS <pubDate>        → pubDate (RFC 2822 → ISO 8601 UTC)
  - RSS <description>    → description (HTML + WP boilerplate stripped)
  - RSS <category>       → category (up to 3 non-generic tags joined)
  - Article og:image     → thumbnail
"""
import asyncio
import re
import shutil
import subprocess
from html import unescape
from urllib.parse import quote_plus, urlparse, urlunparse

from project.spiders.base_news_spider import BaseNewsSpider
from project.utils.date_utils import parse_date
from project.utils.keywords import TAX_KEYWORDS

BASE_URL = "https://www.arabianbusiness.com"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# curl executable — resolved once at import time
_CURL = shutil.which("curl") or "curl"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOILERPLATE_RE = re.compile(r"<p>The post .+?</p>", re.DOTALL)

# Categories too generic to include in "category" field
_SKIP_CATS = {"news", "news & analysis"}


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _cdata(s: str) -> str:
    m = _CDATA_RE.search(s)
    return m.group(1).strip() if m else unescape(s).strip()


def _strip_html(html: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(html or "")).strip()


def _clean_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _sync_curl_fetch(url: str, timeout: int = 20) -> str:
    """Run ``curl`` in a subprocess and return the response body as text.

    Uses Schannel TLS (Windows) which bypasses Cloudflare bot detection.
    Raises RuntimeError on non-zero exit code or Cloudflare challenge response.
    """
    result = subprocess.run(
        [
            _CURL,
            "-s",                         # silent (no progress bar)
            "--max-time", str(timeout),
            "--compressed",               # accept gzip/br, decode automatically
            "-A", _USER_AGENT,
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Language: en-US,en;q=0.9",
            url,
        ],
        capture_output=True,
        timeout=timeout + 5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"curl exited {result.returncode}: {result.stderr.decode('utf-8', errors='replace')[:200]}"
        )
    text = result.stdout.decode("utf-8", errors="replace")
    if "just a moment" in text.lower():
        raise RuntimeError("Cloudflare challenge in response")
    return text


async def _curl_fetch(url: str, timeout: int = 20) -> str:
    """Async wrapper: runs :func:`_sync_curl_fetch` in a thread pool."""
    return await asyncio.to_thread(_sync_curl_fetch, url, timeout)


async def _fetch_thumbnail(url: str, sem: asyncio.Semaphore) -> str:
    """Fetch an article page and extract ``og:image``, or return '' on error."""
    async with sem:
        try:
            html = await _curl_fetch(url, timeout=15)
            m = re.search(
                r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html
            )
            if not m:
                m = re.search(
                    r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', html
                )
            return m.group(1) if m else ""
        except Exception:
            return ""


class ArabianBusinessNewsSpider(BaseNewsSpider):
    """curl-subprocess spider for Arabian Business (Cloudflare Schannel bypass).

    Scrapy's ``async def start()`` yields items directly; all HTTP is done by
    the system ``curl`` binary via ``asyncio.to_thread``, avoiding Cloudflare's
    TLS fingerprint detection that blocks Python's OpenSSL/urllib/Twisted stack.
    """

    name = "arabianbusiness_news"

    # No Scrapy HTTP requests are made, so downloader settings are irrelevant.
    custom_settings = {
        "USER_AGENT": _USER_AGENT,
    }

    _DEFAULT_SOURCE_CONFIG = {
        "id": "arabianbusiness",
        "name": "Arabian Business",
        "sourceType": "media",
        "sourceCountry": "AE",
        "config": {
            "baseUrl": BASE_URL,
            "source": f"{BASE_URL}/",
            "country": "United Arab Emirates",
            "countries": ["United Arab Emirates", "Middle East"],
            "primaryCountry": "United Arab Emirates",
            "jurisdictions": ["AE"],
        },
    }

    # ------------------------------------------------------------------ #
    # Main entry point (Scrapy 2.13+ async generator)                     #
    # ------------------------------------------------------------------ #

    async def start(self):  # noqa: D102 — overrides BaseSpider.start
        """Fetch all keyword RSS feeds via curl, deduplicate, harvest thumbnails."""
        thumb_sem = asyncio.Semaphore(6)
        seen_urls: set[str] = set()
        pending: list[dict] = []  # articles within the timeframe window

        # ── Phase 1: collect in-window articles from each keyword RSS ── #
        for kw in TAX_KEYWORDS:
            rss_url = f"{BASE_URL}/search/{quote_plus(kw)}/feed/rss2/"
            self.logger.debug(f"[arabianbusiness] RSS keyword={kw!r}")
            try:
                rss_text = await _curl_fetch(rss_url)
            except Exception as exc:
                self.logger.warning(
                    f"[arabianbusiness] RSS fetch failed for {kw!r}: {exc}"
                )
                await asyncio.sleep(0.5)
                continue

            items_raw = re.findall(r"<item>(.*?)</item>", rss_text, re.DOTALL)
            for item_xml in items_raw:
                def _tag(tag: str, _xml: str = item_xml) -> str:
                    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", _xml, re.DOTALL)
                    return m.group(1).strip() if m else ""

                title = _strip_html(_cdata(_tag("title")))
                link = _clean_url(_cdata(_tag("link")).strip())
                pub_date_str = _tag("pubDate").strip()
                pub_date = parse_date(pub_date_str)

                if not title or not link:
                    continue
                if not self.is_within_timeframe(pub_date):
                    continue
                if link in seen_urls:
                    continue
                seen_urls.add(link)

                desc_raw = _cdata(_tag("description"))
                desc_clean = _strip_html(_BOILERPLATE_RE.sub("", desc_raw))

                cats_raw = re.findall(
                    r"<category[^>]*>(.*?)</category>", item_xml, re.DOTALL
                )
                cat_names = [
                    _cdata(c)
                    for c in cats_raw
                    if _cdata(c).lower() not in _SKIP_CATS
                ]
                category = ", ".join(cat_names[:3])

                pending.append({
                    "title": title,
                    "link": link,
                    "description": desc_clean,
                    "pub_date": pub_date,
                    "category": category,
                })

            await asyncio.sleep(0.5)

        self.logger.info(
            f"[arabianbusiness] {len(pending)} unique in-window articles; "
            "fetching og:image thumbnails…"
        )

        # ── Phase 2: fetch thumbnails concurrently ── #
        thumb_tasks = [_fetch_thumbnail(item["link"], thumb_sem) for item in pending]
        thumbnails = await asyncio.gather(*thumb_tasks)

        # ── Phase 3: yield assembled items ── #
        for item, thumbnail in zip(pending, thumbnails):
            yield self.build_item(
                title=item["title"],
                link=item["link"],
                description=item["description"],
                thumbnail=thumbnail,
                pub_date=item["pub_date"],
                category=item["category"],
            )
