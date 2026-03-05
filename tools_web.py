"""Web search, page reading, and screenshot tools."""
import asyncio
import hashlib
import logging
import os
import tempfile
import time
from collections import OrderedDict
from typing import Optional

from tools import tool
from discord import Guild

log = logging.getLogger("tools.web")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    """Simple async rate limiter ensuring a minimum interval between calls."""

    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait_time = self.min_interval - (now - self._last_call)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_call = asyncio.get_event_loop().time()


_search_limiter = RateLimiter(min_interval=2.0)


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
class WebCache:
    """Simple in-memory LRU cache with TTL expiration."""

    def __init__(self, max_size: int = 200, default_ttl: int = 3600):
        self._cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.max_size = max_size
        self.default_ttl = default_ttl

    def _make_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def get(self, key: str):
        """Return cached value or None if expired / missing."""
        hk = self._make_key(key)
        entry = self._cache.get(hk)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            self._cache.pop(hk, None)
            return None
        # Move to end (most recently used)
        self._cache.move_to_end(hk)
        return value

    def set(self, key: str, value: object, ttl: int | None = None):
        """Store a value; evict oldest entry if cache is full."""
        hk = self._make_key(key)
        ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + ttl
        # Remove existing entry to refresh position
        self._cache.pop(hk, None)
        self._cache[hk] = (expires_at, value)
        # Evict oldest entries if over capacity
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)


_search_cache = WebCache(max_size=100, default_ttl=3600)   # 1 hour
_page_cache = WebCache(max_size=100, default_ttl=86400)     # 24 hours


# ---------------------------------------------------------------------------
# Web Search (ddgs)
# ---------------------------------------------------------------------------
@tool(
    "web_search",
    "Search the web using DuckDuckGo and other search engines. Returns titles, URLs, and snippets.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default 5, max 10)"},
            "region": {"type": "string", "description": "Region code (e.g. 'jp-jp', 'us-en'). Default: auto"},
        },
        "required": ["query"],
    },
)
async def web_search(guild: Guild, query: str, max_results: int = 5, region: str = None, **kwargs) -> str:
    from ddgs import DDGS

    max_results = min(max_results, 10)

    # Check cache
    cache_key = f"search:{query}:{max_results}:{region}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        log.debug("web_search cache hit for %r", query)
        return cached

    # Rate limit
    await _search_limiter.wait()

    def _search():
        params = {"keywords": query, "max_results": max_results}
        if region:
            params["region"] = region
        return DDGS().text(**params)

    results = await asyncio.to_thread(_search)
    if not results:
        return f"No results found for '{query}'."
    lines = [f"**Web search: '{query}'** ({len(results)} results):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r.get('title', 'No title')}**")
        lines.append(f"   {r.get('href', '')}")
        lines.append(f"   {r.get('body', '')[:400]}")
    response = "\n".join(lines)
    _search_cache.set(cache_key, response)
    return response


