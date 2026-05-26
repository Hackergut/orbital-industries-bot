import logging
import os
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


def search_firecrawl(query, limit=10):
    key = os.getenv("FIRECRAWL_API_KEY")
    if key:
        try:
            r = requests.post(
                "https://api.firecrawl.dev/v1/search",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"query": query, "limit": limit},
                timeout=30,
                verify=False,
            )
            data = r.json()
            if data.get("success") and data.get("data"):
                return [{"title": x.get("title", ""), "url": x.get("url", "")} for x in data["data"]]
        except Exception:
            logger.exception("Firecrawl search failed for query: %s", query)

    return search_duckduckgo(query, limit)


def _get_retry_session():
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def search_duckduckgo(query, limit=10):
    session = _get_retry_session()
    endpoints = [
        "https://duckduckgo.com/html/",
        "https://html.duckduckgo.com/html/",
        "http://duckduckgo.com/html/",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    }
    try:
        r = None
        for endpoint in endpoints:
            try:
                r = session.post(
                    endpoint,
                    data={"q": query},
                    headers=headers,
                    timeout=15,
                )
                if r.status_code == 200:
                    break
            except requests.exceptions.SSLError:
                continue
            except requests.exceptions.ConnectionError:
                continue

        if not r or r.status_code != 200:
            return []

        results = []
        pattern = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
        for match in pattern.finditer(r.text):
            url = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()

            if url.startswith("//duckduckgo.com/l/"):
                m = re.search(r'uddg=([^&]+)', url)
                if m:
                    from urllib.parse import unquote
                    url = unquote(m.group(1))
            elif url.startswith("/l/"):
                m = re.search(r'uddg=([^&]+)', url)
                if m:
                    from urllib.parse import unquote
                    url = unquote(m.group(1))

            if url.startswith("http"):
                results.append({"title": title, "url": url})
                if len(results) >= limit:
                    break

        logger.info("DuckDuckGo search: %d results for '%s'", len(results), query)
        return results
    except Exception:
        logger.exception("DuckDuckGo search failed for query: %s", query)
        return []
