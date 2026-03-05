# Web Search & Browsing for Python AI Agent — Research Summary

Target: Discord bot on a 7GB RAM VM.

---

## 1. DDGS (formerly duckduckgo-search) — ⭐ BEST STARTING POINT

**What it is:** A metasearch library that aggregates results from DuckDuckGo, Google, Bing, Brave, Mojeek, Yandex, Yahoo, Wikipedia. No API key needed. Pure HTTP scraping.

**Package:** `pip install ddgs` (the old `duckduckgo-search` package has been renamed)

**How it works:**
```python
from ddgs import DDGS

# Text search
results = DDGS().text("python programming", max_results=5)
# Returns: [{"title": "...", "href": "...", "body": "..."}, ...]

# Also supports: images(), videos(), news(), books()
results = DDGS().news("AI breakthroughs", max_results=5)
```

**Key features:**
- Multiple backends: `backend="auto"` (or `"google"`, `"bing"`, `"duckduckgo"`, etc.)
- Region, safesearch, time filters
- Proxy support (including Tor via `proxy="tb"`)
- Async not built-in but it's just HTTP calls — wrap with `asyncio.to_thread()`

**Dependencies:** `httpx`, `lxml`, `primp`, `click`, `fake-useragent`

**Resource usage:** Minimal. Pure HTTP requests. ~20-50MB RAM overhead. No browser needed.

**Suitability for 7GB VM:** ✅ Excellent. Negligible resource usage.

**Caveats:**
- Rate limiting is possible — use rotating proxies for heavy use
- Scraping-based, so may break if search engines change HTML
- The library updates frequently to keep up with changes

---

## 2. Trafilatura — ⭐ BEST FOR CONTENT EXTRACTION

**What it is:** Article/content extraction from web pages. Turns raw HTML into clean text/markdown. Used by HuggingFace, IBM, Microsoft Research.

**Package:** `pip install trafilatura`

**How it works:**
```python
import trafilatura

# Download and extract in one step
text = trafilatura.fetch_and_extract("https://example.com/article")

# Or separately
downloaded = trafilatura.fetch_url("https://example.com/article")
text = trafilatura.extract(downloaded)

# With metadata
result = trafilatura.extract(downloaded, output_format="json",
                             include_comments=False, include_tables=True)

# Can also output as markdown, XML, CSV
text = trafilatura.extract(downloaded, output_format="markdown")
```

**Key features:**
- Smart main-content extraction (strips nav, headers, footers, ads)
- Metadata extraction (title, author, date, description)
- Output formats: plain text, markdown, JSON, XML, CSV
- Built-in crawling (sitemaps, feeds)
- Language detection
- Deduplication

**Dependencies:** `lxml`, `courlan`, `htmldate`, `justext`, `charset_normalizer`, `urllib3`

**Resource usage:** Moderate. ~50-100MB RAM. CPU spikes during HTML parsing but brief.

**Suitability for 7GB VM:** ✅ Excellent. Lightweight, no browser needed.

**Perfect combo with DDGS:** Search with DDGS → get URLs → extract content with trafilatura.

---

## 3. BeautifulSoup4 — GENERAL HTML PARSING

**What it is:** The classic Python HTML/XML parser. Lower-level than trafilatura.

**Package:** `pip install beautifulsoup4 lxml` (use lxml parser for speed)

**How it works:**
```python
import httpx
from bs4 import BeautifulSoup

html = httpx.get("https://example.com").text
soup = BeautifulSoup(html, "lxml")

# Extract specific elements
title = soup.find("title").text
links = [a["href"] for a in soup.find_all("a", href=True)]
paragraphs = [p.text for p in soup.find_all("p")]
```

**Resource usage:** Very low. ~10-30MB.

**Suitability for 7GB VM:** ✅ Excellent.

**When to use:** When you need fine-grained HTML parsing (specific selectors, structured data). For article extraction, trafilatura is far better out of the box.

---

## 4. Playwright (Headless Browser) — FOR JS-HEAVY SITES

**What it is:** Microsoft's browser automation library. Controls real Chromium/Firefox/WebKit.

**Package:** `pip install playwright && playwright install chromium`

**How it works:**
```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.goto("https://example.com")
    
    # Screenshot
    await page.screenshot(path="screenshot.png")
    
    # Get rendered HTML (after JS execution)
    content = await page.content()
    
    # Extract text
    text = await page.inner_text("body")
    
    # Click, type, interact
    await page.click("button.load-more")
    await page.fill("input[name=q]", "search query")
    
    await browser.close()
```

