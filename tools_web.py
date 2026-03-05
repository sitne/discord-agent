"""Web search, page reading, and screenshot tools."""
import asyncio
import logging
import os
import tempfile
from typing import Optional

from tools import tool
from discord import Guild

log = logging.getLogger("tools.web")


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
        lines.append(f"   {r.get('body', '')[:200]}")
    return "\n".join(lines)


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
    def _search():
        return DDGS().news(keywords=query, max_results=max_results)
    results = await asyncio.to_thread(_search)
    if not results:
        return f"No news found for '{query}'."
    lines = [f"**News: '{query}'** ({len(results)} articles):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r.get('title', '')}**")
        lines.append(f"   {r.get('url', '')}")
        lines.append(f"   {r.get('body', '')[:200]}")
        if r.get("date"):
            lines.append(f"   Published: {r['date']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Web Page Reading (trafilatura)
# ---------------------------------------------------------------------------
@tool(
    "read_webpage",
    "Fetch a webpage and extract its main content as clean text. Good for reading articles, docs, and blog posts.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to read"},
            "max_length": {"type": "integer", "description": "Max content length in characters (default 3000)"},
        },
        "required": ["url"],
    },
)
async def read_webpage(guild: Guild, url: str, max_length: int = 3000, **kwargs) -> str:
    import trafilatura
    def _fetch():
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        return trafilatura.extract(
            downloaded,
            include_tables=True,
            include_links=True,
            output_format="txt",
        )
    text = await asyncio.to_thread(_fetch)
    if not text:
        return f"Failed to extract content from {url}"
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
            await page.goto(url, wait_until="networkidle", timeout=30000)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp_path = f.name
            await page.screenshot(path=tmp_path, full_page=full_page)
            await browser.close()

        # Upload to Discord
        file = _discord.File(tmp_path, filename="screenshot.png")
        await channel.send(f"📸 Screenshot of {url}", file=file)
        return f"Screenshot of {url} sent to channel."
    except Exception as e:
        return f"Screenshot failed: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