@tool(
    "web_news",
    "Search for recent news articles.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "News search query"},
            "max_results": {"type": "integer", "description": "Max results (default 5)"},
        },
        "required": ["query"],
    },
)
async def web_news(guild: Guild, query: str, max_results: int = 5, **kwargs) -> str:
    from ddgs import DDGS

    max_results = min(max_results, 10)

    # Rate limit
    await _search_limiter.wait()

    def _search():
        return DDGS().news(keywords=query, max_results=max_results)

    results = await asyncio.to_thread(_search)
    if not results:
        return f"No news found for '{query}'."
    lines = [f"**News: '{query}'** ({len(results)} articles):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r.get('title', '')}**")
        lines.append(f"   {r.get('url', '')}")
        lines.append(f"   {r.get('body', '')[:400]}")
        if r.get("date"):
            lines.append(f"   Published: {r['date']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Web Page Reading (trafilatura with tiered fallbacks)
# ---------------------------------------------------------------------------
@tool(
    "read_webpage",
    "Fetch a webpage and extract its main content as clean text. Good for reading articles, docs, and blog posts.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to read"},
            "max_length": {"type": "integer", "description": "Max content length in characters (default 6000)"},
        },
        "required": ["url"],
    },
)
async def read_webpage(guild: Guild, url: str, max_length: int = 6000, **kwargs) -> str:
    # Check cache
    cache_key = f"page:{url}"
    cached = _page_cache.get(cache_key)
    if cached is not None:
        log.debug("read_webpage cache hit for %r", url)
        text = cached
        if len(text) > max_length:
            text = text[:max_length] + f"\n\n... (truncated, {len(cached)} total chars)"
        return f"**Content from {url}:**\n\n{text}"

    text = None

    # --- Tier 1: trafilatura fetch + extract ---
    try:
        import trafilatura

        def _tier1():
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return None
            return trafilatura.extract(
                downloaded,
                include_tables=True,
                include_links=True,
                output_format="txt",
                favor_recall=True,
                deduplicate=True,
            )

        text = await asyncio.to_thread(_tier1)
        if text:
            log.debug("read_webpage Tier 1 (trafilatura) succeeded for %s", url)
    except Exception as e:
        log.warning("read_webpage Tier 1 failed for %s: %s", url, e)

    # --- Tier 2: Jina Reader API fallback ---
    if not text:
        try:
            import urllib.request
            import urllib.error

            def _tier2():
                jina_url = f"https://r.jina.ai/{url}"
                req = urllib.request.Request(jina_url, headers={"Accept": "text/plain"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return resp.read().decode("utf-8", errors="replace")

            text = await asyncio.to_thread(_tier2)
            if text:
                log.debug("read_webpage Tier 2 (Jina Reader) succeeded for %s", url)
        except Exception as e:
            log.warning("read_webpage Tier 2 (Jina) failed for %s: %s", url, e)

    # --- Tier 3: Playwright + trafilatura.extract ---
    if not text:
        try:
            from playwright.async_api import async_playwright
            import trafilatura

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                )
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)  # allow JS rendering
                html = await page.content()
                await browser.close()

            def _tier3_extract():
                return trafilatura.extract(
                    html,
                    include_tables=True,
                    include_links=True,
                    output_format="txt",
                    favor_recall=True,
                    deduplicate=True,
                )

            text = await asyncio.to_thread(_tier3_extract)
            if text:
                log.debug("read_webpage Tier 3 (Playwright) succeeded for %s", url)
        except Exception as e:
            log.warning("read_webpage Tier 3 (Playwright) failed for %s: %s", url, e)

    if not text:
        return (
            f"Failed to extract content from {url}. "
            "All extraction methods failed (trafilatura fetch, Jina Reader API, Playwright). "
            "The page may be inaccessible, require authentication, or block automated requests."
        )

    # Cache the full text before truncating
    _page_cache.set(cache_key, text)

    if len(text) > max_length:
        text = text[:max_length] + f"\n\n... (truncated, {len(text)} total chars)"
    return f"**Content from {url}:**\n\n{text}"


# ---------------------------------------------------------------------------
# Screenshot (Playwright)
# ---------------------------------------------------------------------------
@tool(
    "screenshot_webpage",
    "Take a screenshot of a webpage and upload it to the current Discord channel.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to screenshot"},
            "full_page": {"type": "boolean", "description": "Capture full page (default: false, viewport only)"},
        },
        "required": ["url"],
    },
)
async def screenshot_webpage(guild: Guild, url: str, full_page: bool = False, **kwargs) -> str:
    import discord as _discord

    channel_id = kwargs.get("channel_id")
    if not channel_id:
        return "Cannot send screenshot: no channel context."

    channel = guild.get_channel(int(channel_id))
    if not channel:
        return "Channel not found."

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return "Playwright not installed."

    tmp_path = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 800})

            # Block heavy resources to speed up screenshots
            await page.route(
                "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot}",
                lambda route: route.abort(),
            )

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)  # allow remaining rendering

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp_path = f.name
            await page.screenshot(path=tmp_path, full_page=full_page)
            await browser.close()

        # Upload to Discord
        file = _discord.File(tmp_path, filename="screenshot.png")
        await channel.send(f"\U0001f4f8 Screenshot of {url}", file=file)
        return f"Screenshot of {url} sent to channel."
    except Exception as e:
        return f"Screenshot failed: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