**Installation:**
```bash
pip install playwright
playwright install chromium --with-deps  # Downloads ~150MB Chromium binary
# System deps on Ubuntu: libnss3, libatk1.0-0, libcups2, etc.
```

**Resource usage:** ⚠️ HEAVY
- Chromium binary: ~150-200MB on disk
- Each browser instance: **150-300MB RAM** idle
- Per tab: additional **50-100MB RAM**
- CPU spikes during page loads and rendering
- With a 7GB VM running other services, each browser session is expensive

**Suitability for 7GB VM:** ⚠️ Usable but be careful.
- Launch browser on-demand, close immediately after use
- Use `chromium.launch(args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage", "--single-process"])` to reduce footprint
- Never keep a persistent browser instance
- Limit to 1 concurrent browser session

**When it's necessary:**
- JavaScript-rendered pages (SPAs, React sites)
- Sites that block simple HTTP requests
- Taking actual webpage screenshots
- Filling out forms, navigating complex UIs

---

## 5. SearXNG (Self-hosted Meta-search) — OVERKILL FOR THIS USE CASE

**What it is:** A self-hosted metasearch engine that aggregates 70+ search engines. There's an MCP server (`mcp-searxng`, 456★) that wraps it.

**How it works:** You run a SearXNG Docker container, then query its API:
```bash
# Docker compose to run SearXNG
docker run -p 8080:8080 searxng/searxng

# Then query its JSON API
curl "http://localhost:8080/search?q=test&format=json"
```

**The MCP server** (`npm install -g mcp-searxng`) adds:
- `searxng_web_search` tool with pagination, time filtering, language
- `web_url_read` tool with content extraction to markdown
- Intelligent caching with TTL

**Resource usage:** ⚠️ SIGNIFICANT
- SearXNG Docker container: **200-500MB RAM**
- Plus the MCP server process (Node.js): **50-100MB**
- Plus Docker overhead

**Suitability for 7GB VM:** ❌ Not recommended. You'd be running Docker + SearXNG + Node.js MCP server just to do web searches. DDGS does the same thing with zero infrastructure.

**When it makes sense:** If you're already running SearXNG for other purposes, or need to aggregate many search engines with fine-grained control.

---

## RECOMMENDED ARCHITECTURE

For a Discord bot on a 7GB RAM VM:

### Tier 1 — Implement First (minimal resources, maximum value)
```
pip install ddgs trafilatura
```

| Tool | Purpose | RAM | Complexity |
|------|---------|-----|------------|
| `ddgs` | Web search (text, news, images, videos) | ~20MB | Dead simple |
| `trafilatura` | Extract article content from URLs | ~50MB | Dead simple |

**Example agent tools:**
```python
from ddgs import DDGS
import trafilatura

async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web using DuckDuckGo and other engines."""
    return await asyncio.to_thread(
        DDGS().text, query, max_results=max_results
    )

async def read_webpage(url: str) -> str:
    """Extract the main content from a webpage."""
    downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
    if not downloaded:
        return "Failed to fetch URL"
    text = trafilatura.extract(downloaded, include_tables=True,
                                output_format="markdown")
    return text or "Could not extract content"

async def web_search_and_read(query: str) -> str:
    """Search and read top results."""
    results = await web_search(query, max_results=3)
    summaries = []
    for r in results:
        content = await read_webpage(r["href"])
        summaries.append(f"## {r['title']}\n{r['href']}\n\n{content[:2000]}")
    return "\n\n---\n\n".join(summaries)
```

### Tier 2 — Add Later If Needed
```
pip install playwright && playwright install chromium
```

| Tool | Purpose | RAM | When to add |
|------|---------|-----|-------------|
| `playwright` | JS-heavy sites, screenshots | ~200MB/session | When users need screenshots or JS-rendered pages |
| `beautifulsoup4` | Custom HTML parsing | ~10MB | When trafilatura doesn't extract what you need |

### Skip
- **SearXNG MCP** — too much infrastructure overhead for what DDGS already provides free
- **Paid APIs** (Serper, Tavily) — unnecessary when DDGS works fine

---

## TOTAL ESTIMATED RESOURCE IMPACT

| Configuration | Additional RAM | Additional Disk |
|--------------|---------------|----------------|
| DDGS + trafilatura only | ~70MB | ~30MB |
| + Playwright (on-demand) | ~70MB idle, +250MB during use | ~200MB |
| + SearXNG Docker | ~500MB persistent | ~500MB |

For a 7GB VM, the Tier 1 approach (DDGS + trafilatura) is the clear winner:
**~70MB total overhead, zero infrastructure, no API keys, 5 lines of code to integrate.**
