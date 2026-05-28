"""Search adapters: Crawl4AI (primary, self-hosted), seed file (fallback), DuckDuckGo (last resort)."""
import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from urllib.parse import unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.cache import cache

logger = logging.getLogger(__name__)

# ── Seed cache ──────────────────────────────────────────────────────────────
_seed_cache = None


def _load_seeds():
    global _seed_cache
    if _seed_cache is not None:
        return _seed_cache
    seed_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds.json")
    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Flatten all categories into one list with dedup
        all_urls = []
        seen = set()
        for category, urls in data.items():
            for url in urls:
                if url not in seen:
                    seen.add(url)
                    all_urls.append({"title": f"[{category}] {url}", "url": url})
        random.shuffle(all_urls)
        _seed_cache = all_urls
        logger.info("Loaded %d seed URLs from %s", len(_seed_cache), seed_path)
    except Exception as e:
        logger.warning("Failed to load seeds: %s", e)
        _seed_cache = []
    return _seed_cache


def _get_retry_session(timeout=10):
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ── Crawl4AI self-hosted ────────────────────────────────────────────────────

_CRAWL4AI_BASE = "http://crawl4ai:11235"


def _crawl4ai_available() -> bool:
    """Check if Crawl4AI container is reachable."""
    try:
        r = requests.get(f"{_CRAWL4AI_BASE}/health", timeout=2, verify=False)
        return r.status_code == 200
    except Exception:
        return False


def _crawl4ai_crawl_urls(urls: list, extract_links: bool = True, timeout: int = 30) -> list:
    """Use Crawl4AI /crawl endpoint (v0.8.6 API)."""
    if not urls:
        return []
    try:
        payload = {
            "urls": urls[:10],
            "browser_config": {"headless": True, "timeout": 15000},
            "crawler_config": {"extract_links": extract_links, "verbose": False},
        }
        r = requests.post(
            f"{_CRAWL4AI_BASE}/crawl",
            json=payload,
            timeout=timeout,
            verify=False,
        )
        data = r.json()
        if not data.get("success"):
            logger.warning("Crawl4AI crawl failed: %s", data.get("error"))
            return []
        results = []
        for result in data.get("results", []):
            base_url = result.get("url", "")
            links = result.get("links", {})
            for link in links.get("internal", []) + links.get("external", []):
                href = link.get("href", "")
                text = link.get("text", "")
                if not href.startswith("http"):
                    continue
                if any(k in href.lower() for k in ["contact", "about", "investor", "partnership", "business"]):
                    results.append({"title": text or href, "url": href})
            if base_url:
                results.append({"title": result.get("title", base_url), "url": base_url})
        logger.info("Crawl4AI extracted %d pages from %d URLs", len(results), len(urls))
        return results
    except Exception as e:
        logger.warning("Crawl4AI crawl error: %s", e)
        return []


def _crawl4ai_extract_contact_pages(urls: list, limit: int = 20) -> list:
    """Use Crawl4AI /crawl to extract contact pages from seed URLs."""
    return _crawl4ai_crawl_urls(urls[:limit], extract_links=True, timeout=45)


def _crawl4ai_search(query: str, limit: int = 10) -> list:
    """Search via Crawl4AI by crawling seed URLs related to query."""
    try:
        seeds = _load_seeds()
        query_keywords = set(query.lower().split())
        matched = [s for s in seeds if any(kw in s.get("title","").lower() for kw in query_keywords)]
        if not matched:
            matched = seeds
        urls = [s["url"] for s in matched[:limit*3]]
        return _crawl4ai_crawl_urls(urls, extract_links=True, timeout=25)
    except Exception:
        pass
    return []


# ── Seed-based discovery ────────────────────────────────────────────────────

def search_seed(limit: int = 20, exclude_processed: bool = True) -> list:
    """Return URLs from the local seed file.  
    If exclude_processed, skip URLs already in the Target table.
    """
    seeds = _load_seeds()
    if not seeds:
        return []
    
    # Optionally filter out already-processed URLs
    if exclude_processed:
        try:
            from app import db
            from app.models import Target
            from app import create_app
            app = create_app()
            with app.app_context():
                existing = {t.url for t in Target.query.with_entities(Target.url).all()}
                seeds = [s for s in seeds if s["url"] not in existing]
        except Exception:
            pass
    
    return seeds[:limit]


# ── DuckDuckGo fallback ────────────────────────────────────────────────────

_ddg_session = None


def _get_ddg_session():
    global _ddg_session
    if _ddg_session is None:
        _ddg_session = _get_retry_session(timeout=8)
    return _ddg_session


def search_duckduckgo(query, limit=10):
    session = _get_ddg_session()
    endpoints = [
        "https://html.duckduckgo.com/html/",
        "https://duckduckgo.com/html/",
        "http://html.duckduckgo.com/html/",
    ]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    try:
        r = None
        for endpoint in endpoints:
            try:
                r = session.post(
                    endpoint,
                    data={"q": query, "b": ""},
                    headers=headers,
                    timeout=8,
                )
                if r.status_code == 200:
                    break
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                continue

        if not r or r.status_code != 200:
            logger.warning("DuckDuckGo returned no results for '%s'", query)
            return []

        results = []
        pattern = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)\u003c/a>', re.DOTALL)
        for match in pattern.finditer(r.text):
            url = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()

            if url.startswith("//duckduckgo.com/l/") or url.startswith("/l/"):
                m = re.search(r'uddg=([^&]+)', url)
                if m:
                    url = unquote(m.group(1))

            if url.startswith("http"):
                results.append({"title": title, "url": url})
                if len(results) >= limit:
                    break

        logger.info("DuckDuckGo: %d results for '%s'", len(results), query)
        return results
    except Exception:
        logger.exception("DuckDuckGo search failed for query: %s", query)
        return []


# ── Unified search ──────────────────────────────────────────────────────────

_search_cache_ttl = int(os.getenv("SEARCH_CACHE_TTL", "1800"))


def search_primary(query: str, limit: int = 10) -> list:
    """Primary search with Redis caching."""
    cache_key = {"query": query, "limit": limit}
    cached = cache.get("search_primary", cache_key)
    if cached is not None:
        logger.info("Search cache hit for '%s'", query)
        return cached

    # 1. Try Crawl4AI self-hosted
    if _crawl4ai_available():
        logger.info("Crawl4AI is available, trying search...")
        results = _crawl4ai_search(query, limit=limit)
        if results:
            cache.set("search_primary", cache_key, results, ttl=_search_cache_ttl)
            return results[:limit]
        # Fallback to seed extraction via Crawl4AI
        seeds = _load_seeds()
        seed_urls = [s["url"] for s in seeds[:limit * 2]]
        results = _crawl4ai_extract_contact_pages(seed_urls, limit=limit)
        if results:
            cache.set("search_primary", cache_key, results, ttl=_search_cache_ttl)
            return results[:limit]
    else:
        logger.info("Crawl4AI not available, using seed file...")

    # 2. Use local seed file
    results = search_seed(limit=limit)
    if results:
        cache.set("search_primary", cache_key, results, ttl=_search_cache_ttl)
        return results

    # 3. Last resort: DuckDuckGo
    logger.info("Seed empty, falling back to DuckDuckGo for '%s'", query)
    results = search_duckduckgo(query, limit=limit)
    cache.set("search_primary", cache_key, results, ttl=_search_cache_ttl)
    return results


# Legacy alias — redirects everything through search_primary
def search_firecrawl(query, limit=10):
    return search_primary(query, limit=limit)


def search_crawl4ai(query, limit=10):
    """Legacy alias, now uses unified search."""
    return search_primary(query, limit=limit)
